# -*- coding: utf-8 -*-
"""Диспетчер чистки и CLI (фаза 1). Зеркало core/inspect.py.

    clean_file(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False,
               verify=True) -> DispatchResult

Определяет формат через core.sniff и маршрутизирует по core.sniff.family:
pdf -> clean_pdf, image -> clean_image, ooxml -> clean_ooxml.
Остальные форматы честно
отклоняются: чистки для них ещё нет, и делать вид, что почистили, нельзя.
Отдать пользователю файл, который он считает почищенным, а он не почищен, --
худший исход для этого проекта.

ДОКАЗАТЕЛЬСТВО. Инспектор -- оракул: до чистки inspect_file(src), после --
inspect_file(dst). Но критерий успеха НЕ «critical_after == 0»: в профиле
STEALTH у PDF остаются два критичных находки -- перегенерированные trailer
/ID[0] и /ID[1]. Это не утечка: значения новые, случайные и с исходником не
связаны. Настоящее доказательство -- survived_values(): какие ИСХОДНЫЕ
значения IDENTITY/ENVIRONMENT (длиной больше 5 символов) встречаются в
находках результата. Пустой список -- значит чисто.

РЕЗУЛЬТАТ. cleanmodel.CleanResult трогать нельзя (контракт), поэтому
доказательство лежит в его подклассе DispatchResult: настоящие поля
dataclass, а не присвоенные на лету атрибуты -- они видны dataclasses.asdict()
и попадают в --json. Подкласс остаётся CleanResult для isinstance, а свойство
clean переопределено: оно учитывает survived, а не только critical_after
(базовое clean для STEALTH-PDF всегда False, хотя файл чист).

Три исхода, которые нельзя путать:
  ok=False                   -- файл НЕ выдан (dst не создан и не тронут);
  ok=True,  clean=False      -- файл выдан, но доказать чистоту нельзя
                                (остались исходные значения, или verify=False);
  ok=True,  clean=True       -- файл выдан, ни одно исходное значение не выжило.

ВХОД НЕ МОДИФИЦИРУЕТСЯ: dst совпавший с src отклоняется (по нормализованному
пути и по samefile), чистка идёт во временный каталог рядом с dst и в dst
попадает одним os.replace только после всех проверок -- существующий dst при
любом отказе остаётся нетронутым.

Приватность: ни имён файлов, ни значений находок в логи не пишем (логов нет).
Только stdlib; тяжёлые зависимости тянутся через сами чистильщики.

CLI: python -m core.clean <файл> [--out ПУТЬ] [--profile stealth|paranoid]
                         [--force-signed] [--json] [--overwrite] [--selftest]
Код возврата: 0 -- чисто и доказано; 1 -- файл не выдан; 2 -- выдан, но чистота не доказана.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import sys
import tempfile
from enum import Enum

from . import sniff
from .clean_image import clean_image
from .clean_ooxml import clean_ooxml
from .clean_pdf import clean_pdf
from .cleanmodel import ACT_TITLE, Act, CleanResult, Profile
from .inspect import inspect_file
from .model import Risk, clip

# Больше этого не берём вовсе: у PDF-чистильщика свой потолок 512 МБ, у
# картинок 50 МБ (в память). Здесь -- общий предохранитель диспетчера.
MAX_FILE = 512 * 1024 * 1024

# «Длиной больше 5 символов»: короткие значения («Word», «1.7», «ru») дают
# ложные совпадения по подстроке, а по ним судят о чистоте.
MIN_VALUE_LEN = 6

_LEAKY = (Risk.IDENTITY, Risk.ENVIRONMENT)

# Чего ждать от форматов, у которых чистки ещё нет: (фаза, пояснение).
#
# Номер фазы живёт ЗДЕСЬ: cleanreport._no_cleaner() берёт planned_phase у
# диспетчера, а своя таблица у него -- только запасной путь.
#
# ODF и RTF отнесены к фазе 2, а не 4, и это решение по существу: в фазу 4
# формат попадает тогда, когда почистить его на месте нельзя в принципе.
# Для легаси OLE2 это так -- метаданные и история правок вшиты в контейнер,
# в потоках 1Table/0Table лежит удалённый текст прошлых версий, и единственный
# способ от них избавиться -- пересоздать файл стоковым приложением. Для ODF
# это не так: тот же ZIP, что и OOXML, и zipfix его уже умеет. Для RTF тоже
# не так: плоский текст с control words, правится напрямую. Регенерация для
# них была бы не необходимостью, а потерей вёрстки без причины.
_PLANNED = {
    "odf": (2, "Чистка ODF -- фаза 2."),
    "rtf": (2, "Чистка RTF -- фаза 2."),
    "ole": (4, "Легаси OLE2 (doc/xls/ppt) на месте не чистится в принципе: метаданные и "
               "история правок вшиты в сам контейнер, нужна конвертация через стоковое "
               "приложение -- фаза 4."),
}

# Форматы, для которых чистки нет и в плане её нет.
_NOT_PLANNED = {
    "zip": "ZIP-архив общего вида: чистка архивов в плане не предусмотрена.",
    "text": "Текст/CSV: структурированных метаданных документа в таком файле нет, "
            "чистка этого формата в плане не предусмотрена.",
}

_UNREADABLE = {
    "cloud": "файл -- плейсхолдер облачного провайдера (OneDrive и аналоги): на диске нет "
             "содержимого. Откройте его в проводнике или выберите «Всегда хранить на этом "
             "устройстве», дождитесь скачивания и повторите.",
    "denied": "нет доступа на чтение: нет прав или файл занят другим процессом "
              "(чаще всего открыт в Word или Excel).",
    "missing": "файл не найден.",
    "dir": "это каталог, а не файл.",
    "reparse": "файл -- ссылка или точка повторной обработки, содержимое не читается.",
}


# ============================================================================
#  Результат
# ============================================================================


@dataclasses.dataclass
class Survivor:
    """Исходное чувствительное значение, которое нашлось в результате."""

    risk: str
    location: str      # где лежало в исходнике
    label: str
    value: str
    found_at: str      # где найдено в результате: «location > label»

    def __post_init__(self) -> None:
        self.risk = Risk(self.risk).value
        self.value = clip(self.value)


@dataclasses.dataclass
class DispatchResult(CleanResult):
    """CleanResult + доказательство диспетчера. См. docstring модуля."""

    verified: bool = False          # inspect_file(dst) выполнен и сошёлся по формату
    survived: list = dataclasses.field(default_factory=list)   # [Survivor]
    # Критичное, что инспектор всё ещё видит в результате: (location, label,
    # new_value). new_value=True -- такого значения нет ни в одной находке
    # исходника: перегенерированный trailer /ID у PDF и подобное. С исходником оно
    # не связано, это не утечка. Определяется по данным, а не по тому, что чистильщик
    # записал в actions: PDF без /ID получает свежий /ID от QPDF без всякого действия.
    residual: list = dataclasses.field(default_factory=list)
    gaps: list = dataclasses.field(default_factory=list)       # чего инспектор не смог разобрать
    planned_phase: int = 0          # >0 -- формат отклонён: чистка будет в этой фазе
    # Сигналы, которых в исходнике НЕ БЫЛО, а в результате появились. Прежде
    # всего "scrubbed" и "ai": они означают, что файл теперь опознаётся как
    # обработанный. Утечки в этом нет (clean остаётся True), но профиль
    # STEALTH существует ради незаметности, и молча отдать такой файл --
    # обмануть пользователя в том единственном, ради чего он выбрал STEALTH.
    # На OOXML это не дефект чистки, а предел формата: пустые dc:creator и
    # cp:lastModifiedBy вместе сами являются признаком, а любая альтернатива
    # либо течёт, либо подделывает провенанс. Настоящее решение -- Regenerate
    # (фаза 4), то есть пересоздание файла стоковым приложением.
    new_signals: list = dataclasses.field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Файл выдан, ни одно исходное значение IDENTITY/ENVIRONMENT в нём не
        найдено, и инспектор разобрал файл целиком (иначе утечка могла спрятаться
        в неразобранной части). НЕ равно critical_after == 0 -- см. docstring модуля."""
        return bool(self.ok and self.verified and not self.survived and not self.gaps)


