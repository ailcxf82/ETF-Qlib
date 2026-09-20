from __future__ import annotations

import json
import math
import os
import sys
from contextlib import contextmanager
import time
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from etf_ml.artifacts import RunStore
from etf_ml.backtest.accounting import Account
from etf_ml.data.calendar import read_calendar, rebalance_dates, require_calendar
from etf_ml.data.actions import raw_close_as_of
from etf_ml.data.snapshot import load_snapshot
from etf_ml.data.universe import resolve, validate_metadata
from etf_ml.errors import ConfigurationError, DataNotReady, IntegrityError, QualityError
from etf_ml.models.deployment import load_frozen_model
from etf_ml.models.frozen_features import materialize_frozen_features
from etf_ml.models.predict import predict
from etf_ml.operations.contracts import IngestionReceipt, PaperAccount, local_time
from etf_ml.operations.monitoring import concentration, feature_diagnostics, prediction_diagnostics
from etf_ml.portfolio.allocation import construct
from etf_ml.research.execution import _raise_failure
from etf_ml.runtime.native import NativeBackend
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, verify_files


def check_readiness(snapshot, receipt, account, as_of, config):
    if config.label.execution_lag != 1:
        raise ConfigurationError('Daily execution requires the frozen t+1 protocol')
    observed = local_time(as_of)
    day = observed.tz_localize(None).normalize()
    calendar = read_calendar(snapshot.path / 'execution_calendar.txt')
    require_calendar(calendar)
    if day not in calendar:
        raise DataNotReady('non_trading_day')
    if observed.hour < 15:
        raise DataNotReady('market_not_closed')
    if receipt.snapshot_id != snapshot.snapshot_id or receipt.snapshot_manifest_hash != file_hash(snapshot.path / 'snapshot_manifest.json'):
        raise IntegrityError('Ingestion receipt belongs to another snapshot')
    if (str(receipt.trading_day) != str(day.date()) or snapshot.manifest['cutoff'] != str(day.date()) or
            str(account.trading_day) != str(day.date())):
        raise DataNotReady('stale_or_future_input_day')
    completed = local_time(receipt.completed_at)
    if completed > observed or completed.date() != observed.date() or completed.hour < 15:
        raise DataNotReady('ingestion_not_complete_at_signal_time')
    if not receipt.complete or receipt.missing_instruments:
        raise DataNotReady('incomplete_ingestion')
    semantics = ('volume_unit', 'amount_multiplier', 'price_mode', 'change_unit', 'lot_size',
                 'fields', 'point_in_time_metadata', 'benchmark_id', 'holdout_start')
    if (any(snapshot.manifest['spec'][name] != config.data.model_dump(mode='json')[name] for name in semantics) or
            snapshot.manifest['universe_policy'] != config.universe.model_dump(mode='json')):
        raise ConfigurationError('Daily snapshot changed frozen data semantics or universe policy')
    quality = json.loads((snapshot.path / 'data_quality.json').read_text(encoding='utf-8'))
    if quality.get('status') != 'passed' or quality.get('normalized_errors') or quality.get('raw_source_unchanged') is not True:
        raise QualityError('Daily input snapshot has not passed data quality')
    future = calendar[calendar > day]
    if not len(future) or calendar[-1].to_period('M') <= future[0].to_period('M'):
        raise DataNotReady('incomplete_next_execution_month_calendar')
    next_day = future[0]
    if day <= pd.Timestamp(max(fold.selection.end for fold in config.validation.folds)):
        raise ConfigurationError('Daily inference must follow all development selection periods')
    return day, next_day, calendar


