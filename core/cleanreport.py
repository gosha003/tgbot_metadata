# -*- coding: utf-8 -*-
"""Render a CleanResult as a cleaning report: Telegram HTML and plain text for the CLI.

A cleaning report is more dangerous than an inspection report: the user will
forward the file based on what it says. So the main rule is that the report
must never promise more than re-inspection of the output has proven:

  * result is None / ok == False  -> "FILE NOT CLEANED" and the reason;
  * format without a cleaner       -> "NOT CLEANED, format is inspect-only
                                       (phase N)"; ok=True with zero
                                       actions is not trusted for such a format;
  * critical_after == -1           -> "result not verified", no claim of cleanliness;
  * critical_after > 0             -> "NOT CLEAN" + what exactly remains and why;
  * critical_after == 0            -> "clean", and only then, with caveats
                                       if there were failures, errors or unremovables.

The approach mirrors core/report.py (escaping, UTF-16 splitting, per-section
degradation): file values are untrusted and go through clip() and html.escape();
one broken entry becomes an error line in its place, not an empty report.

Privacy: neither src nor dst ends up in the report; a file name is metadata.

Stdlib only.
"""

from __future__ import annotations

import html
import textwrap
from collections import namedtuple
from types import SimpleNamespace

from core import sniff
from core.cleanmodel import ACT_TITLE, Act
from core.model import _ABS_PATH, PATH_STUB, clip
from core.report import spoiler
from core.i18n import N_, both, tr

__all__ = ["render_telegram", "render_plain", "summary_line"]

TG_LIMIT = 4096
TG_BUDGET = TG_LIMIT - 64      # margin for the part-numbering prefix
WIDTH = 100

# Formats that have a cleaner (clean_pdf, clean_image, clean_ooxml).
# Must match the keys of core.clean._CLEANERS expanded to formats:
# the bot uses this set to decide whether to show cleaning buttons.
CLEANABLE = frozenset({"pdf", "jpeg", "png", "webp", "gif", "docx", "xlsx", "pptx",
                        "rtf", "odt", "ods", "odp"})

# No cleaner yet, phase per docs/ROADMAP.md. Fallback only: the primary number
# comes from the dispatcher via planned_phase, see _no_cleaner(). Keeping these
# numbers in sync with core.clean._PLANNED is mandatory, otherwise the user sees
# two different phases for one format in a single message.
# OOXML is gone from here: since phase 2 docx/xlsx/pptx are cleaned, see CLEANABLE.
# Phase 4 keeps only what can't be cleaned in place at all:
# legacy OLE2 with revision history embedded in the container.
_PHASE = {
    "doc": 4, "xls": 4, "ppt": 4, "ole": 4,
}
# clean_image refuses TIFF/HEIC on purpose (risk of damaging the file); it's not "not yet".
_REFUSED = frozenset({"tiff", "heic"})

# How many inspector findings one REGENERATED action can account for: the PDF
# trailer /ID[0] + /ID[1] pair is two IDENTITY findings. Only used to judge
# "the remainder is explained by the action list"; overestimating would hide an
# unexplained remainder, so the smallest known value is used.
_PER_REGEN = 2

_DONE = (Act.REMOVED, Act.BLANKED, Act.NORMALIZED, Act.REGENERATED)
_ICON = {
    Act.REMOVED: "\U0001F5D1", Act.BLANKED: "⬜", Act.NORMALIZED: "\U0001F527",
    Act.REGENERATED: "\U0001F501", Act.KEPT: "\U0001F4CC", Act.UNREMOVABLE: "\U0001F512",
    Act.FAILED: "❌",
}
# Length limits for values and notes per action kind. Chosen so that even a
# string of nothing but "&" (x5 when escaped) fits in one Telegram message.
_VAL_CAP = {Act.REMOVED: 200, Act.BLANKED: 200, Act.NORMALIZED: 120,
            Act.REGENERATED: 120, Act.KEPT: 160, Act.UNREMOVABLE: 160, Act.FAILED: 160}
_NOTE_CAP = {Act.REMOVED: 160, Act.BLANKED: 160, Act.NORMALIZED: 240,
             Act.REGENERATED: 240, Act.KEPT: 300, Act.UNREMOVABLE: 400, Act.FAILED: 400}
# For these kinds the note matters more than the value: put it on its own line.
_NOTE_OWN_LINE = (Act.KEPT, Act.UNREMOVABLE, Act.FAILED)

_E = namedtuple("_E", "act loc label before after note")

# Verdicts with no cleaned file: action lists aren't shown, otherwise
# "Removed: Author" would read as something that was done.
_NOT_OK = ("none", "unsupported", "failed")

# The note the cleaners put on a field kept by the user's keep list. The verdict
# counts such entries by matching it in either language: the note may have been
# written under a different language than the one active when the report is drawn.
_KEEP_NOTE = N_("kept per the user's whitelist (keep)")


# --- safe reading of an untrusted result -------------------------------------


def _c(value, cap=400) -> str:
    """clip() that doesn't raise even on an object with a failing __str__."""
    try:
        return clip(value, cap)
    except Exception:
        return ""


def _g(obj, name, default=None):
    """getattr that doesn't fail on a failing property; None -> default."""
    try:
        v = getattr(obj, name)
    except Exception:
        return default
    return default if v is None else v


def _try(obj, name):
    """(value, whether it was read). Needed where "field couldn't be read" and
    "field is empty" are different facts: the first must not pass as the second."""
    try:
        return getattr(obj, name), True
    except Exception:
        return None, False


def _val(v):
    """Enum -> its value. str(Profile.STEALTH) in Python 3.11 gives
    'Profile.STEALTH', not 'stealth'."""
    try:
        return getattr(v, "value", v)
    except Exception:
        return v


def _num(v) -> int:
    """Proof counter. Non-numbers and negatives = -1 = "not verified"."""
    try:
        n = int(v)
    except Exception:
        return -1
    return n if n >= 0 else -1


def _scrub(e) -> str:
    """Error text without absolute paths: CleanResult.err() strips them, but
    errors can also be appended bypassing it."""
    try:
        return _c(_ABS_PATH.sub(tr(PATH_STUB), str(e)), 600)
    except Exception:
        return tr("(error text could not be read)")


