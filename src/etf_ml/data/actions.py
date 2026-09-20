"""Corporate-action structural checks and causal raw-price valuation.

Source completeness/announcement evidence is a separate G0 requirement.
The ledger books dated cash receivables separately from available cash and
uses recorded closing holdings when a dividend record date is provided.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from etf_ml.errors import QualityError

EVENT_COLUMNS = ['event_id', 'datetime', 'instrument', 'cash_per_share', 'share_multiplier', 'sequence']


def _dates(values, name):
    try:
        result = pd.to_datetime(values, errors='raise')
        if result.isna().any() or result.dt.tz is not None or not result.equals(result.dt.normalize()):
            raise ValueError('Events require local trading dates')
        return result
    except (ValueError, TypeError, AttributeError) as error:
        raise QualityError('Invalid corporate action ' + name) from error


def _optional_dates(values, required, name):
    known = values.notna()
    if (required & ~known).any():
        raise QualityError('Invalid corporate action ' + name + ': required for cash dividends')
    result = pd.Series(pd.NaT, index=values.index, dtype='datetime64[ns]')
    if known.any():
        result.loc[known] = _dates(values[known], name)
    return result


def validate_events(events, calendar, instruments):
    if events is None or (isinstance(events, pd.DataFrame) and events.empty and not len(events.columns)):
        return pd.DataFrame(columns=EVENT_COLUMNS), {'status': 'structurally_valid', 'event_count': 0,
            'source_completeness_verified': False, 'announcement_time_unknown': 0}
    if not isinstance(events, pd.DataFrame) or events.columns.has_duplicates:
        raise QualityError('Corporate actions require unique table columns')
    required = set(EVENT_COLUMNS) - {'sequence'}
    if not events.empty and not required.issubset(events):
        raise QualityError('Corporate action fields missing')
    if events.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS), {'status': 'structurally_valid', 'event_count': 0,
            'source_completeness_verified': False, 'announcement_time_unknown': 0}
    result = events.copy()
    for column in ['event_id', 'instrument']:
        if not result[column].map(lambda value: isinstance(value, str) and bool(value.strip()) and value == value.strip()).all():
            raise QualityError('Corporate action identity is empty or ambiguous')
    if result.event_id.duplicated().any():
        raise QualityError('Duplicate corporate action IDs')
    if not result.instrument.isin(set(instruments)).all():
        raise QualityError('Corporate action refers to an unknown ETF')
    result['datetime'] = _dates(result.datetime, 'datetime')
    if not result.datetime.isin(calendar).all():
        raise QualityError('Corporate action falls outside the snapshot trading calendar')
    for column in ['cash_per_share', 'share_multiplier']:
        if not pd.api.types.is_numeric_dtype(result[column]) or pd.api.types.is_bool_dtype(result[column]) or not np.isfinite(result[column]).all():
            raise QualityError('Corporate action amounts must be finite numeric values')
    if result.cash_per_share.lt(0).any() or result.share_multiplier.le(0).any():
        raise QualityError('Invalid corporate action amounts')
    if (result.cash_per_share.eq(0) & result.share_multiplier.eq(1)).any():
        raise QualityError('Corporate action has no economic effect')
    if 'sequence' not in result:
        result['sequence'] = 0
    if (not pd.api.types.is_numeric_dtype(result.sequence) or pd.api.types.is_bool_dtype(result.sequence) or
            not np.isfinite(result.sequence).all() or result.sequence.lt(0).any() or (result.sequence % 1 != 0).any()):
        raise QualityError('Corporate action sequence must be a nonnegative integer')
    if result[['datetime', 'instrument', 'sequence']].duplicated().any():
        raise QualityError('Same-day corporate actions require an explicit unique sequence')
    result['sequence'] = result.sequence.astype('int64')
    if 'share_rounding' in result:
        result['share_rounding'] = result.share_rounding.fillna('none')
        if not result.share_rounding.isin(['none', 'ceil', 'floor', 'exact']).all():
            raise QualityError('Invalid corporate action share rounding')
        if (result.share_rounding.ne('none') & result.share_multiplier.eq(1)).any():
            raise QualityError('Share rounding requires an explicit share conversion')
    cash = result.cash_per_share.gt(0)
    if 'cash_share_basis' in result:
        result['cash_share_basis'] = result.cash_share_basis.fillna('record')
        if not result.cash_share_basis.isin(['record', 'post_record_conversions']).all():
            raise QualityError('Invalid dividend cash share basis')
        post = result.cash_share_basis.eq('post_record_conversions')
        if (post & ~cash).any() or (post.any() and
                not {'record_date', 'cash_basis_evidence_id'}.issubset(result)):
            raise QualityError('Post-conversion dividend requires cash, record date and evidence')
        if post.any() and not result.loc[post, 'cash_basis_evidence_id'].map(
                lambda v: isinstance(v, str) and bool(v.strip())).all():
            raise QualityError('Post-conversion dividend evidence missing')
    if 'pay_date' in result:
        result['pay_date'] = _optional_dates(result.pay_date, cash, 'pay_date')
        if (cash & result.pay_date.lt(result.datetime)).any():
            raise QualityError('Dividend payment date precedes its ex-date')
    if 'record_date' in result:
        result['record_date'] = _optional_dates(result.record_date, cash, 'record_date')
        if (cash & (~result.record_date.isin(calendar) | result.record_date.ge(result.datetime))).any():
            raise QualityError('Dividend record date must be a covered trading day preceding its ex-date')
    if 'available_time' in result:
        known = []
        for value in result.available_time:
            try:
                time = pd.Timestamp(value)
                if pd.isna(time) or time.tzinfo is None:
                    raise ValueError('Announcement timestamp requires explicit timezone')
                known.append(time.tz_convert('Asia/Shanghai'))
            except (ValueError, TypeError) as error:
                raise QualityError('Invalid corporate action announcement time') from error
        result['available_time'] = pd.to_datetime(pd.Series(known, index=result.index), utc=True).dt.tz_convert('Asia/Shanghai')
        if (result.available_time.dt.tz_localize(None) > result.datetime + pd.Timedelta(hours=9, minutes=30)).any():
            raise QualityError('Corporate action was not known by its effective opening time')
    result = result.sort_values(['datetime', 'instrument', 'sequence', 'event_id']).reset_index(drop=True)
    if 'cash_share_basis' in result:
        for event in result[result.cash_share_basis.eq('post_record_conversions')].itertuples(index=False):
            changes = dividend_share_changes(event, result)
            if changes.empty or not changes.record_date.eq(event.record_date).all():
                raise QualityError('Post-conversion dividend requires matching registered share changes')
    return result, {'status': 'structurally_valid', 'event_count': len(result),
        'source_completeness_verified': False,
        'announcement_time_unknown': len(result) if 'available_time' not in result else 0,
        'cash_model': 'dated_dividend_receivables',
        'entitlement_model': 'record_close_or_pre_action_compatibility', 'note': 'Structural checks do not prove upstream event completeness or source identity'}


def recorded_quantity(history, record_date, start_day, initially_flat, instrument):
    """Resolve actual recorded holdings, never substitute current holdings."""
    try:
        date = pd.Timestamp(record_date)
        if pd.isna(date) or date.tzinfo is not None or date != date.normalize():
            raise ValueError('Invalid record date')
    except (ValueError, TypeError) as error:
        raise QualityError('Invalid dividend record date') from error
    key = str(date)
    if key in history:
        quantity = float(history[key].get(instrument, 0.))
        if not np.isfinite(quantity) or quantity < 0:
            raise QualityError('Invalid dividend record-day closing holdings')
        return quantity
    if start_day is not None and date < pd.Timestamp(start_day) and initially_flat:
        # Backtests begin from declared cash only; no prior entitlement exists.
        return 0.
    raise QualityError('Missing dividend record-day closing holdings')


def dividend_share_changes(event, events):
    record = getattr(event, 'record_date', None)
    if record is None or pd.isna(record):
        raise QualityError('Dividend share conversion requires record date')
    date = pd.Timestamp(event.datetime)
    sequence = getattr(event, 'sequence', 0)
    return events[(events.instrument == event.instrument) & (events.datetime > record) &
        ((events.datetime < date) | ((events.datetime == date) & (events.sequence < sequence))) &
        events.share_multiplier.ne(1)].sort_values(['datetime', 'sequence'])


def dividend_entitled_shares(registered, event, events):
    if getattr(event, 'cash_share_basis', 'record') != 'post_record_conversions':
        return registered
    for change in dividend_share_changes(event, events).itertuples(index=False):
        registered = adjusted_shares(registered, change.share_multiplier, getattr(change, 'share_rounding', 'none'))
    return registered


def cash_per_ex_share(event, events):
    """Convert per-record-share distributions through intervening share changes."""
    cash = float(event.cash_per_share)
    if getattr(event, 'cash_share_basis', 'record') == 'post_record_conversions':
        return cash
    record = getattr(event, 'record_date', None)
    if not cash or record is None or pd.isna(record):
        return cash  # legacy events explicitly mean per pre-action share
    date = pd.Timestamp(event.datetime)
    sequence = getattr(event, 'sequence', 0)
    changes = events[(events.instrument == event.instrument) & (events.datetime > record) &
        ((events.datetime < date) | ((events.datetime == date) & (events.sequence < sequence)))]
    multiplier = float(np.prod(changes.share_multiplier.to_numpy(dtype=float)))
    if not np.isfinite(multiplier) or multiplier <= 0:
        raise QualityError('Invalid intervening dividend share-unit conversion')
    return cash / multiplier


def adjust_mark(price, cash_per_share=0., share_multiplier=1.):
    if not np.isfinite(price) or price <= 0 or not np.isfinite(cash_per_share) or cash_per_share < 0 or not np.isfinite(share_multiplier) or share_multiplier <= 0:
        raise QualityError('Invalid corporate-action valuation inputs')
    adjusted = (float(price) - float(cash_per_share)) / float(share_multiplier)
    if not np.isfinite(adjusted) or adjusted <= 0:
        raise QualityError('Corporate-action-adjusted mark must be finite and positive')
    return adjusted


def price_histories(panel):
    """Index immutable snapshot closes once for causal account replay."""
    return {instrument: part.droplevel('instrument').raw_close
            for instrument, part in panel[['raw_close']].groupby(level='instrument', sort=False)}


def raw_close_as_of(panel, events, instrument, day, *, histories=None):
    day = pd.Timestamp(day).normalize()
    if histories is None:
        instruments = panel.index.get_level_values('instrument')
        if instrument not in instruments:
            raise QualityError('Held ETF has no raw valuation history')
        history = panel.xs(instrument, level='instrument').loc[:day, 'raw_close']
    else:
        if instrument not in histories:
            raise QualityError('Held ETF has no raw valuation history')
        history = histories[instrument].loc[:day]
    usable = history[np.isfinite(history) & history.gt(0)]
    if usable.empty:
        raise QualityError('Held ETF has no usable raw valuation history')
    quote_day, price = usable.index[-1], float(usable.iloc[-1])
    if events is not None and not events.empty:
        actions = events[(events.instrument == instrument) & (events.datetime > quote_day) & (events.datetime <= day)]
        columns = ['datetime', 'sequence'] if 'sequence' in actions else ['datetime']
        for row in actions.sort_values(columns, kind='stable').itertuples(index=False):
            price = adjust_mark(price, cash_per_ex_share(row, events), row.share_multiplier)
    return price, quote_day


def adjusted_shares(quantity, multiplier, rounding='none'):
    """Account-level conversion; explicit rounding is separate from trade lots."""
    from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
    if (isinstance(quantity, bool) or isinstance(multiplier, bool) or
            not np.isfinite(quantity) or quantity < 0 or
            not np.isfinite(multiplier) or multiplier <= 0 or rounding not in ('none', 'ceil', 'floor', 'exact')):
        raise QualityError('Invalid corporate action share conversion')
    value = float(quantity) * float(multiplier)
    if not np.isfinite(value):
        raise QualityError('Nonfinite converted shares')
    if rounding == 'none':
        return value
    exact = Decimal(str(quantity)) * Decimal(str(multiplier))
    if rounding == 'exact':
        if exact != exact.to_integral_value():
            raise QualityError('Exact share allocation requires an integer result or explicit allocation detail')
        return float(exact)
    mode = ROUND_CEILING if rounding == 'ceil' else ROUND_FLOOR
    return float(exact.to_integral_value(rounding=mode))
