# -*- coding: utf-8 -*-
"""Чистка растровых картинок БЕЗ перекодирования: JPEG, PNG, WebP, GIF.

Фаза 1. Только stdlib (struct, zlib, binascii, hashlib, re) -- ни Pillow, ни
обязательного exiftool.

ГЛАВНЫЙ ПРИНЦИП: НЕ ПЕРЕКОДИРОВАТЬ. Картинка пересобирается как КОНТЕЙНЕР:
сжатые данные (энтропийный поток JPEG, IDAT PNG, VP8/VP8L WebP, LZW GIF)
копируются байт в байт. Перекодирование потеряло бы качество и заменило бы
таблицы квантования JPEG -- а они сами фингерпринтят энкодер: вместо отпечатка
камеры получился бы отпечаток нашей библиотеки. Это хуже, чем не чистить.

Проверка результата -- не наша собственная логика, а повторный прогон
инспектора (inspect_file(dst).critical == 0). Здесь же, в verify_image(),
доказывается, что файл не испорчен: структура валидна, размеры те же, сжатые
данные совпали бит в бит, ориентация сохранена.

РЕШЕНИЯ (каждое осознанное):

* ОРИЕНТАЦИЯ. Телефон кладёт кадр в файл повёрнутым, а правильный поворот
  задаёт EXIF Orientation (0x0112). Снести весь APP1 -- значит получить
  картинку на боку. Физический поворот требует перекодирования (запрещено).
  Выбрано (а)+(в): если Orientation в 2..8, пишем МИНИМАЛЬНЫЙ EXIF -- один IFD,
  один тег, 36 байт сегмента, порядок байтов как в исходнике; всё остальное
  (GPS, серийники, даты, MakerNote, миниатюра) уходит. Orientation = 1 или его
  нет -- EXIF не пишем вообще (1 и так значение по умолчанию). То же для PNG
  (чанк eXIf) и WebP (чанк EXIF).
* ICC-ПРОФИЛЬ -- STRUCTURAL: без него цвета едут. STEALTH сохраняет, PARANOID
  убирает с предупреждением (имя профиля часто названо по монитору/принтеру).
  keep={"icc"} оставляет профиль и в PARANOID.
* JFIF (APP0) оставляем в обоих профилях: плотность пикселей -- STRUCTURAL, а
  отсутствие и EXIF, и JFIF само по себе заметно. Но вшитую миниатюру JFIF
  отрезаем (Xthumbnail=Ythumbnail=0): она могла быть сделана до ретуши.
* Adobe APP14 убираем, КРОМЕ случая, когда без него цвета ломаются: 4-компонентный
  (CMYK/YCCK) или 3-компонентный с transform=0 (RGB в JPEG). Тогда это уже не
  метаданные, а параметр декодирования -- сохраняем 12 байт и помечаем KEPT.
* Таблицы квантования (DQT) убрать нельзя -- Act.UNREMOVABLE.
* Данные ПОСЛЕ конца изображения (после EOI/IEND/RIFF/GIF-trailer) выбрасываем:
  классический тайник, и инспектор считает их ENVIRONMENT. Побочно уходят
  MPF-кадры, видео Motion Photo и gain map Ultra HDR -- это честная цена.
* TIFF и HEIC: честный отказ (ok=False). Метаданные TIFF лежат в самих IFD
  вперемешку со ссылками на данные, HEIF -- в iloc-элементах; пересборка без
  специального кода рискует испортить файл. Отказ лучше порчи.
* Профили: для картинок STEALTH и PARANOID одинаково убирают ВСЕ контейнеры
  метаданных, включая PROVENANCE (даты, Make/Model, Software): по условию задачи
  даты и серийники обязаны исчезнуть, а выборочная пересборка EXIF потребовала
  бы переписывать смещения IFD. Отличие профилей -- ICC. Файл без EXIF для
  картинки -- рядовое состояние (так делают мессенджеры), цена невелика.
  REGENERATE для картинок не имеет смысла и трактуется как PARANOID.

Никаких исключений наружу: каждая секция в try/except, проблема -- res.err().
Вход не модифицируется никогда: пишем только в dst, sha256 входа сверяется
до и после. Логов нет: имена файлов и содержимое находок не печатаются.

Публичный API:
    clean_image(src, dst, profile=Profile.STEALTH, keep=None) -> CleanResult
    verify_image(src, dst) -> dict
"""

from __future__ import annotations

import binascii
import hashlib
import os
import re
import struct
import zlib
from collections import Counter

from core import sniff
from core.cleanmodel import Act, CleanResult, Profile
from core.model import clip

SUPPORTED = ("jpeg", "png", "webp", "gif")

# --- пределы: на вход приходят битые и намеренно кривые файлы ---------------
MAX_FILE = 50 * 1024 * 1024      # больше в память не берём (как и инспектор)
MAX_SEGMENTS = 4096              # маркеров JPEG
MAX_CHUNKS = 100_000             # чанков PNG/WebP/блоков GIF (IDAT по 8 КБ -- тысячи)
MAX_RAW = 256 * 1024 * 1024      # потолок распаковки IDAT при проверке (бомбы)
MAX_IFDS = 16
MAX_ENTRIES = 1000
MAX_NAMED = 40                   # поимённых действий на один EXIF

# Поворот/отражение по коду EXIF Orientation. 1 -- норма, отдельно не описываем.
_ORIENT = {
    2: "зеркально по горизонтали", 3: "поворот на 180°", 4: "зеркально по вертикали",
    5: "зеркало и поворот на 270°", 6: "поворот на 90° по часовой стрелке",
    7: "зеркало и поворот на 90°", 8: "поворот на 270° по часовой (90° против)",
}

C2PA_NOTE = (
    "Криптографически подписанный манифест происхождения (C2PA / Content Credentials) удалён. "
    "Сам срез ДЕТЕКТИРУЕМ: получатель видит, что подпись была и её убрали; при наличии "
    "soft-binding или водяного знака происхождение восстанавливается и без этого блока"
)


# ===========================================================================
# мелочи
# ===========================================================================

def _sha(blob) -> str:
    return hashlib.sha256(blob).hexdigest()


def _file_sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_path(a, b) -> bool:
    """dst не должен совпасть с src: вход не перезаписываем никогда."""
    try:
        if os.path.exists(b) and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _ident(pay) -> str:
    """Метка сегмента (строка до NUL) для отчёта: по ней видно, кто его записал."""
    return clip(bytes(pay[:24]).split(b"\0")[0].decode("latin-1"), 40)


# ===========================================================================
# EXIF: лёгкий разбор (ориентация + что вообще лежало) и минимальная пересборка
# ===========================================================================

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
_SUB_IFD = {0x8769: "ExifIFD", 0x8825: "GPS", 0xA005: "Interop", 0x014A: "SubIFD"}
_XP = (0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F)

# Теги, которые показываем поимённо (значение попадает в before).
_EXIF_NAMES = {
    0x010E: "ImageDescription", 0x010F: "Make", 0x0110: "Model", 0x0131: "Software",
    0x0132: "DateTime", 0x013B: "Artist", 0x013C: "HostComputer", 0x8298: "Copyright",
    0x9003: "DateTimeOriginal", 0x9004: "DateTimeDigitized", 0x9286: "UserComment",
    0xA420: "ImageUniqueID", 0xA430: "CameraOwnerName", 0xA431: "BodySerialNumber",
    0xA433: "LensMake", 0xA434: "LensModel", 0xA435: "LensSerialNumber",
    0xC614: "UniqueCameraModel", 0xC62F: "CameraSerialNumber",
    0x9C9B: "XPTitle", 0x9C9C: "XPComment", 0x9C9D: "XPAuthor", 0x9C9E: "XPKeywords",
    0x9C9F: "XPSubject",
}


def _exif_text(tag, typ, raw) -> str:
    if raw is None:
        return ""
    if tag in _XP:
        return bytes(raw).decode("utf-16-le", "replace").rstrip("\0")
    if tag == 0x9286:                      # UserComment: первые 8 байт -- кодировка
        return bytes(raw[8:]).decode("utf-8", "replace").strip("\0 ")
    if typ == 2:
        return bytes(raw).split(b"\0")[0].decode("utf-8", "replace")
    return "<%d байт>" % len(raw)


def _exif_scan(blob) -> dict:
    """Лёгкий обход TIFF/EXIF. Не бросает, обход ограничен (EXIF умеет зацикливаться).

    -> {order, orient, named[(имя, значение)], gps, maker, thumb, other, bad}
    """
    out = {"order": None, "orient": None, "named": [], "gps": 0, "maker": 0,
           "thumb": 0, "other": 0, "bad": ""}
    blob = bytes(blob)
    if len(blob) < 8 or blob[:2] not in (b"II", b"MM"):
        out["bad"] = "нет заголовка TIFF"
        return out
    en = "<" if blob[:2] == b"II" else ">"
    out["order"] = blob[:2]
    try:
        if struct.unpack_from(en + "H", blob, 2)[0] != 42:
            out["bad"] = "неверная сигнатура TIFF"
            return out
        queue = [(struct.unpack_from(en + "I", blob, 4)[0], "IFD0")]
        seen = set()
        while queue and len(seen) < MAX_IFDS:
            off, name = queue.pop(0)
            if off < 8 or off + 2 > len(blob) or off in seen:
                continue
            seen.add(off)
            count = min(struct.unpack_from(en + "H", blob, off)[0], MAX_ENTRIES)
            for i in range(count):
                e = off + 2 + i * 12
                if e + 12 > len(blob):
                    break
                tag, typ, cnt = struct.unpack_from(en + "HHI", blob, e)
                size = _TYPE_SIZE.get(typ, 0) * cnt
                raw = None
                if 0 < size <= 4:
                    raw = blob[e + 8:e + 8 + size]
                elif 4 < size <= 4096:
                    voff = struct.unpack_from(en + "I", blob, e + 8)[0]
                    if voff >= 8 and voff + size <= len(blob):
                        raw = blob[voff:voff + size]
                if tag in _SUB_IFD and typ in (3, 4, 13):
                    ptr = struct.unpack_from(en + ("H" if typ == 3 else "I"), blob, e + 8)[0]
                    queue.append((ptr, _SUB_IFD[tag]))
                    continue
                if name == "GPS":
                    out["gps"] += 1
                elif tag == 0x927C:
                    out["maker"] = max(out["maker"], size)
                elif name == "IFD0" and tag == 0x0112:
                    if typ == 3 and raw and len(raw) >= 2:
                        out["orient"] = struct.unpack_from(en + "H", raw, 0)[0]
                elif name.startswith("IFD") and name != "IFD0" and tag in (0x0201, 0x0202):
                    out["thumb"] = max(out["thumb"], 1)
                    if tag == 0x0202 and raw and len(raw) >= 4:
                        out["thumb"] = max(out["thumb"], struct.unpack_from(en + "I", raw, 0)[0])
                elif tag in _EXIF_NAMES:
                    out["named"].append((_EXIF_NAMES[tag], _exif_text(tag, typ, raw)))
                else:
                    out["other"] += 1
            nxt_at = off + 2 + count * 12
            if name.startswith("IFD") and nxt_at + 4 <= len(blob):
                nxt = struct.unpack_from(en + "I", blob, nxt_at)[0]
                if nxt:
                    queue.append((nxt, "IFD%d" % (int(name[3:]) + 1)))
    except (struct.error, ValueError, IndexError) as exc:
        out["bad"] = "EXIF оборван (%s)" % type(exc).__name__
    return out


def _min_tiff(orient, order) -> bytes:
    """Минимальный TIFF-поток: один IFD0 с единственным тегом Orientation (26 байт)."""
    e = "<" if order == b"II" else ">"
    return (order + struct.pack(e + "HI", 42, 8) + struct.pack(e + "H", 1)
            + struct.pack(e + "HHIHH", 0x0112, 3, 1, orient, 0) + struct.pack(e + "I", 0))