def _current_universe(panel, snapshot, calendar, day, observed, policy, receipt):
    dates = panel.index.get_level_values('datetime')
    if dates.max() != day or (dates > day).any():
        raise IntegrityError('Daily panel contains a stale cutoff or future rows')
    metadata = validate_metadata(pd.read_parquet(snapshot.path / 'metadata.parquet'))
    metadata = metadata[metadata.available_time <= observed.tz_localize(None)]
    known = metadata[(metadata.valid_from <= day) & (metadata.valid_to >= day) & metadata.operating &
                     (metadata.asset_class == policy.allowed_asset_class)]
    if set(receipt.expected_instruments) != set(known.instrument):
        raise DataNotReady('expected_pool_does_not_match_known_operating_scope')
    today = panel.xs(day, level='datetime')
    if not set(receipt.expected_instruments).issubset(today.index):
        raise DataNotReady('missing_expected_instrument_rows')
    expected = today.reindex(receipt.expected_instruments)
    if not expected.quoted.all() or not np.isfinite(expected.raw_close).all() or (expected.raw_close <= 0).any():
        raise DataNotReady('missing_expected_closing_quotes')
    return resolve(day, panel, metadata, calendar[calendar <= day], policy)


def mark_account(account, panel, calendar, day, events, *, annualization_days=252, income_rules=None):
    prices, stale = {}, {}
    for instrument, quantity in account.shares.items():
        if quantity == 0:
            continue
        price, quote_day = raw_close_as_of(panel, events, instrument, day)
        if quote_day < day:
            stale[instrument] = str(quote_day.date())
        prices[instrument] = price
    paper = Account(account.cash, {i: quantity for i, quantity in account.shares.items() if quantity > 0})
    if account.receivables:
        known = set(panel.index.get_level_values('instrument'))
        for claim in account.receivables:
            if claim.instrument not in known:
                raise QualityError('Paper receivable refers to unknown ETF')
            matching = events[events.event_id.eq(claim.event_id)] if events is not None and not events.empty else pd.DataFrame()
            if (len(matching) != 1 or matching.cash_per_share.iloc[0] <= 0 or matching.instrument.iloc[0] != claim.instrument or
                    pd.Timestamp(matching.datetime.iloc[0]).date() != claim.ex_date or
                    'pay_date' not in matching or pd.Timestamp(matching.pay_date.iloc[0]).date() != claim.pay_date):
                raise QualityError('Paper dividend claim differs from the snapshot event')
            paper.receivables[claim.event_id] = {'instrument': claim.instrument, 'amount': claim.amount,
                'ex_date': str(claim.ex_date), 'pay_date': str(claim.pay_date)}
    from decimal import Decimal
    rules = dict(income_rules or {})
    needed = {i for i, quantity in paper.shares.items() if quantity > 0 and i in rules}
    if not needed.issubset(account.income_balances):
        raise DataNotReady('missing_current_paper_income_balance')
    if account.income_balances and account.income_as_of != day.date():
        raise DataNotReady('stale_paper_income_balance')
    for instrument, amount in account.income_balances.items():
        if instrument not in rules or account.income_rule_ids[instrument] != rules[instrument].rule_id:
            raise QualityError('Paper income differs from the snapshot rule')
        balance = Decimal(str(amount))
        if not balance.is_finite() or balance != balance.quantize(Decimal('.01')):
            raise QualityError('Paper income must be finite booked cents')
        if balance and paper.shares.get(instrument, 0.) <= 0:
            raise QualityError('A fully sold paper income position must already be settled')
        paper.income_book.balances[instrument] = balance
    paper.income_book.rules = rules
    equity = paper.equity(prices)
    if not np.isfinite(equity) or equity <= 0:
        raise QualityError('Paper equity must be finite and positive')
    history = pd.Series({pd.Timestamp(point.date): point.equity for point in account.equity_history}, dtype=float).sort_index()
    if not history.index.isin(calendar).all():
        raise QualityError('Paper equity history contains non-trading dates')
    previous = calendar[calendar < day]
    if history.index[-1] != day and (not len(previous) or history.index[-1] != previous[-1]):
        raise DataNotReady('stale_paper_equity_history')
    if day in history.index and not np.isclose(history.loc[day], equity, rtol=1e-8, atol=1e-6):
        raise QualityError('Paper account equity does not reconcile to cash and raw-share marks')
    history.loc[day] = equity
    expected_dates = calendar[(calendar >= history.index[0]) & (calendar <= day)]
    if not history.index.equals(expected_dates.rename(None)) and not history.index.equals(expected_dates):
        raise QualityError('Paper equity history has missing trading dates')
    returns = history.pct_change().dropna()
    volatility = float(returns.tail(20).std(ddof=1) * np.sqrt(annualization_days)) if len(returns) >= 2 else None
    risk = {'equity': equity, 'available_cash': paper.cash, 'dividend_receivable': paper.receivable_value(),
            'available_cash_weight': paper.cash / equity, 'receivable_weight': paper.receivable_value() / equity,
            'income_balance': paper.income_book.value(), 'income_weight': paper.income_book.value() / equity,
            'drawdown': float(1 - equity / history.max()), 'return_observations': len(returns),
            'annualized_volatility': volatility, 'stale_valuation_dates': stale,
            'note': 'Caller supplies event-adjusted paper holdings; stale marks adjust for known subsequent actions'}
    weights = {i: quantity * prices[i] / equity for i, quantity in paper.shares.items()}
    return paper, weights, history, risk


