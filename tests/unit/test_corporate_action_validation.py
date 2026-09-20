import numpy as np
import pandas as pd
import pytest

from etf_ml.backtest.accounting import Account
from etf_ml.data.actions import adjust_mark, raw_close_as_of, validate_events
from etf_ml.data.snapshot import audit_source, build_snapshot
from etf_ml.contracts import UniversePolicy
from etf_ml.errors import QualityError


@pytest.fixture
def action(calendar):
    return pd.DataFrame([{'event_id': 'known-dividend', 'datetime': calendar[30], 'instrument': '510300.SH',
        'cash_per_share': .2, 'share_multiplier': 1., 'available_time': str(calendar[29].date()) + 'T16:00:00+08:00',
        'pay_date': calendar[30], 'record_date': calendar[29]}])


def test_action_validation_is_stable_and_timezone_aware(action, calendar):
    first, report = validate_events(action, calendar, ['510300.SH'])
    second, _ = validate_events(first, calendar, ['510300.SH'])
    pd.testing.assert_frame_equal(first, second)
    assert first.sequence.iloc[0] == 0
    assert first.available_time.dt.tz is not None
    assert report['event_count'] == 1 and report['announcement_time_unknown'] == 0
    assert not report['source_completeness_verified']
    assert report['cash_model'] == 'dated_dividend_receivables'


@pytest.mark.parametrize('field,value,message', [
    ('event_id', '', 'identity'), ('event_id', ' spaced ', 'identity'),
    ('instrument', 'UNKNOWN', 'unknown ETF'), ('cash_per_share', -.1, 'amounts'),
    ('cash_per_share', np.nan, 'finite numeric'), ('cash_per_share', np.inf, 'finite numeric'),
    ('share_multiplier', 0., 'amounts'), ('share_multiplier', -1., 'amounts'),
    ('share_multiplier', '2', 'finite numeric'), ('share_multiplier', True, 'finite numeric'),
    ('datetime', '2023-01-08', 'calendar'), ('datetime', '2023-02-13T16:00:00', 'datetime'),
    ('datetime', '2023-02-13T00:00:00+08:00', 'datetime'), ('sequence', -1, 'sequence'),
    ('sequence', .5, 'sequence'), ('sequence', True, 'sequence'),
    ('available_time', '2023-02-13T10:00:00+08:00', 'opening time'),
    ('available_time', '2023-02-10T16:00:00', 'announcement time'),
    ('pay_date', '2023-02-10', 'precedes'),
    ('record_date', '2023-02-13', 'record date'),
])
def test_invalid_action_fields_are_rejected(action, calendar, field, value, message):
    action[field] = value
    with pytest.raises(QualityError, match=message):
        validate_events(action, calendar, ['510300.SH'])


def test_duplicate_action_id_and_ambiguous_sequence_fail(action, calendar):
    repeated = pd.concat([action, action], ignore_index=True)
    with pytest.raises(QualityError, match='Duplicate'):
        validate_events(repeated, calendar, ['510300.SH'])
    repeated.loc[1, 'event_id'] = 'second-dividend'
    with pytest.raises(QualityError, match='unique sequence'):
        validate_events(repeated, calendar, ['510300.SH'])
    repeated['sequence'] = [1, 0]
    ordered, _ = validate_events(repeated, calendar, ['510300.SH'])
    assert ordered.event_id.tolist() == ['second-dividend', 'known-dividend']


def test_no_effect_action_is_rejected(action, calendar):
    action.cash_per_share = 0
    with pytest.raises(QualityError, match='no economic effect'):
        validate_events(action, calendar, ['510300.SH'])


