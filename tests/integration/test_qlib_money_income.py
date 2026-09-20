"""Actual Qlib execution with synthetic, explicitly governed monetary income."""
from decimal import Decimal,ROUND_DOWN
import numpy as np
import pandas as pd
import pytest
from etf_ml.backtest.income import IncomeRule
from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.backtest.results import persist_result
from etf_ml.contracts import PortfolioPolicy
from etf_ml.data.money_income import money_income_intervals
from etf_ml.data.source import encode_provider

pytestmark=pytest.mark.qlib


@pytest.mark.parametrize('negative',[False,True])
def test_actual_qlib_income_calendar_fills_and_cash_reconcile_independently(panel,calendar,source_spec,tmp_path,negative):
    money='511960.SH';other='510500.SH'
    frame=panel.reset_index();frame.loc[frame.instrument.eq('510300.SH'),'instrument']=money
    frame=frame.set_index(['datetime','instrument']).sort_index()
    for column in ('raw_open','raw_close','raw_high','raw_low','reference_close','adj_open','adj_close'):
        frame[column]=100.
    frame['amount_currency']=1e9
    raw=pd.DataFrame(index=frame.index)
    for column in ('open','high','low','close'):
        raw[column]=frame['raw_'+column]
    raw['volume']=frame.volume_shares;raw['amount']=frame.amount_currency;raw['factor']=1.
    raw['change']=0.;raw['pre_close']=100.
    provider=tmp_path/'money_provider'
    future=pd.bdate_range(calendar[0],calendar[-1]+pd.offsets.MonthEnd(2),name='datetime')
    encode_provider(raw,calendar,provider,{c:c for c in raw},future_calendar=future)
    universe=pd.DataFrame({'eligible':True,'buyable':True,'sellable':True,
        'tracking_group':[i for _,i in frame.index]},index=frame.index)
    scores=pd.Series(0.,index=frame.index,name='score')
    scores.loc[(slice(None),money)]=2.
    switch=pd.Timestamp('2023-01-30' if negative else '2023-02-14')
    scores.loc[(slice(switch,None),other)]=3.
    start,end=calendar[1],calendar[45]
    days=pd.date_range(start,end,freq='D');amount='-1.0' if negative else '.4'
    income=money_income_intervals(pd.DataFrame({'FSRQ':days.strftime('%Y-%m-%d'),
        'SDATE':'','NAVTYPE':'1','DWJZ':amount}),money,basis_shares=100,par_value=100)
    income['available_time']=income.period_end+pd.Timedelta(days=1,hours=8)
    rule=IncomeRule('synthetic_exact' if negative else 'synthetic_truncation_proxy',
        compound_unpaid=not negative,convert_at_par=not negative,
        rounding='exact' if negative else 'truncate_cent')
    policy=PortfolioPolicy(k_mode='count',k=1,minimum_commission=0,commission_rate=.0003,
        slippage_rate=.0003,liquidity_lookback=1)
    result=evaluate(scores,policy,frame,universe=universe,calendar=calendar,
        benchmark=pd.read_parquet(source_spec.benchmark_path),provider=provider,
        recorder_uri=tmp_path/'records',start_time=start,end_time=end,
        income=income,income_rules={money:rule},allow_income_rounding_proxy=not negative)
    # Advance an independent calendar-day account from quantities and fixed
    # prices/fees. Never use Qlib returns, balances or conversion postings.
    cash=Decimal(500000);holdings={};balance=Decimal(0);calendar_income=Decimal(amount)
    for day in days:
        for trade in result.trades[result.trades.datetime.eq(day)].itertuples():
            q=Decimal(str(trade.filled_shares))
            if not q:continue
            old=holdings.get(trade.instrument,Decimal(0));buy=trade.direction==1
            price=Decimal(100)*(Decimal(1)+(Decimal('.0003') if buy else Decimal('-.0003')))
            notional=q*price;fee=notional*Decimal('.0003')
            assert trade.amount==pytest.approx(float(notional)) and trade.commission==pytest.approx(float(fee))
            cash+=(-notional if buy else notional)-fee
            new=old+(q if buy else -q);holdings[trade.instrument]=new
            if not buy and trade.instrument==money and new==0:
                cash+=balance;balance=Decimal(0)
        q=holdings.get(money,Decimal(0))
        effective=q+(balance/Decimal(100) if not negative else Decimal(0))
        earned=(effective*calendar_income/Decimal(100)).quantize(Decimal('.01'),rounding=ROUND_DOWN)
        balance+=earned
        if not negative and balance>=100:
            converted=int(balance//100);holdings[money]=q+converted;balance-=Decimal(converted)*100
        if day in result.positions:
            position=result.positions[day]
            assert position.get_cash()==pytest.approx(float(cash),abs=1e-6)
            assert position.get_stock_amount(money)==pytest.approx(float(holdings.get(money,Decimal(0))))
            assert position.income_book.value()==pytest.approx(float(balance))
            expected=cash+sum(holdings.values(),Decimal(0))*100+balance
            assert position.calculate_value()==pytest.approx(float(expected),abs=1e-6)
            assert result.ledger.loc[day,'independent_income']==pytest.approx(float(balance))
    assert result.metrics['accounting_reconciled'] and result.metrics['max_income_residual']<1e-8
    assert result.metrics['max_return_residual']<1e-10
    assert result.metrics['income_validation']['rounding_proxy']==(not negative)
    if not negative:
        assert result.income_postings.converted_shares.sum()>0
        # First buy Friday Jan13: six 19.60 calendar-day allocations trigger
        # one 100-yuan share conversion on Wednesday Jan18, including weekend.
        assert result.positions[pd.Timestamp('2023-01-18')].get_stock_amount(money)==4901
    else:
        assert result.exposures.income_weight.min()<0
    settlements=result.income_postings[result.income_postings.kind.eq('full_sale_settlement')]
    assert len(settlements)==1 and (settlements.cash_settled.iloc[0]<0)==negative
    assert result.positions[end].income_book.value()==0 and result.positions[end].get_stock_amount(money)==0
    assert not ((result.trades.datetime>switch)&result.trades.instrument.eq(money)&result.trades.direction.eq(1)&result.trades.filled_shares.gt(0)).any()
    for decision in result.decisions:
        assert sum(decision['weights'].values())+decision['cash_weight']+decision['receivable_weight']+decision['income_weight']==pytest.approx(1.)
    persist_result(result,tmp_path/'published')
    balances=pd.read_parquet(tmp_path/'published'/'income_balances.parquet')
    postings=pd.read_parquet(tmp_path/'published'/'income_postings.parquet')
    assert not balances.empty and len(postings)==len(result.income_postings)
    assert len(postings[postings.kind.eq('full_sale_settlement')])==1
