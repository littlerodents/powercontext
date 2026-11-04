"""
资料回声 - Flask 后端 API

提供 RESTful API：
- POST /api/import - 导入文件
- GET /api/documents - 列出所有文档
- GET /api/documents/<doc_id> - 获取文档详情
- GET /api/search?q=<query> - 全文检索
- GET /api/stats - 数据库统计
"""
import os
import traceback

from flask import Flask, request, jsonify, send_from_directory

import config
from database import EchoDB, safe_filename

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_FILE_SIZE

db = EchoDB()


def allowed_file(filename: str) -> bool:
    """检查文件扩展名是否允许"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in config.ALLOWED_EXTENSIONS


@app.route("/")
def index():
    """提供前端页面"""
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), "static"),
        "index.html"
    )


@app.route("/api/import", methods=["POST"])
def api_import():
    """导入单个文件到数据库"""
    if "file" not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400

    if not allowed_file(file.filename):
        return jsonify({
            "error": f"不支持的文件类型，仅允许: {', '.join(sorted(config.ALLOWED_EXTENSIONS))}"
        }), 400

    try:
        content = file.read().decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"error": "文件编码不是 UTF-8，无法读取"}), 400
    except Exception as e:
        return jsonify({"error": f"读取文件失败: {str(e)}"}), 400

    if len(content.strip()) == 0:
        return jsonify({"error": "文件内容为空"}), 400

    filename = safe_filename(file.filename)

    try:
        result = db.import_document(filename, content)
        return jsonify(result), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 413
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"导入失败: {str(e)}"}), 500


@app.route("/api/import-batch", methods=["POST"])
def api_import_batch():
    """批量导入文件"""
    if "files" not in request.files:
        return jsonify({"error": "未选择文件"}), 400

    files = request.files.getlist("files")
    results = {"imported": 0, "duplicates": 0, "errors": []}

    for file in files:
        if file.filename == "":
            continue
        if not allowed_file(file.filename):
            results["errors"].append({
                "file": file.filename,
                "error": "不支持的文件类型"
            })
            continue
        try:
            content = file.read().decode("utf-8")
            if len(content.strip()) == 0:
                results["errors"].append({
                    "file": file.filename,
                    "error": "文件内容为空"
                })
                continue
            filename = safe_filename(file.filename)
            result = db.import_document(filename, content)
            if result["status"] == "imported":
                results["imported"] += 1
            else:
                results["duplicates"] += 1
        except Exception as e:
            results["errors"].append({
                "file": file.filename,
                "error": str(e)
            })

    return jsonify(results)


@app.route("/api/documents")
def api_list_documents():
    """列出所有已导入文档"""
    try:
        docs = db.list_documents()
        return jsonify({"documents": docs, "total": len(docs)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"获取文档列表失败: {str(e)}"}), 500


@app.route("/api/documents/<int:doc_id>")
def api_get_document(doc_id):
    """获取单个文档详情"""
    try:
        doc = db.get_document(doc_id)
        if doc is None:
            return jsonify({"error": "文档不存在"}), 404
        return jsonify(doc)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"获取文档失败: {str(e)}"}), 500


@app.route("/api/search")
def api_search():
    """全文检索"""
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"results": [], "query": "", "total": 0})

    limit = request.args.get("limit", config.MAX_SEARCH_RESULTS, type=int)

    try:
        results = db.search(query, limit=limit)
        return jsonify({
            "results": results,
            "query": query,
            "total": len(results),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"搜索失败: {str(e)}"}), 500


@app.route("/api/stats")
def api_stats():
    """数据库统计"""
    try:
        stats = db.get_stats()
        return jsonify(stats)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"获取统计信息失败: {str(e)}"}), 500


@app.route("/static/<path:filename>")
def static_files(filename):
    """提供静态文件"""
    return send_from_directory(
        os.path.join(os.path.dirname(__file__), "static"),
        filename
    )


if __name__ == "__main__":
    print(f"资料回声启动中...")
    print(f"数据目录: {config.DATA_DIR}")
    print(f"访问: http://{config.HOST}:{config.PORT}")
    app.run(host=config.HOST, port=config.PORT, debug=False)
