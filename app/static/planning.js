// Planning : plages horaires actives par jour (heure française), saisies sur la grille de la semaine, décalage aléatoire.
(() => {
  const $ = (id) => document.getElementById(id);
  const api = "/api/planning";
  const REFRESH_MS = 15000;
  let data = null;
  let dirty = false;

  const say = (text, kind) => { $("plan-msg").textContent = text; $("plan-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const grid = window.weekGrid.create({ name: "Planning", onChange: () => { dirty = true; } });
  $("plan-days").replaceChildren(grid.element);

  function render() {
    const s = data.settings;
    $("plan-enabled").checked = s.enabled;
    $("plan-offset-min").value = s.offset_min;
    $("plan-offset-max").value = s.offset_max;
    grid.set(s.days);
    $("plan-status").textContent = data.message;
  }

  function formValue() {
    return {
      enabled: $("plan-enabled").checked,
      days: grid.get(),
      offset_min: Number($("plan-offset-min").value) || 0,
      offset_max: Number($("plan-offset-max").value) || 0,
    };
  }

  $("panel-planning").addEventListener("input", () => { dirty = true; });

  $("plan-save").addEventListener("click", async () => {
    const problems = grid.errors();
    if (problems.length) { say(`À corriger : ${problems.join(" · ")}`, "error"); return; }
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
      $("plan-status").textContent = data.message;
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
