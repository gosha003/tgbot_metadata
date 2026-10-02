# -*- coding: utf-8 -*-
"""Рендер CleanResult в отчёт о чистке: Telegram-HTML и plain-text для CLI.

Отчёт о чистке опаснее отчёта об инспекции: пользователь отправит файл дальше
на основании того, что здесь написано. Поэтому главное правило -- отчёт не
имеет права обещать больше, чем доказано повторной инспекцией результата:

  * result is None / ok == False  -> «ФАЙЛ НЕ ПОЧИЩЕН» и причина;
  * формат без чистильщика         -> «НЕ ПОЧИЩЕН, формат только инспектируется
                                       (фаза N)»; на ok=True без единого
                                       действия такому формату не верим;
  * critical_after == -1           -> «результат не проверен», чистоту не заявляем;
  * critical_after > 0             -> «НЕ ЧИСТО» + что именно осталось и почему;
  * critical_after == 0            -> «чисто» -- и только тогда, с оговорками,
                                       если были сбои, ошибки или неубираемое.

Подходы повторяют core/report.py (экранирование, нарезка по UTF-16, посекционная
деградация): значения из файла недоверенные, идут через clip() и html.escape();
одна битая запись -- строка об ошибке на её месте, а не пустой отчёт.

Приватность: ни src, ни dst в отчёт не попадают -- имя файла это метаданные.

Только stdlib.
"""

from __future__ import annotations

import html
import textwrap
from collections import namedtuple
from types import SimpleNamespace

from core import sniff
from core.cleanmodel import ACT_TITLE, Act
from core.model import _ABS_PATH, PATH_STUB, clip
from core.report import spoiler

__all__ = ["render_telegram", "render_plain", "summary_line"]

TG_LIMIT = 4096
TG_BUDGET = TG_LIMIT - 64      # запас под префикс нумерации частей
WIDTH = 100

# Форматы, для которых чистильщик есть (clean_pdf, clean_image, clean_ooxml).
# Должно совпадать с ключами core.clean._CLEANERS, развёрнутыми до форматов:
# по этому множеству бот решает, показывать ли кнопки чистки.
CLEANABLE = frozenset({"pdf", "jpeg", "png", "webp", "gif", "docx", "xlsx", "pptx", "rtf"})

# Чистильщика нет, фаза по docs/ROADMAP.md. Запасной путь: основной номер даёт
# диспетчер через planned_phase, см. _no_cleaner(). Держать эти числа
# согласованными с core.clean._PLANNED обязательно -- иначе пользователь увидит
# в одном сообщении две разные фазы для одного формата.
# OOXML здесь больше нет: с фазы 2 docx/xlsx/pptx чистятся, см. CLEANABLE.
# ODF ещё фаза 2: тот же ZIP, чистильщика пока нет. RTF чистится, см. CLEANABLE.
# В фазе 4 остаётся только то, что на месте не чистится в принципе:
# легаси OLE2 с историей правок, вшитой в контейнер.
_PHASE = {
    "doc": 4, "xls": 4, "ppt": 4, "ole": 4,
    "odt": 2, "ods": 2, "odp": 2,
}
# TIFF/HEIC clean_image отклоняет намеренно (риск испортить файл), это не «ещё не дошли».
_REFUSED = frozenset({"tiff", "heic"})

# Сколько находок инспектора может дать одно REGENERATED-действие: пара
# trailer /ID[0] + /ID[1] у PDF -- две находки IDENTITY. Нужно только для оценки
# «остаток объяснён списком действий»; завышение маскирует необъяснённый остаток,
# поэтому взято минимальное из известных значений.
_PER_REGEN = 2

_DONE = (Act.REMOVED, Act.BLANKED, Act.NORMALIZED, Act.REGENERATED)
_ICON = {
    Act.REMOVED: "\U0001F5D1", Act.BLANKED: "⬜", Act.NORMALIZED: "\U0001F527",
    Act.REGENERATED: "\U0001F501", Act.KEPT: "\U0001F4CC", Act.UNREMOVABLE: "\U0001F512",
    Act.FAILED: "❌",
}
# Лимиты длины значений и пояснений по видам действий. Выбраны так, чтобы даже
# строка из одних "&" (при экранировании x5) влезла в одно сообщение Telegram.
_VAL_CAP = {Act.REMOVED: 200, Act.BLANKED: 200, Act.NORMALIZED: 120,
            Act.REGENERATED: 120, Act.KEPT: 160, Act.UNREMOVABLE: 160, Act.FAILED: 160}
_NOTE_CAP = {Act.REMOVED: 160, Act.BLANKED: 160, Act.NORMALIZED: 240,
             Act.REGENERATED: 240, Act.KEPT: 300, Act.UNREMOVABLE: 400, Act.FAILED: 400}
# Пояснение этих видов важнее значения: выносим на отдельную строку.
_NOTE_OWN_LINE = (Act.KEPT, Act.UNREMOVABLE, Act.FAILED)

_E = namedtuple("_E", "act loc label before after note")

# Вердикты, при которых почищенного файла нет: списки действий не показываем,
# иначе «Удалено: Автор» читалось бы как сделанное.
_NOT_OK = ("none", "unsupported", "failed")


# --- безопасное чтение недоверенного результата -----------------------------


def _c(value, cap=400) -> str:
    """clip(), который не бросает даже на объекте с падающим __str__."""
    try:
        return clip(value, cap)
    except Exception:
        return ""


def _g(obj, name, default=None):
    """getattr, который не падает на падающем свойстве; None -> default."""
    try:
        v = getattr(obj, name)
    except Exception:
        return default
    return default if v is None else v


def _try(obj, name):
    """(значение, прочиталось ли). Нужен там, где «поле не прочиталось» и
    «поле пустое» -- разные факты: первое нельзя выдавать за второе."""
    try:
        return getattr(obj, name), True
    except Exception:
        return None, False


def _val(v):
    """Enum -> его значение. str(Profile.STEALTH) в Python 3.11 даёт
    'Profile.STEALTH', а не 'stealth'."""
    try:
        return getattr(v, "value", v)
    except Exception:
        return v


def _num(v) -> int:
    """Счётчик доказательства. Нечисло и отрицательное = -1 = «не проверялось»."""
    try:
        n = int(v)
    except Exception:
        return -1
    return n if n >= 0 else -1


def _scrub(e) -> str:
    """Текст ошибки без абсолютных путей: CleanResult.err() их режет, но
    errors можно дополнить и в обход него."""
    try:
        return _c(_ABS_PATH.sub(PATH_STUB, str(e)), 600)
    except Exception:
        return "(текст ошибки не прочитался)"