def _snap(result):
    """Snapshot of the result: every field is read once, defensively.
    From here on rendering works only with the snapshot and can't crash on
    a broken object. Anything unreadable becomes an entry in errs, not silence."""
    s = SimpleNamespace(has=result is not None, errs=[])
    s.fmt = _c(_val(_g(result, "fmt", "")), 40).lower()
    if not s.has:
        s.fmt_name = tr("format unknown")
    else:
        s.fmt_name = _c(sniff.pretty(s.fmt), 80) if s.fmt else tr("format not specified")
    s.profile = _c(_val(_g(result, "profile", "")), 40) or tr("not specified")
    try:
        s.ok = bool(_g(result, "ok", False))
    except Exception:
        s.ok = False
    s.cb = _num(_g(result, "critical_before", -1))
    s.ca = _num(_g(result, "critical_after", -1))
    s.sb = _num(_g(result, "sensitive_before", -1))
    s.sa = _num(_g(result, "sensitive_after", -1))

    # Proof from the dispatcher (DispatchResult from core/clean.py), if present.
    # It TAKES PRECEDENCE over the critical_after heuristic: the dispatcher KNOWS
    # the answer from survived_values (which original values appear in the
    # output), while the report can only guess from action counters. While the
    # report guessed on its own, it disagreed with the dispatcher both ways:
    # shouting "NOT CLEAN" at a proven-clean file and calling an unproven one clean.
    s.has_evidence = False
    s.verified = False
    s.survived_n = 0
    s.res_old = []
    s.gaps = []
    try:
        if _g(result, "verified", None) is not None:
            s.has_evidence = True
            s.verified = bool(_g(result, "verified", False))
            sv = _g(result, "survived", None)
            s.survived_n = len(list(sv)) if sv is not None else 0
            # residual: (location, field, is the value new). Not new = it was in the source too.
            s.res_old = ["%s %s" % (r[0], r[1]) for r in (_g(result, "residual", None) or []) if not r[2]]
            # What the inspector could not parse, before or after cleaning.
            s.gaps = [_scrub(g) for g in (_g(result, "gaps", None) or [])]
    except Exception:
        s.errs.append(tr("dispatcher proof could not be read: verdict by heuristic"))
    s.planned_phase = _num(_g(result, "planned_phase", 0))
    # Signals that appeared AFTER cleaning: the file is now recognizable as processed.
    s.new_signals = []
    s.detect = ""
    s.detect_why = []
    try:
        for item in (_g(result, "new_signals", None) or []):
            kind = _c(_val(item[0]), 40)
            s.new_signals.append(kind)
        s.detect = _c(_g(result, "detect", ""), 20)
        s.detect_why = [_c(x, 300) for x in (_g(result, "detect_why", None) or [])]
    except Exception:
        s.errs.append(tr("list of new signals could not be read"))

    entries, bad = [], 0
    raw, got = _try(result, "actions")
    try:
        items = list(raw)
    except Exception:
        items = []
        got = False
    if s.has and not got:
        s.errs.append(tr("action list could not be read: the report is incomplete"))
    for a in items:
        try:
            entries.append(_E(Act(_val(a.act)), _c(a.location, 120), _c(a.label, 120),
                              _c(a.before, 600), _c(a.after, 600), _c(a.note, 600)))
        except Exception:
            bad += 1
    if bad:
        s.errs.append(tr("action entries not read: %d, the list above is incomplete") % bad)
    s.by = {}
    for e in entries:
        s.by.setdefault(e.act, []).append(e)
    s.n_actions = len(entries) + bad
    s.n_changed = sum(len(s.by.get(a, [])) for a in _DONE)

    raw, got = _try(result, "errors")
    try:
        raw = [] if raw is None else [raw] if isinstance(raw, (str, bytes)) else list(raw)
    except Exception:
        raw, got = [], False
    if s.has and not got:
        s.errs.append(tr("error list could not be read"))
    s.errs = [_scrub(e) for e in raw] + s.errs
    return s


def _n(s, *acts) -> int:
    return sum(len(s.by.get(a, [])) for a in acts)


# --- verdict -----------------------------------------------------------------


def _no_cleaner(s) -> str:
    # The phase number comes from the dispatcher: it owns the _PLANNED table, and
    # if the report named its own number, the user would see two different phases
    # for one format in a single message. Our table is only a fallback.
    if s.planned_phase > 0:
        return (tr("format %s is inspect-only for now; cleaning arrives in phase %d")
                % (s.fmt_name, s.planned_phase))
    if s.fmt in _PHASE:
        return (tr("format %s is inspect-only for now; cleaning arrives in phase %d")
                % (s.fmt_name, _PHASE[s.fmt]))
    if s.fmt in _REFUSED:
        return (tr("format %s is deliberately not cleaned: rebuilding it without re-encoding "
                   "risks damaging the file") % s.fmt_name)
    if not s.fmt:
        return tr("the result doesn't specify the file format; cleaning not confirmed")
    return tr("no cleaning is provided for file type \"%s\"") % s.fmt_name


def _pair(before, after, arrow) -> str:
    b = str(before) if before >= 0 else "?"
    a = str(after) if after >= 0 else tr("not verified")
    return b + arrow + a


def _V(kind, mark, head, lines, short):
    return SimpleNamespace(kind=kind, mark=mark, head=head, lines=lines, short=short)


