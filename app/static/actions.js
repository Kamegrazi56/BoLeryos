// Page Actions : onglets, réglages de l'automatisation (enregistrés côté serveur), état du jeu et journal.
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};
const DAYS = ["L", "M", "M", "J", "V", "S", "D"];
const TABS = ["run", "schedule", "constructions", "research", "state", "log", "settings"];

let model = null; // réglages en cours d'édition (source de vérité de l'interface)
let catalog = { buildings: [], research: [], planets: [] };
let confirmedLive = false;
let saveTimer = null;
let overridePlanet = "";
let catalogKey = "";

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

// ---------------------------------------------------------------------------------- onglets
function setTab(id) {
  if (!TABS.includes(id)) id = "run";
  TABS.forEach((t) => {
    const active = t === id;
    $("tab-" + t).setAttribute("aria-selected", String(active));
    $("tab-" + t).tabIndex = active ? 0 : -1;
    $("panel-" + t).hidden = !active;
  });
  try { localStorage.setItem("boleryos.actions.tab", id); } catch { /* stockage indisponible : sans effet */ }
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
function initialTab() {
  const fromHash = location.hash.slice(1);
  if (TABS.includes(fromHash)) return fromHash;
  try { return localStorage.getItem("boleryos.actions.tab") || "run"; } catch { return "run"; }
}

// -------------------------------------------------------------------------- enregistrement
function say(text, ok) {
  const msg = $("save-msg");
  msg.textContent = text;
  msg.className = "msg " + (ok ? "ok" : "error");
}
async function save() {
  const body = JSON.parse(JSON.stringify(model));
  delete body.catalog; // entretenu par le bot
  for (const [planet, goals] of Object.entries(body.constructions.overrides)) {
    if (!Object.keys(goals).length) delete body.constructions.overrides[planet]; // exception vide : retirée
  }
  const res = await fetch("/api/automation/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: body, confirm_live: confirmedLive }),
  });
  const data = await res.json();
  if (!res.ok) {
    return say(Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Réglages refusés.", false);
  }
  catalog = data.catalog;
  say("Réglages enregistrés.", true);
}
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 500);
}

// ---------------------------------------------------------------- listes de niveaux demandés
function currentLevel(entry) {
  if (entry.level_min === null || entry.level_min === undefined) return "niveau actuel inconnu";
  return entry.level_min === entry.level_max ? `actuel : ${entry.level_min}` : `actuel : ${entry.level_min} à ${entry.level_max}`;
}
/** Une ligne par élément du jeu (liste intégrée au bot) : nom, niveau actuel, champ « niveau demandé ». */
function renderGoals(container, entries, targets, placeholders = {}) {
  container.replaceChildren();
  let group = null;
  entries.forEach((entry) => {
    if (entry.group !== group) {
      group = entry.group;
      container.append(el("h3", { textContent: group }));
    }
    const input = el("input", { type: "number", min: 0, max: 999, placeholder: placeholders[entry.name] ?? "—" });
    input.value = targets[entry.name] ?? "";
    input.dataset.goal = entry.name;
    input.addEventListener("input", () => {
      if (input.value === "") delete targets[entry.name];
      else targets[entry.name] = Math.max(0, Math.min(999, Number(input.value)));
      scheduleSave();
    });
    container.append(el("div", { className: "goal-row" },
      el("span", { className: "goal-name", textContent: entry.name }),
      el("span", { className: "goal-current", textContent: currentLevel(entry) }),
      input,
    ));
  });
}
function refreshLevels() {
  for (const [kind, ids] of [["buildings", ["cons-goals", "ov-goals"]], ["research", ["res-goals"]]]) {
    const byName = Object.fromEntries(catalog[kind].map((e) => [e.name, e]));
    ids.forEach((id) => $(id).querySelectorAll(".goal-row").forEach((row) => {
      const entry = byName[row.querySelector("input").dataset.goal];
      if (entry) row.querySelector(".goal-current").textContent = currentLevel(entry);
    }));
  }
}

