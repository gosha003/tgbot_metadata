# -*- coding: utf-8 -*-
"""Байт-точная пересборка ZIP-контейнера: docx, xlsx, pptx и ODF -- один и тот же ZIP.

Фаза 2. Только stdlib (struct, zlib, zipfile -- последний лишь для проверки выхода).

    rebuild(src, dst, replace=None, drop=None, *, strip_slack=False) -> RebuildReport
    entries(src) -> list[ZipEntry]

ЗАЧЕМ. Любой инструмент чистки распаковывает пакет и пакует заново, после чего у
КАЖДОЙ записи меняется сжатый размер, порядок записей становится другим, DOS-
таймстемпы сбрасываются, и профиль контейнера перестаёт соответствовать
приложению из app.xml. Файл выглядит обработанным, даже если все поля вычищены.
Здесь меняется только то, что мы попросили заменить или удалить; всё остальное --
побайтовый срез исходника.

ГЛАВНЫЙ ПРИНЦИП: НЕ СОБИРАТЬ ФАЙЛ ЗАНОВО, А ПРАВИТЬ ЕГО. Выход = вход, в котором
перегенерированы только изменённые куски. Поэтому rebuild(src, dst) без replace
и drop даёт файл, равный входу побайтно: это не частный случай с отдельной
веткой, а тот же код, у которого просто нечего менять. sha256 совпадает.

ПОЧЕМУ НЕ zipfile.ZipFile(..., "w"). Публичного API «скопировать сжатые байты» у
него нет, а всё остальное он пишет по-своему: свои version_made/external_attr,
таймстемп берёт из ZipInfo, при потоковой записи ставит бит 3 и дескрипторы,
local extra не переносит. Office кладёт в локальные заголовки extra 0xA220
(Open Packaging growth hint, 36..520 байт) -- zipfile его потерял бы на каждой
записи. Поэтому и читаем, и пишем руками; zipfile используем только как
независимого проверяющего выхода: открывается, testzip() проходит, состав и
порядок записей те, что мы собирались получить.

УСТРОЙСТВО. Файл режется на сегменты по смещениям из центрального каталога:
    [голова][сегмент записи 1][сегмент записи 2]...[каталог][хвост]
Сегмент записи = локальный заголовок + данные + (дескриптор) + щель до следующей
записи. Нетронутая запись копируется сегментом целиком, СЫРЫМИ СЖАТЫМИ БАЙТАМИ,
без распаковки. Центральный каталог копируется сырыми записями, меняется только
поле смещения (и только если запись сдвинулась). Хвост (ZIP64 EOCD, локатор,
EOCD, комментарий) -- те же байты с исправленными счётчиком/размером/смещением.
Заменённая запись получает новый локальный заголовок: тот же version_needed,
флаги, метод, DOS-время, имя и local extra (в т.ч. growth hint Office), что были
у оригинала; уровень deflate берётся из битов 1-2 флагов (Word пишет 6 =
«самый быстрый»). Метод STORED остаётся STORED. Если у оригинала был бит 3
(потоковый писатель: Java, Google Docs), он остаётся, и после данных пишется
data descriptor того же формата -- заменённая запись не выбивается из соседних.

ЩЕЛИ. «Слепые» байты вне записей -- голова до первой записи, щели между
записями, мёртвое место между каталогом и EOCD, комментарий архива, мусор после
EOCD -- по умолчанию КОПИРУЮТСЯ (иначе правило «без изменений = байт в байт»
перестаёт быть безусловным). Но это классический тайник, Office его не пишет,
и чистильщик вправе его убрать: strip_slack=True. Сколько там было --
RebuildReport.slack_bytes и comment_len.

ОТКАЗЫ (ok=False, dst не создан, существующий dst не тронут). Часть --
структурная порча (нет EOCD, битый каталог, локальный заголовок расходится с
центральным), часть -- политика, потому что чистильщик, пропустивший такое,
соврал бы про результат:
  * дубли имён (в том числе различающиеся только регистром): парсеры расходятся
    в том, какая из двух записей «настоящая»; именно так прячут метаданные
    от инспектора;
  * path traversal в имени (../, абсолютный путь, диск, NUL);
  * зип-бомба (пороги те же, что у core/insp_ooxml.py), шифрование, методы,
    кроме STORED и DEFLATED (OPC других не допускает);
  * перекрывающиеся записи, каталог поверх данных, многотомные архивы;
  * несколько конкурирующих EOCD в конце файла (подделка в комментарии архива:
    чистильщик прочёл бы пустой архив, Word -- настоящий);
  * replace с именем, которого нет в пакете (опечатка не должна тихо дать «вычищено»).
Распаковка нетронутых записей не выполняется вовсе, так что бомба-в-данных нам
вреда не делает; пороги нужны, чтобы после нас её не распаковал чистильщик.

Только чтение входа: src открывается 'rb', выход пишется в dst + ".part" и
переименовывается только после проверки. Приватность: логов нет, в errors --
тексты без путей (проходят тот же скраб, что Report.err()).
"""

from __future__ import annotations

import dataclasses
import os
import struct
import zipfile
import zlib

from .model import _ABS_PATH, PATH_STUB, clip

# --- лимиты -----------------------------------------------------------------
MAX_INPUT = 512 * 1024 * 1024          # как у clean_pdf
MAX_ENTRIES = 50_000
MAX_CD = 64 * 1024 * 1024              # размер центрального каталога
MAX_REPLACE = 256 * 1024 * 1024        # одна заменяемая часть
# Пороги бомбы -- ТЕ ЖЕ, что в core/insp_ooxml.py (дублируем, а не импортируем:
# низкоуровневый модуль не должен зависеть от инспектора, а инспектор потом
# возьмёт entries() отсюда).
MAX_TOTAL_UNPACKED = 512 * 1024 * 1024
MAX_RATIO = 120
MIN_BOMB_UNPACKED = 64 * 1024 * 1024
MAX_ERRORS = 20                        # на враждебном входе список ошибок не раздуваем
_CHUNK = 1 << 20

_U16 = 0xFFFF
_U32 = 0xFFFFFFFF

_SIG_LOCAL = b"PK\x03\x04"
_SIG_CD = b"PK\x01\x02"
_SIG_EOCD = b"PK\x05\x06"
_SIG_Z64 = b"PK\x06\x06"
_SIG_LOC64 = b"PK\x06\x07"
_SIG_DESC = b"PK\x07\x08"

_LH = struct.Struct("<4s5H3L2H")             # локальный заголовок, 30 байт
_CD = struct.Struct("<4s6H3L5H2L")           # запись центрального каталога, 46 байт
_EOCD = struct.Struct("<4s4H2LH")            # 22 байта
_LOC64 = struct.Struct("<4sLQL")             # 20 байт
_Z64 = struct.Struct("<4sQ2H2L4Q")           # фиксированная часть ZIP64 EOCD, 56 байт

# Уровень zlib по битам 1-2 флагов deflate (APPNOTE 4.4.4): нормальный / максимальный /
# быстрый / самый быстрый. Word пишет 6.
_LEVEL = {0: 6, 2: 9, 4: 3, 6: 1}


# ============================================================================
#  Публичные типы
# ============================================================================


