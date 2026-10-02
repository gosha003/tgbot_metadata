# -*- coding: utf-8 -*-
"""Тесты чистки (фазы 1-2): PDF, картинки, OOXML, диспетчер core.clean,
рендер core.cleanreport.

Главная мысль та же, что в core/cleanmodel.py: инспектор -- ОРАКУЛ. Результат
чистки проверяется не по списку действий чистильщика, а повторным
inspect_file() по выходному файлу, плюс независимым сырым поиском значений в
байтах результата (в том числе в распакованных потоках PDF и чанках PNG).

Главный инвариант, проверяется на КАЖДОМ запуске чистки через _clean():
sha256 ВХОДНОГО файла до и после совпадает.

Что здесь НЕ проверяется и почему: чистки OLE2, ODF и RTF не существует
(фаза 4), поэтому для них проверяется только честный ОТКАЗ -- файл не
создан, номер фазы назван, отчёт не обещает чистоты. Подгонять тест под
текущее поведение запрещено (AGENTS.md, п. 6): упавшая проверка здесь -- это
найденный дефект модуля, а не повод ослабить проверку.

Запуск (PYTHONIOENCODING=utf-8 обязателен на Windows):

    PYTHONIOENCODING=utf-8 python -m tests.test_clean

Без pytest, но совместимо с ним: функции test_*, каждая сама заводит и убирает
временный каталог. Порядок -- по имени, поэтому test_99_render идёт ПОСЛЕДНИМ
и разбирает все результаты чистки, накопленные предыдущими тестами.
Код возврата 1 при любом провале.
"""

from __future__ import annotations

import binascii
import hashlib
import io
import os
import re
import shutil
import struct
import sys
import tempfile
import time
import zipfile
import zlib
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pikepdf

from core import clean as clean_mod
from core import clean_ooxml, cleanreport, insp_ooxml, sniff
from core.clean import DispatchResult, clean_file, survived_values
from core.clean_image import clean_image, verify_image
from core.clean_pdf import clean_pdf
from core.cleanmodel import Act, CleanResult, Profile
from core.inspect import inspect_file
from core.model import Risk

from tests import fixtures

# ---------------------------------------------------------------------------
# учёт результатов
# ---------------------------------------------------------------------------

_RESULTS = []   # (имя проверки, прошла?, пояснение)
_SEEN = []      # (метка, результат чистки): сюда стекаются все результаты для test_98/test_99


def _check(name, condition, detail=""):
    _RESULTS.append((name, bool(condition), detail))
    line = "%s %s" % ("OK  " if condition else "FAIL", name)
    if not condition and detail != "":
        line += "  -- %s" % (str(detail)[:400],)
    print(line)
    return bool(condition)


def _with_tmp(fn):
    """Каждый test_* сам заводит и убирает свой временный каталог."""

    def wrapper():
        tmp = tempfile.mkdtemp(prefix="tgbot_clean_test_")
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# мелкие помощники
# ---------------------------------------------------------------------------

STEALTH, PARANOID = Profile.STEALTH, Profile.PARANOID
PROFILES = (STEALTH, PARANOID)
PNG_SIG = b"\x89PNG\r\n\x1a\n"
_LEAKY = (Risk.IDENTITY, Risk.ENVIRONMENT)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _sha256(path):
    return hashlib.sha256(_read(path)).hexdigest()


