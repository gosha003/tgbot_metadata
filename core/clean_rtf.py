"""In-place RTF cleaning: editing control words without rebuilding the document.

Phase 2. Called by the core.clean dispatcher for the "rtf" family. Uses the
group parsing from core.insp_legacy (the same brace walk as the inspector;
a different one would drift on \\{ and \\u).

Public API:
    clean_rtf(src, dst, profile, keep, force_signed=False) -> CleanResult

Never raises. The input file is not modified. On refusal no output file is
created.

STEALTH removes identity and environment (author, company, template, paths,
revision table, custom properties) and keeps generator, dates and rsid:
they're the truth about the file, and their absence is a cleaning trace.
PARANOID removes those too.
"""

from __future__ import annotations

import os
import re

from core.cleanmodel import Act, CleanResult, Profile
from core.insp_legacy import (
    _PATH_DECODED_RE,
    _PATH_RE,
    _brace_group,
    _cp_codec,
    _info_groups,
    _rtf_plain,
    _subgroups,
)
from core.i18n import tr

__all__ = ["clean_rtf"]

_MAX = 32 * 1024 * 1024

# Groups inside \info. Identity and environment always; provenance only in
# PARANOID, otherwise a file without a title and dates looks processed by itself.
_LEAK = frozenset(("author", "operator", "company", "manager", "hlinkbase"))
_PROV = frozenset(("title", "subject", "keywords", "category", "doccomm",
                   "comment", "creatim", "revtim", "printim", "buptim"))
_NUMS = ("edmins", "nofpages", "nofwords", "nofchars", "nofcharsws",
         "version", "vern", "id")


def _enc(text) -> str:
    m = re.search(r"\\ansicpg(\d+)", text[:4096])
    return _cp_codec(m.group(1)) if m else "cp1252"


def _kept(keep, name) -> bool:
    if not keep:
        return False
    bag = {str(x).casefold() for x in keep}
    return name.casefold() in bag


def _apply(text, edits):
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def _balanced(text) -> bool:
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def _find(text, dest):
    """First {\\dest or {\\*\\dest group. Returns (start, end) including braces."""
    m = re.search(r"\{\\\*?\\?%s(?![a-zA-Z])" % dest, text)
    if not m:
        return None
    _body, end = _brace_group(text, m.start())
    return m.start(), end


def _blank_span(text, start, end):
    """{\\word content} -> {\\word}. The content is everything after the control word."""
    inner = text[start + 1:end - 1]
    # {\*\template ...} is "\*\" plus "template", not "\*\" plus "\template".
    # With the second variant the greedy pattern eats the word's own backslash and
    # fails to match: verified on {\*\generator ...}.
    m = re.match(r"\\\*\\[a-zA-Z]+|\\[a-zA-Z]+", inner)
    if not m:
        return None
    return text[start:start + 1 + m.end()] + "}"


def _info_edits(text, paranoid, keep, res, enc):
    found = _find(text, "info")
    if not found:
        return []
    start, end = found
    body = text[start + 1:end - 1]
    base = start + 1
    edits = []
    drop = set(_LEAK)
    if paranoid:
        drop |= _PROV
    for name, content, inner in _info_groups(body):
        if name not in drop or _kept(keep, name):
            continue
        # Position of the subgroup in body: inner is its body without braces; search for it.
        at = body.find("{" + inner + "}")
        if at < 0:
            continue
        gs, ge = base + at, base + at + len(inner) + 2
        if re.match(r"\s*\\upr(?![a-zA-Z])", inner):
            blank = "{\\%s}" % name      # the whole {\upr ANSI+Unicode} -> one empty field
        else:
            blank = _blank_span(text, gs, ge)
        if blank is None:
            continue
        edits.append((gs, ge, blank))
        res.act(Act.BLANKED, "\\info\\%s" % name, name,
                _rtf_plain(content, enc), "",
                tr("The group was left empty: a missing field is more conspicuous than an empty "
                   "value."))
        body = body[:at] + " " * (len(inner) + 2) + body[at + len(inner) + 2:]
    if paranoid:
        for word in _NUMS:
            if _kept(keep, word):
                continue
            for m in re.finditer(r"\\%s-?\d+" % word, text[start:end]):
                edits.append((start + m.start(), start + m.end(), ""))
                res.act(Act.REMOVED, "\\info\\%s" % word, word, m.group(0), "",
                        tr("Numeric \\info property. PARANOID removes it."))
    return edits


def _drop_group(text, dest, res, label, note):
    found = _find(text, dest)
    if not found:
        return []
    start, end = found
    res.act(Act.REMOVED, "\\*\\%s" % dest, label, "", "", note)
    return [(start, end, "")]


def _blank_dest(text, dest, res, label, note):
    found = _find(text, dest)
    if not found:
        return []
    start, end = found
    blank = _blank_span(text, start, end)
    if not blank or blank == text[start:end]:
        return []
    res.act(Act.BLANKED, "\\*\\%s" % dest, label,
            _rtf_plain(text[start:end], "cp1251"), "", note)
    return [(start, end, blank)]


def _revtbl(text, res, enc):
    found = _find(text, "revtbl")
    if not found:
        return []
    start, end = found
    body = text[start + 1:end - 1]
    base = start + 1
    edits = []
    for _name, _content, inner in _subgroups(body):
        value = _rtf_plain(inner, enc).strip(" ;")
        if not value or value.lower() == "unknown":
            continue
        at = body.find("{" + inner + "}")
        if at < 0:
            continue
        edits.append((base + at, base + at + len(inner) + 2, ""))
        res.act(Act.REMOVED, "\\*\\revtbl", tr("Revision author"), value, "",
                tr("A name from the revision table. The service entry Unknown stays: Word always "
                   "writes it, and an empty table is more conspicuous."))
    return edits


