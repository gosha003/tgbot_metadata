# -*- coding: utf-8 -*-
"""OOXML package inspector: .docx / .xlsx / .pptx.

Phase 0: read only. The file is opened strictly for reading, nothing is
extracted to disk, and the input file is never modified.

An OOXML container is an ordinary ZIP, and metadata lives not only in
docProps/*.xml: the ZIP itself leaks (entry timestamps), and so do Word
settings (rsid, template path), revisions with author names, presenceInfo
with real emails, the printer DEVMODE, Excel connection strings and
Purview labels with the tenant GUID. So we parse the package as a
container, not as a "document": the standard libraries (python-docx,
openpyxl) drop parts of the package and, with them, half of the leaks.

Public API:
    inspect_ooxml(path, fmt="docx") -> Report
    parse_props(zf) -> dict            # raw core/app/custom values for phase 2

Self-test: PYTHONIOENCODING=utf-8 python -m core.insp_ooxml
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import zipfile

from lxml import etree

try:
    from .model import Report, Risk
except ImportError:  # running the file directly, outside the package
    from model import Report, Risk
from core.i18n import N_, tr


# --- limits that protect against zip bombs and plain huge files ----------

MAX_PART_READ = 32 * 1024 * 1024      # most we read into memory at once
MAX_STREAM = 50 * 1024 * 1024         # ceiling for the streaming parse of document.xml
MAX_TOTAL_UNPACKED = 512 * 1024 * 1024
MAX_RATIO = 120                       # overall compression ratio of the package
# A high compression ratio is not a bomb by itself: the repetitive XML of a
# large report compresses hundreds of times and is harmless. Only the sum
# "many bytes AND a huge ratio" is dangerous, which is why the ratio has a
# threshold.
MIN_BOMB_UNPACKED = 64 * 1024 * 1024
MAX_PARTS_SCAN = 4000                 # how many ZIP entries we process at all
MAX_ELEMENTS = 400_000                # ceiling on elements during iterparse
MAX_LISTED = 40                       # how many findings of one kind we list

WORD_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)   # what Word stamps on every entry

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PATH_RE = re.compile(
    r"^\s*(file:///|[A-Za-z]:[\\/]|\\\\[^\\]+\\|/Users/|/home/|/Volumes/)"
)
# A path INSIDE a value, not only at its start: document variables and EDMS
# properties usually look like "docVar(name=Database; val=\\FS01\buh\baza.accdb)".
# (?<![A-Za-z]) keeps "https://" from being mistaken for the drive "s:/".
_PATH_ANY_RE = re.compile(
    r"(file:///|(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|/Users/|/home/|/Volumes/)"
)

# Names of properties that hold a person. Without this list, full names from
# EDMS (electronic document management system) properties such as "Executor"
# or "Signer" would end up in PROVENANCE. The pattern also matches the
# Russian equivalents that Russian EDMS products write.
_PERSON_NAME_RE = re.compile(
    r"(author|owner|creator|manager|savedby|lasteditedby|employee|signer"
    r"|автор|исполнител|ответствен|подписант|согласов|утвердил|разработал"
    r"|руководител|сотрудник|фио|контакт)", re.I)

# Parts that Word is required to put into a .docx. Their absence is a sign
# that the file was built by a library, not by Word, even if app.xml claims
# otherwise.
WORD_REQUIRED = [
    "word/styles.xml",
    "word/settings.xml",
    "word/fontTable.xml",
    "word/webSettings.xml",
    "word/theme/theme1.xml",
]

# docProps/core.xml fields: local name -> (risk, label, explanation).
CORE_FIELDS = [
    ("creator", Risk.IDENTITY, N_("dc:creator — author"),
     N_("The user name or login of whoever created the document.")),
    ("lastModifiedBy", Risk.IDENTITY, N_("cp:lastModifiedBy — last saved by"),
     N_("Often differs from the author and reveals a second person in the chain.")),
    ("created", Risk.PROVENANCE, N_("dcterms:created — created"), ""),
    ("modified", Risk.PROVENANCE, N_("dcterms:modified — modified"), ""),
    ("revision", Risk.PROVENANCE, N_("cp:revision — revision number"),
     N_("How many times the document was saved.")),
    ("lastPrinted", Risk.PROVENANCE, N_("cp:lastPrinted — last printed"),
     N_("The fact of printing and its exact time.")),
    ("title", Risk.PROVENANCE, N_("dc:title — title"),
     N_("Often holds the original file name or a project name.")),
    ("subject", Risk.PROVENANCE, N_("dc:subject — subject"), ""),
    ("description", Risk.PROVENANCE, N_("dc:description — description"), ""),
    ("keywords", Risk.PROVENANCE, N_("cp:keywords — keywords"), ""),
    ("category", Risk.PROVENANCE, N_("cp:category — category"), ""),
    ("contentStatus", Risk.PROVENANCE, N_("cp:contentStatus — status"),
     N_("Draft / under review: reveals an internal process.")),
    ("identifier", Risk.PROVENANCE, N_("dc:identifier — identifier"),
     N_("An internal document ID from an EDMS, or a GUID.")),
    ("language", Risk.STRUCTURAL, N_("dc:language — language"),
     N_("Usually kept: it affects spell-checking and accessibility.")),
]

# docProps/app.xml fields.
APP_FIELDS = {
    "Application": (Risk.PROVENANCE, N_("Application — application"),
                    N_("Which program exactly saved the file.")),
    "AppVersion": (Risk.PROVENANCE, N_("AppVersion — application version"),
                   N_("Narrows it down to a specific Office build.")),
    "Company": (Risk.IDENTITY, N_("Company — organization"),
                N_("The company name from the Office settings.")),
    "Manager": (Risk.IDENTITY, N_("Manager — manager"),
                N_("The manager's full name from the document properties.")),
    "TotalTime": (Risk.PROVENANCE, N_("TotalTime — editing time, min"),
                  N_("How long people really worked on the document. A strong leak: it reveals "
                     "both the effort and that the document is not a template.")),
    "Pages": (Risk.PROVENANCE, N_("Pages — pages"), ""),
    "Words": (Risk.PROVENANCE, N_("Words — words"), ""),
    "Characters": (Risk.PROVENANCE, N_("Characters — characters"), ""),
    "CharactersWithSpaces": (Risk.PROVENANCE, N_("CharactersWithSpaces — characters with spaces"), ""),
    "Lines": (Risk.PROVENANCE, N_("Lines — lines"), ""),
    "Paragraphs": (Risk.PROVENANCE, N_("Paragraphs — paragraphs"), ""),
    "Slides": (Risk.PROVENANCE, N_("Slides — slides"), ""),
    "Notes": (Risk.PROVENANCE, N_("Notes — notes"), ""),
    "HiddenSlides": (Risk.PROVENANCE, N_("HiddenSlides — hidden slides"), ""),
    "Template": (Risk.PROVENANCE, N_("Template — template"),
                 N_("The template name; if it is a path, the file system leaks.")),
    "DocSecurity": (Risk.PROVENANCE, N_("DocSecurity — protection flag"), ""),
    "ScaleCrop": (Risk.PROVENANCE, "ScaleCrop", ""),
    "LinksUpToDate": (Risk.PROVENANCE, "LinksUpToDate", ""),
    "SharedDoc": (Risk.PROVENANCE, N_("SharedDoc — shared"), ""),
    "HyperlinksChanged": (Risk.PROVENANCE, "HyperlinksChanged", ""),
    "HeadingPairs": (Risk.PROVENANCE, N_("HeadingPairs — document composition"), ""),
    "TitlesOfParts": (Risk.PROVENANCE, N_("TitlesOfParts — part titles"),
                      N_("Section headings and the sheet names of a workbook.")),
    "DigSig": (Risk.IDENTITY, N_("DigSig — digital signature"), ""),
}

# Attributes in parts with comments and authors.
PERSON_ATTRS = {
    "author": (Risk.IDENTITY, N_("Author")),
    "name": (Risk.IDENTITY, N_("Author name")),
    "displayName": (Risk.IDENTITY, N_("Display name")),
    "initials": (Risk.IDENTITY, N_("Author initials")),
    "userId": (Risk.IDENTITY, N_("userId — usually a real email")),
    "email": (Risk.IDENTITY, N_("Author email")),
    "providerId": (Risk.PROVENANCE, N_("Account provider")),
    "date": (Risk.PROVENANCE, N_("Comment date")),
    "dT": (Risk.PROVENANCE, N_("Comment date")),
    "created": (Risk.PROVENANCE, N_("Comment creation date")),
    # A GUID is not PII by itself, but it links a reply to the author list:
    # if phase 2 scrubs authors.xml and leaves these references, the link is lost.
    "authorId": (Risk.PROVENANCE, N_("authorId — reference to the author list")),
    "personId": (Risk.PROVENANCE, N_("personId — reference to the author list")),
    "lastModifiedBy": (Risk.IDENTITY, N_("Who modified the comment")),
    "userName": (Risk.IDENTITY, N_("Shared-workbook participant (xl/revisions)")),
    "dateTime": (Risk.PROVENANCE, N_("Time saved by the participant")),
}


# === small utilities ========================================================


def _ln(tag) -> str:
    """Local name of a tag or attribute, without the namespace."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(el, name):
    """Attribute by local name: files may use any prefixes."""
    for key, val in el.attrib.items():
        if _ln(key) == name:
            return val
    return None


def _iter_tag(root, *names):
    """All elements with the given local names."""
    wanted = set(names)
    for el in root.iter():
        if _ln(el.tag) in wanted:
            yield el


def _text(el) -> str:
    """All text of an element, descendants included (vt:vector etc.)."""
    return " ".join(t.strip() for t in el.itertext() if t and t.strip())


def _is_path(value) -> bool:
    return bool(value) and bool(_PATH_RE.match(str(value)))


def _has_path(value) -> bool:
    """A path anywhere inside the value, not necessarily at its start."""
    return bool(value) and bool(_PATH_ANY_RE.search(str(value)))


def _emails(value):
    return _EMAIL_RE.findall(str(value or ""))


def _value_risk(value, default=Risk.PROVENANCE):
    """Risk by the shape of the value: email -> identity, path -> environment."""
    if _emails(value):
        return Risk.IDENTITY
    if _has_path(value):
        return Risk.ENVIRONMENT
    return default