def _u16(text):
    """Длина в UTF-16 code units: так считает лимит Telegram, а не len()."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _norm(value):
    return " ".join(str(value).casefold().split()).rstrip("…")


def _pname(profile):
    return getattr(profile, "value", str(profile))


def _rnd(n, seed):
    """Детерминированный «мусор»: воспроизводимый и без os.urandom."""
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(b"%s:%d" % (seed.encode(), i)).digest()
        i += 1
    return out[:n]


def _clean(tag, tmp, src, profile=STEALTH, **kw):
    """clean_file во вложенный out/ + главный инвариант: sha256 входа до и после.

    -> (res, dst). res None, если clean_file бросил исключение (это провал).
    Результат попадает в _SEEN для финальных тестов рендера."""
    label = "%s/%s" % (tag, _pname(profile))
    base = re.sub(r"[^\w.-]+", "_", tag)
    dst = os.path.join(tmp, "out", "%s_%s%s" % (base, _pname(profile), os.path.splitext(src)[1]))
    before = _sha256(src)
    try:
        res = clean_file(src, dst, profile, **kw)
    except Exception as exc:  # noqa: BLE001 -- именно это и проверяем
        _check("%s: clean_file не бросил исключение" % label, False,
               "%s: %s" % (type(exc).__name__, exc))
        res = None
    _check("%s: ИНВАРИАНТ -- sha256 входа до == после чистки" % label, _sha256(src) == before)
    if res is not None:
        _SEEN.append((label, res))
    return res, dst


def _own_survivors(src_rep, dst_rep):
    """Собственный подсчёт, независимый от core.clean.survived_values: какие
    исходные IDENTITY/ENVIRONMENT-значения длиннее 5 символов встречаются в
    значениях находок результата (подстрока, без регистра и лишних пробелов)."""
    dst_vals = [_norm(g.value) for g in dst_rep.findings if g.value]
    out = []
    for f in src_rep.findings:
        v = _norm(f.value)
        if f.risk in _LEAKY and len(v) > 5 and any(v in d for d in dst_vals):
            out.append((f.label, f.value))
    return out


def _oracle(label, src, dst):
    """Повторная инспекция результата СВОИМИ руками (а не через res.verified).
    -> (отчёт src, отчёт dst)."""
    a, b = inspect_file(src), inspect_file(dst)
    universe = [f for f in a.findings if f.risk in _LEAKY and len(_norm(f.value)) > 5]
    _check("%s: оракул не вакуумен -- в исходнике есть IDENTITY/ENVIRONMENT длиннее 5 символов (%d)"
           % (label, len(universe)), len(universe) > 0)
    surv = survived_values(a, b)
    _check("%s: ОРАКУЛ -- ни одно исходное значение не встречается в находках результата"
           % label, not surv, [(s.label, s.found_at) for s in surv][:5])
    own = _own_survivors(a, b)
    _check("%s: ОРАКУЛ -- то же по независимому подсчёту" % label, not own, own[:5])
    return a, b


# --- сырой поиск значений в байтах результата ---------------------------------


def _needles(text):
    """Как значение может лежать в файле: UTF-8/16, cp1251, latin-1 и hex-строка
    PDF (UTF-16BE с BOM и без)."""
    be = text.encode("utf-16-be")
    out = {text.encode("utf-8"), text.encode("utf-16-le"), be,
           be.hex().encode(), (b"\xfe\xff" + be).hex().encode()}
    for enc in ("cp1251", "latin-1"):
        try:
            out.add(text.encode(enc))
        except UnicodeEncodeError:
            pass
    return [n for n in out if len(n) >= 4]


def _inflate(raw, limit=1 << 22):
    return zlib.decompressobj().decompress(raw, limit)


def _png_chunks(data):
    """Независимый разбор PNG: ([(тип, тело, crc_верна)], смещение_конца).
    None -- нет сигнатуры. Оборванный чанк помечается типом b'?'."""
    if data[:8] != PNG_SIG:
        return None
    pos, out = 8, []
    while pos + 12 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        t, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
        crc = data[pos + 8 + n:pos + 12 + n]
        if len(body) != n or len(crc) != 4:
            out.append((b"?", b"", False))
            break
        ok = struct.unpack(">I", crc)[0] == (binascii.crc32(t + body) & 0xFFFFFFFF)
        out.append((t, body, ok))
        pos += 12 + n
    return out, pos


def _chunks(data):
    return (_png_chunks(data) or ([], 0))[0]


def _png_inflated(data):
    """Распакованное содержимое сжатых чанков PNG: в zTXt/iTXt/iCCP значения не
    видны в сырых байтах."""
    out = []
    for t, body, _ok in _chunks(data):
        try:
            if t in (b"zTXt", b"iCCP"):
                out.append(_inflate(body.partition(b"\0")[2][1:]))
            elif t == b"iTXt":
                rest = body.partition(b"\0")[2]
                flag, rest = rest[0], rest[2:]
                text = rest.partition(b"\0")[2].partition(b"\0")[2]
                out.append(_inflate(text) if flag else text)
        except Exception:  # noqa: BLE001 -- битый чанк в поиске не помеха
            pass
    return out


def _blob(path):
    """Всё, что физически лежит в файле: сырые байты + распакованные потоки PDF
    (в т.ч. объектные потоки) и сжатые чанки PNG."""
    data = _read(path)
    parts = [data]
    if data[:5] == b"%PDF-":
        try:
            with pikepdf.open(path) as q:
                for o in q.objects:
                    if isinstance(o, pikepdf.Stream):
                        try:
                            parts.append(o.read_bytes())
                        except Exception:  # noqa: BLE001
                            pass
        except Exception:  # noqa: BLE001
            pass
    elif data[:8] == PNG_SIG:
        parts += _png_inflated(data)
    return b"\n".join(parts)


def _leaked(path, texts, raw=()):
    """Какие из значений (текст во всех кодировках) и сырых байтовых иголок
    физически лежат в файле."""
    low = _blob(path).lower()
    found = [t for t in texts if any(n.lower() in low for n in _needles(t))]
    found += ["<байты %s...>" % n[:6].hex() for n in raw if n.lower() in low]
    return found


# ---------------------------------------------------------------------------
# сборщики файлов (фикстур, которых нет в tests/fixtures.py: SOF и Orientation
# у JPEG, iCCP/eXIf у PNG, подпись и теги у PDF)
# ---------------------------------------------------------------------------

EX_ARTIST = "Орлов Орёл Орлович"
EX_MAKE = "NIKON CORPORATION"
EX_MODEL = "NIKON Z 9 Mk II"
EX_SOFT = "Capture One 23 Windows"
EX_DATE = "2024:06:15 10:30:00"
EX_BODY = "BODY-SN-7731-ZQ"
EX_LENS = "LENS-SN-4419-XR"
EX_TEXTS = [EX_ARTIST, EX_MAKE, EX_MODEL, EX_SOFT, EX_DATE, EX_BODY, EX_LENS]
EX_ORIENT_TAG, EX_ARTIST_TAG, EX_GPS_TAG, EX_EXIF_TAG = 0x0112, 0x013B, 0x8825, 0x8769
# теги IFD0, которых в чистом результате быть не должно
EX_FORBIDDEN = {0x010F, 0x0110, 0x0131, EX_ARTIST_TAG, EX_GPS_TAG, EX_EXIF_TAG}


def _gps_needles(e):
    """Байты рациональных чисел широты и долготы в заданном порядке байтов."""
    return [struct.pack(e + "6I", 59, 1, 56, 1, 1500, 100),
            struct.pack(e + "6I", 30, 1, 19, 1, 2300, 100)]


def _exif_tiff(orient, order="<"):
    """TIFF-блок EXIF: Make/Model/Software/Artist, ExifIFD (дата, серийники тела и
    объектива) и GPS. orient=None -- без тега Orientation. order: "<" или ">"."""
    e = order

    def asc(s):
        return s.encode("utf-8") + b"\0"

    def ifd(tags, base):
        ents, blob, start = b"", b"", base + 2 + 12 * len(tags) + 4
        for tag, typ, cnt, raw in tags:
            if len(raw) <= 4:
                val = raw + b"\0" * (4 - len(raw))
            else:
                if len(blob) % 2:
                    blob += b"\0"
                val = struct.pack(e + "I", start + len(blob))
                blob += raw
            ents += struct.pack(e + "HHI", tag, typ, cnt) + val
        if len(blob) % 2:
            blob += b"\0"
        return struct.pack(e + "H", len(tags)) + ents + struct.pack(e + "I", 0) + blob

    def rat(*pairs):
        return b"".join(struct.pack(e + "II", a, b) for a, b in pairs)

    def ifd0(exif_off, gps_off):
        t = [(0x010F, 2, len(asc(EX_MAKE)), asc(EX_MAKE)), (0x0110, 2, len(asc(EX_MODEL)), asc(EX_MODEL))]
        if orient is not None:
            t.append((EX_ORIENT_TAG, 3, 1, struct.pack(e + "H", orient)))
        t += [(0x0131, 2, len(asc(EX_SOFT)), asc(EX_SOFT)),
              (EX_ARTIST_TAG, 2, len(asc(EX_ARTIST)), asc(EX_ARTIST)),
              (EX_EXIF_TAG, 4, 1, struct.pack(e + "I", exif_off)),
              (EX_GPS_TAG, 4, 1, struct.pack(e + "I", gps_off))]
        return ifd(t, 8)

    exif_off = 8 + len(ifd0(0, 0))
    exif = ifd([(0x9003, 2, len(asc(EX_DATE)), asc(EX_DATE)),
                (0xA431, 2, len(asc(EX_BODY)), asc(EX_BODY)),
                (0xA435, 2, len(asc(EX_LENS)), asc(EX_LENS))], exif_off)
    gps_off = exif_off + len(exif)
    gps = ifd([(0x0001, 2, 2, b"N\0"), (0x0002, 5, 3, rat((59, 1), (56, 1), (1500, 100))),
               (0x0003, 2, 2, b"E\0"), (0x0004, 5, 3, rat((30, 1), (19, 1), (2300, 100)))], gps_off)
    return (b"II" if e == "<" else b"MM") + struct.pack(e + "HI", 42, 8) + ifd0(exif_off, gps_off) + exif + gps


def _entropy():
    """Энтропийный блок с байт-стаффингом FF00 и маркерами RSTn: наивный сканер,
    обрывающий разбор на первом 0xFF, на нём ломается."""
    raw = hashlib.sha256(b"entropy").digest() * 6
    out = b"\xab\xff\x00"
    chunks = [raw[i:i + 48] for i in range(0, len(raw), 48)]
    for k, c in enumerate(chunks):
        out += c.replace(b"\xff", b"\xff\x00")
        if k < len(chunks) - 1:
            out += bytes([0xFF, 0xD0 + k % 8])
    return out


def _jpeg_bytes(orient=6, order="<", w=640, h=480, exif=True, sof=0xC0, tail=b""):
    """JPEG с настоящим SOF (по умолчанию 640x480), DQT, DHT и SOS."""
    segs = [b"\xff\xd8"]
    if exif:
        app1 = b"Exif\0\0" + _exif_tiff(orient, order)
        segs.append(b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1)
    segs.append(b"\xff\xdb" + struct.pack(">H", 67) + b"\x00" + bytes(range(1, 65)))
    segs.append(bytes([0xFF, sof]) + struct.pack(">H", 17) + struct.pack(">BHHB", 8, h, w, 3)
                + b"\x01\x22\x00\x02\x11\x00\x03\x11\x00")
    dht = b"\x00" + bytes([0, 1] + [0] * 14) + b"\x00"
    segs.append(b"\xff\xc4" + struct.pack(">H", 2 + len(dht)) + dht)
    sos = b"\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00"
    segs.append(b"\xff\xda" + struct.pack(">H", 2 + len(sos)) + sos)
    segs.append(_entropy() + b"\xff\xd9" + tail)
    return b"".join(segs)


def _jpeg_segments(data):
    """Независимый разбор сегментов JPEG до SOS включительно: [(маркер, начало, конец)]."""
    if data[:2] != b"\xff\xd8":
        return []
    segs, i = [], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            break
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xD9:
            break
        n = struct.unpack(">H", data[i + 2:i + 4])[0]
        segs.append((m, i, i + 2 + n))
        if m == 0xDA:
            break
        i += 2 + n
    return segs


def _sos_at(data):
    return next((a for m, a, _b in _jpeg_segments(data) if m == 0xDA), None)


def _sof_dims(data):
    """(ширина, высота) из первого SOF, None если SOF нет."""
    for m, a, _b in _jpeg_segments(data):
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            _p, h, w = struct.unpack(">BHH", data[a + 4:a + 9])
            return w, h
    return None


def _tiff_ifd0(tiff):
    """IFD0 TIFF-блока: (порядок байтов, {тег: (тип, счётчик, 4 байта значения)}) или None."""
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(e + "H", tiff[2:4])[0] != 42:
        return None
    off = struct.unpack(e + "I", tiff[4:8])[0]
    if off + 2 > len(tiff):
        return None
    n, tags = struct.unpack(e + "H", tiff[off:off + 2])[0], {}
    for i in range(min(n, 256)):
        ent = tiff[off + 2 + 12 * i:off + 14 + 12 * i]
        if len(ent) < 12:
            break
        tag, typ, cnt = struct.unpack(e + "HHI", ent[:8])
        tags[tag] = (typ, cnt, ent[8:12])
    return e, tags


def _ifd0_tags(tiff_blocks):
    """Объединённые теги IFD0 всех блоков: {тег: ((тип, счётчик, значение4), порядок)}."""
    tags = {}
    for blk in tiff_blocks:
        got = _tiff_ifd0(blk)
        if got:
            tags.update({t: (v, got[0]) for t, v in got[1].items()})
    return tags


def _orientation(tags):
    """Значение Orientation из _ifd0_tags() или None."""
    if EX_ORIENT_TAG not in tags:
        return None
    (_typ, _cnt, raw), e = tags[EX_ORIENT_TAG]
    return struct.unpack(e + "H", raw[:2])[0]


def _jpeg_exif_tags(data):
    return _ifd0_tags([data[a + 10:b] for m, a, b in _jpeg_segments(data)
                       if m == 0xE1 and data[a + 4:a + 10] == b"Exif\0\0"])


def _png_exif_tags(data):
    return _ifd0_tags([body for t, body, _ok in _chunks(data) if t == b"eXIf"])


# --- PNG ---------------------------------------------------------------------

P_AUTHOR = "Ivan Ivanov Testov"
P_SOFT = "GIMP 2.10.34 Linux"
P_PATH = "C:\\Users\\ivan\\secret\\photo.png"
P_ZTXT = "Secret description Ivan Ivanov Testov"
P_ITXT = "Секретный проект Орёл"
P_XMP = "Orlov Test Author"
P_TEXTS = [P_AUTHOR, P_SOFT, P_PATH, P_ZTXT, P_ITXT, P_XMP, "Название"]
ICC_BODY = b"Test ICC profile\0\0" + zlib.compress(b"ICCPROFILE" * 30)
PNG_FORBIDDEN = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _png_bytes(w=8, h=6, pre=(), post=(), trailer=b""):
    """PNG RGB 8 бит настоящего размера w x h; IDAT в двух чанках. -> (байты, строки пикселей)."""
    rows = b"".join(b"\x00" + bytes((x * 31 + y * 17) % 256 for x in range(w * 3)) for y in range(h))
    z = zlib.compress(rows, 9)
    cut = max(1, len(z) // 2)
    ch = fixtures._png_chunk
    data = (PNG_SIG + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + b"".join(pre)
            + ch(b"IDAT", z[:cut]) + ch(b"IDAT", z[cut:]) + b"".join(post) + ch(b"IEND", b"") + trailer)
    return data, rows


def _live_bomb_png(tmp):
    """Валидный PNG, в котором лежит настоящий zTXt-чанк-бомба из fixtures.make_broken.
    -> (путь, строки пикселей)."""
    bomb_src = fixtures.make_broken(tmp)["png_ztxt_bomb"]
    bomb_body = next(b for t, b, _o in _chunks(_read(bomb_src)) if t == b"zTXt")
    data, rows = _png_bytes(pre=[fixtures._png_chunk(b"zTXt", bomb_body)])
    return _write(os.path.join(tmp, "bomb_live.png"), data), rows


def _png_full():
    """PNG, в котором есть ВСЁ из списка на удаление и всё из списка на сохранение."""
    ch = fixtures._png_chunk
    xmp = (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
           b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" dc:creator="'
           + P_XMP.encode("utf-8") + b'"/></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    pre = [ch(b"gAMA", struct.pack(">I", 45455)), ch(b"iCCP", ICC_BODY),
           ch(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)),
           ch(b"tEXt", b"Author\0" + P_AUTHOR.encode("latin-1")),
           ch(b"tEXt", b"Software\0" + P_SOFT.encode("latin-1")),
           ch(b"tEXt", b"Comment\0" + P_PATH.encode("latin-1")),
           ch(b"iTXt", b"XML:com.adobe.xmp\0\x01\x00\x00\x00" + zlib.compress(xmp)),
           ch(b"eXIf", _exif_tiff(None, "<"))]
    post = [ch(b"tIME", struct.pack(">HBBBBB", 2024, 6, 15, 10, 30, 0)),
            ch(b"zTXt", b"Description\0\0" + zlib.compress(P_ZTXT.encode("latin-1"))),
            ch(b"iTXt", b"Title\0\0\0ru\0" + "Название".encode("utf-8") + b"\0" + P_ITXT.encode("utf-8"))]
    return _png_bytes(pre=pre, post=post)


def _png_idat(data):
    return b"".join(body for t, body, _ok in _chunks(data) if t == b"IDAT")


def _png_dims(data):
    for t, body, _ok in _chunks(data):
        if t == b"IHDR":
            return struct.unpack(">II", body[:8])
    return None


def _corrupt_crc(data, ctype):
    """Копия PNG с испорченной CRC первого чанка нужного типа."""
    pos = 8
    while pos + 12 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == ctype:
            i = pos + 8 + n
            return data[:i] + bytes([data[i] ^ 0xFF]) + data[i + 1:]
        pos += 12 + n
    raise ValueError("нет чанка %r" % ctype)


# --- PDF ---------------------------------------------------------------------

RICH_AUTHOR = "Иван Петров Сергеевич"
RICH_COMPANY = "ACME Corporation Ltd"
RICH_EMAIL = "ivan.petrov@acme-corp.example"
RICH_PATH = "C:\\Users\\ivan.petrov\\Documents\\Договор_ACME.docx"
RICH_TITLE = "Годовой отчёт"
RICH_PRODUCER = "Microsoft: Print To PDF"
RICH_CREATOR = "Microsoft Word"
RICH_CDATE = "D:20240101120000+03'00'"
RICH_MDATE = "D:20240105150500+03'00'"
SIGNER = "Подписант Тестовый Иванович"
SIG_AUTHOR = "Автор Подписанного Файла"
STRUCT_AUTHOR = "Структура Автор Тестов"


def _pdf_rich(path):
    """PDF через pikepdf с заполненными /Info и XMP (личность, окружение, приложение)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:creator"] = [RICH_AUTHOR]
        meta["dc:title"] = RICH_TITLE
        meta["xmp:CreatorTool"] = RICH_CREATOR
        meta["pdf:Producer"] = RICH_PRODUCER
        meta["xmpMM:DocumentID"] = "uuid:aaaabbbb-1111-2222-3333-444455556666"
        meta["xmpMM:InstanceID"] = "uuid:ccccdddd-1111-2222-3333-444455556666"
    di = pdf.docinfo
    di["/Author"] = RICH_AUTHOR
    di["/Title"] = RICH_TITLE
    di["/Company"] = RICH_COMPANY
    di["/Creator"] = RICH_CREATOR
    di["/Producer"] = RICH_PRODUCER
    di["/Keywords"] = RICH_PATH
    di["/Subject"] = "Контакт: " + RICH_EMAIL
    di["/CreationDate"] = RICH_CDATE
    di["/ModDate"] = RICH_MDATE
    pdf.save(path)
    pdf.close()
    return path


def _pdf_tagged(path, author=STRUCT_AUTHOR):
    """PDF с /StructTreeRoot, /MarkInfo, /Lang, /Outlines (3 закладки), /ViewerPreferences."""
    N = pikepdf.Name
    pdf = pikepdf.new()
    for _ in range(3):
        pdf.add_blank_page(page_size=(595, 842))
    pdf.Root["/Lang"] = "ru-RU"
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=True)
    pdf.Root["/StructTreeRoot"] = pdf.make_indirect(pikepdf.Dictionary(Type=N.StructTreeRoot))
    pdf.Root["/ViewerPreferences"] = pikepdf.Dictionary(DisplayDocTitle=True)
    with pdf.open_outline() as ol:
        for i, title in enumerate(("Глава 1", "Глава 2", "Глава 3")):
            ol.root.append(pikepdf.OutlineItem(title, i))
    pdf.docinfo["/Author"] = author
    pdf.save(path)
    pdf.close()
    return path


def _pdf_form(path, kind):
    """kind: "filled" -- заполненная подпись; "placeholder" -- пустая заготовка
    (/FT /Sig без /V); "docmdp" -- сертифицирующая подпись только в /Perms."""
    N, D, S, A = pikepdf.Name, pikepdf.Dictionary, pikepdf.String, pikepdf.Array
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.docinfo["/Author"] = SIG_AUTHOR

    def sig_dict():
        return pdf.make_indirect(D(Type=N.Sig, Filter=N("/Adobe.PPKLite"), SubFilter=N("/adbe.pkcs7.detached"),
                                   Name=S(SIGNER), M=S("D:20240105150500"), Contents=S(b"\x00" * 16),
                                   ByteRange=A([0, 1, 2, 3])))

    def widget(**kw):
        a = pdf.make_indirect(D(Type=N.Annot, Subtype=N.Widget, Rect=A([10, 10, 100, 40]), **kw))
        page = pdf.pages[0].obj
        if "/Annots" not in page:
            page["/Annots"] = A()
        page["/Annots"].append(a)
        return a

    if kind == "filled":
        pdf.Root["/AcroForm"] = D(Fields=A([widget(FT=N.Sig, T=S("Sig1"), V=sig_dict())]), SigFlags=3)
    elif kind == "placeholder":
        pdf.Root["/AcroForm"] = D(Fields=A([widget(FT=N.Sig, T=S("Sig2"))]))
    elif kind == "docmdp":
        pdf.Root["/Perms"] = D(DocMDP=sig_dict())
    pdf.save(path)
    pdf.close()
    return path


