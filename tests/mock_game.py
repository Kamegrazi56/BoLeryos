"""Faux jeu qui reproduit la structure observée sur Aleryos (export du 2026-11-01).

Points repris du vrai site : bandeau du haut avec libellés masqués « Planètes/Capteurs/Chat/Favoris » puis le nom
de la planète active, ses coordonnées et les ressources « valeur » puis « LIBELLÉ » ; une carte par bâtiment
(pastille de niveau, titre h3 dans un bloc positionné, lignes ressource/montant, durée, bouton) ; page des
recherches avec « Rechercher » / « Ressources insuffisantes » ; fenêtre « Planètes » listant nom, coordonnées et
stocks ; navigation « Installations » / « Laboratoire » mais aucun bouton vers la page Industrie.
Le serveur garde l'état (planète active, stocks, niveaux) pour que les rechargements de page soient réalistes.
"""

import copy

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from tests.mock_site import SERVERS_PAGE


def _b(name, level, costs, duration, section="buildings"):
    return {"name": name, "level": level, "costs": costs, "duration": duration, "section": section}


def _buildings():
    return [
        _b("Mine de Fer", 40, {"Fer": 201.765e6, "Cristal": 50.441e6}, "1j 2h"),
        _b("Extracteur de Cristal", 38, {"Fer": 71.739e6, "Cristal": 35.869e6}, "12h 22m"),
        _b("Synthétiseur d'Éther", 34, {"Fer": 66.425e6, "Cristal": 22.142e6}, "2h 14m"),
        _b("Centrale Énergétique", 27, {"Fer": 53.73e6, "Cristal": 21.492e6}, "5m 25s"),
        _b("Hangar de Fer", 16, {"Fer": 501.763e6}, "12.75s"),
        _b("Chantier Spatial", 10, {"Fer": 160.564e6, "Cristal": 80.282e6, "Éther": 20.071e6}, "12.15s", "facilities"),
        _b("Centre de Recherche", 19, {"Fer": 156.8e3, "Cristal": 313.6e3, "Éther": 62.7e3}, "5.00s", "facilities"),
        _b("Caserne", 18, {"Fer": 200.705e6, "Cristal": 200.705e6}, "1m 33s", "facilities"),
    ]


def _prod_items():
    """Éléments de production d'une planète : population (par onglet), appareils, défenses, composants, vaisseaux."""
    def it(section, name, costs, owned=0, tab=""):
        return {"section": section, "tab": tab, "name": name, "costs": costs, "owned": owned, "queue": 0}

    rempart = {"Fer": 2616, "Cristal": 5233, "Éther": 218}
    items = [
        it("population", "Rempart de Fer", rempart, 3182, "Assaut"),
        it("population", "Spectre du Rift", {"Fer": 4361, "Cristal": 2616, "Éther": 218}, 999, "Assaut"),
        it("population", "Pilote", {"Fer": 1000, "Cristal": 1000}, 10, "Logistique"),
        it("population", "Technicien", {"Fer": 500, "Cristal": 500, "Éther": 50}, 21331, "Logistique"),
        it("population", "Mineur", {"Fer": 300, "Cristal": 200}, 5, "Administration"),
        it("specialized", "Sonde Spatiale", {"Cristal": 872, "Éther": 43}, 3987),
        it("specialized", "Drone Terrestre", {"Fer": 436, "Cristal": 436}, 10382),
        it("specialized", "Grand Transporteur", {"Fer": 5233, "Cristal": 5233, "Éther": 872}, 9000),
        it("defense", "Sentinelle de Rempart", {"Fer": 2093, "Cristal": 251}, 19962),
        it("defense", "Faisceau de Verrouillage", {"Fer": 4186, "Cristal": 3349}, 436),
        it("defense", "Éclateur Ionique", {"Fer": 10047, "Cristal": 8373, "Éther": 418}, 8694),
        it("component", "Chasseur", {"Fer": 436, "Cristal": 87, "Éther": 43}, 0),
        it("component", "Destroyer", {"Fer": 479710, "Cristal": 104664, "Éther": 71520}, 91),
        it("ship", "Anode", {"Fer": 410152, "Cristal": 100311, "Éther": 95959}, 47),
        it("ship", "Plasmatique", {"Fer": 1.5e9, "Cristal": 1e6, "Éther": 1e6}, 0),
    ]
    return items


