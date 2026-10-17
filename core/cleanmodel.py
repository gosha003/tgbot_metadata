"""Cleaning contract (phases 1-4). Inspection lives in model.py; this extends it.

The central idea everything else follows from: **the inspector is the
cleaner's oracle**. "Did cleaning work?" is not separate logic but a second
run of inspect_file() on the output:

    before = inspect_file(src)
    res    = clean_file(src, dst, Profile.PARANOID)
    after  = inspect_file(dst)
    assert after.critical == 0

That's why phase 0 came first: without a working inspector, cleaning has
no way to prove it worked.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from core.model import Risk, clip
from core.i18n import N_


class Profile(str, Enum):
    """Cleaning strategy. Details and rationale: docs/ROADMAP.md."""

    PARANOID = "paranoid"
    """Remove everything except an explicit whitelist. The file is guaranteed
    clean but obviously processed: a real Word docx always has app.xml with
    Application, and its absence reads as a cleaning tool's trace.
    The right choice when cleanliness matters more than invisibility."""

    STEALTH = "stealth"
    """Default. Don't blank fields; bring them to what a stock application
    writes when no user name is set: an ordinary, unremarkable state that
    occurs in the wild.

    In practice:
      * identity and environment (IDENTITY, ENVIRONMENT): remove;
      * the application and its version: KEEP as is. It's true, the file
        really was made in that application, and a mismatched pair like
        Creator=Word + Producer=qpdf is detected instantly;
      * dates: keep a plausible gap between creation and modification
        instead of collapsing them into one second;
      * structural (STRUCTURAL): keep language, accessibility tags,
        ICC profile, outline."""

    REGENERATE = "regenerate"
    """Regenerate through a stock application (LibreOffice). Phase 4."""


# Which risk categories a profile removes. No profile removes STRUCTURAL:
# it's what cleaning must keep, or accessibility and color break.
PROFILE_REMOVES = {
    Profile.PARANOID: (Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE),
    Profile.STEALTH: (Risk.IDENTITY, Risk.ENVIRONMENT),
    Profile.REGENERATE: (Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE),
}


class Act(str, Enum):
    """What exactly was done to a field."""

    REMOVED = "removed"        # the field is gone
    BLANKED = "blanked"        # the field exists, value empty (stock application look)
    NORMALIZED = "normalized"  # value replaced with a plausible one
    REGENERATED = "regenerated"  # value regenerated (e.g. trailer /ID)
    KEPT = "kept"              # kept on purpose: whitelist or STRUCTURAL
    UNREMOVABLE = "unremovable"  # cannot be removed
    FAILED = "failed"          # tried and failed


ACT_TITLE = {
    Act.REMOVED: N_("removed"),
    Act.BLANKED: N_("blanked"),
    Act.NORMALIZED: N_("normalized"),
    Act.REGENERATED: N_("regenerated"),
    Act.KEPT: N_("kept on purpose"),
    Act.UNREMOVABLE: N_("cannot be removed"),
    Act.FAILED: N_("failed"),
}


@dataclasses.dataclass
class CleanAction:
    """One change. The cleaning report is built from these."""

    act: Act
    location: str
    label: str
    before: str = ""
    after: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        self.act = Act(self.act)
        self.before = clip(self.before)
        self.after = clip(self.after)


@dataclasses.dataclass
class CleanResult:
    """Cleaning result for one file.

    ok == True means the output file was written AND opens with its own
    parser. The cleaner must check this itself: handing the user a "cleaned"
    file that won't open is worse than not cleaning.
    """

    src: str
    dst: str
    fmt: str
    profile: str
    ok: bool = False
    actions: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)

    # Filled in by the dispatcher via re-inspection. Proof of the result.
    critical_before: int = -1
    critical_after: int = -1
    sensitive_before: int = -1
    sensitive_after: int = -1

    # --- helpers ---------------------------------------------------------

    def act(self, act, location, label, before="", after="", note=""):
        self.actions.append(CleanAction(Act(act), location, label, before, after, note))
        return self.actions[-1]

    def err(self, message):
        """As in Report.err(): absolute paths are stripped because the report
        goes to the user's chat."""
        from core.model import _ABS_PATH, PATH_STUB

        self.errors.append(clip(_ABS_PATH.sub(PATH_STUB, str(message)), 600))

    def count(self, *acts) -> int:
        if not acts:
            return len(self.actions)
        wanted = {Act(a) for a in acts}
        return sum(1 for a in self.actions if a.act in wanted)

    @property
    def changed(self) -> int:
        return self.count(Act.REMOVED, Act.BLANKED, Act.NORMALIZED, Act.REGENERATED)

    @property
    def clean(self) -> bool:
        """No identity or environment leak is left, according to re-inspection,
        not our own list of actions. If the dispatcher didn't verify,
        critical_after == -1 and we have no right to claim cleanliness."""
        return self.critical_after == 0

    def by_act(self) -> dict:
        out = {}
        for a in self.actions:
            out.setdefault(a.act, []).append(a)
        return out
