"""PDF inspector: what exactly leaks from the file, and from where.

Phase 0: read only. The file is opened strictly for reading (pikepdf.open
without allow_overwriting_input); nothing is saved or rewritten.

The inspector never raises: any unreadable or broken section becomes an
entry in report.errors, not a crash. We get both damaged files and
deliberately malformed ones.

Requires pikepdf (QPDF bindings; no separate qpdf CLI needed) and lxml.
exiftool is optional enrichment: if found, it adds findings; if not, we
quietly work without it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pikepdf
from lxml import etree

from .model import Report, Risk, clip
from core.i18n import N_, tr

# --- limits (protection against bombs and files built to hang us) ------

MAX_PAGES = 300            # pages we walk page by page
MAX_ANNOTS = 400           # annotations in total
MAX_FIELDS = 300           # form fields in total
MAX_FONTS = 300            # fonts in total
MAX_ATTACH = 100           # attachments in total
MAX_IMAGES = 2000          # JPEG images we parse
MAX_OCGS = 200             # optional content layers
MAX_XMP_KEYS = 400         # XMP keys
MAX_HISTORY = 60           # xmpMM:History events
MAX_STREAM = 50 * 1024 * 1024   # never read more than 50 MB into memory
MAX_XMP_PACKED = 8 * 1024 * 1024  # compressed XMP larger than 8 MB is not decompressed
SCAN_CHUNK = 4 * 1024 * 1024    # chunk size for the raw scan
SCAN_OVERLAP = 512              # overlap between chunks so matches aren't lost

# --- name maps ---------------------------------------------------------------

_NS_PREFIX = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/bj/": "xmpBJ",
    "http://ns.adobe.com/xap/1.0/t/pg/": "xmpTPg",
    "http://ns.adobe.com/xap/1.0/g/": "xmpG",
    "http://ns.adobe.com/xap/1.0/g/img/": "xmpGImg",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://ns.adobe.com/xap/1.0/sType/Dimensions#": "stDim",
    "http://ns.adobe.com/xap/1.0/sType/Version#": "stVer",
    "http://ns.adobe.com/xap/1.0/sType/Job#": "stJob",
    "http://ns.adobe.com/pdf/1.3/": "pdf",
    "http://ns.adobe.com/pdfx/1.3/": "pdfx",
    "http://www.aiim.org/pdfa/ns/id/": "pdfaid",
    "http://www.aiim.org/pdfua/ns/id/": "pdfuaid",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/illustrator/1.0/": "illustrator",
    "http://ns.adobe.com/InDesign/1.0/": "indesign",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/xmp/note/": "xmpNote",
    "http://ns.adobe.com/xmp/Identifier/qual/1.0/": "xmpidq",
    "adobe:ns:meta/": "x",
}

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"

# /Info keys: human-readable name + risk category.
_INFO_MAP = {
    "/Author": (N_("Author"), Risk.IDENTITY),
    "/Creator": (N_("Creator (authoring application)"), Risk.PROVENANCE),
    "/Producer": (N_("Producer (writing library)"), Risk.PROVENANCE),
    "/CreationDate": (N_("Creation date"), Risk.PROVENANCE),
    "/ModDate": (N_("Modification date"), Risk.PROVENANCE),
    "/Title": (N_("Title"), Risk.PROVENANCE),
    "/Subject": (N_("Subject"), Risk.PROVENANCE),
    "/Keywords": (N_("Keywords"), Risk.PROVENANCE),
    "/Trapped": (N_("Trapped (trapping for print)"), Risk.PROVENANCE),
}

# Persistent XMP GUIDs: they link ALL versions of a document together.
_XMP_GUID = {
    "xmpMM:DocumentID",
    "xmpMM:InstanceID",
    "xmpMM:OriginalDocumentID",
    "xmpMM:DerivedFrom",
}

# XMP keys that point to a person.
_XMP_IDENTITY = {
    "dc:creator", "dc:rights", "dc:publisher", "dc:contributor",
    "photoshop:Credit", "photoshop:AuthorsPosition", "photoshop:Source",
    "xmpRights:Owner", "xmpRights:WebStatement", "xmpRights:Marked",
    "xmpRights:UsageTerms", "pdfx:Company", "pdf:Author",
}

# Producer fingerprints: (regex, what it is, whether it's a library generator).
_TOOL_MARKS = [
    (re.compile(r"Skia/PDF", re.I),
     N_("printed from headless Chrome/Chromium (Skia/PDF engine): Puppeteer/Playwright or \"Save "
        "as PDF\""), True),
    (re.compile(r"ReportLab", re.I), N_("ReportLab Python library"), True),
    (re.compile(r"(PyFPDF|fpdf2|FPDF\s)", re.I), N_("fpdf2/PyFPDF Python library"), True),
    (re.compile(r"wkhtmltopdf", re.I), N_("wkhtmltopdf (HTML -> PDF via Qt WebKit)"), True),
    (re.compile(r"(WeasyPrint|Prince|dompdf|TCPDF|mPDF|iText|Apache FOP)", re.I),
     N_("server-side PDF generation library"), True),
    (re.compile(r"(pikepdf|pypdf|PyPDF2|cpdf|MuPDF|mutool)", re.I),
     N_("Python/CLI PDF post-processing library"), True),
    (re.compile(r"pdfTeX-1\.40", re.I), "LaTeX (pdfTeX 1.40.x)", False),
    (re.compile(r"(XeTeX|LuaTeX|dvips|dvipdfm)", re.I), N_("TeX toolchain"), False),
    (re.compile(r"LibreOffice|OpenOffice", re.I), "LibreOffice/OpenOffice", False),
    (re.compile(r"Microsoft.*Print To PDF", re.I), N_("the \"Microsoft Print to PDF\" virtual "
                                                      "printer"), False),
    (re.compile(r"Microsoft.*(Word|Excel|PowerPoint)", re.I), "Microsoft Office", False),
    (re.compile(r"Adobe.*Distiller", re.I), "Adobe Distiller", False),
    (re.compile(r"(Adobe.*Acrobat|Adobe PDF Library|Acrobat Distiller)", re.I), "Adobe Acrobat", False),
    (re.compile(r"Ghostscript", re.I), "Ghostscript", True),
    (re.compile(r"\bqpdf\b", re.I), "qpdf", True),
    (re.compile(r"pandoc", re.I), "pandoc", True),
    (re.compile(r"Quartz PDFContext", re.I), N_("macOS (Quartz PDFContext): the file was made on "
                                                "a Mac"), False),
    (re.compile(r"Chromium", re.I), "Chromium", True),
    (re.compile(r"Nitro|Foxit|PDF-XChange|Soda PDF", re.I), N_("third-party PDF editor"), False),
    (re.compile(r"Canva|Figma|Google", re.I), N_("online service (Canva/Figma/Google)"), False),
]

# Producers that appear in a file only after POST-PROCESSING.
_REWRITER = re.compile(r"(qpdf|Ghostscript|pikepdf|pypdf|PyPDF2|cpdf|mutool|MuPDF|exiftool)", re.I)
# Authoring applications that write their own Producer.
_AUTHORING = re.compile(r"(Microsoft|Word|Excel|PowerPoint|LibreOffice|OpenOffice|Acrobat|InDesign|Illustrator|Pages)", re.I)

# Fonts that date a document regardless of the declared dates.
_FONT_ERA = [
    ("aptos", N_("Aptos is the Microsoft 365 default font since 2023: the document was created "
                 "NO EARLIER than 2023, whatever the declared dates")),
    ("calibri", N_("Calibri is the Office 2007-2023 default font: the document comes from that "
                   "time window")),
    ("aptos display", N_("Aptos Display is a Microsoft 365 typeface since 2023")),
]

_SUBSET = re.compile(r"^([A-Z]{6})\+(.+)$")

_PATHISH = re.compile(
    r"(^[A-Za-z]:[\\/])|(^\\\\)|(^file://)|(/(Users|home|Volumes|var|tmp|mnt)/)|(\\Users\\)",
    re.I,
)

# Standard paper sizes: mm -> name (paper size gives away the country).
_PAPER = [
    (210.0, 297.0, N_("A4 (ISO, everywhere except North America)")),
    (215.9, 279.4, N_("Letter (US/Canada)")),
    (215.9, 355.6, N_("Legal (US)")),
    (297.0, 420.0, "A3 (ISO)"),
    (148.0, 210.0, "A5 (ISO)"),
    (279.4, 431.8, N_("Tabloid (US)")),
]

_PDF_DATE = re.compile(
    r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*([Zz+\-])?(\d{2})?'?(\d{2})?"
)


# --- small utilities ---------------------------------------------------------


def _s(obj) -> str:
    """A PDF object's value as a string. Never raises."""
    if obj is None:
        return ""
    try:
        if isinstance(obj, pikepdf.String):
            return str(obj)
        if isinstance(obj, pikepdf.Name):
            return str(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", "replace")
        text = str(obj)
        # For dicts and arrays str() gives a repr like pikepdf.Array([...]):
        # only the content goes into the report, without the wrapper.
        if text.startswith("pikepdf.") and "(" in text:
            text = text.split("(", 1)[1].rsplit(")", 1)[0].strip()
        return text
    except Exception:
        return tr("<unreadable value>")


def _get(dct, key):
    """dct.get(key) that doesn't fail on broken/foreign objects."""
    try:
        return dct.get(key)
    except TypeError:
        # A non-UTF-8 key (/Auth#9Dor): get() doesn't accept such a string, but [] does.
        try:
            return dct[key] if key in dct else None
        except Exception:
            return None
    except Exception:
        return None


def _stream_text(obj) -> str:
    """Body text: /JS (and similar fields) in a PDF can be a string or a stream;
    a script body sits in a stream just as often as in a string. The compressed
    size is checked before decompression: the same bomb protection as for /Metadata."""
    if obj is None:
        return ""
    try:
        packed = len(obj.read_raw_bytes())
        if packed > MAX_XMP_PACKED:
            return tr("<stream too large to decompress: %d compressed bytes>") % packed
        return obj.read_bytes()[:MAX_STREAM].decode("utf-8", "replace")
    except Exception:
        return _s(obj)


def _pdf_date(raw: str) -> str:
    """D:YYYYMMDDHHmmSS+TZ -> readable form. Empty string if it couldn't be parsed."""
    m = _PDF_DATE.match((raw or "").strip())
    if not m:
        return ""
    y, mo, d, hh, mm, ss, sign, tzh, tzm = m.groups()
    out = "%s-%s-%s" % (y, mo or "01", d or "01")
    if hh:
        out += " %s:%s:%s" % (hh, mm or "00", ss or "00")
    if sign in ("Z", "z"):
        out += " UTC"
    elif sign and tzh:
        out += " UTC%s%s:%s" % (sign, tzh, tzm or "00")
    return out


def _looks_like_path(value: str) -> bool:
    return bool(_PATHISH.search(value or ""))


def _qname(tag) -> str:
    """{uri}Local -> prefix:Local for known namespaces."""
    tag = str(tag)
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    prefix = _NS_PREFIX.get(uri)
    if not prefix:
        prefix = uri.rstrip("/#").rsplit("/", 1)[-1][:24] or "ns"
    return "%s:%s" % (prefix, local)


# --- A. opening the file -----------------------------------------------------


def inspect_pdf(path) -> Report:
    """Full read-only inspection of a PDF. Always returns a Report, never raises."""
    report = Report(path=str(path), fmt="pdf")
    try:
        report.size = os.path.getsize(path)
    except OSError as exc:
        report.err(tr("Could not get the file size: %s") % exc)

    # The raw scan works even on a file pikepdf can't open.
    ctx = {}
    try:
        _sec_raw(path, report, ctx)
    except Exception as exc:
        report.err(tr("Raw byte scan failed: %s: %s") % (type(exc).__name__, exc))

    pdf = None
    try:
        pdf = pikepdf.open(path)   # an empty password is tried automatically
    except pikepdf.PasswordError:
        report.err(tr("The PDF is encrypted and does NOT open with an empty password: content "
                      "and metadata are unavailable without the owner/user password."))
        report.signal("hazard", tr("The file is protected by a user password: inspection is "
                                   "impossible"), "high")
        return report
    except Exception as exc:
        report.err(tr("pikepdf could not open the file (%s): %s") % (type(exc).__name__, exc))
        report.signal("hazard", tr("The PDF is structurally damaged: QPDF refused to parse it"), "high")
        return report

    sections = (
        _sec_encryption,   # A
        _sec_docinfo,      # B
        _sec_xmp,          # C
        _sec_object_xmp,   # C2
        _sec_file_id,      # D
        _sec_annots,       # F
        _sec_acroform,     # G + K
        _sec_attachments,  # H
        _sec_active,       # I
        _sec_layers,       # L
        _sec_fonts,        # M
        _sec_pieceinfo,    # N
        _sec_images,       # N2
        _sec_structural,   # O
        _sec_overview,     # P
    )
    try:
        with pdf:
            for fn in sections:
                try:
                    fn(pdf, report, ctx)
                except Exception as exc:
                    report.err(tr("Section %s: %s: %s") % (fn.__name__, type(exc).__name__, exc))
            try:
                _sec_signals(pdf, report, ctx)   # R
            except Exception as exc:
                report.err(tr("Signals section: %s: %s") % (type(exc).__name__, exc))
    except Exception as exc:
        report.err(tr("Parsing interrupted: %s: %s") % (type(exc).__name__, exc))

    try:
        _enrich_exiftool(path, report, ctx)
    except Exception as exc:
        report.err(tr("exiftool enrichment failed (not critical): %s") % exc)

    return report


def _sec_encryption(pdf, report, ctx):
    """A. Encryption and permissions."""
    if not pdf.is_encrypted:
        return
    report.add(Risk.PROVENANCE, "/Encrypt", tr("Encryption"),
               tr("present, opens with an empty password"),
               note=tr("The file is encrypted, but the user password is empty: the typical "
                       "\"print/copy protection\" that protects nothing but records that the "
                       "file was prepared in Acrobat or a similar editor."),
               removable=True)
    try:
        enc = pdf.encryption
        report.add(Risk.PROVENANCE, "/Encrypt", tr("Encryption parameters"),
                   "R=%s V=%s P=%s %s" % (getattr(enc, "R", "?"), getattr(enc, "V", "?"),
                                          getattr(enc, "P", "?"), getattr(enc, "stream_method", "")),
                   note=tr("The encryption revision and algorithm indirectly date the Acrobat "
                           "version."))
    except Exception as exc:
        report.err(tr("/Encrypt parameters can't be read: %s") % exc)
    try:
        report.add(Risk.PROVENANCE, "/Encrypt", tr("Allowed operations"), str(pdf.allow),
                   note=tr("The set of restrictions (printing, text extraction, editing) is also "
                           "a fingerprint of who prepared the file and with what."))
    except Exception as exc:
        report.err(tr("/Encrypt permissions can't be read: %s") % exc)


def _sec_docinfo(pdf, report, ctx):
    """B. The /Info dictionary with all keys, including non-standard ones."""
    try:
        info = pdf.docinfo
        keys = list(info.keys())
    except Exception as exc:
        report.err(tr("/Info can't be read: %s") % exc)
        ctx["info"] = {}
        return

    collected = {}
    ctx["info"] = collected
    ctx["info_present"] = bool(keys)
    for key in keys:
        try:
            raw = _s(info[key])
        except Exception as exc:
            report.err(tr("/Info %s can't be read: %s") % (key, exc))
            continue
        collected[key] = raw
        label, risk = _INFO_MAP.get(key, (None, None))
        label = label and tr(label)
        if label is None:
            # A non-standard key: corporate DMSs, plugins and scripts write these.
            label = tr("Non-standard key %s") % key
            low = key.lower()
            if any(w in low for w in ("author", "user", "owner", "company", "operator", "signer")):
                risk = Risk.IDENTITY
            elif _looks_like_path(raw):
                risk = Risk.ENVIRONMENT
            else:
                risk = Risk.PROVENANCE
            note = (tr("A key outside the PDF specification: a specific application or corporate "
                       "document management system writes it, so it identifies the file's source "
                       "especially well."))
        elif _looks_like_path(raw):
            risk = Risk.ENVIRONMENT
            note = tr("The value looks like a local path: it reveals the user name and disk "
                      "layout.")
        else:
            note = ""

        if key in ("/CreationDate", "/ModDate"):
            human = _pdf_date(raw)
            value = tr("%s   (original: %s)") % (human, raw) if human else raw
            note = note or (tr("A date in PDF format D:YYYYMMDDHHmmSS+TZ. The time zone offset "
                               "reveals the author's geography."))
            report.add(risk, "/Info", label, value, note=note)
        else:
            report.add(risk, "/Info", label, raw, note=note)


def _sec_xmp(pdf, report, ctx):
    """C. XMP: both via pikepdf and as the raw /Metadata stream. People forget about it."""
    raw = b""
    try:
        meta = _get(pdf.Root, "/Metadata")
        if meta is not None:
            packed = len(meta.read_raw_bytes())
            if packed > MAX_XMP_PACKED:
                report.err(tr("The /Metadata stream is too large (%d bytes compressed): not "
                              "decompressed, bomb protection.") % packed)
            else:
                raw = meta.read_bytes()[:MAX_STREAM]
    except Exception as exc:
        report.err(tr("The raw /Metadata stream can't be read: %s") % exc)

    pairs = []
    if raw:
        report.add(Risk.PROVENANCE, "/Metadata", tr("XMP packet"), tr("%d bytes") % len(raw),
                   note=tr("XMP lives in a separate stream and survives cleaning of /Info: this "
                           "is most often where everything \"already deleted\" remains."))
        pairs = _parse_xmp(raw, report)
    else:
        # Fallback via pikepdf if the raw stream is unavailable.
        try:
            md = pdf.open_metadata()
            pairs = [(_qname(k), _s(v if not isinstance(v, list) else "; ".join(map(str, v))))
                     for k, v in dict(md).items()]
        except Exception as exc:
            report.err(tr("pdf.open_metadata() can't be read: %s") % exc)

    ctx["xmp_present"] = bool(pairs)
    ctx["xmp"] = {k: v for k, v in pairs}

    if not pairs:
        return

    for key, value in pairs[:MAX_XMP_KEYS]:
        risk, note = _xmp_class(key, value)
        report.add(risk, "XMP", key, value, note=note)

    if len(pairs) > MAX_XMP_KEYS:
        report.err(tr("XMP: showing the first %d of %d keys.") % (MAX_XMP_KEYS, len(pairs)))


def _xmp_class(key, value) -> tuple:
    """XMP key -> (Risk, explanation). A shared assessment for the root XMP and object XMP."""
    if key in _XMP_GUID:
        return Risk.IDENTITY, (tr("A persistent document GUID. It does NOT change on re-save and "
                                  "links all versions of the file and all files made from one "
                                  "template: through it two different-looking documents are tied "
                                  "together."))
    if key.startswith("xmpMM:History"):
        return Risk.PROVENANCE, (tr("An xmpMM:History edit log event: the actual history of what "
                                    "the file was edited with, and when."))
    if key in _XMP_IDENTITY or key.startswith(("xmpRights:", "photoshop:Author")):
        return Risk.IDENTITY, tr("Name/rights holder in XMP: survives cleaning of /Info.")
    if _looks_like_path(value):
        return Risk.ENVIRONMENT, tr("The value contains a local path: it reveals the user name "
                                    "and disk layout.")
    if key in ("xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate"):
        return Risk.PROVENANCE, (tr("An XMP date. xmp:MetadataDate is often newer than "
                                    "xmp:ModifyDate: the difference shows the metadata was "
                                    "touched separately from the content."))
    if key.startswith("pdfaid:") or key.startswith("pdfuaid:"):
        return Risk.STRUCTURAL, (tr("A PDF/A or PDF/UA conformance declaration. Usually kept "
                                    "when cleaning: without it the file stops being "
                                    "archival/accessible."))
    return Risk.PROVENANCE, ""


MAX_OBJ_XMP = 200       # objects with their own XMP that we parse
MAX_OBJ_SCAN = 200000   # file objects we scan for /Metadata


def _sec_object_xmp(pdf, report, ctx):
    """C2. XMP attached not to the document but to a page, form or image.

    Such packets come along with an image pasted from Photoshop or a page
    assembled from another PDF, and carry someone else's author, GUID and
    paths. The cleaner removes them (an object walk in clean_pdf), but the
    inspector used to miss them, so the "before" report was incomplete.
    Ordinary service keys aren't listed, to avoid drowning the report: only
    identity, GUIDs, paths and the source application."""
    try:
        root_gen = pdf.Root.objgen
    except Exception:
        root_gen = None
    holders = scanned = 0
    for obj in pdf.objects:
        scanned += 1
        if scanned > MAX_OBJ_SCAN:
            report.err(tr("More than %d objects: object XMP was not parsed across the whole file.") % MAX_OBJ_SCAN)
            break
        try:
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)) or obj.objgen == root_gen:
                continue
            meta = _get(obj, "/Metadata")
            if not isinstance(meta, pikepdf.Stream):
                continue
        except Exception:
            continue
        holders += 1
        if holders > MAX_OBJ_XMP:
            report.err(tr("More than %d objects with their own XMP: the first ones were parsed.") % MAX_OBJ_XMP)
            break
        kind = {"/Page": tr("page"), "/XObject": tr("object")}.get(_s(_get(obj, "/Type")), tr("object"))
        sub = _s(_get(obj, "/Subtype"))
        if sub == "/Image":
            kind = tr("image")
        elif sub == "/Form":
            kind = tr("form")
        where = "XMP: %s %d" % (kind, obj.objgen[0])
        try:
            packed = len(meta.read_raw_bytes())
            if packed > MAX_XMP_PACKED:
                report.err(tr("%s: stream too large (%d compressed bytes), not decompressed.") % (where, packed))
                continue
            pairs = _parse_xmp(meta.read_bytes()[:MAX_STREAM], report)
        except Exception as exc:
            report.err(tr("%s can't be read: %s") % (where, exc))
            continue
        for key, value in pairs[:MAX_XMP_KEYS]:
            risk, note = _xmp_class(key, value)
            if risk is Risk.PROVENANCE and key not in ("xmp:CreatorTool", "pdf:Producer"):
                continue
            report.add(risk, where, key, value, note=note)
        if pairs:
            report.add(Risk.PROVENANCE, where, tr("Object has its own XMP packet"),
                       tr("keys: %d") % len(pairs),
                       note=tr("Metadata of a pasted image or a page from another file. Survives "
                               "cleaning of /Info and the root XMP unless removed separately."))
    ctx["object_xmp"] = holders


