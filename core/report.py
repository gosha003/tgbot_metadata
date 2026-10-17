"""Render a Report as a human-readable report: Telegram HTML and plain text for the CLI.

Phase 0: inspection only. The report cleans nothing and never changes the file;
it only shows what the file contains, so the cleaning rules (phases 2-3) are
built on facts.

Everything that came from the file (values, part names, error texts) is
untrusted and always goes through html.escape(): a field value may well
contain "<b>" or "&" and break parsing on Telegram's side.

Stdlib only.
"""

from __future__ import annotations

import html
import textwrap

from core import sniff
from core.model import RISK_ICON, RISK_ORDER, RISK_TITLE, Risk, clip
from core.i18n import N_, plural, tr

__all__ = ["render_telegram", "render_plain", "summary_line", "spoiler", "MASK_VALUES"]

# Size limit of one Telegram message. The margin is for the part-numbering prefix.
TG_LIMIT = 4096
TG_BUDGET = TG_LIMIT - 64

# Values from the file go to Telegram under a spoiler. This protects against
# casual viewing: chat-list previews, screenshots, someone looking over your
# shoulder, a forwarded message. It does NOT protect against Telegram itself:
# the text has still reached its servers and stays in the chat history. That
# limit has to be stated out loud (_tg_footer does it), not glossed over.
# The policy is shared by both renderers: core/cleanreport.py reads this flag.
MASK_VALUES = True

# Width of the plain report for the CLI.
WIDTH = 100

# Signal block titles. Dict order = output order.
SIGNAL_TITLE = {
    "producer": N_("What produced the file"),
    "ai": N_("Signs of library or AI generation"),
    "dating": N_("Dating and geolocation from indirect evidence"),
    "scrubbed": N_("Signs of earlier scrubbing"),
    "inconsistent": N_("Contradictions in the metadata"),
    "hazard": N_("Active content"),
}

SIGNAL_ICON = {
    "producer": "\U0001F6E0",      # hammer and wrench
    "ai": "\U0001F916",            # robot
    "dating": "\U0001F4C5",        # calendar
    "scrubbed": "\U0001F9FC",      # soap
    "inconsistent": "\u2757",      # exclamation mark
    "hazard": "\u2622",            # radioactive sign
}

CONFIDENCE = {"high": N_("high"), "medium": N_("medium"), "low": N_("low")}

# No emoji in plain output: the Windows console mangles them.
RISK_MARK = {
    Risk.IDENTITY: "[!!!]",
    Risk.ENVIRONMENT: "[!! ]",
    Risk.PROVENANCE: "[!  ]",
    Risk.STRUCTURAL: "[   ]",
}


# --- small utilities -----------------------------------------------------


def _esc(value, cap: int = 400) -> str:
    """Untrusted value -> a safe piece of Telegram HTML.

    clip() first (strips control characters and length), then escaping.
    Order matters: escape what's already clipped, otherwise an HTML entity can
    be cut in half.
    """
    return html.escape(clip(value, cap), quote=False)


def spoiler(fragment: str) -> str:
    """Ready HTML fragment -> the same fragment under a Telegram spoiler.

    Takes ALREADY escaped HTML, unlike _esc(): the wrap goes around <code>,
    not inside it. Telegram doesn't allow nested entities inside code and pre
    but does allow them inside spoiler, so this order is required and must not
    be swapped.

    fragment: a piece of HTML (usually "<code>value</code>").
    Returns the same piece in <tg-spoiler>, or unchanged if masking is turned
    off via MASK_VALUES or the fragment is empty.
    """
    if not MASK_VALUES or not fragment:
        return fragment
    return "<tg-spoiler>%s</tg-spoiler>" % fragment


def _plain(value, cap: int = 400) -> str:
    """Same, but without escaping: for the CLI."""
    return clip(value, cap)


def _plural(n: int, one: str, many: str) -> str:
    """Pick the plural form for n (delegates to core.i18n.plural)."""
    return plural(n, one, many)


def _fmt_size(size) -> str:
    """File size in KB/MB. A broken size is no reason to crash."""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return tr("size unknown")
    if size < 0:
        return tr("size unknown")
    if size < 1024:
        return tr("%d B") % size
    kb = size / 1024.0
    if kb < 1024:
        return tr("%.1f KB") % kb
    return tr("%.1f MB") % (kb / 1024.0)


