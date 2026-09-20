import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.accounting import Account
from etf_ml.data.actions import cash_per_ex_share, raw_close_as_of, recorded_quantity, validate_events
from etf_ml.errors import QualityError


def event(calendar, *, sequence=0):
    return {'event_id': 'dividend', 'datetime': calendar[25], 'instrument': '510300.SH',
            'cash_per_share': .5, 'share_multiplier': 1., 'record_date': calendar[14],
            'pay_date': calendar[35], 'sequence': sequence}


def test_nonadjacent_record_day_is_valid_and_normalization_is_stable(calendar):
    first, report = validate_events(pd.DataFrame([event(calendar)]), calendar, ['510300.SH'])
    second, _ = validate_events(first, calendar, ['510300.SH'])
    pd.testing.assert_frame_equal(first, second)
    assert first.record_date.iloc[0] == calendar[14]
    assert report['entitlement_model'] == 'record_close_or_pre_action_compatibility'


@pytest.mark.parametrize('record', ['2023-01-01', '2023-01-21', '2023-02-06', '2023-02-07'])
def test_uncovered_weekend_same_or_future_record_dates_rejected(calendar, record):
    row = event(calendar)
    row['record_date'] = record
    with pytest.raises(QualityError, match='record date'):
        validate_events(pd.DataFrame([row]), calendar, ['510300.SH'])


@pytest.mark.parametrize('current', [0., 50., 200.])
def test_distribution_uses_recorded_shares_after_sales_or_purchases(calendar, current):
    account = Account(0.)
    account.begin_history(calendar[1])
    account.shares = {'510300.SH': 100.}
    account.marks = {'510300.SH': 10.}
    account.record_holdings(calendar[14])
    account.shares = {'510300.SH': current} if current else {}
    quantity = account.dividend_quantity(calendar[14], '510300.SH')
    assert quantity == 100.
    account.apply_event('dividend', '510300.SH', .5, date=calendar[25], pay_date=calendar[35],
                        entitled_shares=quantity, record_date=calendar[14])
    assert account.cash == 0. and account.receivable_value() == 50.
    assert account.events[-1]['old_shares'] == current and account.events[-1]['entitled_shares'] == 100.
    assert account.settle_receivables(calendar[35])[0]['amount'] == 50.


def test_purchase_after_record_day_does_not_create_entitlement(calendar):
    account = Account(0.)
    account.begin_history(calendar[1])
    account.record_holdings(calendar[14])
    account.shares = {'510300.SH': 100.}
    account.marks = {'510300.SH': 10.}
    registered = account.dividend_quantity(calendar[14], '510300.SH')
    account.apply_event('dividend', '510300.SH', .5, date=calendar[25], pay_date=calendar[35],
                        entitled_shares=registered, record_date=calendar[14])
    assert account.receivable_value() == 0. and account.cash == 0.
    assert account.marks['510300.SH'] == 9.5


def test_unknown_historical_record_cannot_be_replaced_by_current_holdings(calendar):
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    with pytest.raises(QualityError, match='closing holdings'):
        account.dividend_quantity(calendar[14], '510300.SH')
    account.begin_history(calendar[20])
    with pytest.raises(QualityError, match='closing holdings'):
        account.dividend_quantity(calendar[14], '510300.SH')
    with pytest.raises(QualityError, match='closing holdings'):
        account.dividend_quantity(calendar[21], '510300.SH')
    flat = Account(1000.)
    flat.begin_history(calendar[20])
    assert flat.dividend_quantity(calendar[14], '510300.SH') == 0.


def test_recorded_holdings_are_immutable_copies(calendar):
    account = Account(0., {'510300.SH': 100.})
    account.record_holdings(calendar[14])
    original = copy.deepcopy(account.registered_holdings)
    account.shares['510300.SH'] = 0.
    assert account.registered_holdings == original
    with pytest.raises(QualityError, match='recorded'):
        account.record_holdings(calendar[14])
    assert account.registered_holdings == original


@pytest.mark.parametrize('same_day', [False, True])
def test_split_between_record_and_ex_converts_valuation_but_not_cash_entitlement(calendar, same_day):
    row = event(calendar, sequence=1 if same_day else 0)
    split = {'event_id': 'split', 'datetime': calendar[25] if same_day else calendar[18],
             'instrument': '510300.SH', 'cash_per_share': 0., 'share_multiplier': 2.,
             'record_date': pd.NaT, 'pay_date': pd.NaT, 'sequence': 0}
    events, _ = validate_events(pd.DataFrame([row, split]), calendar, ['510300.SH'])
    dividend = next(r for r in events.itertuples(index=False) if r.event_id == 'dividend')
    assert cash_per_ex_share(dividend, events) == .25
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    account.record_holdings(calendar[14])
    account.apply_event('split', '510300.SH', 0., 2., date=split['datetime'])
    quantity = account.dividend_quantity(calendar[14], '510300.SH')
    account.apply_event('dividend', '510300.SH', .5, date=row['datetime'], pay_date=row['pay_date'],
        entitled_shares=quantity, valuation_cash_per_share=.25, record_date=row['record_date'])
    assert account.shares['510300.SH'] == 200. and account.marks['510300.SH'] == 4.75
    assert account.receivable_value() == 50. and account.equity() == 1000.
    index = pd.MultiIndex.from_tuples([(calendar[14], '510300.SH'), (calendar[25], '510300.SH')],
                                     names=['datetime', 'instrument'])
    panel = pd.DataFrame({'raw_close': [10., np.nan]}, index=index)
    assert raw_close_as_of(panel, events, '510300.SH', calendar[25])[0] == 4.75


def test_record_day_split_is_already_in_registered_units(calendar):
    rows = [event(calendar), {'event_id': 'split', 'datetime': calendar[14], 'instrument': '510300.SH',
        'cash_per_share': 0., 'share_multiplier': 2., 'record_date': pd.NaT, 'pay_date': pd.NaT, 'sequence': 0}]
    events, _ = validate_events(pd.DataFrame(rows), calendar, ['510300.SH'])
    dividend = next(r for r in events.itertuples(index=False) if r.event_id == 'dividend')
    assert cash_per_ex_share(dividend, events) == .5


@pytest.mark.parametrize('quantity', [-1., np.inf, np.nan, True])
def test_invalid_entitlement_does_not_mutate_account(calendar, quantity):
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    original = copy.deepcopy(account)
    with pytest.raises(QualityError, match='entitlement'):
        account.apply_event('dividend', '510300.SH', .5, date=calendar[25], pay_date=calendar[35],
                            entitled_shares=quantity, record_date=calendar[14])
    assert account == original


def test_replayed_event_cannot_change_record_date_or_entitled_quantity(calendar):
    account = Account(0., {'510300.SH': 100.}, {'510300.SH': 10.})
    kwargs = {'date': calendar[25], 'pay_date': calendar[35], 'entitled_shares': 100., 'record_date': calendar[14]}
    account.apply_event('dividend', '510300.SH', .5, **kwargs)
    account.apply_event('dividend', '510300.SH', .5, **kwargs)
    assert account.receivable_value() == 50.
    for change in [{'record_date': calendar[13]}, {'entitled_shares': 50.}, {'valuation_cash_per_share': .25}]:
        with pytest.raises(QualityError, match='identity changed'):
            account.apply_event('dividend', '510300.SH', .5, **{**kwargs, **change})
