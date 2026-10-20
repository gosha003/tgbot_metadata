# -*- coding: utf-8 -*-
"""Telegram front end: inspection, cleaning (Stealth / Paranoid / Regenerate),
batch cleaning of ZIPs, deleting reports from the chat.

Inspection never changes the file. Cleaning happens only when a button is
pressed explicitly and only for formats that have a cleaner
(cleanreport.CLEANABLE), plus legacy doc/xls/ppt through regeneration when
LibreOffice is installed. For everything else the bot says outright that the
file is NOT cleaned: handing over a file the user believes is cleaned when it
isn't is the worst outcome for this project.

One instance per machine: main() holds the lock file config.LOCK_PATH.

Cleaning flow: inspection report -> a message with profiles and buttons (only
the file_id is remembered in process memory, not the file itself) -> button
press -> the file is downloaded again, clean_file() runs in a thread with a
timeout -> two results: the cleaning report (cleanreport) and the file itself
as a DOCUMENT with a neutral name. When ok=False the file is never sent.

The core (core/inspect.py, core/clean.py) may be temporarily missing --
importing the bot must not fail because of it (see the try/except blocks
below). Self-test without a token:
    PYTHONIOENCODING=utf-8 python bot.py --selftest

LANGUAGE. Every string the user sees goes through tr() (core/i18n.py). Each
update is handled in its sender's language: _LangMiddleware wraps every handler
in use_lang() (config.BOT_LANG by default, /lang to switch, remembered in
config.LANG_PATH), and asyncio.to_thread() carries the language into the worker
threads that run inspection and cleaning. The self-test and the CLI run in English.

PRIVACY. This is a privacy tool, and its own logs are a potential leak of the
very kind it exists to prevent. So the logs contain ONLY: the file format, its
size, the number of findings or actions, the profile, the outcome and the
duration. Never the file name, its contents, the contents of findings or the
text of an exception (it may include values from the file).
"""

from __future__ import annotations

import asyncio
import functools
import html
import json
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

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
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
    # The core contract may not be ready yet -- the bot must stay importable and
    # working (except for the parsing itself) without it.
    inspect_file = None

try:
    from core.clean import clean_file
except ImportError:
    # No cleaning module or its dependency (pikepdf): the bot stays an inspector,
    # says honestly that there is no cleaning, and shows no buttons.
    clean_file = None

try:
    from core.clean_batch import clean_batch, render_report as render_batch_report
except ImportError:
    # No batch module: single-file cleaning works, and the bot honestly declines
    # ZIPs.
    clean_batch = None

try:
    from core.clean_regen import OUTPUT_FMT as _REGEN_FMT, find_soffice
except ImportError:
    _REGEN_FMT = {}

    def find_soffice():
        return None

try:
    from core.insp_image import _exiftool_exe
except ImportError:
    def _exiftool_exe():
        return None
from core.i18n import LANGS, N_, tr, use_lang

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tgbot_metadata")

router = Router(name="metadata")

# --- language of the replies -------------------------------------------------
# Per-user choice (/lang), persisted in config.LANG_PATH. Only user ids and a
# two-letter code are stored, nothing about files.
_LANGS: dict = {}


def _load_langs() -> None:
    try:
        with open(config.LANG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        _LANGS.update({int(k): v for k, v in data.items() if v in LANGS})
    except (OSError, ValueError, AttributeError, TypeError):
        pass  # no file yet, or a damaged one: everyone gets the default


def _save_langs() -> None:
    tmp = config.LANG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({str(k): v for k, v in _LANGS.items()}, fh)
        os.replace(tmp, config.LANG_PATH)
    except OSError as exc:
        log.warning("could not save the language choice: %s", type(exc).__name__)


def _lang_of(user) -> str:
    return _LANGS.get(getattr(user, "id", None), config.BOT_LANG)


class _LangMiddleware(BaseMiddleware):
    """Runs every handler in the language of the user who sent the update.

    The language is a ContextVar: tasks the handler starts and the worker
    threads of asyncio.to_thread() inherit it, so the reports built there come
    out in the same language.
    """

    async def __call__(self, handler, event, data):
        with use_lang(_lang_of(getattr(event, "from_user", None))):
            return await handler(event, data)


router.message.middleware(_LangMiddleware())
router.callback_query.middleware(_LangMiddleware())

# Inspection and cleaning are blocking and can be slow; the bot is personal --
# one task at a time, no queues, no metrics.
_SEMAPHORE = asyncio.Semaphore(1)
_INSPECT_TIMEOUT = 120  # seconds, see _in_thread below
# clean_file = cleaning + two inspections (before and after) + two sha256
# checks, so the ceiling is three times that of a single inspection.
_CLEAN_TIMEOUT = 360
# A batch = up to MAX_FILES full cleanings in a row (or LibreOffice runs for
# "Regenerate"), so the ceiling is an order of magnitude above a single file's.
_BATCH_TIMEOUT = 1800
# A batch report shorter than this many messages goes to the chat; a longer one
# goes as a document.
_BATCH_CHAT_PARTS = 4

# ponytail: button state lives in process memory and is lost when the bot
# restarts (a press after a restart = "button expired"). Enough for a
# single-user bot; a DB/Redis if a multi-user mode ever appears.
_PENDING_TTL = 30 * 60  # seconds
_PENDING_MAX = 50       # entries; the oldest are evicted

# How many report messages the bot is willing to send at once. The renderer
# slices the report at Telegram's 4096-character limit and does not cap the
# number of messages: on a pathological input with hundreds of findings that
# comes to several dozen messages, and Telegram answers such a flood with 429
# and temporarily bans the bot -- so the bot breaks itself, and on exactly the
# file that is the most interesting. The handler sets the limit: only it knows
# that the recipient is a chat, not a console.
_MAX_PARTS = 12
# Pause between messages. A private chat tolerates about one message per
# second; 0.35 s is a compromise between "don't wait a minute on a long report"
# and "don't collect a 429". The delay only kicks in on genuinely long reports.
_SEND_PAUSE = 0.35

# ponytail: a crude "looks like an auto-generated name" heuristic based on the
# character alphabet (letters/digits/underscore/hyphen), not on the meaning of
# the word. Escalate to a dictionary of typical personal names only if the
# heuristic actually fails.
_NEUTRAL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,40}$")

_PROFILES = {"cl:stealth": Profile.STEALTH, "cl:paranoid": Profile.PARANOID,
             "cl:regen": Profile.REGENERATE, "bt:stealth": Profile.STEALTH,
             "bt:paranoid": Profile.PARANOID, "bt:regen": Profile.REGENERATE}

# User whitelist (keep). The keys are exactly what the cleaners understand:
# PDF matches label/location ("/Author", "dc:creator"), OOXML -- "dc:title",
# ODF and RTF -- local names ("creator", "author"), images -- "icc".
# Each entry is: caption, keep keys, and substrings of the field labels in the
# inspector's report, in Russian and English (the report is rendered in either
# language). The substrings are needed to tell a value that survived BY CHOICE
# from one that survived through a cleaning shortfall.
_KEEP_OPTS = {
    "title": (N_("document title"), {"/Title", "dc:title", "title"},
              ("заголовок", "название", "title")),
    "author": (N_("the author"), {"/Author", "dc:creator", "author", "creator", "initial-creator"},
               ("автор", "creator", "author", "кто сохранял", "last saved by", "lastmodifiedby")),
    "icc": (N_("ICC profile"), {"icc"}, ()),
}
_IMG_FMT = ("jpeg", "png", "webp", "gif")
# Legacy OLE2: can't be cleaned in place at all, only regenerated through
# LibreOffice (output -- docx/xlsx/pptx). Hence they get a single button.
_LEGACY = ("doc", "xls", "ppt")
_EXT = {"pdf": ".pdf", "jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif",
        "docx": ".docx", "xlsx": ".xlsx", "pptx": ".pptx", "rtf": ".rtf",
        "odt": ".odt", "ods": ".ods", "odp": ".odp", "zip": ".zip",
        "doc": ".doc", "xls": ".xls", "ppt": ".ppt"}
# Basis of the neutral name: a generic word by file type, with no image as the
# default -- otherwise a docx would go out named image_*.docx.
_STEM = {"pdf": "document", "docx": "document", "xlsx": "spreadsheet",
         "pptx": "presentation", "rtf": "document", "odt": "document",
         "ods": "spreadsheet", "odp": "presentation", "zip": "archive",
         "doc": "document", "xls": "spreadsheet", "ppt": "presentation"}

HELP_TEXT = (
    N_("This is a metadata inspector and cleaner for documents.\n\nSend a document "
       "(docx/xlsx/pptx, PDF, doc/xls/ppt, ODF, RTF, an image) -- the bot will show what is "
       "inside right now: author, application, dates, local paths, GPS in photos and so on. The "
       "file is <b>not changed and not stored</b>.\n\nFor <b>PDF, images (JPEG, PNG, WebP, GIF), "
       "docx/xlsx/pptx, RTF and ODF</b> cleaning buttons appear after the report: <b>Stealth</b> "
       "(removes identity and environment, keeps the application and version) or <b>Paranoid</b> "
       "(removes everything, but the result looks processed). The bot sends a cleaning report "
       "and a new copy of the file as a document, with a neutral name.\n\nEven Stealth can't "
       "make docx/xlsx/pptx fully undetectable: an empty author is itself a sign of cleaning. "
       "The bot says so honestly in the report rather than keeping quiet.\n\nBefore cleaning you "
       "can use the \"Keep\" toggles to leave the title, the author or the ICC profile in place. "
       "That is your deliberate choice: a file with the author kept <b>will not be clean</b>, "
       "and the bot will say so.\n\nSend a <b>ZIP</b> -- the bot will clean every document "
       "inside and return an archive with only the cleaned and verified ones (a report for each "
       "file will be in the chat).\n\nOld <b>doc/xls/ppt</b> can't be cleaned in place at all: "
       "the metadata and the deleted text of earlier versions are embedded in the container "
       "itself. They get a single button -- <b>Regenerate</b> through LibreOffice, producing "
       "docx/xlsx/pptx. If LibreOffice isn't on the machine, the bot says so and leaves the file "
       "alone.\n\nAutomatic deletion of reports from the chat: {ttl}. To delete right away use "
       "the \"Delete report\" button under a report or the /clear command. Cleaned files are not "
       "deleted. /status shows what is available on this machine.\n\nThe key requirement: send "
       "the file via the paperclip → <b>File</b> (Document), not as a \"Photo\". If you send an "
       "image as a photo, Telegram recompresses it before the bot sees the file, and the "
       "metadata is already lost -- there will be nothing to look at.")
)

CHOICE_TEXT = (
    N_("\U0001F9F9 <b>Clean the file?</b> The original file will not change: the bot will send a "
       "new copy (as a document, not a photo -- otherwise Telegram would recompress it) and a "
       "cleaning report.\n\n• <b>Stealth</b> (recommended) -- removes identity and environment: "
       "author, paths, printer names, GPS, serial numbers. It keeps the application and its "
       "version: empty metadata gives away on its own that the file was cleaned.\n• "
       "<b>Paranoid</b> -- removes everything, including the application and dates. Cleaner, but "
       "the result looks processed.\n{regen}\n<i>\"Keep\" toggles: the field stays as it was, so "
       "the file will NOT be clean -- the report will call this your choice.</i>")
)

# Only for formats that LibreOffice can re-save (clean_regen.OUTPUT_FMT).
# It doesn't regenerate PDFs and images: the button there would lead to a
# guaranteed refusal.
REGEN_BULLET = (
    N_("• <b>Regenerate</b> -- save anew through LibreOffice. There is no cleaning trace because "
       "nobody cleaned the file, but the application becomes LibreOffice and the layout may "
       "change. No LibreOffice -- a refusal, not a different cleaning. The \"Keep\" toggles "
       "don't work with regeneration: the bot will refuse rather than silently ignore them.\n")
)

