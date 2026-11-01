// Page Actions : réglages de l'automatisation (enregistrés côté serveur), état du jeu et journal.
const $ = (id) => document.getElementById(id);
const DAYS = ["L", "M", "M", "J", "V", "S", "D"];
let settings = null;
let confirmedLive = false;
let saveTimer = null;
let building = false; // vrai pendant qu'on remplit le formulaire (évite d'enregistrer en boucle)

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

// ------------------------------------------------------------------ objectifs de niveaux
function renderTargets(container, targets, catalogId) {
  container.replaceChildren();
  Object.entries(targets).forEach(([name, level]) => {
    const input = el("input", { type: "number", min: 0, max: 999, value: level });
    input.dataset.name = name;
    input.addEventListener("change", scheduleSave);
    const remove = el("button", { type: "button", className: "secondary small", textContent: "×", title: "Retirer" });
    remove.addEventListener("click", () => { row.remove(); scheduleSave(); });
    const row = el("div", { className: "target-row" }, el("span", { textContent: name }), el("span", { className: "spacer" }), input, remove);
    container.append(row);
  });
  const name = el("input", { type: "text", placeholder: "Nom exact (ex. Mine de Fer)" });
  name.setAttribute("list", catalogId);
  const level = el("input", { type: "number", min: 0, max: 999, placeholder: "niveau" });
  const add = el("button", { type: "button", className: "secondary small", textContent: "Ajouter" });
  add.addEventListener("click", () => {
    const n = name.value.trim();
    if (!n || level.value === "") return;
    const current = readTargets(container);
    current[n] = Number(level.value);
    renderTargets(container, current, catalogId);
    scheduleSave();
  });
  container.append(el("div", { className: "target-row add" }, name, level, add));
}
const readTargets = (container) =>
  Object.fromEntries([...container.querySelectorAll("input[data-name]")].map((i) => [i.dataset.name, Number(i.value)]));

// ------------------------------------------------------------------------ plages horaires
function renderWindows(windows) {
  const box = $("windows");
  box.replaceChildren();
  windows.forEach((w) => {
    const start = el("input", { type: "time", value: w.start });
    const end = el("input", { type: "time", value: w.end });
    const days = DAYS.map((label, i) => {
      const cb = el("input", { type: "checkbox", checked: w.days.includes(i) });
      cb.dataset.day = i;
      cb.addEventListener("change", scheduleSave);
      return el("label", { className: "day" }, cb, label);
    });
    [start, end].forEach((i) => i.addEventListener("change", scheduleSave));
    const remove = el("button", { type: "button", className: "secondary small", textContent: "×", title: "Retirer" });
    const row = el("div", { className: "window-row" }, start, "→", end, ...days, remove);
    remove.addEventListener("click", () => { row.remove(); scheduleSave(); });
    box.append(row);
  });
}
const readWindows = () =>
  [...$("windows").querySelectorAll(".window-row")].map((row) => {
    const [start, end] = row.querySelectorAll("input[type=time]");
    return { start: start.value, end: end.value, days: [...row.querySelectorAll("input[data-day]")].filter((c) => c.checked).map((c) => Number(c.dataset.day)) };
  });

// ---------------------------------------------------------------------------- formulaire
function fill(data) {
  building = true;
  settings = data;
  $("mode").value = data.mode;
  $("cap").value = data.daily_cap;
  $("cycle-min").value = data.cycle.min_minutes;
  $("cycle-max").value = data.cycle.max_minutes;
  $("sched-enabled").checked = data.schedule.enabled;
  $("tz").value = data.schedule.timezone;
  renderWindows(data.schedule.windows);
  $("cons-enabled").checked = data.constructions.enabled;
  renderTargets($("cons-targets"), data.constructions.targets, "names-buildings");
  $("cons-planets").value = data.constructions.planets.join(", ");
  $("cons-overrides").value = Object.keys(data.constructions.overrides).length ? JSON.stringify(data.constructions.overrides, null, 2) : "";
  $("res-enabled").checked = data.research.enabled;
  renderTargets($("res-targets"), data.research.targets, "names-research");
  $("res-planet").value = data.research.funding_planet;
  for (const [kind, id] of [["buildings", "names-buildings"], ["research", "names-research"]]) {
    $(id).replaceChildren(...data.catalog[kind].map((n) => el("option", { value: n })));
  }
  building = false;
}