def _snap(result):
    """Снимок результата: каждое поле прочитано один раз и защищённо.
    Дальше рендер работает только со снимком и уже не может упасть на
    битом объекте. Нечитаемое -- это запись в errs, а не молчание."""
    s = SimpleNamespace(has=result is not None, errs=[])
    s.fmt = _c(_val(_g(result, "fmt", "")), 40).lower()
    if not s.has:
        s.fmt_name = "формат неизвестен"
    else:
        s.fmt_name = _c(sniff.pretty(s.fmt), 80) if s.fmt else "формат не указан"
    s.profile = _c(_val(_g(result, "profile", "")), 40) or "не указан"
    try:
        s.ok = bool(_g(result, "ok", False))
    except Exception:
        s.ok = False
    s.cb = _num(_g(result, "critical_before", -1))
    s.ca = _num(_g(result, "critical_after", -1))
    s.sb = _num(_g(result, "sensitive_before", -1))
    s.sa = _num(_g(result, "sensitive_after", -1))

    # Доказательство от диспетчера (DispatchResult из core/clean.py), если оно
    # есть. Оно ПРИОРИТЕТНЕЕ эвристики по critical_after: диспетчер ЗНАЕТ ответ
    # из survived_values (какие исходные значения встречаются в результате), а
    # отчёт может его только угадывать по счётчикам действий. Пока отчёт
    # угадывал сам, он расходился с диспетчером в обе стороны -- и кричал
    # «НЕ ЧИСТО» на доказанно чистый файл, и называл чистым недоказанный.
    s.has_evidence = False
    s.verified = False
    s.survived_n = 0
    try:
        if _g(result, "verified", None) is not None:
            s.has_evidence = True
            s.verified = bool(_g(result, "verified", False))
            sv = _g(result, "survived", None)
            s.survived_n = len(list(sv)) if sv is not None else 0
    except Exception:
        s.errs.append("доказательство диспетчера не прочиталось: вердикт по эвристике")
    s.planned_phase = _num(_g(result, "planned_phase", 0))
    # Сигналы, появившиеся ПОСЛЕ чистки: файл теперь опознаётся как обработанный.
    s.new_signals = []
    try:
        for item in (_g(result, "new_signals", None) or []):
            kind = _c(_val(item[0]), 40)
            s.new_signals.append(kind)
    except Exception:
        s.errs.append("список новых сигналов не прочитался")

    entries, bad = [], 0
    raw, got = _try(result, "actions")
    try:
        items = list(raw)
    except Exception:
        items = []
        got = False
    if s.has and not got:
        s.errs.append("список действий не прочитался: отчёт неполон")
    for a in items:
        try:
            entries.append(_E(Act(_val(a.act)), _c(a.location, 120), _c(a.label, 120),
                              _c(a.before, 600), _c(a.after, 600), _c(a.note, 600)))
        except Exception:
            bad += 1
    if bad:
        s.errs.append("записей о действиях не прочитано: %d, список выше неполон" % bad)
    s.by = {}
    for e in entries:
        s.by.setdefault(e.act, []).append(e)
    s.n_actions = len(entries) + bad
    s.n_changed = sum(len(s.by.get(a, [])) for a in _DONE)

    raw, got = _try(result, "errors")
    try:
        raw = [] if raw is None else [raw] if isinstance(raw, (str, bytes)) else list(raw)
    except Exception:
        raw, got = [], False
    if s.has and not got:
        s.errs.append("список ошибок не прочитался")
    s.errs = [_scrub(e) for e in raw] + s.errs
    return s


def _n(s, *acts) -> int:
    return sum(len(s.by.get(a, [])) for a in acts)


# --- вердикт -----------------------------------------------------------------


def _no_cleaner(s) -> str:
    # Номер фазы берём у диспетчера: у него таблица _PLANNED, и если отчёт
    # назовёт свой номер, пользователь увидит в одном сообщении две разные
    # фазы для одного формата. Своя таблица -- только запасной путь.
    if s.planned_phase > 0:
        return ("формат %s пока только инспектируется, чистка появится в фазе %d"
                % (s.fmt_name, s.planned_phase))
    if s.fmt in _PHASE:
        return ("формат %s пока только инспектируется, чистка появится в фазе %d"
                % (s.fmt_name, _PHASE[s.fmt]))
    if s.fmt in _REFUSED:
        return ("формат %s чистка намеренно не берёт: пересборка без перекодирования "
                "рискует испортить файл" % s.fmt_name)
    if not s.fmt:
        return "в результате не указан формат файла, чистка не подтверждена"
    return "для типа файла «%s» чистка не предусмотрена" % s.fmt_name


def _pair(before, after, arrow) -> str:
    b = str(before) if before >= 0 else "?"
    a = str(after) if after >= 0 else "не проверялось"
    return b + arrow + a


def _V(kind, mark, head, lines, short):
    return SimpleNamespace(kind=kind, mark=mark, head=head, lines=lines, short=short)


