import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.contracts import PortfolioPolicy, UniversePolicy
from etf_ml.data.universe import build_history

pytestmark = pytest.mark.qlib


@pytest.mark.parametrize('payment_index', [15, 30, 80])
def test_actual_qlib_books_dividend_split_and_keeps_retired_untradable_holding(
        panel, calendar, metadata, source_spec, tmp_path, payment_index):
    first, second = "510300.SH", "510500.SH"
    frame = panel.copy()
    frame["amount_currency"] = 1e9
    dividend_date, split_date = calendar[15], calendar[25]
    for column in ("raw_open", "raw_close", "raw_high", "raw_low", "reference_close"):
        frame.loc[(slice(None), first), column] = 10.
        frame.loc[(slice(dividend_date, None), first), column] = 9.5
        frame.loc[(slice(split_date, None), first), column] = 9.5 / 1.5
    frame.loc[(slice(None), first), "adj_close"] = 10.
    frame.loc[(slice(None), first), "adj_open"] = 10.
    events = pd.DataFrame([
        {"event_id": "cash-distribution", "datetime": dividend_date, "instrument": first,
         "cash_per_share": .5, "share_multiplier": 1.},
        {"event_id": "share-split", "datetime": split_date, "instrument": first,
         "cash_per_share": 0., "share_multiplier": 1.5},
    ])
    # Mixed source tables may omit cash-only dates for a split row.
    events['pay_date'] = [calendar[payment_index], pd.NaT]
    events['record_date'] = [calendar[14], pd.NaT]
    universe = build_history(frame, metadata, calendar,
                             UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    retired = pd.Timestamp("2023-02-14")
    universe.loc[(slice(retired, None), first), "eligible"] = False
    universe.loc[(slice(retired, None), first), "buyable"] = False
    frame.loc[(slice(retired, None), first), "tradable"] = False
    scores = pd.Series(0., index=frame.index, name="score")
    scores.loc[(slice(None), first)] = 2.
    scores.loc[(slice(retired, None), second)] = 3.
    policy = PortfolioPolicy(k_mode="count", k=1, minimum_commission=5,
                             liquidity_mode="participation", risk_mode="max_drawdown",
                             liquidity_lookback=1)
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
                      benchmark=pd.read_parquet(source_spec.benchmark_path),
                      provider=source_spec.source, recorder_uri=tmp_path / "recorders",
                      start_time=calendar[1], end_time=calendar[60], events=events)
    assert result.metrics["accounting_reconciled"]
    before_div = result.positions[calendar[14]]
    after_div = result.positions[dividend_date]
    amount = before_div.get_stock_amount(first)
    assert amount > 0
    claim = amount * .5
    assert after_div.get_cash() == pytest.approx(before_div.get_cash() + (claim if payment_index == 15 else 0.))
    assert after_div.receivable_value() == pytest.approx(claim if payment_index > 15 else 0.)
    assert result.ledger.loc[dividend_date, 'independent_receivable'] == pytest.approx(after_div.receivable_value())
    assert result.metrics['max_receivable_residual'] < 1e-6
    if payment_index == 30:
        assert result.positions[calendar[29]].receivable_value() == pytest.approx(claim)
        assert result.positions[calendar[30]].get_cash() == pytest.approx(result.positions[calendar[29]].get_cash() + claim)
        assert result.positions[calendar[30]].receivable_value() == 0.
        assert result.daily_returns.loc[calendar[30]] == pytest.approx(0., abs=1e-10)
    elif payment_index == 80:
        assert result.positions[calendar[60]].receivable_value() == pytest.approx(claim)
    from etf_ml.backtest.results import persist_result
    persist_result(result, tmp_path / 'portfolio')
    published = pd.read_parquet(tmp_path / 'portfolio' / 'receivables.parquet')
    assert published.empty == (payment_index == 15)
    if payment_index > 15:
        assert published.amount.unique() == pytest.approx([claim])
    assert after_div.get_stock_amount(first) == amount
    assert result.daily_returns.loc[dividend_date] == pytest.approx(0., abs=1e-10)
    before_split = result.positions[calendar[24]]
    after_split = result.positions[split_date]
    assert after_split.get_stock_amount(first) == pytest.approx(before_split.get_stock_amount(first) * 1.5)
    assert after_split.get_cash() == pytest.approx(before_split.get_cash())
    assert result.daily_returns.loc[split_date] == pytest.approx(0., abs=1e-10)
    locked = result.positions[calendar[31]].get_stock_amount(first)
    assert locked > 0
    assert result.positions[calendar[60]].get_stock_amount(first) == locked
    assert not ((result.trades.instrument == first) &
                (result.trades.datetime >= retired) &
                (result.trades.filled_shares > 0)).any()
    assert all(np.isfinite(result.daily_returns))
    for decision in result.decisions:
        assert sum(decision['weights'].values()) + decision['cash_weight'] + decision['receivable_weight'] == pytest.approx(1.)
    if payment_index > 15:
        assert any(decision['receivable_weight'] > 0 for decision in result.decisions)


