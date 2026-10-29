"""Режим «Список товаров» вкладки «WB»: таблица прибыли и маржи по файлу XLSX/CSV."""

from __future__ import annotations

from decimal import Decimal
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money
from vedcalc.gui.theme import ERROR_BOX, INFO_BOX
from vedcalc.gui.widgets import DecimalEdit, mark_error
from vedcalc.modules.wb_unit.batch import (
    DEFAULT_LOW_MARGIN,
    BatchFileError,
    BatchResult,
    BatchRow,
    SubjectLookup,
    evaluate_batch,
    level,
    parse_rows,
    read_table,
    result_values,
    summarize,
    write_results,
    write_template,
)
from vedcalc.modules.wb_unit.calc import Scheme, Tariffs, WbInput
from vedcalc.reports.style import NEGATIVE, POSITIVE

FILE_FILTER = "Таблицы (*.xlsx *.xlsm *.csv)"
LEVEL_COLORS = {"loss": "#F8D7D3", "low": "#FFF1C7", "error": "#EEEEEE"}
# Короткие заголовки для экрана (в Excel — полные)
SCREEN_HEADERS = ("№", "Название", "До скидки", "Цена", "Комиссия", "К перечислению",
                  "Прибыль", "Маржа", "ROI", "Прибыль {other}", "Ошибка")  # fmt: skip
MONEY_COLS = {2, 3, 5, 6, 9}
PCT_COLS = {4, 7, 8}


class _Item(QTableWidgetItem):
    """Ячейка с числовой сортировкой: показываем текст, сортируем по числу."""

    def __init__(self, text: str, key: float | str):
        super().__init__(text)
        self.key = key

    def __lt__(self, other: QTableWidgetItem) -> bool:
        a, b = self.key, getattr(other, "key", other.text())
        if isinstance(a, float) and isinstance(b, float):
            return a < b
        if isinstance(a, float) != isinstance(b, float):
            return isinstance(a, float)  # числа — раньше пустых
        return str(a) < str(b)


