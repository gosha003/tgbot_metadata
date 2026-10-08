# -*- coding: utf-8 -*-
"""Телеграм-оболочка: инспекция, чистка (Stealth / Paranoid / Пересоздать),
пакетная чистка ZIP, удаление отчётов из чата.

Инспекция файл не меняет. Чистка -- только по явному нажатию кнопки и только
для форматов, у которых чистильщик есть (cleanreport.CLEANABLE), плюс легаси
doc/xls/ppt через пересоздание, если на машине есть LibreOffice. Для остальных
бот прямо пишет, что файл НЕ почищен: отдать файл, который пользователь считает
почищенным, а он не почищен, -- худший исход для этого проекта.

Один экземпляр на машину: main() держит файл-замок config.LOCK_PATH.

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
import functools
import html
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    Document,
    FSInputFile,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from core import cleanreport, sniff
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

try:
    from core.clean_batch import clean_batch, render_report as render_batch_report
except ImportError:
    # Нет пакетного модуля: одиночная чистка работает, ZIP бот честно не берёт.
    clean_batch = None

try:
    from core.clean_regen import find_soffice
except ImportError:
    def find_soffice():
        return None

try:
    from core.insp_image import _exiftool_exe
except ImportError:
    def _exiftool_exe():
        return None

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
# Пакет = до MAX_FILES полных чисток подряд (а на «Пересоздать» -- запусков
# LibreOffice), поэтому потолок на порядок выше одиночного.
_BATCH_TIMEOUT = 1800
# Пакетный отчёт короче этого числа сообщений идёт в чат, длиннее -- документом.
_BATCH_CHAT_PARTS = 4

# ponytail: состояние кнопок живёт в памяти процесса и пропадает при
# перезапуске бота (нажатие после рестарта = "кнопка устарела"). Для
# однопользовательского бота достаточно; БД/Redis -- если появится
# многопользовательский режим.
_PENDING_TTL = 30 * 60  # секунд
_PENDING_MAX = 50       # записей; старейшие вытесняются

# Сколько сообщений отчёта бот готов отправить за один раз. Рендерер нарезает
# отчёт по лимиту Telegram в 4096 символов и числа сообщений не ограничивает:
# на патологическом входе с сотнями находок получается несколько десятков
# сообщений, а Telegram отвечает на такой поток 429 и временно банит бота --
# то есть бот ломает себя сам, и именно на том файле, который интереснее всего.
# Лимит ставит хендлер: он один знает, что получатель -- чат, а не консоль.
_MAX_PARTS = 12
# Пауза между сообщениями. Личный чат терпит примерно одно сообщение в секунду;
# 0.35 с -- компромисс между «не ждать минуту на длинном отчёте» и «не собрать
# 429». Задержка срабатывает только на реально длинных отчётах.
_SEND_PAUSE = 0.35

# ponytail: грубая эвристика "похоже на авто-имя" по алфавиту символов
# (буквы/цифры/подчёркивание/дефис), а не по смыслу слова. Усложнять до
# словаря типичных личных имён -- только если эвристика реально подведёт.
_NEUTRAL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$")

_PROFILES = {"cl:stealth": Profile.STEALTH, "cl:paranoid": Profile.PARANOID,
             "cl:regen": Profile.REGENERATE, "bt:stealth": Profile.STEALTH,
             "bt:paranoid": Profile.PARANOID, "bt:regen": Profile.REGENERATE}

# Whitelist пользователя (keep). Ключи -- ровно то, что понимают чистильщики:
# PDF сверяет label/location ("/Author", "dc:creator"), OOXML -- "dc:title",
# ODF и RTF -- локальные имена ("creator", "author"), картинки -- "icc".
# Подпись, ключи keep, подстроки русских/английских подписей полей из отчёта
# инспектора (нужны, чтобы отличить выжившее ПО ВЫБОРУ от выжившего по недочёту).
_KEEP_OPTS = {
    "title": ("заголовок документа", {"/Title", "dc:title", "title"},
              ("заголовок", "название", "title")),
    "author": ("автора", {"/Author", "dc:creator", "author", "creator", "initial-creator"},
               ("автор", "creator", "author", "кто сохранял", "lastmodifiedby")),
    "icc": ("ICC-профиль", {"icc"}, ()),
}
_IMG_FMT = ("jpeg", "png", "webp", "gif")
# Легаси OLE2: на месте не чистится в принципе, только пересоздание через
# LibreOffice (выход -- docx/xlsx/pptx). Поэтому у них одна кнопка.
_LEGACY = ("doc", "xls", "ppt")
_EXT = {"pdf": ".pdf", "jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif",
        "docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "rtf": ".rtf",
        "odt": ".odt", "ods": ".ods", "odp": ".odp", "zip": ".zip",
        "doc": ".doc", "xls": ".xls", "ppt": ".ppt"}
# Основа нейтрального имени: родовое слово по типу файла, без картинки как
# умолчания -- иначе docx уехал бы с именем image_*.docx.
_STEM = {"pdf": "document", "docx": "document", "xlsx": "spreadsheet",
         "pptx": "presentation", "rtf": "document", "odt": "document",
         "ods": "spreadsheet", "odp": "presentation", "zip": "archive",
         "doc": "document", "xls": "spreadsheet", "ppt": "presentation"}

HELP_TEXT = (
    "Это инспектор и чистильщик метаданных документов.\n\n"
    "Пришлите документ (docx/xlsx/pptx, PDF, doc/xls/ppt, ODF, RTF, "
    "картинку) -- бот покажет, что сейчас лежит внутри: автор, приложение, "
    "даты, локальные пути, GPS в фото и т.п. Файл при этом <b>не меняется "
    "и не сохраняется</b>.\n\n"
    "Для <b>PDF, картинок (JPEG, PNG, WebP, GIF), docx/xlsx/pptx, RTF и ODF</b> после "
    "отчёта появятся кнопки чистки: <b>Stealth</b> (убирает личность и "
    "окружение, оставляет приложение и версию) или <b>Paranoid</b> (убирает "
    "всё, но результат выглядит обработанным). Бот пришлёт отчёт о чистке и "
    "новую копию файла документом, с нейтральным именем.\n\n"
    "У docx/xlsx/pptx полной незаметности не даёт даже Stealth: пустой автор "
    "сам по себе признак чистки. Бот честно пишет это в отчёте, а не "
    "умалчивает.\n\n"
    "Перед чисткой можно переключателями «Сохранить» оставить заголовок, "
    "автора или ICC-профиль. Это ваш осознанный выбор: файл с сохранённым "
    "автором <b>не будет чистым</b>, и бот так и скажет.\n\n"
    "Пришлите <b>ZIP</b> -- бот почистит каждый документ внутри и вернёт архив "
    "только с почищенным и проверенным (отчёт по каждому файлу будет в чате).\n\n"
    "Старые <b>doc/xls/ppt</b> на месте не чистятся в принципе: метаданные "
    "и удалённый текст прошлых версий вшиты в сам контейнер. Для них одна "
    "кнопка -- <b>Пересоздать</b> через LibreOffice, на выходе docx/xlsx/pptx. "
    "Нет LibreOffice на машине -- бот так и скажет и файл не тронет.\n\n"
    "Автоудаление отчётов из чата: {ttl}. Удалить сразу -- кнопка "
    "«Удалить отчёт» под отчётом или команда /clear. Почищенные файлы не "
    "удаляются. /status -- что доступно на этой машине.\n\n"
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
    "результат выглядит обработанным.\n"
    "• <b>Пересоздать</b> -- сохранить заново через LibreOffice. Следа чистки "
    "нет, потому что файл никто не чистил, но приложение становится "
    "LibreOffice и вёрстка может измениться. Нет LibreOffice -- отказ, "
    "а не другая чистка. Переключатели «Сохранить» с пересозданием не "
    "работают: бот откажет, а не проигнорирует их молча.\n\n"
    "<i>Переключатели «Сохранить»: поле остаётся как было, поэтому файл НЕ "
    "будет чистым -- отчёт назовёт это вашим выбором.</i>"
)

LEGACY_TEXT = (
    "\U0001F9F9 <b>Почистить файл?</b> Старый формат Office на месте не "
    "чистится: метаданные и удалённый текст прошлых версий вшиты в сам "
    "контейнер. Честный путь один -- <b>Пересоздать</b>: LibreOffice "
    "сохранит документ заново в современном формате (docx/xlsx/pptx) без "
    "истории правок и без вашего имени.\n\n"
    "<i>Приложением станет LibreOffice, вёрстка может немного измениться. "
    "Исходный файл не меняется: бот пришлёт новую копию.</i>"
)

BATCH_TEXT = (
    "📦 <b>Это ZIP-архив: {n}</b>. Бот почистит каждый файл внутри "
    "(вложенные архивы не разворачиваются) и пришлёт новый архив <b>только с "
    "теми, чья чистота доказана</b> повторной инспекцией. Не очищенное в "
    "архив не попадает: его не отдать по ошибке как чистое. Отчёт по каждому "
    "файлу -- в чате.\n\n"
    "• <b>Stealth</b> / <b>Paranoid</b> -- как для одиночного файла.\n"
    "• <b>Пересоздать</b> -- через LibreOffice, файл за файлом: долго.\n"
    "• Имена файлов тоже метаданные: «нейтральные» -- doc_0001.docx и т.п. "
    "(соответствие номеров именам будет только в чате).\n"
    "• «Сохранить»: файл с сохранённым полем не считается чистым и в архив "
    "не попадёт -- такие файлы чистите по одному."
)


def _allowed(message: Message | CallbackQuery) -> bool:
    """Только те, кого владелец назвал явно.

    Раньше пустой список значил «можно всем». Это расходилось с CLAUDE.md и
    было небезопасно: любой, кто нашёл бота, грузил файлы в разбор и в
    LibreOffice на машине владельца. Теперь ненастроенный доступ = закрытый.
    """
    user = message.from_user
    return bool(user) and user.id in config.ALLOWED_USER_IDS


def _access_hint(user_id) -> str:
    """Что ответить тому, кого нет в списке. Своего user_id человек обычно не
    знает, поэтому называем его и команду, которую надо выполнить."""
    if not config.ACCESS_CONFIGURED:
        return ("Доступ к боту ещё не настроен, поэтому он никого не обслуживает.\n\n"
                "Ваш Telegram user_id: <code>%s</code>\n\n"
                "Если это ваш бот — добавьте в файл <code>.env</code> рядом с "
                "<code>bot.py</code> строку\n"
                "<code>TG_OWNER_ID=%s</code>\n"
                "и перезапустите бота." % (user_id, user_id))
    return ("Доступ к этому боту ограничен. Ваш user_id: <code>%s</code>" % user_id)


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


def _trim_parts(parts: list, how: str) -> list:
    """Отчёт -> не больше _MAX_PARTS сообщений, с честной пометкой об обрезке.

    parts: список готовых сообщений от рендерера (HTML).
    how: чем получить полный отчёт -- подставляется в текст пометки.
    Возврат: новый список; последним элементом добавлена пометка, если обрезали.
    Молчаливая обрезка здесь была бы хуже отсутствия лимита: пользователь
    инструмента приватности решил бы, что находок больше нет.
    """
    if len(parts) <= _MAX_PARTS:
        return list(parts)
    kept = list(parts[:_MAX_PARTS])
    kept.append(
        "⚠ <b>Отчёт обрезан</b>: показаны первые %d сообщений из %d.\n"
        "Остальное не потерялось -- оно есть в полном отчёте: <code>%s</code>.\n"
        "<i>Лимит стоит, чтобы бот не был заблокирован Telegram за поток "
        "сообщений.</i>" % (_MAX_PARTS, len(parts), html.escape(how, quote=False))
    )
    return kept


async def _send_parts(target, parts: list, how: str) -> None:
    """Отправить отчёт с лимитом и паузами.

    target: сообщение, в ответ на которое пишем (нужен только .answer).
    parts: готовые сообщения от рендерера.
    how: команда CLI для пометки об обрезке.

    Каждое сообщение запоминается для удаления (кнопкой, /clear, по таймеру):
    в отчёте лежат найденные значения. Кнопка «Удалить отчёт» -- под последним.

    Сбой на одном сообщении не съедает остальные -- отчёт уже построен, и
    показать его частично полезнее, чем не показать вовсе. Но промолчать о
    потере нельзя: недосланный кусок читается как «больше находок нет».
    """
    parts = _trim_parts(parts, how)
    failed = 0
    for n, part in enumerate(parts):
        if n:
            await asyncio.sleep(_SEND_PAUSE)
        try:
            _track(await target.answer(
                part, reply_markup=_DELETE_KB if n == len(parts) - 1 else None))
        except Exception as exc:
            # Приватность: только тип сбоя. В тексте исключения Telegram
            # возвращает фрагмент сообщения, а в нём -- значения из файла.
            failed += 1
            log.error("сообщение отчёта не отправлено: %s", type(exc).__name__)
    if failed:
        await _quiet(target.answer(
            "⚠ <b>Отчёт дошёл не полностью</b>: не отправлено сообщений -- %d из %d.\n"
            "Считайте отчёт неполным, а не пустым. Полный: <code>%s</code>."
            % (failed, len(parts), html.escape(how, quote=False))))


# ============================================================================
#  Состояние кнопок: (chat_id, message_id) -> что чистить
# ============================================================================


@dataclass
class _Pending:
    file_id: str        # сам файл НЕ храним: скачаем заново при нажатии
    fmt: str
    user_id: int | None
    expires: float
    keep: set = field(default_factory=set)   # ключи _KEEP_OPTS, которые просил сохранить пользователь
    neutral: bool = True                     # только для ZIP: нейтральные имена внутри архива


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
#  Отчёты в чате: (chat_id, message_id) -> когда отправлен
# ============================================================================
#
# Спойлер прячет значение от взгляда, но текст отчёта лежит на серверах
# Telegram и в истории чата. Поэтому бот помнит свои сообщения с находками и
# удаляет их: кнопкой, командой /clear или сам через REPORT_TTL_MIN минут.
# Почищенный файл не удаляется никогда: это результат, а не отчёт.
#
# ponytail: реестр в памяти процесса. После перезапуска бот не помнит старые
# отчёты, их придётся удалить руками (/clear это честно говорит). Хранить на
# диске -- если перезапуски станут частыми.

_reports: "OrderedDict[tuple, float]" = OrderedDict()
_REPORTS_MAX = 2000

_DELETE_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="\U0001F5D1 Удалить отчёт из чата", callback_data="rm:all")]])


def _track(sent) -> None:
    """Запомнить отправленное сообщение с находками. Не бросает."""
    try:
        key = (sent.chat.id, sent.message_id)
    except AttributeError:
        return
    _reports[key] = time.monotonic()
    while len(_reports) > _REPORTS_MAX:
        _reports.popitem(last=False)


async def _delete_reports(bot, chat_id=None, cutoff=None) -> int:
    """Удалить запомненные отчёты: чата chat_id (None -- всех) и отправленные
    не позже cutoff (monotonic; None -- все). Возврат: сколько удалено.
    Запись забывается в любом случае: сообщение, которое не удалилось
    (старше 48 часов или уже удалено руками), повторно пробовать незачем."""
    keys = [k for k, t in list(_reports.items())
            if (chat_id is None or k[0] == chat_id) and (cutoff is None or t <= cutoff)]
    done = 0
    for chat, mid in keys:
        _reports.pop((chat, mid), None)
        try:
            await bot.delete_message(chat, mid)
            done += 1
        except Exception:
            pass
    return done


async def _reaper(bot) -> None:
    """Раз в минуту удаляет отчёты старше REPORT_TTL_MIN. Не падает никогда."""
    while True:
        await asyncio.sleep(60)
        try:
            n = await _delete_reports(bot, cutoff=time.monotonic() - config.REPORT_TTL_MIN * 60)
            if n:
                log.info("автоудаление: удалено сообщений отчётов=%d", n)
        except Exception as exc:
            log.error("сбой автоудаления: %s", type(exc).__name__)


def _ttl_text() -> str:
    m = config.REPORT_TTL_MIN
    if m <= 0:
        return "выключено (REPORT_TTL_MIN=0)"
    return "через %d мин" % m if m < 120 else "через %d ч" % (m // 60)


def _help_text() -> str:
    return HELP_TEXT.format(ttl=_ttl_text())


# ============================================================================
#  Вердикт диспетчера и тексты
# ============================================================================


def _e(value, cap: int = 40) -> str:
    """Недоверенное значение -> безопасный HTML. Лимит 40 держит худший случай
    (каждый символ '&' -> 5 знаков) в пределах одного сообщения Telegram."""
    return html.escape(clip(value, cap), quote=False)


def _opts_for(fmt: str) -> tuple:
    """Какие переключатели «Сохранить» имеют смысл для формата."""
    if fmt in _LEGACY:
        return ()  # только пересоздание, а оно whitelist не понимает
    return ("icc",) if fmt in _IMG_FMT else (
        ("title", "author", "icc") if fmt == "zip" else ("title", "author"))


def _keep_keys(ids) -> set | None:
    """Ключи переключателей -> множество keep для чистильщика (None, если пусто)."""
    keys = set()
    for i in ids:
        keys |= _KEEP_OPTS[i][1]
    return keys or None


def _keep_names(ids) -> str:
    return ", ".join(_KEEP_OPTS[i][0] for i in _KEEP_OPTS if i in ids)


def _by_choice(sv, ids) -> bool:
    """Исходное значение выжило ПО ВЫБОРУ пользователя: и поле-источник, и место,
    где оно найдено в результате, -- это поле включённого переключателя. Автор,
    найденный в заголовке при включённом «сохранить автора», сюда не попадает:
    это недочистка, а не выбор. Чистильщики ODF и RTF записей KEPT про whitelist
    не пишут, а у OOXML в такой записи пустое значение, поэтому по actions выбор
    не определить -- сверяем по подписям полей."""
    label, found = str(sv.label).casefold(), str(sv.found_at).casefold()
    for i in ids:
        pats = _KEEP_OPTS[i][2]
        if any(p in label for p in pats) and any(p in found for p in pats):
            return True
    return False


_DETECT_RU = {"none": "инспектор признаков обработки не видит",
              "noticeable": "файл выглядит чищеным: факт обработки виден",
              "obvious": "файл опознаётся как обработанный"}


def _detect_line(res) -> str:
    """Оценка заметности чистки (core/lint.py) для случаев, где cleanreport её не
    печатает: он выводит ОЦЕНКА только в ветке «выживших нет»."""
    d = getattr(res, "detect", "")
    if d not in _DETECT_RU:
        return ""
    return "👁 <b>Заметность чистки</b>: %s." % _DETECT_RU[d]


def _verdict(res, keep=()) -> tuple:
    """(уровень, HTML): вердикт ДИСПЕТЧЕРА по значениям. Отчёт cleanreport судит
    по счётчику critical_after и про survived не знает: он может написать «ЧИСТО»
    там, где исходное имя автора пережило чистку внутри заголовка. Поэтому при
    любых сомнениях бот говорит сам и ставит это ПЕРЕД отчётом.

    keep -- ключи переключателей «Сохранить». Выжившее по этому выбору -- не
    недочистка, но и не «чисто»: уровень "kept", файл НЕ называется чистым."""
    keep = [k for k in _KEEP_OPTS if k in set(keep)]
    survived = list(getattr(res, "survived", None) or [])
    mine = [sv for sv in survived if _by_choice(sv, keep)]
    survived = [sv for sv in survived if not _by_choice(sv, keep)]
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
    elif old and not mine:
        lines.append("⚠ В результате остались критичные находки с прежними значениями: %d. "
                     "Короткие значения (до 5 символов) проверка по подстроке не сравнивает, "
                     "смотрите отчёт ниже." % len(old))
    if gaps:
        lines.append("⚠ Инспектор не разобрал часть файла (%d): доказательство неполное, в "
                     "неразобранном могло остаться." % len(gaps))
    if mine:
        # Это не скрывается и не подаётся как провал: пользователь так попросил.
        head = ("📌 <b>Сохранено по вашему выбору (%s): исходных значений в файле -- %d.</b> "
                "Это не недочистка: вы просили эти поля оставить. Но файл поэтому <b>НЕ чистый</b>, "
                "и отправлять его дальше можно только сознательно. Отчёт ниже может написать «НЕ "
                "ЧИСТО»: его вердикт считается без учёта вашего списка." % (_keep_names(keep), len(mine)))
        lines.insert(0, head)
        for sv in mine[:5]:
            lines.append("• %s (%s)" % (_e(sv.label), _e(sv.location)))
    if not lines and res.clean:
        note = ("Повторная инспекция: ни одно исходное значение личности и окружения "
                "в результате не найдено.")
        if getattr(res, "critical_after", 0) > 0:
            note += (" Критичное, что инспектор ещё видит, -- новые значения%s: с исходными "
                     "они не связаны." % (" (например, перегенерированный trailer /ID)"
                                          if res.fmt == "pdf" else ""))
        if keep:
            note += " Вы просили сохранить: %s." % _keep_names(keep)
        return "ok", note
    # Отчёт cleanreport при выживших оценку заметности не печатает -- добавляем сами.
    det = _detect_line(res)
    if det and (mine or survived):
        lines.append(det)
    if survived:
        level = "bad"
    elif mine and not gaps and getattr(res, "verified", False):
        level = "kept"
    else:
        level = "warn"
    return level, "\n".join(lines or ["⚠ Чистота не доказана."])


def _caption(level: str, profile: str, note: str, keep=()) -> str:
    if level == "ok":
        return ("✅ Профиль <b>%s</b>. %s Имя файла нейтральное, исходное не использовано."
                % (profile, note))
    if level == "kept":
        return ("📌 Профиль <b>%s</b>: по вашему выбору сохранено: %s. Файл НЕ чистый: "
                "эти поля остались как были." % (profile, _keep_names(keep)))
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


def _keyboard(item: "_Pending | None" = None) -> InlineKeyboardMarkup:
    """Переключатели «Сохранить» (по формату) -> профили -> «Не чистить».
    ZIP: профили идут в пакетный режим (bt:), плюс переключатель имён."""
    fmt = item.fmt if item else ""
    batch = fmt == "zip"
    pre = "bt" if batch else "cl"

    def row(text, data):
        return [InlineKeyboardButton(text=text, callback_data=data)]

    rows = []
    for opt in _opts_for(fmt):
        on = bool(item) and opt in item.keep
        rows.append(row("%s Сохранить: %s" % ("☑" if on else "☐", _KEEP_OPTS[opt][0]), "kp:" + opt))
    if fmt in _LEGACY:
        rows.append(row("Пересоздать (LibreOffice)", "cl:regen"))
    elif batch:
        rows.append(row("Имена в архиве: %s" % ("нейтральные" if (not item or item.neutral)
                                                else "исходные"), "kp:nn"))
        rows += [row("Почистить всё (Stealth)", pre + ":stealth"),
                 row("Почистить всё (Paranoid)", pre + ":paranoid"),
                 row("Пересоздать всё (LibreOffice)", pre + ":regen")]
    else:
        rows += [row("Почистить (Stealth)", pre + ":stealth"),
                 row("Почистить (Paranoid)", pre + ":paranoid"),
                 row("Пересоздать (LibreOffice)", pre + ":regen")]
    rows.append(row("Не чистить", "cl:no"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================================
#  Хендлеры
# ============================================================================


async def _deny(message: Message) -> None:
    # Отвечаем даже чужому: иначе владелец, ещё не прописавший себя в .env,
    # увидит молчащего бота и решит, что тот сломан. Текст не раскрывает
    # ничего, кроме user_id самого спрашивающего.
    user = message.from_user
    await _quiet(message.answer(_access_hint(user.id if user else "?")))


@router.message(CommandStart())
@router.message(Command("help"))
async def on_help(message: Message) -> None:
    if not _allowed(message):
        await _deny(message)
        return
    await message.answer(_help_text())


@router.message(Command("clear"))
async def on_clear(message: Message) -> None:
    """Удалить из чата все отчёты, которые бот помнит."""
    if not _allowed(message):
        await _deny(message)
        return
    n = await _delete_reports(message.bot, message.chat.id)
    await _quiet(message.delete())  # и саму команду: она тоже в истории
    await message.answer(
        "\U0001F5D1 Удалено сообщений с отчётами: %d.\n<i>Отчёты, отправленные до последнего "
        "перезапуска бота, он не помнит -- их удалите вручную. Почищенные файлы и ваши "
        "собственные сообщения бот не трогает.</i>" % n)


@router.message(Command("status"))
async def on_status(message: Message) -> None:
    """Что умеет бот на этой машине. Путей и значений не показывает."""
    if not _allowed(message):
        await _deny(message)
        return

    def yes(flag, ok, no):
        return ("✅ " + ok) if flag else ("⛔ " + no)

    soffice = bool(find_soffice())
    try:
        exif = bool(_exiftool_exe())
    except Exception:  # noqa: BLE001 -- обогащение, не повод падать
        exif = False
    lines = [
        "<b>Состояние бота</b>",
        yes(inspect_file is not None, "инспекция", "инспекция: модуль не загрузился"),
        yes(clean_file is not None, "чистка Stealth / Paranoid", "чистка: модуль не загрузился"),
        yes(clean_batch is not None, "пакетная чистка ZIP", "пакетная чистка: модуль не загрузился"),
        yes(soffice, "LibreOffice: «Пересоздать» и старые doc/xls/ppt",
            "LibreOffice не найден: «Пересоздать» и doc/xls/ppt недоступны"),
        yes(exif, "exiftool: расшифровка MakerNotes в фото",
            "exiftool нет: MakerNotes не расшифровываются (положите exiftool.exe в tools/)"),
        "\U0001F5D1 Автоудаление отчётов: %s" % _ttl_text(),
        "\U0001F464 Допущено пользователей: %d" % len(config.ALLOWED_USER_IDS),
    ]
    await message.answer("\n".join(lines))


@router.callback_query(F.data == "rm:all")
async def on_delete_reports(cb: CallbackQuery) -> None:
    """Кнопка «Удалить отчёт»: удаляет все запомненные отчёты этого чата."""
    try:
        if not _allowed(cb):
            await _quiet(cb.answer())
            return
        msg = cb.message
        if msg is None or isinstance(msg, InaccessibleMessage):
            await _quiet(cb.answer("Сообщение недоступно: удалите его вручную.", show_alert=True))
            return
        n = await _delete_reports(msg.bot, msg.chat.id)
        await _quiet(msg.delete())  # само сообщение с кнопкой, даже если бот его забыл
        await _quiet(cb.answer("Удалено сообщений: %d" % max(n, 1)))
    except Exception as exc:
        log.error("сбой удаления отчёта: %s", type(exc).__name__)
        await _quiet(cb.answer("Не получилось удалить. Удалите вручную.", show_alert=True))


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    if not _allowed(message):
        await _deny(message)
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
        await _deny(message)
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

            if sniff.sniff(file_path) == "zip":
                # ZIP общего вида: инспектировать нечего (метка времени записи --
                # не находка о документе), зато можно почистить содержимое пакетом.
                await _quiet(status.delete())
                await _offer_batch(message, doc, file_path)
                return

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
        await _send_parts(message, parts, "python -m core.inspect <файл>")

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
    """Кнопки -- только там, где чистка есть. Иначе прямо: файл НЕ почищен.
    Легаси doc/xls/ppt -- только «Пересоздать», и только если есть LibreOffice."""
    legacy = fmt in _LEGACY and clean_file is not None and bool(find_soffice())
    if not legacy and (clean_file is None or fmt not in cleanreport.CLEANABLE):
        text = _no_clean_text(fmt)
        if fmt in _LEGACY and clean_file is not None:
            text += ("\n\nПочистить такой файл можно пересозданием через LibreOffice, но на этой "
                     "машине он не найден. Установите LibreOffice и пришлите файл снова.")
        await message.answer(text)
        return
    user = message.from_user
    item = _Pending(doc.file_id, fmt, user.id if user else None, time.monotonic() + _PENDING_TTL)
    sent = await message.answer(LEGACY_TEXT if legacy else CHOICE_TEXT, reply_markup=_keyboard(item))
    _remember((sent.chat.id, sent.message_id), item)


async def _offer_batch(message: Message, doc: Document, path: str) -> None:
    """ZIP: показать, сколько файлов внутри, и предложить пакетную чистку."""
    if clean_batch is None:
        await message.answer("⛔ <b>Пакетной чистки нет</b>: модуль core/clean_batch.py не "
                             "загрузился. ZIP не чищен и не изменён.")
        return
    try:
        with zipfile.ZipFile(path) as zf:
            n = sum(1 for i in zf.infolist() if not i.filename.endswith(("/", "\\")))
    except Exception:  # noqa: BLE001 -- битый каталог: чистильщик сам откажет внятно
        n = -1
    head = "файлов внутри: %d" % n if n >= 0 else "каталог архива не читается, чистка, скорее всего, откажет"
    item = _Pending(doc.file_id, "zip", message.from_user.id if message.from_user else None,
                    time.monotonic() + _PENDING_TTL)
    sent = await message.answer(BATCH_TEXT.format(n=head), reply_markup=_keyboard(item))
    _remember((sent.chat.id, sent.message_id), item)


@router.message()
async def on_other(message: Message) -> None:
    """Всё прочее (текст, стикер, голос): подсказка вместо молчания."""
    if not _allowed(message):
        await _deny(message)
        return
    await message.answer(_help_text())


@router.callback_query(F.data.startswith(("cl:", "bt:", "kp:")))
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
        if cb.data.startswith("kp:"):
            # Переключатель: запись остаётся (и её TTL не продлевается), меняется клавиатура.
            opt = cb.data[3:]
            if opt == "nn" and item.fmt == "zip":
                item.neutral = not item.neutral
            elif opt in _opts_for(item.fmt):
                item.keep ^= {opt}
            else:
                await ack()
                return
            await _quiet(msg.edit_reply_markup(reply_markup=_keyboard(item)))
            await ack()
            return
        if cb.data == "cl:no":
            del _pending[key]
            await ack()
            await _say(msg, "Хорошо, файл не чищу. Он не сохранялся на сервере бота.")
            return
        profile = _PROFILES.get(cb.data)
        if profile is None:
            await ack()
            return
        if profile is Profile.REGENERATE and item.keep:
            # Пересоздание whitelist не понимает (clean_regen его отбрасывает).
            # Молча выдать файл без сохранённого -- обмануть пользователя: говорим прямо.
            await ack("Пересоздание не умеет «Сохранить»: LibreOffice пишет файл заново. "
                      "Снимите переключатели или выберите Stealth/Paranoid.", True)
            return
        # Первое нажатие забирает запись: двойной тап не запустит чистку дважды.
        del _pending[key]
        await ack("Чищу…")
        if cb.data.startswith("bt:"):
            await _batch_and_reply(msg, item, profile)
        else:
            await _clean_and_reply(msg, item, profile)
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
            src = os.path.join(tmp_dir, "input" + _EXT.get(item.fmt, ".bin"))
            dst = os.path.join(tmp_dir, "out", "out.bin")
            await msg.bot.download(item.file_id, destination=src, timeout=60)
            size_in = os.path.getsize(src)
            res = await _in_thread(tmp_dir, _CLEAN_TIMEOUT, clean_file, src, dst, profile,
                                   _keep_keys(item.keep))
            name = _neutral_name(res.fmt if res.fmt not in ("", "unknown") else item.fmt)

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

        level, note = _verdict(res, item.keep) if ok else ("bad", "")
        if ok:
            await _quiet(msg.delete())
            if level != "ok":
                # в вердикте бывают выжившие значения -- это тоже отчёт, под удаление
                _track(await msg.answer(note))  # предупреждение раньше отчёта, который может писать «ЧИСТО»
                # (и выбор пользователя тоже называем до отчёта, а не прячем в нём)
        else:
            await _say(msg, "⛔ <b>Файл НЕ почищен</b> -- чистка не удалась, почищенной копии нет, "
                            "бот никакой файл не отправляет. Причина и подробности ниже.")
        await _send_parts(msg, cleanreport.render_telegram(res),
                          "python -m core.clean <файл>")
        if ok:
            await msg.answer_document(
                FSInputFile(dst, filename=name),
                caption=_caption(level, pname, note, item.keep),
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


def _chunk_pre(text: str, limit: int = 3500) -> list:
    """Текст отчёта -> сообщения <pre> не длиннее limit (после экранирования).
    Строки недоверенные (имена из архива): экранируем каждую."""
    parts, cur = [], ""
    for line in text.splitlines():
        line = html.escape(line[:400], quote=False)
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append("<pre>%s</pre>" % cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        parts.append("<pre>%s</pre>" % cur)
    return parts


def _batch_detect_line(res) -> str:
    """Сводка заметности следов чистки по очищенным файлам пакета."""
    d = [i.detect for i in res.items if i.status == "cleaned"]
    if not d:
        return ""
    return ("👁 <b>Заметность чистки</b> (очищено %d): не видны -- %d, заметны -- %d, "
            "очевидны -- %d." % (len(d), d.count("none"), d.count("noticeable"), d.count("obvious")))


async def _batch_and_reply(msg: Message, item: _Pending, profile: Profile) -> None:
    """ZIP -> clean_batch в потоке -> отчёт по каждому файлу -> архив. Не падает
    никогда. Архив уходит только если в нём есть очищенное и он влезает в лимит
    отправки Bot API; не очищенное в архив не входит (решение clean_batch)."""
    pname = profile.value.capitalize()
    tmp_dir = None
    started = time.monotonic()
    try:
        await _say(msg, "📦 Чищу архив, профиль <b>%s</b>: это может занять минуты…" % pname)
        async with _SEMAPHORE:
            tmp_dir = tempfile.mkdtemp(prefix="tgclean_")
            src = os.path.join(tmp_dir, "input.zip")
            dst = os.path.join(tmp_dir, "out", "out.zip")
            await msg.bot.download(item.file_id, destination=src, timeout=120)
            size_in = os.path.getsize(src)
            res = await _in_thread(tmp_dir, _BATCH_TIMEOUT, functools.partial(
                clean_batch, src, dst, profile, _keep_keys(item.keep),
                neutral_names=item.neutral))

        t = res.totals
        size_out = os.path.getsize(dst) if res.ok and os.path.isfile(dst) else 0
        log.info("пакетная чистка: профиль=%s ok=%s файлов=%d очищено=%d не_очищено=%d "
                 "размер_до=%d размер_после=%d время=%.1fс", profile.value, res.ok,
                 t.get("total", 0), t.get("cleaned", 0), t.get("not_cleaned", 0),
                 size_in, size_out, time.monotonic() - started)

        if not res.ok:
            why = html.escape(clip(res.errors[0] if res.errors else "причина не указана", 300),
                              quote=False)
            await _say(msg, "⛔ <b>Архив НЕ почищен</b> -- выходного архива нет, бот ничего не "
                            "отправляет. Причина: %s" % why)
            return

        await _quiet(msg.delete())
        text = render_batch_report(res, with_names=True)
        parts = _chunk_pre(text)
        if len(parts) <= _BATCH_CHAT_PARTS:
            await _send_parts(msg, parts, "python -m core.clean_batch")
        else:
            # Отчёт на сотни файлов в чат не лезет (429 от Telegram): сводка + полный документом.
            head = text.split("\n\n", 1)[0]
            _track(await msg.answer("<pre>%s</pre>\nПодробно по каждому файлу -- документом ниже."
                                    % html.escape(head, quote=False)))
            rep = os.path.join(tmp_dir, "report.txt")
            with open(rep, "w", encoding="utf-8") as fh:
                fh.write(text)
            # Исходные имена файлов -- тоже метаданные: документ-отчёт удаляется как отчёт.
            _track(await msg.answer_document(
                FSInputFile(rep, filename="report_%s.txt" % secrets.token_hex(3)),
                caption="Отчёт по каждому файлу (с исходными именами).",
                reply_markup=_DELETE_KB))

        det = _batch_detect_line(res)
        if det:
            await msg.answer(det)
        if item.keep and t.get("unproven"):
            await msg.answer(
                "📌 Вы просили сохранить: %s. Файлов, не включённых в архив из-за чистоты, "
                "не доказанной по этой причине: %d -- это следствие вашего выбора. Очистите их "
                "по одному, если нужно." % (_keep_names(item.keep), t["unproven"]))
        if not t.get("cleaned"):
            await msg.answer("⛔ <b>Ни один файл не очищен</b> -- архива с результатом нет. "
                             "Причины по каждому файлу -- в отчёте выше.")
            return
        if size_out > config.MAX_SEND_BYTES:
            await msg.answer(
                "⛔ <b>Результат не отправлен</b>: архив весит %.1f МБ, а Telegram Bot API не даёт "
                "ботам отправлять файлы крупнее %d МБ (это лимит API, а не бота). Файлы очищены, но "
                "доставить их нельзя: разбейте исходный архив на части и пришлите снова."
                % (size_out / (1024 * 1024), config.MAX_SEND_BYTES // (1024 * 1024)))
            return
        if res.all_cleaned:
            cap = ("✅ Профиль <b>%s</b>: очищено и проверено файлов -- %d из %d. Имя архива "
                   "нейтральное." % (pname, t["cleaned"], t["total"]))
        else:
            cap = ("⚠ Профиль <b>%s</b>: в архиве только очищенное и проверенное -- %d из %d. "
                   "Остальные <b>НЕ включены</b> и у вас в исходном виде: см. отчёт." %
                   (pname, t["cleaned"], t["total"]))
        await msg.answer_document(
            FSInputFile(dst, filename=_neutral_name("zip")), caption=cap,
            disable_content_type_detection=True)
    except _Timeout:
        tmp_dir = None  # каталог убирает сам поток, когда закончит
        await _say(msg, "Пакетная чистка не уложилась в {} мин и прервана. Архива с результатом "
                        "нет: ничего не отправляйте как почищенное. Разбейте архив на части и "
                        "пришлите снова.".format(_BATCH_TIMEOUT // 60))
    except Exception as exc:
        log.error("сбой при пакетной чистке: %s", type(exc).__name__)
        log.debug("подробности сбоя", exc_info=True)
        await _say(msg, "Не получилось почистить архив из-за внутренней ошибки. Архив с результатом "
                        "НЕ отправлен: не считайте файлы почищенными. Исходный архив не изменён и "
                        "не сохранён на сервере бота.")
    finally:
        if tmp_dir:
            _rmtree(tmp_dir)


async def _run() -> None:
    token = config.require_token()
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    # Меню команд у кнопки «/» в Telegram. Без него /start не видно.
    await _quiet(bot.set_my_commands([
        BotCommand(command="start", description="Как пользоваться"),
        BotCommand(command="clear", description="Удалить отчёты из чата"),
        BotCommand(command="status", description="Что доступно на этой машине"),
        BotCommand(command="help", description="Справка")]))
    log.info("доступ: пользователей=%d, автоудаление отчётов=%d мин",
             len(config.ALLOWED_USER_IDS), config.REPORT_TTL_MIN)
    reaper = asyncio.create_task(_reaper(bot)) if config.REPORT_TTL_MIN > 0 else None
    try:
        await dp.start_polling(bot)
    finally:
        if reaper:
            reaper.cancel()


def _acquire_lock(path: str):
    """Захватить файл-замок без ожидания. Возврат: открытый файл (держать до
    конца процесса) или None, если замок держит другой процесс.

    Замок снимает ОС, когда процесс завершается любым способом, включая
    убийство из диспетчера задач, -- в отличие от pid-файла, который после
    аварии остаётся и блокирует запуск."""
    try:
        fh = open(path, "a+b")
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def main() -> None:
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(asyncio.run(_selftest()))
    lock = _acquire_lock(config.LOCK_PATH)
    if lock is None:
        sys.stderr.write(
            "Бот уже запущен в другом окне или процессе на этой машине.\n"
            "Два экземпляра с одним токеном мешают друг другу: Telegram отдаёт сообщения "
            "то одному, то другому.\nОстановите первый (Ctrl+C в его окне) и запустите снова.\n")
        raise SystemExit(1)
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        lock.close()


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
            self.deleted = []

        async def delete_message(self, chat_id, message_id):
            self.deleted.append((chat_id, message_id))
            return True

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
            self.markup = kw.get("reply_markup")

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
    # Свой %TEMP% на время самопроверки: проверки «каталоги убраны» считают
    # tginsp_*/tgclean_*, и запущенный рядом живой бот давал ложные провалы.
    saved_tempdir = tempfile.tempdir
    tempfile.tempdir = tempfile.mkdtemp(prefix="botself_tmp_")
    bot = FakeBot()
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    left = lambda: sorted(glob.glob(os.path.join(tempfile.gettempdir(), "tg[ic][nl]*_*")))  # noqa: E731
    texts = lambda ev: [m.text for k, m in ev if k == "text"]  # noqa: E731

    def _is_choice(m):
        """Сообщение с кнопками чистки (а не кнопка «Удалить отчёт» под отчётом)."""
        mk = getattr(m, "markup", None)
        return mk is not None and any(b.callback_data != "rm:all"
                                      for r in mk.inline_keyboard for b in r)
    docs = lambda ev: [d for k, d in ev if k == "doc"]  # noqa: E731
    saved = (g["clean_file"], g["_CLEAN_TIMEOUT"], dict(core_clean._CLEANERS), list(config.ALLOWED_USER_IDS))
    # Подставные пользователи самопроверки -- 7 и 8. Раньше их пускал пустой
    # список («можно всем»), но умолчание перевёрнуто на «никому», поэтому
    # теперь их надо назвать явно. Проверки отказа ниже подменяют список сами.
    config.ALLOWED_USER_IDS[:] = [7, 8]

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
        choice = next((x for k, x in ev if k == "text" and _is_choice(x)), None)
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
        check("кнопки: переключатели «Сохранить», Stealth, Paranoid, Пересоздать, Не чистить",
              btns == ["☐ Сохранить: заголовок документа", "☐ Сохранить: автора",
                       "Почистить (Stealth)", "Почистить (Paranoid)",
                       "Пересоздать (LibreOffice)", "Не чистить"], btns)
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
        # Легаси OLE2 -- фаза 4. RTF чистится, проверка выше.
        src, exp, ev, ch = await send(fx.make_rtf, "rtf")
        check("rtf: кнопки чистки показаны", ch is not None and ch.markup is not None)
        if ch is not None:
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("rtf: файл отправлен как document_*.rtf",
                  len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.rtf", d[0].name) is not None,
                  [x.name for x in d])
            check("rtf: исходные значения ушли", d and survivors(src, d[0].data, "rtf") == [])

        src, exp, ev, ch = await send(fx.make_odt, "odt")
        check("odt: кнопки чистки показаны", ch is not None and ch.markup is not None)
        if ch is not None:
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("odt: файл отправлен как document_*.odt",
                  len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.odt", d[0].name) is not None,
                  [x.name for x in d])
            check("odt: исходные значения ушли", d and survivors(src, d[0].data, "odt") == [])

        # Легаси без LibreOffice: кнопок нет, фаза названа, подсказано, чего не хватает.
        real_soffice = g["find_soffice"]
        g["find_soffice"] = lambda: None
        try:
            for maker, nm, phase in ((fx.make_doc, "doc", 4),):
                _, _, ev, ch = await send(maker, nm)
                last = texts(ev)[-1]
                check("%s без LibreOffice: нет кнопок чистки, нет файла, нет записи" % nm,
                      ch is None and not docs(ev) and not _pending
                      and not any(_is_choice(m) for k, m in ev if k == "text"))
                check("%s без LibreOffice: «НЕ почищен», фаза %d, подсказка про LibreOffice" % (nm, phase),
                      "НЕ почищен" in last and "только инспектируется" in last
                      and "фазе %d" % phase in last and "LibreOffice" in last, last)
        finally:
            g["find_soffice"] = real_soffice

        # Легаси с LibreOffice: одна кнопка «Пересоздать», на выходе docx.
        if real_soffice():
            src, _, ev, ch = await send(fx.make_doc, "doc_regen")
            btns = [b.text for r in ch.markup.inline_keyboard for b in r] if ch else []
            check("doc: только «Пересоздать» и «Не чистить», без «Сохранить»",
                  btns == ["Пересоздать (LibreOffice)", "Не чистить"], btns)
            check("doc: текст объясняет, почему только пересоздание",
                  ch is not None and "вшиты в сам" in ch.text)
            if ch is not None:
                await click(ch, "cl:regen")
                d = docs(ev)
                check("doc -> пересоздан, отправлен как document_*.docx",
                      len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.docx", d[0].name) is not None,
                      [x.name for x in d] or texts(ev)[-1][:300])
                check("doc -> исходные значения ушли", d and survivors(src, d[0].data, "docx") == [])
        else:
            print("  [пропуск] LibreOffice не найден: пересоздание doc не проверено")

        print("4. ok=False: файл не отправляется")
        def refuse(s, d, p, keep=None):
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
        def slow(s, d, p, keep=None):
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
        def boom(s, d, p, keep=None):
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
        # Возвращаем ТЕСТОВЫЙ список, а не исходный: исходный теперь означает
        # «никому», и все проверки после этой точки молча отваливались бы.
        config.ALLOWED_USER_IDS[:] = [7, 8]
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

        print("10. лимит сообщений: без него бот ловит 429 на длинном отчёте")
        short = ["a", "b", "c"]
        check("короткий отчёт не тронут", _trim_parts(short, "cli") == short)
        long_ = ["часть %d" % i for i in range(_MAX_PARTS + 40)]
        cut = _trim_parts(long_, "python -m core.inspect <файл>")
        check("длинный обрезан до лимита плюс пометка", len(cut) == _MAX_PARTS + 1, len(cut))
        check("сохранены именно первые сообщения", cut[:_MAX_PARTS] == long_[:_MAX_PARTS])
        check("пометка называет оба числа и способ получить полное",
              "первые %d" % _MAX_PARTS in cut[-1] and "из %d" % len(long_) in cut[-1]
              and "core.inspect" in cut[-1], cut[-1][:200])
        check("пометка не ломает HTML: угловые скобки экранированы",
              "&lt;файл&gt;" in cut[-1] and "<файл>" not in cut[-1], cut[-1][-200:])
        check("исходный список не изменён на месте", len(long_) == _MAX_PARTS + 40)
        # Граница ровно на лимите: обрезка на единицу раньше молча съела бы
        # последнее сообщение отчёта, а это как раз подвал с оговорками.
        edge = ["x"] * _MAX_PARTS
        check("ровно лимит -- без пометки", _trim_parts(edge, "cli") == edge)

        # Потеря сообщения обязана быть названа: недосланный кусок отчёта
        # читается как «больше находок нет», а это ровно та ошибка, из-за
        # которой пользователь сочтёт файл чистым.
        class _Flaky:
            def __init__(self, fail_on):
                self.fail_on, self.n, self.sent = fail_on, 0, []

            async def answer(self, text, **kw):
                self.n += 1
                if self.n in self.fail_on:
                    raise RuntimeError("boom")
                self.sent.append(text)

        g["_SEND_PAUSE"] = 0
        fl = _Flaky({2})
        await _send_parts(fl, ["один", "два", "три"], "python -m core.inspect <файл>")
        check("сбой одного сообщения не съел остальные", "три" in fl.sent, fl.sent)
        check("о потере сказано, и отчёт назван неполным, а не пустым",
              any("дошёл не полностью" in s and "1 из 3" in s for s in fl.sent), fl.sent)
        g["_SEND_PAUSE"] = _SEND_PAUSE

        print("11. «Пересоздать»: профиль REGENERATE, честный отказ без LibreOffice, файл при его наличии")
        real_clean, real_soffice = saved[0], core_clean.find_soffice
        calls = []

        def spy(s, d, p, keep=None):
            calls.append((p, keep))
            return real_clean(s, d, p, keep)

        g["clean_file"] = spy
        core_clean.find_soffice = lambda: None          # LibreOffice «не установлен»
        try:
            _, _, ev, ch = await send(fx.make_docx, "dx11a")
            await click(ch, "cl:regen")
            check("нажатие cl:regen вызвало clean_file ровно один раз и именно с REGENERATE",
                  len(calls) == 1 and calls[0][0] is Profile.REGENERATE, calls)
            check("нет LibreOffice: файл НЕ выдан, сказано «НЕ почищен»",
                  not docs(ev) and "НЕ почищен" in ch.text, ch.text)
            check("отказ называет причину (нужен LibreOffice), а не молча подменяет профиль",
                  any("LibreOffice" in t and ("требует" in t or "нет" in t) for t in texts(ev)),
                  [t[:160] for t in texts(ev)][-2:])
            check("ни Stealth, ни Paranoid в этом сценарии не запускались",
                  all(c[0] is Profile.REGENERATE for c in calls), calls)
            check("каталоги убраны", left() == base)
        finally:
            core_clean.find_soffice = real_soffice
        calls.clear()
        if real_soffice():
            src, _, ev, ch = await send(fx.make_docx, "dx11b")
            await click(ch, "cl:regen")
            d = docs(ev)
            check("есть LibreOffice: clean_file вызван с REGENERATE",
                  len(calls) == 1 and calls[0][0] is Profile.REGENERATE, calls)
            check("есть LibreOffice: файл выдан документом document_*.docx",
                  len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.docx", d[0].name) is not None,
                  [x.name for x in d] or texts(ev)[-1][:300])
            if d:
                check("пересозданный файл: исходные значения ушли", survivors(src, d[0].data, "dx11b") == [])
                check("подпись называет профиль Regenerate", "Regenerate" in d[0].caption, d[0].caption)
        else:
            print("  [--] LibreOffice на машине нет: ветка «файл выдан» не проверялась")
        g["clean_file"] = saved[0]

        print("12. whitelist (keep): переключатели, выбор пользователя виден в отчёте")
        calls.clear()
        g["clean_file"] = spy
        src, exp, ev, ch = await send(fx.make_pdf, "pdf12")
        key = next(iter(_pending))
        cb = await click(ch, "kp:author")
        lbl = [b.text for r in ch.markup.inline_keyboard for b in r]
        check("переключатель включился: кнопка ☑, запись цела, keep={'author'}",
              "☑ Сохранить: автора" in lbl and _pending[key].keep == {"author"}
              and cb.acks == [(None, False)], (lbl, cb.acks))
        await click(ch, "kp:author")
        check("повторное нажатие выключает", _pending[key].keep == set()
              and "☐ Сохранить: автора" in [b.text for r in ch.markup.inline_keyboard for b in r])
        cb = await click(ch, "kp:author", user_id=8)
        check("чужой переключатель отклонён, состояние не изменилось",
              cb.acks[0][1] and _pending[key].keep == set(), cb.acks)
        cb = await click(ch, "kp:nn")
        check("«kp:nn» вне ZIP и неизвестные ключи ничего не меняют", _pending[key].keep == set()
              and _pending[key].neutral is True)
        await click(ch, "kp:author")
        await click(ch, "kp:title")
        await click(ch, "cl:stealth")
        d = docs(ev)
        sent_keep = calls[0][1] if calls else None
        check("в clean_file ушли именно ключи выбранного",
              sent_keep and {"/Author", "dc:creator", "/Title"} <= set(sent_keep), sent_keep)
        pre = next((i for i, (k, m) in enumerate(ev) if k == "text" and "по вашему выбору" in m.text), -1)
        rep = next((i for i, (k, m) in enumerate(ev) if k == "text" and "Чистка метаданных" in m.text), -1)
        check("файл выдан, выбор назван ДО отчёта как выбор, а не как недочистка",
              len(d) == 1 and 0 <= pre < rep and "Это не недочистка" in ev[pre][1].text
              and "НЕ чистый" in ev[pre][1].text, (pre, rep))
        check("подпись: 📌, «по вашему выбору», «НЕ чистый», без ✅ и без «НЕ ПОЛНОСТЬЮ»",
              d and "\U0001F4CC" in d[0].caption and "по вашему выбору" in d[0].caption
              and "НЕ чистый" in d[0].caption and "✅" not in d[0].caption
              and "НЕ ПОЛНОСТЬЮ" not in d[0].caption, d[0].caption if d else None)
        check("автор действительно остался в файле (это и есть выбор)",
              d and len(survivors(src, d[0].data, "pdf12")) >= 1)
        check("оценка заметности названа и тут (cleanreport её при выживших не печатает)",
              any("Заметность чистки" in m.text for k, m in ev if k == "text" and "по вашему выбору" in m.text))
        for maker, nm, ext_ in ((fx.make_odt, "odt12", "odt"), (fx.make_rtf, "rtf12", "rtf"),
                                (fx.make_pptx, "pptx12", "pptx")):
            calls.clear()
            src, exp, ev, ch = await send(maker, nm)
            await click(ch, "kp:author")
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("%s: keep=автор -> выжившее объяснено выбором, caption 📌 (cleanreport KEPT не пишет)" % ext_,
                  len(d) == 1 and "\U0001F4CC" in d[0].caption and "✅" not in d[0].caption, d[0].caption if d else texts(ev)[-1][:200])
        # Автор, найденный НЕ в поле автора, -- недочистка, даже при включённом переключателе.
        sv_ok = core_clean.Survivor("identity", "/Info", "Автор", "Петров Пётр", "/Info > Автор")
        sv_bad = core_clean.Survivor("identity", "/Info", "Автор", "Петров Пётр", "/Info > Заголовок")
        mk2 = lambda **kw: core_clean.DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, **kw)  # noqa: E731
        check("_verdict: автор в поле автора + переключатель -> kept",
              _verdict(mk2(survived=[sv_ok]), ["author"])[0] == "kept")
        check("_verdict: тот же автор БЕЗ переключателя -> bad",
              _verdict(mk2(survived=[sv_ok]), [])[0] == "bad")
        check("_verdict: автор нашёлся в заголовке при «сохранить автора» -> bad, не выбор",
              _verdict(mk2(survived=[sv_bad]), ["author"])[0] == "bad")
        check("_verdict: выбор + посторонняя утечка -> bad",
              _verdict(mk2(survived=[sv_ok, core_clean.Survivor("environment", "x", "Путь", "C:/Users/ivan", "y > Путь")]),
                       ["author"])[0] == "bad")
        check("_verdict: выбор + неполный разбор -> warn, не kept",
              _verdict(mk2(survived=[sv_ok], gaps=["после чистки: x"]), ["author"])[0] == "warn")
        check("_verdict: просили сохранить, но ничего не осталось -> ok с упоминанием выбора",
              (lambda r: r[0] == "ok" and "заголовок документа" in r[1])(_verdict(mk2(), ["title"])))
        # Пересоздание whitelist не понимает: молчаливая подмена была бы обманом.
        calls.clear()
        src, exp, ev, ch = await send(fx.make_docx, "dx12r")
        key = next(iter(_pending))
        await click(ch, "kp:title")
        cb = await click(ch, "cl:regen")
        check("keep + «Пересоздать»: явный отказ-предупреждение, чистка не запущена",
              cb.acks and cb.acks[0][1] and "не умеет" in cb.acks[0][0] and not calls and not docs(ev), cb.acks)
        check("после отказа запись и переключатели целы -- можно снять и выбрать другое",
              key in _pending and _pending[key].keep == {"title"})
        await click(ch, "kp:title")
        # ICC у картинок
        calls.clear()
        src, exp, ev, ch = await send(fx.make_png, "png12")
        lbl = [b.text for r in ch.markup.inline_keyboard for b in r]
        check("картинка: единственный переключатель -- ICC", lbl[:2] == ["☐ Сохранить: ICC-профиль", "Почистить (Stealth)"], lbl)
        await click(ch, "kp:icc")
        await click(ch, "cl:paranoid")
        check("ICC: keep={'icc'} дошёл до чистильщика, файл выдан",
              calls and calls[0][1] == {"icc"} and len(docs(ev)) == 1, calls)
        # TTL: переключатели живут в той же записи, что и file_id
        src, exp, ev, ch = await send(fx.make_pdf, "pdf12t")
        await click(ch, "kp:title")
        for v in _pending.values():
            v.expires = 0
        cb = await click(ch, "kp:author")
        check("запись истекла вместе с переключателями: «устарела»",
              cb.acks[0][1] and "устарела" in cb.acks[0][0] and not _pending, cb.acks)
        g["clean_file"] = saved[0]

        print("13. ZIP: пакетная чистка")
        import zipfile as zf_

        def mkzip(names_makers, nm, extra=()):
            def maker(dirpath):
                out = os.path.join(dirpath, nm + ".zip")
                originals = {}
                with zf_.ZipFile(out, "w", zf_.ZIP_DEFLATED) as z:
                    for mk_ in names_makers:
                        p, _e = mk_(dirpath)
                        z.write(p, "Папка/" + os.path.basename(p))
                        originals[os.path.basename(p)] = p
                    for arc, data in extra:
                        z.writestr(arc, data)
                return out, {"orig": originals}
            return maker

        base = left()
        src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf, fx.make_png, fx.make_doc), "pack"), "zip1")
        lbl = [b.text for r in ch.markup.inline_keyboard for b in r] if ch else []
        check("ZIP: предложена пакетная чистка с числом файлов и переключателями",
              ch and "ZIP-архив" in ch.text and "файлов внутри: 4" in ch.text
              and lbl == ["☐ Сохранить: заголовок документа", "☐ Сохранить: автора", "☐ Сохранить: ICC-профиль",
                          "Имена в архиве: нейтральные", "Почистить всё (Stealth)", "Почистить всё (Paranoid)",
                          "Пересоздать всё (LibreOffice)", "Не чистить"], lbl)
        check("ZIP: на диске после предложения ничего нет", left() == base)
        cb = await click(ch, "bt:stealth")
        d = docs(ev)
        rep_txt = "\n".join(texts(ev))
        check("ZIP: ответили на нажатие сразу", cb.acks and cb.acks[0][0] == "Чищу…", cb.acks)
        check("ZIP: отчёт по каждому файлу в чате, с исходными именами",
              "ОТЧЁТ О ПАКЕТНОЙ ЧИСТКЕ" in rep_txt and "leaky.docx" in rep_txt and "probe.pdf" in rep_txt
              and "probe.png" in rep_txt, rep_txt[:300])
        check("ZIP: отказ назван и объяснён (легаси doc), а не потерян",
              "НЕ ОЧИЩЕН" in rep_txt and "причина" in rep_txt and "doc" in rep_txt.lower())
        check("ZIP: оценка заметности названа", "Заметность чистки" in rep_txt)
        arc = [x for x in d if x.name.endswith(".zip")]
        check("ZIP: выдан один архив с нейтральным именем archive_*.zip",
              len(arc) == 1 and re.fullmatch(r"archive_[0-9a-f]{6}\.zip", arc[0].name) is not None, [x.name for x in d])
        if arc:
            outp = os.path.join(work, "zip1_out.zip")
            with open(outp, "wb") as fh:
                fh.write(arc[0].data)
            with zf_.ZipFile(outp) as z:
                names = z.namelist()
                inner_report = z.read("_REPORT.txt").decode("utf-8")
                members = {n: z.read(n) for n in names}
            check("ZIP: подпись честно говорит «только очищенное», остальное НЕ включено",
                  "НЕ включены" in arc[0].caption and "3 из 4" in arc[0].caption and "✅" not in arc[0].caption,
                  arc[0].caption)
            check("ZIP: имена внутри нейтральные, исходных нет (имя -- тоже метаданные)",
                  sorted(names) == ["_REPORT.txt", "doc_0001.docx", "doc_0002.pdf", "doc_0003.png"]
                  and not any(k in " ".join(names) for k in ("leaky", "probe", "Папка")), names)
            check("ZIP: _REPORT.txt внутри архива без исходных имён",
                  "leaky" not in inner_report and "probe" not in inner_report and "№0001" in inner_report)
            check("ZIP: легаси .doc в архив не попал (нет «очищенного» оригинала)",
                  not any(n.endswith(".doc") or "NOT_CLEANED" in n for n in names))
            srcs = list(exp["orig"].values())
            for nm_, orig in zip(("doc_0001.docx", "doc_0002.pdf", "doc_0003.png"), srcs):
                check("ZIP: %s -- исходные значения ушли (повторная инспекция)" % nm_,
                      survivors(orig, members[nm_], "z_" + nm_) == [])
        check("ZIP: каталоги убраны", left() == base and not _pending)
        leak = [v for v in ("leaky", "probe") if any(v in line for line in logs)]
        check("ZIP: имена файлов не попали в логи", not leak, leak)

        # переключатель имён: исходные имена остаются, но безопасная нормализация пути остаётся
        src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf), "pack2"), "zip2")
        await click(ch, "kp:nn")
        check("ZIP: «Имена в архиве» переключилось на исходные",
              "Имена в архиве: исходные" in [b.text for r in ch.markup.inline_keyboard for b in r])
        await click(ch, "bt:paranoid")
        arc = [x for x in docs(ev) if x.name.endswith(".zip")]
        check("ZIP: все очищены -> подпись ✅ «2 из 2»",
              len(arc) == 1 and "✅" in arc[0].caption and "2 из 2" in arc[0].caption, [x.caption for x in arc])
        if arc:
            outp = os.path.join(work, "zip2_out.zip")
            with open(outp, "wb") as fh:
                fh.write(arc[0].data)
            with zf_.ZipFile(outp) as z:
                names = sorted(z.namelist())
            check("ZIP: исходные имена сохранены по просьбе", names == ["_REPORT.txt", "Папка/leaky.docx", "Папка/probe.pdf"], names)

        # keep в пакете: файл с сохранённым полем не доказан -> не в архиве, объяснено выбором
        src, exp, ev, ch = await send(mkzip((fx.make_pdf, fx.make_png), "pack3"), "zip3")
        await click(ch, "kp:author")
        await click(ch, "bt:stealth")
        arc = [x for x in docs(ev) if x.name.endswith(".zip")]
        t3 = "\n".join(texts(ev))
        check("ZIP + keep: pdf не доказан (автор оставлен по выбору) и объяснён выбором, не недочисткой",
              "вашего выбора" in t3 and "автора" in t3 and len(arc) == 1, t3[-400:])
        if arc:
            outp = os.path.join(work, "zip3_out.zip")
            with open(outp, "wb") as fh:
                fh.write(arc[0].data)
            with zf_.ZipFile(outp) as z:
                check("ZIP + keep: в архиве только png, pdf с автором не отправлен как чистый",
                      sorted(z.namelist()) == ["_REPORT.txt", "doc_0002.png"], z.namelist())
        # keep + regen в ZIP
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack3b"), "zip3b")
        await click(ch, "kp:title")
        cb = await click(ch, "bt:regen")
        check("ZIP: keep + «Пересоздать всё» -- отказ, пакет не запущен",
              cb.acks[0][1] and "не умеет" in cb.acks[0][0] and not docs(ev) and len(_pending) == 1, cb.acks)
        await click(ch, "cl:no")

        # ничего не очищено: архива нет
        src, exp, ev, ch = await send(mkzip((fx.make_doc,), "pack4", extra=(("note.txt", "просто текст"),)), "zip4")
        await click(ch, "bt:stealth")
        check("ZIP: ничего не очищено -> «Ни один файл не очищен», архив не отправлен",
              not docs(ev) and any("Ни один файл не очищен" in t for t in texts(ev)), texts(ev)[-1][:200])

        # не ZIP-архив / повреждённый: честный отказ ядра, не падение
        def bad_zip(dirpath):
            p = os.path.join(dirpath, "broken.zip")
            with open(p, "wb") as fh:
                fh.write(b"PK\x03\x04" + b"\x00" * 40)
            return p, {}
        src, exp, ev, ch = await send(bad_zip, "zip5")
        if ch is not None and any(b.callback_data == "bt:stealth" for r in ch.markup.inline_keyboard for b in r):
            await click(ch, "bt:stealth")
            check("ZIP битый: файла нет, сказано «НЕ почищен»/причина",
                  not docs(ev) and any("НЕ почищен" in (m.text or "") for k, m in ev + [("text", ch)] if k == "text"), ch.text)
        else:
            check("ZIP битый: чистка не предложена и файла нет", not docs(ev))

        # лимит отправки Bot API
        saved_lim = config.MAX_SEND_BYTES
        config.MAX_SEND_BYTES = 2048
        try:
            src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf), "pack6"), "zip6")
            await click(ch, "bt:stealth")
        finally:
            config.MAX_SEND_BYTES = saved_lim
        check("ZIP: результат больше лимита отправки -> внятное сообщение, архива в чате нет, бот не упал",
              not [x for x in docs(ev) if x.name.endswith(".zip")]
              and any("Результат не отправлен" in t and "Bot API" in t and "МБ" in t for t in texts(ev)),
              texts(ev)[-1][:300])
        check("ZIP: лимит отправки в config равен 50 МБ", config.MAX_SEND_BYTES == 50 * 1024 * 1024)

        # длинный отчёт -> документом
        g["_BATCH_CHAT_PARTS"] = 0
        try:
            src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf), "pack7"), "zip7")
            await click(ch, "bt:stealth")
        finally:
            g["_BATCH_CHAT_PARTS"] = 4
        rd = [x for x in docs(ev) if x.name.endswith(".txt")]
        check("ZIP: длинный отчёт уходит документом report_*.txt с полными именами",
              len(rd) == 1 and re.fullmatch(r"report_[0-9a-f]{6}\.txt", rd[0].name) and b"leaky.docx" in rd[0].data,
              [x.name for x in docs(ev)])

        # исключение и отказ ядра пакета
        real_batch = g["clean_batch"]

        def boom_batch(*a, **k):
            raise RuntimeError("СЕКРЕТНОЕ-ИЗ-АРХИВА")
        g["clean_batch"] = boom_batch
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack8"), "zip8")
        await click(ch, "bt:stealth")
        check("ZIP: исключение в ядре -> человеческий текст, архива нет, значение не в чат и не в логи",
              "внутренней ошибки" in ch.text and "НЕ отправлен" in ch.text and "СЕКРЕТНОЕ" not in ch.text
              and not docs(ev) and not any("СЕКРЕТНОЕ" in x for x in logs) and left() == base, ch.text)

        def refuse_batch(s, d, profile, keep=None, force=False, **kw):
            r = real_batch.__globals__["BatchResult"](src=s, dst=d, profile="stealth")
            r.err("тест: пакет отклонён")
            return r
        g["clean_batch"] = refuse_batch
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack9"), "zip9")
        await click(ch, "bt:stealth")
        check("ZIP: ok=False -> «Архив НЕ почищен» с причиной, архив не отправляется",
              "Архив НЕ почищен" in ch.text and "пакет отклонён" in ch.text and not docs(ev), ch.text)

        # таймаут: поток дорабатывает, каталог убирается после него
        def slow_batch(s, d, profile, keep=None, force=False, **kw):
            time.sleep(1.0)
            return real_batch(s, d, profile, keep, force, **kw)
        g["clean_batch"], g["_BATCH_TIMEOUT"] = slow_batch, 0.3
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack10"), "zip10")
        await click(ch, "bt:stealth")
        check("ZIP: таймаут назван, архива нет", "не уложилась" in ch.text and not docs(ev), ch.text)
        await asyncio.sleep(2.5)
        check("ZIP: после таймаута поток закончил -- каталоги убраны", left() == base, left())
        g["clean_batch"], g["_BATCH_TIMEOUT"] = real_batch, 1800

        print("14. оценка детектируемости доходит до пользователя")
        src, exp, ev, ch = await send(fx.make_docx, "dx14")
        await click(ch, "cl:stealth")
        det = [t for t in texts(ev) if "ОЦЕНКА:" in t]
        check("docx: после чистки в ответе бота есть строка ОЦЕНКА (из res.detect)", len(det) == 1, texts(ev)[-1][:200])
        src, exp, ev, ch = await send(fx.make_pdf, "pdf14")
        await click(ch, "cl:stealth")
        check("pdf: после чистки в ответе бота есть строка ОЦЕНКА", any("ОЦЕНКА:" in t for t in texts(ev)))
        check("оценка не продублирована ботом там, где её уже печатает cleanreport",
              sum("Заметность чистки" in t for t in texts(ev)) == 0, [t[:80] for t in texts(ev)])

        print("15. отчёты удаляются: кнопка, /clear, таймер; файл-результат не трогается")
        _reports.clear()
        bot.deleted.clear()
        src, exp, ev, ch = await send(fx.make_pdf, "pdf15")
        rep_msgs = [m for k, m in ev if k == "text" and m.markup is _DELETE_KB]
        check("под последним сообщением отчёта -- кнопка «Удалить отчёт», ровно одна",
              len(rep_msgs) == 1, len(rep_msgs))
        tracked = set(_reports)
        check("сообщения отчёта запомнены для удаления", len(tracked) >= 1, len(tracked))
        await click(ch, "cl:stealth")
        n_before = len(_reports)
        check("отчёт о чистке тоже запомнен", n_before > len(tracked), (n_before, len(tracked)))
        cb = FakeCb(rep_msgs[0], "rm:all")
        await on_delete_reports(cb)
        check("кнопка удалила все отчёты чата и забыла их",
              not _reports and set(bot.deleted) >= tracked and cb.acks, (len(_reports), cb.acks))
        check("почищенный файл не удалялся (он не в реестре)", len(docs(ev)) == 1)
        cb = FakeCb(rep_msgs[0], "rm:all", user_id=99)
        bot.deleted.clear()
        await on_delete_reports(cb)
        check("чужой не может удалить отчёты", not bot.deleted)

        _reports.clear()
        bot.deleted.clear()
        await send(fx.make_pdf, "pdf15b")
        ev = []
        m = FakeMsg(bot, ev)
        await on_clear(m)
        check("/clear удалил отчёты и сказал сколько",
              bot.deleted and not _reports and any("Удалено сообщений" in t for t in texts(ev)), texts(ev))

        _reports.clear()
        _track(SimpleNamespace(chat=SimpleNamespace(id=5), message_id=1))
        _reports[(5, 1)] -= 3600
        _track(SimpleNamespace(chat=SimpleNamespace(id=5), message_id=2))
        bot.deleted.clear()
        n = await _delete_reports(bot, cutoff=time.monotonic() - 30 * 60)
        check("таймер удаляет только старше срока", n == 1 and bot.deleted == [(5, 1)]
              and (5, 2) in _reports, (n, bot.deleted))
        _track(None)
        check("_track(None) не падает", True)
        _reports.clear()

        print("16. /status, /help, замок единственного экземпляра")
        ev = []
        await on_status(FakeMsg(bot, ev))
        st = texts(ev)[-1] if texts(ev) else ""
        check("/status называет LibreOffice, exiftool и автоудаление",
              "LibreOffice" in st and "exiftool" in st and "Автоудаление" in st, st)
        check("/status не раскрывает путей", ":\\" not in st and "/usr" not in st, st)
        ev = []
        await on_help(FakeMsg(bot, ev))
        h = texts(ev)[-1] if texts(ev) else ""
        check("справка собрана без «{ttl}» и называет /clear", "{" not in h and "/clear" in h, h[-300:])
        check("в текстах бота нет буквального \\n (его видно в Telegram)",
              not any("\\n" in t for t in (HELP_TEXT, CHOICE_TEXT, LEGACY_TEXT, BATCH_TEXT)))
        lock_path = os.path.join(work, "t.lock")
        first = _acquire_lock(lock_path)
        second = _acquire_lock(lock_path)
        check("замок: первый взял, второй -- нет", first is not None and second is None)
        if first:
            first.close()
        third = _acquire_lock(lock_path)
        check("замок: после закрытия снова свободен", third is not None)
        if third:
            third.close()
    finally:
        g["clean_file"], g["_CLEAN_TIMEOUT"] = saved[0], saved[1]
        core_clean._CLEANERS.clear()
        core_clean._CLEANERS.update(saved[2])
        config.ALLOWED_USER_IDS[:] = saved[3]
        config.MAX_SEND_BYTES = 50 * 1024 * 1024
        log.removeHandler(grab)
        shutil.rmtree(work, ignore_errors=True)
        private_tmp, tempfile.tempdir = tempfile.tempdir, saved_tempdir
        shutil.rmtree(private_tmp, ignore_errors=True)
        _pending.clear()
        _reports.clear()

    print("\nИТОГО: %d проверок, %d провалено" % (total[0], len(fails)))
    for f in fails:
        print("  ПРОВАЛ: " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    main()
