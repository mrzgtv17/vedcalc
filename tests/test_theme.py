"""Тёмная тема: выбор в меню, перекраска на лету, отчёты остаются светлыми."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from conftest import TEST_RATES
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from vedcalc.gui import theme
from vedcalc.gui.main_window import MainWindow
from vedcalc.gui.prefs import app_settings
from vedcalc.reports.html import render_cost_html
from vedcalc.reports.style import DARK, LIGHT


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    w = MainWindow(TEST_RATES, threaded=False, extra_modules=[])
    qtbot.addWidget(w)
    yield w
    theme.set_mode(theme.SYSTEM)  # остальные тесты — в светлой теме (offscreen = светлая)


def test_palettes_have_same_fields_and_differ():
    assert LIGHT.dark is False and DARK.dark is True
    assert LIGHT.bg != DARK.bg and LIGHT.text != DARK.text


def test_system_mode_is_default_and_light_offscreen(window):
    assert theme.saved_mode() == theme.SYSTEM
    assert window.theme_actions[theme.SYSTEM].isChecked()
    assert theme.current() is LIGHT


def test_menu_switches_theme_and_saves_choice(window):
    window.theme_actions[theme.DARK_MODE].trigger()
    assert theme.current() is DARK
    assert app_settings().value(theme.SETTINGS_KEY) == theme.DARK_MODE
    assert DARK.bg in QApplication.instance().styleSheet()
    assert theme.icon_path("chevron-down").name == "chevron-down-dark.svg"
    window.theme_actions[theme.LIGHT_MODE].trigger()
    assert theme.current() is LIGHT
    assert LIGHT.bg in QApplication.instance().styleSheet()


def test_details_recolor_without_recalc(window):
    window.open_example()
    details = window.calculator.results.details
    assert LIGHT.text_muted.lower() in details.toHtml().lower()
    theme.set_mode(theme.DARK_MODE)
    assert DARK.text_muted.lower() in details.toHtml().lower()


def test_pdf_report_stays_light(window):
    window.open_example()
    theme.set_mode(theme.DARK_MODE)
    html = render_cost_html(window.calculator.report)
    assert f"color: {LIGHT.text}" in html and DARK.text not in html


def test_tones_follow_theme(window):
    label = QLabel("Ошибка")
    theme.set_tone(label, theme.ERROR_BOX)
    assert label.property("tone") == "error"
    theme.set_tone(label, None)
    assert label.property("tone") == ""
    window.open_example()
    status = window.calculator.results.status
    window.calculator.form.items_table.rows[0]["quantity"].setText("abc")
    window.calculator.recalc()
    assert status.property("tone") == theme.ERROR_BOX
