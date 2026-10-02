# -*- coding: utf-8 -*-
"""Телеграм-оболочка: инспекция (фаза 0), чистка PDF и картинок (фаза 1),
чистка OOXML (фаза 2).

Инспекция файл не меняет. Чистка -- только по явному нажатию кнопки и только
для форматов, у которых чистильщик есть (cleanreport.CLEANABLE: PDF, JPEG, PNG,
WebP, GIF, docx, xlsx, pptx). Для остальных бот прямо пишет, что файл НЕ
почищен и когда чистка появится: отдать файл, который пользователь считает
почищенным, а он не почищен, -- худший исход для этого проекта.

Поток чистки: отчёт об инспекции -> сообщение с профилями и кнопками (в памяти
процесса запоминается только file_id, а не сам файл) -> нажатие -> файл
скачивается заново, clean_file() в потоке с таймаутом -> два результата:
отчёт о чистке (cleanreport) и сам файл ДОКУМЕНТОМ с нейтральным именем.
При ok=False файл не отправляется никогда.

Ядро (core/inspect.py, core/clean.py) может временно отсутствовать -- импорт
бота при этом не должен падать (см. try/except ниже). Самопроверка без токена:
    PYTHONIOENCODING=utf-8 python bot.py --selftest

ПРИВАТНОСТЬ. Это инструмент приватности, и его собственные логи --
потенциальная утечка того же типа, для защиты от которой он написан.
Поэтому в логи попадают ТОЛЬКО: формат файла, его размер, число находок или
действий, профиль, исход и длительность. Никогда -- имя файла, его
содержимое, содержимое находок или текст исключения (в нём бывают значения
из файла).
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    Document,
    FSInputFile,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from core import cleanreport
from core.cleanmodel import CleanResult, Profile
from core.model import clip
from core.report import render_telegram

try:
    from core.inspect import inspect_file
except ImportError:
    # Контракт ещё не готов у соседнего агента -- бот должен оставаться
    # импортируемым и рабочим (кроме собственно разбора) без него.
    inspect_file = None

try:
    from core.clean import clean_file
except ImportError:
    # Нет модуля чистки или его зависимости (pikepdf): бот остаётся
    # инспектором и честно пишет, что чистки нет -- кнопок не показывает.
    clean_file = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tgbot_metadata")

router = Router(name="metadata")

# Инспекция и чистка блокирующие и могут быть долгими; бот личный -- одна
# задача за раз, без очередей и метрик.
_SEMAPHORE = asyncio.Semaphore(1)
_INSPECT_TIMEOUT = 120  # секунд, см. _in_thread ниже
# clean_file = чистка + две инспекции (до и после) + две сверки sha256,
# поэтому потолок втрое выше, чем у одной инспекции.
_CLEAN_TIMEOUT = 360

# ponytail: состояние кнопок живёт в памяти процесса и пропадает при
# перезапуске бота (нажатие после рестарта = "кнопка устарела"). Для
# однопользовательского бота достаточно; БД/Redis -- если появится
# многопользовательский режим.
_PENDING_TTL = 30 * 60  # секунд
_PENDING_MAX = 50       # записей; старейшие вытесняются

# ponytail: грубая эвристика "похоже на авто-имя" по алфавиту символов
# (буквы/цифры/подчёркивание/дефис), а не по смыслу слова. Усложнять до
# словаря типичных личных имён -- только если эвристика реально подведёт.
_NEUTRAL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$")

_PROFILES = {"cl:stealth": Profile.STEALTH, "cl:paranoid": Profile.PARANOID}
_EXT = {"pdf": ".pdf", "jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif",
        "docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx"}
# Основа нейтрального имени: родовое слово по типу файла, без картинки как
# умолчания -- иначе docx уехал бы с именем image_*.docx.
_STEM = {"pdf": "document", "docx": "document", "xlsx": "spreadsheet",
         "pptx": "presentation"}

HELP_TEXT = (
    "Это инспектор и чистильщик метаданных документов.\n\n"
    "Пришлите документ (docx/xlsx/pptx, PDF, doc/xls/ppt, ODF, RTF, "
    "картинку) -- бот покажет, что сейчас лежит внутри: автор, приложение, "
    "даты, локальные пути, GPS в фото и т.п. Файл при этом <b>не меняется "
    "и не сохраняется</b>.\n\n"
    "Для <b>PDF, картинок (JPEG, PNG, WebP, GIF) и docx/xlsx/pptx</b> после "
    "отчёта появятся кнопки чистки: <b>Stealth</b> (убирает личность и "
    "окружение, оставляет приложение и версию) или <b>Paranoid</b> (убирает "
    "всё, но результат выглядит обработанным). Бот пришлёт отчёт о чистке и "
    "новую копию файла документом, с нейтральным именем.\n\n"
    "У docx/xlsx/pptx полной незаметности не даёт даже Stealth: пустой автор "
    "сам по себе признак чистки. Бот честно пишет это в отчёте, а не "
    "умалчивает.\n\n"
    "Для остальных форматов (doc/xls/ppt, ODF, RTF) чистки <b>пока нет</b>: "
    "бот так и напишет и укажет, в какой фазе она запланирована. Такой файл "
    "он не меняет.\n\n"
    "Главное условие: отправляйте файл через скрепку → <b>Файл</b> "
    "(Document), а не как «Фото». Если отправить картинку как фото, "
    "Telegram пережмёт её раньше, чем бот увидит файл, и метаданные "
    "будут уже потеряны -- смотреть будет не на чём."
)

CHOICE_TEXT = (
    "\U0001F9F9 <b>Почистить файл?</b> Исходный файл не изменится: бот пришлёт "
    "новую копию (документом, не фото -- иначе Telegram пережмёт её) и отчёт "
    "о чистке.\n\n"
    "• <b>Stealth</b> (рекомендуется) -- убирает личность и окружение: автора, "
    "пути, имена принтеров, GPS, серийники. Приложение и его версию оставляет: "
    "пустые метаданные сами выдают, что файл чистили.\n"
    "• <b>Paranoid</b> -- убирает всё, включая приложение и даты. Чище, но "
    "результат выглядит обработанным."
)


def _allowed(message: Message | CallbackQuery) -> bool:
    """Пустой ALLOWED_USER_IDS = доступ всем, иначе -- только из списка."""
    if not config.ALLOWED_USER_IDS:
        return True
    user = message.from_user
    return bool(user) and user.id in config.ALLOWED_USER_IDS


def _looks_neutral(filename: str) -> bool:
    """Имя файла тоже метаданные. "Нейтральное" -- похожее на авто-имя."""
    stem = os.path.splitext(filename)[0] if filename else ""
    if not stem:
        return True
    return bool(_NEUTRAL_NAME_RE.match(stem))


def _neutral_name(fmt: str) -> str:
    """Имя выходного файла: не содержит ничего от исходного (имя -- метаданные,
    Telegram его сохраняет). Случайный хвост -- чтобы файлы не склеивались."""
    return "%s_%s%s" % (_STEM.get(fmt, "image"), secrets.token_hex(3),
                        _EXT.get(fmt, ".bin"))


# ============================================================================
#  Блокирующая работа в потоке
# ============================================================================


class _Timeout(Exception):
    """Поток не уложился в срок. Каталог передан потоку на самоуборку."""


def _rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


async def _in_thread(tmp_dir: str, timeout: float, func, *args):
    """func(*args) в потоке с таймаутом. Поток прервать нельзя, он дорабатывает.
    Поэтому при таймауте каталог удаляется не сразу, а когда поток закончит:
    иначе на Windows занятый файл не удалился бы и входной документ навсегда
    остался бы на диске. Вызывающий после _Timeout каталог НЕ трогает."""
    fut = asyncio.ensure_future(asyncio.to_thread(func, *args))
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout)
    except asyncio.TimeoutError:
        def _cleanup(f):
            if not f.cancelled():
                f.exception()  # забрать исключение, чтобы asyncio не ругался
            _rmtree(tmp_dir)

        fut.add_done_callback(_cleanup)
        raise _Timeout from None


async def _quiet(coro) -> None:
    """Косметика (удалить статус, ответить на нажатие) не вправе ронять обработчик."""
    try:
        await coro
    except Exception:
        pass


async def _say(msg, text: str) -> None:
    """Заменить текст сообщения; если не вышло -- написать новым. Для сообщений
    об исходе: пользователь обязан их увидеть."""
    try:
        await msg.edit_text(text)
    except Exception:
        await _quiet(msg.answer(text))


# ============================================================================
#  Состояние кнопок: (chat_id, message_id) -> что чистить
# ============================================================================


@dataclass
class _Pending:
    file_id: str        # сам файл НЕ храним: скачаем заново при нажатии
    fmt: str
    user_id: int | None
    expires: float


_pending: "OrderedDict[tuple, _Pending]" = OrderedDict()


def _purge() -> None:
    """TTL одинаковый, записи идут по времени -- просроченные всегда в начале."""
    now = time.monotonic()
    while _pending and next(iter(_pending.values())).expires <= now:
        _pending.popitem(last=False)


def _remember(key: tuple, item: _Pending) -> None:
    _purge()
    while len(_pending) >= _PENDING_MAX:
        _pending.popitem(last=False)
    _pending[key] = item


# ============================================================================
#  Вердикт диспетчера и тексты
# ============================================================================


def _e(value, cap: int = 40) -> str:
    """Недоверенное значение -> безопасный HTML. Лимит 40 держит худший случай
    (каждый символ '&' -> 5 знаков) в пределах одного сообщения Telegram."""
    return html.escape(clip(value, cap), quote=False)


def _verdict(res) -> tuple:
    """(уровень, HTML): вердикт ДИСПЕТЧЕРА по значениям. Отчёт cleanreport судит
    по счётчику critical_after и про survived не знает: он может написать «ЧИСТО»
    там, где исходное имя автора пережило чистку внутри заголовка. Поэтому при
    любых сомнениях бот говорит сам и ставит это ПЕРЕД отчётом."""
    survived = list(getattr(res, "survived", None) or [])
    gaps = list(getattr(res, "gaps", None) or [])
    old = [r for r in (getattr(res, "residual", None) or []) if not r[2]]
    lines = []
    if not getattr(res, "verified", False):
        lines.append("⚠ Результат записан без повторной инспекции: чистоту утверждать нельзя.")
    if survived:
        lines.append("⛔ <b>Исходные значения личности или окружения остались в файле: %d.</b>"
                     % len(survived))
        for sv in survived[:5]:
            lines.append("• %s (%s) → найдено: %s" % (_e(sv.label), _e(sv.location), _e(sv.found_at)))
        if len(survived) > 5:
            lines.append("… и ещё %d" % (len(survived) - 5))
    elif old:
        lines.append("⚠ В результате остались критичные находки с прежними значениями: %d. "
                     "Короткие значения (до 5 символов) проверка по подстроке не сравнивает, "
                     "смотрите отчёт ниже." % len(old))
    if gaps:
        lines.append("⚠ Инспектор не разобрал часть файла (%d): доказательство неполное, в "
                     "неразобранном могло остаться." % len(gaps))
    if not lines and res.clean:
        note = ("Повторная инспекция: ни одно исходное значение личности и окружения "
                "в результате не найдено.")
        if getattr(res, "critical_after", 0) > 0:
            note += (" Критичное, что инспектор ещё видит, -- новые значения (например, "
                     "перегенерированный trailer /ID): с исходными они не связаны.")
        return "ok", note
    return ("bad" if survived else "warn"), "\n".join(lines or ["⚠ Чистота не доказана."])


def _caption(level: str, profile: str, note: str) -> str:
    if level == "ok":
        return ("✅ Профиль <b>%s</b>. %s Имя файла нейтральное, исходное не использовано."
                % (profile, note))
    if level == "warn":
        return "⚠ Профиль <b>%s</b>: чистота НЕ доказана, прочтите предупреждение выше." % profile
    return ("⛔ ПОЧИЩЕНО НЕ ПОЛНОСТЬЮ: часть исходных значений осталась в файле. "
            "Не отправляйте его дальше.")


def _no_clean_text(fmt: str) -> str:
    """Формат без чистильщика: прямо и без обещаний. Фразу и номер фазы даёт
    cleanreport -- тот же текст, что увидит пользователь в любом отчёте о чистке."""
    if fmt in cleanreport.CLEANABLE:       # чистильщик есть, но модуль не загрузился
        why = "модуль чистки (core/clean.py) не загрузился"
    else:
        why = cleanreport.summary_line(
            CleanResult(src="", dst="", fmt=fmt, profile=Profile.STEALTH.value))
    return ("⛔ <b>Чистки для этого файла нет.</b> %s. Файл только просмотрен и не изменён: "
            "не отправляйте его дальше как почищенный." % html.escape(why, quote=False))


def _keyboard() -> InlineKeyboardMarkup:
    def row(text, data):
        return [InlineKeyboardButton(text=text, callback_data=data)]

    return InlineKeyboardMarkup(inline_keyboard=[
        row("Почистить (Stealth)", "cl:stealth"),
        row("Почистить (Paranoid)", "cl:paranoid"),
        row("Не чистить", "cl:no"),
    ])


# ============================================================================
#  Хендлеры
# ============================================================================


@router.message(CommandStart())
@router.message(Command("help"))
async def on_help(message: Message) -> None:
    if not _allowed(message):
        return
    await message.answer(HELP_TEXT)


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    if not _allowed(message):
        return
    await message.answer(
        "Это пришло как <b>фото</b>, а не файл. Telegram уже пережал и "
        "перекодировал картинку на своей стороне до того, как её увидел "
        "бот -- исходные метаданные (EXIF, GPS, модель камеры и т.п.) уже "
        "срезаны или переписаны. Инспектировать тут нечего, а результат "
        "будет ложным.\n\n"
        "Пришлите тот же файл ещё раз через скрепку → <b>Файл</b> "
        "(Document), не через раздел «Фото»."
    )


@router.message(F.document)
async def on_document(message: Message) -> None:
    if not _allowed(message):
        return
    doc: Document = message.document
    if doc.file_size and doc.file_size > config.MAX_FILE_BYTES:
        await message.answer(
            "Файл весит {mb} МБ. Telegram Bot API вообще не отдаёт ботам "
            "файлы крупнее {limit} МБ -- это ограничение самого Bot API, "
            "а не бота. Пришлите файл меньшего размера.".format(
                mb=round(doc.file_size / (1024 * 1024), 1),
                limit=config.MAX_FILE_BYTES // (1024 * 1024),
            )
        )
        return
    await _inspect_and_reply(message, doc)


async def _inspect_and_reply(message: Message, doc: Document) -> None:
    """Скачать, разобрать в потоке, отрендерить, ответить. Не падает никогда."""
    status = await message.answer("\U0001F50E Принято, разбираю метаданные…")
    tmp_dir = None
    started = time.monotonic()
    try:
        async with _SEMAPHORE:
            tmp_dir = tempfile.mkdtemp(prefix="tginsp_")
            file_path = os.path.join(tmp_dir, "input.bin")
            await message.bot.download(doc, destination=file_path, timeout=60)

            if inspect_file is None:
                await status.edit_text(
                    "Ядро разбора (core/inspect.py) пока не готово -- его "
                    "пишет параллельный агент. Попробуйте чуть позже."
                )
                return

            report = await _in_thread(tmp_dir, _INSPECT_TIMEOUT, inspect_file, file_path)

        duration = time.monotonic() - started
        parts = render_telegram(report)

        # Разрешено логировать только это: формат, размер, число находок, время.
        log.info(
            "разбор завершён: формат=%s размер=%d находок=%d время=%.1fс",
            getattr(report, "fmt", "?"),
            getattr(report, "size", 0) or 0,
            len(getattr(report, "findings", None) or []),
            duration,
        )

        await _quiet(status.delete())
        for part in parts:
            await message.answer(part)

        if not _looks_neutral(doc.file_name or ""):
            await message.answer(
                "Напоминание: само <b>имя файла</b> -- тоже метаданные. "
                "Оно не похоже на техническое авто-имя, так что при "
                "дальнейшей передаче файла его стоит поменять отдельно."
            )

        await _offer_cleaning(message, getattr(report, "fmt", ""), doc)
    except _Timeout:
        tmp_dir = None  # каталог убирает сам поток, когда закончит
        await _say(
            status,
            "Разбор не уложился в {}с и был прерван -- файл слишком "
            "сложный или повреждён.".format(_INSPECT_TIMEOUT),
        )
    except Exception as exc:
        # Приватность: только тип сбоя -- в тексте исключения бывают значения из файла.
        log.error("сбой при разборе документа: %s", type(exc).__name__)
        log.debug("подробности сбоя", exc_info=True)
        await _say(
            status,
            "Не получилось разобрать файл из-за внутренней ошибки. "
            "Файл не изменён и не сохранён на сервере бота.",
        )
    finally:
        if tmp_dir:
            _rmtree(tmp_dir)


async def _offer_cleaning(message: Message, fmt: str, doc: Document) -> None:
    """Кнопки -- только там, где чистка есть. Иначе прямо: файл НЕ почищен."""
    if clean_file is None or fmt not in cleanreport.CLEANABLE:
        await message.answer(_no_clean_text(fmt))
        return
    sent = await message.answer(CHOICE_TEXT, reply_markup=_keyboard())
    user = message.from_user
    _remember(
        (sent.chat.id, sent.message_id),
        _Pending(doc.file_id, fmt, user.id if user else None, time.monotonic() + _PENDING_TTL),
    )


@router.callback_query(F.data.in_(("cl:stealth", "cl:paranoid", "cl:no")))
async def on_clean_choice(cb: CallbackQuery) -> None:
    """Нажатие кнопки. Не падает никогда."""
    async def ack(text=None, alert=False):
        # Отвечать надо сразу: через ~минуту Telegram отвергает ответ на старый запрос.
        await _quiet(cb.answer(text, show_alert=alert))

    try:
        if not _allowed(cb):
            await ack()
            return
        msg = cb.message
        if msg is None or isinstance(msg, InaccessibleMessage):
            await ack("Сообщение недоступно. Пришлите файл ещё раз.", True)
            return
        _purge()
        key = (msg.chat.id, msg.message_id)
        item = _pending.get(key)
        if item is None:
            await ack("Кнопка устарела: прошло больше 30 минут или бот перезапускался. "
                      "Пришлите файл ещё раз.", True)
            await _quiet(msg.edit_reply_markup(reply_markup=None))
            return
        if item.user_id is not None and cb.from_user.id != item.user_id:
            await ack("Эта кнопка не для вас.", True)
            return
        # Первое нажатие забирает запись: двойной тап не запустит чистку дважды.
        del _pending[key]
        if cb.data == "cl:no":
            await ack()
            await _say(msg, "Хорошо, файл не чищу. Он не сохранялся на сервере бота.")
            return
        await ack("Чищу…")
        await _clean_and_reply(msg, item, _PROFILES[cb.data])
    except Exception as exc:
        log.error("сбой в обработчике кнопки: %s", type(exc).__name__)
        log.debug("подробности сбоя", exc_info=True)
        await ack("Внутренняя ошибка. Файл не чищен, пришлите его ещё раз.", True)


async def _clean_and_reply(msg: Message, item: _Pending, profile: Profile) -> None:
    """Скачать заново, почистить в потоке, ответить отчётом и файлом. Не падает
    никогда. msg -- сообщение с кнопками: превращается в строку статуса."""
    pname = profile.value.capitalize()
    tmp_dir = None
    started = time.monotonic()
    try:
        await _say(msg, "\U0001F9F9 Чищу файл, профиль <b>%s</b>…" % pname)  # заодно уберёт кнопки
        # ponytail: по таймауту семафор отпускается, а поток-«сирота» ещё
        # дорабатывает. Для личного бота терпимо; держать семафор до конца
        # потока -- если понадобится строгая одна задача за раз.
        async with _SEMAPHORE:
            tmp_dir = tempfile.mkdtemp(prefix="tgclean_")
            src = os.path.join(tmp_dir, "input.bin")
            name = _neutral_name(item.fmt)
            dst = os.path.join(tmp_dir, "out", name)
            await msg.bot.download(item.file_id, destination=src, timeout=60)
            size_in = os.path.getsize(src)
            res = await _in_thread(tmp_dir, _CLEAN_TIMEOUT, clean_file, src, dst, profile)

        ok = bool(res.ok)
        if ok and not os.path.isfile(dst):
            raise RuntimeError("чистильщик сообщил об успехе, но файла нет")
        # Разрешено логировать только это: формат, профиль, исход, размеры, число действий, время.
        log.info(
            "чистка завершена: формат=%s профиль=%s ok=%s размер_до=%d размер_после=%d "
            "действий=%d время=%.1fс",
            item.fmt, profile.value, ok, size_in, os.path.getsize(dst) if ok else 0,
            len(res.actions), time.monotonic() - started,
        )

        level, note = _verdict(res) if ok else ("bad", "")
        if ok:
            await _quiet(msg.delete())
            if level != "ok":
                await msg.answer(note)  # предупреждение раньше отчёта, который может писать «ЧИСТО»
        else:
            await _say(msg, "⛔ <b>Файл НЕ почищен</b> -- чистка не удалась, почищенной копии нет, "
                            "бот никакой файл не отправляет. Причина и подробности ниже.")
        for part in cleanreport.render_telegram(res):
            await msg.answer(part)
        if ok:
            await msg.answer_document(
                FSInputFile(dst, filename=name),
                caption=_caption(level, pname, note),
                disable_content_type_detection=True,  # документ, не фото: ничего не пережимается
            )
    except _Timeout:
        tmp_dir = None  # каталог убирает сам поток, когда закончит
        await _say(msg, "Чистка не уложилась в {}с и прервана -- файл слишком сложный или "
                        "повреждён. Почищенного файла нет: ничего не отправляйте как "
                        "почищенное. Исходный файл не изменён; чтобы повторить, пришлите "
                        "его ещё раз.".format(_CLEAN_TIMEOUT))
    except Exception as exc:
        # Приватность: только тип сбоя -- в тексте исключения бывают значения из файла.
        log.error("сбой при чистке документа: %s", type(exc).__name__)
        log.debug("подробности сбоя", exc_info=True)
        await _say(msg, "Не получилось почистить файл из-за внутренней ошибки. Почищенный файл "
                        "НЕ отправлен: не считайте файл почищенным. Исходный файл не изменён и "
                        "не сохранён на сервере бота.")
    finally:
        if tmp_dir:
            _rmtree(tmp_dir)


async def _run() -> None:
    token = config.require_token()
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


def main() -> None:
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(asyncio.run(_selftest()))
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


# ============================================================================
#  Самопроверка без токена и сети: подставные message/callback + настоящие
#  inspect_file / clean_file на фикстурах. PYTHONIOENCODING=utf-8 python bot.py --selftest
# ============================================================================


async def _selftest() -> int:
    import glob
    import hashlib
    from types import SimpleNamespace

    from core import clean as core_clean
    from core.inspect import inspect_file as insp
    from tests import fixtures as fx

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    fails, total = [], [0]
    g = globals()

    def check(name, cond, detail=""):
        total[0] += 1
        if not cond:
            fails.append(name)
        print("  [%s] %s%s" % ("ok" if cond else "ПРОВАЛ", name,
                               "  -- %s" % (detail,) if (detail and not cond) else ""))

    class FakeBot:
        def __init__(self):
            self.files = {}

        async def download(self, file, destination, timeout=30):
            with open(destination, "wb") as fh:
                fh.write(self.files[getattr(file, "file_id", file)])

    class FakeMsg:
        _n = 100

        def __init__(self, bot, events, user_id=7, text=None, markup=None):
            FakeMsg._n += 1
            self.bot, self.events = bot, events
            self.chat = SimpleNamespace(id=1)
            self.message_id = FakeMsg._n
            self.from_user = SimpleNamespace(id=user_id)
            self.text, self.markup = text, markup

        async def answer(self, text, reply_markup=None, **kw):
            m = FakeMsg(self.bot, self.events, text=text, markup=reply_markup)
            self.events.append(("text", m))
            return m

        async def answer_document(self, document, caption=None, **kw):
            with open(document.path, "rb") as fh:
                data = fh.read()
            self.events.append(("doc", SimpleNamespace(
                name=document.filename, data=data, caption=caption, kw=kw)))

        async def edit_text(self, text, **kw):
            self.text, self.markup = text, None

        async def edit_reply_markup(self, **kw):
            self.markup = None

        async def delete(self):
            self.events.append(("deleted", self))

    class FakeCb:
        def __init__(self, msg, data, user_id=7):
            self.message, self.data = msg, data
            self.from_user = SimpleNamespace(id=user_id)
            self.acks = []

        async def answer(self, text=None, show_alert=False, **kw):
            self.acks.append((text, show_alert))

    logs = []

    class Grab(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())

    grab = Grab()
    log.addHandler(grab)
    work = tempfile.mkdtemp(prefix="botself_")
    bot = FakeBot()
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    left = lambda: sorted(glob.glob(os.path.join(tempfile.gettempdir(), "tg[ic][nl]*_*")))  # noqa: E731
    texts = lambda ev: [m.text for k, m in ev if k == "text"]  # noqa: E731
    docs = lambda ev: [d for k, d in ev if k == "doc"]  # noqa: E731
    saved = (g["clean_file"], g["_CLEAN_TIMEOUT"], dict(core_clean._CLEANERS), list(config.ALLOWED_USER_IDS))

    async def send(maker, name):
        """Положить фикстуру, прогнать настоящий _inspect_and_reply."""
        d = os.path.join(work, name)
        os.makedirs(d)
        path, exp = maker(d)
        with open(path, "rb") as fh:
            bot.files[name] = fh.read()
        ev = []
        m = FakeMsg(bot, ev)
        await _inspect_and_reply(m, SimpleNamespace(
            file_id=name, file_name=os.path.basename(path), file_size=len(bot.files[name])))
        choice = next((x for k, x in ev if k == "text" and x.markup is not None), None)
        return path, exp, ev, choice

    async def click(choice, data, user_id=7):
        cb = FakeCb(choice, data, user_id)
        await on_clean_choice(cb)
        return cb

    def survivors(src_path, data, tag):
        out = os.path.join(work, "res_" + tag)
        with open(out, "wb") as fh:
            fh.write(data)
        return core_clean.survived_values(insp(src_path), insp(out))

    try:
        print("1. PDF: инспекция -> кнопки -> Stealth -> отчёт + файл")
        base = left()
        src, exp, ev, ch = await send(fx.make_pdf, "pdf")
        before = sha(bot.files["pdf"])
        btns = [b.text for r in ch.markup.inline_keyboard for b in r] if ch else []
        check("кнопки: Stealth, Paranoid, Не чистить",
              btns == ["Почистить (Stealth)", "Почистить (Paranoid)", "Не чистить"], btns)
        check("профили объяснены прямо в сообщении",
              ch and "Stealth" in ch.text and "оставляет" in ch.text and "Paranoid" in ch.text
              and "выглядит обработанным" in ch.text)
        check("запомнен file_id, на диске после инспекции ничего нет", len(_pending) == 1 and left() == base)
        cb = await click(ch, "cl:stealth")
        d = docs(ev)
        check("ответили на нажатие сразу", cb.acks and cb.acks[0][0] == "Чищу…", cb.acks)
        check("файл отправлен один раз", len(d) == 1, len(d))
        if d:
            check("имя нейтральное, исходного названия нет",
                  re.fullmatch(r"document_[0-9a-f]{6}\.pdf", d[0].name) and "probe" not in d[0].name, d[0].name)
            check("отправлен как документ (без определения типа/пережатия)",
                  d[0].kw.get("disable_content_type_detection") is True)
            check("из присланного файла исходные значения ушли", survivors(src, d[0].data, "pdf") == [])
            check("подпись: профиль и вердикт", "Stealth" in d[0].caption and "✅" in d[0].caption, d[0].caption)
        kinds = [k for k, _ in ev]
        check("порядок: отчёт о чистке раньше файла",
              any("Чистка метаданных" in t for t in texts(ev)) and kinds.index("doc") > max(
                  i for i, (k, m) in enumerate(ev) if k == "text" and "Чистка метаданных" in m.text))
        check("вход не изменён, записи и каталоги убраны", sha(open(src, "rb").read()) == before
              and not _pending and left() == base)
        cb = await click(ch, "cl:stealth")
        check("повторное нажатие: «устарела», второго файла нет",
              cb.acks and cb.acks[0][1] and "устарела" in cb.acks[0][0] and len(docs(ev)) == 1, cb.acks)
        leak = [v for v in exp["values"] if any(v in line for line in logs)] + (
            ["имя"] if any("probe" in line for line in logs) else [])
        check("логи: ни значений находок, ни имени файла", not leak, leak)

        print("2. JPEG / Paranoid и PNG / Stealth")
        for maker, nm, prof, ext in ((fx.make_jpeg, "jpg", "cl:paranoid", ".jpg"), (fx.make_png, "png", "cl:stealth", ".png")):
            src, exp, ev, ch = await send(maker, nm)
            await click(ch, prof)
            d = docs(ev)
            check("%s: файл отправлен, имя image_*%s" % (nm, ext),
                  len(d) == 1 and re.fullmatch(r"image_[0-9a-f]{6}" + re.escape(ext), d[0].name) is not None,
                  [x.name for x in d])
            check("%s: исходные значения ушли" % nm, d and survivors(src, d[0].data, nm) == [])

        print("2b. OOXML (фаза 2): кнопки есть, файл выдан с правильным расширением")
        for maker, nm, pat in ((fx.make_docx, "docx", r"document_[0-9a-f]{6}\.docx"),
                               (fx.make_xlsx, "xlsx", r"spreadsheet_[0-9a-f]{6}\.xlsx"),
                               (fx.make_pptx, "pptx", r"presentation_[0-9a-f]{6}\.pptx")):
            src, exp, ev, ch = await send(maker, nm)
            check("%s: кнопки чистки показаны" % nm, ch is not None and ch.markup is not None)
            if ch is None:
                continue
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("%s: файл отправлен, имя нейтральное и с родным расширением" % nm,
                  len(d) == 1 and re.fullmatch(pat, d[0].name) is not None, [x.name for x in d])
            check("%s: исходные значения ушли" % nm, d and survivors(src, d[0].data, nm) == [])
            # Честность про незаметность: у этой фикстуры Stealth оставляет
            # признак чистки (пустые dc:creator, cp:lastModifiedBy, Company), и
            # пользователь обязан прочитать это в отчёте, а не узнать от того,
            # кому отправил файл. Проверяем сквозь бота, а не только в ядре.
            if nm == "docx":
                check("docx: отчёт предупреждает, что файл опознаётся как чищеный",
                      any("ОПОЗНАЁТСЯ КАК ЧИЩЕНЫЙ" in t for t in texts(ev)), texts(ev)[-1][:200])

        print("3. форматы без чистки: кнопок нет, фаза названа")
        # Номер фазы здесь идёт из cleanreport._PHASE, а не от диспетчера:
        # _no_clean_text() строит пустой CleanResult, у которого planned_phase=0.
        # RTF -- фаза 2 (чистится на месте), легаси OLE2 -- фаза 4.
        for maker, nm, phase in ((fx.make_rtf, "rtf", 2), (fx.make_doc, "doc", 4)):
            _, _, ev, ch = await send(maker, nm)
            last = texts(ev)[-1]
            check("%s: нет кнопок, нет файла, нет записи" % nm,
                  ch is None and not docs(ev) and not _pending and all(m.markup is None for k, m in ev if k == "text"))
            check("%s: «НЕ почищен», только инспектируется, фаза %d" % (nm, phase),
                  "НЕ почищен" in last and "только инспектируется" in last and "фазе %d" % phase in last, last)

        print("4. ok=False: файл не отправляется")
        def refuse(s, d, p):
            r = core_clean.DispatchResult(s, d, "pdf", "stealth")
            r.err("тест: отказ чистильщика")
            return r
        g["clean_file"] = refuse
        _, _, ev, ch = await send(fx.make_pdf, "pdf4")
        await click(ch, "cl:paranoid")
        check("нет файла, сказано «НЕ почищен», причина в отчёте",
              not docs(ev) and "НЕ почищен" in ch.text and any("отказ чистильщика" in t for t in texts(ev)), ch.text)
        check("каталоги убраны", left() == base)

        print("5. таймаут: поток дорабатывает, каталог убирается после него")
        def slow(s, d, p):
            time.sleep(1.0)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with open(d, "wb") as fh:
                fh.write(b"x")
            return core_clean.DispatchResult(s, d, "pdf", "stealth", ok=True)
        g["clean_file"], g["_CLEAN_TIMEOUT"] = slow, 0.3
        _, _, ev, ch = await send(fx.make_pdf, "pdf5")
        await click(ch, "cl:stealth")
        check("сказано про таймаут, файла нет", "не уложилась" in ch.text and not docs(ev), ch.text)
        check("пока поток работает, каталог не трогаем", left() != base)
        await asyncio.sleep(1.6)
        check("поток закончил -- каталог убран", left() == base, left())
        g["_CLEAN_TIMEOUT"] = saved[1]

        print("6. исключение в чистильщике: человеческий текст, без утечки")
        def boom(s, d, p):
            raise RuntimeError("СЕКРЕТНОЕ-ЗНАЧЕНИЕ-ИЗ-ФАЙЛА")
        g["clean_file"] = boom
        _, _, ev, ch = await send(fx.make_pdf, "pdf6")
        await click(ch, "cl:stealth")
        check("внутренняя ошибка, файла нет, значение не в чат",
              "внутренней ошибки" in ch.text and "НЕ отправлен" in ch.text and "СЕКРЕТНОЕ" not in ch.text and not docs(ev), ch.text)
        check("значение не в логах, каталоги убраны",
              not any("СЕКРЕТНОЕ" in x for x in logs) and left() == base)
        g["clean_file"] = saved[0]

        print("7. чистильщик-лжец: утечка осталась -> предупреждение ПЕРЕД отчётом")
        def liar(s, d, prof, keep, force):
            shutil.copyfile(s, d)
            r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
            r.ok = True
            r.act("removed", "/Info", "/Author", "-")
            return r
        core_clean._CLEANERS["pdf"] = liar
        _, _, ev, ch = await send(fx.make_pdf, "pdf7")
        await click(ch, "cl:stealth")
        warn = next((i for i, (k, m) in enumerate(ev) if k == "text" and "остались в файле" in m.text), -1)
        rep = next((i for i, (k, m) in enumerate(ev) if k == "text" and "Чистка метаданных" in m.text), -1)
        d = docs(ev)
        check("предупреждение раньше отчёта", 0 <= warn < rep, (warn, rep))
        check("файл выдан, но подпись «НЕ ПОЛНОСТЬЮ»", len(d) == 1 and "НЕ ПОЛНОСТЬЮ" in d[0].caption, [x.caption for x in d])
        core_clean._CLEANERS.update(saved[2])

        print("8. состояние: размер, TTL, чужой, не из списка, «Не чистить»")
        _pending.clear()
        for i in range(_PENDING_MAX + 5):
            _remember((1, i), _Pending("f", "pdf", 7, time.monotonic() + 60))
        check("размер ограничен, вытеснены старейшие",
              len(_pending) == _PENDING_MAX and (1, 0) not in _pending and (1, _PENDING_MAX + 4) in _pending)
        for v in _pending.values():
            v.expires = 0
        _purge()
        check("просроченные выброшены", not _pending)
        _, _, ev, ch = await send(fx.make_pdf, "pdf8")
        cb = await click(ch, "cl:stealth", user_id=8)
        check("чужая кнопка отклонена, запись цела", cb.acks[0][1] and len(_pending) == 1 and not docs(ev))
        config.ALLOWED_USER_IDS[:] = [1]
        cb = await click(ch, "cl:stealth", user_id=7)
        config.ALLOWED_USER_IDS[:] = saved[3]
        check("пользователь не из списка: молчим, запись цела", cb.acks == [(None, False)] and len(_pending) == 1 and not docs(ev), cb.acks)
        await click(ch, "cl:no")
        check("«Не чистить»: записи нет, файла нет, текст сказан",
              not _pending and not docs(ev) and "не чищу" in ch.text, ch.text)

        print("9. _verdict")
        mk = lambda **kw: core_clean.DispatchResult("a", "b", "pdf", "stealth", ok=True, **kw)  # noqa: E731
        lv, note = _verdict(mk(verified=True, critical_after=2, residual=[("trailer", "/ID[0]", True)]))
        check("чисто + новые значения: ok и пояснение про /ID", lv == "ok" and "trailer /ID" in note, (lv, note))
        sv = core_clean.Survivor("identity", "/Info", "<b>&" + "&" * 400, "секретное", "x > " + "&" * 400)
        lv, note = _verdict(mk(verified=True, survived=[sv] * 100))
        body = re.sub(r"</?b>", "", note)
        check("survived: bad, не больше 4096, экранировано, «и ещё 95»",
              lv == "bad" and len(note.encode("utf-16-le")) // 2 <= 4096 and "<" not in body and ">" not in body
              and not re.search(r"&(?!(?:amp|lt|gt);)", body) and "и ещё 95" in note, len(note))
        check("gaps -> warn", _verdict(mk(verified=True, gaps=["x"]))[0] == "warn")
        check("прежние критичные при пустом survived -> warn",
              _verdict(mk(verified=True, residual=[("/Info", "Автор", False)]))[0] == "warn")
        check("не verified -> warn", _verdict(mk())[0] == "warn")
    finally:
        g["clean_file"], g["_CLEAN_TIMEOUT"] = saved[0], saved[1]
        core_clean._CLEANERS.clear()
        core_clean._CLEANERS.update(saved[2])
        config.ALLOWED_USER_IDS[:] = saved[3]
        log.removeHandler(grab)
        shutil.rmtree(work, ignore_errors=True)
        _pending.clear()

    print("\nИТОГО: %d проверок, %d провалено" % (total[0], len(fails)))
    for f in fails:
        print("  ПРОВАЛ: " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    main()