def test_stale_close_applies_ordered_actions_without_future_quotes(panel, calendar):
    first, second, day = calendar[29], calendar[30], calendar[31]
    frame = panel.copy()
    instrument = '510300.SH'
    frame.loc[(first, instrument), 'raw_close'] = 10.
    frame.loc[(slice(second, day), instrument), 'raw_close'] = np.nan
    frame.loc[(calendar[32], instrument), 'raw_close'] = 1000000.
    actions = pd.DataFrame([
        {'event_id': 'first', 'datetime': second, 'instrument': instrument, 'cash_per_share': .2, 'share_multiplier': 2., 'sequence': 0},
        {'event_id': 'second', 'datetime': second, 'instrument': instrument, 'cash_per_share': .1, 'share_multiplier': 1., 'sequence': 1},
        {'event_id': 'future', 'datetime': calendar[32], 'instrument': instrument, 'cash_per_share': 1., 'share_multiplier': 3., 'sequence': 0},
    ])
    # Reverse source order deliberately: sequence, rather than table order,
    # determines the distribution per share after the first conversion.
    price, quote_date = raw_close_as_of(frame, actions.iloc[::-1], instrument, day)
    assert price == pytest.approx(4.8) and quote_date == first
    assert raw_close_as_of(frame, actions, instrument, calendar[32])[0] == 1000000.


def test_invalid_action_does_not_partially_mutate_account():
    account = Account(100, {'ETF': 100}, {'ETF': 10})
    with pytest.raises(QualityError, match='mark'):
        account.apply_event('too-large', 'ETF', 11., 2.)
    assert account.cash == 100 and account.shares == {'ETF': 100} and account.marks == {'ETF': 10}
    assert not account.events and not account.applied_events
    account.apply_event('known', 'ETF', .2, 2.)
    assert account.equity() == pytest.approx(1100)
    with pytest.raises(QualityError, match='identity changed'):
        account.apply_event('known', 'ETF', .3, 2.)


def test_invalid_events_block_snapshot_before_publication(source_spec, action, calendar):
    path = source_spec.source.parent / 'invalid-events.parquet'
    duplicated = pd.concat([action, action], ignore_index=True)
    duplicated.to_parquet(path, index=False)
    source_spec.events_path = path
    before = list(source_spec.artifact_root.glob('*'))
    report = audit_source(source_spec)
    assert report['status'] == 'failed'
    assert 'invalid_corporate_actions' in {row['code'] for row in report['errors']}
    with pytest.raises(QualityError, match='Duplicate'):
        build_snapshot(source_spec.source, source_spec, UniversePolicy(minimum_listing_days=0, liquidity_lookback=1))
    assert list(source_spec.artifact_root.glob('*')) == before


def test_mixed_dividend_and_split_allow_absent_irrelevant_cash_dates(action, calendar):
    split = {'event_id': 'split-only', 'datetime': calendar[40], 'instrument': '510300.SH',
             'cash_per_share': 0., 'share_multiplier': 2., 'pay_date': pd.NaT, 'record_date': pd.NaT,
             'available_time': str(calendar[39].date()) + 'T16:00:00+08:00'}
    combined = pd.concat([action, pd.DataFrame([split])], ignore_index=True)
    first, report = validate_events(combined, calendar, ['510300.SH'])
    second, _ = validate_events(first, calendar, ['510300.SH'])
    pd.testing.assert_frame_equal(first, second)
    assert report['event_count'] == 2
    assert first.loc[0, 'pay_date'] == calendar[30]
    assert pd.isna(first.loc[1, 'pay_date']) and pd.isna(first.loc[1, 'record_date'])
    only_split, _ = validate_events(combined.iloc[[1]], calendar, ['510300.SH'])
    assert pd.isna(only_split.pay_date.iloc[0])


@pytest.mark.parametrize('column,value', [('pay_date', pd.NaT), ('record_date', None),
                                          ('pay_date', 'invalid-date'),
                                          ('record_date', '2023-02-10T00:00:00+08:00')])
def test_cash_rows_still_require_valid_declared_dates(action, calendar, column, value):
    action[column] = value
    with pytest.raises(QualityError, match=column):
        validate_events(action, calendar, ['510300.SH'])


def test_nonempty_irrelevant_split_date_must_still_be_valid(action, calendar):
    action['cash_per_share'] = 0.
    action['share_multiplier'] = 2.
    action['pay_date'] = 'invalid-date'
    with pytest.raises(QualityError, match='pay_date'):
        validate_events(action, calendar, ['510300.SH'])



def test_exact_share_allocation_accepts_integer_product_without_inventing_fractional_rule():
    from etf_ml.data.actions import adjusted_shares
    assert adjusted_shares(101,3,'exact')==303
    assert adjusted_shares(.5,2,'exact')==1
    with pytest.raises(QualityError,match='explicit allocation detail'):
        adjusted_shares(101.1,3,'exact')
