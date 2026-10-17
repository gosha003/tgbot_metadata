"""Language of user-facing text.

The code is written in English. Every string a user can see goes through tr();
module-level constants are marked with N_() and passed through tr() where they
are shown. Russian versions live in core/i18n_ru.py, keyed by the English text.

The language is a ContextVar: the bot sets it per request with use_lang(), and
asyncio.to_thread() carries it into the worker thread that runs inspection and
cleaning. The CLI and the tests never set it, so they stay in English.

Only presentation changes with the language. Logic must never compare a
translated string with an untranslated one; when it has to compare (the oracle's
presence markers, for example), it compares against both languages.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from core.i18n_ru import RU

LANGS = ("ru", "en")
DEFAULT_BOT_LANG = "ru"

_lang: ContextVar[str] = ContextVar("lang", default="en")

# Russian has three plural forms; English has two. Keyed by the English singular.
RU_PLURAL = {
    "finding": ("находка", "находки", "находок"),
    "critical": ("критичная", "критичных", "критичных"),
    "parse error": ("ошибка разбора", "ошибки разбора", "ошибок разбора"),
    "file": ("файл", "файла", "файлов"),
    "message": ("сообщение", "сообщения", "сообщений"),
}


def tr(text: str) -> str:
    """Translate an English source string into the current language."""
    if _lang.get() == "ru":
        return RU.get(text, text)
    return text


def N_(text: str) -> str:
    """Mark a constant for translation without translating it yet (gettext idiom)."""
    return text


def both(text: str) -> tuple:
    """The string in every language, for logic that must match either."""
    return (text, RU.get(text, text))


def plural(n: int, one: str, many: str) -> str:
    """Pick the plural form of a word for n in the current language."""
    if _lang.get() == "ru" and one in RU_PLURAL:
        f1, f2, f5 = RU_PLURAL[one]
        n = abs(n)
        if n % 10 == 1 and n % 100 != 11:
            return f1
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return f2
        return f5
    return one if n == 1 else many


def get_lang() -> str:
    return _lang.get()


@contextmanager
def use_lang(lang: str):
    """Run a block with the given language; unknown codes fall back to English."""
    token = _lang.set(lang if lang in LANGS else "en")
    try:
        yield
    finally:
        _lang.reset(token)
