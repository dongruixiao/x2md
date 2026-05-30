"""Local web UI for x2md."""
from __future__ import annotations

import os
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .converter import BackendOptions, ConversionError, ConversionResult, Resource, convert_file_result, relocate_resources
from .io_utils import write_text

Status = Literal["queued", "running", "done", "failed"]


@dataclass
class FileItem:
    id: str
    name: str
    status: Status = "queued"
    message: str = ""
    output: str | None = None
    download_url: str | None = None


@dataclass
class Job:
    id: str
    status: Status = "queued"
    message: str = ""
    files: list[FileItem] = field(default_factory=list)
    output_dir: str = ""


JOBS: dict[str, Job] = {}
DOWNLOADS: dict[str, Path] = {}
LOCK = threading.Lock()


def _backend_for_quality(quality: str) -> str:
    if quality == "fast":
        return "markitdown"
    if quality == "balanced":
        return "docling"
    if quality == "rapid":
        return "rapiddoc"
    return "mineru"


def _options(quality: str, ocr: bool, remove_watermark: bool, charts: bool, language: str) -> BackendOptions:
    mineru_method = "ocr" if ocr else "txt"
    docling_table_mode = "fast" if quality == "fast" else "accurate"
    docling_image_export_mode = "referenced" if quality == "best" or charts else None
    rapiddoc_formula = False if quality == "rapid" else None
    return BackendOptions(
        mineru_backend="pipeline",
        mineru_method=mineru_method,
        mineru_lang=language,
        mineru_image_analysis=True if charts else None,
        docling_image_export_mode=docling_image_export_mode,
        docling_ocr=True if ocr else None,
        docling_force_ocr=True if ocr else None,
        docling_ocr_lang=language,
        docling_table_mode=docling_table_mode,
        docling_enrich_picture_description=charts,
        docling_enrich_chart_extraction=charts,
        rapiddoc_lang=language if quality == "rapid" else None,
        rapiddoc_parse_method=mineru_method if quality == "rapid" else None,
        rapiddoc_formula=rapiddoc_formula,
        remove_watermark=remove_watermark,
    )


def _copy_resources(target_dir: Path, result: ConversionResult) -> None:
    for resource in result.resources:
        target = target_dir / resource.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resource.source, target)


def _cleanup(result: ConversionResult) -> None:
    for path in result.cleanup_paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def _write_conversion(target: Path, result: ConversionResult) -> None:
    result = relocate_resources(result, f"{target.stem}.assets")
    try:
        write_text(target, result.text)
        _copy_resources(target.parent, result)
    finally:
        _cleanup(result)


def _output_path(output_dir: Path, source: Path) -> Path:
    return output_dir / f"{source.stem}.md"


def _open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _set_job(job: Job) -> None:
    with LOCK:
        JOBS[job.id] = job


def _register_download(path: Path) -> str:
    token = uuid.uuid4().hex
    with LOCK:
        DOWNLOADS[token] = path
    return f"/api/download/{token}"


def _run_job(
    job: Job,
    input_dir: Path,
    quality: str,
    ocr: bool,
    remove_watermark: bool,
    charts: bool,
    language: str,
    overwrite: bool,
) -> None:
    try:
        options = _options(quality, ocr, remove_watermark, charts, language)
        backend = _backend_for_quality(quality)
        output_dir = Path(job.output_dir).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

        job.status = "running"
        job.message = "Converting"
        _set_job(job)

        for item in job.files:
            source = input_dir / item.name
            target = _output_path(output_dir, source)
            if target.exists() and not overwrite:
                item.output = str(target)
                item.download_url = _register_download(target)
                item.status = "done"
                item.message = "Skipped existing output"
                _set_job(job)
                continue

            item.status = "running"
            item.message = "Converting"
            _set_job(job)
            try:
                result = convert_file_result(source, backend, False, options)
                _write_conversion(target, result)
                item.output = str(target)
                item.download_url = _register_download(target)
                item.status = "done"
                item.message = "Done"
            except ConversionError as exc:
                item.status = "failed"
                item.message = str(exc)
                job.status = "failed"
            except Exception as exc:
                item.status = "failed"
                item.message = f"failed to convert {item.name}: {exc}"
                job.status = "failed"
            _set_job(job)

        if all(item.status == "done" for item in job.files):
            job.status = "done"
            job.message = "Done"
        elif any(item.status == "done" for item in job.files):
            job.status = "failed"
            job.message = "Some files failed"
        else:
            job.status = "failed"
            job.message = "Failed"
        _set_job(job)
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)