def _pdf_unequal_id(path):
    """PDF, у которого /ID[0] != /ID[1] (разошедшаяся пара: так выглядит пересохранённый файл)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.docinfo["/Author"] = "Автор Разных Идентификаторов"
    pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(b"A" * 16), pikepdf.String(b"B" * 16)])
    pdf.save(path)
    pdf.close()
    return path


def _pdf_incr_secret(path):
    """Ручная инкрементальная ревизия: в первой ревизии TOPSECRET, во второй -- «замазка»."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.pages[0].obj["/Contents"] = pdf.make_stream(b"BT 10 100 Td (TOPSECRET 4111) Tj ET")
    pdf.docinfo["/Author"] = "Иван Петров Секретный"
    pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable, compress_streams=False, static_id=True)
    pdf.close()
    with pikepdf.open(path) as q:
        n = q.pages[0].Contents.objgen[0]
    data = _read(path)
    old = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    inner = re.search(rb"trailer\s*<<(.*?)>>", data, re.S).group(1)
    new = b"BT 10 100 Td (REDACTED) Tj ET"
    obj = b"\n%d 0 obj\n<< /Length %d >>\nstream\n" % (n, len(new)) + new + b"\nendstream\nendobj\n"
    o_off = len(data) + obj.index(b"%d 0 obj" % n)
    x_off = len(data) + len(obj)
    tail = (b"xref\n%d 1\n%010d 00000 n \ntrailer\n<<" % (n, o_off) + inner
            + b" /Prev %d >>\nstartxref\n%d\n" % (old, x_off) + b"%%EOF\n")
    _write(path, data + obj + tail)
    return path


def _trailer_ids(path):
    """[ID0, ID1] из трейлера (bytes) или None."""
    with pikepdf.open(path) as q:
        ids = q.trailer.get("/ID")
        if isinstance(ids, pikepdf.Array) and len(ids) == 2:
            return [bytes(ids[0]), bytes(ids[1])]
    return None


# ===========================================================================
# 1. ИНВАРИАНТ sha256 и ОРАКУЛ -- на каждой фикстуре и каждом профиле
# ===========================================================================


@_with_tmp
def test_01_invariant_and_oracle_all_fixtures(tmp):
    """Каждая make_* из tests/fixtures.py x каждый профиль. Фикстура, которую
    кто-то добавит позже, попадёт сюда автоматически."""
    names = sorted(n for n in dir(fixtures) if n.startswith("make_") and n != "make_broken")
    cleaned = set()
    for name in names:
        d = os.path.join(tmp, name)
        os.makedirs(d)
        path, _exp = getattr(fixtures, name)(d)
        for prof in PROFILES:
            label = "%s/%s" % (name, _pname(prof))
            res, dst = _clean(name, d, path, prof)
            if res is None:
                continue
            if res.ok:
                cleaned.add(name)
                _check("%s: выдан файл -- dst существует и не пуст" % label,
                       os.path.isfile(dst) and os.path.getsize(dst) > 0)
                _check("%s: диспетчер выполнил повторную инспекцию (verified)" % label,
                       res.verified, res.errors)
                _check("%s: диспетчер доказал чистоту (clean)" % label, res.clean,
                       [(s.label, s.found_at) for s in res.survived])
                _oracle(label, path, dst)
            else:
                _check("%s: отказ -- выходной файл НЕ создан" % label, not os.path.exists(dst))
    want = {"make_pdf", "make_pdf_incremental", "make_jpeg", "make_png"}
    _check("оракул отработал на PDF, PDF с ревизиями, JPEG и PNG (тест не вакуумен)",
           want <= cleaned, sorted(cleaned))


# ===========================================================================
# 2. Независимый сырой поиск значений в байтах результата
# ===========================================================================


@_with_tmp
def test_02_raw_leak_scan(tmp):
    """Оракул-инспектор мог бы пропустить то, что физически осталось в файле.
    Здесь значения ищутся прямо в байтах (все кодировки, распакованные потоки PDF
    и сжатые чанки PNG)."""
    pdf, pexp = fixtures.make_pdf(tmp)
    inc, iexp = fixtures.make_pdf_incremental(tmp)
    jpg, jexp = fixtures.make_jpeg(tmp)
    png, nexp = fixtures.make_png(tmp)
    cases = (
        # (метка, путь, значения, которые ОБЯЗАНЫ исчезнуть в STEALTH / в PARANOID)
        ("pdf", pdf, pexp["identity"], pexp["values"], ()),
        ("pdf_incremental", inc, iexp["values"] + [fixtures.PDF_INCR_OLD_AUTHOR],
         iexp["values"] + [fixtures.PDF_INCR_OLD_AUTHOR], ()),
        ("jpeg", jpg, jexp["values"], jexp["values"], _gps_needles("<")),
        ("png", png, nexp["values"], nexp["values"], ()),
    )
    for tag, path, st_vals, pa_vals, raw in cases:
        seen = _leaked(path, pa_vals)
        _check("%s: поиск видит значения в ИСХОДНОМ файле (иначе проверка вакуумна)" % tag,
               len(seen) == len(pa_vals), "нашлось %d из %d" % (len(seen), len(pa_vals)))
        for prof in PROFILES:
            res, dst = _clean("raw_" + tag, tmp, path, prof)
            if res is None or not _check("%s/%s: ok" % (tag, _pname(prof)), res.ok, res.errors):
                continue
            vals = st_vals if prof is STEALTH else pa_vals
            gone = _leaked(dst, vals, raw)
            _check("%s/%s: в байтах результата нет ни одного из %d значений" % (tag, _pname(prof), len(vals)),
                   not gone, gone)


# ===========================================================================
# 3. PDF: PARANOID -- critical_after == 0; STEALTH -- только новые /ID
# ===========================================================================


@_with_tmp
def test_03_pdf_profiles_and_id(tmp):
    for tag, path in (("pdf", fixtures.make_pdf(tmp)[0]), ("pdf_rich", _pdf_rich(os.path.join(tmp, "rich.pdf")))):
        src_ids = _trailer_ids(path)
        _check("%s: в исходнике есть /ID (иначе проверка регенерации вакуумна)" % tag, src_ids is not None)

        # ---- PARANOID: критичного не остаётся совсем ----
        res, dst = _clean(tag, tmp, path, PARANOID)
        if res is not None and _check("%s/paranoid: ok" % tag, res.ok, res.errors):
            rep = inspect_file(dst)
            left = [(f.label, f.value[:30]) for f in rep.findings if f.risk in _LEAKY]
            _check("%s/paranoid: critical_after == 0 (по данным диспетчера)" % tag,
                   res.critical_after == 0, res.critical_after)
            _check("%s/paranoid: critical == 0 по СОБСТВЕННОЙ повторной инспекции результата" % tag,
                   rep.critical == 0, left)
            _check("%s/paranoid: critical_before > 0 -- чистка было что убирать" % tag, res.critical_before > 0)
            _check("%s/paranoid: остатка нет (residual пуст)" % tag, res.residual == [], res.residual)
            _check("%s/paranoid: /ID убран из трейлера" % tag, _trailer_ids(dst) is None)

        # ---- STEALTH: допустим остаток ровно из перегенерированных /ID ----
        res, dst = _clean(tag, tmp, path, STEALTH)
        if res is None or not _check("%s/stealth: ok" % tag, res.ok, res.errors):
            continue
        rep = inspect_file(dst)
        crit = [f for f in rep.findings if f.risk in _LEAKY]
        labels = {f.label.split(" ")[0] for f in crit}
        _check("%s/stealth: критичный остаток состоит ТОЛЬКО из trailer /ID[0], /ID[1]" % tag,
               labels <= {"/ID[0]", "/ID[1]"}, [(f.location, f.label, f.value[:30]) for f in crit])
        was = {_norm(f.value) for f in inspect_file(path).findings}
        _check("%s/stealth: значения остатка НОВЫЕ -- в находках исходника их нет" % tag,
               all(_norm(f.value) not in was for f in crit), [f.value[:20] for f in crit])
        new_ids = _trailer_ids(dst)
        _check("%s/stealth: в результате есть /ID" % tag, new_ids is not None)
        if src_ids and new_ids:
            _check("%s/stealth: /ID[0] ОТЛИЧАЕТСЯ от исходного (иначе это не регенерация)" % tag,
                   new_ids[0] != src_ids[0], new_ids[0].hex())
            _check("%s/stealth: /ID[1] ОТЛИЧАЕТСЯ от исходного" % tag, new_ids[1] != src_ids[1], new_ids[1].hex())
            _check("%s/stealth: форма пары сохранена (равная осталась равной)" % tag,
                   (src_ids[0] == src_ids[1]) == (new_ids[0] == new_ids[1]), (src_ids, new_ids))
        _check("%s/stealth: диспетчер: остаток критичного -- только новые значения" % tag,
               all(r[2] for r in res.residual), res.residual)
        _check("%s/stealth: у чистки было что убирать" % tag, res.critical_before > res.critical_after
               or res.sensitive_before > res.sensitive_after, (res.critical_before, res.critical_after))

    # ---- разошедшаяся пара /ID ----
    path = _pdf_unequal_id(os.path.join(tmp, "uneq.pdf"))
    src_ids = _trailer_ids(path)
    _check("uneq: в исходнике /ID[0] != /ID[1] (предусловие)", src_ids is not None and src_ids[0] != src_ids[1])
    res, dst = _clean("uneq", tmp, path, STEALTH)
    if res is not None and _check("uneq/stealth: ok", res.ok, res.errors):
        new = _trailer_ids(dst)
        _check("uneq/stealth: оба /ID ОТЛИЧАЮТСЯ от исходных", bool(new and src_ids)
               and new[0] != src_ids[0] and new[1] != src_ids[1], (src_ids, new))
        _check("uneq/stealth: пара осталась разошедшейся (форма как у исходника)",
               bool(new) and new[0] != new[1], new)
    res, dst = _clean("uneq", tmp, path, PARANOID)
    if res is not None and _check("uneq/paranoid: ok", res.ok, res.errors):
        _check("uneq/paranoid: critical_after == 0", res.critical_after == 0, res.critical_after)

    # ---- каким чистка оставляет приложение и даты (STEALTH не обнуляет, PARANOID убирает) ----
    path = _pdf_rich(os.path.join(tmp, "rich2.pdf"))
    res, dst = _clean("rich_info", tmp, path, STEALTH)
    if res is not None and _check("rich_info/stealth: ok", res.ok, res.errors):
        with pikepdf.open(dst) as q:
            info = {k: str(v) for k, v in q.docinfo.items()}
        _check("STEALTH: /Producer и /Creator оставлены как были (пара Creator/Producer согласована)",
               info.get("/Producer") == RICH_PRODUCER and info.get("/Creator") == RICH_CREATOR, info)
        _check("STEALTH: даты не обнулены и не подменены",
               info.get("/CreationDate") == RICH_CDATE and info.get("/ModDate") == RICH_MDATE, info)
        _check("STEALTH: /Author, /Company, путь и email убраны из /Info",
               not {"/Author", "/Company"} & set(info)
               and not any(RICH_EMAIL in v or RICH_PATH in v for v in info.values()), info)
        gone = _leaked(dst, [RICH_AUTHOR, RICH_COMPANY, RICH_EMAIL, RICH_PATH])
        _check("STEALTH: ФИО, компания, email и путь не лежат ни в байтах, ни в потоках (XMP)", not gone, gone)
    res, dst = _clean("rich_info", tmp, path, PARANOID)
    if res is not None and _check("rich_info/paranoid: ok", res.ok, res.errors):
        with pikepdf.open(dst) as q:
            info = {k: str(v) for k, v in q.docinfo.items()}
        _check("PARANOID: /Producer, /Creator и даты убраны", not {"/Producer", "/Creator", "/CreationDate",
                                                                    "/ModDate"} & set(info), info)


# ===========================================================================
# 4. PDF: инкрементальные ревизии выброшены
# ===========================================================================