def target_allocation(scores, weights, constraints, policy, risk, annualization_days):
    risk = dict(risk)
    constraints = {**constraints, 'receivable_weight': risk.get('receivable_weight', 0.), 'income_weight': risk.get('income_weight', 0.), 'risk_triggered': policy.risk_mode == 'max_drawdown' and risk['drawdown'] >= policy.risk}
    allocation = construct(scores, weights, constraints, policy)
    targets, reasons = dict(allocation.weights), dict(allocation.reasons)
    if policy.risk_mode == 'annualized_volatility':
        if risk['return_observations'] < 20:
            targets = {i: w for i, w in targets.items() if i in weights and not constraints['sellable'].get(i, False)}
            reasons['_risk'] = 'insufficient_20_day_risk_history'
        else:
            volatility = risk['annualized_volatility']
            scale = min(1., policy.risk / volatility) if volatility and volatility > 0 else 1.
            targets = {i: w if not constraints['sellable'].get(i, False) else w * scale for i, w in targets.items()}
            if scale < 1:
                reasons['_risk'] = 'annualized_volatility_scaling'
    return targets, reasons, risk


def order_intents(paper, targets, prices, equity, panel, day, constraints, policy):
    intentions = []
    desired = {}
    for instrument in set(targets) | set(paper.shares):
        reference = prices.get(instrument)
        if reference is None:
            current = panel.xs(day, level='datetime')
            if instrument in current.index and np.isfinite(current.loc[instrument, 'raw_close']):
                reference = float(current.loc[instrument, 'raw_close'])
        if not reference or reference <= 0:
            raise QualityError('Order intent requires a known closing reference price')
        target_shares = math.floor(targets.get(instrument, 0.) * equity / reference / policy.lot_size) * policy.lot_size
        delta = target_shares - paper.shares.get(instrument, 0.)
        if abs(delta) > 1e-8:
            desired[instrument] = (delta, reference)
    for instrument in sorted(desired, key=lambda i: (desired[i][0] > 0, i)):
        delta, price = desired[instrument]
        history = panel[(panel.index.get_level_values('instrument') == instrument) &
                        (panel.index.get_level_values('datetime') <= day)].amount_currency.tail(policy.liquidity_lookback)
        average = float(history.mean()) if len(history) == policy.liquidity_lookback and history.notna().all() else 0.
        estimate = paper.trade(instrument, delta, price, policy,
            buyable=constraints['buyable'].get(instrument, False), sellable=constraints['sellable'].get(instrument, False),
            historical_average_amount=average, date=day)
        intentions.append({**estimate, 'kind': 'shadow_order_intent', 'price_basis': 'signal_day_raw_close',
                           'historical_participation_notional': average * policy.liquidity,
                           'note': 'Estimated fills only; next open and execution restrictions remain unknown'})
    return intentions


