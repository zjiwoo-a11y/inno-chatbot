const form = document.getElementById("chat-form");
const textarea = document.getElementById("question");
const messagesEl = document.getElementById("messages");
const categorySelect = document.getElementById("category-select");
const categoryFilter = document.getElementById("category-filter");
const systemFilter = document.getElementById("system-filter");
const systemSelect = document.getElementById("system-select");
let systemsLoaded = false;
let mode = "rag";

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    mode = btn.dataset.mode;
    document.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b === btn));
    if (categoryFilter) categoryFilter.style.display = mode === "rag" ? "" : "none";
    if (systemFilter) systemFilter.style.display = "none";
    if (mode === "sql") { loadSystems().then(() => { if (systemSelect.options.length > 1) systemFilter.style.display = ""; }); }
    textarea.placeholder = mode === "sql" ? textarea.dataset.phSql : textarea.dataset.phRag;
  });
});

async function loadSystems() {
  if (systemsLoaded || !systemSelect) return;
  try {
    const list = await (await fetch("/api/sql/systems")).json();
    systemsLoaded = true;
    systemSelect.innerHTML = "";
    list.forEach((s) => systemSelect.add(new Option(s.name, s.id)));
    if (list.length > 1) systemFilter.style.display = "";
  } catch (e) {}
}

function renderSqlResult(div, data) {
  div.textContent = "";
  if (!data.ok && !data.sql) {
    div.textContent = "SQL을 생성할 수 없습니다: " + (data.message || "알 수 없는 오류");
    return;
  }
  const pre = document.createElement("pre");
  pre.className = "sql-block";
  pre.textContent = data.sql;
  div.appendChild(pre);

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "sql-copy";
  copyBtn.textContent = "SQL 복사";
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(data.sql);
      copyBtn.textContent = "복사됨";
    } catch (e) {
      const range = document.createRange();
      range.selectNodeContents(pre);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      copyBtn.textContent = "선택됨 — Ctrl+C";
    }
    setTimeout(() => (copyBtn.textContent = "SQL 복사"), 1800);
  });
  div.appendChild(copyBtn);

  if (!data.ok) {
    const w = document.createElement("div");
    w.className = "sql-warn";
    w.textContent = "검증 실패: " + data.message;
    div.appendChild(w);
  } else if (data.warnings && data.warnings.length) {
    const w = document.createElement("div");
    w.className = "sql-warn";
    w.textContent = "확인 필요: " + data.warnings.join(" / ");
    div.appendChild(w);
  }
}

const submitBtn = form.querySelector("button[type=submit]");

function showTyping(div, label) {
  div.textContent = "";
  const wrap = document.createElement("span");
  wrap.className = "typing";
  const dots = document.createElement("span");
  dots.className = "dots";
  for (let i = 0; i < 3; i++) dots.appendChild(document.createElement("span"));
  const text = document.createElement("span");
  text.textContent = label || "입력 중...";
  const bar = document.createElement("span");
  bar.className = "typing-bar";
  wrap.append(dots, text, bar);
  div.appendChild(wrap);
}

function setBusy(busy) {
  if (submitBtn) submitBtn.disabled = busy;
}

function addMessage(text, role) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

let busy = false;

async function askSql(question, botDiv) {
  const r = await fetch("/api/sql", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ question, system: systemSelect ? systemSelect.value : "" }),
  });
  renderSqlResult(botDiv, await r.json());
}

async function askRag(question, botDiv) {
  const params = { question };
  if (categorySelect && categorySelect.value) {
    params.category = categorySelect.value;
  }
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params),
  });
  if (!res.ok || !res.body) {
    botDiv.textContent = "오류가 발생했습니다. 다시 시도해 주세요.";
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let full = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    full += decoder.decode(value, { stream: true });
    if (full) botDiv.textContent = full;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = textarea.value.trim();
  if (!question || busy) return;

  busy = true;
  setBusy(true);
  addMessage(question, "user");
  textarea.value = "";
  const botDiv = addMessage("", "bot");
  showTyping(botDiv, mode === "sql" ? "SQL 생성 중..." : "입력 중...");
  try {
    if (mode === "sql") await askSql(question, botDiv);
    else await askRag(question, botDiv);
  } catch (err) {
    botDiv.textContent = "오류가 발생했습니다. 다시 시도해 주세요.";
  } finally {
    busy = false;
    setBusy(false);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    textarea.focus();
  }
});

textarea.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});
