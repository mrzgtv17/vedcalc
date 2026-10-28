"""Блок «Тарифы WB по API» вкладки «WB»: токен, обновление, выбор предмета и склада."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vedcalc.modules.wb_unit.api import (
    Fetch,
    TokenStore,
    WbApiError,
    WbData,
    http_fetch,
    load_cache,
    refresh,
)

NOT_SELECTED = "— не выбран —"


class TokenDialog(QDialog):
    """Ввод токена API WB: хранится в Связке ключей macOS, на экране — скрыт."""

    def __init__(self, store: TokenStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Токен API Wildberries")
        self.store = store
        self.deleted = False
        v = QVBoxLayout(self)
        info = QLabel(
            "Создайте токен в кабинете продавца: Настройки → Доступ к API → категория "
            "<b>«Тарифы»</b>, режим «Только чтение». Токен хранится в Связке ключей этого Mac "
            "и отправляется только на common-api.wildberries.ru."
        )
        info.setWordWrap(True)
        v.addWidget(info)
        self.edit = QLineEdit(store.get())
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText("Вставьте токен")
        v.addWidget(self.edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.delete_button = buttons.addButton(
            "Удалить токен", QDialogButtonBox.ButtonRole.ResetRole
        )
        self.delete_button.setEnabled(bool(store.get()))
        self.delete_button.clicked.connect(self.delete)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self.resize(520, 0)

    def delete(self) -> None:
        self.store.delete()
        self.deleted = True
        super().accept()

    def accept(self) -> None:
        token = "".join(self.edit.text().split())
        try:
            if token:
                self.store.set(token)
            else:
                self.store.delete()
        except WbApiError as exc:
            self.edit.setToolTip(str(exc))
            return
        super().accept()


class _Signals(QObject):
    done = Signal(object, object)  # WbData | None, текст ошибки | None


class _Job(QRunnable):
    def __init__(self, token: str, fetch: Fetch, signals: _Signals):
        super().__init__()
        self.token, self.fetch, self.signals = token, fetch, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(refresh(self.token, self.fetch), None)
        except WbApiError as exc:
            self.signals.done.emit(None, str(exc))
        except Exception as exc:
            self.signals.done.emit(None, f"{type(exc).__name__}: {exc}")


def _combo() -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(8)
    completer = combo.completer()
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    return combo


class WbApiBox(QGroupBox):
    """Комиссия по предмету и тарифы склада из API WB — подставляются в поля вкладки."""

    commissionPicked = Signal(object)  # Subject
    warehousePicked = Signal(object)  # Warehouse
    selectionChanged = Signal()

    def __init__(
        self,
        *,
        store: TokenStore | None = None,
        fetch: Fetch = http_fetch,
        threaded: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__("Тарифы WB по API", parent)
        self.store = store or TokenStore()
        self.fetch = fetch
        self.threaded = threaded
        self.data: WbData = load_cache()
        self.busy = False
        self._auto_done = False
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)

        form = QFormLayout(self)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        form.addRow(self.status)
        buttons = QHBoxLayout()
        self.token_button = QPushButton("Токен…")
        self.token_button.clicked.connect(self.edit_token)
        self.refresh_button = QPushButton("Обновить")
        self.refresh_button.clicked.connect(self.start_refresh)
        buttons.addWidget(self.token_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        form.addRow(buttons)
        self.subject_combo = _combo()
        self.subject_combo.setToolTip("Комиссия FBO и FBS подставится по предмету")
        self.subject_combo.activated.connect(self._pick_subject)
        self.warehouse_combo = _combo()
        self.warehouse_combo.setToolTip(
            "Логистика и хранение склада; коэффициент склада — из тарифов WB"
        )
        self.warehouse_combo.activated.connect(self._pick_warehouse)
        form.addRow("Предмет", self.subject_combo)
        form.addRow("Склад", self.warehouse_combo)
        self._fill()

    # --- данные ---------------------------------------------------------------------------

    def _fill(self) -> None:
        subject, warehouse = self.selected_subject_title(), self.selected_warehouse_name()
        for combo, items in (
            (self.subject_combo, [s.title for s in self.data.subjects]),
            (self.warehouse_combo, [w.name for w in self.data.warehouses]),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(NOT_SELECTED)
            combo.addItems(items)
            combo.blockSignals(False)
        self.set_selection(subject, warehouse)
        self.subject_combo.setEnabled(bool(self.data.subjects))
        self.warehouse_combo.setEnabled(bool(self.data.warehouses))
        self._update_status(None)

    def _update_status(self, error: str | None) -> None:
        token = bool(self.store.get())
        if self.busy:
            text = "Загружаю тарифы и комиссии WB…"
        elif error:
            text = f"<span style='color:#B3261E'>{escape(error)}</span>"
        elif self.data.fetched_at:
            text = (
                f"Данные WB от {self.data.fetched_at:%d.%m.%Y %H:%M}: "
                f"{len(self.data.subjects)} предметов, {len(self.data.warehouses)} складов."
            )
            if self.data.stale():
                text += " Старше суток — нажмите «Обновить»." if token else ""
        elif token:
            text = "Токен сохранён. Нажмите «Обновить», чтобы загрузить тарифы."
        else:
            text = (
                "Без токена комиссии и тарифы вводятся вручную. С токеном категории «Тарифы» "
                "они подставятся по предмету и складу."
            )
        self.status.setText(text)
        self.refresh_button.setEnabled(token and not self.busy)

    def edit_token(self) -> None:
        dialog = TokenDialog(self.store, self.window())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._update_status(None)
            if self.store.get() and not dialog.deleted:
                self.start_refresh()

    def start_refresh(self) -> None:
        token = self.store.get()
        if not token or self.busy:
            return
        self.busy = True
        self._update_status(None)
        job = _Job(token, self.fetch, self._signals)
        if self.threaded:
            QThreadPool.globalInstance().start(job)
        else:
            job.run()

    def auto_refresh(self) -> None:
        """При первом открытии вкладки: обновить, если данным больше суток."""
        if self._auto_done:
            return
        self._auto_done = True
        if self.store.get() and self.data.stale():
            self.start_refresh()

    def _on_done(self, data: WbData | None, error: str | None) -> None:
        self.busy = False
        if data is not None:
            self.data = data
            self._fill()
            # Выбранные предмет и склад — пересчитать по свежим тарифам
            if subject := self.data.subject(self.selected_subject_title()):
                self.commissionPicked.emit(subject)
            if warehouse := self.data.warehouse(self.selected_warehouse_name()):
                self.warehousePicked.emit(warehouse)
        self._update_status(error)

    # --- выбор ----------------------------------------------------------------------------

    def selected_subject_title(self) -> str:
        text = self.subject_combo.currentText()
        return "" if text == NOT_SELECTED else text

    def selected_warehouse_name(self) -> str:
        text = self.warehouse_combo.currentText()
        return "" if text == NOT_SELECTED else text

    def set_selection(self, subject: str, warehouse: str) -> None:
        """Восстановить выбор (из файла проекта) без подстановки значений."""
        for combo, value in ((self.subject_combo, subject), (self.warehouse_combo, warehouse)):
            combo.blockSignals(True)
            idx = combo.findText(value) if value else 0
            if idx < 0:  # нет в загруженных данных — показываем как есть
                combo.addItem(value)
                idx = combo.count() - 1
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _pick_subject(self, *_args) -> None:
        if subject := self.data.subject(self.selected_subject_title()):
            self.commissionPicked.emit(subject)
        self.selectionChanged.emit()

    def _pick_warehouse(self, *_args) -> None:
        if warehouse := self.data.warehouse(self.selected_warehouse_name()):
            self.warehousePicked.emit(warehouse)
        self.selectionChanged.emit()

    def current_warehouse(self):
        return self.data.warehouse(self.selected_warehouse_name())
