# -*- coding: utf-8 -*-
"""Самопроверка фазы 0 на синтетических фикстурах с заранее известными
метаданными. Единственная защита от регрессий во всех инспекторах сразу.

Без pytest (на машине его нет), но совместимо с ним: обычные функции test_*
с assert-ами внутри. Запуск:

    PYTHONIOENCODING=utf-8 python -m tests.test_inspect

PYTHONIOENCODING=utf-8 обязателен на Windows -- иначе консоль (cp866/cp1251)
калечит кириллицу в выводе, и это выглядит как баг в коде, а не артефакт
консоли.

Печатает по строке на проверку и сводку в конце; код возврата 1 при любом
провале хотя бы одной проверки.
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
from core.report import _tg_len  # приватный хелпер, но именно так считает Telegram

from tests import fixtures

# ---------------------------------------------------------------------------
# учёт результатов
# ---------------------------------------------------------------------------

_RESULTS = []  # (имя проверки, прошла?, пояснение)


def _check(name, condition, detail=""):
    _RESULTS.append((name, bool(condition), detail))
    mark = "OK  " if condition else "FAIL"
    line = "%s %s" % (mark, name)
    if not condition and detail:
        line += "  -- %s" % detail
    print(line)
    return bool(condition)


# ---------------------------------------------------------------------------
# мелкие помощники теста
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
    _check("%s: файл не изменён инспекцией (sha256 до==после)" % tag, before == after)


def _check_fixture(tag, path, expected, fmt_hint):
    """Общая обвязка для одной "здоровой" фикстуры: sniff, идемпотентность,
    заложенные значения, категории риска, сигналы. Возвращает Report, чтобы
    вызывающий тест мог дописать свои специфичные проверки."""
    before = _sha256(path)
    sniffed = sniff.sniff(path)
    _check("%s: sniff определил формат (%s)" % (tag, fmt_hint),
           sniffed == fmt_hint, "sniff вернул %r" % sniffed)

    rep = inspect_file(path)
    after = _sha256(path)
    _check("%s: read-only -- sha256 файла не изменился" % tag, before == after)
    _check("%s: inspect_file не бросил исключение, Report вернулся" % tag,
           rep is not None and rep.fmt == fmt_hint or rep is not None)

    blob = " | ".join(f.value for f in rep.findings)
    for v in expected.get("values", []):
        _check("%s: найдено заложенное значение %r" % (tag, v[:50]), v in blob)

    identity_vals = _risk_values(rep, Risk.IDENTITY)
    for v in expected.get("identity", []):
        _check("%s: %r классифицировано как IDENTITY" % (tag, v[:40]),
               any(v in iv for iv in identity_vals))

    env_vals = _risk_values(rep, Risk.ENVIRONMENT)
    for v in expected.get("environment", []):
        _check("%s: %r классифицировано как ENVIRONMENT" % (tag, v[:40]),
               any(v in ev for ev in env_vals))

    sig_kinds = {s.kind for s in rep.signals}
    for k in expected.get("signals", []):
        _check("%s: присутствует сигнал kind=%s" % (tag, k), k in sig_kinds,
               "сигналы отчёта: %s" % sorted(sig_kinds))
    for k in expected.get("not_signals", []):
        _check("%s: НЕТ ложного сигнала kind=%s (защита от false positive)" % (tag, k),
               k not in sig_kinds, "сигналы отчёта: %s" % sorted(sig_kinds))

    if expected.get("gps"):
        lat, lon = expected["gps"]
        _check("%s: GPS пересчитан в десятичные верно (допуск)" % tag,
               _gps_match(rep, lat, lon))

    return rep


# ---------------------------------------------------------------------------
# тесты по фикстурам
# ---------------------------------------------------------------------------


def _with_tmp(fn):
    """Каждый test_* сам заводит и убирает свой временный каталог -- так
    функции вызываются без аргументов и остаются совместимы с pytest
    (которого тут нет, но функции названы test_* на случай, если он
    появится), а не только с самодельным раннером ниже."""

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
    """pptx -- третий формат OOXML, и до этой фикстуры он не инспектировался
    ни одной: покрытие шло только общими путями через docx и xlsx. Здесь же
    единственная проверка DEVMODE на уровне фикстур."""
    path, expected = fixtures.make_pptx(tmp)
    rep = _check_fixture("pptx", path, expected, "pptx")
    _check("pptx: имя принтера найдено именно в printerSettings*.bin",
           any("printerSettings" in f.location and fixtures.PPTX_PRINTER in f.value
               for f in rep.findings),
           [f.location for f in rep.findings][:12])
    _check("pptx: автор комментария найден в ppt/commentAuthors.xml",
           any("commentAuthors" in f.location and fixtures.PPTX_CM_AUTHOR in f.value
               for f in rep.findings),
           [f.location for f in rep.findings][:12])
    # DEVMODE разобран не только до имени принтера: имя формы, код формата и
    # приватный хвост драйвера с UNC-путём к серверу печати.
    labels = {f.label: f.value for f in rep.findings if "printerSettings" in f.location}
    _check("pptx: dmFormName прочитан", fixtures.PPTX_FORM in
           labels.get("dmFormName — формат бумаги", ""), labels)
    _check("pptx: dmPaperSize прочитан и расшифрован",
           str(fixtures.PPTX_PAPER_CODE) in labels.get("dmPaperSize — код формата бумаги", ""),
           labels)
    _check("pptx: UNC-путь к серверу печати найден в приватном хвосте драйвера",
           fixtures.PPTX_PRINT_UNC in labels.get("UNC-путь в данных драйвера печати", ""),
           labels)
    _check("pptx: формат бумаги дал сигнал dating о регионе",
           any(s.kind == "dating" and "регион" in s.detail for s in rep.signals),
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
    _check("pdf_incremental: сигнал hazard явно говорит про инкрементальные ревизии",
           any(s.kind == "hazard" and ("ревизи" in s.detail or "инкремент" in s.detail)
               for s in rep.signals),
           detail_blob[:200])


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
    """Минимальный .docx только с core.xml и app.xml -- ровно то, на чём стоит
    эвристика совпадающих дат. Фикстуры целиком для этого слишком крупные:
    у них свои TotalTime и объём, и подкрутить одно поле в них нельзя."""
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
    """created == modified само по себе не противоречие.

    Word пишет эти даты с точностью до минуты (секунды всегда 00), поэтому у
    любого документа, сделанного одним заходом, они совпадают -- проверено на
    файле от настоящего Word 16, где прежняя эвристика давала ложный плюс.
    Улику даёт только TotalTime: заявленные минуты редактирования не влезают
    в одну минуту жизни документа.
    """
    def dates_signal(rep):
        return [s for s in rep.signals
                if s.kind == "inconsistent" and "TotalTime заявляет" in s.detail]

    same = "2024-03-01T10:00:00Z"
    innocent = _mini_docx(os.path.join(tmp, "fresh.docx"), same, same, total=1)
    rep = inspect_file(innocent)
    _check("даты равны, TotalTime=1: подлинный свежий файл Word -- не сигнал",
           not dates_signal(rep), [s.detail for s in rep.signals])

    guilty = _mini_docx(os.path.join(tmp, "faked.docx"), same, same, total=137)
    rep = inspect_file(guilty)
    sig = dates_signal(rep)
    _check("даты равны, TotalTime=137: противоречие названо", len(sig) == 1,
           [s.detail for s in rep.signals])
    if sig:
        _check("в тексте сигнала есть и дата, и заявленные минуты",
               same in sig[0].detail and "137" in sig[0].detail, sig[0].detail)
        _check("уверенность высокая: противоречие арифметическое, не вкусовое",
               sig[0].confidence == "high", sig[0].confidence)

    moved = _mini_docx(os.path.join(tmp, "normal.docx"), same,
                       "2024-03-05T14:30:00Z", total=137)
    _check("даты разные при том же TotalTime: не сигнал",
           not dates_signal(inspect_file(moved)))


@_with_tmp
def test_broken(tmp):
    """Патологии: inspect_file не бросает исключение ни на одном битом файле,
    zip-бомба не распаковывается (быстрый возврат), идемпотентность тоже
    проверяем -- read-only обязан быть read-only даже на мусоре."""
    broken = fixtures.make_broken(tmp)

    for name, path in broken.items():
        before = _sha256(path)
        try:
            t0 = time.time()
            rep = inspect_file(path)
            elapsed = time.time() - t0
        except Exception as exc:  # noqa: BLE001 -- именно это и проверяем
            _check("broken[%s]: inspect_file не бросил исключение" % name, False,
                   "%s: %s" % (type(exc).__name__, exc))
            continue
        _check("broken[%s]: inspect_file вернул Report" % name, rep is not None)
        after = _sha256(path)
        _check("broken[%s]: файл не изменён (read-only даже на мусоре)" % name,
               before == after)
        if name == "zip_bomb":
            _check("zip_bomb: инспекция не распаковывала содержимое (вернулась быстро)",
                   elapsed < 5.0, "заняло %.2f с" % elapsed)
            _check("zip_bomb: есть сигнал или ошибка про подозрение на бомбу",
                   any(s.kind == "hazard" for s in rep.signals)
                   or any("бомб" in e for e in rep.errors))
        if name == "png_ztxt_bomb":
            # Килобайт сжатых данных -> 200 МБ. Разбор текстовых чанков PNG
            # обязан распаковывать с ограничением, а не целиком.
            _check("png_ztxt_bomb: не повис на распаковке текстового чанка",
                   elapsed < 10.0, "заняло %.2f с" % elapsed)
            _check("png_ztxt_bomb: защита отработала и оставила след в отчёте",
                   bool(rep.errors) or any(s.kind == "hazard" for s in rep.signals),
                   "ни ошибок, ни сигнала: 200 МБ распаковались молча")


@_with_tmp
def test_telegram_rendering(tmp):
    """render_telegram: лимит длины сообщения (в UTF-16 code units, как
    считает сам Telegram) и корректное HTML-экранирование "<script>&"."""
    path, expected = fixtures.make_docx(tmp)
    rep = inspect_file(path)
    messages = render_telegram(rep)

    for i, msg in enumerate(messages):
        _check("telegram[%d]: длина <= %d (UTF-16 code units)" % (i, TG_LIMIT),
               _tg_len(msg) <= TG_LIMIT, "фактически %d" % _tg_len(msg))

    blob = "\n".join(messages)
    xss = expected["xss_value"]
    _check("telegram: исходный '<script>&' НЕ утёк в HTML как есть",
           "<script>" not in blob)
    _check("telegram: значение экранировано как &lt;script&gt;&amp;",
           "&lt;script&gt;&amp;" in blob)

    line = summary_line(rep)
    _check("summary_line: строка непустая и без переводов строк",
           bool(line) and "\n" not in line)


@_with_tmp
def test_idempotent_all(tmp):
    """Read-only для ВСЕХ фикстур, не только тех, что проверены по дороге
    в test_* выше -- отдельный явный проход, как того просит задание."""
    makers = [fixtures.make_docx, fixtures.make_docx_wordlike, fixtures.make_xlsx,
              fixtures.make_pptx, fixtures.make_pdf, fixtures.make_pdf_incremental,
              fixtures.make_jpeg, fixtures.make_png, fixtures.make_rtf, fixtures.make_odt]
    for maker in makers:
        path, _ = maker(tmp)
        _check_idempotent(maker.__name__, path)


@_with_tmp
def test_no_path_leak(tmp):
    """Наши собственные сообщения об ошибках не имеют права утащить в чат
    абсолютный путь с нашей машины.

    Текст исключения парсера (особенно pikepdf) содержит полный путь к
    временному файлу. Отчёт уходит пользователю, то есть инструмент
    приватности сливал бы ровно ту категорию ENVIRONMENT, которую ищет в
    чужих файлах. Скраб живёт в Report.err().

    Обратная сторона проверяется тут же: путь, найденный ВНУТРИ чужого
    файла, -- законная находка, и скраб не смеет её тронуть.
    """
    from core.model import Report

    # Пробел и кириллица в имени: регексп обязан съесть путь целиком, а не
    # до первого пробела -- иначе вторая половина всё равно утекает.
    path = os.path.join(tmp, "секретный отчёт v2.pdf")
    with open(path, "wb") as fh:
        fh.write(b"%PDF-1.7\n" + os.urandom(400))

    rep = inspect_file(path)
    _check("path_leak: битый файл дал ошибки разбора (иначе проверять нечего)",
           bool(rep.errors), "ошибок нет -- тест ничего не проверяет")
    for needle in (tmp, "Users", "AppData", "секретный"):
        bad = [e for e in rep.errors if needle in e]
        _check("path_leak: в ошибках нет %r" % needle, not bad,
               "утекло: %r" % (bad[:1] or ""))

    # Скраб не должен калечить находки.
    probe = Report(path="synthetic", fmt="docx")
    probe.add(Risk.ENVIRONMENT, "word/settings.xml", "Путь к шаблону",
              "file:///C:/Users/ivanov/Templates/corp.dotx")
    _check("path_leak: путь ИЗ ЧУЖОГО файла сохранён как находка",
           "ivanov" in probe.findings[0].value, probe.findings[0].value)


def test_summary_metrics():
    """Регрессия на три дефекта сводки, найденные прогоном на настоящих файлах.

    Фикстуры не нужны: это чистая арифметика отчёта, без разбора файлов.

    1. Report.signal() обязан склеивать дубликаты. Инспекторы ставят сигналы
       внутри циклов (по страницам, по шрифтам), и «Calibri -- шрифт по
       умолчанию Office 2007-2023» повторялся по разу на страницу, вытесняя
       из блока выводов всё остальное -- в том числе сигнал о том, что файл
       уже чистили.
    2. report.critical (IDENTITY+ENVIRONMENT) НЕ равен report.sensitive.
       У реального PDF было «137 критичных» при семи настоящих утечках:
       остальное -- список шрифтов по страницам и версия формата.
    3. summary_line не имеет права называть продюсером сигнал kind="dating"
       (шрифт, формат бумаги). Выдавало «PDF, сделан через Calibri».
    """
    from core.model import Report

    # --- 1. дедупликация сигналов -----------------------------------------
    rep = Report(path="synthetic", fmt="pdf")
    for _ in range(5):
        rep.signal("dating", "Calibri -- шрифт по умолчанию Office 2007-2023", "medium")
    rep.signal("dating", "Calibri -- шрифт по умолчанию Office 2007-2023", "high")
    rep.signal("dating", "Формат бумаги Letter (США/Канада)", "medium")

    calibri = [s for s in rep.signals if s.detail.startswith("Calibri")]
    _check("metrics: шесть одинаковых сигналов склеены в один",
           len(calibri) == 1,
           "сигналы: %r" % [(s.kind, s.detail[:34]) for s in rep.signals])
    _check("metrics: при склейке сохранена высшая уверенность из двух",
           bool(calibri) and calibri[0].confidence == "high",
           calibri[0].confidence if calibri else "сигнал потерян")
    _check("metrics: различающиеся сигналы НЕ склеены",
           len(rep.signals) == 2, "сигналов: %d" % len(rep.signals))

    # --- 2. critical против sensitive -------------------------------------
    rep.add(Risk.IDENTITY, "/Info", "Автор", "Иванов И.И.")
    rep.add(Risk.ENVIRONMENT, "/Info", "Путь к исходнику", "C:\\Users\\ivanov\\doc.pdf")
    for i in range(130):
        rep.add(Risk.PROVENANCE, "/Resources /Font", "Шрифт", "ABCDEF+Font%d" % i)
    rep.add(Risk.STRUCTURAL, "/Root", "Язык", "ru")

    _check("metrics: critical считает только IDENTITY+ENVIRONMENT",
           rep.critical == 2, "critical=%d" % rep.critical)
    _check("metrics: sensitive включает провенанс (это объём работы для чистки)",
           rep.sensitive == 132, "sensitive=%d" % rep.sensitive)
    _check("metrics: critical и sensitive разошлись на шумном провенансе",
           rep.critical != rep.sensitive,
           "оба равны %d -- значит метрики снова склеили" % rep.critical)

    # --- 3. dating не выдаётся за продюсера -------------------------------
    line = summary_line(rep)
    _check("metrics: сводка НЕ называет шрифт продюсером",
           "сделан через Calibri" not in line, line)
    _check("metrics: сводка показывает critical, а не sensitive",
           "(2 " in line and "(132 " not in line, line)
    _check("metrics: без producer-сигнала сводка честно признаёт незнание",
           "не опознан" in line, line)

    rep.signal("producer", "Microsoft Word 16.0", "high")
    line2 = summary_line(rep)
    _check("metrics: настоящий producer попадает в сводку",
           "сделан через Microsoft Word 16.0" in line2, line2)
    _check("metrics: шрифт не попал в сводку даже рядом с продюсером",
           "Calibri" not in line2, line2)


# ---------------------------------------------------------------------------
# БЛОК 1: нечитаемые файлы и облачные плейсхолдеры
# ---------------------------------------------------------------------------
# Настоящих плейсхолдеров OneDrive и файлов без прав доступа в CI нет, поэтому
# швы sniff._read_head и sniff._win_attrs подменяются присваиванием модульного
# атрибута. Восстановление -- ТОЛЬКО в finally: иначе подмена отравит все
# последующие тесты в том же процессе (последний тест файла это проверяет).

_REAL_READ_HEAD = sniff._read_head
_REAL_WIN_ATTRS = sniff._win_attrs


@contextlib.contextmanager
def _unreadable_stand(exc, attrs):
    """Файл "не открывается": чтение головы бросает exc, GetFileAttributesW
    отдаёт attrs (None -- не Windows или вызов не удался)."""
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
    """inspect_file на файле, который "не открывается" по причине exc при
    атрибутах attrs. Возвращает (Report, склеенный текст ошибок). Заодно
    проверяет то, что должно быть верно для ЛЮБОЙ причины: формат
    "unreadable", ложного "не опознан" нет, пути и имени файла в тексте нет.

    Исключения строятся с filename=path, как у настоящего open(): str(exc)
    тогда содержит путь, и если текст исключения когда-нибудь попадёт в
    сообщение, скраб Report.err() обязан его вырезать."""
    with _unreadable_stand(exc, attrs):
        rep = inspect_file(path)
    text = " | ".join(rep.errors)
    tag = "%s attrs=%s" % (type(exc).__name__, "None" if attrs is None else hex(attrs))
    _check("unreadable[%s]: fmt == 'unreadable'" % tag, rep.fmt == "unreadable", rep.fmt)
    _check("unreadable[%s]: нет ложного диагноза 'не опознан'" % tag,
           "не опознан" not in text, text[:200])
    leaked = [n for n in (tmp, os.path.basename(tmp), os.path.basename(path)) if n in text]
    _check("unreadable[%s]: в ошибках нет пути и имени файла" % tag, not leaked,
           "утекло: %r" % leaked)
    return rep, text


def test_sniff_unreadable_keys():
    name = sniff.NAMES.get("unreadable")
    _check("unreadable: sniff.NAMES['unreadable'] -- непустая строка",
           isinstance(name, str) and bool(name), repr(name))
    _check("unreadable: family('unreadable') == 'unreadable'",
           sniff.family("unreadable") == "unreadable", sniff.family("unreadable"))
    _check("unreadable: pretty('unreadable') == 'Файл недоступен для чтения'",
           sniff.pretty("unreadable") == "Файл недоступен для чтения", sniff.pretty("unreadable"))


@_with_tmp
def test_sniff_missing(tmp):
    path = os.path.join(tmp, "нет_такого.doc")
    _check("missing: sniff(несуществующий путь) == 'unreadable'",
           sniff.sniff(path) == "unreadable", sniff.sniff(path))
    reason = sniff.unreadable_reason(path)
    _check("missing: unreadable_reason == ('missing', 2)", reason == ("missing", 2), repr(reason))


@_with_tmp
def test_sniff_never_raises(tmp):
    bad = "bad" + chr(0) + "path"          # NUL в пути: open() бросает ValueError
    _check("never_raises: sniff(путь с NUL) == 'unreadable'",
           sniff.sniff(bad) == "unreadable", sniff.sniff(bad))
    reason = sniff.unreadable_reason(bad)
    _check("never_raises: unreadable_reason(путь с NUL) == ('other', None)",
           reason == ("other", None), repr(reason))
    _check("never_raises: sniff(каталог) == 'unreadable'",
           sniff.sniff(tmp) == "unreadable", sniff.sniff(tmp))


@_with_tmp
def test_sniff_regression_empty_and_random(tmp):
    """Пустой файл ПРОЧИТАН успешно -- это 'unknown', а не 'unreadable'."""
    empty = _make_file(tmp, "empty.bin", 0)
    _check("regression: пустой файл (0 байт) -> 'unknown', не 'unreadable'",
           sniff.sniff(empty) == "unknown", sniff.sniff(empty))
    _check("regression: inspect_file(пустой) -> fmt 'unknown'",
           inspect_file(empty).fmt == "unknown")

    # Детерминированный мусор с NUL и без известных сигнатур (не os.urandom:
    # тест не должен быть недетерминированным).
    junk = os.path.join(tmp, "junk.bin")
    with open(junk, "wb") as fh:
        fh.write(b"\x00\x13\x37" + bytes((i * 7 + 3) % 256 for i in range(600)))
    _check("regression: мусор с NUL без сигнатур -> 'unknown'",
           sniff.sniff(junk) == "unknown", sniff.sniff(junk))


@_with_tmp
def test_cloud_placeholder_simulated(tmp):
    """ГЛАВНЫЙ тест блока. Плейсхолдер OneDrive: файл есть, размер настоящий,
    а open().read() падает с errno 22. Диагноз -- плейсхолдер, не 'не опознан'.
    """
    path = _make_file(tmp, "договор_Иванов.docx", 321)
    rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), 0x00400020)

    _check("cloud: report.size == 321 (у плейсхолдера размер настоящий)",
           rep.size == 321, str(rep.size))
    _check("cloud: ровно одна ошибка", len(rep.errors) == 1, "ошибок: %d" % len(rep.errors))
    first = rep.errors[0] if rep.errors else ""
    _check("cloud: в ошибке сказано 'плейсхолдер'", "плейсхолдер" in first, first[:200])
    _check("cloud: в ошибке есть подсказка 'Always keep on this device'",
           "Always keep on this device" in first, first[:200])
    _check("cloud: НЕТ ложного 'не опознан'", "не опознан" not in first)
    _check("cloud: находок нет", not rep.findings, "findings: %d" % len(rep.findings))
    # Приватность: отчёт уходит пользователю в чат. Report.err() вырезает
    # абсолютные пути, а имя файла -- тоже метаданные.
    _check("cloud: в ошибке НЕТ пути временного каталога",
           tmp not in first and os.path.basename(tmp) not in first, first[:200])
    _check("cloud: в ошибке НЕТ имени файла", "договор_Иванов" not in first, first[:200])


@_with_tmp
def test_cloud_flags_matrix(tmp):
    """Облако определяется по АТРИБУТАМ, не по errno: errno 22 сам по себе
    ничего не доказывает -- так же падает недопустимое имя файла."""
    path = _make_file(tmp, "doc.bin", 321)

    for attrs, what in ((0x00040000, "RECALL_ON_OPEN"), (0x00001020, "OFFLINE")):
        _rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), attrs)
        _check("matrix[%s]: сообщение про плейсхолдер" % what, "плейсхолдер" in text, text[:200])

    _rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), 0x00000420)
    _check("matrix[только REPARSE_POINT]: сообщение про reparse point",
           "reparse point" in text, text[:200])
    # В сообщении про reparse есть условная подсказка "если файл из облака",
    # поэтому слово "облако" не проверяем -- проверяем, что это НЕ диагноз
    # плейсхолдера.
    _check("matrix[только REPARSE_POINT]: НЕ диагноз 'плейсхолдер'",
           "плейсхолдер" not in text and "Always keep on this device" not in text, text[:200])

    for attrs, what in ((0x20, "обычные атрибуты 0x20"), (None, "атрибуты None")):
        _rep, text = _inspect_unreadable(tmp, path, OSError(22, "Invalid argument", path), attrs)
        _check("matrix[%s]: сообщение содержит 'errno 22'" % what, "errno 22" in text, text[:200])
        _check("matrix[%s]: НЕТ слова 'плейсхолдер' (errno 22 облако не доказывает)" % what,
               "плейсхолдер" not in text, text[:200])


@_with_tmp
def test_win_attrs_invalid(tmp):
    import ctypes

    missing = os.path.join(tmp, "нет_такого.bin")
    _check("win_attrs: несуществующий путь -> None (INVALID_FILE_ATTRIBUTES 0xFFFFFFFF обработан)",
           sniff._win_attrs(missing) is None, repr(sniff._win_attrs(missing)))

    real = _make_file(tmp, "real.bin", 10)
    attrs = sniff._win_attrs(real)
    if hasattr(ctypes, "WinDLL"):
        _check("win_attrs: существующий файл -> int", isinstance(attrs, int), repr(attrs))
    else:
        _check("win_attrs: не Windows (нет ctypes.WinDLL) -> None", attrs is None, repr(attrs))

    # Без ctypes.WinDLL (не Windows) функция обязана отдать None, а не бросить.
    saved = getattr(ctypes, "WinDLL", None)
    got = "не вызывалась"
    try:
        if saved is not None:
            delattr(ctypes, "WinDLL")
        try:
            got = sniff._win_attrs(real)
        except Exception as exc:  # noqa: BLE001 -- именно это и проверяем
            got = "исключение %s" % type(exc).__name__
    finally:
        if saved is not None:
            ctypes.WinDLL = saved
    _check("win_attrs: при удалённом ctypes.WinDLL -> None без исключения",
           got is None, repr(got))
    _check("win_attrs: ctypes.WinDLL восстановлен",
           getattr(ctypes, "WinDLL", None) is saved)


@_with_tmp
def test_denied_and_missing_messages(tmp):
    path = _make_file(tmp, "doc.bin", 321)

    _rep, text = _inspect_unreadable(tmp, path, PermissionError(13, "x", path), 0x20)
    _check("denied: есть 'нет доступа на чтение'", "нет доступа на чтение" in text, text[:200])
    _check("denied: есть 'errno 13'", "errno 13" in text, text[:200])

    _rep, text = _inspect_unreadable(tmp, path, FileNotFoundError(2, "x", path), 0x20)
    _check("missing: есть 'не найден'", "не найден" in text, text[:200])


def test_footer_unreadable():
    """Подвал не имеет права врать: нечитаемый файл как раз НЕ прочитан."""
    for fmt, was_read in (("unreadable", False), ("pdf", True)):
        rep = Report(path="x", fmt=fmt, size=10)
        rep.err("тестовая ошибка")
        for kind, text in (("telegram", "\n".join(render_telegram(rep))),
                           ("plain", render_plain(rep))):
            claims_read = "файл прочитан и не изменён" in text
            admits = "прочитать его не удалось" in text
            _check("footer[%s/%s]: утверждение 'файл прочитан и не изменён' %s"
                   % (fmt, kind, "есть" if was_read else "ОТСУТСТВУЕТ"),
                   claims_read == was_read, text[-200:])
            _check("footer[%s/%s]: признание 'прочитать его не удалось' %s"
                   % (fmt, kind, "отсутствует" if was_read else "ЕСТЬ"),
                   admits == (not was_read), text[-200:])


# ---------------------------------------------------------------------------
# БЛОК 2: легаси OLE2 (.doc / .xls / .ppt)
# ---------------------------------------------------------------------------
# Фикстуры -- настоящие CFBF-контейнеры, собранные побайтово и проверенные
# olefile, ole32.dll и шеллом Windows. Эти тесты -- регрессия на семь дефектов,
# найденных первым запуском insp_legacy на настоящем OLE2 (D1-D7): .doc хранит
# удалённый текст прошлых версий и таблицу авторов правок.


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
    """Фикстура -- настоящий контейнер: olefile открывает его без замечаний,
    отдаёт заложенные метаданные и ровно заложенные потоки верхнего уровня."""
    _check("%s: olefile.isOleFile(фикстура)" % tag, olefile.isOleFile(path))
    with olefile.OleFileIO(path) as ole:
        _check("%s: olefile не жалуется на структуру (parsing_issues == [])" % tag,
               ole.parsing_issues == [], repr(ole.parsing_issues)[:200])
        meta = ole.get_metadata()
        bad = {k: getattr(meta, k, None) for k, v in expected.get("ole_meta", {}).items()
               if getattr(meta, k, None) != v}
        _check("%s: olefile.get_metadata() отдаёт все заложенные поля" % tag,
               not bad, "расходятся: %r" % bad)
        streams = ["/".join(p) for p in ole.listdir()]
    want = expected["streams"]
    ok = sorted(streams) == sorted(want) if exact else set(want) <= set(streams)
    _check("%s: состав потоков совпадает с заложенным" % tag, ok,
           "в файле %r, заложено %r" % (streams, want))


@_with_tmp
def test_doc(tmp):
    path, expected = fixtures.make_doc(tmp)
    rep = _check_fixture("doc", path, expected, "doc")
    _check_ole_container("doc", path, expected)

    for label in expected["unremovable_labels"]:
        found = _finds(rep, label)
        _check("doc: %r -- IDENTITY, removable=False, value содержит '1Table'" % label,
               bool(found) and all(f.risk == Risk.IDENTITY and not f.removable
                                   and "1Table" in f.value for f in found),
               repr([(f.risk.value, f.removable, f.value) for f in found]))
    _check("doc: счётчик быстрых сохранений == %d" % expected["quick_saves"],
           _vals(rep, "Счётчик быстрых сохранений") == [str(expected["quick_saves"])],
           repr(_vals(rep, "Счётчик быстрых сохранений")))
    _check("doc: язык приложения (lid) == 0x0419",
           _vals(rep, "Язык приложения (lid)") == ["0x%04X" % expected["lid"]] == ["0x0419"],
           repr(_vals(rep, "Язык приложения (lid)")))
    marks = _vals(rep, "Автор правки (SttbfRMark)")
    _check("doc: SttbfRMark -- оба автора, служебная Unknown отброшена",
           marks == list(fixtures.DOC_RMARK) and "Unknown" not in marks, repr(marks))

    # --- РЕГРЕССИЯ D2: пользовательские свойства. olefile читает их имена
    # только как UTF-8, поэтому Unicode-секция (как пишет настоящий Office) и
    # cp1251-секция теряли Purview-метку, email и значение МОЛЧА: ноль ошибок,
    # ноль находок.
    _check("doc/D2: в фикстуре есть кириллическое имя пользовательского свойства",
           any(re.search("[А-Яа-яЁё]", k) for k in expected["userdef"]),
           repr(list(expected["userdef"])))
    for name, value in expected["userdef"].items():
        label = "Польз. свойство: " + name
        found = _finds(rep, label)
        _check("doc/D2: %r == %r, risk IDENTITY" % (label, value),
               [f.value for f in found] == [value]
               and all(f.risk == Risk.IDENTITY for f in found),
               repr([(f.value, f.risk.value) for f in found]))
    _check("doc/D2: rep.errors пуст (потеря была молчаливой -- ошибок тоже не было)",
           not rep.errors, repr(rep.errors)[:300])

    # --- РЕГРЕССИЯ D1: время изменения контейнера. Раньше
    # ole.getctime("Root Entry") бросал OSError, глотаемый голым except, и
    # находки не было ни у одного файла. Дата намеренно не равна дате
    # последнего сохранения -- иначе находку можно спутать с тем свойством.
    _check("doc/D1: 'Время изменения контейнера' == %s" % expected["root_mtime"],
           _vals(rep, "Время изменения контейнера") == [expected["root_mtime"]],
           repr(_vals(rep, "Время изменения контейнера")))
    _check("doc/D1: время контейнера не равно дате последнего сохранения",
           expected["root_mtime"] not in _vals(rep, "Последнее сохранение"),
           repr(_vals(rep, "Последнее сохранение")))

    # --- времена и места находок
    edit = _vals(rep, "Общее время редактирования")
    _check("doc: 'Общее время редактирования' содержит и 8220, и '2 ч 17 мин'",
           len(edit) == 1 and "8220" in edit[0] and "2 ч 17 мин" in edit[0], repr(edit))
    _check("doc: 'Создан' == 2023-03-05 14:07:00",
           _vals(rep, "Создан") == ["2023-03-05 14:07:00"], repr(_vals(rep, "Создан")))
    for label, loc in (("Организация", "DocumentSummaryInformation"),
                       ("Руководитель", "DocumentSummaryInformation"),
                       ("Автор", "SummaryInformation")):
        locs = [f.location for f in _finds(rep, label)]
        _check("doc: location находки %r == %s" % (label, loc), locs == [loc], repr(locs))


@_with_tmp
def test_xls(tmp):
    path, expected = fixtures.make_xls(tmp)
    rep = _check_fixture("xls", path, expected, "xls")
    _check_ole_container("xls", path, expected)

    found = _finds(rep, "Имя пользователя Excel")
    _check("xls: 'Имя пользователя Excel' == 'Сидорова А. С.', IDENTITY",
           [f.value for f in found] == [fixtures.XLS_WRITEACCESS_USER]
           and all(f.risk == Risk.IDENTITY for f in found), repr([f.value for f in found]))
    sheets = _vals(rep, "Листы книги")
    _check("xls: 'Листы книги' содержит и 'Бюджет', и 'Скрытый_Расчёт'",
           len(sheets) == 1 and fixtures.XLS_SHEET in sheets[0]
           and fixtures.XLS_HIDDEN_SHEET in sheets[0], repr(sheets))
    # В expected лежат только счётчики (supbook/externsheet/hlinks), строка
    # значения собирается из них; EXTERNNAME в фикстуре нет -- 0.
    want = "SUPBOOK: %d, EXTERNSHEET: %d, EXTERNNAME: 0" % (expected["supbook"],
                                                             expected["externsheet"])
    _check("xls: 'Записи внешних связей' == %r" % want,
           _vals(rep, "Записи внешних связей") == [want],
           repr(_vals(rep, "Записи внешних связей")))
    _check("xls: 'Гиперссылки' == '2 шт.'",
           _vals(rep, "Гиперссылки") == ["%d шт." % expected["hlinks"]] == ["2 шт."],
           repr(_vals(rep, "Гиперссылки")))

    # Пользовательские свойства в однобайтовой cp1251, русское имя.
    _check("xls: в фикстуре есть кириллическое имя пользовательского свойства",
           any(re.search("[А-Яа-яЁё]", k) for k in expected["userdef"]))
    for name, value in expected["userdef"].items():
        found = _finds(rep, "Польз. свойство: " + name)
        _check("xls: 'Польз. свойство: %s' == %r, IDENTITY" % (name, value),
               [f.value for f in found] == [value]
               and all(f.risk == Risk.IDENTITY for f in found),
               repr([(f.value, f.risk.value) for f in found]))
    _check("xls: rep.errors пуст", not rep.errors, repr(rep.errors)[:300])


@_with_tmp
def test_ppt(tmp):
    """РЕГРЕССИЯ D5: ANSI-имя в CurrentUserAtom -- cp1251, а не cp1252.
    Раньше 'Петров' выходило как 'Ïåòðîâ'. Обе формы атома: с UTF-16-именем
    (PowerPoint 2000+) и только с ANSI (тогда единственный источник -- cp1251).
    """
    label = "Имя последнего редактора (CurrentUserAtom)"
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
    """РЕГРЕССИЯ D7: поток CompObj лежит и в корне, и внутри встроенного
    объекта; инспектор не имеет права путать вложенное с корневым."""
    path, expected = fixtures.make_doc_active(tmp)
    rep = _check_fixture("doc_active", path, expected, "doc")

    for bad in expected["not_errors"]:
        hits = [e for e in rep.errors if bad in e]
        _check("doc_active/D7: в errors нет записей с %r" % bad, not hits, repr(hits)[:300])
    # Вложенное не путается с корневым: у встроенного объекта свой CompObj
    # (Excel.Sheet.8), у самого документа -- свой (Word.Document.8).
    _check("doc_active/D7: ProgID корня == Word.Document.8, а не вложенного Excel.Sheet.8",
           _vals(rep, "ProgID") == ["Word.Document.8"], repr(_vals(rep, "ProgID")))
    _check("doc_active: сигнал hazard про VBA",
           any(s.kind == "hazard" and "VBA" in s.detail for s in rep.signals),
           repr([(s.kind, s.detail[:60]) for s in rep.signals]))

    objs = _finds(rep, "Вложенные OLE-объекты")
    _check("doc_active: 'Вложенные OLE-объекты' IDENTITY, removable=False, '1 объект(ов): %s'"
           % expected["object_id"],
           len(objs) == 1 and objs[0].risk == Risk.IDENTITY and not objs[0].removable
           and objs[0].value.startswith("1 объект(ов): " + expected["object_id"]),
           repr([(f.risk.value, f.removable, f.value) for f in objs]))
    macros = _finds(rep, "Поток макросов")
    _check("doc_active: 'Поток макросов' содержит %s и removable=False" % expected["macro_stream"],
           any(expected["macro_stream"] in f.value for f in macros)
           and all(not f.removable for f in macros),
           repr([(f.value, f.removable) for f in macros]))
    for label in expected["unremovable_labels"]:
        found = _finds(rep, label)
        _check("doc_active: %r есть и все такие находки removable=False" % label,
               bool(found) and all(not f.removable for f in found))


# Дочерний процесс для векторной бомбы: время меряет он сам, вывод -- ASCII-JSON
# (консоль и кодировки не участвуют). Файл-зонд пишется в utf-8.
_VECTOR_PROBE = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from core.insp_legacy import inspect_ole
t0 = time.time()
rep = inspect_ole(sys.argv[2], "doc")
print(json.dumps({"elapsed": time.time() - t0,
                  "author": [f.value for f in rep.findings if f.label == "Автор"]}))
"""


