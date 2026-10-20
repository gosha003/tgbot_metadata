# -*- coding: utf-8 -*-
"""Batch cleaning: a ZIP of documents -> a ZIP of cleaned ones (phase 5).

    clean_batch(src_zip, dst_zip, profile=Profile.STEALTH, keep=None,
                force_signed=False, *, neutral_names=False, quarantine=False)
        -> BatchResult

No cleaning logic of its own: every file goes through core.clean.clean_file,
which already handles routing, oracle verification (a second inspect_file)
and honest refusals. This module only does packing, protection against a
hostile archive, and the report.

DECISION 1: anything not cleaned is NOT put into the output archive.
Invariant: everything in the output ZIP except _REPORT.txt is cleaned AND
proven by the oracle, so the archive can be forwarded whole without
unpacking. Anything else in the archive is the risk itself: a "NOT_CLEANED
folder" easily travels on with the archive, and the user hands a
counterparty an original full of metadata, sure it's clean. The user
already has the originals. Whoever still wants everything in one archive
sets quarantine=True: the originals then go to NOT_CLEANED/ and also carry
a NOT_CLEANED__ prefix in the name itself (a file taken out of the folder
is still labeled).
A file the dispatcher returned but couldn't prove (original values remain,
or the inspector didn't parse the whole file) counts as NOT cleaned:
status "unproven".

DECISION 2: file names are metadata too ("Dismissal_Petrov_Vector_LLC.docx").
neutral_names=True renames everything to doc_0001.<extension by content>,
and folders are dropped. In this mode _REPORT.txt inside the archive also
refers only to numbers: otherwise the "number -> original name" mapping
would travel with the archive and defeat the point. The name table stays
only with the caller: BatchResult.items[i].name and
render_report(res, with_names=True), for the chat.
Entry times, comment and extra fields of the output archive are constant,
not taken from the source: a file's mtime is a trace too.

PROTECTION: input size, file count, total uncompressed size and compression
ratio are bounded (thresholds as in core/zipfix.py); names with "..",
absolute names, names with a drive letter, ADS and control characters are
rejected (we never write to disk under those names; temp files get generated
names, but an output archive with such a name would unpack outside the
folder on the recipient's side). Nested archives are not unpacked: they're
flagged "contents not cleaned". Encrypted entries are rejected.

The input is never modified (sha256 before and after). Temp directories come
from tempfile and are removed in finally. Nothing is raised. No names or
content are logged (there are no logs).

CLI:  python -m core.clean_batch                      -- self-test
      python -m core.clean_batch in.zip out.zip [--profile ...] [--neutral-names]
                                 [--quarantine]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zipfile

from . import sniff
from .clean import _CLEANERS, MAX_FILE, clean_file
from .cleanmodel import Profile
from .model import _ABS_PATH, PATH_STUB, clip
from .zipfix import (MAX_ENTRIES, MAX_INPUT, MAX_RATIO, MAX_REPLACE, MIN_BOMB_UNPACKED,
                     MAX_TOTAL_UNPACKED)
from core.i18n import N_, tr

# Our own limits: MAX_ENTRIES from zipfix (50,000) is sized for parsing a
# container, while here EVERY entry gets a full cleaning with two inspections
# (and for regenerate, a LibreOffice launch), so the ceiling is orders of
# magnitude lower.
MAX_FILES = min(1000, MAX_ENTRIES)
MAX_MEMBER = min(MAX_REPLACE, MAX_FILE)   # one entry after decompression
REPORT_NAME = "_REPORT.txt"
QUARANTINE_DIR = "NOT_CLEANED"
QUARANTINE_PREFIX = "NOT_CLEANED__"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)        # constant: the real entry time is a trace
_CHUNK = 1 << 20
_BAD_NAME = re.compile(r'[\x00-\x1f<>:"|?*]')   # ':' -- drive letters and NTFS alternate data streams
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

CLEANED, UNPROVEN, REFUSED, UNSUPPORTED, NESTED, REJECTED = (
    "cleaned", "unproven", "refused", "unsupported", "nested", "rejected")
STATUS_TITLE = {
    CLEANED: N_("CLEANED"), UNPROVEN: N_("NOT CLEANED (cleanliness not proven)"),
    REFUSED: N_("NOT CLEANED (refused)"), UNSUPPORTED: N_("NOT CLEANED (format not supported)"),
    NESTED: N_("NOT CLEANED (nested archive)"), REJECTED: N_("NOT CLEANED (rejected by safeguards)"),
}
_DETECT = {"none": N_("the inspector doesn't see any"), "noticeable": N_("noticeable on a closer look"),
           "obvious": N_("obvious: the file is recognizable as processed (a limit of the format, "
                         "see the regenerate profile)")}
_EXT = {"jpeg": "jpg", "ole": "bin", "text": "txt", "unknown": "bin", "unreadable": "bin"}

# Magic numbers of archives sniff doesn't know (it knows zip itself).
_ARCHIVE_MAGIC = ((b"Rar!\x1a\x07", "RAR"), (b"7z\xbc\xaf\x27\x1c", "7z"),
                  (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"))


# ============================================================================
#  Result
# ============================================================================


@dataclasses.dataclass
class BatchItem:
    """One file from the uploaded archive."""

    idx: int
    name: str                  # name in the source archive (control characters removed)
    fmt: str = ""              # sniff key; "" means rejected before format detection
    status: str = REJECTED
    ok: bool = False           # the dispatcher handed over a file
    clean: bool = False        # and proved it clean with the oracle
    survived_n: int = 0        # how many original values were found in the output
    detect: str = ""           # cleaning detectability score (core/lint.py)
    reason: str = ""           # in short: why it wasn't cleaned
    in_archive: bool = False   # whether it's in the output archive (besides the report)
    out_name: str = ""         # name in the output archive

    def __post_init__(self) -> None:
        self.name = clip(_CTRL.sub("?", str(self.name)), 200)


@dataclasses.dataclass
class BatchResult:
    src: str
    dst: str
    profile: str
    ok: bool = False           # the output archive was written (even if 0 files were cleaned)
    neutral_names: bool = False
    quarantine: bool = False
    items: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)
    totals: dict = dataclasses.field(default_factory=dict)

    def err(self, message) -> None:
        self.errors.append(clip(_ABS_PATH.sub(tr(PATH_STUB), str(message)), 600))

    @property
    def all_cleaned(self) -> bool:
        """The whole uploaded batch was cleaned and proven."""
        return bool(self.ok and self.totals.get("total") and
                    self.totals.get("cleaned") == self.totals.get("total"))

    def count(self, status) -> int:
        return sum(1 for i in self.items if i.status == status)


def _totals(items) -> dict:
    t = {"total": len(items)}
    for st in (CLEANED, REFUSED, UNSUPPORTED, UNPROVEN, NESTED, REJECTED):
        t[st] = sum(1 for i in items if i.status == st)
    t["not_cleaned"] = t["total"] - t[CLEANED]
    return t


# ============================================================================
#  Helpers
# ============================================================================


def _sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file(a, b) -> bool:
    try:
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            return True
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def _safe_name(raw):
    """Normalized relative path inside the archive, or None if the name is dangerous."""
    if not raw or _BAD_NAME.search(raw):
        return None
    name = raw.replace("\\", "/")
    if name.startswith("/") or len(name) > 240:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p != p.rstrip(" .") for p in parts):
        return None                      # ".." and trailing dots/spaces (Windows strips them)
    return "/".join(parts)


def _ext(fmt) -> str:
    return _EXT.get(fmt, fmt)


def _archive_kind(path, fmt):
    """Name of a nested archive, or None. OOXML/ODF are documents, not archives."""
    if fmt == "zip":
        return "ZIP"
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    for magic, kind in _ARCHIVE_MAGIC:
        if head.startswith(magic):
            return kind
    return "TAR" if head[257:262] == b"ustar" else None


def _extract(zf, info, path) -> int:
    """Decompress an entry, counting real bytes rather than the size declared in the header."""
    n = 0
    with zf.open(info) as r, open(path, "wb") as w:
        for chunk in iter(lambda: r.read(_CHUNK), b""):
            n += len(chunk)
            if n > MAX_MEMBER:
                raise OverflowError
            w.write(chunk)
    return n


class _Out:
    """Output ZIP: deterministic headers, so no source metadata is carried over."""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
        self.used = set()

    def unique(self, name) -> str:
        base, ext = os.path.splitext(name)
        cand, n = name, 1
        while cand.lower() in self.used:
            n += 1
            cand = "%s (%d)%s" % (base, n, ext)
        self.used.add(cand.lower())
        return cand

    def _info(self, name):
        zi = zipfile.ZipInfo(name, _ZIP_TIME)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.create_system = 3
        zi.external_attr = 0o644 << 16
        return zi

    def add_file(self, name, path) -> None:
        with open(path, "rb") as r, self.zf.open(self._info(name), "w") as w:
            shutil.copyfileobj(r, w, _CHUNK)

    def add_text(self, name, text) -> None:
        self.zf.writestr(self._info(name), text.encode("utf-8"))


# ============================================================================
#  Report
# ============================================================================


def _label(it, with_names) -> str:
    return it.name if with_names else "№%04d" % it.idx


def render_report(res, with_names=True) -> str:
    """Per-file report. with_names=False: without original names (for an archive
    with neutral_names). Not-cleaned files come first: that's what the user must see."""
    t = res.totals
    out = [tr("BATCH CLEANING REPORT"), "",
           tr("Profile: %s. Neutral names: %s.") % (
               res.profile, tr("yes") if res.neutral_names else tr("no")),
           tr("Total files: %d. Cleaned and verified: %d. NOT cleaned: %d.") % (
               t.get("total", 0), t.get("cleaned", 0), t.get("not_cleaned", 0))]
    if t.get("not_cleaned"):
        out.append(tr("  of those not cleaned: refused %d, format not supported %d, nested "
                      "archives %d, cleanliness not proven %d, rejected by safeguards %d.") % (
                       t[REFUSED], t[UNSUPPORTED], t[NESTED], t[UNPROVEN], t[REJECTED]))
        out.append(
            tr("WARNING: files that were not cleaned %s. They are in their original form, with "
               "all their metadata.") %
            (tr("are in the %s/ folder; don't pass it on") % QUARANTINE_DIR if res.quarantine
             else tr("are NOT included in this archive")))
    out.append("")
    for it in sorted(res.items, key=lambda i: (i.status == CLEANED, i.idx)):
        line = "[%s] %s" % (tr(STATUS_TITLE.get(it.status, it.status)), _label(it, with_names))
        if it.fmt:
            line += "  (%s)" % sniff.pretty(it.fmt)
        out.append(line)
        if it.in_archive:
            out.append(tr("    in the archive as: ") + it.out_name)
        if it.status == CLEANED:
            out.append(tr("    cleaning traces: %s") % (tr(_DETECT[it.detect]) if it.detect in _DETECT else tr("not assessed")))
        elif it.reason:
            out.append(tr("    reason: ") + it.reason)
    return "\n".join(out) + "\n"


