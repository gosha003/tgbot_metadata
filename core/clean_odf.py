"""In-place ODF cleaning: meta.xml, settings.xml, comments, thumbnail, Pictures/.

Phase 2. The core.clean dispatcher calls clean_odf for the odf family
(.odt/.ods/.odp). core.zipfix repacks the container: only the listed parts
change, every other entry stays as raw compressed bytes, DOS time included.
So ZIP timestamps aren't normalized: Word's .odt has 1980-01-01,
LibreOffice's has the real save time. A hard choice would leave a
fingerprint on half the files.

XML is edited byte-wise, without a serializer: a round trip through lxml
changes attribute order and the form of empty tags.

Public API:
    clean_odf(src, dst, profile, keep, force_signed=False) -> CleanResult

Never raises. The input is not modified.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
import zlib

from core import sniff
from core.cleanmodel import Act, CleanResult, Profile
from core.insp_legacy import _PATH_RE
from core.zipfix import rebuild
from core.i18n import tr

__all__ = ["clean_odf"]

MAX_PICTURE = 64 * 1024 * 1024   # one image from Pictures/, like MAX_NESTED for OOXML
MAX_PART = 128 * 1024 * 1024     # one XML part once unpacked, like clean_ooxml.MAX_XML

# Leaves whose text is identity or environment. Any namespace prefix:
# Word and LibreOffice use different ones.
_LEAK = frozenset((
    "initial-creator", "creator", "printed-by", "template", "user-defined",
))
# Provenance. STEALTH keeps it: an empty generator is a scrubbed signal by itself.
_PROV = frozenset((
    "generator", "creation-date", "date", "print-date", "editing-cycles",
    "editing-duration", "keyword", "description", "subject", "title",
))

_LEAF = re.compile(
    br"<((?:[\w.-]+:)?)([\w.-]+)([^>]*)>([^<]*)</\1\2>")
# "annotation" must end at whitespace or ">": \b would also match the start of
# <office:annotation-end/>, and the lazy tail would swallow the text up to the next
# comment's closing tag. Start tags are read quote-aware (an attribute value may hold
# ">" or "/>") and never past the next "<", so a malformed tag can't make the scan
# run to the end of the document for every start.
_ANN = re.compile(
    br"""<((?:[\w.-]+:)?)annotation(?=[\s>])(?:[^<>"']|"[^"<]*"|'[^'<]*')*(?<!/)>.*?"""
    br"</\1annotation>", re.DOTALL)
# Ranged-comment end markers are empty elements, so they need their own pattern.
_ANN_END = re.compile(
    br"<((?:[\w.-]+:)?)annotation-end(?=[\s/>])"
    br"""(?:[^<>"']|"[^"<]*"|'[^'<]*')*?(?:/>|>\s*</\1annotation-end\s*>)""")
_STAT = re.compile(
    br"<((?:[\w.-]+:)?)document-statistic\b[^>]*/>")
_ENTRY = re.compile(
    br"<((?:[\w.-]+:)?)file-entry\b([^>]*?)/>")
# meta.xml elements whose path lives in the xlink:href attribute, not in the
# text: the document template (LibreOffice writes file:///C:/Users/NAME/...)
# and auto-reload. _LEAF doesn't see them because they're empty. They're
# removed entirely: a file without a template is ordinary, while an empty
# href would be a trace. Attribute values may contain ">", so quotes are
# parsed explicitly.
_LINKED = re.compile(
    br"<((?:[\w.-]+:)?)(template|auto-reload)(?=[\s/>])"
    br"""(?:[^>"']|"[^"]*"|'[^']*')*?(?:/>|>\s*</\1\2\s*>)""")


def _kept(keep, name) -> bool:
    if not keep:
        return False
    return name.casefold() in {str(x).casefold() for x in keep}


def _blank_leaves(xml, names):
    """Text of the listed leaves -> empty. Tags and attributes stay."""
    def sub(m):
        local = m.group(2).decode("ascii", "replace")
        if local not in names or not m.group(4).strip():
            return m.group(0)
        return (b"<" + m.group(1) + m.group(2) + m.group(3) + b"></"
                + m.group(1) + m.group(2) + b">")
    return _LEAF.sub(sub, xml)


def _blank_settings(xml):
    """A config-item with a printer or a path. Other settings are content."""
    def sub(m):
        if m.group(2) != b"config-item":
            return m.group(0)
        attrs = m.group(3).decode("utf-8", "replace").lower()
        text = m.group(4).decode("utf-8", "replace")
        if "printer" not in attrs and not _PATH_RE.search(text):
            return m.group(0)
        if not m.group(4).strip():
            return m.group(0)
        return (b"<" + m.group(1) + m.group(2) + m.group(3) + b"></"
                + m.group(1) + m.group(2) + b">")
    return _LEAF.sub(sub, xml)


def _strip_annotations(xml):
    if b"annotation" not in xml:
        return xml
    prev = None
    while prev != xml:
        prev = xml
        xml = _ANN.sub(b"", xml)
    return _ANN_END.sub(b"", xml)


def _strip_stats(xml):
    return _STAT.sub(
        lambda m: b"<" + m.group(1) + b"document-statistic/>", xml)


def _strip_thumb_entries(xml):
    def sub(m):
        attrs = m.group(2).decode("utf-8", "replace").lower()
        if "thumbnails/" in attrs:
            return b""
        return m.group(0)
    return _ENTRY.sub(sub, xml)


class _TooBig(Exception):
    """A part over MAX_PART once unpacked: refused as too large, not as corruption."""

    def __init__(self, name, limit):
        super().__init__(name)
        self.name, self.limit = name, limit


def _read(path, name, limit=None):
    limit = MAX_PART if limit is None else limit
    with zipfile.ZipFile(path) as zf:
        try:
            fh = zf.open(name)
        except KeyError:
            return None
        with fh:
            data = fh.read(limit + 1)      # file_size in the header may lie: read with our own cap
    if len(data) > limit:
        raise _TooBig(name, limit)
    return data


def _names(path):
    with zipfile.ZipFile(path) as zf:
        return [i.filename for i in zf.infolist()]


def _generator(meta):
    for m in _LEAF.finditer(meta):
        if m.group(2) == b"generator":
            return m.group(4).decode("utf-8", "replace").strip()
    return ""


def _clean_one(tmp, i, data, prof, keep):
    """Attachment bytes -> (family, CleanResult | None, new bytes | None)."""
    p_in = os.path.join(tmp, "p%04d.in" % i)
    p_out = os.path.join(tmp, "p%04d.out" % i)
    with open(p_in, "wb") as fh:
        fh.write(data)
    fam = sniff.family(sniff.sniff(p_in))
    if fam == "image":
        from core.clean_image import clean_image
        sub = clean_image(p_in, p_out, prof, keep)
    elif fam == "pdf":
        # LibreOffice puts an inserted PDF into Pictures/ as an image.
        from core.clean_pdf import clean_pdf
        sub = clean_pdf(p_in, p_out, prof, keep)
    else:
        return fam, None, None
    out = None
    if sub.ok and os.path.isfile(p_out):
        with open(p_out, "rb") as fh:
            out = fh.read()
    return fam, sub, out


def _clean_pictures(res, src, names, prof, keep, replace):
    """Pictures/ -> the image cleaner, like media/ in OOXML. The inspector walks
    Pictures/ recursively, so without this a photo's EXIF failed the oracle."""
    pics = [n for n in names if n.startswith("Pictures/") and not n.endswith("/")]
    if not pics:
        return
    what = tr("Embedded image")
    skipped = {}
    tmp = tempfile.mkdtemp(prefix=".odf_")
    try:
        with zipfile.ZipFile(src) as zf:
            for i, name in enumerate(pics):
                try:
                    # file_size in the header may lie: read with our own cap.
                    with zf.open(name) as fh:
                        data = fh.read(MAX_PICTURE + 1)
                    if len(data) > MAX_PICTURE:
                        res.act(Act.UNREMOVABLE, name, what, tr("> %d MB") % (MAX_PICTURE >> 20), "",
                                tr("Attachment larger than %d MB: not loaded into memory; "
                                   "metadata inside remains.") % (MAX_PICTURE >> 20))
                        continue
                    fam, sub, out = _clean_one(tmp, i, data, prof, keep)
                except (zipfile.BadZipFile, zlib.error, EOFError):
                    raise   # container corruption: clean_odf handles that as a whole
                except Exception as exc:  # noqa: BLE001 -- one image doesn't take down the rest
                    res.act(Act.FAILED, name, what, "", "",
                            tr("Cleaning the attachment crashed (%s): metadata inside remains.")
                            % type(exc).__name__)
                    continue
                finally:
                    for p in os.listdir(tmp):
                        try:
                            os.remove(os.path.join(tmp, p))
                        except OSError:
                            pass
                if sub is None:
                    tail = name.rsplit("/", 1)[-1]
                    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else "?"
                    skipped[ext] = skipped.get(ext, 0) + 1
                    continue
                if not (sub.ok and out is not None):
                    res.act(Act.UNREMOVABLE, name, what, "", "",
                            tr("The attachment could not be cleaned (%s): metadata inside "
                               "remains.")
                            % "; ".join(sub.errors)[:300])
                    continue
                if sub.actions and all(a.act is Act.KEPT for a in sub.actions):
                    res.act(Act.KEPT, name, tr("Embedded file without metadata"), "", "",
                            tr("The attachment's cleaner removed nothing."))
                else:
                    for a in sub.actions:
                        res.act(a.act, "%s > %s" % (name, a.location), a.label, a.before,
                                a.after, a.note)
                if out != data:
                    replace[name] = out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if skipped:
        res.act(Act.KEPT, "Pictures/", tr("Non-raster images"),
                ", ".join("%s: %d" % kv for kv in sorted(skipped.items())), "",
                tr("Formats the image cleaner doesn't parse (SVM, EMF, WMF, SVG): metadata in "
                   "them was neither searched for nor removed."))


def _clean(res, src, dst, prof, keep):
    paranoid = prof is Profile.PARANOID
    try:
        names = _names(src)
    except Exception as exc:  # noqa: BLE001
        res.err(tr("ODF didn't open as ZIP: %s") % type(exc).__name__)
        return
    replace = {}
    meta = _read(src, "meta.xml")
    if meta is None:
        res.err(tr("no meta.xml: nothing to clean, no output created."))
        return
    gen = _generator(meta)
    want = set(_LEAK)
    if paranoid:
        want |= _PROV
    want = {n for n in want if not _kept(keep, n)}
    new_meta = _blank_leaves(meta, want)
    linked = []

    def drop_linked(m):
        local = m.group(2).decode("ascii")
        if _kept(keep, local):
            return m.group(0)
        linked.append(local)
        return b""

    new_meta = _LINKED.sub(drop_linked, new_meta)
    if linked:
        res.act(Act.REMOVED, "meta.xml", tr("file links (%s)") % ", ".join(linked), "", "",
                tr("Template and auto-reload store a path in the xlink:href attribute: usually "
                   "file:///C:/Users/NAME/... or a network share."))
    if paranoid and not _kept(keep, "document-statistic"):
        new_meta = _strip_stats(new_meta)
    if new_meta != meta:
        replace["meta.xml"] = new_meta
        res.act(Act.BLANKED, "meta.xml", tr("document properties"), "", "",
                tr("Identity blanked, elements kept: their absence would itself be a cleaning "
                   "trace. Generator removed.")
                if paranoid and "generator" in want else
                tr("Identity blanked, elements kept: their absence would itself be a cleaning "
                   "trace. Generator kept."))
    if gen and not _kept(keep, "zip-time"):
        word = "microsoft" in gen.lower()
        res.act(Act.KEPT, "ZIP", tr("entry times"), "", "",
                tr("ZIP times left as written by %s (%s). Changing them would itself be a "
                   "processing trace.")
                % ("Word" if word else tr("the producer"),
                   "1980-01-01" if word else tr("real save time")))

    settings = _read(src, "settings.xml")
    if settings is not None and not _kept(keep, "settings"):
        new_s = _blank_settings(settings)
        if new_s != settings:
            replace["settings.xml"] = new_s
            res.act(Act.BLANKED, "settings.xml", tr("paths and printer"), "", "",
                    tr("Printer name and local paths. Other settings are untouched."))

    for part in ("content.xml", "styles.xml"):
        blob = _read(src, part)
        if blob is None or _kept(keep, part):
            continue
        new = _blank_leaves(_strip_annotations(blob), {"creator"})
        if new != blob:
            replace[part] = new
            res.act(Act.REMOVED, part, tr("revisions and comments"), "", "",
                    tr("Revision author names blanked, comments taken out: the inspector counts "
                       "comment text as an identity leak."))

    drop = [n for n in names if n.startswith("Thumbnails/")]
    if drop and not _kept(keep, "Thumbnails"):
        manifest = replace.get("META-INF/manifest.xml") or _read(src, "META-INF/manifest.xml")
        if manifest:
            cleaned = _strip_thumb_entries(manifest)
            if cleaned != manifest:
                replace["META-INF/manifest.xml"] = cleaned
        res.act(Act.REMOVED, "Thumbnails/", tr("thumbnail"), "%d" % len(drop), "",
                tr("The preview image may show an earlier version of the page."))

    _clean_pictures(res, src, names, prof, keep, replace)

    if not replace and not drop:
        res.act(Act.KEPT, "ODF", tr("nothing to remove"), "", "",
                tr("No identity or environment found in meta.xml and settings.xml."))
    rep = rebuild(src, dst, replace or None, drop or None)
    if not rep.ok:
        res.err(rep.errors[0] if rep.errors else tr("rebuilding the ODF failed"))
        return
    res.ok = True


def clean_odf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult:
    """Clean ODF src -> dst. The input is not changed. Never raises.

    profile: STEALTH keeps the generator and dates, PARANOID removes them too.
    keep: local names of elements to leave alone (creator, generator).
    force_signed: unused. The signature matches the other cleaners.
    """
    del force_signed
    res = CleanResult(src=str(src), dst=str(dst), fmt="odt",
                      profile=getattr(profile, "value", str(profile)))
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err(tr("Unknown cleaning profile."))
        return res
    try:
        _clean(res, str(src), str(dst), prof, keep)
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        if isinstance(exc, _TooBig):
            res.err(tr("Part %s is larger than %d MB once unpacked: not cleaned. No output "
                       "created.") % (exc.name, exc.limit >> 20))
        elif isinstance(exc, (zipfile.BadZipFile, zlib.error, EOFError)):
            # A broken entry (CRC, truncated deflate) is file corruption, not our error.
            res.err(tr("The ODF container is damaged: a part won't unpack (%s). No output "
                       "created.")
                    % type(exc).__name__)
        else:
            res.err(tr("Internal ODF cleaning error: %s") % type(exc).__name__)
        if os.path.exists(str(dst)):
            try:
                os.remove(dst)
            except OSError:
                pass
    return res


def _selftest():
    import shutil
    import struct
    import tempfile
    import tracemalloc

    from core.clean import survived_values
    from core.inspect import inspect_file
    from tests.fixtures import ODT_CREATOR, ODT_GENERATOR, ODT_INITIAL_CREATOR, make_odt

    tmp = tempfile.mkdtemp(prefix="odf_clean_")
    try:
        src, _exp = make_odt(tmp)
        before = open(src, "rb").read()
        dst = os.path.join(tmp, "out.odt")
        res = clean_odf(src, dst, Profile.STEALTH)
        assert res.ok and not res.errors, res.errors
        assert open(src, "rb").read() == before
        rep = inspect_file(dst)
        lived = survived_values(inspect_file(src), rep)
        assert not lived, [(s.label, s.value[:40]) for s in lived]
        blob = " ".join(f.value or "" for f in rep.findings)
        assert ODT_CREATOR not in blob and ODT_INITIAL_CREATOR not in blob
        assert ODT_GENERATOR in blob, "STEALTH must keep the generator"
        dst2 = os.path.join(tmp, "out2.odt")
        res2 = clean_odf(src, dst2, Profile.PARANOID)
        assert res2.ok, res2.errors
        blob2 = " ".join(f.value or "" for f in inspect_file(dst2).findings)
        assert ODT_GENERATOR not in blob2
        assert not survived_values(inspect_file(src), inspect_file(dst2))
        notes = {p: [a.note for a in r.actions if a.location == "meta.xml"
                     and a.label == tr("document properties")]
                 for p, r in (("stealth", res), ("paranoid", res2))}
        assert notes["stealth"][0].endswith("Generator kept."), notes
        assert notes["paranoid"][0].endswith("Generator removed."), notes

        # Two ranged comments with text between them: the end marker of the first must not
        # open a match that swallows the text up to the closing tag of the second.
        ranged = (b'<text:p>Hello <office:annotation office:name="a"><dc:creator>X</dc:creator>'
                  b'<text:p>c1</text:p></office:annotation>world'
                  b'<office:annotation-end office:name="a"/> KEEP THIS MIDDLE TEXT '
                  b'<office:annotation office:name="b"><text:p>c2</text:p></office:annotation>'
                  b'tail<office:annotation-end office:name="b"/> end</text:p>')
        out = _strip_annotations(ranged)
        assert out == b'<text:p>Hello world KEEP THIS MIDDLE TEXT tail end</text:p>', out
        src3 = os.path.join(tmp, "ranged.odt")
        content = (b'<?xml version="1.0" encoding="UTF-8"?><office:document-content '
                   b'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                   b'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                   b'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:body><office:text>'
                   + ranged + b'</office:text></office:body></office:document-content>')
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(src3, "w") as zout:
            for info in zin.infolist():
                zout.writestr(info, content if info.filename == "content.xml" else zin.read(info))
        dst3 = os.path.join(tmp, "out3.odt")
        res3 = clean_odf(src3, dst3, Profile.STEALTH)
        assert res3.ok and not res3.errors, res3.errors
        cleaned = _read(dst3, "content.xml")
        assert b"KEEP THIS MIDDLE TEXT" in cleaned and b"tail" in cleaned, cleaned
        assert b"annotation" not in cleaned and b"c1" not in cleaned, cleaned

        # A part bigger than the read cap is refused, and a header that understates the
        # size doesn't let the stream inflate past the cap.
        bomb = os.path.join(tmp, "bomb.odt")
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("content.xml", b" " * (16 << 20))
        for lie in (False, True):
            if lie:
                raw = bytearray(open(bomb, "rb").read())
                struct.pack_into("<I", raw, raw.rfind(b"PK\x01\x02") + 24, 100)
                open(bomb, "wb").write(raw)
            tracemalloc.start()
            try:
                _read(bomb, "content.xml", 1 << 20)
            except (_TooBig, zipfile.BadZipFile):    # our cap, or zipfile's CRC check on a lying header
                pass
            else:
                raise AssertionError("an oversized part was read in full")
            finally:
                peak = tracemalloc.get_traced_memory()[1]
                tracemalloc.stop()
            assert peak < 6 << 20, "the bomb was inflated in full: peak %d bytes" % peak
        assert _read(src, "content.xml") and _read(src, "no/such.xml") is None

        # An oversized part is refused as too large, not reported as a damaged container.
        global MAX_PART
        saved_part, MAX_PART = MAX_PART, 100
        try:
            res4 = clean_odf(src3, os.path.join(tmp, "out4.odt"), Profile.STEALTH)
        finally:
            MAX_PART = saved_part
        assert not res4.ok and any("larger than" in e for e in res4.errors), res4.errors
        assert not any("damaged" in e for e in res4.errors), res4.errors

        # Quotes in attributes: "/>" or ">" inside a value doesn't hide the comment, and a
        # self-closing annotation (no body) is left alone.
        for q in (b'"x/>y"', b"'x/>y'", b'"a>b"'):
            got = _strip_annotations(b'<text:p>A<office:annotation office:name=' + q
                                     + b'><dc:creator>I</dc:creator><text:p>secret</text:p>'
                                       b'</office:annotation>B</text:p>')
            assert got == b"<text:p>AB</text:p>", (q, got)
        assert _strip_annotations(b"<text:p>A<office:annotation/>B</text:p>") == \
            b"<text:p>A<office:annotation/>B</text:p>"
        # Malformed starts without a closing ">" stay linear (the scan stops at the next "<").
        import time
        junk = b"<office:annotation-end x='1" * 20000 + b"<office:annotation a " * 20000
        t0 = time.time()
        _strip_annotations(junk)
        assert time.time() - t0 < 5, "annotation patterns went quadratic"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    _selftest()
