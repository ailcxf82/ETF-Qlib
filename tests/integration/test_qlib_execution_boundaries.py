import numpy as np
import pandas as pd
import pytest
import qlib
from qlib.backtest.decision import Order
from qlib.backtest.position import Position
from qlib.constant import REG_CN

from etf_ml.backtest.accounting import Account
from etf_ml.backtest.qlib_runner import ETFExchange
from etf_ml.contracts import PortfolioPolicy

pytestmark = pytest.mark.qlib


@pytest.fixture
def exchange_factory(panel, calendar, source_spec, tmp_path):
    qlib.init(provider_uri=source_spec.source, region=REG_CN, kernels=1,
              exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                           "kwargs": {"uri": (tmp_path / "recorders").as_uri(),
                                      "default_exp_name": "execution-boundaries"}})

    def build(*, shares=150., cash=100., minimum_commission=5., capacity=1e9, commission_rate=.003):
        frame = panel.copy()
        frame["raw_open"] = 10.
        frame["raw_close"] = 10.
        # capacity is a currency allowance, computed from previous-day information.
        frame["amount_currency"] = capacity / .2
        policy = PortfolioPolicy(k_mode="count", k=1, liquidity_mode="participation",
                                 risk_mode="max_drawdown", minimum_commission=minimum_commission,
                                 liquidity_lookback=1, commission_rate=commission_rate)
        exchange = ETFExchange(frame, policy, start_time=calendar[10], end_time=calendar[12])
        instrument = "510300.SH"
        position = Position(cash=cash, position_dict={instrument: {"amount": shares, "price": 10.}})
        exchange.replay = Account(cash=cash, shares={instrument: shares}, marks={instrument: 10.})
        return exchange, position, instrument

    return build


def order(instrument, date, amount, direction=Order.SELL):
    return Order(stock_id=instrument, amount=amount, direction=direction,
                 start_time=date, end_time=date + pd.Timedelta(hours=23))


@pytest.mark.parametrize("shares,requested", [(150., 150.), (50., 50.), (150.5, 150.5), (150., 200.)])
def test_actual_qlib_full_odd_lot_liquidation_reconciles(exchange_factory, calendar, shares, requested):
    exchange, position, instrument = exchange_factory(shares=shares)
    trade = order(instrument, calendar[10], requested)
    amount, fee, price = exchange.deal_order(trade, position=position)
    assert trade.deal_amount == pytest.approx(shares)
    assert amount == pytest.approx(shares * 9.997)
    assert fee == pytest.approx(max(amount * .003, 5.))
    assert price == pytest.approx(9.997)
    assert position.get_stock_amount(instrument) == 0
    assert instrument not in exchange.replay.shares
    assert position.get_cash() == pytest.approx(100. + amount - fee)
    assert exchange.audit[-1]["requested_shares"] == requested
    assert exchange.audit[-1]["filled_shares"] == shares
    assert exchange.audit[-1]["status"] == ("filled" if requested == shares else "partial")


def test_partial_liquidity_sell_keeps_odd_remainder_until_next_day(exchange_factory, calendar):
    exchange, position, instrument = exchange_factory(capacity=125 * 9.997)
    # Future turnover cannot improve today's historical participation allowance.
    exchange.panel.loc[(calendar[10], instrument), 'amount_currency'] = 1e9
    first = order(instrument, calendar[10], 150.)
    exchange.deal_order(first, position=position, dealt_order_amount={})
    assert first.deal_amount == 100.
    assert position.get_stock_amount(instrument) == 50.
    assert exchange.audit[-1]["status"] == "partial"
    assert exchange.audit[-1]["reason"] == "liquidity_cash_or_lot_limit"
    # The remaining same-day allowance cannot sell a full odd-lot remainder.
    second = order(instrument, calendar[10], 50.)
    exchange.deal_order(second, position=position, dealt_order_amount={instrument: 100.})
    assert second.deal_amount == 0.
    assert position.get_stock_amount(instrument) == 50.
    third = order(instrument, calendar[11], 50.)
    exchange.deal_order(third, position=position, dealt_order_amount={})
    assert third.deal_amount == 50.
    assert position.get_stock_amount(instrument) == 0.
    assert len(exchange.replay.trades) == 3


def test_minimum_commission_can_prevent_tiny_odd_lot_sale(exchange_factory, calendar):
    exchange, position, instrument = exchange_factory(shares=.1, cash=0., minimum_commission=5.)
    trade = order(instrument, calendar[10], .1)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert amount == fee == trade.deal_amount == 0.
    assert position.get_cash() == 0.
    assert position.get_stock_amount(instrument) == .1
    assert exchange.audit[-1]["reason"] == "fee_exceeds_available_cash"


