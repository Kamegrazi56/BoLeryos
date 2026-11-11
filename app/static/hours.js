// Horaires propres à une fonctionnalité (Piratage, Exploration) : plages par jour à l'intérieur du planning du bot,
// saisies sur la grille de la semaine (heures où le bot est à l'arrêt grisées).
// Monté dans chaque conteneur [data-feature-hours="<id>"] ; enregistré par PUT /api/feature-hours/<id>.
(() => {
  const { el } = window.ui;
  const NAMES = {
    hackings: { of: "du piratage", limit: "Limiter le piratage à des horaires" },
    explorations: { of: "de l'exploration", limit: "Limiter l'exploration à des horaires" },
  };
  const REFRESH_MS = 15000;
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");

  function mount(root) {
    const fid = root.dataset.featureHours;
    const name = NAMES[fid].of;
    const api = `/api/feature-hours/${fid}`;
    let data = null;
    let dirty = false;

    const grid = window.weekGrid.create({ name: `Horaires ${name}`, onChange: () => { dirty = true; } });
    const enabled = el("input", { type: "checkbox", ariaLabel: NAMES[fid].limit });
    const offMin = el("input", { type: "number", min: "0", max: "120", step: "1", ariaLabel: `Décalage aléatoire minimum des horaires ${name} (minutes)` });
    const offMax = el("input", { type: "number", min: "0", max: "120", step: "1", ariaLabel: `Décalage aléatoire maximum des horaires ${name} (minutes)` });
    const save = el("button", { type: "button", textContent: "Enregistrer les horaires" });
    const msg = el("p", { className: "msg", role: "status" });
    msg.setAttribute("aria-live", "polite");
    const status = el("p", { className: "status-line", role: "status" });
    status.setAttribute("aria-live", "polite");
    const details = el("div", { className: "fh-details" },
      el("p", { className: "hint wg-legend" },
        el("span", { className: "wg-key on" }), " actif ", el("span", { className: "wg-key blocked" }), " bot à l'arrêt (planning du bot)"),
      grid.element,
      el("div", { className: "row" },
        el("label", {}, "Décalage aléatoire des débuts et fins : de ", offMin),
        el("label", {}, "à ", offMax, " minutes")));

    root.replaceChildren(
      el("h3", { textContent: `Horaires ${name}` }),
      el("p", { className: "hint", textContent: "Cliquez-glissez sur la grille pour allumer ou éteindre des heures (vers le bas : plusieurs jours d'un coup), "
        + "ou tapez les plages à droite (ex. 8-10, 16-20). Hors de ses plages la fonctionnalité ne lance rien de nouveau "
        + "(une mission partie va jusqu'au bout) et reprend seule au début de la plage suivante. Un jour sans plage : rien ce jour-là." }),
      el("div", { className: "row" }, el("label", { className: "switch" }, enabled, " " + NAMES[fid].limit)),
      details,
      status,
      el("div", { className: "row actions" }, save, msg),
    );

    const say = (text, kind) => { msg.textContent = text; msg.className = "msg " + (kind || ""); };

    function formValue() {
      return { enabled: enabled.checked, days: grid.get(), offset_min: Number(offMin.value) || 0, offset_max: Number(offMax.value) || 0 };
    }

    function render() {
      const s = data.settings;
      enabled.checked = s.enabled;
      offMin.value = s.offset_min;
      offMax.value = s.offset_max;
      details.hidden = !s.enabled;
      grid.set(s.days, data.global);
      status.textContent = data.message;
    }

    root.addEventListener("input", () => { dirty = true; });
    enabled.addEventListener("change", () => { details.hidden = !enabled.checked; });

    save.addEventListener("click", async () => {
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
        status.textContent = data.message;
      } catch { /* le bot redémarre : on réessaie */ }
    }
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  document.querySelectorAll("[data-feature-hours]").forEach((root) => { if (NAMES[root.dataset.featureHours]) mount(root); });
})();
