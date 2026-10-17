# tgbot_metadata

A Telegram bot for inspecting and cleaning document metadata (docx/xlsx/pptx,
PDF, images, legacy .doc/.xls, ODF, RTF). It runs locally on Windows, for the
owner only. Priority in a conflict: **undetectable cleaning matters more than
exact layout preservation** (the Stealth profile is the default).

See [AGENTS.md](AGENTS.md) for the reading order, the checks to run first, and
the rules that break the product when violated.

## Code rules

- Inspectors never raise: each parsing section gets its own `try/except`, and
  the problem is reported through `Report.err()`. We receive broken and
  deliberately malformed files.
- The inspection phase is strictly read-only: the input file is never modified
  (the tests check this by comparing sha256 before and after).
- Zero required external binaries. exiftool / LibreOffice / Ghostscript are
  optional enrichment only: if they are missing, we work without them.
- Values from files are untrusted: pass them through `model.clip()`, never into
  a shell, and through `html.escape` when rendering HTML.
- Privacy: don't log file names, their contents, or the contents of findings.
  This is a privacy tool; logs here are themselves the leak.
- Code, comments, docs, the CLI and the tests are in English.

## Languages (i18n)

The bot is bilingual: Russian is the default language of its replies, English is
available. Everything lives in `core/i18n.py` and `core/i18n_ru.py`.

- Wrap every user-visible string in `tr()`. Module-level display constants are
  marked with `N_()` and wrapped in `tr()` at every place they are shown.
- Never evaluate `tr()` at import time: that freezes the language.
- Add the Russian version of each new string to `core/i18n_ru.py` (`RU =
  {english: russian}`), with the same placeholders.
- Use `both()` for logic that must accept either language and `plural()` for
  Russian plural forms.
- **Only presentation changes with the language, never a verdict.** Logic must
  not compare a translated string with an untranslated one; the oracle's presence
  markers (present/absent, yes/no, empty, not set, on/off) are matched in both
  languages.
- `tests/test_i18n.py` enforces this: every `tr()`/`N_()` literal has a Russian
  version with matching placeholders, `tr()` is never evaluated at import time,
  and every fixture gets identical inspection and cleaning verdicts in English and
  Russian. The CLI and the tests never set a language, so they run in English.

## Testing on Windows

Always `PYTHONIOENCODING=utf-8 python ...`, otherwise the console mangles
Cyrillic and it looks like a bug in the code. This is a cp866 artifact, not a
defect.

`PYTHONIOENCODING=utf-8 python -m tests.run_all` runs every test suite and
module self-test, each in its own process.

## Contract

`core/model.py` (Risk / Finding / Signal / Report) and `core/sniff.py` are the
shared contract of all inspectors. Change them only deliberately and as a whole:
all the other code is written against it.
