# -*- coding: utf-8 -*-
"""Tests for the bot's two languages (core/i18n.py).

The language must change presentation only, never a verdict. The oracle compares
values across two reports, and a value written in one language must not be
compared with a value written in the other: a stray English marker in a Russian
run would turn a clean file into "NOT CLEAN". So the main check here runs every
fixture through inspection and cleaning in both languages and requires the
structural results, and the verdict the user is shown, to be identical.

The static checks guard the translation table itself: every string passed to
tr() / N_() has a Russian version with the same placeholders, and tr() is never
evaluated at import time (it would freeze the language of whoever imported first).

Run (PYTHONIOENCODING=utf-8 is required on Windows):

    PYTHONIOENCODING=utf-8 python -m tests.test_i18n
"""

from __future__ import annotations

import ast
import collections
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import cleanreport, report  # noqa: E402
from core.clean import clean_file  # noqa: E402
from core.cleanmodel import CleanResult, Profile  # noqa: E402
from core.i18n import RU_PLURAL, tr, use_lang  # noqa: E402
from core.i18n_ru import RU  # noqa: E402
from core.inspect import inspect_file  # noqa: E402

from tests import fixtures  # noqa: E402

_RESULTS = []

# Placeholders of %-formatting and str.format fields; %% is a literal, and braces
# around free text ("{part name: bytes}") are not a field.
_PH = re.compile(r"%(?:\([^)]*\))?[-#0 +]*\d*(?:\.\d+)?[sdirfgxXeEc%]"
                 r"|\{(?:[A-Za-z_]\w*|\d*)(?:![rsa])?(?::[^{}]*)?\}")

# Every builder in tests/fixtures.py, so a new one is covered without being listed here.
_FIXTURES = tuple(sorted(n for n in dir(fixtures) if n.startswith("make_")))

# Cleaning paths the default run never takes: a whitelist that leaves survivors
# behind, and a run without re-inspection.
_CONFIGS = (
    ("make_docx", {"keep": {"dc:creator", "Company"}}),
    ("make_xlsx", {"keep": {"dc:creator", "Company"}}),
    ("make_pdf", {"keep": {"/Author"}}),
    ("make_jpeg", {"keep": {"icc"}}),
    ("make_docx", {"verify": False}),
    ("make_pdf", {"verify": False}),
)


def _check(name, condition, detail=""):
    _RESULTS.append((name, bool(condition), detail))
    print("  [%s] %s%s" % ("ok" if condition else "FAIL", name,
                           "" if condition else "  -- %s" % (str(detail)[:300],)))


def _sources():
    files = [os.path.join(ROOT, "bot.py")]
    core = os.path.join(ROOT, "core")
    files += [os.path.join(core, f) for f in sorted(os.listdir(core))
              if f.endswith(".py") and f != "i18n_ru.py"]
    return files


