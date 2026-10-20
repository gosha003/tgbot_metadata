# -*- coding: utf-8 -*-
"""Cleaning dispatcher and CLI (phase 1). Mirror of core/inspect.py.

    clean_file(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False,
               verify=True) -> DispatchResult

Detects the format via core.sniff and routes by core.sniff.family:
pdf -> clean_pdf, image -> clean_image, ooxml -> clean_ooxml, rtf -> clean_rtf,
odf -> clean_odf.
Other formats are refused honestly: there is no cleaning for them yet, and
pretending otherwise is not allowed. Handing the user a file they believe
is cleaned when it isn't is the worst outcome for this project.

PROOF. The inspector is the oracle: inspect_file(src) before cleaning,
inspect_file(dst) after. But success is NOT "critical_after == 0": in the
STEALTH profile a PDF keeps two critical findings, the regenerated trailer
/ID[0] and /ID[1]. That's not a leak: the values are new, random and
unrelated to the source. The real proof is survived_values(): which ORIGINAL
IDENTITY/ENVIRONMENT values (longer than 5 characters) appear in the
output's findings. An empty list means clean.

RESULT. cleanmodel.CleanResult must not be changed (contract), so the proof
lives in its subclass DispatchResult: real dataclass fields, not attributes
assigned on the fly, so they show up in dataclasses.asdict() and in --json.
The subclass is still a CleanResult for isinstance, and the clean property
is overridden: it takes survived into account, not just critical_after
(the base clean is always False for STEALTH PDFs even when the file is clean).

Three outcomes that must not be confused:
  ok=False                   -- no file handed over (dst not created, not touched);
  ok=True,  clean=False      -- file handed over, but cleanliness can't be proven
                                (original values remain, or verify=False);
  ok=True,  clean=True       -- file handed over, no original value survived.

THE INPUT IS NEVER MODIFIED: a dst equal to src is rejected (by normalized
path and by samefile), cleaning runs in a temp directory next to dst and
reaches dst with a single os.replace only after all checks, so an existing
dst stays untouched on any refusal.

Privacy: no file names or finding values are logged (there are no logs).
Stdlib only; heavy dependencies come in through the cleaners themselves.

CLI: python -m core.clean <file> [--out PATH] [--profile stealth|paranoid|regenerate]
                         [--force-signed] [--json] [--overwrite] [--selftest]
Exit code: 0 = clean and proven; 1 = no file handed over; 2 = handed over, cleanliness not proven.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from enum import Enum

from . import sniff
from .clean_image import clean_image
from .clean_ooxml import clean_ooxml
from .clean_odf import clean_odf
from .clean_pdf import clean_pdf
from .clean_regen import OUTPUT_FMT, clean_regen, find_soffice
from .clean_rtf import clean_rtf
from .lint import detect
from .cleanmodel import ACT_TITLE, Act, CleanResult, Profile
from .inspect import inspect_file
from .model import Risk, clip
from core.i18n import N_, both, tr

# Anything larger is refused outright: the PDF cleaner has its own 512 MB
# ceiling, images 50 MB (in memory). This is the dispatcher's general fuse.
MAX_FILE = 512 * 1024 * 1024

# "Longer than 5 characters": short values ("Word", "1.7", "ru") produce
# false substring matches, and cleanliness is judged by them.
MIN_VALUE_LEN = 6

_LEAKY = (Risk.IDENTITY, Risk.ENVIRONMENT)

# What to expect from formats that have no cleaning yet: (phase, explanation).
#
# The phase number lives HERE: cleanreport._no_cleaner() takes planned_phase
# from the dispatcher; its own table is only a fallback.
#
# A format belongs in phase 4 when it can't be cleaned in place at all.
# Legacy OLE2 is like that: metadata and revision history are embedded in the
# container, and the 1Table/0Table streams hold deleted text of earlier
# versions. ODF and RTF are cleaned in place.
_PLANNED = {
    "ole": (4, N_("Legacy OLE2 (doc/xls/ppt) can't be cleaned in place at all: metadata and "
                  "revision history are embedded in the container itself, so conversion through "
                  "a stock application is required: phase 4.")),
}

# Formats with no cleaning and none planned.
_NOT_PLANNED = {
    "zip": N_("Generic ZIP archive: cleaning archives is not planned."),
    "text": N_("Text/CSV: such a file has no structured document metadata; cleaning this format "
               "is not planned."),
}

_UNREADABLE = {
    "cloud": N_("the file is a cloud provider placeholder (OneDrive and the like): there's no "
                "content on disk. Open it in Explorer or choose \"Always keep on this device\", "
                "wait for the download and try again."),
    "denied": N_("no read access: no permission, or the file is locked by another process (most "
                 "often open in Word or Excel)."),
    "missing": N_("file not found."),
    "dir": N_("this is a directory, not a file."),
    "reparse": N_("the file is a link or reparse point; its content can't be read."),
}


# ============================================================================
#  Result
# ============================================================================


@dataclasses.dataclass
class Survivor:
    """An original sensitive value that was found in the output."""

    risk: str
    location: str      # where it was in the source
    label: str
    value: str
    found_at: str      # where it was found in the output: "location > label"

    def __post_init__(self) -> None:
        self.risk = Risk(self.risk).value
        self.value = clip(self.value)


@dataclasses.dataclass
class DispatchResult(CleanResult):
    """CleanResult + the dispatcher's proof. See the module docstring."""

    verified: bool = False          # inspect_file(dst) ran and the format matched
    survived: list = dataclasses.field(default_factory=list)   # [Survivor]
    # Critical findings the inspector still sees in the output: (location, label,
    # new_value). new_value=True means no source finding has such a value: a
    # regenerated PDF trailer /ID and the like. It's unrelated to the source, so
    # it's not a leak. Determined from the data, not from what the cleaner wrote
    # to actions: a PDF without /ID gets a fresh /ID from QPDF without any action.
    residual: list = dataclasses.field(default_factory=list)
    gaps: list = dataclasses.field(default_factory=list)       # what the inspector couldn't parse
    planned_phase: int = 0          # >0: format refused; cleaning will arrive in this phase
    # Signals that were NOT in the source but appeared in the output. Above all
    # "scrubbed" and "ai": they mean the file is now recognizable as processed.
    # That's not a leak (clean stays True), but the STEALTH profile exists for
    # invisibility, and silently handing over such a file would betray the one
    # thing the user picked STEALTH for.
    # For OOXML this isn't a cleaning defect but a limit of the format: empty
    # dc:creator and cp:lastModifiedBy together are a trace in themselves, and
    # every alternative either leaks or forges provenance. The real fix is
    # Regenerate (phase 4), i.e. recreating the file with a stock application.
    new_signals: list = dataclasses.field(default_factory=list)   # [(kind, detail)]
    # Score from the output's signals. none = the inspector sees no processing.
    detect: str = ""
    detect_why: list = dataclasses.field(default_factory=list)

    @property
    def clean(self) -> bool:
        """The file was handed over, no original IDENTITY/ENVIRONMENT value was found
        in it, and the inspector parsed the whole file (otherwise a leak could hide
        in an unparsed part). NOT the same as critical_after == 0; see the module docstring."""
        return bool(self.ok and self.verified and not self.survived and not self.gaps)


