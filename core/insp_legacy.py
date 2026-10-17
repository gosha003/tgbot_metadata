"""Inspectors for legacy and alternative formats: OLE2, OpenDocument, RTF.

Phase 0: read only. The file is opened strictly for reading, nothing is written.

Three public functions:
    inspect_ole(path, fmt="doc") -> Report    # .doc / .xls / .ppt (OLE2)
    inspect_odf(path, fmt="odt") -> Report    # .odt / .ods / .odp
    inspect_rtf(path)            -> Report    # .rtf

None of them raises an exception to the caller: any parsing problem goes
into report.err(). We receive damaged and deliberately malformed files too,
and a crashing inspector is a denial of service, not a diagnosis.
"""

from __future__ import annotations

import datetime
import os
import re
import struct
import zipfile

from core.model import Report, Risk, clip
from core.i18n import N_, tr

# --- bomb guards ------------------------------------------------------------
MAX_READ = 50 * 1024 * 1024        # never load a bigger chunk into memory
MAX_UNPACKED = 300 * 1024 * 1024   # total uncompressed size of a ZIP
MAX_RATIO = 200                    # uncompressed/compressed; above this it looks like a zip bomb
MAX_PARTS = 3000                   # how many container entries we parse at all
MAX_LIST = 60                      # how many findings of one kind we print as a list

RTF_HEAD = 4 * 1024 * 1024         # RTF header: \info, generator, rsid tables
RTF_CHUNK = 1 * 1024 * 1024        # chunk size for the streaming pass
RTF_OVERLAP = 8 * 1024             # overlap so that a match is not cut in half


# ============================================================================
#  Common helpers
# ============================================================================

