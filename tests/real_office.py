"""Генерация файлов настоящим Microsoft Office для приёмки.

Зачем. Синтетические фикстуры (`tests/fixtures.py`) проверяют, что код делает
то, что задумано. Они не могут проверить, что задумано верно: это показывают
только файлы, которые действительно сохранило настоящее приложение. За одну
сессию такие файлы дали три ложных срабатывания инспектора и опровергли
утверждение о таймстемпах ZIP у ODF, на котором собирались строить `clean_odf`.
Поэтому генератор лежит в репозитории, а не остаётся разовым скриптом.

Где в системе. Сбоку от основного набора тестов: ничего из `core/` и
`tests/test_*.py` от этого модуля не зависит. Office — необязательное
обогащение, как `exiftool` и LibreOffice (правило проекта «ноль обязательных
внешних бинарников»), поэтому при отсутствии Word бросается `Unavailable`, а
вызывающий обязан это пережить, а не упасть.

Публичный API:
    make_word_files(dest=None) -> dict   настоящие .doc/.rtf/.odt/.docx
    available() -> bool                  зарегистрирован ли Word.Application
    Unavailable                          исключение «Office недоступен»

CLI:
    PYTHONIOENCODING=utf-8 python -m tests.real_office [каталог]

ПРИВАТНОСТЬ. Созданные файлы несут имя пользователя Word этой машины --
настоящее имя человека, потому что именно его Word пишет в `dc:creator`. Это
не недосмотр, а то, что и надо проверять. Но из этого следует два запрета:
каталог по умолчанию лежит в `%TEMP%`, а не в репозитории, и содержимое
находок в логи и отчёты не печатается.

ПОБОЧНЫЕ ЭФФЕКТЫ. Скрипт на время меняет два параметра установленного Word --
имя пользователя и предупреждение о сохранении файла с исправлениями, -- и
возвращает их в блоке finally. Если процесс убить между правкой и
восстановлением, параметры останутся изменёнными.

Зависимости: только stdlib плюс `powershell`, который есть в любой Windows.
pywin32 намеренно не используется: это была бы новая обязательная зависимость
ради вспомогательного модуля.
"""

from __future__ import annotations

import os
import subprocess
import tempfile

__all__ = ["make_word_files", "available", "Unavailable", "WORD_FILES"]


class Unavailable(RuntimeError):
    """Office на этой машине недоступен или сценарий не отработал."""


# Что создаёт make_word_files: имя файла -> код формата Word (WdSaveFormat).
# wdFormatDocument97=0, wdFormatRTF=6, wdFormatXMLDocument=12,
# wdFormatOpenDocumentText=23.
WORD_FILES = (
    ("word_real.doc", 0),
    ("word_real.rtf", 6),
    ("word_real.docx", 12),
    ("word_real.odt", 23),
)

# Значения, которые мы просим Word записать. Кириллица здесь не украшение:
# Unicode-секция пользовательских свойств (MS-OLEPS) и кодировка RTF
# (\'hh против \uN) на латинице не проверяются.
_TITLE = "Отчёт за 3 квартал"
_SUBJECT = "Внутренний документ"
_COMPANY = "ООО Ромашка-Тест"
_MANAGER = "Сидоров Сергей Петрович"
_KEYWORDS = "отчёт; тест; метаданные"
_PROP_1 = ("Согласовано", "Кузнецова Анастасия Викторовна")
_PROP_2 = ("Отдел", "Бухгалтерия, каб. 312")
_REVIEWER_1 = "Кузнецова Анастасия Викторовна"
_REVIEWER_2 = "Громов Дмитрий Алексеевич"
_COMMENT = "Проверить источник данных"

