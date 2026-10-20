# -*- coding: utf-8 -*-
"""Bot settings.

Importing this module must NOT require a token: without TG_BOT_TOKEN,
TG_BOT_TOKEN is simply None. Only the entry point (bot.main()) may demand
the token and exit, not a module import.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(ROOT, ".env")

# Hard Telegram Bot API limit on a bot downloading a file (not our limit).
MAX_FILE_BYTES = 20 * 1024 * 1024
# And a hard limit on a bot SENDING a file: a batch-cleaning result larger
# than this can't go to the chat.
MAX_SEND_BYTES = 50 * 1024 * 1024

# Who may use the bot.
#
# An empty list used to mean "anyone who knows the bot", contradicting the
# owner-only design. The consequences weren't theoretical: anyone who found
# the bot could push files through the parsers and LibreOffice ON THE OWNER'S
# MACHINE and make it fetch an address of their choice (see external-link
# defusing in clean_regen). So the default is flipped: NOBODY until the owner
# identifies themselves. The bot doesn't stay silent, though: it tells a
# stranger their own user_id and what to do with it.
#
# The owner is set in .env (or an environment variable):
#     TG_OWNER_ID=123456789
# Several people, comma-separated:
#     TG_ALLOWED_IDS=123456789,987654321
def _ids(raw) -> list:
    out = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if re.fullmatch(r"-?[0-9]+", part):
            out.append(int(part))
    return out


def _load_ids() -> list:
    env = _parse_dotenv(ENV_PATH)
    ids = _ids(os.environ.get("TG_OWNER_ID") or env.get("TG_OWNER_ID"))
    ids += _ids(os.environ.get("TG_ALLOWED_IDS") or env.get("TG_ALLOWED_IDS"))
    return sorted(set(ids))


# The values themselves are computed below, after the .env parser.

# Optional enrichment via exiftool: PATH first, then a local copy in ./tools.
# Having neither is normal operation, not an error.
EXIFTOOL_CANDIDATES = ("exiftool", os.path.join(ROOT, "tools", "exiftool.exe"))


#  BOM -> encoding. Windows PowerShell 5.1 writes UTF-16 LE for `>` and
#  `Out-File`, so a .env created there isn't UTF-8. Reading it as UTF-8 means a
#  UnicodeDecodeError traceback instead of a clear "token not found", which is
#  exactly how this project tripped on its first live run.
_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)


def _decode_env(raw: bytes) -> str:
    """.env bytes -> text. The encoding is detected by BOM, otherwise try UTF-8,
    then BOM-less UTF-16 (sign: NUL bytes), then cp1251."""
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            return raw.decode(enc, "replace").replace("﻿", "")
    try:
        text = raw.decode("utf-8")
        # BOM-less UTF-16 decodes as UTF-8 SUCCESSFULLY: NUL is a valid UTF-8
        # character. The result is garbage like 'T\x00G\x00_\x00', and the
        # fallback below is unreachable without this check.
        if "\x00" not in text:
            return text
    except UnicodeDecodeError:
        pass
    # ponytail: take the first encoding that decodes. UTF-16 BE and UTF-32
    # WITHOUT a BOM aren't recognized this way (decoding them as utf-16-le
    # doesn't raise, it returns garbage), and that's deliberate: no editor or
    # shell writes such a .env, and the failure is safe: the token just isn't
    # found and the user sees instructions, not a traceback. If ever needed,
    # choose by NUL byte positions (even/odd) instead of the first success.
    if b"\x00" in raw:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
    return raw.decode("cp1251", "replace")


def _parse_dotenv(path: str) -> dict:
    """Minimal .env parser: KEY=VALUE lines, no python-dotenv.

    Never raises: an unreadable or malformed .env is an empty dict followed
    by an honest message about the missing token, not a traceback.
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

ALLOWED_USER_IDS: list[int] = _load_ids()

# An empty list no longer means "everyone". If the owner hasn't identified
# themselves, the bot serves nobody: a privacy tool that is open by default
# is more dangerous than an inconvenient one.
ACCESS_CONFIGURED: bool = bool(ALLOWED_USER_IDS)


def _load_ttl() -> int:
    """After how many minutes the bot deletes its own reports from the chat. 0 means never.

    A report carries found values (spoilered, but stored as text on Telegram's
    servers), so the default is to delete. A bot can only delete its own
    messages within 48 hours, hence the cap.
    """
    raw = os.environ.get("REPORT_TTL_MIN") or _parse_dotenv(ENV_PATH).get("REPORT_TTL_MIN")
    try:
        return max(0, min(int(str(raw).strip()), 47 * 60))
    except (TypeError, ValueError):
        return 60


REPORT_TTL_MIN: int = _load_ttl()


def _load_lang() -> str:
    """Default language of the bot's replies: "ru" (default) or "en".

    Each user can switch with /lang; that choice is stored in LANG_PATH.
    """
    raw = os.environ.get("BOT_LANG") or _parse_dotenv(ENV_PATH).get("BOT_LANG") or ""
    lang = raw.strip().lower()
    return lang if lang in ("ru", "en") else "ru"


BOT_LANG: str = _load_lang()

# Per-user language choice (/lang): local state next to the bot, git-ignored.
LANG_PATH = os.path.join(ROOT, ".bot_lang.json")

# Lock file: a second bot instance with the same token won't start.
# Otherwise Telegram hands messages to one instance or the other, and replies
# come from the one with stale settings.
LOCK_PATH = os.path.join(ROOT, ".bot.lock")


def require_token() -> str:
    """Called only from the entry point. Explains what to do, without a traceback."""
    if TG_BOT_TOKEN:
        return TG_BOT_TOKEN
    sys.stderr.write(
        "TG_BOT_TOKEN not found.\n\n"
        "Set it in one of these ways:\n"
        "  1) environment variable:\n"
        "       PowerShell:  $env:TG_BOT_TOKEN = \"123:ABC...\"\n"
        "       Bash:        export TG_BOT_TOKEN=123:ABC...\n"
        "  2) a .env file in the project root ({env}):\n"
        "       TG_BOT_TOKEN=123:ABC...\n".format(env=ENV_PATH)
    )
    sys.exit(1)
