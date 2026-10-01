"""Инспекторы старых и альтернативных форматов: OLE2, OpenDocument, RTF.

Фаза 0: только чтение. Файл открывается строго на чтение, ничего не пишется.

Три публичные функции:
    inspect_ole(path, fmt="doc") -> Report    # .doc / .xls / .ppt (OLE2)
    inspect_odf(path, fmt="odt") -> Report    # .odt / .ods / .odp
    inspect_rtf(path)            -> Report    # .rtf

Ни одна из них не бросает исключение наружу: любая проблема разбора уходит
в report.err(). Нам присылают в том числе повреждённые и намеренно кривые
файлы -- падение инспектора это отказ в обслуживании, а не диагностика.
"""

from __future__ import annotations

import datetime
import os
import re
import struct
import zipfile

from core.model import Report, Risk, clip

# --- предохранители от бомб -------------------------------------------------
MAX_READ = 50 * 1024 * 1024        # больше этого куска в память не берём никогда
MAX_UNPACKED = 300 * 1024 * 1024   # суммарный несжатый размер ZIP
MAX_RATIO = 200                    # несжатый/сжатый; выше -- похоже на zip-бомбу
MAX_PARTS = 3000                   # сколько записей контейнера вообще разбираем
MAX_LIST = 60                      # сколько однотипных находок печатаем списком

RTF_HEAD = 4 * 1024 * 1024         # шапка RTF: \info, генератор, таблицы rsid
RTF_CHUNK = 1 * 1024 * 1024        # размер куска при потоковом проходе
RTF_OVERLAP = 8 * 1024             # нахлёст, чтобы не разрезать совпадение


# ============================================================================
#  Общие мелочи
# ============================================================================

