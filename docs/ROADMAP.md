# Phase plan and cleaning strategies

## Main goal: leave no trace that the file was cleaned

This is not cosmetic; it is the project's defining requirement. A file with
**zero** metadata is exactly as suspicious as one with all of it: a genuine
`.docx` from Word **always** has an `app.xml` with `Application` and
`AppVersion`, always has `word/settings.xml`, `theme1.xml`, `fontTable.xml` and
rsids. Their absence reads as "mat2 was here."

### Signs of scrubbing that give a file away

1. All fields are empty and required parts are missing.
2. **An inconsistent pair**: `/Creator: Microsoft Word` + `/Producer: qpdf`.
   Real pairs always correlate.
3. ZIP entry order differs from what Word writes. `zipfile` produces its own.
4. A different deflate level, so the compressed size of every entry changed.
5. `created == modified` to the second; `TotalTime = 0`; `cp:revision = 1` — on
   a forty-page document.
6. No rsids although Word authorship is claimed.
7. A PDF without XMP while `Creator: Microsoft Word` — a Word export always
   writes XMP and `/StructTreeRoot`.
8. The PDF's internal structure matches qpdf/Ghostscript output, while
   `/Producer` claims Word.

### Three strategies

**`Paranoid` — zeroing out (mat2 style).** Strip everything. The file is
guaranteed clean, but obviously processed. The right choice when cleanliness
matters more than inconspicuousness: publishing a leak, journalism, handing a
file to an adversary who will inspect it.

**`Regenerate` — recreation (the least conspicuous).** Don't edit the file;
run it through a real stock application: open it in headless LibreOffice and
save it again. The output is a genuine LO file with a genuine internal
structure that is consistent throughout, because LibreOffice really did
produce it. `Application` = LibreOffice is **true**. "A document written in
LibreOffice by a person who left the user name blank" is an ordinary,
unremarkable thing.
*Cost:* the risk of layout changes. The round trip breaks some Word features —
content controls, SmartArt, complex equations, some charts; for xlsx, Power
Query, pivot cache, VBA. So it cannot be the default.

**`Stealth` — surgery plus consistent defaults. The project default.**
We stay inside the original file:
- `Application` / `AppVersion` / the set of parts are **left untouched** — this
  is true, the file really was made in this application;
- `dc:creator` and `cp:lastModifiedBy` → empty string (this is exactly how Word
  writes them when the user name is blank — a legitimate, commonly seen state);
- `TotalTime` → a plausible non-zero value proportional to the document size
  (optional; by default keep the original);
- `created`/`modified` → keep a plausible gap rather than zeroing it;
- rsids → **keep** (random session IDs that carry no direct PII; removing them
  is a separate option, because they link documents together);
- **rebuild the ZIP strictly byte-compatible with the original producer.**

### Where every tool gets caught

Rebuilding the ZIP. It needs the same entry order, the same compression method
and level, the same DOS timestamps, and no extra fields. A standard
`zipfile.ZipFile(..., 'w')` will not preserve that.

For untouched entries, copy the **raw compressed bytes**: seek to
`zinfo.header_offset`, parse the local header, read `compress_size` bytes,
write a new local header and those bytes. Recompress only the 3–5 XML parts
that were actually edited. Otherwise the compressed size of every entry
changes and the file's profile no longer matches the claimed application.

The inspectors record the container's composition (entry order, timestamps,
compression methods) in `Report.parts`; the cleaner itself reads the ZIP directly
through `core/zipfix.py`.

### The line we don't cross

Normalizing a file to look like "a fresh file from a stock application with no
user name" — we do that. **Forging provenance** — writing in a date before a
deadline, substituting another person's author name, fabricating a signing
time — is falsifying a document, and the project will never have such a
feature.

---

## Phases

| Phase | Content | Status |
|---|---|---|
| **0** | Read-only inspection: all formats, dispatcher, CLI, bot, tests | **done** |
| **1** | PDF cleaning (`pikepdf`) and images (stdlib, no re-encoding) | **done** |
| **2** | In-place cleaning: OOXML (`zipfix.py` + part surgery), ODF and RTF | **done** |
| **3** | Consistency linter and **detectability score** | **done** |
| **4** | `Regenerate` mode via LibreOffice; legacy OLE2 | **done** |
| **5** | Profiles in the UI, whitelist of needed fields, batch mode | **done** |

### Phase 1 — PDF and images
`pikepdf`: `/Info`, XMP including the `xmpMM:*` GUIDs, regeneration of the
trailer `/ID`, a full rewrite without linearization to drop the
incremental-update history (linearizing would leave a fingerprint of its own), annotation authorship, JS, `/EmbeddedFiles`, `/PieceInfo`.
Images: our own container rebuild on the stdlib, without re-encoding; `exiftool`
stays optional enrichment for inspection.

