"""Определение формата по содержимому, а не по расширению.

Расширение врёт: .docx может оказаться .doc, переименованным вручную,
а присланный "документ" -- вообще JPEG. Чистить файл не тем инспектором
опаснее, чем не чистить вообще: получится файл, который выглядит
обработанным, но течёт.

Только stdlib.
"""

from __future__ import annotations

import errno
import os
import zipfile

# Формат -> человекочитаемое имя. Ключи используются как Report.fmt.
NAMES = {
    "docx": "Word (OOXML)",
    "xlsx": "Excel (OOXML)",
    "pptx": "PowerPoint (OOXML)",
    "odt": "OpenDocument Text",
    "ods": "OpenDocument Spreadsheet",
    "odp": "OpenDocument Presentation",
    "zip": "ZIP-архив",
    "pdf": "PDF",
    "doc": "Word 97-2003 (OLE2)",
    "xls": "Excel 97-2003 (OLE2)",
    "ppt": "PowerPoint 97-2003 (OLE2)",
    "ole": "OLE2-контейнер (тип не опознан)",
    "rtf": "RTF",
    "jpeg": "JPEG",
    "png": "PNG",
    "tiff": "TIFF",
    "heic": "HEIF/HEIC",
    "webp": "WebP",
    "gif": "GIF",
    "text": "Текст/CSV",
    "unknown": "Неопознанный формат",
    # Не "не опознан", а "не прочитан": формат неизвестен, потому что файл не
    # открылся (облачный плейсхолдер, нет прав, занят, не найден). Причину
    # даёт unreadable_reason().
    "unreadable": "Файл недоступен для чтения",
}

# Какой инспектор обслуживает формат (заполняется в inspect.py).
FAMILY = {
    "docx": "ooxml", "xlsx": "ooxml", "pptx": "ooxml",
    "odt": "odf", "ods": "odf", "odp": "odf",
    "pdf": "pdf",
    "doc": "ole", "xls": "ole", "ppt": "ole", "ole": "ole",
    "rtf": "rtf",
    "jpeg": "image", "png": "image", "tiff": "image",
    "heic": "image", "webp": "image", "gif": "image",
    "zip": "zip", "text": "text", "unknown": "unknown",
    "unreadable": "unreadable",
}

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Мапинг mimetype ODF -> формат.
_ODF = {
    b"application/vnd.oasis.opendocument.text": "odt",
    b"application/vnd.oasis.opendocument.spreadsheet": "ods",
    b"application/vnd.oasis.opendocument.presentation": "odp",
}


