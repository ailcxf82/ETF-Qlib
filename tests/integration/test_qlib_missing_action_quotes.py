import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.backtest.results import persist_result
from etf_ml.contracts import PortfolioPolicy, UniversePolicy
from etf_ml.data.source import encode_provider
from etf_ml.data.universe import build_history

pytestmark = pytest.mark.qlib


def test_actual_qlib_maintains_value_through_unquoted_dividend_split_and_retirement(
        panel, calendar, metadata, source_spec, tmp_path):
    instrument = '510300.SH'
    dividend, split, restore, price_move = calendar[15], calendar[25], calendar[29], calendar[30]
    frame = panel.copy()
    frame.amount_currency = 1e9
    for column in ['raw_open', 'raw_close', 'raw_high', 'raw_low']:
        frame.loc[(slice(None), instrument), column] = 10.
        frame.loc[(slice(dividend, None), instrument), column] = 9.5
        frame.loc[(slice(split, None), instrument), column] = 9.5 / 1.5
        frame.loc[(slice(price_move, None), instrument), column] = 7.
        frame.loc[(slice(dividend, calendar[28]), instrument), column] = np.nan
    frame.loc[(slice(dividend, None), instrument), 'tradable'] = False
    frame.loc[(slice(dividend, calendar[28]), instrument), 'quoted'] = False
    universe = build_history(frame, metadata, calendar, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    universe.loc[(slice(None), ['510500.SH', '159915.SZ']), ['eligible', 'buyable']] = False
    universe.loc[(slice(calendar[20], None), instrument), ['eligible', 'buyable', 'sellable']] = False
    scores = pd.Series(0., index=frame.index, name='score')
    scores.loc[(slice(None), instrument)] = 2.
    events = pd.DataFrame([
        {'event_id': 'dividend', 'datetime': dividend, 'instrument': instrument, 'cash_per_share': .5, 'share_multiplier': 1.},
        {'event_id': 'split', 'datetime': split, 'instrument': instrument, 'cash_per_share': 0., 'share_multiplier': 1.5},
    ])
    mapped = pd.DataFrame(index=frame.index)
    for field in ['open', 'high', 'low', 'close']:
        mapped[field] = frame['raw_' + field]
    mapped['factor'], mapped['change'] = 1., 0.
    mapped['volume'], mapped['amount'] = frame.volume_shares, frame.amount_currency
    provider = tmp_path / 'actual-unquoted-provider'
    encode_provider(mapped, calendar, provider, {column: column for column in mapped})
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=5,
        liquidity_mode='participation', risk_mode='max_drawdown', liquidity_lookback=1)
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path), provider=provider,
        recorder_uri=tmp_path / 'recorders', start_time=calendar[1], end_time=calendar[40], events=events)
    before = result.positions[calendar[14]]
    quantity = before.get_stock_amount(instrument)
    assert quantity > 0
    assert result.positions[dividend].get_stock_price(instrument) == pytest.approx(9.5)
    assert result.positions[dividend].get_cash() == pytest.approx(before.get_cash() + quantity * .5)
    assert result.positions[split].get_stock_amount(instrument) == pytest.approx(quantity * 1.5)
    assert result.positions[split].get_stock_price(instrument) == pytest.approx(9.5 / 1.5)
    assert result.daily_returns.loc[dividend] == pytest.approx(0., abs=1e-10)
    assert result.daily_returns.loc[split] == pytest.approx(0., abs=1e-10)
    assert result.positions[restore].calculate_value() == pytest.approx(before.calculate_value())
    assert result.positions[price_move].get_stock_price(instrument) == pytest.approx(7.)
    assert result.positions[calendar[40]].get_stock_amount(instrument) == pytest.approx(quantity * 1.5)
    assert not ((result.trades.instrument == instrument) & (result.trades.datetime >= dividend) & result.trades.filled_shares.gt(0)).any()
    assert result.metrics['daily_accounting_reconciled'] and result.metrics['daily_accounting_dates'] == 40
    assert result.metrics['stale_valuation_dates'] == 14
    assert result.ledger.index.equals(result.daily_returns.index)
    assert result.ledger.maximum_share_residual.max() < 1e-6
    assert result.ledger.equity_residual.abs().max() < 1e-6
    output = tmp_path / 'result-export'
    persist_result(result, output)
    pd.testing.assert_frame_equal(pd.read_parquet(output / 'ledger.parquet'), result.ledger)
