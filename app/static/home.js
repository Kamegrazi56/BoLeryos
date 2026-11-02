// Page Accueil : phrase d'état, vérifications avant démarrage, mode, résumé de la simulation, derniers événements.
const { el, ago, table } = window.ui;
const $ = (id) => document.getElementById(id);

const PLANET_STATUS = {
  pending: "en attente de ressources", busy: "construction en cours", would_launch: "réalisable (simulation)",
  done: "objectifs atteints", idle: "rien à lancer", retry: "à réessayer",
};
const waitText = (s) => (s >= 90 ? `${Math.round(s / 60)} min` : `${s} s`);
let last = null;

function renderChecklist(steps) {
  $("checklist").replaceChildren(...steps.map((s) => {
    const cls = s.required ? (s.ok ? "ok" : "ko") : "info";
    const mark = el("span", { className: "mark", textContent: s.required ? (s.ok ? "✓" : "✗") : "i", ariaHidden: "true" });
    const li = el("li", { className: cls }, mark, el("strong", { textContent: s.label }), el("span", { className: "detail", textContent: s.detail }));
    if (s.required && !s.ok) li.append(el("a", { className: "button small", href: s.link, textContent: "Corriger" }));
    li.dataset.step = s.id;
    return li;
  }));
}

function renderMode(mode, simulatedTotal) {
  const live = mode === "live";
  $("mode-sim").setAttribute("aria-pressed", String(!live));
  $("mode-live").setAttribute("aria-pressed", String(live));
  $("mode-hint").textContent = live
    ? "Le bot clique sur « Améliorer » et « Rechercher » sur votre vrai compte, dans la limite du maximum de lancements par jour. Il n'est jamais remis en réel après un redémarrage."
    : "Le bot lit le jeu et écrit ce qu'il ferait, sans cliquer. Regardez le résumé ci-dessous : si cela vous convient, passez en réel.";
  $("go-live").hidden = live;
}

function renderSimulation(items, total, live) {
  const list = $("sim-list");
  if (!items.length) {
    list.replaceChildren(el("li", { className: "empty-state", textContent: live
      ? "Le mode réel est actif : les lancements apparaissent dans « Derniers événements »."
      : "Rien pour l'instant. Une fois le bot démarré (ON), les actions qu'il aurait faites apparaissent ici." }));
    return;
  }
  list.replaceChildren(...items.slice().reverse().map((s) => el("li", {},
    el("span", { className: "when", textContent: ago(s.t) + " :" }),
    `sur ${s.planet}, j'aurais lancé ${s.name} niveau ${s.to}`)));
}

function renderEvents(events) {
  const box = $("events");
  if (!events.length) { box.replaceChildren(el("li", { className: "empty-state", textContent: "Aucun événement pour l'instant." })); return; }
  box.replaceChildren(...events.slice().reverse().map((e) => el("li", { className: e.level },
    el("span", { className: "ts", textContent: e.ts }), e.msg)));
}

function renderPlanets(rows) {
  const box = $("planets");
  if (!rows || !rows.length) {
    box.replaceChildren(el("p", { className: "empty-state", textContent: "Aucune planète vérifiée pour l'instant." }));
    return;
  }
  box.replaceChildren(table(["Planète", "État", "Prochaine vérification"], rows.map((r) => [r.planet, PLANET_STATUS[r.status] || r.status, `dans ${waitText(r.next_in_s)}`])));
}

function render(d) {
  last = d;
  const a = d.automation;
  $("sentence").textContent = d.sentence;
  $("counters").replaceChildren(
    el("span", {}, "Lancements aujourd'hui : ", el("strong", { textContent: `${a.launched_today} / ${a.cap}` })),
    el("span", {}, "Actions simulées : ", el("strong", { textContent: String(d.simulated_total) })),
    el("span", {}, "Dernier cycle : ", el("strong", { textContent: a.last_cycle || "—" })),
  );
  renderChecklist(d.readiness.steps);
  renderMode(a.mode, d.simulated_total);
  renderSimulation(d.simulated, d.simulated_total, a.mode === "live");
  renderEvents(d.events);
  renderPlanets(a.planets_state);
}

async function refresh() {
  try { render(await (await fetch("/api/dashboard")).json()); } catch { /* le bot redémarre : on réessaie */ }
}

async function setMode(live) {
  const msg = $("mode-msg");
  if (live) {
    const none = last && last.simulated_total === 0;
    const text = "Mode RÉEL : le bot va cliquer sur « Améliorer » et « Rechercher » sur votre vrai compte et dépenser des ressources, dans la limite du maximum de lancements par jour."
      + (none ? "\n\nAttention : aucune simulation n'a encore été observée. Il est conseillé de regarder d'abord ce que le bot ferait." : "")
      + "\n\nPasser en mode réel ?";
    if (!confirm(text)) return;
  }
  const res = await fetch("/api/automation/settings", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: { mode: live ? "live" : "dry_run" }, confirm_live: live }),
  });
  const data = await res.json();
  msg.className = "msg " + (res.ok ? "ok" : "error");
  msg.textContent = res.ok ? (live ? "Mode réel activé." : "Retour en simulation.") : (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");
  refresh();
}
$("mode-sim").addEventListener("click", () => setMode(false));
$("mode-live").addEventListener("click", () => setMode(true));
$("go-live").addEventListener("click", () => setMode(true));

refresh();
setInterval(refresh, 2000);
