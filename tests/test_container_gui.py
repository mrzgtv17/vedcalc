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


def test_tab_is_in_main_window(qtbot):
    w = MainWindow(TEST_RATES, threaded=False)
    qtbot.addWidget(w)
    titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert titles == ["Импорт", "Цена продажи", "Контейнер"]


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
