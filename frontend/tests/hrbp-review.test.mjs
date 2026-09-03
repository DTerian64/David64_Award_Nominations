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
