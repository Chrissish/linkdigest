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
const activeBox = $("#active");
const activeList = $("#active-list");
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
const layoutEl = $("#layout");
const tabbarEl = $("#tabbar");

let timer = null;
let focusedId = null; // 完成后自动展开报告的任务；批量提交时不锁定，完成卡上点开
// 进行中的任务卡：id -> { refs, label }，轮询只盯这里面的 id
const active = new Map();

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
    // 版本号跟着后端进程报的走，不在页面里写死（写死必然和发布脱节）
    const ver = $("#ver");
    if (ver && h.version) {
      ver.textContent = `v${h.version}`;
      ver.hidden = false;
    }
    document.getElementById("offline-bar")?.remove();
    return true;
  } catch (err) {
    $("#pills").innerHTML = pill("服务", "未连接", "off");
    banner(`后端未连接 · ${err.message}`);
    return false;
  }
}

/* ── 进行中的任务：一个任务一张卡，统一轮询 ───────────────────────── */

function ensureCard(id, label) {
  activeBox.hidden = false;
  let entry = active.get(id);
  if (entry) return entry;
  const li = document.createElement("li");
  li.className = "job-card";
  li.innerHTML = `
    <div class="status-head">
      <span class="job-name"></span>
      <span class="right"><span class="stage">排队</span><span class="pct">0%</span></span>
    </div>
    <div class="bar"><i></i></div>
    <ul class="timings"></ul>`;
  const refs = {
    li,
    name: li.querySelector(".job-name"),
    stage: li.querySelector(".stage"),
    pct: li.querySelector(".pct"),
    fill: li.querySelector(".bar i"),
    timings: li.querySelector(".timings"),
  };
  if (label) refs.name.textContent = label;
  refs.name.title = label || "";
  activeList.appendChild(li);
  entry = { refs, label: label || "" };
  active.set(id, entry);
  if (!id.startsWith("__submit_")) $("#active-count").textContent = `${active.size}`;
  return entry;
}

function removeCard(id) {
  const entry = active.get(id);
  if (!entry) return;
  entry.refs.li.remove();
  active.delete(id);
  if (focusedId === id) focusedId = null;
  if (!active.size) {
    activeBox.hidden = true;
    $("#active-count").textContent = "";
  }
}

function renderCard(job) {
  const entry = ensureCard(job.id, job.title || job.input);
  const refs = entry.refs;
  // failed 状态，或 done 但带 error（重新理解失败，旧报告还在）都按失败卡渲染
  const failed = job.status === "failed" || (job.status === "done" && !!job.error);
  refs.li.classList.toggle("failed", failed);
  refs.stage.textContent = failed ? "失败" : job.status === "done" ? "完成" : STAGE_LABEL[job.stage] || job.stage;
  const pct = Math.round((job.progress || 0) * 100);
  refs.pct.textContent = failed ? "--" : `${pct}%`;
  refs.fill.style.width = `${failed ? 100 : Math.max(2, pct)}%`;

  const stages = (job.stages || []).filter((s) => s.seconds !== undefined);
  refs.timings.innerHTML = stages
    .map((s) => `<li class="done">${STAGE_LABEL[s.stage] || s.stage} ${s.seconds.toFixed(1)}s</li>`)
    .join("");

  let err = refs.li.querySelector(".err");
  if (failed) {
    if (!err) {
      err = document.createElement("div");
      err.className = "err";
      refs.li.appendChild(err);
    }
    if (err.dataset.job !== job.id + (job.error || "")) {
      err.dataset.job = job.id + (job.error || "");
      err.innerHTML = "";
      const text = document.createElement("div");
      text.textContent = job.error || "未知错误";
      err.appendChild(text);
      if (job.input_kind === "url" && job.status === "failed") {
        const retry = document.createElement("button");
        retry.className = "retry";
        retry.textContent = "重试这个链接";
        retry.onclick = () => submitUrls([job.input]);
        err.appendChild(retry);
      }
      if (job.status === "done") {
        const rerun = document.createElement("button");
        rerun.className = "retry";
        rerun.textContent = "再试一次理解";
        rerun.onclick = () => rerunUnderstand(job);
        err.appendChild(rerun);
      }
    }
  } else if (err) {
    err.remove();
  }

  if (job.status === "done" && !job.error) {
    // 完成卡留着当入口；没被 focus 的批量任务从这里点开报告
    refs.stage.textContent = "完成";
    refs.pct.textContent = "100%";
    refs.fill.style.width = "100%";
    refs.name.classList.add("link");
    refs.name.title = "点击查看报告";
    refs.name.onclick = () => openJob(job);
  }
}