def _kids_attrs(el, limit=MAX_LISTED) -> str:
    """Data of descendants whose value lives in attributes rather than in text.

    w:docVars and w:shapeDefaults are built exactly like that: itertext() over
    them yields an empty string, and without this parsing the names and values
    of the variables are lost.
    """
    out = []
    for kid in el.iter():
        if kid is el or not isinstance(kid.tag, str):
            continue
        pairs = "; ".join("%s=%s" % (_ln(k), v) for k, v in kid.attrib.items())
        if pairs:
            out.append("%s(%s)" % (_ln(kid.tag), pairs))
        if len(out) >= limit:
            break
    return " | ".join(out)


def _parser():
    """XML parser with no external entities and no network: the files are untrusted."""
    return etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False,
        load_dtd=False, recover=True,
    )


class _Capped:
    """A stream with a hard byte ceiling: bomb protection during streaming parse."""

    def __init__(self, fh, limit):
        self._fh = fh
        self._left = limit

    def read(self, size=-1):
        if self._left <= 0:
            return b""
        if size is None or size < 0:
            size = self._left
        chunk = self._fh.read(min(size, self._left))
        self._left -= len(chunk)
        return chunk

    @property
    def exhausted(self) -> bool:
        """Whether the byte ceiling was reached (rather than the natural end of the stream)."""
        return self._left <= 0


def _read_part(zf, name, limit=MAX_PART_READ):
    """Read a package part into memory, checking the declared size."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ValueError(
            tr("part %s is too large (%d bytes), skipped") % (name, info.file_size)
        )
    with zf.open(info, "r") as fh:
        return fh.read(limit + 1)[:limit]


def _read_xml(zf, name, limit=MAX_PART_READ):
    """Parse an XML part. Returns the root or None."""
    raw = _read_part(zf, name, limit)
    if not raw:
        return None
    root = etree.fromstring(raw, parser=_parser())
    return root


def _section(rep, title, fn, *args):
    """Each parsing section is isolated: a failure in one does not bring down the inspection."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 -- we deliberately catch everything
        rep.err("%s: %s: %s" % (title, type(exc).__name__, exc))


