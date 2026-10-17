# -*- coding: utf-8 -*-
"""Inspection dispatcher and CLI.

Phase 0: read only. The file is never modified and nothing is cleaned; the
dispatcher's only job is to assemble the full picture from the inspectors.

Detects the format via core.sniff and routes by core.sniff.family to one of
the inspectors (core.insp_*). The dispatcher itself uses only the stdlib;
heavy dependencies (pikepdf, lxml, olefile) come in transitively through
the inspectors it calls.

Main function:
    inspect_file(path, max_size=None) -> Report

The dispatcher MUST NOT crash: the bot calls it on any file a user sends,
including deliberately broken and deliberately dangerous ones. Every
inspector call is wrapped in try/except; if an inspector does raise (it
shouldn't, but the world isn't perfect), that also goes to report.errors,
not out.

Formats without a dedicated inspector (generic ZIP, text/CSV, unrecognized)
get minimal parsing right here; a separate module for them would be overkill.

Recursion. OOXML and ODF have their own media folders with images (their
Exif/XMP/ICC live there, not in docProps), OOXML additionally has embedded
OLE objects in */embeddings/, and PDF has embedded files in /Names
/EmbeddedFiles. The inspectors only report these ("needs recursive
inspection"); extracting and recursively inspecting them is the
dispatcher's job: the insp_ooxml.py and insp_legacy.py contract explicitly
hands this work over. Recursion is bounded by depth, number of embedded
objects and total extracted size; see the constants below.

CLI: python -m core.inspect <file> [more files...] [--json | --quiet] [--selftest]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from enum import Enum

from .model import Report, Risk
from . import sniff
from .insp_ooxml import inspect_ooxml
from .insp_pdf import inspect_pdf
from .insp_image import inspect_image
from .insp_legacy import inspect_ole, inspect_odf, inspect_rtf
from .report import render_plain, summary_line
from core.i18n import N_, tr

# --- recursion limits: an embedded object is just as untrusted as the
# top-level file, so bombs and object-count inflation are cut just as hard.
MAX_RECURSE_DEPTH = 2          # media in a docx inside a docx inside a docx, etc.: at most 2 levels
MAX_RECURSE_OBJECTS = 40       # embedded objects in total for the whole inspection of a file
MAX_RECURSE_TOTAL = 200 * 1024 * 1024   # total extracted size
MAX_ITEM_READ = 50 * 1024 * 1024        # one embedded object: never more than this in memory

# --- limits of the minimal inspectors (zip/text) -----------------------------
MAX_ZIP_SCAN = 4000             # archive entries we parse at all
MAX_ZIP_LISTED = 40             # how many timestamps we print by name
MAX_ZIP_UNPACKED = 512 * 1024 * 1024   # bomb threshold: total uncompressed size
MIN_ZIP_BOMB = 64 * 1024 * 1024        # below this size a high ratio is not alarming
MAX_ZIP_RATIO = 120                    # uncompressed/compressed: bomb suspicion threshold
MAX_TEXT_PEEK = 1024 * 1024      # the whole file isn't needed for the BOM and line-ending style

_OOXML_MEDIA_RE = re.compile(r"^(word|xl|ppt)/media/")


# ============================================================================
#  Minimal inspectors: formats without a dedicated module
# ============================================================================


def _inspect_zip_min(path, fmt="zip") -> Report:
    """Generic ZIP (not OOXML or ODF; sniff detects those earlier).

    Entry contents are never unpacked, only the listing and the sizes and
    timestamps declared in the headers. So the compression ratio here isn't
    protection against a real bomb (there's no unpacking), just a warning to
    the user that the archive looks suspicious.
    """
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)
    except OSError as exc:
        rep.err(tr("could not get the file size: %s") % exc)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            total = len(infos)
            if total > MAX_ZIP_SCAN:
                rep.err(tr("the archive has %d entries, the first %d were parsed") % (total, MAX_ZIP_SCAN))
                infos = infos[:MAX_ZIP_SCAN]
            total_unc = sum(i.file_size for i in infos)
            total_comp = sum(i.compress_size for i in infos)
            ratio = (total_unc / total_comp) if total_comp else 0
            if total_unc > MAX_ZIP_UNPACKED or (total_unc > MIN_ZIP_BOMB and ratio > MAX_ZIP_RATIO):
                rep.err(tr("the archive looks like a zip bomb: %d bytes declared at a "
                           "compression ratio of %.0f; entry contents are not unpacked") % (total_unc, ratio))
                rep.signal("hazard",
                           tr("ZIP archive with a suspiciously high compression ratio (%.0f).") % ratio,
                           "high")
            rep.add(Risk.PROVENANCE, "ZIP", tr("Entries in the archive"), str(total))
            for info in infos[:MAX_ZIP_LISTED]:
                dt = "%04d-%02d-%02d %02d:%02d:%02d" % info.date_time
                rep.add(Risk.PROVENANCE, info.filename, tr("ZIP entry timestamp"), dt,
                        tr("Modification time of a file inside the archive: a build stamp that "
                           "ties the archive to when it was created."))
            if total > MAX_ZIP_LISTED:
                rep.err(tr("timestamps shown for the first %d of %d entries") % (MAX_ZIP_LISTED, total))
    except (zipfile.BadZipFile, OSError) as exc:
        rep.err(tr("ZIP archive won't open: %s: %s") % (type(exc).__name__, exc))
    return rep


def _inspect_text_min(path, fmt="text") -> Report:
    """Text/CSV: BOM and line-ending style only. We don't go into the text:
    arbitrary text content isn't document metadata."""
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)
    except OSError as exc:
        rep.err(tr("could not get the file size: %s") % exc)
    try:
        with open(path, "rb") as fh:
            head = fh.read(MAX_TEXT_PEEK)
    except OSError as exc:
        rep.err(tr("the file can't be read: %s") % exc)
        return rep

    bom = None
    for label, sig in (("UTF-8 BOM", b"\xef\xbb\xbf"),
                       ("UTF-16 LE BOM", b"\xff\xfe"),
                       ("UTF-16 BE BOM", b"\xfe\xff")):
        if head.startswith(sig):
            bom = label
            break
    rep.add(Risk.PROVENANCE, "BOM", tr("Byte order mark"), bom or tr("none"),
            tr("A BOM reveals the editor or platform the file was saved with.") if bom else "")

    crlf = head.count(b"\r\n")
    lf_only = head.count(b"\n") - crlf
    if crlf and not lf_only:
        style = tr("CRLF (typical of Windows)")
    elif lf_only and not crlf:
        style = tr("LF (typical of Unix/Mac)")
    elif crlf and lf_only:
        style = tr("mixed CRLF and LF")
    else:
        style = tr("no line breaks")
    rep.add(Risk.ENVIRONMENT, "line-endings", tr("Line-ending style"), style,
            tr("CRLF almost always means the file was edited or built on Windows."))

    if rep.size and rep.size > len(head):
        rep.err(tr("the file is larger than %d MB; BOM and line endings were determined from the "
                   "first %d MB")
                % (MAX_TEXT_PEEK // 1048576, MAX_TEXT_PEEK // 1048576))
    return rep


def _inspect_unknown_min(path, fmt="unknown") -> Report:
    """Format not recognized by signature. There's no point digging deeper than
    the signature and size: there's no way to know how to parse it."""
    rep = Report(path=str(path), fmt=fmt)
    size_known = False
    try:
        rep.size = os.path.getsize(path)
        size_known = True
    except OSError as exc:
        rep.err(tr("could not get the file size: %s") % exc)
    if size_known and rep.size == 0:
        # An empty file isn't "unrecognized format": there's nothing to recognize.
        rep.err(tr("the file is empty (0 bytes): it holds neither data nor metadata. It may not "
                   "have finished downloading or saving; check the source."))
        return rep
    rep.err(tr("format not recognized by signature: deep inspection is impossible, only the size "
               "and the fact that it wasn't recognized are known."))
    return rep


# What to tell the user when a file didn't open. The truth and an action, no
# paths: the OSError exception text contains the full path, so it isn't used.
_UNREADABLE_MSG = {
    "cloud": (
        N_("the file is a cloud provider placeholder (OneDrive and the like, Files On-Demand "
           "mode): only metadata is on disk, there's no content, so it can't be checked. This "
           "doesn't mean the file is damaged. Open the file in Explorer or choose \"Always keep "
           "on this device\", wait for the download and check again.")),
    "denied": (
        N_("no read access (errno 13): either there's no permission for the file or it's locked "
           "by another process (most often open in Word or Excel). Close the file, check "
           "permissions and try again.")),
    "missing": N_("file not found (deleted or renamed before the check started)."),
    "dir": N_("this is a directory, not a file."),
    "reparse": (
        N_("the file is a link or reparse point; its content can't be read. If the file comes "
           "from the cloud, open it in Explorer, wait for the download and check again.")),
}


def _inspect_unreadable(path, fmt="unreadable") -> Report:
    """The file exists but didn't open. Not 'unrecognized' (a false diagnosis
    that sends the user hunting for a nonexistent defect) but the honest
    reason and what to do."""
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)   # a placeholder reports its real size
    except OSError:
        pass
    reason, code = sniff.unreadable_reason(path)
    msg = tr(_UNREADABLE_MSG[reason]) if reason in _UNREADABLE_MSG else None
    if msg is None:
        try:
            text = os.strerror(code) if code is not None else ""
        except (ValueError, OverflowError):
            text = ""
        what = ("errno %s%s" % (code, ": " + text if text else "")) if code is not None             else tr("OS error code not available")
        msg = tr("the file could not be read (%s): content unavailable, the check is impossible.") % what
    rep.err(msg)
    return rep


_HANDLERS = {
    "ooxml": lambda p, f: inspect_ooxml(p, f),
    "odf": lambda p, f: inspect_odf(p, f),
    "pdf": lambda p, f: inspect_pdf(p),
    "image": lambda p, f: inspect_image(p, f),
    "ole": lambda p, f: inspect_ole(p, f),
    "rtf": lambda p, f: inspect_rtf(p),
    "zip": _inspect_zip_min,
    "text": _inspect_text_min,
    "unknown": _inspect_unknown_min,
    "unreadable": _inspect_unreadable,
}


def _run_inspector(path, fam, fmt) -> Report:
    """Call the right inspector, catching absolutely everything. The inspector
    contract says they never raise themselves, but the dispatcher has no right
    to take that on faith and crash if the contract is broken somewhere."""
    handler = _HANDLERS.get(fam, _inspect_unknown_min)
    try:
        return handler(path, fmt)
    except Exception as exc:  # noqa: BLE001 -- the dispatcher's last line of defense
        rep = Report(path=str(path), fmt=fmt)
        try:
            rep.size = os.path.getsize(path)
        except OSError:
            pass
        rep.err(tr("the inspector crashed entirely: %s: %s") % (type(exc).__name__, exc))
        return rep


# ============================================================================
#  Recursion into embedded objects
# ============================================================================


def _zip_entry_bytes(zf, info, cap):
    """Read a ZIP entry as a stream, at most cap bytes. None if the entry is
    larger: the size declared in the header is untrusted (it can lie either
    way), so the read stream itself is bounded, not the header."""
    try:
        with zf.open(info) as fh:
            data = fh.read(cap + 1)
    except Exception:
        return None
    if len(data) > cap:
        return None
    return data


def _write_tmp(tmpdir, index, name):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name)) or "part"
    return os.path.join(tmpdir, "%03d_%s" % (index, base))