// ------------------------------------------------------------------------------ planètes
function renderPlanetControls() {
  const planets = [...new Set([...catalog.planets, ...model.constructions.planets, ...Object.keys(model.constructions.overrides)])];
  const box = $("cons-planets");
  box.replaceChildren();
  const all = el("input", { type: "checkbox", checked: model.constructions.planets.length === 0 });
  all.addEventListener("change", () => {
    model.constructions.planets = all.checked ? [] : [...planets];
    renderPlanetControls();
    scheduleSave();
  });
  box.append(el("label", { className: "switch" }, all, "Toutes les planètes"));
  if (!planets.length) {
    box.append(el("p", { className: "hint", textContent: "La liste des planètes apparaît après « Lire l'état du jeu » (onglet État du jeu)." }));
  } else if (!all.checked) {
    planets.forEach((name) => {
      const cb = el("input", { type: "checkbox", checked: model.constructions.planets.includes(name) });
      cb.addEventListener("change", () => {
        const set = new Set(model.constructions.planets);
        cb.checked ? set.add(name) : set.delete(name);
        model.constructions.planets = [...set];
        scheduleSave();
      });
      box.append(el("label", { className: "switch planet" }, cb, name));
    });
  }
  // exceptions par planète
  const sel = $("ov-planet");
  sel.replaceChildren(el("option", { value: "", textContent: planets.length ? "Choisir une planète…" : "Aucune planète connue" }),
    ...planets.map((n) => el("option", { value: n, textContent: n })));
  if (!planets.includes(overridePlanet)) overridePlanet = "";
  sel.value = overridePlanet;
  renderOverrides();
  // planète qui finance les recherches
  const fund = $("res-planet");
  const names = [...new Set([...catalog.planets, model.research.funding_planet].filter(Boolean))];
  fund.replaceChildren(el("option", { value: "", textContent: "Automatique (la planète qui a le plus de Fer)" }),
    ...names.map((n) => el("option", { value: n, textContent: n })));
  fund.value = model.research.funding_planet;
}
function renderOverrides() {
  const container = $("ov-goals");
  if (!overridePlanet) return container.replaceChildren();
  const current = (model.constructions.overrides[overridePlanet] ||= {});
  renderGoals(container, catalog.buildings, current, model.constructions.targets);
}
$("ov-planet").addEventListener("change", () => { overridePlanet = $("ov-planet").value; renderOverrides(); });
$("res-planet").addEventListener("change", () => { model.research.funding_planet = $("res-planet").value; scheduleSave(); });

// ------------------------------------------------------------------------ plages horaires
function renderTimezones() {
  let zones = [];
  try { zones = Intl.supportedValuesOf("timeZone"); } catch { zones = ["Europe/Paris", "Europe/London", "UTC"]; }
  if (!zones.includes(model.schedule.timezone)) zones.unshift(model.schedule.timezone);
  $("tz").replaceChildren(...zones.map((z) => el("option", { value: z, textContent: z })));
  $("tz").value = model.schedule.timezone;
}
function renderWindows() {
  const box = $("windows");
  box.replaceChildren();
  model.schedule.windows.forEach((w, index) => {
    const start = el("input", { type: "time", value: w.start });
    const end = el("input", { type: "time", value: w.end });
    start.addEventListener("input", () => { w.start = start.value; scheduleSave(); });
    end.addEventListener("input", () => { w.end = end.value; scheduleSave(); });
    const days = DAYS.map((label, i) => {
      const cb = el("input", { type: "checkbox", checked: w.days.includes(i) });
      cb.addEventListener("change", () => {
        w.days = DAYS.map((_, d) => d).filter((d) => (d === i ? cb.checked : w.days.includes(d)));
        scheduleSave();
      });
      return el("label", { className: "day" }, cb, label);
    });
    const remove = el("button", { type: "button", className: "secondary small", textContent: "×", title: "Retirer cette plage" });
    remove.addEventListener("click", () => { model.schedule.windows.splice(index, 1); renderWindows(); scheduleSave(); });
    box.append(el("div", { className: "window-row" }, start, "→", end, ...days, remove));
  });
  if (!model.schedule.windows.length) box.append(el("p", { className: "hint", textContent: "Aucune plage : avec la restriction activée, le bot n'agirait jamais." }));
}
$("add-window").addEventListener("click", () => {
  model.schedule.windows.push({ start: "08:00", end: "23:00", days: [0, 1, 2, 3, 4, 5, 6] });
  renderWindows();
  scheduleSave();
});

// ------------------------------------------------------------------------------ formulaire
function renderAll() {
  $("mode").value = model.mode;
  $("cap").value = model.daily_cap;
  $("cycle-min").value = model.cycle.min_minutes;
  $("cycle-max").value = model.cycle.max_minutes;
  $("sched-enabled").checked = model.schedule.enabled;
  renderTimezones();
  renderWindows();
  $("cons-enabled").checked = model.constructions.enabled;
  $("res-enabled").checked = model.research.enabled;
  renderGoals($("cons-goals"), catalog.buildings, model.constructions.targets);
  renderGoals($("res-goals"), catalog.research, model.research.targets);
  renderPlanetControls();
  catalogKey = catalogSignature();
}
const catalogSignature = () => JSON.stringify([catalog.buildings.map((e) => e.name), catalog.research.map((e) => e.name), catalog.planets]);

