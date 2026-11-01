const form = document.getElementById("login-form");
const submit = document.getElementById("submit");
const msg = document.getElementById("msg");
const badge = document.getElementById("badge");

const LABELS = { idle: "Non connecté", logging_in: "Connexion…", logged_in: "Connecté", error: "Échec" };

function show(state, message) {
  badge.className = "badge " + state;
  badge.textContent = LABELS[state] || state;
  msg.className = "msg " + (state === "error" ? "error" : state === "logged_in" ? "ok" : "");
  msg.textContent = message || "";
}

// Si une session est déjà ouverte, on va directement sur la vision.
fetch("/api/status").then((r) => r.json()).then((s) => {
  if (s.state === "logged_in") location.href = "/vision";
}).catch(() => {});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submit.disabled = true;
  show("logging_in", "Connexion en cours, cela peut prendre une trentaine de secondes…");
  const body = JSON.stringify({
    identifier: form.identifier.value.trim(),
    password: form.password.value,
  });
  try {
    const res = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body });
    const data = await res.json();
    if (!res.ok) {
      show("error", data.detail ? String(data.detail) : "Requête refusée.");
    } else {
      show(data.state, data.message);
      if (data.state === "logged_in") {
        form.password.value = "";
        setTimeout(() => { location.href = "/vision"; }, 600);
      }
    }
  } catch {
    show("error", "Impossible de joindre le bot.");
  } finally {
    submit.disabled = false;
    form.password.value = "";
  }
});