def _confidence(value) -> str:
    key = clip(value, 32).lower()
    return tr(CONFIDENCE[key]) if key in CONFIDENCE else (key or tr("not specified"))


def _by_location(items):
    """Group findings by location, keeping the order of appearance."""
    groups = {}
    for f in items:
        groups.setdefault(clip(f.location, 120) or "—", []).append(f)
    return groups.items()


def _other_title(kind: str) -> str:
    """Title for a signal of unknown kind: don't drop it, but don't make things up either."""
    return (tr("Other: ") + kind) if kind else tr("Other (signal kind not specified)")


def _signal_groups(signals):
    """Signals by kind: known kinds in the given order first, then the rest."""
    buckets = {}
    for s in signals:
        buckets.setdefault(clip(s.kind, 40), []).append(s)
    order = [k for k in SIGNAL_TITLE if k in buckets]
    order += [k for k in buckets if k not in SIGNAL_TITLE]
    return [(k, buckets[k]) for k in order]


def _attr(obj, name):
    """A list from a Report that may be missing or may raise.

    The inspector hands us the Report, and for a badly broken file it could
    contain anything, down to an object that raises on attribute access.
    The renderer has no right to crash on that.
    """
    try:
        return getattr(obj, name, None) or []
    except Exception:
        return []


def _fmt_name(report) -> str:
    """Human-readable format name. An empty fmt is a format too: unrecognized."""
    try:
        fmt = clip(getattr(report, "fmt", "") or "", 40)
    except Exception:
        fmt = ""
    return sniff.pretty(fmt or "unknown")


def _counts(report):
    """(total, needing cleaning, revealing a person or machine).

    Two different numbers that must not be confused. sensitive is the amount
    of work for the cleaning phase (provenance included). critical is how bad
    things are: a real PDF can have 137 findings "needing cleaning" with only
    7 real leaks; the rest is per-page font lists and the format version.
    """
    total = len(_attr(report, "findings"))
    try:
        sensitive = int(report.sensitive)
    except Exception:
        # The metric failed: it's more honest to show everything as needing
        # cleaning than to understate it. Underestimating a leak is worse than
        # overestimating it.
        sensitive = total
    try:
        critical = int(report.critical)
    except Exception:
        critical = sensitive
    return total, sensitive, critical


def _safe(build, report):
    """Build sections without letting one failure take the whole report down.

    One broken finding becomes an error line in its place, not an empty
    message instead of the whole report: the header, signals and other
    categories must still reach the user.
    """
    try:
        return build(report)
    except Exception as exc:
        return [[tr("⚠ <i>report section failed to render: %s</i>") % _esc(repr(exc), 200)]]


# --- splitting into Telegram messages ------------------------------------


def _tg_len(s: str) -> int:
    """Length of a string in UTF-16 code units, the way Telegram's limit counts
    it, not Python len() (code points). For characters outside the Basic
    Multilingual Plane (emoji, rare CJK) these differ: one such character is
    2 units, not 1. On metadata values stuffed with such characters, the
    difference doubles the real message length, and Telegram may reject a
    message even though Python's len() fit the limit.
    """
    return len(s.encode("utf-16-le")) // 2


def _pack(sections, limit: int = TG_BUDGET):
    """Sections -> list of messages, each no longer than limit (in UTF-16 units).

    Split at section boundaries first; if one section doesn't fit as a whole,
    split at individual lines (i.e. findings). A line always has balanced
    tags, so HTML is never cut in the middle.
    """
    buf = []
    msgs = []

    def cur_len():
        return sum(_tg_len(x) + 1 for x in buf) - 1 if buf else 0

    def flush():
        if buf:
            msgs.append("\n".join(buf))
            del buf[:]

    for sec in sections:
        if not sec:
            continue
        sec_len = sum(_tg_len(x) + 1 for x in sec) - 1
        if sec_len + 1 <= limit:
            if buf and cur_len() + 1 + sec_len + 1 > limit:
                flush()
            if buf:
                buf.append("")
            buf.extend(sec)
            continue
        # The section doesn't fit as a whole. Top up the current message with
        # its lines rather than sending it half-filled; otherwise we get
        # three-line messages.
        if buf:
            # The separating blank line itself adds 1 to the length. If the
            # buffer is already full to the brim (exactly limit, which is possible,
            # see the invariant below), adding it unchecked once overflowed
            # the final message by 1 unit. Same check as in the
            # "whole section" branch above.
            if cur_len() + 1 > limit:
                flush()
            else:
                buf.append("")
        for line in sec:
            # Safety net: by construction lines are short (every value goes
            # through _esc with a limit), but if someone builds a Report that
            # bypasses model.clip, an HTML tag must not be cut. The skip is made
            # visible: a silently lost finding in a leak report is worse than an
            # explicit "see the text report" marker.
            if _tg_len(line) > limit:
                line = (tr("• <i>(line didn't fit in one Telegram message and was skipped; see "
                           "the text report)</i>"))
            if buf and cur_len() + 1 + _tg_len(line) > limit:
                flush()
            buf.append(line)
    flush()
    return msgs