@_with_tmp
def test_04_pdf_incremental_revisions(tmp):
    cases = [("pdf_incremental", fixtures.make_pdf_incremental(tmp)[0]),
             ("pdf_incr_secret", _pdf_incr_secret(os.path.join(tmp, "secret.pdf")))]
    for tag, path in cases:
        src = _read(path)
        _check("%s: в ИСХОДНИКЕ два %%%%EOF -- фикстура и правда инкрементальная" % tag,
               src.count(b"%%EOF") == 2, src.count(b"%%EOF"))
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            out = _read(dst)
            _check("%s: в сырых байтах результата РОВНО одно вхождение %%%%EOF" % label,
                   out.count(b"%%EOF") == 1, out.count(b"%%EOF"))
            with pikepdf.open(dst) as q:
                _check("%s: в трейлере результата нет /Prev" % label, "/Prev" not in q.trailer)
                _check("%s: страница на месте" % label, len(q.pages) == 1)
            _check("%s: в действиях записано, что ревизии выброшены" % label,
                   any(a.act is Act.REMOVED and "ревизи" in a.label.lower() for a in res.actions),
                   [a.label for a in res.actions])
    # то, ради чего выбрасываются ревизии: текст «под замазкой» уходит физически
    path = cases[1][1]
    raw = _read(path)
    _check("секрет: TOPSECRET лежит в сырых байтах ИСХОДНИКА (в прошлой ревизии)", b"TOPSECRET" in raw)
    with pikepdf.open(path) as q:
        _check("секрет: текущая ревизия показывает «замазку», а не секрет (предусловие)",
               b"REDACTED" in q.pages[0].Contents.read_bytes())
    for prof in PROFILES:
        res, dst = _clean("pdf_incr_secret", tmp, path, prof)
        if res is None or not res.ok:
            continue
        blob = _blob(dst)
        _check("секрет/%s: TOPSECRET и 4111 не лежат ни в байтах, ни в распакованных потоках" % _pname(prof),
               b"TOPSECRET" not in blob and b"4111" not in blob)
        _check("секрет/%s: ТЕКУЩЕЕ содержимое страницы (REDACTED) сохранено" % _pname(prof), b"REDACTED" in blob)
        _check("секрет/%s: автор прошлой ревизии не лежит в файле" % _pname(prof),
               not _leaked(dst, ["Иван Петров Секретный"]))


# ===========================================================================
# 5. PDF: структурное сохраняется в ОБОИХ профилях
# ===========================================================================


@_with_tmp
def test_05_pdf_structural_kept(tmp):
    path = _pdf_tagged(os.path.join(tmp, "tagged.pdf"))
    need = (("/StructTreeRoot", "Дерево структуры"), ("/Lang", "Язык документа"), ("/Outlines", "Оглавление"))
    src_rep = inspect_file(path)
    for key, word in need:
        _check("tagged: в отчёте инспекции ИСХОДНИКА есть %s (предусловие)" % key,
               any(f.risk is Risk.STRUCTURAL and word in f.label for f in src_rep.findings))
    for prof in PROFILES:
        label = "tagged/%s" % _pname(prof)
        res, dst = _clean("tagged", tmp, path, prof)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        rep = inspect_file(dst)
        kept = {a.label for a in res.actions if a.act is Act.KEPT}
        for key, word in need:
            _check("%s: %s -- в отчёте о чистке записан как сохранённый (KEPT)" % (label, key),
                   key in kept, sorted(kept))
            _check("%s: %s -- есть в отчёте инспекции РЕЗУЛЬТАТА (STRUCTURAL)" % (label, key),
                   any(f.risk is Risk.STRUCTURAL and word in f.label for f in rep.findings))
        removed = {a.label for a in res.actions if a.act is Act.REMOVED}
        _check("%s: ни одно структурное поле не помечено удалённым" % label,
               not removed & {"/StructTreeRoot", "/MarkInfo", "/Lang", "/Outlines", "/PageLabels",
                              "/ViewerPreferences"}, sorted(removed))
        with pikepdf.open(dst) as q:
            root = q.Root
            _check("%s: /StructTreeRoot физически в каталоге результата" % label, "/StructTreeRoot" in root)
            _check("%s: /MarkInfo физически сохранён" % label, "/MarkInfo" in root)
            _check("%s: /Lang == ru-RU" % label, str(root.get("/Lang")) == "ru-RU", root.get("/Lang"))
            titles, item = [], root.Outlines.First if "/Outlines" in root else None
            while item is not None and len(titles) < 10:
                titles.append(str(item.Title))
                item = item.get("/Next")
            _check("%s: закладки -- те же три заголовка в том же порядке" % label,
                   titles == ["Глава 1", "Глава 2", "Глава 3"], titles)
            _check("%s: все 3 страницы на месте" % label, len(q.pages) == 3)
        _check("%s: личность (автор) при этом убрана" % label, not _leaked(dst, [STRUCT_AUTHOR]))
        _oracle(label, path, dst)


# ===========================================================================
# 6. PDF: подпись
# ===========================================================================


@_with_tmp
def test_06_pdf_signature(tmp):
    signed = _pdf_form(os.path.join(tmp, "signed.pdf"), "filled")
    src_rep = inspect_file(signed)
    _check("signed: инспектор видит заполненную подпись (предусловие)",
           any(SIGNER in f.value or "подпис" in f.label.lower() for f in src_rep.findings))

    # --- по умолчанию: честный отказ, файла нет ---
    for prof in PROFILES:
        label = "signed/%s" % _pname(prof)
        res, dst = _clean("signed", tmp, signed, prof)
        if res is None:
            continue
        _check("%s: по умолчанию ok=False" % label, not res.ok)
        _check("%s: выходной файл НЕ создан" % label, not os.path.exists(dst))
        _check("%s: причина названа внятно (подпись)" % label,
               any("подпис" in e.lower() for e in res.errors), res.errors)
        _check("%s: нет ни действий, ни заявленной чистоты" % label,
               not res.actions and not res.clean and res.critical_after == -1)
    out = os.path.join(tmp, "direct.pdf")
    r = clean_pdf(signed, out)
    _check("clean_pdf напрямую: ok=False, файла и .part нет, причина названа",
           not r.ok and not os.path.exists(out) and not os.path.exists(out + ".part")
           and any("подпис" in e.lower() for e in r.errors), r.errors)

    # --- force_signed: чистит, подпись уничтожена и об этом сказано ---
    for prof in PROFILES:
        label = "signed_force/%s" % _pname(prof)
        res, dst = _clean("signed_force", tmp, signed, prof, force_signed=True)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        blob = _blob(dst)
        _check("%s: подпись физически убрана (нет adbe.pkcs7)" % label, b"adbe.pkcs7" not in blob)
        _check("%s: имя подписанта не лежит в файле" % label, not _leaked(dst, [SIGNER]))
        _check("%s: в действиях честно записано, что подпись уничтожена (REMOVED)" % label,
               any(a.act is Act.REMOVED and "подпис" in a.label.lower() for a in res.actions),
               [(a.act.value, a.label) for a in res.actions])
        with pikepdf.open(dst) as q:
            fields = q.Root.AcroForm.Fields if "/AcroForm" in q.Root else []
            _check("%s: поле осталось пустой заготовкой (/V нет)" % label,
                   all("/V" not in f for f in fields), [list(f.keys()) for f in fields])
        _oracle(label, signed, dst)

    # --- пустая заготовка поля: НЕ подпись, чистка идёт штатно ---
    ph = _pdf_form(os.path.join(tmp, "placeholder.pdf"), "placeholder")
    for prof in PROFILES:
        label = "sig_placeholder/%s" % _pname(prof)
        res, dst = _clean("sig_placeholder", tmp, ph, prof)
        if res is None or not _check("%s: чистка идёт штатно, force_signed не нужен" % label, res.ok, res.errors):
            continue
        with pikepdf.open(dst) as q:
            f = q.Root.AcroForm.Fields[0]
            _check("%s: заготовка на месте: /FT /Sig, /V нет" % label,
                   str(f.get("/FT")) == "/Sig" and "/V" not in f, list(f.keys()))
        _check("%s: автор убран" % label, not _leaked(dst, [SIG_AUTHOR]))

    # --- сертифицирующая подпись только в /Perms /DocMDP ---
    dm = _pdf_form(os.path.join(tmp, "docmdp.pdf"), "docmdp")
    res, dst = _clean("docmdp", tmp, dm, STEALTH)
    if res is not None:
        _check("docmdp: сертифицирующая подпись (/Perms /DocMDP) -- тоже отказ по умолчанию",
               not res.ok and not os.path.exists(dst) and any("подпис" in e.lower() for e in res.errors),
               res.errors)
    res, dst = _clean("docmdp_force", tmp, dm, STEALTH, force_signed=True)
    if res is not None and _check("docmdp_force: ok", res.ok, res.errors):
        with pikepdf.open(dst) as q:
            _check("docmdp_force: /Perms снят, подписи в файле нет",
                   "/Perms" not in q.Root and b"adbe.pkcs7" not in _blob(dst))


# ===========================================================================
# 7. JPEG: без перекодирования -- энтропийный блок бит в бит
# ===========================================================================


@_with_tmp
def test_07_jpeg_scan_bit_exact(tmp):
    fx_path = fixtures.make_jpeg(tmp)[0]
    own = _write(os.path.join(tmp, "own.jpg"), _jpeg_bytes(6))
    for tag, path in (("fixture_jpeg", fx_path), ("sof_640x480", own)):
        src = _read(path)
        so = _sos_at(src)
        _check("%s: в исходнике найден SOS (предусловие)" % tag, so is not None)
        if so is None:
            continue
        _check("%s: блок от SOS до конца файла непустой (%d байт)" % (tag, len(src) - so), len(src) - so > 10)
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            out = _read(dst)
            do = _sos_at(out)
            _check("%s: SOS найден в результате" % label, do is not None)
            if do is None:
                continue
            _check("%s: БИТ В БИТ -- src[SOS:] == dst[SOS:] (%d байт): перекодирования не было"
                   % (label, len(src) - so), src[so:] == out[do:])
            _check("%s: verify_image подтверждает scan_same" % label,
                   verify_image(path, dst).get("scan_same") is True, verify_image(path, dst))
            _check("%s: результат начинается с SOI и заканчивается EOI" % label,
                   out[:2] == b"\xff\xd8" and out[-2:] == b"\xff\xd9")
            if tag == "sof_640x480":
                _check("%s: стаффинг FF00 и маркеры RSTn внутри блока уцелели" % label, _entropy() in out)
            _check("%s: таблицы квантования (DQT) на месте, не пересобраны" % label,
                   src[[a for m, a, _b in _jpeg_segments(src) if m == 0xDB][0]:][:69]
                   in out)


# ===========================================================================
# 8. JPEG: ОРИЕНТАЦИЯ -- главная регрессия (повёрнутые на бок фотографии)
# ===========================================================================


@_with_tmp
def test_08_jpeg_orientation_preserved(tmp):
    for order in ("<", ">"):
        for orient in (1, 2, 3, 4, 5, 6, 7, 8):
            tag = "jpeg_%s_o%d" % ("LE" if order == "<" else "BE", orient)
            path = _write(os.path.join(tmp, tag + ".jpg"), _jpeg_bytes(orient, order))
            src = _read(path)
            _check("%s: независимый разбор читает Orientation=%d из ИСХОДНИКА (предусловие)" % (tag, orient),
                   _orientation(_jpeg_exif_tags(src)) == orient, _orientation(_jpeg_exif_tags(src)))
            for prof in PROFILES:
                label = "%s/%s" % (tag, _pname(prof))
                res, dst = _clean(tag, tmp, path, prof)
                if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                    continue
                out = _read(dst)
                tags = _jpeg_exif_tags(out)
                got = _orientation(tags)
                if orient == 1:
                    _check("%s: Orientation=1 -- APP1 удалён целиком либо тег равен 1" % label,
                           got in (None, 1), got)
                else:
                    _check("%s: тег Orientation ОБЯЗАН остаться, значение %d (читаем прямо из APP1)"
                           % (label, orient), got == orient, "в результате: %r" % (got,))
                bad = sorted(hex(t) for t in set(tags) & EX_FORBIDDEN)
                _check("%s: в APP1 нет тегов Artist, GPS, Make, Model, Software, указателя ExifIFD" % label,
                       not bad, bad)
                gone = _leaked(dst, EX_TEXTS, _gps_needles(order))
                _check("%s: Artist, серийники, GPS, дата, камера и ПО физически исчезли из байтов" % label,
                       not gone, gone)
                _check("%s: размеры из SOF не изменились (640x480)" % label,
                       _sof_dims(src) == (640, 480) == _sof_dims(out), (_sof_dims(src), _sof_dims(out)))
                so, do = _sos_at(src), _sos_at(out)
                _check("%s: энтропийный блок бит в бит" % label,
                       do is not None and src[so:] == out[do:])
                v = verify_image(path, dst)
                _check("%s: verify_image: ok, orient_ok, dims_same" % label,
                       v.get("ok") and v.get("orient_ok") and v.get("dims_same"), v)
                if orient in (1, 6, 8):
                    _oracle(label, path, dst)

    # PNG: тот же риск через eXIf
    for orient in (1, 6, 8):
        tag = "png_exif_o%d" % orient
        data, rows = _png_bytes(pre=[fixtures._png_chunk(b"eXIf", _exif_tiff(orient, "<"))])
        path = _write(os.path.join(tmp, tag + ".png"), data)
        _check("%s: независимый разбор читает Orientation=%d из исходного eXIf (предусловие)" % (tag, orient),
               _orientation(_png_exif_tags(data)) == orient)
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            out = _read(dst)
            tags = _png_exif_tags(out)
            got = _orientation(tags)
            if orient == 1:
                _check("%s: Orientation=1 -- eXIf удалён либо тег равен 1" % label, got in (None, 1), got)
            else:
                _check("%s: тег Orientation в eXIf ОБЯЗАН остаться, значение %d" % (label, orient),
                       got == orient, "в результате: %r" % (got,))
            bad = sorted(hex(t) for t in set(tags) & EX_FORBIDDEN)
            _check("%s: в eXIf нет Artist, GPS, Make, Model, Software" % label, not bad, bad)
            gone = _leaked(dst, EX_TEXTS, _gps_needles("<"))
            _check("%s: Artist, серийники, GPS физически исчезли из байтов" % label, not gone, gone)
            _check("%s: пиксели те же (IDAT бит в бит, распаковка даёт те же строки)" % label,
                   _png_idat(out) == _png_idat(data) and zlib.decompress(_png_idat(out)) == rows)


