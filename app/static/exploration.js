// Exploration : une seule configuration (moyen, composition, copies, durée, planètes, rythme) et relevé du jour (brèches, modèles).
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/explorations";
  let data = null; // dernière réponse du bot
  let dirty = false; // modification locale pas encore enregistrée : la relecture périodique ne l'écrase pas

  const say = (text, kind) => { $("exp-msg").textContent = text; $("exp-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const options = (select, items, chosen) => {
    select.replaceChildren(...items.map(([value, text]) => el("option", { value, textContent: text })));
    if (items.some(([v]) => v === chosen)) select.value = chosen;
  };

  function renderDaily() {
    const d = data.daily;
    const parts = [];
    if (d.day) {
      const when = d.scanned_at ? new Date(d.scanned_at * 1000).toLocaleTimeString("fr-FR") : "";
      parts.push(el("strong", { textContent: `Relevé du ${d.day}` }), when ? ` à ${when} (${d.source === "manuel" ? "à la demande" : "quotidien"})` : "");
    } else parts.push("Aucun relevé pour l'instant : le bot le fait au premier passage de la journée.");
    if (d.error) parts.push(" — ", el("strong", { className: "warn", textContent: d.error }));
    $("exp-daily").replaceChildren(...parts);
    $("exp-breaches").replaceChildren(...Object.entries(d.breaches).map(([planet, b]) =>
      el("li", { textContent: `${planet} → brèche ${b.address}` + (b.distance === null || b.distance === undefined ? "" : ` (distance ${b.distance})`) + (b.source === "formule" ? " — estimée par la formule" : "") })));
    $("exp-scan").disabled = !!data.scanning;
    $("exp-scan").textContent = data.scanning ? "Relevé en cours…" : "Refaire le relevé du jour";
  }

  function renderUnits(s) {
    const names = data.units[s.method] || [];
    $("exp-units").hidden = s.composition !== "manual";
    $("exp-units").replaceChildren(...names.map((name) => {
      const input = el("input", { type: "number", min: "0", step: "1", value: s.units[name] || "", ariaLabel: `Quantité ${name}` });
      input.dataset.unit = name;
      return el("label", {}, name + " ", input);
    }));
    if (!names.length) $("exp-units").replaceChildren(el("span", { className: "hint", textContent: s.method === "FLEET" ? "Aucun modèle de vaisseau connu : scannez les modèles (onglet Assemblages)." : "" }));
  }

  function renderPlanets(chosen) {
    const all = el("input", { type: "checkbox", checked: chosen.length === 0, ariaLabel: "Toutes les planètes" });
    const boxes = data.planets.map((p) => {
      const box = el("input", { type: "checkbox", checked: chosen.includes(p), disabled: chosen.length === 0, ariaLabel: p });
      box.dataset.planet = p;
      return box;
    });
    all.addEventListener("change", () => { boxes.forEach((b) => { b.disabled = all.checked; if (all.checked) b.checked = false; }); dirty = true; });
    boxes.forEach((b) => b.addEventListener("change", () => {
      if (!boxes.some((x) => x.checked)) { all.checked = true; boxes.forEach((x) => { x.disabled = true; }); }
      dirty = true;
    }));
    $("exp-planets").replaceChildren(el("label", {}, all, " Toutes les planètes"), ...data.planets.map((p, i) => el("label", {}, boxes[i], " " + p)));
  }

  function render() {
    const s = data.settings;
    options($("exp-method"), Object.entries(data.methods), s.method);
    $("exp-composition").value = s.composition;
    const models = (data.models[s.method] || []).map((m) => [m, m]);
    if (s.model && !models.some(([m]) => m === s.model)) models.unshift([s.model, s.model + " (pas dans le dernier relevé)"]);
    options($("exp-model"), models.length ? models : [["", "Aucun modèle relevé"]], s.model);
    $("exp-model-label").hidden = s.composition !== "model";
    renderUnits(s);
    $("exp-copies").value = s.copies;
    options($("exp-duration"), [1, 2, 3, 4, 5, 6].map((h) => [String(h), `${h} h`]), String(s.duration));
    $("exp-rhythm").value = s.rhythm;
    $("exp-per-pass").value = s.per_pass;
    $("exp-per-pass-label").hidden = s.rhythm !== "fixed";
    renderPlanets(s.planets);
    renderDaily();
  }

  // Valeurs du formulaire (réglages proposés au bot).
  function formValue() {
    const units = {};
    document.querySelectorAll("#exp-units input[data-unit]").forEach((i) => { if (i.value !== "") units[i.dataset.unit] = Number(i.value); });
    const all = $("exp-planets").querySelector("input[aria-label='Toutes les planètes']");
    const planets = all && all.checked ? [] : [...document.querySelectorAll("#exp-planets input[data-planet]")].filter((b) => b.checked).map((b) => b.dataset.planet);
    return {
      method: $("exp-method").value,
      composition: $("exp-composition").value,
      model: $("exp-model").value,
      units,
      copies: Number($("exp-copies").value),
      duration: Number($("exp-duration").value),
      planets,
      rhythm: $("exp-rhythm").value,
      per_pass: Number($("exp-per-pass").value),
    };
  }

  // Changer de moyen ou de composition réaffiche les bons champs, sans enregistrer.
  ["exp-method", "exp-composition", "exp-rhythm"].forEach((id) => $(id).addEventListener("change", () => {
    data.settings = { ...data.settings, ...formValue() };
    dirty = true;
    render();
  }));
  ["exp-model", "exp-copies", "exp-duration", "exp-per-pass"].forEach((id) => $(id).addEventListener("input", () => { dirty = true; }));
  $("exp-units").addEventListener("input", () => { dirty = true; });

  $("exp-save").addEventListener("click", async () => {
    say("Enregistrement…");
    try {
      const res = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formValue()) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(body), "error"); return; }
      data = body;
      dirty = false;
      render();
      say("Enregistré.", "ok");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  });

  $("exp-scan").addEventListener("click", async () => {
    $("exp-scan").disabled = true;
    $("exp-scan-msg").textContent = "Relevé en cours (une à deux minutes selon le nombre de planètes)…";
    try {
      const res = await fetch(api + "/scan", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        const keep = dirty ? formValue() : null;
        data = body;
        if (keep) data.settings = { ...data.settings, ...keep };
        render();
        $("exp-scan-msg").textContent = "";
      } else $("exp-scan-msg").textContent = errorText(body);
    } catch { $("exp-scan-msg").textContent = "Le bot ne répond pas."; }
    $("exp-scan").disabled = false;
  });

  async function refresh() {
    try {
      const fresh = await (await fetch(api)).json();
      if (!data || !dirty) { data = fresh; render(); return; }
      data = { ...fresh, settings: { ...data.settings, ...formValue() } }; // relevé à jour, saisie en cours gardée
      renderDaily();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
