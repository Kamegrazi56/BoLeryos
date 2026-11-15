// Liste d'objectifs de recherche : lignes « recherche + niveau cible » (l'ordre = la priorité) et planète chargée des recherches.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);

  let researches = [];
  let planets = [];
  let state = { planet: "", queue: [] };
  let saveTimer = null;
  let saving = false; // enregistrement en cours
  let rev = 0; // change à chaque modification locale : une lecture partie avant ne doit pas écraser la liste
  let inventory = { researches: {}, read_at: null, source: "", error: "", reading: false }; // dernier relevé d'Empire › Inventaire
  let refreshing = false; // relevé demandé par le bouton en cours
  let failed = false; // le dernier enregistrement a été refusé : on garde la liste affichée jusqu'au prochain enregistrement réussi
  const HIDDEN_KEY = "boleryos.researches.hidden"; // catégories repliées, retenues d'une visite à l'autre (confort : sans stockage, tout est déplié)
  const hidden = new Set((() => { try { return JSON.parse(localStorage.getItem(HIDDEN_KEY)) || []; } catch { return []; } })());
  const remember = () => { try { localStorage.setItem(HIDDEN_KEY, JSON.stringify([...hidden])); } catch { /* stockage indisponible */ } };
  const label = (id) => (researches.find((r) => r.id === id) || { label: id }).label;

  function say(text, kind) {
    $("res-msg").textContent = text;
    $("res-msg").className = "msg " + (kind || "");
  }
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    saving = true;
    try {
      const res = await fetch("/api/researches", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
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

  function renderPlanets() {
    const select = $("res-planet");
    if (document.activeElement === select) return;
    const names = [...new Set([...planets, state.planet].filter(Boolean))];
    select.replaceChildren(el("option", { value: "", textContent: "— choisir —" }), ...names.map((n) => el("option", { value: n, textContent: n })));
    select.value = state.planet;
  }

  function renderQueue() {
    const list = $("res-queue");
    if (!state.queue.length) {
      list.replaceChildren(el("li", { className: "empty-state", textContent: "Aucune ligne : cliquez sur « +1 » à côté d'une recherche du tableau ci-dessous." }));
      return;
    }
    list.replaceChildren(...state.queue.map((line, i) => {
      const level = el("input", { type: "number", min: "1", max: "500", step: "1", value: line.level, ariaLabel: `Niveau cible de ${label(line.research)}` });
      level.addEventListener("input", () => { if (level.value !== "") { line.level = Number(level.value); scheduleSave(); } });
      const button = (text, title, onClick, disabled = false) => {
        const b = el("button", { type: "button", className: "secondary small", textContent: text, title, ariaLabel: `${title} : ${label(line.research)}`, disabled });
        b.addEventListener("click", onClick);
        return b;
      };
      const swap = (j) => { [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]]; renderQueue(); scheduleSave(); };
      const item = el("li", {},
        el("span", { className: "rank", textContent: String(i + 1) }),
        el("strong", { className: "name", textContent: label(line.research) }),
        el("label", {}, "niveau ", level),
        button("↑", "Monter", () => swap(i - 1), i === 0),
        button("↓", "Descendre", () => swap(i + 1), i === state.queue.length - 1),
        button("Supprimer", "Supprimer", () => { state.queue.splice(i, 1); renderAll(); scheduleSave(); }));
      item.dataset.research = line.research;
      return item;
    }));
  }

  /** Niveaux actuels (Empire › Inventaire) : une ligne par recherche, son objectif dans la liste, et « +1 » pour viser le niveau suivant. */
  function renderLevels() {
    const when = inventory.read_at
      ? `Relevé le ${new Date(inventory.read_at * 1000).toLocaleString("fr-FR")} (${inventory.source === "passage" ? "au passage du bot" : "à la demande"}).`
      : "Pas encore de relevé : il se fait au prochain passage du bot, ou avec le bouton.";
    $("res-inv-info").textContent = (inventory.error ? `${inventory.error} — ` : "") + (inventory.reading ? "Relevé en cours…" : when);
    $("res-inv-info").classList.toggle("warn", Boolean(inventory.error));
    $("res-inv-refresh").disabled = Boolean(inventory.reading);
    const goal = Object.fromEntries(state.queue.map((l) => [l.research, l.level]));
    const rows = [];
    ["Civil", "Militaire"].forEach((tab) => {
      const tabResearches = researches.filter((r) => r.tab === tab);
      const open = !hidden.has(tab);
      const toggle = el("button", { type: "button", className: "group-toggle", textContent: `${open ? "▾" : "▸"} ${tab} · ${tabResearches.length}`, ariaExpanded: String(open) });
      const head = el("td", { className: "inv-group", colSpan: 3 }, toggle);
      head.addEventListener("click", () => { // toute la ligne (et le bouton, au clavier) masque ou réaffiche les recherches de la catégorie
        if (hidden.has(tab)) hidden.delete(tab); else hidden.add(tab);
        remember();
        renderLevels();
      });
      rows.push(el("tr", {}, head));
      if (!open) return;
      tabResearches.forEach((r) => {
        const level = inventory.researches[r.id];
        const inList = goal[r.id] !== undefined;
        const add = el("button", {
          type: "button", className: "secondary small add-level", textContent: "+1", disabled: level === undefined || inList,
          title: level === undefined ? "Niveau inconnu : relevez d'abord les niveaux" : inList ? "Déjà dans la liste d'objectifs" : `Ajouter à la liste d'objectifs : niveau ${level + 1}`,
          ariaLabel: `Ajouter ${r.label} à la liste d'objectifs au niveau ${level === undefined ? "suivant" : level + 1}`,
        });
        add.addEventListener("click", () => {
          if (level === undefined || state.queue.some((l) => l.research === r.id)) return;
          state.queue.push({ research: r.id, level: level + 1 });
          renderAll();
          scheduleSave();
        });
        const row = el("tr", {}, el("th", { scope: "row", textContent: r.label }),
          el("td", { className: level === undefined ? "none" : "", textContent: level === undefined ? "—" : String(level) }),
          el("td", { className: "res-goal" }, inList ? `objectif ${goal[r.id]}` : "", add));
        row.dataset.research = r.id;
        rows.push(row);
      });
    });
    $("res-levels").replaceChildren(el("table", { className: "inv res-levels" },
      el("thead", {}, el("tr", {}, el("th", { scope: "col", textContent: "Recherche" }), el("th", { scope: "col", textContent: "Niveau" }), el("th", { scope: "col", textContent: "Liste d'objectifs" }))),
      el("tbody", {}, ...rows)));
  }

  function renderAll() {
    renderPlanets();
    renderQueue();
    renderLevels();
  }

  $("res-inv-refresh").addEventListener("click", async () => {
    refreshing = true;
    inventory = { ...inventory, reading: true, error: "" };
    renderLevels();
    try {
      const res = await fetch("/api/constructions/inventory/refresh", { method: "POST" });
      inventory = res.ok ? (await res.json()).inventory : { ...inventory, reading: false, error: "Relevé refusé." };
    } catch { inventory = { ...inventory, reading: false, error: "Relevé impossible : le bot ne répond pas." }; } finally { refreshing = false; }
    renderLevels();
  });

  $("res-planet").addEventListener("change", () => { state.planet = $("res-planet").value; scheduleSave(); });

  async function load() {
    try {
      const data = await (await fetch("/api/researches")).json();
      researches = data.researches;
      planets = data.planets;
      inventory = data.inventory;
      if (!saveTimer) state = data.settings;
      renderAll();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  load();
  setInterval(async () => { // les planètes arrivent après le scan, et le bot retire lui-même les lignes atteintes
    try {
      const at = rev;
      const data = await (await fetch("/api/researches")).json();
      planets = data.planets;
      const idle = at === rev && !saveTimer && !saving && !failed && !$("res-queue").contains(document.activeElement);
      const inventoryChanged = !refreshing && JSON.stringify(data.inventory) !== JSON.stringify(inventory);
      if (!refreshing) inventory = data.inventory; // pendant un relevé demandé, c'est la réponse du bouton qui fait foi
      if (idle && JSON.stringify(data.settings.queue) !== JSON.stringify(state.queue)) { state = data.settings; renderAll(); } else { renderPlanets(); if (inventoryChanged) renderLevels(); }
    } catch { /* idem */ }
  }, 5000);
})();
