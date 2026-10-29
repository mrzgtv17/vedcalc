"""Палитра и шрифты оформления — в духе приложения Claude для macOS.

Общие для GUI (Qt Style Sheets) и отчётов (HTML для экрана и PDF), без зависимости от Qt.
Две палитры: светлая (она же для PDF и печати) и тёмная — под тёмный режим macOS.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    dark: bool
    bg: str  # фон окна
    surface: str  # карточки, поля ввода
    surface_alt: str  # заголовки таблиц, кнопки, выделенные строки
    code_bg: str  # плашка формулы (как инлайн-код в Claude)
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    accent: str  # глиняный оранжевый
    accent_hover: str
    accent_soft: str
    on_accent: str  # текст на акцентной кнопке
    link: str
    positive: str
    negative: str
    danger_soft: str
    warn: str
    warn_soft: str
    info_soft: str
    tooltip_bg: str
    tooltip_text: str
    load_ok: str  # полоса загрузки контейнера
    load_over: str
    row_loss: str  # подсветка убыточной строки
    row_low: str  # подсветка строки с низкой маржой


LIGHT = Palette(
    dark=False,
    bg="#FAF9F5",
    surface="#FFFFFF",
    surface_alt="#F4F2EC",
    code_bg="#F0EEE6",
    border="#E5E2D9",
    border_strong="#D5D1C5",
    text="#1F1E1C",
    text_muted="#76746D",
    text_faint="#A3A097",
    accent="#C96442",
    accent_hover="#B3573A",
    accent_soft="#F6E7E0",
    on_accent="#FFFFFF",
    link="#2F6BCF",
    positive="#2F7D4F",
    negative="#B3261E",
    danger_soft="#FCEDEB",
    warn="#8A5300",
    warn_soft="#FDF3E2",
    info_soft="#F1EFE8",
    tooltip_bg="#1F1E1C",
    tooltip_text="#FFFFFF",
    load_ok="#CFE3D6",
    load_over="#F2C4BF",
    row_loss="#F8D7D3",
    row_low="#FFF1C7",
)

DARK = Palette(
    dark=True,
    bg="#262624",
    surface="#30302E",
    surface_alt="#3A3936",
    code_bg="#1F1E1D",
    border="#45443F",
    border_strong="#5A5852",
    text="#ECEAE3",
    text_muted="#A6A39A",
    text_faint="#77756E",
    accent="#D97757",
    accent_hover="#E8906F",
    accent_soft="#4A3128",
    on_accent="#FFFFFF",
    link="#7FA8F0",
    positive="#6BC28A",
    negative="#F07A6E",
    danger_soft="#4A2724",
    warn="#E8B163",
    warn_soft="#43361F",
    info_soft="#363531",
    tooltip_bg="#ECEAE3",
    tooltip_text="#1F1E1C",
    load_ok="#2F5A40",
    load_over="#6A2E29",
    row_loss="#5A2A26",
    row_low="#4E4022",
)

# Светлая палитра отдельными константами — для отчётов (PDF, печать).
BG = LIGHT.bg
SURFACE = LIGHT.surface
SURFACE_ALT = LIGHT.surface_alt
CODE_BG = LIGHT.code_bg
BORDER = LIGHT.border
BORDER_STRONG = LIGHT.border_strong
TEXT = LIGHT.text
TEXT_MUTED = LIGHT.text_muted
TEXT_FAINT = LIGHT.text_faint
ACCENT = LIGHT.accent
ACCENT_HOVER = LIGHT.accent_hover
ACCENT_SOFT = LIGHT.accent_soft
LINK = LIGHT.link
POSITIVE = LIGHT.positive
NEGATIVE = LIGHT.negative
DANGER_SOFT = LIGHT.danger_soft
WARN = LIGHT.warn
WARN_SOFT = LIGHT.warn_soft
INFO_SOFT = LIGHT.info_soft

# Шрифты: системный SF на macOS, моноширинный — для формул.
UI_FONTS = "'.AppleSystemUIFont', 'SF Pro Text', 'Helvetica Neue', 'Segoe UI', Arial, sans-serif"
MONO_FONTS = "Menlo, Consolas, 'DejaVu Sans Mono', monospace"
