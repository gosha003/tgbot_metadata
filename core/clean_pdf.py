# -*- coding: utf-8 -*-
"""Чистка PDF (фаза 1). Инструмент -- pikepdf (QPDF) и lxml, внешних бинарников нет.

    clean_pdf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult

Инспектор -- оракул: после чистки прогоняется inspect_file(dst), и именно он
решает, чисто ли. Наши собственные списки действий доказательством не служат.

Что выяснено ОПЫТОМ на pikepdf 10.15 / libqpdf 12.4 (не по документации):

* Обычное save() НЕ подставляет свой /Producer. Подставляет его
  pdf.open_metadata() с параметрами по умолчанию: пишет /Producer = "pikepdf X",
  pdf:Producer и xmp:MetadataDate=сейчас. Поэтому XMP правится сырым потоком
  через lxml, а open_metadata не вызывается вовсе. После save() всё равно
  сверяем Creator/Producer с тем, что сами оставили: файл, чья чистка стала
  отпечатком, отдавать нельзя.
* Полная перезапись -- это и есть сборка мусора: QPDF пишет только объекты,
  достижимые из трейлера, так что прошлые ревизии (и текст под «замазкой»)
  уходят и БЕЗ linearize. А linearize=True вредит: в первой xref-секции
  появляется /Prev (инспектор считает это «инкрементальным апдейтом»),
  добавляются hint-таблицы и признак «Линеаризован» -- сама чистка стала бы
  отпечатком. Поэтому сохраняем без линеаризации.
* Сырой подсчёт подстроки /Prev (инспектор и наша же сверка) врёт на файлах с
  закладками: звенья /Outlines (/Prev-/Next) -- тоже /Prev. Настоящий критерий
  -- /Prev в ТРЕЙЛЕРЕ, его и проверяем (pdf.trailer).
* QPDF сохраняет /ID[0] исходника и перегенерирует только /ID[1]; ID[0] у
  многих продюсеров выведен из пути файла, значит связывает файлы.
* QPDF пишет в заголовок бинарный маркер %\xbf\xf7\xa2\xfe -- общеизвестная
  подпись qpdf; заменяем равной длины маркером исходного файла.

Только чтение входа: pikepdf.open(src) без allow_overwriting_input, вывод
пишется в dst + ".part" и переименовывается только после проверки.
Приватность: ни имён файлов, ни значений находок в логи не пишем (логов нет).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import zlib

import pikepdf
from lxml import etree

from .cleanmodel import PROFILE_REMOVES, Act, CleanResult, Profile
from .insp_pdf import (RDF, _INFO_MAP, _XMP_IDENTITY, _flatten,
                       _looks_like_path, _qname, _s, _walk_name_tree)
from .model import Risk

# --- лимиты: чистка обязана обработать ВСЁ, иначе отказ (частично чистый файл
# хуже отказа), поэтому лимиты щедрее инспекторских и превышение = ok=False ----
MAX_INPUT = 512 * 1024 * 1024
MAX_PAGES = 20000
MAX_ANNOTS = 200000
MAX_FIELDS = 20000
MAX_XMP = 4 * 1024 * 1024            # распакованный XMP больше -- не разбираем

_ID_WORDS = ("author", "user", "owner", "company", "operator", "signer")  # как в инспекторе
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_FILENAME_TITLE = re.compile(r"\.(docx?|xlsx?|pptx?|rtf|odt|ods|odp|txt|indd|psd|ai)\s*$", re.I)
_WEB = re.compile(r"(?i)^(https?|mailto|tel):")
_ACTIVE = {"/Launch", "/JavaScript", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE"}
_DROP_NS = {"xmpMM", "photoshop", "illustrator", "xmpRights", "xmpBJ"}   # целиком, в любом профиле
_QPDF_MARK = b"\xbf\xf7\xa2\xfe"
_ADOBE_MARK = b"\xe2\xe3\xcf\xd3"
_XPACKET = b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n%s\n<?xpacket end="w"?>'
_EMPTY_XMP = _XPACKET % (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    b'<rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>')

_NOTE_APP = ("STEALTH: оставлено как было. Файл действительно сделан этим приложением, а пара "
             "вида Creator=Word + Producer=qpdf сразу выдаёт, что файл обрабатывали.")
_NOTE_DATE = ("STEALTH: даты не трогаем. CreationDate == ModDate (обнуление в одну секунду) -- "
              "признак чистки, а подмена даты -- фальсификация провенанса.")

# Структурное: не убирается ни в одном профиле.
_STRUCT = (
    ("/StructTreeRoot", "Теги доступности (тегированный PDF): без них скринридер не читает документ."),
    ("/MarkInfo", "Пометка о тегировании, идёт вместе с /StructTreeRoot."),
    ("/Lang", "Язык документа: нужен скринридерам и переносам."),
    ("/Outlines", "Оглавление (закладки): навигация по документу."),
    ("/PageLabels", "Пользовательская нумерация страниц."),
    ("/ViewerPreferences", "Как открывать документ; безобидно."),
    ("/OutputIntents", "Цветовой профиль печати (ICC): без него ломается цветопередача."),
)


class _Refuse(Exception):
    """Чистка невозможна или небезопасна: ok=False, текст уходит в errors."""


class _C:
    """Контекст чистки: результат, профиль, whitelist пользователя."""

    def __init__(self, res, profile, keep):
        self.res = res
        self.profile = profile
        self.paranoid = profile is Profile.PARANOID
        self.removes = PROFILE_REMOVES[profile]
        if isinstance(keep, str):
            keep = [keep]
        self.keep = {str(k).strip().casefold() for k in (keep or ())}
        self.ua = False          # в XMP заявлен PDF/UA: заголовок обязателен

    def held(self, location, label, before=""):
        """Пользователь велел оставить это поле: пишем KEPT и просим не трогать."""
        if self.keep and (label.casefold() in self.keep or location.casefold() in self.keep):
            self.res.act(Act.KEPT, location, label, before, note="оставлено по whitelist пользователя (keep)")
            return True
        return False


def _pop(c, holder, key, where, before=None, note="", label=None):
    """Убрать holder[key], если пользователь не велел оставить. True -- убрали."""
    if key not in holder:
        return False
    label = label or key
    if before is None:
        before = _s(holder.get(key))
    if c.held(where, label, before):
        return False
    del holder[key]
    c.res.act(Act.REMOVED, where, label, before, note=note)
    return True


def _del(d, key):
    if key in d:
        del d[key]


def _step(c, name, fn, *args):
    """Одна секция чистки: сбой пишется в errors и FAILED, остальные идут дальше."""
    try:
        fn(*args)
    except _Refuse:
        raise
    except Exception as exc:  # noqa: BLE001 -- битые файлы нам присылают специально
        c.res.act(Act.FAILED, name, name, note="сбой секции (%s), подробности в errors" % type(exc).__name__)
        c.res.err("Секция «%s»: %s: %s" % (name, type(exc).__name__, exc))


# ============================================================================
#  Обход структур (с защитой от циклов и DAG-бомб)
# ============================================================================


def _iter_fields(form):
    """Поля AcroForm: (полное имя, поле, унаследованный /FT). Итеративно, без рекурсии."""
    fields = form.get("/Fields")
    if not isinstance(fields, pikepdf.Array):
        return
    stack = [(f, "", "") for f in reversed(list(fields))]
    seen, n = set(), 0
    while stack and n < MAX_FIELDS:
        f, prefix, ft = stack.pop()
        if not isinstance(f, pikepdf.Dictionary):
            continue
        gen = f.objgen
        if gen != (0, 0):
            if gen in seen:
                continue
            seen.add(gen)
        n += 1
        name = _s(f.get("/T"))
        full = "%s.%s" % (prefix, name) if prefix and name else (name or prefix)
        ft = _s(f.get("/FT")) or ft
        yield full, f, ft
        kids = f.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            stack.extend((k, full, ft) for k in reversed(list(kids)))


def _filled_signatures(pdf):
    """Заполненные подписи: [(поле, подписант, поле-объект)] и признак /Perms /DocMDP.
    Пустая заготовка поля (/FT /Sig без /V) подписью НЕ считается."""
    out = []
    form = pdf.Root.get("/AcroForm")
    if isinstance(form, pikepdf.Dictionary):
        for name, f, ft in _iter_fields(form):
            v = f.get("/V")
            if ft == "/Sig" and v is not None:
                signer = _s(v.get("/Name")) if isinstance(v, pikepdf.Dictionary) else ""
                out.append((name, signer, f))
    perms = pdf.Root.get("/Perms")
    docmdp = isinstance(perms, pikepdf.Dictionary) and perms.get("/DocMDP") is not None
    return out, docmdp


def _file_label(spec, fallback=""):
    """Имя и размер вложения из filespec."""
    if not isinstance(spec, pikepdf.Dictionary):
        return fallback or "?"
    name = _s(spec.get("/UF")) or _s(spec.get("/F")) or fallback or "?"
    try:
        ef = spec.get("/EF")
        st = ef.get("/F")
        if st is None:
            st = ef.get("/UF")
        size = _s(st.get("/Params").get("/Size")) or str(len(st.read_raw_bytes()))
        return "%s (%s байт)" % (name, size)
    except Exception:
        return name


# ============================================================================
#  А. /Info
# ============================================================================


def _info_risk(key, raw):
    """Категория поля /Info -- по тем же правилам, что у инспектора (оракул)."""
    _, risk = _INFO_MAP.get(key, (None, None))
    if risk is None:
        low = key.lower()
        risk = Risk.IDENTITY if any(w in low for w in _ID_WORDS) else Risk.PROVENANCE
    if risk is not Risk.IDENTITY and (_looks_like_path(raw) or (key == "/Title" and _FILENAME_TITLE.search(raw))):
        return Risk.ENVIRONMENT      # путь или имя файла вместо названия
    if risk is Risk.PROVENANCE and _EMAIL.search(raw):
        return Risk.IDENTITY
    return risk


def _sec_info(pdf, c):
    info = pdf.trailer.get("/Info")      # НЕ pdf.docinfo: тот создаёт пустой /Info
    if not isinstance(info, pikepdf.Dictionary):
        return
    for key in list(info.keys()):
        raw = _s(info.get(key))
        risk = _info_risk(key, raw)
        ua_title = key == "/Title" and c.ua
        if (risk in c.removes or key.startswith("/PTEX")) and not ua_title:
            if key.startswith("/PTEX"):
                note = "Строка версии TeX Live с датой сборки выдаёт ОС и дистрибутив автора."
            elif c.paranoid:
                note = ("PARANOID убирает всё: файл читается как обработанный (Word-PDF без "
                        "Creator/Producer) -- этого профиль не скрывает.")
            else:
                note = "Личность или окружение: убирается в любом профиле."
            _pop(c, info, key, "/Info", raw, note)
        else:
            if key in ("/Creator", "/Producer"):
                note = _NOTE_APP
            elif key in ("/CreationDate", "/ModDate"):
                note = _NOTE_DATE
            elif ua_title:
                note = "Заголовок нужен PDF/UA (в XMP заявлен pdfuaid) -- структурное, сохраняем."
            else:
                note = "Провенанс: STEALTH оставляет, PARANOID убирает."
            if not c.held("/Info", key, raw):
                c.res.act(Act.KEPT, "/Info", key, raw, note=note)
    if len(info) == 0:
        del pdf.trailer["/Info"]


# ============================================================================
#  Б. XMP: сырой поток через lxml
# ============================================================================


def _read_xmp(stream):
    """Распакованные байты XMP или None, если читать небезопасно. Flate распаковываем
    с потолком (бомба: килобайты разворачиваются в гигабайты); экзотику не трогаем."""
    raw = stream.read_raw_bytes()
    flt = stream.get("/Filter")
    if stream.get("/DecodeParms") is not None:
        return None
    if flt is None:
        data = raw
    elif (isinstance(flt, pikepdf.Array) and len(flt) == 1 and _s(flt[0]) == "/FlateDecode") \
            or (not isinstance(flt, pikepdf.Array) and _s(flt) == "/FlateDecode"):
        d = zlib.decompressobj()
        data = d.decompress(raw, MAX_XMP)
        if d.unconsumed_tail:
            return None
    else:
        return None
    return data if len(data) <= MAX_XMP else None


def _xmp_drop(key, value, c):
    prefix = key.split(":", 1)[0]
    if prefix in ("pdfaid", "pdfuaid"):
        return False                                   # декларация PDF/A, PDF/UA -- структурное
    if c.paranoid:
        return not (c.ua and key == "dc:title")
    if prefix in _DROP_NS or key in _XMP_IDENTITY:
        return True
    if _looks_like_path(value) or _EMAIL.search(value):
        return True
    return key == "dc:title" and bool(_FILENAME_TITLE.search(value))


def _xmp_clean(raw, c, where):
    """-> (новые байты | None, если менять нечего; есть ли что-то, кроме пустой оболочки)."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True, huge_tree=False)
    root = etree.fromstring(raw, parser=parser)
    if root is None:
        return _EMPTY_XMP, False
    c.ua = c.ua or b"/pdfua/ns/id/" in raw
    changed, kept, payload = False, [], False
    for desc in list(root.iter(RDF + "Description")):
        about = desc.get(RDF + "about")
        if about:                                      # uuid: в rdf:about дублирует DocumentID
            desc.set(RDF + "about", "")
            c.res.act(Act.NORMALIZED, where, "rdf:about", about, "", "Идентификатор в rdf:about дублирует xmpMM:DocumentID.")
            changed = True
        for attr in list(desc.attrib):                 # компактная форма: свойства атрибутами
            key = _qname(attr)
            if key.startswith("rdf:"):
                continue
            value = desc.attrib[attr]
            if _xmp_drop(key, value, c) and not c.held(where, key, value):
                del desc.attrib[attr]
                c.res.act(Act.REMOVED, where, key, value, note=_xmp_note(key, c))
                changed = True
            else:
                kept.append(key)
        for child in list(desc):                       # развёрнутая форма: дочерние элементы
            if not isinstance(child.tag, str):
                continue
            key = _qname(child.tag)
            value = _flatten(child)
            if _xmp_drop(key, value, c) and not c.held(where, key, value):
                desc.remove(child)
                c.res.act(Act.REMOVED, where, key, value, note=_xmp_note(key, c))
                changed = True
            else:
                kept.append(key)
        payload = payload or bool(kept)
    if kept:
        c.res.act(Act.KEPT, where, "прочие поля XMP", ", ".join(sorted(set(kept))),
                  note="Структурные декларации (PDF/A, PDF/UA)." if c.paranoid else
                       "Приложение, даты, заголовок и структурные декларации: согласованы с /Info.")
    if not changed:
        return None, payload
    etree.cleanup_namespaces(root, keep_ns_prefixes=["rdf", "x"])
    return _XPACKET % etree.tostring(root, encoding="utf-8"), payload