# Три приёма, без которых сценарий не работает. Каждый стоил отдельной отладки,
# поэтому они описаны здесь, а не только в коде:
#
# 1. PowerShell не умеет индексировать параметризованное свойство COM:
#    $doc.BuiltInDocumentProperties.Item("Title") возвращает $null. Нужен
#    InvokeMember. То же касается CustomDocumentProperties.Add и SaveAs2 --
#    без InvokeMember Word считает имя файла не заданным и открывает диалог
#    «Сохранить как», который при Visible=$false невидим и модален, то есть
#    процесс висит навсегда.
# 2. Word предупреждает при сохранении файла с исправлениями и примечаниями
#    (Options.WarnBeforeSavingPrintingSendingMarkup). Тот же невидимый висяк.
# 3. Файл сценария обязан быть в UTF-8 С BOM: PowerShell 5.1 без BOM читает
#    .ps1 как cp1251 и калечит кириллицу до синтаксической ошибки.
_PS_TEMPLATE = r"""
$ErrorActionPreference = "Stop"
$out = "{out}"
$logf = "{log}"
New-Item -ItemType Directory -Force -Path $out | Out-Null
Set-Content -Path $logf -Value "start" -Encoding UTF8
function L($m) {{ Add-Content -Path $logf -Value $m -Encoding UTF8 }}

function Set-Prop($coll, $name, $value) {{
    $p = [System.__ComObject].InvokeMember("Item", "GetProperty", $null, $coll, @($name))
    [void][System.__ComObject].InvokeMember("Value", "SetProperty", $null, $p, @($value))
}}
function Add-Prop($coll, $name, $value) {{
    [void][System.__ComObject].InvokeMember(
        "Add", "InvokeMethod", $null, $coll, @($name, $false, 4, $value))
}}

$w = New-Object -ComObject Word.Application
$w.Visible = $false
$w.DisplayAlerts = 0
$origUser = $w.UserName
$origInit = $w.UserInitials
$origWarn = $w.Options.WarnBeforeSavingPrintingSendingMarkup
$w.Options.WarnBeforeSavingPrintingSendingMarkup = $false
$w.Options.DoNotPromptForConvert = $true

try {{
    $doc = $w.Documents.Add()
    # Без текста Word не пишет ни статистику, ни rsid, ни время правки.
    $doc.Content.Text = "{body}"

    $bp = $doc.BuiltInDocumentProperties
    Set-Prop $bp "Title" "{title}"
    Set-Prop $bp "Subject" "{subject}"
    Set-Prop $bp "Company" "{company}"
    Set-Prop $bp "Manager" "{manager}"
    Set-Prop $bp "Keywords" "{keywords}"

    $cp = $doc.CustomDocumentProperties
    Add-Prop $cp "{p1name}" "{p1value}"
    Add-Prop $cp "{p2name}" "{p2value}"

    # Два рецензента: их имена ложатся в SttbfRMark у .doc, в w:ins/@w:author
    # у .docx и в \*\revtbl у RTF. Одного мало -- таблица из одной записи не
    # проверяет обход таблицы.
    $doc.TrackRevisions = $true
    $w.UserName = "{rev1}"
    $w.UserInitials = "AA"
    $doc.Range($doc.Content.End - 1, $doc.Content.End - 1).InsertAfter(" Pravka odin.")
    $w.UserName = "{rev2}"
    $w.UserInitials = "BB"
    $doc.Range($doc.Content.End - 1, $doc.Content.End - 1).InsertAfter(" Pravka dva.")
    $doc.Comments.Add($doc.Range(0, 10), "{comment}") | Out-Null
    $doc.TrackRevisions = $false
    L ("revisions=" + $doc.Revisions.Count + " comments=" + $doc.Comments.Count)

    foreach ($pair in @({saves})) {{
        $p = [string](Join-Path $out $pair[0])
        $fmt = [int]$pair[1]
        [void][System.__ComObject].InvokeMember(
            "SaveAs2", "InvokeMethod", $null, $doc, @($p, $fmt))
        L ("saved " + $pair[0] + " " + (Get-Item $p).Length)
    }}
    $doc.Close(0)
    L "OK"
}}
catch {{
    L ("FAIL " + $_.Exception.Message)
}}
finally {{
    try {{
        $w.UserName = $origUser
        $w.UserInitials = $origInit
        $w.Options.WarnBeforeSavingPrintingSendingMarkup = $origWarn
        L "restored"
    }} catch {{ L "FAIL restore" }}
    try {{ $w.Quit() }} catch {{ }}
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($w)
}}
"""


def available() -> bool:
    """Зарегистрирован ли Word.Application в этой системе.

    Возврат: True, если COM-класс Word есть. Проверка по реестру, Word при
    этом не запускается -- запуск стоит секунды и оставляет процесс.
    """
    try:
        import winreg
    except ImportError:
        return False
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Classes\Word.Application\CurVer"):
                return True
        except OSError:
            continue
    return False


