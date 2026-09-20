"""Archive public SSE notices and derive an independently verified calendar.

This is an SSE date-position evidence artifact, not a G0 approval or a
point-in-time trading schedule. Original Qlib files are never modified.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

import pandas as pd

from etf_ml.data.source import QlibBinSource
from etf_ml.errors import IntegrityError
from etf_ml.utils import atomic_json, content_hash, ensure_within, source_hashes

BASE = 'https://www.sse.com.cn/disclosure/'
NOTICES = [
    (2020, '2019-12-20', 'announcement/general/c/c_20191220_4969627.shtml',
     ['2020-01-01/2020-01-01', '2020-01-24/2020-01-30', '2020-04-04/2020-04-06',
      '2020-05-01/2020-05-05', '2020-06-25/2020-06-27', '2020-10-01/2020-10-08']),
    (2021, '2020-12-24', 'dealinstruc/closed/c/c_20201224_5286951.shtml',
     ['2021-01-01/2021-01-03', '2021-02-11/2021-02-17', '2021-04-03/2021-04-05',
      '2021-05-01/2021-05-05', '2021-06-12/2021-06-14', '2021-09-19/2021-09-21', '2021-10-01/2021-10-07']),
    (2022, '2021-12-20', 'dealinstruc/closed/c/c_20211220_5663057.shtml',
     ['2022-01-01/2022-01-03', '2022-01-31/2022-02-06', '2022-04-03/2022-04-05',
      '2022-04-30/2022-05-04', '2022-06-03/2022-06-05', '2022-09-10/2022-09-12', '2022-10-01/2022-10-07']),
    (2023, '2022-12-27', 'dealinstruc/closed/c/c_20221227_5714459.shtml',
     ['2022-12-31/2023-01-02', '2023-01-21/2023-01-27', '2023-04-05/2023-04-05',
      '2023-04-29/2023-05-03', '2023-06-22/2023-06-24', '2023-09-29/2023-10-06']),
    (2024, '2023-12-26', 'dealinstruc/closed/c/c_20231226_5733941.shtml',
     ['2023-12-30/2024-01-01', '2024-02-09/2024-02-17', '2024-04-04/2024-04-06',
      '2024-05-01/2024-05-05', '2024-06-10/2024-06-10', '2024-09-15/2024-09-17', '2024-10-01/2024-10-07']),
    (2025, '2024-12-23', 'dealinstruc/closed/c/c_20241223_10767110.shtml',
     ['2025-01-01/2025-01-01', '2025-01-28/2025-02-04', '2025-04-04/2025-04-06',
      '2025-05-01/2025-05-05', '2025-05-31/2025-06-02', '2025-10-01/2025-10-08']),
    (2026, '2025-12-22', 'announcement/general/c/c_20251222_10802507.shtml',
     ['2026-01-01/2026-01-03', '2026-02-15/2026-02-23', '2026-04-04/2026-04-06',
      '2026-05-01/2026-05-05', '2026-06-19/2026-06-21', '2026-09-25/2026-09-27', '2026-10-01/2026-10-07']),
]
AMENDMENT = {'year': 2020, 'published_date': '2020-01-27',
             'url': BASE + 'announcement/general/c/c_20200127_4991582.shtml',
             'added_closure': '2020-01-31/2020-02-02'}
DATE_RANGE = re.compile(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日(?:至(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日)?休市')


class NoticeText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = []
        self.blocks = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'tr'}:
            self.active.append([tag, []])

    def handle_data(self, data):
        self.parts.append(data)
        for _, parts in self.active:
            parts.append(data)

    def handle_endtag(self, tag):
        if self.active and self.active[-1][0] == tag:
            self.blocks.append(''.join(self.active.pop()[1]))


def normalize(text):
    return re.sub(r'\s+', '', re.sub(r'（[^）]*）|\([^)]*\)', '', text))


def parse_notice(raw, year, published):
    parser = NoticeText()
    parser.feed(raw.decode('utf-8'))
    text = normalize(''.join(parser.parts))
    if published not in text and date.fromisoformat(published).strftime('%Y年%m月%d日') not in text:
        raise IntegrityError('Official notice publication date was not verified')
    observed = set()
    for block in parser.blocks:
        clean = normalize(block)
        for match in DATE_RANGE.finditer(clean):
            y1, m1, d1, y2, m2, d2 = match.groups()
            first = date(int(y1 or year), int(m1), int(d1))
            last = date(int(y2 or year), int(m2 or m1), int(d2 or d1))
            if last < first or (last - first).days > 31:
                raise IntegrityError('Ambiguous official holiday interval')
            observed.add(first.isoformat() + '/' + last.isoformat())
    return observed, text


def days(period):
    first, last = (date.fromisoformat(value) for value in period.split('/'))
    return {first + timedelta(days=offset) for offset in range((last - first).days + 1)}


def calendar_dates(periods, start, end):
    closed = set().union(*(days(period) for period in periods))
    return [day.isoformat() for day in pd.date_range(start, end).date if day.weekday() < 5 and day not in closed]


def download(url):
    with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0 ETF-calendar-evidence'}), timeout=20) as response:
        if response.status != 200 or not response.url.startswith('https://www.sse.com.cn/'):
            raise IntegrityError('Official calendar source redirected outside the verified exchange')
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise IntegrityError('Official calendar source exceeds the evidence size limit')
        return raw


def verify(source, output):
    project = Path(__file__).resolve().parents[1]
    output = ensure_within(Path(output), project)
    original = source_hashes(Path(source))
    blobs, sources, periods = {}, [], []
    for year, published, suffix, expected in NOTICES:
        url = BASE + suffix
        raw = download(url)
        actual, _ = parse_notice(raw, year, published)
        if actual != set(expected):
            raise IntegrityError('Official notice closure intervals differ from the reviewed catalog: ' + str(year))
        name = str(year) + '.html'
        blobs[name] = raw
        sources.append({'year': year, 'url': url, 'published_date': published,
                        'html_path': 'sources/' + name, 'sha256': sha256(raw).hexdigest(), 'closure_periods': sorted(actual)})
        periods.extend(expected)
    raw = download(AMENDMENT['url'])
    _, text = parse_notice(raw, 2020, AMENDMENT['published_date'])
    if '延长2020年春节休市至2月2日，2月3日正常开市' not in text:
        raise IntegrityError('2020 emergency extension was not verified from the official notice')
    name = '2020-spring-amendment.html'
    blobs[name] = raw
    sources.append({**AMENDMENT, 'html_path': 'sources/' + name, 'sha256': sha256(raw).hexdigest()})
    periods.append(AMENDMENT['added_closure'])
    trusted = calendar_dates(periods, '2020-01-01', '2026-12-31')
    reader = QlibBinSource(Path(source))
    if reader.calendar.min() < pd.Timestamp('2020-01-01') or reader.calendar.max() > pd.Timestamp('2026-12-31'):
        raise IntegrityError('Original calendar falls outside the reviewed official evidence years')
    frame = reader.read({field: field for field in ['open', 'high', 'low', 'close']})
    quoted = frame[['open', 'high', 'low', 'close']].notna().all(axis=1)
    dates = frame.index.get_level_values('datetime').strftime('%Y-%m-%d')
    invalid = quoted & ~dates.isin(trusted)
    quote_dates = sorted(set(dates[quoted]))
    old_dates = reader.calendar.strftime('%Y-%m-%d').tolist()
    if original != source_hashes(Path(source)):
        raise IntegrityError('Original ETF data changed during calendar verification')
    report = {'schema_version': 1, 'kind': 'sse_calendar_notice_evidence', 'exchange_scope': 'SSE',
              'status': 'verified_sse_notice_calendar' if not invalid.any() else 'quote_calendar_conflict',
              'g0_status': 'not_passed', 'source': str(Path(source).resolve()), 'source_hashes': original,
              'source_unchanged': True, 'calendar_start': trusted[0], 'calendar_end': trusted[-1],
              'calendar_dates': len(trusted), 'sources': sources, 'quoted_rows': int(quoted.sum()),
              'quoted_dates': len(quote_dates), 'quoted_rows_on_closed_dates': int(invalid.sum()),
              'closed_dates_with_quotes': sorted(set(dates[invalid])),
              'old_calendar_closed_dates': sorted(set(old_dates) - set(trusted)),
              'old_calendar_closed_date_count': len(set(old_dates) - set(trusted)),
              'verified_quote_date_coverage': not bool(invalid.any()),
              'point_in_time_schedule_verified': False,
              'schedule_caveat': 'The January 2020 amendment was published after the original January last trading day. Retrospective month-end dates must not become pre-announcement trading decisions. This evidence verifies dates and positions only; future dates reflect published annual notices and can change.',
              'remaining_g0': ['SZSE_independent_calendar_evidence', 'PIT_calendar_decision_policy',
                               'historical_adjustment_and_corporate_actions', 'PIT_metadata',
                               'CSI300_quotes', 'resolved_parameters']}
    evidence_id = content_hash(report)
    destination = output / evidence_id
    if destination.exists():
        existing = json.loads((destination / 'calendar_evidence.json').read_text(encoding='utf-8'))
        if existing != {**report, 'evidence_id': evidence_id}:
            raise IntegrityError('Existing calendar evidence identity conflict')
        for name, blob in blobs.items():
            if (destination / 'sources' / name).read_bytes() != blob:
                raise IntegrityError('Archived official notice changed')
        if (destination / 'trusted_calendar.txt').read_text(encoding='utf-8') != '\n'.join(trusted) + '\n':
            raise IntegrityError('Archived calendar changed')
    else:
        (destination / 'sources').mkdir(parents=True)
        for name, blob in blobs.items():
            (destination / 'sources' / name).write_bytes(blob)
        (destination / 'trusted_calendar.txt').write_text('\n'.join(trusted) + '\n', encoding='utf-8')
        atomic_json(destination / 'calendar_evidence.json', {**report, 'evidence_id': evidence_id})
    return {'evidence_id': evidence_id, 'artifact_path': str(destination), 'status': report['status'],
            'calendar_dates': len(trusted), 'quoted_rows': report['quoted_rows'],
            'quoted_rows_on_closed_dates': report['quoted_rows_on_closed_dates'],
            'old_calendar_closed_date_count': report['old_calendar_closed_date_count'],
            'source_unchanged': True, 'g0_status': 'not_passed'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('D:/qlib_data/etf_qlib_data'))
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'artifacts/data_evidence')
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.output), ensure_ascii=True))
