# -*- coding: utf-8 -*-
"""Cleaning raster images WITHOUT re-encoding: JPEG, PNG, WebP, GIF.

Phase 1. Stdlib only (struct, zlib, binascii, hashlib, re): no Pillow, no
required exiftool.

THE MAIN PRINCIPLE: DON'T RE-ENCODE. The image is rebuilt as a CONTAINER: the
compressed data (JPEG entropy stream, PNG IDAT, WebP VP8/VP8L, GIF LZW) is
copied byte for byte. Re-encoding would lose quality and replace the JPEG
quantization tables, which fingerprint the encoder themselves: instead of the
camera's fingerprint the file would carry our library's. That's worse than
not cleaning.

The result is verified not by our own logic but by a second run of the
inspector (inspect_file(dst).critical == 0). verify_image() proves here that
the file isn't damaged: the structure is valid, dimensions are the same, the
compressed data matches bit for bit, the orientation is preserved.

DECISIONS (each deliberate):

* ORIENTATION. A phone stores the frame rotated in the file, and the correct
  rotation is given by EXIF Orientation (0x0112). Dropping the whole APP1
  would leave the picture on its side. Physical rotation requires
  re-encoding (forbidden). Chosen (a)+(c): if Orientation is 2..8, write a
  MINIMAL EXIF: one IFD, one tag, a 36-byte segment, byte order as in the
  source; everything else (GPS, serials, dates, MakerNote, thumbnail) goes.
  Orientation = 1 or missing: no EXIF at all (1 is the default anyway). Same
  for PNG (the eXIf chunk) and WebP (the EXIF chunk).
* The ICC PROFILE is STRUCTURAL: colors shift without it. STEALTH keeps it,
  PARANOID removes it with a warning (the profile name is often named after
  the monitor/printer). keep={"icc"} keeps the profile in PARANOID too.
* JFIF (APP0) stays in both profiles: pixel density is STRUCTURAL, and
  having neither EXIF nor JFIF stands out by itself. But the embedded JFIF
  thumbnail is cut off (Xthumbnail=Ythumbnail=0): it may predate retouching.
* Adobe APP14 is removed, EXCEPT when colors break without it: 4-component
  (CMYK/YCCK) or 3-component with transform=0 (RGB in JPEG). Then it isn't
  metadata but a decoding parameter: the 12 bytes are kept and marked KEPT.
* Quantization tables (DQT) can't be removed: Act.UNREMOVABLE.
* Data AFTER the end of the image (after EOI/IEND/RIFF/GIF trailer) is
  dropped: a classic hiding place, and the inspector counts it as
  ENVIRONMENT. MPF frames, Motion Photo video and the Ultra HDR gain map go
  along with it; that's the honest price.
* TIFF and HEIC: honest refusal (ok=False). TIFF metadata lives in the IFDs
  themselves, mixed with references to the data; HEIF's in iloc items;
  rebuilding without dedicated code risks damaging the file. Refusing is
  better than damage.
* Profiles: for images STEALTH and PARANOID both remove ALL metadata
  containers, PROVENANCE included (dates, Make/Model, Software): by the
  requirements dates and serials must disappear, and selectively rebuilding
  EXIF would mean rewriting IFD offsets. The profiles differ in ICC. An image
  without EXIF is an ordinary state (messengers do this), so the cost is low.
  REGENERATE makes no sense for images and is treated as PARANOID.

Nothing is raised: every section is in try/except, problems go to res.err().
The input is never modified: only dst is written, the input's sha256 is
compared before and after. No logs: file names and finding contents are
never printed.

Public API:
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
from core.i18n import N_, tr

SUPPORTED = ("jpeg", "png", "webp", "gif")

# --- limits: broken and deliberately malformed files come in ---------------
MAX_FILE = 50 * 1024 * 1024      # never load more into memory (same as the inspector)
MAX_SEGMENTS = 4096              # JPEG markers
MAX_CHUNKS = 100_000             # PNG/WebP chunks, GIF blocks (8 KB IDATs: thousands)
MAX_RAW = 256 * 1024 * 1024      # IDAT decompression ceiling during verification (bombs)
MAX_IFDS = 16
MAX_ENTRIES = 1000
MAX_NAMED = 40                   # named actions per EXIF

# Rotation/mirroring by EXIF Orientation code. 1 is normal and isn't described.
_ORIENT = {
    2: N_("mirrored horizontally"), 3: N_("rotated 180°"), 4: N_("mirrored vertically"),
    5: N_("mirrored and rotated 270°"), 6: N_("rotated 90° clockwise"),
    7: N_("mirrored and rotated 90°"), 8: N_("rotated 270° clockwise (90° counterclockwise)"),
}

C2PA_NOTE = (
    N_("A cryptographically signed provenance manifest (C2PA / Content Credentials) was removed. "
       "The removal itself is DETECTABLE: the recipient sees that a signature existed and was "
       "stripped; with a soft binding or a watermark, provenance can be recovered even without "
       "this block")
)


# ===========================================================================
# small helpers
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
    """dst must not equal src: the input is never overwritten."""
    try:
        if os.path.exists(b) and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _ident(pay) -> str:
    """A segment's tag (the string up to NUL) for the report: it shows who wrote it."""
    return clip(bytes(pay[:24]).split(b"\0")[0].decode("latin-1"), 40)


# ===========================================================================
# EXIF: light parsing (orientation + what was there at all) and a minimal rebuild
# ===========================================================================

_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
_SUB_IFD = {0x8769: "ExifIFD", 0x8825: "GPS", 0xA005: "Interop", 0x014A: "SubIFD"}
_XP = (0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F)

# Tags shown by name (the value goes into before).
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
    if tag == 0x9286:                      # UserComment: the first 8 bytes are the encoding
        return bytes(raw[8:]).decode("utf-8", "replace").strip("\0 ")
    if typ == 2:
        return bytes(raw).split(b"\0")[0].decode("utf-8", "replace")
    return tr("<%d bytes>") % len(raw)


def _exif_scan(blob) -> dict:
    """Light TIFF/EXIF walk. Never raises; the walk is bounded (EXIF can loop).

    -> {order, orient, named[(name, value)], gps, maker, thumb, other, bad}
    """
    out = {"order": None, "orient": None, "named": [], "gps": 0, "maker": 0,
           "thumb": 0, "other": 0, "bad": ""}
    blob = bytes(blob)
    if len(blob) < 8 or blob[:2] not in (b"II", b"MM"):
        out["bad"] = tr("no TIFF header")
        return out
    en = "<" if blob[:2] == b"II" else ">"
    out["order"] = blob[:2]
    try:
        if struct.unpack_from(en + "H", blob, 2)[0] != 42:
            out["bad"] = tr("invalid TIFF signature")
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
        out["bad"] = tr("EXIF cut off (%s)") % type(exc).__name__
    return out


def _min_tiff(orient, order) -> bytes:
    """Minimal TIFF stream: one IFD0 with the single Orientation tag (26 bytes)."""
    e = "<" if order == b"II" else ">"
    return (order + struct.pack(e + "HI", 42, 8) + struct.pack(e + "H", 1)
            + struct.pack(e + "HHIHH", 0x0112, 3, 1, orient, 0) + struct.pack(e + "I", 0))


def _min_app1(orient, order) -> bytes:
    """A JPEG APP1 segment with a minimal EXIF: 36 bytes including the marker (length field = 34)."""
    body = b"Exif\0\0" + _min_tiff(orient, order)
    return b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body


