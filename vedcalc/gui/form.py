"""Форма ввода: партия, позиции, расходы, курсы, параметры продажи."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import ClassVar

import shiboken6
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.models import (
    AllocationMethod,
    CostKind,
    CostLine,
    DeliveryMode,
    DutyRate,
    DutyUnit,
    Financing,
    Item,
    ManualCost,
    PricingSource,
    SaleParams,
    Shipment,
    TaxRegime,
    default_cost_lines,
)
from vedcalc.core.money import ZERO, Money
from vedcalc.core.settings import load_settings
from vedcalc.core.tnved import TnvedEntry
from vedcalc.core.units import Unit
from vedcalc.core.validation import Issue
from vedcalc.gui.packaging import PackagingButton
from vedcalc.gui.tnved import TnvedEdit
from vedcalc.gui.widgets import (
    CenteredCheck,
    CurrencyCombo,
    DecimalEdit,
    EnumCombo,
    VatCombo,
    mark_error,
)
from vedcalc.reports.style import WARN

COST_PRESETS: list[tuple[str, CostKind, bool, Decimal]] = [
    # (название, вид, включается в ТС, ставка НДС по умолчанию)
    ("Услуги таможенного брокера", CostKind.BROKER, False, Decimal(22)),
    ("СВХ", CostKind.WAREHOUSE, False, Decimal(22)),
    ("Сертификация / декларация соответствия", CostKind.CERTIFICATION, False, Decimal(22)),
    ("Банковская комиссия и валютный контроль", CostKind.BANK, False, ZERO),
    ("Агентское вознаграждение", CostKind.AGENT, False, Decimal(22)),
    ("Доставка со СВХ до склада (отдельный договор)", CostKind.DELIVERY_RF, False, Decimal(22)),
    ("Прочий расход", CostKind.OTHER, False, ZERO),
]


@dataclass(slots=True)
class Column:
    key: str
    header: str
    width: int
    tooltip: str = ""


def _connect(widget: QWidget, slot: Callable[[], None]) -> None:
    """Подписывает slot на любое изменение значения виджета (аргументы сигнала отбрасываются)."""

    def handler(*_args) -> None:
        slot()

    if isinstance(widget, QLineEdit):
        widget.textChanged.connect(handler)
    elif isinstance(widget, CurrencyCombo):
        widget.currentTextChanged.connect(handler)
    elif isinstance(widget, EnumCombo):
        widget.currentIndexChanged.connect(handler)
    elif isinstance(widget, CenteredCheck | QCheckBox):
        widget.toggled.connect(handler)
    elif isinstance(widget, QDateEdit):
        widget.dateChanged.connect(handler)


def _grow_fields(form: QFormLayout) -> None:
    """На macOS поля формы по умолчанию не растягиваются — длинный текст обрезается."""
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


class RowTable(QTableWidget):
    """Таблица, в каждой ячейке которой — виджет ввода."""

    changed = Signal()

    DELETE_KEY = "_delete"

    def __init__(self, columns: list[Column], parent: QWidget | None = None):
        # Первая колонка — кнопка «✕»: удаляет именно свою строку. Общая кнопка «Удалить»
        # удаляла строку, где последний раз стоял курсор, — часто не ту, что хотели.
        columns = [Column(self.DELETE_KEY, "", 30, "Удалить строку"), *columns]
        super().__init__(0, len(columns), parent)
        self.columns = columns
        self.rows: list[dict] = []  # ключ колонки → виджет; «meta» — служебные данные
        self.setHorizontalHeaderLabels([c.header for c in columns])
        for n, c in enumerate(columns):
            self.setColumnWidth(n, c.width)
            if c.tooltip:
                self.horizontalHeaderItem(n).setToolTip(c.tooltip)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setDefaultSectionSize(30)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumHeight(110)

    def append(self, widgets: dict) -> None:
        delete = QToolButton()
        delete.setObjectName("rowDelete")
        delete.setText("✕")
        delete.setToolTip("Удалить строку")
        delete.clicked.connect(lambda: self.remove_row(widgets))
        widgets[self.DELETE_KEY] = delete
        r = self.rowCount()
        self.insertRow(r)
        for n, c in enumerate(self.columns):
            w = widgets[c.key]
            self.setCellWidget(r, n, w)
            if c.key != self.DELETE_KEY:
                _connect(w, self.changed.emit)  # прочие ключи строки (meta) — не виджеты
        self.rows.append(widgets)
        self._fit_height()
        self.changed.emit()

    def remove_row(self, widgets: dict) -> None:
        """Удаляет строку по её виджетам (а не по «текущей» ячейке таблицы)."""
        r = next((i for i, row in enumerate(self.rows) if row is widgets), None)
        if r is None:
            return
        self.removeRow(r)
        del self.rows[r]
        for key, w in widgets.items():
            if isinstance(w, QWidget) and key != "meta":
                w.deleteLater()
        self._fit_height()
        self.changed.emit()

    def clear_rows(self) -> None:
        self.setRowCount(0)
        self.rows.clear()

    def _fit_height(self) -> None:
        # + полоса прокрутки: широкие таблицы прокручиваются вбок и иначе прячут строку
        scroll = self.horizontalScrollBar().sizeHint().height()
        h = self.horizontalHeader().height() + 30 * max(len(self.rows), 2) + 6 + scroll
        self.setFixedHeight(min(max(h, 110), 320))


class ShipmentForm(QWidget):
    """Собирает Shipment из полей. changed — любое изменение ввода."""

    changed = Signal()
    regimeChanged = Signal()

    ITEM_COLUMNS: ClassVar[list[Column]] = [
        Column("name", "Наименование", 180),
        Column(
            "tnved",
            "Код ТН ВЭД",
            125,
            "10 цифр — пошлина подставится из ЕТТ. Лупа — подбор кода по названию.",
        ),
        Column("price", "Цена за ед.", 95),
        Column("currency", "Валюта", 70),
        Column("quantity", "Кол-во", 75),
        Column("unit", "Ед.", 60),
        Column("duty", "Пошлина, %", 80, "Ставка ввозной пошлины, % от ТС (или из ЕТТ по коду)"),
        Column(
            "duty_qty",
            "Кол-во в ед. ставки",
            110,
            "Для специфической ставки за л, пары, м², см³ — количество в этой единице",
        ),
        Column("vat", "НДС, %", 70, "Ставка НДС при ввозе"),
        Column("excise", "Акциз, ₽", 80, "Заглушка: сумма акциза в рублях, обычно 0"),
        Column(
            "gross",
            "Брутто, кг",
            85,
            "Вес брутто всей позиции. Нужен для распределения расходов по весу "
            "и подсказки о загрузке контейнера",
        ),
        Column("net", "Нетто, кг", 80, "Вес нетто всей позиции"),
        Column(
            "volume", "Объём, м³", 80, "Объём всей позиции — для подсказки о загрузке контейнера"
        ),
        Column(
            "pack",
            "Упаковка",
            100,
            "Грузовое место: размеры, вес и сколько товара в месте — для укладки в контейнер",
        ),
    ]
    COST_COLUMNS: ClassVar[list[Column]] = [
        Column("name", "Расход", 240),
        Column("amount", "Сумма", 105),
        Column("currency", "Валюта", 70),
        Column("in_cv", "В ТС", 50, "Включается в таможенную стоимость (расходы до границы ЕАЭС)"),
        Column("vat_included", "С НДС", 55, "Сумма указана с НДС (НДС будет выделен)"),
        Column("vat", "НДС, %", 70, "Входящий НДС по российским услугам — к вычету при ОСН"),
    ]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = load_settings()
        self._loading = False
        # Параметры цены продажи живут на вкладке «Цена продажи», но сохраняются
        # в том же файле расчёта — форма импорта лишь переносит их как есть.
        self.pricing = PricingState()
        self._field_widgets: dict[str, QWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_shipment_box())
        layout.addWidget(self._build_items_box())
        layout.addWidget(self._build_costs_box())
        layout.addWidget(self._build_rates_box())
        layout.addStretch(1)

    # --- построение -----------------------------------------------------------------

    def _build_shipment_box(self) -> QGroupBox:
        box = QGroupBox("Партия")
        form = QFormLayout(box)
        _grow_fields(form)
        self.name_edit = QLineEdit()
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        self.date_edit.setToolTip("Дата расчёта — на неё берутся курсы ЦБ РФ")
        self.delivery_combo = EnumCombo([(m, m.label) for m in DeliveryMode])
        self.delivery_combo.setToolTip("Для контейнеров — загрузка и укладка грузовых мест")
        self.regime_combo = EnumCombo([(r, r.label) for r in TaxRegime])
        self.allocation_combo = EnumCombo([(a, a.label.capitalize()) for a in AllocationMethod])
        form.addRow("Название / номер расчёта", self.name_edit)
        form.addRow("Дата расчёта", self.date_edit)
        form.addRow("Способ доставки", self.delivery_combo)
        form.addRow("Налоговый режим импортёра", self.regime_combo)
        form.addRow("Распределение расходов", self.allocation_combo)
        for w in (self.name_edit, self.date_edit, self.delivery_combo, self.allocation_combo):
            _connect(w, self._emit_changed)
        self.regime_combo.currentIndexChanged.connect(self._on_regime_changed)
        self._field_widgets["tax_regime"] = self.regime_combo
        return box

    def _build_items_box(self) -> QGroupBox:
        box = QGroupBox("Товары")
        v = QVBoxLayout(box)
        self.items_table = RowTable(self.ITEM_COLUMNS)
        self.items_table.changed.connect(self._emit_changed)
        v.addWidget(self.items_table)
        buttons = QHBoxLayout()
        add = QPushButton("+ Позиция")
        add.clicked.connect(lambda: self.add_item())
        buttons.addWidget(add)
        buttons.addStretch(1)
        v.addLayout(buttons)
        self._field_widgets["items"] = self.items_table
        return box

    def _build_costs_box(self) -> QGroupBox:
        box = QGroupBox("Расходы партии")
        v = QVBoxLayout(box)
        self.costs_table = RowTable(self.COST_COLUMNS)
        self.costs_table.changed.connect(self._emit_changed)
        v.addWidget(self.costs_table)
        buttons = QHBoxLayout()
        add = QToolButton()
        add.setText("+ Расход")
        add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(add)
        for name, kind, in_cv, vat in COST_PRESETS:
            menu.addAction(
                name,
                lambda n=name, k=kind, c=in_cv, r=vat: self.add_cost(
                    CostLine(n, Money.rub(0), in_customs_value=c, vat_rate=r, kind=k)
                ),
            )
        add.setMenu(menu)
        hint = QLabel(
            "Доставка по РФ в рамках международной перевозки — НДС 0% (пп. 2.1 п. 1 ст. 164 НК РФ)"
        )
        hint.setStyleSheet("color: #6e6e73;")
        hint.setWordWrap(True)
        buttons.addWidget(add)
        buttons.addWidget(hint, 1)
        v.addLayout(buttons)
        return box

    def _build_rates_box(self) -> QGroupBox:
        box = QGroupBox("Курсы валют")
        v = QVBoxLayout(box)
        self.rates_info = QLabel("Курсы ЦБ РФ на дату расчёта загрузятся автоматически.")
        self.rates_info.setWordWrap(True)
        v.addWidget(self.rates_info)
        self.manual_check = QCheckBox("Задать курс вручную (сценарий «что если»)")
        self.manual_check.toggled.connect(self._on_manual_toggled)
        v.addWidget(self.manual_check)
        grid = QGridLayout()
        self.manual_edits: dict[str, DecimalEdit] = {}
        for n, code in enumerate(c for c in self.settings.currencies if c != "RUB"):
            edit = DecimalEdit(optional=True, placeholder="курс ЦБ")
            edit.setEnabled(False)
            edit.textChanged.connect(self._emit_changed)
            self.manual_edits[code] = edit
            self._field_widgets[f"manual_rates.{code}"] = edit
            grid.addWidget(QLabel(f"{code}, ₽ за 1"), n // 3, (n % 3) * 2)
            grid.addWidget(edit, n // 3, (n % 3) * 2 + 1)
        v.addLayout(grid)
        return box

    # --- строки таблиц -------------------------------------------------------------

    def add_item(self, item: Item | None = None) -> None:
        item = item or Item(
            "",
            Money.rub(0),
            Decimal(1),
            duty=DutyRate.ad_valorem(0),
            vat_rate=self.settings.import_vat_rate,
        )
        name = QLineEdit(item.name)
        name.setPlaceholderText("Наименование товара")
        currency = CurrencyCombo(self.settings.currencies)
        currency.set_value(item.unit_price.currency)
        unit = EnumCombo([(u, u.label) for u in Unit])
        unit.set_value(item.unit)
        vat = VatCombo(self.settings.allowed_vat_rates)
        vat.set_value(item.vat_rate)
        tnved = TnvedEdit(item.tnved_code or "")
        duty_qty = DecimalEdit(item.duty_quantity, optional=True)
        pack = PackagingButton(item.packaging)
        self.items_table.append(
            {
                "name": name,
                "tnved": tnved,
                "price": DecimalEdit(item.unit_price.amount),
                "currency": currency,
                "quantity": DecimalEdit(item.quantity),
                "unit": unit,
                "duty": DecimalEdit(item.duty.percent),
                "duty_qty": duty_qty,
                "vat": vat,
                "excise": DecimalEdit(item.excise.amount or None, optional=True, placeholder="0"),
                "gross": DecimalEdit(item.gross_weight_kg, optional=True),
                "net": DecimalEdit(item.net_weight_kg, optional=True),
                "volume": DecimalEdit(item.volume_m3, optional=True),
                "pack": pack,
                # Ставка из ЕТТ (или из файла расчёта) — со специфической частью и источником
                "meta": {"duty": item.duty if item.duty.source or item.duty.has_specific else None},
            }
        )
        row = self.items_table.rows[-1]
        tnved.entryChanged.connect(lambda entry, r=row: self._apply_tnved(r, entry))
        unit.currentIndexChanged.connect(lambda _=None, r=row: self._apply_duty_ui(r))
        pack.changed.connect(self.items_table.changed.emit)
        for w in (row["quantity"], unit):
            _connect(w, lambda r=row: self._update_pack(r))
        self._apply_duty_ui(row)
        self._update_pack(row)

    def _update_pack(self, row: dict) -> None:
        try:
            qty = row["quantity"].value()
        except ValueError:
            qty = None
        row["pack"].set_context(qty, row["unit"].value())

    def _apply_tnved(self, row: dict, entry: TnvedEntry | None) -> None:
        """Код введён: подставить ставку ЕТТ (ручные правки затираются только новым кодом)."""
        if entry is not None:
            rate = entry.duty
            row["meta"]["duty"] = rate
            row["duty"].set_value(rate.percent)
        elif row["meta"].get("duty") is not None and row["meta"]["duty"].source:
            # Код стёрт или не найден — ставка снова ручная (процент оставляем)
            row["meta"]["duty"] = None
        self._apply_duty_ui(row)

    def _apply_duty_ui(self, row: dict) -> None:
        rate: DutyRate | None = row["meta"].get("duty")
        specific = rate is not None and rate.has_specific
        duty_edit, qty_edit, tnved = row["duty"], row["duty_qty"], row["tnved"]
        duty_edit.setReadOnly(specific)
        duty_edit.setToolTip(
            f"Ставка ЕТТ: {rate.label}" if rate is not None else self.ITEM_COLUMNS[6].tooltip
        )
        needs_qty = specific and _needs_duty_quantity(rate, row["unit"].value())
        qty_edit.setEnabled(needs_qty)
        qty_edit.setPlaceholderText(rate.specific_unit.label if needs_qty else "")
        tnved.setStyleSheet(f"border:1px solid {WARN};" if tnved.unknown else "")

    def add_cost(self, line: CostLine) -> None:
        name = QLineEdit(line.name)
        currency = CurrencyCombo(self.settings.currencies)
        currency.set_value(line.amount.currency)
        vat = VatCombo(self.settings.allowed_vat_rates)
        vat.set_value(line.vat_rate)
        self.costs_table.append(
            {
                "name": name,
                "amount": DecimalEdit(line.amount.amount),
                "currency": currency,
                "in_cv": CenteredCheck(line.in_customs_value, self.COST_COLUMNS[3].tooltip),
                "vat_included": CenteredCheck(line.vat_included, self.COST_COLUMNS[4].tooltip),
                "vat": vat,
                "meta": {"kind": line.kind},
            }
        )

    # --- загрузка и чтение --------------------------------------------------------

    def set_shipment(self, s: Shipment) -> None:
        self._loading = True
        try:
            self.name_edit.setText(s.name)
            self.date_edit.setDate(QDate(s.calc_date.year, s.calc_date.month, s.calc_date.day))
            self.delivery_combo.set_value(s.delivery_mode)
            self.regime_combo.set_value(s.tax_regime)
            self.allocation_combo.set_value(s.allocation)
            self.items_table.clear_rows()
            for item in s.items:
                self.add_item(item)
            self.costs_table.clear_rows()
            for line in s.costs:
                self.add_cost(line)
            self.manual_check.setChecked(bool(s.manual_rates))
            for code, edit in self.manual_edits.items():
                edit.set_value(s.manual_rates.get(code))
            self.pricing = PricingState.from_shipment(s)
        finally:
            self._loading = False
        self.clear_marks()
        self.changed.emit()

    def read(self) -> tuple[Shipment | None, list[Issue]]:
        """Читает форму. Ошибки разбора чисел возвращаются как Issue с путём к полю."""
        issues: list[Issue] = []
        widgets: dict[str, QWidget] = dict(self._field_widgets)

        def num(widget: DecimalEdit, path: str, label: str) -> Decimal | None:
            widgets[path] = widget
            try:
                return widget.value()
            except ValueError as exc:
                issues.append(Issue(path, f"{label}: {exc}"))
                return None

        def code(widget: CurrencyCombo, path: str, label: str) -> str:
            widgets[path] = widget
            try:
                return widget.value()
            except ValueError as exc:
                issues.append(Issue(path, f"{label}: {exc}"))
                return "RUB"

        items: list[Item] = []
        for n, row in enumerate(self.items_table.rows):
            p, who = f"items[{n}]", f"Позиция {n + 1}"
            widgets[f"{p}.name"] = row["name"]
            widgets[f"{p}.tnved_code"] = row["tnved"]
            widgets[f"{p}.vat_rate"] = row["vat"]
            price = num(row["price"], f"{p}.unit_price", f"{who}, цена")
            currency = code(row["currency"], f"{p}.currency", f"{who}, валюта")
            qty = num(row["quantity"], f"{p}.quantity", f"{who}, количество")
            duty_pct = num(row["duty"], f"{p}.duty", f"{who}, пошлина")
            excise = num(row["excise"], f"{p}.excise", f"{who}, акциз")
            gross = num(row["gross"], f"{p}.gross_weight_kg", f"{who}, вес брутто")
            net = num(row["net"], f"{p}.net_weight_kg", f"{who}, вес нетто")
            volume = num(row["volume"], f"{p}.volume_m3", f"{who}, объём")
            duty_qty = num(row["duty_qty"], f"{p}.duty_quantity", f"{who}, кол-во в ед. ставки")
            widgets[f"{p}.duty_quantity"] = row["duty_qty"]
            rate: DutyRate | None = row["meta"].get("duty")
            percent = duty_pct if duty_pct is not None else ZERO
            if rate is not None and (rate.has_specific or rate.percent == percent):
                duty = rate  # ставка ЕТТ (со специфической частью и источником)
            else:
                duty = DutyRate.ad_valorem(percent)  # ручная адвалорная ставка
            items.append(
                Item(
                    name=row["name"].text().strip(),
                    tnved_code=row["tnved"].text().strip() or None,
                    unit_price=Money(price if price is not None else ZERO, currency),
                    quantity=qty if qty is not None else Decimal(1),
                    unit=row["unit"].value(),
                    duty=duty,
                    vat_rate=row["vat"].value(),
                    excise=Money.rub(excise or ZERO),
                    gross_weight_kg=gross,
                    net_weight_kg=net,
                    duty_quantity=duty_qty if row["duty_qty"].isEnabled() else None,
                    volume_m3=volume,
                    packaging=row["pack"].packaging,
                )
            )

        costs: list[CostLine] = []
        for n, row in enumerate(self.costs_table.rows):
            p = f"costs[{n}]"
            who = f"Расход «{row['name'].text() or n + 1}»"
            widgets[f"{p}.vat_rate"] = row["vat"]
            amount = num(row["amount"], f"{p}.amount", f"{who}, сумма")
            currency = code(row["currency"], f"{p}.currency", f"{who}, валюта")
            costs.append(
                CostLine(
                    name=row["name"].text().strip() or "Расход",
                    amount=Money(amount if amount is not None else ZERO, currency),
                    in_customs_value=row["in_cv"].isChecked(),
                    vat_included=row["vat_included"].isChecked(),
                    vat_rate=row["vat"].value(),
                    kind=row["meta"]["kind"],
                )
            )

        manual: dict[str, Decimal] = {}
        if self.manual_check.isChecked():
            for c, edit in self.manual_edits.items():
                value = num(edit, f"manual_rates.{c}", f"Ручной курс {c}")
                if value is not None:
                    if value <= 0:
                        issues.append(
                            Issue(f"manual_rates.{c}", f"Ручной курс {c} должен быть больше нуля")
                        )
                    else:
                        manual[c] = value

        self._read_widgets = widgets
        if issues:
            return None, issues
        qd = self.date_edit.date()
        return Shipment(
            name=self.name_edit.text().strip() or "Без названия",
            calc_date=date(qd.year(), qd.month(), qd.day()),
            delivery_mode=self.delivery_combo.value(),
            tax_regime=self.regime_combo.value(),
            allocation=self.allocation_combo.value(),
            items=items,
            costs=costs,
            manual_rates=manual,
            sale=copy.deepcopy(self.pricing.sale),
            financing=copy.deepcopy(self.pricing.financing),
            pricing_source=self.pricing.source,
            manual_cost=copy.deepcopy(self.pricing.manual_cost),
        ), []

    # --- подсветка ошибок ---------------------------------------------------------

    def clear_marks(self) -> None:
        for w in getattr(self, "_marked", []):
            if shiboken6.isValid(w):  # виджет удалённой строки уже уничтожен
                mark_error(w, None)
        self._marked: list[QWidget] = []

    def mark_issues(self, issues: list[Issue]) -> None:
        self.clear_marks()
        widgets = getattr(self, "_read_widgets", self._field_widgets)
        for issue in issues:
            w = widgets.get(issue.path)
            if w is not None and w not in (self.items_table,):
                mark_error(w, issue.message)
                self._marked.append(w)

    # --- реакции ------------------------------------------------------------------

    def _emit_changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _on_manual_toggled(self, on: bool) -> None:
        for edit in self.manual_edits.values():
            edit.setEnabled(on)
        self._emit_changed()

    def _on_regime_changed(self) -> None:
        if self._loading:
            return
        self.regimeChanged.emit()
        self.changed.emit()

    def set_rates_info(self, html: str) -> None:
        self.rates_info.setText(html)

    def set_cbr_placeholders(self, rates: dict[str, Decimal]) -> None:
        for code, edit in self.manual_edits.items():
            value = rates.get(code)
            edit.setPlaceholderText(f"ЦБ: {value:.4f}".replace(".", ",") if value else "курс ЦБ")


@dataclass(slots=True)
class PricingState:
    """Параметры вкладки «Цена продажи», сохраняемые вместе с расчётом."""

    sale: SaleParams = field(default_factory=SaleParams)
    financing: Financing = field(default_factory=Financing)
    source: PricingSource = PricingSource.IMPORT
    manual_cost: ManualCost = field(default_factory=ManualCost)

    @classmethod
    def from_shipment(cls, s: Shipment) -> PricingState:
        return cls(
            copy.deepcopy(s.sale),
            copy.deepcopy(s.financing),
            s.pricing_source,
            copy.deepcopy(s.manual_cost),
        )


def _needs_duty_quantity(rate: DutyRate, unit: Unit) -> bool:
    """Нужно ли отдельное количество: ставка не за кг/т и не за шт при количестве в шт."""
    su = rate.specific_unit
    if su is None or su.by_weight:
        return False
    return not (su is DutyUnit.PCS and unit is Unit.PCS)


def new_shipment() -> Shipment:
    settings = load_settings()
    return Shipment(
        name="Новый расчёт",
        calc_date=date.today(),
        allocation=settings.allocation_method,
        items=[
            Item(
                "",
                Money("0", "CNY"),
                Decimal(1),
                duty=DutyRate.ad_valorem(0),
                vat_rate=settings.import_vat_rate,
            )
        ],
        costs=default_cost_lines("CNY")[:2] + default_cost_lines("RUB")[2:],
    )
