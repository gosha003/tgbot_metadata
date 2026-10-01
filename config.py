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


def _parse_dotenv(path: str) -> dict:
    """Простой парсер .env: строки KEY=VALUE, без python-dotenv."""
    values: dict = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
    except OSError:
        pass
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