def _exif_acts(res, scan, loc, size, kept_orient) -> None:
    """Line-by-line report: what was in the EXIF and what remains (only Orientation)."""
    if scan["bad"]:
        res.act(Act.REMOVED, loc, tr("EXIF (not parsed)"), tr("%d bytes") % size,
                note=tr("The block couldn't be parsed (%s) and was removed entirely; the "
                        "orientation can't be restored") % scan["bad"])
        return
    for label, value in scan["named"][:MAX_NAMED]:
        res.act(Act.REMOVED, loc, "EXIF " + label, value)
    hidden = max(0, len(scan["named"]) - MAX_NAMED)
    if scan["gps"]:
        res.act(Act.REMOVED, loc, "GPS", tr("%d tag(s)") % scan["gps"],
                note=tr("GPS coordinates, altitude, time and tags: they locate a person, not a "
                        "file"))
    if scan["maker"]:
        res.act(Act.REMOVED, loc, "MakerNote", tr("%d bytes") % scan["maker"],
                note=tr("Proprietary camera block: the unit's serial number, shutter count, "
                        "settings"))
    if scan["thumb"]:
        res.act(Act.REMOVED, loc, tr("Embedded thumbnail"),
                tr("%d bytes") % scan["thumb"] if scan["thumb"] > 1 else tr("present"),
                note=tr("The thumbnail may have been made BEFORE retouching and cropping"))
    if scan["other"] or hidden:
        res.act(Act.REMOVED, loc, tr("Other EXIF tags"), tr("%d pcs") % (scan["other"] + hidden),
                note=tr("Shooting parameters and geometry: together they fingerprint a specific "
                        "camera"))
    o = scan["orient"]
    if kept_orient:
        res.act(Act.KEPT, loc, tr("Orientation (frame orientation)"), str(o),
                tr("%d (the only tag of the minimal EXIF)") % o,
                note=tr("STRUCTURAL: the shot is stored rotated (%s). Without this tag the "
                        "picture would display on its side. Physical rotation would require "
                        "re-encoding, so one tag is kept and the rest removed") % tr(_ORIENT[o]))
    elif o == 1:
        res.act(Act.REMOVED, loc, "Orientation", "1",
                note=tr("Default value: the picture displays the same without the tag"))
    elif o in _ORIENT:
        res.act(Act.REMOVED, loc, "Orientation", str(o),
                note=tr("Tag not kept: viewers don't use this EXIF block (it comes after the "
                        "image data, repeats one already kept, or sits in a WebP without VP8X)"))


# ===========================================================================
# JPEG
# ===========================================================================

_SOF = frozenset(m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC))
# Everything the decoder needs: SOFn, DHT, DAC, DQT, DRI, DNL, SOS, JPEG-LS, TEM.
_JPEG_IMG = frozenset(range(0xC0, 0xD0)) | {0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF, 0xF7, 0xF8, 0x01}
# End of entropy-coded data: FF followed by neither 00 (stuffing), RSTn nor FF (fill).
_SCAN_END = re.compile(rb"\xff(?=[^\x00\xd0-\xd7\xff])")


def _jpeg_walk(data):
    """Walk the markers. -> (segs, end, err); segs=[(marker, start, stop)].

    For SOS, stop includes the entropy-coded data up to the next marker. end is the
    offset right after EOI. Data after the first EOI is not in segs.
    """
    n = len(data)
    if data[:2] != b"\xff\xd8":
        return [], None, tr("no SOI marker")
    segs = [(0xD8, 0, 2)]
    pos = 2
    while True:
        if len(segs) > MAX_SEGMENTS:
            return segs, None, tr("more than %d segments") % MAX_SEGMENTS
        if pos >= n:
            return segs, None, tr("the file is cut off: no EOI marker")
        if data[pos] != 0xFF:
            return segs, None, tr("a marker was expected at offset %d, found 0x%02X") % (pos, data[pos])
        while pos + 1 < n and data[pos + 1] == 0xFF:      # fill bytes
            pos += 1
        if pos + 1 >= n:
            return segs, None, tr("the stream ends on a marker")
        m = data[pos + 1]
        if m == 0xD9:
            segs.append((0xD9, pos, pos + 2))
            return segs, pos + 2, None
        if m == 0x01:
            segs.append((0x01, pos, pos + 2))
            pos += 2
            continue
        if 0xD0 <= m <= 0xD8:
            return segs, None, tr("unexpected marker 0x%02X outside entropy-coded data") % m
        if pos + 4 > n:
            return segs, None, tr("marker 0x%02X without a length (the file is truncated)") % m
        length = struct.unpack_from(">H", data, pos + 2)[0]
        if length < 2:
            return segs, None, tr("marker 0x%02X declares length %d") % (m, length)
        stop = pos + 2 + length
        if stop > n:
            return segs, None, tr("segment 0x%02X is truncated (%d bytes needed, %d available)") % (
                m, length, n - pos - 2)
        if m == 0xDA:
            hit = _SCAN_END.search(data, stop)
            stop = hit.start() if hit else n
        segs.append((m, pos, stop))
        pos = stop


def _jpeg_info(data, segs):
    """(width, height, number of components) from the first SOF, or None."""
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
    return tr("version %d.%02d, density %dx%d, units %d") % (
        pay[5], pay[6], struct.unpack_from(">H", pay, 8)[0],
        struct.unpack_from(">H", pay, 10)[0], pay[7])


