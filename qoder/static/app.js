/**
 * 资料回声 - 前端交互逻辑
 */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => document.querySelectorAll(sel);

  // --- DOM 元素 ---
  const uploadArea = $("#uploadArea");
  const fileInput = $("#fileInput");
  const selectBtn = $("#selectBtn");
  const importStatus = $("#importStatus");
  const searchInput = $("#searchInput");
  const searchBtn = $("#searchBtn");
  const searchResults = $("#searchResults");
  const documentList = $("#documentList");
  const emptyState = $("#emptyState");
  const docCount = $("#docCount");
  const statsInfo = $("#statsInfo");
  const docModal = $("#docModal");
  const modalBody = $("#modalBody");
  const modalClose = $("#modalClose");

  // --- 安全转义 ---
  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    const div = document.createElement("div");
    div.textContent = String(str);
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, "&quot;");
  }

  // --- 初始化 ---
  document.addEventListener("DOMContentLoaded", () => {
    initUpload();
    initSearch();
    loadDocuments();
    loadStats();
  });

  // --- 文件上传 ---
  function initUpload() {
    selectBtn.addEventListener("click", () => fileInput.click());

    uploadArea.addEventListener("click", (e) => {
      if (e.target === uploadArea || e.target.closest(".upload-icon, .upload-hint")) {
        fileInput.click();
      }
    });

    fileInput.addEventListener("change", (e) => {
      if (e.target.files.length > 0) {
        handleFiles(e.target.files);
        fileInput.value = "";
      }
    });

    uploadArea.addEventListener("dragover", (e) => {
      e.preventDefault();
      uploadArea.classList.add("dragover");
    });

    uploadArea.addEventListener("dragleave", () => {
      uploadArea.classList.remove("dragover");
    });

    uploadArea.addEventListener("drop", (e) => {
      e.preventDefault();
      uploadArea.classList.remove("dragover");
      if (e.dataTransfer.files.length > 0) {
        handleFiles(e.dataTransfer.files);
      }
    });
  }

  async function handleFiles(files) {
    const validFiles = [];
    const errors = [];

    for (const file of files) {
      const ext = file.name.split(".").pop().toLowerCase();
      if (!["txt", "md", "markdown", "text"].includes(ext)) {
        errors.push(`${file.name}: 不支持的文件类型`);
        continue;
      }
      if (file.size > 10 * 1024 * 1024) {
        errors.push(`${file.name}: 文件超过 10MB 限制`);
        continue;
      }
      validFiles.push(file);
    }

    if (validFiles.length === 0) {
      showStatus(errors.join("\n"), "error");
      return;
    }

    showStatus(`正在导入 ${validFiles.length} 个文件...`, "info");

    try {
      const formData = new FormData();
      validFiles.forEach((f) => formData.append("files", f));

      const resp = await fetch("/api/import-batch", {
        method: "POST",
        body: formData,
      });

      const data = await resp.json();

      if (!resp.ok) {
        showStatus(escapeHtml(data.error || "导入失败"), "error");
        return;
      }

      const msgs = [];
      if (data.imported > 0) msgs.push(`成功导入 ${data.imported} 个文件`);
      if (data.duplicates > 0) msgs.push(`${data.duplicates} 个重复文件已跳过`);
      if (data.errors && data.errors.length > 0) {
        msgs.push(`${data.errors.length} 个文件出错`);
      }

      showStatus(msgs.join("，"), data.imported > 0 ? "success" : "info");
      loadDocuments();
      loadStats();
    } catch (err) {
      showStatus(`网络错误: ${escapeHtml(err.message)}`, "error");
    }
  }

  function showStatus(msg, type) {
    importStatus.textContent = msg;
    importStatus.className = `import-status ${type}`;
    importStatus.hidden = false;

    if (type === "success" || type === "info") {
      setTimeout(() => {
        importStatus.hidden = true;
      }, 5000);
    }
  }

  // --- 搜索 ---
  function initSearch() {
    searchBtn.addEventListener("click", doSearch);
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") doSearch();
    });
  }

  async function doSearch() {
    const query = searchInput.value.trim();
    if (!query) {
      searchResults.innerHTML = "";
      return;
    }

    searchResults.innerHTML = '<div class="no-results"><div class="loading"></div></div>';

    try {
      const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
      const data = await resp.json();

      if (!resp.ok) {
        searchResults.innerHTML = `<div class="no-results">搜索出错: ${escapeHtml(data.error || "未知错误")}</div>`;
        return;
      }

      if (!data.results || data.results.length === 0) {
        searchResults.innerHTML = `<div class="no-results">未找到与 "${escapeHtml(query)}" 相关的内容</div>`;
        return;
      }

      renderSearchResults(data.results, query);
    } catch (err) {
      searchResults.innerHTML = `<div class="no-results">请求失败: ${escapeHtml(err.message)}</div>`;
    }
  }

  function renderSearchResults(results, query) {
    const html = results
      .map((r) => {
        const highlightedSnippet = highlightText(r.snippet || "", query);
        return `
        <div class="search-result-item" data-doc-id="${escapeAttr(r.doc_id)}">
          <div class="result-title">${escapeHtml(r.title)}</div>
          <div class="result-source">来源: ${escapeHtml(r.source)}</div>
          <div class="result-snippet">${highlightedSnippet}</div>
          ${r.score ? `<div class="result-score">相关度: ${escapeHtml(Number(r.score).toFixed(3))}</div>` : ""}
        </div>
      `;
      })
      .join("");

    searchResults.innerHTML = html;

    searchResults.querySelectorAll(".search-result-item").forEach((item) => {
      item.addEventListener("click", () => {
        showDocDetail(item.dataset.docId);
      });
    });
  }

  function highlightText(text, query) {
    if (!query) return escapeHtml(text);
    const escaped = escapeHtml(text);
    const words = query.split(/\s+/).filter(Boolean);
    let result = escaped;
    for (const word of words) {
      const regex = new RegExp(`(${escapeRegex(escapeHtml(word))})`, "gi");
      result = result.replace(regex, "<mark>$1</mark>");
    }
    return result;
  }

  function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // --- 文档列表 ---
  async function loadDocuments() {
    try {
      const resp = await fetch("/api/documents");
      const data = await resp.json();

      if (!resp.ok) {
        documentList.innerHTML = `<div class="no-results">加载失败: ${escapeHtml(data.error || "未知错误")}</div>`;
        docCount.textContent = "0";
        return;
      }

      docCount.textContent = data.total || 0;

      if (!data.documents || data.documents.length === 0) {
        emptyState.hidden = false;
        documentList.innerHTML = "";
        documentList.appendChild(emptyState);
        return;
      }

      emptyState.hidden = true;
      renderDocumentList(data.documents);
    } catch (err) {
      documentList.innerHTML = `<div class="no-results">加载失败: ${escapeHtml(err.message)}</div>`;
      docCount.textContent = "0";
    }
  }

  function renderDocumentList(docs) {
    const html = docs
      .map((doc) => {
        const date = new Date(doc.imported_at * 1000).toLocaleDateString("zh-CN");
        const size = formatBytes(doc.content_length || 0);
        return `
        <div class="doc-item" data-doc-id="${escapeAttr(doc.doc_id)}">
          <div class="doc-info">
            <div class="doc-title">${escapeHtml(doc.title)}</div>
            <div class="doc-meta">${date} · ${size}</div>
          </div>
          <div class="doc-actions">
            <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); window.__echoViewDoc('${escapeAttr(doc.doc_id)}')">查看</button>
          </div>
        </div>
      `;
      })
      .join("");

    documentList.innerHTML = html;

    documentList.querySelectorAll(".doc-item").forEach((item) => {
      item.addEventListener("click", () => {
        showDocDetail(item.dataset.docId);
      });
    });
  }

  window.__echoViewDoc = function (docId) {
    showDocDetail(docId);
  };

  // --- 文档详情 ---
  async function showDocDetail(docId) {
    modalBody.innerHTML = '<div class="no-results"><div class="loading"></div></div>';
    docModal.hidden = false;

    try {
      const resp = await fetch(`/api/documents/${encodeURIComponent(docId)}`);
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        modalBody.innerHTML = `<div class="no-results">文档不存在: ${escapeHtml(data.error || "未知错误")}</div>`;
        return;
      }

      const doc = await resp.json();
      modalBody.innerHTML = `
        <div class="modal-title">${escapeHtml(doc.title)}</div>
        <div class="modal-source">来源: ${escapeHtml(doc.source)}</div>
        <div class="modal-content-text">${escapeHtml(doc.content || "(无内容)")}</div>
      `;
    } catch (err) {
      modalBody.innerHTML = `<div class="no-results">加载失败: ${escapeHtml(err.message)}</div>`;
    }
  }

  modalClose.addEventListener("click", () => {
    docModal.hidden = true;
  });

  docModal.querySelector(".modal-backdrop").addEventListener("click", () => {
    docModal.hidden = true;
  });

  // --- 统计 ---
  async function loadStats() {
    try {
      const resp = await fetch("/api/stats");
      const data = await resp.json();

      if (!resp.ok) {
        statsInfo.innerHTML = `<span>加载失败: ${escapeHtml(data.error || "未知错误")}</span>`;
        return;
      }

      statsInfo.innerHTML = `
        <div class="stat-item">
          <div class="stat-value">${escapeHtml(String(data.document_count || 0))}</div>
          <div class="stat-label">文档数量</div>
        </div>
        <div class="stat-item">
          <div class="stat-value">${escapeHtml(formatBytes(data.total_content_bytes || 0))}</div>
          <div class="stat-label">总内容大小</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" style="font-size:0.8rem;word-break:break-all;">${escapeHtml(data.data_dir || "")}</div>
          <div class="stat-label">数据目录</div>
        </div>
      `;
    } catch (err) {
      statsInfo.innerHTML = `<span>加载统计信息失败: ${escapeHtml(err.message)}</span>`;
    }
  }

  // --- 工具函数 ---
  function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
  }
})();
