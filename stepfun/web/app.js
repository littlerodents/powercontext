/**
 * 主流程：模型加载 → 增量导入（chunk + 批量化 + upsert + refresh）→ 混合检索 → 状态管理。
 * 检索全部由 seekdb 完成（桥接 API 后端单条 SQL 融合），前端只渲染结果。
 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  embedder: null,
  collection: localStorage.getItem("kb.collection") || "my_kb",
  modelLoaded: false,
  searchSequence: 0,
};

function log(msg) {
  const ts = new Date().toISOString().slice(11, 19);
  $("log").textContent = `[${ts}] ${msg}\n` + $("log").textContent;
}

function refreshBadges() {
  $("collection-badge").textContent = `集合：${state.collection}`;
  if (state.embedder && state.modelLoaded) {
    const backendLabel = state.embedder.device === "webgpu" ? "后端：WebGPU" : "后端：WASM（自动降级或手动指定）";
    const b = $("backend-badge");
    b.textContent = backendLabel;
    b.className = "badge " + (state.embedder.device === "webgpu" ? "ok" : "warn");
    $("model-badge").textContent = `模型：${state.embedder.spec.repo} · ${state.embedder.dtype}`;
    $("import-btn").disabled = false;
    $("search-btn").disabled = false;
  }
  localStorage.setItem("kb.collection", state.collection);
}

/** 简单增量切块：chunk_size 字符 + overlap 重叠；按内容哈希去重（重复文本不再向量化）。 */
function chunkText(docId, text) {
  const size = Number($("chunk-size").value);
  const overlap = Number($("chunk-overlap").value);
  if (!Number.isInteger(size) || size < 100 || size > 2000 ||
      !Number.isInteger(overlap) || overlap < 0 || overlap >= size || overlap > 200) {
    throw new Error("chunk 大小需为 100–2000，重叠需为 0–200 且小于 chunk 大小");
  }
  const chunks = [];
  let i = 0;
  let no = 0;
  while (i < text.length) {
    const content = text.slice(i, i + size);
    chunks.push({ doc_id: docId, chunk_no: no, title: docId, content });
    i += size - overlap;
    no += 1;
  }
  return chunks;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function importDocs(docs) {
  const embedder = state.embedder;
  const collection = state.collection;
  const profile = embedder.profile;
  let inserted = 0;
  let skipped = 0;
  for (const doc of docs) {
    const chunks = chunkText(doc.doc_id, doc.text);
    if (!chunks.length) {
      log(`跳过空文档 ${doc.doc_id}`);
      continue;
    }
    const prepared = await Promise.all(chunks.map(async chunk => ({
      ...chunk, content_sha256: await sha256Hex(chunk.content),
    })));
    const { known } = await SeekdbClient.known({ collection, profile, rows: prepared });
    const missing = prepared.filter((_row, index) => !known[index]);
    skipped += prepared.length - missing.length;
    for (let start = 0; start < missing.length; start += 8) {
      const batch = missing.slice(start, start + 8);
      const vectors = await embedder.embedBatch(batch.map(chunk => chunk.content));
      await SeekdbClient.upsert({
        collection, profile, dim: embedder.spec.dim,
        rows: batch.map((chunk, i) => ({
          ...chunk,
          metadata: { source: "browser-import", imported_at: new Date().toISOString() },
          embedding: vectors[i],
        })),
      });
      inserted += batch.length;
      $("progress-bar").style.width = `${Math.round((start + batch.length) / missing.length * 100)}%`;
    }
    const { removed } = await SeekdbClient.reconcile({
      collection, profile, doc_id: doc.doc_id, chunk_count: chunks.length,
    });
    log(`${doc.doc_id}：写入 ${missing.length}、跳过 ${chunks.length - missing.length}、移除旧尾块 ${removed}`);
  }
  log("导入完成，触发 seekdb 索引刷新…");
  await SeekdbClient.refresh(collection, profile);
  log("索引刷新完成，新内容现在可被检索");
  $("progress-bar").style.width = "100%";
  $("import-hint").textContent = `集合 ${collection}：本次写入 ${inserted} 个 chunk，跳过 ${skipped} 个未变化的 chunk`;
}

async function runSearch() {
  const query = $("query-input").value.trim();
  if (!query) return;
  const embedder = state.embedder;
  const collection = state.collection;
  const sequence = ++state.searchSequence;
  const [vec] = await embedder.embedBatch([query]);
  const resp = await SeekdbClient.search({
    collection,
    profile: embedder.profile,
    query,
    query_vector: vec,
    top_k: Number($("topk-input").value),
  });
  if (sequence !== state.searchSequence || collection !== state.collection || embedder !== state.embedder) {
    log("集合、模型或查询已改变，忽略过期的检索结果");
    return;
  }
  const list = $("results");
  list.replaceChildren();
  for (const hit of resp.results) {
    const li = document.createElement("li");
    const title = document.createElement("strong");
    title.textContent = hit.title || hit.doc_id;
    const score = document.createElement("span");
    score.className = "score";
    score.textContent = `排名 ${hit.rank} · ${hit.matched_by || "seekdb"}`;
    const content = document.createElement("p");
    content.textContent = hit.content;
    li.append(title, score, content);
    list.appendChild(li);
  }
  if (!resp.results.length) log("无命中结果（可能索引尚未刷新或库为空）");
  else log(`seekdb 混合检索返回 ${resp.results.length} 条结果，耗时 ${resp.elapsed_ms ?? "?"}ms`);
}

$("load-model-btn").onclick = async () => {
  try {
    const embedder = new LocalEmbedder();
    const info = await embedder.init(
      $("model-select").value,
      $("dtype-select").value,
      $("device-select").value,
    );
    state.embedder = embedder;
    state.modelLoaded = true;
    log(`模型加载完成：${info.repo} dtype=${info.dtype} device=${info.device}`);
    log("不同模型及量化配置的数据分别存放；切换模型后请重新导入对应语料");
    refreshBadges();
  } catch (err) {
    log(`模型加载失败：${err.message}`);
  }
};

$("switch-collection-btn").onclick = () => {
  const name = $("collection-input").value.trim() || "my_kb";
  state.collection = name;
  $("results").replaceChildren();
  refreshBadges();
  log(`已切换到集合 ${name}（增量导入将写入该集合，检索仅查该集合）`);
};

$("import-btn").onclick = async () => {
  try {
    const files = Array.from($("import-files").files || []);
    const docs = [];
    const pasted = $("import-text").value.trim();
    if (pasted) docs.push({ doc_id: `pasted-${await sha256Hex(pasted)}`, text: pasted });
    for (const f of files) {
      docs.push({ doc_id: f.name, text: await f.text() });
    }
    if (!docs.length) {
      log("没有可导入内容");
      return;
    }
    $("import-btn").disabled = true;
    $("switch-collection-btn").disabled = true;
    $("load-model-btn").disabled = true;
    await importDocs(docs);
  } catch (err) {
    log(`导入失败：${err.message}`);
  } finally {
    $("import-btn").disabled = false;
    $("switch-collection-btn").disabled = false;
    $("load-model-btn").disabled = false;
  }
};

$("search-btn").onclick = () => runSearch().catch((err) => log(`检索失败：${err.message}`));

(async function boot() {
  $("collection-input").value = state.collection;
  try {
    const health = await SeekdbClient.health();
    $("db-badge").textContent = `seekdb：${health.status}`;
    $("db-badge").className = "badge " + (health.status === "ok" ? "ok" : "warn");
  } catch (_err) {
    $("db-badge").textContent = "seekdb：桥接不可达（请先启动 server）";
    $("db-badge").className = "badge warn";
  }
  refreshBadges();
})();
