"""GUI: контрольный пример на экране, ввод, режимы цены, файлы, экспорт, курсы."""

from datetime import date
from decimal import Decimal as D

import pytest

pytest.importorskip("PySide6")

from conftest import TEST_RATES, StaticRates
from PySide6.QtWidgets import QMessageBox

from vedcalc.core.fx import FxError
from vedcalc.core.models import PriceMode, TaxRegime
from vedcalc.gui.main_window import MainWindow

NBSP = " "


def money(text: str) -> str:
    return text.replace(" ", NBSP)


@pytest.fixture
def window(qtbot, monkeypatch):
    # Диалоги «Сохранить изменения?» в тестах не показываем
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    w = MainWindow(TEST_RATES, threaded=False, extra_modules=[])
    qtbot.addWidget(w)
    return w


@pytest.fixture
def example(window):
    window.open_example()
    return window


def results(w):
    return w.calculator.results


def row(w, label):
    return w.calculator.form.items_table.rows[0][label]


def test_new_calculation_shows_hints_not_errors(window):
    status = results(window).status.text()
    assert "Заполните данные" in status
    assert window.calculator.report is None
    assert not window.pdf_action.isEnabled()


def test_control_example_on_screen(example):
    r = results(example)
    assert r.cost_view.value_text("Таможенная стоимость") == money("130 000,00")
    assert r.cost_view.value_text("Таможенный сбор") == money("1 231,00")
    assert r.cost_view.value_text("Ввозная таможенная пошлина") == money("6 500,00")
    assert r.cost_view.value_text("НДС при ввозе") == money("30 030,00")
    assert r.cost_view.value_text("Себестоимость партии без НДС") == money("147 731,00")
    assert r.break_even_view.value_text("Цена с НДС") == money("180 231,82")
    assert r.break_even_view.value_text("НДС-разрыв при P = C") == money("2 470,82")
    # В файле примера сохранён сценарий «продажа за 200 000»
    assert r.target_view.value_text("Чистая прибыль") == money("39 201,75")
    assert r.target_view.value_text("Наценка") == "35,38%"
    assert r.target_view.value_text("Маржинальность") == "26,13%"
    assert "Детализация расчёта" in r.details.toPlainText()
    assert example.pdf_action.isEnabled()


def test_editing_recalculates_on_the_fly(example, qtbot):
    row(example, "quantity").setText("2")
    row(example, "price").setText("50 000")
    qtbot.waitUntil(
        lambda: (
            example.calculator.report is not None
            and example.calculator.report.cost.items[0].quantity == 2
        ),
        timeout=2000,
    )
    report = example.calculator.report
    assert report.cost.cost_net == D("147731.00")
    assert report.cost.items[0].unit_cost_net == D("73865.50")
    assert example.dirty


def test_invalid_input_is_highlighted(example):
    example.calculator.touched = True
    row(example, "quantity").setText("abc")
    example.calculator.recalc()
    assert example.calculator.report is None
    assert "Проверьте ввод" in results(example).status.text()
    assert "не число" in row(example, "quantity").toolTip()
    assert "border" in row(example, "quantity").styleSheet()
    assert not example.pdf_action.isEnabled()
    row(example, "quantity").setText("1")
    example.calculator.recalc()
    assert row(example, "quantity").styleSheet() == ""


def test_price_modes(example):
    r = results(example)
    r.mode_combo.set_value(PriceMode.MARGIN)
    r.value_edit.setText("25")
    example.calculator.recalc()
    assert r.target_view.value_text("Цена без НДС") == money("196 974,67")
    assert r.target_box.title() == "Цена с маржинальностью 25%"

    r.value_edit.setText("100")
    example.calculator.recalc()
    assert "меньше 100%" in r.target_error.text()
    assert example.calculator.report is not None  # остальные блоки считаются


def test_usn_regime(example):
    form = example.calculator.form
    form.regime_combo.set_value(TaxRegime.USN_INCOME_MINUS_EXPENSES)
    example.calculator.recalc()
    assert not form.sale_vat.isEnabled()
    assert form.profit_tax.text() == "15"
    report = example.calculator.report
    assert report.cost.cost_net == D("177761.00")
    assert report.break_even.sale_vat == 0


def test_manual_rate(example):
    row(example, "currency").set_value("CNY")
    row(example, "price").setText("10000")
    example.calculator.recalc()
    assert example.calculator.report.cost.items[0].goods == D("115000.00")  # 11,5 из теста
    form = example.calculator.form
    form.manual_check.setChecked(True)
    form.manual_edits["CNY"].setText("12")
    example.calculator.recalc()
    report = example.calculator.report
    assert report.cost.items[0].goods == D("120000.00")
    assert "Курс изменён вручную" in results(example).status.text()


def test_save_open_duplicate(example, tmp_path):
    path = tmp_path / "calc.vedcalc.json"
    assert example._save_to(path)
    assert not example.dirty and example.windowTitle().startswith("calc.vedcalc.json")
    example.new_file()
    assert example.calculator.report is None
    example.open_file(path)
    assert example.calculator.report.cost.cost_net == D("147731.00")
    assert example.calculator.form.name_edit.text() == "Контрольный пример"
    example.duplicate()
    assert example.calculator.form.name_edit.text() == "Контрольный пример (копия)"
    assert example.path is None and example.dirty


def test_save_refused_with_parse_errors(example, tmp_path):
    row(example, "price").setText("сто")
    assert not example._save_to(tmp_path / "x.json")
    assert not (tmp_path / "x.json").exists()


def test_exports(example, tmp_path):
    pdf, xlsx = tmp_path / "r.pdf", tmp_path / "r.xlsx"
    example.export_pdf(pdf)
    example.export_xlsx(xlsx)
    assert pdf.read_bytes()[:5] == b"%PDF-" and pdf.stat().st_size > 5000
    from openpyxl import load_workbook

    ws = load_workbook(xlsx)["Итоги"]
    values = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert values["Себестоимость партии без НДС"] == 147731
    assert values["Чистая прибыль"] == 39201.75


def test_rates_load_in_background(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow(StaticRates({"CNY": ("11.5", 1)}), threaded=True, extra_modules=[])
    qtbot.addWidget(w)
    form = w.calculator.form
    form.items_table.rows[0]["name"].setText("Насос")
    form.items_table.rows[0]["price"].setText("1000")  # CNY по умолчанию
    qtbot.waitUntil(lambda: w.calculator.report is not None, timeout=3000)
    assert w.calculator.report.cost.items[0].goods == D("11500.00")
    assert "11,5000" in form.rates_info.text()


def test_offline_without_cache_suggests_manual_rate(qtbot, monkeypatch):
    class Offline:
        def get_rates(self, on: date):
            raise FxError("Нет связи с ЦБ РФ и нет сохранённых курсов")

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow(Offline(), threaded=False, extra_modules=[])
    qtbot.addWidget(w)
    form = w.calculator.form
    form.items_table.rows[0]["name"].setText("Насос")
    form.items_table.rows[0]["price"].setText("1000")
    w.calculator.recalc()
    assert "задайте курс вручную" in results(w).status.text()
    form.manual_check.setChecked(True)
    form.manual_edits["CNY"].setText("12,5")
    w.calculator.recalc()
    assert w.calculator.report.cost.items[0].goods == D("12500.00")