def _previous_signal(root, day, version_id):
    candidates = sorted((p for p in Path(root).glob('????-??-??') if p.name < str(day.date())), reverse=True)
    for path in candidates:
        status = json.loads((path / 'status.json').read_text(encoding='utf-8'))
        if status['status'] != 'completed':
            continue
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        verify_files(path, manifest['files'])
        report = json.loads((path / 'daily_report.json').read_text(encoding='utf-8'))
        reference = {'path': str(path.resolve()), 'manifest_hash': file_hash(path / 'manifest.json')}
        verify_previous_signals(reference, Path(root).parent, day)
        if report['version_id'] != version_id:
            return None, {**reference, 'status': 'model_version_changed'}
        return pd.read_parquet(path / 'scores.parquet').score.droplevel('datetime'), reference
    return None, None


def run_daily(config, package_path, snapshot_path, receipt, account, as_of, output):
    started = time.monotonic()
    bundle, reference, package = load_frozen_model(package_path, config=config, allow_rejected=True)
    snapshot = load_snapshot(snapshot_path)
    day, next_day, calendar = check_readiness(snapshot, receipt, account, as_of, config)
    features, panel, snapshot, inference = materialize_frozen_features(
        package_path, reference, package, snapshot_path, output, purpose='daily_inference')
    universe = _current_universe(panel, snapshot, calendar, day, local_time(as_of), config.universe, receipt)
    eligibility = universe.xs(day, level='datetime')
    eligible = eligibility.index[eligibility.eligible]
    if not len(eligible):
        raise DataNotReady('no_eligible_inference_instruments')
    current = features.loc[(day, eligible), :]
    diagnostics = feature_diagnostics(reference.frame, current)
    if diagnostics['minimum_column_coverage'] < config.research.coverage_threshold:
        raise QualityError('Daily frozen feature coverage is below the fixed quality threshold')
    scores = predict(bundle, current, as_of=day)
    scores.attrs['training_snapshot_id'] = scores.attrs['snapshot_id']
    scores.attrs['snapshot_id'] = snapshot.snapshot_id
    scores.to_frame().to_parquet(Path(output) / 'scores.parquet')
    events = pd.read_parquet(snapshot.path / 'events.parquet')
    from etf_ml.data.income_supplement import snapshot_income
    _, income_rules = snapshot_income(snapshot)
    paper, weights, history, risk = mark_account(account, panel, calendar, day, events, annualization_days=config.validation.annualization_days, income_rules=income_rules)
    groups = eligibility.tracking_group.to_dict()
    for instrument in weights:
        known = pd.read_parquet(snapshot.path / 'universe.parquet')
        past = known[known.index.get_level_values('datetime') < day]
        if instrument in past.index.get_level_values('instrument'):
            group = past.xs(instrument, level='instrument').tracking_group.dropna()
            if len(group) and pd.isna(groups.get(instrument)):
                groups[instrument] = group.iloc[-1]
    today = panel.xs(day, level='datetime')
    instruments = set(scores.index.get_level_values('instrument')) | set(weights)
    constraints = {'tracking_group': groups,
                   'buyable': {i: i in eligible and i in today.index and bool(today.loc[i, 'tradable']) and
                               bool(today.loc[i].get('buyable', True)) for i in instruments},
                   'sellable': {i: i in today.index and bool(today.loc[i, 'tradable']) and
                                bool(today.loc[i].get('sellable', True)) for i in instruments}}
    due = next_day in rebalance_dates(calendar, config.portfolio.mid_month_day)
    targets, reasons, risk = target_allocation(scores.droplevel('datetime'), weights, constraints, config.portfolio,
                                              risk, config.validation.annualization_days) if due else (dict(weights), {}, risk)
    intentions = order_intents(paper, targets, paper.marks, risk['equity'], panel, day, constraints, config.portfolio) if due else []
    previous, previous_reference = _previous_signal(config.artifact_root / 'operations' / 'signals', day, package['version_id'])
    monitoring = {'inputs': {'cutoff': str(day.date()), 'completed_at': receipt.completed_at,
                            'ingestion_lag_seconds': (local_time(as_of) - local_time(receipt.completed_at)).total_seconds()},
                  'features': diagnostics, 'predictions': prediction_diagnostics(scores.droplevel('datetime'), previous),
                  'current_concentration': concentration(weights, groups), 'target_concentration': concentration(targets, groups),
                  'risk': risk, 'generation_seconds': time.monotonic() - started}
    history.rename('paper_equity').to_frame().to_parquet(Path(output) / 'paper_equity.parquet')
    atomic_json(Path(output) / 'monitoring.json', monitoring)
    atomic_json(Path(output) / 'order_intents.json', intentions)
    report = {'schema_version': 1, 'status': 'completed', 'mode': 'shadow', 'as_of': str(local_time(as_of)),
              'signal_day': str(day.date()), 'execution_day': str(next_day.date()), 'rebalance_due': bool(due),
              'version_id': package['version_id'], 'model_id': package['model_id'], 'feature_set_id': reference.feature_set_id,
              'snapshot_id': snapshot.snapshot_id, 'investment_status': package['investment_status'],
              'experimental': package['investment_status'] != 'passed', 'score_type': bundle.manifest['score_type'],
              'horizon': bundle.manifest['horizon'], 'target_weights': targets, 'cash_weight': max(0., 1 - risk.get('receivable_weight', 0.) - risk.get('income_weight', 0.) - sum(targets.values())),
              'receivable_weight': risk.get('receivable_weight', 0.),
              'income_weight': risk.get('income_weight', 0.),
              'reasons': reasons, 'order_intents': intentions, 'monitoring': monitoring, 'inference_manifest': inference,
              'previous_signal': previous_reference, 'model_fit_performed': False, 'orders_sent': False,
              'account_hash': content_hash(account), 'receipt_hash': content_hash(receipt)}
    atomic_json(Path(output) / 'daily_report.json', report)
    return report


