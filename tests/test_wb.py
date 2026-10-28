"""Модуль «WB»: юнит-экономика Wildberries для ИП на УСН."""

from decimal import Decimal as D

import pytest

from vedcalc.modules.wb_unit.calc import (
    Scheme,
    Target,
    TaxMode,
    WbInput,
    WbInputError,
    compare_schemes,
    liters_of,
    load_tariffs,
    solve,
)


def line(result, label):
    return next(x.value for x in result.lines if x.label == label)


def test_tariffs_file():
    t = load_tariffs()
    assert t.first_liter == 46 and t.extra_liter == 14 and t.warehouse_coef_pct == 170
    assert [r for _, r in t.small_tiers] == [23, 26, 29, 30, 32]


@pytest.mark.parametrize(
    ("liters", "base"),
    [("0.15", "23"), ("0.2", "23"), ("0.201", "26"), ("0.75", "30"), ("1", "32"),
     ("1.8", "57.2"), ("3", "74")],
)  # fmt: skip
def test_base_logistics(liters, base):
    """До 1 л — по диапазонам; свыше — 46 + 14 × (литры − 1), дробно."""
    assert load_tariffs().base_logistics(D(liters)) == D(base)


def test_worked_example_fbo():
    """20 × 15 × 10 см = 3 л; цена 2 000, скидка 30%, комиссия 25%, выкуп 90%."""
    r = solve(WbInput())
    assert r.liters == D(3)
    assert r.price == D("1400.00")
    assert r.commission == D("350.00")
    assert r.acquiring == D("21.00")
    assert line(r, "Логистика к покупателю (за заказ)") == D("125.80")  # 74 × 170%
    assert line(r, "Обратная логистика (за невыкуп)") == D("74.00")
    assert r.logistics == D("148.00")  # 125,80 / 0,9 + 74 × (1/0,9 − 1)
    assert r.storage == D("12.24")  # 3 × 0,08 × 1,7 × 30
    assert r.payout == D("868.76")
    assert r.tax == D("84.00")
    assert r.profit == D("284.76")
    assert round(r.margin * 100, 2) == D("20.34") and round(r.roi * 100, 2) == D("56.95")


def test_full_buyout_has_no_reverse_logistics():
    r = solve(WbInput(buyout_pct=D(100)))
    assert r.logistics == D("125.80")


def test_fbs_has_no_storage_or_acceptance():
    r = solve(WbInput(scheme=Scheme.FBS, acceptance_coef=D(2)))
    assert r.storage == 0 and r.acceptance == 0
    assert r.commission == D("392.00")  # 28%
    both = compare_schemes(WbInput())
    assert both[Scheme.FBO].profit == D("284.76") and both[Scheme.FBS].profit == D("255.00")


def test_usn_income_minus_expenses_and_minimum_tax():
    r = solve(WbInput(tax_mode=TaxMode.INCOME_MINUS_EXPENSES, tax_rate_pct=D(15)))
    assert r.tax == D("55.31")  # (1 400 − 1 031,24) × 15%
    # Убыточная продажа: платится минимальный налог 1% от доходов
    loss = solve(WbInput(tax_mode=TaxMode.INCOME_MINUS_EXPENSES, tax_rate_pct=D(15),
                         cost_price=D(1200)))  # fmt: skip
    assert loss.tax == D("14.00") and loss.profit < 0


@pytest.mark.parametrize("rate", ["1", "4", "8"])
def test_usn_income_rates(rate):
    assert solve(WbInput(tax_rate_pct=D(rate))).tax == D(1400) * D(rate) / 100


def test_usn_income_rate_limits():
    with pytest.raises(WbInputError, match="от 1 до 8"):
        solve(WbInput(tax_rate_pct=D(9)))


def test_target_margin_and_profit():
    r = solve(WbInput(target=Target.MARGIN, target_value=D(25)))
    assert abs(r.margin * 100 - 25) < D("0.05")
    assert r.retail_price == r.lines[0].value
    p = solve(WbInput(target=Target.PROFIT, target_value=D(500)))
    assert abs(p.profit - 500) <= D("0.02")
    with pytest.raises(WbInputError, match="меньше 100"):
        solve(WbInput(target=Target.MARGIN, target_value=D(100)))


def test_impossible_margin():
    with pytest.raises(WbInputError, match="съедают"):
        solve(WbInput(target=Target.MARGIN, target_value=D(80), commission_fbo_pct=D(30)))


def test_tariff_overrides_and_small_item():
    r = solve(WbInput(first_liter=D(50), extra_liter=D(15)))
    assert line(r, "Обратная логистика (за невыкуп)") == D("80.00")  # 50 + 15 × 2
    small = WbInput(length_cm=D(10), width_cm=D(5), height_cm=D(3))  # 0,15 л
    assert liters_of(small) == D("0.150")
    assert line(solve(small), "Логистика к покупателю (за заказ)") == D("39.10")  # 23 × 170%


def test_validation_messages():
    with pytest.raises(WbInputError, match="Длина"):
        solve(WbInput(length_cm=D(0)))
    with pytest.raises(WbInputError, match="выкупа"):
        solve(WbInput(buyout_pct=D(0)))
