from decimal import Decimal
import copy
import pandas as pd
import pytest
from etf_ml.backtest.income import IncomeRule,IncomeBook,validate_income_inputs
from etf_ml.backtest.accounting import Account
from etf_ml.backtest.position import ETFPosition
from etf_ml.contracts import PortfolioPolicy
from etf_ml.data.money_income import money_income_intervals
from etf_ml.errors import QualityError
from etf_ml.portfolio.allocation import construct


def test_exact_unpaid_income_is_equity_not_cash_and_conversion_keeps_par_value():
    account=Account(1000.,shares={'511960.SH':100.},marks={'511960.SH':100.})
    account.income_book.rules={'511960.SH':IncomeRule('exact',compound_unpaid=False)}
    before=account.equity()
    account.accrue_income('2024-06-17','511960.SH',99.)
    assert account.cash==1000 and account.income_book.value()==99 and account.equity()==before+99
    account.accrue_income('2024-06-18','511960.SH',1.)
    assert account.shares['511960.SH']==101 and account.income_book.value()==0
    assert account.equity()==before+100 and account.cash==1000
    account.accrue_income('2024-06-18','511960.SH',1.)
    assert account.shares['511960.SH']==101  # last post is idempotent


def test_compound_income_uses_unpaid_cash_converted_to_share_units_not_cash_as_shares():
    book=IncomeBook({'511960.SH':IncomeRule('cent-proxy',rounding='truncate_cent')})
    book.accrue('2024-06-17','511960.SH',100.,99.)
    converted=book.accrue('2024-06-18','511960.SH',100.,1.)
    # (100 shares + 99/100 equivalent shares) * 1/100 = 1.0099,
    # with explicitly requested cent truncation -> 1.00, not 1.99.
    assert converted==1 and book.value()==0
    assert book.records[-1]['gross_income']==pytest.approx(1.0099)
    assert book.records[-1]['unallocated_rounding_residual']==pytest.approx(.0099)


def test_exact_mode_rejects_missing_cent_allocation_without_mutation():
    book=IncomeBook({'511960.SH':IncomeRule('exact')})
    before=copy.deepcopy(book)
    with pytest.raises(QualityError,match='cent-allocation'):
        book.accrue('2024-06-17','511960.SH',100.,.4228)
    assert book==before


def test_negative_income_truncates_toward_zero_and_cannot_be_filtered():
    book=IncomeBook({'511960.SH':IncomeRule('proxy',rounding='truncate_cent',convert_at_par=False)})
    book.accrue('2024-06-17','511960.SH',100.,-.0199)
    assert book.value()==-.01
    assert book.records[-1]['unallocated_rounding_residual']==pytest.approx(-.0099)


@pytest.mark.parametrize('filled,after,expected',[(0,100,0),(50,50,0),(100,0,25)])
def test_only_filled_full_sale_settles_income(filled,after,expected):
    book=IncomeBook({'511960.SH':IncomeRule('exact')},balances={'511960.SH':Decimal(25)})
    assert book.settle_sale('2024-06-17','511960.SH',before_shares=100,after_shares=after,filled_shares=filled)==expected
    assert book.value()==25-expected


def test_actual_account_fills_keep_partial_sale_income_and_full_sale_credits_cash_once():
    account=Account(1000.,shares={'511960.SH':200.},marks={'511960.SH':100.})
    account.income_book.rules={'511960.SH':IncomeRule('exact',compound_unpaid=False)}
    account.accrue_income('2024-06-17','511960.SH',10.)
    policy=PortfolioPolicy(k_mode='count',k=1,minimum_commission=0,commission_rate=0,slippage_rate=0)
    account.trade('511960.SH',-100,100.,policy,date='2024-06-18',historical_average_amount=1e9)
    assert account.cash==11000 and account.income_book.value()==20
    account.trade('511960.SH',-100,100.,policy,date='2024-06-18',historical_average_amount=1e9)
    assert account.cash==21020 and account.income_book.value()==0
    assert len([r for r in account.income_book.records if r['kind']=='full_sale_settlement'])==1


@pytest.mark.parametrize('kind',['amount','holdings','older_day'])
def test_reposting_changed_inputs_or_old_days_is_rejected(kind):
    book=IncomeBook({'511960.SH':IncomeRule('exact',compound_unpaid=False)})
    book.accrue('2024-06-17','511960.SH',100.,1.)
    with pytest.raises(QualityError):
        book.accrue('2024-06-16' if kind=='older_day' else '2024-06-17','511960.SH',
                    200. if kind=='holdings' else 100.,2. if kind=='amount' else 1.)


