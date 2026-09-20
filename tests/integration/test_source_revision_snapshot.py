import json
import pandas as pd
import pytest
from tests.source_revision_fixtures import revision_fixture
from etf_ml.data.snapshot import build_snapshot,audit_source
from etf_ml.data.source import QlibBinSource
from etf_ml.contracts import UniversePolicy,PortfolioPolicy
from etf_ml.backtest.qlib_runner import evaluate
from etf_ml.utils import source_hashes


def test_revisions_are_bound_in_actual_snapshot_provider_and_audit(tmp_path):
    spec,_,_,calendar=revision_fixture(tmp_path);before=source_hashes(spec.source)
    report=audit_source(spec);assert report['adjustment_validation']['status']=='passed'
    snapshot=build_snapshot(spec.source,spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    quality=json.loads((snapshot.path/'data_quality.json').read_text(encoding='utf-8'))
    assert quality['source_revisions_validation']['revised_quote_rows']==1
    assert str(spec.source_revisions_path.resolve()) in snapshot.manifest['external_hashes']
    provider=QlibBinSource(snapshot.path/'research/provider').read({'close':'close','factor':'factor'})
    assert provider.loc[(pd.Timestamp('2020-05-22'),'510300.SH'),'close']==pytest.approx(.994)
    assert provider.factor.eq(1).all() and source_hashes(spec.source)==before


@pytest.mark.qlib
def test_actual_qlib_equity_uses_revised_close_and_independent_ledger(tmp_path):
    spec,_,_,calendar=revision_fixture(tmp_path);before=source_hashes(spec.source)
    snapshot=build_snapshot(spec.source,spec,UniversePolicy(minimum_listing_days=0,liquidity_lookback=1))
    panel=pd.read_parquet(snapshot.path/'panel.parquet');universe=pd.read_parquet(snapshot.path/'universe.parquet')
    scores=pd.Series(1.,index=panel.index,name='score')
    policy=PortfolioPolicy(k_mode='count',k=1,minimum_commission=0,commission_rate=.0003,slippage_rate=.0003,liquidity_lookback=1)
    result=evaluate(scores,policy,panel,universe=universe,calendar=calendar,benchmark=pd.read_parquet(spec.benchmark_path),provider=snapshot.path/'research/provider',recorder_uri=tmp_path/'recorders',start_time=calendar[1],end_time=calendar[-1],events=pd.read_parquet(snapshot.path/'events.parquet'))
    position=result.positions[pd.Timestamp('2020-05-22')];quantity=position.get_stock_amount('510300.SH');assert quantity>0
    assert position.calculate_value()==pytest.approx(position.get_cash()+quantity*.994,abs=.1)
    assert abs(position.calculate_value()-(position.get_cash()+quantity*.993))>1
    assert result.metrics['accounting_reconciled'] and source_hashes(spec.source)==before
