"""
资料回声 - seekdb 数据库层

使用 pylibseekdb 1.4.0 + PyMySQL 1.2.0 实现真实的全文检索存储。

架构：
- pylibseekdb.open(db_dir) 创建 seekdb 实例
- 通过 pymysql.connect(**instance.connection_options()) 获得参数化 SQL 接口
- 使用 FULLTEXT INDEX + ngram parser 实现中文全文检索
- 线程安全：每个线程使用独立的 PyMySQL 连接

数据模型（documents 表）：
- doc_id: INTEGER PRIMARY KEY AUTO_INCREMENT
- title: VARCHAR(500) 文档标题
- source: VARCHAR(1000) 文件来源（浏览器上传仅为文件名）
- content: LONGTEXT 文档全文
- content_hash: CHAR(64) UNIQUE 内容 SHA-256 哈希（用于去重）
- imported_at: DOUBLE 导入时间戳
"""
import hashlib
import os
import re
import threading
import time

import pylibseekdb
import pymysql

import config


def _compute_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _extract_title(filepath: str, content: str) -> str:
    """从文件内容提取标题：优先取 Markdown # 标题，否则取文件名"""
    for line in content.split("\n")[:10]:
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title
        elif line.startswith("## "):
            title = line[3:].strip()
            if title:
                return title
    return os.path.basename(filepath)


def safe_filename(filename: str) -> str:
    """
    安全处理文件名。
    - 保留中文字符
    - 去除目录遍历（只取 basename）
    - 去除控制字符（ASCII 0-31, 127）
    - 去除首尾空白和点号
    """
    if not filename:
        return "未命名文件"
    name = os.path.basename(filename)
    name = re.sub(r'[\x00-\x1f\x7f]', '', name)
    name = name.strip().strip('.')
    if not name:
        return "未命名文件"
    return name