def _verdict(s):
    """One honest verdict. Checks run in order of decreasing severity."""
    f, u = _n(s, Act.FAILED), _n(s, Act.UNREMOVABLE)
    hidden = ([tr("Action entries (%d) are not shown: there is no cleaned file, and presenting "
                  "them as done would be untrue.") % s.n_actions] if s.n_actions else [])
    cav = []
    if f:
        cav.append(tr("failed to process: %d") % f)
    if u:
        cav.append(tr("cannot be removed: %d") % u)
    if s.errs:
        cav.append(tr("errors during cleaning: %d") % len(s.errs))
    cav_line = [tr("Caveats: %s. Details in the sections below.") % "; ".join(cav)] if cav else []
    reason = s.errs[0] if s.errs else ""

    if not s.has:
        return _V("none", "bad", tr("FILE NOT CLEANED: cleaning returned no result."),
                  [tr("This is an internal failure, not \"nothing to clean\". There is no "
                      "cleaned copy: don't send anything as cleaned.")],
                  tr("cleaning returned no result: file NOT cleaned"))

    if s.fmt not in CLEANABLE and not (s.ok and s.n_actions):
        why = _no_cleaner(s)
        lines = [tr("Only inspection is available for it right now: it shows what's in the file "
                    "but removes nothing. There is no cleaned copy; don't send this file as "
                    "cleaned.")]
        if s.ok:
            lines.append(tr("The cleaner reported success without a single action: this can't be "
                            "trusted."))
        if reason:
            lines.append(tr("Cleaner message: %s") % reason)
        lines += hidden
        return _V("unsupported", "bad", tr("FILE NOT CLEANED: %s.") % why, lines, tr("NOT cleaned: %s") % why)

    if not s.ok:
        return _V("failed", "bad", tr("FILE NOT CLEANED: cleaning did not finish."),
                  [tr("Reason: %s") % (reason or tr("not given")),
                   tr("There is no cleaned copy: don't send anything as cleaned.")] + hidden,
                  tr("NOT cleaned, cleaning did not finish"))

    # --- the dispatcher's proof takes precedence over the heuristic ----------
    # The dispatcher compares VALUES: which original sensitive values appear
    # in the output's findings. An empty list is the proof of cleanliness,
    # unlike critical_after, which also counts NEW findings (a regenerated
    # trailer /ID says nothing about the source).
    if s.has_evidence:
        if not s.verified:
            return _V("unverified", "warn",
                      tr("RESULT NOT VERIFIED: the file can't be claimed clean."),
                      [tr("The output was not re-inspected, so there was nothing to compare the "
                          "original values against. Run inspection before treating the file as "
                          "cleaned.")]
                      + cav_line, tr("result not verified"))
        if s.survived_n:
            return _V("residual", "bad",
                      tr("NOT CLEAN: original values survived cleaning: %d.") % s.survived_n,
                      [tr("These are values from the original file found in the output: cleaning "
                          "didn't remove them. Don't send this file.")] + cav_line,
                      tr("NOT clean: %d original values survived") % s.survived_n)
        # No survivors, but part of the file was never read (gaps): the absence of
        # evidence is not proof, so no "CLEAN:" headline, and no line below may claim
        # that nothing leaks. The rest of the assessment still applies.
        gap_lines = []
        if s.gaps:
            shown = s.gaps[:5]
            gap_lines = [tr("The inspector could not fully read the file (%d), so the check is "
                            "incomplete: something could have remained in the unread part. Do "
                            "not treat the file as cleaned until you have checked it.") % len(s.gaps)]
            gap_lines += ["• " + g for g in shown]
            if len(s.gaps) > len(shown):
                gap_lines.append(tr("… and %d more") % (len(s.gaps) - len(shown)))
        was = _pair(s.cb, s.ca, " -> ")
        lines = [] if s.gaps else [
            tr("Proof: no original sensitive value was found in the output. The comparison "
               "uses values, not counters.")]
        if s.detect == "obvious":
            lines.append(
                tr("ASSESSMENT: the file looks built by a program rather than saved by an office "
                   "application. For many purposes this is worse than a cleaning trace."))
        elif s.detect == "noticeable":
            lines.append(
                tr("ASSESSMENT: the file looks scrubbed: the fact of processing is visible.")
                if s.gaps else
                tr("ASSESSMENT: the file looks scrubbed. No original values leak, but the fact "
                   "of processing is visible."))
        elif s.detect == "none":
            lines.append(
                tr("ASSESSMENT: the inspector sees no signs of processing. This is not a "
                   "guarantee: it only sees metadata, not writing style or watermarks."))
        if s.new_signals and s.gaps:
            lines.append(tr("Cleaning introduced signs of processing that weren't in the "
                            "original (%s).") % ", ".join(sorted(set(s.new_signals))))
        elif s.new_signals:
            lines.append(
                tr("BUT THE FILE IS NOW RECOGNIZABLE AS SCRUBBED. Nothing leaks, but cleaning "
                   "introduced signs of processing (%s) that weren't in the original. If it "
                   "matters that the cleaning itself stays invisible, this result is not enough.") % ", ".join(sorted(set(s.new_signals))))
            lines.append(
                tr("For docx, xlsx and pptx this is a limit of the format, not a shortcoming: "
                   "empty dc:creator and cp:lastModifiedBy together are a trace in themselves, "
                   "keeping the real name means not cleaning, and substituting someone else's "
                   "means forging provenance. Only regenerating the file with a stock "
                   "application gives invisibility (Regenerate mode, phase 4)."))
        if s.ca > 0 and s.res_old:
            lines.append(tr("Critical findings in the output: %d. Of these, %d have the same "
                            "value as in the original (%s): they weren't part of the leak "
                            "comparison (short or service values). Check them yourself in the "
                            "list below.")
                         % (s.ca, len(s.res_old), "; ".join(s.res_old[:5])))
        elif s.ca > 0:
            lines.append(tr("Critical findings in the output: %d. These are NEW values that "
                            "weren't in the original, e.g. a regenerated trailer /ID. They are "
                            "unrelated to the source file.") % s.ca)
        lines += cav_line
        if s.gaps:
            return _V("unverified", "warn",
                      tr("CLEANLINESS NOT PROVEN: no original values found, but the check could "
                         "not be completed."),
                      gap_lines + lines, tr("cleanliness not proven"))
        if f or s.errs:
            return _V("clean", "warn",
                      tr("NO ORIGINAL VALUES LEFT, BUT WITH CAVEATS (critical %s).") % was, lines, "")
        return _V("clean", "ok",
                  tr("CLEAN: no original value survived cleaning (critical %s).") % was, lines, "")

    if s.ca < 0:
        return _V("unverified", "warn",
                  tr("RESULT NOT VERIFIED: the file can't be claimed clean."),
                  [tr("The file was written and opens, but the output was not re-inspected: the "
                      "number of critical findings after cleaning is unknown. Re-inspect the "
                      "output before treating the file as cleaned.")]
                  + cav_line, tr("result not verified"))

    if s.ca == 0:
        was = tr("was %s, now 0") % (s.cb if s.cb >= 0 else "?")
        # Unremovables on their own (every JPEG has quantization tables) don't
        # lower the status: the oracle sees nothing critical, and the caveat sits
        # right under the verdict. Failures and processing errors do lower it:
        # something could have been left behind.
        if f or s.errs:
            return _V("clean", "warn", tr("NO CRITICAL FINDINGS LEFT, BUT WITH CAVEATS (%s).") % was,
                      cav_line, "")
        return _V("clean", "ok", tr("CLEAN: re-inspection of the output found no critical "
                                    "findings (%s).") % was, cav_line, "")

    # critical_after > 0: such a file is not called clean.
    regen = _n(s, Act.REGENERATED)
    wl = sum(1 for e in s.by.get(Act.KEPT, []) if e.note in both(_KEEP_NOTE))
    unexplained = max(0, s.ca - (regen * _PER_REGEN + f + u + wl))
    lines = []
    if regen:
        lines.append(tr("Values regenerated by cleaning (%d), e.g. trailer /ID: they are new and "
                        "unrelated to the originals. Not a leak, but the inspector counts them "
                        "as findings.") % regen)
    if wl:
        lines.append(tr("Kept per your keep list: %d. Your choice; these fields stayed as they "
                        "were.") % wl)
    if unexplained:
        lines.append(tr("NOT EXPLAINED by the action list: at least %d. Until it's clear what "
                        "this is, the file can't be considered clean: re-inspect the output.") % unexplained)
    lines += cav_line
    was = tr("was %s") % (s.cb if s.cb >= 0 else "?")
    if unexplained:
        return _V("residual", "bad", tr("NOT CLEAN: critical findings after cleaning: %d (%s), "
                                        "not all explained.") % (s.ca, was), lines, tr("remainder not explained"))
    return _V("residual", "warn", tr("CRITICAL FINDINGS LEFT: %d (%s). The action list explains "
                                     "them via the entries below; the file is not declared clean.") % (s.ca, was), lines, "")


# --- formatting --------------------------------------------------------------