def _size(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _dt(value) -> str:
    """A metadata date in readable form. Anything else is turned into a string."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _dur_secs(seconds) -> str:
    """Editing seconds -> '3 h 12 min (total 11520 s)'."""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if total <= 0:
        return tr("0 (file never edited or counter reset)")
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    human = " ".join(p for p in (
        tr("%d h") % h if h else "",
        tr("%d min") % m if m else "",
        tr("%d s") % s if s and not h else "",
    ) if p)
    return tr("%s (total %d s)") % (human or tr("less than a minute"), total)


def _dur_iso(text) -> str:
    """ISO 8601 duration ('PT1H23M45S', 'P2DT3H') -> readable string."""
    if not text:
        return ""
    m = re.match(
        r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$",
        str(text).strip(),
    )
    if not m:
        return str(text)
    years, months, days, hours, mins, secs = m.groups()
    chunks = [
        (tr("%s y") % years) if years else "",
        (tr("%s mo") % months) if months else "",
        (tr("%s d") % days) if days else "",
        (tr("%s h") % hours) if hours else "",
        (tr("%s min") % mins) if mins else "",
        (tr("%s s") % secs) if secs else "",
    ]
    human = " ".join(c for c in chunks if c)
    return "%s [%s]" % (human or "0", text)


# What we count as a trace of generation by a library/script rather than by a
# living person.
_LIB_MARKERS = (
    "odfpy", "python", "relatorio", "appy", "pandas", "jasper", "docutils",
    "pandoc", "reportlab", "php", "phpword", "node", "javascript", "gpt",
    "openai", "anthropic", "claude", "copilot", "aspose", "docxtemplater",
    "ruby", "perl", "golang", "rust", "automation", "headless", "script",
)


def _looks_generated(text) -> str:
    low = (text or "").lower()
    for marker in _LIB_MARKERS:
        if marker in low:
            return marker
    return ""


# Absolute paths and local URIs. The values are untrusted: we only print them.
#
# The UNC branch matches "2 or more backslashes". That is not a typo: in XML
# (ODF) the path arrives already decoded, so there are exactly two backslashes.
# In an RTF stream (_rtf_scan works on the RAW, unescaped text) every real
# backslash is written DOUBLED by the RTF spec ("\\" -> 2 bytes in the file),
# so a real "\\server\share" sits in the bytes as 4+2 backslashes. A strict
# "exactly 2" found the match shifted by one backslash and lost the start of
# the path.
_PATH_RE = re.compile(
    r"(?:file:///[^\s\"'<>{}|^`]+"
    r"|[A-Za-z]:[\\/][^\s\"'<>{}|*?]{2,}"
    r"|\\{2,}[A-Za-z0-9_.$-]+\\+[^\s\"'<>{}|*?]{1,}"
    r"|/(?:home|Users|mnt|media|srv|var/folders)/[^\s\"'<>{}|*?]{2,})"
)

# The same question, but asked of an ALREADY decoded value. _PATH_RE searches
# the raw RTF, where a real backslash is written doubled, and a match can
# contain control words -- `_rtf_plain` drops them, and only a stub is left of
# the match. On a real Word file this produced an ENVIRONMENT finding whose
# value was a single "\" character. In a privacy report a false finding costs
# more than a missed one, so the path must still be a path after decoding: a
# drive letter with a separator, a UNC with exactly two backslashes, a file://
# URI or the root of a Unix home directory.
_PATH_DECODED_RE = re.compile(
    r"^(?:file:///[^\s]{3,}"
    r"|[A-Za-z]:[\\/][^\s]{2,}"
    r"|\\\\[A-Za-z0-9_.$-]+\\[^\s]{1,}"
    r"|/(?:home|Users|mnt|media|srv|var)/[^\s]{2,})"
)


# ============================================================================
#  A. OLE2 -- .doc / .xls / .ppt
# ============================================================================

# SummaryInformation / DocumentSummaryInformation properties:
# olefile attribute -> (risk, human-readable name, explanation, value kind)
_OLE_PROPS = (
    ("author", Risk.IDENTITY, N_("Author"),
     N_("The Windows user name or the signature in the Office settings."), "s"),
    ("last_saved_by", Risk.IDENTITY, N_("Last saved by"),
     N_("The name of whoever last pressed 'Save' -- often NOT the author."), "s"),
    ("company", Risk.IDENTITY, N_("Organization"),
     N_("The company name from the Office license."), "s"),
    ("manager", Risk.IDENTITY, N_("Manager"),
     N_("The 'Manager' field in the document properties."), "s"),
    ("title", Risk.PROVENANCE, N_("Title"), "", "s"),
    ("subject", Risk.PROVENANCE, N_("Subject"), "", "s"),
    ("keywords", Risk.PROVENANCE, N_("Keywords"), "", "s"),
    ("comments", Risk.PROVENANCE, N_("Comments"), "", "s"),
    ("category", Risk.PROVENANCE, N_("Category"), "", "s"),
    ("content_status", Risk.PROVENANCE, N_("Document status"),
     N_("For example 'Draft' -- reveals the stage of work."), "s"),
    ("content_type", Risk.PROVENANCE, N_("Content type"), "", "s"),
    ("create_time", Risk.PROVENANCE, N_("Created"),
     N_("The moment the document was created (UTC)."), "d"),
    ("last_saved_time", Risk.PROVENANCE, N_("Last saved"),
     N_("The moment of the last save (UTC)."), "d"),
    ("last_printed", Risk.PROVENANCE, N_("Last printed"),
     N_("The mere fact of printing sometimes shows that the document left the building."), "d"),
    ("total_edit_time", Risk.PROVENANCE, N_("Total editing time"),
     N_("The total time spent working on the document. Shows whether the text was written over "
        "hours or pasted in ready-made within a minute."), "t"),
    ("creating_application", Risk.PROVENANCE, N_("Creating application"),
     N_("The exact application string and its version."), "s"),
    ("revision_number", Risk.PROVENANCE, N_("Revision number"),
     N_("How many times the document was saved."), "s"),
    ("doc_version", Risk.PROVENANCE, N_("Document version"), "", "s"),
    ("version", Risk.PROVENANCE, N_("Format version"), "", "s"),
    ("num_pages", Risk.PROVENANCE, N_("Pages"), "", "s"),
    ("num_words", Risk.PROVENANCE, N_("Words"), "", "s"),
    ("num_chars", Risk.PROVENANCE, N_("Characters"), "", "s"),
    ("chars_with_spaces", Risk.PROVENANCE, N_("Characters with spaces"), "", "s"),
    ("lines", Risk.PROVENANCE, N_("Lines"), "", "s"),
    ("paragraphs", Risk.PROVENANCE, N_("Paragraphs"), "", "s"),
    ("slides", Risk.PROVENANCE, N_("Slides"), "", "s"),
    ("notes", Risk.PROVENANCE, N_("Slides with notes"),
     N_("Speaker notes are a separate source of text leaks."), "s"),
    ("hidden_slides", Risk.PROVENANCE, N_("Hidden slides"),
     N_("Hidden slides stay in the file in full."), "s"),
    ("titles_of_parts", Risk.PROVENANCE, N_("Part titles"),
     N_("Names of sheets/slides/sections -- they often reveal the internal structure."), "s"),
    ("template", Risk.ENVIRONMENT, N_("Template"),
     N_("Usually a path to a .dot(x): either local or \\\\server\\share."), "s"),
    ("link_base", Risk.ENVIRONMENT, N_("Link base"),
     N_("The base path for relative hyperlinks -- almost always local."), "s"),
    ("presentation_target", Risk.ENVIRONMENT, N_("Presentation target"),
     N_("Sometimes holds a printer name or a screen model."), "s"),
    ("security", Risk.PROVENANCE, N_("Security flag"),
     N_("A bit mask: password, a 'read-only recommended' flag and the like."), "s"),
    ("language", Risk.STRUCTURAL, N_("Language"),
     N_("Usually worth keeping: it affects spell checking."), "s"),
    ("codepage", Risk.STRUCTURAL, N_("Code page"),
     N_("Needed to read the metadata strings correctly."), "s"),
    ("dig_sig", Risk.IDENTITY, N_("Digital signature"),
     N_("The signature contains the signer's certificate (full name, organization, email). It "
        "cannot be removed without losing the signature."), "b"),
)

# Streams that need a separate mention.
_OLE_MACRO_STREAMS = ("Macros", "_VBA_PROJECT_CUR", "VBA", "_VBA_PROJECT",
                      "VBA_PROJECT", "vbaProject")

_CLSID_APPS = {
    "00020906-0000-0000-C000-000000000046": "Microsoft Word 97-2003",
    "00020900-0000-0000-C000-000000000046": "Microsoft Word 6.0/7.0",
    "00020820-0000-0000-C000-000000000046": "Microsoft Excel 97-2003",
    "00020810-0000-0000-C000-000000000046": "Microsoft Excel 5.0/95",
    "64818D10-4F9B-11CF-86EA-00AA00B929E8": "Microsoft PowerPoint 97-2003",
    "74B78F3A-C8C8-11D1-BE11-00C04FB6FAF1": "Microsoft Project",
    "000C1084-0000-0000-C000-000000000046": N_("Windows Installer (MSI database)"),
    "000C1086-0000-0000-C000-000000000046": N_("Windows Installer (MSP patch)"),
}


def _cp_codec(codepage) -> str:
    """Code page number from SummaryInformation -> Python codec name."""
    try:
        cp = int(codepage)
    except (TypeError, ValueError):
        return "cp1252"
    if cp < 0:
        cp += 65536          # the number is stored as a signed int16
    if cp in (65001, 0):
        return "utf-8"
    if cp == 1200:
        return "utf-16-le"
    name = "cp%d" % cp
    try:
        import codecs
        codecs.lookup(name)
    except (LookupError, ValueError):
        return "cp1252"
    return name


def _dec(value, codec="cp1252"):
    """olefile attributes are often bytes -- decode them using the code page."""
    if isinstance(value, bytes):
        try:
            return value.decode(codec, "replace")
        except LookupError:
            return value.decode("cp1252", "replace")
    return value


def _ole_read(ole, name, limit=MAX_READ) -> bytes:
    """Read a whole stream, but no more than limit bytes."""
    with ole.openstream(name) as fh:
        return fh.read(limit)


def _filetime_set(value):
    """An olefile datetime -> the same datetime if the time is really set. A zero
    FILETIME (1601-01-01) means 'not set', not a date: Word writes it into
    'Last printed' for everyone who has never printed."""
    if isinstance(value, datetime.datetime) and value > datetime.datetime(1601, 1, 1):
        return value
    return None


# FMTID_UserDefinedProperties {D5CDD505-2E9C-101B-9397-08002B2CF9AE} in
# on-disk byte order (the first three fields are little-endian).
_USERDEF_FMTID = bytes.fromhex("05d5cdd59c2e1b10939708002b2cf9ae")
MAX_USERDEF = 200                  # we parse no more custom properties than this
USERDEF_READ = 8 * 1024 * 1024     # DocumentSummaryInformation stream: we don't read more than this


def _le(blob, off, size):
    """A little-endian integer from blob. Going past the end of the stream raises
    ValueError instead of silently returning zero: a corrupt file must end up in
    report.errors."""
    if off < 0 or off + size > len(blob):
        raise ValueError(tr("offset %d is outside the stream (%d bytes)") % (off, len(blob)))
    return int.from_bytes(blob[off:off + size], "little")


def _sint(value, bits):
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _userdef_value(blob, off, codec, wide):
    """TypedPropertyValue (MS-OLEPS) -> string. An unknown type becomes a note."""
    vt = _le(blob, off, 2)
    pos = off + 4
    if vt in (0x1E, 0x08):                    # LPSTR / BSTR: length in bytes
        n = _le(blob, pos, 4)
        return blob[pos + 4:pos + 4 + n].decode(
            "utf-16-le" if wide else codec, "replace").strip("\x00")
    if vt == 0x1F:                            # LPWSTR: length in characters
        n = _le(blob, pos, 4)
        return blob[pos + 4:pos + 4 + 2 * n].decode("utf-16-le", "replace").strip("\x00")
    if vt in (0x02, 0x12):                    # I2 / UI2
        v = _le(blob, pos, 2)
        return str(_sint(v, 16) if vt == 0x02 else v)
    if vt in (0x03, 0x16, 0x13, 0x17):        # I4, INT, UI4, UINT
        v = _le(blob, pos, 4)
        return str(_sint(v, 32) if vt in (0x03, 0x16) else v)
    if vt in (0x14, 0x15):                    # I8 / UI8
        v = _le(blob, pos, 8)
        return str(_sint(v, 64) if vt == 0x14 else v)
    if vt == 0x0B:                            # BOOL
        return tr("yes") if _le(blob, pos, 2) else tr("no")
    if vt == 0x05:                            # R8
        return repr(struct.unpack("<d", blob[pos:pos + 8])[0])
    if vt == 0x40:                            # FILETIME
        ticks = _le(blob, pos, 8)
        return _dt(datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ticks // 10))
    return tr("(VT type 0x%02X, cannot be parsed)") % vt


def _userdef_props(blob, default_codec, report):
    """Custom properties from the DocumentSummaryInformation stream (the
    FMTID_UserDefinedProperties section, MS-OLEPS) -> [(name, value)].

    Our own parser rather than olefile.get_userdefined_properties(): that one
    reads names only as UTF-8, so it silently loses Russian names in cp1251 (an
    empty list without a single error), and it parses a section in code page 1200
    (UTF-16: length in characters, 4-byte alignment) into garbage. The section
    has its own code page -- property PID 1. Whatever was parsed before the
    truncation is kept.
    """
    out = []
    try:
        for i in range(min(_le(blob, 24, 4), 16)):
            entry = 28 + 20 * i
            if blob[entry:entry + 16] != _USERDEF_FMTID:
                continue
            sec = _le(blob, entry + 16, 4)
            props = {}
            for j in range(min(_le(blob, sec + 4, 4), 4 * MAX_USERDEF)):
                props[_le(blob, sec + 8 + 8 * j, 4)] = sec + _le(blob, sec + 12 + 8 * j, 4)
            codec, wide = default_codec, False
            if 1 in props and _le(blob, props[1], 2) == 0x02:        # PID 1: VT_I2
                codec = _cp_codec(_le(blob, props[1] + 4, 2))
                wide = codec == "utf-16-le"
            if 0 not in props:                                       # PID 0: the dictionary
                continue
            names, pos = {}, props[0]
            count = _le(blob, pos, 4)
            if count > MAX_USERDEF:
                report.err(tr("%d custom properties, parsing the first %d")
                           % (count, MAX_USERDEF))
            pos += 4
            try:
                for _ in range(min(count, MAX_USERDEF)):
                    pid, length = _le(blob, pos, 4), _le(blob, pos + 4, 4)
                    pos += 8
                    nbytes = length * 2 if wide else length
                    names[pid] = blob[pos:pos + nbytes].decode(codec, "replace").strip("\x00")
                    pad = -nbytes % 4 if wide else 0      # UTF-16: the name is aligned to 4
                    if pad and _le(blob, pos + nbytes + pad, 4) not in props \
                            and _le(blob, pos + nbytes, 4) in props:
                        pad = 0     # the writer did not align: the next PID follows the name immediately
                    pos += nbytes + pad
            except ValueError as exc:       # the dictionary is truncated: take what there is
                report.err(tr("the custom property dictionary is truncated: %s") % (exc,))
            for pid, name in names.items():
                if pid not in props:
                    continue
                try:
                    out.append((name, _userdef_value(blob, props[pid], codec, wide)))
                except Exception:
                    out.append((name, tr("(value could not be parsed)")))
    except Exception as exc:
        report.err(tr("custom properties cut off at a corrupt structure: %s") % (exc,))
    return out


MAX_VECTOR = 100_000               # elements in a vector property: more than this is garbage


def _open_ole(olefile, path):
    """An OleFileIO that a corrupt property set cannot hang.

    olefile parses a vector property (VT_VECTOR) with a loop over a counter taken
    from the file, and elements of a type it doesn't know read not a single byte
    and raise no exceptions. A counter of 0xFFFFFFFF is four billion empty
    iterations and a list of tens of gigabytes: the inspector hangs, and the bot
    with it. One corrupted byte is enough (a disk fault, a truncated upload); no
    malice is needed. So we cap the counter at MAX_VECTOR: anything above that is
    garbage, the property is skipped and the others are read as usual.
    """
    class _SafeOle(olefile.OleFileIO):
        def _parse_property(self, s, offset, property_id, property_type, *rest):
            if property_type & 0x1000:                    # VT_VECTOR
                if int.from_bytes(s[offset:offset + 4], "little") > MAX_VECTOR:
                    return None
            return super()._parse_property(s, offset, property_id, property_type, *rest)

    return _SafeOle(path)


def inspect_ole(path, fmt="doc") -> Report:
    """Inspect an OLE2 container: .doc, .xls, .ppt and the like.

    The main value here is not the document properties (Word shows those too)
    but the container structure: revision table streams, residual text after
    fast saves, embedded objects and macros.
    """
    report = Report(path=str(path), fmt=fmt, size=_size(path))

    try:
        import olefile
    except ImportError:
        report.err(tr("olefile is not installed -- OLE2 parsing is impossible"))
        return report

    try:
        if not olefile.isOleFile(path):
            report.err(tr("the file is not an OLE2 container (no D0CF11E0 signature)"))
            return report
    except Exception as exc:
        report.err(tr("could not check the OLE2 signature: %r") % (exc,))
        return report

    ole = None
    try:
        ole = _open_ole(olefile, path)   # olefile opens strictly for reading
    except Exception as exc:
        report.err(tr("the OLE2 container failed to open: %r") % (exc,))
        return report

    try:
        codec = _section(report, tr("properties"), _ole_meta, ole, report, default="cp1252")
        flat = _section(report, tr("directory"), _ole_parts, ole, report, default={})
        _section(report, tr("root entry"), _ole_root, ole, report)
        _section(report, tr("streams"), _ole_streams, ole, report, flat, codec)
        # We pick the branch by the TOP-level streams, not by fmt: sniff looks for
        # names in the whole tree and takes an .xls with an embedded Word object for
        # a .doc -- then the workbook would stay unparsed and the Word findings would
        # be attributed to a foreign file.
        have = {
            "doc": bool(_top(flat, "WordDocument")),
            "xls": bool(_top(flat, "Workbook") or _top(flat, "Book")),
            "ppt": bool(_top(flat, "PowerPoint Document") or _top(flat, "Current User")),
        }
        if have["doc"]:
            _section(report, "WordDocument", _ole_word, ole, report, flat)
        if have["xls"]:
            _section(report, "Workbook", _ole_xls, ole, report, flat)
        if have["ppt"]:
            _section(report, "PowerPoint", _ole_ppt, ole, report, flat, codec)
        real = [k for k, ok in have.items() if ok]
        if real and fmt in have and fmt not in real:
            report.signal("inconsistent",
                          tr("The format was detected as %s, but the top level of the container "
                             "holds %s streams: an embedded object probably contains streams of "
                             "another application. Parsing follows the top level.")
                          % (fmt, ", ".join(real)), "medium")
    except Exception as exc:                       # last line of defense
        report.err(tr("OLE2 parsing aborted: %r") % (exc,))
    finally:
        try:
            ole.close()
        except Exception:
            pass
    return report


def _section(report, title, fn, *args, default=None):
    """One parsing section in its own try/except: a failure in it doesn't take the
    others down."""
    try:
        return fn(*args)
    except Exception as exc:
        report.err(tr("%s: parsing aborted: %r") % (title, exc))
        return default


def _top(flat, name):
    """The stream/storage entry called name directly in the container root. flat is
    indexed by the last path element, and an embedded object has the same names
    (WordDocument, CompObj, SummaryInformation) deeper down: taking them for our
    own would attribute a foreign document to the file and read it at a path
    that doesn't exist in the root."""
    return next((e for e in flat.get(name, []) if e[0] == name), None)


def _ole_meta(ole, report) -> str:
    """SummaryInformation + DocumentSummaryInformation. Returns the codec."""
    codec = "cp1252"
    try:
        meta = ole.get_metadata()
    except Exception as exc:
        report.err(tr("could not read SummaryInformation: %r") % (exc,))
        return codec

    codec = _cp_codec(getattr(meta, "codepage", None))
    # Each of the two property streams has its own code page. We read the
    # DocumentSummaryInformation strings (organization, manager...) in ITS code
    # page, not in the SummaryInformation one: otherwise Cyrillic, where the pages
    # differ, turns into 'ÎÎÎ «Ðîìàøêà»'.
    doc_cp = getattr(meta, "codepage_doc", None)
    codec_doc = _cp_codec(doc_cp) if doc_cp is not None else codec
    docsum_attrs = set(getattr(type(meta), "DOCSUM_ATTRIBS", ()))
    app = ""
    for attr, risk, label, note, kind in _OLE_PROPS:
        try:
            raw = getattr(meta, attr, None)
            if raw is None:
                continue
            in_doc = attr in docsum_attrs
            cp = codec_doc if in_doc else codec
            if kind == "d":
                null = isinstance(raw, datetime.datetime) and _filetime_set(raw) is None
                value = "" if null else _dt(raw)    # zero FILETIME -> empty
            elif kind == "t":
                value = _dur_secs(raw)
            elif kind == "b":
                value = tr("present, %d bytes") % len(raw) if raw else ""
            else:
                value = _dec(raw, cp)
            if isinstance(value, (list, tuple)):
                value = "; ".join(str(_dec(v, cp)) for v in value)
            removable = attr != "dig_sig"
            report.add(risk, "DocumentSummaryInformation" if in_doc
                       else "SummaryInformation", tr(label), value, tr(note), removable)
            if attr == "creating_application":
                app = clip(value)
        except Exception as exc:
            report.err(tr("property %s could not be parsed: %r") % (attr, exc))

    # Custom document properties (the "Custom" tab in the Office properties).
    try:
        if ole.exists("\x05DocumentSummaryInformation"):
            blob = _ole_read(ole, "\x05DocumentSummaryInformation", USERDEF_READ)
            for name, value in _userdef_props(blob, codec_doc, report):
                report.add(Risk.IDENTITY, "DocumentSummaryInformation",
                           tr("Custom property: %s") % clip(name, 80), value,
                           tr("A free-form field filled in by hand or by an organization "
                              "template: it can hold full names, emails in MSIP classification "
                              "labels, contract numbers, internal department codes."))
    except Exception as exc:
        report.err(tr("custom properties could not be read: %r") % (exc,))

    if app:
        marker = _looks_generated(app)
        report.signal("producer", tr("Creating application: %s") % app, "high")
        if marker:
            report.signal("ai", tr("The creating application looks like a library/script (marker "
                                   "'%s'): %s") % (marker, app), "medium")
    _ole_consistency(meta, report)
    return codec


def _ole_consistency(meta, report):
    """Contradictions between dates and counters."""
    try:
        created = _filetime_set(getattr(meta, "create_time", None))
        saved = _filetime_set(getattr(meta, "last_saved_time", None))
        if isinstance(created, datetime.datetime) and isinstance(saved, datetime.datetime):
            if saved < created:
                report.signal("inconsistent",
                              tr("Save date (%s) is earlier than creation date (%s): the "
                                 "metadata was edited by hand or the system clock was changed.") % (_dt(saved), _dt(created)),
                              "high")
        edit = getattr(meta, "total_edit_time", None)
        author = getattr(meta, "author", None)
        saved_by = getattr(meta, "last_saved_by", None)
        if not author and not saved_by:
            report.signal("scrubbed",
                          tr("Neither 'Author' nor 'Last saved by' is filled in: the file was "
                             "either cleaned already or produced by a program."), "medium")
        if isinstance(edit, int) and edit > 0 and isinstance(created, datetime.datetime) \
                and isinstance(saved, datetime.datetime):
            window = (saved - created).total_seconds()
            if window >= 0 and edit > window + 3600:
                report.signal("inconsistent",
                              tr("Editing time (%d s) exceeds the span between creation and save "
                                 "(%d s): the document is older than its dates claim.") % (edit, int(window)), "medium")
    except Exception as exc:
        report.err(tr("OLE2 date cross-check failed: %r") % (exc,))


def _ole_parts(ole, report) -> dict:
    """All streams and storages of the container -> report.parts. Returns the map."""
    flat = {}
    try:
        entries = ole.listdir(streams=True, storages=True)
    except Exception as exc:
        report.err(tr("could not read the OLE2 directory: %r") % (exc,))
        return flat

    if len(entries) > MAX_PARTS:
        report.err(tr("the container has %d entries, parsing the first %d")
                   % (len(entries), MAX_PARTS))
        entries = entries[:MAX_PARTS]

    for entry in entries:
        name = "/".join(entry)
        size = 0
        kind = "storage"
        try:
            if ole.get_type(entry) == 2:      # STGTY_STREAM
                kind = "stream"
                size = ole.get_size(entry)
        except Exception:
            pass
        report.parts.append({"name": name, "size": size, "kind": kind})
        flat.setdefault(entry[-1], []).append((name, size, kind))
    return flat


def _ole_root(ole, report):
    """CLSID and times of the root storage."""
    try:
        root = ole.root
        clsid = (getattr(root, "clsid", "") or "").upper()
        if clsid and clsid.strip("0-"):
            app = tr(_CLSID_APPS.get(clsid, "unknown application"))
            report.add(Risk.PROVENANCE, "Root Entry", tr("Root storage CLSID"),
                       "%s (%s)" % (clsid, app),
                       tr("The OLE class directly names the application that created the "
                          "container."))
            if clsid in _CLSID_APPS:
                report.signal("producer", tr("Container CLSID: %s") % app, "high")
    except Exception:
        pass
    # ole.getctime("Root Entry") raises OSError("file not found"): the root isn't
    # in the name tree. The root's times are taken from the root entry itself.
    for label, getter, note in (
        (tr("Container creation time"), "getctime",
         tr("The time of the OLE storage itself, separate from the date in the document "
            "properties: the two often diverge and reveal that the metadata was edited.")),
        (tr("Container modification time"), "getmtime", ""),
    ):
        try:
            value = getattr(ole.root, getter)()
            report.add_nonempty(Risk.PROVENANCE, "Root Entry", label, _dt(value), note)
        except Exception as exc:
            report.err(tr("%s could not be read: %r") % (label.lower(), exc))


def _ole_streams(ole, report, flat, codec):
    """Named streams that need a separate mention."""
    # --- macros ---
    found_macro = [n for n in _OLE_MACRO_STREAMS if n in flat]
    for name in found_macro:
        for full, size, _kind in flat[name]:
            report.add(Risk.PROVENANCE, full, tr("Macro stream"),
                       tr("%s, %d bytes") % (name, size),
                       tr("A VBA project. It holds the module source code, the project name and "
                          "its path on the developer's machine, and often old commented-out code "
                          "too."), False)
    if found_macro:
        report.signal("hazard",
                      tr("The file contains a VBA project (%s): active content, plus a source of "
                         "metadata separate from the document.")
                      % ", ".join(sorted(set(found_macro))), "high")

    # --- properties ---
    for name, label in (("\x05SummaryInformation", tr("SummaryInformation stream")),
                        ("\x05DocumentSummaryInformation",
                         tr("DocumentSummaryInformation stream"))):
        if _top(flat, name):
            size = _top(flat, name)[1]
            report.add(Risk.PROVENANCE, name.replace("\x05", "\\x05"), label,
                       tr("%d bytes") % size,
                       tr("The standard container of document properties."))

    # --- digital signature as a separate stream ---
    for name in ("\x05DigitalSignature", "\x05MsiDigitalSignatureEx",
                 "_signatures"):
        if name in flat:
            size = flat[name][0][1]
            report.add(Risk.IDENTITY, name.replace("\x05", "\\x05"),
                       tr("Digital signature"), tr("%d bytes") % size,
                       tr("The signature contains the signer's certificate: a full name or "
                          "organization name, email address, validity period, thumbprint. It can "
                          "be removed only together with the signature itself: the file stops "
                          "being signed."), False)

    # --- CompObj: name of the creating application ---
    if _top(flat, "\x01CompObj"):
        try:
            blob = _ole_read(ole, "\x01CompObj", 64 * 1024)
            for label, value in _parse_compobj(blob, codec):
                report.add_nonempty(Risk.PROVENANCE, "\\x01CompObj", label, value,
                                    tr("CompObj records which application the object was created "
                                       "with -- even if the document properties were erased."))
        except Exception as exc:
            report.err(tr("CompObj could not be parsed: %r") % (exc,))

    # --- nested OLE objects ---
    pool = [n for n in report.parts if n["name"].startswith("ObjectPool")]
    if pool:
        children = sorted({p["name"].split("/")[1] for p in pool
                           if p["name"].count("/") >= 1 and "/" in p["name"]})
    else:
        children = []
    if children:
        report.add(Risk.IDENTITY, "ObjectPool", tr("Nested OLE objects"),
                   tr("%d object(s): %s") % (len(children),
                                          ", ".join(children[:MAX_LIST])),
                   tr("Every embedded object (an Excel sheet, a chart, a pasted document) is a "
                      "separate OLE container WITH ITS OWN metadata: its own author, its own "
                      "dates and its own residual data. Cleaning the outer file doesn't touch "
                      "them."),
                   False)
        report.signal("hazard",
                      tr("ObjectPool holds nested OLE objects (%d of them); their metadata has "
                         "to be inspected separately.") % len(children),
                      "high")


def _parse_compobj(blob, codec):
    """CompObj: after the 28-byte header come length-prefixed strings."""
    out = []
    labels = (tr("Object type name (UserType)"), tr("Clipboard format"), "ProgID")
    pos = 28
    for label in labels:
        if pos + 4 > len(blob):
            break
        length = int.from_bytes(blob[pos:pos + 4], "little")
        pos += 4
        if length in (0xFFFFFFFF, 0xFFFFFFFE):     # the format is given as a number, not a string
            pos += 4
            continue
        if length == 0 or length > 4096 or pos + length > len(blob):
            break
        raw = blob[pos:pos + length].split(b"\x00", 1)[0]
        pos += length
        out.append((label, _dec(raw, codec)))
    return out


# Index of the fc/lcb pair of the revision author table in FibRgFcLcb97
# (MS-DOC 2.5.7). In Word 2000 and later the structure is only ever appended
# to and the first pairs don't move, so one index serves every version from
# Word 97 on.
_FIB_STTBF_RMARK = 51
_FIB_READ = 8 * 1024               # the whole FIB; the document text starts further on
_STTB_CAP = 64 * 1024              # a name table bigger than this is not a name table


def _fib_pair(fib, index):
    """A (fc, lcb) pair from FibRgFcLcb by index.

    fib: the bytes at the start of the WordDocument stream.
    index: the pair number as in MS-DOC 2.5.7 (fcSttbfRMark is 51).
    Returns: (fc, lcb), or None if the FIB is shorter or declares fewer pairs.
    Never raises.

    The base is computed from the actual csw and cslw, not from a constant. In
    real Word it equals 154 (csw=14, cslw=22), but a malformed file is free to
    lie, and then a constant would read garbage at a foreign offset.
    """
    if len(fib) < 34 or index < 0:
        return None
    csw = int.from_bytes(fib[32:34], "little")
    if csw > 64:
        return None
    at = 34 + csw * 2
    if at + 2 > len(fib):
        return None
    cslw = int.from_bytes(fib[at:at + 2], "little")
    if cslw > 128:
        return None
    at += 2 + cslw * 4
    if at + 2 > len(fib):
        return None
    count = int.from_bytes(fib[at:at + 2], "little")
    if index >= count or count > 4096:
        return None
    at += 2 + index * 8
    if at + 8 > len(fib):
        return None
    fc, lcb = struct.unpack_from("<II", fib, at)
    if lcb == 0 or lcb > _STTB_CAP or fc > 64 * 1024 * 1024:
        return None
    return fc, lcb


def _sttb_strings(blob, limit=MAX_LIST):
    """Strings from an STTB (MS-DOC 2.9.272): both the Unicode form
    (fExtend=0xFFFF) that Word 97+ writes and the older single-byte one.

    blob: a piece of the table stream of exactly lcb bytes.
    limit: how many strings to take; the rest is no longer a name table but
        garbage or a bomb.
    Returns: a list of non-empty strings without the service entry "Unknown".
        Word always writes it first, as in the RTF \\*\\revtbl, and it carries
        no identity.
    Never raises: a damaged block yields whatever was parsed.
    """
    if len(blob) < 4:
        return []
    word = int.from_bytes(blob[0:2], "little")
    if word == 0xFFFF:
        if len(blob) < 6:
            return []
        count = int.from_bytes(blob[2:4], "little")
        extra = int.from_bytes(blob[4:6], "little")
        pos, wide = 6, True
    else:
        count = word
        extra = int.from_bytes(blob[2:4], "little")
        pos, wide = 4, False
    if count > 10000 or extra > 256:
        return []
    out = []
    for _ in range(count):
        if len(out) >= limit:
            break
        width = 2 if wide else 1
        if pos + width > len(blob):
            break
        chars = int.from_bytes(blob[pos:pos + width], "little")
        pos += width
        if chars > 1024:
            break
        nbytes = chars * (2 if wide else 1)
        raw = blob[pos:pos + nbytes]
        if len(raw) < nbytes:
            break
        pos += nbytes + extra
        text = (raw.decode("utf-16le", "replace") if wide
                else raw.decode("cp1251", "replace"))
        text = text.strip("\x00 ").strip()
        if text and text.lower() != "unknown":
            out.append(text)
    return out


def _ole_slice(ole, name, start, length) -> bytes:
    """A piece of the stream, not the whole stream: in a real file the 1Table
    stream is orders of magnitude bigger than the name table, and there is no
    need to read all of it."""
    with ole.openstream(name) as fh:
        fh.seek(start)
        return fh.read(length)


def _ole_rmark(ole, report, fib, which):
    """Names from SttbfRMark -- who edited the document, even if the changes were accepted.

    which: the name of the active table stream ("1Table" or "0Table").
    Writes IDENTITY findings, one per name. Never raises: a parsing failure
    becomes an entry in report.err(), and the general finding about residual
    text stays.
    """
    pair = _fib_pair(fib, _FIB_STTBF_RMARK)
    if pair is None:
        return []
    fc, lcb = pair
    try:
        blob = _ole_slice(ole, which, fc, lcb)
    except Exception as exc:  # noqa: BLE001
        report.err(tr("SttbfRMark could not be read: %r") % (exc,))
        return []
    if len(blob) < lcb:
        report.err(tr("SttbfRMark is shorter than declared in the FIB: %d of %d") % (len(blob), lcb))
    names = _sttb_strings(blob)
    for name in names:
        report.add(Risk.IDENTITY, which, tr("Revision author (SttbfRMark)"), name,
                   tr("A name from the revision author table. It stays in the file even after "
                      "the changes themselves are accepted: editing the document properties "
                      "doesn't touch this table."), False)
    if len(names) > 1:
        report.signal(
            "inconsistent",
            tr("The revision author table lists %d people: more than one person worked on the "
               "file, although the document properties name a single author.") % len(names),
            "medium")
    return names


def _ole_word(ole, report, flat):
    """The core of the module: residual text and revision tables in a .doc."""
    if not _top(flat, "WordDocument"):
        return

    tables = [n for n in ("0Table", "1Table") if _top(flat, n)]
    fib = b""
    try:
        fib = _ole_read(ole, "WordDocument", _FIB_READ)
    except Exception as exc:
        report.err(tr("FIB of the WordDocument stream could not be read: %r") % (exc,))

    flags = quick_saves = which_table = None
    if len(fib) >= 12:
        try:
            n_fib = int.from_bytes(fib[2:4], "little")
            lid = int.from_bytes(fib[6:8], "little")
            flags = int.from_bytes(fib[10:12], "little")
            quick_saves = (flags >> 4) & 0x0F
            which_table = "1Table" if flags & 0x0200 else "0Table"
            report.add(Risk.PROVENANCE, "WordDocument", tr("Format version (nFib)"),
                       "0x%04X" % n_fib,
                       tr("The FIB structure version number: it shows exactly which version of "
                          "Word wrote the file last."))
            report.add(Risk.ENVIRONMENT, "WordDocument", tr("Application language (lid)"),
                       "0x%04X" % lid,
                       tr("The Word locale identifier on the machine where the file was saved: "
                          "indirectly reveals the country and the language build of Office."))
            if flags & 0x0100:
                report.add(Risk.STRUCTURAL, "WordDocument", tr("Document is encrypted"),
                           tr("fEncrypted flag"), tr("The content is encrypted with a password; it "
                                                 "cannot be parsed without the password."), False)
                encrypted = True
            else:
                encrypted = False
        except Exception as exc:
            report.err(tr("FIB flags could not be parsed: %r") % (exc,))
            encrypted = False
    else:
        encrypted = False

    # The main finding of the module.
    note = (
        tr("The .doc format physically stores more than it shows. The table stream (%s) holds "
           "the SttbfRMark revision author table: a list of EVERYONE who ever edited the "
           "document, even if the changes were accepted long ago. When saving in fast-save mode "
           "Word doesn't rewrite the file from scratch but appends the changes at the end: the "
           "DELETED TEXT of earlier versions stays in the file physically and can be extracted "
           "with any hex editor. On top of that, unused sectors of the OLE container could "
           "previously have belonged to OTHER files on the same disk, and their fragments travel "
           "with the document. This cannot be cleaned by editing the properties.")
    ) % (", ".join(tables) if tables else tr("0Table/1Table missing"))

    report.add(Risk.IDENTITY, "WordDocument",
               tr("Residual text and revision author table"),
               "WordDocument%s" % (" + " + " + ".join(tables) if tables else ""),
               note, False)

    if which_table and tables and which_table in tables:
        stale = [t for t in tables if t != which_table]
        if stale:
            report.add(Risk.IDENTITY, stale[0], tr("Inactive table stream"),
                       tr("%s (active: %s)") % (stale[0], which_table),
                       tr("Word keeps two table streams and switches between them on save. The "
                          "inactive stream is an OLD version of the service structures from the "
                          "previous save, including the previous list of revision authors. No "
                          "'document properties' tool shows it."), False)

    if quick_saves:
        report.add(Risk.PROVENANCE, "WordDocument", tr("Fast-save count"),
                   str(quick_saves),
                   tr("How many times the file was saved in fast-save mode. Anything above zero "
                      "means pieces of past versions appended to the end of the file."))
    if flags is not None and flags & 0x0004:
        report.add(Risk.IDENTITY, "WordDocument", tr("fComplex flag (fast save)"),
                   tr("set"),
                   tr("The document was saved in complex (fast) mode: the text lies in pieces "
                      "mixed with deleted fragments."), False)

    # Reviewer names. With an encrypted file the table itself is ciphertext, and
    # parsing it as text would pass noise off as people.
    if not encrypted and which_table and _top(flat, which_table):
        try:
            _ole_rmark(ole, report, fib, which_table)
        except Exception as exc:  # noqa: BLE001
            report.err(tr("revision author table could not be parsed: %r") % (exc,))

    report.signal(
        "hazard",
        tr("A .doc (OLE2) file cannot be cleaned in place. Editing the document properties "
           "removes neither the revision author table, nor the deleted text of earlier versions, "
           "nor foreign fragments in the free sectors of the container. The only reliable route "
           "is to re-save the content in a modern format (.docx) or export it to PDF, and clean "
           "that instead."),
        "high",
    )


def _ole_xls(ole, report, flat):
    """.xls: the workbook and the external reference records."""
    book = next((n for n in ("Workbook", "Book") if _top(flat, n)), None)
    if not book:
        return
    size = _top(flat, book)[1]
    report.add(Risk.PROVENANCE, book, tr("Workbook stream"),
               tr("%s, %d bytes") % (book, size),
               tr("The workbook body in BIFF format: formulas, hidden sheets, range names and "
                  "the external link cache live here, not in the properties."))

    try:
        blob = _ole_read(ole, book, MAX_READ)
    except Exception as exc:
        report.err(tr("stream %s could not be read: %r") % (book, exc))
        return

    counts = {}
    writeaccess = ""
    sheets = []
    pos, guard = 0, 0
    try:
        while pos + 4 <= len(blob) and guard < 200000:
            guard += 1
            rec = int.from_bytes(blob[pos:pos + 2], "little")
            length = int.from_bytes(blob[pos + 2:pos + 4], "little")
            body = blob[pos + 4:pos + 4 + length]
            pos += 4 + length
            if rec in (0x01AE, 0x0017, 0x0023, 0x002F, 0x01B8, 0x0085, 0x005C):
                counts[rec] = counts.get(rec, 0) + 1
            if rec == 0x005C and not writeaccess:          # WRITEACCESS
                writeaccess = _biff_string(body)
            elif rec == 0x0085 and len(sheets) < MAX_LIST:  # BOUNDSHEET
                name = _biff_string(body[6:], prefix_len=1)
                if name:
                    sheets.append(name)
    except Exception as exc:
        report.err(tr("BIFF record walk aborted: %r") % (exc,))

    if writeaccess:
        report.add(Risk.IDENTITY, "%s:WRITEACCESS" % book, tr("Excel user name"),
                   writeaccess,
                   tr("The WRITEACCESS record stores the user name from the Excel settings "
                      "separately from the document properties: it isn't visible in the "
                      "properties dialog and it survives their cleaning."))
    if sheets:
        report.add(Risk.PROVENANCE, "%s:BOUNDSHEET" % book, tr("Workbook sheets"),
                   "; ".join(sheets),
                   tr("Sheet names, including hidden and very hidden ones."))
    externals = counts.get(0x01AE, 0) + counts.get(0x0017, 0) + counts.get(0x0023, 0)
    if externals:
        report.add(Risk.ENVIRONMENT, "%s:SUPBOOK/EXTERNSHEET" % book,
                   tr("External link records"),
                   "SUPBOOK: %d, EXTERNSHEET: %d, EXTERNNAME: %d"
                   % (counts.get(0x01AE, 0), counts.get(0x0017, 0),
                      counts.get(0x0023, 0)),
                   tr("Links to other workbooks. The path to the source file (often "
                      "\\\\server\\dept\\budget.xls or C:\\Users\\...) is stored inside together "
                      "with the value cache."))
    if counts.get(0x01B8):
        report.add(Risk.ENVIRONMENT, "%s:HLINK" % book, tr("Hyperlinks"),
                   tr("%d pcs") % counts[0x01B8],
                   tr("Hyperlinks often point to local and network paths."))
    if counts.get(0x002F):
        report.add(Risk.STRUCTURAL, "%s:FILEPASS" % book, tr("Workbook is encrypted"),
                   tr("FILEPASS record"), tr("The content is password-protected."), False)


def _biff_string(body, prefix_len=2) -> str:
    """XLUnicodeString from BIFF8: the length, a flags byte, then the characters themselves."""
    try:
        if len(body) < prefix_len + 1:
            return ""
        cch = int.from_bytes(body[:prefix_len], "little")
        flags = body[prefix_len]
        data = body[prefix_len + 1:]
        if flags & 0x01:
            return data[:cch * 2].decode("utf-16-le", "replace").strip("\x00 ")
        return data[:cch].decode("cp1252", "replace").strip("\x00 ")
    except Exception:
        return ""


def _ole_ppt(ole, report, flat, codec="cp1252"):
    """.ppt: the name of the last editor lives in a separate Current User stream."""
    if _top(flat, "PowerPoint Document"):
        size = _top(flat, "PowerPoint Document")[1]
        report.add(Risk.PROVENANCE, "PowerPoint Document", tr("Presentation stream"),
                   tr("%d bytes") % size,
                   tr("The presentation body. Speaker notes, hidden slides and objects moved off "
                      "the slide area also stay here."))
    if not _top(flat, "Current User"):
        return
    try:
        blob = _ole_read(ole, "Current User", 64 * 1024)
        # CurrentUserAtom (MS-PPT 2.3.3): RecordHeader(8) + size(4) + headerToken(4)
        # + offsetToCurrentEdit(4) + lenUserName(2) + docFileVersion(2) + major(1)
        # + minor(1) + unused(2) = a 28-byte header, then ansiUserName follows directly.
        if len(blob) >= 28:
            len_name = int.from_bytes(blob[20:22], "little")
            # ansiUserName is followed by relFileVersion (4 bytes) and unicodeUserName
            # (UTF-16): it doesn't depend on the code page, so it is preferred.
            # The ANSI name is in the author's system code page (Russian Office: cp1251),
            # not in cp1252: otherwise 'Петров' comes out as 'Ïåòðîâ'.
            wide = blob[32 + len_name:32 + 3 * len_name]
            if len_name and len(wide) == 2 * len_name:
                name = wide.decode("utf-16-le", "replace")
            else:
                name = blob[28:28 + len_name].decode(codec, "replace")
            name = name.strip("\x00")
            report.add_nonempty(Risk.IDENTITY, "Current User",
                                tr("Last editor name (CurrentUserAtom)"), name,
                                tr("PowerPoint writes the user name into a separate stream of "
                                   "the container. It isn't shown in the document properties and "
                                   "stays in place after they are cleaned."))
    except Exception as exc:
        report.err(tr("Current User stream could not be parsed: %r") % (exc,))


# ============================================================================
#  B. OpenDocument -- .odt / .ods / .odp
# ============================================================================

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "script": "urn:oasis:names:tc:opendocument:xmlns:script:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}

# meta.xml: tag -> (risk, name, explanation)
_ODF_META = {
    "{%s}initial-creator" % _NS["meta"]: (
        Risk.IDENTITY, N_("Original author"),
        N_("Who created the document. Survives all later edits.")),
    "{%s}creator" % _NS["dc"]: (
        Risk.IDENTITY, N_("Last saved by"),
        N_("In ODF dc:creator is NOT the author but whoever saved the file last.")),
    "{%s}printed-by" % _NS["meta"]: (
        Risk.IDENTITY, N_("Printed by"),
        N_("The name of the user who sent the document to print.")),
    "{%s}generator" % _NS["meta"]: (
        Risk.PROVENANCE, N_("Generator"),
        N_("The exact application build down to the revision number is a strong fingerprint: it "
           "identifies a specific installation and even the distribution.")),
    "{%s}creation-date" % _NS["meta"]: (
        Risk.PROVENANCE, N_("Creation date"), N_("The moment the document was created.")),
    "{%s}date" % _NS["dc"]: (
        Risk.PROVENANCE, N_("Modification date"), N_("The moment of the last save.")),
    "{%s}print-date" % _NS["meta"]: (
        Risk.PROVENANCE, N_("Print date"), N_("When the document was last printed.")),
    "{%s}editing-cycles" % _NS["meta"]: (
        Risk.PROVENANCE, N_("Editing cycles"),
        N_("How many times the document was opened for editing and saved.")),
    "{%s}keyword" % _NS["meta"]: (
        Risk.PROVENANCE, N_("Keyword"), ""),
    "{%s}description" % _NS["dc"]: (
        Risk.PROVENANCE, N_("Description"), ""),
    "{%s}subject" % _NS["dc"]: (
        Risk.PROVENANCE, N_("Subject"), ""),
    "{%s}title" % _NS["dc"]: (
        Risk.PROVENANCE, N_("Title"), ""),
    "{%s}language" % _NS["dc"]: (
        Risk.STRUCTURAL, N_("Language"),
        N_("Needed for spell checking and accessibility -- usually kept.")),
    "{%s}template" % _NS["meta"]: (
        Risk.ENVIRONMENT, N_("Template"), N_("A reference to the template, almost always a local path.")),
}


def _xml_parser():
    """A parser without external entities or network access: we don't want XXE."""
    from lxml import etree
    return etree.XMLParser(resolve_entities=False, no_network=True,
                           huge_tree=False, recover=True)


def _zip_safe_read(zf, info, report) -> bytes:
    """Read a ZIP entry with a bomb check."""
    if info.file_size > MAX_READ:
        report.err(tr("part %s skipped: %d bytes, over the limit")
                   % (info.filename, info.file_size))
        return b""
    ratio = info.file_size / max(info.compress_size, 1)
    if info.compress_size > 0 and ratio > MAX_RATIO and info.file_size > 1 << 20:
        report.err(tr("part %s skipped: compression ratio %.0f looks like a bomb")
                   % (info.filename, ratio))
        return b""
    try:
        return zf.read(info)
    except Exception as exc:
        report.err(tr("part %s could not be read: %r") % (info.filename, exc))
        return b""


def _odf_xml(zf, by_name, name, report):
    """Read and parse a container part. None if it didn't work out."""
    if name not in by_name:
        return None
    blob = _zip_safe_read(zf, by_name[name], report)
    if not blob:
        return None
    try:
        from lxml import etree
        root = etree.fromstring(blob, _xml_parser())
    except Exception as exc:
        report.err(tr("%s could not be parsed: %r") % (name, exc))
        return None
    if root is None:            # recover=True on pure garbage yields None
        report.err(tr("%s contains no parsable XML") % name)
    return root


def inspect_odf(path, fmt="odt") -> Report:
    """Inspect an OpenDocument file: .odt, .ods, .odp."""
    report = Report(path=str(path), fmt=fmt, size=_size(path))
    try:
        zf = zipfile.ZipFile(path)          # opened for reading
    except Exception as exc:
        report.err(tr("the ODF container did not open as a ZIP: %r") % (exc,))
        return report

    try:
        infos = _odf_parts(zf, report)
        by_name = {i.filename: i for i in infos}
        # Each section separately: a damaged part must not take the rest down with it.
        for section in (_odf_meta, _odf_settings, _odf_changes, _odf_manifest):
            try:
                section(zf, by_name, report)
            except Exception as exc:
                report.err("%s: %r" % (section.__name__, exc))
        for section, arg in ((_odf_zip_times, infos), (_odf_extras, by_name)):
            try:
                section(arg, report)
            except Exception as exc:
                report.err("%s: %r" % (section.__name__, exc))
    except Exception as exc:
        report.err(tr("ODF parsing aborted: %r") % (exc,))
    finally:
        try:
            zf.close()
        except Exception:
            pass
    return report


def _odf_parts(zf, report):
    """All ZIP entries -> report.parts, with a bomb guard."""
    try:
        infos = zf.infolist()
    except Exception as exc:
        report.err(tr("the ZIP entry list could not be read: %r") % (exc,))
        return []

    if len(infos) > MAX_PARTS:
        report.err(tr("the container has %d entries, parsing the first %d")
                   % (len(infos), MAX_PARTS))
        infos = infos[:MAX_PARTS]

    total = 0
    kept = []
    for info in infos:
        total += info.file_size
        # The same set of fields as in the OOXML inspector: phase 2 needs the whole
        # ZIP entry in order to rebuild the container byte for byte.
        report.parts.append({
            "name": info.filename,
            "date_time": tuple(info.date_time),
            "compress_type": info.compress_type,
            "compress_size": info.compress_size,
            "file_size": info.file_size,
            "crc": info.CRC,
            "flag_bits": info.flag_bits,
            "create_system": info.create_system,
        })
        kept.append(info)
        if total > MAX_UNPACKED:
            report.err(tr("total uncompressed size exceeded %d bytes; the remaining entries are "
                          "not parsed") % MAX_UNPACKED)
            break
    return kept


def _odf_zip_times(infos, report):
    """ZIP entry times. In ODF they are real, unlike in OOXML."""
    stamps = sorted({i.date_time for i in infos if i.date_time})
    if not stamps:
        return
    zeroed = all(s[:3] == (1980, 1, 1) for s in stamps)
    fmt = lambda s: "%04d-%02d-%02d %02d:%02d:%02d" % s
    value = fmt(stamps[0]) if len(stamps) == 1 else "%s … %s" % (fmt(stamps[0]),
                                                                 fmt(stamps[-1]))
    if zeroed:
        report.add(Risk.PROVENANCE, "ZIP", tr("Container entry times"), value,
                   tr("All timestamps are zeroed to 1980-01-01. That is atypical for ODF: "
                      "LibreOffice writes the real time. The file was most likely rebuilt by a "
                      "program or already cleaned."))
        report.signal("scrubbed",
                      tr("ZIP entry times are zeroed (1980-01-01), although ODF normally stores "
                         "real ones: the container was rebuilt by something other than "
                         "LibreOffice."),
                      "medium")
    else:
        report.add(Risk.PROVENANCE, "ZIP", tr("Container entry times"), value,
                   tr("LibreOffice/OpenOffice write the REAL local save time into the ZIP (Word "
                      "in OOXML puts in a 1980-01-01 placeholder). These timestamps give away "
                      "the exact moment the file was worked on even after meta.xml is fully "
                      "cleaned, and no document properties dialog shows them."))
        report.signal("inconsistent",
                      tr("The ZIP holds real timestamps (%s): they will remain even if meta.xml "
                         "is cleaned.") % value, "high")


def _odf_meta(zf, by_name, report):
    """meta.xml -- the main set of document properties."""
    if "meta.xml" not in by_name:
        report.add(Risk.PROVENANCE, "meta.xml", "meta.xml", "",
                   tr("The meta.xml part is missing from the container: the document properties "
                      "were either cut out or the file was not built by an office application."))
        report.signal("scrubbed", tr("The container has no meta.xml -- the document properties "
                                     "were deleted or never written."),
                      "medium")
        return
    root = _odf_xml(zf, by_name, "meta.xml", report)
    if root is None:
        return

    values = {}
    generator = ""
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        text = (el.text or "").strip()
        known = _ODF_META.get(tag)
        if known:
            risk, label, note = known
            if tag == "{%s}template" % _NS["meta"]:
                # The template path is in the xlink:href attribute; the element has no text.
                # Going by el.text, the report showed "Template: (empty)" while the path with
                # the user name slipped through unnoticed -- and past the cleaning oracle.
                text = el.get("{%s}href" % _NS["xlink"], "").strip() or text
                report.add_nonempty(Risk.PROVENANCE, "meta.xml", tr("Template name"),
                                    el.get("{%s}title" % _NS["xlink"], ""),
                                    tr("The name of the template the document was created from."))
            report.add(risk, "meta.xml", tr(label), text, tr(note))
            values[tag] = text
            if tag.endswith("}generator"):
                generator = text
        elif tag == "{%s}editing-duration" % _NS["meta"]:
            report.add(Risk.PROVENANCE, "meta.xml", tr("Editing time"),
                       _dur_iso(text),
                       tr("How much time in total the document was open for editing. Shows "
                          "whether the text was written over hours or pasted in ready-made "
                          "within a minute."))
            values[tag] = text
        elif tag == "{%s}user-defined" % _NS["meta"]:
            name = el.get("{%s}name" % _NS["meta"], "")
            report.add(Risk.IDENTITY, "meta.xml",
                       tr("Custom property: %s") % clip(name, 80), text,
                       tr("A free-form field filled in by hand or by a corporate template: full "
                          "names, contract numbers, department codes and email addresses end up "
                          "here."))
        elif tag == "{%s}document-statistic" % _NS["meta"]:
            stats = "; ".join("%s=%s" % (k.split("}")[-1], v)
                              for k, v in sorted(el.attrib.items()))
            report.add(Risk.PROVENANCE, "meta.xml", tr("Document statistics"), stats,
                       tr("The number of pages, words, characters, tables and images. Indirectly "
                          "reveals the volume of cut text if compared with the visible content."))
        elif tag == "{%s}auto-reload" % _NS["meta"] or tag == "{%s}hyperlink-behaviour" % _NS["office"]:
            href = el.get("{%s}href" % _NS["xlink"], "")
            report.add_nonempty(Risk.ENVIRONMENT, "meta.xml", tr("Auto-reload/link"),
                                href, tr("The address the document itself points to."))

    if generator:
        report.signal("producer", tr("ODF generator: %s") % generator, "high")
        marker = _looks_generated(generator)
        if marker:
            report.signal("ai", tr("The generator looks like a library/script (marker '%s'): %s") % (marker, generator), "medium")
    else:
        report.signal("scrubbed", tr("meta:generator is empty or missing: usually a sign that "
                                     "the metadata has already been cleaned."),
                      "medium")

    _odf_consistency(values, report)


def _odf_consistency(values, report):
    """Cross-check of the meta.xml dates and counters against each other."""
    created = values.get("{%s}creation-date" % _NS["meta"], "")
    changed = values.get("{%s}date" % _NS["dc"], "")
    if created and changed and changed < created:
        report.signal("inconsistent",
                      tr("Modification date (%s) is earlier than creation date (%s): the "
                         "metadata was edited by hand.") % (changed, created), "high")
    cycles = values.get("{%s}editing-cycles" % _NS["meta"], "")
    duration = values.get("{%s}editing-duration" % _NS["meta"], "")
    try:
        if cycles and int(cycles) <= 1 and duration and duration not in ("PT0S", "P0D"):
            report.signal("inconsistent",
                          tr("There is only one editing cycle (%s), yet the editing time is "
                             "non-empty (%s): the file seems to have been rebuilt by a program "
                             "from someone else's source.") % (cycles, duration), "low")
    except ValueError:
        pass
    if not values.get("{%s}initial-creator" % _NS["meta"]) and \
            not values.get("{%s}creator" % _NS["dc"]):
        report.signal("scrubbed", tr("Neither meta:initial-creator nor dc:creator is filled in "
                                     "-- a sign that cleaning has already been done."),
                      "medium")


def _odf_settings(zf, by_name, report):
    """settings.xml: local paths, printer, data sources."""
    root = _odf_xml(zf, by_name, "settings.xml", report)
    if root is None:
        return

    shown = 0
    for el in root.iter("{%s}config-item" % _NS["config"]):
        name = el.get("{%s}name" % _NS["config"], "")
        text = (el.text or "").strip()
        if not text:
            continue
        low = name.lower()
        if "printer" in low:
            report.add(Risk.ENVIRONMENT, "settings.xml", tr("Printer: %s") % name,
                       text if len(text) < 200 else tr("%d bytes of settings") % len(text),
                       tr("The printer name and its settings. This points straight at a specific "
                          "workstation: the print queue name often contains a host name, a "
                          "department or an inventory number."))
            shown += 1
            continue
        if _PATH_RE.search(text) and shown < MAX_LIST:
            report.add(Risk.ENVIRONMENT, "settings.xml", tr("Local path: %s") % name,
                       text,
                       tr("An absolute path or file:// URI in the document settings: reveals the "
                          "user name, the disk layout and network shares."))
            shown += 1
        elif low in ("currentdatabasedatasource", "currentdatabasecommand",
                     "bibliographydatasource") and shown < MAX_LIST:
            report.add(Risk.ENVIRONMENT, "settings.xml", tr("Data source: %s") % name,
                       text, tr("A connection string to a data source."))
            shown += 1
    if shown:
        report.signal("inconsistent",
                      tr("settings.xml still holds %d entries with paths/printer: they aren't "
                         "visible in the document properties and survive their cleaning.")
                      % shown, "medium")


def _odf_changes(zf, by_name, report):
    """content.xml and styles.xml: tracked changes and annotations."""
    authors, dates, annots = set(), set(), []
    scripts = 0
    for name in ("content.xml", "styles.xml"):
        root = _odf_xml(zf, by_name, name, report)
        if root is None:
            continue
        try:
            for info in root.iter("{%s}change-info" % _NS["office"]):
                creator = info.findtext("{%s}creator" % _NS["dc"], "").strip()
                date = info.findtext("{%s}date" % _NS["dc"], "").strip()
                if creator:
                    authors.add((name, creator))
                if date:
                    dates.add(date)
            regions = sum(1 for _ in root.iter("{%s}changed-region" % _NS["text"]))
            if regions:
                report.add(Risk.IDENTITY, name, tr("Tracked changes"),
                           tr("%d change region(s)") % regions,
                           tr("Change tracking mode. Text that looks deleted physically stays in "
                              "the file, together with the name of whoever deleted it and the "
                              "time of the edit."), False)
            for ann in root.iter("{%s}annotation" % _NS["office"]):
                creator = ann.findtext("{%s}creator" % _NS["dc"], "").strip()
                date = ann.findtext("{%s}date" % _NS["dc"], "").strip()
                # The comment text lives in text:p; we don't pull in the author and date here.
                body = " ".join(
                    t.strip() for p in ann.iter("{%s}p" % _NS["text"])
                    for t in p.itertext() if t.strip())
                annots.append((name, creator, date, body))
            scripts += sum(1 for _ in root.iter("{%s}event-listener" % _NS["script"]))
        except Exception as exc:
            report.err(tr("parsing of changes in %s aborted: %r") % (name, exc))

    for where, creator in sorted(authors)[:MAX_LIST]:
        report.add(Risk.IDENTITY, where, tr("Revision author"), creator,
                   tr("A name from office:change-info. It stays in the file even after the "
                      "changes are accepted, unless the change regions are purged."), False)
    if dates:
        ordered = sorted(dates)
        report.add(Risk.PROVENANCE, "content.xml / styles.xml", tr("Edit dates"),
                   tr("%d pcs, %s … %s") % (len(ordered), ordered[0], ordered[-1]),
                   tr("The exact moment of every edit: they let one reconstruct the schedule of "
                      "work on the document."))
    for where, creator, date, body in annots[:MAX_LIST]:
        report.add(Risk.IDENTITY, where, tr("Annotation by: %s") % (creator or "?"),
                   "%s %s" % (date, body),
                   tr("A comment in the document. It contains the author's name, the time and "
                      "the text -- often the most candid piece in the whole file."))
    if annots:
        report.signal("inconsistent",
                      tr("The document has %d comments with authors: the visible text and the "
                         "hidden discussion diverge.") % len(annots), "medium")
    if scripts:
        report.signal("hazard",
                      tr("The document has %d event handlers (script:event-listener): active "
                         "content that runs on opening.") % scripts,
                      "high")


def _odf_manifest(zf, by_name, report):
    """META-INF/manifest.xml: the part list and the encryption flag."""
    name = "META-INF/manifest.xml"
    if name not in by_name:
        report.err(tr("%s is missing -- the container does not conform to the ODF specification") % name)
        return
    root = _odf_xml(zf, by_name, name, report)
    if root is None:
        return
    entries = list(root.iter("{%s}file-entry" % _NS["manifest"]))
    encrypted = sum(1 for e in entries
                    if e.find("{%s}encryption-data" % _NS["manifest"]) is not None)
    report.add(Risk.STRUCTURAL, name, tr("Container part list"),
               tr("%d entry(ies)") % len(entries),
               tr("The ODF manifest: a list of all parts and their types. Needed to open the "
                  "file -- kept."))
    if encrypted:
        report.add(Risk.STRUCTURAL, name, tr("Encrypted parts"),
                   tr("%d pcs") % encrypted,
                   tr("The content is password-protected: it cannot be parsed."), False)


def _odf_extras(by_name, report):
    """Images, macros, embedded objects, thumbnail."""
    pictures = [n for n in by_name if n.startswith("Pictures/") and not n.endswith("/")]
    macros = [n for n in by_name
              if n.startswith("Basic/") or n.startswith("Scripts/")]
    objects = sorted({n.split("/")[0] for n in by_name
                      if n.startswith("Object ") or n.startswith("ObjectReplacements")})
    thumbs = [n for n in by_name if n.startswith("Thumbnails/")]

    if pictures:
        # STRUCTURAL, not IDENTITY: the value is a list of part names, referenced
        # from content.xml, and cleaning keeps them. As IDENTITY the oracle would see
        # it as "surviving" in any cleaned file that has a picture. The Exif of each
        # picture itself is parsed by the dispatcher's recursion (core.inspect) --
        # that's where the GPS and the serial number are, each with its own risk.
        report.add(Risk.STRUCTURAL, "Pictures/", tr("Nested images"),
                   tr("%d file(s): %s") % (len(pictures),
                                        ", ".join(sorted(pictures)[:MAX_LIST])),
                   tr("Pictures are put into the container AS IS, together with their own Exif: "
                      "GPS coordinates, camera model, serial number and shooting date. The Exif "
                      "of each picture is shown as separate Pictures/... findings and cleaned in "
                      "a separate pass."), False)
    if thumbs:
        report.add(Risk.PROVENANCE, "Thumbnails/", tr("Preview thumbnail"),
                   ", ".join(sorted(thumbs)),
                   tr("An image of the first page. If the text was edited afterwards, the "
                      "thumbnail may show the PREVIOUS version of the content."))
    if macros:
        report.add(Risk.PROVENANCE, tr("Basic/ and Scripts/"), tr("Document macros"),
                   tr("%d part(s): %s") % (len(macros),
                                         ", ".join(sorted(macros)[:MAX_LIST])),
                   tr("Basic code/scripts inside the document. They store the source code, the "
                      "library name and often the path to the developer's machine."), False)
        report.signal("hazard",
                      tr("The container has macros (%s): active content.")
                      % ", ".join(sorted(macros)[:5]), "high")
    if objects:
        report.add(Risk.IDENTITY, "Object*", tr("Embedded objects"),
                   ", ".join(objects[:MAX_LIST]),
                   tr("Nested documents (formulas, charts, tables) -- each has ITS OWN meta.xml "
                      "with its own author and its own dates."), False)


# ============================================================================
#  C. RTF
# ============================================================================

# Groups inside \info: control word name -> (risk, name, explanation)
_RTF_INFO = {
    "author": (Risk.IDENTITY, N_("Author"),
               N_("The user name of the application that created the document.")),
    "operator": (Risk.IDENTITY, N_("Last saved by"),
                 N_("In RTF \\operator is whoever saved the file last. It often differs from "
                    "\\author and reveals a second participant.")),
    "company": (Risk.IDENTITY, N_("Organization"), N_("The company name from the Office settings.")),
    "manager": (Risk.IDENTITY, N_("Manager"), ""),
    "title": (Risk.PROVENANCE, N_("Title"), ""),
    "subject": (Risk.PROVENANCE, N_("Subject"), ""),
    "keywords": (Risk.PROVENANCE, N_("Keywords"), ""),
    "category": (Risk.PROVENANCE, N_("Category"), ""),
    "doccomm": (Risk.PROVENANCE, N_("Document comments"), ""),
    "hlinkbase": (Risk.ENVIRONMENT, N_("Hyperlink base"),
                  N_("The base path for relative links -- usually local.")),
}

_RTF_DATES = {
    "creatim": (N_("Created"), N_("The moment the document was created.")),
    "revtim": (N_("Last saved"), N_("The moment of the last save.")),
    "printim": (N_("Last printed"), N_("When the document was printed.")),
    "buptim": (N_("Backup"), N_("The time the backup copy was created.")),
}

_RTF_NUMS = {
    "edmins": (Risk.PROVENANCE, N_("Editing minutes"),
               N_("The total editing time in minutes. Shows whether the text was written over "
                  "hours or pasted in ready-made within a minute.")),
    "nofpages": (Risk.PROVENANCE, N_("Pages"), ""),
    "nofwords": (Risk.PROVENANCE, N_("Words"), ""),
    "nofchars": (Risk.PROVENANCE, N_("Characters"), ""),
    "nofcharsws": (Risk.PROVENANCE, N_("Characters with spaces"), ""),
    "version": (Risk.PROVENANCE, N_("Document version number"),
                N_("How many times the document was saved.")),
    "vern": (Risk.PROVENANCE, N_("Internal version"),
             N_("The internal version number of the application.")),
    "id": (Risk.PROVENANCE, N_("Internal identifier"),
           N_("An arbitrary number set by the application.")),
}


def _brace_group(text, start):
    """text[start] == '{'. Return (the body without the outer braces, the index after the group).

    RTF escaping is taken into account: \\{ and \\} don't count as braces.
    """
    depth = 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], n        # the group is not closed -- the file is damaged


