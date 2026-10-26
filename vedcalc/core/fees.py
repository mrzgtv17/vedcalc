"""Шкала таможенных сборов за выпуск товаров (версионируемый JSON в vedcalc/data)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib import resources
from pathlib import Path

from vedcalc.core.money import round_money

DEFAULT_SCALE = "customs_fees_2026.json"


@dataclass(frozen=True, slots=True)
class FeeBracket:
    lower: Decimal | None  # не включительно; None — от нуля
    upper: Decimal | None  # включительно; None — без верхней границы
    fee: Decimal

    @property
    def label(self) -> str:
        def fmt(x: Decimal) -> str:
            return f"{x:,.2f}".replace(",", " ").replace(".", ",")

        if self.lower is None:
            return f"не более {fmt(self.upper)} руб."
        if self.upper is None:
            return f"свыше {fmt(self.lower)} руб."
        return f"свыше {fmt(self.lower)} до {fmt(self.upper)} руб. включительно"


@dataclass(frozen=True, slots=True)
class FeeScale:
    id: str
    valid_from: date
    valid_to: date | None
    legal_basis: str
    source_url: str
    brackets: tuple[FeeBracket, ...]

    def bracket_for(self, customs_value: Decimal) -> FeeBracket:
        """Порог по общей таможенной стоимости (в рублях, с копейками)."""
        cv = round_money(customs_value)
        if cv < 0:
            raise ValueError("Таможенная стоимость не может быть отрицательной")
        for bracket in self.brackets:
            if bracket.upper is None or cv <= bracket.upper:
                return bracket
        raise AssertionError("Шкала сборов без открытого верхнего порога")

    def fee_for(self, customs_value: Decimal) -> Decimal:
        return self.bracket_for(customs_value).fee

    def is_valid_on(self, on: date) -> bool:
        return self.valid_from <= on and (self.valid_to is None or on <= self.valid_to)

    def validity_warning(self, on: date) -> str | None:
        if self.is_valid_on(on):
            return None
        period = f"с {self.valid_from:%d.%m.%Y}"
        if self.valid_to:
            period += f" по {self.valid_to:%d.%m.%Y}"
        return (
            f"Шкала таможенных сборов ({self.legal_basis}) действует {period}, "
            f"а дата расчёта — {on:%d.%m.%Y}. Проверьте актуальность шкалы."
        )


def parse_fee_scale(data: dict) -> FeeScale:
    brackets: list[FeeBracket] = []
    lower: Decimal | None = None
    for raw in data["brackets"]:
        upper = Decimal(raw["up_to"]) if raw["up_to"] is not None else None
        if upper is not None and lower is not None and upper <= lower:
            raise ValueError("Пороги шкалы сборов должны возрастать")
        brackets.append(FeeBracket(lower, upper, Decimal(raw["fee"])))
        lower = upper
    if not brackets or brackets[-1].upper is not None:
        raise ValueError("Последний порог шкалы сборов должен быть открытым (up_to = null)")
    return FeeScale(
        id=data["id"],
        valid_from=date.fromisoformat(data["valid_from"]),
        valid_to=date.fromisoformat(data["valid_to"]) if data.get("valid_to") else None,
        legal_basis=data["legal_basis"],
        source_url=data.get("source_url", ""),
        brackets=tuple(brackets),
    )


def load_fee_scale(path: Path | None = None) -> FeeScale:
    if path is None:
        text = resources.files("vedcalc.data").joinpath(DEFAULT_SCALE).read_text("utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    return parse_fee_scale(json.loads(text))
