// Planning : plages horaires actives par jour (heure française), décalage aléatoire, état actuel.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/planning";
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  let data = null;
  let dirty = false;

  const say = (text, kind) => { $("plan-msg").textContent = text; $("plan-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const label = (day) => data.days[day].charAt(0).toUpperCase() + data.days[day].slice(1);

  function slotRow(day, slot, index) {
    const start = el("input", { type: "time", value: slot.start, ariaLabel: `${label(day)}, plage ${index + 1}, début` });
    const end = el("input", { type: "time", value: slot.end, ariaLabel: `${label(day)}, plage ${index + 1}, fin` });
    start.dataset.part = "start";
    end.dataset.part = "end";
    const remove = el("button", { type: "button", textContent: "Retirer", ariaLabel: `Retirer la plage ${index + 1} du ${data.days[day]}` });
    remove.addEventListener("click", () => {
      const current = formValue();
      current.days[day] = current.days[day].filter((_, i) => i !== index);
      data.settings = current;
      dirty = true;
      render();
    });
    const row = el("div", { className: "row plan-slot" }, "de ", start, " à ", end, " ", remove);
    row.dataset.day = day;
    return row;
  }

  function render() {
    const s = data.settings;
    $("plan-enabled").checked = s.enabled;
    $("plan-offset-min").value = s.offset_min;
    $("plan-offset-max").value = s.offset_max;
    $("plan-days").replaceChildren(...DAYS.map((day) => {
      const add = el("button", { type: "button", textContent: "Ajouter une plage", ariaLabel: `Ajouter une plage le ${data.days[day]}` });
      add.addEventListener("click", () => {
        const current = formValue();
        current.days[day] = [...current.days[day], { start: "08:00", end: "12:00" }];
        data.settings = current;
        dirty = true;
        render();
      });
      const slots = s.days[day].map((slot, i) => slotRow(day, slot, i));
      return el("fieldset", { className: "plan-day" }, el("legend", { textContent: label(day) }),
        ...(slots.length ? slots : [el("p", { className: "hint", textContent: "Aucune plage : inactif ce jour-là." })]), add);
    }));
    $("plan-status").textContent = data.message;
  }

  function formValue() {
    const days = Object.fromEntries(DAYS.map((d) => [d, []]));
    document.querySelectorAll("#plan-days .plan-slot").forEach((row) => {
      days[row.dataset.day].push({ start: row.querySelector("[data-part=start]").value, end: row.querySelector("[data-part=end]").value });
    });
    return {
      enabled: $("plan-enabled").checked,
      days,
      offset_min: Number($("plan-offset-min").value) || 0,
      offset_max: Number($("plan-offset-max").value) || 0,
    };
  }

  $("panel-planning").addEventListener("input", () => { dirty = true; });

  $("plan-save").addEventListener("click", async () => {
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
  setInterval(refresh, 15000);
})();
