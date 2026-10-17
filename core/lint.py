"""Score whether a file looks processed. Phase 3.

Doesn't re-inspect the file or look for new metadata: the inspector has
already collected the signals. Here they're folded into one score that the
report shows after cleaning. The dispatcher's new_signals is a special
case: which traces came from our cleaning specifically. This score looks
at the result as a whole, including traces that were there before.

Public API:
    detect(report) -> (level, reasons)
        level: "none" | "noticeable" | "obvious"
        reasons: short lines for the report, no values from the file

Never raises.
"""

from __future__ import annotations

from core.i18n import tr

__all__ = ["detect"]

# ai is worse than scrubbed: the file looks not cleaned but built by a program.
# inconsistent alone doesn't mean "scrubbed": it's a contradiction in the data.
_OBVIOUS = "obvious"
_NOTICE = "noticeable"
_NONE = "none"


def detect(report) -> tuple:
    """Report signals -> how processed the file looks.

    report: a Report or any object with .signals (items with .kind).
    Returns (level, reasons). level is none / noticeable / obvious.
    reasons is a list of explanations without values from the file.
    On any failure: ("none", []).
    """
    try:
        signals = list(getattr(report, "signals", None) or [])
    except Exception:
        return _NONE, []
    kinds = set()
    edited = 0
    for s in signals:
        kind = getattr(s, "kind", None)
        if kind is None and isinstance(s, (tuple, list)) and s:
            kind = s[0]
        if kind:
            kinds.add(kind)
        # A high-confidence contradiction ("ModDate earlier than CreationDate",
        # "Aptos in a 2019 document") points to manual editing by itself. Weak ones
        # (created == modified) happen in honest files, so they don't count.
        if kind == "inconsistent" and getattr(s, "confidence", "") == "high":
            edited += 1
    reasons = []
    if "ai" in kinds:
        reasons.append(
            tr("The inspector considers the file built by a library or generator, not saved by "
               "an office application."))
    if "scrubbed" in kinds:
        reasons.append(
            tr("There are signs of scrubbing: empty identity fields or a container the claimed "
               "application doesn't write."))
    if edited:
        reasons.append(
            tr("The metadata contradicts itself (%d): dates, versions or fonts disagree in a way "
               "that only happens after manual editing.") % edited)
    if "ai" in kinds:
        return _OBVIOUS, reasons
    if "scrubbed" in kinds or edited:
        return _NOTICE, reasons
    return _NONE, []


def _selftest():
    from types import SimpleNamespace

    def rep(*kinds):
        return SimpleNamespace(signals=[SimpleNamespace(kind=k, detail="x") for k in kinds])

    assert detect(rep()) == ("none", [])
    level, why = detect(rep("scrubbed", "producer"))
    assert level == "noticeable" and why, why
    level, why = detect(rep("ai", "scrubbed"))
    assert level == "obvious" and len(why) == 2
    level, _why = detect(rep("inconsistent"))
    assert level == "none"
    strong = SimpleNamespace(signals=[SimpleNamespace(kind="inconsistent", detail="x", confidence="high")])
    level, why = detect(strong)
    assert level == "noticeable" and len(why) == 1, (level, why)
    weak = SimpleNamespace(signals=[SimpleNamespace(kind="inconsistent", detail="x", confidence="low")])
    assert detect(weak) == ("none", [])
    assert detect(None) == ("none", [])
    print("OK")


if __name__ == "__main__":
    _selftest()
