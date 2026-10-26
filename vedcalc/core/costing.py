"""Себестоимость импорта партии: распределение расходов, таможенные платежи, НДС к вычету."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.customs import duty_amount, import_vat
from vedcalc.core.explain import Explainer, ExplainStep, fmt_money, fmt_number, fmt_rate
from vedcalc.core.fees import FeeBracket, FeeScale
from vedcalc.core.fx import AppliedRate, ResolvedRates
from vedcalc.core.models import (
    AllocationMethod,
    CostKind,
    SaleParams,
    Shipment,
    TaxRegime,
)
from vedcalc.core.money import HUNDRED, ZERO, Money, allocate, round_money
from vedcalc.core.units import Unit
from vedcalc.core.validation import ensure_valid


@dataclass(frozen=True, slots=True)
class CostLineResult:
    name: str
    kind: CostKind
    in_customs_value: bool
    source: Money
    net: Decimal  # руб., без НДС
    vat: Decimal  # руб., входящий НДС
    vat_rate: Decimal


@dataclass(frozen=True, slots=True)
class ItemCost:
    name: str
    quantity: Decimal
    unit: Unit
    goods: Decimal  # стоимость товара, руб.
    costs_in_cv: Decimal  # доля расходов, включаемых в ТС (без НДС)
    costs_other: Decimal  # доля прочих расходов (без НДС)
    services_vat: Decimal  # доля входящего НДС по услугам
    customs_value: Decimal
    customs_fee: Decimal  # доля сбора
    duty: Decimal
    excise: Decimal
    import_vat: Decimal
    cost_net: Decimal  # себестоимость без НДС (при УСН — включая весь НДС)
    deductible_vat: Decimal  # НДС к вычету (0 при УСН)

    @property
    def cost_gross(self) -> Decimal:
        return self.cost_net + self.deductible_vat

    def per_unit(self, value: Decimal) -> Decimal:
        """Сумма позиции → на 1 единицу (шт, кг или т — как задано в позиции)."""
        return value / self.quantity

    @property
    def unit_cost_net(self) -> Decimal:
        return self.cost_net / self.quantity

    @property
    def unit_cost_gross(self) -> Decimal:
        return self.cost_gross / self.quantity


@dataclass(frozen=True, slots=True)
class CostResult:
    shipment_name: str
    tax_regime: TaxRegime
    sale: SaleParams
    allocation: AllocationMethod
    items: tuple[ItemCost, ...]
    lines: tuple[CostLineResult, ...]
    rates: dict[str, AppliedRate]
    fee_bracket: FeeBracket
    fee_scale: FeeScale
    # Части себестоимости, на которые не начислен НДС при ввозе и нет входящего НДС.
    vat_free_components: tuple[tuple[str, Decimal], ...]
    warnings: tuple[str, ...]
    steps: tuple[ExplainStep, ...]

    def _sum(self, attr: str) -> Decimal:
        return sum((getattr(i, attr) for i in self.items), ZERO)

    @property
    def goods(self) -> Decimal:
        return self._sum("goods")

    @property
    def customs_value(self) -> Decimal:
        return self._sum("customs_value")

    @property
    def customs_fee(self) -> Decimal:
        return self._sum("customs_fee")

    @property
    def duty(self) -> Decimal:
        return self._sum("duty")

    @property
    def excise(self) -> Decimal:
        return self._sum("excise")

    @property
    def import_vat(self) -> Decimal:
        return self._sum("import_vat")

    @property
    def services_vat(self) -> Decimal:
        return self._sum("services_vat")

    @property
    def cost_net(self) -> Decimal:
        """C — себестоимость партии без НДС."""
        return self._sum("cost_net")

    @property
    def deductible_vat(self) -> Decimal:
        """I — НДС к вычету (ввозной + входящий по услугам)."""
        return self._sum("deductible_vat")

    @property
    def cost_gross(self) -> Decimal:
        return self.cost_net + self.deductible_vat

    @property
    def manual_rates(self) -> bool:
        return any(r.manual for r in self.rates.values())


def _line_amounts(
    net_or_gross: Decimal, vat_included: bool, rate: Decimal
) -> tuple[Decimal, Decimal]:
    """Возвращает (сумма без НДС, НДС) в копейках."""
    if vat_included:
        vat = round_money(net_or_gross * rate / (HUNDRED + rate))
        return net_or_gross - vat, vat
    return net_or_gross, round_money(net_or_gross * rate / HUNDRED)


def calculate(shipment: Shipment, rates: ResolvedRates, fee_scale: FeeScale) -> CostResult:
    ensure_valid(shipment)
    ex = Explainer()
    vat_payer = shipment.tax_regime.vat_payer
    on = shipment.calc_date

    # 1. Курсы
    used = {"RUB"} | shipment.currencies()
    applied = {code: rates[code] for code in sorted(used)}
    for code, r in applied.items():
        if code == "RUB":
            continue
        mark = " — изменён вручную" if r.manual else ""
        ex.add(
            "Курсы",
            f"Курс {code}",
            f"{fmt_money(r.value)} руб. за {r.nominal} {code} на {r.rate_date:%d.%m.%Y}, "
            f"источник: {r.source}{mark}",
            r.per_unit,
        )

    def to_rub(m: Money) -> Decimal:
        return round_money(applied[m.currency].to_rub(m.amount))

    # 2. Товар
    goods = [
        to_rub(Money(i.unit_price.amount * i.quantity, i.unit_price.currency))
        for i in shipment.items
    ]
    excise = [to_rub(i.excise) for i in shipment.items]
    for item, g in zip(shipment.items, goods, strict=True):
        conv = ""
        if item.unit_price.currency != "RUB":
            conv = f" × {fmt_money(applied[item.unit_price.currency].per_unit)}"
        ex.add(
            "Товар",
            f"Стоимость товара «{item.name}»",
            f"{fmt_money(item.unit_price.amount)} {item.unit_price.currency} × "
            f"{fmt_number(item.quantity)} "
            f"{item.unit.label}{conv} = {fmt_money(g)}",
            g,
        )

    # 3. Расходы партии и их распределение
    if shipment.allocation is AllocationMethod.BY_WEIGHT:
        weights = [i.gross_weight_kg or ZERO for i in shipment.items]
    else:
        weights = goods
    ex.add("Распределение", "Метод распределения расходов партии", shipment.allocation.label)

    n = len(shipment.items)
    costs_in_cv = [ZERO] * n
    costs_other = [ZERO] * n
    services_vat = [ZERO] * n
    lines: list[CostLineResult] = []
    vat_free: list[tuple[str, Decimal]] = []
    for line in shipment.costs:
        amount = to_rub(line.amount)
        net, vat = _line_amounts(amount, line.vat_included, line.vat_rate)
        lines.append(
            CostLineResult(
                line.name, line.kind, line.in_customs_value, line.amount, net, vat, line.vat_rate
            )
        )
        if amount == 0:
            continue
        target = costs_in_cv if line.in_customs_value else costs_other
        for k, share in enumerate(allocate(net, weights)):
            target[k] += share
        for k, share in enumerate(allocate(vat, weights)):
            services_vat[k] += share
        where = "входит в ТС" if line.in_customs_value else "не входит в ТС"
        vat_note = f"НДС {fmt_rate(line.vat_rate)}"
        if line.vat_rate:
            vat_note += f" = {fmt_money(vat)}" + (
                " (выделен из суммы)" if line.vat_included else ""
            )
        ex.add("Расходы", line.name, f"{fmt_money(net)} без НДС, {vat_note}; {where}", net)
        if not line.in_customs_value and line.vat_rate == 0:
            vat_free.append((line.name, net))

    # 4. Таможенная стоимость и сбор
    customs_values = [g + c for g, c in zip(goods, costs_in_cv, strict=True)]
    total_cv = sum(customs_values, ZERO)
    for item, g, c, cv in zip(shipment.items, goods, costs_in_cv, customs_values, strict=True):
        ex.add(
            "Таможенные платежи",
            f"Таможенная стоимость «{item.name}»",
            f"товар {fmt_money(g)} + расходы до границы {fmt_money(c)} = {fmt_money(cv)}",
            cv,
        )
    bracket = fee_scale.bracket_for(total_cv)
    fee = bracket.fee
    fee_shares = allocate(fee, customs_values)
    ex.add(
        "Таможенные платежи",
        "Таможенный сбор",
        f"общая ТС {fmt_money(total_cv)} — порог «{bracket.label}» → {fmt_money(fee)} "
        f"({fee_scale.legal_basis}); распределён пропорционально ТС",
        fee,
    )
    if fee:
        vat_free.insert(0, ("Таможенный сбор", fee))

    # 5. Пошлина, НДС, себестоимость по позициям
    items: list[ItemCost] = []
    for k, item in enumerate(shipment.items):
        cv = customs_values[k]
        duty = duty_amount(item.duty, cv)
        ivat = import_vat(cv, duty, excise[k], item.vat_rate)
        ex.add(
            "Таможенные платежи",
            f"Пошлина «{item.name}»",
            f"{fmt_money(cv)} × {fmt_rate(item.duty.percent)} = {fmt_money(duty)}",
            duty,
        )
        excise_part = f" + акциз {fmt_money(excise[k])}" if excise[k] else ""
        ex.add(
            "Таможенные платежи",
            f"НДС при ввозе «{item.name}»",
            f"({fmt_money(cv)} + {fmt_money(duty)}{excise_part}) × {fmt_rate(item.vat_rate)} "
            f"= {fmt_money(ivat)}",
            ivat,
        )

        base = goods[k] + costs_in_cv[k] + costs_other[k] + fee_shares[k] + duty + excise[k]
        all_vat = ivat + services_vat[k]
        cost_net = base if vat_payer else base + all_vat
        deductible = all_vat if vat_payer else ZERO
        cost_formula = (
            f"товар {fmt_money(goods[k])} + расходы {fmt_money(costs_in_cv[k] + costs_other[k])}"
            f" + сбор {fmt_money(fee_shares[k])} + пошлина {fmt_money(duty)}"
            + (f" + акциз {fmt_money(excise[k])}" if excise[k] else "")
        )
        if vat_payer:
            ex.add(
                "Себестоимость",
                f"Себестоимость без НДС «{item.name}»",
                f"{cost_formula} = {fmt_money(cost_net)}",
                cost_net,
            )
            ex.add(
                "Себестоимость",
                f"НДС к вычету «{item.name}»",
                f"ввозной {fmt_money(ivat)} + по услугам {fmt_money(services_vat[k])} "
                f"= {fmt_money(deductible)}",
                deductible,
            )
        else:
            ex.add(
                "Себестоимость",
                f"Себестоимость «{item.name}» (УСН, НДС включён)",
                f"{cost_formula} + НДС {fmt_money(all_vat)} = {fmt_money(cost_net)}",
                cost_net,
            )
        ex.add(
            "Себестоимость",
            f"Себестоимость единицы «{item.name}»",
            f"{fmt_money(cost_net)} / {fmt_number(item.quantity)} {item.unit.label} = "
            f"{fmt_money(cost_net / item.quantity)} за {item.unit.label}",
            cost_net / item.quantity,
        )
        items.append(
            ItemCost(
                name=item.name,
                quantity=item.quantity,
                unit=item.unit,
                goods=goods[k],
                costs_in_cv=costs_in_cv[k],
                costs_other=costs_other[k],
                services_vat=services_vat[k],
                customs_value=cv,
                customs_fee=fee_shares[k],
                duty=duty,
                excise=excise[k],
                import_vat=ivat,
                cost_net=cost_net,
                deductible_vat=deductible,
            )
        )

    total_c = sum((i.cost_net for i in items), ZERO)
    ex.add(
        "Себестоимость",
        "Себестоимость партии без НДС (C)",
        " + ".join(fmt_money(i.cost_net) for i in items) + f" = {fmt_money(total_c)}",
        total_c,
    )

    warnings = list(rates.warnings)
    if scale_warning := fee_scale.validity_warning(on):
        warnings.append(scale_warning)
    return CostResult(
        shipment_name=shipment.name,
        tax_regime=shipment.tax_regime,
        sale=shipment.sale,
        allocation=shipment.allocation,
        items=tuple(items),
        lines=tuple(lines),
        rates=applied,
        fee_bracket=bracket,
        fee_scale=fee_scale,
        vat_free_components=tuple(vat_free) if vat_payer else (),
        warnings=tuple(warnings),
        steps=tuple(ex.steps),
    )
