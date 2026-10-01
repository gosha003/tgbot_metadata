"""Инспектор растровых изображений: JPEG, PNG, TIFF, WebP, GIF, HEIF/HEIC.

Только stdlib (struct, zlib, binascii, re) -- ни Pillow, ни piexif, ни
обязательного exiftool. Причина жёсткая: модуль вызывается рекурсивно для
картинок, вытащенных из docx/pptx/PDF, и должен работать ВСЕГДА.

Фаза 0: read-only. Файл открывается строго на чтение, наружу не бросается
ни одного исключения -- любая проблема разбора уходит в report.errors.

exiftool подключается только как необязательное обогащение: нашёлся на PATH
или в ./tools/exiftool.exe -- дополним находки, не нашёлся -- молча дальше.
"""

from __future__ import annotations

import binascii
import json
import os
import re
import shutil
import struct
import subprocess
import zlib

from .model import Report, Risk
from . import sniff

# --- пределы: файлы бывают повреждённые и намеренно кривые -----------------

MAX_FILE = 50 * 1024 * 1024      # больше в память не берём
MAX_INFLATE = 16 * 1024 * 1024   # предел распаковки одного zlib-потока
MAX_RATIO = 500                  # предел коэффициента сжатия (zip-бомба)
MAX_SEGMENTS = 512               # маркеров JPEG
MAX_CHUNKS = 1024                # чанков PNG
MAX_IFDS = 16                    # IFD в TIFF/EXIF
MAX_ENTRIES = 1000               # тегов в одном IFD
MAX_BOXES = 512                  # боксов ISOBMFF
MAX_FIELDS = 200                 # полей XMP, которые печатаем поимённо
MAX_GIF_BLOCKS = 512

SUPPORTED = ("jpeg", "png", "tiff", "webp", "gif", "heic")


# ===========================================================================
# мелкие помощники
# ===========================================================================

def _u16(b, off, en=">"):
    return struct.unpack_from(en + "H", b, off)[0]


def _u32(b, off, en=">"):
    return struct.unpack_from(en + "I", b, off)[0]


def _text(raw, enc="utf-8"):
    """Байты в строку: недоверенные, поэтому всегда с заменой битых символов."""
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode(enc, "replace")


def _hexdump(raw, n=16):
    return bytes(raw[:n]).hex(" ")


