import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from etf_ml.contracts import (FeatureArtifact, LabelSpec, ModelSpec, PortfolioPolicy,
                             ResearchPolicy, UniversePolicy, ValidationSpec)
from etf_ml.datasets import generate_labels
from etf_ml.datasets.splits import learning_mask
from etf_ml.errors import QualityError
from etf_ml.research.diagnostics import development_diagnostics
from etf_ml.research.protocol import ComparisonProtocol
from etf_ml.utils import canonical_json, content_hash


@pytest.fixture
def observations(panel, calendar, fold):
    # Deliberately perfect synthetic correlations provide an independent oracle
    # for observation reporting; this is not a causally validated investment factor.
    protocol = ComparisonProtocol(snapshot_id='synthetic', baseline_feature_set_id='base',
        label=LabelSpec(), universe=UniversePolicy(), validation=ValidationSpec(folds=[fold]),
        portfolio=PortfolioPolicy(k_mode='fraction', minimum_commission=0,
                                  liquidity_mode='participation', risk_mode='max_drawdown'),
        research=ResearchPolicy(seeds=[42, 43]), model=ModelSpec(), stress_min_excess_return=0)
    labels, events = generate_labels(panel, calendar, protocol.label)
    index = panel.index
    universe = pd.DataFrame({'eligible': True, 'tracking_group': 'equity'}, index=index)
    baseline = FeatureArtifact('base', (-labels).to_frame('opposite'), {})
    factor = FeatureArtifact('factor', labels.to_frame('factor'), {
        'quality': {'coverage': .97, 'worst_date_coverage': .8, 'nonfinite': 0, 'duplicate_keys': 0},
        'checks': {'status': 'passed'}, 'minimum_observations': 1})
    mask = learning_mask(events, labels, fold.selection,
                         next_start=protocol.validation.holdout_start) & universe.eligible
    index_hash = content_hash([[str(t), i] for t, i in index[mask]])
    reports = {}
    for kind, gain in [('baseline', 0), ('candidate', .02), ('ablation', 0)]:
        metrics = {'net_return': .1 + gain, 'excess_return': .05 + gain, 'max_drawdown': .04,
                   'annualized_volatility': .06, 'turnover': 1., 'commission': 15.,
                   'slippage_cost': 2., 'total_execution_cost': 17., 'effective_dates': 60,
                   'max_single_weight': .3, 'max_group_weight': .5}
        stress = {**metrics, 'excess_return': .03 + gain, 'total_execution_cost': 34.}
        reports[kind] = {'feature_set_id': kind, 'by_fold': [
            {'fold': fold.name, 'seed': seed, 'portfolio': copy.deepcopy(metrics),
             'cost_stress': {'2.0': copy.deepcopy(stress)},
             'dataset': {'evaluation_index_hash': index_hash},
             'predictive': {'ic': .2 + gain, 'rank_ic': .3 + gain, 'effective_dates': 54}}
            for seed in protocol.research.seeds]}
    return panel, calendar, universe, baseline, factor, protocol, reports


def run(values):
    return development_diagnostics(*values)


def test_reports_exact_correlations_costs_and_common_decay_sample(observations):
    result = run(observations)
    factor = result['predictive_metrics']['factor_by_fold'][0]
    assert factor['ic'] == pytest.approx(1.) and factor['rank_ic'] == pytest.approx(1.)
    assert factor['effective_dates'] == 54
    assert result['factor_diagnostics']['redundancy']['opposite'] == pytest.approx(1.)
    assert [r['effective_dates'] for r in result['factor_diagnostics']['stability']] == [27, 27]
    decay = result['factor_diagnostics']['decay']
    assert [r['horizon'] for r in decay] == [1, 5, 10]
    assert {r['common_sample_rows'] for r in decay} == {49 * 3}
    assert {r['effective_dates'] for r in decay} == {49}
    assert result['data_quality']['by_fold'][0]['coverage'] == pytest.approx(.9)
    assert result['data_quality']['by_fold'][0]['worst_date_coverage'] == 0.
    assert result['data_quality']['by_fold'][0]['by_group']['equity'] == pytest.approx(.9)
    assert len(result['portfolio_metrics']['by_fold']) == 2
    assert result['portfolio_metrics']['by_fold'][0]['candidate']['total_execution_cost'] == 17.
    assert [r['excess_return_delta'] for r in result['robustness']['cost_stress']] == pytest.approx([.02, .02])
    assert [r['excess_return_delta'] for r in result['robustness']['ablation']] == pytest.approx([.02, .02])
    assert result['predictive_metrics']['model_by_fold'][0]['candidate']['ic'] == pytest.approx(.22)


@pytest.mark.parametrize('problem', ['holdout_panel', 'holdout_calendar', 'index', 'eligibility', 'infinite', 'sample'])
def test_diagnostics_reject_unsafe_or_different_samples(observations, problem):
    args = list(copy.deepcopy(observations))
    panel, calendar, universe, baseline, factor, protocol, reports = args
    if problem == 'holdout_panel':
        protocol.validation.holdout_start = str(calendar[-1].date())
    elif problem == 'holdout_calendar':
        args[1] = calendar.append(pd.DatetimeIndex(['2026-01-01']))
    elif problem == 'index':
        args[2] = universe.iloc[:-1]
    elif problem == 'eligibility':
        universe['eligible'] = 1
    elif problem == 'infinite':
        factor.frame.iloc[125, 0] = np.inf
    elif problem == 'sample':
        reports['candidate']['by_fold'][1]['dataset']['evaluation_index_hash'] = 'changed'
    with pytest.raises(QualityError):
        run(args)