LEGACY_TEXT = (
    N_("\U0001F9F9 <b>Clean the file?</b> The old Office format can't be cleaned in place: the "
       "metadata and the deleted text of earlier versions are embedded in the container itself. "
       "There is only one honest way -- <b>Regenerate</b>: LibreOffice will save the document "
       "anew in a modern format (docx/xlsx/pptx) without the edit history and without your "
       "name.\n\n<i>The application will become LibreOffice and the layout may change a little. "
       "The original file does not change: the bot will send a new copy.</i>")
)

BATCH_TEXT = (
    N_("📦 <b>This is a ZIP archive: {n}</b>. The bot will clean every file inside (nested "
       "archives are not unpacked) and send a new archive <b>with only those whose cleanliness "
       "is proven</b> by re-inspection. Anything not cleaned does not go into the archive: it "
       "can't be handed over as clean by mistake. A report for each file is in the chat.\n\n• "
       "<b>Stealth</b> / <b>Paranoid</b> -- as for a single file.\n• <b>Regenerate</b> -- "
       "through LibreOffice, file by file: slow.\n• File names are metadata too: \"neutral\" "
       "ones -- doc_0001.docx and so on (the mapping of numbers to names will exist only in the "
       "chat).\n• \"Keep\": a file with a kept field is not considered clean and will not go "
       "into the archive -- clean such files one at a time.")
)


def _allowed(message: Message | CallbackQuery) -> bool:
    """Only those the owner has named explicitly.

    An empty list used to mean "everyone is allowed". That contradicted the
    owner-only design and was unsafe: anyone who found the bot could upload files into parsing and
    into LibreOffice on the owner's machine. Now unconfigured access = closed.
    """
    user = message.from_user
    return bool(user) and user.id in config.ALLOWED_USER_IDS


def _access_hint(user_id) -> str:
    """What to reply to someone who isn't on the list. A person usually doesn't know
    their own user_id, so we tell them what it is and what to do about it."""
    if not config.ACCESS_CONFIGURED:
        return (tr("Access to the bot is not configured yet, so it serves no one.\n\nYour "
                   "Telegram user_id: <code>%s</code>\n\nIf this is your bot, add this line to "
                   "the <code>.env</code> file next to "
                   "<code>bot.py</code>:\n<code>TG_OWNER_ID=%s</code>\nand restart the bot.") % (user_id, user_id))
    return (tr("Access to this bot is restricted. Your user_id: <code>%s</code>") % user_id)


def _looks_neutral(filename: str) -> bool:
    """The file name is metadata too. "Neutral" means resembling an auto-generated name."""
    stem = os.path.splitext(filename)[0] if filename else ""
    if not stem:
        return True
    return bool(_NEUTRAL_NAME_RE.match(stem))


def _neutral_name(fmt: str) -> str:
    """Output file name: contains nothing from the original (the name is metadata,
    Telegram keeps it). A random tail so that files don't get mixed up."""
    return "%s_%s%s" % (_STEM.get(fmt, "image"), secrets.token_hex(3),
                        _EXT.get(fmt, ".bin"))


# ============================================================================
#  Blocking work in a thread
# ============================================================================


class _Timeout(Exception):
    """The thread did not finish in time. The directory has been handed to the
    thread for self-cleanup."""


def _rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


async def _in_thread(tmp_dir: str, timeout: float, func, *args):
    """func(*args) in a thread with a timeout. A thread can't be interrupted, it
    finishes on its own. So on a timeout the directory is not deleted right away
    but when the thread finishes: otherwise on Windows a busy file would not be
    removed and the input document would stay on disk forever. After _Timeout the
    caller does NOT touch the directory."""
    fut = asyncio.ensure_future(asyncio.to_thread(func, *args))
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout)
    except asyncio.TimeoutError:
        def _cleanup(f):
            if not f.cancelled():
                f.exception()  # retrieve the exception so asyncio doesn't complain
            _rmtree(tmp_dir)

        fut.add_done_callback(_cleanup)
        raise _Timeout from None


async def _quiet(coro) -> None:
    """Cosmetics (deleting the status, answering a press) must not be allowed to
    crash the handler."""
    try:
        await coro
    except Exception:
        pass


async def _say(msg, text: str) -> None:
    """Replace the message text; if that fails, write a new message. For outcome
    messages: the user must see them."""
    try:
        await msg.edit_text(text)
    except Exception:
        await _quiet(msg.answer(text))


def _trim_parts(parts: list, how: str) -> list:
    """Report -> at most _MAX_PARTS messages, with an honest truncation note.

    parts: list of ready messages from the renderer (HTML).
    how: how to get the full report -- substituted into the note's text.
    Returns: a new list; the note is appended as the last element if truncated.
    Silent truncation here would be worse than no limit: the user of a privacy
    tool would conclude there are no more findings.
    """
    if len(parts) <= _MAX_PARTS:
        return list(parts)
    kept = list(parts[:_MAX_PARTS])
    kept.append(
        tr("⚠ <b>Report truncated</b>: showing the first %d messages of %d.\nThe rest is not "
           "lost -- it is in the full report: <code>%s</code>.\n<i>The limit keeps Telegram from "
           "blocking the bot for a flood of messages.</i>") % (_MAX_PARTS, len(parts), html.escape(how, quote=False))
    )
    return kept


async def _send_parts(target, parts: list, how: str) -> None:
    """Send a report with a limit and pauses.

    target: the message we reply to (only .answer is needed).
    parts: ready messages from the renderer.
    how: the CLI command for the truncation note.

    Every message is remembered for deletion (by the button, /clear, or the
    timer): the report holds the values that were found. The "Delete report"
    button is under the last one.

    A failure on one message doesn't swallow the rest -- the report is already
    built, and showing it partially is more useful than not showing it at all.
    But staying silent about the loss is not allowed: an undelivered piece reads
    as "no more findings".
    """
    parts = _trim_parts(parts, how)
    failed = 0
    for n, part in enumerate(parts):
        if n:
            await asyncio.sleep(_SEND_PAUSE)
        try:
            _track(await target.answer(
                part, reply_markup=_delete_kb() if n == len(parts) - 1 else None))
        except Exception as exc:
            # Privacy: only the failure type. In the exception text Telegram returns a
            # fragment of the message, and that contains values from the file.
            failed += 1
            log.error("report message not sent: %s", type(exc).__name__)
    if failed:
        await _quiet(target.answer(
            tr("⚠ <b>The report arrived incompletely</b>: messages not sent -- %d of %d.\nTreat "
               "the report as incomplete, not empty. Full report: <code>%s</code>.")
            % (failed, len(parts), html.escape(how, quote=False))))


# ============================================================================
#  Button state: (chat_id, message_id) -> what to clean
# ============================================================================


@dataclass
class _Pending:
    file_id: str        # we do NOT store the file itself: it is downloaded again on press
    fmt: str
    user_id: int | None
    expires: float
    keep: set = field(default_factory=set)   # keys of _KEEP_OPTS that the user asked to keep
    neutral: bool = True                     # ZIP only: neutral names inside the archive


_pending: "OrderedDict[tuple, _Pending]" = OrderedDict()


def _purge() -> None:
    """The TTL is the same for all and entries are in time order -- expired ones are
    always at the start."""
    now = time.monotonic()
    while _pending and next(iter(_pending.values())).expires <= now:
        _pending.popitem(last=False)


def _remember(key: tuple, item: _Pending) -> None:
    _purge()
    while len(_pending) >= _PENDING_MAX:
        _pending.popitem(last=False)
    _pending[key] = item


# ============================================================================
#  Reports in the chat: (chat_id, message_id) -> when it was sent
# ============================================================================
#
# A spoiler hides a value from view, but the report text sits on Telegram's
# servers and in the chat history. So the bot remembers its messages with
# findings and deletes them: by the button, by the /clear command, or on its
# own after REPORT_TTL_MIN minutes. A cleaned file is never deleted: it is a
# result, not a report.
#
# ponytail: the registry lives in process memory. After a restart the bot
# doesn't remember old reports, they have to be deleted by hand (/clear says so
# honestly). Store it on disk -- if restarts become frequent.

_reports: "OrderedDict[tuple, float]" = OrderedDict()
_REPORTS_MAX = 2000

def _delete_kb() -> InlineKeyboardMarkup:
    """The "Delete report" button. Built per message: its text is in the reader's language."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr("\U0001F5D1 Delete report from chat"), callback_data="rm:all")]])


def _track(sent) -> None:
    """Remember a sent message containing findings. Never raises."""
    try:
        key = (sent.chat.id, sent.message_id)
    except AttributeError:
        return
    _reports[key] = time.monotonic()
    while len(_reports) > _REPORTS_MAX:
        _reports.popitem(last=False)


async def _delete_reports(bot, chat_id=None, cutoff=None) -> int:
    """Delete the remembered reports: those of chat chat_id (None -- all chats) and
    sent no later than cutoff (monotonic; None -- all). Returns: how many were
    deleted. The entry is forgotten in any case: a message that failed to delete
    (older than 48 hours or already deleted by hand) isn't worth retrying."""
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
    """Once a minute deletes reports older than REPORT_TTL_MIN. Never crashes."""
    while True:
        await asyncio.sleep(60)
        try:
            n = await _delete_reports(bot, cutoff=time.monotonic() - config.REPORT_TTL_MIN * 60)
            if n:
                log.info("auto-delete: report messages deleted=%d", n)
        except Exception as exc:
            log.error("auto-delete failure: %s", type(exc).__name__)


