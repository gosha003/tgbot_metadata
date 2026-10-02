"""Рендер Report в человекочитаемый отчёт: Telegram-HTML и plain-text для CLI.

Фаза 0: только инспекция. Отчёт ничего не чистит и не меняет файл -- он лишь
показывает, что в файле лежит, чтобы правила чистки (фазы 2-3) писались на
фактах.

Всё, что пришло из файла (значения, имена частей, тексты ошибок), считается
недоверенным и обязательно проходит через html.escape(): значение поля вполне
может содержать "<b>" или "&" и сломать разбор на стороне Telegram.

Только stdlib.
"""

from __future__ import annotations

import html
import textwrap

from core import sniff
from core.model import RISK_ICON, RISK_ORDER, RISK_TITLE, Risk, clip

__all__ = ["render_telegram", "render_plain", "summary_line", "spoiler", "MASK_VALUES"]

# Лимит одного сообщения Telegram. Запас -- на префикс нумерации частей.
TG_LIMIT = 4096
TG_BUDGET = TG_LIMIT - 64

# Значения из файла в Telegram уходят под спойлер. Защищает от случайного
# просмотра: превью в списке чатов, скриншот, взгляд через плечо, пересланное
# сообщение. НЕ защищает от самого Telegram -- текст всё равно ушёл на его
# серверы и лежит в истории чата. Границу надо называть вслух (это делает
# _tg_footer), а не делать вид, что спойлер её снимает.
# Политика общая на оба рендерера: core/cleanreport.py читает этот же флаг.
MASK_VALUES = True

# Ширина plain-отчёта для CLI.
WIDTH = 100

# Заголовки блока выводов. Порядок словаря = порядок вывода.
SIGNAL_TITLE = {
    "producer": "Чем сделан файл",
    "ai": "Признаки генерации библиотекой или ИИ",
    "dating": "Датировка и геолокация по косвенным признакам",
    "scrubbed": "Признаки предыдущей чистки",
    "inconsistent": "Противоречия в метаданных",
    "hazard": "Активное содержимое",
}

SIGNAL_ICON = {
    "producer": "\U0001F6E0",      # молоток и гаечный ключ
    "ai": "\U0001F916",            # робот
    "dating": "\U0001F4C5",        # календарь
    "scrubbed": "\U0001F9FC",      # мыло
    "inconsistent": "\u2757",      # восклицательный знак
    "hazard": "\u2622",            # знак радиации
}

CONFIDENCE = {"high": "высокая", "medium": "средняя", "low": "низкая"}

# В plain-выводе эмодзи не используем: консоль Windows их калечит.
RISK_MARK = {
    Risk.IDENTITY: "[!!!]",
    Risk.ENVIRONMENT: "[!! ]",
    Risk.PROVENANCE: "[!  ]",
    Risk.STRUCTURAL: "[   ]",
}


# --- мелкие утилиты ------------------------------------------------------


def _esc(value, cap: int = 400) -> str:
    """Недоверенное значение -> безопасный кусок Telegram-HTML.

    Сначала clip() (режет управляющие символы и длину), потом экранирование.
    Порядок важен: экранировать надо уже обрезанное, иначе можно разрубить
    HTML-сущность пополам.
    """
    return html.escape(clip(value, cap), quote=False)


def spoiler(fragment: str) -> str:
    """Готовый HTML-фрагмент -> он же под спойлером Telegram.

    Принимает УЖЕ экранированный HTML, в отличие от _esc(): оборачивать надо
    вместе с <code>, а не внутри него. Telegram не допускает вложенных
    сущностей внутри code и pre, зато внутри spoiler допускает -- поэтому
    порядок именно такой и переставлять его нельзя.

    fragment: кусок HTML (обычно "<code>значение</code>").
    Возврат: тот же кусок в <tg-spoiler>, либо без изменений, если маскировка
    выключена через MASK_VALUES или фрагмент пуст.
    """
    if not MASK_VALUES or not fragment:
        return fragment
    return "<tg-spoiler>%s</tg-spoiler>" % fragment


