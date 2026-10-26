"""Курсы ЦБ РФ: разбор XML, номинал, кэш, работа без интернета, ручной курс."""

from datetime import date
from decimal import Decimal as D

import pytest
from conftest import FIXTURES

from vedcalc.core.fx import (
    SOURCE_CACHE,
    CbrRateProvider,
    FxError,
    parse_cbr_xml,
    resolve_rates,
)

XML = (FIXTURES / "cbr_2026-03-10.xml").read_bytes()
DAY = date(2026, 3, 10)


def offline(url):
    raise OSError("нет сети")


def test_parse_with_nominal():
    rs = parse_cbr_xml(XML)
    assert rs.rate_date == DAY
    assert rs.rates["USD"].per_unit == D("82.25")
    assert rs.rates["JPY"].nominal == 100
    assert rs.rates["JPY"].per_unit == D("0.554321")


def test_fetch_uses_cbr_date_format_and_caches(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return XML

    provider = CbrRateProvider(tmp_path / "fx.json", fetch=fetch, today=lambda: DAY)
    assert provider.get_rates(DAY).rates["CNY"].value == D("11.5")
    assert calls == ["https://www.cbr.ru/scripts/XML_daily.asp?date_req=10/03/2026"]
    # Второй раз — из кэша, даже без сети
    cached = CbrRateProvider(tmp_path / "fx.json", fetch=offline, today=lambda: DAY)
    rs = cached.get_rates(DAY)
    assert rs.rates["CNY"].value == D("11.5") and rs.warning is None


def test_offline_falls_back_to_last_saved_with_warning(tmp_path):
    CbrRateProvider(tmp_path / "fx.json", fetch=lambda u: XML, today=lambda: DAY).get_rates(DAY)
    later = date(2026, 3, 20)
    rs = CbrRateProvider(tmp_path / "fx.json", fetch=offline, today=lambda: later).get_rates(later)
    assert rs.source == SOURCE_CACHE
    assert rs.rate_date == DAY
    assert "10.03.2026" in rs.warning and "20.03.2026" in rs.warning


def test_offline_without_cache_fails(tmp_path):
    with pytest.raises(FxError, match="нет сохранённых курсов"):
        CbrRateProvider(tmp_path / "fx.json", fetch=offline).get_rates(DAY)


def test_future_date_not_cached(tmp_path):
    provider = CbrRateProvider(
        tmp_path / "fx.json", fetch=lambda u: XML, today=lambda: date(2026, 3, 9)
    )
    provider.get_rates(DAY)
    assert not (tmp_path / "fx.json").exists()


def test_resolve_rates_manual_priority(tmp_path):
    provider = CbrRateProvider(tmp_path / "fx.json", fetch=lambda u: XML, today=lambda: DAY)
    r = resolve_rates({"USD", "CNY", "RUB"}, DAY, provider, {"usd": D("90")})
    assert r["USD"].manual and r["USD"].per_unit == D(90)
    assert not r["CNY"].manual and r["CNY"].rate_date == DAY
    assert r["RUB"].per_unit == 1
    assert "Курс изменён вручную" in r.warnings


def test_resolve_rates_unknown_currency(tmp_path):
    provider = CbrRateProvider(tmp_path / "fx.json", fetch=lambda u: XML, today=lambda: DAY)
    with pytest.raises(FxError, match="XYZ"):
        resolve_rates({"XYZ"}, DAY, provider)


def test_manual_rate_must_be_positive():
    with pytest.raises(FxError):
        resolve_rates({"USD"}, DAY, None, {"USD": D(0)})
