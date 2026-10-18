# Where metadata hides

A reference by format: what exactly leaks and where it lives. The basis for the
inspectors (phase 0) and for the cleaning rules (phases 1–4). Items marked
**⚠** are the ones almost every off-the-shelf tool misses.

The main idea: metadata is **not** "author and date". There are dozens of
places, and the most dangerous ones are not where people look.

---

## OOXML: `.docx` / `.xlsx` / `.pptx`

The container is a plain ZIP. Work with it through `zipfile` + `lxml`.

### The ZIP container itself ⚠

| What | Why it matters |
|---|---|
| DOS timestamps of the entries | Word/Excel/PowerPoint set `1980-01-01 00:00` on all of them. `openpyxl`/`python-docx` write **the real save time**. A mismatch with the application declared in `app.xml` means the file was made by a library, and the time leaked through the container |
| Entry order | Word writes the parts in a characteristic order, starting with `[Content_Types].xml`. A rebuild in a different order is detectable |
| Compression method and level | A different deflate level changes the compressed size of every entry |
| Extra fields of the local headers | Some ZIP implementations add Unix timestamps |

### `docProps/core.xml`
`dc:creator`, `cp:lastModifiedBy`, `dcterms:created`, `dcterms:modified`,
`cp:revision`, `cp:lastPrinted`, `dc:title`, `dc:subject`, `dc:description`,
`cp:keywords`, `cp:category`, `cp:contentStatus`, `dc:identifier`, `dc:language`.

⚠ **The dates here are accurate to the minute: Word's seconds are always
`00`.** So `created == modified` only means "created and saved within one
minute", the ordinary state of any document made in one sitting, and is not a
sign of forged dates by itself. `cp:revision` doesn't work as evidence either:
a file from Word 16 had `revision=2` after four saves in a row, all within the
same minute. The only real contradiction comes from `TotalTime`: the claimed
minutes of active editing can't fit into one minute of the document's life.

### `docProps/app.xml`
`Application`, `AppVersion`, `Company`, `Manager`,
**`TotalTime`** (editing minutes ⚠), `Pages`/`Words`/`Characters`/`Lines`,
`Template`, `DocSecurity`, `ScaleCrop`, `LinksUpToDate`, `SharedDoc`,
`HyperlinksChanged`, `HeadingPairs`, `TitlesOfParts` (gives away the document
structure and the sheet names).

### `docProps/custom.xml` ⚠
Arbitrary properties. This is where Microsoft Purview labels live: the
`MSIP_Label_<GUID>_*` prefix carries the label GUID, `SetDate`, **`SetBy`, a
real email**, and `SiteId`, the tenant GUID. Plus SharePoint's `ContentTypeId`
and add-in properties. See also `docMetadata/LabelInfo.xml`.

> Removing a corporate label may itself violate a policy and is **detectable
> on the tenant side**. Warn the user.

### `docProps/thumbnail.*` ⚠
A rendered preview. Sometimes of the **previous** version of the document.

### `word/settings.xml`
- **`w:rsid`** ⚠: revision save ID. Word issues one per editing session and
  tags every text run in `document.xml` with them. The statistics give away the
  number of sessions; matching rsids between documents prove a shared Word
  installation lineage. Used in forensics.
- `w:attachedTemplate`: resolves through `r:id` in `.rels`, often to
  `file:///C:/Users/NAME/...`
