# -*- coding: utf-8 -*-
"""Телеграм-оболочка, фаза 0: только показывает метаданные, файл не меняет.

Ядро разбора (core/inspect.py) пишет параллельный агент; этот модуль
полагается только на контракт `core.inspect.inspect_file` и
`core.report.render_telegram` / `summary_line`. core/inspect.py может
временно отсутствовать -- импорт бота при этом не должен падать
(см. try/except ниже и demo() в конце файла).

ПРИВАТНОСТЬ. Это инструмент приватности, и его собственные логи --
потенциальная утечка того же типа, для защиты от которой он написан.
Поэтому в логи попадают ТОЛЬКО: формат файла, его размер, число находок
и длительность разбора. Никогда -- имя файла, его содержимое или
содержимое находок (значения полей, тексты ошибок разбора).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Document, Message

import config
from core.report import render_telegram

try:
    from core.inspect import inspect_file
except ImportError:
    # Контракт ещё не готов у соседнего агента -- бот должен оставаться
    # импортируемым и рабочим (кроме собственно разбора) без него.
    inspect_file = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tgbot_metadata")

router = Router(name="metadata")

# Инспекция блокирующая и может быть долгой; бот личный -- одна задача
# за раз, без очередей и метрик.
_SEMAPHORE = asyncio.Semaphore(1)
_INSPECT_TIMEOUT = 120  # секунд, см. asyncio.wait_for ниже

# ponytail: грубая эвристика "похоже на авто-имя" по алфавиту символов
# (буквы/цифры/подчёркивание/дефис), а не по смыслу слова. Усложнять до
# словаря типичных личных имён -- только если эвристика реально подведёт.
_NEUTRAL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$")

HELP_TEXT = (
    "Это инспектор метаданных документов -- <b>фаза 0, только просмотр</b>.\n\n"
    "Пришлите документ (docx/xlsx/pptx, PDF, doc/xls/ppt, ODF, RTF, "
    "картинку) -- бот покажет, что сейчас лежит внутри: автор, приложение, "
    "даты, локальные пути, GPS в фото и т.п. Файл при этом <b>не меняется "
    "и не сохраняется</b> -- чистка появится в следующих фазах.\n\n"
    "Главное условие: отправляйте файл через скрепку → <b>Файл</b> "
    "(Document), а не как «Фото». Если отправить картинку как фото, "
    "Telegram пережмёт её раньше, чем бот увидит файл, и метаданные "
    "будут уже потеряны -- смотреть будет не на чём."
)


def _allowed(message: Message) -> bool:
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

            report = await asyncio.wait_for(
                asyncio.to_thread(inspect_file, file_path),
                timeout=_INSPECT_TIMEOUT,
            )

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

        await status.delete()
        for part in parts:
            await message.answer(part)

        if not _looks_neutral(doc.file_name or ""):
            await message.answer(
                "Напоминание: само <b>имя файла</b> -- тоже метаданные. "
                "Оно не похоже на техническое авто-имя, так что при "
                "дальнейшей передаче файла его стоит поменять отдельно."
            )
    except asyncio.TimeoutError:
        await status.edit_text(
            "Разбор не уложился в {}с и был прерван -- файл слишком "
            "сложный или повреждён для фазы 0.".format(_INSPECT_TIMEOUT)
        )
    except Exception:
        # Приватность: фиксируем только факт сбоя, без имени и содержимого файла.
        log.exception("сбой при разборе документа")
        try:
            await status.edit_text(
                "Не получилось разобрать файл из-за внутренней ошибки. "
                "Файл не изменён и не сохранён на сервере бота."
            )
        except Exception:
            pass
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def _run() -> None:
    token = config.require_token()
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
