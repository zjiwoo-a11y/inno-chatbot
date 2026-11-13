const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const uploadBtn = document.getElementById("upload-btn");
const selectedFiles = document.getElementById("selected-files");
const form = document.getElementById("upload-form");

function updateSelected() {
  const files = fileInput.files;
  if (!files || files.length === 0) {
    selectedFiles.textContent = "";
    uploadBtn.disabled = true;
    return;
  }
  selectedFiles.textContent = "선택된 파일: " + Array.from(files).map((f) => f.name).join(", ");
  uploadBtn.disabled = false;
}

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", updateSelected);

dropzone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropzone.classList.add("drag-over");
});
dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("drag-over");
});
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("drag-over");
  if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
    fileInput.files = e.dataTransfer.files;
    updateSelected();
  }
});

form.addEventListener("submit", () => {
  uploadBtn.disabled = true;
  uploadBtn.textContent = "업로드 및 색인 중... (문서가 크면 시간이 걸려요)";
});