def _xmp_fromstring(raw: bytes):
    """XMP -> lxml root (or None). Shared by the inspector and the cleaner.

    XMP in Latin-1/cp1252 while declaring UTF-8 is a common bug of old
    producers, and lxml won't take such a packet even with recover=True. Then
    the bad bytes are replaced with U+FFFD and parsing is retried: otherwise
    the whole packet, author and paths included, would be invisible to the
    inspector and untouched by cleaning."""
    parser = etree.XMLParser(recover=True, resolve_entities=False,
                             no_network=True, huge_tree=False)
    try:
        return etree.fromstring(raw, parser=parser)
    except etree.XMLSyntaxError:
        fixed = raw.decode("utf-8", "replace").encode("utf-8")
        if fixed == raw:
            raise
        return etree.fromstring(fixed, parser=parser)


def _parse_xmp(raw: bytes, report) -> list:
    """Parse an XMP packet into a flat list of (key, value), History included."""
    try:
        root = _xmp_fromstring(raw)
    except Exception as exc:
        report.err(tr("XMP doesn't parse as XML: %s") % exc)
        return []
    if root is None:
        report.err(tr("XMP is empty or completely unreadable."))
        return []

    out = []
    seen = set()

    def push(key, value):
        value = clip(value)
        if not key or (key, value) in seen:
            return
        seen.add((key, value))
        out.append((key, value))

    for desc in root.iter(RDF + "Description"):
        # Compact form: properties sit as attributes on rdf:Description.
        for attr, value in desc.attrib.items():
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            push(key, value)
        # Expanded form: each property is a separate child element.
        for child in desc:
            key = _qname(child.tag)
            if key == "xmpMM:History":
                _parse_history(child, push)
                continue
            push(key, _flatten(child))
        if len(out) > MAX_XMP_KEYS * 2:
            break
    return out


