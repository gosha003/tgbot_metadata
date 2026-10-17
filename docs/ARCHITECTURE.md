# Architecture

This document is for whoever extends the project. It describes the contract, the
extension points, and the traps that have already caught us.

## Data flow

```
Telegram ──► bot.py ──┐
                      ├──► core/inspect.py : inspect_file(path) ──► Report ──► core/report.py
CLI ──► python -m core.inspect ──┘              │                                   │
                                               │                         render_telegram → list[str]
                                   sniff(path) → format                  render_plain    → str
                                   family(fmt) → which inspector         summary_line    → str
                                               │
                        ┌──────────────┬───────┴────────┬──────────────┐
                   insp_ooxml       insp_pdf        insp_image     insp_legacy
                 docx/xlsx/pptx       pdf        jpeg/png/tiff/...  doc/xls/odf/rtf
```

The key point: inspectors **know nothing about each other**. Recursion (an image
inside a docx, an attachment inside a PDF, an OLE object in `embeddings/`) is the
job of the dispatcher, `core/inspect.py`, not of an inspector. An inspector only
flags that a nested object is present.

## The contract: `core/model.py`

The single point through which the inspectors agree with each other. Change it
deliberately and as a whole: everything else is written against it.

### `Risk`

| Value | What goes here | Cleaned |
|---|---|---|
| `IDENTITY` | full names, emails, tenant GUIDs, camera serial numbers, signers | yes |
| `ENVIRONMENT` | local paths, hostnames, printer names, database connection strings | yes |
| `PROVENANCE` | application, version, dates, edit time, generator, fonts | yes |
| `STRUCTURAL` | language, accessibility tags, ICC profile, table of contents, page format | **usually kept** |

A common mistake is filing an email or a name under `PROVENANCE` because the field
is called `CreatorTool`. The value decides, not the field name. The opposite
mistake is just as real: `CreatorTool` holds an application name, so it is
`PROVENANCE` even though it contains the substring `creator`; a defect was caught
on exactly this.

### `Finding`

```python
Finding(risk, location, label, value, note="", removable=True)
```

- `location` is the machine-readable place: `docProps/core.xml`, `/Info`,
  `APP1:Exif`, `xl/pivotCache/pivotCacheDefinition1.xml`. When recursing, the
  dispatcher adds a prefix: `word/media/image1.jpeg > APP1:Exif`.
- `value` goes through `clip()`, which strips control characters and caps the
  length. Values are untrusted.
- **An empty value is a fact, not missing data.** An empty `dc:creator` means
  either a previous cleaning or a stock application with no user name. Use
  `add()` to show an empty field and `add_nonempty()` to stay silent about it.
- `removable=False` means it cannot be removed, or not without loss: JPEG
  quantization tables, the revision history in a `.doc`, a digital signature.

### `Signal`

```python
Signal(kind, detail, confidence="medium")   # confidence: low | medium | high
```

A signal is a **conclusion**, not a raw value. The kinds:

| `kind` | Meaning |
|---|---|
| `producer` | what made the file: only the application or library that wrote it |
| `ai` | signs of generation by a library or an AI |
| `dating` | dating and geolocation from indirect evidence: a font, a paper size |
| `scrubbed` | signs of a previous cleaning |
| `inconsistent` | the metadata contradicts itself |
| `hazard` | active content: macros, JS, incremental revisions |

`dating` is deliberately separate from `producer`. The Calibri font dates a
document but is not an application; while it was a `producer`, the summary
reported "PDF, made with Calibri".

`Report.signal()` **merges duplicates** by the `(kind, detail)` pair, keeping the
highest confidence. Inspectors emit signals inside loops over pages and fonts;
without the merge, one conclusion landed in the report dozens of times and pushed
out the rest.

### `Report`

```python
Report(path, fmt, size=0, findings=[], signals=[], errors=[], parts=[])
```

Helpers: `add()`, `add_nonempty()`, `signal()`, `err()`, `by_risk()`,
`count(*risks)`.

There are two metrics, and they must not be confused:

| | What | Where it is used |
|---|---|---|
| `sensitive` | IDENTITY + ENVIRONMENT + PROVENANCE | amount of work for the cleaning phase; report header |
| `critical` | IDENTITY + ENVIRONMENT | how bad things are; summary line |

A real PDF can have `sensitive=137` with `critical=7`: the rest is a per-page font
list and the format version. Calling all 137 critical would drown the seven real
leaks.

`parts` is the container's composition (ZIP entries with their DOS timestamps, OLE
streams). Inspectors fill it; the report does not render it yet. It is the input
for phase 2, which needs a byte-exact repack.

### `err()` and path scrubbing

