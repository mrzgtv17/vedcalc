"""Тема оформления приложения (Qt Style Sheets) в духе Claude для macOS."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from vedcalc.reports.style import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_SOFT,
    BG,
    BORDER,
    BORDER_STRONG,
    CODE_BG,
    DANGER_SOFT,
    INFO_SOFT,
    NEGATIVE,
    SURFACE,
    SURFACE_ALT,
    TEXT,
    TEXT_FAINT,
    TEXT_MUTED,
    WARN,
    WARN_SOFT,
)

ASSETS = Path(__file__).parent / "assets"
BASE_POINT_SIZE = 13

# Готовые стили для рамок статуса (используются панелью результатов)
ERROR_BOX = f"background:{DANGER_SOFT}; color:{NEGATIVE}; border-radius:10px; padding:10px 12px;"
WARN_BOX = f"background:{WARN_SOFT}; color:{WARN}; border-radius:10px; padding:10px 12px;"
INFO_BOX = f"background:{INFO_SOFT}; color:{TEXT}; border-radius:10px; padding:10px 12px;"
FIELD_ERROR = f"border:1px solid {NEGATIVE}; background:{DANGER_SOFT}; border-radius:7px;"


def _qss() -> str:
    chevron = (ASSETS / "chevron-down.svg").as_posix()
    check = (ASSETS / "check.svg").as_posix()
    return f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QMenuBar {{ background: {BG}; }}
QToolTip {{
    background: {TEXT}; color: #FFFFFF; border: none; border-radius: 6px; padding: 6px 8px;
}}

/* Карточки */
QGroupBox, QFrame#card {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px;
}}
QGroupBox {{ margin-top: 24px; padding: 6px 12px 12px 12px; font-weight: 600; }}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 6px; top: 2px;
    padding: 0 2px; color: {TEXT}; font-size: 14px;
}}
QGroupBox QLabel {{ font-weight: 400; }}
QLabel#muted {{ color: {TEXT_MUTED}; }}
QLabel#caption {{ color: {TEXT_MUTED}; font-size: 12px; }}
QLabel#cardTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#chip {{
    background: {ACCENT_SOFT}; color: {ACCENT_HOVER}; border-radius: 9px; padding: 2px 9px;
    font-size: 12px; font-weight: 600;
}}
QLabel#metricValue {{ font-size: 22px; font-weight: 600; }}
QLabel#metricSub {{ color: {TEXT_MUTED}; font-size: 12px; }}
QFrame#metric {{ background: {SURFACE_ALT}; border: none; border-radius: 10px; }}
QFrame#metric[accent="true"] {{ background: {ACCENT_SOFT}; }}
QFrame#divider {{ background: {BORDER}; border: none; max-height: 1px; min-height: 1px; }}

/* Поля ввода */
QLineEdit, QComboBox, QDateEdit {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 7px;
    padding: 4px 8px; min-height: 20px; selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {TEXT_FAINT}; background: {SURFACE_ALT}; }}
QLineEdit[readOnly="true"] {{ background: {SURFACE_ALT}; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; outline: none;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER_STRONG}; border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{check}"); }}

/* Кнопки */
QPushButton, QToolButton {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 8px;
    padding: 5px 12px; color: {TEXT};
}}
QPushButton:hover, QToolButton:hover {{ background: {SURFACE_ALT}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {CODE_BG}; }}
QPushButton#segment {{ padding: 4px 14px; }}
QPushButton#segment:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT_HOVER}; font-weight: 600; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #FFFFFF; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {SURFACE_ALT}; border-color: {BORDER}; color: {TEXT_FAINT}; }}
QToolButton::menu-indicator {{ image: url("{chevron}"); subcontrol-position: right center; right: 6px; }}
QToolButton[popupMode="2"] {{ padding-right: 24px; }}

QToolButton#rowDelete {{ border: none; background: transparent; color: {TEXT_FAINT}; padding: 0; font-size: 12px; }}
QToolButton#rowDelete:hover {{ color: {NEGATIVE}; background: {DANGER_SOFT}; border-radius: 6px; }}

QProgressBar#loadBar {{
    border: none; background: {SURFACE_ALT}; border-radius: 6px; min-height: 20px;
    text-align: center; color: {TEXT}; font-size: 12px;
}}
QProgressBar#loadBar::chunk {{ background: #CFE3D6; border-radius: 6px; }}
QProgressBar#loadBar[over="true"]::chunk {{ background: #F2C4BF; }}

/* Таблицы */
QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    gridline-color: {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: {SURFACE_ALT}; color: {TEXT_MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; border-right: 1px solid {BORDER}; padding: 5px 6px;
    font-size: 12px;
}}
QTableCornerButton::section {{ background: {SURFACE_ALT}; border: none; }}
QTableWidget QLineEdit, QTableWidget QComboBox {{
    border: none; border-radius: 0; background: transparent; padding: 2px 6px;
}}
QTableWidget QLineEdit:focus {{ background: {ACCENT_SOFT}; }}

/* Вкладки — как сегментированный переключатель */
QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_MUTED}; padding: 6px 14px; margin: 0 2px 8px 0;
    border-radius: 8px; font-weight: 500;
}}
QTabBar::tab:selected {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {BORDER}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

/* Детализация */
QTextBrowser {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; padding: 6px; }}

/* Полосы прокрутки — тонкие */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER_STRONG}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {BG}; }}
QStatusBar {{ background: {BG}; color: {TEXT_MUTED}; }}
"""


def apply_theme(app: QApplication) -> None:
    """Светлая тема в духе Claude. Тёмный режим macOS пока не поддерживается."""
    app.setStyle("Fusion")  # предсказуемая отрисовка QSS на всех платформах
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BG),
        (QPalette.ColorRole.Base, SURFACE),
        (QPalette.ColorRole.AlternateBase, SURFACE_ALT),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Button, SURFACE),
        (QPalette.ColorRole.Highlight, ACCENT_SOFT),
        (QPalette.ColorRole.HighlightedText, TEXT),
        (QPalette.ColorRole.PlaceholderText, TEXT_FAINT),
        (QPalette.ColorRole.ToolTipBase, TEXT),
        (QPalette.ColorRole.ToolTipText, "#FFFFFF"),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    font = QFont(app.font())
    font.setPointSize(BASE_POINT_SIZE)
    app.setFont(font)
    app.setStyleSheet(_qss())
