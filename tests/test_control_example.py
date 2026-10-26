"""Контрольный пример из ТЗ (раздел 6) — должен совпадать до копейки."""

from decimal import Decimal as D

from conftest import control_shipment, run

from vedcalc.core.pricing import PriceMode, break_even, price_for, vat_gap


def test_cost(scale):
    cost = run(control_shipment(), scale)
    assert cost.customs_value == D("130000.00")
    assert cost.customs_fee == D("1231")
    assert cost.duty == D("6500.00")
    assert cost.import_vat == D("30030.00")
    assert cost.deductible_vat == D("30030.00")
    assert cost.cost_net == D("147731.00")
    assert cost.cost_gross == D("177761.00")
    assert cost.items[0].unit_cost_net == D("147731.00")


def test_vat_gap(scale):
    gap = vat_gap(run(control_shipment(), scale))
    assert gap.amount == D("2470.82")
    assert [name for name, _ in gap.components] == ["Таможенный сбор", "Доставка по РФ"]
    assert sum(a for _, a in gap.components) == D("11231")
    assert gap.other == 0
    assert "11 231,00 × 22%" in gap.explanation(D(22))


def test_break_even(scale):
    p = break_even(run(control_shipment(), scale))
    assert p.price_net == D("147731.00")
    assert p.sale_vat == D("32500.82")
    assert p.price_gross == D("180231.82")
    assert p.vat_payable == D("2470.82")
    assert p.net_profit == D("0.00")
    assert p.profit_tax == 0


def test_sale_at_200000(scale):
    p = price_for(run(control_shipment(), scale), PriceMode.PRICE, 200_000)
    assert p.price_gross == D("244000.00")
    assert p.gross_profit == D("52269.00")
    assert p.profit_tax == D("13067.25")
    assert p.net_profit == D("39201.75")
    assert p.vat_payable == D("13970.00")
    assert round(p.markup * 100, 2) == D("35.38")
    assert round(p.margin * 100, 2) == D("26.13")
