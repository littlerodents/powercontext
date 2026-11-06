# 时光胶囊伙伴 · PowerContext 记忆版（Dify × PowerContext）

一个"记得你"的跨会话伙伴：每轮对话先从 PowerContext 召回你留下的记忆，再个性化回应，最后把本轮内容写回 PowerContext。开新会话它依然记得；支持"给未来：……"的信件玩法。

## 交付物

| 文件 | 说明 |
|---|---|
| `powercontext-memory-chatflow.yml` | Chatflow DSL（Dify 1.x，已在本机 Dify 1.19.0 连续 6 次导入成功并全流程跑通） |
| `capsule-partner.ifpkg` | Dify 1.19 官方导出的一键导入包（zip：manifest.yaml + app.yaml），双保险 |
| `validation-cases.md` | 验证样例（含 2026-11-05 真机运行记录） |

## 编排结构（Dify 承担核心流程编排）

```
开始(sys.query)
 → HTTP 召回  POST {PC}/v1/memory/search        （检索/工具调用）
 → Code 提取   hits 条数 + 记忆文本（解析失败按 0 条，防呆）
 → IF 机械分支  hit_count > 0                    （条件判断，不经 LLM）
     ├─ 否 → LLM 初次见面（邀请留记忆 / 写给未来的信）
     └─ 是 → LLM 带记忆回应（"我还记得你说过……"）
 → Code 清洗   剥离 <think> 思考标签
 → Answer 输出
 → HTTP 写回  POST {PC}/v1/memory/remember       （回复后落库，失败不影响回复）
```

- **检索零 embedding 依赖**：`/v1/memory/search` 的 `mode:"auto"` 在无向量模型时自动走 `fts`（真机验证，命中 `matched_by:["fts"]`）。
- **跨会话身份**：记忆挂在固定的 PowerContext Scope（环境变量 `powercontext_scope`），与 Dify 会话 ID 解耦——这是"新会话还记得"的关键。
- **凭据不入 DSL**：导入后自行在「设置 → 模型供应商」填 API Key。

## 运行说明（评审复现路径）

1. **起 PowerContext**（需 Python 3.12 + uv）：
   ```bash
   export POWERCONTEXT_SERVER_AUTH_TOKEN=<32+ 位随机串>
   uv venv .venv && uv pip install -e "<powercontext 仓库路径>[server,cli,builtin]"
   .venv/bin/powercontext server run --host 0.0.0.0 --port 8000 --no-env-file
   ```
   容器内访问宿主机需 `--host 0.0.0.0` + Bearer Token。
2. **建 Scope**（一次性）：
   ```bash
   curl -X POST http://127.0.0.1:8000/v1/scopes -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"title":"Dify Capsule Partner","summary":"时光胶囊伙伴记忆","idempotency_key":"capsule-fixed"}'
   ```
   记下返回的 `scope_id`。
3. **导入 DSL**：Dify「工作室 → 创建 → 导入 DSL」选 `powercontext-memory-chatflow.yml`；或直接导入 `capsule-partner.ifpkg`。
4. **配模型**：「设置 → 模型供应商 → 深度求索」填 API Key（本验证用 DeepSeek `deepseek-flash`；任何对话模型均可）。
5. **改环境变量**（应用「编排 → 环境变量」）：
   - `powercontext_url` = `http://host.docker.internal:8000`（Dify 为 Docker 部署时；本机进程直跑用 `http://127.0.0.1:8000`）
   - `powercontext_scope` = 第 2 步的 `scope_id`
   - `powercontext_token` = 第 1 步的 Token（未启用鉴权可留空）
6. **Dify 自托管注意事项（真机踩坑记录）**：Docker 部署需在 `.env` 增加
   `SSRF_PROXY_ALLOW_PRIVATE_IPS=192.168.65.0/24,127.0.0.1,localhost,host.docker.internal`
   后 `docker compose up -d ssrf_proxy`，否则 HTTP 节点访问宿主机会被 SSRF 防护拦截。

## 玩法

- 直接聊天，说出偏好/约定（"记住：……"），新会话再问"我之前说过什么"。
- 写信：`给未来：……`；之后任何会话说"读读我写的信"。

真机验证结果见 `validation-cases.md`（2026-11-05，Dify 1.19.0 + DeepSeek flash + PowerContext 1.2.0）。
