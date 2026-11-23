// Option des constructions « Transporter les ressources manquantes » : planètes donneuses, voie (Piliers ou Flotte),
// modèles du jeu, marge par pas de 0,5 %. Enregistrement automatique, comme la liste des constructions.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const WAIT_MS = 500;

  let data = { settings: null, planets: [], models: { PILLARS: [], FLEET: [] }, methods: { PILLARS: "Piliers", FLEET: "Flotte" }, margin_step: 0.5, margin_max: 50 };
  let state = null; // réglages affichés (modifiés par le joueur)
  let saveTimer = null;
  let saving = false;
  let failed = false;
  let rev = 0;

  function say(text, kind) {
    $("tr-msg").textContent = text;
    $("tr-msg").className = "msg " + (kind || "");
  }
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");

  async function save() {
    saveTimer = null;
    saving = true;
    try {
      const res = await fetch("/api/transport", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state) });
      if (res.ok) { failed = false; data = await res.json(); say("Enregistré.", "ok"); return; }
      failed = true; // les réglages affichés ne sont pas ceux du bot : on les garde jusqu'au prochain enregistrement réussi
      say(errorText(await res.json().catch(() => ({}))), "error");
    } catch { failed = true; say("Enregistrement impossible : le bot ne répond pas.", "error"); } finally { saving = false; rev++; }
  }
  function change() {
    clearTimeout(saveTimer);
    rev++;
    say("Enregistrement…");
    saveTimer = setTimeout(save, WAIT_MS);
    render();
  }

  /** Liste de cases à cocher : `chosen` est la liste de noms retenus (modifiée sur place). */
  function checks(title, names, chosen, empty) {
    const box = el("fieldset", { className: "tr-checks" }, el("legend", { textContent: title }));
    const shown = [...new Set([...names, ...chosen])].sort((a, b) => a.localeCompare(b, "fr", { numeric: true, sensitivity: "base" }));
    if (!shown.length) box.append(el("span", { className: "muted", textContent: empty }));
    shown.forEach((name) => {
      const input = el("input", { type: "checkbox", checked: chosen.includes(name), ariaLabel: `${title} : ${name}` });
      input.addEventListener("change", () => {
        const at = chosen.indexOf(name);
        if (input.checked && at < 0) chosen.push(name);
        if (!input.checked && at >= 0) chosen.splice(at, 1);
        change();
      });
      box.append(el("label", { className: "tr-check" }, input, " " + name));
    });
    return box;
  }

  function render() {
    if (!state) return;
    const root = $("tr-body");
    const on = el("input", { type: "checkbox", checked: state.enabled, id: "tr-enabled" });
    on.addEventListener("change", () => { state.enabled = on.checked; change(); });
    const parts = [el("label", { className: "tr-main" }, on, " Transporter les ressources manquantes pour une construction")];
    parts.push(checks("Planètes donneuses", data.planets, state.donors, "Aucune planète connue : lancez un scan des planètes."));
    const way = el("fieldset", { className: "tr-checks" }, el("legend", { textContent: "Voie" }));
    Object.entries(data.methods).forEach(([id, text]) => {
      const radio = el("input", { type: "radio", name: "tr-method", value: id, checked: state.method === id, ariaLabel: `Voie : ${text}` });
      radio.addEventListener("change", () => { state.method = id; state.models = []; change(); });
      way.append(el("label", { className: "tr-check" }, radio, ` ${text}${id === "FLEET" ? " (spatial)" : ""}`));
    });
    parts.push(way);
    const none = "Aucun modèle relevé : utilisez « Relever les modèles » ci-dessous.";
    parts.push(checks(`Modèles ${data.methods[state.method]} à utiliser (essayés dans l'ordre où ils sont cochés)`, data.models[state.method] || [], state.models, none));
    if (state.method === "PILLARS") {
      parts.push(checks("Modèles Flotte de secours si les Piliers sont fermés", data.models.FLEET || [], state.fleet_models, none));
    }
    const margin = el("input", { type: "number", min: "0", max: String(data.margin_max), step: String(data.margin_step), value: state.margin, ariaLabel: "Marge en pourcentage" });
    margin.addEventListener("input", () => { if (margin.value !== "") { state.margin = Number(margin.value); change(); } });
    parts.push(el("label", { className: "tr-check" }, "Marge à ajouter au manque : ", margin, " %"));
    const scan = el("button", { type: "button", className: "secondary small", textContent: "Relever les modèles", title: "Relit les modèles Piliers et Flotte du jeu (le bot doit être dans une partie)" });
    scan.addEventListener("click", async () => {
      scan.disabled = true;
      say("Relevé des modèles en cours…");
      try { await fetch("/api/explorations/scan", { method: "POST" }); await load(true); say("Modèles relevés.", "ok"); } catch { say("Relevé impossible : le bot ne répond pas.", "error"); }
      scan.disabled = false;
    });
    parts.push(scan);
    const focused = root.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : null;
    root.replaceChildren(...parts);
    if (focused) { const again = [...root.querySelectorAll("[aria-label]")].find((n) => n.getAttribute("aria-label") === focused); if (again) again.focus(); }
  }

  async function load(force = false) {
    try {
      const at = rev;
      const fresh = await (await fetch("/api/transport")).json();
      const idle = at === rev && !saveTimer && !saving && !failed;
      data = fresh;
      if (force || idle || !state) state = JSON.parse(JSON.stringify(fresh.settings));
      if (force || idle || !$("tr-body").contains(document.activeElement)) render();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  load();
  setInterval(load, 5000); // les planètes et les modèles arrivent après le scan
})();