def _plain(value, cap: int = 400) -> str:
    """То же, но без экранирования -- для CLI."""
    return clip(value, cap)


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование числительного: 1 находка, 2 находки, 5 находок."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _fmt_size(size) -> str:
    """Размер файла в КБ/МБ. Битый размер -- не повод падать."""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return "размер неизвестен"
    if size < 0:
        return "размер неизвестен"
    if size < 1024:
        return "%d Б" % size
    kb = size / 1024.0
    if kb < 1024:
        return "%.1f КБ" % kb
    return "%.1f МБ" % (kb / 1024.0)


def _confidence(value) -> str:
    key = clip(value, 32).lower()
    return CONFIDENCE.get(key, key or "не указана")


def _by_location(items):
    """Сгруппировать находки по location, сохраняя порядок появления."""
    groups = {}
    for f in items:
        groups.setdefault(clip(f.location, 120) or "—", []).append(f)
    return groups.items()


def _other_title(kind: str) -> str:
    """Заголовок для сигнала неизвестного вида: не теряем его, но и не врём."""
    return ("Прочее: " + kind) if kind else "Прочее (вид сигнала не указан)"


def _signal_groups(signals):
    """Сигналы по kind: сперва известные виды в заданном порядке, потом прочие."""
    buckets = {}
    for s in signals:
        buckets.setdefault(clip(s.kind, 40), []).append(s)
    order = [k for k in SIGNAL_TITLE if k in buckets]
    order += [k for k in buckets if k not in SIGNAL_TITLE]
    return [(k, buckets[k]) for k in order]


def _attr(obj, name):
    """Список из Report, которого может не быть или который может бросить.

    Report нам приносит инспектор, и при совсем кривом файле там может
    оказаться что угодно -- вплоть до объекта, падающего на доступе к полю.
    Рендерер на этом падать не имеет права.
    """
    try:
        return getattr(obj, name, None) or []
    except Exception:
        return []


def _fmt_name(report) -> str:
    """Человекочитаемое имя формата. Пустой fmt -- тоже формат, неопознанный."""
    try:
        fmt = clip(getattr(report, "fmt", "") or "", 40)
    except Exception:
        fmt = ""
    return sniff.pretty(fmt or "unknown")


def _counts(report):
    """(всего, требуют чистки, выдают личность или машину).

    Два разных числа, и путать их нельзя. sensitive -- объём работы для
    фазы чистки (включая провенанс). critical -- то, насколько всё плохо:
    у реального PDF бывает 137 «требующих чистки» при 7 настоящих утечках,
    остальное -- список шрифтов по страницам и версия формата.
    """
    total = len(_attr(report, "findings"))
    try:
        sensitive = int(report.sensitive)
    except Exception:
        # Метрика не посчиталась -- честнее показать всё как требующее
        # чистки, чем занизить: недооценка утечки опаснее переоценки.
        sensitive = total
    try:
        critical = int(report.critical)
    except Exception:
        critical = sensitive
    return total, sensitive, critical


def _safe(build, report):
    """Собрать секции, не утащив за собой весь отчёт.

    Одна битая находка -- это строка об ошибке на её месте, а не пустое
    сообщение вместо всего отчёта: шапка, выводы и остальные категории
    обязаны доехать до пользователя.
    """
    try:
        return build(report)
    except Exception as exc:
        return [["⚠ <i>секция отчёта не собралась: %s</i>" % _esc(repr(exc), 200)]]


# --- нарезка на сообщения Telegram ---------------------------------------


def _tg_len(s: str) -> int:
    """Длина строки в UTF-16 code units -- как её считает лимит Telegram,
    а не Python len() (число code points). Для символов вне базовой
    плоскости Unicode (эмодзи, редкие иероглифы) это разные числа: один
    такой символ -- 2 unit, а не 1. На значениях из метаданных, набитых
    такими символами, разница вдвое раздувает реальную длину сообщения,
    и его может отвергнуть Telegram, хоть Python-len() и укладывался в лимит.
    """
    return len(s.encode("utf-16-le")) // 2


