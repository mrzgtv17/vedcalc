"""Ступенчатый прайс: цена и прибыль за единицу при разных объёмах заказа покупателя.

Базовая цена — цена единицы по выбранному сценарию (вкладка «Цена продажи»). На каждом
пороге — скидка от неё. Расходы на один заказ (доставка до клиента, документы) делятся
на объём заказа: чем меньше заказ, тем они заметнее в цене единицы.
Себестоимость и стоимость денег на единицу — как в базовом сценарии (см. ASSUMPTIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.explain import fmt_money, fmt_number
from vedcalc.core.models import TierPricing
from vedcalc.core.money import ZERO, pct, round_money
from vedcalc.core.validation import InputError, Issue

HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class TierRow:
    min_qty: Decimal
    discount_pct: Decimal
    price_net: Decimal  # цена единицы без НДС
    vat: Decimal
    order_cost: Decimal  # расходы на заказ на единицу
    gross_profit: Decimal
    net_profit: Decimal  # чистая прибыль на единицу
    margin: Decimal | None  # валовая прибыль / цена, как в сценарии цены
    over_batch: bool  # порог больше, чем товара в партии

    @property
    def price_gross(self) -> Decimal:
        return self.price_net + self.vat

    @property
    def order_total_gross(self) -> Decimal:
        """Сумма минимального заказа на пороге, с НДС."""
        return round_money(self.price_gross * self.min_qty)

    @property
    def loss(self) -> bool:
        return self.net_profit < 0


@dataclass(frozen=True, slots=True)
class TierTable:
    item_name: str
    unit_label: str
    base_price_net: Decimal
    vat_payer: bool
    order_cost: Decimal
    rows: tuple[TierRow, ...]

    @property
    def notes(self) -> list[str]:
        out = []
        if self.order_cost:
            out.append(f"Расходы на заказ {fmt_money(self.order_cost)} ₽ делятся на объём заказа.")
        if any(r.over_batch for r in self.rows):
            out.append("Порог больше, чем товара в партии, — продать такой заказ не из чего.")
        if any(r.loss for r in self.rows):
            out.append("На пороге с убытком скидка больше, чем позволяет себестоимость.")
        return out


def validate(params: TierPricing) -> list[Issue]:
    issues = []
    prev = ZERO
    for n, t in enumerate(params.tiers, 1):
        path = f"tiers.{n}"
        if t.min_qty <= 0:
            issues.append(Issue(f"{path}.min_qty", f"Порог {n}: количество должно быть больше 0"))
        elif t.min_qty <= prev:
            issues.append(Issue(f"{path}.min_qty", f"Порог {n}: количество — по возрастанию"))
        else:
            prev = t.min_qty
        if not ZERO <= t.discount_pct < HUNDRED:
            issues.append(Issue(f"{path}.discount", f"Порог {n}: скидка — от 0 до 99,99 %"))
    if params.order_cost < 0:
        issues.append(Issue("order_cost", "Расходы на заказ не могут быть отрицательными"))
    return issues


def tier_table(report, params: TierPricing) -> TierTable | None:
    """Прайс по позиции params.item; None — нет цены по сценарию или порогов."""
    views = report.unit_views()
    if not views or not params.tiers:
        return None
    if issues := validate(params):
        raise InputError(issues)
    view = views[min(max(params.item, 0), len(views) - 1)]
    sale = report.basis.sale
    vat_rate = pct(sale.vat_rate) if report.vat_payer else ZERO
    r, t = pct(sale.price_dependent_rate), pct(sale.profit_tax_rate)
    base = view.price.net
    rows = []
    for tier in params.tiers:
        p = round_money(base * (1 - tier.discount_pct / HUNDRED))
        order_u = round_money(params.order_cost / tier.min_qty)
        gross = p - view.cost_net - view.financing - round_money(p * r) - order_u
        tax = round_money(max(ZERO, gross) * t)
        rows.append(
            TierRow(
                min_qty=tier.min_qty,
                discount_pct=tier.discount_pct,
                price_net=p,
                vat=round_money(p * vat_rate),
                order_cost=order_u,
                gross_profit=gross,
                net_profit=gross - tax,
                margin=gross / p if p else None,
                over_batch=tier.min_qty > view.quantity,
            )
        )
    return TierTable(
        item_name=view.name,
        unit_label=view.unit_label,
        base_price_net=base,
        vat_payer=report.vat_payer,
        order_cost=params.order_cost,
        rows=tuple(rows),
    )


def tier_label(row: TierRow, unit_label: str) -> str:
    return f"от {fmt_number(row.min_qty)} {unit_label}"