def _verdict(s):
    """Один честный вердикт. Порядок проверок -- порядок убывания тяжести."""
    f, u = _n(s, Act.FAILED), _n(s, Act.UNREMOVABLE)
    hidden = (["Записи о действиях (%d) не показаны: почищенного файла нет, и выдавать их за "
               "сделанное было бы неправдой." % s.n_actions] if s.n_actions else [])
    cav = []
    if f:
        cav.append("не удалось обработать: %d" % f)
    if u:
        cav.append("убрать невозможно: %d" % u)
    if s.errs:
        cav.append("ошибок при чистке: %d" % len(s.errs))
    cav_line = ["Оговорки: %s. Подробности в разделах ниже." % "; ".join(cav)] if cav else []
    reason = s.errs[0] if s.errs else ""

    if not s.has:
        return _V("none", "bad", "ФАЙЛ НЕ ПОЧИЩЕН: чистка не вернула результата.",
                  ["Это внутренний сбой, а не «нечего чистить». Почищенной копии нет: "
                   "ничего не отправляйте как почищенное."],
                  "чистка не вернула результата: файл НЕ почищен")

    if s.fmt not in CLEANABLE and not (s.ok and s.n_actions):
        why = _no_cleaner(s)
        lines = ["Сейчас для него доступна только инспекция: она показывает, что лежит в "
                 "файле, но ничего не убирает. Почищенной копии нет, не отправляйте этот "
                 "файл как почищенный."]
        if s.ok:
            lines.append("Чистильщик отчитался об успехе без единого действия: этому нельзя верить.")
        if reason:
            lines.append("Сообщение чистильщика: %s" % reason)
        lines += hidden
        return _V("unsupported", "bad", "ФАЙЛ НЕ ПОЧИЩЕН: %s." % why, lines, "НЕ почищен: %s" % why)

    if not s.ok:
        return _V("failed", "bad", "ФАЙЛ НЕ ПОЧИЩЕН: чистка не завершилась.",
                  ["Причина: %s" % (reason or "не указана"),
                   "Почищенной копии нет: ничего не отправляйте как почищенное."] + hidden,
                  "НЕ почищен, чистка не завершилась")

    # --- доказательство диспетчера приоритетнее эвристики ------------------
    # Диспетчер сверяет ЗНАЧЕНИЯ: какие исходные чувствительные значения
    # встречаются в находках результата. Пустой список и есть доказательство
    # чистоты -- в отличие от critical_after, который считает и НОВЫЕ находки
    # (перегенерированный trailer /ID ничего об исходнике не говорит).
    if s.has_evidence:
        if not s.verified:
            return _V("unverified", "warn",
                      "РЕЗУЛЬТАТ НЕ ПРОВЕРЕН: утверждать, что файл чист, нельзя.",
                      ["Повторная инспекция результата не прогонялась, сверить "
                       "исходные значения с содержимым результата было нечем. "
                       "Прогоните инспекцию, прежде чем считать файл почищенным."]
                      + cav_line, "результат не проверен")
        if s.survived_n:
            return _V("residual", "bad",
                      "НЕ ЧИСТО: исходных значений пережило чистку: %d." % s.survived_n,
                      ["Это значения из самого исходного файла, найденные в "
                       "результате: чистка их не убрала. Такой файл отправлять "
                       "нельзя."] + cav_line,
                      "НЕ чисто: выжило исходных значений %d" % s.survived_n)
        was = _pair(s.cb, s.ca, " -> ")
        lines = ["Доказательство: ни одно исходное чувствительное значение не "
                 "найдено в результате. Сверка идёт значениями, а не счётчиками."]
        if s.new_signals:
            lines.append(
                "НО ФАЙЛ ТЕПЕРЬ ОПОЗНАЁТСЯ КАК ЧИЩЕНЫЙ. Утечки нет, однако после "
                "чистки появились признаки обработки (%s), которых в исходнике не "
                "было. Если вам важно, чтобы сам факт чистки не был виден, этого "
                "результата недостаточно." % ", ".join(sorted(set(s.new_signals))))
            lines.append(
                "Для docx, xlsx и pptx это предел формата, а не недоработка: пустые "
                "dc:creator и cp:lastModifiedBy вместе сами являются признаком, а "
                "оставить настоящее имя значит не почистить, подставить чужое -- "
                "подделать провенанс. Незаметность даёт только пересоздание файла "
                "стоковым приложением (режим Regenerate, фаза 4).")
        if s.ca > 0:
            lines.append("Критичных находок в результате: %d. Это НОВЫЕ значения, "
                         "которых в исходнике не было -- например перегенерированный "
                         "trailer /ID. С исходным файлом они не связаны." % s.ca)
        lines += cav_line
        if f or s.errs:
            return _V("clean", "warn",
                      "ИСХОДНЫХ ЗНАЧЕНИЙ НЕ ОСТАЛОСЬ, НО ЕСТЬ ОГОВОРКИ "
                      "(критичных %s)." % was, lines, "")
        return _V("clean", "ok",
                  "ЧИСТО: ни одно исходное значение не пережило чистку "
                  "(критичных %s)." % was, lines, "")

    if s.ca < 0:
        return _V("unverified", "warn",
                  "РЕЗУЛЬТАТ НЕ ПРОВЕРЕН: утверждать, что файл чист, нельзя.",
                  ["Файл записан и открывается, но повторная инспекция результата не "
                   "прогонялась: число критичных находок после чистки неизвестно. "
                   "Прогоните инспекцию результата, прежде чем считать файл почищенным."]
                  + cav_line, "результат не проверен")

    if s.ca == 0:
        was = "было %s, стало 0" % (s.cb if s.cb >= 0 else "?")
        # Неубираемое само по себе (таблицы квантования JPEG есть у каждого JPEG)
        # статус не понижает: оракул критичного не видит, оговорка стоит прямо
        # под вердиктом. Сбой и ошибки обработки -- понижают: там могло остаться.
        if f or s.errs:
            return _V("clean", "warn", "КРИТИЧНОГО НЕ ОСТАЛОСЬ, НО ЕСТЬ ОГОВОРКИ (%s)." % was,
                      cav_line, "")
        return _V("clean", "ok", "ЧИСТО: повторная инспекция результата не нашла критичных "
                  "находок (%s)." % was, cav_line, "")

    # critical_after > 0: чистым такой файл не называем.
    regen = _n(s, Act.REGENERATED)
    wl = sum(1 for e in s.by.get(Act.KEPT, []) if "whitelist" in e.note.lower())
    unexplained = max(0, s.ca - (regen * _PER_REGEN + f + u + wl))
    lines = []
    if regen:
        lines.append("Перегенерированные значения (%d), например trailer /ID: они новые и с "
                     "исходными не связаны, это не утечка, но инспектор считает их находками." % regen)
    if wl:
        lines.append("Оставлено по вашему списку keep: %d. Это ваш выбор, поля остались как были." % wl)
    if unexplained:
        lines.append("НЕ ОБЪЯСНЕНО списком действий: не менее %d. Пока не выяснено, что это, "
                     "файл чистым считать нельзя: прогоните инспекцию результата." % unexplained)
    lines += cav_line
    was = "было %s" % (s.cb if s.cb >= 0 else "?")
    if unexplained:
        return _V("residual", "bad", "НЕ ЧИСТО: критичных находок после чистки: %d (%s), и не всё "
                  "объяснено." % (s.ca, was), lines, "остаток не объяснён")
    return _V("residual", "warn", "ОСТАЛОСЬ КРИТИЧНЫХ НАХОДОК: %d (%s). По списку действий они "
              "объясняются записями ниже; файл не объявляется чистым." % (s.ca, was), lines, "")


# --- оформление --------------------------------------------------------------