# ============================================================================
#  Core
# ============================================================================


def _reason(res_file) -> str:
    msg = res_file.errors[0] if res_file.errors else tr("no reason given")
    msg = msg.replace(tr("File NOT cleaned: "), "").replace(tr("File not cleaned: "), "")
    return clip(msg.replace(tr(" Output file not created."), ""), 300)


def _unsupported(res_file) -> str:
    why = tr("there is no cleaning for format \"%s\"") % sniff.pretty(res_file.fmt)
    if res_file.planned_phase:
        why += tr(" (planned: phase %d)") % res_file.planned_phase
    return why


def _member(st, zf, info, it) -> None:
    """Process one entry. Fills in it; puts it into the output archive when needed."""
    safe = _safe_name(info.filename)
    if safe is None:
        it.reason = tr("unsafe entry name (escapes the folder, drive letter, special characters)")
        return
    if info.flag_bits & 0x1:
        it.reason = tr("the entry is encrypted and can't be read")
        return
    if info.file_size > MAX_MEMBER:
        it.reason = tr("the entry is larger than %d MB") % (MAX_MEMBER >> 20)
        return
    if (info.file_size > MIN_BOMB_UNPACKED and
            info.file_size / max(info.compress_size, 1) > MAX_RATIO):
        it.reason = tr("suspected archive bomb (compression ratio above %d)") % MAX_RATIO
        return

    src = os.path.join(st.work, "in_%04d.bin" % it.idx)
    try:
        _extract(zf, info, src)
    except OverflowError:
        it.reason = tr("the entry grew beyond %d MB when decompressed") % (MAX_MEMBER >> 20)
        return
    except Exception:  # noqa: BLE001 -- broken entry, unsupported compression, CRC
        it.reason = tr("the entry is damaged or won't decompress")
        return

    fmt = sniff.sniff(src)
    it.fmt = fmt
    ext = _ext(fmt)
    # LibreOffice needs the extension (regenerate takes the format from it); the
    # other cleaners look at the content.
    typed = os.path.join(st.work, "in_%04d.%s" % (it.idx, ext))
    os.replace(src, typed)
    src = typed

    kind = _archive_kind(src, fmt)
    if kind:
        it.status = NESTED
        it.reason = (tr("nested archive (%s): contents NOT cleaned, archives are not unpacked") %
                     kind)
    else:
        dst = os.path.join(st.work, "out_%04d.%s" % (it.idx, ext))
        r = clean_file(src, dst, st.profile, st.keep, st.force_signed)
        it.ok, it.clean = bool(r.ok), bool(r.clean)
        it.survived_n, it.detect = len(r.survived), r.detect
        if r.ok and r.clean:
            it.status = CLEANED
            src = dst                            # the result goes into the archive
            if (st.profile is Profile.REGENERATE and r.fmt != fmt):
                ext = _ext(r.fmt)               # doc -> docx: the extension must not lie
        elif r.ok:
            it.status = UNPROVEN
            it.reason = (tr("handed over, but cleanliness not proven: %d original values in the "
                            "output, %d inspector notes") % (len(r.survived), len(r.gaps)))
        elif r.planned_phase or sniff.family(r.fmt) not in _CLEANERS \
                or (st.profile is Profile.REGENERATE and r.planned_phase):
            it.status = UNSUPPORTED
            it.reason = _unsupported(r)
        else:
            it.status = REFUSED
            it.reason = _reason(r)

    # Where to put it. The original of a file that wasn't cleaned goes only to quarantine, and only on request.
    if it.status == CLEANED:
        if st.neutral:
            name = "doc_%04d.%s" % (it.idx, ext)
        else:
            name = safe
            if st.profile is Profile.REGENERATE and ext != _ext(fmt):
                name = os.path.splitext(safe)[0] + "." + ext
        it.out_name = st.out.unique(name)
    elif st.quarantine and it.status != REJECTED:
        if st.neutral:
            name = "%s/%snc_%04d.%s" % (QUARANTINE_DIR, QUARANTINE_PREFIX, it.idx, ext)
        else:
            head, base = os.path.split(safe)
            name = "/".join(x for x in (QUARANTINE_DIR, head, QUARANTINE_PREFIX + base) if x)
        it.out_name = st.out.unique(name)
        src = os.path.join(st.work, "in_%04d.%s" % (it.idx, _ext(fmt)))   # original
    if it.out_name:
        st.out.add_file(it.out_name, src)
        it.in_archive = True


