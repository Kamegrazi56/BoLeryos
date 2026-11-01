const badge = document.getElementById("badge");
const screen = document.getElementById("screen");
const page = document.getElementById("page");
const message = document.getElementById("message");
const logBox = document.getElementById("log");
const LABELS = { idle: "Non connecté", logging_in: "Connexion…", logged_in: "Connecté", error: "Échec" };

function render(data) {
  badge.className = "badge " + data.state;
  badge.textContent = LABELS[data.state] || data.state;
  page.textContent = data.page || "-";
  message.textContent = data.message || "";

  // `image` n'est présent que lorsque la capture a changé ; sinon on garde l'image affichée.
  if ("image" in data) {
    if (data.image) {
      let img = screen.querySelector("img");
      if (!img) {
        screen.textContent = "";
        img = document.createElement("img");
        img.alt = "Vue du navigateur du bot";
        screen.appendChild(img);
      }
      img.src = "data:image/jpeg;base64," + data.image;
    } else {
      screen.replaceChildren(Object.assign(document.createElement("span"), {
        textContent: "Démarrage du navigateur du bot…",
      }));
    }
  }

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

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/vision`);
  ws.onmessage = (event) => render(JSON.parse(event.data));
  ws.onclose = () => setTimeout(connect, 2000);
}
connect();

const logout = document.getElementById("logout");
if (logout) {
  logout.addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" });
    location.href = "/";
  });
}