# --- Telegram ------------------------------------------------------------


def _tg_header(report):
    total, sensitive, critical = _counts(report)
    fmt = _esc(_fmt_name(report), 80)
    line = "\U0001F4C4 <b>%s</b> · %s" % (fmt, _esc(_fmt_size(getattr(report, "size", 0)), 40))
    stats = tr("Findings: <b>%d</b>, needing cleaning: <b>%d</b>") % (total, sensitive)
    if critical:
        stats += tr("\n\U0001F534 Directly reveal a person or machine: <b>%d</b>") % critical
    return [line, stats]


def _tg_nothing(report):
    """An empty result can mean two things: either the file really is clean,
    or we simply didn't read it. These must never be confused: in the second
    case the user would think the file is safe when it's merely unchecked."""
    if _attr(report, "errors"):
        return _tg_unread()
    return _tg_clean()


def _tg_unread():
    """Parsing didn't finish: the absence of findings proves nothing here."""
    return [
        tr("⚠ <b>Metadata was not extracted.</b>"),
        tr("Parsing didn't finish (errors below), so an empty result here does <b>not</b> mean "
           "the file is clean: anything could be in the unread parts. Don't judge the file's "
           "cleanliness by this report."),
    ]


def _tg_clean():
    return [
        tr("ℹ <b>No explicit metadata found.</b>"),
        tr("That isn't necessarily good news: a clean file is itself a sign that it has already "
           "been processed. Normally created documents almost always carry at least the "
           "application and dates."),
    ]


def _tg_signals(report):
    signals = _attr(report, "signals")
    if not signals:
        return []
    out = [[tr("\U0001F50E <b>SIGNALS</b>")]]
    for kind, items in _signal_groups(signals):
        title = tr(SIGNAL_TITLE.get(kind, "")) or _other_title(kind)
        lines = ["%s <b>%s</b>" % (SIGNAL_ICON.get(kind, "\u2022"), _esc(title, 120))]
        for s in items:
            lines.append(
                tr("• %s <i>(confidence: %s)</i>")
                % (_esc(s.detail, 600), _esc(_confidence(s.confidence), 32))
            )
        out.append(lines)
    return out


def _tg_finding(f):
    # An empty value isn't spoilered: there's nothing to hide, and an extra tap
    # only makes the report harder to read.
    value = (tr("<i>(empty)</i>") if f.empty
             else spoiler("<code>%s</code>" % _esc(f.value)))
    line = "• %s: %s" % (_esc(f.label, 120), value)
    note = _esc(f.note, 160)
    if f.removable:
        if note:
            line += " <i>— %s</i>" % note
    else:
        line += tr("\n   \U0001F512 cannot be removed — %s") % (note or tr("the file's content would be "
                                                                   "lost"))
    return line


def _tg_findings(report):
    out = []
    by_risk = report.by_risk()
    for risk in RISK_ORDER:
        items = by_risk.get(risk) or []
        if not items:
            continue
        lines = [
            "%s <b>%s</b> — %d"
            % (RISK_ICON[risk], _esc(tr(RISK_TITLE[risk]), 80), len(items))
        ]
        for location, group in _by_location(items):
            lines.append("<i>%s</i>" % _esc(location, 120))
            for f in group:
                lines.append(_tg_finding(f))
        out.append(lines)
    return out


def _tg_errors(report):
    errors = _attr(report, "errors")
    if not errors:
        return []
    lines = [
        tr("⚠ <b>Could not parse</b> — %d") % len(errors),
        tr("These are parts of the file that couldn't be read. Not the same as having no "
           "metadata: anything could be in there."),
    ]
    for e in errors:
        lines.append("• <code>%s</code>" % _esc(e, 600))
    return [lines]