def _xmp_note(key, c):
    if c.paranoid and not (key.startswith("xmpMM:") or key in _XMP_IDENTITY or key.startswith("photoshop:")):
        return "PARANOID убирает всё, кроме структурных деклараций."
    if key.startswith("xmpMM:History"):
        return "Журнал правок: softwareAgent и when каждой правки."
    if key.startswith("xmpMM:"):
        return "Постоянный GUID связывает все версии и все файлы одного шаблона."
    return "Идентичность или окружение в XMP: переживает чистку /Info."


def _xmp_holder(holder, where, c):
    """Один поток /Metadata у holder (корень каталога, страница, XObject)."""
    meta = holder.get("/Metadata")
    if meta is None:
        return
    if not isinstance(meta, pikepdf.Stream):
        _pop(c, holder, "/Metadata", where, "не поток", "Битая ссылка на XMP.")
        return
    if c.held(where, "/Metadata"):
        return
    raw = _read_xmp(meta)
    if raw is None:                  # слишком большой или экзотический фильтр: не читаем
        if c.paranoid:
            _pop(c, holder, "/Metadata", where, "нечитаемый XMP", "Поток слишком велик или в нестандартном кодировании.")
        else:
            meta.write(_EMPTY_XMP)
            c.res.act(Act.NORMALIZED, where, "/Metadata", "нечитаемый XMP", "пустая оболочка",
                      "Поток не разбирается безопасно: заменён пустым пакетом, чтобы XMP остался на месте.")
        return
    new, payload = _xmp_clean(raw, c, where)
    if c.paranoid and not payload:   # от XMP ничего не осталось
        del holder["/Metadata"]
        c.res.act(Act.REMOVED, where, "/Metadata", "XMP-пакет", note="PARANOID: пакет пуст, поток убран целиком.")
        return
    if new is not None:
        meta.write(new)


def _sec_xmp(pdf, c):
    _xmp_holder(pdf.Root, "XMP", c)      # корень первым: он выставляет c.ua для /Info


# ============================================================================
#  Сквозной обход объектов: /PieceInfo, /AF, /Thumb, баннер TeX, XMP не в корне
# ============================================================================

_SWEEP = (
    ("/PieceInfo", "Приватные данные приложения (Acrobat/Distiller/Illustrator), иногда с редактируемым исходником."),
    ("/AF", "Связанные файлы (PDF/A-3, счета): внутри может лежать исходный xlsx/XML целиком."),
    ("/Thumb", "Миниатюра страницы устаревает при правке и показывает прошлую версию страницы."),
    ("/PTEX.Fullbanner", "Строка версии TeX Live с датой сборки выдаёт ОС и дистрибутив автора."),
)


def _sweep_objects(pdf, c):
    root_gen = pdf.Root.objgen
    for obj in pdf.objects:
        try:
            if not isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                continue
            gen = obj.objgen
            where = "/Root" if gen == root_gen else "объект %d" % gen[0]
            for key, note in _SWEEP:
                if key not in obj:
                    continue
                if key == "/PieceInfo":
                    before = ", ".join(str(k).lstrip("/") for k in obj.get(key).keys())
                elif key == "/AF":
                    before = "; ".join(_file_label(s) for s in list(obj.get(key))[:20])
                else:
                    before = "есть" if key == "/Thumb" else _s(obj.get(key))
                _pop(c, obj, key, where, before, note)
            if gen != root_gen and "/Metadata" in obj:
                _xmp_holder(obj, where + " XMP", c)
        except Exception as exc:  # noqa: BLE001
            c.res.err("Обход объектов: %s: %s" % (type(exc).__name__, exc))


