"""«Что если»: курс, фрахт, пошлина при неизменной цене продажи; блок на экране и экспорт."""

from decimal import Decimal as D

import pytest
from conftest import TEST_RATES, run
from test_costing import two_items

from vedcalc.core.basis import basis_from_cost
from vedcalc.core.fees import load_fee_scale
from vedcalc.core.models import CostKind, DutyKind, DutyRate, DutyUnit, Financing, PriceMode
from vedcalc.core.money import Money, round_money
from vedcalc.core.report import build_cost_report, build_price_report
from vedcalc.core.whatif import FX_STEPS, Scenario, run_scenario, sensitivity


def report_for(shipment, financing=None):
    shipment.sale.target_mode = PriceMode.MARKUP
    shipment.sale.target_value = D(20)
    cost = run(shipment, load_fee_scale())
    basis = basis_from_cost(cost, shipment.sale)
    return build_price_report(
        basis, financing or Financing(), shipment.name, build_cost_report(shipment, cost)
    )


@pytest.fixture
def report():
    return report_for(two_items())


def rows(whatif, title):
    (table,) = [t for t in whatif.tables if t.title == title]
    return {r.scenario.label: r for r in table.rows}


def test_base_rows_match_current_calculation(report):
    w = sensitivity(report)
    assert [t.title for t in w.tables] == ["Курс валют", "Фрахт", "Ставка пошлины"]
    assert w.price_net == report.target.price_net and w.unit_label is None
    for table in w.tables:
        base = rows(w, table.title)["текущий"]
        assert base.scenario.is_base
        assert base.cost_net == report.target.cost_net
        assert base.net_profit == report.target.net_profit and base.profit_delta == 0
        assert base.margin == report.target.margin


def test_fx_moves_cost_and_profit_at_fixed_price(report):
    fx = rows(sensitivity(report), "Курс валют")
    assert len(fx) == len(FX_STEPS)
    costs = [r.cost_net for r in fx.values()]
    profits = [r.net_profit for r in fx.values()]
    assert costs == sorted(costs) and profits == sorted(profits, reverse=True)
    # Себестоимость пересчитана целиком: курс +10% = расчёт с курсом CNY ×1,1
    s = two_items()
    rates = TEST_RATES.get_rates(s.calc_date).rates
    s.manual_rates = {c: rates[c].value / rates[c].nominal * D("1.1") for c in ("CNY", "USD")}
    expected = run(s, load_fee_scale()).cost_net
    assert fx["+10%"].cost_net == expected
    assert fx["+10%"].profit_delta == fx["+10%"].net_profit - report.target.net_profit < 0


def test_freight_only_delivery_costs(report):
    shipment = report.cost_report.shipment
    freight = sum(c.amount.amount for c in shipment.costs
                  if c.kind in (CostKind.DELIVERY_TO_BORDER, CostKind.DELIVERY_RF))  # fmt: skip
    assert freight > 0
    fr = rows(sensitivity(report), "Фрахт")
    assert fr["+20%"].cost_net > fr["текущий"].cost_net > fr["−20%"].cost_net
    # Исходный расчёт не изменился
    assert report.cost_report.shipment.costs[0].amount == shipment.costs[0].amount


def test_no_freight_and_rub_only_notes():
    s = two_items()
    s.costs = [c for c in s.costs if c.kind not in (CostKind.DELIVERY_TO_BORDER,
                                                       CostKind.DELIVERY_RF)]  # fmt: skip
    for item in s.items:
        item.unit_price = Money(item.unit_price.amount, "RUB")
    for line in s.costs:
        line.amount = Money(line.amount.amount, "RUB")
    w = sensitivity(report_for(s))
    assert [t.title for t in w.tables] == ["Ставка пошлины"]
    assert any("курс" in n for n in w.notes) and any("доставк" in n for n in w.notes)