def _movement(kind, origin, destination, arrival="06:56:16", countdown="01:20:00"):
    return {"type": kind, "arrival": arrival, "countdown": countdown,
            "origin": dict(zip(("planet", "coords", "player"), origin)), "destination": dict(zip(("planet", "coords", "player"), destination))}


def initial_state():
    stocks = [
        {"Fer": 58.0e6, "Cristal": 22.36e6, "Éther": 19.8e6},
        {"Fer": 4.121e9, "Cristal": 3.201e9, "Éther": 942.839e6},
        {"Fer": 7.3e6, "Cristal": 27.1e6, "Éther": 21.9e6},
    ]
    planets = [
        ("8-50-Concorde", "Z25 95 16", 11014), ("6-79-Egide", "M69 38 06", 8485), ("9-169-Strate", "R59 75 43", 4000),
    ]
    return {
        "active": 0,
        "close_on_select": True,
        "no_debit": False,
        "popup_after_click": False,
        "require_auth": False,  # vrai : connexion → choix de l'univers → partie, avec session qui peut expirer
        "logged_in": False,
        "login_attempts": 0,
        "actions": [],
        "research_busy": False,
        "exp": {"daily": 3, "daily_max": 46, "active": 2, "active_max": 10, "avail": {
            "piliers": [["Soldat", 2507458], ["Rempart de Fer", 3182], ["Grand Transporteur", 9000]],
            "flotte": [["Testo Civil Transpo Destroy Def", 43], ["Sonde Spatiale", 3987]]}},
        # Capteurs interstellaires : un déploiement du joueur, comme dans l'export (aucune attaque au départ)
        "movements": [_movement("Déploiement", ("6-79-Egide", "M69 38 06", "KameGrazi"), ("8-50-Concorde", "Z25 95 16", "KameGrazi"))],
        # Liste de la Galaxie autour de la planète active (structure de l'export du 2026-11-03)
        "galaxy": [
            {"player": "KameGrazi", "tag": "REPCO", "planet": "6-79-Egide", "coords": "M69 38 06", "actions": []},
            {"player": "Kairo-Vigie-378", "tag": "AI", "planet": "Kairo-Vigie-378 7", "coords": "M69 34 68", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"player": "Bryn-Socle-176", "tag": "AI", "planet": "Bryn-Socle-176 3", "coords": "M69 37 24", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"player": "Splif", "tag": "REPCO", "planet": "F5", "coords": "M69 06 25", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"player": "Hades33", "tag": "", "planet": "Gaia - M", "coords": "M69 99 83", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"player": "Torek-Veine-165", "tag": "AI", "planet": "Torek-Veine-165 2", "coords": "M97 50 19", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"mining": True, "planet": "Planete miniere", "coords": "M69 40 68", "actions": ["Miner", "Sonde"]},
        ],
        "ship_mode": "assemblages",  # mode de production affiché sur les cartes de vaisseaux (« Construction » à choisir)
        "planets": [
            {"name": n, "coords": c, "energy": e, "stocks": copy.deepcopy(s), "busy": False, "buildings": _buildings(), "prod": _prod_items()}
            for (n, c, e), s in zip(planets, stocks)
        ],
        "tech": [
            {"name": "Technologie laser", "level": 22, "costs": {"Fer": 2.686e9, "Cristal": 1.343e9}},
            {"name": "Technologie ionique", "level": 19, "costs": {"Fer": 3.358e9, "Cristal": 1.007e9, "Éther": 335.756e6}},
            {"name": "Technologie d’espionnage", "level": 24, "costs": {"Fer": 10.744e9, "Cristal": 53.721e9, "Éther": 10.744e9}},
        ],
    }