# ============================================================================
#  Е, Ж. Активное содержимое и вложения (документ)
# ============================================================================


def _sec_doc(pdf, c):
    root = pdf.Root
    names = root.get("/Names")
    if isinstance(names, pikepdf.Dictionary):
        for key, what, note in (
            ("/JavaScript", "Скрипт уровня документа",
             "JavaScript выполняется при открытии файла и может «звонить домой»."),
            ("/EmbeddedFiles", "Вложенный файл",
             "ВЛОЖЕНИЯ УДАЛЕНЫ: внутри мог лежать исходный документ целиком."),
        ):
            tree = names.get(key)
            if tree is None:
                continue
            items = []
            _walk_name_tree(tree, items, c.res, 0)
            if c.held("/Names", key, "%d шт." % len(items)):
                continue
            del names[key]
            if not items or len(items) >= 100:      # 100 -- потолок обхода дерева имён
                c.res.act(Act.REMOVED, "/Names", key, "пусто" if not items else "перечислены первые 100, дерево удалено целиком",
                          note=note)
            for label, spec in items:
                before = _file_label(spec, label) if key == "/EmbeddedFiles" else label
                c.res.act(Act.REMOVED, "/Names " + key, what, before, note=note)
    _pop(c, root, "/OpenAction", "/Root", note="Что файл делает сам при открытии.")
    _pop(c, root, "/AA", "/Root", note="Обработчики событий документа: печать, сохранение, закрытие.")
    _pop(c, root, "/Collection", "/Root", "портфолио", "Схема портфолио без вложений теряет смысл.")
    if c.paranoid:
        _pop(c, root, "/DSS", "/Root", note="Данные долгосрочной валидации подписи: цепочки сертификатов.")


def _action_bad(act, c, depth=0):
    """Действие (или любое звено цепочки /Next), которое надо убрать."""
    if not isinstance(act, pikepdf.Dictionary):
        return False
    if depth > 6:
        return True
    s = _s(act.get("/S"))
    if s in _ACTIVE:
        return True
    if s == "/URI":
        uri = _s(act.get("/URI")).strip()
        if c.paranoid or _looks_like_path(uri) or not _WEB.match(uri):
            return True
    nxt = act.get("/Next")
    if isinstance(nxt, pikepdf.Array):
        return any(_action_bad(n, c, depth + 1) for n in nxt)
    return _action_bad(nxt, c, depth + 1)


def _scrub_actions(a, where, c):
    act = a.get("/A")
    if act is not None and _action_bad(act, c):
        s = _s(act.get("/S"))
        what = _s(act.get("/URI")) or _s(act.get("/F")) or ""
        _pop(c, a, "/A", where, ("%s %s" % (s, what)).strip(),
             "Активное или внешнее действие: запуск программы, скрипт, отправка данных или "
             "ссылка на локальный путь.")
    _pop(c, a, "/AA", where, note="Скрипты-обработчики событий аннотации или поля.")


# ============================================================================
#  Д, Ж. Страницы и аннотации
# ============================================================================


def _paranoid_keeps(a, sub, c):
    """Что PARANOID оставляет из аннотаций: поля формы и внутреннюю навигацию."""
    if sub == "/Widget":
        return True
    if sub == "/Link":
        act = a.get("/A")
        return a.get("/Dest") is not None or (isinstance(act, pikepdf.Dictionary) and _s(act.get("/S")) == "/GoTo"
                                              and not _action_bad(act, c))
    return False


def _annots(p, where_pg, c, st):
    annots = p.get("/Annots")
    if not isinstance(annots, pikepdf.Array):
        return
    items = list(annots)
    st["annots"] += len(items)
    if st["annots"] > MAX_ANNOTS:
        raise _Refuse("Аннотаций больше %d: чистка не выполнена, чтобы не оставить часть необработанной." % MAX_ANNOTS)
    where = where_pg + " /Annots"
    dicts = [a for a in items if isinstance(a, pikepdf.Dictionary)]
    dropped = set()          # id() удалённых аннотаций (у прямых словарей objgen не различает)
    gone = set()             # objgen удалённых непрямых: по ним ищем popup-и

    def drop(a, label, before, note):
        if c.held(where, label, before):
            return
        dropped.add(id(a))
        if a.objgen != (0, 0):
            gone.add(a.objgen)
        c.res.act(Act.REMOVED, where, label, before, note=note)

    for a in dicts:
        sub = _s(a.get("/Subtype"))
        if sub == "/FileAttachment":
            drop(a, "/FileAttachment", _file_label(a.get("/FS")),
                 "ВЛОЖЕНИЕ УДАЛЕНО: внутри мог лежать исходный документ целиком.")
        elif c.paranoid and not _paranoid_keeps(a, sub, c):
            drop(a, "Аннотация " + sub, _s(a.get("/Contents")),
                 "PARANOID: аннотации удаляются целиком (авторство, текст, даты, popup). "
                 "В обычном профиле остаются, убирается только авторство.")
    for a in dicts:          # popup удалённого родителя уходит с ним: иначе через /Parent
        par = a.get("/Parent")   # вложение остаётся достижимым и переживает чистку
        if _s(a.get("/Subtype")) == "/Popup" and isinstance(par, pikepdf.Dictionary) and par.objgen in gone:
            dropped.add(id(a))
            gone.add(a.objgen)
    kept = [a for a in items if id(a) not in dropped]
    for a in kept:
        if not isinstance(a, pikepdf.Dictionary):
            continue
        sub = _s(a.get("/Subtype"))
        keys = [("/T", "Имя автора аннотации (имя учётки в Acrobat/Reader)."),
                ("/M", "Время правки комментария."), ("/CreationDate", "Время создания комментария."),
                ("/RC", "Форматированный дубль текста комментария.")]
        if sub == "/Popup" or c.paranoid:
            keys.append(("/Contents", "Дубль текста комментария."))
        if c.paranoid:
            keys.append(("/Subj", "Тема комментария."))
        for key, note in keys:
            _pop(c, a, key, where, note=note)
        pop = a.get("/Popup")
        if isinstance(pop, pikepdf.Dictionary) and pop.objgen in gone:
            del a["/Popup"]
        _scrub_actions(a, where, c)
        if sub != "/Popup" and a.get("/Contents") is not None:
            st["comments"] += 1
    if len(kept) != len(items):
        if kept:
            p["/Annots"] = pikepdf.Array(kept)
        else:
            del p["/Annots"]


def _sec_pages(pdf, c):
    if len(pdf.pages) > MAX_PAGES:
        raise _Refuse("Страниц больше %d: чистка не выполнена, чтобы не оставить часть необработанной." % MAX_PAGES)
    st = {"annots": 0, "comments": 0}
    for idx, page in enumerate(pdf.pages):
        try:
            where = "стр. %d" % (idx + 1)
            _pop(c, page.obj, "/AA", where, note="Скрипты, срабатывающие при открытии/закрытии страницы.")
            _annots(page.obj, where, c, st)
        except _Refuse:
            raise
        except Exception as exc:  # noqa: BLE001
            c.res.err("Стр. %d: %s: %s" % (idx + 1, type(exc).__name__, exc))
    if st["comments"]:
        c.res.act(Act.KEPT, "/Annots", "/Contents", "%d шт." % st["comments"],
                  note="Текст комментариев -- содержимое документа, пользователь мог оставить его осознанно; "
                       "авторство и даты убраны. Чтобы убрать и текст -- профиль PARANOID.")


# ============================================================================
#  Форма, подпись, слои, структурное
# ============================================================================


def _sec_form(pdf, c):
    form = pdf.Root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary):
        return
    filled = 0
    cleared = False
    for name, f, ft in _iter_fields(form):
        if ft == "/Sig" or f.get("/V") is None:
            continue
        if not c.paranoid:
            filled += 1
            continue
        if _pop(c, f, "/V", "/AcroForm /Fields", _s(f.get("/V")),
                "PARANOID: значение поля формы. Внешний вид поля перерисует просмотрщик.", "Поле «%s»" % name):
            _del(f, "/DV")
            if f.get("/AS") is not None:
                f["/AS"] = pikepdf.Name("/Off")
            _del(f, "/AP")
            cleared = True
    if filled:
        c.res.act(Act.KEPT, "/AcroForm /Fields", "значения полей", "%d шт." % filled,
                  note="Значения -- содержимое формы, пользователь заполнил их осознанно. PARANOID очищает.")
    if cleared:
        form["/NeedAppearances"] = True
    if c.paranoid:
        _pop(c, form, "/XFA", "/AcroForm", "XML-копия формы",
             "XFA дублирует введённые данные параллельно обычным полям.")


def _sec_signature(pdf, c, sigs, docmdp):
    """Чистка подписанного файла (только force_signed): подпись уничтожается."""
    for name, signer, field in sigs:
        _del(field, "/V")
        c.res.act(Act.REMOVED, "/AcroForm /Sig", "Цифровая подпись «%s»" % name, signer,
                  note="ПОДПИСЬ УНИЧТОЖЕНА: любая чистка метаданных ломает подпись, force_signed=True "
                       "это разрешил. Поле осталось пустой заготовкой.")
    form = pdf.Root.get("/AcroForm")
    if isinstance(form, pikepdf.Dictionary):
        _pop(c, form, "/SigFlags", "/AcroForm", note="Флаг наличия подписей: подписей больше нет.")
    if docmdp:
        _pop(c, pdf.Root, "/Perms", "/Root", "сертифицирующая подпись", "Сертифицирующая подпись снята вместе с остальными.")
    _pop(c, pdf.Root, "/DSS", "/Root", note="Данные валидации подписи: цепочки сертификатов подписантов.")


