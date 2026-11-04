"""
资料回声 - 自动化测试（真实 seekdb 验证）

测试覆盖：
1. 文档导入（含去重、大小限制）
2. 全文检索（基于 seekdb MATCH AGAINST）
3. 来源回查
4. 持久化验证（关闭后重新打开数据仍在）
5. 边界条件处理
6. 并发操作（多线程访问）
7. 跨进程验证

运行方式：
    cd qoder
    .venv/bin/python -m pytest test_echo.py -v
    或
    .venv/bin/python test_echo.py
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from database import EchoDB, _compute_hash, _extract_title, safe_filename


class TestHelperFunctions(unittest.TestCase):
    """测试辅助函数（不依赖 seekdb）"""

    def test_compute_hash_consistent(self):
        h1 = _compute_hash("hello world")
        h2 = _compute_hash("hello world")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_compute_hash_different(self):
        h1 = _compute_hash("hello")
        h2 = _compute_hash("world")
        self.assertNotEqual(h1, h2)

    def test_extract_title_from_markdown_h1(self):
        content = "# 我的标题\n\n正文内容"
        title = _extract_title("test.md", content)
        self.assertEqual(title, "我的标题")

    def test_extract_title_from_markdown_h2(self):
        content = "## 二级标题\n\n正文"
        title = _extract_title("test.md", content)
        self.assertEqual(title, "二级标题")

    def test_extract_title_from_filename(self):
        content = "没有标题的纯文本内容"
        title = _extract_title("/path/to/我的文件.txt", content)
        self.assertEqual(title, "我的文件.txt")

    def test_extract_title_empty_content(self):
        title = _extract_title("empty.txt", "")
        self.assertEqual(title, "empty.txt")

    def test_safe_filename_preserves_chinese(self):
        name = safe_filename("数据库系统概论.md")
        self.assertEqual(name, "数据库系统概论.md")

    def test_safe_filename_removes_directory(self):
        name = safe_filename("/etc/passwd/../../secret.txt")
        self.assertEqual(name, "secret.txt")

    def test_safe_filename_removes_control_chars(self):
        name = safe_filename("file\x00\x01\x1f\x7fname.txt")
        self.assertEqual(name, "filename.txt")

    def test_safe_filename_strips_dots(self):
        name = safe_filename("...hidden...")
        self.assertEqual(name, "hidden")

    def test_safe_filename_empty(self):
        name = safe_filename("")
        self.assertEqual(name, "未命名文件")

    def test_safe_filename_none(self):
        name = safe_filename(None)
        self.assertEqual(name, "未命名文件")


class TestEchoDBReal(unittest.TestCase):
    """测试真实 seekdb 操作"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="echo_test_")
        cls.db = EchoDB(db_dir=cls.test_dir)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        with self.db._get_conn().cursor() as cursor:
            cursor.execute("DELETE FROM documents")

    def test_import_basic(self):
        """测试基本文档导入"""
        content = "# 测试文档\n\n这是一份测试文档的内容，包含数据库相关知识点。"
        result = self.db.import_document("test.md", content)

        self.assertEqual(result["status"], "imported")
        self.assertIn("doc_id", result)
        self.assertEqual(result["title"], "测试文档")
        self.assertIsInstance(result["doc_id"], int)

    def test_import_deduplication(self):
        """测试去重：相同内容不应重复导入"""
        content = "这是用于去重测试的内容"

        result1 = self.db.import_document("file1.txt", content)
        self.assertEqual(result1["status"], "imported")

        result2 = self.db.import_document("file2.txt", content)
        self.assertEqual(result2["status"], "duplicate")
        self.assertEqual(result2["doc_id"], result1["doc_id"])

    def test_import_different_content(self):
        """测试不同内容应分别导入"""
        content1 = "第一份文档的内容"
        content2 = "第二份文档的内容"

        result1 = self.db.import_document("file1.txt", content1)
        result2 = self.db.import_document("file2.txt", content2)

        self.assertEqual(result1["status"], "imported")
        self.assertEqual(result2["status"], "imported")
        self.assertNotEqual(result1["doc_id"], result2["doc_id"])

    def test_import_size_limit(self):
        """测试文件大小限制"""
        large_content = "x" * (config.MAX_FILE_SIZE + 1)

        with self.assertRaises(ValueError) as ctx:
            self.db.import_document("large.txt", large_content)

        self.assertIn("超过限制", str(ctx.exception))

    def test_get_document(self):
        """测试获取文档详情"""
        content = "# 获取测试\n\n这份文档用于测试 get_document 方法。"
        imported = self.db.import_document("get_test.md", content)

        doc = self.db.get_document(imported["doc_id"])
        self.assertIsNotNone(doc)
        self.assertEqual(doc["doc_id"], imported["doc_id"])
        self.assertEqual(doc["title"], "获取测试")
        self.assertEqual(doc["source"], "get_test.md")
        self.assertEqual(doc["content"], content)
        self.assertIn("content_hash", doc)
        self.assertIn("imported_at", doc)

    def test_get_document_not_found(self):
        """测试获取不存在的文档"""
        doc = self.db.get_document(999999)
        self.assertIsNone(doc)

    def test_list_documents(self):
        """测试文档列表"""
        for i in range(3):
            self.db.import_document(f"doc{i}.txt", f"文档 {i} 的内容")

        docs = self.db.list_documents()
        self.assertEqual(len(docs), 3)

        for doc in docs:
            self.assertIn("doc_id", doc)
            self.assertIn("title", doc)
            self.assertIn("source", doc)
            self.assertIn("content_length", doc)
            self.assertIn("imported_at", doc)

    def test_list_documents_order(self):
        """测试文档列表按时间倒序"""
        self.db.import_document("first.txt", "第一个")
        time.sleep(0.01)
        self.db.import_document("second.txt", "第二个")

        docs = self.db.list_documents()
        self.assertGreater(docs[0]["imported_at"], docs[1]["imported_at"])

    def test_search_basic(self):
        """测试基本全文检索"""
        self.db.import_document(
            "db.md",
            "# 数据库系统\n\n关系代数是数据库理论的基础，包括选择、投影和连接操作。"
        )
        self.db.import_document(
            "os.md",
            "# 操作系统\n\n进程管理是操作系统的核心功能之一。"
        )

        results = self.db.search("关系代数")
        self.assertGreater(len(results), 0)

        found = False
        for r in results:
            if "关系代数" in r.get("snippet", "") or "关系代数" in r.get("title", ""):
                found = True
                break
        self.assertTrue(found, "搜索 '关系代数' 应找到数据库文档")

    def test_search_empty_query(self):
        """测试空查询"""
        results = self.db.search("")
        self.assertEqual(results, [])

    def test_search_whitespace_query(self):
        """测试空白查询"""
        results = self.db.search("   ")
        self.assertEqual(results, [])

    def test_search_no_match(self):
        """测试无匹配结果"""
        self.db.import_document("test.txt", "这是一份关于数据库的文档")

        results = self.db.search("量子计算")
        self.assertEqual(len(results), 0)

    def test_search_result_fields(self):
        """测试搜索结果包含必要字段"""
        self.db.import_document("fields.txt", "搜索字段测试内容")

        results = self.db.search("搜索字段")
        if results:
            r = results[0]
            self.assertIn("doc_id", r)
            self.assertIn("title", r)
            self.assertIn("source", r)
            self.assertIn("snippet", r)
            self.assertIn("score", r)

    def test_search_snippet_extraction(self):
        """测试搜索摘要提取"""
        long_content = "开头内容。" * 50 + "关键匹配词在这里。" + "结尾内容。" * 50
        self.db.import_document("long.txt", long_content)

        results = self.db.search("关键匹配词")
        if results:
            snippet = results[0].get("snippet", "")
            self.assertIn("关键匹配词", snippet)

    def test_get_stats(self):
        """测试统计信息"""
        self.db.import_document("stats1.txt", "统计测试内容一")
        self.db.import_document("stats2.txt", "统计测试内容二")

        stats = self.db.get_stats()
        self.assertIn("document_count", stats)
        self.assertIn("total_content_bytes", stats)
        self.assertIn("data_dir", stats)
        self.assertGreaterEqual(stats["document_count"], 2)
        self.assertGreater(stats["total_content_bytes"], 0)

    def test_source_is_filename_not_path(self):
        """测试 source 字段存储的是文件名而非服务器路径"""
        self.db.import_document("浏览器上传的文件.md", "内容")

        docs = self.db.list_documents()
        doc = docs[0]
        self.assertEqual(doc["source"], "浏览器上传的文件.md")
        self.assertNotIn("/", doc["source"].replace("浏览器上传的文件.md", ""))


