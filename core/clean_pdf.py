# -*- coding: utf-8 -*-
"""PDF cleaning (phase 1). The tools are pikepdf (QPDF) and lxml, no external binaries.

    clean_pdf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult

The inspector is the oracle: after cleaning, inspect_file(dst) is run and it alone
decides whether the file is clean. Our own lists of actions are not proof.

What was established BY EXPERIMENT on pikepdf 10.15 / libqpdf 12.4 (not from the
documentation):

* A plain save() does NOT insert its own /Producer. pdf.open_metadata() with
  default arguments does: it writes /Producer = "pikepdf X", pdf:Producer and
  xmp:MetadataDate=now. So the XMP is edited as a raw stream through lxml and
  open_metadata is never called. After save() we still compare Creator/Producer
  with what we left ourselves: a file whose cleaning has become a fingerprint
  must not be handed over.
* A full rewrite IS garbage collection: QPDF writes only the objects reachable
  from the trailer, so earlier revisions (and the text under a "cover-up")
  are gone even WITHOUT linearize. And linearize=True is harmful: a /Prev
  appears in the first xref section (the inspector reads it as an "incremental
  update"), hint tables are added, and so is the "Linearized" sign -- the
  cleaning itself would become a fingerprint. So we save without linearization.
* A raw count of the /Prev substring (the inspector's and our own check) lies on
  files with bookmarks: the /Outlines links (/Prev-/Next) are /Prev too. The
  real criterion is /Prev in the TRAILER, and that is what we check (pdf.trailer).
* QPDF keeps the source's /ID[0] and regenerates only /ID[1]; in many producers
  ID[0] is derived from the file path, so it links files.
* QPDF writes the binary marker %\xbf\xf7\xa2\xfe into the header -- a well-known
  qpdf signature; we replace it with the source file's marker of equal length.

The input is read only: pikepdf.open(src) without allow_overwriting_input, the
output is written to dst + ".part" and renamed only after verification.
Privacy: neither file names nor finding values are written to logs (there are no logs).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zlib

import pikepdf
from lxml import etree

from .clean_image import _clean_jpeg
from .cleanmodel import PROFILE_REMOVES, Act, CleanResult, Profile
from .insp_pdf import (RDF, _XMP_IDENTITY, _flatten, _info_risk, _looks_like_path, _qname, _s,
                       _walk_name_tree, _xmp_fromstring, _xmp_value_risk, is_jpeg_image)
from .model import Risk
from core.i18n import N_, tr

# --- limits: cleaning must process EVERYTHING or refuse (a partly clean file is
# worse than a refusal), so the limits are more generous than the inspector's
# and exceeding one means ok=False ----
MAX_INPUT = 512 * 1024 * 1024
MAX_PAGES = 20000
MAX_ANNOTS = 200000
MAX_FIELDS = 20000
MAX_XMP = 4 * 1024 * 1024            # decompressed XMP larger than this is not parsed
MAX_IMAGES = 20000

_WEB = re.compile(r"(?i)^(https?|mailto|tel):")
_ACTIVE = {"/Launch", "/JavaScript", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE"}
_DROP_NS = {"xmpMM", "photoshop", "illustrator", "xmpRights", "xmpBJ"}   # entirely, in any profile
_QPDF_MARK = b"\xbf\xf7\xa2\xfe"
_ADOBE_MARK = b"\xe2\xe3\xcf\xd3"
_XPACKET = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n%s\n<?xpacket end="w"?>'
_EMPTY_XMP = _XPACKET % (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>')

_NOTE_APP = (N_("STEALTH: left as it was. The file really was made by this application, and a "
                "Creator=Word + Producer=qpdf pair immediately shows that the file was processed."))
_NOTE_DATE = (N_("STEALTH: dates are left alone. CreationDate == ModDate (zeroing to the same "
                 "second) is a sign of scrubbing, and faking a date falsifies provenance."))

# Structural: not removed in any profile.
_STRUCT = (
    ("/StructTreeRoot", N_("Accessibility tags (tagged PDF): without them a screen reader cannot "
                           "read the document.")),
    ("/MarkInfo", N_("Tagging marker; goes together with /StructTreeRoot.")),
    ("/Lang", N_("Document language: needed by screen readers and for hyphenation.")),
    ("/Outlines", N_("Table of contents (bookmarks): navigation through the document.")),
    ("/PageLabels", N_("Custom page numbering.")),
    ("/ViewerPreferences", N_("How to open the document; harmless.")),
    ("/OutputIntents", N_("Print color profile (ICC): without it color reproduction breaks.")),
)


class _Refuse(Exception):
    """Cleaning is impossible or unsafe: ok=False, the text goes to errors."""


class _C:
    """Cleaning context: the result, the profile, the user's whitelist."""

    def __init__(self, res, profile, keep):
        self.res = res
        self.profile = profile
        self.paranoid = profile is Profile.PARANOID
        self.removes = PROFILE_REMOVES[profile]
        if isinstance(keep, str):
            keep = [keep]
        self.keep = {str(k).strip().casefold() for k in (keep or ())}
        self.ua = False          # XMP declares PDF/UA: the title is mandatory

    def held(self, location, label, before=""):
        """The user said to keep this field: record KEPT and leave it alone."""
        if self.keep and (label.casefold() in self.keep or location.casefold() in self.keep):
            self.res.act(Act.KEPT, location, label, before, note=tr("kept per the user's "
                                                                    "whitelist (keep)"))
            return True
        return False


def _pop(c, holder, key, where, before=None, note="", label=None):
    """Remove holder[key] unless the user said to keep it. True means removed."""
    if key not in holder:
        return False
    label = label or key
    if before is None:
        before = _s(holder.get(key))
    if c.held(where, label, before):
        return False
    del holder[key]
    c.res.act(Act.REMOVED, where, label, before, note=note)
    return True


def _del(d, key):
    if key in d:
        del d[key]


def _step(c, name, fn, *args):
    """One cleaning section: a failure is recorded in errors and FAILED, the others carry on."""
    try:
        fn(*args)
    except _Refuse:
        raise
    except Exception as exc:  # noqa: BLE001 -- we are sent broken files on purpose
        c.res.act(Act.FAILED, name, name, note=tr("section failed (%s), details in errors") % type(exc).__name__)
        c.res.err(tr("Section \"%s\": %s: %s") % (name, type(exc).__name__, exc))


# ============================================================================
#  Walking the structures (guarded against cycles and DAG bombs)
# ============================================================================


def _iter_fields(form):
    """AcroForm fields: (full name, field, inherited /FT). Iterative, no recursion."""
    fields = form.get("/Fields")
    if not isinstance(fields, pikepdf.Array):
        return
    stack = [(f, "", "") for f in reversed(list(fields))]
    seen, n = set(), 0
    while stack and n < MAX_FIELDS:
        f, prefix, ft = stack.pop()
        if not isinstance(f, pikepdf.Dictionary):
            continue
        gen = f.objgen
        if gen != (0, 0):
            if gen in seen:
                continue
            seen.add(gen)
        n += 1
        name = _s(f.get("/T"))
        full = "%s.%s" % (prefix, name) if prefix and name else (name or prefix)
        ft = _s(f.get("/FT")) or ft
        yield full, f, ft
        kids = f.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            stack.extend((k, full, ft) for k in reversed(list(kids)))


def _filled_signatures(pdf):
    """Filled signatures: [(field, signer, field object)] and the /Perms /DocMDP flag.
    An empty field placeholder (/FT /Sig without /V) does NOT count as a signature."""
    out = []
    form = pdf.Root.get("/AcroForm")
    if isinstance(form, pikepdf.Dictionary):
        for name, f, ft in _iter_fields(form):
            v = f.get("/V")
            if ft == "/Sig" and v is not None:
                signer = _s(v.get("/Name")) if isinstance(v, pikepdf.Dictionary) else ""
                out.append((name, signer, f))
    perms = pdf.Root.get("/Perms")
    docmdp = isinstance(perms, pikepdf.Dictionary) and perms.get("/DocMDP") is not None
    return out, docmdp


def _file_label(spec, fallback=""):
    """Name and size of an attachment from a filespec."""
    if not isinstance(spec, pikepdf.Dictionary):
        return fallback or "?"
    name = _s(spec.get("/UF")) or _s(spec.get("/F")) or fallback or "?"
    try:
        ef = spec.get("/EF")
        st = ef.get("/F")
        if st is None:
            st = ef.get("/UF")
        size = _s(st.get("/Params").get("/Size")) or str(len(st.read_raw_bytes()))
        return tr("%s (%s bytes)") % (name, size)
    except Exception:
        return name


# ============================================================================
#  A. /Info
# ============================================================================


def _sec_info(pdf, c):
    info = pdf.trailer.get("/Info")      # NOT pdf.docinfo: it creates an empty /Info
    if not isinstance(info, pikepdf.Dictionary):
        return
    for key in list(info.keys()):
        # info[key], not info.get(key): a key name that is not UTF-8 (/Auth#9Dor) is
        # returned by pikepdf as a string with a surrogate, and get() on it raises
        # TypeError -- the section aborted and the keys after it stayed in the file.
        # [] and del work.
        raw = _s(info[key])
        risk = _info_risk(key, raw)
        # PDF/UA needs the title, but only a real one: a path, an email or a file name
        # in its place is a leak in any profile.
        ua_title = key == "/Title" and c.ua and risk is Risk.PROVENANCE
        if (risk in c.removes or key.startswith("/PTEX")) and not ua_title:
            if key.startswith("/PTEX"):
                note = tr("The TeX Live version string with its build date gives away the "
                          "author's OS and distribution.")
            elif c.paranoid:
                note = (tr("PARANOID removes everything: the file reads as processed (a Word PDF "
                           "without Creator/Producer) -- the profile does not hide that."))
            else:
                note = tr("Identity or environment: removed in every profile.")
            _pop(c, info, key, "/Info", raw, note)
        else:
            if key in ("/Creator", "/Producer"):
                note = tr(_NOTE_APP)
            elif key in ("/CreationDate", "/ModDate"):
                note = tr(_NOTE_DATE)
            elif ua_title:
                note = tr("The title is needed by PDF/UA (XMP declares pdfuaid) -- structural, "
                          "kept.")
            else:
                note = tr("Provenance: STEALTH keeps it, PARANOID removes it.")
            if not c.held("/Info", key, raw):
                c.res.act(Act.KEPT, "/Info", key, raw, note=note)
    if len(info) == 0:
        del pdf.trailer["/Info"]


# ============================================================================
#  B. XMP: raw stream through lxml
# ============================================================================


