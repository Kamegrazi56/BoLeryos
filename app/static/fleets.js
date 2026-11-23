// Flottes : relevé des flottes en vol (panneau Capteurs du jeu), choix de l'heure de retour et liste des retours programmés.
(() => {
  const { el, dur } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/fleets";
  const STATE = { pending: "Programmé", done: "Fait", failed: "Échec", abandoned: "Abandonné" };
  let data = null;

  const say = (text, kind) => { $("flt-msg").textContent = text; $("flt-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  /** « 22/11 à 21:53:10 », heure de Paris. */
  const when = (ts) => {
    const d = new Date(ts * 1000);
    const day = d.toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", day: "2-digit", month: "2-digit" });
    const hour = d.toLocaleTimeString("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    return `${day} à ${hour}`;
  };
  const place = (p) => [p.name, p.coords].filter(Boolean).join(" ") || "?";
  const label = (f) => `${f.kind || "Flotte"} · ${place(f.origin)} → ${place(f.destination)}`;
  const content = (f) => f.units || "contenu non lu";

  function renderFleets() {
    const line = $("flt-status-line");
    if (!data.read_at) line.textContent = "Aucun relevé pour l'instant : cliquez sur « Relever les flottes en vol » (le bot doit être dans la partie).";
    else line.textContent = `Relevé le ${when(data.read_at)} — ${data.fleets.length} flotte(s) proposée(s)` + (data.excluded ? `, ${data.excluded} masquée(s) (exploration, fouille, forage, piratage, espionnage).` : ".");
    const items = data.fleets.map((f) => el("li", { className: "flt-fleet" },
      el("strong", { className: "name", textContent: label(f) }),
      el("span", { textContent: content(f) }),
      el("span", { className: "hint", textContent: f.left == null ? "temps restant illisible" : `reste ${dur(f.left)}` }),
      f.can_recall ? el("span", { className: "pill", textContent: "rappelable" }) : el("span", { className: "warn", textContent: "non rappelable" })));
    $("flt-fleets").replaceChildren(...(items.length ? items : [el("li", { className: "empty-state", textContent: "Aucune flotte à afficher." })]));
    const select = $("flt-fleet");
    const chosen = select.value;
    const usable = data.fleets.filter((f) => f.can_recall);
    select.replaceChildren(...usable.map((f) => el("option", { value: f.id, textContent: `${label(f)} (${content(f)})` })));
    if (usable.some((f) => f.id === chosen)) select.value = chosen;
    $("flt-scan").disabled = !!data.scanning;
    $("flt-scan").textContent = data.scanning ? "Relevé en cours…" : "Relever les flottes en vol";
  }

  function renderRecalls() {
    if (!data.recalls.length) {
      $("flt-recalls").replaceChildren(el("li", { className: "empty-state", textContent: "Aucun retour programmé." }));
      return;
    }
    $("flt-recalls").replaceChildren(...data.recalls.map((r) => {
      const remove = el("button", { type: "button", className: "secondary small", textContent: r.state === "pending" ? "Supprimer" : "Retirer",
                                    ariaLabel: `Supprimer le retour de ${r.fleet.label}` });
      remove.addEventListener("click", async () => {
        const res = await fetch(`${api}/recalls/${encodeURIComponent(r.id)}`, { method: "DELETE" });
        if (res.ok) { data = await res.json(); render(); say("Supprimé.", "ok"); } else say(errorText(await res.json().catch(() => ({}))), "error");
      });
      const detail = r.state === "pending"
        ? el("span", { className: "hint", textContent: r.next_check ? `prochain contrôle du bot le ${when(r.next_check)}` : "en attente" })
        : el("span", { className: r.state === "done" ? "hint" : "warn", textContent: r.note });
      const item = el("li", { className: `flt-recall state-${r.state}` },
        el("strong", { className: "name", textContent: r.fleet.label }),
        el("span", { textContent: `rentrée voulue le ${when(r.at)}` }),
        el("span", { className: `pill flt-${r.state}`, textContent: STATE[r.state] || r.state }),
        detail, remove);
      item.dataset.id = r.id;
      return item;
    }));
  }

  function render() {
    renderFleets();
    renderRecalls();
    if (document.activeElement !== $("flt-margin")) $("flt-margin").value = data.margin;
    if (data.error) $("flt-scan-msg").textContent = data.error;
  }

  $("flt-add").addEventListener("click", async () => {
    const at = $("flt-at").value;
    say("Vérification…");
    try {
      const res = await fetch(api + "/recalls", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fleet_id: $("flt-fleet").value, at }) });
      const reply = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(reply), "error"); return; }
      data = reply;
      render();
      say("Retour programmé.", "ok");
    } catch { say("Le bot ne répond pas.", "error"); }
  });
  $("flt-scan").addEventListener("click", async () => {
    $("flt-scan").disabled = true;
    $("flt-scan-msg").textContent = "Relevé en cours…";
    try {
      data = await (await fetch(api + "/scan", { method: "POST" })).json();
      render();
      if (!data.error) $("flt-scan-msg").textContent = "";
    } catch { $("flt-scan-msg").textContent = "Le bot ne répond pas."; }
  });
  $("flt-margin").addEventListener("change", async () => {
    const res = await fetch(api + "/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ margin: Number($("flt-margin").value) }) });
    const reply = await res.json().catch(() => ({}));
    if (res.ok) { data = reply; render(); say("Marge enregistrée.", "ok"); } else say(errorText(reply), "error");
  });

  async function refresh() {
    try {
      data = await (await fetch(api)).json();
      render();
    } catch { /* le bot redémarre : on réessaie */ }
  }
  refresh();
  setInterval(refresh, 5000);
})();
