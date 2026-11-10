// Fouilles : planètes de départ (toutes, ou cochées avec leur nombre de Fouilles simultanées), envoi commun (durée, équipe,
// Mira Venn), option « Réglages différents par Fouille » (durée et équipe de chaque Fouille), délai avant relance, relevé du
// jour et Fouilles suivies. Les réglages sont modifiés dans `form` et enregistrés par le bouton « Enregistrer ».
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/excavations";
  const NO_PLANET = "Cochez au moins une planète, ou choisissez « Toutes les planètes ».";
  const clone = (x) => JSON.parse(JSON.stringify(x));

  let data = null; // dernière réponse du bot
  let shownKey = ""; // réglages, planètes affichés (une relecture identique ne redessine rien)
  let form = null; // { send: {duration, units}, copies, fouilles, planets: [{name, copies, fouilles}], per_slot, mira, delay_min, delay_max }
  const memory = {}; // réglages par planète déjà vus (retrouvés si l'on décoche puis recoche une planète)
  let noneChecked = false; // « Planètes choisies » sans aucune case cochée
  let dirty = false; // modification pas encore enregistrée : la relecture périodique ne l'écrase pas
  let planetBox = null;

  const say = (text, kind) => { $("exc-msg").textContent = text; $("exc-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const total = (units) => Object.values(units).reduce((s, q) => s + q, 0);
  const changed = () => { dirty = true; renderSummary(); };
  const keyOf = (d) => JSON.stringify([d.settings, d.planets]);
  const allPlanets = () => !form.planets.length && !noneChecked;
  const maxActive = () => data.daily.max_active || null; // Fouilles actives au plus pour le compte (relevé du jour)
  const used = () => (allPlanets() ? form.copies * data.planets.length : form.planets.reduce((s, p) => s + p.copies, 0));
  /** Nombre de Fouilles demandé, ramené à ce que le jeu permet encore (`others` : Fouilles des autres planètes). */
  function capped(requested, others, perPlanets = 1) {
    const max = maxActive();
    const wanted = Math.min(10, Math.max(1, requested));
    if (!max) return wanted;
    const room = Math.max(1, Math.floor((max - others) / perPlanets));
    if (wanted > room) say(`Le jeu permet ${max} Fouilles à la fois : ${room} au plus ici.`, "error");
    else if ($("exc-msg").textContent.startsWith("Le jeu permet")) say(""); // nombre de nouveau possible : l'avertissement s'efface
    return Math.min(wanted, room);
  }
  function renderLimit() {
    const max = maxActive();
    const n = used();
    $("exc-limit").textContent = max ? `Fouilles simultanées : ${n} / ${max} (maximum du jeu, relevé du jour)` : "Maximum du jeu inconnu : il sera lu au relevé du jour.";
    $("exc-limit").classList.toggle("warn", !!max && n > max);
  }

  function fromSettings(s) {
    const pick = (f) => ({ duration: f.duration, units: { ...f.units } });
    const planets = s.planets.map((p) => ({ name: p.name, copies: p.copies, fouilles: p.fouilles.map(pick) }));
    planets.forEach((p) => { memory[p.name] = clone(p); });
    noneChecked = false;
    return { send: pick(s), copies: s.copies, fouilles: s.fouilles.map(pick), planets, per_slot: s.per_slot, mira: s.mira,
             delay_min: s.delay_min, delay_max: s.delay_max };
  }

  // ------------------------------------------------------------------------------ une Fouille : durée + équipe
  function durationSelect(send, label) {
    const select = el("select", { ariaLabel: label });
    select.replaceChildren(...data.durations.map((h) => el("option", { value: String(h), textContent: `${h} h` })));
    select.value = String(send.duration);
    select.addEventListener("change", () => { send.duration = Number(select.value); changed(); });
    return select;
  }
  function teamInputs(send, suffix) {
    const sum = el("span", { className: "hint team-total" });
    const show = () => { const n = total(send.units); sum.textContent = `Équipe : ${n} / ${data.max_team}`; sum.classList.toggle("warn", n < 1 || n > data.max_team); };
    const inputs = data.units.map((name) => {
      const input = el("input", { type: "number", min: "0", max: String(data.max_team), step: "1", value: send.units[name] || "", ariaLabel: `Quantité ${name}${suffix}` });
      input.addEventListener("input", () => {
        if (input.value === "" || Number(input.value) === 0) delete send.units[name];
        else send.units[name] = Number(input.value);
        show();
        changed();
      });
      return el("label", {}, name + " ", input);
    });
    show();
    return [...inputs, sum];
  }
  /** Les Fouilles 1…n d'une planète (ou de « toutes les planètes »), chacune avec sa durée et son équipe. */
  function slotRows(owner, count, where) {
    // Une nouvelle Fouille reprend la précédente de la même planète (la première : la durée et l'équipe communes).
    while (owner.fouilles.length < count) owner.fouilles.push(clone(owner.fouilles[owner.fouilles.length - 1] || form.send));
    return Array.from({ length: count }, (_, k) => {
      const send = owner.fouilles[k];
      const suffix = ` : ${where}, Fouille ${k + 1}`;
      return el("div", { className: "exc-slot" },
        el("strong", { className: "exc-slot-name", textContent: `Fouille ${k + 1}` }),
        el("label", {}, "Durée ", durationSelect(send, `Durée${suffix}`)),
        el("div", { className: "row unit-grid" }, ...teamInputs(send, suffix)));
    });
  }

  // ------------------------------------------------------------------------------ planètes de départ
  function renderPlanets() {
    planetBox = window.planetChoice({ planets: data.planets, selected: form.planets.map((p) => p.name), name: "les Fouilles", onChange: () => {
      const chosen = planetBox.value();
      noneChecked = chosen === null;
      form.planets.forEach((p) => { memory[p.name] = clone(p); });
      form.planets = (chosen || []).map((name) => form.planets.find((p) => p.name === name) || clone(memory[name] || { name, copies: 1, fouilles: [] }));
      renderPlanetRows();
      renderMira();
      changed();
    } });
    $("exc-planets").replaceChildren(planetBox.node);
    renderPlanetRows();
  }

  function planetCard(entry) {
    const copies = el("input", { type: "number", min: "1", max: "10", step: "1", value: entry.copies, ariaLabel: `Fouilles simultanées : ${entry.name}` });
    copies.addEventListener("change", () => {
      if (copies.value === "") return;
      entry.copies = capped(Number(copies.value), used() - entry.copies);
      renderPlanetRows(); // une Fouille de plus ou de moins
      changed();
    });
    const item = el("li", {}, el("div", { className: "exp-planet-head" },
      el("strong", { className: "name", textContent: entry.name }),
      el("label", {}, "Fouilles simultanées ", copies)));
    if (form.per_slot) item.append(el("div", { className: "exp-send" }, ...slotRows(entry, entry.copies, entry.name)));
    item.dataset.planet = entry.name;
    return item;
  }

  function renderPlanetRows() {
    const all = allPlanets();
    $("exc-copies-label").hidden = !all;
    $("exc-copies").value = form.copies;
    $("exc-per-slot").checked = form.per_slot;
    $("exc-common").hidden = form.per_slot; // tout se règle alors dans les cartes des planètes
    let cards = form.planets.map(planetCard);
    if (all && form.per_slot) { // toutes les planètes : les mêmes Fouilles pour chacune
      cards = [el("li", { className: "exc-all" }, el("div", { className: "exp-planet-head" }, el("strong", { className: "name", textContent: "Chaque planète" })),
        el("div", { className: "exp-send" }, ...slotRows(form, form.copies, "toutes les planètes")))];
    }
    $("exc-planet-rows").replaceChildren(...cards);
    renderLimit();
  }
  $("exc-copies").addEventListener("change", () => {
    if ($("exc-copies").value === "") return;
    form.copies = capped(Number($("exc-copies").value), 0, Math.max(1, data.planets.length)); // pour chaque planète
    renderPlanetRows();
    changed();
  });
  $("exc-per-slot").addEventListener("change", () => { form.per_slot = $("exc-per-slot").checked; renderPlanetRows(); changed(); });

  // ------------------------------------------------------------------------------ envoi commun, Mira, relance
  function renderMira() {
    const names = form.planets.length ? form.planets.map((p) => p.name) : data.planets;
    if (form.mira && !names.includes(form.mira)) form.mira = "";
    $("exc-mira").replaceChildren(el("option", { value: "", textContent: "aucune" }), ...names.map((n) => el("option", { value: n, textContent: n })));
    $("exc-mira").value = form.mira;
  }
  function renderCommon() {
    $("exc-duration").replaceChildren(...data.durations.map((h) => el("option", { value: String(h), textContent: `${h} h` })));
    $("exc-duration").value = String(form.send.duration);
    $("exc-units").replaceChildren(...teamInputs(form.send, ""));
    renderMira();
    $("exc-delay-min").value = form.delay_min ?? "";
    $("exc-delay-max").value = form.delay_max ?? "";
  }
  $("exc-duration").addEventListener("change", () => { form.send.duration = Number($("exc-duration").value); changed(); });
  $("exc-mira").addEventListener("change", () => { form.mira = $("exc-mira").value; changed(); });
  [["exc-delay-min", "delay_min"], ["exc-delay-max", "delay_max"]].forEach(([id, key]) => $(id).addEventListener("input", () => {
    form[key] = $(id).value === "" ? null : Number($(id).value);
    changed();
  }));

  // ------------------------------------------------------------------------------ résumé
  const sendText = (f) => `${f.duration} h, ${total(f.units)} unités`;
  function planetText(name, count, fouilles) {
    const list = Array.from({ length: count }, (_, k) => (form.per_slot && fouilles[k] ? fouilles[k] : form.send));
    const mira = form.mira === name ? ", Mira Venn avec la Fouille 1" : "";
    const same = list.every((f) => JSON.stringify(f) === JSON.stringify(list[0]));
    const detail = same ? sendText(list[0]) : list.map((f, k) => `Fouille ${k + 1} : ${sendText(f)}`).join(" ; ");
    return `${count} Fouille${count > 1 ? "s" : ""} depuis ${name} (${detail}${mira})`;
  }
  function renderSummary() {
    if (noneChecked) { $("exc-summary").textContent = NO_PLANET; return; }
    const delay = form.delay_min !== null && form.delay_max !== null ? ` Relance ${form.delay_min} à ${form.delay_max} min après la fin.` : " Délai avant relance à choisir.";
    const what = allPlanets()
      ? planetText("chaque planète", form.copies, form.fouilles)
      : form.planets.map((p) => planetText(p.name, p.copies, p.fouilles)).join(" ; ");
    $("exc-summary").textContent = `En continu : ${what}.${delay}`;
  }

  // ------------------------------------------------------------------------------ relevé du jour et Fouilles suivies
  function renderDaily() {
    const d = data.daily;
    const parts = [];
    if (d.day) {
      const when = d.scanned_at ? new Date(d.scanned_at * 1000).toLocaleTimeString("fr-FR") : "";
      parts.push(el("strong", { textContent: `Relevé du ${d.day}` }), when ? ` à ${when}` : "");
      if (d.max_active !== null && d.max_active !== undefined) parts.push(` — Fouilles actives au plus : ${d.max_active}`);
    } else parts.push("Aucun relevé pour l'instant : le bot le fait au premier passage de la journée.");
    if (d.error) parts.push(" — ", el("strong", { className: "warn", textContent: d.error }));
    $("exc-daily").replaceChildren(...parts);
    $("exc-sites").replaceChildren(...Object.entries(d.sites).map(([planet, sites]) =>
      el("li", { textContent: `${planet} → ` + (sites.length ? sites.map((s) => s.address).join(", ") : "aucun site libre") })));
    $("exc-tracked").replaceChildren(...(data.tracked.length ? data.tracked.map((t) => el("li", {
      textContent: `${t.planet}${t.slot === null || t.slot === undefined ? "" : `, Fouille ${t.slot + 1}`} → ${t.site} (${t.duration} h) : fin ${clock(t.end)}, place libre à ${clock(t.ready)}` })) : [el("li", { textContent: "Aucune." })]));
    $("exc-scan").disabled = !!data.scanning;
    $("exc-scan").textContent = data.scanning ? "Relevé en cours…" : "Refaire le relevé du jour";
    if (form) renderLimit(); // le maximum du jeu vient du relevé du jour
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
    return { ...form.send, copies: form.copies, fouilles: form.fouilles, planets: form.planets, per_slot: form.per_slot, mira: form.mira,
             delay_min: form.delay_min, delay_max: form.delay_max };
  }

  $("exc-save").addEventListener("click", async () => {
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

  $("exc-scan").addEventListener("click", async () => {
    $("exc-scan").disabled = true;
    $("exc-scan-msg").textContent = "Relevé en cours (une à deux minutes selon le nombre de planètes)…";
    try {
      const res = await fetch(api + "/scan", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        data = body;
        if (!dirty) { form = fromSettings(data.settings); renderAll(); } else renderDaily(); // saisie en cours gardée
        $("exc-scan-msg").textContent = "";
      } else $("exc-scan-msg").textContent = errorText(body);
    } catch { $("exc-scan-msg").textContent = "Le bot ne répond pas."; }
    $("exc-scan").disabled = false;
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
      if (planetBox) planetBox.setPlanets(data.planets); // saisie en cours gardée : seuls la liste des planètes et le suivi changent
      renderDaily();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