LOGIN_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Aleryos</title></head><body>
<div id="cookies"><button type="button" id="refuse">TOUT REFUSER</button></div>
<main><form id="f"><input type="email" name="email" placeholder="Adresse e-mail"><input type="password" name="password" placeholder="Mot de passe">
<button type="submit">Se connecter</button></form><p id="err" role="alert" hidden></p></main>
<script>
document.getElementById('refuse').onclick = () => document.getElementById('cookies').remove();
document.getElementById('f').onsubmit = async (e) => {
  e.preventDefault();
  const r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password: e.target.password.value})});
  if (r.ok) { location.href = '/servers'; return; }
  const err = document.getElementById('err'); err.hidden = false; err.textContent = 'Identifiants invalides.';
};
</script></body></html>"""

# Page de choix de l'univers : le bouton « Reprendre la partie » mène à la partie.
SERVERS = SERVERS_PAGE.replace("/servers/asterion", "/game/asterion/buildings")

PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Mock jeu</title>
<style>.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
button{min-width:36px;min-height:28px;margin:2px}.card{border:1px solid #888;margin:6px;padding:6px;width:300px}
.top{position:relative;height:40px}.abs{position:absolute;bottom:2px;left:4px}.badge{float:right}</style></head>
<body><div id="cookies"><button type="button" id="refuse">TOUT REFUSER</button></div><div id="root"></div>
<script>
let state = null, overlayOpen = false;
const sec = () => location.pathname.split('/').pop();
const big = (v) => v >= 1e9 ? (v/1e9).toFixed(3).replace('.', ',') + ' G' : v >= 1e6 ? (v/1e6).toFixed(3).replace('.', ',') + ' M' : String(Math.round(v));
const md = (v) => v >= 1e9 ? (v/1e9).toFixed(1).replace('.', ',') + ' Md' : (v/1e6).toFixed(1).replace('.', ',') + ' M';
const cost = (v) => v >= 1e9 ? (v/1e9).toFixed(3) + 'G' : v >= 1e6 ? (v/1e6).toFixed(3) + 'M' : v >= 1e3 ? (v/1e3).toFixed(1) + 'k' : String(v);
const int = (v) => String(v).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
const afford = (p, it, busy) => !busy && Object.entries(it.costs).every(([k, v]) => p.stocks[k] >= v);
function card(p, it, kind, busy) {
  const ok = afford(p, it, busy);
  const label = kind === 'tech' ? (ok ? 'Rechercher' : 'Ressources insuffisantes') : 'Améliorer';
  const costs = Object.entries(it.costs).map(([k, v]) => `<div><span>${k}</span><span>${cost(v)}</span></div>`).join('');
  const dur = kind === 'tech' ? '<div><span>Temps de recherche</span></div>' : `<div><span>Durée</span><span>${it.duration}</span></div>`;
  return `<div class="card"><div class="top"><div class="badge"><span>${it.level}</span></div><div class="abs"><h3>${it.name}</h3></div></div>`
    + `<div class="body">${costs}${dur}<button class="act" data-name="${it.name}" data-kind="${kind}" ${ok ? '' : 'disabled'}>${label}</button></div></div>`;
}
const kfmt = (v) => v >= 1e9 ? (v / 1e9).toFixed(3).replace('.', ',') + ' G' : v >= 1e6 ? (v / 1e6).toFixed(3).replace('.', ',') + ' M' : v >= 1000 ? (v / 1000).toFixed(3).replace('.', ',') + ' K' : String(Math.round(v));
const ui = {tab: 'Assaut', ship: 'Assemblages', way: 'Piliers', mission: 'ATTAQUE'};
const CK = {Fer: 'F', Cristal: 'C', 'Éther': 'E'};
const costLeaves = (c) => Object.entries(c).map(([k, v]) => `<span>${CK[k]}: ${kfmt(v)}</span>`).join('');
function prodCard(it, verb) {
  const short = it.name === 'Faisceau de Verrouillage' ? 'Faisceau de Verrou' : it.name;
  return `<div class="card"><div class="badge"><span>${int(it.owned)}</span></div><h3>${it.name}</h3><div><span>ATQ</span><span>14</span></div>`
    + `<div>${costLeaves(it.costs)}</div><div class="ctl"><input type="number" placeholder="Qté" aria-label="Quantité pour ${short}">`
    + `<button type="button" data-max="1" aria-label="Quantité maximale pour ${short}">MAX</button>`
    + `<button type="button" class="prod" data-section="${it.section}" data-name="${it.name}" aria-label="${verb} ${short}">${verb}</button></div></div>`;
}
function prodMain(p, s) {
  const items = (section) => p.prod.filter((i) => i.section === section);
  if (s === 'staff') {
    const queue = p.prod.filter((i) => i.queue > 0).map((i) => `<div><span>${i.name}</span><span>x${i.queue}</span></div>`).join('');
    const tabs = ['Assaut', 'Logistique', 'Administration', 'Hôpital'].map((t) => `<button role="tab" class="tab" data-tab="${t}">${t}</button>`).join('');
    const cards = items('population').filter((i) => i.tab === ui.tab).map((i) => prodCard(i, 'Recruter')).join('');
    return '<h2>Population</h2><div>' + tabs + '</div><div data-testid="queue-operation-units"><span>Production d’unités</span>' + queue + '</div>'
      + '<div data-testid="population-grid"><div class="card"><div class="badge"><span>2 507 383</span></div><h3>Soldat</h3></div>' + cards + '</div>';
  }
  if (s === 'specialized') {
    return '<h2>Appareils spécialisés</h2><div data-testid="special-unit-grid"><div class="card"><h3>Résonateur de Trame (RT)</h3><span>Flux</span><span>3 987</span></div>'
      + items('specialized').map((i) => prodCard(i, 'Lancer')).join('') + '</div>';
  }
  if (s === 'defense') {
    return '<h2>DÉFENSE</h2><div data-testid="defense-grid"><div class="card"><h3>Iris des Piliers</h3><button type="button">Activer +1</button></div>'
      + items('defense').map((i) => prodCard(i, 'Lancer')).join('') + '</div>';
  }
  const tabs = ['Assemblages', 'Réparation', 'Composants', 'Modèle', 'Recyclage'].map((t) => `<button type="button" class="stab" data-stab="${t}">${t}</button>`).join('');
  if (ui.ship === 'Composants') {
    const rows = items('component').map((i) => `<div data-testid="component-production-row"><div data-testid="component-row-layout"><span>${i.name}</span>`
      + `<span>×${i.owned}</span><div data-testid="component-row-cost">${costLeaves(i.costs)}</div>`
      + `<div data-testid="component-row-controls"><input type="number" placeholder="Qté"><button type="button" data-max="1">MAX</button>`
      + `<button type="button" class="prod" data-section="component" data-name="${i.name}"></button></div></div></div>`).join('');
    return '<h2>Chantier spatial</h2><div>' + tabs + '</div><div data-testid="component-stock-summary"><span>Gestion des stocks</span></div><div>' + rows + '</div>';
  }
  const cost = (c) => Object.entries(c).map(([k, v]) => `<span aria-label="${k.toUpperCase()} : ${kfmt(v)}">${kfmt(v)}</span>`).join('');
  const ships = items('ship').map((i) => `<article data-testid="ship-assembly-card"><button type="button"><span>${i.name}</span><span>En service : <b>${i.owned}</b></span><span>MILITAIRE</span></button>`
    + `<section data-testid="ship-production-control"><button type="button" class="mode" data-mode="construction">Construction</button><button type="button" class="mode" data-mode="assemblages">Assemblages</button>`
    + `<div>${cost(i.costs)}</div><div data-testid="ship-production-quantity-row"><button type="button" aria-label="Qté -">-</button><input type="number" placeholder="1" aria-label="Qté">`
    + `<button type="button" aria-label="Qté +">+</button><button type="button" data-max="1">MAX</button><button type="button" class="prod" data-section="ship" data-name="${i.name}">Lancer</button></div></section></article>`).join('');
  const catalogue = '<article data-testid="ship-assembly-card"><button type="button"><span>Intercepteur</span><span>Inactif</span></button><button type="button">Ajouter à mes modèles</button></article>';
  return '<h2>Chantier spatial</h2><div>' + tabs + '</div><div data-testid="ship-assembly-grid">' + ships + '</div><div data-testid="shipyard-catalog-grid">' + catalogue + '</div>';
}
function galaxyMain() {
  const rows = state.galaxy.map((r) => {
    const actions = r.actions.map((a) => `<button type="button" aria-label="${a}"></button>`).join('');
    if (r.mining) return `<div class="continuous-inspector__list-row"><button type="button">${r.planet} ${r.coords}</button>${actions}</div>`;
    const who = r.tag ? `${r.player} [${r.tag}]` : r.player;
    return `<div class="continuous-inspector__list-row"><button type="button"></button><button type="button">${who}</button><button type="button">${r.planet} · ${r.coords}</button>${actions}</div>`;
  }).join('');
  return '<h2>Galaxie</h2><input aria-label="Recherche de destination"><section class="continuous-inspector__list">' + rows + '</section>';
}
function opsMain() {
  const ex = state.exp;
  const ways = ['Piliers', 'Flotte'].map((w) => `<button type="button" class="way" data-way="${w}">${w.toUpperCase()}</button>`).join('');
  const missions = ['ATTAQUE', 'TRANSPORT', 'EXPLORATION', 'FOUILLE'].map((m) => `<button type="button" class="mission" data-m="${m}">${m}</button>`).join('');
  let body = '';
  if (ui.mission === 'ATTAQUE') {
    const label = ui.way === 'Piliers' ? 'Stock' : 'Disponibles';
    body = '<div><span>2. Composition de mission</span>' + ex.avail[ui.way.toLowerCase()].map(([n, c]) => `<div><span>${n}</span><span>${label} : ${int(c)}</span><input type="number" aria-label="Quantité ${n}"></div>`).join('') + '</div><button type="button">LANCEMENT BLOQUÉ</button>';
  }
  if (ui.mission === 'EXPLORATION') {
    const units = ex.avail[ui.way.toLowerCase()].map(([n, c]) => `<div><span>${n}</span><span>Disponibles : ${int(c)}</span><input type="number" aria-label="Quantité ${n}"></div>`).join('');
    body = `<div><span>Explorations quotidiennes</span><span>${ex.daily}/${ex.daily_max}</span></div><div><span>Explorations actives</span><span>${ex.active}/${ex.active_max}</span></div>`
      + `<div><span>Composition</span>${units}</div><button type="button">AUCUNE UNITÉ SÉLECTIONNÉE.</button>`;
  }
  return '<h2>Opérations</h2><div><span>Moyen de lancement</span>' + ways + '</div><div><span>Mission</span>' + missions + '</div>' + body;
}
function sensors() {
  const side = (s) => `<div><span>${s.planet}</span><button type="button">${s.coords}</button><span>${s.player}</span></div>`;
  const rows = state.movements.map((m) => `<div class="mv"><div>${m.type}</div><div>476051 U</div><div>Arrivée: ${m.arrival}</div><div>${m.countdown}</div>`
    + `<div><span>Origine</span>${side(m.origin)}</div><div><span>Destination</span>${side(m.destination)}</div></div>`).join('');
  return `<div class="sensors" hidden><span>CAPTEURS INTERSTELLAIRES</span><span>Missions actives : ${state.movements.length} / 29</span>${rows}</div>`;
}
function render() {
  if (!state) return;
  const p = state.planets[state.active], s = sec();
  const title = {buildings: 'Industrie', facilities: 'Installations', tech: 'Laboratoire'}[s] || 'Vue';
  const isProd = ['staff', 'shipyard', 'specialized', 'defense', 'operations', 'galaxy'].includes(s);
  let cards = '';
  if (isProd) cards = '';
  else if (s === 'tech') cards = state.tech.map((t) => card(p, t, 'tech', state.research_busy)).join('');
  else cards = p.buildings.filter((b) => b.section === s).map((b) => card(p, b, 'build', p.busy)).join('');
  const planets = state.planets.map((pl, i) => `<button class="pl" data-i="${i}"><span>${pl.name}</span><span>${pl.coords}</span>`
    + `<span>${md(pl.stocks.Fer)}</span><span>${md(pl.stocks.Cristal)}</span><span>${md(pl.stocks['Éther'])}</span></button>`).join('');
  document.getElementById('root').innerHTML =
    '<div class="launcher"><button id="open-planets"><span class="sr-only">Planètes</span></button><button><span class="sr-only">Capteurs</span><span class="game-utility-badge game-utility-badge--fleet">' + state.movements.length + '</span></button>'
    + '<button><span class="sr-only">Chat</span></button><button><span class="sr-only">Favoris</span></button></div>'
    + `<div class="planet"><span>${p.name}</span><span>${p.coords}</span></div>`
    + `<div class="res"><div><span>${big(p.stocks.Fer)}</span><span>FER</span></div><div><span>${big(p.stocks.Cristal)}</span><span>CRISTAL</span></div>`
    + `<div><span>${big(p.stocks['Éther'])}</span><span>ÉTHER</span></div><div><span>${int(p.energy)}</span><span>ÉNERGIE</span></div>`
    + '<div><span>4 808</span><span>ASTRIUM</span></div><div><span>492</span><span>ÉCHOS</span></div></div>'
    + (isProd ? `<main>${s === 'operations' ? opsMain() : s === 'galaxy' ? galaxyMain() : prodMain(p, s)}</main>` : `<main><h2>${title}</h2><div class="grid">${cards}</div></main>`)
    + '<nav><button class="nav" data-s="facilities"><span class="sr-only">Installations</span></button><button class="nav" data-s="tech"><span class="sr-only">Laboratoire</span></button>'
    + '<button class="nav" data-s="staff"><span class="sr-only">Population</span></button><button class="nav" data-s="shipyard"><span class="sr-only">Chantier spatial</span></button>'
    + '<button class="nav" data-s="specialized"><span class="sr-only">Appareils spécialisés</span></button><button class="nav" data-s="defense"><span class="sr-only">Défenses</span></button><button class="nav" data-s="operations"><span class="sr-only">Opérations</span></button><button class="nav" data-s="galaxy"><span class="sr-only">Galaxie</span></button>'
    + '<button><span class="sr-only">Vue d’ensemble</span></button></nav>'
    + `<div id="overlay" ${overlayOpen ? '' : 'hidden'}><button id="close-ov">Fermer</button><button>Arrêter la rotation</button>${planets}</div>`
    + sensors()
    + (state.popup ? '<div role="dialog"><button>Confirmer</button></div>' : '');
  document.getElementById('open-planets').onclick = () => { overlayOpen = true; render(); };
  document.getElementById('close-ov').onclick = () => { overlayOpen = false; render(); };
  document.querySelectorAll('.nav').forEach((b) => b.onclick = () => { history.pushState({}, '', '/game/asterion/' + b.dataset.s); render(); });
  document.querySelectorAll('.pl').forEach((b) => b.onclick = async () => {
    await fetch('/api/select', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({index: +b.dataset.i})});
    if (state.close_on_select) overlayOpen = false;
    await load();
  });
  document.querySelectorAll('.way').forEach((b) => b.onclick = () => { ui.way = b.dataset.way; render(); });
  document.querySelectorAll('.mission').forEach((b) => b.onclick = () => { ui.mission = b.dataset.m; render(); });
  document.querySelectorAll('.tab').forEach((b) => b.onclick = () => { ui.tab = b.dataset.tab; render(); });
  document.querySelectorAll('.stab').forEach((b) => b.onclick = () => { ui.ship = b.dataset.stab; render(); });
  document.querySelectorAll('.mode').forEach((b) => b.onclick = async () => {
    await fetch('/api/ship_mode', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({mode: b.dataset.mode})});
    await load();
  });
  document.querySelectorAll('button[data-max]').forEach((b) => b.onclick = async () => {
    const holder = b.parentElement; const input = holder.querySelector('input'); const prod = holder.querySelector('.prod');
    const r = await (await fetch('/api/affordable', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({section: prod.dataset.section, name: prod.dataset.name})})).json();
    input.value = r.count;
  });
  document.querySelectorAll('.prod').forEach((b) => b.onclick = async () => {
    const input = b.parentElement.querySelector('input'); if (!input || !input.value) return;
    await fetch('/api/produce', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({section: b.dataset.section, name: b.dataset.name, qty: +input.value})});
    await load();
  });
  document.querySelectorAll('.act').forEach((b) => b.onclick = async () => {
    await fetch('/api/upgrade', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({section: sec(), name: b.dataset.name, kind: b.dataset.kind})});
    await load();
  });
}
async function load() { state = await (await fetch('/api/state')).json(); render(); }
window.addEventListener('popstate', render);
document.getElementById('refuse').onclick = () => document.getElementById('cookies').remove();
load();
</script></body></html>"""