def verify_daily_result(report, package, snapshot, receipt, account, day, next_day, root):
    expected = {'schema_version': 1, 'status': 'completed', 'mode': 'shadow', 'signal_day': str(day.date()),
                'execution_day': str(next_day.date()), 'version_id': package['version_id'], 'model_id': package['model_id'],
                'feature_set_id': package['feature_set_id'], 'snapshot_id': snapshot.snapshot_id,
                'model_fit_performed': False, 'orders_sent': False, 'account_hash': content_hash(account), 'receipt_hash': content_hash(receipt)}
    if any(report.get(key) != value for key, value in expected.items()):
        raise IntegrityError('Daily inference result binding changed')
    valid_statuses = {package['investment_status'], 'not_evaluated'}
    if report.get('investment_status') not in valid_statuses or report.get('experimental') != (report['investment_status'] != 'passed'):
        raise IntegrityError('Daily investment status or experimental marker changed')
    calendar = read_calendar(snapshot.path / 'execution_calendar.txt')
    if (report.get('rebalance_due') != (next_day in rebalance_dates(calendar, package['config']['portfolio']['mid_month_day'])) or
            (not report['rebalance_due'] and report.get('order_intents')) or
            not np.isclose(sum(report['target_weights'].values()) + report['cash_weight'] + report.get('receivable_weight', 0.) + report.get('income_weight', 0.), 1., atol=1e-8) or
            any(not np.isfinite(w) or w < 0 for w in [*report['target_weights'].values(),
                report['cash_weight'], report.get('receivable_weight', 0.)]) or
            not np.isfinite(report.get('income_weight', 0.))):
        raise IntegrityError('Daily schedule or target weights changed')
    if report['inference_manifest'].get('historical_feature_parity') != 'passed':
        raise IntegrityError('Daily inference did not reproduce frozen development features')
    previous = report.get('previous_signal')
    if previous:
        verify_previous_signals(previous, root, day)


