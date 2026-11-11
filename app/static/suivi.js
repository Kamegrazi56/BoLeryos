// Page Suivi : le journal lisible du bot (connexion, scan des planètes, avertissements).
// Le plus récent en haut : ce que le bot vient de faire se lit sans descendre.
const { el } = window.ui;
const $ = (id) => document.getElementById(id);

// ------------------------------------------------------------------------------------ journal
let logs = [];
function renderLog() {
  const details = $("log-details").checked, level = $("log-level").value;
  const rank = { info: 0, warn: 1, error: 2 };
  const shown = logs.filter((e) => (details || e.event) && (!level || (rank[e.level] ?? 0) >= rank[level])).reverse();
  const box = $("log");
  // En haut : on y reste pour voir arriver les nouvelles lignes. Plus bas (lecture de lignes anciennes) : les nouvelles
  // lignes s'ajoutent au-dessus sans faire bouger ce qu'on lit.
  const atTop = box.scrollTop <= 8;
  const before = box.scrollHeight;
  if (!shown.length) {
    box.replaceChildren(el("div", { className: "empty-state", textContent: details ? "Journal vide." : "Aucun événement important pour l'instant. Cochez « détails techniques » pour tout voir." }));
    return;
  }
  box.replaceChildren(...shown.map((e) => el("div", { className: e.level }, el("span", { className: "ts", textContent: e.ts }), e.msg)));
  if (atTop) box.scrollTop = 0;
  else box.scrollTop += box.scrollHeight - before;
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
