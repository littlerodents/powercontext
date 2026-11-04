/** seekdb-js 1.4.0 adapter. Embeddings arrive from the browser; no server model is loaded. */
import { createHash } from "node:crypto";
import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from "seekdb";

export class SeekdbStore {
  constructor({ url, dimension }) {
    this.url = url;
    this.dimension = dimension;
    this.client = null;
    this.collections = new Map();
    this.connected = false;
    this.serverVersion = "seekdb server through seekdb-js 1.4.0";
  }

  async connect() {
    const endpoint = new URL(this.url);
    this.client = new SeekdbClient({
      host: endpoint.hostname,
      port: Number(endpoint.port || 2881),
      user: decodeURIComponent(endpoint.username || "root"),
      password: decodeURIComponent(endpoint.password || ""),
      database: endpoint.pathname.slice(1) || "test",
    });
    await this.client.countCollection();
    this.connected = this.client.isConnected();
  }

  async ensureCollection(name, dim = this.dimension) {
    if (this.collections.has(name)) return this.collections.get(name);
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,62}$/.test(name)) throw new Error("集合名称应为字母开头的字母、数字或下划线，最多63字符");
    if (Number(dim) !== this.dimension) throw new Error("集合向量维度与模型不一致");
    const collection = await this.client.getOrCreateCollection({
      name,
      schema: new Schema({
        fulltextIndex: new FulltextIndexConfig("ngram"),
        vectorIndex: new VectorIndexConfig({
          hnsw: { dimension: this.dimension, distance: "l2" },
          embeddingFunction: null,
        }),
      }),
      embeddingFunction: null,
    });
    if (collection.dimension !== this.dimension) throw new Error("已有集合的向量维度不匹配");
    this.collections.set(name, collection);
    return collection;
  }

  async upsert(name, rows) {
    if (!rows.length) return 0;
    const collection = await this.ensureCollection(name);
    for (const row of rows) {
      if (!Array.isArray(row.embedding) || row.embedding.length !== this.dimension || row.embedding.some(value => !Number.isFinite(value))) {
        throw new Error("向量必须具有模型声明的维度且全部为有限数值");
      }
    }
    await collection.upsert({
      ids: rows.map(row => createHash("sha256").update(JSON.stringify([row.doc_id, row.chunk_no])).digest("hex")),
      documents: rows.map(row => String(row.content)),
      embeddings: rows.map(row => row.embedding),
      metadatas: rows.map(row => ({
        ...row.metadata,
        doc_id: String(row.doc_id),
        chunk_no: Number(row.chunk_no),
        title: String(row.title || row.doc_id),
        content_sha256: String(row.content_sha256 || ""),
      })),
    });
    return rows.length;
  }

  async known(name, rows) {
    if (!rows.length) return [];
    const collection = await this.ensureCollection(name);
    const ids = rows.map(row => createHash("sha256").update(JSON.stringify([row.doc_id, row.chunk_no])).digest("hex"));
    const stored = await collection.get({ ids, include: ["metadatas"] });
    const hashes = new Map(stored.ids.map((id, i) => [id, stored.metadatas?.[i]?.content_sha256]));
    return rows.map((row, i) => hashes.get(ids[i]) === row.content_sha256);
  }

  // 仅在一份文件的全部新 chunk 已写入后调用，移除旧版本多出的尾部 chunk。
  async reconcile(name, docId, chunkCount) {
    if (typeof docId !== "string" || !docId || !Number.isInteger(chunkCount) || chunkCount < 1) {
      throw new Error("文档 ID 或 chunk 数无效");
    }
    const collection = await this.ensureCollection(name);
    const stored = await collection.get({ where: { doc_id: docId }, include: ["metadatas"] });
    const stale = stored.ids.filter((id, i) => Number(stored.metadatas?.[i]?.chunk_no) >= chunkCount);
    if (stale.length) await collection.delete({ ids: stale });
    return stale.length;
  }

  async refresh(name) {
    const collection = await this.ensureCollection(name);
    await collection.refresh_index();
    return { refreshed: true, visible_rows: await collection.count() };
  }

  async hybridSearch({ collection: name, query, queryVector, topK }) {
    if (!Array.isArray(queryVector) || queryVector.length !== this.dimension || queryVector.some(value => !Number.isFinite(value))) {
      throw new Error("查询向量必须具有模型声明的维度且全部为有限数值");
    }
    const collection = await this.ensureCollection(name);
    const count = Math.max(1, Math.min(50, Number(topK) || 10));
    const result = await collection.hybridSearch({
      query: { whereDocument: { $contains: String(query) } },
      knn: { queryEmbeddings: [queryVector], nResults: count },
      rank: { rrf: { rankWindowSize: Math.max(50, count), rankConstant: 60 } },
      nResults: count,
      include: ["documents", "metadatas"],
    });
    return (result.ids[0] || []).map((id, index) => {
      const metadata = result.metadatas?.[0]?.[index] || {};
      return {
        id,
        doc_id: metadata.doc_id,
        chunk_no: metadata.chunk_no,
        title: metadata.title,
        content: result.documents?.[0]?.[index] || "",
        metadata,
        rank: index + 1,
        matched_by: "seekdb hybrid search",
      };
    });
  }

  async close() {
    await this.client?.close();
    this.connected = false;
  }
}