def _pack(sections, limit: int = TG_BUDGET):
    """Секции -> список сообщений, каждое не длиннее limit (в UTF-16 unit).

    Рвём сперва по границам секций, а если одна секция не влезает целиком --
    по границам отдельных строк (то есть находок). Строка всегда содержит
    сбалансированные теги, поэтому HTML посередине не рвётся никогда.
    """
    buf = []
    msgs = []

    def cur_len():
        return sum(_tg_len(x) + 1 for x in buf) - 1 if buf else 0

    def flush():
        if buf:
            msgs.append("\n".join(buf))
            del buf[:]

    for sec in sections:
        if not sec:
            continue
        sec_len = sum(_tg_len(x) + 1 for x in sec) - 1
        if sec_len + 1 <= limit:
            if buf and cur_len() + 1 + sec_len + 1 > limit:
                flush()
            if buf:
                buf.append("")
            buf.extend(sec)
            continue
        # Секция не влезает целиком. Дозаполняем текущее сообщение её
        # строками, а не выбрасываем его недобитым -- иначе получаются
        # сообщения в три строки.
        if buf:
            # Разделительная пустая строка сама по себе +1 к длине. Если
            # буфер уже забит вплотную (ровно до limit -- это возможно,
            # см. инвариант ниже), её добавление без проверки один раз
            # переполняло итоговое сообщение на 1 unit. Та же проверка,
            # что и в ветке "секция целиком" выше.
            if cur_len() + 1 > limit:
                flush()
            else:
                buf.append("")
        for line in sec:
            # Страховка: по построению строки короткие (все значения идут
            # через _esc с лимитом), но если кто-то соберёт Report в обход
            # model.clip -- рвать HTML-тег нельзя. Пропуск делаем видимым:
            # молча потерянная находка в отчёте об утечках хуже, чем явная
            # отметка "смотрите текстовый отчёт".
            if _tg_len(line) > limit:
                line = ("• <i>(строка не влезла в одно сообщение Telegram "
                        "и пропущена — смотрите текстовый отчёт)</i>")
            if buf and cur_len() + 1 + _tg_len(line) > limit:
                flush()
            buf.append(line)
    flush()
    return msgs


# --- Telegram ------------------------------------------------------------


def _tg_header(report):
    total, sensitive, critical = _counts(report)
    fmt = _esc(_fmt_name(report), 80)
    line = "\U0001F4C4 <b>%s</b> · %s" % (fmt, _esc(_fmt_size(getattr(report, "size", 0)), 40))
    stats = "Находок: <b>%d</b>, из них требуют чистки: <b>%d</b>" % (total, sensitive)
    if critical:
        stats += "\n\U0001F534 Прямо выдают личность или машину: <b>%d</b>" % critical
    return [line, stats]


def _tg_nothing(report):
    """Пустой результат означает разное: либо файл действительно чист, либо
    мы его просто не прочитали. Путать эти два случая нельзя: на втором
    пользователь решит, что файл безопасен, а он всего лишь не проверен."""
    if _attr(report, "errors"):
        return _tg_unread()
    return _tg_clean()


def _tg_unread():
    """Разбор не дошёл до конца: отсутствие находок здесь ничего не доказывает."""
    return [
        "⚠ <b>Метаданные не извлечены.</b>",
        "Разбор дошёл не до конца (ошибки ниже), поэтому пустой результат "
        "здесь <b>не</b> значит, что файл чист: в непрочитанных частях может "
        "лежать что угодно. Судить о чистоте файла по такому отчёту нельзя.",
    ]


def _tg_clean():
    return [
        "\u2139 <b>Явных метаданных не обнаружено.</b>",
        "Это не обязательно хорошая новость: чистый файл сам по себе "
        "является признаком того, что его уже обрабатывали. "
        "Штатно созданные документы почти всегда несут хотя бы приложение и даты.",
    ]


def _tg_signals(report):
    signals = _attr(report, "signals")
    if not signals:
        return []
    out = [["\U0001F50E <b>ВЫВОДЫ</b>"]]
    for kind, items in _signal_groups(signals):
        title = SIGNAL_TITLE.get(kind) or _other_title(kind)
        lines = ["%s <b>%s</b>" % (SIGNAL_ICON.get(kind, "\u2022"), _esc(title, 120))]
        for s in items:
            lines.append(
                "• %s <i>(уверенность: %s)</i>"
                % (_esc(s.detail, 600), _esc(_confidence(s.confidence), 32))
            )
        out.append(lines)
    return out