def _norm(value) -> str:
    # Значения clip()-нуты до 400 символов с «…» на конце; хвост в сравнении не нужен.
    return " ".join(str(value).casefold().split()).rstrip("…")


def survived_values(src_report, dst_report) -> list:
    """Какие ИСХОДНЫЕ значения IDENTITY/ENVIRONMENT (длиной больше 5 символов)
    встречаются в значениях находок выходного файла -- любого риска: имя автора,
    попавшее в заголовок, -- такая же утечка, как поле Author.

    Пустой список и есть доказательство чистоты. Совпадение -- подстрока без
    учёта регистра и лишних пробелов. Каждое уникальное значение -- один раз."""
    dst = [(g, _norm(g.value)) for g in dst_report.findings]
    dst = [(g, n) for g, n in dst if n]
    out, seen = [], set()
    for f in src_report.findings:
        if f.risk not in _LEAKY:
            continue
        v = _norm(f.value)
        if len(v) < MIN_VALUE_LEN or v in seen:
            continue
        seen.add(v)
        for g, n in dst:
            if v in n:
                out.append(Survivor(f.risk, f.location, f.label, f.value,
                                    "%s > %s" % (g.location, g.label)))
                break
    return out


# ============================================================================
#  Диспетчер
# ============================================================================


def _sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _norm_path(p) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _same_file(a, b) -> bool:
    """dst не должен совпасть с src ни при каком написании пути: другой регистр,
    «.\\», символическая ссылка, жёсткая ссылка, короткое имя 8.3."""
    if _norm_path(a) == _norm_path(b):
        return True
    try:
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


