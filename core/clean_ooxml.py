# -*- coding: utf-8 -*-
"""Чистка OOXML-пакетов: docx, xlsx, pptx (фаза 2).

    clean_ooxml(src, dst, profile=Profile.STEALTH, keep=None) -> CleanResult
    verify_ooxml(src, dst) -> dict

Только stdlib (expat, zipfile, struct) плюс чистильщики картинок и PDF проекта для
рекурсии. python-docx и openpyxl НЕ используются: они пересобирают пакет и теряют
части, которых не понимают. Распаковкой и упаковкой занимается core/zipfix.py, здесь
вычисляется только то, что положить в replace и drop.

Инспектор -- оракул: после чистки диспетчер прогоняет inspect_file(dst), и именно он
решает, чисто ли. Наши списки действий доказательством не служат.

ГЛАВНЫЙ ПРИНЦИП: ПРАВИТЬ, А НЕ ПЕРЕСОБИРАТЬ -- и это касается не только ZIP, но и XML.
Часть, которую мы меняем, правится ПОБАЙТОВО: expat сообщает смещение каждого тега, мы
вырезаем или переписываем только нужные отрезки (значение атрибута, текст элемента, сам
элемент), а всё остальное остаётся байтами исходника. Сериализация через lxml/ElementTree
давала бы отпечаток обработки на каждой изменённой части: lxml переставляет объявления
xmlns перед атрибутами (у Word `mc:Ignorable` стоит среди них), заменяет `\\r\\n` на `\\n`,
пишет `<a/>` вместо `<a></a>`. Проверено опытом на реальных docx/xlsx: у Word
нетронутые части после круга lxml совпали бы, а у Excel -- нет (корень каждой части и вложенные
<ext uri=... xmlns:...> в workbook.xml).

Самопроверка и метрики:
    PYTHONIOENCODING=utf-8 python -m core.clean_ooxml                  синтетика, ~1300 проверок
    PYTHONIOENCODING=utf-8 python -m core.clean_ooxml --smoke          фикстуры проекта и патологии
    PYTHONIOENCODING=utf-8 python -m core.clean_ooxml --real файл...   настоящие файлы (содержимое
                                                                       и персональные данные не печатаются)

РЕШЕНИЯ (каждое осознанное):

* STEALTH ОСТАВЛЯЕТ Application и AppVersion, rsid, структуру пакета, даты core.xml,
  TotalTime, cp:revision, статистику app.xml, dc:language, тему/шрифты/стили. Это правда
  о файле, а пустой или подменённый Application при сохранённой структуре Word --
  мгновенный признак чистки. PARANOID убирает всё из PROVENANCE и честно говорит в note,
  что результат выглядит обработанным.
* dc:creator и cp:lastModifiedBy -- ОБНУЛЯЮТСЯ, элементы остаются пустыми (так Word пишет
  при незаполненном имени). Отсутствие самих элементов -- признак. ПОБОЧНЫЙ ЭФФЕКТ,
  который нельзя устранить из этого модуля: инспектор считает пару пустых creator и
  lastModifiedBy сигналом kind="scrubbed" (insp_ooxml._sec_conclusions, порог в два
  признака). Это противоречие между эвристикой инспектора и самим профилем STEALTH.
* Даты НЕ подменяются. core.xml: dcterms:created/modified остаются как есть (STEALTH)
  или убираются (PARANOID). Даты правок и комментариев убираются (атрибут w:date
  необязателен); обязательные по схеме (dT у threadedComment, created у современных
  комментариев PowerPoint) остаются и помечены KEPT.
* Авторы правок и комментариев -> «Author». Это то, что пишет сам Word при включённой
  «Удалять личные данные при сохранении». Правки выбрасывать нельзя: это контент.
* ZIP: незатронутые записи копирует zipfix сырыми байтами. Единственное исключение --
  таймстемпы: если у записей реальное время (файл собран библиотекой), а Application
  заявляет Microsoft или не заявлен вовсе, они приводятся к 1980-01-01 00:00:00, как у
  Word, -- иначе время работы утекает через контейнер даже при идеальном core.xml
  (инспектор считает это ENVIRONMENT). У файла от Word таймстемпы и так 1980-01-01 и не
  меняются. Для чужих приложений (LibreOffice и т.п.) реальное время оставляется.
* Скрытые байты вне записей ZIP (голова, щели, комментарий архива) убираются
  (strip_slack=True): у файла от Office их нет, это классический тайник.
* Из пакета убираются только перечисленные части: превью, people.xml, printerSettings,
  LabelInfo.xml, custom.xml/app.xml/core.xml (лишь если не осталось ни одного свойства: такие
  пустые части Office не пишет, а инспектор считает их ошибкой разбора), .rels, ставшие
  пустыми, и по профилю PARANOID -- vbaProject и OLE-вложения; с force_signed -- подписи.
  Из [Content_Types].xml уходят только Override удалённых частей и Default расширений,
  которых в пакете больше нет.
* Внешние пути в .rels (file:///, C:\, UNC) заменяются ИМЕНЕМ ФАЙЛА: на такие отношения
  опираются формулы и объекты, поэтому убрать отношение целиком нельзя. Ссылки mailto и
  шаблон/источник слияния убираются вместе с элементом, который на них ссылался. Эти
  правки идут вместе со ссылкой на отношение из XML владельца (w:hyperlink снимается, текст
  остаётся; pageSetup теряет r:id); если безопасно убрать ссылку нечем, часть остаётся и
  это записано как UNREMOVABLE.
* Часть, которую надо править, но которая не разбирается, -- ФЕЙЛ: ok=False, файл не
  выдан. Частично чистый файл хуже отказа (как и в clean_pdf).
* Подписанный пакет (_xmlsignatures/) без force_signed отклоняется: любая правка ломает
  подпись. С force_signed подписи удаляются.
* Всё, чего убрать нельзя, честно помечается Act.UNREMOVABLE: вложенные OLE-объекты в
  STEALTH (внутри свои docProps, чистки OLE2 пока нет), Power Query DataMashup,
  картинки в форматах, которые clean_image не берёт.

Приватность: логов нет, в errors -- тексты без путей. Значения из файлов недоверенные:
через model.clip(), в shell не попадают. Вход не модифицируется никогда: sha256 до и
после; выход пишется во временный файл рядом и переименовывается после проверки.

keep -- whitelist пользователя: множество строк, сравниваются без регистра с коротким
ключом поля или с именем части: "dc:title", "Company", "TotalTime", "thumbnail",
"people", "printerSettings", "docProps/core.xml". Картинки понимают keep={"icc"}.
"""

from __future__ import annotations

import hashlib
import io
import os
import posixpath
import re
import shutil
import struct
import sys
import tempfile
import urllib.parse
import xml.parsers.expat as expat
import zipfile

from . import sniff, zipfix
from .cleanmodel import Act, CleanResult, Profile
from .insp_ooxml import (_PERSON_NAME_RE, _SP_NAMES, _device_name, _emails,
                         _has_path)
from .model import clip

# --- пределы ----------------------------------------------------------------
MAX_INPUT = 512 * 1024 * 1024        # как у zipfix и диспетчера
MAX_XML = 128 * 1024 * 1024          # одна XML-часть, которую правим
MAX_NESTED = 64 * 1024 * 1024        # одно вложение (картинка, встроенный файл)
MAX_DEPTH = 2                        # как MAX_RECURSE_DEPTH у диспетчера инспекции
MAX_ACTS_PER_PART = 40               # однотипных действий на часть, дальше -- итог одной строкой
MAX_XML_DEPTH = 3000                 # вложенность элементов: у Office десятки, больше -- враждебный файл