def _tg_finding(f):
    # Пустое значение под спойлер не прячем: скрывать нечего, а лишний тап
    # только мешает читать отчёт.
    value = ("<i>(пусто)</i>" if f.empty
             else spoiler("<code>%s</code>" % _esc(f.value)))
    line = "• %s: %s" % (_esc(f.label, 120), value)
    note = _esc(f.note, 160)
    if f.removable:
        if note:
            line += " <i>— %s</i>" % note
    else:
        line += "\n   \U0001F512 убрать нельзя — %s" % (note or "потеряется содержимое файла")
    return line


def _tg_findings(report):
    out = []
    by_risk = report.by_risk()
    for risk in RISK_ORDER:
        items = by_risk.get(risk) or []
        if not items:
            continue
        lines = [
            "%s <b>%s</b> — %d"
            % (RISK_ICON[risk], _esc(RISK_TITLE[risk], 80), len(items))
        ]
        for location, group in _by_location(items):
            lines.append("<i>%s</i>" % _esc(location, 120))
            for f in group:
                lines.append(_tg_finding(f))
        out.append(lines)
    return out


def _tg_errors(report):
    errors = _attr(report, "errors")
    if not errors:
        return []
    lines = [
        "\u26A0 <b>Не удалось разобрать</b> — %d" % len(errors),
        "Это части файла, которые не прочитались. "
        "Не путать с отсутствием метаданных: там может лежать что угодно.",
    ]
    for e in errors:
        lines.append("• <code>%s</code>" % _esc(e, 600))
    return [lines]


def _tg_footer(report=None):
    # "Файл прочитан" -- неправда для fmt == "unreadable" (облачный плейсхолдер,
    # нет прав, битый путь): такой файл как раз НЕ прочитан, и утверждать
    # обратное в отчёте о приватности нельзя. Про неизменность говорим всегда:
    # фаза инспекции не пишет в файл ни при каком исходе.
    try:
        read_ok = report is None or (getattr(report, "fmt", "") or "") != "unreadable"
    except Exception:
        read_ok = True
    first = ("Это фаза инспекции: файл прочитан и не изменён."
             if read_ok else
             "Это фаза инспекции. Файл не изменён, но и прочитать его не удалось.")
    lines = ["———", "<i>%s</i>" % first]
    if MASK_VALUES:
        # Честная граница: спойлер прячет значение от взгляда, но не от
        # Telegram. Промолчать об этом в инструменте приватности нельзя --
        # пользователь решит, что значения никуда не уходили.
        lines.append(
            "<i>Значения скрыты — нажмите, чтобы показать. Но текст отчёта уже "
            "ушёл на серверы Telegram и лежит в истории чата: если находки "
            "чувствительные, удалите переписку, а разбор делайте через CLI.</i>")
    return lines


def render_telegram(report) -> list:
    """Report -> список сообщений, готовых к отправке с parse_mode=\"HTML\".

    Используются только теги, разрешённые Telegram: b, i, code. Переводы
    строк -- обычный \\n, никаких <br>.
    """
    # Каждая секция собирается отдельно: одна битая находка не должна уносить
    # с собой шапку, выводы, ошибки разбора и остальные категории.
    sections = _safe(lambda r: [_tg_header(r)], report)
    if not _attr(report, "findings") and not _attr(report, "signals"):
        sections += _safe(lambda r: [_tg_nothing(r)], report)
    else:
        sections += _safe(_tg_signals, report)
        sections += _safe(_tg_findings, report)
    sections += _safe(_tg_errors, report)
    sections.append(_tg_footer(report))
    try:
        parts = _pack(sections)
    except Exception as exc:  # отчёт не имеет права уронить бота
        return ["⚠ Не удалось собрать отчёт: <code>%s</code>" % _esc(repr(exc), 300)]
    if not parts:
        return ["ℹ Отчёт пуст."]
    if len(parts) > 1:
        total = len(parts)
        parts = [
            "<b>[%d/%d]</b>\n%s" % (i, total, p) for i, p in enumerate(parts, 1)
        ]
    return parts


# --- plain text ----------------------------------------------------------


