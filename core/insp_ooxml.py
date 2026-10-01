# -*- coding: utf-8 -*-
"""Инспектор OOXML-пакетов: .docx / .xlsx / .pptx.

Фаза 0: только чтение. Файл открывается строго на чтение, ничего не
распаковывается на диск, входной файл не модифицируется никогда.

Контейнер OOXML -- это обычный ZIP, и метаданные в нём лежат не только в
docProps/*.xml: течёт сам ZIP (таймстемпы записей), настройки Word (rsid,
путь к шаблону), правки с именами авторов, presenceInfo с реальными email,
DEVMODE принтера, строки подключения Excel и метки Purview с GUID тенанта.
Поэтому разбираем пакет как контейнер, а не как "документ": стандартные
библиотеки (python-docx, openpyxl) теряют части пакета и вместе с ними --
половину утечек.

Публичный API:
    inspect_ooxml(path, fmt="docx") -> Report
    parse_props(zf) -> dict            # сырые значения core/app/custom для фазы 2

Самопроверка: PYTHONIOENCODING=utf-8 python -m core.insp_ooxml
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile

from lxml import etree

try:
    from .model import Report, Risk
except ImportError:  # запуск файла напрямую, вне пакета
    from model import Report, Risk


# --- пределы, защищающие от зип-бомб и просто гигантских файлов -----------

MAX_PART_READ = 32 * 1024 * 1024      # сколько максимум читаем в память за раз
MAX_STREAM = 50 * 1024 * 1024         # потолок потокового разбора document.xml
MAX_TOTAL_UNPACKED = 512 * 1024 * 1024
MAX_RATIO = 120                       # суммарный коэффициент сжатия пакета
# Высокий коэффициент сжатия сам по себе не бомба: однообразный XML большого
# отчёта жмётся в сотни раз и при этом безобиден. Опасна только сумма
# "много байт И огромный коэффициент", поэтому у коэффициента есть порог.
MIN_BOMB_UNPACKED = 64 * 1024 * 1024
MAX_PARTS_SCAN = 4000                 # сколько записей ZIP вообще обрабатываем
MAX_ELEMENTS = 400_000                # потолок элементов при iterparse
MAX_LISTED = 40                       # сколько однотипных находок печатаем

WORD_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)   # что Word ставит всем записям

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PATH_RE = re.compile(
    r"^\s*(file:///|[A-Za-z]:[\\/]|\\\\[^\\]+\\|/Users/|/home/|/Volumes/)"
)
# Путь ВНУТРИ значения, а не с его начала: переменные документа и свойства СЭД
# обычно выглядят как "docVar(name=База; val=\\FS01\buh\baza.accdb)".
# (?<![A-Za-z]) не даёт принять "https://" за диск "s:/".
_PATH_ANY_RE = re.compile(
    r"(file:///|(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|/Users/|/home/|/Volumes/)"
)

# Имена свойств, в которых лежит именно человек. Без этого списка ФИО из
# свойств СЭД ("Исполнитель", "Подписант") уезжали бы в PROVENANCE.
_PERSON_NAME_RE = re.compile(
    r"(author|owner|creator|manager|savedby|lasteditedby|employee|signer"
    r"|автор|исполнител|ответствен|подписант|согласов|утвердил|разработал"
    r"|руководител|сотрудник|фио|контакт)", re.I)

# Части, которые Word обязан положить в .docx. Их отсутствие -- признак того,
# что файл собран библиотекой, а не Word, даже если app.xml утверждает обратное.
WORD_REQUIRED = [
    "word/styles.xml",
    "word/settings.xml",
    "word/fontTable.xml",
    "word/webSettings.xml",
    "word/theme/theme1.xml",
]

# Поля docProps/core.xml: локальное имя -> (риск, подпись, пояснение).
CORE_FIELDS = [
    ("creator", Risk.IDENTITY, "dc:creator — автор",
     "Имя пользователя или логин того, кто создал документ."),
    ("lastModifiedBy", Risk.IDENTITY, "cp:lastModifiedBy — кто сохранил последним",
     "Часто не совпадает с автором и выдаёт второго человека в цепочке."),
    ("created", Risk.PROVENANCE, "dcterms:created — создан", ""),
    ("modified", Risk.PROVENANCE, "dcterms:modified — изменён", ""),
    ("revision", Risk.PROVENANCE, "cp:revision — номер ревизии",
     "Сколько раз документ сохраняли."),
    ("lastPrinted", Risk.PROVENANCE, "cp:lastPrinted — последняя печать",
     "Сам факт печати и её точное время."),
    ("title", Risk.PROVENANCE, "dc:title — заголовок",
     "Нередко хранит исходное имя файла или название проекта."),
    ("subject", Risk.PROVENANCE, "dc:subject — тема", ""),
    ("description", Risk.PROVENANCE, "dc:description — описание", ""),
    ("keywords", Risk.PROVENANCE, "cp:keywords — ключевые слова", ""),
    ("category", Risk.PROVENANCE, "cp:category — категория", ""),
    ("contentStatus", Risk.PROVENANCE, "cp:contentStatus — статус",
     "Черновик/на согласовании — выдаёт внутренний процесс."),
    ("identifier", Risk.PROVENANCE, "dc:identifier — идентификатор",
     "Внутренний ID документа в СЭД или GUID."),
    ("language", Risk.STRUCTURAL, "dc:language — язык",
     "Обычно сохраняем: влияет на проверку орфографии и доступность."),
]

# Поля docProps/app.xml.
APP_FIELDS = {
    "Application": (Risk.PROVENANCE, "Application — приложение",
                    "Чем именно сохранён файл."),
    "AppVersion": (Risk.PROVENANCE, "AppVersion — версия приложения",
                   "Сужает круг до конкретной сборки Office."),
    "Company": (Risk.IDENTITY, "Company — организация",
                "Название компании из настроек Office."),
    "Manager": (Risk.IDENTITY, "Manager — руководитель",
                "ФИО руководителя из свойств документа."),
    "TotalTime": (Risk.PROVENANCE, "TotalTime — время редактирования, мин",
                  "Сколько реально работали над документом. Сильная утечка: "
                  "выдаёт и трудоёмкость, и то, что документ не шаблонный."),
    "Pages": (Risk.PROVENANCE, "Pages — страниц", ""),
    "Words": (Risk.PROVENANCE, "Words — слов", ""),
    "Characters": (Risk.PROVENANCE, "Characters — символов", ""),
    "CharactersWithSpaces": (Risk.PROVENANCE, "CharactersWithSpaces — символов с пробелами", ""),
    "Lines": (Risk.PROVENANCE, "Lines — строк", ""),
    "Paragraphs": (Risk.PROVENANCE, "Paragraphs — абзацев", ""),
    "Slides": (Risk.PROVENANCE, "Slides — слайдов", ""),
    "Notes": (Risk.PROVENANCE, "Notes — заметок", ""),
    "HiddenSlides": (Risk.PROVENANCE, "HiddenSlides — скрытых слайдов", ""),
    "Template": (Risk.PROVENANCE, "Template — шаблон",
                 "Имя шаблона; если это путь — утекает файловая система."),
    "DocSecurity": (Risk.PROVENANCE, "DocSecurity — флаг защиты", ""),
    "ScaleCrop": (Risk.PROVENANCE, "ScaleCrop", ""),
    "LinksUpToDate": (Risk.PROVENANCE, "LinksUpToDate", ""),
    "SharedDoc": (Risk.PROVENANCE, "SharedDoc — общий доступ", ""),
    "HyperlinksChanged": (Risk.PROVENANCE, "HyperlinksChanged", ""),
    "HeadingPairs": (Risk.PROVENANCE, "HeadingPairs — состав документа", ""),
    "TitlesOfParts": (Risk.PROVENANCE, "TitlesOfParts — названия частей",
                      "Заголовки разделов и имена листов книги."),
    "DigSig": (Risk.IDENTITY, "DigSig — цифровая подпись", ""),
}

# Атрибуты в частях с комментариями и авторами. З-секция.
PERSON_ATTRS = {
    "author": (Risk.IDENTITY, "Автор"),
    "name": (Risk.IDENTITY, "Имя автора"),
    "displayName": (Risk.IDENTITY, "Отображаемое имя"),
    "initials": (Risk.IDENTITY, "Инициалы автора"),
    "userId": (Risk.IDENTITY, "userId — как правило, реальный email"),
    "email": (Risk.IDENTITY, "Email автора"),
    "providerId": (Risk.PROVENANCE, "Провайдер учётной записи"),
    "date": (Risk.PROVENANCE, "Дата комментария"),
    "dT": (Risk.PROVENANCE, "Дата комментария"),
    "created": (Risk.PROVENANCE, "Дата создания комментария"),
    # GUID сам по себе не PII, но связывает реплику со списком авторов:
    # если фаза 2 вычистит authors.xml, а эти ссылки оставит — связь пропадёт.
    "authorId": (Risk.PROVENANCE, "authorId — ссылка на список авторов"),
    "personId": (Risk.PROVENANCE, "personId — ссылка на список авторов"),
    "lastModifiedBy": (Risk.IDENTITY, "Кем изменён комментарий"),
}


# === мелкие утилиты =======================================================


def _ln(tag) -> str:
    """Локальное имя тега или атрибута без пространства имён."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _attr(el, name):
    """Атрибут по локальному имени: префиксы в файлах бывают любые."""
    for key, val in el.attrib.items():
        if _ln(key) == name:
            return val
    return None


