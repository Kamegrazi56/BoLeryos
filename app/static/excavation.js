// Fouilles : lignes (planète de départ, équipe, durée, places, Mira Venn), délai avant relance, relevé du jour, Fouilles suivies.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/excavations";
  let data = null; // dernière réponse du bot
  let dirty = false; // saisie pas encore enregistrée : la relecture périodique ne l'écrase pas

  const say = (text, kind) => { $("exc-msg").textContent = text; $("exc-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

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
      textContent: `${t.planet} → ${t.site} (${t.duration} h) : fin ${clock(t.end)}, place libre à ${clock(t.ready)}` })) : [el("li", { textContent: "Aucune." })]));
    $("exc-scan").disabled = !!data.scanning;
    $("exc-scan").textContent = data.scanning ? "Relevé en cours…" : "Refaire le relevé du jour";
  }

  function lineRow(line, index) {
    const planet = el("select", { ariaLabel: `Planète de départ, ligne ${index + 1}` });
    const planets = data.planets.includes(line.planet) || !line.planet ? data.planets : [line.planet, ...data.planets];
    planet.replaceChildren(el("option", { value: "", textContent: "— planète —" }), ...planets.map((p) => el("option", { value: p, textContent: p })));
    planet.value = line.planet;
    planet.dataset.field = "planet";
    const duration = el("select", { ariaLabel: `Durée, ligne ${index + 1}` });
    duration.replaceChildren(...data.durations.map((h) => el("option", { value: String(h), textContent: `${h} h` })));
    duration.value = String(line.duration);
    duration.dataset.field = "duration";
    const slots = el("input", { type: "number", min: "1", max: "10", step: "1", value: String(line.slots), ariaLabel: `Places, ligne ${index + 1}` });
    slots.dataset.field = "slots";
    const mira = el("input", { type: "radio", name: "exc-mira", checked: line.mira, ariaLabel: `Mira Venn, ligne ${index + 1}` });
    mira.dataset.field = "mira";
    const total = el("span", { className: "hint" });
    const units = data.units.map((name) => {
      const input = el("input", { type: "number", min: "0", max: String(data.max_team), step: "1", value: line.units[name] ? String(line.units[name]) : "", ariaLabel: `${name}, ligne ${index + 1}` });
      input.dataset.unit = name;
      return el("label", {}, name + " ", input);
    });
    const remove = el("button", { type: "button", textContent: "Supprimer la ligne" });
    remove.addEventListener("click", () => {
      const current = formValue();
      data.settings = { ...current, lines: current.lines.filter((_, i) => i !== index) };
      dirty = true;
      render();
    });
    const row = el("fieldset", { className: "row" }, el("legend", { textContent: `Ligne ${index + 1}` }),
      el("label", {}, "Planète ", planet), el("label", {}, "Durée ", duration), el("label", {}, "Places ", slots),
      el("label", {}, mira, " Mira Venn"), ...units, total, remove);
    const sum = () => { total.textContent = `Équipe : ${units.reduce((s, l) => s + (Number(l.querySelector("input").value) || 0), 0)} / ${data.max_team}`; };
    row.addEventListener("input", () => { dirty = true; sum(); });
    row.addEventListener("change", () => { dirty = true; });
    sum();
    return row;
  }

  function render() {
    const s = data.settings;
    $("exc-lines").replaceChildren(...s.lines.map(lineRow));
    $("exc-delay-min").value = s.delay_min ?? "";
    $("exc-delay-max").value = s.delay_max ?? "";
    renderDaily();
  }

  function formValue() {
    const lines = [...document.querySelectorAll("#exc-lines fieldset")].map((row) => {
      const units = {};
      row.querySelectorAll("input[data-unit]").forEach((i) => { if (Number(i.value) > 0) units[i.dataset.unit] = Number(i.value); });
      return {
        planet: row.querySelector("[data-field=planet]").value,
        duration: Number(row.querySelector("[data-field=duration]").value),
        slots: Number(row.querySelector("[data-field=slots]").value),
        mira: row.querySelector("[data-field=mira]").checked,
        units,
      };
    });
    const number = (id) => ($(id).value === "" ? null : Number($(id).value));
    return { lines, delay_min: number("exc-delay-min"), delay_max: number("exc-delay-max") };
  }

  $("exc-add").addEventListener("click", () => {
    const current = formValue();
    data.settings = { ...current, lines: [...current.lines, { planet: "", duration: data.durations[0], slots: 1, mira: false, units: {} }] };
    dirty = true;
    render();
  });
  ["exc-delay-min", "exc-delay-max"].forEach((id) => $(id).addEventListener("input", () => { dirty = true; }));

  $("exc-save").addEventListener("click", async () => {
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

  $("exc-scan").addEventListener("click", async () => {
    $("exc-scan").disabled = true;
    $("exc-scan-msg").textContent = "Relevé en cours (une à deux minutes selon le nombre de planètes)…";
    try {
      const res = await fetch(api + "/scan", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        const keep = dirty ? formValue() : null;
        data = body;
        if (keep) data.settings = keep;
        render();
        $("exc-scan-msg").textContent = "";
      } else $("exc-scan-msg").textContent = errorText(body);
    } catch { $("exc-scan-msg").textContent = "Le bot ne répond pas."; }
    $("exc-scan").disabled = false;
  });

  async function refresh() {
    try {
      const fresh = await (await fetch(api)).json();
      if (!data || !dirty) { data = fresh; render(); return; }
      data = { ...fresh, settings: formValue() }; // relevé à jour, saisie en cours gardée
      renderDaily();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
