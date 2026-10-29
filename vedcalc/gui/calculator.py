"""Вкладка «Импорт и цена»: форма + результаты, пересчёт на лету, фоновая загрузка курсов."""

from __future__ import annotations

from datetime import date
from html import escape

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSplitter, QWidget

from vedcalc.core.containers import estimate_load
from vedcalc.core.costing import calculate
from vedcalc.core.customs import NotSupportedError
from vedcalc.core.fees import FeeScale, load_fee_scale
from vedcalc.core.fx import FxError, RateProvider, RateSet, resolve_rates
from vedcalc.core.models import Shipment
from vedcalc.core.report import CostReport, build_cost_report
from vedcalc.core.validation import InputError
from vedcalc.gui.form import ShipmentForm, new_shipment
from vedcalc.gui.results import CostResultsPanel
from vedcalc.gui.theme import current

RECALC_DELAY_MS = 150


class _Signals(QObject):
    done = Signal(object, object, object)  # date, RateSet | None, ошибка | None


class _FetchJob(QRunnable):
    def __init__(self, provider: RateProvider, on: date, signals: _Signals):
        super().__init__()
        self.provider, self.on, self.signals = provider, on, signals

    def run(self) -> None:
        try:
            self.signals.done.emit(self.on, self.provider.get_rates(self.on), None)
        except Exception as exc:
            self.signals.done.emit(self.on, None, str(exc))