def _min_app1(orient, order) -> bytes:
    """Сегмент APP1 JPEG с минимальным EXIF: 36 байт вместе с маркером (поле длины = 34)."""
    body = b"Exif\0\0" + _min_tiff(orient, order)
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _exif_acts(res, scan, loc, size, kept_orient) -> None:
    """Построчный отчёт: что было в EXIF и что осталось (только Orientation)."""
    if scan["bad"]:
        res.act(Act.REMOVED, loc, "EXIF (не разобран)", "%d байт" % size,
                note="Блок не разобрался (%s) и удалён целиком; ориентацию восстановить "
                     "нельзя" % scan["bad"])
        return
    for label, value in scan["named"][:MAX_NAMED]:
        res.act(Act.REMOVED, loc, "EXIF " + label, value)
    hidden = max(0, len(scan["named"]) - MAX_NAMED)
    if scan["gps"]:
        res.act(Act.REMOVED, loc, "GPS", "%d тег(ов)" % scan["gps"],
                note="Координаты, высота, время и метки GPS: по ним находят человека, а не файл")
    if scan["maker"]:
        res.act(Act.REMOVED, loc, "MakerNote", "%d байт" % scan["maker"],
                note="Проприетарный блок камеры: серийник экземпляра, счётчик затвора, настройки")
    if scan["thumb"]:
        res.act(Act.REMOVED, loc, "Встроенная миниатюра",
                "%d байт" % scan["thumb"] if scan["thumb"] > 1 else "есть",
                note="Миниатюра могла быть сделана ДО ретуши и кропа")
    if scan["other"] or hidden:
        res.act(Act.REMOVED, loc, "Прочие теги EXIF", "%d шт" % (scan["other"] + hidden),
                note="Параметры съёмки и геометрия: вместе фингерпринтят конкретную камеру")
    o = scan["orient"]
    if kept_orient:
        res.act(Act.KEPT, loc, "Orientation (ориентация кадра)", str(o),
                "%d (единственный тег минимального EXIF)" % o,
                note="STRUCTURAL: снимок лежит в файле повёрнутым (%s). Без этого тега "
                     "картинка отобразится на боку. Физический поворот потребовал бы "
                     "перекодирования, поэтому оставлен один тег, остальное удалено" % _ORIENT[o])
    elif o == 1:
        res.act(Act.REMOVED, loc, "Orientation", "1",
                note="Значение по умолчанию: без тега картинка отображается так же")
    elif o in _ORIENT:
        res.act(Act.REMOVED, loc, "Orientation", str(o),
                note="Тег не сохранён: этот EXIF-блок просмотрщики не используют (стоит после "
                     "данных изображения, повторяет уже сохранённый или лежит в WebP без VP8X)")


# ===========================================================================
# JPEG
# ===========================================================================

_SOF = frozenset(m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC))
# Всё, что нужно декодеру: SOFn, DHT, DAC, DQT, DRI, DNL, SOS, JPEG-LS, TEM.
_JPEG_IMG = frozenset(range(0xC0, 0xD0)) | {0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF, 0xF7, 0xF8, 0x01}
# Конец энтропийных данных: FF, за которым не 00 (стаффинг), не RSTn и не FF (fill).
_SCAN_END = re.compile(rb"\xff(?=[^\x00\xd0-\xd7\xff])")


def _jpeg_walk(data):
    """Проход по маркерам. -> (segs, end, err); segs=[(маркер, start, stop)].

    У SOS stop включает энтропийные данные до следующего маркера. end -- смещение
    сразу за EOI. Данные после первого EOI в segs не входят.
    """
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return [], None, "нет маркера SOI"
    segs = [(0xD8, 0, 2)]
    pos = 2
    while True:
        if len(segs) > MAX_SEGMENTS:
            return segs, None, "больше %d сегментов" % MAX_SEGMENTS
        if pos >= n:
            return segs, None, "файл оборван: нет маркера EOI"
        if data[pos] != 0xFF:
            return segs, None, "на смещении %d ожидался маркер, найдено 0x%02X" % (pos, data[pos])
        while pos + 1 < n and data[pos + 1] == 0xFF:      # fill-байты
            pos += 1
        if pos + 1 >= n:
            return segs, None, "поток обрывается на маркере"
        m = data[pos + 1]
        if m == 0xD9:
            segs.append((0xD9, pos, pos + 2))
            return segs, pos + 2, None
        if m == 0x01:
            segs.append((0x01, pos, pos + 2))
            pos += 2
            continue
        if 0xD0 <= m <= 0xD8:
            return segs, None, "неожиданный маркер 0x%02X вне энтропийных данных" % m
        if pos + 4 > n:
            return segs, None, "маркер 0x%02X без длины (файл обрезан)" % m
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if length < 2:
            return segs, None, "маркер 0x%02X объявляет длину %d" % (m, length)
        stop = pos + 2 + length
        if stop > n:
            return segs, None, "сегмент 0x%02X обрезан (нужно %d байт, есть %d)" % (
                m, length, n - pos - 2)
        if m == 0xDA:
            hit = _SCAN_END.search(data, stop)
            stop = hit.start() if hit else n
        segs.append((m, pos, stop))
        pos = stop


def _jpeg_info(data, segs):
    """(ширина, высота, число компонентов) из первого SOF или None."""
    for m, a, b in segs:
        if m in _SOF and b - a >= 10:
            _, h, w, nf = struct.unpack_from(">BHHB", data, a + 4)
            return w, h, nf
    return None


def _count_dqt(pay) -> int:
    n, p = 0, 0
    while p < len(pay):
        n += 1
        p += 1 + 64 * (2 if pay[p] >> 4 else 1)
    return n


def _jfif_text(pay) -> str:
    return "версия %d.%02d, плотность %dx%d, единицы %d" % (
        pay[5], pay[6], struct.unpack_from(">H", pay, 8)[0],
        struct.unpack_from(">H", pay, 10)[0], pay[7])


def _clean_jpeg(data, res, keep_icc):
    segs, end, err = _jpeg_walk(data)
    if err:
        res.err("JPEG: " + err)
        return None
    if not any(m == 0xDA for m, _, _ in segs):
        res.err("JPEG: нет данных изображения (SOS), чистить нечего")
        return None
    info = _jpeg_info(data, segs)
    out, dqt, img_bytes = [], 0, 0
    icc, c2pa = [0, 0], [0, 0]            # [частей, байт]
    exif_kept = jfif_done = in_scans = False
    for m, a, b in segs:
        raw = data[a:b]
        pay = raw[4:]
        if m in (0xD8, 0xD9):
            out.append(raw)
        elif m in _JPEG_IMG:
            out.append(raw)
            img_bytes += b - a
            in_scans = in_scans or m == 0xDA
            if m == 0xDB:
                dqt += _count_dqt(pay)
        elif m == 0xE0:
            if pay[:5] == b"JFIF\0" and len(pay) >= 14 and not jfif_done:
                jfif_done = True
                new = pay[:12] + b"\0\0"
                out.append(b"\xff\xe0" + struct.pack(">H", 16) + new)
                res.act(Act.KEPT, "APP0:JFIF", "JFIF-заголовок", _jfif_text(pay),
                        _jfif_text(new),
                        note="STRUCTURAL: плотность пикселей нужна для размера при печати, а "
                             "отсутствие и EXIF, и JFIF само заметно. Оставлен осознанно")
                if len(pay) > 14:
                    res.act(Act.REMOVED, "APP0:JFIF", "Вшитая миниатюра JFIF",
                            "%d байт" % (len(pay) - 14),
                            note="Миниатюра могла быть сделана до ретуши и кропа")
            else:
                res.act(Act.REMOVED, "APP0", "Прочий сегмент APP0 (%s)" % _ident(pay),
                        "%d байт" % len(pay), note="JFXX-миниатюра или чужой блок")
        elif m == 0xE1:
            if pay[:6] == b"Exif\0\0":
                scan = _exif_scan(pay[6:])
                # EXIF после первого SOS просмотрщики не читают: ориентацию оттуда не берём
                keep_o = (not exif_kept) and (not in_scans) and scan["orient"] in _ORIENT
                if keep_o:
                    out.append(_min_app1(scan["orient"], scan["order"]))
                    exif_kept = True
                _exif_acts(res, scan, "APP1:Exif", len(pay), keep_o)
            elif pay.startswith((b"http://ns.adobe.com/xap/1.0/\0",
                                 b"http://ns.adobe.com/xmp/extension/")):
                res.act(Act.REMOVED, "APP1:XMP", "XMP-пакет", "%d байт" % len(pay),
                        note="XMP дублирует EXIF и IPTC, несёт GUID документа (xmpMM) "
                             "и историю правок")
            else:
                res.act(Act.REMOVED, "APP1", "Неопознанный APP1 (%s)" % _ident(pay),
                        "%d байт" % len(pay))
        elif m == 0xE2:
            if pay[:12] == b"ICC_PROFILE\0":
                icc[0] += 1
                icc[1] += len(pay)
                if keep_icc:
                    out.append(raw)
            elif pay[:4] == b"MPF\0":
                res.act(Act.REMOVED, "APP2:MPF", "Multi-Picture Format", "%d байт" % len(pay),
                        note="Индекс нескольких кадров в одном файле; сами кадры лежат за EOI "
                             "и уходят вместе с хвостом")
            else:
                res.act(Act.REMOVED, "APP2", "Неопознанный APP2 (%s)" % _ident(pay),
                        "%d байт" % len(pay))
        elif m == 0xEB:
            low = bytes(pay[:4096])
            if any(t in low for t in (b"jumb", b"c2pa", b"jumd")):
                c2pa[0] += 1
                c2pa[1] += len(pay)
            else:
                res.act(Act.REMOVED, "APP11", "JUMBF-контейнер без меток C2PA",
                        "%d байт" % len(pay))
        elif m == 0xED:
            res.act(Act.REMOVED, "APP13:Photoshop", "Photoshop IRB / IPTC", "%d байт" % len(pay),
                    note="IPTC-IIM (подпись, ключевые слова, авторство, адрес), миниатюра "
                         "Photoshop и XMP-копия")
        elif m == 0xEE:
            transform = pay[11] if pay[:5] == b"Adobe" and len(pay) >= 12 else None
            need = bool(info) and transform is not None and (
                info[2] == 4 or (info[2] == 3 and transform == 0))
            if need:
                out.append(b"\xff\xee" + struct.pack(">H", 14) + pay[:12])
                res.act(Act.KEPT, "APP14:Adobe", "Маркер Adobe (transform=%d)" % transform,
                        "%d байт" % len(pay), "12 байт",
                        note="Это не метаданные, а параметр декодирования: без маркера декодер "
                             "угадывает модель по умолчанию (%s) и цвета исказятся. "
                             "Версия и флаги не несут идентичности"
                             % ("YCCK прочтётся как CMYK, инверсия Adobe потеряется"
                                if info[2] == 4 else "RGB в JPEG прочтётся как YCbCr"))
            else:
                res.act(Act.REMOVED, "APP14:Adobe", "Маркер Adobe", "%d байт" % len(pay),
                        note="Говорит, что файл прошёл через софт Adobe. Для YCbCr по умолчанию "
                             "цвета не изменятся")
        elif m == 0xFE:
            res.act(Act.REMOVED, "COM", "Комментарий JPEG",
                    bytes(pay).decode("utf-8", "replace").strip("\0"),
                    note="Кодировщики пишут сюда имя и версию, люди -- что угодно")
        elif 0xE3 <= m <= 0xEF:
            res.act(Act.REMOVED, "APP%d" % (m - 0xE0), "Нестандартный сегмент (%s)" % _ident(pay),
                    "%d байт" % len(pay), note="Приложение записало свой блок произвольного вида")
        else:
            res.act(Act.REMOVED, "0x%02X" % m, "Неизвестный маркер", "%d байт" % len(pay),
                    note="Резервный или приватный маркер: декодеру не нужен")
    if c2pa[0]:
        res.act(Act.REMOVED, "APP11:JUMBF", "Манифест C2PA / Content Credentials",
                "%d сегм., %d байт" % tuple(c2pa), note=C2PA_NOTE)
    if icc[0]:
        if keep_icc:
            res.act(Act.KEPT, "APP2:ICC_PROFILE", "ICC-профиль",
                    "%d част., %d байт" % tuple(icc), "без изменений",
                    note="STRUCTURAL: нужен для правильной цветопередачи. Имя профиля иногда "
                         "названо по монитору или принтеру -- оставлено осознанно; для полной "
                         "чистоты используйте PARANOID")
        else:
            res.act(Act.REMOVED, "APP2:ICC_PROFILE", "ICC-профиль",
                    "%d част., %d байт" % tuple(icc),
                    note="ВНИМАНИЕ: цвета могут измениться -- без профиля просмотрщик "
                         "считает картинку sRGB. Имя профиля называло устройство")
    if dqt:
        res.act(Act.UNREMOVABLE, "DQT", "Таблицы квантования", "%d шт" % dqt, "без изменений",
                note="Убрать нельзя без перекодирования с потерей качества. Таблицы "
                     "фингерпринтят энкодер, но замена их нашими дала бы отпечаток нашей "
                     "библиотеки -- хуже исходного")
    res.act(Act.KEPT, "JPEG:image", "Данные изображения",
            "%dx%d" % info[:2] if info else "SOF не найден",
            "%d байт, байт в байт" % img_bytes,
            note="SOF, DHT, DQT, DRI и энтропийные данные сканов скопированы без "
                 "перекодирования" + ("" if info else
                 ". В исходнике нет SOF: изображение не декодировалось и до чистки, "
                 "размеры не проверялись"))
    if end < len(data):
        res.act(Act.REMOVED, "JPEG:trailer", "Данные после EOI", "%d байт" % (len(data) - end),
                note="Классический тайник: декодеры хвост игнорируют. Могли быть кадры MPF, "
                     "видео Motion Photo, gain map Ultra HDR, архив или чужой документ")
    return b"".join(out)


