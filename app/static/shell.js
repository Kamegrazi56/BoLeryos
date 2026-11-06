// Éléments communs à toutes les pages : navigation, pastille d'état de connexion, petits outils d'affichage.
(() => {
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  };
  const fmt = (v) => {
    if (v === null || v === undefined) return "?";
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(3) + " G";
    if (a >= 1e6) return (v / 1e6).toFixed(3) + " M";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + " k";
    return String(Math.round(v));
  };
  const dur = (s) => {
    if (s === null || s === undefined) return "";
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    return d ? `${d}j ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${Math.round(s % 60)}s` : `${s.toFixed(0)}s`;
  };
  /** « il y a 5 min » à partir d'un horodatage Unix (secondes). */
  const ago = (t) => {
    const s = Math.max(0, Date.now() / 1000 - t);
    return s < 60 ? "À l'instant" : s < 3600 ? `Il y a ${Math.round(s / 60)} min` : s < 86400 ? `Il y a ${Math.round(s / 3600)} h` : `Il y a ${Math.round(s / 86400)} j`;
  };
  const table = (headers, rows) => {
    const t = el("table", {}, el("tr", {}, ...headers.map((h) => el("th", { textContent: h }))));
    rows.forEach((r) => t.append(el("tr", {}, ...r.map((c) => el("td", { textContent: c })))));
    return t;
  };
  /** Pastille d'aide « ? » (texte au survol ou au focus). */
  const help = (text) => {
    const node = el("span", { className: "help", tabIndex: 0, textContent: "?" });
    node.dataset.help = text;
    node.setAttribute("role", "note");
    node.setAttribute("aria-label", text);
    return node;
  };
  window.ui = { el, fmt, dur, ago, table, help };
  const LABELS = { idle: "Non connecté", logging_in: "Connexion…", logged_in: "Connecté", error: "Échec" };

  // ------------------------------------------------------------------------- navigation
  const here = location.pathname === "/" ? "/" : location.pathname.replace(/\/$/, "");
  document.querySelectorAll("header.top nav a").forEach((a) => {
    if (a.getAttribute("href") === here) a.classList.add("active");
  });

  // ------------------------------------------------------------------- état de connexion
  const power = () => document.getElementById("power");
  let botOn = false;
  let toggling = false;
  function paintPower(on, waiting) {
    botOn = on;
    const b = power();
    if (!b) return;
    b.classList.toggle("on", on);
    b.classList.toggle("off", !on);
    b.textContent = on ? "Bot : ON" : "Bot : OFF";
    b.setAttribute("aria-pressed", String(on));
    b.title = on
      ? (waiting ? `En attente : ${waiting}` : "Les fonctionnalités activées tournent. Cliquer pour arrêter (le passage en cours se termine).")
      : "Le bot est sur OFF : aucune fonctionnalité ne tourne. Cliquer pour démarrer.";
  }
  document.addEventListener("click", async (event) => {
    if (!event.target.closest("#power") || toggling) return;
    toggling = true;
    power().disabled = true;
    try {
      const res = await fetch(botOn ? "/api/bot/stop" : "/api/bot/start", { method: "POST" });
      if (res.ok) paintPower((await res.json()).bot_on, "");
    } finally {
      toggling = false;
      power().disabled = false;
      poll();
    }
  });
  function paint(status) {
    if (status.scheduler && !toggling) paintPower(status.scheduler.on, status.scheduler.waiting);
    const badge = document.getElementById("badge");
    if (badge && !document.getElementById("screen")) { // la page Écran met le badge à jour en direct
      badge.className = "badge " + status.state;
      badge.textContent = LABELS[status.state] || status.state;
    }
    window.dispatchEvent(new CustomEvent("boleryos:status", { detail: status }));
  }
  async function poll() {
    try { paint(await (await fetch("/api/status?limit=1")).json()); } catch { /* le bot redémarre */ }
  }
  poll();
  setInterval(poll, 2000);
})();
