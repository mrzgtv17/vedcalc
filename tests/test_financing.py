"""Стоимость денег и загрузка контейнера."""

from decimal import Decimal as D

import pytest
from conftest import control_shipment, run

from vedcalc.core.basis import basis_from_cost, basis_from_manual
from vedcalc.core.containers import estimate_load, load_containers
from vedcalc.core.financing import financing_cost
from vedcalc.core.io import shipment_from_dict, shipment_to_dict
from vedcalc.core.models import (
    DeliveryMode,
    Financing,
    FinancingMode,
    ManualCost,
    PriceMode,
    SaleParams,
)
from vedcalc.core.pricing import price_for


@pytest.fixture
def basis(scale):
    return basis_from_cost(run(control_shipment(), scale))


def test_basis_cash_components(basis):
    assert basis.supplier_payment == D("100000.00")
    assert basis.logistics_payment == D("40000.00")
    assert basis.customs_payment == D("37761.00")  # пошлина 6 500 + НДС 30 030 + сбор 1 231
    assert basis.cost_gross == D("177761.00")


def test_simple_mode(basis):
    fin = Financing(FinancingMode.SIMPLE, annual_rate=D(20), loan_days=60)
    assert financing_cost(fin, basis, D(0)).cost == D("5844.20")  # 177 761 × 20% × 60/365
    fin.loan_amount, fin.borrowed_share = D(100_000), D(50)
    assert financing_cost(fin, basis, D(0)).cost == D("1643.84")  # 100 000 × 20% × 60/365 × 50%


def test_schedule_mode_by_periods(basis):
    fin = Financing(FinancingMode.SCHEDULE, annual_rate=D(20))
    result = financing_cost(fin, basis, D("244000.00"))
    assert [(p.day_from, p.day_to, p.balance) for p in result.periods] == [
        (0, 30, D("30000.00")),  # предоплата 30%
        (30, 70, D("100000.00")),  # + остаток поставщику
        (70, 75, D("140000.00")),  # + логистика по прибытии
        (75, 112, D("177761.00")),  # + таможня; покупатель платит через 7 + 30 дней
    ]
    assert result.cost == D("6672.41")
    assert result.total_days == 112


def test_buyer_prepayment_reduces_cost(basis):
    fin = Financing(FinancingMode.SCHEDULE, annual_rate=D(20))
    without = financing_cost(fin, basis, D(244_000)).cost
    fin.buyer_prepay_pct, fin.buyer_prepay_day = D(50), 30
    with_prepay = financing_cost(fin, basis, D(244_000)).cost
    assert with_prepay < without
    fin.buyer_prepay_pct = D(100)  # покупатель оплатил всё вперёд — вложений после дня 30 нет
    assert financing_cost(fin, basis, D(244_000)).cost == D("493.15")  # только 30 000 × 30 дней


def test_off_or_zero_rate(basis):
    assert financing_cost(Financing(), basis, D(1)).cost == 0
    fin = Financing(FinancingMode.SIMPLE, annual_rate=D(0))
    assert financing_cost(fin, basis, D(1)).cost == 0


@pytest.mark.parametrize(
    ("mode", "value", "check"),
    [
        (PriceMode.MARKUP, 20, lambda r: abs(r.markup * 100 - 20) < D("0.001")),
        (PriceMode.MARGIN, 25, lambda r: abs(r.margin * 100 - 25) < D("0.001")),
        (PriceMode.NET_PROFIT, 30_000, lambda r: abs(r.net_profit - D(30_000)) <= D("0.02")),
    ],
)
def test_price_modes_converge_with_buyer_prepayment(basis, mode, value, check):
    fin = Financing(FinancingMode.SCHEDULE, annual_rate=D(25), buyer_prepay_pct=D(40))
    result = price_for(basis, mode, value, fin)
    assert result.financing > 0
    assert check(result)
    assert result.gross_profit == (
        result.price_net - result.cost_net - result.price_dependent - result.financing
    )


def test_manual_basis():
    manual = ManualCost("Стеарин", D(1000), unit_cost_net=D("150.50"), unit_deductible_vat=D(33))
    b = basis_from_manual(manual, SaleParams())
    assert b.cost_net == D("150500.00") and b.deductible_vat == D("33000.00")
    p = price_for(b, PriceMode.UNIT_PRICE, 200)
    assert p.price_net == D("200000.00")
    assert p.gross_profit == D("49500.00")


def test_financing_saved_in_file():
    s = control_shipment()
    s.financing = Financing(FinancingMode.SCHEDULE, annual_rate=D("17.5"), buyer_deferral_days=45)
    s.manual_cost = ManualCost("X", D(3), unit_cost_net=D(10))
    loaded = shipment_from_dict(shipment_to_dict(s))
    assert loaded.financing == s.financing
    assert loaded.manual_cost == s.manual_cost
    # Файлы 0.2.0 без новых полей открываются с настройками по умолчанию
    old = shipment_to_dict(s)
    for key in ("financing", "pricing_source", "manual_cost"):
        old["shipment"].pop(key)
    assert shipment_from_dict(old).financing == Financing()


# --- контейнеры --------------------------------------------------------------------------


def test_container_specs_from_reference():
    c = load_containers()
    assert set(c) == {"20DC", "20HC", "40DC", "40HC", "45PW", "45HCPW"}
    assert c["20DC"].max_cargo_kg == 27000 and c["40HC"].max_cargo_kg == 26000
    assert c["45PW"].max_cargo_kg == 26000
    assert round(c["40HC"].volume_m3, 2) == D("75.88")  # 12,093 × 2,330 × 2,693


def test_load_estimate_and_overflow():
    s = control_shipment()
    s.delivery_mode = DeliveryMode.DC20
    s.items[0].volume_m3, s.items[0].gross_weight_kg = D("20"), D(30_000)
    load = estimate_load(s)
    assert round(load.volume_share * 100) == 60
    assert round(load.weight_share * 100) == 111  # 30 т из 27 т
    assert not load.fits and load.binding == "вес" and load.containers_needed == 2


def test_no_load_hint_for_non_container_or_empty():
    s = control_shipment()
    assert estimate_load(s) is None  # нет веса и объёма
    s.items[0].volume_m3 = D(1)
    s.delivery_mode = DeliveryMode.AIR
    assert estimate_load(s) is None