W_NSES = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "http://purl.oclc.org/ooxml/wordprocessingml/main"}
R_NSES = {"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "http://purl.oclc.org/ooxml/officeDocument/relationships"}
EPOCH = (1980, 1, 1, 0, 0, 0)        # что Word ставит всем записям ZIP
AUTHOR = "Author"                    # так Word подписывает правки при «удалять личные данные»

_ANON_ATTRS = {"displayName", "name", "author", "lastModifiedBy"}   # -> Author (части с комментариями)
_APP_FLAGS = {"DocSecurity", "ScaleCrop", "LinksUpToDate", "SharedDoc", "HyperlinksChanged"}

# Части с текстом правок: колонтитулы, сноски, комментарии, словарь автотекста. Шире, чем у
# инспектора: плюс commentsExtensible.xml (там w16cex:dateUtc).
_BODY_RE = re.compile(
    r"^word/(document\d*\.xml|glossary/document\d*\.xml|header\d*\.xml|footer\d*\.xml"
    r"|footnotes\.xml|endnotes\.xml|comments\.xml|commentsExtensible\.xml)$")
# Части, где авторы лежат атрибутами и не-Word (у Word своё: _BODY_RE).
_PERSON_RE = re.compile(
    r"^(xl/persons/.+\.xml|xl/threadedComments/.+\.xml|ppt/commentAuthors\.xml|ppt/authors\.xml"
    r"|ppt/comments/.+\.xml|ppt/[^/]+/comments\d*\.xml)$")
_XLCOMMENTS_RE = re.compile(r"^xl/comments\d*\.xml$")
_PIVOT_RE = re.compile(r"^xl/pivotCache/pivotCacheDefinition\d*\.xml$")
_CUSTOMXML_RE = re.compile(r"^customXml/item\d*\.xml$")

# Корни customXml, которые Office кладёт из SharePoint/СЭД: колонки библиотеки, схемы
# типов контента, шаблоны форм. Содержимое обнуляется до пустого корня.
_SP_ROOT_NS = ("http://schemas.microsoft.com/office/2006/metadata/properties",
               "http://schemas.microsoft.com/office/2006/metadata/contentType",
               "http://schemas.microsoft.com/office/2006/metadata/longProperties",
               "http://schemas.microsoft.com/office/2006/metadata/customXsn",
               "http://schemas.microsoft.com/sharepoint/v3/contenttype/forms")
_MACRO_CT = {
    "application/vnd.ms-word.document.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
}
# Атрибуты парольной защиты: хеш и всё, что нужно для его подбора.
_HASH_ATTRS = {"hash", "salt", "cryptProviderType", "cryptAlgorithmClass", "cryptAlgorithmType",
               "cryptAlgorithmSid", "cryptSpinCount", "cryptProvider", "algIdExt",
               "algIdExtSource", "cryptProviderTypeExt", "cryptProviderTypeExtSource",
               "hashValue", "saltValue", "spinCount", "algorithmName",
               "workbookPassword", "workbookPasswordCharacterSet", "workbookHashValue",
               "workbookSaltValue", "workbookSpinCount", "workbookAlgorithmName",
               "revisionsPassword", "revisionsPasswordCharacterSet", "revisionsHashValue",
               "revisionsSaltValue", "revisionsSpinCount", "revisionsAlgorithmName"}
# Ключи строки подключения ODBC/OLEDB, которые несут сервер, базу и учётную запись.
_CONN_SECRET = {"server", "data source", "address", "addr", "network address", "initial catalog",
                "database", "dbq", "uid", "user id", "user", "pwd", "password", "dsn",
                "workstation id", "host", "hostname", "port", "file", "filedsn"}
_CONN_SOURCE_ATTRS = ("connection", "command", "url", "sourceFile", "serverCommand")


class _Bad(Exception):
    """Сбой секции или отказ: текст по-русски и без путей."""


class _Refuse(Exception):
    """Чистка отклонена целиком (подписанный пакет и т.п.): ok=False."""


# ============================================================================
#  Слой XML: правка по байтам
# ============================================================================
# Регулярка стартового тега: кавычки в значениях атрибутов учитываются (в них бывает '>').
_TAG_RE = re.compile(rb"""<[^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>""")
_ATTR_RE = re.compile(rb"""(\s+)([^\s=/>]+)(\s*=\s*)("([^"]*)"|'([^']*)')""")
_NAME_RE = re.compile(rb"<[^\s/>]+")
_ENC_RE = re.compile(rb"""<\?xml[^>]*encoding\s*=\s*["']([^"']+)["']""")


class _N:
    """Элемент: границы тегов в байтах исходника, атрибуты в порядке документа.

    a..b -- стартовый тег, c..d -- конечный (c == -1 у пустого тега <x/>)."""

    __slots__ = ("ns", "name", "attrs", "a", "b", "c", "d", "parent", "kids", "_txt", "_sp")

    def __init__(self, ns, name, attrs, a, b):
        self.ns, self.name, self.attrs, self.a, self.b = ns, name, attrs, a, b
        self.c = self.d = -1
        self.parent = None
        self.kids = ()           # списки заводятся по требованию: на миллионе элементов это память
        self._txt = ()
        self._sp = None

    @property
    def empty(self):
        return self.c < 0

    @property
    def end(self):
        return self.b if self.c < 0 else self.d

    def get(self, local, ns=None):
        """Значение первого атрибута с таким локальным именем (и пространством, если задано:
        строка, множество или False -- «без пространства»)."""
        for ans, aloc, val in self.attrs:
            if aloc != local:
                continue
            if ns is None or (ns is False and not ans) or (ns and ans == ns) \
                    or (isinstance(ns, (set, frozenset)) and ans in ns):
                return val
        return None

    def text(self):
        return "".join(self._txt)


def _enc_ok(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff"):
        return False
    m = _ENC_RE.match(raw[3:] if raw[:3] == b"\xef\xbb\xbf" else raw)
    return not m or m.group(1).lower().replace(b"_", b"-") in (b"utf-8", b"utf8")


def _parse(raw, want=None, text=False):
    """Разбор expat'ом: список элементов (только подходящих под want) с границами тегов.

    Безопасность: DOCTYPE и объявления сущностей в OOXML не бывают -- их наличие отказ,
    внешние сущности expat не разрешает без обработчика. Бросает _Bad."""
    if not _enc_ok(raw):
        raise _Bad("кодировка части не UTF-8: правка по байтам невозможна")
    p = expat.ParserCreate(namespace_separator=" ")
    p.ordered_attributes = True
    p.buffer_text = True
    stack, nodes = [], []
    intern = sys.intern

    def no_dtd(*_a):
        raise _Bad("в части есть DOCTYPE или объявление сущности: в OOXML их не бывает")

    p.StartDoctypeDeclHandler = no_dtd
    p.EntityDeclHandler = no_dtd

    def start(name, attrs):
        if len(stack) > MAX_XML_DEPTH:
            raise _Bad("вложенность XML глубже %d: так файлы Office не пишутся" % MAX_XML_DEPTH)
        ns, _, loc = name.rpartition(" ")
        al = []
        for i in range(0, len(attrs), 2):
            ans, _, aloc = attrs[i].rpartition(" ")
            al.append((intern(ans), intern(aloc), attrs[i + 1]))
        ns, loc = intern(ns), intern(loc)
        node = None
        if want is None or want(ns, loc, al):
            a = p.CurrentByteIndex
            m = _TAG_RE.match(raw, a)
            if m is None:
                raise _Bad("не удалось найти границы тега")
            node = _N(ns, loc, al, a, m.end())
            if raw[node.b - 2:node.b] == b"/>":
                node.c = node.d = -1
            else:
                node.c = 0          # временно: «не пустой»; границы конца пишет end()
            for anc in reversed(stack):
                if anc is not None:
                    node.parent = anc
                    if not anc.kids:
                        anc.kids = []
                    anc.kids.append(node)
                    break
            nodes.append(node)
        stack.append(node)

    def end(_name):
        node = stack.pop()
        if node is not None and node.c >= 0:
            node.c = p.CurrentByteIndex
            node.d = raw.index(b">", node.c) + 1

    def chars(data):
        for s in stack:
            if s is not None:
                if not s._txt:
                    s._txt = []
                s._txt.append(data)

    p.StartElementHandler = start
    p.EndElementHandler = end
    if text:
        p.CharacterDataHandler = chars
    try:
        p.Parse(raw, True)
    except expat.ExpatError as exc:
        raise _Bad("XML не разбирается: %s" % expat.ErrorString(exc.code)) from None
    return nodes


def _well_formed(raw):
    """Независимая проверка результата правки: expat без обработчиков и lxml со строгим парсером."""
    if not _enc_ok(raw):
        raise _Bad("после правки кодировка части не UTF-8")
    try:
        expat.ParserCreate().Parse(raw, True)
    except expat.ExpatError as exc:
        raise _Bad("после правки XML не разбирается: %s" % expat.ErrorString(exc.code)) from None
    try:
        from lxml import etree
        kw = dict(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False, recover=False)
        if len(raw) <= 4 * 1024 * 1024:
            etree.fromstring(raw, parser=etree.XMLParser(**kw))
        else:                                  # потоково: дерево на 100 МБ не строим
            for _ev, el in etree.iterparse(io.BytesIO(raw), events=("end",), **kw):
                el.clear()
                parent = el.getparent()
                while parent is not None and el.getprevious() is not None:
                    del parent[0]
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001
        raise _Bad("после правки XML не проходит строгий разбор (%s)" % type(exc).__name__) from None


def _esc_attr(s, quote):
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
    return s.replace('"' if quote == 34 else "'", "&quot;" if quote == 34 else "&apos;")


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Patch:
    """Набор правок одной XML-части. Правки накапливаются как (начало, конец, байты) и
    применяются разом в finish(); всё вне этих отрезков -- байты исходника."""

    def __init__(self, raw, want=None, text=False):
        self.raw = raw
        self.nodes = _parse(raw, want, text)
        self.edits = []

    def by(self, local, ns=None):
        return [n for n in self.nodes if n.name == local and (
            ns is None or n.ns == ns or (isinstance(ns, (set, frozenset)) and n.ns in ns))]

    def _spans(self, n):
        """Атрибуты стартового тега с байтовыми границами. Соответствие с n.attrs -- по
        порядку: expat отдаёт атрибуты в порядке документа, xmlns-объявления не отдаёт."""
        if n._sp is None:
            tag = self.raw[n.a:n.b]
            pos = _NAME_RE.match(tag).end()
            found = [m for m in _ATTR_RE.finditer(tag, pos)
                     if m.group(2) != b"xmlns" and not m.group(2).startswith(b"xmlns:")]
            if len(found) != len(n.attrs):
                raise _Bad("не удалось сопоставить атрибуты тега")
            n._sp = [(ans, aloc, val, n.a + m.start(), n.a + m.end(), n.a + m.start(4) + 1,
                      n.a + m.end(4) - 1)
                     for (ans, aloc, val), m in zip(n.attrs, found)]
        return n._sp

    def _find(self, n, key):
        ns, loc = key if isinstance(key, tuple) else (None, key)
        for sp in self._spans(n):
            if sp[1] == loc and (ns is None or sp[0] == ns or
                                 (isinstance(ns, (set, frozenset)) and sp[0] in ns)):
                return sp
        return None

    def set_attr(self, n, key, value):
        sp = self._find(n, key)
        if sp is None:
            raise _Bad("атрибута %s нет в теге" % (key[1] if isinstance(key, tuple) else key))
        q = self.raw[sp[5] - 1]
        self.edits.append((sp[5], sp[6], _esc_attr(value, q).encode("utf-8")))

    def del_attr(self, n, key):
        sp = self._find(n, key)
        if sp is None:
            return False
        self.edits.append((sp[3], sp[4], b""))
        return True

    def set_text(self, n, text):
        """Заменить содержимое между тегами. Пустой тег <x/> превращается в <x>текст</x>."""
        body = _esc_text(text).encode("utf-8")
        if n.empty:
            if not text:
                return
            qn = _NAME_RE.match(self.raw[n.a:n.b]).group(0)[1:]
            self.edits.append((n.a, n.b, self.raw[n.a:n.b - 2].rstrip() + b">" + body
                               + b"</" + qn + b">"))
        else:
            self.edits.append((n.b, n.c, body))

    def remove(self, n):
        self.edits.append((n.a, n.end, b""))

    def unwrap(self, n):
        """Убрать сам элемент, оставив содержимое (w:hyperlink)."""
        if n.empty:
            self.remove(n)
        else:
            self.edits.append((n.a, n.b, b""))
            self.edits.append((n.c, n.d, b""))

    def finish(self):
        out, pos = [], 0
        prev = None
        for a, b, rep in sorted(set(self.edits), key=lambda e: (e[0], -e[1])):
            if prev is not None and a < prev[1]:
                if b <= prev[1] and not prev[2]:
                    continue                      # правка внутри удалённого отрезка
                raise _Bad("правки XML пересекаются")
            out.append(self.raw[pos:a])
            out.append(rep)
            pos = b
            prev = (a, b, rep)
        out.append(self.raw[pos:])
        res = b"".join(out)
        _well_formed(res)
        return res


# ============================================================================
#  Модель пакета: имена частей, отношения, удаление частей со ссылками
# ============================================================================


def _type_tail(typ):
    return typ.rsplit("/", 1)[-1]


def _rels_owner(rn):
    """word/_rels/document.xml.rels -> word/document.xml; _rels/.rels -> "" (весь пакет)."""
    head, _, base = rn.rpartition("/")
    if head == "_rels" or head.endswith("/_rels"):
        head = head[:-5].rstrip("/")
    base = base[:-5] if base.endswith(".rels") else base
    return "/".join(x for x in (head, base) if x)


def _rels_name(part):
    """word/document.xml -> word/_rels/document.xml.rels; "" -> _rels/.rels."""
    head, _, base = part.rpartition("/")
    return (head + "/" if head else "") + "_rels/" + base + ".rels"


def _resolve(rn, target):
    """Имя части, на которую указывает внутренний Target отношения из rn. None -- не разрешается."""
    base = posixpath.dirname(_rels_owner(rn))
    t = urllib.parse.unquote(target.replace("\\", "/"))
    p = posixpath.normpath(t.lstrip("/") if t.startswith("/") else posixpath.join(base, t))
    return None if p.startswith("..") or p in (".", "") else p


def _relativize(target):
    """file:///C:/Users/x/Dir/book.xlsx#frag -> book.xlsx#frag. Пусто, если имени файла нет
    (каталог или голое имя общего ресурса): тогда ссылку придётся убирать совсем."""
    t, sep, frag = target.partition("#")
    if not t.strip() or t.strip()[-1] in "/\\":
        return ""
    seg = [s for s in re.split(r"[\\/]+", t) if s]
    if not seg or "." not in seg[-1] or re.fullmatch(r"[A-Za-z]:", seg[-1]):
        return ""
    return seg[-1] + (sep + frag if sep else "")


def _is_pathlike(target):
    return _has_path(target) or target.lower().startswith("file:")


def _ole_target(n):
    """Что вырезать вместе со ссылкой на встроенный объект: w:object (Word) или graphicFrame
    (PowerPoint), а если он обёрнут в mc:AlternateContent -- вся обёртка (там дубль в Fallback)."""
    a, hops = n, 0
    while a is not None and hops < 6:
        a = a.parent
        hops += 1
        if a is not None and ((a.name == "object" and a.ns in W_NSES) or a.name == "graphicFrame"):
            p = a.parent
            if p is not None and p.name in ("Choice", "Fallback") and p.parent is not None \
                    and p.parent.name == "AlternateContent":
                return p.parent
            return a
    return None


def _ref_action(n, aloc):
    """Что делать с элементом, ссылающимся на удаляемое отношение. None -- не умеем безопасно."""
    nm = n.name
    if nm == "hyperlink":
        return "unwrap" if n.ns in W_NSES else "remove"
    if nm in ("hlinkClick", "hlinkHover", "hlinkMouseOver", "attachedTemplate"):
        return "remove"
    if nm == "pageSetup" and aloc == "id":
        return "dropattr"
    if nm in ("OLEObject", "oleObj", "oleObject") and _ole_target(n) is not None:
        return "container"
    return None


_SCRUB_WANT = {"hyperlinks", "hyperlink", "oleObjects", "object", "graphicFrame",
               "AlternateContent", "Choice", "Fallback"}


class _Pkg:
    """Состояние чистки одного пакета: исходный ZIP на чтение, накопленные замены и удаления."""

    def __init__(self, res, zf, profile, keep, depth, tmp, force_signed=False):
        self.res, self.zf = res, zf
        self.profile = profile
        self.paranoid = profile is not Profile.STEALTH
        self.keep = {str(k).strip().casefold() for k in ([keep] if isinstance(keep, str) else (keep or ()))}
        self.depth, self.tmp, self.force_signed = depth, tmp, force_signed
        infos = zf.infolist()
        self.names = [i.filename for i in infos]
        self.nameset = set(self.names)
        self.info = {i.filename: i for i in infos}
        self.cur = {}              # изменённые части: имя -> новые байты
        self.drop = set()
        self.rels = {}             # имя .rels -> [(Id, Type, Target, TargetMode)]
        self.idents = set()        # личные строки этого документа (нижний регистр)
        self.appname = ""          # Application из исходного app.xml
        self.nested_n = 0

    # --- вспомогательное ----------------------------------------------------
    def act(self, a, loc, label, before="", after="", note=""):
        return self.res.act(a, loc, label, before, after, note)

    def held(self, loc, key):
        """Пользователь велел оставить поле: пишем KEPT и просим не трогать."""
        if self.keep and (key.casefold() in self.keep or loc.casefold() in self.keep):
            self.act(Act.KEPT, loc, key, note="оставлено по whitelist пользователя (keep)")
            return True
        return False

    def add_ident(self, value):
        v = " ".join(str(value or "").split())
        if len(v) >= 6:
            self.idents.add(v.casefold())

    def leaky(self, value):
        """Значение похоже на личные данные: email, путь или строка, уже найденная как личная."""
        if not value:
            return False
        low = value.casefold()
        return bool(_emails(value)) or _has_path(value) or any(i in low for i in self.idents)

    def exists(self, name):
        return name in self.nameset and name not in self.drop

    def raw(self, name, limit=None):
        if name in self.cur:
            return self.cur[name]
        limit = MAX_XML if limit is None else limit
        info = self.info.get(name)
        if info is None:
            raise _Bad("части %s нет в пакете" % name)
        if info.file_size > limit:
            raise _Bad("часть %s слишком велика (%d МБ): правка невозможна" % (name, info.file_size >> 20))
        return self.zf.read(info)

    def patch(self, name, want=None, text=False, raw=None):
        try:
            return _Patch(self.raw(name) if raw is None else raw, want, text)
        except _Bad as exc:
            raise _Bad("%s: %s" % (name, exc)) from None

    def commit(self, name, patch):
        """Применить накопленные правки. True -- часть изменилась."""
        if not patch.edits:
            return False
        try:
            new = patch.finish()
        except _Bad as exc:
            raise _Bad("%s: %s" % (name, exc)) from None
        if new == patch.raw:
            return False
        self.cur[name] = new
        return True

    def values(self, name, locals_):
        """Значения атрибутов с указанными локальными именами во всей части (только чтение)."""
        out = []
        try:
            for n in _parse(self.raw(name), lambda _ns, _l, a: any(x[1] in locals_ for x in a)):
                out += [v for _, loc, v in n.attrs if loc in locals_ and v]
        except _Bad:
            pass
        return out

    # --- отношения ----------------------------------------------------------
    def index_rels(self):
        self.rels = {}
        for rn in self.names:
            if rn.endswith(".rels") and rn not in self.drop:
                self.rels[rn] = self._read_rels(rn)

    def _read_rels(self, rn):
        return [(n.get("Id") or "", n.get("Type") or "", n.get("Target") or "", n.get("TargetMode") or "")
                for n in self.patch(rn).by("Relationship")]

    def rel_target(self, owner, rid):
        for r in self.rels.get(_rels_name(owner), ()):
            if r[0] == rid:
                return r[2]
        return None

    def rels_remove(self, rn, rids):
        rids = set(rids)
        P = self.patch(rn)
        hit = [n for n in P.by("Relationship") if n.get("Id") in rids]
        if not hit:
            return
        if len(P.by("Relationship")) == len(hit):
            # Пустой .rels Word не пишет: часть убираем целиком.
            self.drop.add(rn)
            self.cur.pop(rn, None)
            self.rels.pop(rn, None)
            return
        for n in hit:
            P.remove(n)
        self.commit(rn, P)
        self.rels[rn] = [r for r in self.rels.get(rn, ()) if r[0] not in rids]

    def rels_set_target(self, rn, rid, new):
        P = self.patch(rn)
        for n in P.by("Relationship"):
            if n.get("Id") == rid:
                P.set_attr(n, "Target", new)
        self.commit(rn, P)
        self.rels[rn] = [(a, b, new if a == rid else c, d) for a, b, c, d in self.rels.get(rn, ())]

    def incoming(self, name):
        return [(rn, rid, typ) for rn, lst in self.rels.items() for rid, typ, tgt, mode in lst
                if mode != "External" and _resolve(rn, tgt) == name]

    def plan_refs(self, owner, rids):
        """Что надо вырезать из XML части-владельца, чтобы удаляемые отношения не остались висеть.
        Возвращает (патч, план); патч None, если ссылок в XML нет вовсе (определяется по байтам, без
        разбора: у документа на сотни мегабайт это разница между секундами и минутой). False --
        среди ссылок есть такая, которую безопасно убрать нечем: тогда ничего не трогаем."""
        try:
            raw = self.raw(owner)
        except _Bad:
            return False                    # часть слишком велика для правки: ссылку убрать нечем
        if not any(q + r.encode("utf-8") + q in raw for r in rids for q in (b'"', b"'")):
            return None, []

        def want(_ns, loc, attrs):
            return loc in _SCRUB_WANT or any(a in R_NSES and v in rids for a, _l, v in attrs)

        P = self.patch(owner, want, raw=raw)
        plan = []
        for n in P.nodes:
            for ans, aloc, val in n.attrs:
                if ans in R_NSES and val in rids:
                    act = _ref_action(n, aloc)
                    if act is None:
                        return False
                    plan.append((n, act))
        return P, plan

    def apply_refs(self, owner, P, plan):
        if P is None:
            return
        gone, per_parent = set(), {}
        for n, act in plan:
            if id(n) in gone:
                continue
            if act == "unwrap":
                P.unwrap(n)
            elif act == "dropattr":
                P.del_attr(n, (R_NSES, "id"))
            else:
                t = n if act == "remove" else _ole_target(n)
                if id(t) in gone:
                    continue
                P.remove(t)
                gone.add(id(t))
                if t.parent is not None:
                    per_parent.setdefault(id(t.parent), [t.parent, 0])[1] += 1
        for par, cnt in per_parent.values():
            if par.name in ("hyperlinks", "oleObjects") and cnt == len(par.kids):
                P.remove(par)
        self.commit(owner, P)

    def unlink(self, rn, rids):
        """Убрать отношения и ссылки на них из XML владельца. Возвращает множество rId, которые
        убрать НЕ удалось (элемент, ссылающийся на отношение, безопасно вырезать нечем): их
        отношения остаются нетронутыми."""
        rids = set(rids)
        owner = _rels_owner(rn)
        failed = set()
        if owner and self.exists(owner) and owner.endswith((".xml", ".vml")):
            plan = self.plan_refs(owner, rids)
            if plan is not False:
                self.apply_refs(owner, *plan)
            else:                                   # среди ссылок есть неубираемая: выясняем по одной
                for rid in sorted(rids):
                    one = self.plan_refs(owner, {rid})
                    if one is not False:
                        self.apply_refs(owner, *one)
                    else:
                        failed.add(rid)
        self.rels_remove(rn, rids - failed)
        return failed

    def drop_part(self, name):
        """Убрать часть вместе со ссылками на неё. True -- убрана. False -- ссылку из XML
        владельца безопасно убрать нельзя: часть остаётся, ничего не тронуто."""
        if name in self.drop:
            return True
        by_rn, by_owner, plans = {}, {}, []
        for rn, rid, typ in self.incoming(name):
            by_rn.setdefault(rn, set()).add(rid)
            owner = _rels_owner(rn)
            # Ссылка на отношение из XML владельца бывает у любого типа (у printerSettings --
            # pageSetup/@r:id в Excel, у oleObject -- сам объект). Есть ли она, решает поиск по
            # байтам в plan_refs; если есть и убрать её нечем -- часть остаётся.
            if owner and self.exists(owner) and owner.endswith((".xml", ".vml")):
                by_owner.setdefault(owner, set()).add(rid)
        for owner, rids in by_owner.items():
            plan = self.plan_refs(owner, rids)
            if plan is False:
                return False
            plans.append((owner,) + plan)
        for owner, P, plan in plans:
            self.apply_refs(owner, P, plan)
        for rn, rids in by_rn.items():
            self.rels_remove(rn, rids)
        self.drop.add(name)
        self.cur.pop(name, None)
        own = _rels_name(name)
        if own in self.nameset and own not in self.drop:      # у части был свой .rels
            self.drop.add(own)
            self.cur.pop(own, None)
            self.rels.pop(own, None)
        return True

    def result(self):
        """(replace, drop) для zipfix.rebuild."""
        rep = {n: b for n, b in self.cur.items() if n in self.nameset and n not in self.drop}
        return rep, set(self.drop)


# ============================================================================
#  Секции чистки. Каждая -- в своём try (см. _step): сбой одной не валит остальные,
#  но любой сбой делает результат ok=False: частично чистый файл хуже отказа.
# ============================================================================


def _step(pkg, name, fn):
    try:
        fn(pkg)
    except _Refuse:
        raise
    except _Bad as exc:
        pkg.act(Act.FAILED, name, name, note="сбой секции: %s" % exc)
        pkg.res.err("Секция «%s»: %s" % (name, exc))
    except Exception as exc:  # noqa: BLE001 -- битые файлы нам присылают специально
        pkg.act(Act.FAILED, name, name, note="сбой секции (%s), подробности в errors" % type(exc).__name__)
        pkg.res.err("Секция «%s»: %s: %s" % (name, type(exc).__name__, exc))


def _descendants(n):
    for k in n.kids:
        yield k
        yield from _descendants(k)


def _drop(pkg, name, key, label, before, note):
    """Убрать часть целиком. Если ссылку на неё из XML владельца безопасно убрать нельзя --
    часть остаётся, и это честно записано как UNREMOVABLE."""
    if pkg.held(name, key):
        return
    if pkg.drop_part(name):
        pkg.act(Act.REMOVED, name, label, before, note=note)
    else:
        pkg.act(Act.UNREMOVABLE, name, label, before,
                note="Ссылку на часть из XML владельца безопасно убрать не удалось, часть оставлена. " + note)


# --- 0. подпись и личные строки документа -------------------------------------


def _sec_signature(pkg):
    sig = [n for n in pkg.names if n.startswith("_xmlsignatures/")]
    if not sig:
        return
    if not pkg.force_signed:
        raise _Refuse("Пакет подписан (%d частей в _xmlsignatures/): любая правка делает подпись "
                      "недействительной. Файл НЕ очищен; чтобы всё равно очистить и убрать подпись, "
                      "нужен force_signed." % len(sig))
    origin = [n for n in sig if n.endswith(".sigs")]
    for n in origin:
        pkg.drop_part(n)
    for n in sig:
        if n not in pkg.drop:
            pkg.drop.add(n)
            pkg.cur.pop(n, None)
    pkg.act(Act.REMOVED, "_xmlsignatures/", "Цифровая подпись пакета", "%d частей" % len(sig),
            note="Подпись не переживает правку метаданных, поэтому убрана целиком (force_signed).")


def _sec_idents(pkg):
    """Собрать личные строки документа ДО правок: они нужны, чтобы найти их же в заголовке,
    теме, ключевых словах (инспектор считает такое совпадение утечкой)."""
    for name, keys in (("docProps/core.xml", ("creator", "lastModifiedBy")),
                       ("docProps/app.xml", ("Company", "Manager", "Application"))):
        if pkg.exists(name):
            for n in _parse(pkg.raw(name), None, True):
                if n.name in keys:
                    if n.name == "Application":
                        pkg.appname = n.text().strip()
                    else:
                        pkg.add_ident(n.text())
    if pkg.exists("word/people.xml"):
        for v in pkg.values("word/people.xml", {"author", "userId"}):
            pkg.add_ident(v)
    if pkg.exists("word/comments.xml"):
        for v in pkg.values("word/comments.xml", {"author"}):
            pkg.add_ident(v)
    for name in pkg.names:
        if pkg.exists(name) and _PERSON_RE.match(name):
            for v in pkg.values(name, _ANON_ATTRS | {"userId", "email"}):
                pkg.add_ident(v)
    if pkg.exists("docProps/custom.xml"):
        for n in _parse(pkg.raw("docProps/custom.xml"), None, True):
            if n.name == "property" and (_emails(n.text()) or _PERSON_NAME_RE.search(n.get("name") or "")):
                pkg.add_ident(n.text())


def _emptied(P, root):
    """Все дочерние элементы корня вырезаны (и они были). Пустой core.xml/app.xml инспектор считает
    ошибкой разбора, а Office в таком виде их не пишет: часть тогда убирается целиком."""
    gone = set(P.edits)
    return bool(root.kids) and all((k.a, k.end, b"") in gone for k in root.kids)


def _drop_emptied(pkg, name, P, root, why):
    if _emptied(P, root) and pkg.drop_part(name):
        pkg.act(Act.REMOVED, name, name, "", "", "После чистки в части не осталось ни одного свойства: %s" % why)
        return True
    return False


# --- А. docProps/core.xml ------------------------------------------------------

# локальное имя -> (ключ, подпись, STEALTH, PARANOID); prov: оставить, если нет личных данных
_CORE_RULES = {
    "creator": ("dc:creator", "автор", "blank", "blank"),
    "lastModifiedBy": ("cp:lastModifiedBy", "кто сохранил последним", "blank", "blank"),
    "created": ("dcterms:created", "создан", "keep", "remove"),
    "modified": ("dcterms:modified", "изменён", "keep", "remove"),
    "revision": ("cp:revision", "номер ревизии", "keep", "remove"),
    "lastPrinted": ("cp:lastPrinted", "последняя печать", "remove", "remove"),
    "title": ("dc:title", "заголовок", "prov", "remove"),
    "subject": ("dc:subject", "тема", "prov", "remove"),
    "description": ("dc:description", "описание", "prov", "remove"),
    "keywords": ("cp:keywords", "ключевые слова", "prov", "remove"),
    "category": ("cp:category", "категория", "prov", "remove"),
    "contentStatus": ("cp:contentStatus", "статус", "prov", "remove"),
    "identifier": ("dc:identifier", "идентификатор", "prov", "remove"),
    "version": ("cp:version", "версия", "prov", "remove"),
    "language": ("dc:language", "язык", "keep", "keep"),
}
_NOTE_PARANOID = "PARANOID убирает всё из провенанса: файл читается как обработанный -- этого профиль не скрывает."


def _sec_core(pkg):
    name = "docProps/core.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    if not P.nodes:
        raise _Bad("в core.xml нет корневого элемента")
    root = P.nodes[0]
    vals = {n.name: n.text().strip() for n in root.kids}
    same_dates = bool(vals.get("created")) and vals.get("created") == vals.get("modified")
    for n in list(root.kids):
        key, ru, st, pa = _CORE_RULES.get(n.name, ("%s" % n.name, "нестандартное поле", "prov", "remove"))
        val = n.text().strip()
        label = "%s — %s" % (key, ru)
        mode = pa if pkg.paranoid else st
        if pkg.held(name, key):
            continue
        prov_note = "Провенанс: STEALTH оставляет, PARANOID убирает."
        if mode == "prov":
            mode = "blank" if pkg.leaky(val) else "keep"
            if mode == "blank":
                prov_note = ("Значение содержало личные данные (email, путь или имя, найденное в других "
                             "свойствах): обнулено.")
        if mode == "blank":
            if not val:
                pkg.act(Act.KEPT, name, label, "", "", "Уже пусто. " + (
                    "Пустой элемент -- рядовое состояние (Word пишет так при незаполненном имени)."
                    if n.name in ("creator", "lastModifiedBy") else ""))
                continue
            P.set_text(n, "")
            note = ("Личность. Элемент оставлен ПУСТЫМ, а не удалён: Word пишет его пустым, когда имя "
                    "пользователя не заполнено, а отсутствие самого элемента -- признак чистки."
                    if n.name in ("creator", "lastModifiedBy") else prov_note)
            pkg.act(Act.BLANKED, name, label, val, "", note)
        elif mode == "remove":
            if n.name == "lastPrinted":
                note = ("Время последней печати. Word не пишет этот элемент для ненапечатанных "
                        "документов, поэтому отсутствие ничем не выделяется.")
            else:
                note = _NOTE_PARANOID
            P.remove(n)
            pkg.act(Act.REMOVED, name, label, val, "", note)
        else:
            if n.name in ("created", "modified"):
                note = ("STEALTH: даты не трогаем. Обнуление в одну секунду -- признак чистки, а подмена "
                        "даты -- фальсификация провенанса.")
                if same_dates:
                    note += (" В исходнике created и modified совпадают до секунды: это свойство самого "
                             "файла, чистка его не создавала.")
            elif n.name == "revision":
                note = ("STEALTH: Word пишет cp:revision всегда, его отсутствие или обнуление -- признак "
                        "чистки. PARANOID убирает.")
            elif n.name == "language":
                note = "Структурное: язык документа нужен проверке орфографии и скринридерам."
            else:
                note = prov_note
            pkg.act(Act.KEPT, name, label, val, "", note)
    if not _drop_emptied(pkg, name, P, root, "пустой core.xml Office не пишет, а инспектор принимает за ошибку "
                         "разбора; часть убрана вместе со ссылкой в .rels и Override."):
        pkg.commit(name, P)


# --- Б. docProps/app.xml ----------------------------------------------------------


def _sec_app(pkg):
    name = "docProps/app.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    if not P.nodes:
        raise _Bad("в app.xml нет корневого элемента")
    root = P.nodes[0]
    titles = next((k for k in root.kids if k.name == "TitlesOfParts"), None)
    drop_titles = pkg.paranoid or bool(titles is not None and pkg.leaky(titles.text()))
    stats, removed_stats = [], []
    for n in list(root.kids):
        nm, val = n.name, n.text().strip()
        if pkg.held(name, nm):
            continue
        if nm in ("Application", "AppVersion"):
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", "PARANOID убирает приложение и версию: файл без "
                        "них читается как обработанный (Word всегда пишет app.xml с Application) -- "
                        "этого профиль не скрывает.")
            else:
                pkg.act(Act.KEPT, name, nm, val, "", "STEALTH: оставлено как есть. Файл действительно "
                        "сделан этим приложением, а пустой или подменённый Application при сохранённой "
                        "структуре частей -- мгновенный признак обработки.")
        elif nm in ("Company", "Manager"):
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", "Личность: PARANOID убирает элемент.")
            elif val:
                P.set_text(n, "")
                pkg.act(Act.BLANKED, name, nm, val, "", "Личность: обнулено, элемент оставлен пустым "
                        "(так Word пишет незаполненное поле).")
            else:
                pkg.act(Act.KEPT, name, nm, "", "", "Уже пусто.")
        elif nm == "TotalTime":
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", "Время редактирования в минутах. " + _NOTE_PARANOID)
            else:
                pkg.act(Act.KEPT, name, nm, val, "", "STEALTH: провенанс не трогаем. Нулевое значение "
                        "у большого документа -- признак чистки, а выдуманное -- подделка провенанса; "
                        "реальное время редактирования убирает PARANOID.")
        elif nm == "Template":
            if pkg.paranoid:
                P.remove(n)
                pkg.act(Act.REMOVED, name, nm, val, "", "Имя шаблона. " + _NOTE_PARANOID)
            elif _is_pathlike(val) or pkg.leaky(val):
                new = _relativize(val) if _is_pathlike(val) else ""
                if pkg.leaky(new):
                    new = ""
                P.set_text(n, new)
                pkg.act(Act.NORMALIZED, name, nm, val, new, "Путь к шаблону выдаёт имя пользователя и "
                        "каталоги автора: оставлено только имя файла.")
            else:
                stats.append(nm)
        elif nm == "DigSig":
            P.remove(n)
            pkg.act(Act.REMOVED, name, nm, "", "", "Цифровая подпись в свойствах: личность подписанта.")
        elif nm in ("HeadingPairs", "TitlesOfParts"):
            if drop_titles:
                P.remove(n)
                removed_stats.append(nm)
            else:
                stats.append(nm)
        elif nm in _APP_FLAGS:
            stats.append(nm)
        elif pkg.paranoid:
            P.remove(n)
            removed_stats.append(nm)
        elif pkg.leaky(val):
            P.set_text(n, "")
            pkg.act(Act.BLANKED, name, nm, val, "", "Значение содержало личные данные: обнулено.")
        else:
            stats.append(nm)
    if removed_stats:
        pkg.act(Act.REMOVED, name, "Статистика и состав документа", ", ".join(removed_stats), "",
                "PARANOID убирает всё из провенанса (объём, состав частей, названия листов и слайдов)."
                if pkg.paranoid else "Названия частей содержали личные данные: убраны вместе с "
                "HeadingPairs.")
    if stats:
        pkg.act(Act.KEPT, name, "Статистика и флаги", ", ".join(stats), "",
                "STEALTH: объём документа и флаги не трогаем, это провенанс без личных данных.")
    if not _drop_emptied(pkg, name, P, root, "пустой app.xml Office не пишет, а инспектор принимает за ошибку "
                         "разбора; часть убрана вместе со ссылкой в .rels и Override. Файл без app.xml "
                         "читается как обработанный -- этого профиль не скрывает."):
        pkg.commit(name, P)


# --- В. docProps/custom.xml, метки Purview, превью, people, принтеры ----------------

_KEEP_UNDERSCORE = {"_MarkAsFinal", "_ReviewingToolsShownOnce"}


def _custom_why(pkg, name, val):
    """Почему свойство убираем (None -- оставляем). Правила совпадают с инспектором."""
    if name.startswith("MSIP_Label_"):
        return ("Метка Microsoft Purview: GUID метки, время установки, SetBy -- реальный email, SiteId -- "
                "GUID тенанта. Снятие метки тенант может заметить: это решение пользователя.")
    if _emails(val):
        return "Значение содержит email."
    if val and _PERSON_NAME_RE.search(name):
        return "Свойство хранит человека (автор, исполнитель, подписант)."
    if name in _SP_NAMES or "urn:schemas" in name or name.startswith("_dlc_") or (
            name.startswith("_") and name not in _KEEP_UNDERSCORE):
        return "Свойство SharePoint/СЭД или служебное свойство Office: привязка к порталу, библиотеке, переписке."
    if _has_path(val):
        return "Значение похоже на локальный или сетевой путь."
    if pkg.leaky(val):
        return "Значение содержит личную строку из других свойств документа."
    return None


def _sec_custom(pkg):
    name = "docProps/custom.xml"
    if pkg.exists(name):
        P = pkg.patch(name, text=True)
        props = P.by("property")
        gone = []
        for n in props:
            pname, val = n.get("name") or "", n.text().strip()
            why = _custom_why(pkg, pname, val)
            if why is None and pkg.paranoid:
                why = "PARANOID убирает все пользовательские свойства."
            if why is None or pkg.held(name, pname):
                continue
            P.remove(n)
            gone.append(n)
            pkg.act(Act.REMOVED, name, pname, val, "", why)
        if props and len(gone) == len(props):
            # Пустой custom.xml инспектор считает ошибкой разбора, а Word его в таком виде не пишет.
            if pkg.drop_part(name):
                pkg.act(Act.REMOVED, name, "docProps/custom.xml", "%d свойств(а)" % len(props), "",
                        "Свойств не осталось: пустой custom.xml Word не пишет (а инспектор принимает его за "
                        "ошибку разбора), поэтому часть убрана вместе со ссылкой в .rels и Override.")
            else:
                pkg.commit(name, P)
        elif gone:
            rest = [n for n in props if n not in gone]
            if [n.get("pid") for n in rest] != [str(i) for i in range(2, 2 + len(rest))]:
                for i, n in enumerate(rest, 2):      # Word нумерует pid подряд с 2
                    if n.get("pid") != str(i):
                        P.set_attr(n, "pid", str(i))
            pkg.commit(name, P)
    for nm in [n for n in pkg.names if n.lower().endswith("labelinfo.xml")]:
        if pkg.exists(nm):
            _drop(pkg, nm, "LabelInfo", "Метка конфиденциальности (LabelInfo.xml)", "",
                  "Дублирует метку Purview отдельной частью: GUID метки, тенанта, email. Снятие метки "
                  "тенант может заметить: это решение пользователя.")


def _sec_thumbnail(pkg):
    for nm in [n for n in pkg.names if n.startswith("docProps/thumbnail")]:
        if pkg.exists(nm):
            _drop(pkg, nm, "thumbnail", "Превью документа", "%d байт" % pkg.info[nm].file_size,
                  "Превью рендерится при сохранении и может показывать ПРЕДЫДУЩУЮ версию документа, в том "
                  "числе уже удалённый текст; кроме того, это картинка со своими метаданными.")


def _sec_people(pkg):
    if pkg.exists("word/people.xml"):
        _drop(pkg, "word/people.xml", "people", "Список авторов (people.xml)", "",
              "w15:presenceInfo/@userId -- как правило, реальный рабочий email. Часть -- чистая "
              "идентичность, убрана целиком вместе со ссылкой в document.xml.rels и Override в "
              "[Content_Types].xml.")


def _sec_printer(pkg):
    for nm in [n for n in pkg.names if "printersettings" in n.lower() and n.lower().endswith(".bin")]:
        if not pkg.exists(nm):
            continue
        try:
            dev = _device_name(pkg.raw(nm, 4 * 1024 * 1024)) or "<не разобрано>"
        except Exception:  # noqa: BLE001
            dev = "<не прочитано>"
        _drop(pkg, nm, "printerSettings", "Настройки принтера (DEVMODE)", dev,
              "Первые 32 символа структуры -- имя принтера: часто имя хоста, номер кабинета или офис. "
              "Часть убрана с ссылками из .rels (и r:id в pageSetup листов Excel).")


def _sec_vba(pkg):
    vba = [n for n in pkg.names if n.lower().endswith("vbaproject.bin") and pkg.exists(n)]
    for nm in vba:
        size = "%d байт" % pkg.info[nm].file_size
        if pkg.paranoid and not pkg.held(nm, "vbaProject"):
            extra = [n for n in pkg.names if n.lower().endswith(("vbaprojectsignature.bin", "vbadata.xml"))
                     and pkg.exists(n) and posixpath.dirname(n) == posixpath.dirname(nm)]
            if pkg.drop_part(nm):
                for e in extra:
                    pkg.drop_part(e)
                pkg.act(Act.REMOVED, nm, "Проект VBA (макросы)", size, "",
                        "PARANOID удаляет макросы целиком: в них исходники, имена модулей и путь проекта. "
                        "Функциональность документа, которая на них держалась, потеряна.")
            else:
                pkg.act(Act.UNREMOVABLE, nm, "Проект VBA (макросы)", size,
                        note="Ссылки на проект убрать не удалось, макросы оставлены.")
        else:
            pkg.act(Act.KEPT, nm, "Проект VBA (макросы)", size, "",
                    "hazard: в файле макросы (активное содержимое). STEALTH их не удаляет: это сломало бы "
                    "документ, решение за пользователем. Внутри проекта могут быть имена и путь автора.")


# --- вложения: картинки, встроенные файлы, OLE ----------------------------------------


def _clean_blob(pkg, name, data):
    """Прогнать вложение через чистильщик его формата. Возвращает (семейство, sub, новые байты)."""
    pkg.nested_n += 1
    p_in = os.path.join(pkg.tmp, "n%04d.in" % pkg.nested_n)
    p_out = os.path.join(pkg.tmp, "n%04d.out" % pkg.nested_n)
    with open(p_in, "wb") as fh:
        fh.write(data)
    try:
        fam = sniff.family(sniff.sniff(p_in))
        sub = out = None
        if fam == "image":
            from .clean_image import clean_image
            sub = clean_image(p_in, p_out, pkg.profile, pkg.keep)
        elif fam == "ooxml":
            if pkg.depth >= MAX_DEPTH:
                return "depth", None, None
            sub = clean_ooxml(p_in, p_out, pkg.profile, pkg.keep, force_signed=pkg.force_signed,
                              _depth=pkg.depth + 1)
        elif fam == "pdf":
            from .clean_pdf import clean_pdf
            sub = clean_pdf(p_in, p_out, pkg.profile, pkg.keep)
        else:
            return fam, None, None
        if sub.ok and os.path.isfile(p_out):
            with open(p_out, "rb") as fh:
                out = fh.read()
        return fam, sub, out
    finally:
        for p in (p_in, p_out):
            try:
                os.remove(p)
            except OSError:
                pass


def _merge(pkg, name, sub):
    if sub.actions and all(a.act is Act.KEPT for a in sub.actions):
        pkg.act(Act.KEPT, name, "Вложенный файл без метаданных", "", "",
                "Чистильщик вложения ничего не убирал: остались только структурные элементы (%d)." % len(sub.actions))
        return
    for a in sub.actions:
        pkg.act(a.act, "%s > %s" % (name, a.location), a.label, a.before, a.after, a.note)


def _nested(pkg, name, what):
    """Вложение -> чистильщик его формата. Возвращает семейство, если вложение НЕ обработано
    (OLE, неизвестный формат), строку "big"/"depth" при отказе по размеру или глубине, иначе None."""
    info = pkg.info[name]
    if info.file_size > MAX_NESTED:
        pkg.act(Act.UNREMOVABLE, name, what, "%d МБ" % (info.file_size >> 20),
                note="Вложение больше %d МБ: в память не берём, метаданные внутри остались." % (MAX_NESTED >> 20))
        return "big"
    data = pkg.raw(name, MAX_NESTED)
    fam, sub, out = _clean_blob(pkg, name, data)
    if sub is None:
        if fam == "depth":
            pkg.act(Act.UNREMOVABLE, name, what, note="Вложенность глубже %d: внутренний пакет не чистится, "
                    "его метаданные остались." % MAX_DEPTH)
            return "depth"
        return fam
    if sub.ok and out is not None:
        _merge(pkg, name, sub)
        if out != data:
            pkg.cur[name] = out
        return None
    pkg.act(Act.UNREMOVABLE, name, what, note="Вычистить вложение не удалось (%s): метаданные внутри "
            "остались." % "; ".join(sub.errors)[:300])
    return None


def _sec_media(pkg):
    skipped = {}
    for nm in [n for n in pkg.names if re.match(r"^(word|xl|ppt)/media/", n) and not n.endswith("/")]:
        if not pkg.exists(nm):
            continue
        fam = _nested(pkg, nm, "Встроенное изображение")
        if fam and fam not in ("big", "depth"):
            ext = nm.rsplit(".", 1)[-1].lower() if "." in nm.rsplit("/", 1)[-1] else "?"
            skipped[ext] = skipped.get(ext, 0) + 1
    if skipped:
        pkg.act(Act.KEPT, "media", "Нерастровые медиафайлы",
                ", ".join("%s: %d" % kv for kv in sorted(skipped.items())), "",
                "Форматы, которые чистильщик картинок не разбирает (EMF, WMF, SVG, видео, звук): метаданные "
                "в них не искались и не убирались.")


_FAMILY_RU = {"unknown": "формат не опознан", "text": "текст", "zip": "ZIP-архив", "odf": "ODF", "rtf": "RTF",
              "ole": "OLE2"}


def _sec_embeddings(pkg):
    what = "Вложенный объект"
    for nm in [n for n in pkg.names if "/embeddings/" in n and not n.endswith("/")]:
        if not pkg.exists(nm):
            continue
        rest = _nested(pkg, nm, what)
        if rest == "ole":
            size = "%d байт" % pkg.info[nm].file_size
            if pkg.paranoid and not pkg.held(nm, "embeddings"):
                _drop(pkg, nm, "embeddings", "Вложенный OLE-объект", size,
                      "PARANOID удаляет вложенный OLE-объект целиком вместе с объектом в тексте: у него свои "
                      "docProps (автор, компания, путь), а чистки OLE2 пока нет (фаза 4).")
            else:
                pkg.act(Act.UNREMOVABLE, nm, what, size,
                        note="Вложенный OLE-объект: внутри свои docProps (автор, компания, путь), чистки OLE2 "
                        "пока нет (фаза 4), поэтому метаданные ВНУТРИ объекта остались. Профиль paranoid "
                        "удаляет такой объект целиком.")
        elif rest and rest not in ("big", "depth"):
            pkg.act(Act.KEPT, nm, what, "%d байт" % pkg.info[nm].file_size, "",
                    "Вложенный файл формата, для которого чистки нет (%s): содержимое не разбиралось."
                    % _FAMILY_RU.get(rest, rest))


# --- Д. word/settings.xml ---------------------------------------------------------------


def _sec_settings(pkg):
    name = "word/settings.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    rm = set()                                    # r:id из settings.xml.rels, теряющие владельца
    for n in P.by("attachedTemplate", W_NSES):
        rid = n.get("id", R_NSES)
        target = pkg.rel_target(name, rid) if rid else None
        P.remove(n)
        if rid:
            rm.add(rid)
        pkg.act(Act.REMOVED, name, "w:attachedTemplate — присоединённый шаблон", target or "r:id=%s" % rid,
                "", "Путь к шаблону обычно file:///C:/Users/ИМЯ/... или UNC: имя пользователя и сервер "
                "организации. Без присоединённого шаблона Word использует Normal.dotm.")
    for n in P.by("mailMerge", W_NSES):
        bits = []
        for k in _descendants(n):
            for ans, aloc, v in k.attrs:
                if ans in R_NSES:
                    rm.add(v)
                    bits.append("%s=%s" % (k.name, pkg.rel_target(name, v) or v))
                elif aloc in ("val", "id") and k.name in ("dataSource", "headerSource", "query", "src",
                                                            "connectString", "udl", "table", "odso"):
                    bits.append("%s=%s" % (k.name, v))
        P.remove(n)
        pkg.act(Act.REMOVED, name, "w:mailMerge — источник данных слияния", "; ".join(bits)[:400], "",
                "Путь к базе, таблица и строка подключения с логином и сервером. Документ перестаёт быть "
                "главным документом слияния; текст и поля остаются.")
    for n in P.by("documentProtection", W_NSES) + P.by("writeProtection", W_NSES):
        hashed = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS]
        if hashed:
            for aloc, _v in hashed:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, name, "w:%s — хеш и соль пароля" % n.name,
                    "; ".join("%s=%s" % kv for kv in hashed), "",
                    "Хеш позволяет подобрать пароль офлайн, а пароль часто используют повторно. Защита "
                    "остаётся включённой, но БЕЗ пароля: снять её теперь может любой.")
    docvars = P.by("docVar", W_NSES)
    gone = 0
    for n in docvars:
        nm_, val = n.get("name", W_NSES) or "", n.get("val", W_NSES) or ""
        if pkg.paranoid or pkg.leaky(val) or pkg.leaky(nm_):
            P.remove(n)
            gone += 1
            pkg.act(Act.REMOVED, name, "w:docVar %s" % nm_, val, "",
                    "Переменная документа: макросы, надстройки и СЭД кладут сюда ФИО, номера и пути.")
    for par in P.by("docVars", W_NSES):
        if par.kids and all(k in docvars for k in par.kids) and gone == len(docvars):
            P.remove(par)
    rsid_n = len(P.by("rsid", W_NSES)) + len(P.by("rsidRoot", W_NSES))
    if pkg.paranoid:
        for tag in ("rsids", "proofState"):
            for n in P.by(tag, W_NSES):
                P.remove(n)
        for n in P.by("docId"):
            P.remove(n)
        if rsid_n:
            pkg.act(Act.REMOVED, name, "w:rsid — идентификаторы сессий правки", "%d шт." % rsid_n, "",
                    "PARANOID убирает все rsid: они связывают документы одной инсталляции Word. Документ, "
                    "заявляющий Word и не имеющий ни одного rsid, читается как созданный не Word -- наш "
                    "инспектор это детектирует (сигнал ai).")
    elif rsid_n:
        pkg.act(Act.KEPT, name, "w:rsid — идентификаторы сессий правки", "%d шт." % rsid_n, "",
                "STEALTH: оставлено. Это случайные ID сессий без прямых личных данных, а их полное отсутствие "
                "в документе, заявляющем Word, означает «создан не Word». Удаление rsid -- опция PARANOID.")
    pkg.commit(name, P)
    rn = _rels_name(name)
    if rm and rn in pkg.rels:
        pkg.rels_remove(rn, rm)


