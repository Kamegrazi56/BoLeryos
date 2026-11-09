// Liste d'objectifs de construction : lignes « bâtiment + niveau cible » (l'ordre = la priorité), sur toutes les
// planètes ou sur les planètes cochées (choix commun : planet-choice.js).
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const NO_PLANET = "Cochez au moins une planète, ou choisissez « Toutes les planètes ».";

  let buildings = [];
  let planets = [];
  let state = { planets: [], queue: [] };
  let planetBox = null; // choix des planètes affiché
  let shown = null; // planètes qu'il affiche (JSON)
  let saveTimer = null;
  let saving = false; // enregistrement en cours
  let rev = 0; // change à chaque modification locale : une lecture partie avant ne doit pas écraser la liste
  const label = (id) => (buildings.find((b) => b.id === id) || { label: id }).label;

  function say(text, kind) {
    $("cons-msg").textContent = text;
    $("cons-msg").className = "msg " + (kind || "");
  }
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    saving = true;
    try {
      const res = await fetch("/api/constructions", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
      if (res.ok) { say("Enregistré.", "ok"); return; }
      say(errorText(await res.json().catch(() => ({}))), "error");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); } finally { saving = false; rev++; }
  }
  function scheduleSave() {
    clearTimeout(saveTimer);
    rev++;
    say("Enregistrement…");
    saveTimer = setTimeout(save, 500);
  }

  /** Choix des planètes : recréé seulement si les réglages enregistrés ont changé ; sinon, juste la liste des planètes. */
  function renderPlanets() {
    const key = JSON.stringify(state.planets);
    if (planetBox && (key === shown || $("cons-planets").contains(document.activeElement))) {
      planetBox.setPlanets(planets);
      return;
    }
    planetBox = window.planetChoice({ planets, selected: state.planets, name: "les constructions", onChange: () => {
      const chosen = planetBox.value();
      if (chosen === null) { say(NO_PLANET, "error"); return; } // rien coché : les réglages ne changent pas
      state.planets = chosen;
      shown = JSON.stringify(chosen);
      scheduleSave();
    } });
    shown = key;
    $("cons-planets").replaceChildren(planetBox.node);
  }

  function renderQueue() {
    const list = $("cons-queue");
    if (!state.queue.length) {
      list.replaceChildren(el("li", { className: "empty-state", textContent: "Aucune ligne : ajoutez un bâtiment ci-dessous." }));
      return;
    }
    list.replaceChildren(...state.queue.map((line, i) => {
      const level = el("input", { type: "number", min: "1", max: "500", step: "1", value: line.level, ariaLabel: `Niveau cible de ${label(line.building)}` });
      level.addEventListener("input", () => { if (level.value !== "") { line.level = Number(level.value); scheduleSave(); } });
      const button = (text, title, onClick, disabled = false) => {
        const b = el("button", { type: "button", className: "secondary small", textContent: text, title, ariaLabel: `${title} : ${label(line.building)}`, disabled });
        b.addEventListener("click", onClick);
        return b;
      };
      const swap = (j) => { [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]]; renderQueue(); scheduleSave(); };
      const item = el("li", {},
        el("span", { className: "rank", textContent: String(i + 1) }),
        el("strong", { className: "name", textContent: label(line.building) }),
        el("label", {}, "niveau ", level),
        button("↑", "Monter", () => swap(i - 1), i === 0),
        button("↓", "Descendre", () => swap(i + 1), i === state.queue.length - 1),
        button("Supprimer", "Supprimer", () => { state.queue.splice(i, 1); renderAll(); scheduleSave(); }));
      item.dataset.building = line.building;
      return item;
    }));
  }

  function renderAddOptions() {
    const select = $("cons-add-building");
    const used = new Set(state.queue.map((l) => l.building));
    const groups = ["Industrie", "Infrastructures"].map((page) => {
      const group = el("optgroup", { label: page });
      buildings.filter((b) => b.page === page && !used.has(b.id)).forEach((b) => group.append(el("option", { value: b.id, textContent: b.label })));
      return group;
    }).filter((g) => g.children.length);
    select.replaceChildren(...groups);
    $("cons-add").disabled = !groups.length;
  }

  function renderAll() {
    renderPlanets();
    renderQueue();
    renderAddOptions();
  }

  $("cons-add").addEventListener("click", () => {
    const building = $("cons-add-building").value;
    const level = Number($("cons-add-level").value);
    if (!building) return;
    if (!Number.isInteger(level) || level < 1) { say("Le niveau cible est un nombre entier d'au moins 1.", "error"); return; }
    state.queue.push({ building, level });
    renderAll();
    scheduleSave();
  });

  async function load() {
    try {
      const data = await (await fetch("/api/constructions")).json();
      buildings = data.buildings;
      planets = data.planets;
      if (!saveTimer) state = data.settings;
      renderAll();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  load();
  setInterval(async () => { // les planètes arrivent après le scan, et le bot retire lui-même les lignes atteintes
    try {
      const at = rev;
      const data = await (await fetch("/api/constructions")).json();
      planets = data.planets;
      const idle = at === rev && !saveTimer && !saving && !$("cons-queue").contains(document.activeElement);
      if (idle && JSON.stringify(data.settings.queue) !== JSON.stringify(state.queue)) { state = data.settings; renderAll(); } else renderPlanets();
    } catch { /* idem */ }
  }, 5000);
})();