- `w:mailMerge`: paths to the data source
- `w:documentProtection`: the password hash and salt
- `w:proofState`, `w:trackChanges`, `w:hdrShapeDefaults`, `w:docVars`
  (values in the `w:name`/`w:val` attributes; a variable named like "Owner" or
  its localized equivalent holds a full name from a template or a document
  management system, so it's identity, not provenance)

In PowerPoint 365 the authors of "modern" comments live in **`ppt/authors.xml`**
(`name`, `initials`, `userId`, usually a work email), not in `commentAuthors.xml`.

### `word/document.xml`
`w:ins` / `w:del`: tracked changes with the `w:author` and `w:date` attributes.
`w:rsid*` attributes on every paragraph and run. The `_GoBack` bookmark
(harmless, but a Word signature). `w:permStart` with editor names. Parse it as a
stream with `iterparse`: the file can be tens of megabytes.

### `word/people.xml` ⚠
`w15:presenceInfo` with the `userId` attribute: **real email addresses**.
Counterparts: `xl/persons/person.xml`, `xl/threadedComments/*`, `ppt/comments*`.

### `word/comments.xml`, `commentsExtended.xml`
Authors, initials, dates.

### Every `*.rels`
Relationships with `TargetMode="External"`: `file:///`, absolute `C:\...`
paths, UNC `\\server\...`, http hyperlinks, OLE sources.

### `word/media/*`, `xl/media/*`, `ppt/media/*`
Images with **full EXIF**: GPS, the camera serial number, the original file
name. Needs recursion: see the images section.

### `*/embeddings/*.bin` ⚠
Embedded OLE objects. An Excel table embedded in a docx has **its own
`docProps`**: recursion one level deeper.

### `word/printerSettings/printerSettings*.bin` ⚠
A `DEVMODEW` structure followed by the driver's private data. On real `.xlsx`
files downloaded from the internet this parse consistently finds the printers
of whoever prepared the file, even when `docProps` was scrubbed.

| Offset | Field | What it gives |
|---|---|---|
| 0 | `dmDeviceName[32]` WCHAR | **the printer name**: host name, asset tag, office location (`HP-LaserJet-FLOOR3-NYC`, `BUH-01`) |
| 68 | `dmSize` WORD | the length of the structure itself; the driver data starts after it |
| 70 | `dmDriverExtra` WORD | the length of the driver's private block |
| 78 | `dmPaperSize` short | the paper code: `1` Letter, `5` Legal, `9` A4 |
| 102 | `dmFormName[32]` WCHAR | the form name; a custom one is set up by an admin and carries a department name |
| `dmSize`… | driver data | **the UNC path to the print server** (`\\PRINTSRV01\WC7845-BUH`), port, queue |

**32 WCHARs are 64 bytes, not 32.** The field is read as
`blob[0:64].decode("utf-16le")` up to the first NUL. Reading 32 bytes cuts the
printer name at the 16th character without anyone noticing:
`HP LaserJet M404 (BUH-01)` turns into `HP LaserJet M40`, and the office
location, exactly what's being looked for here, is cut off.

**The paper size is geolocation.** `Letter`/`Legal` means North America, `A4`
almost the whole rest of the world; the size follows the system locale rather
than a deliberate choice. That's why it's a `Signal(kind="dating")` and not
just a finding.

The format of the driver's private block is undocumented and differs between
drivers, so it isn't parsed fully: only unambiguously recognizable UNC paths
are searched for. A false finding in a privacy report costs more than a missed
one.

### Excel specifics
| Where | What |
|---|---|
| `xl/workbook.xml` | `fileVersion` with `rupBuild`: the exact Excel build; `workbookPr/@filterPrivacy`; sheets with `state="hidden"` and `"veryHidden"`; `definedName` with references to external workbooks by local paths |
| `xl/workbook.xml` ⚠ | **`x15ac:absPath/@url`** inside `mc:AlternateContent`: the full path to the folder where the workbook was saved (`C:\Users\NAME\...`). Excel writes it silently, it's not visible in the properties. `fileSharing/@userName`: who turned on workbook reservation |
| `xl/pivotCache/*.xml` ⚠ | **`refreshedBy`**: a person's name, `refreshedDate`. A classic leak |
| `xl/connections.xml` ⚠ | ODBC/OLEDB connection strings: server names, logins, sometimes passwords |
| `xl/externalLinks/*` | paths to external workbooks |
| `xl/queryTables/*`, `customXml/*` | Power Query M code, SharePoint bindings |
| `xl/revisions/*` | the shared workbook history |

Plus hidden information that isn't metadata: hidden rows and columns, hidden
sheets, unused named ranges, cached formula results from deleted source data,
white text on white, size-1 fonts.

### `customXml/`: the risk is defined by the root, not by the part's presence ⚠
It occurs in any OOXML package, in real `.docx` files more often than in
`.xlsx`. Things of opposite nature live under the same `customXml/item1.xml`
name, and the path says nothing. Look at the namespace of the root:

| Root | What it is | Risk |
|---|---|---|
| `…microsoft.com/office/2006/metadata/properties` and its neighbors, `…sharepoint/v3/contenttype/forms` | SharePoint library columns, the content type schema, form templates | ENVIRONMENT: employee names, dates, the site GUID |
| `…openxmlformats.org/officeDocument/2006/bibliography` | a bibliography | STRUCTURAL: **this is document content**, visible to the reader |
| `…microsoft.com/DataMashup` | an encoded package with Power Query code | ENVIRONMENT, nothing can remove it |
| something custom | document management data, content control bindings (`w:dataBinding`) | ENVIRONMENT, can't be removed blindly: it would erase visible text |

Two consequences that are expensive to recover from:

**The values live in attributes, not only in text.** A `contentTypeID` with
the site GUID is an attribute of the root. A parse that only collects
`itertext()` misses the most interesting part.

**Raw XML must never be used as a finding's value.** The cleanliness criterion
is "the original sensitive value doesn't occur in the output", and a raw dump
contains tag names and schema URIs that are the same in every document of that
type. A part legitimately left in place would then be declared by the oracle a
"value that survived cleaning", and a correctly cleaned file would get a "not
fully cleaned" verdict. For the same reason a verbal placeholder must not
replace an empty value: the string "no values" would match itself.

### Analysis of missing parts ⚠
For a `.docx` claiming Word authorship, check for `word/theme/theme1.xml`,
`word/fontTable.xml`, `word/webSettings.xml`, `word/styles.xml` and
`word/settings.xml`. Missing required parts and/or no rsids at all mean the file
was made by a library, not by Word.

### `vbaProject.bin`
Macros. A `hazard` signal.

---

## PDF

### The `/Info` dictionary
`/Author`, `/Creator` (the authoring application), `/Producer` (the library
that wrote the PDF), `/CreationDate`, `/ModDate`, `/Title`, `/Subject`,
`/Keywords`, `/Trapped`, plus any non-standard keys. Dates in the
`D:YYYYMMDDHHmmSS+TZ` format.

### XMP (`/Metadata`) ⚠
What people forget after scrubbing `/Info`. It duplicates `/Info` and adds:
- **`xmpMM:DocumentID`, `xmpMM:InstanceID`, `xmpMM:OriginalDocumentID`**:
  persistent GUIDs that link all versions of the document together
- **`xmpMM:History`**: an actual edit log: `stEvt:action`,
  `stEvt:softwareAgent`, `stEvt:when`, `stEvt:changed`
- `xmpMM:DerivedFrom`, `xmp:CreatorTool`, `xmp:CreateDate`/`ModifyDate`
- the `photoshop:`, `illustrator:`, `pdfaid:`, `xmpRights:` namespaces

XMP isn't only in the root: per-page and per-XObject `/Metadata` streams
occur too.

### The trailer `/ID`
Two 16-byte values. Many producers derive `ID[0]` from the path and name of
the source file, which makes it an identifier that links files.

### Incremental updates ⚠⚠
**The most serious finding a PDF can have.** If the file was edited
incrementally, all the previous versions physically sit in it: old xrefs, old
page content and, in particular, **the text under "redacted" black
rectangles**. Detection: the number of `%%EOF` and `startxref` occurrences in
the raw bytes, `/Prev` in the trailers. The fix is a full rewrite: QPDF writes only
the objects reachable from the trailer, so the old revisions are gone.
Linearization isn't needed for that and is a fingerprint of its own (it adds a
`/Prev` and hint tables), so the cleaner doesn't linearize.

