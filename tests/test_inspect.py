# -*- coding: utf-8 -*-
"""Phase 0 self-test on synthetic fixtures with metadata known in advance. The
only guard against regressions in all the inspectors at once.

No pytest (the machine doesn't have it), but compatible with it: plain test_*
functions with asserts inside. Run:

    PYTHONIOENCODING=utf-8 python -m tests.test_inspect

PYTHONIOENCODING=utf-8 is required on Windows: otherwise the console
(cp866/cp1251) mangles Cyrillic in the output, and that looks like a bug in
the code rather than a console artifact.

Prints one line per check and a summary at the end; exit code 1 if even one
check fails.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import olefile

from core import sniff
from core.insp_legacy import inspect_ole
from core.inspect import inspect_file
from core.model import Report, Risk
from core.report import render_plain, render_telegram, summary_line, TG_LIMIT
from core.report import _tg_len  # a private helper, but this is exactly how Telegram counts

from tests import fixtures

# ---------------------------------------------------------------------------
# result bookkeeping
# ---------------------------------------------------------------------------

_RESULTS = []  # (check name, passed?, explanation)


def _check(name, condition, detail=""):
    _RESULTS.append((name, bool(condition), detail))
    mark = "OK  " if condition else "FAIL"
    line = "%s %s" % (mark, name)
    if not condition and detail:
        line += "  -- %s" % detail
    print(line)
    return bool(condition)


# ---------------------------------------------------------------------------
# small test helpers
# ---------------------------------------------------------------------------


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _risk_values(report, risk):
    return [f.value for f in report.findings if f.risk == risk]


def _gps_match(report, lat, lon, tol=1e-3):
    for f in report.findings:
        m = re.match(r"^(-?\d+\.\d+),\s*(-?\d+\.\d+)", f.value)
        if not m:
            continue
        flat, flon = float(m.group(1)), float(m.group(2))
        if abs(flat - lat) < tol and abs(flon - lon) < tol:
            return True
    return False


def _check_idempotent(tag, path):
    before = _sha256(path)
    inspect_file(path)
    after = _sha256(path)
    _check("%s: the file was not modified by inspection (sha256 before==after)" % tag, before == after)


def _check_fixture(tag, path, expected, fmt_hint):
    """Common harness for one "healthy" fixture: sniff, idempotence, planted
    values, risk categories, signals. Returns the Report so the calling test can
    add its own specific checks."""
    before = _sha256(path)
    sniffed = sniff.sniff(path)
    _check("%s: sniff detected the format (%s)" % (tag, fmt_hint),
           sniffed == fmt_hint, "sniff returned %r" % sniffed)

    rep = inspect_file(path)
    after = _sha256(path)
    _check("%s: read-only -- the file's sha256 did not change" % tag, before == after)
    _check("%s: inspect_file did not raise, a Report came back" % tag,
           rep is not None and rep.fmt == fmt_hint or rep is not None)

    blob = " | ".join(f.value for f in rep.findings)
    for v in expected.get("values", []):
        _check("%s: planted value found %r" % (tag, v[:50]), v in blob)

    identity_vals = _risk_values(rep, Risk.IDENTITY)
    for v in expected.get("identity", []):
        _check("%s: %r classified as IDENTITY" % (tag, v[:40]),
               any(v in iv for iv in identity_vals))

    env_vals = _risk_values(rep, Risk.ENVIRONMENT)
    for v in expected.get("environment", []):
        _check("%s: %r classified as ENVIRONMENT" % (tag, v[:40]),
               any(v in ev for ev in env_vals))

    sig_kinds = {s.kind for s in rep.signals}
    for k in expected.get("signals", []):
        _check("%s: signal kind=%s is present" % (tag, k), k in sig_kinds,
               "report signals: %s" % sorted(sig_kinds))
    for k in expected.get("not_signals", []):
        _check("%s: NO false signal kind=%s (false positive guard)" % (tag, k),
               k not in sig_kinds, "report signals: %s" % sorted(sig_kinds))

    if expected.get("gps"):
        lat, lon = expected["gps"]
        _check("%s: GPS converted to decimal correctly (within tolerance)" % tag,
               _gps_match(rep, lat, lon))

    return rep


# ---------------------------------------------------------------------------
# fixture tests
# ---------------------------------------------------------------------------


def _with_tmp(fn):
    """Each test_* creates and removes its own temporary directory, so the
    functions are called without arguments and stay compatible with pytest
    (which isn't here, but the functions are named test_* in case it shows up),
    not only with the home-made runner below."""

    def wrapper():
        tmp = tempfile.mkdtemp(prefix="tgbot_meta_test_")
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    wrapper.__name__ = fn.__name__
    return wrapper


@_with_tmp
def test_docx(tmp):
    path, expected = fixtures.make_docx(tmp)
    _check_fixture("docx", path, expected, "docx")


@_with_tmp
def test_docx_wordlike(tmp):
    path, expected = fixtures.make_docx_wordlike(tmp)
    _check_fixture("docx_wordlike", path, expected, "docx")


@_with_tmp
def test_xlsx(tmp):
    path, expected = fixtures.make_xlsx(tmp)
    _check_fixture("xlsx", path, expected, "xlsx")


@_with_tmp
def test_pptx(tmp):
    """pptx is the third OOXML format, and before this fixture none of them
    inspected it: coverage came only through the shared paths via docx and xlsx.
    This is also the only DEVMODE check at the fixture level."""
    path, expected = fixtures.make_pptx(tmp)
    rep = _check_fixture("pptx", path, expected, "pptx")
    _check("pptx: the printer name was found in printerSettings*.bin specifically",
           any("printerSettings" in f.location and fixtures.PPTX_PRINTER in f.value
               for f in rep.findings),
           [f.location for f in rep.findings][:12])
    _check("pptx: the comment author was found in ppt/commentAuthors.xml",
           any("commentAuthors" in f.location and fixtures.PPTX_CM_AUTHOR in f.value
               for f in rep.findings),
           [f.location for f in rep.findings][:12])
    # DEVMODE is parsed beyond the printer name: the form name, the paper code and
    # the driver's private tail with the UNC path to the print server.
    labels = {f.label: f.value for f in rep.findings if "printerSettings" in f.location}
    _check("pptx: dmFormName was read", fixtures.PPTX_FORM in
           labels.get("dmFormName — paper format", ""), labels)
    _check("pptx: dmPaperSize was read and decoded",
           str(fixtures.PPTX_PAPER_CODE) in labels.get("dmPaperSize — paper size code", ""),
           labels)
    _check("pptx: the UNC path to the print server was found in the driver's private tail",
           fixtures.PPTX_PRINT_UNC in labels.get("UNC path in the print driver data", ""),
           labels)
    _check("pptx: the paper format produced a dating signal about the region",
           any(s.kind == "dating" and "region" in s.detail for s in rep.signals),
           [(s.kind, s.detail[:60]) for s in rep.signals])


@_with_tmp
def test_pdf(tmp):
    path, expected = fixtures.make_pdf(tmp)
    _check_fixture("pdf", path, expected, "pdf")


@_with_tmp
def test_pdf_incremental(tmp):
    path, expected = fixtures.make_pdf_incremental(tmp)
    rep = _check_fixture("pdf_incremental", path, expected, "pdf")
    detail_blob = " | ".join(s.detail for s in rep.signals)
    _check("pdf_incremental: the hazard signal explicitly mentions incremental revisions",
           any(s.kind == "hazard" and ("revision" in s.detail or "incremental" in s.detail)
               for s in rep.signals),
           detail_blob[:200])


@_with_tmp
def test_pdf_nested(tmp):
    """A font declared only inside a nested form, and XMP on an image: the
    inspector used to look only at the page /Resources and the root XMP and saw
    neither. The cleaner removed object XMP, while the "before" report said
    nothing about it."""
    import pikepdf

    path = os.path.join(tmp, "nested.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page()
    font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType, BaseFont=pikepdf.Name("/QWERTY+Aptos")))
    inner = pikepdf.Stream(pdf, b"BT /F1 12 Tf (x) Tj ET")
    inner.Type, inner.Subtype, inner.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 9, 9]
    inner.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    outer = pikepdf.Stream(pdf, b"/Fm2 Do")  # a form within a form: the font is two levels below the page
    outer.Type, outer.Subtype, outer.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 9, 9]
    outer.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm2=pdf.make_indirect(inner)))
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/'
           b'22-rdf-syntax-ns#"><rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" '
           b'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmpMM:DocumentID="xmp.did:NESTED-GUID-77">'
           b'<dc:creator><rdf:Seq><rdf:li>Nested Author</rdf:li></rdf:Seq></dc:creator>'
           b'</rdf:Description></rdf:RDF></x:xmpmeta>')
    meta = pikepdf.Stream(pdf, xmp)
    meta.Type, meta.Subtype = pikepdf.Name.Metadata, pikepdf.Name.XML
    img = pikepdf.Stream(pdf, b"\x00\x00\x00")
    img.Type, img.Subtype, img.Width, img.Height = pikepdf.Name.XObject, pikepdf.Name.Image, 1, 1
    img.ColorSpace, img.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
    img.Metadata = pdf.make_indirect(meta)
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(
        Fm1=pdf.make_indirect(outer), Im1=pdf.make_indirect(img)))
    pdf.save(path)

    before = _sha256(path)
    rep = inspect_file(path)
    _check("pdf_nested: the input was not modified", _sha256(path) == before)
    _check("pdf_nested: the font from the form within a form was found",
           any("QWERTY+Aptos" in f.value for f in rep.findings),
           [f.value for f in rep.findings if "Font" in f.location])
    xmp_found = {f.value: f for f in rep.findings if f.location.startswith("XMP: image")}
    _check("pdf_nested: the author from the image XMP was found and it is IDENTITY",
           "Nested Author" in xmp_found and xmp_found["Nested Author"].risk is Risk.IDENTITY,
           list(xmp_found))
    _check("pdf_nested: the GUID from the image XMP was found",
           "xmp.did:NESTED-GUID-77" in xmp_found, list(xmp_found))
    _check("pdf_nested: no parse errors", not rep.errors, rep.errors)


@_with_tmp
def test_jpeg(tmp):
    path, expected = fixtures.make_jpeg(tmp)
    _check_fixture("jpeg", path, expected, "jpeg")


@_with_tmp
def test_png(tmp):
    path, expected = fixtures.make_png(tmp)
    _check_fixture("png", path, expected, "png")


@_with_tmp
def test_rtf(tmp):
    path, expected = fixtures.make_rtf(tmp)
    _check_fixture("rtf", path, expected, "rtf")


@_with_tmp
def test_odt(tmp):
    path, expected = fixtures.make_odt(tmp)
    _check_fixture("odt", path, expected, "odt")


def _mini_docx(path, created, modified, total, words=23):
    """A minimal .docx with only core.xml and app.xml: exactly what the matching
    dates heuristic rests on. Whole fixtures are too big for this: they have
    their own TotalTime and size, and one field in them can't be tweaked."""
    core_xml = (
        '<?xml version="1.0"?><cp:coreProperties '
        'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/">'
        '<dc:creator>X</dc:creator>'
        '<dcterms:created>%s</dcterms:created>'
        '<dcterms:modified>%s</dcterms:modified>'
        '<cp:revision>2</cp:revision></cp:coreProperties>' % (created, modified))
    app_xml = (
        '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats'
        '.org/officeDocument/2006/extended-properties">'
        '<Application>Microsoft Office Word</Application>'
        '<AppVersion>16.0000</AppVersion>'
        '<TotalTime>%d</TotalTime><Words>%d</Words></Properties>' % (total, words))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0"?><Types xmlns="http://schemas.'
                    'openxmlformats.org/package/2006/content-types"/>')
        zf.writestr("word/document.xml",
                    '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
                    'openxmlformats.org/wordprocessingml/2006/main"><w:body/>'
                    '</w:document>')
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("docProps/app.xml", app_xml)
    return path


