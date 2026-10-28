"""Стоимость денег: проценты за деньги, вложенные в партию, до оплаты покупателем.

Упрощённо: F = сумма × ставка × дни / 365 × доля заёмных.
По графику: остаток вложенных денег B(t) = оплаты поставщику, логистике и таможне минус
поступления от покупателя; F = Σ max(0, B) × дни × ставка / 365 × доля заёмных.
Суммы — с НДС: деньги уходят и приходят вместе с НДС. Возмещение НДС из бюджета
по срокам не моделируется (см. docs/ASSUMPTIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vedcalc.core.basis import PricingBasis
from vedcalc.core.explain import fmt_money, fmt_rate
from vedcalc.core.models import Financing, FinancingMode
from vedcalc.core.money import HUNDRED, ZERO, pct, round_money

DAYS_IN_YEAR = Decimal(365)


@dataclass(frozen=True, slots=True)
class CashEvent:
    day: int
    label: str
    amount: Decimal  # > 0 — платим, < 0 — получаем


@dataclass(frozen=True, slots=True)
class Period:
    day_from: int
    day_to: int
    balance: Decimal  # вложено на начало периода
    interest: Decimal  # проценты за период (до учёта доли заёмных)

    @property
    def days(self) -> int:
        return self.day_to - self.day_from


@dataclass(frozen=True, slots=True)
class FinancingResult:
    cost: Decimal  # F — стоимость денег за партию, руб.
    events: tuple[CashEvent, ...]
    periods: tuple[Period, ...]
    formula: str

    @property
    def total_days(self) -> int:
        return self.periods[-1].day_to - self.periods[0].day_from if self.periods else 0


NONE = FinancingResult(ZERO, (), (), "")


def schedule_events(fin: Financing, basis: PricingBasis, price_gross: Decimal) -> list[CashEvent]:
    """События графика: дни от предоплаты поставщику."""
    prepay = round_money(basis.supplier_payment * pct(fin.supplier_prepay_pct))
    arrival = fin.supplier_balance_day + fin.transit_days
    release = arrival + fin.customs_days
    delivery = release + fin.delivery_days
    buyer_prepay = round_money(price_gross * pct(fin.buyer_prepay_pct))
    events = [
        CashEvent(0, f"Предоплата поставщику {fmt_rate(fin.supplier_prepay_pct)}", prepay),
        CashEvent(fin.supplier_balance_day, "Остаток поставщику", basis.supplier_payment - prepay),
        CashEvent(arrival, "Логистика и прочие расходы (прибытие)", basis.logistics_payment),
        CashEvent(release, "Таможенные платежи (выпуск)", basis.customs_payment),
        CashEvent(fin.buyer_prepay_day, "Предоплата покупателя", -buyer_prepay),
        CashEvent(
            delivery + fin.buyer_deferral_days,
            "Оплата покупателя (остаток)",
            -(price_gross - buyer_prepay),
        ),
    ]
    return sorted((e for e in events if e.amount), key=lambda e: (e.day, -e.amount))


def financing_cost(fin: Financing, basis: PricingBasis, price_gross: Decimal) -> FinancingResult:
    if fin.mode is FinancingMode.OFF or fin.annual_rate <= 0 or fin.borrowed_share <= 0:
        return NONE
    rate = pct(fin.annual_rate)
    share = pct(fin.borrowed_share)
    share_txt = (
        f" × {fmt_rate(fin.borrowed_share)} заёмных" if fin.borrowed_share != HUNDRED else ""
    )

    if fin.mode is FinancingMode.SIMPLE:
        amount = fin.loan_amount if fin.loan_amount is not None else basis.cost_gross
        cost = round_money(amount * rate * fin.loan_days / DAYS_IN_YEAR * share)
        formula = (
            f"{fmt_money(amount)} × {fmt_rate(fin.annual_rate)} × {fin.loan_days} дн. / 365"
            f"{share_txt} = {fmt_money(cost)}"
        )
        return FinancingResult(cost, (), (Period(0, fin.loan_days, amount, cost),), formula)

    events = schedule_events(fin, basis, price_gross)
    periods: list[Period] = []
    balance = ZERO
    total = ZERO
    for k, event in enumerate(events):
        balance += event.amount
        next_day = events[k + 1].day if k + 1 < len(events) else event.day
        if next_day > event.day:
            invested = max(ZERO, balance)
            interest = invested * rate * (next_day - event.day) / DAYS_IN_YEAR
            periods.append(Period(event.day, next_day, invested, interest))
            total += interest
    cost = round_money(total * share)
    formula = f"Σ вложено × дни × {fmt_rate(fin.annual_rate)} / 365{share_txt} = {fmt_money(cost)}"
    return FinancingResult(cost, tuple(events), tuple(periods), formula)