def _read_xmp(stream):
    """Decompressed XMP bytes, or None if reading is unsafe. Flate is decompressed
    with a ceiling (a bomb: a megabyte can expand into a gigabyte); exotic filters are
    left alone."""
    raw = stream.read_raw_bytes()
    flt = stream.get("/Filter")
    if stream.get("/DecodeParms") is not None:
        return None
    if flt is None:
        data = raw
    elif (isinstance(flt, pikepdf.Array) and len(flt) == 1 and _s(flt[0]) == "/FlateDecode") \
            or (not isinstance(flt, pikepdf.Array) and _s(flt) == "/FlateDecode"):
        d = zlib.decompressobj()
        data = d.decompress(raw, MAX_XMP)
        if d.unconsumed_tail:
            return None
    else:
        return None
    return data if len(data) <= MAX_XMP else None


def _xmp_drop(key, value, c):
    prefix = key.split(":", 1)[0]
    if prefix in ("pdfaid", "pdfuaid"):
        return False                                   # PDF/A, PDF/UA declaration -- structural
    if c.paranoid and not (c.ua and key == "dc:title"):
        return True
    if prefix in _DROP_NS or key in _XMP_IDENTITY:
        return True
    # A PDF/UA dc:title is kept in PARANOID too, unless its content leaks like any other value.
    return _xmp_value_risk(key, value) is not None


def _xmp_clean(raw, c, where):
    """-> (new bytes | None if there is nothing to change; whether anything besides
    an empty shell remains)."""
    root = _xmp_fromstring(raw)
    if root is None:
        return _EMPTY_XMP, False
    c.ua = c.ua or b"/pdfua/ns/id/" in raw
    changed, kept, payload = False, [], False
    for desc in list(root.iter(RDF + "Description")):
        about = desc.get(RDF + "about")
        if about:                                      # uuid: in rdf:about it duplicates DocumentID
            desc.set(RDF + "about", "")
            c.res.act(Act.NORMALIZED, where, "rdf:about", about, "", tr("The identifier in "
                                                                        "rdf:about duplicates "
                                                                        "xmpMM:DocumentID."))
            changed = True
        for attr in list(desc.attrib):                 # compact form: properties as attributes
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            value = desc.attrib[attr]
            if _xmp_drop(key, value, c) and not c.held(where, key, value):
                del desc.attrib[attr]
                c.res.act(Act.REMOVED, where, key, value, note=_xmp_note(key, c))
                changed = True
            else:
                kept.append(key)
        for child in list(desc):                       # expanded form: child elements
            if not isinstance(child.tag, str):
                continue
            key = _qname(child.tag)
            value = _flatten(child)
            if _xmp_drop(key, value, c) and not c.held(where, key, value):
                desc.remove(child)
                c.res.act(Act.REMOVED, where, key, value, note=_xmp_note(key, c))
                changed = True
            else:
                kept.append(key)
        payload = payload or bool(kept)
    if kept:
        c.res.act(Act.KEPT, where, tr("other XMP fields"), ", ".join(sorted(set(kept))),
                  note=tr("Structural declarations (PDF/A, PDF/UA).") if c.paranoid else
                       tr("Application, dates, title and structural declarations: consistent "
                          "with /Info."))
    if not changed:
        return None, payload
    etree.cleanup_namespaces(root, keep_ns_prefixes=["rdf", "x"])
    return _XPACKET % etree.tostring(root, encoding="utf-8"), payload


def _xmp_note(key, c):
    if c.paranoid and not (key.startswith("xmpMM:") or key in _XMP_IDENTITY or key.startswith("photoshop:")):
        return tr("PARANOID removes everything except structural declarations.")
    if key.startswith("xmpMM:History"):
        return tr("Edit history: the softwareAgent and when of every edit.")
    if key.startswith("xmpMM:"):
        return tr("A persistent GUID links all versions and all files made from one template.")
    return tr("Identity or environment in XMP: survives the cleaning of /Info.")


def _xmp_holder(holder, where, c):
    """A single /Metadata stream of a holder (catalog root, page, XObject)."""
    meta = holder.get("/Metadata")
    if meta is None:
        return
    if not isinstance(meta, pikepdf.Stream):
        _pop(c, holder, "/Metadata", where, tr("not a stream"), tr("Broken reference to XMP."))
        return
    if c.held(where, "/Metadata"):
        return
    raw = _read_xmp(meta)
    cleaned = None
    if raw is not None:
        try:
            cleaned = _xmp_clean(raw, c, where)
        except etree.XMLSyntaxError:
            # Does not parse even after the byte repair. Leaving it as it is is not an option:
            # the section used to crash, and all the identity in the XMP survived cleaning.
            cleaned = None
    if cleaned is None:              # too large, an exotic filter or not XML: we do not read it
        if c.paranoid:
            _pop(c, holder, "/Metadata", where, tr("unreadable XMP"), tr("The stream is too large or "
                                                                     "uses a non-standard "
                                                                     "encoding."))
        else:
            meta.write(_EMPTY_XMP)
            c.res.act(Act.NORMALIZED, where, "/Metadata", tr("unreadable XMP"), tr("empty shell"),
                      tr("The stream cannot be parsed safely: replaced with an empty packet so "
                         "that the XMP stays in place."))
        return
    new, payload = cleaned
    if c.paranoid and not payload:   # nothing is left of the XMP
        del holder["/Metadata"]
        c.res.act(Act.REMOVED, where, "/Metadata", tr("XMP packet"), note=tr("PARANOID: the packet is "
                                                                        "empty, the stream is "
                                                                        "removed entirely."))
        return
    if new is not None:
        meta.write(new)


def _sec_xmp(pdf, c):
    _xmp_holder(pdf.Root, "XMP", c)      # root first: it sets c.ua for /Info


# ============================================================================
#  Whole-file object sweep: /PieceInfo, /AF, /Thumb, TeX banner, XMP outside the root
# ============================================================================

_SWEEP = (
    ("/PieceInfo", N_("Private application data (Acrobat/Distiller/Illustrator), sometimes with "
                      "an editable source.")),
    ("/AF", N_("Associated files (PDF/A-3, invoices): a whole source xlsx/XML may be inside.")),
    ("/Thumb", N_("A page thumbnail goes stale after an edit and shows a previous version of the "
                  "page.")),
    ("/PTEX.Fullbanner", N_("The TeX Live version string with its build date gives away the "
                            "author's OS and distribution.")),
)


def _sweep_objects(pdf, c):
    root_gen = pdf.Root.objgen
    for obj in pdf.objects:
        try:
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            gen = obj.objgen
            where = "/Root" if gen == root_gen else tr("object %d") % gen[0]
            for key, note in _SWEEP:
                if key not in obj:
                    continue
                if key == "/PieceInfo":
                    before = ", ".join(str(k).lstrip("/") for k in obj.get(key).keys())
                elif key == "/AF":
                    before = "; ".join(_file_label(s) for s in list(obj.get(key))[:20])
                else:
                    before = tr("present") if key == "/Thumb" else _s(obj.get(key))
                _pop(c, obj, key, where, before, tr(note))
            if gen != root_gen and "/Metadata" in obj:
                _xmp_holder(obj, where + " XMP", c)
        except Exception as exc:  # noqa: BLE001
            c.res.err(tr("Object sweep: %s: %s") % (type(exc).__name__, exc))


# ============================================================================
#  JPEG images: EXIF inside a /DCTDecode stream
# ============================================================================


def _sec_images(pdf, c):
    """A /DCTDecode stream is a whole JPEG file, with camera EXIF (GPS, serial number,
    capture date). It is cleaned by the same code as standalone images: metadata
    segments are cut out, the compressed data is copied byte for byte, with no
    re-encoding. Only removals go into the report: "JFIF kept" lines for a hundred
    scans would drown the point."""
    n = 0
    for obj in pdf.objects:
        if not is_jpeg_image(obj):
            continue
        n += 1
        if n > MAX_IMAGES:
            raise _Refuse(tr("More than %d images: cleaning not performed, so as not to leave "
                             "some of them unprocessed.") % MAX_IMAGES)
        where = tr("image (object %d)") % obj.objgen[0]
        data = obj.read_raw_bytes()
        sub = CleanResult(src="", dst="", fmt="jpeg", profile=c.profile.value)
        new = _clean_jpeg(data, sub, keep_icc=not c.paranoid)
        if new is None:
            c.res.act(Act.FAILED, where, "JPEG", tr("%d bytes") % len(data),
                      note=tr("The stream did not parse as JPEG: %s") % "; ".join(sub.errors))
            continue
        if new == data:
            continue
        obj.write(new, filter=obj.get("/Filter"), decode_parms=obj.get("/DecodeParms"))
        for a in sub.actions:
            if a.act is Act.REMOVED:
                c.res.act(Act.REMOVED, "%s %s" % (where, a.location), a.label, a.before, a.after, a.note)


# ============================================================================
#  F, G. Active content and attachments (document)
# ============================================================================


def _sec_doc(pdf, c):
    root = pdf.Root
    names = root.get("/Names")
    if isinstance(names, pikepdf.Dictionary):
        for key, what, note in (
            ("/JavaScript", tr("Document-level script"),
             tr("JavaScript runs when the file is opened and can \"phone home\".")),
            ("/EmbeddedFiles", tr("Embedded file"),
             tr("ATTACHMENTS REMOVED: a whole source document may have been inside.")),
        ):
            tree = names.get(key)
            if tree is None:
                continue
            items = []
            _walk_name_tree(tree, items, c.res, 0)
            if c.held("/Names", key, tr("%d pcs") % len(items)):
                continue
            del names[key]
            if not items or len(items) >= 100:      # 100 is the ceiling of the name-tree walk
                c.res.act(Act.REMOVED, "/Names", key, tr("empty") if not items else tr("first 100 listed, the whole tree removed"),
                          note=note)
            for label, spec in items:
                before = _file_label(spec, label) if key == "/EmbeddedFiles" else label
                c.res.act(Act.REMOVED, "/Names " + key, what, before, note=note)
    _pop(c, root, "/OpenAction", "/Root", note=tr("What the file does on its own when opened."))
    _pop(c, root, "/AA", "/Root", note=tr("Document event handlers: print, save, close."))
    _pop(c, root, "/Collection", "/Root", tr("portfolio"), tr("A portfolio schema is meaningless "
                                                          "without the attachments."))
    if c.paranoid:
        _pop(c, root, "/DSS", "/Root", note=tr("Long-term signature validation data: certificate "
                                               "chains."))


def _action_bad(act, c, depth=0):
    """An action (or any link of a /Next chain) that must be removed."""
    if not isinstance(act, pikepdf.Dictionary):
        return False
    if depth > 6:
        return True
    s = _s(act.get("/S"))
    if s in _ACTIVE:
        return True
    if s == "/URI":
        uri = _s(act.get("/URI")).strip()
        if c.paranoid or _looks_like_path(uri) or not _WEB.match(uri):
            return True
    nxt = act.get("/Next")
    if isinstance(nxt, pikepdf.Array):
        return any(_action_bad(n, c, depth + 1) for n in nxt)
    return _action_bad(nxt, c, depth + 1)