def daily_signal(config, *, package_path, snapshot_path, receipt_path, account_path, as_of, run_id):
    config.portfolio.require_resolved()
    _, reference, package = load_frozen_model(package_path, config=config, allow_rejected=True)
    snapshot = load_snapshot(snapshot_path)
    try:
        receipt = IngestionReceipt.model_validate_json(Path(receipt_path).read_text(encoding='utf-8'))
        account = PaperAccount.model_validate_json(Path(account_path).read_text(encoding='utf-8'))
    except ValidationError as exc:
        raise ConfigurationError('Invalid ingestion receipt or paper account schema') from exc
    root = config.artifact_root / 'operations'
    identity = {'version_id': package['version_id'], 'package_manifest_hash': file_hash(Path(package_path) / 'manifest.json'),
                'snapshot_manifest_hash': file_hash(snapshot.path / 'snapshot_manifest.json'),
                'account_hash': content_hash(account), 'receipt_hash': content_hash(receipt), 'config_hash': config.config_hash}
    with RunStore(root / 'requests', run_id, {**identity, 'as_of': str(local_time(as_of))}) as request_run:
        try:
            day, next_day, calendar = check_readiness(snapshot, receipt, account, as_of, config)
            panel = pd.read_parquet(snapshot.path / 'panel.parquet')
            daily_universe = _current_universe(panel, snapshot, calendar, day, local_time(as_of), config.universe, receipt)
            if not daily_universe.eligible.any():
                raise DataNotReady('no_eligible_inference_instruments')
            verify_development_prefix(snapshot, package, reference)
            from etf_ml.data.income_supplement import snapshot_income
            _, income_rules = snapshot_income(snapshot)
            mark_account(account, panel, calendar, day, pd.read_parquet(snapshot.path / 'events.parquet'),
                         annualization_days=config.validation.annualization_days, income_rules=income_rules)
        except DataNotReady as exc:
            result = {'status': 'skipped', 'mode': 'shadow', 'reason': str(exc), 'orders_sent': False, 'order_intents': [],
                      'version_id': package['version_id'], 'snapshot_id': snapshot.snapshot_id, 'as_of': str(local_time(as_of)),
                      'investment_status': package['investment_status'], 'experimental': package['investment_status'] != 'passed',
                      'artifact_path': str(request_run.path), 'exit_code': 3}
            request_run.complete(result)
            return {**result, 'reused': request_run.reused}
        with day_run(root, day, identity) as signal_run:
            if not signal_run.reused:
                atomic_json(signal_run.path / 'ingestion_receipt.json', receipt)
                atomic_json(signal_run.path / 'account_input.json', account)
                worker_request = {'mode': 'daily', 'config': config.model_dump(mode='json'),
                                  'package_path': str(Path(package_path).resolve()), 'snapshot_path': str(snapshot.path),
                                  'receipt': receipt.model_dump(mode='json'), 'account': account.model_dump(mode='json'),
                                  'as_of': str(local_time(as_of)), 'output_root': str(signal_run.path), 'run_id': run_id}
                atomic_json(signal_run.path / 'request.json', worker_request)
                result = NativeBackend(root).run([sys.executable, '-m', 'etf_ml.research.experiment_worker',
                                                  str(signal_run.path / 'request.json')],
                                                 signal_run.path, {}, config.research.limits, trusted=True)
                atomic_json(signal_run.path / 'execution_result.json', result)
                if result.status != 'succeeded':
                    _raise_failure(signal_run.path, result)
                report = json.loads((signal_run.path / 'daily_report.json').read_text(encoding='utf-8'))
                verify_daily_result(report, package, snapshot, receipt, account, day, next_day, root)
                signal_run.complete(report)
            else:
                report = json.loads((signal_run.path / 'daily_report.json').read_text(encoding='utf-8'))
                verify_daily_result(report, package, snapshot, receipt, account, day, next_day, root)
            result = {**report, 'artifact_path': str(signal_run.path), 'manifest_hash': file_hash(signal_run.path / 'manifest.json'),
                      'reused': signal_run.reused, 'exit_code': 0}
            request_run.complete(result)
            return result


