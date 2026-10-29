"""Тема оформления приложения (Qt Style Sheets) в духе Claude для macOS.

Светлая и тёмная палитры; по умолчанию тема следует настройке macOS и переключается
на лету. Цвета статусов и подписей задаются «тоном» (set_tone), а не готовым стилем, —
тогда смена темы перекрашивает их без пересчёта.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from vedcalc.gui.prefs import app_settings
from vedcalc.reports.style import DARK, LIGHT, Palette

ASSETS = Path(__file__).parent / "assets"
BASE_POINT_SIZE = 13

SYSTEM, LIGHT_MODE, DARK_MODE = "system", "light", "dark"
MODES = {SYSTEM: "Как в системе", LIGHT_MODE: "Светлая", DARK_MODE: "Тёмная"}
SETTINGS_KEY = "ui/theme"

# Тона рамок статуса и полей (значения для set_tone)
ERROR_BOX = "error"
WARN_BOX = "warn"
INFO_BOX = "info"
FIELD_ERROR = "fieldError"


class _Notifier(QObject):
    changed = Signal()


_state: dict = {"palette": LIGHT, "notifier": None, "hooked": False}


def current() -> Palette:
    """Палитра текущей темы — для виджетов, которые рисуют сами (графики, схемы)."""
    return _state["palette"]


def notifier() -> _Notifier:
    """Сигнал changed — тема сменилась; виджетам с «запечённым» цветом пора перерисоваться."""
    if _state["notifier"] is None:
        _state["notifier"] = _Notifier()
    return _state["notifier"]


def icon_path(name: str) -> Path:
    """Иконка под текущую тему (у тёмной — светлый штрих)."""
    dark = ASSETS / f"{name}-dark.svg"
    return dark if current().dark and dark.exists() else ASSETS / f"{name}.svg"


def set_tone(widget: QWidget, tone: str | None) -> None:
    """Задаёт тон виджета (error, warn, info, pos, neg, muted, …) и обновляет стиль."""
    if (widget.property("tone") or "") == (tone or ""):
        return
    widget.setProperty("tone", tone or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def saved_mode() -> str:
    mode = app_settings().value(SETTINGS_KEY, SYSTEM)
    return mode if mode in MODES else SYSTEM


def system_is_dark(app: QApplication) -> bool:
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def resolve(mode: str, app: QApplication) -> Palette:
    if mode == DARK_MODE or (mode == SYSTEM and system_is_dark(app)):
        return DARK
    return LIGHT


def _qss(pal: Palette) -> str:
    chevron = icon_path("chevron-down").as_posix()
    check = (ASSETS / "check.svg").as_posix()
    return f"""
QMainWindow, QDialog {{ background: {pal.bg}; }}
QWidget {{ color: {pal.text}; }}
QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {pal.bg}; }}
QMenuBar {{ background: {pal.bg}; }}
QToolTip {{
    background: {pal.tooltip_bg}; color: {pal.tooltip_text}; border: none; border-radius: 6px; padding: 6px 8px;
}}

/* Карточки */
QGroupBox, QFrame#card {{
    background: {pal.surface}; border: 1px solid {pal.border}; border-radius: 12px;
}}
QGroupBox {{ margin-top: 24px; padding: 6px 12px 12px 12px; font-weight: 600; }}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 6px; top: 2px;
    padding: 0 2px; color: {pal.text}; font-size: 14px;
}}
QGroupBox QLabel {{ font-weight: 400; }}
QLabel#muted {{ color: {pal.text_muted}; }}
QLabel#caption {{ color: {pal.text_muted}; font-size: 12px; }}
QLabel#cardTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#chip {{
    background: {pal.accent_soft}; color: {pal.accent_hover}; border-radius: 9px; padding: 2px 9px;
    font-size: 12px; font-weight: 600;
}}
QLabel#metricValue {{ font-size: 22px; font-weight: 600; }}
QLabel#metricSub {{ color: {pal.text_muted}; font-size: 12px; }}
QFrame#metric {{ background: {pal.surface_alt}; border: none; border-radius: 10px; }}
QFrame#metric[accent="true"] {{ background: {pal.accent_soft}; }}
QFrame#divider {{ background: {pal.border}; border: none; max-height: 1px; min-height: 1px; }}

