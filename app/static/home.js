// Page Accueil : état de la connexion, univers, planètes scannées, derniers événements.
const { el, ago, fmt, table } = window.ui;
const $ = (id) => document.getElementById(id);

function renderStatus(status) {
  const sup = status.supervisor;
  let sentence;
  if (!sup.has_credentials) sentence = "Aucun identifiant en mémoire : saisissez-les dans Réglages › Compte et univers.";
  else if (sup.connection) sentence = sup.connection[0].toUpperCase() + sup.connection.slice(1) + ".";
  else sentence = "Connexion automatique en préparation…";
  $("sentence").textContent = sentence;
  $("counters").replaceChildren(
    el("span", {}, "Univers : ", el("strong", { textContent: sup.universe || "non choisi" })),
    el("span", {}, "Identifiants : ", el("strong", { textContent: sup.has_credentials ? "en mémoire" : "absents" })),
  );
}

function renderPlanets(scan) {
  const box = $("planets");
  if (!scan.planets.length) {
    box.replaceChildren(el("p", { className: "empty-state", textContent: scan.error || (scan.scanning ? "Scan en cours…" : "Aucune planète lue pour l'instant : le scan se fait seul après la connexion.") }));
    $("planets-meta").textContent = "";
    return;
  }
  box.replaceChildren(table(["Nom", "Coordonnées", "Fer", "Cristal", "Éther"], scan.planets.map((p) => [p.name, p.coords, fmt(p.fer), fmt(p.cristal), fmt(p.ether)])));
  $("planets-meta").textContent = `Dernier scan : ${ago(scan.scanned_at)} (${scan.source}).`;
}

function renderEvents(events) {
  const box = $("events");
  if (!events.length) { box.replaceChildren(el("li", { className: "empty-state", textContent: "Aucun événement pour l'instant." })); return; }
  box.replaceChildren(...events.slice().reverse().map((e) => el("li", { className: e.level }, el("span", { className: "ts", textContent: e.ts }), e.msg)));
}

/** Réponse JSON d'une route de l'instance choisie ; une instance arrêtée (409) ou inconnue (404) lève une erreur. */
const getJson = (url) => fetch(url).then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); });

async function refresh() {
  try {
    const [status, scan, events] = await Promise.all([getJson("/api/status?limit=1"), getJson("/api/planets"), getJson("/api/events")]);
    $("instance-view").hidden = false;
    renderStatus(status);
    renderPlanets(scan);
    renderEvents(events);
  } catch {
    $("instance-view").hidden = true; // aucune instance démarrée sélectionnée (ou le bot redémarre) : rien à afficher
  }
}
refresh();
setInterval(refresh, 2000);

// ------------------------------------------------------------------------------ compte, serveurs, instances
const json = { "Content-Type": "application/json" };
const toLobby = (path, init) => window.Instance.fetchAs("compte", path, init); // la session de connexion, sans instance
const post = (url, body) => fetch(url, { method: "POST", headers: json, body: body ? JSON.stringify(body) : undefined });
const detail = async (res, fallback) => ((await res.json().catch(() => ({}))).detail || fallback);
const STATE_TEXT = { stopped: "Arrêtée", idle: "Démarrage…", logging_in: "Connexion…", logged_in: "Connectée", error: "Connexion échouée" };

let passwordSaved = false;
async function fillLoginForm() {
  try {
    const form = await (await toLobby("/api/account/form")).json();
    if (form.identifier && !$("login-id").value) $("login-id").value = form.identifier;
    passwordSaved = !!form.has_password;
    $("login-pw").placeholder = passwordSaved ? "•••••••••• (enregistré : laissez vide pour le garder)" : "";
  } catch { /* l'application redémarre */ }
}
$("login-save").addEventListener("click", async () => {
  const identifier = $("login-id").value.trim(), password = $("login-pw").value;
  if (!identifier || (!password && !passwordSaved)) { $("login-msg").textContent = "Renseignez l'identifiant et le mot de passe."; return; }
  const res = await toLobby("/api/account/credentials", { method: "POST", headers: json, body: JSON.stringify({ identifier, password: password || undefined }) });
  $("login-pw").value = ""; // jamais conservé dans la page
  if (res.ok) { passwordSaved = true; $("login-msg").textContent = "Identifiants enregistrés : le bot se connecte et lit vos serveurs (sans entrer dans aucun univers)."; }
  else $("login-msg").textContent = await detail(res, "Refusé.");
  refreshInstances();
});
$("login-forget").addEventListener("click", async () => {
  await toLobby("/api/account/credentials", { method: "DELETE" });
  $("login-id").value = ""; $("login-pw").value = "";
  passwordSaved = false;
  $("login-pw").placeholder = "";
  $("login-msg").textContent = "Identifiants oubliés : aucune instance ne se reconnecte seule.";
  refreshInstances();
});

