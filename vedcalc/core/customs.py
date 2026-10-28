"""Таможенные платежи: пошлина, НДС при ввозе. Суммы платежей — в копейках.

Пошлина бывает:
- адвалорная — ТС × X%;
- специфическая — Y EUR (USD) за единицу × количество × курс ЦБ;
- комбинированная «X%, но не менее Y EUR за единицу» — большая из двух;
- «X% плюс Y EUR за единицу» — сумма двух.
Количество для специфической части: за кг / т — вес нетто; за шт — количество позиции;
за л, пары, м², см³ — «количество в единице ставки», которое указывает пользователь.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.explain import fmt_money, fmt_number, fmt_rate
from vedcalc.core.models import DutyKind, DutyRate, DutyUnit, Item
from vedcalc.core.money import ZERO, pct, round_money
from vedcalc.core.units import Unit


class NotSupportedError(NotImplementedError):
    pass


class DutyQuantityError(ValueError):
    """Для специфической ставки не хватает количества (веса нетто, литров…)."""


def duty_base_quantity(duty: DutyRate, item: Item) -> tuple[Decimal, str]:
    """Количество в единице специфической ставки и откуда оно взято (для формулы)."""
    unit = duty.specific_unit
    if unit is None:
        raise DutyQuantityError("у специфической ставки не указана единица")
    if unit.by_weight:
        if item.net_weight_kg is not None:
            kg, source = item.net_weight_kg, "вес нетто"
        elif item.unit.is_mass:
            kg = item.quantity * (1000 if item.unit is Unit.T else 1)
            source = "количество"
        else:
            raise DutyQuantityError("для ставки за кг укажите вес нетто позиции")
        return (kg / 1000 if unit is DutyUnit.T else kg), source
    if unit is DutyUnit.PCS and item.unit is Unit.PCS:
        return item.quantity, "количество"
    if item.duty_quantity is None:
        raise DutyQuantityError(
            f"для ставки за {unit.label} укажите количество в {unit.label} "
            "(колонка «Кол-во в ед. ставки»)"
        )
    return item.duty_quantity, "кол-во в ед. ставки"


@dataclass(frozen=True, slots=True)
class DutyCalc:
    amount: Decimal
    formula: str


def duty_amount(
    duty: DutyRate,
    customs_value: Decimal,
    item: Item | None = None,
    rate_to_rub: Decimal | None = None,
) -> Decimal:
    return duty_calc(duty, customs_value, item, rate_to_rub).amount


def duty_calc(
    duty: DutyRate,
    customs_value: Decimal,
    item: Item | None = None,
    rate_to_rub: Decimal | None = None,
) -> DutyCalc:
    """Пошлина и формула с подставленными числами."""
    adval = round_money(customs_value * pct(duty.percent))
    adval_txt = f"{fmt_money(customs_value)} × {fmt_rate(duty.percent)}"
    if duty.kind is DutyKind.AD_VALOREM:
        return DutyCalc(adval, f"{adval_txt} = {fmt_money(adval)}")
    if item is None or rate_to_rub is None or duty.specific is None:
        raise NotSupportedError("Для специфической ставки нужны позиция и курс валюты")
    qty, source = duty_base_quantity(duty, item)
    spec = round_money(duty.specific.amount * qty / duty.per_quantity * rate_to_rub)
    per = f" / {fmt_number(duty.per_quantity)}" if duty.per_quantity != 1 else ""
    spec_txt = (
        f"{fmt_number(duty.specific.amount)} {duty.specific.currency} × "
        f"{fmt_number(qty)} {duty.specific_unit.label} ({source}){per} × "
        f"{fmt_number(rate_to_rub)} ₽"
    )
    if duty.kind is DutyKind.SPECIFIC:
        return DutyCalc(spec, f"{spec_txt} = {fmt_money(spec)}")
    if duty.kind is DutyKind.COMBINED:
        amount = max(adval, spec)
        return DutyCalc(
            amount,
            f"большая из: {adval_txt} = {fmt_money(adval)}; "
            f"{spec_txt} = {fmt_money(spec)} → {fmt_money(amount)}",
        )
    amount = adval + spec
    return DutyCalc(
        amount, f"{adval_txt} + {spec_txt} = {fmt_money(adval)} + {fmt_money(spec)} = "
        f"{fmt_money(amount)}"
    )  # fmt: skip


def import_vat(customs_value: Decimal, duty: Decimal, excise: Decimal, rate: Decimal) -> Decimal:
    """НДС при ввозе = (ТС + пошлина + акциз) × ставка НДС. Сбор в базу не входит."""
    return round_money((customs_value + duty + excise) * pct(rate))


__all__ = [
    "ZERO",
    "DutyCalc",
    "DutyQuantityError",
    "NotSupportedError",
    "duty_amount",
    "duty_base_quantity",
    "duty_calc",
    "import_vat",
]