/* Поля ввода */
QLineEdit, QComboBox, QDateEdit {{
    background: {pal.surface}; border: 1px solid {pal.border_strong}; border-radius: 7px;
    padding: 4px 8px; min-height: 20px; selection-background-color: {pal.accent_soft};
    selection-color: {pal.text};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border: 1px solid {pal.accent}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {pal.text_faint}; background: {pal.surface_alt}; }}
QLineEdit[readOnly="true"] {{ background: {pal.surface_alt}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {pal.surface}; border: 1px solid {pal.border}; border-radius: 8px; padding: 4px;
    selection-background-color: {pal.accent_soft}; selection-color: {pal.text}; outline: none;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {pal.border_strong}; border-radius: 4px;
    background: {pal.surface};
}}
QCheckBox::indicator:checked {{ background: {pal.accent}; border-color: {pal.accent}; image: url("{check}"); }}

/* Кнопки */
QPushButton, QToolButton {{
    background: {pal.surface}; border: 1px solid {pal.border_strong}; border-radius: 8px;
    padding: 5px 12px; color: {pal.text};
}}
QPushButton:hover, QToolButton:hover {{ background: {pal.surface_alt}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {pal.code_bg}; }}
QPushButton#segment {{ padding: 4px 14px; }}
QPushButton#segment:checked {{ background: {pal.accent_soft}; border-color: {pal.accent}; color: {pal.accent_hover}; font-weight: 600; }}
QPushButton#primary {{ background: {pal.accent}; border-color: {pal.accent}; color: {pal.on_accent}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {pal.accent_hover}; border-color: {pal.accent_hover}; }}
QPushButton#primary:disabled {{ background: {pal.surface_alt}; border-color: {pal.border}; color: {pal.text_faint}; }}
QToolButton::menu-indicator {{ image: url("{chevron}"); subcontrol-position: right center; right: 6px; }}
QToolButton[popupMode="2"] {{ padding-right: 24px; }}

QToolButton#rowDelete {{ border: none; background: transparent; color: {pal.text_faint}; padding: 0; font-size: 12px; }}
QToolButton#rowDelete:hover {{ color: {pal.negative}; background: {pal.danger_soft}; border-radius: 6px; }}

QProgressBar#loadBar {{
    border: none; background: {pal.surface_alt}; border-radius: 6px; min-height: 20px;
    text-align: center; color: {pal.text}; font-size: 12px;
}}
QProgressBar#loadBar::chunk {{ background: {pal.load_ok}; border-radius: 6px; }}
QProgressBar#loadBar[over="true"]::chunk {{ background: {pal.load_over}; }}

/* Таблицы */
QTableWidget {{
    background: {pal.surface}; border: 1px solid {pal.border}; border-radius: 8px;
    gridline-color: {pal.border}; selection-background-color: {pal.accent_soft}; selection-color: {pal.text};
}}
QHeaderView::section {{
    background: {pal.surface_alt}; color: {pal.text_muted}; border: none;
    border-bottom: 1px solid {pal.border}; border-right: 1px solid {pal.border}; padding: 5px 6px;
    font-size: 12px;
}}
QTableCornerButton::section {{ background: {pal.surface_alt}; border: none; }}
QTableWidget QLineEdit, QTableWidget QComboBox {{
    border: none; border-radius: 0; background: transparent; padding: 2px 6px;
}}
QTableWidget QLineEdit:focus {{ background: {pal.accent_soft}; }}

/* Вкладки — как сегментированный переключатель */
QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar::tab {{
    background: transparent; color: {pal.text_muted}; padding: 6px 14px; margin: 0 2px 8px 0;
    border-radius: 8px; font-weight: 500;
}}
QTabBar::tab:selected {{ background: {pal.surface}; color: {pal.text}; border: 1px solid {pal.border}; }}
QTabBar::tab:hover:!selected {{ color: {pal.text}; }}

