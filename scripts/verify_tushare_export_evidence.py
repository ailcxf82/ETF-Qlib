"""Read-only verification of the local Tushare ETF CSV -> bin export.

This proves exported field identity and unit evidence, not G0 readiness.
No API client, credentials, downloader, repair or source writes are used.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from etf_ml.data.source import QlibBinSource
from etf_ml.errors import IntegrityError
from etf_ml.utils import atomic_json, content_hash, ensure_within, file_hash, source_hashes


FIELDS = ['open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'volume', 'amount']


def verify(source, csv_root, exporter, output, *, limit=0):
    source, csv_root, exporter = Path(source), Path(csv_root), Path(exporter)
    project = Path(__file__).resolve().parents[1]
    output = ensure_within(Path(output), project)
    before = source_hashes(source)
    exporter_hash = file_hash(exporter)
    reader = QlibBinSource(source)
    members = reader.instruments.instrument.tolist()
    if limit:
        members = members[:limit]
    results, csv_hashes = [], {}
    for member in members:
        path = csv_root / (member.upper() + '.csv')
        if not path.is_file():
            results.append({'instrument': member, 'status': 'missing_csv'})
            continue
        csv_hashes[str(path.resolve())] = file_hash(path)
        raw = pd.read_csv(path, usecols=['datetime', 'instrument', *FIELDS])
        raw['datetime'] = pd.to_datetime(raw.datetime, errors='raise')
        raw['instrument'] = raw.instrument.str.upper()
        if raw[['datetime', 'instrument']].duplicated().any() or set(raw.instrument) != {member.upper()}:
            results.append({'instrument': member, 'status': 'ambiguous_csv_index'})
            continue
        for field in FIELDS:
            raw[field] = pd.to_numeric(raw[field], errors='raise')
        raw = raw.set_index(['datetime', 'instrument']).sort_index()
        decoded = reader.read({field: field for field in FIELDS}, [member])
        expected = raw.reindex(decoded.index)
        mismatch = {}
        for field in FIELDS:
            # Qlib's dumper stores IEEE float32. Check exact decoded identity,
            # rather than a loose decimal tolerance or guessed scale.
            wanted = expected[field].to_numpy(dtype=np.float32).astype(np.float64)
            actual = decoded[field].to_numpy(dtype=np.float64)
            unequal = ~((actual == wanted) | (np.isnan(actual) & np.isnan(wanted)))
            mismatch[field] = int(unequal.sum())
        quoted = expected[['open', 'high', 'low', 'close']].notna().all(axis=1)
        traded = quoted & expected.volume.gt(0) & expected.amount.gt(0)
        average = expected.amount * 1000 / (expected.volume * 100)
        bad_unit_relation = traded & ((average < expected.low * .95) | (average > expected.high * 1.05))
        alias_mismatch = (expected.vol != expected.volume) & ~(expected.vol.isna() & expected.volume.isna())
        missing_factor = not (source / 'features' / member.lower() / 'factor.day.bin').is_file()
        results.append({'instrument': member.upper(), 'status': 'matched' if not any(mismatch.values()) else 'mismatch',
                        'decoded_rows': len(decoded), 'quoted_rows': int(quoted.sum()), 'traded_rows': int(traded.sum()),
                        'csv_rows_outside_bin_member_calendar': int((~raw.index.isin(decoded.index)).sum()),
                        'float32_mismatch_by_field': mismatch, 'volume_vol_alias_mismatches': int(alias_mismatch.sum()),
                        'unit_relation_failures_5pct_tolerance': int(bad_unit_relation.sum()),
                        'factor_bin_missing': missing_factor})
    if before != source_hashes(source) or exporter_hash != file_hash(exporter):
        raise IntegrityError('ETF source or exporter changed during evidence verification')
    if any(file_hash(Path(path)) != expected for path, expected in csv_hashes.items()):
        raise IntegrityError('CSV source changed during evidence verification')
    matched = [row for row in results if row['status'] == 'matched']
    report = {'schema_version': 1, 'kind': 'tushare_export_semantics_evidence',
              'status': 'verified_export_semantics' if len(matched) == len(members) else 'incomplete',
              'g0_status': 'not_passed', 'scope': 'all_bin_members' if not limit else 'limited_bin_member_sample',
              'source': str(source.resolve()), 'csv_root': str(csv_root.resolve()), 'source_hashes': before,
              'csv_hashes': csv_hashes, 'exporter_path': str(exporter.resolve()), 'exporter_sha256': exporter_hash,
              'source_unchanged': True, 'member_count': len(members), 'matched_members': len(matched),
              'quoted_rows': sum(row.get('quoted_rows', 0) for row in results),
              'traded_rows': sum(row.get('traded_rows', 0) for row in results),
              'unit_relation_failures': sum(row.get('unit_relation_failures_5pct_tolerance', 0) for row in results),
              'factor_bins_missing': sum(row.get('factor_bin_missing', False) for row in results),
              'weekend_calendar_dates': int((reader.calendar.dayofweek >= 5).sum()),
              'candidate_semantics': {'volume_unit': 'lots', 'lot_size': 100, 'amount_multiplier': 1000,
                                      'price_mode': 'raw', 'change_unit': 'percent', 'fields_change': 'pct_chg',
                                      'original_change_is_price_difference': True},
              'primary_sources': {'fund_daily': 'https://tushare.pro/document/2?doc_id=127',
                                  'fund_adj': 'https://tushare.pro/document/2?doc_id=199',
                                  'trade_cal': 'https://tushare.pro/document/2?doc_id=26'},
              'remaining_g0': ['verified_authoritative_calendar', 'historical_adjustment_and_corporate_actions',
                               'PIT_classification_and_operating_evidence', 'CSI300_index_quotes',
                               'resolved_portfolio_and_research_rules'],
              'note': 'Units come from current primary docs and local exporter. Raw prices are inferred from the unchanged fund_daily path; missing adjustments are not filled. No active configuration or original data was changed.',
              'by_instrument': results}
    evidence_id = content_hash(report)
    destination = output / evidence_id
    atomic_json(destination / 'export_semantics.json', {**report, 'evidence_id': evidence_id})
    return {'evidence_id': evidence_id, 'artifact_path': str(destination), 'status': report['status'],
            'member_count': len(members), 'matched_members': len(matched), 'quoted_rows': report['quoted_rows'],
            'unit_relation_failures': report['unit_relation_failures'], 'source_unchanged': True, 'g0_status': 'not_passed'}


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('D:/qlib_data/etf_qlib_data'))
    parser.add_argument('--csv-root', type=Path, default=Path('D:/qlib_data/etf_csv_data'))
    parser.add_argument('--exporter', type=Path, default=Path('D:/quant_project/qlibQuantData/tushare_etf_to_qlib.py'))
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'artifacts/data_evidence')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be nonnegative')
    print(json.dumps(verify(args.source, args.csv_root, args.exporter, args.output, limit=args.limit), ensure_ascii=False))
