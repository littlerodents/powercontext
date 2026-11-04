# SDD 工程上下文：MiniMax × PowerContext × seekdb

本样例为商品筛选函数增加 `min_price`。上下文模块将规格、代码和测试存入 PowerContext Memory，由嵌入式 seekdb 提供持久化和全文检索；检索结果交给 MiniMax 修改代码。结果、前后测试和跨进程恢复证据见 `evidence/`。所有文件均位于 `minimax/`，以仓库根目录作为以下命令的当前目录。

## 组件和入口

- `sdd_context.py`：PowerContext Python SDK + `SeekDBConfig`，提供 `import`、`prepare`、`save-state`、`resume`；每次启动都是独立进程。默认数据库路径为 `minimax/.state/seekdb`，`--database` 可指定隔离目录。`--project` 是项目别名，默认 `shop-sdd`，不是人工伪造的 scope ID。
- `run_minimax.py`：读取 `evidence/context-v2.json` 和目标文件，将其发送给 MiniMax，并将返回的完整文件内容写入样例；不执行模型输出的命令。凭据从环境变量读取，不写入文件。
- `examples/shopapp`：可运行的业务函数和 7 项检查；`examples/specs`：同一规格的 v1/v2。
- `evidence/`：真实导入、检索、模型调用元数据、代码差异、测试和恢复结果；不包含密钥。

## 环境

实测 Python 3.12.2，PowerContext 包版本 `0.1.dev1+gdab240063.d20261104`，`pylibseekdb 1.4.0.post1`。一个可复现安装方式（需要 Git 和联网）为：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install 'powercontext[seekdb] @ git+https://github.com/oceanbase/powercontext.git@dab240063bfcfa7917898dc44da4baadb3747bd0' 'pylibseekdb==1.4.0.post1' 'PyMySQL==1.2.0'
```

PowerContext 以 Python SDK 直接嵌入运行，不需要另起 HTTP server。嵌入式 seekdb 的平台支持以该版本安装说明为准；数据库文件已被 `.gitignore` 排除。干净环境实测时，PyMySQL 1.2.3 与 aiomysql 0.3.2 的字节转义不兼容，因此上面固定到通过验证的 1.2.0。

## 重现流程

本仓库保留的是 MiniMax 修改**之后**的文件。若要完整重演，请在独立工作副本执行下列命令；第一步会将样例文件还原到修改前的副本。实际修改需要提供兼容 Chat Completions 的 MiniMax 服务配置。运行后，`run_minimax.py` 会覆盖目标文件及 `before_filters.py`、`change.diff`、`minimax-call.json`。

```bash
cp minimax/evidence/before_filters.py minimax/examples/shopapp/shop/filters.py
python minimax/examples/shopapp/run_tests.py || true  # 预期 4/7：N1–N3 失败，R1–R4 通过

python minimax/sdd_context.py import --kind spec --identity SPEC-SHOP-001 --file minimax/examples/specs/SPEC-SHOP-001.v1.md
python minimax/sdd_context.py import --kind code --file minimax/examples/shopapp/shop/filters.py
python minimax/sdd_context.py import --kind test --file minimax/examples/shopapp/tests/test_filters.py
python minimax/sdd_context.py prepare --task 'max_price filter_products'

# 在同一 identity 更新规格；检索内容应随版本变化。
python minimax/sdd_context.py import --kind spec --identity SPEC-SHOP-001 --file minimax/examples/specs/SPEC-SHOP-001.v2.md
python minimax/sdd_context.py prepare --task 'min_price filter_products' > minimax/evidence/context-v2.json

# 在安全的本地环境设置 MINIMAX_API_KEY 和 MINIMAX_API_HOST，不要写入仓库。
# 可选：MINIMAX_MODEL；本次实际请求为 minimax/minimax-m3.1-flash。
python minimax/run_minimax.py
python minimax/examples/shopapp/run_tests.py       # 预期 7/7

# 更新存储中的代码，并保存当前任务、完成项、待办和检查结果。
python minimax/sdd_context.py import --kind code --file minimax/examples/shopapp/shop/filters.py
python minimax/sdd_context.py save-state --file minimax/evidence/task-state-complete.json
python minimax/sdd_context.py resume
python minimax/sdd_context.py prepare --task 'min_price filter_products'
```

最后两条命令分别启动新的 Python 进程，读取数据库中保存的状态；`prepare` 的 `saved_state` 也包含该状态，证明后续任务实际使用它。若重演时结果与已保存的状态不同，应先修改 state JSON 再保存，不能把样例结果当成当前结果。

## 输入与输出

`import` 接受文件、`spec|code|test` 类型及可选稳定 identity；同一 identity 内容改变时更新版本。`prepare` 接受任务文本，返回匹配的文档内容、来源/版本引用、匹配方式与已保存状态。`save-state` 接受包含 `objective`、`completed`、`pending`、`checks` 的 JSON 对象；`resume` 返回存储的状态。命令帮助见 `python minimax/sdd_context.py -h`。

已完成的实际运行及版本、边界和证据索引见 [TECH_USAGE.md](TECH_USAGE.md)，任务与回归定义见 [examples/validation.md](examples/validation.md)。本地结果不代表官方评测得分。