def _flatten(elem) -> str:
    """Value of an XMP node: text, joined rdf:li, or an unfolded structure."""
    text = (elem.text or "").strip()
    if text:
        return text
    lis = elem.findall(".//" + RDF + "li")
    if lis:
        parts = [(li.text or "").strip() for li in lis]
        parts = [p for p in parts if p]
        if parts:
            return "; ".join(parts[:20])
    bits = []
    for sub in elem.iter():
        for attr, value in sub.attrib.items():
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            bits.append("%s=%s" % (key, value))
        if sub is not elem:
            txt = (sub.text or "").strip()
            if txt:
                bits.append("%s=%s" % (_qname(sub.tag), txt))
    return "; ".join(bits[:20])


def _parse_history(node, push):
    """xmpMM:History -> one entry per event. This is the edit log."""
    events = node.findall(".//" + RDF + "li") or [node]
    for idx, ev in enumerate(events[:MAX_HISTORY]):
        fields = []
        for attr, value in ev.attrib.items():
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            fields.append("%s=%s" % (key.split(":")[-1], value))
        for sub in ev:
            txt = (sub.text or "").strip()
            if txt:
                fields.append("%s=%s" % (_qname(sub.tag).split(":")[-1], txt))
        if fields:
            push("xmpMM:History[%d]" % idx, "; ".join(fields))