def _iter_tag(root, *names):
    """Все элементы с указанными локальными именами."""
    wanted = set(names)
    for el in root.iter():
        if _ln(el.tag) in wanted:
            yield el


def _text(el) -> str:
    """Весь текст элемента вместе с потомками (vt:vector и т.п.)."""
    return " ".join(t.strip() for t in el.itertext() if t and t.strip())


def _is_path(value) -> bool:
    return bool(value) and bool(_PATH_RE.match(str(value)))


def _has_path(value) -> bool:
    """Путь где-то внутри значения, а не обязательно с его начала."""
    return bool(value) and bool(_PATH_ANY_RE.search(str(value)))


def _emails(value):
    return _EMAIL_RE.findall(str(value or ""))


def _value_risk(value, default=Risk.PROVENANCE):
    """Риск по форме значения: email -> личность, путь -> окружение."""
    if _emails(value):
        return Risk.IDENTITY
    if _has_path(value):
        return Risk.ENVIRONMENT
    return default


def _kids_attrs(el, limit=MAX_LISTED) -> str:
    """Данные потомков, у которых значение лежит в атрибутах, а не в тексте.

    w:docVars и w:shapeDefaults устроены именно так: itertext() по ним даёт
    пустую строку, и без этого разбора имена и значения переменных теряются.
    """
    out = []
    for kid in el.iter():
        if kid is el or not isinstance(kid.tag, str):
            continue
        pairs = "; ".join("%s=%s" % (_ln(k), v) for k, v in kid.attrib.items())
        if pairs:
            out.append("%s(%s)" % (_ln(kid.tag), pairs))
        if len(out) >= limit:
            break
    return " | ".join(out)


def _parser():
    """XML-парсер без внешних сущностей и сети: файлы недоверенные."""
    return etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False,
        load_dtd=False, recover=True,
    )


class _Capped:
    """Поток с жёстким потолком байт: защита от бомбы при потоковом разборе."""

    def __init__(self, fh, limit):
        self._fh = fh
        self._left = limit

    def read(self, size=-1):
        if self._left <= 0:
            return b""
        if size is None or size < 0:
            size = self._left
        chunk = self._fh.read(min(size, self._left))
        self._left -= len(chunk)
        return chunk

    @property
    def exhausted(self) -> bool:
        """Достигнут ли потолок байт (а не естественный конец потока)."""
        return self._left <= 0


