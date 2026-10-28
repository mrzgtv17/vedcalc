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
    """Проверка собранного приложения (CI): открыть пример, сделать снимок, сохранить КП
    в PDF и DOCX рядом со снимком, выйти.

    Код выхода 0 — контрольный пример посчитан и КП сохранено, иначе 1.
    """
    window.open_example()

    def finish() -> None:
        from pathlib import Path

        from vedcalc.core.offer import OfferInput, Requisites, build_offer
        from vedcalc.gui.export_pdf import write_offer_pdf
        from vedcalc.reports.offer_docx import write_offer_docx

        report = window.calculator.report
        ok = report is not None and str(report.cost.cost_net) == "147731.00"
        window.grab().save(png_path)
        price = window.pricing.recalc()
        if ok and price is not None:
            offer = build_offer(price, OfferInput(), Requisites(company="Smoke"))
            base = Path(png_path).with_suffix("")
            write_offer_pdf(offer, base.with_suffix(".kp.pdf"))
            write_offer_docx(offer, base.with_suffix(".kp.docx"))
            ok = base.with_suffix(".kp.docx").stat().st_size > 1000
            # Связка ключей для токена WB доступна в сборке (сам токен не читаем)
            import keyring

            backend = type(keyring.get_keyring()).__module__
            print(f"keyring: {backend}")
            ok = ok and backend.endswith("macOS")
        else:
            ok = False
        print(f"smoke test: {'OK' if ok else 'FAILED'}, C = {report and report.cost.cost_net}")
        window.dirty = False
        app.exit(0 if ok else 1)

    QTimer.singleShot(1500, finish)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("vedcalc")
    smoke = os.environ.get(SMOKE_ENV)
    window = MainWindow(
        CbrRateProvider(),
        check_updates=not smoke and not os.environ.get("VEDCALC_NO_UPDATE_CHECK"),
    )
    window.show()
    if smoke:
        _smoke_test(window, app, smoke)
    else:
        QTimer.singleShot(0, window.offer_recovery)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
