// Recrutement de population : une ligne par (population, planète) avec son mode (quantité fixe / MAX / effectif à atteindre),
// sa quantité et sa planète (« toutes » ou une seule). L'ordre de la liste est la priorité.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);

  const MODES = [
    ["fixed", "Quantité fixe à chaque passage"],
    ["max", "MAX à chaque passage"],
    ["target", "Effectif à atteindre"],
  ];
  let populations = [];
  let planets = [];
  let state = { queue: [] };
  let saveTimer = null;
  const label = (id) => (populations.find((p) => p.id === id) || { label: id }).label;

  function say(text, kind) {
    $("pop-msg").textContent = text;
    $("pop-msg").className = "msg " + (kind || "");
  }
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    try {
      const res = await fetch("/api/populations", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
      if (res.ok) { say("Enregistré.", "ok"); return; }
      say(errorText(await res.json().catch(() => ({}))), "error");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  }
  function scheduleSave() {
    clearTimeout(saveTimer);
    say("Enregistrement…");
    saveTimer = setTimeout(save, 500);
  }

  const planetOptions = (selected) => {
    const names = [...new Set([...planets, selected].filter(Boolean))];
    return [el("option", { value: "", textContent: "Toutes les planètes" }), ...names.map((n) => el("option", { value: n, textContent: n }))];
  };
  const modeOptions = () => MODES.map(([value, text]) => el("option", { value, textContent: text }));

  function renderQueue() {
    const list = $("pop-queue");
    if (!state.queue.length) {
      list.replaceChildren(el("li", { className: "empty-state", textContent: "Aucune ligne : ajoutez une population ci-dessous." }));
      return;
    }
    list.replaceChildren(...state.queue.map((line, i) => {
      const name = label(line.population);
      const mode = el("select", { ariaLabel: `Mode de ${name}` }, ...modeOptions());
      mode.value = line.mode;
      const amount = el("input", { type: "number", min: "1", step: "1", value: line.amount || 1, ariaLabel: `Quantité de ${name}`, disabled: line.mode === "max" });
      const planet = el("select", { ariaLabel: `Planète de ${name}` }, ...planetOptions(line.planet));
      planet.value = line.planet;
      mode.addEventListener("change", () => { line.mode = mode.value; amount.disabled = line.mode === "max"; if (line.mode !== "max" && !line.amount) line.amount = 1; scheduleSave(); });
      amount.addEventListener("input", () => { if (amount.value !== "") { line.amount = Number(amount.value); scheduleSave(); } });
      planet.addEventListener("change", () => { line.planet = planet.value; scheduleSave(); });
      const button = (text, title, onClick, disabled = false) => {
        const b = el("button", { type: "button", className: "secondary small", textContent: text, title, ariaLabel: `${title} : ${name}`, disabled });
        b.addEventListener("click", onClick);
        return b;
      };
      const swap = (j) => { [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]]; renderQueue(); scheduleSave(); };
      const item = el("li", {},
        el("span", { className: "rank", textContent: String(i + 1) }),
        el("strong", { className: "name", textContent: name }),
        mode, el("label", {}, "quantité ", amount), planet,
        button("↑", "Monter", () => swap(i - 1), i === 0),
        button("↓", "Descendre", () => swap(i + 1), i === state.queue.length - 1),
        button("Supprimer", "Supprimer", () => { state.queue.splice(i, 1); renderAll(); scheduleSave(); }));
      item.dataset.population = line.population;
      item.dataset.planet = line.planet;
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
    if (!$("pop-add-mode").children.length) $("pop-add-mode").replaceChildren(...modeOptions());
    if (document.activeElement !== $("pop-add-planet")) {
      const chosen = $("pop-add-planet").value;
      $("pop-add-planet").replaceChildren(...planetOptions(chosen));
      $("pop-add-planet").value = chosen;
    }
    $("pop-add-amount-label").hidden = $("pop-add-mode").value === "max";
  }

  function renderAll() {
    renderQueue();
    renderAddForm();
  }

  $("pop-add-mode").addEventListener("change", renderAddForm);
  $("pop-add").addEventListener("click", () => {
    const population = $("pop-add-population").value;
    const mode = $("pop-add-mode").value;
    const planet = $("pop-add-planet").value;
    const amount = mode === "max" ? 0 : Number($("pop-add-amount").value);
    if (!population) return;
    if (mode !== "max" && (!Number.isInteger(amount) || amount < 1)) { say("La quantité est un nombre entier d'au moins 1.", "error"); return; }
    state.queue.push({ population, mode, amount, planet });
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
      planets = (await (await fetch("/api/populations")).json()).planets;
      renderAddForm();
      if (!$("pop-queue").contains(document.activeElement)) renderQueue();
    } catch { /* idem */ }
  }, 5000);
})();
