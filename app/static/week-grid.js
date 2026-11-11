// Grille de la semaine à « peindre » : 7 jours × 96 quarts d'heure, plus un champ texte par jour (saisie au clavier).
// Format échangé : celui du planning ({mon: [{start: "08:00", end: "10:00"}], ...}) ; une plage qui passe minuit
// reste UNE plage (le décalage aléatoire s'applique à son début et à sa fin, pas à minuit).
// Les heures exactes (07:05…) sont gardées tant qu'on ne peint pas : la peinture arrondit au quart d'heure.
(() => {
  const { el } = window.ui;
  const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
  const LABELS = { mon: "Lun", tue: "Mar", wed: "Mer", thu: "Jeu", fri: "Ven", sat: "Sam", sun: "Dim" };
  const NAMES = { mon: "lundi", tue: "mardi", wed: "mercredi", thu: "jeudi", fri: "vendredi", sat: "samedi", sun: "dimanche" };
  const STEP = 15; // minutes par case
  const PER_DAY = 1440 / STEP; // 96
  const WEEK = PER_DAY * 7; // 672
  const MAX_SLOTS_PER_DAY = 10; // comme le serveur (planning.py)
  const TOKEN = /(\d{1,2})(?:[:h](\d{2})?)?\s*(?:-|–|à|→|>)\s*(\d{1,2})(?:[:h](\d{2})?)?/g;

  const pad = (n) => String(n).padStart(2, "0");
  const fmt = (minutes) => `${pad(Math.floor((minutes % 1440) / 60))}:${pad(minutes % 60)}`;
  const toMin = (text) => { const [h, m] = text.split(":").map(Number); return h * 60 + m; };
  const emptyDays = () => Object.fromEntries(DAYS.map((d) => [d, []]));
  const byStart = (x, y) => x.start.localeCompare(y.start);

  /** Plages → cases allumées (Uint8Array de 672), plages qui passent minuit comprises (dimanche → lundi aussi). */
  function toCells(days) {
    const cells = new Uint8Array(WEEK);
    DAYS.forEach((day, d) => (days[day] || []).forEach((slot) => {
      const a = toMin(slot.start);
      let b = toMin(slot.end);
      if (b <= a) b += 1440;
      for (let i = Math.round(a / STEP); i < Math.round(b / STEP); i += 1) cells[(d * PER_DAY + i) % WEEK] = 1;
    }));
    return cells;
  }

  /** Une suite de cases [s, e) → plages du jour où elle commence ; au-delà de 24 h, coupée à minuit. */
  function split(s, e, push) {
    while (s < e) {
      const d = Math.floor(s / PER_DAY), m = s % PER_DAY, midnight = (d + 1) * PER_DAY;
      if (m === 0 && e - s >= PER_DAY) { // journée entière : 2 plages (00:00 → 00:00 serait refusé)
        push(d, 0, PER_DAY / 2);
        push(d, PER_DAY / 2, PER_DAY);
      } else if (e <= midnight || e - s < PER_DAY) { // dans la journée, ou passe minuit en moins de 24 h
        push(d, m, e - d * PER_DAY);
        return;
      } else push(d, m, PER_DAY); // plus de 24 h : coupée à minuit
      s = midnight;
    }
  }

  /** Cases allumées → plages : une suite qui passe minuit devient une seule plage du jour où elle commence. */
  function fromCells(cells) {
    const days = emptyDays();
    const push = (d, from, to) => days[DAYS[d % 7]].push({ start: fmt(from * STEP), end: fmt(to * STEP) });
    if (cells.every(Boolean)) { // toute la semaine : 2 plages par jour (une plage ne peut pas faire 24 h)
      DAYS.forEach((_, d) => { push(d, 0, PER_DAY / 2); push(d, PER_DAY / 2, PER_DAY); });
      return days;
    }
    const z = cells.indexOf(0); // on parcourt la semaine à partir d'une case éteinte : aucune suite coupée
    let start = null;
    for (let p = z + 1; p <= z + WEEK; p += 1) {
      const on = cells[p % WEEK] === 1;
      if (on && start === null) start = p;
      if (on && cells[(p + 1) % WEEK] !== 1) { split(start, p + 1, push); start = null; }
    }
    DAYS.forEach((d) => days[d].sort(byStart));
    return days;
  }

  /** « 8-10, 16h30-20, 22-2 » → plages ; lève une Error lisible si le texte n'est pas compris. */
  function parse(text) {
    const slots = [];
    const rest = text.replace(TOKEN, (_, h1, m1, h2, m2) => {
      const [a, b] = [[h1, m1], [h2, m2]].map(([h, m]) => {
        const hh = Number(h), mm = Number(m || 0);
        if (hh > 24 || mm > 59 || (hh === 24 && mm > 0)) throw new Error(`heure impossible (${h}${m ? ":" + m : ""})`);
        return (hh * 60 + mm) % 1440;
      });
      if (a === b) throw new Error(`début et fin identiques (${fmt(a)})`);
      slots.push({ start: fmt(a), end: fmt(b) });
      return "";
    });
    if (rest.replace(/[\s,;]+/g, "")) throw new Error(`« ${rest.trim()} » non compris (exemple : 8-10, 16h30-20)`);
    return slots.sort(byStart);
  }

  const show = (slots) => slots.map((s) => `${s.start}-${s.end}`).join(", ");

  /**
   * @param {{name: string, onChange?: () => void}} options  name : préfixe des libellés (« Planning », « Horaires du piratage »)
   */
  function create({ name, onChange = () => {} }) {
    let days = emptyDays();
    let blocked = new Uint8Array(WEEK); // cases grisées : bot à l'arrêt (planning du bot)
    let drag = null;
    const cellEls = [];
    const inputs = {};
    const notes = {};

    const scale = el("div", { className: "wg-hours" }, el("span"),
      el("div", { className: "wg-scale" }, ...Array.from({ length: 12 }, (_, i) => el("span", { textContent: `${i * 2}h` }))), el("span"));
    scale.setAttribute("aria-hidden", "true");
    const rows = DAYS.map((day, d) => {
      const strip = el("div", { className: "wg-cells" });
      strip.setAttribute("aria-hidden", "true");
      for (let i = 0; i < PER_DAY; i += 1) {
        const cell = el("div", { className: "wg-cell" + (i % 4 === 0 ? " wg-hour" : "") });
        cell.dataset.d = d;
        cell.dataset.i = i;
        strip.append(cell);
        cellEls[d * PER_DAY + i] = cell;
      }
      const input = el("input", { type: "text", className: "wg-text", placeholder: "rien ce jour-là", ariaLabel: `${name}, ${NAMES[day]}` });
      input.spellcheck = false;
      input.addEventListener("change", () => {
        try {
          days = { ...days, [day]: parse(input.value) };
          refresh();
          onChange();
        } catch (err) { note(day, err.message); }
      });
      inputs[day] = input;
      notes[day] = el("span", { className: "wg-note", role: "status" });
      return el("div", { className: "wg-row" }, el("span", { className: "wg-day", textContent: LABELS[day] }), strip,
        el("div", { className: "wg-side" }, input, notes[day]));
    });
    const element = el("div", { className: "week-grid" }, scale, ...rows);

    function note(day, text) {
      notes[day].textContent = text;
      inputs[day].classList.toggle("invalid", Boolean(text));
    }

    function paintCells(preview) {
      const on = toCells(days);
      for (let k = 0; k < WEEK; k += 1) {
        const inPreview = Boolean(preview && preview.cells.has(k));
        cellEls[k].classList.toggle("on", inPreview ? preview.mode : on[k] === 1);
        cellEls[k].classList.toggle("blocked", blocked[k] === 1);
        cellEls[k].classList.toggle("preview", inPreview);
      }
    }

    function refresh() {
      paintCells(null);
      DAYS.forEach((day) => {
        if (document.activeElement !== inputs[day]) inputs[day].value = show(days[day]);
        note(day, days[day].length > MAX_SLOTS_PER_DAY ? `${days[day].length} plages : ${MAX_SLOTS_PER_DAY} au plus` : "");
      });
    }

    // --- peinture à la souris ou au doigt : la première case décide (allumer ou éteindre), le rectangle s'applique au lâcher
    const pos = (cell) => ({ d: Number(cell.dataset.d), i: Number(cell.dataset.i) });
    const cellAt = (x, y) => {
      const c = document.elementFromPoint(x, y);
      return c && c.classList.contains("wg-cell") && element.contains(c) ? c : null;
    };
    function rect(a, b, mode) {
      const cells = new Set();
      const [d1, d2] = [Math.min(a.d, b.d), Math.max(a.d, b.d)];
      const [i1, i2] = [Math.min(a.i, b.i), Math.max(a.i, b.i)];
      for (let d = d1; d <= d2; d += 1) {
        for (let i = i1; i <= i2; i += 1) {
          const k = d * PER_DAY + i;
          if (!(mode && blocked[k])) cells.add(k); // on n'allume pas une heure où le bot est à l'arrêt
        }
      }
      return cells;
    }
    const preview = () => paintCells({ cells: rect(drag.start, drag.end, drag.mode), mode: drag.mode });

    element.addEventListener("pointerdown", (ev) => {
      const cell = ev.target.closest(".wg-cell");
      if (!cell || ev.button !== 0) return;
      ev.preventDefault();
      drag = { start: pos(cell), end: pos(cell), mode: !cell.classList.contains("on") };
      element.setPointerCapture(ev.pointerId); // le lâcher arrive ici même hors de la grille
      preview();
    });
    element.addEventListener("pointermove", (ev) => {
      if (!drag) return;
      const cell = cellAt(ev.clientX, ev.clientY); // la capture empêche pointerenter : on cherche la case sous le pointeur
      if (!cell) return;
      drag.end = pos(cell);
      preview();
    });
    const finish = (apply) => {
      if (!drag) return;
      if (apply) {
        const cells = toCells(days);
        rect(drag.start, drag.end, drag.mode).forEach((k) => { cells[k] = drag.mode ? 1 : 0; });
        days = fromCells(cells);
      }
      drag = null;
      refresh();
      if (apply) onChange();
    };
    element.addEventListener("pointerup", () => finish(true));
    element.addEventListener("pointercancel", () => finish(false));

    refresh();
    return {
      element,
      /** @param {object} value plages par jour ; @param {object|null} limit planning du bot (heures hors planning grisées) */
      set(value, limit = null) {
        days = { ...emptyDays(), ...JSON.parse(JSON.stringify(value || {})) };
        blocked = limit && limit.enabled ? toCells(limit.days).map((v) => 1 - v) : new Uint8Array(WEEK);
        refresh();
      },
      get: () => JSON.parse(JSON.stringify(days)),
      /** Erreurs à corriger avant d'enregistrer (texte non compris, trop de plages). */
      errors: () => DAYS.filter((d) => notes[d].textContent).map((d) => `${NAMES[d]} : ${notes[d].textContent}`),
    };
  }

  window.weekGrid = { create, toCells, fromCells, parse };
})();
