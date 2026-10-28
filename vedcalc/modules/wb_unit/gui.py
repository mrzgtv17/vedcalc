"""Вкладка «WB»: юнит-экономика Wildberries для ИП на УСН (отдельно от импорта)."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money, fmt_number, fmt_pct
from vedcalc.gui.theme import ERROR_BOX, WARN_BOX
from vedcalc.gui.widgets import DecimalEdit, EnumCombo, mark_error
from vedcalc.modules.wb_unit.calc import (
    Scheme,
    Target,
    TaxMode,
    WbInput,
    WbInputError,
    WbResult,
    compare_schemes,
    liters_of,
    load_tariffs,
    solve,
)
from vedcalc.reports.style import NEGATIVE, POSITIVE, TEXT_MUTED

RECALC_DELAY_MS = 150
DEFAULT_TAX = {TaxMode.INCOME: Decimal(6), TaxMode.INCOME_MINUS_EXPENSES: Decimal(15)}


def _label(text: str = "", name: str = "", *, right: bool = False, bold: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    if right:
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if bold:
        font = label.font()
        font.setBold(True)
        label.setFont(font)
    return label


def _box(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, form


def _tile(caption: str, value: str, sub: str, accent: bool = False) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("metric")
    frame.setProperty("accent", accent)
    v = QVBoxLayout(frame)
    v.setContentsMargins(12, 10, 12, 10)
    v.setSpacing(2)
    v.addWidget(_label(caption, "caption"))
    val = _label(value, "metricValue")
    v.addWidget(val)
    s = _label(sub, "metricSub")
    s.setWordWrap(True)
    v.addWidget(s)
    return frame, val


class WbTab(QWidget):
    modified = Signal()  # пользователь изменил ввод — проект нужно сохранить

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading = False
        self.tariffs = load_tariffs()
        self.result: WbResult | None = None
        self.schemes: dict[Scheme, WbResult] = {}
        self._fields: dict[str, DecimalEdit] = {}

        form = QWidget()
        fv = QVBoxLayout(form)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.addWidget(self._build_product())
        fv.addWidget(self._build_price())
        fv.addWidget(self._build_scheme())
        fv.addWidget(self._build_logistics())
        fv.addWidget(self._build_costs())
        fv.addWidget(self._build_tax())
        fv.addWidget(self._build_tariffs())
        fv.addStretch(1)
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.Shape.NoFrame)
        left.setWidget(form)
        left.setMinimumWidth(460)

        page = QWidget()
        page.setObjectName("page")
        self.out = QVBoxLayout(page)
        self.out.setContentsMargins(2, 2, 6, 2)
        self.out.setSpacing(12)
        self.status = _label()
        self.status.setWordWrap(True)
        self.status.hide()
        self.out.addWidget(self.status)
        self.result_box = QFrame()
        self.result_box.setObjectName("card")
        rv = QVBoxLayout(self.result_box)
        rv.setContentsMargins(16, 14, 16, 16)
        rv.setSpacing(12)
        self.title = _label("", "cardTitle")
        rv.addWidget(self.title)
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        t1, self.price_value = _tile("Цена продажи", "", "")
        t2, self.payout_value = _tile("К перечислению от WB", "", "")
        t3, self.profit_value = _tile("Чистая прибыль", "", "", accent=True)
        self.price_sub = t1.findChildren(QLabel)[-1]
        self.payout_sub = t2.findChildren(QLabel)[-1]
        self.profit_sub = t3.findChildren(QLabel)[-1]
        for t in (t1, t2, t3):
            tiles.addWidget(t, 1)
        rv.addLayout(tiles)
        rv.addWidget(_label("На одну проданную единицу", "caption"))
        self.lines_grid = QGridLayout()
        self.lines_grid.setHorizontalSpacing(14)
        self.lines_grid.setVerticalSpacing(4)
        self.lines_grid.setColumnStretch(1, 1)
        rv.addLayout(self.lines_grid)
        self.out.addWidget(self.result_box)

        self.compare_box = QFrame()
        self.compare_box.setObjectName("card")
        cv = QVBoxLayout(self.compare_box)
        cv.setContentsMargins(16, 14, 16, 14)
        cv.addWidget(_label("FBO или FBS", "cardTitle"))
        self.compare_grid = QGridLayout()
        cv.addLayout(self.compare_grid)
        self.out.addWidget(self.compare_box)
        self.out.addStretch(1)
        right = QScrollArea()
        right.setWidgetResizable(True)
        right.setFrameShape(QFrame.Shape.NoFrame)
        right.setWidget(page)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([560, 760])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RECALC_DELAY_MS)
        self._timer.timeout.connect(self.recalc)
        self._loading = True
        self.set_input(WbInput())
        self._loading = False

    # --- форма --------------------------------------------------------------------------

    def _num(self, key: str, form: QFormLayout, label: str, tip: str = "", **kw) -> DecimalEdit:
        edit = DecimalEdit(**kw)
        edit.setToolTip(tip)
        edit.textChanged.connect(self._changed)
        self._fields[key] = edit
        form.addRow(label, edit)
        return edit

    def _build_product(self) -> QGroupBox:
        box, form = _box("Товар")
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._changed)
        form.addRow("Название", self.name_edit)
        dims = QHBoxLayout()
        for key in ("length_cm", "width_cm", "height_cm"):
            edit = DecimalEdit()
            edit.textChanged.connect(self._changed)
            self._fields[key] = edit
            dims.addWidget(edit)
        form.addRow("Упаковка Д × Ш × В, см", dims)
        self.liters_label = _label("", "muted")
        form.addRow("Объём", self.liters_label)
        return box

    def _build_price(self) -> QGroupBox:
        box, form = _box("Цена")
        self.target_combo = EnumCombo([(t, t.label) for t in Target])
        self.target_combo.currentIndexChanged.connect(self._apply_target_ui)
        form.addRow("Режим", self.target_combo)
        self.retail_edit = self._num("retail_price", form, "Цена до скидки, ₽",
                                     "Цена на витрине до скидки продавца")  # fmt: skip
        self.target_edit = self._num("target_value", form, "Цель")
        self.target_label = form.labelForField(self.target_edit)
        self._num("seller_discount_pct", form, "Скидка продавца, %",
                  "Цена продажи = цена до скидки × (1 − скидка). СПП WB — за счёт WB")  # fmt: skip
        return box

    def _build_scheme(self) -> QGroupBox:
        box, form = _box("Схема и комиссия")
        self.scheme_combo = EnumCombo([(s, s.label) for s in Scheme])
        self.scheme_combo.currentIndexChanged.connect(self._changed)
        form.addRow("Схема", self.scheme_combo)
        self._num("commission_fbo_pct", form, "Комиссия FBO, %", "Из кабинета: по категории")
        self._num("commission_fbs_pct", form, "Комиссия FBS, %", "Из кабинета: по категории")
        return box

    def _build_logistics(self) -> QGroupBox:
        box, form = _box("Логистика и склад")
        self._num("buyout_pct", form, "Выкуп, %",
                  "Доля выкупленных заказов: за невыкуп платится обратная логистика")  # fmt: skip
        self._num("warehouse_coef_pct", form, "Коэффициент склада, %",
                  "С 15.08.2026 единый: 170% для обычных товаров")  # fmt: skip
        self._num("localization_index", form, "Индекс локализации",
                  "Только для поставок, созданных до 15.08.2026; иначе 1")  # fmt: skip
        self._num("storage_days", form, "Хранение до продажи, дней", "Только FBO")
        self._num("acceptance_coef", form, "Коэффициент приёмки",
                  "0 — бесплатная приёмка; только FBO")  # fmt: skip
        return box

    def _build_costs(self) -> QGroupBox:
        box, form = _box("Расходы на единицу")
        self._num("cost_price", form, "Себестоимость, ₽", "Закупка и доставка до склада")
        self._num("acquiring_pct", form, "Эквайринг, %", "От цены продажи")
        self._num("ads_pct", form, "Реклама, %", "От цены продажи")
        self._num("other_per_unit", form, "Прочие, ₽", "Упаковка, маркировка, доставка до WB")
        return box

    def _build_tax(self) -> QGroupBox:
        box, form = _box("Налог (ИП)")
        self.tax_combo = EnumCombo([(t, t.label) for t in TaxMode])
        self.tax_combo.currentIndexChanged.connect(self._on_tax_mode)
        form.addRow("Режим", self.tax_combo)
        self._num("tax_rate_pct", form, "Ставка, %",
                  "«Доходы» — 1…8%; «Доходы минус расходы» — ставка региона")  # fmt: skip
        return box

    def _build_tariffs(self) -> QGroupBox:
        box, form = _box("Базовые тарифы WB")
        t = self.tariffs
        info = _label(
            f"Короба, малогабарит. Действуют с {t.valid_from:%d.%m.%Y}, сверены "
            f"{t.verified_on:%d.%m.%Y}. Если WB поменял тарифы — исправьте здесь.",
            "muted",
        )
        info.setWordWrap(True)
        form.addRow(info)
        self._num("first_liter", form, "Первый литр, ₽")
        self._num("extra_liter", form, "Доп. литр, ₽")
        self._num("storage_per_liter_day", form, "Хранение, ₽ за литр в день")
        tiers = " · ".join(f"до {fmt_number(u)} л — {fmt_money(r)}" for u, r in t.small_tiers)
        small = _label(f"До 1 литра: {tiers} ₽", "caption")
        small.setWordWrap(True)
        form.addRow(small)
        return box

    # --- состояние ----------------------------------------------------------------------

    def set_input(self, inp: WbInput) -> None:
        t = self.tariffs
        values = {
            "length_cm": inp.length_cm, "width_cm": inp.width_cm, "height_cm": inp.height_cm,
            "retail_price": inp.retail_price, "target_value": inp.target_value,
            "seller_discount_pct": inp.seller_discount_pct,
            "commission_fbo_pct": inp.commission_fbo_pct,
            "commission_fbs_pct": inp.commission_fbs_pct, "buyout_pct": inp.buyout_pct,
            "warehouse_coef_pct": inp.warehouse_coef_pct or t.warehouse_coef_pct,
            "localization_index": inp.localization_index,
            "storage_days": Decimal(inp.storage_days), "acceptance_coef": inp.acceptance_coef,
            "cost_price": inp.cost_price, "acquiring_pct": inp.acquiring_pct or t.acquiring_pct,
            "ads_pct": inp.ads_pct, "other_per_unit": inp.other_per_unit,
            "tax_rate_pct": inp.tax_rate_pct,
            "first_liter": inp.first_liter or t.first_liter,
            "extra_liter": inp.extra_liter or t.extra_liter,
            "storage_per_liter_day": inp.storage_per_liter_day or t.storage_per_liter_day,
        }  # fmt: skip
        for w in (*self._fields.values(), self.name_edit, self.target_combo, self.scheme_combo,
                  self.tax_combo):  # fmt: skip
            w.blockSignals(True)
        self.name_edit.setText(inp.name)
        for key, value in values.items():
            self._fields[key].set_value(value)
        self.target_combo.set_value(inp.target)
        self.scheme_combo.set_value(inp.scheme)
        self.tax_combo.set_value(inp.tax_mode)
        for w in (*self._fields.values(), self.name_edit, self.target_combo, self.scheme_combo,
                  self.tax_combo):  # fmt: skip
            w.blockSignals(False)
        self._apply_target_ui()
        self.recalc()

    def read_input(self) -> tuple[WbInput | None, list[str]]:
        errors: list[str] = []
        values: dict[str, Decimal] = {}
        target = self.target_combo.value()
        for key, edit in self._fields.items():
            if key == "retail_price" and target is not Target.PRICE:
                continue
            if key == "target_value" and target is Target.PRICE:
                continue
            try:
                values[key] = edit.value()
                mark_error(edit, None)
            except ValueError as exc:
                label = key
                errors.append(f"{label}: {exc}")
                mark_error(edit, str(exc))
        if errors:
            return None, ["Проверьте числа в подсвеченных полях"]
        days = values["storage_days"]
        if days != days.to_integral_value():
            return None, ["Дней хранения — целое число"]
        inp = WbInput(
            name=self.name_edit.text().strip() or "Товар",
            length_cm=values["length_cm"], width_cm=values["width_cm"],
            height_cm=values["height_cm"], cost_price=values["cost_price"],
            retail_price=values.get("retail_price", Decimal(0)),
            seller_discount_pct=values["seller_discount_pct"],
            scheme=self.scheme_combo.value(),
            commission_fbo_pct=values["commission_fbo_pct"],
            commission_fbs_pct=values["commission_fbs_pct"],
            buyout_pct=values["buyout_pct"],
            warehouse_coef_pct=values["warehouse_coef_pct"],
            localization_index=values["localization_index"],
            storage_days=int(days), acceptance_coef=values["acceptance_coef"],
            acquiring_pct=values["acquiring_pct"], ads_pct=values["ads_pct"],
            other_per_unit=values["other_per_unit"],
            tax_mode=self.tax_combo.value(), tax_rate_pct=values["tax_rate_pct"],
            target=target, target_value=values.get("target_value", Decimal(0)),
            first_liter=values["first_liter"], extra_liter=values["extra_liter"],
            storage_per_liter_day=values["storage_per_liter_day"],
        )  # fmt: skip
        return inp, []

    # --- реакции ------------------------------------------------------------------------

    def _changed(self, *_args) -> None:
        if not self._loading:
            self.modified.emit()
        self._timer.start()

    # --- файл проекта -------------------------------------------------------------------

    def save_state(self) -> dict:
        """Ввод как есть (строки полей) — сохраняется и с ошибками, как набрал пользователь."""
        return {
            "name": self.name_edit.text(),
            "target": self.target_combo.value().value,
            "scheme": self.scheme_combo.value().value,
            "tax_mode": self.tax_combo.value().value,
            "fields": {key: edit.text() for key, edit in self._fields.items()},
        }

    def load_state(self, state: dict | None) -> None:
        """Состояние из файла проекта; None — значения по умолчанию (новый расчёт)."""
        self._loading = True
        try:
            self.set_input(WbInput())
            if not state:
                return
            combos = ((self.target_combo, Target, "target"), (self.scheme_combo, Scheme, "scheme"),
                      (self.tax_combo, TaxMode, "tax_mode"))  # fmt: skip
            widgets = [*self._fields.values(), self.name_edit, *(c for c, _, _ in combos)]
            for w in widgets:
                w.blockSignals(True)
            self.name_edit.setText(str(state.get("name", "")))
            for combo, enum, key in combos:
                if state.get(key) in enum._value2member_map_:
                    combo.set_value(enum(state[key]))
            for key, text in (state.get("fields") or {}).items():
                if key in self._fields:
                    self._fields[key].setText(str(text))
            for w in widgets:
                w.blockSignals(False)
            self._apply_target_ui()
            self.recalc()
        finally:
            self._loading = False

    def _apply_target_ui(self) -> None:
        target = self.target_combo.value()
        self.retail_edit.setEnabled(target is Target.PRICE)
        self.target_edit.setEnabled(target is not Target.PRICE)
        self.target_label.setText(
            {Target.PRICE: "Цель", Target.MARGIN: "Маржа, %", Target.PROFIT: "Прибыль, ₽"}[target]
        )
        self._changed()

    def _on_tax_mode(self) -> None:
        self._fields["tax_rate_pct"].set_value(DEFAULT_TAX[self.tax_combo.value()])

    def recalc(self) -> WbResult | None:
        self._timer.stop()
        inp, errors = self.read_input()
        if inp is not None:
            self.liters_label.setText(f"{fmt_number(liters_of(inp))} л")
            try:
                self.result = solve(inp, self.tariffs)
                self.schemes = compare_schemes(inp, self.tariffs)
            except WbInputError as exc:
                errors = str(exc).split("; ")
        if errors:
            self.result = None
            self.status.setStyleSheet(ERROR_BOX)
            self.status.setText("<b>Проверьте ввод:</b><br>" + "<br>".join(map(escape, errors)))
            self.status.show()
            self.result_box.hide()
            self.compare_box.hide()
            return None
        self._show(self.result)
        return self.result

    def _show(self, r: WbResult) -> None:
        if r.inp.target is not Target.PRICE:
            self.retail_edit.blockSignals(True)
            self.retail_edit.set_value(r.retail_price)
            self.retail_edit.blockSignals(False)
        self.status.setVisible(bool(r.warnings))
        if r.warnings:
            self.status.setStyleSheet(WARN_BOX)
            self.status.setText("<br>".join(map(escape, r.warnings)))
        self.result_box.show()
        self.title.setText(f"{r.inp.name} · {r.inp.scheme.label} · {fmt_number(r.liters)} л")
        self.price_value.setText(fmt_money(r.price))
        self.price_sub.setText(f"до скидки {fmt_money(r.retail_price)}")
        self.payout_value.setText(fmt_money(r.payout))
        self.payout_sub.setText(f"расходы WB {fmt_money(r.wb_expenses)}")
        self.profit_value.setText(fmt_money(r.profit))
        self.profit_value.setStyleSheet(f"color:{POSITIVE if r.profit > 0 else NEGATIVE};")
        self.profit_sub.setText(f"маржа {fmt_pct(r.margin)} · ROI {fmt_pct(r.roi)}")

        grid = self.lines_grid
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for n, line in enumerate(r.lines):
            muted = "muted" if line.informational else ""
            label = _label(line.label, muted, bold=line.strong)
            formula = _label(line.formula, "caption")
            formula.setWordWrap(True)
            value = _label(line.text, muted, right=True, bold=line.strong)
            value.setObjectName(f"wb:{line.label}")
            if line.informational:
                value.setStyleSheet(f"color:{TEXT_MUTED};")
            if line.label == "Чистая прибыль":
                value.setStyleSheet(f"color:{POSITIVE if line.value > 0 else NEGATIVE};")
            grid.addWidget(label, n, 0)
            grid.addWidget(formula, n, 1)
            grid.addWidget(value, n, 2)

        grid = self.compare_grid
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        heads = ["", "Цена продажи", "К перечислению", "Прибыль", "Маржа"]
        for c, h in enumerate(heads):
            grid.addWidget(_label(h, "caption", right=c > 0), 0, c)
        best = max(self.schemes.values(), key=lambda x: x.profit)
        for n, (scheme, res) in enumerate(self.schemes.items(), 1):
            bold = res is best
            cells = [scheme.label, fmt_money(res.price), fmt_money(res.payout),
                     fmt_money(res.profit), fmt_pct(res.margin)]  # fmt: skip
            for c, text in enumerate(cells):
                lbl = _label(text, right=c > 0, bold=bold)
                lbl.setObjectName(f"cmp:{scheme.value}:{c}")
                grid.addWidget(lbl, n, c)
        self.compare_box.show()

    def line_text(self, label: str) -> str | None:
        w = self.findChild(QLabel, f"wb:{label}")
        return w.text() if w else None
