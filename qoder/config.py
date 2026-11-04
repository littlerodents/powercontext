"""
资料回声 - 配置文件
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 数据持久化目录 - 用户可通过环境变量 DATA_DIR 自定义路径
# 默认保存在项目目录下的 data/ 子目录
DATA_DIR = os.environ.get(
    "ECHO_DATA_DIR",
    os.path.join(BASE_DIR, "data")
)

# seekdb 数据库文件路径
DB_PATH = os.path.join(DATA_DIR, "echo.db")

# 上传文件大小限制 (字节) - 默认 10MB
MAX_FILE_SIZE = int(os.environ.get("ECHO_MAX_FILE_SIZE", 10 * 1024 * 1024))

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}

# Flask 配置
SECRET_KEY = os.environ.get("ECHO_SECRET_KEY", "dev-only-not-for-production")
HOST = os.environ.get("ECHO_HOST", "127.0.0.1")
PORT = int(os.environ.get("ECHO_PORT", "5000"))

# 搜索配置
SNIPPET_LENGTH = 200  # 搜索结果摘要长度（字符）
MAX_SEARCH_RESULTS = 50