def _paths(text, res, enc):
    edits = []
    for m in _PATH_RE.finditer(text):
        raw = m.group(0)
        value = _rtf_plain(raw, enc)
        if not _PATH_DECODED_RE.match(value or ""):
            continue
        edits.append((m.start(), m.end(), ""))
        res.act(Act.REMOVED, tr("RTF stream"), tr("Absolute path"), value, "",
                tr("A path in the body or in a field. After decoding it's still a path, not a "
                   "fragment of a control word."))
    return edits


def _clean(res, src, dst, profile, keep):
    paranoid = Profile(profile) is Profile.PARANOID
    try:
        with open(src, "rb") as fh:
            raw = fh.read(_MAX + 1)
    except OSError as exc:
        res.err(tr("the file didn't open: %s") % type(exc).__name__)
        return
    if len(raw) > _MAX:
        res.err(tr("the file is larger than %d MB: partial cleaning isn't allowed, no output "
                   "created.")
                % (_MAX // (1024 * 1024)))
        return
    if not raw.lstrip()[:5] == b"{\\rtf":
        res.err(tr("this is not RTF: no {\\rtf signature. No output created."))
        return
    text = raw.decode("latin-1")
    enc = _enc(text)
    edits = []
    edits += _info_edits(text, paranoid, keep, res, enc)
    if not _kept(keep, "userprops"):
        edits += _drop_group(text, "userprops", res, tr("Custom properties"),
                             tr("Arbitrary organization fields. An ordinary file has no such "
                                "group, so removing it entirely stands out in no way."))
    edits += _revtbl(text, res, enc)
    if not _kept(keep, "template"):
        edits += _blank_dest(text, "template", res, tr("Template"),
                             tr("Template path. An empty group looks like a file without a "
                                "template."))
    if not _kept(keep, "atrfstart"):
        edits += _blank_dest(text, "atrfstart", res, tr("Annotation tag"),
                             tr("Initials of the comment's author."))
    if paranoid and not _kept(keep, "generator"):
        edits += _blank_dest(text, "generator", res, tr("Generator"),
                             tr("PARANOID removes the application string."))
    elif _find(text, "generator"):
        res.act(Act.KEPT, "\\*\\generator", tr("Generator"), "", "",
                tr("STEALTH: the application and version are the truth about the file. An empty "
                   "generator reads as a cleaning trace."))
    if paranoid and not _kept(keep, "rsidtbl"):
        edits += _blank_dest(text, "rsidtbl", res, tr("Session table"),
                             tr("PARANOID removes rsid. The file may then look built by a "
                                "library rather than Word."))
    # Paths come after groups: the template is already cut, and the same spot
    # isn't cut twice. Group spans and paths must not overlap; if a path lies
    # inside a group that's already cut, the second pass doesn't see it,
    # because edits are applied to the source text all at once. So paths are
    # computed on the text AFTER the group edits.
    staged = _apply(text, edits)
    out = _apply(staged, _paths(staged, res, enc))
    if not _balanced(out) or not out.lstrip().startswith("{\\rtf"):
        res.errors.clear()
        res.actions.clear()
        res.err(tr("the edit broke the RTF structure. No output created."))
        return
    tmp = str(dst) + ".part"
    try:
        with open(tmp, "wb") as fh:
            fh.write(out.encode("latin-1"))
        os.replace(tmp, dst)
    except OSError as exc:
        res.err(tr("could not write the result: %s") % type(exc).__name__)
        return
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    res.ok = True


def clean_rtf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult:
    """Clean RTF src -> dst. The input is not changed. Never raises.

    src, dst: paths. dst must not equal src; the dispatcher checks that.
    profile: Profile.STEALTH or Profile.PARANOID.
    keep: names of fields to keep (author, template, ...).
    force_signed: unused, RTF carries no signatures. Present so the signature
        matches the other cleaners.
    Returns a CleanResult. ok=False means there is no file.
    """
    del force_signed
    res = CleanResult(src=str(src), dst=str(dst), fmt="rtf",
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
        res.err(tr("Internal RTF cleaning error: %s") % type(exc).__name__)
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
    return res


def _selftest():
    import os
    import tempfile

    from core.clean import survived_values
    from core.inspect import inspect_file
    from tests.fixtures import RTF_AUTHOR, RTF_GENERATOR, make_rtf

    tmp = tempfile.mkdtemp(prefix="rtf_clean_")
    try:
        src, _exp = make_rtf(tmp)
        before = open(src, "rb").read()
        dst = os.path.join(tmp, "out.rtf")
        res = clean_rtf(src, dst, Profile.STEALTH)
        assert res.ok and not res.errors, res.errors
        assert open(src, "rb").read() == before
        rep = inspect_file(dst)
        assert not survived_values(inspect_file(src), rep), survived_values(inspect_file(src), rep)
        blob = " ".join(f.value or "" for f in rep.findings)
        assert RTF_AUTHOR not in blob
        assert RTF_GENERATOR in blob, "STEALTH must keep the generator"
        dst2 = os.path.join(tmp, "out2.rtf")
        res2 = clean_rtf(src, dst2, Profile.PARANOID)
        assert res2.ok, res2.errors
        blob2 = " ".join(f.value or "" for f in inspect_file(dst2).findings)
        assert RTF_GENERATOR not in blob2
        assert not survived_values(inspect_file(src), inspect_file(dst2))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    _selftest()
