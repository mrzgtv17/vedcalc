"""Сводный отчёт по расчёту — общий источник для экрана, PDF и XLSX."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from vedcalc.core.costing import CostResult
from vedcalc.core.explain import ExplainStep, fmt_money, fmt_pct, fmt_rate
from vedcalc.core.models import PriceMode, Shipment
from vedcalc.core.pricing import PriceResult, VatGap, break_even, price_for, vat_gap
from vedcalc.core.validation import InputError


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


@dataclass(frozen=True, slots=True)
class Report:
    shipment: Shipment
    cost: CostResult
    break_even: PriceResult
    target: PriceResult | None
    target_error: str | None
    gap: VatGap
    generated: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))

    @property
    def vat_payer(self) -> bool:
        return self.cost.tax_regime.vat_payer

    def cost_section(self) -> Section:
        c = self.cost
        rows = [
            Row("Таможенная стоимость", c.customs_value),
            Row("Таможенный сбор", c.customs_fee, hint=c.fee_bracket.label),
            Row("Ввозная таможенная пошлина", c.duty),
        ]
        if c.excise:
            rows.append(Row("Акциз", c.excise))
        rows.append(Row("НДС при ввозе", c.import_vat))
        if self.vat_payer:
            rows += [
                Row("Себестоимость партии без НДС", c.cost_net, strong=True),
                Row("НДС к вычету", c.deductible_vat),
                Row("в т.ч. НДС при ввозе", c.import_vat),
                Row("в т.ч. НДС по услугам", c.services_vat),
                Row("Себестоимость партии с НДС", c.cost_gross),
            ]
        else:
            rows.append(Row("Себестоимость партии (НДС включён, УСН)", c.cost_net, strong=True))
        return Section("Себестоимость", tuple(rows))

    def price_rows(self, p: PriceResult) -> tuple[Row, ...]:
        rows = [
            Row("Цена без НДС", p.price_net, strong=True),
        ]
        if self.vat_payer:
            rows += [Row("в т.ч. НДС", p.sale_vat), Row("Цена с НДС", p.price_gross, strong=True)]
        if p.price_dependent:
            rows.append(Row("Расходы от выручки", p.price_dependent))
        rows += [
            Row("Валовая прибыль", p.gross_profit),
            Row("Налог на прибыль", p.profit_tax),
            Row("Чистая прибыль", p.net_profit, strong=True),
            Row("Наценка", fmt_pct(p.markup)),
            Row("Маржинальность", fmt_pct(p.margin)),
        ]
        if self.vat_payer:
            rows.append(
                Row(
                    "НДС к уплате (справочно)",
                    p.vat_payable,
                    hint="Не расход: платится из НДС, полученного от покупателя",
                )
            )
        return tuple(rows)

    def break_even_section(self) -> Section:
        rows = list(self.price_rows(self.break_even))
        if self.vat_payer:
            rows.append(
                Row(
                    "НДС-разрыв при P = C",
                    self.gap.amount,
                    hint=self.gap.explanation(self.cost.sale.vat_rate),
                )
            )
        return Section("Безубыточная цена", tuple(rows))

    def target_section(self) -> Section:
        title = target_title(self.cost.sale.target_mode, self.cost.sale.target_value)
        if self.target is None:
            return Section(title, (Row("Ошибка", self.target_error or ""),))
        return Section(title, self.price_rows(self.target))

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
            Row("НДС при продаже", f"{c.sale.vat_rate}%" if self.vat_payer else "не начисляется"),
            Row("Налог на прибыль", f"{c.sale.profit_tax_rate}%"),
        )

    def step_groups(self) -> list[tuple[str, tuple[ExplainStep, ...]]]:
        """Шаги расшифровки, сгруппированные по разделам, в порядке расчёта."""
        groups: dict[str, list[ExplainStep]] = {}
        for step in self.cost.steps:
            groups.setdefault(step.section, []).append(step)
        result = [(title, tuple(steps)) for title, steps in groups.items()]
        result.append(("Безубыточная цена", self.break_even.steps))
        if self.target is not None:
            result.append(
                (
                    target_title(self.cost.sale.target_mode, self.cost.sale.target_value),
                    self.target.steps,
                )
            )
        return result


def target_title(mode: PriceMode, value: Decimal) -> str:
    if mode is PriceMode.MARKUP:
        return f"Цена с наценкой {fmt_rate(value)}"
    if mode is PriceMode.MARGIN:
        return f"Цена с маржинальностью {fmt_rate(value)}"
    if mode is PriceMode.NET_PROFIT:
        return f"Цена с чистой прибылью {fmt_money(value)} руб."
    return f"Продажа за {fmt_money(value)} руб. без НДС"


def build_report(shipment: Shipment, cost: CostResult) -> Report:
    target: PriceResult | None = None
    error: str | None = None
    try:
        target = price_for(cost, cost.sale.target_mode, cost.sale.target_value)
    except InputError as exc:
        error = str(exc)
    return Report(shipment, cost, break_even(cost), target, error, vat_gap(cost))
