// Horaires propres à une fonctionnalité (Piratage, Exploration) : plages par jour à l'intérieur du planning du bot.
// Monté dans chaque conteneur [data-feature-hours="<id>"] ; enregistré par PUT /api/feature-hours/<id>.
(() => {
  const { el } = window.ui;
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const NAMES = {
    hackings: { of: "du piratage", limit: "Limiter le piratage à des horaires" },
    explorations: { of: "de l'exploration", limit: "Limiter l'exploration à des horaires" },
  };
  const REFRESH_MS = 15000;
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const cap = (text) => text.charAt(0).toUpperCase() + text.slice(1);

  function mount(root) {
    const fid = root.dataset.featureHours;
    const name = NAMES[fid].of;
    const api = `/api/feature-hours/${fid}`;
    let data = null;
    let dirty = false;

    const enabled = el("input", { type: "checkbox", ariaLabel: NAMES[fid].limit });
    const days = el("div", { className: "plan-days fh-days" });
    const offMin = el("input", { type: "number", min: "0", max: "120", step: "1", ariaLabel: `Décalage aléatoire minimum des horaires ${name} (minutes)` });
    const offMax = el("input", { type: "number", min: "0", max: "120", step: "1", ariaLabel: `Décalage aléatoire maximum des horaires ${name} (minutes)` });
    const copy = el("button", { type: "button", className: "secondary", textContent: "Copier le lundi sur tous les jours" });
    const save = el("button", { type: "button", textContent: "Enregistrer les horaires" });
    const msg = el("p", { className: "msg", role: "status" });
    msg.setAttribute("aria-live", "polite");
    const status = el("p", { className: "status-line", role: "status" });
    status.setAttribute("aria-live", "polite");
    const details = el("div", { className: "fh-details" },
      days,
      el("div", { className: "row" },
        el("label", {}, "Décalage aléatoire des débuts et fins : de ", offMin),
        el("label", {}, "à ", offMax, " minutes"), copy));

    root.replaceChildren(
      el("h3", { textContent: `Horaires ${name}` }),
      el("p", { className: "hint", textContent: "Plages, à l'intérieur du planning du bot, pendant lesquelles cette fonctionnalité peut lancer. "
        + "Hors de ses plages elle ne lance rien de nouveau (une mission partie va jusqu'au bout) et reprend seule au début de la plage suivante. "
        + "Un jour sans plage : rien ce jour-là." }),
      el("div", { className: "row" }, el("label", { className: "switch" }, enabled, " " + NAMES[fid].limit)),
      details,
      status,
      el("div", { className: "row actions" }, save, msg),
    );

    const say = (text, kind) => { msg.textContent = text; msg.className = "msg " + (kind || ""); };
    const label = (day) => cap(data.days[day]);
    const globalHint = (day) => {
      const g = data.global;
      if (!g || !g.enabled) return "Planning du bot : actif toute la journée.";
      const slots = g.days[day];
      return slots.length ? `Planning du bot : ${slots.map((s) => `${s.start} → ${s.end}`).join(", ")}.` : "Planning du bot : inactif ce jour-là.";
    };

    function formValue() {
      const out = Object.fromEntries(DAYS.map((d) => [d, []]));
      days.querySelectorAll(".plan-slot").forEach((row) => {
        out[row.dataset.day].push({ start: row.querySelector("[data-part=start]").value, end: row.querySelector("[data-part=end]").value });
      });
      return { enabled: enabled.checked, days: out, offset_min: Number(offMin.value) || 0, offset_max: Number(offMax.value) || 0 };
    }

    function edit(change) {
      const current = formValue();
      change(current);
      data.settings = current;
      dirty = true;
      render();
    }

    function slotRow(day, slot, index) {
      const start = el("input", { type: "time", value: slot.start, ariaLabel: `Horaires ${name}, ${label(day)}, plage ${index + 1}, début` });
      const end = el("input", { type: "time", value: slot.end, ariaLabel: `Horaires ${name}, ${label(day)}, plage ${index + 1}, fin` });
      start.dataset.part = "start";
      end.dataset.part = "end";
      const remove = el("button", { type: "button", textContent: "Retirer", ariaLabel: `Retirer la plage ${index + 1} du ${data.days[day]} (horaires ${name})` });
      remove.addEventListener("click", () => edit((c) => { c.days[day] = c.days[day].filter((_, i) => i !== index); }));
      const row = el("div", { className: "row plan-slot" }, "de ", start, " à ", end, " ", remove);
      row.dataset.day = day;
      return row;
    }

    function render() {
      const s = data.settings;
      enabled.checked = s.enabled;
      offMin.value = s.offset_min;
      offMax.value = s.offset_max;
      details.hidden = !s.enabled;
      days.replaceChildren(...DAYS.map((day) => {
        const add = el("button", { type: "button", textContent: "Ajouter une plage", ariaLabel: `Ajouter une plage le ${data.days[day]} (horaires ${name})` });
        add.addEventListener("click", () => edit((c) => { c.days[day] = [...c.days[day], { start: "08:00", end: "10:00" }]; }));
        const slots = s.days[day].map((slot, i) => slotRow(day, slot, i));
        return el("fieldset", { className: "plan-day" }, el("legend", { textContent: label(day) }),
          el("p", { className: "hint", textContent: globalHint(day) }),
          ...(slots.length ? slots : [el("p", { className: "hint", textContent: "Aucune plage : rien ce jour-là." })]), add);
      }));
      status.textContent = data.message;
    }

    root.addEventListener("input", () => { dirty = true; });
    enabled.addEventListener("change", () => { details.hidden = !enabled.checked; });
    copy.addEventListener("click", () => edit((c) => {
      DAYS.slice(1).forEach((d) => { c.days[d] = c.days.mon.map((slot) => ({ ...slot })); });
    }));

    save.addEventListener("click", async () => {
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
