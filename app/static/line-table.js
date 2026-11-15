// Liste de lignes « élément + mode + quantité + planète » et tableau « éléments × planètes » pour les créer, commun aux onglets
// Population et Composants (chacun l'appelle avec sa configuration : voir population.js et composants.js).
//
// Le tableau affiche l'effectif actuel de chaque élément sur chaque planète. Au-dessus : le mode des boutons (Max / Quantité
// définie / Nombre à atteindre), le nombre N et l'interrupteur « une seule fois / à chaque passage ». Le bouton d'une case crée
// (ou remplace, à sa place) la ligne de CETTE planète ; celui du début de ligne le fait sur chaque planète. L'ordre de la liste
// est la priorité ; le bot retire lui-même les lignes faites, la liste se rafraîchit donc toute seule.
(() => {
  const { el } = window.ui;
  const MAX_AMOUNT = 1_000_000_000;
  const MAX_LINES = 500; // limite de l'API : une ligne par élément et par planète
  const NO_PLANET = "Cochez au moins une planète, ou choisissez « Toutes les planètes ».";
  const MODES = [["max", "Max"], ["fixed", "Quantité définie"], ["target", "Nombre à atteindre"]];
  const fmt = (n) => n.toLocaleString("fr-FR");
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");
  const stored = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const store = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* stockage indisponible */ } };

  /**
   * cfg : {
   *   prefix: "pop" | "cmp"            préfixe des identifiants de la page (pop-queue, pop-tools, pop-inv…),
   *   api, refreshUrl,                 adresses de la liste et du relevé,
   *   key: "population" | "item",      clé de l'élément dans une ligne (et dans les attributs data-*),
   *   storageKey,                      base des clés du navigateur (catégories repliées, réglages des boutons),
   *   verb: "Recruter" | "Produire",
   *   catalogOf(data) -> [{id, label, group}],
   *   emptyInventory,                  forme du relevé avant la première lecture,
   *   columnsOf(inventory, planets) -> [{name, coords, counts: {id: effectif}}],
   *   planetsOf(inventory, planets) -> noms de planètes proposés dans le choix des planètes d'une ligne,
   *   text: { queueEmpty, noColumns, noReading, onPass, column }
   * }
   */
  window.lineTable = (cfg) => {
    const $ = (s) => document.getElementById(`${cfg.prefix}-${s}`);
    const HIDDEN_KEY = `${cfg.storageKey}.hidden`; // catégories repliées, retenues d'une visite à l'autre (confort : sans stockage, tout est déplié)
    const TOOLS_KEY = `${cfg.storageKey}.tools`;
    const hidden = new Set(stored(HIDDEN_KEY, []));

    let catalog = [];
    let planets = [];
    let state = { queue: [] };
    let saveTimer = null;
    let saving = false; // enregistrement en cours
    let rev = 0; // change à chaque modification locale : une lecture partie avant ne doit pas écraser la liste
    let inventory = { ...cfg.emptyInventory, read_at: null, source: "", error: "", reading: false }; // dernier relevé
    let refreshing = false; // relevé demandé par le bouton en cours
    let failed = false; // le dernier enregistrement a été refusé : on garde la liste affichée jusqu'au prochain enregistrement réussi
    let shownColumns = ""; // colonnes du tableau affiché (JSON) : il est redessiné quand elles changent
    const label = (id) => (catalog.find((p) => p.id === id) || { label: id }).label;
    const columnsKey = () => JSON.stringify(cfg.columnsOf(inventory, planets).map((c) => c.name));

    function say(text, kind) {
      $("msg").textContent = text;
      $("msg").className = "msg " + (kind || "");
    }

    async function save() {
      saveTimer = null;
      saving = true;
      try {
        const res = await fetch(cfg.api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
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

    /** La liste est en cours de modification (champ actif ou choix des planètes ouvert) : on ne la redessine pas. */
    const editing = () => $("queue").contains(document.activeElement) || !!$("queue").querySelector("details[open]");

    function renderQueue() {
      const list = $("queue");
      if (!state.queue.length) {
        list.replaceChildren(el("li", { className: "empty-state", textContent: cfg.text.queueEmpty }));
        return;
      }
      list.replaceChildren(...state.queue.map((line, i) => {
        const name = label(line[cfg.key]);
        const amount = el("input", { type: "number", min: "1", step: "1", value: line.amount || 1, ariaLabel: `Quantité de ${name}`, disabled: line.mode === "max" });
        const choice = window.modeChoice({ name, mode: line.mode, repeat: line.repeat, onChange: () => {
          line.mode = choice.mode();
          line.repeat = choice.repeat();
          amount.disabled = line.mode === "max";
          if (line.mode !== "max" && !line.amount) line.amount = 1;
          renderTable();
          scheduleSave();
        } });
        const where = window.planetChoice({ planets: cfg.planetsOf(inventory, planets), selected: line.planets, name, compact: true, onChange: () => {
          const chosen = where.value();
          if (chosen === null) { say(NO_PLANET, "error"); return; } // rien coché : la ligne garde ses planètes
          line.planets = chosen;
          renderTable();
          scheduleSave();
        } });
        amount.addEventListener("input", () => { if (amount.value !== "") { line.amount = Number(amount.value); renderTable(); scheduleSave(); } });
        const button = (text, title, onClick, disabled = false) => {
          const b = el("button", { type: "button", className: "secondary small", textContent: text, title, ariaLabel: `${title} : ${name}`, disabled });
          b.addEventListener("click", onClick);
          return b;
        };
        const swap = (j) => { [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]]; renderQueue(); scheduleSave(); };
        const item = el("li", {},
          el("span", { className: "rank", textContent: String(i + 1) }),
          el("strong", { className: "name", textContent: name }),
          choice.select, choice.node, el("label", {}, "quantité ", amount), where.node,
          button("↑", "Monter", () => swap(i - 1), i === 0),
          button("↓", "Descendre", () => swap(i + 1), i === state.queue.length - 1),
          button("Supprimer", "Supprimer", () => { state.queue.splice(i, 1); renderAll(); scheduleSave(); }));
        item.dataset[cfg.key] = line[cfg.key];
        return item;
      }));
    }

    /** Ligne propre à une planète (la seule planète de la ligne), sinon undefined. */
    const ownLine = (id, planet) => state.queue.find((l) => l[cfg.key] === id && l.planets.length === 1 && l.planets[0] === planet);
    /** Ligne en vigueur sur une planète : la plus précise l'emporte (une planète, puis quelques planètes, puis toutes), comme pour le bot. */
    function lineOf(id, planet) {
      const mine = state.queue.filter((l) => l[cfg.key] === id && (!l.planets.length || l.planets.includes(planet)));
      const named = mine.filter((l) => l.planets.length).sort((a, b) => a.planets.length - b.planets.length); // tri stable
      return named[0] || mine[0];
    }
    /** Effectif voulu sur une case : seulement pour une ligne « Nombre à atteindre ». */
    const goalOf = (id, planet) => { const line = lineOf(id, planet); return line && line.mode === "target" ? line.amount : undefined; };

    // Réglages des boutons du tableau (au-dessus du tableau) : ce qu'une ligne créée par un clic demande au bot.
    const validAmount = (n) => Number.isInteger(n) && n >= 1 && n <= MAX_AMOUNT;
    let tools = (() => {
      const saved = stored(TOOLS_KEY, null);
      const ok = saved && MODES.some(([m]) => m === saved.mode) && validAmount(saved.amount) && typeof saved.repeat === "boolean";
      return ok ? saved : { mode: "target", amount: 1, repeat: true };
    })();
    const rememberTools = () => store(TOOLS_KEY, tools);
    /** Les boutons du tableau sont utilisables : « Max » n'a pas de quantité, les deux autres modes ont besoin d'un entier d'au moins 1. */
    const toolsReady = () => tools.mode === "max" || validAmount(tools.amount);
    /** Texte court du bouton selon le mode choisi. */
    const toolText = () => (tools.mode === "max" ? "MAX" : !validAmount(tools.amount) ? "…" : tools.mode === "fixed" ? `+${fmt(tools.amount)}` : `→ ${fmt(tools.amount)}`);
    const toolSentence = (what, planetName) => (tools.mode === "max" ? `${cfg.verb} le maximum de ${what} sur ${planetName}` : tools.mode === "fixed"
      ? `${cfg.verb} ${fmt(tools.amount)} ${what} sur ${planetName}` : `Viser ${fmt(tools.amount)} ${what} sur ${planetName}`)
      + (tools.mode === "target" ? "" : tools.repeat ? ", à chaque passage" : ", une seule fois");
    /** Phrase décrivant une ligne existante (info-bulle des cases qui ne sont pas en « Nombre à atteindre »). */
    const lineSentence = (line) => (line.mode === "max" ? `${cfg.verb} le maximum` : `${cfg.verb} ${fmt(line.amount)}`) + (line.repeat ? " à chaque passage" : " une seule fois")
      + (line.planets.length ? "" : " (toutes les planètes)");

    /** Crée (ou remplace, à sa place dans la liste) la ligne de cet élément sur cette planète selon les réglages des boutons.
     * Rend true si la liste a changé. */
    function apply(id, planet) {
      const line = {
        [cfg.key]: id, mode: tools.mode, amount: tools.mode === "max" ? 0 : tools.amount, planets: [planet],
        repeat: tools.mode === "target" ? false : tools.repeat, // un effectif à atteindre ne se répète jamais
      };
      const own = ownLine(id, planet);
      if (!own) {
        if (state.queue.length >= MAX_LINES) { say(`La liste est pleine (${MAX_LINES} lignes au plus).`, "error"); return false; }
        state.queue.push(line);
        return true;
      }
      if (own.mode === line.mode && own.amount === line.amount && own.repeat === line.repeat) return false;
      Object.assign(own, line);
      return true;
    }

    /** Barre au-dessus du tableau : mode des boutons, quantité, une fois / à chaque passage. */
    function renderTools() {
      const radios = MODES.map(([value, text]) => {
        const input = el("input", { type: "radio", name: `${cfg.prefix}-tool-mode`, value, checked: tools.mode === value });
        input.addEventListener("change", () => { tools.mode = value; rememberTools(); syncTools(); renderTable(); });
        return el("label", {}, input, ` ${text}`);
      });
      const group = el("div", { className: "repeat-choice", role: "radiogroup", ariaLabel: "Action des boutons du tableau" }, ...radios);
      const amount = el("input", { id: `${cfg.prefix}-tool-amount`, type: "number", min: "1", max: String(MAX_AMOUNT), step: "1", value: tools.amount });
      amount.addEventListener("input", () => {
        tools.amount = amount.value === "" ? NaN : Number(amount.value); // champ vidé : les boutons se grisent au lieu de garder l'ancien nombre
        if (validAmount(tools.amount)) rememberTools();
        renderTable();
      });
      const amountLabel = el("label", { id: `${cfg.prefix}-tool-amount-label` }, el("span"), " ", amount);
      const repeat = el("input", { id: `${cfg.prefix}-tool-repeat`, type: "checkbox", checked: tools.repeat });
      repeat.addEventListener("change", () => { tools.repeat = repeat.checked; rememberTools(); syncTools(); renderTable(); });
      const repeatText = el("span");
      const repeatLabel = el("label", { className: "switch", id: `${cfg.prefix}-tool-repeat-label` }, repeat, repeatText);
      function syncTools() {
        amountLabel.hidden = tools.mode === "max"; // MAX : pas de quantité
        amountLabel.firstChild.textContent = tools.mode === "fixed" ? "quantité" : "effectif voulu";
        amount.setAttribute("aria-label", tools.mode === "fixed" ? "Quantité à chaque clic" : "Effectif à atteindre à chaque clic");
        repeat.disabled = tools.mode === "target";
        repeatLabel.classList.toggle("is-disabled", tools.mode === "target");
        repeatLabel.title = tools.mode === "target" ? "Un effectif à atteindre ne se répète pas : la ligne disparaît une fois l'objectif atteint." : "";
        repeatText.textContent = tools.mode === "target" ? " Une seule fois (effectif à atteindre)" : repeat.checked ? " À chaque passage de la boucle" : " Une seule fois";
      }
      syncTools();
      $("tools").replaceChildren(group, amountLabel, repeatLabel);
    }

    /** Effectifs actuels : une ligne par élément, une colonne par planète ; un bouton sur chaque case (cette planète) et en début de
     * ligne (toutes les planètes) ; case teintée selon l'objectif ; catégories repliables. */
    function renderTable() {
      const when = inventory.read_at
        ? `Relevé le ${new Date(inventory.read_at * 1000).toLocaleString("fr-FR")} (${inventory.source === "passage" ? cfg.text.onPass : "à la demande"}).`
        : cfg.text.noReading;
      $("inv-info").textContent = (inventory.error ? `${inventory.error} — ` : "") + (inventory.reading ? "Relevé en cours…" : when);
      $("inv-info").classList.toggle("warn", Boolean(inventory.error));
      $("inv-refresh").disabled = Boolean(inventory.reading);
      const box = $("inv");
      const cols = cfg.columnsOf(inventory, planets);
      shownColumns = JSON.stringify(cols.map((c) => c.name));
      if (!cols.length) {
        box.replaceChildren(el("p", { className: "empty-state", textContent: cfg.text.noColumns }));
        return;
      }
      const ready = toolsReady();
      const hint = "Saisissez d'abord un nombre entier d'au moins 1 au-dessus du tableau";
      const head = el("tr", {}, el("th", { scope: "col", textContent: cfg.text.column }),
        ...cols.map((p) => el("th", { scope: "col", title: p.coords, textContent: p.name })));
      const body = [];
      [...new Set(catalog.map((c) => c.group))].forEach((group) => {
        const members = catalog.filter((c) => c.group === group);
        const open = !hidden.has(group);
        const toggle = el("button", { type: "button", className: "group-toggle", textContent: `${open ? "▾" : "▸"} ${group} · ${members.length}`, ariaExpanded: String(open) });
        const groupCell = el("td", { className: "inv-group", colSpan: cols.length + 1 }, toggle);
        groupCell.addEventListener("click", () => { // toute la ligne (et le bouton, au clavier) masque ou réaffiche les éléments de la catégorie
          if (hidden.has(group)) hidden.delete(group); else hidden.add(group);
          store(HIDDEN_KEY, [...hidden]);
          renderTable();
        });
        body.push(el("tr", {}, groupCell));
        if (!open) return;
        members.forEach((p) => {
          const rowSentence = toolSentence(p.label, "chaque planète");
          const rowAdd = el("button", { type: "button", className: "secondary small add-level", textContent: toolText(), disabled: !ready, title: ready ? rowSentence : hint, ariaLabel: rowSentence });
          rowAdd.addEventListener("click", () => {
            const changed = cols.map((c) => apply(p.id, c.name)).some(Boolean); // chaque planète a sa propre ligne
            if (changed) { renderAll(); scheduleSave(); }
          });
          const row = el("tr", {}, el("th", { scope: "row", className: "bname" }, rowAdd, el("span", { textContent: p.label })), ...cols.map((c) => {
            const count = c.counts[p.id];
            const goal = goalOf(p.id, c.name);
            const cls = count === undefined ? "none" : goal === undefined ? "" : count >= goal ? "reached" : "short";
            const sentence = toolSentence(p.label, c.name);
            const add = el("button", { type: "button", className: "secondary small add-level", textContent: toolText(), disabled: !ready, title: ready ? sentence : hint, ariaLabel: sentence });
            add.addEventListener("click", () => { if (apply(p.id, c.name)) { renderAll(); scheduleSave(); } });
            const text = count === undefined ? "—" : goal !== undefined && goal > count ? `${fmt(count)} → ${fmt(goal)}` : fmt(count);
            const line = lineOf(p.id, c.name);
            const tag = line && line.mode !== "target"
              ? el("span", { className: "tag", title: lineSentence(line), textContent: `${line.mode === "max" ? "MAX" : "+" + fmt(line.amount)} ${line.repeat ? "↻" : "1×"}` }) : "";
            const cell = el("td", { className: cls, title: goal !== undefined ? `objectif : ${fmt(goal)}` : "" }, el("span", { className: "lv", textContent: text }), tag, add);
            cell.dataset.planet = c.name;
            return cell;
          }));
          row.dataset[cfg.key] = p.id;
          body.push(row);
        });
      });
      box.replaceChildren(el("table", { className: "inv cons-levels" }, el("thead", {}, head), el("tbody", {}, ...body)));
    }

    function renderAll() {
      renderQueue();
      renderTable();
    }

    $("inv-refresh").addEventListener("click", async () => {
      refreshing = true;
      inventory = { ...inventory, reading: true, error: "" };
      renderTable();
      try {
        const res = await fetch(cfg.refreshUrl, { method: "POST" });
        inventory = res.ok ? (await res.json()).inventory : { ...inventory, reading: false, error: "Relevé refusé." };
      } catch { inventory = { ...inventory, reading: false, error: "Relevé impossible : le bot ne répond pas." }; } finally { refreshing = false; }
      renderTable();
    });

    renderTools();
    async function load() {
      try {
        const data = await (await fetch(cfg.api)).json();
        catalog = cfg.catalogOf(data);
        planets = data.planets;
        inventory = data.inventory;
        if (!saveTimer) state = data.settings;
        renderAll();
      } catch { /* le bot redémarre : on réessaie */ }
    }
    load();
    setInterval(async () => { // les planètes arrivent après le scan, et le bot retire lui-même les lignes faites
      try {
        const at = rev;
        const data = await (await fetch(cfg.api)).json();
        planets = data.planets;
        const idle = at === rev && !saveTimer && !saving && !failed && !editing();
        const inventoryChanged = !refreshing && JSON.stringify(data.inventory) !== JSON.stringify(inventory);
        if (!refreshing) inventory = data.inventory; // pendant un relevé demandé, c'est la réponse du bouton qui fait foi
        if (idle && JSON.stringify(data.settings.queue) !== JSON.stringify(state.queue)) { state = data.settings; renderAll(); }
        else { if (!editing()) renderQueue(); if (inventoryChanged || columnsKey() !== shownColumns) renderTable(); }
      } catch { /* idem */ }
    }, 5000);
  };
})();