class _Style:
    """One set of helpers for both outputs. Methods take the RAW value and
    clean and escape it themselves: ready HTML must not be passed in (double
    escaping), but there's also nowhere to forget html.escape."""

    def __init__(self, tg):
        self.tg = tg
        self.arrow = "→" if tg else "->"
        self.mark = ({"ok": "✅", "warn": "⚠", "bad": "⛔"} if tg
                     else {"ok": "[ OK ]", "warn": "[ !! ]", "bad": "[ XX ]"})
        self.red, self.yel = ("\U0001F534", "\U0001F7E1") if tg else ("  *", "  *")

    def t(self, v, cap=400):
        s = _c(v, cap)
        return html.escape(s, quote=False) if self.tg else s

    def _w(self, tag, v, cap):
        s = self.t(v, cap)
        return "<%s>%s</%s>" % (tag, s, tag) if self.tg and s else s

    def b(self, v, cap=400):
        return self._w("b", v, cap)

    def i(self, v, cap=400):
        return self._w("i", v, cap)

    def code(self, v, cap=400):
        return self._w("code", v, cap)

    def val(self, v, cap=200):
        """A value from the file. In Telegram it goes under a spoiler (policy and
        caveat in core/report.py, flag MASK_VALUES): the cleaning report lists
        exactly what was found, i.e. the same personal data as the inspection
        report. Empty values aren't hidden: nothing to hide."""
        if not _c(v, cap):
            return self.i(tr("(empty)"))
        return spoiler(self.code(v, cap)) if self.tg else self.code(v, cap)

    def p(self, v, cap=700):
        """A paragraph of plain text."""
        return self.t(v, cap) if self.tg else "  " + self.t(v, cap)

    def lead(self, v, cap=800):
        """A note under a heading."""
        return self.i(v, cap) if self.tg else "  " + self.t(v, cap)

    def head(self, icon, title, n=None):
        tail = " — %d" % n if n is not None else ""
        if self.tg:
            return ["%s <b>%s</b>%s" % (icon, self.t(title, 120), tail)]
        return [self.t(title, 120) + tail, "-" * WIDTH]

    def loc(self, v):
        return self.i(v, 120) if self.tg else "  " + self.t(v, 120)

    def bullet(self, styled):
        return ("• " if self.tg else "    - ") + styled

    def sub(self, styled):
        return ("   ↳ " if self.tg else "        ") + styled


_TG, _PL = _Style(True), _Style(False)


# --- sections ----------------------------------------------------------------


def _sec_header(s, st, v):
    if st.tg:
        out = [tr("\U0001F9F9 <b>Metadata cleaning</b> · %s · profile %s")
               % (st.b(s.fmt_name, 80), st.b(s.profile, 40))]
    else:
        out = ["=" * WIDTH, tr("METADATA CLEANING REPORT"), "=" * WIDTH,
               tr("Format:  %s") % st.t(s.fmt_name, 80), tr("Profile: %s") % st.t(s.profile, 40)]
    done = ", ".join("%s %d" % (tr(ACT_TITLE[a]), _n(s, a)) for a in _DONE if _n(s, a))
    if v.kind in _NOT_OK:
        out.append(tr("Changed: %s (no cleaned file)") % st.b("0"))
    else:
        line = tr("Changed: %s") % st.b(str(s.n_changed))
        out.append(line + (" (%s)" % st.t(done, 200) if done else ""))
    if v.kind in ("unverified", "clean", "residual"):
        out.append(st.b(tr("Proof")) + st.t(tr(": re-inspection of the output")))
        out.append(tr("%s Critical (identity, environment): %s")
                   % (st.red, st.b(_pair(s.cb, s.ca, " %s " % st.arrow))))
        out.append(tr("%s Needing cleaning (incl. provenance): %s")
                   % (st.yel, st.b(_pair(s.sb, s.sa, " %s " % st.arrow))))
    else:
        out.append(st.t(tr("No proof: the file was not cleaned, the output was not re-inspected.")))
    return [out]


def _sec_verdict(s, st, v):
    return [["%s %s" % (st.mark[v.mark], st.b(v.head, 500))] + [st.p(x) for x in v.lines]]


def _by_loc(items):
    g = {}
    for e in items:
        g.setdefault(e.loc or "—", []).append(e)
    return g.items()


def _entry(act, e, st):
    """One entry. A broken one becomes an error line in its place."""
    try:
        line = st.t(e.label, 120) or tr("(untitled)")
        if e.before or act not in (Act.FAILED, Act.UNREMOVABLE):
            line += ": " + st.val(e.before, _VAL_CAP[act])
        if act in (Act.NORMALIZED, Act.REGENERATED) or e.after:
            line += " %s %s" % (st.arrow, st.val(e.after, _VAL_CAP[act]))
        note = _c(e.note, _NOTE_CAP[act])
        if note and act in _NOTE_OWN_LINE:
            return st.bullet(line) + "\n" + st.sub(st.i(note, _NOTE_CAP[act]))
        if note:
            line += " " + st.i("— " + note, _NOTE_CAP[act] + 4)
        return st.bullet(line)
    except Exception:
        return st.bullet(st.t(tr("(entry could not be displayed)")))


def _group(act, s, st, lead="", loud=False):
    items = s.by.get(act)
    if not items:
        return []
    title = tr(ACT_TITLE[act]).upper() if loud else tr(ACT_TITLE[act]).capitalize()
    out = st.head(_ICON[act], title, len(items))
    if lead:
        out.append(st.lead(lead))
    for loc, grp in _by_loc(items):
        out.append(st.loc(loc))
        out += [_entry(act, e, st) for e in grp]
    return [out]


def _sec_done(s, st, v):
    out = []
    if v.kind in _NOT_OK:
        return out
    for act in _DONE:
        out += _group(act, s, st)
    return out


def _sec_kept(s, st, v):
    return [] if v.kind in _NOT_OK else _group(Act.KEPT, s, st, loud=True, lead=(
        tr("These fields were kept deliberately, not forgotten. Structural data (document "
           "language, accessibility tags, ICC profile, outline) is needed for reading, printing "
           "and screen readers; some you may have asked to keep yourself (keep).")
        + (tr(" Also, the stealth profile doesn't blank everything: empty metadata gives "
              "cleaning away by itself.") if s.profile == "stealth" else "")
        + tr(" Cleaning leaves these fields alone.")))


def _sec_unrem(s, st, v):
    return [] if v.kind in _NOT_OK else _group(Act.UNREMOVABLE, s, st, loud=True, lead=(
        tr("This data stays in the file and cleaning won't remove it: either the format works "
           "that way, or removing it would break the file or its verification. Before sending "
           "the file, decide whether that's acceptable.")))


def _sec_failed(s, st, v):
    return [] if v.kind in _NOT_OK else _group(Act.FAILED, s, st, loud=True, lead=(
        tr("Cleaning these fields failed: assume they are still in the file.")))


