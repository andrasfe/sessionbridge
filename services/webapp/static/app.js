// SessionBridge agent UI. Chat drives a vision agent that controls an isolated
// remote browser; the live viewer streams it and you can grab control any time.

const VIEW_W = 1280, VIEW_H = 800;
const $ = (id) => document.getElementById(id);

let jobId = null;
let ws = null;
let mouseDown = false;
let busy = false;

const canvas = $("viewer");
const ctx = canvas.getContext("2d");
const img = new Image();

// ---------------------------------------------------------------- chat log
function addMsg(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  $("log").appendChild(div);
  $("log").scrollTop = $("log").scrollHeight;
  return div;
}

// ---------------------------------------------------------------- bootstrap
async function init() {
  setState("starting…", "");
  // wait for backend services
  for (let i = 0; i < 60; i++) {
    try {
      const r = await (await fetch("/api/ready")).json();
      if (r.ready) break;
    } catch (_) {}
    await sleep(1000);
  }
  // start an isolated browser session
  try {
    const r = await fetch("/api/jobs", { method: "POST" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    jobId = (await r.json()).job_id;
  } catch (e) {
    setState("error", "");
    addMsg("system", "Could not start a browser session: " + e);
    return;
  }
  openStream();
  setState("ready", "");
  $("task").disabled = false;
  $("send").disabled = false;
  $("task").focus();
  addMsg("system", "Browser ready. Tell me what to do.");
}

$("chatform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const task = $("task").value.trim();
  if (!task || busy || !jobId) return;
  addMsg("you", task);
  $("task").value = "";
  setBusy(true);
  const thinking = addMsg("system", "working…");
  try {
    const r = await fetch(`/api/jobs/${jobId}/agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task }),
    });
    const data = await r.json();
    thinking.remove();
    renderResult(data);
  } catch (err) {
    thinking.remove();
    addMsg("system", "Agent error: " + err);
  }
  setBusy(false);
});

// Enter to send, Shift+Enter for newline.
$("task").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("chatform").requestSubmit();
  }
});

function renderResult(data) {
  for (const s of data.steps || []) {
    const bits = [s.action, s.detail].filter(Boolean).join(" ");
    addMsg("step", `· ${bits}${s.thought ? " — " + s.thought : ""}`);
  }
  if (data.state === "completed" && data.answer) {
    addMsg("agent", data.answer);
  } else if (data.message) {
    addMsg("agent", data.message);
  } else {
    addMsg("agent", "(no answer)");
  }
}

function setBusy(b) {
  busy = b;
  $("send").disabled = b;
  $("send").textContent = b ? "…" : "Send";
}
function setState(s, host) {
  $("state").textContent = s;
  $("state").className = "pill " + (s === "error" ? "failed" : "");
  $("host").textContent = host ? `host: ${host}` : "";
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- viewer
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
}

function sendInput(event) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "input", event }));
  }
}

function toViewport(e) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((e.clientX - rect.left) / rect.width) * VIEW_W,
    y: ((e.clientY - rect.top) / rect.height) * VIEW_H,
  };
}

canvas.addEventListener("mousemove", (e) => {
  const { x, y } = toViewport(e);
  sendInput({ kind: "mouse", action: "move", x, y, buttons: mouseDown ? 1 : 0 });
});
canvas.addEventListener("mousedown", (e) => {
  canvas.focus();
  mouseDown = true;
  const { x, y } = toViewport(e);
  sendInput({ kind: "mouse", action: "down", x, y, button: btn(e), buttons: 1, clickCount: 1 });
});
window.addEventListener("mouseup", (e) => {
  if (!mouseDown) return;
  mouseDown = false;
  const { x, y } = toViewport(e);
  sendInput({ kind: "mouse", action: "up", x, y, button: btn(e), buttons: 0, clickCount: 1 });
});
canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const { x, y } = toViewport(e);
  sendInput({ kind: "wheel", x, y, deltaX: e.deltaX, deltaY: e.deltaY });
}, { passive: false });
function btn(e) { return e.button === 2 ? "right" : e.button === 1 ? "middle" : "left"; }

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

init();