@_with_tmp
def test_equal_dates_need_corroboration(tmp):
    """created == modified is not a contradiction by itself.

    Word writes these dates to the minute (the seconds are always 00), so any
    document made in one sitting has them equal. Verified on a file from a real
    Word 16, where the old heuristic gave a false positive. Only TotalTime gives
    evidence: the claimed editing minutes don't fit into one minute of the
    document's life.
    """
    def dates_signal(rep):
        return [s for s in rep.signals
                if s.kind == "inconsistent" and "TotalTime claims" in s.detail]

    same = "2024-03-01T10:00:00Z"
    innocent = _mini_docx(os.path.join(tmp, "fresh.docx"), same, same, total=1)
    rep = inspect_file(innocent)
    _check("equal dates, TotalTime=1: a genuine fresh Word file -- not a signal",
           not dates_signal(rep), [s.detail for s in rep.signals])

    guilty = _mini_docx(os.path.join(tmp, "faked.docx"), same, same, total=137)
    rep = inspect_file(guilty)
    sig = dates_signal(rep)
    _check("equal dates, TotalTime=137: the contradiction is named", len(sig) == 1,
           [s.detail for s in rep.signals])
    if sig:
        _check("the signal text has both the date and the claimed minutes",
               same in sig[0].detail and "137" in sig[0].detail, sig[0].detail)
        _check("high confidence: the contradiction is arithmetic, not a matter of taste",
               sig[0].confidence == "high", sig[0].confidence)

    moved = _mini_docx(os.path.join(tmp, "normal.docx"), same,
                       "2024-03-05T14:30:00Z", total=137)
    _check("different dates with the same TotalTime: not a signal",
           not dates_signal(inspect_file(moved)))


