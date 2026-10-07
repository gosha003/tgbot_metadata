"""Оценка, выглядит ли файл обработанным. Фаза 3.

Не инспектирует файл заново и не ищет новые метаданные: сигналы уже собрал
инспектор. Здесь они складываются в одну оценку, которую отчёт показывает
после чистки. new_signals диспетчера -- частный случай: какие признаки
появились именно от нашей чистки. Эта оценка смотрит на результат целиком,
включая признаки, которые были и до неё.

Публичный API:
    detect(report) -> (level, reasons)
        level: "none" | "noticeable" | "obvious"
        reasons: короткие строки для отчёта, без значений из файла

Исключений не бросает.
"""

from __future__ import annotations

__all__ = ["detect"]

# ai хуже scrubbed: файл выглядит не чищеным, а собранным программой.
# inconsistent сам по себе не значит «чистили» -- это противоречие в данных.
_OBVIOUS = "obvious"
_NOTICE = "noticeable"
_NONE = "none"


def detect(report) -> tuple:
    """Сигналы отчёта -> насколько файл выглядит обработанным.

    report: Report или любой объект с .signals (элементы с .kind).
    Возврат: (level, reasons). level -- none / noticeable / obvious.
    reasons -- список пояснений без значений из файла.
    При любом сбое: ("none", []).
    """
    try:
        signals = list(getattr(report, "signals", None) or [])
    except Exception:
        return _NONE, []
    kinds = set()
    edited = 0
    for s in signals:
        kind = getattr(s, "kind", None)
        if kind is None and isinstance(s, (tuple, list)) and s:
            kind = s[0]
        if kind:
            kinds.add(kind)
        # Противоречие с высокой уверенностью («ModDate раньше CreationDate»,
        # «Aptos в документе 2019 года») само говорит о ручной правке. Слабые
        # (created == modified) бывают у честных файлов -- их не считаем.
        if kind == "inconsistent" and getattr(s, "confidence", "") == "high":
            edited += 1
    reasons = []
    if "ai" in kinds:
        reasons.append(
            "Инспектор считает файл собранным библиотекой или генератором, "
            "а не сохранённым в офисном приложении.")
    if "scrubbed" in kinds:
        reasons.append(
            "Есть признаки чистки: пустые поля личности или контейнер, "
            "который так не пишет заявленное приложение.")
    if edited:
        reasons.append(
            "Метаданные противоречат сами себе (%d): даты, версии или шрифты "
            "не сходятся так, как бывает только после ручной правки." % edited)
    if "ai" in kinds:
        return _OBVIOUS, reasons
    if "scrubbed" in kinds or edited:
        return _NOTICE, reasons
    return _NONE, []


def _selftest():
    from types import SimpleNamespace

    def rep(*kinds):
        return SimpleNamespace(signals=[SimpleNamespace(kind=k, detail="x") for k in kinds])

    assert detect(rep()) == ("none", [])
    level, why = detect(rep("scrubbed", "producer"))
    assert level == "noticeable" and why, why
    level, why = detect(rep("ai", "scrubbed"))
    assert level == "obvious" and len(why) == 2
    level, _why = detect(rep("inconsistent"))
    assert level == "none"
    strong = SimpleNamespace(signals=[SimpleNamespace(kind="inconsistent", detail="x", confidence="high")])
    level, why = detect(strong)
    assert level == "noticeable" and len(why) == 1, (level, why)
    weak = SimpleNamespace(signals=[SimpleNamespace(kind="inconsistent", detail="x", confidence="low")])
    assert detect(weak) == ("none", [])
    assert detect(None) == ("none", [])
    print("OK")


if __name__ == "__main__":
    _selftest()