def _read_part(zf, name, limit=MAX_PART_READ):
    """Прочитать часть пакета в память с проверкой заявленного размера."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ValueError(
            "часть %s слишком велика (%d байт), пропущена" % (name, info.file_size)
        )
    with zf.open(info, "r") as fh:
        return fh.read(limit + 1)[:limit]


def _read_xml(zf, name, limit=MAX_PART_READ):
    """Разобрать XML-часть. Возвращает корень или None."""
    raw = _read_part(zf, name, limit)
    if not raw:
        return None
    root = etree.fromstring(raw, parser=_parser())
    return root


def _section(rep, title, fn, *args):
    """Каждая секция разбора изолирована: сбой одной не валит инспекцию."""
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 -- ловим всё намеренно
        rep.err("%s: %s: %s" % (title, type(exc).__name__, exc))


def _try(rep, where, fn, *args):
    """То же, но для ОДНОЙ части внутри цикла.

    Без этого битый локальный заголовок одной записи обрывал разбор всех
    остальных частей секции: в отчёте оставалась одна ошибка вместо находок.
    """
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        rep.err("%s: %s: %s" % (where, type(exc).__name__, exc))
        return None


# === разбор docProps (переиспользуется фазой 2) ===========================


def parse_props(zf) -> dict:
    """Сырые значения docProps/core.xml, app.xml и custom.xml.

    Вынесено отдельной функцией, чтобы фаза 2 (чистка) не переписывала разбор
    заново, а работала с теми же словарями, что видел инспектор.
    Возвращает {"core": {...}, "app": {...}, "custom": [(имя, тип, значение)]}.
    Никогда не бросает: непрочитанная часть даёт пустой словарь.
    """
    out = {"core": {}, "app": {}, "custom": []}

    try:
        root = _read_xml(zf, "docProps/core.xml")
        if root is not None:
            for el in root:
                key = _ln(el.tag)
                if key:
                    out["core"][key] = (el.text or "").strip()
    except Exception:
        pass

    try:
        root = _read_xml(zf, "docProps/app.xml")
        if root is not None:
            for el in root:
                key = _ln(el.tag)
                if not key:
                    continue
                out["app"][key] = _text(el) if len(el) else (el.text or "").strip()
    except Exception:
        pass

    try:
        root = _read_xml(zf, "docProps/custom.xml")
        if root is not None:
            for el in _iter_tag(root, "property"):
                name = _attr(el, "name") or ""
                kids = [k for k in el if isinstance(k.tag, str)]
                vtype = _ln(kids[0].tag) if kids else ""
                value = _text(el)
                out["custom"].append((name, vtype, value))
    except Exception:
        pass

    return out


# === А. сам ZIP-контейнер =================================================


def _sec_zip(zf, rep, ctx):
    infos = zf.infolist()
    ctx["names"] = [i.filename for i in infos]
    ctx["nameset"] = set(ctx["names"])

    if len(infos) > MAX_PARTS_SCAN:
        rep.err("В пакете %d записей, обработаны первые %d"
                % (len(infos), MAX_PARTS_SCAN))
        infos = infos[:MAX_PARTS_SCAN]

    total_unc = 0
    total_cmp = 0
    for i in infos:
        total_unc += i.file_size
        total_cmp += i.compress_size
        # Фаза 2 пересобирает пакет байт-в-байт, ей нужны все поля записи.
        rep.parts.append({
            "name": i.filename,
            "date_time": tuple(i.date_time),
            "compress_type": i.compress_type,
            "compress_size": i.compress_size,
            "file_size": i.file_size,
            "crc": i.CRC,
            "flag_bits": i.flag_bits,
            "create_system": i.create_system,
        })

    ratio = total_unc / max(total_cmp, 1)
    ctx["safe"] = True
    if total_unc > MAX_TOTAL_UNPACKED or (
            total_unc > MIN_BOMB_UNPACKED and ratio > MAX_RATIO):
        ctx["safe"] = False
        rep.signal("hazard",
                   "Подозрение на zip-бомбу: несжатый объём %d байт, коэффициент "
                   "сжатия %.0f. Содержимое частей не распаковывалось."
                   % (total_unc, ratio), "high")
        rep.err("Разбор содержимого пропущен из соображений безопасности")

    rep.add(Risk.PROVENANCE, "ZIP", "Записей в пакете", str(len(infos)),
            "Состав пакета сам по себе выдаёт и генератор, и структуру документа.")

    # Первая запись. Office всегда кладёт [Content_Types].xml первым.
    first = infos[0].filename if infos else ""
    rep.add(Risk.PROVENANCE, "ZIP", "Первая запись контейнера", first,
            "Word/Excel/PowerPoint всегда пишут [Content_Types].xml первым.")
    if first and first != "[Content_Types].xml":
        rep.signal("ai",
                   "Первая запись ZIP — %s, а не [Content_Types].xml: пакет "
                   "собран сторонним инструментом или пересобран." % first,
                   "medium")

    # Таймстемпы. Word обнуляет их в 1980-01-01, библиотеки пишут реальное время.
    stamps = [tuple(i.date_time) for i in infos]
    epoch_like = [s for s in stamps if s[:3] == (1980, 1, 1)]
    real = sorted(s for s in stamps if s[0] >= 2000)
    ctx["zip_real"] = bool(real)
    ctx["zip_epoch_only"] = bool(stamps) and len(epoch_like) == len(stamps)

    if real:
        lo = "%04d-%02d-%02d %02d:%02d:%02d" % real[0]
        hi = "%04d-%02d-%02d %02d:%02d:%02d" % real[-1]
        rep.add(Risk.ENVIRONMENT, "ZIP", "Реальное время сохранения записей",
                "%s … %s (%d из %d записей)" % (lo, hi, len(real), len(stamps)),
                "Office ставит всем записям 1980-01-01 00:00. Реальные даты "
                "означают, что пакет собран или пересобран библиотекой: время "
                "работы над файлом утекает даже при полностью вычищенном "
                "docProps/core.xml.")
        ctx["zip_real_max"] = hi
    elif ctx["zip_epoch_only"]:
        rep.add(Risk.PROVENANCE, "ZIP", "Таймстемпы записей",
                "все 1980-01-01 00:00",
                "Штатное поведение Office: время сохранения через ZIP не течёт.")

    types = {i.compress_type for i in infos}
    if types and types != {zipfile.ZIP_DEFLATED}:
        rep.add(Risk.PROVENANCE, "ZIP", "Методы сжатия",
                ", ".join(str(t) for t in sorted(types)),
                "Office сжимает все части deflate; смесь методов — признак "
                "пересборки сторонним инструментом.")


# === Б. docProps/core.xml =================================================


def _sec_core(zf, rep, ctx):
    core = ctx["props"]["core"]
    if "docProps/core.xml" not in ctx["nameset"]:
        rep.signal("ai", "Нет docProps/core.xml — Office такой пакет не пишет.",
                   "high")
        return
    if not core:
        # Иначе битый core.xml выглядит как чистый: ни ошибки, ни находок.
        rep.err("docProps/core.xml: часть есть, но ни одного свойства не "
                "разобрано (повреждённый или не-XML контент)")
    for key, risk, label, note in CORE_FIELDS:
        present = key in core
        value = core.get(key, "")
        full = note
        if not present:
            full = (note + " Элемент в core.xml отсутствует.").strip()
        # Пустое поле показываем намеренно: это тоже факт (чистили или сток).
        rep.add(risk, "docProps/core.xml", label, value, full)
        for mail in _emails(value):
            rep.add(Risk.IDENTITY, "docProps/core.xml",
                    "Email в поле %s" % key, mail,
                    "Адрес в свойствах документа.")


# === В. docProps/app.xml ==================================================


def _sec_app(zf, rep, ctx):
    app = ctx["props"]["app"]
    if "docProps/app.xml" not in ctx["nameset"]:
        rep.signal("ai", "Нет docProps/app.xml — Office такой пакет не пишет.",
                   "high")
        return
    if not app:
        rep.err("docProps/app.xml: часть есть, но ни одного свойства не "
                "разобрано (повреждённый или не-XML контент)")
    for key, value in app.items():
        risk, label, note = APP_FIELDS.get(
            key, (Risk.PROVENANCE, "%s" % key, "Нестандартное поле app.xml.")
        )
        if key == "Template" and _has_path(value):
            risk = Risk.ENVIRONMENT
            note = "Путь к шаблону выдаёт файловую систему автора."
        rep.add(risk, "docProps/app.xml", label, value, note)
        for mail in _emails(value):
            rep.add(Risk.IDENTITY, "docProps/app.xml",
                    "Email в поле %s" % key, mail, "Адрес в свойствах документа.")


# === Г. docProps/custom.xml и метки Purview ==============================

# Свойства SharePoint/СЭД: привязка к конкретному порталу и библиотеке.
_SP_NAMES = {
    "ContentTypeId", "TemplateUrl", "_SourceUrl", "_SharedFileIndex",
    "_dlc_DocId", "_dlc_DocIdUrl", "_dlc_DocIdItemGuid", "Order",
    "xd_Signature", "xd_ProgID", "_ExtendedDescription",
}

_MSIP_NOTES = {
    "SetBy": "Реальный email пользователя, применившего метку. Самая прямая "
             "утечка личности в OOXML.",
    "SiteId": "GUID тенанта Microsoft 365 — однозначно идентифицирует "
              "организацию, даже если всё остальное вычищено.",
    "SetDate": "Точное время применения метки.",
    "Name": "Человекочитаемое имя метки конфиденциальности.",
    "ActionId": "GUID операции маркировки.",
    "Enabled": "Флаг активности метки.",
    "Method": "Как применена метка: вручную или политикой.",
    "ContentBits": "Какие части документа промаркированы (колонтитул, ватермарк).",
    "SiteUrl": "URL портала организации.",
}


def _sec_custom(zf, rep, ctx):
    props = ctx["props"]["custom"]
    loc = "docProps/custom.xml"
    if not props and loc in ctx["nameset"]:
        # Та же логика, что у core.xml/app.xml: часть есть, но ни одного
        # свойства не разобрано -- иначе битый custom.xml выглядит как файл
        # без кастомных свойств вовсе, и находка молча пропадает.
        rep.err("docProps/custom.xml: часть есть, но ни одного свойства не "
                "разобрано (повреждённый или не-XML контент)")
    for name, vtype, value in props:
        risk = Risk.PROVENANCE
        note = "Пользовательское свойство (тип %s)." % (vtype or "?")

        if name.startswith("MSIP_Label_"):
            risk = Risk.IDENTITY
            field = name.rsplit("_", 1)[-1]
            note = _MSIP_NOTES.get(
                field, "Метка конфиденциальности Microsoft Purview.")
            note = "Purview: " + note
        # Email/ФИО в ЗНАЧЕНИИ -- это личность независимо от того, как называется
        # само свойство. Раньше имя вида "_dlc_DocId" или "_Approver" уводило
        # находку в ENVIRONMENT по одному лишь подчёркиванию, даже если внутри
        # лежал реальный email: проверка по значению должна идти раньше
        # проверки по имени SharePoint/СЭД, а не после неё.
        elif _emails(value):
            risk = Risk.IDENTITY
            note = "Значение содержит email."
        elif value and _PERSON_NAME_RE.search(name):
            risk = Risk.IDENTITY
            note = ("Свойство хранит человека (автор, исполнитель, подписант) — "
                    "ФИО из шаблона или карточки СЭД.")
        elif name in _SP_NAMES or name.startswith("_") or "urn:schemas" in name:
            risk = Risk.ENVIRONMENT
            note = "Свойство SharePoint/СЭД: привязка к порталу и библиотеке."
        elif _has_path(value):
            risk = Risk.ENVIRONMENT
            note = "Значение похоже на локальный или сетевой путь."

        rep.add(risk, loc, name, value, note)

    if any(n.startswith("MSIP_Label_") for n, _, _ in props):
        rep.signal("producer",
                   "Документ промаркирован Microsoft Purview: метка несёт GUID "
                   "тенанта и email применившего её сотрудника.", "high")

    # Параллельная площадка для тех же меток в новых сборках Office.
    for name in ctx["names"]:
        if name.lower().endswith("labelinfo.xml"):
            raw = _try(rep, name, _read_part, zf, name, 1 * 1024 * 1024)
            if raw:
                rep.add(Risk.IDENTITY, name, "LabelInfo (метка Purview)",
                        raw.decode("utf-8", "replace"),
                        "Дублирует метку конфиденциальности отдельной частью "
                        "пакета; чистка только custom.xml её не убирает.")


# === Д. превью ============================================================


def _sec_thumbnail(zf, rep, ctx):
    for name in ctx["names"]:
        if not name.startswith("docProps/thumbnail"):
            continue
        try:
            size = zf.getinfo(name).file_size
        except KeyError:
            size = 0
        rep.add(Risk.PROVENANCE, name, "Превью документа", "%d байт" % size,
                "Превью рендерится при сохранении и может показывать ПРЕДЫДУЩУЮ "
                "версию документа — в том числе уже удалённый текст. Кроме "
                "того, это картинка со своими метаданными.")
        rep.signal("hazard",
                   "Есть встроенное превью (%s): визуально может отличаться от "
                   "текущего содержимого." % name, "low")


# === И. отношения (*.rels) ================================================


def _owner_of_rels(name):
    """word/_rels/settings.xml.rels -> word/settings.xml"""
    head, _, base = name.rpartition("/")
    base = base[:-5] if base.endswith(".rels") else base
    head = head[:-6] if head.endswith("/_rels") else head
    if head == "_rels":
        head = ""
    return (head + "/" + base).lstrip("/")


def _sec_rels(zf, rep, ctx):
    ctx["rels"] = {}
    shown = 0
    http_count = 0
    for name in ctx["names"]:
        if not name.endswith(".rels"):
            continue
        try:
            root = _read_xml(zf, name, 8 * 1024 * 1024)
        except Exception as exc:  # noqa: BLE001
            rep.err("rels %s: %s" % (name, exc))
            continue
        if root is None:
            continue
        owner = _owner_of_rels(name)
        table = ctx["rels"].setdefault(owner, {})
        for el in _iter_tag(root, "Relationship"):
            rid = _attr(el, "Id") or ""
            target = _attr(el, "Target") or ""
            mode = _attr(el, "TargetMode") or ""
            rtype = _attr(el, "Type") or ""
            table[rid] = (target, mode, rtype)
            low = target.lower()
            # На TargetMode полагаться нельзя: в битых пакетах атрибут теряется
            # при восстановлении XML. Внутренняя цель никогда не выглядит как
            # абсолютный путь, mailto: или http(s), поэтому судим по форме.
            external = (
                mode == "External" or _has_path(target)
                or low.startswith(("mailto:", "http://", "https://"))
            )
            if not external:
                continue
            if low.startswith("mailto:"):
                rep.add(Risk.IDENTITY, name, "Ссылка mailto", target,
                        "Прямой email-адрес во внешнем отношении.")
                continue
            # http(s) разбираем ДО путей: иначе URL с буквой диска внутри
            # попадал бы в ветку файлов.
            if low.startswith("http://") or low.startswith("https://"):
                http_count += 1
                if shown < MAX_LISTED:
                    shown += 1
                    risk = Risk.ENVIRONMENT if (
                        "sharepoint.com" in low or "/personal/" in low
                        or ".local/" in low
                    ) else Risk.PROVENANCE
                    rep.add(risk, name, "Внешняя гиперссылка", target,
                            "Внешнее отношение; корпоративные URL выдают "
                            "портал и учётную запись.")
                continue
            if _has_path(target):
                rep.add(Risk.ENVIRONMENT, name, "Внешняя ссылка на файл", target,
                        "Абсолютный путь/UNC выдаёт имя пользователя, диск или "
                        "сервер организации.")
                continue
            rep.add(Risk.ENVIRONMENT, name, "Внешнее отношение (%s)" % rid,
                    target, "TargetMode=External, тип %s" % _ln(rtype))

    if http_count > shown:
        rep.add(Risk.PROVENANCE, "*.rels", "Ещё внешних гиперссылок",
                str(http_count - shown), "Показаны не все, остальные того же рода.")


def _resolve(ctx, owner, rid):
    """Разрешить r:id в цель через соответствующий .rels."""
    if not rid:
        return None
    entry = ctx.get("rels", {}).get(owner, {}).get(rid)
    return entry[0] if entry else None


# === Е. word/settings.xml =================================================


def _sec_settings(zf, rep, ctx):
    loc = "word/settings.xml"
    if loc not in ctx["nameset"]:
        return
    root = _read_xml(zf, loc)
    if root is None:
        return

    # rsid: идентификаторы сессий правки.
    rsids = []
    for el in _iter_tag(root, "rsid", "rsidRoot"):
        val = _attr(el, "val")
        if val:
            rsids.append(val)
    ctx["rsid_count"] = len(rsids)
    rep.add(Risk.PROVENANCE, loc, "w:rsid — идентификаторы сессий правки",
            "%d шт.: %s" % (len(rsids), ", ".join(rsids[:8]) or "нет"),
            "Каждый rsid — отдельная сессия редактирования. Их количество "
            "выдаёт, сколько раз документ открывали и правили, а сам набор "
            "связывает разные документы одной инсталляции Word.")

    # attachedTemplate: почти всегда путь вида file:///C:/Users/ИМЯ/...
    for el in _iter_tag(root, "attachedTemplate"):
        rid = _attr(el, "id")
        target = _resolve(ctx, loc, rid)
        risk = Risk.ENVIRONMENT if _has_path(target) else Risk.PROVENANCE
        rep.add(risk, loc, "w:attachedTemplate — присоединённый шаблон",
                target or ("r:id=%s (не разрешён)" % rid),
                "Путь к шаблону обычно содержит имя пользователя Windows или "
                "UNC-путь к серверу организации.")

    # mailMerge: источник данных рассылки.
    for el in _iter_tag(root, "mailMerge"):
        for kid in el.iter():
            kname = _ln(kid.tag)
            # w:udl -- это и есть строка подключения слияния (ODBC/OLEDB),
            # w:table -- имя листа или таблицы в источнике. Без них разбор
            # mailMerge не доставал главного: логина и адреса сервера.
            if kname not in ("dataSource", "headerSource", "query", "src",
                             "connectString", "udl", "table", "odso"):
                continue
            val = _attr(kid, "val") or _attr(kid, "id") or ""
            if _attr(kid, "id"):
                val = _resolve(ctx, loc, _attr(kid, "id")) or val
            if not val:
                continue
            rep.add(_value_risk(val, Risk.ENVIRONMENT),
                    loc, "w:mailMerge/%s" % kname, val,
                    "Источник данных слияния: путь к базе, таблице или "
                    "строка подключения с логином и адресом сервера.")

    # documentProtection: хеш и соль пароля.
    for el in _iter_tag(root, "documentProtection", "writeProtection"):
        parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
        rep.add(Risk.IDENTITY, loc, "w:%s — защита документа" % _ln(el.tag),
                "; ".join(parts),
                "Хеш и соль пароля. Удаление снимает саму защиту, поэтому "
                "убирать нельзя без потери функциональности.",
                removable=False)

    for el in _iter_tag(root, "proofState"):
        parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
        rep.add(Risk.PROVENANCE, loc, "w:proofState — состояние проверки",
                "; ".join(parts),
                "Показывает, дочитал ли автор проверку орфографии.")

    if any(True for _ in _iter_tag(root, "trackChanges")):
        rep.add(Risk.PROVENANCE, loc, "w:trackChanges — запись исправлений",
                "включена",
                "Режим записи правок включён: все последующие изменения будут "
                "сохраняться вместе с именем автора.")
        rep.signal("hazard",
                   "В settings.xml включён режим записи исправлений.", "medium")

    for el in _iter_tag(root, "hdrShapeDefaults", "shapeDefaults"):
        value = _kids_attrs(el) or _text(el) or "присутствует"
        rep.add(_value_risk(value), loc,
                "w:%s — умолчания фигур" % _ln(el.tag), value,
                "Блок хранит настройки объектов и иногда идентификаторы "
                "предыдущих вставок.")

    for el in _iter_tag(root, "docVars"):
        # Значения docVar лежат в атрибутах name/val, текста у элемента нет:
        # через itertext() всё содержимое переменных теряется.
        value = _kids_attrs(el) or _text(el) or "присутствует"
        rep.add(_value_risk(value), loc, "w:docVars — переменные документа",
                value,
                "Переменные макросов, надстроек и шаблонов СЭД: там регулярно "
                "лежат ФИО согласующих, номера договоров и пути к базам.")


# === Ж. word/document.xml и его соседи ====================================

_REVISION_TAGS = ("ins", "del", "moveFrom", "moveTo", "cellIns", "cellDel",
                  "rPrChange", "pPrChange", "sectPrChange", "tblPrChange")

# Части, где лежит текст со правками. Колонтитулы, сноски и словарь автотекста
# (word/glossary/document.xml) -- такое же тело со своими авторами и rsid,
# и чистка, которая смотрит только в document.xml, их пропускает.
_BODY_PART_RE = re.compile(
    r"^word/(document\d*\.xml|glossary/document\d*\.xml|header\d*\.xml"
    r"|footer\d*\.xml|footnotes\.xml|endnotes\.xml|comments\.xml)$"
)


def _scan_body(zf, part, acc):
    """Потоковый разбор одной части тела.

    Возвращает причину обрыва (строка) или None, если часть разобрана целиком.
    Обрыв возможен по двум независимым пределам: числу элементов (MAX_ELEMENTS)
    и объёму декомпрессированных байт (MAX_STREAM, через _Capped) -- документ
    может быть маленьким по числу тегов, но содержать один огромный текстовый
    узел, и тогда предел по байтам сработает первым. Раньше в этом случае
    правки за обрывом пропадали молча: ни в findings, ни в errors следа не
    было. Оба предела теперь обязаны попасть в отчёт.
    """
    with zf.open(zf.getinfo(part), "r") as raw:
        # iterparse, а не fromstring: document.xml бывает сотни мегабайт.
        capped = _Capped(raw, MAX_STREAM)
        context = etree.iterparse(
            capped, events=("end",), resolve_entities=False,
            no_network=True, huge_tree=False, recover=True,
        )
        seen = 0
        for _, el in context:
            seen += 1
            if seen > MAX_ELEMENTS:
                return "предел %d элементов" % MAX_ELEMENTS
            tag = _ln(el.tag)
            if tag in _REVISION_TAGS:
                who = _attr(el, "author")
                when = _attr(el, "date")
                if who is not None:
                    key = (part, who)
                    acc["authors"][key] = acc["authors"].get(key, 0) + 1
                if when:
                    acc["dates"].append(when)
            elif tag == "bookmarkStart":
                if _attr(el, "name") == "_GoBack":
                    acc["goback"].add(part)
            elif tag == "permStart":
                ed = _attr(el, "ed")
                if ed:
                    acc["editors"].add((part, ed))
            for key in el.attrib:
                if _ln(key).startswith("rsid"):
                    acc["rsid"] += 1
            el.clear()
            parent = el.getparent()
            if parent is not None:
                while el.getprevious() is not None:
                    del parent[0]
        if capped.exhausted:
            return "предел потокового разбора %d МБ" % (MAX_STREAM // (1024 * 1024))
    return None


def _sec_document(zf, rep, ctx):
    targets = [n for n in ctx["names"] if _BODY_PART_RE.match(n)]
    if not targets:
        return

    acc = {"authors": {}, "dates": [], "editors": set(), "goback": set(),
           "rsid": 0}
    for part in targets:
        # Своё try на каждую часть: битый заголовок одной записи не должен
        # лишать отчёт правок из всех остальных.
        try:
            reason = _scan_body(zf, part, acc)
            if reason:
                rep.err("%s: разбор остановлен (%s), правки за этим пределом "
                        "в отчёт не попали" % (part, reason))
        except Exception as exc:  # noqa: BLE001
            rep.err("%s: %s: %s" % (part, type(exc).__name__, exc))

    ctx["rsid_attrs"] = acc["rsid"]
    main = ("word/document.xml" if "word/document.xml" in targets
            else targets[0])
    rep.add(Risk.PROVENANCE, main, "rsid-атрибутов в теле", str(acc["rsid"]),
            "Word помечает каждый абзац и прогон идентификатором сессии правки. "
            "Полное отсутствие rsid означает, что документ создан не Word. "
            "Просканировано частей тела: %d (%s)."
            % (len(targets), ", ".join(targets[:8])))

    ranked = sorted(acc["authors"].items(), key=lambda kv: -kv[1])
    for (part, who), cnt in ranked[:MAX_LISTED]:
        rep.add(Risk.IDENTITY, part, "Автор исправления (w:ins/w:del)",
                "%s (%d правок)" % (who or "<пусто>", cnt),
                "Track changes хранит ФИО или логин правившего прямо в теле "
                "документа — вне docProps.")
    if acc["dates"]:
        acc["dates"].sort()
        rep.add(Risk.PROVENANCE, main, "Даты исправлений",
                "%s … %s (%d шт.)"
                % (acc["dates"][0], acc["dates"][-1], len(acc["dates"])),
                "Точное время каждой правки.")
    for part, ed in sorted(acc["editors"])[:MAX_LISTED]:
        rep.add(Risk.IDENTITY, part, "w:permStart — разрешённый редактор", ed,
                "Имя учётной записи, которой разрешено править область.")
    for part in sorted(acc["goback"]):
        rep.add(Risk.PROVENANCE, part, "Закладка _GoBack", "присутствует",
                "Word ставит её на позицию последней правки. Факт её наличия "
                "подтверждает сохранение именно из Word.")
    if acc["authors"]:
        people = {who for _, who in acc["authors"]}
        where = sorted({part for part, _ in acc["authors"]})
        rep.signal("hazard",
                   "В теле документа сохранены исправления (track changes) от "
                   "%d автор(ов), части: %s."
                   % (len(people), ", ".join(where[:8])), "high")


# === З. комментарии, авторы, presenceInfo =================================


def _is_person_part(name):
    if name in ("word/comments.xml", "word/commentsExtended.xml",
                "word/commentsIds.xml", "word/people.xml",
                "word/commentsExtensible.xml", "ppt/commentAuthors.xml"):
        return True
    return bool(
        re.match(r"^xl/persons/.*\.xml$", name)
        or re.match(r"^xl/threadedComments/.*\.xml$", name)
        or re.match(r"^ppt/comments/.*\.xml$", name)
        or re.match(r"^ppt/.*/comments\d*\.xml$", name)
    )


def _sec_people(zf, rep, ctx):
    seen = set()
    for name in ctx["names"]:
        if not _is_person_part(name):
            continue
        try:
            root = _read_xml(zf, name)
        except Exception as exc:  # noqa: BLE001
            rep.err("%s: %s" % (name, exc))
            continue
        if root is None:
            continue
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for key, val in el.attrib.items():
                spec = PERSON_ATTRS.get(_ln(key))
                if not spec or not val:
                    continue
                risk, label = spec
                mark = (name, label, val)
                if mark in seen or len(seen) > 400:
                    continue
                seen.add(mark)
                note = "Автор комментария или участник обсуждения."
                if _ln(key) == "userId":
                    note = ("w15:presenceInfo/@userId — в подавляющем "
                            "большинстве случаев это реальный рабочий email.")
                rep.add(risk, name, label, val, note)


# === К. медиа, вложения, макросы ==========================================


def _sec_media(zf, rep, ctx):
    media = []
    embeds = []
    vba = []
    activex = []
    for name in ctx["names"]:
        low = name.lower()
        try:
            size = zf.getinfo(name).file_size
        except KeyError:
            size = 0
        if re.match(r"^(word|xl|ppt)/media/", name):
            media.append((name, size))
        elif "/embeddings/" in name:
            embeds.append((name, size))
        elif low.endswith("vbaproject.bin"):
            vba.append((name, size))
        elif "/activex/" in low:
            activex.append((name, size))

    if media:
        listed = ", ".join("%s (%d Б)" % (n.rsplit("/", 1)[-1], s)
                           for n, s in media[:20])
        rep.add(Risk.PROVENANCE, "media", "Встроенные изображения",
                "%d шт.: %s" % (len(media), listed),
                "Каждая картинка несёт собственные метаданные (EXIF, XMP, ICC, "
                "GPS). Требуется рекурсивная инспекция инспектором изображений: "
                "вызывает её диспетчер, не этот модуль.")
    for name, size in embeds[:MAX_LISTED]:
        rep.add(Risk.PROVENANCE, name, "Вложенный OLE-объект", "%d байт" % size,
                "У вложенного объекта СВОИ docProps (автор, компания, путь). "
                "Нужна рекурсивная инспекция OLE-инспектором.")
    if embeds:
        rep.signal("hazard",
                   "В пакете %d вложенных OLE-объект(ов) со своими "
                   "метаданными." % len(embeds), "medium")
    for name, size in vba:
        rep.add(Risk.PROVENANCE, name, "Проект VBA (макросы)", "%d байт" % size,
                "Хранит исходники макросов, имена модулей и нередко путь к "
                "проекту на машине автора.")
        rep.signal("hazard",
                   "Файл содержит макросы VBA (%s): активное содержимое." % name,
                   "high")
    for name, size in activex[:10]:
        rep.add(Risk.PROVENANCE, name, "ActiveX-элемент", "%d байт" % size,
                "Активное содержимое во встроенном элементе управления.")
    if activex:
        rep.signal("hazard", "В пакете есть ActiveX-элементы управления.", "medium")


# === Л. printerSettings*.bin (DEVMODE) ====================================


def _device_name(blob: bytes) -> str:
    """dmDeviceName из начала DEVMODE.

    В OOXML почти всегда лежит DEVMODEW: имя занимает 32 WCHAR = 64 байта
    в UTF-16LE и обрывается NUL. Для старого DEVMODEA (32 однобайтовых
    символа) делаем запасной разбор.
    """
    name = blob[:64].decode("utf-16le", "ignore").split("\x00", 1)[0].strip()
    if not name or not name.isprintable():
        name = blob[:32].decode("cp1251", "ignore").split("\x00", 1)[0].strip()
    if not name.isprintable():
        return ""
    return name


def _sec_printer(zf, rep, ctx):
    for name in ctx["names"]:
        if "printersettings" not in name.lower() or not name.lower().endswith(".bin"):
            continue
        # _try, а не прямой вызов: один раздутый/битый printerSettings*.bin не
        # должен гасить разбор остальных -- их в пакете может быть несколько.
        blob = _try(rep, name, _read_part, zf, name, 4 * 1024 * 1024)
        if not blob:
            continue
        device = _device_name(blob)
        rep.add(Risk.ENVIRONMENT, name, "dmDeviceName — имя принтера",
                device or "<не разобрано>",
                "Имя принтера из структуры DEVMODE. Регулярно содержит имя "
                "хоста, номер кабинета, город или название офиса — то есть "
                "физическое расположение автора.")
        rep.add(Risk.ENVIRONMENT, name, "Блок DEVMODE", "%d байт" % len(blob),
                "Полный слепок настроек печати: драйвер, лоток, формат.")


# === М. специфика Excel ===================================================


def _sec_excel(zf, rep, ctx):
    names = ctx["nameset"]

    if "xl/workbook.xml" in names:
        # _try везде ниже: части Excel-специфики независимы друг от друга,
        # и раздутый/битый workbook.xml не должен гасить разбор connections.xml,
        # pivotCache, externalLinks и customXml, которые идут следом.
        root = _try(rep, "xl/workbook.xml", _read_xml, zf, "xl/workbook.xml")
        if root is not None:
            for el in _iter_tag(root, "fileVersion"):
                parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
                rep.add(Risk.PROVENANCE, "xl/workbook.xml",
                        "fileVersion — версия и билд Excel", "; ".join(parts),
                        "rupBuild указывает точную сборку Excel, lastEdited и "
                        "lowestEdited — диапазон версий, в которых книгу правили.")
            for el in _iter_tag(root, "workbookPr"):
                parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
                if parts:
                    rep.add(Risk.PROVENANCE, "xl/workbook.xml",
                            "workbookPr", "; ".join(parts),
                            "filterPrivacy=1 означает, что книгу уже помечали "
                            "как содержащую персональные данные.")
            hidden = []
            for el in _iter_tag(root, "sheet"):
                state = (_attr(el, "state") or "").lower()
                if state in ("hidden", "veryhidden"):
                    hidden.append("%s (%s)" % (_attr(el, "name") or "?", state))
            if hidden:
                rep.add(Risk.PROVENANCE, "xl/workbook.xml", "Скрытые листы",
                        "; ".join(hidden[:MAX_LISTED]),
                        "Скрытые и особо скрытые листы обычно содержат "
                        "черновые расчёты и исходные данные.")
            for el in _iter_tag(root, "definedName"):
                value = _text(el)
                if _is_path(value) or "[" in value:
                    rep.add(Risk.ENVIRONMENT, "xl/workbook.xml",
                            "definedName %s" % (_attr(el, "name") or "?"), value,
                            "Именованный диапазон ссылается на внешнюю книгу — "
                            "нередко с полным локальным путём.")

    for name in ctx["names"]:
        if re.match(r"^xl/pivotCache/pivotCacheDefinition\d*\.xml$", name):
            root = _try(rep, name, _read_xml, zf, name)
            if root is None:
                continue
            who = _attr(root, "refreshedBy")
            when = _attr(root, "refreshedDate")
            if who is not None:
                rep.add(Risk.IDENTITY, name, "refreshedBy — кто обновлял сводную",
                        who,
                        "Классическая утечка Excel: имя человека, обновившего "
                        "кэш сводной таблицы, лежит вне docProps и переживает "
                        "чистку свойств документа.")
            if when is not None:
                rep.add(Risk.PROVENANCE, name, "refreshedDate — когда обновляли",
                        when, "Серийная дата Excel обновления кэша.")
            for key in ("createdVersion", "refreshedVersion", "minRefreshableVersion"):
                val = _attr(root, key)
                if val is not None:
                    rep.add(Risk.PROVENANCE, name, key, val,
                            "Версия Excel, работавшая со сводной.")
            for el in _iter_tag(root, "worksheetSource", "webPr"):
                parts = ["%s=%s" % (_ln(k), v) for k, v in el.attrib.items()]
                if parts:
                    rep.add(Risk.ENVIRONMENT, name,
                            "Источник кэша (%s)" % _ln(el.tag), "; ".join(parts),
                            "Откуда сводная берёт данные: книга, лист или URL.")

    if "xl/connections.xml" in names:
        root = _try(rep, "xl/connections.xml", _read_xml, zf, "xl/connections.xml")
        if root is not None:
            for el in _iter_tag(root, "connection"):
                head = "; ".join(
                    "%s=%s" % (_ln(k), v) for k, v in el.attrib.items()
                    if _ln(k) in ("name", "description", "odcFile", "type",
                                  "credentials", "sourceFile")
                )
                rep.add(Risk.ENVIRONMENT, "xl/connections.xml",
                        "Подключение к данным", head,
                        "Имя и описание внешнего подключения.")
                for kid in el.iter():
                    for attr in ("connection", "command", "url", "sourceFile",
                                 "serverCommand"):
                        val = _attr(kid, attr)
                        if val:
                            rep.add(Risk.ENVIRONMENT, "xl/connections.xml",
                                    "%s/@%s" % (_ln(kid.tag), attr), val,
                                    "Строка подключения ODBC/OLEDB или запрос: "
                                    "содержит адрес сервера, имя базы и логин.")

    for name in ctx["names"]:
        if re.match(r"^xl/externalLinks/externalLink\d*\.xml$", name):
            root = _try(rep, name, _read_xml, zf, name)
            target = None
            for el in _iter_tag(root, "externalBook") if root is not None else []:
                target = _resolve(ctx, name, _attr(el, "id"))
            sheets = []
            if root is not None:
                for el in _iter_tag(root, "sheetName"):
                    sheets.append(_attr(el, "val") or "")
            rep.add(Risk.ENVIRONMENT, name, "Связь с внешней книгой",
                    target or "цель не разрешена",
                    "Путь к внешнему файлу Excel, часто полный локальный или "
                    "UNC. Листы: %s" % (", ".join(s for s in sheets if s)[:200]))
        elif re.match(r"^xl/queryTables/.*\.xml$", name):
            rep.add(Risk.ENVIRONMENT, name, "Таблица запроса (Power Query)",
                    "присутствует",
                    "Power Query хранит источник данных и путь к нему.")
        elif name.startswith("customXml/"):
            if "itemProps" in name:
                root = _try(rep, name, _read_xml, zf, name, 2 * 1024 * 1024)
                refs = []
                if root is not None:
                    for el in _iter_tag(root, "schemaRef"):
                        refs.append(_attr(el, "uri") or "")
                rep.add(Risk.ENVIRONMENT, name, "Схема customXml",
                        "; ".join(r for r in refs if r) or "без schemaRef",
                        "Привязка к схеме SharePoint/СЭД: выдаёт портал и "
                        "тип контента организации.")
            elif re.match(r"^customXml/item\d*\.xml$", name):
                raw = _try(rep, name, _read_part, zf, name, 2 * 1024 * 1024)
                rep.add(Risk.ENVIRONMENT, name, "Данные customXml",
                        (raw or b"").decode("utf-8", "replace"),
                        "Пользовательская XML-часть: обычно колонки библиотеки "
                        "SharePoint с именами сотрудников и датами.")


# === Н. анализ отсутствующих частей =======================================


def _sec_missing(zf, rep, ctx):
    if ctx["fmt"] != "docx":
        return
    app = ctx["props"]["app"].get("Application", "")
    missing = [p for p in WORD_REQUIRED if p not in ctx["nameset"]]
    if missing:
        rep.add(Risk.PROVENANCE, "ZIP", "Отсутствуют обязательные части Word",
                ", ".join(missing),
                "Word всегда кладёт эти части в .docx. Их нет — пакет собран "
                "программно.")
    no_rsid = (ctx.get("rsid_count", 0) == 0 and ctx.get("rsid_attrs", 0) == 0)
    if no_rsid:
        rep.add(Risk.PROVENANCE, "word/", "rsid отсутствуют полностью", "0",
                "Ни одного идентификатора сессии правки: Word такого не пишет.")

    if missing or no_rsid:
        conf = "high" if (missing and no_rsid) else "medium"
        why = []
        if missing:
            why.append("нет частей: %s" % ", ".join(missing))
        if no_rsid:
            why.append("нет ни одного rsid")
        detail = ("Пакет собран библиотекой (python-docx/docxtpl или генератором "
                  "ИИ), а не Word: %s." % "; ".join(why))
        if app:
            detail += " При этом app.xml заявляет Application=%s." % app
        rep.signal("ai", detail, conf)


# === О. выводы и противоречия =============================================


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _sec_conclusions(zf, rep, ctx):
    core = ctx["props"]["core"]
    app = ctx["props"]["app"]
    appname = app.get("Application", "")
    words = _to_int(app.get("Words")) or 0
    total = _to_int(app.get("TotalTime"))
    rev = _to_int(core.get("revision"))

    if appname:
        rep.signal("producer",
                   "Application=%s, AppVersion=%s"
                   % (appname, app.get("AppVersion", "?")), "high")

    ms = any(w in appname.lower()
             for w in ("microsoft", "word", "excel", "powerpoint"))

    if ms and ctx.get("zip_real"):
        rep.signal("inconsistent",
                   "app.xml заявляет продукт Microsoft (%s), но записи ZIP "
                   "помечены реальным временем (последняя %s) вместо "
                   "1980-01-01. Office так не сохраняет."
                   % (appname, ctx.get("zip_real_max", "?")), "high")
        rep.signal("ai",
                   "Пакет пересобран библиотекой поверх шаблона Office: "
                   "реальное время сохранения утекло через таймстемпы ZIP.",
                   "high")

    created = core.get("created", "")
    modified = core.get("modified", "")
    if created and modified and created == modified:
        rep.signal("inconsistent",
                   "dcterms:created и dcterms:modified совпадают до секунды "
                   "(%s): документ не редактировали после создания либо даты "
                   "проставлены программно." % created, "medium")

    if words >= 200 and (total is None or total == 0):
        rep.signal("inconsistent",
                   "В документе %d слов, а TotalTime %s: время редактирования "
                   "обнулено или не проставлялось."
                   % (words, "отсутствует" if total is None else "равен 0"),
                   "medium")

    if rev == 1 and words >= 200:
        rep.signal("inconsistent",
                   "cp:revision=1 при объёме %d слов: документ такого размера "
                   "за одно сохранение не делают." % words, "medium")

    scrub_bits = []
    if "docProps/core.xml" in ctx["nameset"]:
        if not core.get("creator"):
            scrub_bits.append("пустой dc:creator")
        if not core.get("lastModifiedBy"):
            scrub_bits.append("пустой cp:lastModifiedBy")
    if not app.get("Company") and "Company" in app:
        scrub_bits.append("пустой Company")
    if len(scrub_bits) >= 2:
        rep.signal("scrubbed",
                   "Признаки предыдущей чистки: %s. Проверьте остальные "
                   "площадки (ZIP, settings.xml, rels) — их обычно забывают."
                   % ", ".join(scrub_bits), "medium")


# === необязательное обогащение exiftool ===================================


def _exiftool_path():
    """exiftool строго опционален: нет — работаем без него и молчим."""
    found = shutil.which("exiftool")
    if found:
        return found
    local = os.path.join(os.getcwd(), "tools", "exiftool.exe")
    return local if os.path.isfile(local) else None


def _sec_exiftool(path, rep, ctx):
    exe = _exiftool_path()
    if not exe:
        return
    try:
        out = subprocess.run(
            [exe, "-j", "-a", "-G1", "-n", str(path)],
            capture_output=True, timeout=25, shell=False,
        )
    except Exception as exc:  # noqa: BLE001
        rep.err("exiftool не отработал: %s" % exc)
        return
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return
    if not data:
        return
    skip_groups = ("ExifTool", "System", "File")
    added = 0
    for key, value in data[0].items():
        if added >= MAX_LISTED:
            break
        group, _, tag = key.partition(":")
        if not tag or group in skip_groups:
            continue
        text = str(value)
        risk = Risk.PROVENANCE
        # \b, а не голая подстрока: иначе "CreatorTool" (имя ПРИЛОЖЕНИЯ, тег
        # XMP CreatorTool) ловится по "creator" и уезжает в IDENTITY, хотя
        # это чистый PROVENANCE.
        if _emails(text) or re.search(r"\b(author|creator|owner|manager)\b", tag, re.I):
            risk = Risk.IDENTITY
        elif _is_path(text):
            risk = Risk.ENVIRONMENT
        rep.add(risk, "exiftool:%s" % group, tag, text,
                "Дополнено exiftool (необязательное обогащение).")
        added += 1


# === точка входа ==========================================================


def inspect_ooxml(path, fmt="docx") -> Report:
    """Инспектировать OOXML-пакет. Никогда не бросает наружу.

    Любая беда с частью пакета попадает в report.errors, а не в трассировку:
    нам присылают и повреждённые, и намеренно кривые файлы.
    """
    rep = Report(path=str(path), fmt=fmt)
    try:
        rep.size = os.path.getsize(path)
    except OSError as exc:
        rep.err("Не удалось получить размер файла: %s" % exc)

    try:
        zf = zipfile.ZipFile(path, "r")   # строго на чтение
    except Exception as exc:  # noqa: BLE001
        rep.err("ZIP-контейнер не открывается: %s: %s" % (type(exc).__name__, exc))
        return rep

    ctx = {"fmt": fmt, "names": [], "nameset": set(), "safe": True,
           "props": {"core": {}, "app": {}, "custom": []}, "rels": {}}
    try:
        with zf:
            _section(rep, "ZIP-контейнер", _sec_zip, zf, rep, ctx)
            if ctx.get("safe", True):
                ctx["props"] = parse_props(zf)
                _section(rep, "docProps/core.xml", _sec_core, zf, rep, ctx)
                _section(rep, "docProps/app.xml", _sec_app, zf, rep, ctx)
                _section(rep, "docProps/custom.xml", _sec_custom, zf, rep, ctx)
                _section(rep, "Превью", _sec_thumbnail, zf, rep, ctx)
                _section(rep, "Отношения .rels", _sec_rels, zf, rep, ctx)
                _section(rep, "word/settings.xml", _sec_settings, zf, rep, ctx)
                _section(rep, "Тело документа", _sec_document, zf, rep, ctx)
                _section(rep, "Комментарии и авторы", _sec_people, zf, rep, ctx)
                _section(rep, "Медиа и вложения", _sec_media, zf, rep, ctx)
                _section(rep, "Настройки принтера", _sec_printer, zf, rep, ctx)
                _section(rep, "Специфика Excel", _sec_excel, zf, rep, ctx)
                _section(rep, "Отсутствующие части", _sec_missing, zf, rep, ctx)
                _section(rep, "Выводы", _sec_conclusions, zf, rep, ctx)
    except Exception as exc:  # noqa: BLE001
        rep.err("Непредвиденный сбой инспекции: %s: %s" % (type(exc).__name__, exc))

    _section(rep, "exiftool", _sec_exiftool, path, rep, ctx)
    return rep


# === самопроверка =========================================================


def _build_sample(dest):
    """Собрать синтетический .docx с заранее известными значениями."""
    import datetime

    now = datetime.datetime.now().timetuple()[:6]
    devmode = "HP LaserJet M404 (BUH-01)".encode("utf-16le")
    devmode = devmode + b"\x00" * (64 - len(devmode)) + b"\x00" * 156

    parts = [
        ("[Content_Types].xml",
         b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
         b'/package/2006/content-types"/>'),
        ("_rels/.rels",
         b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
         b'.org/package/2006/relationships"><Relationship Id="rId1" Type="t" '
         b'Target="word/document.xml"/></Relationships>'),
        ("docProps/core.xml",
         '<?xml version="1.0"?><cp:coreProperties '
         'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
         'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
         'xmlns:dcterms="http://purl.org/dc/terms/">'
         '<dc:creator>Иванов И.И.</dc:creator>'
         '<cp:lastModifiedBy>petrov@company.ru</cp:lastModifiedBy>'
         '<dcterms:created>2024-03-01T10:00:00Z</dcterms:created>'
         '<dcterms:modified>2024-03-01T10:00:00Z</dcterms:modified>'
         '<cp:revision>1</cp:revision><dc:title></dc:title>'
         '<dc:language>ru-RU</dc:language>'
         '</cp:coreProperties>'),
        ("docProps/app.xml",
         '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats'
         '.org/officeDocument/2006/extended-properties">'
         '<Application>Microsoft Office Word</Application>'
         '<AppVersion>16.0000</AppVersion><Company>ООО Ромашка</Company>'
         '<Manager>Сидоров С.С.</Manager><TotalTime>0</TotalTime>'
         '<Words>1500</Words><Template>Normal.dotm</Template>'
         '<TitlesOfParts>Отчёт за квартал</TitlesOfParts></Properties>'),
        ("docProps/custom.xml",
         '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats'
         '.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas'
         '.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
         '<property fmtid="{f}" pid="2" name="MSIP_Label_9f-1_SetBy">'
         '<vt:lpwstr>a.smirnova@company.ru</vt:lpwstr></property>'
         '<property fmtid="{f}" pid="3" name="MSIP_Label_9f-1_SiteId">'
         '<vt:lpwstr>72f988bf-86f1-41af-91ab-2d7cd011db47</vt:lpwstr></property>'
         '<property fmtid="{f}" pid="4" name="ContentTypeId">'
         '<vt:lpwstr>0x0101009ABC</vt:lpwstr></property></Properties>'),
        ("word/document.xml",
         '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats'
         '.org/wordprocessingml/2006/main"><w:body>'
         '<w:p w:rsidR="00A12B34" w:rsidRDefault="00A12B34">'
         '<w:ins w:id="1" w:author="Кузнецова А.В." w:date="2024-03-01T09:30:00Z">'
         '<w:r><w:t>правка</w:t></w:r></w:ins></w:p>'
         '<w:bookmarkStart w:id="2" w:name="_GoBack"/>'
         '</w:body></w:document>'),
        ("word/settings.xml",
         '<?xml version="1.0"?><w:settings xmlns:w="http://schemas.openxmlformats'
         '.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats'
         '.org/officeDocument/2006/relationships">'
         '<w:attachedTemplate r:id="rId1"/>'
         '<w:documentProtection w:edit="readOnly" w:hash="Aq3f==" w:salt="Zz09"/>'
         '<w:rsids><w:rsidRoot w:val="00A12B34"/><w:rsid w:val="00A12B34"/>'
         '<w:rsid w:val="00B55C21"/></w:rsids></w:settings>'),
        ("word/_rels/settings.xml.rels",
         '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
         '.org/package/2006/relationships"><Relationship Id="rId1" Type="t" '
         'Target="file:///C:/Users/testuser/AppData/Roaming/Microsoft/'
         'Templates/Otchet.dotm" TargetMode="External"/></Relationships>'),
        ("word/people.xml",
         '<?xml version="1.0"?><w15:people xmlns:w15="http://schemas.microsoft'
         '.com/office/word/2012/wordml"><w15:person w15:author="Кузнецова А.В.">'
         '<w15:presenceInfo w15:providerId="AD" '
         'w15:userId="a.kuznetsova@company.ru"/></w15:person></w15:people>'),
        ("word/printerSettings/printerSettings1.bin", devmode),
        ("word/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40),
        ("word/styles.xml", b'<?xml version="1.0"?><x/>'),
    ]

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts:
            if isinstance(body, str):
                body = body.encode("utf-8")
            info = zipfile.ZipInfo(name, date_time=now)  # реальное время => сигнал
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)
    return dest


def demo():
    """Прогон на синтетическом .docx с заранее известными значениями."""
    import tempfile

    tmp = tempfile.mkdtemp(prefix="ooxml_insp_")
    sample = _build_sample(os.path.join(tmp, "sample.docx"))
    rep = inspect_ooxml(sample, "docx")

    blob = " | ".join("%s=%s" % (f.label, f.value) for f in rep.findings)
    for expect in ("Иванов И.И.", "petrov@company.ru", "ООО Ромашка",
                   "Сидоров С.С.", "a.smirnova@company.ru",
                   "72f988bf-86f1-41af-91ab-2d7cd011db47", "ContentTypeId",
                   "HP LaserJet M404 (BUH-01)", "Otchet.dotm",
                   "a.kuznetsova@company.ru", "Кузнецова А.В.",
                   "_GoBack", "00A12B34"):
        assert expect in blob, "не найдено: %s" % expect
    kinds = {s.kind for s in rep.signals}
    assert "inconsistent" in kinds and "producer" in kinds, kinds
    assert not rep.errors, rep.errors

    print("Файл: %s  (%d байт, fmt=%s)" % (rep.path, rep.size, rep.fmt))
    print("Частей ZIP: %d | находок: %d (значимых: %d) | сигналов: %d | ошибок: %d"
          % (len(rep.parts), len(rep.findings), rep.sensitive,
             len(rep.signals), len(rep.errors)))
    print("Пример записи parts: %s" % (rep.parts[0],))
    print("-" * 78)
    for risk, items in rep.by_risk().items():
        if not items:
            continue
        print("[%s]" % risk.value.upper())
        for f in items:
            print("  %-46s %s%s" % (
                f.label[:46], f.value or "<пусто>",
                "" if f.removable else "  (убрать нельзя)"))
    print("-" * 78)
    for s in rep.signals:
        print("SIGNAL %-13s %-6s %s" % (s.kind, s.confidence, s.detail))
    print("OK: самопроверка пройдена")


if __name__ == "__main__":
    demo()
