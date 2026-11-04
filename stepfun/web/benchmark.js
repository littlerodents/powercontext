/* Reproducible browser benchmark; all content below is synthetic. */
'use strict';
const docs = [
  ['refund', 'Customers may return an unused product within thirty days for a full refund.'],
  ['shipping', 'Standard delivery takes three to five working days after dispatch.'],
  ['password', 'To reset a forgotten password, open the security page and request a recovery email.'],
  ['invoice', 'A tax invoice can be downloaded from the billing section after payment.'],
  ['battery', 'Charge the battery fully before the first use and keep it away from extreme heat.'],
  ['meeting', 'The weekly project meeting is held on Tuesday at ten in the morning.'],
  ['backup', 'Create an encrypted backup every night and test the restore procedure monthly.'],
  ['warranty', 'The product warranty covers manufacturing defects for twelve months.'],
  ['coffee', 'Pour hot water slowly over freshly ground coffee for a balanced cup.'],
  ['garden', 'Water the vegetable garden early in the morning during dry weather.'],
  ['train', 'The last train to the airport leaves the central station at eleven thirty.'],
  ['membership', 'Premium members receive early access to new features and priority support.'],
];
const queries = [
  ['How long can I return an unused item and get my money back?', 'refund'],
  ['When should my parcel arrive?', 'shipping'],
  ['I forgot my login secret, how do I recover access?', 'password'],
  ['Where can I obtain a receipt for taxes?', 'invoice'],
  ['How long is a factory defect covered?', 'warranty'],
  ['How do I keep a restorable copy of my data?', 'backup'],
  ['What time does the airport rail service stop?', 'train'],
  ['What benefits come with a premium account?', 'membership'],
];
const status = document.getElementById('status');
const output = document.getElementById('output');
const clock = () => performance.now();
const round = value => Math.round(value * 100) / 100;
async function runModel(dtype, collection) {
  status.textContent = `正在加载 ${dtype} 模型…`;
  const model = new LocalEmbedder();
  const loadStart = clock();
  await model.init('minilm', dtype, 'auto');
  const loadMs = clock() - loadStart;
  const texts = docs.map(item => item[1]);
  // Warm up both batch shapes before measuring; each mode uses the same texts.
  await model.embedBatch(texts.slice(0, 1));
  await model.embedBatch(texts.slice(0, 8));
  status.textContent = `${dtype}：测试逐条与批量向量化…`;
  const singleStart = clock();
  const singles = [];
  for (const text of texts) singles.push((await model.embedBatch([text]))[0]);
  const singleMs = clock() - singleStart;
  const batchStart = clock();
  let vectors = [];
  for (let i = 0; i < texts.length; i += 8) vectors = vectors.concat(await model.embedBatch(texts.slice(i, i + 8)));
  const batchMs = clock() - batchStart;
  const batchAgreement = vectors.map((vector, i) => vector.reduce((sum, x, j) => sum + x * singles[i][j], 0));
  const rows = docs.map(([id, content], index) => ({
    doc_id: id, chunk_no: 0, title: id, content, content_sha256: id,
    metadata: { source: 'synthetic-benchmark' }, embedding: vectors[index],
  }));
  await SeekdbClient.upsert({ collection, profile: model.profile, dim: model.spec.dim, rows });
  const refreshStart = clock();
  await SeekdbClient.refresh(collection, model.profile);
  const refreshMs = clock() - refreshStart;
  const quality = [];
  const dbMs = [];
  for (const [query, expected] of queries) {
    status.textContent = `${dtype}：检索质量 ${quality.length + 1}/${queries.length}…`;
    const [queryVector] = await model.embedBatch([query]);
    const response = await SeekdbClient.search({ collection, profile: model.profile, query, query_vector: queryVector, top_k: 3 });
    const ids = response.results.map(hit => hit.doc_id);
    // Independent vector sanity check for the benchmark only; app results always come from seekdb.
    const vectorReference = vectors.map((vector, i) => ({
      id: docs[i][0], similarity: vector.reduce((sum, x, j) => sum + x * queryVector[j], 0),
    })).sort((a, b) => b.similarity - a.similarity).slice(0, 3).map(item => item.id);
    quality.push({ query, expected, top3: ids, hit: ids.includes(expected), vector_reference_top3: vectorReference });
    dbMs.push(response.elapsed_ms);
  }
  dbMs.sort((a, b) => a - b);
  return { model, report: {
    dtype, backend: model.device, model_files: Object.fromEntries(model.modelFiles), model_bytes_from_progress: model.modelBytes,
    load_ms: round(loadMs), batch1_ms: round(singleMs), batch8_ms: round(batchMs),
    batch_vector_cosine_min: Math.min(...batchAgreement),
    refresh_ms: round(refreshMs), seekdb_ms_p50: dbMs[Math.floor(dbMs.length * .5)],
    quality, recall_at_3: quality.filter(item => item.hit).length / quality.length,
  } };
}
document.getElementById('run').onclick = async () => {
  document.getElementById('run').disabled = true;
  const result = { started_at: new Date().toISOString(), user_agent: navigator.userAgent, corpus_size: docs.length, query_count: queries.length, collection: `sf_bench_${Date.now()}`, reports: [] };
  try {
    const q8 = await runModel('q8', result.collection);
    result.reports.push(q8.report); output.textContent = JSON.stringify(result, null, 2);
    const fp32 = await runModel('fp32', result.collection);
    result.reports.push(fp32.report); output.textContent = JSON.stringify(result, null, 2);
    const fp16 = await runModel('fp16', result.collection);
    result.reports.push(fp16.report); output.textContent = JSON.stringify(result, null, 2);
    status.textContent = '模拟无 WebGPU：验证自动降级及同库检索…';
    const original = Object.getOwnPropertyDescriptor(navigator, 'gpu');
    Object.defineProperty(navigator, 'gpu', { value: undefined, configurable: true });
    try {
      result.auto_fallback = {};
      for (const [dtype, reference] of [['q8', q8], ['fp16', fp16]]) {
        status.textContent = `模拟无 WebGPU：验证 ${dtype} 自动降级…`;
        try {
          const fallback = new LocalEmbedder();
          await fallback.init('minilm', dtype, 'auto');
          const [queryVector] = await fallback.embedBatch([queries[0][0]]);
          const [gpuVector] = await reference.model.embedBatch([queries[0][0]]);
          const response = await SeekdbClient.search({ collection: result.collection, profile: fallback.profile, query: queries[0][0], query_vector: queryVector, top_k: 3 });
          result.auto_fallback[dtype] = { backend: fallback.device, expected: queries[0][1], top3: response.results.map(hit => hit.doc_id), webgpu_wasm_cosine: queryVector.reduce((sum, x, i) => sum + x * gpuVector[i], 0) };
        } catch (error) {
          result.auto_fallback[dtype] = { error: error.message };
        }
      }
    } finally {
      if (original) Object.defineProperty(navigator, 'gpu', original); else delete navigator.gpu;
    }
    result.completed_at = new Date().toISOString();
    output.textContent = JSON.stringify(result, null, 2);
    const download = document.getElementById('download');
    download.href = URL.createObjectURL(new Blob([output.textContent + '\n'], { type: 'application/json' }));
    download.hidden = false;
    status.textContent = '实测完成';
  } catch (error) {
    result.error = String(error && error.stack || error);
    output.textContent = JSON.stringify(result, null, 2);
    status.textContent = `实测失败：${error.message}`;
  } finally {
    document.getElementById('run').disabled = false;
  }
};
