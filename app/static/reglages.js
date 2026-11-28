// Page Réglages : délai de connexion, moteur de lecture, scan des planètes.
const { el, fmt, table, help } = window.ui;
const $ = (id) => document.getElementById(id);
document.querySelectorAll("[data-help-text]").forEach((node) => node.replaceWith(help(node.dataset.helpText)));

const detailText = (data, fallback) => (Array.isArray(data.detail) ? data.detail.join(" · ") : data.detail || fallback);
function say(text, kind) {
  const msg = $("save-msg");
  msg.textContent = text;
  msg.className = "save-state " + (kind || "");
}

// ------------------------------------------------------------------------------ préférences
let saveTimer = null;
async function savePreferences() {
  const body = {
    engine: $("engine").value,
    login_delay: { min_seconds: Number($("login-delay-min").value), max_seconds: Number($("login-delay-max").value) },
  };
  try {
    const res = await fetch("/api/preferences", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (res.ok) say("Enregistré.", "ok");
    else say(detailText(await res.json().catch(() => ({})), "Refusé."), "error");
    return res.ok;
  } catch { say("Enregistrement impossible : le bot ne répond pas.", "error"); return false; }
}
function scheduleSave() {
  clearTimeout(saveTimer);
  say("Enregistrement…");
  saveTimer = setTimeout(savePreferences, 600);
}
async function loadPreferences() {
  try {
    const { preferences: p } = await (await fetch("/api/preferences")).json();
    if (document.activeElement !== $("engine") && [...$("engine").options].some((o) => o.value === p.engine)) $("engine").value = p.engine;
    if (document.activeElement !== $("login-delay-min")) $("login-delay-min").value = p.login_delay.min_seconds;
    if (document.activeElement !== $("login-delay-max")) $("login-delay-max").value = p.login_delay.max_seconds;
  } catch { /* le bot redémarre : on réessaie */ }
}
["engine", "login-delay-min", "login-delay-max"].forEach((id) => {
  $(id).addEventListener($(id).type === "number" ? "input" : "change", () => {
    if ($(id).value === "" && $(id).type === "number") return;
    scheduleSave();
  });
});

// ------------------------------------------------------------------------------------ compte
async function refreshAccount() {
  try {
    const a = await (await fetch("/api/account")).json();
    $("acct-status").replaceChildren(
      el("strong", { textContent: a.has_credentials ? `Identifiants en mémoire (${a.hint})` : "Aucun identifiant en mémoire" }),
      a.has_credentials ? " — le bot peut se reconnecter tout seul." : " — connectez-vous depuis la page Accueil.",
    );
    $("acct-warn").hidden = a.ui_protected || !a.saved_on_disk;
  } catch { /* idem */ }
}
/** État de la connexion automatique, mis à jour en direct (événement émis par shell.js). */
const STATE_TEXT = { idle: "Non connecté", logging_in: "Connexion en cours…", logged_in: "Connecté", error: "Connexion échouée" };
window.addEventListener("boleryos:status", (event) => {
  const { state, supervisor: s } = event.detail;
  const parts = [STATE_TEXT[state] || state];
  if (s.connection) parts.push(s.connection);
  $("conn-status").replaceChildren("Connexion : ", el("strong", { textContent: parts.join(" — ") }));
});
// ----------------------------------------------------------------------------- scan des planètes
const SCAN_SOURCE = { "démarrage": "au démarrage", manuel: "à la demande" };
function renderScan(scan) {
  $("scan-run").disabled = !!scan.scanning;
  $("scan-run").textContent = scan.scanning ? "Scan en cours…" : "Scanner les planètes";
  const when = scan.scanned_at ? new Date(scan.scanned_at * 1000).toLocaleTimeString("fr-FR") : "";
  const parts = [];
  if (scan.scanned_at) parts.push(el("strong", { textContent: `${scan.planets.length} planète(s)` }), ` — dernier scan à ${when} (${SCAN_SOURCE[scan.source] || scan.source})`);
  else parts.push("Aucun scan pour l'instant : il se fait tout seul dès que le bot est dans la partie.");
  if (scan.error) parts.push(" — ", el("strong", { className: "warn", textContent: scan.error }));
  $("scan-status").replaceChildren(...parts);
  const rows = scan.planets.map((p) => [p.name, p.coords, fmt(p.fer), fmt(p.cristal), fmt(p.ether)]);
  $("scan-list").replaceChildren(...(rows.length ? [table(["Planète", "Coordonnées", "Fer", "Cristal", "Éther"], rows)] : []));
}
async function refreshScan() {
  try { renderScan(await (await fetch("/api/planets")).json()); } catch { /* le bot redémarre */ }
}
$("scan-run").addEventListener("click", async () => {
  $("scan-run").disabled = true;
  $("scan-msg").textContent = "";
  try {
    const res = await fetch("/api/planets/scan", { method: "POST" });
    if (res.ok) renderScan(await res.json());
    else $("scan-msg").textContent = detailText(await res.json().catch(() => ({})), "Scan refusé.");
  } catch { $("scan-msg").textContent = "Le bot ne répond pas."; }
  $("scan-run").disabled = false;
});

loadPreferences();
refreshAccount();
refreshScan();
setInterval(refreshAccount, 8000);
setInterval(refreshScan, 5000);

// ------------------------------------------------------------------------------------ onglets
// Ordre du menu latéral (familles Bot, Économie, Opérations, Alliance).
const TABS = ["account", "features", "planning", "discord", "constructions", "researches", "populations", "ships", "components", "slots", "explorations", "excavations", "hackings", "minings", "pillars", "fleets", "spying", "alliance"];
function setTab(id) {
  if (!TABS.includes(id)) id = "account";
  TABS.forEach((t) => {
    const active = t === id;
    $("tab-" + t).setAttribute("aria-selected", String(active));
    $("tab-" + t).tabIndex = active ? 0 : -1;
    $("panel-" + t).hidden = !active;
  });
  if (location.hash !== "#" + id) history.replaceState(null, "", "#" + id);
}
TABS.forEach((t) => {
  $("tab-" + t).addEventListener("click", () => setTab(t));
  $("tab-" + t).addEventListener("keydown", (e) => {
    const i = TABS.indexOf(t);
    const forward = e.key === "ArrowDown" || e.key === "ArrowRight", backward = e.key === "ArrowUp" || e.key === "ArrowLeft";
    const next = forward ? TABS[(i + 1) % TABS.length] : backward ? TABS[(i + TABS.length - 1) % TABS.length] : null;
    if (next) { e.preventDefault(); setTab(next); $("tab-" + next).focus(); }
  });
});
window.addEventListener("hashchange", () => { if (TABS.includes(location.hash.slice(1))) setTab(location.hash.slice(1)); });
setTab(TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "account");
