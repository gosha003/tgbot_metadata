"""Generating files with real Microsoft Office for acceptance testing.

Why. The synthetic fixtures (`tests/fixtures.py`) check that the code does
what was intended. They can't check that the intent is right: only files
that a real application actually saved show that. In one session such files
produced three false positives in the inspector and disproved the claim
about ODF ZIP timestamps that `clean_odf` was about to be built on. That's
why the generator lives in the repository instead of staying a one-off
script.

Where it sits. Beside the main test suite: nothing in `core/` or
`tests/test_*.py` depends on this module. Office is optional enrichment,
like `exiftool` and LibreOffice (the project rule "zero required external
binaries"), so when Word is missing `Unavailable` is raised, and the caller
has to survive that rather than crash.

Public API:
    make_word_files(dest=None) -> dict   real .doc/.rtf/.odt/.docx
    available() -> bool                  whether Word.Application is registered
    Unavailable                          the "Office unavailable" exception

CLI:
    PYTHONIOENCODING=utf-8 python -m tests.real_office [directory]

PRIVACY. The created files carry the Word user name of this machine: a real
person's name, because that's exactly what Word writes into `dc:creator`.
That's not an oversight but exactly what needs checking. Two prohibitions
follow from it: the default directory is in `%TEMP%`, not in the repository,
and the contents of findings are not printed to logs or reports.

SIDE EFFECTS. The script temporarily changes two settings of the installed
Word (the user name and the warning about saving a file with tracked
changes) and restores them in a finally block. If the process is killed
between the change and the restore, the settings stay changed.

Dependencies: stdlib only plus `powershell`, which every Windows has.
pywin32 is deliberately not used: it would be a new required dependency for
the sake of a helper module.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

__all__ = ["make_word_files", "available", "Unavailable", "WORD_FILES"]


class Unavailable(RuntimeError):
    """Office is unavailable on this machine or the script failed."""


# What make_word_files creates: file name -> Word format code (WdSaveFormat).
# wdFormatDocument97=0, wdFormatRTF=6, wdFormatXMLDocument=12,
# wdFormatOpenDocumentText=23.
WORD_FILES = (
    ("word_real.doc", 0),
    ("word_real.rtf", 6),
    ("word_real.docx", 12),
    ("word_real.odt", 23),
)

# The values we ask Word to write. The Cyrillic here isn't decoration: the
# Unicode section of custom properties (MS-OLEPS) and the RTF encoding
# (\'hh vs \uN) can't be tested with Latin text.
_TITLE = "Отчёт за 3 квартал"
_SUBJECT = "Внутренний документ"
_COMPANY = "ООО Ромашка-Тест"
_MANAGER = "Сидоров Сергей Петрович"
_KEYWORDS = "отчёт; тест; метаданные"
_PROP_1 = ("Согласовано", "Кузнецова Анастасия Викторовна")
_PROP_2 = ("Отдел", "Бухгалтерия, каб. 312")
_REVIEWER_1 = "Кузнецова Анастасия Викторовна"
_REVIEWER_2 = "Громов Дмитрий Алексеевич"
_COMMENT = "Проверить источник данных"

# Three tricks without which the script doesn't work. Each cost a separate
# debugging session, so they're described here and not only in the code:
#
# 1. PowerShell can't index a parameterized COM property:
#    $doc.BuiltInDocumentProperties.Item("Title") returns $null. InvokeMember
#    is needed. The same goes for CustomDocumentProperties.Add and SaveAs2:
#    without InvokeMember Word treats the file name as unset and opens the
#    "Save As" dialog, which with Visible=$false is invisible and modal, so
#    the process hangs forever.
# 2. Word warns when saving a file with tracked changes and comments
#    (Options.WarnBeforeSavingPrintingSendingMarkup). The same invisible hang.
# 3. The script file must be UTF-8 WITH a BOM: PowerShell 5.1 reads a .ps1
#    without a BOM as cp1251 and mangles Cyrillic into a syntax error.
_PS_TEMPLATE = r"""
$ErrorActionPreference = "Stop"
$out = "{out}"
$logf = "{log}"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Set-Content -Path $logf -Value "start" -Encoding UTF8
function L($m) {{ Add-Content -Path $logf -Value $m -Encoding UTF8 }}

