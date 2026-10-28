"""Настройки программы (QSettings) — одно место, чтобы тесты могли их подменить."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

SETTINGS_ENV = "VEDCALC_SETTINGS_FILE"  # INI-файл вместо системных настроек (тесты)


def app_settings() -> QSettings:
    path = os.environ.get(SETTINGS_ENV)
    if path:
        return QSettings(path, QSettings.Format.IniFormat)
    return QSettings("vedcalc", "vedcalc")
