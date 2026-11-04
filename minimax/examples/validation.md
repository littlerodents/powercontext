# 验证样例：商品筛选增加最低价格下限

## 任务

按 `SPEC-SHOP-001` v2，在 `shop/filters.py::filter_products` 增加可选 `min_price`：保留价格大于或等于下限的商品；与既有 `max_price` 同时给出时取交集；`None` 与不传等价。

必须保持原有行为：无参数返回全部有价格的商品且顺序不变；`max_price` 仍为包含式上限；缺失价格的商品跳过；返回新列表且不修改输入。

## 检查

| 编号 | 行为 | 修改前 | 修改后 |
| --- | --- | --- | --- |
| N1 | 最低价格下限 | 失败 | 通过 |
| N2 | 最低与最高价格交集 | 失败 | 通过 |
| N3 | `min_price=None` 与不传等价 | 失败 | 通过 |
| R1–R4 | 无参、原有上限、缺失价格、不修改输入 | 4 项通过 | 4 项通过 |

真实输出见 `../evidence/before-tests.txt` 和 `../evidence/after-tests.txt`。从仓库根运行 `python minimax/examples/shopapp/run_tests.py` 可检查当前文件。

先导入 v1 的规格、代码和测试并形成上下文；以同一 identity 重新导入 v2 后再次查询，MiniMax 根据该上下文实际修改文件。修改后重新导入代码，将任务状态和测试结果存储，再以新进程执行 `resume` 和 `prepare`，检查已保存状态被读取并用于后续上下文。所有可复现命令和依赖见 `../README.md`。
