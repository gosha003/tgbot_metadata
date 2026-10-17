"""Raster image inspector: JPEG, PNG, TIFF, WebP, GIF, HEIF/HEIC.

Stdlib only (struct, zlib, binascii, re) -- no Pillow, no piexif, no
mandatory exiftool. The reason is a hard one: this module is called
recursively for images pulled out of docx/pptx/PDF and must ALWAYS work.

Phase 0: read-only. The file is opened strictly for reading and no
exception is ever raised to the caller -- any parsing problem goes into
report.errors.

exiftool is only an optional enrichment: if it is found on PATH or in
./tools/exiftool.exe it supplements the findings; if not, we quietly
move on.
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
from core.i18n import N_, tr

# --- limits: files can be damaged or deliberately malformed ----------------

MAX_FILE = 50 * 1024 * 1024      # never load more than this into memory
MAX_INFLATE = 16 * 1024 * 1024   # limit on inflating a single zlib stream
MAX_RATIO = 500                  # compression ratio limit (zip bomb)
MAX_SEGMENTS = 512               # JPEG markers
MAX_CHUNKS = 1024                # PNG chunks
MAX_IFDS = 16                    # IFDs in TIFF/EXIF
MAX_ENTRIES = 1000               # tags in a single IFD
MAX_BOXES = 512                  # ISOBMFF boxes
MAX_FIELDS = 200                 # XMP fields printed by name
MAX_GIF_BLOCKS = 512

SUPPORTED = ("jpeg", "png", "tiff", "webp", "gif", "heic")


# ===========================================================================
# small helpers
# ===========================================================================

def _u16(b, off, en=">"):
    return struct.unpack_from(en + "H", b, off)[0]


def _u32(b, off, en=">"):
    return struct.unpack_from(en + "I", b, off)[0]


def _text(raw, enc="utf-8"):
    """Bytes to string: untrusted input, so always decode with replacement of bad characters."""
    if isinstance(raw, str):
        return raw
    return bytes(raw).decode(enc, "replace")


def _hexdump(raw, n=16):
    return bytes(raw[:n]).hex(" ")


def _inflate(blob, report, loc):
    """zlib inflate with double protection: by output size and by compression ratio."""
    if not blob:
        return b""
    try:
        d = zlib.decompressobj()
        out = d.decompress(bytes(blob), MAX_INFLATE)
        if not d.eof or d.unconsumed_tail:
            report.err(tr("%s: inflation cut off at the limit of %d bytes") % (loc, MAX_INFLATE))
        if len(blob) and len(out) // max(len(blob), 1) > MAX_RATIO:
            report.err(tr("%s: suspicious compression ratio %dx") % (loc, len(out) // max(len(blob), 1)))
            return out[:MAX_INFLATE]
        return out
    except zlib.error as exc:
        report.err(tr("%s: corrupt zlib stream (%s)") % (loc, exc))
        return b""


# ===========================================================================
# EXIF / TIFF
# ===========================================================================

# Size of one element by TIFF type code.
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8,
              11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}

# tag -> (label, risk, explanation). A shared table for IFD0 and ExifIFD.
_TAGS = {
    0x010E: ("ImageDescription", Risk.IDENTITY,
             N_("Frame description: often the author's full name, or the name of a project or "
                "client")),
    0x010F: ("Make", Risk.PROVENANCE, N_("Camera manufacturer")),
    0x0110: ("Model", Risk.PROVENANCE, N_("Camera model: narrows down the circle of possible "
                                          "owners")),
    0x0131: ("Software", Risk.PROVENANCE, N_("What processed the file")),
    0x0132: ("DateTime", Risk.PROVENANCE, N_("Time of the file's last edit")),
    0x013B: ("Artist", Risk.IDENTITY, N_("Author of the shot, set in the camera or in an editor")),
    0x013C: ("HostComputer", Risk.ENVIRONMENT,
             N_("Name of the computer or machine model that processed the file")),
    0x8298: ("Copyright", Risk.IDENTITY, N_("Copyright: almost always a person's full name or a "
                                            "company name")),
    0x9003: ("DateTimeOriginal", Risk.PROVENANCE, N_("Moment of capture")),
    0x9004: ("DateTimeDigitized", Risk.PROVENANCE, N_("Moment of digitization")),
    0x9010: ("OffsetTime", Risk.PROVENANCE, N_("Device time zone: hints at the region")),
    0x9011: ("OffsetTimeOriginal", Risk.PROVENANCE, N_("Time zone at the moment of capture")),
    0x9012: ("OffsetTimeDigitized", Risk.PROVENANCE, N_("Time zone at digitization")),
    0x9290: ("SubSecTime", Risk.PROVENANCE, N_("Fractions of a second: refine the timestamp")),
    0x9291: ("SubSecTimeOriginal", Risk.PROVENANCE, N_("Fractions of a second of the capture")),
    0x9292: ("SubSecTimeDigitized", Risk.PROVENANCE, N_("Fractions of a second of the "
                                                        "digitization")),
    0x9000: ("ExifVersion", Risk.PROVENANCE, N_("EXIF version: a fingerprint of the "
                                                "firmware/software")),
    0x9286: ("UserComment", Risk.IDENTITY, N_("Free-form user comment")),
    0xA420: ("ImageUniqueID", Risk.IDENTITY, N_("Unique frame identifier: links files to each "
                                                "other")),
    0xA430: ("CameraOwnerName", Risk.IDENTITY, N_("Name of the camera owner, written into the "
                                                  "firmware")),
    0xA431: ("BodySerialNumber", Risk.IDENTITY,
             N_("Body serial number: ties the file to one specific camera unit")),
    0xA433: ("LensMake", Risk.PROVENANCE, N_("Lens manufacturer")),
    0xA434: ("LensModel", Risk.PROVENANCE, N_("Lens model")),
    0xA435: ("LensSerialNumber", Risk.IDENTITY, N_("Lens serial number")),
    0xC614: ("UniqueCameraModel", Risk.PROVENANCE, N_("Camera model according to DNG")),
    0xC62F: ("CameraSerialNumber", Risk.IDENTITY, N_("Camera serial number according to DNG")),
    0x9C9B: ("XPTitle", Risk.IDENTITY, N_("Title from Windows Explorer (UTF-16LE)")),
    0x9C9C: ("XPComment", Risk.IDENTITY, N_("Comment from Windows Explorer (UTF-16LE)")),
    0x9C9D: ("XPAuthor", Risk.IDENTITY, N_("Author from Windows Explorer (UTF-16LE)")),
    0x9C9E: ("XPKeywords", Risk.IDENTITY, N_("Keywords from Windows Explorer (UTF-16LE)")),
    0x9C9F: ("XPSubject", Risk.IDENTITY, N_("Subject from Windows Explorer (UTF-16LE)")),
}

# Tags in UTF-16LE (Windows extensions of EXIF).
_XP_TAGS = (0x9C9B, 0x9C9C, 0x9C9D, 0x9C9E, 0x9C9F)

# Tags that point to nested IFDs.
_SUB_IFD = {0x8769: "ExifIFD", 0x8825: "GPS", 0xA005: "Interop", 0x014A: "SubIFD"}

# Structural: usually kept when cleaning.
_STRUCTURAL = {
    0x0100: "ImageWidth", 0x0101: "ImageLength", 0x0102: "BitsPerSample",
    0x0103: "Compression", 0x0106: "PhotometricInterpretation",
    0x0112: "Orientation", 0x0115: "SamplesPerPixel", 0x011A: "XResolution",
    0x011B: "YResolution", 0x0128: "ResolutionUnit", 0x0213: "YCbCrPositioning",
    0x011C: "PlanarConfiguration", 0xA001: "ColorSpace", 0xA002: "PixelXDimension",
    0xA003: "PixelYDimension", 0x0111: "StripOffsets", 0x0116: "RowsPerStrip",
    0x0117: "StripByteCounts", 0x0142: "TileWidth", 0x0143: "TileLength",
}

# Names of other commonly seen tags -- needed only for a readable summary.
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

# Known MakerNote headers -- we recognize the vendor but don't look inside.
_MAKERNOTE_VENDORS = (
    (b"Nikon", "Nikon"), (b"OLYMPUS", "Olympus"), (b"OM SYSTEM", "OM System"),
    (b"SONY DSC", "Sony"), (b"FUJIFILM", "Fujifilm"), (b"PENTAX", "Pentax"),
    (b"Panasonic", "Panasonic"), (b"LEICA", "Leica"), (b"Apple iOS", "Apple"),
    (b"SAMSUNG", "Samsung"), (b"RICOH", "Ricoh"), (b"SIGMA", "Sigma"),
    (b"CASIO", "Casio"), (b"KONICA", "Konica"), (b"QVC", "Casio QV"),
)


def _decode_value(typ, count, raw, en):
    """Raw bytes of a tag value -> Python value, by TIFF type code."""
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
    """Tag value in printable form."""
    if isinstance(value, (bytes, bytearray)):
        return tr("<%d bytes: %s>") % (len(value), _hexdump(value))
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
    """Degrees/minutes/seconds -> decimal degrees. We hand back a ready-to-use coordinate."""
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
    """Walk the TIFF structure: IFD0 -> ExifIFD/GPS/Interop -> IFD1 (thumbnail).

    Returns {ifd_name: {tag: (typ, value)}}. Offsets form loops in broken
    files, so visited IFDs are remembered and the number of visits is capped.
    """
    out = {}
    if len(blob) < 8:
        report.err(tr("%s: EXIF is shorter than a TIFF header (%d bytes)") % (loc, len(blob)))
        return out
    order = blob[:2]
    if order == b"II":
        en = "<"
    elif order == b"MM":
        en = ">"
    else:
        report.err(tr("%s: unknown byte order %r") % (loc, order[:2]))
        return out
    try:
        magic, first = struct.unpack_from(en + "HI", blob, 2)
    except struct.error:
        report.err(tr("%s: TIFF header is truncated") % loc)
        return out
    if magic == 43:
        report.err(tr("%s: BigTIFF is not supported, only the header was parsed") % loc)
        return out
    if magic != 42:
        report.err(tr("%s: invalid TIFF signature (%d)") % (loc, magic))
        return out

    seen = set()
    queue = [(first, "IFD0")]
    guard = 0
    while queue and guard < MAX_IFDS:
        off, name = queue.pop(0)
        if off <= 0 or off >= len(blob) or off in seen:
            if off in seen:
                report.err(tr("%s: %s points to an IFD that was already parsed (looped EXIF)") % (loc, name))
            elif off:
                report.err(tr("%s: %s points outside the block (offset %d)") % (loc, name, off))
            continue
        seen.add(off)
        guard += 1
        try:
            count = _u16(blob, off, en)
        except struct.error:
            report.err(tr("%s: %s is truncated") % (loc, name))
            continue
        if count > MAX_ENTRIES:
            report.err(tr("%s: %s declares %d tags, parsing the first %d")
                       % (loc, name, count, MAX_ENTRIES))
            count = MAX_ENTRIES
        tags = out.setdefault(name, {})
        # A malformed IFD can raise an error for every single tag. A report of a
        # thousand identical lines is useless, so we make noise only the first
        # eight times.
        noise = [0]

        def bad(msg):
            noise[0] += 1
            if noise[0] <= 8:
                report.err(msg)

        for i in range(count):
            eoff = off + 2 + i * 12
            if eoff + 12 > len(blob):
                bad(tr("%s: %s truncated at tag %d") % (loc, name, i))
                break
            tag, typ, cnt = struct.unpack_from(en + "HHI", blob, eoff)
            size = _TYPE_SIZE.get(typ, 0) * cnt
            if not _TYPE_SIZE.get(typ):
                tags[tag] = (typ, b"")
                continue
            if size > MAX_INFLATE:
                bad(tr("%s: %s tag 0x%04X declares %d bytes, skipped") % (loc, name, tag, size))
                continue
            if size <= 4:
                raw = blob[eoff + 8:eoff + 8 + size]
            else:
                voff = _u32(blob, eoff + 8, en)
                if voff + size > len(blob) or voff < 8:
                    bad(tr("%s: %s tag 0x%04X points outside the block") % (loc, name, tag))
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
            report.err(tr("%s: %s -- %d more similar tag parsing errors suppressed")
                       % (loc, name, noise[0] - 8))
        # chain of next IFDs (IFD1 is the embedded thumbnail)
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
        report.err(tr("%s: reached the limit of %d IFDs, walk stopped") % (loc, MAX_IFDS))

    _report_exif(report, out, loc, ctx)
    return out


def _report_exif(report, ifds, loc, ctx):
    """Turn the parsed IFDs into findings."""
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
                    tr("%d bytes%s, starts with: %s") % (len(raw), (tr(", vendor ") + vendor) if vendor else "",
                                               _hexdump(raw)),
                    note=tr("Proprietary camera block. We don't look inside, but this is usually "
                            "where the unit's serial number, the frame counter and personal "
                            "settings live"),
                )
                continue
            if tag == 0x02BC:                       # XMLPacket = XMP inside TIFF
                _parse_xmp(report, _text(bytes(value) if isinstance(value, (bytes, bytearray)) else value),
                           where + ":XMP", ctx)
                continue
            if tag == 0x8773:                       # ICC inside TIFF
                blob = bytes(value) if isinstance(value, (bytes, bytearray)) else b""
                desc = _icc_desc(blob)
                report.add(Risk.STRUCTURAL, where, tr("ICC profile"),
                           tr("%d bytes%s") % (len(blob), (tr(", description: ") + desc) if desc else ""),
                           note=tr("Color profile: usually kept when cleaning, but the profile "
                                   "name may name a specific monitor or scanner"))
                continue
            if tag in (0x83BB, 0x8649):             # IPTC-NAA / Photoshop IRB in TIFF
                blob = bytes(value) if isinstance(value, (bytes, bytearray)) else b""
                if tag == 0x8649:
                    _parse_irb(report, blob, where + ":Photoshop", ctx)
                else:
                    _parse_iptc(report, blob, where + ":IPTC", ctx)
                continue
            if tag in _TAGS:
                label, risk, note = _TAGS[tag]
                shown = _show(value)
                report.add(risk, where, label, shown, note=tr(note))
                _remember(ctx, label, shown)
                continue
            if tag in _STRUCTURAL:
                minor.append("%s=%s" % (_STRUCTURAL[tag], _show(value)))
                continue
            minor.append("%s=%s" % (_MINOR.get(tag, "0x%04X" % tag), _show(value)))
        if minor:
            report.add(Risk.STRUCTURAL, where, tr("Other tags (%d)") % len(minor),
                       "; ".join(minor[:40]),
                       note=tr("Technical shooting parameters and geometry. Not PII on their "
                               "own, but together they fingerprint a specific camera and its "
                               "settings"))
        # embedded thumbnail
        if name != "IFD0" and 0x0201 in tags:
            try:
                thumb_len = tags.get(0x0202, (4, [0]))[1][0]
            except (IndexError, TypeError):
                thumb_len = 0
            report.add(Risk.IDENTITY, where, tr("Embedded thumbnail"), tr("%d bytes") % thumb_len,
                       note=tr("The thumbnail may have been made BEFORE retouching and cropping: "
                               "it sometimes shows the whole original frame, including what was "
                               "painted over"))
            ctx["thumbnail"] = thumb_len


def _report_gps(report, tags, where, ctx):
    """GPS is the most valuable finding in an image, so we compute the coordinate right away."""
    plain = {}
    for tag, (typ, value) in sorted(tags.items()):
        label = _GPS_TAGS.get(tag, "GPS:0x%04X" % tag)
        plain[label] = value
        if label in ("GPSLatitude", "GPSLongitude"):
            continue  # shown recomputed below
        if tag in (0x001B, 0x001C) and isinstance(value, (bytes, bytearray)):
            value = _text(bytes(value)[8:]) or _hexdump(value)
        report.add(Risk.IDENTITY, where, label, _show(value),
                   note=tr("Geolocation data of the shot"))
    lat = _gps_decimal(plain.get("GPSLatitude"), plain.get("GPSLatitudeRef", ""))
    lon = _gps_decimal(plain.get("GPSLongitude"), plain.get("GPSLongitudeRef", ""))
    if lat is not None and lon is not None:
        coord = "%.6f, %.6f" % (lat, lon)
        ctx["gps"] = coord
        alt = plain.get("GPSAltitude")
        extra = ""
        if isinstance(alt, list) and alt and isinstance(alt[0], tuple):
            extra = tr(", altitude %.1f m") % _ratio(alt[0])
        report.add(Risk.IDENTITY, where, tr("Coordinates (decimal)"), coord + extra,
                   note=tr("A ready-made point on the map: shooting address, home, office. The "
                           "most dangerous finding in an image -- it leads to a person, not to a "
                           "file"))


def _icc_desc(blob):
    """Name from the ICC profile description: often named after the device or monitor."""
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

# prefix:name -> (label, risk)
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
# Provenance fields are printed as a separate block, so we don't put them in the "other" summary.
_XMP_PROV = ("xmp:CreatorTool", "tiff:Software", "stEvt:softwareAgent",
             "xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate",
             "tiff:Make", "tiff:Model", "pdf:Producer", "GCamera:MicroVideo")
_AI_XMP = ("trainedalgorithmicmedia", "compositewithtrainedalgorithmicmedia",
           "algorithmicmedia")


def _parse_xmp(report, text, loc, ctx):
    """XMP packet. A full XML parser is unneeded and risky -- we pull fields with regexes."""
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
    # rdf:li inside dc:creator and similar containers
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
                   note=tr("XMP field. XMP is duplicated in EXIF and IPTC and survives re-saving "
                           "in most editors"))
        _remember(ctx, key, value)
    for key in _XMP_PROV:
        if key in seen:
            report.add(Risk.PROVENANCE, loc, key, seen[key],
                       note=tr("What made or modified the file, and when"))
            _remember(ctx, key, seen[key])
    agents = re.findall(r"stEvt:softwareAgent\s*=\s*\"([^\"]{0,200})\"", text)
    if len(agents) > 1:
        report.add(Risk.PROVENANCE, loc, tr("Edit history xmpMM:History"),
                   "; ".join(dict.fromkeys(agents))[:400],
                   note=tr("Chain of tools the file went through: the whole pipeline is visible"))
        for agent in agents:
            _remember(ctx, "softwareAgent", agent)
    if minor:
        report.add(Risk.PROVENANCE, loc, tr("Other XMP fields (%d)") % len(minor),
                   "; ".join(minor[:40]),
                   note=tr("Develop settings and technical fields: not PII, but they fingerprint "
                           "a specific editor and its version"))
    low = text.lower()
    for marker in _AI_XMP:
        if marker in low:
            report.signal("ai", tr("XMP declares digitalSourceType=%s: the file is marked as "
                                   "generated or edited by AI") % marker, "high")
            break


# ===========================================================================
# IPTC-IIM and Photoshop IRB
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
    """IPTC-IIM: a stream of records 0x1C record dataset length data."""
    pos, guard = 0, 0
    while pos + 5 <= len(blob) and guard < 512:
        if blob[pos] != 0x1C:
            pos += 1
            continue
        guard += 1
        record, dataset = blob[pos + 1], blob[pos + 2]
        length = _u16(blob, pos + 3)
        head = 5
        if length & 0x8000:                      # extended size
            nbytes = length & 0x7FFF
            if nbytes > 4 or pos + 5 + nbytes > len(blob):
                break
            length = int.from_bytes(blob[pos + 5:pos + 5 + nbytes], "big")
            head = 5 + nbytes
        if pos + head + length > len(blob) or length > MAX_INFLATE:
            report.err(tr("%s: IPTC record 2:%d is truncated") % (loc, dataset))
            break
        value = _text(blob[pos + head:pos + head + length])
        pos += head + length
        if record != 2:
            continue
        label, risk = _IPTC_2.get(dataset, ("IPTC 2:%d" % dataset, Risk.PROVENANCE))
        report.add(risk, loc, label, value,
                   note=tr("IPTC-IIM: the old photo caption format; editors carry it between "
                           "files along with the picture"))
        _remember(ctx, label, value)


def _parse_irb(report, blob, loc, ctx):
    """Photoshop Image Resource Block: a sequence of 8BIM."""
    pos, guard, found = 0, 0, []
    while pos + 12 <= len(blob) and guard < 512:
        if blob[pos:pos + 4] != b"8BIM":
            pos += 1
            continue
        guard += 1
        res_id = _u16(blob, pos + 4)
        nlen = blob[pos + 6]
        npad = nlen + 1 + ((nlen + 1) % 2)        # Pascal string, padded to an even length
        size_off = pos + 6 + npad
        if size_off + 4 > len(blob):
            break
        size = _u32(blob, size_off)
        start = size_off + 4
        if size > len(blob) - start or size > MAX_INFLATE:
            report.err(tr("%s: resource 0x%04X declares %d bytes, truncated") % (loc, res_id, size))
            break
        data = blob[start:start + size]
        pos = start + size + (size % 2)
        name = _IRB_IDS.get(res_id, "8BIM 0x%04X" % res_id)
        found.append(tr("%s (%d B)") % (name, size))
        if res_id == 0x0404:
            _parse_iptc(report, data, loc + ":IPTC", ctx)
        elif res_id == 0x0424:
            _parse_xmp(report, _text(data), loc + ":XMP", ctx)
        elif res_id == 0x040C:
            report.add(Risk.IDENTITY, loc, tr("Photoshop thumbnail"), tr("%d bytes") % size,
                       note=tr("The thumbnail was saved by the editor and may have been made "
                               "BEFORE the edits: it sometimes shows the original frame"))
        elif res_id == 0x040B:
            report.add(Risk.ENVIRONMENT, loc, "URL", _text(data),
                       note=tr("URL embedded in the Photoshop resources"))
        elif res_id == 0x0BB7:
            report.add(Risk.ENVIRONMENT, loc, tr("Clipping path name"), _text(data),
                       note=tr("Name of the clipping path: set by a person, can be revealing"))
    if found:
        report.add(Risk.PROVENANCE, loc, tr("Photoshop resources (%d)") % len(found),
                   "; ".join(found[:30]),
                   note=tr("The mere presence of 8BIM means the file was opened in Photoshop or "
                           "a compatible editor"))
        ctx["photoshop"] = True


# ===========================================================================
# JPEG
# ===========================================================================

_APP_NAMES = {0xE0: "APP0", 0xE1: "APP1", 0xE2: "APP2", 0xE3: "APP3", 0xE4: "APP4",
              0xE5: "APP5", 0xE6: "APP6", 0xE7: "APP7", 0xE8: "APP8", 0xE9: "APP9",
              0xEA: "APP10", 0xEB: "APP11", 0xEC: "APP12", 0xED: "APP13",
              0xEE: "APP14", 0xEF: "APP15"}

C2PA_NOTE = (
    N_("CRYPTOGRAPHICALLY SIGNED provenance manifest (C2PA / Content Credentials). Removing it "
       "is itself detectable: the recipient sees that a signature existed and was stripped. "
       "Moreover, with a soft binding or a watermark the provenance can be recovered from a "
       "registry even without this block in the file")
)


def _jpeg(report, data, ctx):
    """Walk the JPEG markers: FFD8, then FFxx + big-endian length, up to FFDA/SOS."""
    seg_map = []
    pos, guard = 2, 0
    icc_chunks = 0
    while pos + 1 < len(data) and guard < MAX_SEGMENTS:
        if data[pos] != 0xFF:
            report.err(tr("JPEG: a marker was expected at offset %d, found 0x%02X") % (pos, data[pos]))
            break
        while pos + 1 < len(data) and data[pos + 1] == 0xFF:   # fill bytes
            pos += 1
        if pos + 1 >= len(data):
            report.err(tr("JPEG: the stream ends at a marker"))
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
            report.err(tr("JPEG: marker 0x%02X has no length (file truncated)") % marker)
            break
        length = _u16(data, pos + 2)
        if length < 2:
            report.err(tr("JPEG: marker 0x%02X declares length %d") % (marker, length))
            break
        end = pos + 2 + length
        if end > len(data):
            report.err(tr("JPEG: segment 0x%02X is truncated (needs %d bytes, have %d)")
                       % (marker, length, len(data) - pos - 2))
            seg = data[pos + 4:]
            end = len(data)
        else:
            seg = data[pos + 4:end]
        name = _APP_NAMES.get(marker) or {0xFE: "COM", 0xDB: "DQT", 0xC4: "DHT",
                                          0xDD: "DRI", 0xDA: "SOS"}.get(marker)
        if name is None:
            name = "SOF%d" % (marker - 0xC0) if 0xC0 <= marker <= 0xCF else "0x%02X" % marker
        seg_map.append(tr("%s (%d B)") % (name, length))
        try:
            icc_chunks += _jpeg_segment(report, marker, name, seg, ctx)
        except Exception as exc:                  # noqa: BLE001 -- a bad segment must not fail the file
            report.err(tr("JPEG: %s was not parsed (%s: %s)") % (name, type(exc).__name__, exc))
        if marker == 0xDA:
            break
        pos = end
    if guard >= MAX_SEGMENTS:
        report.err(tr("JPEG: reached the limit of %d segments") % MAX_SEGMENTS)
    if icc_chunks > 1:
        report.add(Risk.STRUCTURAL, "APP2:ICC_PROFILE", tr("Profile parts"), str(icc_chunks),
                   note=tr("The ICC profile is split across several APP2 segments; the "
                           "description is read from the first part"))
    report.add(Risk.STRUCTURAL, "JPEG", tr("Segment map (%d)") % len(seg_map),
               "; ".join(seg_map[:60]),
               note=tr("Full list of markers with sizes: shows what exactly is about to be cut "
                       "out and what will remain"))
    ctx["segments"] = seg_map
    _trailing(report, data, b"\xff\xd9", "JPEG", "EOI")


def _jpeg_segment(report, marker, name, seg, ctx):
    """Parse one segment. Returns 1 if it is part of an ICC profile."""
    if marker == 0xE0:                                      # APP0 = JFIF
        if seg[:5] == b"JFIF\x00" and len(seg) >= 14:
            units = {0: tr("no units (aspect ratio)"), 1: tr("dots per inch"), 2: tr("dots per cm")}
            report.add(Risk.STRUCTURAL, "APP0:JFIF", "JFIF",
                       tr("version %d.%02d, density %dx%d, %s")
                       % (seg[5], seg[6], _u16(seg, 8), _u16(seg, 10),
                          units.get(seg[7], tr("code %d") % seg[7])),
                       note=tr("Basic JPEG header. The density sometimes gives away a scanner or "
                               "a specific converter"))
        elif seg[:5] == b"JFXX\x00":
            report.add(Risk.PROVENANCE, "APP0:JFXX", tr("JFXX thumbnail"), tr("%d bytes") % len(seg),
                       note=tr("JFIF extension with an embedded thumbnail"))
        return 0
    if marker == 0xE1:                                      # APP1 = EXIF or XMP
        if seg[:6] == b"Exif\x00\x00":
            ctx["exif"] = True
            _walk_tiff(report, seg[6:], "APP1:Exif", ctx)
        elif seg.startswith(b"http://ns.adobe.com/xap/1.0/\x00"):
            _parse_xmp(report, _text(seg[29:]), "APP1:XMP", ctx)
        elif seg.startswith(b"http://ns.adobe.com/xmp/extension/"):
            report.add(Risk.PROVENANCE, "APP1:XMP-extension", tr("Extended XMP"),
                       tr("%d bytes") % len(seg),
                       note=tr("Continuation of an XMP packet that did not fit into 64 KB"))
            _parse_xmp(report, _text(seg[75:]), "APP1:XMP-extension", ctx)
        else:
            report.add(Risk.PROVENANCE, "APP1", tr("Unrecognized APP1"),
                       tr("%d bytes, starts with: %s") % (len(seg), _hexdump(seg)),
                       note=tr("APP1 segment with a foreign signature: something appended it by "
                               "hand"))
        return 0
    if marker == 0xE2:                                      # APP2 = ICC or MPF
        if seg[:12] == b"ICC_PROFILE\x00":
            seq, total = (seg[12], seg[13]) if len(seg) > 13 else (1, 1)
            if seq <= 1:
                desc = _icc_desc(seg[14:])
                report.add(Risk.STRUCTURAL, "APP2:ICC_PROFILE", tr("ICC profile"),
                           tr("%d bytes, part %d of %d%s")
                           % (len(seg), seq, total, (tr(", description: ") + desc) if desc else ""),
                           note=tr("The color profile is normally KEPT. But the description name "
                                   "is often that of a specific monitor, printer or scanner -- "
                                   "which already points to a workplace"))
                if desc:
                    _remember(ctx, "ICC", desc)
            return 1
        if seg[:4] == b"MPF\x00":
            report.add(Risk.PROVENANCE, "APP2:MPF", "Multi-Picture Format", tr("%d bytes") % len(seg),
                       note=tr("A container of several images in one file: the second frame may "
                               "lie inside in full, with its own metadata"))
            return 0
        report.add(Risk.PROVENANCE, "APP2", tr("Unrecognized APP2"),
                   tr("%d bytes, starts with: %s") % (len(seg), _hexdump(seg)))
        return 0
    if marker == 0xEB:                                      # APP11 = JUMBF / C2PA
        low = bytes(seg[:4096])
        if b"jumb" in low or b"c2pa" in low or b"jumd" in low:
            ctx["c2pa"] = True
            report.add(Risk.PROVENANCE, "APP11:JUMBF", tr("C2PA / Content Credentials manifest"),
                       tr("%d bytes, markers: %s") % (len(seg),
                                               ", ".join(m for m in ("jumb", "jumd", "c2pa")
                                                         if m.encode() in low)),
                       note=tr(C2PA_NOTE), removable=False)
        else:
            report.add(Risk.PROVENANCE, "APP11", tr("JUMBF container without C2PA markers"),
                       tr("%d bytes, starts with: %s") % (len(seg), _hexdump(seg)))
        return 0
    if marker == 0xED:                                      # APP13 = Photoshop IRB
        if seg[:14] == b"Photoshop 3.0\x00":
            _parse_irb(report, seg[14:], "APP13:Photoshop", ctx)
        else:
            _parse_irb(report, seg, "APP13", ctx)
        return 0
    if marker == 0xEE:                                      # APP14 = Adobe
        if seg[:5] == b"Adobe" and len(seg) >= 12:
            transform = {0: "RGB/CMYK", 1: "YCbCr", 2: "YCCK"}.get(seg[11], tr("code %d") % seg[11])
            report.add(Risk.PROVENANCE, "APP14:Adobe", tr("Adobe marker"),
                       tr("version %d, transform=%s") % (_u16(seg, 5), transform),
                       note=tr("The segment is written by Photoshop and Adobe libraries: it "
                               "shows which software the file went through"))
            ctx["photoshop"] = True
        return 0
    if marker == 0xFE:                                      # COM
        report.add(Risk.PROVENANCE, "COM", tr("JPEG comment"), _text(seg),
                   note=tr("Text comment. Encoders write their own name and version here, people "
                           "write anything"))
        _remember(ctx, "COM", _text(seg))
        return 0
    if marker == 0xDB:                                      # DQT
        tables = []
        p = 0
        while p < len(seg):
            prec, tid = seg[p] >> 4, seg[p] & 0x0F
            size = 64 * (2 if prec else 1)
            tables.append(tr("table %d (%d-bit)") % (tid, 16 if prec else 8))
            p += 1 + size
        report.add(Risk.PROVENANCE, "DQT", tr("Quantization tables"),
                   tr("%d tables: %s") % (len(tables), ", ".join(tables[:8])),
                   note=tr("CANNOT be removed without re-encoding with quality loss. At the same "
                           "time the tables fingerprint the encoder and the quality setting: "
                           "they reveal what re-saved the file and at what quality"),
                   removable=False)
        return 0
    if 0xE3 <= marker <= 0xEF:                              # other APPn
        report.add(Risk.PROVENANCE, name, tr("Non-standard segment"),
                   tr("%d bytes, starts with: %s") % (len(seg), _hexdump(seg)),
                   note=tr("An application wrote its own block into the file. The content is "
                           "arbitrary"))
    return 0


def _trailing(report, data, eof_marker, fmt, label, extra=0):
    """Data after the logical end of the file -- a classic hiding place.

    extra -- how many bytes normally follow the marker itself (in PNG the
    IEND chunk type is followed by its CRC, and that is not a tail yet).
    """
    idx = data.rfind(eof_marker)
    if idx == -1:
        report.err(tr("%s: marker %s not found, the file is truncated") % (fmt, label))
        return
    stop = idx + len(eof_marker) + extra
    tail = len(data) - stop
    if tail > 0:
        report.add(Risk.ENVIRONMENT, fmt, tr("Data after %s") % label,
                   tr("%d bytes, starts with: %s") % (tail, _hexdump(data[stop:])),
                   note=tr("There are more bytes after the end of the image. Decoders ignore "
                           "them, so anything can be hidden there -- from an archive to someone "
                           "else's document"))
        report.signal("hazard", tr("after marker %s there are %d more bytes of data") % (label, tail), "medium")


# ===========================================================================
# PNG
# ===========================================================================

_PNG_KNOWN = {
    b"IHDR": N_("header"), b"PLTE": N_("palette"), b"IDAT": N_("pixels"), b"IEND": N_("end"),
    b"gAMA": N_("gamma"), b"cHRM": N_("chromaticity"), b"sRGB": "sRGB", b"bKGD": N_("background"),
    b"tRNS": N_("transparency"), b"hIST": N_("histogram"), b"sPLT": N_("suggested palette"),
    b"acTL": N_("APNG: control"), b"fcTL": N_("APNG: frame"), b"fdAT": N_("APNG: data"),
    b"cICP": N_("coding parameters"), b"mDCv": N_("HDR mastering"), b"cLLi": N_("HDR brightness"),
    b"eXIf": "EXIF", b"iCCP": "ICC", b"pHYs": N_("density"), b"sBIT": N_("significant bits"),
    b"tIME": N_("modification time"), b"tEXt": N_("text"), b"zTXt": N_("compressed text"),
    b"iTXt": N_("UTF-8 text"), b"caBX": "C2PA", b"dSIG": N_("signature"),
}


def _png(report, data, ctx):
    """Walk the PNG chunks: length(4) type(4) data CRC(4)."""
    pos, guard = 8, 0
    chunk_map, texts = [], 0
    while pos + 8 <= len(data) and guard < MAX_CHUNKS:
        guard += 1
        length = _u32(data, pos)
        ctype = bytes(data[pos + 4:pos + 8])
        if length > MAX_FILE:
            report.err(tr("PNG: chunk %s declares %d bytes") % (_text(ctype), length))
            break
        end = pos + 8 + length + 4
        if end > len(data):
            report.err(tr("PNG: chunk %s is truncated (needs %d bytes, have %d)")
                       % (_text(ctype), length, len(data) - pos - 12))
            break
        body = data[pos + 8:pos + 8 + length]
        crc_stored = _u32(data, pos + 8 + length)
        if binascii.crc32(ctype + bytes(body)) & 0xFFFFFFFF != crc_stored:
            report.err(tr("PNG: bad CRC in chunk %s") % _text(ctype))
            ctx["bad_crc"] = ctx.get("bad_crc", 0) + 1
        chunk_map.append(tr("%s (%d B)") % (_text(ctype), length))
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
                report.add(Risk.STRUCTURAL, "PNG:iCCP", tr("ICC profile"),
                           "%s%s" % (_text(name), (tr(", description: ") + desc) if desc else ""),
                           note=tr("The color profile is usually kept, but its name is often "
                                   "that of a specific monitor or scanner"))
                _remember(ctx, "ICC", _text(name) + " " + desc)
            elif ctype == b"tIME":
                if length >= 7:
                    y, mo, d, h, mi, s = struct.unpack(">HBBBBB", bytes(body[:7]))
                    report.add(Risk.PROVENANCE, "PNG:tIME", tr("Last modification time"),
                               "%04d-%02d-%02d %02d:%02d:%02d UTC" % (y, mo, d, h, mi, s),
                               note=tr("Moment the image was last changed"))
            elif ctype == b"pHYs":
                if length >= 9:
                    x, y = _u32(body, 0), _u32(body, 4)
                    unit = tr("px/m") if body[8] == 1 else tr("no units")
                    report.add(Risk.STRUCTURAL, "PNG:pHYs", tr("Density"),
                               "%d x %d %s" % (x, y, unit),
                               note=tr("Physical size: sometimes gives away the scanner's "
                                       "original DPI"))
            elif ctype == b"sBIT":
                report.add(Risk.STRUCTURAL, "PNG:sBIT", tr("Significant bits"),
                           ", ".join(str(b) for b in bytes(body)),
                           note=tr("Bit depth of the source data: hints at the source (scanner, "
                                   "render)"))
            elif ctype == b"caBX":
                ctx["c2pa"] = True
                report.add(Risk.PROVENANCE, "PNG:caBX", tr("C2PA / Content Credentials manifest"),
                           tr("%d bytes") % length, note=tr(C2PA_NOTE), removable=False)
            elif ctype == b"dSIG":
                report.add(Risk.PROVENANCE, "PNG:dSIG", tr("Digital signature"), tr("%d bytes") % length,
                           note=tr("The signature covers the file content: any cleaning is "
                                   "guaranteed to break it, and the very fact of a signature is "
                                   "visible to the recipient"),
                           removable=False)
            elif ctype not in _PNG_KNOWN and ctype[:1].islower():
                report.add(Risk.PROVENANCE, "PNG:%s" % _text(ctype),
                           tr("Unknown ancillary chunk"),
                           tr("%d bytes, starts with: %s") % (length, _hexdump(body)),
                           note=tr("Private chunk: written by a specific application, the "
                                   "content is arbitrary"))
        except Exception as exc:                  # noqa: BLE001
            report.err(tr("PNG: chunk %s was not parsed (%s: %s)") % (_text(ctype), type(exc).__name__, exc))
        pos = end
        if ctype == b"IEND":
            break
    if guard >= MAX_CHUNKS:
        report.err(tr("PNG: reached the limit of %d chunks") % MAX_CHUNKS)
    report.add(Risk.STRUCTURAL, "PNG", tr("Chunk map (%d)") % len(chunk_map),
               "; ".join(chunk_map[:60]),
               note=tr("Full list of chunks with sizes"))
    ctx["png_texts"] = texts
    _trailing(report, data, b"IEND", "PNG", "IEND", extra=4)   # +4 = CRC of the IEND chunk


# PNG text keys that usually have a person behind them.
_PNG_ID_KEYS = ("author", "artist", "copyright", "creator", "owner", "email",
                "contact", "disclaimer", "warning", "comment", "description", "title")


def _png_text(report, ctype, body, ctx):
    """tEXt / zTXt / iTXt. There can be many repeats -- we don't lose a single one."""
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
            report.err(tr("%s: chunk %s is truncated") % (loc, key))
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
               note=tr("PNG text chunk. Written by both people and generators: prompts, model "
                       "parameters and the path to the source file end up here too"))
    _remember(ctx, key, value)


