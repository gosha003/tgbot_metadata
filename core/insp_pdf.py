"""Инспектор PDF: что именно течёт из файла и откуда.

Фаза 0 -- только чтение. Файл открывается строго на чтение (pikepdf.open без
allow_overwriting_input), ничего не сохраняется и не переписывается.

Инспектор не бросает исключений наружу: любая недоступная или битая секция --
это запись в report.errors, а не падение. Нам присылают и повреждённые, и
намеренно кривые файлы.

Требует pikepdf (биндинги QPDF, отдельный qpdf CLI не нужен) и lxml.
exiftool -- необязательное обогащение: если найден, дополняем находки; нет --
молча работаем без него.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pikepdf
from lxml import etree

from .model import Report, Risk, clip

# --- лимиты (защита от бомб и от файлов, собранных чтобы нас подвесить) ------

MAX_PAGES = 300            # страниц, по которым ходим постранично
MAX_ANNOTS = 400           # аннотаций всего
MAX_FIELDS = 300           # полей формы всего
MAX_FONTS = 300            # шрифтов всего
MAX_ATTACH = 100           # вложений всего
MAX_OCGS = 200             # слоёв optional content
MAX_XMP_KEYS = 400         # ключей XMP
MAX_HISTORY = 60           # событий xmpMM:History
MAX_STREAM = 50 * 1024 * 1024   # больше 50 МБ в память не читаем
MAX_XMP_PACKED = 8 * 1024 * 1024  # сжатый XMP больше 8 МБ не распаковываем
SCAN_CHUNK = 4 * 1024 * 1024    # размер чанка сырого скана
SCAN_OVERLAP = 512              # нахлёст между чанками, чтобы не терять совпадения

# --- карты имён --------------------------------------------------------------

_NS_PREFIX = {
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://purl.org/dc/elements/1.1/": "dc",
    "http://purl.org/dc/terms/": "dcterms",
    "http://ns.adobe.com/xap/1.0/": "xmp",
    "http://ns.adobe.com/xap/1.0/mm/": "xmpMM",
    "http://ns.adobe.com/xap/1.0/rights/": "xmpRights",
    "http://ns.adobe.com/xap/1.0/bj/": "xmpBJ",
    "http://ns.adobe.com/xap/1.0/t/pg/": "xmpTPg",
    "http://ns.adobe.com/xap/1.0/g/": "xmpG",
    "http://ns.adobe.com/xap/1.0/g/img/": "xmpGImg",
    "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#": "stEvt",
    "http://ns.adobe.com/xap/1.0/sType/ResourceRef#": "stRef",
    "http://ns.adobe.com/xap/1.0/sType/Dimensions#": "stDim",
    "http://ns.adobe.com/xap/1.0/sType/Version#": "stVer",
    "http://ns.adobe.com/xap/1.0/sType/Job#": "stJob",
    "http://ns.adobe.com/pdf/1.3/": "pdf",
    "http://ns.adobe.com/pdfx/1.3/": "pdfx",
    "http://www.aiim.org/pdfa/ns/id/": "pdfaid",
    "http://www.aiim.org/pdfua/ns/id/": "pdfuaid",
    "http://ns.adobe.com/photoshop/1.0/": "photoshop",
    "http://ns.adobe.com/illustrator/1.0/": "illustrator",
    "http://ns.adobe.com/InDesign/1.0/": "indesign",
    "http://ns.adobe.com/camera-raw-settings/1.0/": "crs",
    "http://ns.adobe.com/exif/1.0/": "exif",
    "http://ns.adobe.com/tiff/1.0/": "tiff",
    "http://ns.adobe.com/xmp/note/": "xmpNote",
    "http://ns.adobe.com/xmp/Identifier/qual/1.0/": "xmpidq",
    "adobe:ns:meta/": "x",
}

RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"

# Ключи /Info: человеческое имя + категория риска.
_INFO_MAP = {
    "/Author": ("Автор", Risk.IDENTITY),
    "/Creator": ("Creator (приложение-автор)", Risk.PROVENANCE),
    "/Producer": ("Producer (библиотека-писатель)", Risk.PROVENANCE),
    "/CreationDate": ("Дата создания", Risk.PROVENANCE),
    "/ModDate": ("Дата изменения", Risk.PROVENANCE),
    "/Title": ("Заголовок", Risk.PROVENANCE),
    "/Subject": ("Тема", Risk.PROVENANCE),
    "/Keywords": ("Ключевые слова", Risk.PROVENANCE),
    "/Trapped": ("Trapped (треппинг для печати)", Risk.PROVENANCE),
}

# Постоянные GUID XMP: связывают между собой ВСЕ версии документа.
_XMP_GUID = {
    "xmpMM:DocumentID",
    "xmpMM:InstanceID",
    "xmpMM:OriginalDocumentID",
    "xmpMM:DerivedFrom",
}

# Ключи XMP, которые тянут на личность.
_XMP_IDENTITY = {
    "dc:creator", "dc:rights", "dc:publisher", "dc:contributor",
    "photoshop:Credit", "photoshop:AuthorsPosition", "photoshop:Source",
    "xmpRights:Owner", "xmpRights:WebStatement", "xmpRights:Marked",
    "xmpRights:UsageTerms", "pdfx:Company", "pdf:Author",
}

# Отпечатки продюсеров: (регексп, что это, библиотечный ли генератор).
_TOOL_MARKS = [
    (re.compile(r"Skia/PDF", re.I),
     "печать из headless Chrome/Chromium (движок Skia/PDF) -- Puppeteer/Playwright или «Сохранить как PDF»", True),
    (re.compile(r"ReportLab", re.I), "python-библиотека ReportLab", True),
    (re.compile(r"(PyFPDF|fpdf2|FPDF\s)", re.I), "python-библиотека fpdf2/PyFPDF", True),
    (re.compile(r"wkhtmltopdf", re.I), "wkhtmltopdf (HTML -> PDF через Qt WebKit)", True),
    (re.compile(r"(WeasyPrint|Prince|dompdf|TCPDF|mPDF|iText|Apache FOP)", re.I),
     "серверная библиотека генерации PDF", True),
    (re.compile(r"(pikepdf|pypdf|PyPDF2|cpdf|MuPDF|mutool)", re.I),
     "python/CLI-библиотека постобработки PDF", True),
    (re.compile(r"pdfTeX-1\.40", re.I), "LaTeX (pdfTeX 1.40.x)", False),
    (re.compile(r"(XeTeX|LuaTeX|dvips|dvipdfm)", re.I), "TeX-тулчейн", False),
    (re.compile(r"LibreOffice|OpenOffice", re.I), "LibreOffice/OpenOffice", False),
    (re.compile(r"Microsoft.*Print To PDF", re.I), "виртуальный принтер «Microsoft Print to PDF»", False),
    (re.compile(r"Microsoft.*(Word|Excel|PowerPoint)", re.I), "Microsoft Office", False),
    (re.compile(r"Adobe.*Distiller", re.I), "Adobe Distiller", False),
    (re.compile(r"(Adobe.*Acrobat|Adobe PDF Library|Acrobat Distiller)", re.I), "Adobe Acrobat", False),
    (re.compile(r"Ghostscript", re.I), "Ghostscript", True),
    (re.compile(r"\bqpdf\b", re.I), "qpdf", True),
    (re.compile(r"pandoc", re.I), "pandoc", True),
    (re.compile(r"Quartz PDFContext", re.I), "macOS (Quartz PDFContext) -- файл сделан на Mac", False),
    (re.compile(r"Chromium", re.I), "Chromium", True),
    (re.compile(r"Nitro|Foxit|PDF-XChange|Soda PDF", re.I), "сторонний PDF-редактор", False),
    (re.compile(r"Canva|Figma|Google", re.I), "онлайн-сервис (Canva/Figma/Google)", False),
]

# Продюсеры, которые появляются в файле только после ПОСТОБРАБОТКИ.
_REWRITER = re.compile(r"(qpdf|Ghostscript|pikepdf|pypdf|PyPDF2|cpdf|mutool|MuPDF|exiftool)", re.I)
# Приложения-авторы, которые пишут свой собственный Producer.
_AUTHORING = re.compile(r"(Microsoft|Word|Excel|PowerPoint|LibreOffice|OpenOffice|Acrobat|InDesign|Illustrator|Pages)", re.I)

# Шрифты, которые датируют документ независимо от заявленных дат.
_FONT_ERA = [
    ("aptos", "Aptos -- шрифт по умолчанию Microsoft 365 с 2023 года: документ создан НЕ РАНЬШЕ 2023-го, "
              "чем бы ни были заявленные даты"),
    ("calibri", "Calibri -- шрифт по умолчанию Office 2007-2023: документ из этого окна времени"),
    ("aptos display", "Aptos Display -- гарнитура Microsoft 365 с 2023 года"),
]

_SUBSET = re.compile(r"^([A-Z]{6})\+(.+)$")

_PATHISH = re.compile(
    r"(^[A-Za-z]:[\\/])|(^\\\\)|(^file://)|(/(Users|home|Volumes|var|tmp|mnt)/)|(\\Users\\)",
    re.I,
)

# Стандартные форматы бумаги: мм -> имя (формат бумаги выдаёт страну).
_PAPER = [
    (210.0, 297.0, "A4 (ISO, весь мир кроме Северной Америки)"),
    (215.9, 279.4, "Letter (США/Канада)"),
    (215.9, 355.6, "Legal (США)"),
    (297.0, 420.0, "A3 (ISO)"),
    (148.0, 210.0, "A5 (ISO)"),
    (279.4, 431.8, "Tabloid (США)"),
]

_PDF_DATE = re.compile(
    r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?\s*([Zz+\-])?(\d{2})?'?(\d{2})?"
)


# --- мелкие утилиты ----------------------------------------------------------


def _s(obj) -> str:
    """Значение PDF-объекта в строку. Никогда не бросает."""
    if obj is None:
        return ""
    try:
        if isinstance(obj, pikepdf.String):
            return str(obj)
        if isinstance(obj, pikepdf.Name):
            return str(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", "replace")
        text = str(obj)
        # У словарей и массивов str() даёт repr вида pikepdf.Array([...]):
        # в отчёт идёт только содержимое, без обёртки.
        if text.startswith("pikepdf.") and "(" in text:
            text = text.split("(", 1)[1].rsplit(")", 1)[0].strip()
        return text
    except Exception:
        return "<нечитаемое значение>"


def _get(dct, key):
    """dct.get(key), не падающий на битых/чужих объектах."""
    try:
        return dct.get(key)
    except Exception:
        return None


def _stream_text(obj) -> str:
    """Текст тела: /JS (и подобные поля) в PDF бывает и строкой, и потоком --
    тело скрипта лежит в потоке ровно так же часто, как строкой. Сжатый размер
    проверяем перед распаковкой -- та же защита от бомбы, что и для /Metadata."""
    if obj is None:
        return ""
    try:
        packed = len(obj.read_raw_bytes())
        if packed > MAX_XMP_PACKED:
            return "<поток слишком велик для распаковки: %d байт сжатых>" % packed
        return obj.read_bytes()[:MAX_STREAM].decode("utf-8", "replace")
    except Exception:
        return _s(obj)


def _pdf_date(raw: str) -> str:
    """D:YYYYMMDDHHmmSS+TZ -> читаемый вид. Пустая строка, если не разобрали."""
    m = _PDF_DATE.match((raw or "").strip())
    if not m:
        return ""
    y, mo, d, hh, mm, ss, sign, tzh, tzm = m.groups()
    out = "%s-%s-%s" % (y, mo or "01", d or "01")
    if hh:
        out += " %s:%s:%s" % (hh, mm or "00", ss or "00")
    if sign in ("Z", "z"):
        out += " UTC"
    elif sign and tzh:
        out += " UTC%s%s:%s" % (sign, tzh, tzm or "00")
    return out


def _looks_like_path(value: str) -> bool:
    return bool(_PATHISH.search(value or ""))


def _qname(tag) -> str:
    """{uri}Local -> prefix:Local по известным пространствам имён."""
    tag = str(tag)
    if not tag.startswith("{"):
        return tag
    uri, _, local = tag[1:].partition("}")
    prefix = _NS_PREFIX.get(uri)
    if not prefix:
        prefix = uri.rstrip("/#").rsplit("/", 1)[-1][:24] or "ns"
    return "%s:%s" % (prefix, local)


# --- А. вскрытие файла -------------------------------------------------------


def inspect_pdf(path) -> Report:
    """Полная read-only инспекция PDF. Всегда возвращает Report, не бросает."""
    report = Report(path=str(path), fmt="pdf")
    try:
        report.size = os.path.getsize(path)
    except OSError as exc:
        report.err("Не удалось получить размер файла: %s" % exc)

    # Сырой скан работает даже на файле, который pikepdf открыть не сможет.
    ctx = {}
    try:
        _sec_raw(path, report, ctx)
    except Exception as exc:
        report.err("Сырой скан байтов не удался: %s: %s" % (type(exc).__name__, exc))

    pdf = None
    try:
        pdf = pikepdf.open(path)   # пустой пароль пробуется автоматически
    except pikepdf.PasswordError:
        report.err("PDF зашифрован и НЕ открывается пустым паролем -- содержимое и "
                   "метаданные недоступны без пароля владельца/пользователя.")
        report.signal("hazard", "Файл защищён паролем пользователя: инспекция невозможна", "high")
        return report
    except Exception as exc:
        report.err("pikepdf не смог открыть файл (%s): %s" % (type(exc).__name__, exc))
        report.signal("hazard", "PDF структурно повреждён: QPDF отказался его разбирать", "high")
        return report

    sections = (
        _sec_encryption,   # А
        _sec_docinfo,      # Б
        _sec_xmp,          # В
        _sec_file_id,      # Г
        _sec_annots,       # Е
        _sec_acroform,     # Ж + К
        _sec_attachments,  # З
        _sec_active,       # И
        _sec_layers,       # Л
        _sec_fonts,        # М
        _sec_pieceinfo,    # Н
        _sec_structural,   # О
        _sec_overview,     # П
    )
    try:
        with pdf:
            for fn in sections:
                try:
                    fn(pdf, report, ctx)
                except Exception as exc:
                    report.err("Секция %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
            try:
                _sec_signals(pdf, report, ctx)   # Р
            except Exception as exc:
                report.err("Секция сигналов: %s: %s" % (type(exc).__name__, exc))
    except Exception as exc:
        report.err("Разбор прерван: %s: %s" % (type(exc).__name__, exc))

    try:
        _enrich_exiftool(path, report, ctx)
    except Exception as exc:
        report.err("Обогащение exiftool не удалось (не критично): %s" % exc)

    return report


def _sec_encryption(pdf, report, ctx):
    """А. Шифрование и права доступа."""
    if not pdf.is_encrypted:
        return
    report.add(Risk.PROVENANCE, "/Encrypt", "Шифрование",
               "есть, открывается пустым паролем",
               note="Файл зашифрован, но пароль пользователя пустой: это типичная "
                    "«защита от печати/копирования», которая ничего не защищает, зато "
                    "фиксирует, что файл готовили в Acrobat или похожем редакторе.",
               removable=True)
    try:
        enc = pdf.encryption
        report.add(Risk.PROVENANCE, "/Encrypt", "Параметры шифрования",
                   "R=%s V=%s P=%s %s" % (getattr(enc, "R", "?"), getattr(enc, "V", "?"),
                                          getattr(enc, "P", "?"), getattr(enc, "stream_method", "")),
                   note="Ревизия и алгоритм шифрования косвенно датируют версию Acrobat.")
    except Exception as exc:
        report.err("Параметры /Encrypt не читаются: %s" % exc)
    try:
        report.add(Risk.PROVENANCE, "/Encrypt", "Разрешённые операции", str(pdf.allow),
                   note="Набор запретов (печать, извлечение текста, правка) -- это тоже "
                        "отпечаток того, кто и чем готовил файл.")
    except Exception as exc:
        report.err("Права /Encrypt не читаются: %s" % exc)


def _sec_docinfo(pdf, report, ctx):
    """Б. Словарь /Info со всеми ключами, включая нестандартные."""
    try:
        info = pdf.docinfo
        keys = list(info.keys())
    except Exception as exc:
        report.err("/Info не читается: %s" % exc)
        ctx["info"] = {}
        return

    collected = {}
    ctx["info"] = collected
    ctx["info_present"] = bool(keys)
    for key in keys:
        try:
            raw = _s(info[key])
        except Exception as exc:
            report.err("/Info %s не читается: %s" % (key, exc))
            continue
        collected[key] = raw
        label, risk = _INFO_MAP.get(key, (None, None))
        if label is None:
            # Нестандартный ключ: их пишут корпоративные DMS, плагины и скрипты.
            label = "Нестандартный ключ %s" % key
            low = key.lower()
            if any(w in low for w in ("author", "user", "owner", "company", "operator", "signer")):
                risk = Risk.IDENTITY
            elif _looks_like_path(raw):
                risk = Risk.ENVIRONMENT
            else:
                risk = Risk.PROVENANCE
            note = ("Ключ вне спецификации PDF: его пишет конкретное приложение или "
                    "корпоративная система документооборота, поэтому он особенно "
                    "хорошо опознаёт источник файла.")
        elif _looks_like_path(raw):
            risk = Risk.ENVIRONMENT
            note = "Значение похоже на локальный путь: выдаёт имя пользователя и структуру диска."
        else:
            note = ""

        if key in ("/CreationDate", "/ModDate"):
            human = _pdf_date(raw)
            value = "%s   (оригинал: %s)" % (human, raw) if human else raw
            note = note or ("Дата в формате PDF D:YYYYMMDDHHmmSS+TZ. Смещение часового "
                            "пояса выдаёт географию автора.")
            report.add(risk, "/Info", label, value, note=note)
        else:
            report.add(risk, "/Info", label, raw, note=note)


def _sec_xmp(pdf, report, ctx):
    """В. XMP: и через pikepdf, и сырым потоком /Metadata. Про него забывают."""
    raw = b""
    try:
        meta = _get(pdf.Root, "/Metadata")
        if meta is not None:
            packed = len(meta.read_raw_bytes())
            if packed > MAX_XMP_PACKED:
                report.err("Поток /Metadata слишком велик (%d байт в сжатом виде) -- "
                           "не распаковываем, защита от бомбы." % packed)
            else:
                raw = meta.read_bytes()[:MAX_STREAM]
    except Exception as exc:
        report.err("Сырой поток /Metadata не читается: %s" % exc)

    pairs = []
    if raw:
        report.add(Risk.PROVENANCE, "/Metadata", "XMP-пакет", "%d байт" % len(raw),
                   note="XMP лежит отдельным потоком и переживает чистку /Info: чаще всего "
                        "именно тут остаётся всё, что «уже удалили».")
        pairs = _parse_xmp(raw, report)
    else:
        # Запасной путь -- через pikepdf, если сырой поток недоступен.
        try:
            md = pdf.open_metadata()
            pairs = [(_qname(k), _s(v if not isinstance(v, list) else "; ".join(map(str, v))))
                     for k, v in dict(md).items()]
        except Exception as exc:
            report.err("pdf.open_metadata() не читается: %s" % exc)

    ctx["xmp_present"] = bool(pairs)
    ctx["xmp"] = {k: v for k, v in pairs}

    if not pairs:
        return

    for key, value in pairs[:MAX_XMP_KEYS]:
        if key in _XMP_GUID:
            report.add(Risk.IDENTITY, "XMP", key, value,
                       note="Постоянный GUID документа. Он НЕ меняется при пересохранении и "
                            "связывает между собой все версии файла и все файлы, сделанные из "
                            "одного шаблона: по нему два разных на вид документа сводятся в один.")
        elif key.startswith("xmpMM:History"):
            report.add(Risk.PROVENANCE, "XMP", key, value,
                       note="Событие журнала правок xmpMM:History -- фактическая история того, "
                            "чем и когда файл редактировали.")
        elif key in _XMP_IDENTITY or key.startswith(("xmpRights:", "photoshop:Author")):
            report.add(Risk.IDENTITY, "XMP", key, value,
                       note="Имя/правообладатель в XMP: переживает чистку /Info.")
        elif _looks_like_path(value):
            report.add(Risk.ENVIRONMENT, "XMP", key, value,
                       note="В значении локальный путь: выдаёт имя пользователя и структуру диска.")
        elif key in ("xmp:CreateDate", "xmp:ModifyDate", "xmp:MetadataDate"):
            report.add(Risk.PROVENANCE, "XMP", key, value,
                       note="Дата XMP. xmp:MetadataDate часто новее xmp:ModifyDate -- разница "
                            "показывает, что метаданные трогали отдельно от содержимого.")
        elif key.startswith("pdfaid:") or key.startswith("pdfuaid:"):
            report.add(Risk.STRUCTURAL, "XMP", key, value,
                       note="Декларация соответствия PDF/A или PDF/UA. При чистке обычно сохраняем: "
                            "без неё файл перестаёт быть архивным/доступным.")
        else:
            report.add(Risk.PROVENANCE, "XMP", key, value)

    if len(pairs) > MAX_XMP_KEYS:
        report.err("XMP: показаны первые %d ключей из %d." % (MAX_XMP_KEYS, len(pairs)))


def _parse_xmp(raw: bytes, report) -> list:
    """Разбор XMP-пакета в плоский список (ключ, значение), включая History."""
    try:
        parser = etree.XMLParser(recover=True, resolve_entities=False,
                                 no_network=True, huge_tree=False)
        root = etree.fromstring(raw, parser=parser)
    except Exception as exc:
        report.err("XMP не разбирается как XML: %s" % exc)
        return []
    if root is None:
        report.err("XMP пуст или полностью нечитаем.")
        return []

    out = []
    seen = set()

    def push(key, value):
        value = clip(value)
        if not key or (key, value) in seen:
            return
        seen.add((key, value))
        out.append((key, value))

    for desc in root.iter(RDF + "Description"):
        # Компактная форма: свойства сидят атрибутами на rdf:Description.
        for attr, value in desc.attrib.items():
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            push(key, value)
        # Развёрнутая форма: каждое свойство -- отдельный дочерний элемент.
        for child in desc:
            key = _qname(child.tag)
            if key == "xmpMM:History":
                _parse_history(child, push)
                continue
            push(key, _flatten(child))
        if len(out) > MAX_XMP_KEYS * 2:
            break
    return out


def _flatten(elem) -> str:
    """Значение узла XMP: текст, склейка rdf:li или разложенная структура."""
    text = (elem.text or "").strip()
    if text:
        return text
    lis = elem.findall(".//" + RDF + "li")
    if lis:
        parts = [(li.text or "").strip() for li in lis]
        parts = [p for p in parts if p]
        if parts:
            return "; ".join(parts[:20])
    bits = []
    for sub in elem.iter():
        for attr, value in sub.attrib.items():
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            bits.append("%s=%s" % (key, value))
        if sub is not elem:
            txt = (sub.text or "").strip()
            if txt:
                bits.append("%s=%s" % (_qname(sub.tag), txt))
    return "; ".join(bits[:20])


def _parse_history(node, push):
    """xmpMM:History -> по событию на запись. Это и есть журнал правок."""
    events = node.findall(".//" + RDF + "li") or [node]
    for idx, ev in enumerate(events[:MAX_HISTORY]):
        fields = []
        for attr, value in ev.attrib.items():
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            fields.append("%s=%s" % (key.split(":")[-1], value))
        for sub in ev:
            txt = (sub.text or "").strip()
            if txt:
                fields.append("%s=%s" % (_qname(sub.tag).split(":")[-1], txt))
        if fields:
            push("xmpMM:History[%d]" % idx, "; ".join(fields))


def _sec_file_id(pdf, report, ctx):
    """Г. trailer /ID -- пара 16-байтовых идентификаторов файла."""
    ids = _get(pdf.trailer, "/ID")
    if ids is None:
        report.add(Risk.PROVENANCE, "trailer", "/ID", "отсутствует",
                   note="Трейлер без /ID: либо файл собран вручную, либо его уже переписывали "
                        "инструментом, который /ID не восстановил.")
        return
    try:
        items = list(ids)
    except Exception as exc:
        report.err("trailer /ID не читается: %s" % exc)
        return
    labels = ["/ID[0] (постоянный, задаётся при создании)",
              "/ID[1] (меняется при каждом сохранении)"]
    hexes = []
    for i, item in enumerate(items[:2]):
        try:
            hx = bytes(item).hex()
        except Exception:
            hx = _s(item)
        hexes.append(hx)
        report.add(Risk.IDENTITY, "trailer", labels[i] if i < 2 else "/ID[%d]" % i, hx,
                   note="У многих продюсеров /ID[0] выводится из полного пути и имени исходного "
                        "файла: одинаковый /ID[0] у двух PDF означает общий источник.")
    ctx["id_equal"] = len(hexes) == 2 and hexes[0] == hexes[1]
    if ctx.get("id_equal"):
        report.add(Risk.PROVENANCE, "trailer", "/ID[0] == /ID[1]", hexes[0],
                   note="Оба идентификатора совпадают: файл ни разу не пересохраняли после "
                        "создания, либо его собрали одним проходом (типично для генераторов).")


def _sec_raw(path, report, ctx):
    """Д. Сырой скан байтов: инкрементальные апдейты, версия, ObjStm, TeX-баннер.

    Работает на любом файле, в том числе на таком, который pikepdf не открывает.
    Читаем чанками с нахлёстом, ничего крупного в память не тянем.
    """
    needles = re.compile(rb"%%EOF|startxref|/Prev|/ObjStm|/Linearized|/Encrypt|/XFA")
    banner = re.compile(rb"/PTEX\.Fullbanner\s*\(([^)]{0,400})\)")
    header = re.compile(rb"%PDF-(\d\.\d)")

    hits = {}
    banners = []
    version = ""
    offset = 0
    prev = b""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(SCAN_CHUNK)
                if not chunk:
                    break
                buf = prev + chunk
                base = offset - len(prev)
                if not version:
                    m = header.search(buf[:2048])
                    if m:
                        version = m.group(1).decode("ascii", "replace")
                for m in needles.finditer(buf):
                    hits.setdefault(m.group(0), set()).add(base + m.start())
                for m in banner.finditer(buf):
                    banners.append(m.group(1).decode("utf-8", "replace"))
                offset += len(chunk)
                prev = buf[-SCAN_OVERLAP:]
    except OSError as exc:
        report.err("Сырой скан: файл не читается: %s" % exc)
        return

    n_eof = len(hits.get(b"%%EOF", ()))
    n_startxref = len(hits.get(b"startxref", ()))
    n_prev = len(hits.get(b"/Prev", ()))
    ctx["header_version"] = version
    ctx["objstm"] = bool(hits.get(b"/ObjStm"))
    ctx["raw_linearized"] = bool(hits.get(b"/Linearized"))

    if version:
        report.add(Risk.PROVENANCE, "заголовок", "Версия PDF (%%PDF-)", version,
                   note="Версия в заголовке часто расходится с возможностями файла: её "
                        "выставляет конкретный продюсер и по ней он опознаётся.")

    report.add(Risk.PROVENANCE, "сырые байты", "Маркеров %%EOF / startxref / /Prev",
               "%d / %d / %d" % (n_eof, n_startxref, n_prev),
               note="Один %%EOF -- файл писали одним проходом.")

    if n_eof > 1 or n_startxref > 1 or n_prev > 0:
        report.add(Risk.IDENTITY, "сырые байты", "Инкрементальные апдейты",
                   "%d ревизий (по %%EOF), /Prev в трейлере: %d" % (max(n_eof, n_startxref), n_prev),
                   note="В файле ФИЗИЧЕСКИ ЛЕЖАТ ПРЕДЫДУЩИЕ ВЕРСИИ документа: старые объекты, "
                        "старые xref и, в частности, текст под «заредактированными» чёрными "
                        "прямоугольниками -- прямоугольник рисуется поверх, а текст остаётся "
                        "в предыдущей ревизии и достаётся обычным текстовым поиском. "
                        "Пересохранение с linearize (или полная перезапись объектов) выбрасывает "
                        "все прошлые ревизии -- это и есть способ их убрать.")
        report.signal("hazard",
                      "PDF содержит %d инкрементальных ревизий: прошлые версии страниц и "
                      "«зачёркнутый» текст извлекаются из файла напрямую" % max(n_eof, n_startxref),
                      "high")

    if ctx["objstm"]:
        report.add(Risk.PROVENANCE, "сырые байты", "Object streams (/ObjStm)", "есть",
                   note="Объекты упакованы в сжатые потоки (PDF 1.5+). Простым grep-ом по файлу "
                        "метаданные не видны, но при разборе достаются полностью.")

    for b in banners[:10]:
        report.add(Risk.ENVIRONMENT, "/PTEX.Fullbanner", "Баннер TeX", b,
                   note="Полная строка версии TeX Live с датой сборки дистрибутива: датирует "
                        "рабочую машину автора и выдаёт его ОС/дистрибутив.")
    if banners:
        report.signal("producer", "pdfTeX/LaTeX: в файле есть /PTEX.Fullbanner -- %s" % banners[0], "high")


def _pages(pdf, report):
    """Безопасный итератор по страницам с лимитом."""
    try:
        total = len(pdf.pages)
    except Exception as exc:
        report.err("Список страниц не читается: %s" % exc)
        return
    if total > MAX_PAGES:
        report.err("Страниц %d, постранично разобраны первые %d." % (total, MAX_PAGES))
    for i in range(min(total, MAX_PAGES)):
        try:
            yield i, pdf.pages[i]
        except Exception as exc:
            report.err("Страница %d не читается: %s" % (i + 1, exc))


def _sec_annots(pdf, report, ctx):
    """Е. Аннотации: /T -- это имя автора комментария."""
    count = 0
    for idx, page in _pages(pdf, report):
        annots = _get(page, "/Annots")
        if annots is None:
            continue
        try:
            items = list(annots)
        except Exception as exc:
            report.err("Стр. %d: /Annots не читается: %s" % (idx + 1, exc))
            continue
        for a in items:
            if count >= MAX_ANNOTS:
                report.err("Аннотаций больше %d, остальные не разобраны." % MAX_ANNOTS)
                return
            count += 1
            try:
                _one_annot(a, idx, report, ctx)
            except Exception as exc:
                report.err("Стр. %d: аннотация не читается: %s" % (idx + 1, exc))
    ctx["annots"] = count


def _one_annot(a, page_idx, report, ctx):
    where = "стр. %d /Annots" % (page_idx + 1)
    sub = _s(_get(a, "/Subtype"))
    author = _s(_get(a, "/T"))
    if author:
        report.add(Risk.IDENTITY, where, "Автор аннотации %s /T" % sub, author,
                   note="Имя, под которым человек залогинен в Acrobat/Reader. Обычно это "
                        "настоящее ФИО или доменная учётка, и её никто не чистит.")
    for key, label, risk, note in (
        ("/M", "Дата изменения /M", Risk.PROVENANCE, "Когда комментарий правили в последний раз."),
        ("/CreationDate", "Дата создания", Risk.PROVENANCE, ""),
        ("/Contents", "Текст комментария", Risk.IDENTITY,
         "Тело комментария целиком: рабочая переписка, которую не видно при обычном просмотре."),
        ("/RC", "Форматированный текст /RC", Risk.IDENTITY,
         "Дубль комментария в RTF/HTML: остаётся, даже если /Contents почистили."),
        ("/Subj", "Тема", Risk.PROVENANCE, ""),
    ):
        raw = _s(_get(a, key))
        if not raw:
            continue
        if key in ("/M", "/CreationDate"):
            human = _pdf_date(raw)
            raw = "%s   (оригинал: %s)" % (human, raw) if human else raw
        report.add(risk, where, label, raw, note=note)
    if _get(a, "/Popup") is not None:
        report.add(Risk.PROVENANCE, where, "Popup-окно аннотации %s" % sub, "есть",
                   note="Всплывающее окно комментария: отдельный объект, который тащит за собой "
                        "те же /T и /Contents.")
    if sub == "/FileAttachment":
        fs = _get(a, "/FS")
        if fs is not None:
            _one_filespec(fs, "стр. %d /FileAttachment" % (page_idx + 1), report)


def _sec_acroform(pdf, report, ctx):
    """Ж + К. Поля формы, XFA и цифровые подписи."""
    form = _get(pdf.Root, "/AcroForm")
    if form is None:
        return
    report.add(Risk.PROVENANCE, "/AcroForm", "Интерактивная форма", "есть")

    if _get(form, "/NeedAppearances") is not None:
        report.add(Risk.PROVENANCE, "/AcroForm", "/NeedAppearances",
                   _s(_get(form, "/NeedAppearances")),
                   note="Просмотрщику приказано перерисовать поля по их значениям: значения "
                        "полей в файле есть даже там, где визуально пусто.")

    xfa = _get(form, "/XFA")
    if xfa is not None:
        report.add(Risk.IDENTITY, "/AcroForm", "XFA-форма", "есть",
                   note="XFA -- это полная XML-копия формы вместе с введёнными данными. Она живёт "
                        "параллельно обычным полям: даже если /V вычистить, данные останутся в XFA.")
        report.signal("hazard", "В файле XFA-форма: данные дублируются в XML и переживают чистку полей", "high")

    sigflags = _get(form, "/SigFlags")
    if sigflags is not None:
        report.add(Risk.PROVENANCE, "/AcroForm", "/SigFlags", _s(sigflags),
                   note="Флаг наличия подписей в форме.", removable=False)
    if _get(pdf.Root, "/Perms") is not None:
        report.add(Risk.IDENTITY, "/Perms", "Сертифицирующая подпись документа", "есть",
                   note="Документ сертифицирован. ЛЮБАЯ правка метаданных ломает подпись: "
                        "чистка и подпись взаимоисключающи.", removable=False)

    fields = _get(form, "/Fields")
    if fields is None:
        return
    state = {"n": 0, "sig": 0}
    _walk_fields(fields, "", report, state, 0)
    ctx["sig_fields"] = state["sig"]
    if state["sig"]:
        report.signal("hazard",
                      "PDF подписан электронной подписью (%d поле/полей /FT /Sig): чистка "
                      "метаданных ГАРАНТИРОВАННО сделает подпись недействительной" % state["sig"],
                      "high")


def _walk_fields(fields, prefix, report, state, depth):
    if depth > 8:
        return
    try:
        items = list(fields)
    except Exception as exc:
        report.err("/Fields не читается: %s" % exc)
        return
    for f in items:
        if state["n"] >= MAX_FIELDS:
            report.err("Полей формы больше %d, остальные не разобраны." % MAX_FIELDS)
            return
        state["n"] += 1
        try:
            name = _s(_get(f, "/T"))
            full = "%s.%s" % (prefix, name) if prefix and name else (name or prefix)
            ftype = _s(_get(f, "/FT"))
            if ftype == "/Sig":
                if _one_signature(f, full, report):
                    state["sig"] += 1
            else:
                value = _get(f, "/V")
                if value is not None:
                    report.add(Risk.IDENTITY, "/AcroForm /Fields", "Поле «%s» (%s)" % (full, ftype or "?"),
                               _s(value),
                               note="Значение поля формы. Поля, визуально «очищенные» стиранием "
                                    "внешнего вида, обычно сохраняют /V -- данные остаются в файле.")
                dv = _get(f, "/DV")
                if dv is not None:
                    report.add(Risk.PROVENANCE, "/AcroForm /Fields", "Значение по умолчанию «%s»" % full,
                               _s(dv))
            kids = _get(f, "/Kids")
            if kids is not None:
                _walk_fields(kids, full, report, state, depth + 1)
        except Exception as exc:
            report.err("Поле формы не читается: %s" % exc)


def _one_signature(field, name, report):
    """К. Подписант и параметры подписи. Убрать нельзя -- сломается подпись.

    Возвращает True, если поле РЕАЛЬНО подписано (есть /V) -- только такие
    поля считаются в сигнале-предупреждении и получают removable=False.
    Пустое поле-заготовку ломать нечем: убрать можно как обычное поле формы.
    """
    v = _get(field, "/V")
    if v is None:
        report.add(Risk.PROVENANCE, "/AcroForm /Sig", "Поле подписи «%s»" % name,
                   "пустое (подпись не поставлена)",
                   note="Подготовленное, но не подписанное поле: подписи ещё нет, ломать "
                        "нечего, можно убирать как обычное поле формы.", removable=True)
        return False
    note = ("Цифровая подпись. Метаданные подписи входят в подписанный диапазон: любая "
            "чистка файла ломает подпись. Чистка и сохранение подписи -- взаимоисключающие "
            "операции, выбирать должен пользователь.")
    report.add(Risk.IDENTITY, "/AcroForm /Sig", "Поле подписи «%s»" % name, "есть",
               note=note, removable=False)
    for key, label, risk in (
        ("/Name", "Подписант", Risk.IDENTITY),
        ("/M", "Время подписания", Risk.PROVENANCE),
        ("/Reason", "Причина подписания", Risk.IDENTITY),
        ("/Location", "Место подписания", Risk.ENVIRONMENT),
        ("/ContactInfo", "Контакты подписанта", Risk.IDENTITY),
        ("/Filter", "Обработчик подписи", Risk.PROVENANCE),
        ("/SubFilter", "Формат подписи", Risk.PROVENANCE),
    ):
        raw = _s(_get(v, key))
        if not raw:
            continue
        if key == "/M":
            human = _pdf_date(raw)
            raw = "%s   (оригинал: %s)" % (human, raw) if human else raw
        report.add(risk, "/AcroForm /Sig", "%s («%s»)" % (label, name), raw,
                   note=note, removable=False)
    br = _get(v, "/ByteRange")
    if br is not None:
        report.add(Risk.PROVENANCE, "/AcroForm /Sig", "/ByteRange («%s»)" % name, _s(br),
                   note="Диапазон байт, накрытый подписью. Всё, что внутри него, изменить нельзя. "
                        + note, removable=False)
    return True


def _sec_attachments(pdf, report, ctx):
    """З. Вложенные файлы: у PDF/A-3 и е-счетов внутри лежит исходник целиком."""
    names = _get(pdf.Root, "/Names")
    if names is None:
        return
    tree = _get(names, "/EmbeddedFiles")
    if tree is None:
        return
    found = []
    _walk_name_tree(tree, found, report, 0)
    for label, spec in found[:MAX_ATTACH]:
        try:
            _one_filespec(spec, "/Names /EmbeddedFiles", report, label)
        except Exception as exc:
            report.err("Вложение %s не читается: %s" % (label, exc))
    if found:
        report.signal("hazard",
                      "В PDF вложено файлов: %d. Вложение -- это отдельный документ со своими "
                      "метаданными, инспекция PDF его метаданные не вскрывает" % len(found),
                      "high")
    ctx["attachments"] = len(found)


def _walk_name_tree(node, out, report, depth, _seen=None, _budget=None):
    """Обход дерева имён (/EmbeddedFiles, /JavaScript). Глубина ограничена, но
    этого недостаточно: /Kids может много раз ссылаться на ОДИН И ТОТ ЖЕ
    индиректный объект (шаред-ссылки вместо отдельных узлов), и тогда без
    защиты обход раздувается экспоненциально (branching^depth вызовов) на
    файле в несколько объектов -- классическая DAG-бомба. Поэтому вдобавок к
    глубине: не заходим повторно в уже посещённый объект (_seen по objgen) и
    жёстко ограничиваем суммарное число вызовов (_budget), а не только число
    найденных записей."""
    if _seen is None:
        _seen = set()
    if _budget is None:
        _budget = [4000]
    if depth > 8 or len(out) >= MAX_ATTACH or _budget[0] <= 0:
        return
    _budget[0] -= 1
    try:
        key = node.objgen
    except Exception:
        key = None
    if key and key != (0, 0):
        if key in _seen:
            return
        _seen.add(key)
    try:
        arr = _get(node, "/Names")
        if arr is not None:
            items = list(arr)
            for i in range(0, len(items) - 1, 2):
                out.append((_s(items[i]), items[i + 1]))
        kids = _get(node, "/Kids")
        if kids is not None:
            for kid in list(kids)[:64]:
                _walk_name_tree(kid, out, report, depth + 1, _seen, _budget)
    except Exception as exc:
        report.err("Дерево имён вложений не читается: %s" % exc)


def _one_filespec(spec, where, report, label=""):
    fname = _s(_get(spec, "/UF")) or _s(_get(spec, "/F")) or label
    report.add(Risk.IDENTITY, where, "Вложенный файл", fname,
               note="Имя вложения. Само вложение -- полноценный документ со своими метаданными "
                    "(у PDF/A-3 и электронных счетов внутри лежит исходный XML/xlsx целиком).")
    desc = _s(_get(spec, "/Desc"))
    if desc:
        report.add(Risk.PROVENANCE, where, "Описание вложения «%s»" % fname, desc)
    ef = _get(spec, "/EF")
    if ef is None:
        return
    stream = _get(ef, "/F") or _get(ef, "/UF")
    if stream is None:
        return
    params = _get(stream, "/Params")
    if params is None:
        return
    for key, lbl, risk in (("/Size", "Размер вложения", Risk.PROVENANCE),
                           ("/CreationDate", "Дата создания вложения", Risk.PROVENANCE),
                           ("/ModDate", "Дата изменения вложения", Risk.PROVENANCE),
                           ("/CheckSum", "Контрольная сумма вложения", Risk.PROVENANCE)):
        raw = _s(_get(params, key))
        if not raw:
            continue
        if key.endswith("Date"):
            human = _pdf_date(raw)
            raw = "%s   (оригинал: %s)" % (human, raw) if human else raw
        report.add(risk, where, "%s «%s»" % (lbl, fname), raw)


def _sec_active(pdf, report, ctx):
    """И. Активное содержимое: JS, автодействия, запуск программ, внешние ссылки."""
    hazards = 0
    names = _get(pdf.Root, "/Names")
    if names is not None and _get(names, "/JavaScript") is not None:
        js = []
        _walk_name_tree(_get(names, "/JavaScript"), js, report, 0)
        for label, entry in js[:50]:
            body = _get(entry, "/JS")
            report.add(Risk.ENVIRONMENT, "/Names /JavaScript", "Скрипт уровня документа «%s»" % label,
                       _stream_text(body) or "есть",
                       note="JavaScript выполняется при открытии файла. Код часто содержит имена "
                            "полей, адреса серверов и внутренние идентификаторы системы.")
            hazards += 1
        if js:
            report.signal("hazard", "В документе %d JavaScript-скрипт(ов) уровня документа" % len(js), "high")

    if _get(pdf.Root, "/OpenAction") is not None:
        oa = _get(pdf.Root, "/OpenAction")
        report.add(Risk.ENVIRONMENT, "/OpenAction", "Действие при открытии", _s(oa)[:400],
                   note="Что файл делает сам, как только его открыли.")
        hazards += 1
    if _get(pdf.Root, "/AA") is not None:
        report.add(Risk.ENVIRONMENT, "/AA", "Дополнительные действия документа", _s(_get(pdf.Root, "/AA"))[:400],
                   note="Обработчики событий документа (печать, сохранение, закрытие).")
        hazards += 1

    for idx, page in _pages(pdf, report):
        try:
            if _get(page, "/AA") is not None:
                report.add(Risk.ENVIRONMENT, "стр. %d /AA" % (idx + 1), "Действия страницы",
                           _s(_get(page, "/AA"))[:400],
                           note="Скрипты, срабатывающие при открытии/закрытии страницы.")
                hazards += 1
            annots = _get(page, "/Annots")
            if annots is None:
                continue
            for a in list(annots)[:100]:
                act = _get(a, "/A")
                if act is None:
                    continue
                stype = _s(_get(act, "/S"))
                if stype == "/Launch":
                    report.add(Risk.ENVIRONMENT, "стр. %d /Annots /A" % (idx + 1),
                               "Запуск внешней программы /Launch", _s(_get(act, "/F")) or _s(act)[:300],
                               note="Аннотация пытается запустить программу или открыть файл по "
                                    "локальному пути -- путь выдаёт машину автора.")
                    hazards += 1
                elif stype == "/URI":
                    uri = _s(_get(act, "/URI"))
                    risk = Risk.ENVIRONMENT if _looks_like_path(uri) else Risk.PROVENANCE
                    report.add(risk, "стр. %d /Annots /A" % (idx + 1), "Ссылка /URI", uri,
                               note="Внешняя ссылка. file:// и UNC-пути выдают внутреннюю сеть; "
                                    "трекинговые ссылки выдают корпоративные системы.")
                elif stype in ("/JavaScript", "/SubmitForm", "/ImportData"):
                    report.add(Risk.ENVIRONMENT, "стр. %d /Annots /A" % (idx + 1),
                               "Действие %s" % stype, _s(act)[:300],
                               note="Активное действие в аннотации: отправка данных или исполнение кода.")
                    hazards += 1
        except Exception as exc:
            report.err("Стр. %d: активное содержимое не читается: %s" % (idx + 1, exc))

    if hazards:
        report.signal("hazard", "Активное содержимое в PDF: %d объект(ов) (JS/OpenAction/AA/Launch)" % hazards,
                      "high")


def _sec_layers(pdf, report, ctx):
    """Л. Скрытые слои: выключенный слой может содержать невидимый текст."""
    ocp = _get(pdf.Root, "/OCProperties")
    if ocp is None:
        return
    groups = {}
    try:
        for g in list(_get(ocp, "/OCGs") or [])[:MAX_OCGS]:
            try:
                groups[g.objgen] = _s(_get(g, "/Name"))
            except Exception:
                pass
    except Exception as exc:
        report.err("/OCProperties /OCGs не читается: %s" % exc)
    for key, name in groups.items():
        report.add(Risk.PROVENANCE, "/OCProperties", "Слой (optional content)", name or "без имени",
                   note="Именованный слой. Имена слоёв часто выдают внутренние названия версий, "
                        "заказчиков и черновиков («Draft», «Клиент А», «Правки юриста»).")
    d = _get(ocp, "/D")
    if d is None:
        return
    try:
        off = list(_get(d, "/OFF") or [])
    except Exception as exc:
        report.err("/OCProperties /D /OFF не читается: %s" % exc)
        return
    for g in off[:MAX_OCGS]:
        try:
            name = groups.get(g.objgen) or _s(_get(g, "/Name"))
        except Exception:
            name = "без имени"
        report.add(Risk.IDENTITY, "/OCProperties /D /OFF", "ВЫКЛЮЧЕННЫЙ слой", name or "без имени",
                   note="Слой выключен по умолчанию: его содержимое ЕСТЬ в файле, но не отображается. "
                        "Так прячут черновики, комментарии и служебные пометки -- включается одним "
                        "кликом в любом просмотрщике.")
    if off:
        report.signal("hazard", "В PDF %d выключенных слоёв: скрытое содержимое включается одним кликом" % len(off),
                      "high")


def _sec_fonts(pdf, report, ctx):
    """М. Шрифты: сабсеты и гарнитуры, датирующие документ."""
    seen = {}
    for idx, page in _pages(pdf, report):
        try:
            res = _get(page, "/Resources")
            if res is None:
                continue
            fonts = _get(res, "/Font")
            if fonts is None:
                continue
            for fkey in list(fonts.keys())[:100]:
                if len(seen) >= MAX_FONTS:
                    break
                base = _s(_get(fonts[fkey], "/BaseFont"))
                if base:
                    seen.setdefault(base, idx + 1)
        except Exception as exc:
            report.err("Стр. %d: /Resources /Font не читается: %s" % (idx + 1, exc))
    ctx["fonts"] = set(seen)
    if not seen:
        return
    subsets = []
    for base in sorted(seen):
        clean = base.lstrip("/")
        m = _SUBSET.match(clean)
        if m:
            subsets.append(m.group(1))
            report.add(Risk.PROVENANCE, "/Resources /Font", "Шрифт (сабсет)", clean,
                       note="Префикс %s -- метка подмножества глифов. Префиксы генерируются "
                            "детерминированно конкретным продюсером и совпадают у файлов, "
                            "собранных одной установкой." % m.group(1))
        else:
            report.add(Risk.PROVENANCE, "/Resources /Font", "Шрифт", clean)
        low = clean.split("+")[-1].lower()
        for marker, note in _FONT_ERA:
            if low.startswith(marker):
                report.add(Risk.PROVENANCE, "/Resources /Font", "Шрифт, датирующий документ", clean,
                           note=note)
                # kind="dating", а НЕ "producer": шрифт датирует документ, но
                # не является приложением, которым он сделан. С "producer"
                # сводка выдавала «PDF, сделан через Calibri» -- бессмыслица.
                report.signal("dating", note, "medium")
                ctx.setdefault("era_fonts", []).append((marker, clean))
                break
    if subsets:
        report.add(Risk.PROVENANCE, "/Resources /Font", "Префиксы сабсетов",
                   ", ".join(sorted(set(subsets))[:40]),
                   note="Набор префиксов сабсетов -- устойчивый отпечаток связки «продюсер + "
                        "набор шрифтов»: совпадение у двух файлов указывает на общий источник.")


def _sec_pieceinfo(pdf, report, ctx):
    """Н. /PieceInfo -- приватные данные приложения, иногда с исходником внутри."""
    def dump(holder, where):
        pi = _get(holder, "/PieceInfo")
        if pi is None:
            return
        try:
            apps = list(pi.keys())
        except Exception as exc:
            report.err("%s /PieceInfo не читается: %s" % (where, exc))
            return
        for app in apps[:50]:
            entry = _get(pi, app)
            last = _s(_get(entry, "/LastModified")) if entry is not None else ""
            human = _pdf_date(last)
            value = app.lstrip("/")
            if last:
                value += "  (LastModified: %s)" % (human or last)
            report.add(Risk.PROVENANCE, "%s /PieceInfo" % where, "Приватные данные приложения", value,
                       note="Приложение (Acrobat, Distiller, Illustrator, InDesign) хранит тут свои "
                            "служебные данные -- вплоть до редактируемого исходника документа. "
                            "Наличие ключа однозначно называет приложение, которым файл правили.")

    dump(pdf.Root, "/Root")
    for idx, page in _pages(pdf, report):
        try:
            dump(page, "стр. %d" % (idx + 1))
        except Exception as exc:
            report.err("Стр. %d: /PieceInfo не читается: %s" % (idx + 1, exc))


def _sec_structural(pdf, report, ctx):
    """О. Структурное: при чистке обычно СОХРАНЯЕМ."""
    root = pdf.Root
    checks = (
        ("/StructTreeRoot", "Дерево структуры (тегированный PDF)",
         "Теги доступности: без них скринридер не читает документ. При чистке сохраняем."),
        ("/MarkInfo", "/MarkInfo (пометка о тегировании)",
         "Объявление, что PDF тегированный. Сохраняем вместе с /StructTreeRoot."),
        ("/Outlines", "Оглавление (закладки)",
         "Навигация по документу. Сохраняем, но заголовки разделов сами по себе могут "
         "выдавать внутреннюю структуру проекта."),
        ("/PageLabels", "Нумерация страниц (/PageLabels)",
         "Пользовательская нумерация (i, ii, 1, 2...). Сохраняем."),
        ("/ViewerPreferences", "Настройки просмотрщика",
         "Как открывать документ. Безобидно, сохраняем."),
        ("/OutputIntents", "Цветовой профиль (/OutputIntents, ICC)",
         "ICC-профиль печати. Сохраняем: без него ломается цветопередача. Имя профиля при этом "
         "иногда называет типографию."),
    )
    has_struct = False
    for key, label, note in checks:
        if label is None:
            continue
        if _get(root, key) is not None:
            if key == "/StructTreeRoot":
                has_struct = True
            report.add(Risk.STRUCTURAL, "/Root", label, "есть", note=note)
    ctx["struct_tree"] = has_struct

    lang = _s(_get(root, "/Lang"))
    if lang:
        report.add(Risk.STRUCTURAL, "/Root", "Язык документа /Lang", lang,
                   note="Язык нужен скринридерам и переносам -- сохраняем. Но сам по себе он "
                        "выдаёт языковые настройки машины автора (ru-RU, en-GB).")

    try:
        page = pdf.pages[0]
        box = _get(page, "/MediaBox")
        if box is not None:
            pts = [float(v) for v in list(box)]
            w = abs(pts[2] - pts[0]) * 25.4 / 72.0
            h = abs(pts[3] - pts[1]) * 25.4 / 72.0
            name = ""
            for pw, ph, pname in _PAPER:
                if (abs(w - pw) < 3 and abs(h - ph) < 3) or (abs(w - ph) < 3 and abs(h - pw) < 3):
                    name = pname
                    break
            report.add(Risk.STRUCTURAL, "стр. 1 /MediaBox", "Формат страницы",
                       "%.1f x %.1f мм%s" % (w, h, "  -> %s" % name if name else ""),
                       note="Формат бумаги выдаёт страну: Letter -- США/Канада, A4 -- всё остальное. "
                            "Это работает, даже если все даты и имена вычищены.")
            if "США" in name:
                # Формат бумаги -- геолокация, не продюсер.
                report.signal("dating", "Формат бумаги %s -- документ готовили под "
                                        "североамериканский стандарт" % name, "medium")
    except Exception as exc:
        report.err("/MediaBox первой страницы не читается: %s" % exc)


def _sec_overview(pdf, report, ctx):
    """П. Общая структура файла."""
    try:
        npages = len(pdf.pages)
    except Exception:
        npages = -1
    try:
        nobj = len(pdf.objects)
    except Exception:
        nobj = -1
    lin = False
    try:
        lin = bool(pdf.is_linearized)
    except Exception:
        lin = ctx.get("raw_linearized", False)
    ctx["linearized"] = lin
    report.add(Risk.PROVENANCE, "структура", "Страниц / объектов",
               "%d / %d" % (npages, nobj),
               note="Число объектов относительно числа страниц -- грубый отпечаток продюсера: "
                    "генераторы дают предсказуемо мало объектов на страницу.")
    report.add(Risk.PROVENANCE, "структура", "Линеаризован (fast web view)",
               "да" if lin else "нет",
               note="Линеаризация -- признак сохранения через Acrobat или явного qpdf --linearize. "
                    "Заодно она выбрасывает все инкрементальные ревизии.")
    try:
        report.add(Risk.PROVENANCE, "структура", "Версия PDF (по разбору)", str(pdf.pdf_version),
                   note="Версия, которую видит QPDF. Расхождение с заголовком %%PDF- означает, что "
                        "файл переписывали.")
        if ctx.get("header_version") and str(pdf.pdf_version) != ctx["header_version"]:
            report.signal("inconsistent",
                          "Версия в заголовке (%s) не совпадает с версией по разбору (%s): файл "
                          "переписывали сторонним инструментом" % (ctx["header_version"], pdf.pdf_version),
                          "medium")
    except Exception as exc:
        report.err("Версия PDF не читается: %s" % exc)


def _sec_signals(pdf, report, ctx):
    """Р. Выводы: чем сделан файл и где метаданные противоречат сами себе."""
    info = ctx.get("info", {})
    xmp = ctx.get("xmp", {})
    creator = info.get("/Creator", "") or xmp.get("xmp:CreatorTool", "")
    producer = info.get("/Producer", "") or xmp.get("pdf:Producer", "")
    blob = "%s | %s" % (creator, producer)

    named = []
    for rx, human, is_lib in _TOOL_MARKS:
        m = rx.search(blob)
        if not m:
            continue
        detail = human
        if "Skia" in rx.pattern:
            ms = re.search(r"Skia/PDF\s*m(\d+)", blob, re.I)
            if ms:
                detail += " -- майлстоун Chrome %s" % ms.group(1)
        named.append((detail, is_lib))
        report.signal("producer", "%s (Creator=%r, Producer=%r)" % (detail, creator, producer), "high")
        if is_lib:
            report.signal("ai", "Файл сгенерирован программно, а не «напечатан» человеком из "
                                "редактора: %s. Для документа, который выдают за офисный, это "
                                "само по себе аномалия." % detail, "high")

    if not named and (creator or producer):
        report.signal("producer", "Продюсер не опознан по базе отпечатков: Creator=%r, Producer=%r"
                      % (creator, producer), "low")
    if not creator and not producer:
        report.signal("scrubbed", "Ни /Creator, ни /Producer не заполнены: так не делает ни один "
                                  "известный продюсер -- файл почти наверняка уже чистили", "high")

    # Постобработка поверх офисного приложения.
    if producer and _REWRITER.search(producer) and creator and _AUTHORING.search(creator):
        report.signal("scrubbed",
                      "Creator заявляет авторское приложение (%r), а Producer -- служебный "
                      "переписыватель (%r): файл уже прогоняли через постобработку" % (creator, producer),
                      "high")

    # Word без XMP и без тегов -- так Word не экспортирует.
    word = bool(re.search(r"Microsoft|Word", creator or "", re.I))
    if word and not ctx.get("xmp_present"):
        report.signal("scrubbed",
                      "Creator = Microsoft Word, но XMP в файле нет. Экспорт Word всегда пишет "
                      "XMP-пакет -- значит, метаданные уже вычищали", "high")
    if word and not ctx.get("struct_tree"):
        report.signal("scrubbed",
                      "Creator = Microsoft Word, но нет /StructTreeRoot. Word всегда тегирует "
                      "экспортируемый PDF -- файл переписывали сторонним инструментом", "medium")

    # /Info и XMP должны существовать вместе.
    if ctx.get("xmp_present") and not ctx.get("info_present"):
        report.signal("scrubbed", "/Info пуст, а XMP на месте: чистили только словарь /Info, "
                                  "а XMP забыли -- там всё осталось", "high")
    if ctx.get("info_present") and not ctx.get("xmp_present"):
        report.signal("inconsistent", "/Info заполнен, а XMP отсутствует: либо очень старый "
                                      "продюсер, либо XMP вырезали отдельно", "medium")

    # Даты.
    c_date = info.get("/CreationDate", "")
    m_date = info.get("/ModDate", "")
    if c_date and m_date:
        if c_date == m_date:
            report.signal("inconsistent",
                          "CreationDate == ModDate (%s): файл не редактировали после создания -- "
                          "либо это одноразовая машинная генерация, либо даты переписали скопом"
                          % (_pdf_date(c_date) or c_date), "low")
        elif m_date < c_date:
            report.signal("inconsistent",
                          "ModDate (%s) РАНЬШЕ CreationDate (%s): даты правили вручную"
                          % (_pdf_date(m_date) or m_date, _pdf_date(c_date) or c_date), "high")
    x_create = xmp.get("xmp:CreateDate", "")
    if c_date and x_create and _pdf_date(c_date)[:10] and x_create[:10] != _pdf_date(c_date)[:10]:
        report.signal("inconsistent",
                      "Дата создания в /Info (%s) не совпадает с xmp:CreateDate (%s): один из "
                      "источников правили" % (_pdf_date(c_date), x_create), "high")

    # Шрифт против заявленной даты: Aptos физически не мог попасть в документ
    # раньше 2023 года, каким бы старым файл себя ни объявлял.
    year = ""
    m_year = re.match(r"D?:?(\d{4})", c_date or "")
    if m_year:
        year = m_year.group(1)
    for marker, name in ctx.get("era_fonts", []):
        if marker.startswith("aptos") and year and year < "2023":
            report.signal("inconsistent",
                          "Заявленная дата создания %s противоречит шрифту %s: Aptos появился "
                          "только в 2023 году -- дату переписали" % (year, name), "high")
        if marker == "calibri" and year and year < "2007":
            report.signal("inconsistent",
                          "Заявленная дата создания %s противоречит шрифту %s: Calibri появился "
                          "только в 2007 году -- дату переписали" % (year, name), "high")

    # Автор в одном месте есть, в другом нет.
    if info.get("/Author") and not xmp.get("dc:creator"):
        report.signal("inconsistent", "Автор есть в /Info, но dc:creator в XMP пуст", "low")
    if xmp.get("dc:creator") and not info.get("/Author"):
        report.signal("scrubbed", "dc:creator в XMP заполнен (%s), а /Author в /Info вычищен: "
                                  "классическая половинчатая чистка" % xmp["dc:creator"], "high")


def _enrich_exiftool(path, report, ctx):
    """Необязательное обогащение. Нет exiftool -- молча идём дальше."""
    exe = shutil.which("exiftool")
    if not exe:
        local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "tools", "exiftool.exe")
        exe = local if os.path.isfile(local) else None
    if not exe:
        return
    try:
        proc = subprocess.run([exe, "-json", "-a", "-G1", "-s", "-n", str(path)],
                              capture_output=True, timeout=30, shell=False)
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except Exception as exc:
        report.err("exiftool не отработал (не критично): %s" % exc)
        return
    if not data:
        return
    known = {k.split(":")[-1].lower() for k in ctx.get("xmp", {})}
    known |= {k.lstrip("/").lower() for k in ctx.get("info", {})}
    added = 0
    for group_key, value in data[0].items():
        if added >= 120:
            break
        tag = group_key.split(":")[-1]
        if tag in ("SourceFile", "ExifToolVersion", "FileName", "Directory",
                   "FileSize", "FileModifyDate", "FileAccessDate", "FileInodeChangeDate",
                   "FilePermissions", "FileType", "FileTypeExtension", "MIMEType"):
            continue
        if tag.lower() in known:
            continue
        added += 1
        report.add_nonempty(Risk.PROVENANCE, "exiftool", tag, _s(value),
                            note="Дополнительно найдено exiftool (необязательное обогащение).")
    if added:
        report.signal("producer", "exiftool добавил %d полей, не покрытых разбором pikepdf" % added, "low")
