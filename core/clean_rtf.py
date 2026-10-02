"""Чистка RTF на месте: правка control words, без пересборки документа.

Фаза 2. Вызывает диспетчер core.clean по семейству "rtf". Сам вызывает
разбор групп из core.insp_legacy (тот же обход скобок, что у инспектора:
другой разошёлся бы на \\{ и \\u).

Публичный API:
    clean_rtf(src, dst, profile, keep, force_signed=False) -> CleanResult

Исключений наружу не бросает. Входной файл не изменяется. При отказе
выходной файл не создаётся.

STEALTH убирает личность и окружение (автор, организация, шаблон, пути,
таблицу правок, пользовательские свойства) и оставляет генератор, даты и
rsid: это правда о файле, а их отсутствие -- след чистки. PARANOID убирает
и их.
"""

from __future__ import annotations

import os
import re

from core.cleanmodel import Act, CleanResult, Profile
from core.insp_legacy import (
    _PATH_DECODED_RE,
    _PATH_RE,
    _brace_group,
    _cp_codec,
    _rtf_plain,
    _subgroups,
)

__all__ = ["clean_rtf"]

_MAX = 32 * 1024 * 1024

# Группы внутри \info. Личность и окружение -- всегда; провенанс -- только
# PARANOID, иначе файл без заголовка и дат сам выглядит обработанным.
_LEAK = frozenset(("author", "operator", "company", "manager", "hlinkbase"))
_PROV = frozenset(("title", "subject", "keywords", "category", "doccomm",
                   "comment", "creatim", "revtim", "printim", "buptim"))
_NUMS = ("edmins", "nofpages", "nofwords", "nofchars", "nofcharsws",
         "version", "vern", "id")


def _enc(text) -> str:
    m = re.search(r"\\ansicpg(\d+)", text[:4096])
    return _cp_codec(m.group(1)) if m else "cp1252"


def _kept(keep, name) -> bool:
    if not keep:
        return False
    bag = {str(x).casefold() for x in keep}
    return name.casefold() in bag


def _apply(text, edits):
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def _balanced(text) -> bool:
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def _find(text, dest):
    """Первая группа {\\dest или {\\*\\dest. Возврат (start, end) включая скобки."""
    m = re.search(r"\{\\\*?\\?%s(?![a-zA-Z])" % dest, text)
    if not m:
        return None
    _body, end = _brace_group(text, m.start())
    return m.start(), end


def _blank_span(text, start, end):
    """{\\word содержимое} -> {\\word}. Содержимое -- всё после управляющего слова."""
    inner = text[start + 1:end - 1]
    # {\*\template ...} -- это «\*\» плюс «template», а не «\*\» плюс «\template».
    # Второй вариант жадный шаблон съедает обратный слэш самого слова и не
    # совпадает: проверено на {\*\generator ...}.
    m = re.match(r"\\\*\\[a-zA-Z]+|\\[a-zA-Z]+", inner)
    if not m:
        return None
    return text[start:start + 1 + m.end()] + "}"


def _info_edits(text, paranoid, keep, res, enc):
    found = _find(text, "info")
    if not found:
        return []
    start, end = found
    body = text[start + 1:end - 1]
    base = start + 1
    edits = []
    drop = set(_LEAK)
    if paranoid:
        drop |= _PROV
    for name, _content, inner in _subgroups(body):
        if name not in drop or _kept(keep, name):
            continue
        # Позиция подгруппы в body: inner -- её тело без скобок, ищем его.
        at = body.find("{" + inner + "}")
        if at < 0:
            continue
        gs, ge = base + at, base + at + len(inner) + 2
        blank = _blank_span(text, gs, ge)
        if blank is None:
            continue
        edits.append((gs, ge, blank))
        res.act(Act.BLANKED, "\\info\\%s" % name, name,
                _rtf_plain(inner, enc), "",
                "Группа осталась пустой: отсутствие самого поля заметнее, "
                "чем пустое значение.")
        body = body[:at] + " " * (len(inner) + 2) + body[at + len(inner) + 2:]
    if paranoid:
        for word in _NUMS:
            if _kept(keep, word):
                continue
            for m in re.finditer(r"\\%s-?\d+" % word, text[start:end]):
                edits.append((start + m.start(), start + m.end(), ""))
                res.act(Act.REMOVED, "\\info\\%s" % word, word, m.group(0), "",
                        "Числовое свойство \\info. PARANOID его убирает.")
    return edits


def _drop_group(text, dest, res, label, note):
    found = _find(text, dest)
    if not found:
        return []
    start, end = found
    res.act(Act.REMOVED, "\\*\\%s" % dest, label, "", "", note)
    return [(start, end, "")]


def _blank_dest(text, dest, res, label, note):
    found = _find(text, dest)
    if not found:
        return []
    start, end = found
    blank = _blank_span(text, start, end)
    if not blank or blank == text[start:end]:
        return []
    res.act(Act.BLANKED, "\\*\\%s" % dest, label,
            _rtf_plain(text[start:end], "cp1251"), "", note)
    return [(start, end, blank)]


def _revtbl(text, res, enc):
    found = _find(text, "revtbl")
    if not found:
        return []
    start, end = found
    body = text[start + 1:end - 1]
    base = start + 1
    edits = []
    for _name, _content, inner in _subgroups(body):
        value = _rtf_plain(inner, enc).strip(" ;")
        if not value or value.lower() == "unknown":
            continue
        at = body.find("{" + inner + "}")
        if at < 0:
            continue
        edits.append((base + at, base + at + len(inner) + 2, ""))
        res.act(Act.REMOVED, "\\*\\revtbl", "Автор правки", value, "",
                "Имя из таблицы правок. Служебная запись Unknown остаётся: "
                "Word пишет её всегда, и пустая таблица заметнее.")
    return edits