def _norm(value) -> str:
    # Values are clip()ped to 400 characters with a trailing "…"; the tail isn't needed for comparison.
    return " ".join(str(value).casefold().split()).rstrip("…")


# Marker values: the inspector uses them to denote the FACT that something is
# present ("Signature field "Sig1" = present"), not data. Comparing them across
# reports is pointless: the word "present" in the source and in the output is
# not a leak that survived cleaning but identical markup in two reports.
# A heuristic with a known limit: if someone's real name or path ever turns out
# to be exactly such a word, the oracle will miss it. For these words that's a
# price worth paying; otherwise the oracle catches its own vocabulary.
# The set holds every language, since reports are rendered in the user's language.
# "Author" is the neutral name the OOXML cleaner writes (clean_ooxml.AUTHOR, the same in
# every language): cleaning a file twice must not report it as a survivor.
_PLACEHOLDERS = frozenset(_norm(w) for p in (
    N_("present"), N_("no"), N_("yes"), N_("absent"), N_("empty"), N_("not set"),
    N_("on"), N_("off"), "true", "false", "none", "n/a", "Author",
) for w in both(p))


def _matcher(value):
    """How to look for an original value in the output's findings.

    A long value is searched as a substring: an author name that moved into the
    title is as much a leak as the Author field.

    A short one can't be searched as a substring ("ru" would be found inside
    "trust"), but it can't be SKIPPED either, which is exactly what used to
    happen. "Ivan", "Oleg", "Anna", "Bob" are shorter than six characters, and
    with them a liar cleaner (a plain copy of the file) got clean=True and a
    "CLEANED" verdict: the project's central claim failed on the most common
    kind of data, names. So a short value is searched on word boundaries:
    "Ivan" is found in "Author: Ivan" but not inside "Ivanov" or "trust".
    """
    if len(value) >= MIN_VALUE_LEN:
        return lambda n: value in n
    pat = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(value))
    return lambda n: pat.search(n) is not None


def survived_values(src_report, dst_report) -> list:
    """Which ORIGINAL IDENTITY/ENVIRONMENT values appear in the values of the
    output file's findings, of any risk: an author name that ended up in the
    title is as much a leak as the Author field.

    An empty list is the proof of cleanliness. Comparison ignores case and
    extra whitespace; the method depends on the value's length, see _matcher.
    Each unique value is reported once."""
    dst = [(g, _norm(g.value)) for g in dst_report.findings]
    dst = [(g, n) for g, n in dst if n]
    out, seen = [], set()
    for f in src_report.findings:
        if f.risk not in _LEAKY:
            continue
        v = _norm(f.value)
        if not v or v in seen or v in _PLACEHOLDERS:
            continue
        seen.add(v)
        hit = _matcher(v)
        for g, n in dst:
            if hit(n):
                out.append(Survivor(f.risk, f.location, f.label, f.value,
                                    "%s > %s" % (g.location, g.label)))
                break
    return out


