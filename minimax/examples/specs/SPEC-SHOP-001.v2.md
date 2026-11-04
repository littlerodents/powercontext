# SPEC-SHOP-001 v2：商品筛选规格

状态：当前有效。相对 v1 的变更：新增 S-2b 最低价格下限。

## S-1 筛选入口

`shop/filters.py::filter_products(products, *, max_price=None, min_price=None)` 提供唯一筛选入口。

## S-2 价格上限（沿用 v1）

当提供 `max_price` 时，仅保留 `price <= max_price` 的商品。

## S-2b 价格下限（v2 新增）

当提供 `min_price` 时，仅保留 `price >= min_price` 的商品；与 `max_price` 同时给出时按交集过滤。`min_price=None` 与不传等价。

## S-3 缺失价格（沿用 v1）

`price is None` 的商品一律跳过，不参与任何筛选结果。

## S-4 无副作用（沿用 v1）

筛选返回新列表，不得修改入参 `products`，保留原顺序。
