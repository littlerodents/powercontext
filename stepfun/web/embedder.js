/**
 * 浏览器本地 embedding：transformers.js v3 + WebGPU/WASM 降级 + INT8/FP16 量化选择。
 * 本文件不产生任何云端推理调用；ONNX 权重仅作为静态文件从 CDN 下载。
 */
"use strict";

// 固定 transformers.js 版本；模型文件也固定到具体 revision。
const TRANSFORMERS_CDN = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/dist/transformers.min.js";

const MODEL_SPECS = {
  minilm: {
    repo: "Xenova/all-MiniLM-L6-v2",
    revision: "751bff37182d3f1213fa05d7196b954e230abad9",
    dim: 384,
    pooling: "mean",
    normalize: true,
  },
};

async function webgpuAvailable() {
  if (typeof navigator === "undefined" || !navigator.gpu) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return adapter !== null;
  } catch (_err) {
    return false;
  }
}

class LocalEmbedder {
  constructor() {
    this.extractor = null;
    this.spec = null;
    this.dtype = "q8";
    this.device = "wasm";
    this.transformersVersion = "3.8.1";
    this.modelFiles = new Map();
  }

  /** @param {"minilm"} modelKey @param {"q8"|"fp16"|"fp32"} dtype @param {"auto"|"wasm"} devicePref */
  async init(modelKey, dtype, devicePref) {
    const spec = MODEL_SPECS[modelKey];
    if (!spec) throw new Error(`未知模型 ${modelKey}`);
    const tf = await import(/* webpackIgnore: true */ TRANSFORMERS_CDN);
    this.transformersVersion = tf.env?.version || "3.8.1";
    if (tf.env && tf.env.backends && tf.env.backends.onnx) {
      tf.env.backends.onnx.wasm = tf.env.backends.onnx.wasm || {};
      // wasm 路径由 transformers.js 自动管理；此处显式保留配置入口便于排查。
    }

    const wantWebgpu = devicePref === "auto" && (await webgpuAvailable());
    const tryDevice = async (device) => {
      const opts = { device, dtype, revision: spec.revision, progress_callback: progress => {
        if (progress.file?.endsWith(".onnx") && Number.isFinite(progress.total)) this.modelFiles.set(progress.file, progress.total);
      } };
      if (device === "webgpu" && dtype === "fp32") {
        // WebGPU 不支持 fp32 权重时的兜底：由 transformers.js 内部处理，失败会抛错进入 WASM 分支。
      }
      return tf.pipeline("feature-extraction", spec.repo, opts);
    };

    if (wantWebgpu) {
      try {
        this.extractor = await tryDevice("webgpu");
        this.device = "webgpu";
      } catch (err) {
        console.warn("WebGPU 初始化失败，降级 WASM：", err);
        this.extractor = await tryDevice("wasm");
        this.device = "wasm";
      }
    } else {
      this.extractor = await tryDevice("wasm");
      this.device = "wasm";
    }
    this.spec = spec;
    this.dtype = dtype;
    this.profile = `${spec.repo}@${spec.revision}:${dtype}:${spec.pooling}:${spec.dim}`;
    this.modelBytes = [...this.modelFiles.values()].reduce((sum, bytes) => sum + bytes, 0);
    return { device: this.device, dtype, repo: spec.repo };
  }

  /** 批量向量化；返回 number[][]（已按模型规则池化与归一化）。 */
  async embedBatch(texts) {
    if (!this.extractor) throw new Error("模型未加载");
    if (!Array.isArray(texts) || texts.length === 0) return [];
    const out = await this.extractor(texts, {
      pooling: this.spec.pooling,
      normalize: this.spec.normalize,
    });
    const vectors = [];
    for (let i = 0; i < out.dims[0]; i++) {
      vectors.push(Array.from(out.data.slice(i * this.spec.dim, (i + 1) * this.spec.dim)));
    }
    return vectors;
  }
}

window.LocalEmbedder = LocalEmbedder;
window.MODEL_SPECS = MODEL_SPECS;