_CW_RE = re.compile(r"\s*\\(\*\\)?([a-zA-Z]+)(-?\d+)?\s?")


def _subgroups(body):
    """Iterate over the first-level nested groups: (name, content, raw body)."""
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            inner, i = _brace_group(body, i)
            m = _CW_RE.match(inner)
            if m:
                yield m.group(2), inner[m.end():], inner
            else:
                yield "", inner, inner
            continue
        i += 1


def _info_groups(body):
    """Subgroups of \\info with \\upr expanded. Word writes a non-Latin value twice:
    {\\upr{\\author ANSI}{\\*\\ud{\\author Unicode}}} -- and the subgroup is called
    upr, not author, so a by-name parse skipped it. We return (field name,
    content, raw body of the top-level group); the content comes from \\ud, where
    the real text is."""
    for name, content, inner in _subgroups(body):
        if name != "upr":
            yield name, content, inner
            continue
        field, value = "", ""
        for n, c, _i in _subgroups(content):
            if n == "ud":
                for n2, c2, _i2 in _subgroups(c):
                    field, value = n2, c2
                    break
                break
            if n and not field:
                field, value = n, c
        if field:
            yield field, value, inner


def _rtf_plain(text, enc="cp1252") -> str:
    """RTF group text -> plain string: \\'hh, \\uN, \\\\, \\{ and \\}."""
    out, raw = [], bytearray()
    uc = 1

    def flush():
        if raw:
            try:
                out.append(bytes(raw).decode(enc, "replace"))
            except LookupError:
                out.append(bytes(raw).decode("cp1252", "replace"))
            raw.clear()

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "'" and i + 3 < n:
                try:
                    raw.append(int(text[i + 2:i + 4], 16))
                    i += 4
                    continue
                except ValueError:
                    i += 2
                    continue
            if nxt in "\\{}":
                flush()
                out.append(nxt)
                i += 2
                continue
            m = _CW_RE.match(text, i)
            if not m:
                i += 2
                continue
            word, arg = m.group(2), m.group(3)
            i = m.end()
            if word == "uc" and arg is not None:
                uc = max(0, int(arg))
            elif word == "u" and arg is not None:
                flush()
                code = int(arg)
                if code < 0:
                    code += 65536
                out.append(chr(code))
                # \uN is followed by a fallback ANSI representation -- skip uc units
                skipped = 0
                while skipped < uc and i < n:
                    if text[i] == "\\" and text[i + 1:i + 2] == "'":
                        i += 4
                    elif text[i] in "{}\\":
                        break
                    else:
                        i += 1
                    skipped += 1
            elif word in ("par", "line", "tab"):
                flush()
                out.append(" ")
            continue
        if ch in "{}":
            i += 1
            continue
        raw.extend(ch.encode("latin-1", "replace"))
        i += 1
    flush()
    return " ".join("".join(out).split())


