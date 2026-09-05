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
  const html = renderEvidence({ engine_results: {
    rf: null,
    graph: {
      available: true, score: 88.2, risk_level: 'HIGH',
      winning_pattern_type: 'Ring', winning_pattern_count: 417,
      winning_finding: { pattern_type: 'Ring', finding_score: 88.2,
        derived_severity: 'HIGH', detail: 'Three-person reciprocal nomination cycle.',
        affected_roles: ['nominator'], affected_user_ids: [12, 15, 19], nomination_ids: [201, 202, 203] },
      findings: ['[Graph] nominator: Ring (88.20, HIGH)'],
    },
    gnn: null,
    semantic: null,
  } });
  assert.match(html, /Biggest contributor to score is/);
  assert.match(html, /Ring pattern: 417/);
  assert.match(html, /Nomination Ring/);
  assert.match(html, /Score 88.20/);
  assert.match(html, /Three-person reciprocal nomination cycle/);
  assert.match(html, /Affected users:<\/span> #12, #15, #19/);
  assert.match(html, /Nominations:<\/span> #201, #202, #203/);
  assert.equal((html.match(/nominator: Ring/g) || []).length, 0);
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
  assert.equal((html.match(/LLM explanation/g) || []).length, 1);
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
  assert.match(html, /Biggest contributor to score/);
  assert.equal((html.match(/Beneficiary is an outlier/g) || []).length, 1);
  assert.doesNotMatch(html, /Older duplicate/);
});
