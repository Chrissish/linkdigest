const STAGE_LABEL = {
  queued: "排队",
  ingest: "解析链接",
  download: "取流",
  extract: "抽取音轨",
  asr: "语音转写",
  normalize: "校验归一",
  bundle: "落盘契约",
  understand: "分层理解",
  report: "生成报告",
  handoff: "导出交接",
  done: "完成",
};

const $ = (sel) => document.querySelector(sel);
const form = $("#form");
const urlInput = $("#url");
const goBtn = $("#go");
const fileInput = $("#file");
const statusBox = $("#status");
const stageEl = $("#stage");
const pctEl = $("#pct");
const fillEl = $("#fill");
const timingsEl = $("#timings");
const reportEl = $("#report");
const historyBox = $("#history");
const historyList = $("#history-list");
const emptyBox = $("#empty");

let timer = null;
let currentJob = null;

const OFFLINE_HINT = location.protocol === "file:"
  ? "页面是用 file:// 打开的，后端根本没在跑。先 cd linkdigest && ./scripts/run.sh，再访问 http://127.0.0.1:8787/"
  : `连不上后端 ${location.origin}。确认服务还活着：cd linkdigest && ./scripts/run.sh（默认 127.0.0.1:8787）`;

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    // fetch 只在网络层直接失败时抛 TypeError，最常见就是服务没起/打开方式不对
    throw new Error(OFFLINE_HINT);
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text.slice(0, 300) || res.statusText);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

function banner(text) {
  let el = document.getElementById("offline-bar");
  if (!el) {
    el = document.createElement("div");
    el.id = "offline-bar";
    el.style.cssText = [
      "position:sticky", "top:0", "z-index:9", "margin:0", "padding:10px 16px",
      "background:rgba(224,178,99,0.12)", "border-bottom:1px solid rgba(224,178,99,0.4)",
      "color:#e0b263", "font:12.5px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace",
      "white-space:pre-wrap",
    ].join(";");
    document.body.prepend(el);
  }
  el.textContent = text;
}

function pill(label, value, state) {
  return `<span class="pill ${state || ""}"><span class="dot"></span>${label} <b>${value}</b></span>`;
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    const engines = h.asr_engines.length ? h.asr_engines.join(" / ") : "未安装";
    const ck = h.cookies || {};
    const cookieText = !ck.present ? "无" : (ck.logged_in ? "已登录" : "仅访客");
    const pills = [
      pill("ffmpeg", h.ffmpeg ? "就绪" : "缺失", h.ffmpeg ? "on" : "off"),
      pill("ASR", `${engines} · ${h.asr_model}`, h.asr_engines.length ? "on" : "off"),
      pill("模型", h.llm.ready ? h.llm.model : "未配置（降级）", h.llm.ready ? "on" : "off"),
      pill("cookies", cookieText, ck.logged_in ? "on" : "off"),
    ];
    $("#pills").innerHTML = pills.join("");
    $("#pills").title = ck.hint || "";
    document.getElementById("offline-bar")?.remove();
    return true;
  } catch (err) {
    $("#pills").innerHTML = pill("服务", "未连接", "off");
    banner(`后端未连接 · ${err.message}`);
    return false;
  }
}

function renderStatus(job) {
  statusBox.hidden = false;
  statusBox.classList.toggle("failed", job.status === "failed");
  stageEl.textContent = job.status === "failed" ? "失败" : STAGE_LABEL[job.stage] || job.stage;
  const pct = Math.round((job.progress || 0) * 100);
  pctEl.textContent = job.status === "failed" ? "--" : `${pct}%`;
  fillEl.style.width = `${job.status === "failed" ? 100 : Math.max(2, pct)}%`;

  const stages = (job.stages || []).filter((s) => s.seconds !== undefined);
  timingsEl.innerHTML = stages
    .map((s) => `<li class="done">${STAGE_LABEL[s.stage] || s.stage} ${s.seconds.toFixed(1)}s</li>`)
    .join("");

  let err = statusBox.querySelector(".err");
  if (job.status === "failed") {
    if (!err) {
      err = document.createElement("div");
      err.className = "err";
      statusBox.appendChild(err);
    }
    if (err.dataset.job !== job.id) {
      err.dataset.job = job.id;
      err.innerHTML = "";
      const text = document.createElement("div");
      text.textContent = job.error || "未知错误";
      err.appendChild(text);
      if (job.input_kind === "url") {
        const retry = document.createElement("button");
        retry.className = "retry";
        retry.textContent = "重试这个链接";
        retry.onclick = () => submitUrl(job.input);
        err.appendChild(retry);
      }
    }
  } else if (err) {
    err.remove();
  }
}

async function poll(jobId) {
  const job = await api(`/api/jobs/${jobId}`);
  renderStatus(job);
  if (job.status === "done") {
    stopPolling();
    goBtn.disabled = false;
    await showReport(job);
    loadHistory();
    return;
  }
  if (job.status === "failed") {
    stopPolling();
    goBtn.disabled = false;
  }
}

function stopPolling() {
  if (timer) clearInterval(timer);
  timer = null;
}

function startPolling(jobId) {
  stopPolling();
  currentJob = jobId;
  timer = setInterval(() => poll(jobId).catch((err) => {
    stopPolling();
    goBtn.disabled = false;
    stageEl.textContent = "轮询失败";
  }), 1200);
  poll(jobId).catch(() => {});
}

