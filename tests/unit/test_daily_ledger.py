import copy

import numpy as np
import pandas as pd
import pytest
from qlib.backtest.position import Position

from etf_ml.backtest.accounting import reconcile_daily_positions
from etf_ml.contracts import PortfolioPolicy
from etf_ml.errors import QualityError


@pytest.fixture
def manual_daily_ledger():
    days = pd.bdate_range('2023-01-02', periods=3, name='datetime')
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=0,
        liquidity_mode='participation', risk_mode='max_drawdown')
    cash1 = 498996.6991  # 500000 - 1000.3 - 3.0009
    cash2 = cash1 + 50.
    value = cash1 + 1000.
    positions = {
        days[0]: Position(cash=cash1, position_dict={'ETF': {'amount': 100., 'price': 10.}}),
        days[1]: Position(cash=cash2, position_dict={'ETF': {'amount': 200., 'price': 4.75}}),
    }
    panel = pd.DataFrame({'raw_close': [10., np.nan, 1000000.]},
        index=pd.MultiIndex.from_product([days, ['ETF']], names=['datetime', 'instrument']))
    events = pd.DataFrame([{'event_id': 'combined', 'datetime': days[1], 'instrument': 'ETF',
                            'cash_per_share': .5, 'share_multiplier': 2., 'sequence': 0}])
    trades = pd.DataFrame([{'datetime': days[0], 'instrument': 'ETF', 'direction': 1,
        'filled_shares': 100., 'amount': 1000.3, 'commission': 3.0009, 'price': 10.003, 'reference_price': 10.}])
    returns = pd.Series([value / 500000 - 1, 0.], index=days[:2], name='net_return')
    return positions, trades, events, panel, policy, returns


def test_daily_ledger_replays_manual_cash_and_ignores_future_price(manual_daily_ledger):
    positions, trades, events, panel, policy, returns = manual_daily_ledger
    result = reconcile_daily_positions(*manual_daily_ledger)
    assert result.independent_cash.tolist() == pytest.approx([498996.6991, 499046.6991])
    assert result.independent_equity.tolist() == pytest.approx([499996.6991, 499996.6991])
    assert result.stale_valuation_instruments.tolist() == [0, 1]
    assert result.equity_residual.abs().max() < 1e-6
    assert result.return_residual.abs().max() < 1e-10
    assert result.index.equals(returns.index)
    assert trades.commission.iloc[0] == 3.0009 and positions[returns.index[1]].get_stock_amount('ETF') == 200.


@pytest.mark.parametrize('mutation', ['cash', 'shares', 'valuation', 'return', 'commission', 'notional',
    'slippage', 'oversell', 'fill_after_range', 'missing_position', 'infinite_return'])
def test_daily_ledger_rejects_financial_or_date_mismatch(manual_daily_ledger, mutation):
    positions, trades, events, panel, policy, returns = copy.deepcopy(manual_daily_ledger)
    last = returns.index[-1]
    if mutation == 'cash': positions[last].position['cash'] += 100.
    elif mutation == 'shares': positions[last].position['ETF']['amount'] += 100.
    elif mutation == 'valuation': positions[last].position['ETF']['price'] += 1.
    elif mutation == 'return': returns.iloc[-1] = .01
    elif mutation == 'commission': trades.loc[0, 'commission'] = .3
    elif mutation == 'notional': trades.loc[0, 'amount'] = 1000.
    elif mutation == 'slippage': trades.loc[0, 'price'] = 10.
    elif mutation == 'oversell': trades.loc[0, 'direction'] = 0; trades.loc[0, 'price'] = 9.997; trades.loc[0, 'amount'] = 999.7; trades.loc[0, 'commission'] = 2.9991
    elif mutation == 'fill_after_range': trades.loc[0, 'datetime'] = panel.index.get_level_values('datetime')[-1]
    elif mutation == 'missing_position': positions.pop(last)
    else: returns.iloc[-1] = np.inf
    with pytest.raises(QualityError):
        reconcile_daily_positions(positions, trades, events, panel, policy, returns)


@pytest.mark.parametrize('rounding,quantity',[('ceil',253),('floor',252)])
def test_manual_odd_record_shares_split_then_cash_preserves_exact_distribution(rounding,quantity):
    from etf_ml.backtest.position import ETFPosition
    from etf_ml.data.actions import validate_events
    days=pd.bdate_range('2023-01-02',periods=4,name='datetime')
    policy=PortfolioPolicy(k_mode='count',k=1,commission_rate=.0003,minimum_commission=0,
        liquidity_mode='participation',risk_mode='max_drawdown')
    initial_cash=498999.39991  # 500000 - 1000.3 - .30009
    post_mark=10/1.01/2.5-.1292
    claim=quantity*.1292
    prices=[10.,10/1.01,post_mark,post_mark]
    positions={day:ETFPosition(cash=initial_cash+(claim if i==3 else 0),
        position_dict={'ETF':{'amount':[100,101,quantity,quantity][i],'price':prices[i]}})
        for i,day in enumerate(days)}
    positions[days[2]].dividend_receivables={'cash':{'amount':claim,'pay_date':str(days[3])}}
    events=pd.DataFrame([
        {'event_id':'odd','datetime':days[1],'instrument':'ETF','cash_per_share':0.,'share_multiplier':1.01,'sequence':0},
        {'event_id':'split','datetime':days[2],'instrument':'ETF','cash_per_share':0.,'share_multiplier':2.5,
         'sequence':0,'record_date':days[1],'share_rounding':rounding},
        {'event_id':'cash','datetime':days[2],'instrument':'ETF','cash_per_share':.1292,'share_multiplier':1.,
         'sequence':1,'record_date':days[1],'pay_date':days[3],
         'cash_share_basis':'post_record_conversions','cash_basis_evidence_id':'issuer-notice'}])
    events,_=validate_events(events,days,['ETF'])
    panel=pd.DataFrame({'raw_close':prices},index=pd.MultiIndex.from_product([days,['ETF']],names=['datetime','instrument']))
    trades=pd.DataFrame([{'datetime':days[0],'instrument':'ETF','direction':1,'filled_shares':100.,
        'amount':1000.3,'commission':.30009,'price':10.003,'reference_price':10.}])
    equity=[initial_cash+1000,initial_cash+1000,initial_cash+quantity*10/1.01/2.5,
        initial_cash+quantity*10/1.01/2.5]
    returns=pd.Series([equity[0]/500000-1,equity[1]/equity[0]-1,equity[2]/equity[1]-1,0.],index=days)
    result=reconcile_daily_positions(positions,trades,events,panel,policy,returns)
    assert result.loc[days[2],'independent_receivable']==pytest.approx(claim)
    assert result.loc[days[3],'independent_cash']==pytest.approx(initial_cash+claim)
    positions[days[2]].dividend_receivables['cash']['amount']=101*.1292
    with pytest.raises(QualityError,match='disagrees'):
        reconcile_daily_positions(positions,trades,events,panel,policy,returns)
