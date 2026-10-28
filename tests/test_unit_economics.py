"""Расчёт на единицу товара: себестоимость и цена за 1 шт / 1 кг / 1 т."""

from decimal import Decimal as D

import pytest
from conftest import control_shipment, run
from test_costing import two_items

from vedcalc.core.basis import basis_from_cost
from vedcalc.core.models import Financing, FinancingMode, PriceMode, SaleParams, TaxRegime
from vedcalc.core.pricing import price_for
from vedcalc.core.report import build_cost_report, build_price_report
from vedcalc.core.units import Unit
from vedcalc.core.validation import InputError


def cost_views(shipment, scale):
    return build_cost_report(shipment, run(shipment, scale)).unit_views()


def price_views(shipment, scale, financing=None):
    cost = run(shipment, scale)
    report = build_cost_report(shipment, cost)
    price = build_price_report(basis_from_cost(cost), financing or Financing(), "x", report)
    return price.unit_views()


def lines(view):
    return {line.label: line.value for line in view.cost_lines}


def test_control_example_single_unit(scale):
    (v,) = cost_views(control_shipment(), scale)
    assert v.per == "за 1 шт"
    assert v.cost_net == D("147731.00")
    assert v.cost_gross == D("177761.00")


def test_four_pieces_split_per_unit(scale):
    s = control_shipment()
    s.items[0].quantity = D(4)
    (v,) = cost_views(s, scale)
    cost = lines(v)
    assert cost["Товар"] == D("100000.00")
    assert cost["Доставка до границы, страхование"] == D("7500.00")
    assert cost["Таможенная стоимость"] == D("107500.00")
    assert cost["Ввозная пошлина"] == D("5375.00")
    assert cost["Таможенный сбор"] == D("615.50")  # 2 462 (ТС 430 000) / 4
    assert cost["Доставка по РФ и прочие расходы"] == D("2500.00")
    assert v.cost_net == D("115990.50")
    assert cost["НДС при ввозе"] == D("24832.50")


@pytest.mark.parametrize("qty", ["3", "7", "0.37", "12.5"])
def test_unit_arithmetic_adds_up_to_kopeck(scale, qty):
    """Каждая показанная формула должна сходиться до копейки."""
    s = control_shipment()
    s.items[0].quantity = D(qty)
    s.sale.target_mode, s.sale.target_value = PriceMode.MARGIN, D("17.5")
    (c,) = cost_views(s, scale)
    parts = [
        x.value
        for x in c.cost_lines
        if not x.informational and not x.strong and x.label != "Себестоимость с НДС"
    ]
    assert sum(parts) == c.cost_net
    fin = Financing(FinancingMode.SIMPLE, annual_rate=D("18.5"), loan_days=47)
    (v,) = price_views(s, scale, fin)
    p = v.price
    assert v.cost_net == c.cost_net  # цифры на вкладках совпадают
    assert p.gross == p.net + p.vat
    assert p.gross_profit == p.net - v.cost_net - p.dependent - v.financing
    assert p.net_profit == p.gross_profit - p.profit_tax
    assert all(x.value == x.value.quantize(D("0.01")) for x in p.lines)


def test_unit_label_follows_item_unit(scale):
    a, b = cost_views(two_items(), scale)
    assert a.per == "за 1 шт"
    assert b.per == "за 1 т"


def test_target_markup_per_unit(scale):
    s = control_shipment()
    s.items[0].quantity = D(4)
    s.sale.target_mode, s.sale.target_value = PriceMode.MARKUP, D(20)
    (v,) = price_views(s, scale)
    assert v.price.net == D("139188.60")  # 115 990,50 × 1,2
    assert v.price.net_profit == D("17398.57")  # 23 198,10 − 5 799,53


def test_markup_includes_cost_of_money(scale):
    s = control_shipment()
    s.sale.target_mode, s.sale.target_value = PriceMode.MARKUP, D(20)
    fin = Financing(FinancingMode.SIMPLE, annual_rate=D(20), loan_days=60)
    (v,) = price_views(s, scale, fin)
    assert v.financing == D("5844.20")  # 177 761 × 20% × 60/365
    assert v.price.net == D("184290.24")  # (147 731 + 5 844,20) × 1,2
    assert "Стоимость денег" in {x.label for x in v.price.lines}


def test_unit_price_mode(scale):
    s = control_shipment()
    s.items[0].quantity = D(4)
    cost = run(s, scale)
    p = price_for(cost, PriceMode.UNIT_PRICE, 150_000)
    assert p.price_net == D("600000.00")
    s.sale.target_mode, s.sale.target_value = PriceMode.UNIT_PRICE, D(150_000)
    (v,) = price_views(s, scale)
    assert v.price.net == D("150000.00")
    assert v.price.lines[0].formula == "задана"


def test_unit_price_mode_needs_single_item(scale):
    with pytest.raises(InputError, match="одна позиция"):
        price_for(run(two_items(), scale), PriceMode.UNIT_PRICE, 1000)


def test_kg_and_tonne(scale):
    s = control_shipment()
    s.items[0].quantity, s.items[0].unit = D(2), Unit.T
    (v,) = cost_views(s, scale)
    assert v.per == "за 1 т"
    # 2 т × 100 000: ТС 230 000 → сбор 2 462; C = 253 962 → на 1 т 126 981
    assert v.cost_net == D("126981.00")


def test_usn_unit_cost_includes_vat(scale):
    s = control_shipment()
    s.tax_regime = TaxRegime.USN_INCOME_MINUS_EXPENSES
    s.sale = SaleParams.for_regime(s.tax_regime)
    (c,) = cost_views(s, scale)
    assert c.cost_net == D("177761.00")
    assert c.deductible_vat == 0
    (v,) = price_views(s, scale)
    assert v.price.vat == 0
    assert any(x.label == "Налог УСН" for x in v.price.lines)
