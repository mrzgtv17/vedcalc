"""Денежные примитивы: только Decimal, округление до копеек ROUND_HALF_UP."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

KOPECK = Decimal("0.01")
ZERO = Decimal("0")
HUNDRED = Decimal("100")
RUB = "RUB"


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Приводит значение к Decimal. float запрещён: он теряет копейки."""
    if isinstance(value, (bool, float)):
        raise TypeError(f"Ожидался Decimal, int или str, получен {type(value).__name__}")
    if isinstance(value, Decimal):
        return value
    return Decimal(value)


def round_money(value: Decimal) -> Decimal:
    """Округление до копеек по правилам математики (ROUND_HALF_UP)."""
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def ceil_money(value: Decimal) -> Decimal:
    """Округление до копеек вверх — для безубыточной цены, чтобы прибыль не ушла в минус."""
    return value.quantize(KOPECK, rounding=ROUND_CEILING)


def pct(rate_percent: Decimal) -> Decimal:
    """22 → 0.22"""
    return rate_percent / HUNDRED


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str = RUB

    def __post_init__(self) -> None:
        object.__setattr__(self, "amount", to_decimal(self.amount))
        object.__setattr__(self, "currency", self.currency.upper())

    @classmethod
    def rub(cls, amount: Decimal | int | str) -> Money:
        return cls(to_decimal(amount), RUB)

    def __add__(self, other: Money) -> Money:
        self._check_same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check_same(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, k: Decimal | int) -> Money:
        return Money(self.amount * to_decimal(k), self.currency)

    __rmul__ = __mul__

    def _check_same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"Нельзя складывать {self.currency} и {other.currency} без конвертации"
            )


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Делит сумму (в копейках) пропорционально весам.

    Каждая доля округляется до копеек, остаток от округления уходит последней позиции
    с ненулевым весом — сумма долей всегда точно равна total.
    """
    if not weights:
        return []
    total_weight = sum(weights, ZERO)
    if total_weight <= 0:
        raise ValueError("Сумма весов для распределения должна быть больше нуля")
    shares = [round_money(total * w / total_weight) for w in weights]
    last = max(i for i, w in enumerate(weights) if w > 0)
    shares[last] += round_money(total) - sum(shares, ZERO)
    return shares
