"""Deterministic tests of reviewed official-notice calendar extraction."""
import importlib.util
from pathlib import Path

import pytest

from etf_ml.errors import IntegrityError

spec = importlib.util.spec_from_file_location('calendar_evidence', Path(__file__).resolve().parents[2] / 'scripts/build_sse_calendar_evidence.py')
calendar_evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calendar_evidence)


def html(body, publication='2023-12-26'):
    return ('<html><div>' + publication + '</div>' + body + '</html>').encode('utf-8')


def test_holiday_range_excludes_reopening_and_makeup_weekend():
    raw = html('<p>春节：2月9日（星期五）至2月17日（星期六）休市，2月19日起照常开市。另外，2月18日为周末休市。</p>')
    periods, _ = calendar_evidence.parse_notice(raw, 2024, '2023-12-26')
    assert periods == {'2024-02-09/2024-02-17'}
    dates = calendar_evidence.calendar_dates(periods, '2024-02-08', '2024-02-20')
    assert dates == ['2024-02-08', '2024-02-19', '2024-02-20']


def test_cross_year_and_single_date_are_distinct():
    raw = html('<p>元旦：2023年12月30日至1月1日休市，1月2日起开市。</p><p>端午节：6月10日休市，6月11日起开市。</p>')
    periods, _ = calendar_evidence.parse_notice(raw, 2024, '2023-12-26')
    assert periods == {'2023-12-30/2024-01-01', '2024-06-10/2024-06-10'}


@pytest.mark.parametrize('body', ['<p>元旦：1月3日至1月1日休市。</p>', '<p>春节：1月1日至3月1日休市。</p>'])
def test_ambiguous_intervals_are_rejected(body):
    with pytest.raises(IntegrityError):
        calendar_evidence.parse_notice(html(body), 2024, '2023-12-26')


def test_publication_date_must_be_present():
    with pytest.raises(IntegrityError):
        calendar_evidence.parse_notice(html('<p>元旦：1月1日休市。</p>', '2023-12-25'), 2024, '2023-12-26')


def test_nested_table_and_paragraph_do_not_duplicate_closures():
    raw = html('<table><tr><td><p>元旦：1月1日休市。</p></td></tr></table>')
    periods, _ = calendar_evidence.parse_notice(raw, 2024, '2023-12-26')
    assert periods == {'2024-01-01/2024-01-01'}


def test_emergency_amendment_changes_actual_calendar_without_backfilling_announcement():
    original = calendar_evidence.NOTICES[0][3]
    amended = [*original, calendar_evidence.AMENDMENT['added_closure']]
    assert calendar_evidence.AMENDMENT['published_date'] == '2020-01-27'
    assert '2020-01-31' in calendar_evidence.calendar_dates(original, '2020-01-20', '2020-02-03')
    assert '2020-01-31' not in calendar_evidence.calendar_dates(amended, '2020-01-20', '2020-02-03')
    assert calendar_evidence.calendar_dates(amended, '2020-01-20', '2020-02-03')[-1] == '2020-02-03'


def combined(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    location = Path(__file__).resolve().parents[2] / 'scripts/build_exchange_calendar_evidence.py'
    module_spec = importlib.util.spec_from_file_location('exchange_evidence', location)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def test_calendar_publication_is_idempotent(monkeypatch, tmp_path):
    module = combined(monkeypatch)
    report = {'kind': 'synthetic_publication_test', 'g0_status': 'not_passed'}
    blobs, calendar = {'sources/notice.html': b'fixture'}, '2024-01-02\n'
    identity, path = module.publish(tmp_path, report, blobs, calendar)
    timestamp = (path / 'trusted_calendar.txt').stat().st_mtime_ns
    assert module.publish(tmp_path, report, blobs, calendar) == (identity, path)
    assert (path / 'trusted_calendar.txt').stat().st_mtime_ns == timestamp


@pytest.mark.parametrize('name', ['sources/notice.html', 'trusted_calendar.txt', 'calendar_evidence.json'])
def test_calendar_publication_rejects_archived_corruption(monkeypatch, tmp_path, name):
    module = combined(monkeypatch)
    report = {'kind': 'synthetic_publication_test'}
    blobs, calendar = {'sources/notice.html': b'fixture'}, '2024-01-02\n'
    _, path = module.publish(tmp_path, report, blobs, calendar)
    (path / name).write_bytes(b'{}')
    with pytest.raises(IntegrityError):
        module.publish(tmp_path, report, blobs, calendar)


def test_calendar_publication_interruption_never_publishes_partial_identity(monkeypatch, tmp_path):
    module = combined(monkeypatch)
    report = {'kind': 'synthetic_publication_test'}
    real_replace = module.os.replace
    def interrupt(source, destination):
        if Path(source).is_dir():
            raise KeyboardInterrupt()
        return real_replace(source, destination)
    monkeypatch.setattr(module.os, 'replace', interrupt)
    with pytest.raises(KeyboardInterrupt):
        module.publish(tmp_path, report, {'sources/notice.html': b'fixture'}, '2024-01-02\n')
    assert not (tmp_path / module.content_hash(report)).exists()
    assert len(list(tmp_path.glob('.calendar-stage-*'))) == 1
    monkeypatch.setattr(module.os, 'replace', real_replace)
    _, path = module.publish(tmp_path, report, {'sources/notice.html': b'fixture'}, '2024-01-02\n')
    assert (path / 'calendar_evidence.json').is_file()


def test_calendar_publication_disallows_source_paths_outside_stage(monkeypatch, tmp_path):
    from etf_ml.errors import ConfigurationError
    module = combined(monkeypatch)
    with pytest.raises(ConfigurationError):
        module.publish(tmp_path, {'kind': 'synthetic_publication_test'}, {'../outside.html': b'fixture'}, '2024-01-02\n')
    assert not (tmp_path / 'outside.html').exists()


def test_verified_calendar_config_keeps_other_g0_conditions_blocked():
    from etf_ml.config import load_config
    from etf_ml.data.snapshot import build_snapshot
    from etf_ml.errors import ConfigurationError
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / 'configs/data/tushare_calendar_evidence.yaml')
    assert config.data.trusted_calendar.name == 'trusted_calendar.txt'
    assert config.data.volume_unit == 'lots' and config.data.amount_multiplier == 1000
    assert config.data.fields['change'] == 'pct_chg'
    assert config.data.fields['factor'] == 'factor'
    assert not config.data.point_in_time_metadata and config.data.metadata_path is None
    assert config.data.benchmark_path is None
    assert config.portfolio.initial_cash == 500000
    assert config.portfolio.commission_rate == .003 and config.portfolio.slippage_rate == .0003
    assert config.portfolio.k == .05 and config.portfolio.k_mode == "fraction"
    assert config.portfolio.risk == .12 and config.portfolio.risk_mode == "max_drawdown"
    assert config.portfolio.liquidity == .2 and config.portfolio.liquidity_mode == "participation"
    assert config.research.budget_mode is None and config.portfolio.minimum_commission is None
    with pytest.raises(ConfigurationError, match='PIT metadata'):
        build_snapshot(config.data.source, config.data, config.universe)
