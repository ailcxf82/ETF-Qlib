import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from tests.source_revision_fixtures import revision_fixture
from etf_ml.data.source_revisions import apply_source_revisions
from etf_ml.data.normalize import normalize
from etf_ml.data.action_consistency import adjustment_event_consistency
from etf_ml.errors import QualityError
from etf_ml.utils import atomic_json,file_hash,source_hashes


def test_explicit_revision_changes_prices_amount_and_factor_without_events_or_raw_writes(tmp_path):
    spec,original,review,calendar=revision_fixture(tmp_path);before=source_hashes(spec.source)
    revised,report,hashes=apply_source_revisions(original,spec.source_revisions_path,spec)
    assert original.loc[(pd.Timestamp('2020-05-22'),'510300.SH'),'close']==pytest.approx(.993)
    assert revised.loc[(pd.Timestamp('2020-05-22'),'510300.SH'),'close']==.994
    assert revised.loc[(pd.Timestamp('2020-05-22'),'510300.SH'),'volume']==25496
    assert revised.loc[(pd.Timestamp('2020-05-22'),'510300.SH'),'amount']==2539.386
    assert revised.factor.eq(1).all() and report['economic_events_created']==0
    assert source_hashes(spec.source)==before and str(spec.source_revisions_path.resolve()) in hashes
    assert adjustment_event_consistency(normalize(original,spec),None,calendar)['status']=='failed'
    assert adjustment_event_consistency(normalize(revised,spec),None,calendar)['status']=='passed'


@pytest.mark.parametrize('mutation',['before','after','quote_hash','factor_hash','provider','unit','bridge','date','adjusted','vendor_disagreement','cash','raw_hash','missing_raw_hash','missing_key'])
def test_revision_rejects_unbound_or_inconsistent_inputs(tmp_path,mutation):
    spec,frame,review,calendar=revision_fixture(tmp_path);rule=review['factor_rules'][0]
    if mutation in ['before','after']:
        p=Path(review['quote_revisions_path']);rows=pd.read_parquet(p);rows.loc[:,mutation+'_close']+=.001;rows.to_parquet(p);review['quote_revisions_sha256']=file_hash(p)
    elif mutation=='quote_hash':review['quote_revisions_sha256']='0'*64
    elif mutation=='factor_hash':rule['factor_source_sha256']='0'*64
    elif mutation=='provider':review['source_root']=str(tmp_path/'other')
    elif mutation=='unit':review['semantics']['volume_unit']='shares'
    elif mutation=='bridge':rule['source_boundary_ratio']=.998
    elif mutation=='date':rule['boundary_date']='2020-05-26'
    elif mutation=='missing_raw_hash':review['raw_file_hashes']={}
    elif mutation=='missing_key':rule.pop('quote_sources')
    elif mutation=='raw_hash':review['raw_file_hashes']['calendars/day.txt']='0'*64
    elif mutation=='cash':
        p=Path(rule['dividend_source_path']);pd.DataFrame([{'ts_code':'510300.SH','div_proc':'实施','ex_date':'20200525'}]).to_parquet(p,index=False);rule['dividend_source_sha256']=file_hash(p)
    else:
        src=rule['quote_sources']['eastmoney' if mutation=='adjusted' else 'tencent'];rp=Path(src['receipt_path']);receipt=json.loads(rp.read_text(encoding='utf-8'))
        if mutation=='adjusted':receipt['params']['fqt']='1'
        else:
            p=Path(src['response_path']);text=p.read_text(encoding='utf-8');p.write_text(text.replace('0.994','0.995'),encoding='utf-8');receipt['response_sha256']=file_hash(p)
        atomic_json(rp,receipt);src['receipt_sha256']=file_hash(rp)
    atomic_json(spec.source_revisions_path,review)
    with pytest.raises(QualityError):apply_source_revisions(frame,spec.source_revisions_path,spec)


def test_revision_rejects_previously_rebased_source_factor_values(tmp_path):
    spec,frame,review,_=revision_fixture(tmp_path);frame.factor=1.
    with pytest.raises(QualityError,match='factors differ'):apply_source_revisions(frame,spec.source_revisions_path,spec)


def test_two_rehashed_sources_do_not_authorize_negative_trade_quantity(tmp_path):
    spec,frame,review,_=revision_fixture(tmp_path)
    for source in review['factor_rules'][0]['quote_sources'].values():
        p=Path(source['response_path']);text=p.read_text(encoding='utf-8');p.write_text(text.replace('25496','-1'),encoding='utf-8')
        rp=Path(source['receipt_path']);receipt=json.loads(rp.read_text(encoding='utf-8'));receipt['response_sha256']=file_hash(p)
        atomic_json(rp,receipt);source['receipt_sha256']=file_hash(rp)
    p=Path(review['quote_revisions_path']);rows=pd.read_parquet(p);rows['after_volume']=-1.;rows.to_parquet(p);review['quote_revisions_sha256']=file_hash(p)
    atomic_json(spec.source_revisions_path,review)
    with pytest.raises(QualityError,match='quantity'):
        apply_source_revisions(frame,spec.source_revisions_path,spec)
