"""回归与新行为断言（被 run_tests.py 调用；也可直接用于 pytest）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shop.filters import filter_products  # noqa: E402

PRODUCTS = [
    {"name": "a", "price": 10},
    {"name": "b", "price": 50},
    {"name": "c", "price": 120},
    {"name": "d", "price": None},
]


def _call_min_price(**kwargs):
    try:
        return filter_products(PRODUCTS, **kwargs)
    except TypeError as err:
        raise AssertionError(f"min_price 参数尚未实现（预期起点）：{err}") from None


def n1_min_price_floor():
    got = [p["name"] for p in _call_min_price(min_price=50)]
    assert got == ["b", "c"], f"N1 期望 ['b','c'] 实得 {got}"


def n2_min_max_intersection():
    got = [p["name"] for p in _call_min_price(min_price=20, max_price=100)]
    assert got == ["b"], f"N2 期望 ['b'] 实得 {got}"


def n3_min_price_none_equivalent():
    got = _call_min_price(min_price=None)
    assert [p["name"] for p in got] == ["a", "b", "c"]


def r1_no_args_returns_all_with_price():
    got = filter_products(PRODUCTS)
    assert [p["name"] for p in got] == ["a", "b", "c"], "R1 无参应返回全部有价格商品且顺序不变"


def r2_max_price_kept():
    got = [p["name"] for p in filter_products(PRODUCTS, max_price=50)]
    assert got == ["a", "b"], "R2 max_price 上限语义必须保持"


def r3_none_price_skipped():
    got = filter_products(PRODUCTS, max_price=1000)
    assert all(p["price"] is not None for p in got), "R3 缺失价格必须跳过"


def r4_no_mutation_returns_new_list():
    snapshot = [dict(p) for p in PRODUCTS]
    got = filter_products(PRODUCTS, max_price=60)
    assert got is not PRODUCTS, "R4 必须返回新列表"
    assert PRODUCTS == snapshot, "R4 不得修改入参"


CASES = [
    ("N1", n1_min_price_floor),
    ("N2", n2_min_max_intersection),
    ("N3", n3_min_price_none_equivalent),
    ("R1", r1_no_args_returns_all_with_price),
    ("R2", r2_max_price_kept),
    ("R3", r3_none_price_skipped),
    ("R4", r4_no_mutation_returns_new_list),
]
