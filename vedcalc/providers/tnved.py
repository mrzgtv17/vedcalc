"""Подстановка ставок по коду ТН ВЭД — интерфейс на будущее.

Планируется: локальная база ставок, затем внешний API или LLM-подбор кода по описанию
товара. Подобранные ставки всегда подтверждает пользователь.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from vedcalc.core.models import DutyRate


@dataclass(frozen=True, slots=True)
class TnvedRates:
    code: str
    duty: DutyRate
    vat_rate: Decimal
    excise_note: str | None = None
    source: str = ""


class TnvedRateProvider(Protocol):
    def lookup(self, code: str) -> TnvedRates | None:
        """Ставки для 10-значного кода ТН ВЭД или None, если код неизвестен."""
        ...


class NullTnvedProvider:
    """Заглушка: ставок не знает, пользователь вводит их вручную."""

    def lookup(self, code: str) -> TnvedRates | None:
        return None
