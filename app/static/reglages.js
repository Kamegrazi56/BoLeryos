// Page Réglages : 5 onglets, enregistrement automatique côté serveur, alertes.
const { el, fmt, table, help } = window.ui;
const $ = (id) => document.getElementById(id);
const DAYS = ["L", "M", "M", "J", "V", "S", "D"];
const TABS = ["account", "constructions", "research", "production", "expeditions", "espionage", "raids", "priority", "schedule", "settings"];

let model = null; // réglages en cours d'édition (source de vérité de l'interface)
let catalog = { buildings: [], research: [], planets: [], universes: [], presets: [], production: {}, production_sections: [], blocks: [], expedition_ways: [], expedition_units: {}, espionage_ways: [] };
let prodDone = {};
let saveTimer = null;
let saving = Promise.resolve();
let catalogKey = "";

document.querySelectorAll("[data-help-text]").forEach((node) => node.replaceWith(help(node.dataset.helpText)));

// ---------------------------------------------------------------------------------- onglets
function setTab(id) {
  if (!TABS.includes(id)) id = "account";
  TABS.forEach((t) => {
    const active = t === id;
    $("tab-" + t).setAttribute("aria-selected", String(active));
    $("tab-" + t).tabIndex = active ? 0 : -1;
    $("panel-" + t).hidden = !active;
  });
  try { localStorage.setItem("boleryos.settings.tab", id); } catch { /* stockage indisponible : sans effet */ }
  if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id);
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
function initialTab() {
  const fromHash = location.hash.slice(1);
  if (TABS.includes(fromHash)) return fromHash;
  try { return localStorage.getItem("boleryos.settings.tab") || "account"; } catch { return "account"; }
}

// -------------------------------------------------------------------------- enregistrement
function say(text, kind) {
  const msg = $("save-msg");
  msg.textContent = text;
  msg.className = "save-state " + (kind || "");
}
const detailText = (data, fallback) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || fallback);

/** Le serveur donne un identifiant à chaque ligne de la liste : on le reprend sans toucher à ce que l'utilisateur tape. */
function adoptIds(saved) {
  const mine = model.constructions.queue, theirs = saved.constructions.queue;
  if (mine.length === theirs.length && mine.every((item, i) => item.name === theirs[i].name)) {
    mine.forEach((item, i) => { item.id = theirs[i].id; });
  }
}
function adoptProductionIds(saved) {
  const mine = model.production.queue, theirs = saved.production.queue;
  if (mine.length === theirs.length && mine.every((item, i) => item.name === theirs[i].name && item.section === theirs[i].section)) {
    mine.forEach((item, i) => { item.id = theirs[i].id; });
  }
}
function adoptExpeditionIds(saved) {
  const mine = model.expeditions.queue, theirs = saved.expeditions.queue;
  if (mine.length === theirs.length && mine.every((item, i) => item.way === theirs[i].way)) mine.forEach((item, i) => { item.id = theirs[i].id; });
}
async function doSave() {
  if (model.constructions.scope === "one" && !model.constructions.planet) return; // en attente du choix de la planète
  if (model.production.scope === "one" && !model.production.planet) return;
  if (model.expeditions.scope === "one" && !model.expeditions.planet) return;
  const body = JSON.parse(JSON.stringify(model));
  delete body.catalog; // entretenu par le bot
  delete body.mode; // le mode se change depuis l'Accueil, avec confirmation
  say("Enregistrement…");
  const res = await fetch("/api/automation/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: body, confirm_live: false }),
  });
  const data = await res.json();
  if (!res.ok) return say(detailText(data, "Réglages refusés."), "error");
  catalog = data.catalog;
  adoptIds(data.settings);
  adoptProductionIds(data.settings);
  adoptExpeditionIds(data.settings);
  say("Enregistré ✓", "ok");
}
const save = () => { clearTimeout(saveTimer); saving = saving.then(doSave).catch(() => say("Enregistrement impossible.", "error")); return saving; };
function scheduleSave() {
  clearTimeout(saveTimer);
  say("Modifications en cours…");
  saveTimer = setTimeout(save, 500);
}
window.beforePowerOn = save; // l'interrupteur ON/OFF enregistre d'abord les réglages

