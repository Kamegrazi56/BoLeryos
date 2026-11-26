// Liste d'objectifs de construction : lignes « bâtiment + niveau cible » (l'ordre = la priorité), affichées par planète, sur toutes les
// planètes ou sur les planètes cochées (choix commun : planet-choice.js).
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const NO_PLANET = "Cochez au moins une planète, ou choisissez « Toutes les planètes ».";

  let buildings = [];
  let planets = [];
  let state = { planets: [], queue: [], modes: {} };
  let planetBox = null; // choix des planètes affiché
  let shown = null; // planètes qu'il affiche (JSON)
  let saveTimer = null;
  let saving = false; // enregistrement en cours
  let rev = 0; // change à chaque modification locale : une lecture partie avant ne doit pas écraser la liste
  let inventory = { planets: [], read_at: null, source: "", error: "", reading: false }; // dernier relevé d'Empire › Inventaire
  let refreshing = false; // relevé demandé par le bouton en cours
  let failed = false; // le dernier enregistrement a été refusé : on garde la liste affichée jusqu'au prochain enregistrement réussi
  const MAX_LEVEL = 500;
  const HIDDEN_KEY = "boleryos.constructions.hidden"; // catégories repliées, retenues d'une visite à l'autre (confort : sans stockage, tout est déplié)
  const hidden = new Set((() => { try { return JSON.parse(localStorage.getItem(HIDDEN_KEY)) || []; } catch { return []; } })());
  const remember = () => { try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden])); } catch { /* stockage indisponible */ } };
  const MODES = [["any", "Dès que les ressources suffisent"], ["order", "Ordre de la liste"]]; // « any » : comportement d'origine, donc par défaut
  const modeOf = (planet) => (state.modes[planet] || (planet ? state.modes[""] : "") || "any");
  const groupKey = (planet) => `queue:${planet}`; // même mémoire que les catégories du tableau : une planète repliée le reste d'une visite à l'autre
  const label = (id) => (buildings.find((b) => b.id === id) || { label: id }).label;

  const withModes = (settings) => ({ ...settings, modes: { ...(settings.modes || {}) } });

  function say(text, kind) {
    $("cons-msg").textContent = text;
    $("cons-msg").className = "msg " + (kind || "");
  }
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    saving = true;
    try {
      const res = await fetch("/api/constructions", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) }); // « modes » est ignoré par le serveur s'il est vide
      if (res.ok) { failed = false; say("Enregistré.", "ok"); return; }
      failed = true; // la liste affichée n'est pas celle du bot : le rafraîchissement automatique ne doit pas effacer la modification refusée
      say(errorText(await res.json().catch(() => ({}))), "error");
    } catch { failed = true; say("Enregistrement impossible : le bot ne répond pas.", "error"); } finally { saving = false; rev++; }
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

  /** Lignes regroupées par planète, en gardant leur rang dans la liste enregistrée (la priorité d'une planète = l'ordre de ses lignes).
   * Ordre des groupes : « toutes les planètes » d'abord, puis les planètes dans l'ordre du scan, puis les planètes inconnues. */
  function groupQueue() {
    const groups = new Map();
    state.queue.forEach((line, index) => {
      const key = line.planet || "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ line, index });
    });
    const rank = (key) => (key === "" ? -1 : planets.includes(key) ? planets.indexOf(key) : planets.length);
    return [...groups.entries()].sort((x, y) => rank(x[0]) - rank(y[0]));
  }

  /** Titre d'une planète : un bouton qui plie / déplie ses lignes, et le choix du mode de lancement de cette planète. */
  function groupHead(planet, count, open) {
    const name = planet || "Toutes les planètes";
    const toggle = el("button", { type: "button", className: "group-toggle", textContent: `${open ? "▾" : "▸"} ${name} · ${count}`, ariaExpanded: String(open), ariaLabel: `${open ? "Masquer" : "Afficher"} les constructions de ${name}` });
    toggle.addEventListener("click", () => {
      if (hidden.has(groupKey(planet))) hidden.delete(groupKey(planet)); else hidden.add(groupKey(planet));
      remember();
      renderQueue();
    });
    const select = el("select", { className: "mode", ariaLabel: `Mode de lancement : ${name}`, title: "Ordre de la liste : on attend les ressources de la première ligne avant de lancer la suivante. Dès que les ressources suffisent : on lance la première ligne que l'on peut payer." },
      ...MODES.map(([value, text]) => el("option", { value, textContent: text })));
    select.value = modeOf(planet);
    select.addEventListener("change", () => { state.modes[planet] = select.value; scheduleSave(); });
    const head = el("li", { className: "group-head" }, toggle, select);
    head.dataset.groupPlanet = planet;
    return head;
  }

  function renderQueue() {
    const list = $("cons-queue");
    if (!state.queue.length) {
      list.replaceChildren(el("li", { className: "empty-state", textContent: "Aucune ligne : cliquez sur « +1 » dans le tableau ci-dessous." }));
      return;
    }
    const swap = (i, j) => { [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]]; renderQueue(); scheduleSave(); };
    const items = [];
    groupQueue().forEach(([planet, members]) => {
      const open = !hidden.has(groupKey(planet));
      items.push(groupHead(planet, members.length, open));
      if (!open) return; // planète repliée : ses lignes en attente sont masquées (elles restent dans la liste du bot)
      members.forEach(({ line, index }, pos) => {
        const where = line.planet || "toutes les planètes";
        const name = `${label(line.building)} (${where})`;
        const level = el("input", { type: "number", min: "1", max: String(MAX_LEVEL), step: "1", value: line.level, ariaLabel: `Niveau cible de ${name}` });
        level.addEventListener("input", () => { if (level.value !== "") { line.level = Number(level.value); renderInventory(); scheduleSave(); } });
        const button = (text, title, onClick, disabled = false) => {
          const b = el("button", { type: "button", className: "secondary small", textContent: text, title, ariaLabel: `${title} : ${name}`, disabled });
          b.addEventListener("click", onClick);
          return b;
        };
        const before = members[pos - 1]; // on ne monte / descend qu'au sein de la planète : la priorité est propre à chaque planète
        const after = members[pos + 1];
        const item = el("li", {},
          el("span", { className: "rank", textContent: String(pos + 1) }),
          el("strong", { className: "name", textContent: label(line.building) }),
          el("label", {}, "niveau ", level),
          button("↑", "Monter", () => swap(index, before.index), !before),
          button("↓", "Descendre", () => swap(index, after.index), !after),
          button("Supprimer", "Supprimer", () => { state.queue.splice(index, 1); renderAll(); scheduleSave(); }));
        item.dataset.building = line.building;
        item.dataset.planet = line.planet || "";
        items.push(item);
      });
    });
    list.replaceChildren(...items);
  }

  const inScope = (planet) => !state.planets.length || state.planets.includes(planet);
  const ownLine = (building, planet) => state.queue.find((l) => l.building === building && l.planet === planet);
  const commonLine = (building) => state.queue.find((l) => l.building === building && !l.planet);
  /** Objectif en vigueur sur une case : la ligne propre à la planète, sinon la ligne « toutes les planètes » si la planète est visée. */
  function goalOf(building, planet) {
    const own = ownLine(building, planet);
    if (own) return own.level;
    const common = commonLine(building);
    return common && inScope(planet) ? common.level : undefined;
  }

  /** « +1 » d'une case : l'objectif de cette planète passe au-dessus du plus haut du niveau actuel et de l'objectif en vigueur
   * (on peut donc cliquer plusieurs fois de suite). Rend true si la liste a changé. */
  function raise(building, planet, level) {
    if (level === undefined) return false;
    const next = Math.max(level, goalOf(building, planet) || 0) + 1;
    if (next > MAX_LEVEL) return false;
    const own = ownLine(building, planet);
    if (own) own.level = next; else state.queue.push({ building, planet, level: next });
    return true;
  }

  /** Niveaux actuels (Empire › Inventaire) : une ligne par bâtiment, une colonne par planète ; « +1 » sur chaque case (cette planète)
   * et en début de ligne (toutes les planètes) ; case teintée selon l'objectif ; catégories repliables. */
  function renderInventory() {
    const when = inventory.read_at
      ? `Relevé le ${new Date(inventory.read_at * 1000).toLocaleString("fr-FR")} (${inventory.source === "passage" ? "au passage des constructions" : "à la demande"}).`
      : "Pas encore de relevé : il se fait au prochain passage des constructions, ou avec le bouton.";
    $("cons-inv-info").textContent = (inventory.error ? `${inventory.error} — ` : "") + (inventory.reading ? "Relevé en cours…" : when);
    $("cons-inv-info").classList.toggle("warn", Boolean(inventory.error));
    $("cons-inv-refresh").disabled = Boolean(inventory.reading);
    const box = $("cons-inv");
    if (!inventory.planets.length) {
      box.replaceChildren(el("p", { className: "empty-state", textContent: "Aucun niveau relevé pour l'instant." }));
      return;
    }
    const cols = inventory.planets;
    const head = el("tr", {}, el("th", { scope: "col", textContent: "Bâtiment" }),
      ...cols.map((p) => el("th", { scope: "col", title: p.coords, textContent: p.name })));
    const body = [];
    ["Industrie", "Infrastructures"].forEach((page) => {
      const pageBuildings = buildings.filter((b) => b.page === page);
      const open = !hidden.has(page);
      const toggle = el("button", { type: "button", className: "group-toggle", textContent: `${open ? "▾" : "▸"} ${page} · ${pageBuildings.length}`, ariaExpanded: String(open) });
      const groupCell = el("td", { className: "inv-group", colSpan: cols.length + 1 }, toggle);
      groupCell.addEventListener("click", () => { // toute la ligne (et le bouton, au clavier) masque ou réaffiche les bâtiments de la catégorie
        if (hidden.has(page)) hidden.delete(page); else hidden.add(page);
        remember();
        renderInventory();
      });
      body.push(el("tr", {}, groupCell));
      if (!open) return;
      pageBuildings.forEach((b) => {
        const known = cols.some((p) => p.levels[b.id] !== undefined);
        const rowAdd = el("button", {
          type: "button", className: "secondary small add-level", textContent: "+1", disabled: !known,
          title: known ? `Monter ${b.label} d'un niveau sur chaque planète` : "Niveaux inconnus : relevez d'abord les niveaux",
          ariaLabel: `Monter ${b.label} d'un niveau sur chaque planète`,
        });
        rowAdd.addEventListener("click", () => {
          const changed = cols.map((p) => raise(b.id, p.name, p.levels[b.id])).some(Boolean); // chaque planète : son niveau + 1
          if (changed) { renderAll(); scheduleSave(); }
        });
        const row = el("tr", {}, el("th", { scope: "row", className: "bname" }, rowAdd, el("span", { textContent: b.label })), ...cols.map((p) => {
          const level = p.levels[b.id];
          const goal = goalOf(b.id, p.name);
          const cls = level === undefined ? "none" : goal === undefined ? "" : level >= goal ? "reached" : "short";
          const add = el("button", {
            type: "button", className: "secondary small add-level", textContent: "+1", disabled: level === undefined,
            title: level === undefined ? "Niveau inconnu" : `Monter ${b.label} d'un niveau sur ${p.name}`,
            ariaLabel: `Monter ${b.label} d'un niveau sur ${p.name}`,
          });
          add.addEventListener("click", () => { if (raise(b.id, p.name, level)) { renderAll(); scheduleSave(); } });
          const text = level === undefined ? "—" : goal !== undefined && goal > level ? `${level} → ${goal}` : String(level);
          const cell = el("td", { className: cls, title: goal !== undefined ? `objectif : niveau ${goal}` : "" }, el("span", { className: "lv", textContent: text }), add);
          cell.dataset.planet = p.name;
          return cell;
        }));
        row.dataset.building = b.id;
        body.push(row);
      });
    });
    box.replaceChildren(el("table", { className: "inv cons-levels" }, el("thead", {}, head), el("tbody", {}, ...body)));
  }

  function renderAll() {
    renderPlanets();
    renderQueue();
    renderInventory();
  }

  $("cons-inv-refresh").addEventListener("click", async () => {
    refreshing = true;
    inventory = { ...inventory, reading: true, error: "" };
    renderInventory();
    try {
      const res = await fetch("/api/constructions/inventory/refresh", { method: "POST" });
      inventory = res.ok ? (await res.json()).inventory : { ...inventory, reading: false, error: "Relevé refusé." };
    } catch { inventory = { ...inventory, reading: false, error: "Relevé impossible : le bot ne répond pas." }; } finally { refreshing = false; }
    renderInventory();
  });

  async function load() {
    try {
      const data = await (await fetch("/api/constructions")).json();
      buildings = data.buildings;
      planets = data.planets;
      inventory = data.inventory;
      if (!saveTimer) state = withModes(data.settings);
      renderAll();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  load();
  setInterval(async () => { // les planètes arrivent après le scan, et le bot retire lui-même les lignes atteintes
    try {
      const at = rev;
      const data = await (await fetch("/api/constructions")).json();
      planets = data.planets;
      const idle = at === rev && !saveTimer && !saving && !failed && !$("cons-queue").contains(document.activeElement);
      const inventoryChanged = JSON.stringify(data.inventory) !== JSON.stringify(inventory);
      if (!refreshing) inventory = data.inventory; // pendant un relevé demandé, c'est la réponse du bouton qui fait foi
      const changed = JSON.stringify([data.settings.queue, data.settings.modes || {}]) !== JSON.stringify([state.queue, state.modes]);
      if (idle && changed) { state = withModes(data.settings); renderAll(); } else { renderPlanets(); if (inventoryChanged) renderInventory(); }
    } catch { /* idem */ }
  }, 5000);
})();
