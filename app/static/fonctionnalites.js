// Page Fonctionnalités : interrupteur, minuteur (minimum / maximum en minutes) et priorité de chaque fonctionnalité.
(() => {
  const { el, dur, ago } = window.ui;
  const $ = (id) => document.getElementById(id);

  const detailText = (data, fallback) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || fallback);
  function say(text, kind) {
    const msg = $("feat-msg");
    msg.textContent = text;
    msg.className = "save-state " + (kind || "");
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
    const i = order.indexOf(id), j = i + delta;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    const res = await send("/api/features/order", { order });
    if (res.ok) { say("Priorités enregistrées.", "ok"); render(await res.json(), true); }
    else say(detailText(await res.json().catch(() => ({})), "Refusé."), "error");
  }

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

  function card(f, index, total, waiting) {
    const min = el("input", { type: "number", min: "0", step: "0.5", value: f.min_minutes ?? "", ariaLabel: `Minuteur minimum (minutes) de ${f.label}` });
    const max = el("input", { type: "number", min: "0", step: "0.5", value: f.max_minutes ?? "", ariaLabel: `Minuteur maximum (minutes) de ${f.label}` });
    const timer = () => ({ min_minutes: min.value === "" ? undefined : Number(min.value), max_minutes: max.value === "" ? undefined : Number(max.value) });
    min.addEventListener("input", () => schedulePush(f.id, timer));
    max.addEventListener("input", () => schedulePush(f.id, timer));
    const toggle = el("input", { type: "checkbox", checked: f.enabled, ariaLabel: `Activer ${f.label}` });
    toggle.addEventListener("change", () => push(f.id, f.self_timed ? { enabled: toggle.checked } : { enabled: toggle.checked, ...timer() }));
    // Relance automatique (Fouilles, Piratage) : pas de minuteur, le bot repasse à la fin de chaque mission + le délai choisi.
    const timing = f.self_timed
      ? el("p", { className: "hint feature-timing", textContent: "Relance automatique : à la fin de chaque mission, plus le délai choisi dans son onglet (vérification de secours toutes les 30 minutes)." })
      : el("div", { className: "row" }, el("label", {}, `${f.timer_label} `, min, " à ", max, " minutes"));
    const up = el("button", { type: "button", className: "secondary small", textContent: "↑", title: "Plus prioritaire", ariaLabel: `Monter ${f.label}`, disabled: index === 0 });
    const down = el("button", { type: "button", className: "secondary small", textContent: "↓", title: "Moins prioritaire", ariaLabel: `Descendre ${f.label}`, disabled: index === total - 1 });
    up.addEventListener("click", () => move(f.id, -1));
    down.addEventListener("click", () => move(f.id, 1));
    const li = el("li", { className: "feature" + (f.enabled ? " enabled" : "") },
      el("div", { className: "feature-head" },
        el("span", { className: "rank", textContent: String(index + 1) }),
        el("strong", { textContent: f.label }),
        el("label", { className: "switch" }, toggle, " Activée"),
        el("span", { className: "spacer" }), up, down),
      el("p", { className: "hint", textContent: f.description }),
      timing,
      el("p", { className: "status-line feature-status", textContent: statusText(f, waiting) }));
    li.dataset.feature = f.id;
    return li;
  }

  function render(state, force = false) {
    current = state;
    $("bot-line").replaceChildren(
      "Bot : ", el("strong", { textContent: state.bot_on ? "ON" : (state.stopping ? "OFF (passage en cours…)" : "OFF") }),
      state.waiting ? ` — en attente : ${state.waiting}` : "",
      state.persistent ? "" : " — aucun volume /data : les réglages seront perdus au redémarrage",
    );
    const editing = document.activeElement && $("features").contains(document.activeElement) && document.activeElement.tagName === "INPUT";
    if (editing && !force) { // pas de redessin pendant la saisie : on ne met à jour que les lignes d'état
      state.features.forEach((f) => {
        const line = document.querySelector(`[data-feature="${f.id}"] .feature-status`);
        if (line) line.textContent = statusText(f, state.waiting);
      });
      return;
    }
    $("features").replaceChildren(...state.features.map((f, i) => card(f, i, state.features.length, state.waiting)));
  }

  async function refresh(force = false) {
    try { render(await (await fetch("/api/features")).json(), force); } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh(true);
  setInterval(refresh, 2000);
})();