def _clean_jpeg(data, res, keep_icc):
    segs, end, err = _jpeg_walk(data)
    if err:
        res.err("JPEG: " + err)
        return None
    if not any(m == 0xDA for m, _, _ in segs):
        res.err(tr("JPEG: no image data (SOS), nothing to clean"))
        return None
    info = _jpeg_info(data, segs)
    out, dqt, img_bytes = [], 0, 0
    icc, c2pa = [0, 0], [0, 0]            # [parts, bytes]
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
                res.act(Act.KEPT, "APP0:JFIF", tr("JFIF header"), _jfif_text(pay),
                        _jfif_text(new),
                        note=tr("STRUCTURAL: pixel density sets the printed size, and having "
                                "neither EXIF nor JFIF stands out by itself. Kept deliberately"))
                if len(pay) > 14:
                    res.act(Act.REMOVED, "APP0:JFIF", tr("Embedded JFIF thumbnail"),
                            tr("%d bytes") % (len(pay) - 14),
                            note=tr("The thumbnail may have been made before retouching and "
                                    "cropping"))
            else:
                res.act(Act.REMOVED, "APP0", tr("Other APP0 segment (%s)") % _ident(pay),
                        tr("%d bytes") % len(pay), note=tr("JFXX thumbnail or a foreign block"))
        elif m == 0xE1:
            if pay[:6] == b"Exif\0\0":
                scan = _exif_scan(pay[6:])
                # Viewers don't read EXIF after the first SOS: orientation isn't taken from there
                keep_o = (not exif_kept) and (not in_scans) and scan["orient"] in _ORIENT
                if keep_o:
                    out.append(_min_app1(scan["orient"], scan["order"]))
                    exif_kept = True
                _exif_acts(res, scan, "APP1:Exif", len(pay), keep_o)
            elif pay.startswith((b"http://ns.adobe.com/xap/1.0/\0",
                                 b"http://ns.adobe.com/xmp/extension/")):
                res.act(Act.REMOVED, "APP1:XMP", tr("XMP packet"), tr("%d bytes") % len(pay),
                        note=tr("XMP duplicates EXIF and IPTC and carries the document GUID "
                                "(xmpMM) and edit history"))
            else:
                res.act(Act.REMOVED, "APP1", tr("Unrecognized APP1 (%s)") % _ident(pay),
                        tr("%d bytes") % len(pay))
        elif m == 0xE2:
            if pay[:12] == b"ICC_PROFILE\0":
                icc[0] += 1
                icc[1] += len(pay)
                if keep_icc:
                    out.append(raw)
            elif pay[:4] == b"MPF\0":
                res.act(Act.REMOVED, "APP2:MPF", "Multi-Picture Format", tr("%d bytes") % len(pay),
                        note=tr("An index of several frames in one file; the frames themselves "
                                "sit after EOI and go with the tail"))
            else:
                res.act(Act.REMOVED, "APP2", tr("Unrecognized APP2 (%s)") % _ident(pay),
                        tr("%d bytes") % len(pay))
        elif m == 0xEB:
            low = bytes(pay[:4096])
            if any(t in low for t in (b"jumb", b"c2pa", b"jumd")):
                c2pa[0] += 1
                c2pa[1] += len(pay)
            else:
                res.act(Act.REMOVED, "APP11", tr("JUMBF container without C2PA labels"),
                        tr("%d bytes") % len(pay))
        elif m == 0xED:
            res.act(Act.REMOVED, "APP13:Photoshop", "Photoshop IRB / IPTC", tr("%d bytes") % len(pay),
                    note=tr("IPTC-IIM (caption, keywords, authorship, address), Photoshop "
                            "thumbnail and an XMP copy"))
        elif m == 0xEE:
            transform = pay[11] if pay[:5] == b"Adobe" and len(pay) >= 12 else None
            need = bool(info) and transform is not None and (
                info[2] == 4 or (info[2] == 3 and transform == 0))
            if need:
                out.append(b"\xff\xee" + struct.pack(">H", 14) + pay[:12])
                res.act(Act.KEPT, "APP14:Adobe", tr("Adobe marker (transform=%d)") % transform,
                        tr("%d bytes") % len(pay), tr("12 bytes"),
                        note=tr("This isn't metadata but a decoding parameter: without the "
                                "marker the decoder guesses the default model (%s) and colors "
                                "get distorted. Version and flags carry no identity")
                             % (tr("YCCK would be read as CMYK and the Adobe inversion would be "
                                   "lost")
                                if info[2] == 4 else tr("RGB in JPEG would be read as YCbCr")))
            else:
                res.act(Act.REMOVED, "APP14:Adobe", tr("Adobe marker"), tr("%d bytes") % len(pay),
                        note=tr("Says the file went through Adobe software. For default YCbCr "
                                "the colors won't change"))
        elif m == 0xFE:
            res.act(Act.REMOVED, "COM", tr("JPEG comment"),
                    bytes(pay).decode("utf-8", "replace").strip("\0"),
                    note=tr("Encoders write their name and version here, people write anything"))
        elif 0xE3 <= m <= 0xEF:
            res.act(Act.REMOVED, "APP%d" % (m - 0xE0), tr("Non-standard segment (%s)") % _ident(pay),
                    tr("%d bytes") % len(pay), note=tr("An application wrote its own block of "
                                                  "arbitrary form"))
        else:
            res.act(Act.REMOVED, "0x%02X" % m, tr("Unknown marker"), tr("%d bytes") % len(pay),
                    note=tr("A reserved or private marker: the decoder doesn't need it"))
    if c2pa[0]:
        res.act(Act.REMOVED, "APP11:JUMBF", tr("C2PA / Content Credentials manifest"),
                tr("%d segm., %d bytes") % tuple(c2pa), note=tr(C2PA_NOTE))
    if icc[0]:
        if keep_icc:
            res.act(Act.KEPT, "APP2:ICC_PROFILE", tr("ICC profile"),
                    tr("%d parts, %d bytes") % tuple(icc), tr("unchanged"),
                    note=tr("STRUCTURAL: needed for correct color rendering. The profile name is "
                            "sometimes named after a monitor or printer; kept deliberately; use "
                            "PARANOID for full cleanliness"))
        else:
            res.act(Act.REMOVED, "APP2:ICC_PROFILE", tr("ICC profile"),
                    tr("%d parts, %d bytes") % tuple(icc),
                    note=tr("WARNING: colors may change -- without a profile the viewer assumes "
                            "sRGB. The profile name named the device"))
    if dqt:
        res.act(Act.UNREMOVABLE, "DQT", tr("Quantization tables"), tr("%d pcs") % dqt, tr("unchanged"),
                note=tr("Can't be removed without lossy re-encoding. The tables fingerprint the "
                        "encoder, but replacing them with ours would leave our library's "
                        "fingerprint, which is worse than the original"))
    res.act(Act.KEPT, "JPEG:image", tr("Image data"),
            "%dx%d" % info[:2] if info else tr("SOF not found"),
            tr("%d bytes, byte for byte") % img_bytes,
            note=tr("SOF, DHT, DQT, DRI and the entropy-coded scan data were copied without "
                    "re-encoding") + ("" if info else
                 tr(". The source has no SOF: the image didn't decode before cleaning either; "
                    "dimensions weren't checked")))
    if end < len(data):
        res.act(Act.REMOVED, "JPEG:trailer", tr("Data after EOI"), tr("%d bytes") % (len(data) - end),
                note=tr("A classic hiding place: decoders ignore the tail. It may hold MPF "
                        "frames, Motion Photo video, an Ultra HDR gain map, an archive or "
                        "someone else's document"))
    return b"".join(out)


# ===========================================================================
# PNG
# ===========================================================================

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# Image data: the file won't open without it. tRNS is the alpha channel.
_PNG_PIXELS = frozenset({b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"acTL", b"fcTL", b"fdAT"})
# Display parameters: background, significant bits, HDR tags.
_PNG_DISPLAY = frozenset({b"bKGD", b"sBIT", b"hIST", b"cICP", b"mDCv", b"cLLi"})
# STRUCTURAL: kept in both profiles (iCCP is decided separately, by profile).
_PNG_STRUCT = {b"gAMA": N_("Gamma"), b"cHRM": N_("Chromaticity"), b"sRGB": N_("sRGB color space"),
               b"pHYs": N_("Pixel density")}
_PNG_COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _png_chunk(ctype, body) -> bytes:
    """Assemble a chunk; the CRC is recomputed."""
    return (struct.pack(">I", len(body)) + ctype + bytes(body)
            + struct.pack(">I", binascii.crc32(ctype + bytes(body)) & 0xFFFFFFFF))


def _png_walk(data):
    """Walk the chunks, checking CRCs. -> (chunks, end, err).

    chunks=[(type, start, stop)], start..stop is the whole chunk (length, type, body, CRC).
    """
    if data[:8] != _PNG_SIG:
        return [], 0, tr("no PNG signature")
    mv = memoryview(data)
    n, pos, chunks = len(data), 8, []
    while True:
        if len(chunks) >= MAX_CHUNKS:
            return chunks, pos, tr("more than %d chunks") % MAX_CHUNKS
        if pos + 12 > n:
            return chunks, pos, tr("the file is cut off: no IEND chunk")
        length = struct.unpack_from(">I", data, pos)[0]
        ctype = bytes(mv[pos + 4:pos + 8])
        if not all(65 <= c <= 90 or 97 <= c <= 122 for c in ctype):
            return chunks, pos, tr("invalid chunk type at offset %d") % pos
        name = ctype.decode("ascii")
        stop = pos + 12 + length
        if stop > n:
            return chunks, pos, tr("chunk %s is truncated (%d bytes needed, %d available)") % (name, length, n - pos - 12)
        crc = struct.unpack_from(">I", data, stop - 4)[0]
        if binascii.crc32(mv[pos + 4:stop - 4]) & 0xFFFFFFFF != crc:
            return chunks, pos, tr("invalid CRC of chunk %s: the file is damaged or was edited "
                                   "by hand") % name
        if not chunks and (ctype != b"IHDR" or length != 13):
            return chunks, pos, tr("the first chunk must be IHDR of length 13")
        chunks.append((ctype, pos, stop))
        pos = stop
        if ctype == b"IEND":
            return chunks, pos, None