def _scrub_actions(a, where, c):
    act = a.get("/A")
    if act is not None and _action_bad(act, c):
        s = _s(act.get("/S"))
        what = _s(act.get("/URI")) or _s(act.get("/F")) or ""
        _pop(c, a, "/A", where, ("%s %s" % (s, what)).strip(),
             tr("Active or external action: launching a program, a script, sending data or a "
                "link to a local path."))
    _pop(c, a, "/AA", where, note=tr("Event-handler scripts of an annotation or field."))


# ============================================================================
#  E, G. Pages and annotations
# ============================================================================


def _paranoid_keeps(a, sub, c):
    """What PARANOID keeps of the annotations: form fields and internal navigation."""
    if sub == "/Widget":
        return True
    if sub == "/Link":
        act = a.get("/A")
        return a.get("/Dest") is not None or (isinstance(act, pikepdf.Dictionary) and _s(act.get("/S")) == "/GoTo"
                                              and not _action_bad(act, c))
    return False


def _annots(p, where_pg, c, st):
    annots = p.get("/Annots")
    if not isinstance(annots, pikepdf.Array):
        return
    items = list(annots)
    st["annots"] += len(items)
    if st["annots"] > MAX_ANNOTS:
        raise _Refuse(tr("More than %d annotations: cleaning not performed, so as not to leave "
                         "some of them unprocessed.") % MAX_ANNOTS)
    where = where_pg + " /Annots"
    dicts = [a for a in items if isinstance(a, pikepdf.Dictionary)]
    dropped = set()          # id() of removed annotations (for direct dictionaries objgen does not tell them apart)
    gone = set()             # objgen of removed indirect ones: used to find their popups

    def drop(a, label, before, note):
        if c.held(where, label, before):
            return
        dropped.add(id(a))
        if a.objgen != (0, 0):
            gone.add(a.objgen)
        c.res.act(Act.REMOVED, where, label, before, note=note)

    for a in dicts:
        sub = _s(a.get("/Subtype"))
        if sub == "/FileAttachment":
            drop(a, "/FileAttachment", _file_label(a.get("/FS")),
                 tr("ATTACHMENT REMOVED: a whole source document may have been inside."))
        elif c.paranoid and not _paranoid_keeps(a, sub, c):
            drop(a, tr("Annotation ") + sub, _s(a.get("/Contents")),
                 tr("PARANOID: annotations are removed entirely (author, text, dates, popup). In "
                    "the regular profile they stay and only the author is removed."))
    for a in dicts:          # a removed parent's popup goes with it: otherwise, through /Parent,
        par = a.get("/Parent")   # the attachment stays reachable and survives cleaning
        if _s(a.get("/Subtype")) == "/Popup" and isinstance(par, pikepdf.Dictionary) and par.objgen in gone:
            dropped.add(id(a))
            gone.add(a.objgen)
    kept = [a for a in items if id(a) not in dropped]
    for a in kept:
        if not isinstance(a, pikepdf.Dictionary):
            continue
        sub = _s(a.get("/Subtype"))
        keys = [("/T", tr("Name of the annotation's author (the account name in Acrobat/Reader).")),
                ("/M", tr("Time the comment was edited.")), ("/CreationDate", tr("Time the comment was "
                                                                          "created.")),
                ("/RC", tr("Formatted duplicate of the comment text."))]
        if sub == "/Popup" or c.paranoid:
            keys.append(("/Contents", tr("Duplicate of the comment text.")))
        if c.paranoid:
            keys.append(("/Subj", tr("Comment subject.")))
        for key, note in keys:
            _pop(c, a, key, where, note=note)
        pop = a.get("/Popup")
        if isinstance(pop, pikepdf.Dictionary) and pop.objgen in gone:
            del a["/Popup"]
        _scrub_actions(a, where, c)
        if sub != "/Popup" and a.get("/Contents") is not None:
            st["comments"] += 1
    if len(kept) != len(items):
        if kept:
            p["/Annots"] = pikepdf.Array(kept)
        else:
            del p["/Annots"]


def _sec_pages(pdf, c):
    if len(pdf.pages) > MAX_PAGES:
        raise _Refuse(tr("More than %d pages: cleaning not performed, so as not to leave some of "
                         "them unprocessed.") % MAX_PAGES)
    st = {"annots": 0, "comments": 0}
    for idx, page in enumerate(pdf.pages):
        try:
            where = tr("p. %d") % (idx + 1)
            _pop(c, page.obj, "/AA", where, note=tr("Scripts that fire when a page is "
                                                    "opened/closed."))
            _annots(page.obj, where, c, st)
        except _Refuse:
            raise
        except Exception as exc:  # noqa: BLE001
            c.res.err(tr("Page %d: %s: %s") % (idx + 1, type(exc).__name__, exc))
    if st["comments"]:
        c.res.act(Act.KEPT, "/Annots", "/Contents", tr("%d pcs") % st["comments"],
                  note=tr("The comment text is document content, the user may have left it "
                          "deliberately; authorship and dates are removed. To remove the text as "
                          "well, use the PARANOID profile."))


# ============================================================================
#  Form, signature, layers, structural
# ============================================================================


def _sec_form(pdf, c):
    form = pdf.Root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary):
        return
    filled = 0
    cleared = False
    for name, f, ft in _iter_fields(form):
        if ft == "/Sig" or f.get("/V") is None:
            continue
        if not c.paranoid:
            filled += 1
            continue
        if _pop(c, f, "/V", "/AcroForm /Fields", _s(f.get("/V")),
                tr("PARANOID: the form field value. The viewer will redraw the field's "
                   "appearance."), tr("Field \"%s\"") % name):
            _del(f, "/DV")
            if f.get("/AS") is not None:
                f["/AS"] = pikepdf.Name("/Off")
            _del(f, "/AP")
            cleared = True
    if filled:
        c.res.act(Act.KEPT, "/AcroForm /Fields", tr("field values"), tr("%d pcs") % filled,
                  note=tr("The values are form content, the user filled them in deliberately. "
                          "PARANOID clears them."))
    if cleared:
        form["/NeedAppearances"] = True
    if c.paranoid:
        _pop(c, form, "/XFA", "/AcroForm", tr("XML copy of the form"),
             tr("XFA duplicates the entered data in parallel with the regular fields."))


def _sec_signature(pdf, c, sigs, docmdp):
    """Cleaning a signed file (force_signed only): the signature is destroyed."""
    for name, signer, field in sigs:
        _del(field, "/V")
        c.res.act(Act.REMOVED, "/AcroForm /Sig", tr("Digital signature \"%s\"") % name, signer,
                  note=tr("SIGNATURE DESTROYED: any metadata cleaning breaks the signature, and "
                          "force_signed=True allowed it. The field is left as an empty "
                          "placeholder."))
    form = pdf.Root.get("/AcroForm")
    if isinstance(form, pikepdf.Dictionary):
        _pop(c, form, "/SigFlags", "/AcroForm", note=tr("Flag that signatures are present: there "
                                                        "are no signatures any more."))
    if docmdp:
        _pop(c, pdf.Root, "/Perms", "/Root", tr("certifying signature"), tr("The certifying "
                                                                           "signature was "
                                                                           "removed together "
                                                                           "with the others."))
    _pop(c, pdf.Root, "/DSS", "/Root", note=tr("Signature validation data: the signers' "
                                               "certificate chains."))


def _sec_perms(pdf, c):
    """/Perms without /DocMDP is Reader Extensions (UR3): invalid after any edit."""
    perms = pdf.Root.get("/Perms")
    if isinstance(perms, pikepdf.Dictionary) and perms.get("/DocMDP") is None:
        _pop(c, pdf.Root, "/Perms", "/Root", tr("Reader Extensions rights"),
             tr("The usage-rights signature (UR3) is invalid after any edit; Reader will merely "
                "disable the extended features."))


def _sec_structural(pdf, c):
    root = pdf.Root
    for key, note in _STRUCT:
        if key in root:
            before = _s(root.get(key)) if key == "/Lang" else tr("present")
            if not c.held("/Root", key, before):
                c.res.act(Act.KEPT, "/Root", key, before, note=tr(note))
    ocp = root.get("/OCProperties")
    if isinstance(ocp, pikepdf.Dictionary):
        d = ocp.get("/D")
        off = d.get("/OFF") if isinstance(d, pikepdf.Dictionary) else None
        off = list(off) if isinstance(off, pikepdf.Array) else []
        if off:
            c.res.act(Act.UNREMOVABLE, "/OCProperties /D /OFF", tr("layers switched off"), tr("%d pcs") % len(off),
                      note=tr("The content of switched-off layers IS in the file and turns on "
                              "with one click. It can only be removed by rewriting the page "
                              "content streams -- cleaning does not do that."))
        else:
            c.res.act(Act.KEPT, "/OCProperties", tr("layers"), tr("all visible"), note=tr("The layers are visible, no content is hidden."))


# ============================================================================
#  D, C. Revisions, /ID, header, verification
# ============================================================================


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_eof(path):
    """Number of %%EOF in the raw bytes (in chunks, with overlap at the joins)."""
    pos, off, prev = set(), 0, b""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            buf = prev + chunk
            for m in re.finditer(rb"%%EOF", buf):
                pos.add(off - len(prev) + m.start())
            off += len(chunk)
            prev = buf[-8:]
    return len(pos)


def _prepare_id(pdf, c):
    """Return the description of /ID from before cleaning. QPDF keeps ID[0] and changes
    only ID[1], so we change ID[0] ourselves. The shape of the pair is preserved: in
    Word, Chrome, LibreOffice ID[0]==ID[1], and a diverged pair is a trace of a
    re-save. Equal -- delete /ID, QPDF writes both equal; different -- set a random
    ID[0], and QPDF derives ID[1] from the content of the result (deterministic_id:
    not the time and not the file name). static_id is not an option: it is a
    constant for all files."""
    try:
        ids = pdf.trailer.get("/ID")
        if not (isinstance(ids, pikepdf.Array) and len(ids) == 2):
            return None
        a, b = bytes(ids[0]), bytes(ids[1])
        if a == b:
            del pdf.trailer["/ID"]
        else:
            pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(os.urandom(16)), ids[1]])
        return "%s / %s" % (a.hex()[:8], b.hex()[:8]), a == b
    except Exception as exc:  # noqa: BLE001
        c.res.err(tr("/ID not regenerated: %s: %s") % (type(exc).__name__, exc))
        return None


def _save(pdf, path):
    # No linearize (see the module docstring). deterministic_id -- ID[1] from the content.
    # fix_metadata_version=False: otherwise save() calls open_metadata itself, and the
    # root XMP comes out reformatted by the pikepdf serializer (indentation, attribute
    # order, pdf:PDFVersion) -- a fingerprint on top of our edit through lxml.
    pdf.save(path, deterministic_id=True, fix_metadata_version=False)


_ID_RE = re.compile(rb"/ID\s*\[\s*<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\s*\]")


