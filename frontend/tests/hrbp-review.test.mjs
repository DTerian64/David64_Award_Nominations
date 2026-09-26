import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Exercise the real rendering component without initializing browser-only auth.
const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/components/HRBPReviewTab.tsx', import.meta.url))],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'cjs',
  packages: 'external',
  plugins: [{
    name: 'isolate-browser-auth',
    setup(builder) {
      builder.onResolve({ filter: /ImpersonationContext$/ }, () => ({
        path: 'auth-context', namespace: 'test',
      }));
      builder.onLoad({ filter: /.*/, namespace: 'test' }, () => ({
        contents: 'export function useImpersonation() { throw new Error("Auth should not be used by evidence rendering"); }',
        loader: 'js',
      }));
    },
  }],
});
const componentModule = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
  createRequire(import.meta.url), componentModule, componentModule.exports,
);
const { EngineVerdicts } = componentModule.exports;

function renderEvidence(overrides = {}) {
  return renderToStaticMarkup(React.createElement(EngineVerdicts, { item: {
    decision_source: 'integrity_v2',
    review_scope: null,
    final_route: 'MANAGER_APPROVAL',
    status: 'Approved',
    decisive_engines: [],
    engine_results: {
      rf: { available: true, score: 15, risk_level: 'LOW', findings: [] },
      graph: null, gnn: null, semantic: null,
    },
    ...overrides,
  } }));
}

test('non-review nomination with null scope retains its evidence without crashing', () => {
  const html = renderEvidence();
  assert.match(html, /Review scope: Not applicable/);
  assert.match(html, /LOW/);
  assert.doesNotMatch(html, /LEGACY FRAUD/);
});

test('pending HRBP nomination with missing scope shows unavailable, not a guessed scope', () => {
  const html = renderEvidence({ status: 'PendingHRBPReview', final_route: 'HRBP_REVIEW' });
  assert.match(html, /Review scope: Unavailable/);
});

test('pending HRBP status with conflicting route does not report scope as not applicable', () => {
  assert.match(renderEvidence({ status: 'PendingHRBPReview' }), /Review scope: Unavailable/);
});

test('unassessed nomination with no decision handles null scope', () => {
  assert.match(renderEvidence({ decision_source: null, final_route: null, status: 'Submitted' }), /Review scope: Unavailable/);
});

test('known scopes remain readable', () => {
  for (const scope of ['FRAUD', 'SEMANTIC', 'FRAUD_AND_SEMANTIC', 'LEGACY_FRAUD']) {
    assert.ok(renderEvidence({ review_scope: scope }).includes(`Review scope: ${scope.replaceAll('_', ' ')}`));
  }
});

