# -*- coding: utf-8 -*-
"""Диспетчер инспекции и CLI.

Фаза 0: только чтение. Файл никогда не модифицируется, ничего не чистится --
задача диспетчера только собрать полную картину из готовых инспекторов.

Определяет формат через core.sniff и маршрутизирует по core.sniff.family на
один из готовых инспекторов (core.insp_*). Сам диспетчер использует только
stdlib; тяжёлые зависимости (pikepdf, lxml, olefile) тянутся транзитивно
через инспекторы, которые он вызывает.

Главная функция:
    inspect_file(path, max_size=None) -> Report

Диспетчер НЕ ИМЕЕТ ПРАВА падать: он вызывается из бота на любом файле, какой
пользователь пришлёт, включая намеренно битые и намеренно опасные. Каждый
вызов инспектора обёрнут в try/except; если инспектор всё-таки исключение
бросил (не должен, но мир не идеален) -- это тоже уходит в report.errors,
а не наружу.

Форматы без специализированного инспектора (ZIP общего вида, текст/CSV,
неопознанный формат) разбираются минимально прямо здесь -- заводить для
них отдельный модуль было бы избыточно.

Рекурсия. У OOXML и ODF свои media-каталоги с картинками (Exif/XMP/ICC там
свои, не в docProps), у OOXML вдобавок вложенные OLE-объекты в */embeddings/,
у PDF -- вложенные файлы в /Names /EmbeddedFiles. Сами инспекторы про это
только сообщают ("нужна рекурсивная инспекция") -- достать и рекурсивно
прогнать эти части должен диспетчер: contract insp_ooxml.py и insp_legacy.py
явно перекладывает эту работу на него. Рекурсия ограничена по глубине, числу
вложенных объектов и суммарному извлечённому объёму -- см. константы ниже.

CLI: python -m core.inspect <файл> [ещё файлы...] [--json | --quiet] [--selftest]
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

# --- пределы рекурсии: вложенный объект -- такой же недоверенный файл, как
# и верхний, поэтому бомбы и раздувание числа объектов режем так же жёстко.
MAX_RECURSE_DEPTH = 2          # media в docx внутри docx внутри docx и т.д. -- не более 2 уровней
MAX_RECURSE_OBJECTS = 40       # вложенных объектов суммарно за всю инспекцию файла
MAX_RECURSE_TOTAL = 200 * 1024 * 1024   # суммарный извлечённый объём
MAX_ITEM_READ = 50 * 1024 * 1024        # один вложенный объект -- не больше этого в память

# --- пределы минимальных инспекторов (zip/text) -----------------------------
MAX_ZIP_SCAN = 4000             # записей архива, которые вообще разбираем
MAX_ZIP_LISTED = 40             # сколько таймстемпов печатаем поимённо
MAX_ZIP_UNPACKED = 512 * 1024 * 1024   # порог бомбы: суммарный несжатый размер
MIN_ZIP_BOMB = 64 * 1024 * 1024        # ниже этого объёма высокий коэффициент не тревожит
MAX_ZIP_RATIO = 120                    # несжатый/сжатый -- порог подозрения на бомбу
MAX_TEXT_PEEK = 1024 * 1024      # для BOM и стиля переводов строк целый файл не нужен

_OOXML_MEDIA_RE = re.compile(r"^(word|xl|ppt)/media/")


# ============================================================================
#  Минимальные инспекторы: форматы без отдельного модуля
# ============================================================================


def _inspect_zip_min(path, fmt="zip") -> Report:
    """ZIP общего вида (не OOXML и не ODF -- те определяет sniff раньше).

    Содержимое записей не распаковываем вообще -- только перечень и заявленные
    в заголовке размеры и таймстемпы. Поэтому коэффициент сжатия здесь не
    защита от настоящей бомбы (распаковки нет), а просто предупреждение
    пользователю, что архив выглядит подозрительно.
    """
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)
    except OSError as exc:
        rep.err("не удалось узнать размер файла: %s" % exc)
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            total = len(infos)
            if total > MAX_ZIP_SCAN:
                rep.err("в архиве %d записей, разобраны первые %d" % (total, MAX_ZIP_SCAN))
                infos = infos[:MAX_ZIP_SCAN]
            total_unc = sum(i.file_size for i in infos)
            total_comp = sum(i.compress_size for i in infos)
            ratio = (total_unc / total_comp) if total_comp else 0
            if total_unc > MAX_ZIP_UNPACKED or (total_unc > MIN_ZIP_BOMB and ratio > MAX_ZIP_RATIO):
                rep.err("архив похож на zip-бомбу: заявлено %d байт при коэффициенте "
                        "сжатия %.0f -- содержимое записей не распаковываем" % (total_unc, ratio))
                rep.signal("hazard",
                           "ZIP-архив с подозрительно высоким коэффициентом сжатия (%.0f)." % ratio,
                           "high")
            rep.add(Risk.PROVENANCE, "ZIP", "Записей в архиве", str(total))
            for info in infos[:MAX_ZIP_LISTED]:
                dt = "%04d-%02d-%02d %02d:%02d:%02d" % info.date_time
                rep.add(Risk.PROVENANCE, info.filename, "Таймстемп записи ZIP", dt,
                        "Время модификации файла внутри архива -- метка сборки, "
                        "привязывает архив к моменту создания.")
            if total > MAX_ZIP_LISTED:
                rep.err("таймстемпы показаны для первых %d записей из %d" % (MAX_ZIP_LISTED, total))
    except (zipfile.BadZipFile, OSError) as exc:
        rep.err("ZIP-архив не открывается: %s: %s" % (type(exc).__name__, exc))
    return rep


def _inspect_text_min(path, fmt="text") -> Report:
    """Текст/CSV: BOM и стиль переводов строк -- дальше в текст не лезем,
    произвольный текстовый контент к метаданным документа не относится."""
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)
    except OSError as exc:
        rep.err("не удалось узнать размер файла: %s" % exc)
    try:
        with open(path, "rb") as fh:
            head = fh.read(MAX_TEXT_PEEK)
    except OSError as exc:
        rep.err("файл не читается: %s" % exc)
        return rep

    bom = None
    for label, sig in (("UTF-8 BOM", b"\xef\xbb\xbf"),
                       ("UTF-16 LE BOM", b"\xff\xfe"),
                       ("UTF-16 BE BOM", b"\xfe\xff")):
        if head.startswith(sig):
            bom = label
            break
    rep.add(Risk.PROVENANCE, "BOM", "Метка порядка байт", bom or "нет",
            "BOM выдаёт редактор или платформу, которой сохраняли файл." if bom else "")

    crlf = head.count(b"\r\n")
    lf_only = head.count(b"\n") - crlf
    if crlf and not lf_only:
        style = "CRLF (типично для Windows)"
    elif lf_only and not crlf:
        style = "LF (типично для Unix/Mac)"
    elif crlf and lf_only:
        style = "смешанный CRLF и LF"
    else:
        style = "переводов строк нет"
    rep.add(Risk.ENVIRONMENT, "line-endings", "Стиль переводов строк", style,
            "CRLF почти всегда означает, что файл правили или собирали на Windows.")

    if rep.size and rep.size > len(head):
        rep.err("файл больше %d МБ, BOM и переводы строк определены по первым %d МБ"
                % (MAX_TEXT_PEEK // 1048576, MAX_TEXT_PEEK // 1048576))
    return rep


def _inspect_unknown_min(path, fmt="unknown") -> Report:
    """Формат не опознан сигнатурой. Глубже сигнатуры и размера лезть смысла
    нет -- неизвестно, как это парсить."""
    rep = Report(path=str(path), fmt=fmt)
    size_known = False
    try:
        rep.size = os.path.getsize(path)
        size_known = True
    except OSError as exc:
        rep.err("не удалось узнать размер файла: %s" % exc)
    if size_known and rep.size == 0:
        # Пустой файл -- не "формат не опознан": опознавать нечего.
        rep.err("файл пустой (0 байт): в нём нет ни данных, ни метаданных. "
                "Возможно, он не докачался или не сохранился -- проверьте источник.")
        return rep
    rep.err("формат не опознан по сигнатуре -- глубокая инспекция невозможна, "
            "известны только размер и факт нераспознавания.")
    return rep


# Что сказать пользователю, когда файл не открылся. Правда и действие, без
# путей: текст исключения OSError содержит полный путь, поэтому его не берём.
_UNREADABLE_MSG = {
    "cloud": (
        "файл -- плейсхолдер облачного провайдера (OneDrive и аналоги, режим "
        "Files On-Demand): на диске лежат только метаданные, содержимого нет, "
        "поэтому проверить его нельзя. Это не значит, что файл повреждён. "
        "Откройте файл в проводнике или выберите «Always keep on this device» "
        "(«Всегда хранить на этом устройстве»), дождитесь скачивания и "
        "повторите проверку."),
    "denied": (
        "нет доступа на чтение (errno 13): либо нет прав на файл, либо он занят "
        "другим процессом (чаще всего открыт в Word или Excel). Закройте "
        "файл, проверьте права доступа и повторите проверку."),
    "missing": "файл не найден (удалён или переименован до начала проверки).",
    "dir": "это каталог, а не файл.",
    "reparse": (
        "файл -- ссылка или точка повторной обработки (reparse point), её "
        "содержимое не читается. Если файл из облака, откройте его в "
        "проводнике, дождитесь скачивания и повторите проверку."),
}


def _inspect_unreadable(path, fmt="unreadable") -> Report:
    """Файл существует, но не открылся. Не 'не опознан' (это ложный диагноз,
    после которого пользователь ищет несуществующую поломку), а честная
    причина и что делать."""
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)   # у плейсхолдера размер настоящий
    except OSError:
        pass
    reason, code = sniff.unreadable_reason(path)
    msg = _UNREADABLE_MSG.get(reason)
    if msg is None:
        try:
            text = os.strerror(code) if code is not None else ""
        except (ValueError, OverflowError):
            text = ""
        what = ("errno %s%s" % (code, ": " + text if text else "")) if code is not None             else "код ошибки ОС не получен"
        msg = "файл не удалось прочитать (%s) -- содержимое недоступно, проверка невозможна." % what
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
    """Вызвать нужный инспектор, поймав абсолютно всё. Контракт инспекторов
    говорит, что они сами не бросают -- но диспетчер не имеет права поверить
    на слово и упасть, если контракт где-то нарушен."""
    handler = _HANDLERS.get(fam, _inspect_unknown_min)
    try:
        return handler(path, fmt)
    except Exception as exc:  # noqa: BLE001 -- последний рубеж диспетчера
        rep = Report(path=str(path), fmt=fmt)
        try:
            rep.size = os.path.getsize(path)
        except OSError:
            pass
        rep.err("инспектор упал целиком: %s: %s" % (type(exc).__name__, exc))
        return rep


# ============================================================================
#  Рекурсия во вложенные объекты
# ============================================================================


def _zip_entry_bytes(zf, info, cap):
    """Прочитать запись ZIP потоково, не больше cap байт. None, если запись
    больше -- заявленный в заголовке размер недоверенный (в т.ч. может лгать
    в обе стороны), поэтому ограничиваем сам поток чтения, а не заголовок."""
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
    """Сколько байт можно прочитать для следующего вложенного объекта, или
    None, если общий бюджет рекурсии (число объектов или суммарный объём)
    исчерпан."""
    if budget["objects"] >= MAX_RECURSE_OBJECTS:
        return None
    remaining = MAX_RECURSE_TOTAL - budget["bytes"]
    if remaining <= 0:
        return None
    return min(MAX_ITEM_READ, remaining)


def _extract_zip_children(rep, path, tmpdir, budget, match, kind_label):
    """Общая часть для OOXML (media/, */embeddings/) и ODF (Pictures/):
    оба контейнера -- обычный ZIP, различается только то, какие имена в нём
    интересны."""
    out = []
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        rep.err("рекурсия: %s-контейнер повторно не открылся: %s" % (kind_label, exc))
        return out
    with zf:
        idx = 0
        for scanned, info in enumerate(zf.infolist()):
            if scanned >= MAX_ZIP_SCAN:
                break
            name = info.filename
            if not match(name):
                continue
            cap = _reserve(budget)
            if cap is None:
                rep.err("рекурсия: бюджет вложенных объектов исчерпан в %s, "
                        "часть вложений не инспектирована" % kind_label)
                break
            data = _zip_entry_bytes(zf, info, cap)
            if data is None:
                rep.err("рекурсия: %s больше лимита %d МБ, пропущен" % (name, cap // 1048576))
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
    """Обход /Names-дерева /EmbeddedFiles. Глубина и посещённые objgen
    ограничены так же, как в insp_pdf._walk_name_tree -- /Kids может по кругу
    ссылаться на один и тот же узел (DAG-бомба), не только вглубь."""
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
    """Вложенные файлы PDF: /Names /EmbeddedFiles. insp_pdf уже перечисляет
    их имена и метаданные, но не сами байты -- байты нужны диспетчеру, чтобы
    прогнать вложение соответствующим инспектором, поэтому дерево имён
    приходится обходить второй раз, здесь."""
    out = []
    import pikepdf  # уже обязательная зависимость инспектора PDF

    try:
        pdf = pikepdf.open(path)
    except Exception as exc:
        rep.err("рекурсия: PDF повторно не открылся: %s" % exc)
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
                    rep.err("рекурсия: бюджет вложенных объектов исчерпан в PDF, "
                            "часть вложений не инспектирована")
                    break
                try:
                    ef = spec.get("/EF")
                    stream = (ef.get("/F") or ef.get("/UF")) if ef is not None else None
                    if stream is None:
                        continue
                    # сначала сырой (сжатый) размер -- та же защита от бомбы,
                    # что и в insp_pdf._stream_text, до полной распаковки.
                    if len(stream.read_raw_bytes()) > cap:
                        rep.err("рекурсия: вложение PDF %r больше лимита %d МБ, пропущено"
                                % (label, cap // 1048576))
                        continue
                    data = stream.read_bytes()
                    if len(data) > cap:
                        rep.err("рекурсия: вложение PDF %r распаковалось больше лимита "
                                "%d МБ, пропущено" % (label, cap // 1048576))
                        continue
                except Exception as exc:
                    rep.err("рекурсия: вложение PDF %r не читается: %s" % (label, exc))
                    continue
                idx += 1
                budget["objects"] += 1
                budget["bytes"] += len(data)
                dest = _write_tmp(tmpdir, idx, label or ("attach%d" % idx))
                with open(dest, "wb") as fh:
                    fh.write(data)
                out.append((label or "вложение %d" % idx, dest))
    except Exception as exc:
        rep.err("рекурсия: обход вложений PDF прерван: %s" % exc)
    return out


def _merge(rep, child_rep, label):
    """Влить находки вложенного отчёта в основной, добавив к location
    префикс с именем части: "word/media/image1.jpeg > APP1:Exif"."""
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
        except Exception as exc:  # noqa: BLE001 -- один вложенный объект не должен ронять остальные
            rep.err("%s: рекурсивная инспекция вложения упала: %s: %s" % (label, type(exc).__name__, exc))


# ============================================================================
#  Публичная точка входа
# ============================================================================


def inspect_file(path, max_size=None) -> Report:
    """Полная read-only инспекция одного файла. Никогда не бросает исключение
    -- это единственная функция, которую дёргает бот, и падать ей нельзя
    ни на каком входе.

    max_size -- если задан и файл больше, инспекция не запускается вовсе
    (даже сигнатура не разбирается вложенных объектов): отчёт получает только
    размер и запись в errors. Это защита бота от разбора гигантских вложений,
    а не бомб внутри маленького файла -- от тех защищает рекурсия ниже.
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
        rep.err("файл больше лимита %d байт (фактический размер %d байт), "
                "инспекция не выполнена" % (max_size, size))
        return rep

    fam = sniff.family(fmt)
    rep = _run_inspector(path, fam, fmt)

    tmpdir = tempfile.mkdtemp(prefix="inspect_")
    try:
        _recurse_into(rep, path, fam, fmt, "", 1, {"objects": 0, "bytes": 0}, tmpdir)
    except Exception as exc:  # noqa: BLE001 -- рекурсия не должна ронять основной отчёт
        rep.err("рекурсивная инспекция вложений упала целиком: %s: %s" % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return rep


# ============================================================================
#  CLI
# ============================================================================


def _json_default(obj):
    """Risk -- str-Enum, json уже умеет его как строку; это на случай, если
    когда-нибудь в отчёт попадёт что-то ещё, чего json нативно не понимает."""
    if isinstance(obj, Enum):
        return obj.value
    return str(obj)


def _to_json(report) -> str:
    return json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2,
                       default=_json_default)


