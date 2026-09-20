from pathlib import Path
import json
import numpy as np
import pandas as pd
from etf_ml.contracts import DataSpec
from etf_ml.data.source import encode_provider,QlibBinSource
from etf_ml.data.tushare_supplement import apply_adjustment_supplement
from etf_ml.utils import atomic_json,file_hash,source_hashes


def revision_fixture(tmp_path):
    calendar=pd.bdate_range('2020-05-11','2020-06-02',name='datetime')
    instrument='510300.SH';previous=pd.Timestamp('2020-05-22');day=pd.Timestamp('2020-05-25')
    close=np.where(calendar>=day,1.012,1.004);close[calendar==previous]=.993
    mapped=pd.DataFrame({'open':1.004,'high':1.02,'low':.99,'close':close,'volume':1e6,'amount':100400.,'change':0.,'factor':np.where(calendar>=day,.999,1.),'pre_close':np.where(calendar>day,1.012,1.004)},index=calendar)
    mapped.loc[previous,['open','high','low','volume','amount','change']]=[1.005,1.005,.992,25000.,2500.,-1.0956]
    mapped.loc[day,['open','high','low','change','pre_close']]=[1.,1.012,1.,1.8109,.994]
    mapped['instrument']=instrument;raw=mapped.reset_index().set_index(['datetime','instrument']).sort_index()
    future=pd.bdate_range(calendar[0],'2020-06-30',name='datetime')
    source=tmp_path/'raw';encode_provider(raw,calendar,source,{c:c for c in raw},future_calendar=future)
    trusted=tmp_path/'calendar.txt';trusted.write_text('\n'.join(future.strftime('%Y-%m-%d'))+'\n',encoding='utf-8')
    factor=tmp_path/'fund_adj.parquet';pd.DataFrame({'ts_code':instrument,'trade_date':calendar.strftime('%Y%m%d'),'adj_factor':np.where(calendar>=day,.999,1.)}).to_parquet(factor,index=False)
    div=tmp_path/'fund_div.parquet';pd.DataFrame(columns=['div_proc','ex_date']).to_parquet(div,index=False)
    meta=tmp_path/'metadata.parquet';pd.DataFrame([{'instrument':instrument,'valid_from':calendar[0],'valid_to':calendar[-1],'available_time':calendar[0]-pd.Timedelta(days=1),'listing_date':calendar[0],'asset_class':'domestic_equity','tracking_group':'CSI300','operating':True}]).to_parquet(meta,index=False)
    benchmark=tmp_path/'benchmark.parquet';b=pd.DataFrame({'open':4000.,'close':4000.},index=calendar);b.attrs['benchmark_id']='CSI300';b.to_parquet(benchmark)
    spec=DataSpec(source=source,artifact_root=tmp_path/'snapshots',trusted_calendar=trusted,volume_unit='lots',amount_multiplier=1000,price_mode='raw',change_unit='percent',metadata_path=meta,point_in_time_metadata=True,benchmark_path=benchmark,adjustment_path=factor)
    decoded=apply_adjustment_supplement(QlibBinSource(source).read(spec.fields),factor)
    key=(previous,instrument);revisions=tmp_path/'revisions.parquet'
    row={**{'before_'+c:float(decoded.loc[key,c]) for c in ['close','change','volume','amount']},'after_close':.994,'after_change':-.996,'after_volume':25496.,'after_amount':2539.386}
    pd.DataFrame([row],index=pd.MultiIndex.from_tuples([key],names=['datetime','instrument'])).to_parquet(revisions)
    emrows=[['2020-05-22','1.005','0.994','1.005','0.992','25496','2539386','1.29','-1.00','-0.010','0.25'],['2020-05-25','1.000','1.012','1.012','1.000','41302','4162956','1.21','1.81','0.018','0.40']]
    txrows=[x[:6]+[{},'0.00',str(round(float(x[6])/10000,2))] for x in emrows]
    sources={}
    for name in ['eastmoney','tencent']:
        response=tmp_path/(name+'.txt');receipt=tmp_path/(name+'.receipt.json')
        if name=='eastmoney':
            atomic_json(response,{'rc':0,'data':{'code':'510300','klines':[','.join(x) for x in emrows]}})
            record={'url':'https://push2his.eastmoney.com/api/qt/stock/kline/get','params':{'fqt':'0','klt':'101','secid':'1.510300'}}
        else:
            response.write_text('kline_day2020='+json.dumps({'code':0,'data':{'sh510300':{'day':txrows}}}),encoding='utf-8')
            record={'url':'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get','params':{'param':'sh510300,day,2020-01-01,2021-12-31,640,'},'raw_adjustment_key':'day'}
        record.update(instrument=instrument,status='independent_raw_quote_response_captured',response_sha256=file_hash(response));atomic_json(receipt,record)
        sources[name]={'receipt_path':str(receipt),'receipt_sha256':file_hash(receipt),'response_path':str(response)}
    review={'version':1,'policy':'dual_vendor_raw_quote_factor_boundary_revision','source_root':str(source),'semantics':{'price_mode':'raw','volume_unit':'lots','amount_multiplier':1000,'change_unit':'percent'},'raw_file_hashes':source_hashes(source),'evidence_hashes':{},'quote_revisions_path':str(revisions),'quote_revisions_sha256':file_hash(revisions),'factor_rules':[{'instrument':instrument,'previous_date':str(previous.date()),'boundary_date':str(day.date()),'source_boundary_ratio':.999,'factor_source_path':str(factor),'factor_source_sha256':file_hash(factor),'dividend_source_path':str(div),'dividend_source_sha256':file_hash(div),'quote_sources':sources}]}
    reviewpath=tmp_path/'review.json';atomic_json(reviewpath,review);spec.source_revisions_path=reviewpath
    return spec,decoded,review,calendar
