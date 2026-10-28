"""Справочник ТН ВЭД (tws.by) и специфические / комбинированные пошлины."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from conftest import control_shipment, run

from vedcalc.core.customs import DutyQuantityError, duty_base_quantity, duty_calc
from vedcalc.core.io import shipment_from_dict, shipment_to_dict
from vedcalc.core.models import DutyKind, DutyRate, DutyUnit, Item
from vedcalc.core.money import Money
from vedcalc.core.tnved import TariffParseError, load_directory, parse_tariff
from vedcalc.core.units import Unit
from vedcalc.core.validation import InputError

DIR = load_directory()


@pytest.mark.parametrize(
    ("text", "kind", "percent", "amount", "currency", "unit", "per"),
    [
        ("5%", DutyKind.AD_VALOREM, "5", None, None, None, "1"),
        ("6.5%", DutyKind.AD_VALOREM, "6.5", None, None, None, "1"),
        ("50%, но не менее 1 EUR за 1 кг", DutyKind.COMBINED, "50", "1", "EUR", "kg", "1"),
        ("0.06 EUR за 1 кг", DutyKind.SPECIFIC, "0", "0.06", "EUR", "kg", "1"),
        ("10%, но не менее 0.046 EUR за 1 л", DutyKind.COMBINED, "10", "0.046", "EUR", "l", "1"),
        ("250 USD за 1 т", DutyKind.SPECIFIC, "0", "250", "USD", "t", "1"),
        ("0.75 EUR за 1 пар", DutyKind.SPECIFIC, "0", "0.75", "EUR", "pair", "1"),
        ("20%, но не менее 0.8 EUR за 1 см3, мл", DutyKind.COMBINED, "20", "0.8", "EUR", "cm3",
         "1"),
        ("0.38 EUR за 1 м2", DutyKind.SPECIFIC, "0", "0.38", "EUR", "m2", "1"),
        ("4.5% плюс 0.02 EUR за 1 кг", DutyKind.SUM, "4.5", "0.02", "EUR", "kg", "1"),
        ("2 EUR за 1 1000 шт", DutyKind.SPECIFIC, "0", "2", "EUR", "pcs", "1000"),
        ("17%, но не менее 1.7 EUR за 1 1000 шт", DutyKind.COMBINED, "17", "1.7", "EUR", "pcs",
         "1000"),
        ("2.86 EUR за 1 шт", DutyKind.SPECIFIC, "0", "2.86", "EUR", "pcs", "1"),
    ],
)  # fmt: skip
def test_parse_all_patterns(text, kind, percent, amount, currency, unit, per):
    r = parse_tariff(text)
    assert r.kind is kind and r.percent == D(percent) and r.source == text
    if amount:
        assert r.specific == Money(D(amount), currency)
        assert r.specific_unit is DutyUnit(unit) and r.per_quantity == D(per)


def test_empty_and_broken_tariffs():
    assert parse_tariff("").percent == 0
    with pytest.raises(TariffParseError):
        parse_tariff("какая-то ставка")


def test_bundled_directory_parses_completely():
    assert len(DIR.entries) > 13_000
    parsed = [entry.duty for entry in DIR.entries.values()]  # без исключений
    assert len(parsed) == len(DIR.entries)
    meat = DIR.get("0201100008")
    assert meat.tariff == "50%, но не менее 1 EUR за 1 кг"
    assert DIR.details(meat).startswith("https://www.tws.by/tws/tnved/code/")


def test_search_by_words_and_code_prefix():
    found = DIR.search("аккумуляторы СВИНЦОВЫЕ")
    assert found and all("свинцов" in e.name.lower() for e in found)
    assert all(e.code.startswith("8507") for e in DIR.search("8507"))
    assert DIR.search(" ") == []


def test_stale_reminder():
    assert DIR.stale_warning(DIR.actual_date + timedelta(days=10)) is None
    warning = DIR.stale_warning(DIR.actual_date + timedelta(days=40))
    assert warning and "Обновите программу" in warning


# --- расчёт пошлины ----------------------------------------------------------------------


def item(**kw) -> Item:
    kw.setdefault("quantity", D(1))
    return Item("Товар", Money.rub(1), **kw)


def test_quantity_sources():
    kg = parse_tariff("0.06 EUR за 1 кг")
    assert duty_base_quantity(kg, item(net_weight_kg=D(120))) == (D(120), "вес нетто")
    assert duty_base_quantity(kg, item(unit=Unit.T, quantity=D(2)))[0] == D(2000)
    with pytest.raises(DutyQuantityError, match="вес нетто"):
        duty_base_quantity(kg, item())
    t = parse_tariff("250 USD за 1 т")
    assert duty_base_quantity(t, item(net_weight_kg=D(5000)))[0] == D(5)
    pcs = parse_tariff("2 EUR за 1 1000 шт")
    assert duty_base_quantity(pcs, item(quantity=D(30000)))[0] == D(30000)
    pairs = parse_tariff("0.75 EUR за 1 пар")
    with pytest.raises(DutyQuantityError, match="пар"):
        duty_base_quantity(pairs, item())
    assert duty_base_quantity(pairs, item(duty_quantity=D(400)))[0] == D(400)


@pytest.mark.parametrize(
    ("text", "cv", "kw", "rate", "expected"),
    [
        ("0.06 EUR за 1 кг", "100000", {"net_weight_kg": D(1000)}, "95.10", "5706.00"),
        ("250 USD за 1 т", "100000", {"net_weight_kg": D(2000)}, "82.25", "41125.00"),
        ("2 EUR за 1 1000 шт", "1", {"quantity": D(30000)}, "95.10", "5706.00"),
        # комбинированная — большая из двух
        ("50%, но не менее 1 EUR за 1 кг", "100000", {"net_weight_kg": D(1000)}, "95.10",
         "95100.00"),
        ("50%, но не менее 1 EUR за 1 кг", "300000", {"net_weight_kg": D(1000)}, "95.10",
         "150000.00"),
        # «плюс» — сумма
        ("4.5% плюс 0.02 EUR за 1 кг", "100000", {"net_weight_kg": D(1000)}, "95.10", "6402.00"),
    ],
)  # fmt: skip
def test_duty_amounts(text, cv, kw, rate, expected):
    calc = duty_calc(parse_tariff(text), D(cv), item(**kw), D(rate))
    assert calc.amount == D(expected)
    assert calc.formula.endswith(f"{expected.split('.')[0][-3:]},{expected.split('.')[1]}")


def test_combined_duty_in_full_calculation(scale):
    s = control_shipment()
    s.items[0].tnved_code = "0201100008"
    s.items[0].duty = DIR.get("0201100008").duty
    with pytest.raises(InputError) as e:
        run(s, scale)
    assert e.value.issues[0].path == "items[0].net_weight_kg"
    s.items[0].net_weight_kg = D(1000)  # 1 EUR × 1 000 кг × 95,10 = 95 100 > 50% × 130 000
    cost = run(s, scale)
    assert cost.duty == D("95100.00")
    assert "EUR" in cost.rates  # курс евро подтянут для специфической части
    step = next(x for x in cost.steps if x.title.startswith("Пошлина"))
    assert "ЕТТ" in step.title and "большая из" in step.formula


def test_duty_fields_saved_in_file():
    s = control_shipment()
    s.items[0].duty = parse_tariff("0.75 EUR за 1 пар")
    s.items[0].duty_quantity = D(400)
    loaded = shipment_from_dict(shipment_to_dict(s))
    assert loaded.items[0].duty == s.items[0].duty
    assert loaded.items[0].duty_quantity == D(400)


def test_manual_rate_label():
    r = DutyRate(DutyKind.COMBINED, D(10), Money("2", "EUR"), DutyUnit.L)
    assert r.label == "10%, но не менее 2 EUR за л"