def _fill(text, indent="", extra="  "):
    """Перенос длинного значения по ширине WIDTH. Содержимое не режем."""
    if not text:
        return indent.rstrip()
    return textwrap.fill(
        text,
        width=WIDTH,
        initial_indent=indent,
        subsequent_indent=indent + extra,
        break_long_words=True,
        break_on_hyphens=False,
    )


def render_plain(report) -> str:
    """То же содержимое без HTML, с отступами -- для CLI и логов."""
    try:
        return _render_plain(report)
    except Exception as exc:
        return "Не удалось собрать отчёт: %s" % repr(exc)


def _plain_signals(report):
    """Блок ВЫВОДОВ в plain-виде."""
    signals = _attr(report, "signals")
    if not signals:
        return []
    out = ["", "ВЫВОДЫ", "-" * WIDTH]
    for kind, items in _signal_groups(signals):
        title = SIGNAL_TITLE.get(kind) or _other_title(kind)
        out.append("  %s" % _plain(title, 120))
        for s in items:
            out.append(
                _fill(
                    "- %s (уверенность: %s)"
                    % (_plain(s.detail, 600), _confidence(s.confidence)),
                    "    ",
                )
            )
    return out


def _plain_findings(report):
    """Сырые находки в plain-виде: по категориям, внутри -- по location."""
    if not _attr(report, "findings"):
        return []
    out = []
    by_risk = report.by_risk()
    for risk in RISK_ORDER:
        items = by_risk.get(risk) or []
        if not items:
            continue
        out.append("")
        out.append("%s %s — %d" % (RISK_MARK[risk], RISK_TITLE[risk], len(items)))
        out.append("-" * WIDTH)
        for location, group in _by_location(items):
            out.append("  %s" % _plain(location, 120))
            for f in group:
                value = "(пусто)" if f.empty else _plain(f.value)
                out.append(_fill("- %s: %s" % (_plain(f.label, 120), value), "    "))
                note = _plain(f.note, 160)
                if not f.removable:
                    out.append(
                        _fill(
                            "! убрать нельзя: %s"
                            % (note or "потеряется содержимое файла"),
                            "      ",
                        )
                    )
                elif note:
                    out.append(_fill("%s" % note, "      "))
    return out


def _safe_lines(build, report):
    """То же, что _safe, но для plain: секция -> плоский список строк."""
    try:
        return build(report)
    except Exception as exc:
        return ["", "  ! секция отчёта не собралась: %r" % (exc,)]


def _render_plain(report):
    total, sensitive, critical = _counts(report)
    signals = _attr(report, "signals")
    findings = _attr(report, "findings")
    errors = _attr(report, "errors")

    rule = "=" * WIDTH
    out = [rule, "ОТЧЁТ ОБ ИНСПЕКЦИИ МЕТАДАННЫХ", rule]
    out.append("Формат:  %s" % _plain(_fmt_name(report), 80))
    out.append("Размер:  %s" % _fmt_size(getattr(report, "size", 0)))
    out.append("Находок: %d, из них требуют чистки: %d" % (total, sensitive))
    if critical:
        out.append("ВЫДАЮТ ЛИЧНОСТЬ ИЛИ МАШИНУ: %d" % critical)

    if not findings and not signals:
        out.append("")
        if errors:
            # Отсутствие находок при ошибках разбора -- не чистый файл,
            # а непроверенный. Формулировка обязана это различать.
            out.append("Метаданные не извлечены.")
            out.append(
                _fill(
                    "Внимание: разбор дошёл не до конца (см. ошибки ниже), поэтому "
                    "пустой результат НЕ означает, что файл чист. В непрочитанных "
                    "частях может лежать что угодно; судить о чистоте файла по "
                    "такому отчёту нельзя.",
                    "  ",
                    "",
                )
            )
        else:
            out.append("Явных метаданных не обнаружено.")
            out.append(
                _fill(
                    "Внимание: отсутствие метаданных само по себе является признаком "
                    "предыдущей обработки файла. Штатно созданные документы почти всегда "
                    "несут хотя бы приложение-генератор и даты.",
                    "  ",
                    "",
                )
            )
    else:
        out.extend(_safe_lines(_plain_signals, report))
        out.extend(_safe_lines(_plain_findings, report))

    if errors:
        out.append("")
        out.append("НЕ УДАЛОСЬ РАЗОБРАТЬ — %d" % len(errors))
        out.append("-" * WIDTH)
        out.append(
            _fill(
                "Это части файла, которые не прочитались. Не путать с отсутствием "
                "метаданных: там может лежать что угодно.",
                "  ",
                "",
            )
        )
        for e in errors:
            out.append(_fill("- %s" % _plain(e, 600), "    "))

    out.append("")
    out.append(rule)
    out.append("Фаза инспекции: файл прочитан и не изменён."
               if (getattr(report, "fmt", "") or "") != "unreadable"
               else "Фаза инспекции. Файл не изменён, но прочитать его не удалось.")
    out.append("Чистка -- отдельная команда: python -m core.clean <файл>.")
    out.append(rule)
    return "\n".join(out)


