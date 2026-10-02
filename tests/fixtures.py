# -*- coding: utf-8 -*-
"""Синтетические файлы с ЗАРАНЕЕ ИЗВЕСТНЫМИ метаданными для self-теста инспекторов.

Каждая функция make_*(dir) кладёт файл в каталог dir и возвращает
(путь, expected) -- словарь expected описывает, что должен найти инспектор,
чтобы test_inspect.py мог сверить факт разбора с тем, что на самом деле лежит
в файле, а не поверить на слово самому инспектору.

Формат словаря expected (поля опциональны, кладём только то, что относится
к конкретной фикстуре):
    values       -- подстроки, которые обязаны встретиться хоть в одной
                    Finding.value отчёта (сам факт, что значение извлечено).
    identity     -- подстроки, которые обязаны встретиться в значении
                    находки именно с риском IDENTITY.
    environment  -- то же для риска ENVIRONMENT.
    signals      -- kind-ы Signal, которые обязаны появиться хоть раз.
    not_signals  -- kind-ы, которых быть НЕ должно (защита от ложных срабатываний).
    gps          -- (широта, долгота) десятичные, если в файле заложены GPS.

Только у OLE2-фикстур (make_doc / make_doc_active / make_xls / make_ppt):
    ole_meta           -- что обязан вернуть olefile.get_metadata(): строки
                          bytes в cp1251, даты datetime, total_edit_time в секундах.
    streams            -- имена потоков верхнего уровня.
    userdef            -- пользовательские свойства {имя: значение}, все IDENTITY.
    unremovable_labels -- label находок, которые обязаны быть removable=False.
    root_mtime         -- значение находки "Время изменения контейнера".

Только stdlib + pikepdf (тот же набор зависимостей, что у самих инспекторов).
Все строки-находки -- по-русски, чтобы фикстуры естественно проверяли и
кириллицу, и экранирование HTML при рендере.
"""

from __future__ import annotations

import binascii
import datetime
import os
import re
import struct
import uuid
import zipfile
import zlib

import pikepdf


# ===========================================================================
# мелкие помощники
# ===========================================================================


