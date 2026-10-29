"""Блок «Что если» на вкладке «Цена продажи»: таблицы чувствительности и график по курсу."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from vedcalc.core.explain import fmt_money, fmt_pct
from vedcalc.core.fees import FeeScale
from vedcalc.core.report import PriceReport
from vedcalc.core.whatif import ScenarioResult, Table, WhatIf, sensitivity
from vedcalc.gui.theme import current, notifier
from vedcalc.gui.widgets import DecimalEdit, mark_error


def _label(text: str = "", name: str = "", *, right: bool = False, bold: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    if right:
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if bold:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
    return label


def fmt_delta(value: Decimal) -> str:
    if value == 0:
        return "—"
    return ("+" if value > 0 else "−") + fmt_money(abs(value))


class ProfitChart(QWidget):
    """Чистая прибыль партии в зависимости от курса: линия по точкам таблицы."""

    def __init__(self, rows: tuple[ScenarioResult, ...], parent: QWidget | None = None):
        super().__init__(parent)
        self.rows = rows
        self.setMinimumHeight(170)

    def paintEvent(self, event) -> None:
        if len(self.rows) < 2:
            return
        p = QPainter(self)
        pal = current()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.85)
        p.setFont(font)
        left, top, right, bottom = 82, 10, 26, 26
        plot = QRectF(left, top, self.width() - left - right, self.height() - top - bottom)
        xs = [float(r.scenario.fx_pct) for r in self.rows]
        ys = [float(r.net_profit) for r in self.rows]
        y_min, y_max = min([*ys, 0.0]), max([*ys, 0.0])
        if y_max == y_min:
            y_max += 1
        x_min, x_max = min(xs), max(xs)

        def pt(x: float, y: float) -> QPointF:
            return QPointF(
                plot.left() + (x - x_min) / (x_max - x_min) * plot.width(),
                plot.bottom() - (y - y_min) / (y_max - y_min) * plot.height(),
            )

        p.setPen(QPen(QColor(pal.border), 1))
        p.drawRect(plot)
        zero = pt(x_min, 0).y()
        p.setPen(QPen(QColor(pal.text_muted), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), zero), QPointF(plot.right(), zero))
        p.setPen(QColor(pal.text_muted))
        for value in (y_min, 0.0, y_max):
            y = pt(x_min, value).y()
            p.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignmentFlag.AlignRight,
                       fmt_money(Decimal(round(value))).removesuffix(",00"))  # fmt: skip
        for row in self.rows:
            point = pt(float(row.scenario.fx_pct), 0)
            label = row.scenario.label.replace("текущий", "0%")
            p.drawText(QRectF(point.x() - 30, plot.bottom() + 4, 60, 16),
                       Qt.AlignmentFlag.AlignHCenter, label)  # fmt: skip
        p.setPen(QPen(QColor(pal.accent), 2))
        points = [pt(x, y) for x, y in zip(xs, ys, strict=True)]
        for a, b in pairwise(points):
            p.drawLine(a, b)
        for row, point in zip(self.rows, points, strict=True):
            color = pal.positive if row.net_profit >= 0 else pal.negative
            p.setBrush(QColor(color))
            p.setPen(QPen(QColor(color), 1))
            radius = 4.5 if row.scenario.is_base else 3
            p.drawEllipse(point, radius, radius)
        p.end()


class WhatIfView(QFrame):
    """Карточка «Что если»: курс ±15 %, фрахт ±20 %, пошлина ±5 п. п. и своя ставка."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.report: PriceReport | None = None
        self.fee_scale: FeeScale | None = None
        self.whatif: WhatIf | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        v.addWidget(_label("Что если", "cardTitle"))
        self.intro = _label("", "muted")
        self.intro.setWordWrap(True)
        v.addWidget(self.intro)
        custom = QHBoxLayout()
        custom.addWidget(_label("Своя ставка пошлины для сравнения, %", "muted"))
        self.custom_duty = DecimalEdit(None, optional=True, placeholder="например, 0")
        self.custom_duty.setMaximumWidth(140)
        self.custom_duty.textChanged.connect(lambda _=None: self.refresh())
        custom.addWidget(self.custom_duty)
        custom.addStretch(1)
        v.addLayout(custom)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        v.addLayout(self.body)
        notifier().changed.connect(self.refresh)  # цвета таблиц — из текущей темы

    def set_report(self, report: PriceReport | None, fee_scale: FeeScale | None) -> None:
        self.report, self.fee_scale = report, fee_scale
        self.refresh()

    def _custom(self) -> Decimal | None:
        try:
            value = self.custom_duty.value()
        except ValueError as exc:
            mark_error(self.custom_duty, str(exc))
            return None
        if value is not None and value < 0:
            mark_error(self.custom_duty, "Ставка не может быть отрицательной")
            return None
        mark_error(self.custom_duty, None)
        return value

    def _clear(self) -> None:
        while self.body.count():
            item = self.body.takeAt(0)
            if widget := item.widget():
                widget.hide()  # убираем сразу: deleteLater сработает лишь в цикле событий
                widget.setParent(None)
                widget.deleteLater()

    def refresh(self) -> None:
        self._clear()
        report = self.report
        self.whatif = sensitivity(report, self.fee_scale, self._custom()) if report else None
        self.setVisible(self.whatif is not None)
        if self.whatif is None:
            return
        w = self.whatif
        self.intro.setText(f"Цена продажи не меняется: {fmt_money(w.price_net)} ₽ без НДС за "
                           f"партию. {w.scope_text}")  # fmt: skip
        for table in w.tables:
            self.body.addWidget(self._table(table))
            if table.title == "Курс валют":
                self.body.addWidget(ProfitChart(table.rows))
        for note in w.notes:
            label = _label(note, "caption")
            label.setWordWrap(True)
            self.body.addWidget(label)

    def _table(self, table: Table) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(_label(table.title, bold=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(0, 1)
        for c, head in enumerate(self.whatif.heads):
            grid.addWidget(_label(head, "caption", right=c > 0), 0, c)
        pal = current()
        for r, row in enumerate(table.rows, 1):
            cost = row.unit_cost if row.unit_cost is not None else row.cost_net
            profit_color = pal.positive if row.net_profit >= 0 else pal.negative
            delta_color = (
                pal.text_muted
                if row.profit_delta == 0
                else pal.positive
                if row.profit_delta > 0
                else pal.negative
            )
            cells = (
                (row.scenario.label, ""),
                (fmt_money(cost), ""),
                (fmt_money(row.net_profit), profit_color),
                (fmt_pct(row.margin), ""),
                (fmt_delta(row.profit_delta), delta_color),
            )
            for c, (text, color) in enumerate(cells):
                label = _label(text, right=c > 0, bold=row.scenario.is_base)
                label.setObjectName(f"whatif:{table.title}:{r}:{c}")
                style = f"color:{color};" if color else ""
                if row.scenario.is_base:
                    style += f"background:{pal.surface_alt};"
                label.setStyleSheet(style + "padding:2px 4px;")
                grid.addWidget(label, r, c)
        v.addLayout(grid)
        note = _label(table.note, "caption")
        note.setWordWrap(True)
        v.addWidget(note)
        return box