def _tg_footer(report=None):
    # "The file was read" is untrue for fmt == "unreadable" (cloud placeholder,
    # no permission, broken path): such a file was NOT read, and a privacy
    # report must not claim otherwise. Unchanged is always stated: the
    # inspection phase never writes to the file, whatever the outcome.
    try:
        read_ok = report is None or (getattr(report, "fmt", "") or "") != "unreadable"
    except Exception:
        read_ok = True
    first = (tr("This is the inspection phase: the file was read and not modified.")
             if read_ok else
             tr("This is the inspection phase. The file was not modified, but it couldn't be "
                "read either."))
    lines = ["———", "<i>%s</i>" % first]
    if MASK_VALUES:
        # Honest limit: the spoiler hides the value from view, but not from
        # Telegram. A privacy tool must not stay silent about it, or the user
        # will think the values never went anywhere.
        lines.append(
            tr("<i>Values are hidden; tap to reveal. But the report text has already reached "
               "Telegram's servers and stays in the chat history: if the findings are sensitive, "
               "delete the conversation and use the CLI instead.</i>"))
    return lines


def render_telegram(report) -> list:
    """Report -> list of messages ready to send with parse_mode=\"HTML\".

    Only tags Telegram allows are used: b, i, code. Line breaks are
    a plain \\n, no <br>.
    """
    # Each section is built separately: one broken finding must not take
    # the header, signals, parse errors and other categories down with it.
    sections = _safe(lambda r: [_tg_header(r)], report)
    if not _attr(report, "findings") and not _attr(report, "signals"):
        sections += _safe(lambda r: [_tg_nothing(r)], report)
    else:
        sections += _safe(_tg_signals, report)
        sections += _safe(_tg_findings, report)
    sections += _safe(_tg_errors, report)
    sections.append(_tg_footer(report))
    try:
        parts = _pack(sections)
    except Exception as exc:  # the report must never crash the bot
        return [tr("⚠ Could not build the report: <code>%s</code>") % _esc(repr(exc), 300)]
    if not parts:
        return [tr("ℹ The report is empty.")]
    if len(parts) > 1:
        total = len(parts)
        parts = [
            "<b>[%d/%d]</b>\n%s" % (i, total, p) for i, p in enumerate(parts, 1)
        ]
    return parts


# --- plain text ----------------------------------------------------------


def _fill(text, indent="", extra="  "):
    """Wrap a long value to WIDTH. Content is never cut."""
    if not text:
        return indent.rstrip()
    return textwrap.fill(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent + extra,
        break_long_words=True,
        break_on_hyphens=False,
    )


def render_plain(report) -> str:
    """The same content without HTML, indented: for the CLI and logs."""
    try:
        return _render_plain(report)
    except Exception as exc:
        return tr("Could not build the report: %s") % repr(exc)


def _plain_signals(report):
    """The SIGNALS block in plain form."""
    signals = _attr(report, "signals")
    if not signals:
        return []
    out = ["", tr("SIGNALS"), "-" * WIDTH]
    for kind, items in _signal_groups(signals):
        title = tr(SIGNAL_TITLE.get(kind, "")) or _other_title(kind)
        out.append("  %s" % _plain(title, 120))
        for s in items:
            out.append(
                _fill(
                    tr("- %s (confidence: %s)")
                    % (_plain(s.detail, 600), _confidence(s.confidence)),
                    "    ",
                )
            )
    return out


def _plain_findings(report):
    """Raw findings in plain form: by category, then by location."""
    if not _attr(report, "findings"):
        return []
    out = []
    by_risk = report.by_risk()
    for risk in RISK_ORDER:
        items = by_risk.get(risk) or []
        if not items:
            continue
        out.append("")
        out.append("%s %s — %d" % (RISK_MARK[risk], tr(RISK_TITLE[risk]), len(items)))
        out.append("-" * WIDTH)
        for location, group in _by_location(items):
            out.append("  %s" % _plain(location, 120))
            for f in group:
                value = tr("(empty)") if f.empty else _plain(f.value)
                out.append(_fill("- %s: %s" % (_plain(f.label, 120), value), "    "))
                note = _plain(f.note, 160)
                if not f.removable:
                    out.append(
                        _fill(
                            tr("! cannot be removed: %s")
                            % (note or tr("the file's content would be lost")),
                            "      ",
                        )
                    )
                elif note:
                    out.append(_fill("%s" % note, "      "))
    return out