def create_mock_game() -> FastAPI:
    app = FastAPI()
    app.state.game = initial_state()

    def _denied() -> bool:
        g = app.state.game
        return g["require_auth"] and not g["logged_in"]

    @app.get("/", response_class=HTMLResponse)
    async def login_page():
        g = app.state.game
        if g["require_auth"] and g["logged_in"]:
            return RedirectResponse("/servers")
        return LOGIN_PAGE

    @app.post("/api/login")
    async def login(request: Request):
        g, body = app.state.game, await request.json()
        g["login_attempts"] += 1
        if body.get("password") != "good-password":
            raise HTTPException(401, "identifiants invalides")
        g["logged_in"] = True
        return {"ok": True}

    @app.get("/servers", response_class=HTMLResponse)
    async def servers():
        return RedirectResponse("/") if _denied() else SERVERS

    @app.get("/game/asterion/{section}", response_class=HTMLResponse)
    async def page(section: str):
        return RedirectResponse("/") if _denied() else PAGE

    @app.get("/api/state")
    async def get_state():
        g = app.state.game
        return {**g, "planets": [{k: v for k, v in p.items()} for p in g["planets"]]}

    @app.post("/api/select")
    async def select(request: Request):
        body = await request.json()
        app.state.game["active"] = int(body["index"])
        return {"ok": True}

    @app.post("/api/upgrade")
    async def upgrade(request: Request):
        g, body = app.state.game, await request.json()
        planet = g["planets"][g["active"]]
        research = body.get("kind") == "tech"
        items = g["tech"] if research else planet["buildings"]
        item = next((i for i in items if i["name"] == body["name"]), None)
        if item is None:
            raise HTTPException(404)
        busy = g["research_busy"] if research else planet["busy"]
        if busy or not all(planet["stocks"][k] >= v for k, v in item["costs"].items()):
            raise HTTPException(409, "indisponible")
        if not g["no_debit"]:
            for k, v in item["costs"].items():
                planet["stocks"][k] -= v
        item["level"] += 1
        if research:
            g["research_busy"] = True
        else:
            planet["busy"] = True
        g["actions"].append({"kind": "research" if research else "building", "planet": planet["name"], "name": item["name"], "level_after": item["level"]})
        if g["popup_after_click"]:
            g["popup"] = True
        return {"ok": True}

    def _prod_item(section: str, name: str):
        planet = app.state.game["planets"][app.state.game["active"]]
        item = next((i for i in planet["prod"] if i["section"] == section and i["name"] == name), None)
        if item is None:
            raise HTTPException(404)
        return planet, item

    def _afford(planet, item) -> int:
        return int(min(planet["stocks"][k] // v for k, v in item["costs"].items() if v > 0))

    @app.post("/api/ship_mode")
    async def ship_mode(request: Request):
        app.state.game["ship_mode"] = (await request.json())["mode"]
        return {"ok": True}

    @app.post("/api/affordable")
    async def affordable(request: Request):
        body = await request.json()
        planet, item = _prod_item(body["section"], body["name"])
        return {"count": _afford(planet, item)}

    @app.post("/api/produce")
    async def produce(request: Request):
        g, body = app.state.game, await request.json()
        planet, item = _prod_item(body["section"], body["name"])
        qty = int(body["qty"])
        if qty < 1 or qty > _afford(planet, item):
            raise HTTPException(409, "ressources insuffisantes")
        if body["section"] == "ship" and g["ship_mode"] != "construction":
            raise HTTPException(409, "mode Assemblages : composants requis")
        if not g["no_debit"]:
            for k, v in item["costs"].items():
                planet["stocks"][k] -= v * qty
        if body["section"] == "population":
            item["queue"] += qty  # la population est entraînée : elle apparaît dans la file d'attente
        else:
            item["owned"] += qty
        g["actions"].append({"kind": "production", "section": body["section"], "planet": planet["name"], "name": item["name"],
                             "qty": qty, "mode": g["ship_mode"] if body["section"] == "ship" else None})
        if g["popup_after_click"]:
            g["popup"] = True
        return {"ok": True}

    @app.post("/api/_galaxy")
    async def set_galaxy(request: Request):
        app.state.game["galaxy"] = (await request.json())["rows"]
        return {"ok": True}

    @app.post("/api/_movements")
    async def set_movements(request: Request):
        app.state.game["movements"] = (await request.json())["movements"]
        return {"ok": True}

    @app.post("/api/_exp")
    async def set_exp(request: Request):
        body = await request.json()
        exp = app.state.game["exp"]
        for key in ("daily", "daily_max", "active", "active_max"):
            if key in body:
                exp[key] = body[key]
        for way, rows in (body.get("avail") or {}).items():
            exp["avail"][way] = rows
        return {"ok": True}

    @app.post("/api/_prod")
    async def set_prod(request: Request):
        body = await request.json()
        planet = app.state.game["planets"][int(body["index"])]
        item = next(i for i in planet["prod"] if i["name"] == body["name"])
        item.update({k: body[k] for k in ("owned", "queue") if k in body})
        return {"ok": True}

    # --- crochets de test -------------------------------------------------------
    @app.post("/api/_reset")
    async def reset(request: Request):
        app.state.game = initial_state()
        patch = await request.json() if (await request.body()) else {}
        g = app.state.game
        for key in ("close_on_select", "no_debit", "popup_after_click", "require_auth"):
            if key in patch:
                g[key] = patch[key]
        for index, energy in (patch.get("energy") or {}).items():
            g["planets"][int(index)]["energy"] = energy
        for index, stocks in (patch.get("stocks") or {}).items():
            g["planets"][int(index)]["stocks"].update(stocks)
        return {"ok": True}

    @app.post("/api/_logout")
    async def logout():
        app.state.game["logged_in"] = False  # session expirée
        return {"ok": True}

    @app.get("/api/_info")
    async def info():
        g = app.state.game
        return {"logged_in": g["logged_in"], "login_attempts": g["login_attempts"]}

    @app.post("/api/_stocks")
    async def set_stocks(request: Request):
        body = await request.json()
        app.state.game["planets"][int(body["index"])]["stocks"].update(body["stocks"])
        return {"ok": True}

    @app.post("/api/_complete")
    async def complete():
        g = app.state.game
        g["research_busy"] = False
        for p in g["planets"]:
            p["busy"] = False
            for item in p["prod"]:  # la population en file d'attente est terminée
                item["owned"] += item["queue"]
                item["queue"] = 0
        return {"ok": True}

    @app.get("/api/_actions")
    async def actions():
        return JSONResponse(app.state.game["actions"])

    return app