Ghostscript — only when a hidden layer must be forcibly flattened, understanding
that it completely changes the file's profile.

### Phase 2 — OOXML
`core/zipfix.py` is the load-bearing piece: `docx`, `xlsx` and `pptx` are one
and the same container, so a single module serves three formats. It comes
first, then the part edits (`core/clean_ooxml.py`). Plus recursion into
`media/` and `embeddings/`.

The parts that are edited are edited **byte by byte**, at offsets reported by
expat, and not re-serialized: a round trip through lxml/ElementTree leaves a
processing fingerprint of its own (`xmlns` order, `\r\n` → `\n`, `<a/>` instead
of `<a></a>`).

**A limit that phase 2 does not lift.** `STEALTH` for OOXML does not reach
inconspicuousness, and this is not an implementation defect: `dc:creator` and
`cp:lastModifiedBy` are blanked (which is how Word writes them when the user
name is empty), but a pair of empty fields is itself a sign — our own inspector
raises `scrubbed`. Leaving the real name means not cleaning; substituting
another name means forging provenance. Measured on the fixtures: PDF/`STEALTH`
— 0 new signals, docx and xlsx/`STEALTH` — 1 (`scrubbed`). The dispatcher
collects such signals in `DispatchResult.new_signals`, and `cleanreport` must
tell the user about them explicitly. The real solution is `Regenerate`
(phase 4).

**`PARANOID` has a worse limit, measured on a genuine Word file.** The profile
strips rsids — and afterwards the inspector raises an `ai` signal on the file:
"the package was assembled by a library (python-docx or an AI generator), not
Word: there is not a single rsid." In other words, `PARANOID` makes the file
look not cleaned but machine-generated, which for many tasks is the worse
outcome: suspicion of forgery weighs more than suspicion of cleaning. The
warning mechanism works — the signal arrives in `new_signals` and ends up in
the report, and the detectability score (`core/lint.py`) rates it `obvious`.
This stays a known limit: keeping rsids keeps a link between documents, and
dropping them reads as a library-made file. Verified with one command:
`python -m core.clean_ooxml --real-word`.

### Phase 2 — ODF and RTF

RTF is **done** (`core/clean_rtf.py`): the groups `\info`, `\*\userprops`,
`\*\revtbl`, `\*\template` and absolute paths are edited in place; the file is
not rebuilt. STEALTH keeps `\*\generator`, dates and `\*\rsidtbl`. PARANOID
strips those too; an empty generator and an empty rsid table are themselves
read as a scrubbing trace, and the report shows this in its list of actions.

ODF is **done** (`core/clean_odf.py`): `meta.xml`, `settings.xml`, comments and
`Thumbnails/`. The container is repacked by `zipfix`, and entry times are not
rewritten. In a file from Word they are already `1980-01-01`, in a file from
LibreOffice they are already real: changing them in either direction would
leave a fingerprint. STEALTH keeps `meta:generator` and dates. PARANOID strips
those too.

A trap, and the reason it is done this way. It was assumed that ODF ZIP
timestamps are always **real**. That holds only for LibreOffice. Word, when
saving an `.odt`, writes `1980-01-01`, and on such a file the inspector raises
`scrubbed` — because the generator is not LibreOffice. The cleaner does not
choose the time: it keeps whatever the producer wrote.

### Phase 3 — linter and detectability score
The feature the project exists for: after cleaning, the bot answers not
"done" but with a report — whether the file is clean, **and whether it looks
processed**. The checks: consistency of `Creator` ↔ `Producer`, ZIP timestamps
matching the claimed application, presence of the parts that application
requires.

As built: the consistency checks live in the inspectors (`Creator` ↔
`Producer`, `/Info` ↔ XMP, dates, Calibri together with Aptos, a font against
the claimed date), and `core/lint.py` folds their signals into a single score:
`ai` — `obvious`; `scrubbed`, or an `inconsistent` contradiction with `high`
confidence — `noticeable`. Weak contradictions (`created == modified`) occur
in honest files and do not count toward the score.

### Phase 4 — Regenerate and legacy OLE2

This phase covers only what cannot be cleaned in place at all.

**Legacy OLE2** (`.doc`/`.xls`/`.ppt`). Metadata and edit history are baked
into the container: the `1Table`/`0Table` streams hold the **deleted text of
earlier versions**, and it cannot be dug out without rebuilding the document.
The only honest way is conversion by a stock application.

**Done.** For `.doc`/`.xls`/`.ppt` the bot offers a single "Regenerate" button
if LibreOffice is found; without it, a refusal with a hint. After conversion
the result is cleaned up by our own cleaner (`clean_ooxml` with
`drop_custom=True`): LibreOffice carries over the document's custom properties,
while a fresh file never has any — measured on the "Отдел" ("Department")
property from `DocumentSummaryInformation`, which otherwise survived
regeneration.