# Тесты подменяют чистильщиков через этот словарь.
_CLEANERS = {
    "pdf": lambda s, d, prof, keep, force: clean_pdf(s, d, prof, keep, force),
    "image": lambda s, d, prof, keep, force: clean_image(s, d, prof, keep),
    "ooxml": lambda s, d, prof, keep, force: clean_ooxml(
        s, d, prof, keep, force_signed=force),
}


def _refuse(res, fmt, fam, size) -> None:
    """Формата без чистильщика: честный отказ с номером фазы."""
    name = sniff.pretty(fmt)
    if fam in _PLANNED:
        res.planned_phase, detail = _PLANNED[fam]
        res.err("Файл НЕ очищен: формат «%s» пока только инспектируется, чистка ещё не "
                "реализована. %s Выходной файл не создан." % (name, detail))
    elif fam in _NOT_PLANNED:
        res.err("Файл НЕ очищен: %s Выходной файл не создан." % _NOT_PLANNED[fam])
    elif fam == "unknown":
        res.err("Файл НЕ очищен: %s Выходной файл не создан." %
                ("файл пуст (0 байт), возможно, не докачался." if size == 0
                 else "формат не опознан, чистить нечем."))
    else:
        res.err("Файл НЕ очищен: для формата «%s» чистки нет. Выходной файл не создан." % name)


def _run(res, tmpdirs, src, dst, profile, keep, force_signed, verify) -> None:
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err("Неизвестный профиль чистки.")
        return
    res.profile = prof.value
    if prof is Profile.REGENERATE:
        res.planned_phase = 4
        res.err("Профиль regenerate (пересоздание через LibreOffice) -- фаза 4, пока не "
                "реализован. Файл НЕ очищен, выходной файл не создан.")
        return

    fmt = sniff.sniff(src)
    res.fmt = fmt
    if fmt == "unreadable":
        reason, code = sniff.unreadable_reason(src)
        res.err("Файл не очищен: %s" % _UNREADABLE.get(
            reason, "файл не читается (errno %s)." % code))
        return

    # Вход не перезаписываем никогда -- проверяем до всего остального.
    if _same_file(src, dst):
        res.err("Выходной путь совпадает с входным: входной файл не перезаписывается никогда.")
        return
    if os.path.isdir(dst):
        res.err("Выходной путь -- каталог, нужен путь к файлу.")
        return

    size = os.path.getsize(src)
    if size > MAX_FILE:
        res.err("Файл больше %d МБ: чистка не выполнена." % (MAX_FILE >> 20))
        return

    fam = sniff.family(fmt)
    cleaner = _CLEANERS.get(fam)
    if cleaner is None:
        _refuse(res, fmt, fam, size)
        return

    # Временный каталог рядом с dst: тогда os.replace атомарен (тот же том), а
    # существующий dst при отказе остаётся нетронутым.
    parent = os.path.dirname(os.path.abspath(dst))
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".clean_", dir=parent)
    tmpdirs.append(tmp)
    out = os.path.join(tmp, "out.tmp")

    sha_before = _sha(src)
    sub = cleaner(src, out, prof, keep, force_signed)
    res.actions = list(sub.actions)
    for e in sub.errors:
        res.errors.append(e)          # уже прошли CleanResult.err (пути вырезаны)
    if not sub.ok:
        return
    if not os.path.isfile(out):
        res.err("Чистильщик сообщил об успехе, но выходного файла нет.")
        return

    if verify:
        before = inspect_file(src, max_size=MAX_FILE)
        after = inspect_file(out, max_size=MAX_FILE)
        if after.fmt != fmt:
            res.err("Результат определяется как %s, а не %s: файл не выдан." %
                    (sniff.pretty(after.fmt), sniff.pretty(fmt)))
            return
        res.critical_before, res.sensitive_before = before.critical, before.sensitive
        res.critical_after, res.sensitive_after = after.critical, after.sensitive
        res.survived = survived_values(before, after)
        was = {_norm(f.value) for f in before.findings}
        # Значение короче MIN_VALUE_LEN не может быть утечкой личности, и
        # проверять его на вхождение в исходник бессмысленно: ПУСТАЯ строка
        # встречается в исходнике всегда, поэтому обнулённые нами же поля
        # (dc:creator, Company) засчитывались как «несут исходное значение» и
        # отчёт пугал утечкой, которой нет. Короткое и пустое -- не исходное.
        res.residual = [(g.location, g.label,
                         len(_norm(g.value)) < MIN_VALUE_LEN or _norm(g.value) not in was)
                        for g in after.findings if g.risk in _LEAKY]
        res.gaps = (["до чистки: " + e for e in before.errors] +
                    ["после чистки: " + e for e in after.errors])
        was_sig = {(sg.kind, sg.detail) for sg in before.signals}
        was_kinds = {sg.kind for sg in before.signals}
        res.new_signals = [(sg.kind, sg.detail) for sg in after.signals
                           if (sg.kind, sg.detail) not in was_sig
                           and sg.kind in ("scrubbed", "ai")
                           and sg.kind not in was_kinds]
        res.verified = True

    # Вход не изменился за время работы: иначе «до» и «после» сравнивали разные байты.
    if _sha(src) != sha_before:
        res.err("Входной файл изменился во время работы: результат ненадёжен, файл не выдан.")
        return

    try:
        os.replace(out, dst)
    except OSError as exc:
        res.err("Не удалось записать выходной файл (errno %s): он занят другим процессом или "
                "нет прав. Файл не выдан." % exc.errno)
        return
    res.ok = True


