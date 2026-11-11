// Espionnage des Errants : planète, minimum par ressource, nombre de ressources à atteindre, campagne manuelle.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/spying";
  let data = null; // dernière réponse du bot
  let dirty = false; // saisie pas encore enregistrée : la relecture périodique ne l'écrase pas

  const say = (text, kind) => { $("spy-msg").textContent = text; $("spy-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");

  /** « Écartés : 4 Veyrs, 2 Haldens. » ; vide si aucun. */
  function skippedText(s) {
    const parts = Object.entries(s.skipped || {}).filter(([, n]) => n).map(([f, n]) => `${n} ${data.factions[f] || f}`);
    if (s.unknown_faction) parts.push(`${s.unknown_faction} de faction illisible`);
    return parts.length ? ` Écartés : ${parts.join(", ")}.` : "";
  }

  function renderStatus() {
    const s = data.status;
    let line = "Aucun espionnage lancé.";
    if (s.running) line = `Espionnage en cours autour de ${s.planet} — ${s.message} : ${s.kept} cible(s) retenue(s) sur ${s.total} Errant(s) trouvé(s).`;
    else if (s.started) line = `Dernier espionnage (${s.planet}, ${s.started} → ${s.finished}) : ${s.kept} cible(s) retenue(s) sur ${s.total} Errant(s) trouvé(s) — ${s.message}.`;
    if (s.started) line += skippedText(s);
    $("spy-status").textContent = line;
    $("spy-start").disabled = s.running;
    $("spy-stop").hidden = !s.running;
    $("spy-closed-title").textContent = `Piliers fermés (${s.closed.length})`;
    $("spy-closed").replaceChildren(...s.closed.map((name) => el("li", { textContent: name })));
  }

  function render() {
    const s = data.settings;
    const items = [["", "— choisir —"], ...data.planets.map((p) => [p, p])];
    if (s.planet && !data.planets.includes(s.planet)) items.push([s.planet, s.planet]);
    $("spy-planet").replaceChildren(...items.map(([value, text]) => el("option", { value, textContent: text })));
    $("spy-planet").value = s.planet;
    $("spy-required").value = String(s.required);
    $("spy-factions").replaceChildren(el("legend", { textContent: "Factions à espionner" }), ...Object.entries(data.factions).map(([id, label]) => {
      const box = el("input", { type: "checkbox", checked: s.factions.includes(id), ariaLabel: `Espionner les ${label}` });
      box.dataset.faction = id;
      return el("label", { className: "switch" }, box, ` ${label}`);
    }));
    $("spy-minimums").replaceChildren(...data.resources.map((r) => {
      const input = el("input", { type: "number", min: "0", step: "1", value: String(s.minimums[r] || 0), ariaLabel: `Minimum ${r}` });
      input.dataset.resource = r;
      return el("label", {}, `Minimum ${r} `, input);
    }));
    renderStatus();
  }

  function formValue() {
    const minimums = {};
    document.querySelectorAll("#spy-minimums input[data-resource]").forEach((i) => { minimums[i.dataset.resource] = Number(i.value) || 0; });
    const factions = [...document.querySelectorAll("#spy-factions input[data-faction]:checked")].map((i) => i.dataset.faction);
    return { planet: $("spy-planet").value, minimums, required: Number($("spy-required").value), factions };
  }

  async function send(url, options) {
    const res = await fetch(url, options);
    const body = await res.json().catch(() => ({}));
    return { ok: res.ok, body };
  }

  ["spy-planet", "spy-required"].forEach((id) => $(id).addEventListener("change", () => { dirty = true; }));
  $("spy-minimums").addEventListener("input", () => { dirty = true; });
  $("spy-factions").addEventListener("change", () => { dirty = true; });

  async function save() {
    const { ok, body } = await send(api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formValue()) });
    if (!ok) { say(errorText(body), "error"); return false; }
    data = body;
    dirty = false;
    render();
    return true;
  }

  $("spy-save").addEventListener("click", async () => {
    say("Enregistrement…");
    try { if (await save()) say("Enregistré.", "ok"); } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  });

  $("spy-start").addEventListener("click", async () => {
    try {
      if (dirty && !(await save())) return; // on lance avec les réglages affichés
      const { ok, body } = await send(api + "/start", { method: "POST" });
      if (!ok) { say(errorText(body), "error"); return; }
      data = body;
      renderStatus();
      say("Espionnage lancé : le bot est sur OFF.", "ok");
    } catch { say("Le bot ne répond pas.", "error"); }
  });

  $("spy-stop").addEventListener("click", async () => {
    try {
      const { body } = await send(api + "/stop", { method: "POST" });
      data = { ...body, settings: dirty ? { ...body.settings, ...formValue() } : body.settings };
      renderStatus();
      say("Arrêt demandé : le drone en cours se termine.", "ok");
    } catch { say("Le bot ne répond pas.", "error"); }
  });

  async function refresh() {
    try {
      const fresh = await (await fetch(api)).json();
      if (!data || !dirty) { data = fresh; render(); return; }
      data = { ...fresh, settings: { ...data.settings, ...formValue() } };
      renderStatus();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 3000);
})();
