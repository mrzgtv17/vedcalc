"""Вкладка «Контейнер»: ввод мест, результат, сравнение типов, ошибки."""

from decimal import Decimal as D

import pytest

pytest.importorskip("PySide6")

from conftest import TEST_RATES
from PySide6.QtWidgets import QLabel

from vedcalc.gui.main_window import MainWindow
from vedcalc.modules.container_calc.gui import ContainerTab
from vedcalc.modules.container_calc.packing import MAX_KINDS, CargoKind


@pytest.fixture
def tab(qtbot):
    t = ContainerTab(threaded=False)
    qtbot.addWidget(t)
    t.table.remove_row(t.table.rows[0])
    return t


def euro_pallets(tab, qty=40):
    tab.add_kind(CargoKind("Европаллета", 1200, 800, 1000, D(300), qty, stackable=False))
    tab.recalc()


def test_tabs_in_main_window(qtbot):
    w = MainWindow(TEST_RATES, threaded=False)
    qtbot.addWidget(w)
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert titles == ["Импорт", "Цена продажи", "Контейнер", "WB"]


def test_europallets_in_40dc(tab):
    tab.spec_combo.set_value("40DC")
    euro_pallets(tab)
    r = tab.result
    assert [c.count for c in r.containers] == [25, 15]
    assert "Нужно контейнеров: 2 × 40' DC" in tab.summary.text()
    card = tab.cards.itemAt(0).widget()
    assert card.volume_bar.format().startswith("35%")  # 25 × 0,96 = 24 м³ из 67,72 м³
    # 40 паллет: 40' DC, 40' HC и 45' — по 2 контейнера; лучший — с меньшим объёмом
    assert tab.best_text() == "40DC"
    best_label = tab.findChild(QLabel, f"cmp:{tab.best_text()}:3")
    assert best_label.text() == "★ лучший"


def test_input_in_centimetres_and_errors(tab):
    tab.add_kind()
    row = tab.table.rows[0]
    row["name"].setText("Ящик")
    for key, value in (("length", "120"), ("width", "100"), ("height", "80"),
                       ("weight", "400"), ("quantity", "70")):  # fmt: skip
        row[key].setText(value)
    tab.spec_combo.set_value("40HC")
    tab.recalc()
    assert tab.result.containers[0].count == 65  # упирается в 26 т
    assert "ограничивает вес" in tab.summary.text()
    row["quantity"].setText("2,5")
    tab.recalc()
    assert tab.result is None and "целое" in tab.status.text()


def test_item_that_does_not_fit(tab):
    tab.add_kind(CargoKind("Труба", 400, 400, 2800, D(50), 4))
    tab.recalc()
    assert "кантование" in tab.status.text()
    tab.table.rows[0]["tip"].setChecked(True)
    tab.recalc()
    assert tab.result.complete and not tab.status.isVisible()


def test_max_twenty_kinds(tab):
    for n in range(MAX_KINDS + 3):
        tab.add_kind(CargoKind(f"К{n}", 300, 300, 300, D(5), 2))
    assert len(tab.table.rows) == MAX_KINDS
    assert not tab.add_button.isEnabled()
    tab.recalc()
    assert tab.result.complete


def test_wb_tab(qtbot):
    from vedcalc.modules.wb_unit.calc import Target, TaxMode
    from vedcalc.modules.wb_unit.gui import WbTab

    tab = WbTab()
    qtbot.addWidget(tab)
    nbsp = " "
    assert tab.profit_value.text() == f"284{nbsp}76".replace(nbsp, nbsp)[:0] + "284,76"
    assert tab.line_text("Логистика на продажу") == "148,00"
    assert tab.findChild(QLabel, "cmp:fbs:3").text() == "255,00"
    tab.target_combo.set_value(Target.MARGIN)
    tab._fields["target_value"].setText("25")
    tab.recalc()
    assert not tab.retail_edit.isEnabled()
    assert tab.result.retail_price == tab._fields["retail_price"].value()
    tab.tax_combo.set_value(TaxMode.INCOME_MINUS_EXPENSES)
    assert tab._fields["tax_rate_pct"].text() == "15"
    tab._fields["length_cm"].setText("0")
    tab.recalc()
    assert tab.result is None and "Длина" in tab.status.text()
