# -*- coding: utf-8 -*-
"""Пакетная чистка: ZIP с документами -> ZIP с почищенными (фаза 5).

    clean_batch(src_zip, dst_zip, profile=Profile.STEALTH, keep=None,
                force_signed=False, *, neutral_names=False, quarantine=False)
        -> BatchResult

Свою чистку не пишем: каждый файл идёт через core.clean.clean_file, а он уже
умеет маршрутизацию, проверку оракулом (повторный inspect_file) и честные отказы.
Здесь только упаковка, защита от враждебного архива и отчёт.

РЕШЕНИЕ 1: не очищенное в выходной архив НЕ кладётся. Инвариант: всё, что лежит в
выходном ZIP, кроме _REPORT.txt, почищено И доказано оракулом, поэтому архив можно
переслать целиком, не разбирая. Остальное в архиве -- это сам риск: «папка
NOT_CLEANED» легко уезжает дальше вместе с архивом, и пользователь отдаёт
контрагенту оригинал с метаданными, будучи уверенным, что отдаёт чистое. Оригиналы
у пользователя и так есть. Кто всё же хочет всё в одном архиве -- quarantine=True:
тогда оригиналы лежат в NOT_CLEANED/ и ещё имеют префикс NOT_CLEANED__ в самом имени
(вынутый из папки файл всё равно подписан).
Файл, выданный диспетчером, но не доказанный (остались исходные значения или
инспектор не разобрал файл целиком), считается НЕ очищенным: статус "unproven".

РЕШЕНИЕ 2: имена файлов -- тоже метаданные («Увольнение_Петрова_ООО_Вектор.docx»).
neutral_names=True переименовывает всё в doc_0001.<расширение по содержимому>, каталоги
выкидываются. В этом режиме _REPORT.txt внутри архива тоже ссылается только на
номера: иначе соответствие «номер -> исходное имя» поехало бы вместе с архивом и
нейтрализация потеряла бы смысл. Таблица имён остаётся только у вызывающего:
BatchResult.items[i].name и render_report(res, with_names=True) -- для чата.
Время записей ZIP, комментарий и extra-поля выходного архива константны, а не взяты
из источника: mtime файла тоже след.

ЗАЩИТА: размер входа, число файлов, суммарный несжатый объём, коэффициент сжатия
(пороги -- как в core/zipfix.py), имена с «..», абсолютные, с диском, ADS и
управляющими символами отклоняются (на диск по ним мы не пишем никогда -- временные
файлы получают сгенерированные имена, но выходной архив с таким именем у
получателя распаковался бы мимо каталога). Вложенные архивы не разворачиваем:
помечаем «внутри не чищено». Шифрованные записи отклоняются.

Вход не модифицируется никогда (sha256 до и после). Временные каталоги --
tempfile, удаляются в finally. Исключений наружу нет. Имён и содержимого в логи не
пишем (логов нет).

CLI:  python -m core.clean_batch                      -- самопроверка
      python -m core.clean_batch in.zip out.zip [--profile ...] [--neutral-names]
                                 [--quarantine]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import re
import shutil
import sys
import tempfile
import zipfile

from . import sniff
from .clean import _CLEANERS, MAX_FILE, clean_file
from .cleanmodel import Profile
from .model import _ABS_PATH, PATH_STUB, clip
from .zipfix import (MAX_ENTRIES, MAX_INPUT, MAX_RATIO, MAX_REPLACE, MIN_BOMB_UNPACKED,
                     MAX_TOTAL_UNPACKED)

# Свои пределы: MAX_ENTRIES из zipfix (50 000) рассчитан на разбор контейнера, а
# здесь на КАЖДУЮ запись идёт полная чистка с двойной инспекцией (а на regenerate --
# запуск LibreOffice), поэтому потолок на порядки ниже.
MAX_FILES = min(1000, MAX_ENTRIES)
MAX_MEMBER = min(MAX_REPLACE, MAX_FILE)   # одна запись после распаковки
REPORT_NAME = "_REPORT.txt"
QUARANTINE_DIR = "NOT_CLEANED"
QUARANTINE_PREFIX = "NOT_CLEANED__"
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)        # константа: настоящее время записи -- след
_CHUNK = 1 << 20
_BAD_NAME = re.compile(r'[\x00-\x1f<>:"|?*]')   # ':' -- диск и альтернативные потоки NTFS
_CTRL = re.compile(r"[\x00-\x1f\x7f]")

CLEANED, UNPROVEN, REFUSED, UNSUPPORTED, NESTED, REJECTED = (
    "cleaned", "unproven", "refused", "unsupported", "nested", "rejected")
STATUS_TITLE = {
    CLEANED: "ОЧИЩЕН", UNPROVEN: "НЕ ОЧИЩЕН (чистота не доказана)",
    REFUSED: "НЕ ОЧИЩЕН (отказ)", UNSUPPORTED: "НЕ ОЧИЩЕН (формат не поддержан)",
    NESTED: "НЕ ОЧИЩЕН (вложенный архив)", REJECTED: "НЕ ОЧИЩЕН (отклонён защитой)",
}
_DETECT = {"none": "инспектор их не видит", "noticeable": "заметны при внимательном взгляде",
           "obvious": "очевидны: файл опознаётся как обработанный (предел формата, "
                      "см. профиль regenerate)"}
_EXT = {"jpeg": "jpg", "ole": "bin", "text": "txt", "unknown": "bin", "unreadable": "bin"}

# Магические числа архивов, которые sniff не знает (zip он знает сам).
_ARCHIVE_MAGIC = ((b"Rar!\x1a\x07", "RAR"), (b"7z\xbc\xaf\x27\x1c", "7z"),
                  (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"))


# ============================================================================
#  Результат
# ============================================================================


@dataclasses.dataclass
class BatchItem:
    """Один файл из присланного архива."""

    idx: int
    name: str                  # имя в исходном архиве (очищено от управляющих символов)
    fmt: str = ""              # ключ sniff; "" -- до определения формата отклонён
    status: str = REJECTED
    ok: bool = False           # диспетчер выдал файл
    clean: bool = False        # и доказал чистоту оракулом
    survived_n: int = 0        # сколько исходных значений нашлось в результате
    detect: str = ""           # оценка заметности чистки (core/lint.py)
    reason: str = ""           # коротко: почему не очищен
    in_archive: bool = False   # лежит ли в выходном архиве (кроме отчёта)
    out_name: str = ""         # имя в выходном архиве

    def __post_init__(self) -> None:
        self.name = clip(_CTRL.sub("?", str(self.name)), 200)


@dataclasses.dataclass
class BatchResult:
    src: str
    dst: str
    profile: str
    ok: bool = False           # выходной архив записан (даже если очищено 0 файлов)
    neutral_names: bool = False
    quarantine: bool = False
    items: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)
    totals: dict = dataclasses.field(default_factory=dict)

    def err(self, message) -> None:
        self.errors.append(clip(_ABS_PATH.sub(PATH_STUB, str(message)), 600))

    @property
    def all_cleaned(self) -> bool:
        """Весь присланный пакет очищен и доказан."""
        return bool(self.ok and self.totals.get("total") and
                    self.totals.get("cleaned") == self.totals.get("total"))

    def count(self, status) -> int:
        return sum(1 for i in self.items if i.status == status)


def _totals(items) -> dict:
    t = {"total": len(items)}
    for st in (CLEANED, REFUSED, UNSUPPORTED, UNPROVEN, NESTED, REJECTED):
        t[st] = sum(1 for i in items if i.status == st)
    t["not_cleaned"] = t["total"] - t[CLEANED]
    return t


# ============================================================================
#  Помощники
# ============================================================================


def _sha(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_file(a, b) -> bool:
    try:
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            return True
        return os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def _safe_name(raw):
    """Нормализованный относительный путь внутри архива или None, если имя опасно."""
    if not raw or _BAD_NAME.search(raw):
        return None
    name = raw.replace("\\", "/")
    if name.startswith("/") or len(name) > 240:
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p != p.rstrip(" .") for p in parts):
        return None                      # «..» и хвостовые точки/пробелы (Windows их съедает)
    return "/".join(parts)


def _ext(fmt) -> str:
    return _EXT.get(fmt, fmt)


def _archive_kind(path, fmt):
    """Название вложенного архива или None. OOXML/ODF -- не архивы, это документы."""
    if fmt == "zip":
        return "ZIP"
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    for magic, kind in _ARCHIVE_MAGIC:
        if head.startswith(magic):
            return kind
    return "TAR" if head[257:262] == b"ustar" else None


def _extract(zf, info, path) -> int:
    """Распаковать запись, считая реальные байты, а не заявленные в заголовке."""
    n = 0
    with zf.open(info) as r, open(path, "wb") as w:
        for chunk in iter(lambda: r.read(_CHUNK), b""):
            n += len(chunk)
            if n > MAX_MEMBER:
                raise OverflowError
            w.write(chunk)
    return n


class _Out:
    """Выходной ZIP: детерминированные заголовки, чтобы не нести метаданные источника."""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
        self.used = set()

    def unique(self, name) -> str:
        base, ext = os.path.splitext(name)
        cand, n = name, 1
        while cand.lower() in self.used:
            n += 1
            cand = "%s (%d)%s" % (base, n, ext)
        self.used.add(cand.lower())
        return cand

    def _info(self, name):
        zi = zipfile.ZipInfo(name, _ZIP_TIME)
        zi.compress_type = zipfile.ZIP_DEFLATED
        zi.create_system = 3
        zi.external_attr = 0o644 << 16
        return zi

    def add_file(self, name, path) -> None:
        with open(path, "rb") as r, self.zf.open(self._info(name), "w") as w:
            shutil.copyfileobj(r, w, _CHUNK)

    def add_text(self, name, text) -> None:
        self.zf.writestr(self._info(name), text.encode("utf-8"))


# ============================================================================
#  Отчёт
# ============================================================================


def _label(it, with_names) -> str:
    return it.name if with_names else "№%04d" % it.idx


def render_report(res, with_names=True) -> str:
    """Отчёт по каждому файлу. with_names=False -- без исходных имён (для архива
    с neutral_names). Сначала не очищенное: это то, что пользователь обязан увидеть."""
    t = res.totals
    out = ["ОТЧЁТ О ПАКЕТНОЙ ЧИСТКЕ", "",
           "Профиль: %s. Нейтральные имена: %s." % (
               res.profile, "да" if res.neutral_names else "нет"),
           "Всего файлов: %d. Очищено и проверено: %d. НЕ очищено: %d." % (
               t.get("total", 0), t.get("cleaned", 0), t.get("not_cleaned", 0))]
    if t.get("not_cleaned"):
        out.append("  из не очищенных: отказов %d, формат не поддержан %d, вложенных архивов %d, "
                   "чистота не доказана %d, отклонено защитой %d." % (
                       t[REFUSED], t[UNSUPPORTED], t[NESTED], t[UNPROVEN], t[REJECTED]))
        out.append(
            "ВНИМАНИЕ: не очищенные файлы %s. Они в исходном виде, со всеми метаданными." %
            ("лежат в папке %s/, не отдавайте её дальше" % QUARANTINE_DIR if res.quarantine
             else "в этот архив НЕ включены"))
    out.append("")
    for it in sorted(res.items, key=lambda i: (i.status == CLEANED, i.idx)):
        line = "[%s] %s" % (STATUS_TITLE.get(it.status, it.status), _label(it, with_names))
        if it.fmt:
            line += "  (%s)" % sniff.pretty(it.fmt)
        out.append(line)
        if it.in_archive:
            out.append("    в архиве как: " + it.out_name)
        if it.status == CLEANED:
            out.append("    следы чистки: %s" % _DETECT.get(it.detect, "не оценены"))
        elif it.reason:
            out.append("    причина: " + it.reason)
    return "\n".join(out) + "\n"


# ============================================================================
#  Ядро
# ============================================================================


def _reason(res_file) -> str:
    msg = res_file.errors[0] if res_file.errors else "причина не указана"
    msg = msg.replace("Файл НЕ очищен: ", "").replace("Файл не очищен: ", "")
    return clip(msg.replace(" Выходной файл не создан.", ""), 300)


def _unsupported(res_file) -> str:
    why = "для формата «%s» чистки нет" % sniff.pretty(res_file.fmt)
    if res_file.planned_phase:
        why += " (в плане: фаза %d)" % res_file.planned_phase
    return why


def _member(st, zf, info, it) -> None:
    """Обработать одну запись. Заполняет it; кладёт в выходной архив при необходимости."""
    safe = _safe_name(info.filename)
    if safe is None:
        it.reason = "небезопасное имя записи (выход за каталог, диск, спецсимволы)"
        return
    if info.flag_bits & 0x1:
        it.reason = "запись зашифрована, прочитать нельзя"
        return
    if info.file_size > MAX_MEMBER:
        it.reason = "запись больше %d МБ" % (MAX_MEMBER >> 20)
        return
    if (info.file_size > MIN_BOMB_UNPACKED and
            info.file_size / max(info.compress_size, 1) > MAX_RATIO):
        it.reason = "подозрение на архивную бомбу (коэффициент сжатия выше %d)" % MAX_RATIO
        return

    src = os.path.join(st.work, "in_%04d.bin" % it.idx)
    try:
        _extract(zf, info, src)
    except OverflowError:
        it.reason = "запись при распаковке выросла больше %d МБ" % (MAX_MEMBER >> 20)
        return
    except Exception:  # noqa: BLE001 -- битая запись, неподдержанное сжатие, CRC
        it.reason = "запись повреждена или не распаковывается"
        return

    fmt = sniff.sniff(src)
    it.fmt = fmt
    ext = _ext(fmt)
    # Расширение нужно LibreOffice (regenerate берёт формат из него); остальные
    # чистильщики смотрят на содержимое.
    typed = os.path.join(st.work, "in_%04d.%s" % (it.idx, ext))
    os.replace(src, typed)
    src = typed

    kind = _archive_kind(src, fmt)
    if kind:
        it.status = NESTED
        it.reason = ("вложенный архив (%s): содержимое НЕ чищено, архивы не разворачиваются" %
                     kind)
    else:
        dst = os.path.join(st.work, "out_%04d.%s" % (it.idx, ext))
        r = clean_file(src, dst, st.profile, st.keep, st.force_signed)
        it.ok, it.clean = bool(r.ok), bool(r.clean)
        it.survived_n, it.detect = len(r.survived), r.detect
        if r.ok and r.clean:
            it.status = CLEANED
            src = dst                            # в архив идёт результат
            if (st.profile is Profile.REGENERATE and r.fmt != fmt):
                ext = _ext(r.fmt)               # doc -> docx: расширение должно не врать
        elif r.ok:
            it.status = UNPROVEN
            it.reason = ("выдан, но чистота не доказана: исходных значений в результате %d, "
                         "замечаний инспектора %d" % (len(r.survived), len(r.gaps)))
        elif r.planned_phase or sniff.family(r.fmt) not in _CLEANERS \
                or (st.profile is Profile.REGENERATE and r.planned_phase):
            it.status = UNSUPPORTED
            it.reason = _unsupported(r)
        else:
            it.status = REFUSED
            it.reason = _reason(r)

    # Куда класть. Исходник не очищенного -- только в карантин и только по запросу.
    if it.status == CLEANED:
        if st.neutral:
            name = "doc_%04d.%s" % (it.idx, ext)
        else:
            name = safe
            if st.profile is Profile.REGENERATE and ext != _ext(fmt):
                name = os.path.splitext(safe)[0] + "." + ext
        it.out_name = st.out.unique(name)
    elif st.quarantine and it.status != REJECTED:
        if st.neutral:
            name = "%s/%snc_%04d.%s" % (QUARANTINE_DIR, QUARANTINE_PREFIX, it.idx, ext)
        else:
            head, base = os.path.split(safe)
            name = "/".join(x for x in (QUARANTINE_DIR, head, QUARANTINE_PREFIX + base) if x)
        it.out_name = st.out.unique(name)
        src = os.path.join(st.work, "in_%04d.%s" % (it.idx, _ext(fmt)))   # оригинал
    if it.out_name:
        st.out.add_file(it.out_name, src)
        it.in_archive = True


class _State:
    pass


def _run(res, st, dst, profile) -> None:
    try:
        st.profile = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err("Неизвестный профиль чистки.")
        return
    res.profile = st.profile.value
    src = res.src

    if _same_file(src, dst):
        res.err("Выходной путь совпадает с входным: входной архив не перезаписывается никогда.")
        return
    if os.path.isdir(dst):
        res.err("Выходной путь -- каталог, нужен путь к файлу.")
        return
    if not os.path.isfile(src):
        res.err("Входной архив не найден или это не файл.")
        return
    size = os.path.getsize(src)
    if size == 0:
        res.err("Входной файл пуст (0 байт), возможно, не докачался.")
        return
    if size > MAX_INPUT:
        res.err("Архив больше %d МБ: чистка не выполнена." % (MAX_INPUT >> 20))
        return

    sha_before = _sha(src)
    try:
        zf = zipfile.ZipFile(src)
        infos = [i for i in zf.infolist() if not i.filename.endswith(("/", "\\"))]
    except Exception:  # noqa: BLE001 -- не ZIP, обрезан, мусор в каталоге
        res.err("Присланный файл не ZIP-архив или повреждён: чистить нечего.")
        return

    with zf:
        if not infos:
            res.err("Архив пуст: в нём нет ни одного файла.")
            return
        if len(infos) > MAX_FILES:
            res.err("В архиве %d файлов, больше предела %d: чистка не выполнена. "
                    "Разбейте на части." % (len(infos), MAX_FILES))
            return
        declared = sum(i.file_size for i in infos)
        if declared > MAX_TOTAL_UNPACKED or (
                declared > MIN_BOMB_UNPACKED and declared / size > MAX_RATIO):
            res.err("Суммарный несжатый объём %d МБ при размере архива %d КБ: похоже на "
                    "архивную бомбу или превышен предел %d МБ. Чистка не выполнена." %
                    (declared >> 20, size >> 10, MAX_TOTAL_UNPACKED >> 20))
            return

        parent = os.path.dirname(os.path.abspath(dst))
        os.makedirs(parent, exist_ok=True)
        fd, st.tmp_out = tempfile.mkstemp(prefix=".batch_", suffix=".zip", dir=parent)
        os.close(fd)
        st.work = tempfile.mkdtemp(prefix="cleanbatch_")
        st.out = _Out(st.tmp_out)
        st.out.used.add(REPORT_NAME.lower())

        for n, info in enumerate(infos, 1):
            it = BatchItem(idx=n, name=info.filename)
            res.items.append(it)
            try:
                _member(st, zf, info, it)
            except Exception as exc:  # noqa: BLE001 -- сбой на файле не срывает пакет
                if it.status == CLEANED and it.in_archive:
                    pass
                else:
                    it.status, it.ok, it.clean, it.in_archive = REFUSED, False, False, False
                    it.reason = "внутренняя ошибка при обработке: %s" % type(exc).__name__
            finally:
                for f in os.listdir(st.work):          # место на диске: по одному файлу
                    try:
                        os.remove(os.path.join(st.work, f))
                    except OSError:
                        pass

    res.totals = _totals(res.items)
    st.out.add_text(REPORT_NAME, render_report(res, with_names=not st.neutral))
    st.out.zf.close()
    if _sha(src) != sha_before:
        res.err("Входной архив изменился во время работы: результат ненадёжен, архив не выдан.")
        return
    try:
        os.replace(st.tmp_out, dst)
    except OSError as exc:
        res.err("Не удалось записать выходной архив (errno %s): он занят или нет прав." %
                exc.errno)
        return
    st.tmp_out = None
    res.ok = True


def clean_batch(src_zip, dst_zip, profile=Profile.STEALTH, keep=None, force_signed=False,
                *, neutral_names=False, quarantine=False) -> BatchResult:
    """Очистить каждый файл архива src_zip, собрать dst_zip. НЕ бросает исключений.

    ok=True -- выходной архив записан (внутри всегда _REPORT.txt; очищено может быть и 0
    файлов -- смотри totals). ok=False -- архива нет, причина в errors; существующий
    dst не тронут. Входной архив не изменяется никогда."""
    try:
        s, d = os.fsdecode(src_zip), os.fsdecode(dst_zip)
    except Exception:  # noqa: BLE001
        s, d = str(src_zip), str(dst_zip)
    res = BatchResult(src=s, dst=d, profile=str(getattr(profile, "value", profile)),
                      neutral_names=bool(neutral_names), quarantine=bool(quarantine))
    st = _State()
    st.work = st.tmp_out = st.out = None
    st.keep, st.force_signed = keep, force_signed
    st.neutral, st.quarantine = bool(neutral_names), bool(quarantine)
    try:
        _run(res, st, d, profile)
    except Exception as exc:  # noqa: BLE001 -- последний рубеж
        res.ok = False
        res.err("Внутренняя ошибка пакетной чистки: %s: %s" % (type(exc).__name__, exc))
    finally:
        try:
            if st.out is not None:
                st.out.zf.close()
        except Exception:  # noqa: BLE001
            pass
        if st.work:
            shutil.rmtree(st.work, ignore_errors=True)
        if st.tmp_out:
            try:
                os.remove(st.tmp_out)
            except OSError:
                pass
    res.totals = _totals(res.items)
    if not res.ok:
        for it in res.items:
            it.in_archive = False
    return res


# ============================================================================
#  Самопроверка: PYTHONIOENCODING=utf-8 python -m core.clean_batch
# ============================================================================

_FAILS = []
_TOTAL = [0]


def _check(name, cond, detail=""):
    _TOTAL[0] += 1
    if not cond:
        _FAILS.append(name)
    print("  [%s] %s%s" % ("ok" if cond else "ПРОВАЛ", name,
                           "  -- " + str(detail) if (detail and not cond) else ""))


def _names(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def _selftest() -> int:
    import time
    from tests import fixtures as fx
    from .inspect import inspect_file

    root = tempfile.mkdtemp(prefix="batchtest_")
    tmpdir = os.path.join(root, "tmp")           # сюда смотрит tempfile: ловим утечки временного
    os.makedirs(tmpdir)
    old_tmp, tempfile.tempdir = tempfile.tempdir, tmpdir
    t0 = time.time()
    try:
        fxd = os.path.join(root, "fx")
        os.makedirs(fxd)
        made = {}
        for fn in (fx.make_docx, fx.make_xlsx, fx.make_pptx, fx.make_pdf, fx.make_jpeg,
                   fx.make_png, fx.make_rtf, fx.make_odt, fx.make_doc, fx.make_xls):
            path, exp = fn(fxd)
            made[os.path.basename(path)] = (path, exp)
        nested = os.path.join(fxd, "inner.zip")
        with zipfile.ZipFile(nested, "w") as z:
            z.writestr("secret.docx", b"x")
        notes = os.path.join(fxd, "notes.txt")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("просто текст")

        def mkzip(path, entries):
            import warnings
            with warnings.catch_warnings(), zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                warnings.simplefilter("ignore")      # намеренные дубликаты имён
                for arc, data in entries:
                    z.writestr(arc, data)
            return path

        def rd(p):
            with open(p, "rb") as fh:
                return fh.read()

        entries = [("Отдел кадров/" + n, rd(p)) for n, (p, _) in made.items()]
        entries += [("inner.zip", rd(nested)), ("notes.txt", rd(notes))]
        mixed = mkzip(os.path.join(root, "mixed.zip"), entries)
        sha_in = _sha(mixed)

        print("\n== смешанный пакет, профили stealth и paranoid ==")
        for prof in ("stealth", "paranoid"):
            dst = os.path.join(root, "out_%s.zip" % prof)
            r = clean_batch(mixed, dst, prof)
            print("\n--- профиль %s: ok=%s totals=%s" % (prof, r.ok, r.totals))
            print(render_report(r, with_names=True))
            by = {i.name.split("/")[-1]: i for i in r.items}
            _check("%s: ok и вход цел" % prof, r.ok and _sha(mixed) == sha_in, r.errors)
            _check("%s: всего 12 файлов" % prof, r.totals["total"] == 12, r.totals)
            for n in ("leaky.docx", "probe.pdf", "probe.jpg", "probe.png", "probe.rtf",
                      "probe.odt", "book.xlsx", "deck.pptx"):
                if n in by:
                    _check("%s: %s -> %s" % (prof, n, by[n].status), by[n].status == CLEANED,
                           by[n].reason)
            _check("%s: .doc и .xls честно не поддержаны" % prof,
                   by["leaky.doc"].status == UNSUPPORTED and by["book.xls"].status == UNSUPPORTED)
            _check("%s: inner.zip -- вложенный, notes.txt -- не поддержан" % prof,
                   by["inner.zip"].status == NESTED and by["notes.txt"].status == UNSUPPORTED)
            names = _names(dst)
            _check("%s: в архиве нет не очищенных и нет NOT_CLEANED" % prof,
                   not any(x.endswith((".doc", ".xls", "inner.zip", "notes.txt")) or
                           QUARANTINE_DIR in x for x in names), names)
            _check("%s: отчёт внутри архива" % prof, REPORT_NAME in names)
            _check("%s: время записей константно" % prof, all(
                z.date_time == _ZIP_TIME for z in zipfile.ZipFile(dst).infolist()))
            # Независимый оракул: заложенные значения не найдены повторной инспекцией.
            chk = os.path.join(root, "chk")
            os.makedirs(chk, exist_ok=True)
            leaked = []
            with zipfile.ZipFile(dst) as z:
                for it in r.items:
                    if it.status != CLEANED:
                        continue
                    p = os.path.join(chk, os.path.basename(it.out_name))
                    with open(p, "wb") as fh:
                        fh.write(z.read(it.out_name))
                    rep = inspect_file(p)
                    vals = " ".join(str(f.value) for f in rep.findings).lower()
                    exp = made[os.path.basename(it.name)][1]
                    leaked += [v for v in exp.get("identity", []) if str(v).lower() in vals]
            _check("%s: заложенные identity-значения не найдены в результатах" % prof,
                   not leaked, leaked)

        print("\n== neutral_names + quarantine ==")
        dst = os.path.join(root, "out_neutral.zip")
        r = clean_batch(mixed, dst, neutral_names=True, quarantine=True)
        names = _names(dst)
        print("  записи архива:", names)
        rep = zipfile.ZipFile(dst).read(REPORT_NAME).decode("utf-8")
        orig_parts = {"leaky", "probe", "book", "deck", "inner", "notes", "Отдел", "кадров"}
        _check("neutral: ни в именах, ни в отчёте архива нет исходных имён",
               not any(p in " ".join(names) + rep for p in orig_parts))
        _check("neutral: оригиналы не очищенного в NOT_CLEANED/ с префиксом", all(
            x.startswith(QUARANTINE_DIR + "/" + QUARANTINE_PREFIX) for x in names
            if "nc_" in x) and sum("nc_" in x for x in names) == 4, names)
        _check("neutral: отчёт для чата содержит исходные имена",
               "leaky.doc" in render_report(r, with_names=True))
        _check("quarantine: оригинал .doc побайтно равен исходному", any(
            zipfile.ZipFile(dst).read(x) == rd(made["leaky.doc"][0]) for x in names
            if "nc_" in x))

        print("\n== regenerate (если есть LibreOffice) ==")
        from .clean_regen import find_soffice
        if find_soffice():
            sub = mkzip(os.path.join(root, "regen_in.zip"),
                        [("a.docx", rd(made["leaky.docx"][0])), ("b.doc", rd(made["leaky.doc"][0]))])
            r = clean_batch(sub, os.path.join(root, "regen_out.zip"), Profile.REGENERATE)
            print(render_report(r))
            _check("regenerate: без исключений, отчёт есть", r.ok and len(r.items) == 2, r.errors)
        else:
            print("  LibreOffice не найден, пропущено")

        print("\n== патологии ==")
        p = os.path.join(root, "p")
        os.makedirs(p)
        docx = rd(made["leaky.docx"][0])
        cases = {}

        cases["пустой архив"] = mkzip(os.path.join(p, "empty.zip"), [])
        with open(os.path.join(p, "notzip.zip"), "wb") as fh:
            fh.write(b"this is not a zip" * 100)
        cases["не ZIP"] = os.path.join(p, "notzip.zip")
        with open(os.path.join(p, "cut.zip"), "wb") as fh:
            fh.write(rd(mixed)[:5000])
        cases["обрезанный ZIP"] = os.path.join(p, "cut.zip")
        with open(os.path.join(p, "zero.zip"), "wb"):
            pass
        cases["0 байт"] = os.path.join(p, "zero.zip")
        cases["zip-бомба 100 МБ"] = mkzip(os.path.join(p, "bomb.zip"),
                                          [("bomb.bin", b"\x00" * (100 << 20))])
        cases["не существует"] = os.path.join(p, "nope.zip")
        cases["только каталоги"] = mkzip(os.path.join(p, "dirs.zip"), [("a/", b""), ("b/c/", b"")])
        for nm, path in cases.items():
            before = _sha(path) if os.path.isfile(path) else None
            dst = os.path.join(p, "o_%d.zip" % len(nm))
            try:
                r = clean_batch(path, dst)
                ok_exc = True
            except Exception as exc:  # noqa: BLE001
                ok_exc, r = False, None
            _check("%s: без исключения, отказ, dst не создан, вход цел" % nm,
                   ok_exc and not r.ok and r.errors and not os.path.exists(dst) and
                   (before is None or _sha(path) == before), r and r.errors)
            if r:
                print("      ->", r.errors[0])

        # path traversal: внутри один нормальный docx
        trav = mkzip(os.path.join(p, "trav.zip"), [
            ("../evil.docx", docx), ("/abs.docx", docx), ("C:\\win.docx", docx),
            ("a/../../x.docx", docx), ("ads.docx:stream", docx), ("ok.docx", docx)])
        dst = os.path.join(p, "trav_out.zip")
        r = clean_batch(trav, dst)
        print(render_report(r))
        _check("traversal: 5 опасных отклонены, 1 нормальный очищен",
               r.ok and r.count(REJECTED) == 5 and r.count(CLEANED) == 1, r.totals)
        _check("traversal: в выходном архиве только ok.docx и отчёт",
               sorted(_names(dst)) == sorted(["ok.docx", REPORT_NAME]), _names(dst))
        _check("traversal: за пределы каталога ничего не записано",
               not os.path.exists(os.path.join(root, "evil.docx")) and
               not os.path.exists(os.path.join(os.path.dirname(root), "evil.docx")))

        # вложенный архив рядом с нормальным
        r = clean_batch(mkzip(os.path.join(p, "nest.zip"),
                              [("inner.zip", rd(nested)), ("ok.docx", docx), ("a.7z", b"7z\xbc\xaf\x27\x1c" + b"\0" * 50)]),
                        os.path.join(p, "nest_out.zip"))
        _check("вложенные: оба помечены, не разворачивались, docx очищен",
               r.ok and r.count(NESTED) == 2 and r.count(CLEANED) == 1, r.totals)
        print("      ->", [i.reason for i in r.items if i.status == NESTED][0])

        # одноимённые записи
        r = clean_batch(mkzip(os.path.join(p, "dup.zip"), [("a.docx", docx), ("A.DOCX", docx), ("a.docx", docx)]),
                        os.path.join(p, "dup_out.zip"))
        n = _names(os.path.join(p, "dup_out.zip"))
        _check("дубликаты имён не затирают друг друга", r.count(CLEANED) == 3 and len(n) == 4, n)

        # 200 файлов
        pdf, png = rd(made["probe.pdf"][0]), rd(made["probe.png"][0])
        many = [("d%03d/f%03d.%s" % (i // 20, i, "docx" if i % 3 == 0 else "pdf" if i % 3 == 1 else "png"),
                 docx if i % 3 == 0 else pdf if i % 3 == 1 else png) for i in range(200)]
        mp = mkzip(os.path.join(p, "many.zip"), many)
        s_many = _sha(mp)
        t1 = time.time()
        r = clean_batch(mp, os.path.join(p, "many_out.zip"))
        print("  200 файлов: %.1f с, totals=%s" % (time.time() - t1, r.totals))
        _check("200 файлов: все очищены, вход цел, в архиве 201 запись",
               r.ok and r.count(CLEANED) == 200 and _sha(mp) == s_many and
               len(_names(os.path.join(p, "many_out.zip"))) == 201, r.totals)

        # пределы через подмену констант
        global MAX_FILES, MAX_MEMBER, clean_file
        keep_f, keep_m, keep_c = MAX_FILES, MAX_MEMBER, clean_file
        try:
            MAX_FILES = 3
            r = clean_batch(mp, os.path.join(p, "lim_out.zip"))
            _check("лимит числа файлов: отказ всего пакета", not r.ok and "файлов" in r.errors[0], r.errors)
            MAX_FILES, MAX_MEMBER = keep_f, 1000
            r = clean_batch(mixed, os.path.join(p, "lim2_out.zip"))
            _check("лимит размера записи: крупные отклонены, мелкие обработаны",
                   r.ok and r.count(REJECTED) >= 5, r.totals)
            MAX_MEMBER = keep_m

            # сбой диспетчера на одном файле не срывает пакет
            calls = [0]

            def flaky(*a, **k):
                calls[0] += 1
                if calls[0] == 2:
                    raise RuntimeError("boom")
                return keep_c(*a, **k)
            clean_file = flaky
            r = clean_batch(mkzip(os.path.join(p, "flaky.zip"), [("1.docx", docx), ("2.docx", docx), ("3.docx", docx)]),
                            os.path.join(p, "flaky_out.zip"))
            _check("исключение на одном файле: остальные очищены, он помечен",
                   r.ok and r.count(CLEANED) == 2 and r.count(REFUSED) == 1, r.totals)
        finally:
            MAX_FILES, MAX_MEMBER, clean_file = keep_f, keep_m, keep_c

        # ничего не очищено: архив всё равно выдаётся (с отчётом), пакет не «успешен»
        r = clean_batch(mkzip(os.path.join(p, "onlydoc.zip"), [("x.doc", rd(made["leaky.doc"][0]))]),
                        os.path.join(p, "onlydoc_out.zip"))
        _check("ни одного очищенного: ok, в архиве только отчёт, all_cleaned=False",
               r.ok and not r.all_cleaned and _names(os.path.join(p, "onlydoc_out.zip")) == [REPORT_NAME])

        # dst == src
        cp = os.path.join(p, "same.zip")
        shutil.copy(trav, cp)
        s0 = _sha(cp)
        r = clean_batch(cp, cp)
        _check("dst == src отклонён, вход цел", not r.ok and _sha(cp) == s0, r.errors)

        left = [x for x in os.listdir(tmpdir)]
        junk = [x for x in os.listdir(p) + os.listdir(root) if x.startswith(".batch_")]
        _check("временные каталоги и .batch_* удалены", not left and not junk, (left, junk))
    finally:
        tempfile.tempdir = old_tmp
        shutil.rmtree(root, ignore_errors=True)

    print("\nИТОГО: %d проверок, %d провалено (%.1f с)" % (_TOTAL[0], len(_FAILS), time.time() - t0))
    for f in _FAILS:
        print("  ПРОВАЛ:", f)
    return 1 if _FAILS else 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog="python -m core.clean_batch",
                                 description="Пакетная чистка ZIP. Без аргументов -- самопроверка.")
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--profile", choices=[p.value for p in Profile], default="stealth")
    ap.add_argument("--neutral-names", action="store_true")
    ap.add_argument("--quarantine", action="store_true")
    a = ap.parse_args(argv)
    if not a.src:
        return _selftest()
    if not a.dst:
        ap.error("нужен выходной архив")
    r = clean_batch(a.src, a.dst, a.profile, neutral_names=a.neutral_names,
                    quarantine=a.quarantine)
    for e in r.errors:
        print(e)
    if r.ok:
        print(render_report(r))
    return 0 if r.all_cleaned else 1


if __name__ == "__main__":
    raise SystemExit(main())