def _drop_id(path):
    """PARANOID: remove /ID from the trailer of the result (QPDF always writes it). It
    is replaced with whitespace padding of equal length: xref offsets do not shift,
    the dictionary stays valid.
    ponytail: we search from the last startxref instead of parsing the trailer --
    enough for QPDF output."""
    with open(path, "r+b") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 64))
        m = re.search(rb"startxref\s+(\d+)", fh.read())
        if not m:
            return False
        off = int(m.group(1))
        fh.seek(off)
        m = _ID_RE.search(fh.read())
        if not m:
            return False
        fh.seek(off + m.start())
        fh.write(b" " * (m.end() - m.start()))
    return True


_ID_PAIR_RE = re.compile(rb"/ID\s*\[\s*<([0-9A-Fa-f]*)>\s*<([0-9A-Fa-f]*)>\s*\]")


def _reroll_id(path, same):
    """STEALTH: replace both /ID values in the trailer of the result with random ones of the same length.

    deterministic_id derives /ID from the content. Cleaning an already cleaned file
    again gives the same content, hence the same /ID: two copies get linked, and the
    oracle honestly sees a surviving value (measured on a real PDF cleaned by the bot
    twice). A random value is linked to nothing. same -- keep the shape of the pair
    (ID[0]==ID[1], as in Word and Chrome). The length does not change, so xref
    offsets do not shift. False -- /ID not found, file untouched."""
    with open(path, "r+b") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 64))
        m = re.search(rb"startxref\s+(\d+)", fh.read())
        if not m:
            return False
        off = int(m.group(1))
        fh.seek(off)
        m = _ID_PAIR_RE.search(fh.read())
        if not m or not m.group(1) or len(m.group(1)) % 2 or len(m.group(2)) % 2:
            return False
        a = os.urandom(len(m.group(1)) // 2).hex().encode()
        b = a if same and len(m.group(1)) == len(m.group(2)) else os.urandom(len(m.group(2)) // 2).hex().encode()
        fh.seek(off + m.start(1))
        fh.write(a)
        fh.seek(off + m.start(2))
        fh.write(b)
    return True


def _fix_header(path, src):
    """Replace the qpdf marker in the header with the source's marker of equal length. -> (was, became)."""
    with open(src, "rb") as fh:
        m = re.match(rb"%PDF-\d\.\d[\r\n]+%([\x80-\xff]{4})[\r\n]", fh.read(64))
    mark = m.group(1) if m else _ADOBE_MARK
    if mark == _QPDF_MARK:               # the source was itself written by qpdf: the marker is its own, nothing to change
        return None
    with open(path, "r+b") as fh:
        head = fh.read(32)
        i = head.find(b"\n%" + _QPDF_MARK + b"\n")
        if i < 0:
            return None
        fh.seek(i + 2)
        fh.write(mark)
    return _QPDF_MARK.hex(" "), mark.hex(" ")


def _verify(res, tmp, snap, npages, no_id):
    """The output opens with our own parser and has not become a fingerprint. -> list of problems."""
    bad = []
    with pikepdf.open(tmp) as q:
        if len(q.pages) != npages:
            bad.append(tr("page count changed: %d -> %d") % (npages, len(q.pages)))
        if no_id and "/ID" in q.trailer:
            bad.append(tr("/ID is still in the trailer"))
        if "/Prev" in q.trailer:
            bad.append(tr("/Prev is still in the trailer: earlier revisions were not dropped"))
        info = q.trailer.get("/Info")
        for key, was in snap.items():
            now = _s(info.get(key)) if isinstance(info, pikepdf.Dictionary) and key in info else None
            if now != was:
                bad.append(tr("%s changed during saving (the cleaning itself would become a "
                              "fingerprint)") % key)
        left = sum(1 for o in q.objects if isinstance(o, pikepdf.Stream) and _s(o.get("/Type")) == "/EmbeddedFile")
        if left:
            bad.append(tr("attachments left in the file: %d") % left)
        if q.is_linearized:
            bad.append(tr("the result is linearized"))
    eof = _count_eof(tmp)
    if eof != 1:
        res.err(tr("The result has %d %%%%EOF markers (expected 1): possibly a string inside a "
                   "stream.") % eof)
    return bad


# ============================================================================
#  Entry point
# ============================================================================


def _clean(res, src, dst, profile, keep, force_signed, tmp):
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        raise _Refuse(tr("Unknown cleaning profile."))
    res.profile = prof.value
    if prof is Profile.REGENERATE:
        raise _Refuse(tr("The regenerate profile is not supported for PDF (it is for office "
                         "formats)."))
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        raise _Refuse(tr("The output path equals the input path: the input is never overwritten."))
    size = os.path.getsize(src)
    if size == 0:
        raise _Refuse(tr("The file is empty: possibly the download did not finish."))
    if size > MAX_INPUT:
        raise _Refuse(tr("File larger than %d MB: cleaning not performed.") % (MAX_INPUT >> 20))
    sha_before = _sha256(src)
    revs = _count_eof(src)
    c = _C(res, prof, keep)

    try:
        pdf = pikepdf.open(src)                       # strictly read-only
    except pikepdf.PasswordError:
        raise _Refuse(tr("The PDF is encrypted and does not open with an empty password: it "
                         "cannot be cleaned without the password."))
    except pikepdf.PdfError as exc:
        raise _Refuse(tr("The PDF does not open (%s): the file is damaged or is not a PDF.") % type(exc).__name__)
    with pdf:
        if pdf.is_encrypted:
            raise _Refuse(tr("The PDF is encrypted: encrypted files are not cleaned, so as not "
                             "to strip the protection silently."))
        if any(re.search(r"damaged|reconstruct|recover", w, re.I) for w in pdf.get_warnings()):
            raise _Refuse(tr("The file is damaged: QPDF had to rebuild its structure. Cleaning "
                             "not performed, so as not to hand you a file with lost parts."))
        npages = len(pdf.pages)
        sigs, docmdp = _filled_signatures(pdf)
        if (sigs or docmdp) and not force_signed:
            raise _Refuse(tr("The document is digitally signed (%d): any metadata cleaning would "
                             "destroy the signature. A signature and cleaning are mutually "
                             "exclusive; to clean anyway and lose the signature, pass "
                             "force_signed=True.") % max(len(sigs), 1))
        was_lin = pdf.is_linearized
        if sigs or docmdp:
            _step(c, tr("signature"), _sec_signature, pdf, c, sigs, docmdp)
        for name, fn, args in (
            ("XMP", _sec_xmp, (pdf, c)),
            ("/Info", _sec_info, (pdf, c)),
            (tr("active content and attachments"), _sec_doc, (pdf, c)),
            (tr("pages and annotations"), _sec_pages, (pdf, c)),
            (tr("images"), _sec_images, (pdf, c)),
            (tr("form"), _sec_form, (pdf, c)),
            ("/Perms", _sec_perms, (pdf, c)),
            (tr("object sweep"), _sweep_objects, (pdf, c)),
            (tr("layers and structural"), _sec_structural, (pdf, c)),
        ):
            _step(c, name, fn, *args)
        try:
            pdf.remove_unreferenced_resources()
        except Exception as exc:  # noqa: BLE001
            res.err("remove_unreferenced_resources: %s: %s" % (type(exc).__name__, exc))
        before_id = _prepare_id(pdf, c)
        info = pdf.trailer.get("/Info")
        snap = {k: (_s(info.get(k)) if isinstance(info, pikepdf.Dictionary) and k in info else None)
                for k in ("/Creator", "/Producer")}
        _save(pdf, tmp)

    marks = _fix_header(tmp, src)
    id_dropped = c.paranoid and _drop_id(tmp)
    if not c.paranoid:
        # Without the source /ID QPDF writes an equal pair -- we keep the same shape.
        _reroll_id(tmp, before_id[1] if before_id else True)
    bad = _verify(res, tmp, snap, npages, c.paranoid)
    for b in bad:
        res.err(b)
    if bad:
        return
    if _sha256(src) != sha_before:
        res.err(tr("The input file changed during cleaning (sha256 before and after differ)."))
        return

    if revs > (2 if was_lin else 1):
        res.act(Act.REMOVED, tr("raw bytes"), tr("Incremental revisions"), tr("%d revisions") % revs, "1",
                tr("Earlier page versions and the text under a \"cover-up\" are physically "
                   "discarded by the full rewrite (QPDF garbage collection); there is no /Prev "
                   "in the trailer."))
    if was_lin:
        res.act(Act.NORMALIZED, tr("structure"), tr("Linearization"), tr("yes"), tr("no"),
                tr("Saved without linearization: the first xref section of a linearized file has "
                   "/Prev, and linearization itself is an Acrobat sign."))
    if id_dropped:
        res.act(Act.REMOVED, "trailer", "/ID", before_id[0] if before_id else "", "",
                tr("PARANOID: /ID removed altogether (it is optional). In many producers ID[0] "
                   "is derived from the file path and name and links files; a file without /ID "
                   "looks processed."))
    elif before_id:
        with pikepdf.open(tmp) as q:
            after = "/".join(bytes(x).hex()[:8] for x in q.trailer.get("/ID", []))
        res.act(Act.REGENERATED, "trailer", "/ID", before_id[0], after,
                tr("In many producers ID[0] is derived from the file path and name and links "
                   "files. Both values are new and random; the pair is %s, as in the source (a "
                   "diverged pair betrays a re-save). Not derived from the content: otherwise "
                   "cleaning the same file again would give the same /ID.")
                % (tr("equal") if before_id[1] else tr("different")))
    if marks:
        res.act(Act.NORMALIZED, tr("header"), tr("qpdf binary marker"), marks[0], marks[1],
                tr("Marker %¿÷¢þ is a well-known qpdf signature; replaced with the source's "
                   "marker of equal length."))
    os.replace(tmp, dst)
    res.ok = True


def clean_pdf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult:
    """Clean the PDF src -> dst. The input is not modified. Never raises: on refusal
    ok=False, the reason is in errors, and the file dst is not created.

    keep -- the user's whitelist: a set of strings, compared (case-insensitively) with
    the label and location of an action: "/Author", "dc:creator", "/Info", "p. 3 /Annots".
    force_signed -- clean a signed file, destroying the signature."""
    res = CleanResult(src=str(src), dst=str(dst), fmt="pdf",
                      profile=getattr(profile, "value", str(profile)))
    tmp = str(dst) + ".part"
    try:
        d = os.path.dirname(os.path.abspath(str(dst)))
        os.makedirs(d, exist_ok=True)
        _clean(res, str(src), str(dst), profile, keep, force_signed, tmp)
    except _Refuse as exc:
        res.err(exc)
    except Exception as exc:  # noqa: BLE001 -- nothing may reach the user
        res.err(tr("Internal PDF cleaning error: %s: %s") % (type(exc).__name__, exc))
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return res


# ============================================================================
#  Self-test: PYTHONIOENCODING=utf-8 python -m core.clean_pdf
#  Your own files with metrics (no content printed): ... -m core.clean_pdf --real file.pdf ...
# ============================================================================

_FAILS = []
_TOTAL = [0]
_WORD_MARK = b"\xb5\xb5\xb5\xb5"      # Word header marker (µµµµ)


def _check(name, cond, detail=""):
    _TOTAL[0] += 1
    if not cond:
        _FAILS.append(name)
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name, "  -- " + str(detail) if (detail and not cond) else ""))


