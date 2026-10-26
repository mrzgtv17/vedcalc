"""Панель результатов: себестоимость, безубыточная цена, цена с доходностью, детализация."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money
from vedcalc.core.models import PriceMode
from vedcalc.core.report import Report
from vedcalc.core.validation import Issue
from vedcalc.gui.widgets import DecimalEdit, EnumCombo, SectionView, mark_error
from vedcalc.reports.html import render_html

ERROR_BOX = "background:#fff0f0; border:1px solid #f3b7bd; border-radius:6px; padding:8px;"
WARN_BOX = "background:#fff4e5; border:1px solid #f5d2a3; border-radius:6px; padding:8px;"
INFO_BOX = "background:#eef2f7; border:1px solid #cfd8e3; border-radius:6px; padding:8px;"

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

ITEM_HEADERS = [
    "Позиция",
    "Кол-во",
    "Себест. ед.\nбез НДС",
    "Себест. ед.\nс НДС",
    "Безубыт. цена ед.\nбез НДС",
    "Безубыт. цена ед.\nс НДС",
    "Цена ед. с НДС\n(блок 3)",
]


class ResultsPanel(QTabWidget):
    targetChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.report: Report | None = None

        page = QWidget()
        v = QVBoxLayout(page)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.hide()
        v.addWidget(self.status)

        self.cost_view = SectionView("Себестоимость")
        v.addWidget(self.cost_view)

        self.items_box = QGroupBox("По позициям")
        ib = QVBoxLayout(self.items_box)
        self.items_table = QTableWidget(0, len(ITEM_HEADERS))
        self.items_table.setHorizontalHeaderLabels(ITEM_HEADERS)
        self.items_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.items_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.items_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.items_table.verticalHeader().hide()
        ib.addWidget(self.items_table)
        v.addWidget(self.items_box)

        self.break_even_view = SectionView("Безубыточная цена (чистая прибыль = 0)")
        v.addWidget(self.break_even_view)

        self.target_box = QGroupBox("Цена с наценкой / маржой")
        tb = QVBoxLayout(self.target_box)
        controls = QHBoxLayout()
        self.mode_combo = EnumCombo([(m, m.label) for m in PriceMode])
        self.value_edit = DecimalEdit(Decimal(20))
        self.value_edit.setMaximumWidth(160)
        controls.addWidget(QLabel("Режим:"))
        controls.addWidget(self.mode_combo, 1)
        controls.addWidget(self.value_edit)
        tb.addLayout(controls)
        self.target_error = QLabel()
        self.target_error.setStyleSheet("color:#d70015;")
        self.target_error.hide()
        tb.addWidget(self.target_error)
        self.target_view = SectionView("")
        tb.addWidget(self.target_view)
        v.addWidget(self.target_box)
        v.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.addTab(scroll, "Результаты")

        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.addTab(self.details, "Детализация")

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.value_edit.textChanged.connect(self._on_target_edited)

    # --- сценарий цены ---------------------------------------------------------------

    def set_target(self, mode: PriceMode, value: Decimal) -> None:
        for w in (self.mode_combo, self.value_edit):
            w.blockSignals(True)
        self.mode_combo.set_value(mode)
        self.value_edit.set_value(value)
        for w in (self.mode_combo, self.value_edit):
            w.blockSignals(False)

    def target(self) -> tuple[PriceMode, Decimal] | None:
        try:
            value = self.value_edit.value()
        except ValueError as exc:
            mark_error(self.value_edit, str(exc))
            return None
        mark_error(self.value_edit, None)
        return self.mode_combo.value(), value

    def _on_mode_changed(self) -> None:
        # Подставляем осмысленное значение для нового режима
        mode = self.mode_combo.value()
        if self.report is not None:
            current = self.report.target or self.report.break_even
            suggestion = {
                PriceMode.MARKUP: Decimal(20),
                PriceMode.MARGIN: Decimal(20),
                PriceMode.NET_PROFIT: (
                    current.net_profit if current.net_profit > 0 else Decimal(10000)
                ),
                PriceMode.PRICE: current.price_net,
            }[mode]
            self.value_edit.blockSignals(True)
            self.value_edit.set_value(suggestion)
            self.value_edit.blockSignals(False)
        self.targetChanged.emit()

    def _on_target_edited(self) -> None:
        self.targetChanged.emit()

    # --- вывод -----------------------------------------------------------------------

    def _set_status(self, html: str | None, style: str = INFO_BOX) -> None:
        if not html:
            self.status.hide()
            return
        self.status.setStyleSheet(style)
        self.status.setText(html)
        self.status.show()

    def _set_results_enabled(self, enabled: bool) -> None:
        # Без результата блоки не показываем вовсе — пустые серые рамки только путают.
        for w in (self.cost_view, self.items_box, self.break_even_view, self.target_box):
            w.setVisible(enabled)

    def show_issues(self, issues: list[Issue], *, gentle: bool = False) -> None:
        """gentle — расчёт только открыт и ещё не заполнен: подсказка вместо ошибки."""
        items = "".join(f"<li>{escape(i.message)}</li>" for i in issues)
        title = "Заполните данные для расчёта:" if gentle else "Проверьте ввод:"
        self._set_status(
            f"<b>{title}</b><ul style='margin:4px 0'>{items}</ul>",
            INFO_BOX if gentle else ERROR_BOX,
        )
        self._set_results_enabled(False)

    def show_message(self, html: str, *, error: bool = False) -> None:
        self._set_status(html, ERROR_BOX if error else INFO_BOX)
        self._set_results_enabled(False)

    def show_report(self, report: Report) -> None:
        self.report = report
        self._set_results_enabled(True)
        if report.cost.warnings:
            items = "".join(f"<li>{escape(w)}</li>" for w in report.cost.warnings)
            self._set_status(f"<ul style='margin:0'>{items}</ul>", WARN_BOX)
        else:
            self._set_status(None)

        self.cost_view.set_rows(report.cost_section().rows)
        be = report.break_even_section()
        self.break_even_view.set_rows(be.rows)
        target = report.target_section()
        self.target_box.setTitle(target.title)
        if report.target is None:
            self.target_error.setText(report.target_error or "")
            self.target_error.show()
            self.target_view.set_rows(())
        else:
            self.target_error.hide()
            self.target_view.set_rows(target.rows)
        self._fill_items(report)
        scroll = self.details.verticalScrollBar().value()
        self.details.setHtml(render_html(report))
        self.details.verticalScrollBar().setValue(scroll)

    def _fill_items(self, report: Report) -> None:
        cost, be, tg = report.cost.items, report.break_even.items, report.target
        self.items_table.setRowCount(len(cost))
        for r, item in enumerate(cost):
            cells = [
                item.name,
                f"{item.quantity.normalize():f} {item.unit.label}".replace(".", ","),
                fmt_money(item.unit_cost_net),
                fmt_money(item.unit_cost_gross),
                fmt_money(be[r].unit_price_net),
                fmt_money(be[r].unit_price_gross),
                fmt_money(tg.items[r].unit_price_gross) if tg else "—",
            ]
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if c:
                    cell.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.items_table.setItem(r, c, cell)
        t = self.items_table
        t.resizeRowsToContents()
        rows_h = sum(t.rowHeight(r) for r in range(len(cost)))
        scroll_h = t.horizontalScrollBar().sizeHint().height()
        t.setFixedHeight(
            t.horizontalHeader().sizeHint().height() + rows_h + scroll_h + 2 * t.frameWidth()
        )