def _png_decodes(data, chunks):
    """The IDAT stream decompresses completely, with no less data than IHDR declares.

    Decompression is streaming with a MAX_RAW ceiling: a bomb in IDAT won't eat memory.
    -> (ok, reason)
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
        return False, tr("the IDAT stream doesn't decompress (%s)") % exc
    if capped:
        return True, ""
    if not d.eof:
        return False, tr("the IDAT stream is cut off: the image is truncated")
    ch = _PNG_COLOR_CHANNELS.get(color)
    if interlace == 0 and ch:
        need = h * (1 + (w * ch * depth + 7) // 8)
        if total < need:
            return False, tr("IDAT has %d bytes but IHDR requires %d: the image is truncated") % (total, need)
    return True, ""


def _png_brief(t, body) -> str:
    try:
        if t == b"gAMA":
            return tr("gamma %.5f") % (struct.unpack(">I", body[:4])[0] / 100000)
        if t == b"sRGB":
            return "intent %d" % body[0]
        if t == b"pHYs":
            x, y, u = struct.unpack(">IIB", body[:9])
            return tr("%d x %d, units %d") % (x, y, u)
    except (struct.error, IndexError):
        pass
    return tr("%d bytes") % len(body)


def _png_text_act(res, ctype, body) -> None:
    name = ctype.decode("ascii")
    kw, _, rest = bytes(body).partition(b"\0")
    key = clip(kw.decode("latin-1"), 80)
    if ctype == b"tEXt":
        val = rest.decode("latin-1")
    elif ctype == b"zTXt":
        val = tr("<compressed, %d bytes; not decompressed: bomb protection>") % len(rest)
    elif rest[:1] == b"\0":                           # uncompressed iTXt
        tail = rest[2:].partition(b"\0")[2].partition(b"\0")[2]
        val = tail.decode("utf-8", "replace")
    else:
        val = tr("<compressed, %d bytes>") % len(rest)
    xmp = ctype == b"iTXt" and "xmp" in key.lower()
    res.act(Act.REMOVED, "PNG:" + name,
            (tr("XMP packet (iTXt \"%s\")") if xmp else tr("Text chunk ") + name + " «%s»") % key, val,
            note=tr("XMP: document GUID, authorship, edit history") if xmp else
                 tr("Written by people and generators alike: prompts, model parameters and the "
                    "source path end up here too"))


def _clean_png(data, res, keep_icc):
    chunks, end, err = _png_walk(data)
    if err:
        res.err("PNG: " + err)
        return None
    if not any(t == b"IDAT" for t, _, _ in chunks):
        res.err(tr("PNG: no image data (IDAT), nothing to clean"))
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
            res.act(Act.KEPT, "PNG:" + name, tr(_PNG_STRUCT[t]), _png_brief(t, body),
                    tr("unchanged"),
                    note=tr("STRUCTURAL: affects color or printed size, kept in every profile"))
        elif t == b"iCCP":
            pname = clip(bytes(body).split(b"\0", 1)[0].decode("latin-1"), 80)
            if keep_icc:
                out.append(_png_chunk(t, body))
                res.act(Act.KEPT, "PNG:iCCP", tr("ICC profile"), tr("\"%s\", %d bytes") % (pname, len(body)),
                        tr("unchanged"),
                        note=tr("STRUCTURAL: needed for correct color rendering. The profile "
                                "name is sometimes named after a monitor or scanner; kept "
                                "deliberately; use PARANOID for full cleanliness"))
            else:
                res.act(Act.REMOVED, "PNG:iCCP", tr("ICC profile"),
                        tr("\"%s\", %d bytes") % (pname, len(body)),
                        note=tr("WARNING: colors may change -- without a profile the viewer "
                                "relies on gAMA/sRGB or assumes sRGB"))
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
                when = tr("%d bytes") % len(body)
            res.act(Act.REMOVED, "PNG:tIME", tr("Time of the last edit"), when,
                    note=tr("When the picture was last modified"))
        elif t == b"caBX":
            res.act(Act.REMOVED, "PNG:caBX", tr("C2PA / Content Credentials manifest"),
                    tr("%d bytes") % len(body), note=tr(C2PA_NOTE))
        elif t == b"dSIG":
            res.act(Act.REMOVED, "PNG:dSIG", tr("Digital signature"), tr("%d bytes") % len(body),
                    note=tr("The signature covered the file's content: cleaning breaks it, and "
                            "the recipient can see that it was signed"))
        elif t[:1].isupper():
            out.append(_png_chunk(t, body))
            res.act(Act.KEPT, "PNG:" + name, tr("Unknown critical chunk"),
                    tr("%d bytes") % len(body), tr("unchanged"),
                    note=tr("A decoder must understand a critical chunk: removing it would "
                            "damage the file"))
        else:
            res.act(Act.REMOVED, "PNG:" + name, tr("Unknown ancillary chunk"),
                    tr("%d bytes") % len(body),
                    note=tr("A specific application's private chunk with arbitrary content"))
    res.act(Act.KEPT, "PNG:image", tr("Image data"),
            ", ".join(k + ("×%d" % v if v > 1 else "") for k, v in pix.items()),
            tr("byte for byte"), note=tr("Pixel data, palette and transparency copied without "
                                   "re-encoding; CRCs recomputed"))
    if disp:
        res.act(Act.KEPT, "PNG:display", tr("Display parameters"), ", ".join(disp),
                tr("unchanged"), note=tr("STRUCTURAL: background, significant bits and HDR tags "
                                         "affect how the picture is shown"))
    if end < len(data):
        res.act(Act.REMOVED, "PNG:trailer", tr("Data after IEND"), tr("%d bytes") % (len(data) - end),
                note=tr("A classic hiding place: decoders ignore the tail"))
    return b"".join(out)


# ===========================================================================
# WebP (RIFF)
# ===========================================================================

_WEBP_IMG = frozenset({b"VP8 ", b"VP8L", b"ALPH", b"ANIM", b"ANMF"})
_VP8X_FLAGS = ((0x20, "ICC"), (0x10, N_("alpha")), (0x08, "EXIF"), (0x04, "XMP"), (0x02, N_("animation")))


def _riff(fourcc, body) -> bytes:
    return fourcc + struct.pack("<I", len(body)) + bytes(body) + (b"\0" if len(body) & 1 else b"")


def _webp_walk(data):
    """RIFF container. -> (chunks, end, err); chunks=[(fourcc, start, stop)] is the chunk body."""
    n = len(data)
    if n < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return [], 0, tr("no RIFF/WEBP header")
    total = struct.unpack_from("<I", data, 4)[0] + 8
    if total > n:
        return [], 0, tr("RIFF declares %d bytes, the file has %d: the file is truncated") % (total, n)
    pos, chunks = 12, []
    while pos < total:
        if len(chunks) >= MAX_CHUNKS:
            return chunks, total, tr("more than %d chunks") % MAX_CHUNKS
        if pos + 8 > total:
            return chunks, total, tr("the RIFF tail is shorter than a chunk header")
        size = struct.unpack_from("<I", data, pos + 4)[0]
        if pos + 8 + size > total:
            return chunks, total, tr("a chunk runs past the end of RIFF")
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
    return ", ".join(tr(n) for bit, n in _VP8X_FLAGS if flags & bit) or tr("none")


def _clean_webp(data, res, keep_icc):
    chunks, end, err = _webp_walk(data)
    if err:
        res.err("WebP: " + err)
        return None
    kinds = {f for f, _, _ in chunks}
    if not kinds & {b"VP8 ", b"VP8L", b"ANMF"}:
        res.err(tr("WebP: no image data, nothing to clean"))
        return None
    vp8x = next((c for c in chunks if c[0] == b"VP8X"), None)
    if vp8x and vp8x[2] - vp8x[1] < 10:
        res.err(tr("WebP: the VP8X chunk is truncated"))
        return None
    # orientation: the first EXIF with a valid tag; it's written only if VP8X is present
    # (without VP8X an EXIF chunk isn't allowed in the file at all)
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
                res.act(Act.NORMALIZED, "WebP:VP8X", tr("Container flags"),
                        "0x%02X: %s" % (flags, _flag_names(flags)),
                        "0x%02X: %s" % (new, _flag_names(new)),
                        note=tr("The flags now match the remaining chunks: otherwise a sign of "
                                "crude cleaning would remain (a flag without its chunk)"))
        elif f in _WEBP_IMG:
            parts.append(_riff(f, body))
            pix[name.strip()] += 1
        elif f == b"ICCP" and vp8x:
            if keep_icc:
                parts.append(_riff(f, body))
                res.act(Act.KEPT, "WebP:ICCP", tr("ICC profile"), tr("%d bytes") % len(body),
                        tr("unchanged"),
                        note=tr("STRUCTURAL: needed for correct color rendering. The profile "
                                "name is sometimes named after the device; kept deliberately; "
                                "use PARANOID for full cleanliness"))
            else:
                res.act(Act.REMOVED, "WebP:ICCP", tr("ICC profile"), tr("%d bytes") % len(body),
                        note=tr("WARNING: colors may change -- without a profile the viewer "
                                "assumes sRGB"))
        elif f == b"EXIF":
            sc = _exif_scan(body[6:] if body[:6] == b"Exif\0\0" else body)
            first = keep_o and not exif_done and sc is not None and sc["orient"] == orient
            if first:
                exif_done = True
                parts.append(_riff(f, (b"Exif\0\0" if prefix else b"")
                                   + _min_tiff(orient, scan["order"])))
            _exif_acts(res, sc, "WebP:EXIF", len(body), first)
        elif f == b"XMP ":
            res.act(Act.REMOVED, "WebP:XMP", tr("XMP packet"), tr("%d bytes") % len(body),
                    note=tr("XMP: document GUID, authorship, edit history"))
        else:
            res.act(Act.REMOVED, "WebP:" + name.strip(), tr("Unknown chunk"), tr("%d bytes") % len(body),
                    note=tr("A chunk the decoder doesn't need: an application's private data"))
    res.act(Act.KEPT, "WebP:image", tr("Image data"),
            ", ".join(k + ("×%d" % v if v > 1 else "") for k, v in pix.items()),
            tr("byte for byte"), note=tr("The compressed frame data was copied without re-encoding"))
    body = b"WEBP" + b"".join(parts)
    if end < len(data):
        res.act(Act.REMOVED, "WebP:trailer", tr("Data after RIFF"), tr("%d bytes") % (len(data) - end),
                note=tr("A classic hiding place: decoders ignore the tail"))
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ===========================================================================
# GIF
# ===========================================================================

_GIF_XMP_MAGIC = b"\x01\xff\xfe\xfd\xfc"
_GIF_LOOP = (b"NETSCAPE2.0", b"ANIMEXTS1.0")       # animation repeat count: STRUCTURAL
_GIF_IMG = frozenset({"hdr", "gce", "img", "pt", "loop"})


def _gif_sub(data, q):
    """End of a sub-block chain (past the zero terminator), or None if it's cut off."""
    n = len(data)
    while q < n:
        size = data[q]
        q += 1 + size
        if size == 0:
            return q
    return None


