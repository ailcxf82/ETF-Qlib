import copy

import pandas as pd
import pytest
from pydantic import ValidationError

from etf_ml.backtest.accounting import Account
from etf_ml.backtest.position import ETFPosition
from etf_ml.backtest.metrics import position_exposures
from etf_ml.contracts import PortfolioPolicy
from etf_ml.data.actions import validate_events
from etf_ml.errors import QualityError
from etf_ml.operations.contracts import PaperAccount
from etf_ml.operations.daily import mark_account


def test_dividend_claim_preserves_value_is_not_spendable_and_survives_split_sale(calendar):
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    ex, pay = calendar[15], calendar[30]
    account.apply_event('dividend', '510300.SH', .5, 1., date=ex, pay_date=pay)
    assert account.cash == 0. and account.receivable_value() == 50.
    assert account.equity() == 1000.
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=0, commission_rate=0,
                             slippage_rate=0, liquidity_mode='participation', risk_mode='max_drawdown')
    trade = account.trade('510500.SH', 100., .5, policy, historical_average_amount=1e9)
    assert trade['filled_shares'] == 0. and account.cash == 0.
    account.apply_event('split', '510300.SH', 0., 2., date=calendar[25])
    assert account.shares['510300.SH'] == 200. and account.marks['510300.SH'] == 4.75
    assert account.receivable_value() == 50.
    account.trade('510300.SH', -200., 4.75, policy, historical_average_amount=1e9)
    assert account.cash == 950. and not account.shares and account.equity() == 1000.
    assert account.settle_receivables(calendar[29]) == []
    assert len(account.settle_receivables(pay)) == 1
    assert account.cash == 1000. and account.equity() == 1000. and not account.receivables
    assert account.settle_receivables(pay) == []
    account.apply_event('dividend', '510300.SH', .5, 1., date=ex, pay_date=pay)
    assert account.cash == 1000.  # idempotent replay after settlement cannot pay twice
    with pytest.raises(QualityError, match='identity changed'):
        account.apply_event('dividend', '510300.SH', .5, 1., date=ex, pay_date=calendar[31])


@pytest.mark.parametrize('payment', ['2023-01-01', '2023-02-13T12:00:00', '2023-02-13T00:00:00+08:00'])
def test_invalid_payment_does_not_mutate_account(calendar, payment):
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    original = copy.deepcopy(account)
    with pytest.raises(QualityError):
        account.apply_event('invalid', '510300.SH', .5, date=calendar[15], pay_date=payment)
    assert account == original


def test_qlib_claim_is_separate_from_cash_delay_and_stock_ids(calendar):
    position = ETFPosition(cash=50., position_dict={'510300.SH': {'amount': 100., 'price': 9.5}})
    position.dividend_receivables['dividend'] = {'amount': 50., 'pay_date': str(calendar[30])}
    assert position.get_cash() == 50. and position.calculate_value() == 1050.
    assert position.get_stock_list() == ['510300.SH']
    position.settle_start('cash')
    position.settle_commit()
    assert position.get_cash() == 50. and position.receivable_value() == 50.
    position.settle_dividends(calendar[29])
    assert position.get_cash() == 50.
    position.settle_dividends(calendar[30])
    assert position.get_cash() == 100. and position.calculate_value() == 1050.
    position.settle_dividends(calendar[31])
    assert position.get_cash() == 100.


def test_exposure_separately_accounts_nonspendable_receivable(calendar):
    day = calendar[20]
    position = ETFPosition(cash=0., position_dict={'510300.SH': {'amount': 100., 'price': 9.5}})
    position.dividend_receivables['dividend'] = {'amount': 50., 'pay_date': str(calendar[30])}
    universe = pd.DataFrame({'tracking_group': 'equity'},
        index=pd.MultiIndex.from_tuples([(day, '510300.SH')], names=['datetime', 'instrument']))
    metrics, frame = position_exposures({day: position}, universe)
    assert frame.single_weight.iloc[0] == .95 and frame.cash_weight.iloc[0] == 0.
    assert frame.receivable_weight.iloc[0] == .05 and metrics['max_receivable_weight'] == .05


def paper(calendar):
    return {'trading_day': str(calendar[20].date()), 'cash': 0., 'shares': {'510300.SH': 100.},
            'receivables': [{'event_id': 'dividend', 'instrument': '510300.SH', 'amount': 50.,
                            'ex_date': str(calendar[15].date()), 'pay_date': str(calendar[30].date())}],
            'equity_history': [{'date': str(calendar[19].date()), 'equity': 1000.}]}


