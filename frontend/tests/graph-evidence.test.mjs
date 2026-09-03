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

test('winner precedes expandable detector groups, preserving all findings', () => {
  const findings = Array.from({ length: 10 }, (_, i) => ({
    finding_hash: `ring-${i}`, pattern_type: 'Ring', finding_score: 80,
    affected_roles: ['nominator'], routing_relevant: true, detail: `ring evidence ${i}`,
  }));
  findings.push({ finding_hash: 'winner', pattern_type: 'CopyPaste', finding_score: 91,
    detail: 'Winning evidence', affected_roles: ['beneficiary'], routing_relevant: false });
  const html = render({ pattern_findings: findings, winning_pattern_type: 'Ring',
    fraud_score: 80, winning_finding: findings[0] });
  assert.ok(html.indexOf('Winning finding: Ring') < html.indexOf('<details'));
  assert.match(html, /Ring · 10 findings · Highest 80.00/);
  assert.match(html, /CopyPaste · 1 findings · Highest 91.00/);
  assert.match(html, /Not scoring this nomination/);
  assert.equal((html.match(/<details/g) ?? []).length, 13);
  for (let i = 0; i < 10; i++) assert.ok(html.includes(`ring evidence ${i}`));
});

test('legacy warning-only logs remain readable and grouped', () => {
  const html = render({ warning_flags: ['nominator: Ring (80, HIGH)', 'beneficiary: Ring (75, HIGH)'] });
  assert.match(html, /Ring · 2 findings/);
  assert.match(html, /nominator: Ring/);
  assert.doesNotMatch(html, /Winning finding/);
});

test('clean or unavailable logs do not invent findings', () => {
  assert.doesNotMatch(render({ pattern_findings: [], warning_flags: [] }), /<details|Winning finding/);
});
