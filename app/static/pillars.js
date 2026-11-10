// Piliers programmés : état des Piliers de chaque planète (relevé dans « Gérer les Piliers »), programmation d'un changement
// (planète, Ouvrir / Fermer, date et heure où il est effectif) et liste des changements avec leur résultat.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/pillars";
  const ACTION = { open: "Ouvrir", close: "Fermer" };
  const STATE = { pending: "Programmé", done: "Fait", failed: "Échec", abandoned: "Abandonné" };
  const order = (names) => [...names].sort((a, b) => a.localeCompare(b, "fr", { numeric: true, sensitivity: "base" }));
  let data = null;

  const say = (text, kind) => { $("pil-msg").textContent = text; $("pil-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  /** « 11/11 à 09:00 », heure de Paris. */
  const when = (ts) => {
    const d = new Date(ts * 1000);
    const day = d.toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit" });
    const hour = d.toLocaleTimeString("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit" });
    return `${day} à ${hour}`;
  };
  const parisField = (ts) => new Date(ts * 1000).toLocaleString("sv-SE", { timeZone: "Europe/Paris" }).replace(" ", "T").slice(0, 16);
  /** Valeur du champ date/heure (« AAAA-MM-JJTHH:MM ») en heure de Paris → epoch, ou null. */
  function parseLocal(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
    if (!m) return null;
    const guess = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) / 1000;
    const s = parisField(guess);
    const drift = Date.UTC(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10), +s.slice(11, 13), +s.slice(14, 16)) / 1000 - guess;
    return guess - drift; // décalage de Paris (heure d'hiver / d'été)
  }
  /** Maintenant + 2 h, arrondi aux 5 minutes, au format du champ date/heure (heure de Paris). */
  const defaultAt = () => parisField(Math.ceil((Date.now() / 1000 + 2 * 3600) / 300) * 300);

  function renderStatus() {
    const st = data.status;
    const planets = st.planets || {};
    const names = order(Object.keys(planets));
    if (!st.read_at) {
      $("pil-status-line").textContent = "Aucun relevé pour l'instant : cliquez sur « Relever l'état des Piliers » (le bot doit être dans la partie).";
    } else {
      const closed = names.filter((n) => (planets[n].closed && planets[n].pending_to !== "open") || planets[n].pending_to === "close").length;
      $("pil-status-line").textContent = `Relevé le ${when(st.read_at)} — planètes aux Piliers fermés : ${closed} / ${data.rules.max_closed}.`;
    }
    $("pil-status").replaceChildren(...names.map((n) => {
      const p = planets[n];
      let text = p.closed ? "fermés" : "ouverts";
      if (p.pending_to) text = `${p.pending_to === "open" ? "ouverture" : "fermeture"} en cours` + (p.effective_at ? ` (effective le ${when(p.effective_at)})` : "");
      return el("li", { className: p.pending_to ? "pending" : p.closed ? "closed" : "open", textContent: `${n} : ${text}` });
    }));
    $("pil-scan").disabled = !!data.scanning;
    $("pil-scan").textContent = data.scanning ? "Relevé en cours…" : "Relever l'état des Piliers";
  }

  function renderPlanets() {
    const select = $("pil-planet");
    const chosen = select.value;
    const names = order(new Set([...data.planets, ...Object.keys(data.status.planets || {})]));
    select.replaceChildren(...names.map((n) => el("option", { value: n, textContent: n })));
    if (names.includes(chosen)) select.value = chosen;
  }

  function renderChanges() {
    if (!data.changes.length) {
      $("pil-changes").replaceChildren(el("li", { className: "empty-state", textContent: "Aucun changement programmé." }));
      return;
    }
    $("pil-changes").replaceChildren(...data.changes.map((c) => {
      const remove = el("button", { type: "button", className: "secondary small", textContent: c.state === "pending" ? "Supprimer" : "Retirer",
                                    ariaLabel: `Supprimer : ${ACTION[c.action]} ${c.planet} le ${when(c.at)}` });
      remove.addEventListener("click", async () => {
        const res = await fetch(`${api}/changes/${encodeURIComponent(c.id)}`, { method: "DELETE" });
        if (res.ok) { data = await res.json(); render(); say("Supprimé.", "ok"); } else say(errorText(await res.json().catch(() => ({}))), "error");
      });
      const detail = c.state === "pending"
        ? (c.problem ? el("span", { className: "warn", textContent: `Attention : ${c.problem}.` }) : el("span", { className: "hint", textContent: `clic du bot le ${when(c.click_at)}` }))
        : el("span", { className: c.state === "done" ? "hint" : "warn", textContent: c.note });
      const item = el("li", { className: `pil-change state-${c.state}` },
        el("strong", { className: "name", textContent: `${ACTION[c.action]} ${c.planet}` }),
        el("span", { textContent: `effectif le ${when(c.at)}` }),
        el("span", { className: `pill pil-${c.state}`, textContent: STATE[c.state] || c.state }),
        detail, remove);
      item.dataset.id = c.id;
      return item;
    }));
  }

  function renderHint() {
    const at = parseLocal($("pil-at").value);
    $("pil-hint").textContent = at ? `Le bot cliquera le ${when(at - 3600)} (le changement prend 1 h).` : "";
  }

  function render() {
    renderStatus();
    renderPlanets();
    renderChanges();
    if (data.error) $("pil-scan-msg").textContent = data.error;
  }

  $("pil-at").addEventListener("input", renderHint);
  $("pil-add").addEventListener("click", async () => {
    const action = document.querySelector('input[name="pil-action"]:checked').value;
    const body = { planet: $("pil-planet").value, action, at: $("pil-at").value };
    say("Vérification…");
    try {
      const res = await fetch(api + "/changes", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const reply = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(reply), "error"); return; }
      data = reply;
      render();
      say(`Programmé : ${ACTION[action].toLowerCase()} ${body.planet}.`, "ok");
    } catch { say("Le bot ne répond pas.", "error"); }
  });
  $("pil-scan").addEventListener("click", async () => {
    $("pil-scan").disabled = true;
    $("pil-scan-msg").textContent = "Relevé en cours…";
    try {
      const res = await fetch(api + "/scan", { method: "POST" });
      data = await res.json();
      render();
      if (!data.error) $("pil-scan-msg").textContent = "";
    } catch { $("pil-scan-msg").textContent = "Le bot ne répond pas."; }
  });

  async function refresh() {
    try {
      data = await (await fetch(api)).json();
      render();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  $("pil-at").value = defaultAt();
  renderHint();
  refresh();
  setInterval(refresh, 5000);
})();