function renderServers(summary) {
  const box = $("server-list");
  if (!summary.universes.length) {
    const reading = summary.lobby.has_credentials && !summary.instances.some((i) => i.running);
    box.replaceChildren(el("p", { className: "empty-state", textContent: reading ? "Connexion et lecture des serveurs en cours…" : "Aucun serveur lu pour l'instant : saisissez vos identifiants." }));
    return;
  }
  const full = summary.instances.length >= summary.max;
  box.replaceChildren(table(["Serveur", ""], summary.universes.map((u) => [u.name, ""])));
  box.querySelectorAll("tr").forEach((row, i) => {
    if (!i) return; // en-tête
    const u = summary.universes[i - 1];
    const button = el("button", { type: "button", textContent: u.used ? "Instance existante" : "Créer une instance", disabled: u.used || full });
    button.addEventListener("click", async () => {
      button.disabled = true;
      const res = await post("/api/instances", { universe: u.name });
      $("scan-msg").textContent = res.ok ? `Instance « ${u.name} » créée : démarrez-la ci-dessous.` : await detail(res, "Création refusée.");
      refreshInstances();
    });
    row.lastChild.replaceChildren(button);
  });
}

$("scan-servers").addEventListener("click", async () => {
  const button = $("scan-servers");
  button.disabled = true;
  try {
    for (let attempt = 0; attempt < 10; attempt++) { // la connexion du compte peut prendre quelques secondes
      $("scan-msg").textContent = attempt ? "Connexion en cours…" : "Lecture des serveurs…";
      const res = await post("/api/instances/scan");
      const result = await res.json().catch(() => ({ error: "Réponse illisible." }));
      if (!result.error) { $("scan-msg").textContent = `${result.universes.length} serveur(s) trouvé(s).`; break; }
      $("scan-msg").textContent = result.error;
      if (!result.error.startsWith("Connexion en cours")) break;
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  } finally {
    button.disabled = false;
    refreshInstances();
  }
});

function instanceRow(i, summary) {
  const act = (label, handler, props = {}) => {
    const b = el("button", { type: "button", textContent: label, ...props });
    b.addEventListener("click", async () => { b.disabled = true; await handler(); refreshInstances(); });
    return b;
  };
  const chosen = i.id === window.Instance.current();
  const state = STATE_TEXT[i.state] || i.state;
  const actions = el("td", { className: "inst-actions" },
    act(chosen ? "Affichée" : "Afficher", async () => { window.Instance.select(i.id); location.reload(); }, { disabled: chosen }),
    i.running
      ? act("Arrêter", async () => { await post(`/api/instances/${i.id}/stop`); })
      : act("Démarrer", async () => { await post(`/api/instances/${i.id}/start`); }),
    act("Supprimer", async () => {
      if (!window.confirm(`Supprimer l'instance « ${i.name} » ? Ses réglages sont gardés dans la corbeille du dossier de données.`)) return;
      await fetch(`/api/instances/${i.id}`, { method: "DELETE" });
    }),
  );
  return el("tr", {}, el("td", { textContent: i.name }), el("td", { textContent: i.universe || "non choisi" }),
    el("td", { textContent: i.connection ? `${state} — ${i.connection}` : state }), actions);
}

function renderInstances(summary) {
  const rows = summary.instances.map((i) => instanceRow(i, summary));
  const t = el("table", {}, el("tr", {}, ...["Nom", "Serveur", "État", ""].map((h) => el("th", { textContent: h }))));
  t.append(...rows);
  $("instance-table").replaceChildren(summary.instances.length ? t : el("p", { className: "empty-state", textContent: "Aucune instance : choisissez un serveur ci-dessus pour en créer une." }));
  const running = summary.instances.filter((i) => i.running).length;
  const memory = summary.memory_mb ? ` Mémoire du navigateur : ${Math.round(summary.memory_mb)} Mo.` : "";
  $("instance-hint").textContent = `${summary.instances.length} / ${summary.max} instances, ${running} démarrée(s).${memory} Chaque instance démarrée ajoute environ 100 Mo au navigateur.`;
  renderServers(summary);
}

let instanceBusy = false;
async function refreshInstances() {
  if (instanceBusy) return;
  instanceBusy = true;
  try { renderInstances(await (await fetch("/api/instances")).json()); } catch { /* l'application redémarre */ }
  instanceBusy = false;
}
fillLoginForm();
refreshInstances();
setInterval(refreshInstances, 3000);