def test_ineligible_members_and_unrelated_training_rows_do_not_pollute_factor_diagnostics(observations):
    panel, calendar, universe, baseline, factor, protocol, reports = copy.deepcopy(observations)
    # Training-only changes cannot affect any reported selection-period diagnostic.
    expected = run((panel, calendar, universe, baseline, factor, protocol, reports))
    training = panel.index.get_level_values('datetime') < calendar[120]
    baseline.frame.loc[training, 'opposite'] = 1e9
    factor.frame.loc[training, 'factor'] = -1e9
    assert run((panel, calendar, universe, baseline, factor, protocol, reports)) == expected


def test_constant_cross_section_has_unknown_ic_not_fabricated_zero(observations):
    args = list(copy.deepcopy(observations))
    args[4].frame.iloc[:, 0] = 1.
    result = run(args)
    rows = result['predictive_metrics']['factor_by_fold']
    assert rows[0]['ic'] is None and rows[0]['rank_ic'] is None and rows[0]['effective_dates'] == 0
    assert all(row['rank_ic'] is None for row in result['factor_diagnostics']['decay'])
    canonical_json(result)


def test_compact_feedback_preserves_diagnostics_and_strips_nested_private_fields(observations):
    from etf_ml.adapters.rdagent.feedback import compact_diagnostics
    result = run(observations)
    result['private'] = {'holdout': '2026-01-01'}
    result['data_quality']['path'] = 'C:/private'
    result['predictive_metrics']['factor_by_fold'][0]['holdout'] = '2026-01-01'
    result['portfolio_metrics']['by_fold'][0]['candidate']['model_path'] = 'C:/private'
    result['robustness']['cost_stress'][0]['baseline']['secret'] = 'private-key'
    result['factor_diagnostics']['redundancy'].update({f'extra_{i}': i / 20 for i in range(10)})
    compact = compact_diagnostics(result)
    encoded = canonical_json(compact)
    assert all(word not in encoded for word in ('by_date', '2026', 'C:/', 'secret', 'private'))
    assert compact['predictive_metrics']['factor_by_fold'][0]['rank_ic'] == pytest.approx(1.)
    assert compact['robustness']['cost_stress'][0]['candidate']['total_execution_cost'] == 34.
    assert len(compact['factor_diagnostics']['most_correlated_features']) == 3
    assert compact['factor_diagnostics']['compared_features'] == 11
    assert len(encoded) < len(canonical_json(result)) / 3


def feedback(values, *, status='accepted', change=None):
    from etf_ml.adapters.rdagent.feedback import ETFFeedback
    diagnostics = run(values)
    protocol = values[5]
    if change:
        diagnostics.update(change)
    row = {'factor_id': 'synthetic', 'status': status, 'development_diagnostics': diagnostics,
           'direction': 'negative', 'applicable_scope': 'domestic_equity',
           'evaluation': {'baseline_id': 'base', 'candidate_id': 'candidate', 'run_id': 'trial-1',
                          'reasons': ['all_frozen_gates_passed']},
           'group_ablation': {'status': 'accepted', 'group': 'trend', 'reasons': ['group_ablation_confirmed'],
                              'paired_deltas': [{'fold': 'fixture', 'seed': 42, 'excess_return': .02}]}}
    result = {'stage': 'factor_selection', 'protocol_id': protocol.protocol_id, 'status': status,
              'by_candidate': [row]}
    scenario = SimpleNamespace(session=SimpleNamespace(protocol=protocol))
    return ETFFeedback(scenario).generate_feedback(SimpleNamespace(result=result), None)


@pytest.mark.parametrize('field,value', [('stage', 'final_acceptance'), ('protocol_id', 'other'),
                                        ('baseline_id', 'other'), ('candidate_id', 'other')])
def test_feedback_rejects_mismatched_or_final_diagnostics(observations, field, value):
    with pytest.raises(QualityError):
        feedback(observations, change={field: value})


def test_observations_cannot_promote_rejected_candidate(observations):
    result = feedback(observations, status='rejected')
    assert result.decision is False
    row = result.structured['by_candidate'][0]
    assert row['observations']['predictive_metrics']['factor_by_fold'][0]['ic'] == pytest.approx(1.)
    assert row['status'] == 'rejected'
    assert row['direction'] == 'negative' and row['applicable_scope'] == 'domestic_equity'
    assert row['group_paired_deltas'][0]['excess_return'] == .02


def test_missing_diagnostics_and_nonfinite_fields_stay_unknown():
    from etf_ml.adapters.rdagent.feedback import compact_diagnostics
    assert compact_diagnostics(None) == {'status': 'unavailable'}
    result = compact_diagnostics({'data_quality': {'coverage': np.inf, 'nonfinite': True},
                                  'predictive_metrics': {'factor_by_fold': [{'ic': np.nan, 'rank_ic': 0.}]}})
    assert result['data_quality']['coverage'] is None and result['data_quality']['nonfinite'] is None
    assert result['predictive_metrics']['factor_by_fold'][0]['ic'] is None
    assert result['predictive_metrics']['factor_by_fold'][0]['rank_ic'] == 0.
    canonical_json(result)