# --- одна строка ---------------------------------------------------------


def summary_line(report) -> str:
    """Краткий ответ и строка для логов.

    "PDF, 12 находок (3 критичных), сделан через Skia/PDF — печать из Chrome".
    "Критичных" здесь = report.critical (IDENTITY + ENVIRONMENT), а НЕ
    report.sensitive: провенанса бывают сотни строк, и он утопил бы
    настоящие утечки. Объём работы для чистки стоит в шапке отчёта.
    """
    try:
        fmt = _plain(_fmt_name(report), 80)
        total, sensitive, critical = _counts(report)
        signals = _attr(report, "signals")
        errors = _attr(report, "errors")
        err_part = "%d %s разбора" % (
            len(errors), _plural(len(errors), "ошибка", "ошибки", "ошибок")
        )
        if not total and not signals:
            # Без находок формулировка зависит от того, читался ли файл.
            # "Метаданных не обнаружено" про нечитаемый файл -- ложь в логе.
            if errors:
                return "%s, метаданные не извлечены, %s" % (fmt, err_part)
            return "%s, метаданных не обнаружено" % fmt
        parts = [
            fmt,
            "%d %s (%d %s)"
            % (
                total,
                _plural(total, "находка", "находки", "находок"),
                critical,
                _plural(critical, "критичная", "критичных", "критичных"),
            ),
        ]
        # "Сделан через" допустимо говорить только про настоящего продюсера
        # (kind="producer") или про библиотеку/ИИ (kind="ai"). Сигналы вида
        # "dating" -- шрифт, формат бумаги -- датируют документ, но ничего
        # не говорят о приложении: раньше сюда попадал Calibri и сводка
        # выдавала "PDF, сделан через Calibri".
        def _first(kind):
            return next(
                (s for s in signals if clip(getattr(s, "kind", ""), 40) == kind), None
            )

        made_by = _first("producer") or _first("ai")
        if made_by is not None:
            parts.append("сделан через %s" % _plain(getattr(made_by, "detail", ""), 120))
        else:
            dating = _first("dating")
            if dating is not None:
                parts.append("продюсер не опознан; %s"
                             % _plain(getattr(dating, "detail", ""), 120))
        if errors:
            parts.append(err_part)
        return ", ".join(parts)
    except Exception as exc:
        return "отчёт не собрался: %r" % (exc,)


# --- самопроверка --------------------------------------------------------


