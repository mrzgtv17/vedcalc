"""Пошаговая расшифровка расчёта и форматирование чисел для людей."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.money import round_money


def fmt_money(value: Decimal) -> str:
    """147731 → «147 731,00» (неразрывный пробел между разрядами)."""
    return f"{round_money(value):,.2f}".replace(",", " ").replace(".", ",")


def fmt_number(value: Decimal) -> str:
    """Количество без лишних нулей: 2.500 → «2,5», 1000 → «1\u00a0000»."""
    text = f"{value:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    whole, _, frac = text.partition(".")
    whole = f"{int(whole):,}".replace(",", "\u00a0") if whole.lstrip("-").isdigit() else whole
    return whole + ("," + frac if frac else "")


def fmt_pct(value: Decimal | None, places: int = 2) -> str:
    """Доля 0.3538… → «35,38%»."""
    if value is None:
        return "—"
    return f"{value * 100:.{places}f}%".replace(".", ",")


def fmt_rate(value: Decimal) -> str:
    """Ставка в процентах 22 → «22%», 7.5 → «7,5%»."""
    return f"{value.normalize():f}%".replace(".", ",")


@dataclass(frozen=True, slots=True)
class ExplainStep:
    section: str  # «Таможенные платежи», «Себестоимость», «Цена»…
    title: str
    formula: str  # формула с подставленными числами
    value: Decimal | None = None


class Explainer:
    def __init__(self) -> None:
        self.steps: list[ExplainStep] = []

    def add(self, section: str, title: str, formula: str, value: Decimal | None = None) -> None:
        self.steps.append(ExplainStep(section, title, formula, value))
