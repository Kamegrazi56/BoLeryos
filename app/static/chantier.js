// Chantier spatial : listes « élément + mode + quantité + planètes » des Assemblages (vaisseaux) et des Composants.
// Même principe que l'onglet Population ; les planètes sont « toutes » ou une ou plusieurs planètes cochées.
// Les lignes atteintes ou « une seule fois » faites sont retirées par le bot : la liste se rafraîchit toute seule.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);

  const MODES = [
    ["fixed:1", "Quantité à chaque passage"],
    ["fixed:0", "Quantité une seule fois"],
    ["max:1", "MAX à chaque passage"],
    ["max:0", "MAX une seule fois"],
    ["target:0", "Effectif à atteindre"],
  ];
  const modeValue = (line) => `${line.mode}:${line.mode !== "target" && line.repeat !== false ? 1 : 0}`;
  const applyMode = (line, value) => { const [mode, repeat] = value.split(":"); line.mode = mode; line.repeat = repeat === "1"; };
  const modeOptions = () => MODES.map(([value, text]) => el("option", { value, textContent: text }));
  const planetsText = (planets) => (planets.length ? planets.join(", ") : "Toutes les planètes");
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  /** Choix des planètes : « Toutes » ou une ou plusieurs cases cochées. `value()` rend [] pour « toutes ». */
  function planetPicker(planets, selected, name, onChange) {
    const names = [...new Set([...planets, ...selected])];
    const all = el("input", { type: "checkbox", checked: selected.length === 0, ariaLabel: `Toutes les planètes pour ${name}` });
    const boxes = names.map((p) => el("input", { type: "checkbox", checked: selected.includes(p), disabled: selected.length === 0, ariaLabel: `${p} pour ${name}` }));
    const value = () => (all.checked ? [] : names.filter((_, i) => boxes[i].checked));
    const summary = () => (all.checked ? planetsText([]) : boxes.some((b) => b.checked) ? planetsText(value()) : "Choisissez les planètes…");
    const refresh = () => { node.querySelector("summary").textContent = summary(); onChange(); };
    all.addEventListener("change", () => {
      boxes.forEach((b) => { b.disabled = all.checked; if (all.checked) b.checked = false; });
      refresh();
    });
    boxes.forEach((b) => b.addEventListener("change", () => {
      if (!boxes.some((x) => x.checked)) { all.checked = true; boxes.forEach((x) => { x.disabled = true; }); } // plus aucune case : « toutes »
      refresh();
    }));
    const node = el("details", { className: "planet-picker" },
      el("summary", { textContent: planetsText(selected) }),
      el("label", {}, all, " Toutes les planètes"),
      ...names.map((p, i) => el("label", {}, boxes[i], " " + p)));
    node.value = value;
    return node;
  }

  function editor({ prefix, api, itemsOf, emptyText }) {
    const id = (s) => $(`${prefix}-${s}`);
    let items = [];
    let planets = [];
    let state = { queue: [] };
    let saveTimer = null;
    let saving = false;
    let rev = 0; // change à chaque modification locale : une lecture partie avant ne doit pas écraser la liste
    const label = (item) => (items.find((i) => i.value === item) || { label: item }).label;

    const say = (text, kind) => { id("msg").textContent = text; id("msg").className = "msg " + (kind || ""); };
    async function save() {
      saveTimer = null;
      saving = true;
      try {
        const res = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
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

    function renderQueue() {
      const list = id("queue");
      if (!state.queue.length) {
        list.replaceChildren(el("li", { className: "empty-state", textContent: emptyText }));
        return;
      }
      list.replaceChildren(...state.queue.map((line, i) => {
        const name = label(line.item);
        const mode = el("select", { ariaLabel: `Mode de ${name}` }, ...modeOptions());
        mode.value = modeValue(line);
        const amount = el("input", { type: "number", min: "1", step: "1", value: line.amount || 1, ariaLabel: `Quantité de ${name}`, disabled: line.mode === "max" });
        mode.addEventListener("change", () => { applyMode(line, mode.value); amount.disabled = line.mode === "max"; if (line.mode !== "max" && !line.amount) line.amount = 1; scheduleSave(); });
        amount.addEventListener("input", () => { if (amount.value !== "") { line.amount = Number(amount.value); scheduleSave(); } });
        const where = planetPicker(planets, line.planets, name, () => { line.planets = where.value(); scheduleSave(); });
        const button = (text, title, onClick, disabled = false) => {
          const b = el("button", { type: "button", className: "secondary small", textContent: text, title, ariaLabel: `${title} : ${name}`, disabled });
          b.addEventListener("click", onClick);
          return b;
        };
        const swap = (j) => { [state.queue[i], state.queue[j]] = [state.queue[j], state.queue[i]]; renderQueue(); scheduleSave(); };
        const item = el("li", {},
          el("span", { className: "rank", textContent: String(i + 1) }),
          el("strong", { className: "name", textContent: name }),
          mode, el("label", {}, "quantité ", amount), where,
          button("↑", "Monter", () => swap(i - 1), i === 0),
          button("↓", "Descendre", () => swap(i + 1), i === state.queue.length - 1),
          button("Supprimer", "Supprimer", () => { state.queue.splice(i, 1); renderAll(); scheduleSave(); }));
        item.dataset.item = line.item;
        return item;
      }));
    }

    let picker = null;
    function renderAddForm() {
      const select = id("add-item");
      const chosen = select.value;
      const groups = [...new Set(items.map((i) => i.group).filter(Boolean))];
      const option = (i) => el("option", { value: i.value, textContent: i.label });
      select.replaceChildren(...(groups.length
        ? groups.map((g) => el("optgroup", { label: g }, ...items.filter((i) => i.group === g).map(option)))
        : items.map(option)));
      if (chosen && items.some((i) => i.value === chosen)) select.value = chosen;
      if (!id("add-mode").children.length) id("add-mode").replaceChildren(...modeOptions());
      const selected = picker ? picker.value() : [];
      picker = planetPicker(planets, selected, "la nouvelle ligne", () => {});
      id("add-planets").replaceChildren(picker);
      id("add").disabled = !items.length;
      id("add-amount-label").hidden = id("add-mode").value.startsWith("max");
    }
    const renderAll = () => { renderQueue(); renderAddForm(); };

    id("add-mode").addEventListener("change", () => { id("add-amount-label").hidden = id("add-mode").value.startsWith("max"); });
    id("add").addEventListener("click", () => {
      const item = id("add-item").value;
      const [mode, repeat] = id("add-mode").value.split(":");
      const amount = mode === "max" ? 0 : Number(id("add-amount").value);
      if (!item) return;
      if (mode !== "max" && (!Number.isInteger(amount) || amount < 1)) { say("La quantité est un nombre entier d'au moins 1.", "error"); return; }
      state.queue.push({ item, mode, amount, planets: picker ? picker.value() : [], repeat: repeat === "1" });
      renderAll();
      scheduleSave();
    });

    async function refresh(first) {
      try {
        const at = rev;
        const data = await (await fetch(api)).json();
        items = itemsOf(data);
        planets = data.planets;
        const idle = at === rev && !saveTimer && !saving;
        if (first === true) { if (!saveTimer) state = data.settings; renderAll(); return; }
        const listFocused = id("queue").contains(document.activeElement) || id("add-planets").contains(document.activeElement);
        if (idle && !listFocused) {
          if (JSON.stringify(data.settings.queue) !== JSON.stringify(state.queue)) state = data.settings; // lignes retirées par le bot
          renderAll();
        }
      } catch { /* le bot redémarre : on réessaie */ }
    }
    refresh(true);
    setInterval(refresh, 5000);
  }

  editor({
    prefix: "shp",
    api: "/api/ships",
    itemsOf: (data) => data.models.map((m) => ({ value: m, label: m })),
    emptyText: "Aucune ligne : scannez les modèles, puis ajoutez un modèle ci-dessous.",
  });
  editor({
    prefix: "cmp",
    api: "/api/components",
    itemsOf: (data) => data.components.map((c) => ({ value: c.id, label: c.label, group: c.category })),
    emptyText: "Aucune ligne : ajoutez un composant ci-dessous.",
  });

  // ---- scan des modèles (onglet Assemblages)
  const SOURCES = { démarrage: "au démarrage", manuel: "à la demande" };
  function renderScan(scan) {
    $("shp-scan").disabled = !!scan.scanning;
    $("shp-scan").textContent = scan.scanning ? "Scan en cours…" : "Scanner les modèles";
    const parts = [];
    if (scan.scanned_at) {
      const when = new Date(scan.scanned_at * 1000).toLocaleTimeString("fr-FR");
      parts.push(el("strong", { textContent: `${scan.models.length} modèle(s)` }), ` — dernier scan à ${when} (${SOURCES[scan.source] || scan.source})`);
    } else parts.push("Aucun scan pour l'instant : il se fait tout seul dès que le bot est dans la partie.");
    if (scan.error) parts.push(" — ", el("strong", { className: "warn", textContent: scan.error }));
    $("shp-scan-status").replaceChildren(...parts);
    $("shp-scan-list").replaceChildren(...scan.models.map((m) => el("li", {}, `${m.name} — ${m.kind || "?"}${m.owned === null ? "" : `, en service : ${m.owned}`}`)));
  }
  async function refreshScan() {
    try { renderScan(await (await fetch("/api/models")).json()); } catch { /* idem */ }
  }
  $("shp-scan").addEventListener("click", async () => {
    $("shp-scan").disabled = true;
    $("shp-scan-msg").textContent = "";
    try {
      const res = await fetch("/api/models/scan", { method: "POST" });
      if (res.ok) renderScan(await res.json());
      else $("shp-scan-msg").textContent = errorText(await res.json().catch(() => ({})));
    } catch { $("shp-scan-msg").textContent = "Le bot ne répond pas."; }
    $("shp-scan").disabled = false;
  });
  refreshScan();
  setInterval(refreshScan, 5000);
})();