def _ttl_text() -> str:
    m = config.REPORT_TTL_MIN
    if m <= 0:
        return tr("disabled (REPORT_TTL_MIN=0)")
    return tr("after %d min") % m if m < 120 else tr("after %d h") % (m // 60)


def _help_text() -> str:
    return (tr(HELP_TEXT).format(ttl=_ttl_text()) + "\n\n"
            + tr("/lang switches the language of the bot's replies (Русский / English)."))


# ============================================================================
#  Dispatcher verdict and texts
# ============================================================================


def _e(value, cap: int = 40) -> str:
    """Untrusted value -> safe HTML. The limit of 40 keeps the worst case (every
    character '&' -> 5 characters) within one Telegram message."""
    return html.escape(clip(value, cap), quote=False)


def _opts_for(fmt: str) -> tuple:
    """Which "Keep" toggles make sense for the format."""
    if fmt in _LEGACY:
        return ()  # regeneration only, and it doesn't understand a whitelist
    return ("icc",) if fmt in _IMG_FMT else (
        ("title", "author", "icc") if fmt == "zip" else ("title", "author"))


def _keep_keys(ids) -> set | None:
    """Toggle keys -> the keep set for the cleaner (None if empty)."""
    keys = set()
    for i in ids:
        keys |= _KEEP_OPTS[i][1]
    return keys or None


def _keep_names(ids) -> str:
    return ", ".join(tr(_KEEP_OPTS[i][0]) for i in _KEEP_OPTS if i in ids)


def _by_choice(sv, ids) -> bool:
    """The original value survived BY THE USER'S CHOICE: both the source field and
    the place where it was found in the result are the field of an enabled
    toggle. An author found in the title with "keep author" enabled does not
    count here: that is a cleaning shortfall, not a choice. The ODF and RTF
    cleaners don't write KEPT records for the whitelist, and OOXML has an empty
    value in such a record, so the choice can't be determined from the actions --
    we compare by the field labels."""
    label, found = str(sv.label).casefold(), str(sv.found_at).casefold()
    for i in ids:
        pats = _KEEP_OPTS[i][2]
        if any(p in label for p in pats) and any(p in found for p in pats):
            return True
    return False


_DETECT = {"none": N_("the inspector sees no signs of processing"),
           "noticeable": N_("the file looks scrubbed: the fact of processing is visible"),
           "obvious": N_("the file is recognizable as processed")}


def _detect_line(res) -> str:
    """Assessment of how detectable the cleaning is (core/lint.py) for cases where
    cleanreport doesn't print it: it prints ASSESSMENT only in the "no survivors"
    branch."""
    d = getattr(res, "detect", "")
    if d not in _DETECT:
        return ""
    return tr("👁 <b>Cleaning detectability</b>: %s.") % tr(_DETECT[d])


def _verdict(res, keep=()) -> tuple:
    """(level, HTML): the DISPATCHER's verdict on the values. The cleanreport report
    judges by the critical_after counter and knows nothing about survived: it may
    write "CLEAN" where the original author's name survived cleaning inside the
    title. So whenever in doubt the bot speaks for itself and puts this BEFORE
    the report.

    keep -- the keys of the "Keep" toggles. What survived because of that choice
    is not a cleaning shortfall, but not "clean" either: level "kept", the file
    is NOT called clean."""
    keep = [k for k in _KEEP_OPTS if k in set(keep)]
    survived = list(getattr(res, "survived", None) or [])
    mine = [sv for sv in survived if _by_choice(sv, keep)]
    survived = [sv for sv in survived if not _by_choice(sv, keep)]
    gaps = list(getattr(res, "gaps", None) or [])
    old = [r for r in (getattr(res, "residual", None) or []) if not r[2]]
    lines = []
    if not getattr(res, "verified", False):
        lines.append(tr("⚠ The result was written without re-inspection: cleanliness can't be "
                        "claimed."))
    if survived:
        lines.append(tr("⛔ <b>Original identity or environment values remained in the file: "
                        "%d.</b>")
                     % len(survived))
        for sv in survived[:5]:
            lines.append(tr("• %s (%s) → found: %s") % (_e(sv.label), _e(sv.location), _e(sv.found_at)))
        if len(survived) > 5:
            lines.append(tr("… and %d more") % (len(survived) - 5))
    elif old and not mine:
        lines.append(tr("⚠ Critical findings with their previous values remain in the result: "
                        "%d. Short values (up to 5 characters) are not compared by the substring "
                        "check, see the report below.") % len(old))
    if gaps:
        lines.append(tr("⚠ The inspector did not parse part of the file (%d): the proof is "
                        "incomplete, something could have remained in the unparsed part.") % len(gaps))
    if mine:
        # This is not hidden and not presented as a failure: the user asked for it.
        head = (tr("📌 <b>Kept by your choice (%s): original values in the file -- %d.</b> This "
                   "is not a cleaning shortfall: you asked for these fields to stay. But because "
                   "of that the file is <b>NOT clean</b>, and it should be passed on only "
                   "deliberately. The report below may say \"NOT CLEAN\": its verdict is "
                   "computed without taking your list into account.") % (_keep_names(keep), len(mine)))
        lines.insert(0, head)
        for sv in mine[:5]:
            lines.append("• %s (%s)" % (_e(sv.label), _e(sv.location)))
    if not lines and res.clean:
        note = (tr("Re-inspection: no original identity or environment value was found in the "
                   "result."))
        if getattr(res, "critical_after", 0) > 0:
            note += (tr(" The critical findings the inspector still sees are new values%s: they "
                        "are not related to the originals.") % (tr(" (for example, the regenerated trailer /ID)")
                                          if res.fmt == "pdf" else ""))
        if keep:
            note += tr(" You asked to keep: %s.") % _keep_names(keep)
        return "ok", note
    # When values survive, the cleanreport report doesn't print the detectability
    # assessment -- we add it ourselves.
    det = _detect_line(res)
    if det and (mine or survived):
        lines.append(det)
    if survived:
        level = "bad"
    elif mine and not gaps and getattr(res, "verified", False):
        level = "kept"
    else:
        level = "warn"
    return level, "\n".join(lines or [tr("⚠ Cleanliness is not proven.")])


def _caption(level: str, profile: str, note: str, keep=()) -> str:
    if level == "ok":
        return (tr("✅ Profile <b>%s</b>. %s The file name is neutral, the original one was not "
                   "used.")
                % (profile, note))
    if level == "kept":
        return (tr("📌 Profile <b>%s</b>: kept by your choice: %s. The file is NOT clean: these "
                   "fields stayed as they were.") % (profile, _keep_names(keep)))
    if level == "warn":
        return tr("⚠ Profile <b>%s</b>: cleanliness is NOT proven, read the warning above.") % profile
    return (tr("⛔ NOT FULLY CLEANED: some of the original values remained in the file. Do not "
               "send it any further."))


def _no_clean_text(fmt: str) -> str:
    """A format without a cleaner: straight and without promises. The phrase and the
    phase number come from cleanreport -- the same text the user sees in any
    cleaning report."""
    if fmt in cleanreport.CLEANABLE:       # a cleaner exists, but the module didn't load
        why = tr("the cleaning module (core/clean.py) did not load")
    else:
        why = cleanreport.summary_line(
            CleanResult(src="", dst="", fmt=fmt, profile=Profile.STEALTH.value))
    return (tr("⛔ <b>There is no cleaning for this file.</b> %s. The file was only inspected and "
               "not changed: do not pass it on as cleaned.") % html.escape(why, quote=False))


def _keyboard(item: "_Pending | None" = None) -> InlineKeyboardMarkup:
    """The "Keep" toggles (by format) -> profiles -> "Don't clean".
    ZIP: the profiles go to batch mode (bt:), plus a names toggle."""
    fmt = item.fmt if item else ""
    batch = fmt == "zip"
    pre = "bt" if batch else "cl"

    def row(text, data):
        return [InlineKeyboardButton(text=text, callback_data=data)]

    rows = []
    for opt in _opts_for(fmt):
        on = bool(item) and opt in item.keep
        rows.append(row(tr("%s Keep: %s") % ("☑" if on else "☐", tr(_KEEP_OPTS[opt][0])),
                        "kp:" + opt))
    if fmt in _LEGACY:
        rows.append(row(tr("Regenerate (LibreOffice)"), "cl:regen"))
    elif batch:
        rows.append(row(tr("Names in archive: %s") % (tr("neutral") if (not item or item.neutral)
                                                else tr("original")), "kp:nn"))
        rows += [row(tr("Clean all (Stealth)"), pre + ":stealth"),
                 row(tr("Clean all (Paranoid)"), pre + ":paranoid"),
                 row(tr("Regenerate all (LibreOffice)"), pre + ":regen")]
    else:
        rows += [row(tr("Clean (Stealth)"), pre + ":stealth"),
                 row(tr("Clean (Paranoid)"), pre + ":paranoid")]
        if fmt in _REGEN_FMT:
            rows.append(row(tr("Regenerate (LibreOffice)"), pre + ":regen"))
    rows.append(row(tr("Don't clean"), "cl:no"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================================
#  Handlers
# ============================================================================


async def _deny(message: Message) -> None:
    # We answer even a stranger: otherwise an owner who hasn't yet put their id in
    # .env would see a silent bot and decide it is broken. The text reveals nothing
    # except the asker's own user_id.
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
    """Delete from the chat all the reports the bot remembers."""
    if not _allowed(message):
        await _deny(message)
        return
    n = await _delete_reports(message.bot, message.chat.id)
    await _quiet(message.delete())  # and the command itself: it is in the history too
    await message.answer(
        tr("\U0001F5D1 Report messages deleted: %d.\n<i>The bot doesn't remember reports sent "
           "before its last restart -- delete those by hand. The bot doesn't touch cleaned files "
           "or the files you sent; it deletes only its own reports and this command.</i>") % n)


@router.message(Command("status"))
async def on_status(message: Message) -> None:
    """What the bot can do on this machine. Shows no paths or values."""
    if not _allowed(message):
        await _deny(message)
        return

    def yes(flag, ok, no):
        return ("✅ " + ok) if flag else ("⛔ " + no)

    soffice = bool(find_soffice())
    try:
        exif = bool(_exiftool_exe())
    except Exception:  # noqa: BLE001 -- enrichment is not a reason to crash
        exif = False
    lines = [
        tr("<b>Bot status</b>"),
        yes(inspect_file is not None, tr("inspection"), tr("inspection: module failed to load")),
        yes(clean_file is not None, tr("cleaning Stealth / Paranoid"), tr("cleaning: module failed to "
                                                                    "load")),
        yes(clean_batch is not None, tr("batch ZIP cleaning"), tr("batch cleaning: module failed to "
                                                               "load")),
        yes(soffice, tr("LibreOffice: \"Regenerate\" and old doc/xls/ppt"),
            tr("LibreOffice not found: \"Regenerate\" and doc/xls/ppt are unavailable")),
        yes(exif, tr("exiftool: decoding MakerNotes in photos"),
            tr("no exiftool: MakerNotes are not decoded (how to install -- docs/GUIDE.md, "
               "section 7)")),
        tr("\U0001F5D1 Auto-deletion of reports: %s") % _ttl_text(),
        tr("\U0001F464 Users allowed: %d") % len(config.ALLOWED_USER_IDS),
    ]
    await message.answer("\n".join(lines))


# Each language names itself, and the confirmation is written in the chosen one.
_LANG_NAMES = {"ru": "Русский", "en": "English"}
_LANG_DONE = {"ru": "Готово: бот отвечает по-русски.", "en": "Done: the bot replies in English."}


def _lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=_LANG_NAMES[c], callback_data="lang:" + c) for c in LANGS]])


def _set_lang(user_id, code: str) -> str:
    _LANGS[user_id] = code
    _save_langs()
    return _LANG_DONE[code]


@router.message(Command("lang"))
async def on_lang(message: Message) -> None:
    """/lang shows the choice; /lang ru or /lang en switches right away."""
    if not _allowed(message):
        await _deny(message)
        return
    arg = ((message.text or "").split(maxsplit=1)[1:] or [""])[0].strip().lower()
    if arg in LANGS:
        await message.answer(_set_lang(message.from_user.id, arg))
        return
    await message.answer(tr("Choose the language of the bot's replies:"),
                         reply_markup=_lang_keyboard())


@router.callback_query(F.data.startswith("lang:"))
async def on_lang_choice(cb: CallbackQuery) -> None:
    if not _allowed(cb):
        await _quiet(cb.answer())
        return
    code = (cb.data or "").split(":", 1)[-1]
    if code not in LANGS:
        await _quiet(cb.answer())
        return
    done = _set_lang(cb.from_user.id, code)
    await _quiet(cb.answer(done))
    msg = cb.message
    if msg is not None and not isinstance(msg, InaccessibleMessage):
        await _quiet(msg.edit_text(done))


@router.callback_query(F.data == "rm:all")
async def on_delete_reports(cb: CallbackQuery) -> None:
    """The "Delete report" button: deletes all remembered reports of this chat."""
    try:
        if not _allowed(cb):
            await _quiet(cb.answer())
            return
        msg = cb.message
        if msg is None or isinstance(msg, InaccessibleMessage):
            await _quiet(cb.answer(tr("The message is unavailable: delete it by hand."), show_alert=True))
            return
        n = await _delete_reports(msg.bot, msg.chat.id)
        await _quiet(msg.delete())  # the message with the button itself, even if the bot has forgotten it
        await _quiet(cb.answer(tr("Messages deleted: %d") % max(n, 1)))
    except Exception as exc:
        log.error("report deletion failure: %s", type(exc).__name__)
        await _quiet(cb.answer(tr("Could not delete. Delete by hand."), show_alert=True))


@router.message(F.photo)
async def on_photo(message: Message) -> None:
    if not _allowed(message):
        await _deny(message)
        return
    await message.answer(
        tr("This arrived as a <b>photo</b>, not a file. Telegram already recompressed and "
           "re-encoded the image on its side before the bot saw it -- the original metadata "
           "(EXIF, GPS, camera model and so on) has already been stripped or rewritten. There is "
           "nothing to inspect here, and the result would be false.\n\nSend the same file again "
           "via the paperclip → <b>File</b> (Document), not through the \"Photo\" section.")
    )


@router.message(F.document)
async def on_document(message: Message) -> None:
    if not _allowed(message):
        await _deny(message)
        return
    doc: Document = message.document
    if doc.file_size and doc.file_size > config.MAX_FILE_BYTES:
        await message.answer(
            tr("The file weighs {mb} MB. The Telegram Bot API doesn't deliver files larger than "
               "{limit} MB to bots at all -- that is a limit of the Bot API itself, not of the "
               "bot. Send a smaller file.").format(
                mb=round(doc.file_size / (1024 * 1024), 1),
                limit=config.MAX_FILE_BYTES // (1024 * 1024),
            )
        )
        return
    await _inspect_and_reply(message, doc)


