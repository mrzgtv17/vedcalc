"""Панели результатов: себестоимость импорта и цена продажи. Упор — на 1 шт / кг / т."""

from __future__ import annotations

from decimal import Decimal
from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vedcalc.core.explain import fmt_money, fmt_pct
from vedcalc.core.models import PriceMode
from vedcalc.core.report import CostReport, PriceReport
from vedcalc.core.unit_economics import UnitCostView, UnitLine, UnitPriceView
from vedcalc.core.validation import Issue
from vedcalc.gui.theme import ERROR_BOX, INFO_BOX, WARN_BOX
from vedcalc.gui.widgets import DecimalEdit, EnumCombo, SectionView, mark_error
from vedcalc.reports.html import load_warning, render_cost_html, render_price_html
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
        v.addWidget(cap)
        v.addWidget(self.value)
        if sub:
            s = _label(sub, "metricSub")
            s.setWordWrap(True)
            v.addWidget(s)


class _UnitCard(QFrame):
    """Общая основа карточки «за 1 шт / кг / т»."""

    def __init__(self, name: str, per: str, quantity: Decimal, unit_label: str):
        super().__init__()
        self.setObjectName("card")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(16, 14, 16, 16)
        self.v.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(_label(name or "Позиция", "cardTitle"))
        head.addWidget(_label(per, "chip"))
        head.addStretch(1)
        qty = f"{quantity.normalize():f}".replace(".", ",")
        head.addWidget(_label(f"в партии {qty} {unit_label}", "muted"))
        self.v.addLayout(head)
        self.tiles: dict[str, MetricTile] = {}
        self.tiles_row = QHBoxLayout()
        self.tiles_row.setSpacing(8)
        self.v.addLayout(self.tiles_row)

    def add_tile(self, key: str, caption: str, value: Decimal, sub: str, accent=False) -> None:
        tile = MetricTile(caption, fmt_money(value), sub, accent=accent)
        self.tiles[key] = tile
        self.tiles_row.addWidget(tile, 1)

    def add_lines(self, caption: str, lines: tuple[UnitLine, ...]) -> None:
        self.v.addWidget(_label(caption, "caption"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        for n, line in enumerate(lines):
            muted = "muted" if line.informational else ""
            label = _label(line.label, muted, bold=line.strong)
            value = _label(line.text, muted, right=True, bold=line.strong)
            value.setObjectName(f"unit:{line.label}")
            if line.informational:
                value.setStyleSheet(f"color:{TEXT_MUTED};")
            if line.label == "Чистая прибыль" and line.value:
                value.setStyleSheet(f"color:{POSITIVE if line.value > 0 else NEGATIVE};")
            tip = line.formula + (f"\n{line.hint}" if line.hint else "")
            label.setToolTip(tip)
            value.setToolTip(tip)
            grid.addWidget(label, n, 0)
            grid.addWidget(value, n, 1)
        self.v.addLayout(grid)

    def metric_text(self, key: str) -> str | None:
        tile = self.tiles.get(key)
        return tile.value.text() if tile else None

    def line_text(self, label: str) -> str | None:
        w = self.findChild(QLabel, f"unit:{label}")
        return w.text() if w else None


class CostUnitCard(_UnitCard):
    def __init__(self, view: UnitCostView, osn: bool):
        super().__init__(view.name, view.per, view.quantity, view.unit_label)
        self.view = view
        self.add_tile(
            "cost",
            "Себестоимость без НДС" if osn else "Себестоимость (УСН)",
            view.cost_net,
            f"с НДС {fmt_money(view.cost_gross)}" if osn else "НДС включён",
            accent=True,
        )
        if osn:
            self.add_tile("vat", "НДС к вычету", view.deductible_vat, "возмещается из бюджета")
        self.add_lines(f"Из чего складывается себестоимость {view.per}", view.cost_lines)


class PriceUnitCard(_UnitCard):
    def __init__(self, view: UnitPriceView, osn: bool):
        super().__init__(view.name, view.per, view.quantity, view.unit_label)
        self.view = view
        p = view.price
        self.add_tile(
            "cost",
            "Себестоимость" + (" без НДС" if osn else ""),
            view.cost_net,
            f"+ стоимость денег {fmt_money(view.financing)}" if view.financing else "",
        )
        self.add_tile(
            "price", "Цена без НДС" if osn else "Цена", p.net,
            f"с НДС {fmt_money(p.gross)}" if osn else "", accent=True,
        )  # fmt: skip
        margin = fmt_pct(p.gross_profit / p.net) if p.net else "—"
        self.add_tile("profit", "Чистая прибыль", p.net_profit, f"валовая маржа {margin}")
        self.add_lines(f"Цена и прибыль {view.per}", p.lines)


class LoadCard(QFrame):
    """Подсказка: сколько контейнера занято по объёму и по весу."""

    def __init__(self, report: CostReport):
        super().__init__()
        self.setObjectName("card")
        load = report.load
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        v.addWidget(_label(f"Загрузка контейнера {load.spec.title}", "cardTitle"))
        self.bars: dict[str, QProgressBar] = {}
        for key, caption, share, text in (
            (
                "volume",
                "Объём",
                load.volume_share,
                f"{fmt_money(load.volume_m3)} из {fmt_money(load.spec.volume_m3)} м³",
            ),
            (
                "weight",
                "Вес брутто",
                load.weight_share,
                f"{fmt_money(load.weight_kg / 1000)} из "
                f"{fmt_money(load.spec.max_cargo_kg / 1000)} т",
            ),
        ):
            row = QHBoxLayout()
            row.addWidget(_label(caption), 0)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(min(100, int(share * 100)))
            bar.setFormat(f"{fmt_pct(share, 0)} · {text}")
            bar.setProperty("over", share > 1)
            bar.setObjectName("loadBar")
            self.bars[key] = bar
            row.addWidget(bar, 1)
            v.addLayout(row)
        if warning := load_warning(report):
            msg = _label(warning)
            msg.setWordWrap(True)
            msg.setStyleSheet(f"color:{NEGATIVE}; font-weight:600;")
            msg.setObjectName("loadWarning")
            v.addWidget(msg)
        missing = []
        if load.missing_volume:
            missing.append("без объёма: " + ", ".join(load.missing_volume))
        if load.missing_weight:
            missing.append("без веса: " + ", ".join(load.missing_weight))
        note = "По сумме объёмов и весов позиций, без учёта укладки"
        if missing:
            note += ". Не учтены позиции " + "; ".join(missing)
        hint = _label(note + ".", "caption")
        hint.setWordWrap(True)
        v.addWidget(hint)

    def bar_text(self, key: str) -> str:
        return self.bars[key].format()


class _ResultsBase(QTabWidget):
    """Вкладки «Результаты» и «Детализация», строка статуса, скрытие пустых блоков."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        page = QWidget()
        page.setObjectName("page")
        self.page_layout = QVBoxLayout(page)
        self.page_layout.setContentsMargins(2, 2, 6, 2)
        self.page_layout.setSpacing(12)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.hide()
        self.page_layout.addWidget(self.status)
        self.result_widgets: list[QWidget] = []

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.addTab(scroll, "Результаты")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.addTab(self.details, "Детализация")

        self.cards_host = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)

    def _set_status(self, html: str | None, style: str = INFO_BOX) -> None:
        if not html:
            self.status.hide()
            return
        self.status.setStyleSheet(style)
        self.status.setText(html)
        self.status.show()

    def _set_results_visible(self, visible: bool) -> None:
        # Без результата блоки не показываем вовсе — пустые рамки только путают.
        for w in self.result_widgets:
            w.setVisible(visible)

    def show_issues(self, issues: list[Issue], *, gentle: bool = False) -> None:
        """gentle — расчёт только открыт и ещё не заполнен: подсказка вместо ошибки."""
        items = "".join(f"<li>{escape(i.message)}</li>" for i in issues)
        title = "Заполните данные для расчёта:" if gentle else "Проверьте ввод:"
        self._set_status(
            f"<b>{title}</b><ul style='margin:4px 0 0 -18px'>{items}</ul>",
            INFO_BOX if gentle else ERROR_BOX,
        )
        self._set_results_visible(False)

    def show_message(self, html: str, *, error: bool = False) -> None:
        self._set_status(html, ERROR_BOX if error else INFO_BOX)
        self._set_results_visible(False)

    def _show_warnings(self, warnings: list[str]) -> None:
        if warnings:
            items = "".join(f"<li>{escape(w)}</li>" for w in warnings)
            self._set_status(f"<ul style='margin:0 0 0 -18px'>{items}</ul>", WARN_BOX)
        else:
            self._set_status(None)

    def _replace_cards(self, cards: list[QWidget]) -> None:
        while self.cards_layout.count():
            w = self.cards_layout.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for card in cards:
            self.cards_layout.addWidget(card)

    def _set_details(self, html: str) -> None:
        scroll = self.details.verticalScrollBar().value()
        self.details.setHtml(html)
        self.details.verticalScrollBar().setValue(scroll)


class CostResultsPanel(_ResultsBase):
    """Вкладка «Импорт»: себестоимость единицы, итоги партии, загрузка контейнера."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.report: CostReport | None = None
        self.unit_cards: list[CostUnitCard] = []
        self.load_card: LoadCard | None = None
        self.load_host = QWidget()
        self.load_layout = QVBoxLayout(self.load_host)
        self.load_layout.setContentsMargins(0, 0, 0, 0)
        self.batch_view = SectionView("Итого по партии")
        for w in (self.load_host, self.cards_host, self.batch_view):
            self.page_layout.addWidget(w)
            self.result_widgets.append(w)
        self.page_layout.addStretch(1)

    def show_report(self, report: CostReport) -> None:
        self.report = report
        self._set_results_visible(True)
        self._show_warnings(list(report.cost.warnings))
        if self.load_card is not None:
            self.load_card.setParent(None)
            self.load_card.deleteLater()
            self.load_card = None
        if report.load is not None:
            self.load_card = LoadCard(report)
            self.load_layout.addWidget(self.load_card)
        self.unit_cards = [CostUnitCard(v, report.vat_payer) for v in report.unit_views()]
        self._replace_cards(self.unit_cards)
        self.batch_view.set_rows(report.batch_section().rows)
        self._set_details(render_cost_html(report, font_family=self.font().family()))


class PriceResultsPanel(_ResultsBase):
    """Вкладка «Цена продажи»: сценарий, цена и прибыль за единицу, итоги, стоимость денег."""

    targetChanged = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.report: PriceReport | None = None
        self.unit_cards: list[PriceUnitCard] = []

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
        # Сценарий доступен и без результата: в нём может быть сама ошибка
        self.page_layout.addWidget(self.target_box)

        self.batch_view = SectionView("Итого по партии")
        self.financing_view = SectionView("Стоимость денег")
        for w in (self.cards_host, self.batch_view, self.financing_view):
            self.page_layout.addWidget(w)
            self.result_widgets.append(w)
        self.page_layout.addStretch(1)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.value_edit.textChanged.connect(lambda _=None: self.targetChanged.emit())

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
        t = self.report.target if self.report else None
        if t is not None:
            views = self.report.unit_views()
            suggestion = {
                PriceMode.MARKUP: Decimal(20),
                PriceMode.MARGIN: Decimal(20),
                PriceMode.NET_PROFIT: t.net_profit if t.net_profit > 0 else Decimal(10000),
                PriceMode.UNIT_PRICE: views[0].price.net if views else Decimal(0),
                PriceMode.PRICE: t.price_net,
            }[mode]
            self.value_edit.blockSignals(True)
            self.value_edit.set_value(suggestion)
            self.value_edit.blockSignals(False)
        self.targetChanged.emit()

    def show_issues(self, issues: list[Issue], *, gentle: bool = False) -> None:
        super().show_issues(issues, gentle=gentle)
        self.target_summary.hide()

    def show_message(self, html: str, *, error: bool = False) -> None:
        super().show_message(html, error=error)
        self.target_summary.hide()

    def show_report(self, report: PriceReport) -> None:
        self.report = report
        self._set_results_visible(True)
        self._set_status(None)
        t = report.target
        if t is None:
            self.target_error.setText(report.target_error or "")
            self.target_error.show()
            self.target_summary.hide()
        else:
            self.target_error.hide()
            fin = f" · стоимость денег {fmt_money(t.financing)}" if t.financing else ""
            self.target_summary.setText(
                f"{report.target_title}. Наценка {fmt_pct(t.markup)} · маржинальность "
                f"{fmt_pct(t.margin)} · чистая прибыль партии {fmt_money(t.net_profit)}{fin}"
            )
            self.target_summary.show()
        self.unit_cards = [PriceUnitCard(v, report.vat_payer) for v in report.unit_views()]
        self._replace_cards(self.unit_cards)
        self.batch_view.set_rows(report.batch_section().rows)
        fin_rows = report.financing_rows()
        self.financing_view.set_rows(fin_rows)
        self.financing_view.setVisible(bool(fin_rows))
        self._set_details(render_price_html(report, font_family=self.font().family()))