def _dest_body(text, dest):
    """Find the destination {\\*\\dest ...} and return its body without the control word."""
    m = re.search(r"\{\\\*?\\?%s(?![a-zA-Z])" % dest, text)
    if not m:
        return None
    body, _end = _brace_group(text, m.start())
    cw = _CW_RE.match(body)
    return body[cw.end():] if cw else body


def _rtf_date(fragment) -> str:
    """\\yr2024\\mo3\\dy5\\hr14\\min7 -> '2024-03-05 14:07:00'."""
    parts = {m.group(1): int(m.group(2))
             for m in re.finditer(r"\\(yr|mo|dy|hr|min|sec)(-?\d+)", fragment)}
    if not parts:
        return ""
    return "%04d-%02d-%02d %02d:%02d:%02d" % (
        parts.get("yr", 0), parts.get("mo", 0), parts.get("dy", 0),
        parts.get("hr", 0), parts.get("min", 0), parts.get("sec", 0))


def inspect_rtf(path) -> Report:
    """Inspect an RTF file. Parsing is streaming: the file isn't loaded into memory whole."""
    report = Report(path=str(path), fmt="rtf", size=_size(path))
    try:
        with open(path, "rb") as fh:            # strictly for reading
            head = fh.read(RTF_HEAD)
    except OSError as exc:
        report.err(tr("the file did not open: %r") % (exc,))
        return report

    if not head:
        report.err(tr("the file is empty"))
        return report
    if not head.lstrip()[:5] == b"{\\rtf":
        report.err(tr("no {\\rtf signature at the start of the file -- parsing as is"))

    text = head.decode("latin-1")               # byte-transparent
    enc = _rtf_codepage(text, report)

    try:
        _rtf_info(text, enc, report)
    except Exception as exc:
        report.err(tr("the \\info group could not be parsed: %r") % (exc,))
    for fn in (_rtf_userprops, _rtf_rsid, _rtf_generator, _rtf_revtbl, _rtf_template):
        try:
            fn(text, enc, report)
        except Exception as exc:
            report.err("%s: %r" % (fn.__name__, exc))
    try:
        _rtf_scan(path, enc, report)
    except Exception as exc:
        report.err(tr("streaming pass over the file aborted: %r") % (exc,))
    return report