def clean_file(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False,
               verify=True) -> DispatchResult:
    """Очистить src -> dst. Вход не модифицируется никогда. НЕ бросает исключений.

    ok=False -- файл не выдан: dst не создан, а существующий dst не тронут;
    причина в errors. Для формата без чистильщика planned_phase > 0.
    verify=True -- повторная инспекция до/после, заполняются critical_*,
    sensitive_*, survived, residual, gaps; res.clean -- вердикт.
    verify=False -- файл выдан без доказательства (clean всегда False).
    keep, force_signed -- как у clean_pdf; картинки понимают keep={"icc"}."""
    try:
        s, d = os.fsdecode(src), os.fsdecode(dst)
    except Exception:  # noqa: BLE001 -- мусор вместо пути
        s, d = str(src), str(dst)
    res = DispatchResult(src=s, dst=d, fmt="unknown",
                         profile=str(getattr(profile, "value", profile)))
    tmpdirs = []
    try:
        _run(res, tmpdirs, s, d, profile, keep, force_signed, verify)
    except Exception as exc:  # noqa: BLE001 -- последний рубеж: на любом входе не падаем
        res.ok = False
        res.err("Внутренняя ошибка диспетчера чистки: %s: %s" % (type(exc).__name__, exc))
    finally:
        for t in tmpdirs:
            shutil.rmtree(t, ignore_errors=True)
    if not res.ok:
        # Файл не выдан: список «что сделали» над невыданным файлом только вводит в заблуждение.
        res.actions.clear()
        res.verified = False
        res.survived, res.residual, res.gaps = [], [], []
        res.critical_before = res.critical_after = res.sensitive_before = res.sensitive_after = -1
    return res


# ============================================================================
#  CLI
# ============================================================================


def _default_out(path) -> str:
    base, ext = os.path.splitext(path)
    return base + ".clean" + ext


def _json_default(obj):
    return obj.value if isinstance(obj, Enum) else str(obj)


def _to_json(res) -> str:
    data = dataclasses.asdict(res)
    data["clean"] = res.clean
    return json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)


def _verdict(res) -> str:
    if not res.ok:
        return "НЕ ПОЧИЩЕНО: файл не выдан"
    if res.clean:
        return "ПОЧИЩЕНО: ни одно исходное значение не найдено в результате"
    if not res.verified:
        return "ЗАПИСАНО БЕЗ ПРОВЕРКИ (verify=False): чистота не доказана"
    # clean == ok and verified and not survived and not gaps. Досюда можно
    # дойти при НУЛЕ выживших значений -- когда непусты gaps. Называть
    # причиной выжившие значения в этом случае значит врать пользователю
    # о том, что именно не получилось.
    if res.survived:
        return ("ПОЧИЩЕНО НЕ ПОЛНОСТЬЮ: исходных значений осталось %d"
                % len(res.survived))
    if res.gaps:
        return ("ЧИСТОТА НЕ ДОКАЗАНА: исходных значений не найдено, но проверку "
                "провести полностью не удалось (%s)" % "; ".join(res.gaps[:2]))
    return "ЧИСТОТА НЕ ДОКАЗАНА: причина не определена"