@_with_tmp
def test_broken(tmp):
    """Pathologies: inspect_file doesn't raise on any broken file, a zip bomb is
    not decompressed (fast return), and idempotence is checked too: read-only
    has to be read-only even on garbage."""
    broken = fixtures.make_broken(tmp)

    for name, path in broken.items():
        before = _sha256(path)
        try:
            t0 = time.time()
            rep = inspect_file(path)
            elapsed = time.time() - t0
        except Exception as exc:  # noqa: BLE001 -- this is exactly what we test
            _check("broken[%s]: inspect_file did not raise" % name, False,
                   "%s: %s" % (type(exc).__name__, exc))
            continue
        _check("broken[%s]: inspect_file returned a Report" % name, rep is not None)
        after = _sha256(path)
        _check("broken[%s]: the file was not modified (read-only even on garbage)" % name,
               before == after)
        if name == "zip_bomb":
            _check("zip_bomb: inspection did not decompress the content (returned quickly)",
                   elapsed < 5.0, "took %.2f s" % elapsed)
            _check("zip_bomb: there is a signal or an error about a suspected bomb",
                   any(s.kind == "hazard" for s in rep.signals)
                   or any("bomb" in e for e in rep.errors))
        if name == "png_ztxt_bomb":
            # ~200 KB of compressed data -> 200 MB. Parsing PNG text chunks must
            # decompress with a limit, not all at once.
            _check("png_ztxt_bomb: did not hang decompressing the text chunk",
                   elapsed < 10.0, "took %.2f s" % elapsed)
            _check("png_ztxt_bomb: the guard fired and left a trace in the report",
                   bool(rep.errors) or any(s.kind == "hazard" for s in rep.signals),
                   "no errors and no signal: 200 MB were decompressed silently")


@_with_tmp
def test_telegram_rendering(tmp):
    """render_telegram: the message length limit (in UTF-16 code units, the way
    Telegram itself counts) and correct HTML escaping of "<script>&"."""
    path, expected = fixtures.make_docx(tmp)
    rep = inspect_file(path)
    messages = render_telegram(rep)

    for i, msg in enumerate(messages):
        _check("telegram[%d]: length <= %d (UTF-16 code units)" % (i, TG_LIMIT),
               _tg_len(msg) <= TG_LIMIT, "actually %d" % _tg_len(msg))

    blob = "\n".join(messages)
    xss = expected["xss_value"]
    _check("telegram: the raw '<script>&' did NOT leak into the HTML as is",
           "<script>" not in blob)
    _check("telegram: the value is escaped as &lt;script&gt;&amp;",
           "&lt;script&gt;&amp;" in blob)

    line = summary_line(rep)
    _check("summary_line: the line is non-empty and has no line breaks",
           bool(line) and "\n" not in line)


@_with_tmp
def test_idempotent_all(tmp):
    """Read-only for ALL fixtures, not only the ones checked along the way in the
    test_* above: a separate explicit pass."""
    makers = [fixtures.make_docx, fixtures.make_docx_wordlike, fixtures.make_xlsx,
              fixtures.make_pptx, fixtures.make_pdf, fixtures.make_pdf_incremental,
              fixtures.make_jpeg, fixtures.make_png, fixtures.make_rtf, fixtures.make_odt]
    for maker in makers:
        path, _ = maker(tmp)
        _check_idempotent(maker.__name__, path)


@_with_tmp
def test_no_path_leak(tmp):
    """Our own error messages must never carry an absolute path from our machine
    into the chat.

    A parser's exception text (pikepdf's especially) contains the full path to
    the temporary file. The report goes to the user, so the privacy tool would
    leak exactly the ENVIRONMENT category it looks for in other people's files.
    The scrub lives in Report.err().

    The other side is checked right here: a path found INSIDE someone else's
    file is a legitimate finding, and the scrub must not touch it.
    """
    from core.model import Report

    # A space and Cyrillic in the name: the regex has to eat the whole path, not
    # stop at the first space, otherwise the second half leaks anyway.
    path = os.path.join(tmp, "секретный отчёт v2.pdf")
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.7\n" + os.urandom(400))

    rep = inspect_file(path)
    _check("path_leak: the broken file produced parse errors (otherwise there's nothing to check)",
           bool(rep.errors), "no errors -- the test checks nothing")
    for needle in (tmp, "Users", "AppData", "секретный"):
        bad = [e for e in rep.errors if needle in e]
        _check("path_leak: the errors don't contain %r" % needle, not bad,
               "leaked: %r" % (bad[:1] or ""))

    # The scrub must not mangle findings.
    probe = Report(path="synthetic", fmt="docx")
    probe.add(Risk.ENVIRONMENT, "word/settings.xml", "Template path",
              "file:///C:/Users/ivanov/Templates/corp.dotx")
    _check("path_leak: a path FROM SOMEONE ELSE'S file is kept as a finding",
           "ivanov" in probe.findings[0].value, probe.findings[0].value)