class _State:
    pass


def _run(res, st, dst, profile) -> None:
    try:
        st.profile = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err(tr("Unknown cleaning profile."))
        return
    res.profile = st.profile.value
    src = res.src

    if _same_file(src, dst):
        res.err(tr("The output path matches the input path: the input archive is never "
                   "overwritten."))
        return
    if os.path.isdir(dst):
        res.err(tr("The output path is a directory; a file path is required."))
        return
    if not os.path.isfile(src):
        res.err(tr("The input archive was not found or is not a file."))
        return
    size = os.path.getsize(src)
    if size == 0:
        res.err(tr("The input file is empty (0 bytes); it may not have finished downloading."))
        return
    if size > MAX_INPUT:
        res.err(tr("The archive is larger than %d MB: cleaning not performed.") % (MAX_INPUT >> 20))
        return

    sha_before = _sha(src)
    try:
        zf = zipfile.ZipFile(src)
        infos = [i for i in zf.infolist() if not i.filename.endswith(("/", "\\"))]
    except Exception:  # noqa: BLE001 -- not a ZIP, truncated, junk in the directory
        res.err(tr("The uploaded file is not a ZIP archive or is damaged: nothing to clean."))
        return

    with zf:
        if not infos:
            res.err(tr("The archive is empty: it contains no files."))
            return
        if len(infos) > MAX_FILES:
            res.err(tr("The archive has %d files, more than the limit of %d: cleaning not "
                       "performed. Split it into parts.") % (len(infos), MAX_FILES))
            return
        declared = sum(i.file_size for i in infos)
        if declared > MAX_TOTAL_UNPACKED or (
                declared > MIN_BOMB_UNPACKED and declared / size > MAX_RATIO):
            res.err(tr("Total uncompressed size %d MB with an archive size of %d KB: looks like "
                       "an archive bomb or exceeds the %d MB limit. Cleaning not performed.") %
                    (declared >> 20, size >> 10, MAX_TOTAL_UNPACKED >> 20))
            return

        parent = os.path.dirname(os.path.abspath(dst))
        os.makedirs(parent, exist_ok=True)
        fd, st.tmp_out = tempfile.mkstemp(prefix=".batch_", suffix=".zip", dir=parent)
        os.close(fd)
        st.work = tempfile.mkdtemp(prefix="cleanbatch_")
        st.out = _Out(st.tmp_out)
        st.out.used.add(REPORT_NAME.lower())

        for n, info in enumerate(infos, 1):
            it = BatchItem(idx=n, name=info.filename)
            res.items.append(it)
            try:
                _member(st, zf, info, it)
            except Exception as exc:  # noqa: BLE001 -- a failure on one file doesn't abort the batch
                if it.status == CLEANED and it.in_archive:
                    pass
                else:
                    it.status, it.ok, it.clean, it.in_archive = REFUSED, False, False, False
                    it.reason = tr("internal error while processing: %s") % type(exc).__name__
            finally:
                for f in os.listdir(st.work):          # disk space: one file at a time
                    try:
                        os.remove(os.path.join(st.work, f))
                    except OSError:
                        pass

    res.totals = _totals(res.items)
    st.out.add_text(REPORT_NAME, render_report(res, with_names=not st.neutral))
    st.out.zf.close()
    if _sha(src) != sha_before:
        res.err(tr("The input archive changed during processing: the result is unreliable, "
                   "archive not handed over."))
        return
    try:
        os.replace(st.tmp_out, dst)
    except OSError as exc:
        res.err(tr("Could not write the output archive (errno %s): it's locked or there's no "
                   "permission.") %
                exc.errno)
        return
    st.tmp_out = None
    res.ok = True


