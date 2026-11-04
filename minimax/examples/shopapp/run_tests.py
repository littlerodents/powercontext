#!/usr/bin/env python3
"""零依赖测试运行器：执行 tests/test_filters.py 中的 N1–N3 / R1–R4 并汇总。

用法：python minimax/examples/shopapp/run_tests.py
退出码：全部通过=0；存在失败=1（修改前 N1/N2 失败为预期起点，见 examples/validation.md）。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
from test_filters import CASES  # noqa: E402


def main() -> int:
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as err:
            failed += 1
            print(f"FAIL {name}: {err}")
        except Exception:
            failed += 1
            print(f"ERROR {name}:\n{traceback.format_exc()}")
    total = len(CASES)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
