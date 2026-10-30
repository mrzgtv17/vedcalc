"""Карточка «Ступенчатый прайс» на вкладке «Цена продажи»."""

from __future__ import annotations

import copy
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money, fmt_pct
from vedcalc.core.models import PriceTier, TierPricing
from vedcalc.core.report import PriceReport
from vedcalc.core.tiers import TierTable, tier_label, tier_table, validate
from vedcalc.core.validation import InputError, Issue
from vedcalc.gui.theme import set_tone
from vedcalc.gui.widgets import DecimalEdit, mark_error

MAX_TIERS = 8


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


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif child := item.layout():
            _clear(child)


class TierView(QFrame):
    """Пороги «от N единиц — скидка %», расходы на заказ и таблица цены и прибыли."""

    modified = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.report: PriceReport | None = None
        self.params = TierPricing()
        self.table: TierTable | None = None
        self.rows: list[tuple[DecimalEdit, DecimalEdit]] = []
        self._loading = False

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        v.addWidget(_label("Ступенчатый прайс", "cardTitle"))
        self.intro = _label("", "muted")
        self.intro.setWordWrap(True)
        v.addWidget(self.intro)

        top = QGridLayout()
        top.setHorizontalSpacing(10)
        self.item_label = _label("Позиция", "muted")
        self.item_combo = QComboBox()
        self.item_combo.setMinimumWidth(240)
        self.item_combo.currentIndexChanged.connect(self._on_edit)
        top.addWidget(self.item_label, 0, 0)
        top.addWidget(self.item_combo, 0, 1)
        top.addWidget(_label("Расходы на заказ, ₽ без НДС", "muted"), 1, 0)
        self.order_cost = DecimalEdit(None, optional=True, placeholder="например, 3000")
        self.order_cost.setMaximumWidth(160)
        self.order_cost.setToolTip(
            "Доставка до клиента, документы, упаковка — на один заказ. "
            "Делятся на объём заказа: у маленького заказа прибыль на единицу ниже."
        )
        self.order_cost.textChanged.connect(self._on_edit)
        top.addWidget(self.order_cost, 1, 1)
        top.setColumnStretch(2, 1)
        v.addLayout(top)

        self.inputs = QGridLayout()
        self.inputs.setColumnStretch(3, 1)
        self.inputs.setHorizontalSpacing(8)
        self.inputs.setVerticalSpacing(4)
        v.addLayout(self.inputs)
        self.add_button = QPushButton("+ Порог")
        self.add_button.clicked.connect(self.add_tier)
        add_row = QHBoxLayout()
        add_row.addWidget(self.add_button)
        add_row.addStretch(1)
        v.addLayout(add_row)

        self.body = QGridLayout()
        self.body.setHorizontalSpacing(14)
        self.body.setVerticalSpacing(2)
        self.body.setColumnStretch(0, 1)
        v.addLayout(self.body)
        self.notes = QVBoxLayout()
        v.addLayout(self.notes)
        self._build_inputs()

    # --- ввод -------------------------------------------------------------------------

    def set_params(self, params: TierPricing) -> None:
        self.params = copy.deepcopy(params)
        self._loading = True
        try:
            self.order_cost.set_value(params.order_cost or None)
            self._build_inputs()
            self._fill_items()
        finally:
            self._loading = False
        self.refresh()

    def _build_inputs(self) -> None:
        _clear(self.inputs)
        self.rows = []
        self.inputs.addWidget(_label("Заказ от, ед.", "caption"), 0, 0)
        self.inputs.addWidget(_label("Скидка от базовой цены, %", "caption"), 0, 1)
        for tier in self.params.tiers:
            self._add_row(tier)
        self.add_button.setEnabled(len(self.rows) < MAX_TIERS)

    def _add_row(self, tier: PriceTier) -> None:
        n = len(self.rows) + 1
        qty = DecimalEdit(tier.min_qty)
        disc = DecimalEdit(tier.discount_pct)
        qty.setMaximumWidth(120)
        disc.setMaximumWidth(120)
        delete = QToolButton()
        delete.setText("✕")
        delete.setObjectName("rowDelete")
        delete.setToolTip("Удалить порог")
        delete.clicked.connect(lambda _=False, e=qty: self._remove(e))
        for w in (qty, disc):
            w.textChanged.connect(self._on_edit)
        self.inputs.addWidget(qty, n, 0)
        self.inputs.addWidget(disc, n, 1)
        self.inputs.addWidget(delete, n, 2)
        self.rows.append((qty, disc))

    def add_tier(self) -> None:
        if len(self.rows) >= MAX_TIERS:
            return
        last = self.params.tiers[-1].min_qty if self.params.tiers else Decimal(0)
        disc = self.params.tiers[-1].discount_pct if self.params.tiers else Decimal(0)
        self.params.tiers.append(PriceTier(last * 2 if last else Decimal(10), disc))
        self._build_inputs()
        self._on_edit()

    def _remove(self, qty_edit: DecimalEdit) -> None:
        index = next(n for n, (q, _) in enumerate(self.rows) if q is qty_edit)
        self.read()
        del self.params.tiers[index]
        self._build_inputs()
        self._on_edit()

    def _fill_items(self) -> None:
        views = self.report.unit_views() if self.report else []
        names = [v.name for v in views]
        if names != [self.item_combo.itemText(i) for i in range(self.item_combo.count())]:
            self.item_combo.blockSignals(True)
            self.item_combo.clear()
            self.item_combo.addItems(names)
            self.item_combo.blockSignals(False)
        if names:
            self.item_combo.setCurrentIndex(min(self.params.item, len(names) - 1))
        visible = len(names) > 1
        self.item_label.setVisible(visible)
        self.item_combo.setVisible(visible)

    def read(self) -> list[Issue]:
        """Читает ввод в self.params (только корректный) и подсвечивает ошибки."""
        issues: list[Issue] = []
        tiers = []
        for n, (qty, disc) in enumerate(self.rows, 1):
            values = []
            for edit, path in ((qty, "min_qty"), (disc, "discount")):
                try:
                    values.append(edit.value())
                    mark_error(edit, None)
                except ValueError as exc:
                    issues.append(Issue(f"tiers.{n}.{path}", f"Порог {n}: {exc}"))
                    mark_error(edit, str(exc))
                    values.append(None)
            if None not in values:
                tiers.append(PriceTier(values[0], values[1]))
        try:
            order = self.order_cost.value() or Decimal(0)
            mark_error(self.order_cost, None)
        except ValueError as exc:
            issues.append(Issue("order_cost", f"Расходы на заказ: {exc}"))
            mark_error(self.order_cost, str(exc))
            order = self.params.order_cost
        item = max(self.item_combo.currentIndex(), 0) if self.item_combo.count() else 0
        candidate = TierPricing(tiers, order, item)
        if issues:
            return issues
        if more := validate(candidate):
            for issue in more:
                if issue.path == "order_cost":
                    mark_error(self.order_cost, issue.message)
                else:
                    n, field = issue.path.split(".")[1:]
                    edit = self.rows[int(n) - 1][0 if field == "min_qty" else 1]
                    mark_error(edit, issue.message)
            return more
        self.params = candidate
        return []

    def _on_edit(self, *_args) -> None:
        if self._loading:
            return
        if not self.read():
            self.modified.emit()
        self.refresh()

    # --- результат ----------------------------------------------------------------------

    def set_report(self, report: PriceReport | None) -> None:
        self.report = report
        self._loading = True
        try:
            self._fill_items()
        finally:
            self._loading = False
        self.refresh()

    def refresh(self) -> None:
        _clear(self.body)
        _clear(self.notes)
        try:
            self.table = tier_table(self.report, self.params) if self.report else None
        except InputError:
            self.table = None
        self.setVisible(self.report is not None and bool(self.report.unit_views()))
        t = self.table
        if t is None:
            self.intro.setText("Задайте пороги по количеству — от 10, 50, 100 единиц.")
            return
        self.intro.setText(
            f"Цена за единицу при заказе от N {t.unit_label}. Базовая — цена по сценарию: "
            f"{fmt_money(t.base_price_net)} ₽ без НДС за 1 {t.unit_label}."
        )
        heads = ["Заказ", "Скидка", "Цена без НДС"]
        if t.vat_payer:
            heads.append("Цена с НДС")
        heads += ["Прибыль на ед.", "Маржа"]
        for c, head in enumerate(heads):
            self.body.addWidget(_label(head, "caption", right=c > 0), 0, c)
        for r, row in enumerate(t.rows, 1):
            cells = [tier_label(row, t.unit_label), fmt_pct(row.discount_pct / 100, 1),
                     fmt_money(row.price_net)]  # fmt: skip
            if t.vat_payer:
                cells.append(fmt_money(row.price_gross))
            cells += [fmt_money(row.net_profit), fmt_pct(row.margin)]
            for c, text in enumerate(cells):
                label = _label(text, right=c > 0)
                label.setObjectName(f"tier:{r}:{c}")
                if c == len(cells) - 2:
                    set_tone(label, "neg" if row.loss else "pos")
                elif row.over_batch:
                    set_tone(label, "muted")
                if row.order_cost:
                    label.setToolTip(
                        f"в т.ч. расходы на заказ {fmt_money(row.order_cost)} ₽ на ед."
                    )
                self.body.addWidget(label, r, c)
        for note in t.notes:
            label = _label(note, "caption")
            label.setWordWrap(True)
            self.notes.addWidget(label)
