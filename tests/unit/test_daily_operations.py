import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from etf_ml.config import load_config
from etf_ml.contracts import DataSnapshot
from etf_ml.errors import ConfigurationError, DataNotReady, IntegrityError, QualityError
from etf_ml.operations.contracts import IngestionReceipt, PaperAccount, local_time
from etf_ml.operations.daily import check_readiness, day_run, mark_account, order_intents, target_allocation
from etf_ml.operations.monitoring import concentration, feature_diagnostics, prediction_diagnostics
from etf_ml.utils import atomic_json, file_hash


@pytest.fixture
def operational_input(tmp_path, calendar, fold):
    config = load_config(overrides={'portfolio': {'k_mode': 'fraction', 'minimum_commission': 0,
        'liquidity_mode': 'participation', 'risk_mode': 'max_drawdown'},
        'validation': {'folds': [fold.model_copy(update={'selection': fold.selection.model_copy(update={'end': str(calendar[139].date())})}).model_dump()]}})
    path = tmp_path / 'snapshot'
    path.mkdir()
    execution = pd.bdate_range(calendar[0], calendar[-1] + pd.offsets.MonthEnd(2))
    (path / 'execution_calendar.txt').write_text('\n'.join(execution.strftime('%Y-%m-%d')) + '\n')
    atomic_json(path / 'data_quality.json', {'status': 'passed', 'normalized_errors': [], 'raw_source_unchanged': True})
    manifest = {'cutoff': str(calendar[-1].date()), 'spec': config.data.model_dump(mode='json'),
                'universe_policy': config.universe.model_dump(mode='json')}
    atomic_json(path / 'snapshot_manifest.json', manifest)
    snapshot = DataSnapshot('a' * 64, path, manifest)
    receipt = IngestionReceipt(snapshot_id=snapshot.snapshot_id, snapshot_manifest_hash=file_hash(path / 'snapshot_manifest.json'),
        trading_day=calendar[-1].date(), completed_at=f'{calendar[-1].date()}T16:00:00+08:00', complete=True,
        expected_instruments=['510300.SH', '510500.SH', '159915.SZ'])
    account = PaperAccount(trading_day=calendar[-1].date(), cash=500000, shares={},
                           equity_history=[{'date': calendar[-2].date(), 'equity': 500000}])
    return config, snapshot, receipt, account, f'{calendar[-1].date()}T16:30:00+08:00'


def test_timezone_equivalence_and_valid_readiness(operational_input):
    config, snapshot, receipt, account, observed = operational_input
    assert local_time(observed) == local_time(str(local_time(observed).tz_convert('UTC')))
    day, next_day, calendar = check_readiness(snapshot, receipt, account, observed, config)
    assert next_day == calendar[calendar > day][0]


@pytest.mark.parametrize('case,expected', [
    ('before_close', DataNotReady), ('weekend', DataNotReady), ('stale', DataNotReady),
    ('partial', DataNotReady), ('missing', DataNotReady), ('future_ingestion', DataNotReady),
    ('early_ingestion', DataNotReady), ('wrong_hash', IntegrityError), ('wrong_semantics', ConfigurationError),
    ('bad_quality', QualityError), ('short_execution_calendar', DataNotReady), ('stale_account', DataNotReady)])
def test_readiness_rejects_unready_or_changed_inputs(operational_input, case, expected):
    config, snapshot, receipt, account, observed = operational_input
    if case == 'before_close': observed = observed.replace('16:30', '14:30')
    elif case == 'weekend': observed = str(local_time(observed) + pd.Timedelta(days=1))
    elif case == 'stale': snapshot.manifest['cutoff'] = '2020-01-01'
    elif case == 'partial': receipt.complete = False
    elif case == 'missing': receipt.missing_instruments = ['510300.SH']
    elif case == 'future_ingestion': receipt.completed_at = receipt.completed_at.replace('16:00', '17:00')
    elif case == 'early_ingestion': receipt.completed_at = receipt.completed_at.replace('16:00', '14:00')
    elif case == 'wrong_hash': receipt.snapshot_manifest_hash = 'b' * 64
    elif case == 'wrong_semantics': snapshot.manifest['spec']['volume_unit'] = 'lots'
    elif case == 'bad_quality': atomic_json(snapshot.path / 'data_quality.json', {'status': 'failed'})
    elif case == 'short_execution_calendar':
        calendar = pd.bdate_range('2023-01-02', str(receipt.trading_day))
        (snapshot.path / 'execution_calendar.txt').write_text('\n'.join(calendar.strftime('%Y-%m-%d')) + '\n')
    elif case == 'stale_account': account.trading_day = account.trading_day - pd.Timedelta(days=1)
    with pytest.raises(expected): check_readiness(snapshot, receipt, account, observed, config)