def test_summary_metrics():
    """Regression test for three summary defects found by a run on real files.

    No fixtures needed: this is pure report arithmetic, no file parsing.

    1. Report.signal() must merge duplicates. Inspectors raise signals inside
       loops (per page, per font), and "Calibri is the Office 2007-2023 default
       font" repeated once per page, crowding everything else out of the
       conclusions block, including the signal that the file had already been
       cleaned.
    2. report.critical (IDENTITY+ENVIRONMENT) is NOT the same as
       report.sensitive. A real PDF showed "137 critical" with seven actual
       leaks: the rest was the per-page font list and the format version.
    3. summary_line must never call a kind="dating" signal (a font, a paper
       size) the producer. It used to print "PDF, made with Calibri".
    """
    from core.model import Report

    # --- 1. signal deduplication ------------------------------------------
    rep = Report(path="synthetic", fmt="pdf")
    for _ in range(5):
        rep.signal("dating", "Calibri is the Office 2007-2023 default font", "medium")
    rep.signal("dating", "Calibri is the Office 2007-2023 default font", "high")
    rep.signal("dating", "Letter paper size (US/Canada)", "medium")

    calibri = [s for s in rep.signals if s.detail.startswith("Calibri")]
    _check("metrics: six identical signals were merged into one",
           len(calibri) == 1,
           "signals: %r" % [(s.kind, s.detail[:34]) for s in rep.signals])
    _check("metrics: merging kept the higher of the two confidences",
           bool(calibri) and calibri[0].confidence == "high",
           calibri[0].confidence if calibri else "signal lost")
    _check("metrics: different signals were NOT merged",
           len(rep.signals) == 2, "signals: %d" % len(rep.signals))

    # --- 2. critical vs sensitive -----------------------------------------
    rep.add(Risk.IDENTITY, "/Info", "Author", "Иванов И.И.")
    rep.add(Risk.ENVIRONMENT, "/Info", "Source path", "C:\\Users\\ivanov\\doc.pdf")
    for i in range(130):
        rep.add(Risk.PROVENANCE, "/Resources /Font", "Font", "ABCDEF+Font%d" % i)
    rep.add(Risk.STRUCTURAL, "/Root", "Language", "ru")

    _check("metrics: critical counts only IDENTITY+ENVIRONMENT",
           rep.critical == 2, "critical=%d" % rep.critical)
    _check("metrics: sensitive includes provenance (that's the amount of cleaning work)",
           rep.sensitive == 132, "sensitive=%d" % rep.sensitive)
    _check("metrics: critical and sensitive diverged on noisy provenance",
           rep.critical != rep.sensitive,
           "both equal %d -- so the metrics were merged again" % rep.critical)

    # --- 3. dating is not passed off as the producer ----------------------
    line = summary_line(rep)
    _check("metrics: the summary does NOT call a font the producer",
           "made with Calibri" not in line, line)
    _check("metrics: the summary shows critical, not sensitive",
           "(2 " in line and "(132 " not in line, line)
    _check("metrics: without a producer signal the summary honestly admits not knowing",
           "not recognized" in line, line)

    rep.signal("producer", "Microsoft Word 16.0", "high")
    line2 = summary_line(rep)
    _check("metrics: a real producer makes it into the summary",
           "made with Microsoft Word 16.0" in line2, line2)
    _check("metrics: the font didn't get into the summary even next to the producer",
           "Calibri" not in line2, line2)


# ---------------------------------------------------------------------------
# BLOCK 1: unreadable files and cloud placeholders
# ---------------------------------------------------------------------------
# CI has no real OneDrive placeholders or files without access rights, so the
# sniff._read_head and sniff._win_attrs seams are swapped out by assigning the
# module attribute. Restoring happens ONLY in finally: otherwise the swap would
# poison every later test in the same process (the last test of the file
# checks this).

_REAL_READ_HEAD = sniff._read_head
_REAL_WIN_ATTRS = sniff._win_attrs


@contextlib.contextmanager
def _unreadable_stand(exc, attrs):
    """The file "doesn't open": reading the head raises exc, GetFileAttributesW
    returns attrs (None: not Windows or the call failed)."""
    def _boom(_path):
        raise exc

    old_read, old_attrs = sniff._read_head, sniff._win_attrs
    sniff._read_head = _boom
    sniff._win_attrs = lambda _path: attrs
    try:
        yield
    finally:
        sniff._read_head, sniff._win_attrs = old_read, old_attrs


def _make_file(tmp, name, size):
    path = os.path.join(tmp, name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def _inspect_unreadable(tmp, path, exc, attrs):
    """inspect_file on a file that "doesn't open" because of exc, with attributes
    attrs. Returns (Report, joined error text). Also checks what must hold for
    ANY reason: the format is "unreadable", there's no false "not recognized",
    and the text has no path or file name.

    The exceptions are built with filename=path, like a real open(): str(exc)
    then contains the path, and if the exception text ever reaches a message,
    the Report.err() scrub has to cut it out."""
    with _unreadable_stand(exc, attrs):
        rep = inspect_file(path)
    text = " | ".join(rep.errors)
    tag = "%s attrs=%s" % (type(exc).__name__, "None" if attrs is None else hex(attrs))
    _check("unreadable[%s]: fmt == 'unreadable'" % tag, rep.fmt == "unreadable", rep.fmt)
    _check("unreadable[%s]: no false 'not recognized' diagnosis" % tag,
           "not recognized" not in text, text[:200])
    leaked = [n for n in (tmp, os.path.basename(tmp), os.path.basename(path)) if n in text]
    _check("unreadable[%s]: the errors have no path or file name" % tag, not leaked,
           "leaked: %r" % leaked)
    return rep, text


def test_sniff_unreadable_keys():
    name = sniff.NAMES.get("unreadable")
    _check("unreadable: sniff.NAMES['unreadable'] is a non-empty string",
           isinstance(name, str) and bool(name), repr(name))
    _check("unreadable: family('unreadable') == 'unreadable'",
           sniff.family("unreadable") == "unreadable", sniff.family("unreadable"))
    _check("unreadable: pretty('unreadable') == 'File is not readable'",
           sniff.pretty("unreadable") == "File is not readable", sniff.pretty("unreadable"))


@_with_tmp
def test_sniff_missing(tmp):
    path = os.path.join(tmp, "нет_такого.doc")
    _check("missing: sniff(nonexistent path) == 'unreadable'",
           sniff.sniff(path) == "unreadable", sniff.sniff(path))
    reason = sniff.unreadable_reason(path)
    _check("missing: unreadable_reason == ('missing', 2)", reason == ("missing", 2), repr(reason))


@_with_tmp
def test_sniff_never_raises(tmp):
    bad = "bad" + chr(0) + "path"          # NUL in the path: open() raises ValueError
    _check("never_raises: sniff(path with NUL) == 'unreadable'",
           sniff.sniff(bad) == "unreadable", sniff.sniff(bad))
    reason = sniff.unreadable_reason(bad)
    _check("never_raises: unreadable_reason(path with NUL) == ('other', None)",
           reason == ("other", None), repr(reason))
    _check("never_raises: sniff(directory) == 'unreadable'",
           sniff.sniff(tmp) == "unreadable", sniff.sniff(tmp))


@_with_tmp
def test_sniff_regression_empty_and_random(tmp):
    """An empty file was READ successfully: it's 'unknown', not 'unreadable'."""
    empty = _make_file(tmp, "empty.bin", 0)
    _check("regression: an empty file (0 bytes) -> 'unknown', not 'unreadable'",
           sniff.sniff(empty) == "unknown", sniff.sniff(empty))
    _check("regression: inspect_file(empty) -> fmt 'unknown'",
           inspect_file(empty).fmt == "unknown")

    # Deterministic garbage with NULs and no known signatures (not os.urandom:
    # the test must not be nondeterministic).
    junk = os.path.join(tmp, "junk.bin")
    with open(junk, "wb") as fh:
        fh.write(b"\x00\x13\x37" + bytes((i * 7 + 3) % 256 for i in range(600)))
    _check("regression: garbage with NULs and no signatures -> 'unknown'",
           sniff.sniff(junk) == "unknown", sniff.sniff(junk))


@_with_tmp
def test_cloud_placeholder_simulated(tmp):
    """The MAIN test of the block. A OneDrive placeholder: the file exists, its
    size is real, but open().read() fails with errno 22. The diagnosis is a
    placeholder, not 'not recognized'.
    """
    path = _make_file(tmp, "договор_Иванов.docx", 321)
    rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), 0x00400020)

    _check("cloud: report.size == 321 (a placeholder has its real size)",
           rep.size == 321, str(rep.size))
    _check("cloud: exactly one error", len(rep.errors) == 1, "errors: %d" % len(rep.errors))
    first = rep.errors[0] if rep.errors else ""
    _check("cloud: the error says 'placeholder'", "placeholder" in first, first[:200])
    _check("cloud: the error has the hint 'Always keep on this device'",
           "Always keep on this device" in first, first[:200])
    _check("cloud: NO false 'not recognized'", "not recognized" not in first)
    _check("cloud: no findings", not rep.findings, "findings: %d" % len(rep.findings))
    # Privacy: the report goes to the user in the chat. Report.err() cuts out
    # absolute paths, and the file name is metadata too.
    _check("cloud: the error has NO temporary directory path",
           tmp not in first and os.path.basename(tmp) not in first, first[:200])
    _check("cloud: the error has NO file name", "договор_Иванов" not in first, first[:200])