// ---------------------------------------------------------------- listes de niveaux demandés
function currentLevel(entry) {
  if (!entry || entry.level_min === null || entry.level_min === undefined) return "niveau actuel inconnu";
  return entry.level_min === entry.level_max ? `actuel : ${entry.level_min}` : `actuel : ${entry.level_min} à ${entry.level_max}`;
}
/** Une ligne par élément du jeu (liste intégrée au bot) : nom, niveau actuel, champ « niveau demandé ». */
function renderGoals(container, entries, targets) {
  container.replaceChildren();
  if (!entries.length) {
    container.append(el("p", { className: "empty-state", textContent: "La liste se remplit au premier passage du bot dans le jeu." }));
    return;
  }
  let group = null;
  entries.forEach((entry) => {
    if (entry.group !== group) {
      group = entry.group;
      container.append(el("h3", { textContent: group }));
    }
    const input = el("input", { type: "number", min: 0, max: 999, placeholder: "—" });
    input.value = targets[entry.name] ?? "";
    input.dataset.goal = entry.name;
    input.setAttribute("aria-label", `Niveau demandé pour ${entry.name}`);
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
  const buildings = Object.fromEntries(catalog.buildings.map((e) => [e.name, e]));
  document.querySelectorAll("#cons-queue .queue-row").forEach((row) => {
    const entry = buildings[row.querySelector(".goal-name").textContent];
    if (entry) row.querySelector(".goal-current").textContent = currentLevel(entry);
  });
  const research = Object.fromEntries(catalog.research.map((e) => [e.name, e]));
  $("res-goals").querySelectorAll(".goal-row").forEach((row) => {
    const entry = research[row.querySelector("input").dataset.goal];
    if (entry) row.querySelector(".goal-current").textContent = currentLevel(entry);
  });
}

// ------------------------------------------------------------------------------ constructions
function renderScope() {
  const c = model.constructions;
  $("scope-all").checked = c.scope === "all";
  $("scope-one").checked = c.scope === "one";
  const names = [...new Set([...catalog.planets, c.planet].filter(Boolean))];
  $("cons-planet").replaceChildren(
    el("option", { value: "", textContent: names.length ? "Choisir une planète…" : "Aucune planète connue" }),
    ...names.map((n) => el("option", { value: n, textContent: n })),
  );
  $("cons-planet").value = c.planet;
  $("cons-planet").disabled = c.scope !== "one";
  $("cons-planet-hint").textContent = !catalog.planets.length
    ? "La liste des planètes apparaît après « Lire l'état du jeu » (page Suivi) ou au premier passage du bot."
    : c.scope === "one" && !c.planet ? "Choisissez la planète concernée." : "";
}
document.querySelectorAll('input[name="cons-scope"]').forEach((radio) => radio.addEventListener("change", () => {
  const c = model.constructions;
  c.scope = radio.value;
  c.planet = c.scope === "all" ? "" : c.planet || catalog.planets[0] || "";
  renderScope();
  scheduleSave();
}));
$("cons-planet").addEventListener("change", () => { model.constructions.planet = $("cons-planet").value; renderScope(); scheduleSave(); });

/** Liste ordonnée des améliorations souhaitées : le bot lance la première réalisable. */
function renderQueue() {
  const queue = model.constructions.queue;
  const byName = Object.fromEntries(catalog.buildings.map((e) => [e.name, e]));
  const list = $("cons-queue");
  list.replaceChildren();
  queue.forEach((item, i) => {
    const mode = el("select", { ariaLabel: `Type d'objectif pour ${item.name}` },
      el("option", { value: "absolute", textContent: "Jusqu'au niveau" }),
      el("option", { value: "relative", textContent: "Monter de" }));
    mode.value = item.relative ? "relative" : "absolute";
    mode.addEventListener("change", () => {
      item.relative = mode.value === "relative";
      if (item.relative && !(item.level >= 1)) item.level = 5;
      level.value = item.level;
      level.min = item.relative ? 1 : 0;
      scheduleSave();
    });
    const level = el("input", { type: "number", min: item.relative ? 1 : 0, max: 999, ariaLabel: `Niveau pour ${item.name}` });
    level.value = item.level;
    level.addEventListener("input", () => {
      if (level.value === "") return;
      item.level = Math.max(item.relative ? 1 : 0, Math.min(999, Number(level.value)));
      scheduleSave();
    });
    const button = (text, title, onClick, disabled = false) => {
      const b = el("button", { type: "button", className: "secondary small", textContent: text, title, disabled });
      b.addEventListener("click", onClick);
      return b;
    };
    const move = (d) => () => { [queue[i], queue[i + d]] = [queue[i + d], queue[i]]; renderQueue(); scheduleSave(); };
    list.append(el("li", { className: "queue-row" },
      el("span", { className: "rank", textContent: String(i + 1) }),
      el("span", { className: "goal-name", textContent: item.name }),
      el("span", { className: "goal-current", textContent: currentLevel(byName[item.name]) }),
      mode, level,
      el("span", { className: "hint", textContent: item.relative ? "niveaux" : "" }),
      button("↑", "Monter (plus prioritaire)", move(-1), i === 0),
      button("↓", "Descendre", move(1), i === queue.length - 1),
      button("×", "Retirer de la liste", () => { queue.splice(i, 1); renderQueue(); scheduleSave(); }),
    ));
  });
  if (!queue.length) list.append(el("li", { className: "empty-state", textContent: "Liste vide : choisissez une liste toute faite ou un bâtiment ci-dessous." }));
  // bâtiment à ajouter : liste intégrée au bot, sans ceux déjà présents
  const used = new Set(queue.map((q) => q.name));
  const select = $("cons-add-name");
  const keep = select.value;
  select.replaceChildren(el("option", { value: "", textContent: "Choisir un bâtiment…" }));
  let group = null, optgroup = null;
  catalog.buildings.filter((e) => !used.has(e.name)).forEach((e) => {
    if (e.group !== group) { group = e.group; optgroup = el("optgroup", { label: group }); select.append(optgroup); }
    optgroup.append(el("option", { value: e.name, textContent: e.name }));
  });
  select.value = keep;
}
$("cons-add").addEventListener("click", () => {
  const name = $("cons-add-name").value, level = $("cons-add-level").value;
  const relative = $("cons-add-mode").value === "relative";
  if (!name || level === "") return;
  model.constructions.queue.push({ name, level: Math.max(relative ? 1 : 0, Math.min(999, Number(level))), relative });
  $("cons-add-level").value = "";
  renderQueue();
  scheduleSave();
});
$("cons-add-mode").addEventListener("change", () => {
  $("cons-add-level").placeholder = $("cons-add-mode").value === "relative" ? "nb de niveaux" : "niveau";
});

function renderPresets() {
  const select = $("preset");
  select.replaceChildren(...(catalog.presets || []).map((p) => el("option", { value: p.id, textContent: p.label })));
  $("preset-apply").disabled = !(catalog.presets || []).length;
}
$("preset-apply").addEventListener("click", () => {
  const preset = (catalog.presets || []).find((p) => p.id === $("preset").value);
  if (!preset) return;
  const n = Math.max(1, Math.min(999, Number($("preset-n").value) || 5));
  const queue = model.constructions.queue;
  if (queue.length && !confirm("Remplacer la liste actuelle par cette liste toute faite ?")) return;
  model.constructions.queue = preset.names.map((name) => ({ name, level: n, relative: true }));
  renderQueue();
  scheduleSave();
});

function renderResearchPlanet() {
  const names = [...new Set([...catalog.planets, model.research.funding_planet].filter(Boolean))];
  $("res-planet").replaceChildren(
    el("option", { value: "", textContent: "Automatique (la planète qui a le plus de Fer)" }),
    ...names.map((n) => el("option", { value: n, textContent: n })),
  );
  $("res-planet").value = model.research.funding_planet;
}
$("res-planet").addEventListener("change", () => { model.research.funding_planet = $("res-planet").value; scheduleSave(); });


// ---------------------------------------------------------------------------------- production
const MODE_LABEL = { once: "Commande unique", stock: "Objectif de stock", repeat: "Répéter à chaque passage" };
const sectionLabel = (id) => (catalog.production_sections.find((s) => s.id === id) || { label: id }).label;

function renderProdScope() {
  const c = model.production;
  $("pscope-all").checked = c.scope === "all";
  $("pscope-one").checked = c.scope === "one";
  const names = [...new Set([...catalog.planets, c.planet].filter(Boolean))];
  $("prod-planet").replaceChildren(
    el("option", { value: "", textContent: names.length ? "Choisir une planète…" : "Aucune planète connue" }),
    ...names.map((n) => el("option", { value: n, textContent: n })),
  );
  $("prod-planet").value = c.planet;
  $("prod-planet").disabled = c.scope !== "one";
  $("prod-planet-hint").textContent = c.scope === "one" && !c.planet ? "Choisissez la planète concernée." : "";
}
document.querySelectorAll('input[name="prod-scope"]').forEach((radio) => radio.addEventListener("change", () => {
  const c = model.production;
  c.scope = radio.value;
  c.planet = c.scope === "all" ? "" : c.planet || catalog.planets[0] || "";
  renderProdScope();
  scheduleSave();
}));
$("prod-planet").addEventListener("change", () => { model.production.planet = $("prod-planet").value; renderProdScope(); scheduleSave(); });

function renderProdAddNames() {
  const section = $("prod-add-section").value;
  const entries = (catalog.production[section] || []).filter((e) => !model.production.queue.some((q) => q.section === section && q.name === e.name));
  const select = $("prod-add-name");
  const keep = select.value;
  select.replaceChildren(el("option", { value: "", textContent: entries.length ? "Choisir…" : "Aucun élément disponible" }));
  let group = null, optgroup = null;
  entries.forEach((e) => {
    if (e.group !== group) { group = e.group; optgroup = el("optgroup", { label: group }); select.append(optgroup); }
    optgroup.append(el("option", { value: e.name, textContent: e.name }));
  });
  select.value = keep;
  $("prod-ships-hint").textContent = section === "ships" && !(catalog.production.ships || []).length
    ? "Vos modèles de vaisseaux apparaissent après « Lire l'état du jeu » (page Suivi, onglet État du jeu)."
    : "";
}
$("prod-add-section").addEventListener("change", renderProdAddNames);
$("prod-add-mode").addEventListener("change", () => {
  if ($("prod-add-mode").value === "stock") $("prod-add-max").checked = false;
  $("prod-add-max").disabled = $("prod-add-mode").value === "stock";
});
$("prod-add-max").addEventListener("change", () => { $("prod-add-amount").disabled = $("prod-add-max").checked; });

function renderProdQueue() {
  const queue = model.production.queue;
  const list = $("prod-queue");
  list.replaceChildren();
  queue.forEach((item, i) => {
    const mode = el("select", { ariaLabel: `Comportement pour ${item.name}` }, ...Object.entries(MODE_LABEL).map(([v, t]) => el("option", { value: v, textContent: t })));
    mode.value = item.mode;
    const amount = el("input", { type: "number", min: 1, max: 1e12, ariaLabel: `Quantité pour ${item.name}`, disabled: item.max });
    amount.value = item.max ? "" : item.amount;
    const maxBox = el("input", { type: "checkbox", checked: item.max, disabled: item.mode === "stock", ariaLabel: `Maximum pour ${item.name}` });
    mode.addEventListener("change", () => {
      item.mode = mode.value;
      if (item.mode === "stock") item.max = false;
      renderProdQueue();
      scheduleSave();
    });
    amount.addEventListener("input", () => {
      if (amount.value === "") return;
      item.amount = Math.max(1, Math.min(1e12, Math.floor(Number(amount.value))));
      scheduleSave();
    });
    maxBox.addEventListener("change", () => { item.max = maxBox.checked; renderProdQueue(); scheduleSave(); });
    const button = (text, title, onClick, disabled = false) => {
      const b = el("button", { type: "button", className: "secondary small", textContent: text, title, disabled });
      b.addEventListener("click", onClick);
      return b;
    };
    const move = (d) => () => { [queue[i], queue[i + d]] = [queue[i + d], queue[i]]; renderProdQueue(); scheduleSave(); };
    const done = prodDone[item.id];
    const status = el("span", { className: "goal-current" });
    if (item.mode === "once" && done) {
      status.append(`faite sur ${done} planète(s) `);
      const again = button("Relancer", "La commande sera refaite au prochain passage", async () => {
        await fetch("/api/automation/production/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: item.id }) });
        refreshCatalog(false);
      });
      status.append(again);
    }
    list.append(el("li", { className: "queue-row" },
      el("span", { className: "rank", textContent: String(i + 1) }),
      el("span", { className: "goal-name", textContent: item.name, title: sectionLabel(item.section) }),
      el("span", { className: "hint", textContent: sectionLabel(item.section) }),
      mode, amount, el("label", { className: "day" }, maxBox, "max"), status,
      button("↑", "Monter (plus prioritaire)", move(-1), i === 0),
      button("↓", "Descendre", move(1), i === queue.length - 1),
      button("×", "Retirer de la liste", () => { queue.splice(i, 1); renderProdQueue(); scheduleSave(); }),
    ));
  });
  if (!queue.length) list.append(el("li", { className: "empty-state", textContent: "Liste vide : choisissez un type de production et un élément ci-dessous." }));
  renderProdAddNames();
}
$("prod-add").addEventListener("click", () => {
  const section = $("prod-add-section").value, name = $("prod-add-name").value, mode = $("prod-add-mode").value;
  const max = $("prod-add-max").checked, raw = $("prod-add-amount").value;
  if (!section || !name || (!max && raw === "")) return;
  model.production.queue.push({ section, name, mode, max, amount: max ? 1 : Math.max(1, Math.min(1e12, Math.floor(Number(raw)))) });
  $("prod-add-amount").value = "";
  renderProdQueue();
  scheduleSave();
});

function renderPriority() {
  const order = model.priority;
  const label = (id) => (catalog.blocks.find((b) => b.id === id) || { label: id }).label;
  const list = $("priority-list");
  list.replaceChildren(...order.map((id, i) => {
    const button = (text, title, onClick, disabled) => {
      const b = el("button", { type: "button", className: "secondary small", textContent: text, title, disabled });
      b.addEventListener("click", onClick);
      return b;
    };
    const move = (d) => () => { [order[i], order[i + d]] = [order[i + d], order[i]]; renderPriority(); scheduleSave(); };
    const row = el("li", { className: "queue-row" },
      el("span", { className: "rank", textContent: String(i + 1) }),
      el("span", { className: "goal-name", textContent: label(id) }),
      button("↑", "Plus prioritaire", move(-1), i === 0),
      button("↓", "Moins prioritaire", move(1), i === order.length - 1));
    row.dataset.block = id;
    return row;
  }));
}
$("prod-enabled").addEventListener("change", () => { model.production.enabled = $("prod-enabled").checked; scheduleSave(); });
$("prod-recheck").addEventListener("input", () => { if ($("prod-recheck").value !== "") { model.production.recheck_minutes = Number($("prod-recheck").value); scheduleSave(); } });

function renderProduction() {
  $("prod-enabled").checked = model.production.enabled;
  $("prod-recheck").value = model.production.recheck_minutes;
  $("prod-add-section").replaceChildren(...catalog.production_sections.map((s) => el("option", { value: s.id, textContent: s.label })));
  renderProdScope();
  renderProdQueue();
  renderPriority();
}

// -------------------------------------------------------------------------------------- expéditions
const UNIT_MODE = { number: "nombre", max: "maximum", percent: "% des disponibles" };
const wayLabel = (id) => (catalog.expedition_ways.find((w) => w.id === id) || { label: id }).label;

function renderExpScope() {
  const c = model.expeditions;
  $("escope-all").checked = c.scope === "all";
  $("escope-one").checked = c.scope === "one";
  const names = [...new Set([...catalog.planets, c.planet].filter(Boolean))];
  $("exp-planet").replaceChildren(
    el("option", { value: "", textContent: names.length ? "Choisir une planète…" : "Aucune planète connue" }),
    ...names.map((n) => el("option", { value: n, textContent: n })),
  );
  $("exp-planet").value = c.planet;
  $("exp-planet").disabled = c.scope !== "one";
}
document.querySelectorAll('input[name="exp-scope"]').forEach((radio) => radio.addEventListener("change", () => {
  const c = model.expeditions;
  c.scope = radio.value;
  c.planet = c.scope === "all" ? "" : c.planet || catalog.planets[0] || "";
  renderExpScope();
  scheduleSave();
}));
$("exp-planet").addEventListener("change", () => { model.expeditions.planet = $("exp-planet").value; renderExpScope(); scheduleSave(); });
$("exp-enabled").addEventListener("change", () => { model.expeditions.enabled = $("exp-enabled").checked; scheduleSave(); });
$("exp-target").addEventListener("input", () => {
  if ($("exp-target").value !== "") { model.expeditions.active_target = Math.max(1, Math.min(50, Math.floor(Number($("exp-target").value)))); scheduleSave(); }
});
$("exp-recheck").addEventListener("input", () => { if ($("exp-recheck").value !== "") { model.expeditions.recheck_minutes = Number($("exp-recheck").value); scheduleSave(); } });

function renderExpQueue() {
  const queue = model.expeditions.queue;
  const list = $("exp-queue");
  list.replaceChildren();
  const button = (text, title, onClick, disabled = false) => {
    const b = el("button", { type: "button", className: "secondary small", textContent: text, title, disabled });
    b.addEventListener("click", onClick);
    return b;
  };
  queue.forEach((line, i) => {
    const hours = el("input", { type: "number", min: 1, max: 24, ariaLabel: "Durée en heures" });
    hours.value = line.hours;
    hours.addEventListener("input", () => { if (hours.value !== "") { line.hours = Math.max(1, Math.min(24, Math.floor(Number(hours.value)))); scheduleSave(); } });
    const units = el("ul", { className: "unit-list" });
    line.units.forEach((u, k) => {
      const value = el("input", { type: "number", min: 1, max: u.mode === "percent" ? 100 : 1e12, disabled: u.mode === "max", ariaLabel: `Quantité de ${u.name}` });
      value.value = u.mode === "max" ? "" : u.value;
      value.addEventListener("input", () => { if (value.value !== "") { u.value = Math.max(1, Math.floor(Number(value.value))); scheduleSave(); } });
      const mode = el("select", { ariaLabel: `Mode pour ${u.name}` }, ...Object.entries(UNIT_MODE).map(([v, t]) => el("option", { value: v, textContent: t })));
      mode.value = u.mode;
      mode.addEventListener("change", () => { u.mode = mode.value; if (u.mode === "percent") u.value = Math.min(u.value, 100); renderExpQueue(); scheduleSave(); });
      units.append(el("li", {}, el("span", { className: "goal-name", textContent: u.name }), mode, value,
        button("×", "Retirer cette unité", () => { if (line.units.length > 1) { line.units.splice(k, 1); renderExpQueue(); scheduleSave(); } }, line.units.length < 2)));
    });
    const choices = (catalog.expedition_units[line.way] || []).filter((e) => !line.units.some((u) => u.name === e.name));
    const pick = el("select", { ariaLabel: "Unité à ajouter" }, el("option", { value: "", textContent: choices.length ? "Ajouter une unité…" : "Aucune autre unité" }),
      ...choices.map((e) => el("option", { value: e.name, textContent: e.name })));
    pick.addEventListener("change", () => {
      if (!pick.value) return;
      line.units.push({ name: pick.value, mode: "number", value: 1 });
      renderExpQueue();
      scheduleSave();
    });
    const move = (d) => () => { [queue[i], queue[i + d]] = [queue[i + d], queue[i]]; renderExpQueue(); scheduleSave(); };
    list.append(el("li", { className: "queue-row exp-row" },
      el("span", { className: "rank", textContent: String(i + 1) }),
      el("span", { className: "goal-name", textContent: wayLabel(line.way) }),
      el("label", { className: "day" }, "durée", hours, "h"),
      units, pick,
      button("↑", "Monter (plus prioritaire)", move(-1), i === 0),
      button("↓", "Descendre", move(1), i === queue.length - 1),
      button("×", "Retirer de la liste", () => { queue.splice(i, 1); renderExpQueue(); scheduleSave(); })));
  });
  if (!queue.length) list.append(el("li", { className: "empty-state", textContent: "Liste vide : ajoutez une expédition ci-dessous." }));
  $("exp-hint").textContent = !(catalog.expedition_units.flotte || []).some((u) => u.group.startsWith("Mes modèles"))
    ? "Vos modèles de vaisseaux (voie Flotte) apparaissent après « Lire l'état du jeu » (page Suivi, onglet État du jeu)."
    : "";
}
$("exp-add").addEventListener("click", () => {
  const way = $("exp-add-way").value;
  const first = (catalog.expedition_units[way] || [])[0];
  if (!way || !first) return;
  model.expeditions.queue.push({ way, hours: 1, units: [{ name: first.name, mode: "number", value: 1 }] });
  renderExpQueue();
  scheduleSave();
});
function renderExpeditions() {
  $("exp-enabled").checked = model.expeditions.enabled;
  $("exp-target").value = model.expeditions.active_target;
  $("exp-recheck").value = model.expeditions.recheck_minutes;
  $("exp-add-way").replaceChildren(...catalog.expedition_ways.map((w) => el("option", { value: w.id, textContent: w.label })));
  renderExpScope();
  renderExpQueue();
}

// -------------------------------------------------------------------------------------- espionnage
function renderEspionage() {
  const c = model.espionage;
  $("spy-enabled").checked = c.enabled;
  $("spy-players").checked = c.players;
  $("spy-errants").checked = c.errants;
  $("spy-exclude").checked = c.exclude_alliance;
  $("spy-min").value = c.coord_min;
  $("spy-max").value = c.coord_max;
  $("spy-way").replaceChildren(...catalog.espionage_ways.map((w) => el("option", { value: w.id, textContent: w.label })));
  $("spy-way").value = c.way;
  const names = [...new Set([...catalog.planets, c.planet].filter(Boolean))];
  $("spy-planet").replaceChildren(el("option", { value: "", textContent: "La planète active" }), ...names.map((n) => el("option", { value: n, textContent: n })));
  $("spy-planet").value = c.planet;
}
[["spy-enabled", "enabled"], ["spy-players", "players"], ["spy-errants", "errants"], ["spy-exclude", "exclude_alliance"]].forEach(([id, key]) => {
  $(id).addEventListener("change", () => { model.espionage[key] = $(id).checked; scheduleSave(); });
});
[["spy-min", "coord_min"], ["spy-max", "coord_max"]].forEach(([id, key]) => {
  $(id).addEventListener("change", () => { $(id).value = $(id).value.trim().toUpperCase(); model.espionage[key] = $(id).value; scheduleSave(); });
});
$("spy-way").addEventListener("change", () => { model.espionage.way = $("spy-way").value; scheduleSave(); });
$("spy-planet").addEventListener("change", () => { model.espionage.planet = $("spy-planet").value; scheduleSave(); });

// -------------------------------------------------------------------------------- attaque des errants
function renderRaidUnits() {
  const r = model.raids;
  const list = $("raid-units");
  list.replaceChildren(...r.units.map((u, k) => {
    const value = el("input", { type: "number", min: 1, max: u.mode === "percent" ? 100 : 1e12, disabled: u.mode === "max", ariaLabel: `Quantité de ${u.name}` });
    value.value = u.mode === "max" ? "" : u.value;
    value.addEventListener("input", () => { if (value.value !== "") { u.value = Math.max(1, Math.floor(Number(value.value))); scheduleSave(); } });
    const mode = el("select", { ariaLabel: `Mode pour ${u.name}` }, ...Object.entries(UNIT_MODE).map(([v, t]) => el("option", { value: v, textContent: t })));
    mode.value = u.mode;
    mode.addEventListener("change", () => { u.mode = mode.value; if (u.mode === "percent") u.value = Math.min(u.value, 100); renderRaidUnits(); scheduleSave(); });
    const remove = el("button", { type: "button", className: "secondary small", textContent: "×", title: "Retirer cette unité" });
    remove.addEventListener("click", () => { r.units.splice(k, 1); renderRaidUnits(); scheduleSave(); });
    return el("li", {}, el("span", { className: "goal-name", textContent: u.name }), mode, value, remove);
  }));
  if (!r.units.length) list.append(el("li", { className: "empty-state", textContent: "Aucune unité : ajoutez-en une ci-dessous." }));
  const choices = (catalog.expedition_units[r.way] || []).filter((e) => !r.units.some((u) => u.name === e.name));
  $("raid-add-unit").replaceChildren(el("option", { value: "", textContent: "Ajouter une unité…" }), ...choices.map((e) => el("option", { value: e.name, textContent: e.name })));
}
function renderRaids() {
  const r = model.raids;
  $("raid-enabled").checked = r.enabled;
  $("raid-min").value = r.coord_min;
  $("raid-max").value = r.coord_max;
  $("raid-fer").value = r.min_fer;
  $("raid-cristal").value = r.min_cristal;
  $("raid-ether").value = r.min_ether;
  $("raid-required").value = String(r.required);
  $("raid-total").value = r.total;
  $("raid-way").replaceChildren(...catalog.expedition_ways.map((w) => el("option", { value: w.id, textContent: w.label })));
  $("raid-way").value = r.way;
  const names = [...new Set([...catalog.planets, r.planet].filter(Boolean))];
  $("raid-planet").replaceChildren(el("option", { value: "", textContent: "La planète active" }), ...names.map((n) => el("option", { value: n, textContent: n })));
  $("raid-planet").value = r.planet;
  renderRaidUnits();
}
$("raid-enabled").addEventListener("change", () => { model.raids.enabled = $("raid-enabled").checked; scheduleSave(); });
[["raid-min", "coord_min"], ["raid-max", "coord_max"]].forEach(([id, key]) => {
  $(id).addEventListener("change", () => { $(id).value = $(id).value.trim().toUpperCase(); model.raids[key] = $(id).value; scheduleSave(); });
});
[["raid-fer", "min_fer"], ["raid-cristal", "min_cristal"], ["raid-ether", "min_ether"], ["raid-total", "total"]].forEach(([id, key]) => {
  $(id).addEventListener("input", () => { if ($(id).value !== "") { model.raids[key] = Math.max(0, Math.floor(Number($(id).value))); scheduleSave(); } });
});
$("raid-required").addEventListener("change", () => { model.raids.required = Number($("raid-required").value); scheduleSave(); });
$("raid-planet").addEventListener("change", () => { model.raids.planet = $("raid-planet").value; scheduleSave(); });
$("raid-way").addEventListener("change", () => { model.raids.way = $("raid-way").value; model.raids.units = []; renderRaidUnits(); scheduleSave(); });
$("raid-add-unit").addEventListener("change", () => {
  const name = $("raid-add-unit").value;
  if (!name) return;
  model.raids.units.push({ name, mode: "number", value: 1 });
  renderRaidUnits();
  scheduleSave();
});
$("raid-reset").addEventListener("click", async () => {
  await fetch("/api/automation/raids/reset", { method: "POST" });
  refreshCatalog(false);
});

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
  if (!model.schedule.windows.length) box.append(el("p", { className: "empty-state", textContent: "Aucune plage : avec la restriction activée, le bot n'agirait jamais." }));
}
$("add-window").addEventListener("click", () => {
  model.schedule.windows.push({ start: "08:00", end: "23:00", days: [0, 1, 2, 3, 4, 5, 6] });
  renderWindows();
  scheduleSave();
});

// ------------------------------------------------------------------------------ formulaire
const catalogSignature = () => JSON.stringify([catalog.buildings.map((e) => e.name), catalog.research.map((e) => e.name), catalog.planets, catalog.universes, (catalog.production.ships || []).map((e) => e.name)]);
function renderAll() {
  $("cap").value = model.daily_cap;
  $("cycle-min").value = model.cycle.min_minutes;
  $("cycle-max").value = model.cycle.max_minutes;
  $("sched-enabled").checked = model.schedule.enabled;
  renderTimezones();
  renderWindows();
  $("cons-enabled").checked = model.constructions.enabled;
  $("res-enabled").checked = model.research.enabled;
  $("attack-enabled").checked = model.attack_alert.enabled;
  $("attack-interval").value = model.attack_alert.interval_seconds;
  $("cons-recheck").value = model.constructions.recheck_minutes;
  renderScope();
  renderPresets();
  renderQueue();
  renderGoals($("res-goals"), catalog.research, model.research.targets);
  renderResearchPlanet();
  renderUniverse();
  renderProduction();
  renderExpeditions();
  renderEspionage();
  renderRaids();
  catalogKey = catalogSignature();
}
function renderUniverse() {
  const names = [...new Set([...(catalog.universes || []), model.universe].filter(Boolean))];
  $("universe-list").replaceChildren(...names.map((n) => el("option", { value: n })));
  if (document.activeElement !== $("universe")) $("universe").value = model.universe || "";
}
$("universe").addEventListener("change", () => { model.universe = $("universe").value.trim(); scheduleSave(); });

const bind = (id, apply) => {
  const node = $(id);
  const event = node.type === "number" ? "input" : "change"; // champs numériques : enregistrés dès la saisie
  node.addEventListener(event, () => { apply(node); scheduleSave(); });
};
bind("cap", (n) => { if (n.value !== "") model.daily_cap = Number(n.value); });
bind("cycle-min", (n) => { if (n.value !== "") model.cycle.min_minutes = Number(n.value); });
bind("cycle-max", (n) => { if (n.value !== "") model.cycle.max_minutes = Number(n.value); });
bind("sched-enabled", (n) => { model.schedule.enabled = n.checked; });
bind("tz", (n) => { model.schedule.timezone = n.value; });
bind("cons-enabled", (n) => { model.constructions.enabled = n.checked; });
bind("cons-recheck", (n) => { if (n.value !== "") model.constructions.recheck_minutes = Number(n.value); });
bind("res-enabled", (n) => { model.research.enabled = n.checked; });
bind("attack-enabled", (n) => { model.attack_alert.enabled = n.checked; });
bind("attack-interval", (n) => { if (n.value !== "") model.attack_alert.interval_seconds = Number(n.value); });

// ------------------------------------------------------------------------------------ compte
async function refreshAccount() {
  try {
    const a = await (await fetch("/api/account")).json();
    $("acct-status").replaceChildren(
      el("strong", { textContent: a.has_credentials ? `Identifiants en mémoire (${a.hint})` : "Aucun identifiant en mémoire" }),
      a.has_credentials ? " — le bot peut se reconnecter tout seul." : " — le bot ne pourra pas se connecter tout seul.",
    );
  } catch { /* idem */ }
}
/** État de la connexion automatique, mis à jour en direct (événement émis par shell.js). */
const STATE_TEXT = { idle: "Non connecté", logging_in: "Connexion en cours…", logged_in: "Connecté", error: "Connexion échouée" };
window.addEventListener("boleryos:status", (event) => {
  const { state, automation: a } = event.detail;
  const parts = [STATE_TEXT[state] || state];
  if (a.connection) parts.push(a.connection);
  $("conn-status").replaceChildren("Connexion : ", el("strong", { textContent: parts.join(" — ") }));
});
$("acct-save").addEventListener("click", async () => {
  const identifier = $("acct-id").value.trim(), password = $("acct-pw").value;
  if (!identifier || !password) { $("acct-msg").textContent = "Renseignez l'identifiant et le mot de passe."; return; }
  model.universe = $("universe").value.trim();
  await save(); // l'univers est enregistré avant que le bot ne se connecte
  const res = await fetch("/api/account/credentials", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identifier, password }) });
  $("acct-pw").value = ""; // jamais conservé dans la page
  $("acct-msg").textContent = res.ok ? "Enregistré : le bot se connecte." : "Refusé.";
  refreshAccount();
});
$("acct-login").addEventListener("click", async () => {
  const res = await fetch("/api/account/login", { method: "POST" });
  const data = await res.json();
  $("acct-msg").textContent = res.ok ? data.message : detailText(data, "Impossible.");
});
$("acct-forget").addEventListener("click", async () => {
  await fetch("/api/account/credentials", { method: "DELETE" });
  $("acct-id").value = ""; $("acct-pw").value = "";
  $("acct-msg").textContent = "Oubliés.";
  refreshAccount();
});

