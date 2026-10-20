"""Detect the format from content, not from the extension.

Extensions lie: a .docx may turn out to be a .doc renamed by hand, and a
"document" someone sent may be a JPEG. Cleaning a file with the wrong
inspector is more dangerous than not cleaning it at all: the result looks
processed but still leaks.

Stdlib only.
"""

from __future__ import annotations

import errno
import os
import zipfile
from core.i18n import N_, tr

# Format -> human-readable name. Keys are used as Report.fmt.
NAMES = {
    "docx": "Word (OOXML)",
    "xlsx": "Excel (OOXML)",
    "pptx": "PowerPoint (OOXML)",
    "odt": "OpenDocument Text",
    "ods": "OpenDocument Spreadsheet",
    "odp": "OpenDocument Presentation",
    "zip": N_("ZIP archive"),
    "pdf": "PDF",
    "doc": "Word 97-2003 (OLE2)",
    "xls": "Excel 97-2003 (OLE2)",
    "ppt": "PowerPoint 97-2003 (OLE2)",
    "ole": N_("OLE2 container (type not recognized)"),
    "rtf": "RTF",
    "jpeg": "JPEG",
    "png": "PNG",
    "tiff": "TIFF",
    "heic": "HEIF/HEIC",
    "webp": "WebP",
    "gif": "GIF",
    "text": N_("Text/CSV"),
    "unknown": N_("Unrecognized format"),
    # Not "unrecognized" but "unread": the format is unknown because the file
    # didn't open (cloud placeholder, no permission, locked, not found). The
    # reason comes from unreadable_reason().
    "unreadable": N_("File is not readable"),
}

# Which inspector handles the format (filled in by inspect.py).
FAMILY = {
    "docx": "ooxml", "xlsx": "ooxml", "pptx": "ooxml",
    "odt": "odf", "ods": "odf", "odp": "odf",
    "pdf": "pdf",
    "doc": "ole", "xls": "ole", "ppt": "ole", "ole": "ole",
    "rtf": "rtf",
    "jpeg": "image", "png": "image", "tiff": "image",
    "heic": "image", "webp": "image", "gif": "image",
    "zip": "zip", "text": "text", "unknown": "unknown",
    "unreadable": "unreadable",
}

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# ODF mimetype -> format mapping.
_ODF = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
}


def _read_head(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(4096)


def sniff(path) -> str:
    """Return the format key. Never raises.

    'unreadable': the file didn't open (reason: unreadable_reason()).
    'unknown': the file was read but the signature wasn't recognized (or it's empty).
    These are different diagnoses: the first is fixed by user action, the second isn't.
    """
    try:
        head = _read_head(path)
    except (OSError, ValueError):  # ValueError: NUL in path
        return "unreadable"

    if not head:
        return "unknown"

    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:8] == OLE_MAGIC:
        return _sniff_ole(path)
    if head[:5] == b"{\\rtf":
        return "rtf"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim"):
            return "heic"
    if head[:2] == b"PK":
        return _sniff_zip(path)
    # Some PDFs start with junk; the spec allows %PDF- within the first 1024 bytes.
    # Only AFTER the exact signatures: a ZIP whose first entry is an uncompressed
    # PDF has %PDF- in its first hundred bytes and used to be detected as PDF.
    if b"%PDF-" in head[:1024]:
        return "pdf"

    # Text: no NUL bytes and it decodes.
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            for enc in ("cp1251", "cp1252"):
                try:
                    head.decode(enc)
                    return "text"
                except UnicodeDecodeError:
                    pass
    return "unknown"


