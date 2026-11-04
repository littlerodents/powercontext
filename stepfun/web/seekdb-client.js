/** 本地桥接 REST 客户端。仅调用本机 server，不触碰任何云端推理服务。 */
"use strict";

const SeekdbClient = {
  base: "", // 同源（server.js 同时托管静态页与 API）
  async _post(path, payload) {
    const resp = await fetch(this.base + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(`${path} HTTP ${resp.status}: ${data.error || JSON.stringify(data)}`);
    return data;
  },
  async health() {
    const resp = await fetch(this.base + "/api/health");
    return resp.json();
  },
  async upsert(payload) {
    return this._post("/api/upsert", payload);
  },
  async known(payload) {
    return this._post("/api/known", payload);
  },
  async reconcile(payload) {
    return this._post("/api/reconcile", payload);
  },
  async refresh(collection, profile) {
    return this._post("/api/refresh", { collection, profile });
  },
  async search(payload) {
    return this._post("/api/search", payload);
  },
};

window.SeekdbClient = SeekdbClient;