### Other
| Where | What |
|---|---|
| `/Annots` | `/T` = **the annotation author's name**, `/M`, `/CreationDate`, `/Contents`, `/RC` |
| `/AcroForm` | field values that look "cleared" but kept `/V`; `/XFA` with a copy of the data |
| `/Names /EmbeddedFiles`, `/FileAttachment` | attachments; PDF/A-3 and e-invoices carry the whole source XML/xlsx |
| `/Names /JavaScript`, `/OpenAction`, `/AA`, `/Launch`, `/URI` | active content, can "phone home" |
| `/Sig`, `/Perms`, `/SigFlags` | the signer's certificate: full name, email, organization, signing time, document hash |
| `/OCProperties` | optional content layers; those turned off in `/D /OFF` may hold invisible text |
| `/PieceInfo` | private Acrobat/Distiller/Illustrator data, sometimes an editable source |
| `/PTEX.Fullbanner` | the full TeX Live version string with the build date |
| fonts | subset prefixes like `ABCDEF+Calibri`; **the glyph set gives away characters from deleted text** |
| `/MediaBox` | paper size → country: Letter = US/Canada, A4 = elsewhere |

### Structural: keep
`/StructTreeRoot` and `/MarkInfo` (tagged PDF, screen reader accessibility),
`/Lang`, `/Outlines` (the outline), `/PageLabels`, `/ViewerPreferences`.

### Digital signature
**Cleaning and signing are mutually exclusive.** Any metadata change breaks the
signature. Mark it `removable=False` and warn explicitly.

> A subtlety that caught a defect here: an empty field stub (`/FT /Sig`
> **without** `/V`) is not a signature. There's nothing to break, and
> `removable=False` on it would block cleaning for no reason.

