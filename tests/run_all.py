# -*- coding: utf-8 -*-
"""Run every test suite and module self-test; exit 1 if any of them fails.

    PYTHONIOENCODING=utf-8 python -m tests.run_all

Each suite runs in its own process (the self-tests patch module globals, so one
process per suite keeps them independent). A suite fails when its process exits
with a non-zero code; the tail of its output is printed for diagnosis.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SUITES = [
    ("tests.test_inspect", ["-m", "tests.test_inspect"]),
    ("tests.test_clean", ["-m", "tests.test_clean"]),
    ("tests.test_i18n", ["-m", "tests.test_i18n"]),
    ("core.inspect", ["-m", "core.inspect", "--selftest"]),
    ("core.clean", ["-m", "core.clean", "--selftest"]),
    ("core.clean_batch", ["-m", "core.clean_batch"]),
    ("core.zipfix", ["-m", "core.zipfix"]),
    ("core.clean_ooxml", ["-m", "core.clean_ooxml"]),
    ("core.clean_pdf", ["-m", "core.clean_pdf"]),
    ("core.clean_image", ["-m", "core.clean_image"]),
    ("core.clean_odf", ["-m", "core.clean_odf"]),
    ("core.clean_rtf", ["-m", "core.clean_rtf"]),
    ("core.insp_ooxml", ["-m", "core.insp_ooxml"]),
    ("core.insp_image", ["-m", "core.insp_image"]),
    ("core.insp_legacy", ["-m", "core.insp_legacy"]),
    ("core.report", ["-m", "core.report"]),
    ("core.cleanreport", ["-m", "core.cleanreport"]),
    ("core.lint", ["-m", "core.lint"]),
    ("bot", ["bot.py", "--selftest"]),
    ("tests.real_office", ["-m", "tests.real_office"]),  # skipped without Microsoft Word
]

_TOTAL = re.compile(r"TOTAL: (\d+) checks?, (\d+) failed")


def main(argv=None) -> int:
    only = set(argv or sys.argv[1:])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    failed, checks = [], 0
    for name, args in SUITES:
        if only and name not in only:
            continue
        start = time.time()
        proc = subprocess.run([sys.executable] + args, cwd=ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        out = proc.stdout + proc.stderr
        counted = sum(int(m.group(1)) for m in _TOTAL.finditer(out))
        checks += counted
        ok = proc.returncode == 0
        print("%-20s %s  %6.1fs%s" % (name, "ok  " if ok else "FAIL", time.time() - start,
                                      "  (%d checks)" % counted if counted else ""), flush=True)
        if not ok:
            failed.append(name)
            print("\n".join("    " + line for line in out.strip().splitlines()[-25:]))
    print("=" * 60)
    print("%d suites, %d failed%s" % (len(SUITES) if not only else len(only), len(failed),
                                      "; %d counted checks" % checks if checks else ""))
    for name in failed:
        print("  - %s" % name)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