# ============================================================================
#  Dispatcher
# ============================================================================


def _sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _norm_path(p) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _same_file(a, b) -> bool:
    """dst must not match src under any spelling of the path: different case,
    ".\\", a symlink, a hard link, an 8.3 short name."""
    if _norm_path(a) == _norm_path(b):
        return True
    try:
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


# Tests swap cleaners through this dict.
_CLEANERS = {
    "pdf": lambda s, d, prof, keep, force: clean_pdf(s, d, prof, keep, force),
    "image": lambda s, d, prof, keep, force: clean_image(s, d, prof, keep),
    "ooxml": lambda s, d, prof, keep, force: clean_ooxml(
        s, d, prof, keep, force_signed=force),
    "rtf": lambda s, d, prof, keep, force: clean_rtf(s, d, prof, keep),
    "odf": lambda s, d, prof, keep, force: clean_odf(s, d, prof, keep),
}


def _refuse(res, fmt, fam, size) -> None:
    """Format without a cleaner: honest refusal with a phase number."""
    name = sniff.pretty(fmt)
    if fam in _PLANNED:
        res.planned_phase, detail = _PLANNED[fam]
        detail = tr(detail)
        res.err(tr("File NOT cleaned: format \"%s\" is inspect-only for now; cleaning isn't "
                   "implemented yet. %s Output file not created.") % (name, detail))
    elif fam in _NOT_PLANNED:
        res.err(tr("File NOT cleaned: %s Output file not created.") % tr(_NOT_PLANNED[fam]))
    elif fam == "unknown":
        res.err(tr("File NOT cleaned: %s Output file not created.") %
                (tr("the file is empty (0 bytes); it may not have finished downloading.") if size == 0
                 else tr("format not recognized; nothing to clean it with.")))
    else:
        res.err(tr("File NOT cleaned: there is no cleaning for format \"%s\". Output file not "
                   "created.") % name)


def _run(res, tmpdirs, src, dst, profile, keep, force_signed, verify) -> None:
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err(tr("Unknown cleaning profile."))
        return
    res.profile = prof.value

    fmt = sniff.sniff(src)
    res.fmt = fmt
    if fmt == "unreadable":
        reason, code = sniff.unreadable_reason(src)
        res.err(tr("File not cleaned: %s") % tr(_UNREADABLE.get(
            reason, tr("the file can't be read (errno %s).") % code)))
        return

    # Never overwrite the input: check this before anything else.
    if _same_file(src, dst):
        res.err(tr("The output path matches the input path: the input file is never overwritten."))
        return
    if os.path.isdir(dst):
        res.err(tr("The output path is a directory; a file path is required."))
        return

    size = os.path.getsize(src)
    if size > MAX_FILE:
        res.err(tr("File larger than %d MB: cleaning not performed.") % (MAX_FILE >> 20))
        return

    fam = sniff.family(fmt)
    if prof is Profile.REGENERATE:
        if fmt not in OUTPUT_FMT or not find_soffice():
            res.planned_phase = 4
            if fmt not in OUTPUT_FMT:
                res.err(tr("The regenerate profile (re-creation via LibreOffice) doesn't apply "
                           "to format \"%s\", phase 4. File NOT cleaned, output file not created.")
                        % sniff.pretty(fmt))
            else:
                res.err(tr("The regenerate profile requires LibreOffice, which isn't installed "
                           "on this machine, phase 4. In-place cleaning is not substituted: the "
                           "invisibility would be different. File NOT cleaned, output file not "
                           "created."))
            return
        cleaner = clean_regen
    else:
        cleaner = _CLEANERS.get(fam)
        if cleaner is None:
            _refuse(res, fmt, fam, size)
            return

    # Temp directory next to dst: then os.replace is atomic (same volume), and
    # an existing dst stays untouched on refusal.
    parent = os.path.dirname(os.path.abspath(dst))
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".clean_", dir=parent)
    tmpdirs.append(tmp)
    out = os.path.join(tmp, "out.tmp")

    sha_before = _sha(src)
    sub = cleaner(src, out, prof, keep, force_signed)
    res.actions = list(sub.actions)
    for e in sub.errors:
        res.errors.append(e)          # already went through CleanResult.err (paths stripped)
    if not sub.ok:
        return
    if not os.path.isfile(out):
        res.err(tr("The cleaner reported success, but there is no output file."))
        return

    if verify:
        before = inspect_file(src, max_size=MAX_FILE)
        after = inspect_file(out, max_size=MAX_FILE)
        if after.fmt != (sub.fmt if prof is Profile.REGENERATE else fmt):
            res.err(tr("The output is detected as %s, not %s: file not handed over.") %
                    (sniff.pretty(after.fmt), sniff.pretty(sub.fmt if prof is Profile.REGENERATE else fmt)))
            return
        res.fmt = after.fmt
        res.critical_before, res.sensitive_before = before.critical, before.sensitive
        res.critical_after, res.sensitive_after = after.critical, after.sensitive
        res.survived = survived_values(before, after)
        was = {_norm(f.value) for f in before.findings}
        # A value shorter than MIN_VALUE_LEN can't be an identity leak, and
        # checking whether it occurs in the source is pointless: the EMPTY string
        # always occurs in the source, so fields we blanked ourselves
        # (dc:creator, Company) counted as "carrying an original value" and
        # the report scared users with a leak that didn't exist. Short and empty are not original.
        res.residual = [(g.location, g.label,
                         len(_norm(g.value)) < MIN_VALUE_LEN or _norm(g.value) not in was)
                        for g in after.findings if g.risk in _LEAKY]
        res.gaps = ([tr("before cleaning: ") + e for e in before.errors] +
                    [tr("after cleaning: ") + e for e in after.errors])
        was_sig = {(sg.kind, sg.detail) for sg in before.signals}
        was_kinds = {sg.kind for sg in before.signals}
        res.new_signals = [(sg.kind, sg.detail) for sg in after.signals
                           if (sg.kind, sg.detail) not in was_sig
                           and sg.kind in ("scrubbed", "ai")
                           and sg.kind not in was_kinds]
        res.detect, res.detect_why = detect(after)
        res.verified = True

    # The input didn't change during the run: otherwise "before" and "after" compared different bytes.
    if _sha(src) != sha_before:
        res.err(tr("The input file changed during processing: the result is unreliable, file not "
                   "handed over."))
        return

    try:
        os.replace(out, dst)
    except OSError as exc:
        res.err(tr("Could not write the output file (errno %s): it's locked by another process "
                   "or there's no permission. File not handed over.") % exc.errno)
        return
    res.ok = True