def _demo_report():
    """Report со всеми четырьмя категориями, пустым значением, removable=False,
    сигналами всех видов, HTML-инъекцией в значении и ошибкой разбора."""
    from core.model import Report

    r = Report(path="demo.docx", fmt="docx", size=1536 * 1024)
    r.add(Risk.IDENTITY, "docProps/core.xml", "Автор", "Иванов Иван <ivan@corp.ru>",
          note="ФИО и почта владельца лицензии")
    r.add(Risk.IDENTITY, "docProps/core.xml", "Кем изменён", "")
    r.add(Risk.IDENTITY, "word/_rels", "Цифровая подпись", "CN=Иванов И.И., O=ООО Ромашка",
          note="удаление сломает подпись", removable=False)
    r.add(Risk.ENVIRONMENT, "word/settings.xml", "Путь к шаблону",
          r"C:\Users\ivanov\AppData\Roaming\Microsoft\Шаблоны\<script>&Normal.dotm")
    r.add(Risk.ENVIRONMENT, "docProps/app.xml", "Принтер", "\\\\srv-print01\\HP-3-этаж")
    r.add(Risk.PROVENANCE, "docProps/app.xml", "Приложение", "Microsoft Office Word 16.0")
    r.add(Risk.PROVENANCE, "docProps/app.xml", "TotalTime", "417",
          note="минут чистого редактирования")
    r.add(Risk.STRUCTURAL, "word/styles.xml", "Язык документа", "ru-RU",
          note="при чистке сохраняем")
    r.signal("producer", "Microsoft Word 16.0 (Office 365)", "high")
    r.signal("ai", "порядок частей ZIP характерен для python-docx", "low")
    r.signal("scrubbed", "dc:creator пуст при заполненном cp:lastModifiedBy", "medium")
    r.signal("inconsistent", "дата создания позже даты изменения", "high")
    r.signal("hazard", "vbaProject.bin: документ содержит макросы", "high")
    r.err("word/embeddings/oleObject1.bin: не удалось открыть OLE-поток")
    return r