const bind = (id, apply) => {
  const node = $(id);
  const event = node.type === "number" ? "input" : "change"; // champs numériques : enregistrés dès la saisie
  node.addEventListener(event, () => { apply(node); scheduleSave(); });
};
bind("cap", (n) => { model.daily_cap = Number(n.value); });
bind("cycle-min", (n) => { model.cycle.min_minutes = Number(n.value); });
bind("cycle-max", (n) => { model.cycle.max_minutes = Number(n.value); });
bind("sched-enabled", (n) => { model.schedule.enabled = n.checked; });
bind("tz", (n) => { model.schedule.timezone = n.value; });
bind("cons-enabled", (n) => { model.constructions.enabled = n.checked; });
bind("res-enabled", (n) => { model.research.enabled = n.checked; });
$("mode").addEventListener("change", () => {
  if ($("mode").value === "live") {
    const ok = confirm("Mode RÉEL : le bot va cliquer sur « Améliorer » et « Rechercher » sur votre vrai compte et dépenser des ressources, dans la limite du plafond quotidien.\n\nActiver le mode réel ?");
    if (!ok) { $("mode").value = "dry_run"; return; }
    confirmedLive = true;
  }
  model.mode = $("mode").value;
  save();
});

// ------------------------------------------------------------------------ actions et état
const post = (url) => fetch(url, { method: "POST" });
$("start").addEventListener("click", async () => { await save(); await post("/api/automation/start"); refresh(); });
$("stop").addEventListener("click", async () => { await post("/api/automation/stop"); refresh(); });

$("import").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    data.mode = "dry_run"; // un fichier importé ne peut jamais activer le mode réel
    delete data.catalog;
    confirmedLive = false;
    const res = await fetch("/api/automation/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: data, confirm_live: false }) });
    const out = await res.json();
    if (!res.ok) return say((Array.isArray(out.detail) ? out.detail.join(" · ") : out.detail) || "Fichier refusé.", false);
    model = out.settings;
    catalog = out.catalog;
    renderAll();
    say("Réglages importés (mode à blanc).", true);
    setTab("run");
  } catch {
    say("Fichier illisible.", false);
  }
  event.target.value = "";
});

function table(headers, rows) {
  const t = el("table", {}, el("tr", {}, ...headers.map((h) => el("th", { textContent: h }))));
  rows.forEach((r) => t.append(el("tr", {}, ...r.map((c) => el("td", { textContent: c })))));
  return t;
}
$("read-state").addEventListener("click", async () => {
  $("read-msg").textContent = "Lecture en cours…";
  const res = await post("/api/automation/read-state");
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
  await refreshCatalog(true);
});

// -------------------------------------------------------------------------- état en direct
const LABELS = { idle: "Non connecté", logging_in: "Connexion…", logged_in: "Connecté", error: "Échec" };
function renderStatus(a, state) {
  $("badge").className = "badge " + state;
  $("badge").textContent = LABELS[state] || state;
  const live = a.mode === "live";
  const next = a.running && a.next_run_in_s !== null ? ` · prochain cycle dans ${a.next_run_in_s} s` : "";
  $("run-status").replaceChildren(
    el("strong", { textContent: a.running ? (a.cycle_running ? "Actif — cycle en cours" : "Actif") : "Arrêté" }),
    " · mode ", el("strong", { className: live ? "live" : "", textContent: live ? "RÉEL" : "à blanc" }),
    ` · plage : ${a.in_window ? "active" : "inactive"} (${a.window})`,
    ` · lancements aujourd'hui : ${a.launched_today}/${a.cap}${next}`,
    a.last_cycle ? ` · dernier cycle ${a.last_cycle}` : "",
  );
  $("start").disabled = a.running;
  $("persist").textContent = a.persistent
    ? "Réglages enregistrés sur le serveur (volume /data) : ils survivent à un redémarrage."
    : "Aucun volume /data : les réglages sont perdus au redémarrage (exportez-les pour les conserver).";
}
async function refresh() {
  try {
    const data = await (await fetch("/api/status?limit=400")).json();
    renderStatus(data.automation, data.state);
    const box = $("log");
    const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    box.replaceChildren(...data.logs.map((e) => el("div", { className: e.level }, el("span", { className: "ts", textContent: e.ts }), e.msg)));
    if (stick) box.scrollTop = box.scrollHeight;
  } catch { /* le bot redémarre : on réessaie */ }
}
/** Met à jour niveaux actuels et planètes ; ne reconstruit les listes que si leur contenu a changé. */
async function refreshCatalog(force = false) {
  try {
    const data = await (await fetch("/api/automation")).json();
    catalog = data.catalog;
    if (force || catalogSignature() !== catalogKey) {
      if (!document.activeElement || !document.activeElement.matches("input, select")) renderAll();
    } else {
      refreshLevels();
    }
  } catch { /* idem */ }
}

(async () => {
  const data = await (await fetch("/api/automation")).json();
  model = data.settings;
  catalog = data.catalog;
  renderAll();
  setTab(initialTab());
  refresh();
  setInterval(refresh, 2000);
  setInterval(() => refreshCatalog(false), 8000);
})();
