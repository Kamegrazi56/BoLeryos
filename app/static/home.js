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

async function refresh() {
  try {
    const [status, scan, events] = await Promise.all([
      fetch("/api/status?limit=1").then((r) => r.json()),
      fetch("/api/planets").then((r) => r.json()),
      fetch("/api/events").then((r) => r.json()),
    ]);
    renderStatus(status);
    renderPlanets(scan);
    renderEvents(events);
  } catch { /* le bot redémarre : on réessaie */ }
}
refresh();
setInterval(refresh, 2000);
