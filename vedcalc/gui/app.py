"""Точка входа GUI."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from vedcalc.core.fx import CbrRateProvider
from vedcalc.gui.main_window import APP_TITLE, MainWindow

SMOKE_ENV = "VEDCALC_SMOKE_TEST"


def _smoke_test(window: MainWindow, app: QApplication, png_path: str) -> None:
    """Проверка собранного приложения (CI): открыть пример, сделать снимок, выйти.

    Код выхода 0 — контрольный пример посчитан, иначе 1.
    """
    window.open_example()

    def finish() -> None:
        report = window.calculator.report
        ok = report is not None and str(report.cost.cost_net) == "147731.00"
        window.grab().save(png_path)
        print(f"smoke test: {'OK' if ok else 'FAILED'}, C = {report and report.cost.cost_net}")
        window.dirty = False
        app.exit(0 if ok else 1)

    QTimer.singleShot(1500, finish)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("vedcalc")
    window = MainWindow(CbrRateProvider())
    window.show()
    if png := os.environ.get(SMOKE_ENV):
        _smoke_test(window, app, png)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
