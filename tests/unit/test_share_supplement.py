import pandas as pd
import pytest

from etf_ml.data.share_supplement import load_share_supplement, validate_share_binding
from etf_ml.errors import QualityError
from etf_ml.utils import file_hash


def supplement_fixture(tmp_path):
    evidence = tmp_path / "notice.pdf"
    evidence.write_bytes(b"%PDF-share-conversion-notice")
    frame = pd.DataFrame([{"event_id":"merge", "instrument":"A", "datetime":pd.Timestamp("2023-01-06"),
                           "cash_per_share":0., "share_multiplier":.49977589,"sequence":0,"share_rounding":"ceil",
                           "record_date":pd.Timestamp("2023-01-05"),"available_time":pd.Timestamp("2023-01-03 23:59:59",tz="Asia/Shanghai"),
                           "evidence_id":"notice"}])
    frame.attrs = {"primary_evidence":[{"evidence_id":"notice","path":"notice.pdf","url":"https://exchange.example/notice",
                                      "sha256":file_hash(evidence)}], "source_factor_hashes":{"A":"1"*64}}
    path = tmp_path / "shares.parquet"
    frame.to_parquet(path,index=False)
    return frame, path


def test_share_supplement_binds_explicit_dates_rounding_and_primary_evidence(tmp_path):
    _, path = supplement_fixture(tmp_path)
    loaded, hashes = load_share_supplement(path,pd.bdate_range("2023-01-02","2023-01-10"),["A"])
    mapped = loaded.copy()
    mapped.attrs['share_event_input_hashes'] = hashes
    validate_share_binding(mapped, loaded, hashes)
    mapped.loc[0,'share_rounding'] = 'none'
    with pytest.raises(QualityError,match="changed"):
        validate_share_binding(mapped,loaded,hashes)


@pytest.mark.parametrize("change", ["record_date","rounding_unknown","rounding_missing","late_announcement","evidence_tamper","factor_binding"])
def test_share_supplement_rejects_unknown_or_unavailable_source_semantics(tmp_path,change):
    frame,path = supplement_fixture(tmp_path)
    if change == "record_date":
        frame.loc[0,'record_date'] = pd.Timestamp('2023-01-04')
    elif change == "rounding_unknown":
        frame.loc[0,'share_rounding'] = 'nearest'
    elif change == "rounding_missing":
        frame.loc[0,'share_rounding'] = None
    elif change == "late_announcement":
        frame.loc[0,'available_time'] = pd.Timestamp('2023-01-06 10:00',tz='Asia/Shanghai')
    elif change == "evidence_tamper":
        (tmp_path/'notice.pdf').write_bytes(b'tampered')
    else:
        frame.attrs['source_factor_hashes'] = {}
    frame.to_parquet(path,index=False)
    with pytest.raises(QualityError):
        load_share_supplement(path,pd.bdate_range('2023-01-02','2023-01-10'),['A'])


def cash_basis_fixture(tmp_path):
    from etf_ml.data.share_supplement import merge_share_events
    shares, path = supplement_fixture(tmp_path)
    shares.loc[0, 'share_multiplier'] = 2.5
    shares.attrs['cash_basis_overrides'] = [{'event_id':'div', 'share_event_id':'merge', 'evidence_id':'notice',
        'cash_per_share':.1292, 'record_date':'2023-01-05', 'pay_date':'2023-01-09', 'sequence':1}]
    shares.to_parquet(path,index=False)
    calendar = pd.bdate_range('2023-01-02','2023-01-10')
    loaded, hashes = load_share_supplement(path,calendar,['A'])
    cash = pd.DataFrame([{'event_id':'div','instrument':'A','datetime':pd.Timestamp('2023-01-06'),
        'cash_per_share':.1292,'share_multiplier':1.,'sequence':0,'record_date':pd.Timestamp('2023-01-05'),
        'pay_date':pd.Timestamp('2023-01-09'),'available_time':pd.Timestamp('2023-01-03T23:59:59+08:00')}])
    return cash, loaded, hashes, calendar


def test_cash_basis_merge_preserves_cash_and_binds_sequence_and_evidence(tmp_path):
    from etf_ml.data.share_supplement import merge_share_events
    from etf_ml.data.actions import cash_per_ex_share, dividend_entitled_shares
    cash, shares, hashes, calendar = cash_basis_fixture(tmp_path)
    mapped = merge_share_events(cash,shares,hashes,calendar,['A'])
    dividend = mapped[mapped.event_id.eq('div')].iloc[0]
    assert dividend.sequence == 1 and dividend.cash_per_share == .1292
    assert cash_per_ex_share(dividend,mapped) == .1292
    assert dividend_entitled_shares(1001,dividend,mapped) == 2503
    assert cash.sequence.iloc[0] == 0


@pytest.mark.parametrize('field',['cash_per_share','sequence','cash_share_basis','cash_basis_evidence_id'])
def test_frozen_cash_basis_tamper_rejected(tmp_path,field):
    from etf_ml.data.share_supplement import merge_share_events
    cash, shares, hashes, calendar = cash_basis_fixture(tmp_path)
    mapped = merge_share_events(cash,shares,hashes,calendar,['A'])
    index=mapped.index[mapped.event_id.eq('div')][0]
    mapped.loc[index,field] = {'cash_per_share':.323,'sequence':0,'cash_share_basis':'record','cash_basis_evidence_id':'wrong'}[field]
    with pytest.raises(QualityError):
        validate_share_binding(mapped,shares,hashes)


@pytest.mark.parametrize('field',['cash_per_share','record_date','pay_date','event_id'])
def test_cash_basis_original_source_mismatch_rejected(tmp_path,field):
    from etf_ml.data.share_supplement import merge_share_events
    cash, shares, hashes, calendar = cash_basis_fixture(tmp_path)
    cash.loc[0,field]={'cash_per_share':.323,'record_date':pd.Timestamp('2023-01-04'),
        'pay_date':pd.Timestamp('2023-01-10'),'event_id':'wrong'}[field]
    with pytest.raises(QualityError):
        merge_share_events(cash,shares,hashes,calendar,['A'])
