"""Пересоздание файла через LibreOffice. Фаза 4, профиль regenerate.

Необязательный путь: нет soffice -- честный отказ, а не тихая подмена
чисткой на месте. Пользователь выбрал regenerate ради незаметности.

Конвертация идёт с одноразовым профилем и пустым именем пользователя.
Иначе LibreOffice впишет в документ имя владельца машины.

Публичный API:
    find_soffice() -> str | None
    clean_regen(src, dst, profile, keep, force_signed=False) -> CleanResult
        fmt результата -- формат ВЫХОДА (doc становится docx).

Исключений наружу не бросает. Вход не изменяется.
"""

from __future__ import annotations

import glob
import html
import os
import re
import shutil
import subprocess
import tempfile

from core.cleanmodel import Act, CleanResult, Profile

__all__ = ["clean_regen", "find_soffice", "OUTPUT_FMT"]

# Легаси OLE2 на месте не чистится: честный выход -- современный OOXML.
OUTPUT_FMT = {
    "doc": "docx", "xls": "xlsx", "ppt": "pptx",
    "docx": "docx", "xlsx": "xlsx", "pptx": "pptx",
    "odt": "odt", "ods": "ods", "odp": "odp", "rtf": "rtf",
}


# Где LibreOffice лежит, если его нет в PATH. Windows и macOS в PATH себя не
# добавляют; на Linux пакетный soffice обычно в PATH, но snap и ручная
# установка в /opt -- нет.
_SOFFICE_PLACES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    "/usr/lib/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
)


def find_soffice():
    """Путь к soffice, если LibreOffice установлен. Иначе None.

    Windows: soffice.com, а не soffice.exe -- exe отцепляется от консоли и
    ничего не печатает. Сначала стандартные каталоги установки, потом PATH.
    """
    for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        path = os.path.join(base, "LibreOffice", "program", "soffice.com")
        if os.path.isfile(path):
            return path
    found = shutil.which("soffice.com") or shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    # /opt/libreofficeX.Y -- так ставит официальный .deb/.rpm с сайта
    extra = sorted(glob.glob("/opt/libreoffice*/program/soffice"), reverse=True)
    for path in _SOFFICE_PLACES + tuple(extra):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _file_url(path) -> str:
    return "file:///" + os.path.abspath(path).replace("\\", "/").lstrip("/")