@dataclasses.dataclass(frozen=True)
class ZipEntry:
    """Опись одной записи контейнера. Всё -- как записано в центральном каталоге."""

    name: str
    date_time: tuple          # (год, месяц, день, час, мин, сек): DOS-время как есть
    compress_type: int        # 0 STORED, 8 DEFLATED
    compress_size: int
    file_size: int
    crc: int
    order: int                # индекс в центральном каталоге (так их видит Word)
    flag_bits: int = 0
    header_offset: int = 0


@dataclasses.dataclass
class RebuildReport:
    """Итог пересборки. ok=True означает: выход записан И открывается zipfile,
    testzip() проходит, состав и порядок записей совпали с ожидаемыми.

    Счётчики -- числа записей. На ok=False нулевые (кроме src_entries,
    slack_bytes и comment_len, если исходник удалось разобрать): выход не выдан.
    """

    copied_raw: int = 0       # скопировано сырыми сжатыми байтами
    recompressed: int = 0     # заменено (пережато)
    dropped: int = 0          # удалено
    src_entries: int = 0
    dst_entries: int = 0
    slack_bytes: int = 0      # байты вне записей в исходнике (голова, щели, хвост), без комментария
    comment_len: int = 0      # комментарий архива в исходнике
    errors: list = dataclasses.field(default_factory=list)
    ok: bool = False

    def err(self, message):
        """Как Report.err(): абсолютные пути вырезаются, потому что текст уходит в чат."""
        if len(self.errors) < MAX_ERRORS:
            self.errors.append(clip(_ABS_PATH.sub(PATH_STUB, str(message)), 600))


# ============================================================================
#  Разбор контейнера
# ============================================================================


class _Bad(Exception):
    """Структурная порча или отказ по политике. Текст по-русски и без путей."""


class _Refused(Exception):
    """Отказ, причины которого уже записаны в report.errors."""


@dataclasses.dataclass
class _Ent:
    idx: int                  # порядок в центральном каталоге
    name: str
    nameb: bytes
    rec: bytes                # сырая запись центрального каталога
    flags: int
    method: int
    mtime: int
    mdate: int
    crc: int
    csize: int                # истинные (с учётом ZIP64) размеры и смещение
    usize: int
    off: int
    off_slot: int = -1        # где в rec лежит 8-байтовое смещение ZIP64, если оно там
    lflags: int = 0
    data_start: int = 0
    data_end: int = 0
    own_end: int = 0          # конец заголовка + данных + дескриптора
    dsig: bool = False        # у дескриптора есть подпись PK\x07\x08
    dw: int = 0               # ширина размеров в дескрипторе (4 или 8); 0 -- дескриптора нет
    seg_end: int = 0          # начало следующей записи (или каталога)


@dataclasses.dataclass
class _Box:
    size: int
    ents: list                # в порядке каталога
    cd_off: int
    cd_size: int
    eocd_pos: int
    clen: int
    z64_pos: int              # -1, если ZIP64 нет
    z64_len: int
    loc_pos: int
    first_off: int            # конец головы
    slack: int


def _find_eocd(f, size):
    if size < _EOCD.size:
        raise _Bad("файл короче минимального ZIP: это не ZIP")
    n = min(size, _EOCD.size + _U16)
    f.seek(size - n)
    tail = f.read(n)
    if len(tail) != n:
        raise _Bad("файл прочитан не полностью")
    cands = []
    i = tail.find(_SIG_EOCD)
    while i >= 0:
        cands.append(i)
        i = tail.find(_SIG_EOCD, i + 1)
    if not cands:
        raise _Bad("не найден конец архива (EOCD): не ZIP или файл обрезан")

    def stop(i):                                  # где кончается EOCD вместе с комментарием
        return i + _EOCD.size + int.from_bytes(tail[i + 20:i + 22], "little")

    # Подделка EOCD в комментарии архива: разные парсеры выберут разный EOCD и
    # прочтут разный архив (чистильщик увидел бы пустой, Word -- настоящий).
    # Берём ровно тот EOCD, что кончается на конце файла, и только если он
    # последняя сигнатура, как у zipfile. Вложенный ZIP внутри записи (своя
    # сигнатура до внешнего каталога) этому не мешает: он не доходит до конца файла.
    exact = [i for i in cands if i + _EOCD.size <= len(tail) and stop(i) == len(tail)]
    if len(exact) > 1 or (exact and exact[0] != cands[-1]) or (not exact and len(cands) > 1):
        raise _Bad("в конце файла несколько сигнатур EOCD: парсеры прочтут разный архив")
    i = exact[0] if exact else cands[-1]          # без exact -- мусор после EOCD
    if i + _EOCD.size > len(tail) or stop(i) > len(tail):
        raise _Bad("EOCD обрезан: комментарий архива длиннее остатка файла")
    return size - n + i, tail[i:i + _EOCD.size]


def _z64_extra(extra, usize, csize, off):
    """Истинные размеры и смещение с учётом ZIP64-extra (id 1) записи каталога.
    Возвращает (usize, csize, off, слот смещения в extra или -1)."""
    q = 0
    while q + 4 <= len(extra):
        hid, hs = struct.unpack_from("<HH", extra, q)
        body, nxt = q + 4, q + 4 + hs
        if nxt > len(extra):
            raise _Bad("битое extra-поле записи")
        if hid == 1:
            vals = [usize, csize, off]
            slot = -1
            p = body
            for k in range(3):
                if vals[k] == _U32:
                    if p + 8 > nxt:
                        raise _Bad("битое ZIP64-поле записи")
                    vals[k] = int.from_bytes(extra[p:p + 8], "little")
                    if k == 2:
                        slot = p
                    p += 8
            return vals[0], vals[1], vals[2], slot
        q = nxt
    return usize, csize, off, -1


def _z64_values(extra):
    """Значения ZIP64-extra (id 1) как 8-байтовые числа; [], если поля нет."""
    q = 0
    while q + 4 <= len(extra):
        hid, hs = struct.unpack_from("<HH", extra, q)
        if q + 4 + hs > len(extra):
            raise _Bad("битое extra-поле записи")
        if hid == 1:
            return [int.from_bytes(extra[p:p + 8], "little") for p in range(q + 4, q + 4 + hs - hs % 8, 8)]
        q += 4 + hs
    return []


def _strip_z64(extra):
    """extra без ZIP64-поля: у заменённой записи размеры пишем честными 32-битными."""
    out = bytearray()
    q = 0
    while q + 4 <= len(extra):
        hid, hs = struct.unpack_from("<HH", extra, q)
        if q + 4 + hs > len(extra):
            raise _Bad("битое extra-поле записи")
        if hid != 1:
            out += extra[q:q + 4 + hs]
        q += 4 + hs
    out += extra[q:]
    return bytes(out)


def _descr_len(f, pos, limit, crc, csize, usize):
    """Формат data descriptor после данных (бит 3): (длина, есть_подпись, ширина).
    Вариантов четыре: с подписью PK\\x07\\x08 или без, размеры 4 или 8 байт. Признак --
    совпадение со значениями каталога; None, если ни один не подошёл."""
    f.seek(pos)
    d = f.read(max(0, min(24, limit - pos)))
    for sig in (True, False):
        # Сначала 8: у малых размеров верхние половины нулевые и узкий вариант совпал
        # бы случайно. Ошибка в эту сторону безопасна (лишние нули учтутся как часть
        # дескриптора), в обратную -- strip_slack отрезал бы кусок настоящего.
        for w in (8, 4):
            n = (4 if sig else 0) + 4 + 2 * w
            if len(d) < n or (sig and d[:4] != _SIG_DESC):
                continue
            p = 4 if sig else 0
            got = (int.from_bytes(d[p:p + 4], "little"),
                   int.from_bytes(d[p + 4:p + 4 + w], "little"),
                   int.from_bytes(d[p + 4 + w:p + 4 + 2 * w], "little"))
            if got == (crc, csize, usize):
                return n, sig, w
    return None


