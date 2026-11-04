# 资料回声（Material Echo）

一个帮助学生和研究者导入和检索本地资料的工具。基于 seekdb（OceanBase 嵌入式全文检索引擎）+ PyMySQL 实现真实的数据库级全文搜索。

## 功能特性

- **文件导入**：支持拖拽或选择 .txt / .md 文件，自动从 Markdown 标题或文件名提取标题
- **去重检测**：基于内容 SHA-256 哈希，重复文件自动跳过（跨重启持久有效）
- **大小限制**：单文件最大 10MB（可通过 `ECHO_MAX_FILE_SIZE` 环境变量调整）
- **全文检索**：基于 seekdb 的 `MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE)` 全文搜索，使用 ngram parser 支持中文
- **来源回查**：每条搜索结果展示文档标题和原始文件名（保留中文，去除路径和控制字符）
- **响应式界面**：适配桌面和移动端，包含空状态和错误提示，所有用户输入安全转义
- **数据持久化**：数据保存在 seekdb 数据库中，程序重启后数据仍在
- **线程安全**：每个 Flask 线程使用独立的 PyMySQL 连接，避免并发冲突

## 技术架构

| 组件 | 技术 | 说明 |
|------|------|------|
| 数据库引擎 | pylibseekdb 1.4.0.post1 | seekdb 嵌入式数据库 |
| SQL 接口 | PyMySQL 1.2.0 | 参数化 SQL 查询 |
| 全文索引 | FULLTEXT INDEX + ngram | 中文分词全文检索 |
| 并发控制 | threading.local() | 线程局部存储，每线程独立连接 |
| Web 框架 | Flask >= 3.0 | HTTP API 服务 |
| 前端 | 原生 HTML/CSS/JS | 无构建步骤，安全转义防 XSS |

### seekdb 接口使用

```python
import pylibseekdb
import pymysql
import threading

# 创建 seekdb 实例
instance = pylibseekdb.open(db_dir)

# 线程局部存储，每个线程独立连接
_local = threading.local()

def _get_conn():
    if not hasattr(_local, 'conn') or _local.conn is None:
        conn_opts = instance.connection_options()
        _local.conn = pymysql.connect(
            **conn_opts,
            database="test",
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor
        )
    return _local.conn

# 使用参数化 SQL 进行全文检索
conn = _get_conn()
cursor = conn.cursor()
cursor.execute("""
    SELECT doc_id, title, source, content,
           MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
    FROM documents
    WHERE MATCH(title, content) AGAINST (%s IN NATURAL LANGUAGE MODE)
    ORDER BY score DESC
    LIMIT %s
""", (query, query, limit))
```

## 数据保存路径

数据默认保存在项目目录下的 `data/` 子目录中。可通过环境变量自定义：

```bash
# 自定义数据目录
export ECHO_DATA_DIR=/path/to/your/data

# 自定义文件大小限制（字节）
export ECHO_MAX_FILE_SIZE=20971520  # 20MB

# 自定义服务地址和端口
export ECHO_HOST=0.0.0.0
export ECHO_PORT=8080
```

## 依赖版本（已实测）

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | >= 3.10 | 运行环境 |
| Flask | >= 3.0, < 4.0 | Web 框架 |
| Werkzeug | >= 3.0, < 4.0 | Flask 依赖 |
| pylibseekdb | == 1.4.0.post1 | seekdb Python 绑定 |
| PyMySQL | == 1.2.0 | MySQL 协议客户端 |

## 从零启动

```bash
# 1. 进入项目目录
cd qoder

# 2. 创建虚拟环境并安装依赖（如果尚未完成）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 启动服务
.venv/bin/python app.py

# 4. 打开浏览器访问
# http://127.0.0.1:5000
```

注意：Flask 默认以 `debug=False` 启动。如需开发模式调试，可设置环境变量 `FLASK_DEBUG=1`。

## 内置示例资料

项目在 `examples/` 目录下提供了 3 个公开示例文件：