def clean_file(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False,
               verify=True) -> DispatchResult:
    """Clean src -> dst. The input is never modified. Never raises.

    ok=False: no file handed over; dst not created and an existing dst not
    touched; the reason is in errors. For a format without a cleaner,
    planned_phase > 0.
    verify=True: re-inspection before/after; critical_*, sensitive_*,
    survived, residual and gaps are filled in; res.clean is the verdict.
    verify=False: file handed over without proof (clean is always False).
    keep, force_signed: as in clean_pdf; images understand keep={"icc"}."""
    try:
        s, d = os.fsdecode(src), os.fsdecode(dst)
    except Exception:  # noqa: BLE001 -- junk instead of a path
        s, d = str(src), str(dst)
    res = DispatchResult(src=s, dst=d, fmt="unknown",
                         profile=str(getattr(profile, "value", profile)))
    tmpdirs = []
    try:
        _run(res, tmpdirs, s, d, profile, keep, force_signed, verify)
    except Exception as exc:  # noqa: BLE001 -- last line of defense: never crash on any input
        res.ok = False
        res.err(tr("Internal error in the cleaning dispatcher: %s: %s") % (type(exc).__name__, exc))
    finally:
        for t in tmpdirs:
            shutil.rmtree(t, ignore_errors=True)
    if not res.ok:
        # No file handed over: a list of "what was done" to a file that wasn't handed over only misleads.
        res.actions.clear()
        res.verified = False
        res.survived, res.residual, res.gaps = [], [], []
        res.new_signals, res.detect, res.detect_why = [], "", []
        res.critical_before = res.critical_after = res.sensitive_before = res.sensitive_after = -1
    return res


# ============================================================================
#  CLI
# ============================================================================


def _default_out(path, profile="stealth") -> str:
    base, ext = os.path.splitext(path)
    if profile == "regenerate":
        # Regeneration changes the format (doc -> docx): the extension follows the output, not the input.
        out = OUTPUT_FMT.get(ext.lower().lstrip("."))
        if out:
            ext = "." + out
    return base + ".clean" + ext


def _json_default(obj):
    return obj.value if isinstance(obj, Enum) else str(obj)


def _to_json(res) -> str:
    data = dataclasses.asdict(res)
    data["clean"] = res.clean
    return json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)


