from __future__ import annotations

import json
from pathlib import Path

from etf_ml.artifacts import RunStore, RUN_ID
from etf_ml.errors import ConfigurationError, IntegrityError
from etf_ml.models.deployment import load_frozen_model
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash


def release_history(root):
    root = Path(root)
    head = root / 'head.json'
    if not head.exists():
        return []
    event_id = json.loads(head.read_text(encoding='utf-8'))['event_id']
    events, visited = [], set()
    while event_id:
        if event_id in visited or len(event_id) != 64 or any(c not in '0123456789abcdef' for c in event_id):
            raise IntegrityError('Invalid operational release history')
        visited.add(event_id)
        path = ensure_within(root / 'events' / (event_id + '.json'), root)
        event = json.loads(path.read_text(encoding='utf-8'))
        if content_hash({k: v for k, v in event.items() if k != 'event_id'}) != event_id or event['event_id'] != event_id:
            raise IntegrityError('Operational release event changed')
        package_path = Path(event['package_path'])
        if file_hash(package_path / 'manifest.json') != event['package_manifest_hash']:
            raise IntegrityError('Released complete model package changed')
        events.append(event)
        event_id = event['previous_event_id']
    events.reverse()
    if [event['sequence'] for event in events] != list(range(1, len(events) + 1)):
        raise IntegrityError('Operational release sequence changed')
    return events


def load_active_model(config):
    events = release_history(config.artifact_root / 'operations' / 'releases')
    if not events:
        raise ConfigurationError('No explicitly activated shadow model')
    event = events[-1]
    bundle, features, package = load_frozen_model(event['package_path'], config=config, allow_rejected=True)
    if package['version_id'] != event['version_id']:
        raise IntegrityError('Operational release model identity changed')
    return bundle, features, package, event


def change_release(config, *, run_id, reason, package_path=None, restore=False):
    if not isinstance(reason, str) or not reason.strip() or not RUN_ID.fullmatch(run_id):
        raise ConfigurationError('Model activation or restore needs an explicit reason and valid run ID')
    if restore == (package_path is not None):
        raise ConfigurationError('Restore takes no replacement weights; activation requires a complete frozen package')
    root = config.artifact_root / 'operations' / 'releases'
    identity = {'operation': 'restore' if restore else 'activate', 'reason': reason.strip(),
                'config_hash': config.config_hash, 'package_path': str(Path(package_path).resolve()) if package_path else None,
                'package_manifest_hash': file_hash(Path(package_path) / 'manifest.json') if package_path else None}
    with RunStore(root / 'runs', run_id, identity) as run, FileLock(root / '.release.lock'):
        events = release_history(root)
        committed = [event for event in events if event['request_run_id'] == run_id]
        if committed:
            if len(committed) != 1 or committed[0]['request_hash'] != content_hash(identity):
                raise IntegrityError('Operational release request changed')
            event = committed[0]
        else:
            if run.reused:
                raise IntegrityError('Completed operational request lost its release event')
            intent_path = root / 'intents' / (run_id + '.json')
            if intent_path.exists():
                event = json.loads(intent_path.read_text(encoding='utf-8'))
                if event['request_hash'] != content_hash(identity):
                    raise IntegrityError('Operational release intent changed')
            else:
                if restore:
                    if len(events) < 2:
                        raise ConfigurationError('No previous complete model version to restore')
                    package_path = events[-2]['package_path']
                _, _, package = load_frozen_model(package_path, config=config, allow_rejected=True)
                payload = {'sequence': len(events) + 1, 'operation': identity['operation'],
                           'request_run_id': run_id, 'request_hash': content_hash(identity), 'reason': reason.strip(),
                           'version_id': package['version_id'], 'package_path': str(Path(package_path).resolve()),
                           'package_manifest_hash': file_hash(Path(package_path) / 'manifest.json'),
                           'previous_event_id': events[-1]['event_id'] if events else None,
                           'note': 'Explicit shadow activation; registry investment decision is unchanged'}
                event = {**payload, 'event_id': content_hash(payload)}
                atomic_json(intent_path, event)
            if (event['previous_event_id'] != (events[-1]['event_id'] if events else None) or
                    content_hash({k: v for k, v in event.items() if k != 'event_id'}) != event['event_id']):
                raise ConfigurationError('Release changed during interrupted publication; inspect the recorded intent')
            _, _, package = load_frozen_model(event['package_path'], config=config, allow_rejected=True)
            if package['version_id'] != event['version_id'] or file_hash(Path(event['package_path']) / 'manifest.json') != event['package_manifest_hash']:
                raise IntegrityError('Operational release intent package changed')
            event_path = root / 'events' / (event['event_id'] + '.json')
            if event_path.exists() and json.loads(event_path.read_text(encoding='utf-8')) != event:
                raise IntegrityError('Operational release event is immutable')
            atomic_json(event_path, event)
            atomic_json(root / 'head.json', {'event_id': event['event_id']})
        _, _, package = load_frozen_model(event['package_path'], config=config, allow_rejected=True)
        result = {'status': 'completed', 'event_id': event['event_id'], 'version_id': event['version_id'],
                  'frozen_model_path': event['package_path'], 'operation': event['operation'],
                  'investment_status': package['investment_status'], 'mode': 'shadow',
                  'experimental': package['investment_status'] != 'passed', 'reason': event['reason'],
                  'current_event_id': events[-1]['event_id'] if committed else event['event_id']}
        run.complete(result)
        return {**result, 'reused': run.reused or bool(committed)}