def _rtf_codepage(text, report) -> str:
    """\\ansicpgNNNN sets the encoding of eight-bit escape sequences."""
    m = re.search(r"\\ansicpg(\d+)", text[:4096])
    codec = "cp1252"
    if m:
        codec = _cp_codec(m.group(1))
        report.add(Risk.STRUCTURAL, "\\ansicpg", tr("Code page"), m.group(1),
                   tr("Needed to read the text correctly -- kept."))
    m = re.search(r"\\deflang(\d+)", text[:4096])
    if m:
        # STRUCTURAL, not ENVIRONMENT. The locale does indirectly reveal the country,
        # but it cannot be removed: reading the text depends on it. Its direct
        # counterpart in OOXML -- dc:language ("ru-RU" gives away exactly as much) --
        # is marked STRUCTURAL and kept. A value cannot be kept in a leak category
        # while being deliberately left in place: the oracle rightly counts such a
        # value as having survived cleaning, and the "clean" verdict would never be
        # issued -- to no RTF file at all.
        report.add(Risk.STRUCTURAL, "\\deflang", tr("Default language"), m.group(1),
                   tr("The locale identifier of the application that saved the file: indirectly "
                      "reveals the country and the language build of Office. It cannot be "
                      "removed: reading the text correctly depends on it."))
    return codec


