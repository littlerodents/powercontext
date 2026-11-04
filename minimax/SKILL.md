---
name: sdd-context
description: 在局部代码修改前，使用 PowerContext 与 seekdb 检索当前规格、相关代码和测试，保留并恢复任务进展；适合持续演进项目的规格驱动开发。
---

# SDD 工程上下文

入口为 `minimax/sdd_context.py`，依赖 PowerContext 的 Python SDK 和嵌入式 seekdb，安装与完整样例见 `minimax/README.md`。

1. 新会话先运行 `python minimax/sdd_context.py resume`，检查 `saved_state` 中的目标、完成项、待办和验证结果，避免重复工作。
2. 首次或资料变化时使用 `import --kind spec|code|test --file <path>`。同一规格的不同版本使用相同的 `--identity`，更新后应重新导入。
3. 使用 `prepare --task "<任务>"` 查询；检查 `items` 中的类型、来源引用、版本和真实内容，以及 `saved_state`。检索不到所需资料时说明缺口并补充导入，不得编造上下文。
4. 将检索结果与目标文件交给使用 MiniMax 的开发助手修改代码。只把模型输出当作待审核的源代码，不执行模型给出的命令。
5. 运行功能与回归测试。把已完成事项、待办和实际检查结果写入 JSON，再用 `save-state --file <path>` 持久化。修改后的资料重新导入，以免下次检索到旧代码。
6. 启动新进程运行 `resume` 和 `prepare`，确认状态已读取并出现在新的任务上下文中。

返回值应能定位适用规格、代码与测试，并区分已完成、待完成和实际验证结果。不要把方案或检索结果当作已经完成的代码修改，不要伪造引用或测试成绩，也不要把密钥写入文档或证据。
