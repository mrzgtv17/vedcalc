"""Вкладка «Цена продажи»: себестоимость (из импорта или вручную), цена, стоимость денег."""

from __future__ import annotations

import copy
from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.basis import basis_from_cost, basis_from_manual
from vedcalc.core.explain import fmt_money
from vedcalc.core.models import (
    Financing,
    FinancingMode,
    ManualCost,
    PricingSource,
    SaleParams,
    TaxRegime,
)
from vedcalc.core.money import HUNDRED, ZERO
from vedcalc.core.report import PriceReport, build_price_report
from vedcalc.core.settings import load_settings
from vedcalc.core.units import Unit
from vedcalc.core.validation import Issue
from vedcalc.gui.form import PricingState
from vedcalc.gui.results import PriceResultsPanel
from vedcalc.gui.widgets import DecimalEdit, EnumCombo, VatCombo, mark_error

RECALC_DELAY_MS = 150
REGIMES = (TaxRegime.OSN, TaxRegime.USN_INCOME_MINUS_EXPENSES)

# Поля графика платежей: атрибут Financing, подпись, подсказка
SCHEDULE_FIELDS = (
    ("supplier_prepay_pct", "Предоплата поставщику, %", "Платится в день 0"),
    ("supplier_balance_day", "Остаток поставщику, день", "День оплаты остатка за товар"),
    ("transit_days", "Перевозка, дней", "От оплаты остатка до прибытия; логистика — по прибытии"),
    ("customs_days", "Растаможка, дней", "После прибытия; тогда платятся пошлина, НДС, сбор"),
    ("delivery_days", "Доставка покупателю, дней", "От выпуска до поставки покупателю"),
    ("buyer_prepay_pct", "Предоплата покупателя, %", "Доля цены с НДС"),
    ("buyer_prepay_day", "Предоплата покупателя, день", "День получения предоплаты"),
    ("buyer_deferral_days", "Отсрочка покупателю, дней", "Остаток — через N дней после поставки"),
)  # fmt: skip
PERCENT_FIELDS = {"supplier_prepay_pct", "buyer_prepay_pct"}


