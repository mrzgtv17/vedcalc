"""Цена продажи B2B: безубыточная, с наценкой/маржой/целевой прибылью, обратный расчёт.

Обозначения: C — себестоимость без НДС, P — выручка без НДС, v — НДС при продаже,
t — налог на прибыль, r — доля расходов, зависящих от цены (без НДС), I — НДС к вычету.

    Валовая прибыль  G = P − C − r·P
    Налог на прибыль   = max(0, G) × t
    Чистая прибыль     = G − налог
    НДС к доплате    D = P·v − I   (справочно, не расход)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.costing import CostResult
from vedcalc.core.explain import Explainer, ExplainStep, fmt_money, fmt_pct, fmt_rate
from vedcalc.core.models import PriceMode
from vedcalc.core.money import (
    KOPECK,
    ZERO,
    allocate,
    ceil_money,
    pct,
    round_money,
    to_decimal,
)
from vedcalc.core.units import Unit
from vedcalc.core.validation import InputError, Issue

ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class ItemPrice:
    name: str
    quantity: Decimal
    unit: Unit
    price_net: Decimal
    vat: Decimal

    @property
    def price_gross(self) -> Decimal:
        return self.price_net + self.vat

    @property
    def unit_price_net(self) -> Decimal:
        return self.price_net / self.quantity

    @property
    def unit_price_gross(self) -> Decimal:
        return self.price_gross / self.quantity


@dataclass(frozen=True, slots=True)
class PriceResult:
    cost_net: Decimal  # C
    price_net: Decimal  # P
    sale_vat: Decimal  # P·v
    price_dependent: Decimal  # r·P
    gross_profit: Decimal
    profit_tax: Decimal
    net_profit: Decimal
    deductible_vat: Decimal  # I
    vat_payable: Decimal  # D = P·v − I, справочно
    markup: Decimal | None  # G / C
    margin: Decimal | None  # G / P
    items: tuple[ItemPrice, ...]
    steps: tuple[ExplainStep, ...]

    @property
    def price_gross(self) -> Decimal:
        return self.price_net + self.sale_vat


@dataclass(frozen=True, slots=True)
class VatGap:
    """НДС к доплате при P = C: v × часть себестоимости без входящего НДС."""

    amount: Decimal
    components: tuple[tuple[str, Decimal], ...]
    other: Decimal  # разница ставок НДС и округление

    def explanation(self, sale_vat_rate: Decimal) -> str:
        if not self.components and not self.other:
            return "НДС-разрыва нет: на все части себестоимости начислен входящий НДС."
        parts = [f"{name} {fmt_money(amount)}" for name, amount in self.components]
        base = sum((a for _, a in self.components), ZERO)
        text = (
            "На эти части себестоимости не начислен НДС при ввозе и нет входящего НДС, "
            f"а НДС с продажи на них начисляется: {', '.join(parts)}; "
            f"итого {fmt_money(base)} × {fmt_rate(sale_vat_rate)}"
        )
        if self.other:
            text += f"; разница ставок НДС и округление {fmt_money(self.other)}"
        return text + f" = {fmt_money(self.amount)}."


def _sale_vat_rate(cost: CostResult) -> Decimal:
    return cost.sale.vat_rate if cost.tax_regime.vat_payer else ZERO


def _r(cost: CostResult) -> Decimal:
    return pct(cost.sale.price_dependent_rate)


def _fail(path: str, message: str) -> InputError:
    return InputError([Issue(path, message)])


def evaluate(cost: CostResult, price_net: Decimal | int | str) -> PriceResult:
    """Обратный расчёт: по цене без НДС — прибыль, наценка, маржа, НДС."""
    p = round_money(to_decimal(price_net))
    if p < 0:
        raise _fail("price", "Цена не может быть отрицательной")
    c = cost.cost_net
    v_rate = _sale_vat_rate(cost)
    t = pct(cost.sale.profit_tax_rate)
    sale_vat = round_money(p * pct(v_rate))
    dependent = round_money(p * _r(cost))
    gross = p - c - dependent
    tax = round_money(max(ZERO, gross) * t)
    net = gross - tax
    i = cost.deductible_vat
    d = sale_vat - i
    markup = gross / c if c else None
    margin = gross / p if p else None

    weights = [it.cost_net for it in cost.items]
    if sum(weights, ZERO) <= 0:
        weights = [it.goods for it in cost.items]
    items = tuple(
        ItemPrice(it.name, it.quantity, it.unit, pn, vat)
        for it, pn, vat in zip(
            cost.items, allocate(p, weights), allocate(sale_vat, weights), strict=True
        )
    )

    ex = Explainer()
    s = "Цена"
    ex.add(s, "Выручка без НДС (P)", fmt_money(p), p)
    ex.add(
        s, "НДС с продажи", f"{fmt_money(p)} × {fmt_rate(v_rate)} = {fmt_money(sale_vat)}", sale_vat
    )
    ex.add(
        s,
        "Цена с НДС",
        f"{fmt_money(p)} + {fmt_money(sale_vat)} = {fmt_money(p + sale_vat)}",
        p + sale_vat,
    )
    if dependent:
        ex.add(
            s,
            "Расходы от выручки",
            f"{fmt_money(p)} × {fmt_rate(cost.sale.price_dependent_rate)} = {fmt_money(dependent)}",
            dependent,
        )
    dep_part = f" − {fmt_money(dependent)}" if dependent else ""
    ex.add(
        s,
        "Валовая прибыль",
        f"{fmt_money(p)} − {fmt_money(c)}{dep_part} = {fmt_money(gross)}",
        gross,
    )
    ex.add(
        s,
        "Налог на прибыль",
        f"max(0; {fmt_money(gross)}) × {fmt_rate(cost.sale.profit_tax_rate)} = {fmt_money(tax)}",
        tax,
    )
    ex.add(s, "Чистая прибыль", f"{fmt_money(gross)} − {fmt_money(tax)} = {fmt_money(net)}", net)
    ex.add(s, "Наценка", f"{fmt_money(gross)} / {fmt_money(c)} = {fmt_pct(markup)}")
    ex.add(s, "Маржинальность", f"{fmt_money(gross)} / {fmt_money(p)} = {fmt_pct(margin)}")
    if cost.tax_regime.vat_payer:
        ex.add(
            s,
            "НДС к уплате (справочно)",
            f"{fmt_money(sale_vat)} − к вычету {fmt_money(i)} = {fmt_money(d)}; "
            "не расход, в прибыль не входит",
            d,
        )

    return PriceResult(
        cost_net=c,
        price_net=p,
        sale_vat=sale_vat,
        price_dependent=dependent,
        gross_profit=gross,
        profit_tax=tax,
        net_profit=net,
        deductible_vat=i,
        vat_payable=d,
        markup=markup,
        margin=margin,
        items=items,
        steps=tuple(ex.steps),
    )


def break_even(cost: CostResult) -> PriceResult:
    """Минимальная цена (чистая прибыль = 0): P_min = C / (1 − r).

    Цена округляется до копеек вверх: при округлении вниз чистая прибыль стала бы
    отрицательной на доли копейки.
    """
    r = _r(cost)
    if r >= ONE:
        raise _fail("sale.price_dependent_rate", "Расходы от выручки должны быть меньше 100%")
    p = ceil_money(cost.cost_net / (ONE - r))
    result = evaluate(cost, p)
    # Округление r·P до копеек может съесть копейку — добираем её.
    while result.net_profit < 0:
        p += KOPECK
        result = evaluate(cost, p)
    return result


def price_for(cost: CostResult, mode: PriceMode, value: Decimal | int | str) -> PriceResult:
    """Цена с заданной доходностью. value — проценты для наценки/маржи, рубли — для прибыли."""
    x = to_decimal(value)
    c = cost.cost_net
    r = _r(cost)
    if r >= ONE:
        raise _fail("sale.price_dependent_rate", "Расходы от выручки должны быть меньше 100%")
    if mode is PriceMode.PRICE:
        return evaluate(cost, x)
    if mode is PriceMode.MARKUP:
        if x <= -100:
            raise _fail("markup", "Наценка должна быть больше −100%")
        p = c * (ONE + pct(x)) / (ONE - r)
    elif mode is PriceMode.MARGIN:
        m = pct(x)
        if m >= ONE:
            raise _fail("margin", "Маржинальность должна быть меньше 100%")
        if m + r >= ONE:
            raise _fail(
                "margin", "Маржинальность вместе с расходами от выручки должна быть меньше 100%"
            )
        p = c / (ONE - m - r)
    elif mode is PriceMode.NET_PROFIT:
        t = pct(cost.sale.profit_tax_rate)
        if t >= ONE:
            raise _fail("sale.profit_tax_rate", "Налог на прибыль должен быть меньше 100%")
        if x < 0:
            # При убытке налог не возникает: G = N
            p = (c + x) / (ONE - r)
        else:
            p = (c + x / (ONE - t)) / (ONE - r)
    else:  # pragma: no cover
        raise ValueError(mode)
    if p < 0:
        raise _fail(str(mode), "Получилась отрицательная цена — проверьте параметры")
    return evaluate(cost, p)


def vat_gap(cost: CostResult) -> VatGap:
    """НДС к доплате при P = C (справочно)."""
    if not cost.tax_regime.vat_payer:
        return VatGap(ZERO, (), ZERO)
    v = pct(cost.sale.vat_rate)
    amount = round_money(cost.cost_net * v) - cost.deductible_vat
    components = cost.vat_free_components
    base = sum((a for _, a in components), ZERO)
    other = amount - round_money(base * v)
    return VatGap(amount, components, other)