def test_existing_cash_can_pay_minimum_fee_for_tiny_odd_lot(exchange_factory, calendar):
    exchange, position, instrument = exchange_factory(shares=.1, cash=5., minimum_commission=5.)
    trade = order(instrument, calendar[10], .1)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert trade.deal_amount == .1
    assert amount == pytest.approx(.9997)
    assert fee == 5.
    assert position.get_cash() == pytest.approx(.9997)
    assert position.get_stock_amount(instrument) == 0.


@pytest.mark.parametrize("requested,expected", [(100., 100.), (50., 0.)])
def test_partial_request_does_not_unlock_full_liquidation(exchange_factory, calendar, requested, expected):
    exchange, position, instrument = exchange_factory()
    trade = order(instrument, calendar[10], requested)
    exchange.deal_order(trade, position=position)
    assert trade.deal_amount == expected
    assert position.get_stock_amount(instrument) == 150. - expected


@pytest.mark.parametrize("direction,field", [
    (Order.BUY, "buyable"), (Order.SELL, "sellable"),
    (Order.BUY, "tradable"), (Order.SELL, "tradable"),
])
def test_directional_restrictions_do_not_charge_or_change_holdings(
        exchange_factory, calendar, direction, field):
    exchange, position, instrument = exchange_factory(cash=50000.)
    exchange.panel[field] = True
    exchange.panel.loc[(calendar[10], instrument), field] = False
    trade = order(instrument, calendar[10], 100., direction)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert amount == fee == trade.deal_amount == 0.
    assert position.get_cash() == 50000.
    assert position.get_stock_amount(instrument) == 150.
    expected = "source_not_tradable" if field == "tradable" else ("source_buy_blocked" if field == "buyable" else "source_sell_blocked")
    assert exchange.audit[-1]["reason"] == expected


def test_missing_quote_preserves_holding_and_recovers_next_day(exchange_factory, calendar):
    exchange, position, instrument = exchange_factory()
    exchange.panel = exchange.panel.drop((calendar[10], instrument))
    failed = order(instrument, calendar[10], 150.)
    amount, fee, _ = exchange.deal_order(failed, position=position)
    assert amount == fee == failed.deal_amount == 0.
    assert position.get_stock_amount(instrument) == 150.
    assert position.get_cash() == 100.
    assert exchange.get_close(instrument, calendar[10], calendar[10]) == 10.
    assert exchange.audit[-1]["reference_price"] is None
    assert exchange.audit[-1]["reason"] == "no_usable_reference_quote"
    recovered = order(instrument, calendar[11], 150.)
    exchange.deal_order(recovered, position=position)
    assert recovered.deal_amount == 150.
    assert position.get_stock_amount(instrument) == 0.


def test_opposite_sides_share_exact_currency_allowance(exchange_factory, calendar):
    exchange, position, instrument = exchange_factory(shares=.05, cash=50000., capacity=2000.)
    buy = order(instrument, calendar[10], 100., Order.BUY)
    exchange.deal_order(buy, position=position)
    sell = order(instrument, calendar[10], 100.05, Order.SELL)
    exchange.deal_order(sell, position=position, dealt_order_amount={instrument: 100.})
    assert sell.deal_amount == 100.
    assert position.get_stock_amount(instrument) == pytest.approx(.05)
    assert sum(row["amount"] for row in exchange.audit) <= 2000. + 1e-8


@pytest.mark.parametrize("cash,minimum,rate,requested,expected", [
    (1004., 5., .003, 100., 0.),
    (1005.3, 5., .003, 100., 100.),
    (1005.299, 5., .003, 100., 0.),
    (2100., 50., .003, 300., 200.),
    (1100., 5., 0., 300., 100.),
    (1100., 0., 0., 300., 100.),
])
def test_buy_cash_minimum_fee_and_zero_proportional_commission(
        exchange_factory, calendar, cash, minimum, rate, requested, expected):
    exchange, position, instrument = exchange_factory(
        cash=cash, minimum_commission=minimum, commission_rate=rate)
    trade = order(instrument, calendar[10], requested, Order.BUY)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert trade.deal_amount == expected
    assert position.get_stock_amount(instrument) == 150. + expected
    assert position.get_cash() == pytest.approx(cash - amount - fee)
    assert fee == pytest.approx(max(amount * rate, minimum) if expected else 0.)
    assert position.get_cash() >= -1e-8


def test_fresh_caller_fill_dictionary_cannot_reset_currency_allowance(exchange_factory, calendar):
    exchange, position, instrument = exchange_factory(capacity=125 * 9.997)
    exchange.deal_order(order(instrument, calendar[10], 150.), position=position)
    retried = order(instrument, calendar[10], 50.)
    exchange.deal_order(retried, position=position, dealt_order_amount={})
    assert retried.deal_amount == 0.
    assert position.get_stock_amount(instrument) == 50.
    assert sum(row["amount"] for row in exchange.audit) <= 125 * 9.997


