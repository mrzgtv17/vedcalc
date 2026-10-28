"""Отчёты — общий источник для экрана, PDF и XLSX.

CostReport — себестоимость импорта (вкладка «Импорт»).
PriceReport — цена продажи и прибыль (вкладка «Цена продажи»).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from vedcalc.core.basis import PricingBasis
from vedcalc.core.containers import LoadEstimate
from vedcalc.core.costing import CostResult
from vedcalc.core.explain import ExplainStep, fmt_money, fmt_pct, fmt_rate
from vedcalc.core.models import Financing, FinancingMode, PriceMode, Shipment
from vedcalc.core.pricing import PriceResult, price_for
from vedcalc.core.unit_economics import (
    UnitCostView,
    UnitPriceView,
    unit_cost_views,
    unit_price_views,
)
from vedcalc.core.validation import InputError


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    value: Decimal | str | None  # Decimal — рубли; str — готовый текст (проценты и т.п.)
    strong: bool = False
    hint: str = ""

    @property
    def text(self) -> str:
        if self.value is None:
            return "—"
        if isinstance(self.value, Decimal):
            return fmt_money(self.value)
        return self.value


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    rows: tuple[Row, ...]


# --- себестоимость импорта ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostReport:
    shipment: Shipment
    cost: CostResult
    load: LoadEstimate | None = None
    generated: datetime = field(default_factory=_now)

    @property
    def vat_payer(self) -> bool:
        return self.cost.tax_regime.vat_payer

    def unit_views(self) -> list[UnitCostView]:
        return unit_cost_views(self.shipment, self.cost)

    def batch_section(self) -> Section:
        c = self.cost
        rows = [
            Row(
                "Себестоимость партии без НДС" if self.vat_payer else "Себестоимость партии (УСН)",
                c.cost_net,
                strong=True,
            ),
        ]
        if self.vat_payer:
            rows += [
                Row(
                    "НДС к вычету",
                    c.deductible_vat,
                    hint=f"НДС при ввозе {fmt_money(c.import_vat)} + по услугам "
                    f"{fmt_money(c.services_vat)}",
                ),
                Row("Себестоимость партии с НДС", c.cost_gross),
            ]
        rows += [
            Row("Стоимость товара", c.goods),
            Row("Таможенная стоимость", c.customs_value),
            Row("Таможенный сбор", c.customs_fee, hint=c.fee_bracket.label),
            Row("Ввозная таможенная пошлина", c.duty),
        ]
        if c.excise:
            rows.append(Row("Акциз", c.excise))
        rows.append(Row("НДС при ввозе", c.import_vat))
        return Section("Итого по партии", tuple(rows))

    def load_rows(self) -> tuple[Row, ...]:
        load = self.load
        if load is None:
            return ()
        spec = load.spec
        return (
            Row(
                "Объём груза",
                f"{fmt_money(load.volume_m3)} из {fmt_money(spec.volume_m3)} м³ · "
                f"{fmt_pct(load.volume_share, 0)}",
            ),
            Row(
                "Вес груза брутто",
                f"{fmt_money(load.weight_kg / 1000)} из {fmt_money(spec.max_cargo_kg / 1000)} т · "
                f"{fmt_pct(load.weight_share, 0)}",
            ),
            Row("Нужно контейнеров", str(load.containers_needed)),
        )

    def rates_rows(self) -> tuple[Row, ...]:
        rows = []
        for code, r in self.cost.rates.items():
            if code == "RUB":
                continue
            mark = ", изменён вручную" if r.manual else ""
            rows.append(
                Row(
                    f"{code} (за {r.nominal})",
                    f"{fmt_money(r.value)} на {r.rate_date:%d.%m.%Y}, {r.source}{mark}",
                )
            )
        return tuple(rows)

    def method_rows(self) -> tuple[Row, ...]:
        c = self.cost
        return (
            Row("Налоговый режим", c.tax_regime.label),
            Row("Распределение расходов", c.allocation.label),
            Row("Шкала сборов", f"{c.fee_scale.legal_basis}, с {c.fee_scale.valid_from:%d.%m.%Y}"),
            Row("Порог шкалы", f"{c.fee_bracket.label} → {fmt_money(c.fee_bracket.fee)}"),
        )

    def step_groups(self) -> list[tuple[str, tuple[ExplainStep, ...]]]:
        groups: dict[str, list[ExplainStep]] = {}
        for step in self.cost.steps:
            groups.setdefault(step.section, []).append(step)
        return [(title, tuple(steps)) for title, steps in groups.items()]


def build_cost_report(
    shipment: Shipment, cost: CostResult, load: LoadEstimate | None = None
) -> CostReport:
    return CostReport(shipment, cost, load)


# --- цена продажи ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceReport:
    title: str
    basis: PricingBasis
    financing: Financing
    target: PriceResult | None
    target_error: str | None
    cost_report: CostReport | None = None  # если себестоимость — из расчёта импорта
    generated: datetime = field(default_factory=_now)

    @property
    def vat_payer(self) -> bool:
        return self.basis.tax_regime.vat_payer

    @property
    def target_title(self) -> str:
        unit = self.basis.items[0].unit.label if len(self.basis.items) == 1 else None
        sale = self.basis.sale
        return target_title(sale.target_mode, sale.target_value, unit)

    def unit_views(self) -> list[UnitPriceView]:
        if self.target is None:
            return []
        costs = [v.cost_net for v in self.cost_report.unit_views()] if self.cost_report else None
        return unit_price_views(self.basis, self.target, costs)

    def batch_section(self) -> Section:
        b, t = self.basis, self.target
        osn = self.vat_payer
        rows = [Row("Себестоимость партии" + (" без НДС" if osn else " (УСН)"), b.cost_net)]
        if t is None:
            return Section("Итого по партии", tuple(rows))
        if t.financing:
            rows.append(Row("Стоимость денег", t.financing, hint=self.financing_hint))
        rows.append(Row("Цена партии без НДС", t.price_net, strong=True))
        if osn:
            rows += [
                Row("в т.ч. НДС", t.sale_vat),
                Row("Цена партии с НДС", t.price_gross, strong=True),
            ]
        if t.price_dependent:
            rows.append(Row("Расходы от выручки", t.price_dependent))
        rows += [
            Row("Валовая прибыль", t.gross_profit),
            Row("Налог на прибыль" if osn else "Налог УСН", t.profit_tax),
            Row("Чистая прибыль", t.net_profit, strong=True),
            Row(
                "Наценка",
                fmt_pct(t.markup),
                hint="Валовая прибыль / (себестоимость + стоимость денег)",
            ),
            Row("Маржинальность", fmt_pct(t.margin)),
        ]
        if osn:
            rows.append(
                Row(
                    "НДС к уплате (справочно)",
                    t.vat_payable,
                    hint="Не расход: платится из НДС, полученного от покупателя",
                )
            )
        return Section("Итого по партии", tuple(rows))

    @property
    def financing_hint(self) -> str:
        detail = self.target.financing_detail if self.target else None
        if detail is None:
            return ""
        return f"{self.financing.mode.label}: {detail.formula}"

    def financing_rows(self) -> tuple[Row, ...]:
        f = self.financing
        detail = self.target.financing_detail if self.target else None
        if f.mode is FinancingMode.OFF or detail is None:
            return ()
        rows = [
            Row("Способ", f.mode.label),
            Row("Ставка", f"{fmt_rate(f.annual_rate)} годовых"),
            Row("Доля заёмных средств", fmt_rate(f.borrowed_share)),
        ]
        if f.mode is FinancingMode.SCHEDULE:
            rows += [
                Row(
                    f"День {e.day}: {e.label}",
                    f"{'−' if e.amount > 0 else '+'}{fmt_money(abs(e.amount))}",
                )
                for e in detail.events
            ]
            rows.append(Row("Деньги в обороте", f"{detail.total_days} дн."))
        rows.append(Row("Стоимость денег за партию", detail.cost, strong=True, hint=detail.formula))
        return tuple(rows)

    def period_rows(self) -> tuple[tuple[str, str, str, str], ...]:
        """Таблица графика: дни · длительность · вложено · проценты (до учёта доли заёмных)."""
        detail = self.target.financing_detail if self.target else None
        if detail is None or self.financing.mode is not FinancingMode.SCHEDULE:
            return ()
        return tuple(
            (f"{p.day_from}–{p.day_to}", str(p.days), fmt_money(p.balance), fmt_money(p.interest))
            for p in detail.periods
        )

    def method_rows(self) -> tuple[Row, ...]:
        sale = self.basis.sale
        osn = self.vat_payer
        return (
            Row("Себестоимость", self.basis.source_label),
            Row("Налоговый режим", self.basis.tax_regime.label),
            Row("НДС при продаже", f"{sale.vat_rate}%" if osn else "не начисляется"),
            Row("Налог на прибыль" if osn else "Ставка УСН", f"{sale.profit_tax_rate}%"),
            Row("Расходы от выручки", fmt_rate(sale.price_dependent_rate)),
        )

    def step_groups(self) -> list[tuple[str, tuple[ExplainStep, ...]]]:
        return [(self.target_title, self.target.steps)] if self.target else []


def build_price_report(
    basis: PricingBasis,
    financing: Financing,
    title: str,
    cost_report: CostReport | None = None,
) -> PriceReport:
    target: PriceResult | None = None
    error: str | None = None
    try:
        target = price_for(basis, basis.sale.target_mode, basis.sale.target_value, financing)
    except InputError as exc:
        error = str(exc)
    return PriceReport(title, basis, financing, target, error, cost_report)


def target_title(mode: PriceMode, value: Decimal, unit_label: str | None = None) -> str:
    if mode is PriceMode.MARKUP:
        return f"Цена с наценкой {fmt_rate(value)}"
    if mode is PriceMode.MARGIN:
        return f"Цена с маржинальностью {fmt_rate(value)}"
    if mode is PriceMode.NET_PROFIT:
        return f"Цена с чистой прибылью {fmt_money(value)} руб. на партию"
    if mode is PriceMode.UNIT_PRICE:
        return f"Продажа по {fmt_money(value)} руб. за 1 {unit_label or 'ед.'} без НДС"
    return f"Продажа партии за {fmt_money(value)} руб. без НДС"
