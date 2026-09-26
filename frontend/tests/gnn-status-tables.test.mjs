import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const source = readFileSync(new URL('../src/components/SetupPanel.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('setup.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declarations = ast.statements.filter(node =>
  (ts.isFunctionDeclaration(node) && node.name?.text === 'GnnFinalTestTables') ||
  (ts.isVariableStatement(node) && node.declarationList.declarations.some(item =>
    ['asRecord', 'diagnosticLabel'].includes(item.name.getText(ast)))),
);
assert.equal(declarations.length, 3);
const compiled = ts.transpileModule(declarations.map(node => node.getText(ast)).join('\n'), {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
}).outputText;
const Component = new Function('React', `${compiled}\nreturn GnnFinalTestTables;`)(React);
const render = diagnostics => renderToStaticMarkup(React.createElement(Component, { diagnostics }));

test('final-test metrics compare GNN with both MLP baselines in one headered table', () => {
  const html = render({
    final_test_overall: { pr_auc: .13, roc_auc: .831, base_rate: .02, lift: 6.485, brier_score: .019, count: 3000, positive_count: 60, negative_count: 2940 },
    raw_mlp_overall: { pr_auc: .012, roc_auc: .195, base_rate: .02, lift: .592, brier_score: .236, count: 3000, positive_count: 60, negative_count: 2940 },
    engineered_graph_mlp_overall: { pr_auc: .018, roc_auc: .446, base_rate: .02, lift: .923, brier_score: .038, count: 3000, positive_count: 60, negative_count: 2940 },
  });
  assert.equal((html.match(/<table /g) || []).length, 1);
  for (const text of ['Final-test overall comparison', 'Selected GNN', 'Raw-feature MLP', 'Engineered-graph MLP', 'PR-AUC', 'ROC-AUC', 'Brier score', 'Fraud labels', 'Legitimate labels', '0.1300', '0.0120', '0.0180', '2.00%', '3,000', '2,940']) {
    assert.ok(html.includes(text), text);
  }
  assert.doesNotMatch(html, /\[object Object\]|Pr Auc:/);
});

test('head states have their own table with one row per pattern and meaningful columns', () => {
  const html = render({ head_states: {
    RING: { state: 'ACTIVE', training_positive_count: 30, final_test_positive_count: 10, final_test_pr_auc: .225 },
    SUPER_BENEFICIARY: { state: 'DIAGNOSTIC_ONLY', training_positive_count: 30, final_test_positive_count: 10, final_test_pr_auc: .042 },
  } });
  assert.equal((html.match(/<table /g) || []).length, 2);
  for (const text of ['Pattern-head states', 'Ring', 'Super Beneficiary', 'Active', 'Diagnostic Only', 'Training positives', 'Final-test positives', 'Final-test PR-AUC', '0.2250', '0.0420']) {
    assert.ok(html.includes(text), text);
  }
  assert.doesNotMatch(html, /Final Test Pr Auc:/);
});

test('zero metrics are retained and missing metrics are not invented', () => {
  const html = render({ final_test_overall: { pr_auc: 0, count: 0 }, raw_mlp_overall: null });
  assert.match(html, /0\.0000/);
  assert.match(html, /—/);
  assert.doesNotMatch(html, /NaN|undefined|Pattern-head states/);
});

test('the generic diagnostic chips do not duplicate these structured sections', () => {
  const hidden = ast.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(item => item.name.getText(ast) === 'HIDDEN_DIAGNOSTICS')).getText(ast);
  for (const key of ['final_test_overall', 'raw_mlp_overall', 'engineered_graph_mlp_overall', 'head_states']) {
    assert.ok(hidden.includes(`'${key}'`));
  }
  assert.match(source, /<GnnFinalTestTables diagnostics=\{diagnostics\}/);
});