def _xmp_fixture():
    return ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="uuid:aaaaaaaa" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
            'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
            'xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#" '
            'xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#" '
            'xmp:CreatorTool="Microsoft Word" xmp:CreateDate="2024-01-01T12:00:00+03:00" '
            'xmp:ModifyDate="2024-01-05T15:05:00+03:00" xmpMM:DocumentID="uuid:11111111" '
            'xmpMM:InstanceID="uuid:22222222" pdf:Producer="Microsoft: Print To PDF" photoshop:Credit="Photo Credit">'
            '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Годовой отчёт</rdf:li></rdf:Alt></dc:title>'
            '<dc:creator><rdf:Seq><rdf:li>Иван Петров</rdf:li></rdf:Seq></dc:creator>'
            '<xmpMM:History><rdf:Seq><rdf:li rdf:parseType="Resource"><stEvt:action>saved</stEvt:action>'
            '<stEvt:softwareAgent>Adobe Acrobat 9</stEvt:softwareAgent><stEvt:when>2024-01-05T15:05:00+03:00</stEvt:when>'
            '</rdf:li></rdf:Seq></xmpMM:History>'
            '<xmpMM:DerivedFrom rdf:parseType="Resource"><stRef:instanceID>uuid:33333333</stRef:instanceID>'
            '</xmpMM:DerivedFrom></rdf:Description></rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>').encode("utf-8")


def _fx_base(outlines=False, equal_id=False):
    """Base Word-like PDF: /Info with identity, XMP with a history, tags, bookmarks."""
    N = pikepdf.Name
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=N.Font, Subtype=N.Type1, BaseFont=N.Helvetica))
    for _ in range(2):
        pdf.add_blank_page(page_size=(595, 842))
        pg = pdf.pages[-1].obj
        pg.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        pg.Contents = pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (Page text) Tj ET")
    info = pdf.docinfo
    for k, v in (("/Author", "Иван Петров"), ("/Creator", "Microsoft Word"), ("/Producer", "Microsoft: Print To PDF"),
                 ("/CreationDate", "D:20240101120000+03'00'"), ("/ModDate", "D:20240105150500+03'00'"),
                 ("/Title", "Годовой отчёт"), ("/Company", "ACME Ltd"), ("/Custom", "benign"),
                 ("/PTEX.Fullbanner", "This is pdfTeX, Version 3.14 (TeX Live 2020)")):
        info[k] = v
    pdf.Root["/Lang"] = "ru-RU"
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=True)
    pdf.Root["/StructTreeRoot"] = pdf.make_indirect(pikepdf.Dictionary(Type=N.StructTreeRoot))
    pdf.Root["/ViewerPreferences"] = pikepdf.Dictionary(DisplayDocTitle=True)
    if outlines:
        with pdf.open_outline() as ol:
            for t in ("Глава 1", "Глава 2", "Глава 3"):
                ol.root.append(pikepdf.OutlineItem(t, 0))
    m = pdf.make_stream(_xmp_fixture())
    m["/Type"], m["/Subtype"] = N.Metadata, N.XML
    pdf.Root["/Metadata"] = m
    if equal_id:
        pass                         # without /ID QPDF writes an equal pair: that is what Word, Chrome, LibreOffice look like
    else:
        pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(bytes([0x41] * 16)), pikepdf.String(bytes([0x42] * 16))])
    return pdf


def _write(tmp, name, pdf, **kw):
    path = os.path.join(tmp, name + ".pdf")
    # No open_metadata on save: otherwise pikepdf silently replaces an unreadable XMP
    # with an empty one and decompresses a compressed one -- the "bomb" and "compressed
    # XMP" fixtures would never reach cleaning.
    kw.setdefault("fix_metadata_version", False)
    pdf.save(path, **kw)
    pdf.close()
    return path


def _annot(pdf, page, **kw):
    a = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Rect=[10, 10, 100, 40], **kw))
    pg = pdf.pages[page].obj
    if "/Annots" not in pg:
        pg["/Annots"] = pikepdf.Array()
    pg["/Annots"].append(a)
    return a