/* Детализация */
QTextBrowser {{ background: {pal.surface}; border: 1px solid {pal.border}; border-radius: 12px; padding: 6px; }}

/* Полосы прокрутки — тонкие */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {pal.border_strong}; border-radius: 3px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {pal.border_strong}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {pal.bg}; }}
QStatusBar {{ background: {pal.bg}; color: {pal.text_muted}; }}

/* Тона: виджету задаётся свойство tone (см. set_tone), цвет берётся из текущей темы */
QLabel[tone="error"] {{
    background: {pal.danger_soft}; color: {pal.negative}; border-radius: 10px; padding: 10px 12px;
}}
QLabel[tone="warn"] {{
    background: {pal.warn_soft}; color: {pal.warn}; border-radius: 10px; padding: 10px 12px;
}}
QLabel[tone="info"] {{
    background: {pal.info_soft}; color: {pal.text}; border-radius: 10px; padding: 10px 12px;
}}
QLineEdit[tone="fieldError"], QComboBox[tone="fieldError"], QDateEdit[tone="fieldError"],
QSpinBox[tone="fieldError"] {{
    border: 1px solid {pal.negative}; background: {pal.danger_soft}; border-radius: 7px;
}}
QLineEdit[tone="warnBorder"] {{ border: 1px solid {pal.warn}; }}
QLabel[tone="pos"] {{ color: {pal.positive}; }}
QLabel[tone="neg"] {{ color: {pal.negative}; }}
QLabel[tone="negStrong"] {{ color: {pal.negative}; font-weight: 600; }}
QLabel[tone="muted"] {{ color: {pal.text_muted}; }}
QLabel[tone="cell"] {{ background: {pal.surface_alt}; padding: 2px 4px; }}
QTextBrowser[tone="paper"] {{ background: #FFFFFF; color: #1F1E1C; }}
"""


def apply_theme(app: QApplication, mode: str | None = None) -> None:
    """Применяет тему: mode — system / light / dark, по умолчанию — сохранённая настройка."""
    mode = mode or saved_mode()
    pal = resolve(mode, app)
    _state["palette"] = pal
    app.setStyle("Fusion")  # предсказуемая отрисовка QSS на всех платформах
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, pal.bg),
        (QPalette.ColorRole.Base, pal.surface),
        (QPalette.ColorRole.AlternateBase, pal.surface_alt),
        (QPalette.ColorRole.Text, pal.text),
        (QPalette.ColorRole.WindowText, pal.text),
        (QPalette.ColorRole.ButtonText, pal.text),
        (QPalette.ColorRole.Button, pal.surface),
        (QPalette.ColorRole.Highlight, pal.accent_soft),
        (QPalette.ColorRole.HighlightedText, pal.text),
        (QPalette.ColorRole.PlaceholderText, pal.text_faint),
        (QPalette.ColorRole.ToolTipBase, pal.tooltip_bg),
        (QPalette.ColorRole.ToolTipText, pal.tooltip_text),
        (QPalette.ColorRole.Link, pal.link),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    font = QFont(app.font())
    font.setPointSize(BASE_POINT_SIZE)
    app.setFont(font)
    qss = _qss(pal)
    if app.styleSheet() != qss:  # повторная установка перерисовывает все виджеты — дорого
        app.setStyleSheet(qss)
    if not _state["hooked"]:
        _state["hooked"] = True
        app.styleHints().colorSchemeChanged.connect(lambda *_: _on_system_change(app))
    notifier().changed.emit()


def _on_system_change(app: QApplication) -> None:
    if saved_mode() == SYSTEM:
        apply_theme(app, SYSTEM)


def set_mode(mode: str) -> None:
    """Выбор пользователя в меню «Вид → Тема»: сохраняется и применяется сразу."""
    app_settings().setValue(SETTINGS_KEY, mode)
    apply_theme(QApplication.instance(), mode)
