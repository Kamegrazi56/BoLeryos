// Page Suivi : le journal lisible du bot (connexion, scan des planètes, avertissements).
const { el } = window.ui;
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------------------------ journal
let logs = [];
function renderLog() {
  const details = $("log-details").checked, level = $("log-level").value;
  const rank = { info: 0, warn: 1, error: 2 };
  const shown = logs.filter((e) => (details || e.event) && (!level || (rank[e.level] ?? 0) >= rank[level]));
  const box = $("log");
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
  if (!shown.length) {
    box.replaceChildren(el("div", { className: "empty-state", textContent: details ? "Journal vide." : "Aucun événement important pour l'instant. Cochez « détails techniques » pour tout voir." }));
    return;
  }
  box.replaceChildren(...shown.map((e) => el("div", { className: e.level }, el("span", { className: "ts", textContent: e.ts }), e.msg)));
  if (stick) box.scrollTop = box.scrollHeight;
}
$("log-details").addEventListener("change", renderLog);
$("log-level").addEventListener("change", renderLog);

async function refresh() {
  try {
    logs = (await (await fetch("/api/status?limit=400")).json()).logs;
    renderLog();
  } catch { /* le bot redémarre : on réessaie */ }
}
refresh();
setInterval(refresh, 2000);