class RatesLoader(QObject):
    """Загружает курсы ЦБ в фоне, чтобы окно не замирало при медленной сети."""

    loaded = Signal()

    def __init__(self, provider: RateProvider, threaded: bool = True, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.threaded = threaded
        self.sets: dict[date, RateSet] = {}
        self.errors: dict[date, str] = {}
        self.pending: set[date] = set()
        self._signals = _Signals()
        # AutoConnection: из фонового потока — через очередь, в синхронном режиме — сразу
        self._signals.done.connect(self._on_done)

    def request(self, on: date) -> None:
        if on in self.sets or on in self.pending or on in self.errors:
            return
        self.pending.add(on)
        if self.threaded:
            QThreadPool.globalInstance().start(_FetchJob(self.provider, on, self._signals))
        else:
            _FetchJob(self.provider, on, self._signals).run()

    def retry(self, on: date) -> None:
        self.errors.pop(on, None)
        self.request(on)

    def _on_done(self, on: date, rate_set: RateSet | None, error: str | None) -> None:
        self.pending.discard(on)
        if rate_set is not None:
            self.sets[on] = rate_set
        else:
            self.errors[on] = error or "неизвестная ошибка"
        self.loaded.emit()

    def get_rates(self, on: date) -> RateSet:
        """Интерфейс RateProvider для resolve_rates: только уже загруженное."""
        if on in self.sets:
            return self.sets[on]
        raise FxError(self.errors.get(on, "курсы ещё загружаются"))


class CalculatorTab(QWidget):
    """Вкладка «Импорт»: себестоимость партии и единицы, подсказка о загрузке контейнера."""

    modified = Signal()
    reportChanged = Signal()
    showPacking = Signal()  # открыть грузовые места партии во вкладке «Контейнер»

    def __init__(
        self,
        provider: RateProvider,
        fee_scale: FeeScale | None = None,
        *,
        threaded: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.fee_scale = fee_scale or load_fee_scale()
        self.rates = RatesLoader(provider, threaded=threaded, parent=self)
        self.rates.loaded.connect(self.recalc)
        self.report: CostReport | None = None
        self.touched = False  # пользователь уже что-то вводил после открытия расчёта

        self.form = ShipmentForm()
        self.results = CostResultsPanel()
        self.results.showPacking.connect(self.showPacking)

        left = QScrollArea()
        left.setWidgetResizable(True)
        left.setFrameShape(QFrame.Shape.NoFrame)
        left.setWidget(self.form)
        left.setMinimumWidth(520)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self.results)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([780, 520])
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(splitter)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(RECALC_DELAY_MS)
        self._timer.timeout.connect(self.recalc)

        self.form.changed.connect(self._on_input_changed)

        self.set_shipment(new_shipment())

    # --- данные -----------------------------------------------------------------------

    def set_shipment(self, shipment: Shipment) -> None:
        self.form.set_shipment(shipment)
        self.touched = False
        self.recalc()

    def shipment(self) -> Shipment:
        """Текущий расчёт для сохранения. InputError — если поля не разобрать."""
        shipment, issues = self.form.read()
        if shipment is None:
            raise InputError(issues)
        return shipment

    # --- пересчёт ---------------------------------------------------------------------

    def _on_input_changed(self) -> None:
        self.touched = True
        self.modified.emit()
        self._timer.start()

    def recalc(self) -> CostReport | None:
        """Пересчёт. Неожиданная ошибка показывается, а не оставляет на экране старый итог."""
        try:
            return self._recalc()
        except Exception as exc:  # pragma: no cover — защитная сетка
            self._set_report(None)
            self.results.show_message(
                f"<b>Не удалось выполнить расчёт.</b><br>{escape(type(exc).__name__)}: "
                f"{escape(str(exc))}<br>Сообщите разработчику, как это получилось.",
                error=True,
            )
            return None

    def _recalc(self) -> CostReport | None:
        self._timer.stop()
        # Курсы на дату расчёта запрашиваем сразу, даже если форма ещё не заполнена
        form_date = self.form.date_edit.date().toPython()
        self.rates.request(form_date)
        shipment, issues = self.form.read()
        self.form.mark_issues(issues if self.touched else [])
        if shipment is None:
            self._update_rates_info(form_date, None)
            self._set_report(None)
            self.results.show_issues(issues, gentle=not self.touched)
            return None

        on = shipment.calc_date
        need_cbr = shipment.currencies() - set(shipment.manual_rates)
        self._update_rates_info(on, need_cbr)
        if need_cbr and on not in self.rates.sets:
            if on in self.rates.errors:
                self._set_report(None)
                self.results.show_message(
                    "<b>Не удалось получить курсы ЦБ РФ.</b><br>"
                    f"{escape(self.rates.errors[on])}<br>"
                    "Проверьте интернет или задайте курс вручную.",
                    error=True,
                )
            else:
                self.rates.request(on)
                if on in self.rates.pending:
                    self._set_report(None)
                    self.results.show_message(f"Загружаю курсы ЦБ РФ на {on:%d.%m.%Y}…")
                    return None
                return self._recalc()  # синхронная загрузка уже завершилась
            return None

        try:
            rates = resolve_rates(shipment.currencies(), on, self.rates, shipment.manual_rates)
            cost = calculate(shipment, rates, self.fee_scale)
            load = estimate_load(shipment)
            report = build_cost_report(shipment, cost, load)
        except InputError as exc:
            self.form.mark_issues(exc.issues if self.touched else [])
            self._set_report(None)
            self.results.show_issues(exc.issues, gentle=not self.touched)
            return None
        except (FxError, NotSupportedError, ValueError) as exc:
            self._set_report(None)
            self.results.show_message(escape(str(exc)), error=True)
            return None
        self._set_report(report)
        self.results.show_report(report)
        return report

    def _set_report(self, report: CostReport | None) -> None:
        self.report = report
        self.reportChanged.emit()

    def _update_rates_info(self, on: date, need_cbr: set[str] | None) -> None:
        """need_cbr=None — валюты ещё не известны (форма не заполнена)."""
        rate_set = self.rates.sets.get(on)
        if rate_set is not None:
            self.form.set_cbr_placeholders({c: r.per_unit for c, r in rate_set.rates.items()})
        if need_cbr is not None and not need_cbr and not self.form.manual_check.isChecked():
            self.form.set_rates_info("Все суммы в рублях — курсы не нужны.")
            return
        if rate_set is None:
            if on in self.rates.errors:
                self.form.set_rates_info(
                    f"<span style='color:{current().negative}'>Курсы ЦБ РФ недоступны."
                    "</span> Задайте курс вручную."
                )
            else:
                self.form.set_rates_info(f"Загружаю курсы ЦБ РФ на {on:%d.%m.%Y}…")
            return
        shown = sorted(need_cbr or ()) or [c for c in ("CNY", "USD", "EUR") if c in rate_set.rates]
        parts = []
        for code in shown:
            r = rate_set.rates.get(code)
            if r is not None:
                per = f" за {r.nominal}" if r.nominal != 1 else ""
                parts.append(f"<b>{code}</b> {r.value:.4f}{per}".replace(".", ","))
        text = f"{rate_set.source}, курс на {rate_set.rate_date:%d.%m.%Y}: " + " · ".join(parts)
        if rate_set.warning:
            text += f"<br><span style='color:{current().warn}'>⚠ {escape(rate_set.warning)}</span>"
        self.form.set_rates_info(text)
