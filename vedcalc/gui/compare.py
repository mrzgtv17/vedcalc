"""Окно «Сравнение вариантов»: запомненные расчёты рядом, разница к первому."""

from __future__ import annotations

import copy
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.compare import MAX_VARIANTS, Comparison, Kind, Variant, compare, evaluate
from vedcalc.core.explain import fmt_money, fmt_number, fmt_pct
from vedcalc.core.io import FileFormatError, project_from_dict, read_json
from vedcalc.core.models import Snapshot
from vedcalc.core.validation import InputError
from vedcalc.gui.theme import INFO_BOX, current, notifier, set_tone

CURRENT = "Текущий расчёт"


def _fmt(value, kind: Kind) -> str:
    if value is None:
        return "—"
    if kind is Kind.PCT:
        return fmt_pct(value)
    if kind is Kind.NUMBER:
        return fmt_number(value)
    return fmt_money(value)


def _fmt_delta(value, kind: Kind) -> str:
    if value is None or not value:
        return ""
    sign = "+" if value > 0 else "−"
    if kind is Kind.PCT:
        return f"{sign}{fmt_number(round(abs(value) * 100, 2))} п. п."
    return sign + _fmt(abs(value), kind)


class CompareDialog(QDialog):
    """Слева — варианты с галочками (до 4), справа — таблица по статьям."""

    def __init__(self, window, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Сравнение вариантов")
        self.resize(1180, 720)
        self.window_ = window
        self.comparison: Comparison | None = None

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(f"Отметьте 2–{MAX_VARIANTS} варианта. Разница — к первому отмеченному.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lv.addWidget(hint)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemChanged.connect(self._on_check)
        self.list.currentRowChanged.connect(lambda _=0: self._update_buttons())
        lv.addWidget(self.list, 1)
        self.remember_button = QPushButton("Запомнить текущий…")
        self.remember_button.clicked.connect(self.remember_current)
        self.file_button = QPushButton("Добавить из файла…")
        self.file_button.clicked.connect(lambda: self.add_from_file())
        self.rename_button = QPushButton("Переименовать…")
        self.rename_button.clicked.connect(self.rename)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self.delete)
        self.open_button = QPushButton("Открыть в редакторе")
        self.open_button.setToolTip(
            "Заменить ввод вкладок «Импорт» и «Цена продажи» этим вариантом"
        )
        self.open_button.clicked.connect(self.open_in_editor)
        for b in (self.remember_button, self.file_button, self.rename_button,
                  self.delete_button, self.open_button):  # fmt: skip
            lv.addWidget(b)
        left.setMinimumWidth(260)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 1)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        rv.addWidget(self.table, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.RichText)
        rv.addWidget(self.info)
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.export_button = QPushButton("Выгрузить в XLSX…")
        self.export_button.clicked.connect(lambda: self.export())
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        bar.addWidget(self.export_button)
        bar.addWidget(close)
        rv.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([300, 880])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        notifier().changed.connect(self.refresh)
        self._fill_list(checked=self._default_checked())
        self.refresh()

    # --- список вариантов -------------------------------------------------------------

    @property
    def snapshots(self) -> list[Snapshot]:
        return self.window_.snapshots

    def _default_checked(self) -> set[int]:
        """По умолчанию: последние запомненные варианты (до 4); если их меньше двух —
        с текущим расчётом."""
        n = len(self.snapshots)
        if n >= 2:
            return set(range(max(1, n - MAX_VARIANTS + 1), n + 1))
        return {0, *range(1, n + 1)}

    def _checked(self) -> list[int]:
        """Отмеченные строки списка: 0 — текущий расчёт, k — snapshots[k − 1]."""
        return [r for r in range(self.list.count())
                if self.list.item(r).checkState() == Qt.CheckState.Checked]  # fmt: skip

    def _fill_list(self, checked: set[int] | None = None) -> None:
        checked = set(self._checked()) if checked is None else checked
        self.list.blockSignals(True)
        self.list.clear()
        titles = [CURRENT] + [f"{s.name}  ·  {s.created:%d.%m %H:%M}" for s in self.snapshots]
        for r, title in enumerate(titles):
            item = QListWidgetItem(title)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if r in checked else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_buttons()

    def _on_check(self, item: QListWidgetItem) -> None:
        if len(self._checked()) > MAX_VARIANTS:
            self.list.blockSignals(True)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.blockSignals(False)
            set_tone(self.info, INFO_BOX)
            self.info.setText(f"Сравнить можно до {MAX_VARIANTS} вариантов одновременно.")
            return
        self.refresh()

    def _selected_snapshot(self) -> Snapshot | None:
        row = self.list.currentRow()
        return self.snapshots[row - 1] if row >= 1 else None

    def _update_buttons(self) -> None:
        has = self._selected_snapshot() is not None
        for b in (self.rename_button, self.delete_button, self.open_button):
            b.setEnabled(has)

    def remember_current(self) -> None:
        checked = set(self._checked())
        snapshot = self.window_.remember_variant()
        if snapshot is not None:
            self._fill_list(checked | {len(self.snapshots)})
            self.refresh()

    def add_from_file(self, path: Path | None = None) -> Snapshot | None:
        """Расчёт из другого файла проекта — как вариант (например, «Турция» из своего файла)."""
        if path is None:
            name, _ = QFileDialog.getOpenFileName(
                self, "Добавить вариант из файла", "", "Расчёт ВЭД (*.vedcalc.json *.json)"
            )
            if not name:
                return None
            path = Path(name)
        try:
            shipment, _ = project_from_dict(read_json(path))
        except (OSError, FileFormatError) as exc:
            QMessageBox.warning(self, self.windowTitle(), f"Не удалось открыть файл:\n{exc}")
            return None
        checked = set(self._checked())
        snapshot = Snapshot(shipment.name, shipment)
        self.snapshots.append(snapshot)
        self.window_._mark_dirty()
        self._fill_list(checked | {len(self.snapshots)})
        self.refresh()
        return snapshot

    def rename(self, name: str | None = None) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        if name is None:
            name, ok = QInputDialog.getText(self, "Переименовать", "Название:", text=snapshot.name)
            if not ok:
                return
        snapshot.name = name.strip() or snapshot.name
        self.window_._mark_dirty()
        row = self.list.currentRow()
        self._fill_list()
        self.list.setCurrentRow(row)
        self.refresh()

    def delete(self) -> None:
        row = self.list.currentRow()
        if row < 1:
            return
        checked = {r if r < row else r - 1 for r in self._checked() if r != row}
        del self.snapshots[row - 1]
        self.window_._mark_dirty()
        self._fill_list(checked)
        self.refresh()

    def open_in_editor(self) -> None:
        snapshot = self._selected_snapshot()
        if snapshot is None:
            return
        self.window_.open_variant(snapshot)
        self.accept()

    # --- таблица ----------------------------------------------------------------------

    def _variants(self):
        win = self.window_
        provider = win.calculator.rates.provider
        scale = win.calculator.fee_scale
        out = []
        for r in self._checked():
            if r == 0:
                try:
                    shipment = win.calculator.shipment()
                except InputError as exc:  # ввод с ошибками — показываем ошибку, без столбца
                    out.append(Variant(CURRENT, None, error=str(exc)))  # type: ignore[arg-type]
                    continue
                out.append(evaluate(CURRENT, shipment, provider, scale))
            else:
                s = self.snapshots[r - 1]
                out.append(evaluate(s.name, copy.deepcopy(s.shipment), provider, scale))
        return out

    def refresh(self) -> None:
        all_variants = self._variants()
        variants = [v for v in all_variants if v.shipment is not None]
        broken = [(v.name, v.error) for v in all_variants if v.shipment is None]
        self.table.clearContents()
        if len(variants) < 2:
            self.comparison = None
            self.table.setRowCount(0)
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels(["Статья"])
            set_tone(self.info, INFO_BOX)
            self.info.setText(
                "Запомните варианты расчёта («Файл → Запомнить вариант…» или кнопкой слева) "
                "и отметьте два или больше."
                + "".join(f"<br>{escape(n)}: {escape(e)}" for n, e in broken)
            )
            self.export_button.setEnabled(False)
            return
        c = self.comparison = compare(variants)
        self.export_button.setEnabled(True)
        pal = current()
        self.table.setColumnCount(1 + len(c.names))
        self.table.setHorizontalHeaderLabels(["Статья", *c.names])
        header = self.table.horizontalHeader()
        for k in range(1, len(c.names) + 1):
            header.setSectionResizeMode(k, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(k, 150)
        sections = []
        for row in c.rows:
            if not sections or sections[-1] != row.section:
                sections.append(row.section)
        self.table.setRowCount(len(c.rows) + len(sections))
        r, section = 0, None
        bold = QFont(self.table.font())
        bold.setBold(True)
        for row in c.rows:
            if row.section != section:
                section = row.section
                head = QTableWidgetItem(section)
                head.setFont(bold)
                head.setBackground(QColor(pal.surface_alt))
                self.table.setItem(r, 0, head)
                for k in range(1, len(c.names) + 1):
                    filler = QTableWidgetItem("")
                    filler.setBackground(QColor(pal.surface_alt))
                    self.table.setItem(r, k, filler)
                r += 1
            label = QTableWidgetItem(row.label)
            if row.strong:
                label.setFont(bold)
            self.table.setItem(r, 0, label)
            for k, value in enumerate(row.values):
                text = _fmt(value, row.kind)
                if k and (delta := _fmt_delta(row.delta(k), row.kind)):
                    text += f"\n{delta}"
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if row.strong:
                    item.setFont(bold)
                verdict = row.verdict(k)
                if verdict:
                    item.setForeground(QColor(pal.positive if verdict > 0 else pal.negative))
                self.table.setItem(r, k + 1, item)
            r += 1
        self.table.resizeRowsToContents()
        lines = [f"<b>{escape(n)}</b>: {escape(e)}" for n, e in [*c.errors, *broken]]
        lines += [escape(n) for n in c.notes]
        lines.append("Зелёным — выгоднее первого варианта, красным — дороже или менее прибыльно.")
        set_tone(self.info, INFO_BOX)
        self.info.setText("<br>".join(lines))

    def export(self, path: Path | None = None) -> Path | None:
        if self.comparison is None:
            return None
        if path is None:
            name, _ = QFileDialog.getSaveFileName(
                self, "Выгрузить сравнение", "Сравнение вариантов.xlsx", "Excel (*.xlsx)"
            )
            if not name:
                return None
            path = Path(name)
        from vedcalc.reports.xlsx import write_compare_xlsx

        try:
            write_compare_xlsx(self.comparison, path)
        except OSError as exc:
            QMessageBox.warning(self, self.windowTitle(), f"Не удалось сохранить файл:\n{exc}")
            return None
        return path
