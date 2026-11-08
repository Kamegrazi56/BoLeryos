// Fonctionnalités : interrupteur, minuteur (minimum / maximum en minutes) et priorité de chaque fonctionnalité.
// Rendu à trois endroits : la liste « Fonctionnalités » (vue d'ensemble), l'en-tête de la page de chaque fonctionnalité
// (même interrupteur, même minuteur) et la pastille d'état du menu latéral.
(() => {
  const { el, dur, ago } = window.ui;
  const $ = (id) => document.getElementById(id);

  /** Page de réglages de chaque fonctionnalité (id de fonctionnalité → onglet). */
  const FEATURE_TAB = {
    constructions: "constructions", researches: "researches", populations: "populations", ships: "ships", components: "components",
    explorations: "explorations", excavations: "excavations", hackings: "hackings", minings: "minings", alliance_tokens: "alliance",
  };
  // « Test (ne fait rien) » ne sert qu'aux essais : masquée sauf si localStorage « boleryos.showTest » vaut « 1 ».
  const HIDDEN = new Set(["test"]);
  const visibleFeatures = (state) => state.features.filter((f) => !HIDDEN.has(f.id) || localStorage.getItem("boleryos.showTest") === "1");

  const detailText = (data, fallback) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || fallback);
  function say(text, kind) {
    for (const msg of [$("feat-msg"), ...document.querySelectorAll(".fhead-msg")]) {
      msg.textContent = text;
      msg.className = (msg.id === "feat-msg" ? "save-state " : "fhead-msg ") + (kind || "");
    }
  }
  const send = (url, body) => fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  let current = null; // dernier état reçu
  const timers = {};

  async function push(id, patch) {
    try {
      const res = await send(`/api/features/${id}`, patch);
      if (res.ok) { say("Enregistré.", "ok"); render(await res.json(), true); }
      else { say(detailText(await res.json().catch(() => ({})), "Refusé."), "error"); await refresh(true); }
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  }
  function schedulePush(id, readPatch) {
    clearTimeout(timers[id]);
    say("Enregistrement…");
    timers[id] = setTimeout(() => push(id, readPatch()), 600);
  }
  async function move(id, delta) {
    const order = current.features.map((f) => f.id);
    const shown = visibleFeatures(current).map((f) => f.id);
    const neighbour = shown[shown.indexOf(id) + delta];
    if (!neighbour) return;
    const i = order.indexOf(id), j = order.indexOf(neighbour);
    [order[i], order[j]] = [order[j], order[i]];
    const res = await send("/api/features/order", { order });
    if (res.ok) { say("Priorités enregistrées.", "ok"); render(await res.json(), true); }
    else say(detailText(await res.json().catch(() => ({})), "Refusé."), "error");
  }

  /** État résumé : run (passage en cours), err (dernier passage en échec), on (activée), off. */
  function stateOf(f) {
    if (f.running) return "run";
    if (!f.enabled) return "off";
    return f.last_ok === false ? "err" : "on";
  }
  const STATE_LABEL = { run: "En cours", err: "À vérifier", on: "Activée", off: "Désactivée" };

  function statusText(f, waiting) {
    if (f.running) return "Passage en cours…";
    const parts = [];
    if (f.enabled) {
      if (f.next_in_s === null) parts.push("Prochain passage : au démarrage du bot (ON)");
      else if (waiting && f.next_in_s === 0) parts.push(`En attente : ${waiting}`);
      else parts.push(f.next_in_s === 0 ? "Prochain passage : maintenant" : `Prochain passage dans ${dur(f.next_in_s)}`);
    } else parts.push("Désactivée");
    if (f.last_at) parts.push(`Dernier passage : ${ago(f.last_at).toLowerCase()} — ${f.last_result}`);
    return parts.join(" · ");
  }

  /** Interrupteur + minuteur d'une fonctionnalité ; `suffix` distingue les libellés de l'en-tête de page. */
  function controls(f, suffix = "") {
    const min = el("input", { type: "number", min: "0", step: "0.5", value: f.min_minutes ?? "", ariaLabel: `Minuteur minimum (minutes) de ${f.label}${suffix}` });
    const max = el("input", { type: "number", min: "0", step: "0.5", value: f.max_minutes ?? "", ariaLabel: `Minuteur maximum (minutes) de ${f.label}${suffix}` });
    const timer = () => ({ min_minutes: min.value === "" ? undefined : Number(min.value), max_minutes: max.value === "" ? undefined : Number(max.value) });
    min.addEventListener("input", () => schedulePush(f.id, timer));
    max.addEventListener("input", () => schedulePush(f.id, timer));
    const toggle = el("input", { type: "checkbox", checked: f.enabled, ariaLabel: `Activer ${f.label}${suffix}` });
    toggle.addEventListener("change", () => push(f.id, f.self_timed ? { enabled: toggle.checked } : { enabled: toggle.checked, ...timer() }));
    const switchBox = el("label", { className: "toggle" }, toggle, el("span", { textContent: "Activée" }));
    // Relance automatique (Fouilles, Piratage, Forages) : pas de minuteur, le bot repasse à la fin de chaque mission + le délai choisi.
    const timing = f.self_timed
      ? el("span", { className: "timing auto", title: "À la fin de chaque mission, plus le délai choisi dans sa page (vérification de secours toutes les 30 minutes).", textContent: "Relance automatique" })
      : el("span", { className: "timing" }, `${f.timer_label} `, min, " à ", max, " min");
    return { switchBox, timing };
  }

  function card(f, index, total, waiting) {
    const { switchBox, timing } = controls(f);
    const up = el("button", { type: "button", className: "secondary small icon", textContent: "↑", title: "Plus prioritaire", ariaLabel: `Monter ${f.label}`, disabled: index === 0 });
    const down = el("button", { type: "button", className: "secondary small icon", textContent: "↓", title: "Moins prioritaire", ariaLabel: `Descendre ${f.label}`, disabled: index === total - 1 });
    up.addEventListener("click", () => move(f.id, -1));
    down.addEventListener("click", () => move(f.id, 1));
    const tab = FEATURE_TAB[f.id];
    const name = tab ? el("a", { href: "#" + tab, className: "feature-name", textContent: f.label, title: f.description }) : el("strong", { className: "feature-name", textContent: f.label, title: f.description });
    const state = stateOf(f);
    const li = el("li", { className: `feature state-${state}` + (f.enabled ? " enabled" : "") },
      el("span", { className: "rank", textContent: String(index + 1) }),
      el("div", { className: "feature-main" },
        el("div", { className: "feature-head" }, name, el("span", { className: `pill ${state}`, textContent: STATE_LABEL[state] })),
        el("p", { className: "status-line feature-status", textContent: statusText(f, waiting) })),
      el("div", { className: "feature-controls" }, switchBox, timing),
      el("div", { className: "feature-order" }, up, down));
    li.dataset.feature = f.id;
    return li;
  }

  /** En-tête de la page d'une fonctionnalité : même interrupteur et même minuteur que la liste. */
  function head(f, waiting) {
    const { switchBox, timing } = controls(f, " (page)");
    const state = stateOf(f);
    return el("div", { className: `fhead state-${state}` },
      el("div", { className: "fhead-row" }, switchBox, el("span", { className: `pill ${state}`, textContent: STATE_LABEL[state] })),
      el("div", { className: "fhead-row" }, timing),
      el("p", { className: "status-line fhead-status", textContent: statusText(f, waiting) }),
      el("span", { className: "fhead-msg", role: "status", ariaLive: "polite" }));
  }

  const editingIn = (node) => node && document.activeElement && node.contains(document.activeElement) && document.activeElement.tagName === "INPUT";

  function renderHeads(state, force) {
    for (const f of state.features) {
      const tab = FEATURE_TAB[f.id];
      const slot = document.querySelector(`[data-feature-head="${f.id}"]`);
      const dot = tab && document.querySelector(`[data-dot="${tab}"]`);
      if (dot) { dot.className = `dot ${stateOf(f)}`; dot.title = STATE_LABEL[stateOf(f)]; }
      if (!slot) continue;
      if (editingIn(slot) && !force) { slot.querySelector(".fhead-status").textContent = statusText(f, state.waiting); continue; }
      slot.replaceChildren(head(f, state.waiting));
    }
  }

  function render(state, force = false) {
    current = state;
    $("bot-line").replaceChildren(
      "Bot : ", el("strong", { textContent: state.bot_on ? "ON" : (state.stopping ? "OFF (passage en cours…)" : "OFF") }),
      state.waiting ? ` — en attente : ${state.waiting}` : "",
      state.persistent ? "" : " — aucun volume /data : les réglages seront perdus au redémarrage",
    );
    renderHeads(state, force);
    const shown = visibleFeatures(state);
    if (editingIn($("features")) && !force) { // pas de redessin pendant la saisie : on ne met à jour que les lignes d'état
      shown.forEach((f) => {
        const line = document.querySelector(`[data-feature="${f.id}"] .feature-status`);
        if (line) line.textContent = statusText(f, state.waiting);
      });
      return;
    }
    $("features").replaceChildren(...shown.map((f, i) => card(f, i, shown.length, state.waiting)));
  }

  async function refresh(force = false) {
    try { render(await (await fetch("/api/features")).json(), force); } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh(true);
  setInterval(refresh, 2000);
})();