| 文件 | 主题 |
|------|------|
| `数据库系统概论.md` | 关系数据库基础、SQL、范式理论、事务管理 |
| `分布式系统笔记.md` | CAP 定理、BASE 理论、Paxos/Raft 一致性协议 |
| `OceanBase简介.md` | OceanBase 架构、存储引擎、高可用、应用场景 |

### 使用示例资料验证功能

1. 启动应用后，将 `examples/` 目录下的文件拖拽到上传区域
2. 在搜索框中输入关键词（如"关系代数"、"Paxos"、"OceanBase"）
3. 查看搜索结果中的摘要片段和来源信息
4. 点击结果或文档列表中的"查看"按钮查看完整内容

## 开发记录与实际演示

[Qoder 开发记录](QODER_DEVELOPMENT.md) · [真实搜索截图](assets/search-demo.jpg) · [Qoder Sites 展示网站](https://material-echo-oceanbase-gh24k60h61r.qoder.zone/)

## 自动化测试

### 单元测试（覆盖真实 seekdb 操作）

```bash
cd qoder

# 使用 pytest
.venv/bin/python -m pytest test_echo.py -v

# 或使用 unittest
.venv/bin/python -m unittest test_echo -v

# 或直接运行
.venv/bin/python test_echo.py
```

### 集成验证（完整生命周期 + 跨进程检查）

```bash
.venv/bin/python test_integration.py
```

集成验证覆盖：
1. 创建数据库并导入多份文档
2. 去重检测（相同内容不重复导入）
3. 全文检索（中文 ngram 分词搜索）
4. 文档列表和详情获取
5. 统计信息
6. 关闭数据库后重新打开，验证数据持久化
7. **跨进程验证**：启动子进程读取相同数据目录，验证数据在进程间可见
8. 边界条件（空查询、超大文件等）

### 测试覆盖范围

| 测试类 | 覆盖内容 |
|--------|---------|
| `TestHelperFunctions` | 哈希计算、标题提取、文件名安全处理 |
| `TestEchoDBReal` | 真实 seekdb 导入、去重、检索、列表、统计 |
| `TestPersistence` | 关闭后重新打开验证数据持久化 |
| `TestConcurrency` | **多线程并发**：导入、读取、搜索、混合操作 |
| `TestCrossProcess` | **跨进程**：一个进程写入后另一个进程读取 |
| `TestConfig` | 配置项验证 |
| `TestExamplesExist` | 示例文件存在性和可导入性 |

## 功能验证步骤

### 1. 导入验证
- [ ] 启动应用，页面正常加载
- [ ] 拖拽 `examples/数据库系统概论.md` 到上传区域
- [ ] 确认显示"成功导入 1 个文件"
- [ ] 再次导入同一文件，确认显示"重复文件已跳过"
- [ ] 确认中文文件名正确显示（不被剥掉）

### 2. 检索验证
- [ ] 在搜索框输入"关系代数"
- [ ] 确认搜索结果包含来自"数据库系统概论.md"的片段
- [ ] 确认关键词被高亮显示
- [ ] 确认显示文档来源（文件名，非服务器路径）

### 3. 来源回查验证
- [ ] 点击搜索结果条目
- [ ] 确认弹窗显示完整文档标题和来源文件名
- [ ] 确认文档内容可正常展示

### 4. 持久化验证
- [ ] 导入文件后，停止并重启应用
- [ ] 确认文档列表仍显示之前导入的文件
- [ ] 确认搜索功能仍然正常
- [ ] 确认重复文件仍被检测为 duplicate

### 5. 并发验证
- [ ] 同时打开多个浏览器标签页
- [ ] 在不同标签页同时进行搜索和导入操作
- [ ] 确认没有出现 `Packet sequence number wrong` 错误
- [ ] 确认所有请求正常返回

### 6. 移动端验证
- [ ] 使用浏览器开发者工具切换到移动设备模式
- [ ] 确认布局正常适配
- [ ] 确认上传、搜索、查看功能可用

## 项目结构

```
qoder/
├── app.py                # Flask 后端 API 服务
├── config.py             # 配置文件
├── database.py           # seekdb + PyMySQL 数据库层（线程安全）
├── requirements.txt      # Python 依赖（已实测版本）
├── test_echo.py          # 单元测试（真实 seekdb + 并发 + 跨进程）
├── test_integration.py   # 集成验证（含跨进程子进程检查）
├── static/
│   ├── index.html        # 前端页面
│   ├── style.css         # 样式表
│   └── app.js            # 前端交互逻辑（安全转义防 XSS）
├── examples/
│   ├── 数据库系统概论.md
│   ├── 分布式系统笔记.md
│   └── OceanBase简介.md
└── data/                 # 运行时生成的数据目录
    └── (seekdb 数据库文件)
```

## 设计决策

### 线程安全
Flask 默认使用多线程处理请求。为避免 PyMySQL 连接协议损坏（`Packet sequence number wrong`），采用 `threading.local()` 为每个线程维护独立连接。数据库操作失败时自动关闭当前线程连接，避免污染连接池。

### 文件名处理
浏览器上传文件时只能获得文件名。`safe_filename()` 函数：
- 保留中文字符（不使用 `secure_filename` 避免剥掉中文）
- 去除目录遍历（`os.path.basename`）
- 去除控制字符（ASCII 0-31, 127）
- 去除首尾空白和点号

### SQL 安全
所有 SQL 查询使用 PyMySQL 的参数化接口（`%s` 占位符），防止 SQL 注入。seekdb 原生 cursor 仅接受 SQL 字符串，因此通过 PyMySQL 获得参数化能力。

### 前端安全
所有用户生成的内容（文件名、文档标题、搜索关键词、服务器错误信息）在插入 innerHTML 前均通过 `escapeHtml()` 转义，防止 XSS 攻击。

### 连接生命周期
`EchoDB` 实例持有 seekdb instance 引用。每个线程通过 `_get_conn()` 获取自己的 PyMySQL 连接。`close()` 方法关闭当前线程连接和 seekdb instance。数据库操作异常时自动调用 `_close_thread_conn()` 清理。

### 去重机制
基于文档内容的 SHA-256 哈希实现去重。`content_hash` 字段设置了 UNIQUE 约束，数据库层面保证不会重复插入相同内容。

**并发去重**：多线程同时导入相同内容时，采用"先查后插 + 捕获唯一约束冲突"策略：
1. 先 SELECT 检查 content_hash 是否已存在
2. 若不存在则 INSERT
3. 若 INSERT 抛出 `IntegrityError` (error 1062, duplicate entry)，说明另一线程已抢先插入，此时重新 SELECT 获取已存在记录的 doc_id 和 title，返回 `status: "duplicate"`
4. 其他 SQL 错误不被吞掉，正常向上抛出

这保证了：多线程并发导入相同内容时，恰好一个返回 `imported`，其余返回 `duplicate`，所有响应包含相同的 doc_id 和 title。去重完全依赖数据库 UNIQUE 约束的原子性，不使用内存锁或内存数据替代。

## 已知限制

1. **单进程**：当前实现为单进程模式，不支持多进程并发写入。多线程通过线程局部连接解决，并发去重依赖数据库 UNIQUE 约束。
2. **编码**：仅支持 UTF-8 编码的文本文件。
3. **搜索质量**：搜索质量取决于 seekdb 的 ngram 全文索引实现。对于极短文档或非常见词汇，搜索结果可能不理想。
4. **数据库名**：硬编码使用 `test` 数据库。seekdb 实例创建后默认包含此数据库。
5. **连接泄漏**：长时间运行的应用应定期清理不活跃线程的连接。当前实现依赖线程结束时的垃圾回收。
6. **并发窗口**：极端高并发下，多个线程同时通过 SELECT 检查后同时尝试 INSERT，只有一个成功，其余触发 IntegrityError 后回查。这在高并发场景下可能产生少量额外的数据库往返，但保证了正确性。

## 许可证

本项目为 OceanBase 2026 竞赛参赛作品。
