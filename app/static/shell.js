// Éléments communs à toutes les pages : navigation, interrupteur Bot ON/OFF, bandeau de mode (simulation / réel),
// vérification « Prêt à démarrer ? » avant de lancer le bot, alertes du navigateur.
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
  const buttons = () => [...document.querySelectorAll("#power, [data-power]")];
  let running = false;
  let busy = false;

  // ------------------------------------------------------------------------- navigation
  const here = location.pathname === "/" ? "/" : location.pathname.replace(/\/$/, "");
  document.querySelectorAll("header.top nav a").forEach((a) => {
    if (a.getAttribute("href") === here) a.classList.add("active");
  });

  // -------------------------------------------------------------------- bandeau de mode
  const banner = el("div", { id: "mode-banner", className: "mode-banner", hidden: true, role: "status" });
  const header = document.querySelector("header.top");
  if (header) header.after(banner);
  function paintBanner(mode) {
    banner.hidden = false;
    banner.className = "mode-banner " + (mode === "live" ? "live" : "sim");
    banner.replaceChildren(
      el("strong", { textContent: mode === "live" ? "MODE RÉEL" : "SIMULATION" }),
      mode === "live"
        ? " — le bot clique sur « Améliorer » et « Rechercher » et dépense vos ressources."
        : " — le bot lit le jeu et dit ce qu'il ferait, mais ne clique pas.",
      here === "/" ? "" : el("a", { href: "/#mode", textContent: "Changer de mode" }),
    );
  }

  // ----------------------------------------------------------------- état du bot (ON/OFF)
  function paint(status) {
    const a = status.automation;
    running = !!a.running;
    buttons().forEach((b) => {
      b.classList.toggle("on", running);
      b.classList.toggle("off", !running);
      b.textContent = running ? "Bot : ON" : "Bot : OFF";
      b.setAttribute("aria-pressed", String(running));
      b.title = running
        ? (a.waiting ? `En attente : ${a.waiting}` : "Le bot est actif. Cliquer pour l'arrêter (OFF = arrêt d'urgence).")
        : "Le bot est arrêté. Cliquer pour l'activer : il se connecte si besoin, entre dans l'univers choisi, puis travaille.";
    });
    const badge = document.getElementById("badge");
    if (badge && !document.getElementById("screen")) { // la page Écran met le badge à jour en direct
      badge.className = "badge " + status.state;
      badge.textContent = LABELS[status.state] || status.state;
    }
    paintBanner(a.mode);
    window.dispatchEvent(new CustomEvent("boleryos:status", { detail: status }));
  }
  async function poll() {
    try { paint(await (await fetch("/api/status?limit=1")).json()); } catch { /* le bot redémarre */ }
  }

  // ----------------------------------------- « Prêt à démarrer ? » avant d'allumer le bot
  function askAboutMissing(missing) {
    return new Promise((resolve) => {
      const dialog = el("dialog", { className: "modal" });
      const done = (value) => { dialog.close(); dialog.remove(); resolve(value); };
      dialog.append(
        el("h2", { textContent: "Il manque quelques réglages" }),
        el("p", { className: "hint", textContent: "Sans eux, le bot ne pourra pas travailler tout seul :" }),
        el("ul", { className: "missing" }, ...missing.map((m) => el("li", {},
          el("strong", { textContent: m.label }), el("span", { textContent: ` — ${m.detail}` }),
          el("a", { className: "button secondary small", href: m.link, textContent: "Corriger" })))),
        el("div", { className: "row" },
          Object.assign(el("button", { type: "button", className: "secondary", textContent: "Annuler" }), { onclick: () => done(false) }),
          Object.assign(el("button", { type: "button", textContent: "Démarrer quand même" }), { onclick: () => done(true) })),
      );
      dialog.addEventListener("cancel", () => done(false));
      document.body.append(dialog);
      dialog.showModal();
    });
  }
  async function toggle() {
    if (busy) return;
    busy = true;
    buttons().forEach((b) => (b.disabled = true));
    try {
      if (running) {
        await fetch("/api/automation/stop", { method: "POST" });
      } else {
        if (window.beforePowerOn) await window.beforePowerOn(); // la page Réglages enregistre d'abord ses réglages
        const missing = (await (await fetch("/api/dashboard")).json()).readiness.steps.filter((s) => s.required && !s.ok);
        if (missing.length && !(await askAboutMissing(missing))) return;
        await fetch("/api/automation/start", { method: "POST" });
      }
    } finally {
      busy = false;
      buttons().forEach((b) => (b.disabled = false));
      poll();
    }
  }
  document.addEventListener("click", (event) => {
    if (event.target.closest("#power, [data-power]")) toggle();
  });

  // ------------------------------------------------------------ alertes dans le navigateur
  let lastEvent = Date.now() / 1000;
  async function pollAlerts() {
    let enabled = false;
    try { enabled = localStorage.getItem("boleryos.alerts") === "1"; } catch { /* stockage indisponible */ }
    if (!enabled || !("Notification" in window) || Notification.permission !== "granted") return;
    try {
      const events = await (await fetch(`/api/events?after=${lastEvent}`)).json();
      events.forEach((e) => {
        lastEvent = Math.max(lastEvent, e.t);
        if (e.kind !== "info") new Notification(e.kind === "launch" ? "BoLeryos : amélioration lancée" : "BoLeryos : attention", { body: e.msg });
      });
    } catch { /* idem */ }
  }

  poll();
  setInterval(poll, 2000);
  setInterval(pollAlerts, 5000);
})();
