"""Себестоимость: валюты, несколько позиций, распределение, ручной курс, УСН, НДС услуг."""

from decimal import Decimal as D

import pytest
from conftest import control_shipment, run

from vedcalc.core.models import (
    AllocationMethod,
    CostLine,
    DutyKind,
    DutyRate,
    Item,
    SaleParams,
    TaxRegime,
)
from vedcalc.core.money import Money
from vedcalc.core.pricing import break_even, vat_gap
from vedcalc.core.units import Unit
from vedcalc.core.validation import InputError


def two_items(allocation=AllocationMethod.BY_VALUE):
    s = control_shipment()
    s.allocation = allocation
    s.items = [
        Item(
            "Насосы",
            Money("1000", "CNY"),
            D(10),
            duty=DutyRate.ad_valorem(5),
            gross_weight_kg=D(300),
        ),
        Item(
            "Фитинги",
            Money("500", "USD"),
            D(2),
            unit=Unit.T,
            duty=DutyRate.ad_valorem("7.5"),
            vat_rate=D(10),
            gross_weight_kg=D(2100),
        ),
    ]
    return s


def test_currencies_converted_at_calc_date_rate(scale):
    cost = run(two_items(), scale)
    # 1000 CNY × 10 × 11,5 = 115 000; 500 USD × 2 × 82,25 = 82 250
    assert [i.goods for i in cost.items] == [D("115000.00"), D("82250.00")]
    assert cost.rates["CNY"].per_unit == D("11.5")
    assert not cost.manual_rates


def test_two_items_different_duty_and_vat(scale):
    cost = run(two_items(), scale)
    a, b = cost.items
    # Расходы 30 000 до границы делятся по стоимости 115 000 : 82 250
    assert a.costs_in_cv + b.costs_in_cv == D("30000.00")
    assert a.costs_in_cv == D("17490.49")  # 30000 × 115000 / 197250
    assert a.customs_value == D("132490.49")
    assert b.customs_value == D("94759.51")
    assert cost.customs_value == D("227250.00")
    assert cost.customs_fee == D("2462")  # 200 000,01 – 450 000
    assert a.customs_fee + b.customs_fee == D("2462")
    assert a.duty == D("6624.52")  # 132 490,49 × 5%
    assert b.duty == D("7106.96")  # 94 759,51 × 7,5%
    assert a.import_vat == D("30605.30")  # (132 490,49 + 6 624,52) × 22%
    assert b.import_vat == D("10186.65")  # (94 759,51 + 7 106,96) × 10%
    assert cost.cost_net == sum(i.cost_net for i in cost.items)
    assert cost.cost_net == D("197250") + D("30000") + D("10000") + D("2462") + a.duty + b.duty


def test_unit_cost_in_item_unit(scale):
    cost = run(two_items(), scale)
    tonnes = cost.items[1]
    assert tonnes.unit is Unit.T
    assert tonnes.unit_cost_net == tonnes.cost_net / 2


def test_allocation_by_weight(scale):
    cost = run(two_items(AllocationMethod.BY_WEIGHT), scale)
    a, b = cost.items
    # 300 : 2100 → 1/8 и 7/8
    assert a.costs_in_cv == D("3750.00")
    assert b.costs_in_cv == D("26250.00")
    assert a.costs_other == D("1250.00")
    assert b.costs_other == D("8750.00")
    assert any("весу" in s.formula for s in cost.steps)


def test_allocation_by_weight_requires_weight(scale):
    s = two_items(AllocationMethod.BY_WEIGHT)
    s.items[0].gross_weight_kg = None
    with pytest.raises(InputError) as e:
        run(s, scale)
    assert e.value.issues[0].path == "items[0].gross_weight_kg"


def test_manual_rate_overrides_and_is_flagged(scale):
    s = two_items()
    s.manual_rates = {"CNY": D("12")}
    cost = run(s, scale)
    assert cost.items[0].goods == D("120000.00")
    assert cost.rates["CNY"].manual and not cost.rates["USD"].manual
    assert cost.manual_rates
    assert "Курс изменён вручную" in cost.warnings
    assert any("изменён вручную" in st.formula for st in cost.steps)