def _selftest():
    """Самопроверка диспетчера: маршрутизация по формату, минимальные
    инспекторы, рекурсия и то, что диспетчер не падает на битом входе.
    Не заменяет прогон на реальном файле из задания -- дополняет его."""
    tmp = tempfile.mkdtemp(prefix="inspect_selftest_")
    try:
        # 1. Неопознанный формат: не падает, размер известен, есть errors.
        junk_data = b"\x01\x02\x03garbage\x00\xff" * 10
        junk = os.path.join(tmp, "junk.bin")
        with open(junk, "wb") as fh:
            fh.write(junk_data)
        rep = inspect_file(junk)
        assert rep.fmt == "unknown", rep.fmt
        assert rep.size == len(junk_data), rep.size
        assert rep.errors

        # 2. Текст: BOM + CRLF.
        txt = os.path.join(tmp, "notes.txt")
        with open(txt, "wb") as fh:
            fh.write(b"\xef\xbb\xbfline1\r\nline2\r\n")
        rep = inspect_file(txt)
        assert rep.fmt == "text", rep.fmt
        values = {f.label: f.value for f in rep.findings}
        assert values.get("Метка порядка байт") == "UTF-8 BOM", values
        assert "CRLF" in values.get("Стиль переводов строк", ""), values

        # 3. ZIP общего вида: запись и её таймстемп попадают в отчёт.
        zpath = os.path.join(tmp, "arch.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("readme.txt", b"hello")
        rep = inspect_file(zpath)
        assert rep.fmt == "zip", rep.fmt
        assert any(f.location == "readme.txt" for f in rep.findings), rep.findings

        # 4. max_size обрывает инспекцию до разбора содержимого.
        rep = inspect_file(zpath, max_size=1)
        assert rep.errors and not rep.findings, rep

        # 5. Рекурсия: картинка в word/media/ вливается в отчёт docx
        #    отдельным вложенным объектом (part).
        docx = os.path.join(tmp, "sample.docx")
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types xmlns='x'/>")
            zf.writestr("word/document.xml", "<w:document xmlns:w='x'/>")
            zf.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        rep = inspect_file(docx)
        assert rep.fmt == "docx", rep.fmt
        assert any(p["name"] == "word/media/image1.png" for p in rep.parts), rep.parts

        # 6. Файл с ZIP-сигнатурой, но битым содержимым -- диспетчер не падает.
        broken = os.path.join(tmp, "broken.docx")
        with open(broken, "wb") as fh:
            fh.write(b"PK\x03\x04" + os.urandom(64))
        rep = inspect_file(broken)
        assert isinstance(rep, Report)

        # 7. Пустой файл: не "формат не опознан", а "пустой".
        empty = os.path.join(tmp, "empty.docx")
        open(empty, "wb").close()
        rep = inspect_file(empty)
        assert rep.fmt == "unknown" and rep.size == 0, (rep.fmt, rep.size)
        assert any("пустой" in e for e in rep.errors), rep.errors

        # 8. Файл не открывается: каждая причина -- своё сообщение, без пути
        #    и без ложного "формат не опознан". Настоящих плейсхолдеров в CI нет,
        #    поэтому подменяем швы sniff: чтение головы и атрибуты Windows.
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
            assert "не опознан" not in text, text
            assert tmp not in text and "doc.bin" not in text, text   # путь не утёк
            return rep

        rep = _case(OSError(22, "Invalid argument"), 0x00400020, "плейсхолдер")
        assert rep.size == 321, rep.size
        assert "Always keep on this device" in rep.errors[0]
        _case(OSError(22, "x"), 0x00040000, "плейсхолдер")       # RECALL_ON_OPEN
        _case(OSError(22, "x"), 0x00001020, "плейсхолдер")       # OFFLINE
        _case(OSError(22, "x"), 0x00000420, "reparse point")     # одна ссылка -- не облако
        _case(OSError(22, "x"), 0x20, "errno 22")                # атрибуты есть, облака нет
        _case(OSError(22, "x"), None, "errno 22")                # не Windows / вызов не удался
        _case(PermissionError(13, "x"), 0x20, "нет доступа на чтение")
        _case(FileNotFoundError(2, "x"), None, "не найден")
        rep = inspect_file(os.path.join(tmp, "нет_такого.docx"))   # по-настоящему
        assert rep.fmt == "unreadable" and any("не найден" in e for e in rep.errors), rep.errors
        rep = inspect_file(tmp)                                      # каталог
        assert rep.fmt == "unreadable" and any("каталог" in e for e in rep.errors), rep.errors

        # 9. sniff на нечитаемом не бросает и не путает с 'unknown'.
        assert sniff.sniff(os.path.join(tmp, "нет_такого.docx")) == "unreadable"
        assert sniff.sniff("bad" + chr(0) + "path") == "unreadable"
        assert sniff.unreadable_reason("bad" + chr(0) + "path") == ("other", None)

        print("OK: самопроверка диспетчера пройдена")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    # Консоль Windows по умолчанию cp866/cp1251 -- кириллический отчёт на ней
    # падает с UnicodeEncodeError. Перекодируем поток вывода прямо тут, до
    # первой печати.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        prog="python -m core.inspect",
        description="Инспекция метаданных документа (read-only, фаза 0).")
    parser.add_argument("files", nargs="*", help="путь(и) к файлу(ам)")
    parser.add_argument("--json", action="store_true", help="вывести отчёт в JSON")
    parser.add_argument("--quiet", action="store_true", help="вывести только сводную строку")
    parser.add_argument("--selftest", action="store_true",
                        help="самопроверка диспетчера без входных файлов")
    args = parser.parse_args(argv)

    if args.selftest:
        _selftest()
        return 0
    if not args.files:
        parser.error("нужен хотя бы один файл (или --selftest)")

    exit_code = 0
    for path in args.files:
        try:
            report = inspect_file(path)
        except Exception as exc:  # noqa: BLE001 -- CLI не должен падать даже если контракт нарушен
            print("Не удалось инспектировать %r: %s: %s" % (path, type(exc).__name__, exc))
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