def _sec_file_id(pdf, report, ctx):
    """D. trailer /ID: a pair of 16-byte file identifiers."""
    ids = _get(pdf.trailer, "/ID")
    if ids is None:
        report.add(Risk.PROVENANCE, "trailer", "/ID", tr("absent"),
                   note=tr("A trailer without /ID: either the file was built by hand, or it was "
                           "already rewritten by a tool that didn't restore /ID."))
        return
    try:
        items = list(ids)
    except Exception as exc:
        report.err(tr("trailer /ID can't be read: %s") % exc)
        return
    labels = [tr("/ID[0] (permanent, set at creation)"),
              tr("/ID[1] (changes on every save)")]
    hexes = []
    for i, item in enumerate(items[:2]):
        try:
            hx = bytes(item).hex()
        except Exception:
            hx = _s(item)
        hexes.append(hx)
        report.add(Risk.IDENTITY, "trailer", labels[i] if i < 2 else "/ID[%d]" % i, hx,
                   note=tr("Many producers derive /ID[0] from the full path and name of the "
                           "source file: the same /ID[0] in two PDFs means a common source."))
    ctx["id_equal"] = len(hexes) == 2 and hexes[0] == hexes[1]
    if ctx.get("id_equal"):
        report.add(Risk.PROVENANCE, "trailer", "/ID[0] == /ID[1]", hexes[0],
                   note=tr("Both identifiers match: the file was never re-saved after creation, "
                           "or it was built in a single pass (typical of generators)."))


def _sec_raw(path, report, ctx):
    """E. Raw byte scan: incremental updates, version, ObjStm, TeX banner.

    Works on any file, including one pikepdf can't open.
    Reads in overlapping chunks; nothing large is pulled into memory.
    """
    needles = re.compile(rb"%%EOF|startxref|/Prev|/ObjStm|/Linearized|/Encrypt|/XFA|trailer")
    banner = re.compile(rb"/PTEX\.Fullbanner\s*\(([^)]{0,400})\)")
    header = re.compile(rb"%PDF-(\d\.\d)")

    hits = {}
    banners = []
    version = ""
    offset = 0
    prev = b""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(SCAN_CHUNK)
                if not chunk:
                    break
                buf = prev + chunk
                base = offset - len(prev)
                if not version:
                    m = header.search(buf[:2048])
                    if m:
                        version = m.group(1).decode("ascii", "replace")
                for m in needles.finditer(buf):
                    hits.setdefault(m.group(0), set()).add(base + m.start())
                for m in banner.finditer(buf):
                    banners.append(m.group(1).decode("utf-8", "replace"))
                offset += len(chunk)
                prev = buf[-SCAN_OVERLAP:]
    except OSError as exc:
        report.err(tr("Raw scan: the file can't be read: %s") % exc)
        return

    n_eof = len(hits.get(b"%%EOF", ()))
    n_startxref = len(hits.get(b"startxref", ()))
    # /Prev must NOT be counted across the whole file: it's a regular key of
    # an outline item (a link to the previous bookmark at the same level), not
    # only a trailer key. On a real document with 62 bookmarks a bare counter
    # gave 62 and raised a false "previous page versions are in the file"
    # signal on EVERY PDF with an outline.
    # Only /Prev entries shortly after the trailer keyword are counted,
    # i.e. inside the trailer dictionary.
    prev_all = hits.get(b"/Prev", ())
    trailers = hits.get(b"trailer", ())
    n_prev_raw = len(prev_all)
    n_prev = sum(
        1 for p in prev_all
        if any(0 <= p - t <= 400 for t in trailers)
    )
    ctx["header_version"] = version
    ctx["objstm"] = bool(hits.get(b"/ObjStm"))
    ctx["raw_linearized"] = bool(hits.get(b"/Linearized"))

    if version:
        report.add(Risk.PROVENANCE, tr("header"), tr("PDF version (%%PDF-)"), version,
                   note=tr("The header version often disagrees with the file's features: a "
                           "specific producer sets it, and it identifies that producer."))

    report.add(Risk.PROVENANCE, tr("raw bytes"), tr("%%EOF / startxref markers"),
               "%d / %d" % (n_eof, n_startxref),
               note=tr("One %%EOF: the file was written in a single pass."))
    report.add(Risk.PROVENANCE, tr("raw bytes"), tr("/Prev occurrences"),
               tr("%d in total, %d of them in the trailer") % (n_prev_raw, n_prev),
               note=tr("/Prev is a regular outline-item key (a link to the previous bookmark), "
                       "so its occurrences across the file say NOTHING about revisions. Only "
                       "/Prev inside the trailer does."))

    # The deciding sign is the number of %%EOF/startxref. /Prev in the trailer
    # only confirms it. A single revision is not an incremental update.
    revisions = max(n_eof, n_startxref)
    if revisions > 1 or n_prev > 0:
        report.add(Risk.IDENTITY, tr("raw bytes"), tr("Incremental updates"),
                   tr("%d revisions (by %%EOF), /Prev in the trailer: %d") % (revisions, n_prev),
                   note=tr("PREVIOUS VERSIONS of the document PHYSICALLY REMAIN in the file: old "
                           "objects, old xrefs and, in particular, text under \"redacted\" black "
                           "boxes: the box is drawn on top, while the text stays in the previous "
                           "revision and can be found with an ordinary text search. Re-saving "
                           "with linearize (or a full rewrite of the objects) drops all previous "
                           "revisions; that's how to remove them."))
        report.signal("hazard",
                      tr("The PDF contains %d revisions: earlier page versions and "
                         "\"crossed-out\" text can be extracted straight from the file") % revisions,
                      "high")

    if ctx["objstm"]:
        report.add(Risk.PROVENANCE, tr("raw bytes"), "Object streams (/ObjStm)", tr("present"),
                   note=tr("Objects are packed into compressed streams (PDF 1.5+). A plain grep "
                           "won't show the metadata, but parsing extracts it completely."))

    for b in banners[:10]:
        report.add(Risk.ENVIRONMENT, "/PTEX.Fullbanner", tr("TeX banner"), b,
                   note=tr("The full TeX Live version string with the distribution's build date: "
                           "it dates the author's machine and reveals their OS/distribution."))
    if banners:
        report.signal("producer", tr("pdfTeX/LaTeX: the file has /PTEX.Fullbanner: %s") % banners[0], "high")


def _pages(pdf, report):
    """A safe page iterator with a limit."""
    try:
        total = len(pdf.pages)
    except Exception as exc:
        report.err(tr("The page list can't be read: %s") % exc)
        return
    if total > MAX_PAGES:
        report.err(tr("%d pages, the first %d were parsed page by page.") % (total, MAX_PAGES))
    for i in range(min(total, MAX_PAGES)):
        try:
            yield i, pdf.pages[i]
        except Exception as exc:
            report.err(tr("Page %d can't be read: %s") % (i + 1, exc))


def _sec_annots(pdf, report, ctx):
    """F. Annotations: /T is the name of the comment's author."""
    count = 0
    for idx, page in _pages(pdf, report):
        annots = _get(page, "/Annots")
        if annots is None:
            continue
        try:
            items = list(annots)
        except Exception as exc:
            report.err(tr("Page %d: /Annots can't be read: %s") % (idx + 1, exc))
            continue
        for a in items:
            if count >= MAX_ANNOTS:
                report.err(tr("More than %d annotations; the rest were not parsed.") % MAX_ANNOTS)
                return
            count += 1
            try:
                _one_annot(a, idx, report, ctx)
            except Exception as exc:
                report.err(tr("Page %d: an annotation can't be read: %s") % (idx + 1, exc))
    ctx["annots"] = count


def _one_annot(a, page_idx, report, ctx):
    where = tr("p. %d /Annots") % (page_idx + 1)
    sub = _s(_get(a, "/Subtype"))
    author = _s(_get(a, "/T"))
    if author:
        report.add(Risk.IDENTITY, where, tr("Annotation author %s /T") % sub, author,
                   note=tr("The name the person is logged in with in Acrobat/Reader. Usually a "
                           "real full name or a domain account, and nobody cleans it."))
    for key, label, risk, note in (
        ("/M", tr("Modification date /M"), Risk.PROVENANCE, tr("When the comment was last edited.")),
        ("/CreationDate", tr("Creation date"), Risk.PROVENANCE, ""),
        ("/Contents", tr("Comment text"), Risk.IDENTITY,
         tr("The whole comment body: working correspondence that's invisible in normal viewing.")),
        ("/RC", tr("Rich text /RC"), Risk.IDENTITY,
         tr("A duplicate of the comment in RTF/HTML: it stays even if /Contents was cleaned.")),
        ("/Subj", tr("Subject"), Risk.PROVENANCE, ""),
    ):
        raw = _s(_get(a, key))
        if not raw:
            continue
        if key in ("/M", "/CreationDate"):
            human = _pdf_date(raw)
            raw = tr("%s   (original: %s)") % (human, raw) if human else raw
        report.add(risk, where, label, raw, note=note)
    if _get(a, "/Popup") is not None:
        report.add(Risk.PROVENANCE, where, tr("Annotation popup %s") % sub, tr("present"),
                   note=tr("The comment's popup window: a separate object that drags along the "
                           "same /T and /Contents."))
    if sub == "/FileAttachment":
        fs = _get(a, "/FS")
        if fs is not None:
            _one_filespec(fs, tr("p. %d /FileAttachment") % (page_idx + 1), report)