@pytest.mark.parametrize('value', ['2023-09-08T16:30:00', 'not-a-date', 'NaT'])
def test_operational_clock_requires_timezone(value):
    with pytest.raises(ConfigurationError): local_time(value)


@pytest.mark.parametrize('change', [{'cash': -1}, {'shares': {'ETF': float('nan')}}, {'shares': {'ETF': -2}},
    {'equity_history': [{'date': '2023-09-09', 'equity': 1}]},
    {'equity_history': [{'date': '2023-09-07', 'equity': 1}, {'date': '2023-09-07', 'equity': 2}]}])
def test_paper_account_rejects_invalid_or_future_state(change):
    payload = {'trading_day': '2023-09-08', 'cash': 500000, 'shares': {},
               'equity_history': [{'date': '2023-09-07', 'equity': 500000}]}
    with pytest.raises(ValidationError): PaperAccount.model_validate({**payload, **change})


def test_mark_preserves_held_shares_and_checks_equity(panel, calendar):
    day = calendar[-1]
    account = PaperAccount(trading_day=day.date(), cash=100, shares={'510300.SH': 100.33},
        equity_history=[{'date': calendar[-2].date(), 'equity': 500}])
    events = pd.DataFrame(columns=['datetime', 'instrument', 'cash_per_share', 'share_multiplier'])
    paper, weights, history, risk = mark_account(account, panel, calendar, day, events, annualization_days=365)
    assert paper.shares == account.shares and account.cash == 100
    assert risk['equity'] == pytest.approx(100 + 100.33 * panel.loc[(day, '510300.SH'), 'raw_close'])
    assert sum(weights.values()) + paper.cash / risk['equity'] == pytest.approx(1)
    bad = account.model_copy(update={'equity_history': [account.equity_history[0].model_copy(update={'date': day.date(), 'equity': 999})]})
    with pytest.raises(QualityError, match='reconcile'): mark_account(bad, panel, calendar, day, events)


def test_stale_held_mark_adjusts_split_and_dividend_without_synthetic_trade(panel, calendar):
    day, instrument = calendar[-1], '510300.SH'
    prior = panel.loc[(calendar[-2], instrument), 'raw_close']
    panel = panel.drop(index=(day, instrument))
    account = PaperAccount(trading_day=day.date(), cash=50, shares={instrument: 200},
        equity_history=[{'date': calendar[-2].date(), 'equity': 50 + 200 * (prior - .1) / 2}])
    events = pd.DataFrame([{'datetime': day, 'instrument': instrument, 'cash_per_share': .1, 'share_multiplier': 2}])
    paper, weights, history, risk = mark_account(account, panel, calendar, day, events)
    assert paper.marks[instrument] == pytest.approx((prior - .1) / 2)
    assert risk['stale_valuation_dates'] == {instrument: str(calendar[-2].date())}
    assert paper.shares == {instrument: 200} and not paper.trades


def test_drawdown_blocks_new_buys_but_preserves_unsellable_holdings(operational_input):
    policy = operational_input[0].portfolio
    scores = pd.Series({'A': 3., 'B': 2.})
    constraints = {'buyable': {'A': True, 'B': True}, 'sellable': {'HELD': False},
                   'tracking_group': {'A': 'g', 'B': 'h', 'HELD': 'g'}}
    targets, reasons, _ = target_allocation(scores, {'HELD': .3}, constraints, policy,
        {'drawdown': .12, 'annualized_volatility': .2, 'return_observations': 20}, 252)
    assert targets == {'HELD': .3} and reasons['_risk'] == 'risk_limit_triggered'


def test_volatility_policy_requires_twenty_observations_and_scales_targets(operational_input):
    policy = operational_input[0].portfolio.model_copy(update={'risk_mode': 'annualized_volatility', 'k': 1.})
    constraints = {'buyable': {'A': True}, 'sellable': {'A': True, 'HELD': False},
                   'tracking_group': {'A': 'g', 'HELD': 'h'}}
    risk = {'drawdown': 0, 'annualized_volatility': .24, 'return_observations': 19}
    targets, reasons, _ = target_allocation(pd.Series({'A': 1.}), {'HELD': .2}, constraints, policy, risk, 252)
    assert targets == {'HELD': .2} and reasons['_risk'] == 'insufficient_20_day_risk_history'
    targets, _, _ = target_allocation(pd.Series({'A': 1.}), {'HELD': .2}, constraints, policy,
                                      {**risk, 'return_observations': 20}, 252)
    assert targets == pytest.approx({'HELD': .2, 'A': .4})