def test_actual_rotation_liquidates_fractional_split_shares(
        panel, calendar, metadata, source_spec, tmp_path):
    first, second = "510300.SH", "510500.SH"
    frame = panel.copy()
    frame["amount_currency"] = 1e9
    split_date = calendar[25]
    multiplier = 1.0033
    for column in ("raw_open", "raw_close", "raw_high", "raw_low", "reference_close"):
        frame.loc[(slice(None), first), column] = 10.
        frame.loc[(slice(split_date, None), first), column] = 10. / multiplier
    frame.loc[(slice(None), first), "adj_open"] = 10.
    frame.loc[(slice(None), first), "adj_close"] = 10.
    universe = build_history(frame, metadata, calendar,
                             UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    switch_date = pd.Timestamp("2023-02-14")
    universe.loc[(slice(switch_date, None), first), "eligible"] = False
    scores = pd.Series(0., index=frame.index, name="score")
    scores.loc[(slice(None), first)] = 2.
    scores.loc[(slice(switch_date, None), second)] = 3.
    events = pd.DataFrame([{"event_id": "fractional-split", "datetime": split_date,
                           "instrument": first, "cash_per_share": 0.,
                           "share_multiplier": multiplier}])
    policy = PortfolioPolicy(k_mode="count", k=1, minimum_commission=5,
                             liquidity_mode="participation", risk_mode="max_drawdown",
                             liquidity_lookback=1)
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
                      benchmark=pd.read_parquet(source_spec.benchmark_path),
                      provider=source_spec.source, recorder_uri=tmp_path / "fractional-recorders",
                      start_time=calendar[1], end_time=calendar[40], events=events)
    quantity = result.positions[pd.Timestamp("2023-02-14")].get_stock_amount(first)
    assert quantity > 0 and not np.isclose(quantity % policy.lot_size, 0.)
    liquidation = result.trades[(result.trades.instrument == first) &
                               (result.trades.datetime == pd.Timestamp("2023-02-15")) &
                               (result.trades.direction == 0)]
    assert len(liquidation) == 1
    assert liquidation.iloc[0].requested_shares == pytest.approx(quantity)
    assert liquidation.iloc[0].filled_shares == pytest.approx(quantity)
    assert liquidation.iloc[0].status == "filled"
    assert result.positions[pd.Timestamp("2023-02-15")].get_stock_amount(first) == 0.
    assert result.positions[calendar[40]].get_stock_amount(first) == 0.
    assert result.metrics["accounting_reconciled"]
    assert result.daily_returns.loc[split_date] == pytest.approx(0., abs=1e-10)