test('Graph verdict shows its biggest contributor and maximum finding_score', () => {
  const html = renderEvidence({ nominator_id: 242, beneficiary_id: 198, engine_results: {
    rf: null,
    graph: {
      available: true, score: 88.2, risk_level: 'HIGH',
      winning_pattern_type: 'Ring', winning_pattern_count: 417,
      winning_finding: { pattern_type: 'Ring', finding_score: 88.2,
        derived_severity: 'HIGH', detail: 'Three-person reciprocal nomination cycle.',
        evidence_scope: 'CURRENT_NOMINATION', evaluation_mode: 'CANDIDATE_EDGE',
        affected_roles: ['nominator', 'beneficiary'], affected_user_ids: [12, 15, 19], nomination_ids: [201, 202, 203] },
      candidate_detector_scores: [
        { detector: 'Ring', score: 88.2, eligible: true, enabled_for_routing: true, state: 'SCORING' },
        { detector: 'CopyPaste', score: 72.4, eligible: true, enabled_for_routing: true, state: 'SCORING' },
        { detector: 'SuperBeneficiary', score: 55.25, eligible: false, enabled_for_routing: true,
          state: 'NOT_SCORING', eligibility_reasons: ['unique nominators 2/4'] },
        { detector: 'Desert', score: 40, eligible: true, enabled_for_routing: false, state: 'ANALYTICS_ONLY' },
      ],
      findings: ['[Graph] nominator: Ring (88.20, HIGH)'],
    },
    gnn: null,
    semantic: null,
  } });
  assert.match(html, /Biggest score contributor/);
  assert.match(html, /Nomination Ring/);
  assert.doesNotMatch(html, /417 relevant/);
  assert.match(html, /Score 88.20/);
  assert.match(html, /Three-person reciprocal nomination cycle/);
  assert.match(html, /Affected roles:<\/span> nominator #242, beneficiary #198/);
  assert.match(html, /Affected users:<\/span> #12, #15, #19/);
  assert.match(html, /Nominations:<\/span> #201, #202, #203/);
  assert.match(html, /Other detector scores/);
  assert.match(html, /Copy-Paste Fraud.*72\.40/s);
  assert.match(html, /Copy-Paste Fraud.*72\.40.*Eligible.*lower than the winning detector/s);
  assert.match(html, /Super Beneficiary.*55\.25.*Not eligible.*unique nominators 2\/4/s);
  assert.match(html, /Nomination Desert.*40\.00.*Analytics only.*Excluded from routing by policy/s);
  assert.doesNotMatch(html, /Nomination Ring.*Eligible.*88\.20/s);
  assert.equal((html.match(/nominator: Ring/g) || []).length, 0);
});

test('Random Forest verdict renders its ranked SHAP factors inside the engine card', () => {
  const html = renderEvidence({ engine_results: {
    rf: {
      available: true, score: 91, model_probability: 0.918, risk_level: 'CRITICAL',
      findings: ['Repeated beneficiary'],
      explanation: { top_features: [
        { feature: 'NominatorUniqueBeneficiaries', raw_value: 6, contribution: 0.097 },
        { feature: 'PairNominationCount', raw_value: 9, contribution: 0.067 },
        { feature: 'HasReciprocalNomination', raw_value: 1, contribution: -0.023 },
      ] },
    },
    graph: null, gnn: null, semantic: null,
  } });
  assert.match(html, /Top SHAP factors/);
  assert.match(html, /Nominator unique beneficiaries/);
  assert.match(html, /\+9\.7 pp/);
  assert.match(html, /Same nominator.*beneficiary pair count/s);
  assert.match(html, /-2\.3 pp/);
});

test('Graph participant Ring history is displayed but explicitly excluded from scoring', () => {
  const html = renderEvidence({ engine_results: {
    rf: null,
    graph: {
      available: true, score: 0, risk_level: 'NONE', findings: [],
      nominator_history: [{ pattern_type: 'Ring' }],
      beneficiary_history: [{ pattern_type: 'Ring' }, { pattern_type: 'Ring' }],
      shared_history: [{ pattern_type: 'Ring' }],
    },
    gnn: null,
    semantic: null,
  } });
  assert.match(html, /Participant graph history/);
  assert.match(html, /Context only.*not included in the Ring score/);
  assert.match(html, /Nominator: 1 historical ring finding/);
  assert.match(html, /Beneficiary: 2 historical ring findings/);
  assert.match(html, /Both participants: 1 shared historical ring finding/);
});

test('RF narrative and Semantic description belong to their engine cards', () => {
  const semanticReason = 'Description needs stronger category evidence.';
  const rfExplanation = 'SHAP factors explain the RF score.';
  const html = renderEvidence({
    llm_explanation: rfExplanation,
    warning_flags: [`[Description] ${semanticReason}`],
    engine_results: {
      rf: { available: true, score: 61, risk_level: 'MEDIUM', findings: [],
        explanation: { llm_text: rfExplanation } },
      graph: null,
      gnn: null,
      semantic: { available: true, combined_decision: {
        action: 'flag', checks: ['category_alignment'], reason: semanticReason,
      } },
    },
  });
  assert.equal((html.match(/LLM explanation/g) || []).length, 2); // Narrative and explicit lifecycle.
  assert.equal((html.match(/SHAP factors explain/g) || []).length, 1);
  assert.equal((html.match(/Semantic finding/g) || []).length, 1);
  assert.equal((html.match(/Description needs stronger/g) || []).length, 1);
  assert.match(html, /category alignment/);
});

test('older Graph evidence without a pattern type retains one maximum finding', () => {
  const html = renderEvidence({ engine_results: {
    rf: null,
    graph: { available: true, score: 50, risk_level: 'MEDIUM',
      findings: ['[Graph] Beneficiary is an outlier', '[Graph] Older duplicate'] },
    gnn: null,
    semantic: null,
  } });
  assert.match(html, /Biggest score contributor/);
  assert.equal((html.match(/Beneficiary is an outlier/g) || []).length, 1);
  assert.doesNotMatch(html, /Older duplicate/);
});

test('Tabular MLP is correctly named and explicitly shows skipped attribution', () => {
  const html = renderEvidence({
    top_features: [{ feature: 'Amount', raw_value: 500, contribution: 0.4 }],
    llm_explanation: 'Old RF narrative',
    engine_results: { rf: {
      available: true, architecture: 'tabular_mlp', score: 21, risk_level: 'LOW',
      explanation: { shap_status: 'SKIPPED', shap_reason: 'risk_below_medium', top_features: [],
        llm_status: 'SKIPPED', llm_reason: 'risk_below_medium', llm_text: null },
    } },
  });
  assert.match(html, /Tabular MLP/);
  assert.match(html, /SHAP: Not called/);
  assert.match(html, /Risk below medium/);
  assert.match(html, /LLM explanation: Not called/);
  assert.doesNotMatch(html, /Random Forest|Top SHAP factors|Old RF narrative/);
});

test('GNN evidence is always visible in the default HRBP view, including skip reason', () => {
  const html = renderEvidence({ engine_results: { gnn: {
    available: true, architecture: 'gatv2', score: 0, risk_level: 'NONE',
    causal_context: { window_days: 365, eligible_edge_count: 320,
      features: { LogReverseThreeHopPathCount: Math.log1p(118) } },
    feature_inputs: { features: [{ name: 'Amount', pre_scaler_value: 300, model_input_value: 0.5 }] },
    pattern_heads: { RING: { status: 'ACTIVE', probability: 0.0011 },
      SUPER_BENEFICIARY: { status: 'DIAGNOSTIC_ONLY' } },
    explanation: { status: 'NOT_REQUESTED', reason: 'BELOW_TRIGGER_RISK' },
  } } });
  for (const value of ['GNN inference summary', 'Causal graph signals', 'Complete GNN feature vector',
    '118', 'Shared-model pattern heads', 'No live claim', 'GNNExplainer: Not called', 'Below trigger risk']) {
    assert.ok(html.includes(value), value);
  }
});

test('historical missing attribution is explicit, and failures are not rendered as success', () => {
  assert.match(renderEvidence({ engine_results: { gnn: { available: true } } }), /GNNExplainer: Not recorded/);
  const html = renderEvidence({ engine_results: { gnn: { available: true,
    explanation: { status: 'FAILED', reason: 'EXPLANATION_ENGINE_NOT_DEPLOYED' } } } });
  assert.match(html, /GNNExplainer: Failed/);
  assert.match(html, /Explanation engine not deployed/);
});

test('above-threshold Tabular MLP explicitly states that its local explainer is unavailable', () => {
  const html = renderEvidence({ engine_results: { rf: {
    available: true, architecture: 'tabular_mlp', score: 65, risk_level: 'HIGH',
    explanation: { shap_status: 'SKIPPED', shap_reason: 'architecture_explainer_unavailable', top_features: [] },
  } } });
  assert.match(html, /SHAP: Not called/);
  assert.match(html, /Architecture explainer unavailable/);
  assert.doesNotMatch(html, /Top SHAP factors/);
});
