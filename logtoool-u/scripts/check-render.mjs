#!/usr/bin/env node
/**
 * Render smoke-test for the dashboard views.
 *
 *     npm run check:render
 *
 * `tsc --noEmit` proves the types line up; it does not prove the component
 * survives being rendered. This bundles the real component and renders it to
 * a string against fixture data, then asserts on the produced markup.
 *
 * It exists because IlaBankView shipped with a crash on the empty-window
 * path -- `report.duration_stats.buckets` read against a `no_data` response
 * that carries neither field. Types now make that specific bug impossible,
 * but "it compiles" and "it renders" remain different claims, and only one
 * of them is what the user sees.
 *
 * Dependency-free beyond esbuild, which ships inside vite.
 */
import { build } from 'esbuild';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';

const OK = {
  status: 'ok', window_start: '2026-08-27T09:10:02Z', window_end: '2026-08-27T10:31:00Z',
  total_events_analyzed: 10, total_trackers: 4, untracked_events: 1, error_count: 2, warning_count: 1,
  error_rate: 0.2, trackers_with_errors: 2, multiline_entries: 2, sensitive_field_entries: 0,
  level_counts: { INFO: 7, ERROR: 2, WARN: 1 },
  event_type_counts: { request_end: 4, message: 3, error: 2, retry: 1 },
  parse_status_counts: { complete: 9, unrecognized: 1 },
  severity_granularity: 'hour',
  severity_timeline: [
    { bucket: '2026-08-27T09:00', error: 1, warn: 1, info: 4 },
    { bucket: '2026-08-27T10:00', error: 1, warn: 0, info: 3 },
  ],
  top_exceptions: { NullReferenceException: 15, WebException: 2 },
  top_stack_frames: { 'AFSMW_ILACreditServices.IBM_MQ.ConnectMQ_Out': 15 },
  failure_signatures: [
    { exception: 'NullReferenceException', exception_namespace: 'System', method: 'ConnectMQ_Out',
      owner: 'AFSMW_ILACreditServices.IBM_MQ', count: 15, share: 0.8824 },
    { exception: 'WebException', exception_namespace: 'System.Net', method: 'GetResponse',
      owner: 'System.Net.HttpWebRequest', count: 2, share: 0.1176 },
  ],
  headline_failure: { exception: 'NullReferenceException', exception_namespace: 'System', method: 'ConnectMQ_Out',
    owner: 'AFSMW_ILACreditServices.IBM_MQ', count: 15, share: 0.8824 },
  top_services: { PaymentService: 1, NotificationService: 1 },
  http_status_counts: { '500': 1, '504': 1 },
  duration_stats: {
    count: 5, p50_ms: 4120, p95_ms: 12300, max_ms: 12300,
    buckets: [
      { label: '<250ms', count: 1 }, { label: '250ms-500ms', count: 0 }, { label: '500ms-1s', count: 0 },
      { label: '1s-2s', count: 1 }, { label: '2s-5s', count: 2 }, { label: '5s-10s', count: 0 },
      { label: '>10s', count: 1 },
    ],
  },
  trackers: [{
    tracker_id: 'ILA-77001', entries: 4, errors: 1, warnings: 0,
    first_timestamp: '2026-08-27T09:10:02Z', last_timestamp: '2026-08-27T09:10:04Z',
    span_ms: 2000, event_types: ['message', 'error', 'retry'], exceptions: [],
  }],
  recent_errors: [{
    timestamp: '2026-08-27T09:10:02Z', tracker_id: 'ILA-77001',
    // Carries a PAN on purpose: the render must not put it in the DOM.
    message: 'Core adapter failed for CardNo=4111111111111111',
    exceptions: ['System.Net.Http.HttpRequestException'], http_codes: [500],
  }],
};

// A window where nothing failed and nothing was timed -- every "empty"
// branch on the page at once. This is the shape that crashed before.
const SPARSE = {
  ...OK,
  error_count: 0, warning_count: 0, error_rate: 0, trackers_with_errors: 0, total_trackers: 0,
  top_exceptions: {}, top_stack_frames: {}, top_services: {}, http_status_counts: {},
  failure_signatures: [], headline_failure: null,
  duration_stats: { count: 0, p50_ms: null, p95_ms: null, max_ms: null, buckets: OK.duration_stats.buckets.map((b) => ({ ...b, count: 0 })) },
  trackers: [], recent_errors: [], severity_timeline: [],
};

const ASSERTIONS = {
  OK: [
    ['tracker row', 'ILA-77001'], ['error-rate tile', '20.0%'], ['p95 tile', '12.30 s'],
    ['headline exception', 'NullReferenceException'], ['headline throw site', 'ConnectMQ_Out'],
    ['headline share', '88.2%'], ['owning type', 'AFSMW_ILACreditServices.IBM_MQ'],
    ['secondary signature', 'GetResponse'],
    ['http code', '500'], ['capture fidelity %', '90.0%'], ['masked PAN', '************1111'],
  ],
  SPARSE: [
    ['durations empty state', 'No explicit durations logged'],
    ['failures empty state', 'No exceptions thrown in this window'],
    ['http empty state', 'No HTTP status codes'],
    ['trackers empty state', 'No tracker-correlated transactions'],
    ['severity empty state', 'No timestamped entries'],
  ],
};

const dir = mkdtempSync(join(tmpdir(), 'render-'));
const entry = 'node_modules/.render-entry.tsx';   // inside the project so imports resolve
const out = join(dir, 'bundle.cjs');

writeFileSync(
  entry,
  `import { createElement } from 'react';
   import { renderToString } from 'react-dom/server';
   import { IlaReportBody } from '../src/components/IlaBankView';
   export function render(report: any) { return renderToString(createElement(IlaReportBody, { report })); }`,
);

try {
  await build({
    entryPoints: [entry], bundle: true, format: 'cjs', platform: 'node', outfile: out,
    jsx: 'automatic', logLevel: 'error',
    plugins: [{
      name: 'stub-api',
      setup(b) {
        b.onResolve({ filter: /\/api$/ }, () => ({ path: 'api', namespace: 'stub' }));
        b.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
          contents: 'class ApiError extends Error {}; module.exports = { api: { get: async () => ({}) }, ApiError };',
          loader: 'js',
        }));
      },
    }],
  });

  const { render } = createRequire(import.meta.url)(out);
  let failures = 0;

  for (const [name, report] of [['OK', OK], ['SPARSE', SPARSE]]) {
    let html;
    try {
      html = render(report);
    } catch (err) {
      console.error(`${name}: CRASHED while rendering — ${err.message}`);
      failures++;
      continue;
    }
    const missing = ASSERTIONS[name].filter(([, needle]) => !html.includes(needle));
    if (missing.length) {
      failures++;
      console.error(`${name}: missing from rendered output — ${missing.map((m) => m[0]).join(', ')}`);
    } else {
      console.log(`${name}: rendered, ${ASSERTIONS[name].length} assertions present`);
    }
  }

  if (render(OK).includes('4111111111111111')) {
    failures++;
    console.error('LEAK: an unmasked PAN reached the rendered DOM');
  } else {
    console.log('masking: no unmasked PAN in rendered output');
  }

  if (failures) process.exit(1);
} finally {
  rmSync(dir, { recursive: true, force: true });
  rmSync(entry, { force: true });
}
