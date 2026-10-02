"""Чистка ODF на месте: meta.xml, settings.xml, комментарии, миниатюра.

Фаза 2. Диспетчер core.clean вызывает clean_odf для семейства odf
(.odt/.ods/.odp). Контейнер перекладывает core.zipfix: меняются только
перечисленные части, остальные записи остаются сырыми сжатыми байтами,
включая DOS-время. Поэтому таймстемпы ZIP не нормализуются: у Word в .odt
это 1980-01-01, у LibreOffice -- реальное время сохранения. Жёсткий выбор
оставил бы отпечаток на половине файлов.

XML правится побайтово, без сериализатора: круг через lxml меняет порядок
атрибутов и вид пустых тегов.

Публичный API:
    clean_odf(src, dst, profile, keep, force_signed=False) -> CleanResult

Исключений наружу не бросает. Вход не изменяется.
"""

from __future__ import annotations

import os
import re
import zipfile

from core.cleanmodel import Act, CleanResult, Profile
from core.insp_legacy import _PATH_RE
from core.zipfix import rebuild

__all__ = ["clean_odf"]

# Листья, чей текст -- личность или окружение. Префикс пространства имён
# любой: у Word и LibreOffice он разный.
_LEAK = frozenset((
    "initial-creator", "creator", "printed-by", "template", "user-defined",
))
# Провенанс. STEALTH оставляет: пустой generator сам сигнал scrubbed.
_PROV = frozenset((
    "generator", "creation-date", "date", "print-date", "editing-cycles",
    "editing-duration", "keyword", "description", "subject", "title",
))

_LEAF = re.compile(
    br"<((?:[\w.-]+:)?)([\w.-]+)([^>]*)>([^<]*)</\1\2>")
_ANN = re.compile(
    br"<((?:[\w.-]+:)?)annotation\b[^>]*>.*?</\1annotation>", re.DOTALL)
_STAT = re.compile(
    br"<((?:[\w.-]+:)?)document-statistic\b[^>]*/>")
_ENTRY = re.compile(
    br"<((?:[\w.-]+:)?)file-entry\b([^>]*?)/>")


def _kept(keep, name) -> bool:
    if not keep:
        return False
    return name.casefold() in {str(x).casefold() for x in keep}


def _blank_leaves(xml, names):
    """Текст перечисленных листьев -> пусто. Теги и атрибуты остаются."""
    def sub(m):
        local = m.group(2).decode("ascii", "replace")
        if local not in names or not m.group(4).strip():
            return m.group(0)
        return (b"<" + m.group(1) + m.group(2) + m.group(3) + b"></"
                + m.group(1) + m.group(2) + b">")
    return _LEAF.sub(sub, xml)


def _blank_settings(xml):
    """config-item с принтером или путём. Остальные настройки -- содержимое."""
    def sub(m):
        if m.group(2) != b"config-item":
            return m.group(0)
        attrs = m.group(3).decode("utf-8", "replace").lower()
        text = m.group(4).decode("utf-8", "replace")
        if "printer" not in attrs and not _PATH_RE.search(text):
            return m.group(0)
        if not m.group(4).strip():
            return m.group(0)
        return (b"<" + m.group(1) + m.group(2) + m.group(3) + b"></"
                + m.group(1) + m.group(2) + b">")
    return _LEAF.sub(sub, xml)


def _strip_annotations(xml):
    if b"annotation" not in xml:
        return xml
    prev = None
    while prev != xml:
        prev = xml
        xml = _ANN.sub(b"", xml)
    return xml


def _strip_stats(xml):
    return _STAT.sub(
        lambda m: b"<" + m.group(1) + b"document-statistic/>", xml)


def _strip_thumb_entries(xml):
    def sub(m):
        attrs = m.group(2).decode("utf-8", "replace").lower()
        if "thumbnails/" in attrs:
            return b""
        return m.group(0)
    return _ENTRY.sub(sub, xml)


def _read(path, name):
    with zipfile.ZipFile(path) as zf:
        try:
            return zf.read(name)
        except KeyError:
            return None


def _names(path):
    with zipfile.ZipFile(path) as zf:
        return [i.filename for i in zf.infolist()]


def _generator(meta):
    for m in _LEAF.finditer(meta):
        if m.group(2) == b"generator":
            return m.group(4).decode("utf-8", "replace").strip()
    return ""


