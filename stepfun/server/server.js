/**
 * 本地桥接服务：静态托管 web/ + seekdb 数据层 REST API。
 * 设计约束（题面）：向量/原文/元数据只存 seekdb；混合检索在 seekdb 内完成；
 * 本服务不做任何模型推理，也不拼合两路检索结果。
 */
"use strict";

import http from "node:http";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { SeekdbStore } from "./seekdb-store.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB_DIR = path.join(__dirname, "..", "web");
const PORT = Number(process.env.PORT || 8787);
const DEFAULT_COLLECTION = process.env.SEEKDB_KB_COLLECTION || "my_kb";

const store = new SeekdbStore({
  url: process.env.SEEKDB_URL || "mysql://root@127.0.0.1:2881/seekdb_kb",
  dimension: Number(process.env.SEEKDB_KB_DIM || 384),
});

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
};

function send(res, code, body, type = "application/json") {
  const payload = typeof body === "string" ? body : JSON.stringify(body);
  res.writeHead(code, { "Content-Type": type, "Content-Length": Buffer.byteLength(payload) });
  res.end(payload);
}

// 同名知识库在不同模型、revision、量化配置下使用不同物理集合，避免向量空间混杂。
function physicalCollection(body) {
  const name = body.collection || DEFAULT_COLLECTION;
  if (typeof name !== "string" || !/^[a-zA-Z][a-zA-Z0-9_]{0,62}$/.test(name)) {
    throw new Error("集合名称应为字母开头的字母、数字或下划线，最多63字符");
  }
  if (typeof body.profile !== "string" || !body.profile || body.profile.length > 512) {
    throw new Error("请先加载模型并提供模型配置");
  }
  const suffix = createHash("sha256").update(JSON.stringify([name, body.profile])).digest("hex").slice(0, 12);
  return `${name.slice(0, 50)}_${suffix}`;
}

async function readJson(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function serveStatic(res, urlPath) {
  const rel = urlPath === "/" ? "index.html" : urlPath.replace(/^\/+/, "");
  const file = path.join(WEB_DIR, rel);
  if (!file.startsWith(WEB_DIR)) return send(res, 403, { error: "forbidden" });
  try {
    const data = await readFile(file);
    res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
    res.end(data);
  } catch {
    send(res, 404, { error: "not found" });
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  try {
    if (url.pathname.startsWith("/api/")) {
      if (url.pathname === "/api/health" && req.method === "GET") {
        return send(res, 200, { status: store.connected ? "ok" : "seekdb-not-connected", collection: DEFAULT_COLLECTION });
      }
      if (url.pathname === "/api/known" && req.method === "POST") {
        const body = await readJson(req);
        return send(res, 200, { known: await store.known(physicalCollection(body), body.rows || []) });
      }
      if (url.pathname === "/api/upsert" && req.method === "POST") {
        const body = await readJson(req);
        const collection = physicalCollection(body);
        await store.ensureCollection(collection, body.dim);
        const inserted = await store.upsert(collection, body.rows || []);
        return send(res, 200, { inserted });
      }
      if (url.pathname === "/api/reconcile" && req.method === "POST") {
        const body = await readJson(req);
        const removed = await store.reconcile(physicalCollection(body), body.doc_id, body.chunk_count);
        return send(res, 200, { removed });
      }
      if (url.pathname === "/api/refresh" && req.method === "POST") {
        const body = await readJson(req);
        return send(res, 200, await store.refresh(physicalCollection(body)));
      }
      if (url.pathname === "/api/search" && req.method === "POST") {
        const body = await readJson(req);
        const t0 = Date.now();
        const results = await store.hybridSearch({
          collection: physicalCollection(body),
          query: body.query || "",
          queryVector: body.query_vector || [],
          topK: Number(body.top_k || 10),
        });
        return send(res, 200, { results, elapsed_ms: Date.now() - t0 });
      }
      return send(res, 404, { error: "unknown api" });
    }
    return await serveStatic(res, url.pathname);
  } catch (err) {
    console.error("[bridge]", err);
    send(res, 500, { error: err.message });
  }
});

server.listen(PORT, "127.0.0.1", async () => {
  console.log(`[bridge] web + api: http://127.0.0.1:${PORT}`);
  try {
    await store.connect();
    console.log(`[bridge] seekdb connected, dimension=${store.dimension}`);
  } catch (err) {
    console.error(`[bridge] seekdb 连接失败（应用仍可打开，但无法导入/检索）：${err.message}`);
    console.error("[bridge] 检查 SEEKDB_URL 与 seekdb 服务状态。");
  }
});