@_with_tmp
def test_cloud_flags_matrix(tmp):
    """The cloud is detected by ATTRIBUTES, not by errno: errno 22 by itself
    proves nothing, since an invalid file name fails the same way."""
    path = _make_file(tmp, "doc.bin", 321)

    for attrs, what in ((0x00040000, "RECALL_ON_OPEN"), (0x00001020, "OFFLINE")):
        _rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), attrs)
        _check("matrix[%s]: the message is about a placeholder" % what, "placeholder" in text, text[:200])

    _rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), 0x00000420)
    _check("matrix[REPARSE_POINT only]: the message is about a reparse point",
           "reparse point" in text, text[:200])
    # The reparse message has a conditional hint "if the file comes from the
    # cloud", so we don't check for the word "cloud": we check that it is NOT a
    # placeholder diagnosis.
    _check("matrix[REPARSE_POINT only]: NOT a 'placeholder' diagnosis",
           "placeholder" not in text and "Always keep on this device" not in text, text[:200])

    for attrs, what in ((0x20, "plain attributes 0x20"), (None, "attributes None")):
        _rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), attrs)
        _check("matrix[%s]: the message contains 'errno 22'" % what, "errno 22" in text, text[:200])
        _check("matrix[%s]: NO word 'placeholder' (errno 22 doesn't prove the cloud)" % what,
               "placeholder" not in text, text[:200])


@_with_tmp
def test_win_attrs_invalid(tmp):
    import ctypes

    missing = os.path.join(tmp, "нет_такого.bin")
    _check("win_attrs: nonexistent path -> None (INVALID_FILE_ATTRIBUTES 0xFFFFFFFF handled)",
           sniff._win_attrs(missing) is None, repr(sniff._win_attrs(missing)))

    real = _make_file(tmp, "real.bin", 10)
    attrs = sniff._win_attrs(real)
    if hasattr(ctypes, "WinDLL"):
        _check("win_attrs: existing file -> int", isinstance(attrs, int), repr(attrs))
    else:
        _check("win_attrs: not Windows (no ctypes.WinDLL) -> None", attrs is None, repr(attrs))

    # Without ctypes.WinDLL (not Windows) the function has to return None, not raise.
    saved = getattr(ctypes, "WinDLL", None)
    got = "not called"
    try:
        if saved is not None:
            delattr(ctypes, "WinDLL")
        try:
            got = sniff._win_attrs(real)
        except Exception as exc:  # noqa: BLE001 -- this is exactly what we test
            got = "exception %s" % type(exc).__name__
    finally:
        if saved is not None:
            ctypes.WinDLL = saved
    _check("win_attrs: with ctypes.WinDLL removed -> None without an exception",
           got is None, repr(got))
    _check("win_attrs: ctypes.WinDLL restored",
           getattr(ctypes, "WinDLL", None) is saved)


@_with_tmp
def test_denied_and_missing_messages(tmp):
    path = _make_file(tmp, "doc.bin", 321)

    _rep, text = _inspect_unreadable(tmp, path, PermissionError(13, "x", path), 0x20)
    _check("denied: 'no read access' is present", "no read access" in text, text[:200])
    _check("denied: 'errno 13' is present", "errno 13" in text, text[:200])

    _rep, text = _inspect_unreadable(tmp, path, FileNotFoundError(2, "x", path), 0x20)
    _check("missing: 'not found' is present", "not found" in text, text[:200])


def test_footer_unreadable():
    """The footer must not lie: an unreadable file is exactly the one that was NOT read."""
    for fmt, was_read in (("unreadable", False), ("pdf", True)):
        rep = Report(path="x", fmt=fmt, size=10)
        rep.err("test error")
        for kind, text in (("telegram", "\n".join(render_telegram(rep))),
                           ("plain", render_plain(rep))):
            claims_read = "the file was read and not modified" in text
            admits = "but it couldn't be read" in text
            _check("footer[%s/%s]: the claim 'the file was read and not modified' is %s"
                   % (fmt, kind, "present" if was_read else "ABSENT"),
                   claims_read == was_read, text[-200:])
            _check("footer[%s/%s]: the admission 'but it couldn't be read' is %s"
                   % (fmt, kind, "absent" if was_read else "PRESENT"),
                   admits == (not was_read), text[-200:])


# ---------------------------------------------------------------------------
# BLOCK 2: legacy OLE2 (.doc / .xls / .ppt)
# ---------------------------------------------------------------------------
# The fixtures are real CFBF containers, built byte by byte and verified with
# olefile, ole32.dll and the Windows shell. These tests are a regression suite
# for seven defects found by the first run of insp_legacy on real OLE2 (D1-D7):
# a .doc keeps deleted text from past versions and a table of revision authors.


def _vals(rep, label, loc=None):
    return [f.value for f in rep.findings
            if f.label == label and (loc is None or f.location == loc)]


def _finds(rep, label):
    return [f for f in rep.findings if f.label == label]