`Report.err()` strips absolute paths from the error text. A parser exception
message contains the full path to the file **on our machine**, and the report goes
to the user's chat; otherwise a privacy tool would leak exactly the `ENVIRONMENT`
category it looks for in other people's files.

Scrubbing lives **only in `err()` and deliberately not in `add()`**: a path found
inside someone else's file is a legitimate finding and must be shown. Strings that
look the same, with opposite meanings.

## Format detection: `core/sniff.py`

```python
sniff(path) -> str                 # format key, NEVER raises
unreadable_reason(path) -> tuple   # ("cloud"|"missing"|"dir"|"denied"|"other", errno)
pretty(fmt) -> str                 # human-readable name
family(fmt) -> str                 # which inspector handles it
```

The format is determined **by magic bytes, not by extension**. The extension lies:
a `.docx` may well be a renamed PDF.

### Three different kinds of "don't know"

They must not be mixed up, because the user needs a different action for each:

| `fmt` | Meaning | What to say |
|---|---|---|
| `unknown` | the file **was read**, but the signature is not recognized | deep inspection is impossible |
| `unknown`, `size == 0` | the file was read and is empty | possibly an incomplete download |
| `unreadable` | the file **was not read** | the reason from `unreadable_reason()` |

For `unreadable`, the reason `"cloud"` means a cloud provider's placeholder. The
report must say so plainly and give an action ("open the file in Explorer or
choose Always keep on this device"). Such a file used to be reported as "format not
recognized", a false diagnosis that would send the user looking for corruption that
does not exist.

`errno 22` on its own does **not** prove it is the cloud: an invalid file name
fails the same way. The sign of a cloud placeholder is the Windows attribute
flags, not the errno.

### Test seams

CI has no cloud placeholders and no files without permissions. So
`sniff._read_head` and `sniff._win_attrs` are intentional seams: a test replaces
them as module attributes and restores them in `finally`. This is not thread-safe,
which is fine for a single-threaded self-test.

### OLE2 is detected by the top level

`_sniff_ole()` compares stream names at the **top level only** (`len(parts) == 1`
in `ole.listdir()`). A flat search over the whole tree is a defect: a nested OLE
object lives in a storage (`ObjectPool/_1396712391/WordDocument`), and an Excel
workbook with an embedded Word document was detected as `.doc`. Parsing with the
wrong inspector is more dangerous than not parsing at all: the output is a file
that looks processed but leaks.

## A report must not lie

Three places where this has already been caught:

1. The footer said "file read and not modified" even for `fmt == "unreadable"`.
   Such a file is exactly the one that was **not** read. We always state that the
   file is unmodified, because the inspection phase never writes to the file,
   whatever the outcome.
2. An empty result with non-empty `errors` was reported as "no metadata found, so
   the file has already been cleaned". An unread file was passed off as safe.
3. One broken finding took down the whole report: a single outer `try` instead of
   per-section degradation lost the header, the conclusions, and the real PII
   findings.

## Languages (i18n)

The bot is bilingual. Russian is the default language of the bot's replies, and
English is available. Code, comments, docs, the CLI and the tests are in English.

### Pieces

| Where | What |
|---|---|
| `core/i18n.py` | the machinery: `tr()`, `N_()`, `both()`, `plural()`, `use_lang()` |
| `core/i18n_ru.py` | the Russian table `RU = {english: russian}` |
| `tests/test_i18n.py` | enforces the rules below |
| `bot.py` | the `/lang` command, per-user storage of the choice |

The API in `core/i18n.py`:

| Function | What it does |
|---|---|
| `tr(text)` | returns the current language's version of an English source string |
| `N_(text)` | marks a module-level constant for translation without translating it; the constant is passed through `tr()` where it is shown |
| `both(text)` | returns `(english, russian)`, for logic that must accept either |
| `plural(n, one, many)` | picks the plural form; Russian has three forms, driven by `RU_PLURAL` (keyed by the English singular) |
| `use_lang(lang)` | context manager that sets the current language; `get_lang()` reads it |

The current language is a `contextvars.ContextVar`, set per request with
`use_lang(lang)`. `asyncio.to_thread` copies the context into the worker thread
that runs inspection and cleaning, so the language follows the request without
being passed through every call. The CLI and the tests never set it, so they run
in English.

### Rules

- **Only presentation changes with the language, never a verdict.** The oracle
  compares values across two reports; a value in one language must never be
  compared with a value in the other.
- **Logic must not compare a translated string with an untranslated one.** Where
  logic has to match text, it matches both languages: the oracle's presence markers
  (present / absent / yes / no / empty / not set / on / off) are matched via
  `both()`.
- **`tr()` is never evaluated at import time.** That would freeze the language of
  whoever imported the module first. Module-level constants use `N_()` and are
  translated with `tr()` where they are shown.
- **The Russian version has the same placeholders as the English one** (`%s`, `%d`,
  `{name}`), in the same order.

### Adding a user-visible string

1. Write the English string and wrap it in `tr()`; at module level, in `N_()`:

   ```python
   from core.i18n import N_, tr

   _UNSUPPORTED = N_("Format %s is not supported")      # module-level constant

   report.err(tr("Archive is encrypted: %s") % name)    # inside a function
   report.err(tr(_UNSUPPORTED) % fmt)                   # translated where shown
   ```

2. Add the Russian version to `core/i18n_ru.py`, keyed by the exact English text:

   ```python
   "Archive is encrypted: %s": "Архив зашифрован: %s",
   ```

3. For a counted noun, use `plural(n, "finding", "findings")` and, for a new word,
   add its three Russian forms to `RU_PLURAL` in `core/i18n.py`.
4. Run `PYTHONIOENCODING=utf-8 python -m tests.test_i18n`.

A string missing from `RU` does not crash anything: the user just sees English in
the Russian reply. That is why the test exists.

### How the tests enforce it

`tests/test_i18n.py` checks that:

- every `tr()` / `N_()` literal has a Russian version with matching placeholders;
- every plural word has its Russian forms;
- `tr()` is never evaluated at import time;
- every fixture gets identical inspection and cleaning verdicts in English and in
  Russian: the language must not change a verdict.

### In the bot

- `/lang` shows two buttons, "Русский" and "English"; `/lang ru` and `/lang en`
  set the language directly.
- The choice is remembered per user in a local file `.bot_lang.json` next to
  `bot.py`. The file is git-ignored.
- The default for users who have not chosen comes from `BOT_LANG` in `.env` (`ru`
  or `en`; default `ru`).
- Command descriptions are registered in both languages, so Telegram shows them in
  the language of the user's client.

## Adding a format inspector

1. `core/sniff.py`: add the format key to `NAMES` (the display name, shown through
   `tr()`) and to `FAMILY` (the family it belongs to), plus magic-byte detection
   in `sniff()`. **The extension is not used**: it lies, and a `.docx` may well be
   a renamed PDF.
2. `core/insp_<format>.py`: a function `inspect_<format>(path, fmt=...) -> Report`.
3. `core/inspect.py`: a route in the dispatcher by `family()`.
4. `tests/fixtures.py`: `make_<format>(dir) -> (path, expected_values)`.
5. `tests/test_inspect.py`: a test through `_check_fixture()`.
6. Wrap every user-visible string in `tr()` (module-level constants in `N_()`) and
   add its Russian version to `core/i18n_ru.py` with the same placeholders; see
   [Languages (i18n)](#languages-i18n). `tests/test_i18n.py` fails otherwise.

Required properties of an inspector:

- **Never raises.** Every parsing section gets its own `try/except`, and a problem
  goes through `report.err()`. We are sent broken and deliberately malformed files.
- **Read-only.** The input is never modified; this is checked by comparing sha256
  before and after on every fixture.
- **Bomb protection.** Check the uncompressed size and the compression ratio before
  decompressing, and limit the number of parts processed and the nesting depth.
  There is no sandbox.
- **Zero mandatory external binaries.** `exiftool` is optional enrichment only: if
  it is not on the machine, we work without it.
- **XXE protection.** Untrusted XML is parsed with `resolve_entities=False`,
  `no_network=True`, and no DTD expansion.

## Adding a format cleaner

The cleaning contract is [core/cleanmodel.py](../core/cleanmodel.py). There are more
steps than it seems, and **skipping any of 3-5 does not break the tests but
silently disables cleaning in the bot**: that is exactly how phase 2 reached the bot
non-working. The core cleaned `.docx`, while the bot answered "cleaning will appear
in phase 2". The consistency check is
`tests/test_clean.py::test_14_cleanable_matches_cleaners`.

1. `core/clean_<format>.py`: `clean_<format>(src, dst, profile, keep, ...) ->
   CleanResult`. It raises nothing; on failure it returns `ok=False` and no file is
   produced.
2. `core/clean.py`: an entry in `_CLEANERS` **by family** (`family()`, not by
   format), and remove the family from `_PLANNED`, otherwise the dispatcher keeps
   refusing.
3. `core/cleanreport.py`: add the formats to `CLEANABLE` and remove them from
   `_PHASE`. This is the gate for the buttons in the bot.
4. `bot.py`: the extension in `_EXT` and the generic word in `_STEM`; otherwise the
   result goes to the user as `image_ab12cd.bin`.
5. `tests/fixtures.py` + `tests/test_clean.py`: a fixture and checks. The fixture
   must **carry the values the cleaner is supposed to remove**: a "the part is gone"
   check on a file that never had that part is vacuous and guards nothing.
6. Wrap every user-visible string (cleaning report lines, error messages) in `tr()`,
   module-level constants in `N_()`, and add the Russian versions to
   `core/i18n_ru.py`; see [Languages (i18n)](#languages-i18n).
   `tests/test_i18n.py` also runs every fixture through cleaning in both languages
   and requires identical verdicts.

Required properties of a cleaner:

- **The oracle is the inspector, not the cleaner's own list of actions.** The
  success criterion is not "zero critical" but "no original sensitive value appears
  in the result"; the dispatcher checks it by running `inspect_file()` again on the
  output file.
- **The input is never modified.** The result is written to a separate file, and the
  input's sha256 is compared before and after.
- **A partially cleaned file is worse than a refusal.** If part of what had to be
  cleaned could not be, return `ok=False` and no file. Handing over a file the user
  believes is clean when it is not is the worst outcome for this project.
- **The output must open with its own parser**, and the cleaner checks this itself.
- **What cannot be removed is marked `Act.UNREMOVABLE`**, not hushed up.
- **Edit, don't rebuild.** Rebuilding the container or a round trip through an XML
  serializer changes the file's profile and itself becomes a fingerprint of
  processing, even when the metadata is cleaned perfectly.

## Traps that have already caught us

**The Windows console mangles Cyrillic.** Always use
`PYTHONIOENCODING=utf-8 python ...`. Otherwise the output turns into mojibake and
looks like a bug in the code. It is a cp866 artifact, not a defect.

**ZIP timestamps give away the real save time.** Word, Excel and PowerPoint give
every ZIP entry `1980-01-01 00:00`. Libraries (`openpyxl`, `python-docx`) use the
real time. So a file that claims a Microsoft product in `app.xml` but has real,
recent entry timestamps was made by a library, and the true save time leaked through
the container even if `core.xml` is perfectly cleaned. This is the strongest `ai`
signal, and no tool that looks only at `docProps` can see it.

**OneDrive cloud placeholders.** `os.path.exists()` returns `True`,
`os.path.getsize()` returns the real size, and `open().read()` fails with
`OSError errno 22`. The flag is `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`
(`0x00400000`). The content is not on disk. This must not be reported as "format not
recognized": the diagnosis is false, and the user goes looking for a problem that
does not exist.

**`python-docx` and `openpyxl` must not be used for cleaning.** They parse the
document into their own model and rebuild the package, dropping the parts they do not
understand (`customXml/`, themes, `printerSettings`). Work at the level of ZIP
entries: `core/zipfix.py` repacks the container byte-exactly (entry order,
compression method, DOS timestamps, local extra), and untouched entries are copied
as raw bytes.

**Serializing XML through `lxml`/`ElementTree` is a fingerprint too.** A round trip
through them moves the `xmlns` declarations in front of the attributes (in Word's
files `mc:Ignorable` is among them), changes `\r\n` to `\n`, and writes `<a/>`
instead of `<a></a>`, and this is visible on every modified part. That is why
`core/clean_ooxml.py` edits parts **byte by byte** at the offsets reported by
`expat`: only the needed span is rewritten (an attribute value, an element's text,
the element itself), and the rest stays as the source's bytes.

**`mat2` rasterizes PDF pages.** It really does kill everything, but it kills the
text layer and search along with it. PDF needs `pikepdf` with surgical edits.

**Telegram rendering is HTML only, not MarkdownV2.** Escaping is more reliable. All
values from a file go through `html.escape`. Only `b, i, u, s, a, code, pre,
blockquote, tg-spoiler` are allowed; a line break is a plain `\n`. The message limit
is 4096 **UTF-16 code units**, not characters; `_tg_len()` counts them.

**Photo versus file in Telegram.** An image sent as a "photo" is re-encoded by
Telegram before it reaches the bot, so the metadata is already stripped. The bot must
refuse with an explanation, otherwise it produces a false result on the wrong file.

## Tests

```bash
PYTHONIOENCODING=utf-8 python -m tests.run_all
```

`tests.run_all` runs every test suite and module self-test, each in its own process,
and exits 1 if any of them fails. CI (`.github/workflows/tests.yml`) runs it on
Ubuntu and Windows. A single suite can be run on its own:

```bash
PYTHONIOENCODING=utf-8 python -m tests.test_inspect
```

`pytest` is not required (the functions are named `test_*` and are compatible with
it). The harness: the `_with_tmp` decorator gives a test a temporary directory and
removes it; `_check(name, condition, detail)` registers a check; the exit code is 1
on any failure.

The rule: **a failing test is valuable information; a test bent to fit a bug is
harmful.** If a check fails because of a real defect in a module, fix the module.