def _safe_lines(build, report):
    """Same as _safe, but for plain output: section -> flat list of lines."""
    try:
        return build(report)
    except Exception as exc:
        return ["", tr("  ! report section failed to render: %r") % (exc,)]


def _render_plain(report):
    total, sensitive, critical = _counts(report)
    signals = _attr(report, "signals")
    findings = _attr(report, "findings")
    errors = _attr(report, "errors")

    rule = "=" * WIDTH
    out = [rule, tr("METADATA INSPECTION REPORT"), rule]
    out.append(tr("Format:  %s") % _plain(_fmt_name(report), 80))
    out.append(tr("Size:    %s") % _fmt_size(getattr(report, "size", 0)))
    out.append(tr("Findings: %d, needing cleaning: %d") % (total, sensitive))
    if critical:
        out.append(tr("REVEAL A PERSON OR MACHINE: %d") % critical)

    if not findings and not signals:
        out.append("")
        if errors:
            # No findings with parse errors means not a clean file but an unchecked
            # one. The wording must make that distinction.
            out.append(tr("Metadata was not extracted."))
            out.append(
                _fill(
                    tr("Warning: parsing didn't finish (see errors below), so an empty result "
                       "does NOT mean the file is clean. Anything could be in the unread parts; "
                       "don't judge the file's cleanliness by this report."),
                    "  ",
                    "",
                )
            )
        else:
            out.append(tr("No explicit metadata found."))
            out.append(
                _fill(
                    tr("Warning: the absence of metadata is itself a sign the file was processed "
                       "before. Normally created documents almost always carry at least the "
                       "generating application and dates."),
                    "  ",
                    "",
                )
            )
    else:
        out.extend(_safe_lines(_plain_signals, report))
        out.extend(_safe_lines(_plain_findings, report))

    if errors:
        out.append("")
        out.append(tr("COULD NOT PARSE — %d") % len(errors))
        out.append("-" * WIDTH)
        out.append(
            _fill(
                tr("These are parts of the file that couldn't be read. Not the same as having no "
                   "metadata: anything could be in there."),
                "  ",
                "",
            )
        )
        for e in errors:
            out.append(_fill("- %s" % _plain(e, 600), "    "))

    out.append("")
    out.append(rule)
    out.append(tr("Inspection phase: the file was read and not modified.")
               if (getattr(report, "fmt", "") or "") != "unreadable"
               else tr("Inspection phase. The file was not modified, but it couldn't be read."))
    out.append(tr("Cleaning is a separate command: python -m core.clean <file>."))
    out.append(rule)
    return "\n".join(out)


# --- one-line summary ----------------------------------------------------


def summary_line(report) -> str:
    """Short answer and log line.

    "PDF, 12 findings (3 critical), made with Skia/PDF — printed from Chrome".
    "Critical" here is report.critical (IDENTITY + ENVIRONMENT), NOT
    report.sensitive: provenance can run to hundreds of lines and would drown
    the real leaks. The cleaning workload is shown in the report header.
    """
    try:
        fmt = _plain(_fmt_name(report), 80)
        total, sensitive, critical = _counts(report)
        signals = _attr(report, "signals")
        errors = _attr(report, "errors")
        err_part = "%d %s" % (
            len(errors), _plural(len(errors), "parse error", "parse errors")
        )
        if not total and not signals:
            # With no findings the wording depends on whether the file was read.
            # "No metadata found" about an unreadable file would be a lie in the log.
            if errors:
                return tr("%s, metadata not extracted, %s") % (fmt, err_part)
            return tr("%s, no metadata found") % fmt
        parts = [
            fmt,
            "%d %s (%d %s)"
            % (
                total,
                _plural(total, "finding", "findings"),
                critical,
                _plural(critical, "critical", "critical"),
            ),
        ]
        # "Made with" may only be said about a real producer (kind="producer")
        # or a library/AI (kind="ai"). "dating" signals (a font, the paper size)
        # date the document but say nothing about the application: Calibri used
        # to end up here and the summary said "PDF, made with Calibri".
        def _first(kind):
            return next(
                (s for s in signals if clip(getattr(s, "kind", ""), 40) == kind), None
            )

        made_by = _first("producer") or _first("ai")
        if made_by is not None:
            parts.append(tr("made with %s") % _plain(getattr(made_by, "detail", ""), 120))
        else:
            dating = _first("dating")
            if dating is not None:
                parts.append(tr("producer not recognized; %s")
                             % _plain(getattr(dating, "detail", ""), 120))
        if errors:
            parts.append(err_part)
        return ", ".join(parts)
    except Exception as exc:
        return tr("report failed to build: %r") % (exc,)


