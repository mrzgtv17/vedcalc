"""Экономика единицы товара: себестоимость и цена за 1 шт, 1 кг или 1 т.

Суммы на единицу округляются до копеек построчно, а итоги и производные считаются из уже
округлённых строк. Так каждая показанная формула сходится до копейки. Итоги по партии
считаются отдельно (costing, pricing) и при умножении на количество могут отличаться
от «цены единицы × количество» на несколько копеек.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.costing import CostResult, ItemCost
from vedcalc.core.explain import fmt_money, fmt_number, fmt_rate
from vedcalc.core.models import Item, PriceMode, Shipment
from vedcalc.core.money import ZERO, ceil_money, pct, round_money
from vedcalc.core.pricing import ItemPrice, PriceResult

ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class UnitLine:
    """Строка расчёта на единицу: подпись, формула с подставленными числами, сумма."""

    label: str
    formula: str
    value: Decimal
    strong: bool = False
    hint: str = ""
    informational: bool = False  # не слагаемое себестоимости (например, ТС)

    @property
    def text(self) -> str:
        return fmt_money(self.value)


@dataclass(frozen=True, slots=True)
class UnitPrice:
    net: Decimal
    vat: Decimal
    dependent: Decimal
    gross_profit: Decimal
    profit_tax: Decimal
    net_profit: Decimal
    lines: tuple[UnitLine, ...]

    @property
    def gross(self) -> Decimal:
        return self.net + self.vat


@dataclass(frozen=True, slots=True)
class UnitView:
    name: str
    quantity: Decimal
    unit_label: str
    cost_net: Decimal  # себестоимость единицы без НДС (при УСН — с НДС)
    deductible_vat: Decimal  # НДС к вычету на единицу
    cost_lines: tuple[UnitLine, ...]
    break_even: UnitPrice
    target: UnitPrice | None

    @property
    def per(self) -> str:
        return f"за 1 {self.unit_label}"

    @property
    def cost_gross(self) -> Decimal:
        return self.cost_net + self.deductible_vat


def _cost_lines(
    cost: CostResult, it: ItemCost, src: Item
) -> tuple[tuple[UnitLine, ...], Decimal, Decimal]:
    """Строки себестоимости единицы, себестоимость без НДС и НДС к вычету на единицу."""
    vat_payer = cost.tax_regime.vat_payer
    u = it.unit.label
    q = fmt_number(it.quantity)

    def per(value: Decimal) -> Decimal:
        return round_money(value / it.quantity)

    def share(total: Decimal, what: str) -> str:
        return what if it.quantity == 1 else f"{what} {fmt_money(total)} / {q} {u}"

    goods = per(it.goods)
    cur = src.unit_price.currency
    if cur == "RUB":
        goods_formula = "цена поставщика"
    else:
        rate = cost.rates[cur].per_unit
        goods_formula = f"{fmt_money(src.unit_price.amount)} {cur} × курс {fmt_number(rate)}"
    lines = [UnitLine("Товар", goods_formula, goods)]
    parts = [goods]

    to_border = per(it.costs_in_cv)
    if it.costs_in_cv:
        lines.append(
            UnitLine(
                "Доставка до границы, страхование",
                share(it.costs_in_cv, "доля расходов партии"),
                to_border,
                hint="Входит в таможенную стоимость",
            )
        )
        parts.append(to_border)

    cv = goods + to_border
    lines.append(
        UnitLine(
            "Таможенная стоимость",
            "товар + доставка до границы" if it.costs_in_cv else "= стоимость товара",
            cv,
            hint="База для пошлины и НДС; в сумму не входит",
            informational=True,
        )
    )

    duty = per(it.duty)
    lines.append(
        UnitLine(
            "Ввозная пошлина",
            f"{fmt_money(cv)} × {fmt_rate(src.duty.percent)}",
            duty,
        )
    )
    parts.append(duty)

    excise = per(it.excise)
    if it.excise:
        lines.append(UnitLine("Акциз", share(it.excise, "акциз позиции"), excise))
        parts.append(excise)

    fee = per(it.customs_fee)
    fee_formula = (
        cost.fee_bracket.label
        if len(cost.items) == 1 and it.quantity == 1
        else share(it.customs_fee, "доля сбора")
    )
    lines.append(
        UnitLine(
            "Таможенный сбор",
            fee_formula,
            fee,
            hint=f"Сбор за декларацию {fmt_money(cost.customs_fee)} "
            f"(ТС партии {fmt_money(cost.customs_value)})",
        )
    )
    parts.append(fee)

    other = per(it.costs_other)
    if it.costs_other:
        lines.append(
            UnitLine(
                "Доставка по РФ и прочие расходы",
                share(it.costs_other, "доля расходов партии"),
                other,
                hint="В таможенную стоимость не входят",
            )
        )
        parts.append(other)

    ivat = per(it.import_vat)
    ivat_formula = (
        f"({fmt_money(cv)} + {fmt_money(duty)}"
        + (f" + {fmt_money(excise)}" if it.excise else "")
        + f") × {fmt_rate(src.vat_rate)}"
    )
    svat = per(it.services_vat)

    if vat_payer:
        cost_net = sum(parts, ZERO)
        deductible = ivat + svat
        lines.append(
            UnitLine(
                "Себестоимость без НДС",
                " + ".join(fmt_money(x) for x in parts),
                cost_net,
                strong=True,
            )
        )
        lines.append(
            UnitLine(
                "НДС при ввозе", ivat_formula, ivat, hint="Принимается к вычету", informational=True
            )
        )
        if it.services_vat:
            lines.append(
                UnitLine(
                    "НДС по услугам",
                    share(it.services_vat, "доля входящего НДС"),
                    svat,
                    hint="Принимается к вычету",
                    informational=True,
                )
            )
        lines.append(
            UnitLine(
                "Себестоимость с НДС",
                f"{fmt_money(cost_net)} + НДС к вычету {fmt_money(deductible)}",
                cost_net + deductible,
                hint="НДС к вычету возмещается из бюджета",
            )
        )
        return tuple(lines), cost_net, deductible

    lines.append(
        UnitLine("НДС при ввозе", ivat_formula, ivat, hint="При УСН входит в себестоимость")
    )
    parts.append(ivat)
    if it.services_vat:
        lines.append(
            UnitLine(
                "НДС по услугам",
                share(it.services_vat, "доля входящего НДС"),
                svat,
                hint="При УСН входит в себестоимость",
            )
        )
        parts.append(svat)
    cost_net = sum(parts, ZERO)
    lines.append(
        UnitLine(
            "Себестоимость (УСН, с НДС)",
            " + ".join(fmt_money(x) for x in parts),
            cost_net,
            strong=True,
        )
    )
    return tuple(lines), cost_net, ZERO


def _price(cost: CostResult, cost_u: Decimal, price_u: Decimal, price_formula: str) -> UnitPrice:
    sale = cost.sale
    vat_payer = cost.tax_regime.vat_payer
    v = sale.vat_rate if vat_payer else ZERO
    r = sale.price_dependent_rate
    vat = round_money(price_u * pct(v))
    dependent = round_money(price_u * pct(r))
    gross = price_u - cost_u - dependent
    tax = round_money(max(ZERO, gross) * pct(sale.profit_tax_rate))
    net = gross - tax

    lines = [UnitLine("Цена без НДС", price_formula, price_u, strong=True)]
    if vat_payer:
        lines += [
            UnitLine(f"НДС {fmt_rate(v)}", f"{fmt_money(price_u)} × {fmt_rate(v)}", vat),
            UnitLine(
                "Цена с НДС", f"{fmt_money(price_u)} + {fmt_money(vat)}", price_u + vat, strong=True
            ),
        ]
    if dependent:
        lines.append(
            UnitLine("Расходы от выручки", f"{fmt_money(price_u)} × {fmt_rate(r)}", dependent)
        )
    dep_txt = f" − {fmt_money(dependent)}" if dependent else ""
    tax_label = "Налог на прибыль" if vat_payer else "Налог УСН"
    lines += [
        UnitLine("Валовая прибыль", f"{fmt_money(price_u)} − {fmt_money(cost_u)}{dep_txt}", gross),
        UnitLine(
            tax_label,
            f"max(0; {fmt_money(gross)}) × {fmt_rate(sale.profit_tax_rate)}",
            tax,
        ),
        UnitLine("Чистая прибыль", f"{fmt_money(gross)} − {fmt_money(tax)}", net, strong=True),
    ]
    return UnitPrice(price_u, vat, dependent, gross, tax, net, tuple(lines))


def _break_even(cost: CostResult, cost_u: Decimal) -> UnitPrice:
    r = pct(cost.sale.price_dependent_rate)
    if r:
        price = ceil_money(cost_u / (ONE - r))
        formula = f"{fmt_money(cost_u)} / (1 − {fmt_rate(cost.sale.price_dependent_rate)})"
    else:
        price, formula = cost_u, "= себестоимость без НДС"
    result = _price(cost, cost_u, price, formula)
    # Округление расходов от выручки может съесть копейку — добираем её.
    while result.net_profit < 0:
        price += Decimal("0.01")
        result = _price(cost, cost_u, price, formula)
    return result


def _target(cost: CostResult, cost_u: Decimal, ip: ItemPrice) -> UnitPrice:
    sale = cost.sale
    mode, value = sale.target_mode, sale.target_value
    r = sale.price_dependent_rate
    r_txt = f" / (1 − {fmt_rate(r)})" if r else ""
    if mode is PriceMode.MARKUP:
        price = round_money(cost_u * (ONE + pct(value)) / (ONE - pct(r)))
        formula = f"{fmt_money(cost_u)} × (1 + {fmt_rate(value)}){r_txt}"
    elif mode is PriceMode.MARGIN:
        price = round_money(cost_u / (ONE - pct(value) - pct(r)))
        r_part = f" − {fmt_rate(r)}" if r else ""
        formula = f"{fmt_money(cost_u)} / (1 − {fmt_rate(value)}{r_part})"
    elif mode is PriceMode.UNIT_PRICE:
        price, formula = round_money(value), "задана"
    else:
        # Цена партии или целевая прибыль партии: доля позиции на единицу
        price = round_money(ip.price_net / ip.quantity)
        if len(cost.items) == 1:
            formula = f"цена партии {fmt_money(ip.price_net)} / {fmt_number(ip.quantity)}"
        else:
            formula = "доля цены партии пропорционально себестоимости позиции"
    return _price(cost, cost_u, price, formula)


def unit_views(
    shipment: Shipment,
    cost: CostResult,
    break_even: PriceResult,
    target: PriceResult | None,
) -> list[UnitView]:
    views = []
    for k, (it, src) in enumerate(zip(cost.items, shipment.items, strict=True)):
        lines, cost_u, deductible = _cost_lines(cost, it, src)
        views.append(
            UnitView(
                name=it.name,
                quantity=it.quantity,
                unit_label=it.unit.label,
                cost_net=cost_u,
                deductible_vat=deductible,
                cost_lines=lines,
                break_even=_break_even(cost, cost_u),
                target=_target(cost, cost_u, target.items[k]) if target else None,
            )
        )
    return views