def _reserve(budget):
    """How many bytes may be read for the next embedded object, or None if the
    overall recursion budget (object count or total size) is exhausted."""
    if budget["objects"] >= MAX_RECURSE_OBJECTS:
        return None
    remaining = MAX_RECURSE_TOTAL - budget["bytes"]
    if remaining <= 0:
        return None
    return min(MAX_ITEM_READ, remaining)


def _extract_zip_children(rep, path, tmpdir, budget, match, kind_label):
    """Shared part for OOXML (media/, */embeddings/) and ODF (Pictures/):
    both containers are plain ZIP; only the interesting names differ."""
    out = []
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        rep.err(tr("recursion: %s container failed to reopen: %s") % (kind_label, exc))
        return out
    with zf:
        idx = 0
        for scanned, info in enumerate(zf.infolist()):
            if scanned >= MAX_ZIP_SCAN:
                break
            name = info.filename
            # A directory entry ("word/media/") is not an embedded object: its empty
            # body produced a "file is empty" error, and the file could never be proven clean.
            if name.endswith("/") or not match(name):
                continue
            cap = _reserve(budget)
            if cap is None:
                rep.err(tr("recursion: embedded-object budget exhausted in %s; some embedded "
                           "items were not inspected") % kind_label)
                break
            data = _zip_entry_bytes(zf, info, cap)
            if data is None:
                rep.err(tr("recursion: %s exceeds the %d MB limit, skipped") % (name, cap // 1048576))
                continue
            idx += 1
            budget["objects"] += 1
            budget["bytes"] += len(data)
            dest = _write_tmp(tmpdir, idx, name)
            with open(dest, "wb") as fh:
                fh.write(data)
            out.append((name, dest))
    return out


def _pdf_walk_names(node, out, depth, seen, calls):
    """Walk the /EmbeddedFiles /Names tree. Depth and visited objgens are
    bounded as in insp_pdf._walk_name_tree: /Kids can point back to the same
    node in a loop (a DAG bomb), not just deeper."""
    if depth > 8 or calls[0] <= 0 or len(out) >= MAX_RECURSE_OBJECTS:
        return
    calls[0] -= 1
    try:
        key = node.objgen
    except Exception:
        key = None
    if key and key != (0, 0):
        if key in seen:
            return
        seen.add(key)
    try:
        arr = node.get("/Names")
        if arr is not None:
            items = list(arr)
            for i in range(0, len(items) - 1, 2):
                out.append((str(items[i]), items[i + 1]))
        kids = node.get("/Kids")
        if kids is not None:
            for kid in list(kids)[:64]:
                _pdf_walk_names(kid, out, depth + 1, seen, calls)
    except Exception:
        pass


def _extract_pdf_children(rep, path, tmpdir, budget):
    """PDF embedded files: /Names /EmbeddedFiles. insp_pdf already lists their
    names and metadata, but not the bytes; the dispatcher needs the bytes to
    run the attachment through the matching inspector, so the name tree has
    to be walked a second time, here."""
    out = []
    import pikepdf  # already a required dependency of the PDF inspector

    try:
        pdf = pikepdf.open(path)
    except Exception as exc:
        rep.err(tr("recursion: the PDF failed to reopen: %s") % exc)
        return out
    idx = 0
    try:
        with pdf:
            try:
                names = pdf.Root.get("/Names")
                tree = names.get("/EmbeddedFiles") if names is not None else None
            except Exception:
                tree = None
            if tree is None:
                return out
            found = []
            _pdf_walk_names(tree, found, 0, set(), [4000])
            for label, spec in found:
                cap = _reserve(budget)
                if cap is None:
                    rep.err(tr("recursion: embedded-object budget exhausted in the PDF; some "
                               "attachments were not inspected"))
                    break
                try:
                    ef = spec.get("/EF")
                    stream = (ef.get("/F") or ef.get("/UF")) if ef is not None else None
                    if stream is None:
                        continue
                    # check the raw (compressed) size first: the same bomb protection
                    # as in insp_pdf._stream_text, before full decompression.
                    if len(stream.read_raw_bytes()) > cap:
                        rep.err(tr("recursion: PDF attachment %r exceeds the %d MB limit, skipped")
                                % (label, cap // 1048576))
                        continue
                    data = stream.read_bytes()
                    if len(data) > cap:
                        rep.err(tr("recursion: PDF attachment %r decompressed beyond the %d MB "
                                   "limit, skipped") % (label, cap // 1048576))
                        continue
                except Exception as exc:
                    rep.err(tr("recursion: PDF attachment %r can't be read: %s") % (label, exc))
                    continue
                idx += 1
                budget["objects"] += 1
                budget["bytes"] += len(data)
                dest = _write_tmp(tmpdir, idx, label or ("attach%d" % idx))
                with open(dest, "wb") as fh:
                    fh.write(data)
                out.append((label or tr("attachment %d") % idx, dest))
    except Exception as exc:
        rep.err(tr("recursion: walking PDF attachments was interrupted: %s") % exc)
    return out


def _merge(rep, child_rep, label):
    """Merge the findings of an embedded report into the main one, prefixing
    the location with the part name: "word/media/image1.jpeg > APP1:Exif"."""
    for f in child_rep.findings:
        loc = "%s > %s" % (label, f.location) if f.location else label
        rep.add(f.risk, loc, f.label, f.value, f.note, f.removable)
    for s in child_rep.signals:
        rep.signal(s.kind, "%s: %s" % (label, s.detail), s.confidence)
    for e in child_rep.errors:
        rep.err("%s: %s" % (label, e))
    rep.parts.append({"name": label, "size": child_rep.size, "kind": "nested:%s" % child_rep.fmt})


def _recurse_into(rep, path, fam, fmt, prefix, depth, budget, tmpdir):
    if depth > MAX_RECURSE_DEPTH:
        return
    if fam == "ooxml":
        children = _extract_zip_children(
            rep, path, tmpdir, budget,
            lambda n: bool(_OOXML_MEDIA_RE.match(n)) or "/embeddings/" in n,
            "OOXML")
    elif fam == "odf":
        children = _extract_zip_children(
            rep, path, tmpdir, budget,
            lambda n: n.startswith("Pictures/") and not n.endswith("/"),
            "ODF")
    elif fam == "pdf":
        children = _extract_pdf_children(rep, path, tmpdir, budget)
    else:
        return

    for name, child_path in children:
        label = name if not prefix else "%s > %s" % (prefix, name)
        try:
            child_fmt = sniff.sniff(child_path)
            child_fam = sniff.family(child_fmt)
            child_rep = _run_inspector(child_path, child_fam, child_fmt)
            _merge(rep, child_rep, label)
            if depth < MAX_RECURSE_DEPTH:
                _recurse_into(rep, child_path, child_fam, child_fmt, label, depth + 1, budget, tmpdir)
        except Exception as exc:  # noqa: BLE001 -- one embedded object must not take down the rest
            rep.err(tr("%s: recursive inspection of the attachment crashed: %s: %s") % (label, type(exc).__name__, exc))


# ============================================================================
#  Public entry point
# ============================================================================


def inspect_file(path, max_size=None) -> Report:
    """Full read-only inspection of one file. Never raises: this is the only
    function the bot calls, and it must not crash on any input.

    max_size: if set and the file is larger, inspection doesn't run at all
    (not even the signature or embedded objects are parsed): the report gets
    only the size and an entry in errors. This protects the bot from parsing
    huge uploads, not from bombs inside a small file; recursion below guards
    against those.
    """
    path = str(path)
    try:
        fmt = sniff.sniff(path)
    except Exception:
        fmt = "unknown"
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0

    if max_size is not None and size > max_size:
        rep = Report(path=path, fmt=fmt, size=size)
        rep.err(tr("the file exceeds the %d-byte limit (actual size %d bytes); inspection was "
                   "not run") % (max_size, size))
        return rep

    fam = sniff.family(fmt)
    rep = _run_inspector(path, fam, fmt)

    tmpdir = None
    try:
        # mkdtemp is inside the try too: no space or permissions in %TEMP% is an
        # entry in errors, not an exception from a function that must never raise.
        tmpdir = tempfile.mkdtemp(prefix="inspect_")
        _recurse_into(rep, path, fam, fmt, "", 1, {"objects": 0, "bytes": 0}, tmpdir)
    except Exception as exc:  # noqa: BLE001 -- recursion must not take down the main report
        rep.err(tr("recursive inspection of embedded items crashed entirely: %s: %s") % (type(exc).__name__, exc))
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return rep


# ============================================================================
#  CLI
# ============================================================================


def _json_default(obj):
    """Risk is a str Enum, json already handles it as a string; this is in case
    something else json doesn't understand natively ever ends up in a report."""
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)


def _to_json(report) -> str:
    return json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2,
                       default=_json_default)


def _selftest():
    """Dispatcher self-test: routing by format, the minimal inspectors,
    recursion, and that the dispatcher doesn't crash on broken input.
    Doesn't replace a run on a real file; it complements one."""
    tmp = tempfile.mkdtemp(prefix="inspect_selftest_")
    try:
        # 1. Unrecognized format: no crash, size known, errors present.
        junk_data = b"\x01\x02\x03garbage\x00\xff" * 10
        junk = os.path.join(tmp, "junk.bin")
        with open(junk, "wb") as fh:
            fh.write(junk_data)
        rep = inspect_file(junk)
        assert rep.fmt == "unknown", rep.fmt
        assert rep.size == len(junk_data), rep.size
        assert rep.errors

        # 2. Text: BOM + CRLF.
        txt = os.path.join(tmp, "notes.txt")
        with open(txt, "wb") as fh:
            fh.write(b"\xef\xbb\xbfline1\r\nline2\r\n")
        rep = inspect_file(txt)
        assert rep.fmt == "text", rep.fmt
        values = {f.label: f.value for f in rep.findings}
        assert values.get("Byte order mark") == "UTF-8 BOM", values
        assert "CRLF" in values.get("Line-ending style", ""), values

        # 3. Generic ZIP: the entry and its timestamp make it into the report.
        zpath = os.path.join(tmp, "arch.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("readme.txt", b"hello")
        rep = inspect_file(zpath)
        assert rep.fmt == "zip", rep.fmt
        assert any(f.location == "readme.txt" for f in rep.findings), rep.findings

        # 4. max_size stops inspection before the content is parsed.
        rep = inspect_file(zpath, max_size=1)
        assert rep.errors and not rep.findings, rep

        # 5. Recursion: an image in word/media/ is merged into the docx report
        #    as a separate embedded object (part).
        docx = os.path.join(tmp, "sample.docx")
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types xmlns='x'/>")
            zf.writestr("word/document.xml", "<w:document xmlns:w='x'/>")
            zf.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        rep = inspect_file(docx)
        assert rep.fmt == "docx", rep.fmt
        assert any(p["name"] == "word/media/image1.png" for p in rep.parts), rep.parts

        # 6. A file with a ZIP signature but broken content: the dispatcher doesn't crash.
        broken = os.path.join(tmp, "broken.docx")
        with open(broken, "wb") as fh:
            fh.write(b"PK\x03\x04" + os.urandom(64))
        rep = inspect_file(broken)
        assert isinstance(rep, Report)

        # 7. Empty file: not "unrecognized format" but "empty".
        empty = os.path.join(tmp, "empty.docx")
        open(empty, "wb").close()
        rep = inspect_file(empty)
        assert rep.fmt == "unknown" and rep.size == 0, (rep.fmt, rep.size)
        assert any("empty" in e for e in rep.errors), rep.errors

        # 8. The file won't open: each reason gets its own message, with no path
        #    and no false "unrecognized format". CI has no real placeholders,
        #    so the sniff seams are patched: head reading and Windows attributes.
        real = os.path.join(tmp, "doc.bin")
        with open(real, "wb") as fh:
            fh.write(b"x" * 321)

        def _case(exc, attrs, want):
            old_read, old_attrs = sniff._read_head, sniff._win_attrs
            def _boom(_p):
                raise exc
            sniff._read_head = _boom
            sniff._win_attrs = lambda _p: attrs
            try:
                rep = inspect_file(real)
            finally:
                sniff._read_head, sniff._win_attrs = old_read, old_attrs
            assert rep.fmt == "unreadable", rep.fmt
            text = " | ".join(rep.errors)
            assert want in text, (want, text)
            assert "not recognized" not in text, text
            assert tmp not in text and "doc.bin" not in text, text   # the path didn't leak
            return rep

        rep = _case(OSError(22, "Invalid argument"), 0x00400020, "placeholder")
        assert rep.size == 321, rep.size
        assert "Always keep on this device" in rep.errors[0]
        _case(OSError(22, "x"), 0x00040000, "placeholder")       # RECALL_ON_OPEN
        _case(OSError(22, "x"), 0x00001020, "placeholder")       # OFFLINE
        _case(OSError(22, "x"), 0x00000420, "reparse point")     # a lone link isn't the cloud
        _case(OSError(22, "x"), 0x20, "errno 22")                # attributes present, no cloud
        _case(OSError(22, "x"), None, "errno 22")                # not Windows / the call failed
        _case(PermissionError(13, "x"), 0x20, "no read access")
        _case(FileNotFoundError(2, "x"), None, "not found")
        rep = inspect_file(os.path.join(tmp, "no_such_file.docx"))   # for real
        assert rep.fmt == "unreadable" and any("not found" in e for e in rep.errors), rep.errors
        rep = inspect_file(tmp)                                      # directory
        assert rep.fmt == "unreadable" and any("directory" in e for e in rep.errors), rep.errors

        # 9. sniff on an unreadable file doesn't raise and isn't confused with 'unknown'.
        assert sniff.sniff(os.path.join(tmp, "no_such_file.docx")) == "unreadable"
        assert sniff.sniff("bad" + chr(0) + "path") == "unreadable"
        assert sniff.unreadable_reason("bad" + chr(0) + "path") == ("other", None)

        print("OK: dispatcher self-test passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    # The Windows console defaults to cp866/cp1251, and a report with non-ASCII
    # text crashes on it with UnicodeEncodeError. Re-encode the output stream
    # right here, before the first print.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="python -m core.inspect",
        description="Document metadata inspection (read-only, phase 0).")
    parser.add_argument("files", nargs="*", help="path(s) to file(s)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--quiet", action="store_true", help="print only the summary line")
    parser.add_argument("--selftest", action="store_true",
                        help="dispatcher self-test without input files")
    args = parser.parse_args(argv)

    if args.selftest:
        _selftest()
        return 0
    if not args.files:
        parser.error("at least one file is required (or --selftest)")

    exit_code = 0
    for path in args.files:
        try:
            report = inspect_file(path)
        except Exception as exc:  # noqa: BLE001 -- the CLI must not crash even if the contract is broken
            print("Could not inspect %r: %s: %s" % (path, type(exc).__name__, exc))
            exit_code = 1
            continue
        if args.json:
            print(_to_json(report))
        elif args.quiet:
            print(summary_line(report))
        else:
            print(render_plain(report))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