async def _inspect_and_reply(message: Message, doc: Document) -> None:
    """Download, parse in a thread, render, reply. Never crashes."""
    status = await message.answer(tr("\U0001F50E Got it, inspecting the metadata…"))
    tmp_dir = None
    started = time.monotonic()
    try:
        async with _SEMAPHORE:
            tmp_dir = tempfile.mkdtemp(prefix="tginsp_")
            file_path = os.path.join(tmp_dir, "input.bin")
            await message.bot.download(doc, destination=file_path, timeout=60)

            if sniff.sniff(file_path) == "zip":
                # A generic ZIP: nothing to inspect (the write timestamp is not a finding about
                # a document), but the contents can be cleaned as a batch.
                await _quiet(status.delete())
                await _offer_batch(message, doc, file_path)
                return

            if inspect_file is None:
                await status.edit_text(
                    tr("The inspection core (core/inspect.py) is not ready yet. Try again a "
                       "little later.")
                )
                return

            report = await _in_thread(tmp_dir, _INSPECT_TIMEOUT, inspect_file, file_path)

        duration = time.monotonic() - started
        parts = render_telegram(report)

        # Only this may be logged: format, size, number of findings, time.
        log.info(
            "inspection finished: format=%s size=%d findings=%d time=%.1fs",
            getattr(report, "fmt", "?"),
            getattr(report, "size", 0) or 0,
            len(getattr(report, "findings", None) or []),
            duration,
        )

        await _quiet(status.delete())
        await _send_parts(message, parts, tr("python -m core.inspect <file>"))

        if not _looks_neutral(doc.file_name or ""):
            await message.answer(
                tr("Reminder: the <b>file name</b> itself is metadata too. It doesn't look like "
                   "a technical auto-generated name, so when you pass the file on, it's worth "
                   "renaming it separately.")
            )

        await _offer_cleaning(message, getattr(report, "fmt", ""), doc)
    except _Timeout:
        tmp_dir = None  # the thread removes the directory itself when it finishes
        await _say(
            status,
            tr("Inspection did not finish within {}s and was aborted -- the file is too complex "
               "or damaged.").format(_INSPECT_TIMEOUT),
        )
    except Exception as exc:
        # Privacy: only the failure type -- exception text may contain values from the file.
        log.error("document inspection failure: %s", type(exc).__name__)
        log.debug("failure details", exc_info=True)
        await _say(
            status,
            tr("Could not inspect the file because of an internal error. The file is unchanged "
               "and not stored on the bot's server."),
        )
    finally:
        if tmp_dir:
            _rmtree(tmp_dir)


async def _offer_cleaning(message: Message, fmt: str, doc: Document) -> None:
    """Buttons only where cleaning exists. Otherwise, straight: the file is NOT
    cleaned. Legacy doc/xls/ppt -- only "Regenerate", and only if LibreOffice is
    available."""
    legacy = fmt in _LEGACY and clean_file is not None and bool(find_soffice())
    if not legacy and (clean_file is None or fmt not in cleanreport.CLEANABLE):
        text = _no_clean_text(fmt)
        if fmt in _LEGACY and clean_file is not None:
            text += (tr("\n\nSuch a file can be cleaned by regeneration through LibreOffice, but "
                        "it was not found on this machine. Install LibreOffice and send the file "
                        "again."))
        await message.answer(text)
        return
    user = message.from_user
    item = _Pending(doc.file_id, fmt, user.id if user else None, time.monotonic() + _PENDING_TTL)
    text = tr(LEGACY_TEXT) if legacy else tr(CHOICE_TEXT).format(
        regen=tr(REGEN_BULLET) if fmt in _REGEN_FMT else "")
    sent = await message.answer(text, reply_markup=_keyboard(item))
    _remember((sent.chat.id, sent.message_id), item)


async def _offer_batch(message: Message, doc: Document, path: str) -> None:
    """ZIP: show how many files are inside and offer batch cleaning."""
    if clean_batch is None:
        await message.answer(tr("⛔ <b>No batch cleaning</b>: the core/clean_batch.py module "
                                "failed to load. The ZIP is not cleaned and not changed."))
        return
    try:
        with zipfile.ZipFile(path) as zf:
            n = sum(1 for i in zf.infolist() if not i.filename.endswith(("/", "\\")))
    except Exception:  # noqa: BLE001 -- broken directory: the cleaner will refuse clearly on its own
        n = -1
    head = tr("files inside: %d") % n if n >= 0 else tr("the archive directory can't be read, "
                                                     "cleaning will most likely refuse")
    item = _Pending(doc.file_id, "zip", message.from_user.id if message.from_user else None,
                    time.monotonic() + _PENDING_TTL)
    sent = await message.answer(tr(BATCH_TEXT).format(n=head), reply_markup=_keyboard(item))
    _remember((sent.chat.id, sent.message_id), item)


@router.message()
async def on_other(message: Message) -> None:
    """Everything else (text, sticker, voice): a hint instead of silence."""
    if not _allowed(message):
        await _deny(message)
        return
    await message.answer(_help_text())


@router.callback_query(F.data.startswith(("cl:", "bt:", "kp:")))
async def on_clean_choice(cb: CallbackQuery) -> None:
    """A button press. Never crashes."""
    async def ack(text=None, alert=False):
        # Must answer immediately: after about a minute Telegram rejects an answer to
        # an old query.
        await _quiet(cb.answer(text, show_alert=alert))

    try:
        if not _allowed(cb):
            await ack()
            return
        msg = cb.message
        if msg is None or isinstance(msg, InaccessibleMessage):
            await ack(tr("The message is unavailable. Send the file again."), True)
            return
        _purge()
        key = (msg.chat.id, msg.message_id)
        item = _pending.get(key)
        if item is None:
            await ack(tr("Button expired: more than 30 minutes have passed or the bot was "
                         "restarted. Send the file again."), True)
            await _quiet(msg.edit_reply_markup(reply_markup=None))
            return
        if item.user_id is not None and cb.from_user.id != item.user_id:
            await ack(tr("This button is not for you."), True)
            return
        if cb.data.startswith("kp:"):
            # A toggle: the entry stays (and its TTL is not extended), only the keyboard
            # changes.
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
            await _say(msg, tr("OK, not cleaning the file. It was not stored on the bot's server."))
            return
        profile = _PROFILES.get(cb.data)
        if profile is None:
            await ack()
            return
        if profile is Profile.REGENERATE and item.keep:
            # Regeneration doesn't understand the whitelist (clean_regen drops it).
            # Silently handing over a file without what was kept would deceive the user, so
            # we say so directly.
            await ack(tr("Regeneration can't do \"Keep\": LibreOffice writes the file anew. Turn "
                         "the toggles off or choose Stealth/Paranoid."), True)
            return
        # The first press takes the entry: a double tap won't start the cleaning twice.
        del _pending[key]
        await ack(tr("Cleaning…"))
        if cb.data.startswith("bt:"):
            await _batch_and_reply(msg, item, profile)
        else:
            await _clean_and_reply(msg, item, profile)
    except Exception as exc:
        log.error("button handler failure: %s", type(exc).__name__)
        log.debug("failure details", exc_info=True)
        await ack(tr("Internal error. The file is not cleaned, send it again."), True)


async def _clean_and_reply(msg: Message, item: _Pending, profile: Profile) -> None:
    """Download again, clean in a thread, reply with the report and the file. Never
    crashes. msg is the message with the buttons: it turns into a status line."""
    pname = profile.value.capitalize()
    tmp_dir = None
    started = time.monotonic()
    try:
        await _say(msg, tr("\U0001F9F9 Cleaning the file, profile <b>%s</b>…") % pname)  # this also removes the buttons
        # ponytail: on a timeout the semaphore is released while the "orphan" thread is
        # still finishing. Tolerable for a personal bot; hold the semaphore until the
        # thread ends -- if strictly one task at a time is ever needed.
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
            raise RuntimeError("the cleaner reported success, but there is no file")
        # Only this may be logged: format, profile, outcome, sizes, number of actions, time.
        log.info(
            "cleaning finished: format=%s profile=%s ok=%s size_before=%d size_after=%d "
            "actions=%d time=%.1fs",
            item.fmt, profile.value, ok, size_in, os.path.getsize(dst) if ok else 0,
            len(res.actions), time.monotonic() - started,
        )

        level, note = _verdict(res, item.keep) if ok else ("bad", "")
        if ok:
            await _quiet(msg.delete())
            if level != "ok":
                # the verdict may contain surviving values -- that is a report too, subject to deletion
                _track(await msg.answer(note))  # the warning before the report, which may say "CLEAN"
                # (and the user's choice is also named before the report, not hidden in it)
        else:
            await _say(msg, tr("⛔ <b>File NOT cleaned</b> -- cleaning failed, there is no "
                               "cleaned copy, the bot sends no file. The reason and details are "
                               "below."))
        await _send_parts(msg, cleanreport.render_telegram(res),
                          tr("python -m core.clean <file>"))
        if ok:
            await msg.answer_document(
                FSInputFile(dst, filename=name),
                caption=_caption(level, pname, note, item.keep),
                disable_content_type_detection=True,  # a document, not a photo: nothing gets recompressed
            )
    except _Timeout:
        tmp_dir = None  # the thread removes the directory itself when it finishes
        await _say(msg, tr("Cleaning did not finish within {}s and was aborted -- the file is "
                           "too complex or damaged. There is no cleaned file: do not send "
                           "anything on as cleaned. The original file is unchanged; to retry, "
                           "send it again.").format(_CLEAN_TIMEOUT))
    except Exception as exc:
        # Privacy: only the failure type -- exception text may contain values from the file.
        log.error("document cleaning failure: %s", type(exc).__name__)
        log.debug("failure details", exc_info=True)
        await _say(msg, tr("Could not clean the file because of an internal error. The cleaned "
                           "file was NOT sent: do not consider the file cleaned. The original "
                           "file is unchanged and not stored on the bot's server."))
    finally:
        if tmp_dir:
            _rmtree(tmp_dir)


def _chunk_pre(text: str, limit: int = 3500) -> list:
    """Report text -> <pre> messages no longer than limit (after escaping).
    The lines are untrusted (names from the archive): we escape every one."""
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
    """Summary of the detectability of cleaning traces across the cleaned files of
    the batch."""
    d = [i.detect for i in res.items if i.status == "cleaned"]
    if not d:
        return ""
    return (tr("👁 <b>Cleaning detectability</b> (cleaned %d): not visible -- %d, noticeable -- "
               "%d, obvious -- %d.") % (len(d), d.count("none"), d.count("noticeable"), d.count("obvious")))


