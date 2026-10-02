"""Контракт чистки (фазы 1-4). Инспекция -- в model.py, это её продолжение.

Главная мысль, из которой выведено всё остальное: **инспектор является
оракулом для чистильщика**. Проверка «почистили ли» -- это не отдельная
логика, а повторный прогон inspect_file() по результату:

    было = inspect_file(src)
    res  = clean_file(src, dst, Profile.PARANOID)
    стало = inspect_file(dst)
    assert стало.critical == 0

Поэтому фаза 0 делалась первой: без работающего инспектора у чистки нет
способа доказать, что она сработала.

Только stdlib.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

from core.model import Risk, clip


class Profile(str, Enum):
    """Стратегия чистки. Подробности и обоснование -- docs/ROADMAP.md."""

    PARANOID = "paranoid"
    """Убрать всё, кроме явного whitelist. Файл гарантированно чист, но
    очевидно обработан: у настоящего docx из Word всегда есть app.xml с
    Application, и его отсутствие читается как след инструмента чистки.
    Правильный выбор, когда важна чистота, а не незаметность."""

    STEALTH = "stealth"
    """Дефолт. Не обнулять, а приводить к виду стокового приложения без
    заполненного имени пользователя -- рядовое, ничем не выделяющееся
    состояние, которое встречается в природе.

    Что это значит на практике:
      * личность и окружение (IDENTITY, ENVIRONMENT) -- убрать;
      * приложение и его версию -- ОСТАВИТЬ как есть: это правда, файл
        действительно сделан в этом приложении, а несогласованная пара
        вида Creator=Word + Producer=qpdf детектируется мгновенно;
      * даты -- сохранить правдоподобный зазор между созданием и
        изменением, а не обнулять в одну секунду;
      * структурное (STRUCTURAL) -- сохранить: язык, теги доступности,
        ICC-профиль, оглавление."""

    REGENERATE = "regenerate"
    """Пересоздание через стоковое приложение (LibreOffice). Фаза 4."""


# Какие категории риска профиль убирает. STRUCTURAL не убирает никто:
# это то, что при чистке нужно сохранить, иначе ломается доступность и цвет.
PROFILE_REMOVES = {
    Profile.PARANOID: (Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE),
    Profile.STEALTH: (Risk.IDENTITY, Risk.ENVIRONMENT),
    Profile.REGENERATE: (Risk.IDENTITY, Risk.ENVIRONMENT, Risk.PROVENANCE),
}


class Act(str, Enum):
    """Что именно сделали с полем."""

    REMOVED = "removed"        # поля больше нет
    BLANKED = "blanked"        # поле есть, значение пустое (вид стокового приложения)
    NORMALIZED = "normalized"  # значение заменено на правдоподобное
    REGENERATED = "regenerated"  # значение перегенерировано (например trailer /ID)
    KEPT = "kept"              # сохранено намеренно: whitelist или STRUCTURAL
    UNREMOVABLE = "unremovable"  # убрать нельзя, см. §5 анализа
    FAILED = "failed"          # попытались и не смогли


ACT_TITLE = {
    Act.REMOVED: "удалено",
    Act.BLANKED: "обнулено",
    Act.NORMALIZED: "нормализовано",
    Act.REGENERATED: "перегенерировано",
    Act.KEPT: "сохранено намеренно",
    Act.UNREMOVABLE: "убрать невозможно",
    Act.FAILED: "не удалось",
}


@dataclasses.dataclass
class CleanAction:
    """Одно изменение. Отчёт о чистке строится из них."""

    act: Act
    location: str
    label: str
    before: str = ""
    after: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        self.act = Act(self.act)
        self.before = clip(self.before)
        self.after = clip(self.after)


@dataclasses.dataclass
class CleanResult:
    """Результат чистки одного файла.

    ok == True означает, что выходной файл записан И открывается своим
    парсером. Чистильщик обязан проверить это сам: отдать пользователю
    «почищенный» файл, который не открывается, хуже, чем не чистить.
    """

    src: str
    dst: str
    fmt: str
    profile: str
    ok: bool = False
    actions: list = dataclasses.field(default_factory=list)
    errors: list = dataclasses.field(default_factory=list)

    # Заполняет диспетчер повторной инспекцией. Доказательство результата.
    critical_before: int = -1
    critical_after: int = -1
    sensitive_before: int = -1
    sensitive_after: int = -1

    # --- хелперы ---------------------------------------------------------

    def act(self, act, location, label, before="", after="", note=""):
        self.actions.append(CleanAction(Act(act), location, label, before, after, note))
        return self.actions[-1]

    def err(self, message):
        """Как и в Report.err(): абсолютные пути вырезаются, потому что
        отчёт уходит пользователю в чат."""
        from core.model import _ABS_PATH, PATH_STUB

        self.errors.append(clip(_ABS_PATH.sub(PATH_STUB, str(message)), 600))

    def count(self, *acts) -> int:
        if not acts:
            return len(self.actions)
        wanted = {Act(a) for a in acts}
        return sum(1 for a in self.actions if a.act in wanted)

    @property
    def changed(self) -> int:
        return self.count(Act.REMOVED, Act.BLANKED, Act.NORMALIZED, Act.REGENERATED)

    @property
    def clean(self) -> bool:
        """Утечки личности и окружения не осталось -- по повторной инспекции,
        а не по нашему же списку действий. Если диспетчер не проверял,
        critical_after == -1 и утверждать чистоту мы не имеем права."""
        return self.critical_after == 0

    def by_act(self) -> dict:
        out = {}
        for a in self.actions:
            out.setdefault(a.act, []).append(a)
        return out