# ===========================================================================
# 9. Размеры изображения не изменились (SOF у JPEG, IHDR у PNG)
# ===========================================================================


@_with_tmp
def test_09_image_dims_unchanged(tmp):
    cases = []
    for w, h, sof in ((640, 480, 0xC0), (4000, 3000, 0xC0), (1024, 768, 0xC2), (1, 1, 0xC0)):
        cases.append(("jpeg_%dx%d_sof%02X" % (w, h, sof), ".jpg", _jpeg_bytes(6, "<", w, h, sof=sof),
                      (w, h), _sof_dims))
    meta = [fixtures._png_chunk(b"tEXt", b"Author\0" + P_AUTHOR.encode("latin-1"))]
    for w, h in ((8, 6), (300, 200)):
        cases.append(("png_%dx%d" % (w, h), ".png", _png_bytes(w, h, pre=meta)[0], (w, h), _png_dims))
    for tag, ext, data, want, dims in cases:
        path = _write(os.path.join(tmp, tag + ext), data)
        _check("%s: размеры исходника читаются независимым разбором: %s" % (tag, want),
               dims(data) == want, dims(data))
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            _check("%s: ширина и высота ДО и ПОСЛЕ совпадают: %s" % (label, want),
                   dims(_read(dst)) == want, dims(_read(dst)))
            v = verify_image(path, dst)
            _check("%s: verify_image: dims_same" % label, v.get("dims_same") is True, v)
    path = fixtures.make_png(tmp)[0]
    for prof in PROFILES:
        res, dst = _clean("fixture_png", tmp, path, prof)
        if res is not None and res.ok:
            _check("fixture_png/%s: IHDR (1x1) не изменился" % _pname(prof),
                   _png_dims(_read(path)) == _png_dims(_read(dst)) == (1, 1))


# ===========================================================================
# 10. PNG: чанки, CRC, порядок, ICC
# ===========================================================================


@_with_tmp
def test_10_png_chunks(tmp):
    data, rows = _png_full()
    path = _write(os.path.join(tmp, "full.png"), data)
    src_chunks = _chunks(data)
    src_types = [t for t, _b, _o in src_chunks]
    _check("предусловие: CRC всех чанков исходника верны", all(o for _t, _b, o in src_chunks))
    _check("предусловие: в исходнике есть tEXt, zTXt, iTXt, eXIf, tIME и iCCP",
           PNG_FORBIDDEN <= set(src_types) and b"iCCP" in src_types, src_types)
    src_body = {t: b for t, b, _o in src_chunks}
    for prof in PROFILES:
        label = "png_full/%s" % _pname(prof)
        res, dst = _clean("png_full", tmp, path, prof)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        out = _read(dst)
        parsed = _png_chunks(out)
        if not _check("%s: результат разбирается как PNG" % label, parsed is not None):
            continue
        chunks, end = parsed
        types = [t for t, _b, _o in chunks]
        body = {t: b for t, b, _o in chunks}
        left = sorted(t.decode() for t in PNG_FORBIDDEN & set(types))
        _check("%s: tEXt, zTXt, iTXt, eXIf, tIME удалены" % label, not left, "остались: %s" % left)
        _check("%s: CRC всех %d чанков результата корректны (пересчитаны binascii.crc32)" % (label, len(chunks)),
               all(o for _t, _b, o in chunks), [t for t, _b, o in chunks if not o])
        _check("%s: IHDR первый, IEND последний" % label,
               types[:1] == [b"IHDR"] and types[-1:] == [b"IEND"], types)
        _check("%s: после IEND в файле ничего нет" % label, end == len(out), (end, len(out)))
        _check("%s: IHDR не изменился (размеры, глубина, тип цвета)" % label,
               body.get(b"IHDR") == src_body[b"IHDR"])
        _check("%s: IDAT совпал побайтно и распаковывается в те же строки пикселей" % label,
               _png_idat(out) == _png_idat(data) and zlib.decompress(_png_idat(out)) == rows)
        _check("%s: gAMA и pHYs (STRUCTURAL) сохранены без изменений" % label,
               body.get(b"gAMA") == src_body[b"gAMA"] and body.get(b"pHYs") == src_body[b"pHYs"], types)
        gone = _leaked(dst, P_TEXTS + EX_TEXTS, _gps_needles("<"))
        _check("%s: значения tEXt, zTXt, iTXt, XMP, EXIF, GPS не лежат ни в байтах, ни в распакованных чанках"
               % label, not gone, gone)
        _oracle(label, path, dst)
        if prof is STEALTH:
            _check("%s: iCCP сохранён байт в байт" % label, body.get(b"iCCP") == src_body[b"iCCP"], types)
        else:
            removed = any(a.act is Act.REMOVED and "ICC" in a.label for a in res.actions)
            _check("%s: iCCP либо сохранён, либо его удаление записано в действиях (молча не пропадает)" % label,
                   b"iCCP" in types or removed, [(a.act.value, a.label) for a in res.actions][:8])
    res, dst = _clean("png_full_keepicc", tmp, path, PARANOID, keep={"icc"})
    if res is not None and _check("png_full_keepicc/paranoid: ok", res.ok, res.errors):
        body = {t: b for t, b, _o in _chunks(_read(dst))}
        _check("keep={'icc'}: iCCP остаётся и в PARANOID байт в байт", body.get(b"iCCP") == src_body[b"iCCP"])
        _check("keep={'icc'}: остальные метаданные (tEXt, eXIf, tIME) всё равно убраны",
               not (PNG_FORBIDDEN & {t for t, _b, _o in _chunks(_read(dst))}))

    # хвост после IEND (классический тайник) выброшен
    data, _rows = _png_bytes(trailer=b"SECRET-TAIL-PAYLOAD-0451")
    path = _write(os.path.join(tmp, "tail.png"), data)
    for prof in PROFILES:
        res, dst = _clean("png_tail", tmp, path, prof)
        if res is not None and res.ok:
            out = _read(dst)
            _check("png_tail/%s: данные после IEND выброшены" % _pname(prof),
                   b"SECRET-TAIL-PAYLOAD" not in out and _png_chunks(out)[1] == len(out))


# ===========================================================================
# 16. JPEG: данные после EOI выброшены; zTXt-бомба в живом PNG не вешает чистку
# ===========================================================================


@_with_tmp
def test_16_image_hidden_payload_and_bomb(tmp):
    path = _write(os.path.join(tmp, "tail.jpg"), _jpeg_bytes(6, "<", tail=b"PK\x03\x04SECRET-TAIL-PAYLOAD-0451"))
    for prof in PROFILES:
        label = "jpeg_tail/%s" % _pname(prof)
        res, dst = _clean("jpeg_tail", tmp, path, prof)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        out = _read(dst)
        _check("%s: данные после EOI (тайник) выброшены, файл кончается на EOI" % label,
               b"SECRET-TAIL-PAYLOAD" not in out and out[-2:] == b"\xff\xd9")
        so, do = _sos_at(_read(path)), _sos_at(out)
        _check("%s: энтропийный блок до EOI бит в бит" % label,
               do is not None and _read(path)[so:so + (len(out) - do)] == out[do:])

    # настоящая zTXt-бомба (200 МБ нулей в ~200 КБ) внутри ВАЛИДНОГО PNG
    path, rows = _live_bomb_png(tmp)
    for prof in PROFILES:
        label = "png_ztxt_bomb_live/%s" % _pname(prof)
        t0 = time.time()
        res, dst = _clean("png_ztxt_bomb_live", tmp, path, prof)
        dt = time.time() - t0
        if res is None:
            continue
        _check("%s: отработало быстро и без раздувания памяти (%.1f с)" % (label, dt), dt < 30, dt)
        if _check("%s: ok -- чанк-бомба вычищен без распаковки" % label, res.ok, res.errors):
            out = _read(dst)
            _check("%s: результат маленький, zTXt нет, пиксели те же" % label,
                   len(out) < 10000 and b"zTXt" not in {t for t, _b, _o in _chunks(out)}
                   and zlib.decompress(_png_idat(out)) == rows, len(out))


# ===========================================================================
# 11. ОТКАЗЫ: форматы без чистильщика -- честное «не почищено» и номер фазы
# ===========================================================================


def _claims_clean(text):
    """Объявляет ли текст отчёта файл чистым. «НЕ ПОЧИЩЕН» -- не объявляет, а
    render_plain переносит строки (textwrap), так что пробелы нормализуем."""
    flat = " ".join(text.split())
    return "ЧИСТО: повторная" in flat or re.search(r"(?<!НЕ )ПОЧИЩЕН", flat) is not None


def _phases(text):
    """Все номера фаз, упомянутые в тексте: «фаза 2», «в фазе 4»."""
    return {int(m) for m in re.findall(r"фаз[аеыу]\s+(\d+)", text)}


# (метка, фабрика, допустимые номера фазы). Легаси OLE2 -- фаза 4: на месте не
# чистится в принципе, история правок вшита в контейнер. ODF и RTF -- фаза 2:
# чистятся на месте, регенерация им не нужна (обоснование в core.clean._PLANNED).
# Номер обязан быть ОДИН и тот же везде, где он назван (см. ниже).
_REFUSED = (
    # docx, docx_wordlike, xlsx, pptx убраны: с фазы 2 они ЧИСТЯТСЯ, а не
    # отказываются. Их поведение проверяется в тестах чистки OOXML ниже.
    ("doc", fixtures.make_doc, (4,)),
    ("doc_active", fixtures.make_doc_active, (4,)),
    ("xls", fixtures.make_xls, (4,)),
    ("ppt", fixtures.make_ppt, (4,)),
    ("rtf", fixtures.make_rtf, (2,)),
    ("odt", fixtures.make_odt, (2,)),
)


def test_14_cleanable_matches_cleaners():
    """cleanreport.CLEANABLE обязан совпадать с тем, что диспетчер реально умеет.

    Это не придирка к дублированию, а защита от молчаливой дыры: кнопки чистки в
    боте показываются по CLEANABLE, а не по core.clean._CLEANERS. Добавить
    чистильщик и забыть про CLEANABLE -- значит получить бота, который
    отказывает в чистке формата, уже умеющего чиститься, и никакой тест формата
    этого не заметит. Ровно так фаза 2 и доехала до бота нерабочей.
    """
    families = set(clean_mod._CLEANERS)
    expected = {fmt for fmt, fam in sniff.FAMILY.items() if fam in families}
    # TIFF/HEIC clean_image отклоняет намеренно (риск испортить файл), поэтому
    # они входят в семейство image, но чистимыми не считаются.
    expected -= set(cleanreport._REFUSED)
    _check("CLEANABLE == форматы семейств, у которых есть чистильщик, минус намеренно "
           "отклонённые: %s" % ", ".join(sorted(expected)),
           set(cleanreport.CLEANABLE) == expected,
           "лишние в CLEANABLE: %s; недостающие: %s"
           % (sorted(set(cleanreport.CLEANABLE) - expected),
              sorted(expected - set(cleanreport.CLEANABLE))))
    # Второй конец той же связи: формат с чистильщиком не имеет права
    # одновременно числиться отказанным с номером фазы.
    both = sorted(set(cleanreport.CLEANABLE) & set(cleanreport._PHASE))
    _check("формат не может быть и чистимым, и ожидающим фазы", not both, both)
    planned = sorted(f for f in cleanreport.CLEANABLE if sniff.FAMILY.get(f) in clean_mod._PLANNED)
    _check("чистимый формат не стоит в таблице отказов диспетчера _PLANNED", not planned, planned)


