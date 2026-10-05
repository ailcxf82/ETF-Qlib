"""Join independently reviewed SSE/SZSE notices without rewriting raw data.

The full-bin quote verification is inherited from an immutable SSE evidence
artifact only after checking every original source hash and archived notice.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import uuid
from urllib.error import URLError
from urllib.request import Request, urlopen

import build_sse_calendar_evidence as sse
from etf_ml.errors import IntegrityError
from etf_ml.utils import FileLock, atomic_json, content_hash, ensure_within, file_hash, source_hashes

SZSE_URLS = [
    'https://www.szse.cn/disclosure/notice/general/t20191220_572766.html',
    'https://www.szse.cn/disclosure/notice/general/t20201224_583950.html',
    'https://www.szse.cn/disclosure/notice/general/t20211220_590321.html',
    'https://www.szse.cn/disclosure/notice/general/t20221227_598022.html',
    'https://www.szse.cn/disclosure/notice/t20231226_605108.html',
    'https://www.szse.cn/disclosure/notice/general/t20241223_611283.html',
    'https://investor.szse.cn/disclosure/notice/general/t20251222_618087.html',
]
SZSE_AMENDMENT = 'https://www.szse.cn/disclosure/notice/t20200127_573917.html'
DEFAULT_SSE = '64e98e53a8f914dd7e8a1124f837355003aca5797e890d397c8ecbb686ba2f21'


def download(url):
    last = None
    for _ in range(3):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15) as response:
                if response.status != 200 or not any(response.url.startswith(host) for host in ['https://www.szse.cn/', 'https://investor.szse.cn/']):
                    raise IntegrityError('SZSE notice source redirected outside the reviewed official hosts')
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise IntegrityError('SZSE notice source exceeds the evidence size limit')
                return raw
        except URLError as error:
            last = error
    raise IntegrityError('Public SZSE notice was unavailable after three bounded attempts') from last


def load_sse_evidence(root):
    root = Path(root)
    report_path = root / 'calendar_evidence.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    claimed = report.pop('evidence_id')
    if claimed != root.name or claimed != content_hash(report) or report['status'] != 'verified_sse_notice_calendar' or report['exchange_scope'] != 'SSE' or not report['source_unchanged'] or report['quoted_rows_on_closed_dates'] != 0:
        raise IntegrityError('SSE evidence identity or quote verification failed')
    expected_notices = {sse.BASE + suffix: (year, published, set(periods)) for year, published, suffix, periods in sse.NOTICES}
    actual_notices = {row['url']: row for row in report['sources']}
    if len(actual_notices) != len(report['sources']) or set(actual_notices) != set(expected_notices) | {sse.AMENDMENT['url']}:
        raise IntegrityError('SSE archived notice set is incomplete or duplicated')
    periods, blobs = [], {}
    for url, row in actual_notices.items():
        path = ensure_within(root / row['html_path'], root)
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != row['sha256']:
            raise IntegrityError('Archived SSE notice hash changed')
        if url == sse.AMENDMENT['url']:
            _, text = sse.parse_notice(raw, 2020, sse.AMENDMENT['published_date'])
            if '延长2020年春节休市至2月2日，2月3日正常开市' not in text:
                raise IntegrityError('Archived SSE emergency amendment is invalid')
            periods.append(sse.AMENDMENT['added_closure'])
        else:
            year, published, expected = expected_notices[url]
            actual, _ = sse.parse_notice(raw, year, published)
            if actual != expected or set(row['closure_periods']) != expected or row['published_date'] != published:
                raise IntegrityError('Archived SSE notice periods changed')
            periods.extend(actual)
        blobs['sse-' + path.name] = raw
    trusted = sse.calendar_dates(periods, '2020-01-01', '2026-12-31')
    calendar = '\n'.join(trusted) + '\n'
    if (root / 'trusted_calendar.txt').read_text(encoding='utf-8') != calendar or report['calendar_dates'] != len(trusted):
        raise IntegrityError('SSE calendar differs from its archived notices')
    return {**report, 'evidence_id': claimed}, trusted, blobs


def publish(output, report, blobs, calendar):
    evidence_id = content_hash(report)
    destination = ensure_within(output / evidence_id, output)
    complete = {**report, 'evidence_id': evidence_id}
    with FileLock(output / '.calendar-publication.lock'):
        if destination.exists():
            if json.loads((destination / 'calendar_evidence.json').read_text(encoding='utf-8')) != complete:
                raise IntegrityError('Combined calendar evidence identity conflict')
            for name, raw in blobs.items():
                if ensure_within(destination / name, destination).read_bytes() != raw:
                    raise IntegrityError('Combined archived source changed')
            if (destination / 'trusted_calendar.txt').read_text(encoding='utf-8') != calendar:
                raise IntegrityError('Combined archived calendar changed')
        else:
            stage = ensure_within(output / ('.calendar-stage-' + str(os.getpid()) + '-' + uuid.uuid4().hex), output)
            stage.mkdir()
            for name, raw in blobs.items():
                path = ensure_within(stage / name, stage)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (stage / 'trusted_calendar.txt').write_text(calendar, encoding='utf-8')
            atomic_json(stage / 'calendar_evidence.json', complete)
            # Both resolved directory targets were checked within the project
            # output before this atomic publication. Incomplete staging never
            # appears under an evidence ID and does not replace successful data.
            os.replace(stage, destination)
    return evidence_id, destination


def verify(sse_root, output):
    project = Path(__file__).resolve().parents[1]
    output = ensure_within(Path(output), project)
    inherited, trusted, sse_blobs = load_sse_evidence(sse_root)
    sources, periods = [], []
    blobs = {'sources/' + name: raw for name, raw in sse_blobs.items()}
    for (year, published, _, expected), url in zip(sse.NOTICES, SZSE_URLS, strict=True):
        raw = download(url)
        actual, _ = sse.parse_notice(raw, year, published)
        if actual != set(expected):
            raise IntegrityError('SZSE closure periods differ from independently reviewed SSE notices: ' + str(year))
        name = 'sources/szse-' + str(year) + '.html'
        blobs[name] = raw
        sources.append({'year': year, 'published_date': published, 'url': url,
                        'html_path': name, 'sha256': sha256(raw).hexdigest(), 'closure_periods': sorted(actual)})
        periods.extend(actual)
    raw = download(SZSE_AMENDMENT)
    _, text = sse.parse_notice(raw, 2020, sse.AMENDMENT['published_date'])
    if '延长2020年春节休市至2月2日，2月3日起照常开市' not in text:
        raise IntegrityError('SZSE 2020 emergency extension was not verified')
    name = 'sources/szse-2020-spring-amendment.html'
    blobs[name] = raw
    sources.append({'year': 2020, 'published_date': sse.AMENDMENT['published_date'], 'url': SZSE_AMENDMENT,
                    'html_path': name, 'sha256': sha256(raw).hexdigest(), 'added_closure': sse.AMENDMENT['added_closure']})
    periods.append(sse.AMENDMENT['added_closure'])
    if sse.calendar_dates(periods, '2020-01-01', '2026-12-31') != trusted:
        raise IntegrityError('SSE and SZSE derived calendars differ')
    if source_hashes(Path(inherited['source'])) != inherited['source_hashes']:
        raise IntegrityError('Original ETF files differ from the inherited quote calendar verification')
    for path in [Path(__file__), Path(sse.__file__)]:
        blobs['derivation/' + path.name] = path.read_bytes()
    calendar = '\n'.join(trusted) + '\n'
    report = {'schema_version': 1, 'kind': 'exchange_calendar_notice_evidence', 'exchange_scope': ['SSE', 'SZSE'],
              'status': 'verified_exchange_notice_calendar', 'g0_status': 'not_passed',
              'inherited_sse_evidence': {'path': str(Path(sse_root).resolve()), 'evidence_id': inherited['evidence_id'],
                                         'report_sha256': file_hash(Path(sse_root) / 'calendar_evidence.json')},
              'szse_sources': sources, 'sources_and_derivation_sha256': {name: sha256(raw).hexdigest() for name, raw in blobs.items()},
              'calendar_sha256': sha256(calendar.encode('utf-8')).hexdigest(), 'calendar_dates': len(trusted),
              'calendar_start': trusted[0], 'calendar_end': trusted[-1], 'source': inherited['source'],
              'source_hashes': inherited['source_hashes'], 'source_unchanged': True,
              'quoted_rows': inherited['quoted_rows'], 'quoted_dates': inherited['quoted_dates'],
              'quoted_rows_on_closed_dates': 0, 'old_calendar_closed_date_count': inherited['old_calendar_closed_date_count'],
              'old_calendar_closed_dates': inherited['old_calendar_closed_dates'], 'independent_exchange_match': True,
              'point_in_time_schedule_verified': False, 'schedule_caveat': inherited['schedule_caveat'],
              'remaining_g0': ['PIT_calendar_decision_policy', 'historical_adjustment_and_corporate_actions',
                               'PIT_metadata', 'CSI300_quotes', 'resolved_parameters']}
    evidence_id, destination = publish(output, report, blobs, calendar)
    return {key: value for key, value in {**report, 'evidence_id': evidence_id, 'artifact_path': str(destination)}.items()
            if key in {'status', 'g0_status', 'calendar_dates', 'quoted_rows', 'old_calendar_closed_date_count',
                       'source_unchanged', 'independent_exchange_match', 'evidence_id', 'artifact_path'}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    project = Path(__file__).resolve().parents[1]
    parser.add_argument('--sse-evidence', type=Path, default=project / 'artifacts/data_evidence' / DEFAULT_SSE)
    parser.add_argument('--output', type=Path, default=project / 'artifacts/data_evidence')
    args = parser.parse_args()
    print(json.dumps(verify(args.sse_evidence, args.output), ensure_ascii=True))
