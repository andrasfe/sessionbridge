// SessionBridge frontend. Draws remote browser frames onto a canvas and forwards
// input back. It only ever talks to this web app's own origin — never the runner.

const VIEW_W = 1280, VIEW_H = 800;
const $ = (id) => document.getElementById(id);

let jobId = null;
let ws = null;
let pollTimer = null;
let mouseDown = false;

const canvas = $("viewer");
const ctx = canvas.getContext("2d");
const img = new Image();

const TERMINAL = new Set(["completed", "failed", "stopped_by_user"]);

function setBusy(running) {
  $("start").disabled = running;
  $("stop").disabled = !running;
}

$("start").onclick = async () => {
  resetUI();
  setBusy(true);
  let job;
  try {
    const r = await fetch("/api/jobs", { method: "POST" });
    if (!r.ok) {
      let detail = await r.text();
      try { detail = JSON.parse(detail).detail || detail; } catch (_) {}
      showError(`Couldn't start the remote browser (HTTP ${r.status}). ${detail}`);
      return;
    }
    job = await r.json();
  } catch (e) {
    showError("Could not reach the server: " + e);
    return;
  }
  if (!job || !job.job_id) {
    showError("Unexpected response from server; no job was created.");
    return;
  }
  jobId = job.job_id;
  openStream();
  poll();
};

function showError(text) {
  $("state").textContent = "error";
  $("state").className = "pill failed";
  $("message").textContent = text;
  setBusy(false);            // re-enable the start button so the user can retry
  $("continue").disabled = true;
}

$("continue").onclick = async () => {
  $("continue").disabled = true;
  $("loginNotice").classList.add("hidden");
  await fetch(`/api/jobs/${jobId}/confirm-login`, { method: "POST" });
  poll();
};

$("stop").onclick = async () => {
  if (jobId) await fetch(`/api/jobs/${jobId}/stop`, { method: "POST" });
  teardown();
};

function openStream() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/${jobId}`);
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "frame") {
      img.onload = () => ctx.drawImage(img, 0, 0, VIEW_W, VIEW_H);
      img.src = "data:image/jpeg;base64," + msg.data;
    }
  };
  ws.onerror = () => { $("message").textContent = "Live view connection error."; };
  ws.onclose = () => {};
}

function sendInput(event) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "input", event }));
  }
}

// --- coordinate mapping: CSS pixels -> 1280x800 viewport ---
function toViewport(e) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((e.clientX - rect.left) / rect.width) * VIEW_W,
    y: ((e.clientY - rect.top) / rect.height) * VIEW_H,
  };
}

canvas.addEventListener("mousemove", (e) => {
  const { x, y } = toViewport(e);
  sendInput({ kind: "mouse", action: "move", x, y });
});
canvas.addEventListener("mousedown", (e) => {
  canvas.focus();
  mouseDown = true;
  const { x, y } = toViewport(e);
  sendInput({ kind: "mouse", action: "down", x, y, button: btn(e), clickCount: 1 });
});
window.addEventListener("mouseup", (e) => {
  if (!mouseDown) return;
  mouseDown = false;
  const { x, y } = toViewport(e);
  sendInput({ kind: "mouse", action: "up", x, y, button: btn(e), clickCount: 1 });
});
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const { x, y } = toViewport(e);
  sendInput({ kind: "wheel", x, y, deltaX: e.deltaX, deltaY: e.deltaY });
}, { passive: false });

function btn(e) {
  return e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
}

// keyboard (only when viewer focused)
canvas.addEventListener("keydown", (e) => {
  e.preventDefault();
  const text = e.key.length === 1 ? e.key : "";
  sendInput({ kind: "key", action: "down", key: e.key, code: e.code, text });
});
canvas.addEventListener("keyup", (e) => {
  e.preventDefault();
  sendInput({ kind: "key", action: "up", key: e.key, code: e.code });
});
canvas.addEventListener("paste", (e) => {
  e.preventDefault();
  const text = (e.clipboardData || window.clipboardData).getData("text");
  if (text) sendInput({ kind: "text", text });
});
canvas.addEventListener("contextmenu", (e) => e.preventDefault());

// --- status polling ---
function poll() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!jobId) return;
    try {
      const r = await fetch(`/api/jobs/${jobId}`);
      if (!r.ok) { showError(`Lost the job (HTTP ${r.status}).`); return; }
      const s = await r.json();
      render(s);
      if (!TERMINAL.has(s.state)) poll();
    } catch (e) {
      showError("Lost connection to the server: " + e);
    }
  }, 1000);
}

function render(s) {
  $("state").textContent = s.state;
  $("state").className = "pill " + s.state;
  $("message").textContent = s.message || "";
  $("host").textContent = s.current_host ? `host: ${s.current_host}` : "";

  const loginMode = s.state === "awaiting_user_login" || s.state === "login_in_progress";
  $("continue").disabled = !loginMode;
  $("loginNotice").classList.toggle("hidden", !loginMode);

  if (s.state === "completed" && s.artifact_id) showResult(s);
  if (s.state === "requires_user_intervention") {
    $("continue").disabled = false; // let the user take over
  }
  if (TERMINAL.has(s.state)) setBusy(false);
}

async function showResult(s) {
  const r = await fetch(`/api/jobs/${jobId}`);
  const meta = await r.json();
  $("result").classList.remove("hidden");
  $("resultMeta").innerHTML = "";
  const add = (k, v) => {
    if (v == null || v === "") return;
    const li = document.createElement("li");
    li.innerHTML = `<b>${k}:</b> ${v}`;
    $("resultMeta").appendChild(li);
  };
  add("source host", meta.current_host);
  add("state", meta.state);
  $("download").href = `/api/jobs/${jobId}/artifact`;
}

function resetUI() {
  $("result").classList.add("hidden");
  ctx.clearRect(0, 0, VIEW_W, VIEW_H);
}

function teardown() {
  if (ws) ws.close();
  ws = null;
  clearTimeout(pollTimer);
  setBusy(false);
  $("continue").disabled = true;
  $("loginNotice").classList.add("hidden");
}

// --- readiness gate: keep the Fetch button disabled until the remote browser
// runner is actually up, so the user never clicks into a dead backend. ---
async function checkReady() {
  if (jobId) return; // a job is already running; don't interfere
  let ready = false, down = "services";
  try {
    const s = await (await fetch("/api/ready")).json();
    ready = !!s.ready;
    down = Object.entries(s.services || {}).filter(([, v]) => !v).map(([k]) => k).join(", ") || down;
  } catch (_) { /* server unreachable */ }

  if (ready) {
    $("start").disabled = false;
    if ($("state").textContent === "starting…") {
      $("state").textContent = "idle";
      $("state").className = "pill";
      $("message").textContent = "";
    }
    return; // ready — stop polling
  }
  $("start").disabled = true;
  $("state").textContent = "starting…";
  $("state").className = "pill";
  $("message").textContent = `Waiting for the remote browser to be ready (${down} not ready)…`;
  setTimeout(checkReady, 2000);
}
checkReady();