def _sec_errors(s, st, v):
    if not s.errs:
        return []
    out = st.head("⚠" if st.tg else "", tr("Errors during cleaning"), len(s.errs))
    out.append(st.lead(tr("These parts of the file couldn't be parsed or processed: metadata may "
                          "remain in them even if re-inspection found nothing.")))
    out += [st.bullet(st.code(e, 600)) for e in s.errs]
    return [out]


def _sec_footer(s, st, v):
    msgs = (tr("The source file was not modified by cleaning: it only writes a new copy."),
            tr("The file name is metadata too, and Telegram keeps it: if the name contains a "
               "person's name or a project title, rename the file before sending."),
            tr("The report covers metadata only: it doesn't assess writing style or the content "
               "itself."))
    out = ["———" if st.tg else "=" * WIDTH]
    out += [st.lead(m, 400) for m in msgs]
    if not st.tg:
        out.append("=" * WIDTH)
    return [out]


_BUILDERS = (_sec_header, _sec_verdict, _sec_done, _sec_kept, _sec_unrem, _sec_failed,
             _sec_errors, _sec_footer)


def _sections(s, st):
    try:
        v = _verdict(s)
    except Exception:
        v = _V("failed", "bad", tr("FILE NOT CLEANED: the verdict could not be built."), [],
               tr("NOT cleaned, verdict could not be built"))
    out = []
    for build in _BUILDERS:
        try:
            out += build(s, st, v)
        except Exception as exc:
            # One section broke; the rest still get through.
            out.append([tr("%s report section failed to render (%s)")
                        % ("⚠" if st.tg else "[ !! ]", type(exc).__name__)])
    return out


# --- splitting into Telegram messages (as in core/report.py) ------------------


