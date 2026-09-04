// Run from frontend: node --test tests/user-analysis.test.mjs
// Render the actual User Analysis components without browser authentication.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/components/UserAnalysisTab.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external',
  plugins: [{
    name: 'isolate-browser-auth',
    setup(builder) {
      builder.onResolve({ filter: /ImpersonationContext$/ }, () => ({ path: 'auth', namespace: 'test' }));
      builder.onLoad({ filter: /.*/, namespace: 'test' }, () => ({
        contents: 'export function useImpersonation() { throw new Error("Unexpected auth access"); }', loader: 'js',
      }));
    },
  }],
});
const componentModule = { exports: {} };
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(
  createRequire(import.meta.url), componentModule, componentModule.exports,
);
const { UserAnalysisSummary, UserNominationEvidence, UserAnalysisTab } = componentModule.exports;
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

test('initial user search is explicit and read-only', () => {
  const html = render(UserAnalysisTab, {
    apiFetch: () => { throw new Error('Do not fetch the directory before search'); },
    onOpenAnalysis: () => {}, onOpenLogs: () => {},
  });
  assert.match(html, /Name, email, or user ID/);
  assert.match(html, /Engine signals are not proof of wrongdoing/);
  assert.doesNotMatch(html, /Confirm integrity concern|Approve nomination/);
});

test('summary distinguishes explicit exclusion, missing evidence and cleared outcomes', () => {
  const html = render(UserAnalysisSummary, { summary: {
    total: 10, engine_concerns: 5, confirmed_issues: 2, cleared_concerns: 1,
    unsubstantiated: 1, not_for_training: 2, missing_evidence: 3,
  } });
  for (const label of ['Human-confirmed issues', 'Cleared — no concern', 'Cleared — unsubstantiated', 'Not for training', 'No inference recorded']) {
    assert.ok(html.includes(label));
  }
  assert.match(html, /Categories overlap/);
  assert.match(html, /Confirmed issues include semantic concerns/);
  assert.match(html, /explicit EXCLUDED disposition, not a missing label/);
});

test('all four engines show findings, semantic review and unavailable states', () => {
  const html = render(UserNominationEvidence, { item: { engines: {
    rf: { available: true, score: 7, risk_level: 'NONE', concern: true, findings: ['Reciprocal nominations'] },
    graph: { available: true, score: 50, risk_level: 'MEDIUM', concern: true, findings: ['Graph outlier'] },
    gnn: { available: false, score: 0, risk_level: 'NONE', concern: false, unavailable_reason: 'BELOW_MINIMUM_VOLUME' },
    semantic: { available: true, concern: true, combined_decision: { action: 'flag', checks: ['Category concern'], reason: 'Needs human review' } },
  } } });
  for (const label of ['RF', 'Graph Analytics', 'GNN', 'Semantic', 'Reciprocal nominations', 'Graph outlier', 'Category concern', 'Needs human review', 'BELOW_MINIMUM_VOLUME']) {
    assert.ok(html.includes(label));
  }
  assert.doesNotMatch(html, /NONE · Score 0/);
  assert.doesNotMatch(html, /\[object Object\]/);
});

test('missing inference is not presented as a clean engine assessment', () => {
  const html = render(UserNominationEvidence, { item: { engines: {} } });
  assert.equal((html.match(/Unavailable \/ not recorded/g) || []).length, 4);
  assert.doesNotMatch(html, /NONE|Score 0/);
});
