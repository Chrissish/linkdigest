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
const filtersEl = $("#filters");
const pickAll = $("#pick-all");
const pickAllText = $("#pick-all-text");
const bulkCount = $("#bulk-count");
const bulkDel = $("#bulk-del");
const histMsg = $("#hist-msg");
const histEmpty = $("#hist-empty");
const moreBtn = $("#more");
const totalEl = $("#h-total");

let timer = null;
let currentJob = null;
let viewedJobId = null; // 当前正在展示状态/报告的任务，删掉它时要把展示区一起清掉

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

function pill(label, value, state, title) {
  const tip = title ? ` title="${title}"` : "";
  return `<span class="pill ${state || ""}"${tip}><span class="dot"></span>${label} <b>${value}</b></span>`;
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    // 徽章报「实际会用哪个」：装了两个引擎不代表两个都在跑
    const installed = h.asr_engines.length ? h.asr_engines.join(" / ") : "未安装";
    const engine = h.asr_engine || installed;
    const asrTip = [h.asr_engine_note, `已安装：${installed}`].filter(Boolean).join("\n");
    const ck = h.cookies || {};
    const cookieText = !ck.present ? "无" : (ck.logged_in ? "已登录" : "仅访客");
    const pills = [
      pill("ffmpeg", h.ffmpeg ? "就绪" : "缺失", h.ffmpeg ? "on" : "off"),
      pill("ASR", `${engine} · ${h.asr_model}`, h.asr_engines.length ? "on" : "off", asrTip),
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
  viewedJobId = jobId;
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

/* ── 历史任务：列表 + 状态筛选 + 多选删除 ───────────────────────── */

const STATUS_LABEL = { queued: "排队", running: "进行中", done: "完成", failed: "失败" };
const FILTERS = [
  { key: "all", label: "全部" },
  { key: "active", label: "进行中" },
  { key: "done", label: "完成" },
  { key: "failed", label: "失败" },
];

// selected 的作用域是「当前筛选的这一页」：换筛选即清空，全选也只作用于可见行
const history = { status: "all", limit: 50, jobs: [], counts: {}, selected: new Set() };

function escapeHtml(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function hhmmss(sec) {
  const total = Math.round(sec);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function stamp(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const sameYear = d.getFullYear() === now.getFullYear();
  const md = `${d.getMonth() + 1}/${d.getDate()}`;
  return sameYear ? `${md} ${hm}` : `${d.getFullYear()}/${md} ${hm}`;
}

function metaBits(job) {
  const bits = [];
  if (job.platform) bits.push(job.platform);
  if (job.duration_sec) bits.push(hhmmss(job.duration_sec));
  if (job.chars) bits.push(`${job.chars >= 1000 ? (job.chars / 1000).toFixed(1) + "k" : job.chars} 字`);
  if (job.degraded) bits.push("待外部理解");
  const when = stamp(job.created_at);
  if (when) bits.push(when);
  return bits;
}

function historyMsg(text, tone) {
  histMsg.textContent = text || "";
  histMsg.className = `bulk-msg ${tone || ""}`;
  clearTimeout(historyMsg._timer);
  if (text) historyMsg._timer = setTimeout(() => { histMsg.textContent = ""; }, 6000);
}

async function loadHistory() {
  try {
    const data = await api(`/api/jobs?status=${history.status}&limit=${history.limit}`);
    history.jobs = data.jobs || [];
    history.counts = data.counts || {};
    const alive = new Set(history.jobs.map((job) => job.id));
    for (const id of [...history.selected]) {
      if (!alive.has(id)) history.selected.delete(id);
    }
    renderHistory();
  } catch (err) {
    /* 历史不重要，静默失败 */
  }
}

function renderHistory() {
  const counts = history.counts || {};
  const total = counts.all || 0;
  // 一条任务都没有时整块不出现，把版面让给新手引导
  historyBox.hidden = total === 0;
  if (total === 0) return;
  totalEl.textContent = `${total}`;

  filtersEl.innerHTML = "";
  for (const f of FILTERS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `filter${history.status === f.key ? " on" : ""}`;
    btn.innerHTML = `${f.label}<span class="n">${counts[f.key] ?? 0}</span>`;
    btn.onclick = () => {
      if (history.status === f.key) return;
      history.status = f.key;
      history.limit = 50;
      history.selected.clear();
      disarmBulk();
      historyMsg("");
      loadHistory();
    };
    filtersEl.appendChild(btn);
  }

  historyList.innerHTML = "";
  for (const job of history.jobs) historyList.appendChild(jobRow(job));
  histEmpty.hidden = history.jobs.length > 0;
  histEmpty.textContent = counts[history.status] === 0 ? "这个筛选下没有任务。" : "";
  moreBtn.hidden = history.jobs.length < history.limit || history.jobs.length >= (counts[history.status] ?? 0);
  moreBtn.textContent = `加载更多（已显示 ${history.jobs.length} / ${counts[history.status] ?? 0}）`;
  syncBulk();
}

function jobRow(job) {
  const li = document.createElement("li");
  li.className = `job ${job.status}`;
  li.dataset.id = job.id;

  const pick = document.createElement("label");
  pick.className = "pick";
  pick.title = "选中后可批量删除";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = history.selected.has(job.id);
  box.onchange = () => {
    if (box.checked) history.selected.add(job.id);
    else history.selected.delete(job.id);
    disarmBulk();
    syncBulk();
  };
  pick.appendChild(box);

  const main = document.createElement("div");
  main.className = "job-main";

  const title = document.createElement("button");
  title.type = "button";
  title.className = "job-title";
  title.textContent = job.title || job.input;
  title.title = job.input;
  title.onclick = () => openJob(job);

  const meta = document.createElement("div");
  meta.className = "job-meta";
  meta.innerHTML = [
    `<span class="state">${STATUS_LABEL[job.status] || job.status}</span>`,
    ...metaBits(job).map((bit) => `<span>${escapeHtml(bit)}</span>`),
  ].join('<i class="sep">·</i>');

  main.append(title, meta);

  const actions = document.createElement("div");
  actions.className = "job-actions";
  const open = document.createElement("button");
  open.type = "button";
  open.textContent = "打开";
  open.onclick = () => openJob(job);
  const del = document.createElement("button");
  del.type = "button";
  del.className = "del";
  del.textContent = "删除";
  if (job.status === "queued" || job.status === "running") {
    del.disabled = true;
    del.title = "任务还在跑，等它结束或失败后再删";
  } else {
    del.onclick = () => confirmThen(del, () => deleteJobs([job.id]));
  }
  actions.append(open, del);

  li.append(pick, main, actions);
  return li;
}

async function openJob(job) {
  reportEl.innerHTML = "";
  emptyBox.hidden = true;
  viewedJobId = job.id;
  renderStatus(job);
  if (job.status === "done") {
    await showReport(job);
  } else if (job.status === "queued" || job.status === "running") {
    startPolling(job.id);
  }
}

function syncBulk() {
  const shown = history.jobs.length;
  const picked = history.selected.size;
  bulkCount.textContent = picked ? `已选 ${picked} / ${shown}` : "";
  bulkDel.disabled = picked === 0;
  bulkDel.textContent = picked ? `删除所选（${picked}）` : "删除所选";
  pickAll.checked = shown > 0 && picked === shown;
  pickAll.indeterminate = picked > 0 && picked < shown;
  pickAllText.textContent = shown ? `全选当前筛选（${shown}）` : "全选";
}

// 二次确认做成按钮自身状态：一次点错不会真的删掉东西，4 秒后自动复位
const ARM_TIMEOUT = 4000;

function confirmThen(btn, action) {
  if (btn.dataset.armed === "1") {
    clearTimeout(Number(btn.dataset.timer));
    btn.dataset.armed = "";
    btn.classList.remove("armed");
    btn.textContent = btn.dataset.label || "删除";
    action();
    return;
  }
  btn.dataset.label = btn.textContent;
  btn.dataset.armed = "1";
  btn.classList.add("armed");
  btn.textContent = "确认删除？";
  btn.dataset.timer = String(setTimeout(() => {
    if (btn.dataset.armed !== "1") return;
    btn.dataset.armed = "";
    btn.classList.remove("armed");
    btn.textContent = btn.dataset.label || "删除";
  }, ARM_TIMEOUT));
}

function disarmBulk() {
  clearTimeout(Number(bulkDel.dataset.timer));
  bulkDel.dataset.armed = "";
  bulkDel.classList.remove("armed");
  bulkDel.textContent = history.selected.size ? `删除所选（${history.selected.size}）` : "删除所选";
}

async function deleteJobs(ids) {
  if (!ids.length) return;
  bulkDel.disabled = true;
  try {
    const res = await api("/api/jobs/prune", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    const removed = res.removed || [];
    const blocked = res.blocked || [];
    for (const id of removed) {
      history.selected.delete(id);
      if (viewedJobId === id) {
        stopPolling();
        currentJob = null;
        viewedJobId = null;
        reportEl.innerHTML = "";
        statusBox.hidden = true;
      }
    }
    historyMsg(
      blocked.length
        ? `已删除 ${removed.length} 个；${blocked.length} 个还在跑，先不动它们`
        : `已删除 ${removed.length} 个任务，目录在废纸篓`,
      blocked.length ? "warn" : "",
    );
    await loadHistory();
  } catch (err) {
    historyMsg(`删除失败：${err.message}`, "warn");
  } finally {
    syncBulk();
  }
}

pickAll.addEventListener("change", () => {
  if (pickAll.checked) history.jobs.forEach((job) => history.selected.add(job.id));
  else history.selected.clear();
  historyList.querySelectorAll("input[type=checkbox]").forEach((box) => {
    box.checked = history.selected.has(box.closest(".job").dataset.id);
  });
  disarmBulk();
  syncBulk();
});

bulkDel.addEventListener("click", () => confirmThen(bulkDel, () => deleteJobs([...history.selected])));

moreBtn.addEventListener("click", () => {
  history.limit += 50;
  loadHistory();
});


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
      viewedJobId = job.id;
      renderStatus(job);
      if (job.status === "done") await showReport(job);
    })
    .catch(() => {});
}

loadHealth();
loadHistory();