# --- self-test -----------------------------------------------------------


def _demo_report():
    """Report with all four categories, an empty value, removable=False,
    signals of every kind, HTML injection in a value and a parse error."""
    from core.model import Report

    r = Report(path="demo.docx", fmt="docx", size=1536 * 1024)
    r.add(Risk.IDENTITY, "docProps/core.xml", "Author", "Иванов Иван <ivan@corp.ru>",
          note="full name and email of the license owner")
    r.add(Risk.IDENTITY, "docProps/core.xml", "Modified by", "")
    r.add(Risk.IDENTITY, "word/_rels", "Digital signature", "CN=Иванов И.И., O=ООО Ромашка",
          note="removing it would break the signature", removable=False)
    r.add(Risk.ENVIRONMENT, "word/settings.xml", "Template path",
          r"C:\Users\ivanov\AppData\Roaming\Microsoft\Шаблоны\<script>&Normal.dotm")
    r.add(Risk.ENVIRONMENT, "docProps/app.xml", "Printer", "\\\\srv-print01\\HP-3-этаж")
    r.add(Risk.PROVENANCE, "docProps/app.xml", "Application", "Microsoft Office Word 16.0")
    r.add(Risk.PROVENANCE, "docProps/app.xml", "TotalTime", "417",
          note="minutes of actual editing")
    r.add(Risk.STRUCTURAL, "word/styles.xml", "Document language", "ru-RU",
          note="kept when cleaning")
    r.signal("producer", "Microsoft Word 16.0 (Office 365)", "high")
    r.signal("ai", "ZIP part order is typical of python-docx", "low")
    r.signal("scrubbed", "dc:creator is empty while cp:lastModifiedBy is set", "medium")
    r.signal("inconsistent", "creation date is later than modification date", "high")
    r.signal("hazard", "vbaProject.bin: the document contains macros", "high")
    r.err("word/embeddings/oleObject1.bin: could not open the OLE stream")
    return r


