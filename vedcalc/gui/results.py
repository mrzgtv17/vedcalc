"""Панель результатов: упор на единицу товара (1 шт / кг / т), итоги партии — ниже."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money, fmt_pct
from vedcalc.core.models import PriceMode
from vedcalc.core.report import Report
from vedcalc.core.unit_economics import UnitLine, UnitView
from vedcalc.core.validation import Issue
from vedcalc.gui.theme import ERROR_BOX, INFO_BOX, WARN_BOX
from vedcalc.gui.widgets import DecimalEdit, EnumCombo, SectionView, mark_error
from vedcalc.reports.html import render_html
from vedcalc.reports.style import NEGATIVE, POSITIVE, TEXT_MUTED

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def _label(text: str, name: str = "", *, right: bool = False, bold: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    if right:
        label.setAlignment(RIGHT)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if bold:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
    label.setMinimumHeight(label.fontMetrics().height() + 4)
    return label


def _card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)
    return frame, layout


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


class MetricTile(QFrame):
    """Крупное число с подписью: «Себестоимость 147 731,00 — с НДС 177 761,00»."""

    def __init__(self, caption: str, value: str, sub: str = "", *, accent: bool = False):
        super().__init__()
        self.setObjectName("metric")
        self.setProperty("accent", accent)
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(2)
        cap = _label(caption, "caption")
        cap.setWordWrap(True)
        self.value = _label(value, "metricValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.value.setProperty("caption", caption)
        v.addWidget(cap)
        v.addWidget(self.value)
        if sub:
            s = _label(sub, "metricSub")
            s.setWordWrap(True)
            v.addWidget(s)


class UnitCard(QFrame):
    """Карточка позиции «за 1 шт / кг / т»: показатели, состав себестоимости, цены."""

    def __init__(self, view: UnitView, report: Report):
        super().__init__()
        self.setObjectName("card")
        self.view = view
        osn = report.vat_payer
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 16)
        v.setSpacing(12)

        head = QHBoxLayout()
        head.addWidget(_label(view.name or "Позиция", "cardTitle"))
        chip = _label(view.per, "chip")
        head.addWidget(chip)
        head.addStretch(1)
        qty = f"{view.quantity.normalize():f}".replace(".", ",")
        head.addWidget(_label(f"в партии {qty} {view.unit_label}", "muted"))
        v.addLayout(head)

        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        vat_sub = (lambda x: f"с НДС {fmt_money(x)}") if osn else (lambda x: "")
        self.tiles: dict[str, MetricTile] = {}

        def tile(key: str, caption: str, value: Decimal, sub: str, accent: bool = False):
            t = MetricTile(caption, fmt_money(value), sub, accent=accent)
            self.tiles[key] = t
            tiles.addWidget(t, 1)

        tile(
            "cost",
            "Себестоимость без НДС" if osn else "Себестоимость (УСН)",
            view.cost_net,
            vat_sub(view.cost_gross),
        )
        tile("break_even", "Безубыточная цена", view.break_even.net, vat_sub(view.break_even.gross))
        if view.target is not None:
            sub = vat_sub(view.target.gross)
            profit = f"прибыль {fmt_money(view.target.net_profit)}"
            tile(
                "target",
                "Цена по сценарию",
                view.target.net,
                f"{sub} · {profit}" if sub else profit,
                accent=True,
            )
        v.addLayout(tiles)

        v.addWidget(_label(f"Из чего складывается себестоимость {view.per}", "caption"))
        v.addLayout(self._lines_grid(view.cost_lines))
        v.addWidget(_divider())
        v.addWidget(_label(f"Цена и прибыль {view.per}", "caption"))
        v.addLayout(self._price_grid(view))

    def _lines_grid(self, lines: tuple[UnitLine, ...]) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        for n, line in enumerate(lines):
            label = _label(line.label, "muted" if line.informational else "", bold=line.strong)
            value = _label(
                line.text, "muted" if line.informational else "", right=True, bold=line.strong
            )
            value.setObjectName(f"unit:{line.label}")
            if line.informational:
                value.setStyleSheet(f"color:{TEXT_MUTED};")
            tip = line.formula + (f"\n{line.hint}" if line.hint else "")
            label.setToolTip(tip)
            value.setToolTip(tip)
            grid.addWidget(label, n, 0)
            grid.addWidget(value, n, 1)
        return grid

    def _price_grid(self, view: UnitView) -> QGridLayout:
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        columns = [("Безубыточная", view.break_even)]
        if view.target is not None:
            columns.append(("По сценарию", view.target))
        for c, (title, _) in enumerate(columns, 1):
            grid.addWidget(_label(title, "caption", right=True), 0, c)
        for n, line in enumerate(view.break_even.lines, 1):
            grid.addWidget(_label(line.label, bold=line.strong), n, 0)
            for c, (title, price) in enumerate(columns, 1):
                cell = {x.label: x for x in price.lines}.get(line.label)
                if cell is None:
                    continue
                value = _label(cell.text, right=True, bold=cell.strong)
                value.setObjectName(f"price:{title}:{cell.label}")
                value.setToolTip(cell.formula)
                if cell.label == "Чистая прибыль" and cell.value:
                    value.setStyleSheet(f"color:{POSITIVE if cell.value > 0 else NEGATIVE};")
                grid.addWidget(value, n, c)
        return grid

    def metric_text(self, key: str) -> str | None:
        tile = self.tiles.get(key)
        return tile.value.text() if tile else None

    def line_text(self, label: str) -> str | None:
        w = self.findChild(QLabel, f"unit:{label}")
        return w.text() if w else None

    def price_text(self, column: str, label: str) -> str | None:
        w = self.findChild(QLabel, f"price:{column}:{label}")
        return w.text() if w else None


class ResultsPanel(QTabWidget):
    targetChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.report: Report | None = None
        self.unit_cards: list[UnitCard] = []

        page = QWidget()
        page.setObjectName("page")
        v = QVBoxLayout(page)
        v.setContentsMargins(2, 2, 6, 2)
        v.setSpacing(12)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.hide()
        v.addWidget(self.status)

        # Сценарий цены
        self.target_box, tb = _card()
        tb.addWidget(_label("Сценарий цены", "cardTitle"))
        controls = QHBoxLayout()
        self.mode_combo = EnumCombo([(m, m.label) for m in PriceMode])
        self.value_edit = DecimalEdit(Decimal(20))
        self.value_edit.setMaximumWidth(150)
        controls.addWidget(self.mode_combo, 1)
        controls.addWidget(self.value_edit)
        tb.addLayout(controls)
        self.target_summary = _label("", "muted")
        self.target_summary.setWordWrap(True)
        tb.addWidget(self.target_summary)
        self.target_error = _label("")
        self.target_error.setStyleSheet(f"color:{NEGATIVE};")
        self.target_error.setWordWrap(True)
        self.target_error.hide()
        tb.addWidget(self.target_error)
        v.addWidget(self.target_box)

        # Карточки «за единицу» по позициям
        self.units_host = QWidget()
        self.units_layout = QVBoxLayout(self.units_host)
        self.units_layout.setContentsMargins(0, 0, 0, 0)
        self.units_layout.setSpacing(12)
        v.addWidget(self.units_host)

        # Итоги по партии
        self.batch_view = SectionView("Итого по партии")
        v.addWidget(self.batch_view)
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
            units = self.report.unit_views()
            unit_price = (units[0].target or units[0].break_even).net if units else Decimal(0)
            suggestion = {
                PriceMode.MARKUP: Decimal(20),
                PriceMode.MARGIN: Decimal(20),
                PriceMode.NET_PROFIT: (
                    current.net_profit if current.net_profit > 0 else Decimal(10000)
                ),
                PriceMode.UNIT_PRICE: unit_price,
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
        # Без результата блоки не показываем вовсе — пустые рамки только путают.
        for w in (self.target_box, self.units_host, self.batch_view):
            w.setVisible(enabled)

    def show_issues(self, issues: list[Issue], *, gentle: bool = False) -> None:
        """gentle — расчёт только открыт и ещё не заполнен: подсказка вместо ошибки."""
        items = "".join(f"<li>{escape(i.message)}</li>" for i in issues)
        title = "Заполните данные для расчёта:" if gentle else "Проверьте ввод:"
        self._set_status(
            f"<b>{title}</b><ul style='margin:4px 0 0 -18px'>{items}</ul>",
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
            self._set_status(f"<ul style='margin:0 0 0 -18px'>{items}</ul>", WARN_BOX)
        else:
            self._set_status(None)

        if report.target is None:
            self.target_error.setText(report.target_error or "")
            self.target_error.show()
            self.target_summary.hide()
        else:
            t = report.target
            self.target_error.hide()
            self.target_summary.setText(
                f"{report.target_title}. Наценка {fmt_pct(t.markup)} · маржинальность "
                f"{fmt_pct(t.margin)} · чистая прибыль партии {fmt_money(t.net_profit)}"
            )
            self.target_summary.show()

        for card in self.unit_cards:
            card.setParent(None)
            card.deleteLater()
        self.unit_cards = [UnitCard(view, report) for view in report.unit_views()]
        for card in self.unit_cards:
            self.units_layout.addWidget(card)

        self.batch_view.set_rows(report.batch_section().rows)

        scroll = self.details.verticalScrollBar().value()
        self.details.setHtml(render_html(report, font_family=self.font().family()))
        self.details.verticalScrollBar().setValue(scroll)