def _sec_perms(pdf, c):
    """/Perms без /DocMDP -- это Reader Extensions (UR3): после любой правки он невалиден."""
    perms = pdf.Root.get("/Perms")
    if isinstance(perms, pikepdf.Dictionary) and perms.get("/DocMDP") is None:
        _pop(c, pdf.Root, "/Perms", "/Root", "права Reader Extensions",
             "Подпись прав использования (UR3) недействительна после любой правки; Reader лишь отключит расширенные функции.")


def _sec_structural(pdf, c):
    root = pdf.Root
    for key, note in _STRUCT:
        if key in root:
            before = _s(root.get(key)) if key == "/Lang" else "есть"
            if not c.held("/Root", key, before):
                c.res.act(Act.KEPT, "/Root", key, before, note=note)
    ocp = root.get("/OCProperties")
    if isinstance(ocp, pikepdf.Dictionary):
        d = ocp.get("/D")
        off = d.get("/OFF") if isinstance(d, pikepdf.Dictionary) else None
        off = list(off) if isinstance(off, pikepdf.Array) else []
        if off:
            c.res.act(Act.UNREMOVABLE, "/OCProperties /D /OFF", "выключенные слои", "%d шт." % len(off),
                      note="Содержимое выключенных слоёв ЛЕЖИТ в файле и включается одним кликом. Убрать его "
                           "можно только переписыванием потоков содержимого страниц -- чистка этого не делает.")
        else:
            c.res.act(Act.KEPT, "/OCProperties", "слои", "все видимы", note="Слои видимы, содержимое не скрыто.")


# ============================================================================
#  Г, В. Ревизии, /ID, заголовок, проверка
# ============================================================================


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_eof(path):
    """Число %%EOF в сырых байтах (чанками, с нахлёстом на стыках)."""
    pos, off, prev = set(), 0, b""
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(4 << 20), b""):
            buf = prev + chunk
            for m in re.finditer(rb"%%EOF", buf):
                pos.add(off - len(prev) + m.start())
            off += len(chunk)
            prev = buf[-8:]
    return len(pos)


def _prepare_id(pdf, c):
    """Вернуть описание /ID до чистки. QPDF сохраняет ID[0] и меняет только ID[1], поэтому
    ID[0] меняем сами. Форму пары сохраняем: у Word, Chrome, LibreOffice ID[0]==ID[1], и
    разошедшаяся пара -- след пересохранения. Равна -- удаляем /ID, QPDF пишет обе равными;
    различна -- ставим случайный ID[0], ID[1] QPDF выведет из содержимого результата
    (deterministic_id: не время и не имя файла). static_id нельзя: константа на все файлы."""
    try:
        ids = pdf.trailer.get("/ID")
        if not (isinstance(ids, pikepdf.Array) and len(ids) == 2):
            return None
        a, b = bytes(ids[0]), bytes(ids[1])
        if a == b:
            del pdf.trailer["/ID"]
        else:
            pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(os.urandom(16)), ids[1]])
        return "%s / %s" % (a.hex()[:8], b.hex()[:8]), a == b
    except Exception as exc:  # noqa: BLE001
        c.res.err("/ID не перегенерирован: %s: %s" % (type(exc).__name__, exc))
        return None


def _save(pdf, path):
    # Без linearize (см. docstring модуля). deterministic_id -- ID[1] из содержимого.
    pdf.save(path, deterministic_id=True)


_ID_RE = re.compile(rb"/ID\s*\[\s*<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\s*\]")


def _drop_id(path):
    """PARANOID: убрать /ID из трейлера результата (QPDF пишет его всегда). Заменяем равным по
    длине пробельным заполнением: смещения xref не плывут, словарь остаётся валидным.
    ponytail: ищем от последнего startxref, не парсим трейлер -- хватает для вывода QPDF."""
    with open(path, "r+b") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 64))
        m = re.search(rb"startxref\s+(\d+)", fh.read())
        if not m:
            return False
        off = int(m.group(1))
        fh.seek(off)
        m = _ID_RE.search(fh.read())
        if not m:
            return False
        fh.seek(off + m.start())
        fh.write(b" " * (m.end() - m.start()))
    return True


def _fix_header(path, src):
    """Заменить маркер qpdf в заголовке на равный по длине маркер исходника. -> (было, стало)."""
    with open(src, "rb") as fh:
        m = re.match(rb"%PDF-\d\.\d[\r\n]+%([\x80-\xff]{4})[\r\n]", fh.read(64))
    mark = m.group(1) if m else _ADOBE_MARK
    if mark == _QPDF_MARK:               # исходник сам писан qpdf: маркер его, менять нечего
        return None
    with open(path, "r+b") as fh:
        head = fh.read(32)
        i = head.find(b"\n%" + _QPDF_MARK + b"\n")
        if i < 0:
            return None
        fh.seek(i + 2)
        fh.write(mark)
    return _QPDF_MARK.hex(" "), mark.hex(" ")


def _verify(res, tmp, snap, npages, no_id):
    """Выход открывается своим парсером и не стал отпечатком. -> список проблем."""
    bad = []
    with pikepdf.open(tmp) as q:
        if len(q.pages) != npages:
            bad.append("число страниц изменилось: %d -> %d" % (npages, len(q.pages)))
        if no_id and "/ID" in q.trailer:
            bad.append("/ID остался в трейлере")
        if "/Prev" in q.trailer:
            bad.append("в трейлере остался /Prev: прошлые ревизии не сброшены")
        info = q.trailer.get("/Info")
        for key, was in snap.items():
            now = _s(info.get(key)) if isinstance(info, pikepdf.Dictionary) and key in info else None
            if now != was:
                bad.append("%s изменился при сохранении (чистка сама стала бы отпечатком)" % key)
        left = sum(1 for o in q.objects if isinstance(o, pikepdf.Stream) and _s(o.get("/Type")) == "/EmbeddedFile")
        if left:
            bad.append("вложений осталось в файле: %d" % left)
        if q.is_linearized:
            bad.append("результат линеаризован")
    eof = _count_eof(tmp)
    if eof != 1:
        res.err("В результате %d маркеров %%EOF (ожидался 1): возможно, строка внутри потока." % eof)
    return bad


# ============================================================================
#  Точка входа
# ============================================================================