def _patch_profile(profile_dir):
    """Дописать снятие личных данных. Готовый xcu LibreOffice при первом
    запуске перезаписывает, поэтому править надо уже созданный им файл."""
    path = os.path.join(profile_dir, "user", "registrymodifications.xcu")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if "RemovePersonalInfoOnSaving" in text:
        return True
    extra = (
        '<item oor:path="/org.openoffice.Office.Common/Security">'
        '<prop oor:name="RemovePersonalInfoOnSaving" oor:op="fuse"><value>true</value></prop>'
        '<prop oor:name="RemoveEditingTimeOnSaving" oor:op="fuse"><value>true</value></prop>'
        "</item>"
    )
    if "</oor:items>" not in text:
        return False
    text = text.replace("</oor:items>", extra + "</oor:items>", 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def _run(soffice, profile_dir, extra, timeout):
    cmd = [
        soffice, "--headless", "--norestore", "--nolockcheck", "--nologo",
        "--nofirststartwizard",
        "-env:UserInstallation=%s" % _file_url(profile_dir),
    ] + extra
    return subprocess.run(cmd, timeout=timeout, capture_output=True, check=False)


def _empty_profile(root) -> str:
    user = os.path.join(root, "user")
    os.makedirs(user, exist_ok=True)
    return root


# Атрибуты, по которым LibreOffice при конвертации сам что-то загружает:
# Target у .rels, xlink:href у ODF, href/src у VML.
_LINK_ATTR_RE = re.compile(
    rb'((?:Target|href|r:link|src)\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
# Куда загрузка уводит с машины: любая схема с «//» (http, ftp, smb, dav...),
# file: и UNC (\\сервер, //сервер) -- на Windows это SMB-подключение, которое
# отдаёт чужому серверу IP и NTLM-хэш владельца бота, -- и абсолютный путь с
# диском. Замерено: по file://-ссылке на картинку LibreOffice открывает файл.
# Внутренние цели пакета («media/x.png», «/word/document.xml») сюда не попадают.
_REMOTE_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:)?(?://|\\\\)|^file:|^[a-z]:[\\/]",
                        re.IGNORECASE)


def _remote(value: bytes) -> bool:
    """Значение атрибута уводит наружу. Проверяем РАСКОДИРОВАННОЕ значение:
    «&#104;ttp://» XML-парсер LibreOffice прочтёт как http://, а по сырым байтам
    такой адрес проходил мимо. Пробелы и управляющие символы внутри адреса
    URL-парсеры выбрасывают -- выбрасываем и мы."""
    text = html.unescape(value.decode("utf-8", "replace"))
    return bool(_REMOTE_RE.match(re.sub(r"[\x00-\x20]", "", text)))


def _defang_bytes(raw: bytes):
    """Ссылки наружу -> пустая строка той же формы. Возврат: (байты, сколько)."""
    hits = 0

    def sub(m):
        nonlocal hits
        if not _remote(m.group(3)):
            return m.group(0)
        hits += 1
        return m.group(1) + m.group(2) + m.group(2)

    return _LINK_ATTR_RE.sub(sub, raw), hits


def _defang_external(src, work, res):
    """Копия файла без ссылок наружу; при любой заминке -- None (отказ).

    Работает по сырым байтам XML-частей ZIP-пакета (OOXML и ODF) и по тексту
    RTF. Разбирать XML ради этого не нужно: цель -- чтобы LibreOffice не нашёл
    чего загружать, а не чтобы получился образцовый документ. Ссылка
    заменяется на пустую строку той же формы, поэтому структура не ломается.

    Сам файл результата от этого не страдает: внешние ссылки всё равно
    вычищаются вторым проходом как ENVIRONMENT.
    """
    try:
        import zipfile

        from . import zipfix
        from .sniff import family, sniff

        fam = family(sniff(src))
        out = os.path.join(work, "defanged" + os.path.splitext(src)[1])

        if fam in ("ooxml", "odf"):
            replace, hits = {}, 0
            with zipfile.ZipFile(src) as z:
                for name in z.namelist():
                    if not name.lower().endswith((".xml", ".rels", ".vml")):
                        continue
                    raw = z.read(name)
                    new, n = _defang_bytes(raw)
                    if n:
                        replace[name] = new
                        hits += n
            if not hits:
                return src
            rep = zipfix.rebuild(src, out, replace=replace)
            if not getattr(rep, "ok", False) or not os.path.isfile(out):
                res.err("Внешние ссылки обезвредить не удалось, пересоздание "
                        "отменено: LibreOffice пошёл бы по ним в сеть.")
                return None
            res.act(Act.REMOVED, "внешние ссылки", "Ссылки на сеть до конвертации",
                    "%d шт." % hits, "",
                    "LibreOffice при конвертации сам загружает внешние картинки, "
                    "раскрывая ваш IP владельцу ссылки. Обезврежены до запуска.")
            return out

        if fam == "rtf":
            with open(src, "rb") as fh:
                raw = fh.read()
            new, n = _defang_bytes(raw)
            if not n:
                return src
            with open(out, "wb") as fh:
                fh.write(new)
            res.act(Act.REMOVED, "внешние ссылки", "Ссылки на сеть до конвертации",
                    "%d шт." % n, "", "Обезврежены до запуска LibreOffice.")
            return out
    except Exception:  # noqa: BLE001
        res.err("Проверка внешних ссылок сорвалась, пересоздание отменено: "
                "LibreOffice мог бы пойти по ним в сеть.")
        return None
    return src


def _scrub_after_convert(path, res) -> None:
    """Вычистить свойства уже пересозданного файла нашим же чистильщиком.

    Профиль STEALTH: после конвертации файл ДЕЙСТВИТЕЛЬНО сделан LibreOffice,
    поэтому оставить Application=LibreOffice -- правда, а пустые поля личности
    это рядовое состояние LibreOffice с незаполненным именем пользователя.
    Для OOXML такая пара обычно сама выдаёт чистку, но здесь пакет собран
    стоковым приложением целиком, и противоречия не возникает.
    """
    try:
        from .sniff import family, sniff
        fam = family(sniff(path))
        if fam == "ooxml":
            import functools

            from .clean_ooxml import clean_ooxml
            # Пользовательских свойств у свежего файла LibreOffice нет: всё, что
            # там есть, приехало из исходника. STEALTH часть таких свойств
            # оставляет как безобидные, а инспектор .doc считает их личностью --
            # замерено на «Отдел» из DocumentSummaryInformation.
            _inner = functools.partial(clean_ooxml, drop_custom=True)
        elif fam == "odf":
            from .clean_odf import clean_odf as _inner
        elif fam == "rtf":
            # RTF -> RTF: LibreOffice переносит \info так же, как свойства OOXML.
            from .clean_rtf import clean_rtf as _inner
        else:
            res.err("После конвертации формат не поддержан дочисткой: "
                    "свойства документа могли остаться.")
            return
    except Exception:
        res.err("Дочистка после конвертации недоступна: свойства документа "
                "могли остаться.")
        return

    work = tempfile.mkdtemp(prefix="lo_scrub_")
    try:
        tmp_out = os.path.join(work, os.path.basename(path))
        inner = _inner(path, tmp_out, Profile.STEALTH, None)
        if inner.ok and os.path.isfile(tmp_out) and os.path.getsize(tmp_out) > 0:
            shutil.copyfile(tmp_out, path)
            for a in inner.actions:
                res.actions.append(a)
            for e in inner.errors:
                res.err(e)
        else:
            res.err("Дочистка после конвертации не прошла: свойства документа "
                    "могли остаться в файле. %s" % ((inner.errors or [""])[0])[:160])
    except Exception:  # noqa: BLE001
        res.err("Дочистка после конвертации сорвалась: свойства документа "
                "могли остаться.")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def clean_regen(src, dst, profile=Profile.REGENERATE, keep=None, force_signed=False) -> CleanResult:
    """Пересохранить src через LibreOffice в dst. Не бросает.

    keep и force_signed не используются: профиль один, стоковое приложение
    само решает, что оставить. Сигнатура совпадает с остальными чистильщиками.
    """
    del keep, force_signed, profile
    res = CleanResult(src=str(src), dst=str(dst), fmt="unknown", profile="regenerate")
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    # sniff ещё не звали: расширение исходника -- подсказка для LO, формат
    # проверит диспетчер повторной инспекцией.
    target = OUTPUT_FMT.get(ext)
    if target is None:
        res.err("Regenerate для этого расширения не применяется.")
        return res
    res.fmt = target
    soffice = find_soffice()
    if not soffice:
        res.err("LibreOffice не найден. Regenerate не подменяется чисткой на месте.")
        return res
    work = tempfile.mkdtemp(prefix="lo_regen_")
    try:
        profile_dir = _empty_profile(os.path.join(work, "profile"))
        outdir = os.path.join(work, "out")
        os.makedirs(outdir)

        # Внешние ссылки обезвреживаются ДО конвертации. LibreOffice при
        # --convert-to САМ скачивает внешние картинки документа: замерено, что
        # docx с a:blip r:link=http://... даёт три HTTP-запроса с машины
        # владельца. Для инструмента приватности это утечка в чистом виде --
        # отправитель файла узнаёт IP и может достучаться до внутренней сети.
        # Поэтому конвертируем не оригинал, а копию без внешних ссылок.
        src = _defang_external(src, work, res)
        if src is None:
            # Обезвредить не вышло -- файл НЕ пересоздаём. Запустить
            # LibreOffice «на всякий случай» значит сходить в сеть по чужому
            # адресу: отказ здесь безопаснее успеха.
            return res
        try:
            # Первый запуск только создаёт профиль: заранее положенный xcu
            # LibreOffice выбрасывает. Настройку дописываем во второй.
            _run(soffice, profile_dir, ["--terminate_after_init"], 60)
            if not _patch_profile(profile_dir):
                res.err("Не удалось включить снятие личных данных в профиле LibreOffice.")
                return res
            proc = _run(soffice, profile_dir,
                        ["--convert-to", target, "--outdir", outdir, src], 120)
        except subprocess.TimeoutExpired:
            res.err("LibreOffice не ответил за отведённое время. Файл не создан.")
            return res
        except OSError:
            res.err("LibreOffice не запустился. Файл не создан.")
            return res
        produced = os.path.join(
            outdir, os.path.splitext(os.path.basename(src))[0] + "." + target)
        if proc.returncode != 0 or not os.path.isfile(produced) or os.path.getsize(produced) == 0:
            res.err("LibreOffice не сохранил файл (код %s)." % proc.returncode)
            return res
        shutil.copyfile(produced, dst)
        res.act(Act.REGENERATED, "файл", "пересоздание", "", target,
                "Файл заново сохранён LibreOffice: пакет собран стоковым "
                "приложением, поэтому его структура согласована насквозь. "
                "Заявленное приложение теперь LibreOffice, не исходное. "
                "Вёрстка могла измениться.")

        # ВТОРОЙ ПРОХОД, без него режим течёт. LibreOffice при --convert-to
        # ПЕРЕНОСИТ свойства документа: замерено, что автор, кто сохранял,
        # организация и кастомная метка MSIP с email доезжают в результат
        # нетронутыми, несмотря на RemovePersonalInfoOnSaving в профиле (эта
        # опция работает при сохранении из интерфейса, а не при конвертации).
        # Полагаться на чужую настройку нельзя: regenerate решает задачу
        # НЕЗАМЕТНОСТИ, а чистоту даёт наш собственный чистильщик.
        _scrub_after_convert(dst, res)
        res.ok = True
    except Exception:  # noqa: BLE001
        res.ok = False
        res.err("Внутренняя ошибка Regenerate.")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return res