def _write_zip(path, parts, date_time):
    """parts -- список (имя, содержимое). Содержимое str кодируется в utf-8."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts:
            if isinstance(body, str):
                body = body.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)


def _png_chunk(ctype, body):
    return (struct.pack(">I", len(body)) + ctype + body
            + struct.pack(">I", binascii.crc32(ctype + body) & 0xFFFFFFFF))


def _tiff_ifd(tags, base, next_off=0):
    """Собрать один IFD. tags -- [(tag, type, count, raw_bytes), ...].

    Значения длиннее 4 байт уезжают в область данных сразу за самим IFD;
    base -- абсолютное смещение начала этого IFD в TIFF-потоке.
    """
    ifd_len = 2 + 12 * len(tags) + 4
    body, blob = struct.pack("<H", len(tags)), b""
    for tag, typ, count, raw in tags:
        if len(raw) <= 4:
            val = raw + b"\x00" * (4 - len(raw))
        else:
            val = struct.pack("<I", base + ifd_len + len(blob))
            blob += raw + (b"\x00" if len(raw) % 2 else b"")
        body += struct.pack("<HHI", tag, typ, count) + val
    return body + struct.pack("<I", next_off) + blob


def _tiff_rational(*pairs):
    return b"".join(struct.pack("<II", n, d) for n, d in pairs)


def _pad(blob):
    return len(blob) + (len(blob) % 2)


def _pdf_text_literal(text):
    """Строка PDF: ASCII -- как "(...)" литерал, иначе -- hex-строка
    "<FEFF...>" в UTF-16BE с BOM (это и пишет pikepdf для не-latin1 текста,
    см. docinfo round-trip в make_pdf)."""
    try:
        raw = text.encode("ascii")
        esc = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        return "(" + esc.decode("ascii") + ")"
    except UnicodeEncodeError:
        raw = b"\xfe\xff" + text.encode("utf-16-be")
        return "<" + raw.hex() + ">"


# ===========================================================================
# OOXML: docx
# ===========================================================================

DOCX_CREATOR = "Тест Автор"
DOCX_LAST_MODIFIED_BY = "Второй Человек"
DOCX_COMPANY = "ООО Тест"
DOCX_MSIP_EMAIL = "leaker@example.com"
DOCX_TEMPLATE_PATH = "file:///C:/Users/testuser/\u0448\u0430\u0431\u043b\u043e\u043d\u044b/t.dotx"
DOCX_PRESENCE_NAME = "Leak Person"
DOCX_PRESENCE_EMAIL = "leak@example.com"
DOCX_XSS_TITLE = "<script>&"

_CT_DOCX = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    '<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
    '</Types>'
)

_RELS_ROOT_DOCX = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>'
    '</Relationships>'
)


def make_docx(dir):
    """.docx с реальными (не 1980-01-01) таймстемпами ZIP -- должен триггерить
    сигнал о библиотечной генерации ("ai"/"inconsistent"), и набором классических
    утечек: email в MSIP_Label, ФИО в Company/core.xml, абсолютный путь к
    шаблону, email в w15:presenceInfo, XSS-подобное значение в dc:title.
    """
    path = os.path.join(dir, "leaky.docx")

    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:creator>%s</dc:creator>'
        '<cp:lastModifiedBy>%s</cp:lastModifiedBy>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">2024-04-10T08:15:00Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2024-04-11T09:20:00Z</dcterms:modified>'
        '<cp:revision>3</cp:revision>'
        '<dc:title>%s</dc:title>'
        '</cp:coreProperties>'
    ) % (DOCX_CREATOR, DOCX_LAST_MODIFIED_BY,
         DOCX_XSS_TITLE.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        '<Application>Microsoft Office Word</Application>'
        '<AppVersion>16.0000</AppVersion>'
        '<Company>%s</Company>'
        '<TotalTime>0</TotalTime>'
        '<Words>50</Words>'
        '</Properties>'
    ) % DOCX_COMPANY

    custom_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="MSIP_Label_a1b2_SetBy">'
        '<vt:lpwstr>%s</vt:lpwstr></property>'
        '</Properties>'
    ) % DOCX_MSIP_EMAIL

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>\u0442\u0435\u043a\u0441\u0442 \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430.</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )

    settings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:attachedTemplate r:id="rId1"/></w:settings>'
    )

    settings_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
        'Target="%s" TargetMode="External"/></Relationships>'
    ) % DOCX_TEMPLATE_PATH

    people_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
        '<w15:person w15:author="%s">'
        '<w15:presenceInfo w15:providerId="AD" w15:userId="%s"/>'
        '</w15:person></w15:people>'
    ) % (DOCX_PRESENCE_NAME, DOCX_PRESENCE_EMAIL)

    parts = [
        ("[Content_Types].xml", _CT_DOCX),
        ("_rels/.rels", _RELS_ROOT_DOCX),
        ("docProps/core.xml", core_xml),
        ("docProps/app.xml", app_xml),
        ("docProps/custom.xml", custom_xml),
        ("word/document.xml", document_xml),
        ("word/settings.xml", settings_xml),
        ("word/_rels/settings.xml.rels", settings_rels),
        ("word/people.xml", people_xml),
    ]
    # Реальное время (не 1980-01-01) -- именно это выдаёт пересборку библиотекой.
    now = datetime.datetime.now().timetuple()[:6]
    _write_zip(path, parts, now)

    expected = {
        "values": [DOCX_CREATOR, DOCX_LAST_MODIFIED_BY, DOCX_COMPANY,
                   DOCX_MSIP_EMAIL, DOCX_TEMPLATE_PATH, DOCX_PRESENCE_EMAIL,
                   DOCX_XSS_TITLE],
        "identity": [DOCX_CREATOR, DOCX_LAST_MODIFIED_BY, DOCX_COMPANY,
                     DOCX_MSIP_EMAIL, DOCX_PRESENCE_EMAIL],
        "environment": [DOCX_TEMPLATE_PATH],
        "signals": ["ai", "inconsistent"],
        "xss_value": DOCX_XSS_TITLE,
    }
    return path, expected


def make_docx_wordlike(dir):
    """.docx с таймстемпами 1980-01-01 и ПОЛНЫМ набором частей Word (+ rsid) --
    контроль того, что сигнал "ai" НЕ срабатывает ложно на обычном файле.
    """
    path = os.path.join(dir, "wordlike.docx")

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
        '<dc:creator>\u041e\u0431\u044b\u0447\u043d\u044b\u0439 \u0410\u0432\u0442\u043e\u0440</dc:creator>'
        '<cp:revision>1</cp:revision>'
        '</cp:coreProperties>'
    )
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        '<Application>Microsoft Office Word</Application><AppVersion>16.0000</AppVersion>'
        '</Properties>'
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p w:rsidR="00A12B34"><w:r><w:t>\u043e\u0431\u044b\u0447\u043d\u044b\u0439 \u0442\u0435\u043a\u0441\u0442.</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    settings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rsids><w:rsidRoot w:val="00A12B34"/><w:rsid w:val="00A12B34"/></w:rsids></w:settings>'
    )

    parts = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", rels),
        ("docProps/core.xml", core_xml),
        ("docProps/app.xml", app_xml),
        ("word/document.xml", document_xml),
        ("word/settings.xml", settings_xml),
        ("word/fontTable.xml", '<?xml version="1.0"?><x/>'),
        ("word/webSettings.xml", '<?xml version="1.0"?><x/>'),
        ("word/styles.xml", '<?xml version="1.0"?><x/>'),
        ("word/theme/theme1.xml", '<?xml version="1.0"?><x/>'),
    ]
    _write_zip(path, parts, (1980, 1, 1, 0, 0, 0))

    expected = {
        "not_signals": ["ai", "inconsistent"],
    }
    return path, expected


# ===========================================================================
# OOXML: xlsx
# ===========================================================================

XLSX_RUPBUILD = "24729"
XLSX_HIDDEN_SHEET = "\u0421\u043a\u0440\u044b\u0442\u044b\u0439_\u041b\u0438\u0441\u0442"
XLSX_PIVOT_USER = "\u0418\u0432\u0430\u043d \u041f\u0435\u0442\u0440\u043e\u0432"
XLSX_CONN_STR = "SERVER=sql01.corp.local;DATABASE=erp;UID=ivanov"


def make_xlsx(dir):
    """.xlsx: fileVersion с rupBuild, скрытый лист, pivotCache.refreshedBy и
    строка подключения в connections.xml."""
    path = os.path.join(dir, "book.xlsx")

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<fileVersion appName="xl" lastEdited="7" lowestEdited="6" rupBuild="%s"/>'
        '<sheets>'
        '<sheet name="\u041b\u0438\u0441\u04421" sheetId="1" r:id="rId2"/>'
        '<sheet name="%s" sheetId="2" state="hidden" r:id="rId3"/>'
        '</sheets></workbook>'
    ) % (XLSX_RUPBUILD, XLSX_HIDDEN_SHEET)
    pivot_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'r:id="rId1" refreshedBy="%s" refreshedDate="45000.5" createdVersion="6"/>'
    ) % XLSX_PIVOT_USER
    connections_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<connections xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<connection id="1" name="ERP" description="ERP connection">'
        '<dbPr connection="DRIVER=SQL Server;%s" command="SELECT * FROM dbo.Orders"/>'
        '</connection></connections>'
    ) % XLSX_CONN_STR

    parts = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", rels),
        ("xl/workbook.xml", workbook_xml),
        ("xl/pivotCache/pivotCacheDefinition1.xml", pivot_xml),
        ("xl/connections.xml", connections_xml),
    ]
    _write_zip(path, parts, (1980, 1, 1, 0, 0, 0))

    expected = {
        "values": [XLSX_RUPBUILD, XLSX_HIDDEN_SHEET, XLSX_PIVOT_USER, XLSX_CONN_STR],
        "identity": [XLSX_PIVOT_USER],
        "environment": [XLSX_CONN_STR],
    }
    return path, expected


# ===========================================================================
# OOXML: pptx
# ===========================================================================

PPTX_CREATOR = "\u0421\u0432\u0435\u0442\u043b\u0430\u043d\u0430 \u041e\u0440\u043b\u043e\u0432\u0430"
PPTX_LASTBY = "s.orlova@vector-ao.ru"
PPTX_COMPANY = "\u0410\u041e \u0412\u0435\u043a\u0442\u043e\u0440"
PPTX_CM_AUTHOR = "\u0413\u0440\u043e\u043c\u043e\u0432 \u0414.\u0410."
# Имя принтера из DEVMODE: 32 WCHAR в UTF-16LE в начале структуры. Здесь оно
# несёт номер кабинета -- то, из-за чего это поле и попало в METADATA.md.
PPTX_PRINTER = "Xerox WC7845 (KAB-312)"


def _devmode(device_name):
    """Минимальный правдоподобный DEVMODEW: dmDeviceName -- 32 WCHAR в UTF-16LE
    с NUL-добивкой, дальше хвост фиксированных полей (драйвер, лоток, формат).
    Инспектор читает первые 64 байта, чистильщик выбрасывает часть целиком."""
    blob = device_name.encode("utf-16le")
    if len(blob) > 64:
        raise ValueError("dmDeviceName длиннее 32 WCHAR")
    return blob + b"\x00" * (64 - len(blob)) + b"\x00" * 156


def make_pptx(dir):
    """.pptx: автор и email в core.xml, Company и TitlesOfParts в app.xml,
    автор комментария в ppt/commentAuthors.xml, ИМЯ ПРИНТЕРА в DEVMODE и превью.

    Единственная фикстура с printerSettings*.bin. До её появления проверки вида
    «имя принтера не осталось» проходили ВАКУУМНО: ни в одном docx/xlsx этой
    части не было. Таймстемпы 1980-01-01 и Application=PowerPoint согласованы
    между собой -- это пакет, похожий на настоящий, а не на библиотечный.
    """
    path = os.path.join(dir, "deck.pptx")

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument.presentationml.printerSettings"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="docProps/thumbnail.jpeg"/>'
        '</Relationships>'
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
        '<dc:creator>%s</dc:creator>'
        '<cp:lastModifiedBy>%s</cp:lastModifiedBy>'
        '<cp:revision>7</cp:revision>'
        '<dcterms:created xsi:type="dcterms:W3CDTF" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">2026-03-02T09:14:00Z</dcterms:created>'
        '</cp:coreProperties>'
    ) % (PPTX_CREATOR, PPTX_LASTBY)
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        '<Application>Microsoft Office PowerPoint</Application><AppVersion>16.0000</AppVersion>'
        '<Company>%s</Company><TotalTime>412</TotalTime>'
        '<TitlesOfParts>\u0421\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u044f \u0434\u043e \u043a\u043e\u043d\u0446\u0430 \u0433\u043e\u0434\u0430</TitlesOfParts>'
        '</Properties>'
    ) % PPTX_COMPANY
    presentation_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst></p:presentation>'
    )
    slide_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld><p:spTree/></p:cSld></p:sld>'
    )
    # ppt/commentAuthors.xml -- площадка IDENTITY, специфичная для PowerPoint:
    # у Word авторы комментариев лежат совсем в других частях.
    comment_authors = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:cmAuthorLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cmAuthor id="1" name="%s" initials="\u0413\u0414" lastIdx="3" clrIdx="0"/>'
        '</p:cmAuthorLst>'
    ) % PPTX_CM_AUTHOR

    parts = [
        ("[Content_Types].xml", content_types),
        ("_rels/.rels", rels),
        ("docProps/core.xml", core_xml),
        ("docProps/app.xml", app_xml),
        ("docProps/thumbnail.jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"),
        ("ppt/presentation.xml", presentation_xml),
        ("ppt/slides/slide1.xml", slide_xml),
        ("ppt/commentAuthors.xml", comment_authors),
        ("ppt/printerSettings/printerSettings1.bin", _devmode(PPTX_PRINTER)),
    ]
    _write_zip(path, parts, (1980, 1, 1, 0, 0, 0))

    expected = {
        "values": [PPTX_CREATOR, PPTX_LASTBY, PPTX_COMPANY, PPTX_CM_AUTHOR, PPTX_PRINTER],
        "identity": [PPTX_CREATOR, PPTX_LASTBY, PPTX_CM_AUTHOR],
        "environment": [PPTX_PRINTER],
        "signals": ["producer"],
        # Пакет согласован сам с собой: таймстемпы 1980-01-01 при
        # Application=PowerPoint, автор заполнен. Ни «собрано библиотекой», ни
        # «уже чистили», ни «метаданные противоречат» здесь взяться не может.
        "not_signals": ["ai", "scrubbed", "inconsistent"],
    }
    return path, expected


# ===========================================================================
# PDF
# ===========================================================================

PDF_AUTHOR = "\u041f\u0435\u0442\u0440\u043e\u0432 \u041f\u0451\u0442\u0440 \u041f\u0435\u0442\u0440\u043e\u0432\u0438\u0447"
PDF_CREATOR = "FixtureGen Writer"
PDF_PRODUCER = "FixtureGen 1.0"
PDF_DOCUMENT_ID = "uuid:12345678-aaaa-bbbb-cccc-1234567890ab"
PDF_CREATION_DATE = "D:20230101000000Z"
PDF_MOD_DATE = "D:20230102000000Z"


def make_pdf(dir):
    """PDF через pikepdf.new(): /Info (Author/Creator/Producer/даты) + XMP
    с постоянным xmpMM:DocumentID."""
    path = os.path.join(dir, "probe.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    # set_pikepdf_as_editor=False -- иначе выход из контекста переписывает
    # /Producer и pdf:Producer сигнатурой самого pikepdf, затирая наш.
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["xmpMM:DocumentID"] = PDF_DOCUMENT_ID
        meta["dc:creator"] = [PDF_AUTHOR]
    pdf.docinfo["/Author"] = PDF_AUTHOR
    pdf.docinfo["/Creator"] = PDF_CREATOR
    pdf.docinfo["/Producer"] = PDF_PRODUCER
    pdf.docinfo["/CreationDate"] = PDF_CREATION_DATE
    pdf.docinfo["/ModDate"] = PDF_MOD_DATE
    pdf.save(path)
    pdf.close()

    expected = {
        "values": [PDF_AUTHOR, PDF_CREATOR, PDF_PRODUCER, PDF_DOCUMENT_ID,
                   PDF_CREATION_DATE, PDF_MOD_DATE],
        "identity": [PDF_AUTHOR, PDF_DOCUMENT_ID],
        "signals": ["producer"],
    }
    return path, expected


PDF_INCR_OLD_AUTHOR = "\u0418\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0410\u0432\u0442\u043e\u0440"
PDF_INCR_NEW_AUTHOR = "\u0421\u043a\u0440\u044b\u0442\u044b\u0439 \u0410\u0432\u0442\u043e\u0440"


def make_pdf_incremental(dir):
    """PDF с инкрементальным апдейтом.

    pikepdf.save() всегда делает полный рерайт (единый свежий xref), поэтому
    настоящий инкрементальный апдейт через него не получить -- библиотека
    просто не умеет дописывать ревизию, только пересохранять целиком.
    Способ: взять валидный PDF, сделанный pikepdf (ревизия 1), и склеить
    вручную байт-в-байт вторую ревизию -- новое тело объекта /Info (с другим
    /Author и /ModDate) + новая xref-секция с /Prev, указывающим на offset
    startxref первой ревизии, + второй startxref/%%EOF. Это ровно то, как
    инкрементальные апдейты устроены в самом PDF (так их пишут Acrobat и
    цифровая подпись), поэтому pikepdf/QPDF успешно открывает результат и
    видит актуальную (вторую) ревизию, а сырой байтовый скан инспектора
    видит обе.
    """
    base_path = os.path.join(dir, "incr_base.pdf")
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.docinfo["/Author"] = PDF_INCR_OLD_AUTHOR
    pdf.docinfo["/Producer"] = PDF_PRODUCER
    pdf.docinfo["/CreationDate"] = PDF_CREATION_DATE
    pdf.save(base_path)
    pdf.close()

    data = open(base_path, "rb").read()
    m = re.search(rb"startxref\s+(\d+)\s*\r?\n%%EOF", data)
    prev_offset = int(m.group(1))

    # /Info лежит в объекте 2 0 obj у pikepdf.new() + add_blank_page() без
    # XMP -- проверено эмпирически на этой версии pikepdf; если раскладка
    # вдруг другая, достанем номер из самого трейлера, не угадывая.
    tm = re.search(rb"/Info\s+(\d+)\s+0\s+R", data)
    info_num = int(tm.group(1))

    new_info = (
        ("%d 0 obj\n<< /Author %s /CreationDate (%s) /ModDate (%s) "
         "/Producer (%s) >>\nendobj\n")
        % (info_num, _pdf_text_literal(PDF_INCR_NEW_AUTHOR),
           PDF_CREATION_DATE, PDF_MOD_DATE, PDF_PRODUCER)
    ).encode("latin-1")
    obj_offset = len(data)
    xref_offset = obj_offset + len(new_info)
    xref_table = (
        ("xref\n%d 1\n%010d 00000 n \n"
         "trailer\n<< /Info %d 0 R /Root 1 0 R /Size %d /Prev %d >>\n"
         "startxref\n%d\n%%%%EOF\n")
        % (info_num, obj_offset, info_num, info_num + 3, prev_offset, xref_offset)
    ).encode("latin-1")

    path = os.path.join(dir, "incremental.pdf")
    with open(path, "wb") as fh:
        fh.write(data + new_info + xref_table)

    expected = {
        "values": [PDF_INCR_NEW_AUTHOR],
        "identity": [PDF_INCR_NEW_AUTHOR],
        "signals": ["hazard"],
    }
    return path, expected


# ===========================================================================
# JPEG
# ===========================================================================

JPEG_ARTIST = "\u0424\u043e\u0442\u043e\u0433\u0440\u0430\u0444 \u0422\u0435\u0441\u0442\u043e\u0432"
JPEG_MAKE = "Canon"
JPEG_MODEL = "EOS R5"
JPEG_SOFTWARE = "Adobe Photoshop 2024"
JPEG_SERIAL = "SN-0001-TEST"
# 59°56'15.00" N, 30°19'23.00" E -> 59.937500 / 30.323056
JPEG_LAT_DEG, JPEG_LAT_MIN, JPEG_LAT_SEC = 59, 56, 15.0
JPEG_LON_DEG, JPEG_LON_MIN, JPEG_LON_SEC = 30, 19, 23.0
JPEG_LAT = JPEG_LAT_DEG + JPEG_LAT_MIN / 60 + JPEG_LAT_SEC / 3600
JPEG_LON = JPEG_LON_DEG + JPEG_LON_MIN / 60 + JPEG_LON_SEC / 3600


def make_jpeg(dir):
    """JPEG: FFD8 + APP1/EXIF (Artist/Make/Model/Software/GPS/BodySerialNumber)
    + минимальные DQT/SOS + FFD9."""
    path = os.path.join(dir, "probe.jpg")

    artist = JPEG_ARTIST.encode("utf-8") + b"\x00"
    make_ = JPEG_MAKE.encode("ascii") + b"\x00"
    model = JPEG_MODEL.encode("ascii") + b"\x00"
    software = JPEG_SOFTWARE.encode("ascii") + b"\x00"
    serial = JPEG_SERIAL.encode("ascii") + b"\x00"

    ifd0_len = 2 + 12 * 6 + 4
    ifd0_blob_len = _pad(artist) + _pad(make_) + _pad(model) + _pad(software)
    exif_base = 8 + ifd0_len + ifd0_blob_len

    exif_ifd_len = 2 + 12 * 1 + 4
    exif_blob_len = _pad(serial)
    gps_base = exif_base + exif_ifd_len + exif_blob_len

    exif_ifd = _tiff_ifd([(0xA431, 2, len(serial), serial)], exif_base)
    gps = _tiff_ifd([
        (0x0001, 2, 2, b"N\x00"),
        (0x0002, 5, 3, _tiff_rational((JPEG_LAT_DEG, 1), (JPEG_LAT_MIN, 1),
                                       (int(JPEG_LAT_SEC * 100), 100))),
        (0x0003, 2, 2, b"E\x00"),
        (0x0004, 5, 3, _tiff_rational((JPEG_LON_DEG, 1), (JPEG_LON_MIN, 1),
                                       (int(JPEG_LON_SEC * 100), 100))),
    ], gps_base)
    ifd0 = _tiff_ifd([
        (0x013B, 2, len(artist), artist),
        (0x010F, 2, len(make_), make_),
        (0x0110, 2, len(model), model),
        (0x0131, 2, len(software), software),
        (0x8769, 4, 1, struct.pack("<I", exif_base)),
        (0x8825, 4, 1, struct.pack("<I", gps_base)),
    ], 8)

    tiff = b"II" + struct.pack("<HI", 42, 8) + ifd0 + exif_ifd + gps
    app1 = b"Exif\x00\x00" + tiff
    jpeg = (b"\xff\xd8"
            + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
            + b"\xff\xdb" + struct.pack(">H", 2 + 65) + b"\x00" + bytes(range(1, 65))
            + b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x00\x00\x3f\x00\x00"
            + b"\x00" * 8 + b"\xff\xd9")

    with open(path, "wb") as fh:
        fh.write(jpeg)

    expected = {
        "values": [JPEG_ARTIST, JPEG_MAKE, JPEG_MODEL, JPEG_SOFTWARE, JPEG_SERIAL],
        "identity": [JPEG_ARTIST, JPEG_SERIAL],
        "gps": (JPEG_LAT, JPEG_LON),
        "signals": ["hazard"],
    }
    return path, expected


# ===========================================================================
# PNG
# ===========================================================================

PNG_SOFTWARE = "TestSuite PNG Writer 1.0"
PNG_XMP_CREATOR = "PNG Test Author"
PNG_TIME = (2024, 6, 15, 10, 30, 0)


def make_png(dir):
    """PNG: IHDR + tEXt(Software) + iTXt(сжатый XMP) + tIME + IDAT + IEND."""
    path = os.path.join(dir, "probe.png")

    xmp_packet = (
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        b'<rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" '
        b'dc:creator="' + PNG_XMP_CREATOR.encode("utf-8") + b'"/>'
        b'</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )
    itxt_body = b"XML:com.adobe.xmp\x00\x01\x00\x00\x00" + zlib.compress(xmp_packet)

    png = (b"\x89PNG\r\n\x1a\n"
           + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + _png_chunk(b"tEXt", b"Software\x00" + PNG_SOFTWARE.encode("latin-1"))
           + _png_chunk(b"iTXt", itxt_body)
           + _png_chunk(b"tIME", struct.pack(">HBBBBB", *PNG_TIME))
           + _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
           + _png_chunk(b"IEND", b""))

    with open(path, "wb") as fh:
        fh.write(png)

    expected = {
        "values": [PNG_SOFTWARE, PNG_XMP_CREATOR],
        "identity": [PNG_XMP_CREATOR],
        "signals": ["producer"],
    }
    return path, expected


# ===========================================================================
# RTF
# ===========================================================================

RTF_AUTHOR = "\u0421\u0435\u0440\u0433\u0435\u0435\u0432 \u0421\u0435\u0440\u0433\u0435\u0439"
RTF_OPERATOR = "\u041e\u0440\u043b\u043e\u0432\u0430 \u041e\u043b\u044c\u0433\u0430"
RTF_COMPANY = "Test Company LLC"
RTF_GENERATOR = "TestRTF Writer 1.0"
RTF_TEMPLATE_PATH = r"C:\Users\testov\AppData\Roaming\Microsoft\Templates\Normal.dotm"
RTF_LINK_PATH = "file:///C:/fixtures/secret_plan.xlsx"


def _rtf_cp1251_escape(text):
    """Кириллица как \\'XX-экранирование cp1251 -- так реально пишет RTF
    редактор с \\ansicpg1251, а не UTF-8."""
    raw = text.encode("cp1251")
    return "".join("\\'%02x" % b for b in raw)


def make_rtf(dir):
    """RTF с известными значениями в группе \\info + \\generator + \\template
    + абсолютные пути в теле."""
    path = os.path.join(dir, "probe.rtf")

    author_esc = _rtf_cp1251_escape(RTF_AUTHOR)
    operator_esc = _rtf_cp1251_escape(RTF_OPERATOR)
    template_esc = RTF_TEMPLATE_PATH.replace("\\", "\\\\")

    rtf = (
        r"{\rtf1\ansi\ansicpg1251\deflang1049"
        r"{\*\generator " + RTF_GENERATOR + r";}"
        r"{\info"
        r"{\author " + author_esc + r"}"
        r"{\operator " + operator_esc + r"}"
        r"{\company " + RTF_COMPANY + r"}"
        r"{\creatim\yr2024\mo7\dy12\hr11\min5}"
        r"{\revtim\yr2024\mo7\dy13\hr8\min30}"
        r"\edmins42\nofpages2\nofwords300\nofchars1800\version3"
        r"}"
        r"{\*\rsidtbl \rsid1112223\rsid4445556}"
        r"{\*\template " + template_esc + r"}"
        r"\par Link to " + RTF_LINK_PATH +
        r"\par}"
    )
    with open(path, "w", encoding="latin-1") as fh:
        fh.write(rtf)

    expected = {
        "values": [RTF_AUTHOR, RTF_OPERATOR, RTF_COMPANY, RTF_GENERATOR,
                   RTF_TEMPLATE_PATH, RTF_LINK_PATH],
        "identity": [RTF_AUTHOR, RTF_OPERATOR, RTF_COMPANY],
        "environment": [RTF_TEMPLATE_PATH, RTF_LINK_PATH],
    }
    return path, expected


# ===========================================================================
# ODT
# ===========================================================================

ODT_INITIAL_CREATOR = "\u0421\u043c\u0438\u0440\u043d\u043e\u0432 \u0410\u043d\u0442\u043e\u043d"
ODT_CREATOR = "\u041a\u0443\u0437\u043d\u0435\u0446\u043e\u0432 \u041a\u0443\u0437\u044c\u043c\u0430"
ODT_GENERATOR = "LibreOffice/7.6.4.1$Linux_X86_64 LibreOffice_project/60$Build-2"
ODT_EDITING_DURATION = "PT1H30M0S"


def make_odt(dir):
    """ODT (ZIP с mimetype + meta.xml) с известными значениями в meta.xml."""
    path = os.path.join(dir, "probe.odt")

    meta_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-meta '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><office:meta>'
        '<meta:generator>%s</meta:generator>'
        '<meta:initial-creator>%s</meta:initial-creator>'
        '<dc:creator>%s</dc:creator>'
        '<meta:creation-date>2024-08-01T10:00:00</meta:creation-date>'
        '<dc:date>2024-08-02T11:15:00</dc:date>'
        '<meta:editing-duration>%s</meta:editing-duration>'
        '<dc:title>Test ODT Fixture</dc:title>'
        '</office:meta></office:document-meta>'
    ) % (ODT_GENERATOR, ODT_INITIAL_CREATOR, ODT_CREATOR, ODT_EDITING_DURATION)

    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        '<office:body><office:text><text:p>probe</text:p></office:text></office:body>'
        '</office:document-content>'
    )
    manifest_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype", (1980, 1, 1, 0, 0, 0)),
                    "application/vnd.oasis.opendocument.text", zipfile.ZIP_STORED)
        for name, blob in (("meta.xml", meta_xml), ("content.xml", content_xml),
                           ("META-INF/manifest.xml", manifest_xml)):
            zf.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), blob)

    expected = {
        "values": [ODT_GENERATOR, ODT_INITIAL_CREATOR, ODT_CREATOR],
        "identity": [ODT_INITIAL_CREATOR, ODT_CREATOR],
    }
    return path, expected


# ===========================================================================
# OLE2 / CFBF: легаси .doc и .xls
# ===========================================================================
#
# Контейнер собирается побайтово по MS-CFB (версия 3: сектор 512 байт,
# мини-сектор 64, порог мини-потока 4096), свойства -- по MS-OLEPS. Писателя
# CFBF в окружении нет (olefile умеет только читать), поэтому формат весь
# здесь, руками. Смысл именно в том, чтобы olefile и inspect_ole() прошли
# НАСТОЯЩИЙ путь разбора -- FAT, мини-FAT, дерево директории, property set --
# а не заглушку, "достаточно похожую" на контейнер.

_CFB_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_CFB_ENDOFCHAIN = 0xFFFFFFFE
_CFB_FREESECT = 0xFFFFFFFF
_CFB_FATSECT = 0xFFFFFFFD
_CFB_NOSTREAM = 0xFFFFFFFF
_CFB_MINI_CUTOFF = 4096

_FMTID_SUMMARY = "F29F85E0-4FF9-1068-AB91-08002B27B3D9"
_FMTID_DOCSUM = "D5CDD502-2E9C-101B-9397-08002B2CF9AE"
_FMTID_USERDEF = "D5CDD505-2E9C-101B-9397-08002B2CF9AE"
_CLSID_WORD8 = "00020906-0000-0000-C000-000000000046"
_CLSID_EXCEL8 = "00020820-0000-0000-C000-000000000046"


def _cfb_entry(name, kind, color, left, right, child, clsid=b"\x00" * 16,
               start=0, size=0, mtime=0):
    """Одна 128-байтная запись директории. name -- str, кодируется UTF-16LE."""
    raw = name.encode("utf-16-le")
    assert len(raw) <= 62, name
    return (raw.ljust(64, b"\x00")
            + struct.pack("<HBBIII", len(raw) + 2 if raw else 0, kind, color,
                          left, right, child)
            + clsid + struct.pack("<IQQ", 0, 0, mtime)
            + struct.pack("<IQ", start, size))


def _cfb_tree(items):
    """Сбалансированное дерево детей одного хранилища по порядку CFBF: сначала
    длина имени, затем имя в верхнем регистре. items -- [(номер записи,
    имя)]. Возвращает (номер корня поддерева, {номер: (left, right, color)}).

    Цвет: всё чёрное, кроме неполного нижнего уровня -- он красный. Так
    красно-чёрные инварианты (нет двух красных подряд, равная чёрная высота)
    держатся без настоящей балансировки.
    """
    order = sorted(items, key=lambda it: (len(it[1]), it[1].upper()))
    links, depth = {}, {}

    def build(lo, hi, d):
        if lo >= hi:
            return _CFB_NOSTREAM
        mid = (lo + hi) // 2
        sid = order[mid][0]
        depth[sid] = d
        links[sid] = [build(lo, mid, d + 1), build(mid + 1, hi, d + 1)]
        return sid

    root = build(0, len(order), 0)
    if not depth:
        return root, {}
    deepest = max(depth.values())
    full = len(order) == 2 ** (deepest + 1) - 1
    return root, {sid: (l, r, 0 if depth[sid] == deepest and not full else 1)
                  for sid, (l, r) in links.items()}


def _cfb_build(streams, root_clsid=b"\x00" * 16, root_mtime=0):
    """Собрать CFBF v3 из списка потоков [(путь, bytes)]. Путь -- имя потока в
    корне (str) либо кортеж ("Хранилище", ..., "поток"); хранилища создаются
    по префиксам путей.

    Потоки короче 4096 байт уезжают в мини-поток (мини-FAT + контейнер в
    обычных секторах), остальные -- в обычные сектора со своей цепочкой FAT.
    Раскладка секторов: FAT, директория, мини-FAT, мини-поток, большие потоки.
    """
    def sectors(n):
        return -(-n // 512)

    streams = [((p,) if isinstance(p, str) else tuple(p), d) for p, d in streams]
    data_of = dict(streams)
    storages = []
    for path, _d in streams:
        for k in range(1, len(path)):
            if path[:k] not in storages:
                storages.append(path[:k])
    nodes = storages + [p for p, _d in streams]        # запись номер i+1 -> nodes[i]
    sid_of = {path: i + 1 for i, path in enumerate(nodes)}

    mini_blob, mini_fat, start = b"", [], {}
    for path, data in streams:
        if len(data) >= _CFB_MINI_CUTOFF:
            continue
        assert data, path
        count, base = -(-len(data) // 64), len(mini_fat)
        start[path] = base
        mini_fat += [base + i + 1 for i in range(count - 1)] + [_CFB_ENDOFCHAIN]
        mini_blob += data.ljust(count * 64, b"\x00")
    big = [(p, d) for p, d in streams if len(d) >= _CFB_MINI_CUTOFF]

    n_dir = sectors((len(nodes) + 1) * 128)
    n_mfat = sectors(len(mini_fat) * 4)
    n_mini = sectors(len(mini_blob))
    rest = n_dir + n_mfat + n_mini + sum(sectors(len(d)) for _p, d in big)
    n_fat = 1
    while n_fat * 128 < rest + n_fat:
        n_fat += 1
    assert n_fat <= 109            # DIFAT хватает заголовка, внешние секторы не нужны

    fat = [_CFB_FREESECT] * (n_fat * 128)
    fat[:n_fat] = [_CFB_FATSECT] * n_fat
    cursor = [n_fat]

    def alloc(count):
        if not count:
            return _CFB_ENDOFCHAIN
        first = cursor[0]
        for i in range(count):
            fat[first + i] = first + i + 1 if i < count - 1 else _CFB_ENDOFCHAIN
        cursor[0] += count
        return first

    dir_start = alloc(n_dir)
    mfat_start = alloc(n_mfat)
    mini_start = alloc(n_mini)
    for path, data in big:
        start[path] = alloc(sectors(len(data)))

    kids = {}
    for path in nodes:
        kids.setdefault(path[:-1], []).append((sid_of[path], path[-1]))
    trees = {parent: _cfb_tree(items) for parent, items in kids.items()}
    links = {}
    for _root, tree_links in trees.values():
        links.update(tree_links)

    entries = [_cfb_entry("Root Entry", 5, 1, _CFB_NOSTREAM, _CFB_NOSTREAM,
                          trees[()][0], root_clsid, mini_start, len(mini_blob),
                          root_mtime)]
    for path in nodes:
        left, right, color = links[sid_of[path]]
        if path in data_of:
            entries.append(_cfb_entry(path[-1], 2, color, left, right,
                                      _CFB_NOSTREAM, start=start[path],
                                      size=len(data_of[path])))
        else:                                           # хранилище
            entries.append(_cfb_entry(path[-1], 1, color, left, right,
                                      trees[path][0]))
    unalloc = _cfb_entry("", 0, 0, _CFB_NOSTREAM, _CFB_NOSTREAM, _CFB_NOSTREAM)
    entries += [unalloc] * (n_dir * 4 - len(entries))

    header = (_CFB_SIG + b"\x00" * 16
              + struct.pack("<HHHHH", 0x003E, 3, 0xFFFE, 9, 6) + b"\x00" * 6
              + struct.pack("<IIIIIIIII", 0, n_fat, dir_start, 0, _CFB_MINI_CUTOFF,
                            mfat_start, n_mfat, _CFB_ENDOFCHAIN, 0)
              + struct.pack("<109I", *(list(range(n_fat))
                                       + [_CFB_FREESECT] * (109 - n_fat))))
    assert len(header) == 512
    mfat = mini_fat + [_CFB_FREESECT] * (n_mfat * 128 - len(mini_fat))
    out = [header, struct.pack("<%dI" % len(fat), *fat), b"".join(entries),
           struct.pack("<%dI" % len(mfat), *mfat), mini_blob.ljust(n_mini * 512, b"\x00")]
    out += [d.ljust(sectors(len(d)) * 512, b"\x00") for _p, d in big]
    return b"".join(out)


def _filetime(dt):
    """datetime (наивный, UTC) -> FILETIME: сотни наносекунд с 1601-01-01."""
    delta = dt - datetime.datetime(1601, 1, 1)
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


def _vt_pad(raw):
    return raw + b"\x00" * (-len(raw) % 4)


def _vt_i2(n):
    return struct.pack("<HHhH", 2, 0, n, 0)


def _vt_i4(n):
    return struct.pack("<HHi", 3, 0, n)


def _vt_lpstr(text, codec):
    raw = text.encode(codec) + b"\x00"
    return struct.pack("<HHI", 30, 0, len(raw)) + _vt_pad(raw)


def _vt_filetime(ticks):
    return struct.pack("<HHQ", 64, 0, ticks)


def _ole_section(props):
    """Секция property set: Size, NumProperties, таблица (PID, смещение от
    начала секции), затем значения. props -- [(pid, готовое значение)]."""
    table_len = 8 + 8 * len(props)
    table, body = b"", b""
    for pid, raw in props:
        table += struct.pack("<II", pid, table_len + len(body))
        body += raw
    return struct.pack("<II", table_len + len(body), len(props)) + table + body


def _ole_propstream(*sections):
    """Поток свойств: заголовок + таблица (FMTID, смещение) + секции."""
    head_len = 28 + 20 * len(sections)
    head = (struct.pack("<HHI", 0xFFFE, 0, 0x00020006) + b"\x00" * 16
            + struct.pack("<I", len(sections)))
    table, body = b"", b""
    for fmtid, section in sections:
        table += uuid.UUID(fmtid).bytes_le + struct.pack("<I", head_len + len(body))
        body += section
    return head + table + body


def _vt_lpwstr(text):
    raw = (text + "\x00").encode("utf-16-le")
    return struct.pack("<HHI", 31, 0, len(raw) // 2) + _vt_pad(raw)


def _ole_userdef_section(items, codepage):
    """Пользовательские свойства: PID 0 -- словарь (PID -> имя), PID 1 --
    кодовая страница секции, значения с PID 2 и выше. Именно в таком порядке
    строк таблицы: так их читает и olefile.

    codepage == 1200 -- как пишет сам Office: имена UTF-16LE (длина в
    СИМВОЛАХ, выравнивание на 4) и значения VT_LPWSTR. Любая другая -- имена и
    значения однобайтовой кодировкой cp<N> (длина в байтах, без выравнивания).
    """
    wide = codepage == 1200
    codec = "utf-16-le" if wide else "cp%d" % codepage
    dictionary = struct.pack("<I", len(items))
    for i, (name, _value) in enumerate(items):
        raw = (name + "\x00").encode(codec)
        dictionary += (struct.pack("<II", 2 + i, len(raw) // 2 if wide else len(raw))
                       + (_vt_pad(raw) if wide else raw))
    props = [(0, _vt_pad(dictionary)), (1, _vt_i2(codepage))]
    props += [(2 + i, _vt_lpwstr(value) if wide else _vt_lpstr(value, codec))
              for i, (_n, value) in enumerate(items)]
    return _ole_section(props)


def _ole_compobj(usertype, clipfmt, progid, clsid):
    """Поток \\x01CompObj: заголовок 28 байт + три строки с префиксом длины."""
    def lp(text):
        raw = text.encode("cp1252") + b"\x00"
        return struct.pack("<I", len(raw)) + raw

    return (struct.pack("<III", 0xFFFE0001, 0x00000A03, 0xFFFFFFFF)
            + uuid.UUID(clsid).bytes_le + lp(usertype) + lp(clipfmt) + lp(progid))


DOC_CODEPAGE = 1251
DOC_TITLE = "Служебная записка"
DOC_AUTHOR = "Иванов Иван Петрович"
DOC_KEYWORDS = "договор; смета; черновик"
DOC_COMMENTS = "Не рассылать, внутренний черновик"
DOC_TEMPLATE = "C:\\Users\\ivanov\\AppData\\Roaming\\Microsoft\\Шаблоны\\Normal.dotm"
DOC_LAST_SAVED_BY = "Сидорова Анна Сергеевна"
DOC_REVNUMBER = "7"
DOC_EDIT_MINUTES = 137
DOC_EDIT_SECONDS = DOC_EDIT_MINUTES * 60
DOC_CREATE = datetime.datetime(2023, 3, 5, 14, 7, 0)
DOC_LASTPRINTED = datetime.datetime(2024, 3, 6, 9, 30, 0)
DOC_SAVED = datetime.datetime(2024, 3, 6, 9, 42, 11)
# Время изменения корневой записи контейнера -- отдельное от дат в свойствах.
DOC_ROOT_MTIME = datetime.datetime(2024, 3, 6, 9, 43, 7)
DOC_APPNAME = "Microsoft Office Word"
DOC_CATEGORY = "Внутреннее"
DOC_MANAGER = "Кузнецов Кузьма Кузьмич"
DOC_COMPANY = "ООО «Ромашка»"
DOC_MSIP_EMAIL = "leaker@example.com"
DOC_CUSTOM_KEY = "Отдел"
DOC_CUSTOM_DEPT = "Бухгалтерия №17"
DOC_QUICK_SAVES = 2
DOC_LID = 0x0419


def _ole_props_streams(user_items=(), user_codepage=1200):
    """Потоки SummaryInformation и DocumentSummaryInformation с заложенными
    значениями. Строки -- VT_LPSTR в cp1251, даты -- FILETIME, время правки --
    FILETIME-длительность в сотнях наносекунд. user_items -- пользовательские
    свойства второй секции DocumentSummaryInformation [(имя, значение)]."""
    codec = "cp%d" % DOC_CODEPAGE
    summary = _ole_section([
        (1, _vt_i2(DOC_CODEPAGE)),
        (2, _vt_lpstr(DOC_TITLE, codec)),
        (4, _vt_lpstr(DOC_AUTHOR, codec)),
        (5, _vt_lpstr(DOC_KEYWORDS, codec)),
        (6, _vt_lpstr(DOC_COMMENTS, codec)),
        (7, _vt_lpstr(DOC_TEMPLATE, codec)),
        (8, _vt_lpstr(DOC_LAST_SAVED_BY, codec)),
        (9, _vt_lpstr(DOC_REVNUMBER, codec)),
        (10, _vt_filetime(DOC_EDIT_SECONDS * 10_000_000)),
        (11, _vt_filetime(_filetime(DOC_LASTPRINTED))),
        (12, _vt_filetime(_filetime(DOC_CREATE))),
        (13, _vt_filetime(_filetime(DOC_SAVED))),
        (14, _vt_i4(4)),
        (15, _vt_i4(1200)),
        (16, _vt_i4(7800)),
        (18, _vt_lpstr(DOC_APPNAME, codec)),
    ])
    docsum = _ole_section([
        (1, _vt_i2(DOC_CODEPAGE)),
        (2, _vt_lpstr(DOC_CATEGORY, codec)),
        (14, _vt_lpstr(DOC_MANAGER, codec)),
        (15, _vt_lpstr(DOC_COMPANY, codec)),
    ])
    sections = [(_FMTID_DOCSUM, docsum)]
    if user_items:
        sections.append((_FMTID_USERDEF, _ole_userdef_section(user_items, user_codepage)))
    return (_ole_propstream((_FMTID_SUMMARY, summary)),
            _ole_propstream(*sections))


def _doc_ole_meta():
    """Что olefile.get_metadata() обязан отдать для make_doc (строки -- bytes
    в cp1251, как их и возвращает olefile)."""
    enc = lambda s: s.encode("cp%d" % DOC_CODEPAGE)
    return {
        "codepage": DOC_CODEPAGE,
        "title": enc(DOC_TITLE), "author": enc(DOC_AUTHOR),
        "keywords": enc(DOC_KEYWORDS), "comments": enc(DOC_COMMENTS),
        "template": enc(DOC_TEMPLATE), "last_saved_by": enc(DOC_LAST_SAVED_BY),
        "revision_number": enc(DOC_REVNUMBER), "total_edit_time": DOC_EDIT_SECONDS,
        "last_printed": DOC_LASTPRINTED, "create_time": DOC_CREATE,
        "last_saved_time": DOC_SAVED, "creating_application": enc(DOC_APPNAME),
        "category": enc(DOC_CATEGORY), "manager": enc(DOC_MANAGER),
        "company": enc(DOC_COMPANY),
    }


def _word_streams(user_items):
    """Потоки обычного .doc: свойства (+ пользовательская секция user_items в
    кодовой странице 1200, как пишет Office), CompObj, WordDocument и 1Table.

    FIB: FibBase 32 байта -- wIdent, nFib, lid, flags (fComplex, cQuickSaves,
    fWhichTblStm). Остальное нули, инспектор читает только базу. WordDocument и
    1Table лежат в обычных секторах (4608 и ровно 4096 байт -- на самой границе
    мини-потока), остальное -- в мини-потоке: так проходят обе ветки чтения.
    """
    flags = 0x0004 | (DOC_QUICK_SAVES << 4) | 0x0200
    fib = struct.pack("<HHHHHHHIBBHHII", 0xA5EC, 0x00C1, 0, DOC_LID, 0, flags,
                      0x00BF, 0, 0, 0, 0, 0, 0, 0)
    body = "Текст документа.".encode("cp1251")
    word_document = (fib.ljust(0x800, b"\x00") + body).ljust(4608, b"\x00")
    summary, docsum = _ole_props_streams(user_items, 1200)
    compobj = _ole_compobj("Microsoft Word 97-2003 Document", "MSWordDoc",
                           "Word.Document.8", _CLSID_WORD8)
    return [("\x05SummaryInformation", summary),
            ("\x05DocumentSummaryInformation", docsum),
            ("\x01CompObj", compobj),
            ("WordDocument", word_document),
            ("1Table", b"\x00" * 4096)]


def make_doc(dir):
    """Настоящий OLE2/CFBF .doc: SummaryInformation, DocumentSummaryInformation
    (+ пользовательская секция в UTF-16: метка MSIP с email и русское имя
    свойства), CompObj, WordDocument (FIB с fComplex, двумя быстрыми
    сохранениями и активной таблицей 1Table) и сама 1Table. Строки свойств --
    cp1251, не UTF-8.
    """
    path = os.path.join(dir, "leaky.doc")
    data = _cfb_build(
        _word_streams([("MSIP_Label_a1b2_SetBy", DOC_MSIP_EMAIL),
                       (DOC_CUSTOM_KEY, DOC_CUSTOM_DEPT)]),
        root_clsid=uuid.UUID(_CLSID_WORD8).bytes_le,
        root_mtime=_filetime(DOC_ROOT_MTIME))
    with open(path, "wb") as fh:
        fh.write(data)

    expected = {
        "values": [DOC_TITLE, DOC_AUTHOR, DOC_KEYWORDS, DOC_COMMENTS,
                   DOC_TEMPLATE, DOC_LAST_SAVED_BY, DOC_APPNAME, DOC_CATEGORY,
                   DOC_MANAGER, DOC_COMPANY, DOC_MSIP_EMAIL, DOC_CUSTOM_DEPT,
                   "2023-03-05 14:07:00", "2024-03-06 09:42:11",
                   "2024-03-06 09:30:00", str(DOC_EDIT_SECONDS), "2024-03-06 09:43:07"],
        "identity": [DOC_AUTHOR, DOC_LAST_SAVED_BY, DOC_COMPANY, DOC_MANAGER,
                     DOC_MSIP_EMAIL, DOC_CUSTOM_DEPT],
        "environment": [DOC_TEMPLATE],
        "signals": ["producer", "hazard"],
        "not_signals": ["ai", "scrubbed", "inconsistent"],
        # olefile.get_metadata(): строки bytes в cp1251, даты datetime, время
        # правки целое число секунд.
        "ole_meta": _doc_ole_meta(),
        "streams": ["\x05SummaryInformation", "\x05DocumentSummaryInformation",
                    "\x01CompObj", "WordDocument", "1Table"],
        # Находка про таблицу авторов правок и остаточный текст: поднимается
        # потоком 1Table, убрать её нельзя (removable=False).
        "unremovable_labels": ["Остаточный текст и таблица авторов правок"],
        # Находка "Время изменения контейнера" (корневая запись CFBF).
        "root_mtime": "2024-03-06 09:43:07",
        "quick_saves": DOC_QUICK_SAVES,
        "lid": DOC_LID,
        # Пользовательские свойства (DocumentSummaryInformation, UTF-16):
        # метка -> значение. Все -- IDENTITY.
        "userdef": {"MSIP_Label_a1b2_SetBy": DOC_MSIP_EMAIL,
                    DOC_CUSTOM_KEY: DOC_CUSTOM_DEPT},
    }
    return path, expected


DOC_OBJECT_ID = "_1396712391"
DOC_OBJECT_TYPE = "Microsoft Excel Worksheet"


def make_doc_active(dir):
    """.doc с активным содержимым: проект VBA в хранилище Macros и встроенный
    OLE-объект в ObjectPool/<id>. Поток CompObj лежит и на верхнем уровне, и
    внутри объекта -- проверка, что инспектор не путает вложенное с корневым."""
    path = os.path.join(dir, "active.doc")
    embedded = _ole_compobj(DOC_OBJECT_TYPE, "Biff8", "Excel.Sheet.8", _CLSID_EXCEL8)
    data = _cfb_build(
        _word_streams([])
        + [(("Macros", "PROJECT"), b'ID="{00000000-0000-0000-0000-000000000000}"\r\n'),
           (("Macros", "VBA", "_VBA_PROJECT"), b"\xcc\x61\x00\x00\x00\x00\x00"),
           (("Macros", "VBA", "dir"), b"\x01\x02\x03" * 20),
           (("Macros", "VBA", "ThisDocument"), b'Attribute VB_Name = "ThisDocument"\r\n'),
           (("ObjectPool", DOC_OBJECT_ID, "\x01CompObj"), embedded),
           (("ObjectPool", DOC_OBJECT_ID, "\x01Ole10Native"), b"\x08\x00\x00\x00payload!")],
        root_clsid=uuid.UUID(_CLSID_WORD8).bytes_le,
        root_mtime=_filetime(DOC_ROOT_MTIME))
    with open(path, "wb") as fh:
        fh.write(data)

    expected = {
        "signals": ["hazard"],
        # Находки с removable=False: проект VBA, вложенные объекты, таблица правок.
        "unremovable_labels": ["Поток макросов", "Вложенные OLE-объекты",
                               "Остаточный текст и таблица авторов правок"],
        "values": [DOC_OBJECT_ID, "_VBA_PROJECT"],
        "macro_stream": "_VBA_PROJECT",
        "object_id": DOC_OBJECT_ID,
        "not_errors": ["CompObj"],
    }
    return path, expected


XLS_WRITEACCESS_USER = "Сидорова А. С."
XLS_SHEET = "Бюджет"
XLS_HIDDEN_SHEET = "Скрытый_Расчёт"
XLS_CUSTOM_KEY = "Подразделение"
XLS_CUSTOM_VALUE = "К-17 Бухгалтерия"
XLS_OWNER_EMAIL = "owner@corp.example"
XLS_EXTERNAL_BOOK = "\\\\SRV-BUH01\\Отдел\\план_2024.xls"


def _biff(rec, body=b""):
    return struct.pack("<HH", rec, len(body)) + body


def _biff_str(text, len_bytes=2):
    """XLUnicodeString / ShortXLUnicodeString в UTF-16LE (флаг 1)."""
    return (len(text).to_bytes(len_bytes, "little") + b"\x01"
            + text.encode("utf-16-le"))


def make_xls(dir):
    """Настоящий OLE2/CFBF .xls: свойства + поток Workbook с BOF, CODEPAGE,
    WRITEACCESS (имя пользователя Excel), двумя BOUNDSHEET (один скрытый),
    SUPBOOK/EXTERNSHEET (внешняя книга), HLINK и EOF. Поток Workbook короче
    4096 байт -- лежит в мини-потоке."""
    path = os.path.join(dir, "book.xls")

    bof_globals = _biff(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x2AE5,
                                            0x07CD, 0x000100C1, 0x00000406))
    bof_sheet = _biff(0x0809, struct.pack("<HHHHII", 0x0600, 0x0010, 0x2AE5,
                                          0x07CD, 0x000100C1, 0x00000406))
    eof = _biff(0x000A)
    sheet = (bof_sheet
             + _biff(0x0200, struct.pack("<IIHHH", 0, 1, 0, 1, 0))      # DIMENSIONS
             # HLINK: ссылка на ячейку + GUID hyperlink-объекта; тело минимальное.
             + _biff(0x01B8, struct.pack("<HHHH", 0, 0, 0, 0)
                     + uuid.UUID("79EAC9D0-BAF9-11CE-8C82-00AA004BA90B").bytes_le
                     + struct.pack("<II", 2, 0))
             + eof)
    sheets_raw = [(XLS_SHEET, 0), (XLS_HIDDEN_SHEET, 1)]

    def globals_stream(offsets):
        out = bof_globals + _biff(0x0042, struct.pack("<H", DOC_CODEPAGE))
        wa = (_biff_str(XLS_WRITEACCESS_USER) + b" " * 112)[:112]
        out += _biff(0x005C, wa)
        for (name, state), off in zip(sheets_raw, offsets):
            out += _biff(0x0085, struct.pack("<IBB", off, state, 0)
                         + _biff_str(name, 1))
        out += _biff(0x01AE, struct.pack("<HH", 2, 0x0401))          # внутренняя
        out += _biff(0x01AE, struct.pack("<H", 1) + _biff_str("\x01" + XLS_EXTERNAL_BOOK))
        out += _biff(0x0017, struct.pack("<HHHH", 1, 1, 0, 0))       # EXTERNSHEET
        return out + eof

    first = len(globals_stream([0, 0]))
    workbook = globals_stream([first, first + len(sheet)]) + sheet + sheet

    # Пользовательские свойства в однобайтовой cp1251 (не UTF-16, не UTF-8):
    # русское имя свойства -- именно оно ломает разбор по UTF-8.
    summary, docsum = _ole_props_streams(
        [(XLS_CUSTOM_KEY, XLS_CUSTOM_VALUE), ("Owner", XLS_OWNER_EMAIL)], 1251)
    data = _cfb_build(
        [("\x05SummaryInformation", summary),
         ("\x05DocumentSummaryInformation", docsum),
         ("Workbook", workbook)],
        root_clsid=uuid.UUID(_CLSID_EXCEL8).bytes_le,
        root_mtime=_filetime(DOC_ROOT_MTIME))
    with open(path, "wb") as fh:
        fh.write(data)

    expected = {
        "values": [DOC_AUTHOR, DOC_LAST_SAVED_BY, DOC_COMPANY, DOC_MANAGER,
                   XLS_WRITEACCESS_USER, XLS_SHEET, XLS_HIDDEN_SHEET,
                   XLS_CUSTOM_VALUE, XLS_OWNER_EMAIL,
                   "2023-03-05 14:07:00", "2024-03-06 09:42:11", "2024-03-06 09:43:07"],
        "identity": [DOC_AUTHOR, DOC_LAST_SAVED_BY, DOC_COMPANY, DOC_MANAGER,
                     XLS_WRITEACCESS_USER, XLS_CUSTOM_VALUE, XLS_OWNER_EMAIL],
        "signals": ["producer"],
        "not_signals": ["ai", "scrubbed", "inconsistent"],
        "ole_meta": _doc_ole_meta(),
        "streams": ["\x05SummaryInformation", "\x05DocumentSummaryInformation",
                    "Workbook"],
        # Внешние связи: SUPBOOK x2, EXTERNSHEET x1, EXTERNNAME x0, HLINK x2.
        "supbook": 2, "externsheet": 1, "hlinks": 2, "root_mtime": "2024-03-06 09:43:07",
        # Пользовательские свойства (cp1251): метка -> значение. Все -- IDENTITY.
        "userdef": {XLS_CUSTOM_KEY: XLS_CUSTOM_VALUE, "Owner": XLS_OWNER_EMAIL},
    }
    return path, expected


PPT_USER = "Петров Пётр Петрович"
_CLSID_PPT8 = "64818D10-4F9B-11CF-86EA-00AA00B929E8"


def make_ppt(dir, unicode_name=True):
    """Настоящий OLE2/CFBF .ppt: свойства, поток "PowerPoint Document" и
    "Current User" (CurrentUserAtom, MS-PPT 2.3.2) с именем последнего
    редактора. ansiUserName -- в cp1251, как пишет русский PowerPoint;
    unicode_name=True добавляет и unicodeUserName (UTF-16), как делают
    PowerPoint 2000+."""
    path = os.path.join(dir, "deck.ppt")
    ansi = PPT_USER.encode("cp1251")
    atom = (struct.pack("<IIIHHBBH", 0x14, 0xE391C05F, 0, len(PPT_USER), 0x03F4, 3, 0, 0)
            + ansi)
    if unicode_name:
        atom += struct.pack("<I", 8) + PPT_USER.encode("utf-16-le")
    current_user = struct.pack("<HHI", 0, 0x0FF6, len(atom)) + atom

    summary, docsum = _ole_props_streams()
    data = _cfb_build(
        [("\x05SummaryInformation", summary),
         ("\x05DocumentSummaryInformation", docsum),
         ("Current User", current_user),
         ("PowerPoint Document", b"\x00" * 4096)],
        root_clsid=uuid.UUID(_CLSID_PPT8).bytes_le,
        root_mtime=_filetime(DOC_ROOT_MTIME))
    with open(path, "wb") as fh:
        fh.write(data)

    expected = {
        "values": [DOC_AUTHOR, DOC_LAST_SAVED_BY, DOC_COMPANY, PPT_USER, "2024-03-06 09:43:07"],
        "identity": [DOC_AUTHOR, DOC_LAST_SAVED_BY, DOC_COMPANY, PPT_USER],
        "signals": ["producer"],
        "not_signals": ["ai", "scrubbed", "inconsistent"],
        "streams": ["Current User", "PowerPoint Document"],
    }
    return path, expected


# ===========================================================================
# Патологии
# ===========================================================================


def make_broken(dir):
    """Набор файлов-патологий. Возвращает {имя: путь} -- тут нет "заложенных
    значений", тест только проверяет, что инспекция не падает и не ест память.
    """
    out = {}

    p = os.path.join(dir, "empty.bin")
    with open(p, "wb") as fh:
        pass
    out["empty"] = p

    p = os.path.join(dir, "junk_pdf_header.pdf")
    with open(p, "wb") as fh:
        fh.write(b"%PDF-" + os.urandom(500))
    out["pdf_header_junk"] = p

    p = os.path.join(dir, "bad_entry.zip")
    good = os.path.join(dir, "_bad_entry_source.zip")
    with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ok.txt", "normal entry")
        zf.writestr("second.txt", "second entry " * 50)
    with open(good, "rb") as fh:
        full = fh.read()
    # Обрезаем хвост -- вторая запись и вся центральная директория/EOCD
    # пропадают. zipfile.ZipFile() не находит EOCD и падает BadZipFile,
    # это и есть "битая запись": сигнатура PK в начале на месте, но
    # архив как целое не открывается.
    with open(p, "wb") as fh:
        fh.write(full[: int(len(full) * 0.6)])
    os.remove(good)
    out["zip_bad_entry"] = p

    p = os.path.join(dir, "zip_bomb.zip")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
        # Один байт, повторённый в 200 МБ -- deflate сжимает это в доли
        # процента исходного размера, коэффициент сжатия огромный.
        zf.writestr("bomb.bin", b"\x00" * (200 * 1024 * 1024))
    out["zip_bomb"] = p

    p = os.path.join(dir, "cut.jpg")
    full = b"\xff\xd8\xff\xe1\x01\x00Exif\x00\x00" + b"\x00" * 600
    with open(p, "wb") as fh:
        fh.write(full[: len(full) // 2])
    out["jpeg_truncated"] = p

    # PNG с zTXt-бомбой: около килобайта сжатых данных разворачиваются в
    # 200 МБ. Отдельный от zip-бомбы вектор отказа -- здесь распаковку
    # запускает разбор ТЕКСТОВОГО чанка PNG, и сделать это через голый
    # zlib.decompress() означает съесть память на файле, который по размеру
    # выглядит безобидно. Бот принимает произвольные файлы и работает без
    # сандбокса, так что защита обязана быть в самом разборе.
    p = os.path.join(dir, "ztxt_bomb.png")
    payload = zlib.compress(b"\x00" * (200 * 1024 * 1024), 9)
    with open(p, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)))
        fh.write(_png_chunk(b"zTXt", b"Bomb\x00\x00" + payload))
        fh.write(_png_chunk(b"IEND", b""))
    out["png_ztxt_bomb"] = p

    return out
