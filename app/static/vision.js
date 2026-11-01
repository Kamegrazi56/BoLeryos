// Vue en direct du navigateur du bot, partagée par la page de connexion et la page Vision.
// Avec « Contrôle manuel », les clics, la molette et le clavier sont rejoués sur le navigateur du bot.
const badge = document.getElementById("badge");
const screen = document.getElementById("screen");
const page = document.getElementById("page");
const message = document.getElementById("message");
const logBox = document.getElementById("log");
const manual = document.getElementById("manual");
const LABELS = { idle: "Non connecté", logging_in: "Connexion…", logged_in: "Connecté", error: "Échec" };

let ws = null;
let manualLockUntil = 0;
function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

function render(data) {
  badge.className = "badge " + data.state;
  badge.textContent = LABELS[data.state] || data.state;
  page.textContent = data.page || "-";
  message.textContent = data.message || "";
  if (Date.now() > manualLockUntil) {  // évite que l'état du serveur décoche l'interrupteur juste après un clic
    manual.checked = !!data.manual;
  }
  screen.classList.toggle("manual", manual.checked);

  const stick = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 8;
  logBox.replaceChildren(...data.logs.map((entry) => {
    const row = document.createElement("div");
    row.className = entry.level;
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = entry.ts;
    row.append(ts, document.createTextNode(entry.msg));
    return row;
  }));
  if (stick) logBox.scrollTop = logBox.scrollHeight;
}

// Adapte le format du navigateur du bot à la zone d'affichage (pas de bandes vides).
let resizeTimer = null;
function reportViewport() {
  const r = screen.getBoundingClientRect();
  if (r.width > 200 && r.height > 150) {
    send({ t: "viewport", width: Math.round(r.width), height: Math.round(r.height) });
  }
}
new ResizeObserver(() => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(reportViewport, 400);
}).observe(screen);

// Les images arrivent en binaire (JPEG) : pas de conversion en texte, affichage immédiat.
let frameUrl = null;
function showFrame(blob) {
  let img = screen.querySelector("img");
  if (!img) {
    screen.textContent = "";
    img = document.createElement("img");
    img.alt = "Vue du navigateur du bot";
    img.draggable = false;
    screen.appendChild(img);
  }
  const previous = frameUrl;
  frameUrl = URL.createObjectURL(blob);
  img.onload = () => { if (previous) URL.revokeObjectURL(previous); };
  img.src = frameUrl;
}

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/vision`);
  ws.onopen = reportViewport;
  ws.binaryType = "blob";
  ws.onmessage = (event) => {
    if (typeof event.data === "string") render(JSON.parse(event.data));
    else showFrame(event.data);
  };
  ws.onclose = () => setTimeout(connect, 2000);
}
connect();

// ---------------------------------------------------------------- contrôle manuel
manual.addEventListener("change", () => {
  manualLockUntil = Date.now() + 800;
  screen.classList.toggle("manual", manual.checked);
  send({ t: "control", enabled: manual.checked });
  if (manual.checked) screen.focus();
});

// Position dans l'image affichée (object-fit: contain), en fraction de 0 à 1.
function pos(event) {
  const img = screen.querySelector("img");
  if (!img || !img.naturalWidth) return null;
  const r = img.getBoundingClientRect();
  const scale = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
  const w = img.naturalWidth * scale;
  const h = img.naturalHeight * scale;
  const x = (event.clientX - r.left - (r.width - w) / 2) / w;
  const y = (event.clientY - r.top - (r.height - h) / 2) / h;
  return x < 0 || x > 1 || y < 0 || y > 1 ? null : { x, y };
}
const BUTTONS = ["left", "middle", "right"];
let lastMove = 0;

screen.addEventListener("mousemove", (event) => {
  if (!manual.checked || Date.now() - lastMove < 40) return;
  const p = pos(event);
  if (p) { lastMove = Date.now(); send({ t: "move", ...p }); }
});
screen.addEventListener("mousedown", (event) => {
  if (!manual.checked) return;
  event.preventDefault();
  screen.focus();
  const p = pos(event);
  if (p) send({ t: "down", button: BUTTONS[event.button] || "left", ...p });
});
screen.addEventListener("mouseup", (event) => {
  if (!manual.checked) return;
  const p = pos(event);
  if (p) send({ t: "up", button: BUTTONS[event.button] || "left", ...p });
});
screen.addEventListener("contextmenu", (event) => { if (manual.checked) event.preventDefault(); });
screen.addEventListener("wheel", (event) => {
  if (!manual.checked) return;
  event.preventDefault();
  const p = pos(event);
  if (p) send({ t: "wheel", dx: event.deltaX, dy: event.deltaY, ...p });
}, { passive: false });

const NAMED_KEYS = new Set(["Enter", "Backspace", "Tab", "Escape", "Delete", "Home", "End", "PageUp", "PageDown",
  "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);
screen.addEventListener("keydown", (event) => {
  if (!manual.checked || ["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") return; // géré par l'évènement « paste »
  event.preventDefault();
  const shortcut = event.ctrlKey || event.metaKey || event.altKey;
  if (event.key.length === 1 && !shortcut) {
    send({ t: "type", text: event.key });
  } else if (event.key.length === 1 || NAMED_KEYS.has(event.key) || /^F\d{1,2}$/.test(event.key)) {
    const mods = (event.ctrlKey || event.metaKey ? "Control+" : "") + (event.altKey ? "Alt+" : "")
      + (event.shiftKey && event.key.length > 1 ? "Shift+" : "");
    send({ t: "press", key: mods + event.key });
  }
});
document.addEventListener("paste", (event) => {
  if (!manual.checked || document.activeElement !== screen) return;
  const text = event.clipboardData.getData("text");
  if (text) { event.preventDefault(); send({ t: "type", text: text.slice(0, 500) }); }
});

const logout = document.getElementById("logout");
if (logout) {
  logout.addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" });
    location.href = "/";
  });
}
