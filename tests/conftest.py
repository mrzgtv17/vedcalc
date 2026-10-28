from __future__ import annotations

import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vedcalc.core.costing import CostResult, calculate
from vedcalc.core.fees import FeeScale, load_fee_scale
from vedcalc.core.fx import Rate, RateSet, resolve_rates
from vedcalc.core.models import CostKind, CostLine, DutyRate, Item, Shipment, TaxRegime
from vedcalc.core.money import Money

# GUI-тесты работают без экрана (CI, ssh)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Настройки, реквизиты и автосохранение — во временной папке, а не в ~/Library
_TMP = Path(tempfile.mkdtemp(prefix="vedcalc-tests-"))
os.environ["VEDCALC_SETTINGS_FILE"] = str(_TMP / "settings.ini")
os.environ["VEDCALC_DATA_DIR"] = str(_TMP / "data")
os.environ["VEDCALC_NO_KEYCHAIN"] = "1"  # токен WB — только в памяти

FIXTURES = Path(__file__).parent / "fixtures"
CALC_DATE = date(2026, 3, 10)

D = Decimal


class StaticRates:
    """Провайдер курсов для тестов (вместо ЦБ РФ)."""

    def __init__(self, rates: dict[str, tuple[str, int]], on: date = CALC_DATE):
        self.rate_set = RateSet(on, {k: Rate(k, D(v), n) for k, (v, n) in rates.items()})

    def get_rates(self, on: date) -> RateSet:
        return self.rate_set


TEST_RATES = StaticRates({"CNY": ("11.5", 1), "USD": ("82.25", 1), "EUR": ("95.10", 1)})


@pytest.fixture(scope="session")
def scale() -> FeeScale:
    return load_fee_scale()


def control_shipment() -> Shipment:
    """Контрольный пример из ТЗ, раздел 6."""
    return Shipment(
        name="Контрольный пример",
        calc_date=CALC_DATE,
        tax_regime=TaxRegime.OSN,
        items=[
            Item("Товар", Money.rub(100_000), D(1), duty=DutyRate.ad_valorem(5), vat_rate=D(22))
        ],
        costs=[
            CostLine(
                "Доставка до границы",
                Money.rub(30_000),
                in_customs_value=True,
                kind=CostKind.DELIVERY_TO_BORDER,
            ),
            CostLine("Доставка по РФ", Money.rub(10_000), kind=CostKind.DELIVERY_RF),
        ],
    )


def run(shipment: Shipment, scale: FeeScale, provider=TEST_RATES) -> CostResult:
    rates = resolve_rates(
        shipment.currencies(), shipment.calc_date, provider, shipment.manual_rates
    )
    return calculate(shipment, rates, scale)
