"""Canonical share conversions backed by explicit primary announcements."""
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd

from etf_ml.data.actions import validate_events
from etf_ml.errors import QualityError
from etf_ml.utils import ensure_within, file_hash


def load_share_supplement(path, calendar, instruments):
    path = Path(path).resolve()
    initial_hash = file_hash(path)
    original = pd.read_parquet(path)
    required = {'event_id', 'instrument', 'datetime', 'cash_per_share', 'share_multiplier',
                'share_rounding', 'record_date', 'available_time', 'evidence_id'}
    if original.empty or not required.issubset(original):
        raise QualityError('Share supplement requires explicit conversion, timing, rounding and evidence')
    if original.share_rounding.isna().any():
        raise QualityError('Share conversion rounding must be explicit')
    events, _ = validate_events(original, calendar, instruments)
    if not events.cash_per_share.eq(0).all() or events.share_multiplier.eq(1).any():
        raise QualityError('Share supplement must contain only explicit noncash conversions')
    for event in events.itertuples(index=False):
        location = calendar.get_indexer([event.datetime])[0]
        if location <= 0 or event.record_date != calendar[location - 1]:
            raise QualityError('Share conversion must apply to the preceding trading close')
    evidence = original.attrs.get('primary_evidence')
    if not isinstance(evidence, list) or not evidence:
        raise QualityError('Share conversion primary evidence missing')
    hashes = {str(path): initial_hash}
    ids = set()
    for proof in evidence:
        if not isinstance(proof, dict) or set(proof) != {'evidence_id', 'path', 'url', 'sha256'}:
            raise QualityError('Invalid share conversion evidence')
        if proof['evidence_id'] in ids or urlsplit(proof['url']).scheme != 'https' or not urlsplit(proof['url']).netloc:
            raise QualityError('Share evidence identity or source URL invalid')
        ids.add(proof['evidence_id'])
        if Path(proof['path']).is_absolute():
            raise QualityError('Share evidence path must be relative')
        document = ensure_within(path.parent / proof['path'], path.parent)
        if not document.is_file() or file_hash(document) != proof['sha256']:
            raise QualityError('Share conversion evidence missing or hash changed')
        hashes[str(document)] = proof['sha256']
    if not events.evidence_id.isin(ids).all():
        raise QualityError('Share conversion references unknown primary evidence')
    factor_hashes = original.attrs.get('source_factor_hashes')
    if not isinstance(factor_hashes, dict) or set(factor_hashes) != set(events.instrument):
        raise QualityError('Share conversion needs exact source factor response bindings')
    if any(not isinstance(v, str) or len(v) != 64 or any(c not in '0123456789abcdef' for c in v)
           for v in factor_hashes.values()):
        raise QualityError('Invalid share conversion source factor hash')
    if file_hash(path) != initial_hash:
        raise QualityError('Share supplement changed while loading')
    events.attrs = dict(original.attrs)
    _cash_overrides(events)
    return events, hashes


def validate_share_binding(events, supplemental, hashes):
    if not isinstance(events, pd.DataFrame) or events.attrs.get('share_event_input_hashes') != hashes:
        raise QualityError('Mapped events are not bound to the share conversion supplement')
    columns = ['event_id', 'instrument', 'datetime', 'cash_per_share', 'share_multiplier',
               'share_rounding', 'record_date', 'available_time', 'evidence_id', 'sequence']
    if not set(columns).issubset(events):
        raise QualityError('Mapped events lost explicit share conversion fields')
    for event in supplemental.itertuples(index=False):
        matched = events[events.event_id.eq(event.event_id)]
        if len(matched) != 1 or any(matched.iloc[0][c] != getattr(event, c) for c in columns):
            raise QualityError('Mapped events changed or lost the explicit share conversion')
    rules = _cash_overrides(supplemental)
    for rule in rules:
        index = _check_cash_override(events, supplemental, rule)
        row = events.loc[index]
        if (row.sequence != rule['sequence'] or row.get('cash_share_basis') != 'post_record_conversions' or
                row.get('cash_basis_evidence_id') != rule['evidence_id']):
            raise QualityError('Mapped cash basis or sequence changed')
    if 'cash_share_basis' in events:
        post = events[events.cash_share_basis.eq('post_record_conversions')]
        if set(post.event_id) != {rule['event_id'] for rule in rules}:
            raise QualityError('Mapped cash basis not backed by the share announcement input')