async function showReport(job) {
  const html = await api(`/api/jobs/${job.id}/report`);
  emptyBox.hidden = true;
  reportEl.innerHTML = html;
  if (job.degraded) reportEl.prepend(handoffBanner(job));
  const actions = document.createElement("div");
  actions.className = "actions";
  actions.innerHTML = `
    <a href="/api/jobs/${job.id}/html" target="_blank" rel="noopener">打开 HTML 报告</a>
    <a href="/api/jobs/${job.id}/download/report.html">下载 HTML</a>
    <a href="/api/jobs/${job.id}/download/report.md">下载 Markdown</a>
    <a href="/api/jobs/${job.id}/download/transcript.json">下载契约 JSON</a>
    <a href="/api/jobs/${job.id}/bundle" target="_blank">查看 Bundle</a>
  `;
  reportEl.appendChild(actions);
  reportEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

function handoffBanner(job) {
  const box = document.createElement("section");
  box.className = "handoff";
  box.innerHTML = `
    <div class="handoff-head"><span class="dot"></span>理解层待外部完成</div>
    <p>本机没有模型密钥，下面这份是抽取式降级结果。转写已经落盘，把作业单交给任意一个 agent
      （这儿的会话、编辑器里的助手、浏览器对话都行），让它按格式回填答案，再点应用。</p>
    <div class="handoff-dir">${job.dir || job.id}/handoff/</div>
    <div class="handoff-actions">
      <button data-act="copy">复制作业单</button>
      <button data-act="open">查看作业单</button>
      <button data-act="apply">应用 answer.json</button>
      <span class="handoff-msg"></span>
    </div>`;
  const msg = box.querySelector(".handoff-msg");
  box.querySelector('[data-act="copy"]').onclick = async () => {
    try {
      const text = await api(`/api/jobs/${job.id}/handoff`);
      await navigator.clipboard.writeText(text);
      msg.textContent = "已复制，粘给 agent 就能干活";
    } catch (err) {
      msg.textContent = `复制失败：${err.message}`;
    }
  };
  box.querySelector('[data-act="open"]').onclick = () => {
    window.open(`/api/jobs/${job.id}/handoff`, "_blank");
  };
  box.querySelector('[data-act="apply"]').onclick = async (e) => {
    e.target.disabled = true;
    msg.textContent = "校验中…";
    try {
      await api(`/api/jobs/${job.id}/apply`, { method: "POST" });
      msg.textContent = "已应用";
      const fresh = await api(`/api/jobs/${job.id}`);
      renderStatus(fresh);
      await showReport(fresh);
      loadHistory();
    } catch (err) {
      msg.textContent = err.message;
      e.target.disabled = false;
    }
  };
  return box;
}

async function submitUrl(url) {
  reportEl.innerHTML = "";
  emptyBox.hidden = true;
  statusBox.hidden = false;
  statusBox.classList.remove("failed");
  clearError();
  stageEl.textContent = "提交中";
  fillEl.style.width = "2%";
  goBtn.disabled = true;
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    startPolling(job.id);
  } catch (err) {
    goBtn.disabled = false;
    statusBox.classList.add("failed");
    stageEl.textContent = "提交失败";
    showError(err.message);
  }
}

function clearError() {
  statusBox.querySelectorAll(".err").forEach((n) => n.remove());
}

function showError(text) {
  clearError();
  const div = document.createElement("div");
  div.className = "err";
  div.textContent = text;
  statusBox.appendChild(div);
}

async function submitFile(file) {
  reportEl.innerHTML = "";
  emptyBox.hidden = true;
  statusBox.hidden = false;
  statusBox.classList.remove("failed");
  clearError();
  stageEl.textContent = "上传中";
  goBtn.disabled = true;
  const data = new FormData();
  data.append("file", file);
  try {
    const job = await api("/api/upload", { method: "POST", body: data });
    startPolling(job.id);
  } catch (err) {
    goBtn.disabled = false;
    statusBox.classList.add("failed");
    stageEl.textContent = "上传失败";
    showError(err.message);
  }
}

async function loadHistory() {
  try {
    const jobs = await api("/api/jobs?limit=12");
    const done = jobs.filter((j) => j.status === "done" || j.status === "failed");
    if (!done.length) return;
    historyBox.hidden = false;
    historyList.innerHTML = "";
    for (const job of done) {
      const chip = document.createElement("button");
      chip.className = `chip ${job.status === "failed" ? "miss" : ""}`;
      const stamp = (job.created_at || "").slice(11, 16);
      const flag = job.degraded ? " · 待理解" : "";
      chip.textContent = `${job.title || job.input}${stamp ? ` · ${stamp}` : ""}${flag}`;
      chip.title = `${job.dir || job.id}\n${job.input}`;
      chip.addEventListener("click", async () => {
        reportEl.innerHTML = "";
        renderStatus(job);
        if (job.status === "done") await showReport(job);
      });
      historyList.appendChild(chip);
    }
  } catch (err) {
    /* 历史不重要，静默失败 */
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;
  submitUrl(url);
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) submitFile(file);
  fileInput.value = "";
});

document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => {
    urlInput.value = btn.dataset.tip || "";
    urlInput.focus();
  });
});

const deepLink = new URLSearchParams(location.search).get("job");
if (deepLink) {
  api(`/api/jobs/${deepLink}`)
    .then(async (job) => {
      renderStatus(job);
      if (job.status === "done") await showReport(job);
    })
    .catch(() => {});
}

loadHealth();
loadHistory();
