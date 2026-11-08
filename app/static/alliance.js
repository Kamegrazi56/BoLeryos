// Alliance : nombre de jetons donnés à chaque passage à la technologie prioritaire (tous, 1 ou 2).
(() => {
  const $ = (id) => document.getElementById(id);
  const api = "/api/alliance";
  const say = (text, kind) => { $("ally-msg").textContent = text; $("ally-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");

  async function load() {
    try {
      const body = await (await fetch(api)).json();
      $("ally-amount").value = String(body.settings.amount);
    } catch { /* le bot redémarre : on réessaie au prochain affichage */ }
  }

  $("ally-save").addEventListener("click", async () => {
    const value = $("ally-amount").value;
    say("Enregistrement…");
    try {
      const res = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: value === "all" ? "all" : Number(value) }) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(body), "error"); return; }
      $("ally-amount").value = String(body.settings.amount);
      say("Enregistré.", "ok");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  });

  load();
})();
