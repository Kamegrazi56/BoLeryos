// Places de mission : places à garder libres et priorité entre Fouilles, Explorations, Forages et Piratage.
// Enregistré tout seul à chaque changement ; le résumé montre ce que chaque fonctionnalité peut prendre au dernier relevé.
(() => {
  const { el } = window.ui;
  const $ = (id) => document.getElementById(id);
  const api = "/api/slots";
  let data = null;
  let timer = null;

  const say = (text, kind) => { $("slots-msg").textContent = text; $("slots-msg").className = "msg " + (kind || ""); };
  const errorText = (body) => (Array.isArray(body.detail) ? body.detail.join(" · ") : body.detail || "Refusé.");
  const clock = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

  async function save(settings) {
    say("Enregistrement…");
    try {
      const res = await fetch(api, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) { say(errorText(body), "error"); return; }
      data = body;
      render();
      say("Enregistré.", "ok");
    } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); }
  }

  function renderOrder() {
    const order = data.settings.order;
    $("slots-order").replaceChildren(...order.map((id, i) => {
      const button = (text, title, j) => {
        const b = el("button", { type: "button", className: "secondary small icon", textContent: text, title, ariaLabel: `${title} : ${data.labels[id]}`, disabled: j < 0 || j >= order.length });
        b.addEventListener("click", () => {
          const next = [...order];
          [next[i], next[j]] = [next[j], next[i]];
          save({ ...data.settings, order: next });
        });
        return b;
      };
      const need = data.needs[id];
      const state = !data.enabled[id] ? "désactivée : ne réserve rien" : need ? `il lui manque ${need} place(s)` : "rien à réserver pour l'instant";
      const item = el("li", {}, el("span", { className: "rank", textContent: String(i + 1) }),
        el("strong", { className: "name", textContent: data.labels[id] }),
        el("span", { className: "hint", textContent: state }),
        button("↑", "Plus prioritaire", i - 1), button("↓", "Moins prioritaire", i + 1));
      item.dataset.feature = id;
      return item;
    }));
  }

  function renderSummary() {
    const seen = data.seen, reserve = data.settings.reserve;
    $("slots-seen").textContent = seen.max ? `Missions actives au dernier relevé (${clock(seen.at)}) : ${seen.active} / ${seen.max}.`
      : "Compteur pas encore lu : il l'est avant chaque lancement d'Exploration, de Fouille, de Forage ou de Piratage.";
    if (!seen.max) { $("slots-summary").textContent = `Le bot laissera toujours ${reserve} place(s) libre(s).`; return; }
    let left = seen.max - seen.active - reserve;
    const parts = data.settings.order.map((id) => {
      const can = Math.max(0, left);
      left -= data.needs[id];
      return `${data.labels[id]} : ${can} place(s)`;
    });
    $("slots-summary").textContent = `Le bot peut occuper au plus ${Math.max(0, seen.max - reserve)} place(s) (${seen.max} − ${reserve} gardée(s) libre(s)). `
      + `Maintenant, chaque fonctionnalité pourrait prendre — ${parts.join(" ; ")}.`;
  }

  function render() {
    if (document.activeElement !== $("slots-reserve")) $("slots-reserve").value = data.settings.reserve;
    renderOrder();
    renderSummary();
  }

  $("slots-reserve").addEventListener("input", () => {
    if ($("slots-reserve").value === "") return;
    clearTimeout(timer);
    timer = setTimeout(() => save({ ...data.settings, reserve: Number($("slots-reserve").value) }), 600);
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
