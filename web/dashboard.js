/**
 * The dashboard's whole front end. Plain DOM, no framework, no build step, no CDN - it is
 * served from localhost next to the files it lists and should keep working offline.
 *
 * Two rules it does not get to break:
 *
 * 1. `decode_key` is text, always. It is a 64-bit integer, so Number() or any arithmetic
 *    on it silently rounds it (13835058055282163712 -> 13835058055282164000) and the
 *    keystream would be unrelated. It arrives as a JSON string, goes into textContent,
 *    and is never converted. Nothing here needs its value.
 * 2. Captured text - a clip's description, an author's nickname, a file name - is written
 *    with textContent or createElement, never innerHTML. It comes from a WeChat page.
 */

const POLL_BUSY = 1000;
const POLL_IDLE = 3000;
const BUSY_STATES = ["queued", "running"];

const STATE_LABEL = {
  queued: "排队中",
  running: "下载中",
  done: "完成",
  failed: "失败",
  skipped: "已存在",
};

const el = (id) => document.getElementById(id);
let timer = null;
let pending = new Set();

/** One cell, with its text set safely. */
function cell(row, text, className) {
  const td = document.createElement("td");
  if (className) td.className = className;
  td.textContent = text === null || text === undefined ? "" : String(text);
  row.appendChild(td);
  return td;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function showError(message) {
  const box = el("error");
  box.textContent = message;
  box.hidden = !message;
}

async function poll() {
  let state;
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state = await response.json();
    showError("");
  } catch (err) {
    showError(`读不到面板状态：${err.message}。wxclip serve 还在跑吗？`);
    schedule(POLL_IDLE);
    return;
  }
  render(state);
  const busy = state.jobs.some((job) => BUSY_STATES.includes(job.state));
  schedule(busy ? POLL_BUSY : POLL_IDLE);
}

function schedule(delay) {
  clearTimeout(timer);
  timer = setTimeout(poll, delay);
}

function render(state) {
  renderStatus(state);
  renderMedia(state.media);
  renderJobs(state.jobs);
  renderFiles(state.downloads);
}

function renderStatus(state) {
  const up = state.proxy.listening;
  el("proxy-dot").className = `dot ${up ? "up" : "down"}`;
  el("proxy-text").textContent = up
    ? `代理在 ${state.proxy.host}:${state.proxy.port}`
    : `代理没在跑（${state.proxy.host}:${state.proxy.port}）`;
  el("proxy-hint").hidden = up;
  el("proxy-command").textContent = state.proxy.command;

  el("media-count").textContent = state.media.length;
  el("file-count").textContent = state.downloads.filter((f) => !f.partial).length;
  el("version").textContent = `wxclip ${state.version}`;
  el("capture-dir").textContent = state.capture_dir;
  el("log-count").textContent = state.logs.length ? `（${state.logs.length} 份日志）` : "（还没有日志）";
  el("download-dir").textContent = state.download_dir;
}

function renderMedia(media) {
  const body = el("media");
  clear(body);
  el("media-empty").hidden = media.length > 0;

  media.forEach((item, index) => {
    const row = document.createElement("tr");
    cell(row, index, "num");

    const what = cell(row, "");
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = item.description || "(无说明)";
    what.appendChild(title);
    if (item.nickname) {
      const who = document.createElement("div");
      who.className = "muted";
      who.textContent = item.nickname;
      what.appendChild(who);
    }

    cell(row, item.human_size || "未知", "num");

    // Text, never a number - see the header of this file.
    const key = cell(row, item.decode_key || "-", "mono");
    if (!item.decode_key_ok) {
      const bad = document.createElement("span");
      bad.className = "badge bad";
      bad.textContent = "不是数字串";
      key.append(" ", bad);
    }

    const url = cell(row, item.url, "mono url");
    url.title = item.url;

    const actions = cell(row, "");
    if (item.downloaded) {
      const done = document.createElement("span");
      done.className = "badge";
      done.textContent = "已下载";
      actions.appendChild(done);
    }
    const button = document.createElement("button");
    button.textContent = item.downloaded ? "重新下载" : "下载";
    button.disabled = !item.decode_key_ok || pending.has(item.url);
    button.addEventListener("click", () => download(item, button));
    actions.appendChild(button);

    body.appendChild(row);
  });
}

async function download(item, button) {
  button.disabled = true;
  pending.add(item.url);
  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "content-type": "application/json" },
      // The url of a captured entry is the only thing the server accepts here; it
      // refuses anything it did not capture itself.
      body: JSON.stringify({ url: item.url, overwrite: item.downloaded }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    showError("");
  } catch (err) {
    showError(`下载没能开始：${err.message}`);
    button.disabled = false;
  } finally {
    pending.delete(item.url);
  }
  schedule(POLL_BUSY);
}

function renderJobs(jobs) {
  const body = el("jobs");
  clear(body);
  el("jobs-empty").hidden = jobs.length > 0;

  for (const job of jobs) {
    const row = document.createElement("tr");
    cell(row, job.name, "mono");

    const status = cell(row, STATE_LABEL[job.state] || job.state);
    if (job.state === "failed" && job.error) {
      status.className = "muted";
      status.textContent = `${STATE_LABEL.failed}：${job.error}`;
    }
    if (job.state === "done" && job.looks_like_mp4 === false) {
      const bad = document.createElement("span");
      bad.className = "badge bad";
      bad.textContent = "头部不是 mp4，key 可能不对";
      status.append(" ", bad);
    }

    const progress = cell(row, "");
    if (job.fraction === null) {
      progress.textContent = job.state === "running" ? "大小未知" : "";
    } else {
      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("i");
      fill.style.width = `${(job.fraction * 100).toFixed(1)}%`;
      bar.appendChild(fill);
      progress.appendChild(bar);
    }

    cell(row, job.human_downloaded, "num");
    body.appendChild(row);
  }
}

function renderFiles(files) {
  const body = el("files");
  clear(body);
  el("files-empty").hidden = files.length > 0;

  for (const file of files) {
    const row = document.createElement("tr");
    const name = cell(row, file.name, "mono");
    if (file.partial) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = "未完成，可续传";
      name.append(" ", badge);
    }
    cell(row, file.human_size, "num");
    cell(row, file.modified.replace("T", " ").replace("+00:00", " UTC"), "muted");

    const actions = cell(row, "");
    if (!file.partial) {
      const href = `/files/${encodeURIComponent(file.name)}`;
      const play = document.createElement("button");
      play.textContent = "播放";
      play.addEventListener("click", () => {
        el("player").hidden = false;
        el("video").src = href;
        el("video").scrollIntoView({ block: "nearest" });
      });
      const save = document.createElement("a");
      save.href = href;
      save.download = file.name;
      save.textContent = "另存为";
      actions.append(play, " ", save);
    }
    body.appendChild(row);
  }
}

poll();