def _cash_overrides(supplemental):
    overrides = supplemental.attrs.get('cash_basis_overrides', [])
    if not isinstance(overrides, list):
        raise QualityError('Invalid cash basis override list')
    seen = set()
    evidence_ids = {p['evidence_id'] for p in supplemental.attrs.get('primary_evidence', [])}
    for rule in overrides:
        required = {'event_id', 'share_event_id', 'evidence_id', 'cash_per_share', 'record_date', 'pay_date', 'sequence'}
        if not isinstance(rule, dict) or set(rule) != required:
            raise QualityError('Invalid explicit cash basis override')
        if not isinstance(rule['event_id'], str) or not rule['event_id'] or rule['event_id'] in seen:
            raise QualityError('Ambiguous cash basis override identity')
        seen.add(rule['event_id'])
        share = supplemental[supplemental.event_id.eq(rule['share_event_id'])]
        if len(share) != 1 or rule['evidence_id'] not in evidence_ids:
            raise QualityError('Cash basis override references unknown announcement or conversion')
        if share.iloc[0].evidence_id != rule['evidence_id']:
            raise QualityError('Cash basis evidence must bind the registered conversion')
        if (isinstance(rule['sequence'], bool) or not isinstance(rule['sequence'], int) or
                rule['sequence'] <= share.iloc[0].sequence):
            raise QualityError('Cash basis override must follow the declared conversion')
        if (isinstance(rule['cash_per_share'], bool) or not isinstance(rule['cash_per_share'], (int, float)) or
                not 0 < rule['cash_per_share'] < float('inf')):
            raise QualityError('Invalid cash basis override cash')
        try:
            record, payment = pd.Timestamp(rule['record_date']), pd.Timestamp(rule['pay_date'])
            if (pd.isna(record) or pd.isna(payment) or record.tzinfo is not None or payment.tzinfo is not None or
                    record != record.normalize() or payment != payment.normalize() or
                    record != share.iloc[0].record_date or payment < share.iloc[0].datetime):
                raise ValueError('Invalid cash basis dates')
        except (ValueError, TypeError) as error:
            raise QualityError('Invalid cash basis override dates') from error
    return overrides


def _check_cash_override(events, supplemental, rule):
    share = supplemental[supplemental.event_id.eq(rule['share_event_id'])].iloc[0]
    matched = events[events.event_id.eq(rule['event_id'])]
    if len(matched) != 1:
        raise QualityError('Cash basis override must match exactly one original dividend')
    row = matched.iloc[0]
    if (row.instrument != share.instrument or row.datetime != share.datetime or row.share_multiplier != 1 or
            row.cash_per_share != rule['cash_per_share'] or row.record_date != pd.Timestamp(rule['record_date']) or
            row.pay_date != pd.Timestamp(rule['pay_date'])):
        raise QualityError('Cash basis override original economics or dates changed')
    return matched.index[0]


def merge_share_events(events, supplemental, hashes, calendar, instruments):
    mapping = dict(events.attrs)
    combined = pd.concat([events, supplemental], ignore_index=True) if len(events) else supplemental.copy()
    for rule in _cash_overrides(supplemental):
        index = _check_cash_override(combined, supplemental, rule)
        combined.loc[index, 'sequence'] = rule['sequence']
        combined.loc[index, 'cash_share_basis'] = 'post_record_conversions'
        combined.loc[index, 'cash_basis_evidence_id'] = rule['evidence_id']
    combined, _ = validate_events(combined, calendar, instruments)
    combined.attrs = mapping
    combined.attrs['share_event_input_hashes'] = hashes
    combined.attrs['share_actions_included'] = True
    validate_share_binding(combined, supplemental, hashes)
    return combined