def _clean(res, src, dst, profile, keep, force_signed, tmp):
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        raise _Refuse("Неизвестный профиль чистки.")
    res.profile = prof.value
    if prof is Profile.REGENERATE:
        raise _Refuse("Профиль regenerate для PDF не поддерживается (он для офисных форматов).")
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
        raise _Refuse("Путь результата совпадает с путём входного файла: вход не перезаписываем.")
    size = os.path.getsize(src)
    if size == 0:
        raise _Refuse("Файл пуст: возможно, не докачался.")
    if size > MAX_INPUT:
        raise _Refuse("Файл больше %d МБ: чистка не выполнена." % (MAX_INPUT >> 20))
    sha_before = _sha256(src)
    revs = _count_eof(src)
    c = _C(res, prof, keep)

    try:
        pdf = pikepdf.open(src)                       # строго на чтение
    except pikepdf.PasswordError:
        raise _Refuse("PDF зашифрован и не открывается пустым паролем: чистка невозможна без пароля.")
    except pikepdf.PdfError as exc:
        raise _Refuse("PDF не открывается (%s): файл повреждён или это не PDF." % type(exc).__name__)
    with pdf:
        if pdf.is_encrypted:
            raise _Refuse("PDF зашифрован: чистка зашифрованных файлов не выполняется, чтобы не снять "
                          "защиту молча.")
        if any(re.search(r"damaged|reconstruct|recover", w, re.I) for w in pdf.get_warnings()):
            raise _Refuse("Файл повреждён: QPDF пришлось восстанавливать структуру. Чистка не выполнена, "
                          "чтобы не отдать вам файл с потерянными частями.")
        npages = len(pdf.pages)
        sigs, docmdp = _filled_signatures(pdf)
        if (sigs or docmdp) and not force_signed:
            raise _Refuse("Документ подписан цифровой подписью (%d): любая чистка метаданных её уничтожит. "
                          "Подпись и чистка взаимоисключающи; чтобы всё же очистить и потерять подпись, "
                          "передайте force_signed=True." % max(len(sigs), 1))
        was_lin = pdf.is_linearized
        if sigs or docmdp:
            _step(c, "подпись", _sec_signature, pdf, c, sigs, docmdp)
        for name, fn, args in (
            ("XMP", _sec_xmp, (pdf, c)),
            ("/Info", _sec_info, (pdf, c)),
            ("активное содержимое и вложения", _sec_doc, (pdf, c)),
            ("страницы и аннотации", _sec_pages, (pdf, c)),
            ("форма", _sec_form, (pdf, c)),
            ("/Perms", _sec_perms, (pdf, c)),
            ("сквозной обход объектов", _sweep_objects, (pdf, c)),
            ("слои и структурное", _sec_structural, (pdf, c)),
        ):
            _step(c, name, fn, *args)
        try:
            pdf.remove_unreferenced_resources()
        except Exception as exc:  # noqa: BLE001
            res.err("remove_unreferenced_resources: %s: %s" % (type(exc).__name__, exc))
        before_id = _prepare_id(pdf, c)
        info = pdf.trailer.get("/Info")
        snap = {k: (_s(info.get(k)) if isinstance(info, pikepdf.Dictionary) and k in info else None)
                for k in ("/Creator", "/Producer")}
        _save(pdf, tmp)

    marks = _fix_header(tmp, src)
    id_dropped = c.paranoid and _drop_id(tmp)
    bad = _verify(res, tmp, snap, npages, c.paranoid)
    for b in bad:
        res.err(b)
    if bad:
        return
    if _sha256(src) != sha_before:
        res.err("Входной файл изменился во время чистки (sha256 до и после не совпал).")
        return

    if revs > (2 if was_lin else 1):
        res.act(Act.REMOVED, "сырые байты", "Инкрементальные ревизии", "%d ревизий" % revs, "1",
                "Прошлые версии страниц и текст под «замазкой» физически выброшены полной перезаписью "
                "(сборка мусора QPDF); /Prev в трейлере нет.")
    if was_lin:
        res.act(Act.NORMALIZED, "структура", "Линеаризация", "да", "нет",
                "Сохранено без линеаризации: в первой xref-секции линеаризованного файла /Prev, "
                "а сама линеаризация -- признак Acrobat.")
    if id_dropped:
        res.act(Act.REMOVED, "trailer", "/ID", before_id[0] if before_id else "", "",
                "PARANOID: /ID убран совсем (он необязателен). У многих продюсеров ID[0] выведен из пути и "
                "имени файла и связывает файлы; файл без /ID выглядит обработанным.")
    elif before_id:
        with pikepdf.open(tmp) as q:
            after = "/".join(bytes(x).hex()[:8] for x in q.trailer.get("/ID", []))
        res.act(Act.REGENERATED, "trailer", "/ID", before_id[0], after,
                "ID[0] у многих продюсеров выведен из пути и имени файла и связывает файлы. Оба значения "
                "новые; пара %s, как у исходника (разошедшаяся пара выдаёт пересохранение). ID[1] выведен "
                "из содержимого результата (deterministic_id), не из времени и не из имени файла."
                % ("совпадает" if before_id[1] else "различна"))
    if marks:
        res.act(Act.NORMALIZED, "заголовок", "бинарный маркер qpdf", marks[0], marks[1],
                "Маркер %¿÷¢þ -- общеизвестная подпись qpdf; заменён равным по длине маркером исходника.")
    os.replace(tmp, dst)
    res.ok = True


def clean_pdf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult:
    """Очистить PDF src -> dst. Вход не меняется. Не бросает исключений: при отказе
    ok=False, причина в errors, файл dst не создаётся.

    keep -- whitelist пользователя: множество строк, сравниваются (без регистра) с label
    и location действия: "/Author", "dc:creator", "/Info", "стр. 3 /Annots".
    force_signed -- чистить подписанный файл, уничтожив подпись."""
    res = CleanResult(src=str(src), dst=str(dst), fmt="pdf",
                      profile=getattr(profile, "value", str(profile)))
    tmp = str(dst) + ".part"
    try:
        d = os.path.dirname(os.path.abspath(str(dst)))
        os.makedirs(d, exist_ok=True)
        _clean(res, str(src), str(dst), profile, keep, force_signed, tmp)
    except _Refuse as exc:
        res.err(exc)
    except Exception as exc:  # noqa: BLE001 -- до пользователя не должно долететь ничего
        res.err("Внутренняя ошибка чистки PDF: %s: %s" % (type(exc).__name__, exc))
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return res


# ============================================================================
#  Самопроверка: PYTHONIOENCODING=utf-8 python -m core.clean_pdf
#  Свои файлы с метриками (без вывода содержимого): ... -m core.clean_pdf --real файл.pdf ...
# ============================================================================

_FAILS = []
_TOTAL = [0]
_WORD_MARK = b"\xb5\xb5\xb5\xb5"      # маркер заголовка Word (µµµµ)


def _check(name, cond, detail=""):
    _TOTAL[0] += 1
    if not cond:
        _FAILS.append(name)
    print("  [%s] %s%s" % ("ok" if cond else "ПРОВАЛ", name, "  -- " + str(detail) if (detail and not cond) else ""))


def _xmp_fixture():
    return ('<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
            '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="uuid:aaaaaaaa" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
            'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
            'xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#" '
            'xmlns:stRef="http://ns.adobe.com/xap/1.0/sType/ResourceRef#" '
            'xmp:CreatorTool="Microsoft Word" xmp:CreateDate="2024-01-01T12:00:00+03:00" '
            'xmp:ModifyDate="2024-01-05T15:05:00+03:00" xmpMM:DocumentID="uuid:11111111" '
            'xmpMM:InstanceID="uuid:22222222" pdf:Producer="Microsoft: Print To PDF" photoshop:Credit="Photo Credit">'
            '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Годовой отчёт</rdf:li></rdf:Alt></dc:title>'
            '<dc:creator><rdf:Seq><rdf:li>Иван Петров</rdf:li></rdf:Seq></dc:creator>'
            '<xmpMM:History><rdf:Seq><rdf:li rdf:parseType="Resource"><stEvt:action>saved</stEvt:action>'
            '<stEvt:softwareAgent>Adobe Acrobat 9</stEvt:softwareAgent><stEvt:when>2024-01-05T15:05:00+03:00</stEvt:when>'
            '</rdf:li></rdf:Seq></xmpMM:History>'
            '<xmpMM:DerivedFrom rdf:parseType="Resource"><stRef:instanceID>uuid:33333333</stRef:instanceID>'
            '</xmpMM:DerivedFrom></rdf:Description></rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>').encode("utf-8")


def _fx_base(outlines=False, equal_id=False):
    """Базовый Word-подобный PDF: /Info с личностью, XMP с журналом, теги, закладки."""
    N = pikepdf.Name
    pdf = pikepdf.Pdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=N.Font, Subtype=N.Type1, BaseFont=N.Helvetica))
    for _ in range(2):
        pdf.add_blank_page(page_size=(595, 842))
        pg = pdf.pages[-1].obj
        pg.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        pg.Contents = pdf.make_stream(b"BT /F1 12 Tf 72 720 Td (Page text) Tj ET")
    info = pdf.docinfo
    for k, v in (("/Author", "Иван Петров"), ("/Creator", "Microsoft Word"), ("/Producer", "Microsoft: Print To PDF"),
                 ("/CreationDate", "D:20240101120000+03'00'"), ("/ModDate", "D:20240105150500+03'00'"),
                 ("/Title", "Годовой отчёт"), ("/Company", "ACME Ltd"), ("/Custom", "benign"),
                 ("/PTEX.Fullbanner", "This is pdfTeX, Version 3.14 (TeX Live 2020)")):
        info[k] = v
    pdf.Root["/Lang"] = "ru-RU"
    pdf.Root["/MarkInfo"] = pikepdf.Dictionary(Marked=True)
    pdf.Root["/StructTreeRoot"] = pdf.make_indirect(pikepdf.Dictionary(Type=N.StructTreeRoot))
    pdf.Root["/ViewerPreferences"] = pikepdf.Dictionary(DisplayDocTitle=True)
    if outlines:
        with pdf.open_outline() as ol:
            for t in ("Глава 1", "Глава 2", "Глава 3"):
                ol.root.append(pikepdf.OutlineItem(t, 0))
    m = pdf.make_stream(_xmp_fixture())
    m["/Type"], m["/Subtype"] = N.Metadata, N.XML
    pdf.Root["/Metadata"] = m
    if equal_id:
        pass                         # без /ID QPDF пишет равную пару: так выглядят Word, Chrome, LibreOffice
    else:
        pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(bytes([0x41] * 16)), pikepdf.String(bytes([0x42] * 16))])
    return pdf


def _write(tmp, name, pdf, **kw):
    path = os.path.join(tmp, name + ".pdf")
    pdf.save(path, **kw)
    pdf.close()
    return path


def _annot(pdf, page, **kw):
    a = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Annot, Rect=[10, 10, 100, 40], **kw))
    pg = pdf.pages[page].obj
    if "/Annots" not in pg:
        pg["/Annots"] = pikepdf.Array()
    pg["/Annots"].append(a)
    return a