function collect() {
  let overrides = {};
  const raw = $("cons-overrides").value.trim();
  if (raw) overrides = JSON.parse(raw); // une erreur de syntaxe est signalée par save()
  return {
    mode: $("mode").value,
    daily_cap: Number($("cap").value),
    cycle: { min_minutes: Number($("cycle-min").value), max_minutes: Number($("cycle-max").value) },
    schedule: { enabled: $("sched-enabled").checked, timezone: $("tz").value.trim() || "Europe/Paris", windows: readWindows() },
    constructions: {
      enabled: $("cons-enabled").checked,
      targets: readTargets($("cons-targets")),
      overrides,
      planets: $("cons-planets").value.split(",").map((s) => s.trim()).filter(Boolean),
    },
    research: { enabled: $("res-enabled").checked, targets: readTargets($("res-targets")), funding_planet: $("res-planet").value.trim() },
    catalog: settings ? settings.catalog : { buildings: [], research: [] },
  };
}

function say(text, ok) {
  const msg = $("save-msg");
  msg.textContent = text;
  msg.className = "msg " + (ok ? "ok" : "error");
}

async function save() {
  let body;
  try {
    body = collect();
  } catch {
    return say("Exceptions par planète : JSON invalide.", false);
  }
  const res = await fetch("/api/automation/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: body, confirm_live: confirmedLive }),
  });
  const data = await res.json();
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Réglages refusés.";
    return say(detail, false);
  }
  settings = data.settings;
  say("Réglages enregistrés.", true);
}
function scheduleSave() {
  if (building) return;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 500);
}

document.querySelectorAll("#cap, #cycle-min, #cycle-max, #sched-enabled, #tz, #cons-enabled, #cons-planets, #cons-overrides, #res-enabled, #res-planet")
  .forEach((node) => node.addEventListener("change", scheduleSave));
$("add-window").addEventListener("click", () => {
  const windows = readWindows();
  windows.push({ start: "08:00", end: "23:00", days: [0, 1, 2, 3, 4, 5, 6] });
  renderWindows(windows);
  scheduleSave();
});
$("mode").addEventListener("change", () => {
  if ($("mode").value === "live") {
    const ok = confirm("Mode RÉEL : le bot va cliquer sur « Améliorer » et « Rechercher » sur votre vrai compte et dépenser des ressources, dans la limite du plafond quotidien.\n\nActiver le mode réel ?");
    if (!ok) { $("mode").value = "dry_run"; return; }
    confirmedLive = true;
  }
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
    confirmedLive = false;
    const res = await fetch("/api/automation/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: data, confirm_live: false }) });
    const out = await res.json();
    if (!res.ok) return say((Array.isArray(out.detail) ? out.detail.join(" · ") : out.detail) || "Fichier refusé.", false);
    fill(out.settings);
    say("Réglages importés (mode à blanc).", true);
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
  const fresh = await (await fetch("/api/automation")).json();
  fill(fresh.settings); // les noms découverts alimentent les listes de suggestion
});

// -------------------------------------------------------------------------- état en direct
const LABELS = { idle: "Non connecté", logging_in: "Connexion…", logged_in: "Connecté", error: "Échec" };
function renderStatus(a, state) {
  $("badge").className = "badge " + state;
  $("badge").textContent = LABELS[state] || state;
  const live = a.mode === "live";
  const next = a.running && a.next_run_in_s !== null ? ` · prochain cycle dans ${a.next_run_in_s} s` : "";
  const line = $("run-status");
  line.replaceChildren(
    el("strong", { textContent: a.running ? (a.cycle_running ? "Actif — cycle en cours" : "Actif") : "Arrêté" }),
    ` · mode `, el("strong", { className: live ? "live" : "", textContent: live ? "RÉEL" : "à blanc" }),
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
    {
      box.replaceChildren(...data.logs.map((e) => el("div", { className: e.level }, el("span", { className: "ts", textContent: e.ts }), e.msg)));
      if (stick) box.scrollTop = box.scrollHeight;
    }
  } catch { /* le bot redémarre : on réessaie */ }
}
(async () => {
  fill((await (await fetch("/api/automation")).json()).settings);
  refresh();
  setInterval(refresh, 2000);
})();