async def _batch_and_reply(msg: Message, item: _Pending, profile: Profile) -> None:
    """ZIP -> clean_batch in a thread -> a report for each file -> the archive. Never
    crashes. The archive is sent only if it contains something cleaned and fits
    the Bot API sending limit; anything not cleaned is not in the archive (a
    clean_batch decision)."""
    pname = profile.value.capitalize()
    tmp_dir = None
    started = time.monotonic()
    try:
        await _say(msg, tr("📦 Cleaning the archive, profile <b>%s</b>: this may take minutes…") % pname)
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
        log.info("batch cleaning: profile=%s ok=%s files=%d cleaned=%d not_cleaned=%d "
                 "size_before=%d size_after=%d time=%.1fs", profile.value, res.ok,
                 t.get("total", 0), t.get("cleaned", 0), t.get("not_cleaned", 0),
                 size_in, size_out, time.monotonic() - started)

        if not res.ok:
            why = html.escape(clip(res.errors[0] if res.errors else tr("no reason given"), 300),
                              quote=False)
            await _say(msg, tr("⛔ <b>Archive NOT cleaned</b> -- no output archive, the bot sends "
                               "nothing. Reason: %s") % why)
            return

        await _quiet(msg.delete())
        text = render_batch_report(res, with_names=True)
        parts = _chunk_pre(text)
        if len(parts) <= _BATCH_CHAT_PARTS:
            await _send_parts(msg, parts, "python -m core.clean_batch")
        else:
            # A report on hundreds of files doesn't fit in the chat (429 from Telegram): a
            # summary + the full one as a document.
            head = text.split("\n\n", 1)[0]
            _track(await msg.answer(tr("<pre>%s</pre>\nDetails for each file -- in the document "
                                       "below.")
                                    % html.escape(head, quote=False)))
            rep = os.path.join(tmp_dir, "report.txt")
            with open(rep, "w", encoding="utf-8") as fh:
                fh.write(text)
            # Original file names are metadata too: the report document is deleted like a
            # report.
            _track(await msg.answer_document(
                FSInputFile(rep, filename="report_%s.txt" % secrets.token_hex(3)),
                caption=tr("Report for each file (with the original names)."),
                reply_markup=_delete_kb()))

        det = _batch_detect_line(res)
        if det:
            await msg.answer(det)
        if item.keep and t.get("unproven"):
            await msg.answer(
                tr("📌 You asked to keep: %s. Files left out of the archive because their "
                   "cleanliness was not proven for this reason: %d -- this is a consequence of "
                   "your choice. Clean them one at a time if needed.") % (_keep_names(item.keep), t["unproven"]))
        if not t.get("cleaned"):
            await msg.answer(tr("⛔ <b>No file was cleaned</b> -- there is no archive with a "
                                "result. The reasons for each file are in the report above."))
            return
        if size_out > config.MAX_SEND_BYTES:
            await msg.answer(
                tr("⛔ <b>Result not sent</b>: the archive weighs %.1f MB, and the Telegram Bot "
                   "API doesn't let bots send files larger than %d MB (that is an API limit, not "
                   "the bot's). The files are cleaned, but they can't be delivered: split the "
                   "original archive into parts and send again.")
                % (size_out / (1024 * 1024), config.MAX_SEND_BYTES // (1024 * 1024)))
            return
        if res.all_cleaned:
            cap = (tr("✅ Profile <b>%s</b>: cleaned and verified files -- %d of %d. The archive "
                      "name is neutral.") % (pname, t["cleaned"], t["total"]))
        else:
            cap = (tr("⚠ Profile <b>%s</b>: the archive contains only what is cleaned and "
                      "verified -- %d of %d. The rest are <b>NOT included</b> and you have them "
                      "in their original form: see the report.") %
                   (pname, t["cleaned"], t["total"]))
        await msg.answer_document(
            FSInputFile(dst, filename=_neutral_name("zip")), caption=cap,
            disable_content_type_detection=True)
    except _Timeout:
        tmp_dir = None  # the thread removes the directory itself when it finishes
        await _say(msg, tr("Batch cleaning did not finish within {} min and was aborted. There "
                           "is no archive with a result: do not send anything on as cleaned. "
                           "Split the archive into parts and send again.").format(_BATCH_TIMEOUT // 60))
    except Exception as exc:
        log.error("batch cleaning failure: %s", type(exc).__name__)
        log.debug("failure details", exc_info=True)
        await _say(msg, tr("Could not clean the archive because of an internal error. The "
                           "archive with the result was NOT sent: do not consider the files "
                           "cleaned. The original archive is unchanged and not stored on the "
                           "bot's server."))
    finally:
        if tmp_dir:
            _rmtree(tmp_dir)


async def _run() -> None:
    token = config.require_token()
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    _load_langs()
    # The command menu at Telegram's "/" button (without it /start is not visible),
    # in both languages: Telegram shows the list matching the user's client
    # language and the default list otherwise.
    for code in (None,) + LANGS:
        with use_lang(code or config.BOT_LANG):
            commands = [
                BotCommand(command="start", description=tr("How to use")),
                BotCommand(command="lang", description=tr("Language: Русский / English")),
                BotCommand(command="clear", description=tr("Delete reports from the chat")),
                BotCommand(command="status", description=tr("What is available on this machine")),
                BotCommand(command="help", description=tr("Help"))]
        await _quiet(bot.set_my_commands(commands, language_code=code))
    log.info("access: users=%d, report auto-deletion=%d min",
             len(config.ALLOWED_USER_IDS), config.REPORT_TTL_MIN)
    reaper = asyncio.create_task(_reaper(bot)) if config.REPORT_TTL_MIN > 0 else None
    try:
        await dp.start_polling(bot)
    finally:
        if reaper:
            reaper.cancel()


def _acquire_lock(path: str):
    """Acquire the lock file without waiting. Returns: the open file (hold it until
    the end of the process) or None if another process holds the lock.

    The OS releases the lock when the process ends in any way, including being
    killed from the task manager -- unlike a pid file, which stays after a crash
    and blocks startup."""
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
            tr("The bot is already running in another window or process on this machine.\nTwo "
               "instances with one token interfere with each other: Telegram hands messages to "
               "one, then to the other.\nStop the first (Ctrl+C in its window) and start again.\n"))
        raise SystemExit(1)
    from aiogram.exceptions import TelegramNotFound, TelegramUnauthorizedError
    from aiogram.utils.token import TokenValidationError
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    except (TokenValidationError, TelegramUnauthorizedError, TelegramNotFound):
        # The most common first-run error: a placeholder from .env.example is left in
        # .env, or the token was copied incompletely. For a nonexistent token Telegram
        # answers 404 (NotFound), for a revoked one 401. A traceback is useless here.
        sys.stderr.write(
            tr("Telegram did not accept the token from TG_BOT_TOKEN.\nCheck .env: the whole "
               "token from @BotFather, of the form 123456789:AAH..., with no spaces or quotes "
               "around it. If the token was reissued (/revoke), you need the new one.\n"))
        raise SystemExit(1)
    finally:
        lock.close()


# ============================================================================
#  Self-test without a token or network: fake message/callback + the real
#  inspect_file / clean_file on fixtures. PYTHONIOENCODING=utf-8 python bot.py --selftest
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
        print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
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
    # Our own %TEMP% for the duration of the self-test: the "directories removed"
    # checks count tginsp_*/tgclean_*, and a live bot running alongside gave false
    # failures.
    saved_tempdir = tempfile.tempdir
    tempfile.tempdir = tempfile.mkdtemp(prefix="botself_tmp_")
    bot = FakeBot()
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    left = lambda: sorted(glob.glob(os.path.join(tempfile.gettempdir(), "tg[ic][nl]*_*")))  # noqa: E731
    texts = lambda ev: [m.text for k, m in ev if k == "text"]  # noqa: E731

    def _is_choice(m):
        """A message with the cleaning buttons (not the "Delete report" button under a report)."""
        mk = getattr(m, "markup", None)
        return mk is not None and any(b.callback_data != "rm:all"
                                      for r in mk.inline_keyboard for b in r)
    docs = lambda ev: [d for k, d in ev if k == "doc"]  # noqa: E731
    saved = (g["clean_file"], g["_CLEAN_TIMEOUT"], dict(core_clean._CLEANERS), list(config.ALLOWED_USER_IDS))
    # The self-test's fake users are 7 and 8. They used to be admitted by an empty
    # list ("everyone"), but the default is flipped to "no one", so now they must
    # be named explicitly. The refusal checks below swap the list themselves.
    config.ALLOWED_USER_IDS[:] = [7, 8]

    async def send(maker, name):
        """Put the fixture in place, run the real _inspect_and_reply."""
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
        print("1. PDF: inspection -> buttons -> Stealth -> report + file")
        base = left()
        src, exp, ev, ch = await send(fx.make_pdf, "pdf")
        before = sha(bot.files["pdf"])
        btns = [b.text for r in ch.markup.inline_keyboard for b in r] if ch else []
        # LibreOffice doesn't regenerate PDFs: the "Regenerate" button would lead to a guaranteed refusal.
        check("buttons: \"Keep\" toggles, Stealth, Paranoid, Don't clean (no Regenerate)",
              btns == ["☐ Keep: document title", "☐ Keep: the author",
                       "Clean (Stealth)", "Clean (Paranoid)", "Don't clean"], btns)
        check("profiles explained right in the message, regeneration not promised for PDF",
              ch and "Stealth" in ch.text and "keeps" in ch.text and "Paranoid" in ch.text
              and "looks processed" in ch.text and "Regenerate" not in ch.text
              and "{" not in ch.text)
        check("file_id remembered, nothing on disk after inspection", len(_pending) == 1 and left() == base)
        cb = await click(ch, "cl:stealth")
        d = docs(ev)
        check("answered the press immediately", cb.acks and cb.acks[0][0] == "Cleaning…", cb.acks)
        check("file sent once", len(d) == 1, len(d))
        if d:
            check("name is neutral, no original title",
                  re.fullmatch(r"document_[0-9a-f]{6}\.pdf", d[0].name) and "probe" not in d[0].name, d[0].name)
            check("sent as a document (no type detection/recompression)",
                  d[0].kw.get("disable_content_type_detection") is True)
            check("original values are gone from the delivered file", survivors(src, d[0].data, "pdf") == [])
            check("caption: profile and verdict", "Stealth" in d[0].caption and "✅" in d[0].caption, d[0].caption)
        kinds = [k for k, _ in ev]
        check("order: the cleaning report comes before the file",
              any("Metadata cleaning" in t for t in texts(ev)) and kinds.index("doc") > max(
                  i for i, (k, m) in enumerate(ev) if k == "text" and "Metadata cleaning" in m.text))
        check("input unchanged, entries and directories removed", sha(open(src, "rb").read()) == before
              and not _pending and left() == base)
        cb = await click(ch, "cl:stealth")
        check("repeat press: \"expired\", no second file",
              cb.acks and cb.acks[0][1] and "expired" in cb.acks[0][0] and len(docs(ev)) == 1, cb.acks)
        leak = [v for v in exp["values"] if any(v in line for line in logs)] + (
            ["name"] if any("probe" in line for line in logs) else [])
        check("logs: no finding values and no file name", not leak, leak)

        print("2. JPEG / Paranoid and PNG / Stealth")
        for maker, nm, prof, ext in ((fx.make_jpeg, "jpg", "cl:paranoid", ".jpg"), (fx.make_png, "png", "cl:stealth", ".png")):
            src, exp, ev, ch = await send(maker, nm)
            await click(ch, prof)
            d = docs(ev)
            check("%s: file sent, name image_*%s" % (nm, ext),
                  len(d) == 1 and re.fullmatch(r"image_[0-9a-f]{6}" + re.escape(ext), d[0].name) is not None,
                  [x.name for x in d])
            check("%s: original values are gone" % nm, d and survivors(src, d[0].data, nm) == [])

        print("2b. OOXML (phase 2): buttons present, file delivered with the right extension")
        for maker, nm, pat in ((fx.make_docx, "docx", r"document_[0-9a-f]{6}\.docx"),
                               (fx.make_xlsx, "xlsx", r"spreadsheet_[0-9a-f]{6}\.xlsx"),
                               (fx.make_pptx, "pptx", r"presentation_[0-9a-f]{6}\.pptx")):
            src, exp, ev, ch = await send(maker, nm)
            check("%s: cleaning buttons shown" % nm, ch is not None and ch.markup is not None)
            if ch is None:
                continue
            check("%s: \"Regenerate\" and its description are present" % nm,
                  "Regenerate (LibreOffice)" in [b.text for r in ch.markup.inline_keyboard for b in r]
                  and "Regenerate" in ch.text)
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("%s: file sent, name neutral and with its own extension" % nm,
                  len(d) == 1 and re.fullmatch(pat, d[0].name) is not None, [x.name for x in d])
            check("%s: original values are gone" % nm, d and survivors(src, d[0].data, nm) == [])
            # Honesty about detectability: for this fixture Stealth leaves a sign of
            # cleaning (empty dc:creator, cp:lastModifiedBy, Company), and the user must
            # read that in the report, not learn it from whoever they sent the file to. We
            # check it through the bot, not only in the core.
            if nm == "docx":
                check("docx: the report warns that the file is recognizable as cleaned",
                      any("RECOGNIZABLE AS SCRUBBED" in t for t in texts(ev)), texts(ev)[-1][:200])

        print("3. formats without cleaning: no buttons, phase named")
        # The phase number here comes from cleanreport._PHASE, not from the
        # dispatcher: _no_clean_text() builds an empty CleanResult whose
        # planned_phase=0. Legacy OLE2 is phase 4. RTF is cleaned, see the check above.
        src, exp, ev, ch = await send(fx.make_rtf, "rtf")
        check("rtf: cleaning buttons shown", ch is not None and ch.markup is not None)
        if ch is not None:
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("rtf: file sent as document_*.rtf",
                  len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.rtf", d[0].name) is not None,
                  [x.name for x in d])
            check("rtf: original values are gone", d and survivors(src, d[0].data, "rtf") == [])

        src, exp, ev, ch = await send(fx.make_odt, "odt")
        check("odt: cleaning buttons shown", ch is not None and ch.markup is not None)
        if ch is not None:
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("odt: file sent as document_*.odt",
                  len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.odt", d[0].name) is not None,
                  [x.name for x in d])
            check("odt: original values are gone", d and survivors(src, d[0].data, "odt") == [])

        # Legacy without LibreOffice: no buttons, phase named, what is missing is hinted at.
        real_soffice = g["find_soffice"]
        g["find_soffice"] = lambda: None
        try:
            for maker, nm, phase in ((fx.make_doc, "doc", 4),):
                _, _, ev, ch = await send(maker, nm)
                last = texts(ev)[-1]
                check("%s without LibreOffice: no cleaning buttons, no file, no entry" % nm,
                      ch is None and not docs(ev) and not _pending
                      and not any(_is_choice(m) for k, m in ev if k == "text"))
                check("%s without LibreOffice: \"NOT cleaned\", phase %d, a hint about "
                      "LibreOffice" % (nm, phase),
                      "NOT cleaned" in last and "inspect-only" in last
                      and "phase %d" % phase in last and "LibreOffice" in last, last)
        finally:
            g["find_soffice"] = real_soffice

        # Legacy with LibreOffice: a single "Regenerate" button, docx on output.
        if real_soffice():
            src, _, ev, ch = await send(fx.make_doc, "doc_regen")
            btns = [b.text for r in ch.markup.inline_keyboard for b in r] if ch else []
            check("doc: only \"Regenerate\" and \"Don't clean\", no \"Keep\"",
                  btns == ["Regenerate (LibreOffice)", "Don't clean"], btns)
            check("doc: the text explains why only regeneration",
                  ch is not None and "embedded in the container itself" in ch.text)
            if ch is not None:
                await click(ch, "cl:regen")
                d = docs(ev)
                check("doc -> regenerated, sent as document_*.docx",
                      len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.docx", d[0].name) is not None,
                      [x.name for x in d] or texts(ev)[-1][:300])
                check("doc -> original values are gone", d and survivors(src, d[0].data, "docx") == [])
        else:
            print("  [skip] LibreOffice not found: doc regeneration not checked")

        print("4. ok=False: the file is not sent")
        def refuse(s, d, p, keep=None):
            r = core_clean.DispatchResult(s, d, "pdf", "stealth")
            r.err("test: cleaner refusal")
            return r
        g["clean_file"] = refuse
        _, _, ev, ch = await send(fx.make_pdf, "pdf4")
        await click(ch, "cl:paranoid")
        check("no file, \"NOT cleaned\" stated, reason in the report",
              not docs(ev) and "NOT cleaned" in ch.text and any("cleaner refusal" in t for t in texts(ev)), ch.text)
        check("directories removed", left() == base)

        print("5. timeout: the thread finishes, the directory is removed after it")
        def slow(s, d, p, keep=None):
            time.sleep(1.0)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with open(d, "wb") as fh:
                fh.write(b"x")
            return core_clean.DispatchResult(s, d, "pdf", "stealth", ok=True)
        g["clean_file"], g["_CLEAN_TIMEOUT"] = slow, 0.3
        _, _, ev, ch = await send(fx.make_pdf, "pdf5")
        await click(ch, "cl:stealth")
        check("timeout stated, no file", "did not finish within" in ch.text and not docs(ev), ch.text)
        check("while the thread is working, the directory is left alone", left() != base)
        await asyncio.sleep(1.6)
        check("thread finished -- directory removed", left() == base, left())
        g["_CLEAN_TIMEOUT"] = saved[1]

        print("6. exception in the cleaner: human text, no leak")
        def boom(s, d, p, keep=None):
            raise RuntimeError("SECRET-VALUE-FROM-FILE")
        g["clean_file"] = boom
        _, _, ev, ch = await send(fx.make_pdf, "pdf6")
        await click(ch, "cl:stealth")
        check("internal error, no file, the value is not in the chat",
              "internal error" in ch.text and "NOT sent" in ch.text and "SECRET" not in ch.text and not docs(ev), ch.text)
        check("value is not in the logs, directories removed",
              not any("SECRET" in x for x in logs) and left() == base)
        g["clean_file"] = saved[0]

        print("7. lying cleaner: a leak remained -> warning BEFORE the report")
        def liar(s, d, prof, keep, force):
            shutil.copyfile(s, d)
            r = CleanResult(src=s, dst=d, fmt="pdf", profile="stealth")
            r.ok = True
            r.act("removed", "/Info", "/Author", "-")
            return r
        core_clean._CLEANERS["pdf"] = liar
        _, _, ev, ch = await send(fx.make_pdf, "pdf7")
        await click(ch, "cl:stealth")
        warn = next((i for i, (k, m) in enumerate(ev) if k == "text" and "remained in the file" in m.text), -1)
        rep = next((i for i, (k, m) in enumerate(ev) if k == "text" and "Metadata cleaning" in m.text), -1)
        d = docs(ev)
        check("warning before the report", 0 <= warn < rep, (warn, rep))
        check("file delivered, but the caption says \"NOT FULLY CLEANED\"", len(d) == 1 and "NOT FULLY CLEANED" in d[0].caption, [x.caption for x in d])
        core_clean._CLEANERS.update(saved[2])

        print("8. state: size, TTL, stranger, not on the list, \"Don't clean\"")
        _pending.clear()
        for i in range(_PENDING_MAX + 5):
            _remember((1, i), _Pending("f", "pdf", 7, time.monotonic() + 60))
        check("size is bounded, the oldest are evicted",
              len(_pending) == _PENDING_MAX and (1, 0) not in _pending and (1, _PENDING_MAX + 4) in _pending)
        for v in _pending.values():
            v.expires = 0
        _purge()
        check("expired entries are dropped", not _pending)
        _, _, ev, ch = await send(fx.make_pdf, "pdf8")
        cb = await click(ch, "cl:stealth", user_id=8)
        check("someone else's button rejected, entry intact", cb.acks[0][1] and len(_pending) == 1 and not docs(ev))
        config.ALLOWED_USER_IDS[:] = [1]
        cb = await click(ch, "cl:stealth", user_id=7)
        # Restore the TEST list, not the original: the original now means "no one",
        # and all the checks after this point would silently fall off.
        config.ALLOWED_USER_IDS[:] = [7, 8]
        check("user not on the list: silence, entry intact", cb.acks == [(None, False)] and len(_pending) == 1 and not docs(ev), cb.acks)
        await click(ch, "cl:no")
        check("\"Don't clean\": no entry, no file, text said",
              not _pending and not docs(ev) and "not cleaning" in ch.text, ch.text)

        print("9. _verdict")
        mk = lambda **kw: core_clean.DispatchResult("a", "b", "pdf", "stealth", ok=True, **kw)  # noqa: E731
        lv, note = _verdict(mk(verified=True, critical_after=2, residual=[("trailer", "/ID[0]", True)]))
        check("clean + new values: ok and an explanation about /ID", lv == "ok" and "trailer /ID" in note, (lv, note))
        sv = core_clean.Survivor("identity", "/Info", "<b>&" + "&" * 400, "secret", "x > " + "&" * 400)
        lv, note = _verdict(mk(verified=True, survived=[sv] * 100))
        body = re.sub(r"</?b>", "", note)
        check("survived: bad, no more than 4096, escaped, \"and 95 more\"",
              lv == "bad" and len(note.encode("utf-16-le")) // 2 <= 4096 and "<" not in body and ">" not in body
              and not re.search(r"&(?!(?:amp|lt|gt);)", body) and "and 95 more" in note, len(note))
        check("gaps -> warn", _verdict(mk(verified=True, gaps=["x"]))[0] == "warn")
        check("previous critical findings with an empty survived -> warn",
              _verdict(mk(verified=True, residual=[("/Info", "Author", False)]))[0] == "warn")
        check("not verified -> warn", _verdict(mk())[0] == "warn")

        print("10. message limit: without it the bot hits 429 on a long report")
        short = ["a", "b", "c"]
        check("a short report is untouched", _trim_parts(short, "cli") == short)
        long_ = ["part %d" % i for i in range(_MAX_PARTS + 40)]
        cut = _trim_parts(long_, "python -m core.inspect <file>")
        check("a long one is cut to the limit plus a note", len(cut) == _MAX_PARTS + 1, len(cut))
        check("exactly the first messages are kept", cut[:_MAX_PARTS] == long_[:_MAX_PARTS])
        check("the note names both numbers and a way to get the full one",
              "first %d" % _MAX_PARTS in cut[-1] and "of %d" % len(long_) in cut[-1]
              and "core.inspect" in cut[-1], cut[-1][:200])
        check("the note doesn't break HTML: angle brackets are escaped",
              "&lt;file&gt;" in cut[-1] and "<file>" not in cut[-1], cut[-1][-200:])
        check("the original list is not modified in place", len(long_) == _MAX_PARTS + 40)
        # The boundary exactly at the limit: truncating one message earlier would
        # silently swallow the last message of the report, which is exactly the footer
        # with the caveats.
        edge = ["x"] * _MAX_PARTS
        check("exactly the limit -- no note", _trim_parts(edge, "cli") == edge)

        # A lost message must be named: an undelivered piece of the report reads as
        # "no more findings", which is exactly the mistake that makes the user consider
        # the file clean.
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
        await _send_parts(fl, ["one", "two", "three"], "python -m core.inspect <file>")
        check("a failure of one message did not swallow the rest", "three" in fl.sent, fl.sent)
        check("the loss is stated, and the report is called incomplete, not empty",
              any("arrived incompletely" in s and "1 of 3" in s for s in fl.sent), fl.sent)
        g["_SEND_PAUSE"] = _SEND_PAUSE

        print("11. \"Regenerate\": the REGENERATE profile, an honest refusal without "
              "LibreOffice, a file when it is present")
        real_clean, real_soffice = saved[0], core_clean.find_soffice
        calls = []

        def spy(s, d, p, keep=None):
            calls.append((p, keep))
            return real_clean(s, d, p, keep)

        g["clean_file"] = spy
        core_clean.find_soffice = lambda: None          # LibreOffice "not installed"
        try:
            _, _, ev, ch = await send(fx.make_docx, "dx11a")
            await click(ch, "cl:regen")
            check("pressing cl:regen called clean_file exactly once, and with REGENERATE",
                  len(calls) == 1 and calls[0][0] is Profile.REGENERATE, calls)
            check("no LibreOffice: file NOT delivered, \"NOT cleaned\" stated",
                  not docs(ev) and "NOT cleaned" in ch.text, ch.text)
            check("the refusal names the reason (LibreOffice is needed), and doesn't silently "
                  "swap the profile",
                  any("LibreOffice" in t and ("requires" in t or "not found" in t) for t in texts(ev)),
                  [t[:160] for t in texts(ev)][-2:])
            check("neither Stealth nor Paranoid was run in this scenario",
                  all(c[0] is Profile.REGENERATE for c in calls), calls)
            check("directories removed", left() == base)
        finally:
            core_clean.find_soffice = real_soffice
        calls.clear()
        if real_soffice():
            src, _, ev, ch = await send(fx.make_docx, "dx11b")
            await click(ch, "cl:regen")
            d = docs(ev)
            check("LibreOffice present: clean_file called with REGENERATE",
                  len(calls) == 1 and calls[0][0] is Profile.REGENERATE, calls)
            check("LibreOffice present: file delivered as a document document_*.docx",
                  len(d) == 1 and re.fullmatch(r"document_[0-9a-f]{6}\.docx", d[0].name) is not None,
                  [x.name for x in d] or texts(ev)[-1][:300])
            if d:
                check("regenerated file: original values are gone", survivors(src, d[0].data, "dx11b") == [])
                check("caption names the Regenerate profile", "Regenerate" in d[0].caption, d[0].caption)
        else:
            print("  [--] no LibreOffice on this machine: the \"file delivered\" branch was not "
                  "checked")
        g["clean_file"] = saved[0]

        print("12. whitelist (keep): toggles, the user's choice is visible in the report")
        calls.clear()
        g["clean_file"] = spy
        src, exp, ev, ch = await send(fx.make_pdf, "pdf12")
        key = next(iter(_pending))
        cb = await click(ch, "kp:author")
        lbl = [b.text for r in ch.markup.inline_keyboard for b in r]
        check("toggle switched on: button ☑, entry intact, keep={'author'}",
              "☑ Keep: the author" in lbl and _pending[key].keep == {"author"}
              and cb.acks == [(None, False)], (lbl, cb.acks))
        await click(ch, "kp:author")
        check("pressing again switches it off", _pending[key].keep == set()
              and "☐ Keep: the author" in [b.text for r in ch.markup.inline_keyboard for b in r])
        cb = await click(ch, "kp:author", user_id=8)
        check("someone else's toggle rejected, state unchanged",
              cb.acks[0][1] and _pending[key].keep == set(), cb.acks)
        cb = await click(ch, "kp:nn")
        check("\"kp:nn\" outside ZIP and unknown keys change nothing", _pending[key].keep == set()
              and _pending[key].neutral is True)
        await click(ch, "kp:author")
        await click(ch, "kp:title")
        await click(ch, "cl:stealth")
        d = docs(ev)
        sent_keep = calls[0][1] if calls else None
        check("exactly the keys of the selection went into clean_file",
              sent_keep and {"/Author", "dc:creator", "/Title"} <= set(sent_keep), sent_keep)
        pre = next((i for i, (k, m) in enumerate(ev) if k == "text" and "by your choice" in m.text), -1)
        rep = next((i for i, (k, m) in enumerate(ev) if k == "text" and "Metadata cleaning" in m.text), -1)
        check("file delivered, the choice is named BEFORE the report as a choice, not as a "
              "cleaning shortfall",
              len(d) == 1 and 0 <= pre < rep and "This is not a cleaning shortfall" in ev[pre][1].text
              and "NOT clean" in ev[pre][1].text, (pre, rep))
        check("caption: 📌, \"by your choice\", \"NOT clean\", no ✅ and no \"NOT FULLY CLEANED\"",
              d and "\U0001F4CC" in d[0].caption and "by your choice" in d[0].caption
              and "NOT clean" in d[0].caption and "✅" not in d[0].caption
              and "NOT FULLY CLEANED" not in d[0].caption, d[0].caption if d else None)
        check("the author really stayed in the file (that is the choice)",
              d and len(survivors(src, d[0].data, "pdf12")) >= 1)
        check("the detectability assessment is named here too (cleanreport doesn't print it when "
              "values survive)",
              any("Cleaning detectability" in m.text for k, m in ev if k == "text" and "by your choice" in m.text))
        for maker, nm, ext_ in ((fx.make_odt, "odt12", "odt"), (fx.make_rtf, "rtf12", "rtf"),
                                (fx.make_pptx, "pptx12", "pptx")):
            calls.clear()
            src, exp, ev, ch = await send(maker, nm)
            await click(ch, "kp:author")
            await click(ch, "cl:stealth")
            d = docs(ev)
            check("%s: keep=author -> the survivor is explained by the choice, caption 📌 "
                  "(cleanreport doesn't write KEPT)" % ext_,
                  len(d) == 1 and "\U0001F4CC" in d[0].caption and "✅" not in d[0].caption, d[0].caption if d else texts(ev)[-1][:200])
        # An author found NOT in the author field is a cleaning shortfall, even with the toggle on.
        sv_ok = core_clean.Survivor("identity", "/Info", "Author", "Petrov Petr", "/Info > Author")
        sv_bad = core_clean.Survivor("identity", "/Info", "Author", "Petrov Petr", "/Info > Title")
        mk2 = lambda **kw: core_clean.DispatchResult("a", "b", "pdf", "stealth", ok=True, verified=True, **kw)  # noqa: E731
        check("_verdict: author in the author field + toggle -> kept",
              _verdict(mk2(survived=[sv_ok]), ["author"])[0] == "kept")
        check("_verdict: the same author WITHOUT the toggle -> bad",
              _verdict(mk2(survived=[sv_ok]), [])[0] == "bad")
        check("_verdict: author found in the title with \"keep author\" -> bad, not a choice",
              _verdict(mk2(survived=[sv_bad]), ["author"])[0] == "bad")
        check("_verdict: choice + an extraneous leak -> bad",
              _verdict(mk2(survived=[sv_ok, core_clean.Survivor("environment", "x", "Path", "C:/Users/ivan", "y > Path")]),
                       ["author"])[0] == "bad")
        check("_verdict: choice + incomplete parse -> warn, not kept",
              _verdict(mk2(survived=[sv_ok], gaps=["after cleaning: x"]), ["author"])[0] == "warn")
        check("_verdict: asked to keep, but nothing survived -> ok mentioning the choice",
              (lambda r: r[0] == "ok" and "document title" in r[1])(_verdict(mk2(), ["title"])))
        # Regeneration doesn't understand the whitelist: a silent substitution would be deception.
        calls.clear()
        src, exp, ev, ch = await send(fx.make_docx, "dx12r")
        key = next(iter(_pending))
        await click(ch, "kp:title")
        cb = await click(ch, "cl:regen")
        check("keep + \"Regenerate\": an explicit refusal-warning, cleaning not started",
              cb.acks and cb.acks[0][1] and "can't do" in cb.acks[0][0] and not calls and not docs(ev), cb.acks)
        check("after the refusal the entry and the toggles are intact -- you can turn them off "
              "and choose something else",
              key in _pending and _pending[key].keep == {"title"})
        await click(ch, "kp:title")
        # ICC for images
        calls.clear()
        src, exp, ev, ch = await send(fx.make_png, "png12")
        lbl = [b.text for r in ch.markup.inline_keyboard for b in r]
        check("image: the only toggle is ICC", lbl[:2] == ["☐ Keep: ICC profile", "Clean (Stealth)"], lbl)
        await click(ch, "kp:icc")
        await click(ch, "cl:paranoid")
        check("ICC: keep={'icc'} reached the cleaner, file delivered",
              calls and calls[0][1] == {"icc"} and len(docs(ev)) == 1, calls)
        # TTL: the toggles live in the same entry as file_id
        src, exp, ev, ch = await send(fx.make_pdf, "pdf12t")
        await click(ch, "kp:title")
        for v in _pending.values():
            v.expires = 0
        cb = await click(ch, "kp:author")
        check("the entry expired together with the toggles: \"expired\"",
              cb.acks[0][1] and "expired" in cb.acks[0][0] and not _pending, cb.acks)
        g["clean_file"] = saved[0]

        print("13. ZIP: batch cleaning")
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
        check("ZIP: batch cleaning offered, with the file count and toggles",
              ch and "ZIP archive" in ch.text and "files inside: 4" in ch.text
              and lbl == ["☐ Keep: document title", "☐ Keep: the author", "☐ Keep: ICC profile",
                          "Names in archive: neutral", "Clean all (Stealth)", "Clean all (Paranoid)",
                          "Regenerate all (LibreOffice)", "Don't clean"], lbl)
        check("ZIP: nothing on disk after the offer", left() == base)
        cb = await click(ch, "bt:stealth")
        d = docs(ev)
        rep_txt = "\n".join(texts(ev))
        check("ZIP: answered the press immediately", cb.acks and cb.acks[0][0] == "Cleaning…", cb.acks)
        check("ZIP: a report for each file in the chat, with the original names",
              "BATCH CLEANING REPORT" in rep_txt and "leaky.docx" in rep_txt and "probe.pdf" in rep_txt
              and "probe.png" in rep_txt, rep_txt[:300])
        check("ZIP: the refusal is named and explained (legacy doc), not lost",
              "NOT CLEANED" in rep_txt and "reason" in rep_txt and "doc" in rep_txt.lower())
        check("ZIP: the detectability assessment is named", "Cleaning detectability" in rep_txt)
        arc = [x for x in d if x.name.endswith(".zip")]
        check("ZIP: one archive delivered with the neutral name archive_*.zip",
              len(arc) == 1 and re.fullmatch(r"archive_[0-9a-f]{6}\.zip", arc[0].name) is not None, [x.name for x in d])
        if arc:
            outp = os.path.join(work, "zip1_out.zip")
            with open(outp, "wb") as fh:
                fh.write(arc[0].data)
            with zf_.ZipFile(outp) as z:
                names = z.namelist()
                inner_report = z.read("_REPORT.txt").decode("utf-8")
                members = {n: z.read(n) for n in names}
            check("ZIP: the caption honestly says \"only the cleaned\", the rest NOT included",
                  "NOT included" in arc[0].caption and "3 of 4" in arc[0].caption and "✅" not in arc[0].caption,
                  arc[0].caption)
            check("ZIP: names inside are neutral, no originals (a name is metadata too)",
                  sorted(names) == ["_REPORT.txt", "doc_0001.docx", "doc_0002.pdf", "doc_0003.png"]
                  and not any(k in " ".join(names) for k in ("leaky", "probe", "Папка")), names)
            check("ZIP: _REPORT.txt inside the archive has no original names",
                  "leaky" not in inner_report and "probe" not in inner_report and "№0001" in inner_report)
            check("ZIP: the legacy .doc did not get into the archive (no \"cleaned\" original)",
                  not any(n.endswith(".doc") or "NOT_CLEANED" in n for n in names))
            srcs = list(exp["orig"].values())
            for nm_, orig in zip(("doc_0001.docx", "doc_0002.pdf", "doc_0003.png"), srcs):
                check("ZIP: %s -- original values are gone (re-inspection)" % nm_,
                      survivors(orig, members[nm_], "z_" + nm_) == [])
        check("ZIP: directories removed", left() == base and not _pending)
        leak = [v for v in ("leaky", "probe") if any(v in line for line in logs)]
        check("ZIP: file names did not get into the logs", not leak, leak)

        # names toggle: original names stay, but the safe path normalization remains
        src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf), "pack2"), "zip2")
        await click(ch, "kp:nn")
        check("ZIP: \"Names in archive\" switched to original",
              "Names in archive: original" in [b.text for r in ch.markup.inline_keyboard for b in r])
        await click(ch, "bt:paranoid")
        arc = [x for x in docs(ev) if x.name.endswith(".zip")]
        check("ZIP: all cleaned -> caption ✅ \"2 of 2\"",
              len(arc) == 1 and "✅" in arc[0].caption and "2 of 2" in arc[0].caption, [x.caption for x in arc])
        if arc:
            outp = os.path.join(work, "zip2_out.zip")
            with open(outp, "wb") as fh:
                fh.write(arc[0].data)
            with zf_.ZipFile(outp) as z:
                names = sorted(z.namelist())
            check("ZIP: original names kept on request", names == ["_REPORT.txt", "Папка/leaky.docx", "Папка/probe.pdf"], names)

        # keep in a batch: a file with a kept field is not proven -> not in the archive, explained by the choice
        src, exp, ev, ch = await send(mkzip((fx.make_pdf, fx.make_png), "pack3"), "zip3")
        await click(ch, "kp:author")
        await click(ch, "bt:stealth")
        arc = [x for x in docs(ev) if x.name.endswith(".zip")]
        t3 = "\n".join(texts(ev))
        check("ZIP + keep: pdf not proven (author left by choice) and explained by the choice, "
              "not by a shortfall",
              "your choice" in t3 and "author" in t3 and len(arc) == 1, t3[-400:])
        if arc:
            outp = os.path.join(work, "zip3_out.zip")
            with open(outp, "wb") as fh:
                fh.write(arc[0].data)
            with zf_.ZipFile(outp) as z:
                check("ZIP + keep: only the png in the archive, the pdf with the author was not "
                      "sent as clean",
                      sorted(z.namelist()) == ["_REPORT.txt", "doc_0002.png"], z.namelist())
        # keep + regen in ZIP
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack3b"), "zip3b")
        await click(ch, "kp:title")
        cb = await click(ch, "bt:regen")
        check("ZIP: keep + \"Regenerate all\" -- refusal, the batch is not started",
              cb.acks[0][1] and "can't do" in cb.acks[0][0] and not docs(ev) and len(_pending) == 1, cb.acks)
        await click(ch, "cl:no")

        # nothing cleaned: no archive
        src, exp, ev, ch = await send(mkzip((fx.make_doc,), "pack4", extra=(("note.txt", "just text"),)), "zip4")
        await click(ch, "bt:stealth")
        check("ZIP: nothing cleaned -> \"No file was cleaned\", the archive is not sent",
              not docs(ev) and any("No file was cleaned" in t for t in texts(ev)), texts(ev)[-1][:200])

        # not a ZIP / damaged: an honest refusal from the core, not a crash
        def bad_zip(dirpath):
            p = os.path.join(dirpath, "broken.zip")
            with open(p, "wb") as fh:
                fh.write(b"PK\x03\x04" + b"\x00" * 40)
            return p, {}
        src, exp, ev, ch = await send(bad_zip, "zip5")
        if ch is not None and any(b.callback_data == "bt:stealth" for r in ch.markup.inline_keyboard for b in r):
            await click(ch, "bt:stealth")
            check("ZIP broken: no file, \"NOT cleaned\"/reason stated",
                  not docs(ev) and any("NOT cleaned" in (m.text or "") for k, m in ev + [("text", ch)] if k == "text"), ch.text)
        else:
            check("ZIP broken: cleaning not offered and no file", not docs(ev))

        # Bot API sending limit
        saved_lim = config.MAX_SEND_BYTES
        config.MAX_SEND_BYTES = 2048
        try:
            src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf), "pack6"), "zip6")
            await click(ch, "bt:stealth")
        finally:
            config.MAX_SEND_BYTES = saved_lim
        check("ZIP: result larger than the sending limit -> a clear message, no archive in the "
              "chat, the bot didn't crash",
              not [x for x in docs(ev) if x.name.endswith(".zip")]
              and any("Result not sent" in t and "Bot API" in t and "MB" in t for t in texts(ev)),
              texts(ev)[-1][:300])
        check("ZIP: the sending limit in config equals 50 MB", config.MAX_SEND_BYTES == 50 * 1024 * 1024)
        check("config: a malformed id is skipped instead of raising ValueError",
              config._ids("--5, ², 12; -7") == [12, -7])

        # long report -> as a document
        g["_BATCH_CHAT_PARTS"] = 0
        try:
            src, exp, ev, ch = await send(mkzip((fx.make_docx, fx.make_pdf), "pack7"), "zip7")
            await click(ch, "bt:stealth")
        finally:
            g["_BATCH_CHAT_PARTS"] = 4
        rd = [x for x in docs(ev) if x.name.endswith(".txt")]
        check("ZIP: a long report goes as a document report_*.txt with full names",
              len(rd) == 1 and re.fullmatch(r"report_[0-9a-f]{6}\.txt", rd[0].name) and b"leaky.docx" in rd[0].data,
              [x.name for x in docs(ev)])

        # exception and refusal of the batch core
        real_batch = g["clean_batch"]

        def boom_batch(*a, **k):
            raise RuntimeError("SECRET-FROM-ARCHIVE")
        g["clean_batch"] = boom_batch
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack8"), "zip8")
        await click(ch, "bt:stealth")
        check("ZIP: exception in the core -> human text, no archive, the value is neither in the "
              "chat nor in the logs",
              "internal error" in ch.text and "NOT sent" in ch.text and "SECRET" not in ch.text
              and not docs(ev) and not any("SECRET" in x for x in logs) and left() == base, ch.text)

        def refuse_batch(s, d, profile, keep=None, force=False, **kw):
            r = real_batch.__globals__["BatchResult"](src=s, dst=d, profile="stealth")
            r.err("test: batch rejected")
            return r
        g["clean_batch"] = refuse_batch
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack9"), "zip9")
        await click(ch, "bt:stealth")
        check("ZIP: ok=False -> \"Archive NOT cleaned\" with the reason, the archive is not sent",
              "Archive NOT cleaned" in ch.text and "batch rejected" in ch.text and not docs(ev), ch.text)

        # timeout: the thread finishes, the directory is removed after it
        def slow_batch(s, d, profile, keep=None, force=False, **kw):
            time.sleep(1.0)
            return real_batch(s, d, profile, keep, force, **kw)
        g["clean_batch"], g["_BATCH_TIMEOUT"] = slow_batch, 0.3
        src, exp, ev, ch = await send(mkzip((fx.make_pdf,), "pack10"), "zip10")
        await click(ch, "bt:stealth")
        check("ZIP: timeout named, no archive", "did not finish within" in ch.text and not docs(ev), ch.text)
        await asyncio.sleep(2.5)
        check("ZIP: after the timeout the thread finished -- directories removed", left() == base, left())
        g["clean_batch"], g["_BATCH_TIMEOUT"] = real_batch, 1800

        print("14. the detectability assessment reaches the user")
        src, exp, ev, ch = await send(fx.make_docx, "dx14")
        await click(ch, "cl:stealth")
        det = [t for t in texts(ev) if "ASSESSMENT:" in t]
        check("docx: after cleaning the bot's reply has an ASSESSMENT line (from res.detect)", len(det) == 1, texts(ev)[-1][:200])
        src, exp, ev, ch = await send(fx.make_pdf, "pdf14")
        await click(ch, "cl:stealth")
        check("pdf: after cleaning the bot's reply has an ASSESSMENT line", any("ASSESSMENT:" in t for t in texts(ev)))
        check("the assessment is not duplicated by the bot where cleanreport already prints it",
              sum("Cleaning detectability" in t for t in texts(ev)) == 0, [t[:80] for t in texts(ev)])

        print("15. reports are deleted: button, /clear, timer; the result file is untouched")
        _reports.clear()
        bot.deleted.clear()
        src, exp, ev, ch = await send(fx.make_pdf, "pdf15")
        rep_msgs = [m for k, m in ev if k == "text" and m.markup is not None
                    and [b.callback_data for r in m.markup.inline_keyboard for b in r] == ["rm:all"]]
        check("under the last report message there is a \"Delete report\" button, exactly one",
              len(rep_msgs) == 1, len(rep_msgs))
        tracked = set(_reports)
        check("report messages are remembered for deletion", len(tracked) >= 1, len(tracked))
        await click(ch, "cl:stealth")
        n_before = len(_reports)
        check("the cleaning report is remembered too", n_before > len(tracked), (n_before, len(tracked)))
        cb = FakeCb(rep_msgs[0], "rm:all")
        await on_delete_reports(cb)
        check("the button deleted all of the chat's reports and forgot them",
              not _reports and set(bot.deleted) >= tracked and cb.acks, (len(_reports), cb.acks))
        check("the cleaned file was not deleted (it is not in the registry)", len(docs(ev)) == 1)
        cb = FakeCb(rep_msgs[0], "rm:all", user_id=99)
        bot.deleted.clear()
        await on_delete_reports(cb)
        check("a stranger can't delete reports", not bot.deleted)

        _reports.clear()
        bot.deleted.clear()
        await send(fx.make_pdf, "pdf15b")
        ev = []
        m = FakeMsg(bot, ev)
        await on_clear(m)
        check("/clear deleted the reports and said how many",
              bot.deleted and not _reports and any("Report messages deleted" in t for t in texts(ev)), texts(ev))
        check("/clear says it removes only its own reports and the command, not that it spares "
              "your messages",
              any("only its own reports and this command" in t and "your own messages" not in t
                  for t in texts(ev)), texts(ev))

        _reports.clear()
        _track(SimpleNamespace(chat=SimpleNamespace(id=5), message_id=1))
        _reports[(5, 1)] -= 3600
        _track(SimpleNamespace(chat=SimpleNamespace(id=5), message_id=2))
        bot.deleted.clear()
        n = await _delete_reports(bot, cutoff=time.monotonic() - 30 * 60)
        check("the timer deletes only those older than the limit", n == 1 and bot.deleted == [(5, 1)]
              and (5, 2) in _reports, (n, bot.deleted))
        _track(None)
        check("_track(None) doesn't crash", True)
        _reports.clear()

        print("16. /status, /help, the single-instance lock")
        ev = []
        await on_status(FakeMsg(bot, ev))
        st = texts(ev)[-1] if texts(ev) else ""
        check("/status names LibreOffice, exiftool and auto-deletion",
              "LibreOffice" in st and "exiftool" in st and "Auto-deletion" in st, st)
        check("/status doesn't reveal paths", ":\\" not in st and "/usr" not in st, st)
        ev = []
        await on_help(FakeMsg(bot, ev))
        h = texts(ev)[-1] if texts(ev) else ""
        check("help is assembled without \"{ttl}\" and names /clear", "{" not in h and "/clear" in h, h[-300:])
        check("there is no literal \\n in the bot's texts (it shows up in Telegram)",
              not any("\\n" in t for t in (HELP_TEXT, CHOICE_TEXT, REGEN_BULLET, LEGACY_TEXT, BATCH_TEXT)))
        lock_path = os.path.join(work, "t.lock")
        first = _acquire_lock(lock_path)
        second = _acquire_lock(lock_path)
        check("lock: the first took it, the second didn't", first is not None and second is None)
        if first:
            first.close()
        third = _acquire_lock(lock_path)
        check("lock: free again after closing", third is not None)
        if third:
            third.close()

        print("17. /lang: the language of the replies")
        saved_lang = (config.LANG_PATH, config.BOT_LANG, dict(_LANGS))
        config.LANG_PATH = os.path.join(work, "lang.json")
        try:
            config.BOT_LANG = "ru"
            _LANGS.clear()
            mw = _LangMiddleware()

            async def probe(event, data):
                return tr("Help")

            def user(uid):
                return SimpleNamespace(from_user=SimpleNamespace(id=uid))

            check("the default language comes from BOT_LANG", await mw(probe, user(7), {}) == "Справка")
            ev = []
            await on_lang(FakeMsg(bot, ev, text="/lang"))
            kb = ev[-1][1].markup if ev else None
            check("/lang offers both languages", kb is not None and [
                b.callback_data for r in kb.inline_keyboard for b in r] == ["lang:ru", "lang:en"])
            cb = FakeCb(ev[-1][1], "lang:en")
            await on_lang_choice(cb)
            check("the button switches to English and confirms in English",
                  _LANGS.get(7) == "en" and cb.acks and cb.acks[0][0] == "Done: the bot replies in English.",
                  cb.acks)
            check("the choice applies to the next update", await mw(probe, user(7), {}) == "Help")
            check("other users keep the default", await mw(probe, user(8), {}) == "Справка")
            _LANGS.clear()
            _load_langs()
            check("the choice survives a restart", _LANGS.get(7) == "en", _LANGS)
            ev = []
            await on_lang(FakeMsg(bot, ev, text="/lang ru"))
            check("/lang ru switches right away",
                  _LANGS.get(7) == "ru" and texts(ev) == ["Готово: бот отвечает по-русски."], texts(ev))
            await on_lang_choice(FakeCb(FakeMsg(bot, []), "lang:de"))
            check("an unknown code changes nothing", _LANGS.get(7) == "ru", _LANGS)
            await on_lang_choice(FakeCb(FakeMsg(bot, []), "lang:en", user_id=99))
            check("a stranger can't switch", 99 not in _LANGS, _LANGS)
            with use_lang("ru"):
                h = _help_text()
                kb_text = _delete_kb().inline_keyboard[0][0].text
            check("Russian help is in Russian and mentions /lang",
                  "/lang" in h and "{" not in h and re.search("[а-я]", h) is not None, h[:200])
            check("the report button follows the language", re.search("[а-я]", kb_text) is not None, kb_text)
        finally:
            config.LANG_PATH, config.BOT_LANG = saved_lang[0], saved_lang[1]
            _LANGS.clear()
            _LANGS.update(saved_lang[2])
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

    print("\nTOTAL: %d checks, %d failed" % (total[0], len(fails)))
    for f in fails:
        print("  FAILED: " + f)
    return 1 if fails else 0


if __name__ == "__main__":
    main()
