// Forages : planètes de départ, ressources visées, Mineurs, places, délai avant relance, forages suivis.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/minings";
  let data = null;
  let dirty = false;

  const say = (text, kind) => { $("mine-msg").textContent = text; $("mine-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const PHASES = { aller: "en route", extraction: "extraction en cours", retour: "retour", "terminé": "terminé" };

  function boxes(container, items, chosen, label) {
    container.replaceChildren(...items.map(([value, text]) => {
      const box = el("input", { type: "checkbox", checked: chosen.includes(value), ariaLabel: `${label} ${text}` });
      box.dataset.value = value;
      return el("label", {}, box, " " + text);
    }));
  }

  function renderTracked() {
    $("mine-tracked").replaceChildren(...(data.tracked.length ? data.tracked.map((t) => el("li", {
      textContent: `${t.planet} → ${t.site} : ${PHASES[t.phase] || t.phase}` + (t.end ? `, fin ${clock(t.end)}` : "") + (t.ready ? `, place libre à ${clock(t.ready)}` : "") })) : [el("li", { textContent: "Aucun." })]));
  }

  function render() {
    const s = data.settings;
    const planets = data.planets.includes ? [...new Set([...data.planets, ...s.planets])] : s.planets;
    boxes($("mine-planets"), planets.map((p) => [p, p]), s.planets, "Planète de départ");
    boxes($("mine-resources"), Object.entries(data.resources), s.resources, "Ressource");
    $("mine-miners").value = s.miners;
    $("mine-slots").value = s.slots;
    $("mine-delay-min").value = s.delay_min ?? "";
    $("mine-delay-max").value = s.delay_max ?? "";
    renderTracked();
  }

  const checked = (id) => [...document.querySelectorAll(`#${id} input:checked`)].map((b) => b.dataset.value);
  const number = (id) => ($(id).value === "" ? null : Number($(id).value));
  const formValue = () => ({
    planets: checked("mine-planets"), resources: checked("mine-resources"),
    miners: number("mine-miners") ?? 0, slots: number("mine-slots") ?? 0,
    delay_min: number("mine-delay-min"), delay_max: number("mine-delay-max"),
  });
  $("panel-minings").addEventListener("input", () => { dirty = true; });

  $("mine-save").addEventListener("click", async () => {
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

  async function refresh() {
    try {
      const fresh = await (await fetch(api)).json();
      if (!data || !dirty) { data = fresh; render(); return; }
      data = { ...fresh, settings: formValue() };
      renderTracked();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 10000);
})();