// ------------------------------------------------------------------------- import des réglages
$("import").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    data.mode = "dry_run"; // un fichier importé ne peut jamais activer le mode réel
    delete data.catalog;
    const res = await fetch("/api/automation/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: data, confirm_live: false }) });
    const out = await res.json();
    if (!res.ok) return say(detailText(out, "Fichier refusé."), "error");
    model = out.settings;
    catalog = out.catalog;
    renderAll();
    say("Réglages importés (simulation).", "ok");
  } catch {
    say("Fichier illisible.", "error");
  }
  event.target.value = "";
});

// ----------------------------------------------------------------------------------- alertes
/** `fill` : recopie aussi le format et les cases (au chargement et après un enregistrement réussi, pas après un refus). */
async function refreshNotifications(fill = true) {
  const n = await (await fetch("/api/notifications")).json();
  $("notif-status").replaceChildren(
    el("strong", { textContent: n.configured ? `Alertes actives vers ${n.host}` : "Aucune adresse d'alerte" }),
    n.last_error ? ` — dernière erreur : ${n.last_error}` : "",
  );
  if (fill) {
    $("notif-format").value = n.format;
    $("notif-launch").checked = !!n.events.launch;
    $("notif-attention").checked = !!n.events.attention;
    $("notif-attack").checked = !!n.events.attack;
  }
  $("notif-url").placeholder = n.configured ? "Adresse enregistrée (saisissez-en une autre pour la remplacer)" : "https://…";
  $("notif-test").disabled = $("notif-clear").disabled = !n.configured;
}
function notifMessage(text, ok) {
  $("notif-msg").textContent = text;
  $("notif-msg").className = "msg " + (ok ? "ok" : "error");
}
$("notif-save").addEventListener("click", async () => {
  const typed = $("notif-url").value.trim();
  const res = await fetch("/api/notifications", {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: typed || null, format: $("notif-format").value,
      events: { launch: $("notif-launch").checked, attention: $("notif-attention").checked, attack: $("notif-attack").checked },
    }),
  });
  const data = await res.json();
  $("notif-url").value = ""; // l'adresse n'est jamais réaffichée
  if (!res.ok) notifMessage(detailText(data, "Refusé."), false);
  else notifMessage(data.configured ? "Alertes enregistrées." : "Saisissez une adresse pour activer les alertes.", data.configured);
  refreshNotifications(res.ok);
});
$("notif-test").addEventListener("click", async () => {
  const res = await fetch("/api/notifications/test", { method: "POST" });
  const data = await res.json();
  notifMessage(res.ok ? (data.ok ? "Message de test envoyé." : `Échec de l'envoi : ${data.last_error || "inconnu"}`) : detailText(data, "Refusé."), res.ok && data.ok);
  refreshNotifications(false);
});
$("notif-clear").addEventListener("click", async () => {
  await fetch("/api/notifications", { method: "DELETE" });
  notifMessage("Adresse supprimée.", true);
  refreshNotifications();
});