def test_order_intents_apply_fees_liquidity_and_lots_to_a_copy(panel, calendar, operational_input):
    from etf_ml.backtest.accounting import Account
    policy = operational_input[0].portfolio
    cash = 500000
    account = Account(cash)
    intents = order_intents(account, {'510300.SH': 1.}, {}, cash, panel, calendar[-1],
                           {'buyable': {'510300.SH': True}, 'sellable': {}}, policy)
    assert len(intents) == 1 and intents[0]['kind'] == 'shadow_order_intent'
    intent = intents[0]
    assert intent['amount'] <= intent['historical_participation_notional'] + 1e-8
    assert intent['filled_shares'] % policy.lot_size == 0
    assert intent['commission'] == pytest.approx(intent['amount'] * .003)
    assert intent['price'] == pytest.approx(intent['reference_price'] * 1.0003)
    assert account.cash >= 0


def test_monitoring_uses_development_reference_and_handles_constant_columns():
    reference = pd.DataFrame({'x': [1., 2., 3., 4.], 'constant': [1.] * 4})
    current = pd.DataFrame({'x': [3., np.nan], 'constant': [1., 1.]})
    diagnostic = feature_diagnostics(reference, current)
    assert diagnostic['minimum_column_coverage'] == .5
    assert diagnostic['drift']['constant']['status'] == 'constant_reference'
    assert diagnostic['drift']['constant']['median_shift_over_iqr'] is None
    assert prediction_diagnostics(pd.Series({'A': 3., 'B': 2., 'C': 1.}), pd.Series({'A': 1., 'B': 2., 'C': 3.}))['rank_correlation'] == -1.
    assert concentration({'A': .2, 'B': .3}, {'A': 'g', 'B': 'g'})['maximum_group_weight'] == .5
    assert concentration({'A': .2}, {})['unknown_group_weight'] == .2


def test_failed_day_accepts_corrected_inputs_but_successful_day_is_immutable(tmp_path):
    day = pd.Timestamp('2023-09-08')
    root = tmp_path / 'operations'
    with pytest.raises(RuntimeError):
        with day_run(root, day, {'snapshot': 'bad'}) as run:
            atomic_json(run.path / 'partial.json', {'reason': 'interrupted'})
            raise RuntimeError('Interrupted')
    with day_run(root, day, {'snapshot': 'corrected'}) as run:
        assert not run.reused
        atomic_json(run.path / 'signal.json', {'orders_sent': False})
        run.complete()
    assert list((root / 'failed_signals').glob('*/partial.json'))
    with day_run(root, day, {'snapshot': 'corrected'}) as run: assert run.reused
    with pytest.raises(ConfigurationError):
        with day_run(root, day, {'snapshot': 'different_after_completion'}): pass


def test_operations_cli_contract_requires_explicit_inputs():
    from etf_ml.cli import parser
    args = parser().parse_args(['daily-signal', '--snapshot', 'snapshot', '--ingestion', 'receipt.json',
                              '--account', 'account.json', '--as-of', '2023-09-08T16:30:00+08:00'])
    assert args.frozen_model is None
    assert parser().parse_args(['activate-model', '--frozen-model', 'package', '--selection-reason', 'Explicit shadow test']).command == 'activate-model'
    assert parser().parse_args(['restore-model', '--selection-reason', 'Recover previous complete package']).command == 'restore-model'


def test_committed_day_survives_later_request_publication_interruption(tmp_path):
    root, day = tmp_path / 'operations', pd.Timestamp('2023-09-08')
    with pytest.raises(KeyboardInterrupt):
        with day_run(root, day, {'snapshot': 'fixed'}) as run:
            atomic_json(run.path / 'signal.json', {'orders_sent': False})
            run.complete()
            raise KeyboardInterrupt()
    with day_run(root, day, {'snapshot': 'fixed'}) as run: assert run.reused


def test_paper_marks_ignore_future_quotes(panel, calendar):
    day = calendar[-2]
    changed = panel.copy()
    changed.loc[(calendar[-1], '510300.SH'), 'raw_close'] = 1000000.
    account = PaperAccount(trading_day=day.date(), cash=100, shares={'510300.SH': 100},
        equity_history=[{'date': calendar[-3].date(), 'equity': 500}])
    events = pd.DataFrame(columns=['datetime', 'instrument', 'cash_per_share', 'share_multiplier'])
    paper, _, _, _ = mark_account(account, changed, calendar, day, events)
    assert paper.marks['510300.SH'] == panel.loc[(day, '510300.SH'), 'raw_close']
