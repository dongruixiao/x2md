import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

const serviceBadge = document.querySelector("#serviceBadge");
const serviceStatus = document.querySelector("#serviceStatus");
const serviceTitle = document.querySelector("#serviceTitle");
const serviceHint = document.querySelector("#serviceHint");
const serviceCommand = document.querySelector("#serviceCommand");
const commandBox = document.querySelector("#commandBox");
const logPanel = document.querySelector("#logPanel");
const serviceLogs = document.querySelector("#serviceLogs");
const dropZone = document.querySelector("#dropZone");
const fileInput = document.querySelector("#fileInput");
const chooseFiles = document.querySelector("#chooseFiles");
const retryService = document.querySelector("#retryService");
const clearLogs = document.querySelector("#clearLogs");

const logs = [];

function setServiceState(state, title, hint) {
  serviceBadge.dataset.state = state;
  serviceStatus.textContent = title;
  serviceTitle.textContent = title;
  serviceHint.textContent = hint;
}

function renderLogs() {
  serviceLogs.textContent = logs.join("\n");
  logPanel.hidden = logs.length === 0;
}

function appendLog(message) {
  if (!message) return;
  logs.push(message);
  if (logs.length > 80) logs.shift();
  renderLogs();
}

function showSelectedFiles(files) {
  if (!files.length) return;
  setServiceState("starting", "已选择文件", `${files.length} 个文件已加入，等待接入转换服务。`);
}

async function bindServiceEvents() {
  await listen("x2md-service-starting", (event) => {
    const { python, args } = event.payload;
    const command = [python, ...args].join(" ");
    commandBox.hidden = false;
    serviceCommand.textContent = command;
    logs.length = 0;
    renderLogs();
    setServiceState("starting", "正在启动服务", "正在启动本机 x2md 服务，成功后会自动进入转换界面。");
  });

  await listen("x2md-service-log", (event) => {
    appendLog(event.payload.message);
  });

  await listen("x2md-service-ready", (event) => {
    setServiceState("ready", "服务已就绪", `已连接 ${event.payload}，正在打开转换界面。`);
  });

  await listen("x2md-service-error", (event) => {
    const payload = event.payload;
    const message = typeof payload === "string" ? payload : payload.message;
    const errorLogs = Array.isArray(payload?.logs) ? payload.logs : [];
    errorLogs.forEach(appendLog);
    setServiceState("error", "服务启动失败", message || "本机 x2md 服务没有成功启动。");
  });
}

window.addEventListener("DOMContentLoaded", async () => {
  setServiceState("starting", "桌面壳运行正常", "正在等待 Python 本地服务启动。");
  try {
    await bindServiceEvents();
    await invoke("start_x2md_service");
  } catch (error) {
    setServiceState("error", "桌面事件不可用", String(error));
  }
});

chooseFiles.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => showSelectedFiles([...event.target.files]));
retryService.addEventListener("click", async () => {
  setServiceState("starting", "正在重试连接", "服务接入完成后会自动加载转换界面。");
  try {
    await invoke("restart_x2md_service");
  } catch (error) {
    setServiceState("error", "重试失败", String(error));
  }
});
clearLogs.addEventListener("click", () => {
  logs.length = 0;
  renderLogs();
});

["dragenter", "dragover"].forEach((name) => {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
});

["dragleave", "drop"].forEach((name) => {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
});

dropZone.addEventListener("drop", (event) => {
  showSelectedFiles([...event.dataTransfer.files]);
});