def _sec_acroform(pdf, report, ctx):
    """G + K. Form fields, XFA and digital signatures."""
    form = _get(pdf.Root, "/AcroForm")
    if form is None:
        return
    report.add(Risk.PROVENANCE, "/AcroForm", tr("Interactive form"), tr("present"))

    if _get(form, "/NeedAppearances") is not None:
        report.add(Risk.PROVENANCE, "/AcroForm", "/NeedAppearances",
                   _s(_get(form, "/NeedAppearances")),
                   note=tr("The viewer is told to redraw fields from their values: field values "
                           "exist in the file even where it looks empty."))

    xfa = _get(form, "/XFA")
    if xfa is not None:
        report.add(Risk.IDENTITY, "/AcroForm", tr("XFA form"), tr("present"),
                   note=tr("XFA is a full XML copy of the form together with the entered data. "
                           "It lives in parallel with the regular fields: even if /V is cleaned, "
                           "the data remains in XFA."))
        report.signal("hazard", tr("The file has an XFA form: the data is duplicated in XML and "
                                   "survives cleaning of the fields"), "high")

    sigflags = _get(form, "/SigFlags")
    if sigflags is not None:
        report.add(Risk.PROVENANCE, "/AcroForm", "/SigFlags", _s(sigflags),
                   note=tr("Flag that the form contains signatures."), removable=False)
    if _get(pdf.Root, "/Perms") is not None:
        report.add(Risk.IDENTITY, "/Perms", tr("Document certification signature"), tr("present"),
                   note=tr("The document is certified. ANY metadata edit breaks the signature: "
                           "cleaning and the signature are mutually exclusive."), removable=False)

    fields = _get(form, "/Fields")
    if fields is None:
        return
    state = {"n": 0, "sig": 0}
    _walk_fields(fields, "", report, state, 0)
    ctx["sig_fields"] = state["sig"]
    if state["sig"]:
        report.signal("hazard",
                      tr("The PDF is digitally signed (%d /FT /Sig field(s)): cleaning the "
                         "metadata WILL invalidate the signature") % state["sig"],
                      "high")


def _walk_fields(fields, prefix, report, state, depth):
    if depth > 8:
        return
    try:
        items = list(fields)
    except Exception as exc:
        report.err(tr("/Fields can't be read: %s") % exc)
        return
    for f in items:
        if state["n"] >= MAX_FIELDS:
            report.err(tr("More than %d form fields; the rest were not parsed.") % MAX_FIELDS)
            return
        state["n"] += 1
        try:
            name = _s(_get(f, "/T"))
            full = "%s.%s" % (prefix, name) if prefix and name else (name or prefix)
            ftype = _s(_get(f, "/FT"))
            if ftype == "/Sig":
                if _one_signature(f, full, report):
                    state["sig"] += 1
            else:
                value = _get(f, "/V")
                if value is not None:
                    report.add(Risk.IDENTITY, "/AcroForm /Fields", tr("Field \"%s\" (%s)") % (full, ftype or "?"),
                               _s(value),
                               note=tr("A form field value. Fields visually \"cleared\" by "
                                       "erasing their appearance usually keep /V: the data stays "
                                       "in the file."))
                dv = _get(f, "/DV")
                if dv is not None:
                    report.add(Risk.PROVENANCE, "/AcroForm /Fields", tr("Default value \"%s\"") % full,
                               _s(dv))
            kids = _get(f, "/Kids")
            if kids is not None:
                _walk_fields(kids, full, report, state, depth + 1)
        except Exception as exc:
            report.err(tr("A form field can't be read: %s") % exc)


def _one_signature(field, name, report):
    """K. Signer and signature parameters. Can't be removed without breaking the signature.

    Returns True if the field is REALLY signed (has /V): only such fields count
    toward the warning signal and get removable=False. An empty placeholder
    field has nothing to break: it can be removed like a regular form field.
    """
    v = _get(field, "/V")
    if v is None:
        report.add(Risk.PROVENANCE, "/AcroForm /Sig", tr("Signature field \"%s\"") % name,
                   tr("empty (not signed)"),
                   note=tr("A prepared but unsigned field: there's no signature yet, nothing to "
                           "break; it can be removed like a regular form field."), removable=True)
        return False
    note = (tr("A digital signature. Signature metadata is inside the signed range: any cleaning "
               "of the file breaks the signature. Cleaning and keeping the signature are "
               "mutually exclusive; the user has to choose."))
    report.add(Risk.IDENTITY, "/AcroForm /Sig", tr("Signature field \"%s\"") % name, tr("present"),
               note=note, removable=False)
    for key, label, risk in (
        ("/Name", tr("Signer"), Risk.IDENTITY),
        ("/M", tr("Signing time"), Risk.PROVENANCE),
        ("/Reason", tr("Signing reason"), Risk.IDENTITY),
        ("/Location", tr("Signing location"), Risk.ENVIRONMENT),
        ("/ContactInfo", tr("Signer contact"), Risk.IDENTITY),
        ("/Filter", tr("Signature handler"), Risk.PROVENANCE),
        ("/SubFilter", tr("Signature format"), Risk.PROVENANCE),
    ):
        raw = _s(_get(v, key))
        if not raw:
            continue
        if key == "/M":
            human = _pdf_date(raw)
            raw = tr("%s   (original: %s)") % (human, raw) if human else raw
        report.add(risk, "/AcroForm /Sig", "%s («%s»)" % (label, name), raw,
                   note=note, removable=False)
    br = _get(v, "/ByteRange")
    if br is not None:
        report.add(Risk.PROVENANCE, "/AcroForm /Sig", "/ByteRange («%s»)" % name, _s(br),
                   note=tr("Byte range covered by the signature. Nothing inside it can be "
                           "changed. ")
                        + note, removable=False)
    return True


def _sec_attachments(pdf, report, ctx):
    """H. Embedded files: PDF/A-3 and e-invoices carry the whole source inside."""
    names = _get(pdf.Root, "/Names")
    if names is None:
        return
    tree = _get(names, "/EmbeddedFiles")
    if tree is None:
        return
    found = []
    _walk_name_tree(tree, found, report, 0)
    for label, spec in found[:MAX_ATTACH]:
        try:
            _one_filespec(spec, "/Names /EmbeddedFiles", report, label)
        except Exception as exc:
            report.err(tr("Attachment %s can't be read: %s") % (label, exc))
    if found:
        report.signal("hazard",
                      tr("Files embedded in the PDF: %d. An attachment is a separate document "
                         "with its own metadata; PDF inspection doesn't open its metadata") % len(found),
                      "high")
    ctx["attachments"] = len(found)


def _walk_name_tree(node, out, report, depth, _seen=None, _budget=None):
    """Walk a name tree (/EmbeddedFiles, /JavaScript). Depth is bounded, but
    that's not enough: /Kids can reference THE SAME indirect object many times
    (shared references instead of separate nodes), and without protection the
    walk blows up exponentially (branching^depth calls) on a file of a few
    objects: the classic DAG bomb. So on top of depth: an already visited
    object isn't entered again (_seen by objgen), and the total number of
    calls is hard-capped (_budget), not just the number of entries found."""
    if _seen is None:
        _seen = set()
    if _budget is None:
        _budget = [4000]
    if depth > 8 or len(out) >= MAX_ATTACH or _budget[0] <= 0:
        return
    _budget[0] -= 1
    try:
        key = node.objgen
    except Exception:
        key = None
    if key and key != (0, 0):
        if key in _seen:
            return
        _seen.add(key)
    try:
        arr = _get(node, "/Names")
        if arr is not None:
            items = list(arr)
            for i in range(0, len(items) - 1, 2):
                out.append((_s(items[i]), items[i + 1]))
        kids = _get(node, "/Kids")
        if kids is not None:
            for kid in list(kids)[:64]:
                _walk_name_tree(kid, out, report, depth + 1, _seen, _budget)
    except Exception as exc:
        report.err(tr("The attachments name tree can't be read: %s") % exc)