def make_word_files(dest: str | None = None, timeout: int = 180) -> dict:
    """Создать настоящим Word четыре файла: .doc, .rtf, .docx, .odt.

    dest: каталог для файлов. None -> новый каталог в %TEMP%. Каталог
        создаётся при необходимости; в репозиторий его класть нельзя (файлы
        несут имя пользователя Word этой машины).
    timeout: сколько секунд ждать Word. Больше минуты нужно потому, что
        первый запуск Office после перезагрузки заметно медленнее.

    Возврат: dict «имя файла -> абсолютный путь». В нём ровно то, что
        действительно создалось и непусто.

    Исключения: Unavailable -- Word не зарегистрирован, powershell не
        запустился, сценарий отработал с ошибкой или не создал ни одного
        файла. Текст исключения содержит только диагностику сценария; имён и
        содержимого созданных файлов в нём нет.

    Побочные эффекты: запускает и закрывает Word; на время работы меняет имя
        пользователя Word и предупреждение о сохранении файла с исправлениями,
        затем возвращает их. Пишет файлы в dest.
    """
    if not available():
        raise Unavailable("Word.Application не зарегистрирован: Office не установлен")
    if dest is None:
        dest = tempfile.mkdtemp(prefix="tgm_real_")
    os.makedirs(dest, exist_ok=True)

    work = tempfile.mkdtemp(prefix="tgm_ps_")
    script = os.path.join(work, "make_word.ps1")
    log = os.path.join(work, "make_word.log")
    saves = ", ".join("@(\"%s\", %d)" % (n, f) for n, f in WORD_FILES)
    body = ("Kvartalnyi otchet po proektu`r`n"
            "Pervyi razdel: tekst nuzhen, chtoby Word zapisal statistiku i rsid.`r`n"
            "Vtoroi razdel: eshche nemnogo teksta dlya obyema.")
    text = _PS_TEMPLATE.format(
        out=dest.replace('"', ''), log=log.replace('"', ''), body=body,
        title=_TITLE, subject=_SUBJECT, company=_COMPANY, manager=_MANAGER,
        keywords=_KEYWORDS, p1name=_PROP_1[0], p1value=_PROP_1[1],
        p2name=_PROP_2[0], p2value=_PROP_2[1], rev1=_REVIEWER_1,
        rev2=_REVIEWER_2, comment=_COMMENT, saves=saves)
    # utf-8-sig: BOM обязателен, см. приём 3 выше.
    with open(script, "w", encoding="utf-8-sig") as fh:
        fh.write(text)

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            timeout=timeout, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise Unavailable("powershell не найден: %s" % type(exc).__name__) from exc
    except subprocess.TimeoutExpired as exc:
        # Висяк почти всегда означает невидимый модальный диалог Word.
        raise Unavailable(
            "Word не ответил за %d с: вероятно, открыт невидимый диалог" % timeout
        ) from exc

    lines = []
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as fh:
            lines = [ln.strip() for ln in fh if ln.strip()]
    made = {}
    for name, _fmt in WORD_FILES:
        p = os.path.join(dest, name)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            made[name] = p
    if not made:
        bad = next((ln for ln in lines if ln.startswith("FAIL")), "")
        raise Unavailable("Word не создал ни одного файла. %s" % (bad or "; ".join(lines[-3:])))
    return made


def _demo():
    """Самопроверка: создать файлы и показать, что инспектор в них видит.

    Печатаются только числа, метки и места: значения из файлов -- настоящие
    персональные данные владельца машины, и выводить их нельзя.
    """
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.inspect import inspect_file

    dest = sys.argv[1] if len(sys.argv) > 1 else None
    if not available():
        print("Word не установлен -- приёмка на настоящих файлах пропущена.")
        print("Это не провал: Office для проекта необязателен.")
        return 0
    try:
        made = make_word_files(dest)
    except Unavailable as exc:
        print("не получилось: %s" % exc)
        return 1

    print("создано файлов: %d" % len(made))
    ok = True
    for name in sorted(made):
        rep = inspect_file(made[name])
        risks = {}
        for f in rep.findings:
            risks[f.risk.value] = risks.get(f.risk.value, 0) + 1
        print("  %-16s fmt=%-5s находок %3d (%s), сигналы: %s, ошибок %d"
              % (name, rep.fmt, len(rep.findings),
                 ", ".join("%s %d" % kv for kv in sorted(risks.items())),
                 ",".join(sorted({s.kind for s in rep.signals})) or "нет",
                 len(rep.errors)))
        if rep.errors:
            ok = False
            print("     ОШИБКИ РАЗБОРА: %d -- настоящий файл обязан разбираться "
                  "без ошибок" % len(rep.errors))
        if not rep.findings:
            ok = False
            print("     НИ ОДНОЙ НАХОДКИ -- для файла от Office это дефект инспектора")
        if name == "word_real.doc":
            marks = [f.value for f in rep.findings if f.label == "Автор правки (SttbfRMark)"]
            # Двое рецензентов заложены сценарием; первая запись таблицы --
            # служебная "Unknown", и выдать её за человека нельзя.
            if len(marks) == 2 and "Unknown" not in marks:
                print("     SttbfRMark: 2 автора, служебная запись отброшена")
            else:
                ok = False
                print("     SttbfRMark: ожидались 2 автора без Unknown, получено %d"
                      % len(marks))
    print("каталог: %s" % (dest or "в %TEMP%, путь не печатаем"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_demo())