def _verdict(res) -> str:
    if not res.ok:
        return tr("NOT CLEANED: file not handed over")
    if res.clean:
        return tr("CLEANED: no original value found in the output")
    if not res.verified:
        return tr("WRITTEN WITHOUT VERIFICATION (verify=False): cleanliness not proven")
    # clean == ok and verified and not survived and not gaps. We can get here
    # with ZERO surviving values, when gaps is non-empty. Blaming surviving
    # values in that case would lie to the user about what exactly went wrong.
    if res.survived:
        return (tr("NOT FULLY CLEANED: %d original values remain")
                % len(res.survived))
    if res.gaps:
        return (tr("CLEANLINESS NOT PROVEN: no original values found, but the check could not be "
                   "completed (%s)") % "; ".join(res.gaps[:2]))
    return tr("CLEANLINESS NOT PROVEN: reason unknown")


def _render_plain(res, details=True) -> str:
    """The dispatcher's verdict and proof always come from here, by values, not by
    the critical_after counter. details=False: the details (format, actions,
    messages) are already in the core.cleanreport report; here only the verdict and paths."""
    lines = [tr("Result: ") + _verdict(res)]
    if details:
        lines.append(tr("Format: %s   Profile: %s") % (sniff.pretty(res.fmt), res.profile))
    if res.planned_phase:
        lines.append(tr("Cleaning for this format is planned for phase %d.") % res.planned_phase)
    if res.verified:
        lines.append(tr("Critical findings: %d -> %d   All sensitive: %d -> %d") % (
            res.critical_before, res.critical_after, res.sensitive_before, res.sensitive_after))
        lines.append(tr("Original values that survived cleaning: %d") % len(res.survived))
        for sv in res.survived[:20]:
            lines.append(tr("  ! %s (%s) -> found: %s") % (sv.label, sv.location, sv.found_at))
        if len(res.survived) > 20:
            lines.append(tr("  ... and %d more") % (len(res.survived) - 20))
        new = [r for r in res.residual if r[2]]
        old = [r for r in res.residual if not r[2]]
        if new:
            lines.append(tr("Critical in the output, but the values are new (not in the source, "
                            "unrelated to it): %d (%s)") % (
                             len(new), ", ".join(sorted({"%s %s" % (r[0], r[1].split(" ")[0]) for r in new}))))
        if old:
            lines.append(tr("Critical in the output with original values: %d") % len(old))
        if res.gaps:
            lines.append(tr("The inspector couldn't parse part of the file (%d); the proof is "
                            "incomplete:") % len(res.gaps))
            lines.extend("  - " + g for g in res.gaps[:10])
    if details:
        if res.ok and res.actions:
            lines.append(tr("Actions: ") + ", ".join(
                "%s: %d" % (tr(ACT_TITLE[a]), n) for a, n in ((a, res.count(a)) for a in Act) if n))
        if res.errors:
            lines.append(tr("Messages:"))
            lines.extend("  - " + e for e in res.errors)
    if res.ok:
        lines.append(tr("Output: ") + res.dst)    # cleanreport deliberately doesn't print paths
    return "\n".join(lines)


def _render(res) -> str:
    """The detailed core.cleanreport report (if the module is missing or has a
    different signature, fall back to our own output) + our verdict ON TOP: the
    result is read first, and it must be by values, not by the critical_after counter."""
    try:
        from .cleanreport import render_plain
        body = render_plain(res)
    except Exception:  # noqa: BLE001
        body = None
    if not body:
        return _render_plain(res)
    note = ""
    if res.clean and res.critical_after > 0 and all(r[2] for r in res.residual):
        # cleanreport judges by critical_after and doesn't know about survived: its
        # "critical left" is correct as a count, but not as a conclusion about a leak.
        note = (tr("\nThe detailed report below judges the remainder by the critical-findings "
                   "counter; the result above is by values: the remainder consists of new "
                   "values, not original ones.\n"))
    return "%s\n%s\n%s" % (_render_plain(res, details=False), note, body)


def _exit_code(res) -> int:
    return 1 if not res.ok else (0 if res.clean else 2)


# ============================================================================
#  Self-test: PYTHONIOENCODING=utf-8 python -m core.clean --selftest
# ============================================================================

_FAILS = []
_TOTAL = [0]


def _check(name, cond, detail=""):
    _TOTAL[0] += 1
    if not cond:
        _FAILS.append(name)
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           "  -- " + str(detail) if (detail and not cond) else ""))


def _one(value):
    from .model import Report
    r = Report(path="t", fmt="pdf")
    r.add(Risk.IDENTITY, "x", "x", value)
    return r