class TestPersistence(unittest.TestCase):
    """测试数据持久化 - 关闭后重新打开数据仍在"""

    def test_data_persists_after_reopen(self):
        """测试数据在数据库关闭并重新打开后仍然存在"""
        test_dir = tempfile.mkdtemp(prefix="echo_persist_")

        try:
            db1 = EchoDB(db_dir=test_dir)
            result = db1.import_document("persist.md", "# 持久化测试\n\n这份文档用于验证数据持久化。")
            doc_id = result["doc_id"]
            db1.close()

            db2 = EchoDB(db_dir=test_dir)
            try:
                doc = db2.get_document(doc_id)
                self.assertIsNotNone(doc, "重新打开后文档应仍然存在")
                self.assertEqual(doc["title"], "持久化测试")
                self.assertEqual(doc["source"], "persist.md")

                docs = db2.list_documents()
                self.assertGreaterEqual(len(docs), 1)
            finally:
                db2.close()
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_dedup_persists_after_reopen(self):
        """测试去重在重新打开后仍然有效"""
        test_dir = tempfile.mkdtemp(prefix="echo_dedup_")

        try:
            db1 = EchoDB(db_dir=test_dir)
            content = "用于去重持久化测试的内容"
            db1.import_document("file1.txt", content)
            db1.close()

            db2 = EchoDB(db_dir=test_dir)
            try:
                result = db2.import_document("file2.txt", content)
                self.assertEqual(result["status"], "duplicate", "重新打开后去重应仍然有效")
            finally:
                db2.close()
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

    def test_search_works_after_reopen(self):
        """测试搜索在重新打开后仍然有效"""
        test_dir = tempfile.mkdtemp(prefix="echo_search_")

        try:
            db1 = EchoDB(db_dir=test_dir)
            db1.import_document("search.md", "分布式系统中的 Paxos 算法是一种一致性协议。")
            db1.close()

            db2 = EchoDB(db_dir=test_dir)
            try:
                results = db2.search("Paxos")
                self.assertGreater(len(results), 0, "重新打开后搜索应仍然有效")
            finally:
                db2.close()
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


