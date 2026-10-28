"""Поле «Код ТН ВЭД» со ставкой из ЕТТ и окно подбора кода по названию."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.tnved import TnvedEntry, load_directory
from vedcalc.gui.theme import ASSETS


class TnvedEdit(QLineEdit):
    """Код ТН ВЭД: 10 цифр, кнопка подбора по названию. entryChanged — найденная запись
    справочника (или None, если код неполный или не найден)."""

    entryChanged = Signal(object)

    def __init__(self, code: str = "", parent: QWidget | None = None):
        super().__init__(code, parent)
        self.setMaxLength(10)
        self.setPlaceholderText("10 цифр")
        self.entry: TnvedEntry | None = None
        search = QAction(QIcon(str(ASSETS / "search.svg")), "Подобрать код по названию", self)
        search.triggered.connect(self.pick)
        self.addAction(search, QLineEdit.ActionPosition.TrailingPosition)
        self.textChanged.connect(self._lookup)
        self._lookup(self.text(), emit=False)

    def _lookup(self, text: str, emit: bool = True) -> None:
        directory = load_directory()
        code = text.strip()
        self.entry = directory.get(code) if len(code) == 10 else None
        if self.entry is not None:
            self.setToolTip(
                f"{self.entry.name}\nСтавка ЕТТ: {self.entry.tariff or 'не указана'}\n"
                f"Справочник ТН ВЭД от {directory.actual_date:%d.%m.%Y} (tws.by)"
            )
        elif len(code) == 10:
            self.setToolTip(
                f"Кода нет в справочнике ЕТТ от {directory.actual_date:%d.%m.%Y}. "
                "Проверьте код или задайте пошлину вручную."
            )
        else:
            self.setToolTip("Необязательно, 10 цифр. Пошлина подставится из ЕТТ.")
        if emit:
            self.entryChanged.emit(self.entry)

    @property
    def unknown(self) -> bool:
        return len(self.text().strip()) == 10 and self.entry is None

    def pick(self) -> None:
        dialog = TnvedSearchDialog(self.text() or "", self.window())
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected:
            self.setText(dialog.selected.code)


class TnvedSearchDialog(QDialog):
    """Подбор кода по словам наименования или началу кода — без интернета."""

    def __init__(self, query: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Подбор кода ТН ВЭД")
        self.resize(820, 560)
        self.directory = load_directory()
        self.selected: TnvedEntry | None = None
        v = QVBoxLayout(self)
        self.query = QLineEdit(query)
        self.query.setPlaceholderText("Слова из наименования (например: аккумуляторы свинцовые) "
                                      "или начало кода")  # fmt: skip
        v.addWidget(self.query)
        self.info = QLabel()
        self.info.setObjectName("muted")
        v.addWidget(self.info)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Код", "Наименование", "Ставка ЕТТ"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 100)
        self.table.setColumnWidth(2, 210)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.doubleClicked.connect(self.accept)
        v.addWidget(self.table, 1)
        self.link = QLabel()
        self.link.setOpenExternalLinks(True)
        v.addWidget(self.link)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Выбрать")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self.search)
        self.query.textChanged.connect(lambda _=None: self._timer.start())
        self.table.itemSelectionChanged.connect(self._on_select)
        self.results: list[TnvedEntry] = []
        self.search()

    def search(self) -> None:
        q = self.query.text()
        self.results = self.directory.search(q) if len(q.strip()) >= 2 else []
        self.table.setRowCount(len(self.results))
        for r, e in enumerate(self.results):
            for c, text in enumerate((e.code, e.name, e.tariff or "—")):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(r, c, item)
        self.table.resizeRowsToContents()
        total = len(self.results)
        self.info.setText(
            f"Найдено: {total}{'+' if total >= 200 else ''} · справочник ЕТТ от "
            f"{self.directory.actual_date:%d.%m.%Y}, {len(self.directory.entries)} кодов"
            if q.strip() else "Введите хотя бы два символа"
        )  # fmt: skip
        if self.results:
            self.table.selectRow(0)

    def _on_select(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        self.selected = self.results[rows[0].row()] if rows else None
        url = self.directory.details(self.selected) if self.selected else ""
        self.link.setText(
            f'<a href="{escape(url)}">Подробнее о коде на tws.by — меры регулирования, НДС</a>'
            if url else ""
        )  # fmt: skip


def open_details(entry: TnvedEntry) -> None:
    url = load_directory().details(entry)
    if url:
        QDesktopServices.openUrl(QUrl(url))


__all__ = ["Qt", "TnvedEdit", "TnvedSearchDialog", "open_details"]
