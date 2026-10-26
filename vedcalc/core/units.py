"""Единицы измерения количества и конвертация."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class Unit(StrEnum):
    PCS = "pcs"
    KG = "kg"
    T = "t"

    @property
    def label(self) -> str:
        return {Unit.PCS: "шт", Unit.KG: "кг", Unit.T: "т"}[self]

    @property
    def is_mass(self) -> bool:
        return self in (Unit.KG, Unit.T)


_KG_PER = {Unit.KG: Decimal("1"), Unit.T: Decimal("1000")}


def convert(quantity: Decimal, src: Unit, dst: Unit) -> Decimal:
    """Конвертирует количество между единицами одной природы (масса ↔ масса)."""
    if src == dst:
        return quantity
    if src.is_mass and dst.is_mass:
        return quantity * _KG_PER[src] / _KG_PER[dst]
    raise ValueError(f"Нельзя перевести {src.label} в {dst.label}")
