# 时光胶囊伙伴 · PowerContext 记忆版（Dify × PowerContext）

一个"记得你"的跨会话记忆伙伴📦：每轮对话先从 PowerContext 召回你留下的记忆，个性化回应后再把本轮内容写回。开新会话依然记得；**不同 user_id 互相隔离**；支持"给未来：……"的信件玩法。

## 交付物（同源两包）

| 文件 | 说明 |
|---|---|
| `powercontext-memory-chatflow.yml` | Chatflow DSL（14 节点；从 Dify 1.19.0 导入成功，也是 ifpkg 的源） |
| `capsule-partner.ifpkg` | Dify 官方导出的一键导入包（manifest.yaml + app.yaml，与 yml 同源导出） |
| `evidence/` | **机器生成的运行证据**（非自述）：SSE 原始流、节点级 run 摘要、跨会话与隔离验证、PowerContext 侧数据 |
| `README.md` / `validation-cases.md` | 本文件（含 5 分钟复跑手册）与验证样例 |

## 编排结构（Dify 承担核心流程编排；变量派生全部在 Code 节点内完成）

```
开始(user_id 输入, 默认 demo-user)
 → Code  派生隔离 scope + json.dumps 构造 ensure/remember 请求体   ← 杜绝 sys.query 原文注入 JSON
 → HTTP  POST /v1/scopes   幂等确保 scope（default-value 降级）
 → Code  解析服务端 scope_id，生成 recall/remember 请求体
 → HTTP  POST /v1/memory/search   召回（fts 自动，无 embedding 依赖；default-value 失败降级）
 → Code  机械提取命中条数（解析失败按 0 条）
 → IF    hit_count > 0                     ← 条件判断不经 LLM
     ├─ 否 → LLM 初次见面 → Code 清洗 → Answer
     └─ 是 → LLM 带记忆回应 → Code 清洗 → Answer
 → HTTP  POST /v1/memory/remember  事后写回（失败不影响回复；default-value 降级）
```

## 运行证据（evidence/，全部带 conversation_id / run_id 可追溯）

| 文件 | 内容 |
|---|---|
| `01-sse-sessionA-first-meeting.sse.txt` | 会话 A 原始 SSE 事件流（11 节点全部 succeeded） |
| `02-sse-sessionB-cross-session-memory.sse.txt` | 会话 B 原始 SSE（新 conversation，召回命中） |
| `03-run-summary-sessionA.json` | A 的节点级摘要：hits=0 → 初次分支，answer 落袋 |
| `04-run-summary-sessionB.json` | B 的节点级摘要：hits=1 → "当然记得！你说过最喜欢的水果是榴莲……" |
| `05-powercontext-rows.txt` | PowerContext 侧直接 curl 的 scopes 与 memory/search 原始响应 |
| `06-isolation-other-user.json` | 隔离验证：other-user hits=0 → "咱们才第一次见呀"（用户隔离实证） |

## 5 分钟复跑手册（评审路径）

前置：Docker；Python 3.12 + [uv](https://docs.astral.sh/uv/)。

1. **起 PowerContext**（本仓库即 oceanbase/powercontext 的 ecosystem 分支）：
   ```bash
   git clone -b 2026-competition-ecosystem https://github.com/littlerodents/powercontext.git pc
   cd pc && uv venv .venv && uv pip install -e ".[server,cli,builtin]"
   export POWERCONTEXT_SERVER_AUTH_TOKEN=$(openssl rand -hex 24)
   .venv/bin/powercontext server run --host 0.0.0.0 --port 8000 --no-env-file &
   ```
2. **起 Dify**（自托管 1.19.0；Docker Desktop 下 `host.docker.internal` 直通宿主机）：
   ```bash
   git clone --depth 1 --filter=blob:none --sparse https://github.com/langgenius/dify.git
   cd dify && git sparse-checkout set docker && cd docker
   cp .env.example .env
   printf '\nEXPOSE_NGINX_PORT=8181\nSSRF_PROXY_ALLOW_PRIVATE_IPS=192.168.65.0/24,127.0.0.1,localhost,host.docker.internal\n' >> .env
   docker compose up -d
   ```
   注意：`SSRF_PROXY_ALLOW_PRIVATE_IPS` 必须加，否则 HTTP 节点访问宿主机 PowerContext 被 SSRF 防护拦截。
3. **初始化**：浏览器打开 `http://127.0.0.1:8181/install` 建管理员 → 登录 → Marketplace 安装 DeepSeek 插件 → 填 API Key。
4. **导入**：工作室 → 创建 → 导入 DSL → 选 `powercontext-memory-chatflow.yml`（或直接导入 `capsule-partner.ifpkg`）。
5. **配环境变量**（编排 → 环境变量）：
   - `powercontext_url` = `http://host.docker.internal:8000`
   - `powercontext_scope` = `dify-capsule`（前缀；实际 scope = `dify-capsule-<user_id>`，按用户隔离）
   - `powercontext_token` = 第 1 步的 TOKEN（secret）
6. **体验**：调试/发布后对话。先说"记住：我最喜欢的水果是榴莲"，再**开新会话**问"我之前跟你说过什么？"——它会记得。换一个 `user_id` 输入再问——看不到前者的记忆。

## 已知边界（如实说明）

- DeepSeek 思考链偶尔泄漏原始文本：已加 Code 清洗节点机械剥离 `<think>` 段。
- scope 幂等键冲突（同键不同 title）时 ensure 返回 409：走 default-value 降级路径，后续以派生名调用（评测新环境无此污染，正常路径为 201/幂等 200）。
