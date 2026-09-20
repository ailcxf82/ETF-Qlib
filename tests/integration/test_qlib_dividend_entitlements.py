import pandas as pd
import pytest

from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.backtest.results import persist_result
from etf_ml.contracts import PortfolioPolicy, UniversePolicy
from etf_ml.data.universe import build_history

pytestmark = pytest.mark.qlib


@pytest.mark.parametrize('action', ['hold', 'sell_before_ex', 'buy_after_record'])
def test_actual_qlib_cash_rights_follow_record_close_after_split_and_rotation(
        panel, calendar, metadata, source_spec, tmp_path, action):
    first, second = '510300.SH', '510500.SH'
    frame = panel.copy()
    frame['amount_currency'] = 1e9
    record, split, ex, pay = calendar[14], calendar[18], calendar[25], calendar[35]
    for field in ['raw_open', 'raw_close', 'raw_high', 'raw_low', 'reference_close']:
        frame.loc[(slice(None), first), field] = 10.
        frame.loc[(slice(split, None), first), field] = 5.
        frame.loc[(slice(ex, None), first), field] = 4.75
        frame.loc[(slice(None), second), field] = 10.
    universe = build_history(frame, metadata, calendar,
        UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    scores = pd.Series(0., index=frame.index, name='score')
    initial = second if action == 'buy_after_record' else first
    scores.loc[(slice(None), initial)] = 2.
    if action != 'hold':
        final = first if action == 'buy_after_record' else second
        scores.loc[(slice(calendar[20], None), initial)] = 0.
        scores.loc[(slice(calendar[20], None), final)] = 3.
    events = pd.DataFrame([
        {'event_id': 'split', 'datetime': split, 'instrument': first,
         'cash_per_share': 0., 'share_multiplier': 2., 'record_date': pd.NaT, 'pay_date': pd.NaT},
        {'event_id': 'dividend', 'datetime': ex, 'instrument': first,
         'cash_per_share': .5, 'share_multiplier': 1., 'record_date': record, 'pay_date': pay}])
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=0,
                             liquidity_mode='participation', risk_mode='max_drawdown', liquidity_lookback=1)
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path), provider=source_spec.source,
        recorder_uri=tmp_path / 'recorders', start_time=calendar[1], end_time=calendar[40], events=events)
    registered = result.positions[record].get_stock_amount(first)
    current = result.positions[calendar[24]].get_stock_amount(first)
    expected = registered * .5
    before = result.positions[calendar[24]]
    after = result.positions[ex]
    assert result.metrics['daily_accounting_reconciled'] and result.metrics['max_receivable_residual'] < 1e-6
    assert after.get_cash() == pytest.approx(before.get_cash())
    assert after.receivable_value() == pytest.approx(expected)
    assert result.ledger.loc[ex, 'independent_receivable'] == pytest.approx(expected)
    row = result.entitlements.iloc[0]
    assert row['entitled_shares'] == registered and row['ex_date_shares'] == current
    assert row['cash_per_record_share'] == .5 and row['cash_per_ex_share'] == .25
    assert row['distribution_amount'] == expected and row['basis'] == 'record_day_close'
    if action == 'hold':
        assert registered > 0 and current == pytest.approx(registered * 2)
        assert result.daily_returns.loc[ex] == pytest.approx(0., abs=1e-10)
    elif action == 'sell_before_ex':
        assert registered > 0 and current == 0.
        assert result.daily_returns.loc[ex] == pytest.approx(expected / before.calculate_value())
    else:
        assert registered == 0. and current > 0
        assert result.daily_returns.loc[ex] == pytest.approx(-current * .25 / before.calculate_value())
    assert result.positions[pay].receivable_value() == 0.
    assert result.daily_returns.loc[pay] == pytest.approx(0., abs=1e-10)
    persist_result(result, tmp_path / 'portfolio')
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / 'portfolio' / 'entitlements.parquet'),
                                  result.entitlements, check_like=True)