// 提交失败的卡：不是任务，没 id，用完即弃
function failCard(label, text) {
  const key = `__submit_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  const entry = ensureCard(key, label);
  entry.refs.li.classList.add("failed");
  entry.refs.stage.textContent = "提交失败";
  entry.refs.pct.textContent = "--";
  entry.refs.fill.style.width = "100%";
  entry.refs.timings.innerHTML = "";
  const err = document.createElement("div");
  err.className = "err";
  err.textContent = text;
  entry.refs.li.appendChild(err);
  setTimeout(() => removeCard(key), 8000);
}

async function pollOne(jobId) {
  if (!active.has(jobId)) return;
  const job = await api(`/api/jobs/${jobId}`);
  if (!active.has(jobId)) return; // 轮询间隙被删了
  renderCard(job);
  if (job.status === "done") {
    // 重跑失败（done + error）：卡转失败样式留在页面，不展开旧报告冒充新结果
    if (job.error) {
      if (focusedId === jobId) focusedId = null;
      active.delete(jobId);
      loadHistory();
      return;
    }
    const focused = focusedId === jobId;
    if (focused) removeCard(jobId);
    if (focused) await showReport(job);
    loadHistory();
  } else if (job.status === "failed") {
    // 失败卡留在页面上带重试按钮，但不再轮询它
    active.delete(jobId);
    loadHistory();
  }
}

function pollAll() {
  for (const id of [...active.keys()]) pollOne(id).catch(() => {});
}

function ensurePolling() {
  if (timer || !active.size) return;
  timer = setInterval(() => {
    pollAll();
    if (!active.size) {
      clearInterval(timer);
      timer = null;
    }
  }, 1200);
  pollAll();
}

function reportFrame(job) {
  // 中栏直接内嵌独立报告页：一份渲染器，两处展示（含移动端）永远一致。
  // 时间码跳转、速览卡、引用块这些结构化能力只在 /html 端点有，md 转的片段全丢。
  // ?v= 带更新时间：重跑理解后浏览器不会拿旧报告。
  const frame = document.createElement("iframe");
  frame.className = "report-frame";
  frame.src = `/api/jobs/${job.id}/html?v=${encodeURIComponent(job.updated_at || job.id)}`;
  frame.title = job.title || "报告";
  frame.addEventListener("load", () => {
    try {
      const doc = frame.contentDocument;
      if (!doc || !doc.documentElement) return;
      const fit = () => {
        frame.style.height = `${doc.documentElement.scrollHeight}px`;
      };
      fit();
      new ResizeObserver(fit).observe(doc.documentElement);
    } catch {
      // 理论上同源不会走到这；真走了就让 CSS 的 min-height 兜底
    }
  });
  return frame;
}

async function showReport(job) {
  emptyBox.hidden = true;
  reportEl.innerHTML = "";
  if (job.degraded) reportEl.appendChild(handoffBanner(job));
  reportEl.appendChild(reportFrame(job));
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
  if (job.degraded) {
    const rerun = document.createElement("button");
    rerun.type = "button";
    rerun.className = "rerun-report";
    rerun.textContent = "重新理解";
    rerun.title = "这版报告是降级产物。复用转写只重跑模型，几分钟就好";
    rerun.onclick = () => rerunUnderstand(job);
    actions.prepend(rerun);
  }
  if (isMobile()) {
    ui.tab = "report";
    applyLayout();
  }
  // 只在报告顶不在视野内时才跳，用 instant：无条件 smooth 会每次都晃一下
  const top = reportEl.getBoundingClientRect().top;
  if (top < 0 || top > window.innerHeight * 0.6) {
    reportEl.scrollIntoView({ behavior: "instant", block: "start" });
  }
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
      removeCard(job.id);
      const fresh = await api(`/api/jobs/${job.id}`);
      await showReport(fresh);
      loadHistory();
    } catch (err) {
      msg.textContent = err.message;
      e.target.disabled = false;
    }
  };
  return box;
}

// 拆分批量输入：换行、分号（中英文）、逗号（中英文）、顿号都算分隔符
function splitLinks(raw) {
  return [...new Set(raw.split(/[\s;；,，、]+/).map((s) => s.trim()).filter(Boolean))];
}

// 重新理解：复用转写只跑模型，完成后自动展开报告
async function rerunUnderstand(job) {
  const label = job.title || job.input;
  focusedId = job.id;
  ensureCard(job.id, label);
  activeBox.hidden = false;
  ensurePolling();
  try {
    await api(`/api/jobs/${job.id}/understand`, { method: "POST" });
  } catch (err) {
    focusedId = null;
    removeCard(job.id);
    failCard(label, `重新理解失败：${err.message}`);
  }
}

async function submitUrls(urls) {
  reportEl.innerHTML = "";
  emptyBox.hidden = true;
  goBtn.disabled = true;
  const created = [];
  for (const url of urls) {
    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      created.push(job);
      renderCard(job);
    } catch (err) {
      // 一个链接坏了不影响其它：坏的那条自己吃一张失败卡
      failCard(url, err.message);
    }
  }
  goBtn.disabled = false;
  // 单个提交保持旧行为：完成后自动展开报告；批量则各回各的完成卡
  focusedId = created.length === 1 ? created[0].id : null;
  if (created.length) ensurePolling();
}

async function submitFile(file) {
  reportEl.innerHTML = "";
  emptyBox.hidden = true;
  goBtn.disabled = true;
  const data = new FormData();
  data.append("file", file);
  try {
    const job = await api("/api/upload", { method: "POST", body: data });
    focusedId = job.id;
    renderCard(job);
    ensurePolling();
  } catch (err) {
    failCard(file.name, err.message);
  } finally {
    goBtn.disabled = false;
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

// 任务行用真链接而不是按钮：左键仍是页内打开，右键才有「在新标签页打开 / 复制链接」。
// 已完成的任务指向单文件报告页，新标签页里就是一份能读完的独立文档。
function jobHref(job) {
  return job.status === "done"
    ? `/api/jobs/${job.id}/html`
    : `?job=${encodeURIComponent(job.id)}`;
}

// 只拦无修饰键的左键；ctrl/cmd/shift 点击走浏览器默认行为（新标签页/新窗口）
function inlineOpen(link, job) {
  link.onclick = (e) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    openJob(job);
  };
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

  const title = document.createElement("a");
  title.className = "job-title";
  title.href = jobHref(job);
  title.textContent = job.title || job.input;
  title.title = `${job.input}\n左键在当前页展开；右键可在新标签页打开、复制链接`;
  inlineOpen(title, job);

  const meta = document.createElement("div");
  meta.className = "job-meta";
  meta.innerHTML = [
    `<span class="state">${STATUS_LABEL[job.status] || job.status}</span>`,
    ...metaBits(job).map((bit) => `<span>${escapeHtml(bit)}</span>`),
  ].join('<i class="sep">·</i>');

  main.append(title, meta);

  const actions = document.createElement("div");
  actions.className = "job-actions";
  const open = document.createElement("a");
  open.className = "open";
  open.href = jobHref(job);
  open.textContent = "打开";
  open.title = "左键在当前页展开；右键可在新标签页打开";
  inlineOpen(open, job);
  actions.append(open);
  if (job.status === "failed" && job.input_kind === "url") {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "retry-job";
    retry.textContent = "重试";
    retry.title = "重新提交这个链接";
    retry.onclick = () => submitUrls([job.input]);
    actions.append(retry);
  }
  // 有转写产物的失败任务（比如理解阶段卡死被看门狗中止）：只重跑理解，不重新下载转写
  if (job.status === "failed" && job.has_transcript) {
    const rerun = document.createElement("button");
    rerun.type = "button";
    rerun.className = "retry-job";
    rerun.textContent = "重新理解";
    rerun.title = "转写已完成，只是理解失败。复用转写只重跑模型理解与报告";
    rerun.onclick = () => rerunUnderstand(job);
    actions.append(rerun);
  }
  // 降级（无密钥/调用失败）≠ 成功：给一个不重新转写、只重跑理解的入口
  if (job.status === "done" && job.degraded) {
    const rerun = document.createElement("button");
    rerun.type = "button";
    rerun.className = "retry-job";
    rerun.textContent = "重新理解";
    rerun.title = "上一轮理解降级了。复用已有转写，只重跑模型理解与报告";
    rerun.onclick = () => rerunUnderstand(job);
    actions.append(rerun);
  }
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
  if (job.status === "done") {
    await showReport(job);
    return;
  }
  if (job.status === "queued" || job.status === "running") {
    focusedId = job.id;
    renderCard(job);
    ensurePolling();
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
      removeCard(id); // 卡片和报告区一起清掉
      if (focusedId === id) {
        reportEl.innerHTML = "";
        emptyBox.hidden = false; // 回到工具指南
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
  const urls = splitLinks(urlInput.value);
  if (!urls.length) return;
  urlInput.value = "";
  submitUrls(urls);
});

fileInput.addEventListener("change", () => {
  const file = fileInput.files?.[0];
  if (file) submitFile(file);
  fileInput.value = "";
});

document.querySelectorAll(".example").forEach((btn) => {
  btn.addEventListener("click", () => {
    urlInput.value = btn.dataset.tip || "";
    if (isMobile()) {
      ui.tab = "add";
      applyLayout();
    }
    urlInput.focus();
  });
});

const deepLink = new URLSearchParams(location.search).get("job");
if (deepLink) {
  api(`/api/jobs/${deepLink}`)
    .then(async (job) => {
      if (job.status === "done") {
        await showReport(job);
      } else {
        focusedId = job.id;
        renderCard(job);
        ensurePolling();
      }
    })
    .catch(() => {});
}

/* ── 布局：左右栏收起 + 移动端 tab ───────────────────────── */

const ui = { left: true, right: true, tab: "report" };
const MQ_MOBILE = "(max-width: 680px)";

function isMobile() {
  return matchMedia(MQ_MOBILE).matches;
}

function syncTabbar() {
  tabbarEl.querySelectorAll("[data-tab]").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.tab === ui.tab);
  });
}

function applyLayout() {
  const mobile = isMobile();
  tabbarEl.hidden = !mobile;
  layoutEl.classList.toggle("mobile", mobile);
  if (mobile) {
    layoutEl.classList.remove("no-left", "no-right");
    document.body.dataset.tab = ui.tab;
    syncTabbar();
    return;
  }
  document.body.dataset.tab = "desktop";
  layoutEl.classList.toggle("no-left", !ui.left);
  layoutEl.classList.toggle("no-right", !ui.right);
  $("#toggle-left").classList.toggle("dim", !ui.left);
  $("#toggle-right").classList.toggle("dim", !ui.right);
}

// 窄屏自动收起：始终保住中栏报告区。用户手动开关会被下一次跨档覆盖，这是有意的。
function autoCollapse() {
  if (isMobile()) {
    applyLayout();
    return;
  }
  const w = innerWidth;
  ui.left = w > 900;
  ui.right = w > 1200;
  applyLayout();
}

$("#toggle-left").addEventListener("click", () => {
  ui.left = !ui.left;
  applyLayout();
});
$("#toggle-right").addEventListener("click", () => {
  ui.right = !ui.right;
  applyLayout();
});
tabbarEl.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-tab]");
  if (!btn) return;
  ui.tab = btn.dataset.tab;
  applyLayout();
});

applyLayout();
autoCollapse();
addEventListener("resize", () => autoCollapse());
loadHealth();
loadHistory();
