"""«Контейнер» ↔ «Импорт»: упаковка позиций, укладка партии, фрахт «ставка × N»."""

from decimal import Decimal as D

import pytest
from conftest import TEST_RATES, run
from test_costing import two_items

from vedcalc.core.containers import (
    cargo_kinds,
    estimate_load,
    freight_containers,
    freight_line,
    with_freight,
)
from vedcalc.core.fees import load_fee_scale
from vedcalc.core.io import shipment_from_dict, shipment_to_dict
from vedcalc.core.models import ContainerFreight, CostKind, DeliveryMode, Packaging
from vedcalc.core.money import Money
from vedcalc.core.validation import validate_shipment

BOX = Packaging(D(60), D(40), D(40), D(18), D(1))  # 1 насос в коробке 60×40×40, 18 кг
PALLET = Packaging(D(120), D(80), D(100), D(500), D("0.5"))  # 0,5 т фитингов на паллете


def packed(mode=DeliveryMode.DC20):
    s = two_items()  # 10 шт насосов и 2 т фитингов
    s.delivery_mode = mode
    s.items[0].packaging = BOX
    s.items[1].packaging = PALLET
    return s


def test_places_round_up():
    assert BOX.places(D(10)) == 10
    assert Packaging(D(10), D(10), D(10), D(1), D(12)).places(D(25)) == 3
    assert PALLET.places(D(2)) == 4
    assert PALLET.place_volume_m3 == D("0.96")


def test_load_by_packing():
    s = packed()
    kinds = cargo_kinds(s)
    assert [(k.name, k.length_mm, k.quantity, k.weight_kg) for k in kinds] == [
        ("Насосы", 600, 10, D(18)), ("Фитинги", 1200, 4, D(500)),
    ]  # fmt: skip
    load = estimate_load(s)
    assert load.packed and load.containers_needed == 1 and load.fits
    assert load.weight_kg == D(2180) and load.volume_m3 == D("4.800")


def test_packing_needs_more_containers_than_volume():
    s = packed()
    s.items[1].quantity = D(25)  # 50 паллет: по объёму 48 м³, по укладке в 20' — 5 контейнеров
    load = estimate_load(s)
    assert load.packed and load.containers_needed > 1 and not load.fits


def test_without_full_packaging_falls_back():
    s = packed()
    s.items[1].packaging = None
    load = estimate_load(s)
    assert not load.packed and load.packing is None
    assert load.missing_packaging == ("Фитинги",)
    assert load.weight_kg == D(300) + D(2100)  # веса брутто позиций


def test_freight_line_counts_containers():
    s = packed()
    assert freight_line(s) is None  # ставка не задана
    s.freight = ContainerFreight(Money(D(3500), "USD"))
    line = freight_line(s)
    assert line.amount == Money(D(3500), "USD") and line.in_customs_value
    assert line.kind is CostKind.DELIVERY_TO_BORDER and line.vat_rate == 0
    s.items[1].quantity = D(25)
    n = estimate_load(s).containers_needed
    assert freight_containers(s) == n and freight_line(s).amount.amount == 3500 * n
    s.freight.containers = 2  # вручную
    assert freight_line(s).name.startswith("Фрахт за контейнер: 2 × 3")
    # Не контейнер — фрахт не добавляется
    s.delivery_mode = DeliveryMode.AIR
    assert freight_line(s) is None and with_freight(s) is s


def test_freight_in_cost_and_currency():
    base = packed()
    with_rate = packed()
    with_rate.freight = ContainerFreight(Money(D(1000), "EUR"))
    assert "EUR" in with_rate.currencies()
    scale = load_fee_scale()
    c0, c1 = run(base, scale), run(with_rate, scale)
    # 1000 EUR в таможенной стоимости: себестоимость выросла больше, чем на сам фрахт (пошлина)
    eur = TEST_RATES.get_rates(base.calc_date).rates["EUR"]
    assert c1.cost_net - c0.cost_net > D(1000) * eur.value / eur.nominal
    assert len(with_rate.costs) == len(base.costs)  # исходная партия не меняется


def test_io_roundtrip_and_old_files():
    s = packed()
    s.freight = ContainerFreight(Money(D(3500), "USD"), 3)
    data = shipment_to_dict(s)
    assert shipment_from_dict(data) == s
    del data["shipment"]["freight"]
    for item in data["shipment"]["items"]:
        del item["packaging"]
    old = shipment_from_dict(data)
    assert old.freight == ContainerFreight() and old.items[0].packaging is None


