"""示例业务工程：商品筛选（SDD 验证样例的修改目标）。"""
from __future__ import annotations

from typing import Any


def filter_products(products: list[dict[str, Any]], *, max_price: float | None = None) -> list[dict[str, Any]]:
    """按 SPEC-SHOP-001 v1 实现：max_price 上限；缺失价格跳过；返回新列表。

    v2 任务（由 MiniMax 完成）：增加 min_price 下限，保持 R1–R4 回归行为。
    """
    result: list[dict[str, Any]] = []
    for p in products:
        price = p.get("price")
        if price is None:
            continue
        if max_price is not None and price > max_price:
            continue
        result.append(dict(p))
    return result
