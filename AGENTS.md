# Guidance for contributors and coding tools

Entry point for anyone working on this project, human or tool.
Read this file in full before the first edit; it is short.

## What this is

A Telegram bot and CLI for inspecting and cleaning document metadata: OOXML
(`docx`/`xlsx`/`pptx`), PDF, images, legacy OLE2 (`doc`/`xls`/`ppt`), ODF, RTF.
Local, single-user, Windows.

**Phase 0 (inspection)**, **phase 1 (PDF and image cleaning)** and
**phase 2 (OOXML cleaning)** (`core/zipfix.py` + `core/clean_ooxml.py`) are done.
RTF and ODF are cleaned in place too (`core/clean_rtf.py`, `core/clean_odf.py`).
Inspection never modifies the file. Cleaning writes to a separate file and never
touches the original. Legacy OLE2 can't be cleaned in place; the `Regenerate`
profile (`core/clean_regen.py`, phase 4) re-creates such a file through
LibreOffice when it is installed. Without it, **never pretend the file was
cleaned**: the dispatcher must refuse and name the phase number.

For OOXML the `Stealth` profile is clean but **not undetectable**: the pair of
empty `dc:creator` and `cp:lastModifiedBy` is itself a sign of cleaning. This is
a limit of the format, not a defect; the dispatcher puts such signs into
`DispatchResult.new_signals`, and the report must tell the user about it
plainly. The way out is `Regenerate` (phase 4).

The cleaning contract is `core/cleanmodel.py`. The key point: **the inspector is
the oracle for the cleaner**. The success criterion is not "zero critical
findings" but "none of the original sensitive values appears in the result". It
is checked by running `inspect_file()` again on the output file, not by trusting
the cleaner's own list of actions.

## Reading order

| File | Why |
|---|---|
| [CLAUDE.md](CLAUDE.md) | the mandatory project rules |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | the contract, extension points, pitfalls |
| [docs/METADATA.md](docs/METADATA.md) | **where exactly metadata hides, by format**: the main knowledge asset, expensive to rebuild |
| [core/cleanmodel.py](core/cleanmodel.py) | the cleaning contract: profiles, `Act`, `CleanResult`, the oracle |
| [core/i18n.py](core/i18n.py) | translation helpers: `tr`, `N_`, `both`, `plural`, `use_lang` |
| [docs/ROADMAP.md](docs/ROADMAP.md) | the phase plan, the three cleaning strategies, known gaps |
| [README.md](README.md) | installation and running |

## Check that everything is alive

```bash
PYTHONIOENCODING=utf-8 python -m tests.test_inspect
```

Expected: `TOTAL: N checks, 0 failed`. If anything fails, fix the **module**, not
the test, before starting new work.

```bash
PYTHONIOENCODING=utf-8 python -m tests.test_i18n   # translations; identical verdicts in both languages
PYTHONIOENCODING=utf-8 python -m tests.run_all     # every suite and module self-test, one process each
```

`tests.run_all` takes suite names as arguments to run a subset, for example
`python -m tests.run_all tests.test_i18n core.zipfix`. CI runs the full set on
Ubuntu and Windows.

```bash
PYTHONIOENCODING=utf-8 python -m core.inspect path/to/file.docx
```

## Rules that break the product when violated

1. **`PYTHONIOENCODING=utf-8` on every Python run.** Otherwise the Windows
   console mangles Cyrillic and it looks like a bug in the code. Don't fix a bug
   that doesn't exist.
2. **An inspector never raises.** Each parsing section gets its own
   `try/except`, and the problem is reported through `Report.err()`. We receive
   broken and deliberately malformed files.
3. **The inspection phase is strictly read-only.** The input is never modified;
   this is checked by comparing sha256 before and after.
4. **Zero required external binaries.** `exiftool`, LibreOffice and Ghostscript
   are optional enrichment only.
5. **Don't log file names, their contents, or the contents of findings.** This is
   a privacy tool; logs here are themselves the leak.
6. **Don't weaken a test to fit the current behavior.** A failing test is
   valuable information; a test fitted to a bug is harmful.
7. **The contract** (`core/model.py`, `core/sniff.py`) changes only deliberately:
   everything else is written against it.
8. **Non-trivial logic without a check is not done.** Leave at least one
   runnable check, either in `tests/test_inspect.py` or as a self-test in the
   module's `__main__`.
9. **Only presentation changes with the language, never a verdict.** Code,
   comments, docs, the CLI and tests are in English; the bot's replies are
   Russian by default and English on request. A user-visible string goes through
   `tr()` (or `N_()` at module level) and gets its Russian version in
   `core/i18n_ru.py`. Logic must not compare a translated string with an
   untranslated one; `tests/test_i18n.py` enforces this.

## What this project does not do

Removing metadata is ordinary privacy hygiene, and it is done here in full. But
**forging provenance** is falsifying a document: writing in a date before a
deadline, substituting someone else's author name, fabricating a signing time.
There is no such function in the project and there never will be. Normalizing a
file to look like a fresh one from a stock application with no user name set is
a different thing, and that is done.

Be honest about the limits: metadata is only one channel. Writing style,
token-level watermarks and server-side version history are outside the reach of
any scrubber. See the end of [docs/METADATA.md](docs/METADATA.md).

## Working in parallel

Split the files **before** starting work and don't touch files assigned to
others. If several people or tools need the same file (usually
`tests/test_inspect.py`), let one of them edit it, after all the others, and have
the rest hand back a list of "what to add, and on what data".

## The most expensive knowledge in the project

Four facts that almost every tool misses; details in
[docs/METADATA.md](docs/METADATA.md):

- **ZIP timestamps.** Word sets every entry to `1980-01-01`; libraries write the
  real save time. A file that claims Microsoft Office but has fresh entry
  timestamps was made by a library, and the time leaked through the container
  even if `core.xml` was cleaned perfectly.
- **Incremental updates in PDF.** The file physically contains earlier versions
  of pages and the text under a redaction box.
- **The printer name in `printerSettings*.bin`.** A DEVMODE structure whose
  `dmDeviceName` is 32 WCHAR, which is the **first 64 bytes** of UTF-16LE (not
  32: reading 32 truncates the name at the 16th character). It often contains a
  host name or an office location. Further on, the same structure holds the
  paper size (a geolocation hint: Letter versus A4), and the driver's private
  tail holds the UNC path to the print server.
- **Empty metadata is itself a sign of processing.** That is why the default
  strategy doesn't blank fields but brings them to the state a stock
  application would write.
