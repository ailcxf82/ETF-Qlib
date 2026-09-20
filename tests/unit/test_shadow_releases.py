import json
from pathlib import Path

import pytest

from etf_ml.config import load_config
from etf_ml.errors import ConfigurationError, IntegrityError, QualityError
from etf_ml.operations import releases
from etf_ml.utils import atomic_json


@pytest.fixture
def shadow_releases(tmp_path, monkeypatch):
    config = load_config(overrides={'artifact_root': tmp_path / 'a'})
    packages = []
    for i in range(2):
        version_id = str(i + 1) * 64
        path = config.artifact_root / 'frozen_models' / version_id
        atomic_json(path / 'manifest.json', {'version_id': version_id, 'fixture': 'Synthetic governance only'})
        packages.append((path, {'version_id': version_id, 'investment_status': 'not_evaluated'}))
    inactive = set()
    def load(path, **kwargs):
        path = Path(path)
        if path in inactive:
            raise QualityError('Retired complete package')
        return None, None, next(package for candidate, package in packages if candidate == path)
    monkeypatch.setattr(releases, 'load_frozen_model', load)
    return config, packages, inactive


def test_explicit_activation_restore_and_repeated_old_request_do_not_reactivate(shadow_releases):
    config, packages, _ = shadow_releases
    first = releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Explicit first shadow version')
    assert first['mode'] == 'shadow' and first['experimental']
    second = releases.change_release(config, package_path=packages[1][0], run_id='second', reason='Explicit second shadow version')
    assert releases.load_active_model(config)[2]['version_id'] == packages[1][1]['version_id']
    cached = releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Explicit first shadow version')
    assert cached['reused'] and cached['current_event_id'] == second['event_id']
    assert releases.load_active_model(config)[2]['version_id'] == packages[1][1]['version_id']
    restored = releases.change_release(config, restore=True, run_id='restore', reason='Restore the previous complete package')
    assert restored['frozen_model_path'] == str(packages[0][0])
    assert releases.load_active_model(config)[2]['version_id'] == packages[0][1]['version_id']
    assert len(releases.release_history(config.artifact_root / 'operations/releases')) == 3
    assert releases.change_release(config, restore=True, run_id='restore', reason='Restore the previous complete package')['reused']


def test_release_publication_interruption_recovers_recorded_intent_without_extra_events(shadow_releases, monkeypatch):
    config, packages, _ = shadow_releases
    original = releases.atomic_json
    def interrupt_head(path, value):
        if Path(path).name == 'head.json':
            raise KeyboardInterrupt()
        return original(path, value)
    monkeypatch.setattr(releases, 'atomic_json', interrupt_head)
    with pytest.raises(KeyboardInterrupt):
        releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Explicit shadow choice')
    root = config.artifact_root / 'operations/releases'
    assert not releases.release_history(root) and len(list((root / 'events').glob('*.json'))) == 1
    monkeypatch.setattr(releases, 'atomic_json', original)
    result = releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Explicit shadow choice')
    assert result['status'] == 'completed'
    assert len(releases.release_history(root)) == 1 and len(list((root / 'events').glob('*.json'))) == 1
    assert list((root / 'runs/first').glob('attempt-*'))


def test_restore_rejects_retired_previous_package(shadow_releases):
    config, packages, inactive = shadow_releases
    for i, (path, _) in enumerate(packages):
        releases.change_release(config, package_path=path, run_id=f'activate-{i}', reason='Explicit shadow test')
    inactive.add(packages[0][0])
    with pytest.raises(QualityError): releases.change_release(config, restore=True, run_id='restore', reason='Try previous version')
    assert releases.load_active_model(config)[2]['version_id'] == packages[1][1]['version_id']


@pytest.mark.parametrize('case', ['event', 'package'])
def test_release_history_detects_changed_evidence(shadow_releases, case):
    config, packages, _ = shadow_releases
    result = releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Explicit shadow choice')
    root = config.artifact_root / 'operations/releases'
    if case == 'event':
        path = root / 'events' / (result['event_id'] + '.json')
        event = json.loads(path.read_text())
        atomic_json(path, {**event, 'reason': 'Changed'})
    else:
        atomic_json(packages[0][0] / 'manifest.json', {'changed': True})
    with pytest.raises(IntegrityError): releases.load_active_model(config)


def test_restore_and_activation_require_selection_and_reason(shadow_releases):
    config, packages, _ = shadow_releases
    with pytest.raises(ConfigurationError): releases.load_active_model(config)
    with pytest.raises(ConfigurationError): releases.change_release(config, restore=True, run_id='restore', reason='No earlier version')
    with pytest.raises(ConfigurationError): releases.change_release(config, package_path=packages[0][0], run_id='bad', reason='')
    with pytest.raises(ConfigurationError): releases.change_release(config, run_id='missing', reason='Missing package')


def test_same_release_request_cannot_change_choice_or_reason(shadow_releases):
    config, packages, _ = shadow_releases
    releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Explicit shadow choice')
    with pytest.raises(ConfigurationError):
        releases.change_release(config, package_path=packages[1][0], run_id='first', reason='Explicit shadow choice')
    with pytest.raises(ConfigurationError):
        releases.change_release(config, package_path=packages[0][0], run_id='first', reason='Different reason')