def test_unknown_prior_fills_fail_before_mutating_cash_or_shares(exchange_factory, calendar):
    from etf_ml.errors import QualityError
    exchange, position, instrument = exchange_factory()
    with pytest.raises(QualityError, match="currency execution history"):
        exchange.deal_order(order(instrument, calendar[10], 150.), position=position,
                            dealt_order_amount={instrument: 100.})
    assert position.get_cash() == 100.
    assert position.get_stock_amount(instrument) == 150.
    assert not exchange.audit


@pytest.mark.parametrize("direction,price", [(Order.BUY, 10.003), (Order.SELL, 9.997)])
def test_participation_just_below_one_lot_cannot_round_up(
        exchange_factory, calendar, direction, price):
    exchange, position, instrument = exchange_factory(cash=50000., capacity=99.99999 * price)
    trade = order(instrument, calendar[10], 100., direction)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert amount == fee == trade.deal_amount == 0.
    assert position.get_stock_amount(instrument) == 150.
    assert position.get_cash() == 50000.


@pytest.mark.parametrize("direction,field,state", [
    (Order.BUY, "buyable", 0.), (Order.SELL, "sellable", 0.),
    (Order.BUY, "buyable", np.nan), (Order.SELL, "sellable", np.nan),
    (Order.BUY, "suspended", 1.), (Order.SELL, "suspended", 1.),
])
def test_source_states_block_actual_qlib_execution_after_normalization(
        exchange_factory, source_spec, calendar, direction, field, state):
    from etf_ml.data.source import QlibBinSource
    from etf_ml.data.normalize import normalize

    raw = QlibBinSource(source_spec.source).read(source_spec.fields)
    raw[field] = 0. if field == "suspended" else 1.
    instrument = "510300.SH"
    key = (calendar[10], instrument)
    raw.loc[key, field] = state
    exchange, position, _ = exchange_factory(cash=50000.)
    exchange.panel = normalize(raw, source_spec)
    trade = order(instrument, calendar[10], 100., direction)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert amount == fee == trade.deal_amount == 0.
    assert position.get_cash() == 50000.
    assert position.get_stock_amount(instrument) == 150.
    assert exchange.replay.cash == 50000.
    assert exchange.replay.shares[instrument] == 150.
    opposite = Order.SELL if direction == Order.BUY else Order.BUY
    assert exchange.is_stock_tradable(instrument, calendar[10], calendar[10], opposite) == (field != "suspended")
    assert exchange.is_stock_tradable(instrument, calendar[11], calendar[11], direction)


@pytest.mark.parametrize("direction", [Order.BUY, Order.SELL])
@pytest.mark.parametrize("field,value,expected", [
    ("quoted", False, "no_quote"),
    ("volume_shares", 0., "zero_volume"),
    ("volume_shares", np.nan, "unknown_volume"),
    ("suspended", 1., "suspended"),
    ("suspended", np.nan, "unknown_suspension_state"),
])
def test_actual_qlib_blocking_reason_matches_unchanged_cash_and_shares(exchange_factory, calendar, direction, field, value, expected):
    exchange, position, instrument = exchange_factory(cash=1e6)
    if field == "suspended":
        exchange.panel[field] = 0.
    key = (calendar[10], instrument)
    exchange.panel.loc[key, field] = value
    cash = position.get_cash()
    shares = position.get_stock_amount(instrument)
    trade = order(instrument, calendar[10], 100., direction)
    amount, fee, _ = exchange.deal_order(trade, position=position)
    assert amount == fee == trade.deal_amount == 0.
    assert position.get_cash() == exchange.replay.cash == cash
    assert position.get_stock_amount(instrument) == exchange.replay.shares[instrument] == shares
    assert exchange.audit[-1]["reason"] == expected
    assert exchange.audit[-1]["status"] == "unfilled"
    assert exchange.is_stock_tradable(instrument, calendar[11], calendar[11], direction)


@pytest.mark.parametrize("direction,opening", [(Order.BUY,9.999), (Order.SELL,8.001)])
def test_slippage_cannot_execute_outside_daily_price_limits(exchange_factory,calendar,direction,opening):
    exchange,position,instrument = exchange_factory(cash=1e6)
    exchange.panel["up_limit"],exchange.panel["down_limit"] = 10.,8.
    exchange.panel.loc[(calendar[10],instrument),"raw_open"] = opening
    cash,shares = position.get_cash(),position.get_stock_amount(instrument)
    trade = order(instrument,calendar[10],100.,direction)
    amount,fee,_ = exchange.deal_order(trade,position=position)
    assert amount == fee == trade.deal_amount == 0.
    assert position.get_cash() == exchange.replay.cash == cash
    assert position.get_stock_amount(instrument) == exchange.replay.shares[instrument] == shares
    assert exchange.audit[-1]["reason"] == "execution_price_outside_limits"