# --- Е. тело документа: авторы и даты правок ----------------------------------------------


def _sec_body(pkg):
    for nm in pkg.names:
        if pkg.exists(nm) and (_BODY_RE.match(nm) or (
                pkg.paranoid and nm in ("word/styles.xml", "word/numbering.xml"))):
            _body_part(pkg, nm)


def _body_part(pkg, name):
    par = pkg.paranoid

    def want(ns, loc, attrs):
        for ans, aloc, _v in attrs:
            if (ans in W_NSES and aloc in ("author", "date", "initials", "ed")) or aloc == "dateUtc":
                return True
            if par and aloc.startswith("rsid"):
                return True
        return par and ns in W_NSES and loc in ("rsid", "rsids", "rsidRoot", "bookmarkStart", "bookmarkEnd")

    raw = pkg.raw(name)
    # Дешёвый отсев по байтам: документ без правок и (в PARANOID) без rsid не разбираем вовсе.
    hint = rb"[\s:](?:author|date|dateUtc|initials|ed)\s*=" + (rb"|rsid|_GoBack" if par else b"")
    if not re.search(hint, raw):
        return
    P = pkg.patch(name, want, raw=raw)
    if not P.nodes:
        return
    authors, eds = {}, set()
    dates = initials = rsid = goback = 0
    gb = set()
    for n in P.nodes:
        if par and n.ns in W_NSES and n.name in ("rsid", "rsids", "rsidRoot"):
            P.remove(n)
            rsid += 1
            continue
        if par and n.ns in W_NSES and n.name == "bookmarkStart" and n.get("name", W_NSES) == "_GoBack":
            gb.add(n.get("id", W_NSES))
            P.remove(n)
            goback += 1
            continue
        if par and n.ns in W_NSES and n.name == "bookmarkEnd" and n.get("id", W_NSES) in gb:
            P.remove(n)
            continue
        for ans, aloc, v in n.attrs:
            key = (ans, aloc)
            if ans in W_NSES and aloc == "author":
                if v != AUTHOR:
                    P.set_attr(n, key, AUTHOR)
                    authors[v] = authors.get(v, 0) + 1
            elif (ans in W_NSES and aloc == "date") or aloc == "dateUtc":
                P.del_attr(n, key)
                dates += 1
            elif ans in W_NSES and aloc == "initials":
                P.del_attr(n, key)
                initials += 1
            elif ans in W_NSES and aloc == "ed" and n.name in ("permStart", "permEnd"):
                P.del_attr(n, key)
                eds.add(v)
            elif par and aloc.startswith("rsid"):
                P.del_attr(n, key)
                rsid += 1
    for who, cnt in sorted(authors.items(), key=lambda kv: -kv[1])[:MAX_ACTS_PER_PART]:
        pkg.act(Act.NORMALIZED, name, "Автор исправления или комментария", who, AUTHOR,
                "%d раз(а). Правки (w:ins/w:del) и комментарии остаются: это контент пользователя, убрано "
                "только авторство. «Author» -- то, что пишет сам Word при включённой «Удалять личные данные "
                "при сохранении»." % cnt)
    if len(authors) > MAX_ACTS_PER_PART:
        pkg.act(Act.NORMALIZED, name, "Авторы исправлений (остальные)", "%d авторов" % (len(authors) - MAX_ACTS_PER_PART),
                AUTHOR, "Показаны не все, остальные того же рода.")
    if dates:
        pkg.act(Act.REMOVED, name, "Даты правок и комментариев (w:date, dateUtc)", "%d шт." % dates, "",
                "Точное время каждой правки. Атрибут необязателен по схеме, правки остаются на месте.")
    if initials:
        pkg.act(Act.REMOVED, name, "Инициалы авторов (w:initials)", "%d шт." % initials, "",
                "Инициалы -- часть имени автора комментария.")
    if eds:
        pkg.act(Act.REMOVED, name, "w:permStart/@ed — имя разрешённого редактора", "; ".join(sorted(eds))[:400], "",
                "Диапазоны остаются, но привязка к конкретным людям снята: исключения из защиты для них "
                "больше не действуют.")
    if rsid:
        pkg.act(Act.REMOVED, name, "rsid-атрибуты", "%d шт." % rsid, "", "PARANOID убирает rsid. " + _NOTE_PARANOID)
    if goback:
        pkg.act(Act.REMOVED, name, "Закладка _GoBack", "присутствует", "",
                "Подпись сохранения из Word. " + _NOTE_PARANOID)
    pkg.commit(name, P)


# --- З. комментарии и авторы Excel/PowerPoint ----------------------------------------------

_PERSON_ATTRS = _ANON_ATTRS | {"userId", "email", "initials", "providerId", "date", "dt", "dT", "created"}


def _sec_persons(pkg):
    for nm in pkg.names:
        if not pkg.exists(nm):
            continue
        if _PERSON_RE.match(nm):
            _person_part(pkg, nm)
        elif _XLCOMMENTS_RE.match(nm):
            _xl_comments(pkg, nm)


def _person_part(pkg, name):
    P = pkg.patch(name, lambda _ns, _l, attrs: any(x[1] in _PERSON_ATTRS for x in attrs))
    seen, dates, required = {}, 0, 0
    for n in P.nodes:
        for ans, aloc, v in n.attrs:
            key = (ans, aloc)
            if aloc in _ANON_ATTRS or aloc == "userId":
                if v != AUTHOR:
                    P.set_attr(n, key, AUTHOR)
                    seen[v] = seen.get(v, 0) + 1
            elif aloc in ("email", "initials"):
                if v:
                    P.set_attr(n, key, "")
                    seen[v] = seen.get(v, 0) + 1
            elif aloc == "providerId":
                if v != "None":
                    P.set_attr(n, key, "None")
            elif aloc in ("date", "dt"):
                P.del_attr(n, key)
                dates += 1
            elif aloc in ("dT", "created"):
                required += 1
    for who, cnt in sorted(seen.items(), key=lambda kv: -kv[1])[:MAX_ACTS_PER_PART]:
        pkg.act(Act.NORMALIZED, name, "Автор комментария или участник", who, AUTHOR,
                "Имя, логин, email или инициалы заменены на нейтральные (%d раз(а))." % cnt)
    if dates:
        pkg.act(Act.REMOVED, name, "Даты комментариев", "%d шт." % dates, "", "Необязательный атрибут схемы.")
    if required:
        pkg.act(Act.KEPT, name, "Дата комментария (dT, created)", "%d шт." % required, "",
                "Обязательный атрибут схемы: убрать нельзя без порчи части, подменять нельзя -- это "
                "фальсификация провенанса.")
    pkg.commit(name, P)


def _xl_comments(pkg, name):
    P = pkg.patch(name, text=True)
    seen = {}
    for n in P.by("author"):
        v = n.text().strip()
        if v and v != AUTHOR:
            P.set_text(n, AUTHOR)
            seen[v] = seen.get(v, 0) + 1
    for who, cnt in seen.items():
        pkg.act(Act.NORMALIZED, name, "Автор комментария", who, AUTHOR,
                "Список авторов. Имя в тексте самого комментария (обычно жирная первая строка) -- "
                "содержимое, его не трогаем.")
    pkg.commit(name, P)


# --- Н. специфика Excel ------------------------------------------------------------------------

_FORMULA_PATH = re.compile(r"(?:file:///)?(?:[A-Za-z]:[\\/]|\\\\[^\\/\[\]']+[\\/])[^\[\]']*(?=\[)")


def _sec_workbook(pkg):
    name = "xl/workbook.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name, text=True)
    for n in P.by("fileVersion"):
        if pkg.held(name, "fileVersion"):
            continue
        parts = "; ".join("%s=%s" % (a[1], a[2]) for a in n.attrs)
        if pkg.paranoid:
            P.remove(n)
            pkg.act(Act.REMOVED, name, "fileVersion — версия и билд Excel", parts, "",
                    "Точная сборка Excel (rupBuild). " + _NOTE_PARANOID)
        else:
            pkg.act(Act.KEPT, name, "fileVersion — версия и билд Excel", parts, "",
                    "STEALTH: правда о файле, как Application/AppVersion; без неё книга от Excel выглядит "
                    "обработанной.")
    for n in P.by("absPath"):
        ac = n.parent.parent if (n.parent is not None and n.parent.name == "Choice") else None
        target = ac if (ac is not None and ac.name == "AlternateContent" and len(ac.kids) == 1
                        and len(ac.kids[0].kids) == 1) else n
        P.remove(target)
        pkg.act(Act.REMOVED, name, "x15ac:absPath — абсолютный путь папки сохранения", n.get("url") or "", "",
                "Excel пишет сюда каталог, где книгу сохраняли, с именем пользователя ОС. Инспектор этого "
                "места не видит.")
    for n in P.by("definedName"):
        val = n.text()
        if _has_path(val):
            new = _FORMULA_PATH.sub("", val)
            if new != val:
                P.set_text(n, new)
                pkg.act(Act.NORMALIZED, name, "definedName %s" % (n.get("name") or "?"), val, new,
                        "Ссылка на внешнюю книгу с локальным путём: оставлено только имя книги.")
    for n in P.by("fileSharing"):
        gone = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS or aloc in ("userName", "reservationPassword")]
        if gone:
            for aloc, _v in gone:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, name, "fileSharing — имя и пароль резервирования записи",
                    "; ".join("%s=%s" % kv for kv in gone), "",
                    "userName -- имя пользователя, зарезервировавшего запись; пароль -- его хеш.")
    for n in P.by("workbookProtection"):
        hashed = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS]
        if hashed:
            for aloc, _v in hashed:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, name, "workbookProtection — хеш пароля", "; ".join("%s=%s" % kv for kv in hashed),
                    "", "Защита остаётся включённой, но без пароля: снять её теперь может любой.")
    hidden = [n.get("name") or "?" for n in P.by("sheet") if (n.get("state") or "").lower() in ("hidden", "veryhidden")]
    if hidden:
        pkg.act(Act.KEPT, name, "Скрытые листы", "; ".join(hidden[:20]), "",
                "Скрытый лист -- содержимое книги, не метаданные: не трогаем.")
    pkg.commit(name, P)


_SHEET_PROT_RE = re.compile(r"^xl/(worksheets|chartsheets|dialogsheets)/[^/]+\.xml$")


def _stream_has(pkg, name, needle):
    """Есть ли needle в части (потоково: для частей, которые в память не берём)."""
    tail = b""
    with pkg.zf.open(pkg.info[name]) as fh:
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                return False
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle):]


def _sec_protection(pkg):
    """Хеши паролей защиты листов (sheetProtection) и презентации (modifyVerifier): как и у
    documentProtection, защита остаётся включённой, но без пароля. Листы могут быть огромными --
    разбираем только те, где это слово встречается в байтах."""
    for nm in pkg.names:
        if not pkg.exists(nm):
            continue
        if _SHEET_PROT_RE.match(nm):
            tag, extra = "sheetProtection", ("password",)
        elif nm == "ppt/presentation.xml":
            tag, extra = "modifyVerifier", ()
        else:
            continue
        if pkg.info[nm].file_size > MAX_XML:
            if _stream_has(pkg, nm, tag.encode()):
                pkg.act(Act.UNREMOVABLE, nm, "%s — хеш пароля" % tag, "",
                        note="Часть больше %d МБ: правка невозможна, хеш пароля защиты остался." % (MAX_XML >> 20))
            continue
        raw = pkg.raw(nm)
        if tag.encode() not in raw:
            continue
        P = pkg.patch(nm, lambda _ns, loc, _a: loc == tag, raw=raw)
        for n in P.nodes:
            gone = [(aloc, v) for _a, aloc, v in n.attrs if aloc in _HASH_ATTRS or aloc in extra]
            if not gone:
                continue
            if tag == "modifyVerifier":
                # Элемент без хеша -- не «защита без пароля», а непонятное состояние (PowerPoint мог бы
                # счесть пароль пустым): защита записи снимается целиком.
                P.remove(n)
                pkg.act(Act.REMOVED, nm, "p:modifyVerifier — пароль на запись", "; ".join("%s=%s" % kv for kv in gone), "",
                        "Хеш позволяет подобрать пароль офлайн. Элемент убран целиком: защита записи снята.")
                continue
            for aloc, _v in gone:
                P.del_attr(n, aloc)
            pkg.act(Act.REMOVED, nm, "%s — хеш пароля" % tag, "; ".join("%s=%s" % kv for kv in gone), "",
                    "Хеш позволяет подобрать пароль офлайн. Защита остаётся включённой, но без пароля: "
                    "снять её теперь может любой.")
        pkg.commit(nm, P)


