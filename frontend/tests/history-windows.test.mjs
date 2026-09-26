import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const source = readFileSync(new URL('../src/components/SetupPanel.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('setup.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const declaration = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'HistoryWindowStatus');
assert.ok(declaration);
const compiled = ts.transpileModule(declaration.getText(ast), {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
}).outputText;
const Component = new Function('React', `${compiled}\nreturn HistoryWindowStatus;`)(React);
const render = overrides => renderToStaticMarkup(React.createElement(Component, { row: {
  component: 'GRAPH', configured_window_days: 180, serving_window_days: 365,
  serving_version: 'published-1', legacy_full_history: false, ...overrides,
}}));

test('configured and serving windows are distinct with pending publication', () => {
  const html = render({});
  assert.match(html, /Configured history window/);
  assert.match(html, /180 days/);
  assert.match(html, /Serving history window/);
  assert.match(html, /365 days/);
  assert.match(html, /Window change pending.*snapshot publication/);
});
test('matching windows do not suggest a pending change', () => {
  assert.doesNotMatch(render({ serving_window_days: 180 }), /pending/);
});
test('tabular legacy artifacts remain explicitly full history until retrained', () => {
  const html = render({ component: 'RF', configured_window_days: 365, serving_window_days: null, legacy_full_history: true });
  assert.match(html, /Full history \(legacy model\)/);
  assert.match(html, /training and model publication/);
});
test('absent serving evidence is not replaced with the configured value', () => {
  assert.match(render({ serving_window_days: null }), /Not recorded in this run/);
  assert.doesNotMatch(render({ serving_window_days: null }), /Window change pending/);
  assert.match(render({ serving_version: null }), /No serving model \/ snapshot/);
});
test('setup exposes all three separate tenant window controls', () => {
  for (const key of ['graph_window_days', 'gnn_window_days', 'tabular_window_days']) {
    assert.match(source, new RegExp(`numField\\('${key}'`));
  }
  assert.match(source, /HistoryWindowStatus row=\{row\}/);
});