def _inflate(blob, report, loc):
    """Распаковка zlib с двойной защитой: по объёму и по коэффициенту сжатия."""
    if not blob:
        return b""
    try:
        d = zlib.decompressobj()
        out = d.decompress(bytes(blob), MAX_INFLATE)
        if not d.eof or d.unconsumed_tail:
            report.err("%s: распаковка оборвана на пределе %d байт" % (loc, MAX_INFLATE))
        if len(blob) and len(out) // max(len(blob), 1) > MAX_RATIO:
            report.err("%s: подозрительный коэффициент сжатия %dx" % (loc, len(out) // max(len(blob), 1)))
            return out[:MAX_INFLATE]
        return out
    except zlib.error as exc:
        report.err("%s: битый zlib-поток (%s)" % (loc, exc))
        return b""


# ===========================================================================
# EXIF / TIFF
# ===========================================================================

# Размер одного элемента по коду типа TIFF.
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}

# tag -> (подпись, риск, пояснение). Общая таблица для IFD0 и ExifIFD.
_TAGS = {
    0x010E: ("ImageDescription", Risk.IDENTITY,
             "Описание кадра: часто ФИО автора, название проекта или заказчика"),
    0x010F: ("Make", Risk.PROVENANCE, "Производитель камеры"),
    0x0110: ("Model", Risk.PROVENANCE, "Модель камеры: сужает круг владельцев"),
    0x0131: ("Software", Risk.PROVENANCE, "Чем обработан файл"),
    0x0132: ("DateTime", Risk.PROVENANCE, "Время последней правки файла"),
    0x013B: ("Artist", Risk.IDENTITY, "Автор кадра, прописан в камере или редакторе"),
    0x013C: ("HostComputer", Risk.ENVIRONMENT,
             "Имя компьютера или модель машины, где обрабатывали файл"),
    0x8298: ("Copyright", Risk.IDENTITY, "Копирайт: почти всегда ФИО или название фирмы"),
    0x9003: ("DateTimeOriginal", Risk.PROVENANCE, "Момент съёмки"),
    0x9004: ("DateTimeDigitized", Risk.PROVENANCE, "Момент оцифровки"),
    0x9010: ("OffsetTime", Risk.PROVENANCE, "Часовой пояс устройства: намекает на регион"),
    0x9011: ("OffsetTimeOriginal", Risk.PROVENANCE, "Часовой пояс в момент съёмки"),
    0x9012: ("OffsetTimeDigitized", Risk.PROVENANCE, "Часовой пояс при оцифровке"),
    0x9290: ("SubSecTime", Risk.PROVENANCE, "Доли секунды: уточняют временную метку"),
    0x9291: ("SubSecTimeOriginal", Risk.PROVENANCE, "Доли секунды съёмки"),
    0x9292: ("SubSecTimeDigitized", Risk.PROVENANCE, "Доли секунды оцифровки"),
    0x9000: ("ExifVersion", Risk.PROVENANCE, "Версия EXIF: фингерпринт прошивки/софта"),
    0x9286: ("UserComment", Risk.IDENTITY, "Свободный комментарий пользователя"),
    0xA420: ("ImageUniqueID", Risk.IDENTITY, "Уникальный идентификатор кадра: связывает файлы между собой"),
    0xA430: ("CameraOwnerName", Risk.IDENTITY, "Имя владельца камеры, вписано в прошивку"),
    0xA431: ("BodySerialNumber", Risk.IDENTITY,
             "Серийный номер тушки: привязывает файл к конкретному экземпляру камеры"),
    0xA433: ("LensMake", Risk.PROVENANCE, "Производитель объектива"),
    0xA434: ("LensModel", Risk.PROVENANCE, "Модель объектива"),
    0xA435: ("LensSerialNumber", Risk.IDENTITY, "Серийный номер объектива"),
    0xC614: ("UniqueCameraModel", Risk.PROVENANCE, "Модель камеры по DNG"),
    0xC62F: ("CameraSerialNumber", Risk.IDENTITY, "Серийный номер камеры по DNG"),
    0x9C9B: ("XPTitle", Risk.IDENTITY, "Заголовок из проводника Windows (UTF-16LE)"),
    0x9C9C: ("XPComment", Risk.IDENTITY, "Комментарий из проводника Windows (UTF-16LE)"),
    0x9C9D: ("XPAuthor", Risk.IDENTITY, "Автор из проводника Windows (UTF-16LE)"),
    0x9C9E: ("XPKeywords", Risk.IDENTITY, "Ключевые слова из проводника Windows (UTF-16LE)"),
    0x9C9F: ("XPSubject", Risk.IDENTITY, "Тема из проводника Windows (UTF-16LE)"),
}

# Теги в UTF-16LE (Windows-расширения EXIF).
_XP_TAGS = (0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F)

# Теги-указатели на вложенные IFD.
_SUB_IFD = {0x8769: "ExifIFD", 0x8825: "GPS", 0xA005: "Interop", 0x014A: "SubIFD"}

# Структурное: при чистке обычно сохраняем.
_STRUCTURAL = {
    0x0100: "ImageWidth", 0x0101: "ImageLength", 0x0102: "BitsPerSample",
    0x0103: "Compression", 0x0106: "PhotometricInterpretation",
    0x0112: "Orientation", 0x0115: "SamplesPerPixel", 0x011A: "XResolution",
    0x011B: "YResolution", 0x0128: "ResolutionUnit", 0x0213: "YCbCrPositioning",
    0x011C: "PlanarConfiguration", 0xA001: "ColorSpace", 0xA002: "PixelXDimension",
    0xA003: "PixelYDimension", 0x0111: "StripOffsets", 0x0116: "RowsPerStrip",
    0x0117: "StripByteCounts", 0x0142: "TileWidth", 0x0143: "TileLength",
}

# Имена прочих часто встречающихся тегов -- нужны только для читаемой сводки.
_MINOR = {
    0x829A: "ExposureTime", 0x829D: "FNumber", 0x8822: "ExposureProgram",
    0x8827: "ISOSpeedRatings", 0x9201: "ShutterSpeedValue", 0x9202: "ApertureValue",
    0x9204: "ExposureBiasValue", 0x9205: "MaxApertureValue", 0x9207: "MeteringMode",
    0x9208: "LightSource", 0x9209: "Flash", 0x920A: "FocalLength",
    0xA402: "ExposureMode", 0xA403: "WhiteBalance", 0xA406: "SceneCaptureType",
    0xA404: "DigitalZoomRatio", 0xA405: "FocalLengthIn35mmFilm",
    0xA432: "LensSpecification", 0x9101: "ComponentsConfiguration",
    0x0201: "JPEGInterchangeFormat", 0x0202: "JPEGInterchangeFormatLength",
}

_GPS_TAGS = {
    0x0000: "GPSVersionID", 0x0001: "GPSLatitudeRef", 0x0002: "GPSLatitude",
    0x0003: "GPSLongitudeRef", 0x0004: "GPSLongitude", 0x0005: "GPSAltitudeRef",
    0x0006: "GPSAltitude", 0x0007: "GPSTimeStamp", 0x0008: "GPSSatellites",
    0x0009: "GPSStatus", 0x000A: "GPSMeasureMode", 0x000B: "GPSDOP",
    0x000C: "GPSSpeedRef", 0x000D: "GPSSpeed", 0x000E: "GPSTrackRef",
    0x000F: "GPSTrack", 0x0010: "GPSImgDirectionRef", 0x0011: "GPSImgDirection",
    0x0012: "GPSMapDatum", 0x0013: "GPSDestLatitudeRef", 0x0014: "GPSDestLatitude",
    0x0015: "GPSDestLongitudeRef", 0x0016: "GPSDestLongitude",
    0x001B: "GPSProcessingMethod", 0x001C: "GPSAreaInformation",
    0x001D: "GPSDateStamp", 0x001E: "GPSDifferential", 0x001F: "GPSHPositioningError",
}

# Известные заголовки MakerNote -- опознаём вендора, внутрь не лезем.
_MAKERNOTE_VENDORS = (
    (b"Nikon", "Nikon"), (b"OLYMPUS", "Olympus"), (b"OM SYSTEM", "OM System"),
    (b"SONY DSC", "Sony"), (b"FUJIFILM", "Fujifilm"), (b"PENTAX", "Pentax"),
    (b"Panasonic", "Panasonic"), (b"LEICA", "Leica"), (b"Apple iOS", "Apple"),
    (b"SAMSUNG", "Samsung"), (b"RICOH", "Ricoh"), (b"SIGMA", "Sigma"),
    (b"CASIO", "Casio"), (b"KONICA", "Konica"), (b"QVC", "Casio QV"),
)


def _decode_value(typ, count, raw, en):
    """Сырые байты значения тега -> питоновское значение по коду типа TIFF."""
    if typ == 2:                       # ASCII
        return _text(raw.split(b"\x00")[0])
    if typ in (1, 6, 7):               # BYTE / SBYTE / UNDEFINED
        return raw
    fmt = {3: "H", 8: "h", 4: "I", 9: "i", 11: "f", 12: "d", 13: "I"}.get(typ)
    if fmt:
        size = _TYPE_SIZE[typ]
        n = min(count, len(raw) // size)
        return list(struct.unpack_from(en + str(n) + fmt, raw, 0)) if n else []
    if typ in (5, 10):                 # RATIONAL / SRATIONAL
        fmt = "II" if typ == 5 else "ii"
        n = min(count, len(raw) // 8)
        flat = struct.unpack_from(en + fmt * n, raw, 0) if n else ()
        return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
    return raw


def _show(value):
    """Значение тега в печатаемый вид."""
    if isinstance(value, (bytes, bytearray)):
        return "<%d байт: %s>" % (len(value), _hexdump(value))
    if isinstance(value, list):
        if not value:
            return ""
        if isinstance(value[0], tuple):
            return ", ".join("%d/%d" % r for r in value[:12])
        return ", ".join(str(v) for v in value[:16])
    return str(value)


def _ratio(pair):
    num, den = pair
    return float(num) / den if den else 0.0


def _gps_decimal(vals, ref):
    """Градусы/минуты/секунды -> десятичные градусы. Отдаём готовую координату."""
    if not isinstance(vals, list) or len(vals) < 3:
        return None
    try:
        deg = _ratio(vals[0]) + _ratio(vals[1]) / 60.0 + _ratio(vals[2]) / 3600.0
    except (TypeError, ZeroDivisionError, IndexError):
        return None
    if str(ref).strip().upper().startswith(("S", "W")):
        deg = -deg
    return deg


def _walk_tiff(report, blob, loc, ctx):
    """Обход TIFF-структуры: IFD0 -> ExifIFD/GPS/Interop -> IFD1 (миниатюра).

    Возвращает {имя_ifd: {tag: (typ, value)}}. Смещения кольцуются в битых
    файлах, поэтому посещённые IFD запоминаем и число обходов ограничиваем.
    """
    out = {}
    if len(blob) < 8:
        report.err("%s: EXIF короче заголовка TIFF (%d байт)" % (loc, len(blob)))
        return out
    order = blob[:2]
    if order == b"II":
        en = "<"
    elif order == b"MM":
        en = ">"
    else:
        report.err("%s: неизвестный порядок байтов %r" % (loc, order[:2]))
        return out
    try:
        magic, first = struct.unpack_from(en + "HI", blob, 2)
    except struct.error:
        report.err("%s: обрезан заголовок TIFF" % loc)
        return out
    if magic == 43:
        report.err("%s: BigTIFF не поддерживается, разобран только заголовок" % loc)
        return out
    if magic != 42:
        report.err("%s: неверная сигнатура TIFF (%d)" % (loc, magic))
        return out

    seen = set()
    queue = [(first, "IFD0")]
    guard = 0
    while queue and guard < MAX_IFDS:
        off, name = queue.pop(0)
        if off <= 0 or off >= len(blob) or off in seen:
            if off in seen:
                report.err("%s: %s ссылается на уже разобранный IFD (зацикленный EXIF)" % (loc, name))
            elif off:
                report.err("%s: %s указывает за пределы блока (смещение %d)" % (loc, name, off))
            continue
        seen.add(off)
        guard += 1
        try:
            count = _u16(blob, off, en)
        except struct.error:
            report.err("%s: %s обрезан" % (loc, name))
            continue
        if count > MAX_ENTRIES:
            report.err("%s: %s объявляет %d тегов, разбираем первые %d"
                       % (loc, name, count, MAX_ENTRIES))
            count = MAX_ENTRIES
        tags = out.setdefault(name, {})
        # Кривой IFD умеет генерировать ошибку на каждый тег. Отчёт из тысячи
        # одинаковых строк бесполезен, поэтому шумим только первые восемь раз.
        noise = [0]

        def bad(msg):
            noise[0] += 1
            if noise[0] <= 8:
                report.err(msg)

        for i in range(count):
            eoff = off + 2 + i * 12
            if eoff + 12 > len(blob):
                bad("%s: %s обрезан на теге %d" % (loc, name, i))
                break
            tag, typ, cnt = struct.unpack_from(en + "HHI", blob, eoff)
            size = _TYPE_SIZE.get(typ, 0) * cnt
            if not _TYPE_SIZE.get(typ):
                tags[tag] = (typ, b"")
                continue
            if size > MAX_INFLATE:
                bad("%s: %s тег 0x%04X объявляет %d байт, пропущен" % (loc, name, tag, size))
                continue
            if size <= 4:
                raw = blob[eoff + 8:eoff + 8 + size]
            else:
                voff = _u32(blob, eoff + 8, en)
                if voff + size > len(blob) or voff < 8:
                    bad("%s: %s тег 0x%04X указывает за пределы блока" % (loc, name, tag))
                    continue
                raw = blob[voff:voff + size]
            if tag in _SUB_IFD and typ in (3, 4, 13):
                try:
                    ptr = _decode_value(typ, cnt, raw, en)[0]
                except (IndexError, struct.error):
                    continue
                queue.append((int(ptr), _SUB_IFD[tag]))
                continue
            if tag in _XP_TAGS:
                value = _text(bytes(raw), "utf-16-le").rstrip("\x00")
            else:
                value = _decode_value(typ, cnt, raw, en)
            tags[tag] = (typ, value)
        if noise[0] > 8:
            report.err("%s: %s -- ещё %d однотипных ошибок разбора тегов подавлено"
                       % (loc, name, noise[0] - 8))
        # цепочка следующих IFD (IFD1 -- это встроенная миниатюра)
        nxt_off = off + 2 + count * 12
        if nxt_off + 4 <= len(blob):
            nxt = _u32(blob, nxt_off, en)
            if nxt and name.startswith("IFD"):
                try:
                    idx = int(name[3:]) + 1
                except ValueError:
                    idx = 1
                queue.append((nxt, "IFD%d" % idx))
    if guard >= MAX_IFDS:
        report.err("%s: достигнут предел в %d IFD, обход остановлен" % (loc, MAX_IFDS))

    _report_exif(report, out, loc, ctx)
    return out


def _report_exif(report, ifds, loc, ctx):
    """Превратить разобранные IFD в находки."""
    for name, tags in ifds.items():
        where = "%s:%s" % (loc, name)
        if name == "GPS":
            _report_gps(report, tags, where, ctx)
            continue
        minor = []
        for tag, (typ, value) in sorted(tags.items()):
            if tag == 0x927C:                       # MakerNote
                raw = value if isinstance(value, (bytes, bytearray)) else b""
                vendor = ""
                for sig, human in _MAKERNOTE_VENDORS:
                    if bytes(raw[:16]).startswith(sig):
                        vendor = human
                        break
                ctx["makernote"] = len(raw)
                report.add(
                    Risk.IDENTITY, where, "MakerNote",
                    "%d байт%s, начало: %s" % (len(raw), (", вендор " + vendor) if vendor else "",
                                               _hexdump(raw)),
                    note="Проприетарный блок камеры. Внутрь не лезем, но именно там обычно "
                         "лежат серийный номер экземпляра, счётчик кадров и персональные настройки",
                )
                continue
            if tag == 0x02BC:                       # XMLPacket = XMP внутри TIFF
                _parse_xmp(report, _text(bytes(value) if isinstance(value, (bytes, bytearray)) else value),
                           where + ":XMP", ctx)
                continue
            if tag == 0x8773:                       # ICC внутри TIFF
                blob = bytes(value) if isinstance(value, (bytes, bytearray)) else b""
                desc = _icc_desc(blob)
                report.add(Risk.STRUCTURAL, where, "ICC-профиль",
                           "%d байт%s" % (len(blob), (", описание: " + desc) if desc else ""),
                           note="Цветовой профиль: при чистке обычно сохраняем, но имя профиля "
                                "может называть конкретный монитор или сканер")
                continue
            if tag in (0x83BB, 0x8649):             # IPTC-NAA / Photoshop IRB в TIFF
                blob = bytes(value) if isinstance(value, (bytes, bytearray)) else b""
                if tag == 0x8649:
                    _parse_irb(report, blob, where + ":Photoshop", ctx)
                else:
                    _parse_iptc(report, blob, where + ":IPTC", ctx)
                continue
            if tag in _TAGS:
                label, risk, note = _TAGS[tag]
                shown = _show(value)
                report.add(risk, where, label, shown, note=note)
                _remember(ctx, label, shown)
                continue
            if tag in _STRUCTURAL:
                minor.append("%s=%s" % (_STRUCTURAL[tag], _show(value)))
                continue
            minor.append("%s=%s" % (_MINOR.get(tag, "0x%04X" % tag), _show(value)))
        if minor:
            report.add(Risk.STRUCTURAL, where, "Прочие теги (%d)" % len(minor),
                       "; ".join(minor[:40]),
                       note="Технические параметры съёмки и геометрия. Сами по себе не PII, "
                            "но вместе фингерпринтят конкретную камеру и её настройки")
        # встроенная миниатюра
        if name != "IFD0" and 0x0201 in tags:
            try:
                thumb_len = tags.get(0x0202, (4, [0]))[1][0]
            except (IndexError, TypeError):
                thumb_len = 0
            report.add(Risk.IDENTITY, where, "Встроенная миниатюра", "%d байт" % thumb_len,
                       note="Миниатюра могла быть сделана ДО ретуши и кропа: в ней иногда "
                            "видно исходный кадр целиком, включая то, что замазали")
            ctx["thumbnail"] = thumb_len


def _report_gps(report, tags, where, ctx):
    """GPS -- самая ценная находка в картинке, поэтому координату считаем сразу."""
    plain = {}
    for tag, (typ, value) in sorted(tags.items()):
        label = _GPS_TAGS.get(tag, "GPS:0x%04X" % tag)
        plain[label] = value
        if label in ("GPSLatitude", "GPSLongitude"):
            continue  # покажем пересчитанными ниже
        if tag in (0x001B, 0x001C) and isinstance(value, (bytes, bytearray)):
            value = _text(bytes(value)[8:]) or _hexdump(value)
        report.add(Risk.IDENTITY, where, label, _show(value),
                   note="Геоданные снимка")
    lat = _gps_decimal(plain.get("GPSLatitude"), plain.get("GPSLatitudeRef", ""))
    lon = _gps_decimal(plain.get("GPSLongitude"), plain.get("GPSLongitudeRef", ""))
    if lat is not None and lon is not None:
        coord = "%.6f, %.6f" % (lat, lon)
        ctx["gps"] = coord
        alt = plain.get("GPSAltitude")
        extra = ""
        if isinstance(alt, list) and alt and isinstance(alt[0], tuple):
            extra = ", высота %.1f м" % _ratio(alt[0])
        report.add(Risk.IDENTITY, where, "Координаты (десятичные)", coord + extra,
                   note="Готовая точка на карте: адрес съёмки, дом, офис. Самая опасная "
                        "находка в картинке -- по ней находят человека, а не файл")


def _icc_desc(blob):
    """Имя описания ICC-профиля: часто названо по устройству или монитору."""
    try:
        if len(blob) < 132:
            return ""
        count = _u32(blob, 128)
        if count > 4096:
            return ""
        for i in range(count):
            off = 132 + i * 12
            if off + 12 > len(blob):
                break
            sig = blob[off:off + 4]
            toff, tsize = _u32(blob, off + 4), _u32(blob, off + 8)
            if sig != b"desc" or toff + tsize > len(blob) or tsize > (1 << 20):
                continue
            tag = blob[toff:toff + tsize]
            if tag[:4] == b"desc" and len(tag) >= 12:
                n = min(_u32(tag, 8), len(tag) - 12)
                return _text(tag[12:12 + n]).split("\x00")[0]
            if tag[:4] == b"mluc" and len(tag) >= 28:
                ln, loff = _u32(tag, 20), _u32(tag, 24)
                if loff + ln <= len(tag) and ln < 4096:
                    return _text(tag[loff:loff + ln], "utf-16-be").strip("\x00")
        return ""
    except (struct.error, ValueError):
        return ""


# ===========================================================================
# XMP
# ===========================================================================

_XMP_ATTR = re.compile(r"\b([A-Za-z][A-Za-z0-9]*):([A-Za-z0-9_]+)\s*=\s*\"([^\"]{0,600})\"")
_XMP_ELEM = re.compile(r"<([A-Za-z][A-Za-z0-9]*):([A-Za-z0-9_]+)[^>]*>([^<]{0,600})</\1:\2>")
_XMP_SKIP = {"rdf", "x", "xmlns", "xml"}

# префикс:имя -> (подпись, риск)
_XMP_RISK = {
    "dc:creator": Risk.IDENTITY, "dc:rights": Risk.IDENTITY, "dc:title": Risk.IDENTITY,
    "dc:description": Risk.IDENTITY, "dc:subject": Risk.IDENTITY,
    "photoshop:Credit": Risk.IDENTITY, "photoshop:Source": Risk.IDENTITY,
    "photoshop:AuthorsPosition": Risk.IDENTITY, "photoshop:CaptionWriter": Risk.IDENTITY,
    "photoshop:Headline": Risk.IDENTITY, "photoshop:City": Risk.IDENTITY,
    "photoshop:State": Risk.IDENTITY, "photoshop:Country": Risk.IDENTITY,
    "xmpMM:DocumentID": Risk.IDENTITY, "xmpMM:InstanceID": Risk.IDENTITY,
    "xmpMM:OriginalDocumentID": Risk.IDENTITY, "xmpMM:DerivedFrom": Risk.IDENTITY,
    "aux:SerialNumber": Risk.IDENTITY, "aux:LensSerialNumber": Risk.IDENTITY,
    "Iptc4xmpCore:CreatorContactInfo": Risk.IDENTITY,
    "plus:LicensorName": Risk.IDENTITY,
}
_XMP_ENV = ("crs:RawFileName", "photoshop:LegacyIPTCDigest", "xmp:Label")
# Поля провенанса печатаем отдельным блоком, поэтому в сводку "прочих" не кладём.
_XMP_PROV = ("xmp:CreatorTool", "tiff:Software", "stEvt:softwareAgent",
             "xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate",
             "tiff:Make", "tiff:Model", "pdf:Producer", "GCamera:MicroVideo")
_AI_XMP = ("trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia",
           "algorithmicmedia")


def _parse_xmp(report, text, loc, ctx):
    """XMP-пакет. Полноценный XML тут не нужен и опасен -- тянем поля регулярками."""
    if not text:
        return
    ctx["xmp"] = ctx.get("xmp", 0) + len(text)
    seen = {}
    for match in list(_XMP_ATTR.finditer(text))[:2000]:
        pref, name, value = match.groups()
        if pref.lower() in _XMP_SKIP:
            continue
        seen.setdefault("%s:%s" % (pref, name), value)
    for match in list(_XMP_ELEM.finditer(text))[:2000]:
        pref, name, value = match.groups()
        if pref.lower() in _XMP_SKIP:
            continue
        value = value.strip()
        if value:
            seen.setdefault("%s:%s" % (pref, name), value)
    # rdf:li внутри dc:creator и подобных контейнеров
    for key in ("dc:creator", "dc:rights", "dc:title", "dc:description", "dc:subject"):
        block = re.search(r"<%s\b.*?</%s>" % (key, key), text, re.S)
        if block:
            items = re.findall(r"<rdf:li[^>]*>([^<]{0,400})</rdf:li>", block.group(0))
            if items:
                seen[key] = "; ".join(i.strip() for i in items)

    minor = []
    shown = 0
    for key, value in sorted(seen.items()):
        risk = _XMP_RISK.get(key)
        if risk is None and key in _XMP_ENV:
            risk = Risk.ENVIRONMENT
        if risk is None:
            if key not in _XMP_PROV:
                minor.append("%s=%s" % (key, value[:80]))
            continue
        if shown >= MAX_FIELDS:
            minor.append("%s=%s" % (key, value[:80]))
            continue
        shown += 1
        report.add(risk, loc, key, value,
                   note="Поле XMP. XMP дублируется в EXIF и IPTC и переживает пересохранение "
                        "в большинстве редакторов")
        _remember(ctx, key, value)
    for key in _XMP_PROV:
        if key in seen:
            report.add(Risk.PROVENANCE, loc, key, seen[key],
                       note="Чем и когда сделан или изменён файл")
            _remember(ctx, key, seen[key])
    agents = re.findall(r"stEvt:softwareAgent\s*=\s*\"([^\"]{0,200})\"", text)
    if len(agents) > 1:
        report.add(Risk.PROVENANCE, loc, "История правок xmpMM:History",
                   "; ".join(dict.fromkeys(agents))[:400],
                   note="Цепочка инструментов, через которые прошёл файл: видно весь пайплайн")
        for agent in agents:
            _remember(ctx, "softwareAgent", agent)
    if minor:
        report.add(Risk.PROVENANCE, loc, "Прочие поля XMP (%d)" % len(minor),
                   "; ".join(minor[:40]),
                   note="Настройки проявки и технические поля: не PII, но фингерпринтят "
                        "конкретный редактор и его версию")
    low = text.lower()
    for marker in _AI_XMP:
        if marker in low:
            report.signal("ai", "XMP объявляет digitalSourceType=%s: файл помечен как "
                                "сгенерированный или доработанный ИИ" % marker, "high")
            break


# ===========================================================================
# IPTC-IIM и Photoshop IRB
# ===========================================================================

_IPTC_2 = {
    5: ("Object Name", Risk.IDENTITY), 25: ("Keywords", Risk.IDENTITY),
    55: ("Date Created", Risk.PROVENANCE), 60: ("Time Created", Risk.PROVENANCE),
    62: ("Digital Creation Date", Risk.PROVENANCE),
    63: ("Digital Creation Time", Risk.PROVENANCE),
    65: ("Originating Program", Risk.PROVENANCE),
    70: ("Program Version", Risk.PROVENANCE),
    80: ("By-line", Risk.IDENTITY), 85: ("By-line Title", Risk.IDENTITY),
    90: ("City", Risk.IDENTITY), 92: ("Sub-location", Risk.IDENTITY),
    95: ("Province/State", Risk.IDENTITY), 101: ("Country", Risk.IDENTITY),
    103: ("Original Transmission Reference", Risk.IDENTITY),
    105: ("Headline", Risk.IDENTITY), 110: ("Credit", Risk.IDENTITY),
    115: ("Source", Risk.IDENTITY), 116: ("Copyright Notice", Risk.IDENTITY),
    118: ("Contact", Risk.IDENTITY), 120: ("Caption/Abstract", Risk.IDENTITY),
    122: ("Caption Writer", Risk.IDENTITY),
}

_IRB_IDS = {
    0x03ED: "ResolutionInfo", 0x0404: "IPTC-IIM", 0x0408: "Grid and Guides",
    0x040A: "Copyright Flag", 0x040B: "URL", 0x040C: "Thumbnail",
    0x040F: "ICC Profile", 0x0410: "Watermark", 0x0421: "Version Info",
    0x0422: "EXIF data 1", 0x0423: "EXIF data 3", 0x0424: "XMP",
    0x0425: "Caption digest", 0x0426: "Print scale", 0x042D: "Print flags",
    0x0432: "Layer Selection IDs", 0x043A: "Print Information",
    0x0BB7: "Name of clipping path",
}


def _parse_iptc(report, blob, loc, ctx):
    """IPTC-IIM: поток записей 0x1C record dataset length data."""
    pos, guard = 0, 0
    while pos + 5 <= len(blob) and guard < 512:
        if blob[pos] != 0x1C:
            pos += 1
            continue
        guard += 1
        record, dataset = blob[pos + 1], blob[pos + 2]
        length = _u16(blob, pos + 3)
        head = 5
        if length & 0x8000:                      # расширенный размер
            nbytes = length & 0x7FFF
            if nbytes > 4 or pos + 5 + nbytes > len(blob):
                break
            length = int.from_bytes(blob[pos + 5:pos + 5 + nbytes], "big")
            head = 5 + nbytes
        if pos + head + length > len(blob) or length > MAX_INFLATE:
            report.err("%s: запись IPTC 2:%d обрезана" % (loc, dataset))
            break
        value = _text(blob[pos + head:pos + head + length])
        pos += head + length
        if record != 2:
            continue
        label, risk = _IPTC_2.get(dataset, ("IPTC 2:%d" % dataset, Risk.PROVENANCE))
        report.add(risk, loc, label, value,
                   note="IPTC-IIM: старый формат подписи фотографий, редакторы его "
                        "переносят между файлами вместе с картинкой")
        _remember(ctx, label, value)


def _parse_irb(report, blob, loc, ctx):
    """Photoshop Image Resource Block: последовательность 8BIM."""
    pos, guard, found = 0, 0, []
    while pos + 12 <= len(blob) and guard < 512:
        if blob[pos:pos + 4] != b"8BIM":
            pos += 1
            continue
        guard += 1
        res_id = _u16(blob, pos + 4)
        nlen = blob[pos + 6]
        npad = nlen + 1 + ((nlen + 1) % 2)        # pascal-строка, выравнена на чётность
        size_off = pos + 6 + npad
        if size_off + 4 > len(blob):
            break
        size = _u32(blob, size_off)
        start = size_off + 4
        if size > len(blob) - start or size > MAX_INFLATE:
            report.err("%s: ресурс 0x%04X объявляет %d байт, обрезан" % (loc, res_id, size))
            break
        data = blob[start:start + size]
        pos = start + size + (size % 2)
        name = _IRB_IDS.get(res_id, "8BIM 0x%04X" % res_id)
        found.append("%s (%d Б)" % (name, size))
        if res_id == 0x0404:
            _parse_iptc(report, data, loc + ":IPTC", ctx)
        elif res_id == 0x0424:
            _parse_xmp(report, _text(data), loc + ":XMP", ctx)
        elif res_id == 0x040C:
            report.add(Risk.IDENTITY, loc, "Миниатюра Photoshop", "%d байт" % size,
                       note="Миниатюра сохранена редактором и могла быть сделана ДО правок: "
                            "в ней иногда виден исходный кадр")
        elif res_id == 0x040B:
            report.add(Risk.ENVIRONMENT, loc, "URL", _text(data),
                       note="Ссылка, вписанная в ресурсы Photoshop")
        elif res_id == 0x0BB7:
            report.add(Risk.ENVIRONMENT, loc, "Имя clipping path", _text(data),
                       note="Имя обтравочного контура: задавал человек, бывает говорящим")
    if found:
        report.add(Risk.PROVENANCE, loc, "Ресурсы Photoshop (%d)" % len(found),
                   "; ".join(found[:30]),
                   note="Сам факт наличия 8BIM означает, что файл открывали в Photoshop "
                        "или совместимом редакторе")
        ctx["photoshop"] = True


# ===========================================================================
# JPEG
# ===========================================================================

_APP_NAMES = {0xE0: "APP0", 0xE1: "APP1", 0xE2: "APP2", 0xE3: "APP3", 0xE4: "APP4",
              0xE5: "APP5", 0xE6: "APP6", 0xE7: "APP7", 0xE8: "APP8", 0xE9: "APP9",
              0xEA: "APP10", 0xEB: "APP11", 0xEC: "APP12", 0xED: "APP13",
              0xEE: "APP14", 0xEF: "APP15"}

C2PA_NOTE = (
    "КРИПТОГРАФИЧЕСКИ ПОДПИСАННЫЙ манифест происхождения (C2PA / Content Credentials). "
    "Его удаление само по себе детектируемо: получатель видит, что подпись была и её "
    "срезали. Более того, при наличии soft-binding или водяного знака происхождение "
    "восстанавливается по реестру и без этого блока в файле"
)


def _jpeg(report, data, ctx):
    """Обход маркеров JPEG: FFD8, далее FFxx + big-endian длина, до FFDA/SOS."""
    seg_map = []
    pos, guard = 2, 0
    icc_chunks = 0
    while pos + 1 < len(data) and guard < MAX_SEGMENTS:
        if data[pos] != 0xFF:
            report.err("JPEG: на смещении %d ожидался маркер, найдено 0x%02X" % (pos, data[pos]))
            break
        while pos + 1 < len(data) and data[pos + 1] == 0xFF:   # fill-байты
            pos += 1
        if pos + 1 >= len(data):
            report.err("JPEG: поток обрывается на маркере")
            break
        marker = data[pos + 1]
        guard += 1
        if marker == 0xD9:
            seg_map.append("EOI")
            pos += 2
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            pos += 2
            continue
        if pos + 4 > len(data):
            report.err("JPEG: маркер 0x%02X без длины (файл обрезан)" % marker)
            break
        length = _u16(data, pos + 2)
        if length < 2:
            report.err("JPEG: маркер 0x%02X объявляет длину %d" % (marker, length))
            break
        end = pos + 2 + length
        if end > len(data):
            report.err("JPEG: сегмент 0x%02X обрезан (нужно %d байт, есть %d)"
                       % (marker, length, len(data) - pos - 2))
            seg = data[pos + 4:]
            end = len(data)
        else:
            seg = data[pos + 4:end]
        name = _APP_NAMES.get(marker) or {0xFE: "COM", 0xDB: "DQT", 0xC4: "DHT",
                                          0xDD: "DRI", 0xDA: "SOS"}.get(marker)
        if name is None:
            name = "SOF%d" % (marker - 0xC0) if 0xC0 <= marker <= 0xCF else "0x%02X" % marker
        seg_map.append("%s (%d Б)" % (name, length))
        try:
            icc_chunks += _jpeg_segment(report, marker, name, seg, ctx)
        except Exception as exc:                  # noqa: BLE001 -- сегмент не валит файл
            report.err("JPEG: %s не разобран (%s: %s)" % (name, type(exc).__name__, exc))
        if marker == 0xDA:
            break
        pos = end
    if guard >= MAX_SEGMENTS:
        report.err("JPEG: достигнут предел в %d сегментов" % MAX_SEGMENTS)
    if icc_chunks > 1:
        report.add(Risk.STRUCTURAL, "APP2:ICC_PROFILE", "Частей профиля", str(icc_chunks),
                   note="ICC-профиль разрезан на несколько APP2; описание читаем из первой части")
    report.add(Risk.STRUCTURAL, "JPEG", "Карта сегментов (%d)" % len(seg_map),
               "; ".join(seg_map[:60]),
               note="Полный список маркеров с размерами: по нему видно, что именно предстоит "
                    "вырезать и что при этом останется")
    ctx["segments"] = seg_map
    _trailing(report, data, b"\xff\xd9", "JPEG", "EOI")


def _jpeg_segment(report, marker, name, seg, ctx):
    """Разбор одного сегмента. Возвращает 1, если это часть ICC-профиля."""
    if marker == 0xE0:                                      # APP0 = JFIF
        if seg[:5] == b"JFIF\x00" and len(seg) >= 14:
            units = {0: "без единиц (пропорция)", 1: "точек на дюйм", 2: "точек на см"}
            report.add(Risk.STRUCTURAL, "APP0:JFIF", "JFIF",
                       "версия %d.%02d, плотность %dx%d, %s"
                       % (seg[5], seg[6], _u16(seg, 8), _u16(seg, 10),
                          units.get(seg[7], "код %d" % seg[7])),
                       note="Базовый заголовок JPEG. Плотность иногда выдаёт сканер или "
                            "конкретный конвертер")
        elif seg[:5] == b"JFXX\x00":
            report.add(Risk.PROVENANCE, "APP0:JFXX", "JFXX-миниатюра", "%d байт" % len(seg),
                       note="Расширение JFIF со встроенной миниатюрой")
        return 0
    if marker == 0xE1:                                      # APP1 = EXIF или XMP
        if seg[:6] == b"Exif\x00\x00":
            ctx["exif"] = True
            _walk_tiff(report, seg[6:], "APP1:Exif", ctx)
        elif seg.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            _parse_xmp(report, _text(seg[29:]), "APP1:XMP", ctx)
        elif seg.startswith(b"http://ns.adobe.com/xmp/extension/"):
            report.add(Risk.PROVENANCE, "APP1:XMP-extension", "Расширенный XMP",
                       "%d байт" % len(seg),
                       note="Продолжение XMP-пакета, не поместившегося в 64 КБ")
            _parse_xmp(report, _text(seg[75:]), "APP1:XMP-extension", ctx)
        else:
            report.add(Risk.PROVENANCE, "APP1", "Неопознанный APP1",
                       "%d байт, начало: %s" % (len(seg), _hexdump(seg)),
                       note="Сегмент APP1 с чужой сигнатурой: чем-то дописан вручную")
        return 0
    if marker == 0xE2:                                      # APP2 = ICC или MPF
        if seg[:12] == b"ICC_PROFILE\x00":
            seq, total = (seg[12], seg[13]) if len(seg) > 13 else (1, 1)
            if seq <= 1:
                desc = _icc_desc(seg[14:])
                report.add(Risk.STRUCTURAL, "APP2:ICC_PROFILE", "ICC-профиль",
                           "%d байт, часть %d из %d%s"
                           % (len(seg), seq, total, (", описание: " + desc) if desc else ""),
                           note="Цветовой профиль обычно СОХРАНЯЕМ. Но имя описания часто "
                                "названо по конкретному монитору, принтеру или сканеру -- "
                                "это уже указание на рабочее место")
                if desc:
                    _remember(ctx, "ICC", desc)
            return 1
        if seg[:4] == b"MPF\x00":
            report.add(Risk.PROVENANCE, "APP2:MPF", "Multi-Picture Format", "%d байт" % len(seg),
                       note="Контейнер нескольких изображений в одном файле: внутри может "
                            "лежать второй кадр целиком, со своими метаданными")
            return 0
        report.add(Risk.PROVENANCE, "APP2", "Неопознанный APP2",
                   "%d байт, начало: %s" % (len(seg), _hexdump(seg)))
        return 0
    if marker == 0xEB:                                      # APP11 = JUMBF / C2PA
        low = bytes(seg[:4096])
        if b"jumb" in low or b"c2pa" in low or b"jumd" in low:
            ctx["c2pa"] = True
            report.add(Risk.PROVENANCE, "APP11:JUMBF", "Манифест C2PA / Content Credentials",
                       "%d байт, метки: %s" % (len(seg),
                                               ", ".join(m for m in ("jumb", "jumd", "c2pa")
                                                         if m.encode() in low)),
                       note=C2PA_NOTE, removable=False)
        else:
            report.add(Risk.PROVENANCE, "APP11", "JUMBF-контейнер без меток C2PA",
                       "%d байт, начало: %s" % (len(seg), _hexdump(seg)))
        return 0
    if marker == 0xED:                                      # APP13 = Photoshop IRB
        if seg[:14] == b"Photoshop 3.0\x00":
            _parse_irb(report, seg[14:], "APP13:Photoshop", ctx)
        else:
            _parse_irb(report, seg, "APP13", ctx)
        return 0
    if marker == 0xEE:                                      # APP14 = Adobe
        if seg[:5] == b"Adobe" and len(seg) >= 12:
            transform = {0: "RGB/CMYK", 1: "YCbCr", 2: "YCCK"}.get(seg[11], "код %d" % seg[11])
            report.add(Risk.PROVENANCE, "APP14:Adobe", "Маркер Adobe",
                       "версия %d, transform=%s" % (_u16(seg, 5), transform),
                       note="Сегмент пишут Photoshop и библиотеки Adobe: указывает, "
                            "через какой софт прошёл файл")
            ctx["photoshop"] = True
        return 0
    if marker == 0xFE:                                      # COM
        report.add(Risk.PROVENANCE, "COM", "Комментарий JPEG", _text(seg),
                   note="Текстовый комментарий. Кодировщики пишут сюда своё имя и версию, "
                        "люди -- всё что угодно")
        _remember(ctx, "COM", _text(seg))
        return 0
    if marker == 0xDB:                                      # DQT
        tables = []
        p = 0
        while p < len(seg):
            prec, tid = seg[p] >> 4, seg[p] & 0x0F
            size = 64 * (2 if prec else 1)
            tables.append("таблица %d (%d бит)" % (tid, 16 if prec else 8))
            p += 1 + size
        report.add(Risk.PROVENANCE, "DQT", "Таблицы квантования",
                   "%d шт: %s" % (len(tables), ", ".join(tables[:8])),
                   note="Убрать НЕЛЬЗЯ без перекодирования с потерей качества. При этом "
                        "таблицы фингерпринтят энкодер и настройку качества: по ним "
                        "определяют, чем и с каким качеством пересохраняли файл",
                   removable=False)
        return 0
    if 0xE3 <= marker <= 0xEF:                              # прочие APPn
        report.add(Risk.PROVENANCE, name, "Нестандартный сегмент",
                   "%d байт, начало: %s" % (len(seg), _hexdump(seg)),
                   note="Приложение записало в файл свой блок. Содержимое произвольное")
    return 0


def _trailing(report, data, eof_marker, fmt, label, extra=0):
    """Данные после логического конца файла -- классический тайник.

    extra -- сколько байт штатно идёт после самого маркера (у PNG за типом
    чанка IEND стоит его CRC, и это ещё не хвост).
    """
    idx = data.rfind(eof_marker)
    if idx == -1:
        report.err("%s: не найден маркер %s, файл обрезан" % (fmt, label))
        return
    stop = idx + len(eof_marker) + extra
    tail = len(data) - stop
    if tail > 0:
        report.add(Risk.ENVIRONMENT, fmt, "Данные после %s" % label,
                   "%d байт, начало: %s" % (tail, _hexdump(data[stop:])),
                   note="После конца изображения есть ещё байты. Декодеры их игнорируют, "
                        "поэтому там прячут что угодно -- от архива до чужого документа")
        report.signal("hazard", "после маркера %s ещё %d байт данных" % (label, tail), "medium")


# ===========================================================================
# PNG
# ===========================================================================

_PNG_KNOWN = {
    b"IHDR": "заголовок", b"PLTE": "палитра", b"IDAT": "пиксели", b"IEND": "конец",
    b"gAMA": "гамма", b"cHRM": "хроматичность", b"sRGB": "sRGB", b"bKGD": "фон",
    b"tRNS": "прозрачность", b"hIST": "гистограмма", b"sPLT": "доп. палитра",
    b"acTL": "APNG: управление", b"fcTL": "APNG: кадр", b"fdAT": "APNG: данные",
    b"cICP": "параметры кодирования", b"mDCv": "мастеринг HDR", b"cLLi": "яркость HDR",
    b"eXIf": "EXIF", b"iCCP": "ICC", b"pHYs": "плотность", b"sBIT": "значащие биты",
    b"tIME": "время правки", b"tEXt": "текст", b"zTXt": "сжатый текст",
    b"iTXt": "текст UTF-8", b"caBX": "C2PA", b"dSIG": "подпись",
}


def _png(report, data, ctx):
    """Обход чанков PNG: длина(4) тип(4) данные CRC(4)."""
    pos, guard = 8, 0
    chunk_map, texts = [], 0
    while pos + 8 <= len(data) and guard < MAX_CHUNKS:
        guard += 1
        length = _u32(data, pos)
        ctype = bytes(data[pos + 4:pos + 8])
        if length > MAX_FILE:
            report.err("PNG: чанк %s объявляет %d байт" % (_text(ctype), length))
            break
        end = pos + 8 + length + 4
        if end > len(data):
            report.err("PNG: чанк %s обрезан (нужно %d байт, есть %d)"
                       % (_text(ctype), length, len(data) - pos - 12))
            break
        body = data[pos + 8:pos + 8 + length]
        crc_stored = _u32(data, pos + 8 + length)
        if binascii.crc32(ctype + bytes(body)) & 0xFFFFFFFF != crc_stored:
            report.err("PNG: неверная CRC чанка %s" % _text(ctype))
            ctx["bad_crc"] = ctx.get("bad_crc", 0) + 1
        chunk_map.append("%s (%d Б)" % (_text(ctype), length))
        try:
            if ctype in (b"tEXt", b"zTXt", b"iTXt"):
                texts += 1
                _png_text(report, ctype, body, ctx)
            elif ctype == b"eXIf":
                ctx["exif"] = True
                _walk_tiff(report, body, "PNG:eXIf", ctx)
            elif ctype == b"iCCP":
                name, _, rest = bytes(body).partition(b"\x00")
                profile = _inflate(rest[1:], report, "PNG:iCCP")
                desc = _icc_desc(profile)
                report.add(Risk.STRUCTURAL, "PNG:iCCP", "ICC-профиль",
                           "%s%s" % (_text(name), (", описание: " + desc) if desc else ""),
                           note="Цветовой профиль обычно сохраняем, но его имя нередко "
                                "названо по конкретному монитору или сканеру")
                _remember(ctx, "ICC", _text(name) + " " + desc)
            elif ctype == b"tIME":
                if length >= 7:
                    y, mo, d, h, mi, s = struct.unpack(">HBBBBB", bytes(body[:7]))
                    report.add(Risk.PROVENANCE, "PNG:tIME", "Время последней правки",
                               "%04d-%02d-%02d %02d:%02d:%02d UTC" % (y, mo, d, h, mi, s),
                               note="Момент последнего изменения картинки")
            elif ctype == b"pHYs":
                if length >= 9:
                    x, y = _u32(body, 0), _u32(body, 4)
                    unit = "пикс/м" if body[8] == 1 else "без единиц"
                    report.add(Risk.STRUCTURAL, "PNG:pHYs", "Плотность",
                               "%d x %d %s" % (x, y, unit),
                               note="Физический размер: иногда выдаёт исходный DPI сканера")
            elif ctype == b"sBIT":
                report.add(Risk.STRUCTURAL, "PNG:sBIT", "Значащие биты",
                           ", ".join(str(b) for b in bytes(body)),
                           note="Глубина исходных данных: намекает на источник (сканер, рендер)")
            elif ctype == b"caBX":
                ctx["c2pa"] = True
                report.add(Risk.PROVENANCE, "PNG:caBX", "Манифест C2PA / Content Credentials",
                           "%d байт" % length, note=C2PA_NOTE, removable=False)
            elif ctype == b"dSIG":
                report.add(Risk.PROVENANCE, "PNG:dSIG", "Цифровая подпись", "%d байт" % length,
                           note="Подпись покрывает содержимое файла: любая чистка её "
                                "гарантированно ломает, а сам факт подписи виден получателю",
                           removable=False)
            elif ctype not in _PNG_KNOWN and ctype[:1].islower():
                report.add(Risk.PROVENANCE, "PNG:%s" % _text(ctype),
                           "Неизвестный вспомогательный чанк",
                           "%d байт, начало: %s" % (length, _hexdump(body)),
                           note="Приватный чанк: пишет конкретное приложение, содержимое "
                                "произвольное")
        except Exception as exc:                  # noqa: BLE001
            report.err("PNG: чанк %s не разобран (%s: %s)" % (_text(ctype), type(exc).__name__, exc))
        pos = end
        if ctype == b"IEND":
            break
    if guard >= MAX_CHUNKS:
        report.err("PNG: достигнут предел в %d чанков" % MAX_CHUNKS)
    report.add(Risk.STRUCTURAL, "PNG", "Карта чанков (%d)" % len(chunk_map),
               "; ".join(chunk_map[:60]),
               note="Полный список чанков с размерами")
    ctx["png_texts"] = texts
    _trailing(report, data, b"IEND", "PNG", "IEND", extra=4)   # +4 = CRC чанка IEND


# Текстовые ключи PNG, за которыми обычно стоит человек.
_PNG_ID_KEYS = ("author", "artist", "copyright", "creator", "owner", "email",
                "contact", "disclaimer", "warning", "comment", "description", "title")


def _png_text(report, ctype, body, ctx):
    """tEXt / zTXt / iTXt. Повторов бывает много -- не теряем ни одного."""
    raw = bytes(body)
    keyword, _, rest = raw.partition(b"\x00")
    key = _text(keyword, "latin-1")
    loc = "PNG:%s" % _text(ctype)
    if ctype == b"tEXt":
        value = _text(rest, "latin-1")
    elif ctype == b"zTXt":
        value = _text(_inflate(rest[1:], report, loc + ":" + key), "latin-1")
    else:                                          # iTXt
        if len(rest) < 2:
            report.err("%s: чанк %s обрезан" % (loc, key))
            return
        compressed = rest[0] == 1
        tail = rest[2:]
        lang, _, tail = tail.partition(b"\x00")
        translated, _, tail = tail.partition(b"\x00")
        value = _text(_inflate(tail, report, loc + ":" + key)) if compressed else _text(tail)
        if lang or translated:
            key = "%s [%s/%s]" % (key, _text(lang), _text(translated))
    low = key.lower()
    if low.startswith("raw profile type"):
        _png_raw_profile(report, key, value, loc, ctx)
        return
    if "xmp" in low or value.lstrip()[:40].startswith(("<?xpacket", "<x:xmpmeta")):
        _parse_xmp(report, value, loc + ":XMP", ctx)
        return
    risk = Risk.IDENTITY if any(k in low for k in _PNG_ID_KEYS) else Risk.PROVENANCE
    report.add(risk, loc, key, value,
               note="Текстовый чанк PNG. Пишут и люди, и генераторы: сюда же складывают "
                    "промпты, параметры моделей и путь к исходнику")
    _remember(ctx, key, value)


def _png_raw_profile(report, key, value, loc, ctx):
    """ImageMagick прячет целые профили в текстовый чанк как hex-дамп.

    Выглядит как безобидный текст "Raw profile type iptc", а внутри живой
    IPTC, EXIF или ICC. Обычные просмотрщики его не показывают -- и чистилки,
    которые смотрят только на штатные чанки, тоже его не видят.
    """
    name = key.split()[-1].lower()
    where = "%s:raw/%s" % (loc, name)
    # Формат: "\n<имя профиля>\n<длина десятичным>\n<hex>". Якорь -- строка с
    # длиной: имя профиля и переводы строк у разных версий ImageMagick разные.
    lines = value.replace("\r", "\n").split("\n")
    declared, start = 0, 2
    for i, line in enumerate(lines[:4]):
        if line.strip().isdigit():
            declared, start = int(line.strip()), i + 1
            break
    digits = re.sub(r"[^0-9a-fA-F]", "", "".join(lines[start:]))
    if len(digits) // 2 > MAX_INFLATE:
        report.err("%s: hex-профиль объявляет %d байт, пропущен" % (where, len(digits) // 2))
        return
    try:
        blob = binascii.unhexlify(digits[:len(digits) - len(digits) % 2])
    except (binascii.Error, ValueError) as exc:
        report.err("%s: hex-профиль не декодируется (%s)" % (where, exc))
        return
    if declared and len(blob) != declared:
        report.err("%s: объявлено %d байт, декодировалось %d" % (where, declared, len(blob)))
        blob = blob[:declared] if declared < len(blob) else blob
    report.add(Risk.PROVENANCE, where, "Профиль в hex-виде (%s)" % name,
               "%d байт" % len(blob),
               note="Профиль спрятан в текстовый чанк как hex: так делает ImageMagick. "
                    "Просмотрщики его не показывают, но метаданные внутри живые")
    if blob[:4] == b"8BIM":
        _parse_irb(report, blob, where, ctx)
    elif blob[:1] == b"\x1c":
        _parse_iptc(report, blob, where, ctx)
    elif blob[:6] == b"Exif\x00\x00":
        ctx["exif"] = True
        _walk_tiff(report, blob[6:], where, ctx)
    elif blob[:2] in (b"II", b"MM") and name in ("exif", "app1", "tiff"):
        ctx["exif"] = True
        _walk_tiff(report, blob, where, ctx)
    elif name in ("icc", "icm"):
        desc = _icc_desc(blob)
        report.add(Risk.STRUCTURAL, where, "ICC-профиль (hex)",
                   "%d байт%s" % (len(blob), (", описание: " + desc) if desc else ""),
                   note="Цветовой профиль, спрятанный в текстовый чанк")
    elif name == "xmp" or blob.lstrip()[:12].startswith((b"<?xpacket", b"<x:xmpmeta")):
        _parse_xmp(report, _text(blob), where, ctx)
    else:
        report.add(Risk.PROVENANCE, where, "Неопознанный hex-профиль",
                   "%d байт, начало: %s" % (len(blob), _hexdump(blob)),
                   note="Структуру не опознали: разбирать вручную на фазе 2")


# ===========================================================================
# WebP
# ===========================================================================

_VP8X_FLAGS = ((0x20, "ICC"), (0x10, "альфа"), (0x08, "EXIF"), (0x04, "XMP"), (0x02, "анимация"))


def _webp(report, data, ctx):
    """RIFF-контейнер: fourcc + little-endian размер, данные выравнены на чётность."""
    if len(data) < 12:
        report.err("WebP: файл короче заголовка RIFF")
        return
    riff_size = _u32(data, 4, "<")
    if riff_size + 8 > len(data):
        report.err("WebP: RIFF объявляет %d байт, в файле %d" % (riff_size + 8, len(data)))
    pos, guard, chunk_map = 12, 0, []
    while pos + 8 <= len(data) and guard < MAX_CHUNKS:
        guard += 1
        fourcc = bytes(data[pos:pos + 4])
        size = _u32(data, pos + 4, "<")
        if size > MAX_FILE:
            report.err("WebP: чанк %s объявляет %d байт" % (_text(fourcc), size))
            break
        body = data[pos + 8:pos + 8 + size]
        if pos + 8 + size > len(data):
            report.err("WebP: чанк %s обрезан" % _text(fourcc))
        chunk_map.append("%s (%d Б)" % (_text(fourcc), size))
        try:
            if fourcc == b"VP8X" and size >= 1:
                flags = body[0]
                names = [n for bit, n in _VP8X_FLAGS if flags & bit]
                report.add(Risk.STRUCTURAL, "WebP:VP8X", "Флаги контейнера",
                           "0x%02X: %s" % (flags, ", ".join(names) or "нет"),
                           note="Флаги объявляют, какие блоки метаданных лежат в файле. "
                                "Расхождение флагов и реальных чанков -- признак ручной правки")
                ctx["vp8x"] = names
            elif fourcc == b"EXIF":
                ctx["exif"] = True
                blob = bytes(body)
                if blob[:6] == b"Exif\x00\x00":
                    blob = blob[6:]
                _walk_tiff(report, blob, "WebP:EXIF", ctx)
            elif fourcc == b"XMP ":
                _parse_xmp(report, _text(body), "WebP:XMP", ctx)
            elif fourcc == b"ICCP":
                desc = _icc_desc(bytes(body))
                report.add(Risk.STRUCTURAL, "WebP:ICCP", "ICC-профиль",
                           "%d байт%s" % (size, (", описание: " + desc) if desc else ""),
                           note="Цветовой профиль: обычно сохраняем")
            elif fourcc == b"ANIM":
                report.add(Risk.STRUCTURAL, "WebP:ANIM", "Анимация", "%d байт" % size,
                           note="Анимированный WebP: кадры могут различаться содержимым")
        except Exception as exc:                  # noqa: BLE001
            report.err("WebP: чанк %s не разобран (%s: %s)"
                       % (_text(fourcc), type(exc).__name__, exc))
        pos += 8 + size + (size % 2)
    report.add(Risk.STRUCTURAL, "WebP", "Карта чанков (%d)" % len(chunk_map),
               "; ".join(chunk_map[:60]), note="Полный список RIFF-чанков с размерами")
    flags = ctx.get("vp8x") or []
    if "EXIF" in flags and not ctx.get("exif"):
        report.signal("inconsistent", "VP8X объявляет EXIF, но чанка EXIF в файле нет: "
                                      "метаданные вырезали грубо, флаг остался", "medium")


# ===========================================================================
# GIF
# ===========================================================================

_GIF_XMP_MAGIC = b"\x01\xff\xfe\xfd\xfc"


def _gif(report, data, ctx):
    """GIF: Comment Extension (0xFE) и Application Extension (0xFF), в т.ч. XMP Data."""
    if len(data) < 13:
        report.err("GIF: файл короче логического экрана")
        return
    packed = data[10]
    pos = 13
    if packed & 0x80:
        pos += 3 * (2 ** ((packed & 7) + 1))
    guard, blocks = 0, []
    while pos < len(data) and guard < MAX_GIF_BLOCKS:
        guard += 1
        marker = data[pos]
        if marker == 0x3B:
            blocks.append("Trailer")
            break
        if marker == 0x2C:                                  # изображение
            pos += 1
            if pos + 9 > len(data):
                report.err("GIF: дескриптор изображения обрезан")
                break
            ipacked = data[pos + 8]
            pos += 9
            if ipacked & 0x80:
                pos += 3 * (2 ** ((ipacked & 7) + 1))
            pos += 1                                        # LZW min code size
            pos = _gif_skip(data, pos)
            blocks.append("Image")
            continue
        if marker != 0x21:
            report.err("GIF: неизвестный блок 0x%02X на смещении %d" % (marker, pos))
            break
        if pos + 2 > len(data):
            report.err("GIF: расширение обрезано")
            break
        label = data[pos + 1]
        pos += 2
        if label == 0xFE:                                   # Comment Extension
            chunk, pos = _gif_blocks(data, pos)
            report.add(Risk.PROVENANCE, "GIF:Comment", "Комментарий", _text(chunk),
                       note="Комментарий GIF: сюда пишут имя конвертера, а иногда и автора")
            _remember(ctx, "GIF:Comment", _text(chunk))
            blocks.append("Comment")
            continue
        if label == 0xFF:                                   # Application Extension
            if pos >= len(data):
                break
            blen = data[pos]
            appid = bytes(data[pos + 1:pos + 1 + blen])
            pos += 1 + blen
            name = _text(appid)
            blocks.append("App:%s" % name.strip())
            if appid[:8] == b"XMP Data":
                end = data.find(_GIF_XMP_MAGIC, pos)
                if end == -1:
                    chunk, pos = _gif_blocks(data, pos)
                else:
                    chunk, pos = data[pos:end], end + 258
                _parse_xmp(report, _text(chunk), "GIF:XMP", ctx)
                continue
            chunk, pos = _gif_blocks(data, pos)
            if appid[:11] == b"NETSCAPE2.0":
                report.add(Risk.STRUCTURAL, "GIF:NETSCAPE2.0", "Зацикливание анимации",
                           "%d байт" % len(chunk), note="Штатное расширение анимации")
            else:
                report.add(Risk.PROVENANCE, "GIF:%s" % name.strip(),
                           "Application Extension",
                           "%d байт: %s" % (len(chunk), _text(chunk) or _hexdump(chunk)),
                           note="Блок, оставленный конкретной программой: само имя приложения "
                                "уже говорит, чем сделан файл")
                _remember(ctx, "GIF:App", name)
            continue
        chunk, pos = _gif_blocks(data, pos)                 # GCE, Plain Text
        blocks.append("Ext:0x%02X" % label)
    report.add(Risk.STRUCTURAL, "GIF", "Карта блоков (%d)" % len(blocks),
               "; ".join(blocks[:60]), note="Последовательность блоков GIF")


def _gif_blocks(data, pos):
    """Прочитать цепочку sub-блоков до нулевого терминатора."""
    out, guard = bytearray(), 0
    while pos < len(data) and guard < 4096:
        guard += 1
        size = data[pos]
        if size == 0:
            return bytes(out), pos + 1
        out += data[pos + 1:pos + 1 + size]
        pos += 1 + size
        if len(out) > MAX_INFLATE:
            break
    return bytes(out), pos


def _gif_skip(data, pos):
    _, pos = _gif_blocks(data, pos)
    return pos


# ===========================================================================
# HEIF / HEIC (ISOBMFF)
# ===========================================================================

_BOX_NOTE = {
    b"ftyp": "объявление бренда контейнера",
    b"meta": "контейнер метаданных",
    b"mdat": "сами данные изображения",
    b"moov": "таблицы дорожек (как в видео)",
    b"iinf": "список элементов: тут видно Exif и XMP",
    b"iloc": "смещения элементов",
    b"iprp": "свойства элементов (в т.ч. ICC)",
    b"uuid": "произвольный блок вендора",
}


def _heif(report, data, ctx):
    """ISOBMFF: перечисляем боксы верхнего уровня и спускаемся в meta/iinf.

    Полный разбор HEIF не делаем осознанно: цель фазы 0 -- честно сказать,
    что в контейнере есть метаданные, и показать его структуру.
    """
    report.err("HEIF/HEIC: поддержка ограниченная -- перечисляем боксы и элементы, "
               "значения EXIF внутри iloc не извлекаем")
    top = _boxes(report, data, 0, len(data), "HEIF")
    report.add(Risk.STRUCTURAL, "HEIF", "Боксы верхнего уровня (%d)" % len(top),
               "; ".join("%s (%d Б)%s" % (_text(t), sz,
                                          (" -- " + _BOX_NOTE[t]) if t in _BOX_NOTE else "")
                         for t, off, sz in top[:40]),
               note="Структура контейнера ISOBMFF")
    for typ, off, size in top:
        if typ != b"meta":
            continue
        # meta -- fullbox: 4 байта версии и флагов перед дочерними боксами
        inner = _boxes(report, data, off + 12, off + size, "HEIF:meta")
        report.add(Risk.STRUCTURAL, "HEIF:meta", "Боксы meta (%d)" % len(inner),
                   "; ".join("%s (%d Б)" % (_text(t), sz) for t, o, sz in inner[:40]),
                   note="Внутри meta лежат описания элементов: миниатюры, EXIF, XMP")
        for ityp, ioff, isize in inner:
            if ityp == b"iinf":
                _heif_iinf(report, data, ioff, isize, ctx)
            elif ityp == b"idat":
                report.add(Risk.PROVENANCE, "HEIF:idat", "Встроенные данные элементов",
                           "%d байт" % isize,
                           note="Блок данных элементов: там же лежат миниатюры")


def _boxes(report, data, start, stop, loc):
    """Список боксов (тип, смещение, размер) в заданном диапазоне."""
    out, pos, guard = [], start, 0
    stop = min(stop, len(data))
    while pos + 8 <= stop and guard < MAX_BOXES:
        guard += 1
        size = _u32(data, pos)
        typ = bytes(data[pos + 4:pos + 8])
        if size == 1:
            if pos + 16 > stop:
                report.err("%s: largesize-бокс %s обрезан" % (loc, _text(typ)))
                break
            size = struct.unpack_from(">Q", data, pos + 8)[0]
        elif size == 0:
            size = stop - pos
        if size < 8 or pos + size > stop:
            report.err("%s: бокс %s объявляет %d байт, доступно %d"
                       % (loc, _text(typ), size, stop - pos))
            out.append((typ, pos, stop - pos))
            break
        out.append((typ, pos, size))
        pos += size
    return out


def _heif_iinf(report, data, off, size, ctx):
    """iinf: список infe. Нас интересует наличие элементов Exif и mime (XMP)."""
    body = bytes(data[off + 8:off + size])
    kinds = []
    for match in re.finditer(b"infe", body):
        start = match.end()          # сразу за типом бокса лежит version+flags
        if start >= len(body):
            break
        version = body[start]
        # v2: version+flags(4) item_ID(2) protection(2) item_type(4)
        # v3: version+flags(4) item_ID(4) protection(2) item_type(4)
        if version == 2:
            kinds.append(_text(body[start + 8:start + 12]))
        elif version == 3:
            kinds.append(_text(body[start + 10:start + 14]))
        else:
            kinds.append("infe v%d (тип только в content_type)" % version)
        if len(kinds) > MAX_BOXES:
            break
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    report.add(Risk.STRUCTURAL, "HEIF:iinf", "Элементы контейнера (%d)" % len(kinds),
               "; ".join("%s x%d" % (k, n) for k, n in sorted(counts.items())[:30]),
               note="Типы элементов внутри HEIF")
    if any("Exif" in k for k in kinds):
        ctx["exif"] = True
        report.add(Risk.PROVENANCE, "HEIF:iinf", "Элемент Exif", "присутствует",
                   note="В контейнере лежит полноценный EXIF -- значит, там же с высокой "
                        "вероятностью GPS, серийники и модель камеры. Для точного разбора "
                        "нужен обход iloc, которого в фазе 0 нет")
    if any("mime" in k for k in kinds):
        report.add(Risk.PROVENANCE, "HEIF:iinf", "Элемент mime (обычно XMP)", "присутствует",
                   note="Элемент с произвольным MIME: чаще всего это XMP-пакет")


# ===========================================================================
# exiftool: необязательное обогащение
# ===========================================================================

def _exiftool_exe():
    """exiftool на PATH или в ./tools/exiftool.exe. Нет -- значит нет."""
    found = shutil.which("exiftool")
    if found:
        return found
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.join(root, "tools", "exiftool.exe"),
                      os.path.join(root, "tools", "exiftool"),
                      os.path.join("tools", "exiftool.exe")):
        if os.path.isfile(candidate):
            return candidate
    return None


def _exiftool(report, path, ctx):
    """Дополнить находки тем, чего не разобрали сами (прежде всего MakerNotes).

    Содержимое файла в shell не попадает: вызов списком аргументов, без shell.
    Любой сбой -- молчим и работаем дальше.
    """
    exe = _exiftool_exe()
    if not exe:
        return
    try:
        proc = subprocess.run(
            [exe, "-j", "-a", "-u", "-G1", os.fspath(path)],
            capture_output=True, timeout=25, shell=False,
        )
        rows = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except Exception:                             # noqa: BLE001 -- обогащение необязательное
        return
    if not rows or not isinstance(rows[0], dict):
        return
    known = ctx.setdefault("seen_values", set())
    added = 0
    for key, value in rows[0].items():
        if ":" not in key:
            continue
        group, _, name = key.partition(":")
        if group in ("ExifTool", "System", "File", "Composite"):
            continue
        text = str(value)
        if not text or text.lower() in known or added >= 120:
            continue
        low = name.lower()
        if group.startswith("MakerNotes") or "serial" in low or "owner" in low:
            risk = Risk.IDENTITY
        elif "gps" in low:
            risk = Risk.IDENTITY
        elif "software" in low or "date" in low or "camera" in low:
            risk = Risk.PROVENANCE
        else:
            continue
        added += 1
        report.add(risk, "exiftool:%s" % group, name, text,
                   note="Найдено внешним exiftool: наш разбор до этого поля не добирается")
    if added:
        report.signal("producer", "exiftool дополнил разбор %d полем(ями), в основном "
                                  "расшифрованные MakerNotes" % added, "high")


# ===========================================================================
# сигналы
# ===========================================================================

_AI_TOOLS = ("midjourney", "stable diffusion", "stablediffusion", "dall-e", "dall e",
             "dalle", "openai", "firefly", "comfyui", "automatic1111", "invokeai",
             "novelai", "leonardo", "ideogram", "recraft", "flux.1", "imagen", "sora",
             "nano banana", "seedream", "qwen-image", "hunyuan", "fooocus",
             "diffusionbee", "draw things", "krea", "runway", "generative fill",
             "playground v", "grok-image", "gemini image")

_EDITORS = ("photoshop", "lightroom", "gimp", "paint.net", "affinity", "capture one",
            "snapseed", "pixelmator", "darktable", "rawtherapee", "canva", "figma",
            "picsart", "facetune", "luminar", "topaz", "dxo", "corel", "paintshop",
            "acdsee", "irfanview", "xnview", "photoscape", "fotor", "polarr", "vsco",
            "adobe", "capcut", "inkscape", "krita", "photopea")

_LIBS = ("imagemagick", "graphicsmagick", "pillow", "matplotlib", "cairo", "skia",
         "libpng", "libwebp", "ffmpeg", "lavc", "gd-jpeg", "chromium", "chrome",
         "wkhtmltopdf", "reportlab", "opencv", "sharp", "node-canvas", "plotly",
         "libjpeg", "mozjpeg", "jpeg-recompress", "google")


def _remember(ctx, label, value):
    """Складываем значения в общий мешок: по нему потом строим сигналы."""
    text = str(value).strip()
    if not text:
        return
    ctx.setdefault("seen_values", set()).add(text.lower()[:200])
    ctx.setdefault("fields", []).append((str(label), text))


def _signals(report, ctx):
    """Выводы поверх сырых находок."""
    if ctx.get("gps"):
        report.signal("hazard", "в файле есть GPS-координаты: %s -- точка съёмки восстанавливается "
                                "до дома" % ctx["gps"], "high")
    serials = [(l, v) for l, v in ctx.get("fields", [])
               if "serial" in l.lower() or l in ("BodySerialNumber", "LensSerialNumber")]
    if serials:
        report.signal("hazard", "серийный номер техники: " +
                      "; ".join("%s=%s" % (l, v) for l, v in serials[:4]), "high")
    if ctx.get("makernote"):
        report.signal("producer", "MakerNote на %d байт: проприетарный блок камеры, внутри "
                                  "обычно серийник экземпляра и счётчик затвора" % ctx["makernote"],
                      "high")
    if ctx.get("c2pa"):
        report.signal("producer", "в файле лежит подписанный манифест C2PA: происхождение "
                                  "задокументировано криптографически, а его срез -- "
                                  "детектируемое событие", "high")
    software = [v for l, v in ctx.get("fields", [])
                if l in ("Software", "xmp:CreatorTool", "tiff:Software", "softwareAgent",
                         "COM", "GIF:Comment", "GIF:App", "Originating Program",
                         "Program Version")]
    for value in software:
        low = value.lower()
        if any(t in low for t in _AI_TOOLS):
            report.signal("ai", "поле софта называет ИИ-инструмент: %s" % value, "high")
        elif any(t in low for t in _EDITORS):
            report.signal("producer", "файл прошёл через графический редактор: %s" % value, "high")
        elif any(t in low for t in _LIBS):
            report.signal("producer", "файл сделан библиотекой или конвертером: %s" % value, "high")
        else:
            report.signal("producer", "поле софта: %s" % value, "medium")
    dt = dict(ctx.get("fields", []))
    if dt.get("DateTime") and dt.get("DateTimeOriginal") and dt["DateTime"] != dt["DateTimeOriginal"]:
        report.signal("inconsistent", "DateTime (%s) не совпадает с DateTimeOriginal (%s): "
                                      "файл пересохраняли после съёмки"
                      % (dt["DateTime"], dt["DateTimeOriginal"]), "medium")
    if ctx.get("bad_crc"):
        report.signal("inconsistent", "битых CRC чанков: %d. Либо файл повреждён, либо "
                                      "метаданные правили байтами вручную" % ctx["bad_crc"],
                      "high")
    if ctx.get("thumbnail"):
        report.signal("hazard", "встроенная миниатюра на %d байт: могла быть сделана до "
                                "ретуши и показывать исходный кадр" % ctx["thumbnail"], "medium")
    if report.fmt == "jpeg" and not ctx.get("exif") and not ctx.get("xmp") \
            and not ctx.get("photoshop") and not ctx.get("c2pa"):
        report.signal("scrubbed", "JPEG вообще без EXIF, XMP и IPTC: файл либо уже чистили, "
                                  "либо он прошёл через мессенджер, который пережимает картинки",
                      "medium")


# ===========================================================================
# точка входа
# ===========================================================================

def inspect_image(path, fmt=None) -> Report:
    """Инспекция растрового изображения. Никогда не бросает исключение наружу."""
    fmt = fmt or sniff.sniff(path)
    report = Report(path=str(path), fmt=fmt)
    ctx = {}
    try:
        try:
            report.size = os.path.getsize(path)
        except OSError as exc:
            report.err("не удалось узнать размер файла: %s" % exc)
        try:
            with open(path, "rb") as fh:          # строго на чтение, файл не трогаем
                data = fh.read(MAX_FILE + 1)
        except OSError as exc:
            report.err("файл не читается: %s" % exc)
            return report
        if len(data) > MAX_FILE:
            report.err("файл больше %d МБ: разобраны только первые %d МБ"
                       % (MAX_FILE // 1048576, MAX_FILE // 1048576))
            data = data[:MAX_FILE]
        if not data:
            report.err("файл пустой")
            return report

        handlers = {"jpeg": _jpeg, "png": _png, "tiff": _tiff,
                    "webp": _webp, "gif": _gif, "heic": _heif}
        handler = handlers.get(fmt)
        if handler is None:
            report.err("формат %r не растровое изображение, инспектор картинок пропускает файл"
                       % fmt)
            return report
        try:
            handler(report, data, ctx)
        except Exception as exc:                  # noqa: BLE001 -- битый файл не валит бота
            report.err("разбор %s прерван: %s: %s" % (fmt, type(exc).__name__, exc))
        try:
            _exiftool(report, path, ctx)
        except Exception as exc:                  # noqa: BLE001
            report.err("exiftool не отработал: %s" % exc)
        try:
            _signals(report, ctx)
        except Exception as exc:                  # noqa: BLE001
            report.err("сигналы не построены: %s: %s" % (type(exc).__name__, exc))
    except Exception as exc:                      # noqa: BLE001 -- последний рубеж
        report.err("инспектор упал целиком: %s: %s" % (type(exc).__name__, exc))
    return report


def _tiff(report, data, ctx):
    """TIFF: прямой обход IFD, включая цепочку следующих IFD."""
    ctx["exif"] = True
    _walk_tiff(report, data, "TIFF", ctx)


# ===========================================================================
# самопроверка: собираем файлы из байтов и прогоняем инспектор
# ===========================================================================

def _demo():
    import tempfile

    def pack_ifd(en, tags, base, next_off=0):
        """Собрать IFD: значения длиннее 4 байт уезжают в область данных."""
        ifd_len = 2 + 12 * len(tags) + 4
        body, blob = struct.pack(en + "H", len(tags)), b""
        for tag, typ, count, raw in tags:
            if len(raw) <= 4:
                val = raw + b"\x00" * (4 - len(raw))
            else:
                val = struct.pack(en + "I", base + ifd_len + len(blob))
                blob += raw + (b"\x00" if len(raw) % 2 else b"")
            body += struct.pack(en + "HHI", tag, typ, count) + val
        return body + struct.pack(en + "I", next_off) + blob

    def rational(*pairs):
        return b"".join(struct.pack("<II", n, d) for n, d in pairs)

    en = "<"
    artist = "Игорь Татаров".encode("utf-8") + b"\x00"
    model = b"NIKON D850\x00"
    # IFD0: Artist, Model, GPS-указатель
    ifd0_data_len = len(artist) + len(artist) % 2 + len(model) + len(model) % 2
    gps_base = 8 + (2 + 12 * 3 + 4) + ifd0_data_len
    # 55°45'4.48" N, 37°37'6.30" E -- центр Москвы, 55.751244 / 37.618417
    gps = pack_ifd(en, [
        (0x0001, 2, 2, b"N\x00"),
        (0x0002, 5, 3, rational((55, 1), (45, 1), (448, 100))),
        (0x0003, 2, 2, b"E\x00"),
        (0x0004, 5, 3, rational((37, 1), (37, 1), (630, 100))),
        (0x001D, 2, 11, b"2024:05:17\x00"),
    ], gps_base)
    ifd0 = pack_ifd(en, [
        (0x013B, 2, len(artist), artist),
        (0x0110, 2, len(model), model),
        (0x8825, 4, 1, struct.pack(en + "I", gps_base)),
    ], 8)
    tiff = b"II" + struct.pack(en + "HI", 42, 8) + ifd0 + gps
    app1 = b"Exif\x00\x00" + tiff
    jpeg = (b"\xff\xd8"
            + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xfe" + struct.pack(">H", 2 + 21) + b"Created with GIMP 2.10"[:21]
            + b"\xff\xdb" + struct.pack(">H", 2 + 65) + b"\x00" + bytes(range(1, 65))
            + b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x00\x00\x3f\x00\x00"
            + b"\x00" * 8 + b"\xff\xd9")

    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body
                + struct.pack(">I", binascii.crc32(typ + body) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"tEXt", b"Author\x00Igor Tatarov")
           + chunk(b"tEXt", b"Comment\x00C:\\Users\\testuser\\shot.png")
           + chunk(b"zTXt", b"Software\x00\x00" + zlib.compress(b"Adobe Photoshop 25.0"))
           + chunk(b"tIME", struct.pack(">HBBBBB", 2024, 5, 17, 12, 30, 5))
           + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
           + chunk(b"IEND", b""))

    def riff(fourcc, body):
        return fourcc + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")

    webp_body = (b"WEBP" + riff(b"VP8X", b"\x0c\x00\x00\x00\x00\x00\x00\x00\x00\x00")
                 + riff(b"EXIF", tiff)
                 + riff(b"XMP ", b'<x:xmpmeta><rdf:RDF><rdf:Description '
                                 b'xmp:CreatorTool="Midjourney v6"/></rdf:RDF></x:xmpmeta>'))
    webp = b"RIFF" + struct.pack("<I", len(webp_body)) + webp_body

    def gif_sub(blob):
        return bytes([len(blob)]) + blob + b"\x00"

    gif = (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x00, 0, 0)
           + b"\x21\xfe" + gif_sub(b"Made by ImageMagick 7.1")
           + b"\x21\xff" + bytes([11]) + b"XMP DataXMP"
           + b'<x:xmpmeta><rdf:Description dc:creator="Igor T"/></x:xmpmeta>'
           + b"\x01" + bytes(range(255, -1, -1)) + b"\x00"
           + b"\x3b")

    def box(typ, body):
        return struct.pack(">I", len(body) + 8) + typ + body

    infe = box(b"infe", b"\x02\x00\x00\x00" + b"\x00\x01" + b"\x00\x00" + b"Exif" + b"\x00")
    heic = (box(b"ftyp", b"heic" + b"\x00\x00\x00\x00" + b"heic" + b"mif1")
            + box(b"meta", b"\x00\x00\x00\x00"
                  + box(b"hdlr", b"\x00" * 4 + b"\x00" * 4 + b"pict" + b"\x00" * 13)
                  + box(b"iinf", b"\x00\x00\x00\x00" + b"\x00\x01" + infe))
            + box(b"mdat", b"\x00" * 16))

    tmp = tempfile.mkdtemp(prefix="insp_image_")
    cases = {"ok.jpg": jpeg, "ok.png": png, "ok.tif": tiff, "ok.webp": webp,
             "ok.gif": gif, "ok.heic": heic,
             "cut.jpg": jpeg[:len(app1) // 2],
             "junk.jpg": b"\xff\xd8\xff" + os.urandom(2000),
             "empty.png": b"\x89PNG\r\n\x1a\n",
             "loop.tif": b"II" + struct.pack("<HI", 42, 8)
                         + pack_ifd("<", [(0x013B, 2, 5, b"loop\x00")], 8, next_off=8)}
    paths = {}
    for name, blob in cases.items():
        paths[name] = os.path.join(tmp, name)
        with open(paths[name], "wb") as fh:
            fh.write(blob)

    for name in cases:
        rep = inspect_image(paths[name])
        print("=" * 72)
        print("%s  fmt=%s  size=%d  находок=%d  сигналов=%d  ошибок=%d"
              % (name, rep.fmt, rep.size, len(rep.findings), len(rep.signals), len(rep.errors)))
        for f in rep.findings:
            print("  [%s] %-28s %-26s %s" % (f.risk.value[:4], f.location, f.label, f.value))
        for s in rep.signals:
            print("  <%s/%s> %s" % (s.kind, s.confidence, s.detail))
        for e in rep.errors:
            print("  ! %s" % e)

    # --- проверки: ломается логика -- падает здесь ------------------------
    jp = inspect_image(paths["ok.jpg"])
    flat = {(f.label, f.value) for f in jp.findings}
    assert any(l == "Artist" and "Игорь" in v for l, v in flat), "Artist не найден"
    assert any("55.7512" in v and "37.6184" in v for _, v in flat), "GPS не пересчитан"
    assert any(s.kind == "hazard" and "GPS" in s.detail for s in jp.signals), "нет сигнала GPS"
    assert any(l == "Комментарий JPEG" for l, _ in flat), "COM не разобран"
    assert any(l == "Таблицы квантования" for l, _ in flat), "DQT не найден"
    assert any(f.label == "Таблицы квантования" and not f.removable for f in jp.findings)

    pg = inspect_image(paths["ok.png"])
    pflat = {(f.label, f.value) for f in pg.findings}
    assert any(l == "Author" and "Igor" in v for l, v in pflat), "tEXt Author не найден"
    assert any(l == "Comment" and "Users" in v for l, v in pflat), "второй tEXt потерян"
    assert any(l == "Software" and "Photoshop" in v for l, v in pflat), "zTXt не распакован"
    assert any(s.kind == "producer" and "редактор" in s.detail for s in pg.signals)

    tf = inspect_image(paths["ok.tif"])
    assert any(f.label == "Artist" for f in tf.findings), "TIFF: IFD не обойден"
    wb = inspect_image(paths["ok.webp"])
    assert any(f.label == "Флаги контейнера" for f in wb.findings), "WebP: VP8X не разобран"
    assert any(s.kind == "ai" for s in wb.signals), "WebP: ИИ-инструмент в XMP не распознан"
    gf = inspect_image(paths["ok.gif"])
    assert any(f.label == "Комментарий" for f in gf.findings), "GIF: комментарий не найден"
    assert any(f.label == "dc:creator" for f in gf.findings), "GIF: XMP Data не разобран"
    hc = inspect_image(paths["ok.heic"])
    assert any(f.label == "Элемент Exif" for f in hc.findings), "HEIF: iinf не разобран"
    assert hc.errors, "HEIF: должна быть пометка об ограниченной поддержке"

    for broken in ("cut.jpg", "junk.jpg", "empty.png", "loop.tif"):
        rep = inspect_image(paths[broken])
        assert isinstance(rep, Report), broken
        assert rep.errors, "%s: ожидали записи в errors" % broken

    print("=" * 72)
    print("самопроверка пройдена")


if __name__ == "__main__":
    _demo()
