// Exploration : planètes de départ (toutes, ou cochées avec leur nombre d'expéditions simultanées), envoi commun (moyen,
// composition, durée), option « Réglages différents par planète » (un envoi par planète cochée), relevé du jour.
// Les réglages sont modifiés dans `form` et enregistrés par le bouton « Enregistrer ».
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/explorations";
  const NO_PLANET = "Cochez au moins une planète, ou choisissez « Toutes les planètes ».";
  const HOURS = [1, 2, 3, 4, 5, 6];
  const BY = { PILLARS: "par les Piliers", FLEET: "par la Flotte" };
  const clone = (x) => JSON.parse(JSON.stringify(x));

  let data = null; // dernière réponse du bot
  let shownKey = ""; // réglages, planètes et modèles affichés (une relecture identique ne redessine rien)
  let form = null; // réglages en cours de modification : { send, copies, planets: [{name, copies, send}], per_planet }
  const memory = {}; // réglages par planète déjà vus (retrouvés si l'on décoche puis recoche une planète)
  let noneChecked = false; // « Planètes choisies » sans aucune case cochée
  let dirty = false; // modification pas encore enregistrée : la relecture périodique ne l'écrase pas
  let planetBox = null;

  const say = (text, kind) => { $("exp-msg").textContent = text; $("exp-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const options = (select, items, chosen) => {
    select.replaceChildren(...items.map(([value, text]) => el("option", { value, textContent: text })));
    if (items.some(([v]) => v === chosen)) select.value = chosen;
  };
  const changed = () => { dirty = true; renderSummary(); };
  const keyOf = (d) => JSON.stringify([d.settings, d.planets, d.models, d.units]);

  function fromSettings(s) {
    const pick = ({ method, composition, model, units, duration }) => ({ method, composition, model, units: { ...units }, duration });
    const planets = s.planets.map((p) => ({ name: p.name, copies: p.copies, send: p.send ? pick(p.send) : null }));
    planets.forEach((p) => { memory[p.name] = clone(p); });
    noneChecked = false;
    return { send: pick(s), copies: s.copies, planets, per_planet: s.per_planet };
  }
  const allPlanets = () => !form.planets.length && !noneChecked;

  // ------------------------------------------------------------------------------ un envoi (commun ou d'une planète)
  function modelItems(send) {
    const models = (data.models[send.method] || []).map((m) => [m, m]);
    if (send.model && !models.some(([m]) => m === send.model)) models.unshift([send.model, send.model + " (pas dans le dernier relevé)"]);
    return models.length ? models : [["", "Aucun modèle relevé"]];
  }
  function unitInputs(send, suffix) {
    const names = data.units[send.method] || [];
    if (!names.length) {
      return [el("span", { className: "hint", textContent: send.method === "FLEET" ? "Aucun modèle de vaisseau connu : scannez les modèles (onglet Assemblages)." : "" })];
    }
    return names.map((name) => {
      const input = el("input", { type: "number", min: "0", step: "1", value: send.units[name] || "", ariaLabel: `Quantité ${name}${suffix}` });
      input.addEventListener("input", () => {
        if (input.value === "" || Number(input.value) === 0) delete send.units[name];
        else send.units[name] = Number(input.value);
        changed();
      });
      return el("label", {}, name + " ", input);
    });
  }

  /** Envoi propre à une planète : mêmes champs que l'envoi commun, dans la ligne de la planète. */
  function planetSend(entry) {
    const box = el("div", { className: "exp-send" });
    const send = entry.send;
    const draw = () => {
      const suffix = ` : ${entry.name}`;
      const method = el("select", { ariaLabel: `Envoyer par${suffix}` });
      options(method, Object.entries(data.methods), send.method);
      const composition = el("select", { ariaLabel: `Composition${suffix}` });
      options(composition, [["model", "Un modèle du jeu"], ["manual", "Une quantité par unité"]], send.composition);
      const duration = el("select", { ariaLabel: `Durée de l'expédition${suffix}` });
      options(duration, HOURS.map((h) => [String(h), `${h} h`]), String(send.duration));
      method.addEventListener("change", () => { send.method = method.value; send.model = ""; send.units = {}; draw(); changed(); });
      composition.addEventListener("change", () => { send.composition = composition.value; draw(); changed(); });
      duration.addEventListener("change", () => { send.duration = Number(duration.value); changed(); });
      const row = [el("label", {}, "Envoyer par ", method), el("label", {}, "Composition ", composition)];
      if (send.composition === "model") {
        const model = el("select", { ariaLabel: `Modèle${suffix}` });
        options(model, modelItems(send), send.model);
        if (!send.model) send.model = model.value;
        model.addEventListener("change", () => { send.model = model.value; changed(); });
        row.push(el("label", {}, "Modèle ", model));
      }
      row.push(el("label", {}, "Durée de l'expédition ", duration));
      box.replaceChildren(el("div", { className: "row" }, ...row),
        ...(send.composition === "manual" ? [el("div", { className: "row unit-grid" }, ...unitInputs(send, suffix))] : []));
    };
    draw();
    return box;
  }

  // ------------------------------------------------------------------------------ envoi commun
  function renderCommon() {
    const send = form.send;
    options($("exp-method"), Object.entries(data.methods), send.method);
    $("exp-composition").value = send.composition;
    options($("exp-model"), modelItems(send), send.model);
    if (!send.model && send.composition === "model") send.model = $("exp-model").value;
    $("exp-model-label").hidden = send.composition !== "model";
    options($("exp-duration"), HOURS.map((h) => [String(h), `${h} h`]), String(send.duration));
    $("exp-units").hidden = send.composition !== "manual";
    $("exp-units").replaceChildren(...unitInputs(send, ""));
  }
  $("exp-method").addEventListener("change", () => { form.send.method = $("exp-method").value; form.send.model = ""; form.send.units = {}; renderCommon(); changed(); });
  $("exp-composition").addEventListener("change", () => { form.send.composition = $("exp-composition").value; renderCommon(); changed(); });
  $("exp-model").addEventListener("change", () => { form.send.model = $("exp-model").value; changed(); });
  $("exp-duration").addEventListener("change", () => { form.send.duration = Number($("exp-duration").value); changed(); });

  // ------------------------------------------------------------------------------ planètes de départ
  function renderPlanets() {
    planetBox = window.planetChoice({ planets: data.planets, selected: form.planets.map((p) => p.name), name: "l'exploration", onChange: () => {
      const chosen = planetBox.value();
      noneChecked = chosen === null;
      form.planets.forEach((p) => { memory[p.name] = clone(p); });
      form.planets = (chosen || []).map((name) => form.planets.find((p) => p.name === name) || clone(memory[name] || { name, copies: 1, send: null }));
      if (form.per_planet) prefill();
      renderPlanetRows();
      changed();
    } });
    $("exp-planets").replaceChildren(planetBox.node);
    renderPlanetRows();
  }

  /** Une planète sans envoi propre reprend celui de la planète précédente (ou, pour la première, les réglages communs). */
  function prefill() {
    let previous = form.send;
    form.planets.forEach((p) => {
      if (!p.send) p.send = clone(previous);
      previous = p.send;
    });
  }

  function renderPlanetRows() {
    const all = allPlanets();
    $("exp-common").hidden = form.per_planet && !all; // tout se règle alors dans les cartes des planètes
    $("exp-copies-label").hidden = !all;
    $("exp-copies").value = form.copies;
    $("exp-per-planet").disabled = all;
    $("exp-per-planet").checked = form.per_planet && !all;
    $("exp-per-planet-label").classList.toggle("is-disabled", all);
    $("exp-planet-rows").replaceChildren(...form.planets.map((entry) => {
      const copies = el("input", { type: "number", min: "1", max: "10", step: "1", value: entry.copies, ariaLabel: `Expéditions simultanées : ${entry.name}` });
      copies.addEventListener("input", () => { if (copies.value !== "") { entry.copies = Number(copies.value); changed(); } });
      const item = el("li", {}, el("div", { className: "exp-planet-head" },
        el("strong", { className: "name", textContent: entry.name }),
        el("label", {}, "Expéditions simultanées ", copies)));
      if (form.per_planet) item.append(planetSend(entry));
      item.dataset.planet = entry.name;
      return item;
    }));
  }
  $("exp-copies").addEventListener("input", () => { if ($("exp-copies").value !== "") { form.copies = Number($("exp-copies").value); changed(); } });
  $("exp-per-planet").addEventListener("change", () => {
    form.per_planet = $("exp-per-planet").checked;
    if (form.per_planet) prefill();
    renderPlanetRows();
    changed();
  });

  // ------------------------------------------------------------------------------ résumé
  function sendText(send) {
    const what = send.composition === "model"
      ? (send.model ? `modèle « ${send.model} »` : "aucun modèle choisi")
      : (Object.entries(send.units).map(([n, q]) => `${q} × ${n}`).join(", ") || "aucune unité");
    return `${send.duration} h, ${BY[send.method] || send.method}, ${what}`;
  }
  const many = (n) => `${n} expédition${n > 1 ? "s" : ""}`;
  function renderSummary() {
    if (noneChecked) { $("exp-summary").textContent = NO_PLANET; return; }
    if (allPlanets()) {
      $("exp-summary").textContent = `À chaque passage : ${many(form.copies)} simultanée${form.copies > 1 ? "s" : ""} depuis chaque planète (${sendText(form.send)}).`;
      return;
    }
    if (form.per_planet) {
      $("exp-summary").textContent = "À chaque passage : " + form.planets.map((p) => `${many(p.copies)} depuis ${p.name} (${sendText(p.send || form.send)})`).join(" ; ") + ".";
      return;
    }
    $("exp-summary").textContent = "À chaque passage : " + form.planets.map((p) => `${many(p.copies)} depuis ${p.name}`).join(", ") + ` (${sendText(form.send)}).`;
  }

  // ------------------------------------------------------------------------------ relevé du jour
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

  function renderAll() {
    shownKey = keyOf(data);
    renderPlanets();
    renderCommon();
    renderSummary();
    renderDaily();
  }

  // ------------------------------------------------------------------------------ enregistrement et relecture
  function formValue() {
    return { ...form.send, copies: form.copies, planets: form.planets, per_planet: form.per_planet && !allPlanets() };
  }

  $("exp-save").addEventListener("click", async () => {
    if (noneChecked) { say(NO_PLANET, "error"); return; }
    say("Enregistrement…");
    try {
      const res = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(formValue()) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(body), "error"); return; }
      data = body;
      form = fromSettings(data.settings);
      dirty = false;
      renderAll();
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
        data = body;
        if (!dirty) form = fromSettings(data.settings);
        renderAll(); // les modèles relevés arrivent dans les listes ; une saisie en cours est gardée
        $("exp-scan-msg").textContent = "";
      } else $("exp-scan-msg").textContent = errorText(body);
    } catch { $("exp-scan-msg").textContent = "Le bot ne répond pas."; }
    $("exp-scan").disabled = false;
  });

  async function refresh() {
    try {
      const fresh = await (await fetch(api)).json();
      const first = !data;
      data = fresh;
      if (first || (!dirty && keyOf(fresh) !== shownKey)) {
        form = fromSettings(data.settings);
        renderAll();
        return;
      }
      if (planetBox) planetBox.setPlanets(data.planets); // saisie en cours gardée : seuls la liste des planètes et le relevé changent
      renderDaily();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
