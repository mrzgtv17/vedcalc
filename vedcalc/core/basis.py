"""База для расчёта цены продажи: себестоимость из расчёта импорта или заданная вручную.

Модуль цены не зависит от того, откуда взялась себестоимость.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.costing import CostResult
from vedcalc.core.models import ManualCost, SaleParams, TaxRegime
from vedcalc.core.money import ZERO, round_money
from vedcalc.core.units import Unit


@dataclass(frozen=True, slots=True)
class BasisItem:
    name: str
    quantity: Decimal
    unit: Unit
    cost_net: Decimal  # себестоимость позиции без НДС (при УСН — с НДС)
    deductible_vat: Decimal  # НДС к вычету по позиции
    goods: Decimal  # стоимость товара (вес для распределения, если себестоимость 0)

    @property
    def unit_cost_net(self) -> Decimal:
        return self.cost_net / self.quantity


@dataclass(frozen=True, slots=True)
class PricingBasis:
    tax_regime: TaxRegime
    sale: SaleParams
    items: tuple[BasisItem, ...]
    # Денежные потоки для графика стоимости денег, руб. с НДС:
    supplier_payment: Decimal  # оплата поставщику за товар
    logistics_payment: Decimal  # логистика и прочие расходы
    customs_payment: Decimal  # пошлина, НДС при ввозе, сбор, акциз
    source_label: str

    @property
    def cost_net(self) -> Decimal:
        """C — себестоимость партии без НДС."""
        return sum((i.cost_net for i in self.items), ZERO)

    @property
    def deductible_vat(self) -> Decimal:
        return sum((i.deductible_vat for i in self.items), ZERO)

    @property
    def cost_gross(self) -> Decimal:
        return self.cost_net + self.deductible_vat


def basis_from_cost(cost: CostResult, sale: SaleParams | None = None) -> PricingBasis:
    customs = cost.duty + cost.import_vat + cost.customs_fee + cost.excise
    logistics = sum((line.net + line.vat for line in cost.lines), ZERO)
    return PricingBasis(
        tax_regime=cost.tax_regime,
        sale=sale or cost.sale,
        items=tuple(
            BasisItem(i.name, i.quantity, i.unit, i.cost_net, i.deductible_vat, i.goods)
            for i in cost.items
        ),
        supplier_payment=cost.goods,
        logistics_payment=logistics,
        customs_payment=customs,
        source_label=f"расчёт импорта «{cost.shipment_name}»",
    )


def basis_from_manual(manual: ManualCost, sale: SaleParams) -> PricingBasis:
    q = manual.quantity
    net = round_money(manual.unit_cost_net * q)
    vat = round_money(manual.unit_deductible_vat * q) if manual.tax_regime.vat_payer else ZERO
    return PricingBasis(
        tax_regime=manual.tax_regime,
        sale=sale,
        items=(BasisItem(manual.name or "Товар", q, manual.unit, net, vat, net),),
        # Разбивки нет — вся себестоимость считается оплатой поставщику
        supplier_payment=net + vat,
        logistics_payment=ZERO,
        customs_payment=ZERO,
        source_label="себестоимость задана вручную",
    )