def _png_raw_profile(report, key, value, loc, ctx):
    """ImageMagick hides whole profiles in a text chunk as a hex dump.

    It looks like harmless text "Raw profile type iptc", but inside is live
    IPTC, EXIF or ICC. Ordinary viewers don't show it -- and cleaners that
    look only at the standard chunks don't see it either.
    """
    name = key.split()[-1].lower()
    where = "%s:raw/%s" % (loc, name)
    # Format: "\n<profile name>\n<decimal length>\n<hex>". The anchor is the line
    # with the length: the profile name and the line breaks differ between
    # ImageMagick versions.
    lines = value.replace("\r", "\n").split("\n")
    declared, start = 0, 2
    for i, line in enumerate(lines[:4]):
        if line.strip().isdigit():
            declared, start = int(line.strip()), i + 1
            break
    digits = re.sub(r"[^0-9a-fA-F]", "", "".join(lines[start:]))
    if len(digits) // 2 > MAX_INFLATE:
        report.err(tr("%s: hex profile declares %d bytes, skipped") % (where, len(digits) // 2))
        return
    try:
        blob = binascii.unhexlify(digits[:len(digits) - len(digits) % 2])
    except (binascii.Error, ValueError) as exc:
        report.err(tr("%s: hex profile cannot be decoded (%s)") % (where, exc))
        return
    if declared and len(blob) != declared:
        report.err(tr("%s: declared %d bytes, decoded %d") % (where, declared, len(blob)))
        blob = blob[:declared] if declared < len(blob) else blob
    report.add(Risk.PROVENANCE, where, tr("Profile in hex form (%s)") % name,
               tr("%d bytes") % len(blob),
               note=tr("The profile is hidden in a text chunk as hex: this is what ImageMagick "
                       "does. Viewers don't show it, but the metadata inside is live"))
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
        report.add(Risk.STRUCTURAL, where, tr("ICC profile (hex)"),
                   tr("%d bytes%s") % (len(blob), (tr(", description: ") + desc) if desc else ""),
                   note=tr("Color profile hidden in a text chunk"))
    elif name == "xmp" or blob.lstrip()[:12].startswith((b"<?xpacket", b"<x:xmpmeta")):
        _parse_xmp(report, _text(blob), where, ctx)
    else:
        report.add(Risk.PROVENANCE, where, tr("Unrecognized hex profile"),
                   tr("%d bytes, starts with: %s") % (len(blob), _hexdump(blob)),
                   note=tr("Structure not recognized: to be parsed by hand in phase 2"))


# ===========================================================================
# WebP
# ===========================================================================

_VP8X_FLAGS = ((0x20, "ICC"), (0x10, N_("alpha")), (0x08, "EXIF"), (0x04, "XMP"), (0x02, N_("animation")))


def _webp(report, data, ctx):
    """RIFF container: fourcc + little-endian size, data padded to an even length."""
    if len(data) < 12:
        report.err(tr("WebP: file is shorter than a RIFF header"))
        return
    riff_size = _u32(data, 4, "<")
    if riff_size + 8 > len(data):
        report.err(tr("WebP: RIFF declares %d bytes, the file has %d") % (riff_size + 8, len(data)))
    pos, guard, chunk_map = 12, 0, []
    while pos + 8 <= len(data) and guard < MAX_CHUNKS:
        guard += 1
        fourcc = bytes(data[pos:pos + 4])
        size = _u32(data, pos + 4, "<")
        if size > MAX_FILE:
            report.err(tr("WebP: chunk %s declares %d bytes") % (_text(fourcc), size))
            break
        body = data[pos + 8:pos + 8 + size]
        if pos + 8 + size > len(data):
            report.err(tr("WebP: chunk %s is truncated") % _text(fourcc))
        chunk_map.append(tr("%s (%d B)") % (_text(fourcc), size))
        try:
            if fourcc == b"VP8X" and size >= 1:
                flags = body[0]
                names = [n for bit, n in _VP8X_FLAGS if flags & bit]
                report.add(Risk.STRUCTURAL, "WebP:VP8X", tr("Container flags"),
                           "0x%02X: %s" % (flags, ", ".join(tr(n) for n in names) or tr("none")),
                           note=tr("The flags declare which metadata blocks are in the file. A "
                                   "mismatch between the flags and the actual chunks is a sign "
                                   "of manual editing"))
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
                report.add(Risk.STRUCTURAL, "WebP:ICCP", tr("ICC profile"),
                           tr("%d bytes%s") % (size, (tr(", description: ") + desc) if desc else ""),
                           note=tr("Color profile: usually kept"))
            elif fourcc == b"ANIM":
                report.add(Risk.STRUCTURAL, "WebP:ANIM", tr("Animation"), tr("%d bytes") % size,
                           note=tr("Animated WebP: the frames may differ in content"))
        except Exception as exc:                  # noqa: BLE001
            report.err(tr("WebP: chunk %s was not parsed (%s: %s)")
                       % (_text(fourcc), type(exc).__name__, exc))
        pos += 8 + size + (size % 2)
    report.add(Risk.STRUCTURAL, "WebP", tr("Chunk map (%d)") % len(chunk_map),
               "; ".join(chunk_map[:60]), note=tr("Full list of RIFF chunks with sizes"))
    flags = ctx.get("vp8x") or []
    if "EXIF" in flags and not ctx.get("exif"):
        report.signal("inconsistent", tr("VP8X declares EXIF, but there is no EXIF chunk in the "
                                         "file: the metadata was cut out crudely and the flag "
                                         "stayed"), "medium")


# ===========================================================================
# GIF
# ===========================================================================

_GIF_XMP_MAGIC = b"\x01\xff\xfe\xfd\xfc"


def _gif(report, data, ctx):
    """GIF: Comment Extension (0xFE) and Application Extension (0xFF), incl. XMP Data."""
    if len(data) < 13:
        report.err(tr("GIF: file is shorter than the logical screen"))
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
        if marker == 0x2C:                                  # image
            pos += 1
            if pos + 9 > len(data):
                report.err(tr("GIF: image descriptor is truncated"))
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
            report.err(tr("GIF: unknown block 0x%02X at offset %d") % (marker, pos))
            break
        if pos + 2 > len(data):
            report.err(tr("GIF: extension is truncated"))
            break
        label = data[pos + 1]
        pos += 2
        if label == 0xFE:                                   # Comment Extension
            chunk, pos = _gif_blocks(data, pos)
            report.add(Risk.PROVENANCE, "GIF:Comment", tr("Comment"), _text(chunk),
                       note=tr("GIF comment: the converter's name goes here, and sometimes the "
                               "author's"))
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
                report.add(Risk.STRUCTURAL, "GIF:NETSCAPE2.0", tr("Animation looping"),
                           tr("%d bytes") % len(chunk), note=tr("Standard animation extension"))
            else:
                report.add(Risk.PROVENANCE, "GIF:%s" % name.strip(),
                           "Application Extension",
                           tr("%d bytes: %s") % (len(chunk), _text(chunk) or _hexdump(chunk)),
                           note=tr("A block left by a specific program: the application name "
                                   "alone already tells what made the file"))
                _remember(ctx, "GIF:App", name)
            continue
        chunk, pos = _gif_blocks(data, pos)                 # GCE, Plain Text
        blocks.append("Ext:0x%02X" % label)
    report.add(Risk.STRUCTURAL, "GIF", tr("Block map (%d)") % len(blocks),
               "; ".join(blocks[:60]), note=tr("Sequence of GIF blocks"))


def _gif_blocks(data, pos):
    """Read a chain of sub-blocks up to the zero terminator."""
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
    b"ftyp": N_("container brand declaration"),
    b"meta": N_("metadata container"),
    b"mdat": N_("the image data itself"),
    b"moov": N_("track tables (as in video)"),
    b"iinf": N_("item list: Exif and XMP are visible here"),
    b"iloc": N_("item offsets"),
    b"iprp": N_("item properties (incl. ICC)"),
    b"uuid": N_("arbitrary vendor block"),
}


def _heif(report, data, ctx):
    """ISOBMFF: list the top-level boxes and descend into meta/iinf.

    We deliberately don't do a full HEIF parse: the goal of phase 0 is to say
    honestly that the container holds metadata, and to show its structure.
    """
    report.err(tr("HEIF/HEIC: limited support -- we list the boxes and items, EXIF values inside "
                  "iloc are not extracted"))
    top = _boxes(report, data, 0, len(data), "HEIF")
    report.add(Risk.STRUCTURAL, "HEIF", tr("Top-level boxes (%d)") % len(top),
               "; ".join(tr("%s (%d B)%s") % (_text(t), sz,
                                          (" -- " + tr(_BOX_NOTE[t])) if t in _BOX_NOTE else "")
                         for t, off, sz in top[:40]),
               note=tr("ISOBMFF container structure"))
    for typ, off, size in top:
        if typ != b"meta":
            continue
        # meta is a fullbox: 4 bytes of version and flags precede the child boxes
        inner = _boxes(report, data, off + 12, off + size, "HEIF:meta")
        report.add(Risk.STRUCTURAL, "HEIF:meta", tr("meta boxes (%d)") % len(inner),
                   "; ".join(tr("%s (%d B)") % (_text(t), sz) for t, o, sz in inner[:40]),
                   note=tr("meta holds the item descriptions: thumbnails, EXIF, XMP"))
        for ityp, ioff, isize in inner:
            if ityp == b"iinf":
                _heif_iinf(report, data, ioff, isize, ctx)
            elif ityp == b"idat":
                report.add(Risk.PROVENANCE, "HEIF:idat", tr("Embedded item data"),
                           tr("%d bytes") % isize,
                           note=tr("Item data block: thumbnails live there too"))


def _boxes(report, data, start, stop, loc):
    """List of boxes (type, offset, size) in the given range."""
    out, pos, guard = [], start, 0
    stop = min(stop, len(data))
    while pos + 8 <= stop and guard < MAX_BOXES:
        guard += 1
        size = _u32(data, pos)
        typ = bytes(data[pos + 4:pos + 8])
        if size == 1:
            if pos + 16 > stop:
                report.err(tr("%s: largesize box %s is truncated") % (loc, _text(typ)))
                break
            size = struct.unpack_from(">Q", data, pos + 8)[0]
        elif size == 0:
            size = stop - pos
        if size < 8 or pos + size > stop:
            report.err(tr("%s: box %s declares %d bytes, %d available")
                       % (loc, _text(typ), size, stop - pos))
            out.append((typ, pos, stop - pos))
            break
        out.append((typ, pos, size))
        pos += size
    return out


def _heif_iinf(report, data, off, size, ctx):
    """iinf: a list of infe. We care about the presence of Exif and mime (XMP) items."""
    body = bytes(data[off + 8:off + size])
    kinds = []
    for match in re.finditer(b"infe", body):
        start = match.end()          # version+flags sit right after the box type
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
            kinds.append(tr("infe v%d (type only in content_type)") % version)
        if len(kinds) > MAX_BOXES:
            break
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    report.add(Risk.STRUCTURAL, "HEIF:iinf", tr("Container items (%d)") % len(kinds),
               "; ".join("%s x%d" % (k, n) for k, n in sorted(counts.items())[:30]),
               note=tr("Item types inside HEIF"))
    if any("Exif" in k for k in kinds):
        ctx["exif"] = True
        report.add(Risk.PROVENANCE, "HEIF:iinf", tr("Exif item"), tr("present"),
                   note=tr("The container holds a full EXIF -- so most likely GPS, serial "
                           "numbers and the camera model are there too. An exact parse needs an "
                           "iloc walk, which phase 0 does not have"))
    if any("mime" in k for k in kinds):
        report.add(Risk.PROVENANCE, "HEIF:iinf", tr("mime item (usually XMP)"), tr("present"),
                   note=tr("An item with an arbitrary MIME type: most often this is an XMP packet"))


# ===========================================================================
# exiftool: optional enrichment
# ===========================================================================

def _exiftool_exe():
    """exiftool on PATH or in ./tools/exiftool.exe. Not there -- then not there."""
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
    """Supplement the findings with what we did not parse ourselves (mainly MakerNotes).

    The file content never reaches a shell: the call uses an argument list,
    no shell. Any failure -- stay silent and carry on.
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
    except Exception:                             # noqa: BLE001 -- the enrichment is optional
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
                   note=tr("Found by the external exiftool: our parsing does not reach this field"))
    if added:
        report.signal("producer", tr("exiftool supplemented the parse with %d field(s), mostly "
                                     "decoded MakerNotes") % added, "high")


# ===========================================================================
# signals
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
    """Put the values into a shared bag: signals are built from it later."""
    text = str(value).strip()
    if not text:
        return
    ctx.setdefault("seen_values", set()).add(text.lower()[:200])
    ctx.setdefault("fields", []).append((str(label), text))


def _signals(report, ctx):
    """Conclusions drawn on top of the raw findings."""
    if ctx.get("gps"):
        report.signal("hazard", tr("the file contains GPS coordinates: %s -- the shooting "
                                   "location can be pinned down to a house") % ctx["gps"], "high")
    serials = [(l, v) for l, v in ctx.get("fields", [])
               if "serial" in l.lower() or l in ("BodySerialNumber", "LensSerialNumber")]
    if serials:
        report.signal("hazard", tr("equipment serial number: ") +
                      "; ".join("%s=%s" % (l, v) for l, v in serials[:4]), "high")
    if ctx.get("makernote"):
        report.signal("producer", tr("MakerNote of %d bytes: a proprietary camera block, usually "
                                     "holding the unit's serial number and the shutter count")
                      % ctx["makernote"], "high")
    if ctx.get("c2pa"):
        report.signal("producer", tr("the file contains a signed C2PA manifest: provenance is "
                                     "documented cryptographically, and stripping it is a "
                                     "detectable event"), "high")
    software = [v for l, v in ctx.get("fields", [])
                if l in ("Software", "xmp:CreatorTool", "tiff:Software", "softwareAgent",
                         "COM", "GIF:Comment", "GIF:App", "Originating Program",
                         "Program Version")]
    for value in software:
        low = value.lower()
        if any(t in low for t in _AI_TOOLS):
            report.signal("ai", tr("software field names an AI tool: %s") % value, "high")
        elif any(t in low for t in _EDITORS):
            report.signal("producer", tr("the file went through a graphics editor: %s") % value, "high")
        elif any(t in low for t in _LIBS):
            report.signal("producer", tr("the file was made by a library or converter: %s") % value, "high")
        else:
            report.signal("producer", tr("software field: %s") % value, "medium")
    dt = dict(ctx.get("fields", []))
    if dt.get("DateTime") and dt.get("DateTimeOriginal") and dt["DateTime"] != dt["DateTimeOriginal"]:
        report.signal("inconsistent", tr("DateTime (%s) does not match DateTimeOriginal (%s): "
                                         "the file was re-saved after capture")
                      % (dt["DateTime"], dt["DateTimeOriginal"]), "medium")
    if ctx.get("bad_crc"):
        report.signal("inconsistent", tr("chunks with a bad CRC: %d. Either the file is damaged "
                                         "or the metadata was edited byte by byte by hand")
                      % ctx["bad_crc"], "high")
    if ctx.get("thumbnail"):
        report.signal("hazard", tr("embedded thumbnail of %d bytes: it may have been made before "
                                   "retouching and show the original frame")
                      % ctx["thumbnail"], "medium")
    if report.fmt == "jpeg" and not ctx.get("exif") and not ctx.get("xmp") \
            and not ctx.get("photoshop") and not ctx.get("c2pa"):
        report.signal("scrubbed", tr("JPEG with no EXIF, XMP or IPTC at all: the file was either "
                                     "already cleaned or went through a messenger that "
                                     "recompresses images"),
                      "medium")


# ===========================================================================
# entry point
# ===========================================================================

def inspect_image(path, fmt=None) -> Report:
    """Inspect a raster image. Never raises an exception to the caller."""
    fmt = fmt or sniff.sniff(path)
    report = Report(path=str(path), fmt=fmt)
    ctx = {}
    try:
        try:
            report.size = os.path.getsize(path)
        except OSError as exc:
            report.err(tr("could not get the file size: %s") % exc)
        try:
            with open(path, "rb") as fh:          # strictly read-only, we don't touch the file
                data = fh.read(MAX_FILE + 1)
        except OSError as exc:
            report.err(tr("the file cannot be read: %s") % exc)
            return report
        if len(data) > MAX_FILE:
            report.err(tr("file is larger than %d MB: only the first %d MB were parsed")
                       % (MAX_FILE // 1048576, MAX_FILE // 1048576))
            data = data[:MAX_FILE]
        if not data:
            report.err(tr("file is empty"))
            return report

        handlers = {"jpeg": _jpeg, "png": _png, "tiff": _tiff,
                    "webp": _webp, "gif": _gif, "heic": _heif}
        handler = handlers.get(fmt)
        if handler is None:
            report.err(tr("format %r is not a raster image, the image inspector skips the file")
                       % fmt)
            return report
        try:
            handler(report, data, ctx)
        except Exception as exc:                  # noqa: BLE001 -- a broken file must not crash the bot
            report.err(tr("parsing of %s aborted: %s: %s") % (fmt, type(exc).__name__, exc))
        try:
            _exiftool(report, path, ctx)
        except Exception as exc:                  # noqa: BLE001
            report.err(tr("exiftool failed: %s") % exc)
        try:
            _signals(report, ctx)
        except Exception as exc:                  # noqa: BLE001
            report.err(tr("signals were not built: %s: %s") % (type(exc).__name__, exc))
    except Exception as exc:                      # noqa: BLE001 -- last line of defense
        report.err(tr("the inspector crashed entirely: %s: %s") % (type(exc).__name__, exc))
    return report


def _tiff(report, data, ctx):
    """TIFF: direct IFD walk, including the chain of next IFDs."""
    ctx["exif"] = True
    _walk_tiff(report, data, "TIFF", ctx)


# ===========================================================================
# self-test: build files from bytes and run the inspector over them
# ===========================================================================

def _demo():
    import tempfile

    def pack_ifd(en, tags, base, next_off=0):
        """Build an IFD: values longer than 4 bytes move out to the data area."""
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
    # IFD0: Artist, Model, GPS pointer
    ifd0_data_len = len(artist) + len(artist) % 2 + len(model) + len(model) % 2
    gps_base = 8 + (2 + 12 * 3 + 4) + ifd0_data_len
    # 55°45'4.48" N, 37°37'6.30" E -- central Moscow, 55.751244 / 37.618417
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
        print("%s  fmt=%s  size=%d  findings=%d  signals=%d  errors=%d"
              % (name, rep.fmt, rep.size, len(rep.findings), len(rep.signals), len(rep.errors)))
        for f in rep.findings:
            print("  [%s] %-28s %-26s %s" % (f.risk.value[:4], f.location, f.label, f.value))
        for s in rep.signals:
            print("  <%s/%s> %s" % (s.kind, s.confidence, s.detail))
        for e in rep.errors:
            print("  ! %s" % e)

    # --- checks: if the logic breaks, it fails here ----------------------
    jp = inspect_image(paths["ok.jpg"])
    flat = {(f.label, f.value) for f in jp.findings}
    assert any(l == "Artist" and "Игорь" in v for l, v in flat), "Artist not found"
    assert any("55.7512" in v and "37.6184" in v for _, v in flat), "GPS not converted to decimal"
    assert any(s.kind == "hazard" and "GPS" in s.detail for s in jp.signals), "no GPS signal"
    assert any(l == "JPEG comment" for l, _ in flat), "COM not parsed"
    assert any(l == "Quantization tables" for l, _ in flat), "DQT not found"
    assert any(f.label == "Quantization tables" and not f.removable for f in jp.findings)

    pg = inspect_image(paths["ok.png"])
    pflat = {(f.label, f.value) for f in pg.findings}
    assert any(l == "Author" and "Igor" in v for l, v in pflat), "tEXt Author not found"
    assert any(l == "Comment" and "Users" in v for l, v in pflat), "second tEXt lost"
    assert any(l == "Software" and "Photoshop" in v for l, v in pflat), "zTXt not inflated"
    assert any(s.kind == "producer" and "editor" in s.detail for s in pg.signals)

    tf = inspect_image(paths["ok.tif"])
    assert any(f.label == "Artist" for f in tf.findings), "TIFF: IFD not walked"
    wb = inspect_image(paths["ok.webp"])
    assert any(f.label == "Container flags" for f in wb.findings), "WebP: VP8X not parsed"
    assert any(s.kind == "ai" for s in wb.signals), "WebP: AI tool in XMP not recognized"
    gf = inspect_image(paths["ok.gif"])
    assert any(f.label == "Comment" for f in gf.findings), "GIF: comment not found"
    assert any(f.label == "dc:creator" for f in gf.findings), "GIF: XMP Data not parsed"
    hc = inspect_image(paths["ok.heic"])
    assert any(f.label == "Exif item" for f in hc.findings), "HEIF: iinf not parsed"
    assert hc.errors, "HEIF: a note about limited support is expected"

    for broken in ("cut.jpg", "junk.jpg", "empty.png", "loop.tif"):
        rep = inspect_image(paths[broken])
        assert isinstance(rep, Report), broken
        assert rep.errors, "%s: expected entries in errors" % broken

    print("=" * 72)
    print("self-test passed")


if __name__ == "__main__":
    _demo()
