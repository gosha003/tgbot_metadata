"""Shared contract for all metadata inspectors.

Stdlib only. Every core/insp_*.py module imports from here and returns a Report.

Phase 0 of the project: read-only inspection. Nothing is cleaned and nothing
is written to the source file. The goal is a complete map of what leaks from
a file, so the cleaning rules (phases 2-3) are built on facts, not guesses.
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum
from core.i18n import N_, tr

MAX_VALUE_LEN = 400

# Signal confidence in ascending order: needed when merging duplicates.
CONF_RANK = {"low": 0, "medium": 1, "high": 2}

# Absolute paths in OUR error messages. Parser exceptions (pikepdf in
# particular) print the full path of the temp file, and it would reach the
# user's chat: a privacy tool leaking the paths of its own machine.
# Match up to a colon or end of line, not up to a space: otherwise a path with
# a space in it gets cut and the second half still leaks.
# A path starts at a word boundary: otherwise the relative package part name
# "word/media/image1.png" became "word<path hidden>", "http://" became
# "htt<path hidden>", and the error message turned unreadable.
_ABS_PATH = re.compile(
    r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\|(?<![\w.\-])/(?:home|Users|root|tmp|var|private|mnt|media)/)"
    r"[^:\r\n]*"
)
PATH_STUB = N_("<path hidden>")


class Risk(str, Enum):
    """Finding category. Drives both the report color and the cleaning profile."""

    IDENTITY = "identity"        # full name, email, tenant GUID, serial numbers, signers
    ENVIRONMENT = "environment"  # local paths, hostnames, printers, DB servers
    PROVENANCE = "provenance"    # application, version, dates, editing time, generator
    STRUCTURAL = "structural"    # what usually MUST be kept: language, tags, ICC


RISK_ICON = {
    Risk.IDENTITY: "\U0001F534",      # red circle
    Risk.ENVIRONMENT: "\U0001F7E0",   # orange circle
    Risk.PROVENANCE: "\U0001F7E1",    # yellow circle
    Risk.STRUCTURAL: "⚪",        # white circle
}

RISK_TITLE = {
    Risk.IDENTITY: N_("Identity and PII"),
    Risk.ENVIRONMENT: N_("Environment and paths"),
    Risk.PROVENANCE: N_("Provenance and application"),
    Risk.STRUCTURAL: N_("Structural (usually kept)"),
}

# Report order: most dangerous first.
RISK_ORDER = [Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE, Risk.STRUCTURAL]


def clip(value, limit: int = MAX_VALUE_LEN) -> str:
    """Turn a finding value into printable form: one line, bounded length.

    Values come from untrusted files, so newlines and control characters are
    stripped; otherwise they break the report output.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value)
    text = "".join(" " if ch in "\r\n\t" else ch for ch in text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


@dataclasses.dataclass
class Finding:
    """One concrete leak: where it lives, what it's called, what it contains."""

    risk: Risk
    location: str          # "docProps/core.xml" / "/Info" / "APP1:Exif" / "settings.xml"
    label: str             # human-readable field name: "Author", "TotalTime"
    value: str             # the value itself (clipped)
    note: str = ""         # why it matters
    removable: bool = True # False => cannot be removed

    def __post_init__(self) -> None:
        self.value = clip(self.value)
        self.risk = Risk(self.risk)

    @property
    def empty(self) -> bool:
        """The field exists but is empty. Informative in itself."""
        return self.value == ""


@dataclasses.dataclass
class Signal:
    """A conclusion, not a raw value: 'written by openpyxl', 'already scrubbed'."""

    kind: str          # producer | ai | scrubbed | inconsistent | hazard
    detail: str
    confidence: str = "medium"   # high | medium | low

    def __post_init__(self) -> None:
        self.detail = clip(self.detail, 600)


@dataclasses.dataclass
class Report:
    """Inspection result for one file."""

    path: str
    fmt: str                      # key from sniff.py: "docx", "pdf", "jpeg", "doc" ...
    size: int = 0
    findings: list = dataclasses.field(default_factory=list)
    signals: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)
    parts: list = dataclasses.field(default_factory=list)   # container parts (ZIP/OLE)

    # --- helpers that keep inspectors short -------------------------------

    def add(self, risk, location, label, value, note="", removable=True):
        """Add a finding. Empty values are NOT dropped: an empty dc:creator is a
        fact too (a sign of either scrubbing or a stock application with no user
        name). Filtering happens in report.py."""
        self.findings.append(
            Finding(Risk(risk), location, label, value, note, removable)
        )
        return self.findings[-1]

    def add_nonempty(self, risk, location, label, value, note="", removable=True):
        """Same, but silently skip when there is no value."""
        if value is None or clip(value) == "":
            return None
        return self.add(risk, location, label, value, note, removable)

    def signal(self, kind, detail, confidence="medium"):
        """Add a signal. Duplicates are dropped.

        Inspectors emit signals inside loops: per page, per font, per package
        part. Without this guard the same conclusion lands in the report dozens
        of times: "Calibri is the Office 2007-2023 default font" repeated once per
        page and crowded out every other signal.
        On a repeat, the higher of the two confidences wins.
        """
        fresh = Signal(kind, detail, confidence)
        for prev in self.signals:
            if prev.kind == fresh.kind and prev.detail == fresh.detail:
                if CONF_RANK.get(fresh.confidence, 0) > CONF_RANK.get(prev.confidence, 0):
                    prev.confidence = fresh.confidence
                return prev
        self.signals.append(fresh)
        return fresh

    def err(self, message):
        """Parse error. An inspector must NEVER fail as a whole: an unreadable
        part of the file is an entry in errors, not an exception.

        Absolute paths are stripped. A parser's exception text contains the full
        path of the file on OUR machine, and the report goes to the user's chat:
        otherwise the privacy tool leaks exactly what it looks for in other
        people's files. The base name goes too: a file name is metadata.

        Scrubbing happens only here and NOT in add(): a path found INSIDE someone
        else's file is a legitimate ENVIRONMENT finding and must be shown, not
        hidden.
        """
        self.errors.append(clip(_ABS_PATH.sub(tr(PATH_STUB), str(message)), 600))

    # --- report aggregates ------------------------------------------------

    def by_risk(self) -> dict:
        out = {r: [] for r in RISK_ORDER}
        for f in self.findings:
            out.setdefault(f.risk, []).append(f)
        return out

    def count(self, *risks) -> int:
        if not risks:
            return len(self.findings)
        wanted = {Risk(r) for r in risks}
        return sum(1 for f in self.findings if f.risk in wanted)

    @property
    def sensitive(self) -> int:
        """How many findings actually need cleaning (excluding STRUCTURAL).

        This is the metric for the cleaning phase: provenance gets cleaned too.
        The user-facing summary needs critical, not this number.
        """
        return self.count(Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE)

    @property
    def critical(self) -> int:
        """Findings that directly reveal a person or a machine.

        Deliberately NOT equal to sensitive. Provenance (application, version,
        dates, fonts) needs cleaning too, but it can run to hundreds of lines: a
        real PDF had 130 entries like "PDF version = 1.7" and per-page font lists
        next to seven real leaks. Calling all 137 critical would drown those
        seven. The summary and report header count this field; the cleaning
        phase counts sensitive.
        """
        return self.count(Risk.IDENTITY, Risk.ENVIRONMENT)
