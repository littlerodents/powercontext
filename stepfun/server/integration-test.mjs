/** Real seekdb-js HTTP integration test. Uses new, task-specific collections; no existing data is removed. */
import { writeFile } from 'node:fs/promises';
import { performance } from 'node:perf_hooks';

const base = process.env.STEPFUN_BASE_URL || 'http://127.0.0.1:8787';
const collection = `sf_it_${Date.now()}`;
const profile = 'integration-model@v1:q8';
const otherProfile = 'integration-model@v1:fp32';
const vec = (n) => Array.from({ length: 384 }, (_, i) => i === n ? 1 : 0);
const checks = [];
function check(label, condition, detail = '') {
  checks.push({ label, passed: Boolean(condition), detail });
  console.log(`${condition ? 'PASS' : 'FAIL'} ${label}${detail ? `: ${detail}` : ''}`);
}
async function post(path, body, expectOk = true) {
  const response = await fetch(base + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (expectOk && !response.ok) throw new Error(`${path}: ${JSON.stringify(data)}`);
  return { response, data };
}
const rows = [
  { doc_id: 'file.txt', chunk_no: 0, title: '<b>literal title</b>', content: 'Refund requests are accepted within thirty days.', content_sha256: 'hash-a', metadata: {}, embedding: vec(0) },
  { doc_id: 'file.txt', chunk_no: 1, title: 'file.txt', content: 'A receipt is required for a refund.', content_sha256: 'hash-b', metadata: {}, embedding: vec(1) },
];
const select = { collection, profile };
const health = await (await fetch(base + '/api/health')).json();
check('seekdb bridge connected', health.status === 'ok');
const before = await post('/api/known', { ...select, rows });
check('new chunks are unknown', before.data.known.every(value => value === false));
await post('/api/upsert', { ...select, dim: 384, rows });
const known = await post('/api/known', { ...select, rows });
check('unchanged chunks are detected before embedding', known.data.known.every(Boolean));
const changed = await post('/api/known', { ...select, rows: [{ ...rows[0], content_sha256: 'changed' }] });
check('changed content requires embedding', changed.data.known[0] === false);
const refresh = await post('/api/refresh', select);
check('refresh exposes both chunks', refresh.data.refreshed && refresh.data.visible_rows === 2, JSON.stringify(refresh.data));
const query = { ...select, query: 'refund', query_vector: vec(0), top_k: 5 };
const search = await post('/api/search', query);
check('hybrid search returns stored document', search.data.results.some(hit => hit.doc_id === 'file.txt'));
check('search result includes original text and rank', search.data.results.some(hit => hit.content === rows[0].content && Number.isInteger(hit.rank)));
const other = await post('/api/known', { collection, profile: otherProfile, rows });
check('different model profile is isolated', other.data.known.every(value => value === false));
const shortened = await post('/api/reconcile', { ...select, doc_id: 'file.txt', chunk_count: 1 });
check('shorter file removes stale tail only', shortened.data.removed === 1);
await post('/api/refresh', select);
const after = await post('/api/known', { ...select, rows });
check('first chunk retained and old tail removed', after.data.known[0] === true && after.data.known[1] === false);
const invalid = await post('/api/search', { ...query, query_vector: [1, 2] }, false);
check('wrong vector dimension rejected', !invalid.response.ok);
const noProfile = await post('/api/search', { collection, query: 'refund', query_vector: vec(0) }, false);
check('missing model profile rejected', !noProfile.response.ok);
for (let i = 0; i < 3; i++) await post('/api/search', query);
const timings = [];
for (let i = 0; i < 20; i++) {
  const start = performance.now();
  await post('/api/search', query);
  timings.push(performance.now() - start);
}
timings.sort((a, b) => a - b);
const result = { recorded_at: new Date().toISOString(), base, seekdb: 'seekdb-js 1.4.0 / local server', dataset: 'two synthetic 384-dimensional chunks', checks, latency_ms: { samples: timings.length, p50: timings[10], p95: timings[19] } };
await writeFile(new URL('./integration-result.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log(`Summary: ${checks.filter(item => item.passed).length}/${checks.length} checks; HTTP p50 ${timings[10].toFixed(1)}ms, p95 ${timings[19].toFixed(1)}ms`);
if (checks.some(item => !item.passed)) process.exitCode = 1;