def _tg_len(s: str) -> int:
    """Length in UTF-16 code units: that's how Telegram's limit counts, not len()."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _pack(sections, limit: int = TG_BUDGET):
    """Sections -> messages no longer than limit. Split at section boundaries,
    and an oversized section at lines (items); an item always has balanced
    tags, so a tag is never cut in the middle."""
    buf, msgs = [], []

    def cur():
        return sum(_tg_len(x) + 1 for x in buf) - 1 if buf else 0

    def flush():
        if buf:
            msgs.append("\n".join(buf))
            del buf[:]

    for sec in sections:
        if not sec:
            continue
        n = sum(_tg_len(x) + 1 for x in sec) - 1
        if n + 1 <= limit:
            if buf and cur() + 1 + n + 1 > limit:
                flush()
            if buf:
                buf.append("")
            buf.extend(sec)
            continue
        if buf:
            if cur() + 1 > limit:
                flush()
            else:
                buf.append("")
        for line in sec:
            if _tg_len(line) > limit:
                # Silently losing an entry in a leak report is worse than marking it.
                line = (tr("• <i>(entry didn't fit in one Telegram message and was skipped; see "
                           "the text report)</i>"))
            if buf and cur() + 1 + _tg_len(line) > limit:
                flush()
            buf.append(line)
    flush()
    return msgs


# --- public API ----------------------------------------------------------------


def render_telegram(result) -> list:
    """CleanResult -> list of messages for parse_mode="HTML". Tags: b, i, code only.
    Never raises: on any failure returns a "consider the file NOT cleaned" message."""
    try:
        parts = _pack(_sections(_snap(result), _TG))
    except Exception as exc:
        return [tr("⚠ The cleaning report could not be built (%s). Consider the file NOT cleaned.")
                % type(exc).__name__]
    if not parts:
        return [tr("⚠ The report is empty. Consider the file NOT cleaned.")]
    if len(parts) > 1:
        total = len(parts)
        parts = ["<b>[%d/%d]</b>\n%s" % (i, total, p) for i, p in enumerate(parts, 1)]
    return parts


def _wrap(line: str) -> str:
    """Wrap to WIDTH with a hanging indent; content is never cut."""
    if len(line) <= WIDTH:
        return line
    body = line.lstrip(" ")
    ind = line[:len(line) - len(body)] + ("  " if body.startswith("- ") else "")
    return textwrap.fill(line, width=WIDTH, subsequent_indent=ind,
                         break_long_words=True, break_on_hyphens=False)


def render_plain(result) -> str:
    """The same without HTML, width WIDTH, for the CLI. Never raises."""
    try:
        out = []
        for sec in _sections(_snap(result), _PL):
            if out:
                out.append("")
            for el in sec:
                out += [_wrap(ln) for ln in el.split("\n")]
        return "\n".join(out)
    except Exception as exc:
        return tr("The cleaning report could not be built (%s). Consider the file NOT cleaned.") % type(exc).__name__


def summary_line(result) -> str:
    """One line for logs and short replies, no line breaks:
    "PDF, profile stealth: changed 7, critical 7->2, kept on purpose 10".
    It contains no file names and no finding contents."""
    try:
        s = _snap(result)
        v = _verdict(s)
        if v.kind in ("none", "unsupported"):
            text = v.short
        elif v.kind == "failed":
            text = tr("%s, profile %s: %s") % (s.fmt_name, s.profile, v.short)
        else:
            parts = [tr("changed %d") % s.n_changed,
                     "critical %s" % _pair(s.cb, s.ca, "->") if s.ca >= 0 else tr("not verified"),
                     tr("kept on purpose %d") % _n(s, Act.KEPT)]
            for label, n in ((tr("unremovable"), _n(s, Act.UNREMOVABLE)),
                             (tr("failed"), _n(s, Act.FAILED)), (tr("errors"), len(s.errs))):
                if n:
                    parts.append("%s %d" % (label, n))
            if v.short and (v.kind == "residual" or s.gaps):
                parts.append(v.short)
            text = tr("%s, profile %s: %s") % (s.fmt_name, s.profile, ", ".join(parts))
        return " ".join(_c(text, 400).split())
    except Exception:
        return tr("cleaning report could not be built: consider the file NOT cleaned")


# --- self-test -----------------------------------------------------------------


def _demo():
    import re
    from html.parser import HTMLParser

    from core.cleanmodel import CleanResult, Profile
    from core.i18n import RU, use_lang
    from core.model import PATH_STUB

    allowed = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}
    tag_re = re.compile(r"</?(?:%s)>" % "|".join(sorted(allowed)))
    fails = []

    class Bal(HTMLParser):
        """Minimal parser: allowed tags, correct closing order."""

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in allowed:
                self.bad.append("forbidden tag " + tag)
            self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack.pop() != tag:
                self.bad.append("closing order broken " + tag)

    def u16(text):
        """Length in UTF-16 code units, computed INDEPENDENTLY of the module's code."""
        return len(text.encode("utf-16-le", "surrogatepass")) // 2

    def claims_clean(text):
        """Whether the text claims "CLEAN:" (as opposed to "NOT CLEAN:")."""
        return re.search(r"(?<!NOT )CLEAN:", text) is not None

    def check(name, cond, detail=""):
        print("  [%s] %s%s" % ("ok" if cond else "FAIL", name, "" if cond else ": %r" % (detail,)))
        if not cond:
            fails.append(name)

    def check_tg(name, result):
        msgs = render_telegram(result)
        sizes = [u16(m) for m in msgs]
        bad = []
        for m in msgs:
            p = Bal()
            p.feed(m)
            p.close()
            bad += p.bad + (["not closed: " + ",".join(p.stack)] if p.stack else [])
            rest = tag_re.sub("", m)
            if "<" in rest or ">" in rest:
                bad.append("raw < or >")
            if re.search(r"&(?!(?:amp|lt|gt);)", rest):
                bad.append("raw &")
        check("TG %s: %d msgs, max %d <= %d, tags balanced"
              % (name, len(msgs), max(sizes), TG_LIMIT), max(sizes) <= TG_LIMIT and not bad,
              (sizes, bad[:3]))
        return msgs

    def check_plain(name, result):
        text = render_plain(result)
        widest = max(len(x) for x in text.split("\n"))
        check("plain %s: max width %d <= %d, no HTML" % (name, widest, WIDTH),
              widest <= WIDTH and "<code>" not in text and "&amp;" not in text, widest)
        return text

    def mk(fmt="pdf", ok=True, cb=7, ca=2, sb=137, sa=130, profile=Profile.STEALTH):
        return CleanResult(src="C:/Users/secret/Иванов_договор.pdf", dst="C:/Users/secret/out.pdf",
                           fmt=fmt, profile=profile, ok=ok, critical_before=cb,
                           critical_after=ca, sensitive_before=sb, sensitive_after=sa)

    # 1. Every Act kind, injected value, ok, critical remainder explained by /ID.
    full = mk()
    full.act("removed", "/Info", "/Author", "Иванов Иван <ivan@corp.ru>", note="license owner's full name")
    full.act("removed", "/Info", "/Subject", "<script>&")
    full.act("removed", "<i>/Info", "<b>/Creator", "")
    full.act("removed", "XMP", "xmpMM:DocumentID", "uuid:1234-abcd")
    full.act("blanked", "/Info", "/Title", "Договор №5")
    full.act("normalized", "header", "qpdf binary marker", "25C2B5", "25E2E3",
             "Replaced with a marker of the same length as the original.")
    full.act("regenerated", "trailer", "/ID", "a1b2c3d4", "9f8e7d6c",
             "Both values are new, the pair is shaped like the original.")
    full.act("kept", "/Root", "/Lang", "ru-RU", note="Document language: needed by screen "
                                                     "readers.")
    full.act("kept", "/Root", "/StructTreeRoot", "present", note="Accessibility tags.")
    full.act("kept", "/Root", "/Outlines", "present", note="Outline.")
    full.act("kept", "/Root", "/OutputIntents", "ICC sRGB", note="ICC profile: colors shift "
                                                                 "without it.")
    full.act("unremovable", "DQT", "Quantization tables", "2 pcs", "unchanged",
             "Can't be removed without re-encoding: they fingerprint the encoder themselves.")
    full.act("failed", "/Names", "JavaScript", note="section failure (ValueError)")
    full.err("Section \"/Names\": ValueError: bad dictionary <a href=x>")
    print("=== 1. every Act kind, ok=True, critical 7->2 ===")
    tg = check_tg("full", full)
    for i, m in enumerate(tg, 1):
        print("--- message %d, %d UTF-16 ---" % (i, u16(m)))
        print(m)
    pl = check_plain("full", full)
    print(pl)
    joined = "\n".join(tg)
    check("value escaped", "&lt;script&gt;&amp;" in joined and "<script>" not in joined)
    check("in plain the value is as is", "<script>&" in pl)
    check("no \"CLEAN\" claimed with critical_after=2", not claims_clean(joined) and not claims_clean(pl))
    check("remainder explained by /ID, not 'not explained'", "values regenerated" in joined.lower()
          and "NOT EXPLAINED" not in joined)
    check("KEPT and UNREMOVABLE sections stand out",
          "KEPT ON PURPOSE" in joined and "CANNOT BE REMOVED" in joined
          and "deliberately, not forgotten" in joined and "Quantization tables" in joined)
    check("order: removed < blanked < normalized < regenerated < kept < unremovable < failed",
          [joined.find(x) for x in ("Removed", "Blanked", "Normalized", "Regenerated",
                                    "KEPT", "CANNOT", "FAILED")]
          == sorted(joined.find(x) for x in ("Removed", "Blanked", "Normalized",
                                             "Regenerated", "KEPT", "CANNOT", "FAILED")))
    check("\"before\" shown for removed entries", "Иванов Иван" in joined and "Договор №5" in joined)
    check("footer: source not modified, file name = metadata",
          "The source file was not modified by cleaning" in tg[-1] and "Telegram keeps it" in tg[-1])
    check("file names didn't leak", all(x not in joined + pl + summary_line(full)
                                         for x in ("Иванов_договор", "secret", "out.pdf")))
    print("summary:", summary_line(full))

    # 2. Exactly the example from the spec.
    ex = mk()
    for i in range(6):
        ex.act("removed", "/Info", "field %d" % i, "v")
    ex.act("regenerated", "trailer", "/ID", "aa", "bb")
    for i in range(10):
        ex.act("kept", "/Root", "k%d" % i, "v")
    check("summary_line == the spec example",
          summary_line(ex) == "PDF, profile stealth: changed 7, critical 7->2, kept on purpose 10",
          summary_line(ex))

    # 3. ok=False.
    bad = mk(ok=False, cb=-1, ca=-1, sb=-1, sa=-1)
    bad.err("PDF is password-protected: cleaning refused C:\\Users\\secret\\x.pdf")
    print("\n=== 3. ok=False ===")
    m = check_tg("ok=False", bad)
    print("\n".join(m))
    check("ok=False: NOT CLEANED and the reason", "FILE NOT CLEANED" in m[0] and "password-protected" in m[0])
    check("ok=False: path stripped from the error", "secret" not in "\n".join(m) and html.escape(PATH_STUB) in "\n".join(m))
    check("ok=False: no \"clean\" and no proof numbers", not claims_clean(m[0]) and "No proof" in m[0])
    with use_lang("ru"):
        ru_err = mk()
        ru_err.err("boom C:\\Users\\secret\\x.pdf here")
    check("CleanResult.err: the path stub follows the language",
          "<путь скрыт>" in ru_err.errors[0] and "secret" not in ru_err.errors[0], ru_err.errors)
    print("summary:", summary_line(bad))
    check_plain("ok=False", bad)
    bad.act("removed", "/Info", "/Author", "Иванов")
    m = "\n".join(render_telegram(bad))
    check("ok=False + actions: not shown as done, but that is stated",
          "Removed" not in m and "Иванов" not in m and "are not shown" in m, m[:400])
    del bad.actions[:]

    # 3b. Value masking. The cleaning report lists exactly the values the
    # inspector found, i.e. the same personal data, so they must be hidden here
    # too. There's a single render site (_Style.val), so it's enough to check
    # that the spoiler holds the value, not an empty string or the whole paragraph.
    msk = mk()
    msk.act("removed", "/Info", "/Author", "Иванов Иван Иванович")
    msk.act("blanked", "docProps/core.xml", "dc:creator", "")
    m = "\n".join(render_telegram(msk))
    check("masking: the value went under a spoiler",
          "<tg-spoiler><code>Иванов Иван Иванович</code></tg-spoiler>" in m, m[:300])
    check("masking: an empty value isn't hidden (nothing to hide)",
          "<tg-spoiler><i>(empty)</i>" not in m and "<i>(empty)</i>" in m)
    check("masking: no spoiler in plain output, it goes to the local console",
          "tg-spoiler" not in render_plain(msk))
    import core.report as _rep
    _rep.MASK_VALUES = False
    try:
        check("masking: the shared switch also applies to the cleaning report",
              "tg-spoiler" not in "\n".join(render_telegram(msk))
              and "<code>Иванов Иван Иванович</code>" in "\n".join(render_telegram(msk)))
    finally:
        _rep.MASK_VALUES = True

    # 4. critical_after == -1.
    unv = mk(ca=-1, sa=-1)
    unv.act("removed", "/Info", "/Author", "Иванов")
    print("\n=== 4. critical_after = -1 (verification not run) ===")
    m = check_tg("not verified", unv)
    print("\n".join(m))
    check("not verified: no cleanliness claimed",
          "RESULT NOT VERIFIED" in m[0] and not claims_clean(m[0]) and "not verified" in m[0])
    print("summary:", summary_line(unv))
    check_plain("not verified", unv)

    # 5. Clean / unexplained remainder.
    ok0 = mk(ca=0, sa=130)
    ok0.act("removed", "/Info", "/Author", "Иванов")
    ok0.act("kept", "/Root", "/Lang", "ru-RU", note="language")
    m = check_tg("clean", ok0)
    check("critical_after=0 without caveats: CLEAN", claims_clean(m[0]) and "was 7, now 0" in m[0])
    ok0.act("unremovable", "DQT", "Quantization tables", "2 pcs", note="impossible")
    m = check_tg("clean with a caveat", ok0)
    check("critical_after=0 + unremovable: CLEAN, but the caveat is visible under the verdict",
          claims_clean(m[0]) and "cannot be removed: 1" in m[0], m[0][:600])
    ok0.act("failed", "/Names", "JavaScript", note="failure")
    m = check_tg("clean with a failure", ok0)
    check("critical_after=0, but FAILED present: caveats, not \"CLEAN:\"",
          not claims_clean(m[0]) and "CAVEATS" in m[0] and "failed to process: 1" in m[0], m[0][:600])
    res5 = mk(ca=5)
    res5.act("regenerated", "trailer", "/ID", "aa", "bb")
    m = check_tg("remainder not explained", res5)
    check("critical_after=5 with one /ID: \"NOT CLEAN\" and \"at least 3\"",
          "NOT CLEAN" in m[0] and "at least 3" in m[0], m[0][:400])
    print("summary:", summary_line(res5))
    wl = mk(ca=3)
    wl.act("regenerated", "trailer", "/ID", "aa", "bb")
    wl.act("kept", "/Info", "/Author", "Иванов", note="kept per the user's whitelist (keep)")
    m = check_tg("whitelist", wl)
    check("critical_after=3 = /ID (2) + keep (1): remainder explained, but not \"CLEAN\"",
          "NOT EXPLAINED" not in m[0] and "per your keep list: 1" in m[0] and not claims_clean(m[0]),
          m[0][:500])
    # The keep note is recognized by its text in either language, not by the Latin
    # word "whitelist": a Russian wording without that word must still be counted.
    key = "kept per the user's whitelist (keep)"
    saved_ru = RU[key]
    RU[key] = "оставлено по списку пользователя (keep)"
    try:
        wl_ru = mk(ca=3)
        wl_ru.act("regenerated", "trailer", "/ID", "aa", "bb")
        wl_ru.act("kept", "/Info", "/Author", "Иванов", note=RU[key])
        m = check_tg("whitelist, note written in Russian", wl_ru)
    finally:
        RU[key] = saved_ru
    check("a keep note written in another language is still counted",
          "NOT EXPLAINED" not in m[0] and "per your keep list: 1" in m[0], m[0][:500])

    # 5b. The dispatcher found no surviving values, but the inspector couldn't read
    # part of the file: absence of evidence is not proof, so no "CLEAN:".
    from core.clean import DispatchResult
    gp = DispatchResult(src="a", dst="b", fmt="pdf", profile="stealth", ok=True, verified=True,
                        critical_before=7, critical_after=0, sensitive_before=3, sensitive_after=0,
                        gaps=["after cleaning: Section \"/Names\": ValueError: bad"])
    gp.act("removed", "/Info", "/Author", "Иванов")
    m = check_tg("verified, no survivors, gaps", gp)
    plain = check_plain("verified, no survivors, gaps", gp)
    check("gaps: warned, \"CLEAN:\" not claimed, the gap is named",
          "CLEANLINESS NOT PROVEN" in m[0] and "/Names" in m[0] and not claims_clean(m[0])
          and not claims_clean(plain), m[0][:500])
    check("gaps: the one-line summary says so too", "cleanliness not proven" in summary_line(gp),
          summary_line(gp))
    gp.detect, gp.new_signals = "noticeable", [("scrubbed", "x")]
    m = check_tg("verified, no survivors, gaps, detectable", gp)
    check("gaps: the assessment and the new signs of processing are still shown, without "
          "claiming that nothing leaks",
          "ASSESSMENT" in m[0] and "scrubbed" in m[0] and "No original values leak" not in m[0]
          and "Nothing leaks" not in m[0], m[0][:600])
    gp.detect, gp.new_signals = "", []
    gp.gaps = []
    check("the same result without gaps is CLEAN", claims_clean(render_telegram(gp)[0]))

    # 6. Formats without a cleaner: no lying.
    print("\n=== 6. formats without a cleaner ===")
    # docx/xlsx/pptx are gone from here: since phase 2 they DO have a cleaner
    # (CLEANABLE). What's left are formats that really have none.
    for fmt, ok, phase in (("doc", False, "phase 4"), ("xls", False, "phase 4"),
                           ("doc", True, "phase 4")):
        r = mk(fmt=fmt, ok=ok, cb=-1, ca=0 if ok else -1)
        m = check_tg("%s ok=%s" % (fmt, ok), r)
        check("%s ok=%s: NOT CLEANED + inspect-only + %s" % (fmt, ok, phase),
              "FILE NOT CLEANED" in m[0] and "inspect-only" in m[0] and phase in m[0]
              and not claims_clean(m[0]), m[0][:300])
        if fmt == "doc" and not ok:
            print(m[0])
        print("summary:", summary_line(r))
    tiff = mk(fmt="tiff", ok=False, cb=-1, ca=-1)
    tiff.err("TIFF: cleaning not supported")
    m = check_tg("tiff", tiff)
    check("tiff: deliberate refusal, not a \"phase\"", "deliberately not cleaned" in m[0] and "phase" not in m[0])
    # A format without a cleaner but with actions and ok=True: the report is
    # built as usual, not as a refusal. A safety net for when a format's cleaner
    # lands before it's added to CLEANABLE.
    future = mk(fmt="ppt", ok=True, ca=0)
    future.act("removed", "meta.xml", "dc:creator", "Иванов")
    check("ppt ok=True with actions (cleaner outside CLEANABLE): report built as usual",
          claims_clean(render_telegram(future)[0]))

    # 7. 500 actions, 5000-char values.
    big = mk()
    for i in range(500):
        big.actions.append(SimpleNamespace(
            act=Act.REMOVED if i % 3 else Act.KEPT, location="loc%d" % (i % 7), label="Field %d <&>" % i,
            before="<&>" * 1700, after="&" * 5000, note="&" * 5000))
    print("\n=== 7. 500 actions of 5000 chars ===")
    m = check_tg("500 actions", big)
    check("part numbering [i/n]", m[0].startswith("<b>[1/%d]</b>" % len(m)) and len(m) > 1)
    check("footer in the last part", "The source file was not modified by cleaning" in m[-1])
    check("verdict in the first part", "CRITICAL FINDINGS LEFT" in m[0] or "NOT CLEAN" in m[0])
    check_plain("500 actions", big)
    print("summary:", summary_line(big))
    flood = mk()
    for i in range(60):
        flood.act("removed", "/Info", "Field %d" % i, "\U0001F600" * 150, note="\U0001F600" * 100)
    check_tg("non-BMP emoji (2 units per char)", flood)

    # 8. None and broken objects.
    print("\n=== 8. None ===")
    m = check_tg("None", None)
    print("\n".join(m))
    print(render_plain(None))
    print("summary:", summary_line(None))
    check("None: NOT CLEANED", "FILE NOT CLEANED" in m[0] and "NOT cleaned" in summary_line(None))

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("broken result")

    class Half:
        fmt, profile, ok = "pdf", Profile.STEALTH, True
        critical_before, sensitive_before, sensitive_after = 7, 1, 1
        errors = ["something"]

        @property
        def critical_after(self):
            raise RuntimeError("failing field")

        @property
        def actions(self):
            raise RuntimeError("failing list")

    class BadStr:
        def __str__(self):
            raise RuntimeError("no str")

    mixed = mk()
    mixed.actions[:] = [SimpleNamespace(act="nonsense", location="x", label="x", before="", after="", note=""),
                        object(), SimpleNamespace(act=Act.REMOVED, location="/Info", label=BadStr(),
                                                  before=BadStr(), after="", note=""),
                        ]
    mixed.act("removed", "/Info", "/Author", "Иванов")
    for name, obj in (("Boom", Boom()), ("Half", Half()), ("actions=None", SimpleNamespace(
            fmt="pdf", profile="stealth", ok=True, actions=None, errors=None, critical_before=1,
            critical_after=0, sensitive_before=1, sensitive_after=0)), ("broken entries", mixed),
            ("number as a string", SimpleNamespace(fmt="pdf", profile="x", ok="yes", actions=[], errors="one error",
                                              critical_before="7", critical_after="abc",
                                              sensitive_before=None, sensitive_after=-5))):
        try:
            t, p, sline = render_telegram(obj), render_plain(obj), summary_line(obj)
            good = (all(isinstance(x, str) for x in t) and isinstance(p, str) and "\n" not in sline)
            check("%s: not a single exception" % name, good)
        except Exception as exc:  # noqa: BLE001
            check("%s: not a single exception" % name, False, repr(exc))
            continue
        check_tg(name, obj)
        print("   summary:", sline)
    m = "\n".join(render_telegram(mixed))
    check("broken entry -> an error line, not an empty report",
          "Metadata cleaning" in m and "Иванов" in m and "not read: 2" in m and not claims_clean(m), m[:500])
    h = render_telegram(Half())[0]
    check("Half: critical-after unreadable -> \"not verified\"", "RESULT NOT VERIFIED" in h)
    check("Half: unreadable action list is noted in errors", "action list could not be read" in h)

    # 9. Fuzzing: junk in every field. One invariant: no exceptions, limit and tags intact.
    import random
    rnd = random.Random(20260930)
    alphabet = list("ab <>&\"'\n\t\x00\x1b\\/") + ["\ud800", "\U0001F600", "Ж", "‮", "&amp;", "</b>", "<b>"]
    junk = lambda: "".join(rnd.choice(alphabet) for _ in range(rnd.choice((0, 1, 5, 80, 3000))))
    nums = [-5, 0, 1, 7, 10 ** 30, "x", None, 2.5, True]
    bad_runs = 0
    for _ in range(150):
        acts = [rnd.choice(list(Act) + ["nonsense", None, 5]) for _ in range(rnd.choice((0, 1, 20, 60)))]
        fz = SimpleNamespace(
            fmt=rnd.choice(["pdf", "docx", "tiff", "", None, junk(), "jpeg", 5]),
            profile=rnd.choice([Profile.PARANOID, "stealth", junk(), None]),
            ok=rnd.choice([True, False, None, "yes"]), src=junk(), dst=junk(),
            critical_before=rnd.choice(nums), critical_after=rnd.choice(nums),
            sensitive_before=rnd.choice(nums), sensitive_after=rnd.choice(nums),
            actions=[SimpleNamespace(act=a, location=junk(), label=junk(), before=junk(), after=junk(),
                                     note=junk()) for a in acts] + [rnd.choice([None, 5, "string"])],
            errors=rnd.choice([[junk(), junk()], [], None, junk(), 5]))
        try:
            t, p, line = render_telegram(fz), render_plain(fz), summary_line(fz)
            for msg in t:
                q = Bal()
                q.feed(msg)
                q.close()
                rest = tag_re.sub("", msg)
                if (u16(msg) > TG_LIMIT or q.bad or q.stack or "<" in rest or ">" in rest
                        or re.search(r"&(?!(?:amp|lt|gt);)", rest)):
                    bad_runs += 1
            if "\n" in line or not line or any(len(x) > WIDTH for x in p.split("\n")):
                bad_runs += 1
        except Exception as exc:  # noqa: BLE001
            bad_runs += 1
            print("   fuzzing: exception", repr(exc))
    check("fuzzing, 150 runs: no exceptions, limit/tags/width intact", bad_runs == 0, bad_runs)

    lines = [summary_line(x) for x in (full, bad, unv, ok0, res5, big, None, Boom(), Half(), mixed)]
    check("summary_line has no line breaks", all("\n" not in x and x for x in lines))

    print()
    if fails:
        print("FAILED: %d: %s" % (len(fails), fails))
        raise SystemExit(1)
    print("OK")


if __name__ == "__main__":
    _demo()