def _demo():
    from core.model import Report

    global MASK_VALUES          # self-test of the masking switch, see below

    r = _demo_report()

    msgs = render_telegram(r)
    for m in msgs:
        # Telegram's real limit is in UTF-16 code units, not Python len().
        assert _tg_len(m) <= TG_LIMIT, _tg_len(m)
    joined = "\n".join(msgs)
    assert "&lt;script&gt;&amp;" in joined, "value not escaped"
    assert "<script>" not in joined, "raw tag leaked into Telegram HTML"
    assert "<i>(empty)</i>" in joined
    assert "cannot be removed" in joined

    # Value masking. Spoilers are counted one by one instead of searching for
    # a single tag: a missed render site would otherwise go unnoticed, since
    # other findings already add spoilers and the check becomes vacuous.
    assert MASK_VALUES, "masking must be on by default"
    n_values = sum(1 for f in r.findings if not f.empty)
    assert joined.count("<tg-spoiler><code>") == n_values, (
        "%d spoilers, but %d non-empty values"
        % (joined.count("<tg-spoiler><code>"), n_values))
    assert "<tg-spoiler><i>(empty)</i>" not in joined, (
        "an empty value needn't be hidden: there's nothing to hide")
    assert "reached Telegram's servers" in joined, (
        "the note about Telegram storage is mandatory: the spoiler hides the value from view, "
        "not from Telegram")
    # The switch must actually switch off: otherwise there's no way to test
    # rendering without masking.
    MASK_VALUES = False
    try:
        bare = "\n".join(render_telegram(r))
        assert "tg-spoiler" not in bare, "MASK_VALUES=False did not disable the spoiler"
        assert "<code>" in bare, "without masking the value must stay in <code>"
    finally:
        MASK_VALUES = True

    print("=== render_telegram: %d message(s) ===" % len(msgs))
    for i, m in enumerate(msgs, 1):
        print("--- message %d, %d chars ---" % (i, len(m)))
        print(m)

    print()
    print("=== render_plain ===")
    plain = render_plain(r)
    assert "<code>" not in plain and "&amp;" not in plain
    print(plain)

    print()
    print("=== summary_line ===")
    print(summary_line(r))

    # empty report
    empty = Report(path="clean.pdf", fmt="pdf", size=2048)
    print()
    print("=== empty report ===")
    print(render_telegram(empty)[0])
    print(summary_line(empty))

    # load: 500 findings must be split and fit the limit
    big = Report(path="big.pdf", fmt="pdf", size=99 * 1024 * 1024)
    for i in range(500):
        big.add(Risk.PROVENANCE, "/Info", "Field %d" % i, "value <%d> & more" % i)
    big_msgs = render_telegram(big)
    sizes = [_tg_len(m) for m in big_msgs]
    assert all(s <= TG_LIMIT for s in sizes), sizes
    assert len(big_msgs) > 1
    print()
    print("=== 500 findings: %d messages, lengths %s ===" % (len(big_msgs), sizes))
    print(summary_line(big))
    print("max message length: %d (limit %d)" % (max(sizes), TG_LIMIT))

    # Regression for _pack(): a buffer filled EXACTLY to the limit by one line,
    # followed by a section that doesn't fit as a whole. The blank separator
    # line before such a section used to be added unchecked and could push the
    # message 1 unit over the limit.
    overflow_secs = [["A" * TG_BUDGET], ["x" * (TG_BUDGET + 1), "y"]]
    overflow_msgs = _pack(overflow_secs)
    assert all(_tg_len(m) <= TG_BUDGET for m in overflow_msgs), [
        _tg_len(m) for m in overflow_msgs
    ]

    # Characters outside the Basic Multilingual Plane (emoji etc.) are 2 UTF-16
    # code units each, not 1. Python len() undercounts them by half; if the limit
    # recount is forgotten, the packer emits a message Telegram itself rejects
    # as too long, even though Python considered it within the limit. This
    # checks exactly that, not just the Python length.
    astral = Report(path="astral.pdf", fmt="pdf", size=10)
    for i in range(9):
        astral.add(Risk.PROVENANCE, "/Info", "Field %d" % i, "\U0001F600" * 400)
    astral_msgs = render_telegram(astral)
    astral_sizes = [_tg_len(m) for m in astral_msgs]
    assert all(s <= TG_LIMIT for s in astral_sizes), astral_sizes
    print()
    print("=== non-BMP emoji: %d messages, UTF-16 lengths %s ===" % (
        len(astral_msgs), astral_sizes))

    # Broken file: no findings, but parse errors. The report must say "not
    # read", not "already scrubbed": otherwise the user would think an
    # unchecked file is safe.
    broken = Report(path="trunc.pdf", fmt="pdf", size=812)
    broken.err("truncated xref: pikepdf could not open the file")
    broken.err("/Info: object not found")
    tg_broken = render_telegram(broken)
    pl_broken = render_plain(broken)
    assert any("not extracted" in m for m in tg_broken), tg_broken
    assert "not extracted" in pl_broken
    assert not any("already been processed" in m for m in tg_broken)
    assert "processed before" not in pl_broken
    assert "2 parse errors" in summary_line(broken), summary_line(broken)
    print()
    print("=== broken file: no findings, but 2 parse errors ===")
    for m in tg_broken:
        print(m)
    print(summary_line(broken))

    # One broken finding must not take the whole report down: the header,
    # signals and parse errors must still reach the user.
    class _BrokenFinding:
        def __getattr__(self, name):
            raise RuntimeError("broken finding")

    partial = _demo_report()
    partial.findings.append(_BrokenFinding())
    tg_partial = render_telegram(partial)
    blob = " ".join(tg_partial)
    assert "Word (OOXML)" in blob                     # header survived
    assert "SIGNALS" in blob                         # signals survived
    assert "Could not parse" in blob            # parse errors survived
    assert "failed to render" in blob                  # and the failure itself is visible
    assert "failed to render" in render_plain(partial)
    print()
    print("=== one broken finding: the report degrades section by section ===")
    for m in tg_partial:
        print(m)

    # Singular forms in the summary.
    one = Report(path="one.pdf", fmt="pdf", size=10)
    one.add(Risk.IDENTITY, "/Info", "Author", "Иванов")
    assert "1 finding (1 critical)" in summary_line(one), summary_line(one)
    print()
    print("=== summary_line, singular ===")
    print(summary_line(one))

    print("OK")


if __name__ == "__main__":
    _demo()