def _fx_incremental(path, **save_kw):
    """Manual incremental revision: the first revision has TOPSECRET, the second a "cover-up".
    save_kw goes to save(), e.g. linearize=True for a linearized base revision."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.pages[0].obj["/Contents"] = pdf.make_stream(b"BT 10 100 Td (TOPSECRET 4111) Tj ET")
    pdf.docinfo["/Author"] = "Иван Петров"
    pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable, compress_streams=False, static_id=True,
             **save_kw)
    with pikepdf.open(path) as q:
        n = q.pages[0].Contents.objgen[0]
    data = open(path, "rb").read()
    old = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    inner = re.sub(rb"\s*/Prev\s+\d+", b"", re.search(rb"trailer\s*<<(.*?)>>", data, re.S).group(1))
    new = b"BT 10 100 Td (REDACTED) Tj ET"
    obj = b"\n%d 0 obj\n<< /Length %d >>\nstream\n" % (n, len(new)) + new + b"\nendstream\nendobj\n"
    o_off = len(data) + obj.index(b"%d 0 obj" % n)
    x_off = len(data) + len(obj)
    tail = (b"xref\n%d 1\n%010d 00000 n \ntrailer\n<<" % (n, o_off) + inner
            + b" /Prev %d >>\nstartxref\n%d\n" % (old, x_off) + b"%%EOF\n")
    open(path, "wb").write(data + obj + tail)


def _raw(path):
    d = open(path, "rb").read()
    return len(re.findall(rb"%%EOF", d)), len(re.findall(rb"/Prev", d))


def _selftest():
    import shutil

    from .inspect import inspect_file

    N, D, S, A = pikepdf.Name, pikepdf.Dictionary, pikepdf.String, pikepdf.Array
    tmp = tempfile.mkdtemp(prefix="clean_pdf_test_")
    try:
        def run(src, name, profile=Profile.STEALTH, **kw):
            dst = os.path.join(tmp, name + "_out.pdf")
            h = _sha256(src)
            res = clean_pdf(src, dst, profile, **kw)
            _check("%s: input unchanged (sha256 before == after)" % name, _sha256(src) == h)
            return res, dst

        def info_of(path):
            with pikepdf.open(path) as q:
                inf = q.trailer.get("/Info")
                return {k: _s(inf.get(k)) for k in inf.keys()} if isinstance(inf, pikepdf.Dictionary) else {}

        # --- 1. basic STEALTH --------------------------------------------------
        print("1. STEALTH on a Word-like file")
        src = _write(tmp, "base", _fx_base(outlines=True))
        raw = open(src, "rb").read()
        open(src, "wb").write(raw.replace(b"\n%" + _QPDF_MARK + b"\n", b"\n%" + _WORD_MARK + b"\n", 1))   # as in Word
        before = inspect_file(src)
        res, dst = run(src, "stealth")
        _check("ok and dst created", res.ok and os.path.exists(dst), res.errors)
        i0, i1 = info_of(src), info_of(dst)
        for k in ("/Author", "/Company", "/PTEX.Fullbanner"):
            _check("%s removed" % k, k not in i1)
        for k in ("/Creator", "/Producer", "/CreationDate", "/ModDate", "/Title", "/Custom"):
            _check("%s kept as it was (including Cyrillic)" % k, i1.get(k) == i0.get(k), (i0.get(k), i1.get(k)))
        _check("Producer did not become pikepdf/qpdf", "pikepdf" not in i1.get("/Producer", "") and "qpdf" not in i1.get("/Producer", "").lower())
        with pikepdf.open(dst) as q:
            xmp = q.Root.Metadata.read_bytes().decode("utf-8")
            _check("XMP stayed in place", "xmpmeta" in xmp)
            for bad in ("DocumentID", "InstanceID", "History", "DerivedFrom", "dc:creator", "photoshop", "Иван", "uuid:"):
                _check("no %s in XMP" % bad, bad not in xmp)
            _check("xmp:CreatorTool, dates and dc:title in place", all(x in xmp for x in ("CreatorTool", "CreateDate", "Годовой отчёт")))
            for k in ("/StructTreeRoot", "/MarkInfo", "/Lang", "/Outlines", "/ViewerPreferences"):
                _check("structural %s kept" % k, k in q.Root)
            _check("same number of pages", len(q.pages) == 2)
            ids = [bytes(x) for x in q.trailer["/ID"]]
        with pikepdf.open(src) as q:
            old_ids = [bytes(x) for x in q.trailer["/ID"]]
        _check("/ID regenerated (both values are new)", ids[0] != old_ids[0] and ids[1] != old_ids[1] and len(ids[0]) == 16)
        _check("pair shape: the original is different -- and the new one is different", ids[0] != ids[1])
        _check("header marker is the source's (Word), not qpdf's", _WORD_MARK in open(dst, "rb").read(32)
               and _QPDF_MARK not in open(dst, "rb").read(32))
        open(src + "2", "wb").write(raw)                       # the source was already written by qpdf: the marker stays its own
        _check("the source's marker is not invented", clean_pdf(src + "2", dst + "2").ok and _QPDF_MARK in open(dst + "2", "rb").read(32))
        os.remove(src + "2")
        os.remove(dst + "2")
        _check("KEPT actions present (Creator, Producer, structural)", res.count(Act.KEPT) >= 6)
        _check("REGENERATED /ID recorded", res.count(Act.REGENERATED) == 1)
        after = inspect_file(dst)
        left = {f.label.split(" ")[0] for f in after.findings if f.risk.value in ("identity", "environment")}
        _check("oracle: of the critical ones only /ID and the false bookmark /Prev remain", left <= {"/ID[0]", "/ID[1]", "Incremental"}, left)
        new_sig = {s.kind for s in after.signals} - {s.kind for s in before.signals}
        _check("oracle: no scrubbed/inconsistent signals appeared", not (new_sig & {"scrubbed", "inconsistent"}), new_sig)
        r2, d2 = run(dst, "stealth_again")
        _check("re-cleaning the result is ok too", r2.ok, r2.errors)

        # --- 2. equal /ID pair ---------------------------------------------------
        print("2. /ID shape: an equal pair stays equal")
        s2 = _write(tmp, "eqid", _fx_base(equal_id=True))
        with pikepdf.open(s2) as q:
            old = [bytes(x) for x in q.trailer["/ID"]]
        res, dst = run(s2, "eqid")
        with pikepdf.open(dst) as q:
            ids = [bytes(x) for x in q.trailer["/ID"]]
        _check("fixture: the source pair is equal", old[0] == old[1])
        _check("the pair was equal -- still equal, the value is new", res.ok and ids[0] == ids[1] and ids[0] != old[0])
        # Re-cleaning the result: /ID used to be derived from the content and matched
        # the input, the oracle saw a surviving value, and the bot answered "not fully cleaned".
        res2, dst2 = run(dst, "eqid_again")
        with pikepdf.open(dst2) as q:
            ids2 = [bytes(x) for x in q.trailer["/ID"]]
        _check("re-cleaning: /ID is new again, the pair is equal",
               res2.ok and ids2[0] == ids2[1] and ids2[0] != ids[0], res2.errors)

        # --- 3. PARANOID ----------------------------------------------------------
        print("3. PARANOID: the oracle requires critical == 0")
        s3 = _write(tmp, "par", _fx_base())
        res, dst = run(s3, "par", Profile.PARANOID)
        _check("ok", res.ok, res.errors)
        after = inspect_file(dst)
        _check("critical_after == 0", after.critical == 0,
               [(f.location, f.label) for f in after.findings if f.risk.value in ("identity", "environment")])
        with pikepdf.open(dst) as q:
            _check("/Info removed entirely, /Metadata removed", "/Info" not in q.trailer and "/Metadata" not in q.Root)
            _check("/ID removed, pages in place", "/ID" not in q.trailer and len(q.pages) == 2)
            _check("structural kept in PARANOID too", all(k in q.Root for k in ("/StructTreeRoot", "/MarkInfo", "/Lang")))

        # --- 4. incremental revisions -------------------------------------------
        print("4. incremental revisions: text under a \"cover-up\"")
        s4 = os.path.join(tmp, "inc.pdf")
        _fx_incremental(s4)
        eof0, prev0 = _raw(s4)
        _check("fixture: TOPSECRET is in the raw bytes, 2 revisions, /Prev 1",
               b"TOPSECRET" in open(s4, "rb").read() and eof0 == 2 and prev0 == 1, (eof0, prev0))
        b4 = inspect_file(s4)
        for prof in (Profile.STEALTH, Profile.PARANOID):
            res, dst = run(s4, "inc_" + prof.value, prof)
            eof1, prev1 = _raw(dst)
            _check("%s: ok" % prof.value, res.ok, res.errors)
            _check("%s: TOPSECRET is gone from the file, %%%%EOF == 1, /Prev == 0" % prof.value,
                   b"TOPSECRET" not in open(dst, "rb").read() and eof1 == 1 and prev1 == 0, (eof1, prev1))
            with pikepdf.open(dst) as q:
                _check("%s: the \"cover-up\" remains on the page" % prof.value, b"REDACTED" in q.pages[0].Contents.read_bytes())
            a4 = inspect_file(dst)
            _check("%s: oracle: \"Incremental updates\" and hazard are gone" % prof.value,
                   not any("Incremental" in f.label for f in a4.findings) and not any(s.kind == "hazard" for s in a4.signals))
            if prof is Profile.PARANOID:
                _check("PARANOID: critical == 0 (was %d)" % b4.critical, a4.critical == 0)

        # --- 5. attachments -----------------------------------------------------------
        print("5. attachments: remove, but list")
        pdf = _fx_base()
        pdf.attachments["исходник.xlsx"] = b"PK-secret-spreadsheet" * 50
        fs = pikepdf.AttachedFileSpec(pdf, b"hidden-note" * 20, filename="note.txt")
        fa = _annot(pdf, 0, Subtype=N.FileAttachment, FS=pdf.make_indirect(fs.obj), T="Иван Петров", Contents="note")
        fa["/Popup"] = _annot(pdf, 0, Subtype=N.Popup, Parent=fa, Contents="note")
        s5 = _write(tmp, "att", pdf)
        for prof in (Profile.STEALTH, Profile.PARANOID):
            res, dst = run(s5, "att_" + prof.value, prof)
            blob = open(dst, "rb").read()
            _check("%s: ok" % prof.value, res.ok, res.errors)
            _check("%s: no attachment bytes in the file" % prof.value, b"PK-secret-spreadsheet" not in blob and b"hidden-note" not in blob)
            names = " | ".join(a.before for a in res.actions if a.act is Act.REMOVED)
            _check("%s: both attachments are named in actions" % prof.value, "исходник.xlsx" in names and "note.txt" in names, names[:200])
            with pikepdf.open(dst) as q:
                _check("%s: /EmbeddedFiles and FileAttachment removed" % prof.value,
                       len(q.attachments) == 0 and "/Annots" not in q.pages[0].obj)

        # --- 6. active content and links --------------------------------------
        print("6. active content")
        pdf = _fx_base()
        js = D(S=N.JavaScript, JS=S("app.alert('x')"))
        pdf.Root["/OpenAction"] = js
        pdf.Root["/AA"] = D(WC=js)
        pdf.Root["/Names"] = D(JavaScript=D(Names=A([S("init"), pdf.make_indirect(js)])))
        pdf.pages[0].obj["/AA"] = D(O=js)
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.Launch, F=S("C:\\Users\\bob\\run.exe")))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.URI, URI=S("file://corp-srv/share/doc.docx")))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.URI, URI=S("https://example.com/page")))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.GoTo, D=A([pdf.pages[1].obj, N.Fit])))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.GoTo, D=A([pdf.pages[1].obj, N.Fit]), Next=js))
        s6 = _write(tmp, "act", pdf)
        res, dst = run(s6, "act")
        _check("ok", res.ok, res.errors)
        with pikepdf.open(dst) as q:
            r = q.Root
            _check("OpenAction, AA, Names/JavaScript, page AA removed",
                   "/OpenAction" not in r and "/AA" not in r and "/JavaScript" not in r.get("/Names", D()) and "/AA" not in q.pages[0].obj)
            acts = [(_s(a["/A"].get("/S")), _s(a["/A"].get("/URI"))) if "/A" in a else None for a in q.pages[0].obj.Annots]
        _check("Launch and file:// removed, https and internal GoTo kept, GoTo with JS in /Next "
               "removed",
               acts == [None, None, ("/URI", "https://example.com/page"), ("/GoTo", ""), None], acts)
        a6 = inspect_file(dst)
        env = [(f.location, f.label) for f in a6.findings if f.risk.value == "environment"]
        _check("oracle: no environment left (JS/OpenAction/AA/Launch/file://)", not env, env)
        _check("oracle: the hazard about active content is gone", not any("Active content" in s.detail for s in a6.signals))
        res, dst = run(s6, "act_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            kinds = [_s(a.get("/Subtype")) + (":" + _s(a["/A"].get("/S")) if "/A" in a else "") for a in q.pages[0].obj.get("/Annots", [])]
        _check("PARANOID: of the links only a clean internal GoTo is left (the one with JS in "
               "/Next is removed)", kinds == ["/Link:/GoTo"], kinds)

        # --- 7. comments --------------------------------------------------------
        print("7. annotations: remove authorship, not the annotations themselves")
        pdf = _fx_base()
        note = _annot(pdf, 0, Subtype=N.Text, T="Иван Петров", M="D:20240102000000", CreationDate="D:20240102000000",
                      Contents="проверьте пункт 5", RC="<p>проверьте</p>")
        note["/Popup"] = _annot(pdf, 0, Subtype=N.Popup, Parent=note, T="Иван Петров", Contents="проверьте пункт 5")
        s7 = _write(tmp, "com", pdf)
        res, dst = run(s7, "com")
        with pikepdf.open(dst) as q:
            an = list(q.pages[0].obj.Annots)
            subs = [_s(a.get("/Subtype")) for a in an]
            keys = set().union(*[set(a.keys()) for a in an])
        _check("both annotations in place", subs == ["/Text", "/Popup"], subs)
        _check("/T, /M, /CreationDate, /RC removed, popup cleared, the parent's /Contents kept",
               not (keys & {"/T", "/M", "/CreationDate", "/RC"}) and "/Contents" in an[0] and "/Contents" not in an[1], keys)
        _check("KEPT about the comment text is present", any(a.label == "/Contents" and a.act is Act.KEPT for a in res.actions))
        res, dst = run(s7, "com_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            _check("PARANOID: annotations removed entirely", "/Annots" not in q.pages[0].obj)
        _check("PARANOID: the note says so", any("entirely" in a.note for a in res.actions if a.act is Act.REMOVED))

        # --- 8. signature ------------------------------------------------------------
        print("8. digital signature: refusal by default")
        pdf = _fx_base()
        sigd = pdf.make_indirect(D(Type=N.Sig, Filter=N.Adobe_PPKLite, SubFilter=N("/adbe.pkcs7.detached"), Name=S("Иван Петров"),
                                   M=S("D:20240105150500"), Contents=S(b"\x00" * 16), ByteRange=A([0, 1, 2, 3])))
        fld = _annot(pdf, 0, Subtype=N.Widget, FT=N.Sig, T=S("Sig1"), V=sigd)
        empty = _annot(pdf, 1, Subtype=N.Widget, FT=N.Sig, T=S("Sig2"))
        pdf.Root["/AcroForm"] = D(Fields=A([fld, empty]), SigFlags=3)
        s8 = _write(tmp, "sig", pdf)
        res, dst = run(s8, "sig")
        _check("signed: ok=False, file not created, reason named",
               not res.ok and not os.path.exists(dst) and any("signed" in e for e in res.errors), res.errors)
        res, dst = run(s8, "sig_f", force_signed=True)
        _check("force_signed: ok, the signature is physically removed", res.ok and b"adbe.pkcs7" not in open(dst, "rb").read(), res.errors)
        _check("force_signed: Act.REMOVED about the signature", any("signature" in a.label.lower() and a.act is Act.REMOVED for a in res.actions))
        a8 = inspect_file(dst)
        _check("force_signed: the oracle sees no filled signatures",
               not any(f.label.startswith("Signature field \"Sig1\"") and f.value == "present" for f in a8.findings))
        pdf = _fx_base()
        fld = _annot(pdf, 0, Subtype=N.Widget, FT=N.Sig, T=S("Sig2"))
        pdf.Root["/AcroForm"] = D(Fields=A([fld]))
        res, dst = run(_write(tmp, "sig_empty", pdf), "sig_empty")
        _check("an empty signature placeholder does NOT block cleaning", res.ok, res.errors)

        # --- 9. form, layers, whitelist ---------------------------------------------
        print("9. form, layers, keep")
        pdf = _fx_base()
        tf = _annot(pdf, 0, Subtype=N.Widget, FT=N.Tx, T=S("fio"), V=S("Иван Петров секретно"), AP=D(N=pdf.make_stream(b"q Q")))
        pdf.Root["/AcroForm"] = D(Fields=A([tf]))
        ocg = pdf.make_indirect(D(Type=N.OCG, Name=S("Черновик")))
        pdf.Root["/OCProperties"] = D(OCGs=A([ocg]), D=D(OFF=A([ocg])))
        s9 = _write(tmp, "form", pdf)
        res, dst = run(s9, "form")
        with pikepdf.open(dst) as q:
            _check("STEALTH: the form field value is kept (content)", "/V" in q.Root.AcroForm.Fields[0])
        _check("STEALTH: a switched-off layer is UNREMOVABLE, not silence", res.count(Act.UNREMOVABLE) == 1)
        res, dst = run(s9, "form_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            f = q.Root.AcroForm.Fields[0]
            _check("PARANOID: /V and the field's appearance removed, NeedAppearances on",
                   "/V" not in f and "/AP" not in f and bool(q.Root.AcroForm.get("/NeedAppearances")))
        res, dst = run(s7, "keep", keep={"/Author", "dc:creator"})
        with pikepdf.open(dst) as q:
            xmp = q.Root.Metadata.read_bytes().decode("utf-8")
        _check("keep: /Author and dc:creator kept, GUIDs removed all the same",
               info_of(dst).get("/Author") == "Иван Петров" and "Иван Петров" in xmp and "DocumentID" not in xmp)
        _check("keep: actions have KEPT referring to the whitelist", any(a.act is Act.KEPT and "whitelist" in a.note for a in res.actions))

        # --- 10. robustness -------------------------------------------------------
        print("10. broken, empty, encrypted, wrong arguments")
        good = open(s3, "rb").read()
        junk = {"empty": b"", "junk": b"%PDF-1.7\n" + os.urandom(400), "notpdf": b"PK\x03\x04hello", "truncated": good[: len(good) // 2]}
        for name, data in junk.items():
            p = os.path.join(tmp, name + ".pdf")
            open(p, "wb").write(data)
            res, dst = run(p, "bad_" + name)
            _check("%s: ok=False, errors present, no dst, no exceptions" % name,
                   not res.ok and bool(res.errors) and not os.path.exists(dst), res.errors)
        s10 = _write(tmp, "enc", _fx_base(), encryption=pikepdf.Encryption(user="u", owner="o"))
        res, dst = run(s10, "enc")
        _check("encrypted: ok=False, no dst", not res.ok and not os.path.exists(dst), res.errors)
        s10 = _write(tmp, "enc2", _fx_base(), encryption=pikepdf.Encryption(user="", owner="o"))
        res, dst = run(s10, "enc_empty")
        _check("encrypted with an empty password: also a refusal (we never strip protection "
               "silently)", not res.ok and not os.path.exists(dst), res.errors)
        res = clean_pdf(os.path.join(tmp, "нет_такого.pdf"), os.path.join(tmp, "x.pdf"))
        _check("nonexistent input: ok=False, errors without an absolute path",
               not res.ok and bool(res.errors) and not any(tmp in e for e in res.errors), res.errors)
        h = _sha256(s3)
        res = clean_pdf(s3, s3)
        _check("dst == src: refusal, input intact", not res.ok and _sha256(s3) == h)
        res = clean_pdf(s3, os.path.join(tmp, "x.pdf"), Profile.REGENERATE)
        _check("regenerate profile for PDF: refusal", not res.ok and bool(res.errors))
        res = clean_pdf(s3, os.path.join(tmp, "x.pdf"), "нет-такого")
        _check("unknown profile: refusal without an exception", not res.ok and bool(res.errors))

        # --- 11. self-fingerprint: Producer substituted on save ---------------
        print("11. if save() substitutes Producer -- the file is not handed over")
        global _save
        orig_save = _save

        def evil_save(pdf, path):
            pdf.docinfo["/Producer"] = "pikepdf 99"
            orig_save(pdf, path)

        _save = evil_save
        try:
            res, dst = run(src, "evil")
        finally:
            _save = orig_save
        _check("Producer substitution after saving is caught: ok=False, no dst", not res.ok and not os.path.exists(dst), res.errors)

        # --- 12. sweep spots, compressed XMP, bomb, cycles --------------------------
        print("12. sweep spots: image XMP, /PieceInfo, /Thumb, /AF, cycles")
        pdf = _fx_base()
        pdf.Root.Metadata.write(zlib.compress(_xmp_fixture()), filter=N.FlateDecode)      # compressed XMP
        xm = pdf.make_stream(_xmp_fixture())
        xm["/Type"], xm["/Subtype"] = N.Metadata, N.XML
        img = pdf.make_stream(b"\x00\x00\x00", Type=N.XObject, Subtype=N.Image, Width=1, Height=1,
                              ColorSpace=N.DeviceRGB, BitsPerComponent=8)
        img["/Metadata"] = xm                                                              # XMP on an image
        pdf.pages[0].obj.Resources["/XObject"] = D(Im1=img)
        pdf.pages[0].contents_add(pdf.make_stream(b"q /Im1 Do Q"))                     # drawn: not an "unused resource"
        pdf.Root["/PieceInfo"] = D(Illustrator=D(LastModified=S("D:20240101000000")))
        pdf.pages[0].obj["/PieceInfo"] = D(Acrobat=D(LastModified=S("D:20240101000000")))
        pdf.pages[0].obj["/Thumb"] = pdf.make_stream(b"\x00")
        pdf.Root["/AF"] = A([pdf.make_indirect(pikepdf.AttachedFileSpec(pdf, b"af-secret", filename="af.xml").obj)])
        ctree = pdf.make_indirect(D(Kids=A([])))
        ctree.Kids.append(ctree)                                                           # cycle in the name tree
        pdf.Root["/Names"] = D(EmbeddedFiles=ctree)
        fld = pdf.make_indirect(D(FT=N.Tx, T=S("a"), Kids=A([])))
        fld.Kids.append(fld)                                                               # cycle in the form fields
        pdf.Root["/AcroForm"] = D(Fields=A([fld]))
        s12 = _write(tmp, "misc", pdf)
        res, dst = run(s12, "misc")
        _check("ok (cycles did not hang cleaning)", res.ok, res.errors)
        with pikepdf.open(dst) as q:
            xmps = [o.read_bytes() for o in q.objects if isinstance(o, pikepdf.Stream) and _s(o.get("/Type")) == "/Metadata"]
            _check("root XMP (compressed) and image XMP cleaned: no GUIDs in any stream",
                   len(xmps) == 2 and not any(b"uuid:" in x or b"DocumentID" in x for x in xmps), len(xmps))
            _check("/PieceInfo (root, page), /Thumb, /AF removed",
                   "/PieceInfo" not in q.Root and "/PieceInfo" not in q.pages[0].obj and "/Thumb" not in q.pages[0].obj
                   and "/AF" not in q.Root)
        _check("actions have both /PieceInfo and /AF with the file name", any(a.label == "/AF" and "af.xml" in a.before for a in res.actions)
               and any(a.label == "/PieceInfo" for a in res.actions))
        pdf = _fx_base()
        pdf.Root.Metadata.write(zlib.compress(b"\x00" * (8 << 20)), filter=N.FlateDecode)   # bomb: 8 MB of zeros in ~8 KB
        s12b = _write(tmp, "xmpbomb", pdf)
        res, dst = run(s12b, "newdir/bomb")
        with pikepdf.open(dst) as q:
            small = len(q.Root.Metadata.read_bytes()) < 1000
        _check("bomb in XMP: not decompressed, XMP replaced with an empty shell, dst directory "
               "created", res.ok and small and res.count(Act.NORMALIZED) >= 1, res.errors)
        res, dst = run(s12b, "bomb_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            _check("bomb in XMP, PARANOID: stream removed", res.ok and "/Metadata" not in q.Root, res.errors)
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        pdf.Root.Metadata = pdf.make_stream(_EMPTY_XMP, Type=N.Metadata, Subtype=N.XML)
        _save(pdf, os.path.join(tmp, "xmp_bytes.pdf"))
        with pikepdf.open(os.path.join(tmp, "xmp_bytes.pdf")) as q:
            _check("_save does not re-serialize the root XMP (open_metadata is not called)",
                   q.Root.Metadata.read_bytes() == _EMPTY_XMP)

        # --- 13. inspector: linearized files, /Info classes, bounded streams, texts -------
        print("13. inspector: linearized files, /Info classes, bounded streams, exiftool, texts")
        import base64
        import json
        import time
        import types

        from . import insp_pdf
        from .insp_pdf import _stream_fits
        from .model import Report
        from .report import render_plain

        # A linearized file (Fast Web View) has a second %%EOF/startxref and a /Prev in the
        # first-page trailer by design: that is not an incremental update.
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        lin = _write(tmp, "lin", pdf, linearize=True, object_stream_mode=pikepdf.ObjectStreamMode.disable)
        _check("fixture: a linearized file has two %%EOF", _raw(lin)[0] == 2, _raw(lin))
        a13 = inspect_file(lin)
        _check("linearized: no \"Incremental updates\", no hazard",
               not any("Incremental" in f.label for f in a13.findings) and not any(s.kind == "hazard" for s in a13.signals))
        li = os.path.join(tmp, "lin_inc.pdf")
        _fx_incremental(li, linearize=True)
        a13 = inspect_file(li)
        inc = [f.value for f in a13.findings if "Incremental" in f.label]
        _check("linearized + an appended revision: still reported, as 2 revisions, with the hazard",
               len(inc) == 1 and inc[0].startswith("2 revisions") and any(s.kind == "hazard" for s in a13.signals), inc)
        txt = render_plain(a13)
        _check("report text: %PDF- header, %%EOF end marker (no doubled percent in the header, none lost in the end marker)",
               "%%PDF" not in txt and "(%PDF-)" in txt and "(by %%EOF)" in txt, [l for l in txt.splitlines() if "EOF" in l or "PDF-" in l])
        r13 = CleanResult(src="", dst="", fmt="pdf", profile="stealth")
        _verify(r13, li, {}, 1, False)
        _check("_verify prints the real marker %%EOF", any("%%EOF markers" in e for e in r13.errors), r13.errors)

        # The cleaner's /Info rules are the inspector's: an email in a provenance field is identity,
        # a file name instead of a title is environment.
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        pdf.docinfo["/Subject"] = "ivan@corp.example"
        pdf.docinfo["/Title"] = "Договор_Иванов_финал.docx"
        pdf.docinfo["/Keywords"] = "plain words"
        s13 = _write(tmp, "infoclass", pdf)
        risks = {f.label: f.risk.value for f in inspect_file(s13).findings if f.location == "/Info"}
        _check("inspector: email in /Subject is identity, file name in /Title is environment, plain /Keywords stay provenance",
               (risks.get("Subject"), risks.get("Title"), risks.get("Keywords")) == ("identity", "environment", "provenance"), risks)
        res, dst = run(s13, "infoclass")
        i13 = info_of(dst)
        _check("the cleaner agrees: /Subject and /Title removed, /Keywords kept",
               res.ok and "/Subject" not in i13 and "/Title" not in i13 and "/Keywords" in i13, i13)

        # read_bytes() decodes the whole stream, so the decoded size is bounded before it is called.
        bp = pikepdf.Pdf.new()

        def _stream(data, **kw):
            st = bp.make_stream(b"")
            st.write(data, **kw)
            return st

        cap = 1 << 20
        bomb64 = _stream(zlib.compress(b"\x00" * (64 << 20)), filter=N.FlateDecode)
        small = zlib.compress(b"\x00" * 1000)
        pred_bomb = _stream(zlib.compress(b"\x02" + b"\x00" * 9), filter=N.FlateDecode,
                            decode_parms=pikepdf.Dictionary(Predictor=12, Columns=50_000_000))
        pred_ok = _stream(zlib.compress(b"\x02" + b"\x00" * 99), filter=N.FlateDecode,
                          decode_parms=pikepdf.Dictionary(Predictor=12, Columns=100))
        crypt = bp.make_stream(small)
        crypt.Filter = pikepdf.Array([N.Crypt, N.FlateDecode])
        crypt.DecodeParms = pikepdf.Array([pikepdf.Dictionary(Name=N.Identity), None])
        a85 = bp.make_stream(base64.a85encode(small, adobe=True))
        a85.Filter = pikepdf.Array([N.ASCII85Decode, N.FlateDecode])
        a85_bomb = bp.make_stream(base64.a85encode(zlib.compress(b"\x00" * (64 << 20)), adobe=True))
        a85_bomb.Filter = pikepdf.Array([N.ASCII85Decode, N.FlateDecode])
        # The bound must come before any decoding: read_bytes() may not be called at all.
        real_read = pikepdf.Object.read_bytes
        pikepdf.Object.read_bytes = lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded"))
        try:
            fits = {name: _stream_fits(st, cap) for name, st in (
                ("bomb", bomb64), ("small", _stream(small, filter=N.FlateDecode)), ("plain", _stream(b"x" * 100)),
                ("plain_big", _stream(b"x" * (cap + 1))), ("lzw", _stream(b"\x00", filter=N.LZWDecode)),
                ("corrupt", _stream(b"not zlib", filter=N.FlateDecode)), ("pred_bomb", pred_bomb),
                ("pred_ok", pred_ok), ("crypt", crypt), ("a85", a85), ("a85_bomb", a85_bomb))}
        finally:
            pikepdf.Object.read_bytes = real_read
        _check("_stream_fits: Flate bombs are refused without being inflated, also behind ASCII85",
               not fits["bomb"] and not fits["a85_bomb"], fits)
        _check("_stream_fits: small Flate and plain streams fit, also behind /Crypt and ASCII85",
               fits["small"] and fits["plain"] and fits["crypt"] and fits["a85"], fits)
        _check("_stream_fits: a predictor whose row alone is over the cap does not fit; a normal one does",
               not fits["pred_bomb"] and fits["pred_ok"], fits)
        _check("_stream_fits: a plain stream over the cap and a filter chain that can't be bounded do not fit",
               not fits["plain_big"] and not fits["lzw"], fits)
        _check("_stream_fits: corrupt Flate data fits (QPDF stops short of the cap)", fits["corrupt"], fits)

        # A long value without "@" must not make the email search quadratic (a tiny PDF
        # could otherwise stall the inspector for hours).
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        pdf.docinfo["/Subject"] = "a" * 200_000
        pdf.docinfo["/Keywords"] = "contact: ivan@corp.example"
        slow = _write(tmp, "redos", pdf)
        t0 = time.time()
        a13 = inspect_file(slow)
        took = time.time() - t0
        risks = {f.label: f.risk.value for f in a13.findings if f.location == "/Info"}
        _check("a 200 KB /Info value without \"@\" is inspected fast; an email is still identity",
               took < 5 and risks.get("Keywords") == "identity" and risks.get("Subject") == "provenance",
               (round(took, 2), risks))

        # XMP values are classified by content as the cleaner removes them: an email or a
        # file name instead of a title is critical in the report too.
        from .insp_pdf import _xmp_class
        _check("XMP: an email in a value is identity, a file-name dc:title is environment, a title stays provenance",
               (_xmp_class("dc:description", "write to ivan@corp.example")[0], _xmp_class("dc:title", "Договор_Иванов.docx")[0],
                _xmp_class("dc:title", "Annual report")[0]) == (Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE))

        # PDF/UA needs a title, so a real one stays even in PARANOID; a file name in its
        # place is a leak and goes in every profile, as the oracle says.
        ua_xmp = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
                  b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
                  b'rdf:about="" xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"><pdfuaid:part>1'
                  b'</pdfuaid:part></rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>')
        for title, prof, kept in (("Annual report", Profile.PARANOID, True),
                                  ("Договор_Иванов_финал.docx", Profile.STEALTH, False),
                                  ("Договор_Иванов_финал.docx", Profile.PARANOID, False)):
            pdf = pikepdf.Pdf.new()
            pdf.add_blank_page()
            pdf.Root.Metadata = pdf.make_stream(ua_xmp, Type=N.Metadata, Subtype=N.XML)
            pdf.docinfo["/Title"] = title
            ua = _write(tmp, "ua_%s_%s" % (prof.value, int(kept)), pdf)
            res, dst = run(ua, "ua_%s_%s" % (prof.value, int(kept)), prof)
            from .clean import survived_values
            left = survived_values(inspect_file(ua), inspect_file(dst))
            _check("PDF/UA, %s, title %r: %s, and the oracle sees no original value survive"
                   % (prof.value, title, "kept" if kept else "removed"),
                   res.ok and ("/Title" in info_of(dst)) == kept and not left, (res.errors, info_of(dst), left))

        # A refused /Metadata must not be decoded after all by the open_metadata() fallback.
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        pdf.Root.Metadata = pdf.make_stream(b"", Type=N.Metadata, Subtype=N.XML)
        pdf.Root.Metadata.write(zlib.compress(b"\x00" * (4 << 20)), filter=N.FlateDecode)

        class _Pdf:
            Root = pdf.Root

            def open_metadata(self):
                raise AssertionError("fallback decoded the refused stream")

        real_cap, insp_pdf.MAX_STREAM = insp_pdf.MAX_STREAM, 1 << 20
        try:
            r13 = Report(path="", fmt="pdf")
            insp_pdf._sec_xmp(_Pdf(), r13, {})
        finally:
            insp_pdf.MAX_STREAM = real_cap
        _check("oversized /Metadata: refused with bomb protection, and the open_metadata() fallback is not run on it",
               any("bomb protection" in e for e in r13.errors) and not any("open_metadata" in e for e in r13.errors), r13.errors)

        # exiftool reports the temporary download (System/File/ExifTool groups) next to the document.
        out = json.dumps([{"SourceFile": "x.pdf", "System:FileCreateDate": "2026:10:20 00:26:20-04:00",
                           "System:FileName": "x.pdf", "ExifTool:ExifToolVersion": 12.0, "File:FileType": "PDF",
                           "PDF:Author": "Ivan", "XMP-dc:Creator": "Ivan"}]).encode()
        real = insp_pdf.shutil, insp_pdf.subprocess
        insp_pdf.shutil = types.SimpleNamespace(which=lambda _n: "exiftool")
        insp_pdf.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=out))
        try:
            r13 = Report(path="", fmt="pdf")
            insp_pdf._enrich_exiftool("x.pdf", r13, {})
        finally:
            insp_pdf.shutil, insp_pdf.subprocess = real
        _check("exiftool: tags of the temporary copy are skipped, document tags are kept",
               {f.label for f in r13.findings} == {"Author", "Creator"}, [f.label for f in r13.findings])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nTOTAL: %d checks, %d failed" % (_TOTAL[0], len(_FAILS)))
    for f in _FAILS:
        print("  FAIL:", f)
    return 1 if _FAILS else 0


def _real(paths):
    """Cleaning metrics on real files. Content and any personal data found are not printed."""
    import shutil

    from .inspect import inspect_file

    def facts(path):
        with pikepdf.open(path) as q:
            inf = q.trailer.get("/Info")
            app = {k: (_s(inf.get(k)) if isinstance(inf, pikepdf.Dictionary) and k in inf else None)
                   for k in ("/Creator", "/Producer")}
            keys = {k for k in ("/StructTreeRoot", "/MarkInfo", "/Lang", "/Outlines", "/PageLabels", "/ViewerPreferences")
                    if k in q.Root}
            outl = sum(1 for o in q.objects
                       if isinstance(o, pikepdf.Dictionary) and "/Prev" in o and "/Title" in o and "/Parent" in o)
            return {"pages": len(q.pages), "app": app, "keys": keys, "trailer_prev": "/Prev" in q.trailer, "outl_prev": outl}

    tmp = tempfile.mkdtemp(prefix="clean_pdf_real_")
    try:
        for n, p in enumerate(paths, 1):
            h0 = _sha256(p)
            b = inspect_file(p)
            eof0, prev0 = _raw(p)
            f0 = facts(p)
            print("file %d: pages %d; BEFORE: critical %d, sensitive %d, %%%%EOF %d, raw /Prev %d "
                  "(of them /Outlines bookmark links: %d), /Prev in trailer: %s"
                  % (n, f0["pages"], b.critical, b.sensitive, eof0, prev0, f0["outl_prev"], f0["trailer_prev"]))
            for prof in (Profile.STEALTH, Profile.PARANOID):
                dst = os.path.join(tmp, "o%d_%s.pdf" % (n, prof.value))
                res = clean_pdf(p, dst, prof)
                if not res.ok:
                    print("  %-8s ok=False errors=%s" % (prof.value, res.errors))
                    continue
                a = inspect_file(dst)
                eof1, prev1 = _raw(dst)
                f1 = facts(dst)
                new_kinds = sorted({k for k, _ in {(s.kind, s.detail) for s in a.signals} - {(s.kind, s.detail) for s in b.signals}
                                    if k in ("scrubbed", "inconsistent")})
                left = sorted({(f.location, f.label.split(" ")[0]) for f in a.findings if f.risk.value in ("identity", "environment")})
                print("  %-8s ok=%s errors=%d  critical %d->%d  sensitive %d->%d" % (prof.value, res.ok, len(res.errors), b.critical, a.critical, b.sensitive, a.sensitive))
                print("           %%%%EOF %d->%d; raw /Prev %d->%d (bookmark links left %d); /Prev "
                      "in the TRAILER %s->%s"
                      % (eof0, eof1, prev0, prev1, f1["outl_prev"], f0["trailer_prev"], f1["trailer_prev"]))
                print("           structural kept %s of %s; Creator/Producer as they were: %s; "
                      "dst opens in pikepdf, pages %s"
                      % (sorted(f1["keys"]), sorted(f0["keys"]), f1["app"] == f0["app"], "same" if f1["pages"] == f0["pages"] else "CHANGED"))
                print("           re-inspect: new scrubbed/inconsistent signals: %s; critical "
                      "left: %s" % (new_kinds or "none", left))
                print("           actions: %s" % {k.value: len(v) for k, v in res.by_act().items()})
            print("           sha256 of the input before == after: %s (%s...)" % (_sha256(p) == h0, h0[:12]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2 and sys.argv[1] == "--real":
        _real(sys.argv[2:])
    else:
        sys.exit(_selftest())