def _rtf_info(text, enc, report):
    """The \\info group: document properties."""
    m = re.search(r"\{\\info(?![a-zA-Z])", text)
    if not m:
        report.signal("scrubbed", tr("The RTF has no \\info group: the document properties are "
                                     "absent -- either they were cleaned already or the file was "
                                     "written by a program."), "medium")
        return
    body, _end = _brace_group(text, m.start())

    for name, content, inner in _info_groups(body):
        known = _RTF_INFO.get(name)
        if known:
            risk, label, note = known
            report.add(risk, "\\info\\%s" % name, tr(label), _rtf_plain(content, enc),
                       tr(note))
        elif name in _RTF_DATES:
            label, note = _RTF_DATES[name]
            report.add_nonempty(Risk.PROVENANCE, "\\info\\%s" % name, tr(label),
                                _rtf_date(inner), tr(note))

    # Numeric properties sit right in the \info body, without groups of their own.
    for word, (risk, label, note) in _RTF_NUMS.items():
        m2 = re.search(r"\\%s(-?\d+)" % word, body)
        if not m2:
            continue
        value = m2.group(1)
        if word == "edmins":
            value = _dur_secs(int(value) * 60)
        report.add(risk, "\\info\\%s" % word, tr(label), value, tr(note))


def _rtf_userprops(text, enc, report):
    """{\\*\\userprops {\\propname Name}\\proptype30{\\staticval Value}}."""
    m = re.search(r"\{\\\*\\userprops(?![a-zA-Z])", text)
    if not m:
        return
    body, _end = _brace_group(text, m.start())
    name = None
    for word, content, _inner in _subgroups(body):
        if word == "propname":
            name = _rtf_plain(content, enc)
        elif word == "staticval":
            report.add(Risk.IDENTITY, "\\*\\userprops",
                       tr("Custom property: %s") % clip(name or "?", 80),
                       _rtf_plain(content, enc),
                       tr("A free-form field filled in by hand or by an organization template: "
                          "full names, contract numbers and internal department codes show up "
                          "here."))
            name = None


def _rtf_rsid(text, _enc, report):
    """{\\*\\rsidtbl \\rsid123\\rsid456} -- editing session identifiers."""
    m = re.search(r"\{\\\*\\rsidtbl(?![a-zA-Z])", text)
    if not m:
        return
    body, _end = _brace_group(text, m.start())
    rsids = re.findall(r"\\rsid(-?\d+)", body)
    if not rsids:
        return
    report.add(Risk.PROVENANCE, "\\*\\rsidtbl", tr("Editing session table (rsid)"),
               tr("%d identifier(s): %s") % (len(rsids), ", ".join(rsids[:20])),
               tr("Word creates a new rsid for every editing session and tags every piece of "
                  "text with it. The table shows how many sessions the document took, and the "
                  "text markup shows which paragraphs were added later than the rest and "
                  "therefore where they were copied from."))
    if len(rsids) > 1:
        report.signal("inconsistent",
                      tr("The document has %d editing sessions (rsid): the text was put together "
                         "in at least that many sittings, even if the dates say otherwise.")
                      % len(rsids), "medium")


def _rtf_generator(text, enc, report):
    """{\\*\\generator Riched20 10.0.19041} -- the application string."""
    body = _dest_body(text, "generator")
    if body is None:
        return
    value = _rtf_plain(body, enc).strip(" ;")
    report.add_nonempty(Risk.PROVENANCE, "\\*\\generator", tr("Generator"), value,
                        tr("The exact application string and its version -- a strong fingerprint "
                           "of a specific installation."))
    if value:
        report.signal("producer", tr("RTF generator: %s") % value, "high")
        marker = _looks_generated(value)
        if marker:
            report.signal("ai", tr("The generator looks like a library/script (marker '%s'): %s") % (marker, value), "medium")


