import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from etf_ml.cli import main
from etf_ml.config import load_config
from etf_ml.contracts import FoldSpec, ModelSpec, UniversePolicy, ExecutionResult
from etf_ml.data.snapshot import build_snapshot
from etf_ml.errors import ExecutionError, ConfigurationError
from etf_ml.features.baseline import materialize
from etf_ml.models.deployment import freeze_model, load_frozen_model
from etf_ml.research.finalize import freeze_features, compare_models
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.research.session import ResearchSession
from etf_ml.validation import holdout
from etf_ml.validation.usage import HoldoutUsageStore
from etf_ml.utils import file_hash, source_hashes

pytestmark = pytest.mark.qlib


@pytest.mark.parametrize("minimum_return,expected_status", [(-.99, "passed"), (1., "failed")])
def test_actual_independent_holdout_retries_then_caches_fixed_model_and_blocks_other_candidate(
        source_spec, calendar, tmp_path, capsys, monkeypatch, minimum_return, expected_status):
    boundary = str(calendar[140].date())
    source_spec.holdout_start = boundary
    fold = FoldSpec(name="fixture", train={"start": str(calendar[0].date()), "end": str(calendar[79].date())},
        early_stop={"start": str(calendar[80].date()), "end": str(calendar[99].date())},
        selection={"start": str(calendar[100].date()), "end": str(calendar[139].date())})
    # Synthetic thresholds and independence are declared before any results.
    config = load_config(overrides={"artifact_root": tmp_path / "a",
        "portfolio": {"k_mode": "fraction", "minimum_commission": 0, "liquidity_mode": "participation", "risk_mode": "max_drawdown"},
        "validation": {"folds": [fold.model_dump()], "holdout_start": boundary, "holdout_independent": True},
        "research": {"budget_mode": "free_only", "seeds": [42, 43]},
        "acceptance": {"minimum_net_return": minimum_return, "minimum_excess_return": -1., "maximum_drawdown": .12,
            "maximum_annualized_volatility": 1., "maximum_execution_cost_over_initial_equity": 1., "minimum_effective_dates": 10}})
    config.data = source_spec
    config.universe = UniversePolicy(minimum_listing_days=0, liquidity_lookback=1)
    config.models = [ModelSpec(name="ridge"), ModelSpec(name="lightgbm", constructor={"num_boost_round": 4,
        "early_stopping_rounds": 2, "min_data_in_leaf": 5, "num_threads": 1}, fit={"verbose_eval": 0}),
        ModelSpec(name="xgboost", constructor={"max_depth": 3, "eta": .1, "nthread": 1},
                  fit={"num_boost_round": 4, "early_stopping_rounds": 2, "verbose_eval": False})]
    snapshot = build_snapshot(source_spec.source, source_spec, config.universe)
    panel = pd.read_parquet(snapshot.path / "research" / "panel.parquet")
    base = materialize({"snapshot_id": snapshot.snapshot_id}, panel)
    protocol = ComparisonProtocol(snapshot_id=snapshot.snapshot_id, baseline_feature_set_id=base.feature_set_id,
        label=config.label, universe=config.universe, validation=config.validation, portfolio=config.portfolio,
        research=config.research, model=config.models[1], cost_multipliers=[3.], stress_min_excess_return=-1.)
    session = ResearchSession(config, snapshot.path, protocol, root=tmp_path / "a" / "research" / "fixture")
    feature_freeze = freeze_features(session, config.artifact_root / "final_reviews", run_id="features")
    assert feature_freeze["status"] == "accepted"
    compare_models(config, snapshot, feature_freeze["freeze_path"], config.artifact_root / "comparisons", run_id="compare")
    comparison_path = config.artifact_root / "comparisons/runs/compare/comparison.json"
    selected = freeze_model(config, feature_freeze["freeze_path"], comparison_path,
        model="ridge", fold="fixture", seed=42, reason="Synthetic fixed choice before holdout", run_id="freeze")
    package_path = Path(selected["frozen_model_path"])
    research_before = source_hashes(session.root)
    weights_before = {p: file_hash(p) for p in config.artifact_root.rglob("bundle.pkl")}
    original_run = holdout.NativeBackend.run
    monkeypatch.setattr(holdout.NativeBackend, "run", lambda *args, **kwargs:
        ExecutionResult(status="failed", returncode=None, stdout="", stderr="", duration_seconds=0., reason="timeout"))
    with pytest.raises(ExecutionError): holdout.evaluate_holdout(config, package_path, run_id="technical-retry")
    usage = HoldoutUsageStore(config.artifact_root / "final_acceptance/usage")
    claim_path = next((usage.root / "claims").glob("*.json"))
    usage_id = json.loads(claim_path.read_text())["usage_id"]
    assert [event["status"] for event in usage.history(usage_id)] == ["started", "technical_failed"]
    monkeypatch.setattr(holdout.NativeBackend, "run", original_run)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
    capsys.readouterr()
    args = ["evaluate-holdout", "--config", str(config_path), "--frozen-model", str(package_path), "--run-id", "holdout-cli"]
    # If decision publication is interrupted after the actual portfolio run,
    # resume from that immutable report instead of evaluating the data again.
    from etf_ml.registry.model_versions import ModelVersionRegistry
    original_transition = ModelVersionRegistry.transition
    def interrupt_commit(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(ModelVersionRegistry, "transition", interrupt_commit)
    assert main(args) == 130
    capsys.readouterr()
    assert usage.history(usage_id)[-1]["details"]["phase"] == "decision_commit"
    completed_data_manifest = config.artifact_root / "final_acceptance/runs/technical-retry/manifest.json"
    completed_data_time = completed_data_manifest.stat().st_mtime_ns
    monkeypatch.setattr(ModelVersionRegistry, "transition", original_transition)
    assert main(args) == (0 if expected_status == "passed" else 5)
    summary = json.loads(capsys.readouterr().out)
    assert summary["holdout_reused"] is True
    assert completed_data_manifest.stat().st_mtime_ns == completed_data_time
    assert summary["investment_status"] == expected_status and summary["quality_status"] == "passed"
    artifact = Path(summary["holdout_path"])
    result = json.loads((artifact / "holdout_report.json").read_text())
    assert result["model_fit_performed"] is False and result["stage"] == "independent_holdout"
    assert result["portfolio"]["effective_dates"] == len(calendar) - 140
    assert set(result["cost_stress"]) == {"3.0"}
    assert result["cost_stress"]["3.0"]["effective_dates"] == len(calendar) - 140
    assert result["inference_manifest"]["historical_feature_parity"] == "passed"
    assert result["stability"]["status"] == "inconclusive" and result["stability"]["block_length"] == 20
    assert sum(row["effective_dates"] for row in result["by_month"]) == len(calendar) - 140
    assert result["acceptance"] == config.acceptance.model_dump(mode="json")
    assert list(artifact.glob("attempt-*"))
    assert [event["status"] for event in usage.history(usage_id)] == ["started", "technical_failed", "started", "technical_failed", "completed"]
    _, _, final_package = load_frozen_model(package_path, config=config, allow_rejected=True)
    assert final_package["registry_state"] == ("accepted" if expected_status == "passed" else "rejected")
    assert weights_before == {p: file_hash(p) for p in weights_before}
    assert research_before == source_hashes(session.root)
    manifest_time = (artifact / "manifest.json").stat().st_mtime_ns
    assert main(args) == (0 if expected_status == "passed" else 5)
    assert json.loads(capsys.readouterr().out)["reused"]
    cached = holdout.evaluate_holdout(config, package_path, run_id="new-name-same-version")
    assert cached["reused"] and cached["artifact_path"] == str(artifact)
    assert (artifact / "manifest.json").stat().st_mtime_ns == manifest_time
    alternative = freeze_model(config, feature_freeze["freeze_path"], comparison_path,
        model="lightgbm", fold="fixture", seed=43, reason="Synthetic alternative fixed choice", run_id="alternative")
    with pytest.raises(ConfigurationError, match="already consumed"):
        holdout.evaluate_holdout(config, Path(alternative["frozen_model_path"]), run_id="forbidden-reuse")
    _verify_actual_daily_and_complete_version_restore(config, source_spec, snapshot, config_path, package_path,
        Path(alternative['frozen_model_path']), calendar, tmp_path, capsys, monkeypatch, expected_status)
    # Successful/failed command reuse always rechecks the actual ledger files.
    trades = artifact / "portfolio/trades.parquet"
    trades.write_bytes(trades.read_bytes() + b"corrupt")
    assert main(args) == 5

def _verify_actual_daily_and_complete_version_restore(config, source_spec, snapshot, config_path, package_path,
        alternative_path, calendar, tmp_path, capsys, monkeypatch, expected_status):
    # Synthetic dates/quotes validate actual frozen Qlib inference and CLI;
    # they do not constitute ten real trading days or an investment result.
    from etf_ml.operations import daily, releases
    from etf_ml.artifacts import RunStore
    from etf_ml.data.source import QlibBinSource, encode_provider
    from etf_ml.data.calendar import read_calendar
    from etf_ml.utils import atomic_json
    original_weights = {path: file_hash(path) for path in config.artifact_root.rglob('bundle.pkl')}
    original_inputs = source_hashes(source_spec.source)
    activate = ['activate-model', '--config', str(config_path), '--frozen-model', str(package_path),
                '--selection-reason', 'Explicit synthetic shadow choice', '--run-id', 'shadow-first']
    assert main(activate) == 0
    assert json.loads(capsys.readouterr().out)['experimental'] == (expected_status != 'passed')
    assert main(['activate-model', '--config', str(config_path), '--frozen-model', str(alternative_path),
        '--selection-reason', 'Explicit synthetic alternate shadow choice', '--run-id', 'shadow-alternate']) == 0
    capsys.readouterr()
    assert releases.load_active_model(config)[2]['version_id'] == alternative_path.name
    assert main(['restore-model', '--config', str(config_path), '--selection-reason', 'Restore the full first package',
                 '--run-id', 'shadow-restore']) == 0
    capsys.readouterr()
    assert releases.load_active_model(config)[2]['version_id'] == package_path.name
    assert len(releases.release_history(config.artifact_root / 'operations/releases')) == 3
    mapped = QlibBinSource(source_spec.source).read(source_spec.fields).rename(columns=source_spec.fields)
    future = read_calendar(source_spec.trusted_calendar)
    completed_paths = []
    for i, (date, expected_due) in enumerate([('2023-08-14', True), ('2023-08-30', True), ('2023-09-01', False)]):
        day = pd.Timestamp(date)
        partial = mapped[mapped.index.get_level_values('datetime') <= day]
        partial_calendar = calendar[calendar <= day]
        incremental_spec = source_spec.model_copy(deep=True)
        incremental_spec.source = tmp_path / ('incremental-raw-' + date)
        incremental_spec.artifact_root = tmp_path / 'incremental-snapshots'
        encode_provider(partial, partial_calendar, incremental_spec.source, {name: name for name in partial},
                        future_calendar=future)
        incremental = build_snapshot(incremental_spec.source, incremental_spec, config.universe)
        receipt_path, account_path = tmp_path / f'receipt-{i}.json', tmp_path / f'account-{i}.json'
        receipt = {'schema_version': 1, 'snapshot_id': incremental.snapshot_id,
            'snapshot_manifest_hash': file_hash(incremental.path / 'snapshot_manifest.json'), 'trading_day': date,
            'completed_at': date + 'T16:00:00+08:00', 'complete': True, 'missing_instruments': [],
            'expected_instruments': ['510300.SH', '510500.SH', '159915.SZ']}
        account = {'schema_version': 1, 'trading_day': date, 'cash': 500000, 'shares': {},
            'equity_history': [{'date': str(partial_calendar[-2].date()), 'equity': 500000}]}
        atomic_json(receipt_path, receipt)
        atomic_json(account_path, account)
        args = ['daily-signal', '--config', str(config_path), '--snapshot', str(incremental.path),
                '--ingestion', str(receipt_path), '--account', str(account_path), '--as-of', date + 'T16:30:00+08:00',
                '--run-id', 'daily-' + str(i)]
        if i == 0:
            bad_path = tmp_path / 'incomplete-receipt.json'
            atomic_json(bad_path, {**receipt, 'complete': False})
            skipped = list(args)
            skipped[skipped.index('--ingestion') + 1] = str(bad_path)
            skipped[-1] = 'daily-unready'
            assert main(skipped) == 3
            skipped_report = json.loads(capsys.readouterr().out)
            assert skipped_report['status'] == 'skipped' and not skipped_report['order_intents']
            assert not (config.artifact_root / 'operations/signals' / date).exists()
            original_run = daily.NativeBackend.run
            monkeypatch.setattr(daily.NativeBackend, 'run', lambda *args, **kwargs:
                ExecutionResult(status='failed', returncode=None, stdout='', stderr='', duration_seconds=0., reason='timeout'))
            assert main(args) == 4
            capsys.readouterr()
            monkeypatch.setattr(daily.NativeBackend, 'run', original_run)
            original_complete = RunStore.complete
            def interrupt_request(store, *values, **kwargs):
                if store.path == config.artifact_root / 'operations/requests/daily-0':
                    raise KeyboardInterrupt()
                return original_complete(store, *values, **kwargs)
            monkeypatch.setattr(RunStore, 'complete', interrupt_request)
            assert main(args) == 130
            capsys.readouterr()
            signal_path = config.artifact_root / 'operations/signals' / date
            committed_time = (signal_path / 'manifest.json').stat().st_mtime_ns
            assert json.loads((signal_path / 'status.json').read_text())['status'] == 'completed'
            monkeypatch.setattr(RunStore, 'complete', original_complete)
        assert main(args) == 0
        report = json.loads(capsys.readouterr().out)
        assert report['rebalance_due'] == expected_due
        assert report['investment_status'] == expected_status
        assert report['experimental'] == (expected_status != 'passed')
        assert report['model_fit_performed'] is False and report['orders_sent'] is False
        assert report['inference_manifest']['historical_feature_parity'] == 'passed'
        assert report['monitoring']['features']['minimum_column_coverage'] == 1.
        assert report['snapshot_id'] == incremental.snapshot_id
        path = Path(report['artifact_path'])
        actual_scores = pd.read_parquet(path / 'scores.parquet').score
        assert actual_scores.index.get_level_values('datetime').unique().tolist() == [day]
        bundle, _, _ = load_frozen_model(package_path, config=config, allow_rejected=True)
        from etf_ml.models.predict import predict
        from pandas.testing import assert_series_equal
        features = pd.read_parquet(path / 'features.parquet')
        expected_scores = predict(bundle, features.loc[actual_scores.index], as_of=day)
        assert_series_equal(actual_scores, expected_scores, check_exact=False, rtol=1e-10, atol=1e-10)
        if expected_due:
            assert report['order_intents']
            for intent in report['order_intents']:
                assert intent['kind'] == 'shadow_order_intent' and intent['price_basis'] == 'signal_day_raw_close'
                assert intent['amount'] <= intent['historical_participation_notional'] + 1e-8
                assert intent['commission'] == pytest.approx(intent['amount'] * config.portfolio.commission_rate)
        else:
            assert report['target_weights'] == {} and report['cash_weight'] == 1.
            assert report['order_intents'] == []
        if i == 0:
            assert report['reused']
            assert (path / 'manifest.json').stat().st_mtime_ns == committed_time
            assert list(path.glob('attempt-*'))
        if i > 0:
            assert report['monitoring']['predictions']['stability_status'] == 'computed'
            assert report['previous_signal']['path'] == str(completed_paths[-1])
        committed_time = (path / 'manifest.json').stat().st_mtime_ns
        assert main(args) == 0
        assert json.loads(capsys.readouterr().out)['reused']
        assert (path / 'manifest.json').stat().st_mtime_ns == committed_time
        alternate_name = list(args)
        alternate_name[-1] += '-another-name'
        assert main(alternate_name) == 0
        assert json.loads(capsys.readouterr().out)['artifact_path'] == str(path)
        completed_paths.append(path)
    assert original_inputs == source_hashes(source_spec.source)
    assert original_weights == {path: file_hash(path) for path in original_weights}
    # A changed account cannot overwrite the completed daily intent.
    atomic_json(account_path, {**account, 'cash': 499000})
    changed = list(args)
    changed[-1] = 'changed-account'
    assert main(changed) == 2
    capsys.readouterr()
    atomic_json(account_path, account)
    switched = list(args) + ['--frozen-model', str(alternative_path)]
    switched[switched.index('--run-id') + 1] = 'different-version-same-day'
    assert main(switched) == 2
    capsys.readouterr()
    rewritten_events = pd.DataFrame([{'event_id': 'rewritten-development-event', 'datetime': calendar[120],
        'instrument': '510300.SH', 'cash_per_share': .1, 'share_multiplier': 1.}])
    event_path = tmp_path / 'rewritten-development-events.parquet'
    rewritten_events.to_parquet(event_path, index=False)
    changed_spec = incremental_spec.model_copy(deep=True)
    changed_spec.events_path = event_path
    # Build internally consistent rewritten source so the daily entry still tests
    # frozen-history rejection rather than failing source accounting first.
    from etf_ml.data.source import QlibBinSource, encode_provider
    changed_reader = QlibBinSource(changed_spec.source)
    rewritten = changed_reader.read(changed_spec.fields)
    instrument_history = rewritten.xs('510300.SH', level='instrument')
    event_day = calendar[120]
    previous_close = float(instrument_history.loc[instrument_history.index < event_day, 'close'].iloc[-1])
    multiplier = previous_close / (previous_close - .1)
    rewritten.loc[(slice(event_day, None), '510300.SH'), 'factor'] *= multiplier
    rewritten.loc[(event_day, '510300.SH'), 'reference_close'] = previous_close - .1
    rewritten.loc[(event_day, '510300.SH'), 'change'] = (
        rewritten.loc[(event_day, '510300.SH'), 'close'] / (previous_close - .1) - 1)
    rewritten_source = tmp_path / 'consistent-rewritten-provider'
    encode_provider(rewritten, changed_reader.calendar, rewritten_source,
                    {field: canonical for canonical, field in changed_spec.fields.items()})
    changed_spec.source = rewritten_source
    changed_snapshot = build_snapshot(changed_spec.source, changed_spec, config.universe)
    changed_receipt = {**receipt, 'snapshot_id': changed_snapshot.snapshot_id,
        'snapshot_manifest_hash': file_hash(changed_snapshot.path / 'snapshot_manifest.json')}
    changed_receipt_path = tmp_path / 'rewritten-event-receipt.json'
    atomic_json(changed_receipt_path, changed_receipt)
    assert main(['daily-signal', '--config', str(config_path), '--snapshot', str(changed_snapshot.path),
        '--frozen-model', str(package_path), '--ingestion', str(changed_receipt_path), '--account', str(account_path),
        '--as-of', date + 'T16:30:00+08:00', '--run-id', 'rewritten-development-event']) == 5
    assert 'frozen development' in json.loads(capsys.readouterr().err)['message']
    from etf_ml.registry.model_versions import ModelVersionRegistry
    ModelVersionRegistry(config.artifact_root / 'model_versions').transition(alternative_path.name, 'retired', reasons=['Synthetic restore guard'])
    assert main(['restore-model', '--config', str(config_path), '--selection-reason', 'Retired alternate must not restore',
                 '--run-id', 'retired-restore']) == 5
    capsys.readouterr()
    assert releases.load_active_model(config)[2]['version_id'] == package_path.name
    # Reused monitoring verifies the complete referenced earlier signal chain.
    first_scores = completed_paths[0] / 'scores.parquet'
    first_scores.write_bytes(first_scores.read_bytes() + b'corrupt')
    assert main(args) == 5
    capsys.readouterr()
