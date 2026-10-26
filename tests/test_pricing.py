"""Цена продажи: режимы, обратный расчёт, расходы от выручки, ошибки ввода."""

from decimal import Decimal as D

import pytest
from conftest import control_shipment, run

from vedcalc.core.pricing import PriceMode, break_even, evaluate, price_for
from vedcalc.core.validation import InputError


@pytest.fixture
def cost(scale):
    return run(control_shipment(), scale)


def with_r(scale, r):
    s = control_shipment()
    s.sale.price_dependent_rate = D(r)
    return run(s, scale)


def test_markup(cost):
    p = price_for(cost, PriceMode.MARKUP, 20)
    assert p.price_net == D("177277.20")  # 147 731 × 1,2
    assert round(p.markup * 100, 4) == D("20.0000")


def test_margin(cost):
    p = price_for(cost, PriceMode.MARGIN, 25)
    assert p.price_net == D("196974.67")  # 147 731 / 0,75
    assert round(p.margin * 100, 2) == D("25.00")


def test_target_net_profit(cost):
    p = price_for(cost, PriceMode.NET_PROFIT, 30_000)
    assert p.price_net == D("187731.00")  # 147 731 + 30 000 / 0,75
    assert p.net_profit == D("30000.00")


def test_negative_target_profit_means_loss_without_tax(cost):
    p = price_for(cost, PriceMode.NET_PROFIT, -1_000)
    assert p.price_net == D("146731.00") and p.profit_tax == 0 and p.net_profit == D("-1000.00")


@pytest.mark.parametrize("mode", [PriceMode.MARKUP, PriceMode.MARGIN, PriceMode.NET_PROFIT])
def test_reverse_matches_forward(cost, mode):
    forward = price_for(cost, mode, 17)
    back = evaluate(cost, forward.price_net)
    assert back == forward


def test_margin_100_rejected(cost):
    with pytest.raises(InputError, match="меньше 100%"):
        price_for(cost, PriceMode.MARGIN, 100)


def test_break_even_with_price_dependent_expenses(scale):
    cost = with_r(scale, 5)
    p = break_even(cost)
    assert p.price_net == D("155506.32")  # 147 731 / 0,95, вверх до копейки
    assert p.price_dependent == D("7775.32")
    assert D(0) <= p.net_profit < D("0.02")


def test_margin_with_price_dependent_expenses(scale):
    cost = with_r(scale, 5)
    p = price_for(cost, PriceMode.MARGIN, 20)
    assert p.price_net == D("196974.67")  # 147 731 / (1 − 0,2 − 0,05)
    assert round(p.margin * 100, 2) == D("20.00")


def test_margin_plus_r_over_100_rejected(scale):
    with pytest.raises(InputError, match="вместе с расходами"):
        price_for(with_r(scale, 30), PriceMode.MARGIN, 70)


def test_break_even_never_loses_a_kopeck(scale):
    s = control_shipment()
    s.costs[1].amount = s.costs[1].amount + type(s.costs[1].amount).rub("0.01")
    s.sale.price_dependent_rate = D("3.3333")
    for r in ("0", "1.7", "3.3333", "12.5"):
        s.sale.price_dependent_rate = D(r)
        assert break_even(run(s, scale)).net_profit >= 0


def test_per_item_prices_sum_to_total(scale):
    from test_costing import two_items

    cost = run(two_items(), scale)
    p = price_for(cost, PriceMode.MARGIN, 30)
    assert sum(i.price_net for i in p.items) == p.price_net
    assert sum(i.vat for i in p.items) == p.sale_vat
    assert p.items[0].unit_price_net == p.items[0].price_net / 10


def test_loss_has_no_profit_tax(cost):
    p = evaluate(cost, 100_000)
    assert p.gross_profit == D("-47731.00") and p.profit_tax == 0
    assert p.net_profit == D("-47731.00")