def test_validation():
    s = packed()
    s.items[0].packaging = Packaging(D(0), D(40), D(40), D(18), D(1))
    s.freight = ContainerFreight(Money(D(-1), "USD"), 0)
    paths = {i.path for i in validate_shipment(s)}
    assert {"items[0].packaging", "freight.rate", "freight.containers"} <= paths


def test_whatif_scales_freight_rate():
    from vedcalc.core.basis import basis_from_cost
    from vedcalc.core.report import build_cost_report, build_price_report
    from vedcalc.core.whatif import sensitivity

    s = packed()
    s.costs = [c for c in s.costs if c.kind is not CostKind.DELIVERY_TO_BORDER]
    s.costs = [c for c in s.costs if c.kind is not CostKind.DELIVERY_RF]
    s.freight = ContainerFreight(Money(D(3500), "USD"))
    cost = run(s, load_fee_scale())
    report = build_price_report(basis_from_cost(cost, s.sale), s.financing, "x",
                                build_cost_report(s, cost))  # fmt: skip
    (freight,) = [t for t in sensitivity(report).tables if t.title == "Фрахт"]
    rows = {r.scenario.label: r for r in freight.rows}
    assert rows["+20%"].cost_net > rows["текущий"].cost_net > rows["−20%"].cost_net


# --- экран ------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from vedcalc.gui.main_window import MainWindow
    from vedcalc.modules.container_calc import module_spec

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow(TEST_RATES, threaded=False, extra_modules=[module_spec(threaded=False)])
    qtbot.addWidget(w)
    return w


def test_form_reads_packaging_and_freight(window):
    s = packed()
    s.freight = ContainerFreight(Money(D(3500), "USD"))
    window._load(s, None)
    form = window.calculator.form
    assert form.items_table.rows[0]["pack"].text() == "10 мест"
    assert form.items_table.rows[1]["pack"].text() == "4 места"
    read, issues = form.read()
    assert not issues and read.items[1].packaging == PALLET and read.freight == s.freight
    report = window.calculator.recalc()
    assert report.load.packed
    assert "1 × 3" in form.freight_info.text() and "по укладке" in form.freight_info.text()
    card = window.calculator.results.load_card
    assert card.packing_button.isVisible() or card.packing_button.isVisibleTo(card)
    # Количество меняется — число мест в кнопке тоже
    form.items_table.rows[0]["quantity"].setText("25")
    assert form.items_table.rows[0]["pack"].text() == "25 мест"
    # Не контейнер — поля фрахта недоступны
    form.delivery_combo.set_value(DeliveryMode.AIR)
    assert not form.freight_amount.isEnabled()


def test_manual_container_count(window):
    s = packed()
    s.freight = ContainerFreight(Money(D(1000), "USD"))
    window._load(s, None)
    window.calculator.form.freight_count.setValue(3)
    window.calculator.recalc()
    assert "3 × 1" in window.calculator.form.freight_info.text()
    assert "вручную" in window.calculator.form.freight_info.text()


def test_packaging_dialog():
    pytest.importorskip("PySide6")
    from vedcalc.core.units import Unit
    from vedcalc.gui.packaging import PackagingDialog

    dialog = PackagingDialog(None, D(25), Unit.PCS)
    assert not dialog.remove_button.isEnabled() and dialog.result.text() == ""
    dialog.accept()  # пустые поля — не принимает
    assert dialog.packaging is None and dialog.result is not None
    for w, text in ((dialog.length, "60"), (dialog.width, "40"), (dialog.height, "40"),
                    (dialog.gross, "18"), (dialog.units, "12"), (dialog.layers, "3")):  # fmt: skip
        w.setText(text)
    assert dialog.result.text().startswith("3 места ")
    dialog.accept()
    assert dialog.packaging == Packaging(D(60), D(40), D(40), D(18), D(12), max_layers=3)
    again = PackagingDialog(dialog.packaging, D(25), Unit.PCS)
    assert again.remove_button.isEnabled()
    again.remove()
    assert again.packaging is None


def test_show_packing_opens_container_tab(window):
    window._load(packed(), None)
    window.calculator.recalc()
    window.show_packing()
    tab = window.module_tabs["container"]
    assert window.tabs.currentWidget() is tab and window.dirty
    assert tab.spec_combo.value() == "20DC"
    rows = [(r["name"].text(), r["length"].text(), r["quantity"].text()) for r in tab.table.rows]
    assert rows == [("Насосы", "60", "10"), ("Фитинги", "120", "4")]
    result = tab.recalc()
    assert result.containers_needed == 1 and result.complete