def _sec_pivot(pkg):
    for nm in pkg.names:
        if not (pkg.exists(nm) and _PIVOT_RE.match(nm)):
            continue
        P = pkg.patch(nm)
        if not P.nodes:
            continue
        root = P.nodes[0]
        for ans, aloc, v in root.attrs:
            if aloc == "refreshedBy" and v:
                P.set_attr(root, (ans, aloc), "")
                pkg.act(Act.BLANKED, nm, "refreshedBy — кто обновлял сводную", v, "",
                        "Классическая утечка Excel: имя человека, обновившего кэш, лежит вне docProps.")
            elif aloc in ("refreshedDate", "refreshedDateIso"):
                P.del_attr(root, (ans, aloc))
                pkg.act(Act.REMOVED, nm, "%s — когда обновляли" % aloc, v, "", "Время обновления кэша сводной.")
        pkg.commit(nm, P)


def _split_conn(s):
    return re.findall(r"""(?:[^;"']|"[^"]*"|'[^']*')+""", s)


def _sanitize_conn(s):
    """DRIVER=SQL Server;SERVER=x;UID=y -> DRIVER=SQL Server (внутренние токены вида $Workbook$ остаются)."""
    kept = []
    for part in _split_conn(s):
        k, eq, v = part.partition("=")
        if eq and k.strip().lower() in _CONN_SECRET and not v.strip().startswith("$"):
            continue
        kept.append(part)
    out = ";".join(kept)
    return "" if (_has_path(out) or _emails(out)) else out


def _sec_connections(pkg):
    name = "xl/connections.xml"
    if not pkg.exists(name):
        return
    P = pkg.patch(name)
    for conn in P.by("connection", None):
        if conn.parent is None:
            continue                                  # корень <connections>, а не подключение
        mashup = any("microsoft.mashup" in (a[2] or "").lower() for k in [conn] + list(_descendants(conn))
                     for a in k.attrs)
        head = "; ".join("%s=%s" % (a[1], a[2]) for a in conn.attrs if a[1] in ("name", "description"))
        if mashup:
            pkg.act(Act.KEPT, name, "Подключение Power Query", head, "",
                    "Внутреннее подключение к данным самой книги ($Workbook$): внешнего сервера в нём нет. "
                    "Код запросов лежит в customXml (DataMashup), см. отдельное действие.")
            continue
        before, changed = [], False
        for a in conn.attrs:
            if a[1] in ("description", "odcFile", "sourceFile", "singleSignOnId") and a[2]:
                P.del_attr(conn, (a[0], a[1]))
                before.append("%s=%s" % (a[1], a[2]))
                changed = True
        for k in _descendants(conn):
            for ans, aloc, v in k.attrs:
                if aloc not in _CONN_SOURCE_ATTRS or not v:
                    continue
                new = _sanitize_conn(v) if aloc == "connection" else ""
                if new != v:
                    P.set_attr(k, (ans, aloc), new)
                    before.append("%s=%s" % (aloc, v))
                    changed = True
        if changed:
            pkg.act(Act.BLANKED, name, "Подключение к данным", "; ".join(before)[:400], "",
                    "Строка подключения (сервер, база, логин), запрос и пути обезличены: подключение больше "
                    "не обновляется, но структура книги и кэшированные данные целы.")
    pkg.commit(name, P)


# --- customXml: метаданные SharePoint ----------------------------------------------------------


def _root_info(raw):
    first = []

    def want(_ns, _loc, _a):
        if first:
            return False
        first.append(1)
        return True

    nodes = _parse(raw, want)
    return nodes[0] if nodes else None


def _sec_customxml(pkg):
    other = 0
    for nm in pkg.names:
        if not (pkg.exists(nm) and _CUSTOMXML_RE.match(nm)):
            continue
        raw = pkg.raw(nm)
        try:
            root = _root_info(raw)
        except _Bad:
            root = None                      # не XML: правок он не требует, инспектор сам покажет, что там
        if root is None:
            other += 1
            continue
        if root.ns.startswith("http://schemas.microsoft.com/DataMashup"):
            pkg.act(Act.UNREMOVABLE, nm, "Power Query (DataMashup)", "%d байт" % len(raw),
                    note="Внутри закодированный пакет с кодом запросов: адреса серверов, пути к файлам и "
                    "названия источников. Чистка этого контейнера не реализована, метаданные остались.")
            continue
        if root.ns not in _SP_ROOT_NS:
            other += 1
            continue
        qn = _NAME_RE.match(raw[root.a:root.b]).group(0)[1:]
        prefix = qn.split(b":")[0] if b":" in qn else b""
        ns = root.ns.encode("ascii")
        # У корня в пространстве имён по умолчанию объявление не пишем: пустому элементу оно ни к чему,
        # а сам URI схемы -- публичный адрес, но инспектор принимает любую подстроку за утечку.
        decl = (b" xmlns:" + prefix + b'="' + ns + b'"') if prefix else b""
        m = re.match(rb"(\xef\xbb\xbf)?<\?xml[^>]*\?>(\r?\n)?", raw)
        body = b"<documentManagement/>" if root.name == "properties" else b""
        stub = (m.group(0) if m else b"") + b"<" + qn + decl + (
            b">" + body + b"</" + qn + b">" if body else b"/>")
        if stub != raw:
            _well_formed(stub)
            pkg.cur[nm] = stub
            pkg.act(Act.REMOVED, nm, "Метаданные SharePoint (customXml)",
                    clip(re.sub(rb"<[^>]*>", b" ", raw).decode("utf-8", "replace")), "",
                    "Колонки библиотеки, схема типа контента, шаблоны форм: имена сотрудников, даты, адреса "
                    "портала. Содержимое обнулено до пустого корня, часть осталась на месте.")
        _strip_schema_refs(pkg, nm)
    if other:
        pkg.act(Act.KEPT, "customXml", "Прочие пользовательские XML-части", "%d шт." % other, "",
                "Не SharePoint: привязка элементов содержимого, библиография. Это содержимое документа, не "
                "трогаем.")


def _strip_schema_refs(pkg, item):
    rn = _rels_name(item)
    for rid, typ, tgt, mode in pkg.rels.get(rn, ()):
        if _type_tail(typ) != "customXmlProps" or mode == "External":
            continue
        props = _resolve(rn, tgt)
        if not props or not pkg.exists(props):
            continue
        P = pkg.patch(props)
        refs = P.by("schemaRef")
        if not refs:
            continue
        uris = [r.get("uri") or "" for r in refs]
        for r in refs:
            P.remove(r)
        pkg.commit(props, P)
        pkg.act(Act.REMOVED, props, "Ссылки на схемы (schemaRef)", "; ".join(u for u in uris if u)[:400], "",
                "Привязка к схемам портала; после обнуления данных части смысла не имеют.")


# --- И. внешние отношения -------------------------------------------------------------------------


def _sec_extrels(pkg):
    http = 0
    for rn in list(pkg.rels):
        kill = {}                      # rId -> (метка, было, пояснение): отношение убираем целиком
        for rid, typ, tgt, mode in list(pkg.rels.get(rn, ())):
            low = tgt.strip().lower()
            if low.startswith(("http://", "https://", "ftp://", "tel:")):
                http += 1
            elif low.startswith("mailto:"):
                kill[rid] = ("Ссылка mailto", tgt, "Прямой email во внешнем отношении. Ссылка снята, видимый "
                             "текст в документе остался (он -- содержимое).")
            elif _is_pathlike(tgt) and (mode == "External" or _resolve(rn, tgt) not in pkg.nameset):
                new = _relativize(tgt)
                if _type_tail(typ) in ("attachedTemplate", "mailMergeSource") or not new:
                    kill[rid] = ("Внешняя ссылка на файл", tgt, "Абсолютный путь или UNC выдаёт имя "
                                 "пользователя, диск и сервер организации.")
                else:
                    pkg.rels_set_target(rn, rid, new)
                    pkg.act(Act.NORMALIZED, rn, "Внешняя ссылка на файл (%s)" % rid, tgt, new,
                            "Путь выдаёт имя пользователя, диск и сервер. Ссылка осталась рабочей структурой "
                            "(формулы и объекты на неё опираются), но каталог убран: оставлено имя файла.")
        if kill:
            failed = pkg.unlink(rn, set(kill))
            for rid, (label, before, why) in kill.items():
                if rid in failed:
                    pkg.act(Act.UNREMOVABLE, rn, label, before, note="Элемент, ссылающийся на отношение, "
                            "убрать безопасно не удалось: отношение оставлено.")
                else:
                    pkg.act(Act.REMOVED, rn, label, before, "", why)
    if http:
        pkg.act(Act.KEPT, "*.rels", "Внешние гиперссылки http(s)", "%d шт." % http, "",
                "Это содержимое документа, не метаданные: не трогаем. Корпоративные адреса SharePoint и "
                "OneDrive с именем учётной записи в них остаются.")


# --- структура: Content_Types, ZIP-уровень ------------------------------------------------------------


def _sec_content_types(pkg):
    name = "[Content_Types].xml"
    if not pkg.exists(name):
        raise _Bad("в пакете нет [Content_Types].xml")
    gone = {n for n in pkg.drop if n in pkg.nameset}
    P = pkg.patch(name)
    left = {n.rsplit(".", 1)[-1].lower() for n in pkg.names if n not in pkg.drop and "." in n.rsplit("/", 1)[-1]}
    ext_gone = {n.rsplit(".", 1)[-1].lower() for n in gone if "." in n.rsplit("/", 1)[-1]}
    dropped = []
    for n in P.by("Override"):
        part = (n.get("PartName") or "").lstrip("/")
        if part in gone:
            P.remove(n)
            dropped.append(part)
        elif pkg.paranoid and (n.get("ContentType") or "") in _MACRO_CT and any(
                p.lower().endswith("vbaproject.bin") for p in gone):
            P.set_attr(n, "ContentType", _MACRO_CT[n.get("ContentType")])
    for n in P.by("Default"):
        if (n.get("Extension") or "").lower() in ext_gone - left:
            P.remove(n)
    if pkg.commit(name, P):
        pkg.act(Act.KEPT, name, "[Content_Types].xml", "", "",
                "Часть сохранена. Убраны только Override удалённых частей (%d) и Default расширений, которых "
                "в пакете больше нет." % len(dropped))
    else:
        pkg.act(Act.KEPT, name, "[Content_Types].xml", "", "", "Часть сохранена как есть, байт в байт.")


_STRUCT_KEPT = (
    ("word/theme/theme1.xml", "Тема оформления: её отсутствие означает «создан не Word»."),
    ("xl/theme/theme1.xml", "Тема оформления: её отсутствие означает «создан не Excel»."),
    ("ppt/theme/theme1.xml", "Тема оформления: её отсутствие означает «создан не PowerPoint»."),
    ("word/fontTable.xml", "Таблица шрифтов: часть, обязательная для файла от Word."),
    ("word/styles.xml", "Стили: содержимое документа."),
    ("word/webSettings.xml", "Веб-настройки: часть, обязательная для файла от Word."),
    ("xl/styles.xml", "Стили: содержимое книги."),
)


def _sec_structure(pkg):
    for nm, why in _STRUCT_KEPT:
        if pkg.exists(nm):
            pkg.act(Act.KEPT, nm, nm.rsplit("/", 1)[-1], "", "", "Структурное: сохранено во всех профилях. " + why)


_TIME_EXTRAS = {0x5455: "UT", 0x5855: "Unix", 0x7855: "Unix2", 0x000A: "NTFS"}


def _extra_times(zf):
    """Записи, у которых в extra-полях лежит точное время (UT, NTFS, Unix): оно переживает приведение
    DOS-времени к 1980-01-01. Office таких полей не пишет, их добавляют архиваторы при ручной пересборке."""
    hit = 0
    for i in zf.infolist():
        ex, q = i.extra, 0
        while q + 4 <= len(ex):
            hid, hs = struct.unpack_from("<HH", ex, q)
            if hid in _TIME_EXTRAS:
                hit += 1
                break
            q += 4 + hs
    return hit


def _plan_times(pkg):
    """Нужно ли привести таймстемпы ZIP к 1980-01-01. Решение принимает здесь, исполняет _set_times."""
    ents = zipfix.entries(pkg.res.src)
    odd = [e for e in ents if tuple(e.date_time) != EPOCH]
    extra = _extra_times(pkg.zf)
    if extra:
        pkg.act(Act.UNREMOVABLE, "ZIP", "Точное время в extra-полях записей", "%d из %d записей" % (extra, len(ents)),
                note="Поля UT/NTFS/Unix в заголовках ZIP хранят время сохранения с точностью до секунды и "
                "переживают приведение DOS-времени. Убрать их без пересборки заголовков нельзя, а "
                "пересборка выдала бы чистку: поля оставлены.")
    if not odd:
        return False
    low = pkg.appname.lower()
    ms = any(w in low for w in ("microsoft", "word", "excel", "powerpoint"))
    if ms or not pkg.appname or pkg.paranoid:
        lo = min(e.date_time for e in odd)
        hi = max(e.date_time for e in odd)
        pkg.act(Act.NORMALIZED, "ZIP", "Таймстемпы записей контейнера",
                "%04d-%02d-%02d %02d:%02d:%02d … %04d-%02d-%02d %02d:%02d:%02d (%d из %d записей)"
                % (tuple(lo) + tuple(hi) + (len(odd), len(ents))), "1980-01-01 00:00:00",
                "Office ставит всем записям 1980-01-01 00:00. Реальное время означает, что пакет собран "
                "библиотекой: время работы утекает через контейнер даже при вычищенном core.xml. Приведено к "
                "виду Word (это не подмена даты документа, а контейнерный артефакт).")
        return True
    pkg.act(Act.KEPT, "ZIP", "Таймстемпы записей контейнера", "%d из %d записей с реальным временем"
            % (len(odd), len(ents)), "", "Application=%s: чужое приложение, оно могло писать реальное время "
            "само, приведение к 1980-01-01 нарушило бы согласованность профиля. Время сохранения при этом "
            "остаётся видимым через контейнер." % clip(pkg.appname, 60))
    return False


def _set_times(path):
    """Прописать 1980-01-01 00:00:00 в локальные заголовки и центральный каталог на месте
    (сжатые данные не трогаем). Бросает при любой неожиданности: файл тогда не выдаётся."""
    ents = zipfix.entries(path)
    with zipfile.ZipFile(path) as zf:
        cd = zf.start_dir
    with open(path, "r+b") as fh:
        for e in ents:
            fh.seek(e.header_offset + 10)
            fh.write(struct.pack("<HH", 0, 0x21))
        fh.seek(cd)
        for _ in ents:
            hdr = fh.read(46)
            if len(hdr) != 46 or hdr[:4] != b"PK\x01\x02":
                raise _Bad("центральный каталог разобрался не так, как ожидалось")
            n, m, k = struct.unpack_from("<3H", hdr, 28)
            at = fh.tell() - 46
            fh.seek(at + 12)
            fh.write(struct.pack("<HH", 0, 0x21))
            fh.seek(at + 46 + n + m + k)


# ============================================================================
#  Проверка результата (пишется здесь, а не доверяется zipfix и нашим же правкам)
# ============================================================================


def verify_ooxml(src, dst) -> dict:
    """Независимая проверка выходного пакета против входного. Не бросает.

    ok=True, если: ZIP открывается и проходит testzip(); [Content_Types].xml на месте (и первый,
    если был первым у входа); порядок записей тот же с точностью до удалённых; каждая ЗАМЕНЁННАЯ
    XML-часть разбирается строго; ни одно отношение и ни один Override не ведут на часть, которую
    мы убрали; ни одна заменённая часть не ссылается по r:id на отношение, которого больше нет.
    Остальное -- метрики незаметности (times_same, order_preserved): они не условие ok, потому
    что приведение таймстемпов к 1980-01-01 -- осознанное действие."""
    out = {"ok": False, "opens": False, "testzip": False, "content_types_present": False,
           "content_types_first": False, "order_preserved": False, "times_same": False,
           "entries_src": 0, "entries_dst": 0, "dropped": [], "edited": [], "missing_targets": [],
           "broken_rels": [], "broken_overrides": [], "bad_xml": [], "dangling_refs": [], "errors": []}
    try:
        es, ed = zipfix.entries(src), zipfix.entries(dst)
        out["entries_src"], out["entries_dst"] = len(es), len(ed)
        sn, dn = [e.name for e in es], [e.name for e in ed]
        sset, dset = set(sn), set(dn)
        out["dropped"] = [n for n in sn if n not in dset]
        out["order_preserved"] = dn == [n for n in sn if n in dset]
        sdt = {e.name: tuple(e.date_time) for e in es}
        out["times_same"] = all(sdt.get(e.name) == tuple(e.date_time) for e in ed)
        scrc = {e.name: e.crc for e in es}
        out["edited"] = [e.name for e in ed if scrc.get(e.name) != e.crc]
        out["content_types_present"] = "[Content_Types].xml" in dset
        out["content_types_first"] = dn[:1] == ["[Content_Types].xml"]
        with zipfile.ZipFile(dst) as zf, zipfile.ZipFile(src) as zs:
            out["opens"] = True
            out["testzip"] = zf.testzip() is None
            for rn in dn:
                if not rn.endswith(".rels"):
                    continue
                for n in _parse(zf.read(rn)):
                    if n.name != "Relationship":
                        continue
                    tgt, mode = n.get("Target") or "", n.get("TargetMode") or ""
                    if mode == "External" or tgt.startswith("#") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", tgt):
                        continue
                    part = _resolve(rn, tgt)
                    if part and part not in dset:
                        out["missing_targets"].append("%s -> %s" % (rn, part))
                        if part in sset:
                            out["broken_rels"].append("%s -> %s" % (rn, part))
            if "[Content_Types].xml" in dset:
                for n in _parse(zf.read("[Content_Types].xml")):
                    part = (n.get("PartName") or "").lstrip("/") if n.name == "Override" else ""
                    if part and part not in dset and part in sset:
                        out["broken_overrides"].append(part)
            for nm in out["edited"]:
                if nm.endswith((".xml", ".rels", ".vml")):
                    try:
                        _well_formed(zf.read(nm))
                    except _Bad as exc:
                        out["bad_xml"].append("%s: %s" % (nm, exc))
            # Ссылки на убранные отношения: смотрим владельца КАЖДОГО изменившегося или исчезнувшего
            # .rels, даже если сам владелец не правился (его могли забыть почистить).
            for rn in sn:
                if not rn.endswith(".rels"):
                    continue
                owner = _rels_owner(rn)
                if not owner or owner not in dset or not owner.endswith((".xml", ".vml")):
                    continue
                lost = _rel_ids(zs, sset, owner) - _rel_ids(zf, dset, owner)
                if not lost:
                    continue
                raw = zf.read(owner)
                if not any(q + r.encode("utf-8") + q in raw for r in lost for q in (b'"', b"'")):
                    continue                          # убранных отношений в тексте нет: разбирать нечего
                for n in _parse(raw, lambda _ns, _l, a: any(x[0] in R_NSES for x in a)):
                    for ans, _aloc, v in n.attrs:
                        if ans in R_NSES and v in lost:
                            out["dangling_refs"].append("%s: %s" % (owner, v))
    except Exception as exc:  # noqa: BLE001 -- проверка не бросает
        out["errors"].append("проверка не завершилась: %s" % type(exc).__name__)
        return out
    if not out["opens"]:
        out["errors"].append("результат не открывается как ZIP")
    if not out["testzip"]:
        out["errors"].append("результат не проходит проверку CRC")
    if not out["content_types_present"]:
        out["errors"].append("в результате нет [Content_Types].xml")
    if sn[:1] == ["[Content_Types].xml"] and not out["content_types_first"]:
        out["errors"].append("[Content_Types].xml перестал быть первой записью")
    if not out["order_preserved"]:
        out["errors"].append("порядок записей не совпал с исходным")
    for key, text in (("broken_rels", "отношение ведёт на убранную часть"),
                      ("broken_overrides", "Override ведёт на убранную часть"),
                      ("bad_xml", "заменённая часть не разбирается"),
                      ("dangling_refs", "в части остался r:id убранного отношения")):
        for item in out[key][:5]:
            out["errors"].append("%s: %s" % (text, item))
    out["ok"] = not out["errors"]
    return out


def _rel_ids(zf, names, part):
    """Id отношений части part (из её .rels в zf); пусто, если .rels нет."""
    rn = _rels_name(part)
    if rn not in names:
        return set()
    try:
        return {n.get("Id") for n in _parse(zf.read(rn)) if n.name == "Relationship"}
    except _Bad:
        return set()


# ============================================================================
#  Точка входа
# ============================================================================

_SECTIONS = (
    ("Отношения (.rels)", lambda pkg: pkg.index_rels()),
    ("Подпись пакета", _sec_signature),
    ("Личные строки документа", _sec_idents),
    ("docProps/core.xml", _sec_core),
    ("docProps/app.xml", _sec_app),
    ("docProps/custom.xml", _sec_custom),
    ("Превью", _sec_thumbnail),
    ("word/people.xml", _sec_people),
    ("Настройки принтера", _sec_printer),
    ("Макросы VBA", _sec_vba),
    ("Вложенные объекты", _sec_embeddings),
    ("Картинки", _sec_media),
    ("word/settings.xml", _sec_settings),
    ("Тело документа", _sec_body),
    ("Комментарии и авторы", _sec_persons),
    ("xl/workbook.xml", _sec_workbook),
    ("Защита листов и презентации", _sec_protection),
    ("Сводные таблицы", _sec_pivot),
    ("Подключения Excel", _sec_connections),
    ("customXml", _sec_customxml),
    ("Внешние ссылки", _sec_extrels),
    ("[Content_Types].xml", _sec_content_types),
    ("Структура", _sec_structure),
)

_UNREADABLE = {
    "cloud": "файл -- плейсхолдер облачного провайдера: на диске нет содержимого.",
    "denied": "нет доступа на чтение: нет прав или файл занят другим процессом.",
    "missing": "файл не найден.",
    "dir": "это каталог, а не файл.",
    "reparse": "файл -- ссылка или точка повторной обработки.",
}


def _first_broken(zf):
    """Имя первой записи, которая не распаковывается или не сходится по CRC (None -- все целы)."""
    for i in zf.infolist():
        try:
            with zf.open(i) as fh:
                while fh.read(1 << 20):
                    pass
        except Exception:  # noqa: BLE001 -- zlib.error, BadZipFile, NotImplementedError
            return i.filename
    return None


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_file(a, b):
    try:
        if os.path.exists(b) and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return os.path.normcase(os.path.realpath(os.path.abspath(a))) == \
        os.path.normcase(os.path.realpath(os.path.abspath(b)))