def input_frame():
    days=pd.date_range('2024-06-14','2024-06-17')
    raw=pd.DataFrame({'FSRQ':days.strftime('%Y-%m-%d'),'SDATE':'','NAVTYPE':'1','DWJZ':'.4'})
    frame=money_income_intervals(raw,'511960.SH',basis_shares=100,par_value=100)
    frame['available_time']=frame.period_end+pd.Timedelta(days=1,hours=8)
    return frame


@pytest.mark.parametrize('mutation',['units','gap','availability','missing_time','proxy','rules','aggregated'])
def test_income_input_gates_refuse_missing_or_future_information(mutation):
    frame=input_frame();rule=IncomeRule('exact');rules={'511960.SH':rule}
    if mutation=='units':rules['511960.SH']=IncomeRule('other',par_value=1)
    elif mutation=='gap':frame=frame.iloc[1:]
    elif mutation=='availability':frame.loc[frame.index[0],'available_time']=pd.Timestamp('2024-06-17 10:00')
    elif mutation=='missing_time':frame=frame.drop(columns='available_time')
    elif mutation=='proxy':rules['511960.SH']=IncomeRule('proxy',rounding='truncate_cent')
    elif mutation=='rules':rules={}
    else:frame.loc[frame.index[1],'period_start']=pd.Timestamp('2024-06-14');frame.loc[frame.index[1],'source_record_type']='0'
    with pytest.raises(QualityError):validate_income_inputs(frame,rules,['511960.SH'],pd.bdate_range('2024-06-14','2024-06-19'),'2024-06-14','2024-06-17')


def test_valid_daily_inputs_include_holidays_and_do_not_invent_publication_times():
    frame=input_frame();result,rules,status=validate_income_inputs(frame,{'511960.SH':IncomeRule('exact')},['511960.SH'],
        pd.bdate_range('2024-06-14','2024-06-19'),'2024-06-14','2024-06-17')
    assert len(result)==4 and status['calendar_day_coverage'] and not status['rounding_proxy']
    assert result.available_time.equals(frame.available_time)


def test_signed_income_weight_is_reserved_in_equity_budget():
    policy=PortfolioPolicy(k_mode='count',k=1,minimum_commission=0)
    scores=pd.Series({'511960.SH':1.})
    allocation=construct(scores,{}, {'buyable':{'511960.SH':True},'tracking_group':{'511960.SH':'money'},'income_weight':.1},policy)
    assert allocation.weights=={'511960.SH':.9} and allocation.income_weight==.1
    allocation=construct(scores,{'511960.SH':1.01},{'buyable':{'511960.SH':True},'sellable':{'511960.SH':True},'tracking_group':{'511960.SH':'money'},'income_weight':-.02},policy)
    assert sum(allocation.weights.values())+allocation.cash_weight+allocation.income_weight==pytest.approx(1.)


def test_native_qlib_position_partial_then_full_sale_books_only_actual_settlement():
    from qlib.backtest.decision import Order
    position=ETFPosition(cash=1000.,position_dict={'511960.SH':{'amount':200.,'price':100.}})
    position.income_book.rules={'511960.SH':IncomeRule('exact')}
    position.income_book.balances={'511960.SH':Decimal(20)}
    order=Order('511960.SH',100.,Order.SELL,pd.Timestamp('2024-06-18'),pd.Timestamp('2024-06-18 15:00'))
    position.update_order(order,10000.,0.,100.)
    assert position.get_stock_amount('511960.SH')==100 and position.get_cash()==11000 and position.income_book.value()==20
    position.update_order(order,10000.,0.,100.)
    assert position.get_stock_amount('511960.SH')==0 and position.get_cash()==21020 and position.income_book.value()==0


@pytest.mark.parametrize('mutation',['nan','fill_mismatch','closed_day','corrupt_balance'])
def test_sale_settlement_refuses_corrupt_or_inconsistent_state(mutation):
    book=IncomeBook({'511960.SH':IncomeRule('exact',compound_unpaid=False)})
    book.accrue('2024-06-17','511960.SH',100.,1.)
    if mutation=='corrupt_balance':book.balances['511960.SH']=Decimal('NaN')
    before=copy.deepcopy(book)
    with pytest.raises(QualityError):
        book.settle_sale('2024-06-17' if mutation=='closed_day' else '2024-06-18','511960.SH',
            before_shares=100,after_shares=0,filled_shares=float('nan') if mutation=='nan' else (99 if mutation=='fill_mismatch' else 100))
    assert book.records==before.records and book.last_dates==before.last_dates