def clean_batch(src_zip, dst_zip, profile=Profile.STEALTH, keep=None, force_signed=False,
                *, neutral_names=False, quarantine=False) -> BatchResult:
    """Clean every file of src_zip and build dst_zip. Never raises.

    ok=True: the output archive was written (it always contains _REPORT.txt;
    0 files may have been cleaned, see totals). ok=False: no archive, the reason
    is in errors; an existing dst is untouched. The input archive is never modified."""
    try:
        s, d = os.fsdecode(src_zip), os.fsdecode(dst_zip)
    except Exception:  # noqa: BLE001
        s, d = str(src_zip), str(dst_zip)
    res = BatchResult(src=s, dst=d, profile=str(getattr(profile, "value", profile)),
                      neutral_names=bool(neutral_names), quarantine=bool(quarantine))
    st = _State()
    st.work = st.tmp_out = st.out = None
    st.keep, st.force_signed = keep, force_signed
    st.neutral, st.quarantine = bool(neutral_names), bool(quarantine)
    try:
        _run(res, st, d, profile)
    except Exception as exc:  # noqa: BLE001 -- last line of defense
        res.ok = False
        res.err(tr("Internal batch cleaning error: %s: %s") % (type(exc).__name__, exc))
    finally:
        try:
            if st.out is not None:
                st.out.zf.close()
        except Exception:  # noqa: BLE001
            pass
        if st.work:
            shutil.rmtree(st.work, ignore_errors=True)
        if st.tmp_out:
            try:
                os.remove(st.tmp_out)
            except OSError:
                pass
    res.totals = _totals(res.items)
    if not res.ok:
        for it in res.items:
            it.in_archive = False
    return res