def _scan(f, size):
    """Структурный разбор. Бросает _Bad. Ничего не пишет, ничего не распаковывает."""
    if size > MAX_INPUT:
        raise _Bad("файл больше %d МБ" % (MAX_INPUT >> 20))
    eocd_pos, eocd = _find_eocd(f, size)
    _, disk, cd_disk, n_disk, n_tot, cd_size, cd_off, clen = _EOCD.unpack(eocd)

    z64_pos = loc_pos = -1
    z64_len = 0
    if eocd_pos >= _LOC64.size:
        f.seek(eocd_pos - _LOC64.size)
        loc = f.read(_LOC64.size)
        if loc[:4] == _SIG_LOC64:
            _, ldisk, z_off, ltot = _LOC64.unpack(loc)
            if ldisk != 0 or ltot > 1:
                raise _Bad("многотомный архив не поддерживается")
            loc_pos = eocd_pos - _LOC64.size
            if z_off + _Z64.size > loc_pos:
                raise _Bad("битый локатор ZIP64 EOCD")
            f.seek(z_off)
            zr = f.read(_Z64.size)
            if len(zr) < _Z64.size or zr[:4] != _SIG_Z64:
                raise _Bad("битая запись ZIP64 EOCD")
            _, rsize, _, _, disk, cd_disk, n_disk, n_tot, cd_size, cd_off = _Z64.unpack(zr)
            z64_pos, z64_len = z_off, 12 + rsize
            if rsize < 44 or z64_pos + z64_len > loc_pos:
                raise _Bad("ZIP64 EOCD налезает на локатор")
    if z64_pos < 0 and (n_tot == _U16 or cd_size == _U32 or cd_off == _U32):
        raise _Bad("в EOCD указан ZIP64, но записи ZIP64 EOCD нет")
    if disk != 0 or cd_disk != 0 or n_disk != n_tot:
        raise _Bad("многотомный архив не поддерживается")
    if n_tot > MAX_ENTRIES:
        raise _Bad("записей в пакете %d, предел %d" % (n_tot, MAX_ENTRIES))
    if cd_size > MAX_CD:
        raise _Bad("центральный каталог больше %d МБ" % (MAX_CD >> 20))
    cd_end = cd_off + cd_size
    a_end = z64_pos if z64_pos >= 0 else eocd_pos
    if cd_end > a_end:
        raise _Bad("центральный каталог не укладывается до конца архива "
                   "(данные перед ZIP со сдвинутыми смещениями или порча)")

    f.seek(cd_off)
    buf = f.read(cd_size)
    if len(buf) != cd_size:
        raise _Bad("центральный каталог оборван")

    ents = []
    pos = 0
    for idx in range(n_tot):
        if pos + _CD.size > len(buf) or buf[pos:pos + 4] != _SIG_CD:
            raise _Bad("битая запись центрального каталога")
        (_, _, _, flags, method, mtime, mdate, crc, csize, usize,
         nlen, elen, clen_, disk_, _, _, off) = _CD.unpack_from(buf, pos)
        end = pos + _CD.size + nlen + elen + clen_
        if end > len(buf):
            raise _Bad("запись центрального каталога выходит за его границы")
        nameb = buf[pos + _CD.size:pos + _CD.size + nlen]
        extra = buf[pos + _CD.size + nlen:pos + _CD.size + nlen + elen]
        if disk_ != 0:
            raise _Bad("многотомный архив не поддерживается")
        usize, csize, off, slot = _z64_extra(extra, usize, csize, off)
        try:
            name = nameb.decode("utf-8" if flags & 0x800 else "cp437")
        except UnicodeDecodeError:
            raise _Bad("имя записи не декодируется") from None
        e = _Ent(idx, name, nameb, bytes(buf[pos:end]), flags, method, mtime, mdate,
                 crc, csize, usize, off)
        if slot >= 0:
            e.off_slot = _CD.size + nlen + slot
        ents.append(e)
        pos = end
    if pos != len(buf):
        raise _Bad("в центральном каталоге лишние байты после последней записи")

    # Локальные заголовки: имя и метод обязаны совпасть с каталогом, иначе разные
    # парсеры увидят разные файлы.
    for e in ents:
        if e.off + _LH.size > cd_off:
            raise _Bad("локальный заголовок записи лежит за пределами данных")
        f.seek(e.off)
        h = f.read(_LH.size)
        if len(h) != _LH.size or h[:4] != _SIG_LOCAL:
            raise _Bad("битый локальный заголовок записи")
        _, _, lflags, lmethod, _, _, _, _, _, lnl, lel = _LH.unpack(h)
        if f.read(lnl) != e.nameb:
            raise _Bad("имя в локальном заголовке не совпадает с центральным каталогом")
        if lmethod != e.method:
            raise _Bad("метод сжатия в локальном заголовке не совпадает с каталогом")
        e.lflags = lflags
        e.data_start = e.off + _LH.size + lnl + lel
        e.data_end = e.data_start + e.csize

    phys = sorted(ents, key=lambda x: x.off)
    for a, b in zip(phys, phys[1:]):
        if a.off == b.off:
            raise _Bad("две записи каталога указывают на одни данные")
    slack = 0
    for k, e in enumerate(phys):
        e.seg_end = phys[k + 1].off if k + 1 < len(phys) else cd_off
        if e.data_end > e.seg_end:
            raise _Bad("записи перекрываются или данные выходят за границы")
        e.own_end = e.data_end
        if e.lflags & 8:
            d = _descr_len(f, e.data_end, e.seg_end, e.crc, e.csize, e.usize)
            if d is None:
                raise _Bad("не распознан data descriptor записи")
            e.own_end += d[0]
            e.dsig, e.dw = d[1], d[2]
        slack += e.seg_end - e.own_end
    first_off = phys[0].off if phys else cd_off
    slack += first_off
    slack += a_end - cd_end
    if z64_pos >= 0:
        slack += loc_pos - (z64_pos + z64_len)
    slack += size - (eocd_pos + _EOCD.size + clen)
    return _Box(size, ents, cd_off, cd_size, eocd_pos, clen, z64_pos, z64_len,
                loc_pos, first_off, slack)