def _selftest() -> int:
    global MAX_FILE
    from .model import Report
    from tests import fixtures as fx

    tmp = tempfile.mkdtemp(prefix="clean_selftest_")
    try:
        # 1. survived_values on synthetic data: what counts and what doesn't.
        print("1. survived_values")
        a = Report(path="x", fmt="pdf")
        a.add(Risk.IDENTITY, "/Info", "Author", "Иван Петров")
        a.add(Risk.ENVIRONMENT, "/Info", "Path", "C:\\Users\\ivan\\отчёт.docx")
        a.add(Risk.IDENTITY, "/Info", "Short", "Bob")               # < 6: by word boundary
        a.add(Risk.IDENTITY, "/Info", "Inside", "Ann")                 # only inside a word
        a.add(Risk.PROVENANCE, "/Info", "Producer", "Microsoft Word 365")  # not IDENTITY/ENV
        a.add(Risk.IDENTITY, "XMP", "Author2", "иван   петров")        # duplicate after normalization
        b = Report(path="y", fmt="pdf")
        b.add(Risk.STRUCTURAL, "/Info", "Title", "Отчёт ИВАН ПЕТРОВ за квартал")  # any risk, case
        b.add(Risk.PROVENANCE, "/Info", "Producer", "Microsoft Word 365")
        b.add(Risk.IDENTITY, "/Info", "X", "Bob")
        b.add(Risk.STRUCTURAL, "/Info", "Y", "Annual report")
        got = survived_values(a, b)
        _check("name found in the title, case-insensitive",
               [g.label for g in got] == ["Author", "Short"], got)
        _check("path, PROVENANCE and short-inside-a-word are not counted",
               all(g.label not in ("Path", "Inside", "Producer") for g in got))
        _check("empty result for a clean output", survived_values(a, Report(path="z", fmt="pdf")) == [])
        _check("clean requires verified, empty survived and no gaps",
               DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True).clean
               and not DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, gaps=["x"]).clean
               and not DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, survived=[1]).clean
               and not DispatchResult("a", "b", "pdf", "stealth", ok=True).clean
               and not DispatchResult("a", "b", "pdf", "stealth", verified=True).clean)
        _check("length boundary: 6 chars are searched as a substring, 5 only as a word",
               len(survived_values(_one("abcdef"), _one("xabcdefx"))) == 1
               and survived_values(_one("abcde"), _one("xabcdex")) == [])

        # 2. Real formats: routing, proof, input untouched.
        print("2. PDF and images")
        for i, (maker, prof) in enumerate(((fx.make_pdf, "stealth"), (fx.make_pdf, "paranoid"),
                                           (fx.make_pdf_incremental, "stealth"),
                                           (fx.make_jpeg, "stealth"), (fx.make_png, "paranoid"))):
            d = os.path.join(tmp, "case_%d" % i)
            os.makedirs(d)
            path, _exp = maker(d)
            before = _sha(path)
            out = os.path.join(d, "out", "result.bin")      # the directory doesn't exist: it's created
            res = clean_file(path, out, prof)
            tag = "%s/%s" % (os.path.basename(path).split(".")[1], prof)
            _check("%s: ok and verified" % tag, res.ok and res.verified, res.errors)
            _check("%s: survived empty, clean" % tag, res.survived == [] and res.clean, res.survived)
            _check("%s: the oracle saw leaks before cleaning" % tag, res.critical_before > 0, res.critical_before)
            _check("%s: remaining critical findings are only new values" % tag,
                   all(r[2] for r in res.residual), res.residual)
            _check("%s: input unchanged" % tag, _sha(path) == before)
            _check("%s: dst created, no temp directories left" % tag,
                   os.path.isfile(out) and os.listdir(os.path.dirname(out)) == ["result.bin"],
                   os.listdir(os.path.dirname(out)))
            if prof == "stealth" and maker is fx.make_pdf:
                _check("STEALTH PDF: critical-after = exactly /ID[0] and /ID[1]",
                       sorted(r[1].split(" ")[0] for r in res.residual) == ["/ID[0]", "/ID[1]"], res.residual)
            if maker is fx.make_pdf_incremental:
                _check("incremental PDF: earlier revisions dropped (a single %%EOF)",
                       open(out, "rb").read().count(b"%%EOF") == 1)

        # 2b. keep: a value kept at the user's request is shown, not hidden.
        print("2b. keep")
        d = os.path.join(tmp, "keep")
        os.makedirs(d)
        path, exp = fx.make_pdf(d)
        res = clean_file(path, os.path.join(d, "k.pdf"), keep={"/Author"})
        _check("keep: file handed over, but clean=False and Author in survived",
               res.ok and res.verified and not res.clean and any(sv.label == "Author" for sv in res.survived),
               [(sv.label, sv.found_at) for sv in res.survived])

        # 3. Formats without cleaning: honest refusal with a phase number, dst not created and not touched.
        print("3. refusals by format")
        d = os.path.join(tmp, "refuse")
        os.makedirs(d)
        # docx/xlsx/pptx were removed from the refusals: since phase 2 they are cleaned (see 3b).
        cases = [(fx.make_doc, "doc", 4), (fx.make_xls, "xls", 4), (fx.make_ppt, "ppt", 4)]
        for maker, name, phase in cases:
            sub = os.path.join(d, name)
            os.makedirs(sub)
            path, _exp = maker(sub)
            before = _sha(path)
            dst = os.path.join(sub, "keep_me.out")
            with open(dst, "wb") as fh:
                fh.write(b"old")
            res = clean_file(path, dst)
            msg = " ".join(res.errors)
            _check("%s: ok=False, phase %d named" % (name, phase),
                   not res.ok and res.planned_phase == phase and ("phase %d" % phase) in msg, msg)
            _check("%s: says the file was NOT cleaned" % name, "NOT cleaned" in msg, msg)
            _check("%s: input and existing dst untouched" % name,
                   _sha(path) == before and open(dst, "rb").read() == b"old")
            _check("%s: neither proof nor actions" % name,
                   res.critical_after == -1 and not res.actions and not res.clean)
            _check("%s: the path didn't leak into the message" % name, sub not in msg and os.path.basename(path) not in msg)

        path, _exp = fx.make_rtf(d)
        res = clean_file(path, os.path.join(d, "clean.rtf"))
        _check("rtf: file handed over and no original values survived",
               res.ok and res.verified and res.clean, (res.ok, res.clean, res.errors[:2]))
        path, _exp = fx.make_odt(d)
        res = clean_file(path, os.path.join(d, "clean.odt"))
        _check("odt: file handed over and no original values survived",
               res.ok and res.verified and res.clean, (res.ok, res.clean, res.errors[:2]))

        # 4. dst == src under any spelling of the path.
        print("4. dst doesn't overwrite src")
        path, _exp = fx.make_pdf(os.path.join(tmp, "case_0"))
        before = _sha(path)
        d0 = os.path.dirname(path)
        variants = [path, os.path.join(d0, ".", os.path.basename(path)),
                    os.path.join(d0, "out", "..", os.path.basename(path))]
        if os.name == "nt":                            # only a case-insensitive FS makes it the same file
            variants.append(path.upper())
        for variant in variants:
            res = clean_file(path, variant)
            _check("refused: %s" % ("path variant"), not res.ok and "matches" in " ".join(res.errors), res.errors)
        link = os.path.join(d0, "hard.pdf")
        try:
            os.link(path, link)
            res = clean_file(path, link)
            _check("hard link to the input rejected", not res.ok and "matches" in " ".join(res.errors), res.errors)
        except OSError:
            print("  [--] hard links unavailable, skipped")
        _check("input intact after all attempts", _sha(path) == before)

        # 5. Pathologies and safeguards.
        print("5. pathologies")
        for name, p in fx.make_broken(os.path.join(tmp)).items():
            res = clean_file(p, os.path.join(tmp, "br_%s.out" % name))
            _check("%s: no crash, result correct" % name,
                   isinstance(res, DispatchResult) and not (res.ok and not res.verified)
                   and (not res.ok or os.path.isfile(res.dst)), res.errors)
        for bad, want in ((os.path.join(tmp, "нет_такого.pdf"), "not found"), (tmp, "directory")):
            res = clean_file(bad, os.path.join(tmp, "x.out"))
            _check("%s: refused with a reason" % want, not res.ok and want in " ".join(res.errors), res.errors)
        res = clean_file("bad" + chr(0) + "path", os.path.join(tmp, "x.out"))
        _check("NUL in the path doesn't crash", not res.ok)
        # A tiny zip declaring a huge deflated "mimetype": sniff() must not unpack it.
        import tracemalloc
        import zipfile
        bomb = os.path.join(tmp, "mimetype_bomb.zip")
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("mimetype", b"A" * (30 << 20))
        tracemalloc.start()
        try:
            kind = sniff.sniff(bomb)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        _check("sniff: a huge deflated mimetype entry is read bounded", kind == "zip" and peak < (4 << 20),
               (kind, peak))
        path, _exp = fx.make_pdf(os.path.join(tmp, "case_0"))
        res = clean_file(path, os.path.join(tmp, "p.out"), "nonsense")
        _check("unknown profile rejected", not res.ok and "profile" in " ".join(res.errors))
        res = clean_file(path, os.path.join(tmp, "p.out"), Profile.REGENERATE)
        _check("REGENERATE: phase 4, no file", not res.ok and res.planned_phase == 4
               and not os.path.exists(os.path.join(tmp, "p.out")))
        old, MAX_FILE = MAX_FILE, 10
        try:
            res = clean_file(path, os.path.join(tmp, "big.out"))
        finally:
            MAX_FILE = old
        _check("size limit", not res.ok and "larger than" in " ".join(res.errors), res.errors)

        # 6. verify=False: file handed over, but cleanliness can't be claimed.
        print("6. verify=False")
        res = clean_file(path, os.path.join(tmp, "nv.pdf"), verify=False)
        _check("ok, but clean=False and critical_after=-1",
               res.ok and not res.clean and res.critical_after == -1 and not res.verified)

        # 7. The dispatcher doesn't take the cleaner's word: swap in saboteurs.
        print("7. dishonest cleaner")
        saved = dict(_CLEANERS)
        try:
            def liar(s, d, prof, keep, force):         # copies the file as is and swears it's clean
                shutil.copyfile(s, d)
                r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
                r.ok = True
                r.act(Act.REMOVED, "/Info", "/Author", "-", "")
                return r
            _CLEANERS["pdf"] = liar
            res = clean_file(path, os.path.join(tmp, "liar.pdf"))
            _check("leak not removed: survived non-empty, clean=False",
                   res.ok and res.verified and res.survived and not res.clean, res.survived)

            def boom(s, d, prof, keep, force):
                with open(d, "wb") as fh:
                    fh.write(b"%PDF-1.4 half")
                raise RuntimeError("boom")
            _CLEANERS["pdf"] = boom
            dst = os.path.join(tmp, "boom_dir", "boom.pdf")
            res = clean_file(path, dst)
            _check("cleaner exception: ok=False, no dst, no junk left",
                   not res.ok and not os.path.exists(dst) and os.listdir(os.path.dirname(dst)) == [],
                   os.listdir(os.path.dirname(dst)))

            copy = os.path.join(tmp, "mutating.pdf")
            shutil.copyfile(path, copy)

            def mutator(s, d, prof, keep, force):      # corrupts the INPUT during cleaning
                r = saved["pdf"](s, d, prof, keep, force)
                with open(s, "ab") as fh:
                    fh.write(b"\n%junk")
                return r
            _CLEANERS["pdf"] = mutator
            dst = os.path.join(tmp, "mut.out")
            res = clean_file(copy, dst)
            _check("input changed during the run: file not handed over",
                   not res.ok and not os.path.exists(dst) and "changed" in " ".join(res.errors), res.errors)

            png = fx.make_png(os.path.join(tmp, "o"))[0] if os.makedirs(os.path.join(tmp, "o")) is None else None

            def other_format(s, d, prof, keep, force):  # the "cleaning" produced something that isn't a PDF
                shutil.copyfile(png, d)
                r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
                r.ok = True
                return r
            _CLEANERS["pdf"] = other_format
            res = clean_file(path, os.path.join(tmp, "of.out"))
            _check("an output of a different format is not handed over", not res.ok and not os.path.exists(os.path.join(tmp, "of.out")),
                   res.errors)
        finally:
            _CLEANERS.clear()
            _CLEANERS.update(saved)

        # 8. An existing dst is replaced atomically on success.
        print("8. replacing an existing dst")
        dst = os.path.join(tmp, "exists.pdf")
        with open(dst, "wb") as fh:
            fh.write(b"old")
        res = clean_file(path, dst)
        _check("on success dst is replaced", res.ok and open(dst, "rb").read()[:5] == b"%PDF-")
        if os.name == "nt":                            # only Windows refuses to replace an open file
            with open(dst, "rb") as held:              # dst is locked: replacement impossible
                res = clean_file(path, dst)
                held_ok = held.read(5) == b"%PDF-"
            _check("locked dst: file not handed over, no path leak and no junk",
                   held_ok and not res.ok and "locked" in " ".join(res.errors)
                   and tmp not in " ".join(res.errors)
                   and not [n for n in os.listdir(tmp) if n.startswith(".clean_")], res.errors)
        else:
            print("  [--] locked files are a Windows notion, skipped")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nTOTAL: %d checks, %d failed" % (_TOTAL[0], len(_FAILS)))
    for f in _FAILS:
        print("  FAIL: " + f)
    return 1 if _FAILS else 0