@pytest.mark.parametrize('case', ['duplicate', 'future_ex', 'already_payable', 'negative', 'bool'])
def test_paper_account_rejects_invalid_receivables(calendar, case):
    data = paper(calendar)
    claim = data['receivables'][0]
    if case == 'duplicate': data['receivables'].append(copy.deepcopy(claim))
    elif case == 'future_ex': claim['ex_date'] = str(calendar[21].date())
    elif case == 'already_payable': claim['pay_date'] = data['trading_day']
    elif case == 'negative': claim['amount'] = -50.
    elif case == 'bool': claim['amount'] = True
    with pytest.raises(ValidationError):
        PaperAccount.model_validate(data)


def test_paper_valuation_reconciles_receivable_and_checks_snapshot_event(panel, calendar):
    data = paper(calendar)
    frame = panel.copy()
    frame.loc[(slice(None), '510300.SH'), 'raw_close'] = 9.5
    events = pd.DataFrame([{'event_id': 'dividend', 'instrument': '510300.SH', 'datetime': calendar[15],
                           'cash_per_share': .5, 'share_multiplier': 1., 'pay_date': calendar[30],
                           'record_date': calendar[14]}])
    events, _ = validate_events(events, calendar, ['510300.SH'])
    # Supplied history and as-of paper account already reflect earned claims.
    account = PaperAccount.model_validate(data)
    result = mark_account(account, frame, calendar, calendar[20], events)
    assert result[0].cash == 0. and result[0].receivable_value() == 50.
    assert result[0].equity() == 1000.
    with pytest.raises(QualityError, match='snapshot event'):
        mark_account(account, frame, calendar, calendar[20], events.assign(event_id='different'))


def test_payment_after_snapshot_and_weekend_dates_are_valid_future_claims(calendar):
    events = pd.DataFrame([{'event_id': 'future', 'instrument': '510300.SH', 'datetime': calendar[15],
                           'cash_per_share': .5, 'share_multiplier': 1., 'pay_date': '2024-01-06',
                           'record_date': calendar[14]}])
    normalized, _ = validate_events(events, calendar, ['510300.SH'])
    assert normalized.pay_date.iloc[0] == pd.Timestamp('2024-01-06')
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    account.apply_event('future', '510300.SH', .5, date=calendar[15], pay_date=normalized.pay_date.iloc[0])
    assert account.settle_receivables(calendar[-1]) == []
    assert len(account.settle_receivables(pd.Timestamp('2024-01-08'))) == 1


@pytest.mark.parametrize('locked,turnover', [(0., 1.), (.3, 1.), (.3, .1)])
def test_common_allocator_and_shadow_targets_reserve_receivables(locked, turnover):
    from etf_ml.portfolio.allocation import construct
    from etf_ml.operations.daily import target_allocation
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=0,
        liquidity_mode='participation', risk_mode='max_drawdown', max_turnover=turnover)
    positions = {'HELD': locked} if locked else {}
    constraints = {'buyable': {'BUY': True}, 'sellable': {'BUY': True, 'HELD': False},
                   'tracking_group': {'BUY': 'new', 'HELD': 'old'}, 'receivable_weight': .05}
    scores = pd.Series({'BUY': 1.})
    allocation = construct(scores, positions, constraints, policy)
    assert allocation.receivable_weight == .05
    assert sum(allocation.weights.values()) + allocation.cash_weight + allocation.receivable_weight == pytest.approx(1.)
    assert sum(allocation.weights.values()) <= .95
    assert allocation.weights.get('HELD', 0.) == locked
    if turnover == 1.:
        assert allocation.weights['BUY'] == pytest.approx(.95 - locked)
    risk = {'drawdown': 0., 'receivable_weight': .05}
    targets, reasons, _ = target_allocation(scores, positions, constraints, policy, risk, 252)
    assert targets == allocation.weights
    assert reasons['_receivables'] == 'dividend_receivable_not_spendable'


@pytest.mark.parametrize('weight', [-.1, 1.1, float('nan'), float('inf'), True])
def test_allocator_rejects_invalid_receivable_weights(weight):
    from etf_ml.portfolio.allocation import construct
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=0,
        liquidity_mode='participation', risk_mode='max_drawdown')
    with pytest.raises(QualityError, match='receivable weight'):
        construct(pd.Series({'BUY': 1.}), {}, {'receivable_weight': weight}, policy)


def test_allocator_rejects_receivable_and_stocks_exceeding_equity():
    from etf_ml.portfolio.allocation import construct
    policy = PortfolioPolicy(k_mode='count', k=1, minimum_commission=0,
        liquidity_mode='participation', risk_mode='max_drawdown')
    with pytest.raises(QualityError, match='receivable weight'):
        construct(pd.Series({'BUY': 1.}), {'HELD': .98}, {'receivable_weight': .05}, policy)