def _demo():
    from core.model import Report

    global MASK_VALUES          # самопроверка выключателя маскировки, см. ниже

    r = _demo_report()

    msgs = render_telegram(r)
    for m in msgs:
        # Реальный лимит Telegram -- в UTF-16 code units, не в Python len().
        assert _tg_len(m) <= TG_LIMIT, _tg_len(m)
    joined = "\n".join(msgs)
    assert "&lt;script&gt;&amp;" in joined, "значение не экранировано"
    assert "<script>" not in joined, "сырой тег утёк в Telegram-HTML"
    assert "<i>(пусто)</i>" in joined
    assert "убрать нельзя" in joined

    # Маскировка значений. Считаем спойлеры поштучно, а не ищем один тег:
    # пропущенная площадка рендера иначе не видна -- в отчёте и так есть
    # спойлеры от других находок, и проверка становится вакуумной.
    assert MASK_VALUES, "по умолчанию маскировка обязана быть включена"
    n_values = sum(1 for f in r.findings if not f.empty)
    assert joined.count("<tg-spoiler><code>") == n_values, (
        "спойлеров %d, а непустых значений %d"
        % (joined.count("<tg-spoiler><code>"), n_values))
    assert "<tg-spoiler><i>(пусто)</i>" not in joined, (
        "пустое значение прятать не надо -- скрывать нечего")
    assert "ушёл на серверы Telegram" in joined, (
        "оговорка про хранение в Telegram обязательна: спойлер прячет "
        "значение от взгляда, но не от Telegram")
    # Выключатель обязан действительно выключать: иначе рендер без маскировки
    # проверить нечем.
    MASK_VALUES = False
    try:
        bare = "\n".join(render_telegram(r))
        assert "tg-spoiler" not in bare, "MASK_VALUES=False не отключил спойлер"
        assert "<code>" in bare, "без маскировки значение обязано остаться в <code>"
    finally:
        MASK_VALUES = True

    print("=== render_telegram: %d сообщение(й) ===" % len(msgs))
    for i, m in enumerate(msgs, 1):
        print("--- сообщение %d, %d символов ---" % (i, len(m)))
        print(m)

    print()
    print("=== render_plain ===")
    plain = render_plain(r)
    assert "<code>" not in plain and "&amp;" not in plain
    print(plain)

    print()
    print("=== summary_line ===")
    print(summary_line(r))

    # пустой отчёт
    empty = Report(path="clean.pdf", fmt="pdf", size=2048)
    print()
    print("=== пустой отчёт ===")
    print(render_telegram(empty)[0])
    print(summary_line(empty))

    # нагрузка: 500 находок должны разбиться и влезть в лимит
    big = Report(path="big.pdf", fmt="pdf", size=99 * 1024 * 1024)
    for i in range(500):
        big.add(Risk.PROVENANCE, "/Info", "Поле %d" % i, "значение <%d> & прочее" % i)
    big_msgs = render_telegram(big)
    sizes = [_tg_len(m) for m in big_msgs]
    assert all(s <= TG_LIMIT for s in sizes), sizes
    assert len(big_msgs) > 1
    print()
    print("=== 500 находок: %d сообщений, длины %s ===" % (len(big_msgs), sizes))
    print(summary_line(big))
    print("макс. длина сообщения: %d (лимит %d)" % (max(sizes), TG_LIMIT))

    # Регрессия для _pack(): буфер, забитый РОВНО до лимита одной строкой,
    # за которым идёт секция, не влезающая целиком. Пустая строка-разделитель
    # перед такой секцией раньше добавлялась без проверки и могла раздуть уже
    # набранное сообщение на 1 unit сверх лимита.
    overflow_secs = [["A" * TG_BUDGET], ["x" * (TG_BUDGET + 1), "y"]]
    overflow_msgs = _pack(overflow_secs)
    assert all(_tg_len(m) <= TG_BUDGET for m in overflow_msgs), [
        _tg_len(m) for m in overflow_msgs
    ]

    # Символы вне базовой плоскости Unicode (эмодзи и т.п.) в UTF-16 -- это
    # 2 code unit на символ, а не 1. Python len() их недосчитывает вдвое;
    # если пересчёт лимита забыт, пакер выпустит сообщение, которое сам
    # Telegram отклонит как слишком длинное, хоть Python и посчитал его
    # укладывающимся. Проверяем именно это, а не просто Python-длину.
    astral = Report(path="astral.pdf", fmt="pdf", size=10)
    for i in range(9):
        astral.add(Risk.PROVENANCE, "/Info", "Поле %d" % i, "\U0001F600" * 400)
    astral_msgs = render_telegram(astral)
    astral_sizes = [_tg_len(m) for m in astral_msgs]
    assert all(s <= TG_LIMIT for s in astral_sizes), astral_sizes
    print()
    print("=== эмодзи вне BMP: %d сообщений, UTF-16 длины %s ===" % (
        len(astral_msgs), astral_sizes))

    # Битый файл: находок нет, но есть ошибки разбора. Отчёт обязан сказать
    # "не прочитали", а не "файл уже чистили": иначе пользователь решит, что
    # непроверенный файл безопасен.
    broken = Report(path="trunc.pdf", fmt="pdf", size=812)
    broken.err("обрезанный xref: pikepdf не открыл файл")
    broken.err("/Info: объект не найден")
    tg_broken = render_telegram(broken)
    pl_broken = render_plain(broken)
    assert any("не извлечены" in m for m in tg_broken), tg_broken
    assert "не извлечены" in pl_broken
    assert not any("уже обрабатывали" in m for m in tg_broken)
    assert "предыдущей обработки" not in pl_broken
    assert "2 ошибки разбора" in summary_line(broken), summary_line(broken)
    print()
    print("=== битый файл: находок нет, но есть 2 ошибки разбора ===")
    for m in tg_broken:
        print(m)
    print(summary_line(broken))

    # Одна битая находка не имеет права унести весь отчёт: шапка, выводы и
    # ошибки разбора обязаны доехать до пользователя.
    class _BrokenFinding:
        def __getattr__(self, name):
            raise RuntimeError("битая находка")

    partial = _demo_report()
    partial.findings.append(_BrokenFinding())
    tg_partial = render_telegram(partial)
    blob = " ".join(tg_partial)
    assert "Word (OOXML)" in blob                     # шапка выжила
    assert "ВЫВОДЫ" in blob                         # выводы выжили
    assert "Не удалось разобрать" in blob            # ошибки разбора выжили
    assert "не собралась" in blob                  # и сама поломка видна
    assert "не собралась" in render_plain(partial)
    print()
    print("=== одна битая находка: отчёт деградирует посекционно ===")
    for m in tg_partial:
        print(m)

    # Согласование числительных в единственном числе.
    one = Report(path="one.pdf", fmt="pdf", size=10)
    one.add(Risk.IDENTITY, "/Info", "Автор", "Иванов")
    assert "1 находка (1 критичная)" in summary_line(one), summary_line(one)
    print()
    print("=== summary_line, единственное число ===")
    print(summary_line(one))

    print("ОК")


if __name__ == "__main__":
    _demo()
