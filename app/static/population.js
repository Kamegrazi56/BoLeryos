// Recrutement de population : une ligne par (population, planète) avec son mode (quantité fixe / MAX / effectif à atteindre),
// sa quantité et sa planète (« toutes » ou une seule). L'ordre de la liste est la priorité.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);

  let populations = [];
  let planets = [];
  let state = { queue: [] };
  let saveTimer = null;
  let saving = false; // enregistrement en cours
  let rev = 0; // change à chaque modification locale : une lecture partie avant ne doit pas écraser la liste
  const label = (id) => (populations.find((p) => p.id === id) || { label: id }).label;

  function say(text, kind) {
    $("pop-msg").textContent = text;
    $("pop-msg").className = "msg " + (kind || "");
  }
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    saving = true;
    try {
      const res = await fetch("/api/populations", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
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

  const NO_PLANET = "Cochez au moins une planète, ou choisissez « Toutes les planètes ».";
  /** La liste est en cours de modification (champ actif ou choix des planètes ouvert) : on ne la redessine pas. */
  const editing = () => $("pop-queue").contains(document.activeElement) || !!$("pop-queue").querySelector("details[open]");

  function renderQueue() {
    const list = $("pop-queue");
    if (!state.queue.length) {
      list.replaceChildren(el("li", { className: "empty-state", textContent: "Aucune ligne : ajoutez une population ci-dessous." }));
      return;
    }
    list.replaceChildren(...state.queue.map((line, i) => {
      const name = label(line.population);
      const amount = el("input", { type: "number", min: "1", step: "1", value: line.amount || 1, ariaLabel: `Quantité de ${name}`, disabled: line.mode === "max" });
      const choice = window.modeChoice({ name, mode: line.mode, repeat: line.repeat, onChange: () => {
        line.mode = choice.mode();
        line.repeat = choice.repeat();
        amount.disabled = line.mode === "max";
        if (line.mode !== "max" && !line.amount) line.amount = 1;
        scheduleSave();
      } });
      const where = window.planetChoice({ planets, selected: line.planets, name, compact: true, onChange: () => {
        const chosen = where.value();
        if (chosen === null) { say(NO_PLANET, "error"); return; } // rien coché : la ligne garde ses planètes
        line.planets = chosen;
        scheduleSave();
      } });
      amount.addEventListener("input", () => { if (amount.value !== "") { line.amount = Number(amount.value); scheduleSave(); } });
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
      item.dataset.population = line.population;
      return item;
    }));
  }

  function renderAddForm() {
    const select = $("pop-add-population");
    if (!select.children.length) {
      select.replaceChildren(...["Assaut", "Logistique", "Administration"].map((tab) => {
        const group = el("optgroup", { label: tab });
        populations.filter((p) => p.tab === tab).forEach((p) => group.append(el("option", { value: p.id, textContent: p.label })));
        return group;
      }));
    }
    addPlanets.setPlanets(planets); // garde les cases déjà cochées
    $("pop-add-amount-label").hidden = addChoice.mode() === "max";
  }

  function renderAll() {
    renderQueue();
    renderAddForm();
  }

  const addChoice = window.modeChoice({ name: "la nouvelle ligne", select: $("pop-add-mode"), onChange: renderAddForm });
  $("pop-add-repeat").replaceChildren(addChoice.node);
  const addPlanets = window.planetChoice({ planets: [], name: "la nouvelle ligne" });
  $("pop-add-planets").replaceChildren(addPlanets.node);
  $("pop-add").addEventListener("click", () => {
    const population = $("pop-add-population").value;
    const mode = addChoice.mode();
    const chosen = addPlanets.value();
    const amount = mode === "max" ? 0 : Number($("pop-add-amount").value);
    if (!population) return;
    if (mode !== "max" && (!Number.isInteger(amount) || amount < 1)) { say("La quantité est un nombre entier d'au moins 1.", "error"); return; }
    if (chosen === null) { say(NO_PLANET, "error"); return; }
    state.queue.push({ population, mode, amount, planets: chosen, repeat: addChoice.repeat() });
    renderAll();
    scheduleSave();
  });

  async function load() {
    try {
      const data = await (await fetch("/api/populations")).json();
      populations = data.populations;
      planets = data.planets;
      if (!saveTimer) state = data.settings;
      renderAll();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  load();
  setInterval(async () => { // les planètes arrivent après le scan : on rafraîchit leurs listes, sans toucher à une ligne en cours de saisie
    try {
      const at = rev;
      const data = await (await fetch("/api/populations")).json();
      planets = data.planets;
      renderAddForm();
      const idle = at === rev && !saveTimer && !saving && !editing();
      if (idle && JSON.stringify(data.settings.queue) !== JSON.stringify(state.queue)) state = data.settings; // lignes retirées par le bot
      if (!editing()) renderQueue();
    } catch { /* idem */ }
  }, 5000);
})();