def _one_filespec(spec, where, report, label=""):
    fname = _s(_get(spec, "/UF")) or _s(_get(spec, "/F")) or label
    report.add(Risk.IDENTITY, where, tr("Embedded file"), fname,
               note=tr("Attachment name. The attachment itself is a full document with its own "
                       "metadata (PDF/A-3 and e-invoices carry the whole source XML/xlsx inside)."))
    desc = _s(_get(spec, "/Desc"))
    if desc:
        report.add(Risk.PROVENANCE, where, tr("Attachment description \"%s\"") % fname, desc)
    ef = _get(spec, "/EF")
    if ef is None:
        return
    stream = _get(ef, "/F") or _get(ef, "/UF")
    if stream is None:
        return
    params = _get(stream, "/Params")
    if params is None:
        return
    for key, lbl, risk in (("/Size", tr("Attachment size"), Risk.PROVENANCE),
                           ("/CreationDate", tr("Attachment creation date"), Risk.PROVENANCE),
                           ("/ModDate", tr("Attachment modification date"), Risk.PROVENANCE),
                           ("/CheckSum", tr("Attachment checksum"), Risk.PROVENANCE)):
        raw = _s(_get(params, key))
        if not raw:
            continue
        if key.endswith("Date"):
            human = _pdf_date(raw)
            raw = tr("%s   (original: %s)") % (human, raw) if human else raw
        report.add(risk, where, "%s «%s»" % (lbl, fname), raw)


def _sec_active(pdf, report, ctx):
    """I. Active content: JS, auto-actions, launching programs, external links."""
    hazards = 0
    names = _get(pdf.Root, "/Names")
    if names is not None and _get(names, "/JavaScript") is not None:
        js = []
        _walk_name_tree(_get(names, "/JavaScript"), js, report, 0)
        for label, entry in js[:50]:
            body = _get(entry, "/JS")
            report.add(Risk.ENVIRONMENT, "/Names /JavaScript", tr("Document-level script \"%s\"") % label,
                       _stream_text(body) or tr("present"),
                       note=tr("JavaScript runs when the file is opened. The code often contains "
                               "field names, server addresses and internal system identifiers."))
            hazards += 1
        if js:
            report.signal("hazard", tr("The document has %d document-level JavaScript script(s)") % len(js), "high")

    if _get(pdf.Root, "/OpenAction") is not None:
        oa = _get(pdf.Root, "/OpenAction")
        report.add(Risk.ENVIRONMENT, "/OpenAction", tr("Open action"), _s(oa)[:400],
                   note=tr("What the file does by itself as soon as it's opened."))
        hazards += 1
    if _get(pdf.Root, "/AA") is not None:
        report.add(Risk.ENVIRONMENT, "/AA", tr("Additional document actions"), _s(_get(pdf.Root, "/AA"))[:400],
                   note=tr("Document event handlers (print, save, close)."))
        hazards += 1

    for idx, page in _pages(pdf, report):
        try:
            if _get(page, "/AA") is not None:
                report.add(Risk.ENVIRONMENT, tr("p. %d /AA") % (idx + 1), tr("Page actions"),
                           _s(_get(page, "/AA"))[:400],
                           note=tr("Scripts that fire when a page is opened/closed."))
                hazards += 1
            annots = _get(page, "/Annots")
            if annots is None:
                continue
            for a in list(annots)[:100]:
                act = _get(a, "/A")
                if act is None:
                    continue
                stype = _s(_get(act, "/S"))
                if stype == "/Launch":
                    report.add(Risk.ENVIRONMENT, tr("p. %d /Annots /A") % (idx + 1),
                               tr("Launch external program /Launch"), _s(_get(act, "/F")) or _s(act)[:300],
                               note=tr("The annotation tries to launch a program or open a file "
                                       "by a local path: the path reveals the author's machine."))
                    hazards += 1
                elif stype == "/URI":
                    uri = _s(_get(act, "/URI"))
                    risk = Risk.ENVIRONMENT if _looks_like_path(uri) else Risk.PROVENANCE
                    report.add(risk, tr("p. %d /Annots /A") % (idx + 1), tr("/URI link"), uri,
                               note=tr("An external link. file:// and UNC paths reveal the "
                                       "internal network; tracking links reveal corporate "
                                       "systems."))
                elif stype in ("/JavaScript", "/SubmitForm", "/ImportData"):
                    report.add(Risk.ENVIRONMENT, tr("p. %d /Annots /A") % (idx + 1),
                               tr("Action %s") % stype, _s(act)[:300],
                               note=tr("An active action in an annotation: submitting data or "
                                       "executing code."))
                    hazards += 1
        except Exception as exc:
            report.err(tr("Page %d: active content can't be read: %s") % (idx + 1, exc))

    if hazards:
        report.signal("hazard", tr("Active content in the PDF: %d object(s) "
                                   "(JS/OpenAction/AA/Launch)") % hazards,
                      "high")


def _sec_layers(pdf, report, ctx):
    """L. Hidden layers: a layer that's turned off may contain invisible text."""
    ocp = _get(pdf.Root, "/OCProperties")
    if ocp is None:
        return
    groups = {}
    try:
        for g in list(_get(ocp, "/OCGs") or [])[:MAX_OCGS]:
            try:
                groups[g.objgen] = _s(_get(g, "/Name"))
            except Exception:
                pass
    except Exception as exc:
        report.err(tr("/OCProperties /OCGs can't be read: %s") % exc)
    for key, name in groups.items():
        report.add(Risk.PROVENANCE, "/OCProperties", tr("Layer (optional content)"), name or tr("unnamed"),
                   note=tr("A named layer. Layer names often reveal internal version names, "
                           "clients and drafts (\"Draft\", \"Client A\", \"Lawyer's edits\")."))
    d = _get(ocp, "/D")
    if d is None:
        return
    try:
        off = list(_get(d, "/OFF") or [])
    except Exception as exc:
        report.err(tr("/OCProperties /D /OFF can't be read: %s") % exc)
        return
    for g in off[:MAX_OCGS]:
        try:
            name = groups.get(g.objgen) or _s(_get(g, "/Name"))
        except Exception:
            name = tr("unnamed")
        report.add(Risk.IDENTITY, "/OCProperties /D /OFF", tr("Layer TURNED OFF"), name or tr("unnamed"),
                   note=tr("The layer is off by default: its content IS in the file but isn't "
                           "displayed. That's how drafts, comments and service notes get hidden; "
                           "one click in any viewer turns it on."))
    if off:
        report.signal("hazard", tr("The PDF has %d layers turned off: the hidden content is one "
                                   "click away") % len(off),
                      "high")


MAX_RES_NODES = 3000   # /Resources dictionaries we walk recursively (for the whole file)
MAX_RES_DEPTH = 12     # Form XObject nesting inside Form XObject


def _page_resources(page, report, idx, budget, seen):
    """All /Resources dictionaries of a page: its own, those of nested Form
    XObjects and tiling /Patterns, and annotation appearances (/AP). A font used
    only in a form or a stamp isn't declared on the page, so without a deep
    walk the font list is incomplete by construction.

    budget is [remaining nodes] for the whole file, seen holds already visited
    objects (shared forms appear on every page, and a cycle in a broken file
    must not loop the parser)."""
    stack = [(_get(page, "/Resources"), 0)]
    try:
        for annot in list(_get(page, "/Annots") or [])[:MAX_ANNOTS]:
            ap = _get(annot, "/AP")
            if ap is None:
                continue
            for state in ("/N", "/R", "/D"):
                view = _get(ap, state)
                if view is None:
                    continue
                if isinstance(view, pikepdf.Stream):
                    stack.append((_get(view, "/Resources"), 1))
                elif isinstance(view, pikepdf.Dictionary):
                    for k in list(view.keys())[:20]:
                        stack.append((_get(view[k], "/Resources"), 1))
    except Exception as exc:
        report.err(tr("Page %d: annotation appearance (/AP) can't be read: %s") % (idx + 1, exc))
    while stack and budget[0] > 0:
        res, depth = stack.pop()
        if res is None or depth > MAX_RES_DEPTH:
            continue
        # A cycle is only possible through indirect references, so only those are
        # remembered. id() of direct objects won't do: pikepdf creates a wrapper on
        # every access, and a freed wrapper's id goes to the next one.
        try:
            key = res.objgen if res.is_indirect else None
        except Exception:
            key = None
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        budget[0] -= 1
        yield res
        for sub in ("/XObject", "/Pattern"):
            try:
                group = _get(res, sub)
                if group is None:
                    continue
                for k in list(group.keys())[:200]:
                    obj = group[k]
                    if sub == "/XObject" and _s(_get(obj, "/Subtype")) != "/Form":
                        continue  # images carry no resources
                    stack.append((_get(obj, "/Resources"), depth + 1))
            except Exception as exc:
                report.err(tr("Page %d: %s can't be read: %s") % (idx + 1, sub, exc))