def _render_plain(res, details=True) -> str:
    """Вердикт и доказательство диспетчера -- всегда отсюда, по значениям, а не по
    счётчику critical_after. details=False: подробности (формат, действия,
    сообщения) уже есть в отчёте core.cleanreport, здесь только вердикт и пути."""
    lines = ["Итог: " + _verdict(res)]
    if details:
        lines.append("Формат: %s   Профиль: %s" % (sniff.pretty(res.fmt), res.profile))
    if res.planned_phase:
        lines.append("Чистка этого формата запланирована на фазу %d." % res.planned_phase)
    if res.verified:
        lines.append("Критичных находок: %d -> %d   Всех чувствительных: %d -> %d" % (
            res.critical_before, res.critical_after, res.sensitive_before, res.sensitive_after))
        lines.append("Исходных значений, пережившихся чистку: %d" % len(res.survived))
        for sv in res.survived[:20]:
            lines.append("  ! %s (%s) -> найдено: %s" % (sv.label, sv.location, sv.found_at))
        if len(res.survived) > 20:
            lines.append("  ... и ещё %d" % (len(res.survived) - 20))
        new = [r for r in res.residual if r[2]]
        old = [r for r in res.residual if not r[2]]
        if new:
            lines.append("Критичное в результате, но значения новые (в исходнике таких нет, "
                         "с ним не связаны): %d (%s)" % (
                             len(new), ", ".join(sorted({"%s %s" % (r[0], r[1].split(" ")[0]) for r in new}))))
        if old:
            lines.append("Критичное в результате с исходными значениями: %d" % len(old))
        if res.gaps:
            lines.append("Инспектор не разобрал часть файла (%d), доказательство неполное:" % len(res.gaps))
            lines.extend("  - " + g for g in res.gaps[:10])
    if details:
        if res.ok and res.actions:
            lines.append("Действия: " + ", ".join(
                "%s: %d" % (ACT_TITLE[a], n) for a, n in ((a, res.count(a)) for a in Act) if n))
        if res.errors:
            lines.append("Сообщения:")
            lines.extend("  - " + e for e in res.errors)
    if res.ok:
        lines.append("Результат: " + res.dst)    # cleanreport путей не печатает намеренно
    return "\n".join(lines)


def _render(res) -> str:
    """Подробный отчёт core.cleanreport (пишет параллельный агент; нет модуля или
    другая сигнатура -- обходимся своим выводом) + наш вердикт СВЕРХУ: итог читают
    первым, и он должен быть по значениям, а не по счётчику critical_after."""
    try:
        from .cleanreport import render_plain
        body = render_plain(res)
    except Exception:  # noqa: BLE001
        body = None
    if not body:
        return _render_plain(res)
    note = ""
    if res.clean and res.critical_after > 0:
        # cleanreport судит по critical_after и не знает про survived: его «осталось
        # критичных» верно как счёт, но не как вывод об утечке.
        note = ("\nПодробный отчёт ниже оценивает остаток по счётчику критичных находок; "
                "итог выше -- по значениям: остаток состоит из новых значений, не из исходных.\n")
    return "%s\n%s\n%s" % (_render_plain(res, details=False), note, body)


def _exit_code(res) -> int:
    return 1 if not res.ok else (0 if res.clean else 2)


# ============================================================================
#  Самопроверка: PYTHONIOENCODING=utf-8 python -m core.clean --selftest
# ============================================================================

_FAILS = []
_TOTAL = [0]


def _check(name, cond, detail=""):
    _TOTAL[0] += 1
    if not cond:
        _FAILS.append(name)
    print("  [%s] %s%s" % ("ok" if cond else "ПРОВАЛ", name,
                           "  -- " + str(detail) if (detail and not cond) else ""))


def _one(value):
    from .model import Report
    r = Report(path="t", fmt="pdf")
    r.add(Risk.IDENTITY, "x", "x", value)
    return r


