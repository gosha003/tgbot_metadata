"""Общий контракт для всех инспекторов метаданных.

Только stdlib. Все модули core/insp_*.py импортируют отсюда и возвращают Report.

Фаза 0 проекта: инспекция read-only. Ничего не чистим, ничего не пишем в
исходный файл. Задача — собрать полную карту того, что в файле течёт, чтобы
правила чистки (фазы 2-3) писались на фактах, а не на догадках.
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum

MAX_VALUE_LEN = 400

# Уверенность сигнала по возрастанию: нужна при слиянии дубликатов.
CONF_RANK = {"low": 0, "medium": 1, "high": 2}

# Абсолютные пути в НАШИХ сообщениях об ошибках. Исключения парсеров
# (особенно pikepdf) печатают полный путь к временному файлу, и он уезжает
# пользователю в чат -- инструмент приватности сливает пути своей машины.
# Матчим до двоеточия или конца строки, а не до пробела: иначе путь с
# пробелом в имени обрезается и вторая половина всё равно утекает.
_ABS_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|/(?:home|Users|root|tmp|var|private|mnt|media)/)"
    r"[^:\r\n]*"
)
PATH_STUB = "<путь скрыт>"


class Risk(str, Enum):
    """Категория находки. Определяет и цвет в отчёте, и профиль чистки."""

    IDENTITY = "identity"        # ФИО, email, GUID тенанта, серийники, подписанты
    ENVIRONMENT = "environment"  # локальные пути, хостнеймы, принтеры, серверы БД
    PROVENANCE = "provenance"    # приложение, версия, даты, время правки, генератор
    STRUCTURAL = "structural"    # то, что обычно НУЖНО сохранить: язык, теги, ICC


RISK_ICON = {
    Risk.IDENTITY: "\U0001F534",      # красный круг
    Risk.ENVIRONMENT: "\U0001F7E0",   # оранжевый круг
    Risk.PROVENANCE: "\U0001F7E1",    # жёлтый круг
    Risk.STRUCTURAL: "⚪",        # белый круг
}

RISK_TITLE = {
    Risk.IDENTITY: "Личность и PII",
    Risk.ENVIRONMENT: "Окружение и пути",
    Risk.PROVENANCE: "Провенанс и приложение",
    Risk.STRUCTURAL: "Структурное (обычно сохраняем)",
}

# Порядок вывода в отчёте: от самого опасного к безобидному.
RISK_ORDER = [Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE, Risk.STRUCTURAL]


def clip(value, limit: int = MAX_VALUE_LEN) -> str:
    """Значение находки в печатаемый вид: одна строка, ограниченная длина.

    Значения приходят из недоверенных файлов, поэтому переводы строк и
    управляющие символы вырезаются -- иначе они ломают вывод отчёта.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value)
    text = "".join(" " if ch in "\r\n\t" else ch for ch in text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


@dataclasses.dataclass
class Finding:
    """Одна конкретная утечка: где лежит, как называется, что содержит."""

    risk: Risk
    location: str          # "docProps/core.xml" / "/Info" / "APP1:Exif" / "settings.xml"
    label: str             # человекочитаемое имя поля: "Автор", "TotalTime"
    value: str             # само значение (обрезанное)
    note: str = ""         # почему это важно
    removable: bool = True # False => убрать нельзя, см. §5 анализа

    def __post_init__(self) -> None:
        self.value = clip(self.value)
        self.risk = Risk(self.risk)

    @property
    def empty(self) -> bool:
        """Поле присутствует, но пустое. Само по себе информативно."""
        return self.value == ""


@dataclasses.dataclass
class Signal:
    """Вывод, а не сырое значение: 'это писал openpyxl', 'файл уже чистили'."""

    kind: str          # producer | ai | scrubbed | inconsistent | hazard
    detail: str
    confidence: str = "medium"   # high | medium | low

    def __post_init__(self) -> None:
        self.detail = clip(self.detail, 600)


@dataclasses.dataclass
class Report:
    """Результат инспекции одного файла."""

    path: str
    fmt: str                      # ключ из sniff.py: "docx", "pdf", "jpeg", "doc" ...
    size: int = 0
    findings: list = dataclasses.field(default_factory=list)
    signals: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)
    parts: list = dataclasses.field(default_factory=list)   # части контейнера (ZIP/OLE)

    # --- хелперы, чтобы инспекторы были короткими -------------------------

    def add(self, risk, location, label, value, note="", removable=True):
        """Добавить находку. Пустые значения НЕ отбрасываются: пустой
        dc:creator -- это тоже факт (признак либо чистки, либо стокового
        приложения без имени пользователя). Фильтрует уже report.py."""
        self.findings.append(
            Finding(Risk(risk), location, label, value, note, removable)
        )
        return self.findings[-1]

    def add_nonempty(self, risk, location, label, value, note="", removable=True):
        """То же, но молча пропустить, если значения нет."""
        if value is None or clip(value) == "":
            return None
        return self.add(risk, location, label, value, note, removable)

    def signal(self, kind, detail, confidence="medium"):
        """Добавить вывод. Дубликаты отбрасываются.

        Инспекторы ставят сигналы внутри циклов -- по страницам, по шрифтам,
        по частям пакета. Без этой защиты один и тот же вывод попадает в
        отчёт десятки раз: «Calibri -- шрифт по умолчанию Office 2007-2023»
        повторялся по разу на страницу и вытеснял остальные выводы.
        При повторе сохраняем более высокую уверенность из двух.
        """
        fresh = Signal(kind, detail, confidence)
        for prev in self.signals:
            if prev.kind == fresh.kind and prev.detail == fresh.detail:
                if CONF_RANK.get(fresh.confidence, 0) > CONF_RANK.get(prev.confidence, 0):
                    prev.confidence = fresh.confidence
                return prev
        self.signals.append(fresh)
        return fresh

    def err(self, message):
        """Ошибка разбора. Инспектор НИКОГДА не должен падать целиком:
        недоступная часть файла -- это запись в errors, а не исключение.

        Абсолютные пути вырезаются. Текст исключения от парсера содержит
        полный путь к файлу на НАШЕЙ машине, а отчёт уходит пользователю в
        чат: иначе инструмент приватности сам сливает то, что ищет в чужих
        файлах. Вырезается и базовое имя тоже -- имя файла это метаданные.

        Скраб только здесь и НЕ в add(): путь, найденный ВНУТРИ чужого
        файла, -- это легитимная находка категории ENVIRONMENT, её надо
        показывать, а не скрывать.
        """
        self.errors.append(clip(_ABS_PATH.sub(PATH_STUB, str(message)), 600))

    # --- агрегаты для отчёта ---------------------------------------------

    def by_risk(self) -> dict:
        out = {r: [] for r in RISK_ORDER}
        for f in self.findings:
            out.setdefault(f.risk, []).append(f)
        return out

    def count(self, *risks) -> int:
        if not risks:
            return len(self.findings)
        wanted = {Risk(r) for r in risks}
        return sum(1 for f in self.findings if f.risk in wanted)

    @property
    def sensitive(self) -> int:
        """Сколько находок реально требуют чистки (без STRUCTURAL).

        Это метрика для фазы чистки: провенанс тоже чистится.
        Для сводки пользователю нужен critical, а не это число.
        """
        return self.count(Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE)

    @property
    def critical(self) -> int:
        """Находки, которые прямо выдают человека или машину.

        Намеренно НЕ равно sensitive. Провенанс (приложение, версия, даты,
        шрифты) чистить тоже надо, но его бывают сотни строк: у реального
        PDF -- 130 записей вроде «Версия PDF = 1.7» и списка шрифтов по
        страницам при семи настоящих утечках. Назвать критичными все 137
        значит утопить эти семь. Сводка и шапка отчёта считают по этому
        полю, фаза чистки -- по sensitive.
        """
        return self.count(Risk.IDENTITY, Risk.ENVIRONMENT)
