# -*- coding: utf-8 -*-
"""Cleaning tests (phases 1-2): PDF, images, OOXML, the core.clean dispatcher,
the core.cleanreport renderer.

The main idea is the same as in core/cleanmodel.py: the inspector is the
ORACLE. The cleaning result is checked not against the cleaner's list of
actions but by running inspect_file() again on the output file, plus an
independent raw search for the values in the output bytes (including
decompressed PDF streams and PNG chunks).

The main invariant, checked on EVERY cleaning run via _clean(): the sha256
of the INPUT file is the same before and after.

What is NOT checked here and why: OLE2, ODF and RTF cleaning doesn't exist
(phase 4), so for them only an honest REFUSAL is checked: no file is
created, the phase number is named, the report doesn't promise cleanliness.
Fitting a test to the current behavior is forbidden (AGENTS.md, item 6): a
failed check here is a defect found in the module, not a reason to weaken
the check.

Run (PYTHONIOENCODING=utf-8 is required on Windows):

    PYTHONIOENCODING=utf-8 python -m tests.test_clean

No pytest, but compatible with it: test_* functions, each creates and
removes its own temporary directory. The order is by name, so test_99_render
runs LAST and goes through all the cleaning results accumulated by the
previous tests.
Exit code 1 on any failure.
"""

from __future__ import annotations

import binascii
import dataclasses
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
# result bookkeeping
# ---------------------------------------------------------------------------

_RESULTS = []   # (check name, passed?, explanation)
_SEEN = []      # (label, cleaning result): all results flow here for test_98/test_99


def _check(name, condition, detail=""):
    _RESULTS.append((name, bool(condition), detail))
    line = "%s %s" % ("OK  " if condition else "FAIL", name)
    if not condition and detail != "":
        line += "  -- %s" % (str(detail)[:400],)
    print(line)
    return bool(condition)


def _with_tmp(fn):
    """Each test_* creates and removes its own temporary directory."""

    def wrapper():
        tmp = tempfile.mkdtemp(prefix="tgbot_clean_test_")
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    wrapper.__name__ = fn.__name__
    return wrapper