# ===========================================================================
# PNG
# ===========================================================================

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# Данные изображения: без них файл не откроется. tRNS -- это альфа-канал.
_PNG_PIXELS = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"acTL", b"fcTL", b"fdAT"})
# Параметры отображения: фон, значимые биты, HDR-метки.
_PNG_DISPLAY = frozenset({b"bKGD", b"sBIT", b"hIST", b"cICP", b"mDCv", b"cLLi"})
# STRUCTURAL: сохраняем в обоих профилях (iCCP решается отдельно, по профилю).
_PNG_STRUCT = {b"gAMA": "Гамма", b"cHRM": "Хроматичность", b"sRGB": "Цветовое пространство sRGB",
               b"pHYs": "Плотность пикселей"}
_PNG_COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _png_chunk(ctype, body) -> bytes:
    """Собрать чанк, CRC считается заново."""
    return (struct.pack(">I", len(body)) + ctype + bytes(body)
            + struct.pack(">I", binascii.crc32(ctype + bytes(body)) & 0xFFFFFFFF))


def _png_walk(data):
    """Проход по чанкам со сверкой CRC. -> (chunks, end, err).

    chunks=[(тип, start, stop)], start..stop -- весь чанк (длина, тип, тело, CRC).
    """
    if data[:8] != _PNG_SIG:
        return [], 0, "нет сигнатуры PNG"
    mv = memoryview(data)
    n, pos, chunks = len(data), 8, []
    while True:
        if len(chunks) >= MAX_CHUNKS:
            return chunks, pos, "больше %d чанков" % MAX_CHUNKS
        if pos + 12 > n:
            return chunks, pos, "файл оборван: нет чанка IEND"
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = bytes(mv[pos + 4:pos + 8])
        if not all(65 <= c <= 90 or 97 <= c <= 122 for c in ctype):
            return chunks, pos, "некорректный тип чанка на смещении %d" % pos
        name = ctype.decode("ascii")
        stop = pos + 12 + length
        if stop > n:
            return chunks, pos, "чанк %s обрезан (нужно %d байт, есть %d)" % (name, length, n - pos - 12)
        crc = struct.unpack_from(">I", data, stop - 4)[0]
        if binascii.crc32(mv[pos + 4:stop - 4]) & 0xFFFFFFFF != crc:
            return chunks, pos, "неверная CRC чанка %s: файл повреждён или правился вручную" % name
        if not chunks and (ctype != b"IHDR" or length != 13):
            return chunks, pos, "первым чанком должен идти IHDR длиной 13"
        chunks.append((ctype, pos, stop))
        pos = stop
        if ctype == b"IEND":
            return chunks, pos, None


