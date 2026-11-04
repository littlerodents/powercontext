#!/usr/bin/env python3
"""
资料回声 - 集成验证脚本

本脚本验证 seekdb 的完整生命周期：
1. 创建数据库并导入文档
2. 验证去重
3. 验证全文检索
4. 关闭数据库后重新打开验证持久化
5. 清理测试数据

运行方式：
    cd qoder
    .venv/bin/python test_integration.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import EchoDB


def run_cross_process_check(test_dir):
    """
    真实跨进程验证：启动子进程读取数据。
    子进程使用相同的 test_dir，验证数据在进程间可见。
    """
    script = f"""
import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from database import EchoDB

db = EchoDB(db_dir={test_dir!r})
try:
    docs = db.list_documents()
    print(f"DOC_COUNT={{len(docs)}}")
    if docs:
        print(f"FIRST_TITLE={{docs[0]['title']}}")
    results = db.search("Paxos")
    print(f"SEARCH_COUNT={{len(results)}}")
finally:
    db.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30
    )
    if result.returncode != 0:
        print(f"  [ERROR] 子进程失败: {result.stderr}")
        return {}

    output = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            key, val = line.split("=", 1)
            output[key] = val
    return output


def main():
    print("=" * 60)
    print("资料回声 - 集成验证")
    print("=" * 60)

    test_dir = tempfile.mkdtemp(prefix="echo_integration_")
    print(f"\n测试目录: {test_dir}")

    passed = 0
    failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            print(f"  [PASS] {name}")
            passed += 1
        else:
            print(f"  [FAIL] {name} {detail}")
            failed += 1

    try:
        # --- 阶段 1: 创建数据库并导入 ---
        print("\n--- 阶段 1: 创建数据库并导入文档 ---")
        db1 = EchoDB(db_dir=test_dir)

        result1 = db1.import_document(
            "数据库系统概论.md",
            "# 数据库系统概论\n\n关系代数是数据库理论的基础，包括选择、投影和连接操作。SQL 是关系数据库的标准语言。"
        )
        check("导入第一份文档", result1["status"] == "imported", f"got {result1}")
        doc_id_1 = result1["doc_id"]

        result2 = db1.import_document(
            "分布式系统.md",
            "# 分布式系统\n\nCAP 定理指出分布式系统无法同时满足一致性、可用性和分区容忍性。Paxos 和 Raft 是常见的一致性协议。"
        )
        check("导入第二份文档", result2["status"] == "imported", f"got {result2}")

        result3 = db1.import_document(
            "OceanBase.md",
            "# OceanBase\n\nOceanBase 是蚂蚁集团自研的分布式关系数据库，采用 Shared-Nothing 架构，基于 Paxos 协议保证数据一致性。"
        )
        check("导入第三份文档", result3["status"] == "imported", f"got {result3}")

        # --- 阶段 2: 验证去重 ---
        print("\n--- 阶段 2: 验证去重 ---")
        dup_result = db1.import_document(
            "重复文件.md",
            "# 数据库系统概论\n\n关系代数是数据库理论的基础，包括选择、投影和连接操作。SQL 是关系数据库的标准语言。"
        )
        check("重复内容检测为 duplicate", dup_result["status"] == "duplicate", f"got {dup_result}")
        check("重复文档 doc_id 一致", dup_result["doc_id"] == doc_id_1, f"{dup_result['doc_id']} != {doc_id_1}")

        # --- 阶段 3: 验证全文检索 ---
        print("\n--- 阶段 3: 验证全文检索 ---")
        results = db1.search("关系代数")
        check("搜索 '关系代数' 有结果", len(results) > 0, f"got {len(results)} results")
        if results:
            check("结果包含 snippet", "snippet" in results[0], f"keys: {results[0].keys()}")
            check("结果包含 score", "score" in results[0], f"keys: {results[0].keys()}")

        results2 = db1.search("Paxos")
        check("搜索 'Paxos' 有结果", len(results2) > 0, f"got {len(results2)} results")
        if results2:
            sources = [r["source"] for r in results2]
            check("Paxos 结果包含分布式系统", any("分布式" in s for s in sources), f"sources: {sources}")
            check("Paxos 结果包含 OceanBase", any("OceanBase" in s for s in sources), f"sources: {sources}")

        results3 = db1.search("量子计算")
        check("搜索 '量子计算' 无结果", len(results3) == 0, f"got {len(results3)} results")

        # --- 阶段 4: 验证文档列表 ---
        print("\n--- 阶段 4: 验证文档列表 ---")
        docs = db1.list_documents()
        check("文档列表数量正确", len(docs) == 3, f"got {len(docs)}")

        doc = db1.get_document(doc_id_1)
        check("获取文档详情", doc is not None, "doc is None")
        if doc:
            check("文档标题正确", doc["title"] == "数据库系统概论", f"got '{doc['title']}'")
            check("文档来源为文件名", doc["source"] == "数据库系统概论.md", f"got '{doc['source']}'")
            check("文档内容完整", "关系代数" in doc["content"], "content missing")

        stats = db1.get_stats()
        check("统计信息 document_count", stats["document_count"] == 3, f"got {stats['document_count']}")
        check("统计信息 total_content_bytes > 0", stats["total_content_bytes"] > 0)
        check("统计信息包含 data_dir", "data_dir" in stats, f"keys: {stats.keys()}")

        # --- 阶段 5: 关闭后重新打开验证持久化 ---
        print("\n--- 阶段 5: 关闭后重新打开验证持久化 ---")
        db1.close()
        check("数据库关闭成功", True)

        db2 = EchoDB(db_dir=test_dir)

        doc_after = db2.get_document(doc_id_1)
        check("重新打开后文档仍存在", doc_after is not None, "doc is None after reopen")
        if doc_after:
            check("重新打开后标题一致", doc_after["title"] == "数据库系统概论")

        docs_after = db2.list_documents()
        check("重新打开后文档列表数量一致", len(docs_after) == 3, f"got {len(docs_after)}")

        results_after = db2.search("Paxos")
        check("重新打开后搜索仍有效", len(results_after) > 0, f"got {len(results_after)} results")

        dup_after = db2.import_document(
            "再次重复.md",
            "# 数据库系统概论\n\n关系代数是数据库理论的基础，包括选择、投影和连接操作。SQL 是关系数据库的标准语言。"
        )
        check("重新打开后去重仍有效", dup_after["status"] == "duplicate", f"got {dup_after}")

        db2.close()

        # --- 阶段 6: 跨进程验证 ---
        print("\n--- 阶段 6: 跨进程验证（子进程读取） ---")
        cross = run_cross_process_check(test_dir)
        check("子进程能读取文档数量", cross.get("DOC_COUNT") == "3", f"got {cross.get('DOC_COUNT')}")
        check("子进程能搜索到 Paxos", int(cross.get("SEARCH_COUNT", "0")) > 0, f"got {cross.get('SEARCH_COUNT')}")

        # --- 阶段 7: 边界条件 ---
        print("\n--- 阶段 7: 边界条件 ---")
        db3 = EchoDB(db_dir=test_dir)

        empty_results = db3.search("")
        check("空查询返回空列表", empty_results == [], f"got {empty_results}")

        ws_results = db3.search("   ")
        check("空白查询返回空列表", ws_results == [], f"got {ws_results}")

        not_found = db3.get_document(999999)
        check("不存在的 doc_id 返回 None", not_found is None)

        try:
            db3.import_document("huge.txt", "x" * (10 * 1024 * 1024 + 1))
            check("超大文件应抛出 ValueError", False, "no exception raised")
        except ValueError:
            check("超大文件抛出 ValueError", True)
        except Exception as e:
            check("超大文件抛出 ValueError", False, f"got {type(e).__name__}: {e}")

        db3.close()

    except Exception as e:
        import traceback
        print(f"\n[ERROR] 未预期的异常: {e}")
        traceback.print_exc()
        failed += 1

    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\n已清理测试目录: {test_dir}")

    # --- 汇总 ---
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"验证完成: {passed}/{total} 通过, {failed}/{total} 失败")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