def create_app(api_token: str | None = None) -> FastAPI:
    app = FastAPI(title="x2md")

    @app.middleware("http")
    async def require_api_token(request: Request, call_next):
        if api_token and request.url.path.startswith("/api/"):
            supplied_token = request.headers.get("x-x2md-token") or request.query_params.get("x2md_token")
            if supplied_token != api_token:
                return JSONResponse({"detail": "invalid API token"}, status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML.replace("__X2MD_TOKEN__", api_token or "")

    @app.post("/api/jobs")
    async def create_job(
        files: list[UploadFile] = File(...),
        output_dir: str = Form(""),
        quality: str = Form("best"),
        ocr: bool = Form(False),
        remove_watermark: bool = Form(False),
        charts: bool = Form(False),
        language: str = Form("ch"),
        overwrite: bool = Form(False),
    ) -> dict[str, str]:
        if quality not in {"fast", "balanced", "rapid", "best"}:
            raise HTTPException(400, "invalid quality")
        input_dir = Path(tempfile.mkdtemp(prefix="x2md-web-input-"))
        if not output_dir.strip():
            output_path = Path.home() / "x2md-output"
        else:
            output_path = Path(output_dir).expanduser()
        job = Job(id=uuid.uuid4().hex, output_dir=str(output_path))
        for upload in files:
            name = Path(upload.filename or "document").name
            target = input_dir / name
            with target.open("wb") as f:
                while chunk := await upload.read(1024 * 1024):
                    f.write(chunk)
            job.files.append(FileItem(id=uuid.uuid4().hex, name=name))
        _set_job(job)
        thread = threading.Thread(
            target=_run_job,
            args=(job, input_dir, quality, ocr, remove_watermark, charts, language, overwrite),
            daemon=True,
        )
        thread.start()
        return {"job_id": job.id}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> Job:
        with LOCK:
            job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/api/download/{token}")
    def download(token: str) -> FileResponse:
        with LOCK:
            path = DOWNLOADS.get(token)
        if path is None or not path.exists():
            raise HTTPException(404, "file not found")
        return FileResponse(path, filename=path.name)

    @app.post("/api/open-folder/{job_id}")
    def open_folder(job_id: str) -> dict[str, bool]:
        with LOCK:
            job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        _open_folder(Path(job.output_dir))
        return {"ok": True}

    return app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(host: str = "127.0.0.1", port: int = 8765, desktop: bool = False) -> None:
    import uvicorn

    if port == 0:
        port = _free_port()
    token = uuid.uuid4().hex if desktop else None
    url = f"http://{host}:{port}"
    if host in {"127.0.0.1", "localhost"} and not desktop:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    if desktop:
        print(json.dumps({"url": url, "token": token}, ensure_ascii=False), flush=True)
    else:
        print(f"x2md web running at {url}", flush=True)
    uvicorn.run(create_app(token), host=host, port=port, log_level="warning")


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>x2md</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #d8dde6;
      --accent: #0f766e;
      --accent-weak: #e7f4f2;
      --danger: #b42318;
      --ok: #067647;
      --warn: #b54708;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 24px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 18px;
      padding: 18px;
      max-width: 1280px;
      margin: 0 auto;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .drop {
      min-height: 180px;
      display: grid;
      place-items: center;
      margin-bottom: 18px;
      border: 1.5px dashed #9aa4b2;
      background: #fbfcfd;
      text-align: center;
      cursor: pointer;
    }
    .drop.dragging {
      border-color: var(--accent);
      background: var(--accent-weak);
    }
    .drop strong { display: block; font-size: 18px; margin-bottom: 6px; }
    .drop span { color: var(--muted); }
    .queue { overflow: hidden; }
    .toolbar, .settings { padding: 16px; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 12px 14px;
      border-top: 1px solid var(--line);
      text-align: left;
      vertical-align: middle;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      background: #fbfcfd;
    }
    .name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 12px;
      background: #eef2f6;
      color: #344054;
    }
    .status.done { background: #ecfdf3; color: var(--ok); }
    .status.failed { background: #fef3f2; color: var(--danger); }
    .status.running { background: #fffaeb; color: var(--warn); }
    .dots::after {
      content: "";
      animation: dots 1.2s steps(4, end) infinite;
    }
    @keyframes dots {
      0% { content: ""; }
      25% { content: "."; }
      50% { content: ".."; }
      75%, 100% { content: "..."; }
    }
    label { display: block; margin: 0 0 6px; font-weight: 600; }
    .field { margin-bottom: 16px; }
    input[type="text"], select {
      width: 100%;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 10px;
      background: white;
      color: var(--text);
    }
    .check {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 0;
      border-top: 1px solid #eef1f5;
    }
    .check:first-of-type { border-top: 0; }
    button {
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0 12px;
      background: white;
      color: var(--text);
      cursor: pointer;
      font-weight: 600;
    }
    button.primary {
      width: 100%;
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }
    button:disabled {
      opacity: .55;
      cursor: not-allowed;
    }
    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    a { color: var(--accent); text-decoration: none; font-weight: 600; }
    .message {
      max-width: 360px;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .summary { color: var(--muted); }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      header { padding: 0 16px; }
      .hide-sm { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>x2md 文档转换工具</h1>
    <div class="summary" id="summary">等待文件</div>
  </header>
  <main>
    <section>
      <div class="drop panel" id="drop">
        <div>
          <strong>拖拽文件到这里</strong>
          <span>支持 PDF、Word、PPT、Excel、图片等文档</span>
        </div>
        <input id="fileInput" type="file" multiple hidden>
      </div>
      <div class="queue panel">
        <div class="toolbar actions">
          <button id="pick">选择文件</button>
          <button id="clear">清空队列</button>
          <button id="openFolder" disabled>打开输出目录</button>
        </div>
        <table>
          <thead>
            <tr>
              <th>文件</th>
              <th style="width:120px">状态</th>
              <th class="hide-sm">信息</th>
              <th style="width:90px">结果</th>
            </tr>
          </thead>
          <tbody id="rows">
            <tr><td colspan="4" class="summary">暂无文件</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    <aside class="panel settings">
      <div class="field">
        <label for="quality">转换质量</label>
        <select id="quality">
          <option value="balanced">标准</option>
          <option value="rapid">快速高质量</option>
          <option value="best">高质量</option>
          <option value="fast">快速</option>
        </select>
      </div>
      <div class="field">
        <label for="outputDir">输出目录</label>
        <input id="outputDir" type="text" placeholder="默认保存到用户目录下的 x2md-output">
      </div>
      <div class="field">
        <label for="language">语言</label>
        <select id="language">
          <option value="ch">中文</option>
          <option value="en">英文</option>
        </select>
      </div>
      <div class="check">
        <span>OCR</span>
        <input id="ocr" type="checkbox">
      </div>
      <div class="check">
        <span>去水印</span>
        <input id="removeWatermark" type="checkbox">
      </div>
      <div class="check">
        <span>图表识别</span>
        <input id="charts" type="checkbox">
      </div>
      <div class="check">
        <span>覆盖已有输出</span>
        <input id="overwrite" type="checkbox">
      </div>
      <div class="field" style="margin-top:18px">
        <button class="primary" id="start" disabled>开始转换</button>
      </div>
    </aside>
  </main>
  <script>
    const X2MD_TOKEN = "__X2MD_TOKEN__";
    const state = { files: [], jobId: null, job: null };
    const $ = (id) => document.getElementById(id);
    const rows = $("rows");
    const drop = $("drop");
    const fileInput = $("fileInput");

    function resetQueue() {
      state.files = [];
      state.job = null;
      state.jobId = null;
      fileInput.value = "";
    }

    function canStartFreshQueue() {
      return state.job && state.job.status !== "queued" && state.job.status !== "running";
    }

    function setFiles(list) {
      if (canStartFreshQueue()) {
        resetQueue();
      }
      state.files = [...state.files, ...Array.from(list)];
      render();
    }

    function render() {
      $("start").disabled = state.files.length === 0 || (state.job && state.job.status === "running");
      $("openFolder").disabled = !state.job || !["done", "failed"].includes(state.job.status);
      $("summary").textContent = state.job ? `${state.job.message} · ${state.job.files.length} 个文件` : `${state.files.length} 个文件`;
      if (state.job) {
        rows.innerHTML = state.job.files.map(file => `
          <tr>
            <td class="name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</td>
            <td><span class="status ${file.status}">${label(file.status)}${file.status === "running" ? '<span class="dots"></span>' : ""}</span></td>
            <td class="message hide-sm" title="${escapeHtml(file.message || "")}">${escapeHtml(file.message || "")}</td>
            <td>${file.download_url ? `<a href="${downloadUrl(file.download_url)}">下载</a>` : ""}</td>
          </tr>
        `).join("");
      } else if (state.files.length) {
        rows.innerHTML = state.files.map(file => `
          <tr>
            <td class="name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</td>
            <td><span class="status">等待</span></td>
            <td class="message hide-sm">${formatSize(file.size)}</td>
            <td></td>
          </tr>
        `).join("");
      } else {
        rows.innerHTML = '<tr><td colspan="4" class="summary">暂无文件</td></tr>';
      }
    }

    function label(status) {
      return { queued: "等待", running: "转换中", done: "完成", failed: "失败" }[status] || status;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function formatSize(bytes) {
      if (bytes < 1024) return `${bytes} B`;
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
    }

    async function start() {
      const data = new FormData();
      state.files.forEach(file => data.append("files", file));
      data.append("quality", $("quality").value);
      data.append("output_dir", $("outputDir").value);
      data.append("language", $("language").value);
      data.append("ocr", $("ocr").checked ? "true" : "false");
      data.append("remove_watermark", $("removeWatermark").checked ? "true" : "false");
      data.append("charts", $("charts").checked ? "true" : "false");
      data.append("overwrite", $("overwrite").checked ? "true" : "false");
      $("start").disabled = true;
      const response = await apiFetch("/api/jobs", { method: "POST", body: data });
      if (!response.ok) {
        alert(await response.text());
        $("start").disabled = false;
        return;
      }
      const payload = await response.json();
      state.jobId = payload.job_id;
      poll();
    }

    async function poll() {
      if (!state.jobId) return;
      const response = await apiFetch(`/api/jobs/${state.jobId}`);
      state.job = await response.json();
      render();
      if (state.job.status === "queued" || state.job.status === "running") {
        setTimeout(poll, 900);
      }
    }

    drop.addEventListener("click", () => fileInput.click());
    $("pick").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", event => setFiles(event.target.files));
    $("clear").addEventListener("click", () => {
      resetQueue();
      render();
    });
    $("start").addEventListener("click", start);
    $("openFolder").addEventListener("click", () => {
      if (state.jobId) apiFetch(`/api/open-folder/${state.jobId}`, { method: "POST" });
    });
    ["dragenter", "dragover"].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault();
      drop.classList.add("dragging");
    }));
    ["dragleave", "drop"].forEach(name => drop.addEventListener(name, event => {
      event.preventDefault();
      drop.classList.remove("dragging");
    }));
    drop.addEventListener("drop", event => setFiles(event.dataTransfer.files));

    function apiFetch(url, options = {}) {
      const headers = new Headers(options.headers || {});
      if (X2MD_TOKEN) headers.set("x-x2md-token", X2MD_TOKEN);
      return fetch(url, { ...options, headers });
    }

    function downloadUrl(url) {
      if (!X2MD_TOKEN) return url;
      const separator = url.includes("?") ? "&" : "?";
      return `${url}${separator}x2md_token=${encodeURIComponent(X2MD_TOKEN)}`;
    }

    render();
  </script>
</body>
</html>"""