def _policy(box):
    """Отказы по политике (см. docstring модуля). Список сообщений."""
    bad = []

    def add(msg):
        if msg not in bad:
            bad.append(msg)

    seen = {}
    for e in box.ents:
        if e.flags & 0x41:
            add("запись зашифрована")
        if e.method not in (0, 8):
            add("метод сжатия %d не допускается в OOXML/ODF (только STORED и DEFLATED)" % e.method)
        n = e.name.replace("\\", "/")
        if ("\x00" in n or n.startswith("/") or (len(n) > 1 and n[1] == ":")
                or ".." in n.split("/")):
            add("недопустимое имя записи (выход за каталог или абсолютный путь): %s"
                % clip(e.name, 80))
        key = e.name.casefold()
        if key in seen:
            add("дублирующееся имя записи: %s" % clip(e.name, 80))
        seen[key] = e
    unc = sum(e.usize for e in box.ents)
    cmp_ = sum(e.csize for e in box.ents)
    if unc > MAX_TOTAL_UNPACKED or (unc > MIN_BOMB_UNPACKED and unc / max(cmp_, 1) > MAX_RATIO):
        add("подозрение на zip-бомбу: распакованный объём %d байт при сжатом %d" % (unc, cmp_))
    return bad


def entries(src) -> list:
    """Опись контейнера в порядке центрального каталога. НЕ бросает: на файле,
    который не разбирается как ZIP, возвращает []. Политику отказов (дубли, бомба,
    traversal) не применяет -- опись полезна и для таких файлов; рубит их rebuild()."""
    try:
        with open(os.fspath(src), "rb") as f:
            box = _scan(f, os.fstat(f.fileno()).st_size)
    except Exception:  # noqa: BLE001 -- контракт: не бросаем
        return []
    return [ZipEntry(e.name, _dos_time(e.mdate, e.mtime), e.method, e.csize, e.usize,
                     e.crc, e.idx, e.flags, e.off) for e in box.ents]


def _dos_time(d, t):
    return ((d >> 9) + 1980, (d >> 5) & 0xF, d & 0x1F, t >> 11, (t >> 5) & 0x3F, (t & 0x1F) * 2)


# ============================================================================
#  Запись
# ============================================================================


def _copy(f, g, start, end):
    """Срез [start, end) входа -> выход. Возвращает число байт."""
    if end < start:
        raise _Bad("внутренняя ошибка: отрицательный отрезок")
    f.seek(start)
    left = end - start
    while left:
        b = f.read(min(left, _CHUNK))
        if not b:
            raise _Bad("файл оборван при чтении")
        g.write(b)
        left -= len(b)
    return end - start