class _Style:
    """Один набор приёмов на два вывода. Методы принимают СЫРОЕ значение и сами
    его чистят и экранируют: готовый HTML сюда передавать нельзя (будет двойное
    экранирование), зато забыть html.escape негде."""

    def __init__(self, tg):
        self.tg = tg
        self.arrow = "→" if tg else "->"
        self.mark = ({"ok": "✅", "warn": "⚠", "bad": "⛔"} if tg
                     else {"ok": "[ OK ]", "warn": "[ !! ]", "bad": "[ XX ]"})
        self.red, self.yel = ("\U0001F534", "\U0001F7E1") if tg else ("  *", "  *")

    def t(self, v, cap=400):
        s = _c(v, cap)
        return html.escape(s, quote=False) if self.tg else s

    def _w(self, tag, v, cap):
        s = self.t(v, cap)
        return "<%s>%s</%s>" % (tag, s, tag) if self.tg and s else s

    def b(self, v, cap=400):
        return self._w("b", v, cap)

    def i(self, v, cap=400):
        return self._w("i", v, cap)

    def code(self, v, cap=400):
        return self._w("code", v, cap)

    def val(self, v, cap=200):
        """Значение из файла. В Telegram уходит под спойлер (политика и
        оговорка -- в core/report.py, флаг MASK_VALUES): отчёт о чистке
        перечисляет ровно то, что было найдено, то есть те же персональные
        данные, что и отчёт инспекции. Пустое не прячем -- скрывать нечего."""
        if not _c(v, cap):
            return self.i("(пусто)")
        return spoiler(self.code(v, cap)) if self.tg else self.code(v, cap)

    def p(self, v, cap=700):
        """Абзац обычного текста."""
        return self.t(v, cap) if self.tg else "  " + self.t(v, cap)

    def lead(self, v, cap=800):
        """Пояснение под заголовком."""
        return self.i(v, cap) if self.tg else "  " + self.t(v, cap)

    def head(self, icon, title, n=None):
        tail = " — %d" % n if n is not None else ""
        if self.tg:
            return ["%s <b>%s</b>%s" % (icon, self.t(title, 120), tail)]
        return [self.t(title, 120) + tail, "-" * WIDTH]

    def loc(self, v):
        return self.i(v, 120) if self.tg else "  " + self.t(v, 120)

    def bullet(self, styled):
        return ("• " if self.tg else "    - ") + styled

    def sub(self, styled):
        return ("   ↳ " if self.tg else "        ") + styled


_TG, _PL = _Style(True), _Style(False)


# --- секции ------------------------------------------------------------------


def _sec_header(s, st, v):
    if st.tg:
        out = ["\U0001F9F9 <b>Чистка метаданных</b> · %s · профиль %s"
               % (st.b(s.fmt_name, 80), st.b(s.profile, 40))]
    else:
        out = ["=" * WIDTH, "ОТЧЁТ О ЧИСТКЕ МЕТАДАННЫХ", "=" * WIDTH,
               "Формат:  %s" % st.t(s.fmt_name, 80), "Профиль: %s" % st.t(s.profile, 40)]
    done = ", ".join("%s %d" % (ACT_TITLE[a], _n(s, a)) for a in _DONE if _n(s, a))
    if v.kind in _NOT_OK:
        out.append("Изменено: %s (почищенного файла нет)" % st.b("0"))
    else:
        line = "Изменено: %s" % st.b(str(s.n_changed))
        out.append(line + (" (%s)" % st.t(done, 200) if done else ""))
    if v.kind in ("unverified", "clean", "residual"):
        out.append(st.b("Доказательство") + st.t(": повторная инспекция результата"))
        out.append("%s Критичные (личность, окружение): %s"
                   % (st.red, st.b(_pair(s.cb, s.ca, " %s " % st.arrow))))
        out.append("%s Требующие чистки (с провенансом): %s"
                   % (st.yel, st.b(_pair(s.sb, s.sa, " %s " % st.arrow))))
    else:
        out.append(st.t("Доказательства нет: файл не почищен, повторная инспекция результата "
                        "не выполнялась."))
    return [out]


def _sec_verdict(s, st, v):
    return [["%s %s" % (st.mark[v.mark], st.b(v.head, 500))] + [st.p(x) for x in v.lines]]


def _by_loc(items):
    g = {}
    for e in items:
        g.setdefault(e.loc or "—", []).append(e)
    return g.items()


def _entry(act, e, st):
    """Одна запись. Битая -- строка об ошибке на её месте."""
    try:
        line = st.t(e.label, 120) or "(без названия)"
        if e.before or act not in (Act.FAILED, Act.UNREMOVABLE):
            line += ": " + st.val(e.before, _VAL_CAP[act])
        if act in (Act.NORMALIZED, Act.REGENERATED) or e.after:
            line += " %s %s" % (st.arrow, st.val(e.after, _VAL_CAP[act]))
        note = _c(e.note, _NOTE_CAP[act])
        if note and act in _NOTE_OWN_LINE:
            return st.bullet(line) + "\n" + st.sub(st.i(note, _NOTE_CAP[act]))
        if note:
            line += " " + st.i("— " + note, _NOTE_CAP[act] + 4)
        return st.bullet(line)
    except Exception:
        return st.bullet(st.t("(запись не отобразилась)"))


def _group(act, s, st, lead="", loud=False):
    items = s.by.get(act)
    if not items:
        return []
    title = ACT_TITLE[act].upper() if loud else ACT_TITLE[act].capitalize()
    out = st.head(_ICON[act], title, len(items))
    if lead:
        out.append(st.lead(lead))
    for loc, grp in _by_loc(items):
        out.append(st.loc(loc))
        out += [_entry(act, e, st) for e in grp]
    return [out]


def _sec_done(s, st, v):
    out = []
    if v.kind in _NOT_OK:
        return out
    for act in _DONE:
        out += _group(act, s, st)
    return out


def _sec_kept(s, st, v):
    return [] if v.kind in _NOT_OK else _group(Act.KEPT, s, st, loud=True, lead=(
        "Эти поля оставлены осознанно, а не забыты. Структурное (язык документа, теги "
        "доступности, ICC-профиль, оглавление) нужно для чтения, печати и скринридеров; часть "
        "вы могли сами попросить сохранить (keep)."
        + (" Профиль stealth к тому же не обнуляет всё подряд: пустые метаданные сами выдают "
           "чистку." if s.profile == "stealth" else "")
        + " Чистка эти поля не трогает."))


def _sec_unrem(s, st, v):
    return [] if v.kind in _NOT_OK else _group(Act.UNREMOVABLE, s, st, loud=True, lead=(
        "Эти данные остаются в файле, и чистка их не уберёт: так устроен формат, либо "
        "удаление сломало бы файл или его проверку. Прежде чем отправлять файл, решите, "
        "приемлемо ли это."))


def _sec_failed(s, st, v):
    return [] if v.kind in _NOT_OK else _group(Act.FAILED, s, st, loud=True, lead=(
        "Чистка этих полей не удалась: считайте, что они остались в файле."))


def _sec_errors(s, st, v):
    if not s.errs:
        return []
    out = st.head("⚠" if st.tg else "", "Ошибки при чистке", len(s.errs))
    out.append(st.lead("Эти части файла не разобрались или не обработались: в них могли остаться "
                       "метаданные, даже если повторная инспекция ничего не нашла."))
    out += [st.bullet(st.code(e, 600)) for e in s.errs]
    return [out]


