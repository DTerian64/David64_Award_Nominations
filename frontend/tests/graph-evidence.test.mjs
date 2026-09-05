import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Exercise the actual pure renderer without importing browser-only auth.
const source = readFileSync(new URL('../src/components/NominationLogsDrawer.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('drawer.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = ast.statements.filter(node =>
  (ts.isFunctionDeclaration(node) && ['formatDetailValue', 'GraphEvidence'].includes(node.name?.text))
  || (ts.isVariableStatement(node) && node.getText(ast).includes('GRAPH_PATTERN_LABELS')),
);
assert.equal(declarations.length, 3);
const isolated = declarations.map(node => node.getText(ast)).join('\n');
const compiled = ts.transpileModule(isolated, {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
}).outputText;
const GraphEvidence = new Function('React', `${compiled}\nreturn GraphEvidence;`)(React);
const render = extras => renderToStaticMarkup(React.createElement(GraphEvidence, { extras }));

test('only the biggest-contributing pattern and its maximum finding are shown', () => {
  const findings = Array.from({ length: 10 }, (_, i) => ({
    finding_hash: `ring-${i}`, pattern_type: 'Ring', finding_score: 80,
    affected_roles: ['nominator'], routing_relevant: true, detail: `ring evidence ${i}`,
  }));
  findings.push({ finding_hash: 'winner', pattern_type: 'CopyPaste', finding_score: 91,
    detail: 'Winning evidence', affected_roles: ['beneficiary'], routing_relevant: false });
  const html = render({ pattern_findings: findings, winning_pattern_type: 'Ring',
    winning_pattern_count: 10, fraud_score: 80, winning_finding: findings[0] });
  assert.match(html, /Biggest score contributor/);
  assert.match(html, /Nomination Ring · 10 relevant findings/);
  assert.match(html, /Maximum finding_score: 80 \/ 100/);
  assert.match(html, /ring evidence 0/);
  assert.doesNotMatch(html, /CopyPaste|Not scoring|ring evidence 1|<details/);
});

test('legacy warning-only logs derive one winner and count its pattern', () => {
  const html = render({ warning_flags: ['nominator: Ring (80, HIGH)', 'beneficiary: Ring (75, HIGH)'] });
  assert.match(html, /Biggest score contributor/);
  assert.match(html, /Nomination Ring · 2 relevant findings/);
  assert.match(html, /Maximum finding_score: 80 \/ 100/);
  assert.match(html, /nominator: Ring/);
  assert.doesNotMatch(html, /beneficiary: Ring/);
});

test('clean or unavailable logs do not invent findings', () => {
  assert.doesNotMatch(render({ pattern_findings: [], warning_flags: [] }), /Biggest contributor/);
});

test('candidate edge winner omits historical count and renders participant history as context', () => {
  const html = render({
    pattern_findings: [{ pattern_type: 'Ring', finding_score: 92, routing_relevant: true,
      evidence_scope: 'CURRENT_NOMINATION', evaluation_mode: 'CANDIDATE_EDGE',
      detail: 'Current nomination completes directed ring: 2 → 3 → 1 → 2' }],
    candidate_findings: [{ pattern_type: 'Ring', finding_score: 92, routing_relevant: true,
      evidence_scope: 'CURRENT_NOMINATION', evaluation_mode: 'CANDIDATE_EDGE',
      detail: 'Current nomination completes directed ring: 2 → 3 → 1 → 2' }],
    nominator_history: [{ pattern_type: 'Ring', finding_score: 88, routing_relevant: false }],
    beneficiary_history: [{ pattern_type: 'Ring', finding_score: 84, routing_relevant: false }],
    winning_pattern_type: 'Ring', winning_pattern_count: 1,
    fraud_score: 92,
    winning_finding: { pattern_type: 'Ring', finding_score: 92,
      evidence_scope: 'CURRENT_NOMINATION', evaluation_mode: 'CANDIDATE_EDGE',
      detail: 'Current nomination completes directed ring: 2 → 3 → 1 → 2' },
  });
  assert.match(html, /Biggest score contributor/);
  assert.match(html, /Nomination Ring/);
  assert.doesNotMatch(html, /relevant finding/);
  assert.match(html, /Current nomination completes directed ring/);
  assert.match(html, /Participant graph history/);
  assert.match(html, /Context only — not included in the Ring score/);
  assert.match(html, /Nominator: 1 historical ring finding/);
  assert.match(html, /Beneficiary: 1 historical ring finding/);
});
