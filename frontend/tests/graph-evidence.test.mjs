import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// Exercise the actual pure renderer without importing browser-only auth.
const source = readFileSync(new URL('../src/components/NominationLogsDrawer.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('drawer.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const functions = ast.statements.filter(node => ts.isFunctionDeclaration(node)
  && ['formatDetailValue', 'GraphEvidence'].includes(node.name?.text));
assert.equal(functions.length, 2);
const isolated = functions.map(node => node.getText(ast)).join('\n');
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
  assert.match(html, /Biggest contributor to score is Ring pattern: 10/);
  assert.match(html, /Maximum finding_score: 80 \/ 100/);
  assert.match(html, /ring evidence 0/);
  assert.doesNotMatch(html, /CopyPaste|Not scoring|ring evidence 1|<details/);
});

test('legacy warning-only logs derive one winner and count its pattern', () => {
  const html = render({ warning_flags: ['nominator: Ring (80, HIGH)', 'beneficiary: Ring (75, HIGH)'] });
  assert.match(html, /Biggest contributor to score is Ring pattern: 2/);
  assert.match(html, /Maximum finding_score: 80 \/ 100/);
  assert.match(html, /nominator: Ring/);
  assert.doesNotMatch(html, /beneficiary: Ring/);
});

test('clean or unavailable logs do not invent findings', () => {
  assert.doesNotMatch(render({ pattern_findings: [], warning_flags: [] }), /Biggest contributor/);
});