def main(argv=None) -> int:
    # The Windows console (cp866/cp1251) mangles non-ASCII text and crashes with UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(
        prog="python -m core.clean",
        description="Document metadata cleaning. The input is never changed.")
    parser.add_argument("file", nargs="?", help="path to the file")
    parser.add_argument("--out", help="where to write the result (default: next to the input, "
                                      "with a .clean suffix)")
    parser.add_argument("--profile", choices=["stealth", "paranoid", "regenerate"], default="stealth",
                        help="stealth: doesn't give processing away (default); paranoid: remove "
                             "everything; regenerate: re-create via LibreOffice (doc/xls/ppt -> "
                             "docx/xlsx/pptx)")
    parser.add_argument("--force-signed", action="store_true",
                        help="clean a signed PDF, destroying the signature")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--overwrite", action="store_true", help="overwrite an existing output "
                                                                 "file")
    parser.add_argument("--selftest", action="store_true", help="dispatcher self-test")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.file:
        parser.error("a file is required (or --selftest)")

    out = args.out or _default_out(args.file, args.profile)
    if os.path.exists(out) and not args.overwrite and not _same_file_safe(args.file, out):
        print("Output file already exists: %s (--overwrite to replace it)" % out)
        return 1
    res = clean_file(args.file, out, args.profile, force_signed=args.force_signed)
    print(_to_json(res) if args.json else _render(res))
    return _exit_code(res)


def _same_file_safe(a, b) -> bool:
    try:
        return _same_file(a, b)
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