def _sec_footer(s, st, v):
    msgs = ("Исходный файл чисткой не изменён: она пишет только в новую копию.",
            "Имя файла — тоже метаданные, и Telegram его сохраняет: если в имени есть ФИО или "
            "название проекта, переименуйте файл перед отправкой.",
            "Отчёт касается только метаданных: стиль текста и сам контент он не оценивает.")
    out = ["———" if st.tg else "=" * WIDTH]
    out += [st.lead(m, 400) for m in msgs]
    if not st.tg:
        out.append("=" * WIDTH)
    return [out]


_BUILDERS = (_sec_header, _sec_verdict, _sec_done, _sec_kept, _sec_unrem, _sec_failed,
             _sec_errors, _sec_footer)


def _sections(s, st):
    try:
        v = _verdict(s)
    except Exception:
        v = _V("failed", "bad", "ФАЙЛ НЕ ПОЧИЩЕН: вердикт не собрался.", [],
               "НЕ почищен, вердикт не собрался")
    out = []
    for build in _BUILDERS:
        try:
            out += build(s, st, v)
        except Exception as exc:
            # Сломалась одна секция -- остальные доезжают.
            out.append(["%s секция отчёта не собралась (%s)"
                        % ("⚠" if st.tg else "[ !! ]", type(exc).__name__)])
    return out


# --- нарезка на сообщения Telegram (как в core/report.py) ----------------------


def _tg_len(s: str) -> int:
    """Длина в UTF-16 code units: так считает лимит Telegram, а не len()."""
    return len(s.encode("utf-16-le", "surrogatepass")) // 2


def _pack(sections, limit: int = TG_BUDGET):
    """Секции -> сообщения не длиннее limit. Рвём по границам секций, а не
    влезающую секцию -- по строкам (элементам); элемент всегда с
    сбалансированными тегами, поэтому тег посередине не рвётся никогда."""
    buf, msgs = [], []

    def cur():
        return sum(_tg_len(x) + 1 for x in buf) - 1 if buf else 0

    def flush():
        if buf:
            msgs.append("\n".join(buf))
            del buf[:]

    for sec in sections:
        if not sec:
            continue
        n = sum(_tg_len(x) + 1 for x in sec) - 1
        if n + 1 <= limit:
            if buf and cur() + 1 + n + 1 > limit:
                flush()
            if buf:
                buf.append("")
            buf.extend(sec)
            continue
        if buf:
            if cur() + 1 > limit:
                flush()
            else:
                buf.append("")
        for line in sec:
            if _tg_len(line) > limit:
                # Молча потерять запись в отчёте об утечках хуже, чем пометить.
                line = ("• <i>(запись не влезла в одно сообщение Telegram и пропущена, "
                        "смотрите текстовый отчёт)</i>")
            if buf and cur() + 1 + _tg_len(line) > limit:
                flush()
            buf.append(line)
    flush()
    return msgs


# --- публичный API -------------------------------------------------------------


def render_telegram(result) -> list:
    """CleanResult -> список сообщений для parse_mode="HTML". Теги только b, i, code.
    Не бросает: на любой сбой возвращает сообщение «считайте файл НЕ почищенным»."""
    try:
        parts = _pack(_sections(_snap(result), _TG))
    except Exception as exc:
        return ["⚠ Отчёт о чистке не собрался (%s). Считайте файл НЕ почищенным."
                % type(exc).__name__]
    if not parts:
        return ["⚠ Отчёт пуст. Считайте файл НЕ почищенным."]
    if len(parts) > 1:
        total = len(parts)
        parts = ["<b>[%d/%d]</b>\n%s" % (i, total, p) for i, p in enumerate(parts, 1)]
    return parts


def _wrap(line: str) -> str:
    """Перенос по WIDTH с висячим отступом; содержимое не режем."""
    if len(line) <= WIDTH:
        return line
    body = line.lstrip(" ")
    ind = line[:len(line) - len(body)] + ("  " if body.startswith("- ") else "")
    return textwrap.fill(line, width=WIDTH, subsequent_indent=ind,
                         break_long_words=True, break_on_hyphens=False)


def render_plain(result) -> str:
    """То же без HTML, ширина WIDTH, для CLI. Не бросает."""
    try:
        out = []
        for sec in _sections(_snap(result), _PL):
            if out:
                out.append("")
            for el in sec:
                out += [_wrap(ln) for ln in el.split("\n")]
        return "\n".join(out)
    except Exception as exc:
        return "Отчёт о чистке не собрался (%s). Считайте файл НЕ почищенным." % type(exc).__name__


def summary_line(result) -> str:
    """Одна строка для логов и краткого ответа, без переводов строк:
    «PDF, профиль stealth: изменено 7, critical 7->2, сохранено намеренно 10».
    Имён файлов и содержимого находок в ней нет."""
    try:
        s = _snap(result)
        v = _verdict(s)
        if v.kind in ("none", "unsupported"):
            text = v.short
        elif v.kind == "failed":
            text = "%s, профиль %s: %s" % (s.fmt_name, s.profile, v.short)
        else:
            parts = ["изменено %d" % s.n_changed,
                     "critical %s" % _pair(s.cb, s.ca, "->") if s.ca >= 0 else "не проверено",
                     "сохранено намеренно %d" % _n(s, Act.KEPT)]
            for label, n in (("не убрать", _n(s, Act.UNREMOVABLE)),
                             ("не удалось", _n(s, Act.FAILED)), ("ошибок", len(s.errs))):
                if n:
                    parts.append("%s %d" % (label, n))
            if v.kind == "residual" and v.short:
                parts.append(v.short)
            text = "%s, профиль %s: %s" % (s.fmt_name, s.profile, ", ".join(parts))
        return " ".join(_c(text, 400).split())
    except Exception:
        return "отчёт о чистке не собрался: файл считать НЕ почищенным"


# --- самопроверка --------------------------------------------------------------