def test_15_customxml_namespaces_agree():
    """Инспектор и чистильщик обязаны одинаково понимать корни customXml.

    Инспектор -- оракул: что он относит к ENVIRONMENT, то чистильщик обязан
    убрать. Корень, который инспектор считает утечкой SharePoint, а чистильщик
    в своём списке не имеет, остаётся в файле -- и правильно почищенный файл
    получает вердикт «почищено не полностью» на ровном месте. Списки лежат в
    двух модулях (инспектор не имеет права зависеть от чистильщика), поэтому их
    совпадение проверяется здесь, а не достигается общим импортом.
    """
    insp_sp = set(insp_ooxml._CX_SHAREPOINT_NS)
    clean_sp = set(clean_ooxml._SP_ROOT_NS)
    _check("список корней SharePoint у инспектора и чистильщика совпадает",
           insp_sp == clean_sp,
           "только у инспектора: %s; только у чистильщика: %s"
           % (sorted(insp_sp - clean_sp), sorted(clean_sp - insp_sp)))
    insp_c = set(insp_ooxml._CX_CONTENT_NS)
    clean_c = set(clean_ooxml._CONTENT_ROOT_NS)
    _check("список корней-содержимого у инспектора и чистильщика совпадает",
           insp_c == clean_c,
           "только у инспектора: %s; только у чистильщика: %s"
           % (sorted(insp_c - clean_c), sorted(clean_c - insp_c)))
    _check("корень не может быть одновременно утечкой SharePoint и содержимым",
           not (insp_sp & insp_c), sorted(insp_sp & insp_c))


@_with_tmp
def test_11_refusals_unsupported_formats(tmp):
    for name, maker, phases in _REFUSED:
        d = os.path.join(tmp, name)
        os.makedirs(d)
        path, _exp = maker(d)
        _check("%s: sniff опознал формат файла (%s) -- отказ вызван отсутствием чистильщика, "
               "а не мусором" % (name, sniff.sniff(path)), sniff.sniff(path) not in ("unknown", "unreadable"))
        for prof in PROFILES:
            label = "%s/%s" % (name, _pname(prof))
            res, dst = _clean(name, d, path, prof)
            if res is None:
                continue
            msg = " ".join(res.errors)
            _check("%s: ok=False" % label, not res.ok)
            _check("%s: выходной файл НЕ создан" % label, not os.path.exists(dst))
            _check("%s: в тексте ошибки сказано, что файл НЕ очищен" % label, "НЕ очищен" in msg, msg)
            named = _phases(msg)
            _check("%s: в тексте ошибки назван номер фазы, допустимый для формата %s" % (label, phases),
                   len(named) == 1 and named <= set(phases), "названо: %s; %s" % (sorted(named), msg))
            _check("%s: planned_phase совпадает с названной в тексте фазой" % label,
                   res.planned_phase > 0 and {res.planned_phase} == named, (res.planned_phase, named))
            _check("%s: чистота не заявлена -- clean=False, не verified, critical_after=-1, действий нет" % label,
                   not res.clean and not res.verified and res.critical_after == -1 and not res.actions)
            _check("%s: в ошибках нет ни пути каталога, ни имени файла" % label,
                   d not in msg and os.path.basename(path) not in msg, msg)
        # существующий dst при отказе не затирается
        sent = os.path.join(d, "sentinel.out")
        _write(sent, b"old")
        res = clean_file(path, sent)
        _check("%s: отказ не трогает уже существующий выходной файл" % name,
               not res.ok and _read(sent) == b"old")

        # отчёт для пользователя не обещает чистоты и называет ту же фазу
        res, _dst = _clean(name + "_report", d, path, STEALTH)
        if res is None:
            continue
        plain = cleanreport.render_plain(res)
        tg = "\n".join(cleanreport.render_telegram(res))
        summ = cleanreport.summary_line(res)
        _check("%s: отчёт (текст и Telegram) говорит «ФАЙЛ НЕ ПОЧИЩЕН»" % name,
               "ФАЙЛ НЕ ПОЧИЩЕН" in plain and "ФАЙЛ НЕ ПОЧИЩЕН" in tg)
        _check("%s: сводка начинается с «НЕ почищен»" % name, summ.startswith("НЕ почищен"), summ)
        _check("%s: отчёт нигде не называет файл чистым" % name,
               not _claims_clean(plain) and not _claims_clean(tg))
        # Предусловие равенства ниже: файл ОТКАЗАН, и номер фазы в отчёте --
        # причина отказа. У почищенного файла planned_phase == 0, а фаза в его
        # отчёте называется информационно (предупреждение про Regenerate как
        # путь к незаметности), и равенство к нему не применимо.
        _check("%s: предусловие -- файл отказан и у отказа есть номер фазы" % name,
               not res.ok and res.planned_phase > 0, (res.ok, res.planned_phase))
        ph = _phases(plain) | _phases(tg)
        _check("%s: отчёт cleanreport и диспетчер называют ОДНУ И ТУ ЖЕ фазу (пользователь видит оба текста)" % name,
               ph == {res.planned_phase}, "фазы в отчёте: %s, planned_phase=%s" % (sorted(ph), res.planned_phase))

    # профиль REGENERATE (фаза 4) для PDF не создаёт файл
    path = fixtures.make_pdf(tmp)[0]
    dst = os.path.join(tmp, "regen.pdf")
    res = clean_file(path, dst, Profile.REGENERATE)
    _check("REGENERATE: ok=False, фаза 4 названа, файла нет",
           not res.ok and res.planned_phase == 4 and not os.path.exists(dst) and 4 in _phases(" ".join(res.errors)),
           res.errors)
    res = clean_file(path, dst, "no-such-profile")
    _check("неизвестный профиль: ok=False, файла нет", not res.ok and not os.path.exists(dst), res.errors)


# ===========================================================================
# 12. Защита от затирания входа
# ===========================================================================


@_with_tmp
def test_12_no_overwrite_of_input(tmp):
    for name, maker in (("pdf", fixtures.make_pdf), ("jpeg", fixtures.make_jpeg),
                        ("png", fixtures.make_png), ("docx", fixtures.make_docx)):
        d = os.path.join(tmp, name)
        os.makedirs(d)
        path, _exp = maker(d)
        base = os.path.basename(path)
        variants = [("тот же путь", path), ("через «.»", os.path.join(d, ".", base)),
                    ("через «x/..»", os.path.join(d, "x", "..", base))]
        if os.name == "nt":
            variants.append(("другой регистр", os.path.join(d, base.upper())))
        link = os.path.join(d, "hard_" + base)
        linked = False
        try:
            os.link(path, link)
            linked = True
            variants.append(("жёсткая ссылка", link))
        except (OSError, NotImplementedError, AttributeError):
            pass
        before, listing = _sha256(path), sorted(os.listdir(d))
        for vname, dst in variants:
            label = "%s: clean_file(src, %s)" % (name, vname)
            try:
                res = clean_file(path, dst)
            except Exception as exc:  # noqa: BLE001
                _check("%s не бросил исключение" % label, False, "%s: %s" % (type(exc).__name__, exc))
                continue
            _SEEN.append(("%s/overwrite/%s" % (name, vname), res))
            _check("%s -> ok=False" % label, not res.ok, res.errors)
            _check("%s: src не изменён (sha256)" % label, _sha256(path) == before)
            if vname == "жёсткая ссылка":
                _check("%s: и ссылка (та же inode) не изменена" % label, _sha256(link) == before)
        _check("%s: в каталоге не появилось и не осталось лишних файлов" % name,
               sorted(os.listdir(d)) == listing, sorted(os.listdir(d)))
        # сами чистильщики, в обход диспетчера: вход цел при любом dst
        if name == "pdf":
            r = clean_pdf(path, path)
            _check("pdf: clean_pdf(src, src) -> ok=False", not r.ok, r.errors)
            if linked:
                clean_pdf(path, link)
        elif name in ("jpeg", "png"):
            r = clean_image(path, path)
            _check("%s: clean_image(src, src) -> ok=False" % name, not r.ok, r.errors)
            if linked:
                r = clean_image(path, link)
                _check("%s: clean_image(src, жёсткая ссылка на src) -> ok=False" % name, not r.ok, r.errors)
        _check("%s: после вызовов чистильщиков напрямую src не изменён" % name, _sha256(path) == before)


# ===========================================================================
# 13. ПАТОЛОГИИ: ни одного исключения наружу, вход цел, файла нет
# ===========================================================================


def _no_leftovers(dirpath):
    if not os.path.isdir(dirpath):
        return []
    return [n for n in os.listdir(dirpath) if n.startswith(".clean_") or n.endswith(".part")]


