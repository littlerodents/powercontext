# 验证样例 · 时光胶囊伙伴（v3.3，2026-11-06 真机执行）

> 环境：Dify 1.19.0 自托管（Docker）+ DeepSeek `deepseek-flash` + PowerContext（本仓库 ecosystem 分支，带 Bearer 鉴权）。
> 全部结果为机器采集（SSE 原始流 + 节点级 run 摘要，见 `evidence/`），非人工自述。

## Case 1：记忆写入（会话 A，judge-demo）

- 初始条件：**全新 PowerContext 数据库**（服务刚启动，scopes 仅系统 Default）。
- 输入：`记住：我最喜欢的水果是榴莲，梦想去冰岛看极光。`
- 操作步骤：发起对话；等回复。
- 预期表现：召回 0 命中 → 走"初次见面"分支；回复确认记住；写回节点 succeeded。
- **真机结果 ✅**（`03-run-summary-sessionA.json`）：`hit_count: 0`；answer："叮！榴莲和小极光已存档📦……"；11 节点全 succeeded。

## Case 2：跨会话记忆召回（会话 B，核心验收）

- 初始条件：**新开 conversation**（与 A 不同的 conversation_id），同一 `user_id=judge-demo`。
- 输入：`新会话提问：我之前跟你说过我最喜欢什么水果？我的梦想是什么？`
- 操作步骤：直接发送。
- 预期表现：召回命中 A 写入的记忆，回答包含"榴莲"与"冰岛极光"。
- **真机结果 ✅**（`04-run-summary-sessionB.json`）：`hit_count: 1`，命中文本 `- [用户] 记住：我最喜欢的水果是榴莲，梦想去冰岛看极光`；answer："**当然记得！你说过最喜欢的水果是榴莲，梦想是去冰岛看极光。🌌** 这两个我都替你好好收着呢……"
- 佐证：`05-powercontext-rows.txt` 为 PowerContext 侧直接 curl 的 memory/search 原始响应（matched_by: fts）。

## Case 3：用户隔离（other-user，负面验证）

- 初始条件：任意会话，`user_id=other-user`。
- 输入：`我之前跟你说过我最喜欢什么水果？`
- 预期表现：**看不到** judge-demo 的记忆（0 命中 → 初次见面分支）。
- **真机结果 ✅**（`06-isolation-other-user.json`）：`hit_count: 0`；answer："**咱们才第一次见呀，我还没记住呢！** 现在告诉我你最爱什么水果？"

## Case 4：给未来的信（体验彩蛋）

- 输入：`给未来：希望明年今天的我，已经去过一次冰岛。`
- 之后新会话输入：`读读我写给未来的信`。
- 预期表现：召回命中"给未来"记忆，伙伴复述信的内容。

## 故障降级（防呆，均实测）

- PowerContext 停机/401/409：召回/写回节点 default-value 降级，对话不中断（走初次分支）。
- 模型思考链泄漏：Code 清洗节点机械剥离。