def _gif_walk(data):
    """-> (items, end, err); items=[(kind, start, stop)].

    Kinds: hdr, gce, img, pt, loop, icc, comment, xmp, app, ext, end.
    """
    n = len(data)
    if n < 13 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return [], 0, tr("no GIF signature")
    pos = 13 + ((3 << ((data[10] & 7) + 1)) if data[10] & 0x80 else 0)
    if pos > n:
        return [], 0, tr("the global palette is truncated")
    items = [("hdr", 0, pos)]
    while True:
        if len(items) >= MAX_CHUNKS:
            return items, pos, tr("more than %d blocks") % MAX_CHUNKS
        if pos >= n:
            return items, pos, tr("the file is cut off: no trailing 0x3B byte")
        t = data[pos]
        if t == 0x3B:
            items.append(("end", pos, pos + 1))
            return items, pos + 1, None
        if t == 0x2C:
            if pos + 10 > n:
                return items, pos, tr("the image descriptor is truncated")
            packed, q = data[pos + 9], pos + 10
            if packed & 0x80:
                q += 3 << ((packed & 7) + 1)
            q = _gif_sub(data, q + 1)                      # +1 -- LZW min code size
            if q is None:
                return items, pos, tr("the image data is cut off")
            items.append(("img", pos, q))
            pos = q
            continue
        if t != 0x21:
            return items, pos, tr("unknown block 0x%02X at offset %d") % (t, pos)
        if pos + 2 > n:
            return items, pos, tr("the extension is truncated")
        label, kind, q = data[pos + 1], "ext", None
        if label == 0xFF:
            if pos + 3 > n or pos + 3 + data[pos + 2] > n:
                return items, pos, tr("Application Extension is truncated")
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
            return items, pos, tr("extension 0x%02X is cut off") % label
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
        res.err(tr("GIF: no frames, nothing to clean"))
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
                res.act(Act.KEPT, "GIF:ICC", tr("ICC profile"), tr("%d bytes") % len(raw), tr("unchanged"),
                        note=tr("STRUCTURAL: needed for correct color rendering"))
            else:
                res.act(Act.REMOVED, "GIF:ICC", tr("ICC profile"), tr("%d bytes") % len(raw),
                        note=tr("WARNING: colors may change"))
        elif kind == "comment":
            res.act(Act.REMOVED, "GIF:Comment", tr("GIF comment"), _gif_text(raw),
                    note=tr("Converters write their name here, and sometimes the author's"))
        elif kind == "xmp":
            res.act(Act.REMOVED, "GIF:XMP", tr("XMP packet"), tr("%d bytes") % len(raw),
                    note=tr("XMP: authorship, document GUID, edit history"))
        elif kind == "app":
            appid = bytes(raw[3:3 + raw[2]]) if len(raw) > 2 else b""
            res.act(Act.REMOVED, "GIF:Application", "Application Extension (%s)"
                    % clip(appid.decode("latin-1"), 24), tr("%d bytes") % len(raw),
                    note=tr("A specific program's block: the application name alone says what "
                            "made the file"))
        else:
            res.act(Act.REMOVED, "GIF:ext", tr("Unknown extension 0x%02X") % raw[1],
                    tr("%d bytes") % len(raw), note=tr("The decoder doesn't need it: an application's "
                                                  "private data"))
    w, h = struct.unpack_from("<HH", data, 6)
    res.act(Act.KEPT, "GIF:image", tr("Image data"), tr("%dx%d, %d frames") % (w, h, frames),
            tr("byte for byte"),
            note=tr("LZW data, palettes and frame control copied without re-encoding")
                 + (tr("; the animation loop extension was kept (STRUCTURAL): without it the "
                       "animation plays once") if loops else ""))
    if end < len(data):
        res.act(Act.REMOVED, "GIF:trailer", tr("Data after the trailer"), tr("%d bytes") % (len(data) - end),
                note=tr("A classic hiding place: decoders ignore the tail"))
    return b"".join(out)


# ===========================================================================
# verifying the result: is the file damaged (regardless of who built it)
# ===========================================================================

_WALK = {"jpeg": _jpeg_walk, "png": _png_walk, "webp": _webp_walk, "gif": _gif_walk}


def _stream(fmt, d, parts) -> bytes:
    """"Image stream": everything the decoder needs, in the original order, byte for byte."""
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
    """The first EXIF Orientation in the file (or None)."""
    try:
        for t, a, b in parts:
            if fmt == "jpeg" and t == 0xDA:
                break                                  # EXIF isn't read after SOS
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
    except Exception:                                  # noqa: BLE001 -- verification doesn't crash
        pass
    return None


def _dst_problems(fmt, d, parts, end) -> list:
    """Structural validity of the result, checked with our own parser."""
    errs = []
    if end != len(d):
        errs.append(tr("data remains after the end of the image (%d bytes)") % (len(d) - end))
    if fmt == "jpeg" and not any(m == 0xDA for m, _, _ in parts):
        errs.append(tr("no SOS"))
    if fmt == "png":
        if not any(t == b"IDAT" for t, _, _ in parts):
            errs.append(tr("no IDAT"))
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
                    errs.append(tr("VP8X flags don't match the chunks (%s)") % fourcc.decode().strip())
    return errs


def _verify(fmt, a, b) -> dict:
    v = {"fmt": fmt, "ok": False, "valid": False, "dims_src": None, "dims_dst": None,
         "dims_same": False, "image_same": False, "image_bytes": 0, "image_sha256": "",
         "scan_same": None, "scan_bytes": 0, "orient_src": None, "orient_dst": None,
         "orient_ok": False, "errors": []}
    walk = _WALK.get(fmt)
    if walk is None:
        v["errors"].append(tr("verification doesn't support format %s") % fmt)
        return v
    pa, ea, era = walk(a)
    pb, eb, erb = walk(b)
    if era:
        v["errors"].append(tr("source: ") + era)
    if erb:
        v["errors"].append(tr("result: ") + erb)
    if era or erb:
        return v
    probs = _dst_problems(fmt, b, pb, eb)
    v["errors"] += [tr("result: ") + p for p in probs]
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
        v["errors"].append(tr("dimensions changed: %s -> %s") % (v["dims_src"], v["dims_dst"]))
    if not v["image_same"]:
        v["errors"].append(tr("the compressed image data changed: the file was re-encoded or "
                              "damaged"))
    if not v["orient_ok"]:
        v["errors"].append(tr("orientation lost: %s -> %s") % (v["orient_src"], v["orient_dst"]))
    v["ok"] = v["valid"] and v["dims_same"] and v["image_same"] and v["orient_ok"]
    return v


