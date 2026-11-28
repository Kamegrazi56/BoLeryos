// Pilotage par Discord : jeton, salon du panneau de boutons, qui peut commander, confirmation avant d'arrêter, état de la connexion.
(() => {
  const $ = (id) => document.getElementById(id);
  const api = "/api/discord";
  const STATE_TEXT = { off: "Pilotage coupé", connecting: "Connexion à Discord…", connected: "Connecté à Discord", error: "Connexion impossible" };
  const say = (text, kind) => { $("dc-msg").textContent = text; $("dc-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");

  function render({ settings: s, status }) {
    const parts = [STATE_TEXT[status.state] || status.state];
    if (status.error) parts.push(status.error);
    if (!s.persistent) parts.push("dossier de données absent : rien ne sera conservé");
    $("dc-status").textContent = parts.join(" — ");
    $("dc-token").placeholder = s.has_token ? `Jeton enregistré (…${s.token_hint}) : laissez vide pour le garder` : "Collez le jeton ici";
    $("dc-forget").disabled = !s.has_token;
    if (document.activeElement !== $("dc-panel")) $("dc-panel").value = s.panel_channel;
    if (document.activeElement !== $("dc-confirm")) $("dc-confirm").checked = s.confirm;
    if (document.activeElement !== $("dc-enabled")) $("dc-enabled").checked = s.enabled;
  }

  async function load() {
    try { render(await (await fetch(api)).json()); } catch { /* le bot redémarre : on réessaie */ }
  }

  async function send(url, method, body) {
    say("Enregistrement…");
    try {
      const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(data), "error"); return; }
      $("dc-token").value = "";
      render(data);
      say("Enregistré.", "ok");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  }

  $("dc-save").addEventListener("click", () => send(api, "PUT", {
    token: $("dc-token").value, panel_channel: $("dc-panel").value.trim(),
    confirm: $("dc-confirm").checked, enabled: $("dc-enabled").checked,
  }));
  $("dc-forget").addEventListener("click", () => send(api + "/token", "DELETE"));

  load();
  setInterval(load, 5000);
})();