function Set-Prop($coll, $name, $value) {{
    $p = [System.__ComObject].InvokeMember("Item", "GetProperty", $null, $coll, @($name))
    [void][System.__ComObject].InvokeMember("Value", "SetProperty", $null, $p, @($value))
}}
function Add-Prop($coll, $name, $value) {{
    [void][System.__ComObject].InvokeMember(
        "Add", "InvokeMethod", $null, $coll, @($name, $false, 4, $value))
}}

$w = New-Object -ComObject Word.Application
$w.Visible = $false
$w.DisplayAlerts = 0
$origUser = $w.UserName
$origInit = $w.UserInitials
$origWarn = $w.Options.WarnBeforeSavingPrintingSendingMarkup
$w.Options.WarnBeforeSavingPrintingSendingMarkup = $false
$w.Options.DoNotPromptForConvert = $true

try {{
    $doc = $w.Documents.Add()
    # Without text Word writes neither statistics, nor rsid, nor the editing time.
    $doc.Content.Text = "{body}"

    $bp = $doc.BuiltInDocumentProperties
    Set-Prop $bp "Title" "{title}"
    Set-Prop $bp "Subject" "{subject}"
    Set-Prop $bp "Company" "{company}"
    Set-Prop $bp "Manager" "{manager}"
    Set-Prop $bp "Keywords" "{keywords}"

    $cp = $doc.CustomDocumentProperties
    Add-Prop $cp "{p1name}" "{p1value}"
    Add-Prop $cp "{p2name}" "{p2value}"

    # Two reviewers: their names land in SttbfRMark in .doc, in w:ins/@w:author
    # in .docx and in \*\revtbl in RTF. One isn't enough: a one-entry table
    # doesn't test walking the table.
    $doc.TrackRevisions = $true
    $w.UserName = "{rev1}"
    $w.UserInitials = "AA"
    $doc.Range($doc.Content.End - 1, $doc.Content.End - 1).InsertAfter(" Pravka odin.")
    $w.UserName = "{rev2}"
    $w.UserInitials = "BB"
    $doc.Range($doc.Content.End - 1, $doc.Content.End - 1).InsertAfter(" Pravka dva.")
    $doc.Comments.Add($doc.Range(0, 10), "{comment}") | Out-Null
    $doc.TrackRevisions = $false
    L ("revisions=" + $doc.Revisions.Count + " comments=" + $doc.Comments.Count)

    foreach ($pair in @({saves})) {{
        $p = [string](Join-Path $out $pair[0])
        $fmt = [int]$pair[1]
        [void][System.__ComObject].InvokeMember(
            "SaveAs2", "InvokeMethod", $null, $doc, @($p, $fmt))
        L ("saved " + $pair[0] + " " + (Get-Item $p).Length)
    }}
    $doc.Close(0)
    L "OK"
}}
catch {{
    L ("FAIL " + $_.Exception.Message)
}}
finally {{
    try {{
        $w.UserName = $origUser
        $w.UserInitials = $origInit
        $w.Options.WarnBeforeSavingPrintingSendingMarkup = $origWarn
        L "restored"
    }} catch {{ L "FAIL restore" }}
    try {{ $w.Quit() }} catch {{ }}
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($w)
}}
"""


def available() -> bool:
    """Whether Word.Application is registered on this system.

    Returns: True if the Word COM class exists. Checked via the registry, Word
    isn't started: starting it costs seconds and leaves a process behind.
    """
    try:
        import winreg
    except ImportError:
        return False
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Classes\Word.Application\CurVer"):
                return True
        except OSError:
            continue
    return False


def make_word_files(dest: str | None = None, timeout: int = 180) -> dict:
    """Create four files with real Word: .doc, .rtf, .docx, .odt.

    dest: the directory for the files. None -> a new directory in %TEMP%. The
        directory is created if needed; it must not be put in the repository
        (the files carry the Word user name of this machine).
    timeout: how many seconds to wait for Word. More than a minute is needed
        because the first Office start after a reboot is noticeably slower.

    Returns: a dict "file name -> absolute path". It holds exactly what was
        actually created and is non-empty.

    Raises: Unavailable if Word isn't registered, powershell didn't start, the
        script failed, or it created no files. The exception text contains only
        the script's diagnostics; there are no names or contents of the created
        files in it.

    Side effects: starts and closes Word; while running, changes the Word user
        name and the warning about saving a file with tracked changes, then
        restores them. Writes files into dest.
    """
    if not available():
        raise Unavailable("Word.Application is not registered: Office is not installed")
    if dest is None:
        dest = tempfile.mkdtemp(prefix="tgm_real_")
    os.makedirs(dest, exist_ok=True)

    work = tempfile.mkdtemp(prefix="tgm_ps_")
    script = os.path.join(work, "make_word.ps1")
    log = os.path.join(work, "make_word.log")
    saves = ", ".join("@(\"%s\", %d)" % (n, f) for n, f in WORD_FILES)
    body = ("Kvartalnyi otchet po proektu`r`n"
            "Pervyi razdel: tekst nuzhen, chtoby Word zapisal statistiku i rsid.`r`n"
            "Vtoroi razdel: eshche nemnogo teksta dlya obyema.")
    text = _PS_TEMPLATE.format(
        out=dest.replace('"', ''), log=log.replace('"', ''), body=body,
        title=_TITLE, subject=_SUBJECT, company=_COMPANY, manager=_MANAGER,
        keywords=_KEYWORDS, p1name=_PROP_1[0], p1value=_PROP_1[1],
        p2name=_PROP_2[0], p2value=_PROP_2[1], rev1=_REVIEWER_1,
        rev2=_REVIEWER_2, comment=_COMMENT, saves=saves)
    # utf-8-sig: the BOM is required, see trick 3 above.
    with open(script, "w", encoding="utf-8-sig") as fh:
        fh.write(text)

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            timeout=timeout, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise Unavailable("powershell not found: %s" % type(exc).__name__) from exc
    except subprocess.TimeoutExpired as exc:
        # A hang almost always means an invisible modal Word dialog.
        raise Unavailable(
            "Word didn't respond within %d s: an invisible dialog is probably open" % timeout
        ) from exc

    lines = []
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    made = {}
    for name, _fmt in WORD_FILES:
        p = os.path.join(dest, name)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            made[name] = p
    if not made:
        bad = next((ln for ln in lines if ln.startswith("FAIL")), "")
        raise Unavailable("Word created no files. %s" % (bad or "; ".join(lines[-3:])))
    return made


def _demo():
    """Self-test: create the files and show what the inspector sees in them.

    Only numbers, labels and locations are printed: the values from the files
    are the machine owner's real personal data and must not be printed.
    """
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.inspect import inspect_file

    dest = sys.argv[1] if len(sys.argv) > 1 else None
    if not available():
        print("Word is not installed -- acceptance testing on real files skipped.")
        print("This is not a failure: Office is optional for the project.")
        return 0
    try:
        made = make_word_files(dest)
    except Unavailable as exc:
        print("failed: %s" % exc)
        return 1

    print("files created: %d" % len(made))
    ok = True
    for name in sorted(made):
        rep = inspect_file(made[name])
        risks = {}
        for f in rep.findings:
            risks[f.risk.value] = risks.get(f.risk.value, 0) + 1
        print("  %-16s fmt=%-5s findings %3d (%s), signals: %s, errors %d"
              % (name, rep.fmt, len(rep.findings),
                 ", ".join("%s %d" % kv for kv in sorted(risks.items())),
                 ",".join(sorted({s.kind for s in rep.signals})) or "none",
                 len(rep.errors)))
        if rep.errors:
            ok = False
            print("     PARSE ERRORS: %d -- a real file must parse without errors" % len(rep.errors))
        if not rep.findings:
            ok = False
            print("     NOT A SINGLE FINDING -- for a file from Office that's an inspector defect")
        if name == "word_real.doc":
            marks = [f.value for f in rep.findings if f.label == "Revision author (SttbfRMark)"]
            # Two reviewers are planted by the script; the first table entry is the
            # service entry "Unknown", and it must not be passed off as a person.
            if len(marks) == 2 and "Unknown" not in marks:
                print("     SttbfRMark: 2 authors, the service entry dropped")
            else:
                ok = False
                print("     SttbfRMark: expected 2 authors without Unknown, got %d"
                      % len(marks))
    print("directory: %s" % (dest or "in %TEMP%, the path is not printed"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_demo())
