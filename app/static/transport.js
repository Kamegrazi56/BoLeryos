// Option des constructions « Transporter les ressources manquantes » : planètes donneuses, voie (Piliers ou Flotte),
// modèles Piliers du jeu ou vaisseaux relevés sur les planètes donneuses (avec leur soute), marge par pas de 0,5 %.
// Enregistrement automatique, comme la liste des constructions.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const WAIT_MS = 500;

  let data = { settings: null, planets: [], models: { PILLARS: [], FLEET: [] }, ships: { planets: {}, names: [] }, methods: { PILLARS: "Piliers", FLEET: "Flotte" }, margin_step: 0.5, margin_max: 50 };
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
  function checks(title, names, chosen, empty, detail) {
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
      box.append(el("label", { className: "tr-check" }, input, " " + name + (detail ? detail(name) : "")));
    });
    return box;
  }

  const number = (n) => Number(n).toLocaleString("fr-FR");

  /** Soute d'un vaisseau, d'après le dernier relevé (« — soute 2 968 928 m³ par vaisseau »). */
  function shipDetail(name) {
    for (const info of Object.values(data.ships.planets || {})) {
      const ship = info.ships.find((s) => s.name === name && s.cargo);
      if (ship) return ` — soute ${number(ship.cargo)} m³ par vaisseau`;
    }
    return " — soute inconnue";
  }

  /** Tableau du dernier relevé : pour chaque planète donneuse cochée, ses vaisseaux disponibles et leur soute. */
  function roster() {
    const box = el("div", { className: "tr-roster" });
    state.donors.forEach((planet) => {
      const info = (data.ships.planets || {})[planet];
      const list = info ? info.ships.map((s) => `${s.name} : ${number(s.available)} dispo` + (s.cargo ? `, soute ${number(s.cargo)} m³` : "")).join(" · ") : "pas encore relevée";
      box.append(el("p", { className: "muted", textContent: `${planet} — ${list || "aucun vaisseau"}` }));
    });
    return box;
  }

  async function scanShips() {
    say("Relevé des vaisseaux en cours…");
    try {
      const res = await fetch("/api/transport/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ planets: state.donors }) });
      const body = await res.json();
      if (!res.ok) { say(errorText(body), "error"); return; }
      data = body;
      render();
      say(body.error || "Vaisseaux relevés.", body.error ? "error" : "ok");
    } catch { say("Relevé impossible : le bot ne répond pas.", "error"); }
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
    if (state.method === "PILLARS") {
      parts.push(checks("Modèles Piliers à utiliser (essayés dans l'ordre où ils sont cochés)", data.models.PILLARS || [], state.models, "Aucun modèle relevé : lancez le relevé des modèles de l'onglet Exploration."));
    }
    const none = "Aucun vaisseau relevé : utilisez « Relever les vaisseaux » ci-dessous.";
    const shipTitle = state.method === "PILLARS" ? "Vaisseaux de secours si les Piliers sont fermés" : "Vaisseaux à envoyer";
    parts.push(checks(`${shipTitle} (utilisés dans l'ordre où ils sont cochés)`, data.ships.names || [], state.ships, none, shipDetail));
    const margin = el("input", { type: "number", min: "0", max: String(data.margin_max), step: String(data.margin_step), value: state.margin, ariaLabel: "Marge en pourcentage" });
    margin.addEventListener("input", () => { if (margin.value !== "") { state.margin = Number(margin.value); change(); } });
    parts.push(el("label", { className: "tr-check" }, "Marge à ajouter au manque : ", margin, " %"));
    const scan = el("button", { type: "button", className: "secondary small", textContent: "Relever les vaisseaux", title: "Lit les vaisseaux disponibles et leur soute sur chaque planète donneuse cochée (lecture seule ; le bot doit être dans une partie)" });
    scan.addEventListener("click", scanShips);
    scan.disabled = !state.donors.length;
    parts.push(scan, roster());
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
  setInterval(load, 5000); // les planètes et les vaisseaux arrivent après le scan
})();