def _png_decodes(data, chunks):
    """Поток IDAT распаковывается целиком, данных не меньше, чем объявляет IHDR.

    Распаковка потоковая и с потолком MAX_RAW: бомба в IDAT не съест память.
    -> (ok, причина)
    """
    a = chunks[0][1]
    w, h, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", data[a + 8:a + 21])
    d, total, capped = zlib.decompressobj(), 0, False
    try:
        for t, x, y in chunks:
            if t != b"IDAT":
                continue
            buf = bytes(data[x + 8:y - 4])
            while buf and not d.eof:
                piece = d.decompress(buf, 1 << 22)
                total += len(piece)
                if total > MAX_RAW:
                    capped = True
                    break
                nxt = d.unconsumed_tail
                if not piece and len(nxt) == len(buf):
                    break
                buf = nxt
            if capped or d.eof:
                break
    except zlib.error as exc:
        return False, "поток IDAT не распаковывается (%s)" % exc
    if capped:
        return True, ""
    if not d.eof:
        return False, "поток IDAT оборван: изображение обрезано"
    ch = _PNG_COLOR_CHANNELS.get(color)
    if interlace == 0 and ch:
        need = h * (1 + (w * ch * depth + 7) // 8)
        if total < need:
            return False, "в IDAT %d байт, а IHDR требует %d: изображение обрезано" % (total, need)
    return True, ""


def _png_brief(t, body) -> str:
    try:
        if t == b"gAMA":
            return "гамма %.5f" % (struct.unpack(">I", body[:4])[0] / 100000)
        if t == b"sRGB":
            return "intent %d" % body[0]
        if t == b"pHYs":
            x, y, u = struct.unpack(">IIB", body[:9])
            return "%d x %d, единицы %d" % (x, y, u)
    except (struct.error, IndexError):
        pass
    return "%d байт" % len(body)


def _png_text_act(res, ctype, body) -> None:
    name = ctype.decode("ascii")
    kw, _, rest = bytes(body).partition(b"\0")
    key = clip(kw.decode("latin-1"), 80)
    if ctype == b"tEXt":
        val = rest.decode("latin-1")
    elif ctype == b"zTXt":
        val = "<сжато, %d байт; не распаковываем: защита от бомб>" % len(rest)
    elif rest[:1] == b"\0":                           # iTXt без сжатия
        tail = rest[2:].partition(b"\0")[2].partition(b"\0")[2]
        val = tail.decode("utf-8", "replace")
    else:
        val = "<сжато, %d байт>" % len(rest)
    xmp = ctype == b"iTXt" and "xmp" in key.lower()
    res.act(Act.REMOVED, "PNG:" + name,
            ("XMP-пакет (iTXt «%s»)" if xmp else "Текстовый чанк " + name + " «%s»") % key, val,
            note="XMP: GUID документа, авторство, история правок" if xmp else
                 "Пишут и люди, и генераторы: сюда же складывают промпты, параметры "
                 "моделей и путь к исходнику")


def _clean_png(data, res, keep_icc):
    chunks, end, err = _png_walk(data)
    if err:
        res.err("PNG: " + err)
        return None
    if not any(t == b"IDAT" for t, _, _ in chunks):
        res.err("PNG: нет данных изображения (IDAT), чистить нечего")
        return None
    out, pix, disp, exif_kept = [_PNG_SIG], Counter(), [], False
    for t, a, b in chunks:
        body = data[a + 8:b - 4]
        name = t.decode("ascii")
        if t in _PNG_PIXELS:
            out.append(_png_chunk(t, body))
            pix[name] += 1
        elif t in _PNG_DISPLAY:
            out.append(_png_chunk(t, body))
            disp.append(name)
        elif t in _PNG_STRUCT:
            out.append(_png_chunk(t, body))
            res.act(Act.KEPT, "PNG:" + name, _PNG_STRUCT[t], _png_brief(t, body),
                    "без изменений",
                    note="STRUCTURAL: влияет на цвет или размер при печати, сохраняется "
                         "в любом профиле")
        elif t == b"iCCP":
            pname = clip(bytes(body).split(b"\0", 1)[0].decode("latin-1"), 80)
            if keep_icc:
                out.append(_png_chunk(t, body))
                res.act(Act.KEPT, "PNG:iCCP", "ICC-профиль", "«%s», %d байт" % (pname, len(body)),
                        "без изменений",
                        note="STRUCTURAL: нужен для правильной цветопередачи. Имя профиля "
                             "иногда названо по монитору или сканеру -- оставлено осознанно; "
                             "для полной чистоты используйте PARANOID")
            else:
                res.act(Act.REMOVED, "PNG:iCCP", "ICC-профиль",
                        "«%s», %d байт" % (pname, len(body)),
                        note="ВНИМАНИЕ: цвета могут измениться -- без профиля просмотрщик "
                             "опирается на gAMA/sRGB или считает картинку sRGB")
        elif t == b"eXIf":
            scan = _exif_scan(body)
            keep_o = (not exif_kept) and scan["orient"] in _ORIENT
            if keep_o:
                out.append(_png_chunk(t, _min_tiff(scan["orient"], scan["order"])))
                exif_kept = True
            _exif_acts(res, scan, "PNG:eXIf", len(body), keep_o)
        elif t in (b"tEXt", b"zTXt", b"iTXt"):
            _png_text_act(res, t, body)
        elif t == b"tIME":
            try:
                when = "%04d-%02d-%02d %02d:%02d:%02d UTC" % struct.unpack(">HBBBBB", body[:7])
            except struct.error:
                when = "%d байт" % len(body)
            res.act(Act.REMOVED, "PNG:tIME", "Время последней правки", when,
                    note="Момент последнего изменения картинки")
        elif t == b"caBX":
            res.act(Act.REMOVED, "PNG:caBX", "Манифест C2PA / Content Credentials",
                    "%d байт" % len(body), note=C2PA_NOTE)
        elif t == b"dSIG":
            res.act(Act.REMOVED, "PNG:dSIG", "Цифровая подпись", "%d байт" % len(body),
                    note="Подпись покрывала содержимое файла: чистка её ломает, а сам факт "
                         "подписи виден получателю")
        elif t[:1].isupper():
            out.append(_png_chunk(t, body))
            res.act(Act.KEPT, "PNG:" + name, "Неизвестный критический чанк",
                    "%d байт" % len(body), "без изменений",
                    note="Критический чанк обязан понимать декодер: убрать его значит "
                         "испортить файл")
        else:
            res.act(Act.REMOVED, "PNG:" + name, "Неизвестный вспомогательный чанк",
                    "%d байт" % len(body),
                    note="Приватный чанк конкретного приложения, содержимое произвольное")
    res.act(Act.KEPT, "PNG:image", "Данные изображения",
            ", ".join(k + ("×%d" % v if v > 1 else "") for k, v in pix.items()),
            "байт в байт", note="Пиксельные данные, палитра и прозрачность скопированы без "
                                "перекодирования; CRC пересчитаны")
    if disp:
        res.act(Act.KEPT, "PNG:display", "Параметры отображения", ", ".join(disp),
                "без изменений", note="STRUCTURAL: фон, значимые биты и HDR-метки влияют на "
                                      "показ картинки")
    if end < len(data):
        res.act(Act.REMOVED, "PNG:trailer", "Данные после IEND", "%d байт" % (len(data) - end),
                note="Классический тайник: декодеры хвост игнорируют")
    return b"".join(out)


# ===========================================================================
# WebP (RIFF)
# ===========================================================================

_WEBP_IMG = frozenset({b"VP8 ", b"VP8L", b"ALPH", b"ANIM", b"ANMF"})
_VP8X_FLAGS = ((0x20, "ICC"), (0x10, "альфа"), (0x08, "EXIF"), (0x04, "XMP"), (0x02, "анимация"))


def _riff(fourcc, body) -> bytes:
    return fourcc + struct.pack("<I", len(body)) + bytes(body) + (b"\0" if len(body) & 1 else b"")


def _webp_walk(data):
    """RIFF-контейнер. -> (chunks, end, err); chunks=[(fourcc, start, stop)] -- тело чанка."""
    n = len(data)
    if n < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return [], 0, "нет заголовка RIFF/WEBP"
    total = struct.unpack_from("<I", data, 4)[0] + 8
    if total > n:
        return [], 0, "RIFF объявляет %d байт, в файле %d: файл обрезан" % (total, n)
    pos, chunks = 12, []
    while pos < total:
        if len(chunks) >= MAX_CHUNKS:
            return chunks, total, "больше %d чанков" % MAX_CHUNKS
        if pos + 8 > total:
            return chunks, total, "хвост RIFF короче заголовка чанка"
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + size > total:
            return chunks, total, "чанк выходит за пределы RIFF"
        chunks.append((bytes(data[pos:pos + 4]), pos + 8, pos + 8 + size))
        pos += 8 + size + (size & 1)
    return chunks, total, None


def _webp_dims(data, chunks):
    for f, a, b in chunks:
        body = data[a:b]
        if f == b"VP8X" and len(body) >= 10:
            return (1 + int.from_bytes(body[4:7], "little"), 1 + int.from_bytes(body[7:10], "little"))
    for f, a, b in chunks:
        body = data[a:b]
        if f == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
            return (struct.unpack_from("<H", body, 6)[0] & 0x3FFF,
                    struct.unpack_from("<H", body, 8)[0] & 0x3FFF)
        if f == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
            bits = struct.unpack_from("<I", body, 1)[0]
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _flag_names(flags) -> str:
    return ", ".join(n for bit, n in _VP8X_FLAGS if flags & bit) or "нет"


def _clean_webp(data, res, keep_icc):
    chunks, end, err = _webp_walk(data)
    if err:
        res.err("WebP: " + err)
        return None
    kinds = {f for f, _, _ in chunks}
    if not kinds & {b"VP8 ", b"VP8L", b"ANMF"}:
        res.err("WebP: нет данных изображения, чистить нечего")
        return None
    vp8x = next((c for c in chunks if c[0] == b"VP8X"), None)
    if vp8x and vp8x[2] - vp8x[1] < 10:
        res.err("WebP: чанк VP8X обрезан")
        return None
    # ориентация: первый EXIF с корректным тегом; пишем её только при наличии VP8X
    # (без VP8X EXIF-чанк в файле вообще недопустим)
    orient = scan = prefix = None
    for f, a, b in chunks:
        if f == b"EXIF":
            blob = data[a:b]
            pfx = blob[:6] == b"Exif\0\0"
            sc = _exif_scan(blob[6:] if pfx else blob)
            if sc["orient"] in _ORIENT:
                orient, scan, prefix = sc["orient"], sc, pfx
                break
    keep_o = bool(vp8x) and orient is not None
    icc_present = any(f == b"ICCP" for f, _, _ in chunks) and keep_icc and bool(vp8x)
    parts, pix, exif_done = [], Counter(), False
    for f, a, b in chunks:
        body = data[a:b]
        name = clip(f.decode("latin-1"), 8)
        if f == b"VP8X":
            flags = body[0]
            new = (flags & 0x13) | (0x20 if icc_present else 0) | (0x08 if keep_o else 0)
            parts.append(_riff(f, bytes([new]) + body[1:10]))
            if new != flags:
                res.act(Act.NORMALIZED, "WebP:VP8X", "Флаги контейнера",
                        "0x%02X: %s" % (flags, _flag_names(flags)),
                        "0x%02X: %s" % (new, _flag_names(new)),
                        note="Флаги приведены в соответствие с оставшимися чанками: иначе "
                             "остался бы признак грубой чистки (флаг есть, чанка нет)")
        elif f in _WEBP_IMG:
            parts.append(_riff(f, body))
            pix[name.strip()] += 1
        elif f == b"ICCP" and vp8x:
            if keep_icc:
                parts.append(_riff(f, body))
                res.act(Act.KEPT, "WebP:ICCP", "ICC-профиль", "%d байт" % len(body),
                        "без изменений",
                        note="STRUCTURAL: нужен для правильной цветопередачи. Имя профиля "
                             "иногда названо по устройству -- оставлено осознанно; для полной "
                             "чистоты используйте PARANOID")
            else:
                res.act(Act.REMOVED, "WebP:ICCP", "ICC-профиль", "%d байт" % len(body),
                        note="ВНИМАНИЕ: цвета могут измениться -- без профиля просмотрщик "
                             "считает картинку sRGB")
        elif f == b"EXIF":
            sc = _exif_scan(body[6:] if body[:6] == b"Exif\0\0" else body)
            first = keep_o and not exif_done and sc is not None and sc["orient"] == orient
            if first:
                exif_done = True
                parts.append(_riff(f, (b"Exif\0\0" if prefix else b"")
                                   + _min_tiff(orient, scan["order"])))
            _exif_acts(res, sc, "WebP:EXIF", len(body), first)
        elif f == b"XMP ":
            res.act(Act.REMOVED, "WebP:XMP", "XMP-пакет", "%d байт" % len(body),
                    note="XMP: GUID документа, авторство, история правок")
        else:
            res.act(Act.REMOVED, "WebP:" + name.strip(), "Неизвестный чанк", "%d байт" % len(body),
                    note="Чанк, который декодеру не нужен: приватные данные приложения")
    res.act(Act.KEPT, "WebP:image", "Данные изображения",
            ", ".join(k + ("×%d" % v if v > 1 else "") for k, v in pix.items()),
            "байт в байт", note="Сжатые данные кадров скопированы без перекодирования")
    body = b"WEBP" + b"".join(parts)
    if end < len(data):
        res.act(Act.REMOVED, "WebP:trailer", "Данные после RIFF", "%d байт" % (len(data) - end),
                note="Классический тайник: декодеры хвост игнорируют")
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ===========================================================================
# GIF
# ===========================================================================

_GIF_XMP_MAGIC = b"\x01\xff\xfe\xfd\xfc"
_GIF_LOOP = (b"NETSCAPE2.0", b"ANIMEXTS1.0")       # число повторов анимации: STRUCTURAL
_GIF_IMG = frozenset({"hdr", "gce", "img", "pt", "loop"})


def _gif_sub(data, q):
    """Конец цепочки sub-блоков (за нулевым терминатором) или None, если оборвана."""
    n = len(data)
    while q < n:
        size = data[q]
        q += 1 + size
        if size == 0:
            return q
    return None


def _gif_walk(data):
    """-> (items, end, err); items=[(вид, start, stop)].

    Виды: hdr, gce, img, pt, loop, icc, comment, xmp, app, ext, end.
    """
    n = len(data)
    if n < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return [], 0, "нет сигнатуры GIF"
    pos = 13 + ((3 << ((data[10] & 7) + 1)) if data[10] & 0x80 else 0)
    if pos > n:
        return [], 0, "обрезана глобальная палитра"
    items = [("hdr", 0, pos)]
    while True:
        if len(items) >= MAX_CHUNKS:
            return items, pos, "больше %d блоков" % MAX_CHUNKS
        if pos >= n:
            return items, pos, "файл оборван: нет завершающего байта 0x3B"
        t = data[pos]
        if t == 0x3B:
            items.append(("end", pos, pos + 1))
            return items, pos + 1, None
        if t == 0x2C:
            if pos + 10 > n:
                return items, pos, "дескриптор изображения обрезан"
            packed, q = data[pos + 9], pos + 10
            if packed & 0x80:
                q += 3 << ((packed & 7) + 1)
            q = _gif_sub(data, q + 1)                      # +1 -- LZW min code size
            if q is None:
                return items, pos, "данные изображения оборваны"
            items.append(("img", pos, q))
            pos = q
            continue
        if t != 0x21:
            return items, pos, "неизвестный блок 0x%02X на смещении %d" % (t, pos)
        if pos + 2 > n:
            return items, pos, "расширение обрезано"
        label, kind, q = data[pos + 1], "ext", None
        if label == 0xFF:
            if pos + 3 > n or pos + 3 + data[pos + 2] > n:
                return items, pos, "Application Extension обрезан"
            head = pos + 3 + data[pos + 2]
            appid = bytes(data[pos + 3:head])
            if appid[:8] == b"XMP Data":
                kind = "xmp"
                k = data.find(_GIF_XMP_MAGIC, head)
                q = k + 258 if k != -1 and k + 258 <= n else _gif_sub(data, head)
            else:
                kind = ("loop" if appid in _GIF_LOOP else "icc" if appid == b"ICCRGBG1012"
                        else "app")
                q = _gif_sub(data, head)
        else:
            kind = {0xF9: "gce", 0xFE: "comment", 0x01: "pt"}.get(label, "ext")
            q = _gif_sub(data, pos + 2)
        if q is None:
            return items, pos, "расширение 0x%02X оборвано" % label
        items.append((kind, pos, q))
        pos = q


def _gif_text(raw) -> str:
    out, q = bytearray(), 2
    while q < len(raw) and raw[q] and len(out) < 400:
        s = raw[q]
        out += raw[q + 1:q + 1 + s]
        q += 1 + s
    return bytes(out).decode("utf-8", "replace")


def _clean_gif(data, res, keep_icc):
    items, end, err = _gif_walk(data)
    if err:
        res.err("GIF: " + err)
        return None
    frames = sum(1 for k, _, _ in items if k == "img")
    if not frames:
        res.err("GIF: нет кадров, чистить нечего")
        return None
    out, loops = [], 0
    for kind, a, b in items:
        raw = data[a:b]
        if kind in _GIF_IMG or kind == "end":
            out.append(raw)
            loops += kind == "loop"
        elif kind == "icc":
            if keep_icc:
                out.append(raw)
                res.act(Act.KEPT, "GIF:ICC", "ICC-профиль", "%d байт" % len(raw), "без изменений",
                        note="STRUCTURAL: нужен для правильной цветопередачи")
            else:
                res.act(Act.REMOVED, "GIF:ICC", "ICC-профиль", "%d байт" % len(raw),
                        note="ВНИМАНИЕ: цвета могут измениться")
        elif kind == "comment":
            res.act(Act.REMOVED, "GIF:Comment", "Комментарий GIF", _gif_text(raw),
                    note="Сюда пишут имя конвертера, а иногда и автора")
        elif kind == "xmp":
            res.act(Act.REMOVED, "GIF:XMP", "XMP-пакет", "%d байт" % len(raw),
                    note="XMP: авторство, GUID документа, история правок")
        elif kind == "app":
            appid = bytes(raw[3:3 + raw[2]]) if len(raw) > 2 else b""
            res.act(Act.REMOVED, "GIF:Application", "Application Extension (%s)"
                    % clip(appid.decode("latin-1"), 24), "%d байт" % len(raw),
                    note="Блок конкретной программы: имя приложения само говорит, чем сделан файл")
        else:
            res.act(Act.REMOVED, "GIF:ext", "Неизвестное расширение 0x%02X" % raw[1],
                    "%d байт" % len(raw), note="Декодеру не нужно: приватные данные приложения")
    w, h = struct.unpack_from("<HH", data, 6)
    res.act(Act.KEPT, "GIF:image", "Данные изображения", "%dx%d, кадров %d" % (w, h, frames),
            "байт в байт",
            note="LZW-данные, палитры и управление кадрами скопированы без перекодирования"
                 + ("; расширение зацикливания анимации оставлено (STRUCTURAL): без него "
                    "анимация проиграется один раз" if loops else ""))
    if end < len(data):
        res.act(Act.REMOVED, "GIF:trailer", "Данные после trailer", "%d байт" % (len(data) - end),
                note="Классический тайник: декодеры хвост игнорируют")
    return b"".join(out)


# ===========================================================================
# проверка результата: не испорчен ли файл (независимо от того, кто его собрал)
# ===========================================================================

_WALK = {"jpeg": _jpeg_walk, "png": _png_walk, "webp": _webp_walk, "gif": _gif_walk}


def _stream(fmt, d, parts) -> bytes:
    """«Поток изображения»: всё, что нужно декодеру, в исходном порядке и байт в байт."""
    if fmt == "jpeg":
        return b"".join(d[x:y] for m, x, y in parts if m in _JPEG_IMG)
    if fmt == "png":
        return b"".join(d[x:y] for t, x, y in parts if t in _PNG_PIXELS)
    if fmt == "webp":
        return b"".join(_riff(f, d[x:y]) for f, x, y in parts if f in _WEBP_IMG)
    return b"".join(d[x:y] for k, x, y in parts if k in _GIF_IMG)


def _dims(fmt, d, parts):
    try:
        if fmt == "jpeg":
            info = _jpeg_info(d, parts)
            return info[:2] if info else None
        if fmt == "png":
            return struct.unpack(">II", d[parts[0][1] + 8:parts[0][1] + 16])
        if fmt == "webp":
            return _webp_dims(d, parts)
        return struct.unpack_from("<HH", d, 6)
    except (struct.error, IndexError):
        return None


def _orient(fmt, d, parts):
    """Первый EXIF Orientation в файле (или None)."""
    try:
        for t, a, b in parts:
            if fmt == "jpeg" and t == 0xDA:
                break                                  # после SOS EXIF не читают
            if fmt == "jpeg" and t == 0xE1 and d[a + 4:a + 10] == b"Exif\0\0":
                blob = d[a + 10:b]
            elif fmt == "png" and t == b"eXIf":
                blob = d[a + 8:b - 4]
            elif fmt == "webp" and t == b"EXIF":
                blob = d[a:b]
                blob = blob[6:] if blob[:6] == b"Exif\0\0" else blob
            else:
                continue
            o = _exif_scan(blob)["orient"]
            if o is not None:
                return o
    except Exception:                                  # noqa: BLE001 -- проверка не падает
        pass
    return None


def _dst_problems(fmt, d, parts, end) -> list:
    """Структурная валидность результата своим парсером."""
    errs = []
    if end != len(d):
        errs.append("после конца изображения остались данные (%d байт)" % (len(d) - end))
    if fmt == "jpeg" and not any(m == 0xDA for m, _, _ in parts):
        errs.append("нет SOS")
    if fmt == "png":
        if not any(t == b"IDAT" for t, _, _ in parts):
            errs.append("нет IDAT")
        else:
            ok, why = _png_decodes(d, parts)
            if not ok:
                errs.append(why)
    if fmt == "webp":
        kinds = {f for f, _, _ in parts}
        vp8x = next((d[a:b] for f, a, b in parts if f == b"VP8X"), None)
        if vp8x is not None and len(vp8x) >= 10:
            for bit, fourcc in ((0x08, b"EXIF"), (0x04, b"XMP "), (0x20, b"ICCP")):
                if bool(vp8x[0] & bit) != (fourcc in kinds):
                    errs.append("флаги VP8X не совпадают с чанками (%s)" % fourcc.decode().strip())
    return errs


def _verify(fmt, a, b) -> dict:
    v = {"fmt": fmt, "ok": False, "valid": False, "dims_src": None, "dims_dst": None,
         "dims_same": False, "image_same": False, "image_bytes": 0, "image_sha256": "",
         "scan_same": None, "scan_bytes": 0, "orient_src": None, "orient_dst": None,
         "orient_ok": False, "errors": []}
    walk = _WALK.get(fmt)
    if walk is None:
        v["errors"].append("формат %s проверка не поддерживает" % fmt)
        return v
    pa, ea, era = walk(a)
    pb, eb, erb = walk(b)
    if era:
        v["errors"].append("исходник: " + era)
    if erb:
        v["errors"].append("результат: " + erb)
    if era or erb:
        return v
    probs = _dst_problems(fmt, b, pb, eb)
    v["errors"] += ["результат: " + p for p in probs]
    v["valid"] = not probs
    v["dims_src"], v["dims_dst"] = _dims(fmt, a, pa), _dims(fmt, b, pb)
    v["dims_same"] = v["dims_src"] == v["dims_dst"]
    sa, sb = _stream(fmt, a, pa), _stream(fmt, b, pb)
    v["image_same"] = bool(sa) and sa == sb
    v["image_bytes"], v["image_sha256"] = len(sb), _sha(sb)
    if fmt == "jpeg":
        xa = next((x for m, x, y in pa if m == 0xDA), None)
        xb = next((x for m, x, y in pb if m == 0xDA), None)
        v["scan_same"] = xa is not None and xb is not None and a[xa:ea] == b[xb:eb]
        v["scan_bytes"] = eb - xb if xb is not None else 0
    v["orient_src"], v["orient_dst"] = _orient(fmt, a, pa), _orient(fmt, b, pb)
    norm = lambda o: o if o in _ORIENT else None            # noqa: E731
    v["orient_ok"] = norm(v["orient_src"]) == norm(v["orient_dst"])
    if not v["dims_same"]:
        v["errors"].append("размеры изменились: %s -> %s" % (v["dims_src"], v["dims_dst"]))
    if not v["image_same"]:
        v["errors"].append("сжатые данные изображения изменились: файл перекодирован или повреждён")
    if not v["orient_ok"]:
        v["errors"].append("ориентация потеряна: %s -> %s" % (v["orient_src"], v["orient_dst"]))
    v["ok"] = v["valid"] and v["dims_same"] and v["image_same"] and v["orient_ok"]
    return v


def verify_image(src, dst) -> dict:
    """Доказательство, что чистка не испортила файл. Читает оба файла, ничего не пишет.

    -> {ok, fmt, valid, dims_src, dims_dst, dims_same, image_same, image_bytes,
        image_sha256, scan_same (JPEG: блок SOS..EOI целиком бит в бит),
        scan_bytes, orient_src, orient_dst, orient_ok, errors}
    """
    v = {"ok": False, "errors": []}
    try:
        with open(src, "rb") as fh:
            a = fh.read(MAX_FILE + 1)
        with open(dst, "rb") as fh:
            b = fh.read(MAX_FILE + 1)
        fa, fb = sniff.sniff(src), sniff.sniff(dst)
        if fa != fb:
            v["errors"].append("формат изменился: %s -> %s" % (fa, fb))
            return v
        return _verify(fa, a, b)
    except Exception as exc:                           # noqa: BLE001
        v["errors"].append("проверка не выполнена: %s" % type(exc).__name__)
        return v


# ===========================================================================
# публичная точка входа
# ===========================================================================

_CLEANERS = {"jpeg": _clean_jpeg, "png": _clean_png, "webp": _clean_webp, "gif": _clean_gif}

_REFUSE = {
    "tiff": "TIFF пока не поддержан чисткой: метаданные лежат в самих IFD вместе со ссылками "
            "на данные, пересборка без специального кода рискует испортить файл",
    "heic": "HEIF/HEIC пока не поддержан чисткой: метаданные лежат в элементах iloc, "
            "пересборка без специального кода рискует испортить файл",
}
_UNREADABLE = {
    "cloud": "файл является облачным плейсхолдером: откройте его в проводнике или выберите "
             "«Всегда хранить на этом устройстве»",
    "missing": "файл не найден", "dir": "это каталог, а не файл", "denied": "нет прав на чтение",
}


def _run(res, state, src, dst, profile, keep) -> None:
    try:
        profile = Profile(profile)
    except ValueError:
        res.err("неизвестный профиль чистки: %s" % res.profile)
        return
    res.profile = profile.value
    names = [keep] if isinstance(keep, str) else (keep or [])
    keep = {str(k).lower() for k in names}
    # PARANOID и REGENERATE убирают всё; ICC остаётся только в STEALTH или по keep={"icc"}
    keep_icc = profile is Profile.STEALTH or "icc" in keep

    if _same_path(src, dst):
        res.err("выходной путь совпадает с входным: входной файл не перезаписывается никогда")
        return
    fmt = sniff.sniff(src)
    res.fmt = fmt
    if fmt == "unreadable":
        reason, code = sniff.unreadable_reason(src)
        res.err("%s (errno %s)" % (_UNREADABLE.get(reason, "файл не читается"), code))
        return
    if fmt in _REFUSE:
        res.err(_REFUSE[fmt])
        return
    if fmt not in _CLEANERS:
        res.err("не растровое изображение (формат %s): чистка картинок не применима" % fmt
                if fmt != "unknown" else "файл пустой или формат не опознан")
        return
    try:
        if os.path.getsize(src) > MAX_FILE:
            res.err("файл больше %d МБ: не берём в память" % (MAX_FILE // 1048576))
            return
        with open(src, "rb") as fh:
            data = fh.read(MAX_FILE + 1)
    except OSError as exc:
        res.err("файл не читается (errno %s)" % exc.errno)
        return
    sha_before = _sha(data)

    out = _CLEANERS[fmt](data, res, keep_icc)
    if out is None:
        return                                      # причина уже в res.errors

    try:
        with open(dst, "wb") as fh:
            fh.write(out)
        state["wrote"] = True
        with open(dst, "rb") as fh:
            back = fh.read(MAX_FILE + 1)
    except OSError as exc:
        res.err("не удалось записать выходной файл (errno %s)" % exc.errno)
        return
    if back != out:
        res.err("выходной файл на диске не совпал с собранным")
        return
    v = _verify(fmt, data, back)                     # проверяем то, что реально лежит на диске
    if not v["ok"]:
        for e in v["errors"] or ["проверка результата не пройдена"]:
            res.err(e)
        return
    try:
        if _file_sha(src) != sha_before:
            res.err("входной файл изменился во время работы: результат ненадёжен")
            return
    except OSError as exc:
        res.err("входной файл не перечитался (errno %s)" % exc.errno)
        return
    res.ok = True


def clean_image(src, dst, profile=Profile.STEALTH, keep=None) -> CleanResult:
    """Очистить картинку без перекодирования. Исключений наружу не бросает.

    src -- вход (не модифицируется никогда), dst -- куда писать результат.
    profile -- Profile.STEALTH (дефолт) или PARANOID; REGENERATE для картинок
    трактуется как PARANOID. Разница профилей -- ICC-профиль: STEALTH его
    сохраняет, PARANOID убирает с предупреждением.
    keep -- набор имён, которые надо сохранить; понимается {"icc"} (оставить
    ICC-профиль и в PARANOID), остальное игнорируется.

    ok=True означает: dst записан, структура валидна (проверено своим парсером),
    размеры те же, сжатые данные совпали байт в байт, ориентация сохранена.
    При ok=False dst не остаётся на диске, actions пуст, причина -- в errors.
    """
    res = CleanResult(src=str(src), dst=str(dst), fmt="unknown",
                      profile=str(getattr(profile, "value", profile)))
    state = {"wrote": False}
    try:
        _run(res, state, os.fspath(src), os.fspath(dst), profile, keep)
    except Exception as exc:                           # noqa: BLE001 -- битый файл не валит бота
        res.ok = False
        res.err("внутренняя ошибка чистки: %s: %s" % (type(exc).__name__, exc))
    if not res.ok:
        res.actions.clear()
        if state["wrote"]:
            try:
                os.remove(dst)
            except OSError:
                pass
    return res



# ===========================================================================
# самопроверка: PYTHONIOENCODING=utf-8 python -m core.clean_image
# ===========================================================================

def _selftest() -> int:
    """Собирает файлы из байтов, чистит, и доказывает результат ИНСПЕКТОРОМ-оракулом.

    По каждой фикстуре и профилю: critical/sensitive до и после, ориентация, размеры,
    энтропийный блок бит в бит, валидность выхода, sha256 входа до и после.
    Любой провал -- код возврата 1.
    """
    import random
    import shutil
    import tempfile

    from core.inspect import inspect_file

    rng = random.Random(20260101)
    fails = []

    def check(name, cond, detail=""):
        if not cond:
            fails.append(name)
        print("    [%s] %s%s" % ("ok" if cond else "ПРОВАЛ", name,
                                  (" -- %s" % (detail,)) if detail and not cond else ""))

    # ----- сборка фикстур ---------------------------------------------------

    def seg(m, payload):
        return b"\xff" + bytes([m]) + struct.pack(">H", len(payload) + 2) + payload

    def entropy(n=300):
        out = bytearray()
        for i in range(n):
            out.append(rng.randrange(0, 255))               # без 0xFF: маркеры ставим сами
            if i % 37 == 5:
                out += b"\xff\x00"                          # стаффинг
            if i % 120 == 119:
                out += bytes([0xFF, 0xD0 + (i // 120) % 8])  # RSTn
        return bytes(out)

    def tiff(order, ifds):
        """ifds: {имя: [(tag, тип, значение|имя_IFD)]}; count считается сам."""
        e = "<" if order == b"II" else ">"
        size = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1}
        names = list(ifds)

        def isize(ent):
            return 2 + 12 * len(ent) + 4 + sum(len(r) + len(r) % 2 for _, _, r in ent
                                                 if not isinstance(r, str) and len(r) > 4)
        offs, pos = {}, 8
        for n in names:
            offs[n] = pos
            pos += isize(ifds[n])
        out = order + struct.pack(e + "HI", 42, 8)
        for n in names:
            ent = sorted(ifds[n], key=lambda t: t[0])
            data_at = offs[n] + 2 + 12 * len(ent) + 4
            body, blob = struct.pack(e + "H", len(ent)), b""
            for tag, typ, raw in ent:
                if isinstance(raw, str):
                    cnt, val = 1, struct.pack(e + "I", offs[raw])
                else:
                    cnt = len(raw) // size[typ]
                    if len(raw) <= 4:
                        val = raw + b"\0" * (4 - len(raw))
                    else:
                        val = struct.pack(e + "I", data_at + len(blob))
                        blob += raw + b"\0" * (len(raw) % 2)
                body += struct.pack(e + "HHI", tag, typ, cnt) + val
            nxt = offs["IFD1"] if n == "IFD0" and "IFD1" in ifds else 0
            out += body + struct.pack(e + "I", nxt) + blob
        return out

    def exif_phone(orient, order=b"MM"):
        e = ">" if order == b"MM" else "<"
        asc = lambda s: s.encode("utf-8") + b"\0"            # noqa: E731
        sh = lambda v: struct.pack(e + "H", v)               # noqa: E731
        lg = lambda v: struct.pack(e + "I", v)               # noqa: E731
        rat = lambda *p: b"".join(struct.pack(e + "II", n, d) for n, d in p)   # noqa: E731
        return tiff(order, {
            "IFD0": [(0x010F, 2, asc("Apple")), (0x0110, 2, asc("iPhone 13 Pro")),
                     (0x0112, 3, sh(orient)), (0x0131, 2, asc("15.4.1")),
                     (0x0132, 2, asc("2024:05:17 12:30:05")),
                     (0x013B, 2, asc("Игорь Татаров")),
                     (0x8769, 4, "ExifIFD"), (0x8825, 4, "GPS")],
            "ExifIFD": [(0x9003, 2, asc("2024:05:17 12:30:05")),
                        (0x9286, 7, b"ASCII\0\0\0secret note"),
                        (0x927C, 7, b"Apple iOS\0\0\x01MM" + bytes(range(40))),
                        (0xA001, 3, sh(1)), (0xA431, 2, asc("F2LSERIAL123")),
                        (0xA435, 2, asc("LENS-SERIAL-9"))],
            "GPS": [(0x0001, 2, b"N\0"), (0x0002, 5, rat((55, 1), (45, 1), (448, 100))),
                    (0x0003, 2, b"E\0"), (0x0004, 5, rat((37, 1), (37, 1), (630, 100))),
                    (0x0006, 5, rat((150, 1)))],
            "IFD1": [(0x0103, 3, sh(6)), (0x0201, 4, lg(0)), (0x0202, 4, lg(1234))],
        })

    def icc(desc="Test Display P3 (Dell U2720Q)"):
        text = desc.encode() + b"\0"
        tag = b"desc" + b"\0" * 4 + struct.pack(">I", len(text)) + text
        table = struct.pack(">I", 1) + b"desc" + struct.pack(">II", 128 + 4 + 12, len(tag))
        head = bytearray(128)
        head[36:40] = b"acsp"
        prof = bytes(head) + table + tag
        return struct.pack(">I", len(prof)) + prof[4:]

    xmp_text = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
                b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" '
                b'xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" '
                b'xmp:CreatorTool="Adobe Photoshop 25.0" xmpMM:DocumentID="xmp.did:SELFTEST-1234">'
                b'<dc:creator><rdf:Seq><rdf:li>Igor Tatarov</rdf:li></rdf:Seq></dc:creator>'
                b'</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')

    def irb():
        iptc = b"".join(b"\x1c\x02" + bytes([ds]) + struct.pack(">H", len(v)) + v
                        for ds, v in ((80, b"Igor Tatarov"), (90, b"Moscow"), (120, b"Caption secret")))
        return (b"Photoshop 3.0\0" + b"8BIM" + struct.pack(">H", 0x0404) + b"\0\0"
                + struct.pack(">I", len(iptc)) + iptc + (b"\0" if len(iptc) % 2 else b""))

    def jfif(thumb=False):
        body = b"JFIF\0" + bytes([1, 2, 1]) + struct.pack(">HH", 72, 72)
        return seg(0xE0, body + (bytes([2, 2]) + bytes(12) if thumb else b"\0\0"))

    def icc_segs(profile):
        half = len(profile) // 2
        return [seg(0xE2, b"ICC_PROFILE\0" + bytes([i + 1, 2]) + part)
                for i, part in enumerate((profile[:half], profile[half:]))]

    def jpeg(app=(), nf=3, w=16, h=8, scans=1, between=(), tail=b""):
        comps = ([(1, 0x11, 0), (2, 0x11, 0), (3, 0x11, 0), (4, 0x11, 0)] if nf == 4
                 else [(1, 0x22, 0), (2, 0x11, 1), (3, 0x11, 1)][:nf])
        out = b"\xff\xd8" + b"".join(app)
        out += seg(0xDB, b"\x00" + bytes(range(1, 65)) + b"\x01" + bytes(range(2, 66)))
        out += seg(0xC0, struct.pack(">BHHB", 8, h, w, nf) + b"".join(bytes(c) for c in comps))
        out += seg(0xC4, b"\x00" + bytes([0, 1, 5, 1, 1, 1, 1, 1, 1] + [0] * 7) + bytes(range(12)))
        for i in range(scans):
            if i:
                out += b"".join(between) + seg(0xC4, b"\x10" + bytes([0, 1, 1] + [0] * 13) + b"\x01\x02")
            out += seg(0xDA, bytes([nf]) + b"".join(bytes([c[0], 0]) for c in comps)
                       + b"\x00\x3f\x00") + entropy()
        return out + b"\xff\xd9" + tail

    adobe = lambda t: seg(0xEE, b"Adobe" + struct.pack(">HHHB", 100, 0, 0, t))   # noqa: E731
    c2pa = lambda: seg(0xEB, b"JP\0\x01\0\0\0\x01" + struct.pack(">I", 60) + b"jumbc2pa" + bytes(60))  # noqa: E731
    exif_seg = lambda orient, order=b"MM": seg(0xE1, b"Exif\0\0" + exif_phone(orient, order))  # noqa: E731
    xmp_seg = seg(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + xmp_text)
    com_seg = seg(0xFE, b"Created with GIMP 2.10 by igor")
    PROFILE = icc()

    def pchunk(t, body):
        return (struct.pack(">I", len(body)) + t + body
                + struct.pack(">I", binascii.crc32(t + body) & 0xFFFFFFFF))

    def png(chunks=(), w=4, h=4, tail=b""):
        raw = b"".join(b"\0" + bytes((x * 17 + y * 31) & 255 for x in range(w * 3)) for y in range(h))
        z = zlib.compress(raw, 9)
        cut = len(z) // 2
        return (_PNG_SIG + pchunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + b"".join(chunks) + pchunk(b"IDAT", z[:cut]) + pchunk(b"IDAT", z[cut:])
                + pchunk(b"IEND", b"") + tail)

    png_full = png([
        pchunk(b"iCCP", b"Test Display\0\0" + zlib.compress(PROFILE)),
        pchunk(b"gAMA", struct.pack(">I", 45455)),
        pchunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
        pchunk(b"tEXt", b"Author\0Igor Tatarov"),
        pchunk(b"tEXt", b"Comment\0C:\\Users\\testuser\\shot.png"),
        pchunk(b"zTXt", b"Software\0\0" + zlib.compress(b"Adobe Photoshop 25.0")),
        pchunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + xmp_text),
        pchunk(b"tIME", struct.pack(">HBBBBB", 2024, 5, 17, 12, 30, 5)),
        pchunk(b"eXIf", exif_phone(8, b"II")),
        pchunk(b"caBX", b"jumb" + bytes(40)),
        pchunk(b"prVt", b"private app data"),
    ], tail=b"PK\x03\x04hidden archive")

    def riff(f, body):
        return f + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")

    def webp(parts, tail=b""):
        body = b"WEBP" + b"".join(parts)
        return b"RIFF" + struct.pack("<I", len(body)) + body + tail

    vp8 = riff(b"VP8 ", b"\x50\x02\x00\x9d\x01\x2a" + struct.pack("<HH", 32, 16)
               + bytes(rng.randrange(256) for _ in range(41)))
    vp8x = lambda fl: riff(b"VP8X", bytes([fl]) + b"\0\0\0" + (31).to_bytes(3, "little")  # noqa: E731
                           + (15).to_bytes(3, "little"))
    webp_full = webp([vp8x(0x2C), riff(b"ICCP", PROFILE), vp8,
                      riff(b"EXIF", exif_phone(3, b"II")), riff(b"XMP ", xmp_text)],
                     tail=b"secret tail")
    webp_prefixed = webp([vp8x(0x08), vp8, riff(b"EXIF", b"Exif\0\0" + exif_phone(6, b"MM"))])
    webp_flag_only = webp([vp8x(0x0C), vp8])                   # флаги есть, чанков нет

    def sub(b):
        return b"".join(bytes([len(b[i:i + 255])]) + b[i:i + 255] for i in range(0, len(b), 255)) + b"\0"

    gif_full = (b"GIF89a" + struct.pack("<HHBBB", 2, 2, 0x80, 0, 0) + b"\0\0\0\xff\xff\xff"
                + b"\x21\xff\x0bNETSCAPE2.0" + sub(b"\x01\0\0")
                + b"\x21\xfe" + sub(b"Made by ImageMagick 7.1 igor")
                + b"\x21\xff\x0bXMP DataXMP" + xmp_text + b"\x01" + bytes(range(255, -1, -1)) + b"\0"
                + b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
                + b"\x2c" + struct.pack("<HHHHB", 0, 0, 2, 2, 0) + b"\x02" + sub(b"\x44\x01")
                + b"\x3b" + b"PK\x03\x04tail")

    cases = {   # имя -> байты
        "jpeg_phone_orient6.jpg": jpeg([jfif(True), exif_seg(6), xmp_seg] + icc_segs(PROFILE)
                                       + [seg(0xED, irb()), com_seg], tail=b"PK\x03\x04secret archive"),
        "jpeg_phone_little_endian_orient8.jpg": jpeg([exif_seg(8, b"II")] + icc_segs(PROFILE)),
        "jpeg_orient1.jpg": jpeg([jfif(), exif_seg(1)]),
        "jpeg_c2pa.jpg": jpeg([jfif(), c2pa(), c2pa()]),
        "jpeg_adobe_cmyk.jpg": jpeg([adobe(2), com_seg], nf=4),
        "jpeg_adobe_rgb.jpg": jpeg([adobe(0)], nf=3),
        "jpeg_adobe_ycc.jpg": jpeg([adobe(1)], nf=3),
        "jpeg_scans_with_junk_between.jpg": jpeg([jfif()], scans=2, between=(com_seg, exif_seg(6))),
        "jpeg_already_clean.jpg": jpeg([jfif()]),
        "png_full_orient8.png": png_full,
        "png_no_meta.png": png(),
        "webp_full_orient3.webp": webp_full,
        "webp_prefixed_orient6.webp": webp_prefixed,
        "webp_flag_without_chunk.webp": webp_flag_only,
        "gif_full.gif": gif_full,
    }

    tmp = tempfile.mkdtemp(prefix="clean_image_")
    outdir = os.path.join(tmp, "out")
    os.makedirs(outdir)

    def put(name, blob):
        p = os.path.join(tmp, name)
        with open(p, "wb") as fh:
            fh.write(blob)
        return p

    try:
        # --- фикстуры проекта (tests/fixtures.py): как есть -----------------
        try:
            from tests import fixtures as F
            fx = {}
            for mk in (F.make_jpeg, F.make_png):
                p, _ = mk(tmp)
                fx[os.path.basename(p)] = p
            broken = F.make_broken(tmp)
        except Exception as exc:                          # noqa: BLE001
            print("!! фикстуры tests/fixtures.py недоступны: %s" % type(exc).__name__)
            fx, broken = {}, {}

        paths = {n: put(n, b) for n, b in cases.items()}
        paths.update({"fixtures.make_" + k: v for k, v in fx.items()})

        results = {}
        print("=" * 100)
        print("ЧИСТКА: critical/sensitive до -> после (инспектор-оракул), ориентация, размеры, "
              "энтропийный блок, валидность, sha256 входа")
        print("=" * 100)
        for name, src in paths.items():
            sha0 = _file_sha(src)
            before = inspect_file(src)
            for prof in (Profile.STEALTH, Profile.PARANOID):
                dst = os.path.join(outdir, "%s__%s" % (prof.value, os.path.basename(src)))
                res = clean_image(src, dst, prof)
                sha1 = _file_sha(src)
                after = inspect_file(dst) if res.ok else None
                v = verify_image(src, dst) if res.ok else {}
                results[(name, prof)] = (res, before, after, v, dst)
                print("%-38s %-8s ok=%-5s crit %2d -> %-2s sens %2d -> %-2s orient %s->%s dims %s=%s "
                      "image_same=%s scan_same=%s(%s Б) valid=%s sha_in=%s"
                      % (name, prof.value, res.ok, before.critical,
                         after.critical if after else "-", before.sensitive,
                         after.sensitive if after else "-", v.get("orient_src"), v.get("orient_dst"),
                         v.get("dims_src"), v.get("dims_dst"), v.get("image_same"),
                         v.get("scan_same"), v.get("scan_bytes"), v.get("valid"),
                         "совпал" if sha0 == sha1 else "ИЗМЕНИЛСЯ"))
                for e in res.errors:
                    print("      ! " + e)
                check("%s/%s: вход не тронут (sha256 до == после)" % (name, prof.value), sha0 == sha1)
                check("%s/%s: ok=True" % (name, prof.value), res.ok, res.errors)
                if res.ok:
                    check("%s/%s: critical после == 0" % (name, prof.value), after.critical == 0,
                          [(f.location, f.label) for f in after.findings
                           if f.risk.value in ("identity", "environment")])
                    check("%s/%s: размеры, сжатые данные и ориентация целы" % (name, prof.value),
                          v["ok"], v["errors"])
                    # идемпотентность: повторная чистка своего же результата ничего не меняет
                    again = os.path.join(outdir, "again_" + os.path.basename(dst))
                    r2 = clean_image(dst, again, prof)
                    check("%s/%s: повторная чистка идемпотентна" % (name, prof.value),
                          r2.ok and open(again, "rb").read() == open(dst, "rb").read(), r2.errors)

        # --- точечные утверждения ------------------------------------------
        print("-" * 100)
        print("ТОЧЕЧНЫЕ ПРОВЕРКИ")
        leak_labels = {"Artist", "BodySerialNumber", "LensSerialNumber", "MakerNote", "DateTime",
                       "DateTimeOriginal", "Make", "Model", "Software", "UserComment",
                       "Координаты (десятичные)", "Встроенная миниатюра", "Комментарий JPEG",
                       "Author", "Comment", "dc:creator", "xmp:CreatorTool", "Комментарий"}

        def read(p):
            with open(p, "rb") as fh:
                return fh.read()

        def jmarks(p):
            segs, _, _ = _jpeg_walk(read(p))
            return [m for m, _, _ in segs]

        def orient_in_report(rep):
            for f in rep.findings:
                if "Orientation=" in f.value:
                    return int(f.value.split("Orientation=")[1].split(";")[0])
            return None

        for prof in (Profile.STEALTH, Profile.PARANOID):
            tag = prof.value
            print("  --- профиль %s" % tag)
            res, before, after, v, dst = results[("jpeg_phone_orient6.jpg", prof)]
            data = read(dst)
            check("JPEG/6: ориентация 6 сохранена (по инспектору)", orient_in_report(after) == 6)
            check("JPEG/6: ориентация 6 сохранена (по verify)", v["orient_src"] == 6 == v["orient_dst"])
            check("JPEG/6: GPS, серийники, даты, MakerNote, Artist, миниатюра исчезли",
                  not ({f.label for f in after.findings} & leak_labels),
                  sorted({f.label for f in after.findings} & leak_labels))
            check("JPEG/6: минимальный APP1 ровно 36 байт (маркер + длина 34)",
                  any(m == 0xE1 and y - x == 36 for m, x, y in _jpeg_walk(data)[0]))
            check("JPEG/6: хвост после EOI и XMP/APP13/COM убраны",
                  not ({0xED, 0xFE} & set(jmarks(dst))) and _jpeg_walk(data)[1] == len(data)
                  and b"Igor" not in data and b"secret" not in data and b"Photoshop" not in data)
            check("JPEG/6: JFIF-миниатюра отрезана (APP0 = 18 байт)",
                  any(m == 0xE0 and y - x == 18 for m, x, y in _jpeg_walk(data)[0]))
            check("JPEG/6: блок SOS..EOI бит в бит", v["scan_same"] and v["image_same"])
            icc_kept = 0xE2 in jmarks(dst)
            check("JPEG/6: ICC %s" % ("сохранён (STEALTH)" if tag == "stealth" else "убран (PARANOID)"),
                  icc_kept == (tag == "stealth"))
            check("JPEG/6: DQT помечен UNREMOVABLE", res.count(Act.UNREMOVABLE) == 1)
            check("JPEG/6: Orientation и ICC/JFIF отражены как KEPT" if tag == "stealth"
                  else "JPEG/6: Orientation и JFIF отражены как KEPT, ICC -- REMOVED с предупреждением",
                  any(a.act == Act.KEPT and "Orientation" in a.label for a in res.actions)
                  and (any(a.act == Act.KEPT and "ICC" in a.label for a in res.actions) if tag == "stealth"
                       else any(a.act == Act.REMOVED and "ICC" in a.label and "цвета" in a.note
                                for a in res.actions)))

            res, before, after, v, dst = results[("jpeg_phone_little_endian_orient8.jpg", prof)]
            check("JPEG/8 (II): ориентация 8 сохранена", orient_in_report(after) == 8 and v["orient_dst"] == 8)

            res, before, after, v, dst = results[("jpeg_orient1.jpg", prof)]
            check("JPEG orient=1: EXIF не пишем вовсе", 0xE1 not in jmarks(dst))

            res, before, after, v, dst = results[("jpeg_c2pa.jpg", prof)]
            check("JPEG C2PA: APP11 убраны, предупреждение про детектируемость в note",
                  0xEB not in jmarks(dst) and any("ДЕТЕКТИРУЕМ" in a.note for a in res.actions))

            res, before, after, v, dst = results[("jpeg_adobe_cmyk.jpg", prof)]
            check("JPEG CMYK: APP14 сохранён (иначе цвета ломаются), COM убран",
                  0xEE in jmarks(dst) and 0xFE not in jmarks(dst)
                  and any(a.act == Act.KEPT and "Adobe" in a.label for a in res.actions))
            res, before, after, v, dst = results[("jpeg_adobe_rgb.jpg", prof)]
            check("JPEG RGB transform=0: APP14 сохранён", 0xEE in jmarks(dst))
            res, before, after, v, dst = results[("jpeg_adobe_ycc.jpg", prof)]
            check("JPEG YCbCr transform=1: APP14 убран", 0xEE not in jmarks(dst))

            res, before, after, v, dst = results[("jpeg_scans_with_junk_between.jpg", prof)]
            check("JPEG мусор МЕЖДУ сканами убран, а сжатые данные целы",
                  v["image_same"] and v["scan_same"] is False
                  and not ({0xFE, 0xE1} & set(jmarks(dst))), (v["image_same"], v["scan_same"]))

            res, before, after, v, dst = results[("jpeg_already_clean.jpg", prof)]
            check("JPEG без метаданных: результат побайтно равен входу",
                  read(dst) == read(paths["jpeg_already_clean.jpg"]))

            res, before, after, v, dst = results[("png_full_orient8.png", prof)]
            names = [t.decode() for t, _, _ in _png_walk(read(dst))[0]]
            check("PNG: текст/XMP/tIME/eXIf-полный/caBX/приватный чанк/хвост убраны",
                  not ({"tEXt", "zTXt", "iTXt", "tIME", "caBX", "prVt"} & set(names))
                  and _png_walk(read(dst))[1] == len(read(dst)) and b"Igor" not in read(dst), names)
            check("PNG: ориентация 8 сохранена, GPS/Artist из eXIf ушли",
                  orient_in_report(after) == 8 and "eXIf" in names
                  and not ({f.label for f in after.findings} & leak_labels))
            check("PNG: gAMA и pHYs (STRUCTURAL) сохранены, оба IDAT на месте",
                  {"gAMA", "pHYs"} <= set(names) and names.count("IDAT") == 2, names)
            check("PNG: iCCP %s" % ("сохранён" if tag == "stealth" else "убран"),
                  ("iCCP" in names) == (tag == "stealth"), names)
            res, before, after, v, dst = results[("png_no_meta.png", prof)]
            check("PNG без метаданных: результат побайтно равен входу",
                  read(dst) == read(paths["png_no_meta.png"]))

            res, before, after, v, dst = results[("webp_full_orient3.webp", prof)]
            d = read(dst)
            ch, _, _ = _webp_walk(d)
            flags = d[ch[0][1]]
            check("WebP: ориентация 3 сохранена, EXIF/XMP/хвост убраны",
                  v["orient_dst"] == 3 and orient_in_report(after) == 3 and b"Igor" not in d
                  and b"secret" not in d and not any(f == b"XMP " for f, _, _ in ch))
            check("WebP: флаги VP8X = %s, signal inconsistent нет" % ("0x28" if tag == "stealth" else "0x08"),
                  flags == (0x28 if tag == "stealth" else 0x08)
                  and not any(s.kind == "inconsistent" for s in after.signals), hex(flags))
            res, before, after, v, dst = results[("webp_prefixed_orient6.webp", prof)]
            check("WebP c префиксом Exif: ориентация 6 сохранена, префикс повторён",
                  v["orient_dst"] == 6 and b"Exif\0\0" in read(dst))
            res, before, after, v, dst = results[("webp_flag_without_chunk.webp", prof)]
            check("WebP флаг без чанка: флаги приведены в порядок",
                  read(dst)[20] == 0x00 and any(a.act == Act.NORMALIZED for a in res.actions))

            res, before, after, v, dst = results[("gif_full.gif", prof)]
            d = read(dst)
            check("GIF: комментарий, XMP, хвост убраны; NETSCAPE2.0 (зацикливание) оставлен",
                  b"ImageMagick" not in d and b"xmpmeta" not in d and b"PK\x03\x04" not in d
                  and b"NETSCAPE2.0" in d and d.endswith(b"\x3b"))

        for key in [k for k in results if k[0].startswith("fixtures.")]:
            res, before, after, v, dst = results[key]
            check("%s/%s: ok=True" % (key[0], key[1].value), res.ok, res.errors)

        # --- патологии: везде ok=False, errors, без исключений, dst не остаётся ---
        print("-" * 100)
        print("ПАТОЛОГИИ (ожидаем ok=False, errors, без исключений, без файла на диске)")
        good_jpeg = cases["jpeg_phone_orient6.jpg"]
        bad = {
            "пустой файл": b"",
            "обрезан на середине APP1": good_jpeg[:60],
            "JPEG без EOI": good_jpeg[:good_jpeg.index(b"\xff\xd9") - 5],
            "JPEG мусор вместо маркера": b"\xff\xd8\xff" + bytes([0xE0]) + struct.pack(">H", 4)
                                          + b"xx" + b"\x00\x11\x22\x33",
            "JPEG без SOS": b"\xff\xd8" + jfif() + b"\xff\xd9",
            "PNG битый CRC": png_full.replace(b"Author\0Igor", b"Author\0Igoz"),
            "PNG обрезан в IDAT": png_full[:png_full.index(b"IDAT") + 10],
            "PNG без IEND": png([])[:-12],
            "PNG без IDAT": _PNG_SIG + pchunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                            + pchunk(b"IEND", b""),
            "PNG: IDAT короче, чем объявляет IHDR": _PNG_SIG
            + pchunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + pchunk(b"IDAT", zlib.compress(b"\0" * 10)) + pchunk(b"IEND", b""),
            "WebP обрезан": webp_full[:40],
            "GIF обрезан": gif_full[:60],
            "TIFF (честный отказ)": tiff(b"II", {"IFD0": [(0x013B, 2, b"Igor\0")]}),
            "HEIC (честный отказ)": struct.pack(">I", 24) + b"ftypheic" + bytes(4) + b"heicmif1" + bytes(40),
            "не картинка (текст)": b"hello world, this is plain text\n" * 5,
        }
        for label, path in broken.items():
            bad["фикстура проекта: " + label] = read(path) if label in ("jpeg_truncated", "png_ztxt_bomb") else None
        for label, blob in bad.items():
            if blob is None:
                continue
            src = put("bad.bin", blob)
            dst = os.path.join(outdir, "bad_out.bin")
            if os.path.exists(dst):
                os.remove(dst)
            sha0 = _file_sha(src)
            try:
                res = clean_image(src, dst)
                raised = False
            except Exception:                              # noqa: BLE001
                res, raised = None, True
            ok = (not raised) and (not res.ok) and bool(res.errors) and not os.path.exists(dst) \
                and not res.actions and _file_sha(src) == sha0
            print("  %-42s %s" % (label, ("ok=False: " + res.errors[0][:70]) if res else "ИСКЛЮЧЕНИЕ"))
            check("патология «%s»: отказ без исключения и без файла" % label, ok,
                  res and (res.ok, res.errors))
        # dst == src: вход не перезаписываем
        src = put("same.jpg", good_jpeg)
        sha0 = _file_sha(src)
        res = clean_image(src, src)
        check("dst == src: отказ, вход цел", (not res.ok) and _file_sha(src) == sha0, res.errors)
        res = clean_image(os.path.join(tmp, "нет_такого_файла.jpg"), os.path.join(outdir, "x.jpg"))
        check("несуществующий вход: отказ без исключения", (not res.ok) and bool(res.errors))
        res = clean_image(src, os.path.join(outdir, "x.jpg"), profile="no-such-profile")
        check("неизвестный профиль: отказ без исключения", (not res.ok) and bool(res.errors))
        res = clean_image(src, os.path.join(outdir, "kept_icc.jpg"), Profile.PARANOID, keep={"icc"})
        check("keep={'icc'} оставляет ICC и в PARANOID", res.ok and 0xE2 in jmarks(os.path.join(outdir, "kept_icc.jpg")))
        res = clean_image(src, os.path.join(outdir, "regen.jpg"), Profile.REGENERATE)
        check("REGENERATE трактуется как PARANOID", res.ok and 0xE2 not in jmarks(os.path.join(outdir, "regen.jpg")))
        check("в сообщениях об ошибках нет путей и имён файлов",
              all("Users" not in e and tmp not in e for e in
                  clean_image(os.path.join(tmp, "nope.jpg"), os.path.join(outdir, "y.jpg")).errors))

        # недоверенные строки из файла (метки сегментов, комментарии, ключи tEXt, fourcc, appid)
        # не должны уносить в отчёт управляющие символы
        hostile = {
            "hostile.jpg": jpeg([seg(0xE5, b"Ev\nil\x07tag\0data"), seg(0xFE, b"line1\nline2\x1b[31m")]),
            "hostile.png": png([pchunk(b"tEXt", b"Ke\ny\x07\0va\x1blue"),
                                pchunk(b"iCCP", b"na\nme\x1b\0\0" + zlib.compress(b"x"))]),
            "hostile.webp": webp([vp8x(0x00), riff(b"AB\n\x07", b"data"), vp8]),
            "hostile.gif": (b"GIF89a" + struct.pack("<HHBBB", 1, 1, 0x00, 0, 0)
                            + b"\x21\xff\x0bEv\nil\x07app\x1b1" + sub(b"x")
                            + b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, 0) + b"\x02" + sub(b"\x44\x01")
                            + b"\x3b"),
        }
        for hname, hblob in hostile.items():
            hres = clean_image(put(hname, hblob), os.path.join(outdir, "out_" + hname))
            dirty = [a for a in hres.actions
                     if any(c in (a.location + a.label + a.before + a.after) for c in "\n\r\x07\x1b")]
            check("%s: ok и в actions нет управляющих символов" % hname, hres.ok and not dirty,
                  (hres.errors, [(a.location, a.label) for a in dirty]))
        # кириллица и пробелы в путях
        ru_dir = os.path.join(tmp, "папка с пробелом")
        os.makedirs(ru_dir)
        ru_src = os.path.join(ru_dir, "фото Игорь.jpg")
        with open(ru_src, "wb") as fh:
            fh.write(good_jpeg)
        res = clean_image(ru_src, os.path.join(ru_dir, "результат.jpg"))
        check("кириллица и пробелы в путях", res.ok, res.errors)
        # выходной каталог не существует: отказ без исключения, вход цел
        sha0 = _file_sha(ru_src)
        res = clean_image(ru_src, os.path.join(tmp, "нет такого каталога", "x.jpg"))
        check("dst в несуществующем каталоге: отказ без исключения", (not res.ok) and bool(res.errors)
              and _file_sha(ru_src) == sha0, res.errors)
        # откат: если проверка результата не прошла, dst не остаётся на диске
        real_verify = globals()["_verify"]
        globals()["_verify"] = lambda fmt, a, b: {"ok": False, "errors": ["подмена для теста отката"]}
        try:
            rb_dst = os.path.join(outdir, "rollback.jpg")
            res = clean_image(ru_src, rb_dst)
        finally:
            globals()["_verify"] = real_verify
        check("провал проверки результата: dst удалён, actions пуст, вход цел",
              (not res.ok) and not os.path.exists(rb_dst) and not res.actions
              and _file_sha(ru_src) == sha0, res.errors)

        # --- необязательный независимый декодер: Pillow, если он вдруг есть ------
        print("-" * 100)
        try:
            _pillow_check(tmp, outdir, check)
        except Exception as exc:                           # noqa: BLE001
            print("  Pillow-проверка прервана: %s: %s" % (type(exc).__name__, exc))
            check("Pillow-проверка не должна падать", False, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 100)
    print("ИТОГО: %s" % ("все проверки пройдены" if not fails else "ПРОВАЛЕНО: %d" % len(fails)))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


def _pillow_check(tmp, outdir, check) -> None:
    """Независимый декодер (если установлен): пиксели ДО и ПОСЛЕ чистки совпадают бит в бит,
    ориентация читается. В продукте Pillow не нужен -- это только внешний свидетель."""
    try:
        from PIL import Image
    except ImportError:
        print("  Pillow не найден: независимая декодировка пропущена")
        return
    print("НЕЗАВИСИМЫЙ ДЕКОДЕР (Pillow %s): реальные картинки, пиксели до == после" % Image.__version__)
    import io
    w, h = 64, 48
    base = Image.frombytes("RGB", (w, h), bytes((x * 4 + y, y * 5, 255 - x * 3) [c] & 255
                                                 for y in range(h) for x in range(w) for c in range(3)))
    profile = b""
    try:
        from PIL import ImageCms
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:                                    # noqa: BLE001
        pass
    exif = Image.Exif()
    exif[0x010F], exif[0x0110], exif[0x0131] = "Canon", "EOS R5", "Photoshop 25"
    exif[0x013B], exif[0x0112], exif[0x0132] = "Igor Tatarov", 6, "2024:05:17 12:30:05"
    exif.get_ifd(0x8769)[0xA431] = "SERIAL-123"
    exif.get_ifd(0x8825).update({1: "N", 2: (55.0, 45.0, 4.48), 3: "E", 4: (37.0, 37.0, 6.3)})
    made = {}
    kw = {"exif": exif}
    if profile:
        kw["icc_profile"] = profile
    base.save(os.path.join(tmp, "pil_base.jpg"), "JPEG", quality=85, comment=b"secret comment", **kw)
    made["pil.jpg"] = "pil_base.jpg"
    base.save(os.path.join(tmp, "pil_prog.jpg"), "JPEG", quality=70, progressive=True, optimize=True,
              comment=b"secret", **kw)
    made["pil_progressive.jpg"] = "pil_prog.jpg"
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Author", "Igor")
    info.add_itxt("Description", "секрет")
    base.save(os.path.join(tmp, "pil.png"), "PNG", pnginfo=info, exif=exif, **({"icc_profile": profile} if profile else {}))
    made["pil.png"] = "pil.png"
    rgba = base.convert("RGBA")
    rgba.putalpha(Image.linear_gradient("L").resize((w, h)))
    rgba.save(os.path.join(tmp, "pil_rgba.png"), "PNG", pnginfo=info)
    made["pil_rgba.png"] = "pil_rgba.png"
    try:
        base.save(os.path.join(tmp, "pil.webp"), "WEBP", quality=80, exif=exif.tobytes(),
                  xmp=b"<x:xmpmeta xmlns:x='adobe:ns:meta/'/>", **({"icc_profile": profile} if profile else {}))
        made["pil.webp"] = "pil.webp"
    except Exception as exc:                             # noqa: BLE001
        print("  WebP не собрался Pillow (%s): пропущено" % type(exc).__name__)
    frames = [base.convert("P").point(lambda v, k=k: (v + k * 20) % 256) for k in range(3)]
    frames[0].save(os.path.join(tmp, "pil.gif"), "GIF", save_all=True, append_images=frames[1:],
                   duration=80, loop=0, comment=b"secret gif comment")
    made["pil.gif"] = "pil.gif"

    def frames_of(path):
        out = []
        with Image.open(path) as im:
            for i in range(getattr(im, "n_frames", 1)):
                im.seek(i)
                out.append((im.mode, im.size, im.convert("RGBA").tobytes()))
            return out, im.info.get("loop"), dict(im.getexif()).get(0x0112)

    for name, fname in made.items():
        src = os.path.join(tmp, fname)
        for prof in (Profile.STEALTH, Profile.PARANOID):
            dst = os.path.join(outdir, "pil_%s_%s" % (prof.value, name))
            res = clean_image(src, dst, prof)
            if not res.ok:
                check("Pillow/%s/%s: чистка ok" % (name, prof.value), False, res.errors)
                continue
            fa, loop_a, orient_a = frames_of(src)
            fb, loop_b, orient_b = frames_of(dst)
            same = fa == fb
            print("  %-22s %-8s пиксели совпали=%s кадров=%d ориентация %s->%s loop %s->%s размер %d->%d Б"
                  % (name, prof.value, same, len(fb), orient_a, orient_b, loop_a, loop_b,
                     os.path.getsize(src), os.path.getsize(dst)))
            check("Pillow/%s/%s: пиксели ДО и ПОСЛЕ идентичны" % (name, prof.value), same)
            check("Pillow/%s/%s: ориентация и зацикливание сохранены" % (name, prof.value),
                  orient_a == orient_b and loop_a == loop_b, (orient_a, orient_b, loop_a, loop_b))
            with open(dst, "rb") as fh:
                blob = fh.read()
            check("Pillow/%s/%s: секреты из файла исчезли" % (name, prof.value),
                  not any(s in blob for s in (b"Igor", b"SERIAL-123", b"secret", b"Canon", b"EOS R5",
                                              b"Photoshop 25", b"2024:05:17")))


if __name__ == "__main__":
    raise SystemExit(_selftest())