### Dating by fonts
`Aptos` has been the Microsoft 365 default font since 2023: the document was
created no earlier than 2023, **whatever the claimed dates say**. `Calibri`
means Office 2007–2023. In the 2017 Panama case a forgery was exposed exactly
this way: Calibri wasn't commercially available in February 2006.

---

## Legacy OLE2: `.doc` / `.xls` / `.ppt`

A CFBF container, read with `olefile` (it can't write).

`SummaryInformation` and `DocumentSummaryInformation`: `author`,
`last_saved_by`, `company`, `manager`, `title`, `subject`, `keywords`,
`comments`, `create_time`, `last_saved_time`, `last_printed`,
**`total_edit_time`**, `num_pages`, `creating_application`, `security`,
`revision_number`, `template`, `codepage`. Values arrive as `bytes`: decode
them according to the codepage.

### Why these are converted rather than cleaned ⚠⚠
- The **`1Table`/`0Table`** stream keeps the edit history and the author table
  (`SttbfRMark`)
- When saved in fast-save mode, **the deleted text of earlier versions**
  physically stays in the file
- The unused space of the OLE container holds fragments of **other documents
  from the same disk**

Conclusion: such a file can't be cleaned in place. The only reliable way is
converting it to a modern format (a round trip through LibreOffice).

Also: `\x01CompObj` (the name of the creating application), `ObjectPool`
(embedded objects with their own metadata), `Macros` / `_VBA_PROJECT_CUR` /
`VBA` (a `hazard` signal).

### What else turned up in real containers

| Where | What |
|---|---|
| **Custom properties** ⚠⚠ | `olefile.get_userdefined_properties()` reads property names **only as UTF-8**. Because of that, a Unicode section (code page 1200, exactly how real Office writes it) and a cp1251 section are lost **silently**: zero errors, zero findings. That's exactly where Purview labels with an email and values like a department name live. Parse the sections yourself per MS-OLEPS, tolerating name alignment to 4 bytes and its absence |
| `Root Entry`, modification time | `ole.getctime("Root Entry")` raises `OSError`; with a bare `except` the finding vanishes for every file. The root modification time is **not equal** to the last saved date and is a separate leak |
| `CurrentUserAtom` (`.ppt`) | the last editor's name. The cp1251 code page read as cp1252 gives `Ïåòðîâ` instead of `Петров` |
| Fast-save count (`.doc`) | how many times the file was saved in fast-save mode: a direct measure of how much deleted text may remain |
| `lid` in the FIB (`.doc`) | the language of the Word installation (`0x0419` = Russian), i.e. the author's locale |
| `SUPBOOK` / `EXTERNSHEET` (`.xls`) | the workbook's external links, including UNC paths like `\\SRV\share\file.xls` |
| Vector properties of a property set | A field with `VT_VECTOR` and an element count of 4 billion is a denial vector: the parser has to check the count against the actual stream size instead of trusting the header |

> `SttbfRMark` is the table of reviewer names in the active table stream
> (`0Table` or `1Table`). The `fc`/`lcb` pair is index 51 in `FibRgFcLcb`, the
> base is computed from `csw`/`cslw` in the FIB. The format is a Unicode STTB
> (`fExtend=0xFFFF`). The first entry is always the service entry `Unknown`, as
> in RTF, and doesn't count as a person. The names are extracted. The deleted
> text of earlier versions in the same stream is still not extracted: that's
> already a document rebuild, phase 4.

---

## Images

### EXIF
- **IDENTITY:** `Artist` (0x013B), `Copyright` (0x8298), `CameraOwnerName`
  (0xA430), **`BodySerialNumber`** (0xA431), **`LensSerialNumber`** (0xA435),
  `HostComputer` (0x013C), `ImageDescription` (0x010E), `XPAuthor`/`XPComment`/
  `XPKeywords` (0x9C9D/0x9C9C/0x9C9E, in **UTF-16LE** ⚠)
- **GPS** (IFD 0x8825): `GPSLatitude`/`GPSLongitude` with their `Ref`: convert
  degrees-minutes-seconds to decimal; `GPSAltitude`, `GPSDateStamp`
- **PROVENANCE:** `Make`, `Model`, `Software`, `DateTime`, `DateTimeOriginal`,
  `DateTimeDigitized`, `LensModel`
- **`MakerNote`** (0x927C) ⚠: a proprietary block, often with serial numbers and
  the settings of a specific camera unit
- **IFD1 thumbnail** ⚠: the embedded thumbnail may have been made **before
  retouching** and hold the original image

Defenses: limit the number of IFDs and tags walked, track visited offsets:
broken EXIF can loop.

### JPEG, by segment
`APP0` JFIF · `APP1` Exif or XMP · `APP2` ICC (the profile name is often named
after the device or monitor) / MPF · **`APP11` JUMBF, the C2PA container** ·
`APP13` Photoshop IRB / IPTC-IIM · `APP14` Adobe · `COM` comment ·
**`DQT` quantization tables**: they fingerprint the encoder and can't be
removed without lossy re-encoding (`removable=False`).

### PNG, by chunk
`tEXt`, `zTXt` (zlib), `iTXt` (often a whole XMP packet), `tIME`,
**`eXIf`** ⚠ (full EXIF in newer PNGs), `iCCP`, `pHYs`, `caBX` (C2PA).

> ⚠ `zTXt`/`iTXt` are a denial vector: a kilobyte of compressed data expands
> into gigabytes. Decompress with a limit, not with a bare `zlib.decompress()`.
> The tests have a `png_ztxt_bomb` fixture of 200 MB.

### C2PA / Content Credentials
A cryptographically signed provenance manifest: a JUMBF box in JPEG/PNG/TIFF,
a stream in PDF, a part in OOXML. It can technically be removed, but removing a
signed manifest is **itself detectable**, and with a soft binding or a
watermark the provenance can be recovered even without the file.

---

## ODF, RTF, other

**ODF** (`.odt`/`.ods`/`.odp`): ZIP + `meta.xml`: `meta:generator` (the exact
LibreOffice build, a strong fingerprint), `meta:initial-creator`, `dc:creator`,
`meta:creation-date`, `meta:editing-cycles` (how many times it was opened for
editing), **`meta:editing-duration`**, `meta:printed-by`,
`meta:document-statistic`, every `meta:user-defined`. Plus `settings.xml` with
local paths, `Basic/` and `Scripts/` with macros, tracked changes in
`content.xml`.

⚠ **`meta:template` is an empty element, the path is in the `xlink:href`
attribute** (`file:///C:/Users/NAME/.../template/letterhead.ott`), the template
name is in `xlink:title`. Reading the element text is useless: it's always
empty, and the report showed "Template: (empty)", missing the user name.
`meta:auto-reload` works the same way (a link, often to a file server).

⚠ **ODF ZIP timestamps depend on the producer, not on the format.**
LibreOffice writes **the real time**. Word exporting to `.odt` writes
`1980-01-01`, the same as in its own OOXML (verified on a file from Word 16;
its `meta:generator` is `MicrosoftOffice/15.0 MicrosoftWord`). So "ODF has real
timestamps" is a wrong generalization, and the cleaner has to decide by the
generator. For a file from Word, real times in the entries would be a sign of
processing, while for a file from LibreOffice the epoch would be.

**RTF**: text with control words. The `\info` group: `\author`, `\operator`
(who saved last), `\company`, `\creatim`/`\revtim`/`\printim` (like
`\yr2024\mo3\dy5`), **`\edmins`** (editing minutes), `\nofpages`, `\version`.
Plus `\*\userprops`, **`\*\rsidtbl`**, `\*\generator`, `\*\revtbl`,
`\objdata` (embedded OLE in hex), `\*\datastore`, `\*\template`.
Mind the escaping: `\\`, `\{`, `\}`, `\'hh`.

Two RTF traps, both produce false findings and both were verified on a file
from Word:

**`\*\revtbl` starts with the service entry `{Unknown;}`.** Word always writes
it, even when the file has no tracked changes. It must not be passed off as a
person: an extra `IDENTITY` finding, an author count inflated by one and, worse,
a word longer than the cleaning oracle's threshold, so it "survives" any
cleaning and the file gets a "not fully cleaned" verdict.

**Search for paths in the decoded text, not in the raw one.** In raw RTF a real
backslash is written doubled, and control words can sit between characters. A
match like `\\\hich\f37` (a literal backslash in the text plus two control
words) looks like a UNC path, but after decoding only a single `\` is left.

**CSV/TXT**: almost clean: only the BOM and the line ending style (CRLF →
Windows). The safest export format.

**The file name** is the biggest leak, and Telegram keeps it.

---

## What metadata doesn't solve

Boundaries that have to be communicated to the user honestly:

- **Writing style.** Stylometry and perplexity are outside metadata.
- **Token-level watermarks** in the text itself (like SynthID for text).
  Statistical, they live in the words; only rewriting the prose removes them.
- **The server side.** The version history and audit log of
  SharePoint/Drive/M365 live **on the server**, not in the file. Cleaning a
  local copy doesn't affect them.
- **JPEG quantization tables** without lossy re-encoding.
- **Dating by features and fonts**: see Aptos/Calibri above.