def _demo():
    import re
    from html.parser import HTMLParser

    from core.cleanmodel import CleanResult, Profile
    from core.model import PATH_STUB

    allowed = {"b", "i", "u", "s", "a", "code", "pre", "blockquote", "tg-spoiler"}
    tag_re = re.compile(r"</?(?:%s)>" % "|".join(sorted(allowed)))
    fails = []

    class Bal(HTMLParser):
        """Простой парсер: разрешённые теги, правильный порядок закрытия."""

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack, self.bad = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in allowed:
                self.bad.append("запрещённый тег " + tag)
            self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack.pop() != tag:
                self.bad.append("нарушен порядок закрытия " + tag)

    def u16(text):
        """Длина в UTF-16 code units, посчитанная НЕЗАВИСИМО от кода модуля."""
        return len(text.encode("utf-16-le", "surrogatepass")) // 2

    def claims_clean(text):
        """Утверждает ли текст «ЧИСТО:» (а не «НЕ ЧИСТО:»)."""
        return re.search(r"(?<!НЕ )ЧИСТО:", text) is not None

    def check(name, cond, detail=""):
        print("  [%s] %s%s" % ("ok" if cond else "ПРОВАЛ", name, "" if cond else ": %r" % (detail,)))
        if not cond:
            fails.append(name)

    def check_tg(name, result):
        msgs = render_telegram(result)
        sizes = [u16(m) for m in msgs]
        bad = []
        for m in msgs:
            p = Bal()
            p.feed(m)
            p.close()
            bad += p.bad + (["не закрыты: " + ",".join(p.stack)] if p.stack else [])
            rest = tag_re.sub("", m)
            if "<" in rest or ">" in rest:
                bad.append("сырой < или >")
            if re.search(r"&(?!(?:amp|lt|gt);)", rest):
                bad.append("сырой &")
        check("TG %s: %d сообщ., макс %d <= %d, теги сбалансированы"
              % (name, len(msgs), max(sizes), TG_LIMIT), max(sizes) <= TG_LIMIT and not bad,
              (sizes, bad[:3]))
        return msgs

    def check_plain(name, result):
        text = render_plain(result)
        widest = max(len(x) for x in text.split("\n"))
        check("plain %s: макс. ширина %d <= %d, без HTML" % (name, widest, WIDTH),
              widest <= WIDTH and "<code>" not in text and "&amp;" not in text, widest)
        return text

    def mk(fmt="pdf", ok=True, cb=7, ca=2, sb=137, sa=130, profile=Profile.STEALTH):
        return CleanResult(src="C:/Users/secret/Иванов_договор.pdf", dst="C:/Users/secret/out.pdf",
                           fmt=fmt, profile=profile, ok=ok, critical_before=cb,
                           critical_after=ca, sensitive_before=sb, sensitive_after=sa)

    # 1. Все виды Act, значение-инъекция, ok, критичный остаток объяснён /ID.
    full = mk()
    full.act("removed", "/Info", "/Author", "Иванов Иван <ivan@corp.ru>", note="ФИО владельца лицензии")
    full.act("removed", "/Info", "/Subject", "<script>&")
    full.act("removed", "<i>/Info", "<b>/Creator", "")
    full.act("removed", "XMP", "xmpMM:DocumentID", "uuid:1234-abcd")
    full.act("blanked", "/Info", "/Title", "Договор №5")
    full.act("normalized", "заголовок", "бинарный маркер qpdf", "25C2B5", "25E2E3",
             "Заменён равным по длине маркером исходника.")
    full.act("regenerated", "trailer", "/ID", "a1b2c3d4", "9f8e7d6c",
             "Оба значения новые, пара как у исходника.")
    full.act("kept", "/Root", "/Lang", "ru-RU", note="Язык документа: нужен скринридерам.")
    full.act("kept", "/Root", "/StructTreeRoot", "есть", note="Теги доступности.")
    full.act("kept", "/Root", "/Outlines", "есть", note="Оглавление.")
    full.act("kept", "/Root", "/OutputIntents", "ICC sRGB", note="ICC-профиль: без него цвета едут.")
    full.act("unremovable", "DQT", "Таблицы квантования", "2 шт", "без изменений",
             "Убрать нельзя без перекодирования: они сами фингерпринтят энкодер.")
    full.act("failed", "/Names", "JavaScript", note="сбой секции (ValueError)")
    full.err("Секция «/Names»: ValueError: плохой словарь <a href=x>")
    print("=== 1. все виды Act, ok=True, critical 7->2 ===")
    tg = check_tg("полный", full)
    for i, m in enumerate(tg, 1):
        print("--- сообщение %d, %d UTF-16 ---" % (i, u16(m)))
        print(m)
    pl = check_plain("полный", full)
    print(pl)
    joined = "\n".join(tg)
    check("значение экранировано", "&lt;script&gt;&amp;" in joined and "<script>" not in joined)
    check("в plain значение как есть", "<script>&" in pl)
    check("не сказано «ЧИСТО» при critical_after=2", not claims_clean(joined) and not claims_clean(pl))
    check("остаток объяснён /ID, а не 'не объяснён'", "перегенерированные" in joined.lower()
          and "НЕ ОБЪЯСНЕНО" not in joined)
    check("секции KEPT и UNREMOVABLE заметны",
          "СОХРАНЕНО НАМЕРЕННО" in joined and "УБРАТЬ НЕВОЗМОЖНО" in joined
          and "осознанно, а не забыты" in joined and "Таблицы квантования" in joined)
    check("порядок: удалено < обнулено < нормализовано < перегенерировано < сохранено < невозможно < не удалось",
          [joined.find(x) for x in ("Удалено", "Обнулено", "Нормализовано", "Перегенерировано",
                                    "СОХРАНЕНО", "УБРАТЬ", "НЕ УДАЛОСЬ")]
          == sorted(joined.find(x) for x in ("Удалено", "Обнулено", "Нормализовано",
                                             "Перегенерировано", "СОХРАНЕНО", "УБРАТЬ", "НЕ УДАЛОСЬ")))
    check("before виден у удалённого", "Иванов Иван" in joined and "Договор №5" in joined)
    check("подвал: исходник не изменён, имя файла = метаданные",
          "Исходный файл чисткой не изменён" in tg[-1] and "Telegram его сохраняет" in tg[-1])
    check("имена файлов не утекли", all(x not in joined + pl + summary_line(full)
                                         for x in ("Иванов_договор", "secret", "out.pdf")))
    print("summary:", summary_line(full))

    # 2. Ровно пример из задания.
    ex = mk()
    for i in range(6):
        ex.act("removed", "/Info", "поле %d" % i, "v")
    ex.act("regenerated", "trailer", "/ID", "aa", "bb")
    for i in range(10):
        ex.act("kept", "/Root", "k%d" % i, "v")
    check("summary_line == пример из задания",
          summary_line(ex) == "PDF, профиль stealth: изменено 7, critical 7->2, сохранено намеренно 10",
          summary_line(ex))

    # 3. ok=False.
    bad = mk(ok=False, cb=-1, ca=-1, sb=-1, sa=-1)
    bad.err("PDF защищён паролем: чистка отклонена C:\\Users\\secret\\x.pdf")
    print("\n=== 3. ok=False ===")
    m = check_tg("ok=False", bad)
    print("\n".join(m))
    check("ok=False: НЕ ПОЧИЩЕН и причина", "ФАЙЛ НЕ ПОЧИЩЕН" in m[0] and "защищён паролем" in m[0])
    check("ok=False: путь вырезан из ошибки", "secret" not in "\n".join(m) and html.escape(PATH_STUB) in "\n".join(m))
    check("ok=False: нет «чисто» и нет цифр доказательства", not claims_clean(m[0]) and "Доказательства нет" in m[0])
    print("summary:", summary_line(bad))
    check_plain("ok=False", bad)
    bad.act("removed", "/Info", "/Author", "Иванов")
    m = "\n".join(render_telegram(bad))
    check("ok=False + действия: не показаны как сделанное, но об этом сказано",
          "Удалено" not in m and "Иванов" not in m and "не показаны" in m, m[:400])
    del bad.actions[:]

    # 3b. Маскировка значений. Отчёт о чистке перечисляет ровно те значения,
    # что нашёл инспектор, то есть те же персональные данные -- значит прятать
    # их надо здесь так же. Площадка одна (_Style.val), поэтому хватит проверки,
    # что под спойлером оказалось значение, а не пустая строка и не весь абзац.
    msk = mk()
    msk.act("removed", "/Info", "/Author", "Иванов Иван Иванович")
    msk.act("blanked", "docProps/core.xml", "dc:creator", "")
    m = "\n".join(render_telegram(msk))
    check("маскировка: значение ушло под спойлер",
          "<tg-spoiler><code>Иванов Иван Иванович</code></tg-spoiler>" in m, m[:300])
    check("маскировка: пустое значение не прячем (скрывать нечего)",
          "<tg-spoiler><i>(пусто)</i>" not in m and "<i>(пусто)</i>" in m)
    check("маскировка: в plain спойлера нет -- это вывод в локальную консоль",
          "tg-spoiler" not in render_plain(msk))
    import core.report as _rep
    _rep.MASK_VALUES = False
    try:
        check("маскировка: общий выключатель действует и на отчёт о чистке",
              "tg-spoiler" not in "\n".join(render_telegram(msk))
              and "<code>Иванов Иван Иванович</code>" in "\n".join(render_telegram(msk)))
    finally:
        _rep.MASK_VALUES = True

    # 4. critical_after == -1.
    unv = mk(ca=-1, sa=-1)
    unv.act("removed", "/Info", "/Author", "Иванов")
    print("\n=== 4. critical_after = -1 (проверка не прогонялась) ===")
    m = check_tg("не проверено", unv)
    print("\n".join(m))
    check("не проверено: не заявляем чистоту",
          "РЕЗУЛЬТАТ НЕ ПРОВЕРЕН" in m[0] and not claims_clean(m[0]) and "не проверялось" in m[0])
    print("summary:", summary_line(unv))
    check_plain("не проверено", unv)

    # 5. Чисто / остаток без объяснения.
    ok0 = mk(ca=0, sa=130)
    ok0.act("removed", "/Info", "/Author", "Иванов")
    ok0.act("kept", "/Root", "/Lang", "ru-RU", note="язык")
    m = check_tg("чисто", ok0)
    check("critical_after=0 без оговорок: ЧИСТО", claims_clean(m[0]) and "было 7, стало 0" in m[0])
    ok0.act("unremovable", "DQT", "Таблицы квантования", "2 шт", note="нельзя")
    m = check_tg("чисто с оговоркой", ok0)
    check("critical_after=0 + неубираемое: ЧИСТО, но оговорка видна под вердиктом",
          claims_clean(m[0]) and "убрать невозможно: 1" in m[0], m[0][:600])
    ok0.act("failed", "/Names", "JavaScript", note="сбой")
    m = check_tg("чисто с неудачей", ok0)
    check("critical_after=0, но есть FAILED: оговорки, не «ЧИСТО:»",
          not claims_clean(m[0]) and "ОГОВОРКИ" in m[0] and "не удалось обработать: 1" in m[0], m[0][:600])
    res5 = mk(ca=5)
    res5.act("regenerated", "trailer", "/ID", "aa", "bb")
    m = check_tg("остаток не объяснён", res5)
    check("critical_after=5 при одном /ID: «НЕ ЧИСТО» и «не менее 3»",
          "НЕ ЧИСТО" in m[0] and "не менее 3" in m[0], m[0][:400])
    print("summary:", summary_line(res5))
    wl = mk(ca=3)
    wl.act("regenerated", "trailer", "/ID", "aa", "bb")
    wl.act("kept", "/Info", "/Author", "Иванов", note="оставлено по whitelist пользователя (keep)")
    m = check_tg("whitelist", wl)
    check("critical_after=3 = /ID (2) + keep (1): остаток объяснён, но не «ЧИСТО»",
          "НЕ ОБЪЯСНЕНО" not in m[0] and "по вашему списку keep: 1" in m[0] and not claims_clean(m[0]),
          m[0][:500])

    # 6. Форматы без чистильщика: не врать.
    print("\n=== 6. форматы без чистильщика ===")
    # docx/xlsx/pptx здесь больше нет: с фазы 2 чистильщик для них ЕСТЬ
    # (CLEANABLE). Остались форматы, у которых его правда нет.
    for fmt, ok, phase in (("doc", False, "фазе 4"), ("xls", False, "фазе 4"), ("odt", False, "фазе 2"),
                           ("doc", True, "фазе 4")):
        r = mk(fmt=fmt, ok=ok, cb=-1, ca=0 if ok else -1)
        m = check_tg("%s ok=%s" % (fmt, ok), r)
        check("%s ok=%s: НЕ ПОЧИЩЕН + только инспекция + %s" % (fmt, ok, phase),
              "ФАЙЛ НЕ ПОЧИЩЕН" in m[0] and "только инспектируется" in m[0] and phase in m[0]
              and not claims_clean(m[0]), m[0][:300])
        if fmt == "doc" and not ok:
            print(m[0])
        print("summary:", summary_line(r))
    tiff = mk(fmt="tiff", ok=False, cb=-1, ca=-1)
    tiff.err("TIFF: чистка не поддерживается")
    m = check_tg("tiff", tiff)
    check("tiff: намеренный отказ, не «фаза»", "намеренно не берёт" in m[0] and "фазе" not in m[0])
    # Формат без чистильщика, но с действиями и ok=True: отчёт строится как
    # обычно, а не отказом. Страховка на случай, когда чистильщик для формата
    # появится раньше, чем его впишут в CLEANABLE.
    future = mk(fmt="odt", ok=True, ca=0)
    future.act("removed", "meta.xml", "dc:creator", "Иванов")
    check("odt ok=True с действиями (чистильщик вне CLEANABLE): отчёт строится как обычно",
          claims_clean(render_telegram(future)[0]))

    # 7. 500 действий, значения по 5000 символов.
    big = mk()
    for i in range(500):
        big.actions.append(SimpleNamespace(
            act=Act.REMOVED if i % 3 else Act.KEPT, location="loc%d" % (i % 7), label="Поле %d <&>" % i,
            before="<&>" * 1700, after="&" * 5000, note="&" * 5000))
    print("\n=== 7. 500 действий по 5000 символов ===")
    m = check_tg("500 действий", big)
    check("нумерация частей [i/n]", m[0].startswith("<b>[1/%d]</b>" % len(m)) and len(m) > 1)
    check("подвал в последней части", "Исходный файл чисткой не изменён" in m[-1])
    check("вердикт в первой части", "ОСТАЛОСЬ КРИТИЧНЫХ" in m[0] or "НЕ ЧИСТО" in m[0])
    check_plain("500 действий", big)
    print("summary:", summary_line(big))
    flood = mk()
    for i in range(60):
        flood.act("removed", "/Info", "Поле %d" % i, "\U0001F600" * 150, note="\U0001F600" * 100)
    check_tg("эмодзи вне BMP (2 unit на символ)", flood)

    # 8. None и битые объекты.
    print("\n=== 8. None ===")
    m = check_tg("None", None)
    print("\n".join(m))
    print(render_plain(None))
    print("summary:", summary_line(None))
    check("None: НЕ ПОЧИЩЕН", "ФАЙЛ НЕ ПОЧИЩЕН" in m[0] and "НЕ почищен" in summary_line(None))

    class Boom:
        def __getattr__(self, name):
            raise RuntimeError("битый результат")

    class Half:
        fmt, profile, ok = "pdf", Profile.STEALTH, True
        critical_before, sensitive_before, sensitive_after = 7, 1, 1
        errors = ["что-то"]

        @property
        def critical_after(self):
            raise RuntimeError("падающее поле")

        @property
        def actions(self):
            raise RuntimeError("падающий список")

    class BadStr:
        def __str__(self):
            raise RuntimeError("нет str")

    mixed = mk()
    mixed.actions[:] = [SimpleNamespace(act="чушь", location="x", label="x", before="", after="", note=""),
                        object(), SimpleNamespace(act=Act.REMOVED, location="/Info", label=BadStr(),
                                                  before=BadStr(), after="", note=""),
                        ]
    mixed.act("removed", "/Info", "/Author", "Иванов")
    for name, obj in (("Boom", Boom()), ("Half", Half()), ("actions=None", SimpleNamespace(
            fmt="pdf", profile="stealth", ok=True, actions=None, errors=None, critical_before=1,
            critical_after=0, sensitive_before=1, sensitive_after=0)), ("битые записи", mixed),
            ("число строкой", SimpleNamespace(fmt="pdf", profile="x", ok="yes", actions=[], errors="одна ошибка",
                                              critical_before="7", critical_after="abc",
                                              sensitive_before=None, sensitive_after=-5))):
        try:
            t, p, sline = render_telegram(obj), render_plain(obj), summary_line(obj)
            good = (all(isinstance(x, str) for x in t) and isinstance(p, str) and "\n" not in sline)
            check("%s: ни одного исключения" % name, good)
        except Exception as exc:  # noqa: BLE001
            check("%s: ни одного исключения" % name, False, repr(exc))
            continue
        check_tg(name, obj)
        print("   summary:", sline)
    m = "\n".join(render_telegram(mixed))
    check("битая запись -- строка об ошибке, а не пустой отчёт",
          "Чистка метаданных" in m and "Иванов" in m and "не прочитано: 2" in m and not claims_clean(m), m[:500])
    h = render_telegram(Half())[0]
    check("Half: критичное после чистки не прочиталось -> «не проверен»", "РЕЗУЛЬТАТ НЕ ПРОВЕРЕН" in h)
    check("Half: нечитаемый список действий отмечен в ошибках", "список действий не прочитался" in h)

    # 9. Фаззинг: мусор в любых полях. Инвариант один -- без исключений, лимит и теги целы.
    import random
    rnd = random.Random(20260930)
    alphabet = list("ab <>&\"'\n\t\x00\x1b\\/") + ["\ud800", "\U0001F600", "Ж", "‮", "&amp;", "</b>", "<b>"]
    junk = lambda: "".join(rnd.choice(alphabet) for _ in range(rnd.choice((0, 1, 5, 80, 3000))))
    nums = [-5, 0, 1, 7, 10 ** 30, "x", None, 2.5, True]
    bad_runs = 0
    for _ in range(150):
        acts = [rnd.choice(list(Act) + ["чушь", None, 5]) for _ in range(rnd.choice((0, 1, 20, 60)))]
        fz = SimpleNamespace(
            fmt=rnd.choice(["pdf", "docx", "tiff", "", None, junk(), "jpeg", 5]),
            profile=rnd.choice([Profile.PARANOID, "stealth", junk(), None]),
            ok=rnd.choice([True, False, None, "yes"]), src=junk(), dst=junk(),
            critical_before=rnd.choice(nums), critical_after=rnd.choice(nums),
            sensitive_before=rnd.choice(nums), sensitive_after=rnd.choice(nums),
            actions=[SimpleNamespace(act=a, location=junk(), label=junk(), before=junk(), after=junk(),
                                     note=junk()) for a in acts] + [rnd.choice([None, 5, "строка"])],
            errors=rnd.choice([[junk(), junk()], [], None, junk(), 5]))
        try:
            t, p, line = render_telegram(fz), render_plain(fz), summary_line(fz)
            for msg in t:
                q = Bal()
                q.feed(msg)
                q.close()
                rest = tag_re.sub("", msg)
                if (u16(msg) > TG_LIMIT or q.bad or q.stack or "<" in rest or ">" in rest
                        or re.search(r"&(?!(?:amp|lt|gt);)", rest)):
                    bad_runs += 1
            if "\n" in line or not line or any(len(x) > WIDTH for x in p.split("\n")):
                bad_runs += 1
        except Exception as exc:  # noqa: BLE001
            bad_runs += 1
            print("   фаззинг: исключение", repr(exc))
    check("фаззинг, 150 прогонов: без исключений, лимит/теги/ширина целы", bad_runs == 0, bad_runs)

    lines = [summary_line(x) for x in (full, bad, unv, ok0, res5, big, None, Boom(), Half(), mixed)]
    check("summary_line без переводов строк", all("\n" not in x and x for x in lines))

    print()
    if fails:
        print("ПРОВАЛЕНО: %d: %s" % (len(fails), fails))
        raise SystemExit(1)
    print("ОК")


if __name__ == "__main__":
    _demo()