def _paths(text, res, enc):
    edits = []
    for m in _PATH_RE.finditer(text):
        raw = m.group(0)
        value = _rtf_plain(raw, enc)
        if not _PATH_DECODED_RE.match(value or ""):
            continue
        edits.append((m.start(), m.end(), ""))
        res.act(Act.REMOVED, "поток RTF", "Абсолютный путь", value, "",
                "Путь в теле или в поле. После декодирования это всё ещё путь, "
                "а не огрызок управляющего слова.")
    return edits


def _clean(res, src, dst, profile, keep):
    paranoid = Profile(profile) is Profile.PARANOID
    try:
        with open(src, "rb") as fh:
            raw = fh.read(_MAX + 1)
    except OSError as exc:
        res.err("файл не открылся: %s" % type(exc).__name__)
        return
    if len(raw) > _MAX:
        res.err("файл больше %d МБ: чистить частично нельзя, выход не создан."
                % (_MAX // (1024 * 1024)))
        return
    if not raw.lstrip()[:5] == b"{\\rtf":
        res.err("это не RTF: нет сигнатуры {\\rtf. Выход не создан.")
        return
    text = raw.decode("latin-1")
    enc = _enc(text)
    edits = []
    edits += _info_edits(text, paranoid, keep, res, enc)
    if not _kept(keep, "userprops"):
        edits += _drop_group(text, "userprops", res, "Пользовательские свойства",
                             "Произвольные поля организации. Группы нет у обычного "
                             "файла, поэтому удаление целиком ничем не выделяется.")
    edits += _revtbl(text, res, enc)
    if not _kept(keep, "template"):
        edits += _blank_dest(text, "template", res, "Шаблон",
                             "Путь к шаблону. Пустая группа -- как у файла без шаблона.")
    if not _kept(keep, "atrfstart"):
        edits += _blank_dest(text, "atrfstart", res, "Метка аннотации",
                             "Инициалы автора примечания.")
    if paranoid and not _kept(keep, "generator"):
        edits += _blank_dest(text, "generator", res, "Генератор",
                             "PARANOID убирает строку приложения.")
    elif _find(text, "generator"):
        res.act(Act.KEPT, "\\*\\generator", "Генератор", "", "",
                "STEALTH: приложение и версия -- правда о файле. Пустой "
                "генератор читается как след чистки.")
    if paranoid and not _kept(keep, "rsidtbl"):
        edits += _blank_dest(text, "rsidtbl", res, "Таблица сеансов",
                             "PARANOID убирает rsid. Файл после этого может "
                             "выглядеть собранным библиотекой, а не Word.")
    # Пути -- после групп: шаблон уже вырезан, и одно и то же место не
    # режется дважды. Отрезки групп и пути не должны пересекаться; если
    # путь лежит внутри уже вырезанной группы, второй проход его не видит,
    # потому что правки применяются к исходному тексту разом. Поэтому пути
    # считаем по тексту ПОСЛЕ групповых правок.
    staged = _apply(text, edits)
    out = _apply(staged, _paths(staged, res, enc))
    if not _balanced(out) or not out.lstrip().startswith("{\\rtf"):
        res.errors.clear()
        res.actions.clear()
        res.err("правка сломала структуру RTF. Выход не создан.")
        return
    tmp = str(dst) + ".part"
    try:
        with open(tmp, "wb") as fh:
            fh.write(out.encode("latin-1"))
        os.replace(tmp, dst)
    except OSError as exc:
        res.err("не удалось записать результат: %s" % type(exc).__name__)
        return
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    res.ok = True


def clean_rtf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult:
    """Очистить RTF src -> dst. Вход не меняется. Не бросает.

    src, dst: пути. dst не должен совпадать с src -- это проверяет диспетчер.
    profile: Profile.STEALTH или Profile.PARANOID.
    keep: имена полей, которые оставить (author, template, ...).
    force_signed: не используется, RTF подписи не несёт. Есть, чтобы сигнатура
        совпадала с остальными чистильщиками.
    Возврат: CleanResult. ok=False -- файла нет.
    """
    del force_signed
    res = CleanResult(src=str(src), dst=str(dst), fmt="rtf",
                      profile=getattr(profile, "value", str(profile)))
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err("Неизвестный профиль чистки.")
        return res
    try:
        _clean(res, str(src), str(dst), prof, keep)
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.err("Внутренняя ошибка чистки RTF: %s" % type(exc).__name__)
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
    return res


def _selftest():
    import os
    import tempfile

    from core.clean import survived_values
    from core.inspect import inspect_file
    from tests.fixtures import RTF_AUTHOR, RTF_GENERATOR, make_rtf

    tmp = tempfile.mkdtemp(prefix="rtf_clean_")
    try:
        src, _exp = make_rtf(tmp)
        before = open(src, "rb").read()
        dst = os.path.join(tmp, "out.rtf")
        res = clean_rtf(src, dst, Profile.STEALTH)
        assert res.ok and not res.errors, res.errors
        assert open(src, "rb").read() == before
        rep = inspect_file(dst)
        assert not survived_values(inspect_file(src), rep), survived_values(inspect_file(src), rep)
        blob = " ".join(f.value or "" for f in rep.findings)
        assert RTF_AUTHOR not in blob
        assert RTF_GENERATOR in blob, "STEALTH обязан оставить генератор"
        dst2 = os.path.join(tmp, "out2.rtf")
        res2 = clean_rtf(src, dst2, Profile.PARANOID)
        assert res2.ok, res2.errors
        blob2 = " ".join(f.value or "" for f in inspect_file(dst2).findings)
        assert RTF_GENERATOR not in blob2
        assert not survived_values(inspect_file(src), inspect_file(dst2))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    _selftest()