def _run(res, state, src, dst, profile, keep, force_signed, depth):
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err("Неизвестный профиль чистки.")
        return
    res.profile = prof.value
    if _same_file(src, dst):
        res.err("Выходной путь совпадает с входным: входной файл не перезаписывается никогда.")
        return
    fmt = sniff.sniff(src)
    res.fmt = fmt
    if fmt == "unreadable":
        reason, code = sniff.unreadable_reason(src)
        res.err("Файл не очищен: %s" % _UNREADABLE.get(reason, "файл не читается (errno %s)." % code))
        return
    size = os.path.getsize(src)
    if size == 0:
        res.err("Файл пуст (0 байт), возможно, не докачался. Файл не очищен.")
        return
    ents = zipfix.entries(src)
    if fmt not in ("docx", "xlsx", "pptx"):
        if fmt == "zip" and not ents:
            res.err("ZIP-контейнер не разбирается: файл повреждён или обрезан. Файл не очищен.")
        else:
            res.err("Не пакет OOXML (формат «%s»): чистка docx/xlsx/pptx к нему неприменима." % sniff.pretty(fmt))
        return
    if size > MAX_INPUT:
        res.err("Файл больше %d МБ: чистка не выполнена." % (MAX_INPUT >> 20))
        return
    if not ents:
        res.err("ZIP-контейнер не разбирается: файл повреждён, обрезан или это не ZIP.")
        return
    unc, cmp_ = sum(e.file_size for e in ents), sum(e.compress_size for e in ents)
    if unc > zipfix.MAX_TOTAL_UNPACKED or (unc > zipfix.MIN_BOMB_UNPACKED and unc / max(cmp_, 1) > zipfix.MAX_RATIO):
        res.err("Подозрение на zip-бомбу: распакованный объём %d байт при сжатом %d. Файл не очищен." % (unc, cmp_))
        return
    sha_before = _sha(src)
    state["tmp"] = tempfile.mkdtemp(prefix=".ooxml_")
    state["work"] = dst + ".ooxml"
    try:
        zf = zipfile.ZipFile(src)
    except Exception as exc:  # noqa: BLE001
        res.err("ZIP-контейнер не открывается: %s." % type(exc).__name__)
        return
    with zf:
        pkg = _Pkg(res, zf, prof, keep, depth, state["tmp"], force_signed)
        for title, fn in _SECTIONS:
            _step(pkg, title, fn)
        _step(pkg, "Таймстемпы ZIP", lambda p: state.__setitem__("times", _plan_times(p)))
        if any(a.act is Act.FAILED for a in res.actions):
            res.err("Файл не выдан: часть секций чистки завершилась сбоем, чистота не гарантирована.")
            return
        replace, drop = pkg.result()
        rep = zipfix.rebuild(src, state["work"], replace=replace, drop=drop, strip_slack=True)
        if not rep.ok:
            broken = _first_broken(zf)
            if broken:
                res.err("Исходный файл повреждён: запись «%s» не читается или не проходит проверку CRC." % clip(broken, 80))
            for e in rep.errors:
                res.err(e)
            return
        if rep.slack_bytes or rep.comment_len:
            res.act(Act.REMOVED, "ZIP", "Скрытые байты вне записей контейнера",
                    "%d байт" % rep.slack_bytes, "",
                    "Голова файла, щели между записями, мусор после конца архива%s. Office их не пишет: это "
                    "классический тайник."
                    % ("; комментарий архива %d байт" % rep.comment_len if rep.comment_len else ""))
    if state.get("times"):
        _set_times(state["work"])
    v = verify_ooxml(src, state["work"])
    if not v["ok"]:
        for e in v["errors"]:
            res.err(e)
        res.err("Проверка результата не пройдена: файл не выдан.")
        return
    if _sha(src) != sha_before:
        res.err("Входной файл изменился во время работы: результат ненадёжен, файл не выдан.")
        return
    os.replace(state["work"], dst)
    res.ok = True


def clean_ooxml(src, dst, profile=Profile.STEALTH, keep=None, *, force_signed=False, _depth=0) -> CleanResult:
    """Очистить docx/xlsx/pptx src -> dst. Вход не модифицируется никогда. Исключений наружу не
    бросает: при отказе ok=False, причина в errors, dst не создан и существующий dst не тронут.

    ok=True означает: dst записан, проходит verify_ooxml (ZIP открывается, testzip, части и ссылки
    целы, заменённые части разбираются строго), вход не изменился. Чистоту доказывает не это, а
    повторный inspect_file(dst) диспетчера.

    profile -- STEALTH (дефолт) или PARANOID; REGENERATE трактуется как PARANOID.
    keep -- whitelist (см. docstring модуля). force_signed -- чистить подписанный пакет, уничтожив
    подпись. _depth -- служебный: глубина вложенности при рекурсии в embeddings/."""
    res = CleanResult(src=str(src), dst=str(dst), fmt="unknown",
                      profile=str(getattr(profile, "value", profile)))
    state = {}
    try:
        s, d = os.fspath(src), os.fspath(dst)
        os.makedirs(os.path.dirname(os.path.abspath(d)), exist_ok=True)
        _run(res, state, s, d, profile, keep, force_signed, _depth)
    except _Refuse as exc:
        res.err(exc)
    except Exception as exc:  # noqa: BLE001 -- до пользователя не должно долететь ничего
        res.err("Внутренняя ошибка чистки OOXML: %s: %s" % (type(exc).__name__, exc))
    finally:
        if state.get("tmp"):
            shutil.rmtree(state["tmp"], ignore_errors=True)
        for p in (state.get("work"), (state.get("work") or "") + ".part"):
            if p:
                try:
                    os.remove(p)
                except OSError:
                    pass
    if not res.ok:
        # Файл не выдан: список «что сделали» над невыданным файлом только вводит в заблуждение.
        res.actions[:] = [a for a in res.actions if a.act is Act.FAILED]
    return res


# ============================================================================
#  Самопроверка и CLI:
#      PYTHONIOENCODING=utf-8 python -m core.clean_ooxml                 -- синтетика
#      PYTHONIOENCODING=utf-8 python -m core.clean_ooxml --real файл...  -- метрики по настоящим
#                                                                           файлам (содержимое не печатается)
# ============================================================================

_D = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"

# Заранее известные утечки: всё это (длиннее 5 символов) не должно пережить чистку.
K = {
    "creator": "Иванов Иван Иванович", "lmb": "Петрова Анна Сергеевна",
    "company": "ООО Ромашка-Тест", "manager": "Сидоров Сергей Петрович",
    "msip": "a.smirnova@romashka-test.ru", "site": "72f988bf-86f1-41af-91ab-2d7cd011db47",
    "ctid": "0x0101009ABC1234567890", "tpl": "file:///C:/Users/ivanov/AppData/Roaming/Microsoft/Templates/Otchet.dotm",
    "mm": "file:///C:/Users/ivanov/Documents/clients.xlsx",
    "conn": "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=\\\\FS01\\buh\\clients.xlsx",
    "hash": "Aq3fGh7KzXw9Pq2LmN4oQ5rS6tU7vW8xYz==", "salt": "Zz09aBcDeFg1HiJk",
    "docvar": "\\\\FS01\\buh\\baza.accdb", "rev": "Кузнецова Анастасия Викторовна",
    "ed": "petrov@romashka-test.ru", "pres": "a.kuznetsova@romashka-test.ru",
    "hdr": "Смирнов Дмитрий Дмитриевич", "printer": "HP LaserJet M404 FLOOR3-NYC",
    "mailto": "mailto:hr@romashka-test.ru", "filelink": "file:///C:/Users/ivanov/Documents/spec.pdf",
    "emb": "Встроенный Автор Табличный",
}


def _zip_write(path, parts, dt=EPOCH, stored=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in parts:
            if isinstance(body, str):
                body = body.encode("utf-8")
            zi = zipfile.ZipInfo(name, dt)
            zi.compress_type = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
            zf.writestr(zi, body)
    return path


def _rels_xml(items):
    """items: [(Id, Type-хвост или полный Type, Target, External?)]"""
    out = [_D, '<Relationships xmlns="%s">' % _NS["rel"]]
    for rid, typ, tgt, ext in items:
        typ = typ if typ.startswith("http") else _RT + typ
        out.append('<Relationship Id="%s" Type="%s" Target="%s"%s/>'
                   % (rid, typ, tgt.replace("&", "&amp;"), ' TargetMode="External"' if ext else ""))
    out.append("</Relationships>")
    return "".join(out)


def _ct_xml(defaults, overrides):
    out = [_D, '<Types xmlns="%s">' % _NS["ct"]]
    out += ['<Default Extension="%s" ContentType="%s"/>' % kv for kv in defaults]
    out += ['<Override PartName="%s" ContentType="%s"/>' % kv for kv in overrides]
    out.append("</Types>")
    return "".join(out)


def _devmode(name):
    raw = name.encode("utf-16le")
    return raw + b"\x00" * (64 - len(raw)) + b"\x01\x00\x00\x00" * 40


def _core(creator, lmb, title="", extra="", created="2024-03-01T10:00:00Z", modified="2024-03-05T14:30:00Z"):
    return (_D + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dc:title>%s</dc:title><dc:creator>%s</dc:creator><cp:keywords>отчёт; квартал</cp:keywords>'
            '<cp:lastModifiedBy>%s</cp:lastModifiedBy><cp:revision>7</cp:revision>%s'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
            % (title, creator, lmb, extra, created, modified))


def _app(app="Microsoft Office Word", ver="16.0000", company="", manager=""):
    return (_D + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<Template>Normal.dotm</Template><TotalTime>187</TotalTime><Pages>4</Pages><Words>1250</Words>'
            '<Characters>7100</Characters><Application>%s</Application><DocSecurity>0</DocSecurity>'
            '<Lines>60</Lines><Paragraphs>14</Paragraphs><ScaleCrop>false</ScaleCrop><Manager>%s</Manager>'
            '<Company>%s</Company><LinksUpToDate>false</LinksUpToDate><CharactersWithSpaces>8300</CharactersWithSpaces>'
            '<SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>%s</AppVersion>'
            '</Properties>' % (app, manager, company, ver))


def _custom(props):
    out = [_D, '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
           'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">']
    for i, (n, v) in enumerate(props, 2):
        out.append('<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="%d" name="%s">'
                   '<vt:lpwstr>%s</vt:lpwstr></property>' % (i, n, v))
    out.append("</Properties>")
    return "".join(out)


def _tiny_jpeg(leaky=True):
    """JPEG с EXIF (GPS, серийник): из фикстур проекта. Без tests.fixtures -- минимальный JFIF."""
    try:
        from tests.fixtures import make_jpeg
        d = tempfile.mkdtemp(prefix="oox_jpg_")
        try:
            with open(make_jpeg(d)[0], "rb") as fh:
                return fh.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
                b"\xff\xd9")