def _pack(e, data):
    """Данные заменяемой записи в её же методе сжатия."""
    if e.method == 0:
        return data
    c = zlib.compressobj(_LEVEL[e.flags & 6], zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _new_local(f, e, crc, csize, usize):
    """Локальный заголовок заменённой записи: поля оригинала (флаги, версия, время,
    имя, local extra -- в т.ч. growth hint Office 0xA220), кроме crc/размеров и
    ZIP64-extra. У потокового писателя (бит 3) там заглушки, настоящие значения --
    в дескрипторе: тогда заголовок остаётся как был, а бит 3 и дескриптор
    сохраняются, чтобы заменённая запись не выбивалась из соседних."""
    f.seek(e.off)
    h = bytearray(f.read(_LH.size))
    lnl, lel = struct.unpack_from("<2H", h, 26)
    rest = f.read(lnl + lel)
    if e.lflags & 8:
        crc0, cs0, us0 = struct.unpack_from("<3L", h, 14)
        if _U32 in (cs0, us0):
            if any(_z64_values(rest[lnl:])):
                raise _Bad("замена записи с битом 3 и настоящими ZIP64-размерами "
                           "в локальном заголовке не поддерживается")
            return bytes(h) + rest                  # ZIP64-заглушки потокового писателя
        if (crc0, cs0, us0) == (0, 0, 0):
            return bytes(h) + rest                  # обычные заглушки
        # писатель продублировал настоящие значения в заголовке: обновим их ниже
    extra = _strip_z64(rest[lnl:])
    struct.pack_into("<3L", h, 14, crc, csize, usize)
    struct.pack_into("<H", h, 28, len(extra))
    return bytes(h) + rest[:lnl] + extra


def _descr(e, crc, csize, usize):
    """Новый data descriptor в том же формате, что у оригинала."""
    w = "<Q" if e.dw == 8 else "<L"
    return ((_SIG_DESC if e.dsig else b"") + struct.pack("<L", crc)
            + struct.pack(w, csize) + struct.pack(w, usize))


def _cd_replaced(e, off, crc, csize, usize):
    rec = e.rec
    nlen, elen = struct.unpack_from("<2H", rec, 28)
    extra = _strip_z64(rec[_CD.size + nlen:_CD.size + nlen + elen])
    if off >= _U32:
        raise _Bad("смещение записи превышает 4 ГБ")
    h = bytearray(rec[:_CD.size])
    struct.pack_into("<3L", h, 16, crc, csize, usize)
    struct.pack_into("<H", h, 30, len(extra))
    struct.pack_into("<L", h, 42, off)
    return bytes(h) + rec[_CD.size:_CD.size + nlen] + extra + rec[_CD.size + nlen + elen:]


def _cd_moved(e, off):
    b = bytearray(e.rec)
    if e.off_slot >= 0:                       # смещение живёт в ZIP64-extra
        b[e.off_slot:e.off_slot + 8] = off.to_bytes(8, "little")
    elif off >= _U32 or struct.unpack_from("<L", b, 42)[0] == _U32:
        raise _Bad("смещение записи превышает 4 ГБ")
    else:
        struct.pack_into("<L", b, 42, off)
    return bytes(b)


def _put(buf, fmt, at, new, marker):
    """Поле хвоста: значение-маркер ZIP64 (0xFFFF/0xFFFFFFFF) не трогаем."""
    if struct.unpack_from(fmt, buf, at)[0] != marker:
        struct.pack_into(fmt, buf, at, new)


def _write(f, g, box, replace, drop, strip, rep):
    ents = sorted(box.ents, key=lambda x: x.off)
    pos = 0
    if not strip:
        pos += _copy(f, g, 0, box.first_off)
    newoff = {}
    meta = {}                                  # idx -> (crc, csize, usize) заменённых
    for e in ents:
        if e.name in drop:
            rep.dropped += 1
            continue
        newoff[e.idx] = pos
        if e.name in replace:
            data = replace[e.name]
            body = _pack(e, data)
            if len(body) >= _U32:
                raise _Bad("заменяемая часть слишком велика")
            crc = zlib.crc32(data) & _U32
            meta[e.idx] = (crc, len(body), len(data))
            hdr = _new_local(f, e, crc, len(body), len(data))
            tail = _descr(e, crc, len(body), len(data)) if e.lflags & 8 else b""
            g.write(hdr)
            g.write(body)
            g.write(tail)
            pos += len(hdr) + len(body) + len(tail)
            if not strip:
                pos += _copy(f, g, e.own_end, e.seg_end)
            rep.recompressed += 1
        else:
            pos += _copy(f, g, e.off, e.own_end if strip else e.seg_end)
            rep.copied_raw += 1

    cd_off = pos
    for e in box.ents:                         # порядок каталога -- как в исходнике
        if e.idx not in newoff:
            continue
        if e.idx in meta:
            rec = _cd_replaced(e, newoff[e.idx], *meta[e.idx])
        elif newoff[e.idx] != e.off:
            rec = _cd_moved(e, newoff[e.idx])
        else:
            rec = e.rec
        g.write(rec)
        pos += len(rec)
    cd_size = pos - cd_off
    n = len(newoff)

    a_end = box.z64_pos if box.z64_pos >= 0 else box.eocd_pos
    if not strip:
        pos += _copy(f, g, box.cd_off + box.cd_size, a_end)
    z64_new = -1
    if box.z64_pos >= 0:
        f.seek(box.z64_pos)
        z = bytearray(f.read(box.z64_len))
        struct.pack_into("<4Q", z, 24, n, n, cd_size, cd_off)
        z64_new = pos
        g.write(z)
        pos += len(z)
        if not strip:
            pos += _copy(f, g, box.z64_pos + box.z64_len, box.loc_pos)
        f.seek(box.loc_pos)
        loc = bytearray(f.read(_LOC64.size))
        struct.pack_into("<Q", loc, 8, z64_new)
        g.write(loc)
    f.seek(box.eocd_pos)
    eocd = bytearray(f.read(_EOCD.size))
    _put(eocd, "<H", 8, n, _U16)
    _put(eocd, "<H", 10, n, _U16)
    _put(eocd, "<L", 12, cd_size, _U32)
    _put(eocd, "<L", 16, cd_off, _U32)
    if strip:
        struct.pack_into("<H", eocd, 20, 0)
    g.write(eocd)
    if not strip:
        _copy(f, g, box.eocd_pos + _EOCD.size, box.size)   # комментарий + мусор после него
    return [e.name for e in box.ents if e.idx in newoff]


def _verify(path, names, replace):
    """Независимая проверка выхода стандартным zipfile."""
    try:
        with zipfile.ZipFile(path) as zf:
            if [i.orig_filename for i in zf.infolist()] != names:
                raise _Bad("состав или порядок записей выхода не совпал с ожидаемым")
            if zf.testzip() is not None:
                raise _Bad("запись выхода не проходит проверку CRC")
            for name, data in replace.items():
                if zf.read(name) != data:
                    raise _Bad("замена записана неверно")
    except _Bad:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _Bad("выход не открывается стандартным zipfile: %s" % type(exc).__name__) from None


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def rebuild(src, dst, replace=None, drop=None, *, strip_slack=False) -> RebuildReport:
    """src -> dst. replace {имя: bytes} -- записи пережимаются; drop {имена} -- их в
    выходе нет; всё остальное -- сырые сжатые байты. Без replace и drop выход равен
    входу побайтно. НЕ бросает исключений. ok=False: dst не создан, прежний dst не
    тронут, причина в errors. strip_slack=True -- не переносить голову, щели, мусор
    после EOCD и комментарий архива (см. docstring модуля)."""
    rep = RebuildReport()
    part = None
    try:
        s, d = os.fspath(src), os.fspath(dst)
        replace = dict(replace or {})
        drop = set(drop or ())
        for k, v in replace.items():
            if not isinstance(k, str) or not isinstance(v, (bytes, bytearray, memoryview)):
                raise _Bad("replace: нужен словарь {имя части: bytes}")
            if len(v) > MAX_REPLACE:
                raise _Bad("заменяемая часть больше %d МБ" % (MAX_REPLACE >> 20))
        replace = {k: bytes(v) for k, v in replace.items()}
        if any(not isinstance(k, str) for k in drop):
            raise _Bad("drop: нужно множество имён частей")
        if replace.keys() & drop:
            raise _Bad("одна и та же часть и в replace, и в drop")
        if _same_file(s, d):
            raise _Bad("выход совпадает со входом: вход не модифицируется никогда")
        with open(s, "rb") as f:
            box = _scan(f, os.fstat(f.fileno()).st_size)
            rep.src_entries = len(box.ents)
            rep.slack_bytes = box.slack
            rep.comment_len = box.clen
            problems = _policy(box)
            have = {e.name for e in box.ents}
            problems += ["замена несуществующей части: %s" % clip(k, 80)
                         for k in replace if k not in have]
            if problems:
                for p in problems:
                    rep.err(p)
                raise _Refused()
            part = d + ".part"
            with open(part, "wb") as g:
                names = _write(f, g, box, replace, drop, strip_slack, rep)
        _verify(part, names, replace)
        os.replace(part, d)
        part = None
        rep.dst_entries = rep.copied_raw + rep.recompressed
        rep.ok = True
    except _Refused:
        pass
    except _Bad as exc:
        rep.err(exc)
    except Exception as exc:  # noqa: BLE001 -- контракт: наружу не бросаем
        # strerror, а не str(exc): в тексте OSError лежит имя файла.
        why = getattr(exc, "strerror", None) if isinstance(exc, OSError) else None
        rep.err("ошибка ввода-вывода: %s" % why if why else
                "внутренняя ошибка: %s" % type(exc).__name__)
    finally:
        if part is not None:
            try:
                os.remove(part)
            except OSError:
                pass
    if not rep.ok:
        rep.copied_raw = rep.recompressed = rep.dropped = rep.dst_entries = 0
        if not rep.errors:
            rep.err("пересборка не удалась")
    return rep


# ============================================================================
#  Самопроверка и CLI:  python -m core.zipfix            -- синтетика
#                       python -m core.zipfix файл...    -- метрики по реальным файлам
# ============================================================================


def _check_file(path, say):
    """Метрики пересборки одного реального файла. Содержимое и имена не печатаются."""
    import hashlib
    import tempfile

    def sha(p):
        return hashlib.sha256(open(p, "rb").read()).hexdigest()

    ok = True
    src = entries(path)
    with tempfile.TemporaryDirectory(prefix="zipfix_") as td:
        dst = os.path.join(td, "out")
        r = rebuild(path, dst)
        same = r.ok and sha(path) == sha(dst)
        out = entries(dst) if r.ok else []
        same_meta = [(e.name, e.date_time, e.compress_type, e.flag_bits) for e in src] == \
                    [(e.name, e.date_time, e.compress_type, e.flag_bits) for e in out]
        try:
            with zipfile.ZipFile(dst) as zf:
                tz = zf.testzip() is None
        except Exception:  # noqa: BLE001
            tz = False
        say("  без изменений: ok=%s sha256 %s, записей %d, сырьём %d, пережато %d, testzip %s, "
            "таймстемпы+порядок+метод %s, щелей %d байт, комментарий %d байт"
            % (r.ok, "СОВПАЛ" if same else "НЕ СОВПАЛ", r.src_entries, r.copied_raw,
               r.recompressed, "ok" if tz else "ПРОВАЛ", "совпали" if same_meta else "НЕ СОВПАЛИ",
               r.slack_bytes, r.comment_len))
        ok &= same and tz and same_meta and r.copied_raw == r.src_entries
        # Замена одной XML-части: все остальные обязаны читаться байт в байт.
        xml = [e for e in src if e.name.endswith(".xml") and e.name != "[Content_Types].xml"
               and e.file_size > 0]
        if xml:
            tgt = min(xml, key=lambda e: e.file_size).name
            with zipfile.ZipFile(path) as zf:
                new = zf.read(tgt) + b"\n"
                orig = {i.filename: zf.read(i) for i in zf.infolist() if i.filename != tgt}
            r2 = rebuild(path, dst, replace={tgt: new})
            with zipfile.ZipFile(dst) as zf:
                rest = all(zf.read(k) == v for k, v in orig.items())
                tz2 = zf.testzip() is None and zf.read(tgt) == new
            meta2 = [(e.name, e.date_time, e.compress_type, e.flag_bits) for e in entries(dst)]
            same2 = meta2 == [(e.name, e.date_time, e.compress_type, e.flag_bits) for e in src]
            say("  замена 1 части: ok=%s, сырьём %d, пережато %d, testzip+замена %s, "
                "остальные части байт в байт %s, таймстемпы+порядок+метод %s"
                % (r2.ok, r2.copied_raw, r2.recompressed, "ok" if tz2 else "ПРОВАЛ",
                   "да" if rest else "НЕТ", "совпали" if same2 else "НЕ СОВПАЛИ"))
            ok &= r2.ok and tz2 and rest and same2
        r3 = rebuild(path, dst, drop={src[-1].name}) if len(src) > 1 else None
        if r3:
            say("  удаление последней записи: ok=%s, сырьём %d, удалено %d, записей на выходе %d"
                % (r3.ok, r3.copied_raw, r3.dropped, r3.dst_entries))
            ok &= r3.ok and r3.dropped == 1 and r3.dst_entries == len(src) - 1
    return ok


def _crc_zeros(n):
    c = 0
    z = b"\x00" * (1 << 20)
    for _ in range(n >> 20):
        c = zlib.crc32(z, c)
    return c & _U32


def _mk_raw(name, body, usize, crc):
    """Минимальный ZIP с одной DEFLATED-записью и заданными (в т.ч. лживыми) размерами."""
    nb = name.encode()
    loc = struct.pack("<4s5H3L2H", _SIG_LOCAL, 20, 0, 8, 0, 0x21, crc, len(body), usize, len(nb), 0) + nb
    cd = struct.pack("<4s6H3L5H2L", _SIG_CD, 20, 20, 0, 8, 0, 0x21, crc, len(body), usize,
                     len(nb), 0, 0, 0, 0, 0, 0) + nb
    eocd = struct.pack("<4s4H2LH", _SIG_EOCD, 0, 0, 1, 1, len(cd), len(loc) + len(body), 0)
    return loc + body + cd + eocd


def _selftest():
    """Синтетика: тождественность, замена, удаление, ZIP64, дескрипторы, щели, патологии, фазз."""
    import hashlib
    import io
    import random
    import shutil
    import tempfile
    import warnings

    stat = [0, 0]

    def chk(name, cond, detail=""):
        stat[0] += 1
        if not cond:
            stat[1] += 1
            print("ПРОВАЛ: %s %s" % (name, detail))

    class NoSeek(io.BytesIO):                  # zipfile сам перейдёт на бит 3 и дескрипторы
        def seek(self, *a, **k):
            raise OSError("no seek")

    def mk(items, seekable=True, extra=b""):
        bio = io.BytesIO() if seekable else NoSeek()
        with zipfile.ZipFile(bio, "w") as zf:
            for name, data, method, dt in items:
                zi = zipfile.ZipInfo(name, dt)
                zi.compress_type = method
                zi.extra = extra
                zf.writestr(zi, data)
        return bio.getvalue()

    def set_flags6(raw):                       # как Word: «самый быстрый» deflate
        b = bytearray(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = zf.infolist()
        pos = struct.unpack_from("<L", raw, raw.rfind(_SIG_EOCD) + 16)[0]
        for i in infos:
            if i.compress_type == 8:
                for at in (pos + 8, i.header_offset + 6):
                    struct.pack_into("<H", b, at, struct.unpack_from("<H", b, at)[0] | 6)
            n, x, c = struct.unpack_from("<3H", b, pos + 28)
            pos += 46 + n + x + c
        return bytes(b)

    def inject(raw, head=b"", gap=b"", mid=b"", comment=b"", trail=b""):
        """Независимый от rebuild способ завести щели: смещения пересчитывает сам."""
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            infos = sorted(zf.infolist(), key=lambda i: i.header_offset)
        eocd = raw.rfind(_SIG_EOCD)
        cd_off = struct.unpack_from("<L", raw, eocd + 16)[0]
        out = bytearray(head)
        new = {}
        for k, i in enumerate(infos):
            end = infos[k + 1].header_offset if k + 1 < len(infos) else cd_off
            new[i.header_offset] = len(out)
            out += raw[i.header_offset:end] + gap
        cd = bytearray(raw[cd_off:eocd])
        pos = 0
        while pos < len(cd):
            struct.pack_into("<L", cd, pos + 42, new[struct.unpack_from("<L", cd, pos + 42)[0]])
            n, x, c = struct.unpack_from("<3H", cd, pos + 28)
            pos += 46 + n + x + c
        e = bytearray(raw[eocd:eocd + 22])
        struct.pack_into("<L", e, 16, len(out))
        struct.pack_into("<H", e, 20, len(comment))
        return bytes(out + cd + mid + e + comment + trail)

    def reorder_cd(raw):                        # меняем местами 2-ю и 6-ю записи только в каталоге
        eocd = raw.rfind(_SIG_EOCD)
        cd_off = struct.unpack_from("<L", raw, eocd + 16)[0]
        recs, pos = [], cd_off
        while pos < eocd:
            n, x, c = struct.unpack_from("<3H", raw, pos + 28)
            recs.append(raw[pos:pos + 46 + n + x + c])
            pos += 46 + n + x + c
        recs[1], recs[5] = recs[5], recs[1]
        return raw[:cd_off] + b"".join(recs) + raw[eocd:]

    D0, D1 = (1980, 1, 1, 0, 0, 0), (2026, 9, 2, 18, 10, 6)
    rnd = random.Random(7)
    blob = bytes(rnd.getrandbits(8) for _ in range(3000))
    xml = b"<a>" + b"<b>text</b>" * 200 + b"</a>"
    base = [("[Content_Types].xml", xml, 8, D0), ("_rels/.rels", xml[:300], 8, D0),
            ("word/", b"", 0, D0), ("word/empty.xml", b"", 8, D0), ("word/e2.xml", b"", 0, D0),
            ("word/document.xml", xml * 3, 8, D0), ("word/media/i.bin", blob, 0, D0),
            ("docProps/core.xml", xml[:150], 8, D1), ("docProps/app.xml", xml[:400], 8, D0)]
    td = tempfile.mkdtemp(prefix="zipfix_")
    P = lambda n: os.path.join(td, n)  # noqa: E731
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()  # noqa: E731

    def wr(n, b):
        with open(P(n), "wb") as fh:
            fh.write(b)
        return P(n)

    def raw_of(zpath):                          # {имя: локальный заголовок + данные}
        with open(zpath, "rb") as f:
            box = _scan(f, os.fstat(f.fileno()).st_size)
            out = {}
            for e in box.ents:
                f.seek(e.off)
                out[e.name] = f.read(e.data_end - e.off)
            return out

    def prof(zpath):
        return [(e.name, e.date_time, e.compress_type, e.flag_bits) for e in entries(zpath)]

    def bad(name, raw, **kw):
        src = wr("p.zip", raw) if isinstance(raw, bytes) else raw
        h0 = sha(src)
        dst = P("never.zip")
        if os.path.exists(dst):
            os.remove(dst)
        try:
            r = rebuild(src, dst, **kw)
        except Exception as exc:  # noqa: BLE001
            chk("%s: не бросает" % name, False, repr(exc))
            return
        chk("%s: ok=False и есть errors" % name, (not r.ok) and bool(r.errors), (r.ok, r.errors))
        chk("%s: вход не изменён" % name, sha(src) == h0)
        chk("%s: dst и .part не созданы" % name,
            not os.path.exists(dst) and not os.path.exists(dst + ".part"))
        chk("%s: счётчики обнулены" % name,
            (r.copied_raw, r.recompressed, r.dropped, r.dst_entries) == (0, 0, 0, 0))

    try:
        lim = zipfile.ZIP64_LIMIT
        zipfile.ZIP64_LIMIT = 50                # настоящий ZIP64: размеры/смещения в extra, EOCD64
        try:
            raw64, raw64d = mk(base), mk(base, seekable=False)
        finally:
            zipfile.ZIP64_LIMIT = lim
        plain = mk(base)
        gaps = dict(head=b"STUB" * 5, gap=b"\x00\x11" * 3, comment=b"cmt", trail=b"tail!")
        junk = inject(plain, **gaps)
        cases = {
            "deflate+stored+dir+empty": plain,
            "флаги Word (6)": set_flags6(plain),
            "бит 3, дескрипторы": mk(base, seekable=False),
            "ZIP64": raw64,
            "ZIP64 + бит 3": raw64d,
            "комментарий архива": plain[:-2] + struct.pack("<H", 11) + b"hello world",
            "щели, голова, мусор после EOCD": junk,
            "extra в обоих заголовках": mk(base, extra=struct.pack("<HH", 0xA220, 36) + b"\x28\xa0\x20\x00" + b"\x00" * 32),
            "порядок каталога != физический": reorder_cd(plain),
            "вложенный ZIP внутри записи (stored)": mk(base + [("embed.zip", plain, 0, D0)]),
            "пустой архив": mk([]),
        }
        for k, raw in cases.items():
            src = wr("s.zip", raw)
            n = len(zipfile.ZipFile(src).infolist())
            r = rebuild(src, P("o.zip"))
            chk("[%s] без изменений: ok" % k, r.ok, r.errors)
            chk("[%s] sha256 совпал" % k, r.ok and sha(src) == sha(P("o.zip")))
            chk("[%s] всё сырьём" % k, r.copied_raw == n == r.src_entries == r.dst_entries, vars(r))
            if not n:
                continue
            old = raw_of(src)
            for tag, rep, drp, strip in (
                    ("замена мал.", {"docProps/core.xml": b"<new/>"}, None, False),
                    ("замена больш.", {"word/document.xml": blob * 40}, None, False),
                    ("замена пустой", {"word/media/i.bin": b""}, None, False),
                    ("замена + удаление", {"docProps/app.xml": b"<x/>"}, {"word/document.xml"}, False),
                    ("замена, strip", {"docProps/core.xml": b"<new/>"}, None, True),
                    ("всё удалено", None, {e.name for e in entries(src)}, False)):
                r = rebuild(src, P("o.zip"), replace=rep, drop=drp, strip_slack=strip)
                chk("[%s] %s: ok" % (k, tag), r.ok, r.errors)
                if not r.ok:
                    continue
                with zipfile.ZipFile(P("o.zip")) as zf, zipfile.ZipFile(src) as zs:
                    chk("[%s] %s: testzip" % (k, tag), zf.testzip() is None)
                    gone = set(drp or ())
                    chk("[%s] %s: состав и порядок" % (k, tag),
                        [i.filename for i in zf.infolist()] ==
                        [i.filename for i in zs.infolist() if i.filename not in gone])
                    for i in zf.infolist():
                        want = (rep or {}).get(i.filename)
                        want = zs.read(i.filename) if want is None else want
                        chk("[%s] %s: часть %s читается" % (k, tag, i.filename),
                            zf.read(i.filename) == want)
                chk("[%s] %s: таймстемпы/метод/флаги/порядок" % (k, tag),
                    prof(P("o.zip")) == [p for p in prof(src) if p[0] not in (drp or ())])
                new = raw_of(P("o.zip"))
                chk("[%s] %s: нетронутые записи байт в байт" % (k, tag),
                    all(new[nm] == old[nm] for nm in new if nm not in (rep or {})))
                with open(src, "rb") as f1, open(P("o.zip"), "rb") as f2:
                    b1 = {e.name: e for e in _scan(f1, os.fstat(f1.fileno()).st_size).ents}
                    b2 = {e.name: e for e in _scan(f2, os.fstat(f2.fileno()).st_size).ents}   # проверит и дескрипторы
                chk("[%s] %s: бит 3 и формат дескриптора как у оригинала" % (k, tag),
                    all((b1[n_].lflags & 8, b1[n_].flags, b1[n_].dsig, b1[n_].dw) ==
                        (b2[n_].lflags & 8, b2[n_].flags, b2[n_].dsig, b2[n_].dw) for n_ in b2))
                chk("[%s] %s: счётчики" % (k, tag),
                    (r.recompressed, r.dropped, r.copied_raw + r.recompressed) ==
                    (len(rep or {}), len(drp or ()), r.dst_entries))

        # local extra заменённой записи сохраняется, метод и уровень -- по флагам оригинала
        hint = struct.pack("<HH", 0xA220, 36) + b"\x28\xa0\x20\x00" + b"\x00" * 32
        src = wr("h.zip", mk([("a.xml", xml, 8, D0), ("b.xml", xml, 0, D0)], extra=hint))
        r = rebuild(src, P("o.zip"), replace={"a.xml": b"<z/>", "b.xml": b"<y/>"})
        chk("growth hint: ok", r.ok, r.errors)
        with open(P("o.zip"), "rb") as f:
            box = _scan(f, os.fstat(f.fileno()).st_size)
            f.seek(box.ents[0].off)
            h = f.read(box.ents[0].data_start - box.ents[0].off)
        chk("growth hint сохранён в локальном заголовке заменённой записи", hint in h)
        chk("growth hint сохранён в каталоге", all(hint in e.rec for e in box.ents))
        chk("методы сохранены (DEFLATED остался, STORED остался)",
            [e.method for e in box.ents] == [8, 0])
        data = bytes(rnd.getrandbits(3) for _ in range(20000))
        sizes = {fl: len(_pack(_Ent(0, "x", b"x", b"", fl, 8, 0, 0, 0, 0, 0, 0), data))
                 for fl in (0, 2, 4, 6)}
        chk("уровень по флагам: максимальный не хуже самого быстрого", sizes[2] <= sizes[6], sizes)

        # щели: strip_slack возвращает чистую раскладку; мёртвая зона перед EOCD zipfile не читает
        src = wr("j.zip", junk)
        r = rebuild(src, P("o.zip"), strip_slack=True)
        chk("strip_slack: ok", r.ok, r.errors)
        chk("strip_slack: выход == чистый исходник до инъекции",
            r.ok and open(P("o.zip"), "rb").read() == plain)
        chk("slack_bytes и comment_len посчитаны верно",
            (r.slack_bytes, r.comment_len) == (20 + 6 * len(base) + 5, 3), (r.slack_bytes, r.comment_len))
        src = wr("m.zip", inject(plain, mid=b"DEAD"))
        r = rebuild(src, P("o.zip"))
        chk("мёртвая зона перед EOCD: без strip отказ (zipfile не откроет выход)", not r.ok and bool(r.errors))
        r = rebuild(src, P("o.zip"), strip_slack=True)
        chk("мёртвая зона перед EOCD: со strip чистый выход",
            r.ok and open(P("o.zip"), "rb").read() == plain, r.errors)

        # патологии: везде ok=False, errors, вход не изменён, dst не создан
        bad("пустой файл", b"")
        bad("мусор", b"x" * 100)
        bad("PK-сигнатура, не ZIP", b"PK\x03\x04" + b"junk" * 50)
        bad("PK EOCD-сигнатура, не ZIP", b"PK\x05\x06" + b"\xff" * 40)
        bad("только PK", b"PK")
        fake = b"PK\x05\x06" + b"\x00" * 18          # EOCD «пустого архива» внутри комментария
        bad("поддельный EOCD в конце комментария", plain[:-2] + struct.pack("<H", 22) + fake)
        bad("поддельный EOCD в середине комментария", plain[:-2] + struct.pack("<H", 24) + fake + b"zz")
        bad("настоящий EOCD + мусор с поддельным", plain + fake + b"zz")
        for cut in (22, 60, len(plain) // 2, len(plain) - 1, len(plain) - 22):
            bad("обрезан на %d" % cut, plain[:cut])
        bad("traversal ../../x", mk([("../../x", b"1", 8, D0)]))
        bad("traversal в глубине", mk([("a/../../x", b"1", 8, D0)]))
        bad("traversal, обратные слэши", mk([("..\\..\\x", b"1", 8, D0)]))
        bad("абсолютный путь", mk([("/etc/x", b"1", 8, D0)]))
        bad("диск", mk([("C:/x", b"1", 8, D0)]))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bad("дубль имён", mk([("a.xml", b"1", 8, D0), ("a.xml", b"2", 8, D0)]))
            bad("дубль, отличие регистра", mk([("a.xml", b"1", 8, D0), ("A.XML", b"2", 8, D0)]))
        bad("метод bzip2", mk([("a.xml", b"1" * 99, zipfile.ZIP_BZIP2, D0)]))
        enc = bytearray(plain)
        struct.pack_into("<H", enc, struct.unpack_from("<L", enc, enc.rfind(_SIG_EOCD) + 16)[0] + 8, 1)
        bad("флаг шифрования", bytes(enc))
        bad("replace несуществующей части", plain, replace={"нет.xml": b"1"})
        bad("replace и drop одной части", plain,
            replace={"docProps/app.xml": b"1"}, drop={"docProps/app.xml"})
        bad("replace: не bytes", plain, replace={"docProps/app.xml": "строка"})
        src = wr("same.zip", plain)
        r = rebuild(src, src)
        chk("вход == выход: отказ и вход цел", (not r.ok) and open(src, "rb").read() == plain)
        # зип-бомба: 70 МБ нулей жмутся в ~70 КБ; распаковки при пересборке нет вовсе
        co = zlib.compressobj(9, zlib.DEFLATED, -15)
        body = b"".join(co.compress(b"\x00" * (1 << 20)) for _ in range(70)) + co.flush()
        bad("зип-бомба (70 МБ из %d КБ)" % (len(body) >> 10),
            _mk_raw("a.xml", body, 70 << 20, _crc_zeros(70 << 20)))
        b = bytearray(plain)
        b[0:4] = b"XXXX"
        bad("битый локальный заголовок", bytes(b))
        b = bytearray(plain)
        b[struct.unpack_from("<L", b, b.rfind(_SIG_EOCD) + 16)[0] + 46] ^= 0x20
        bad("имя в каталоге != имени в локальном заголовке", bytes(b))
        b = bytearray(plain)
        for at in (8, 10):
            struct.pack_into("<H", b, b.rfind(_SIG_EOCD) + at, len(base) + 5)
        bad("в EOCD записей больше, чем в каталоге", bytes(b))
        b = bytearray(plain)
        struct.pack_into("<L", b, b.rfind(_SIG_EOCD) + 16, 3)
        bad("смещение каталога указывает в никуда", bytes(b))
        keep = wr("keep.zip", b"old content")
        r = rebuild(wr("p2.zip", b"junk" * 20), keep)
        chk("отказ не трогает существующий dst", (not r.ok) and open(keep, "rb").read() == b"old content")
        r = rebuild(P("нет_такого.zip"), P("q.zip"))
        chk("нет входного файла: ok=False, без исключения, без пути в тексте",
            (not r.ok) and r.errors and td not in " ".join(r.errors), r.errors)
        r = rebuild(wr("p3.zip", plain), os.path.join(td, "нет_каталога", "o.zip"))
        chk("нельзя записать выход: ok=False без исключения", not r.ok and bool(r.errors))
        chk("entries() на мусоре и на отсутствующем файле: []",
            entries(wr("g.zip", b"PK\x03\x04 junk")) == [] and entries(P("нет")) == [])
        src = wr("e.zip", plain)
        with zipfile.ZipFile(src) as zf:
            want = [(i.filename, i.date_time, i.compress_type, i.compress_size, i.file_size, i.CRC,
                     i.flag_bits, i.header_offset) for i in zf.infolist()]
        chk("entries() == zipfile.infolist()",
            [(e.name, e.date_time, e.compress_type, e.compress_size, e.file_size, e.crc,
              e.flag_bits, e.header_offset) for e in entries(src)] == want)
        chk("entries(): order", [e.order for e in entries(src)] == list(range(len(want))))

        # фазз: порча валидного архива. Не бросать, вход цел, ok => выход валиден, .part не остаётся
        pool = [plain, raw64, raw64d, junk]
        for it in range(300):
            raw = bytearray(rnd.choice(pool))
            for _ in range(rnd.randint(1, 4)):
                m = rnd.randint(0, 2)
                at = rnd.randrange(len(raw))
                if m == 0:
                    raw[at] = rnd.getrandbits(8)
                elif m == 1:
                    del raw[at:at + rnd.randint(1, 40)]
                else:
                    raw[at:at + rnd.randint(0, 8)] = bytes(rnd.getrandbits(8) for _ in range(rnd.randint(0, 8)))
            src = wr("f.zip", bytes(raw))
            h0 = sha(src)
            try:
                entries(src)
                r = rebuild(src, P("fo.zip"), replace={"docProps/core.xml": b"<n/>"} if it % 2 else None)
            except Exception as exc:  # noqa: BLE001
                chk("фазз %d: не бросает" % it, False, repr(exc))
                continue
            chk("фазз %d: вход цел" % it, sha(src) == h0)
            if r.ok:
                try:
                    with zipfile.ZipFile(P("fo.zip")) as zf:
                        good = zf.testzip() is None
                except Exception:  # noqa: BLE001
                    good = False
                chk("фазз %d: ok => выход валиден" % it, good)
            chk("фазз %d: .part не осталось" % it, not os.path.exists(P("fo.zip.part")))
            if os.path.exists(P("fo.zip")):
                os.remove(P("fo.zip"))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("ИТОГО: %d проверок, %d провалено" % tuple(stat))
    return stat[1] == 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        good = True
        for n, p in enumerate(sys.argv[1:], 1):
            print("файл %d:" % n)
            try:
                good &= _check_file(p, print)
            except Exception as exc:  # noqa: BLE001
                good = False
                print("  ПРОВАЛ проверки: %s" % type(exc).__name__)
        sys.exit(0 if good else 1)
    sys.exit(0 if _selftest() else 1)