def _fx_incremental(path):
    """Ручная инкрементальная ревизия: в первой ревизии TOPSECRET, во второй -- «замазка»."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.pages[0].obj["/Contents"] = pdf.make_stream(b"BT 10 100 Td (TOPSECRET 4111) Tj ET")
    pdf.docinfo["/Author"] = "Иван Петров"
    pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable, compress_streams=False, static_id=True)
    with pikepdf.open(path) as q:
        n = q.pages[0].Contents.objgen[0]
    data = open(path, "rb").read()
    old = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
    inner = re.search(rb"trailer\s*<<(.*?)>>", data, re.S).group(1)
    new = b"BT 10 100 Td (REDACTED) Tj ET"
    obj = b"\n%d 0 obj\n<< /Length %d >>\nstream\n" % (n, len(new)) + new + b"\nendstream\nendobj\n"
    o_off = len(data) + obj.index(b"%d 0 obj" % n)
    x_off = len(data) + len(obj)
    tail = (b"xref\n%d 1\n%010d 00000 n \ntrailer\n<<" % (n, o_off) + inner
            + b" /Prev %d >>\nstartxref\n%d\n" % (old, x_off) + b"%%EOF\n")
    open(path, "wb").write(data + obj + tail)


def _raw(path):
    d = open(path, "rb").read()
    return len(re.findall(rb"%%EOF", d)), len(re.findall(rb"/Prev", d))


def _selftest():
    import shutil

    from .inspect import inspect_file

    N, D, S, A = pikepdf.Name, pikepdf.Dictionary, pikepdf.String, pikepdf.Array
    tmp = tempfile.mkdtemp(prefix="clean_pdf_test_")
    try:
        def run(src, name, profile=Profile.STEALTH, **kw):
            dst = os.path.join(tmp, name + "_out.pdf")
            h = _sha256(src)
            res = clean_pdf(src, dst, profile, **kw)
            _check("%s: вход не изменён (sha256 до == после)" % name, _sha256(src) == h)
            return res, dst

        def info_of(path):
            with pikepdf.open(path) as q:
                inf = q.trailer.get("/Info")
                return {k: _s(inf.get(k)) for k in inf.keys()} if isinstance(inf, pikepdf.Dictionary) else {}

        # --- 1. базовый STEALTH --------------------------------------------------
        print("1. STEALTH на Word-подобном файле")
        src = _write(tmp, "base", _fx_base(outlines=True))
        raw = open(src, "rb").read()
        open(src, "wb").write(raw.replace(b"\n%" + _QPDF_MARK + b"\n", b"\n%" + _WORD_MARK + b"\n", 1))   # как у Word
        before = inspect_file(src)
        res, dst = run(src, "stealth")
        _check("ok и dst создан", res.ok and os.path.exists(dst), res.errors)
        i0, i1 = info_of(src), info_of(dst)
        for k in ("/Author", "/Company", "/PTEX.Fullbanner"):
            _check("%s убран" % k, k not in i1)
        for k in ("/Creator", "/Producer", "/CreationDate", "/ModDate", "/Title", "/Custom"):
            _check("%s сохранён как был (в т.ч. кириллица)" % k, i1.get(k) == i0.get(k), (i0.get(k), i1.get(k)))
        _check("Producer не стал pikepdf/qpdf", "pikepdf" not in i1.get("/Producer", "") and "qpdf" not in i1.get("/Producer", "").lower())
        with pikepdf.open(dst) as q:
            xmp = q.Root.Metadata.read_bytes().decode("utf-8")
            _check("XMP остался на месте", "xmpmeta" in xmp)
            for bad in ("DocumentID", "InstanceID", "History", "DerivedFrom", "dc:creator", "photoshop", "Иван", "uuid:"):
                _check("в XMP нет %s" % bad, bad not in xmp)
            _check("xmp:CreatorTool, даты и dc:title на месте", all(x in xmp for x in ("CreatorTool", "CreateDate", "Годовой отчёт")))
            for k in ("/StructTreeRoot", "/MarkInfo", "/Lang", "/Outlines", "/ViewerPreferences"):
                _check("структурное %s сохранено" % k, k in q.Root)
            _check("страниц столько же", len(q.pages) == 2)
            ids = [bytes(x) for x in q.trailer["/ID"]]
        with pikepdf.open(src) as q:
            old_ids = [bytes(x) for x in q.trailer["/ID"]]
        _check("/ID перегенерирован (оба значения новые)", ids[0] != old_ids[0] and ids[1] != old_ids[1] and len(ids[0]) == 16)
        _check("форма пары: исходная различна -- и новая различна", ids[0] != ids[1])
        _check("маркер в заголовке -- как у исходника (Word), а не qpdf", _WORD_MARK in open(dst, "rb").read(32)
               and _QPDF_MARK not in open(dst, "rb").read(32))
        open(src + "2", "wb").write(raw)                       # исходник, уже писанный qpdf: маркер остаётся его
        _check("маркер исходника не выдумываем", clean_pdf(src + "2", dst + "2").ok and _QPDF_MARK in open(dst + "2", "rb").read(32))
        os.remove(src + "2")
        os.remove(dst + "2")
        _check("KEPT-действия есть (Creator, Producer, структурное)", res.count(Act.KEPT) >= 6)
        _check("REGENERATED /ID записано", res.count(Act.REGENERATED) == 1)
        after = inspect_file(dst)
        left = {f.label.split(" ")[0] for f in after.findings if f.risk.value in ("identity", "environment")}
        _check("оракул: из критичного остались только /ID и ложный /Prev закладок", left <= {"/ID[0]", "/ID[1]", "Инкрементальные"}, left)
        new_sig = {s.kind for s in after.signals} - {s.kind for s in before.signals}
        _check("оракул: сигналов scrubbed/inconsistent не появилось", not (new_sig & {"scrubbed", "inconsistent"}), new_sig)
        r2, d2 = run(dst, "stealth_again")
        _check("повторная чистка результата тоже ok", r2.ok, r2.errors)

        # --- 2. равная пара /ID ---------------------------------------------------
        print("2. форма /ID: равная пара остаётся равной")
        s2 = _write(tmp, "eqid", _fx_base(equal_id=True))
        with pikepdf.open(s2) as q:
            old = [bytes(x) for x in q.trailer["/ID"]]
        res, dst = run(s2, "eqid")
        with pikepdf.open(dst) as q:
            ids = [bytes(x) for x in q.trailer["/ID"]]
        _check("фикстура: исходная пара равна", old[0] == old[1])
        _check("пара совпадала -- совпадает, значение новое", res.ok and ids[0] == ids[1] and ids[0] != old[0])

        # --- 3. PARANOID ----------------------------------------------------------
        print("3. PARANOID: оракул требует critical == 0")
        s3 = _write(tmp, "par", _fx_base())
        res, dst = run(s3, "par", Profile.PARANOID)
        _check("ok", res.ok, res.errors)
        after = inspect_file(dst)
        _check("critical_after == 0", after.critical == 0,
               [(f.location, f.label) for f in after.findings if f.risk.value in ("identity", "environment")])
        with pikepdf.open(dst) as q:
            _check("/Info убран целиком, /Metadata убран", "/Info" not in q.trailer and "/Metadata" not in q.Root)
            _check("/ID убран, страницы на месте", "/ID" not in q.trailer and len(q.pages) == 2)
            _check("структурное сохранено и в PARANOID", all(k in q.Root for k in ("/StructTreeRoot", "/MarkInfo", "/Lang")))

        # --- 4. инкрементальные ревизии -------------------------------------------
        print("4. инкрементальные ревизии: текст под «замазкой»")
        s4 = os.path.join(tmp, "inc.pdf")
        _fx_incremental(s4)
        eof0, prev0 = _raw(s4)
        _check("фикстура: TOPSECRET лежит в сырых байтах, 2 ревизии, /Prev 1",
               b"TOPSECRET" in open(s4, "rb").read() and eof0 == 2 and prev0 == 1, (eof0, prev0))
        b4 = inspect_file(s4)
        for prof in (Profile.STEALTH, Profile.PARANOID):
            res, dst = run(s4, "inc_" + prof.value, prof)
            eof1, prev1 = _raw(dst)
            _check("%s: ok" % prof.value, res.ok, res.errors)
            _check("%s: TOPSECRET из файла ушёл, %%EOF == 1, /Prev == 0" % prof.value,
                   b"TOPSECRET" not in open(dst, "rb").read() and eof1 == 1 and prev1 == 0, (eof1, prev1))
            with pikepdf.open(dst) as q:
                _check("%s: на странице осталась «замазка»" % prof.value, b"REDACTED" in q.pages[0].Contents.read_bytes())
            a4 = inspect_file(dst)
            _check("%s: оракул: «Инкрементальные апдейты» и hazard исчезли" % prof.value,
                   not any("Инкрементальные" in f.label for f in a4.findings) and not any(s.kind == "hazard" for s in a4.signals))
            if prof is Profile.PARANOID:
                _check("PARANOID: critical == 0 (было %d)" % b4.critical, a4.critical == 0)

        # --- 5. вложения -----------------------------------------------------------
        print("5. вложения: удалить, но перечислить")
        pdf = _fx_base()
        pdf.attachments["исходник.xlsx"] = b"PK-secret-spreadsheet" * 50
        fs = pikepdf.AttachedFileSpec(pdf, b"hidden-note" * 20, filename="note.txt")
        fa = _annot(pdf, 0, Subtype=N.FileAttachment, FS=pdf.make_indirect(fs.obj), T="Иван Петров", Contents="note")
        fa["/Popup"] = _annot(pdf, 0, Subtype=N.Popup, Parent=fa, Contents="note")
        s5 = _write(tmp, "att", pdf)
        for prof in (Profile.STEALTH, Profile.PARANOID):
            res, dst = run(s5, "att_" + prof.value, prof)
            blob = open(dst, "rb").read()
            _check("%s: ok" % prof.value, res.ok, res.errors)
            _check("%s: байтов вложений в файле нет" % prof.value, b"PK-secret-spreadsheet" not in blob and b"hidden-note" not in blob)
            names = " | ".join(a.before for a in res.actions if a.act is Act.REMOVED)
            _check("%s: в actions названы оба вложения" % prof.value, "исходник.xlsx" in names and "note.txt" in names, names[:200])
            with pikepdf.open(dst) as q:
                _check("%s: /EmbeddedFiles и FileAttachment убраны" % prof.value,
                       len(q.attachments) == 0 and "/Annots" not in q.pages[0].obj)

        # --- 6. активное содержимое и ссылки --------------------------------------
        print("6. активное содержимое")
        pdf = _fx_base()
        js = D(S=N.JavaScript, JS=S("app.alert('x')"))
        pdf.Root["/OpenAction"] = js
        pdf.Root["/AA"] = D(WC=js)
        pdf.Root["/Names"] = D(JavaScript=D(Names=A([S("init"), pdf.make_indirect(js)])))
        pdf.pages[0].obj["/AA"] = D(O=js)
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.Launch, F=S("C:\\Users\\bob\\run.exe")))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.URI, URI=S("file://corp-srv/share/doc.docx")))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.URI, URI=S("https://example.com/page")))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.GoTo, D=A([pdf.pages[1].obj, N.Fit])))
        _annot(pdf, 0, Subtype=N.Link, A=D(S=N.GoTo, D=A([pdf.pages[1].obj, N.Fit]), Next=js))
        s6 = _write(tmp, "act", pdf)
        res, dst = run(s6, "act")
        _check("ok", res.ok, res.errors)
        with pikepdf.open(dst) as q:
            r = q.Root
            _check("OpenAction, AA, Names/JavaScript, AA страницы убраны",
                   "/OpenAction" not in r and "/AA" not in r and "/JavaScript" not in r.get("/Names", D()) and "/AA" not in q.pages[0].obj)
            acts = [(_s(a["/A"].get("/S")), _s(a["/A"].get("/URI"))) if "/A" in a else None for a in q.pages[0].obj.Annots]
        _check("Launch и file:// убраны, https и внутренний GoTo остались, GoTo с JS в /Next убран",
               acts == [None, None, ("/URI", "https://example.com/page"), ("/GoTo", ""), None], acts)
        a6 = inspect_file(dst)
        env = [(f.location, f.label) for f in a6.findings if f.risk.value == "environment"]
        _check("оракул: окружение (JS/OpenAction/AA/Launch/file://) не осталось", not env, env)
        _check("оракул: hazard про активное содержимое исчез", not any("Активное содержимое" in s.detail for s in a6.signals))
        res, dst = run(s6, "act_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            kinds = [_s(a.get("/Subtype")) + (":" + _s(a["/A"].get("/S")) if "/A" in a else "") for a in q.pages[0].obj.get("/Annots", [])]
        _check("PARANOID: от ссылок остался только чистый внутренний GoTo (а с JS в /Next -- удалён)", kinds == ["/Link:/GoTo"], kinds)

        # --- 7. комментарии --------------------------------------------------------
        print("7. аннотации: убрать авторство, не сами аннотации")
        pdf = _fx_base()
        note = _annot(pdf, 0, Subtype=N.Text, T="Иван Петров", M="D:20240102000000", CreationDate="D:20240102000000",
                      Contents="проверьте пункт 5", RC="<p>проверьте</p>")
        note["/Popup"] = _annot(pdf, 0, Subtype=N.Popup, Parent=note, T="Иван Петров", Contents="проверьте пункт 5")
        s7 = _write(tmp, "com", pdf)
        res, dst = run(s7, "com")
        with pikepdf.open(dst) as q:
            an = list(q.pages[0].obj.Annots)
            subs = [_s(a.get("/Subtype")) for a in an]
            keys = set().union(*[set(a.keys()) for a in an])
        _check("обе аннотации на месте", subs == ["/Text", "/Popup"], subs)
        _check("/T, /M, /CreationDate, /RC убраны, popup очищен, /Contents родителя сохранён",
               not (keys & {"/T", "/M", "/CreationDate", "/RC"}) and "/Contents" in an[0] and "/Contents" not in an[1], keys)
        _check("KEPT про текст комментариев есть", any(a.label == "/Contents" and a.act is Act.KEPT for a in res.actions))
        res, dst = run(s7, "com_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            _check("PARANOID: аннотации удалены целиком", "/Annots" not in q.pages[0].obj)
        _check("PARANOID: об этом сказано в note", any("целиком" in a.note for a in res.actions if a.act is Act.REMOVED))

        # --- 8. подпись ------------------------------------------------------------
        print("8. цифровая подпись: по умолчанию отказ")
        pdf = _fx_base()
        sigd = pdf.make_indirect(D(Type=N.Sig, Filter=N.Adobe_PPKLite, SubFilter=N("/adbe.pkcs7.detached"), Name=S("Иван Петров"),
                                   M=S("D:20240105150500"), Contents=S(b"\x00" * 16), ByteRange=A([0, 1, 2, 3])))
        fld = _annot(pdf, 0, Subtype=N.Widget, FT=N.Sig, T=S("Sig1"), V=sigd)
        empty = _annot(pdf, 1, Subtype=N.Widget, FT=N.Sig, T=S("Sig2"))
        pdf.Root["/AcroForm"] = D(Fields=A([fld, empty]), SigFlags=3)
        s8 = _write(tmp, "sig", pdf)
        res, dst = run(s8, "sig")
        _check("подписанный: ok=False, файл не создан, причина названа",
               not res.ok and not os.path.exists(dst) and any("подпис" in e for e in res.errors), res.errors)
        res, dst = run(s8, "sig_f", force_signed=True)
        _check("force_signed: ok, подпись физически убрана", res.ok and b"adbe.pkcs7" not in open(dst, "rb").read(), res.errors)
        _check("force_signed: Act.REMOVED про подпись", any("подпись" in a.label.lower() and a.act is Act.REMOVED for a in res.actions))
        a8 = inspect_file(dst)
        _check("force_signed: оракул не видит заполненных подписей",
               not any(f.label.startswith("Поле подписи «Sig1»") and f.value == "есть" for f in a8.findings))
        pdf = _fx_base()
        fld = _annot(pdf, 0, Subtype=N.Widget, FT=N.Sig, T=S("Sig2"))
        pdf.Root["/AcroForm"] = D(Fields=A([fld]))
        res, dst = run(_write(tmp, "sig_empty", pdf), "sig_empty")
        _check("пустая заготовка подписи НЕ блокирует чистку", res.ok, res.errors)

        # --- 9. форма, слои, whitelist ---------------------------------------------
        print("9. форма, слои, keep")
        pdf = _fx_base()
        tf = _annot(pdf, 0, Subtype=N.Widget, FT=N.Tx, T=S("fio"), V=S("Иван Петров секретно"), AP=D(N=pdf.make_stream(b"q Q")))
        pdf.Root["/AcroForm"] = D(Fields=A([tf]))
        ocg = pdf.make_indirect(D(Type=N.OCG, Name=S("Черновик")))
        pdf.Root["/OCProperties"] = D(OCGs=A([ocg]), D=D(OFF=A([ocg])))
        s9 = _write(tmp, "form", pdf)
        res, dst = run(s9, "form")
        with pikepdf.open(dst) as q:
            _check("STEALTH: значение поля формы сохранено (содержимое)", "/V" in q.Root.AcroForm.Fields[0])
        _check("STEALTH: выключенный слой -- UNREMOVABLE, а не молчание", res.count(Act.UNREMOVABLE) == 1)
        res, dst = run(s9, "form_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            f = q.Root.AcroForm.Fields[0]
            _check("PARANOID: /V и внешний вид поля убраны, NeedAppearances включён",
                   "/V" not in f and "/AP" not in f and bool(q.Root.AcroForm.get("/NeedAppearances")))
        res, dst = run(s7, "keep", keep={"/Author", "dc:creator"})
        with pikepdf.open(dst) as q:
            xmp = q.Root.Metadata.read_bytes().decode("utf-8")
        _check("keep: /Author и dc:creator оставлены, GUID всё равно убраны",
               info_of(dst).get("/Author") == "Иван Петров" and "Иван Петров" in xmp and "DocumentID" not in xmp)
        _check("keep: в actions KEPT со ссылкой на whitelist", any(a.act is Act.KEPT and "whitelist" in a.note for a in res.actions))

        # --- 10. устойчивость -------------------------------------------------------
        print("10. битые, пустые, зашифрованные, неверные аргументы")
        good = open(s3, "rb").read()
        junk = {"empty": b"", "junk": b"%PDF-1.7\n" + os.urandom(400), "notpdf": b"PK\x03\x04hello", "truncated": good[: len(good) // 2]}
        for name, data in junk.items():
            p = os.path.join(tmp, name + ".pdf")
            open(p, "wb").write(data)
            res, dst = run(p, "bad_" + name)
            _check("%s: ok=False, есть errors, dst нет, без исключений" % name,
                   not res.ok and bool(res.errors) and not os.path.exists(dst), res.errors)
        s10 = _write(tmp, "enc", _fx_base(), encryption=pikepdf.Encryption(user="u", owner="o"))
        res, dst = run(s10, "enc")
        _check("зашифрованный: ok=False, dst нет", not res.ok and not os.path.exists(dst), res.errors)
        s10 = _write(tmp, "enc2", _fx_base(), encryption=pikepdf.Encryption(user="", owner="o"))
        res, dst = run(s10, "enc_empty")
        _check("зашифрованный с пустым паролем: тоже отказ (защиту молча не снимаем)", not res.ok and not os.path.exists(dst), res.errors)
        res = clean_pdf(os.path.join(tmp, "нет_такого.pdf"), os.path.join(tmp, "x.pdf"))
        _check("несуществующий вход: ok=False, errors без абсолютного пути",
               not res.ok and bool(res.errors) and not any(tmp in e for e in res.errors), res.errors)
        h = _sha256(s3)
        res = clean_pdf(s3, s3)
        _check("dst == src: отказ, вход цел", not res.ok and _sha256(s3) == h)
        res = clean_pdf(s3, os.path.join(tmp, "x.pdf"), Profile.REGENERATE)
        _check("профиль regenerate для PDF: отказ", not res.ok and bool(res.errors))
        res = clean_pdf(s3, os.path.join(tmp, "x.pdf"), "нет-такого")
        _check("неизвестный профиль: отказ без исключения", not res.ok and bool(res.errors))

        # --- 11. самоотпечаток: Producer, подменённый при сохранении ---------------
        print("11. если save() подменит Producer -- файл не отдаём")
        global _save
        orig_save = _save

        def evil_save(pdf, path):
            pdf.docinfo["/Producer"] = "pikepdf 99"
            orig_save(pdf, path)

        _save = evil_save
        try:
            res, dst = run(src, "evil")
        finally:
            _save = orig_save
        _check("подмена Producer после сохранения ловится: ok=False, dst нет", not res.ok and not os.path.exists(dst), res.errors)

        # --- 12. сквозные места, сжатый XMP, бомба, циклы --------------------------
        print("12. сквозные места: XMP картинки, /PieceInfo, /Thumb, /AF, циклы")
        pdf = _fx_base()
        pdf.Root.Metadata.write(zlib.compress(_xmp_fixture()), filter=N.FlateDecode)      # сжатый XMP
        xm = pdf.make_stream(_xmp_fixture())
        xm["/Type"], xm["/Subtype"] = N.Metadata, N.XML
        img = pdf.make_stream(b"\x00\x00\x00", Type=N.XObject, Subtype=N.Image, Width=1, Height=1,
                              ColorSpace=N.DeviceRGB, BitsPerComponent=8)
        img["/Metadata"] = xm                                                              # XMP у картинки
        pdf.pages[0].obj.Resources["/XObject"] = D(Im1=img)
        pdf.Root["/PieceInfo"] = D(Illustrator=D(LastModified=S("D:20240101000000")))
        pdf.pages[0].obj["/PieceInfo"] = D(Acrobat=D(LastModified=S("D:20240101000000")))
        pdf.pages[0].obj["/Thumb"] = pdf.make_stream(b"\x00")
        pdf.Root["/AF"] = A([pdf.make_indirect(pikepdf.AttachedFileSpec(pdf, b"af-secret", filename="af.xml").obj)])
        ctree = pdf.make_indirect(D(Kids=A([])))
        ctree.Kids.append(ctree)                                                           # цикл в дереве имён
        pdf.Root["/Names"] = D(EmbeddedFiles=ctree)
        fld = pdf.make_indirect(D(FT=N.Tx, T=S("a"), Kids=A([])))
        fld.Kids.append(fld)                                                               # цикл в полях формы
        pdf.Root["/AcroForm"] = D(Fields=A([fld]))
        s12 = _write(tmp, "misc", pdf)
        res, dst = run(s12, "misc")
        _check("ok (циклы не повесили чистку)", res.ok, res.errors)
        with pikepdf.open(dst) as q:
            xmps = [o.read_bytes() for o in q.objects if isinstance(o, pikepdf.Stream) and _s(o.get("/Type")) == "/Metadata"]
            _check("XMP корня (сжатый) и XMP картинки вычищены: GUID-ов нет ни в одном потоке",
                   len(xmps) == 2 and not any(b"uuid:" in x or b"DocumentID" in x for x in xmps), len(xmps))
            _check("/PieceInfo (корень, страница), /Thumb, /AF убраны",
                   "/PieceInfo" not in q.Root and "/PieceInfo" not in q.pages[0].obj and "/Thumb" not in q.pages[0].obj
                   and "/AF" not in q.Root)
        _check("в actions есть и /PieceInfo, и /AF с именем файла", any(a.label == "/AF" and "af.xml" in a.before for a in res.actions)
               and any(a.label == "/PieceInfo" for a in res.actions))
        pdf = _fx_base()
        pdf.Root.Metadata.write(zlib.compress(b"\x00" * (8 << 20)), filter=N.FlateDecode)   # бомба: 8 МБ нулей в ~8 КБ
        s12b = _write(tmp, "xmpbomb", pdf)
        res, dst = run(s12b, "newdir/bomb")
        with pikepdf.open(dst) as q:
            small = len(q.Root.Metadata.read_bytes()) < 1000
        _check("бомба в XMP: не распаковываем, XMP заменён пустой оболочкой, каталог dst создан", res.ok and small and res.count(Act.NORMALIZED) >= 1, res.errors)
        res, dst = run(s12b, "bomb_p", Profile.PARANOID)
        with pikepdf.open(dst) as q:
            _check("бомба в XMP, PARANOID: поток убран", res.ok and "/Metadata" not in q.Root, res.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nИТОГО: %d проверок, %d провалено" % (_TOTAL[0], len(_FAILS)))
    for f in _FAILS:
        print("  ПРОВАЛ:", f)
    return 1 if _FAILS else 0


def _real(paths):
    """Метрики чистки на настоящих файлах. Содержимое и найденные ПДн не печатаются."""
    import shutil

    from .inspect import inspect_file

    def facts(path):
        with pikepdf.open(path) as q:
            inf = q.trailer.get("/Info")
            app = {k: (_s(inf.get(k)) if isinstance(inf, pikepdf.Dictionary) and k in inf else None)
                   for k in ("/Creator", "/Producer")}
            keys = {k for k in ("/StructTreeRoot", "/MarkInfo", "/Lang", "/Outlines", "/PageLabels", "/ViewerPreferences")
                    if k in q.Root}
            outl = sum(1 for o in q.objects
                       if isinstance(o, pikepdf.Dictionary) and "/Prev" in o and "/Title" in o and "/Parent" in o)
            return {"pages": len(q.pages), "app": app, "keys": keys, "trailer_prev": "/Prev" in q.trailer, "outl_prev": outl}

    tmp = tempfile.mkdtemp(prefix="clean_pdf_real_")
    try:
        for n, p in enumerate(paths, 1):
            h0 = _sha256(p)
            b = inspect_file(p)
            eof0, prev0 = _raw(p)
            f0 = facts(p)
            print("файл %d: страниц %d; ДО: critical %d, sensitive %d, %%EOF %d, сырых /Prev %d (из них звеньев закладок "
                  "/Outlines: %d), /Prev в трейлере: %s"
                  % (n, f0["pages"], b.critical, b.sensitive, eof0, prev0, f0["outl_prev"], f0["trailer_prev"]))
            for prof in (Profile.STEALTH, Profile.PARANOID):
                dst = os.path.join(tmp, "o%d_%s.pdf" % (n, prof.value))
                res = clean_pdf(p, dst, prof)
                if not res.ok:
                    print("  %-8s ok=False errors=%s" % (prof.value, res.errors))
                    continue
                a = inspect_file(dst)
                eof1, prev1 = _raw(dst)
                f1 = facts(dst)
                new_kinds = sorted({k for k, _ in {(s.kind, s.detail) for s in a.signals} - {(s.kind, s.detail) for s in b.signals}
                                    if k in ("scrubbed", "inconsistent")})
                left = sorted({(f.location, f.label.split(" ")[0]) for f in a.findings if f.risk.value in ("identity", "environment")})
                print("  %-8s ok=%s errors=%d  critical %d->%d  sensitive %d->%d" % (prof.value, res.ok, len(res.errors), b.critical, a.critical, b.sensitive, a.sensitive))
                print("           %%EOF %d->%d; сырых /Prev %d->%d (звеньев закладок осталось %d); /Prev в ТРЕЙЛЕРЕ %s->%s"
                      % (eof0, eof1, prev0, prev1, f1["outl_prev"], f0["trailer_prev"], f1["trailer_prev"]))
                print("           сохранено структурное %s из %s; Creator/Producer как были: %s; dst открывается в pikepdf, страниц %s"
                      % (sorted(f1["keys"]), sorted(f0["keys"]), f1["app"] == f0["app"], "столько же" if f1["pages"] == f0["pages"] else "ИЗМЕНИЛОСЬ"))
                print("           повторный inspect: новые сигналы scrubbed/inconsistent: %s; остаточно критичное: %s" % (new_kinds or "нет", left))
                print("           действия: %s" % {k.value: len(v) for k, v in res.by_act().items()})
            print("           sha256 входа до == после: %s (%s...)" % (_sha256(p) == h0, h0[:12]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 2 and sys.argv[1] == "--real":
        _real(sys.argv[2:])
    else:
        sys.exit(_selftest())
