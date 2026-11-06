// Liste d'objectifs de recherche : lignes « recherche + niveau cible » (l'ordre = la priorité) et planète chargée des recherches.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);

  let researches = [];
  let planets = [];
  let state = { planet: "", queue: [] };
  let saveTimer = null;
  const label = (id) => (researches.find((r) => r.id === id) || { label: id }).label;

  function say(text, kind) {
    $("res-msg").textContent = text;
    $("res-msg").className = "msg " + (kind || "");
  }
  const errorText = (data) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    try {
      const res = await fetch("/api/researches", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
      if (res.ok) { say("Enregistré.", "ok"); return; }
      say(errorText(await res.json().catch(() => ({}))), "error");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  }
  function scheduleSave() {
    clearTimeout(saveTimer);
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
      list.replaceChildren(el("li", { className: "empty-state", textContent: "Aucune ligne : ajoutez une recherche ci-dessous." }));
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

  function renderAddOptions() {
    const select = $("res-add-research");
    const used = new Set(state.queue.map((l) => l.research));
    const groups = ["Civil", "Militaire"].map((tab) => {
      const group = el("optgroup", { label: tab });
      researches.filter((r) => r.tab === tab && !used.has(r.id)).forEach((r) => group.append(el("option", { value: r.id, textContent: r.label })));
      return group;
    }).filter((g) => g.children.length);
    select.replaceChildren(...groups);
    $("res-add").disabled = !groups.length;
  }

  function renderAll() {
    renderPlanets();
    renderQueue();
    renderAddOptions();
  }

  $("res-planet").addEventListener("change", () => { state.planet = $("res-planet").value; scheduleSave(); });
  $("res-add").addEventListener("click", () => {
    const research = $("res-add-research").value;
    const level = Number($("res-add-level").value);
    if (!research) return;
    if (!Number.isInteger(level) || level < 1) { say("Le niveau cible est un nombre entier d'au moins 1.", "error"); return; }
    state.queue.push({ research, level });
    renderAll();
    scheduleSave();
  });

  async function load() {
    try {
      const data = await (await fetch("/api/researches")).json();
      researches = data.researches;
      planets = data.planets;
      if (!saveTimer) state = data.settings;
      renderAll();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  load();
  setInterval(async () => { // les planètes arrivent après le scan : on rafraîchit seulement leur liste
    try { planets = (await (await fetch("/api/researches")).json()).planets; renderPlanets(); } catch { /* idem */ }
  }, 5000);
})();