def verify_development_prefix(snapshot, package, reference):
    original = load_snapshot(package['snapshot_path'])
    past = pd.read_parquet(original.path / 'panel.parquet').loc[reference.frame.index]
    current = pd.read_parquet(snapshot.path / 'panel.parquet')
    if not past.index.isin(current.index).all():
        raise IntegrityError('Daily input lost frozen development market rows')
    original_universe = pd.read_parquet(original.path / 'universe.parquet')
    current_universe = pd.read_parquet(snapshot.path / 'universe.parquet')
    last_development_day = reference.frame.index.get_level_values('datetime').max()
    original_universe = original_universe[original_universe.index.get_level_values('datetime') <= last_development_day]
    current_universe = current_universe[current_universe.index.get_level_values('datetime') <= last_development_day]
    try:
        assert_frame_equal(past, current.loc[past.index], check_exact=True)
        assert_frame_equal(original_universe, current_universe, check_exact=True)
        original_events = pd.read_parquet(original.path / 'events.parquet')
        current_events = pd.read_parquet(snapshot.path / 'events.parquet')
        original_events = original_events[original_events.datetime <= last_development_day].reset_index(drop=True)
        current_events = current_events[current_events.datetime <= last_development_day].reset_index(drop=True)
        if len(original_events) or len(current_events):
            assert_frame_equal(original_events, current_events, check_exact=True)
        from etf_ml.data.income_supplement import snapshot_income
        old_income, old_rules = snapshot_income(original)
        new_income, new_rules = snapshot_income(snapshot)
        if (old_income is None) != (new_income is None):
            raise IntegrityError('Daily input changed frozen development income coverage')
        if old_income is not None:
            old_income = old_income[old_income.period_end <= last_development_day]
            new_income = new_income[new_income.period_end <= last_development_day]
            assert_frame_equal(old_income, new_income, check_exact=True)
            if old_rules != new_rules or old_income.attrs["income_rules"] != new_income.attrs["income_rules"]:
                raise IntegrityError('Daily input changed frozen income rules')
    except AssertionError as exc:
        raise IntegrityError('Daily input revised frozen development prices, PIT membership corporate actions or income') from exc


@contextmanager
def day_run(root, day, identity):
    root = Path(root)
    day_id = str(day.date())
    with FileLock(root / '.day-locks' / (day_id + '.lock')):
        destination = ensure_within(root / 'signals' / day_id, root / 'signals')
        status_path = destination / 'status.json'
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding='utf-8'))
            if status['status'] != 'completed' and status['config_hash'] != content_hash(identity):
                # A failed computation never freezes a daily signal. Preserve
                # its entire attempt before accepting a corrected input version.
                archived = ensure_within(root / 'failed_signals' / (day_id + '-' + format(time.time_ns(), 'x')), root)
                archived.parent.mkdir(parents=True, exist_ok=True)
                os.replace(destination, archived)
        with RunStore(root / 'signals', day_id, identity, preserve_completed_on_error=True) as run:
            yield run


def verify_previous_signals(reference, root, before_day):
    while reference:
        path = ensure_within(Path(reference['path']), Path(root) / 'signals')
        if path.parent != (Path(root) / 'signals').resolve() or pd.Timestamp(path.name) >= before_day:
            raise IntegrityError('Monitoring history is not an owned earlier daily signal')
        if file_hash(path / 'manifest.json') != reference['manifest_hash']:
            raise IntegrityError('Previous monitoring signal changed')
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        status = json.loads((path / 'status.json').read_text(encoding='utf-8'))
        if status['status'] != 'completed' or status['config_hash'] != manifest['config_hash']:
            raise IntegrityError('Previous monitoring signal is incomplete')
        verify_files(path, manifest['files'])
        report = json.loads((path / 'daily_report.json').read_text(encoding='utf-8'))
        if report['signal_day'] != path.name:
            raise IntegrityError('Monitoring history date changed')
        before_day = pd.Timestamp(path.name)
        reference = report.get('previous_signal')
