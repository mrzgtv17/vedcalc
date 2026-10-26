"""Деньги, единицы, сохранение расчёта, независимость ядра от GUI."""

import json
import subprocess
import sys
from decimal import Decimal as D

import pytest
from conftest import control_shipment, run
from test_costing import two_items

from vedcalc.core.io import (
    FileFormatError,
    duplicate_shipment,
    load_shipment,
    save_shipment,
)
from vedcalc.core.money import Money, allocate, round_money, to_decimal
from vedcalc.core.units import Unit, convert


def test_no_floats():
    with pytest.raises(TypeError):
        to_decimal(0.1)
    with pytest.raises(TypeError):
        Money(1.5)


def test_round_half_up():
    assert round_money(D("0.005")) == D("0.01")
    assert round_money(D("2.675")) == D("2.68")


def test_money_currency_guard():
    with pytest.raises(ValueError, match="конвертации"):
        Money.rub(1) + Money("1", "USD")


def test_allocate_exact():
    assert allocate(D("1231"), [D(1), D(1), D(1)]) == [D("410.33"), D("410.33"), D("410.34")]
    assert allocate(D(10), [D(1), D(0)]) == [D("10.00"), D("0.00")]


def test_units():
    assert convert(D("2.5"), Unit.T, Unit.KG) == D(2500)
    assert convert(D(500), Unit.KG, Unit.T) == D("0.5")
    with pytest.raises(ValueError):
        convert(D(1), Unit.PCS, Unit.KG)


def test_save_load_roundtrip(tmp_path, scale):
    s = two_items()
    s.manual_rates = {"CNY": D("12.3456")}
    s.items[0].tnved_code = "8413702100"
    path = tmp_path / "calc.vedcalc.json"
    save_shipment(s, path)
    loaded = load_shipment(path)
    assert loaded == s
    assert run(loaded, scale).cost_net == run(s, scale).cost_net
    raw = json.loads(path.read_text("utf-8"))
    assert raw["format"] == "vedcalc.shipment"
    assert raw["shipment"]["items"][0]["unit_price"] == {"amount": "1000", "currency": "CNY"}


def test_load_rejects_foreign_file(tmp_path):
    p = tmp_path / "x.json"
    p.write_text('{"hello": 1}')
    with pytest.raises(FileFormatError):
        load_shipment(p)
    p.write_text("not json")
    with pytest.raises(FileFormatError):
        load_shipment(p)


def test_duplicate_is_independent():
    s = control_shipment()
    d = duplicate_shipment(s)
    d.items[0].quantity = D(5)
    assert s.items[0].quantity == 1
    assert d.name == "Контрольный пример (копия)"


def test_core_does_not_import_gui():
    code = (
        "import sys, vedcalc.core.costing, vedcalc.core.pricing, vedcalc.core.io, "
        "vedcalc.core.fx, vedcalc.providers.tnved, vedcalc.modules.registry;"
        "bad = [m for m in sys.modules if m.startswith(('PySide6', 'vedcalc.gui'))];"
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