def _write_cfb(tmp, name, streams, **kw):
    path = os.path.join(tmp, name)
    with open(path, "wb") as fh:
        fh.write(fixtures._cfb_build(streams, **kw))
    return path


def _check_ole_container(tag, path, expected, exact=True):
    """The fixture is a real container: olefile opens it without complaints and
    returns the planted metadata and exactly the planted top-level streams."""
    _check("%s: olefile.isOleFile(fixture)" % tag, olefile.isOleFile(path))
    with olefile.OleFileIO(path) as ole:
        _check("%s: olefile doesn't complain about the structure (parsing_issues == [])" % tag,
               ole.parsing_issues == [], repr(ole.parsing_issues)[:200])
        meta = ole.get_metadata()
        bad = {k: getattr(meta, k, None) for k, v in expected.get("ole_meta", {}).items()
               if getattr(meta, k, None) != v}
        _check("%s: olefile.get_metadata() returns all the planted fields" % tag,
               not bad, "mismatched: %r" % bad)
        streams = ["/".join(p) for p in ole.listdir()]
    want = expected["streams"]
    ok = sorted(streams) == sorted(want) if exact else set(want) <= set(streams)
    _check("%s: the set of streams matches the planted one" % tag, ok,
           "in the file %r, planted %r" % (streams, want))


@_with_tmp
def test_doc(tmp):
    path, expected = fixtures.make_doc(tmp)
    rep = _check_fixture("doc", path, expected, "doc")
    _check_ole_container("doc", path, expected)

    for label in expected["unremovable_labels"]:
        found = _finds(rep, label)
        _check("doc: %r -- IDENTITY, removable=False, the value contains '1Table'" % label,
               bool(found) and all(f.risk == Risk.IDENTITY and not f.removable
                                   and "1Table" in f.value for f in found),
               repr([(f.risk.value, f.removable, f.value) for f in found]))
    _check("doc: fast-save count == %d" % expected["quick_saves"],
           _vals(rep, "Fast-save count") == [str(expected["quick_saves"])],
           repr(_vals(rep, "Fast-save count")))
    _check("doc: application language (lid) == 0x0419",
           _vals(rep, "Application language (lid)") == ["0x%04X" % expected["lid"]] == ["0x0419"],
           repr(_vals(rep, "Application language (lid)")))
    marks = _vals(rep, "Revision author (SttbfRMark)")
    _check("doc: SttbfRMark -- both authors, the service entry Unknown is dropped",
           marks == list(fixtures.DOC_RMARK) and "Unknown" not in marks, repr(marks))

    # --- REGRESSION D2: custom properties. olefile reads their names only as
    # UTF-8, so a Unicode section (the way real Office writes it) and a cp1251
    # section lost the Purview label, the email and the value SILENTLY: zero
    # errors, zero findings.
    _check("doc/D2: the fixture has a Cyrillic custom property name",
           any(re.search("[А-Яа-яЁё]", k) for k in expected["userdef"]),
           repr(list(expected["userdef"])))
    for name, value in expected["userdef"].items():
        label = "Custom property: " + name
        found = _finds(rep, label)
        _check("doc/D2: %r == %r, risk IDENTITY" % (label, value),
               [f.value for f in found] == [value]
               and all(f.risk == Risk.IDENTITY for f in found),
               repr([(f.value, f.risk.value) for f in found]))
    _check("doc/D2: rep.errors is empty (the loss was silent: there were no errors either)",
           not rep.errors, repr(rep.errors)[:300])

    # --- REGRESSION D1: container modification time. ole.getctime("Root Entry")
    # used to raise OSError, swallowed by a bare except, and no file had this
    # finding. The date is deliberately not equal to the last saved date,
    # otherwise the finding could be confused with that property.
    _check("doc/D1: 'Container modification time' == %s" % expected["root_mtime"],
           _vals(rep, "Container modification time") == [expected["root_mtime"]],
           repr(_vals(rep, "Container modification time")))
    _check("doc/D1: the container time is not equal to the last saved date",
           expected["root_mtime"] not in _vals(rep, "Last saved"),
           repr(_vals(rep, "Last saved")))

    # --- finding times and locations
    edit = _vals(rep, "Total editing time")
    _check("doc: 'Total editing time' contains both 8220 and '2 h 17 min'",
           len(edit) == 1 and "8220" in edit[0] and "2 h 17 min" in edit[0], repr(edit))
    _check("doc: 'Created' == 2023-03-05 14:07:00",
           _vals(rep, "Created") == ["2023-03-05 14:07:00"], repr(_vals(rep, "Created")))
    for label, loc in (("Organization", "DocumentSummaryInformation"),
                       ("Manager", "DocumentSummaryInformation"),
                       ("Author", "SummaryInformation")):
        locs = [f.location for f in _finds(rep, label)]
        _check("doc: the location of finding %r == %s" % (label, loc), locs == [loc], repr(locs))


@_with_tmp
def test_xls(tmp):
    path, expected = fixtures.make_xls(tmp)
    rep = _check_fixture("xls", path, expected, "xls")
    _check_ole_container("xls", path, expected)

    found = _finds(rep, "Excel user name")
    _check("xls: 'Excel user name' == 'Сидорова А. С.', IDENTITY",
           [f.value for f in found] == [fixtures.XLS_WRITEACCESS_USER]
           and all(f.risk == Risk.IDENTITY for f in found), repr([f.value for f in found]))
    sheets = _vals(rep, "Workbook sheets")
    _check("xls: 'Workbook sheets' contains both 'Бюджет' and 'Скрытый_Расчёт'",
           len(sheets) == 1 and fixtures.XLS_SHEET in sheets[0]
           and fixtures.XLS_HIDDEN_SHEET in sheets[0], repr(sheets))
    # expected holds only the counters (supbook/externsheet/hlinks), and the
    # value string is built from them; the fixture has no EXTERNNAME, so 0.
    want = "SUPBOOK: %d, EXTERNSHEET: %d, EXTERNNAME: 0" % (expected["supbook"],
                                                             expected["externsheet"])
    _check("xls: 'External link records' == %r" % want,
           _vals(rep, "External link records") == [want],
           repr(_vals(rep, "External link records")))
    _check("xls: 'Hyperlinks' == '2 pcs'",
           _vals(rep, "Hyperlinks") == ["%d pcs" % expected["hlinks"]] == ["2 pcs"],
           repr(_vals(rep, "Hyperlinks")))

    # Custom properties in single-byte cp1251, a Russian name.
    _check("xls: the fixture has a Cyrillic custom property name",
           any(re.search("[А-Яа-яЁё]", k) for k in expected["userdef"]))
    for name, value in expected["userdef"].items():
        found = _finds(rep, "Custom property: " + name)
        _check("xls: 'Custom property: %s' == %r, IDENTITY" % (name, value),
               [f.value for f in found] == [value]
               and all(f.risk == Risk.IDENTITY for f in found),
               repr([(f.value, f.risk.value) for f in found]))
    _check("xls: rep.errors is empty", not rep.errors, repr(rep.errors)[:300])


