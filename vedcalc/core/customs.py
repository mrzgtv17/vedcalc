"""Таможенные платежи: пошлина, НДС при ввозе. Суммы платежей — в копейках."""

from __future__ import annotations

from decimal import Decimal

from vedcalc.core.models import DutyKind, DutyRate
from vedcalc.core.money import pct, round_money


class NotSupportedError(NotImplementedError):
    pass


def duty_amount(duty: DutyRate, customs_value: Decimal) -> Decimal:
    """Ввозная таможенная пошлина. Пока только адвалорная: ТС × ставка."""
    if duty.kind is DutyKind.AD_VALOREM:
        return round_money(customs_value * pct(duty.percent))
    raise NotSupportedError("Специфические и комбинированные ставки пошлины пока не поддерживаются")


def import_vat(customs_value: Decimal, duty: Decimal, excise: Decimal, rate: Decimal) -> Decimal:
    """НДС при ввозе = (ТС + пошлина + акциз) × ставка НДС. Сбор в базу не входит."""
    return round_money((customs_value + duty + excise) * pct(rate))
