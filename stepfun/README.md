# StepFun：浏览器端迷你知识库

在浏览器本地对文档和查询进行向量化，经 `seekdb-js` 将向量、原文及元数据写入 seekdb，并由 seekdb 完成全文与向量混合检索。应用不调用云端推理 API。首次需要下载开源 ONNX 模型与 Transformers.js，下载后推理发生在浏览器所在设备。

## 环境与启动

实测：macOS 26.2 / Apple M3 Pro、Chrome 156、Node.js 22.23.2、本地 seekdb 1.4.0.0。需要现代 Chrome 或 Edge、Node.js 20 及以上、可访问的 seekdb server。默认示例模型以英文为主。

```bash
# 如果尚无 seekdb，可在本机创建一个独立的 seekdb 容器。
docker run -d --name stepfun-seekdb -p 2881:2881 oceanbase/seekdb:latest
# 等待 seekdb 启动后创建本应用的数据库；如果已有实例，改为你的连接信息。
mysql -h127.0.0.1 -P2881 -uroot -e 'CREATE DATABASE IF NOT EXISTS seekdb_kb;'

cd stepfun/server
npm ci
export SEEKDB_URL='mysql://root@127.0.0.1:2881/seekdb_kb'
export PORT=8787
npm start
```

打开 <http://127.0.0.1:8787/>，保持默认 FP16 和自动后端，点击“加载模型”。将下列合成文本粘贴到导入框并点击“开始导入”，待日志显示刷新完成后输入查询并点击“检索”：

```text
Customers may return an unused product within thirty days for a full refund.
```

示例查询：`How long can I return an unused item?`。结果应显示原文及排序。再次导入同样内容，页面应显示写入 0、跳过 1。可切换集合验证数据隔离；文件以文件名作为文档身份，同名文件内容更新时只重算变化的块并移除多出的旧尾块。粘贴文本以内容哈希作为身份。不同模型 revision/精度使用不同物理集合；切换配置后需要为该配置重新导入语料。

## 模型与运行方式

- [Xenova/all-MiniLM-L6-v2](https://huggingface.co/Xenova/all-MiniLM-L6-v2)，Apache-2.0，固定 revision `751bff37182d3f1213fa05d7196b954e230abad9`；384 维，mean pooling + L2 归一化。
- Transformers.js **3.8.1** / ONNX Runtime Web。支持 WebGPU 时优先使用，初始化失败或不可用时自动使用 WASM；页面徽章显示实际后端，也可手动选择 WASM。
- 默认使用公开的 FP16 ONNX 权重；FP32 供基线比较。当前设备实测 q8 WebGPU 批量结果不稳定，故不在主应用提供 q8 选项；原始实验仍保存在性能记录中。
- 模型脚本和权重从 CDN/模型仓库获取；文档和查询只发送到本机桥接服务，桥接服务不运行模型，检索和存储均由 seekdb 承担。

## 验证与性能复现

桥接服务运行后：

```bash
# 在 stepfun/server 中运行；只创建新的测试专用集合，不删除已有数据。
node integration-test.mjs
# 若服务使用其他端口：STEPFUN_BASE_URL=http://127.0.0.1:18108 node integration-test.mjs
```

打开 <http://127.0.0.1:8787/benchmark.html>，点击“运行实测”。该页面用 12 篇合成英文文档和 8 条人工指定相关文档的查询，对 FP32、FP16、q8 进行实际浏览器推理和 seekdb 混合检索；随后模拟无 WebGPU 环境验证自动降级。结果可下载为 JSON。首次运行会下载各模型，需等待；已有缓存时是热加载。实测结果与局限见 [PERF_LOG.md](PERF_LOG.md)，技术说明见 [TECH_NOTES.md](TECH_NOTES.md)，原始记录为 `benchmark-result.json` 和 `server/integration-result.json`。

目录：`web/` 为静态页面、端侧 embedding 与可复现实测；`server/` 为仅监听 127.0.0.1 的 Node 桥接、seekdb-js 数据层及集成检查。图片检索不是本作品的功能。