def _rtf_revtbl(text, enc, report):
    """{\\*\\revtbl {Unknown;}{Ivanov I.I.;}} -- authors of tracked changes."""
    for dest, label in (("revtbl", tr("Revision author")),
                        ("atrfstart", tr("Annotation tag"))):
        m = re.search(r"\{\\\*\\%s(?![a-zA-Z])" % dest, text)
        if not m:
            continue
        body, _end = _brace_group(text, m.start())
        names = []
        for _word, _content, inner in _subgroups(body):
            value = _rtf_plain(inner, enc).strip(" ;")
            if not value:
                continue
            # "Unknown" is a service zero entry: Word always writes it first, even when
            # the file has no revisions. It carries no identity, yet does harm twice: an
            # extra IDENTITY finding in the report and an author count in the signal that
            # is one too high. On top of that it is longer than the cleaning oracle's
            # threshold (clean.MIN_VALUE_LEN), so it would survive any cleaning and be
            # counted as a leaked value.
            if value.lower() == "unknown":
                continue
            names.append(value)
        if not names and dest == "atrfstart":
            names = [_rtf_plain(body, enc).strip(" ;")]
        for value in names[:MAX_LIST]:
            report.add(Risk.IDENTITY, "\\*\\%s" % dest, label, value,
                       tr("The revision author table. The names stay in the file even after the "
                          "changes themselves are accepted or rejected."),
                       False)
        if dest == "revtbl" and len(names) > 1:
            report.signal("inconsistent",
                          tr("The revision table lists %d author(s): more than one person worked "
                             "on the file, although \\author names just one.") % len(names),
                          "medium")


def _rtf_template(text, enc, report):
    """{\\*\\template ...} -- the path to the template."""
    body = _dest_body(text, "template")
    if body is None:
        return
    value = _rtf_plain(body, enc)
    report.add_nonempty(Risk.ENVIRONMENT, "\\*\\template", tr("Template"), value,
                        tr("The path to the .dot(x): either the local user profile or the "
                           "organization's network share."))


def _rtf_scan(path, enc, report):
    """A streaming pass over the whole file: objects, paths, active content."""
    markers = {
        "objdata": 0, "objclass": 0, "objemb": 0, "objlink": 0,
        "datastore": 0, "shppict": 0, "objupdate": 0,
    }
    paths, classes = [], []
    seen_paths = set()
    tail = ""
    total = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(RTF_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            text = tail + chunk.decode("latin-1")
            last = len(chunk) < RTF_CHUNK
            # The overlap is needed so that a match isn't cut at the chunk boundary,
            # but nothing may be counted in it: it will arrive again with the next chunk.
            cut = len(text) if last else max(0, len(text) - RTF_OVERLAP)
            for word in markers:
                markers[word] += sum(
                    1 for m in re.finditer(r"\\\*?\\?%s(?![a-zA-Z])" % word, text)
                    if m.start() < cut)
            for m in _PATH_RE.finditer(text):
                if m.start() >= cut:
                    continue
                value = _rtf_plain(m.group(0), enc)
                if not _PATH_DECODED_RE.match(value or ""):
                    continue
                if value not in seen_paths and len(seen_paths) < MAX_LIST:
                    seen_paths.add(value)
                    paths.append(value)
            for m in re.finditer(r"\{\\\*\\objclass\s*([^}]{0,120})\}", text):
                if m.start() >= cut:
                    continue
                name = _rtf_plain(m.group(1), enc)
                if name and name not in classes and len(classes) < MAX_LIST:
                    classes.append(name)
            tail = "" if last else text[cut:]

    report.add(Risk.STRUCTURAL, tr("file"), tr("Parsed stream size"),
               tr("%d bytes") % total, tr("How many bytes went through the streaming parse."))

    for value in paths:
        report.add(Risk.ENVIRONMENT, tr("RTF stream"), tr("Absolute path"), value,
                   tr("A file system path right in the document body: reveals the user name, the "
                      "disk layout and network shares. It gets here from hyperlinks, "
                      "INCLUDEPICTURE fields and embedded objects."))
    if markers["objdata"]:
        report.add(Risk.IDENTITY, "\\objdata", tr("Embedded OLE objects"),
                   tr("%d pcs%s") % (markers["objdata"],
                                 (tr(", classes: ") + ", ".join(classes)) if classes else ""),
                   tr("The object is laid into the RTF as a hex dump of a whole OLE container. "
                      "Inside is ITS OWN metadata (author, dates, the path to the source file) "
                      "and, if it's a pasted document, its full content, not just the visible "
                      "picture."), False)
        report.signal("hazard",
                      tr("%d OLE object(s) embedded in the RTF: inside each one is a separate "
                         "container with its own metadata.") % markers["objdata"], "high")
    if markers["datastore"]:
        report.add(Risk.PROVENANCE, "\\*\\datastore", tr("Document data store"),
                   tr("%d pcs") % markers["datastore"],
                   tr("A Word service binary block: among other things it collects settings that "
                      "the document properties don't reflect."), False)
    if markers["shppict"]:
        report.add(Risk.IDENTITY, "\\shppict", tr("Nested images"),
                   tr("%d pcs") % markers["shppict"],
                   tr("Pictures lie in the RTF as they are, together with their own Exif: GPS "
                      "coordinates, camera model, serial number, shooting date. Cleaning the "
                      "document properties doesn't touch them."), False)
    if markers["objemb"]:
        report.add(Risk.IDENTITY, "\\objemb", tr("Embedded objects (\\objemb)"),
                   tr("%d pcs") % markers["objemb"],
                   tr("The flag of an embedded OLE object on every \\object group. The object "
                      "itself and its metadata sit in the neighbouring \\objdata of the same "
                      "group."), False)
    if markers["objlink"]:
        report.add(Risk.ENVIRONMENT, "\\objlink", tr("Linked objects (\\objlink)"),
                   tr("%d pcs") % markers["objlink"],
                   tr("The object is not embedded but LINKED to an external file: the document "
                      "stores the path to the source and goes to it when the field is updated -- "
                      "that is a path on someone else's machine, not the file's content."),
                   False)
        report.signal("hazard",
                      tr("%d object(s) in the RTF are linked to an external file (\\objlink) "
                         "rather than embedded: the document reaches outside itself.")
                      % markers["objlink"], "medium")
    if markers["objupdate"]:
        report.add(Risk.PROVENANCE, "\\objupdate", tr("Auto-update of linked objects"),
                   tr("%d pcs") % markers["objupdate"],
                   tr("Linked objects are updated automatically when the file is opened -- that "
                      "is active behavior, not a static insert."),
                   False)
        report.signal("hazard",
                      tr("\\objupdate is present: objects are updated when the file is opened -- "
                         "active content."), "medium")
    if not report.findings:
        report.signal("scrubbed", tr("Not a single metadata field was found in the RTF: the file "
                                     "was either cleaned already or built from scratch by a "
                                     "program."), "medium")


# ============================================================================
#  Self-test: PYTHONIOENCODING=utf-8 python -m core.insp_legacy
# ============================================================================

class _StubOle:
    """An OleFileIO stub with preset streams -- for the self-test only."""

    def __init__(self, streams):
        self._streams = streams

    def openstream(self, name):
        import io
        return io.BytesIO(self._streams[name])


def _demo_ole_real(tmp, show):
    """inspect_ole() on REAL containers: tests/fixtures.py builds the CFBF
    byte by byte (olefile and Windows ole32 open them without complaint)."""
    import faulthandler
    import struct
    import time
    try:
        from tests import fixtures as fx
    except ImportError:
        print("\n(!) tests.fixtures cannot be imported -- real OLE2 files were not checked")
        return

    def vals(rep, label, loc=None):
        return [f.value for f in rep.findings
                if f.label == label and (loc is None or f.location == loc)]

    # --- .doc: all the planted values, code pages, 1Table ------------------
    path, exp = fx.make_doc(tmp)
    rep = inspect_ole(path, "doc")
    show(rep, "OLE2: a real .doc from tests/fixtures.make_doc")
    assert not rep.errors, rep.errors
    assert vals(rep, "Author") == [fx.DOC_AUTHOR], vals(rep, "Author")
    assert vals(rep, "Organization", "DocumentSummaryInformation") == [fx.DOC_COMPANY]
    assert vals(rep, "Manager") == [fx.DOC_MANAGER]
    assert vals(rep, "Created") == ["2023-03-05 14:07:00"], vals(rep, "Created")
    assert "8220" in vals(rep, "Total editing time")[0], rep.findings
    assert vals(rep, "Container modification time") == ["2024-03-06 09:43:07"]
    # Custom properties: a UTF-16 section, a Russian name, an email in an MSIP label.
    assert vals(rep, "Custom property: Отдел") == [fx.DOC_CUSTOM_DEPT]
    assert vals(rep, "Custom property: MSIP_Label_a1b2_SetBy") == [fx.DOC_MSIP_EMAIL]
    assert all(f.risk.value == "identity" for f in rep.findings
               if f.label.startswith("Custom"))
    assert any(not f.removable and "1Table" in f.value and
               "Residual text" in f.label for f in rep.findings)
    assert vals(rep, "Revision author (SttbfRMark)") == list(fx.DOC_RMARK), (
        vals(rep, "Revision author (SttbfRMark)"))
    assert vals(rep, "Fast-save count") == ["2"]

    # --- .xls: a cp1251 custom property section, WRITEACCESS, sheets ---
    path, exp = fx.make_xls(tmp)
    rep = inspect_ole(path, "xls")
    assert not rep.errors, rep.errors
    assert vals(rep, "Custom property: " + fx.XLS_CUSTOM_KEY) == [fx.XLS_CUSTOM_VALUE]
    assert vals(rep, "Custom property: Owner") == [fx.XLS_OWNER_EMAIL]
    assert vals(rep, "Excel user name") == [fx.XLS_WRITEACCESS_USER]
    assert fx.XLS_HIDDEN_SHEET in vals(rep, "Workbook sheets")[0]
    assert vals(rep, "External link records") == ["SUPBOOK: 2, EXTERNSHEET: 1, EXTERNNAME: 0"]
    assert vals(rep, "Hyperlinks") == ["2 pcs"]

    # --- .ppt: the name from CurrentUserAtom -- both from UTF-16 and from cp1251 -------------
    for uni in (True, False):
        path, exp = fx.make_ppt(tmp, unicode_name=uni)
        rep = inspect_ole(path, "ppt")
        assert vals(rep, "Last editor name (CurrentUserAtom)") == [fx.PPT_USER], \
            (uni, rep.findings)

    # --- a .doc with macros and a nested object ---------------------------
    path, exp = fx.make_doc_active(tmp)
    rep = inspect_ole(path, "doc")
    assert not rep.errors, rep.errors
    assert any(s.kind == "hazard" and "VBA" in s.detail for s in rep.signals)
    assert vals(rep, "Nested OLE objects")[0].startswith("1 object(s): " + fx.DOC_OBJECT_ID)
    assert any("_VBA_PROJECT" in v for v in vals(rep, "Macro stream"))

    # --- an .xls with an embedded Word object: sniff will say 'doc', but parsing
    #     goes by the top level -- the workbook is parsed, and there's no foreign Word in the findings.
    summary, docsum = fx._ole_props_streams()
    word = (b"\xec\xa5" + b"\x00" * 4200)
    p = os.path.join(tmp, "xls_with_word.xls")
    with open(p, "wb") as fh:
        fh.write(fx._cfb_build([
            ("\x05SummaryInformation", summary),
            ("Workbook", struct.pack("<HH", 0x0809, 16) + b"\x00" * 16 + struct.pack("<HH", 0x000A, 0)),
            (("MBD0001A2B3", "\x01CompObj"), fx._ole_compobj("Word", "MSWordDoc", "Word.Document.8", fx._CLSID_WORD8)),
            (("MBD0001A2B3", "WordDocument"), word)]))
    rep = inspect_ole(p, "doc")      # fmt="doc" -- that's how sniff will detect it
    assert not rep.errors, rep.errors
    assert vals(rep, "Workbook stream"), "workbook not parsed"
    assert not vals(rep, "Residual text and revision author table"), "foreign Word"
    assert any(s.kind == "inconsistent" for s in rep.signals), rep.signals

    # --- zero FILETIME and diverging code pages of the property streams ---
    summary = fx._ole_section([
        (1, fx._vt_i2(1252)), (4, fx._vt_lpstr("John Smith", "cp1252")),
        (11, fx._vt_filetime(0)),                       # 'never printed'
        (12, fx._vt_filetime(fx._filetime(fx.DOC_CREATE))),
        (13, fx._vt_filetime(0))])                      # save time not set
    docsum = fx._ole_section([(1, fx._vt_i2(1251)),
                              (15, fx._vt_lpstr(fx.DOC_COMPANY, "cp1251"))])
    p = os.path.join(tmp, "cp_mismatch.doc")
    with open(p, "wb") as fh:
        fh.write(fx._cfb_build([
            ("\x05SummaryInformation", fx._ole_propstream((fx._FMTID_SUMMARY, summary))),
            ("\x05DocumentSummaryInformation", fx._ole_propstream((fx._FMTID_DOCSUM, docsum))),
            ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)]))
    rep = inspect_ole(p, "doc")
    assert vals(rep, "Organization", "DocumentSummaryInformation") == [fx.DOC_COMPANY]
    assert vals(rep, "Last printed") == [""], vals(rep, "Last printed")
    assert not any(s.kind == "inconsistent" for s in rep.signals), rep.signals

    # --- a vector property with a 0xFFFFFFFF counter doesn't hang the parser ---------
    summary = fx._ole_section([
        (1, fx._vt_i2(1251)),
        (3, struct.pack("<HHI", 0x1005, 0, 0xFFFFFFFF)),     # VT_VECTOR|VT_R8
        (4, fx._vt_lpstr(fx.DOC_AUTHOR, "cp1251"))])
    p = os.path.join(tmp, "vector_bomb.doc")
    with open(p, "wb") as fh:
        fh.write(fx._cfb_build([
            ("\x05SummaryInformation", fx._ole_propstream((fx._FMTID_SUMMARY, summary))),
            ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)]))
    faulthandler.dump_traceback_later(20, exit=True)     # regression -- not an endless hang
    t0 = time.time()
    rep = inspect_ole(p, "doc")
    faulthandler.cancel_dump_traceback_later()
    assert time.time() - t0 < 5, "parsing the vector bomb took %.1f s" % (time.time() - t0)
    assert vals(rep, "Author") == [fx.DOC_AUTHOR], rep.findings

    print("\nOK: real OLE2 files (.doc/.xls/.ppt, macros, nested objects, corrupt counters) "
          "parsed")