class EchoDB:
    """资料回声数据库 - 基于 seekdb + PyMySQL 的文档存储与全文检索"""

    def __init__(self, db_dir=None):
        self.db_dir = db_dir or config.DATA_DIR
        os.makedirs(self.db_dir, exist_ok=True)

        self._instance = None
        self._local = threading.local()
        self._init_seekdb()
        self._init_schema()

    def _init_seekdb(self):
        """初始化 seekdb 实例"""
        self._instance = pylibseekdb.open(self.db_dir)

    def _get_conn(self):
        """获取当前线程的 PyMySQL 连接（线程局部存储）"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn_opts = self._instance.connection_options()
            self._local.conn = pymysql.connect(
                **conn_opts,
                database="test",
                charset="utf8mb4",
                autocommit=True,
                cursorclass=pymysql.cursors.DictCursor
            )
        return self._local.conn

    def _close_thread_conn(self):
        """关闭当前线程的连接"""
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_schema(self):
        """创建 documents 表和全文索引"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS documents (
                        doc_id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        title VARCHAR(500) NOT NULL,
                        source VARCHAR(1000) NOT NULL,
                        content LONGTEXT NOT NULL,
                        content_hash CHAR(64) NOT NULL UNIQUE,
                        imported_at DOUBLE NOT NULL
                    )
                """)

                try:
                    cursor.execute("""
                        CREATE FULLTEXT INDEX ft_content
                        ON documents (content)
                        WITH PARSER ngram
                    """)
                except pymysql.err.OperationalError as e:
                    if e.args[0] != 1061:
                        raise

                try:
                    cursor.execute("""
                        CREATE FULLTEXT INDEX ft_title_content
                        ON documents (title, content)
                        WITH PARSER ngram
                    """)
                except pymysql.err.OperationalError as e:
                    if e.args[0] != 1061:
                        raise
        except Exception:
            self._close_thread_conn()
            raise

    def import_document(self, filename: str, content: str) -> dict:
        """
        导入单个文档到 seekdb。

        Args:
            filename: 文件名（浏览器上传仅提供文件名，不伪装服务器路径）
            content: 文件内容

        Returns:
            dict: {"doc_id": int, "status": "imported"|"duplicate", "title": str}

        Raises:
            ValueError: 文件内容超限
        """
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > config.MAX_FILE_SIZE:
            raise ValueError(
                f"文件大小 {content_bytes} 字节超过限制 {config.MAX_FILE_SIZE} 字节"
            )

        content_hash = _compute_hash(content)
        title = _extract_title(filename, content)
        safe_name = safe_filename(filename)

        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT doc_id, title FROM documents WHERE content_hash = %s",
                    (content_hash,)
                )
                existing = cursor.fetchone()
                if existing:
                    return {
                        "doc_id": existing["doc_id"],
                        "status": "duplicate",
                        "title": existing["title"],
                    }

                now = time.time()
                try:
                    cursor.execute(
                        """INSERT INTO documents (title, source, content, content_hash, imported_at)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (title, safe_name, content, content_hash, now)
                    )
                    doc_id = cursor.lastrowid
                    return {
                        "doc_id": doc_id,
                        "status": "imported",
                        "title": title,
                    }
                except pymysql.err.IntegrityError as e:
                    if e.args[0] == 1062:
                        cursor.execute(
                            "SELECT doc_id, title FROM documents WHERE content_hash = %s",
                            (content_hash,)
                        )
                        existing = cursor.fetchone()
                        if existing:
                            return {
                                "doc_id": existing["doc_id"],
                                "status": "duplicate",
                                "title": existing["title"],
                            }
                    raise
        except Exception:
            self._close_thread_conn()
            raise

    def search(self, query: str, limit=None) -> list:
        """
        全文检索 - 使用 seekdb 的 MATCH AGAINST。

        Args:
            query: 搜索关键词
            limit: 最大返回数量

        Returns:
            list[dict]: 搜索结果，每项包含:
                - doc_id: 文档 ID
                - title: 文档标题
                - source: 文件来源
                - snippet: 匹配片段
                - score: 相关性分数
        """
        if not query or not query.strip():
            return []

        limit = limit or config.MAX_SEARCH_RESULTS
        query = query.strip()

        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                sql = """
                    SELECT doc_id, title, source, content,
                           MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
                    FROM documents
                    WHERE MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE)
                    ORDER BY score DESC
                    LIMIT %s
                """
                cursor.execute(sql, (query, query, limit))
                rows = cursor.fetchall()

            results = []
            for row in rows:
                snippet = self._extract_snippet(row["content"], query)
                results.append({
                    "doc_id": row["doc_id"],
                    "title": row["title"],
                    "source": row["source"],
                    "snippet": snippet,
                    "score": float(row["score"]) if row["score"] else 0.0,
                })
            return results
        except Exception:
            self._close_thread_conn()
            raise

    def _extract_snippet(self, content: str, query: str) -> str:
        """从内容中提取包含关键词的摘要片段"""
        if not content:
            return ""

        query_lower = query.lower()
        content_lower = content.lower()

        pos = content_lower.find(query_lower)
        if pos == -1:
            words = query.split()
            for word in words:
                pos = content_lower.find(word.lower())
                if pos != -1:
                    break

        if pos == -1:
            return content[:config.SNIPPET_LENGTH] + "..."

        start = max(0, pos - config.SNIPPET_LENGTH // 2)
        end = min(len(content), pos + config.SNIPPET_LENGTH // 2)

        snippet = content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."

        return snippet

    def get_document(self, doc_id: int) -> dict | None:
        """根据 doc_id 获取完整文档"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT doc_id, title, source, content, content_hash, imported_at FROM documents WHERE doc_id = %s",
                    (doc_id,)
                )
                return cursor.fetchone()
        except Exception:
            self._close_thread_conn()
            raise

    def list_documents(self) -> list:
        """列出所有已导入文档的元数据"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT doc_id, title, source,
                           LENGTH(content) AS content_length,
                           imported_at
                    FROM documents
                    ORDER BY imported_at DESC
                """)
                return cursor.fetchall()
        except Exception:
            self._close_thread_conn()
            raise

    def get_stats(self) -> dict:
        """获取数据库统计信息"""
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS cnt, SUM(LENGTH(content)) AS total_bytes FROM documents")
                row = cursor.fetchone()

            return {
                "document_count": row["cnt"] or 0,
                "total_content_bytes": row["total_bytes"] or 0,
                "data_dir": self.db_dir,
            }
        except Exception:
            self._close_thread_conn()
            raise

    def close(self):
        """关闭当前线程的数据库连接和 seekdb 实例"""
        self._close_thread_conn()

        if self._instance:
            try:
                self._instance.close()
            except Exception:
                pass
            self._instance = None

    def __del__(self):
        self.close()