def _fx_embedded_xlsx():
    """Встроенная книга (как диаграмма в Word): у неё свои docProps со своим автором."""
    ct = _ct_xml([("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                  ("xml", "application/xml")],
                 [("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
                  ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml")])
    rels = _rels_xml([("rId1", "officeDocument", "xl/workbook.xml", False),
                      ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                       "docProps/core.xml", False)])
    wb = _D + '<workbook xmlns="%s"><sheets><sheet name="Лист1" sheetId="1"/></sheets></workbook>' % _NS["x"]
    tmp = os.path.join(tempfile.mkdtemp(prefix="oox_emb_"), "e.xlsx")
    try:
        _zip_write(tmp, [("[Content_Types].xml", ct), ("_rels/.rels", rels), ("xl/workbook.xml", wb),
                         ("docProps/core.xml", _core(K["emb"], K["emb"]))])
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)


def _fx_docx(dt=EPOCH, ole=False, app="Microsoft Office Word", all_leaky_custom=False, jpeg=None):
    """Word-подобный .docx со всеми утечками из списка задачи. ole=True добавляет встроенный OLE .bin
    и встроенную книгу .xlsx."""
    W = _NS["w"]
    jpeg = jpeg or _tiny_jpeg()
    doc = (_D + '<w:document xmlns:w="%s" xmlns:r="%s" xmlns:o="urn:schemas-microsoft-com:office:office" '
           'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:mc="%s" '
           'xmlns:w16du="http://schemas.microsoft.com/office/word/2023/wordml/word16du" mc:Ignorable="w16du">'
           '<w:body><w:p w:rsidR="00B54A06" w:rsidRDefault="00B54A06"><w:bookmarkStart w:id="0" w:name="_GoBack"/>'
           '<w:bookmarkEnd w:id="0"/>'
           '<w:ins w:id="1" w:author="%s" w:date="2024-05-01T10:00:00Z" w16du:dateUtc="2024-05-01T07:00:00Z">'
           '<w:r><w:t>вставленный текст</w:t></w:r></w:ins>'
           '<w:del w:id="2" w:author="%s" w:date="2024-05-01T10:01:00Z"><w:r w:rsidDel="006D6EA0">'
           '<w:delText>удалённый текст</w:delText></w:r></w:del>'
           '<w:permStart w:id="3" w:ed="%s" w:edGrp="editors"/><w:r><w:t>защищённая область</w:t></w:r>'
           '<w:permEnd w:id="3"/>'
           '<w:hyperlink r:id="rId20" w:history="1"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
           '<w:t>написать в отдел кадров</w:t></w:r></w:hyperlink>'
           '<w:hyperlink r:id="rId25" w:history="1"><w:r><w:t>сайт</w:t></w:r></w:hyperlink>'
           '</w:p><w:sectPr w:rsidR="00B54A06" w:rsidSect="003E602A"><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
           '</w:body></w:document>' % (W, _NS["r"], _NS["mc"], K["rev"], K["rev"], K["ed"]))
    if ole:
        doc = doc.replace("</w:p><w:sectPr", (
            '<w:r><w:object w:dxaOrig="1440" w:dyaOrig="1440"><v:shape id="_x0000_i1025" type="#_x0000_t75">'
            '<v:imagedata r:id="rId21" o:title=""/></v:shape><o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" '
            'ShapeID="_x0000_i1025" DrawAspect="Content" ObjectID="_1" r:id="rId22"/></w:object></w:r>'
            '<w:r><w:object><o:OLEObject Type="Embed" ProgID="Excel.Sheet.12" r:id="rId26"/></w:object></w:r>'
            '</w:p><w:sectPr'))
    rels = [("rId1", "styles", "styles.xml", False), ("rId2", "settings", "settings.xml", False),
            ("rId3", "theme", "theme/theme1.xml", False), ("rId4", "fontTable", "fontTable.xml", False),
            ("rId5", "webSettings", "webSettings.xml", False),
            ("rId6", "http://schemas.microsoft.com/office/2011/relationships/people", "people.xml", False),
            ("rId7", "comments", "comments.xml", False), ("rId8", "header", "header1.xml", False),
            ("rId20", "hyperlink", K["mailto"], True), ("rId21", "image", "media/image1.jpeg", False),
            ("rId23", "printerSettings", "printerSettings/printerSettings1.bin", False),
            ("rId24", "hyperlink", K["filelink"], True), ("rId25", "hyperlink", "https://example.org/", True)]
    if ole:
        rels += [("rId22", "oleObject", "embeddings/oleObject1.bin", False),
                 ("rId26", "package", "embeddings/Microsoft_Excel_Worksheet.xlsx", False)]
    settings = (_D + '<w:settings xmlns:w="%s" xmlns:r="%s" xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
                '<w:zoom w:percent="100"/><w:trackRevisions/>'
                '<w:documentProtection w:edit="readOnly" w:enforcement="1" w:cryptProviderType="rsaAES" '
                'w:cryptAlgorithmClass="hash" w:hash="%s" w:salt="%s" w:cryptSpinCount="100000"/>'
                '<w:proofState w:spelling="clean" w:grammar="clean"/>'
                '<w:attachedTemplate r:id="rId1"/><w:rsids><w:rsidRoot w:val="00B54A06"/><w:rsid w:val="00B54A06"/>'
                '<w:rsid w:val="006D6EA0"/></w:rsids>'
                '<w:docVars><w:docVar w:name="База" w:val="%s"/><w:docVar w:name="Стадия" w:val="черновик"/></w:docVars>'
                '<w:mailMerge><w:mainDocumentType w:val="formLetters"/><w:dataType w:val="native"/>'
                '<w:connectString w:val="%s"/><w:dataSource r:id="rId2"/></w:mailMerge>'
                '<w15:docId w15:val="{6D2B3E1C-1111-4222-8333-AAAABBBBCCCC}"/></w:settings>'
                % (W, _NS["r"], K["hash"], K["salt"], K["docvar"], K["conn"].replace("\\", "\\")))
    comments = (_D + '<w:comments xmlns:w="%s"><w:comment w:id="0" w:author="%s" w:date="2024-05-02T09:00:00Z" '
                'w:initials="КА"><w:p><w:r><w:t>комментарий</w:t></w:r></w:p></w:comment></w:comments>' % (W, K["rev"]))
    header = (_D + '<w:hdr xmlns:w="%s"><w:p w:rsidR="00C11111"><w:ins w:id="9" w:author="%s" '
              'w:date="2024-05-03T09:00:00Z"><w:r><w:t>колонтитул</w:t></w:r></w:ins></w:p></w:hdr>' % (W, K["hdr"]))
    people = (_D + '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
              '<w15:person w15:author="%s"><w15:presenceInfo w15:providerId="AD" w15:userId="%s"/></w15:person>'
              '</w15:people>' % (K["rev"], K["pres"]))
    cext = (_D + '<w16cex:commentsExtensible xmlns:w16cex="http://schemas.microsoft.com/office/word/2018/wordml/cex">'
            '<w16cex:commentExtensible w16cex:durableId="1A2B3C4D" w16cex:dateUtc="2024-05-02T06:00:00Z"/>'
            '</w16cex:commentsExtensible>')
    styles = (_D + '<w:styles xmlns:w="%s"><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/>'
              '<w:rsid w:val="00B54A06"/></w:style></w:styles>' % W)
    settings_rels = _rels_xml([("rId1", "attachedTemplate", K["tpl"], True),
                               ("rId2", "mailMergeSource", K["mm"], True)])
    props = [("MSIP_Label_9f1_SetBy", K["msip"]), ("MSIP_Label_9f1_SiteId", K["site"]),
             ("ContentTypeId", K["ctid"])]
    if not all_leaky_custom:
        props += [("ProjectCode", "ALPHA-1")]
    label = (_D + '<clbl:labelList xmlns:clbl="http://schemas.microsoft.com/office/2020/mipLabelMetadata">'
             '<clbl:label id="{9f1}" siteId="{%s}" setBy="%s" enabled="1"/></clbl:labelList>' % (K["site"], K["msip"]))
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("jpeg", "image/jpeg")]
    overrides = [("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
                 ("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
                 ("/word/settings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"),
                 ("/word/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
                 ("/word/fontTable.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml"),
                 ("/word/webSettings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml"),
                 ("/word/people.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml"),
                 ("/word/comments.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"),
                 ("/word/commentsExtensible.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtensible+xml"),
                 ("/word/header1.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"),
                 ("/word/printerSettings/printerSettings1.bin", "application/vnd.openxmlformats-officedocument.wordprocessingml.printerSettings"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml"),
                 ("/docProps/custom.xml", "application/vnd.openxmlformats-officedocument.custom-properties+xml"),
                 ("/docMetadata/LabelInfo.xml", "application/vnd.ms-office.classificationlabels+xml")]
    if ole:
        defaults += [("bin", "application/vnd.openxmlformats-officedocument.oleObject"),
                     ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")]
    pkg_rels = _rels_xml([("rId1", "officeDocument", "word/document.xml", False),
                          ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                           "docProps/core.xml", False),
                          ("rId3", _RT + "extended-properties", "docProps/app.xml", False),
                          ("rId4", _RT + "custom-properties", "docProps/custom.xml", False),
                          ("rId5", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
                           "docProps/thumbnail.jpeg", False),
                          ("rId6", "http://schemas.microsoft.com/office/2020/02/relationships/classificationlabels",
                           "docMetadata/LabelInfo.xml", False)])
    parts = [("[Content_Types].xml", _ct_xml(defaults, overrides)), ("_rels/.rels", pkg_rels),
             ("word/_rels/document.xml.rels", _rels_xml(rels)), ("word/document.xml", doc),
             ("word/theme/theme1.xml", _D + "<a:theme xmlns:a=\"%s\" name=\"Office\"/>" % _NS["a"]),
             ("word/settings.xml", settings), ("word/_rels/settings.xml.rels", settings_rels),
             ("word/fontTable.xml", _D + '<w:fonts xmlns:w="%s"/>' % W),
             ("word/webSettings.xml", _D + '<w:webSettings xmlns:w="%s"/>' % W),
             ("docProps/core.xml", _core(K["creator"], K["lmb"], title="Отчёт %s" % K["creator"])),
             ("word/styles.xml", styles), ("word/people.xml", people), ("word/comments.xml", comments),
             ("word/commentsExtensible.xml", cext), ("word/header1.xml", header),
             ("word/printerSettings/printerSettings1.bin", _devmode(K["printer"])),
             ("word/media/image1.jpeg", jpeg), ("docProps/thumbnail.jpeg", jpeg),
             ("docProps/custom.xml", _custom(props)), ("docMetadata/LabelInfo.xml", label),
             ("docProps/app.xml", _app(app, "16.0000", K["company"], K["manager"]))]
    if ole:
        try:
            from tests.fixtures import make_doc
            d = tempfile.mkdtemp(prefix="oox_ole_")
            try:
                with open(make_doc(d)[0], "rb") as fh:
                    ole_bytes = fh.read()
            finally:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:  # noqa: BLE001
            ole_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504
        parts += [("word/embeddings/oleObject1.bin", ole_bytes),
                  ("word/embeddings/Microsoft_Excel_Worksheet.xlsx", _fx_embedded_xlsx())]
    return parts


def _fx_xlsx(dt=EPOCH):
    X = _NS["x"]
    sheet1 = (_D + '<worksheet xmlns="%s" xmlns:r="%s" xmlns:mc="%s" mc:Ignorable="x14ac" '
              'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"><sheetData>'
              '<row r="1" x14ac:dyDescent="0.25"><c r="A1" t="s"><v>0</v></c></row></sheetData>'
              '<hyperlinks><hyperlink ref="A1" r:id="rId1"/><hyperlink ref="A2" r:id="rId2"/></hyperlinks>'
              '<pageSetup orientation="portrait" r:id="rId3"/><drawing r:id="rId4"/></worksheet>'
              % (X, _NS["r"], _NS["mc"]))
    sheet2 = (_D + '<worksheet xmlns="%s" xmlns:r="%s"><sheetData/><sheetProtection sheet="1" password="CC1A" '
              'algorithmName="SHA-512" hashValue="%s" saltValue="%s" spinCount="100000" objects="1"/>'
              '<pageSetup orientation="landscape" r:id="rId1"/></worksheet>' % (X, _NS["r"], K["hash"], K["salt"]))
    wb = (_D + '<workbook xmlns="%s" xmlns:r="%s" xmlns:mc="%s" mc:Ignorable="x15" '
          'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main">'
          '<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="10531"/>'
          '<fileSharing userName="Иванов Иван Иванович" reservationPassword="CC1A"/><workbookPr codeName="ThisWorkbook"/>'
          '<mc:AlternateContent xmlns:mc="%s"><mc:Choice Requires="x15">'
          '<x15ac:absPath url="/Users/ivanov/Sites/scorecard/public/files/" '
          'xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"/></mc:Choice></mc:AlternateContent>'
          '<workbookProtection workbookAlgorithmName="SHA-512" workbookHashValue="%s" workbookSaltValue="%s" '
          'workbookSpinCount="100000" lockStructure="1"/>'
          '<sheets><sheet name="Данные" sheetId="1" r:id="rId1"/><sheet name="Скрытый_Лист" sheetId="2" state="hidden" r:id="rId2"/>'
          '</sheets><definedNames><definedName name="Внешняя">\'C:\\Users\\ivanov\\Documents\\[budget.xlsx]Sheet1\'!$A$1</definedName>'
          '<definedName name="Таблица">Table1[Сумма]</definedName></definedNames>'
          '<externalReferences><externalReference r:id="rId5"/></externalReferences></workbook>'
          % (X, _NS["r"], _NS["mc"], _NS["mc"], K["hash"], K["salt"]))
    wb_rels = _rels_xml([("rId1", "worksheet", "worksheets/sheet1.xml", False),
                         ("rId2", "worksheet", "worksheets/sheet2.xml", False),
                         ("rId3", "styles", "styles.xml", False), ("rId4", "theme", "theme/theme1.xml", False),
                         ("rId5", "externalLink", "externalLinks/externalLink1.xml", False),
                         ("rId6", "pivotCacheDefinition", "pivotCache/pivotCacheDefinition1.xml", False),
                         ("rId7", "connections", "connections.xml", False),
                         ("rId8", "http://schemas.microsoft.com/office/2017/10/relationships/person", "persons/person.xml", False)])
    s1_rels = _rels_xml([("rId1", "hyperlink", K["mailto"], True), ("rId2", "hyperlink", "https://example.org/", True),
                         ("rId3", "printerSettings", "../printerSettings/printerSettings1.bin", False),
                         ("rId4", "drawing", "../drawings/drawing1.xml", False)])
    s2_rels = _rels_xml([("rId1", "printerSettings", "../printerSettings/printerSettings2.bin", False)])
    ext = (_D + '<externalLink xmlns="%s" xmlns:r="%s"><externalBook r:id="rId1"><sheetNames><sheetName val="Sheet1"/>'
           '</sheetNames></externalBook></externalLink>' % (X, _NS["r"]))
    ext_rels = _rels_xml([("rId1", "externalLinkPath", "file:///C:\\Users\\ivanov\\Documents\\budget.xlsx", True)])
    pivot = (_D + '<pivotCacheDefinition xmlns="%s" xmlns:r="%s" r:id="rId1" refreshedBy="%s" refreshedDate="45000.5" '
             'createdVersion="6"/>' % (X, _NS["r"], K["rev"]))
    conns = (_D + '<connections xmlns="%s"><connection id="1" name="ERP" description="подключение к ERP" type="1">'
             '<dbPr connection="DRIVER=SQL Server;SERVER=sql01.corp.local;DATABASE=erp;UID=ivanov;Trusted_Connection=yes" '
             'command="SELECT * FROM dbo.Orders"/></connection>'
             '<connection id="2" name="Query - Таблица1" description="Подключение к запросу" type="5">'
             '<dbPr connection="Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=Таблица1" '
             'command="SELECT * FROM [Таблица1]"/></connection></connections>' % X)
    persons = (_D + '<personList xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">'
               '<person displayName="%s" id="{11111111-2222-3333-4444-555555555555}" userId="%s" providerId="AD"/>'
               '</personList>' % (K["rev"], K["pres"]))
    thr = (_D + '<ThreadedComments xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments">'
           '<threadedComment ref="A1" dT="2024-05-02T09:00:00.00" personId="{11111111-2222-3333-4444-555555555555}" '
           'id="{99999999-2222-3333-4444-555555555555}"><text>вопрос</text></threadedComment></ThreadedComments>')
    xlc = (_D + '<comments xmlns="%s"><authors><author>%s</author></authors><commentList>'
           '<comment ref="A1" authorId="0"><text><r><t>заметка</t></r></text></comment></commentList></comments>'
           % (X, K["rev"]))
    sp_props = (_D[:-2] + '<p:properties xmlns:p="http://schemas.microsoft.com/office/2006/metadata/properties" '
                'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><documentManagement><Reviewer>%s</Reviewer>'
                '</documentManagement></p:properties>' % K["rev"])
    sp_ct = ('<?xml version="1.0" encoding="utf-8"?><ct:contentTypeSchema ct:_="" ma:_="" '
             'ma:contentTypeName="Документ" ma:contentTypeID="0x0101%s" '
             'xmlns:ct="http://schemas.microsoft.com/office/2006/metadata/contentType" '
             'xmlns:ma="http://schemas.microsoft.com/office/2006/metadata/properties/metaAttributes">'
             '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"/></ct:contentTypeSchema>' % K["site"])
    sp_forms = ('<?mso-contentType?><FormTemplates xmlns="http://schemas.microsoft.com/sharepoint/v3/contenttype/forms">'
                '<Display>DocumentLibraryForm</Display><Edit>DocumentLibraryForm</Edit></FormTemplates>')
    mashup = ('<?xml version="1.0" encoding="utf-8"?><DataMashup xmlns="http://schemas.microsoft.com/DataMashup">'
              'UEsDBBQAAAAIAAAAAAAAAAAAAAAAAAAAAAAJAAAAQ29uZmlnLw==</DataMashup>')

    def props(i, uris):
        return (_D + '<ds:datastoreItem ds:itemID="{%08d-AAAA-BBBB-CCCC-DDDDEEEEFFFF}" '
                'xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml"><ds:schemaRefs>%s'
                '</ds:schemaRefs></ds:datastoreItem>'
                % (i, "".join('<ds:schemaRef ds:uri="%s"/>' % u for u in uris)))

    def item_rels(i):
        return _rels_xml([("rId1", "customXmlProps", "itemProps%d.xml" % i, False)])

    app = _app("Microsoft Excel", "16.0300", K["company"], "")
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("png", "image/png"), ("bin", "application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings")]
    ov = lambda p, t: (p, "application/vnd.openxmlformats-officedocument.%s+xml" % t)  # noqa: E731
    overrides = [ov("/xl/workbook.xml", "spreadsheetml.sheet.main"), ov("/xl/worksheets/sheet1.xml", "spreadsheetml.worksheet"),
                 ov("/xl/worksheets/sheet2.xml", "spreadsheetml.worksheet"),
                 ov("/xl/externalLinks/externalLink1.xml", "spreadsheetml.externalLink"),
                 ov("/xl/pivotCache/pivotCacheDefinition1.xml", "spreadsheetml.pivotCacheDefinition"),
                 ov("/xl/connections.xml", "spreadsheetml.connections"),
                 ov("/xl/persons/person.xml", "spreadsheetml.person"),
                 ("/xl/threadedComments/threadedComment1.xml", "application/vnd.ms-excel.threadedcomments+xml"),
                 ov("/xl/comments1.xml", "spreadsheetml.comments"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ov("/docProps/app.xml", "extended-properties"), ov("/docProps/custom.xml", "custom-properties")]
    overrides += [("/customXml/itemProps%d.xml" % i, "application/vnd.openxmlformats-officedocument.customXmlProperties+xml")
                  for i in (1, 2, 3, 4)]
    pkg_rels = _rels_xml([("rId1", "officeDocument", "xl/workbook.xml", False),
                          ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                           "docProps/core.xml", False),
                          ("rId3", _RT + "extended-properties", "docProps/app.xml", False),
                          ("rId4", _RT + "custom-properties", "docProps/custom.xml", False)])
    wb_rels2 = wb_rels[:-len("</Relationships>")] + "".join(
        '<Relationship Id="rId%d" Type="%scustomXml" Target="../customXml/item%d.xml"/>' % (20 + i, _RT, i)
        for i in (1, 2, 3, 4)) + "</Relationships>"
    from_png = b"\x89PNG\r\n\x1a\n"
    try:
        from tests.fixtures import make_png
        d = tempfile.mkdtemp(prefix="oox_png_")
        try:
            with open(make_png(d)[0], "rb") as fh:
                from_png = fh.read()
        finally:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    parts = [("[Content_Types].xml", _ct_xml(defaults, overrides)), ("_rels/.rels", pkg_rels),
             ("xl/_rels/workbook.xml.rels", wb_rels2), ("xl/workbook.xml", wb),
             ("xl/worksheets/sheet1.xml", sheet1), ("xl/worksheets/sheet2.xml", sheet2),
             ("xl/worksheets/_rels/sheet1.xml.rels", s1_rels), ("xl/worksheets/_rels/sheet2.xml.rels", s2_rels),
             ("xl/theme/theme1.xml", _D + '<a:theme xmlns:a="%s" name="Office"/>' % _NS["a"]),
             ("xl/styles.xml", _D + '<styleSheet xmlns="%s"/>' % X),
             ("xl/externalLinks/externalLink1.xml", ext), ("xl/externalLinks/_rels/externalLink1.xml.rels", ext_rels),
             ("xl/pivotCache/pivotCacheDefinition1.xml", pivot), ("xl/connections.xml", conns),
             ("xl/persons/person.xml", persons), ("xl/threadedComments/threadedComment1.xml", thr),
             ("xl/comments1.xml", xlc), ("xl/media/image1.png", from_png),
             ("xl/drawings/drawing1.xml", _D + '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"/>'),
             ("xl/printerSettings/printerSettings1.bin", _devmode(K["printer"])),
             ("xl/printerSettings/printerSettings2.bin", _devmode("Canon iR-ADV C5535 buh-02")),
             ("customXml/item1.xml", sp_props), ("customXml/itemProps1.xml", props(1, [
                 "http://schemas.microsoft.com/office/2006/metadata/properties", "http://www.w3.org/2001/XMLSchema"])),
             ("customXml/_rels/item1.xml.rels", item_rels(1)),
             ("customXml/item2.xml", sp_ct), ("customXml/itemProps2.xml", props(2, [
                 "http://schemas.microsoft.com/office/2006/metadata/contentType", "http://www.w3.org/2001/XMLSchema"])),
             ("customXml/_rels/item2.xml.rels", item_rels(2)),
             ("customXml/item3.xml", sp_forms), ("customXml/itemProps3.xml", props(3, [
                 "http://schemas.microsoft.com/sharepoint/v3/contenttype/forms"])),
             ("customXml/_rels/item3.xml.rels", item_rels(3)),
             ("customXml/item4.xml", mashup), ("customXml/itemProps4.xml", props(4, [
                 "http://schemas.microsoft.com/DataMashup"])),
             ("customXml/_rels/item4.xml.rels", item_rels(4)),
             ("docProps/core.xml", _core(K["creator"], K["lmb"])), ("docProps/app.xml", app),
             ("docProps/custom.xml", _custom([("ContentTypeId", K["ctid"]), ("_AuthorEmail", K["msip"])]))]
    return parts


def _fx_pptx(dt=EPOCH):
    P, A, R = _NS["p"], _NS["a"], _NS["r"]
    slide = (_D + '<p:sld xmlns:p="%s" xmlns:a="%s" xmlns:r="%s" xmlns:mc="%s"><p:cSld><p:spTree>'
             '<p:sp><p:txBody><a:p><a:r><a:rPr><a:hlinkClick r:id="rId2"/></a:rPr><a:t>написать</a:t></a:r></a:p></p:txBody></p:sp>'
             '<mc:AlternateContent><mc:Choice Requires="v"><p:graphicFrame><a:graphic><a:graphicData '
             'uri="http://schemas.openxmlformats.org/presentationml/2006/ole"><p:oleObj name="Object 2" r:id="rId3" '
             'progId="Excel.Sheet.12"><p:embed/></p:oleObj></a:graphicData></a:graphic></p:graphicFrame></mc:Choice>'
             '<mc:Fallback><p:graphicFrame><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/presentationml/2006/ole">'
             '<p:oleObj name="Object 2" r:id="rId3" progId="Excel.Sheet.12"><p:embed/></p:oleObj></a:graphicData></a:graphic>'
             '</p:graphicFrame></mc:Fallback></mc:AlternateContent></p:spTree></p:cSld></p:sld>'
             % (P, A, R, _NS["mc"]))
    authors = (_D + '<p:cmAuthorLst xmlns:p="%s"><p:cmAuthor id="0" name="%s" initials="КА" lastIdx="1" clrIdx="0"/></p:cmAuthorLst>'
               % (P, K["rev"]))
    comment = (_D + '<p:cmLst xmlns:p="%s"><p:cm authorId="0" dt="2024-05-02T09:00:00.000" idx="1"><p:pos x="10" y="10"/>'
               '<p:text>вопрос</p:text></p:cm></p:cmLst>' % P)
    pres = (_D + '<p:presentation xmlns:p="%s" xmlns:r="%s"><p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>'
            '<p:modifyVerifier cryptProviderType="rsaAES" cryptAlgorithmClass="hash" hashValue="%s" saltValue="%s" '
            'spinCount="100000"/></p:presentation>' % (P, R, K["hash"], K["salt"]))
    pres_rels = _rels_xml([("rId1", "slide", "slides/slide1.xml", False),
                           ("rId2", "commentAuthors", "commentAuthors.xml", False),
                           ("rId3", "printerSettings", "printerSettings/printerSettings1.bin", False)])
    slide_rels = _rels_xml([("rId1", "comments", "../comments/comment1.xml", False),
                            ("rId2", "hyperlink", K["mailto"], True),
                            ("rId3", "oleObject", "../embeddings/oleObject1.bin", False)])
    defaults = [("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
                ("jpeg", "image/jpeg"), ("bin", "application/vnd.openxmlformats-officedocument.oleObject")]
    overrides = [("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
                 ("/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"),
                 ("/ppt/commentAuthors.xml", "application/vnd.openxmlformats-officedocument.presentationml.commentAuthors+xml"),
                 ("/ppt/comments/comment1.xml", "application/vnd.openxmlformats-officedocument.presentationml.comments+xml"),
                 ("/ppt/printerSettings/printerSettings1.bin", "application/vnd.openxmlformats-officedocument.presentationml.printerSettings"),
                 ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                 ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
    pkg_rels = _rels_xml([("rId1", "officeDocument", "ppt/presentation.xml", False),
                          ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                           "docProps/core.xml", False),
                          ("rId3", _RT + "extended-properties", "docProps/app.xml", False),
                          ("rId4", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail",
                           "docProps/thumbnail.jpeg", False)])
    return [("[Content_Types].xml", _ct_xml(defaults, overrides)), ("_rels/.rels", pkg_rels),
            ("ppt/_rels/presentation.xml.rels", pres_rels), ("ppt/presentation.xml", pres),
            ("ppt/slides/slide1.xml", slide), ("ppt/slides/_rels/slide1.xml.rels", slide_rels),
            ("ppt/commentAuthors.xml", authors), ("ppt/comments/comment1.xml", comment),
            ("ppt/printerSettings/printerSettings1.bin", _devmode(K["printer"])),
            ("ppt/embeddings/oleObject1.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504),
            ("docProps/thumbnail.jpeg", _tiny_jpeg()),
            ("docProps/core.xml", _core(K["creator"], K["lmb"])),
            ("docProps/app.xml", _app("Microsoft Office PowerPoint", "16.0000", K["company"], K["manager"]))]


def _selftest(verbose=False):
    """Синтетика: движок правки XML, три формата в двух профилях, оракул, структура, незаметность,
    идемпотентность, патологии, фазз. Печатает только провалы и итог."""
    import io
    import random

    from .clean import survived_values
    from .inspect import inspect_file

    stat = [0, 0]

    def chk(name, cond, detail=""):
        stat[0] += 1
        if not cond:
            stat[1] += 1
            print("ПРОВАЛ: %s %s" % (name, str(detail)[:400]))
        elif verbose:
            print("  ok: %s" % name)

    td = tempfile.mkdtemp(prefix="oox_self_")
    P = lambda n: os.path.join(td, n)  # noqa: E731
    ST, PA = Profile.STEALTH, Profile.PARANOID

    def read(path, name):
        with zipfile.ZipFile(path) as z:
            return z.read(name)

    def txt(path, name):
        return read(path, name).decode("utf-8")

    def names(path):
        with zipfile.ZipFile(path) as z:
            return [i.filename for i in z.infolist()]

    def build(name, parts, dt=EPOCH, **kw):
        return _zip_write(P(name), parts, dt, **kw)

    def leftovers():
        return [f for f in os.listdir(td) if f.endswith((".ooxml", ".part")) or f.startswith(".ooxml_")]

    def run(src, prof, tag, **kw):
        dst = P("out_%s%s" % (tag, os.path.splitext(src)[1]))
        h0 = _sha(src)
        try:
            res = clean_ooxml(src, dst, prof, **kw)
        except Exception as exc:  # noqa: BLE001
            chk("%s: не бросает" % tag, False, repr(exc))
            return None, dst
        chk("%s: вход не изменён" % tag, _sha(src) == h0)
        chk("%s: временных файлов не осталось" % tag, not leftovers(), leftovers())
        return res, dst

    def oracle(src, dst):
        b, a = inspect_file(src), inspect_file(dst)
        chk("%s: инспектор не получил новых ошибок разбора (иначе диспетчер скажет «чистота не доказана»)"
            % os.path.basename(dst), len(a.errors) <= len(b.errors), a.errors)
        return b, a, survived_values(b, a)

    def new_signals(b, a):
        return {s.kind for s in a.signals} - {s.kind for s in b.signals}

    def bad(name, src, prof=ST, **kw):
        """Патология: ok=False, есть errors, вход цел, dst не создан, временных файлов нет."""
        dst = P("never.bin")
        if os.path.exists(dst):
            os.remove(dst)
        h0 = _sha(src) if os.path.isfile(src) else None
        try:
            res = clean_ooxml(src, dst, prof, **kw)
        except Exception as exc:  # noqa: BLE001
            chk("%s: не бросает" % name, False, repr(exc))
            return None
        chk("%s: ok=False и есть errors" % name, (not res.ok) and bool(res.errors), (res.ok, res.errors))
        chk("%s: вход цел" % name, h0 is None or _sha(src) == h0)
        chk("%s: dst не создан" % name, not os.path.exists(dst))
        chk("%s: временных файлов нет" % name, not leftovers(), leftovers())
        chk("%s: в errors нет путей" % name, td not in " ".join(res.errors))
        return res

    try:
        # ------------------------------------------------------------------ движок правки XML
        raw = ('\ufeff<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n<r:root xmlns:r="urn:r" xmlns:w="urn:w" '
               'w:z="1" a=\'q"x\'><w:ins w:id="1" w:author="Иван &amp; Ко" w:date="2020-01-01T00:00:00Z" k="&gt;">'
               '<w:r><w:t>text</w:t></w:r></w:ins><Company>ООО Ромашка</Company><Empty/><Pair></Pair>'
               '<x w:author = "A" /><w:hyperlink r:id="rId5"><w:r>link</w:r></w:hyperlink><!-- c <z> -->'
               '<![CDATA[ <z> ]]></r:root>').encode("utf-8")
        E = _Patch(raw, text=True)
        ins = E.by("ins")[0]
        chk("движок: атрибуты в порядке документа с декодированием",
            [a[1] for a in ins.attrs] == ["id", "author", "date", "k"] and ins.get("author") == "Иван & Ко" and ins.get("k") == ">")
        chk("движок: пустые теги определены", E.by("Empty")[0].empty and E.by("x")[0].empty and not E.by("Pair")[0].empty)
        chk("движок: без правок вывод равен входу байт в байт", _Patch(raw).finish() == raw)
        E.set_attr(ins, "author", "Author")
        E.del_attr(ins, "date")
        E.set_text(E.by("Company")[0], "")
        E.set_text(E.by("Empty")[0], "a<b")
        E.set_attr(E.by("x")[0], "author", "Au'th")
        E.unwrap(E.by("hyperlink")[0])
        out = E.finish().decode("utf-8")
        chk("движок: правки точечные, остальное байты исходника",
            out == raw.decode("utf-8").replace('w:author="Иван &amp; Ко" w:date="2020-01-01T00:00:00Z"', 'w:author="Author"')
            .replace("<Company>ООО Ромашка</Company>", "<Company></Company>").replace("<Empty/>", "<Empty>a&lt;b</Empty>")
            .replace('w:author = "A"', 'w:author = "Au\'th"')
            .replace('<w:hyperlink r:id="rId5"><w:r>link</w:r></w:hyperlink>', "<w:r>link</w:r>"), out)
        E = _Patch(raw)
        E.remove(E.by("ins")[0])
        E.set_attr(E.by("ins")[0], "author", "Z")
        chk("движок: правка внутри удалённого элемента игнорируется", b"w:ins" not in E.finish())
        E = _Patch(raw)
        E.set_attr(E.by("x")[0], "author", "B")
        E.set_attr(E.by("x")[0], "author", "C")
        try:
            E.finish()
            chk("движок: две правки одного места -- одна выигрывает, XML цел", True)
        except _Bad:
            chk("движок: две правки одного места -- отказ, а не порча", True)
        for tag, bad_raw in (("слишком глубокая вложенность", b"<a>" * 4000 + b"</a>" * 4000),
                             ("DOCTYPE", b'<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'),
                             ("UTF-16", "<?xml version='1.0' encoding='utf-16'?><a/>".encode("utf-16")),
                             ("не XML", b"<a><b></a>"), ("пусто", b"")):
            try:
                _parse(bad_raw)
                chk("движок: %s отклонён" % tag, False)
            except _Bad:
                chk("движок: %s отклонён" % tag, True)
        # чужие префиксы, одинарные кавычки, пробелы вокруг '=': правка не привязана к виду документа
        alt = ("<?xml version='1.0' encoding='UTF-8'?>\n<q:document xmlns:q='%s' xmlns:r='%s'><q:body>"
               "<q:ins q:id = '1'  q:author = 'Иванов' q:date='2024-01-01T00:00:00Z'><q:r><q:t>x</q:t></q:r></q:ins>"
               "</q:body></q:document>" % (_NS["w"], _NS["r"])).encode("utf-8")
        E = _Patch(alt)
        n = E.by("ins")[0]
        E.set_attr(n, "author", AUTHOR)
        E.del_attr(n, "date")
        chk("движок: чужой префикс и одинарные кавычки", E.finish().decode("utf-8").count("Author") == 1
            and "q:date" not in E.finish().decode("utf-8"))
        # фазз движка: порча валидного XML никогда не даёт ничего, кроме _Bad
        rnd = random.Random(11)
        for i in range(300):
            b = bytearray(raw)
            for _ in range(rnd.randint(1, 4)):
                at = rnd.randrange(len(b))
                if rnd.random() < .5:
                    b[at] = rnd.getrandbits(8)
                else:
                    del b[at:at + rnd.randint(1, 8)]
            try:
                Pf = _Patch(bytes(b), text=True)
                for n in Pf.nodes[:3]:
                    Pf.set_attr(n, "author", "x") if n.attrs else Pf.remove(n)
                Pf.finish()
            except _Bad:
                pass
            except Exception as exc:  # noqa: BLE001
                chk("движок: фазз %d падает только _Bad" % i, False, repr(exc))
                break
        else:
            chk("движок: 300 порченых XML -- только _Bad", True)

        # ------------------------------------------------------------------ docx (Word-подобный)
        src = build("w.docx", _fx_docx())
        b0 = inspect_file(src)
        chk("фикстура docx действительно течёт", b0.critical >= 15, b0.critical)
        sig0 = {s.kind for s in b0.signals}
        res, dst = run(src, ST, "wS")
        chk("docx STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src, dst)
        chk("docx STEALTH: оракул -- ни одно исходное значение не пережило", not sv,
            [(s.label, s.found_at) for s in sv][:6])
        v = verify_ooxml(src, dst)
        chk("docx STEALTH: verify_ooxml", v["ok"], v["errors"])
        chk("docx STEALTH: нет нового сигнала ai", "ai" not in new_signals(b, a), new_signals(b, a))
        chk("docx STEALTH: сигнал scrubbed -- известное следствие профиля (инспектор считает пустые "
            "creator+lastModifiedBy чисткой)", "scrubbed" in new_signals(b, a))
        with zipfile.ZipFile(dst) as z:
            blob = b"".join(z.read(n) for n in z.namelist() if not n.endswith((".jpeg", ".png")))
        for k in ("creator", "lmb", "company", "manager", "msip", "site", "ctid", "tpl", "mm", "conn", "hash", "salt",
                  "docvar", "rev", "ed", "pres", "hdr", "printer", "mailto", "filelink"):
            chk("docx STEALTH: «%s» нет в байтах результата" % k,
                K[k].encode("utf-8") not in blob and K[k].encode("utf-16le") not in blob)
        nm = names(dst)
        for gone in ("word/people.xml", "docProps/thumbnail.jpeg", "word/printerSettings/printerSettings1.bin",
                     "docMetadata/LabelInfo.xml", "word/_rels/settings.xml.rels"):
            chk("docx STEALTH: %s убран" % gone, gone not in nm)
        for kept in ("word/theme/theme1.xml", "word/fontTable.xml", "word/webSettings.xml", "word/styles.xml",
                     "word/settings.xml", "docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
            chk("docx STEALTH: %s на месте" % kept, kept in nm)
        sn = names(src)
        chk("docx STEALTH: порядок записей сохранён",
            nm == [n for n in sn if n in set(nm)] and nm[0] == "[Content_Types].xml")
        with zipfile.ZipFile(dst) as z:
            chk("docx STEALTH: таймстемпы 1980-01-01 сохранены", all(tuple(i.date_time) == EPOCH for i in z.infolist()))
            chk("docx STEALTH: testzip", z.testzip() is None)
        app, core = txt(dst, "docProps/app.xml"), txt(dst, "docProps/core.xml")
        chk("docx STEALTH: Application и AppVersion как были",
            "<Application>Microsoft Office Word</Application>" in app and "<AppVersion>16.0000</AppVersion>" in app)
        chk("docx STEALTH: Company и Manager обнулены, элементы на месте",
            "<Company></Company>" in app and "<Manager></Manager>" in app)
        chk("docx STEALTH: TotalTime, Words, Template не тронуты",
            "<TotalTime>187</TotalTime>" in app and "<Words>1250</Words>" in app and "<Template>Normal.dotm</Template>" in app)
        chk("docx STEALTH: creator и lastModifiedBy -- пустые элементы, а не удалённые",
            "<dc:creator></dc:creator>" in core and "<cp:lastModifiedBy></cp:lastModifiedBy>" in core)
        chk("docx STEALTH: created и modified не тронуты (не подменены и не обнулены)",
            "2024-03-01T10:00:00Z" in core and "2024-03-05T14:30:00Z" in core)
        chk("docx STEALTH: revision и keywords сохранены", "<cp:revision>7</cp:revision>" in core and "отчёт; квартал" in core)
        chk("docx STEALTH: заголовок с ФИО автора обнулён", "<dc:title></dc:title>" in core)
        doc, sett = txt(dst, "word/document.xml"), txt(dst, "word/settings.xml")
        chk("docx STEALTH: авторы правок -> Author, правки остались",
            doc.count('w:author="Author"') == 2 and "вставленный текст" in doc and "удалённый текст" in doc)
        chk("docx STEALTH: даты правок убраны (w:date и w16du:dateUtc)", "w:date=" not in doc and "dateUtc" not in doc)
        chk("docx STEALTH: rsid остались", "w:rsidR=" in doc and "w:rsidSect=" in doc and "<w:rsid " in sett)
        chk("docx STEALTH: _GoBack остался", "_GoBack" in doc)
        chk("docx STEALTH: permStart без имени редактора, группа осталась",
            '<w:permStart w:id="3" w:edGrp="editors"/>' in doc)
        chk("docx STEALTH: mailto-ссылка снята, её текст остался; http-ссылка цела",
            'r:id="rId20"' not in doc and "написать в отдел кадров" in doc and 'r:id="rId25"' in doc)
        chk("docx STEALTH: шаблон, слияние, хеш пароля убраны",
            "attachedTemplate" not in sett and "mailMerge" not in sett and "w:hash=" not in sett and "w:salt=" not in sett)
        chk("docx STEALTH: защита осталась включённой", 'w:enforcement="1"' in sett and 'w:edit="readOnly"' in sett)
        chk("docx STEALTH: docVar с путём убран, безобидный остался",
            "База" not in sett and 'w:name="Стадия"' in sett)
        chk("docx STEALTH: proofState и docId остались", "proofState" in sett and "docId" in sett)
        com = txt(dst, "word/comments.xml")
        chk("docx STEALTH: комментарий: автор Author, без инициалов и даты",
            'w:author="Author"' in com and "w:initials" not in com and "w:date" not in com and "комментарий" in com)
        chk("docx STEALTH: commentsExtensible без dateUtc", "dateUtc" not in txt(dst, "word/commentsExtensible.xml"))
        chk("docx STEALTH: колонтитул: автор и дата правки", 'w:author="Author"' in txt(dst, "word/header1.xml")
            and "w:date" not in txt(dst, "word/header1.xml") and "w:rsidR" in txt(dst, "word/header1.xml"))
        rl = txt(dst, "word/_rels/document.xml.rels")
        chk("docx STEALTH: .rels: people, принтер, mailto убраны",
            "people" not in rl and "printerSettings" not in rl and "mailto" not in rl)
        chk("docx STEALTH: .rels: путь к файлу -> имя файла, http не тронут",
            'Target="spec.pdf"' in rl and "https://example.org/" in rl and 'TargetMode="External"' in rl)
        ct = txt(dst, "[Content_Types].xml")
        chk("docx STEALTH: Content_Types: Override убранных частей ушли, Default jpeg остался",
            "people" not in ct and "printerSettings" not in ct and "LabelInfo" not in ct and 'Extension="jpeg"' in ct)
        cu = txt(dst, "docProps/custom.xml")
        chk("docx STEALTH: custom.xml: безобидное свойство осталось, pid подряд с 2",
            "ProjectCode" in cu and 'pid="2"' in cu and "MSIP_Label" not in cu and "ContentTypeId" not in cu)
        chk("docx STEALTH: картинка пересобрана без EXIF", read(dst, "word/media/image1.jpeg") != read(src, "word/media/image1.jpeg")
            and b"Exif" not in read(dst, "word/media/image1.jpeg"))
        # байт-точность: у правленых частей объявление, CRLF и корень те же; у остальных -- сырые байты
        sd, dd = read(src, "word/document.xml"), read(dst, "word/document.xml")
        cut = sd.index(b"<w:body>")
        chk("docx STEALTH: document.xml: объявление, CRLF и корневой тег -- байты исходника",
            dd[:cut] == sd[:cut] and dd.endswith(b"</w:body></w:document>"))
        chk("docx STEALTH: core.xml: объявление и CRLF сохранены", read(dst, "docProps/core.xml").startswith(_D.encode()))
        es = {e.name: e for e in zipfix.entries(src)}
        ed = {e.name: e for e in zipfix.entries(dst)}
        same = [n for n in ed if n not in v["edited"]]
        chk("docx STEALTH: нетронутые записи -- те же сжатые байты (размер и CRC)",
            all(es[n].compress_size == ed[n].compress_size and es[n].crc == ed[n].crc for n in same) and len(same) >= 4,
            len(same))
        chk("docx STEALTH: список действий осмыслен",
            res.count(Act.KEPT) >= 8 and res.count(Act.REMOVED) >= 8 and res.count(Act.FAILED) == 0 and res.changed >= 15,
            {a.value: res.count(a) for a in Act})
        # идемпотентность: повторная чистка своего результата ничего не меняет
        res2, dst2 = run(dst, ST, "wS2")
        chk("docx STEALTH: повторная чистка результата -- ok", res2.ok, res2.errors)
        chk("docx STEALTH: повторная чистка не меняет ни байта", res2.ok and _sha(dst2) == _sha(dst))

        res, dst = run(src, PA, "wP")
        chk("docx PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src, dst)
        chk("docx PARANOID: оракул -- ничего не пережило", not sv, [(s.label, s.found_at) for s in sv][:6])
        v = verify_ooxml(src, dst)
        chk("docx PARANOID: verify_ooxml", v["ok"], v["errors"])
        app, core = txt(dst, "docProps/app.xml"), txt(dst, "docProps/core.xml")
        chk("docx PARANOID: Application, AppVersion, TotalTime, Template, статистика убраны",
            not any(t in app for t in ("Application", "AppVersion", "TotalTime", "Template", "<Pages>", "<Words>", "Company", "Manager")))
        chk("docx PARANOID: флаги app.xml остались (иначе пустой app.xml -- ошибка разбора)", "DocSecurity" in app)
        chk("docx PARANOID: даты, ревизия, заголовок, ключевые слова убраны; creator пустой",
            not any(t in core for t in ("created", "modified", "revision", "<dc:title", "keywords")) and "<dc:creator></dc:creator>" in core)
        allx = b"".join(read(dst, n) for n in ("word/document.xml", "word/settings.xml", "word/styles.xml", "word/header1.xml"))
        chk("docx PARANOID: rsid и _GoBack убраны", b"rsid" not in allx and b"_GoBack" not in allx)
        chk("docx PARANOID: custom.xml убран целиком", "docProps/custom.xml" not in names(dst))
        chk("docx PARANOID: правки и текст на месте", "вставленный текст" in txt(dst, "word/document.xml"))
        chk("docx PARANOID: честно виден как обработанный (сигнал ai: нет ни одного rsid)", "ai" in new_signals(b, a))

        # язык (STRUCTURAL) живёт во всех профилях
        lang = _core(K["creator"], K["lmb"], extra="<dc:language>ru-RU</dc:language>")
        p2 = [(n, lang if n == "docProps/core.xml" else bd) for n, bd in _fx_docx()]
        for prof, tag in ((ST, "S"), (PA, "P")):
            r_, d_ = run(build("lang.docx", p2), prof, "lang" + tag)
            chk("dc:language сохранён (%s)" % prof.value, r_.ok and "ru-RU" in txt(d_, "docProps/core.xml"), r_.errors)

        # app.xml и core.xml, в которых после PARANOID не остаётся ни одного свойства, убираются целиком
        # (иначе инспектор получает «ошибку разбора» и диспетчер не докажет чистоту)
        thin = [(n, (_D + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                     '<Application>Microsoft Office Word</Application><AppVersion>16.0000</AppVersion></Properties>'
                     if n == "docProps/app.xml" else (
                         _D + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                         'xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:created>2024-03-01T10:00:00Z</dcterms:created>'
                         '</cp:coreProperties>' if n == "docProps/core.xml" else bd))) for n, bd in _fx_docx()]
        r_, d_ = run(build("thin.docx", thin), PA, "thin")
        b, a, sv = oracle(P("thin.docx"), d_)
        chk("PARANOID: app.xml и core.xml без остатка убраны целиком, инспектор без ошибок разбора",
            r_.ok and "docProps/app.xml" not in names(d_) and "docProps/core.xml" not in names(d_) and not a.errors
            and verify_ooxml(P("thin.docx"), d_)["ok"], (r_.errors, a.errors))
        r_, d_ = run(P("thin.docx"), ST, "thinS")
        chk("STEALTH: те же части остаются на месте", r_.ok and "docProps/app.xml" in names(d_), r_.errors)

        # whitelist пользователя
        r_, d_ = run(src, ST, "keep", keep={"dc:title", "Company", "thumbnail"})
        chk("keep: поля и превью оставлены, остальное вычищено",
            r_.ok and "ООО Ромашка-Тест" in txt(d_, "docProps/app.xml") and "docProps/thumbnail.jpeg" in names(d_)
            and K["creator"].encode() not in read(d_, "docProps/core.xml").replace(("Отчёт " + K["creator"]).encode("utf-8"), b"")
            and any(a_.act is Act.KEPT and "whitelist" in a_.note for a_ in r_.actions), r_.errors)

        # ------------------------------------------------------------------ docx с вложениями
        src_o = build("o.docx", _fx_docx(ole=True))
        res, dst = run(src_o, ST, "oS")
        chk("docx+OLE STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src_o, dst)
        emb = zipfile.ZipFile(io.BytesIO(read(dst, "word/embeddings/Microsoft_Excel_Worksheet.xlsx")))
        chk("docx+OLE STEALTH: встроенная книга вычищена рекурсивно (свой автор убран)",
            K["emb"].encode() not in emb.read("docProps/core.xml") and b"<dc:creator></dc:creator>" in emb.read("docProps/core.xml")
            and emb.testzip() is None)
        chk("docx+OLE STEALTH: OLE остался и честно помечен UNREMOVABLE",
            "word/embeddings/oleObject1.bin" in names(dst) and any(
                a_.act is Act.UNREMOVABLE and "oleObject1.bin" in a_.location for a_ in res.actions))
        chk("docx+OLE STEALTH: выжившее -- только внутри OLE (врать нельзя, и инспектор это видит)",
            all("oleObject1.bin" in s.found_at for s in sv), [(s.label, s.found_at) for s in sv][:5])
        res, dst = run(src_o, PA, "oP")
        chk("docx+OLE PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src_o, dst)
        chk("docx+OLE PARANOID: оракул -- ничего не пережило", not sv, [(s.label, s.found_at) for s in sv][:5])
        v = verify_ooxml(src_o, dst)
        chk("docx+OLE PARANOID: verify_ooxml (нет битых ссылок на убранный объект)", v["ok"], v["errors"])
        d_ = txt(dst, "word/document.xml")
        chk("docx+OLE PARANOID: OLE убран с объектом в тексте, ссылкой и Default bin",
            "word/embeddings/oleObject1.bin" not in names(dst) and 'r:id="rId22"' not in d_ and "rId22" not in txt(dst, "word/_rels/document.xml.rels")
            and 'Extension="bin"' not in txt(dst, "[Content_Types].xml") and "Microsoft_Excel_Worksheet.xlsx" in " ".join(names(dst)))
        # слишком глубокая вложенность не зацикливает чистку
        deep = _fx_embedded_xlsx()
        for _ in range(MAX_DEPTH + 2):
            tmp_in = P("deep.bin")
            _zip_write(tmp_in, [("[Content_Types].xml", _ct_xml([("rels", "x"), ("xml", "y")], [])), ("word/document.xml", "<a/>"),
                                ("word/embeddings/e.docx", deep)])
            with open(tmp_in, "rb") as fh:
                deep = fh.read()
        open(P("deep.docx"), "wb").write(deep)
        r_, d_ = run(P("deep.docx"), ST, "deep")
        chk("вложенность глубже предела: не зацикливается, честно UNREMOVABLE",
            r_.ok and any(a_.act is Act.UNREMOVABLE for a_ in r_.actions), r_.errors)

        # ------------------------------------------------------------------ xlsx
        src_x = build("x.xlsx", _fx_xlsx())
        b0 = inspect_file(src_x)
        chk("фикстура xlsx действительно течёт", b0.critical >= 12, b0.critical)
        res, dst = run(src_x, ST, "xS")
        chk("xlsx STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src_x, dst)
        chk("xlsx STEALTH: оракул -- выжило только оставленное намеренно (DataMashup, подключение Power Query, "
            "структурная ссылка таблицы: инспектор принимает их за утечки)", all(_expected_survivor(s) for s in sv),
            [(s.label, s.found_at) for s in sv if not _expected_survivor(s)][:5])
        chk("xlsx STEALTH: DataMashup помечен UNREMOVABLE",
            any(a_.act is Act.UNREMOVABLE and "item4.xml" in a_.location for a_ in res.actions))
        v = verify_ooxml(src_x, dst)
        chk("xlsx STEALTH: verify_ooxml", v["ok"], v["errors"])
        nm = names(dst)
        chk("xlsx STEALTH: принтеры убраны, .rels листа без принтера убран, у sheet1 остался",
            not any("printerSettings" in n for n in nm) and "xl/worksheets/_rels/sheet2.xml.rels" not in nm
            and "xl/worksheets/_rels/sheet1.xml.rels" in nm)
        s1 = txt(dst, "xl/worksheets/sheet1.xml")
        chk("xlsx STEALTH: pageSetup без r:id, остальное цело",
            '<pageSetup orientation="portrait"/>' in s1 and 'r:id="rId4"' in s1 and "x14ac:dyDescent" in s1)
        chk("xlsx STEALTH: mailto снят, http-ссылка осталась",
            "rId1\"" not in s1.split("<hyperlinks>")[1].split("</hyperlinks>")[0] and 'r:id="rId2"' in s1
            and s1.count("<hyperlink ") == 1)
        wbx = txt(dst, "xl/workbook.xml")
        chk("xlsx STEALTH: absPath и его обёртка убраны", "absPath" not in wbx and "AlternateContent" not in wbx)
        chk("xlsx STEALTH: fileVersion и скрытый лист не тронуты", "rupBuild=\"10531\"" in wbx and 'state="hidden"' in wbx)
        chk("xlsx STEALTH: хеш пароля книги убран, защита структуры осталась",
            "workbookHashValue" not in wbx and 'lockStructure="1"' in wbx)
        chk("xlsx STEALTH: fileSharing без имени и пароля", "userName" not in wbx and "reservationPassword" not in wbx
            and "<fileSharing" in wbx)
        s2 = txt(dst, "xl/worksheets/sheet2.xml")
        chk("xlsx STEALTH: sheetProtection без хеша, защита листа осталась",
            "hashValue" not in s2 and "saltValue" not in s2 and 'password=' not in s2 and 'sheet="1"' in s2
            and 'objects="1"' in s2)
        chk("xlsx STEALTH: внешний путь в определённом имени -> имя книги, структурная ссылка цела",
            "[budget.xlsx]Sheet1" in wbx and "C:\\Users" not in wbx and "Table1[Сумма]" in wbx)
        chk("xlsx STEALTH: externalLink: путь -> имя файла",
            'Target="budget.xlsx"' in txt(dst, "xl/externalLinks/_rels/externalLink1.xml.rels"))
        pv = txt(dst, "xl/pivotCache/pivotCacheDefinition1.xml")
        chk("xlsx STEALTH: refreshedBy пуст, refreshedDate убран", 'refreshedBy=""' in pv and "refreshedDate" not in pv)
        cn = txt(dst, "xl/connections.xml")
        chk("xlsx STEALTH: подключение: сервер, база, логин, запрос, описание убраны; драйвер остался",
            "sql01" not in cn and "UID" not in cn and "dbo.Orders" not in cn and "подключение к ERP" not in cn
            and "DRIVER=SQL Server" in cn)
        chk("xlsx STEALTH: подключение Power Query не тронуто", "Microsoft.Mashup.OleDb.1" in cn and "$Workbook$" in cn)
        chk("xlsx STEALTH: person: имена и email нейтральны",
            K["rev"] not in txt(dst, "xl/persons/person.xml") and K["pres"] not in txt(dst, "xl/persons/person.xml")
            and 'providerId="None"' in txt(dst, "xl/persons/person.xml"))
        chk("xlsx STEALTH: threadedComment: dT (обязательный) оставлен", "dT=" in txt(dst, "xl/threadedComments/threadedComment1.xml"))
        chk("xlsx STEALTH: legacy-комментарии: автор Author, текст на месте",
            "<author>Author</author>" in txt(dst, "xl/comments1.xml") and "заметка" in txt(dst, "xl/comments1.xml"))
        chk("xlsx STEALTH: SharePoint customXml обнулён до корня, части на месте",
            "Reviewer" not in txt(dst, "customXml/item1.xml") and "ma:contentTypeID" not in txt(dst, "customXml/item2.xml")
            and "Display" not in txt(dst, "customXml/item3.xml") and "customXml/item1.xml" in nm)
        chk("xlsx STEALTH: схемы в itemProps убраны, itemID остался",
            "schemaRef " not in txt(dst, "customXml/itemProps1.xml") and "itemID" in txt(dst, "customXml/itemProps1.xml"))
        chk("xlsx STEALTH: DataMashup не тронут", read(dst, "customXml/item4.xml") == read(src_x, "customXml/item4.xml"))
        cu = txt(dst, "docProps/custom.xml") if "docProps/custom.xml" in nm else ""
        chk("xlsx STEALTH: custom.xml: ни одного свойства -- часть убрана целиком", not cu and "custom" not in txt(dst, "_rels/.rels"))
        chk("xlsx STEALTH: PNG: Default png и bin: bin убран (принтеров больше нет), png остался",
            'Extension="bin"' not in txt(dst, "[Content_Types].xml") and 'Extension="png"' in txt(dst, "[Content_Types].xml"))
        chk("xlsx STEALTH: Application сохранён", "<Application>Microsoft Excel</Application>" in txt(dst, "docProps/app.xml"))
        res, dst = run(src_x, PA, "xP")
        chk("xlsx PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src_x, dst)
        chk("xlsx PARANOID: оракул -- выжило только оставленное намеренно",
            all(_expected_survivor(s) for s in sv), [(s.label, s.found_at) for s in sv if not _expected_survivor(s)][:5])
        chk("xlsx PARANOID: fileVersion убран, скрытый лист нет",
            "fileVersion" not in txt(dst, "xl/workbook.xml") and 'state="hidden"' in txt(dst, "xl/workbook.xml"))
        v = verify_ooxml(src_x, dst)
        chk("xlsx PARANOID: verify_ooxml", v["ok"], v["errors"])

        # огромные листы (больше предела правки) не валят чистку: что нельзя убрать -- честно UNREMOVABLE
        mod = sys.modules[__name__]
        saved_max = mod.MAX_XML
        pad = [(n, (bd.replace("<sheetData", "<!--" + "x" * 8000 + "--><sheetData", 1)
                    if n in ("xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml") else bd)) for n, bd in _fx_xlsx()]
        mod.MAX_XML = 6000
        try:
            r_, d_ = run(build("big.xlsx", pad), ST, "bigsheet")
        finally:
            mod.MAX_XML = saved_max
        chk("лист больше предела правки: файл выдан, остальное вычищено", r_.ok and "<dc:creator></dc:creator>" in txt(d_, "docProps/core.xml"),
            r_.errors)
        chk("лист больше предела: принтеры, mailto и хеш пароля листа остались и честно названы UNREMOVABLE",
            r_.ok and "xl/printerSettings/printerSettings1.bin" in names(d_) and "xl/printerSettings/printerSettings2.bin" in names(d_)
            and "mailto" in txt(d_, "xl/worksheets/_rels/sheet1.xml.rels")
            and sum(1 for a_ in r_.actions if a_.act is Act.UNREMOVABLE and "printerSettings" in a_.location) == 2
            and any(a_.act is Act.UNREMOVABLE and "sheetProtection" in a_.label for a_ in r_.actions)
            and any(a_.act is Act.UNREMOVABLE and "mailto" in a_.label for a_ in r_.actions)
            and verify_ooxml(P("big.xlsx"), d_)["ok"], [(a_.act.value, a_.label) for a_ in r_.actions if a_.act is Act.UNREMOVABLE])

        # ------------------------------------------------------------------ pptx
        src_p = build("p.pptx", _fx_pptx())
        res, dst = run(src_p, ST, "pS")
        chk("pptx STEALTH: ok", res.ok, res.errors)
        b, a, sv = oracle(src_p, dst)
        chk("pptx STEALTH: выжило только внутри OLE (UNREMOVABLE)", all("oleObject1.bin" in s.found_at for s in sv),
            [(s.label, s.found_at) for s in sv][:5])
        sl = txt(dst, "ppt/slides/slide1.xml")
        chk("pptx STEALTH: hlinkClick с mailto убран", "hlinkClick" not in sl and "написать" in sl)
        chk("pptx STEALTH: пароль на запись (modifyVerifier) снят целиком, остальное цело",
            "modifyVerifier" not in txt(dst, "ppt/presentation.xml") and "sldIdLst" in txt(dst, "ppt/presentation.xml"))
        chk("pptx STEALTH: commentAuthors: имя Author, инициалы пусты",
            'name="Author"' in txt(dst, "ppt/commentAuthors.xml") and 'initials=""' in txt(dst, "ppt/commentAuthors.xml"))
        chk("pptx STEALTH: comment: dt убран, текст на месте",
            "dt=" not in txt(dst, "ppt/comments/comment1.xml") and "вопрос" in txt(dst, "ppt/comments/comment1.xml"))
        chk("pptx STEALTH: превью и принтер убраны, OLE .bin остался",
            "docProps/thumbnail.jpeg" not in names(dst) and "ppt/printerSettings/printerSettings1.bin" not in names(dst)
            and "ppt/embeddings/oleObject1.bin" in names(dst))
        chk("pptx STEALTH: verify_ooxml", verify_ooxml(src_p, dst)["ok"], verify_ooxml(src_p, dst)["errors"])
        res, dst = run(src_p, PA, "pP")
        chk("pptx PARANOID: ok", res.ok, res.errors)
        b, a, sv = oracle(src_p, dst)
        chk("pptx PARANOID: оракул -- ничего не пережило", not sv, [(s.label, s.found_at) for s in sv][:5])
        sl = txt(dst, "ppt/slides/slide1.xml")
        chk("pptx PARANOID: OLE вместе с AlternateContent убран, ссылка и часть тоже",
            "oleObj" not in sl and "AlternateContent" not in sl and "ppt/embeddings/oleObject1.bin" not in names(dst)
            and "oleObject" not in txt(dst, "ppt/slides/_rels/slide1.xml.rels"))
        v = verify_ooxml(src_p, dst)
        chk("pptx PARANOID: verify_ooxml", v["ok"], v["errors"])

        # ------------------------------------------------------------------ фикстуры проекта
        from tests.fixtures import make_docx, make_docx_wordlike, make_xlsx
        fx = tempfile.mkdtemp(prefix="oox_fx_")
        for label, mk in (("make_docx", make_docx), ("make_docx_wordlike", make_docx_wordlike), ("make_xlsx", make_xlsx)):
            path = mk(fx)[0]
            for prof in (ST, PA):
                r_, d_ = run(path, prof, "%s_%s" % (label, prof.value[0]))
                chk("%s %s: ok" % (label, prof.value), r_.ok, r_.errors)
                if not r_.ok:
                    continue
                b, a, sv = oracle(path, d_)
                chk("%s %s: оракул -- ничего не пережило" % (label, prof.value), not sv,
                    [(s.label, s.found_at) for s in sv][:5])
                chk("%s %s: verify_ooxml" % (label, prof.value), verify_ooxml(path, d_)["ok"], verify_ooxml(path, d_)["errors"])
                if label != "make_docx":
                    chk("%s %s: нет нового ai" % (label, prof.value), "ai" not in new_signals(b, a) or prof is PA,
                        new_signals(b, a))
        r_, d_ = run(make_docx(fx)[0], ST, "mkd_t")
        with zipfile.ZipFile(d_) as z:
            chk("make_docx (реальные таймстемпы, Application=Microsoft): приведены к 1980-01-01",
                all(tuple(i.date_time) == EPOCH for i in z.infolist()))
        chk("make_docx: реальное время ZIP в списке действий", any(
            a_.act is Act.NORMALIZED and "Таймстемпы" in a_.label for a_ in r_.actions))
        # чужое приложение с реальным временем: согласованность профиля важнее, оставляем и говорим об этом
        lo = [(n, (_app("LibreOffice/7.6", "7.6", "", "") if n == "docProps/app.xml" else bd)) for n, bd in _fx_docx()]
        real_dt = (2025, 6, 7, 8, 9, 10)
        r_, d_ = run(build("lo.docx", lo, dt=real_dt), ST, "lo")
        with zipfile.ZipFile(d_) as z:
            chk("чужое приложение: таймстемпы не тронуты и это записано как KEPT",
                r_.ok and all(tuple(i.date_time) == real_dt for i in z.infolist())
                and any(a_.act is Act.KEPT and "Таймстемпы" in a_.label for a_ in r_.actions), r_.errors)
        # скрытые байты вне записей убираются
        raw = bytearray(open(src, "rb").read())
        e2 = raw.rfind(b"PK\x05\x06")
        struct.pack_into("<H", raw, e2 + 20, 12)
        open(P("slack.docx"), "wb").write(bytes(raw) + b"hidden-comm!")
        r_, d_ = run(P("slack.docx"), ST, "slack")
        chk("комментарий архива (тайник) убирается",
            r_.ok and zipfile.ZipFile(d_).comment == b"" and any("Скрытые байты" in a_.label for a_ in r_.actions), r_.errors)

        # документ в нестандартной вёрстке XML: чужой префикс, одинарные кавычки, пробелы у '='
        fancy = []
        for n, bd in _fx_docx():
            if n == "word/document.xml":
                s = bd.replace("w:", "q:").replace("xmlns:w=", "xmlns:q=").replace('w16du:', 'z:').replace("xmlns:w16du", "xmlns:z")
                s = re.sub(r'(\S+)="([^"]*)"', lambda m: "%s = '%s'" % (m.group(1), m.group(2)) if m.group(1).startswith("q:") else m.group(0), s)
                bd = s
            fancy.append((n, bd))
        r_, d_ = run(build("fancy.docx", fancy), ST, "fancy")
        dx = txt(d_, "word/document.xml") if r_.ok else ""
        chk("нестандартная вёрстка XML: авторы и даты чистятся независимо от префикса и кавычек",
            r_.ok and dx.count("q:author = 'Author'") == 2 and "q:date" not in dx and "dateUtc" not in dx
            and K["rev"] not in dx, r_.errors)

        # подписанный пакет
        sigs = _fx_docx() + [("_xmlsignatures/origin.sigs", b""), ("_xmlsignatures/sig1.xml", "<Signature/>"),
                             ("_xmlsignatures/_rels/origin.sigs.rels", _rels_xml([("rId1", "http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/signature", "sig1.xml", False)]))]
        sigs = [(n, (bd.replace("</Relationships>", '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/origin" Target="_xmlsignatures/origin.sigs"/></Relationships>')
                     if n == "_rels/.rels" else bd)) for n, bd in sigs]
        sigs = [(n, (bd.replace("</Types>", '<Default Extension="sigs" ContentType="application/vnd.openxmlformats-package.digital-signature-origin"/><Override PartName="/_xmlsignatures/sig1.xml" ContentType="application/vnd.openxmlformats-package.digital-signature-xmlsignature+xml"/></Types>')
                     if n == "[Content_Types].xml" else bd)) for n, bd in sigs]
        src_s = build("sig.docx", sigs)
        r_ = bad("подписанный пакет без force_signed", src_s)
        chk("подписанный пакет: причина названа", r_ is not None and "подпис" in " ".join(r_.errors).lower())
        r_, d_ = run(src_s, ST, "sig", force_signed=True)
        chk("подписанный пакет с force_signed: подпись убрана целиком, пакет цел",
            r_.ok and not any(n.startswith("_xmlsignatures/") for n in names(d_)) and "sigs" not in txt(d_, "[Content_Types].xml")
            and "signature" not in txt(d_, "_rels/.rels") and verify_ooxml(src_s, d_)["ok"], r_.errors)

        # ссылка из XML на отношение неизвестного вида: часть не удаляется, честно UNREMOVABLE, пакет цел
        weird = [(n, (bd.replace('<w:p w:rsidR="00B54A06"', '<w:p w:rsidR="00B54A06"><w:foo r:id="rId23"/></w:p><w:p w:rsidR="00B54A06"', 1)
                      if n == "word/document.xml" else bd)) for n, bd in _fx_docx()]
        r_, d_ = run(build("weird.docx", weird), ST, "weird")
        chk("ссылка на принтер из чужого элемента: часть оставлена, UNREMOVABLE, пакет цел",
            r_.ok and "word/printerSettings/printerSettings1.bin" in names(d_) and any(
                a_.act is Act.UNREMOVABLE and "printerSettings1.bin" in a_.location for a_ in r_.actions)
            and verify_ooxml(P("weird.docx"), d_)["ok"], r_.errors)

        # много mailto-ссылок: снимаются все, текст ссылок остаётся
        many = []
        for n, bd in _fx_docx():
            if n == "word/_rels/document.xml.rels":
                bd = bd.replace("</Relationships>", "".join(
                    '<Relationship Id="rM%d" Type="%shyperlink" Target="mailto:u%d@romashka-test.ru" TargetMode="External"/>'
                    % (i, _RT, i) for i in range(40)) + "</Relationships>")
            if n == "word/document.xml":
                bd = bd.replace("</w:p><w:sectPr", "".join(
                    '<w:hyperlink r:id="rM%d"><w:r><w:t>почта%d</w:t></w:r></w:hyperlink>' % (i, i) for i in range(40)) + "</w:p><w:sectPr", 1)
            many.append((n, bd))
        r_, d_ = run(build("many.docx", many), ST, "many")
        dm = txt(d_, "word/document.xml") if r_.ok else ""
        chk("40 mailto-ссылок: все сняты, тексты на месте, пакет цел", r_.ok and "rM" not in dm and "почта39" in dm
            and "romashka-test.ru" not in txt(d_, "word/_rels/document.xml.rels") and verify_ooxml(P("many.docx"), d_)["ok"], r_.errors)

        # точное время в extra-полях ZIP переживает приведение DOS-времени: честно UNREMOVABLE
        ut = P("ut.docx")
        with zipfile.ZipFile(ut, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, bd in _fx_docx():
                zi = zipfile.ZipInfo(n, EPOCH)
                zi.extra = struct.pack("<HHBL", 0x5455, 5, 1, 1700000000)
                zf.writestr(zi, bd.encode("utf-8") if isinstance(bd, str) else bd)
        r_, d_ = run(ut, ST, "ut")
        chk("время в extra-полях ZIP: UNREMOVABLE, а не молчание", r_.ok and any(
            a_.act is Act.UNREMOVABLE and a_.location == "ZIP" for a_ in r_.actions), r_.errors)

        # встроенный PDF чистится рекурсивно
        try:
            from tests.fixtures import make_pdf
            pd = tempfile.mkdtemp(prefix="oox_pdf_")
            pdf_path, pdf_exp = make_pdf(pd)
            pdf_bytes = open(pdf_path, "rb").read()
            shutil.rmtree(pd, ignore_errors=True)
            r_, d_ = run(build("pdf.docx", _fx_docx() + [("word/embeddings/embedded.pdf", pdf_bytes)]), ST, "pdf")
            b, a, sv = oracle(P("pdf.docx"), d_)
            chk("встроенный PDF вычищен рекурсивно: ни одно значение не пережило", r_.ok and not sv and read(d_, "word/embeddings/embedded.pdf") != pdf_bytes,
                [(s.label, s.found_at) for s in sv][:4] or r_.errors)
        except ImportError:
            pass

        # действия осмысленны: у каждого есть место и подпись, значения ограничены
        chk("действия: у каждого есть location и label, значения не длиннее предела",
            all(a_.location and a_.label and len(a_.before) <= 400 and len(a_.after) <= 400 for a_ in res.actions) and res.actions)

        # ------------------------------------------------------------------ патологии
        open(P("empty.docx"), "wb").close()
        bad("пустой файл", P("empty.docx"))
        open(P("pk.docx"), "wb").write(b"PK\x03\x04" + b"junk" * 60)
        bad("не-ZIP с сигнатурой PK", P("pk.docx"))
        open(P("junk.docx"), "wb").write(b"x" * 500)
        bad("мусор", P("junk.docx"))
        whole = open(src, "rb").read()
        for cut in (len(whole) // 2, len(whole) - 30, len(whole) - 1):
            open(P("cut.docx"), "wb").write(whole[:cut])
            bad("обрезанный docx (%d)" % cut, P("cut.docx"))
        bad("файла нет", P("нет_такого.docx"))
        bad("каталог вместо файла", td)
        r_ = clean_ooxml(src, src)
        chk("вход == выход: отказ, вход цел", (not r_.ok) and _sha(src) == _sha(src) and r_.errors, r_.errors)
        open(P("keepme.docx"), "wb").write(b"old content")
        r_ = clean_ooxml(P("junk.docx"), P("keepme.docx"))
        chk("отказ не трогает существующий dst", (not r_.ok) and open(P("keepme.docx"), "rb").read() == b"old content")
        r_ = clean_ooxml(src, P("keepme.docx"))
        chk("успех атомарно заменяет существующий dst", r_.ok and open(P("keepme.docx"), "rb").read() != b"old content"
            and zipfile.ZipFile(P("keepme.docx")).testzip() is None and not leftovers(), r_.errors)
        r_ = clean_ooxml(src, P("x.docx"), "garbage")
        chk("неизвестный профиль: ok=False, без исключения", (not r_.ok) and r_.errors)
        r_ = clean_ooxml(src, P("str.docx"), "paranoid")
        chk("профиль строкой принимается", r_.ok, r_.errors)
        r_ = clean_ooxml(src, os.path.join(td, "новый каталог", "глубже", "o.docx"))
        chk("каталог выхода создаётся", r_.ok and os.path.isfile(os.path.join(td, "новый каталог", "глубже", "o.docx")), r_.errors)
        txtfile = build("t.txt", [("a.txt", "x")])
        bad("ZIP без Content_Types (не OOXML)", txtfile)
        # битая часть, которую надо править
        garbage = [(n, ("это вообще не XML" if n == "docProps/core.xml" else bd)) for n, bd in _fx_docx()]
        bad("docx с битой docProps/core.xml", build("g1.docx", garbage))
        dtd = [(n, ('<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><cp:coreProperties xmlns:cp="urn:x"/>'
                    if n == "docProps/core.xml" else bd)) for n, bd in _fx_docx()]
        bad("docx с DOCTYPE в core.xml", build("g2.docx", dtd))
        u16 = [(n, ("<?xml version='1.0' encoding='utf-16'?><a/>".encode("utf-16") if n == "docProps/app.xml" else bd))
               for n, bd in _fx_docx()]
        bad("docx с UTF-16 в app.xml", build("g3.docx", u16))
        brokenrels = [(n, ("<Relationships" if n == "word/_rels/document.xml.rels" else bd)) for n, bd in _fx_docx()]
        bad("docx с битым .rels", build("g4.docx", brokenrels))
        # битая часть на уровне ZIP: портим сжатые байты styles.xml (CRC)
        e = {x.name: x for x in zipfix.entries(src)}["word/styles.xml"]
        mut = bytearray(whole)
        mut[e.header_offset + 30 + len(e.name) + 3] ^= 0xFF
        open(P("crc.docx"), "wb").write(bytes(mut))
        bad("docx с битой записью (CRC)", P("crc.docx"))
        with warnings_off():
            dup = build("dup.docx", _fx_docx() + [("word/document.xml", "<a/>")])
        bad("дубль имён записей", dup)
        trav = build("trav.docx", _fx_docx() + [("../evil.xml", "<a/>")])
        bad("path traversal в имени записи", trav)

        # ------------------------------------------------------------------ фазз: порча валидного пакета
        rnd = random.Random(5)
        pool = [open(src, "rb").read(), open(src_x, "rb").read(), open(src_p, "rb").read()]
        for it in range(150):
            b = bytearray(rnd.choice(pool))
            for _ in range(rnd.randint(1, 5)):
                at = rnd.randrange(len(b))
                m = rnd.random()
                if m < .5:
                    b[at] = rnd.getrandbits(8)
                elif m < .8:
                    del b[at:at + rnd.randint(1, 30)]
                else:
                    b[at:at] = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 20)))
            fz = P("fz.docx")
            open(fz, "wb").write(bytes(b))
            h0 = _sha(fz)
            fo = P("fz_out.docx")
            if os.path.exists(fo):
                os.remove(fo)
            try:
                r_ = clean_ooxml(fz, fo, ST if it % 2 else PA)
            except Exception as exc:  # noqa: BLE001
                chk("фазз %d: не бросает" % it, False, repr(exc))
                continue
            chk("фазз %d: вход цел" % it, _sha(fz) == h0)
            chk("фазз %d: временных файлов нет" % it, not leftovers(), leftovers())
            if r_.ok:
                try:
                    with zipfile.ZipFile(fo) as z:
                        good = z.testzip() is None
                except Exception:  # noqa: BLE001
                    good = False
                chk("фазз %d: ok => выход валиден" % it, good and verify_ooxml(fz, fo)["ok"])
            else:
                chk("фазз %d: отказ с причиной, dst не создан" % it, bool(r_.errors) and not os.path.exists(fo))

        # ------------------------------------------------------------------ фазз со знанием структуры:
        # порча СОДЕРЖИМОГО XML-частей внутри целого, валидного ZIP -- именно так нам присылают «кривые» файлы,
        # и именно тут может упасть движок правки
        rnd = random.Random(23)
        kits = [_fx_docx(ole=True), _fx_xlsx(), _fx_pptx()]
        ext = [".docx", ".xlsx", ".pptx"]
        survived_ok = refused = 0
        for it in range(240):
            k = rnd.randrange(3)
            parts = []
            for n, bd in kits[k]:
                raw_b = bd.encode("utf-8") if isinstance(bd, str) else bd
                if n.endswith((".xml", ".rels")) and rnd.random() < 0.05:
                    b = bytearray(raw_b)
                    for _ in range(rnd.randint(1, 3)):
                        if not b:
                            break
                        at = rnd.randrange(len(b))
                        m = rnd.random()
                        if m < .3:
                            b[at] = rnd.choice(b'<>"\'=/& \r\n')
                        elif m < .6:
                            del b[at:at + rnd.randint(1, 25)]
                        elif m < .8:                       # дубль куска: теги ломаются и множатся
                            seg = bytes(b[at:at + rnd.randint(5, 60)])
                            b[at:at] = seg
                        else:
                            b[at:at] = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(1, 6)))
                    raw_b = bytes(b)
                parts.append((n, raw_b))
            fz = P("sf%s" % ext[k])
            _zip_write(fz, parts)
            h0 = _sha(fz)
            fo = P("sf_out%s" % ext[k])
            if os.path.exists(fo):
                os.remove(fo)
            try:
                r_ = clean_ooxml(fz, fo, ST if it % 2 else PA)
            except Exception as exc:  # noqa: BLE001
                chk("структурный фазз %d: не бросает" % it, False, repr(exc))
                continue
            chk("структурный фазз %d: вход цел, временных файлов нет" % it, _sha(fz) == h0 and not leftovers(), leftovers())
            if r_.ok:
                survived_ok += 1
                vv = verify_ooxml(fz, fo)
                chk("структурный фазз %d: ok => verify_ooxml" % it, vv["ok"], vv["errors"])
            else:
                refused += 1
                chk("структурный фазз %d: отказ с причиной, dst не создан" % it, bool(r_.errors) and not os.path.exists(fo),
                    r_.errors)
        chk("структурный фазз: и чистятся, и отказываются (оба пути живы)", survived_ok > 20 and refused > 20, (survived_ok, refused))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ИТОГО: %d проверок, %d провалено" % tuple(stat))
    return stat[1] == 0


def _expected_survivor(s):
    """Выжившее, которое чистильщик оставляет намеренно, а инспектор принимает за утечку: код Power Query
    (DataMashup, UNREMOVABLE), внутреннее подключение Power Query (на внешний сервер не указывает),
    структурная ссылка Table1[Колонка] (формула, а не путь)."""
    return ("item4.xml" in s.found_at or "xl/connections.xml" in s.found_at or "definedName Таблица" in s.found_at)


class warnings_off:
    """zipfile пишет UserWarning на дубли имён: в тесте они нужны, шум -- нет."""

    def __enter__(self):
        import warnings
        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore")

    def __exit__(self, *a):
        return self._w.__exit__(*a)


def _check_real(path, say):
    """Метрики чистки одного настоящего файла в обоих профилях. Печатаются только числа, имена
    частей и названия полей: значения из файла и персональные данные -- никогда."""
    from .clean import survived_values
    from .inspect import inspect_file

    good = True
    before = inspect_file(path)
    kinds0 = {s.kind for s in before.signals}
    ents0 = zipfix.entries(path)
    say("  исходник: %s, записей %d, критичных находок %d, всех чувствительных %d, ошибок разбора %d"
        % (sniff.pretty(sniff.sniff(path)), len(ents0), before.critical, before.sensitive, len(before.errors)))
    for prof in (Profile.STEALTH, Profile.PARANOID):
        td = tempfile.mkdtemp(prefix="oox_real_")
        try:
            dst = os.path.join(td, "out" + os.path.splitext(path)[1])
            h0 = _sha(path)
            res = clean_ooxml(path, dst, prof)
            h1 = _sha(path)
            say("  --- профиль %s: ok=%s, ошибок %d%s" % (prof.value, res.ok, len(res.errors),
                                                          ("; " + "; ".join(res.errors)[:300]) if res.errors else ""))
            say("      sha256 входа до/после: %s / %s -- %s" % (h0[:16], h1[:16], "СОВПАЛ" if h0 == h1 else "НЕ СОВПАЛ"))
            good &= res.ok and h0 == h1
            if not res.ok:
                continue
            after = inspect_file(dst)
            sv = survived_values(before, after)
            v = verify_ooxml(path, dst)
            kinds1 = {s.kind for s in after.signals}
            new = sorted(kinds1 - kinds0)
            say("      критичных находок: %d -> %d; всех чувствительных: %d -> %d; исходных значений выжило: %d"
                % (before.critical, after.critical, before.sensitive, after.sensitive, len(sv)))
            for s in sv:
                say("        выжило: «%s» в %s -> найдено: %s" % (s.label, s.location, s.found_at))
            say("      открывается: zipfile=%s, testzip=%s, [Content_Types].xml первый=%s, verify_ooxml ok=%s"
                % (v["opens"], v["testzip"], v["content_types_first"], v["ok"]))
            say("      записей %d -> %d, убрано: %s" % (v["entries_src"], v["entries_dst"], ", ".join(v["dropped"]) or "ничего"))
            ents1 = zipfix.entries(dst)
            say("      порядок записей совпал: %s; таймстемпы совпали с исходными: %s (исходные: %s; в результате: %s%s)"
                % (v["order_preserved"], v["times_same"],
                   ", ".join(sorted({"%04d-%02d-%02d" % tuple(e.date_time[:3]) for e in ents0})),
                   ", ".join(sorted({"%04d-%02d-%02d" % tuple(e.date_time[:3]) for e in ents1})),
                   "" if v["times_same"] else "; реальное время ZIP приведено к виду Word сознательно"))
            say("      изменено частей: %d из %d, остальные скопированы сырыми сжатыми байтами"
                % (len(v["edited"]), v["entries_dst"]))
            a0, a1 = _app_name(path), _app_name(dst)
            say("      Application: %s" % ("сохранён как был" if a0 == a1 and a0 else
                                           "убран (профиль убирает всё из провенанса)" if a0 and not a1 else
                                           "в исходнике не заявлен" if not a0 else "ИЗМЕНЁН"))
            say("      сигналы до: %s; после: %s; новые: %s%s"
                % (sorted(kinds0), sorted(kinds1), new or "нет",
                   "".join("\n        новый %s: %s" % (s.kind, clip(s.detail, 200)) for s in after.signals
                           if s.kind in ("ai", "scrubbed") and s.kind in new)))
            say("      действия: %s" % ", ".join("%s %d" % (a.value, res.count(a)) for a in Act if res.count(a)))
            good &= (not sv) and v["ok"] and (prof is Profile.PARANOID or "ai" not in new)
        finally:
            shutil.rmtree(td, ignore_errors=True)
    return good


def _smoke_pathologies(say):
    """Патологические входы: у каждого ok=False, причина в errors, исключения нет, вход цел."""
    td = tempfile.mkdtemp(prefix="oox_path_")
    good = True
    try:
        cases = []
        base = _zip_write(os.path.join(td, "base.docx"), _fx_docx())
        whole = open(base, "rb").read()
        cases.append(("пустой файл (0 байт)", b""))
        cases.append(("не-ZIP с сигнатурой PK", b"PK\x03\x04" + b"junk" * 60))
        cases.append(("обрезанный docx (половина)", whole[:len(whole) // 2]))
        cases.append(("обрезанный docx (без хвоста каталога)", whole[:-30]))
        e = {x.name: x for x in zipfix.entries(base)}["word/styles.xml"]
        mut = bytearray(whole)
        mut[e.header_offset + 30 + len(e.name) + 3] ^= 0xFF
        cases.append(("docx с битой записью (CRC styles.xml)", bytes(mut)))
        for label, name in (("docx с битой docProps/core.xml (не XML)", "docProps/core.xml"),):
            cases.append((label, open(_zip_write(os.path.join(td, "g.docx"), [
                (n, ("это вообще не XML" if n == name else b)) for n, b in _fx_docx()]), "rb").read()))
        cases.append(("docx с DOCTYPE в core.xml", open(_zip_write(os.path.join(td, "g2.docx"), [
            (n, ('<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a/>' if n == "docProps/core.xml" else b))
            for n, b in _fx_docx()]), "rb").read()))
        with warnings_off():
            cases.append(("дубль имён записей", open(_zip_write(os.path.join(td, "d.docx"), _fx_docx() + [
                ("word/document.xml", "<a/>")]), "rb").read()))
        cases.append(("path traversal в имени записи", open(_zip_write(os.path.join(td, "t.docx"), _fx_docx() + [
            ("../evil.xml", "<a/>")]), "rb").read()))
        for label, data in cases:
            src, dst = os.path.join(td, "in.docx"), os.path.join(td, "never.docx")
            open(src, "wb").write(data)
            h0 = _sha(src)
            try:
                res = clean_ooxml(src, dst)
                exc = ""
            except Exception as ex:  # noqa: BLE001
                res, exc = None, type(ex).__name__
            ok = res is not None and (not res.ok) and bool(res.errors) and _sha(src) == h0 and not os.path.exists(dst)
            good &= ok
            say("  %-44s -> %s%s%s" % (label, "ok=False, errors=%d, вход цел, dst нет" % len(res.errors) if ok else "ПРОВАЛ",
                                      (" [исключение %s]" % exc) if exc else "",
                                      ("; причина: " + clip(res.errors[0], 110)) if res is not None and res.errors else ""))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return good


def _app_name(path):
    try:
        with zipfile.ZipFile(path) as z:
            for n in _parse(z.read("docProps/app.xml"), None, True):
                if n.name == "Application":
                    return n.text().strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "--smoke":
        # Метрики по фикстурам проекта (+ патологии): то, что показывается в отчёте о приёмке.
        from tests.fixtures import make_docx, make_docx_wordlike, make_xlsx
        fx = tempfile.mkdtemp(prefix="oox_fx_")
        allgood = True
        try:
            for label, mk in (("make_docx", make_docx), ("make_docx_wordlike", make_docx_wordlike), ("make_xlsx", make_xlsx)):
                print("фикстура %s:" % label)
                allgood &= _check_real(mk(fx)[0], print)
        finally:
            shutil.rmtree(fx, ignore_errors=True)
        print("патологии:")
        allgood &= _smoke_pathologies(print)
        sys.exit(0 if allgood else 1)
    if args and args[0] == "--real":
        allgood = True
        for n, p in enumerate(args[1:], 1):
            print("файл %d:" % n)
            try:
                allgood &= _check_real(p, print)
            except Exception as exc:  # noqa: BLE001
                allgood = False
                print("  ПРОВАЛ проверки: %s" % type(exc).__name__)
        sys.exit(0 if allgood else 1)
    sys.exit(0 if _selftest("-v" in args) else 1)
