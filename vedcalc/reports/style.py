"""Палитра и шрифты оформления — в духе приложения Claude для macOS.

Общие для GUI (Qt Style Sheets) и отчётов (HTML для экрана и PDF), без зависимости от Qt.
"""

from __future__ import annotations

# Цвета: тёплый «бумажный» фон, белые карточки, глиняный акцент.
BG = "#FAF9F5"  # фон окна
SURFACE = "#FFFFFF"  # карточки, поля ввода
SURFACE_ALT = "#F4F2EC"  # заголовки таблиц, кнопки, выделенные строки
CODE_BG = "#F0EEE6"  # плашка формулы (как инлайн-код в Claude)
BORDER = "#E5E2D9"
BORDER_STRONG = "#D5D1C5"
TEXT = "#1F1E1C"
TEXT_MUTED = "#76746D"
TEXT_FAINT = "#A3A097"
ACCENT = "#C96442"  # глиняный оранжевый
ACCENT_HOVER = "#B3573A"
ACCENT_SOFT = "#F6E7E0"
LINK = "#2F6BCF"
POSITIVE = "#2F7D4F"
NEGATIVE = "#B3261E"
DANGER_SOFT = "#FCEDEB"
WARN = "#8A5300"
WARN_SOFT = "#FDF3E2"
INFO_SOFT = "#F1EFE8"

# Шрифты: системный SF на macOS, моноширинный — для формул.
UI_FONTS = "'.AppleSystemUIFont', 'SF Pro Text', 'Helvetica Neue', 'Segoe UI', Arial, sans-serif"
MONO_FONTS = "Menlo, Consolas, 'DejaVu Sans Mono', monospace"
