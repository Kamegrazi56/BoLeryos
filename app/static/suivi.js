// Page Suivi : journal lisible, état du jeu (lecture seule), planètes suivies.
const { el, fmt, dur, table } = window.ui;
const $ = (id) => document.getElementById(id);
const TABS = ["log", "state", "planets"];

function setTab(id) {
  if (!TABS.includes(id)) id = "log";
  TABS.forEach((t) => {
    const active = t === id;
    $("tab-" + t).setAttribute("aria-selected", String(active));
    $("tab-" + t).tabIndex = active ? 0 : -1;
    $("panel-" + t).hidden = !active;
  });
  if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id);
  if (id === "log") $("log").scrollTop = $("log").scrollHeight;
}
TABS.forEach((t) => {
  $("tab-" + t).addEventListener("click", () => setTab(t));
  $("tab-" + t).addEventListener("keydown", (e) => {
    const i = TABS.indexOf(t);
    const next = e.key === "ArrowRight" ? TABS[(i + 1) % TABS.length] : e.key === "ArrowLeft" ? TABS[(i + TABS.length - 1) % TABS.length] : null;
    if (next) { setTab(next); $("tab-" + next).focus(); }
  });
});
window.addEventListener("hashchange", () => { if (TABS.includes(location.hash.slice(1))) setTab(location.hash.slice(1)); });

// ------------------------------------------------------------------------------------ journal
let logs = [];
function renderLog() {
  const details = $("log-details").checked, level = $("log-level").value;
  const rank = { info: 0, warn: 1, error: 2 };
  const shown = logs.filter((e) => (details || e.event) && (!level || (rank[e.level] ?? 0) >= rank[level]));
  const box = $("log");
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  if (!shown.length) {
    box.replaceChildren(el("div", { className: "empty-state", textContent: details ? "Journal vide." : "Aucun événement important pour l'instant. Cochez « détails techniques » pour tout voir." }));
    return;
  }
  box.replaceChildren(...shown.map((e) => el("div", { className: e.level }, el("span", { className: "ts", textContent: e.ts }), e.msg)));
  if (stick) box.scrollTop = box.scrollHeight;
}
$("log-details").addEventListener("change", renderLog);
$("log-level").addEventListener("change", renderLog);

// ------------------------------------------------------------------------- planètes suivies
const PLANET_STATUS = {
  pending: "en attente de ressources", busy: "construction en cours", would_launch: "réalisable (simulation)",
  done: "objectifs atteints", idle: "rien à lancer", retry: "à réessayer",
};
const waitText = (s) => (s >= 90 ? `${Math.round(s / 60)} min` : `${s} s`);
function renderPlanets(rows) {
  const box = $("cons-pending");
  if (!rows || !rows.length) {
    box.replaceChildren(el("p", { className: "empty-state", textContent: "Aucune planète suivie pour l'instant : le bot n'en a pas encore vérifié. Elles apparaissent ici une fois le bot démarré (ON)." }));
    return;
  }
  box.replaceChildren(table(["Planète", "État", "Détail", "Prochaine vérification"], rows.map((r) => [r.planet, PLANET_STATUS[r.status] || r.status, r.detail, `dans ${waitText(r.next_in_s)}`])));
}

async function refresh() {
  try {
    const data = await (await fetch("/api/status?limit=400")).json();
    logs = data.logs;
    renderLog();
    renderPlanets(data.automation.planets_state);
  } catch { /* le bot redémarre : on réessaie */ }
}

// ---------------------------------------------------------------------------- état du jeu
$("read-state").addEventListener("click", async () => {
  $("read-msg").textContent = "Lecture en cours…";
  const res = await fetch("/api/automation/read-state", { method: "POST" });
  const data = await res.json();
  if (!res.ok) { $("read-msg").textContent = data.detail || "Impossible de lire l'état."; return; }
  $("read-msg").textContent = "Lecture terminée.";
  const out = $("state-out");
  out.replaceChildren(
    el("h2", { textContent: `Planètes (${data.planets.length})` }),
    table(["Nom", "Coordonnées", "Fer", "Cristal", "Éther"], data.planets.map((p) => [p.name, p.coords, ...p.stocks.map(fmt)])),
  );
  for (const [key, s] of Object.entries(data.sections)) {
    const r = s.resources;
    out.append(
      el("h2", { textContent: `${s.title || key} — planète ${s.planet}` }),
      el("p", { className: "hint", textContent: `Fer ${fmt(r.fer)} · Cristal ${fmt(r.cristal)} · Éther ${fmt(r.ether)} · Énergie ${fmt(r.energie)}` }),
      table(["Nom", "Niveau", "Coût", "Durée", "Bouton"], s.cards.map((c) => [
        c.name, c.level ?? "?", Object.entries(c.costs).map(([k, v]) => `${k} ${fmt(v)}`).join(", "), dur(c.duration_s),
        c.action + (c.enabled ? "" : " (inactif)"),
      ])),
    );
  }
});

setTab(TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "log");
refresh();
setInterval(refresh, 2000);
