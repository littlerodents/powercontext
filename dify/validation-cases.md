# 验证样例 · 时光胶囊伙伴

> 前置：PowerContext Server 已启动（`curl http://127.0.0.1:8000/health/ready` 200）；DSL 已导入，模型供应商已配置，应用环境变量已按 README 填好。
> 以下 Case 1/2 于 **2026-11-05 真机执行**（Dify 1.19.0 自托管 + DeepSeek `deepseek-flash` + PowerContext 1.2.0），回答为原始记录。

## Case 1：记忆写入（首轮，初次见面分支）

- 初始条件：全新对话（PowerContext Scope 内已有早前测试记忆则走带记忆分支，语义等价）。
- 输入：`记住：我最喜欢的水果是榴莲，我的梦想是去冰岛看极光。`
- 操作步骤：发送消息，等待回复；随后在 PowerContext 侧核对落库：
  `curl -X POST http://127.0.0.1:8000/v1/memory/search -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"scope_id":"<scope>","query":"水果","limit":3}'`
- 预期表现：伙伴确认记住；curl 返回含 `"text":"[用户] 记住：我最喜欢的水果是榴莲…"` 且 `"matched_by":["fts"]`。
- **真机结果 ✅**：回复正常，workflow `exceptions_count:0`、`total_steps:7`；PowerContext 落库可查。

## Case 2：跨会话记忆召回（核心验收）

- 初始条件：**新开一个会话**（与 Case 1 不同 conversation）。
- 输入：`新会话终验：我之前跟你说过我最喜欢什么水果？`
- 操作步骤：直接发送。
- 预期表现：回答包含"榴莲"（以及冰岛极光），并表明来自记忆。
- **真机结果 ✅（原文）**：
  > 我记得呀，你说过最喜欢的水果是榴莲😄 还顺带提过梦想是去冰岛看极光。这两样我可都替你存在时光胶囊里了，没丢～

  该回答依赖 HTTP 召回节点返回的 fts 命中（机械分支 `hit_count>0`），证明记忆真实存于 PowerContext 并跨会话复用。

## Case 3：给未来的信（体验彩蛋）

- 初始条件：任意会话。
- 输入：`给未来：希望明年今天的我，已经去过一次冰岛。`
- 预期表现：伙伴封存这句话（写回 PowerContext）。
- 之后新会话输入：`读读我写给未来的信`。
- 预期表现：召回 fts 命中"给未来"记忆，伙伴复述信的内容并聊聊变化。

## 故障降级（防呆）

- Scope 未配置/写错：召回失败按 0 条处理 → 走"初次见面"分支，应用不报错；
  写回失败发生在回复输出之后，不影响体验。
- 模型思考标签：Code 节点机械剥离 `<think>…</think>`，输出保持干净。
