# 技术使用记录

## 实测版本与作用

| 组件 | 本次实测 | 在任务中的作用 |
| --- | --- | --- |
| Python | 3.12.2 | 分别启动上下文模块的独立进程 |
| MiniMax | 请求及响应模型 `minimax/minimax-m3.1-flash`，Chat Completions 兼容接口 | 读取真实检索上下文，生成 `filters.py` 修改；调用元数据见 `evidence/minimax-call.json` |
| PowerContext | 包版本 `0.1.dev1+gdab240063.d20261104`，Python SDK | 创建 scope，管理版本化 Memory 文档，保存并在后续进程读取任务状态 |
| seekdb | `pylibseekdb 1.4.0.post1`，嵌入式 | 持久化 Memory；原生 FTS 搜索结果用于构造 MiniMax 任务上下文 |

MiniMax 实际调用通过用户配置的兼容 API 地址；凭据只取自进程环境变量，未存入本仓库。响应仅作为源文件文本写入，未执行其提供的命令。

## 运行证据

- 初始行为：`evidence/before-tests.txt`，4/7，四项原有行为通过，新增参数的三项失败。
- 规格演进：`evidence/import-spec-v1.json` 和 `import-spec-v2.json` 记录相同 entry ID；v2 的版本号为 2。`context-v1.json` / `context-v2.json` 保存对应检索结果。v2 检索包含规格、代码、测试三类真实资料。
- MiniMax 修改：`evidence/minimax-call.json` 记录模型、token 用量及三个上下文项；`evidence/change.diff` 是实际差异。`before_filters.py` 是修改前副本。
- 修改后：`evidence/after-tests.txt` 为独立重新执行的 7/7 输出；N1–N3 验证新增行为，R1–R4 验证原有行为。
- 恢复与使用：`state-complete-saved.json`、`state-complete-resumed.json` 分别为保存和新进程恢复结果；`context-complete-resumed.json` 中的 `saved_state` 与恢复结果一致，检索到了更新后的代码及规格、测试。`import-code-after.json` 记录修改后代码重新导入。
- 干净虚拟环境重建：按 README 安装并固定 `PyMySQL==1.2.0` 后，在独立数据库中再次完成 v1→v2 更新、三类资料检索、状态保存与新进程恢复。未固定时解析出的 PyMySQL 1.2.3 在当前 aiomysql 0.3.2 下字节转义报错，故不能用于此复现。
- `task-state.json` 与 `state-after-restart.json` 保留的是较早的配额耗尽阶段；它们是历史证据，当前完成状态以 `task-state-complete.json` 和 `state-complete-resumed.json` 为准。

完整命令及复现注意事项见 README。所有结论为本地验证，尚需以官方评测结果为准。
