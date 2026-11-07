// Piratage : planète de départ, délai avant relance, Errants les plus proches, état du piratage en boucle de Naël Soryn.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/hackings";
  let data = null;
  let dirty = false;

  const say = (text, kind) => { $("hack-msg").textContent = text; $("hack-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

  function renderStatus() {
    const d = data.daily, st = data.state;
    const parts = [];
    if (d.day) parts.push(`Relevé du ${d.day} autour de ${d.planet}.`);
    else parts.push("Aucun relevé pour l'instant : le bot le fait au premier passage de la journée.");
    if (st.target) parts.push(` Cible : ${st.target}.`);
    if (st.end) parts.push(` Dernier piratage : fin ${clock(st.end)}, relance possible à ${clock(st.ready)}.`);
    if (d.error) parts.push(" ", el("strong", { className: "warn", textContent: d.error }));
    $("hack-status").replaceChildren(...parts);
    $("hack-targets").replaceChildren(...d.targets.map((t) => el("li", {
      textContent: `${t.name || t.address} (${t.address}) — distance ${t.distance}` + (st.refused.includes(t.address) ? " — refusé par le jeu" : "") })));
    $("hack-scan").disabled = !!data.scanning;
  }

  function render() {
    const s = data.settings;
    const items = [["", "— choisir —"], ...data.planets.map((p) => [p, p])];
    if (s.planet && !data.planets.includes(s.planet)) items.push([s.planet, s.planet]);
    $("hack-planet").replaceChildren(...items.map(([value, text]) => el("option", { value, textContent: text })));
    $("hack-planet").value = s.planet;
    $("hack-delay-min").value = s.delay_min ?? "";
    $("hack-delay-max").value = s.delay_max ?? "";
    renderStatus();
  }

  const number = (id) => ($(id).value === "" ? null : Number($(id).value));
  const formValue = () => ({ planet: $("hack-planet").value, delay_min: number("hack-delay-min"), delay_max: number("hack-delay-max") });
  ["hack-planet", "hack-delay-min", "hack-delay-max"].forEach((id) => $(id).addEventListener("input", () => { dirty = true; }));

  $("hack-save").addEventListener("click", async () => {
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

  $("hack-scan").addEventListener("click", async () => {
    $("hack-scan").disabled = true;
    say("Relevé des Errants en cours…");
    try {
      const res = await fetch(api + "/scan", { method: "POST" });
      const body = await res.json().catch(() => ({}));
      if (res.ok) {
        const keep = dirty ? formValue() : null;
        data = body;
        if (keep) data.settings = keep;
        render();
        say(data.settings.planet ? "Relevé terminé." : "Choisissez et enregistrez d'abord la planète de départ.", data.settings.planet ? "ok" : "error");
      } else say(errorText(body), "error");
    } catch { say("Le bot ne répond pas.", "error"); }
    $("hack-scan").disabled = false;
  });

  async function refresh() {
    try {
      const fresh = await (await fetch(api)).json();
      if (!data || !dirty) { data = fresh; render(); return; }
      data = { ...fresh, settings: formValue() };
      renderStatus();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