@pytest.mark.parametrize("rounding", ["ceil", "floor", "none"])
def test_actual_qlib_share_merger_rounding_reconciles_account_grant(
        panel, calendar, metadata, source_spec, tmp_path, rounding):
    import math

    first = "510300.SH"
    effective = calendar[25]
    multiplier = .49977589
    frame = panel.copy()
    frame["amount_currency"] = 1e9
    for column in ("raw_open", "raw_close", "raw_high", "raw_low", "reference_close"):
        frame.loc[(slice(None), first), column] = 10.
        frame.loc[(slice(effective, None), first), column] = 10. / multiplier
    events = pd.DataFrame([{"event_id":"merge", "instrument":first,"datetime":effective,
                            "cash_per_share":0.,"share_multiplier":multiplier,"share_rounding":rounding}])
    universe = build_history(frame, metadata, calendar, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    scores = pd.Series([5. if i == first else 0. for _, i in frame.index], index=frame.index)
    policy = PortfolioPolicy(k_mode="count", k=1, minimum_commission=0., commission_rate=0.,
                             slippage_rate=0., liquidity_lookback=1)
    result = evaluate(scores, policy, frame, universe=universe, calendar=calendar,
                      benchmark=pd.read_parquet(source_spec.benchmark_path),
                      provider=source_spec.source, recorder_uri=tmp_path / "merger_recorders",
                      start_time=calendar[1], end_time=calendar[30], events=events)
    old = result.positions[calendar[24]]
    new = result.positions[effective]
    held = old.get_stock_amount(first)
    assert held > 0
    exact = held * multiplier
    expected = math.ceil(exact) if rounding == "ceil" else math.floor(exact) if rounding == "floor" else exact
    assert new.get_stock_amount(first) == pytest.approx(expected)
    assert new.get_cash() == pytest.approx(old.get_cash())
    before = old.calculate_value()
    delta = (expected - exact) * 10. / multiplier
    assert new.calculate_value() == pytest.approx(before + delta)
    assert result.daily_returns.loc[effective] == pytest.approx(delta / before)
    assert result.metrics["accounting_reconciled"]
    assert result.ledger["maximum_share_residual"].max() < 1e-8


@pytest.mark.parametrize('rounding',['ceil','floor','exact'])
def test_actual_qlib_dividend_on_rounded_split_record_holdings(panel,calendar,metadata,source_spec,tmp_path,rounding):
    import math
    first='510300.SH'
    frame=panel.copy()
    frame['amount_currency']=1e9
    effective=calendar[25]
    multiplier=2.5
    cash=.1292
    for column in ('raw_open','raw_close','raw_high','raw_low','reference_close'):
        frame.loc[(slice(None),first),column]=10.
        frame.loc[(slice(effective,None),first),column]=10./multiplier-cash
    events=pd.DataFrame([
        {'event_id':'split','instrument':first,'datetime':effective,'record_date':calendar[24],
         'cash_per_share':0.,'share_multiplier':multiplier,'sequence':0,'share_rounding':rounding},
        {'event_id':'cash','instrument':first,'datetime':effective,'record_date':calendar[24],
         'cash_per_share':cash,'share_multiplier':1.,'sequence':1,'pay_date':calendar[26],
         'cash_share_basis':'post_record_conversions','cash_basis_evidence_id':'issuer-notice'}])
    universe=build_history(frame,metadata,calendar,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    scores=pd.Series(0.,index=frame.index,name='score')
    scores.loc[(slice(None),first)]=2.
    policy=PortfolioPolicy(k_mode='count',k=1,commission_rate=.0003,minimum_commission=0,
        liquidity_mode='participation',risk_mode='max_drawdown',liquidity_lookback=1)
    result=evaluate(scores,policy,frame,universe=universe,calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path),provider=source_spec.source,
        recorder_uri=tmp_path/'post-split-recorders',start_time=calendar[1],end_time=calendar[40],events=events)
    before=result.positions[calendar[24]]
    quantity=before.get_stock_amount(first)
    expected=(quantity*multiplier if rounding=='exact' else (math.ceil if rounding=='ceil' else math.floor)(quantity*multiplier))
    assert quantity>0 and result.positions[effective].get_stock_amount(first)==expected
    claim=expected*cash
    assert result.positions[effective].receivable_value()==pytest.approx(claim)
    assert result.positions[calendar[26]].get_cash()==pytest.approx(before.get_cash()+claim)
    assert result.positions[calendar[26]].receivable_value()==0
    assert result.metrics['accounting_reconciled']
    assert result.entitlements.iloc[0].entitled_shares==expected
    assert result.entitlements.iloc[0].cash_per_ex_share==cash
    assert result.entitlements.iloc[0].basis=='post_record_conversions'