def _calls(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names:
            yield node


def _placeholders(text):
    return [p for p in _PH.findall(text) if p != "%%"]


# ---------------------------------------------------------------------------
# the translation table
# ---------------------------------------------------------------------------


def test_01_every_source_string_has_russian():
    missing, mismatched, total = [], [], 0
    for path in _sources():
        tree = ast.parse(open(path, encoding="utf-8").read())
        for call in _calls(tree, ("tr", "N_")):
            arg = call.args[0] if call.args else None
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                continue
            total += 1
            where = "%s:%d" % (os.path.relpath(path, ROOT), call.lineno)
            if arg.value not in RU:
                missing.append((where, arg.value[:80]))
            elif _placeholders(arg.value) != _placeholders(RU[arg.value]):
                mismatched.append((where, arg.value[:80]))
    print("  tr()/N_() literals: %d, RU entries: %d" % (total, len(RU)))
    _check("every tr()/N_() literal has a Russian version", not missing, missing[:10])
    _check("placeholders match in order", not mismatched, mismatched[:10])


def test_02_plural_words_have_russian_forms():
    missing = []
    for path in _sources():
        tree = ast.parse(open(path, encoding="utf-8").read())
        for call in _calls(tree, ("plural", "_plural")):
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
                if call.args[1].value not in RU_PLURAL:
                    missing.append("%s:%d %r" % (os.path.basename(path), call.lineno,
                                                 call.args[1].value))
    _check("plural() words have three Russian forms", not missing, missing)


def test_03_tr_is_never_evaluated_at_import():
    """tr() at module or class level, or in a default argument, runs once at import
    and freezes whatever language was active then. Constants use N_() instead."""
    bad = []
    for path in _sources():
        tree = ast.parse(open(path, encoding="utf-8").read())
        runtime = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                body = node.body if isinstance(node.body, list) else [node.body]
                for stmt in body:
                    for sub in ast.walk(stmt):
                        runtime.add(id(sub))
        for call in _calls(tree, ("tr",)):
            if id(call) not in runtime:
                bad.append("%s:%d" % (os.path.relpath(path, ROOT), call.lineno))
    _check("no tr() outside function bodies", not bad, bad[:10])


# ---------------------------------------------------------------------------
# the language never changes a verdict
# ---------------------------------------------------------------------------


def _inspect_signature(rep):
    findings = collections.Counter((f.risk.value, f.removable, f.empty) for f in rep.findings)
    signals = sorted((s.kind, s.confidence) for s in rep.signals)
    return (rep.fmt, len(rep.findings), sorted(findings.items()), signals, len(rep.errors),
            rep.critical, rep.sensitive)


def _clean_signature(res):
    acts = collections.Counter(getattr(a.act, "value", a.act) for a in res.actions)
    # The verdict the user is shown: its kind and mark, not its wording.
    v = cleanreport._verdict(cleanreport._snap(res))
    return (v.kind, v.mark, bool(v.short), res.ok, getattr(res, "verified", None), res.clean,
            len(getattr(res, "survived", [])), len(getattr(res, "residual", [])),
            len(getattr(res, "gaps", [])),
            res.critical_before, res.critical_after, res.sensitive_before, res.sensitive_after,
            getattr(res, "planned_phase", 0), sorted(acts.items()), len(res.errors),
            # new_signals holds (kind, detail); the detail is display text.
            sorted({kind for kind, _ in getattr(res, "new_signals", []) or []}),
            getattr(res, "detect", None))


def _fixture_paths(name, tmp):
    """Fixture builders return (path, expected); make_broken returns {name: path}."""
    made = getattr(fixtures, name)(tmp)
    if isinstance(made, dict):
        return sorted(made.items())
    return [(name, made[0])]


def test_10_inspection_is_language_independent():
    tmp = tempfile.mkdtemp(prefix="i18n_")
    try:
        for fixture in _FIXTURES:
            for name, path in _fixture_paths(fixture, tmp):
                with use_lang("en"):
                    en = _inspect_signature(inspect_file(path))
                with use_lang("ru"):
                    ru = _inspect_signature(inspect_file(path))
                _check("%s: inspection identical in EN and RU" % name, en == ru, (en, ru))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _clean_signatures(src, dst_base, prof, **kwargs):
    sigs = {}
    for lang in ("en", "ru"):
        with use_lang(lang):
            sigs[lang] = _clean_signature(clean_file(src, "%s_%s.out" % (dst_base, lang), prof,
                                                     **kwargs))
    return sigs


def test_11_cleaning_verdict_is_language_independent():
    tmp = tempfile.mkdtemp(prefix="i18n_")
    try:
        for name, src in (p for f in _FIXTURES for p in _fixture_paths(f, tmp)):
            for prof in (Profile.STEALTH, Profile.PARANOID):
                sigs = _clean_signatures(src, os.path.join(tmp, "%s_%s" % (name, prof.value)), prof)
                _check("%s/%s: cleaning verdict identical in EN and RU" % (name, prof.value),
                       sigs["en"] == sigs["ru"], (sigs["en"], sigs["ru"]))
        not_clean = 0
        for fixture, kwargs in _CONFIGS:
            src = _fixture_paths(fixture, tmp)[0][1]
            label = "%s+%s" % (fixture, "+".join(sorted(kwargs)))
            for prof in (Profile.STEALTH, Profile.PARANOID):
                sigs = _clean_signatures(src, os.path.join(tmp, "%s_%s" % (label, prof.value)),
                                         prof, **kwargs)
                not_clean += sigs["en"][0] == "residual"      # the verdict kind comes first
                _check("%s/%s: cleaning verdict identical in EN and RU" % (label, prof.value),
                       sigs["en"] == sigs["ru"], (sigs["en"], sigs["ru"]))
        _check("the keep configurations leave survivors: the NOT CLEAN verdict is exercised",
               not_clean)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_12_report_verdict_ignores_note_wording():
    """The report counts entries kept per the user's whitelist by their note, which is
    display text. 3 critical findings = a regenerated /ID (2) + one kept field, so
    the remainder is explained in either language."""
    # The Russian note is swapped for a wording without the Latin word "whitelist", so
    # a match on that substring (instead of the whole note in both languages) fails here.
    key = "kept per the user's whitelist (keep)"
    saved = RU[key]
    RU[key] = "оставлено по списку пользователя (keep)"
    verdicts, texts = {}, {}
    try:
        for lang in ("en", "ru"):
            with use_lang(lang):
                res = CleanResult(src="x.pdf", dst="y.pdf", fmt="pdf", profile="stealth", ok=True)
                res.critical_before, res.critical_after = 7, 3
                res.act("regenerated", "trailer", "/ID", "aa", "bb")
                res.act("kept", "/Info", "/Author", "x", note=tr(key))
                v = cleanreport._verdict(cleanreport._snap(res))
                verdicts[lang] = (v.kind, v.mark)
                texts[lang] = "\n".join(cleanreport.render_telegram(res))
    finally:
        RU[key] = saved
    _check("kept per the whitelist: the verdict is identical in EN and RU",
           verdicts["en"] == verdicts["ru"], verdicts)
    _check("kept per the whitelist: the remainder is explained, not \"bad\" (EN and RU)",
           all(mark == "warn" for _kind, mark in verdicts.values()), verdicts)
    _check("kept per the whitelist: no \"NOT EXPLAINED\" line in either report",
           all("NOT EXPLAINED" not in t and "НЕ ОБЪЯСНЕН" not in t for t in texts.values()),
           {k: t[:200] for k, t in texts.items()})


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_20_reports_render_in_the_selected_language():
    tmp = tempfile.mkdtemp(prefix="i18n_")
    try:
        src = fixtures.make_pdf(tmp)[0]
        with use_lang("ru"):
            rep_ru = report.render_plain(inspect_file(src))
            res = clean_file(src, os.path.join(tmp, "out_ru.pdf"), Profile.STEALTH)
            clean_ru = "\n".join(cleanreport.render_telegram(res))
        with use_lang("en"):
            rep_en = report.render_plain(inspect_file(src))
            res = clean_file(src, os.path.join(tmp, "out_en.pdf"), Profile.STEALTH)
            clean_en = "\n".join(cleanreport.render_telegram(res))
        _check("RU inspection report is in Russian", "ОТЧЁТ ОБ ИНСПЕКЦИИ" in rep_ru, rep_ru[:200])
        _check("EN inspection report is in English", "METADATA INSPECTION REPORT" in rep_en,
               rep_en[:200])
        _check("RU cleaning report is in Russian", "Чистка метаданных" in clean_ru, clean_ru[:200])
        _check("EN cleaning report is in English", "Metadata cleaning" in clean_en, clean_en[:200])
        # Default language outside use_lang() is English (CLI and tests).
        plain = report.render_plain(inspect_file(src))
        _check("default language is English", "METADATA INSPECTION REPORT" in plain, plain[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        print("=== %s ===" % fn.__name__)
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- one test must not stop the rest
            _check("%s: no unhandled exception" % fn.__name__, False,
                   "%s: %s" % (type(exc).__name__, exc))
    failed = [r for r in _RESULTS if not r[1]]
    print("TOTAL: %d checks, %d failed" % (len(_RESULTS), len(failed)))
    for name, _, detail in failed:
        print("  - %s  -- %s" % (name, str(detail)[:300]))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(_run_all())