class BatchPanel(QWidget):
    changed = Signal()  # загружен новый файл или изменён порог

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.table_data: list[list[str]] = []  # строки файла — для файла проекта
        self.file_name = ""
        self.rows: list[BatchRow] = []
        self.results: list[BatchResult] = []
        self.scheme = Scheme.FBO
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 6, 2)
        v.setSpacing(10)
        bar = QHBoxLayout()
        self.load_button = QPushButton("Загрузить файл…")
        self.load_button.setObjectName("primary")
        self.load_button.clicked.connect(self.choose_file)
        template = QPushButton("Скачать шаблон…")
        template.clicked.connect(self.save_template)
        self.export_button = QPushButton("Выгрузить в Excel…")
        self.export_button.clicked.connect(self.export)
        bar.addWidget(self.load_button)
        bar.addWidget(template)
        bar.addWidget(self.export_button)
        bar.addStretch(1)
        bar.addWidget(QLabel("Маржа ниже, %"))
        self.low_margin = DecimalEdit(DEFAULT_LOW_MARGIN)
        self.low_margin.setMaximumWidth(70)
        self.low_margin.setToolTip("Строки с маржой ниже порога подсвечиваются жёлтым")
        self.low_margin.textChanged.connect(self._on_threshold)
        bar.addWidget(self.low_margin)
        v.addLayout(bar)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(self.info)
        self.table = QTableWidget(0, len(SCREEN_HEADERS))
        self.table.setHorizontalHeaderLabels([h.format(other="FBS") for h in SCREEN_HEADERS])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)  # «Ошибка» забирает остаток ширины
        for col, width in enumerate((40, 230, 88, 84, 76, 112, 84, 68, 68, 100, 160)):
            self.table.setColumnWidth(col, width)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        v.addWidget(self.table, 1)
        self._show_empty()

    # --- ввод -----------------------------------------------------------------------------

    def threshold(self) -> Decimal:
        try:
            value = self.low_margin.value()
            mark_error(self.low_margin, None)
            return value if value is not None else DEFAULT_LOW_MARGIN
        except ValueError as exc:
            mark_error(self.low_margin, str(exc))
            return DEFAULT_LOW_MARGIN

    def _on_threshold(self, *_args) -> None:
        self._fill()
        self.changed.emit()

    def choose_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Список товаров", "", FILE_FILTER)
        if name:
            self.load_file(Path(name))

    def load_file(self, path: Path) -> bool:
        try:
            table = read_table(path)
            rows = parse_rows(table)
        except (OSError, BatchFileError) as exc:
            self._message(f"<b>Не удалось загрузить «{escape(path.name)}».</b> {escape(str(exc))}")
            return False
        self.table_data, self.file_name, self.rows = table, path.name, rows
        self.changed.emit()
        return True

    def set_data(self, file_name: str, table: list[list[str]]) -> None:
        """Из файла проекта: строки таблицы как были загружены."""
        self.file_name, self.table_data = file_name, table
        try:
            self.rows = parse_rows(table) if table else []
        except BatchFileError:
            self.rows = []

    def clear(self) -> None:
        self.set_data("", [])
        self.results = []
        self._show_empty()

    # --- расчёт ---------------------------------------------------------------------------

    def update(self, base: WbInput | None, tariffs: Tariffs, subject: SubjectLookup,
               errors: list[str] | None = None) -> None:  # fmt: skip
        if not self.rows:
            self.results = []
            self._show_empty()
            return
        if base is None:
            self.results = []
            self.table.setRowCount(0)
            self._message("<b>Исправьте форму слева:</b><br>" + "<br>".join(
                map(escape, errors or [])), error=True)  # fmt: skip
            return
        self.scheme = base.scheme
        self.results = evaluate_batch(self.rows, base, tariffs, subject)
        self._fill()

    def _show_empty(self) -> None:
        self.table.setRowCount(0)
        self.export_button.setEnabled(False)
        self._message(
            "Загрузите XLSX или CSV со списком товаров: название, габариты упаковки, "
            "себестоимость, цена до скидки. Остальное — схема, налог, тарифы, выкуп — берётся "
            "из формы слева; пустые ячейки файла — тоже из формы. Колонки — как в шаблоне."
        )

    def _message(self, html: str, *, error: bool = False) -> None:
        self.info.setStyleSheet(ERROR_BOX if error else INFO_BOX)
        self.info.setText(html)

    def _fill(self) -> None:
        if not self.results:
            return
        low = self.threshold()
        other = "FBS" if self.scheme is Scheme.FBO else "FBO"
        self.table.setHorizontalHeaderLabels([h.format(other=other) for h in SCREEN_HEADERS])
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.results))
        for r, res in enumerate(self.results):
            lvl = level(res, low)
            for c, value in enumerate(result_values(res)):
                item = self._cell(c, value)
                if lvl in LEVEL_COLORS:
                    item.setBackground(QColor(LEVEL_COLORS[lvl]))
                if c in (6, 9) and isinstance(value, Decimal):
                    item.setForeground(QColor(POSITIVE if value >= 0 else NEGATIVE))
                if c == 10 and value:
                    item.setToolTip(str(value))
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        s = summarize(self.results, low)
        parts = [
            f"<b>{escape(self.file_name or 'Список')}</b>: товаров {s.total}, рассчитано "
            f"{s.computed}",
            f"<span style='color:{NEGATIVE}'>убыточных {s.losses}</span>",
            f"маржа ниже {low.normalize():f}% — {s.low_margin}",
        ]
        if s.errors:
            parts.append(f"с ошибками {s.errors}")
        parts.append(f"схема {self.scheme.label}")
        self._message(" · ".join(parts) + ".")
        self.export_button.setEnabled(s.computed > 0)

    @staticmethod
    def _cell(col: int, value) -> _Item:
        if value is None:
            return _Item("", "")
        if isinstance(value, Decimal):
            text = fmt_money(value) + ("%" if col in PCT_COLS else "")
            item = _Item(text, float(value))
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return item
        if isinstance(value, int):
            return _Item(str(value), float(value))
        return _Item(str(value), str(value))

    # --- файлы ----------------------------------------------------------------------------

    def save_template(self, path: Path | None = None) -> Path | None:
        if path is None:
            name, _ = QFileDialog.getSaveFileName(
                self, "Шаблон списка товаров", "Шаблон WB.xlsx", "Excel (*.xlsx)"
            )
            if not name:
                return None
            path = Path(name)
        try:
            write_template(path)
        except OSError as exc:
            QMessageBox.warning(self, "Шаблон", f"Не удалось сохранить:\n{exc}")
            return None
        return path

    def export(self, path: Path | None = None) -> Path | None:
        if not self.results:
            return None
        if path is None:
            base = Path(self.file_name).stem or "WB"
            name, _ = QFileDialog.getSaveFileName(
                self, "Выгрузить в Excel", f"{base} — расчёт WB.xlsx", "Excel (*.xlsx)"
            )
            if not name:
                return None
            path = Path(name)
        try:
            write_results(self.results, self.scheme, self.threshold(), path)
        except OSError as exc:
            QMessageBox.warning(self, "Выгрузка", f"Не удалось сохранить:\n{exc}")
            return None
        return path
