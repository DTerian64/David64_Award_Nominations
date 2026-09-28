import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(
  new URL('../src/components/SetupPanel.tsx', import.meta.url),
  'utf8',
);

test('Setup exposes the Analytics Jobs tenant-admin sub-tab', () => {
  assert.match(source, /id: 'analyticsJobs', label: 'Analytics Jobs'/);
  assert.match(source, /sub === 'analyticsJobs'.*<AnalyticsJobsPanel/s);
});

test('Analytics Jobs reads and writes only the authenticated tenant endpoint', () => {
  const endpointMatches = source.match(/\/api\/admin\/setup\/analytics-job/g) || [];
  assert.equal(endpointMatches.length, 2);
  assert.doesNotMatch(source, /analytics-job\/\$\{.*tenant/i);
  assert.match(source, /JSON\.stringify\(\{ enabled: data\.enabled \}\)/);
});

test('pause copy preserves serving models and describes in-flight behavior', () => {
  assert.match(source, /Existing serving models and live nomination checks remain active/);
  assert.match(source, /Work already in progress is allowed to finish/);
  assert.match(source, /Run scheduled integrity analytics for this organization/);
});
