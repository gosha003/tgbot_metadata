"""Чистка ODF на месте: meta.xml, settings.xml, комментарии, миниатюра, Pictures/.

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
import shutil
import tempfile
import zipfile
import zlib

from core import sniff
from core.cleanmodel import Act, CleanResult, Profile
from core.insp_legacy import _PATH_RE
from core.zipfix import rebuild

__all__ = ["clean_odf"]

MAX_PICTURE = 64 * 1024 * 1024   # одна картинка из Pictures/, как MAX_NESTED у OOXML

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
# Элементы meta.xml, у которых путь лежит в атрибуте xlink:href, а не в тексте:
# шаблон документа (у LibreOffice -- file:///C:/Users/ИМЯ/...) и автоперезагрузка.
# _LEAF их не видит: они пустые. Убираем целиком -- файл без шаблона рядовой, а
# пустой href был бы следом. Значения атрибутов могут содержать «>», поэтому
# кавычки разбираем явно.
_LINKED = re.compile(
    br"<((?:[\w.-]+:)?)(template|auto-reload)(?=[\s/>])"
    br"""(?:[^>"']|"[^"]*"|'[^']*')*?(?:/>|>\s*</\1\2\s*>)""")


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


def _clean_one(tmp, i, data, prof, keep):
    """Байты вложения -> (семейство, CleanResult | None, новые байты | None)."""
    p_in = os.path.join(tmp, "p%04d.in" % i)
    p_out = os.path.join(tmp, "p%04d.out" % i)
    with open(p_in, "wb") as fh:
        fh.write(data)
    fam = sniff.family(sniff.sniff(p_in))
    if fam == "image":
        from core.clean_image import clean_image
        sub = clean_image(p_in, p_out, prof, keep)
    elif fam == "pdf":
        # LibreOffice кладёт вставленный PDF в Pictures/ как картинку.
        from core.clean_pdf import clean_pdf
        sub = clean_pdf(p_in, p_out, prof, keep)
    else:
        return fam, None, None
    out = None
    if sub.ok and os.path.isfile(p_out):
        with open(p_out, "rb") as fh:
            out = fh.read()
    return fam, sub, out


def _clean_pictures(res, src, names, prof, keep, replace):
    """Pictures/ -> чистильщик картинок, как media/ у OOXML. Инспектор разбирает
    Pictures/ рекурсивно, так что EXIF фото без этого валил оракул."""
    pics = [n for n in names if n.startswith("Pictures/") and not n.endswith("/")]
    if not pics:
        return
    what = "Вложенное изображение"
    skipped = {}
    tmp = tempfile.mkdtemp(prefix=".odf_")
    try:
        with zipfile.ZipFile(src) as zf:
            for i, name in enumerate(pics):
                try:
                    # file_size из заголовка может врать: читаем с потолком сами.
                    with zf.open(name) as fh:
                        data = fh.read(MAX_PICTURE + 1)
                    if len(data) > MAX_PICTURE:
                        res.act(Act.UNREMOVABLE, name, what, "> %d МБ" % (MAX_PICTURE >> 20), "",
                                "Вложение больше %d МБ: в память не берём, метаданные внутри "
                                "остались." % (MAX_PICTURE >> 20))
                        continue
                    fam, sub, out = _clean_one(tmp, i, data, prof, keep)
                except (zipfile.BadZipFile, zlib.error, EOFError):
                    raise   # порча контейнера -- это решает clean_odf целиком
                except Exception as exc:  # noqa: BLE001 -- одна картинка не валит остальные
                    res.act(Act.FAILED, name, what, "", "",
                            "Чистка вложения упала (%s): метаданные внутри остались."
                            % type(exc).__name__)
                    continue
                finally:
                    for p in os.listdir(tmp):
                        try:
                            os.remove(os.path.join(tmp, p))
                        except OSError:
                            pass
                if sub is None:
                    tail = name.rsplit("/", 1)[-1]
                    ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else "?"
                    skipped[ext] = skipped.get(ext, 0) + 1
                    continue
                if not (sub.ok and out is not None):
                    res.act(Act.UNREMOVABLE, name, what, "", "",
                            "Вычистить вложение не удалось (%s): метаданные внутри остались."
                            % "; ".join(sub.errors)[:300])
                    continue
                if sub.actions and all(a.act is Act.KEPT for a in sub.actions):
                    res.act(Act.KEPT, name, "Вложенный файл без метаданных", "", "",
                            "Чистильщик вложения ничего не убирал.")
                else:
                    for a in sub.actions:
                        res.act(a.act, "%s > %s" % (name, a.location), a.label, a.before,
                                a.after, a.note)
                if out != data:
                    replace[name] = out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if skipped:
        res.act(Act.KEPT, "Pictures/", "Нерастровые изображения",
                ", ".join("%s: %d" % kv for kv in sorted(skipped.items())), "",
                "Форматы, которые чистильщик картинок не разбирает (SVM, EMF, WMF, SVG): "
                "метаданные в них не искались и не убирались.")


def _clean(res, src, dst, prof, keep):
    paranoid = prof is Profile.PARANOID
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
    linked = []

    def drop_linked(m):
        local = m.group(2).decode("ascii")
        if _kept(keep, local):
            return m.group(0)
        linked.append(local)
        return b""

    new_meta = _LINKED.sub(drop_linked, new_meta)
    if linked:
        res.act(Act.REMOVED, "meta.xml", "ссылки на файлы (%s)" % ", ".join(linked), "", "",
                "Шаблон и автоперезагрузка хранят путь в атрибуте xlink:href: обычно это "
                "file:///C:/Users/ИМЯ/... или сетевой ресурс.")
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

    _clean_pictures(res, src, names, prof, keep, replace)

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
        _clean(res, str(src), str(dst), prof, keep)
    except Exception as exc:  # noqa: BLE001
        res.ok = False
        if isinstance(exc, (zipfile.BadZipFile, zlib.error, EOFError)):
            # Битая запись (CRC, обрезанный deflate) -- это порча файла, а не наша ошибка.
            res.err("ODF-контейнер повреждён: часть не распаковывается (%s). Выход не создан."
                    % type(exc).__name__)
        else:
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