def _form_box(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, form


class PricingTab(QWidget):
    modified = Signal()
    reportChanged = Signal()
    offerRequested = Signal()

    def __init__(self, import_tab, parent: QWidget | None = None):
        super().__init__(parent)
        self.import_tab = import_tab
        self.settings = load_settings()
        self.report: PriceReport | None = None
        self._loading = False
        self._widgets: dict[str, QWidget] = {}

        form_page = QWidget()
        v = QVBoxLayout(form_page)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self._build_source_box())
        v.addWidget(self._build_sale_box())
        v.addWidget(self._build_financing_box())
        self.offer_button = QPushButton("Коммерческое предложение…")
        self.offer_button.setObjectName("primary")
        self.offer_button.setToolTip(
            "КП для клиента в PDF или DOCX: цены и условия, без себестоимости и наценки"
        )
        self.offer_button.setEnabled(False)
        self.offer_button.clicked.connect(self.offerRequested)
        v.addWidget(self.offer_button)
        v.addStretch(1)
        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.Shape.NoFrame)
        left.setWidget(form_page)
        left.setMinimumWidth(440)

        self.results = PriceResultsPanel()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([560, 740])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RECALC_DELAY_MS)
        self._timer.timeout.connect(self.recalc)
        self.results.targetChanged.connect(self._on_changed)
        import_tab.reportChanged.connect(self._on_import_changed)
        import_tab.form.regimeChanged.connect(self._on_import_regime_changed)

    # --- построение формы ---------------------------------------------------------------

    def _watch(self, *widgets: QWidget) -> None:
        for w in widgets:
            if isinstance(w, QLineEdit):
                w.textChanged.connect(self._on_changed)
            elif isinstance(w, EnumCombo):
                w.currentIndexChanged.connect(self._on_changed)

    def _build_source_box(self) -> QGroupBox:
        box = QGroupBox("Себестоимость")
        v = QVBoxLayout(box)
        self.source_combo = EnumCombo([(s, s.label) for s in PricingSource])
        v.addWidget(self.source_combo)
        self.import_info = QLabel()
        self.import_info.setObjectName("muted")
        self.import_info.setWordWrap(True)
        v.addWidget(self.import_info)

        self.manual_box = QWidget()
        form = QFormLayout(self.manual_box)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.m_name = QLineEdit()
        self.m_qty = DecimalEdit()
        self.m_unit = EnumCombo([(u, u.label) for u in Unit])
        self.m_cost = DecimalEdit()
        self.m_cost.setToolTip("Себестоимость единицы без НДС (при УСН — с НДС)")
        self.m_vat = DecimalEdit(optional=True, placeholder="0")
        self.m_vat.setToolTip("Входящий НДС на единицу, который принимается к вычету")
        self.m_regime = EnumCombo([(r, r.label) for r in REGIMES])
        form.addRow("Товар", self.m_name)
        qty_row = QHBoxLayout()
        qty_row.addWidget(self.m_qty, 1)
        qty_row.addWidget(self.m_unit)
        form.addRow("Количество", qty_row)
        form.addRow("Себестоимость единицы, ₽", self.m_cost)
        form.addRow("НДС к вычету на единицу, ₽", self.m_vat)
        form.addRow("Налоговый режим", self.m_regime)
        v.addWidget(self.manual_box)
        self._widgets |= {"manual.quantity": self.m_qty, "manual.unit_cost_net": self.m_cost,
                          "manual.unit_deductible_vat": self.m_vat}  # fmt: skip
        self.source_combo.currentIndexChanged.connect(self._apply_source_ui)
        self.m_regime.currentIndexChanged.connect(self._on_manual_regime_changed)
        self._watch(
            self.source_combo, self.m_name, self.m_qty, self.m_unit, self.m_cost, self.m_vat
        )
        return box

    def _build_sale_box(self) -> QGroupBox:
        box, form = _form_box("Параметры продажи")
        self.sale_vat = VatCombo(self.settings.allowed_vat_rates)
        self.profit_tax = DecimalEdit()
        self.price_dependent = DecimalEdit()
        self.price_dependent.setToolTip(
            "Комиссия, эквайринг, бонус клиенту — доля от выручки без НДС (r)"
        )
        self.profit_tax_label = QLabel("Налог на прибыль, %")
        form.addRow("НДС при продаже", self.sale_vat)
        form.addRow(self.profit_tax_label, self.profit_tax)
        form.addRow("Расходы от выручки, %", self.price_dependent)
        self._widgets |= {"sale.profit_tax_rate": self.profit_tax,
                          "sale.price_dependent_rate": self.price_dependent}  # fmt: skip
        self._watch(self.sale_vat, self.profit_tax, self.price_dependent)
        return box

    def _build_financing_box(self) -> QGroupBox:
        box = QGroupBox("Стоимость денег")
        v = QVBoxLayout(box)
        hint = QLabel(
            "Проценты за деньги, вложенные в партию до оплаты покупателем. "
            "Уменьшают прибыль, учитываются в цене."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.fin_mode = EnumCombo([(m, m.label) for m in FinancingMode])
        v.addWidget(self.fin_mode)

        self.fin_common = QWidget()
        common = QFormLayout(self.fin_common)
        common.setContentsMargins(0, 0, 0, 0)
        self.fin_rate = DecimalEdit()
        self.fin_share = DecimalEdit()
        self.fin_share.setToolTip(
            "Какая часть вложений — заёмные деньги. 100% — все деньги стоят как кредит "
            "(упущенная выгода)"
        )
        common.addRow("Ставка, % годовых", self.fin_rate)
        common.addRow("Доля заёмных средств, %", self.fin_share)
        v.addWidget(self.fin_common)

        self.fin_simple = QWidget()
        simple = QFormLayout(self.fin_simple)
        simple.setContentsMargins(0, 0, 0, 0)
        self.fin_amount = DecimalEdit(optional=True, placeholder="себестоимость партии с НДС")
        self.fin_days = DecimalEdit()
        simple.addRow("Сумма займа, ₽", self.fin_amount)
        simple.addRow("Срок займа, дней", self.fin_days)
        v.addWidget(self.fin_simple)

        self.fin_schedule = QWidget()
        sched = QFormLayout(self.fin_schedule)
        sched.setContentsMargins(0, 0, 0, 0)
        self.schedule_edits: dict[str, DecimalEdit] = {}
        for attr, label, tip in SCHEDULE_FIELDS:
            edit = DecimalEdit()
            edit.setToolTip(tip)
            lbl = QLabel(label)
            lbl.setToolTip(tip)
            sched.addRow(lbl, edit)
            self.schedule_edits[attr] = edit
            self._widgets[f"financing.{attr}"] = edit
        v.addWidget(self.fin_schedule)
        self._widgets |= {"financing.annual_rate": self.fin_rate,
                          "financing.borrowed_share": self.fin_share,
                          "financing.loan_amount": self.fin_amount,
                          "financing.loan_days": self.fin_days}  # fmt: skip
        self.fin_mode.currentIndexChanged.connect(self._apply_financing_ui)
        self._watch(self.fin_mode, self.fin_rate, self.fin_share, self.fin_amount, self.fin_days,
                    *self.schedule_edits.values())  # fmt: skip
        return box

    # --- состояние ----------------------------------------------------------------------

    def set_state(self, state: PricingState) -> None:
        self._loading = True
        try:
            sale, fin, m = state.sale, state.financing, state.manual_cost
            self.source_combo.set_value(state.source)
            self.m_name.setText(m.name)
            self.m_qty.set_value(m.quantity)
            self.m_unit.set_value(m.unit)
            self.m_cost.set_value(m.unit_cost_net)
            self.m_vat.set_value(m.unit_deductible_vat or None)
            self.m_regime.set_value(m.tax_regime)
            self.sale_vat.set_value(sale.vat_rate)
            self.profit_tax.set_value(sale.profit_tax_rate)
            self.price_dependent.set_value(sale.price_dependent_rate)
            self.results.set_target(sale.target_mode, sale.target_value)
            self.fin_mode.set_value(fin.mode)
            self.fin_rate.set_value(fin.annual_rate)
            self.fin_share.set_value(fin.borrowed_share)
            self.fin_amount.set_value(fin.loan_amount)
            self.fin_days.set_value(Decimal(fin.loan_days))
            for attr, edit in self.schedule_edits.items():
                edit.set_value(Decimal(getattr(fin, attr)))
        finally:
            self._loading = False
        self._apply_source_ui()
        self._apply_financing_ui()
        self.recalc()

    def read_state(self) -> tuple[PricingState | None, list[Issue]]:
        issues: list[Issue] = []

        def num(widget: DecimalEdit, path: str, label: str, *, integer=False, below_100=False,
                at_most_100=False) -> Decimal | None:  # fmt: skip
            try:
                value = widget.value()
            except ValueError as exc:
                issues.append(Issue(path, f"{label}: {exc}"))
                return None
            if value is None:
                return None
            if value < 0:
                issues.append(Issue(path, f"{label}: не может быть отрицательным"))
            elif integer and value != value.to_integral_value():
                issues.append(Issue(path, f"{label}: укажите целое число дней"))
            elif below_100 and value >= HUNDRED:
                issues.append(Issue(path, f"{label}: должно быть меньше 100%"))
            elif at_most_100 and value > HUNDRED:
                issues.append(Issue(path, f"{label}: не может быть больше 100%"))
            return value

        target = self.results.target()
        if target is None:
            issues.append(Issue("target", "Сценарий цены: введите число"))
            target = (self.results.mode_combo.value(), ZERO)
        regime = self._regime()
        sale = SaleParams(
            vat_rate=self.sale_vat.value() if regime.vat_payer else ZERO,
            profit_tax_rate=num(self.profit_tax, "sale.profit_tax_rate", "Налог", below_100=True)
            or ZERO,
            price_dependent_rate=num(
                self.price_dependent, "sale.price_dependent_rate", "Расходы от выручки",
                below_100=True,
            ) or ZERO,
            target_mode=target[0],
            target_value=target[1],
        )  # fmt: skip
        fin = Financing(mode=self.fin_mode.value())
        fin.annual_rate = num(self.fin_rate, "financing.annual_rate", "Ставка") or ZERO
        fin.borrowed_share = (
            num(self.fin_share, "financing.borrowed_share", "Доля заёмных", at_most_100=True)
            or ZERO
        )
        fin.loan_amount = num(self.fin_amount, "financing.loan_amount", "Сумма займа")
        days = num(self.fin_days, "financing.loan_days", "Срок займа", integer=True)
        fin.loan_days = int(days or 0)
        for attr, label, _ in SCHEDULE_FIELDS:
            pct_field = attr in PERCENT_FIELDS
            value = num(self.schedule_edits[attr], f"financing.{attr}", label,
                        integer=not pct_field, at_most_100=pct_field)  # fmt: skip
            setattr(fin, attr, (value or ZERO) if pct_field else int(value or 0))
        manual = ManualCost(
            name=self.m_name.text().strip() or "Товар",
            quantity=num(self.m_qty, "manual.quantity", "Количество") or ZERO,
            unit=self.m_unit.value(),
            unit_cost_net=num(self.m_cost, "manual.unit_cost_net", "Себестоимость") or ZERO,
            unit_deductible_vat=num(self.m_vat, "manual.unit_deductible_vat", "НДС к вычету")
            or ZERO,
            tax_regime=self.m_regime.value(),
        )
        source = self.source_combo.value()
        if source is PricingSource.MANUAL and manual.quantity <= 0:
            issues.append(Issue("manual.quantity", "Количество должно быть больше нуля"))
        self._mark(issues)
        if issues:
            return None, issues
        return PricingState(sale, fin, source, manual), []

    def _mark(self, issues: list[Issue]) -> None:
        bad = {i.path: i.message for i in issues}
        for path, w in self._widgets.items():
            mark_error(w, bad.get(path))

    # --- реакции ------------------------------------------------------------------------

    def _regime(self) -> TaxRegime:
        if self.source_combo.value() is PricingSource.MANUAL:
            return self.m_regime.value()
        return self.import_tab.form.regime_combo.value()

    def _apply_regime_ui(self) -> None:
        osn = self._regime().vat_payer
        self.sale_vat.setEnabled(osn)
        self.profit_tax_label.setText("Налог на прибыль, %" if osn else "Ставка УСН, %")

    def _apply_regime_defaults(self) -> None:
        defaults = SaleParams.for_regime(self._regime())
        self._loading = True
        try:
            self.sale_vat.set_value(defaults.vat_rate)
            self.profit_tax.set_value(defaults.profit_tax_rate)
        finally:
            self._loading = False
        self._apply_regime_ui()
        self._on_changed()

    def _on_import_regime_changed(self) -> None:
        if self.source_combo.value() is PricingSource.IMPORT:
            self._apply_regime_defaults()

    def _on_manual_regime_changed(self) -> None:
        if not self._loading:
            self._apply_regime_defaults()

    def _apply_source_ui(self) -> None:
        manual = self.source_combo.value() is PricingSource.MANUAL
        self.manual_box.setVisible(manual)
        self.import_info.setVisible(not manual)
        self._apply_regime_ui()
        self._update_import_info()

    def _apply_financing_ui(self) -> None:
        mode = self.fin_mode.value()
        self.fin_common.setVisible(mode is not FinancingMode.OFF)
        self.fin_simple.setVisible(mode is FinancingMode.SIMPLE)
        self.fin_schedule.setVisible(mode is FinancingMode.SCHEDULE)

    def _update_import_info(self) -> None:
        cost_report = self.import_tab.report
        if cost_report is None:
            self.import_info.setText("Расчёт импорта ещё не готов — заполните вкладку «Импорт».")
            return
        c = cost_report.cost
        units = ", ".join(
            f"{escape(v.name)} — {fmt_money(v.cost_net)} {v.per}" for v in cost_report.unit_views()
        )
        self.import_info.setText(
            f"«{escape(c.shipment_name)}»: себестоимость партии {fmt_money(c.cost_net)} ₽ "
            f"({escape(c.tax_regime.label)}). {units}."
        )

    def _on_import_changed(self) -> None:
        self._update_import_info()
        self._apply_regime_ui()
        if self.source_combo.value() is PricingSource.IMPORT:
            self._timer.start()

    def _on_changed(self, *_args) -> None:
        if self._loading:
            return
        self.modified.emit()
        self._timer.start()

    def recalc(self) -> PriceReport | None:
        self._timer.stop()
        try:
            return self._recalc()
        except Exception as exc:  # pragma: no cover — защитная сетка
            self.report = None
            self.results.show_message(
                f"<b>Не удалось рассчитать цену.</b><br>{escape(type(exc).__name__)}: "
                f"{escape(str(exc))}",
                error=True,
            )
            return None
        finally:
            self.offer_button.setEnabled(self.offer_ready)
            self.reportChanged.emit()

    @property
    def offer_ready(self) -> bool:
        return self.report is not None and self.report.target is not None

    def _recalc(self) -> PriceReport | None:
        state, issues = self.read_state()
        if state is None:
            self.report = None
            self.results.show_issues(issues)
            return None
        # Сохраняем состояние в форму импорта — оно уйдёт в файл расчёта
        self.import_tab.form.pricing = copy.deepcopy(state)
        cost_report = None
        if state.source is PricingSource.IMPORT:
            cost_report = self.import_tab.report
            if cost_report is None:
                self.report = None
                self.results.show_message(
                    "Себестоимость ещё не рассчитана: заполните вкладку «Импорт» "
                    "или выберите «Ввести вручную»."
                )
                return None
            basis = basis_from_cost(cost_report.cost, state.sale)
            title = cost_report.shipment.name
        else:
            basis = basis_from_manual(state.manual_cost, state.sale)
            title = state.manual_cost.name
        self.report = build_price_report(basis, state.financing, title, cost_report)
        self.results.show_report(self.report)
        self.results.whatif_view.set_report(self.report, self.import_tab.fee_scale)
        return self.report
