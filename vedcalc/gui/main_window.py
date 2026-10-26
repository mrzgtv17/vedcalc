"""Главное окно: вкладки модулей, меню «Файл», сохранение и экспорт."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget

from vedcalc import __version__
from vedcalc.core.fx import RateProvider
from vedcalc.core.io import (
    FileFormatError,
    duplicate_shipment,
    load_shipment,
    save_shipment,
    shipment_from_dict,
)
from vedcalc.core.validation import InputError
from vedcalc.gui.calculator import CalculatorTab
from vedcalc.gui.form import new_shipment
from vedcalc.gui.theme import apply_theme
from vedcalc.gui.updates import UpdateController
from vedcalc.modules.registry import ModuleSpec, modules

FILE_FILTER = "Расчёт ВЭД (*.vedcalc.json *.json)"
APP_TITLE = "Калькулятор ВЭД"


class MainWindow(QMainWindow):
    def __init__(
        self,
        provider: RateProvider,
        *,
        threaded: bool = True,
        extra_modules: list[ModuleSpec] | None = None,
        check_updates: bool = False,
    ):
        super().__init__()
        apply_theme(QApplication.instance())
        self.path: Path | None = None
        self.dirty = False
        self.updates = UpdateController(self, threaded=threaded)

        self.calculator = CalculatorTab(provider, threaded=threaded)
        self.calculator.modified.connect(self._mark_dirty)
        self.calculator.reportChanged.connect(self._update_actions)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.calculator, "Импорт и цена")
        for spec in extra_modules if extra_modules is not None else modules():
            self.tabs.addTab(spec.factory(), spec.title)
        self.tabs.tabBar().setVisible(self.tabs.count() > 1)
        self.setCentralWidget(self.tabs)

        self._build_menu()
        self.resize(1360, 860)
        self.dirty = False
        self._update_title()
        self._update_actions()
        if check_updates:
            self.updates.schedule_startup_check()

    # --- меню -------------------------------------------------------------------------

    def _action(self, text: str, slot, shortcut=None) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("Файл")
        m.addAction(self._action("Новый расчёт", self.new_file, QKeySequence.StandardKey.New))
        m.addAction(self._action("Открыть…", self.open_file, QKeySequence.StandardKey.Open))
        m.addAction(self._action("Открыть контрольный пример", self.open_example))
        m.addSeparator()
        m.addAction(self._action("Сохранить", self.save, QKeySequence.StandardKey.Save))
        m.addAction(self._action("Сохранить как…", self.save_as, QKeySequence.StandardKey.SaveAs))
        m.addAction(self._action("Дублировать расчёт", self.duplicate, "Ctrl+D"))
        m.addSeparator()
        self.pdf_action = self._action("Экспорт в PDF…", self.export_pdf, "Ctrl+E")
        self.xlsx_action = self._action("Экспорт в XLSX…", self.export_xlsx, "Ctrl+Shift+E")
        m.addAction(self.pdf_action)
        m.addAction(self.xlsx_action)
        m.addSeparator()
        m.addAction(self._action("Выход", self.close, QKeySequence.StandardKey.Quit))

        h = self.menuBar().addMenu("Справка")
        h.addAction(self._action("Проверить обновления…", lambda: self.updates.check(manual=True)))
        h.addAction(self._action("О программе", self.about))

    def _update_actions(self) -> None:
        ready = self.calculator.report is not None
        self.pdf_action.setEnabled(ready)
        self.xlsx_action.setEnabled(ready)

    # --- состояние --------------------------------------------------------------------

    def _mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_title(self) -> None:
        name = self.path.name if self.path else "Новый расчёт"
        self.setWindowTitle(f"{name}{' •' if self.dirty else ''} — {APP_TITLE}")
        self.setWindowModified(self.dirty)

    def _load(self, shipment, path: Path | None) -> None:
        self.calculator.set_shipment(shipment)
        self.path = path
        self.dirty = False
        self._update_title()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            "Сохранить изменения в текущем расчёте?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _error(self, text: str) -> None:
        QMessageBox.warning(self, APP_TITLE, text)

    # --- файлы ------------------------------------------------------------------------

    def new_file(self) -> None:
        if self._confirm_discard():
            self._load(new_shipment(), None)

    def open_file(self, path: Path | None = None) -> None:
        if not self._confirm_discard():
            return
        if not path:
            name, _ = QFileDialog.getOpenFileName(self, "Открыть расчёт", "", FILE_FILTER)
            if not name:
                return
            path = Path(name)
        try:
            self._load(load_shipment(path), path)
        except (OSError, FileFormatError) as exc:
            self._error(f"Не удалось открыть файл:\n{exc}")

    def open_example(self) -> None:
        if not self._confirm_discard():
            return
        import json

        text = resources.files("vedcalc.data").joinpath("control_example.vedcalc.json")
        self._load(shipment_from_dict(json.loads(text.read_text("utf-8"))), None)

    def _current(self):
        try:
            return self.calculator.shipment()
        except InputError as exc:
            self._error(
                "Исправьте ошибки ввода перед сохранением:\n• "
                + "\n• ".join(i.message for i in exc.issues)
            )
            return None

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._save_to(self.path)

    def save_as(self) -> bool:
        shipment = self._current()
        if shipment is None:
            return False
        suggested = f"{shipment.name}.vedcalc.json"
        name, _ = QFileDialog.getSaveFileName(self, "Сохранить расчёт", suggested, FILE_FILTER)
        if not name:
            return False
        return self._save_to(Path(name))

    def _save_to(self, path: Path) -> bool:
        shipment = self._current()
        if shipment is None:
            return False
        try:
            save_shipment(shipment, path)
        except OSError as exc:
            self._error(f"Не удалось сохранить файл:\n{exc}")
            return False
        self.path = path
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Сохранено: {path}", 4000)
        return True

    def duplicate(self) -> None:
        shipment = self._current()
        if shipment is None or not self._confirm_discard():
            return
        self._load(duplicate_shipment(shipment), None)
        self.dirty = True
        self._update_title()
        self.statusBar().showMessage("Создана копия расчёта — сохраните её под новым именем", 5000)

    # --- экспорт ----------------------------------------------------------------------

    def _export_path(self, suffix: str, title: str, file_filter: str) -> Path | None:
        report = self.calculator.report
        if report is None:
            self._error("Нет результата для экспорта — проверьте ввод.")
            return None
        base = (
            self.path.name.removesuffix(".json").removesuffix(".vedcalc")
            if self.path
            else report.shipment.name
        )
        name, _ = QFileDialog.getSaveFileName(self, title, f"{base}.{suffix}", file_filter)
        return Path(name) if name else None

    def export_pdf(self, path: Path | None = None) -> None:
        from vedcalc.gui.export_pdf import write_pdf

        path = path or self._export_path("pdf", "Экспорт в PDF", "PDF (*.pdf)")
        if path:
            write_pdf(self.calculator.report, path)
            self.statusBar().showMessage(f"PDF сохранён: {path}", 4000)

    def export_xlsx(self, path: Path | None = None) -> None:
        from vedcalc.reports.xlsx import write_xlsx

        path = path or self._export_path("xlsx", "Экспорт в XLSX", "Excel (*.xlsx)")
        if path:
            try:
                write_xlsx(self.calculator.report, path)
            except OSError as exc:
                self._error(f"Не удалось сохранить файл:\n{exc}")
                return
            self.statusBar().showMessage(f"XLSX сохранён: {path}", 4000)

    def about(self) -> None:
        scale = self.calculator.fee_scale
        QMessageBox.about(
            self,
            APP_TITLE,
            f"<b>{APP_TITLE}</b> {__version__}<br>Себестоимость импорта и цена продажи B2B."
            f"<br><br>Шкала сборов: {scale.legal_basis}, с {scale.valid_from:%d.%m.%Y}."
            "<br>Курсы: ЦБ РФ на дату расчёта.<br><br>Программа не заменяет консультацию "
            "таможенного брокера и бухгалтера.",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._confirm_discard():
            event.accept()
        else:
            event.ignore()