// Alertes du navigateur (affichées par shell.js tant qu'une page du bot est ouverte)
(function browserAlerts() {
  const box = $("browser-alerts");
  let on = false;
  try { on = localStorage.getItem("boleryos.alerts") === "1"; } catch { /* stockage indisponible */ }
  const supported = "Notification" in window;
  box.checked = on && supported && Notification.permission === "granted";
  if (!supported) { box.disabled = true; $("browser-msg").textContent = "Ce navigateur ne gère pas les notifications."; }
  box.addEventListener("change", async () => {
    let enabled = box.checked;
    if (enabled) {
      const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      if (permission !== "granted") { enabled = false; box.checked = false; $("browser-msg").textContent = "Autorisation refusée par le navigateur."; }
      else $("browser-msg").textContent = "Actif tant qu'une page du bot reste ouverte dans ce navigateur.";
    } else {
      $("browser-msg").textContent = "";
    }
    try { localStorage.setItem("boleryos.alerts", enabled ? "1" : "0"); } catch { /* idem */ }
  });
})();

// -------------------------------------------------------------------------- mises à jour
async function refreshCatalog(force = false) {
  try {
    const data = await (await fetch("/api/automation")).json();
    catalog = data.catalog;
    const doneNow = JSON.stringify(data.status.production_done || {});
    const doneChanged = doneNow !== JSON.stringify(prodDone);
    prodDone = data.status.production_done || {};
    $("raid-done").textContent = `Attaques faites : ${data.status.raids_done} sur ${model ? model.raids.total : "?"}.`;
    $("attack-status").textContent = `État : ${data.status.attack_watch}.`;
    if (doneChanged && model && !(document.activeElement && document.activeElement.matches("input, select"))) renderProdQueue();
    $("persist").textContent = data.status.persistent
      ? "Réglages enregistrés sur le serveur (volume /data) : ils survivent à un redémarrage."
      : "Aucun volume /data : les réglages sont perdus au redémarrage (exportez-les pour les conserver).";
    if (force || catalogSignature() !== catalogKey) {
      if (!document.activeElement || !document.activeElement.matches("input, select")) renderAll();
    } else {
      refreshLevels();
    }
  } catch { /* le bot redémarre : on réessaie */ }
}

(async () => {
  const data = await (await fetch("/api/automation")).json();
  model = data.settings;
  catalog = data.catalog;
  renderAll();
  setTab(initialTab());
  refreshCatalog(false);
  refreshAccount();
  refreshNotifications();
  setInterval(refreshAccount, 8000);
  setInterval(() => refreshCatalog(false), 8000);
})();
