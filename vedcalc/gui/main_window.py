"""Главное окно: вкладки модулей, меню «Файл», сохранение и экспорт."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QFileDialog, QMainWindow, QMessageBox, QTabWidget

from vedcalc import __version__
from vedcalc.core.fx import RateProvider
from vedcalc.core.io import (
    FileFormatError,
    duplicate_shipment,
    load_project,
    project_from_dict,
    project_to_dict,
    read_json,
    save_project,
    write_json,
)
from vedcalc.core.offer import requisites_dir
from vedcalc.core.validation import InputError
from vedcalc.gui.calculator import CalculatorTab
from vedcalc.gui.form import PricingState, new_shipment
from vedcalc.gui.prefs import app_settings
from vedcalc.gui.pricing_tab import PricingTab
from vedcalc.gui.theme import apply_theme
from vedcalc.gui.updates import UpdateController
from vedcalc.modules import builtin_modules
from vedcalc.modules.registry import ModuleSpec, modules

FILE_FILTER = "Расчёт ВЭД (*.vedcalc.json *.json)"
APP_TITLE = "Калькулятор ВЭД"
MAX_RECENT = 10
AUTOSAVE_MS = 60_000  # копия несохранённого расчёта — раз в минуту


def autosave_path() -> Path:
    return requisites_dir() / "recovery" / "autosave.vedcalc.json"


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
        self.pricing = PricingTab(self.calculator)
        self.pricing.modified.connect(self._mark_dirty)
        self.pricing.set_state(self.calculator.form.pricing)
        self.tabs.addTab(self.calculator, "Импорт")
        self.tabs.addTab(self.pricing, "Цена продажи")
        self.tabs.currentChanged.connect(self._update_actions)
        specs = extra_modules if extra_modules is not None else builtin_modules() + modules()
        # Вкладки-модули с save_state/load_state сохраняются в файл проекта
        self.module_tabs: dict[str, object] = {}
        for spec in sorted(specs, key=lambda m: m.order):
            tab = spec.factory()
            self.tabs.addTab(tab, spec.title)
            if hasattr(tab, "save_state") and hasattr(tab, "load_state"):
                self.module_tabs[spec.id] = tab
            if hasattr(tab, "modified"):
                tab.modified.connect(self._mark_dirty)
        self.tabs.tabBar().setVisible(self.tabs.count() > 1)
        self.setCentralWidget(self.tabs)

        self.settings = app_settings()
        self._build_menu()
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start()
        self.pricing.reportChanged.connect(self._update_actions)
        self.pricing.offerRequested.connect(self.commercial_offer)
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
        self.recent_menu = m.addMenu("Открыть последние")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
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
        self.offer_action = self._action(
            "Коммерческое предложение…", self.commercial_offer, "Ctrl+K"
        )
        m.addAction(self.offer_action)
        m.addAction(self._action("Мои реквизиты…", self.edit_requisites))
        m.addSeparator()
        m.addAction(self._action("Выход", self.close, QKeySequence.StandardKey.Quit))

        h = self.menuBar().addMenu("Справка")
        h.addAction(self._action("Проверить обновления…", lambda: self.updates.check(manual=True)))
        h.addAction(self._action("Справочник ТН ВЭД…", self.tnved_info))
        h.addAction(self._action("О программе", self.about))

    def _update_actions(self) -> None:
        ready = self.current_report() is not None
        self.pdf_action.setEnabled(ready)
        self.xlsx_action.setEnabled(ready)
        self.offer_action.setEnabled(self.pricing.offer_ready)

    # --- состояние --------------------------------------------------------------------

    def _mark_dirty(self) -> None:
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _update_title(self) -> None:
        name = self.path.name if self.path else "Новый расчёт"
        self.setWindowTitle(f"{name}{' •' if self.dirty else ''} — {APP_TITLE}")
        self.setWindowModified(self.dirty)

    def current_report(self):
        """Отчёт активной вкладки: себестоимость импорта или цена продажи."""
        if self.tabs.currentWidget() is self.pricing:
            return self.pricing.report
        if self.tabs.currentWidget() is self.calculator:
            return self.calculator.report
        return None

    def _load(self, shipment, path: Path | None, modules: dict[str, dict] | None = None) -> None:
        self.calculator.set_shipment(shipment)
        self.pricing.set_state(PricingState.from_shipment(shipment))
        for module_id, tab in self.module_tabs.items():
            tab.load_state((modules or {}).get(module_id))
        self.path = path
        self.dirty = False
        self._update_title()
        self.discard_autosave()

    def module_states(self) -> dict[str, dict]:
        return {module_id: tab.save_state() for module_id, tab in self.module_tabs.items()}

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
            shipment, states = load_project(path)
        except (OSError, FileFormatError) as exc:
            self._error(f"Не удалось открыть файл:\n{exc}")
            self._forget_recent(path)
            return
        self._load(shipment, path, states)
        self._remember_recent(path)

    def open_example(self) -> None:
        if not self._confirm_discard():
            return
        import json

        text = resources.files("vedcalc.data").joinpath("control_example.vedcalc.json")
        self._load(*project_from_dict(json.loads(text.read_text("utf-8"))), None)

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
            save_project(shipment, self.module_states(), path)
        except OSError as exc:
            self._error(f"Не удалось сохранить файл:\n{exc}")
            return False
        self.path = path
        self.dirty = False
        self._update_title()
        self._remember_recent(path)
        self.discard_autosave()
        self.statusBar().showMessage(f"Сохранено: {path}", 4000)
        return True

    def duplicate(self) -> None:
        shipment = self._current()
        if shipment is None or not self._confirm_discard():
            return
        self._load(duplicate_shipment(shipment), None, self.module_states())
        self.dirty = True
        self._update_title()
        self.statusBar().showMessage("Создана копия расчёта — сохраните её под новым именем", 5000)

    # --- последние файлы ----------------------------------------------------------------

    def recent_files(self) -> list[Path]:
        raw = self.settings.value("recent_files", []) or []
        if isinstance(raw, str):  # QSettings возвращает строку, если в списке один элемент
            raw = [raw]
        return [Path(p) for p in raw]

    def _set_recent(self, paths: list[Path]) -> None:
        self.settings.setValue("recent_files", [str(p) for p in paths[:MAX_RECENT]])

    def _remember_recent(self, path: Path) -> None:
        path = path.resolve()
        self._set_recent([path] + [p for p in self.recent_files() if p != path])

    def _forget_recent(self, path: Path) -> None:
        self._set_recent([p for p in self.recent_files() if p != path.resolve()])

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        paths = [p for p in self.recent_files() if p.is_file()]
        for path in paths:
            action = self.recent_menu.addAction(path.name)
            action.setToolTip(str(path))
            action.triggered.connect(lambda _=False, p=path: self.open_file(p))
        if paths:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction(
                self._action("Очистить список", lambda: self._set_recent([]))
            )
        else:
            self.recent_menu.addAction("Нет недавних файлов").setEnabled(False)

    # --- автосохранение ---------------------------------------------------------------

    def autosave(self) -> bool:
        """Копия несохранённого расчёта в папке программы — на случай сбоя.

        Сам файл пользователя не трогаем: он сохраняется только по ⌘S.
        """
        if not self.dirty:
            return False
        try:
            shipment = self.calculator.shipment()
        except InputError:
            return False  # ввод с ошибками — оставляем прошлую копию
        data = project_to_dict(shipment, self.module_states())
        data["recovery"] = {"path": str(self.path) if self.path else None}
        target = autosave_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_json(data, target)
        except OSError:
            return False
        return True

    def discard_autosave(self) -> None:
        autosave_path().unlink(missing_ok=True)

    def offer_recovery(self) -> bool:
        """При запуске: если осталась копия после сбоя — предложить восстановить."""
        target = autosave_path()
        if not target.is_file():
            return False
        try:
            data = read_json(target)
            shipment, states = project_from_dict(data)
        except (OSError, FileFormatError):
            self.discard_autosave()
            return False
        original = (data.get("recovery") or {}).get("path")
        where = f"«{Path(original).name}»" if original else "новый расчёт"
        answer = QMessageBox.question(
            self,
            APP_TITLE,
            f"Программа закрылась, не сохранив изменения ({where}).\n"
            "Восстановить несохранённый расчёт?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.discard_autosave()
            return False
        self._load(shipment, Path(original) if original else None, states)
        self.dirty = True
        self._update_title()
        self.autosave()  # копия нужна, пока восстановленное не сохранено
        return True

    # --- экспорт ----------------------------------------------------------------------

    def _export_path(self, suffix: str, title: str, file_filter: str) -> Path | None:
        report = self.current_report()
        if report is None:
            self._error("Нет результата для экспорта — проверьте ввод.")
            return None
        base = (
            self.path.name.removesuffix(".json").removesuffix(".vedcalc")
            if self.path
            else getattr(report, "title", None) or report.shipment.name
        )
        if report is self.pricing.report:
            base += " — цена продажи"
        name, _ = QFileDialog.getSaveFileName(self, title, f"{base}.{suffix}", file_filter)
        return Path(name) if name else None

    def _whatif(self):
        """Таблицы «Что если» с экрана — только для отчёта о цене."""
        if self.current_report() is self.pricing.report:
            return self.pricing.results.whatif_view.whatif
        return None

    def export_pdf(self, path: Path | None = None) -> None:
        from vedcalc.gui.export_pdf import write_pdf

        path = path or self._export_path("pdf", "Экспорт в PDF", "PDF (*.pdf)")
        if path:
            write_pdf(self.current_report(), path, self._whatif())
            self.statusBar().showMessage(f"PDF сохранён: {path}", 4000)

    def export_xlsx(self, path: Path | None = None) -> None:
        from vedcalc.reports.xlsx import write_cost_xlsx, write_price_xlsx

        path = path or self._export_path("xlsx", "Экспорт в XLSX", "Excel (*.xlsx)")
        if path:
            try:
                report = self.current_report()
                if report is self.pricing.report:
                    write_price_xlsx(report, path, self._whatif())
                else:
                    write_cost_xlsx(report, path)
            except OSError as exc:
                self._error(f"Не удалось сохранить файл:\n{exc}")
                return
            self.statusBar().showMessage(f"XLSX сохранён: {path}", 4000)

    def _base_name(self) -> str:
        return self.path.name.removesuffix(".json").removesuffix(".vedcalc") if self.path else ""

    def commercial_offer(self) -> None:
        from vedcalc.gui.offer import OfferDialog

        if not self.pricing.offer_ready:
            self._error("Цена продажи не рассчитана — заполните вкладку «Цена продажи».")
            return
        OfferDialog(self.pricing.report, base_name=self._base_name(), parent=self).exec()

    def edit_requisites(self) -> None:
        from vedcalc.gui.offer import RequisitesDialog

        RequisitesDialog(parent=self).exec()

    def tnved_info(self) -> None:
        from vedcalc.core.tnved import STALE_AFTER_DAYS, load_directory

        d = load_directory()
        age = d.age_days()
        state = (
            f"<b>Справочник старше {STALE_AFTER_DAYS} дней</b> — обновите программу: "
            "Справка → Проверить обновления."
            if d.stale()
            else "Актуален: обновляется вместе с программой не реже раза в месяц."
        )
        QMessageBox.information(
            self,
            "Справочник ТН ВЭД",
            f"Коды ТН ВЭД ЕАЭС и ставки ввозной пошлины ЕТТ.<br><br>"
            f"Данные на {d.actual_date:%d.%m.%Y} ({age} дн. назад), кодов: {len(d.entries)}.<br>"
            f"{state}<br><br>Источник: <a href='https://www.tws.by/tws/tnved'>tws.by</a> — "
            "бесплатная выгрузка «как есть». НДС, акцизы и меры регулирования в справочнике "
            "нет — их видно по ссылке «Подробнее» в подборе кода.",
        )

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
            self.discard_autosave()  # изменения сохранены или отброшены осознанно
            event.accept()
        else:
            event.ignore()