def _demo():
    import tempfile

    tmp = tempfile.mkdtemp(prefix="insp_legacy_")

    # --- RTF: built by hand with known values -----------------------
    rtf_path = os.path.join(tmp, "probe.rtf")
    rtf = (
        r"{\rtf1\ansi\ansicpg1251\deflang1049"
        r"{\*\generator Riched20 10.0.19041;}"
        r"{\info"
        r"{\author \'c8\'e2\'e0\'ed\'ee\'e2 \'c8\'e2\'e0\'ed}"
        r"{\operator \'cf\'e5\'f2\'f0\'ee\'e2 \'cf\'e5\'f2\'f0}"
        r"{\company \'ce\'ce\'ce \'d0\'ee\'ec\'e0\'f8\'ea\'e0}"
        r"{\title \'d1\'ec\'e5\'f2\'e0 2024}"
        r"{\subject Budget}{\keywords secret;draft}{\category Internal}"
        r"{\manager Sidorov}{\doccomm Do not distribute}"
        r"{\creatim\yr2024\mo3\dy5\hr14\min7}"
        r"{\revtim\yr2024\mo3\dy6\hr9\min42}"
        r"{\printim\yr2024\mo3\dy6\hr10\min0}"
        r"\edmins137\nofpages4\nofwords1200\nofchars7800\version7\vern16393\id1234567"
        r"}"
        r"{\*\userprops{\propname Dept}\proptype30{\staticval K-17}"
        r"{\propname Contract}\proptype30{\staticval 2024/88-\'c0}}"
        r"{\*\rsidtbl \rsid1234567\rsid7654321\rsid1111111}"
        r"{\*\revtbl {Unknown;}{Ivanov I.I.;}{Petrov P.P.;}}"
        r"{\*\template C:\\Users\\ivanov\\AppData\\Roaming\\Microsoft\\Templates\\Normal.dotm}"
        r"\par Text with a link to file:///C:/secret/plan.xlsx and C:\\Work\\old.doc"
        # A decoy from a real Word file: a literal backslash in the text, followed
        # by control words. _PATH_RE finds this match in the raw text, but after
        # decoding a single "\" character is left -- not a path, just noise.
        r"\par Literal backslash \\\hich\f37 in body"
        r"{\*\objclass Word.Document.8}{\object\objemb{\*\objdata 0105000002000000}}"
        r"{\*\datastore 00}\par}"
    )
    with open(rtf_path, "w", encoding="latin-1") as fh:
        fh.write(rtf)

    # --- ODF: a ZIP with mimetype, meta.xml, settings.xml, content.xml -----------
    odt_path = os.path.join(tmp, "probe.odt")
    meta_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
        '<meta:generator>LibreOffice/7.6.4.1$Linux_X86_64 '
        'LibreOffice_project/60$Build-1</meta:generator>'
        '<meta:initial-creator>Иванов Иван</meta:initial-creator>'
        '<dc:creator>Петров Пётр</dc:creator>'
        '<meta:printed-by>Сидоров С.</meta:printed-by>'
        '<meta:creation-date>2024-03-05T14:07:00</meta:creation-date>'
        '<dc:date>2024-03-06T09:42:11</dc:date>'
        '<meta:print-date>2024-03-06T10:00:00</meta:print-date>'
        '<meta:editing-cycles>7</meta:editing-cycles>'
        '<meta:editing-duration>PT2H17M45S</meta:editing-duration>'
        '<dc:title>Смета 2024</dc:title><dc:subject>Budget</dc:subject>'
        '<meta:keyword>secret</meta:keyword>'
        '<dc:description>черновик, не рассылать</dc:description>'
        '<meta:user-defined meta:name="Отдел">К-17</meta:user-defined>'
        '<meta:document-statistic meta:page-count="4" meta:word-count="1200" '
        'meta:character-count="7800"/>'
        '</office:meta></office:document-meta>'
    )
    settings_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-settings '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
        '<config:config-item config:name="PrinterName" config:type="string">'
        'HP LaserJet на SRV-BUH01</config:config-item>'
        '<config:config-item config:name="CurrentDatabaseDataSource" '
        'config:type="string">file:///home/ivanov/base/kadry.odb</config:config-item>'
        '</office:document-settings>'
    )
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:body><office:text>'
        '<text:tracked-changes><text:changed-region text:id="ct1">'
        '<text:deletion><office:change-info><dc:creator>Иванов Иван</dc:creator>'
        '<dc:date>2024-03-06T09:10:00</dc:date></office:change-info>'
        '</text:deletion></text:changed-region></text:tracked-changes>'
        '<text:p>Текст<office:annotation><dc:creator>Петров Пётр</dc:creator>'
        '<dc:date>2024-03-06T09:41:00</dc:date><text:p>перепроверить цифру</text:p>'
        '</office:annotation></text:p></office:text></office:body>'
        '</office:document-content>'
    )
    manifest_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" '
        'manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )
    with zipfile.ZipFile(odt_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype", (2024, 3, 6, 9, 42, 10)),
                    "application/vnd.oasis.opendocument.text",
                    zipfile.ZIP_STORED)
        for name, blob in (("meta.xml", meta_xml), ("settings.xml", settings_xml),
                           ("content.xml", content_xml), ("styles.xml", content_xml),
                           ("META-INF/manifest.xml", manifest_xml),
                           ("Pictures/10000001.jpg", "\xff\xd8\xffdummy"),
                           ("Basic/Standard/Module1.xml", "<x/>"),
                           ("Thumbnails/thumbnail.png", "png")):
            zf.writestr(zipfile.ZipInfo(name, (2024, 3, 6, 9, 42, 12)), blob)

    # --- OLE2: broken inputs (real containers are further below, from tests/fixtures.py)
    broken_path = os.path.join(tmp, "broken.doc")
    with open(broken_path, "wb") as fh:
        fh.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200)

    def show(report, title):
        print("=" * 78)
        print("%s  [fmt=%s, size=%d]" % (title, report.fmt, report.size))
        print("-" * 78)
        for f in report.findings:
            print("  [%s] %-28s | %-34s | %s%s"
                  % (f.risk.value[:4], f.location[:28], f.label[:34],
                     f.value[:70], "" if f.removable else "   <NOT REMOVABLE>"))
        for s in report.signals:
            print("  ~ %-12s (%s) %s" % (s.kind, s.confidence, s.detail))
        for e in report.errors:
            print("  ! %s" % e)
        print("  total: %d findings, %d need cleaning, %d parts, %d errors"
              % (len(report.findings), report.sensitive, len(report.parts),
                 len(report.errors)))

    r_rtf = inspect_rtf(rtf_path)
    show(r_rtf, "RTF")
    r_odf = inspect_odf(odt_path, "odt")
    show(r_odf, "ODF (.odt)")
    for name, probe in (("OLE2: missing file", os.path.join(tmp, "нет.doc")),
                        ("OLE2: corrupt signature", broken_path),
                        ("OLE2: an ODF passed off as OLE2", odt_path)):
        show(inspect_ole(probe, "doc"), name)

    # A third-party OLE2 file from the system (if there is one): .msi is also OLE2
    # with its own SummaryInformation. The .doc/.xls/.ppt containers are built by
    # tests/fixtures.py.
    import glob
    real = sorted(glob.glob(r"C:\Windows\Installer\*.msi"))
    if real:
        r_ole = inspect_ole(real[0], "ole")
        show(r_ole, "OLE2: a real container %s" % os.path.basename(real[0]))
        assert not r_ole.errors, r_ole.errors
        assert r_ole.parts and r_ole.findings
    else:
        print("\n(!) no OLE2 file for the container-walk test was found on the system")

    # The .doc specifics are checked on a stream stub: FIB parsing, choosing the
    # active table and the text about residual content -- that is pure logic.
    r_doc = Report(path="stub.doc", fmt="doc")
    flags = 0x0004 | 0x0200 | (3 << 4)     # fComplex + 1Table active + 3 fast saves
    fib = (b"\xec\xa5" + b"\xc1\x00" + b"\x00\x00" + b"\x19\x04" + b"\x00\x00"
           + flags.to_bytes(2, "little") + b"\x00" * 52)
    _ole_word(_StubOle({"WordDocument": fib}), r_doc,
              {"WordDocument": [("WordDocument", len(fib), "stream")],
               "0Table": [("0Table", 512, "stream")],
               "1Table": [("1Table", 4096, "stream")]})
    show(r_doc, "OLE2: the .doc logic on a WordDocument stream stub")
    dvalues = {f.label: f.value for f in r_doc.findings}
    assert "0Table" in dvalues.get("Inactive table stream", ""), dvalues
    assert dvalues.get("Fast-save count") == "3", dvalues
    assert dvalues.get("Application language (lid)") == "0x0419", dvalues
    assert any(not f.removable and "Residual text" in f.label
               for f in r_doc.findings), dvalues
    assert any(s.kind == "hazard" and s.confidence == "high" and
               "cannot be cleaned in place" in s.detail for s in r_doc.signals)
    assert not r_doc.errors, r_doc.errors

    _demo_ole_real(tmp, show)

    # Minimal checks: if parsing breaks, it will fail here and not in the bot.
    # \uN: a unicode escape with a fallback ANSI character that has to be eaten
    assert _rtf_plain(r"\u1055 ?\u1105 ?\u1090 ?\u1088 !") == "Пётр", \
        _rtf_plain(r"\u1055 ?\u1105 ?\u1090 ?\u1088 !")

    values = {f.label: f.value for f in r_rtf.findings}
    assert values.get("Author") == "Иванов Иван", values.get("Author")
    assert values.get("Last saved by") == "Петров Петр", values
    assert values.get("Generator") == "Riched20 10.0.19041", values
    assert values.get("Template", "").startswith("C:\\Users\\ivanov"), values
    assert values.get("Created") == "2024-03-05 14:07:00", values.get("Created")
    assert "2 h 17 min" in values.get("Editing minutes", ""), values
    assert any("rsid" in f.label.lower() for f in r_rtf.findings)
    assert any(f.label == "Revision author" and "Ivanov" in f.value
               for f in r_rtf.findings)
    assert any("Absolute path" == f.label and "secret" in f.value
               for f in r_rtf.findings), [f.value for f in r_rtf.findings]

    # False positives found on a real file from Word.
    rtf_paths = [f.value for f in r_rtf.findings if f.label == "Absolute path"]
    assert all(_PATH_DECODED_RE.match(v) for v in rtf_paths), rtf_paths
    assert "\\" not in rtf_paths, (
        "a stub of a single backslash is not a path: the match is checked after decoding, not "
        "before: %r" % (rtf_paths,))
    assert len(rtf_paths) == 3, rtf_paths
    rtf_revs = [f.value for f in r_rtf.findings if f.label == "Revision author"]
    assert "Unknown" not in rtf_revs, (
        "the service zero revtbl entry was passed off as a person: %r" % (rtf_revs,))
    assert sorted(rtf_revs) == ["Ivanov I.I.", "Petrov P.P."], rtf_revs
    assert any(s.kind == "inconsistent" and "table lists 2 author" in s.detail
               for s in r_rtf.signals), (
        "the author count must count people, not table entries: %r"
        % [s.detail for s in r_rtf.signals])

    # A multi-chunk pass: a marker that falls into the overlap must not be doubled.
    big_path = os.path.join(tmp, "big.rtf")
    with open(big_path, "w", encoding="latin-1") as fh:
        fh.write(r"{\rtf1\ansi" + "A" * (RTF_CHUNK - 40)
                 + r"{\*\objdata 0102}" + "B" * 5000
                 + r"{\*\objdata 0304}" + "}")
    r_big = inspect_rtf(big_path)
    show(r_big, "RTF: a file larger than one chunk (overlap check)")
    objs = {f.label: f.value for f in r_big.findings}.get("Embedded OLE objects", "")
    assert objs.startswith("2 pcs"), objs

    ovalues = {f.label: f.value for f in r_odf.findings}
    assert ovalues.get("Original author") == "Иванов Иван", ovalues
    assert "LibreOffice/7.6.4.1" in ovalues.get("Generator", "")
    assert "2 h 17 min 45 s" in ovalues.get("Editing time", ""), ovalues
    assert "2024-03-06 09:42" in ovalues.get("Container entry times", "")
    assert any(f.label.startswith("Printer") for f in r_odf.findings)
    assert len(r_odf.parts) == 9, len(r_odf.parts)
    assert any(f.label.startswith("Annotation") and
               f.value.endswith("перепроверить цифру") for f in r_odf.findings), \
        [f.value for f in r_odf.findings if f.label.startswith("Annotation")]

    print("\nOK: self-test passed, temporary files in %s" % tmp)


if __name__ == "__main__":
    _demo()
