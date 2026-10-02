# -*- coding: utf-8 -*-
"""Настройки бота.

Импорт этого модуля НЕ должен требовать токен -- при отсутствии TG_BOT_TOKEN
TG_BOT_TOKEN просто будет None. Требовать токен (и завершать процесс) имеет
право только точка входа (bot.main()), а не импорт модуля.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")

# Жёсткий лимит Telegram Bot API на скачивание файла ботом (не наш лимит).
MAX_FILE_BYTES = 20 * 1024 * 1024

# Бот личный/для узкого круга. Пустой список = доступ разрешён всем,
# кто знает бота; заполните своими user_id, чтобы ограничить круг.
ALLOWED_USER_IDS: list[int] = []

# Необязательное обогащение через exiftool: сначала PATH, потом локальная
# копия в ./tools. Отсутствие обоих -- штатный режим, а не ошибка.
EXIFTOOL_CANDIDATES = ("exiftool", os.path.join(ROOT, "tools", "exiftool.exe"))


#  BOM -> кодировка. Windows PowerShell 5.1 на `>` и `Out-File` пишет UTF-16 LE,
#  поэтому созданный там .env приезжает не в UTF-8. Читать его как UTF-8 значит
#  получить UnicodeDecodeError трейсбеком вместо внятного «токен не найден» --
#  именно так этот проект и спотыкнулся при первом живом запуске.
_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def _decode_env(raw: bytes) -> str:
    """Байты .env -> текст. Кодировку определяем по BOM, иначе пробуем UTF-8,
    затем UTF-16 без BOM (признак -- NUL-байты), затем cp1251."""
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc, "replace").replace("﻿", "")
    try:
        text = raw.decode("utf-8")
        # UTF-16 без BOM декодируется как UTF-8 УСПЕШНО: NUL -- валидный
        # символ UTF-8. На выходе мусор вида 'T\x00G\x00_\x00', и запасной
        # путь ниже без этой проверки недостижим.
        if "\x00" not in text:
            return text
    except UnicodeDecodeError:
        pass
    # ponytail: берём первую кодировку, которая декодировалась. UTF-16 BE и
    # UTF-32 БЕЗ BOM так не опознаются (decode как utf-16-le на них не бросает,
    # а отдаёт мусор), и это осознанно: ни один редактор и ни одна оболочка
    # такого .env не пишут, а деградация безопасная -- токен просто не найдётся
    # и пользователь увидит инструкцию, а не трейсбек. Понадобится -- выбирать
    # по позициям NUL-байтов (чётные/нечётные), а не по первому успеху.
    if b"\x00" in raw:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return raw.decode("cp1251", "replace")


def _parse_dotenv(path: str) -> dict:
    """Простой парсер .env: строки KEY=VALUE, без python-dotenv.

    Никогда не бросает: нечитаемый или кривой .env -- это пустой словарь и
    затем честное сообщение про отсутствующий токен, а не трейсбек.
    """
    values: dict = {}
    try:
        with open(path, "rb") as fh:
            text = _decode_env(fh.read())
    except OSError:
        return values
    except Exception:
        return values
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _load_token() -> str | None:
    token = os.environ.get("TG_BOT_TOKEN")
    if token:
        return token
    return _parse_dotenv(ENV_PATH).get("TG_BOT_TOKEN") or None


TG_BOT_TOKEN = _load_token()


def require_token() -> str:
    """Вызывается только из точки входа. Без трейсбека объясняет, что делать."""
    if TG_BOT_TOKEN:
        return TG_BOT_TOKEN
    sys.stderr.write(
        "Не найден TG_BOT_TOKEN.\n\n"
        "Задайте его одним из способов:\n"
        "  1) переменная окружения:\n"
        "       PowerShell:  $env:TG_BOT_TOKEN = \"123:ABC...\"\n"
        "       Bash:        export TG_BOT_TOKEN=123:ABC...\n"
        "  2) файл .env в корне проекта ({env}):\n"
        "       TG_BOT_TOKEN=123:ABC...\n".format(env=ENV_PATH)
    )
    sys.exit(1)
