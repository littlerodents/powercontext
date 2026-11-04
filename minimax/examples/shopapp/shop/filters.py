"""示例业务工程：商品筛选（SDD 验证样例的修改目标）。"""
from __future__ import annotations

from typing import Any


def filter_products(
    products: list[dict[str, Any]],
    *,
    max_price: float | None = None,
    min_price: float | None = None,
) -> list[dict[str, Any]]:
    """按 SPEC-SHOP-001 v2 实现价格区间筛选。

    支持可选的价格上限和下限；缺失价格跳过；返回新列表并保持原顺序。
    """
    result: list[dict[str, Any]] = []
    for p in products:
        price = p.get("price")
        if price is None:
            continue
        if max_price is not None and price > max_price:
            continue
        if min_price is not None and price < min_price:
            continue
        result.append(dict(p))
    return result