def test_duty_shift_and_custom_rate(report):
    w = sensitivity(report, duty_custom=D(0))
    duty = rows(w, "Ставка пошлины")
    assert list(duty) == ["−5 п. п.", "текущий", "+5 п. п.", "0% для всех"]
    assert duty["+5 п. п."].net_profit < duty["текущий"].net_profit < duty["−5 п. п."].net_profit
    assert duty["0% для всех"].cost_net < duty["текущий"].cost_net


def test_duty_never_negative_and_specific_kept():
    s = two_items()
    s.items[0].duty = DutyRate.ad_valorem(3)
    s.items[1].duty = DutyRate(DutyKind.SPECIFIC, D(0), Money(D(1), "EUR"), DutyUnit.PCS, D(1))
    s.items[1].duty_quantity = D(100)
    report = report_for(s)
    minus = run_scenario(report, Scenario("x", duty_pp=D(-5)), load_fee_scale())
    zero = run_scenario(report, Scenario("y", duty_custom=D(0)), load_fee_scale())
    # −5 п. п. от 3% — это 0%, специфическая пошлина второй позиции осталась
    assert minus.cost_net > zero.cost_net


def test_unit_cost_for_single_item():
    s = two_items()
    s.items = s.items[:1]
    report = report_for(s)
    w = sensitivity(report)
    base = rows(w, "Курс валют")["текущий"]
    item = report.basis.items[0]
    assert w.unit_label == s.items[0].unit.label
    assert base.unit_cost == round_money(item.cost_net / item.quantity)
    assert w.cost_header.startswith("Себестоимость 1 ")


def test_not_available_without_import_or_price(report):
    from dataclasses import replace

    assert sensitivity(replace(report, cost_report=None)) is None
    assert sensitivity(replace(report, target=None)) is None


# --- экран и экспорт ----------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox

    from vedcalc.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow(TEST_RATES, threaded=False, extra_modules=[])
    qtbot.addWidget(w)
    w._load(two_items(), None)
    w.tabs.setCurrentWidget(w.pricing)
    w.pricing.recalc()
    return w


def test_view_on_pricing_tab(window):
    view = window.pricing.results.whatif_view
    assert not view.isHidden() and len(view.whatif.tables) == 3
    from PySide6.QtWidgets import QLabel

    base = view.findChild(QLabel, "whatif:Курс валют:4:2")  # строка «текущий», прибыль
    assert base.text().replace("\xa0", " ").replace(" ", " ") == (
        f"{window.pricing.report.target.net_profit:,.2f}".replace(",", " ").replace(".", ",")
    )
    view.custom_duty.setText("0")
    assert rows(view.whatif, "Ставка пошлины")["0% для всех"]
    view.custom_duty.setText("-1")
    assert "0% для всех" not in rows(view.whatif, "Ставка пошлины")
    # Ручная себестоимость — блок скрыт
    from vedcalc.core.models import PricingSource

    window.pricing.source_combo.set_value(PricingSource.MANUAL)
    window.pricing.m_name.setText("Товар")
    window.pricing.m_qty.setText("10")
    window.pricing.m_cost.setText("100")
    window.pricing.recalc()
    assert view.whatif is None and view.isHidden()


def test_exports_include_whatif(window, tmp_path):
    from openpyxl import load_workbook

    window.pricing.results.whatif_view.custom_duty.setText("0")
    pdf, xlsx = tmp_path / "p.pdf", tmp_path / "p.xlsx"
    window.export_pdf(pdf)
    window.export_xlsx(xlsx)
    assert pdf.read_bytes()[:5] == b"%PDF-"
    ws = load_workbook(xlsx)["Что если"]
    labels = [ws.cell(r, 1).value for r in range(1, ws.max_row + 1)]
    assert {"Курс валют", "Фрахт", "Ставка пошлины", "0% для всех", "текущий"} <= set(labels)
    # На вкладке «Импорт» листа «Что если» нет
    window.tabs.setCurrentWidget(window.calculator)
    window.export_xlsx(xlsx)
    assert "Что если" not in load_workbook(xlsx).sheetnames