def _sniff_zip(path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            # ODF declares itself with a first 'mimetype' entry (uncompressed).
            if "mimetype" in names:
                try:
                    # Bounded: a tiny zip can declare a huge deflated mimetype.
                    with zf.open("mimetype") as fh:
                        mime = fh.read(100).strip()
                    if mime in _ODF:
                        return _ODF[mime]
                except Exception:
                    pass
            if "[Content_Types].xml" in names:
                if any(n.startswith("word/") for n in names):
                    return "docx"
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
                if any(n.startswith("ppt/") for n in names):
                    return "pptx"
            return "zip"
    except (zipfile.BadZipFile, OSError):
        return "zip"


def _sniff_ole(path) -> str:
    """The OLE2 type is decided by the names of the container's TOP-LEVEL streams.

    Top-level only, not the whole tree. An embedded OLE object lives inside a
    storage (ObjectPool/_1396712391/WordDocument), and searching names flatly
    across the tree would detect an Excel workbook with an embedded Word
    document as .doc, and then the wrong inspector would parse it.
    Cleaning a file with the wrong inspector is more dangerous than not
    cleaning it at all: the result looks processed but still leaks.
    """
    try:
        import olefile
    except ImportError:
        return "ole"
    try:
        if not olefile.isOleFile(path):
            return "ole"
        with olefile.OleFileIO(path) as ole:
            # listdir() returns a path as a list of components; length 1 == top level.
            top = {parts[0] for parts in ole.listdir() if len(parts) == 1}
            if "WordDocument" in top:
                return "doc"
            if "Workbook" in top or "Book" in top:
                return "xls"
            if "PowerPoint Document" in top:
                return "ppt"
    except Exception:
        pass
    return "ole"


# --- why a file can't be read --------------------------------------------------
# Windows attributes from GetFileAttributesW. A cloud provider placeholder
# (OneDrive Files On-Demand and the like) has a size and metadata on disk but
# no content, and open() fails with OSError (errno 22 when measured).
_ATTR_REPARSE_POINT = 0x00000400
_ATTR_OFFLINE = 0x00001000
_ATTR_RECALL_ON_OPEN = 0x00040000
_ATTR_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_ATTRS = _ATTR_OFFLINE | _ATTR_RECALL_ON_OPEN | _ATTR_RECALL_ON_DATA_ACCESS
_INVALID_ATTRS = 0xFFFFFFFF


def _win_attrs(path):
    """Windows file attributes, or None (not Windows, or the call failed).
    A separate function so tests can patch it without real placeholders."""
    try:
        import ctypes
        # Our own DLL instance: don't touch restype on the shared windll.kernel32.
        # Outside Windows there is no ctypes.WinDLL: AttributeError, return None.
        fn = ctypes.WinDLL("kernel32").GetFileAttributesW
        fn.restype = ctypes.c_uint32
        fn.argtypes = [ctypes.c_wchar_p]
        attrs = fn(os.fspath(path))
    except Exception:
        return None
    return None if attrs == _INVALID_ATTRS else attrs


def unreadable_reason(path):
    """Why a file won't open: (reason, errno or None). Never raises.

    Reasons: cloud (cloud placeholder), dir, denied (errno 13: no permission OR
    the file is locked by another process; the Windows CRT can't tell them
    apart and open()'s exception has no winerror), missing (errno 2), reparse
    (link/reparse point without cloud attributes), other.
    Cloud is detected by ATTRIBUTES, not errno: the same errno 22 also comes
    from an invalid file name.
    """
    try:
        try:
            _read_head(path)
            return "other", None   # readable now (downloaded between attempts)
        except OSError as exc:
            code = exc.errno
        if os.path.isdir(path):
            return "dir", code
        attrs = _win_attrs(path) or 0
        if attrs & _CLOUD_ATTRS:
            return "cloud", code
        if code == errno.EACCES:
            return "denied", code
        if code == errno.ENOENT:
            return "missing", code
        if attrs & _ATTR_REPARSE_POINT:
            return "reparse", code
        return "other", code
    except Exception:
        return "other", None


def pretty(fmt: str) -> str:
    return tr(NAMES.get(fmt, fmt))


def family(fmt: str) -> str:
    return FAMILY.get(fmt, "unknown")