def _size(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _dt(value) -> str:
    """Дата из метаданных в читаемый вид. Что угодно -- в строку."""
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _dur_secs(seconds) -> str:
    """Секунды правки -> '3 ч 12 мин (11520 с)'."""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if total <= 0:
        return "0 (файл не редактировали или счётчик сброшен)"
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    human = " ".join(p for p in (
        "%d ч" % h if h else "",
        "%d мин" % m if m else "",
        "%d с" % s if s and not h else "",
    ) if p)
    return "%s (всего %d с)" % (human or "меньше минуты", total)


def _dur_iso(text) -> str:
    """ISO 8601 duration ('PT1H23M45S', 'P2DT3H') -> читаемая строка."""
    if not text:
        return ""
    m = re.match(
        r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$",
        str(text).strip(),
    )
    if not m:
        return str(text)
    years, months, days, hours, mins, secs = m.groups()
    chunks = [
        ("%s г" % years) if years else "",
        ("%s мес" % months) if months else "",
        ("%s дн" % days) if days else "",
        ("%s ч" % hours) if hours else "",
        ("%s мин" % mins) if mins else "",
        ("%s с" % secs) if secs else "",
    ]
    human = " ".join(c for c in chunks if c)
    return "%s [%s]" % (human or "0", text)


# Что считаем следом генерации библиотекой/скриптом, а не живым человеком.
_LIB_MARKERS = (
    "odfpy", "python", "relatorio", "appy", "pandas", "jasper", "docutils",
    "pandoc", "reportlab", "php", "phpword", "node", "javascript", "gpt",
    "openai", "anthropic", "claude", "copilot", "aspose", "docxtemplater",
    "ruby", "perl", "golang", "rust", "automation", "headless", "script",
)


def _looks_generated(text) -> str:
    low = (text or "").lower()
    for marker in _LIB_MARKERS:
        if marker in low:
            return marker
    return ""


# Абсолютные пути и локальные URI. Значения недоверенные -- только печатаем.
#
# UNC-ветка ловит "2 и больше backslash". Это не опечатка: в XML (ODF) путь
# приходит уже декодированным -- там backslash ровно два. А в потоке RTF
# (_rtf_scan работает на СЫРОМ, неэкранированном тексте) каждый настоящий
# backslash по спеке RTF записан ДВОЙНЫМ ("\\" -> 2 байта в файле), поэтому
# реальный "\\сервер\шара" лежит в байтах как 4+2 backslash. Жёсткое "ровно 2"
# находило совпадение со сдвигом на один backslash и теряло начало пути.
_PATH_RE = re.compile(
    r"(?:file:///[^\s\"'<>{}|^`]+"
    r"|[A-Za-z]:[\\/][^\s\"'<>{}|*?]{2,}"
    r"|\\{2,}[A-Za-z0-9_.$-]+\\+[^\s\"'<>{}|*?]{1,}"
    r"|/(?:home|Users|mnt|media|srv|var/folders)/[^\s\"'<>{}|*?]{2,})"
)


# ============================================================================
#  А. OLE2 -- .doc / .xls / .ppt
# ============================================================================

# Свойства SummaryInformation / DocumentSummaryInformation:
# атрибут olefile -> (риск, человекочитаемое имя, пояснение, вид значения)
_OLE_PROPS = (
    ("author", Risk.IDENTITY, "Автор",
     "Имя пользователя Windows или подпись в настройках Office.", "s"),
    ("last_saved_by", Risk.IDENTITY, "Кто сохранял последним",
     "Имя того, кто последним нажал 'Сохранить' -- часто НЕ автор.", "s"),
    ("company", Risk.IDENTITY, "Организация",
     "Название компании из лицензии Office.", "s"),
    ("manager", Risk.IDENTITY, "Руководитель",
     "Поле 'Руководитель' в свойствах документа.", "s"),
    ("title", Risk.PROVENANCE, "Заголовок", "", "s"),
    ("subject", Risk.PROVENANCE, "Тема", "", "s"),
    ("keywords", Risk.PROVENANCE, "Ключевые слова", "", "s"),
    ("comments", Risk.PROVENANCE, "Комментарии", "", "s"),
    ("category", Risk.PROVENANCE, "Категория", "", "s"),
    ("content_status", Risk.PROVENANCE, "Состояние документа",
     "Например 'Черновик' -- выдаёт стадию работы.", "s"),
    ("content_type", Risk.PROVENANCE, "Тип содержимого", "", "s"),
    ("create_time", Risk.PROVENANCE, "Создан",
     "Момент создания документа (UTC).", "d"),
    ("last_saved_time", Risk.PROVENANCE, "Последнее сохранение",
     "Момент последнего сохранения (UTC).", "d"),
    ("last_printed", Risk.PROVENANCE, "Последняя печать",
     "Сам факт печати иногда выдаёт, что документ уходил наружу.", "d"),
    ("total_edit_time", Risk.PROVENANCE, "Общее время редактирования",
     "Суммарное время работы над документом. Выдаёт, писали текст "
     "часами или вставили готовый за минуту.", "t"),
    ("creating_application", Risk.PROVENANCE, "Приложение-создатель",
     "Точная строка приложения и его версии.", "s"),
    ("revision_number", Risk.PROVENANCE, "Номер ревизии",
     "Сколько раз документ сохраняли.", "s"),
    ("doc_version", Risk.PROVENANCE, "Версия документа", "", "s"),
    ("version", Risk.PROVENANCE, "Версия формата", "", "s"),
    ("num_pages", Risk.PROVENANCE, "Страниц", "", "s"),
    ("num_words", Risk.PROVENANCE, "Слов", "", "s"),
    ("num_chars", Risk.PROVENANCE, "Символов", "", "s"),
    ("chars_with_spaces", Risk.PROVENANCE, "Символов с пробелами", "", "s"),
    ("lines", Risk.PROVENANCE, "Строк", "", "s"),
    ("paragraphs", Risk.PROVENANCE, "Абзацев", "", "s"),
    ("slides", Risk.PROVENANCE, "Слайдов", "", "s"),
    ("notes", Risk.PROVENANCE, "Слайдов с заметками",
     "Заметки докладчика -- отдельный источник утечки текста.", "s"),
    ("hidden_slides", Risk.PROVENANCE, "Скрытых слайдов",
     "Скрытые слайды остаются в файле целиком.", "s"),
    ("titles_of_parts", Risk.PROVENANCE, "Названия частей",
     "Имена листов/слайдов/разделов -- часто выдают внутреннюю структуру.", "s"),
    ("template", Risk.ENVIRONMENT, "Шаблон",
     "Обычно путь к .dot(x): либо локальный, либо \\\\сервер\\шара.", "s"),
    ("link_base", Risk.ENVIRONMENT, "База ссылок",
     "Базовый путь для относительных гиперссылок -- почти всегда локальный.", "s"),
    ("presentation_target", Risk.ENVIRONMENT, "Целевой формат показа",
     "Иногда содержит имя принтера или модель экрана.", "s"),
    ("security", Risk.PROVENANCE, "Флаг защиты",
     "Битовая маска: пароль, рекомендация 'только чтение' и т.п.", "s"),
    ("language", Risk.STRUCTURAL, "Язык",
     "Обычно нужно сохранить: влияет на проверку орфографии.", "s"),
    ("codepage", Risk.STRUCTURAL, "Кодовая страница",
     "Нужна для корректного чтения строк метаданных.", "s"),
    ("dig_sig", Risk.IDENTITY, "Цифровая подпись",
     "Подпись содержит сертификат подписанта (ФИО, организация, почта). "
     "Удалить её без потери подписи невозможно.", "b"),
)

# Потоки, про которые нужно сказать отдельно.
_OLE_MACRO_STREAMS = ("Macros", "_VBA_PROJECT_CUR", "VBA", "_VBA_PROJECT",
                      "VBA_PROJECT", "vbaProject")

_CLSID_APPS = {
    "00020906-0000-0000-C000-000000000046": "Microsoft Word 97-2003",
    "00020900-0000-0000-C000-000000000046": "Microsoft Word 6.0/7.0",
    "00020820-0000-0000-C000-000000000046": "Microsoft Excel 97-2003",
    "00020810-0000-0000-C000-000000000046": "Microsoft Excel 5.0/95",
    "64818D10-4F9B-11CF-86EA-00AA00B929E8": "Microsoft PowerPoint 97-2003",
    "74B78F3A-C8C8-11D1-BE11-00C04FB6FAF1": "Microsoft Project",
    "000C1084-0000-0000-C000-000000000046": "Windows Installer (база MSI)",
    "000C1086-0000-0000-C000-000000000046": "Windows Installer (патч MSP)",
}


def _cp_codec(codepage) -> str:
    """Номер кодовой страницы из SummaryInformation -> имя кодека Python."""
    try:
        cp = int(codepage)
    except (TypeError, ValueError):
        return "cp1252"
    if cp < 0:
        cp += 65536          # номер лежит как знаковый int16
    if cp in (65001, 0):
        return "utf-8"
    if cp == 1200:
        return "utf-16-le"
    name = "cp%d" % cp
    try:
        import codecs
        codecs.lookup(name)
    except (LookupError, ValueError):
        return "cp1252"
    return name


def _dec(value, codec="cp1252"):
    """Атрибуты olefile часто bytes -- декодируем с учётом кодовой страницы."""
    if isinstance(value, bytes):
        try:
            return value.decode(codec, "replace")
        except LookupError:
            return value.decode("cp1252", "replace")
    return value


def _ole_read(ole, name, limit=MAX_READ) -> bytes:
    """Прочитать поток целиком, но не больше limit байт."""
    with ole.openstream(name) as fh:
        return fh.read(limit)


def _filetime_set(value):
    """datetime из olefile -> он же, если время реально задано. Нулевой
    FILETIME (1601-01-01) -- это 'не задано', а не дата: Word пишет его в
    'Последнюю печать' всем, кто ни разу не печатал."""
    if isinstance(value, datetime.datetime) and value > datetime.datetime(1601, 1, 1):
        return value
    return None


# FMTID_UserDefinedProperties {D5CDD505-2E9C-101B-9397-08002B2CF9AE} в порядке
# байт на диске (первые три поля little-endian).
_USERDEF_FMTID = bytes.fromhex("05d5cdd59c2e1b10939708002b2cf9ae")
MAX_USERDEF = 200                  # пользовательских свойств разбираем не больше
USERDEF_READ = 8 * 1024 * 1024     # поток DocumentSummaryInformation: больше не читаем


def _le(blob, off, size):
    """Целое little-endian из blob. Выход за границу потока -- ValueError, а не
    молчаливый ноль: битый файл должен попасть в report.errors."""
    if off < 0 or off + size > len(blob):
        raise ValueError("смещение %d вне потока (%d байт)" % (off, len(blob)))
    return int.from_bytes(blob[off:off + size], "little")


def _sint(value, bits):
    return value - (1 << bits) if value >= 1 << (bits - 1) else value


def _userdef_value(blob, off, codec, wide):
    """TypedPropertyValue (MS-OLEPS) -> строка. Неизвестный тип -- пометка."""
    vt = _le(blob, off, 2)
    pos = off + 4
    if vt in (0x1E, 0x08):                    # LPSTR / BSTR: длина в байтах
        n = _le(blob, pos, 4)
        return blob[pos + 4:pos + 4 + n].decode(
            "utf-16-le" if wide else codec, "replace").strip("\x00")
    if vt == 0x1F:                            # LPWSTR: длина в символах
        n = _le(blob, pos, 4)
        return blob[pos + 4:pos + 4 + 2 * n].decode("utf-16-le", "replace").strip("\x00")
    if vt in (0x02, 0x12):                    # I2 / UI2
        v = _le(blob, pos, 2)
        return str(_sint(v, 16) if vt == 0x02 else v)
    if vt in (0x03, 0x16, 0x13, 0x17):        # I4, INT, UI4, UINT
        v = _le(blob, pos, 4)
        return str(_sint(v, 32) if vt in (0x03, 0x16) else v)
    if vt in (0x14, 0x15):                    # I8 / UI8
        v = _le(blob, pos, 8)
        return str(_sint(v, 64) if vt == 0x14 else v)
    if vt == 0x0B:                            # BOOL
        return "да" if _le(blob, pos, 2) else "нет"
    if vt == 0x05:                            # R8
        return repr(struct.unpack("<d", blob[pos:pos + 8])[0])
    if vt == 0x40:                            # FILETIME
        ticks = _le(blob, pos, 8)
        return _dt(datetime.datetime(1601, 1, 1) + datetime.timedelta(microseconds=ticks // 10))
    return "(тип VT 0x%02X, не разбирается)" % vt


def _userdef_props(blob, default_codec, report):
    """Пользовательские свойства из потока DocumentSummaryInformation (секция
    FMTID_UserDefinedProperties, MS-OLEPS) -> [(имя, значение)].

    Свой разбор, а не olefile.get_userdefined_properties(): тот читает имена
    только как UTF-8, поэтому русские имена в cp1251 теряет молча (пустой
    список без единой ошибки), а секцию в кодовой странице 1200 (UTF-16: длина
    в символах, выравнивание на 4) разбирает в мусор. У секции своя кодовая
    страница -- свойство PID 1. Что успели разобрать до обрыва -- остаётся.
    """
    out = []
    try:
        for i in range(min(_le(blob, 24, 4), 16)):
            entry = 28 + 20 * i
            if blob[entry:entry + 16] != _USERDEF_FMTID:
                continue
            sec = _le(blob, entry + 16, 4)
            props = {}
            for j in range(min(_le(blob, sec + 4, 4), 4 * MAX_USERDEF)):
                props[_le(blob, sec + 8 + 8 * j, 4)] = sec + _le(blob, sec + 12 + 8 * j, 4)
            codec, wide = default_codec, False
            if 1 in props and _le(blob, props[1], 2) == 0x02:        # PID 1: VT_I2
                codec = _cp_codec(_le(blob, props[1] + 4, 2))
                wide = codec == "utf-16-le"
            if 0 not in props:                                       # PID 0: словарь
                continue
            names, pos = {}, props[0]
            count = _le(blob, pos, 4)
            if count > MAX_USERDEF:
                report.err("пользовательских свойств %d, разбираем первые %d"
                           % (count, MAX_USERDEF))
            pos += 4
            try:
                for _ in range(min(count, MAX_USERDEF)):
                    pid, length = _le(blob, pos, 4), _le(blob, pos + 4, 4)
                    pos += 8
                    nbytes = length * 2 if wide else length
                    names[pid] = blob[pos:pos + nbytes].decode(codec, "replace").strip("\x00")
                    pad = -nbytes % 4 if wide else 0      # UTF-16: имя выровнено на 4
                    if pad and _le(blob, pos + nbytes + pad, 4) not in props \
                            and _le(blob, pos + nbytes, 4) in props:
                        pad = 0     # писатель не выравнивал: следующий PID сразу за именем
                    pos += nbytes + pad
            except ValueError as exc:       # словарь оборван: берём, что есть
                report.err("словарь пользовательских свойств оборван: %s" % (exc,))
            for pid, name in names.items():
                if pid not in props:
                    continue
                try:
                    out.append((name, _userdef_value(blob, props[pid], codec, wide)))
                except Exception:
                    out.append((name, "(значение не разобралось)"))
    except Exception as exc:
        report.err("пользовательские свойства оборваны на битой структуре: %s" % (exc,))
    return out


MAX_VECTOR = 100_000               # элементов в векторном свойстве: больше -- мусор


def _open_ole(olefile, path):
    """OleFileIO, которому нельзя повесить процесс битым property set.

    olefile разбирает векторное свойство (VT_VECTOR) циклом по счётчику из
    файла, а элементы неизвестного ему типа не читают ни байта и не бросают
    исключений. Счётчик 0xFFFFFFFF -- это четыре миллиарда пустых итераций и
    список на десятки гигабайт: инспектор зависает, бот вместе с ним. Хватает
    одного испорченного байта (сбой диска, обрезанная загрузка), злого умысла
    не нужно. Поэтому счётчик режем до MAX_VECTOR: больше -- значение мусор,
    свойство пропускаем, остальные читаются как обычно.
    """
    class _SafeOle(olefile.OleFileIO):
        def _parse_property(self, s, offset, property_id, property_type, *rest):
            if property_type & 0x1000:                    # VT_VECTOR
                if int.from_bytes(s[offset:offset + 4], "little") > MAX_VECTOR:
                    return None
            return super()._parse_property(s, offset, property_id, property_type, *rest)

    return _SafeOle(path)


def inspect_ole(path, fmt="doc") -> Report:
    """Инспекция OLE2-контейнера: .doc, .xls, .ppt и прочее.

    Главная ценность здесь не свойства документа (их видно и в Word), а
    структура контейнера: потоки таблиц правок, остаточный текст после
    быстрого сохранения, встроенные объекты и макросы.
    """
    report = Report(path=str(path), fmt=fmt, size=_size(path))

    try:
        import olefile
    except ImportError:
        report.err("olefile не установлен -- разбор OLE2 невозможен")
        return report

    try:
        if not olefile.isOleFile(path):
            report.err("файл не является OLE2-контейнером (нет сигнатуры D0CF11E0)")
            return report
    except Exception as exc:
        report.err("не удалось проверить сигнатуру OLE2: %r" % (exc,))
        return report

    ole = None
    try:
        ole = _open_ole(olefile, path)   # olefile открывает строго на чтение
    except Exception as exc:
        report.err("OLE2-контейнер не открылся: %r" % (exc,))
        return report

    try:
        codec = _section(report, "свойства", _ole_meta, ole, report, default="cp1252")
        flat = _section(report, "директория", _ole_parts, ole, report, default={})
        _section(report, "корневая запись", _ole_root, ole, report)
        _section(report, "потоки", _ole_streams, ole, report, flat, codec)
        # Ветку выбираем по потокам ВЕРХНЕГО уровня, а не по fmt: sniff ищет
        # имена по всему дереву, и .xls со встроенным Word-объектом он считает
        # .doc -- тогда книга осталась бы без разбора, а Word-находки были бы
        # приписаны чужому файлу.
        have = {
            "doc": bool(_top(flat, "WordDocument")),
            "xls": bool(_top(flat, "Workbook") or _top(flat, "Book")),
            "ppt": bool(_top(flat, "PowerPoint Document") or _top(flat, "Current User")),
        }
        if have["doc"]:
            _section(report, "WordDocument", _ole_word, ole, report, flat)
        if have["xls"]:
            _section(report, "Workbook", _ole_xls, ole, report, flat)
        if have["ppt"]:
            _section(report, "PowerPoint", _ole_ppt, ole, report, flat, codec)
        real = [k for k, ok in have.items() if ok]
        if real and fmt in have and fmt not in real:
            report.signal("inconsistent",
                          "Формат определён как %s, но на верхнем уровне контейнера "
                          "лежат потоки %s: вероятно, во встроенном объекте есть потоки "
                          "другого приложения. Разбор идёт по верхнему уровню."
                          % (fmt, ", ".join(real)), "medium")
    except Exception as exc:                       # последний рубеж
        report.err("разбор OLE2 прерван: %r" % (exc,))
    finally:
        try:
            ole.close()
        except Exception:
            pass
    return report


def _section(report, title, fn, *args, default=None):
    """Одна секция разбора в своём try/except: сбой в ней не отнимает остальные."""
    try:
        return fn(*args)
    except Exception as exc:
        report.err("%s: разбор прерван: %r" % (title, exc))
        return default


def _top(flat, name):
    """Запись потока/хранилища name прямо в корне контейнера. flat индексирован
    последним элементом пути, а у встроенного объекта те же имена
    (WordDocument, CompObj, SummaryInformation) лежат глубже: принять
    их за родные значит приписать файлу чужой документ и читать по пути,
    которого в корне нет."""
    return next((e for e in flat.get(name, []) if e[0] == name), None)


def _ole_meta(ole, report) -> str:
    """SummaryInformation + DocumentSummaryInformation. Возвращает кодек."""
    codec = "cp1252"
    try:
        meta = ole.get_metadata()
    except Exception as exc:
        report.err("не читается SummaryInformation: %r" % (exc,))
        return codec

    codec = _cp_codec(getattr(meta, "codepage", None))
    # У каждого из двух потоков свойств своя кодовая страница. Строки
    # DocumentSummaryInformation (организация, руководитель...) читаем в
    # ЕЁ странице, а не в странице SummaryInformation: иначе кириллица там,
    # где страницы разошлись, превращается в 'ÎÎÎ «Ðîìàøêà»'.
    doc_cp = getattr(meta, "codepage_doc", None)
    codec_doc = _cp_codec(doc_cp) if doc_cp is not None else codec
    docsum_attrs = set(getattr(type(meta), "DOCSUM_ATTRIBS", ()))
    app = ""
    for attr, risk, label, note, kind in _OLE_PROPS:
        try:
            raw = getattr(meta, attr, None)
            if raw is None:
                continue
            in_doc = attr in docsum_attrs
            cp = codec_doc if in_doc else codec
            if kind == "d":
                null = isinstance(raw, datetime.datetime) and _filetime_set(raw) is None
                value = "" if null else _dt(raw)    # нулевой FILETIME -> пусто
            elif kind == "t":
                value = _dur_secs(raw)
            elif kind == "b":
                value = "присутствует, %d байт" % len(raw) if raw else ""
            else:
                value = _dec(raw, cp)
            if isinstance(value, (list, tuple)):
                value = "; ".join(str(_dec(v, cp)) for v in value)
            removable = attr != "dig_sig"
            report.add(risk, "DocumentSummaryInformation" if in_doc
                       else "SummaryInformation", label, value, note, removable)
            if attr == "creating_application":
                app = clip(value)
        except Exception as exc:
            report.err("свойство %s не разобралось: %r" % (attr, exc))

    # Пользовательские свойства документа (вкладка "Прочие" в свойствах Office).
    try:
        if ole.exists("\x05DocumentSummaryInformation"):
            blob = _ole_read(ole, "\x05DocumentSummaryInformation", USERDEF_READ)
            for name, value in _userdef_props(blob, codec_doc, report):
                report.add(Risk.IDENTITY, "DocumentSummaryInformation",
                           "Польз. свойство: %s" % clip(name, 80), value,
                           "Произвольное поле, заполненное вручную или шаблоном "
                           "организации: тут встречаются ФИО, email в метках "
                           "классификации MSIP, номера договоров, внутренние "
                           "коды подразделений.")
    except Exception as exc:
        report.err("пользовательские свойства не прочитались: %r" % (exc,))

    if app:
        marker = _looks_generated(app)
        report.signal("producer", "Приложение-создатель: %s" % app, "high")
        if marker:
            report.signal("ai", "Приложение-создатель похоже на библиотеку/скрипт "
                                "(маркер '%s'): %s" % (marker, app), "medium")
    _ole_consistency(meta, report)
    return codec


def _ole_consistency(meta, report):
    """Противоречия между датами и счётчиками."""
    try:
        created = _filetime_set(getattr(meta, "create_time", None))
        saved = _filetime_set(getattr(meta, "last_saved_time", None))
        if isinstance(created, datetime.datetime) and isinstance(saved, datetime.datetime):
            if saved < created:
                report.signal("inconsistent",
                              "Дата сохранения (%s) раньше даты создания (%s): "
                              "метаданные правили вручную или переносили "
                              "системное время." % (_dt(saved), _dt(created)),
                              "high")
        edit = getattr(meta, "total_edit_time", None)
        author = getattr(meta, "author", None)
        saved_by = getattr(meta, "last_saved_by", None)
        if not author and not saved_by:
            report.signal("scrubbed",
                          "Ни 'Автор', ни 'Кто сохранял последним' не заполнены: "
                          "файл либо уже чистили, либо делала программа.", "medium")
        if isinstance(edit, int) and edit > 0 and isinstance(created, datetime.datetime) \
                and isinstance(saved, datetime.datetime):
            window = (saved - created).total_seconds()
            if window >= 0 and edit > window + 3600:
                report.signal("inconsistent",
                              "Время редактирования (%d с) больше промежутка между "
                              "созданием и сохранением (%d с): документ старше, чем "
                              "утверждают его даты." % (edit, int(window)), "medium")
    except Exception as exc:
        report.err("сверка дат OLE2 не удалась: %r" % (exc,))


def _ole_parts(ole, report) -> dict:
    """Все потоки и хранилища контейнера -> report.parts. Возвращает карту."""
    flat = {}
    try:
        entries = ole.listdir(streams=True, storages=True)
    except Exception as exc:
        report.err("не читается директория OLE2: %r" % (exc,))
        return flat

    if len(entries) > MAX_PARTS:
        report.err("в контейнере %d записей, разбираем первые %d"
                   % (len(entries), MAX_PARTS))
        entries = entries[:MAX_PARTS]

    for entry in entries:
        name = "/".join(entry)
        size = 0
        kind = "storage"
        try:
            if ole.get_type(entry) == 2:      # STGTY_STREAM
                kind = "stream"
                size = ole.get_size(entry)
        except Exception:
            pass
        report.parts.append({"name": name, "size": size, "kind": kind})
        flat.setdefault(entry[-1], []).append((name, size, kind))
    return flat


def _ole_root(ole, report):
    """CLSID и времена корневого хранилища."""
    try:
        root = ole.root
        clsid = (getattr(root, "clsid", "") or "").upper()
        if clsid and clsid.strip("0-"):
            app = _CLSID_APPS.get(clsid, "неизвестное приложение")
            report.add(Risk.PROVENANCE, "Root Entry", "CLSID корневого хранилища",
                       "%s (%s)" % (clsid, app),
                       "Класс OLE прямо называет приложение, создавшее контейнер.")
            if clsid in _CLSID_APPS:
                report.signal("producer", "CLSID контейнера: %s" % app, "high")
    except Exception:
        pass
    # ole.getctime("Root Entry") бросает OSError("file not found"): корень не
    # лежит в дереве имён. Времена корня берутся у самой записи root.
    for label, getter, note in (
        ("Время создания контейнера", "getctime",
         "Время самого OLE-хранилища, отдельное от даты в свойствах документа: "
         "они часто расходятся и выдают правку метаданных."),
        ("Время изменения контейнера", "getmtime", ""),
    ):
        try:
            value = getattr(ole.root, getter)()
            report.add_nonempty(Risk.PROVENANCE, "Root Entry", label, _dt(value), note)
        except Exception as exc:
            report.err("%s не прочиталось: %r" % (label.lower(), exc))


def _ole_streams(ole, report, flat, codec):
    """Именованные потоки, про которые нужно сказать отдельно."""
    # --- макросы ---
    found_macro = [n for n in _OLE_MACRO_STREAMS if n in flat]
    for name in found_macro:
        for full, size, _kind in flat[name]:
            report.add(Risk.PROVENANCE, full, "Поток макросов",
                       "%s, %d байт" % (name, size),
                       "Проект VBA. Внутри лежат исходники модулей, имя проекта "
                       "и путь к нему на машине разработчика, а нередко и "
                       "закомментированный старый код.", False)
    if found_macro:
        report.signal("hazard",
                      "В файле есть проект VBA (%s): активное содержимое, "
                      "плюс отдельный от документа источник метаданных."
                      % ", ".join(sorted(set(found_macro))), "high")

    # --- свойства ---
    for name, label in (("\x05SummaryInformation", "Поток SummaryInformation"),
                        ("\x05DocumentSummaryInformation",
                         "Поток DocumentSummaryInformation")):
        if _top(flat, name):
            size = _top(flat, name)[1]
            report.add(Risk.PROVENANCE, name.replace("\x05", "\\x05"), label,
                       "%d байт" % size,
                       "Стандартный контейнер свойств документа.")

    # --- цифровая подпись отдельным потоком ---
    for name in ("\x05DigitalSignature", "\x05MsiDigitalSignatureEx",
                 "_signatures"):
        if name in flat:
            size = flat[name][0][1]
            report.add(Risk.IDENTITY, name.replace("\x05", "\\x05"),
                       "Цифровая подпись", "%d байт" % size,
                       "Подпись содержит сертификат подписанта: ФИО или название "
                       "организации, адрес почты, срок действия, отпечаток. "
                       "Убрать её можно только вместе с самой подписью -- "
                       "файл перестанет быть подписанным.", False)

    # --- CompObj: имя приложения-создателя ---
    if _top(flat, "\x01CompObj"):
        try:
            blob = _ole_read(ole, "\x01CompObj", 64 * 1024)
            for label, value in _parse_compobj(blob, codec):
                report.add_nonempty(Risk.PROVENANCE, "\\x01CompObj", label, value,
                                    "CompObj хранит, каким приложением объект был "
                                    "создан -- даже если свойства документа стёрли.")
        except Exception as exc:
            report.err("CompObj не разобрался: %r" % (exc,))

    # --- вложенные OLE-объекты ---
    pool = [n for n in report.parts if n["name"].startswith("ObjectPool")]
    if pool:
        children = sorted({p["name"].split("/")[1] for p in pool
                           if p["name"].count("/") >= 1 and "/" in p["name"]})
    else:
        children = []
    if children:
        report.add(Risk.IDENTITY, "ObjectPool", "Вложенные OLE-объекты",
                   "%d объект(ов): %s" % (len(children),
                                          ", ".join(children[:MAX_LIST])),
                   "Каждый встроенный объект (лист Excel, диаграмма, вставленный "
                   "документ) -- это отдельный OLE-контейнер СО СВОИМИ "
                   "метаданными: своим автором, своими датами и своими "
                   "остаточными данными. Чистка внешнего файла их не трогает.",
                   False)
        report.signal("hazard",
                      "Внутри ObjectPool лежат вложенные OLE-объекты (%d шт.); "
                      "их метаданные надо инспектировать отдельно." % len(children),
                      "high")


def _parse_compobj(blob, codec):
    """CompObj: после 28-байтного заголовка идут строки с префиксом длины."""
    out = []
    labels = ("Имя типа объекта (UserType)", "Формат буфера обмена", "ProgID")
    pos = 28
    for label in labels:
        if pos + 4 > len(blob):
            break
        length = int.from_bytes(blob[pos:pos + 4], "little")
        pos += 4
        if length in (0xFFFFFFFF, 0xFFFFFFFE):     # формат задан числом, не строкой
            pos += 4
            continue
        if length == 0 or length > 4096 or pos + length > len(blob):
            break
        raw = blob[pos:pos + length].split(b"\x00", 1)[0]
        pos += length
        out.append((label, _dec(raw, codec)))
    return out


def _ole_word(ole, report, flat):
    """Ядро модуля: остаточный текст и таблицы правок в .doc."""
    if not _top(flat, "WordDocument"):
        return

    tables = [n for n in ("0Table", "1Table") if _top(flat, n)]
    fib = b""
    try:
        fib = _ole_read(ole, "WordDocument", 64)
    except Exception as exc:
        report.err("FIB потока WordDocument не читается: %r" % (exc,))

    flags = quick_saves = which_table = None
    if len(fib) >= 12:
        try:
            n_fib = int.from_bytes(fib[2:4], "little")
            lid = int.from_bytes(fib[6:8], "little")
            flags = int.from_bytes(fib[10:12], "little")
            quick_saves = (flags >> 4) & 0x0F
            which_table = "1Table" if flags & 0x0200 else "0Table"
            report.add(Risk.PROVENANCE, "WordDocument", "Версия формата (nFib)",
                       "0x%04X" % n_fib,
                       "Номер версии структуры FIB -- по нему видно, какая именно "
                       "версия Word писала файл последней.")
            report.add(Risk.ENVIRONMENT, "WordDocument", "Язык приложения (lid)",
                       "0x%04X" % lid,
                       "Идентификатор локали Word на машине, где сохраняли файл: "
                       "косвенно выдаёт страну и языковую сборку Office.")
            if flags & 0x0100:
                report.add(Risk.STRUCTURAL, "WordDocument", "Документ зашифрован",
                           "флаг fEncrypted", "Содержимое зашифровано паролем; "
                           "разобрать его без пароля нельзя.", False)
        except Exception as exc:
            report.err("флаги FIB не разобрались: %r" % (exc,))

    # Главная находка модуля.
    note = (
        "Формат .doc физически хранит больше, чем показывает. В потоке таблицы "
        "(%s) лежит таблица авторов правок SttbfRMark -- список ВСЕХ, кто когда-либо "
        "редактировал документ, даже если сами правки давно приняты. При сохранении "
        "в режиме быстрого сохранения Word не переписывает файл заново, а дописывает "
        "изменения в конец: УДАЛЁННЫЙ ТЕКСТ предыдущих версий остаётся в файле "
        "физически и извлекается любым hex-редактором. Сверх того, неиспользованные "
        "секторы OLE-контейнера ранее могли принадлежать ДРУГИМ файлам с того же "
        "диска, и их фрагменты уезжают вместе с документом. Вычистить это "
        "редактированием свойств невозможно."
    ) % (", ".join(tables) if tables else "0Table/1Table отсутствует")

    report.add(Risk.IDENTITY, "WordDocument",
               "Остаточный текст и таблица авторов правок",
               "WordDocument%s" % (" + " + " + ".join(tables) if tables else ""),
               note, False)

    if which_table and tables and which_table in tables:
        stale = [t for t in tables if t != which_table]
        if stale:
            report.add(Risk.IDENTITY, stale[0], "Неактивный поток таблицы",
                       "%s (активен %s)" % (stale[0], which_table),
                       "Word держит два потока таблиц и переключается между ними "
                       "при сохранении. Неактивный поток -- это СТАРАЯ версия "
                       "служебных структур предыдущего сохранения, включая "
                       "предыдущий список авторов правок. Никакой программой "
                       "'свойств документа' он не виден.", False)

    if quick_saves:
        report.add(Risk.PROVENANCE, "WordDocument", "Счётчик быстрых сохранений",
                   str(quick_saves),
                   "Сколько раз файл сохранялся в режиме быстрого сохранения. "
                   "Всё, что больше нуля, означает дописанные в конец файла "
                   "куски прошлых версий.")
    if flags is not None and flags & 0x0004:
        report.add(Risk.IDENTITY, "WordDocument", "Флаг fComplex (быстрое сохранение)",
                   "установлен",
                   "Документ сохранён в сложном (быстром) режиме: текст лежит "
                   "кусками вперемешку с удалёнными фрагментами.", False)

    report.signal(
        "hazard",
        "Файл .doc (OLE2) нельзя чистить на месте. Правка свойств документа "
        "не убирает ни таблицу авторов правок, ни удалённый текст предыдущих "
        "версий, ни чужие фрагменты в свободных секторах контейнера. "
        "Единственный надёжный путь -- пересохранить содержимое в современный "
        "формат (.docx) или отдать PDF, и чистить уже его.",
        "high",
    )


def _ole_xls(ole, report, flat):
    """.xls: рабочая книга и записи внешних ссылок."""
    book = next((n for n in ("Workbook", "Book") if _top(flat, n)), None)
    if not book:
        return
    size = _top(flat, book)[1]
    report.add(Risk.PROVENANCE, book, "Поток рабочей книги",
               "%s, %d байт" % (book, size),
               "Тело книги в формате BIFF: формулы, скрытые листы, имена "
               "диапазонов и кеш внешних связей лежат здесь, а не в свойствах.")

    try:
        blob = _ole_read(ole, book, MAX_READ)
    except Exception as exc:
        report.err("поток %s не читается: %r" % (book, exc))
        return

    counts = {}
    writeaccess = ""
    sheets = []
    pos, guard = 0, 0
    try:
        while pos + 4 <= len(blob) and guard < 200000:
            guard += 1
            rec = int.from_bytes(blob[pos:pos + 2], "little")
            length = int.from_bytes(blob[pos + 2:pos + 4], "little")
            body = blob[pos + 4:pos + 4 + length]
            pos += 4 + length
            if rec in (0x01AE, 0x0017, 0x0023, 0x002F, 0x01B8, 0x0085, 0x005C):
                counts[rec] = counts.get(rec, 0) + 1
            if rec == 0x005C and not writeaccess:          # WRITEACCESS
                writeaccess = _biff_string(body)
            elif rec == 0x0085 and len(sheets) < MAX_LIST:  # BOUNDSHEET
                name = _biff_string(body[6:], prefix_len=1)
                if name:
                    sheets.append(name)
    except Exception as exc:
        report.err("обход записей BIFF прерван: %r" % (exc,))

    if writeaccess:
        report.add(Risk.IDENTITY, "%s:WRITEACCESS" % book, "Имя пользователя Excel",
                   writeaccess,
                   "Запись WRITEACCESS хранит имя пользователя из настроек Excel "
                   "отдельно от свойств документа: её не видно в диалоге свойств "
                   "и она переживает их очистку.")
    if sheets:
        report.add(Risk.PROVENANCE, "%s:BOUNDSHEET" % book, "Листы книги",
                   "; ".join(sheets),
                   "Имена листов, включая скрытые и очень скрытые.")
    externals = counts.get(0x01AE, 0) + counts.get(0x0017, 0) + counts.get(0x0023, 0)
    if externals:
        report.add(Risk.ENVIRONMENT, "%s:SUPBOOK/EXTERNSHEET" % book,
                   "Записи внешних связей",
                   "SUPBOOK: %d, EXTERNSHEET: %d, EXTERNNAME: %d"
                   % (counts.get(0x01AE, 0), counts.get(0x0017, 0),
                      counts.get(0x0023, 0)),
                   "Связи с другими книгами. Путь к исходному файлу (часто "
                   "\\\\сервер\\отдел\\бюджет.xls или C:\\Users\\...) хранится "
                   "внутри вместе с кешем значений.")
    if counts.get(0x01B8):
        report.add(Risk.ENVIRONMENT, "%s:HLINK" % book, "Гиперссылки",
                   "%d шт." % counts[0x01B8],
                   "Гиперссылки нередко указывают на локальные и сетевые пути.")
    if counts.get(0x002F):
        report.add(Risk.STRUCTURAL, "%s:FILEPASS" % book, "Книга зашифрована",
                   "запись FILEPASS", "Содержимое защищено паролем.", False)


def _biff_string(body, prefix_len=2) -> str:
    """XLUnicodeString из BIFF8: длина, байт флагов, затем сами символы."""
    try:
        if len(body) < prefix_len + 1:
            return ""
        cch = int.from_bytes(body[:prefix_len], "little")
        flags = body[prefix_len]
        data = body[prefix_len + 1:]
        if flags & 0x01:
            return data[:cch * 2].decode("utf-16-le", "replace").strip("\x00 ")
        return data[:cch].decode("cp1252", "replace").strip("\x00 ")
    except Exception:
        return ""


def _ole_ppt(ole, report, flat, codec="cp1252"):
    """.ppt: имя последнего редактора лежит в отдельном потоке Current User."""
    if _top(flat, "PowerPoint Document"):
        size = _top(flat, "PowerPoint Document")[1]
        report.add(Risk.PROVENANCE, "PowerPoint Document", "Поток презентации",
                   "%d байт" % size,
                   "Тело презентации. Здесь же остаются заметки докладчика, "
                   "скрытые слайды и объекты, вынесенные за границу слайда.")
    if not _top(flat, "Current User"):
        return
    try:
        blob = _ole_read(ole, "Current User", 64 * 1024)
        # CurrentUserAtom (MS-PPT 2.3.3): RecordHeader(8) + size(4) + headerToken(4)
        # + offsetToCurrentEdit(4) + lenUserName(2) + docFileVersion(2) + major(1)
        # + minor(1) + unused(2) = 28 байт заголовка, дальше сразу ansiUserName.
        if len(blob) >= 28:
            len_name = int.from_bytes(blob[20:22], "little")
            # За ansiUserName идут relFileVersion (4 байта) и unicodeUserName
            # (UTF-16): он от кодовой страницы не зависит, поэтому предпочтителен.
            # ANSI-имя -- в системной странице автора (русский Office: cp1251),
            # а не в cp1252: иначе 'Петров' выходит как 'Ïåòðîâ'.
            wide = blob[32 + len_name:32 + 3 * len_name]
            if len_name and len(wide) == 2 * len_name:
                name = wide.decode("utf-16-le", "replace")
            else:
                name = blob[28:28 + len_name].decode(codec, "replace")
            name = name.strip("\x00")
            report.add_nonempty(Risk.IDENTITY, "Current User",
                                "Имя последнего редактора (CurrentUserAtom)", name,
                                "PowerPoint пишет имя пользователя в отдельный "
                                "поток контейнера. Оно не показывается в свойствах "
                                "документа и остаётся на месте после их очистки.")
    except Exception as exc:
        report.err("поток Current User не разобрался: %r" % (exc,))


# ============================================================================
#  Б. OpenDocument -- .odt / .ods / .odp
# ============================================================================

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
    "script": "urn:oasis:names:tc:opendocument:xmlns:script:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}

# meta.xml: тег -> (риск, имя, пояснение)
_ODF_META = {
    "{%s}initial-creator" % _NS["meta"]: (
        Risk.IDENTITY, "Первоначальный автор",
        "Кто создал документ. Переживает все последующие правки."),
    "{%s}creator" % _NS["dc"]: (
        Risk.IDENTITY, "Кто сохранял последним",
        "В ODF dc:creator -- это НЕ автор, а тот, кто последним сохранял файл."),
    "{%s}printed-by" % _NS["meta"]: (
        Risk.IDENTITY, "Кто печатал",
        "Имя пользователя, отправившего документ на печать."),
    "{%s}generator" % _NS["meta"]: (
        Risk.PROVENANCE, "Генератор",
        "Точная сборка приложения вплоть до номера ревизии -- сильный отпечаток: "
        "по нему опознаётся конкретная установка и даже дистрибутив."),
    "{%s}creation-date" % _NS["meta"]: (
        Risk.PROVENANCE, "Дата создания", "Момент создания документа."),
    "{%s}date" % _NS["dc"]: (
        Risk.PROVENANCE, "Дата изменения", "Момент последнего сохранения."),
    "{%s}print-date" % _NS["meta"]: (
        Risk.PROVENANCE, "Дата печати", "Когда документ печатали в последний раз."),
    "{%s}editing-cycles" % _NS["meta"]: (
        Risk.PROVENANCE, "Циклов редактирования",
        "Сколько раз документ открывали на правку и сохраняли."),
    "{%s}keyword" % _NS["meta"]: (
        Risk.PROVENANCE, "Ключевое слово", ""),
    "{%s}description" % _NS["dc"]: (
        Risk.PROVENANCE, "Описание", ""),
    "{%s}subject" % _NS["dc"]: (
        Risk.PROVENANCE, "Тема", ""),
    "{%s}title" % _NS["dc"]: (
        Risk.PROVENANCE, "Заголовок", ""),
    "{%s}language" % _NS["dc"]: (
        Risk.STRUCTURAL, "Язык",
        "Нужен для проверки орфографии и доступности -- обычно сохраняем."),
    "{%s}template" % _NS["meta"]: (
        Risk.ENVIRONMENT, "Шаблон", "Ссылка на шаблон, почти всегда локальный путь."),
}


def _xml_parser():
    """Парсер без внешних сущностей и сетевых обращений: XXE нам не нужен."""
    from lxml import etree
    return etree.XMLParser(resolve_entities=False, no_network=True,
                           huge_tree=False, recover=True)


def _zip_safe_read(zf, info, report) -> bytes:
    """Прочитать запись ZIP с проверкой на бомбу."""
    if info.file_size > MAX_READ:
        report.err("часть %s пропущена: %d байт, больше лимита"
                   % (info.filename, info.file_size))
        return b""
    ratio = info.file_size / max(info.compress_size, 1)
    if info.compress_size > 0 and ratio > MAX_RATIO and info.file_size > 1 << 20:
        report.err("часть %s пропущена: коэффициент сжатия %.0f похож на бомбу"
                   % (info.filename, ratio))
        return b""
    try:
        return zf.read(info)
    except Exception as exc:
        report.err("часть %s не читается: %r" % (info.filename, exc))
        return b""


def _odf_xml(zf, by_name, name, report):
    """Прочитать и разобрать часть контейнера. None, если не вышло."""
    if name not in by_name:
        return None
    blob = _zip_safe_read(zf, by_name[name], report)
    if not blob:
        return None
    try:
        from lxml import etree
        root = etree.fromstring(blob, _xml_parser())
    except Exception as exc:
        report.err("%s не разобрался: %r" % (name, exc))
        return None
    if root is None:            # recover=True на полном мусоре даёт None
        report.err("%s не содержит разбираемого XML" % name)
    return root


def inspect_odf(path, fmt="odt") -> Report:
    """Инспекция OpenDocument: .odt, .ods, .odp."""
    report = Report(path=str(path), fmt=fmt, size=_size(path))
    try:
        zf = zipfile.ZipFile(path)          # открывается на чтение
    except Exception as exc:
        report.err("ODF-контейнер не открылся как ZIP: %r" % (exc,))
        return report

    try:
        infos = _odf_parts(zf, report)
        by_name = {i.filename: i for i in infos}
        # Каждая секция отдельно: битая часть не должна уносить с собой остальные.
        for section in (_odf_meta, _odf_settings, _odf_changes, _odf_manifest):
            try:
                section(zf, by_name, report)
            except Exception as exc:
                report.err("%s: %r" % (section.__name__, exc))
        for section, arg in ((_odf_zip_times, infos), (_odf_extras, by_name)):
            try:
                section(arg, report)
            except Exception as exc:
                report.err("%s: %r" % (section.__name__, exc))
    except Exception as exc:
        report.err("разбор ODF прерван: %r" % (exc,))
    finally:
        try:
            zf.close()
        except Exception:
            pass
    return report


def _odf_parts(zf, report):
    """Все записи ZIP -> report.parts, с предохранителем от бомбы."""
    try:
        infos = zf.infolist()
    except Exception as exc:
        report.err("список записей ZIP не читается: %r" % (exc,))
        return []

    if len(infos) > MAX_PARTS:
        report.err("в контейнере %d записей, разбираем первые %d"
                   % (len(infos), MAX_PARTS))
        infos = infos[:MAX_PARTS]

    total = 0
    kept = []
    for info in infos:
        total += info.file_size
        # Набор полей тот же, что у ooxml-инспектора: фазе 2 нужна вся запись
        # ZIP, чтобы пересобрать контейнер байт-в-байт.
        report.parts.append({
            "name": info.filename,
            "date_time": tuple(info.date_time),
            "compress_type": info.compress_type,
            "compress_size": info.compress_size,
            "file_size": info.file_size,
            "crc": info.CRC,
            "flag_bits": info.flag_bits,
            "create_system": info.create_system,
        })
        kept.append(info)
        if total > MAX_UNPACKED:
            report.err("суммарный несжатый размер превысил %d байт -- "
                       "дальше записи не разбираем" % MAX_UNPACKED)
            break
    return kept


def _odf_zip_times(infos, report):
    """Времена записей ZIP. В ODF они настоящие -- в отличие от OOXML."""
    stamps = sorted({i.date_time for i in infos if i.date_time})
    if not stamps:
        return
    zeroed = all(s[:3] == (1980, 1, 1) for s in stamps)
    fmt = lambda s: "%04d-%02d-%02d %02d:%02d:%02d" % s
    value = fmt(stamps[0]) if len(stamps) == 1 else "%s … %s" % (fmt(stamps[0]),
                                                                 fmt(stamps[-1]))
    if zeroed:
        report.add(Risk.PROVENANCE, "ZIP", "Времена записей контейнера", value,
                   "Все отметки времени обнулены до 1980-01-01. Для ODF это "
                   "нетипично: LibreOffice пишет реальное время. Файл, скорее "
                   "всего, пересобран программой или уже чистили.")
        report.signal("scrubbed",
                      "Времена записей ZIP обнулены (1980-01-01), хотя ODF обычно "
                      "хранит настоящие: контейнер пересобран не LibreOffice.",
                      "medium")
    else:
        report.add(Risk.PROVENANCE, "ZIP", "Времена записей контейнера", value,
                   "LibreOffice/OpenOffice записывают в ZIP НАСТОЯЩЕЕ локальное "
                   "время сохранения (Word в OOXML ставит заглушку 1980-01-01). "
                   "Эти отметки выдают точный момент работы над файлом даже "
                   "после полной очистки meta.xml, и их не видно ни в одном "
                   "диалоге свойств документа.")
        report.signal("inconsistent",
                      "В ZIP лежат реальные отметки времени (%s): они останутся "
                      "даже если очистить meta.xml." % value, "high")


def _odf_meta(zf, by_name, report):
    """meta.xml -- основной набор свойств документа."""
    if "meta.xml" not in by_name:
        report.add(Risk.PROVENANCE, "meta.xml", "meta.xml", "",
                   "Части meta.xml в контейнере нет: свойства документа либо "
                   "вырезаны, либо файл собран не офисным приложением.")
        report.signal("scrubbed", "В контейнере нет meta.xml -- свойства "
                                  "документа удалены или никогда не писались.",
                      "medium")
        return
    root = _odf_xml(zf, by_name, "meta.xml", report)
    if root is None:
        return

    values = {}
    generator = ""
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        text = (el.text or "").strip()
        known = _ODF_META.get(tag)
        if known:
            risk, label, note = known
            report.add(risk, "meta.xml", label, text, note)
            values[tag] = text
            if tag.endswith("}generator"):
                generator = text
        elif tag == "{%s}editing-duration" % _NS["meta"]:
            report.add(Risk.PROVENANCE, "meta.xml", "Время редактирования",
                       _dur_iso(text),
                       "Сколько суммарно времени документ был открыт на правку. "
                       "Выдаёт, писали текст часами или вставили готовый за минуту.")
            values[tag] = text
        elif tag == "{%s}user-defined" % _NS["meta"]:
            name = el.get("{%s}name" % _NS["meta"], "")
            report.add(Risk.IDENTITY, "meta.xml",
                       "Польз. свойство: %s" % clip(name, 80), text,
                       "Произвольное поле, заполняемое вручную или корпоративным "
                       "шаблоном: сюда попадают ФИО, номера договоров, коды "
                       "подразделений, адреса почты.")
        elif tag == "{%s}document-statistic" % _NS["meta"]:
            stats = "; ".join("%s=%s" % (k.split("}")[-1], v)
                              for k, v in sorted(el.attrib.items()))
            report.add(Risk.PROVENANCE, "meta.xml", "Статистика документа", stats,
                       "Число страниц, слов, символов, таблиц, картинок. "
                       "Косвенно выдаёт объём вырезанного текста, если сравнить "
                       "со видимым содержимым.")
        elif tag == "{%s}auto-reload" % _NS["meta"] or tag == "{%s}hyperlink-behaviour" % _NS["office"]:
            href = el.get("{%s}href" % _NS["xlink"], "")
            report.add_nonempty(Risk.ENVIRONMENT, "meta.xml", "Автоперезагрузка/ссылка",
                                href, "Адрес, на который документ сам ссылается.")

    if generator:
        report.signal("producer", "Генератор ODF: %s" % generator, "high")
        marker = _looks_generated(generator)
        if marker:
            report.signal("ai", "Генератор похож на библиотеку/скрипт (маркер "
                                "'%s'): %s" % (marker, generator), "medium")
    else:
        report.signal("scrubbed", "meta:generator пуст или отсутствует: обычно "
                                  "признак того, что метаданные уже чистили.",
                      "medium")

    _odf_consistency(values, report)


def _odf_consistency(values, report):
    """Сверка дат и счётчиков meta.xml между собой."""
    created = values.get("{%s}creation-date" % _NS["meta"], "")
    changed = values.get("{%s}date" % _NS["dc"], "")
    if created and changed and changed < created:
        report.signal("inconsistent",
                      "Дата изменения (%s) раньше даты создания (%s): "
                      "метаданные правили вручную." % (changed, created), "high")
    cycles = values.get("{%s}editing-cycles" % _NS["meta"], "")
    duration = values.get("{%s}editing-duration" % _NS["meta"], "")
    try:
        if cycles and int(cycles) <= 1 and duration and duration not in ("PT0S", "P0D"):
            report.signal("inconsistent",
                          "Цикл редактирования всего один (%s), но время правки "
                          "непустое (%s): файл, похоже, пересобран программой из "
                          "чужого исходника." % (cycles, duration), "low")
    except ValueError:
        pass
    if not values.get("{%s}initial-creator" % _NS["meta"]) and \
            not values.get("{%s}creator" % _NS["dc"]):
        report.signal("scrubbed", "Ни meta:initial-creator, ни dc:creator не "
                                  "заполнены -- признак уже выполненной чистки.",
                      "medium")


def _odf_settings(zf, by_name, report):
    """settings.xml: локальные пути, принтер, источники данных."""
    root = _odf_xml(zf, by_name, "settings.xml", report)
    if root is None:
        return

    shown = 0
    for el in root.iter("{%s}config-item" % _NS["config"]):
        name = el.get("{%s}name" % _NS["config"], "")
        text = (el.text or "").strip()
        if not text:
            continue
        low = name.lower()
        if "printer" in low:
            report.add(Risk.ENVIRONMENT, "settings.xml", "Принтер: %s" % name,
                       text if len(text) < 200 else "%d байт настроек" % len(text),
                       "Имя принтера и его настройки. Это прямой указатель на "
                       "конкретное рабочее место: имя очереди печати часто "
                       "содержит имя хоста, отдела или инвентарный номер.")
            shown += 1
            continue
        if _PATH_RE.search(text) and shown < MAX_LIST:
            report.add(Risk.ENVIRONMENT, "settings.xml", "Локальный путь: %s" % name,
                       text,
                       "Абсолютный путь или file:// URI в настройках документа: "
                       "выдаёт имя пользователя, структуру дисков и сетевые шары.")
            shown += 1
        elif low in ("currentdatabasedatasource", "currentdatabasecommand",
                     "bibliographydatasource") and shown < MAX_LIST:
            report.add(Risk.ENVIRONMENT, "settings.xml", "Источник данных: %s" % name,
                       text, "Строка подключения к источнику данных.")
            shown += 1
    if shown:
        report.signal("inconsistent",
                      "В settings.xml осталось %d записей с путями/принтером: "
                      "они не видны в свойствах документа и переживают их очистку."
                      % shown, "medium")


def _odf_changes(zf, by_name, report):
    """content.xml и styles.xml: отслеженные правки и аннотации."""
    authors, dates, annots = set(), set(), []
    scripts = 0
    for name in ("content.xml", "styles.xml"):
        root = _odf_xml(zf, by_name, name, report)
        if root is None:
            continue
        try:
            for info in root.iter("{%s}change-info" % _NS["office"]):
                creator = info.findtext("{%s}creator" % _NS["dc"], "").strip()
                date = info.findtext("{%s}date" % _NS["dc"], "").strip()
                if creator:
                    authors.add((name, creator))
                if date:
                    dates.add(date)
            regions = sum(1 for _ in root.iter("{%s}changed-region" % _NS["text"]))
            if regions:
                report.add(Risk.IDENTITY, name, "Отслеженные изменения",
                           "%d область(ей) правок" % regions,
                           "Режим записи исправлений. Текст, который выглядит "
                           "удалённым, физически лежит в файле вместе с именем "
                           "того, кто его удалил, и временем правки.", False)
            for ann in root.iter("{%s}annotation" % _NS["office"]):
                creator = ann.findtext("{%s}creator" % _NS["dc"], "").strip()
                date = ann.findtext("{%s}date" % _NS["dc"], "").strip()
                # Текст комментария лежит в text:p; автора и дату сюда не тянем.
                body = " ".join(
                    t.strip() for p in ann.iter("{%s}p" % _NS["text"])
                    for t in p.itertext() if t.strip())
                annots.append((name, creator, date, body))
            scripts += sum(1 for _ in root.iter("{%s}event-listener" % _NS["script"]))
        except Exception as exc:
            report.err("разбор правок в %s прерван: %r" % (name, exc))

    for where, creator in sorted(authors)[:MAX_LIST]:
        report.add(Risk.IDENTITY, where, "Автор правки", creator,
                   "Имя из office:change-info. Остаётся в файле, даже когда сами "
                   "правки уже приняты, если области изменений не вычищены.", False)
    if dates:
        ordered = sorted(dates)
        report.add(Risk.PROVENANCE, "content.xml / styles.xml", "Даты правок",
                   "%d шт., %s … %s" % (len(ordered), ordered[0], ordered[-1]),
                   "Точные моменты каждой правки: по ним восстанавливается "
                   "график работы над документом.")
    for where, creator, date, body in annots[:MAX_LIST]:
        report.add(Risk.IDENTITY, where, "Аннотация от: %s" % (creator or "?"),
                   "%s %s" % (date, body),
                   "Комментарий в документе. Содержит имя автора, время и текст -- "
                   "нередко самый откровенный кусок во всём файле.")
    if annots:
        report.signal("inconsistent",
                      "В документе %d комментариев с авторами: видимый текст и "
                      "скрытое обсуждение расходятся." % len(annots), "medium")
    if scripts:
        report.signal("hazard",
                      "В документе %d обработчиков событий (script:event-listener): "
                      "активное содержимое, запускающееся при открытии." % scripts,
                      "high")


def _odf_manifest(zf, by_name, report):
    """META-INF/manifest.xml: перечень частей и признак шифрования."""
    name = "META-INF/manifest.xml"
    if name not in by_name:
        report.err("нет %s -- контейнер не соответствует спецификации ODF" % name)
        return
    root = _odf_xml(zf, by_name, name, report)
    if root is None:
        return
    entries = list(root.iter("{%s}file-entry" % _NS["manifest"]))
    encrypted = sum(1 for e in entries
                    if e.find("{%s}encryption-data" % _NS["manifest"]) is not None)
    report.add(Risk.STRUCTURAL, name, "Перечень частей контейнера",
               "%d запись(ей)" % len(entries),
               "Манифест ODF: список всех частей и их типов. Нужен для открытия "
               "файла -- сохраняем.")
    if encrypted:
        report.add(Risk.STRUCTURAL, name, "Зашифрованные части",
                   "%d шт." % encrypted,
                   "Содержимое защищено паролем: разобрать его нельзя.", False)


def _odf_extras(by_name, report):
    """Картинки, макросы, встроенные объекты, миниатюра."""
    pictures = [n for n in by_name if n.startswith("Pictures/") and not n.endswith("/")]
    macros = [n for n in by_name
              if n.startswith("Basic/") or n.startswith("Scripts/")]
    objects = sorted({n.split("/")[0] for n in by_name
                      if n.startswith("Object ") or n.startswith("ObjectReplacements")})
    thumbs = [n for n in by_name if n.startswith("Thumbnails/")]

    if pictures:
        report.add(Risk.IDENTITY, "Pictures/", "Вложенные изображения",
                   "%d файл(ов): %s" % (len(pictures),
                                        ", ".join(sorted(pictures)[:MAX_LIST])),
                   "Картинки кладутся в контейнер КАК ЕСТЬ, вместе со своим Exif: "
                   "GPS-координатами, моделью камеры, серийным номером и датой "
                   "съёмки. Чистка meta.xml их не касается -- нужен отдельный "
                   "проход по каждому файлу.", False)
    if thumbs:
        report.add(Risk.PROVENANCE, "Thumbnails/", "Миниатюра предпросмотра",
                   ", ".join(sorted(thumbs)),
                   "Картинка первой страницы. Если текст потом отредактировали, "
                   "миниатюра может показывать ПРЕДЫДУЩУЮ версию содержимого.")
    if macros:
        report.add(Risk.PROVENANCE, "Basic/ и Scripts/", "Макросы документа",
                   "%d часть(ей): %s" % (len(macros),
                                         ", ".join(sorted(macros)[:MAX_LIST])),
                   "Код Basic/скрипты внутри документа. Хранит исходники, имя "
                   "библиотеки и нередко путь к машине разработчика.", False)
        report.signal("hazard",
                      "В контейнере есть макросы (%s): активное содержимое."
                      % ", ".join(sorted(macros)[:5]), "high")
    if objects:
        report.add(Risk.IDENTITY, "Object*", "Встроенные объекты",
                   ", ".join(objects[:MAX_LIST]),
                   "Вложенные документы (формулы, диаграммы, таблицы) -- у каждого "
                   "СВОЙ meta.xml со своим автором и своими датами.", False)


# ============================================================================
#  В. RTF
# ============================================================================

# Группы внутри \info: имя управляющего слова -> (риск, имя, пояснение)
_RTF_INFO = {
    "author": (Risk.IDENTITY, "Автор",
               "Имя пользователя приложения, создавшего документ."),
    "operator": (Risk.IDENTITY, "Кто сохранял последним",
                 "В RTF \\operator -- это тот, кто последним сохранял файл. "
                 "Часто отличается от \\author и выдаёт второго участника."),
    "company": (Risk.IDENTITY, "Организация", "Название компании из настроек Office."),
    "manager": (Risk.IDENTITY, "Руководитель", ""),
    "title": (Risk.PROVENANCE, "Заголовок", ""),
    "subject": (Risk.PROVENANCE, "Тема", ""),
    "keywords": (Risk.PROVENANCE, "Ключевые слова", ""),
    "category": (Risk.PROVENANCE, "Категория", ""),
    "doccomm": (Risk.PROVENANCE, "Комментарии к документу", ""),
    "hlinkbase": (Risk.ENVIRONMENT, "База гиперссылок",
                  "Базовый путь для относительных ссылок -- обычно локальный."),
}

_RTF_DATES = {
    "creatim": ("Создан", "Момент создания документа."),
    "revtim": ("Последнее сохранение", "Момент последнего сохранения."),
    "printim": ("Последняя печать", "Когда документ печатали."),
    "buptim": ("Резервная копия", "Время создания резервной копии."),
}

_RTF_NUMS = {
    "edmins": (Risk.PROVENANCE, "Минут редактирования",
               "Суммарное время правки в минутах. Выдаёт, писали текст часами "
               "или вставили готовый за минуту."),
    "nofpages": (Risk.PROVENANCE, "Страниц", ""),
    "nofwords": (Risk.PROVENANCE, "Слов", ""),
    "nofchars": (Risk.PROVENANCE, "Символов", ""),
    "nofcharsws": (Risk.PROVENANCE, "Символов с пробелами", ""),
    "version": (Risk.PROVENANCE, "Номер версии документа",
                "Сколько раз документ сохраняли."),
    "vern": (Risk.PROVENANCE, "Внутренняя версия",
             "Внутренний номер версии приложения."),
    "id": (Risk.PROVENANCE, "Внутренний идентификатор",
           "Произвольный номер, проставленный приложением."),
}


def _brace_group(text, start):
    """text[start] == '{'. Вернуть (тело без внешних скобок, индекс за группой).

    Экранирование RTF учитывается: \\{ и \\} скобками не считаются.
    """
    depth = 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return text[start + 1:], n        # группа не закрыта -- файл битый


_CW_RE = re.compile(r"\s*\\(\*\\)?([a-zA-Z]+)(-?\d+)?\s?")


def _subgroups(body):
    """Перебрать вложенные группы первого уровня: (имя, содержимое, сырое тело)."""
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            inner, i = _brace_group(body, i)
            m = _CW_RE.match(inner)
            if m:
                yield m.group(2), inner[m.end():], inner
            else:
                yield "", inner, inner
            continue
        i += 1


def _rtf_plain(text, enc="cp1252") -> str:
    """Текст группы RTF -> обычная строка: \\'hh, \\uN, \\\\, \\{ и \\}."""
    out, raw = [], bytearray()
    uc = 1

    def flush():
        if raw:
            try:
                out.append(bytes(raw).decode(enc, "replace"))
            except LookupError:
                out.append(bytes(raw).decode("cp1252", "replace"))
            raw.clear()

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "'" and i + 3 < n:
                try:
                    raw.append(int(text[i + 2:i + 4], 16))
                    i += 4
                    continue
                except ValueError:
                    i += 2
                    continue
            if nxt in "\\{}":
                flush()
                out.append(nxt)
                i += 2
                continue
            m = _CW_RE.match(text, i)
            if not m:
                i += 2
                continue
            word, arg = m.group(2), m.group(3)
            i = m.end()
            if word == "uc" and arg is not None:
                uc = max(0, int(arg))
            elif word == "u" and arg is not None:
                flush()
                code = int(arg)
                if code < 0:
                    code += 65536
                out.append(chr(code))
                # за \uN идёт запасное ANSI-представление -- пропускаем uc единиц
                skipped = 0
                while skipped < uc and i < n:
                    if text[i] == "\\" and text[i + 1:i + 2] == "'":
                        i += 4
                    elif text[i] in "{}\\":
                        break
                    else:
                        i += 1
                    skipped += 1
            elif word in ("par", "line", "tab"):
                flush()
                out.append(" ")
            continue
        if ch in "{}":
            i += 1
            continue
        raw.extend(ch.encode("latin-1", "replace"))
        i += 1
    flush()
    return " ".join("".join(out).split())


def _dest_body(text, dest):
    """Найти назначение {\\*\\dest ...} и вернуть его тело без управляющего слова."""
    m = re.search(r"\{\\\*?\\?%s(?![a-zA-Z])" % dest, text)
    if not m:
        return None
    body, _end = _brace_group(text, m.start())
    cw = _CW_RE.match(body)
    return body[cw.end():] if cw else body


def _rtf_date(fragment) -> str:
    """\\yr2024\\mo3\\dy5\\hr14\\min7 -> '2024-03-05 14:07:00'."""
    parts = {m.group(1): int(m.group(2))
             for m in re.finditer(r"\\(yr|mo|dy|hr|min|sec)(-?\d+)", fragment)}
    if not parts:
        return ""
    return "%04d-%02d-%02d %02d:%02d:%02d" % (
        parts.get("yr", 0), parts.get("mo", 0), parts.get("dy", 0),
        parts.get("hr", 0), parts.get("min", 0), parts.get("sec", 0))


def inspect_rtf(path) -> Report:
    """Инспекция RTF. Разбор потоковый: файл целиком в память не грузится."""
    report = Report(path=str(path), fmt="rtf", size=_size(path))
    try:
        with open(path, "rb") as fh:            # строго на чтение
            head = fh.read(RTF_HEAD)
    except OSError as exc:
        report.err("файл не открылся: %r" % (exc,))
        return report

    if not head:
        report.err("файл пуст")
        return report
    if not head.lstrip()[:5] == b"{\\rtf":
        report.err("нет сигнатуры {\\rtf в начале файла -- разбираем как есть")

    text = head.decode("latin-1")               # побайтово прозрачно
    enc = _rtf_codepage(text, report)

    try:
        _rtf_info(text, enc, report)
    except Exception as exc:
        report.err("группа \\info не разобралась: %r" % (exc,))
    for fn in (_rtf_userprops, _rtf_rsid, _rtf_generator, _rtf_revtbl, _rtf_template):
        try:
            fn(text, enc, report)
        except Exception as exc:
            report.err("%s: %r" % (fn.__name__, exc))
    try:
        _rtf_scan(path, enc, report)
    except Exception as exc:
        report.err("потоковый проход по файлу прерван: %r" % (exc,))
    return report


def _rtf_codepage(text, report) -> str:
    """\\ansicpgNNNN задаёт кодировку восьмибитных escape-последовательностей."""
    m = re.search(r"\\ansicpg(\d+)", text[:4096])
    codec = "cp1252"
    if m:
        codec = _cp_codec(m.group(1))
        report.add(Risk.STRUCTURAL, "\\ansicpg", "Кодовая страница", m.group(1),
                   "Нужна для корректного чтения текста -- сохраняем.")
    m = re.search(r"\\deflang(\d+)", text[:4096])
    if m:
        report.add(Risk.ENVIRONMENT, "\\deflang", "Язык по умолчанию", m.group(1),
                   "Идентификатор локали приложения, сохранившего файл: "
                   "косвенно выдаёт страну и языковую сборку Office.")
    return codec


def _rtf_info(text, enc, report):
    """Группа \\info: свойства документа."""
    m = re.search(r"\{\\info(?![a-zA-Z])", text)
    if not m:
        report.signal("scrubbed", "В RTF нет группы \\info: свойства документа "
                                  "отсутствуют -- либо их уже чистили, либо файл "
                                  "писала программа.", "medium")
        return
    body, _end = _brace_group(text, m.start())

    for name, content, inner in _subgroups(body):
        known = _RTF_INFO.get(name)
        if known:
            risk, label, note = known
            report.add(risk, "\\info\\%s" % name, label, _rtf_plain(content, enc), note)
        elif name in _RTF_DATES:
            label, note = _RTF_DATES[name]
            report.add_nonempty(Risk.PROVENANCE, "\\info\\%s" % name, label,
                                _rtf_date(inner), note)

    # Числовые свойства лежат прямо в теле \info, без своих групп.
    for word, (risk, label, note) in _RTF_NUMS.items():
        m2 = re.search(r"\\%s(-?\d+)" % word, body)
        if not m2:
            continue
        value = m2.group(1)
        if word == "edmins":
            value = _dur_secs(int(value) * 60)
        report.add(risk, "\\info\\%s" % word, label, value, note)


def _rtf_userprops(text, enc, report):
    """{\\*\\userprops {\\propname Имя}\\proptype30{\\staticval Значение}}."""
    m = re.search(r"\{\\\*\\userprops(?![a-zA-Z])", text)
    if not m:
        return
    body, _end = _brace_group(text, m.start())
    name = None
    for word, content, _inner in _subgroups(body):
        if word == "propname":
            name = _rtf_plain(content, enc)
        elif word == "staticval":
            report.add(Risk.IDENTITY, "\\*\\userprops",
                       "Польз. свойство: %s" % clip(name or "?", 80),
                       _rtf_plain(content, enc),
                       "Произвольное поле, заполненное вручную или шаблоном "
                       "организации: тут встречаются ФИО, номера договоров и "
                       "внутренние коды подразделений.")
            name = None


def _rtf_rsid(text, _enc, report):
    """{\\*\\rsidtbl \\rsid123\\rsid456} -- идентификаторы сеансов правки."""
    m = re.search(r"\{\\\*\\rsidtbl(?![a-zA-Z])", text)
    if not m:
        return
    body, _end = _brace_group(text, m.start())
    rsids = re.findall(r"\\rsid(-?\d+)", body)
    if not rsids:
        return
    report.add(Risk.PROVENANCE, "\\*\\rsidtbl", "Таблица сеансов правки (rsid)",
               "%d идентификатор(ов): %s" % (len(rsids), ", ".join(rsids[:20])),
               "Word заводит новый rsid на каждый сеанс редактирования и "
               "помечает им каждый кусок текста. По таблице видно, за сколько "
               "заходов сделан документ, а по разметке текста -- какие абзацы "
               "дописаны позже остальных и, значит, откуда скопированы.")
    if len(rsids) > 1:
        report.signal("inconsistent",
                      "В документе %d сеансов правки (rsid): текст собирался "
                      "минимум за столько заходов, даже если даты говорят иначе."
                      % len(rsids), "medium")


def _rtf_generator(text, enc, report):
    """{\\*\\generator Riched20 10.0.19041} -- строка приложения."""
    body = _dest_body(text, "generator")
    if body is None:
        return
    value = _rtf_plain(body, enc).strip(" ;")
    report.add_nonempty(Risk.PROVENANCE, "\\*\\generator", "Генератор", value,
                        "Точная строка приложения и его версии -- сильный "
                        "отпечаток конкретной установки.")
    if value:
        report.signal("producer", "Генератор RTF: %s" % value, "high")
        marker = _looks_generated(value)
        if marker:
            report.signal("ai", "Генератор похож на библиотеку/скрипт (маркер "
                                "'%s'): %s" % (marker, value), "medium")


def _rtf_revtbl(text, enc, report):
    """{\\*\\revtbl {Unknown;}{Иванов И.И.;}} -- авторы отслеженных правок."""
    for dest, label in (("revtbl", "Автор правки"),
                        ("atrfstart", "Метка аннотации")):
        m = re.search(r"\{\\\*\\%s(?![a-zA-Z])" % dest, text)
        if not m:
            continue
        body, _end = _brace_group(text, m.start())
        names = []
        for _word, _content, inner in _subgroups(body):
            value = _rtf_plain(inner, enc).strip(" ;")
            if value:
                names.append(value)
        if not names and dest == "atrfstart":
            names = [_rtf_plain(body, enc).strip(" ;")]
        for value in names[:MAX_LIST]:
            report.add(Risk.IDENTITY, "\\*\\%s" % dest, label, value,
                       "Таблица авторов правок. Имена остаются в файле даже "
                       "после того, как сами исправления приняты или отклонены.",
                       False)
        if dest == "revtbl" and len(names) > 1:
            report.signal("inconsistent",
                          "В таблице правок %d автор(ов): над файлом работал не "
                          "один человек, хотя \\author указан один." % len(names),
                          "medium")


def _rtf_template(text, enc, report):
    """{\\*\\template ...} -- путь к шаблону."""
    body = _dest_body(text, "template")
    if body is None:
        return
    value = _rtf_plain(body, enc)
    report.add_nonempty(Risk.ENVIRONMENT, "\\*\\template", "Шаблон", value,
                        "Путь к .dot(x): либо локальный профиль пользователя, "
                        "либо сетевая шара организации.")


def _rtf_scan(path, enc, report):
    """Потоковый проход по всему файлу: объекты, пути, активное содержимое."""
    markers = {
        "objdata": 0, "objclass": 0, "objemb": 0, "objlink": 0,
        "datastore": 0, "shppict": 0, "objupdate": 0,
    }
    paths, classes = [], []
    seen_paths = set()
    tail = ""
    total = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(RTF_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            text = tail + chunk.decode("latin-1")
            last = len(chunk) < RTF_CHUNK
            # Нахлёст нужен, чтобы совпадение не разрезалось на границе куска,
            # но считать в нём нельзя: он придёт ещё раз со следующим куском.
            cut = len(text) if last else max(0, len(text) - RTF_OVERLAP)
            for word in markers:
                markers[word] += sum(
                    1 for m in re.finditer(r"\\\*?\\?%s(?![a-zA-Z])" % word, text)
                    if m.start() < cut)
            for m in _PATH_RE.finditer(text):
                if m.start() >= cut:
                    continue
                value = _rtf_plain(m.group(0), enc)
                if value and value not in seen_paths and len(seen_paths) < MAX_LIST:
                    seen_paths.add(value)
                    paths.append(value)
            for m in re.finditer(r"\{\\\*\\objclass\s*([^}]{0,120})\}", text):
                if m.start() >= cut:
                    continue
                name = _rtf_plain(m.group(1), enc)
                if name and name not in classes and len(classes) < MAX_LIST:
                    classes.append(name)
            tail = "" if last else text[cut:]

    report.add(Risk.STRUCTURAL, "файл", "Размер разобранного потока",
               "%d байт" % total, "Сколько байт прошло через потоковый разбор.")

    for value in paths:
        report.add(Risk.ENVIRONMENT, "поток RTF", "Абсолютный путь", value,
                   "Путь файловой системы прямо в теле документа: выдаёт имя "
                   "пользователя, структуру дисков и сетевые шары. Попадает сюда "
                   "из гиперссылок, полей INCLUDEPICTURE и встроенных объектов.")
    if markers["objdata"]:
        report.add(Risk.IDENTITY, "\\objdata", "Встроенные OLE-объекты",
                   "%d шт.%s" % (markers["objdata"],
                                 (", классы: " + ", ".join(classes)) if classes else ""),
                   "Объект уложен в RTF как hex-дамп целого OLE-контейнера. "
                   "Внутри -- СВОИ метаданные (автор, даты, путь к исходному "
                   "файлу) и, если это вставленный документ, его полное "
                   "содержимое, а не только видимая картинка.", False)
        report.signal("hazard",
                      "В RTF встроено %d OLE-объект(ов): внутри каждого отдельный "
                      "контейнер со своими метаданными." % markers["objdata"], "high")
    if markers["datastore"]:
        report.add(Risk.PROVENANCE, "\\*\\datastore", "Хранилище данных документа",
                   "%d шт." % markers["datastore"],
                   "Служебный двоичный блок Word: сюда складываются в том числе "
                   "настройки, не отражённые в свойствах документа.", False)
    if markers["shppict"]:
        report.add(Risk.IDENTITY, "\\shppict", "Вложенные изображения",
                   "%d шт." % markers["shppict"],
                   "Картинки лежат в RTF как есть, вместе со своим Exif: "
                   "GPS-координаты, модель камеры, серийный номер, дата съёмки. "
                   "Чистка свойств документа их не касается.", False)
    if markers["objemb"]:
        report.add(Risk.IDENTITY, "\\objemb", "Внедрённые объекты (\\objemb)",
                   "%d шт." % markers["objemb"],
                   "Флаг внедрённого OLE-объекта у каждой группы \\object. Сам "
                   "объект и его метаданные лежат в соседнем \\objdata этой же "
                   "группы.", False)
    if markers["objlink"]:
        report.add(Risk.ENVIRONMENT, "\\objlink", "Связанные объекты (\\objlink)",
                   "%d шт." % markers["objlink"],
                   "Объект не внедрён, а СВЯЗАН с внешним файлом: документ "
                   "хранит путь к исходнику и обращается к нему при обновлении "
                   "поля -- это путь на чужой машине, а не содержимое файла.",
                   False)
        report.signal("hazard",
                      "В RTF %d объект(ов) связаны с внешним файлом (\\objlink), "
                      "а не внедрены: документ обращается за пределы себя самого."
                      % markers["objlink"], "medium")
    if markers["objupdate"]:
        report.add(Risk.PROVENANCE, "\\objupdate", "Автообновление связанных объектов",
                   "%d шт." % markers["objupdate"],
                   "Связанные объекты обновляются автоматически при открытии "
                   "файла -- это активное поведение, а не статичная вставка.",
                   False)
        report.signal("hazard",
                      "Есть \\objupdate: объекты обновляются при открытии файла -- "
                      "активное содержимое.", "medium")
    if not report.findings:
        report.signal("scrubbed", "В RTF не нашлось ни одного поля метаданных: "
                                  "файл либо уже чистили, либо его собрала "
                                  "программа с нуля.", "medium")


# ============================================================================
#  Самопроверка: PYTHONIOENCODING=utf-8 python -m core.insp_legacy
# ============================================================================

class _StubOle:
    """Заглушка OleFileIO с заданными потоками -- только для самопроверки."""

    def __init__(self, streams):
        self._streams = streams

    def openstream(self, name):
        import io
        return io.BytesIO(self._streams[name])


def _demo_ole_real(tmp, show):
    """inspect_ole() на НАСТОЯЩИХ контейнерах: tests/fixtures.py собирает CFBF
    побайтово (olefile и ole32 Windows открывают их без замечаний)."""
    import faulthandler
    import struct
    import time
    try:
        from tests import fixtures as fx
    except ImportError:
        print("\n(!) tests.fixtures не импортируется -- настоящие OLE2 не проверены")
        return

    def vals(rep, label, loc=None):
        return [f.value for f in rep.findings
                if f.label == label and (loc is None or f.location == loc)]

    # --- .doc: все заложенные значения, кодовые страницы, 1Table -----------
    path, exp = fx.make_doc(tmp)
    rep = inspect_ole(path, "doc")
    show(rep, "OLE2: настоящий .doc из tests/fixtures.make_doc")
    assert not rep.errors, rep.errors
    assert vals(rep, "Автор") == [fx.DOC_AUTHOR], vals(rep, "Автор")
    assert vals(rep, "Организация", "DocumentSummaryInformation") == [fx.DOC_COMPANY]
    assert vals(rep, "Руководитель") == [fx.DOC_MANAGER]
    assert vals(rep, "Создан") == ["2023-03-05 14:07:00"], vals(rep, "Создан")
    assert "8220" in vals(rep, "Общее время редактирования")[0], rep.findings
    assert vals(rep, "Время изменения контейнера") == ["2024-03-06 09:43:07"]
    # Пользовательские свойства: UTF-16-секция, русское имя, email в метке MSIP.
    assert vals(rep, "Польз. свойство: Отдел") == [fx.DOC_CUSTOM_DEPT]
    assert vals(rep, "Польз. свойство: MSIP_Label_a1b2_SetBy") == [fx.DOC_MSIP_EMAIL]
    assert all(f.risk.value == "identity" for f in rep.findings
               if f.label.startswith("Польз."))
    assert any(not f.removable and "1Table" in f.value and
               "Остаточный текст" in f.label for f in rep.findings)
    assert vals(rep, "Счётчик быстрых сохранений") == ["2"]

    # --- .xls: cp1251-секция пользовательских свойств, WRITEACCESS, листы ---
    path, exp = fx.make_xls(tmp)
    rep = inspect_ole(path, "xls")
    assert not rep.errors, rep.errors
    assert vals(rep, "Польз. свойство: " + fx.XLS_CUSTOM_KEY) == [fx.XLS_CUSTOM_VALUE]
    assert vals(rep, "Польз. свойство: Owner") == [fx.XLS_OWNER_EMAIL]
    assert vals(rep, "Имя пользователя Excel") == [fx.XLS_WRITEACCESS_USER]
    assert fx.XLS_HIDDEN_SHEET in vals(rep, "Листы книги")[0]
    assert vals(rep, "Записи внешних связей") == ["SUPBOOK: 2, EXTERNSHEET: 1, EXTERNNAME: 0"]
    assert vals(rep, "Гиперссылки") == ["2 шт."]

    # --- .ppt: имя из CurrentUserAtom -- и из UTF-16, и из cp1251 -------------
    for uni in (True, False):
        path, exp = fx.make_ppt(tmp, unicode_name=uni)
        rep = inspect_ole(path, "ppt")
        assert vals(rep, "Имя последнего редактора (CurrentUserAtom)") == [fx.PPT_USER], \
            (uni, rep.findings)

    # --- .doc с макросами и вложенным объектом ---------------------------
    path, exp = fx.make_doc_active(tmp)
    rep = inspect_ole(path, "doc")
    assert not rep.errors, rep.errors
    assert any(s.kind == "hazard" and "VBA" in s.detail for s in rep.signals)
    assert vals(rep, "Вложенные OLE-объекты")[0].startswith("1 объект(ов): " + fx.DOC_OBJECT_ID)
    assert any("_VBA_PROJECT" in v for v in vals(rep, "Поток макросов"))

    # --- .xls со встроенным Word-объектом: sniff скажет 'doc', а разбор идёт
    #     по верхнему уровню -- книга разобрана, чужого Word-а в находках нет.
    summary, docsum = fx._ole_props_streams()
    word = (b"\xec\xa5" + b"\x00" * 4200)
    p = os.path.join(tmp, "xls_with_word.xls")
    with open(p, "wb") as fh:
        fh.write(fx._cfb_build([
            ("\x05SummaryInformation", summary),
            ("Workbook", struct.pack("<HH", 0x0809, 16) + b"\x00" * 16 + struct.pack("<HH", 0x000A, 0)),
            (("MBD0001A2B3", "\x01CompObj"), fx._ole_compobj("Word", "MSWordDoc", "Word.Document.8", fx._CLSID_WORD8)),
            (("MBD0001A2B3", "WordDocument"), word)]))
    rep = inspect_ole(p, "doc")      # fmt="doc" -- так его определит sniff
    assert not rep.errors, rep.errors
    assert vals(rep, "Поток рабочей книги"), "книга не разобрана"
    assert not vals(rep, "Остаточный текст и таблица авторов правок"), "чужой Word"
    assert any(s.kind == "inconsistent" for s in rep.signals), rep.signals

    # --- нулевой FILETIME и расходящиеся кодовые страницы потоков свойств ---
    summary = fx._ole_section([
        (1, fx._vt_i2(1252)), (4, fx._vt_lpstr("John Smith", "cp1252")),
        (11, fx._vt_filetime(0)),                       # 'не печатали'
        (12, fx._vt_filetime(fx._filetime(fx.DOC_CREATE))),
        (13, fx._vt_filetime(0))])                      # сохранение не задано
    docsum = fx._ole_section([(1, fx._vt_i2(1251)),
                              (15, fx._vt_lpstr(fx.DOC_COMPANY, "cp1251"))])
    p = os.path.join(tmp, "cp_mismatch.doc")
    with open(p, "wb") as fh:
        fh.write(fx._cfb_build([
            ("\x05SummaryInformation", fx._ole_propstream((fx._FMTID_SUMMARY, summary))),
            ("\x05DocumentSummaryInformation", fx._ole_propstream((fx._FMTID_DOCSUM, docsum))),
            ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)]))
    rep = inspect_ole(p, "doc")
    assert vals(rep, "Организация", "DocumentSummaryInformation") == [fx.DOC_COMPANY]
    assert vals(rep, "Последняя печать") == [""], vals(rep, "Последняя печать")
    assert not any(s.kind == "inconsistent" for s in rep.signals), rep.signals

    # --- векторное свойство с счётчиком 0xFFFFFFFF не вешает разбор ---------
    summary = fx._ole_section([
        (1, fx._vt_i2(1251)),
        (3, struct.pack("<HHI", 0x1005, 0, 0xFFFFFFFF)),     # VT_VECTOR|VT_R8
        (4, fx._vt_lpstr(fx.DOC_AUTHOR, "cp1251"))])
    p = os.path.join(tmp, "vector_bomb.doc")
    with open(p, "wb") as fh:
        fh.write(fx._cfb_build([
            ("\x05SummaryInformation", fx._ole_propstream((fx._FMTID_SUMMARY, summary))),
            ("WordDocument", b"\xec\xa5" + b"\x00" * 4200)]))
    faulthandler.dump_traceback_later(20, exit=True)     # регрессия -- не бесконечное зависание
    t0 = time.time()
    rep = inspect_ole(p, "doc")
    faulthandler.cancel_dump_traceback_later()
    assert time.time() - t0 < 5, "разбор векторной бомбы занял %.1f с" % (time.time() - t0)
    assert vals(rep, "Автор") == [fx.DOC_AUTHOR], rep.findings

    print("\nOK: настоящие OLE2 (.doc/.xls/.ppt, макросы, вложенные объекты, "
          "битые счётчики) разобраны")


def _demo():
    import tempfile

    tmp = tempfile.mkdtemp(prefix="insp_legacy_")

    # --- RTF: собираем руками с известными значениями -----------------------
    rtf_path = os.path.join(tmp, "probe.rtf")
    rtf = (
        r"{\rtf1\ansi\ansicpg1251\deflang1049"
        r"{\*\generator Riched20 10.0.19041;}"
        r"{\info"
        r"{\author \'c8\'e2\'e0\'ed\'ee\'e2 \'c8\'e2\'e0\'ed}"
        r"{\operator \'cf\'e5\'f2\'f0\'ee\'e2 \'cf\'e5\'f2\'f0}"
        r"{\company \'ce\'ce\'ce \'d0\'ee\'ec\'e0\'f8\'ea\'e0}"
        r"{\title \'d1\'ec\'e5\'f2\'e0 2024}"
        r"{\subject Budget}{\keywords secret;draft}{\category Internal}"
        r"{\manager Sidorov}{\doccomm Do not distribute}"
        r"{\creatim\yr2024\mo3\dy5\hr14\min7}"
        r"{\revtim\yr2024\mo3\dy6\hr9\min42}"
        r"{\printim\yr2024\mo3\dy6\hr10\min0}"
        r"\edmins137\nofpages4\nofwords1200\nofchars7800\version7\vern16393\id1234567"
        r"}"
        r"{\*\userprops{\propname Dept}\proptype30{\staticval K-17}"
        r"{\propname Contract}\proptype30{\staticval 2024/88-\'c0}}"
        r"{\*\rsidtbl \rsid1234567\rsid7654321\rsid1111111}"
        r"{\*\revtbl {Unknown;}{Ivanov I.I.;}{Petrov P.P.;}}"
        r"{\*\template C:\\Users\\ivanov\\AppData\\Roaming\\Microsoft\\Templates\\Normal.dotm}"
        r"\par Text with a link to file:///C:/secret/plan.xlsx and C:\\Work\\old.doc"
        r"{\*\objclass Word.Document.8}{\object\objemb{\*\objdata 0105000002000000}}"
        r"{\*\datastore 00}\par}"
    )
    with open(rtf_path, "w", encoding="latin-1") as fh:
        fh.write(rtf)

    # --- ODF: ZIP с mimetype, meta.xml, settings.xml, content.xml -----------
    odt_path = os.path.join(tmp, "probe.odt")
    meta_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
        '<meta:generator>LibreOffice/7.6.4.1$Linux_X86_64 '
        'LibreOffice_project/60$Build-1</meta:generator>'
        '<meta:initial-creator>Иванов Иван</meta:initial-creator>'
        '<dc:creator>Петров Пётр</dc:creator>'
        '<meta:printed-by>Сидоров С.</meta:printed-by>'
        '<meta:creation-date>2024-03-05T14:07:00</meta:creation-date>'
        '<dc:date>2024-03-06T09:42:11</dc:date>'
        '<meta:print-date>2024-03-06T10:00:00</meta:print-date>'
        '<meta:editing-cycles>7</meta:editing-cycles>'
        '<meta:editing-duration>PT2H17M45S</meta:editing-duration>'
        '<dc:title>Смета 2024</dc:title><dc:subject>Budget</dc:subject>'
        '<meta:keyword>secret</meta:keyword>'
        '<dc:description>черновик, не рассылать</dc:description>'
        '<meta:user-defined meta:name="Отдел">К-17</meta:user-defined>'
        '<meta:document-statistic meta:page-count="4" meta:word-count="1200" '
        'meta:character-count="7800"/>'
        '</office:meta></office:document-meta>'
    )
    settings_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-settings '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0">'
        '<config:config-item config:name="PrinterName" config:type="string">'
        'HP LaserJet на SRV-BUH01</config:config-item>'
        '<config:config-item config:name="CurrentDatabaseDataSource" '
        'config:type="string">file:///home/ivanov/base/kadry.odb</config:config-item>'
        '</office:document-settings>'
    )
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:body><office:text>'
        '<text:tracked-changes><text:changed-region text:id="ct1">'
        '<text:deletion><office:change-info><dc:creator>Иванов Иван</dc:creator>'
        '<dc:date>2024-03-06T09:10:00</dc:date></office:change-info>'
        '</text:deletion></text:changed-region></text:tracked-changes>'
        '<text:p>Текст<office:annotation><dc:creator>Петров Пётр</dc:creator>'
        '<dc:date>2024-03-06T09:41:00</dc:date><text:p>перепроверить цифру</text:p>'
        '</office:annotation></text:p></office:text></office:body>'
        '</office:document-content>'
    )
    manifest_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" '
        'manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )
    with zipfile.ZipFile(odt_path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype", (2024, 3, 6, 9, 42, 10)),
                    "application/vnd.oasis.opendocument.text",
                    zipfile.ZIP_STORED)
        for name, blob in (("meta.xml", meta_xml), ("settings.xml", settings_xml),
                           ("content.xml", content_xml), ("styles.xml", content_xml),
                           ("META-INF/manifest.xml", manifest_xml),
                           ("Pictures/10000001.jpg", "\xff\xd8\xffdummy"),
                           ("Basic/Standard/Module1.xml", "<x/>"),
                           ("Thumbnails/thumbnail.png", "png")):
            zf.writestr(zipfile.ZipInfo(name, (2024, 3, 6, 9, 42, 12)), blob)

    # --- OLE2: битые входы (настоящие контейнеры -- ниже, из tests/fixtures.py)
    broken_path = os.path.join(tmp, "broken.doc")
    with open(broken_path, "wb") as fh:
        fh.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200)

    def show(report, title):
        print("=" * 78)
        print("%s  [fmt=%s, size=%d]" % (title, report.fmt, report.size))
        print("-" * 78)
        for f in report.findings:
            print("  [%s] %-28s | %-34s | %s%s"
                  % (f.risk.value[:4], f.location[:28], f.label[:34],
                     f.value[:70], "" if f.removable else "   <НЕУДАЛЯЕМО>"))
        for s in report.signals:
            print("  ~ %-12s (%s) %s" % (s.kind, s.confidence, s.detail))
        for e in report.errors:
            print("  ! %s" % e)
        print("  итого: находок %d, из них требуют чистки %d, частей %d, ошибок %d"
              % (len(report.findings), report.sensitive, len(report.parts),
                 len(report.errors)))

    r_rtf = inspect_rtf(rtf_path)
    show(r_rtf, "RTF")
    r_odf = inspect_odf(odt_path, "odt")
    show(r_odf, "ODF (.odt)")
    for name, probe in (("OLE2: отсутствующий файл", os.path.join(tmp, "нет.doc")),
                        ("OLE2: битая сигнатура", broken_path),
                        ("OLE2: подсунут ODF", odt_path)):
        show(inspect_ole(probe, "doc"), name)

    # Сторонний OLE2-файл из системы (если есть): .msi -- тоже OLE2 со своим
    # SummaryInformation. Контейнеры .doc/.xls/.ppt собирает tests/fixtures.py.
    import glob
    real = sorted(glob.glob(r"C:\Windows\Installer\*.msi"))
    if real:
        r_ole = inspect_ole(real[0], "ole")
        show(r_ole, "OLE2: настоящий контейнер %s" % os.path.basename(real[0]))
        assert not r_ole.errors, r_ole.errors
        assert r_ole.parts and r_ole.findings
    else:
        print("\n(!) OLE2-файла для теста обхода контейнера в системе не нашлось")

    # Специфика .doc проверяется на заглушке потока: разбор FIB, выбор активной
    # таблицы и текст про остаточное содержимое -- это чистая логика.
    r_doc = Report(path="stub.doc", fmt="doc")
    flags = 0x0004 | 0x0200 | (3 << 4)     # fComplex + активна 1Table + 3 быстрых
    fib = (b"\xec\xa5" + b"\xc1\x00" + b"\x00\x00" + b"\x19\x04" + b"\x00\x00"
           + flags.to_bytes(2, "little") + b"\x00" * 52)
    _ole_word(_StubOle({"WordDocument": fib}), r_doc,
              {"WordDocument": [("WordDocument", len(fib), "stream")],
               "0Table": [("0Table", 512, "stream")],
               "1Table": [("1Table", 4096, "stream")]})
    show(r_doc, "OLE2: логика .doc на заглушке потока WordDocument")
    dvalues = {f.label: f.value for f in r_doc.findings}
    assert "0Table" in dvalues.get("Неактивный поток таблицы", ""), dvalues
    assert dvalues.get("Счётчик быстрых сохранений") == "3", dvalues
    assert dvalues.get("Язык приложения (lid)") == "0x0419", dvalues
    assert any(not f.removable and "Остаточный текст" in f.label
               for f in r_doc.findings), dvalues
    assert any(s.kind == "hazard" and s.confidence == "high" and
               "нельзя чистить на месте" in s.detail for s in r_doc.signals)
    assert not r_doc.errors, r_doc.errors

    _demo_ole_real(tmp, show)

    # Минимальные проверки: если разбор сломается, это упадёт здесь, а не в боте.
    # \uN: unicode-экранирование с запасным ANSI-символом, который надо съесть
    assert _rtf_plain(r"\u1055 ?\u1105 ?\u1090 ?\u1088 !") == "Пётр", \
        _rtf_plain(r"\u1055 ?\u1105 ?\u1090 ?\u1088 !")

    values = {f.label: f.value for f in r_rtf.findings}
    assert values.get("Автор") == "Иванов Иван", values.get("Автор")
    assert values.get("Кто сохранял последним") == "Петров Петр", values
    assert values.get("Генератор") == "Riched20 10.0.19041", values
    assert values.get("Шаблон", "").startswith("C:\\Users\\ivanov"), values
    assert values.get("Создан") == "2024-03-05 14:07:00", values.get("Создан")
    assert "2 ч 17 мин" in values.get("Минут редактирования", ""), values
    assert any("rsid" in f.label.lower() for f in r_rtf.findings)
    assert any(f.label == "Автор правки" and "Ivanov" in f.value
               for f in r_rtf.findings)
    assert any("Абсолютный путь" == f.label and "secret" in f.value
               for f in r_rtf.findings), [f.value for f in r_rtf.findings]

    # Многокусковый проход: маркер, попавший в нахлёст, не должен удвоиться.
    big_path = os.path.join(tmp, "big.rtf")
    with open(big_path, "w", encoding="latin-1") as fh:
        fh.write(r"{\rtf1\ansi" + "A" * (RTF_CHUNK - 40)
                 + r"{\*\objdata 0102}" + "B" * 5000
                 + r"{\*\objdata 0304}" + "}")
    r_big = inspect_rtf(big_path)
    show(r_big, "RTF: файл больше одного куска (проверка нахлёста)")
    objs = {f.label: f.value for f in r_big.findings}.get("Встроенные OLE-объекты", "")
    assert objs.startswith("2 шт."), objs

    ovalues = {f.label: f.value for f in r_odf.findings}
    assert ovalues.get("Первоначальный автор") == "Иванов Иван", ovalues
    assert "LibreOffice/7.6.4.1" in ovalues.get("Генератор", "")
    assert "2 ч 17 мин 45 с" in ovalues.get("Время редактирования", ""), ovalues
    assert "2024-03-06 09:42" in ovalues.get("Времена записей контейнера", "")
    assert any(f.label.startswith("Принтер") for f in r_odf.findings)
    assert len(r_odf.parts) == 9, len(r_odf.parts)
    assert any(f.label.startswith("Аннотация") and
               f.value.endswith("перепроверить цифру") for f in r_odf.findings), \
        [f.value for f in r_odf.findings if f.label.startswith("Аннотация")]

    print("\nOK: самопроверка пройдена, временные файлы в %s" % tmp)


if __name__ == "__main__":
    _demo()