# ============================================================================
#  Self-test: PYTHONIOENCODING=utf-8 python -m core.clean_batch
# ============================================================================

_FAILS = []
_TOTAL = [0]


def _check(name, cond, detail=""):
    _TOTAL[0] += 1
    if not cond:
        _FAILS.append(name)
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           "  -- " + str(detail) if (detail and not cond) else ""))


def _names(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def _selftest() -> int:
    import time
    from tests import fixtures as fx
    from .inspect import inspect_file

    root = tempfile.mkdtemp(prefix="batchtest_")
    tmpdir = os.path.join(root, "tmp")           # tempfile looks here: catching temp-file leaks
    os.makedirs(tmpdir)
    old_tmp, tempfile.tempdir = tempfile.tempdir, tmpdir
    t0 = time.time()
    try:
        fxd = os.path.join(root, "fx")
        os.makedirs(fxd)
        made = {}
        for fn in (fx.make_docx, fx.make_xlsx, fx.make_pptx, fx.make_pdf, fx.make_jpeg,
                   fx.make_png, fx.make_rtf, fx.make_odt, fx.make_doc, fx.make_xls):
            path, exp = fn(fxd)
            made[os.path.basename(path)] = (path, exp)
        nested = os.path.join(fxd, "inner.zip")
        with zipfile.ZipFile(nested, "w") as z:
            z.writestr("secret.docx", b"x")
        notes = os.path.join(fxd, "notes.txt")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("просто текст")

        def mkzip(path, entries):
            import warnings
            with warnings.catch_warnings(), zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                warnings.simplefilter("ignore")      # deliberate duplicate names
                for arc, data in entries:
                    z.writestr(arc, data)
            return path

        def rd(p):
            with open(p, "rb") as fh:
                return fh.read()

        entries = [("Отдел кадров/" + n, rd(p)) for n, (p, _) in made.items()]
        entries += [("inner.zip", rd(nested)), ("notes.txt", rd(notes))]
        mixed = mkzip(os.path.join(root, "mixed.zip"), entries)
        sha_in = _sha(mixed)

        print("\n== mixed batch, stealth and paranoid profiles ==")
        for prof in ("stealth", "paranoid"):
            dst = os.path.join(root, "out_%s.zip" % prof)
            r = clean_batch(mixed, dst, prof)
            print("\n--- profile %s: ok=%s totals=%s" % (prof, r.ok, r.totals))
            print(render_report(r, with_names=True))
            by = {i.name.split("/")[-1]: i for i in r.items}
            _check("%s: ok and input intact" % prof, r.ok and _sha(mixed) == sha_in, r.errors)
            _check("%s: 12 files in total" % prof, r.totals["total"] == 12, r.totals)
            for n in ("leaky.docx", "probe.pdf", "probe.jpg", "probe.png", "probe.rtf",
                      "probe.odt", "book.xlsx", "deck.pptx"):
                if n in by:
                    _check("%s: %s -> %s" % (prof, n, by[n].status), by[n].status == CLEANED,
                           by[n].reason)
            _check("%s: .doc and .xls honestly unsupported" % prof,
                   by["leaky.doc"].status == UNSUPPORTED and by["book.xls"].status == UNSUPPORTED)
            _check("%s: inner.zip is nested, notes.txt unsupported" % prof,
                   by["inner.zip"].status == NESTED and by["notes.txt"].status == UNSUPPORTED)
            names = _names(dst)
            _check("%s: no uncleaned files and no NOT_CLEANED in the archive" % prof,
                   not any(x.endswith((".doc", ".xls", "inner.zip", "notes.txt")) or
                           QUARANTINE_DIR in x for x in names), names)
            _check("%s: report inside the archive" % prof, REPORT_NAME in names)
            _check("%s: entry times are constant" % prof, all(
                z.date_time == _ZIP_TIME for z in zipfile.ZipFile(dst).infolist()))
            # Independent oracle: the planted values aren't found by re-inspection.
            chk = os.path.join(root, "chk")
            os.makedirs(chk, exist_ok=True)
            leaked = []
            with zipfile.ZipFile(dst) as z:
                for it in r.items:
                    if it.status != CLEANED:
                        continue
                    p = os.path.join(chk, os.path.basename(it.out_name))
                    with open(p, "wb") as fh:
                        fh.write(z.read(it.out_name))
                    rep = inspect_file(p)
                    vals = " ".join(str(f.value) for f in rep.findings).lower()
                    exp = made[os.path.basename(it.name)][1]
                    leaked += [v for v in exp.get("identity", []) if str(v).lower() in vals]
            _check("%s: planted identity values not found in the outputs" % prof,
                   not leaked, leaked)

        print("\n== neutral_names + quarantine ==")
        dst = os.path.join(root, "out_neutral.zip")
        r = clean_batch(mixed, dst, neutral_names=True, quarantine=True)
        names = _names(dst)
        print("  archive entries:", names)
        rep = zipfile.ZipFile(dst).read(REPORT_NAME).decode("utf-8")
        orig_parts = {"leaky", "probe", "book", "deck", "inner", "notes", "Отдел", "кадров"}
        _check("neutral: no original names in the archive's names or report",
               not any(p in " ".join(names) + rep for p in orig_parts))
        _check("neutral: originals of uncleaned files in NOT_CLEANED/ with a prefix", all(
            x.startswith(QUARANTINE_DIR + "/" + QUARANTINE_PREFIX) for x in names
            if "nc_" in x) and sum("nc_" in x for x in names) == 4, names)
        _check("neutral: the chat report contains original names",
               "leaky.doc" in render_report(r, with_names=True))
        _check("quarantine: the .doc original is byte-identical to the source", any(
            zipfile.ZipFile(dst).read(x) == rd(made["leaky.doc"][0]) for x in names
            if "nc_" in x))

        print("\n== regenerate (if LibreOffice is available) ==")
        from .clean_regen import find_soffice
        if find_soffice():
            sub = mkzip(os.path.join(root, "regen_in.zip"),
                        [("a.docx", rd(made["leaky.docx"][0])), ("b.doc", rd(made["leaky.doc"][0]))])
            r = clean_batch(sub, os.path.join(root, "regen_out.zip"), Profile.REGENERATE)
            print(render_report(r))
            _check("regenerate: no exceptions, report present", r.ok and len(r.items) == 2, r.errors)
        else:
            print("  LibreOffice not found, skipped")

        print("\n== pathologies ==")
        p = os.path.join(root, "p")
        os.makedirs(p)
        docx = rd(made["leaky.docx"][0])
        cases = {}

        cases["empty archive"] = mkzip(os.path.join(p, "empty.zip"), [])
        with open(os.path.join(p, "notzip.zip"), "wb") as fh:
            fh.write(b"this is not a zip" * 100)
        cases["not a ZIP"] = os.path.join(p, "notzip.zip")
        with open(os.path.join(p, "cut.zip"), "wb") as fh:
            fh.write(rd(mixed)[:5000])
        cases["truncated ZIP"] = os.path.join(p, "cut.zip")
        with open(os.path.join(p, "zero.zip"), "wb"):
            pass
        cases["0 bytes"] = os.path.join(p, "zero.zip")
        cases["100 MB zip bomb"] = mkzip(os.path.join(p, "bomb.zip"),
                                          [("bomb.bin", b"\x00" * (100 << 20))])
        cases["doesn't exist"] = os.path.join(p, "nope.zip")
        cases["directories only"] = mkzip(os.path.join(p, "dirs.zip"), [("a/", b""), ("b/c/", b"")])
        for nm, path in cases.items():
            before = _sha(path) if os.path.isfile(path) else None
            dst = os.path.join(p, "o_%d.zip" % len(nm))
            try:
                r = clean_batch(path, dst)
                ok_exc = True
            except Exception as exc:  # noqa: BLE001
                ok_exc, r = False, None
            _check("%s: no exception, refused, dst not created, input intact" % nm,
                   ok_exc and not r.ok and r.errors and not os.path.exists(dst) and
                   (before is None or _sha(path) == before), r and r.errors)
            if r:
                print("      ->", r.errors[0])

        # path traversal: one normal docx inside
        trav = mkzip(os.path.join(p, "trav.zip"), [
            ("../evil.docx", docx), ("/abs.docx", docx), ("C:\\win.docx", docx),
            ("a/../../x.docx", docx), ("ads.docx:stream", docx), ("ok.docx", docx)])
        dst = os.path.join(p, "trav_out.zip")
        r = clean_batch(trav, dst)
        print(render_report(r))
        _check("traversal: 5 dangerous rejected, 1 normal cleaned",
               r.ok and r.count(REJECTED) == 5 and r.count(CLEANED) == 1, r.totals)
        _check("traversal: only ok.docx and the report in the output archive",
               sorted(_names(dst)) == sorted(["ok.docx", REPORT_NAME]), _names(dst))
        _check("traversal: nothing written outside the folder",
               not os.path.exists(os.path.join(root, "evil.docx")) and
               not os.path.exists(os.path.join(os.path.dirname(root), "evil.docx")))

        # a nested archive next to a normal one
        r = clean_batch(mkzip(os.path.join(p, "nest.zip"),
                              [("inner.zip", rd(nested)), ("ok.docx", docx), ("a.7z", b"7z\xbc\xaf\x27\x1c" + b"\0" * 50)]),
                        os.path.join(p, "nest_out.zip"))
        _check("nested: both flagged, not unpacked, docx cleaned",
               r.ok and r.count(NESTED) == 2 and r.count(CLEANED) == 1, r.totals)
        print("      ->", [i.reason for i in r.items if i.status == NESTED][0])

        # same-name entries
        r = clean_batch(mkzip(os.path.join(p, "dup.zip"), [("a.docx", docx), ("A.DOCX", docx), ("a.docx", docx)]),
                        os.path.join(p, "dup_out.zip"))
        n = _names(os.path.join(p, "dup_out.zip"))
        _check("duplicate names don't overwrite each other", r.count(CLEANED) == 3 and len(n) == 4, n)

        # 200 files
        pdf, png = rd(made["probe.pdf"][0]), rd(made["probe.png"][0])
        many = [("d%03d/f%03d.%s" % (i // 20, i, "docx" if i % 3 == 0 else "pdf" if i % 3 == 1 else "png"),
                 docx if i % 3 == 0 else pdf if i % 3 == 1 else png) for i in range(200)]
        mp = mkzip(os.path.join(p, "many.zip"), many)
        s_many = _sha(mp)
        t1 = time.time()
        r = clean_batch(mp, os.path.join(p, "many_out.zip"))
        print("  200 files: %.1f s, totals=%s" % (time.time() - t1, r.totals))
        _check("200 files: all cleaned, input intact, 201 entries in the archive",
               r.ok and r.count(CLEANED) == 200 and _sha(mp) == s_many and
               len(_names(os.path.join(p, "many_out.zip"))) == 201, r.totals)

        # limits via patched constants
        global MAX_FILES, MAX_MEMBER, clean_file
        keep_f, keep_m, keep_c = MAX_FILES, MAX_MEMBER, clean_file
        try:
            MAX_FILES = 3
            r = clean_batch(mp, os.path.join(p, "lim_out.zip"))
            _check("file count limit: the whole batch is refused", not r.ok and "files" in r.errors[0], r.errors)
            MAX_FILES, MAX_MEMBER = keep_f, 1000
            r = clean_batch(mixed, os.path.join(p, "lim2_out.zip"))
            _check("entry size limit: large ones rejected, small ones processed",
                   r.ok and r.count(REJECTED) >= 5, r.totals)
            MAX_MEMBER = keep_m

            # a dispatcher failure on one file doesn't abort the batch
            calls = [0]

            def flaky(*a, **k):
                calls[0] += 1
                if calls[0] == 2:
                    raise RuntimeError("boom")
                return keep_c(*a, **k)
            clean_file = flaky
            r = clean_batch(mkzip(os.path.join(p, "flaky.zip"), [("1.docx", docx), ("2.docx", docx), ("3.docx", docx)]),
                            os.path.join(p, "flaky_out.zip"))
            _check("exception on one file: the rest are cleaned, it's flagged",
                   r.ok and r.count(CLEANED) == 2 and r.count(REFUSED) == 1, r.totals)
        finally:
            MAX_FILES, MAX_MEMBER, clean_file = keep_f, keep_m, keep_c

        # nothing cleaned: the archive is still handed over (with the report), the batch isn't "successful"
        r = clean_batch(mkzip(os.path.join(p, "onlydoc.zip"), [("x.doc", rd(made["leaky.doc"][0]))]),
                        os.path.join(p, "onlydoc_out.zip"))
        _check("nothing cleaned: ok, only the report in the archive, all_cleaned=False",
               r.ok and not r.all_cleaned and _names(os.path.join(p, "onlydoc_out.zip")) == [REPORT_NAME])

        # dst == src
        cp = os.path.join(p, "same.zip")
        shutil.copy(trav, cp)
        s0 = _sha(cp)
        r = clean_batch(cp, cp)
        _check("dst == src rejected, input intact", not r.ok and _sha(cp) == s0, r.errors)

        left = [x for x in os.listdir(tmpdir)]
        junk = [x for x in os.listdir(p) + os.listdir(root) if x.startswith(".batch_")]
        _check("temp directories and .batch_* removed", not left and not junk, (left, junk))
    finally:
        tempfile.tempdir = old_tmp
        shutil.rmtree(root, ignore_errors=True)

    print("\nTOTAL: %d checks, %d failed (%.1f s)" % (_TOTAL[0], len(_FAILS), time.time() - t0))
    for f in _FAILS:
        print("  FAIL:", f)
    return 1 if _FAILS else 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="python -m core.clean_batch",
                                 description="Batch ZIP cleaning. With no arguments: self-test.")
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--profile", choices=[p.value for p in Profile], default="stealth")
    ap.add_argument("--neutral-names", action="store_true")
    ap.add_argument("--quarantine", action="store_true")
    a = ap.parse_args(argv)
    if not a.src:
        return _selftest()
    if not a.dst:
        ap.error("an output archive is required")
    r = clean_batch(a.src, a.dst, a.profile, neutral_names=a.neutral_names,
                    quarantine=a.quarantine)
    for e in r.errors:
        print(e)
    if r.ok:
        print(render_report(r))
    return 0 if r.all_cleaned else 1


if __name__ == "__main__":
    raise SystemExit(main())