def _clean(res, src, dst, paranoid, keep):
    try:
        names = _names(src)
    except Exception as exc:  # noqa: BLE001
        res.err("ODF не открылся как ZIP: %s" % type(exc).__name__)
        return
    replace = {}
    meta = _read(src, "meta.xml")
    if meta is None:
        res.err("нет meta.xml: чистить нечего, выход не создан.")
        return
    gen = _generator(meta)
    want = set(_LEAK)
    if paranoid:
        want |= _PROV
    want = {n for n in want if not _kept(keep, n)}
    new_meta = _blank_leaves(meta, want)
    if paranoid and not _kept(keep, "document-statistic"):
        new_meta = _strip_stats(new_meta)
    if new_meta != meta:
        replace["meta.xml"] = new_meta
        res.act(Act.BLANKED, "meta.xml", "свойства документа", "", "",
                "Личность обнулена, элементы оставлены: их отсутствие само "
                "признак чистки. Генератор %s."
                % ("убран" if paranoid and "generator" in want
                   else "сохранён"))
    if gen and not _kept(keep, "zip-time"):
        word = "microsoft" in gen.lower()
        res.act(Act.KEPT, "ZIP", "время записей", "", "",
                "Время ZIP оставлено как записал %s (%s). Смена времени сама "
                "была бы следом обработки."
                % ("Word" if word else "производитель",
                   "1980-01-01" if word else "реальное время сохранения"))

    settings = _read(src, "settings.xml")
    if settings is not None and not _kept(keep, "settings"):
        new_s = _blank_settings(settings)
        if new_s != settings:
            replace["settings.xml"] = new_s
            res.act(Act.BLANKED, "settings.xml", "пути и принтер", "", "",
                    "Имя принтера и локальные пути. Остальные настройки на месте.")

    for part in ("content.xml", "styles.xml"):
        blob = _read(src, part)
        if blob is None or _kept(keep, part):
            continue
        new = _blank_leaves(_strip_annotations(blob), {"creator"})
        if new != blob:
            replace[part] = new
            res.act(Act.REMOVED, part, "правки и комментарии", "", "",
                    "Имена авторов правок обнулены, комментарии вынуты: "
                    "текст комментария инспектор считает утечкой личности.")

    drop = [n for n in names if n.startswith("Thumbnails/")]
    if drop and not _kept(keep, "Thumbnails"):
        manifest = replace.get("META-INF/manifest.xml") or _read(src, "META-INF/manifest.xml")
        if manifest:
            cleaned = _strip_thumb_entries(manifest)
            if cleaned != manifest:
                replace["META-INF/manifest.xml"] = cleaned
        res.act(Act.REMOVED, "Thumbnails/", "миниатюра", "%d" % len(drop), "",
                "Картинка предпросмотра может показывать прежнюю страницу.")

    pics = [n for n in names if n.startswith("Pictures/")]
    if pics:
        res.act(Act.UNREMOVABLE, "Pictures/", "вложенные изображения",
                "%d" % len(pics), "",
                "Картинки лежат как есть, вместе со своим Exif. Чистка meta.xml "
                "их не касается.")

    if not replace and not drop:
        res.act(Act.KEPT, "ODF", "нечего убирать", "", "",
                "Личности и окружения в meta.xml и settings.xml не нашлось.")
    rep = rebuild(src, dst, replace or None, drop or None)
    if not rep.ok:
        res.err(rep.errors[0] if rep.errors else "пересборка ODF не удалась")
        return
    res.ok = True


def clean_odf(src, dst, profile=Profile.STEALTH, keep=None, force_signed=False) -> CleanResult:
    """Очистить ODF src -> dst. Вход не меняется. Не бросает.

    profile: STEALTH оставляет генератор и даты, PARANOID убирает и их.
    keep: локальные имена элементов, которые не трогать (creator, generator).
    force_signed: не используется. Сигнатура как у остальных чистильщиков.
    """
    del force_signed
    res = CleanResult(src=str(src), dst=str(dst), fmt="odt",
                      profile=getattr(profile, "value", str(profile)))
    try:
        prof = Profile(str(getattr(profile, "value", profile)).lower())
    except ValueError:
        res.err("Неизвестный профиль чистки.")
        return res
    try:
        _clean(res, str(src), str(dst), prof is Profile.PARANOID, keep)
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        res.err("Внутренняя ошибка чистки ODF: %s" % type(exc).__name__)
        if os.path.exists(str(dst)):
            try:
                os.remove(dst)
            except OSError:
                pass
    return res


def _selftest():
    import shutil
    import tempfile

    from core.clean import survived_values
    from core.inspect import inspect_file
    from tests.fixtures import ODT_CREATOR, ODT_GENERATOR, ODT_INITIAL_CREATOR, make_odt

    tmp = tempfile.mkdtemp(prefix="odf_clean_")
    try:
        src, _exp = make_odt(tmp)
        before = open(src, "rb").read()
        dst = os.path.join(tmp, "out.odt")
        res = clean_odf(src, dst, Profile.STEALTH)
        assert res.ok and not res.errors, res.errors
        assert open(src, "rb").read() == before
        rep = inspect_file(dst)
        lived = survived_values(inspect_file(src), rep)
        assert not lived, [(s.label, s.value[:40]) for s in lived]
        blob = " ".join(f.value or "" for f in rep.findings)
        assert ODT_CREATOR not in blob and ODT_INITIAL_CREATOR not in blob
        assert ODT_GENERATOR in blob, "STEALTH обязан оставить генератор"
        dst2 = os.path.join(tmp, "out2.odt")
        res2 = clean_odf(src, dst2, Profile.PARANOID)
        assert res2.ok, res2.errors
        blob2 = " ".join(f.value or "" for f in inspect_file(dst2).findings)
        assert ODT_GENERATOR not in blob2
        assert not survived_values(inspect_file(src), inspect_file(dst2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    _selftest()
