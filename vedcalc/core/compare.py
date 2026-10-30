"""Сравнение вариантов расчёта («море или ж/д», «Китай или Турция») по статьям.

Каждый вариант — полный расчёт: себестоимость импорта по своим курсам на свою дату
и цена продажи по своему сценарию. Разница — к первому варианту.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from vedcalc.core.basis import basis_from_cost, basis_from_manual
from vedcalc.core.costing import CostResult, calculate
from vedcalc.core.customs import NotSupportedError
from vedcalc.core.fees import FeeScale
from vedcalc.core.fx import FxError, RateProvider, resolve_rates
from vedcalc.core.models import PriceMode, PricingSource, Shipment
from vedcalc.core.pricing import PriceResult
from vedcalc.core.report import build_price_report
from vedcalc.core.validation import InputError

MAX_VARIANTS = 4


class Better(StrEnum):
    LOW = "low"  # меньше — лучше (расходы)
    HIGH = "high"  # больше — лучше (прибыль)


class Kind(StrEnum):
    MONEY = "money"
    PCT = "pct"  # доля (0,25 = 25 %)
    NUMBER = "number"


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    shipment: Shipment
    cost: CostResult | None = None
    price: PriceResult | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class CompareRow:
    section: str
    label: str
    values: tuple[Decimal | None, ...]
    kind: Kind = Kind.MONEY
    better: Better | None = None
    strong: bool = False

    def delta(self, k: int) -> Decimal | None:
        """Разница варианта k с первым; None — нечего сравнивать."""
        base, value = self.values[0], self.values[k]
        if k == 0 or base is None or value is None:
            return None
        return value - base

    def verdict(self, k: int) -> int:
        """+1 — вариант k выгоднее первого, −1 — хуже, 0 — так же или не сравнить."""
        d = self.delta(k)
        if d is None or not d or self.better is None:
            return 0
        sign = 1 if d > 0 else -1
        return sign if self.better is Better.HIGH else -sign


@dataclass(frozen=True, slots=True)
class Comparison:
    names: tuple[str, ...]
    rows: tuple[CompareRow, ...]
    errors: tuple[tuple[str, str], ...]  # (вариант, ошибка)
    notes: tuple[str, ...]


def evaluate(
    name: str, shipment: Shipment, provider: RateProvider | None, fee_scale: FeeScale
) -> Variant:
    """Расчёт варианта; ошибка ввода, курсов или цены — в Variant.error."""
    try:
        cost = None
        if shipment.pricing_source is PricingSource.MANUAL:
            basis = basis_from_manual(shipment.manual_cost, shipment.sale)
        else:
            rates = resolve_rates(
                shipment.currencies(), shipment.calc_date, provider, shipment.manual_rates
            )
            cost = calculate(shipment, rates, fee_scale)
            basis = basis_from_cost(cost, shipment.sale)
        report = build_price_report(basis, shipment.financing, name)
    except (InputError, FxError, NotSupportedError) as exc:
        return Variant(name, shipment, error=str(exc))
    return Variant(name, shipment, cost, report.target, report.target_error)


def _single_unit(v: Variant) -> tuple[Decimal, str] | None:
    """Количество и единица, если в варианте одна позиция."""
    s = v.shipment
    if s.pricing_source is PricingSource.MANUAL:
        return s.manual_cost.quantity, s.manual_cost.unit.label
    if len(s.items) == 1:
        return s.items[0].quantity, s.items[0].unit.label
    return None


def compare(variants: list[Variant]) -> Comparison:
    names = tuple(v.name for v in variants)
    rows: list[CompareRow] = []

    def add(section, label, values, kind=Kind.MONEY, better=None, strong=False, *, skip_zero=False):
        values = tuple(values)
        if all(x is None for x in values):
            return
        if skip_zero and all(not x for x in values):
            return
        rows.append(CompareRow(section, label, values, kind, better, strong))

    def cost_attr(attr: str):
        return [getattr(v.cost, attr) if v.cost else None for v in variants]

    def item_sum(attr: str):
        return [sum((getattr(i, attr) for i in v.cost.items), Decimal(0)) if v.cost else None
                for v in variants]  # fmt: skip

    imp = "Себестоимость импорта"
    add(imp, "Стоимость товара", cost_attr("goods"), better=Better.LOW)
    add(imp, "Расходы до границы (в таможенной стоимости)", item_sum("costs_in_cv"),
        better=Better.LOW)  # fmt: skip
    add(imp, "Таможенная стоимость", cost_attr("customs_value"))
    add(imp, "Пошлина", cost_attr("duty"), better=Better.LOW)
    add(imp, "Акциз", cost_attr("excise"), better=Better.LOW, skip_zero=True)
    add(imp, "Таможенный сбор", cost_attr("customs_fee"), better=Better.LOW)
    add(imp, "НДС при ввозе", cost_attr("import_vat"), better=Better.LOW)
    add(imp, "Прочие расходы (вне таможенной стоимости)", item_sum("costs_other"),
        better=Better.LOW)  # fmt: skip

    def cost_net(v: Variant) -> Decimal | None:
        if v.cost is not None:
            return v.cost.cost_net
        if v.price is not None:
            return v.price.cost_net
        return None

    add(imp, "Себестоимость партии без НДС", [cost_net(v) for v in variants],
        better=Better.LOW, strong=True)  # fmt: skip
    add(imp, "НДС к вычету", [v.cost.deductible_vat if v.cost else None for v in variants],
        skip_zero=True)  # fmt: skip

    units = [_single_unit(v) for v in variants]
    same_unit = all(u is not None for u in units) and len({u[1] for u in units}) == 1
    if same_unit:
        label = units[0][1]
        add(imp, f"Количество, {label}", [u[0] for u in units], Kind.NUMBER)
        add(imp, f"Себестоимость 1 {label} без НДС",
            [c / u[0] if c is not None and u[0] else None
             for c, u in zip((cost_net(v) for v in variants), units, strict=True)],
            better=Better.LOW, strong=True)  # fmt: skip

    sale = "Цена продажи"
    prices = [v.price for v in variants]
    add(sale, "Цена партии без НДС", [p.price_net if p else None for p in prices])
    if same_unit:
        add(sale, f"Цена 1 {units[0][1]} без НДС",
            [p.price_net / u[0] if p and u[0] else None
             for p, u in zip(prices, units, strict=True)])  # fmt: skip
    add(sale, "Стоимость денег", [p.financing if p else None for p in prices],
        better=Better.LOW, skip_zero=True)  # fmt: skip
    add(sale, "Чистая прибыль партии", [p.net_profit if p else None for p in prices],
        better=Better.HIGH, strong=True)  # fmt: skip
    add(sale, "Маржинальность", [p.margin if p else None for p in prices], Kind.PCT,
        Better.HIGH)  # fmt: skip
    add(sale, "Наценка", [p.markup if p else None for p in prices], Kind.PCT, Better.HIGH)

    notes = []
    dates = {v.shipment.calc_date for v in variants if v.cost}
    if len(dates) > 1:
        notes.append("Даты расчёта разные — курсы и шкала сборов взяты на дату каждого варианта.")
    regimes = {v.shipment.tax_regime for v in variants if v.cost}
    if len(regimes) > 1:
        notes.append("Налоговые режимы разные: при ОСН суммы без НДС, при УСН — с НДС.")
    modes = {(v.shipment.sale.target_mode, v.shipment.sale.target_value) for v in variants}
    if len(modes) > 1:
        notes.append("Сценарии цены разные — прибыль сравнивается при разных ценах.")
    elif modes and next(iter(modes))[0] in (PriceMode.MARKUP, PriceMode.MARGIN):
        notes.append(
            "Цена считается от себестоимости (наценка или маржа), поэтому у дорогого варианта "
            "и цена выше. Чтобы сравнить прибыль при одной цене, задайте сценарий «Цена партии»."
        )
    errors = tuple((v.name, v.error) for v in variants if v.error)
    return Comparison(names, tuple(rows), errors, tuple(notes))
