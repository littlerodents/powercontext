# 技术说明 · StepFun 端侧迷你知识库

**模型与运行时。** [Xenova/all-MiniLM-L6-v2](https://huggingface.co/Xenova/all-MiniLM-L6-v2) 的 ONNX 权重，Apache-2.0，revision `751bff37182d3f1213fa05d7196b954e230abad9`；384 维，mean pooling、L2 归一化。浏览器运行 Transformers.js 3.8.1 / ONNX Runtime Web。应用默认 FP16，FP32 为对照；WebGPU 优先，缺失或初始化失败自动回退 WASM，界面显示实际后端。模型文件可远程下载，但文档和查询的 embedding 均在浏览器本机计算。

**量化及质量。** 公开预转换 FP16 权重 45,297,825 字节，FP32 为 90,387,606 字节，体积降低 49.9%（1.995×）。相同 12 文档、8 个查询和人工相关标注的实测中，seekdb 混合检索 Recall@3 均为 8/8；这只是小样本，不代表所有数据集。FP16 模拟无 WebGPU 时成功使用 WASM，跨 WebGPU/WASM 查询向量余弦相似度 0.999989，预期文档仍在 Top 3。q8 在本机 WebGPU 上批量与单条向量不一致，Recall@3 仅 1/8，故主应用不提供 q8。各项原始记录及复现方法见 `PERF_LOG.md`、`benchmark-result.json`。

**seekdb 数据层。** 本地 Node 桥接通过 `seekdb-js` 1.4.0 创建集合，保存 384 维向量、原文、文档 ID、块号和内容哈希；全文索引使用 ngram，向量索引使用 HNSW。`collection.hybridSearch` 在 seekdb 中进行全文/向量候选和 RRF 排序，客户端不合并两路结果。每次导入完成后调用 `refresh_index`，再允许验证新数据。每个逻辑集合按模型 revision、精度及池化配置派生独立物理集合，避免不同向量空间混合。结果只展示数据库排序，不将 SDK 的混合分数误称为向量距离。

**增量与性能。** 文本按可配置大小分块；同一文档块的内容哈希先与 seekdb 核对，未变化时跳过 embedding。变化块按最多 8 个一批向量化并 upsert，同名文件缩短时删除该文件多余旧块，最后统一刷新索引。浏览器 localStorage 保存当前逻辑集合；切换集合或模型时丢弃过期查询结果。热缓存、12 个合成文本的单次测量中 FP16 batch=1 为 56.0ms，batch=8 为 10.1ms；此数字仅反映该设备和短样本。`server/integration-test.mjs` 在真实 seekdb 上验证增量、隔离、刷新和混合检索。