def _try(rep, where, fn, *args):
    """The same, but for ONE part inside a loop.

    Without it, a broken local header of one entry aborted the parsing of all
    the other parts of the section: the report ended up with a single error
    instead of findings.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        rep.err("%s: %s: %s" % (where, type(exc).__name__, exc))
        return None


# === docProps parsing (reused by phase 2) ===================================


def parse_props(zf) -> dict:
    """Raw values of docProps/core.xml, app.xml and custom.xml.

    Factored out into a separate function so that phase 2 (cleaning) does not
    rewrite the parsing but works with the same dictionaries the inspector saw.
    Returns {"core": {...}, "app": {...}, "custom": [(name, type, value)]}.
    Never raises: a part that cannot be read yields an empty dictionary.
    """
    out = {"core": {}, "app": {}, "custom": []}

    try:
        root = _read_xml(zf, "docProps/core.xml")
        if root is not None:
            for el in root:
                key = _ln(el.tag)
                if key:
                    out["core"][key] = (el.text or "").strip()
    except Exception:
        pass

    try:
        root = _read_xml(zf, "docProps/app.xml")
        if root is not None:
            for el in root:
                key = _ln(el.tag)
                if not key:
                    continue
                out["app"][key] = _text(el) if len(el) else (el.text or "").strip()
    except Exception:
        pass

    try:
        root = _read_xml(zf, "docProps/custom.xml")
        if root is not None:
            for el in _iter_tag(root, "property"):
                name = _attr(el, "name") or ""
                kids = [k for k in el if isinstance(k.tag, str)]
                vtype = _ln(kids[0].tag) if kids else ""
                value = _text(el)
                out["custom"].append((name, vtype, value))
    except Exception:
        pass

    return out


# === ZIP container ==========================================================


# Stock office suites that are NOT Microsoft Office. Their files legitimately
# differ from Word's: a different order of ZIP entries, a different set of
# parts, no rsid. Without this list ANY genuine LibreOffice document was
# declared "built by the python-docx library or an AI generator" -- a false
# positive on real people's files, not only on our regenerated ones.
_STOCK_SUITES = ("libreoffice", "openoffice", "staroffice", "neooffice",
                 "collabora", "onlyoffice", "ascensio", "wps ", "kingsoft")


def _stock_suite(app) -> str:
    """Name of a stock non-Microsoft suite from Application, otherwise ''."""
    low = str(app or "").lower()
    if "microsoft" in low:
        return ""
    for marker in _STOCK_SUITES:
        if marker in low:
            return str(app).split("/")[0].split("$")[0].strip()[:60]
    return ""


def _app_name_early(zf) -> str:
    """Application before the sections are parsed: the ZIP section needs to know
    what built the package, while _sec_app runs later. Reads one small part,
    never raises."""
    try:
        raw = zf.read("docProps/app.xml")
    except Exception:
        return ""
    try:
        m = re.search(rb"<Application>([^<]{0,200})</Application>", raw)
        return m.group(1).decode("utf-8", "replace") if m else ""
    except Exception:
        return ""


def _sec_zip(zf, rep, ctx):
    infos = zf.infolist()
    ctx["names"] = [i.filename for i in infos]
    ctx["nameset"] = set(ctx["names"])

    if len(infos) > MAX_PARTS_SCAN:
        rep.err(tr("The package has %d entries, only the first %d were processed")
                % (len(infos), MAX_PARTS_SCAN))
        infos = infos[:MAX_PARTS_SCAN]

    total_unc = 0
    total_cmp = 0
    for i in infos:
        total_unc += i.file_size
        total_cmp += i.compress_size
        # Phase 2 rebuilds the package byte for byte, so it needs every field of the entry.
        rep.parts.append({
            "name": i.filename,
            "date_time": tuple(i.date_time),
            "compress_type": i.compress_type,
            "compress_size": i.compress_size,
            "file_size": i.file_size,
            "crc": i.CRC,
            "flag_bits": i.flag_bits,
            "create_system": i.create_system,
        })

    ratio = total_unc / max(total_cmp, 1)
    ctx["safe"] = True
    if total_unc > MAX_TOTAL_UNPACKED or (
            total_unc > MIN_BOMB_UNPACKED and ratio > MAX_RATIO):
        ctx["safe"] = False
        rep.signal("hazard",
                   tr("Suspected zip bomb: uncompressed size %d bytes, compression ratio %.0f. "
                      "The contents of the parts were not decompressed.")
                   % (total_unc, ratio), "high")
        rep.err(tr("Content parsing skipped for security reasons"))

    rep.add(Risk.PROVENANCE, "ZIP", tr("Entries in the package"), str(len(infos)),
            tr("The package layout alone reveals both the generator and the document structure."))

    # First entry. Office always puts [Content_Types].xml first.
    first = infos[0].filename if infos else ""
    rep.add(Risk.PROVENANCE, "ZIP", tr("First container entry"), first,
            tr("Word/Excel/PowerPoint always write [Content_Types].xml first."))
    if first and first != "[Content_Types].xml":
        suite = _stock_suite(_app_name_early(zf))
        if suite:
            # LibreOffice and the like put word/document.xml first. That is their normal
            # order, not a trace of a foreign tool.
            rep.signal("producer",
                       tr("First ZIP entry is %s: the normal order for %s, whereas Microsoft "
                          "Office puts [Content_Types].xml first.")
                       % (first, suite), "medium")
        else:
            rep.signal("ai",
                       tr("First ZIP entry is %s, not [Content_Types].xml: the package was built "
                          "by a third-party tool or rebuilt.") % first,
                       "medium")

    # Timestamps. Word zeroes them to 1980-01-01, libraries write the real time.
    stamps = [tuple(i.date_time) for i in infos]
    epoch_like = [s for s in stamps if s[:3] == (1980, 1, 1)]
    real = sorted(s for s in stamps if s[0] >= 2000)
    ctx["zip_real"] = bool(real)
    ctx["zip_epoch_only"] = bool(stamps) and len(epoch_like) == len(stamps)

    if real:
        lo = "%04d-%02d-%02d %02d:%02d:%02d" % real[0]
        hi = "%04d-%02d-%02d %02d:%02d:%02d" % real[-1]
        rep.add(Risk.ENVIRONMENT, "ZIP", tr("Real save time of entries"),
                tr("%s … %s (%d of %d entries)") % (lo, hi, len(real), len(stamps)),
                tr("Office stamps every entry with 1980-01-01 00:00. Real dates mean the package "
                   "was built or rebuilt by a library: the time spent working on the file leaks "
                   "even when docProps/core.xml is completely scrubbed."))
        ctx["zip_real_max"] = hi
    elif ctx["zip_epoch_only"]:
        rep.add(Risk.PROVENANCE, "ZIP", tr("Entry timestamps"),
                tr("all 1980-01-01 00:00"),
                tr("Normal Office behavior: the save time does not leak through the ZIP."))

    types = {i.compress_type for i in infos}
    if types and types != {zipfile.ZIP_DEFLATED}:
        rep.add(Risk.PROVENANCE, "ZIP", tr("Compression methods"),
                ", ".join(str(t) for t in sorted(types)),
                tr("Office compresses all parts with deflate; a mix of methods is a sign of "
                   "rebuilding by a third-party tool."))


# === docProps/core.xml ======================================================


def _sec_core(zf, rep, ctx):
    core = ctx["props"]["core"]
    if "docProps/core.xml" not in ctx["nameset"]:
        rep.signal("ai", tr("No docProps/core.xml — Office does not write a package like this."),
                   "high")
        return
    if not core:
        # Otherwise a broken core.xml looks clean: no error and no findings.
        rep.err(tr("docProps/core.xml: the part exists but no property could be parsed "
                   "(corrupted or non-XML content)"))
    for key, risk, label, note in CORE_FIELDS:
        label, note = tr(label), tr(note)
        present = key in core
        value = core.get(key, "")
        full = note
        if not present:
            full = (note + tr(" The element is missing from core.xml.")).strip()
        # An empty field is shown deliberately: that is a fact too (scrubbed or stock).
        rep.add(risk, "docProps/core.xml", label, value, full)
        for mail in _emails(value):
            rep.add(Risk.IDENTITY, "docProps/core.xml",
                    tr("Email in field %s") % key, mail,
                    tr("An address in the document properties."))


# === docProps/app.xml =======================================================


def _sec_app(zf, rep, ctx):
    app = ctx["props"]["app"]
    if "docProps/app.xml" not in ctx["nameset"]:
        rep.signal("ai", tr("No docProps/app.xml — Office does not write a package like this."),
                   "high")
        return
    if not app:
        rep.err(tr("docProps/app.xml: the part exists but no property could be parsed (corrupted "
                   "or non-XML content)"))
    for key, value in app.items():
        risk, label, note = APP_FIELDS.get(
            key, (Risk.PROVENANCE, "%s" % key, tr("Non-standard app.xml field."))
        )
        if key in APP_FIELDS:
            label, note = tr(label), tr(note)
        if key == "Template" and _has_path(value):
            risk = Risk.ENVIRONMENT
            note = tr("The template path reveals the author's file system.")
        rep.add(risk, "docProps/app.xml", label, value, note)
        for mail in _emails(value):
            rep.add(Risk.IDENTITY, "docProps/app.xml",
                    tr("Email in field %s") % key, mail, tr("An address in the document properties."))


# === docProps/custom.xml and Purview labels =================================

# SharePoint/EDMS properties: a tie to a specific portal and library.
_SP_NAMES = {
    "ContentTypeId", "TemplateUrl", "_SourceUrl", "_SharedFileIndex",
    "_dlc_DocId", "_dlc_DocIdUrl", "_dlc_DocIdItemGuid", "Order",
    "xd_Signature", "xd_ProgID", "_ExtendedDescription",
}

_MSIP_NOTES = {
    "SetBy": N_("The real email of the user who applied the label. The most direct identity leak "
                "in OOXML."),
    "SiteId": N_("The Microsoft 365 tenant GUID: uniquely identifies the organization even if "
                 "everything else is scrubbed."),
    "SetDate": N_("The exact time the label was applied."),
    "Name": N_("The human-readable name of the sensitivity label."),
    "ActionId": N_("GUID of the labeling operation."),
    "Enabled": N_("Flag showing whether the label is active."),
    "Method": N_("How the label was applied: manually or by policy."),
    "ContentBits": N_("Which parts of the document are labeled (header/footer, watermark)."),
    "SiteUrl": N_("URL of the organization's portal."),
}


def _sec_custom(zf, rep, ctx):
    props = ctx["props"]["custom"]
    loc = "docProps/custom.xml"
    if not props and loc in ctx["nameset"]:
        # The same logic as for core.xml/app.xml: the part exists but no property
        # could be parsed -- otherwise a broken custom.xml looks like a file with no
        # custom properties at all, and the finding silently disappears.
        rep.err(tr("docProps/custom.xml: the part exists but no property could be parsed "
                   "(corrupted or non-XML content)"))
    for name, vtype, value in props:
        risk = Risk.PROVENANCE
        note = tr("Custom property (type %s).") % (vtype or "?")

        if name.startswith("MSIP_Label_"):
            risk = Risk.IDENTITY
            field = name.rsplit("_", 1)[-1]
            note = (tr(_MSIP_NOTES[field]) if field in _MSIP_NOTES
                    else tr("Microsoft Purview sensitivity label."))
            note = "Purview: " + note
        # An email or full name in the VALUE is an identity no matter what the
        # property itself is called. A name like "_dlc_DocId" or "_Approver" used to
        # send the finding to ENVIRONMENT on the underscore alone, even when a real
        # email sat inside: the check by value must come before the check by
        # SharePoint/EDMS name, not after it.
        elif _emails(value):
            risk = Risk.IDENTITY
            note = tr("The value contains an email.")
        elif value and _PERSON_NAME_RE.search(name):
            risk = Risk.IDENTITY
            note = (tr("The property holds a person (author, executor, signer) — a full name "
                       "from a template or an EDMS card."))
        elif name in _SP_NAMES or name.startswith("_") or "urn:schemas" in name:
            risk = Risk.ENVIRONMENT
            note = tr("SharePoint/EDMS property: a tie to a portal and library.")
        elif _has_path(value):
            risk = Risk.ENVIRONMENT
            note = tr("The value looks like a local or network path.")

        rep.add(risk, loc, name, value, note)

    if any(n.startswith("MSIP_Label_") for n, _, _ in props):
        rep.signal("producer",
                   tr("The document is labeled with Microsoft Purview: the label carries the "
                      "tenant GUID and the email of the employee who applied it."), "high")

    # A parallel place for the same labels in newer Office builds.
    for name in ctx["names"]:
        if name.lower().endswith("labelinfo.xml"):
            raw = _try(rep, name, _read_part, zf, name, 1 * 1024 * 1024)
            if raw:
                rep.add(Risk.IDENTITY, name, tr("LabelInfo (Purview label)"),
                        raw.decode("utf-8", "replace"),
                        tr("Duplicates the sensitivity label as a separate package part; "
                           "cleaning custom.xml alone does not remove it."))


# === preview ================================================================


def _sec_thumbnail(zf, rep, ctx):
    for name in ctx["names"]:
        if not name.startswith("docProps/thumbnail"):
            continue
        try:
            size = zf.getinfo(name).file_size
        except KeyError:
            size = 0
        rep.add(Risk.PROVENANCE, name, tr("Document preview"), tr("%d bytes") % size,
                tr("The preview is rendered at save time and may show the PREVIOUS version of "
                   "the document — including text that has since been deleted. It is also an "
                   "image with its own metadata."))
        rep.signal("hazard",
                   tr("There is an embedded preview (%s): it may visually differ from the "
                      "current content.") % name, "low")


# === relationships (*.rels) =================================================


def _owner_of_rels(name):
    """word/_rels/settings.xml.rels -> word/settings.xml"""
    head, _, base = name.rpartition("/")
    base = base[:-5] if base.endswith(".rels") else base
    head = head[:-6] if head.endswith("/_rels") else head
    if head == "_rels":
        head = ""
    return (head + "/" + base).lstrip("/")


def _sec_rels(zf, rep, ctx):
    ctx["rels"] = {}
    shown = 0
    http_count = 0
    for name in ctx["names"]:
        if not name.endswith(".rels"):
            continue
        try:
            root = _read_xml(zf, name, 8 * 1024 * 1024)
        except Exception as exc:  # noqa: BLE001
            rep.err("rels %s: %s" % (name, exc))
            continue
        if root is None:
            continue
        owner = _owner_of_rels(name)
        table = ctx["rels"].setdefault(owner, {})
        for el in _iter_tag(root, "Relationship"):
            rid = _attr(el, "Id") or ""
            target = _attr(el, "Target") or ""
            mode = _attr(el, "TargetMode") or ""
            rtype = _attr(el, "Type") or ""
            table[rid] = (target, mode, rtype)
            low = target.lower()
            # TargetMode cannot be relied on: in broken packages the attribute is lost
            # when the XML is recovered. An internal target never looks like an absolute
            # path, mailto: or http(s), so we judge by shape.
            external = (
                mode == "External" or _has_path(target)
                or low.startswith(("mailto:", "http://", "https://"))
            )
            if not external:
                continue
            if low.startswith("mailto:"):
                rep.add(Risk.IDENTITY, name, tr("mailto link"), target,
                        tr("A direct email address in an external relationship."))
                continue
            # http(s) is handled BEFORE paths: otherwise a URL with a drive letter
            # inside would fall into the file branch.
            if low.startswith("http://") or low.startswith("https://"):
                http_count += 1
                if shown < MAX_LISTED:
                    shown += 1
                    risk = Risk.ENVIRONMENT if (
                        "sharepoint.com" in low or "/personal/" in low
                        or ".local/" in low
                    ) else Risk.PROVENANCE
                    rep.add(risk, name, tr("External hyperlink"), target,
                            tr("External relationship; corporate URLs reveal the portal and the "
                               "account."))
                continue
            if _has_path(target):
                rep.add(Risk.ENVIRONMENT, name, tr("External file link"), target,
                        tr("An absolute path/UNC reveals the user name, the drive or the "
                           "organization's server."))
                continue
            rep.add(Risk.ENVIRONMENT, name, tr("External relationship (%s)") % rid,
                    target, tr("TargetMode=External, type %s") % _ln(rtype))

    if http_count > shown:
        rep.add(Risk.PROVENANCE, "*.rels", tr("More external hyperlinks"),
                str(http_count - shown), tr("Not all are shown, the rest are of the same kind."))


def _resolve(ctx, owner, rid):
    """Resolve an r:id to its target through the corresponding .rels."""
    if not rid:
        return None
    entry = ctx.get("rels", {}).get(owner, {}).get(rid)
    return entry[0] if entry else None


# === word/settings.xml ======================================================


def _sec_settings(zf, rep, ctx):
    loc = "word/settings.xml"
    if loc not in ctx["nameset"]:
        return
    root = _read_xml(zf, loc)
    if root is None:
        return

    # rsid: edit session identifiers.
    rsids = []
    for el in _iter_tag(root, "rsid", "rsidRoot"):
        val = _attr(el, "val")
        if val:
            rsids.append(val)
    ctx["rsid_count"] = len(rsids)
    rep.add(Risk.PROVENANCE, loc, tr("w:rsid — edit session identifiers"),
            tr("%d total: %s") % (len(rsids), ", ".join(rsids[:8]) or tr("none")),
            tr("Each rsid is a separate editing session. Their number reveals how many times the "
               "document was opened and edited, and the set itself links different documents "
               "from one Word installation."))

    # attachedTemplate: almost always a path like file:///C:/Users/NAME/...
    for el in _iter_tag(root, "attachedTemplate"):
        rid = _attr(el, "id")
        target = _resolve(ctx, loc, rid)
        risk = Risk.ENVIRONMENT if _has_path(target) else Risk.PROVENANCE
        rep.add(risk, loc, tr("w:attachedTemplate — attached template"),
                target or (tr("r:id=%s (not resolved)") % rid),
                tr("The template path usually contains the Windows user name or a UNC path to "
                   "the organization's server."))

    # mailMerge: the mail-merge data source.
    for el in _iter_tag(root, "mailMerge"):
        for kid in el.iter():
            kname = _ln(kid.tag)
            # w:udl is the merge connection string (ODBC/OLEDB), w:table is the sheet
            # or table name in the source. Without them the mailMerge parsing missed the
            # main thing: the login and the server address.
            if kname not in ("dataSource", "headerSource", "query", "src",
                             "connectString", "udl", "table", "odso"):
                continue
            val = _attr(kid, "val") or _attr(kid, "id") or ""
            if _attr(kid, "id"):
                val = _resolve(ctx, loc, _attr(kid, "id")) or val
            if not val:
                continue
            rep.add(_value_risk(val, Risk.ENVIRONMENT),
                    loc, "w:mailMerge/%s" % kname, val,
                    tr("Merge data source: a path to a database or table, or a connection string "
                       "with a login and server address."))

    # documentProtection: password hash and salt.
    for el in _iter_tag(root, "documentProtection", "writeProtection"):
        parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
        rep.add(Risk.IDENTITY, loc, tr("w:%s — document protection") % _ln(el.tag),
                "; ".join(parts),
                tr("Password hash and salt. Removing them lifts the protection itself, so they "
                   "cannot be removed without losing functionality."),
                removable=False)

    for el in _iter_tag(root, "proofState"):
        parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
        rep.add(Risk.PROVENANCE, loc, tr("w:proofState — proofing state"),
                "; ".join(parts),
                tr("Shows whether the author finished the spell check."))

    if any(True for _ in _iter_tag(root, "trackChanges")):
        rep.add(Risk.PROVENANCE, loc, tr("w:trackChanges — change tracking"),
                tr("enabled"),
                tr("Change tracking is on: all subsequent edits will be saved together with the "
                   "author's name."))
        rep.signal("hazard",
                   tr("Change tracking is turned on in settings.xml."), "medium")

    for el in _iter_tag(root, "hdrShapeDefaults", "shapeDefaults"):
        value = _kids_attrs(el) or _text(el) or tr("present")
        rep.add(_value_risk(value), loc,
                tr("w:%s — shape defaults") % _ln(el.tag), value,
                tr("The block stores object settings and sometimes identifiers of earlier "
                   "insertions."))

    for el in _iter_tag(root, "docVars"):
        # docVar values live in the name/val attributes and the element has no text:
        # through itertext() the whole content of the variables is lost.
        value = _kids_attrs(el) or _text(el) or tr("present")
        rep.add(_value_risk(value), loc, tr("w:docVars — document variables"),
                value,
                tr("Variables of macros, add-ins and EDMS templates: they regularly hold the "
                   "full names of approvers, contract numbers and database paths."))
        # A variable named like a person ("Owner", "Executor") is an identity, like a
        # same-named property in custom.xml. As a separate finding: in the combined
        # line above its value got lost in PROVENANCE, and Stealth left the full name
        # in the file.
        for var in _iter_tag(el, "docVar"):
            if _PERSON_NAME_RE.search(_attr(var, "name") or ""):
                rep.add_nonempty(Risk.IDENTITY, loc,
                                 "w:docVar %s" % (_attr(var, "name") or "")[:60], _attr(var, "val"),
                                 tr("The document variable holds a person: a full name from a "
                                    "template or an EDMS."))


# === word/document.xml and its neighbours ===================================

_REVISION_TAGS = ("ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel",
                  "rPrChange", "pPrChange", "sectPrChange", "tblPrChange")

# Parts that hold text with revisions. Headers and footers, footnotes and the
# autotext glossary (word/glossary/document.xml) are the same kind of body
# with their own authors and rsids, and cleaning that looks only at
# document.xml misses them.
_BODY_PART_RE = re.compile(
    r"^word/(document\d*\.xml|glossary/document\d*\.xml|header\d*\.xml"
    r"|footer\d*\.xml|footnotes\.xml|endnotes\.xml|comments\.xml)$"
)


def _scan_body(zf, part, acc):
    """Streaming parse of one body part.

    Returns the reason for stopping (a string) or None if the part was parsed
    in full. Stopping is possible at two independent limits: the number of
    elements (MAX_ELEMENTS) and the volume of decompressed bytes (MAX_STREAM,
    via _Capped) -- a document can be small in tag count yet hold one huge text
    node, and then the byte limit fires first. Previously the revisions beyond
    the cutoff vanished silently: no trace in findings or in errors. Both
    limits must now reach the report.
    """
    with zf.open(zf.getinfo(part), "r") as raw:
        # iterparse, not fromstring: document.xml can be hundreds of megabytes.
        capped = _Capped(raw, MAX_STREAM)
        context = etree.iterparse(
            capped, events=("end",), resolve_entities=False,
            no_network=True, huge_tree=False, recover=True,
        )
        seen = 0
        for _, el in context:
            seen += 1
            if seen > MAX_ELEMENTS:
                return tr("limit of %d elements") % MAX_ELEMENTS
            tag = _ln(el.tag)
            if tag in _REVISION_TAGS:
                who = _attr(el, "author")
                when = _attr(el, "date")
                if who is not None:
                    key = (part, who)
                    acc["authors"][key] = acc["authors"].get(key, 0) + 1
                if when:
                    acc["dates"].append(when)
            elif tag == "bookmarkStart":
                if _attr(el, "name") == "_GoBack":
                    acc["goback"].add(part)
            elif tag == "permStart":
                ed = _attr(el, "ed")
                if ed:
                    acc["editors"].add((part, ed))
            for key in el.attrib:
                if _ln(key).startswith("rsid"):
                    acc["rsid"] += 1
            el.clear()
            parent = el.getparent()
            if parent is not None:
                while el.getprevious() is not None:
                    del parent[0]
        if capped.exhausted:
            return tr("streaming parse limit of %d MB") % (MAX_STREAM // (1024 * 1024))
    return None


def _sec_document(zf, rep, ctx):
    targets = [n for n in ctx["names"] if _BODY_PART_RE.match(n)]
    if not targets:
        return

    acc = {"authors": {}, "dates": [], "editors": set(), "goback": set(),
           "rsid": 0}
    for part in targets:
        # A try of its own for each part: a broken header of one entry must not
        # deprive the report of the revisions from all the others.
        try:
            reason = _scan_body(zf, part, acc)
            if reason:
                rep.err(tr("%s: parsing stopped (%s), revisions beyond this limit did not make "
                           "it into the report") % (part, reason))
        except Exception as exc:  # noqa: BLE001
            rep.err("%s: %s: %s" % (part, type(exc).__name__, exc))

    ctx["rsid_attrs"] = acc["rsid"]
    main = ("word/document.xml" if "word/document.xml" in targets
            else targets[0])
    rep.add(Risk.PROVENANCE, main, tr("rsid attributes in the body"), str(acc["rsid"]),
            tr("Word marks every paragraph and run with an edit session identifier. A complete "
               "absence of rsid means the document was not created by Word. Body parts scanned: "
               "%d (%s).")
            % (len(targets), ", ".join(targets[:8])))

    ranked = sorted(acc["authors"].items(), key=lambda kv: -kv[1])
    for (part, who), cnt in ranked[:MAX_LISTED]:
        rep.add(Risk.IDENTITY, part, tr("Revision author (w:ins/w:del)"),
                tr("%s (%d edits)") % (who or tr("<empty>"), cnt),
                tr("Track changes stores the full name or login of the editor right in the "
                   "document body — outside docProps."))
    if acc["dates"]:
        acc["dates"].sort()
        rep.add(Risk.PROVENANCE, main, tr("Revision dates"),
                tr("%s … %s (%d total)")
                % (acc["dates"][0], acc["dates"][-1], len(acc["dates"])),
                tr("The exact time of each edit."))
    for part, ed in sorted(acc["editors"])[:MAX_LISTED]:
        rep.add(Risk.IDENTITY, part, tr("w:permStart — permitted editor"), ed,
                tr("The account name that is allowed to edit the region."))
    for part in sorted(acc["goback"]):
        rep.add(Risk.PROVENANCE, part, tr("_GoBack bookmark"), tr("present"),
                tr("Word places it at the position of the last edit. Its presence confirms the "
                   "file was saved from Word."))
    if acc["authors"]:
        people = {who for _, who in acc["authors"]}
        where = sorted({part for part, _ in acc["authors"]})
        rep.signal("hazard",
                   tr("The document body contains saved revisions (track changes) from %d "
                      "author(s), parts: %s.")
                   % (len(people), ", ".join(where[:8])), "high")


# === comments, authors, presenceInfo ========================================


def _is_person_part(name):
    if name in ("word/comments.xml", "word/commentsExtended.xml",
                "word/commentsIds.xml", "word/people.xml",
                "word/commentsExtensible.xml", "ppt/commentAuthors.xml",
                "ppt/authors.xml"):     # authors of the "modern" comments of PowerPoint 365
        return True
    return bool(
        re.match(r"^xl/persons/.*\.xml$", name)
        or re.match(r"^xl/revisions/.*\.xml$", name)
        or re.match(r"^xl/threadedComments/.*\.xml$", name)
        or re.match(r"^ppt/comments/.*\.xml$", name)
        or re.match(r"^ppt/.*/comments\d*\.xml$", name)
    )


def _sec_people(zf, rep, ctx):
    seen = set()
    for name in ctx["names"]:
        if not _is_person_part(name):
            continue
        try:
            root = _read_xml(zf, name)
        except Exception as exc:  # noqa: BLE001
            rep.err("%s: %s" % (name, exc))
            continue
        if root is None:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for key, val in el.attrib.items():
                spec = PERSON_ATTRS.get(_ln(key))
                if not spec or not val:
                    continue
                risk, label = spec
                mark = (name, label, val)
                if mark in seen or len(seen) > 400:
                    continue
                seen.add(mark)
                note = tr("A comment author or discussion participant.")
                if _ln(key) == "userId":
                    note = (tr("w15:presenceInfo/@userId — in the overwhelming majority of cases "
                               "this is a real work email."))
                rep.add(risk, name, tr(label), val, note)


# === media, attachments, macros =============================================


def _sec_media(zf, rep, ctx):
    media = []
    embeds = []
    vba = []
    activex = []
    for name in ctx["names"]:
        low = name.lower()
        try:
            size = zf.getinfo(name).file_size
        except KeyError:
            size = 0
        if re.match(r"^(word|xl|ppt)/media/", name):
            media.append((name, size))
        elif "/embeddings/" in name:
            embeds.append((name, size))
        elif low.endswith("vbaproject.bin"):
            vba.append((name, size))
        elif "/activex/" in low:
            activex.append((name, size))

    if media:
        listed = ", ".join(tr("%s (%d B)") % (n.rsplit("/", 1)[-1], s)
                           for n, s in media[:20])
        rep.add(Risk.PROVENANCE, "media", tr("Embedded images"),
                tr("%d total: %s") % (len(media), listed),
                tr("Each picture carries its own metadata (EXIF, XMP, ICC, GPS). A recursive "
                   "inspection by the image inspector is required: the dispatcher calls it, not "
                   "this module."))
    for name, size in embeds[:MAX_LISTED]:
        rep.add(Risk.PROVENANCE, name, tr("Embedded OLE object"), tr("%d bytes") % size,
                tr("An embedded object has ITS OWN docProps (author, company, path). A recursive "
                   "inspection by the OLE inspector is needed."))
    if embeds:
        rep.signal("hazard",
                   tr("The package has %d embedded OLE object(s) with their own metadata.") % len(embeds), "medium")
    for name, size in vba:
        rep.add(Risk.PROVENANCE, name, tr("VBA project (macros)"), tr("%d bytes") % size,
                tr("Stores macro sources, module names and often the project path on the "
                   "author's machine."))
        rep.signal("hazard",
                   tr("The file contains VBA macros (%s): active content.") % name,
                   "high")
    for name, size in activex[:10]:
        rep.add(Risk.PROVENANCE, name, tr("ActiveX control"), tr("%d bytes") % size,
                tr("Active content in an embedded control."))
    if activex:
        rep.signal("hazard", tr("The package contains ActiveX controls."), "medium")


# === printerSettings*.bin (DEVMODE) =========================================


def _wchar32(blob: bytes, offset: int) -> str:
    """A WCHAR[32] field at offset: 64 bytes of UTF-16LE up to the first NUL.

    An empty string if the field cannot be read or is not printable -- these
    are untrusted bytes, and a finding with garbage inside must not be made
    from them.
    """
    if len(blob) < offset + 64:
        return ""
    text = blob[offset:offset + 64].decode("utf-16le", "ignore").split("\x00", 1)[0].strip()
    return text if text and text.isprintable() else ""


# Offsets in DEVMODEW. The structure is fixed, this is not a heuristic:
# dmDeviceName[32] WCHAR, then service WORD/DWORD fields, then dmFormName[32]
# WCHAR. The full size of DEVMODEW is 220 bytes, followed by the driver's
# private data of length dmDriverExtra.
_DM_SIZE_OFF = 68          # dmSize: WORD, full length of the structure itself
_DM_DRIVER_EXTRA_OFF = 70  # dmDriverExtra: WORD, length of the driver's private tail
_DM_PAPERSIZE_OFF = 78     # dmPaperSize: short, paper size code
_DM_FORMNAME_OFF = 102     # dmFormName[32] WCHAR
_DEVMODEW_SIZE = 220

# dmPaperSize codes that tell the region apart. Letter/Legal mean North
# America, A4 almost the whole rest of the world. The contract
# (docs/ARCHITECTURE.md) explicitly files paper size under the signal
# kind="dating": geolocation from indirect evidence.
_PAPER_REGION = {
    1: ("Letter 8.5x11in", N_("US and Canada")),
    5: ("Legal 8.5x14in", N_("US and Canada")),
    8: ("A3 297x420mm", N_("Europe and the rest of the world")),
    9: ("A4 210x297mm", N_("Europe and the rest of the world")),
    11: ("A5 148x210mm", N_("Europe and the rest of the world")),
}
# Form names that give the region even when dmPaperSize is not filled in.
_FORM_REGION = {"letter": N_("US and Canada"), "legal": N_("US and Canada"),
                "a4": N_("Europe and the rest of the world"), "a3": N_("Europe and the rest of the world"),
                "a5": N_("Europe and the rest of the world")}


def _device_name(blob: bytes) -> str:
    """dmDeviceName from the start of the DEVMODE.

    In OOXML this is almost always a DEVMODEW: the name takes 32 WCHAR = 64
    bytes in UTF-16LE and ends at a NUL. For the old DEVMODEA (32 single-byte
    characters) there is a fallback parse.
    """
    name = _wchar32(blob, 0)
    if name:
        return name
    name = blob[:32].decode("cp1251", "ignore").split("\x00", 1)[0].strip()
    if not name.isprintable():
        return ""
    return name


def _devmode_tail(blob: bytes):
    """The driver's private data that sits AFTER the DEVMODE structure.

    Returns (offset, bytes) or (0, b""). The length is taken from dmSize, not
    from a constant: a driver may have its own version of the structure. If
    dmSize is insane, we fall back to the standard DEVMODEW size.
    """
    if len(blob) < _DEVMODEW_SIZE:
        return 0, b""
    size = struct.unpack_from("<H", blob, _DM_SIZE_OFF)[0]
    if not (_DEVMODEW_SIZE <= size <= len(blob)):
        size = _DEVMODEW_SIZE
    return size, blob[size:]


# A host or UNC path in the driver's private data: \\PRINTSRV01\HP4050 or a
# port name regularly turns up there. We search in UTF-16LE and in a
# single-byte encoding.
_UNC_RE = re.compile(r"\\\\[A-Za-z0-9._-]{2,63}\\[A-Za-z0-9._$ -]{1,63}")


def _tail_strings(tail: bytes, limit=6):
    """UNC paths from the driver's private tail. The tail is binary garbage, so we
    look only for what is recognizable unambiguously and do not invent
    "name-like" strings: a false finding in a privacy report costs more than a
    missed one."""
    found, seen = [], set()
    for raw in (tail.decode("utf-16le", "ignore"), tail.decode("cp1251", "ignore")):
        for m in _UNC_RE.finditer(raw):
            val = m.group(0).strip()
            if val.lower() in seen:
                continue
            seen.add(val.lower())
            found.append(val)
            if len(found) >= limit:
                return found
    return found


def _sec_printer(zf, rep, ctx):
    for name in ctx["names"]:
        if "printersettings" not in name.lower() or not name.lower().endswith(".bin"):
            continue
        # _try, not a direct call: one bloated/broken printerSettings*.bin must not
        # suppress parsing of the others -- there can be several in a package.
        blob = _try(rep, name, _read_part, zf, name, 4 * 1024 * 1024)
        if not blob:
            continue
        device = _device_name(blob)
        rep.add(Risk.ENVIRONMENT, name, tr("dmDeviceName — printer name"),
                device or tr("<unparsed>"),
                tr("The printer name from the DEVMODE structure. It regularly contains a host "
                   "name, an office number, a city or an office name — that is, the physical "
                   "location of the author."))
        rep.add(Risk.ENVIRONMENT, name, tr("DEVMODE block"), tr("%d bytes") % len(blob),
                tr("A full snapshot of the print settings: driver, tray, paper format."))
        _try(rep, name, _devmode_details, rep, name, blob)


def _devmode_details(rep, name, blob):
    """DEVMODE fields beyond dmDeviceName. A separate function so that a broken
    field does not take with it the printer name already extracted (it was
    added above)."""
    form = _wchar32(blob, _DM_FORMNAME_OFF)
    paper = None
    if len(blob) >= _DM_PAPERSIZE_OFF + 2:
        paper = struct.unpack_from("<h", blob, _DM_PAPERSIZE_OFF)[0]

    region = None
    if form:
        region = _FORM_REGION.get(form.lower())
        rep.add(Risk.ENVIRONMENT, name, tr("dmFormName — paper format"), form,
                tr("The print form name. A non-standard value may have been set up by an "
                   "administrator and then carries the name of an organization or department; a "
                   "standard one reveals the region."))
    if region is None and paper in _PAPER_REGION:
        region = _PAPER_REGION[paper][1]
    if paper in _PAPER_REGION:
        rep.add(Risk.ENVIRONMENT, name, tr("dmPaperSize — paper size code"),
                "%d (%s)" % (paper, _PAPER_REGION[paper][0]),
                tr("The paper size code from the DEVMODE."))
    if region:
        rep.signal("dating",
                   tr("The paper format in the print settings (%s) points to a region: %s. An "
                      "indirect sign: the format is set from the system locale rather than "
                      "chosen deliberately.")
                   % (form or _PAPER_REGION.get(paper, ("?",))[0], tr(region)))

    if len(blob) >= _DM_DRIVER_EXTRA_OFF + 2:
        extra = struct.unpack_from("<H", blob, _DM_DRIVER_EXTRA_OFF)[0]
        if extra:
            rep.add(Risk.ENVIRONMENT, name, tr("dmDriverExtra — driver private data"),
                    tr("%d bytes") % extra,
                    tr("An opaque block of settings of a specific driver. The format is "
                       "undocumented and differs between drivers, so it is not fully parsed — "
                       "but the print queue, port and print server usually sit right here."))
    offset, tail = _devmode_tail(blob)
    for val in _tail_strings(tail):
        rep.add(Risk.ENVIRONMENT, name, tr("UNC path in the print driver data"), val,
                tr("Found in the driver's private block (offset %d). The print server and queue "
                   "name: this is a host name on your network.") % offset)


# === customXml ==============================================================
# Lives in the Excel section for historical reasons but has nothing to do with
# Excel: customXml/ can be in any OOXML package, and real .docx files have it
# more often.

# SharePoint/EDMS roots: this is where employee names, dates and the portal
# address live. The list must match core/clean_ooxml.py::_SP_ROOT_NS -- the
# cleaner decides from it what to zero out. A mismatch between the two lists
# makes the oracle give false positives: the inspector would declare a leak
# what the cleaner deliberately leaves alone, and a correctly cleaned file
# would get the verdict "not cleaned completely".
_CX_SHAREPOINT_NS = frozenset((
    "http://schemas.microsoft.com/office/2006/metadata/properties",
    "http://schemas.microsoft.com/office/2006/metadata/contentType",
    "http://schemas.microsoft.com/office/2006/metadata/longProperties",
    "http://schemas.microsoft.com/office/2006/metadata/customXsn",
    "http://schemas.microsoft.com/sharepoint/v3/contenttype/forms",
))

# Roots that mean document CONTENT, not environment metadata. A bibliography
# is the list of references: it is visible in the text and the reader sees
# it; removing it would spoil the document, and declaring it a leak would be
# a lie.
_CX_CONTENT_NS = frozenset((
    "http://schemas.openxmlformats.org/officeDocument/2006/bibliography",
))

# Public schema addresses: identical for all documents in the world and say
# nothing about the organization. A corporate URI in schemaRef, on the
# contrary, immediately gives away the portal.
_PUBLIC_SCHEMA_RE = re.compile(
    r"^(https?://)?(schemas\.(openxmlformats\.org|microsoft\.com)"
    r"|purl\.org|www\.w3\.org)/", re.I)


def _xml_values(root, limit=400):
    """Values of the part: the text between tags plus attribute values. Tag names
    and namespace declarations are dropped deliberately -- they are the same
    for all documents of this type, and a raw XML dump makes the oracle give
    false positives: any legitimately left part would count as a "value that
    survived cleaning". Attributes are needed though: contentTypeID with the
    site GUID lives exactly there."""
    out = []
    for el in root.iter():
        if not isinstance(el.tag, str):     # comments and PIs carry no values
            continue
        for key, val in el.attrib.items():
            name = _ln(key)
            if name in ("xmlns", "nil", "schemaLocation") or key.startswith("{http://www.w3.org/2000/xmlns/"):
                continue
            val = (val or "").strip()
            if val:
                out.append(val)
        for part in (el.text, el.tail):
            part = (part or "").strip()
            if part:
                out.append(part)
    return " ".join(out)[:limit]


def _customxml_item(zf, rep, name):
    """customXml/item*.xml. The risk is determined by the root namespace, not by
    the mere presence of the part: SharePoint is a leak, a bibliography is
    content."""
    # A local try even though the call is already wrapped in _try: for an
    # unparsed part a finding must still be emitted. Silence here would read as
    # "nothing suspicious", while the right reading is "not checked".
    try:
        root = _read_xml(zf, name, 2 * 1024 * 1024)
    except Exception as exc:  # noqa: BLE001
        rep.err("%s: %s" % (name, exc))
        root = None
    if root is None:
        rep.add(Risk.ENVIRONMENT, name, tr("customXml data"), tr("not parsed as XML"),
                tr("A custom XML part that could not be parsed. The content was not checked -- "
                   "we assume the worst case."))
        return
    ns = (etree.QName(root).namespace or "").strip()
    text = _xml_values(root)
    n_el = sum(1 for _ in root.iter())

    if ns in _CX_SHAREPOINT_NS:
        # An empty SharePoint part is not a leak but provenance. The split is
        # essential: the IDENTITY and ENVIRONMENT risks are checked by the oracle by
        # matching values, and a placeholder word instead of a value would match
        # itself -- a correctly cleaned file would get the verdict "value survived
        # cleaning".
        if text:
            rep.add(Risk.ENVIRONMENT, name, tr("customXml data (SharePoint/EDMS)"), text,
                    tr("SharePoint library columns: employee names, dates, the portal address "
                       "and the organization's content type."))
        else:
            rep.add(Risk.PROVENANCE, name, tr("SharePoint/EDMS part (empty)"),
                    tr("no values"),
                    tr("There are no values inside, but the part itself remained: the document "
                       "lived in a SharePoint library or in an EDMS."),
                    removable=False)
    elif ns in _CX_CONTENT_NS:
        rep.add(Risk.STRUCTURAL, name, tr("Bibliography (customXml)"),
                tr("elements: %d") % n_el,
                tr("The document's list of references. This is content visible to the reader, "
                   "not metadata: cleaning deliberately leaves it alone. The author names here "
                   "are the authors of the cited works, not yours."),
                removable=False)
    elif not text:
        # The root is unknown but there are no values inside: worth reporting, nothing to be scared of.
        rep.add(Risk.PROVENANCE, name, tr("Custom XML part (empty)"),
                ns or tr("no namespace"),
                tr("The part exists, there are no values inside. The mere fact of its presence "
                   "tells about the application or system that created the file."))
    else:
        rep.add(Risk.ENVIRONMENT, name, tr("customXml data"), text,
                tr("A custom XML part of unknown origin (%s). This is how EDMS data and "
                   "content-control bindings are stored; it cannot be removed blindly -- the "
                   "document text may depend on it.") % (ns or tr("no namespace")))


def _customxml_props(zf, rep, name):
    """customXml/itemProps*.xml -- a binding to a schema. It leaks only when the
    schema address is corporate: the public OOXML URIs are everywhere."""
    try:
        root = _read_xml(zf, name, 2 * 1024 * 1024)
    except Exception as exc:  # noqa: BLE001
        rep.err("%s: %s" % (name, exc))
        root = None
    refs = []
    if root is not None:
        for el in _iter_tag(root, "schemaRef"):
            uri = (_attr(el, "uri") or "").strip()
            if uri:
                refs.append(uri)
    private = [r for r in refs if not _PUBLIC_SCHEMA_RE.match(r)]
    if private:
        rep.add(Risk.ENVIRONMENT, name, tr("customXml schema"), "; ".join(private),
                tr("A binding to a non-standard schema: reveals the SharePoint portal or the "
                   "organization's content type."))
    elif refs:
        rep.add(Risk.STRUCTURAL, name, tr("customXml schema (standard)"),
                "; ".join(refs),
                tr("A public OOXML schema address. The same for all documents, says nothing "
                   "about the organization."), removable=False)
    else:
        rep.add(Risk.PROVENANCE, name, tr("customXml schema"), tr("no schemaRef"),
                tr("The schema binding part exists, but there is no reference in it."))


# === Excel specifics ========================================================


def _sec_excel(zf, rep, ctx):
    names = ctx["nameset"]

    if "xl/workbook.xml" in names:
        # _try everywhere below: the Excel-specific parts are independent of each
        # other, and a bloated/broken workbook.xml must not suppress parsing of
        # connections.xml, pivotCache, externalLinks and customXml, which come next.
        root = _try(rep, "xl/workbook.xml", _read_xml, zf, "xl/workbook.xml")
        if root is not None:
            for el in _iter_tag(root, "fileVersion"):
                parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
                rep.add(Risk.PROVENANCE, "xl/workbook.xml",
                        tr("fileVersion — Excel version and build"), "; ".join(parts),
                        tr("rupBuild gives the exact Excel build, lastEdited and lowestEdited — "
                           "the range of versions in which the workbook was edited."))
            for el in _iter_tag(root, "workbookPr"):
                parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
                if parts:
                    rep.add(Risk.PROVENANCE, "xl/workbook.xml",
                            "workbookPr", "; ".join(parts),
                            tr("filterPrivacy=1 means the workbook was already marked as "
                               "containing personal data."))
            # The cleaner has removed these two places for a long time, but the
            # inspector did not see them: the report was silent about the most common
            # Excel leak, and the oracle had nothing to check against.
            for el in _iter_tag(root, "absPath"):
                rep.add_nonempty(Risk.ENVIRONMENT, "xl/workbook.xml",
                                 tr("x15ac:absPath — folder where the workbook was saved"), _attr(el, "url"),
                                 tr("Excel silently writes the full path of the workbook's "
                                    "folder, usually C:\\Users\\NAME\\...: it is not visible in "
                                    "the document properties."))
            for el in _iter_tag(root, "fileSharing"):
                rep.add_nonempty(Risk.IDENTITY, "xl/workbook.xml",
                                 tr("fileSharing — who reserved the workbook"), _attr(el, "userName"),
                                 tr("The name of the user who turned on 'read-only recommended' "
                                    "or a password to modify."))
            hidden = []
            for el in _iter_tag(root, "sheet"):
                state = (_attr(el, "state") or "").lower()
                if state in ("hidden", "veryhidden"):
                    hidden.append("%s (%s)" % (_attr(el, "name") or "?", state))
            if hidden:
                rep.add(Risk.PROVENANCE, "xl/workbook.xml", tr("Hidden sheets"),
                        "; ".join(hidden[:MAX_LISTED]),
                        tr("Hidden and very hidden sheets usually contain draft calculations and "
                           "source data."))
            for el in _iter_tag(root, "definedName"):
                value = _text(el)
                if _is_path(value) or "[" in value:
                    rep.add(Risk.ENVIRONMENT, "xl/workbook.xml",
                            "definedName %s" % (_attr(el, "name") or "?"), value,
                            tr("A named range refers to an external workbook — often with a full "
                               "local path."))

    for name in ctx["names"]:
        if re.match(r"^xl/pivotCache/pivotCacheDefinition\d*\.xml$", name):
            root = _try(rep, name, _read_xml, zf, name)
            if root is None:
                continue
            who = _attr(root, "refreshedBy")
            when = _attr(root, "refreshedDate")
            if who is not None:
                rep.add(Risk.IDENTITY, name, tr("refreshedBy — who refreshed the pivot"),
                        who,
                        tr("A classic Excel leak: the name of the person who refreshed the pivot "
                           "table cache sits outside docProps and survives the cleaning of the "
                           "document properties."))
            if when is not None:
                rep.add(Risk.PROVENANCE, name, tr("refreshedDate — when it was refreshed"),
                        when, tr("The Excel serial date of the cache refresh."))
            for key in ("createdVersion", "refreshedVersion", "minRefreshableVersion"):
                val = _attr(root, key)
                if val is not None:
                    rep.add(Risk.PROVENANCE, name, key, val,
                            tr("The Excel version that worked with the pivot."))
            for el in _iter_tag(root, "worksheetSource", "webPr"):
                parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
                if parts:
                    rep.add(Risk.ENVIRONMENT, name,
                            tr("Cache source (%s)") % _ln(el.tag), "; ".join(parts),
                            tr("Where the pivot takes its data from: a workbook, a sheet or a "
                               "URL."))

    if "xl/connections.xml" in names:
        root = _try(rep, "xl/connections.xml", _read_xml, zf, "xl/connections.xml")
        if root is not None:
            for el in _iter_tag(root, "connection"):
                head = "; ".join(
                    "%s=%s" % (_ln(k), v) for k, v in el.attrib.items()
                    if _ln(k) in ("name", "description", "odcFile", "type",
                                  "credentials", "sourceFile")
                )
                rep.add(Risk.ENVIRONMENT, "xl/connections.xml",
                        tr("Data connection"), head,
                        tr("The name and description of an external connection."))
                for kid in el.iter():
                    for attr in ("connection", "command", "url", "sourceFile",
                                 "serverCommand"):
                        val = _attr(kid, attr)
                        if val:
                            rep.add(Risk.ENVIRONMENT, "xl/connections.xml",
                                    "%s/@%s" % (_ln(kid.tag), attr), val,
                                    tr("An ODBC/OLEDB connection string or a query: contains the "
                                       "server address, the database name and the login."))

    for name in ctx["names"]:
        if re.match(r"^xl/externalLinks/externalLink\d*\.xml$", name):
            root = _try(rep, name, _read_xml, zf, name)
            target = None
            for el in _iter_tag(root, "externalBook") if root is not None else []:
                target = _resolve(ctx, name, _attr(el, "id"))
            sheets = []
            if root is not None:
                for el in _iter_tag(root, "sheetName"):
                    sheets.append(_attr(el, "val") or "")
            rep.add(Risk.ENVIRONMENT, name, tr("External workbook link"),
                    target or tr("target not resolved"),
                    tr("The path to an external Excel file, often a full local or UNC one. "
                       "Sheets: %s") % (", ".join(s for s in sheets if s)[:200]))
        elif re.match(r"^xl/queryTables/.*\.xml$", name):
            rep.add(Risk.ENVIRONMENT, name, tr("Query table (Power Query)"),
                    tr("present"),
                    tr("Power Query stores the data source and the path to it."))
        elif name.startswith("customXml/"):
            if "itemProps" in name:
                _try(rep, name, _customxml_props, zf, rep, name)
            elif re.match(r"^customXml/item\d*\.xml$", name):
                _try(rep, name, _customxml_item, zf, rep, name)


# === analysis of missing parts ==============================================


def _sec_missing(zf, rep, ctx):
    if ctx["fmt"] != "docx":
        return
    app = ctx["props"]["app"].get("Application", "")
    missing = [p for p in WORD_REQUIRED if p not in ctx["nameset"]]
    if missing:
        rep.add(Risk.PROVENANCE, "ZIP", tr("Required Word parts are missing"),
                ", ".join(missing),
                tr("Word always puts these parts into a .docx. They are missing — the package "
                   "was built programmatically."))
    no_rsid = (ctx.get("rsid_count", 0) == 0 and ctx.get("rsid_attrs", 0) == 0)
    if no_rsid:
        rep.add(Risk.PROVENANCE, "word/", tr("rsid entirely absent"), "0",
                tr("Not a single edit session identifier: Word does not write that."))

    if missing or no_rsid:
        conf = "high" if (missing and no_rsid) else "medium"
        why = []
        if missing:
            why.append(tr("missing parts: %s") % ", ".join(missing))
        if no_rsid:
            why.append(tr("no rsid at all"))
        suite = _stock_suite(app)
        if suite:
            # Another office suite -- not a library and not an AI generator. Its files
            # legitimately lack Word's parts and rsids.
            rep.signal("producer",
                       tr("The package was built in %s, not in Word: %s. This is normal for that "
                          "suite.") % (suite, "; ".join(why)), "high")
        else:
            detail = (tr("The package was built by a library (python-docx/docxtpl or an AI "
                         "generator), not by Word: %s.") % "; ".join(why))
            if app:
                detail += tr(" Meanwhile app.xml claims Application=%s.") % app
            rep.signal("ai", detail, conf)


# === conclusions and contradictions =========================================


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sec_conclusions(zf, rep, ctx):
    core = ctx["props"]["core"]
    app = ctx["props"]["app"]
    appname = app.get("Application", "")
    words = _to_int(app.get("Words")) or 0
    total = _to_int(app.get("TotalTime"))
    rev = _to_int(core.get("revision"))

    if appname:
        rep.signal("producer",
                   "Application=%s, AppVersion=%s"
                   % (appname, app.get("AppVersion", "?")), "high")

    ms = any(w in appname.lower()
             for w in ("microsoft", "word", "excel", "powerpoint"))

    if ms and ctx.get("zip_real"):
        rep.signal("inconsistent",
                   tr("app.xml claims a Microsoft product (%s), but the ZIP entries are stamped "
                      "with a real time (the latest is %s) instead of 1980-01-01. Office does "
                      "not save like that.")
                   % (appname, ctx.get("zip_real_max", "?")), "high")
        rep.signal("ai",
                   tr("The package was rebuilt by a library on top of an Office template: the "
                      "real save time leaked through the ZIP timestamps."),
                   "high")

    # A match between created and modified means NOTHING BY ITSELF, and there
    # used to be a false positive here on genuine files. Two reasons, both
    # verified on a file from Word 16:
    #   * Word writes these dates to the MINUTE -- the seconds are always 00.
    #     So for Word "match to the second" only means "created and saved
    #     within one minute", which is routine for any document made in one
    #     sitting.
    #   * cp:revision is no good as evidence: Word counted 2 revisions over four
    #     consecutive saves, and all of them fell within the same minute.
    # Only TotalTime gives a real contradiction: if N minutes of active editing
    # are claimed while the whole life of the document fit into one minute, one
    # of the two is untrue. The threshold of 3 minutes leaves slack for minutes
    # being rounded up and down at the boundary.
    created = core.get("created", "")
    modified = core.get("modified", "")
    if (created and modified and created == modified
            and total is not None and total >= 3):
        rep.signal("inconsistent",
                   tr("dcterms:created and dcterms:modified match (%s), meaning the document was "
                      "created and saved within one minute, while TotalTime claims %d minutes of "
                      "editing. One of the two is untrue: usually the dates were substituted.")
                   % (created, total), "high")

    if words >= 200 and (total is None or total == 0):
        rep.signal("inconsistent",
                   tr("The document has %d words, but TotalTime %s: the editing time was zeroed "
                      "or never set.")
                   % (words, tr("is absent") if total is None else tr("is 0")),
                   "medium")

    if rev == 1 and words >= 200:
        rep.signal("inconsistent",
                   tr("cp:revision=1 with a volume of %d words: a document of that size is not "
                      "made in a single save.") % words, "medium")

    scrub_bits = []
    if "docProps/core.xml" in ctx["nameset"]:
        if not core.get("creator"):
            scrub_bits.append(tr("empty dc:creator"))
        if not core.get("lastModifiedBy"):
            scrub_bits.append(tr("empty cp:lastModifiedBy"))
    if not app.get("Company") and "Company" in app:
        scrub_bits.append(tr("empty Company"))
    if len(scrub_bits) >= 2:
        # An asymmetry that cannot be ignored: Word asks for a name at first launch,
        # while LibreOffice is installed with EMPTY user data and writes an empty
        # author by default. For such a package empty fields are the normal state,
        # not a trace of cleaning, hence the low confidence.
        _suite = _stock_suite(app.get("Application", ""))
        if _suite:
            rep.signal("scrubbed",
                       tr("Empty identity fields (%s). For %s this is the default state: the "
                          "suite is installed with blank user data, so this is not a trace of "
                          "cleaning.")
                       % (", ".join(scrub_bits), _suite), "low")
        else:
            rep.signal("scrubbed",
                   tr("Signs of earlier cleaning: %s. Check the other places (ZIP, settings.xml, "
                      "rels) — they are usually forgotten.")
                   % ", ".join(scrub_bits), "medium")


# === optional exiftool enrichment ===========================================


def _exiftool_path():
    """exiftool is strictly optional: if it is absent we work without it and stay quiet."""
    found = shutil.which("exiftool")
    if found:
        return found
    local = os.path.join(os.getcwd(), "tools", "exiftool.exe")
    return local if os.path.isfile(local) else None


def _sec_exiftool(path, rep, ctx):
    exe = _exiftool_path()
    if not exe:
        return
    try:
        out = subprocess.run(
            [exe, "-j", "-a", "-G1", "-n", str(path)],
            capture_output=True, timeout=25, shell=False,
        )
    except Exception as exc:  # noqa: BLE001
        rep.err(tr("exiftool failed: %s") % exc)
        return
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return
    if not data:
        return
    skip_groups = ("ExifTool", "System", "File")
    added = 0
    for key, value in data[0].items():
        if added >= MAX_LISTED:
            break
        group, _, tag = key.partition(":")
        if not tag or group in skip_groups:
            continue
        text = str(value)
        risk = Risk.PROVENANCE
        # \b, not a bare substring: otherwise "CreatorTool" (the name of an
        # APPLICATION, the XMP tag CreatorTool) is caught by "creator" and goes to
        # IDENTITY, although it is pure PROVENANCE.
        if _emails(text) or re.search(r"\b(author|creator|owner|manager)\b", tag, re.I):
            risk = Risk.IDENTITY
        elif _is_path(text):
            risk = Risk.ENVIRONMENT
        rep.add(risk, "exiftool:%s" % group, tag, text,
                tr("Added by exiftool (optional enrichment)."))
        added += 1


# === entry point ============================================================


def inspect_ooxml(path, fmt="docx") -> Report:
    """Inspect an OOXML package. Never raises outward.

    Any trouble with a package part goes to report.errors, not to a traceback:
    we are sent both damaged and deliberately malformed files.
    """
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)
    except OSError as exc:
        rep.err(tr("Could not get the file size: %s") % exc)

    try:
        zf = zipfile.ZipFile(path, "r")   # strictly read-only
    except Exception as exc:  # noqa: BLE001
        rep.err(tr("The ZIP container cannot be opened: %s: %s") % (type(exc).__name__, exc))
        return rep

    ctx = {"fmt": fmt, "names": [], "nameset": set(), "safe": True,
           "props": {"core": {}, "app": {}, "custom": []}, "rels": {}}
    try:
        with zf:
            _section(rep, tr("ZIP container"), _sec_zip, zf, rep, ctx)
            if ctx.get("safe", True):
                ctx["props"] = parse_props(zf)
                _section(rep, "docProps/core.xml", _sec_core, zf, rep, ctx)
                _section(rep, "docProps/app.xml", _sec_app, zf, rep, ctx)
                _section(rep, "docProps/custom.xml", _sec_custom, zf, rep, ctx)
                _section(rep, tr("Preview"), _sec_thumbnail, zf, rep, ctx)
                _section(rep, tr("Relationships (.rels)"), _sec_rels, zf, rep, ctx)
                _section(rep, "word/settings.xml", _sec_settings, zf, rep, ctx)
                _section(rep, tr("Document body"), _sec_document, zf, rep, ctx)
                _section(rep, tr("Comments and authors"), _sec_people, zf, rep, ctx)
                _section(rep, tr("Media and attachments"), _sec_media, zf, rep, ctx)
                _section(rep, tr("Printer settings"), _sec_printer, zf, rep, ctx)
                _section(rep, tr("Excel specifics"), _sec_excel, zf, rep, ctx)
                _section(rep, tr("Missing parts"), _sec_missing, zf, rep, ctx)
                _section(rep, tr("Conclusions"), _sec_conclusions, zf, rep, ctx)
    except Exception as exc:  # noqa: BLE001
        rep.err(tr("Unexpected inspection failure: %s: %s") % (type(exc).__name__, exc))

    _section(rep, "exiftool", _sec_exiftool, path, rep, ctx)
    return rep


# === self-test ==============================================================


def _build_sample(dest):
    """Build a synthetic .docx with known values."""
    import datetime

    now = datetime.datetime.now().timetuple()[:6]
    # A plausible DEVMODEW: dmDeviceName, dmSize, dmDriverExtra, dmPaperSize and
    # dmFormName are filled in, plus the driver's private tail with a UNC path --
    # otherwise there is nothing to check the parsing of fields beyond the
    # printer name against.
    _dm_tail = b"\x00\x00" + "\\\\PRINTSRV01\\HP4050-BUH".encode("utf-16le")
    _devmode_buf = bytearray(_DEVMODEW_SIZE)
    _dev = "HP LaserJet M404 (BUH-01)".encode("utf-16le")
    _devmode_buf[0:len(_dev)] = _dev
    struct.pack_into("<H", _devmode_buf, _DM_SIZE_OFF, _DEVMODEW_SIZE)
    struct.pack_into("<H", _devmode_buf, _DM_DRIVER_EXTRA_OFF, len(_dm_tail))
    struct.pack_into("<h", _devmode_buf, _DM_PAPERSIZE_OFF, 9)   # A4
    _form = "A4".encode("utf-16le")
    _devmode_buf[_DM_FORMNAME_OFF:_DM_FORMNAME_OFF + len(_form)] = _form
    devmode = bytes(_devmode_buf) + _dm_tail

    parts = [
        ("[Content_Types].xml",
         b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
         b'/package/2006/content-types"/>'),
        ("_rels/.rels",
         b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
         b'.org/package/2006/relationships"><Relationship Id="rId1" Type="t" '
         b'Target="word/document.xml"/></Relationships>'),
        ("docProps/core.xml",
         '<?xml version="1.0"?><cp:coreProperties '
         'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
         'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
         'xmlns:dcterms="http://purl.org/dc/terms/">'
         '<dc:creator>Иванов И.И.</dc:creator>'
         '<cp:lastModifiedBy>petrov@company.ru</cp:lastModifiedBy>'
         '<dcterms:created>2024-03-01T10:00:00Z</dcterms:created>'
         '<dcterms:modified>2024-03-01T10:00:00Z</dcterms:modified>'
         '<cp:revision>1</cp:revision><dc:title></dc:title>'
         '<dc:language>ru-RU</dc:language>'
         '</cp:coreProperties>'),
        ("docProps/app.xml",
         '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats'
         '.org/officeDocument/2006/extended-properties">'
         '<Application>Microsoft Office Word</Application>'
         '<AppVersion>16.0000</AppVersion><Company>ООО Ромашка</Company>'
         '<Manager>Сидоров С.С.</Manager><TotalTime>0</TotalTime>'
         '<Words>1500</Words><Template>Normal.dotm</Template>'
         '<TitlesOfParts>Отчёт за квартал</TitlesOfParts></Properties>'),
        ("docProps/custom.xml",
         '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats'
         '.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas'
         '.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
         '<property fmtid="{f}" pid="2" name="MSIP_Label_9f-1_SetBy">'
         '<vt:lpwstr>a.smirnova@company.ru</vt:lpwstr></property>'
         '<property fmtid="{f}" pid="3" name="MSIP_Label_9f-1_SiteId">'
         '<vt:lpwstr>72f988bf-86f1-41af-91ab-2d7cd011db47</vt:lpwstr></property>'
         '<property fmtid="{f}" pid="4" name="ContentTypeId">'
         '<vt:lpwstr>0x0101009ABC</vt:lpwstr></property></Properties>'),
        ("word/document.xml",
         '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
         '.org/wordprocessingml/2006/main"><w:body>'
         '<w:p w:rsidR="00A12B34" w:rsidRDefault="00A12B34">'
         '<w:ins w:id="1" w:author="Кузнецова А.В." w:date="2024-03-01T09:30:00Z">'
         '<w:r><w:t>правка</w:t></w:r></w:ins></w:p>'
         '<w:bookmarkStart w:id="2" w:name="_GoBack"/>'
         '</w:body></w:document>'),
        ("word/settings.xml",
         '<?xml version="1.0"?><w:settings xmlns:w="http://schemas.openxmlformats'
         '.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats'
         '.org/officeDocument/2006/relationships">'
         '<w:attachedTemplate r:id="rId1"/>'
         '<w:documentProtection w:edit="readOnly" w:hash="Aq3f==" w:salt="Zz09"/>'
         '<w:rsids><w:rsidRoot w:val="00A12B34"/><w:rsid w:val="00A12B34"/>'
         '<w:rsid w:val="00B55C21"/></w:rsids></w:settings>'),
        ("word/_rels/settings.xml.rels",
         '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
         '.org/package/2006/relationships"><Relationship Id="rId1" Type="t" '
         'Target="file:///C:/Users/testuser/AppData/Roaming/Microsoft/'
         'Templates/Otchet.dotm" TargetMode="External"/></Relationships>'),
        ("word/people.xml",
         '<?xml version="1.0"?><w15:people xmlns:w15="http://schemas.microsoft'
         '.com/office/word/2012/wordml"><w15:person w15:author="Кузнецова А.В.">'
         '<w15:presenceInfo w15:providerId="AD" '
         'w15:userId="a.kuznetsova@company.ru"/></w15:person></w15:people>'),
        ("word/printerSettings/printerSettings1.bin", devmode),
        # All four customXml places at once: the risk depends on the root, and this
        # is exactly where a classification mistake poisons the cleaning oracle (see
        # _customxml_item).
        ("customXml/item1.xml",
         '<?xml version="1.0"?><p:properties xmlns:p="http://schemas.microsoft'
         '.com/office/2006/metadata/properties" xmlns:ma="http://schemas'
         '.microsoft.com/office/2006/metadata/properties/metaAttributes" '
         'ma:contentTypeID="0x010100DEADBEEF"><documentManagement>'
         '<Reviewer>Кузнецова А.В.</Reviewer></documentManagement>'
         '</p:properties>'),
        ("customXml/itemProps1.xml",
         '<?xml version="1.0"?><ds:datastoreItem xmlns:ds="http://schemas'
         '.openxmlformats.org/officeDocument/2006/customXml" '
         'ds:itemID="{00000001-AAAA-BBBB-CCCC-DDDDEEEEFFFF}"><ds:schemaRefs>'
         '<ds:schemaRef ds:uri="http://sharepoint.romashka.local/sites/buh"/>'
         '</ds:schemaRefs></ds:datastoreItem>'),
        ("customXml/item2.xml",
         '<?xml version="1.0"?><b:Sources xmlns:b="http://schemas'
         '.openxmlformats.org/officeDocument/2006/bibliography"/>'),
        ("customXml/itemProps2.xml",
         '<?xml version="1.0"?><ds:datastoreItem xmlns:ds="http://schemas'
         '.openxmlformats.org/officeDocument/2006/customXml" '
         'ds:itemID="{00000002-AAAA-BBBB-CCCC-DDDDEEEEFFFF}"><ds:schemaRefs>'
         '<ds:schemaRef ds:uri="http://schemas.openxmlformats.org/'
         'officeDocument/2006/bibliography"/></ds:schemaRefs>'
         '</ds:datastoreItem>'),
        ("customXml/item3.xml",
         '<?xml version="1.0"?><Root xmlns="urn:romashka:erp:card"/>'),
        ("word/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40),
        ("word/styles.xml", b'<?xml version="1.0"?><x/>'),
    ]

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts:
            if isinstance(body, str):
                body = body.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=now)  # real time => signal
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)
    return dest


def demo():
    """Run on a synthetic .docx with known values."""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ooxml_insp_")
    sample = _build_sample(os.path.join(tmp, "sample.docx"))
    rep = inspect_ooxml(sample, "docx")

    blob = " | ".join("%s=%s" % (f.label, f.value) for f in rep.findings)
    for expect in ("Иванов И.И.", "petrov@company.ru", "ООО Ромашка",
                   "Сидоров С.С.", "a.smirnova@company.ru",
                   "72f988bf-86f1-41af-91ab-2d7cd011db47", "ContentTypeId",
                   "HP LaserJet M404 (BUH-01)", "Otchet.dotm",
                   "a.kuznetsova@company.ru", "Кузнецова А.В.",
                   "_GoBack", "00A12B34",
                   # DEVMODE beyond the printer name
                   "A4 210x297mm", "\\\\PRINTSRV01\\HP4050-BUH"):
        assert expect in blob, "not found: %s" % expect
    kinds = {s.kind for s in rep.signals}
    assert "inconsistent" in kinds and "producer" in kinds, kinds
    assert "dating" in kinds, "paper format A4 must produce a dating signal: %s" % kinds
    assert not rep.errors, rep.errors

    # customXml: the risk is determined by the root. Only what the cleaner must
    # remove counts as a leak (and hence as a subject of the oracle check in
    # core/clean.py).
    by_loc = {f.location: f for f in rep.findings if f.location.startswith("customXml/")}
    assert len(by_loc) == 5, sorted(by_loc)
    cx = by_loc["customXml/item1.xml"]
    assert cx.risk is Risk.ENVIRONMENT, cx
    assert "Кузнецова А.В." in cx.value, cx.value
    assert "0x010100DEADBEEF" in cx.value, (
        "SharePoint values can sit in attributes, not only in text: %s" % cx.value)
    assert by_loc["customXml/itemProps1.xml"].risk is Risk.ENVIRONMENT, "corporate schema URI"
    assert by_loc["customXml/item2.xml"].risk is Risk.STRUCTURAL, (
        "the bibliography is document content, the cleaner does not remove it")
    assert by_loc["customXml/itemProps2.xml"].risk is Risk.STRUCTURAL, (
        "the public OOXML schema address says nothing about the organization")
    assert by_loc["customXml/item3.xml"].risk is Risk.PROVENANCE, (
        "an empty part of unknown origin is provenance, not a leak")

    print("File: %s  (%d bytes, fmt=%s)" % (rep.path, rep.size, rep.fmt))
    print("ZIP parts: %d | findings: %d (significant: %d) | signals: %d | errors: %d"
          % (len(rep.parts), len(rep.findings), rep.sensitive,
             len(rep.signals), len(rep.errors)))
    print("Sample of a parts entry: %s" % (rep.parts[0],))
    print("-" * 78)
    for risk, items in rep.by_risk().items():
        if not items:
            continue
        print("[%s]" % risk.value.upper())
        for f in items:
            print("  %-46s %s%s" % (
                f.label[:46], f.value or "<empty>",
                "" if f.removable else "  (cannot be removed)"))
    print("-" * 78)
    for s in rep.signals:
        print("SIGNAL %-13s %-6s %s" % (s.kind, s.confidence, s.detail))
    print("OK: self-test passed")


if __name__ == "__main__":
    demo()