def _sec_fonts(pdf, report, ctx):
    """M. Fonts: subsets and typefaces that date the document. Includes fonts of
    nested forms, patterns and annotation appearances."""
    seen = {}
    budget, visited = [MAX_RES_NODES], set()
    for idx, page in _pages(pdf, report):
        try:
            for res in _page_resources(page, report, idx, budget, visited):
                fonts = _get(res, "/Font")
                if fonts is None:
                    continue
                for fkey in list(fonts.keys())[:100]:
                    if len(seen) >= MAX_FONTS:
                        break
                    base = _s(_get(fonts[fkey], "/BaseFont"))
                    if base:
                        seen.setdefault(base, idx + 1)
        except Exception as exc:
            report.err(tr("Page %d: /Resources /Font can't be read: %s") % (idx + 1, exc))
    if budget[0] <= 0:
        report.err(tr("More than %d resources: not all fonts of nested forms were parsed.") % MAX_RES_NODES)
    ctx["fonts"] = set(seen)
    if not seen:
        return
    subsets = []
    for base in sorted(seen):
        clean = base.lstrip("/")
        m = _SUBSET.match(clean)
        if m:
            subsets.append(m.group(1))
            report.add(Risk.PROVENANCE, "/Resources /Font", tr("Font (subset)"), clean,
                       note=tr("The %s prefix tags a glyph subset. Prefixes are generated "
                               "deterministically by a specific producer and match across files "
                               "built by the same installation.") % m.group(1))
        else:
            report.add(Risk.PROVENANCE, "/Resources /Font", tr("Font"), clean)
        low = clean.split("+")[-1].lower()
        for marker, note in _FONT_ERA:
            if low.startswith(marker):
                note = tr(note)
                report.add(Risk.PROVENANCE, "/Resources /Font", tr("Font that dates the document"), clean,
                           note=note)
                # kind="dating", NOT "producer": a font dates the document but
                # isn't the application that made it. With "producer" the summary
                # said "PDF, made with Calibri", which is nonsense.
                report.signal("dating", note, "medium")
                ctx.setdefault("era_fonts", []).append((marker, clean))
                break
    if subsets:
        report.add(Risk.PROVENANCE, "/Resources /Font", tr("Subset prefixes"),
                   ", ".join(sorted(set(subsets))[:40]),
                   note=tr("The set of subset prefixes is a stable fingerprint of a \"producer + "
                           "font set\" pair: a match between two files points to a common source."))


def _sec_pieceinfo(pdf, report, ctx):
    """N. /PieceInfo: an application's private data, sometimes with the source inside."""
    def dump(holder, where):
        pi = _get(holder, "/PieceInfo")
        if pi is None:
            return
        try:
            apps = list(pi.keys())
        except Exception as exc:
            report.err(tr("%s /PieceInfo can't be read: %s") % (where, exc))
            return
        for app in apps[:50]:
            entry = _get(pi, app)
            last = _s(_get(entry, "/LastModified")) if entry is not None else ""
            human = _pdf_date(last)
            value = app.lstrip("/")
            if last:
                value += "  (LastModified: %s)" % (human or last)
            report.add(Risk.PROVENANCE, "%s /PieceInfo" % where, tr("Application private data"), value,
                       note=tr("The application (Acrobat, Distiller, Illustrator, InDesign) "
                               "stores its service data here, up to an editable source of the "
                               "document. The key's presence unambiguously names the application "
                               "the file was edited with."))

    dump(pdf.Root, "/Root")
    for idx, page in _pages(pdf, report):
        try:
            dump(page, tr("p. %d") % (idx + 1))
        except Exception as exc:
            report.err(tr("Page %d: /PieceInfo can't be read: %s") % (idx + 1, exc))


def is_jpeg_image(obj) -> bool:
    """An image whose stream is a whole JPEG file (a single /DCTDecode filter)."""
    if not isinstance(obj, pikepdf.Stream) or _s(_get(obj, "/Subtype")) != "/Image":
        return False
    flt = _get(obj, "/Filter")
    if isinstance(flt, pikepdf.Array):
        return len(flt) == 1 and _s(flt[0]) == "/DCTDecode"
    return _s(flt) == "/DCTDecode"


def _sec_images(pdf, report, ctx):
    """N2. JPEG images inside a PDF: a /DCTDecode stream is a JPEG file byte for
    byte, with all its EXIF (GPS, camera serial, capture date). img2pdf and
    scanner apps embed photos without re-encoding. Document metadata has
    nothing to do with it."""
    from .insp_image import _jpeg, _signals   # JPEG parsing from the image inspector
    n = 0
    for obj in pdf.objects:
        if not is_jpeg_image(obj):
            continue
        n += 1
        if n > MAX_IMAGES:
            report.err(tr("More than %d JPEG images; the rest were not parsed.") % MAX_IMAGES)
            return
        try:
            data = obj.read_raw_bytes()
            if len(data) > MAX_STREAM:
                report.err(tr("Image (object %d) is larger than %d MB: not parsed.") % (obj.objgen[0], MAX_STREAM >> 20))
                continue
            sub, sctx = Report(path="", fmt="pdf-image"), {}
            _jpeg(sub, data, sctx)
            _signals(sub, sctx)
        except Exception as exc:
            report.err(tr("Image (object %d): %s: %s") % (obj.objgen[0], type(exc).__name__, exc))
            continue
        where = tr("image (object %d) ") % obj.objgen[0]
        for f in sub.findings:
            if f.risk is not Risk.STRUCTURAL:   # segment map, JFIF, ICC: noise for a PDF
                report.add(f.risk, where + f.location, f.label, f.value, f.note, f.removable)
        for s in sub.signals:
            report.signal(s.kind, tr("image inside the PDF: ") + s.detail, s.confidence)


def _sec_structural(pdf, report, ctx):
    """O. Structural: usually KEPT when cleaning."""
    root = pdf.Root
    checks = (
        ("/StructTreeRoot", tr("Structure tree (tagged PDF)"),
         tr("Accessibility tags: without them a screen reader can't read the document. Kept when "
            "cleaning.")),
        ("/MarkInfo", tr("/MarkInfo (tagging declaration)"),
         tr("Declares that the PDF is tagged. Kept together with /StructTreeRoot.")),
        ("/Outlines", tr("Outline (bookmarks)"),
         tr("Document navigation. Kept, but section headings by themselves may reveal the "
            "internal structure of a project.")),
        ("/PageLabels", tr("Page numbering (/PageLabels)"),
         tr("Custom numbering (i, ii, 1, 2...). Kept.")),
        ("/ViewerPreferences", tr("Viewer preferences"),
         tr("How to open the document. Harmless, kept.")),
        ("/OutputIntents", tr("Color profile (/OutputIntents, ICC)"),
         tr("Print ICC profile. Kept: without it color rendering breaks. The profile name "
            "sometimes names the print shop, though.")),
    )
    has_struct = False
    for key, label, note in checks:
        if label is None:
            continue
        if _get(root, key) is not None:
            if key == "/StructTreeRoot":
                has_struct = True
            report.add(Risk.STRUCTURAL, "/Root", label, tr("present"), note=note)
    ctx["struct_tree"] = has_struct

    lang = _s(_get(root, "/Lang"))
    if lang:
        report.add(Risk.STRUCTURAL, "/Root", tr("Document language /Lang"), lang,
                   note=tr("Screen readers and hyphenation need the language, so it's kept. But "
                           "by itself it reveals the language settings of the author's machine "
                           "(ru-RU, en-GB)."))

    try:
        page = pdf.pages[0]
        box = _get(page, "/MediaBox")
        if box is not None:
            pts = [float(v) for v in list(box)]
            w = abs(pts[2] - pts[0]) * 25.4 / 72.0
            h = abs(pts[3] - pts[1]) * 25.4 / 72.0
            name = ""
            for pw, ph, pname in _PAPER:
                if (abs(w - pw) < 3 and abs(h - ph) < 3) or (abs(w - ph) < 3 and abs(h - pw) < 3):
                    name = pname
                    break
            report.add(Risk.STRUCTURAL, tr("p. 1 /MediaBox"), tr("Page size"),
                       tr("%.1f x %.1f mm%s") % (w, h, "  -> %s" % tr(name) if name else ""),
                       note=tr("Paper size gives away the country: Letter means US/Canada, A4 "
                               "everywhere else. This works even if every date and name has been "
                               "removed."))
            if "US" in name:
                # Paper size is geolocation, not a producer.
                report.signal("dating", tr("Paper size %s: the document was prepared for the "
                                           "North American standard") % tr(name), "medium")
    except Exception as exc:
        report.err(tr("/MediaBox of the first page can't be read: %s") % exc)