@_with_tmp
def test_ppt(tmp):
    """REGRESSION D5: the ANSI name in CurrentUserAtom is cp1251, not cp1252.
    'Петров' used to come out as 'Ïåòðîâ'. Both forms of the atom: with a
    UTF-16 name (PowerPoint 2000+) and with ANSI only (then cp1251 is the only
    source).
    """
    label = "Last editor name (CurrentUserAtom)"
    for tag, kwargs in (("ppt", {}), ("ppt_ansi", {"unicode_name": False})):
        sub = os.path.join(tmp, tag)
        os.mkdir(sub)
        path, expected = fixtures.make_ppt(sub, **kwargs)
        rep = _check_fixture(tag, path, expected, "ppt")
        _check_ole_container(tag, path, expected, exact=False)
        found = _finds(rep, label)
        _check("%s/D5: %r == %r, IDENTITY" % (tag, label, fixtures.PPT_USER),
               [f.value for f in found] == [fixtures.PPT_USER]
               and all(f.risk == Risk.IDENTITY for f in found),
               repr([f.value for f in found]))


@_with_tmp
def test_doc_active(tmp):
    """REGRESSION D7: a CompObj stream sits both in the root and inside an
    embedded object; the inspector must not confuse the nested one with the
    root one."""
    path, expected = fixtures.make_doc_active(tmp)
    rep = _check_fixture("doc_active", path, expected, "doc")

    for bad in expected["not_errors"]:
        hits = [e for e in rep.errors if bad in e]
        _check("doc_active/D7: errors have no entries with %r" % bad, not hits, repr(hits)[:300])
    # The nested one isn't confused with the root one: the embedded object has
    # its own CompObj (Excel.Sheet.8), and the document itself has its own
    # (Word.Document.8).
    _check("doc_active/D7: root ProgID == Word.Document.8, not the nested Excel.Sheet.8",
           _vals(rep, "ProgID") == ["Word.Document.8"], repr(_vals(rep, "ProgID")))
    _check("doc_active: hazard signal about VBA",
           any(s.kind == "hazard" and "VBA" in s.detail for s in rep.signals),
           repr([(s.kind, s.detail[:60]) for s in rep.signals]))

    objs = _finds(rep, "Nested OLE objects")
    _check("doc_active: 'Nested OLE objects' IDENTITY, removable=False, '1 object(s): %s'"
           % expected["object_id"],
           len(objs) == 1 and objs[0].risk == Risk.IDENTITY and not objs[0].removable
           and objs[0].value.startswith("1 object(s): " + expected["object_id"]),
           repr([(f.risk.value, f.removable, f.value) for f in objs]))
    macros = _finds(rep, "Macro stream")
    _check("doc_active: 'Macro stream' contains %s and removable=False" % expected["macro_stream"],
           any(expected["macro_stream"] in f.value for f in macros)
           and all(not f.removable for f in macros),
           repr([(f.value, f.removable) for f in macros]))
    for label in expected["unremovable_labels"]:
        found = _finds(rep, label)
        _check("doc_active: %r is present and all such findings are removable=False" % label,
               bool(found) and all(not f.removable for f in found))


# A child process for the vector bomb: it measures the time itself, and the
# output is ASCII JSON (the console and encodings play no part). The probe
# file is written in utf-8.
_VECTOR_PROBE = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from core.insp_legacy import inspect_ole
t0 = time.time()
rep = inspect_ole(sys.argv[2], "doc")
print(json.dumps({"elapsed": time.time() - t0,
                  "author": [f.value for f in rep.findings if f.label == "Author"]}))