**`Regenerate` mode** — recreating the file through LibreOffice. It is also the
only thing that lifts the OOXML inconspicuousness limit from phase 2: a file
saved by a stock application carries no sign of cleaning, because nobody
cleaned it.

It requires LibreOffice on the machine, while the project rule is **zero
mandatory external binaries**. So `Regenerate` is built as an optional path:
`soffice` is looked up, and if it is missing — an honest refusal with an
explanation, not a silent downgrade to in-place cleaning (the user chose
`Regenerate` precisely for inconspicuousness, and quietly giving them something
else would be deceiving them).

The second trade-off, which has to be stated out loud: LibreOffice changes the
layout and itself becomes the claimed application. After `Regenerate` the
file's profile is that of a LibreOffice document, not a Word one. For some
tasks that is worse than the original problem, so the mode stays the user's
choice and not the default.

### Phase 5 — whitelist of "needed" metadata
What we usually **keep** when cleaning: `dc:title` for a published report,
`/Lang` and `/StructTreeRoot` (accessibility for screen readers), ICC profiles
(correct color in print), PDF bookmarks and table of contents, form field
structure, digital signature (mutually exclusive with cleaning — warn),
corporate sensitivity labels (removing them may violate policy and is detected
on the tenant side — warn).

---

## Deferred tasks and known gaps

For planning, not a bug list.

**PDF.** Fonts are traversed in depth: Form XObjects, tiling `/Pattern`s and
annotation appearance streams (`/AP`), with a limit on nodes and depth. XMP is
also read on individual objects (pages, forms, images) — previously only the
cleaner removed it, and the "before" report said nothing about it. `/PieceInfo`
is not unpacked (only the application name is recorded).
The `/Prev` counter is computed by a substring search over the bytes — a false
positive is theoretically possible if `/Prev` appears in uncompressed page
content.

**OOXML.** The full reference part order for Word/Excel is not checked — there
is no set of real reference files. DEVMODE is parsed up to `dmDeviceName`,
`dmFormName`, `dmPaperSize` and `dmDriverExtra`; the driver's private block is
**not** parsed, and only UNC paths are extracted from it — the format differs
for every driver and is undocumented, and a false finding in a privacy report
costs more than a missed one. pptx specifics (`presProps.xml`,
`notesSlides/*`, `slideMasters/*`) are covered only by the generic part.
`docMetadata/LabelInfo.xml` is matched by name suffix, not by exact path.

**Report.** `Report.parts` is not rendered (it records the container's composition
for the tests; users don't need it). The number of messages is capped by `bot._MAX_PARTS`, with an honest
truncation notice; without the cap, Telegram answered 429 to a report of dozens
of messages, i.e. the bot blocked itself. Values go under `tg-spoiler`
(`report.MASK_VALUES`).

What masking does **not** give you: a spoiler hides the value from view —
previews, screenshots, forwarded messages — but the report text has still gone
to Telegram's servers and sits in the chat history. The report footer says so
plainly. That is why there is auto-deletion (`REPORT_TTL_MIN`, 60 minutes by
default; a bot can delete its own messages within 48 hours), a "Delete report
from chat" button and the `/clear` command. Limit: the report registry lives in
process memory, so after a restart old reports have to be deleted by hand. The
source file the user sent is not touched by the bot — it is the user's
message.

**Legacy OLE2.** **`SttbfRMark` is extracted**: reviewer names are read from the
active table stream by the `fc`/`lcb` pair at index 51 in `FibRgFcLcb`
(MS-DOC 2.5.7). The first entry is the placeholder `Unknown`, as in RTF
`\*\revtbl`, and does not count as a person. The deleted text of earlier
versions in the stream is still not extracted: it cannot be pulled out without
rebuilding the document, and that is a phase 4 task, not the inspector's. Also:
paths of external workbooks from `SUPBOOK` in `.xls` are not extracted (only
the records are counted); the Unicode section of custom properties is built
per MS-OLEPS and has not been checked against a file from real Office (no real
Office `.doc` sample was available when it was written). Not covered by fixtures: the v4 container (4096-byte sectors),
DIFAT beyond the header (>109 FAT sectors), an encrypted `.doc`,
BIFF5/Excel 95, digital signature.

**Unreadable files.** Cloud-placeholder indicators are checked only **after** a
failed `open()`: if OneDrive manages to download the file on open, everything
works as usual (hydration as a side effect of reading). There is deliberately
no up-front attribute check, so as not to change behavior on ordinary files.
"No permission" and "busy with another process" cannot be told apart from the
`open()` exception: both give `errno 13` without `winerror`; that would take a
dedicated `CreateFileW` with `GetLastError`. An empty file stays `unknown`
(there is no separate `empty` key, to avoid widening the contract more than
necessary) — the difference is only in the error text.
