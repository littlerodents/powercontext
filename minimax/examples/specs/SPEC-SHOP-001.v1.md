# SPEC-SHOP-001 v1：商品筛选规格

状态：已被 v2 取代（v2 增加 min_price 要求）。保留本文件用于验证"规格演进后上下文使用新内容"。

## S-1 筛选入口

`shop/filters.py::filter_products(products, *, max_price=None)` 提供唯一筛选入口。

## S-2 价格上限

当提供 `max_price` 时，仅保留 `price <= max_price` 的商品。

## S-3 缺失价格

`price is None` 的商品一律跳过，不参与任何筛选结果。

## S-4 无副作用

筛选返回新列表，不得修改入参 `products`，保留原顺序。
