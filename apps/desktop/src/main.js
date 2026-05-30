const serviceStatus = document.querySelector("#serviceStatus");
const serviceTitle = document.querySelector("#serviceTitle");
const serviceHint = document.querySelector("#serviceHint");
const dropZone = document.querySelector("#dropZone");
const fileInput = document.querySelector("#fileInput");
const chooseFiles = document.querySelector("#chooseFiles");
const retryService = document.querySelector("#retryService");

function setServiceState(title, hint) {
  serviceStatus.textContent = title;
  serviceTitle.textContent = title;
  serviceHint.textContent = hint;
}

function showSelectedFiles(files) {
  if (!files.length) return;
  setServiceState("已选择文件", `${files.length} 个文件已加入，等待接入转换服务。`);
}

window.addEventListener("DOMContentLoaded", () => {
  setServiceState("桌面壳运行正常", "下一步会自动启动 Python 本地服务。");
});

chooseFiles.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => showSelectedFiles([...event.target.files]));
retryService.addEventListener("click", () => {
  setServiceState("正在重试连接", "服务接入完成后会自动加载转换界面。");
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