@_with_tmp
def test_13_pathologies(tmp):
    cases = dict(fixtures.make_broken(tmp))
    pdf = _read(fixtures.make_pdf(tmp)[0])
    cases["pdf_truncated_half"] = _write(os.path.join(tmp, "pdf_half.pdf"), pdf[:len(pdf) // 2])
    cases["pdf_truncated_head8"] = _write(os.path.join(tmp, "pdf_head8.pdf"), pdf[:8])
    cases["pdf_truncated_tail20"] = _write(os.path.join(tmp, "pdf_tail20.pdf"), pdf[:-20])
    cases["pdf_junk_after_magic"] = _write(os.path.join(tmp, "pdf_junk2.pdf"), b"%PDF-1.7\n" + _rnd(600, "pdf"))
    png = _png_full()[0]
    for ctype in (b"IHDR", b"IDAT", b"tEXt", b"IEND"):
        cases["png_bad_crc_" + ctype.decode()] = _write(
            os.path.join(tmp, "png_crc_%s.png" % ctype.decode()), _corrupt_crc(png, ctype))
    cases["png_junk_after_magic"] = _write(os.path.join(tmp, "png_junk.png"), PNG_SIG + _rnd(400, "png"))
    cases["jpeg_junk_after_magic"] = _write(os.path.join(tmp, "jpg_junk.jpg"), b"\xff\xd8\xff" + _rnd(500, "jpg"))
    jpg = _jpeg_bytes(6)
    cases["jpeg_cut_before_sos"] = _write(os.path.join(tmp, "jpg_cut.jpg"), jpg[:_sos_at(jpg) - 5])
    must = {"empty", "pdf_header_junk", "pdf_truncated_half", "pdf_truncated_head8", "pdf_truncated_tail20",
            "pdf_junk_after_magic", "png_ztxt_bomb", "png_junk_after_magic", "jpeg_junk_after_magic",
            "jpeg_cut_before_sos", "jpeg_truncated", "zip_bad_entry", "zip_bomb"} | {
        n for n in cases if n.startswith("png_bad_crc_")}
    _check("набор патологий собран: пустой файл, мусор с магией, обрезанный PDF, битые CRC, zTXt-бомба",
           {"empty", "pdf_header_junk", "pdf_truncated_half", "png_bad_crc_IDAT", "png_ztxt_bomb"} <= set(cases),
           sorted(cases))
    for name, path in sorted(cases.items()):
        out_dir = os.path.join(tmp, "out_" + name)
        dst = os.path.join(out_dir, "result" + os.path.splitext(path)[1])
        before = _sha256(path)
        t0 = time.time()
        try:
            res = clean_file(path, dst)
        except Exception as exc:  # noqa: BLE001 -- именно это и проверяем
            _check("%s: исключение НЕ вылетело наружу" % name, False, "%s: %s" % (type(exc).__name__, exc))
            continue
        dt = time.time() - t0
        _SEEN.append(("pathology/" + name, res))
        _check("%s: вернулся DispatchResult, быстро (%.1f с)" % (name, dt), isinstance(res, DispatchResult) and dt < 30, dt)
        _check("%s: вход не изменён (sha256)" % name, _sha256(path) == before)
        if name in must:
            _check("%s: ok=False" % name, not res.ok, "ok=True, errors=%s" % res.errors)
            _check("%s: errors непусты -- причина названа" % name, bool(res.errors))
        if not res.ok:
            msg = " ".join(res.errors)
            _check("%s: выходной файл НЕ создан" % name, not os.path.exists(dst))
            _check("%s: временных файлов (.clean_*, .part) не осталось" % name, not _no_leftovers(out_dir),
                   _no_leftovers(out_dir))
            _check("%s: в ошибках нет пути каталога и имени файла" % name,
                   tmp not in msg and os.path.basename(path) not in msg, msg)
            _check("%s: чистота не заявлена, действий нет" % name,
                   not res.clean and res.critical_after == -1 and not res.actions)
            sent = os.path.join(tmp, "sentinel_" + name)
            _write(sent, b"old")
            r2 = clean_file(path, sent)
            _check("%s: существующий выходной файл при отказе не тронут" % name,
                   not r2.ok and _read(sent) == b"old")
        else:
            _check("%s: файл выдан -- dst существует и результат подтверждён повторной инспекцией" % name,
                   os.path.isfile(dst) and res.verified, res.errors)

    # свип: любая одиночная порча PNG (бит-флип в любом месте или обрыв) -> отказ, а не тихо испорченный выход
    png = _png_bytes(pre=[fixtures._png_chunk(b"tEXt", b"Author\0" + P_AUTHOR.encode("latin-1"))])[0]
    problems = []
    runs = 0
    for i in list(range(0, len(png), 5)) + [len(png) - 1]:
        bad = png[:i] + bytes([png[i] ^ 0x55]) + png[i + 1:]
        src = _write(os.path.join(tmp, "flip.png"), bad)
        dst = os.path.join(tmp, "flip_out", "o.png")
        try:
            res = clean_file(src, dst)
        except Exception as exc:  # noqa: BLE001
            problems.append("флип %d: исключение %s" % (i, type(exc).__name__))
            continue
        runs += 1
        if res.ok or os.path.exists(dst) or _sha256(src) != hashlib.sha256(bad).hexdigest():
            problems.append("флип %d: ok=%s dst=%s" % (i, res.ok, os.path.exists(dst)))
    for cut in range(1, len(png), 13):
        src = _write(os.path.join(tmp, "cut.png"), png[:cut])
        dst = os.path.join(tmp, "cut_out", "o.png")
        try:
            res = clean_file(src, dst)
        except Exception as exc:  # noqa: BLE001
            problems.append("обрыв %d: исключение %s" % (cut, type(exc).__name__))
            continue
        runs += 1
        if res.ok or os.path.exists(dst):
            problems.append("обрыв %d: ok=%s dst=%s" % (cut, res.ok, os.path.exists(dst)))
    _check("PNG: все %d одиночных порч (бит-флип, обрыв) отклонены, исключений нет, файла нет" % runs,
           not problems, problems[:5])

    # свип: обрыв PDF в любой точке -> либо отказ, либо полностью валидный результат с одним %%EOF
    problems, runs = [], 0
    for cut in sorted(set(list(range(9, len(pdf), 37)) + list(range(len(pdf) - 30, len(pdf))))):
        src = _write(os.path.join(tmp, "cutp.pdf"), pdf[:cut])
        dst = os.path.join(tmp, "cutp_out", "o.pdf")
        if os.path.exists(dst):
            os.remove(dst)
        try:
            res = clean_file(src, dst)
        except Exception as exc:  # noqa: BLE001
            problems.append("обрыв %d: исключение %s" % (cut, type(exc).__name__))
            continue
        runs += 1
        if not res.ok:
            if os.path.exists(dst):
                problems.append("обрыв %d: отказ, но файл создан" % cut)
            continue
        try:
            with pikepdf.open(dst) as q:
                good = len(q.pages) == 1 and _read(dst).count(b"%%EOF") == 1
        except Exception:  # noqa: BLE001
            good = False
        if not good:
            problems.append("обрыв %d: ok=True, но результат невалиден" % cut)
    _check("PDF: на всех %d точках обрыва -- отказ либо полностью валидный результат" % runs,
           not problems, problems[:5])


# ===========================================================================
# 15. Честность вердиктов: проверка не верит чистильщику на слово
# ===========================================================================


@_with_tmp
def test_15_honest_verdicts(tmp):
    path = fixtures.make_pdf(tmp)[0]

    # verify=False: файл выдан, но чистоту утверждать нельзя
    res, dst = _clean("pdf_noverify", tmp, path, STEALTH, verify=False)
    if res is not None:
        text = cleanreport.render_plain(res) + "\n".join(cleanreport.render_telegram(res))
        _check("verify=False: ok, но clean=False, verified=False, critical_after=-1",
               res.ok and not res.clean and not res.verified and res.critical_after == -1)
        _check("verify=False: отчёт говорит «РЕЗУЛЬТАТ НЕ ПРОВЕРЕН» и не объявляет файл чистым",
               "НЕ ПРОВЕРЕН" in text and "ЧИСТО: повторная" not in text)

    # keep: оставленное по просьбе значение -- не скрываем, а показываем
    res, dst = _clean("pdf_keep", tmp, path, STEALTH, keep={"/Author"})
    if res is not None and _check("keep: ok и verified", res.ok and res.verified, res.errors):
        _check("keep: /Author физически остался в файле (просьба выполнена)", bool(_leaked(dst, [fixtures.PDF_AUTHOR])))
        _check("keep: диспетчер НЕ объявляет файл чистым и называет оставшееся значение",
               not res.clean and any(fixtures.PDF_AUTHOR in sv.value for sv in res.survived),
               [(s.label, s.value) for s in res.survived])
        _check("keep: отчёт не называет файл чистым",
               "ЧИСТО: повторная" not in cleanreport.render_plain(res))

    # подмена чистильщиков -- документированный шов core.clean._CLEANERS
    saved = dict(clean_mod._CLEANERS)
    try:
        def liar(s, d, prof, keep, force):          # копирует файл как есть и клянётся, что чисто
            shutil.copyfile(s, d)
            r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
            r.ok = True
            r.act(Act.REMOVED, "/Info", "/Author", "-", "")
            return r

        clean_mod._CLEANERS["pdf"] = liar
        res, dst = _clean("pdf_liar", tmp, path, STEALTH)
        if res is not None:
            text = cleanreport.render_plain(res) + "\n".join(cleanreport.render_telegram(res))
            _check("чистильщик-лжец: утечка не убрана -- survived не пуст, clean=False",
                   res.ok and res.verified and bool(res.survived) and not res.clean, res.survived)
            _check("чистильщик-лжец: отчёт не называет файл чистым", "ЧИСТО: повторная" not in text)

        def boom(s, d, prof, keep, force):          # пишет половину файла и падает
            with open(d, "wb") as fh:
                fh.write(b"%PDF-1.4 half")
            raise RuntimeError("взрыв")

        clean_mod._CLEANERS["pdf"] = boom
        res, dst = _clean("pdf_boom", tmp, path, STEALTH)
        if res is not None:
            _check("исключение чистильщика: ok=False, файла нет, мусора нет",
                   not res.ok and not os.path.exists(dst) and not _no_leftovers(os.path.dirname(dst)),
                   (res.errors, os.listdir(os.path.dirname(dst)) if os.path.isdir(os.path.dirname(dst)) else None))

        png_dir = os.path.join(tmp, "png_for_swap")
        os.makedirs(png_dir)
        png_path = fixtures.make_png(png_dir)[0]

        def other_format(s, d, prof, keep, force):  # «чистка» выдала не PDF
            shutil.copyfile(png_path, d)
            r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
            r.ok = True
            return r

        clean_mod._CLEANERS["pdf"] = other_format
        res, dst = _clean("pdf_other_format", tmp, path, STEALTH)
        if res is not None:
            _check("результат другого формата не выдаётся: ok=False, файла нет",
                   not res.ok and not os.path.exists(dst), res.errors)
    finally:
        clean_mod._CLEANERS.clear()
        clean_mod._CLEANERS.update(saved)
    _check("швы: core.clean._CLEANERS восстановлен после подмен", clean_mod._CLEANERS == saved)


# ===========================================================================
# 97. То, что реально печатает CLI: итог диспетчера не противоречит своим же цифрам
# ===========================================================================


@_with_tmp
def test_97_cli_verdict_text(tmp):
    """python -m core.clean печатает итог диспетчера СВЕРХУ и отчёт cleanreport ниже.
    Пользователь читает именно этот текст, поэтому итог не имеет права говорить
    «часть исходных значений осталась», когда в той же распечатке «пережившихся
    чистку: 0» (случай: инспектор не смог разобрать часть исходника -- доказательство
    неполное, но утечки никто не видел)."""
    import contextlib

    def run_cli(src, extra=()):
        out = os.path.join(tmp, "cli_out", os.path.basename(src))
        buf = io.StringIO()
        before = _sha256(src)
        try:
            with contextlib.redirect_stdout(buf):
                rc = clean_mod.main([src, "--out", out, "--overwrite"] + list(extra))
        except SystemExit as exc:     # argparse
            rc = exc.code
        _check("CLI %s: вход не изменён (sha256)" % os.path.basename(src), _sha256(src) == before)
        return rc, buf.getvalue(), out

    bomb, _rows = _live_bomb_png(tmp)
    rc, text, out = run_cli(bomb)
    survived_zero = "пережившихся чистку: 0" in text
    _check("CLI, zTXt-бомба: файл выдан, чистота не доказана -> код возврата 2", rc == 2 and os.path.isfile(out), rc)
    _check("CLI, zTXt-бомба: в распечатке есть «пережившихся чистку: 0» (предусловие)", survived_zero, text[:400])
    _check("CLI, zTXt-бомба: итог не утверждает «часть исходных значений осталась» при нуле выживших",
           not (survived_zero and "часть исходных значений осталась" in text), text[:300])

    path = fixtures.make_pdf(tmp)[0]
    rc, text, out = run_cli(path)
    _check("CLI, обычный PDF: код возврата 0 и итог «ПОЧИЩЕНО: ни одно исходное значение»",
           rc == 0 and "ПОЧИЩЕНО: ни одно исходное значение" in text and "НЕ ПОЛНОСТЬЮ" not in text, (rc, text[:200]))

    # С фазы 2 docx чистится. Отказ с номером фазы остаётся у форматов,
    # чистки которых ещё нет: легаси OLE2 (фаза 4), ODF и RTF.
    rc, text, out = run_cli(fixtures.make_docx(tmp)[0])
    _check("CLI, docx: код возврата 0, файл выдан, «ПОЧИЩЕНО»",
           rc == 0 and os.path.isfile(out) and "ПОЧИЩЕНО" in text and "НЕ ПОЧИЩЕНО" not in text,
           (rc, text[:300]))

    rc, text, out = run_cli(fixtures.make_doc(tmp)[0])
    _check("CLI, legacy .doc: код возврата 1, файла нет, «НЕ ПОЧИЩЕНО», названа фаза 4",
           rc == 1 and not os.path.exists(out) and "НЕ ПОЧИЩЕНО" in text and 4 in _phases(text),
           (rc, text[:300]))


# ===========================================================================
# 20. Фаза 2: чистка OOXML
# ===========================================================================

_WORD_EPOCH = (1980, 1, 1, 0, 0, 0)   # что Office ставит всем записям ZIP


@_with_tmp
def test_20_ooxml_cleaning(tmp):
    """Фаза 2: чистка docx/xlsx/pptx поверх байт-точной пересборки ZIP.

    Заменяет проверки, которые раньше утверждали, что эти форматы ОТКАЗЫВАЮТ.
    Главное здесь -- не только «утечек не осталось», но и «пакет остался
    пакетом Office»: порядок записей, DOS-таймстемпы, Application. Наивная
    пересборка их ломает, и файл Word превращается в файл-от-библиотеки,
    который наш же инспектор опознаёт сигналом ai.
    """
    import zipfile

    for maker, name in ((fixtures.make_docx, "docx"),
                        (fixtures.make_docx_wordlike, "docx_wordlike"),
                        (fixtures.make_xlsx, "xlsx"),
                        (fixtures.make_pptx, "pptx")):
        src, _exp = maker(tmp)
        src_ts = {i.filename: i.date_time for i in zipfile.ZipFile(src).infolist()}
        src_order = [i.filename for i in zipfile.ZipFile(src).infolist()]
        src_had_app = bool([f for f in inspect_file(src).findings
                            if "Application" in (f.label or "")])

        for profile in (STEALTH, PARANOID):
            tag = "%s/%s" % (name, _pname(profile))
            res, dst = _clean(name, tmp, src, profile)
            if res is None:
                continue
            _check("%s: файл выдан и признан чистым" % tag,
                   res.ok and res.clean, (res.ok, res.errors[:1]))
            _check("%s: ни одно исходное значение не выжило" % tag,
                   not res.survived, [s_.value for s_ in res.survived][:3])

            if not os.path.isfile(dst):
                continue
            with zipfile.ZipFile(dst) as z:
                _check("%s: результат открывается, testzip проходит" % tag, z.testzip() is None)
                out_order = [i.filename for i in z.infolist()]
                out_ts = {i.filename: i.date_time for i in z.infolist()}
            _check("%s: [Content_Types].xml остался первой записью" % tag,
                   out_order and out_order[0] == "[Content_Types].xml", out_order[:2])
            kept = [n for n in src_order if n in set(out_order)]
            _check("%s: порядок уцелевших записей не изменился" % tag, out_order == kept,
                   (out_order[:4], kept[:4]))
            # Таймстемпы ZIP -- канал утечки сам по себе (docs/METADATA.md): Word
            # ставит всем записям 1980-01-01, библиотеки -- реальное время
            # сохранения. Поэтому «сохранить как было» верно ТОЛЬКО для пакета от
            # Word; у пакета с реальным временем сохранять его означало бы оставить
            # утечку, и чистильщик обязан привести всё к 1980-01-01. Запрещено
            # ровно одно: вынести реальное время в результат.
            real_out = [n for n, t in out_ts.items() if tuple(t) != _WORD_EPOCH]
            if all(tuple(t) == _WORD_EPOCH for t in src_ts.values()):
                bad_ts = [n for n in out_ts if n in src_ts and out_ts[n] != src_ts[n]]
                _check("%s: пакет от Word -- DOS-таймстемпы записей не тронуты" % tag,
                       not bad_ts, bad_ts[:3])
            else:
                _check("%s: реальное время записей приведено к 1980-01-01 (иначе время "
                       "сохранения утекает через контейнер)" % tag, not real_out, real_out[:3])
            _check("%s: реального времени сохранения в таймстемпах результата нет" % tag,
                   not real_out, real_out[:3])

            after = inspect_file(dst)
            app = [f.value for f in after.findings if "Application" in (f.label or "")]
            # Если в исходнике Application не было (xlsx-фикстура идёт без
            # docProps/app.xml), сохранять нечего и появиться он не имеет права:
            # дописанный Application -- подделка провенанса.
            if profile is STEALTH:
                _check("%s: Application сохранён как есть -- это правда о файле "
                       "(в исходнике %s)" % (tag, "был" if src_had_app else "не было"),
                       bool(app) == src_had_app, app)
            # Проверять «части больше нет» имеет смысл только если она БЫЛА:
            # иначе утверждение выполняется вакуумно и ничего не охраняет.
            # people.xml есть у make_docx, printerSettings -- только у make_pptx.
            if "word/people.xml" in src_order:
                _check("%s: word/people.xml с userId не осталось" % tag,
                       "word/people.xml" not in out_order)
            src_prn = [n for n in src_order if "printerSettings" in n]
            if src_prn:
                _check("%s: printerSettings с именем принтера (было %d шт.) не осталось"
                       % (tag, len(src_prn)),
                       not [n for n in out_order if "printerSettings" in n], out_order)
                raw = _read(dst)
                _check("%s: имя принтера не осталось и в сырых байтах результата" % tag,
                       fixtures.PPTX_PRINTER.encode("utf-16le") not in raw
                       and fixtures.PPTX_PRINTER.encode("utf-8") not in raw)

        # Честность про незаметность: для OOXML stealth файл опознаётся как
        # чищеный, и отчёт ОБЯЗАН это сказать. Пустые dc:creator и
        # cp:lastModifiedBy вместе -- сами по себе признак; это предел формата,
        # а не дефект, см. docs/ROADMAP.md.
        res, dst = _clean(name + "_warn", tmp, src, STEALTH)
        if res is None:
            continue
        kinds = [k for k, _d in getattr(res, "new_signals", [])]
        if kinds:
            plain = cleanreport.render_plain(res)
            tg = "\n".join(cleanreport.render_telegram(res))
            _check("%s: отчёт предупреждает, что файл опознаётся как чищеный" % name,
                   "ОПОЗНАЁТСЯ КАК ЧИЩЕНЫЙ" in plain and "ОПОЗНАЁТСЯ КАК ЧИЩЕНЫЙ" in tg,
                   plain[:200])
            _check("%s: предупреждение называет Regenerate как решение" % name,
                   "Regenerate" in plain, plain[:200])


# 98. Вердикты диспетчера и отчёта не противоречат друг другу
# ===========================================================================


def test_98_verdicts_agree():
    """Пользователь видит оба текста (CLI печатает вердикт диспетчера сверху и
    отчёт cleanreport ниже). Они не имеют права расходиться ни в одну сторону."""
    seen = [(l, r) for l, r in _SEEN if isinstance(r, DispatchResult)]
    _check("накоплено результатов для сверки: %d" % len(seen), len(seen) > 50, len(seen))
    false_alarm = [l for l, r in seen if r.ok and r.clean and "НЕ ЧИСТО" in cleanreport.render_plain(r)]
    _check("файл ДОКАЗАННО чист (диспетчер) -- отчёт не кричит «НЕ ЧИСТО» (ложная тревога)",
           not false_alarm, false_alarm[:6])
    overclaim = [l for l, r in seen if r.ok and not r.clean and "ЧИСТО: повторная" in cleanreport.render_plain(r)]
    _check("чистота НЕ доказана (диспетчер) -- отчёт не называет файл чистым", not overclaim, overclaim[:6])
    refused_ok = [l for l, r in seen if not r.ok and _claims_clean(cleanreport.render_plain(r))]
    _check("файл НЕ выдан -- отчёт нигде не называет его чистым", not refused_ok, refused_ok[:6])


# ===========================================================================
# 99. РЕНДЕР: лимит Telegram, теги, экранирование (идёт ПОСЛЕДНИМ)
# ===========================================================================

_TG_TAGS = ("b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler")
_TG_LIMIT = 4096


class _TagCheck(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack, self.bad = [], []

    def handle_starttag(self, tag, attrs):
        if tag in _TG_TAGS:
            self.stack.append(tag)
        else:
            self.bad.append("<%s>" % tag)

    def handle_startendtag(self, tag, attrs):
        self.bad.append("<%s/>" % tag)

    def handle_endtag(self, tag):
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.bad.append("</%s>" % tag)


def _html_problems(msg):
    """Что не так с сообщением Telegram HTML: чужие теги, несбалансированные теги,
    голые <, > и & в тексте."""
    p = _TagCheck()
    try:
        p.feed(msg)
        p.close()
    except Exception as exc:  # noqa: BLE001
        return ["парсер: %s" % exc]
    bad = list(p.bad) + ["не закрыт <%s>" % t for t in p.stack]
    text = re.sub(r"</?(?:%s)>" % "|".join(_TG_TAGS), "", msg)
    if "<" in text or ">" in text:
        bad.append("голые < или > в тексте")
    if re.search(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)", text):
        bad.append("голый &")
    return bad


def _render_problems(res, hostile=()):
    """Проблемы Telegram-рендера одного результата. hostile -- строки, которых в
    сыром виде в сообщениях быть не должно."""
    try:
        msgs = cleanreport.render_telegram(res)
    except Exception as exc:  # noqa: BLE001
        return ["render_telegram бросил %s: %s" % (type(exc).__name__, exc)], []
    out = []
    if not msgs or not all(isinstance(m, str) and m for m in msgs):
        out.append("пустой список или пустое сообщение")
    for i, m in enumerate(msgs):
        n = _u16(m)
        if n > _TG_LIMIT:
            out.append("сообщение %d: %d UTF-16 units > %d" % (i + 1, n, _TG_LIMIT))
        out += ["сообщение %d: %s" % (i + 1, p) for p in _html_problems(m)]
        out += ["сообщение %d: сырое %r" % (i + 1, h) for h in hostile if h in m]
    return out, msgs


def _hostile_result(n_each=1, value="<script>&", ok=True):
    r = CleanResult(src="x", dst="y", fmt="pdf", profile="stealth", ok=ok)
    for _ in range(n_each):
        for act in Act:
            r.act(act, "loc " + value, "label " + value, "before " + value, "after " + value, "note " + value)
    r.errors.append("ошибка " + value)
    r.critical_before, r.critical_after, r.sensitive_before, r.sensitive_after = 5, 0, 7, 3
    return r


@_with_tmp
def test_99_render(tmp):
    # --- 1. каждый результат чистки, накопленный всеми тестами ---
    problems, privacy, plain_bad, n_msgs, n_multi = [], [], [], 0, 0
    for label, res in _SEEN:
        probs, msgs = _render_problems(res, hostile=("<script",))
        n_msgs += len(msgs)
        n_multi += len(msgs) > 1
        problems += ["%s: %s" % (label, p) for p in probs]
        text = "\n".join(msgs)
        for secret in {os.path.basename(str(res.src)), os.path.basename(str(res.dst))} - {""}:
            if len(secret) > 6 and secret in text:
                privacy.append("%s: в отчёт попало имя файла" % label)
        try:
            plain, line = cleanreport.render_plain(res), cleanreport.summary_line(res)
            if not (isinstance(plain, str) and plain and isinstance(line, str) and line and "\n" not in line):
                plain_bad.append(label)
        except Exception as exc:  # noqa: BLE001
            plain_bad.append("%s: %s" % (label, type(exc).__name__))
    _check("render_telegram на всех %d результатах (%d сообщений): каждое <= %d UTF-16 units, теги "
           "сбалансированы и разрешены, голых <, >, & нет" % (len(_SEEN), n_msgs, _TG_LIMIT),
           not problems and len(_SEEN) > 50, problems[:5] or "накоплено мало результатов: %d" % len(_SEEN))
    _check("render_plain и summary_line на всех результатах: непустые, сводка в одну строку",
           not plain_bad, plain_bad[:5])
    _check("приватность: имена файлов не попали ни в одно сообщение отчёта", not privacy, privacy[:5])

    # --- 2. реальный PDF со значением «<script>&» в Author и XMP ---
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:creator"] = ["<script>&"]
    pdf.docinfo["/Author"] = "<script>&"
    pdf.docinfo["/Title"] = "<b>тег</b> & <i>курсив"
    pdf.docinfo["/Producer"] = "Gen <script>&"
    path = os.path.join(tmp, "xss.pdf")
    pdf.save(path)
    pdf.close()
    for prof in PROFILES:
        label = "xss_pdf/%s" % _pname(prof)
        res, _dst = _clean("xss_pdf", tmp, path, prof)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        probs, msgs = _render_problems(res, hostile=("<script", "<b>тег"))
        text = "\n".join(msgs)
        _check("%s: теги и длина сообщений в порядке, сырого <script> нет" % label, not probs, probs[:4])
        _check("%s: удалённое значение показано ЭКРАНИРОВАННЫМ: &lt;script&gt;&amp;" % label,
               "&lt;script&gt;&amp;" in text, text[:300])

    # --- 3. враждебный результат, собранный руками: «<script>&» во ВСЕХ полях ---
    res = _hostile_result()
    res.fmt, res.profile = "<script>&", "<b>&"
    res.errors.append("C:\\Users\\ivan\\secret.docx: <script>&")
    probs, msgs = _render_problems(res, hostile=("<script",))
    text = "\n".join(msgs)
    _check("враждебные поля: теги сбалансированы, сообщения в лимите, сырого <script> нет", not probs, probs[:4])
    _check("враждебные поля: значение корректно экранировано (&lt;script&gt;&amp;)", "&lt;script&gt;&amp;" in text)
    _check("враждебные поля: путь из текста ошибки вырезан (пользователь не получит путь нашей машины)",
           "ivan" not in text and "secret.docx" not in text, text[-300:])

    # --- 4. объём: сотни записей, утроенное экранирование, суррогатные пары ---
    big = CleanResult(src="x", dst="y", fmt="pdf", profile="stealth", ok=True)
    for i in range(300):
        big.act(Act.REMOVED, "loc %d" % i, "Поле %d" % i, "&" * 400, "<" * 400, "n" * 400)
    big.critical_before, big.critical_after = 5, 0
    probs, msgs = _render_problems(big)
    _check("300 записей из одних '&' и '<': несколько сообщений, каждое <= 4096, теги сбалансированы",
           not probs and len(msgs) > 1, probs[:3] or "сообщений: %d" % len(msgs))
    _check("при нарезке у сообщений есть нумерация частей [i/n]",
           len(msgs) > 1 and all(m.startswith("<b>[%d/%d]</b>" % (i + 1, len(msgs))) for i, m in enumerate(msgs)),
           [m[:20] for m in msgs[:3]])
    emoji = CleanResult(src="x", dst="y", fmt="pdf", profile="stealth", ok=True)
    for i in range(80):
        emoji.act(Act.REMOVED, "loc", "Поле %d \U0001F600" % i, "\U0001F600" * 190, "", "\U0001F600" * 150)
    emoji.critical_before, emoji.critical_after = 5, 0
    probs, msgs = _render_problems(emoji)
    _check("эмодзи (2 UTF-16 unit на символ): каждое сообщение <= 4096 UTF-16 units, а не символов",
           not probs and len(msgs) > 1, probs[:3])
    _check("эмодзи: проверка не вакуумна -- UTF-16 длина сообщений заметно больше числа символов",
           len(msgs) > 1 and sum(_u16(m) for m in msgs) > 1.3 * sum(len(m) for m in msgs),
           (sum(_u16(m) for m in msgs), sum(len(m) for m in msgs)))

    # --- 5. не результат вовсе: бот не должен упасть и не должен соврать ---
    for junk in (None, object(), CleanResult(src="x", dst="y", fmt="", profile="")):
        probs, msgs = _render_problems(junk)
        _check("render_telegram(%s): не падает, сообщение есть, «НЕ ПОЧИЩЕН»" % type(junk).__name__,
               not probs and "НЕ ПОЧИЩЕН" in "\n".join(msgs), probs[:3] or msgs[:1])


# ---------------------------------------------------------------------------
# запуск
# ---------------------------------------------------------------------------


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        print("-" * 78)
        print("=== %s ===" % fn.__name__)
        try:
            fn()
        except AssertionError as exc:
            _check("%s: без необработанных AssertionError" % fn.__name__, False, str(exc))
        except Exception as exc:  # noqa: BLE001 -- тест не должен останавливать остальные
            _check("%s: без необработанного исключения" % fn.__name__, False,
                   "%s: %s" % (type(exc).__name__, exc))

    print("=" * 78)
    total = len(_RESULTS)
    failed = [r for r in _RESULTS if not r[1]]
    print("ИТОГО: %d проверок, %d провалено" % (total, len(failed)))
    if failed:
        print("ПРОВАЛЕННЫЕ ПРОВЕРКИ:")
        for name, _, detail in failed:
            print("  - %s%s" % (name, ("  -- " + str(detail)[:300]) if detail != "" else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(_run_all())