def _selftest() -> int:
    global MAX_FILE
    from .model import Report
    from tests import fixtures as fx

    tmp = tempfile.mkdtemp(prefix="clean_selftest_")
    try:
        # 1. survived_values на синтетике: что считается, а что нет.
        print("1. survived_values")
        a = Report(path="x", fmt="pdf")
        a.add(Risk.IDENTITY, "/Info", "Автор", "Иван Петров")
        a.add(Risk.ENVIRONMENT, "/Info", "Путь", "C:\\Users\\ivan\\отчёт.docx")
        a.add(Risk.IDENTITY, "/Info", "Короткое", "Bob")               # <= 5: не считаем
        a.add(Risk.PROVENANCE, "/Info", "Producer", "Microsoft Word 365")  # не IDENTITY/ENV
        a.add(Risk.IDENTITY, "XMP", "Автор2", "иван   петров")        # дубль после нормализации
        b = Report(path="y", fmt="pdf")
        b.add(Risk.STRUCTURAL, "/Info", "Title", "Отчёт ИВАН ПЕТРОВ за квартал")  # любой риск, регистр
        b.add(Risk.PROVENANCE, "/Info", "Producer", "Microsoft Word 365")
        b.add(Risk.IDENTITY, "/Info", "X", "Bob")
        got = survived_values(a, b)
        _check("имя в заголовке найдено, регистр не важен", len(got) == 1 and got[0].label == "Автор", got)
        _check("путь не найден, короткое и PROVENANCE не считаются",
               all(g.label not in ("Путь", "Короткое", "Producer") for g in got))
        _check("пустой результат при чистом выходе", survived_values(a, Report(path="z", fmt="pdf")) == [])
        _check("clean требует и verified, и пустых survived, и отсутствия gaps",
               DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True).clean
               and not DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, gaps=["x"]).clean
               and not DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, survived=[1]).clean
               and not DispatchResult("a", "b", "pdf", "stealth", ok=True).clean
               and not DispatchResult("a", "b", "pdf", "stealth", verified=True).clean)
        _check("граница длины: 6 символов считаются, 5 -- нет",
               len(survived_values(_one("abcdef"), _one("xabcdefx"))) == 1
               and survived_values(_one("abcde"), _one("xabcdex")) == [])

        # 2. Настоящие форматы: маршрутизация, доказательство, вход не тронут.
        print("2. PDF и картинки")
        for i, (maker, prof) in enumerate(((fx.make_pdf, "stealth"), (fx.make_pdf, "paranoid"),
                                           (fx.make_pdf_incremental, "stealth"),
                                           (fx.make_jpeg, "stealth"), (fx.make_png, "paranoid"))):
            d = os.path.join(tmp, "case_%d" % i)
            os.makedirs(d)
            path, _exp = maker(d)
            before = _sha(path)
            out = os.path.join(d, "out", "result.bin")      # каталога нет -- создаётся
            res = clean_file(path, out, prof)
            tag = "%s/%s" % (os.path.basename(path).split(".")[1], prof)
            _check("%s: ok и verified" % tag, res.ok and res.verified, res.errors)
            _check("%s: survived пуст, clean" % tag, res.survived == [] and res.clean, res.survived)
            _check("%s: оракул видел утечки до чистки" % tag, res.critical_before > 0, res.critical_before)
            _check("%s: остаток критичного -- только новые значения" % tag,
                   all(r[2] for r in res.residual), res.residual)
            _check("%s: вход не изменён" % tag, _sha(path) == before)
            _check("%s: dst создан, временных каталогов не осталось" % tag,
                   os.path.isfile(out) and os.listdir(os.path.dirname(out)) == ["result.bin"],
                   os.listdir(os.path.dirname(out)))
            if prof == "stealth" and maker is fx.make_pdf:
                _check("STEALTH-PDF: критичное после = ровно /ID[0] и /ID[1]",
                       sorted(r[1].split(" ")[0] for r in res.residual) == ["/ID[0]", "/ID[1]"], res.residual)
            if maker is fx.make_pdf_incremental:
                _check("инкрементальный PDF: прошлые ревизии сброшены (один %%EOF)",
                       open(out, "rb").read().count(b"%%EOF") == 1)

        # 2b. keep: значение, оставленное по просьбе пользователя, -- не скрываем, а показываем.
        print("2b. keep")
        d = os.path.join(tmp, "keep")
        os.makedirs(d)
        path, exp = fx.make_pdf(d)
        res = clean_file(path, os.path.join(d, "k.pdf"), keep={"/Author"})
        _check("keep: файл выдан, но clean=False и Автор в survived",
               res.ok and res.verified and not res.clean and any(sv.label == "Автор" for sv in res.survived),
               [(sv.label, sv.found_at) for sv in res.survived])

        # 3. Форматы без чистки: честный отказ с номером фазы, dst не создан и не тронут.
        print("3. отказы по форматам")
        d = os.path.join(tmp, "refuse")
        os.makedirs(d)
        # docx/xlsx/pptx убраны из отказов: с фазы 2 они чистятся (см. пункт 3b).
        cases = [(fx.make_rtf, "rtf", 2), (fx.make_odt, "odt", 2),
                 (fx.make_doc, "doc", 4), (fx.make_xls, "xls", 4), (fx.make_ppt, "ppt", 4)]
        for maker, name, phase in cases:
            sub = os.path.join(d, name)
            os.makedirs(sub)
            path, _exp = maker(sub)
            before = _sha(path)
            dst = os.path.join(sub, "keep_me.out")
            with open(dst, "wb") as fh:
                fh.write(b"old")
            res = clean_file(path, dst)
            msg = " ".join(res.errors)
            _check("%s: ok=False, фаза %d названа" % (name, phase),
                   not res.ok and res.planned_phase == phase and ("фаза %d" % phase) in msg, msg)
            _check("%s: сказано, что файл НЕ очищен" % name, "НЕ очищен" in msg, msg)
            _check("%s: вход и существующий dst не тронуты" % name,
                   _sha(path) == before and open(dst, "rb").read() == b"old")
            _check("%s: нет ни доказательства, ни действий" % name,
                   res.critical_after == -1 and not res.actions and not res.clean)
            _check("%s: путь в сообщении не утёк" % name, sub not in msg and os.path.basename(path) not in msg)

        # 4. dst == src при любом написании пути.
        print("4. dst не затирает src")
        path, _exp = fx.make_pdf(os.path.join(tmp, "case_0"))
        before = _sha(path)
        d0 = os.path.dirname(path)
        for variant in (path, os.path.join(d0, ".", os.path.basename(path)),
                        path.upper(), os.path.join(d0, "out", "..", os.path.basename(path))):
            res = clean_file(path, variant)
            _check("отказ: %s" % ("вариант пути"), not res.ok and "совпадает" in " ".join(res.errors), res.errors)
        link = os.path.join(d0, "hard.pdf")
        try:
            os.link(path, link)
            res = clean_file(path, link)
            _check("жёсткая ссылка на вход отклонена", not res.ok and "совпадает" in " ".join(res.errors), res.errors)
        except OSError:
            print("  [--] жёсткие ссылки недоступны, пропущено")
        _check("вход цел после всех попыток", _sha(path) == before)

        # 5. Патологии и защита.
        print("5. патологии")
        for name, p in fx.make_broken(os.path.join(tmp)).items():
            res = clean_file(p, os.path.join(tmp, "br_%s.out" % name))
            _check("%s: не упало, результат корректен" % name,
                   isinstance(res, DispatchResult) and not (res.ok and not res.verified)
                   and (not res.ok or os.path.isfile(res.dst)), res.errors)
        for bad, want in ((os.path.join(tmp, "нет_такого.pdf"), "не найден"), (tmp, "каталог")):
            res = clean_file(bad, os.path.join(tmp, "x.out"))
            _check("%s: отказ с причиной" % want, not res.ok and want in " ".join(res.errors), res.errors)
        res = clean_file("bad" + chr(0) + "path", os.path.join(tmp, "x.out"))
        _check("NUL в пути не роняет", not res.ok)
        path, _exp = fx.make_pdf(os.path.join(tmp, "case_0"))
        res = clean_file(path, os.path.join(tmp, "p.out"), "nonsense")
        _check("неизвестный профиль отклонён", not res.ok and "профиль" in " ".join(res.errors))
        res = clean_file(path, os.path.join(tmp, "p.out"), Profile.REGENERATE)
        _check("REGENERATE -- фаза 4, файла нет", not res.ok and res.planned_phase == 4
               and not os.path.exists(os.path.join(tmp, "p.out")))
        old, MAX_FILE = MAX_FILE, 10
        try:
            res = clean_file(path, os.path.join(tmp, "big.out"))
        finally:
            MAX_FILE = old
        _check("лимит размера", not res.ok and "больше" in " ".join(res.errors), res.errors)

        # 6. verify=False: файл выдан, но чистоту утверждать нельзя.
        print("6. verify=False")
        res = clean_file(path, os.path.join(tmp, "nv.pdf"), verify=False)
        _check("ok, но clean=False и critical_after=-1",
               res.ok and not res.clean and res.critical_after == -1 and not res.verified)

        # 7. Диспетчер не верит чистильщику на слово: подменяем его вредителями.
        print("7. недобросовестный чистильщик")
        saved = dict(_CLEANERS)
        try:
            def liar(s, d, prof, keep, force):         # копирует файл как есть и клянётся, что чисто
                shutil.copyfile(s, d)
                r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
                r.ok = True
                r.act(Act.REMOVED, "/Info", "/Author", "-", "")
                return r
            _CLEANERS["pdf"] = liar
            res = clean_file(path, os.path.join(tmp, "liar.pdf"))
            _check("утечка не убрана: survived не пуст, clean=False",
                   res.ok and res.verified and res.survived and not res.clean, res.survived)

            def boom(s, d, prof, keep, force):
                with open(d, "wb") as fh:
                    fh.write(b"%PDF-1.4 half")
                raise RuntimeError("взрыв")
            _CLEANERS["pdf"] = boom
            dst = os.path.join(tmp, "boom_dir", "boom.pdf")
            res = clean_file(path, dst)
            _check("исключение чистильщика: ok=False, dst нет, мусора нет",
                   not res.ok and not os.path.exists(dst) and os.listdir(os.path.dirname(dst)) == [],
                   os.listdir(os.path.dirname(dst)))

            copy = os.path.join(tmp, "mutating.pdf")
            shutil.copyfile(path, copy)

            def mutator(s, d, prof, keep, force):      # портит ВХОД во время чистки
                r = saved["pdf"](s, d, prof, keep, force)
                with open(s, "ab") as fh:
                    fh.write(b"\n%junk")
                return r
            _CLEANERS["pdf"] = mutator
            dst = os.path.join(tmp, "mut.out")
            res = clean_file(copy, dst)
            _check("вход изменился во время работы: файл не выдан",
                   not res.ok and not os.path.exists(dst) and "изменился" in " ".join(res.errors), res.errors)

            png = fx.make_png(os.path.join(tmp, "o"))[0] if os.makedirs(os.path.join(tmp, "o")) is None else None

            def other_format(s, d, prof, keep, force):  # «чистка» выдала не PDF
                shutil.copyfile(png, d)
                r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
                r.ok = True
                return r
            _CLEANERS["pdf"] = other_format
            res = clean_file(path, os.path.join(tmp, "of.out"))
            _check("результат другого формата не выдаётся", not res.ok and not os.path.exists(os.path.join(tmp, "of.out")),
                   res.errors)
        finally:
            _CLEANERS.clear()
            _CLEANERS.update(saved)

        # 8. Существующий dst заменяется атомарно при успехе.
        print("8. замена существующего dst")
        dst = os.path.join(tmp, "exists.pdf")
        with open(dst, "wb") as fh:
            fh.write(b"old")
        res = clean_file(path, dst)
        _check("при успехе dst заменён", res.ok and open(dst, "rb").read()[:5] == b"%PDF-")
        with open(dst, "rb") as held:                  # dst занят -- замена невозможна
            res = clean_file(path, dst)
            held_ok = held.read(5) == b"%PDF-"
        _check("занятый dst: файл не выдан, без утечки пути и мусора",
               held_ok and not res.ok and "занят" in " ".join(res.errors) and tmp not in " ".join(res.errors)
               and not [n for n in os.listdir(tmp) if n.startswith(".clean_")], res.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nИТОГО: %d проверок, %d провалено" % (_TOTAL[0], len(_FAILS)))
    for f in _FAILS:
        print("  ПРОВАЛ: " + f)
    return 1 if _FAILS else 0


def main(argv=None) -> int:
    # Консоль Windows cp866/cp1251 калечит кириллицу и падает на UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(
        prog="python -m core.clean",
        description="Чистка метаданных документа (фаза 1: PDF и картинки). Вход не меняется.")
    parser.add_argument("file", nargs="?", help="путь к файлу")
    parser.add_argument("--out", help="куда писать результат (по умолчанию рядом, с суффиксом .clean)")
    parser.add_argument("--profile", choices=["stealth", "paranoid"], default="stealth",
                        help="stealth -- не выдаёт обработку (по умолчанию); paranoid -- убрать всё")
    parser.add_argument("--force-signed", action="store_true",
                        help="чистить подписанный PDF, уничтожив подпись")
    parser.add_argument("--json", action="store_true", help="вывести результат в JSON")
    parser.add_argument("--overwrite", action="store_true", help="перезаписать существующий выходной файл")
    parser.add_argument("--selftest", action="store_true", help="самопроверка диспетчера")
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.file:
        parser.error("нужен файл (или --selftest)")

    out = args.out or _default_out(args.file)
    if os.path.exists(out) and not args.overwrite and not _same_file_safe(args.file, out):
        print("Выходной файл уже существует: %s (--overwrite -- перезаписать)" % out)
        return 1
    res = clean_file(args.file, out, args.profile, force_signed=args.force_signed)
    print(_to_json(res) if args.json else _render(res))
    return _exit_code(res)


def _same_file_safe(a, b) -> bool:
    try:
        return _same_file(a, b)
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