class TestConcurrency(unittest.TestCase):
    """测试并发操作 - 多线程访问"""

    @classmethod
    def setUpClass(cls):
        cls.test_dir = tempfile.mkdtemp(prefix="echo_concurrent_")
        cls.db = EchoDB(db_dir=cls.test_dir)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        with self.db._get_conn().cursor() as cursor:
            cursor.execute("DELETE FROM documents")

    def test_concurrent_imports(self):
        """测试并发导入"""
        results = []
        errors = []

        def import_doc(i):
            try:
                content = f"并发测试文档 {i} 的内容"
                result = self.db.import_document(f"concurrent_{i}.txt", content)
                results.append(result)
            except Exception as e:
                errors.append((i, str(e)))

        threads = []
        for i in range(10):
            t = threading.Thread(target=import_doc, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"并发导入出错: {errors}")
        self.assertEqual(len(results), 10)
        imported = [r for r in results if r["status"] == "imported"]
        self.assertEqual(len(imported), 10)

    def test_concurrent_reads(self):
        """测试并发读取"""
        for i in range(5):
            self.db.import_document(f"read_{i}.txt", f"读取测试文档 {i}")

        results_list = []
        results_stats = []
        errors = []

        def read_docs():
            try:
                docs = self.db.list_documents()
                results_list.append(len(docs))
            except Exception as e:
                errors.append(("list", str(e)))

        def read_stats():
            try:
                stats = self.db.get_stats()
                results_stats.append(stats["document_count"])
            except Exception as e:
                errors.append(("stats", str(e)))

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=read_docs))
            threads.append(threading.Thread(target=read_stats))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"并发读取出错: {errors}")
        self.assertTrue(all(c == 5 for c in results_list))
        self.assertTrue(all(c == 5 for c in results_stats))

    def test_concurrent_search(self):
        """测试并发搜索"""
        self.db.import_document("search1.md", "数据库系统概论 关系代数 SQL")
        self.db.import_document("search2.md", "分布式系统 Paxos Raft 一致性")
        self.db.import_document("search3.md", "OceanBase 分布式数据库")

        results = []
        errors = []

        def search(query):
            try:
                res = self.db.search(query)
                results.append((query, len(res)))
            except Exception as e:
                errors.append((query, str(e)))

        threads = []
        queries = ["数据库", "Paxos", "OceanBase", "关系代数", "分布式"]
        for q in queries:
            for _ in range(3):
                threads.append(threading.Thread(target=search, args=(q,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"并发搜索出错: {errors}")
        self.assertEqual(len(results), 15)

    def test_concurrent_mixed_operations(self):
        """测试混合并发操作"""
        errors = []
        stop_flag = threading.Event()
        counter_lock = threading.Lock()
        counter = [0]

        def importer():
            while not stop_flag.is_set():
                try:
                    with counter_lock:
                        counter[0] += 1
                        i = counter[0]
                    self.db.import_document(
                        f"mixed_{threading.current_thread().name}_{i}.txt",
                        f"混合并发内容 {i} {threading.current_thread().name}"
                    )
                except Exception as e:
                    errors.append(("import", str(e)))
                    break
                time.sleep(0.001)

        def reader():
            while not stop_flag.is_set():
                try:
                    self.db.list_documents()
                    self.db.get_stats()
                except Exception as e:
                    errors.append(("read", str(e)))
                    break
                time.sleep(0.001)

        def searcher():
            while not stop_flag.is_set():
                try:
                    self.db.search("内容")
                except Exception as e:
                    errors.append(("search", str(e)))
                    break
                time.sleep(0.001)

        threads = [
            threading.Thread(target=importer, name="importer1"),
            threading.Thread(target=importer, name="importer2"),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=searcher),
        ]

        for t in threads:
            t.start()

        time.sleep(0.5)
        stop_flag.set()

        for t in threads:
            t.join(timeout=5)

        self.assertEqual(len(errors), 0, f"混合并发出错: {errors}")

    def test_concurrent_same_content_import(self):
        """回归测试：多线程同时导入相同内容，应只有一个 imported，其余 duplicate"""
        shared_content = "这是用于并发去重回归测试的相同内容，所有线程导入完全一样的文本。"
        results = []
        errors = []
        barrier = threading.Barrier(10)

        def import_same(i):
            try:
                barrier.wait(timeout=5)
                result = self.db.import_document(f"same_{i}.txt", shared_content)
                results.append(result)
            except Exception as e:
                errors.append((i, str(e)))

        threads = []
        for i in range(10):
            t = threading.Thread(target=import_same, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0, f"并发导入相同内容出错: {errors}")
        self.assertEqual(len(results), 10, f"应有 10 个结果，实际 {len(results)}")

        imported = [r for r in results if r["status"] == "imported"]
        duplicates = [r for r in results if r["status"] == "duplicate"]

        self.assertEqual(len(imported), 1, f"应恰好 1 个 imported，实际 {len(imported)}")
        self.assertEqual(len(duplicates), 9, f"应有 9 个 duplicate，实际 {len(duplicates)}")

        all_doc_ids = {r["doc_id"] for r in results}
        self.assertEqual(len(all_doc_ids), 1, f"所有结果应同一 doc_id，实际 {all_doc_ids}")

        all_titles = {r["title"] for r in results}
        self.assertEqual(len(all_titles), 1, f"所有结果应同一 title，实际 {all_titles}")

        docs = self.db.list_documents()
        self.assertEqual(len(docs), 1, f"数据库应只有 1 条记录，实际 {len(docs)}")


class TestCrossProcess(unittest.TestCase):
    """测试跨进程数据可见性"""

    def test_cross_process_read(self):
        """测试一个进程写入后另一个进程能读取"""
        test_dir = tempfile.mkdtemp(prefix="echo_cross_")

        try:
            db1 = EchoDB(db_dir=test_dir)
            db1.import_document("cross.md", "跨进程测试内容")
            db1.close()

            script = f"""
import sys
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
from database import EchoDB

db = EchoDB(db_dir={test_dir!r})
try:
    docs = db.list_documents()
    print(f"COUNT={{len(docs)}}")
    results = db.search("跨进程")
    print(f"SEARCH={{len(results)}}")
finally:
    db.close()
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=30
            )

            self.assertEqual(result.returncode, 0, f"子进程失败: {result.stderr}")

            output = {}
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, val = line.split("=", 1)
                    output[key] = val

            self.assertEqual(output.get("COUNT"), "1")
            self.assertEqual(output.get("SEARCH"), "1")
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


class TestConfig(unittest.TestCase):
    """测试配置模块"""

    def test_allowed_extensions(self):
        self.assertIn(".txt", config.ALLOWED_EXTENSIONS)
        self.assertIn(".md", config.ALLOWED_EXTENSIONS)
        self.assertIn(".markdown", config.ALLOWED_EXTENSIONS)

    def test_max_file_size(self):
        self.assertGreater(config.MAX_FILE_SIZE, 0)
        self.assertEqual(config.MAX_FILE_SIZE, 10 * 1024 * 1024)

    def test_snippet_length(self):
        self.assertGreater(config.SNIPPET_LENGTH, 0)

    def test_max_search_results(self):
        self.assertGreater(config.MAX_SEARCH_RESULTS, 0)


class TestExamplesExist(unittest.TestCase):
    """测试示例文件存在"""

    def test_examples_directory_exists(self):
        examples_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "examples"
        )
        self.assertTrue(
            os.path.isdir(examples_dir),
            f"示例目录不存在: {examples_dir}"
        )

    def test_example_files_exist(self):
        examples_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "examples"
        )
        if os.path.isdir(examples_dir):
            files = os.listdir(examples_dir)
            md_files = [f for f in files if f.endswith(".md")]
            self.assertGreater(len(md_files), 0, "没有示例 Markdown 文件")

    def test_example_files_importable(self):
        """测试示例文件可以被成功导入"""
        examples_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "examples"
        )
        if not os.path.isdir(examples_dir):
            self.skipTest("示例目录不存在")

        test_dir = tempfile.mkdtemp(prefix="echo_examples_")
        try:
            db = EchoDB(db_dir=test_dir)
            try:
                md_files = [f for f in os.listdir(examples_dir) if f.endswith(".md")]
                for filename in md_files:
                    filepath = os.path.join(examples_dir, filename)
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    result = db.import_document(filename, content)
                    self.assertEqual(result["status"], "imported", f"导入 {filename} 失败")
            finally:
                db.close()
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
