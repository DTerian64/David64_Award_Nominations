import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadEvidenceModule } from './evidence-module.mjs';

const { ModelLogEvidence } = await loadEvidenceModule('../src/components/NominationLogsDrawer.tsx');
const render = (text, extras) => renderToStaticMarkup(React.createElement(ModelLogEvidence, { text, extras }));

test('Nomination Logs show the stored Tabular MLP and explicit skipped attribution', () => {
  const html = render('Tabular assessment completed', { engine_result: {
    available: true, architecture: 'tabular_mlp', score: 21, model_probability: 0.2199,
    explanation: { shap_status: 'SKIPPED', shap_reason: 'risk_below_medium', top_features: [],
      llm_status: 'SKIPPED', llm_reason: 'risk_below_medium' },
  } });
  assert.match(html, /Tabular MLP/);
  assert.match(html, /Probability 21.99%/);
  assert.match(html, /SHAP: Not called/);
  assert.match(html, /Risk below medium/);
  assert.doesNotMatch(html, /Random Forest|Top SHAP factors/);
});

test('Nomination Logs render GNN heads and features rather than raw JSON', () => {
  const html = render('GNN assessment completed', { engine_result: {
    available: true, architecture: 'gatv2', score: 0, model_probability: 0,
    pattern_heads: { RING: { status: 'ACTIVE', probability: 0.0002 } },
    causal_context: { features: { LogPriorDirectedPairCount: Math.log1p(30) } },
    explanation: { status: 'NOT_REQUESTED', reason: 'BELOW_TRIGGER_RISK' },
  } });
  assert.match(html, /Shared-model pattern heads/);
  assert.match(html, /0.02%/);
  assert.match(html, /Causal graph signals/);
  assert.match(html, /GNNExplainer: Not called/);
  assert.match(html, /Below trigger risk/);
});

test('historical compact logs preserve skip status and do not invent absent evidence', () => {
  assert.match(render('Random Forest assessment completed', {
    model_available: true, architecture: 'random_forest', fraud_score: 12,
    shap_status: 'SKIPPED', shap_reason: 'risk_below_medium',
  }), /SHAP: Not called/);
  const html = render('GNN assessment completed', { model_available: true, fraud_score: 0 });
  assert.match(html, /GNNExplainer: Not recorded/);
  assert.doesNotMatch(html, /Shared-model pattern heads|Causal graph signals/);
});

test('completed RF attribution remains available in Nomination Logs', () => {
  const html = render('Tabular assessment completed', { engine_result: {
    available: true, architecture: 'random_forest', score: 65,
    explanation: { shap_status: 'COMPLETED', top_features: [
      { feature: 'Amount', raw_value: 500, contribution: 0.12 },
    ] },
  } });
  assert.match(html, /SHAP: Completed/);
  assert.match(html, /Top SHAP factors/);
  assert.match(html, /\+12.0 pp/);
});

test('asynchronous explanation events show running and failed lifecycle honestly', () => {
  assert.match(render('GNN explanation started', {}), /GNNExplainer: Running/);
  const html = render('GNN explanation failed', { reason: 'EXPLANATION_ENGINE_NOT_DEPLOYED' });
  assert.match(html, /GNNExplainer: Failed/);
  assert.match(html, /Explanation engine not deployed/);
});