@_with_tmp
def test_ole_vector_bomb(tmp):
    """РЕГРЕССИЯ D6 (отказ в обслуживании): VT_VECTOR|VT_R8 со счётчиком
    0xFFFFFFFF. olefile разбирал его циклом на четыре миллиарда пустых
    итераций и списком на десятки гигабайт -- бот зависал вместе с разбором.
    Хватает одного испорченного байта, злого умысла не нужно."""
    summary = fixtures._ole_section([
        (1, fixtures._vt_i2(1251)),
        (3, struct.pack("<HHI", 0x1005, 0, 0xFFFFFFFF)),     # 4 млрд элементов
        (4, fixtures._vt_lpstr(fixtures.DOC_AUTHOR, "cp1251"))])
    path = _write_cfb(tmp, "vector_bomb.doc", [
        ("\x05SummaryInformation",
         fixtures._ole_propstream((fixtures._FMTID_SUMMARY, summary))),
        ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)])

    # Регрессия -- это ЗАВИСАНИЕ (и рост памяти), поэтому разбор идёт в дочернем
    # процессе с жёстким таймаутом: при регрессии тест честно падает, процесс
    # убит, а сводка прогона не теряется (watchdog в самом процессе убил бы
    # заодно и весь раннер).
    probe = os.path.join(tmp, "probe_vector_bomb.py")
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write(_VECTOR_PROBE)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        proc = subprocess.run([sys.executable, probe, root, path],
                              capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        _check("vector_bomb/D6: inspect_ole вернулся (не завис на счётчике 0xFFFFFFFF)",
               False, "нет ответа за 30 с, дочерний процесс убит")
        return
    try:
        got = json.loads(proc.stdout.decode("ascii"))
    except ValueError:
        _check("vector_bomb/D6: дочерний процесс вернул результат", False,
               "код %s, stderr: %s" % (proc.returncode,
                                       proc.stderr.decode("utf-8", "replace")[-300:]))
        return
    _check("vector_bomb/D6: inspect_ole вернулся быстрее 5 с",
           got["elapsed"] < 5.0, "заняло %.2f с" % got["elapsed"])
    _check("vector_bomb/D6: 'Автор' при этом найден (остальные свойства читаются)",
           got["author"] == [fixtures.DOC_AUTHOR], repr(got["author"]))


@_with_tmp
def test_ole_codepage_and_null_filetime(tmp):
    """РЕГРЕССИЯ D3 и D4. У двух потоков свойств СВОИ кодовые страницы:
    SummaryInformation в 1252, DocumentSummaryInformation в 1251 -- строки
    второго читаем в его странице, иначе 'ООО «Ромашка»' выходит как
    'ÎÎÎ «Ðîìàøêà»' (D3). Нулевой FILETIME -- 'не задано', а не дата 1601 года:
    из-за него 'сохранено раньше создания' давало ложный inconsistent (D4)."""
    summary = fixtures._ole_section([
        (1, fixtures._vt_i2(1252)),
        (4, fixtures._vt_lpstr("John Smith", "cp1252")),
        (11, fixtures._vt_filetime(0)),                       # "не печатали"
        (12, fixtures._vt_filetime(fixtures._filetime(fixtures.DOC_CREATE))),
        (13, fixtures._vt_filetime(0))])                      # сохранение не задано
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

    company = _vals(rep, "Организация", "DocumentSummaryInformation")
    _check("codepage/D3: 'Организация' == 'ООО «Ромашка»' (а не 'ÎÎÎ «Ðîìàøêà»')",
           company == [fixtures.DOC_COMPANY], repr(company))
    _check("codepage/D3: 'Автор' из секции cp1252 == 'John Smith'",
           _vals(rep, "Автор") == ["John Smith"], repr(_vals(rep, "Автор")))
    _check("codepage/D4: 'Последняя печать' пусто (нулевой FILETIME -- не дата)",
           _vals(rep, "Последняя печать") == [""], repr(_vals(rep, "Последняя печать")))
    _check("codepage/D4: нет ложного сигнала kind=inconsistent",
           not any(s.kind == "inconsistent" for s in rep.signals),
           repr([(s.kind, s.detail[:80]) for s in rep.signals]))


@_with_tmp
def test_xls_with_embedded_word(tmp):
    """Книга Excel со встроенным объектом Word остаётся книгой. Раньше sniff
    искал имена потоков плоско по всему дереву и находил хранилище
    MBD.../WordDocument -- книга определялась как 'doc' и её разбирал не тот
    инспектор. Теперь сравнивается только верхний уровень ole.listdir().
    Чистить файл не тем инспектором опаснее, чем не чистить вообще."""
    summary, _docsum = fixtures._ole_props_streams()
    workbook = (struct.pack("<HH", 0x0809, 16) + b"\x00" * 16      # BOF
                + struct.pack("<HH", 0x000A, 0))                    # EOF
    path = _write_cfb(tmp, "xls_with_word.xls", [
        ("\x05SummaryInformation", summary),
        ("Workbook", workbook),
        (("MBD0001A2B3", "\x01CompObj"),
         fixtures._ole_compobj("Word", "MSWordDoc", "Word.Document.8", fixtures._CLSID_WORD8)),
        (("MBD0001A2B3", "WordDocument"), b"\xec\xa5" + b"\x00" * 4200)])

    _check("xls+word: sniff == 'xls' (встроенный Word не превращает книгу в документ)",
           sniff.sniff(path) == "xls", sniff.sniff(path))
    rep = inspect_file(path)
    _check("xls+word: inspect_file -> fmt 'xls'", rep.fmt == "xls", rep.fmt)
    _check("xls+word: есть находка 'Поток рабочей книги'",
           bool(_vals(rep, "Поток рабочей книги")))
    _check("xls+word: НЕТ находки 'Остаточный текст и таблица авторов правок' (чужой Word)",
           not _finds(rep, "Остаточный текст и таблица авторов правок"))
    _check("xls+word: rep.errors пуст", not rep.errors, repr(rep.errors)[:300])

    # Вторая линия обороны: даже если fmt передан неверно, разбор идёт по
    # потокам верхнего уровня, а несовпадение уходит в сигнал inconsistent.
    forced = inspect_ole(path, "doc")
    _check("xls+word: inspect_ole(fmt='doc') всё равно разбирает книгу, а не чужой Word",
           bool(_vals(forced, "Поток рабочей книги"))
           and not _finds(forced, "Остаточный текст и таблица авторов правок"))
    _check("xls+word: inspect_ole(fmt='doc') -- сигнал inconsistent про расхождение",
           any(s.kind == "inconsistent" for s in forced.signals))


@_with_tmp
def test_ole_idempotent_and_truncated(tmp):
    """Read-only на всех OLE2-фикстурах и устойчивость к обрезанным копиям."""
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
            except Exception as exc:  # noqa: BLE001 -- именно это и проверяем
                _check("%s обрезан до %d байт: inspect_file не бросил исключение" % (name, n),
                       False, "%s: %s" % (type(exc).__name__, exc))
                continue
            _check("%s обрезан до %d байт: вернулся Report, быстро" % (name, n),
                   isinstance(rep, Report) and elapsed < 5.0,
                   "%s, %.2f с" % (type(rep).__name__, elapsed))


# ---------------------------------------------------------------------------
# Должен идти ПОСЛЕДНИМ (раннер сортирует тесты по имени): подмены швов sniff
# из блока 1 не имеют права пережить свои тесты.
# ---------------------------------------------------------------------------


def test_zz_seams_restored():
    _check("швы: sniff._read_head восстановлен после подмен",
           sniff._read_head is _REAL_READ_HEAD)
    _check("швы: sniff._win_attrs восстановлен после подмен",
           sniff._win_attrs is _REAL_WIN_ATTRS)


# ---------------------------------------------------------------------------
# запуск
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
            _check("%s: без необработанных AssertionError" % fn.__name__, False, str(exc))
        except Exception as exc:  # noqa: BLE001 -- тест не должен останавливать остальные
            _check("%s: без необработанного исключения" % fn.__name__, False,
                   "%s: %s" % (type(exc).__name__, exc))

    print("=" * 78)
    total = len(_RESULTS)
    failed = [r for r in _RESULTS if not r[1]]
    print("ИТОГО: %d проверок, %d провалено" % (total, len(failed)))
    if failed:
        print("ПРОВАЛЕННЫЕ ПРОВЕРКИ:")
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