def _sec_overview(pdf, report, ctx):
    """P. Overall file structure."""
    try:
        npages = len(pdf.pages)
    except Exception:
        npages = -1
    try:
        nobj = len(pdf.objects)
    except Exception:
        nobj = -1
    lin = False
    try:
        lin = bool(pdf.is_linearized)
    except Exception:
        lin = ctx.get("raw_linearized", False)
    ctx["linearized"] = lin
    report.add(Risk.PROVENANCE, tr("structure"), tr("Pages / objects"),
               "%d / %d" % (npages, nobj),
               note=tr("The number of objects relative to pages is a rough producer fingerprint: "
                       "generators produce predictably few objects per page."))
    report.add(Risk.PROVENANCE, tr("structure"), tr("Linearized (fast web view)"),
               tr("yes") if lin else tr("no"),
               note=tr("Linearization is a sign of saving through Acrobat or an explicit qpdf "
                       "--linearize. It also drops all incremental revisions."))
    try:
        report.add(Risk.PROVENANCE, tr("structure"), tr("PDF version (as parsed)"), str(pdf.pdf_version),
                   note=tr("The version QPDF sees. A mismatch with the %%PDF- header means the "
                           "file was rewritten."))
        if ctx.get("header_version") and str(pdf.pdf_version) != ctx["header_version"]:
            report.signal("inconsistent",
                          tr("The header version (%s) doesn't match the parsed version (%s): the "
                             "file was rewritten by a third-party tool") % (ctx["header_version"], pdf.pdf_version),
                          "medium")
    except Exception as exc:
        report.err(tr("PDF version can't be read: %s") % exc)


def _sec_signals(pdf, report, ctx):
    """R. Signals: what produced the file and where the metadata contradicts itself."""
    info = ctx.get("info", {})
    xmp = ctx.get("xmp", {})
    creator = info.get("/Creator", "") or xmp.get("xmp:CreatorTool", "")
    producer = info.get("/Producer", "") or xmp.get("pdf:Producer", "")
    blob = "%s | %s" % (creator, producer)

    named = []
    for rx, human, is_lib in _TOOL_MARKS:
        m = rx.search(blob)
        if not m:
            continue
        detail = tr(human)
        if "Skia" in rx.pattern:
            ms = re.search(r"Skia/PDF\s*m(\d+)", blob, re.I)
            if ms:
                detail += tr(" -- Chrome milestone %s") % ms.group(1)
        named.append((detail, is_lib))
        report.signal("producer", "%s (Creator=%r, Producer=%r)" % (detail, creator, producer), "high")
        if is_lib:
            report.signal("ai", tr("The file was generated programmatically, not \"printed\" by "
                                   "a person from an editor: %s. For a document presented as an "
                                   "office one, that alone is an anomaly.") % detail, "high")

    if not named and (creator or producer):
        report.signal("producer", tr("Producer not recognized by the fingerprint database: "
                                     "Creator=%r, Producer=%r")
                      % (creator, producer), "low")
    if not creator and not producer:
        report.signal("scrubbed", tr("Neither /Creator nor /Producer is set: no known producer "
                                     "does that, so the file has almost certainly been scrubbed "
                                     "already"), "high")

    # Post-processing on top of an office application.
    if producer and _REWRITER.search(producer) and creator and _AUTHORING.search(creator):
        report.signal("scrubbed",
                      tr("Creator claims an authoring application (%r), while Producer is a "
                         "service rewriter (%r): the file has already been post-processed") % (creator, producer),
                      "high")

    # Word without XMP and without tags: that's not how Word exports.
    word = bool(re.search(r"Microsoft|Word", creator or "", re.I))
    if word and not ctx.get("xmp_present"):
        report.signal("scrubbed",
                      tr("Creator = Microsoft Word, but there's no XMP in the file. A Word "
                         "export always writes an XMP packet, so the metadata has already been "
                         "scrubbed"), "high")
    if word and not ctx.get("struct_tree"):
        report.signal("scrubbed",
                      tr("Creator = Microsoft Word, but there's no /StructTreeRoot. Word always "
                         "tags an exported PDF, so the file was rewritten by a third-party tool"), "medium")

    # /Info and XMP should exist together.
    if ctx.get("xmp_present") and not ctx.get("info_present"):
        report.signal("scrubbed", tr("/Info is empty but XMP is in place: only the /Info "
                                     "dictionary was cleaned and XMP was forgotten; everything "
                                     "is still there"), "high")
    if ctx.get("info_present") and not ctx.get("xmp_present"):
        report.signal("inconsistent", tr("/Info is filled in but XMP is missing: either a very "
                                         "old producer, or XMP was cut out separately"), "medium")

    # Dates.
    c_date = info.get("/CreationDate", "")
    m_date = info.get("/ModDate", "")
    if c_date and m_date:
        if c_date == m_date:
            report.signal("inconsistent",
                          tr("CreationDate == ModDate (%s): the file wasn't edited after "
                             "creation; either it's one-off machine generation, or the dates "
                             "were rewritten wholesale")
                          % (_pdf_date(c_date) or c_date), "low")
        elif m_date < c_date:
            report.signal("inconsistent",
                          tr("ModDate (%s) is EARLIER than CreationDate (%s): the dates were "
                             "edited by hand")
                          % (_pdf_date(m_date) or m_date, _pdf_date(c_date) or c_date), "high")
    x_create = xmp.get("xmp:CreateDate", "")
    if c_date and x_create and _pdf_date(c_date)[:10] and x_create[:10] != _pdf_date(c_date)[:10]:
        report.signal("inconsistent",
                      tr("The creation date in /Info (%s) doesn't match xmp:CreateDate (%s): one "
                         "of the sources was edited") % (_pdf_date(c_date), x_create), "high")

    # Font vs. declared date: Aptos physically couldn't have gotten into a
    # document before 2023, however old the file claims to be.
    year = ""
    m_year = re.match(r"D?:?(\d{4})", c_date or "")
    if m_year:
        year = m_year.group(1)
    markers = {m for m, _name in ctx.get("era_fonts", [])}
    if any(m.startswith("aptos") for m in markers) and "calibri" in markers:
        report.signal(
            "inconsistent",
            tr("The file has both Calibri and Aptos. Calibri belongs to 2007–2023, Aptos to 2023 "
               "or later. Together: the document was edited after Aptos appeared, on top of an "
               "older base."),
            "medium")
    for marker, name in ctx.get("era_fonts", []):
        if marker.startswith("aptos") and year and year < "2023":
            report.signal("inconsistent",
                          tr("The declared creation date %s contradicts the font %s: Aptos only "
                             "appeared in 2023, so the date was rewritten") % (year, name), "high")
        if marker == "calibri" and year and year < "2007":
            report.signal("inconsistent",
                          tr("The declared creation date %s contradicts the font %s: Calibri "
                             "only appeared in 2007, so the date was rewritten") % (year, name), "high")

    # The author is present in one place but not the other.
    if info.get("/Author") and not xmp.get("dc:creator"):
        report.signal("inconsistent", tr("The author is in /Info, but dc:creator in XMP is empty"), "low")
    if xmp.get("dc:creator") and not info.get("/Author"):
        report.signal("scrubbed", tr("dc:creator in XMP is set (%s), while /Author in /Info is "
                                     "cleaned out: a classic half-done scrub") % xmp["dc:creator"], "high")


def _enrich_exiftool(path, report, ctx):
    """Optional enrichment. No exiftool: quietly move on."""
    exe = shutil.which("exiftool")
    if not exe:
        local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "tools", "exiftool.exe")
        exe = local if os.path.isfile(local) else None
    if not exe:
        return
    try:
        proc = subprocess.run([exe, "-json", "-a", "-G1", "-s", "-n", str(path)],
                              capture_output=True, timeout=30, shell=False)
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except Exception as exc:
        report.err(tr("exiftool failed (not critical): %s") % exc)
        return
    if not data:
        return
    known = {k.split(":")[-1].lower() for k in ctx.get("xmp", {})}
    known |= {k.lstrip("/").lower() for k in ctx.get("info", {})}
    added = 0
    for group_key, value in data[0].items():
        if added >= 120:
            break
        tag = group_key.split(":")[-1]
        if tag in ("SourceFile", "ExifToolVersion", "FileName", "Directory",
                   "FileSize", "FileModifyDate", "FileAccessDate", "FileInodeChangeDate",
                   "FilePermissions", "FileType", "FileTypeExtension", "MIMEType"):
            continue
        if tag.lower() in known:
            continue
        added += 1
        report.add_nonempty(Risk.PROVENANCE, "exiftool", tag, _s(value),
                            note=tr("Additionally found by exiftool (optional enrichment)."))
    if added:
        report.signal("producer", tr("exiftool added %d fields not covered by pikepdf parsing") % added, "low")