"""


@_with_tmp
def test_ole_vector_bomb(tmp):
    """REGRESSION D6 (denial of service): VT_VECTOR|VT_R8 with the counter
    0xFFFFFFFF. olefile parsed it with a loop of four billion empty iterations
    and a list of tens of gigabytes: the bot hung along with the parse. One
    corrupted byte is enough, no malice needed."""
    summary = fixtures._ole_section([
        (1, fixtures._vt_i2(1251)),
        (3, struct.pack("<HHI", 0x1005, 0, 0xFFFFFFFF)),     # 4 billion elements
        (4, fixtures._vt_lpstr(fixtures.DOC_AUTHOR, "cp1251"))])
    path = _write_cfb(tmp, "vector_bomb.doc", [
        ("\x05SummaryInformation",
         fixtures._ole_propstream((fixtures._FMTID_SUMMARY, summary))),
        ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)])

    # The regression is a HANG (and memory growth), so the parse runs in a child
    # process with a hard timeout: on a regression the test fails honestly, the
    # process is killed, and the run summary isn't lost (a watchdog in this
    # process would kill the whole runner along with it).
    probe = os.path.join(tmp, "probe_vector_bomb.py")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(_VECTOR_PROBE)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        proc = subprocess.run([sys.executable, probe, root, path],
                              capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        _check("vector_bomb/D6: inspect_ole returned (didn't hang on the 0xFFFFFFFF counter)",
               False, "no answer within 30 s, the child process was killed")
        return
    try:
        got = json.loads(proc.stdout.decode("ascii"))
    except ValueError:
        _check("vector_bomb/D6: the child process returned a result", False,
               "code %s, stderr: %s" % (proc.returncode,
                                       proc.stderr.decode("utf-8", "replace")[-300:]))
        return
    _check("vector_bomb/D6: inspect_ole returned in under 5 s",
           got["elapsed"] < 5.0, "took %.2f s" % got["elapsed"])
    _check("vector_bomb/D6: 'Author' is still found (the other properties are read)",
           got["author"] == [fixtures.DOC_AUTHOR], repr(got["author"]))


@_with_tmp
def test_ole_codepage_and_null_filetime(tmp):
    """REGRESSION D3 and D4. The two property streams have THEIR OWN code pages:
    SummaryInformation in 1252, DocumentSummaryInformation in 1251. Strings of
    the second are read in its own page, otherwise 'ООО «Ромашка»' comes out as
    'ÎÎÎ «Ðîìàøêà»' (D3). A zero FILETIME is 'not set', not a date in 1601:
    because of it, 'saved before created' produced a false inconsistent (D4)."""
    summary = fixtures._ole_section([
        (1, fixtures._vt_i2(1252)),
        (4, fixtures._vt_lpstr("John Smith", "cp1252")),
        (11, fixtures._vt_filetime(0)),                       # "never printed"
        (12, fixtures._vt_filetime(fixtures._filetime(fixtures.DOC_CREATE))),
        (13, fixtures._vt_filetime(0))])                      # save time not set
    docsum = fixtures._ole_section([
        (1, fixtures._vt_i2(1251)),
        (15, fixtures._vt_lpstr(fixtures.DOC_COMPANY, "cp1251"))])
    path = _write_cfb(tmp, "cp_mismatch.doc", [
        ("\x05SummaryInformation",
         fixtures._ole_propstream((fixtures._FMTID_SUMMARY, summary))),
        ("\x05DocumentSummaryInformation",
         fixtures._ole_propstream((fixtures._FMTID_DOCSUM, docsum))),
        ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)])
    rep = inspect_ole(path, "doc")

    company = _vals(rep, "Organization", "DocumentSummaryInformation")
    _check("codepage/D3: 'Organization' == 'ООО «Ромашка»' (not 'ÎÎÎ «Ðîìàøêà»')",
           company == [fixtures.DOC_COMPANY], repr(company))
    _check("codepage/D3: 'Author' from the cp1252 section == 'John Smith'",
           _vals(rep, "Author") == ["John Smith"], repr(_vals(rep, "Author")))
    _check("codepage/D4: 'Last printed' is empty (a zero FILETIME is not a date)",
           _vals(rep, "Last printed") == [""], repr(_vals(rep, "Last printed")))
    _check("codepage/D4: no false signal kind=inconsistent",
           not any(s.kind == "inconsistent" for s in rep.signals),
           repr([(s.kind, s.detail[:80]) for s in rep.signals]))


@_with_tmp
def test_xls_with_embedded_word(tmp):
    """An Excel workbook with an embedded Word object stays a workbook. sniff used
    to search for stream names flatly across the whole tree and found the
    MBD.../WordDocument storage: the workbook was detected as 'doc' and parsed
    by the wrong inspector. Now only the top level of ole.listdir() is compared.
    Cleaning a file with the wrong inspector is more dangerous than not
    cleaning it at all."""
    summary, _docsum = fixtures._ole_props_streams()
    workbook = (struct.pack("<HH", 0x0809, 16) + b"\x00" * 16      # BOF
                + struct.pack("<HH", 0x000A, 0))                    # EOF
    path = _write_cfb(tmp, "xls_with_word.xls", [
        ("\x05SummaryInformation", summary),
        ("Workbook", workbook),
        (("MBD0001A2B3", "\x01CompObj"),
         fixtures._ole_compobj("Word", "MSWordDoc", "Word.Document.8", fixtures._CLSID_WORD8)),
        (("MBD0001A2B3", "WordDocument"), b"\xec\xa5" + b"\x00" * 4200)])

    _check("xls+word: sniff == 'xls' (an embedded Word doesn't turn a workbook into a document)",
           sniff.sniff(path) == "xls", sniff.sniff(path))
    rep = inspect_file(path)
    _check("xls+word: inspect_file -> fmt 'xls'", rep.fmt == "xls", rep.fmt)
    _check("xls+word: the 'Workbook stream' finding is present",
           bool(_vals(rep, "Workbook stream")))
    _check("xls+word: NO 'Residual text and revision author table' finding (someone else's Word)",
           not _finds(rep, "Residual text and revision author table"))
    _check("xls+word: rep.errors is empty", not rep.errors, repr(rep.errors)[:300])

    # The second line of defense: even if fmt is passed wrong, parsing follows the
    # top-level streams, and a mismatch goes into an inconsistent signal.
    forced = inspect_ole(path, "doc")
    _check("xls+word: inspect_ole(fmt='doc') still parses the workbook, not the other Word",
           bool(_vals(forced, "Workbook stream"))
           and not _finds(forced, "Residual text and revision author table"))
    _check("xls+word: inspect_ole(fmt='doc') -- an inconsistent signal about the mismatch",
           any(s.kind == "inconsistent" for s in forced.signals))


@_with_tmp
def test_ole_idempotent_and_truncated(tmp):
    """Read-only on all OLE2 fixtures and resilience to truncated copies."""
    made = {}
    for maker in (fixtures.make_doc, fixtures.make_doc_active, fixtures.make_xls,
                  fixtures.make_ppt):
        path, _ = maker(tmp)
        made[maker.__name__] = path
        _check_idempotent(maker.__name__, path)

    for name, path in made.items():
        with open(path, "rb") as fh:
            full = fh.read()
        for n in (0, 100, 512, 1000, 4096):
            cut = os.path.join(tmp, "cut%d_%s" % (n, os.path.basename(path)))
            with open(cut, "wb") as fh:
                fh.write(full[:n])
            try:
                t0 = time.time()
                rep = inspect_file(cut)
                elapsed = time.time() - t0
            except Exception as exc:  # noqa: BLE001 -- this is exactly what we test
                _check("%s truncated to %d bytes: inspect_file did not raise" % (name, n),
                       False, "%s: %s" % (type(exc).__name__, exc))
                continue
            _check("%s truncated to %d bytes: a Report came back, quickly" % (name, n),
                   isinstance(rep, Report) and elapsed < 5.0,
                   "%s, %.2f s" % (type(rep).__name__, elapsed))


# ---------------------------------------------------------------------------
# Must run LAST (the runner sorts tests by name): the sniff seam swaps from
# block 1 must not outlive their tests.
# ---------------------------------------------------------------------------


def test_zz_seams_restored():
    _check("seams: sniff._read_head restored after the swaps",
           sniff._read_head is _REAL_READ_HEAD)
    _check("seams: sniff._win_attrs restored after the swaps",
           sniff._win_attrs is _REAL_WIN_ATTRS)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        print("-" * 78)
        print("=== %s ===" % fn.__name__)
        try:
            fn()
        except AssertionError as exc:
            _check("%s: no unhandled AssertionError" % fn.__name__, False, str(exc))
        except Exception as exc:  # noqa: BLE001 -- a test must not stop the others
            _check("%s: no unhandled exception" % fn.__name__, False,
                   "%s: %s" % (type(exc).__name__, exc))

    print("=" * 78)
    total = len(_RESULTS)
    failed = [r for r in _RESULTS if not r[1]]
    print("TOTAL: %d checks, %d failed" % (total, len(failed)))
    if failed:
        print("FAILED CHECKS:")
        for name, _, detail in failed:
            print("  - %s%s" % (name, ("  -- " + detail) if detail else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(_run_all())