def _read_head(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(4096)


def sniff(path) -> str:
    """Вернуть ключ формата. Никогда не бросает.

    'unreadable' -- файл не открылся (причина: unreadable_reason()).
    'unknown' -- файл прочитан, но сигнатура не опознана (или он пустой).
    Это разные диагнозы: первый лечится действием пользователя, второй нет.
    """
    try:
        head = _read_head(path)
    except (OSError, ValueError):  # ValueError -- NUL в пути
        return "unreadable"

    if not head:
        return "unknown"

    if head[:5] == b"%PDF-":
        return "pdf"
    # Некоторые PDF начинаются с мусора; спека допускает %PDF- в первых 1024 байт.
    if b"%PDF-" in head[:1024]:
        return "pdf"
    if head[:8] == OLE_MAGIC:
        return _sniff_ole(path)
    if head[:5] == b"{\\rtf":
        return "rtf"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim"):
            return "heic"
    if head[:2] == b"PK":
        return _sniff_zip(path)

    # Текст: нет NUL-байтов и декодируется.
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            for enc in ("cp1251", "cp1252"):
                try:
                    head.decode(enc)
                    return "text"
                except UnicodeDecodeError:
                    pass
    return "unknown"


def _sniff_zip(path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            # ODF объявляет себя первой записью 'mimetype' (несжатой).
            if "mimetype" in names:
                try:
                    mime = zf.read("mimetype").strip()
                    if mime in _ODF:
                        return _ODF[mime]
                except Exception:
                    pass
            if "[Content_Types].xml" in names:
                if any(n.startswith("word/") for n in names):
                    return "docx"
                if any(n.startswith("xl/") for n in names):
                    return "xlsx"
                if any(n.startswith("ppt/") for n in names):
                    return "pptx"
            return "zip"
    except (zipfile.BadZipFile, OSError):
        return "zip"


def _sniff_ole(path) -> str:
    """Тип OLE2 определяется по именам потоков ВЕРХНЕГО УРОВНЯ контейнера.

    Именно верхнего, а не всего дерева. Вложенный OLE-объект живёт внутри
    хранилища (ObjectPool/_1396712391/WordDocument), и если искать имена
    плоско по всему дереву, то книга Excel со встроенным документом Word
    определится как .doc -- после чего её разберёт не тот инспектор.
    Чистить файл не тем инспектором опаснее, чем не чистить вообще:
    получится файл, который выглядит обработанным, но течёт.
    """
    try:
        import olefile
    except ImportError:
        return "ole"
    try:
        if not olefile.isOleFile(path):
            return "ole"
        with olefile.OleFileIO(path) as ole:
            # listdir() отдаёт путь списком компонентов; длина 1 == верхний уровень.
            top = {parts[0] for parts in ole.listdir() if len(parts) == 1}
            if "WordDocument" in top:
                return "doc"
            if "Workbook" in top or "Book" in top:
                return "xls"
            if "PowerPoint Document" in top:
                return "ppt"
    except Exception:
        pass
    return "ole"


# --- почему файл не читается --------------------------------------------------
# Атрибуты Windows из GetFileAttributesW. Плейсхолдер облачного провайдера
# (OneDrive Files On-Demand и аналоги): размер и метаданные на диске есть,
# содержимого нет, open() падает с OSError (на замере -- errno 22).
_ATTR_REPARSE_POINT = 0x00000400
_ATTR_OFFLINE = 0x00001000
_ATTR_RECALL_ON_OPEN = 0x00040000
_ATTR_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_ATTRS = _ATTR_OFFLINE | _ATTR_RECALL_ON_OPEN | _ATTR_RECALL_ON_DATA_ACCESS
_INVALID_ATTRS = 0xFFFFFFFF


def _win_attrs(path):
    """Атрибуты файла Windows или None (не Windows, ошибка вызова).
    Отдельная функция, чтобы тесты подменяли её без настоящих плейсхолдеров."""
    try:
        import ctypes
        # Свой экземпляр DLL: restype у общего windll.kernel32 не трогаем.
        # Вне Windows ctypes.WinDLL нет -- AttributeError, отдаём None.
        fn = ctypes.WinDLL("kernel32").GetFileAttributesW
        fn.restype = ctypes.c_uint32
        fn.argtypes = [ctypes.c_wchar_p]
        attrs = fn(os.fspath(path))
    except Exception:
        return None
    return None if attrs == _INVALID_ATTRS else attrs


def unreadable_reason(path):
    """Почему файл не открывается: (причина, errno или None). Не бросает.

    Причины: cloud (плейсхолдер облака), dir, denied (errno 13: нет прав ИЛИ
    файл занят другим процессом -- CRT Windows не различает, winerror у
    исключения open() пустой), missing (errno 2), reparse (ссылка/точка
    повторной обработки без признаков облака), other.
    Облако определяем по АТРИБУТАМ, не по errno: тот же errno 22 даёт и
    недопустимое имя файла.
    """
    try:
        try:
            _read_head(path)
            return "other", None   # уже читается (скачалось между попытками)
        except OSError as exc:
            code = exc.errno
        if os.path.isdir(path):
            return "dir", code
        attrs = _win_attrs(path) or 0
        if attrs & _CLOUD_ATTRS:
            return "cloud", code
        if code == errno.EACCES:
            return "denied", code
        if code == errno.ENOENT:
            return "missing", code
        if attrs & _ATTR_REPARSE_POINT:
            return "reparse", code
        return "other", code
    except Exception:
        return "other", None


def pretty(fmt: str) -> str:
    return NAMES.get(fmt, fmt)


def family(fmt: str) -> str:
    return FAMILY.get(fmt, "unknown")
