"""Шкала таможенных сборов 2026: границы порогов (ПП № 1637 в ред. ПП № 1638)."""

from datetime import date
from decimal import Decimal as D

import pytest

from vedcalc.core.fees import parse_fee_scale

BOUNDARIES = [
    # (таможенная стоимость, сбор)
    ("0.00", 1231),
    ("200000.00", 1231),
    ("200000.01", 2462),
    ("450000.00", 2462),
    ("450000.01", 4924),
    ("1200000.00", 4924),
    ("1200000.01", 13541),
    ("2700000.00", 13541),
    ("2700000.01", 18465),
    ("4200000.00", 18465),
    ("4200000.01", 21344),
    ("5500000.00", 21344),
    ("5500000.01", 49240),
    ("10000000.00", 49240),
    ("10000000.01", 73860),
    ("999999999.99", 73860),
]


@pytest.mark.parametrize(("cv", "fee"), BOUNDARIES)
def test_boundaries(scale, cv, fee):
    assert scale.fee_for(D(cv)) == D(fee)


def test_compares_rounded_to_kopecks(scale):
    # 200 000,004 при округлении до копеек — 200 000,00, т.е. первый порог
    assert scale.fee_for(D("200000.004")) == D(1231)
    assert scale.fee_for(D("200000.005")) == D(2462)


def test_metadata_and_validity(scale):
    assert scale.valid_from == date(2026, 1, 1)
    assert "1637" in scale.legal_basis and "1638" in scale.legal_basis
    assert scale.validity_warning(date(2026, 10, 26)) is None
    warning = scale.validity_warning(date(2025, 12, 31))
    assert warning and "01.01.2026" in warning


def test_bracket_labels(scale):
    assert scale.bracket_for(D(1)).label == "не более 200 000,00 руб."
    assert scale.bracket_for(D(10**8)).label.startswith("свыше 10 000 000,00")


def test_rejects_bad_scale():
    with pytest.raises(ValueError, match="открытым"):
        parse_fee_scale(
            {
                "id": "x",
                "valid_from": "2026-01-01",
                "legal_basis": "",
                "brackets": [{"up_to": "100", "fee": "1"}],
            }
        )
    with pytest.raises(ValueError, match="возрастать"):
        parse_fee_scale(
            {
                "id": "x",
                "valid_from": "2026-01-01",
                "legal_basis": "",
                "brackets": [
                    {"up_to": "100", "fee": "1"},
                    {"up_to": "50", "fee": "2"},
                    {"up_to": None, "fee": "3"},
                ],
            }
        )
