"""«Что если»: чувствительность себестоимости и прибыли к курсу, фрахту и пошлине.

Расчёт импорта повторяется с изменённым курсом (все валюты партии — на один процент),
фрахтом (расходы «доставка до границы» и «доставка по РФ») или ставкой пошлины.
Цена продажи при этом не меняется — она уже согласована с клиентом; меняются
себестоимость, прибыль и маржа.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from decimal import Decimal

from vedcalc.core.basis import basis_from_cost
from vedcalc.core.costing import calculate
from vedcalc.core.fees import FeeScale, load_fee_scale
from vedcalc.core.fx import RUB, ResolvedRates
from vedcalc.core.models import CostKind, DutyKind, DutyRate, Shipment
from vedcalc.core.money import HUNDRED, ZERO, round_money
from vedcalc.core.pricing import evaluate
from vedcalc.core.report import PriceReport
from vedcalc.core.validation import InputError

ONE = Decimal(1)
FX_STEPS = (-15, -10, -5, 0, 5, 10, 15)  # изменение курса, %
FREIGHT_STEPS = (-20, -10, 0, 10, 20)  # изменение фрахта, %
DUTY_STEPS = (-5, 0, 5)  # изменение ставки пошлины, п. п.
FREIGHT_KINDS = frozenset({CostKind.DELIVERY_TO_BORDER, CostKind.DELIVERY_RF})


@dataclass(frozen=True, slots=True)
class Scenario:
    label: str
    fx_pct: Decimal = ZERO
    freight_pct: Decimal = ZERO
    duty_pp: Decimal = ZERO
    duty_custom: Decimal | None = None  # своя адвалорная ставка для всех позиций, %

    @property
    def is_base(self) -> bool:
        return not (self.fx_pct or self.freight_pct or self.duty_pp) and self.duty_custom is None


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    scenario: Scenario
    cost_net: Decimal  # себестоимость партии
    unit_cost: Decimal | None  # себестоимость единицы — если позиция одна
    net_profit: Decimal
    margin: Decimal | None  # маржинальность: валовая прибыль / цена без НДС (доля)
    profit_delta: Decimal  # к текущему расчёту


@dataclass(frozen=True, slots=True)
class Table:
    title: str
    note: str
    rows: tuple[ScenarioResult, ...]


@dataclass(frozen=True, slots=True)
class WhatIf:
    price_net: Decimal  # цена партии без НДС — неизменная
    unit_label: str | None
    tables: tuple[Table, ...]
    notes: tuple[str, ...] = ()

    @property
    def cost_header(self) -> str:
        return f"Себестоимость 1 {self.unit_label}" if self.unit_label else "Себестоимость партии"

    @property
    def heads(self) -> tuple[str, ...]:
        return ("Сценарий", self.cost_header, "Чистая прибыль", "Маржинальность", "К текущему")

    @property
    def scope_text(self) -> str:
        if self.unit_label:
            return f"Себестоимость — за 1 {self.unit_label}, прибыль и маржа — по партии."
        return "Себестоимость, прибыль и маржа — по партии."


def _rates(rates: ResolvedRates, fx_pct: Decimal) -> ResolvedRates:
    k = ONE + fx_pct / HUNDRED
    scaled = {
        code: r if code == RUB else replace(r, value=r.value * k) for code, r in rates.rates.items()
    }
    return ResolvedRates(scaled, rates.warnings)


def _duty(rate: DutyRate, pp: Decimal) -> DutyRate:
    if rate.kind is DutyKind.SPECIFIC:
        return rate  # только евро за единицу — процентной части нет
    return replace(rate, percent=max(ZERO, rate.percent + pp))


def _shipment(base: Shipment, sc: Scenario) -> Shipment:
    s = copy.deepcopy(base)
    if sc.freight_pct:
        k = ONE + sc.freight_pct / HUNDRED
        for line in s.costs:
            if line.kind in FREIGHT_KINDS:
                line.amount = replace(line.amount, amount=line.amount.amount * k)
        if s.freight.rate is not None:
            s.freight.rate = replace(s.freight.rate, amount=s.freight.rate.amount * k)
    for item in s.items:
        if sc.duty_custom is not None:
            item.duty = DutyRate.ad_valorem(sc.duty_custom)
        elif sc.duty_pp:
            item.duty = _duty(item.duty, sc.duty_pp)
    return s


def run_scenario(report: PriceReport, sc: Scenario, fee_scale: FeeScale) -> ScenarioResult:
    cost_report = report.cost_report
    base_target = report.target
    rates = ResolvedRates(cost_report.cost.rates)
    cost = calculate(_shipment(cost_report.shipment, sc), _rates(rates, sc.fx_pct), fee_scale)
    basis = basis_from_cost(cost, report.basis.sale)
    result = evaluate(basis, base_target.price_net, report.financing)
    unit = None
    if len(basis.items) == 1 and basis.items[0].quantity:
        unit = round_money(basis.items[0].cost_net / basis.items[0].quantity)
    return ScenarioResult(
        sc,
        result.cost_net,
        unit,
        result.net_profit,
        result.margin,
        result.net_profit - base_target.net_profit,
    )


def _signed(n: int, suffix: str) -> str:
    return "текущий" if n == 0 else f"{'+' if n > 0 else '−'}{abs(n)}{suffix}"


def sensitivity(
    report: PriceReport, fee_scale: FeeScale | None = None, duty_custom: Decimal | None = None
) -> WhatIf | None:
    """Таблицы «что если». None — если цена не рассчитана или себестоимость введена вручную."""
    if report.target is None or report.cost_report is None:
        return None
    fee_scale = fee_scale or load_fee_scale()
    shipment = report.cost_report.shipment
    tables, notes = [], []

    def table(title: str, note: str, scenarios: list[Scenario]) -> None:
        rows = []
        for sc in scenarios:
            try:
                rows.append(run_scenario(report, sc, fee_scale))
            except InputError:
                continue  # недопустимый ввод в сценарии (например, отрицательная сумма)
        if rows:
            tables.append(Table(title, note, tuple(rows)))

    if any(code != RUB for code in report.cost_report.cost.rates):
        table(
            "Курс валют",
            "Все валюты партии — на один и тот же процент к курсу расчёта.",
            [Scenario(_signed(n, "%"), fx_pct=Decimal(n)) for n in FX_STEPS],
        )
    else:
        notes.append("Все суммы в рублях — курс на расчёт не влияет.")

    has_freight = shipment.freight.rate is not None or any(
        line.kind in FREIGHT_KINDS for line in shipment.costs
    )
    if has_freight:
        table(
            "Фрахт",
            "Расходы «Доставка до границы» и «Доставка по РФ».",
            [Scenario(_signed(n, "%"), freight_pct=Decimal(n)) for n in FREIGHT_STEPS],
        )
    else:
        notes.append("В расчёте нет расходов на доставку — таблица фрахта не строится.")

    duty = []
    if any(item.duty.kind is not DutyKind.SPECIFIC for item in shipment.items):
        duty = [Scenario(_signed(n, " п. п."), duty_pp=Decimal(n)) for n in DUTY_STEPS]
    if duty_custom is not None:
        duty.append(Scenario(f"{duty_custom.normalize():f}% для всех", duty_custom=duty_custom))
    if duty:
        table(
            "Ставка пошлины",
            "Сдвиг процентной части ставки каждой позиции (не ниже 0%); специфическая часть "
            "не меняется.",
            duty,
        )
    unit_label = shipment.items[0].unit.label if len(shipment.items) == 1 else None
    return WhatIf(report.target.price_net, unit_label, tuple(tables), tuple(notes))