def verify_image(src, dst) -> dict:
    """Proof that cleaning didn't damage the file. Reads both files, writes nothing.

    -> {ok, fmt, valid, dims_src, dims_dst, dims_same, image_same, image_bytes,
        image_sha256, scan_same (JPEG: the whole SOS..EOI block bit for bit),
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
            v["errors"].append(tr("format changed: %s -> %s") % (fa, fb))
            return v
        return _verify(fa, a, b)
    except Exception as exc:                           # noqa: BLE001
        v["errors"].append(tr("verification not performed: %s") % type(exc).__name__)
        return v


# ===========================================================================
# public entry point
# ===========================================================================

_CLEANERS = {"jpeg": _clean_jpeg, "png": _clean_png, "webp": _clean_webp, "gif": _clean_gif}

_REFUSE = {
    "tiff": N_("TIFF cleaning isn't supported yet: the metadata lives in the IFDs themselves "
               "together with references to the data, and rebuilding without dedicated code "
               "risks damaging the file"),
    "heic": N_("HEIF/HEIC cleaning isn't supported yet: the metadata lives in iloc items, and "
               "rebuilding without dedicated code risks damaging the file"),
}
_UNREADABLE = {
    "cloud": N_("the file is a cloud placeholder: open it in Explorer or choose \"Always keep on "
                "this device\""),
    "missing": N_("file not found"), "dir": N_("this is a directory, not a file"), "denied": N_("no read permission"),
}


def _run(res, state, src, dst, profile, keep) -> None:
    try:
        profile = Profile(profile)
    except ValueError:
        res.err(tr("unknown cleaning profile: %s") % res.profile)
        return
    res.profile = profile.value
    names = [keep] if isinstance(keep, str) else (keep or [])
    keep = {str(k).lower() for k in names}
    # PARANOID and REGENERATE remove everything; ICC stays only in STEALTH or with keep={"icc"}
    keep_icc = profile is Profile.STEALTH or "icc" in keep

    if _same_path(src, dst):
        res.err(tr("the output path matches the input path: the input file is never overwritten"))
        return
    fmt = sniff.sniff(src)
    res.fmt = fmt
    if fmt == "unreadable":
        reason, code = sniff.unreadable_reason(src)
        res.err("%s (errno %s)" % (tr(_UNREADABLE.get(reason, "the file can't be read")), code))
        return
    if fmt in _REFUSE:
        res.err(tr(_REFUSE[fmt]))
        return
    if fmt not in _CLEANERS:
        res.err(tr("not a raster image (format %s): image cleaning doesn't apply") % fmt
                if fmt != "unknown" else tr("the file is empty or the format wasn't recognized"))
        return
    try:
        if os.path.getsize(src) > MAX_FILE:
            res.err(tr("the file is larger than %d MB: not loaded into memory") % (MAX_FILE // 1048576))
            return
        with open(src, "rb") as fh:
            data = fh.read(MAX_FILE + 1)
    except OSError as exc:
        res.err(tr("the file can't be read (errno %s)") % exc.errno)
        return
    sha_before = _sha(data)

    out = _CLEANERS[fmt](data, res, keep_icc)
    if out is None:
        return                                      # the reason is already in res.errors

    try:
        with open(dst, "wb") as fh:
            fh.write(out)
        state["wrote"] = True
        with open(dst, "rb") as fh:
            back = fh.read(MAX_FILE + 1)
    except OSError as exc:
        res.err(tr("could not write the output file (errno %s)") % exc.errno)
        return
    if back != out:
        res.err(tr("the output file on disk doesn't match what was built"))
        return
    v = _verify(fmt, data, back)                     # verify what's actually on disk
    if not v["ok"]:
        for e in v["errors"] or [tr("verification of the result failed")]:
            res.err(e)
        return
    try:
        if _file_sha(src) != sha_before:
            res.err(tr("the input file changed during processing: the result is unreliable"))
            return
    except OSError as exc:
        res.err(tr("the input file couldn't be re-read (errno %s)") % exc.errno)
        return
    res.ok = True


def clean_image(src, dst, profile=Profile.STEALTH, keep=None) -> CleanResult:
    """Clean an image without re-encoding. Never raises.

    src is the input (never modified), dst is where to write the result.
    profile is Profile.STEALTH (default) or PARANOID; REGENERATE is treated as
    PARANOID for images. The profiles differ in the ICC profile: STEALTH keeps
    it, PARANOID removes it with a warning.
    keep is a set of names to keep; {"icc"} is understood (keep the ICC profile
    in PARANOID too), the rest is ignored.

    ok=True means: dst was written, the structure is valid (checked with our own
    parser), dimensions are the same, the compressed data matches byte for byte,
    the orientation is preserved. With ok=False dst doesn't remain on disk,
    actions is empty, the reason is in errors.
    """
    res = CleanResult(src=str(src), dst=str(dst), fmt="unknown",
                      profile=str(getattr(profile, "value", profile)))
    state = {"wrote": False}
    try:
        _run(res, state, os.fspath(src), os.fspath(dst), profile, keep)
    except Exception as exc:                           # noqa: BLE001 -- a broken file doesn't crash the bot
        res.ok = False
        res.err(tr("internal cleaning error: %s: %s") % (type(exc).__name__, exc))
    if not res.ok:
        res.actions.clear()
        if state["wrote"]:
            try:
                os.remove(dst)
            except OSError:
                pass
    return res



# ===========================================================================
# self-test: PYTHONIOENCODING=utf-8 python -m core.clean_image
# ===========================================================================

def _selftest() -> int:
    """Builds files from bytes, cleans them, and proves the result with the INSPECTOR as the oracle.

    For every fixture and profile: critical/sensitive before and after, orientation,
    dimensions, the entropy-coded block bit for bit, output validity, the input's
    sha256 before and after. Any failure means exit code 1.
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
        print("    [%s] %s%s" % ("ok" if cond else "FAIL", name,
                                  (" -- %s" % (detail,)) if detail and not cond else ""))

    # ----- building fixtures ---------------------------------------------------

    def seg(m, payload):
        return b"\xff" + bytes([m]) + struct.pack(">H", len(payload) + 2) + payload

    def entropy(n=300):
        out = bytearray()
        for i in range(n):
            out.append(rng.randrange(0, 255))               # no 0xFF: we place markers ourselves
            if i % 37 == 5:
                out += b"\xff\x00"                          # stuffing
            if i % 120 == 119:
                out += bytes([0xFF, 0xD0 + (i // 120) % 8])  # RSTn
        return bytes(out)

    def tiff(order, ifds):
        """ifds: {name: [(tag, type, value|IFD_name)]}; count is computed automatically."""
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
    webp_flag_only = webp([vp8x(0x0C), vp8])                   # flags present, chunks missing

    def sub(b):
        return b"".join(bytes([len(b[i:i + 255])]) + b[i:i + 255] for i in range(0, len(b), 255)) + b"\0"

    gif_full = (b"GIF89a" + struct.pack("<HHBBB", 2, 2, 0x80, 0, 0) + b"\0\0\0\xff\xff\xff"
                + b"\x21\xff\x0bNETSCAPE2.0" + sub(b"\x01\0\0")
                + b"\x21\xfe" + sub(b"Made by ImageMagick 7.1 igor")
                + b"\x21\xff\x0bXMP DataXMP" + xmp_text + b"\x01" + bytes(range(255, -1, -1)) + b"\0"
                + b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
                + b"\x2c" + struct.pack("<HHHHB", 0, 0, 2, 2, 0) + b"\x02" + sub(b"\x44\x01")
                + b"\x3b" + b"PK\x03\x04tail")

    cases = {   # name -> bytes
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
        # --- project fixtures (tests/fixtures.py), as is -----------------
        try:
            from tests import fixtures as F
            fx = {}
            for mk in (F.make_jpeg, F.make_png):
                p, _ = mk(tmp)
                fx[os.path.basename(p)] = p
            broken = F.make_broken(tmp)
        except Exception as exc:                          # noqa: BLE001
            print("!! tests/fixtures.py fixtures are unavailable: %s" % type(exc).__name__)
            fx, broken = {}, {}

        paths = {n: put(n, b) for n, b in cases.items()}
        paths.update({"fixtures.make_" + k: v for k, v in fx.items()})

        results = {}
        print("=" * 100)
        print("CLEANING: critical/sensitive before -> after (inspector as oracle), orientation, "
              "dimensions, entropy block, validity, input sha256")
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
                print("%-38s %-8s ok=%-5s crit %2d -> %-2s sens %2d -> %-2s orient %s->%s dims "
                      "%s=%s image_same=%s scan_same=%s(%s B) valid=%s sha_in=%s"
                      % (name, prof.value, res.ok, before.critical,
                         after.critical if after else "-", before.sensitive,
                         after.sensitive if after else "-", v.get("orient_src"), v.get("orient_dst"),
                         v.get("dims_src"), v.get("dims_dst"), v.get("image_same"),
                         v.get("scan_same"), v.get("scan_bytes"), v.get("valid"),
                         "match" if sha0 == sha1 else "CHANGED"))
                for e in res.errors:
                    print("      ! " + e)
                check("%s/%s: input untouched (sha256 before == after)" % (name, prof.value), sha0 == sha1)
                check("%s/%s: ok=True" % (name, prof.value), res.ok, res.errors)
                if res.ok:
                    check("%s/%s: critical after == 0" % (name, prof.value), after.critical == 0,
                          [(f.location, f.label) for f in after.findings
                           if f.risk.value in ("identity", "environment")])
                    check("%s/%s: dimensions, compressed data and orientation intact" % (name, prof.value),
                          v["ok"], v["errors"])
                    # idempotency: re-cleaning our own result changes nothing
                    again = os.path.join(outdir, "again_" + os.path.basename(dst))
                    r2 = clean_image(dst, again, prof)
                    check("%s/%s: re-cleaning is idempotent" % (name, prof.value),
                          r2.ok and open(again, "rb").read() == open(dst, "rb").read(), r2.errors)

        # --- targeted assertions ------------------------------------------
        print("-" * 100)
        print("TARGETED CHECKS")
        leak_labels = {"Artist", "BodySerialNumber", "LensSerialNumber", "MakerNote", "DateTime",
                       "DateTimeOriginal", "Make", "Model", "Software", "UserComment",
                       "Coordinates (decimal)", "Embedded thumbnail", "JPEG comment",
                       "Author", "Comment", "dc:creator", "xmp:CreatorTool", "Comment"}

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
            print("  --- profile %s" % tag)
            res, before, after, v, dst = results[("jpeg_phone_orient6.jpg", prof)]
            data = read(dst)
            check("JPEG/6: orientation 6 kept (per the inspector)", orient_in_report(after) == 6)
            check("JPEG/6: orientation 6 kept (per verify)", v["orient_src"] == 6 == v["orient_dst"])
            check("JPEG/6: GPS, serials, dates, MakerNote, Artist, thumbnail are gone",
                  not ({f.label for f in after.findings} & leak_labels),
                  sorted({f.label for f in after.findings} & leak_labels))
            check("JPEG/6: minimal APP1 is exactly 36 bytes (marker + length 34)",
                  any(m == 0xE1 and y - x == 36 for m, x, y in _jpeg_walk(data)[0]))
            check("JPEG/6: tail after EOI and XMP/APP13/COM removed",
                  not ({0xED, 0xFE} & set(jmarks(dst))) and _jpeg_walk(data)[1] == len(data)
                  and b"Igor" not in data and b"secret" not in data and b"Photoshop" not in data)
            check("JPEG/6: JFIF thumbnail cut off (APP0 = 18 bytes)",
                  any(m == 0xE0 and y - x == 18 for m, x, y in _jpeg_walk(data)[0]))
            check("JPEG/6: SOS..EOI block bit for bit", v["scan_same"] and v["image_same"])
            icc_kept = 0xE2 in jmarks(dst)
            check("JPEG/6: ICC %s" % ("kept (STEALTH)" if tag == "stealth" else "removed (PARANOID)"),
                  icc_kept == (tag == "stealth"))
            check("JPEG/6: DQT marked UNREMOVABLE", res.count(Act.UNREMOVABLE) == 1)
            check("JPEG/6: Orientation and ICC/JFIF reported as KEPT" if tag == "stealth"
                  else "JPEG/6: Orientation and JFIF reported as KEPT, ICC as REMOVED with a "
                       "warning",
                  any(a.act == Act.KEPT and "Orientation" in a.label for a in res.actions)
                  and (any(a.act == Act.KEPT and "ICC" in a.label for a in res.actions) if tag == "stealth"
                       else any(a.act == Act.REMOVED and "ICC" in a.label and "colors" in a.note
                                for a in res.actions)))

            res, before, after, v, dst = results[("jpeg_phone_little_endian_orient8.jpg", prof)]
            check("JPEG/8 (II): orientation 8 kept", orient_in_report(after) == 8 and v["orient_dst"] == 8)

            res, before, after, v, dst = results[("jpeg_orient1.jpg", prof)]
            check("JPEG orient=1: no EXIF written at all", 0xE1 not in jmarks(dst))

            res, before, after, v, dst = results[("jpeg_c2pa.jpg", prof)]
            check("JPEG C2PA: APP11 removed, detectability warning in the note",
                  0xEB not in jmarks(dst) and any("DETECTABLE" in a.note for a in res.actions))

            res, before, after, v, dst = results[("jpeg_adobe_cmyk.jpg", prof)]
            check("JPEG CMYK: APP14 kept (colors break otherwise), COM removed",
                  0xEE in jmarks(dst) and 0xFE not in jmarks(dst)
                  and any(a.act == Act.KEPT and "Adobe" in a.label for a in res.actions))
            res, before, after, v, dst = results[("jpeg_adobe_rgb.jpg", prof)]
            check("JPEG RGB transform=0: APP14 kept", 0xEE in jmarks(dst))
            res, before, after, v, dst = results[("jpeg_adobe_ycc.jpg", prof)]
            check("JPEG YCbCr transform=1: APP14 removed", 0xEE not in jmarks(dst))

            res, before, after, v, dst = results[("jpeg_scans_with_junk_between.jpg", prof)]
            check("JPEG junk BETWEEN scans removed, compressed data intact",
                  v["image_same"] and v["scan_same"] is False
                  and not ({0xFE, 0xE1} & set(jmarks(dst))), (v["image_same"], v["scan_same"]))

            res, before, after, v, dst = results[("jpeg_already_clean.jpg", prof)]
            check("JPEG without metadata: the result equals the input byte for byte",
                  read(dst) == read(paths["jpeg_already_clean.jpg"]))

            res, before, after, v, dst = results[("png_full_orient8.png", prof)]
            names = [t.decode() for t, _, _ in _png_walk(read(dst))[0]]
            check("PNG: text/XMP/tIME/full eXIf/caBX/private chunk/tail removed",
                  not ({"tEXt", "zTXt", "iTXt", "tIME", "caBX", "prVt"} & set(names))
                  and _png_walk(read(dst))[1] == len(read(dst)) and b"Igor" not in read(dst), names)
            check("PNG: orientation 8 kept, GPS/Artist from eXIf gone",
                  orient_in_report(after) == 8 and "eXIf" in names
                  and not ({f.label for f in after.findings} & leak_labels))
            check("PNG: gAMA and pHYs (STRUCTURAL) kept, both IDATs in place",
                  {"gAMA", "pHYs"} <= set(names) and names.count("IDAT") == 2, names)
            check("PNG: iCCP %s" % ("kept" if tag == "stealth" else "removed"),
                  ("iCCP" in names) == (tag == "stealth"), names)
            res, before, after, v, dst = results[("png_no_meta.png", prof)]
            check("PNG without metadata: the result equals the input byte for byte",
                  read(dst) == read(paths["png_no_meta.png"]))

            res, before, after, v, dst = results[("webp_full_orient3.webp", prof)]
            d = read(dst)
            ch, _, _ = _webp_walk(d)
            flags = d[ch[0][1]]
            check("WebP: orientation 3 kept, EXIF/XMP/tail removed",
                  v["orient_dst"] == 3 and orient_in_report(after) == 3 and b"Igor" not in d
                  and b"secret" not in d and not any(f == b"XMP " for f, _, _ in ch))
            check("WebP: VP8X flags = %s, no inconsistent signal" % ("0x28" if tag == "stealth" else "0x08"),
                  flags == (0x28 if tag == "stealth" else 0x08)
                  and not any(s.kind == "inconsistent" for s in after.signals), hex(flags))
            res, before, after, v, dst = results[("webp_prefixed_orient6.webp", prof)]
            check("WebP with the Exif prefix: orientation 6 kept, prefix repeated",
                  v["orient_dst"] == 6 and b"Exif\0\0" in read(dst))
            res, before, after, v, dst = results[("webp_flag_without_chunk.webp", prof)]
            check("WebP flag without a chunk: flags fixed",
                  read(dst)[20] == 0x00 and any(a.act == Act.NORMALIZED for a in res.actions))

            res, before, after, v, dst = results[("gif_full.gif", prof)]
            d = read(dst)
            check("GIF: comment, XMP, tail removed; NETSCAPE2.0 (looping) kept",
                  b"ImageMagick" not in d and b"xmpmeta" not in d and b"PK\x03\x04" not in d
                  and b"NETSCAPE2.0" in d and d.endswith(b"\x3b"))

        for key in [k for k in results if k[0].startswith("fixtures.")]:
            res, before, after, v, dst = results[key]
            check("%s/%s: ok=True" % (key[0], key[1].value), res.ok, res.errors)

        # --- pathologies: always ok=False, errors, no exceptions, no dst left ---
        print("-" * 100)
        print("PATHOLOGIES (expect ok=False, errors, no exceptions, no file on disk)")
        good_jpeg = cases["jpeg_phone_orient6.jpg"]
        bad = {
            "empty file": b"",
            "cut in the middle of APP1": good_jpeg[:60],
            "JPEG without EOI": good_jpeg[:good_jpeg.index(b"\xff\xd9") - 5],
            "JPEG junk instead of a marker": b"\xff\xd8\xff" + bytes([0xE0]) + struct.pack(">H", 4)
                                          + b"xx" + b"\x00\x11\x22\x33",
            "JPEG without SOS": b"\xff\xd8" + jfif() + b"\xff\xd9",
            "PNG broken CRC": png_full.replace(b"Author\0Igor", b"Author\0Igoz"),
            "PNG cut inside IDAT": png_full[:png_full.index(b"IDAT") + 10],
            "PNG without IEND": png([])[:-12],
            "PNG without IDAT": _PNG_SIG + pchunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
                            + pchunk(b"IEND", b""),
            "PNG: IDAT shorter than IHDR declares": _PNG_SIG
            + pchunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + pchunk(b"IDAT", zlib.compress(b"\0" * 10)) + pchunk(b"IEND", b""),
            "WebP truncated": webp_full[:40],
            "GIF truncated": gif_full[:60],
            "TIFF (honest refusal)": tiff(b"II", {"IFD0": [(0x013B, 2, b"Igor\0")]}),
            "HEIC (honest refusal)": struct.pack(">I", 24) + b"ftypheic" + bytes(4) + b"heicmif1" + bytes(40),
            "not an image (text)": b"hello world, this is plain text\n" * 5,
        }
        for label, path in broken.items():
            bad["project fixture: " + label] = read(path) if label in ("jpeg_truncated", "png_ztxt_bomb") else None
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
            print("  %-42s %s" % (label, ("ok=False: " + res.errors[0][:70]) if res else "EXCEPTION"))
            check("pathology \"%s\": refused without an exception and without a file" % label, ok,
                  res and (res.ok, res.errors))
        # dst == src: the input is never overwritten
        src = put("same.jpg", good_jpeg)
        sha0 = _file_sha(src)
        res = clean_image(src, src)
        check("dst == src: refused, input intact", (not res.ok) and _file_sha(src) == sha0, res.errors)
        res = clean_image(os.path.join(tmp, "no_such_file.jpg"), os.path.join(outdir, "x.jpg"))
        check("missing input: refused without an exception", (not res.ok) and bool(res.errors))
        res = clean_image(src, os.path.join(outdir, "x.jpg"), profile="no-such-profile")
        check("unknown profile: refused without an exception", (not res.ok) and bool(res.errors))
        res = clean_image(src, os.path.join(outdir, "kept_icc.jpg"), Profile.PARANOID, keep={"icc"})
        check("keep={'icc'} keeps ICC in PARANOID too", res.ok and 0xE2 in jmarks(os.path.join(outdir, "kept_icc.jpg")))
        res = clean_image(src, os.path.join(outdir, "regen.jpg"), Profile.REGENERATE)
        check("REGENERATE is treated as PARANOID", res.ok and 0xE2 not in jmarks(os.path.join(outdir, "regen.jpg")))
        check("error messages contain no paths or file names",
              all("Users" not in e and tmp not in e for e in
                  clean_image(os.path.join(tmp, "nope.jpg"), os.path.join(outdir, "y.jpg")).errors))

        # untrusted strings from the file (segment tags, comments, tEXt keys, fourcc, appid)
        # must not carry control characters into the report
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
            check("%s: ok and no control characters in actions" % hname, hres.ok and not dirty,
                  (hres.errors, [(a.location, a.label) for a in dirty]))
        # Cyrillic and spaces in paths
        ru_dir = os.path.join(tmp, "папка с пробелом")
        os.makedirs(ru_dir)
        ru_src = os.path.join(ru_dir, "фото Игорь.jpg")
        with open(ru_src, "wb") as fh:
            fh.write(good_jpeg)
        res = clean_image(ru_src, os.path.join(ru_dir, "результат.jpg"))
        check("Cyrillic and spaces in paths", res.ok, res.errors)
        # the output directory doesn't exist: refused without an exception, input intact
        sha0 = _file_sha(ru_src)
        res = clean_image(ru_src, os.path.join(tmp, "no such directory", "x.jpg"))
        check("dst in a missing directory: refused without an exception", (not res.ok) and bool(res.errors)
              and _file_sha(ru_src) == sha0, res.errors)
        # rollback: if verification fails, dst doesn't remain on disk
        real_verify = globals()["_verify"]
        globals()["_verify"] = lambda fmt, a, b: {"ok": False, "errors": ["stub for the rollback "
                                                                          "test"]}
        try:
            rb_dst = os.path.join(outdir, "rollback.jpg")
            res = clean_image(ru_src, rb_dst)
        finally:
            globals()["_verify"] = real_verify
        check("verification failure: dst removed, actions empty, input intact",
              (not res.ok) and not os.path.exists(rb_dst) and not res.actions
              and _file_sha(ru_src) == sha0, res.errors)

        # --- optional independent decoder: Pillow, if it happens to be installed ------
        print("-" * 100)
        try:
            _pillow_check(tmp, outdir, check)
        except Exception as exc:                           # noqa: BLE001
            print("  Pillow check interrupted: %s: %s" % (type(exc).__name__, exc))
            check("the Pillow check must not crash", False, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 100)
    print("TOTAL: %s" % ("all checks passed" if not fails else "FAILED: %d" % len(fails)))
    for f in fails:
        print("  - " + f)
    return 1 if fails else 0


def _pillow_check(tmp, outdir, check) -> None:
    """Independent decoder (if installed): pixels BEFORE and AFTER cleaning match bit for bit,
    and the orientation is readable. The product doesn't need Pillow: it's only an outside witness."""
    try:
        from PIL import Image
    except ImportError:
        print("  Pillow not found: independent decoding skipped")
        return
    print("INDEPENDENT DECODER (Pillow %s): real images, pixels before == after" % Image.__version__)
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
    info.add_itxt("Description", "secret")
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
        print("  Pillow couldn't build the WebP (%s): skipped" % type(exc).__name__)
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
                check("Pillow/%s/%s: cleaning ok" % (name, prof.value), False, res.errors)
                continue
            fa, loop_a, orient_a = frames_of(src)
            fb, loop_b, orient_b = frames_of(dst)
            same = fa == fb
            print("  %-22s %-8s pixels match=%s frames=%d orientation %s->%s loop %s->%s size "
                  "%d->%d B"
                  % (name, prof.value, same, len(fb), orient_a, orient_b, loop_a, loop_b,
                     os.path.getsize(src), os.path.getsize(dst)))
            check("Pillow/%s/%s: pixels BEFORE and AFTER are identical" % (name, prof.value), same)
            check("Pillow/%s/%s: orientation and looping kept" % (name, prof.value),
                  orient_a == orient_b and loop_a == loop_b, (orient_a, orient_b, loop_a, loop_b))
            with open(dst, "rb") as fh:
                blob = fh.read()
            check("Pillow/%s/%s: secrets from the file are gone" % (name, prof.value),
                  not any(s in blob for s in (b"Igor", b"SERIAL-123", b"secret", b"Canon", b"EOS R5",
                                              b"Photoshop 25", b"2024:05:17")))


if __name__ == "__main__":
    raise SystemExit(_selftest())