def test_usn_puts_all_vat_into_cost(scale):
    s = control_shipment()
    s.tax_regime = TaxRegime.USN_INCOME_MINUS_EXPENSES
    s.sale = SaleParams.for_regime(s.tax_regime)
    cost = run(s, scale)
    assert cost.deductible_vat == 0
    assert cost.cost_net == D("177761.00")  # 147 731 + 30 030
    p = break_even(cost)
    assert p.sale_vat == 0 and p.price_gross == D("177761.00")
    assert p.vat_payable == 0
    assert vat_gap(cost).amount == 0


def test_usn_income_not_supported(scale):
    s = control_shipment()
    s.tax_regime = TaxRegime.USN_INCOME
    with pytest.raises(InputError, match="не поддерживается"):
        run(s, scale)


def test_services_vat_deductible_on_osn(scale):
    s = control_shipment()
    s.costs.append(
        CostLine("Таможенный брокер", Money.rub(12_200), vat_included=True, vat_rate=D(22))
    )
    cost = run(s, scale)
    assert cost.services_vat == D("2200.00")
    assert cost.deductible_vat == D("32230.00")
    assert cost.cost_net == D("157731.00")  # +10 000 без НДС
    # Брокер облагается НДС — в НДС-разрыв не входит
    assert vat_gap(cost).amount == D("2470.82")


def test_services_vat_on_top(scale):
    s = control_shipment()
    s.costs[1].vat_rate = D(22)  # доставка со СВХ по отдельному договору
    cost = run(s, scale)
    assert cost.services_vat == D("2200.00")
    assert vat_gap(cost).amount == D("270.82")  # остался только сбор 1 231 × 22%


def test_rounding_residue_goes_to_last_item(scale):
    s = control_shipment()
    s.items = [
        Item(f"Позиция {n}", Money.rub(1), D(1), duty=DutyRate.ad_valorem(0)) for n in range(3)
    ]
    s.costs = [CostLine("Доставка", Money.rub(100), in_customs_value=True)]
    cost = run(s, scale)
    assert [i.costs_in_cv for i in cost.items] == [D("33.33"), D("33.33"), D("33.34")]


def test_specific_duty_is_a_clear_error(scale):
    s = control_shipment()
    s.items[0].duty = DutyRate(DutyKind.COMBINED, D(5), Money("0.5", "EUR"), Unit.KG)
    with pytest.raises(InputError, match="комбинированные ставки пошлины"):
        run(s, scale)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda s: s.items.clear(), "items"),
        (lambda s: setattr(s.items[0], "quantity", D(0)), "items[0].quantity"),
        (lambda s: setattr(s.items[0], "unit_price", Money.rub(-1)), "items[0].unit_price"),
        (lambda s: setattr(s.items[0], "vat_rate", D(20)), "items[0].vat_rate"),
        (lambda s: setattr(s.items[0], "tnved_code", "84137"), "items[0].tnved_code"),
        (lambda s: setattr(s.items[0], "name", " "), "items[0].name"),
        (lambda s: setattr(s.costs[0], "amount", Money.rub(-5)), "costs[0].amount"),
        (lambda s: setattr(s.sale, "profit_tax_rate", D(100)), "sale.profit_tax_rate"),
    ],
)
def test_validation_messages(scale, mutate, path):
    s = control_shipment()
    mutate(s)
    with pytest.raises(InputError) as e:
        run(s, scale)
    assert path in [i.path for i in e.value.issues]
    assert all(i.message for i in e.value.issues)


def test_scale_outdated_warning(scale):
    s = control_shipment()
    s.calc_date = s.calc_date.replace(year=2025)
    assert any("Шкала таможенных сборов" in w for w in run(s, scale).warnings)


def test_explanation_has_substituted_numbers(scale):
    steps = {st.title: st.formula for st in run(control_shipment(), scale).steps}
    assert steps["Пошлина «Товар»"] == "130 000,00 × 5% = 6 500,00"
    assert "не более 200 000,00 руб." in steps["Таможенный сбор"]