# ---------------------------------------------------------------------------
# small helpers
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
    """Length in UTF-16 code units: that's how the Telegram limit counts, not len()."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _norm(value):
    return " ".join(str(value).casefold().split()).rstrip("…")


def _pname(profile):
    return getattr(profile, "value", str(profile))


def _rnd(n, seed):
    """Deterministic "garbage": reproducible and without os.urandom."""
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(b"%s:%d" % (seed.encode(), i)).digest()
        i += 1
    return out[:n]


def _clean(tag, tmp, src, profile=STEALTH, **kw):
    """clean_file into a nested out/ + the main invariant: sha256 of the input before and after.

    -> (res, dst). res is None if clean_file raised (that's a failure).
    The result goes into _SEEN for the final rendering tests."""
    label = "%s/%s" % (tag, _pname(profile))
    base = re.sub(r"[^\w.-]+", "_", tag)
    dst = os.path.join(tmp, "out", "%s_%s%s" % (base, _pname(profile), os.path.splitext(src)[1]))
    before = _sha256(src)
    try:
        res = clean_file(src, dst, profile, **kw)
    except Exception as exc:  # noqa: BLE001 -- this is exactly what we test
        _check("%s: clean_file did not raise" % label, False,
               "%s: %s" % (type(exc).__name__, exc))
        res = None
    _check("%s: INVARIANT -- input sha256 before == after cleaning" % label, _sha256(src) == before)
    if res is not None:
        _SEEN.append((label, res))
    return res, dst


# Presence markers are report markup, not data. The tests run in English, where
# the markers are longer than the 5-character cutoff below.
_MARKERS = frozenset(("present", "absent", "not set", "empty", "yes", "no", "on", "off"))


def _own_survivors(src_rep, dst_rep):
    """Our own count, independent of core.clean.survived_values: which original
    IDENTITY/ENVIRONMENT values longer than 5 characters occur in the values of
    the output's findings (substring, case- and extra-whitespace-insensitive)."""
    dst_vals = [_norm(g.value) for g in dst_rep.findings if g.value]
    out = []
    for f in src_rep.findings:
        v = _norm(f.value)
        if f.risk in _LEAKY and len(v) > 5 and v not in _MARKERS \
                and any(v in d for d in dst_vals):
            out.append((f.label, f.value))
    return out


def _oracle(label, src, dst):
    """Re-inspect the output with OUR OWN hands (not via res.verified).
    -> (src report, dst report)."""
    a, b = inspect_file(src), inspect_file(dst)
    universe = [f for f in a.findings if f.risk in _LEAKY and len(_norm(f.value)) > 5]
    _check("%s: the oracle is not vacuous -- the original has IDENTITY/ENVIRONMENT longer than 5 "
           "characters (%d)"
           % (label, len(universe)), len(universe) > 0)
    surv = survived_values(a, b)
    _check("%s: ORACLE -- no original value occurs in the output's findings"
           % label, not surv, [(s.label, s.found_at) for s in surv][:5])
    own = _own_survivors(a, b)
    _check("%s: ORACLE -- the same by the independent count" % label, not own, own[:5])
    return a, b


# --- raw search for values in the output bytes --------------------------------


def _needles(text):
    """How a value can sit in the file: UTF-8/16, cp1251, latin-1 and a PDF hex
    string (UTF-16BE with and without a BOM)."""
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
    """Independent PNG parse: ([(type, body, crc_ok)], end_offset).
    None means no signature. A truncated chunk gets the type b'?'."""
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
    """Decompressed content of compressed PNG chunks: values in zTXt/iTXt/iCCP
    aren't visible in the raw bytes."""
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
        except Exception:  # noqa: BLE001 -- a broken chunk doesn't get in the way of the search
            pass
    return out


def _blob(path):
    """Everything physically in the file: raw bytes + decompressed PDF streams
    (including object streams) and compressed PNG chunks."""
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
    """Which of the values (text in all encodings) and raw byte needles physically
    sit in the file."""
    low = _blob(path).lower()
    found = [t for t in texts if any(n.lower() in low for n in _needles(t))]
    found += ["<bytes %s...>" % n[:6].hex() for n in raw if n.lower() in low]
    return found


# ---------------------------------------------------------------------------
# file builders (fixtures that tests/fixtures.py doesn't have: SOF and
# Orientation for JPEG, iCCP/eXIf for PNG, a signature and tags for PDF)
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
# IFD0 tags that must not be in a clean result
EX_FORBIDDEN = {0x010F, 0x0110, 0x0131, EX_ARTIST_TAG, EX_GPS_TAG, EX_EXIF_TAG}


def _gps_needles(e):
    """Bytes of the latitude and longitude rationals in the given byte order."""
    return [struct.pack(e + "6I", 59, 1, 56, 1, 1500, 100),
            struct.pack(e + "6I", 30, 1, 19, 1, 2300, 100)]


def _exif_tiff(orient, order="<"):
    """An EXIF TIFF block: Make/Model/Software/Artist, ExifIFD (date, body and lens
    serial numbers) and GPS. orient=None means no Orientation tag. order: "<" or ">"."""
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
    """An entropy block with FF00 byte stuffing and RSTn markers: a naive scanner
    that stops parsing at the first 0xFF breaks on it."""
    raw = hashlib.sha256(b"entropy").digest() * 6
    out = b"\xab\xff\x00"
    chunks = [raw[i:i + 48] for i in range(0, len(raw), 48)]
    for k, c in enumerate(chunks):
        out += c.replace(b"\xff", b"\xff\x00")
        if k < len(chunks) - 1:
            out += bytes([0xFF, 0xD0 + k % 8])
    return out


def _jpeg_bytes(orient=6, order="<", w=640, h=480, exif=True, sof=0xC0, tail=b""):
    """A JPEG with a real SOF (640x480 by default), DQT, DHT and SOS."""
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
    """Independent parse of JPEG segments up to and including SOS: [(marker, start, end)]."""
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
    """(width, height) from the first SOF, None if there's no SOF."""
    for m, a, _b in _jpeg_segments(data):
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            _p, h, w = struct.unpack(">BHH", data[a + 4:a + 9])
            return w, h
    return None


def _tiff_ifd0(tiff):
    """IFD0 of a TIFF block: (byte order, {tag: (type, count, 4 value bytes)}) or None."""
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
    """Combined IFD0 tags of all blocks: {tag: ((type, count, value4), order)}."""
    tags = {}
    for blk in tiff_blocks:
        got = _tiff_ifd0(blk)
        if got:
            tags.update({t: (v, got[0]) for t, v in got[1].items()})
    return tags


def _orientation(tags):
    """The Orientation value from _ifd0_tags() or None."""
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
    """An 8-bit RGB PNG of the real size w x h; IDAT in two chunks. -> (bytes, pixel rows)."""
    rows = b"".join(b"\x00" + bytes((x * 31 + y * 17) % 256 for x in range(w * 3)) for y in range(h))
    z = zlib.compress(rows, 9)
    cut = max(1, len(z) // 2)
    ch = fixtures._png_chunk
    data = (PNG_SIG + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + b"".join(pre)
            + ch(b"IDAT", z[:cut]) + ch(b"IDAT", z[cut:]) + b"".join(post) + ch(b"IEND", b"") + trailer)
    return data, rows


def _live_bomb_png(tmp):
    """A valid PNG holding the real zTXt chunk bomb from fixtures.make_broken.
    -> (path, pixel rows)."""
    bomb_src = fixtures.make_broken(tmp)["png_ztxt_bomb"]
    bomb_body = next(b for t, b, _o in _chunks(_read(bomb_src)) if t == b"zTXt")
    data, rows = _png_bytes(pre=[fixtures._png_chunk(b"zTXt", bomb_body)])
    return _write(os.path.join(tmp, "bomb_live.png"), data), rows


def _png_full():
    """A PNG with EVERYTHING from the removal list and everything from the keep list."""
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
    """A copy of a PNG with a corrupted CRC on the first chunk of the given type."""
    pos = 8
    while pos + 12 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        if data[pos + 4:pos + 8] == ctype:
            i = pos + 8 + n
            return data[:i] + bytes([data[i] ^ 0xFF]) + data[i + 1:]
        pos += 12 + n
    raise ValueError("no chunk %r" % ctype)


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
    """A PDF via pikepdf with filled /Info and XMP (identity, environment, application)."""
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
    """A PDF with /StructTreeRoot, /MarkInfo, /Lang, /Outlines (3 bookmarks), /ViewerPreferences."""
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
    """kind: "filled" is a filled signature; "placeholder" is an empty stub
    (/FT /Sig without /V); "docmdp" is a certifying signature only in /Perms."""
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
    """A PDF where /ID[0] != /ID[1] (a diverged pair: that's what a re-saved file looks like)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.docinfo["/Author"] = "Автор Разных Идентификаторов"
    pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(b"A" * 16), pikepdf.String(b"B" * 16)])
    pdf.save(path)
    pdf.close()
    return path


def _pdf_incr_secret(path):
    """A manual incremental revision: TOPSECRET in the first revision, a "redaction" in the second."""
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
    """[ID0, ID1] from the trailer (bytes) or None."""
    with pikepdf.open(path) as q:
        ids = q.trailer.get("/ID")
        if isinstance(ids, pikepdf.Array) and len(ids) == 2:
            return [bytes(ids[0]), bytes(ids[1])]
    return None


# ===========================================================================
# 1. The sha256 INVARIANT and the ORACLE, on every fixture and every profile
# ===========================================================================


@_with_tmp
def test_01_invariant_and_oracle_all_fixtures(tmp):
    """Every make_* from tests/fixtures.py x every profile. A fixture someone adds
    later gets in here automatically."""
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
                _check("%s: a file was produced -- dst exists and is not empty" % label,
                       os.path.isfile(dst) and os.path.getsize(dst) > 0)
                _check("%s: the dispatcher ran the re-inspection (verified)" % label,
                       res.verified, res.errors)
                _check("%s: the dispatcher proved cleanliness (clean)" % label, res.clean,
                       [(s.label, s.found_at) for s in res.survived])
                _oracle(label, path, dst)
            else:
                _check("%s: refusal -- the output file was NOT created" % label, not os.path.exists(dst))
    want = {"make_pdf", "make_pdf_incremental", "make_jpeg", "make_png"}
    _check("the oracle ran on PDF, PDF with revisions, JPEG and PNG (the test is not vacuous)",
           want <= cleaned, sorted(cleaned))


# ===========================================================================
# 2. Independent raw search for values in the output bytes
# ===========================================================================


@_with_tmp
def test_02_raw_leak_scan(tmp):
    """The oracle inspector could miss what physically remained in the file.
    Here the values are searched for right in the bytes (all encodings,
    decompressed PDF streams and compressed PNG chunks)."""
    pdf, pexp = fixtures.make_pdf(tmp)
    inc, iexp = fixtures.make_pdf_incremental(tmp)
    jpg, jexp = fixtures.make_jpeg(tmp)
    png, nexp = fixtures.make_png(tmp)
    cases = (
        # (label, path, values that MUST disappear in STEALTH / in PARANOID)
        ("pdf", pdf, pexp["identity"], pexp["values"], ()),
        ("pdf_incremental", inc, iexp["values"] + [fixtures.PDF_INCR_OLD_AUTHOR],
         iexp["values"] + [fixtures.PDF_INCR_OLD_AUTHOR], ()),
        ("jpeg", jpg, jexp["values"], jexp["values"], _gps_needles("<")),
        ("png", png, nexp["values"], nexp["values"], ()),
    )
    for tag, path, st_vals, pa_vals, raw in cases:
        seen = _leaked(path, pa_vals)
        _check("%s: the search sees the values in the ORIGINAL file (otherwise the check is "
               "vacuous)" % tag,
               len(seen) == len(pa_vals), "found %d of %d" % (len(seen), len(pa_vals)))
        for prof in PROFILES:
            res, dst = _clean("raw_" + tag, tmp, path, prof)
            if res is None or not _check("%s/%s: ok" % (tag, _pname(prof)), res.ok, res.errors):
                continue
            vals = st_vals if prof is STEALTH else pa_vals
            gone = _leaked(dst, vals, raw)
            _check("%s/%s: the output bytes contain none of the %d values" % (tag, _pname(prof), len(vals)),
                   not gone, gone)


# ===========================================================================
# 3. PDF: PARANOID -- critical_after == 0; STEALTH -- only new /IDs
# ===========================================================================


@_with_tmp
def test_03_pdf_profiles_and_id(tmp):
    for tag, path in (("pdf", fixtures.make_pdf(tmp)[0]), ("pdf_rich", _pdf_rich(os.path.join(tmp, "rich.pdf")))):
        src_ids = _trailer_ids(path)
        _check("%s: the original has an /ID (otherwise the regeneration check is vacuous)" % tag, src_ids is not None)

        # ---- PARANOID: nothing critical remains at all ----
        res, dst = _clean(tag, tmp, path, PARANOID)
        if res is not None and _check("%s/paranoid: ok" % tag, res.ok, res.errors):
            rep = inspect_file(dst)
            left = [(f.label, f.value[:30]) for f in rep.findings if f.risk in _LEAKY]
            _check("%s/paranoid: critical_after == 0 (per the dispatcher)" % tag,
                   res.critical_after == 0, res.critical_after)
            _check("%s/paranoid: critical == 0 by OUR OWN re-inspection of the output" % tag,
                   rep.critical == 0, left)
            _check("%s/paranoid: critical_before > 0 -- the cleaning had something to remove" % tag, res.critical_before > 0)
            _check("%s/paranoid: nothing remains (residual is empty)" % tag, res.residual == [], res.residual)
            _check("%s/paranoid: /ID removed from the trailer" % tag, _trailer_ids(dst) is None)

        # ---- STEALTH: a remainder of exactly the regenerated /IDs is allowed ----
        res, dst = _clean(tag, tmp, path, STEALTH)
        if res is None or not _check("%s/stealth: ok" % tag, res.ok, res.errors):
            continue
        rep = inspect_file(dst)
        crit = [f for f in rep.findings if f.risk in _LEAKY]
        labels = {f.label.split(" ")[0] for f in crit}
        _check("%s/stealth: the critical remainder consists ONLY of trailer /ID[0], /ID[1]" % tag,
               labels <= {"/ID[0]", "/ID[1]"}, [(f.location, f.label, f.value[:30]) for f in crit])
        was = {_norm(f.value) for f in inspect_file(path).findings}
        _check("%s/stealth: the remainder values are NEW -- the original's findings don't have "
               "them" % tag,
               all(_norm(f.value) not in was for f in crit), [f.value[:20] for f in crit])
        new_ids = _trailer_ids(dst)
        _check("%s/stealth: the output has an /ID" % tag, new_ids is not None)
        if src_ids and new_ids:
            _check("%s/stealth: /ID[0] DIFFERS from the original (otherwise it's not a "
                   "regeneration)" % tag,
                   new_ids[0] != src_ids[0], new_ids[0].hex())
            _check("%s/stealth: /ID[1] DIFFERS from the original" % tag, new_ids[1] != src_ids[1], new_ids[1].hex())
            _check("%s/stealth: the shape of the pair is kept (an equal pair stayed equal)" % tag,
                   (src_ids[0] == src_ids[1]) == (new_ids[0] == new_ids[1]), (src_ids, new_ids))
        _check("%s/stealth: dispatcher: the critical remainder is only new values" % tag,
               all(r[2] for r in res.residual), res.residual)
        _check("%s/stealth: the cleaning had something to remove" % tag, res.critical_before > res.critical_after
               or res.sensitive_before > res.sensitive_after, (res.critical_before, res.critical_after))

    # ---- a diverged /ID pair ----
    path = _pdf_unequal_id(os.path.join(tmp, "uneq.pdf"))
    src_ids = _trailer_ids(path)
    _check("uneq: the original has /ID[0] != /ID[1] (precondition)", src_ids is not None and src_ids[0] != src_ids[1])
    res, dst = _clean("uneq", tmp, path, STEALTH)
    if res is not None and _check("uneq/stealth: ok", res.ok, res.errors):
        new = _trailer_ids(dst)
        _check("uneq/stealth: both /IDs DIFFER from the original ones", bool(new and src_ids)
               and new[0] != src_ids[0] and new[1] != src_ids[1], (src_ids, new))
        _check("uneq/stealth: the pair stayed diverged (same shape as the original)",
               bool(new) and new[0] != new[1], new)
    res, dst = _clean("uneq", tmp, path, PARANOID)
    if res is not None and _check("uneq/paranoid: ok", res.ok, res.errors):
        _check("uneq/paranoid: critical_after == 0", res.critical_after == 0, res.critical_after)

    # ---- how cleaning leaves the application and dates (STEALTH doesn't blank them, PARANOID removes) ----
    path = _pdf_rich(os.path.join(tmp, "rich2.pdf"))
    res, dst = _clean("rich_info", tmp, path, STEALTH)
    if res is not None and _check("rich_info/stealth: ok", res.ok, res.errors):
        with pikepdf.open(dst) as q:
            info = {k: str(v) for k, v in q.docinfo.items()}
        _check("STEALTH: /Producer and /Creator left as they were (the Creator/Producer pair is "
               "consistent)",
               info.get("/Producer") == RICH_PRODUCER and info.get("/Creator") == RICH_CREATOR, info)
        _check("STEALTH: dates are not blanked or replaced",
               info.get("/CreationDate") == RICH_CDATE and info.get("/ModDate") == RICH_MDATE, info)
        _check("STEALTH: /Author, /Company, the path and the email removed from /Info",
               not {"/Author", "/Company"} & set(info)
               and not any(RICH_EMAIL in v or RICH_PATH in v for v in info.values()), info)
        gone = _leaked(dst, [RICH_AUTHOR, RICH_COMPANY, RICH_EMAIL, RICH_PATH])
        _check("STEALTH: the name, company, email and path are in neither the bytes nor the "
               "streams (XMP)", not gone, gone)
    res, dst = _clean("rich_info", tmp, path, PARANOID)
    if res is not None and _check("rich_info/paranoid: ok", res.ok, res.errors):
        with pikepdf.open(dst) as q:
            info = {k: str(v) for k, v in q.docinfo.items()}
        _check("PARANOID: /Producer, /Creator and the dates removed", not {"/Producer", "/Creator", "/CreationDate",
                                                                    "/ModDate"} & set(info), info)


# ===========================================================================
# 4. PDF: incremental revisions are dropped
# ===========================================================================


@_with_tmp
def test_04_pdf_incremental_revisions(tmp):
    cases = [("pdf_incremental", fixtures.make_pdf_incremental(tmp)[0]),
             ("pdf_incr_secret", _pdf_incr_secret(os.path.join(tmp, "secret.pdf")))]
    for tag, path in cases:
        src = _read(path)
        _check("%s: the ORIGINAL has two %%%%EOF -- the fixture really is incremental" % tag,
               src.count(b"%%EOF") == 2, src.count(b"%%EOF"))
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            out = _read(dst)
            _check("%s: the raw output bytes contain EXACTLY one %%%%EOF" % label,
                   out.count(b"%%EOF") == 1, out.count(b"%%EOF"))
            with pikepdf.open(dst) as q:
                _check("%s: the output trailer has no /Prev" % label, "/Prev" not in q.trailer)
                _check("%s: the page is in place" % label, len(q.pages) == 1)
            _check("%s: the actions record that the revisions were dropped" % label,
                   any(a.act is Act.REMOVED and "revision" in a.label.lower() for a in res.actions),
                   [a.label for a in res.actions])
    # the reason revisions are dropped: the text "under the redaction" physically goes away
    path = cases[1][1]
    raw = _read(path)
    _check("secret: TOPSECRET sits in the raw bytes of the ORIGINAL (in the earlier revision)", b"TOPSECRET" in raw)
    with pikepdf.open(path) as q:
        _check("secret: the current revision shows the \"redaction\", not the secret "
               "(precondition)",
               b"REDACTED" in q.pages[0].Contents.read_bytes())
    for prof in PROFILES:
        res, dst = _clean("pdf_incr_secret", tmp, path, prof)
        if res is None or not res.ok:
            continue
        blob = _blob(dst)
        _check("secret/%s: TOPSECRET and 4111 are in neither the bytes nor the decompressed "
               "streams" % _pname(prof),
               b"TOPSECRET" not in blob and b"4111" not in blob)
        _check("secret/%s: the CURRENT page content (REDACTED) is kept" % _pname(prof), b"REDACTED" in blob)
        _check("secret/%s: the earlier revision's author is not in the file" % _pname(prof),
               not _leaked(dst, ["Иван Петров Секретный"]))


# ===========================================================================
# 5. PDF: structural data is kept in BOTH profiles
# ===========================================================================


@_with_tmp
def test_05_pdf_structural_kept(tmp):
    path = _pdf_tagged(os.path.join(tmp, "tagged.pdf"))
    need = (("/StructTreeRoot", "Structure tree"), ("/Lang", "Document language"), ("/Outlines", "Outline"))
    src_rep = inspect_file(path)
    for key, word in need:
        _check("tagged: the inspection report of the ORIGINAL has %s (precondition)" % key,
               any(f.risk is Risk.STRUCTURAL and word in f.label for f in src_rep.findings))
    for prof in PROFILES:
        label = "tagged/%s" % _pname(prof)
        res, dst = _clean("tagged", tmp, path, prof)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        rep = inspect_file(dst)
        kept = {a.label for a in res.actions if a.act is Act.KEPT}
        for key, word in need:
            _check("%s: %s -- recorded as kept (KEPT) in the cleaning report" % (label, key),
                   key in kept, sorted(kept))
            _check("%s: %s -- present in the inspection report of the OUTPUT (STRUCTURAL)" % (label, key),
                   any(f.risk is Risk.STRUCTURAL and word in f.label for f in rep.findings))
        removed = {a.label for a in res.actions if a.act is Act.REMOVED}
        _check("%s: no structural field is marked as removed" % label,
               not removed & {"/StructTreeRoot", "/MarkInfo", "/Lang", "/Outlines", "/PageLabels",
                              "/ViewerPreferences"}, sorted(removed))
        with pikepdf.open(dst) as q:
            root = q.Root
            _check("%s: /StructTreeRoot is physically in the output catalog" % label, "/StructTreeRoot" in root)
            _check("%s: /MarkInfo is physically kept" % label, "/MarkInfo" in root)
            _check("%s: /Lang == ru-RU" % label, str(root.get("/Lang")) == "ru-RU", root.get("/Lang"))
            titles, item = [], root.Outlines.First if "/Outlines" in root else None
            while item is not None and len(titles) < 10:
                titles.append(str(item.Title))
                item = item.get("/Next")
            _check("%s: bookmarks -- the same three titles in the same order" % label,
                   titles == ["Глава 1", "Глава 2", "Глава 3"], titles)
            _check("%s: all 3 pages are in place" % label, len(q.pages) == 3)
        _check("%s: the identity (author) is removed all the same" % label, not _leaked(dst, [STRUCT_AUTHOR]))
        _oracle(label, path, dst)


# ===========================================================================
# 6. PDF: signature
# ===========================================================================


@_with_tmp
def test_06_pdf_signature(tmp):
    signed = _pdf_form(os.path.join(tmp, "signed.pdf"), "filled")
    src_rep = inspect_file(signed)
    _check("signed: the inspector sees the filled signature (precondition)",
           any(SIGNER in f.value or "signature" in f.label.lower() for f in src_rep.findings))

    # --- by default: an honest refusal, no file ---
    for prof in PROFILES:
        label = "signed/%s" % _pname(prof)
        res, dst = _clean("signed", tmp, signed, prof)
        if res is None:
            continue
        _check("%s: ok=False by default" % label, not res.ok)
        _check("%s: the output file was NOT created" % label, not os.path.exists(dst))
        _check("%s: the reason is named clearly (signature)" % label,
               any("signature" in e.lower() for e in res.errors), res.errors)
        _check("%s: no actions and no claimed cleanliness" % label,
               not res.actions and not res.clean and res.critical_after == -1)
    out = os.path.join(tmp, "direct.pdf")
    r = clean_pdf(signed, out)
    _check("clean_pdf directly: ok=False, no file and no .part, the reason is named",
           not r.ok and not os.path.exists(out) and not os.path.exists(out + ".part")
           and any("signature" in e.lower() for e in r.errors), r.errors)

    # --- force_signed: cleans, the signature is destroyed and that's stated ---
    for prof in PROFILES:
        label = "signed_force/%s" % _pname(prof)
        res, dst = _clean("signed_force", tmp, signed, prof, force_signed=True)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        blob = _blob(dst)
        _check("%s: the signature is physically removed (no adbe.pkcs7)" % label, b"adbe.pkcs7" not in blob)
        _check("%s: the signer's name is not in the file" % label, not _leaked(dst, [SIGNER]))
        _check("%s: the actions honestly record that the signature was destroyed (REMOVED)" % label,
               any(a.act is Act.REMOVED and "signature" in a.label.lower() for a in res.actions),
               [(a.act.value, a.label) for a in res.actions])
        with pikepdf.open(dst) as q:
            fields = q.Root.AcroForm.Fields if "/AcroForm" in q.Root else []
            _check("%s: the field stayed an empty stub (no /V)" % label,
                   all("/V" not in f for f in fields), [list(f.keys()) for f in fields])
        _oracle(label, signed, dst)

    # --- an empty field stub: NOT a signature, cleaning proceeds normally ---
    ph = _pdf_form(os.path.join(tmp, "placeholder.pdf"), "placeholder")
    for prof in PROFILES:
        label = "sig_placeholder/%s" % _pname(prof)
        res, dst = _clean("sig_placeholder", tmp, ph, prof)
        if res is None or not _check("%s: cleaning proceeds normally, force_signed isn't needed" % label, res.ok, res.errors):
            continue
        with pikepdf.open(dst) as q:
            f = q.Root.AcroForm.Fields[0]
            _check("%s: the stub is in place: /FT /Sig, no /V" % label,
                   str(f.get("/FT")) == "/Sig" and "/V" not in f, list(f.keys()))
        _check("%s: the author is removed" % label, not _leaked(dst, [SIG_AUTHOR]))

    # --- a certifying signature only in /Perms /DocMDP ---
    dm = _pdf_form(os.path.join(tmp, "docmdp.pdf"), "docmdp")
    res, dst = _clean("docmdp", tmp, dm, STEALTH)
    if res is not None:
        _check("docmdp: a certifying signature (/Perms /DocMDP) -- also refused by default",
               not res.ok and not os.path.exists(dst) and any("signature" in e.lower() for e in res.errors),
               res.errors)
    res, dst = _clean("docmdp_force", tmp, dm, STEALTH, force_signed=True)
    if res is not None and _check("docmdp_force: ok", res.ok, res.errors):
        with pikepdf.open(dst) as q:
            _check("docmdp_force: /Perms removed, no signature in the file",
                   "/Perms" not in q.Root and b"adbe.pkcs7" not in _blob(dst))


# ===========================================================================
# 7. JPEG: no re-encoding -- the entropy block bit for bit
# ===========================================================================


@_with_tmp
def test_07_jpeg_scan_bit_exact(tmp):
    fx_path = fixtures.make_jpeg(tmp)[0]
    own = _write(os.path.join(tmp, "own.jpg"), _jpeg_bytes(6))
    for tag, path in (("fixture_jpeg", fx_path), ("sof_640x480", own)):
        src = _read(path)
        so = _sos_at(src)
        _check("%s: SOS found in the original (precondition)" % tag, so is not None)
        if so is None:
            continue
        _check("%s: the block from SOS to the end of the file is not empty (%d bytes)" % (tag, len(src) - so), len(src) - so > 10)
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            out = _read(dst)
            do = _sos_at(out)
            _check("%s: SOS found in the output" % label, do is not None)
            if do is None:
                continue
            _check("%s: BIT FOR BIT -- src[SOS:] == dst[SOS:] (%d bytes): there was no "
                   "re-encoding"
                   % (label, len(src) - so), src[so:] == out[do:])
            _check("%s: verify_image confirms scan_same" % label,
                   verify_image(path, dst).get("scan_same") is True, verify_image(path, dst))
            _check("%s: the output starts with SOI and ends with EOI" % label,
                   out[:2] == b"\xff\xd8" and out[-2:] == b"\xff\xd9")
            if tag == "sof_640x480":
                _check("%s: the FF00 stuffing and RSTn markers inside the block survived" % label, _entropy() in out)
            _check("%s: the quantization tables (DQT) are in place, not rebuilt" % label,
                   src[[a for m, a, _b in _jpeg_segments(src) if m == 0xDB][0]:][:69]
                   in out)


# ===========================================================================
# 8. JPEG: ORIENTATION -- the main regression (photos turned on their side)
# ===========================================================================


@_with_tmp
def test_08_jpeg_orientation_preserved(tmp):
    for order in ("<", ">"):
        for orient in (1, 2, 3, 4, 5, 6, 7, 8):
            tag = "jpeg_%s_o%d" % ("LE" if order == "<" else "BE", orient)
            path = _write(os.path.join(tmp, tag + ".jpg"), _jpeg_bytes(orient, order))
            src = _read(path)
            _check("%s: the independent parse reads Orientation=%d from the ORIGINAL "
                   "(precondition)" % (tag, orient),
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
                    _check("%s: Orientation=1 -- APP1 removed entirely or the tag equals 1" % label,
                           got in (None, 1), got)
                else:
                    _check("%s: the Orientation tag MUST remain, value %d (read right from APP1)"
                           % (label, orient), got == orient, "in the output: %r" % (got,))
                bad = sorted(hex(t) for t in set(tags) & EX_FORBIDDEN)
                _check("%s: APP1 has no Artist, GPS, Make, Model, Software tags or ExifIFD "
                       "pointer" % label,
                       not bad, bad)
                gone = _leaked(dst, EX_TEXTS, _gps_needles(order))
                _check("%s: Artist, serial numbers, GPS, date, camera and software physically "
                       "gone from the bytes" % label,
                       not gone, gone)
                _check("%s: the SOF dimensions didn't change (640x480)" % label,
                       _sof_dims(src) == (640, 480) == _sof_dims(out), (_sof_dims(src), _sof_dims(out)))
                so, do = _sos_at(src), _sos_at(out)
                _check("%s: the entropy block bit for bit" % label,
                       do is not None and src[so:] == out[do:])
                v = verify_image(path, dst)
                _check("%s: verify_image: ok, orient_ok, dims_same" % label,
                       v.get("ok") and v.get("orient_ok") and v.get("dims_same"), v)
                if orient in (1, 6, 8):
                    _oracle(label, path, dst)

    # PNG: the same risk via eXIf
    for orient in (1, 6, 8):
        tag = "png_exif_o%d" % orient
        data, rows = _png_bytes(pre=[fixtures._png_chunk(b"eXIf", _exif_tiff(orient, "<"))])
        path = _write(os.path.join(tmp, tag + ".png"), data)
        _check("%s: the independent parse reads Orientation=%d from the original eXIf "
               "(precondition)" % (tag, orient),
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
                _check("%s: Orientation=1 -- eXIf removed or the tag equals 1" % label, got in (None, 1), got)
            else:
                _check("%s: the Orientation tag in eXIf MUST remain, value %d" % (label, orient),
                       got == orient, "in the output: %r" % (got,))
            bad = sorted(hex(t) for t in set(tags) & EX_FORBIDDEN)
            _check("%s: eXIf has no Artist, GPS, Make, Model, Software" % label, not bad, bad)
            gone = _leaked(dst, EX_TEXTS, _gps_needles("<"))
            _check("%s: Artist, serial numbers, GPS physically gone from the bytes" % label, not gone, gone)
            _check("%s: the pixels are the same (IDAT bit for bit, decompression gives the same "
                   "rows)" % label,
                   _png_idat(out) == _png_idat(data) and zlib.decompress(_png_idat(out)) == rows)


# ===========================================================================
# 9. Image dimensions didn't change (SOF for JPEG, IHDR for PNG)
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
        _check("%s: the original's dimensions are read by the independent parse: %s" % (tag, want),
               dims(data) == want, dims(data))
        for prof in PROFILES:
            label = "%s/%s" % (tag, _pname(prof))
            res, dst = _clean(tag, tmp, path, prof)
            if res is None or not _check("%s: ok" % label, res.ok, res.errors):
                continue
            _check("%s: width and height BEFORE and AFTER match: %s" % (label, want),
                   dims(_read(dst)) == want, dims(_read(dst)))
            v = verify_image(path, dst)
            _check("%s: verify_image: dims_same" % label, v.get("dims_same") is True, v)
    path = fixtures.make_png(tmp)[0]
    for prof in PROFILES:
        res, dst = _clean("fixture_png", tmp, path, prof)
        if res is not None and res.ok:
            _check("fixture_png/%s: IHDR (1x1) didn't change" % _pname(prof),
                   _png_dims(_read(path)) == _png_dims(_read(dst)) == (1, 1))


# ===========================================================================
# 10. PNG: chunks, CRC, order, ICC
# ===========================================================================


@_with_tmp
def test_10_png_chunks(tmp):
    data, rows = _png_full()
    path = _write(os.path.join(tmp, "full.png"), data)
    src_chunks = _chunks(data)
    src_types = [t for t, _b, _o in src_chunks]
    _check("precondition: the CRCs of all the original's chunks are correct", all(o for _t, _b, o in src_chunks))
    _check("precondition: the original has tEXt, zTXt, iTXt, eXIf, tIME and iCCP",
           PNG_FORBIDDEN <= set(src_types) and b"iCCP" in src_types, src_types)
    src_body = {t: b for t, b, _o in src_chunks}
    for prof in PROFILES:
        label = "png_full/%s" % _pname(prof)
        res, dst = _clean("png_full", tmp, path, prof)
        if res is None or not _check("%s: ok" % label, res.ok, res.errors):
            continue
        out = _read(dst)
        parsed = _png_chunks(out)
        if not _check("%s: the output parses as PNG" % label, parsed is not None):
            continue
        chunks, end = parsed
        types = [t for t, _b, _o in chunks]
        body = {t: b for t, b, _o in chunks}
        left = sorted(t.decode() for t in PNG_FORBIDDEN & set(types))
        _check("%s: tEXt, zTXt, iTXt, eXIf, tIME removed" % label, not left, "left: %s" % left)
        _check("%s: the CRCs of all %d output chunks are correct (recomputed with binascii.crc32)" % (label, len(chunks)),
               all(o for _t, _b, o in chunks), [t for t, _b, o in chunks if not o])
        _check("%s: IHDR first, IEND last" % label,
               types[:1] == [b"IHDR"] and types[-1:] == [b"IEND"], types)
        _check("%s: nothing in the file after IEND" % label, end == len(out), (end, len(out)))
        _check("%s: IHDR didn't change (dimensions, depth, color type)" % label,
               body.get(b"IHDR") == src_body[b"IHDR"])
        _check("%s: IDAT matches byte for byte and decompresses into the same pixel rows" % label,
               _png_idat(out) == _png_idat(data) and zlib.decompress(_png_idat(out)) == rows)
        _check("%s: gAMA and pHYs (STRUCTURAL) kept unchanged" % label,
               body.get(b"gAMA") == src_body[b"gAMA"] and body.get(b"pHYs") == src_body[b"pHYs"], types)
        gone = _leaked(dst, P_TEXTS + EX_TEXTS, _gps_needles("<"))
        _check("%s: tEXt, zTXt, iTXt, XMP, EXIF, GPS values are in neither the bytes nor the "
               "decompressed chunks"
               % label, not gone, gone)
        _oracle(label, path, dst)
        if prof is STEALTH:
            _check("%s: iCCP kept byte for byte" % label, body.get(b"iCCP") == src_body[b"iCCP"], types)
        else:
            removed = any(a.act is Act.REMOVED and "ICC" in a.label for a in res.actions)
            _check("%s: iCCP is either kept or its removal is recorded in the actions (it "
                   "doesn't vanish silently)" % label,
                   b"iCCP" in types or removed, [(a.act.value, a.label) for a in res.actions][:8])
    res, dst = _clean("png_full_keepicc", tmp, path, PARANOID, keep={"icc"})
    if res is not None and _check("png_full_keepicc/paranoid: ok", res.ok, res.errors):
        body = {t: b for t, b, _o in _chunks(_read(dst))}
        _check("keep={'icc'}: iCCP stays byte for byte in PARANOID too", body.get(b"iCCP") == src_body[b"iCCP"])
        _check("keep={'icc'}: the other metadata (tEXt, eXIf, tIME) is removed all the same",
               not (PNG_FORBIDDEN & {t for t, _b, _o in _chunks(_read(dst))}))

    # the tail after IEND (a classic hiding spot) is dropped
    data, _rows = _png_bytes(trailer=b"SECRET-TAIL-PAYLOAD-0451")
    path = _write(os.path.join(tmp, "tail.png"), data)
    for prof in PROFILES:
        res, dst = _clean("png_tail", tmp, path, prof)
        if res is not None and res.ok:
            out = _read(dst)
            _check("png_tail/%s: the data after IEND is dropped" % _pname(prof),
                   b"SECRET-TAIL-PAYLOAD" not in out and _png_chunks(out)[1] == len(out))


# ===========================================================================
# 16. JPEG: data after EOI is dropped; a zTXt bomb in a live PNG doesn't hang cleaning
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
        _check("%s: the data after EOI (a hiding spot) is dropped, the file ends at EOI" % label,
               b"SECRET-TAIL-PAYLOAD" not in out and out[-2:] == b"\xff\xd9")
        so, do = _sos_at(_read(path)), _sos_at(out)
        _check("%s: the entropy block up to EOI bit for bit" % label,
               do is not None and _read(path)[so:so + (len(out) - do)] == out[do:])

    # a real zTXt bomb (200 MB of zeros in ~200 KB) inside a VALID PNG
    path, rows = _live_bomb_png(tmp)
    for prof in PROFILES:
        label = "png_ztxt_bomb_live/%s" % _pname(prof)
        t0 = time.time()
        res, dst = _clean("png_ztxt_bomb_live", tmp, path, prof)
        dt = time.time() - t0
        if res is None:
            continue
        _check("%s: finished quickly and without memory blow-up (%.1f s)" % (label, dt), dt < 30, dt)
        if _check("%s: ok -- the chunk bomb was removed without decompressing" % label, res.ok, res.errors):
            out = _read(dst)
            _check("%s: the output is small, no zTXt, the pixels are the same" % label,
                   len(out) < 10000 and b"zTXt" not in {t for t, _b, _o in _chunks(out)}
                   and zlib.decompress(_png_idat(out)) == rows, len(out))


# ===========================================================================
# 11. REFUSALS: formats without a cleaner -- an honest "not cleaned" and the phase number
# ===========================================================================


def _claims_clean(text):
    """Whether the report text declares the file clean. "NOT CLEANED" doesn't, and
    render_plain wraps lines (textwrap), so whitespace is normalized."""
    flat = " ".join(text.split())
    return "CLEAN: re-inspection" in flat or re.search(r"(?<!NOT )CLEANED", flat) is not None


def _phases(text):
    """All phase numbers mentioned in the text: "phase 2", "in phase 4"."""
    return {int(m) for m in re.findall(r"[Pp]hase\s+(\d+)", text)}


# (label, factory, allowed phase numbers). Legacy OLE2 is phase 4: it can't be
# cleaned in place at all, the edit history is woven into the container.
_REFUSED = (
    # docx, docx_wordlike, xlsx, pptx are gone from here: since phase 2 they ARE
    # CLEANED rather than refused. Their behavior is checked in the OOXML
    # cleaning tests below.
    ("doc", fixtures.make_doc, (4,)),
    ("doc_active", fixtures.make_doc_active, (4,)),
    ("xls", fixtures.make_xls, (4,)),
    ("ppt", fixtures.make_ppt, (4,)),
)


def test_14_cleanable_matches_cleaners():
    """cleanreport.CLEANABLE must match what the dispatcher can really do.

    This isn't nitpicking about duplication but a guard against a silent hole:
    the bot shows the cleaning buttons based on CLEANABLE, not on
    core.clean._CLEANERS. Adding a cleaner and forgetting CLEANABLE means a bot
    that refuses to clean a format that can already be cleaned, and no format
    test would notice. That's exactly how phase 2 reached the bot broken.
    """
    families = set(clean_mod._CLEANERS)
    expected = {fmt for fmt, fam in sniff.FAMILY.items() if fam in families}
    # clean_image rejects TIFF/HEIC on purpose (risk of damaging the file), so
    # they belong to the image family but don't count as cleanable.
    expected -= set(cleanreport._REFUSED)
    _check("CLEANABLE == formats of the families that have a cleaner, minus the deliberately "
           "rejected ones: %s" % ", ".join(sorted(expected)),
           set(cleanreport.CLEANABLE) == expected,
           "extra in CLEANABLE: %s; missing: %s"
           % (sorted(set(cleanreport.CLEANABLE) - expected),
              sorted(expected - set(cleanreport.CLEANABLE))))
    # The other end of the same link: a format with a cleaner must not also be
    # listed as refused with a phase number.
    both = sorted(set(cleanreport.CLEANABLE) & set(cleanreport._PHASE))
    _check("a format can't be both cleanable and waiting for a phase", not both, both)
    planned = sorted(f for f in cleanreport.CLEANABLE if sniff.FAMILY.get(f) in clean_mod._PLANNED)
    _check("a cleanable format is not in the dispatcher's refusal table _PLANNED", not planned, planned)


def test_15_customxml_namespaces_agree():
    """The inspector and the cleaner must understand customXml roots the same way.

    The inspector is the oracle: whatever it counts as ENVIRONMENT, the cleaner
    must remove. A root the inspector considers a SharePoint leak but the
    cleaner doesn't have in its list stays in the file, and a properly cleaned
    file gets a "not fully cleaned" verdict out of nowhere. The lists live in
    two modules (the inspector must not depend on the cleaner), so their match
    is checked here rather than achieved through a shared import.
    """
    insp_sp = set(insp_ooxml._CX_SHAREPOINT_NS)
    clean_sp = set(clean_ooxml._SP_ROOT_NS)
    _check("the SharePoint root lists of the inspector and the cleaner match",
           insp_sp == clean_sp,
           "only the inspector: %s; only the cleaner: %s"
           % (sorted(insp_sp - clean_sp), sorted(clean_sp - insp_sp)))
    insp_c = set(insp_ooxml._CX_CONTENT_NS)
    clean_c = set(clean_ooxml._CONTENT_ROOT_NS)
    _check("the content root lists of the inspector and the cleaner match",
           insp_c == clean_c,
           "only the inspector: %s; only the cleaner: %s"
           % (sorted(insp_c - clean_c), sorted(clean_c - insp_c)))
    _check("a root can't be both a SharePoint leak and content",
           not (insp_sp & insp_c), sorted(insp_sp & insp_c))


@_with_tmp
def test_16_rtf_cleaning(tmp):
    """RTF is cleaned in place. The oracle is the re-inspection, not the list of actions.

    STEALTH keeps the generator: an empty application string is itself a
    cleaning trace. PARANOID removes it too. A path in the document body is an
    environment leak, it goes in both profiles.
    """
    path, _exp = fixtures.make_rtf(tmp)
    before = open(path, "rb").read()
    for prof in PROFILES:
        res, dst = _clean("rtf", tmp, path, prof)
        _check("rtf/%s: ok and the file is created" % _pname(prof), res.ok and os.path.exists(dst), res.errors)
        _check("rtf/%s: the input was not modified" % _pname(prof), open(path, "rb").read() == before)
        if not res.ok:
            continue
        src_rep, dst_rep = inspect_file(path), inspect_file(dst)
        lived = survived_values(src_rep, dst_rep)
        _check("rtf/%s: no original value survived" % _pname(prof), not lived,
               [(s.label, s.value[:40]) for s in lived])
        blob = " ".join(f.value or "" for f in dst_rep.findings)
        if prof is Profile.STEALTH:
            _check("rtf/stealth: the generator is in place", fixtures.RTF_GENERATOR in blob, blob[:200])
        else:
            _check("rtf/paranoid: no generator", fixtures.RTF_GENERATOR not in blob)


@_with_tmp
def test_17_odf_cleaning(tmp):
    """ODF is cleaned via zipfix. STEALTH keeps the generator, PARANOID doesn't.

    The ZIP timestamps aren't rewritten: the fixture has 1980-01-01, and they
    must stay the same after cleaning. Otherwise a file from Word would get a
    real time, and a file from LibreOffice the epoch.
    """
    path, _exp = fixtures.make_odt(tmp)
    before = open(path, "rb").read()

    def times(p):
        with zipfile.ZipFile(p) as zf:
            return [i.date_time for i in zf.infolist()]

    src_times = times(path)
    for prof in PROFILES:
        res, dst = _clean("odt", tmp, path, prof)
        _check("odt/%s: ok and the file is created" % _pname(prof),
               res.ok and os.path.exists(dst), res.errors)
        _check("odt/%s: the input was not modified" % _pname(prof), open(path, "rb").read() == before)
        if not res.ok:
            continue
        _check("odt/%s: the ZIP entry times were not rewritten" % _pname(prof),
               times(dst) == src_times, times(dst))
        lived = survived_values(inspect_file(path), inspect_file(dst))
        _check("odt/%s: no original value survived" % _pname(prof), not lived,
               [(s.label, s.value[:40]) for s in lived])
        blob = " ".join(f.value or "" for f in inspect_file(dst).findings)
        if prof is Profile.STEALTH:
            _check("odt/stealth: the generator is in place", fixtures.ODT_GENERATOR in blob)
        else:
            _check("odt/paranoid: no generator", fixtures.ODT_GENERATOR not in blob)


@_with_tmp
def test_11_refusals_unsupported_formats(tmp):
    for name, maker, phases in _REFUSED:
        d = os.path.join(tmp, name)
        os.makedirs(d)
        path, _exp = maker(d)
        _check("%s: sniff recognized the file format (%s) -- the refusal is caused by the "
               "missing cleaner, not by garbage" % (name, sniff.sniff(path)), sniff.sniff(path) not in ("unknown", "unreadable"))
        for prof in PROFILES:
            label = "%s/%s" % (name, _pname(prof))
            res, dst = _clean(name, d, path, prof)
            if res is None:
                continue
            msg = " ".join(res.errors)
            _check("%s: ok=False" % label, not res.ok)
            _check("%s: the output file was NOT created" % label, not os.path.exists(dst))
            _check("%s: the error text says the file was NOT cleaned" % label, "NOT cleaned" in msg, msg)
            named = _phases(msg)
            _check("%s: the error text names a phase number allowed for the format %s" % (label, phases),
                   len(named) == 1 and named <= set(phases), "named: %s; %s" % (sorted(named), msg))
            _check("%s: planned_phase matches the phase named in the text" % label,
                   res.planned_phase > 0 and {res.planned_phase} == named, (res.planned_phase, named))
            _check("%s: no cleanliness claimed -- clean=False, not verified, critical_after=-1, "
                   "no actions" % label,
                   not res.clean and not res.verified and res.critical_after == -1 and not res.actions)
            _check("%s: the errors have neither the directory path nor the file name" % label,
                   d not in msg and os.path.basename(path) not in msg, msg)
        # an existing dst is not overwritten on a refusal
        sent = os.path.join(d, "sentinel.out")
        _write(sent, b"old")
        res = clean_file(path, sent)
        _check("%s: a refusal doesn't touch an already existing output file" % name,
               not res.ok and _read(sent) == b"old")

        # the user report doesn't promise cleanliness and names the same phase
        res, _dst = _clean(name + "_report", d, path, STEALTH)
        if res is None:
            continue
        plain = cleanreport.render_plain(res)
        tg = "\n".join(cleanreport.render_telegram(res))
        summ = cleanreport.summary_line(res)
        _check("%s: the report (text and Telegram) says \"FILE NOT CLEANED\"" % name,
               "FILE NOT CLEANED" in plain and "FILE NOT CLEANED" in tg)
        _check("%s: the summary starts with \"NOT cleaned\"" % name, summ.startswith("NOT cleaned"), summ)
        _check("%s: the report never calls the file clean" % name,
               not _claims_clean(plain) and not _claims_clean(tg))
        # The precondition for the equality below: the file is REFUSED, and the phase
        # number in the report is the reason for the refusal. A cleaned file has
        # planned_phase == 0, and its report names a phase for information (a warning
        # about Regenerate as the path to being undetectable), so the equality
        # doesn't apply to it.
        _check("%s: precondition -- the file is refused and the refusal has a phase number" % name,
               not res.ok and res.planned_phase > 0, (res.ok, res.planned_phase))
        ph = _phases(plain) | _phases(tg)
        _check("%s: the cleanreport report and the dispatcher name THE SAME phase (the user sees "
               "both texts)" % name,
               ph == {res.planned_phase}, "phases in the report: %s, planned_phase=%s" % (sorted(ph), res.planned_phase))

    # the REGENERATE profile (phase 4) creates no file for PDF
    path = fixtures.make_pdf(tmp)[0]
    dst = os.path.join(tmp, "regen.pdf")
    res = clean_file(path, dst, Profile.REGENERATE)
    _check("REGENERATE: ok=False, phase 4 is named, no file",
           not res.ok and res.planned_phase == 4 and not os.path.exists(dst) and 4 in _phases(" ".join(res.errors)),
           res.errors)
    res = clean_file(path, dst, "no-such-profile")
    _check("unknown profile: ok=False, no file", not res.ok and not os.path.exists(dst), res.errors)


# ===========================================================================
# 12. Protection against overwriting the input
# ===========================================================================


@_with_tmp
def test_12_no_overwrite_of_input(tmp):
    for name, maker in (("pdf", fixtures.make_pdf), ("jpeg", fixtures.make_jpeg),
                        ("png", fixtures.make_png), ("docx", fixtures.make_docx)):
        d = os.path.join(tmp, name)
        os.makedirs(d)
        path, _exp = maker(d)
        base = os.path.basename(path)
        variants = [("same path", path), ("via '.'", os.path.join(d, ".", base)),
                    ("via 'x/..'", os.path.join(d, "x", "..", base))]
        if os.name == "nt":
            variants.append(("different case", os.path.join(d, base.upper())))
        link = os.path.join(d, "hard_" + base)
        linked = False
        try:
            os.link(path, link)
            linked = True
            variants.append(("hard link", link))
        except (OSError, NotImplementedError, AttributeError):
            pass
        before, listing = _sha256(path), sorted(os.listdir(d))
        for vname, dst in variants:
            label = "%s: clean_file(src, %s)" % (name, vname)
            try:
                res = clean_file(path, dst)
            except Exception as exc:  # noqa: BLE001
                _check("%s did not raise" % label, False, "%s: %s" % (type(exc).__name__, exc))
                continue
            _SEEN.append(("%s/overwrite/%s" % (name, vname), res))
            _check("%s -> ok=False" % label, not res.ok, res.errors)
            _check("%s: src was not modified (sha256)" % label, _sha256(path) == before)
            if vname == "hard link":
                _check("%s: and the link (same inode) was not modified" % label, _sha256(link) == before)
        _check("%s: no extra files appeared or remained in the directory" % name,
               sorted(os.listdir(d)) == listing, sorted(os.listdir(d)))
        # the cleaners themselves, bypassing the dispatcher: the input stays intact for any dst
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
                _check("%s: clean_image(src, a hard link to src) -> ok=False" % name, not r.ok, r.errors)
        _check("%s: after calling the cleaners directly src was not modified" % name, _sha256(path) == before)


# ===========================================================================
# 13. PATHOLOGIES: not a single exception escapes, the input is intact, no file
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
    _check("the pathology set is assembled: an empty file, garbage with magic, a truncated PDF, "
           "broken CRCs, a zTXt bomb",
           {"empty", "pdf_header_junk", "pdf_truncated_half", "png_bad_crc_IDAT", "png_ztxt_bomb"} <= set(cases),
           sorted(cases))
    for name, path in sorted(cases.items()):
        out_dir = os.path.join(tmp, "out_" + name)
        dst = os.path.join(out_dir, "result" + os.path.splitext(path)[1])
        before = _sha256(path)
        t0 = time.time()
        try:
            res = clean_file(path, dst)
        except Exception as exc:  # noqa: BLE001 -- this is exactly what we test
            _check("%s: the exception did NOT escape" % name, False, "%s: %s" % (type(exc).__name__, exc))
            continue
        dt = time.time() - t0
        _SEEN.append(("pathology/" + name, res))
        _check("%s: a DispatchResult came back, quickly (%.1f s)" % (name, dt), isinstance(res, DispatchResult) and dt < 30, dt)
        _check("%s: the input was not modified (sha256)" % name, _sha256(path) == before)
        if name in must:
            _check("%s: ok=False" % name, not res.ok, "ok=True, errors=%s" % res.errors)
            _check("%s: errors are non-empty -- the reason is named" % name, bool(res.errors))
        if not res.ok:
            msg = " ".join(res.errors)
            _check("%s: the output file was NOT created" % name, not os.path.exists(dst))
            _check("%s: no temporary files (.clean_*, .part) are left" % name, not _no_leftovers(out_dir),
                   _no_leftovers(out_dir))
            _check("%s: the errors have no directory path and no file name" % name,
                   tmp not in msg and os.path.basename(path) not in msg, msg)
            _check("%s: no cleanliness claimed, no actions" % name,
                   not res.clean and res.critical_after == -1 and not res.actions)
            sent = os.path.join(tmp, "sentinel_" + name)
            _write(sent, b"old")
            r2 = clean_file(path, sent)
            _check("%s: an existing output file is untouched on a refusal" % name,
                   not r2.ok and _read(sent) == b"old")
        else:
            _check("%s: a file was produced -- dst exists and the result is confirmed by "
                   "re-inspection" % name,
                   os.path.isfile(dst) and res.verified, res.errors)

    # sweep: any single PNG corruption (a bit flip anywhere or a truncation) -> refusal, not a silently broken output
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
            problems.append("flip %d: exception %s" % (i, type(exc).__name__))
            continue
        runs += 1
        if res.ok or os.path.exists(dst) or _sha256(src) != hashlib.sha256(bad).hexdigest():
            problems.append("flip %d: ok=%s dst=%s" % (i, res.ok, os.path.exists(dst)))
    for cut in range(1, len(png), 13):
        src = _write(os.path.join(tmp, "cut.png"), png[:cut])
        dst = os.path.join(tmp, "cut_out", "o.png")
        try:
            res = clean_file(src, dst)
        except Exception as exc:  # noqa: BLE001
            problems.append("cut %d: exception %s" % (cut, type(exc).__name__))
            continue
        runs += 1
        if res.ok or os.path.exists(dst):
            problems.append("cut %d: ok=%s dst=%s" % (cut, res.ok, os.path.exists(dst)))
    _check("PNG: all %d single corruptions (bit flip, truncation) rejected, no exceptions, no "
           "file" % runs,
           not problems, problems[:5])

    # sweep: a PDF truncated at any point -> either a refusal or a fully valid output with one %%EOF
    problems, runs = [], 0
    for cut in sorted(set(list(range(9, len(pdf), 37)) + list(range(len(pdf) - 30, len(pdf))))):
        src = _write(os.path.join(tmp, "cutp.pdf"), pdf[:cut])
        dst = os.path.join(tmp, "cutp_out", "o.pdf")
        if os.path.exists(dst):
            os.remove(dst)
        try:
            res = clean_file(src, dst)
        except Exception as exc:  # noqa: BLE001
            problems.append("cut %d: exception %s" % (cut, type(exc).__name__))
            continue
        runs += 1
        if not res.ok:
            if os.path.exists(dst):
                problems.append("cut %d: refused, but a file was created" % cut)
            continue
        try:
            with pikepdf.open(dst) as q:
                good = len(q.pages) == 1 and _read(dst).count(b"%%EOF") == 1
        except Exception:  # noqa: BLE001
            good = False
        if not good:
            problems.append("cut %d: ok=True, but the output is invalid" % cut)
    _check("PDF: at all %d truncation points -- a refusal or a fully valid output" % runs,
           not problems, problems[:5])


# ===========================================================================
# 15. Honest verdicts: the check doesn't take the cleaner's word for it
# ===========================================================================


@_with_tmp
def test_15_honest_verdicts(tmp):
    path = fixtures.make_pdf(tmp)[0]

    # verify=False: a file is produced, but cleanliness can't be claimed
    res, dst = _clean("pdf_noverify", tmp, path, STEALTH, verify=False)
    if res is not None:
        text = cleanreport.render_plain(res) + "\n".join(cleanreport.render_telegram(res))
        _check("verify=False: ok, but clean=False, verified=False, critical_after=-1",
               res.ok and not res.clean and not res.verified and res.critical_after == -1)
        _check("verify=False: the report says \"RESULT NOT VERIFIED\" and doesn't declare the "
               "file clean",
               "NOT VERIFIED" in text and "CLEAN: re-inspection" not in text)

    # keep: a value kept on request isn't hidden but shown
    res, dst = _clean("pdf_keep", tmp, path, STEALTH, keep={"/Author"})
    if res is not None and _check("keep: ok and verified", res.ok and res.verified, res.errors):
        _check("keep: /Author physically stayed in the file (the request was honored)", bool(_leaked(dst, [fixtures.PDF_AUTHOR])))
        _check("keep: the dispatcher does NOT declare the file clean and names the kept value",
               not res.clean and any(fixtures.PDF_AUTHOR in sv.value for sv in res.survived),
               [(s.label, s.value) for s in res.survived])
        _check("keep: the report doesn't call the file clean",
               "CLEAN: re-inspection" not in cleanreport.render_plain(res))

    # swapping the cleaners: the documented core.clean._CLEANERS seam
    saved = dict(clean_mod._CLEANERS)
    try:
        def liar(s, d, prof, keep, force):          # copies the file as is and swears it's clean
            shutil.copyfile(s, d)
            r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
            r.ok = True
            r.act(Act.REMOVED, "/Info", "/Author", "-", "")
            return r

        clean_mod._CLEANERS["pdf"] = liar
        res, dst = _clean("pdf_liar", tmp, path, STEALTH)
        if res is not None:
            text = cleanreport.render_plain(res) + "\n".join(cleanreport.render_telegram(res))
            _check("lying cleaner: the leak is not removed -- survived is not empty, clean=False",
                   res.ok and res.verified and bool(res.survived) and not res.clean, res.survived)
            _check("lying cleaner: the report doesn't call the file clean", "CLEAN: re-inspection" not in text)

        def boom(s, d, prof, keep, force):          # writes half the file and crashes
            with open(d, "wb") as fh:
                fh.write(b"%PDF-1.4 half")
            raise RuntimeError("boom")

        clean_mod._CLEANERS["pdf"] = boom
        res, dst = _clean("pdf_boom", tmp, path, STEALTH)
        if res is not None:
            _check("cleaner exception: ok=False, no file, no garbage",
                   not res.ok and not os.path.exists(dst) and not _no_leftovers(os.path.dirname(dst)),
                   (res.errors, os.listdir(os.path.dirname(dst)) if os.path.isdir(os.path.dirname(dst)) else None))

        png_dir = os.path.join(tmp, "png_for_swap")
        os.makedirs(png_dir)
        png_path = fixtures.make_png(png_dir)[0]

        def other_format(s, d, prof, keep, force):  # the "cleaning" produced something that isn't a PDF
            shutil.copyfile(png_path, d)
            r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
            r.ok = True
            return r

        clean_mod._CLEANERS["pdf"] = other_format
        res, dst = _clean("pdf_other_format", tmp, path, STEALTH)
        if res is not None:
            _check("an output of a different format is not handed over: ok=False, no file",
                   not res.ok and not os.path.exists(dst), res.errors)
    finally:
        clean_mod._CLEANERS.clear()
        clean_mod._CLEANERS.update(saved)
    _check("seams: core.clean._CLEANERS restored after the swaps", clean_mod._CLEANERS == saved)


# ===========================================================================
# 97. What the CLI really prints: the dispatcher's summary doesn't contradict its own numbers
# ===========================================================================


@_with_tmp
def test_97_cli_verdict_text(tmp):
    """python -m core.clean prints the dispatcher's summary ON TOP and the
    cleanreport report below. That's the text the user reads, so the summary
    must not say "some of the original values remained" when the same printout
    says "survived cleaning: 0" (the case: the inspector couldn't parse part of
    the original, so the proof is incomplete, but nobody saw a leak)."""
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
        _check("CLI %s: the input was not modified (sha256)" % os.path.basename(src), _sha256(src) == before)
        return rc, buf.getvalue(), out

    bomb, _rows = _live_bomb_png(tmp)
    rc, text, out = run_cli(bomb)
    survived_zero = "survived cleaning: 0" in text
    _check("CLI, zTXt bomb: the file is produced, cleanliness not proven -> exit code 2", rc == 2 and os.path.isfile(out), rc)
    _check("CLI, zTXt bomb: the printout has \"survived cleaning: 0\" (precondition)", survived_zero, text[:400])
    _check("CLI, zTXt bomb: the summary doesn't claim \"some of the original values remained\" "
           "with zero survivors",
           not (survived_zero and "some of the original values remained" in text), text[:300])

    path = fixtures.make_pdf(tmp)[0]
    rc, text, out = run_cli(path)
    _check("CLI, ordinary PDF: exit code 0 and the summary \"CLEANED: no original value\"",
           rc == 0 and "CLEANED: no original value" in text and "NOT FULLY" not in text, (rc, text[:200]))

    # Since phase 2 docx is cleaned. A refusal with a phase number remains for
    # legacy OLE2 (phase 4): it can only be regenerated through LibreOffice.
    rc, text, out = run_cli(fixtures.make_docx(tmp)[0])
    _check("CLI, docx: exit code 0, the file is produced, \"CLEANED\"",
           rc == 0 and os.path.isfile(out) and "CLEANED" in text and "NOT CLEANED" not in text,
           (rc, text[:300]))

    rc, text, out = run_cli(fixtures.make_doc(tmp)[0])
    _check("CLI, legacy .doc: exit code 1, no file, \"NOT CLEANED\", phase 4 is named",
           rc == 1 and not os.path.exists(out) and "NOT CLEANED" in text and 4 in _phases(text),
           (rc, text[:300]))


# ===========================================================================
# 20. Phase 2: OOXML cleaning
# ===========================================================================

_WORD_EPOCH = (1980, 1, 1, 0, 0, 0)   # what Office sets on all ZIP entries


@_with_tmp
def test_20_ooxml_cleaning(tmp):
    """Phase 2: docx/xlsx/pptx cleaning on top of a byte-exact ZIP rebuild.

    Replaces the checks that used to claim these formats are REFUSED. The main
    thing here is not only "no leaks are left" but also "the package stayed an
    Office package": entry order, DOS timestamps, Application. A naive rebuild
    breaks them, and a Word file turns into a library-made file that our own
    inspector recognizes with the ai signal.
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
            _check("%s: the file is produced and recognized as clean" % tag,
                   res.ok and res.clean, (res.ok, res.errors[:1]))
            _check("%s: no original value survived" % tag,
                   not res.survived, [s_.value for s_ in res.survived][:3])

            if not os.path.isfile(dst):
                continue
            with zipfile.ZipFile(dst) as z:
                _check("%s: the output opens, testzip passes" % tag, z.testzip() is None)
                out_order = [i.filename for i in z.infolist()]
                out_ts = {i.filename: i.date_time for i in z.infolist()}
            _check("%s: [Content_Types].xml stayed the first entry" % tag,
                   out_order and out_order[0] == "[Content_Types].xml", out_order[:2])
            kept = [n for n in src_order if n in set(out_order)]
            _check("%s: the order of the surviving entries didn't change" % tag, out_order == kept,
                   (out_order[:4], kept[:4]))
            # ZIP timestamps are a leak channel in themselves (docs/METADATA.md): Word
            # sets 1980-01-01 on all entries, libraries the real save time. So "keep
            # them as they were" is right ONLY for a package from Word; for a package
            # with real times, keeping them would mean leaving the leak, and the cleaner
            # has to bring everything to 1980-01-01. Exactly one thing is forbidden:
            # carrying the real time over into the output.
            real_out = [n for n, t in out_ts.items() if tuple(t) != _WORD_EPOCH]
            if all(tuple(t) == _WORD_EPOCH for t in src_ts.values()):
                bad_ts = [n for n in out_ts if n in src_ts and out_ts[n] != src_ts[n]]
                _check("%s: package from Word -- the entries' DOS timestamps are untouched" % tag,
                       not bad_ts, bad_ts[:3])
            else:
                _check("%s: the entries' real time is brought to 1980-01-01 (otherwise the save "
                       "time leaks through the container)" % tag, not real_out, real_out[:3])
            _check("%s: no real save time in the output timestamps" % tag,
                   not real_out, real_out[:3])

            after = inspect_file(dst)
            app = [f.value for f in after.findings if "Application" in (f.label or "")]
            # If the original had no Application (the xlsx fixture comes without
            # docProps/app.xml), there's nothing to keep and it must not appear: an
            # added Application is a forged provenance.
            if profile is STEALTH:
                _check("%s: Application kept as is -- that's the truth about the file (the "
                       "original %s)" % (tag, "had one" if src_had_app else "had none"),
                       bool(app) == src_had_app, app)
            # Checking "the part is gone" only makes sense if it WAS there: otherwise the
            # assertion holds vacuously and guards nothing.
            # make_docx has people.xml, only make_pptx has printerSettings.
            if "word/people.xml" in src_order:
                _check("%s: no word/people.xml with userId is left" % tag,
                       "word/people.xml" not in out_order)
            src_prn = [n for n in src_order if "printerSettings" in n]
            if src_prn:
                _check("%s: no printerSettings with the printer name (there were %d) is left"
                       % (tag, len(src_prn)),
                       not [n for n in out_order if "printerSettings" in n], out_order)
                raw = _read(dst)
                _check("%s: the printer name is not left in the raw output bytes either" % tag,
                       fixtures.PPTX_PRINTER.encode("utf-16le") not in raw
                       and fixtures.PPTX_PRINTER.encode("utf-8") not in raw)

        # Honesty about being undetectable: for OOXML stealth the file is
        # recognizable as scrubbed, and the report MUST say so. Empty dc:creator and
        # cp:lastModifiedBy together are a sign by themselves; that's the limit of
        # the format, not a defect, see docs/ROADMAP.md.
        res, dst = _clean(name + "_warn", tmp, src, STEALTH)
        if res is None:
            continue
        kinds = [k for k, _d in getattr(res, "new_signals", [])]
        if kinds:
            plain = cleanreport.render_plain(res)
            tg = "\n".join(cleanreport.render_telegram(res))
            _check("%s: the report warns that the file is recognizable as scrubbed" % name,
                   "RECOGNIZABLE AS SCRUBBED" in plain and "RECOGNIZABLE AS SCRUBBED" in tg,
                   plain[:200])
            _check("%s: the warning names Regenerate as the solution" % name,
                   "Regenerate" in plain, plain[:200])


@_with_tmp
def test_21_regenerate_scrubs_properties(tmp):
    """REGENERATE must not only recreate the file but also clean the properties.

    Regression test for a defect found: clean_regen did exactly one action (the
    conversion through LibreOffice) and considered the job done. But
    LibreOffice with --convert-to CARRIES OVER the document properties: the
    author, who saved it, the organization and a custom MSIP label with an
    email made it into the output. The RemovePersonalInfoOnSaving option in the
    profile doesn't apply to conversion. The user got a file they considered
    maximally cleaned, with their name and email inside: the worst possible
    outcome for this project.

    Without LibreOffice on the machine the check is skipped: the mode then
    refuses honestly, and that's covered separately.
    """
    from core.clean_regen import find_soffice

    if not find_soffice():
        _check("regenerate: LibreOffice not found -- check skipped", True,
               "not a failure: in this case the mode must refuse honestly")
        return

    src, _exp = fixtures.make_docx(tmp)
    res, dst = _clean("regen", tmp, src, Profile.REGENERATE)
    if res is None:
        return

    _check("regenerate: the file is produced", res.ok and os.path.isfile(dst),
           (res.ok, res.errors[:1]))
    _check("regenerate: NOT A SINGLE original value survived",
           not res.survived,
           [getattr(x, "value", "") for x in res.survived][:4])
    _check("regenerate: one conversion isn't enough -- there are property cleaning actions",
           res.count() > 1, res.count())
    _check("regenerate: invariant -- the input was not modified", True)


def test_22_oracle_short_values():
    """Regression test for a critical review finding: the oracle was blind to short values.

    survived_values skipped everything shorter than MIN_VALUE_LEN, and a lying
    cleaner (a plain copy of the file) got clean=True and a "CLEANED" verdict
    on a document with dc:creator "Иван". "Олег", "Анна", "Bob" too. The
    project's central claim ("no original value occurs in the output") didn't
    work on the most common class of data: names.

    The threshold was there for a reason: the substring "ru" would be found
    inside "trust". So short values are compared on word boundaries instead of
    being skipped.
    """
    from core.clean import survived_values
    from core.model import Report, Risk

    def rep(value, risk=Risk.IDENTITY):
        r = Report(path="synthetic", fmt="docx")
        r.add(risk, "docProps/core.xml", "dc:creator", value)
        return r

    for name in ("Иван", "Олег", "Анна", "Bob", "Ivan"):
        _check("oracle: the short name %r is not lost" % name,
               len(survived_values(rep(name), rep(name))) == 1, name)

    for short, longer in (("ru", "trust"), ("1.7", "21.75"),
                          ("Иван", "Иванов"), ("ru", "Другое")):
        _check("oracle: %r inside %r -- NOT a match" % (short, longer),
               not survived_values(rep(short), rep(longer)), (short, longer))

    _check("oracle: a short value as a separate word is found",
           len(survived_values(rep("Иван"), rep("Автор: Иван"))) == 1)
    _check("oracle: a long value is still searched for as a substring",
           len(survived_values(rep("Иванов Иван"),
                               rep("Подготовил Иванов Иван, отдел"))) == 1)

    # Presence markers are report markup, not data: "present" in the original and
    # "present" in the output is not a leak that survived cleaning.
    for marker in ("present", "no", "yes", "true"):
        _check("oracle: the marker %r is not counted as a leak" % marker,
               not survived_values(rep(marker), rep(marker)), marker)


def _pdf_with_photo(tmp, jpg):
    """A PDF "as if from img2pdf": the JPEG sits as a /DCTDecode stream byte for byte, with EXIF."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    img = pikepdf.Stream(pdf, _read(jpg))
    img.update({"/Type": pikepdf.Name("/XObject"), "/Subtype": pikepdf.Name("/Image"), "/Width": 8,
                "/Height": 8, "/ColorSpace": pikepdf.Name("/DeviceGray"), "/BitsPerComponent": 8,
                "/Filter": pikepdf.Name("/DCTDecode")})
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    # The page has to DRAW the image: QPDF would throw out an unused one as
    # garbage, and the test would prove cleanliness by deletion, not by cleaning.
    pdf.pages[0].Contents = pdf.make_stream(b"q 200 0 0 200 0 0 cm /Im0 Do Q")
    path = os.path.join(tmp, "scan.pdf")
    pdf.save(path)
    return path


@_with_tmp
def test_23_pdf_photo_exif(tmp):
    """Regression test: the EXIF of a photo inside a PDF survived cleaning, while the report said "clean".

    The PDF inspector didn't look into image streams, so the oracle had nothing
    to compare: the home GPS and the camera serial number went to the recipient
    with a "CLEANED" verdict. PDFs made from photos (img2pdf, scanner apps) are
    a common case."""
    jpg, _ = fixtures.make_jpeg(tmp)
    src = _pdf_with_photo(tmp, jpg)
    rep = inspect_file(src)
    _check("pdf+photo: the inspector sees GPS in the image",
           any("image" in f.location and "GPS" in f.location for f in rep.findings))
    _check("pdf+photo: the inspector sees the serial number in the image",
           any(fixtures.JPEG_SERIAL in f.value for f in rep.findings))
    for profile in PROFILES:
        res, dst = _clean("pdf+photo", tmp, src, profile)
        if res is None or not _check("pdf+photo/%s: the file is produced" % _pname(profile), res.ok, res.errors[:2]):
            continue
        _check("pdf+photo/%s: clean (the oracle found nothing)" % _pname(profile), res.clean,
               [(s.label, s.value) for s in res.survived][:4])
        raw = _read(dst)
        _check("pdf+photo/%s: the serial number and the author are not in the output bytes" % _pname(profile),
               fixtures.JPEG_SERIAL.encode() not in raw and fixtures.JPEG_ARTIST.encode() not in raw)
        with pikepdf.open(dst) as q:
            imgs = [o.read_raw_bytes() for o in q.objects
                    if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == "/Image"]
        _check("pdf+photo/%s: the image is in place and is still a JPEG" % _pname(profile),
               len(imgs) == 1 and imgs[0][:2] == b"\xff\xd8" and imgs[0].rstrip()[-2:] == b"\xff\xd9",
               len(imgs))


SHARED_EDITOR = "Скрытый Участник"


def _xlsx_shared(tmp):
    """An xlsx with a shared workbook log, the way Excel writes it: revisionHeaders
    (userName, dateTime), userNames (everyone who opened it), revisionLog;
    relationships and Override in place."""
    src, _ = fixtures.make_xlsx(tmp)
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    ct = "application/vnd.openxmlformats-officedocument.spreadsheetml."
    parts = {
        "xl/revisions/revisionHeaders.xml":
            '<headers xmlns="%s" xmlns:r="%s" guid="{11111111-0000-0000-0000-000000000001}">'
            '<header guid="{11111111-0000-0000-0000-000000000002}" dateTime="2024-03-01T10:00:00" '
            'maxSheetId="2" userName="%s" r:id="rId1"><sheetIdMap count="1"><sheetId val="1"/>'
            '</sheetIdMap></header></headers>' % (ns, rel[:-1], SHARED_EDITOR),
        "xl/revisions/_rels/revisionHeaders.xml.rels":
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="%srevisionLog" Target="revisionLog1.xml"/></Relationships>' % rel,
        "xl/revisions/revisionLog1.xml": '<revisions xmlns="%s"/>' % ns,
        "xl/revisions/userNames.xml":
            '<users xmlns="%s" count="1"><userInfo guid="{11111111-0000-0000-0000-000000000003}" '
            'name="%s" id="1" dateTime="2024-03-01T10:00:00"/></users>' % (ns, SHARED_EDITOR),
    }
    out = os.path.join(tmp, "shared.xlsx")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "[Content_Types].xml":
                data = data.replace(b"</Types>", (
                    '<Override PartName="/xl/revisions/revisionHeaders.xml" ContentType="%srevisionHeaders+xml"/>'
                    '<Override PartName="/xl/revisions/revisionLog1.xml" ContentType="%srevisionLog+xml"/>'
                    '<Override PartName="/xl/revisions/userNames.xml" ContentType="%suserNames+xml"/></Types>'
                    % (ct, ct, ct)).encode())
            elif info.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"</Relationships>", (
                    '<Relationship Id="rIdRv1" Type="%srevisionHeaders" Target="revisions/revisionHeaders.xml"/>'
                    '<Relationship Id="rIdRv2" Type="%susernames" Target="revisions/userNames.xml"/>'
                    '</Relationships>' % (rel, rel)).encode())
            zout.writestr(info, data)
        for name, text in parts.items():
            zout.writestr(name, text.encode("utf-8"))
    return out


@_with_tmp
def test_24_xlsx_shared_workbook(tmp):
    """Regression test: the shared workbook log (xl/revisions) with the names of
    all participants survived cleaning and wasn't visible to the inspector."""
    src = _xlsx_shared(tmp)
    _check("xlsx shared workbook: the inspector sees the participant",
           any(SHARED_EDITOR in f.value and f.risk is Risk.IDENTITY for f in inspect_file(src).findings))
    for profile in PROFILES:
        res, dst = _clean("xlsx-shared", tmp, src, profile)
        if res is None or not _check("xlsx shared workbook/%s: the file is produced" % _pname(profile), res.ok, res.errors[:2]):
            continue
        _check("xlsx shared workbook/%s: clean" % _pname(profile), res.clean,
               [(s.label, s.value) for s in res.survived][:4])
        with zipfile.ZipFile(dst) as z:
            names = z.namelist()
            blob = b"".join(z.read(n) for n in names)
        _check("xlsx shared workbook/%s: xl/revisions removed, no references to it left" % _pname(profile),
               not any(n.startswith("xl/revisions/") for n in names) and b"revisions/" not in blob,
               [n for n in names if "revision" in n])
        _check("xlsx shared workbook/%s: the participant's name is not in the bytes" % _pname(profile),
               SHARED_EDITOR.encode("utf-8") not in blob)


@_with_tmp
def test_25_rtf_upr_author(tmp):
    """Regression test: a non-Latin author in {\\upr{\\author ANSI}{\\*\\ud{\\author Unicode}}}
    survived cleaning: the subgroup is called upr, and parsing by name didn't see it."""
    name = "Скрытый Автор"
    ansi = "".join("\\'%02x" % b for b in name.encode("cp1251"))
    uni = "".join("\\u%d?" % ord(ch) for ch in name)
    rtf = ("{\\rtf1\\ansi\\ansicpg1251\\deff0{\\info{\\upr{\\author %s}{\\*\\ud{\\author %s}}}"
           "{\\creatim\\yr2024\\mo3\\dy1\\hr10\\min0}}{\\*\\generator Microsoft Word 11.0.0000;}"
           "Hello\\par}" % (ansi, uni))
    src = _write(os.path.join(tmp, "upr.rtf"), rtf.encode("latin-1"))
    _check("rtf \\upr: the inspector sees the author",
           any(f.value == name and f.risk is Risk.IDENTITY for f in inspect_file(src).findings))
    for profile in PROFILES:
        res, dst = _clean("rtf-upr", tmp, src, profile)
        if res is None or not _check("rtf \\upr/%s: the file is produced" % _pname(profile), res.ok, res.errors[:2]):
            continue
        out = _read(dst).decode("latin-1")
        _check("rtf \\upr/%s: clean" % _pname(profile), res.clean,
               [(s.label, s.value) for s in res.survived][:4])
        _check("rtf \\upr/%s: neither the ANSI nor the Unicode copy of the name" % _pname(profile),
               ansi not in out and uni not in out and "\\upr" not in out, out[:200])
        _check("rtf \\upr/%s: the document text is intact" % _pname(profile), "Hello\\par" in out)


def test_26_report_residual_honest():
    """Regression test: with a critical remainder, the report always said "these
    are NEW values", even when the dispatcher (residual) knew the value was in
    the original too."""
    def mk(residual):
        return DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, survived=[],
                              critical_before=3, critical_after=len(residual), residual=residual)

    old = cleanreport.render_plain(mk([("/Root", "Signature field", False), ("trailer", "/ID[0]", True)]))
    old = " ".join(old.split())   # the report wraps lines by width
    _check("report: an old value in the remainder is not called new",
           "NEW values" not in old and "Signature field" in old, old[:300])
    new = cleanreport.render_plain(mk([("trailer", "/ID[0]", True)]))
    _check("report: only new ones -- and that's what it says", "NEW values" in new, new[:300])


def _repack(src, out, edits=(), add=()):
    """A copy of a ZIP package with bytes replaced in parts and parts added."""
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zi.infolist():
            data = zi.read(info.filename)
            for part, old, new in edits:
                if info.filename == part:
                    assert old in data, (part, old)
                    data = data.replace(old, new, 1)
            zo.writestr(info, data)
        for part, data in add:
            zo.writestr(part, data)
    return out


def _values(rep):
    return " | ".join(f.value for f in rep.findings)


@_with_tmp
def test_28_dispatcher_and_sniff_regressions(tmp):
    """Review regressions: the new_signals field, a ZIP with an uncompressed PDF, directory entries, path scrubbing."""
    from core.model import Report as _Rep

    # new_signals was assigned on the fly, and the dataclass field got lost in 89121fc:
    # --json didn't have it, and on a refusal the signals of the file not handed over remained.
    _check("new_signals is a real DispatchResult field (visible in asdict / --json)",
           "new_signals" in dataclasses.asdict(DispatchResult("a", "b", "pdf", "stealth")))
    res = clean_file(os.path.join(tmp, "missing.pdf"), os.path.join(tmp, "o.pdf"))
    _check("on a refusal new_signals and detect are empty", res.new_signals == [] and res.detect == "")

    # A ZIP whose first entry is an uncompressed PDF: "%PDF-" in the first hundred bytes.
    pdf, _ = fixtures.make_pdf(tmp)
    zpath = os.path.join(tmp, "pdfs.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        z.write(pdf, "a.pdf")
        z.write(pdf, "b.pdf")
    _check("a ZIP with uncompressed PDFs is detected as zip, not pdf", sniff.sniff(zpath) == "zip",
           sniff.sniff(zpath))
    junk = _write(os.path.join(tmp, "junk.pdf"), b"garbage line\n" + _read(pdf))
    _check("a PDF with garbage before %PDF- is still pdf", sniff.sniff(junk) == "pdf", sniff.sniff(junk))

    # Directory entries in word/media/: the empty body produced "the file is
    # empty", and cleanliness was never proven (gaps).
    docx, _ = fixtures.make_docx(tmp)
    dirs = _repack(docx, os.path.join(tmp, "dirs.docx"),
                   add=(("word/media/", b""), ("word/embeddings/", b"")))
    rep = inspect_file(dirs)
    _check("a directory entry doesn't produce a \"file is empty\" error",
           not any("empty" in e for e in rep.errors), rep.errors)
    res, _dst = _clean("docx-dirs", tmp, dirs)
    if res is not None:
        _check("docx with directory entries: cleanliness is proven", res.ok and res.clean,
               (res.errors[:2], res.gaps[:2]))

    # Path scrubbing in errors must not mangle part names and URLs.
    r = _Rep(path="x", fmt="docx")
    r.err("word/media/image1.png: broken image")
    r.err("see http://example.com/x")
    r.err("failed to open C:\\Users\\owner\\AppData\\Local\\Temp\\x.bin")
    r.err("failed to open '/home/owner/tmp/x.bin'")
    _check("scrub: the relative part name and the URL are intact, absolute paths are hidden",
           r.errors[0].startswith("word/media/image1.png") and "http://example.com" in r.errors[1]
           and "owner" not in r.errors[2] and "owner" not in r.errors[3], r.errors)


@_with_tmp
def test_29_pdf_info_bad_key_and_broken_xmp(tmp):
    """Fuzzing regressions: a non-UTF-8 /Info key crashed the /Info section (a
    TypeError in get()), and an XMP with a Latin-1 byte wasn't parsed at all:
    the author in the XMP survived cleaning."""
    src = os.path.join(tmp, "badkey.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.trailer.Info = pdf.make_indirect(pikepdf.Dictionary(Author=pikepdf.String("Bad Key Author")))
    pdf.save(src, object_stream_mode=pikepdf.ObjectStreamMode.disable, compress_streams=False)
    raw = _read(src)
    _write(src, raw.replace(b"/Author", b"/Au\x9dhor", 1))     # same length: xref intact
    res, dst = _clean("pdf-key-not-utf8", tmp, src, PARANOID)
    if res is not None:
        _check("pdf: a non-UTF-8 /Info key -- the section doesn't crash",
               res.ok and not res.count(Act.FAILED) and not any("/Info" in e for e in res.errors),
               res.errors[:2])
        _check("pdf: the value under the broken key is removed",
               res.ok and "Bad Key Author" not in _values(inspect_file(dst)))

    xmp = (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
           b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           b'<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
           b'xmlns:xmp="http://ns.adobe.com/xap/1.0/"><dc:creator><rdf:Seq><rdf:li>Latin Xmp Creator</rdf:li>'
           b'</rdf:Seq></dc:creator><xmp:CreatorTool>Caf\xe9 Writer</xmp:CreatorTool></rdf:Description>'
           b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
    src = os.path.join(tmp, "latin1xmp.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.Root.Metadata = pdf.make_stream(xmp)
    pdf.save(src, fix_metadata_version=False)   # otherwise pikepdf itself replaces a broken XMP with an empty one
    _check("pdf: the inspector parses an XMP with a Latin-1 byte and sees the author",
           any(f.value == "Latin Xmp Creator" and f.risk is Risk.IDENTITY for f in inspect_file(src).findings))
    for profile in PROFILES:
        res, dst = _clean("pdf-xmp-latin1", tmp, src, profile)
        if res is None:
            continue
        _check("pdf XMP Latin-1/%s: clean, the author is not in the output" % _pname(profile),
               res.ok and res.clean and "Latin Xmp Creator" not in _values(inspect_file(dst)),
               (res.errors[:2], [s.value for s in res.survived]))


def test_30_regen_defang_links():
    """Regression test: defanging before LibreOffice only caught http/ftp in the
    raw bytes. Measured: LO opens a file via a file:// link (on Windows,
    file://server means SMB and the owner's NTLM hash), and "&#104;ttp://"
    bypassed the check and produced requests."""
    from core.clean_regen import _defang_bytes

    remote = (b'Target="http://x/a.png"', b'Target="&#104;ttp://x/a.png"',
              b'Target="file:///C:/a.png"', b'Target="file://srv/share/a.png"',
              b'Target="\\\\srv\\share\\a.png"', b'xlink:href="//srv/share/a.png"',
              b'src="C:\\Users\\a.png"', b"Target=' HTTPS://x'", b'o:href="smb://srv/a"')
    local = (b'Target="media/image1.png"', b'Target="/word/document.xml"',
             b'Target="../customXml/item1.xml"', b'xlink:href="vnd.sun.star.GraphicObject:1000"',
             b'xlink:href="Pictures/1.png"', b'r:link="rId9"', b'Target="#_Toc1"',
             b'TargetMode="External"')
    for raw in remote:
        new, n = _defang_bytes(raw)
        _check("defang: %r defanged" % raw, n == 1 and new.endswith((b'""', b"''")), new)
    for raw in local:
        new, n = _defang_bytes(raw)
        _check("defang: %r left untouched" % raw, n == 0 and new == raw, new)


@_with_tmp
def test_31_regenerate_rtf_scrubbed(tmp):
    """Regression test: after regenerating RTF -> RTF the second pass didn't run
    ("format isn't supported by the second pass"), while LibreOffice carries
    \\info with the author over as is."""
    from core.clean_regen import find_soffice

    if not find_soffice():
        _check("regenerate rtf: LibreOffice not found -- check skipped", True)
        return
    src, _exp = fixtures.make_rtf(tmp)
    res, _dst = _clean("regen-rtf", tmp, src, Profile.REGENERATE)
    if res is None:
        return
    _check("regenerate rtf: the second pass ran",
           not any("isn't supported by the second pass" in e for e in res.errors), res.errors[:2])
    _check("regenerate rtf: the file is produced and clean", res.ok and res.clean,
           [(s.label, s.value) for s in res.survived][:3])


@_with_tmp
def test_32_odf_template_and_broken(tmp):
    """Regression test: the ODF template path sits in the xlink:href of an empty
    meta:template. The inspector read the element text ("Template: (empty)"),
    the cleaner didn't touch the element: C:/Users/NAME/... went out in the
    "cleaned" file past the oracle."""
    src, _exp = fixtures.make_odt(tmp)
    links = (b'<meta:template xmlns:xlink="http://www.w3.org/1999/xlink" xlink:type="simple" '
             b'xlink:title="Blank > Odt" xlink:href="file:///C:/Users/tpl.owner/Templates/blank.ott" '
             b'meta:date="2024-01-01T10:00:00"/>'
             b'<meta:auto-reload xmlns:xlink="http://www.w3.org/1999/xlink" '
             b'xlink:href="file://fileserver01/share/x.odt" meta:delay="PT0S"></meta:auto-reload>')
    tpl = _repack(src, os.path.join(tmp, "tpl.odt"),
                  edits=(("meta.xml", b"<meta:generator>", links + b"<meta:generator>"),))
    tpl_f = [f for f in inspect_file(tpl).findings if f.label == "Template"]
    _check("odt: the inspector sees the template path from xlink:href",
           any("tpl.owner" in f.value and f.risk is Risk.ENVIRONMENT for f in tpl_f), tpl_f)
    for profile in PROFILES:
        res, dst = _clean("odt-template", tmp, tpl, profile)
        if res is None or not res.ok:
            _check("odt template/%s: the file is produced" % _pname(profile), False, res and res.errors[:2])
            continue
        with zipfile.ZipFile(dst) as z:
            meta = z.read("meta.xml")
        _check("odt template/%s: the path and the server are removed, the file is clean" % _pname(profile),
               res.clean and b"tpl.owner" not in meta and b"fileserver01" not in meta
               and b"<meta:generator>" in meta, ([s.value for s in res.survived], meta[:200]))

    # A broken ZIP entry is file corruption, not an "internal error".
    broken = os.path.join(tmp, "broken.odt")
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(broken, "w", zipfile.ZIP_STORED) as zo:
        for info in zi.infolist():
            info.compress_type = zipfile.ZIP_STORED
            zo.writestr(info, zi.read(info.filename))
    raw = _read(broken)
    _write(broken, raw.replace(b"Test ODT Fixture", b"Test ODT Fixturx", 1))   # the CRC won't match
    res = clean_file(broken, os.path.join(tmp, "broken_out.odt"))
    _check("odt with a broken entry: \"damaged\", not \"internal error\"",
           not res.ok and any("damaged" in e for e in res.errors)
           and not any("Internal" in e for e in res.errors), res.errors)


@_with_tmp
def test_34_odt_photo_exif(tmp):
    """Regression test: images from ODF Pictures/ stayed as they were, EXIF included.
    The inspector parses Pictures/ recursively, so any .odt with a photo got
    "NOT CLEAN" after cleaning: the photo's GPS, serial number and author
    survived cleaning."""
    jpg, _ = fixtures.make_jpeg(tmp)
    src, _exp = fixtures.make_odt(tmp)
    entry = (b'<manifest:file-entry manifest:full-path="Pictures/photo.jpg" '
             b'manifest:media-type="image/jpeg"/></manifest:manifest>')
    src = _repack(src, os.path.join(tmp, "photo.odt"),
                  edits=(("META-INF/manifest.xml", b"</manifest:manifest>", entry),),
                  add=(("Pictures/photo.jpg", _read(jpg)),))
    rep = inspect_file(src)
    _check("odt+photo: the inspector sees the serial number in the image",
           any(fixtures.JPEG_SERIAL in f.value for f in rep.findings))
    for profile in PROFILES:
        res, dst = _clean("odt+photo", tmp, src, profile)
        if res is None or not _check("odt+photo/%s: the file is produced" % _pname(profile), res.ok, res.errors[:2]):
            continue
        _check("odt+photo/%s: clean (the oracle found nothing)" % _pname(profile), res.clean,
               [(s.label, s.value) for s in res.survived][:4])
        raw = _read(dst)
        with zipfile.ZipFile(dst) as z:
            pic = z.read("Pictures/photo.jpg")
            raw += pic   # the entry could be compressed: look at the decompressed bytes too
        _check("odt+photo/%s: the serial number and the author are not in the output bytes" % _pname(profile),
               fixtures.JPEG_SERIAL.encode() not in raw and fixtures.JPEG_ARTIST.encode() not in raw)
        _check("odt+photo/%s: the image is in place and is still a JPEG" % _pname(profile),
               pic[:2] == b"\xff\xd8" and pic.rstrip()[-2:] == b"\xff\xd9" and b"Exif" not in pic)


@_with_tmp
def test_33_ooxml_blind_spots(tmp):
    """Regressions: the cleaner removed x15ac:absPath and fileSharing in xlsx and
    ppt/authors.xml, but the inspector (the oracle, the user report) didn't see
    them; a w:docVar "Owner" with a full name counted as provenance, and Stealth
    left the name in the file."""
    xlsx, _ = fixtures.make_xlsx(tmp)
    xlsx = _repack(xlsx, os.path.join(tmp, "abs.xlsx"), edits=((
        "xl/workbook.xml", b"<sheets>",
        b'<fileSharing userName="Reserved Byuser" reservationPassword="CF1A"/>'
        b'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        b'<mc:Choice Requires="x15"><x15ac:absPath xmlns:x15ac="http://schemas.microsoft.com/office/'
        b'spreadsheetml/2010/11/ac" url="C:\\Users\\absuser.secret\\Documents\\"/></mc:Choice>'
        b'</mc:AlternateContent><sheets>'),))
    pptx, _ = fixtures.make_pptx(tmp)
    pptx = _repack(pptx, os.path.join(tmp, "authors.pptx"), add=((
        "ppt/authors.xml",
        b'<p188:authorLst xmlns:p188="http://schemas.microsoft.com/office/powerpoint/2018/8/main">'
        b'<p188:author id="{11111111-2222-3333-4444-555555555555}" name="Modern Commenter" '
        b'initials="MC" userId="modern.commenter@corp.example" providerId="AD"/></p188:authorLst>'),))
    docx, _ = fixtures.make_docx(tmp)
    docx = _repack(docx, os.path.join(tmp, "docvars.docx"), edits=((
        "word/settings.xml", b"</w:settings>",
        b'<w:docVars><w:docVar w:name="Owner" w:val="DocVar Ownername"/>'
        b'<w:docVar w:name="Stage" w:val="draft-stage-1"/></w:docVars></w:settings>'),))
    cases = ((xlsx, {"absuser.secret": Risk.ENVIRONMENT, "Reserved Byuser": Risk.IDENTITY}),
             (pptx, {"Modern Commenter": Risk.IDENTITY, "modern.commenter@corp.example": Risk.IDENTITY}),
             (docx, {"DocVar Ownername": Risk.IDENTITY}))
    for path, secrets in cases:
        tag = os.path.basename(path)
        rep = inspect_file(path)
        for value, risk in secrets.items():
            _check("%s: the inspector sees %r as %s" % (tag, value, risk.value),
                   any(value in f.value and f.risk is risk for f in rep.findings))
        res, dst = _clean(tag, tmp, path, STEALTH)
        if res is None or not res.ok:
            _check("%s: the file is produced" % tag, False, res and res.errors[:2])
            continue
        with zipfile.ZipFile(dst) as z:
            blob = b"".join(z.read(n) for n in z.namelist())
        left = [v for v in secrets if v.encode() in blob]
        _check("%s: Stealth is clean, the values are not in the bytes" % tag, res.clean and not left,
               (left, [s.value for s in res.survived][:3]))
        if tag == "docvars.docx":
            _check("docx: a harmless document variable is in place", b"draft-stage-1" in blob)


# 98. The dispatcher's and the report's verdicts don't contradict each other
# ===========================================================================


def test_27_find_soffice_other_os():
    """LibreOffice on macOS isn't on PATH: it lives inside the .app. There's no
    Mac here, so we swap out the environment, isfile and which, and check the
    search order itself."""
    from core import clean_regen as cr

    real = (cr.os.environ.get("ProgramFiles"), cr.os.environ.get("ProgramFiles(x86)"),
            cr.shutil.which, cr.os.path.isfile, cr.os.access, cr.glob.glob)
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    try:
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            cr.os.environ.pop(key, None)
        cr.shutil.which = lambda name: None
        cr.os.access = lambda p, mode: True
        cr.glob.glob = lambda pattern: []
        cr.os.path.isfile = lambda p: p == mac
        _check("soffice: macOS -- found inside LibreOffice.app", cr.find_soffice() == mac, cr.find_soffice())
        cr.glob.glob = lambda pattern: ["/opt/libreoffice7.6/program/soffice", "/opt/libreoffice24.8/program/soffice"]
        cr.os.path.isfile = lambda p: p.startswith("/opt/")
        _check("soffice: Linux /opt -- found", (cr.find_soffice() or "").startswith("/opt/libreoffice"),
               cr.find_soffice())
        cr.shutil.which = lambda name: "/usr/bin/soffice" if name == "soffice" else None
        _check("soffice: PATH beats the standard locations", cr.find_soffice() == "/usr/bin/soffice")
        cr.shutil.which = lambda name: None
        cr.glob.glob = lambda pattern: []
        cr.os.path.isfile = lambda p: False
        _check("soffice: nowhere -- None", cr.find_soffice() is None)
    finally:
        for key, val in (("ProgramFiles", real[0]), ("ProgramFiles(x86)", real[1])):
            if val is not None:
                cr.os.environ[key] = val
        cr.shutil.which, cr.os.path.isfile, cr.os.access, cr.glob.glob = real[2:]


def test_98_verdicts_agree():
    """The user sees both texts (the CLI prints the dispatcher's verdict on top and
    the cleanreport report below). They must not diverge in either direction."""
    seen = [(l, r) for l, r in _SEEN if isinstance(r, DispatchResult)]
    _check("results accumulated for cross-checking: %d" % len(seen), len(seen) > 50, len(seen))
    false_alarm = [l for l, r in seen if r.ok and r.clean and "NOT CLEAN" in cleanreport.render_plain(r)]
    _check("the file is PROVEN clean (dispatcher) -- the report doesn't shout \"NOT CLEAN\" (a "
           "false alarm)",
           not false_alarm, false_alarm[:6])
    overclaim = [l for l, r in seen if r.ok and not r.clean and "CLEAN: re-inspection" in cleanreport.render_plain(r)]
    _check("cleanliness NOT proven (dispatcher) -- the report doesn't call the file clean", not overclaim, overclaim[:6])
    refused_ok = [l for l, r in seen if not r.ok and _claims_clean(cleanreport.render_plain(r))]
    _check("the file is NOT produced -- the report never calls it clean", not refused_ok, refused_ok[:6])


# ===========================================================================
# 99. RENDERING: the Telegram limit, tags, escaping (runs LAST)
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
    """What's wrong with a Telegram HTML message: foreign tags, unbalanced tags,
    bare <, > and & in the text."""
    p = _TagCheck()
    try:
        p.feed(msg)
        p.close()
    except Exception as exc:  # noqa: BLE001
        return ["parser: %s" % exc]
    bad = list(p.bad) + ["<%s> not closed" % t for t in p.stack]
    text = re.sub(r"</?(?:%s)>" % "|".join(_TG_TAGS), "", msg)
    if "<" in text or ">" in text:
        bad.append("bare < or > in the text")
    if re.search(r"&(?!(?:amp|lt|gt|quot|#\d+|#x[0-9a-fA-F]+);)", text):
        bad.append("bare &")
    return bad


def _render_problems(res, hostile=()):
    """Telegram rendering problems of one result. hostile lists strings that must
    not appear raw in the messages."""
    try:
        msgs = cleanreport.render_telegram(res)
    except Exception as exc:  # noqa: BLE001
        return ["render_telegram raised %s: %s" % (type(exc).__name__, exc)], []
    out = []
    if not msgs or not all(isinstance(m, str) and m for m in msgs):
        out.append("empty list or empty message")
    for i, m in enumerate(msgs):
        n = _u16(m)
        if n > _TG_LIMIT:
            out.append("message %d: %d UTF-16 units > %d" % (i + 1, n, _TG_LIMIT))
        out += ["message %d: %s" % (i + 1, p) for p in _html_problems(m)]
        out += ["message %d: raw %r" % (i + 1, h) for h in hostile if h in m]
    return out, msgs


def _hostile_result(n_each=1, value="<script>&", ok=True):
    r = CleanResult(src="x", dst="y", fmt="pdf", profile="stealth", ok=ok)
    for _ in range(n_each):
        for act in Act:
            r.act(act, "loc " + value, "label " + value, "before " + value, "after " + value, "note " + value)
    r.errors.append("error " + value)
    r.critical_before, r.critical_after, r.sensitive_before, r.sensitive_after = 5, 0, 7, 3
    return r


@_with_tmp
def test_99_render(tmp):
    # --- 1. every cleaning result accumulated by all the tests ---
    problems, privacy, plain_bad, n_msgs, n_multi = [], [], [], 0, 0
    for label, res in _SEEN:
        probs, msgs = _render_problems(res, hostile=("<script",))
        n_msgs += len(msgs)
        n_multi += len(msgs) > 1
        problems += ["%s: %s" % (label, p) for p in probs]
        text = "\n".join(msgs)
        for secret in {os.path.basename(str(res.src)), os.path.basename(str(res.dst))} - {""}:
            if len(secret) > 6 and secret in text:
                privacy.append("%s: the file name got into the report" % label)
        try:
            plain, line = cleanreport.render_plain(res), cleanreport.summary_line(res)
            if not (isinstance(plain, str) and plain and isinstance(line, str) and line and "\n" not in line):
                plain_bad.append(label)
        except Exception as exc:  # noqa: BLE001
            plain_bad.append("%s: %s" % (label, type(exc).__name__))
    _check("render_telegram on all %d results (%d messages): each <= %d UTF-16 units, tags "
           "balanced and allowed, no bare <, >, &" % (len(_SEEN), n_msgs, _TG_LIMIT),
           not problems and len(_SEEN) > 50, problems[:5] or "too few results accumulated: %d" % len(_SEEN))
    _check("render_plain and summary_line on all results: non-empty, the summary is one line",
           not plain_bad, plain_bad[:5])
    _check("privacy: file names got into none of the report messages", not privacy, privacy[:5])

    # --- 2. a real PDF with the value "<script>&" in Author and XMP ---
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
        _check("%s: tags and message lengths are fine, no raw <script>" % label, not probs, probs[:4])
        _check("%s: the removed value is shown ESCAPED: &lt;script&gt;&amp;" % label,
               "&lt;script&gt;&amp;" in text, text[:300])

    # --- 3. a hostile result built by hand: "<script>&" in ALL fields ---
    res = _hostile_result()
    res.fmt, res.profile = "<script>&", "<b>&"
    res.errors.append("C:\\Users\\ivan\\secret.docx: <script>&")
    probs, msgs = _render_problems(res, hostile=("<script",))
    text = "\n".join(msgs)
    _check("hostile fields: tags balanced, messages within the limit, no raw <script>", not probs, probs[:4])
    _check("hostile fields: the value is escaped correctly (&lt;script&gt;&amp;)", "&lt;script&gt;&amp;" in text)
    _check("hostile fields: the path from the error text is cut out (the user won't get our "
           "machine's path)",
           "ivan" not in text and "secret.docx" not in text, text[-300:])

    # --- 4. volume: hundreds of entries, tripled escaping, surrogate pairs ---
    big = CleanResult(src="x", dst="y", fmt="pdf", profile="stealth", ok=True)
    for i in range(300):
        big.act(Act.REMOVED, "loc %d" % i, "Field %d" % i, "&" * 400, "<" * 400, "n" * 400)
    big.critical_before, big.critical_after = 5, 0
    probs, msgs = _render_problems(big)
    _check("300 entries of nothing but '&' and '<': several messages, each <= 4096, tags balanced",
           not probs and len(msgs) > 1, probs[:3] or "messages: %d" % len(msgs))
    _check("when split, the messages have part numbering [i/n]",
           len(msgs) > 1 and all(m.startswith("<b>[%d/%d]</b>" % (i + 1, len(msgs))) for i, m in enumerate(msgs)),
           [m[:20] for m in msgs[:3]])
    emoji = CleanResult(src="x", dst="y", fmt="pdf", profile="stealth", ok=True)
    for i in range(80):
        emoji.act(Act.REMOVED, "loc", "Field %d \U0001F600" % i, "\U0001F600" * 190, "", "\U0001F600" * 150)
    emoji.critical_before, emoji.critical_after = 5, 0
    probs, msgs = _render_problems(emoji)
    _check("emoji (2 UTF-16 units per character): each message <= 4096 UTF-16 units, not "
           "characters",
           not probs and len(msgs) > 1, probs[:3])
    _check("emoji: the check is not vacuous -- the messages' UTF-16 length is noticeably larger "
           "than the character count",
           len(msgs) > 1 and sum(_u16(m) for m in msgs) > 1.3 * sum(len(m) for m in msgs),
           (sum(_u16(m) for m in msgs), sum(len(m) for m in msgs)))

    # --- 5. not a result at all: the bot must neither crash nor lie ---
    for junk in (None, object(), CleanResult(src="x", dst="y", fmt="", profile="")):
        probs, msgs = _render_problems(junk)
        _check("render_telegram(%s): doesn't crash, there is a message, \"NOT CLEANED\"" % type(junk).__name__,
               not probs and "NOT CLEANED" in "\n".join(msgs), probs[:3] or msgs[:1])


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        print("-" * 78)
        print("=== %s ===" % fn.__name__)
        try:
            fn()
        except AssertionError as exc:
            _check("%s: no unhandled AssertionError" % fn.__name__, False, str(exc))
        except Exception as exc:  # noqa: BLE001 -- a test must not stop the others
            _check("%s: no unhandled exception" % fn.__name__, False,
                   "%s: %s" % (type(exc).__name__, exc))

    print("=" * 78)
    total = len(_RESULTS)
    failed = [r for r in _RESULTS if not r[1]]
    print("TOTAL: %d checks, %d failed" % (total, len(failed)))
    if failed:
        print("FAILED CHECKS:")
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
