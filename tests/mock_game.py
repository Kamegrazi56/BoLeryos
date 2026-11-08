"""Faux jeu qui reproduit la structure observée sur Aleryos (export du 2026-11-01).

Points repris du vrai site : bandeau du haut avec libellés masqués « Planètes/Capteurs/Chat/Favoris » puis le nom
de la planète active, ses coordonnées et les ressources « valeur » puis « LIBELLÉ » ; une carte par bâtiment
(pastille de niveau, titre h3 dans un bloc positionné, lignes ressource/montant, durée, bouton) ; page des
recherches avec « Rechercher » / « Ressources insuffisantes » ; fenêtre « Planètes » listant nom, coordonnées et
stocks ; navigation « Installations » / « Laboratoire » mais aucun bouton vers la page Industrie.
Le serveur garde l'état (planète active, stocks, niveaux) pour que les rechargements de page soient réalistes.
"""

import copy
import time

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
        _b("Centrale à Impulsion", 0, {"Fer": 515, "Cristal": 206, "Éther": 102}, "5.00s"),
        _b("Hangar de Fer", 16, {"Fer": 501.763e6}, "12.75s"),
        _b("Hangar de Cristal", 20, {"Fer": 7.721e9, "Cristal": 6.177e9}, "43.01s"),
        _b("Cuve d'Éther", 18, {"Fer": 1.93e9, "Cristal": 1.544e9, "Éther": 965.086e6}, "17.90s"),
        _b("Usine Robot", 13, {"Fer": 1.206e6, "Cristal": 2.413e6, "Éther": 181.0e3}, "5.00s", "facilities"),
        _b("Chantier Spatial", 10, {"Fer": 160.564e6, "Cristal": 80.282e6, "Éther": 20.071e6}, "12.15s", "facilities"),
        _b("Centre de Recherche", 19, {"Fer": 156.8e3, "Cristal": 313.6e3, "Éther": 62.7e3}, "5.00s", "facilities"),
        _b("Caserne", 18, {"Fer": 200.705e6, "Cristal": 200.705e6}, "1m 33s", "facilities"),
        _b("Institut de Formation", 20, {"Fer": 231.621e6, "Cristal": 154.414e6, "Éther": 15.441e6}, "11.08s", "facilities"),
        _b("Matrice de Coalescence", 11, {"Fer": 753.973e6, "Cristal": 753.973e6, "Éther": 30.159e6}, "12h 6m", "facilities"),
        _b("Tour à Résonance", 12, {"Fer": 361.907e6, "Cristal": 241.271e6, "Éther": 90.477e6}, "1m 14s", "facilities"),
        _b("Terraformeur", 10, {"Fer": 37.699e6, "Cristal": 75.397e6, "Éther": 37.699e6}, "5.00s", "facilities"),
    ]


def _prod_items():
    """Éléments de production d'une planète : population (par onglet), appareils, défenses, composants, vaisseaux."""
    def it(section, name, costs, owned=0, tab=""):
        return {"section": section, "tab": tab, "name": name, "costs": costs, "owned": owned, "queue": 0}

    rempart = {"Fer": 2616, "Cristal": 5233, "Éther": 218}
    items = [
        it("population", "Rempart de Fer", rempart, 3182, "Assaut"),
        it("population", "Spectre du Rift", {"Fer": 4361, "Cristal": 2616, "Éther": 218}, 999, "Assaut"),
        it("population", "Surchargeur Plasmique", {"Fer": 8623, "Cristal": 3880, "Éther": 1034}, 0, "Assaut"),
        it("population", "Faucheur Cinétique", {"Fer": 3449, "Cristal": 2585, "Éther": 215}, 0, "Assaut"),
        it("population", "Pilote", {"Fer": 1000, "Cristal": 1000}, 10, "Logistique"),
        it("population", "Technicien", {"Fer": 500, "Cristal": 500, "Éther": 50}, 21331, "Logistique"),
        it("population", "Informaticien", {"Fer": 431, "Cristal": 862}, 0, "Logistique"),
        it("population", "Scientifique", {"Fer": 172, "Cristal": 258}, 0, "Administration"),
        it("population", "Ingénieur", {"Fer": 258, "Cristal": 86, "Éther": 86}, 0, "Administration"),
        it("population", "Mineur", {"Fer": 300, "Cristal": 200}, 5, "Administration"),
        it("specialized", "Sonde Spatiale", {"Cristal": 872, "Éther": 43}, 3987),
        it("specialized", "Drone Terrestre", {"Fer": 436, "Cristal": 436}, 10382),
        it("specialized", "Grand Transporteur", {"Fer": 5233, "Cristal": 5233, "Éther": 872}, 9000),
        it("defense", "Sentinelle de Rempart", {"Fer": 2093, "Cristal": 251}, 19962),
        it("defense", "Faisceau de Verrouillage", {"Fer": 4186, "Cristal": 3349}, 436),
        it("defense", "Éclateur Ionique", {"Fer": 10047, "Cristal": 8373, "Éther": 418}, 8694),
        it("component", "Chasseur", {"Fer": 431, "Cristal": 86, "Éther": 43}, 0, "Infrastructures"),
        it("component", "Destroyer", {"Fer": 475145, "Cristal": 103668, "Éther": 70839}, 50, "Infrastructures"),
        it("component", "Canon laser", {"Fer": 431, "Cristal": 86, "Éther": 0}, 100, "Armement"),
        it("component", "Coque des Monarques", {"Fer": 1727, "Cristal": 431, "Éther": 431}, 0, "Blindages"),
        it("component", "Réacteur antimatière", {"Fer": 38875, "Cristal": 17278, "Éther": 25917}, 100, "Propulsion"),
        it("ship", "Anode", {"Fer": 410152, "Cristal": 100311, "Éther": 95959}, 47, "Militaire"),
        it("ship", "Plasmatique", {"Fer": 1.5e9, "Cristal": 1e6, "Éther": 1e6}, 0, "Militaire"),
        it("ship", "Transpo civil cuir", {"Fer": 406248, "Cristal": 99357, "Éther": 95046}, 0, "Civil"),
    ]
    return items


def _movement(kind, origin, destination, arrival="06:56:16", countdown="01:20:00"):
    return {"type": kind, "arrival": arrival, "countdown": countdown,
            "origin": dict(zip(("planet", "coords", "player"), origin)), "destination": dict(zip(("planet", "coords", "player"), destination))}


def _exp_units(index: int) -> dict:
    """Unités disponibles pour l'exploration sur chaque planète (Strate, index 2, n'a presque rien)."""
    rich = index < 2
    return {
        "PILLARS": {"Soldat": 2507458 if rich else 10, "Rempart de Fer": 968163 if rich else 100, "Spectre du Rift": 0,
                    "Surchargeur Plasmique": 0, "Faucheur Cinétique": 1380303 if rich else 0, "Grand Transporteur": 43025 if rich else 0,
                    "Petit Transporteur": 0},
        "FLEET": {"Testo Civil Transpo Destroy Def": 43 if rich else 0, ">Lentus": 2},
        # Fouille (relevé du vrai jeu) : unités d'assaut, Scientifiques et Mineurs
        "EXCAVATION": {"Soldat": 17827181 if rich else 10, "Rempart de Fer": 968163 if rich else 0, "Spectre du Rift": 0,
                       "Surchargeur Plasmique": 0, "Faucheur Cinétique": 0, "Scientifique": 1185896 if rich else 0, "Mineur": 1686319 if rich else 0},
    }


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
        "slow_build": False,  # vaisseaux et composants : l'effectif n'augmente qu'à la fin de la fabrication
        "popup_after_click": False,
        "require_auth": False,  # vrai : connexion → choix de l'univers → partie, avec session qui peut expirer
        "logged_in": False,
        "login_attempts": 0,
        "actions": [],
        "research_busy": False,
        # Exploration (relevé du vrai jeu, 2026-11-07) : compteurs communs au compte, modèles par moyen, unités par planète
        "exp": {"daily": 3, "daily_max": 46, "active": 2, "active_max": 10, "no_effect": False, "models": {
            "PILLARS": [{"name": "Expe", "units": {"Rempart de Fer": 1500, "Grand Transporteur": 200}},
                        {"name": "300k-5k", "units": {"Soldat": 300000, "Grand Transporteur": 5000}}],
            "FLEET": [{"name": "Planète minière", "units": {"Testo Civil Transpo Destroy Def": 5}}]}},
        # Carte continue : positions réelles (unités de carte) des planètes et des brèches ; Strate n'a aucune brèche proche
        "map": {"api": True, "positions": {"Z25 95 16": [0, 0], "M69 38 06": [11570, 1544], "R59 75 43": [-60000, 40000]},
                "breaches": [["Z26 10 20", 520, 90], ["Z25 60 80", -300, 250], ["M79 50 37", 11629, 2112], ["M69 99 99", 12900, 1600]],
                # Emplacements libres (Fouille possible) : M58 80 21 est le plus proche d'Egide (relevé du vrai jeu)
                "free": [["M68 75 95", 11234, 1931], ["M58 80 21", 11260, 1171], ["Z25 70 30", 200, -150], ["Z25 99 01", 700, 400]],
                # Planètes d'Errants autour d'Egide (mêmes adresses que la liste de la Galaxie) : Torek est la plus proche mais
                # son Drone est grisé (hors du ratio ×2), Bryn a ses piliers fermés, Kairo est la première piratable.
                "errants": [["M97 50 19", "Torek-Veine-165 2", 11575, 1540], ["M69 37 24", "Bryn-Socle-176 3", 11560, 1552],
                            ["M69 34 68", "Kairo-Vigie-378 7", 11600, 1600], ["M69 41 02", "Lyra-Voile-12 1", 11700, 1700]]},
        # Piratage : Naël Soryn (occupé ou non), piratage en cours (cible, fin), clic sans effet
        "hack": {"busy": False, "active": None, "no_effect": False},
        # Bonus d'activité : bouton présent seulement quand un gain est disponible (vrai jeu, 07/11)
        "bonus": False,
        # Missions (onglet Missions) : objectif atteint = bouton « Récupérer » ; une quête récupérable que le bot ne touche pas
        "missions": [{"name": "Connexion quotidienne", "progress": 1, "goal": 1, "claimed": False, "reward": "+5 Astrium, +5 XP"},
                     {"name": "Réaliser 2 Fouilles", "progress": 2, "goal": 2, "claimed": False, "reward": "+5 Astrium, +5 XP"},
                     {"name": "Réaliser 3 attaques", "progress": 0, "goal": 3, "claimed": False, "reward": "+5 Astrium, +5 XP"},
                     {"name": "Cumuler 5 jours de connexion", "progress": 5, "goal": 5, "claimed": True, "reward": "+15 XP"}],
        "quests": [{"name": "Quête d'essai", "progress": 1, "goal": 1, "claimed": False, "reward": "+1 XP"}],
        # Alliance › Techs : jetons (6 au plus) et technologies ; une seule est la priorité de dons (vrai jeu, 08/11)
        "alliance": {"tokens": 5, "max": 6, "priority": "Forge du Bastion", "no_effect": False,
                     "techs": [{"name": "Forge du Bastion", "progress": 47, "need": 300},
                               {"name": "Canalisation Éthérique", "progress": 0, "need": 180},
                               {"name": "Veines Profondes", "progress": 0, "need": 0}]},
        # Fouilles : plafond personnel, Fouilles en cours (origine, site, durée, fin), Mira Venn, autres missions actives
        "exc": {"max": 6, "active": [], "mira_busy": False, "other_missions": 0, "no_effect": False},
        # Capteurs interstellaires : un déploiement du joueur, comme dans l'export (aucune attaque au départ)
        "movements": [_movement("Déploiement", ("6-79-Egide", "M69 38 06", "KameGrazi"), ("8-50-Concorde", "Z25 95 16", "KameGrazi"))],
        # Liste de la Galaxie autour de la planète active (structure de l'export du 2026-11-03)
        "galaxy": [
            {"player": "KameGrazi", "tag": "REPCO", "planet": "6-79-Egide", "coords": "M69 38 06", "actions": []},
            # Errants : ressources du rapport de drone, piliers fermés, Drone grisé (hors du ratio ×2)
            {"player": "Kairo-Vigie-378", "tag": "AI", "planet": "Kairo-Vigie-378 7", "coords": "M69 34 68", "actions": ["Flotte", "Piliers", "Sonde", "Drone"],
             "spy": {"Fer": 2147794, "Cristal": 900000, "Éther": 300000}},
            {"player": "Bryn-Socle-176", "tag": "AI", "planet": "Bryn-Socle-176 3", "coords": "M69 37 24", "actions": ["Flotte", "Piliers", "Sonde", "Drone"],
             "closed": True},
            {"player": "Lyra-Voile-12", "tag": "AI", "planet": "Lyra-Voile-12 1", "coords": "M69 41 02", "actions": ["Flotte", "Piliers", "Sonde", "Drone"],
             "spy": {"Fer": 1200, "Cristal": 800, "Éther": 50}},
            {"player": "Splif", "tag": "REPCO", "planet": "F5", "coords": "M69 06 25", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"player": "Hades33", "tag": "", "planet": "Gaia - M", "coords": "M69 99 83", "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
            {"player": "Torek-Veine-165", "tag": "AI", "planet": "Torek-Veine-165 2", "coords": "M97 50 19", "actions": ["Flotte", "Piliers", "Sonde", "Drone"],
             "spy": {"Fer": 9e9, "Cristal": 9e9, "Éther": 9e9}, "drone_off": True},
            {"mining": True, "planet": "Planete miniere", "coords": "M69 40 68", "actions": ["Miner", "Sonde"]},
        ],
        "ship_mode": "assemblages",  # mode de production affiché sur les cartes de vaisseaux (« Construction » à choisir)
        "planets": [
            {"name": n, "coords": c, "energy": e, "stocks": copy.deepcopy(s), "busy": False, "buildings": _buildings(), "prod": _prod_items(),
             "units": _exp_units(i)}
            for i, ((n, c, e), s) in enumerate(zip(planets, stocks))
        ],
        # Laboratoire : onglets Civil / Militaire ; coûts réglés pour que seule la planète Egide (index 1) ou Concorde (index 0) puisse payer
        "tech": [
            {"name": "Ordinateur Quantique", "tab": "Civil", "level": 21, "costs": {"Fer": 900e6, "Cristal": 1.6e9, "Éther": 600e6}, "duration": "10 h 43 min"},
            {"name": "Technologie Énergie", "tab": "Civil", "level": 20, "costs": {"Fer": 10e6, "Cristal": 5e6, "Éther": 2e6}, "duration": "5 h 21 min"},
            {"name": "Efficience Éthérique", "tab": "Civil", "level": 20, "costs": {}, "duration": "", "locked": "Centre de Recherche Niv. 99"},
            {"name": "Technologie laser", "tab": "Militaire", "level": 24, "costs": {"Fer": 2.6e9, "Cristal": 1.3e9}, "duration": "4 j 18 h"},
            {"name": "Technologie ionique", "tab": "Militaire", "level": 22, "costs": {"Fer": 3.2e9, "Cristal": 978e6, "Éther": 326e6}, "duration": "1 j 10 h"},
            {"name": "Technologie d’espionnage", "tab": "Militaire", "level": 26, "costs": {"Fer": 10.4e9, "Cristal": 52e9, "Éther": 10.4e9}, "duration": "19 j 1 h"},
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
let state = null, overlayOpen = false, navOpen = false;
const sec = () => location.pathname.split('/').pop();
const big = (v) => v >= 1e9 ? (v/1e9).toFixed(3).replace('.', ',') + ' G' : v >= 1e6 ? (v/1e6).toFixed(3).replace('.', ',') + ' M' : String(Math.round(v));
const md = (v) => v >= 1e9 ? (v/1e9).toFixed(1).replace('.', ',') + ' Md' : (v/1e6).toFixed(1).replace('.', ',') + ' M';
const cost = (v) => v >= 1e9 ? (v/1e9).toFixed(3) + 'G' : v >= 1e6 ? (v/1e6).toFixed(3) + 'M' : v >= 1e3 ? (v/1e3).toFixed(1) + 'k' : String(v);
const int = (v) => String(v).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
const afford = (p, it, busy) => !busy && Object.entries(it.costs).every(([k, v]) => p.stocks[k] >= v);
function techCard(p, it, busy) {
  if (it.locked) return `<div class="card"><div><span>Niveau</span><span>${it.level}</span></div><h3>${it.name}</h3><div><span>VERROUILLÉ</span></div><div><span>Prérequis structurels</span><a href="#">${it.locked}</a></div></div>`;
  const ok = afford(p, it, busy);
  const costs = Object.entries(it.costs).map(([k, v]) => `<div><span>${k}</span><span>${cost(v)}</span></div>`).join('');
  return `<div class="card"><div><span>Niveau</span><span>${it.level}</span></div><button type="button" aria-label="Informations"><svg width="8" height="8"></svg></button><h3>${it.name}</h3>`
    + `${costs}<div><span>Temps de recherche</span><span>${it.duration}</span></div>`
    + `<button class="act" data-name="${it.name}" data-kind="tech" ${ok ? '' : 'disabled'}>${ok ? 'Rechercher' : 'Ressources insuffisantes'}</button></div>`;
}
function card(p, it, kind, busy) {
  const ok = afford(p, it, busy);
  const label = kind === 'tech' ? (ok ? 'Rechercher' : 'Ressources insuffisantes') : 'Améliorer';
  const costs = Object.entries(it.costs).map(([k, v]) => `<div><span>${k}</span><span>${cost(v)}</span></div>`).join('');
  const dur = kind === 'tech' ? '<div><span>Temps de recherche</span></div>' : `<div><span>Durée</span><span>${it.duration}</span></div>`;
  return `<div class="card"><div class="top"><div class="badge"><span>${it.level}</span></div><div class="abs"><h3>${it.name}</h3></div></div>`
    + `<div class="body">${costs}${dur}<button class="act" data-name="${it.name}" data-kind="${kind}" ${ok ? '' : 'disabled'}>${label}</button></div></div>`;
}
const kfmt = (v) => v >= 1e9 ? (v / 1e9).toFixed(3).replace('.', ',') + ' G' : v >= 1e6 ? (v / 1e6).toFixed(3).replace('.', ',') + ' M' : v >= 1000 ? (v / 1000).toFixed(3).replace('.', ',') + ' K' : String(Math.round(v));
const ui = {tab: 'Assaut', ship: 'Assemblages', way: 'Piliers', mission: 'ATTAQUE', techTab: 'Civil', zoom: 1, windowRows: [], modelsOpen: false,
            zoomLevel: 4.64, listShown: 25, toasts: [], report: null, freeRows: [], allyTab: 'vue', allyMsg: null, missionTab: 'missions', missionMsg: null,
            form: {target: '', units: {}, copies: '1', duration: '1', hours: 6, mira: false}};
(() => { const q = new URLSearchParams(location.search); if (q.get('mission')) ui.mission = q.get('mission'); if (q.get('method')) ui.way = q.get('method') === 'FLEET' ? 'Flotte' : 'Piliers'; })();
const CK = {Fer: 'F', Cristal: 'C', 'Éther': 'E'};
const costLeaves = (c) => Object.entries(c).map(([k, v]) => `<span>${CK[k]}: ${kfmt(v)}</span>`).join('');
function prodCard(it, verb) {
  const short = it.name === 'Faisceau de Verrouillage' ? 'Faisceau de Verrou' : it.name;
  return `<div class="card"><div class="badge"><span>${int(it.owned)}</span></div><h3>${it.name}</h3><div><span>ATQ</span><span>14</span></div>`
    + `<div>${costLeaves(it.costs)}</div><div><span>1.00s</span></div><div class="ctl"><input type="number" placeholder="Qté" aria-label="Quantité pour ${short}">`
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
  const tabs = ['Assemblages', 'Réparation', 'Composants', 'Modèle', 'Recyclage'].map((t) => `<button type="button" role="tab" class="stab" data-stab="${t}">${t}</button>`).join('');
  if (ui.ship === 'Composants') {
    const rows = items('component').map((i) => `<div class="crow"><div><span title="${i.name}">${i.name}</span><span>×${int(i.owned)}</span></div>`
      + `<div><span title="Capacité de fret">400</span><span title="Unités logistiques">1 UL</span></div>`
      + `<div><div>${costLeaves(i.costs)}</div><span>1,00 s<svg width="8" height="8"></svg></span></div>`
      + `<div><input type="number" placeholder="Qté"><button type="button" data-max="1">MAX</button>`
      + `<button type="button" class="prod" title="Produire" data-section="component" data-name="${i.name}"><svg width="8" height="8"></svg></button></div></div>`).join('');
    return '<h2>Chantier spatial</h2><div>' + tabs + '</div><h3>Gestion des stocks</h3><div>' + rows + '</div>';
  }
  if (ui.ship !== 'Assemblages') return '<h2>Chantier spatial</h2><div>' + tabs + '</div><p>Aucun vaisseau endommagé à réparer</p>';
  const cost = (c) => Object.entries(c).map(([k, v]) => `<span aria-label="${k.toUpperCase()} : ${kfmt(v)}">${kfmt(v)}<img alt=""></span>`).join('');
  const ships = items('ship').map((i) => `<article><div><button type="button"><span class="truncate">${i.name}</span><span>En service : ${i.owned}<svg width="8" height="8"></svg></span><span>${i.tab}</span></button></div>`
    + `<div><span>Actif</span></div><section><div><button type="button" class="mode" aria-pressed="${state.ship_mode === 'construction'}" data-mode="construction">Construction</button>`
    + `<button type="button" class="mode" aria-pressed="${state.ship_mode === 'assemblages'}" data-mode="assemblages">Assemblages</button></div>`
    + (state.ship_mode === 'construction' ? `<div>${cost(i.costs)}</div>` : '<div><b>Composants insuffisants</b></div>')
    + `<div><button type="button" aria-label="Qté -">-</button><input type="number" placeholder="1" aria-label="Qté"><button type="button" aria-label="Qté +">+</button>`
    + `<button type="button" data-max="1">MAX</button><span>1,00 s<svg width="8" height="8"></svg></span>`
    + `<button type="button" class="prod" data-section="ship" data-name="${i.name}">${state.ship_mode === 'construction' ? 'Lancer' : 'Composants insuffisants'}</button></div></section></article>`).join('');
  return '<h2>Chantier spatial</h2><h3>Actifs — Militaire : 6 · Civil : 3 · Total : 9/11</h3><div>' + tabs + '</div><div>' + ships + '</div>';
}
function galaxyMain() {
  const shown = state.galaxy.slice(0, ui.listShown);
  const rows = shown.map((r) => {
    const actions = r.actions.map((a) => a === 'Drone'
      ? `<button type="button" aria-label="Drone" class="drone" data-c="${r.coords}" ${r.drone_off ? 'disabled' : ''}></button>`
      : `<button type="button" aria-label="${a}"></button>`).join('');
    if (r.mining) return `<div class="continuous-inspector__list-row"><button type="button">${r.planet} ${r.coords}</button>${actions}</div>`;
    const who = r.tag ? `${r.player} [${r.tag}]` : r.player;
    return `<div class="continuous-inspector__list-row"><button type="button"></button><button type="button">${who}</button><button type="button">${r.planet} · ${r.coords}</button>${actions}</div>`;
  }).join('');
  const breaches = ui.windowRows.map((a) => `<div class="continuous-inspector__list-row"><button type="button">Brèches${a}</button><button type="button" aria-label="Flotte"></button><button type="button" aria-label="Piliers"></button></div>`).join('');
  const zoom = '<div><button type="button" class="zoom" data-z="-1" aria-label="Zoomer">+</button><button type="button" class="zoom" data-z="1" aria-label="Dézoomer">-</button>'
    + '<button type="button" class="zoom" data-z="0" aria-label="Recentrer la carte">o</button>'
    + `<input type="range" id="zoom-level" aria-label="Niveau de zoom" min="0" max="6" step="0.01" value="${ui.zoomLevel}"></div>`;
  const free = ui.freeRows.map((a) => `<div class="continuous-inspector__list-row"><button type="button">Planètes libres${a}</button><button type="button" aria-label="Coloniser"></button><button type="button" aria-label="Fouille"></button></div>`).join('');
  const more = state.galaxy.length > ui.listShown ? '<button type="button" id="more">Afficher la suite</button>' : '';
  const toast = ui.toasts.length ? `<div class="toasts">${ui.toasts.map((t) => `<div>${t.text}</div>`).join('')}</div>` : '';
  const rep = ui.report;
  const report = rep ? `<div role="dialog" aria-label="Espionnage"><div><span>Rapport de drone : ${rep.planet}</span>`
    + '<button type="button" id="rclose" aria-label="Fermer le message">x</button><button type="button" id="rdel" aria-label="Supprimer le message">d</button></div>'
    + Object.entries(rep.resources).map(([k, v]) => `<div><span>${k}</span><span>${int(v)}</span></div>`).join('') + '</div>' : '';
  return '<h2>Galaxie</h2><input aria-label="Recherche de destination">' + zoom + '<section class="continuous-inspector__list">' + rows + breaches + free + more + '</section>' + toast + report;
}
// Opérations › Exploration : cible, modèles, unités, copies, durée, lancement (structure du vrai jeu).
const METHOD = () => (ui.way === 'Flotte' ? 'FLEET' : 'PILLARS');
const fmtTarget = (v) => { const c = v.replace(/\\s+/g, '').toUpperCase(); return /^[A-Z]\\d{6}$/.test(c) ? `${c.slice(0, 3)} ${c.slice(3, 5)} ${c.slice(5)}` : v; };
function expForm(p) {
  const ex = state.exp, units = p.units[METHOD()], f = ui.form;
  const free = ex.active_max - ex.active;
  const copies = Math.max(1, +f.copies || 1);
  const total = Object.values(f.units).reduce((s, q) => s + (+q || 0), 0);
  const valid = /^[A-Z]\\d{2} \\d{2} \\d{2}$/.test(f.target);
  const ok = valid && total > 0 && copies <= free && Object.entries(f.units).every(([n, q]) => (+q || 0) * copies <= (units[n] || 0));
  const rows = Object.entries(units).map(([n, c]) => `<div><span>${n}</span><span>Disponibles : ${int(c)}</span><input type="number" placeholder="0" class="unit" data-u="${n}" aria-label="Quantité ${n}" value="${f.units[n] || ''}"><button type="button" aria-label="Sélectionner le maximum"></button></div>`).join('');
  const opts = (n, sel, suffix) => Array.from({length: n}, (_, i) => `<option value="${i + 1}" ${String(i + 1) === String(sel) ? 'selected' : ''}>${i + 1}${suffix}</option>`).join('');
  // Fenêtre imbriquée comme sur le vrai jeu : en-tête, « Nouveau modèle (unités actuelles) », nom, Enregistrer, puis les lignes
  const dialog = ui.modelsOpen ? `<div role="dialog" aria-label="Modèles de ${ui.way}"><div class="dialog-body"><div><span>Modèles de ${ui.way}</span><button type="button" id="mclose" aria-label="Fermer">x</button></div>`
    + `<div><span>Nouveau modèle (unités actuelles)</span><span>${ex.models[METHOD()].length} / 6</span><input placeholder="Nom du modèle…"><button type="button" title="Enregistrer la sélection actuelle">s</button></div>`
    + '<div><span>Modèles enregistrés</span>'
    + ex.models[METHOD()].map((m, i) => `<div><div><span>${m.name}</span><span>${Object.values(m.units).reduce((s, q) => s + q, 0)} Unités</span></div>`
      + `<button type="button" class="apply" data-i="${i}" title="Appliquer le modèle">></button><button type="button" class="del" data-i="${i}" title="Supprimer">x</button></div>`).join('') + '</div></div></div>' : '';
  return `<div><span>Explorations quotidiennes</span><span>${ex.daily}/${ex.daily_max}</span></div><div><span>Explorations actives</span><span>${ex.active}/${ex.active_max}</span></div>`
    + `<div class="cible"><label>Cible</label><button type="button">Favoris</button><input type="text" id="target" placeholder="G46 50 50" value="${f.target}">${valid ? `<span class="font-mono">${f.target}</span>` : ''}</div>`
    + `<div><h3>Force d’exploration (${ui.way})</h3><button type="button" id="models-btn">Modèles</button>${rows}</div>`
    + `<select aria-label="Copies" id="copies">${opts(Math.max(1, free), f.copies, '')}</select><select aria-label="Durée nominale" id="duration">${opts(6, f.duration, ' h')}</select>`
    + `<button type="button">Mobilisation générale</button><button type="button" id="launch" ${ok ? '' : 'disabled'}>${ok ? 'Lancer l’exploration' : 'Aucune unité sélectionnée.'}</button>`
    + `<button type="button" id="recall" aria-label="Préparer le retour de la flotte">r</button>` + dialog;
}
// Opérations › Fouille : cible, héros, équipe (1 à 5 000), durée en boutons, compteurs, « Missions en cours » (vrai jeu, 07/11).
const two = (n) => String(n).padStart(2, '0');
function excCards() {
  const now = Date.now() / 1000;
  return state.exc.active.map((a) => {
    const left = Math.max(0, Math.round(a.end - now)), end = new Date(a.end * 1000);
    return `<div class="mcard"><div><span>Fouille</span><span>${a.hours} heures · 0 résultat</span></div>`
      + `<div><span>Active : ${two(end.getHours())}:${two(end.getMinutes())}</span><span>${two(Math.floor(left / 3600))}:${two(Math.floor(left % 3600 / 60))}:${two(left % 60)}</span></div>`
      + `<div><span>Origine</span><span>${a.origin}</span></div><div><span>Destination</span><span>${a.name}</span><span>${a.site}</span></div></div>`;
  }).join('');
}
function excForm(p) {
  const exc = state.exc, units = p.units.EXCAVATION, f = ui.form;
  const missions = exc.active.length + exc.other_missions;
  const total = Object.values(f.units).reduce((s, q) => s + (+q || 0), 0);
  const valid = /^[A-Z]\\d{2} \\d{2} \\d{2}$/.test(f.target);
  const ok = valid && total >= 1 && total <= 5000 && exc.active.length < exc.max && missions < 29
    && Object.entries(f.units).every(([n, q]) => (+q || 0) <= (units[n] || 0));
  const rows = Object.entries(units).map(([n, c]) => `<div><span>${n}</span><span>Stock : ${int(c)}</span><input type="number" placeholder="0" class="unit" data-u="${n}" aria-label="Quantité ${n}" value="${f.units[n] || ''}"></div>`).join('');
  const hero = '<div><span>Héros de mission</span>'
    + `<label><input type="radio" name="hero" class="hero" value="none" ${f.mira ? '' : 'checked'}> Lancer sans héros</label>`
    + `<label><input type="radio" name="hero" class="hero" value="mira" ${f.mira ? 'checked' : ''} ${exc.mira_busy ? 'disabled' : ''}><span>Mira Venn · Niveau 8</span><span>${exc.mira_busy ? 'En mission' : 'Disponible'}</span></label></div>`;
  const hours = [3, 6, 12, 24].map((h) => `<button type="button" class="edur" data-h="${h}" aria-pressed="${f.hours === h}">${h} heures</button>`).join('');
  return `<div class="cible"><label>Cible</label><button type="button">Favoris</button><input type="text" id="target" placeholder="G46 50 50" value="${f.target}">${valid ? `<span class="font-mono">${f.target}</span>` : ''}</div>`
    + hero + `<div><h3>2. Composition de mission</h3><button type="button">Modèles</button>${rows}</div><button type="button">Mobilisation générale</button>`
    + `<div><h3>Durée et lancement</h3>${hours}<div><span>Fouilles actives</span><span>${exc.active.length} / ${exc.max}</span></div>`
    + `<div><span>Missions actives</span><span>${missions} / 29</span></div>`
    + `<button type="button" id="elaunch" ${ok ? '' : 'disabled'}>Lancer la fouille</button></div>`
    + `<section><h3>Missions en cours</h3>${excCards() || '<p>Aucune mission disponible</p>'}</section>`;
}
// Opérations › Piratage : cible, carte de Naël Soryn, durée fixe, « Missions en cours » (vrai jeu, 07/11).
function hackCards() {
  const h = state.hack.active;
  if (!h) return '';
  const now = Date.now() / 1000, left = Math.max(0, Math.round(h.end - now)), end = new Date(h.end * 1000);
  return `<div class="mcard"><div><span>Piratage</span><span># 102-02971</span><span>0 U</span></div>`
    + `<div><span>Arrivée : ${two(end.getHours())}:${two(end.getMinutes())}:${two(end.getSeconds())}</span><span>${two(Math.floor(left / 3600))}:${two(Math.floor(left % 3600 / 60))}:${two(left % 60)}</span></div>`
    + `<div><span>Origine</span><span>${h.origin}</span><span>${h.origin_coords}</span><span>KameGrazi</span></div>`
    + `<div><span>Destination</span><span>${h.name}</span><span>${h.target}</span></div></div>`;
}
function hackForm() {
  const f = ui.form, busy = state.hack.busy || !!state.hack.active;
  const valid = /^[A-Z]\\d{2} \\d{2} \\d{2}$/.test(f.target);
  // Un formulaire d'une autre mission reste caché dans la page, comme sur le vrai jeu.
  return '<div hidden><label>Cible</label><input type="text" placeholder="G46 50 50"><span>Naël Soryn</span><span>Disponible</span></div>'
    + `<div class="cible"><label>Cible</label><input type="text" id="target" placeholder="G46 50 50" value="${f.target}">${valid ? `<span class="font-mono">${f.target}</span>` : ''}</div>`
    + `<div><h3>Piratage par les Piliers</h3><div><span>Naël Soryn</span><span>Niveau 11</span><span>${busy ? 'Indisponible' : 'Disponible'}</span></div>`
    + `<div><span>Durée fixe</span><span>30 min</span></div><div><span>Quota cible du jour</span><span>${valid ? '0 / ∞' : 'Inconnu'}</span></div></div>`
    + `<button type="button" id="hlaunch" ${valid && !busy ? '' : 'disabled'}>Lancer le piratage</button>`
    + `<section><h3>Missions en cours</h3>${(hackCards() + excCards()) || '<p>Aucune mission disponible</p>'}</section>`
    + (ui.toasts.length ? `<div class="toasts">${ui.toasts.map((t) => `<div>${t.text}</div>`).join('')}</div>` : '');
}
// Alliance : onglets (Vue, Territoires, Techs) ; Techs = jetons + technologies (vrai jeu, 08/11).
function allianceMain() {
  const tabs = [['vue', 'Vue'], ['territoires', 'Territoires'], ['techs', '⚡ Techs']].map(([k, n]) =>
    `<button type="button" role="tab" class="allytab" data-k="${k}" aria-selected="${ui.allyTab === k}">${n}</button>`).join('');
  let body = '<p>Manifeste</p>';
  if (ui.allyTab === 'techs') {
    const a = state.alliance;
    const cards = a.techs.map((t) => {
      const prio = t.name === a.priority, open = prio && t.need > 0 && a.tokens > 0;
      const star = prio ? `Retirer ${t.name} de la priorité de dons de l’alliance` : `Marquer ${t.name} comme priorité de dons de l’alliance`;
      return `<article><button type="button" class="star" aria-label="${star}">*</button><h3>${t.name}</h3>`
        + (t.need > 0 ? `<div><span>Progression</span><span>${t.progress} / ${t.need}</span></div>`
          + `<button type="button" class="tok" data-c="1" ${open ? '' : 'disabled'}>+1 jeton</button><button type="button" class="tok" data-c="max" ${open ? '' : 'disabled'}>Max. (+${a.tokens})</button>`
          : '<p>Technologie au niveau maximal</p>') + '</article>';
    }).join('');
    body = `<div><span>Jetons d’alliance</span><span>${a.tokens}</span><span>/${a.max}</span><span>Prochain jeton dans : 1h 40m</span></div>${cards}`;
  }
  return '<h2>Alliance</h2><div role="tablist" aria-label="Sections de l’alliance">' + tabs + '</div>' + body
    + (ui.allyMsg ? `<div class="toast">${ui.allyMsg}</div>` : '');
}
// Missions : onglets Missions / Quêtes / Événements ; « Récupérer » quand l'objectif est atteint (vrai jeu, 08/11).
function missionsMain() {
  const tabs = [['missions', 'Missions'], ['quests', 'Quêtes'], ['events', 'Événements']].map(([k, n]) =>
    `<button type="button" role="tab" class="mtab" data-k="${k}" aria-selected="${ui.missionTab === k}">${n}</button>`).join('');
  const list = ui.missionTab === 'missions' ? state.missions : ui.missionTab === 'quests' ? state.quests : [];
  const rows = list.map((m, i) => `<div class="mrow"><span>${m.name}</span>`
    + (m.claimed ? '<span>Récupéré</span>'
      : m.progress >= m.goal ? `<button type="button" class="claim" data-list="${ui.missionTab}" data-i="${i}" aria-label="Récupérer ${m.reward}">Récupérer</button>`
      : `<span>${m.progress} / ${m.goal}</span>`) + '</div>').join('');
  return '<h2>Missions</h2><div role="tablist">' + tabs + '<button type="button">Actualiser</button></div>'
    + `<section><h3>Quotidiennes</h3>${rows}</section>` + (ui.missionMsg ? `<div role="status">${ui.missionMsg}</div>` : '');
}
function opsMain() {
  const ex = state.exp;
  const ways = ['Piliers', 'Flotte'].map((w) => `<button type="button" class="way" data-way="${w}">${w.toUpperCase()}</button>`).join('');
  const missions = [['ATTAQUE', 'Attaque'], ['TRANSPORT', 'Transport'], ['EXPLORATION', 'Exploration'], ['EXCAVATION', 'Fouille'], ['HACKING', 'Piratage']]
    .map(([m, label]) => `<button type="button" class="mission" data-m="${m}">${label}</button>`).join('');
  let body = '';
  if (ui.mission === 'ATTAQUE') body = '<div><span>2. Composition de mission</span></div><button type="button">LANCEMENT BLOQUÉ</button>';
  if (ui.mission === 'EXPLORATION') body = expForm(state.planets[state.active]);
  if (ui.mission === 'EXCAVATION') body = excForm(state.planets[state.active]);
  if (ui.mission === 'HACKING') body = hackForm();
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
  const title = {buildings: 'Industrie', facilities: 'Infrastructures', tech: 'Laboratoire'}[s] || 'Vue';
  const isProd = ['staff', 'shipyard', 'specialized', 'defense', 'operations', 'galaxy', 'alliance', 'missions'].includes(s);
  let cards = '';
  if (isProd) cards = '';
  else if (s === 'tech') cards = state.tech.filter((t) => t.tab === ui.techTab).map((t) => techCard(p, t, state.research_busy)).join('');
  else cards = p.buildings.filter((b) => b.section === s).map((b) => card(p, b, 'build', p.busy)).join('');
  const planets = state.planets.map((pl, i) => `<button class="pl" data-i="${i}"><span>${pl.name}</span><span>${pl.coords}</span>`
    + `<span>${md(pl.stocks.Fer)}</span><span>${md(pl.stocks.Cristal)}</span><span>${md(pl.stocks['Éther'])}</span></button>`).join('');
  document.getElementById('root').innerHTML =
    '<div class="launcher"><button id="open-planets"><span class="sr-only">Planètes / Favoris / Piliers</span></button><button hidden><span class="sr-only">Planètes</span></button><button><span class="sr-only">Capteurs</span><span class="game-utility-badge game-utility-badge--fleet">' + state.movements.length + '</span></button>'
    + '<button><span class="sr-only">Chat</span></button><button><span class="sr-only">Favoris</span></button></div>'
    + `<div class="planet"><span>${p.name}</span><span>${p.coords}</span></div>`
    + `<button type="button" id="planet-btn" aria-label="Sélectionner la planète active"><span>${p.name}</span><span>${p.coords}</span></button>`
    + `<div id="navlist" ${navOpen ? '' : 'hidden'}><span>Navigation</span>${state.planets.map((pl, i) => `<button type="button" class="npl" data-i="${i}"><span>${pl.name}</span><span>${pl.coords}</span></button>`).join('')}</div>`
    + `<div class="res"><div aria-label="FER : ${big(p.stocks.Fer)}"><span>${big(p.stocks.Fer)}</span><span>FER</span></div><div aria-label="CRISTAL : ${big(p.stocks.Cristal)}"><span>${big(p.stocks.Cristal)}</span><span>CRISTAL</span></div>`
    + `<div aria-label="ÉTHER : ${big(p.stocks['Éther'])}"><span>${big(p.stocks['Éther'])}</span><span>ÉTHER</span></div><div><span>${int(p.energy)}</span><span>ÉNERGIE</span></div>`
    + '<div><span>4 808</span><span>ASTRIUM</span></div><div><span>492</span><span>ÉCHOS</span></div></div>'
    + (isProd ? `<main>${s === 'operations' ? opsMain() : s === 'galaxy' ? galaxyMain() : s === 'alliance' ? allianceMain() : s === 'missions' ? missionsMain() : prodMain(p, s)}</main>` : `<main><h2>${title}</h2>${(s === 'buildings' || s === 'facilities') ? '<div>' + [['buildings', 'Industrie'], ['facilities', 'Infrastructures']].map(([k, n]) => `<button type="button" role="tab" class="tabnav" aria-selected="${k === s}" data-s="${k}">${n}</button>`).join('') + '</div>' : ''}${s === 'tech' ? '<div>' + ['Civil', 'Militaire'].map((n) => `<button type="button" role="tab" class="techtab" aria-selected="${n === ui.techTab}" data-t="${n}">${n}</button>`).join('') + '</div>' : ''}<div class="grid">${cards}</div></main>`)
    + '<nav><button class="nav" data-s="facilities"><span class="sr-only">Installations</span></button><button class="nav" data-s="tech"><span class="sr-only">Laboratoire</span></button>'
    + '<button class="nav" data-s="staff"><span class="sr-only">Population</span></button><button class="nav" data-s="shipyard"><span class="sr-only">Chantier spatial</span></button>'
    + '<button class="nav" data-s="specialized"><span class="sr-only">Appareils spécialisés</span></button><button class="nav" data-s="defense"><span class="sr-only">Défenses</span></button><button class="nav" data-s="operations"><span class="sr-only">Opérations</span></button><button class="nav" data-s="galaxy"><span class="sr-only">Galaxie</span></button>'
    + '<button><span class="sr-only">Vue d’ensemble</span></button></nav>'
    + `<div id="overlay" ${overlayOpen ? '' : 'hidden'}><button id="close-ov">Fermer</button><button>Arrêter la rotation</button>${planets}</div>`
    + sensors()
    + (state.bonus ? '<button type="button" data-guidance-target="activity-bonus" id="bonus"><span data-testid="navigation-bonus-pulse" aria-hidden="true"></span>Bonus</button>' : '')
    + (ui.bonusMsg ? `<div class="bonus-toast">${ui.bonusMsg}</div>` : '')
    + (state.popup ? '<div role="dialog"><button>Confirmer</button></div>' : '');
  document.getElementById('open-planets').onclick = () => { overlayOpen = true; render(); };
  document.getElementById('close-ov').onclick = () => { overlayOpen = false; render(); };
  document.querySelectorAll('.nav').forEach((b) => b.onclick = () => { history.pushState({}, '', '/game/asterion/' + b.dataset.s); render(); });
  document.querySelectorAll('.techtab').forEach((b) => b.onclick = () => { ui.techTab = b.dataset.t; render(); });
  document.querySelectorAll('.tabnav').forEach((b) => b.onclick = () => { history.pushState({}, '', '/game/asterion/' + b.dataset.s); render(); });
  document.getElementById('planet-btn').onclick = () => { navOpen = !navOpen; render(); };
  document.querySelectorAll('.npl').forEach((b) => b.onclick = async () => {
    await fetch('/api/select', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({index: +b.dataset.i})});
    navOpen = false;
    await load();
  });
  document.querySelectorAll('.pl').forEach((b) => b.onclick = async () => {
    await fetch('/api/select', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({index: +b.dataset.i})});
    if (state.close_on_select) overlayOpen = false;
    await load();
  });
  const post = (url, body) => fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  document.querySelectorAll('.zoom').forEach((b) => b.onclick = async () => {
    ui.zoom = Math.max(0, Math.min(3, ui.zoom + (+b.dataset.z)));
    const r = await (await post('/map/continuous/window', {zoom: ui.zoom})).json();
    ui.windowRows = r.rows || [];
    ui.freeRows = r.free_rows || [];
    render();
  });
  const zl = document.getElementById('zoom-level');
  if (zl) {
    zl.oninput = () => { ui.zoomLevel = +zl.value; };
    zl.onchange = () => { ui.zoomLevel = +zl.value; post('/api/_zoom', {level: +zl.value}); };
  }
  const mo = document.getElementById('more'); if (mo) mo.onclick = () => { ui.listShown += 25; render(); };
  document.querySelectorAll('.drone').forEach((b) => b.onclick = async () => {
    const r = await (await post('/map/continuous/destination', {coords: b.dataset.c, action: 'DRONE'})).json();
    if (r.refused) {  // les messages s'empilent et disparaissent chacun après 3 s ; un message identique encore affiché n'est pas répété
      if (ui.toasts.some((x) => x.text === r.refused)) return;
      const t = {text: r.refused};
      ui.toasts.push(t);
      setTimeout(() => { ui.toasts = ui.toasts.filter((x) => x !== t); render(); }, 3000);
    } else ui.report = r.report;
    render();
  });
  const rcl = document.getElementById('rclose'); if (rcl) rcl.onclick = () => { ui.report = null; render(); };
  const rdl = document.getElementById('rdel'); if (rdl) rdl.onclick = async () => {
    await post('/api/_spy_delete', {planet: ui.report.planet});
    ui.report = null;
    render();
  };
  const tgt = document.getElementById('target');
  if (tgt) tgt.oninput = () => { ui.form.target = fmtTarget(tgt.value); render(); };
  document.querySelectorAll('.unit').forEach((i) => i.oninput = () => { ui.form.units[i.dataset.u] = i.value; render(); });
  const cp = document.getElementById('copies'); if (cp) cp.onchange = () => { ui.form.copies = cp.value; render(); };
  const du = document.getElementById('duration'); if (du) du.onchange = () => { ui.form.duration = du.value; render(); };
  const mb = document.getElementById('models-btn'); if (mb) mb.onclick = () => { ui.modelsOpen = true; render(); };
  const mc = document.getElementById('mclose'); if (mc) mc.onclick = () => { ui.modelsOpen = false; render(); };
  document.querySelectorAll('.apply').forEach((b) => b.onclick = () => {
    const m = state.exp.models[METHOD()][+b.dataset.i];
    ui.form.units = Object.fromEntries(Object.entries(m.units).map(([k, v]) => [k, String(v)]));
    ui.modelsOpen = false;
    render();
  });
  document.querySelectorAll('.del').forEach((b) => b.onclick = () => post('/api/_forbidden', {what: 'Supprimer un modèle'}));
  const rc = document.getElementById('recall'); if (rc) rc.onclick = () => post('/api/_forbidden', {what: 'Préparer le retour de la flotte'});
  document.querySelectorAll('.edur').forEach((b) => b.onclick = () => { ui.form.hours = +b.dataset.h; render(); });
  document.querySelectorAll('.hero').forEach((r) => r.onchange = () => { ui.form.mira = r.value === 'mira' && r.checked; render(); });
  document.querySelectorAll('.mtab').forEach((b) => b.onclick = () => { ui.missionTab = b.dataset.k; render(); });
  document.querySelectorAll('.claim').forEach((b) => b.onclick = async () => {
    const r = await (await post('/api/claim', {list: b.dataset.list, index: +b.dataset.i})).json();
    ui.missionMsg = r.message;
    setTimeout(() => { ui.missionMsg = null; render(); }, 3000);
    await load();
  });
  document.querySelectorAll('.allytab').forEach((b) => b.onclick = () => { ui.allyTab = b.dataset.k; render(); });
  document.querySelectorAll('.star').forEach((b) => b.onclick = () => post('/api/_forbidden', {what: 'Changer la priorité de dons'}));
  document.querySelectorAll('.tok').forEach((b) => b.onclick = async () => {
    const r = await (await post('/api/token', {count: b.dataset.c})).json();
    if (r.message) { ui.allyMsg = r.message; setTimeout(() => { ui.allyMsg = null; render(); }, 3000); }
    await load();
  });
  const bn = document.getElementById('bonus');
  if (bn) bn.onclick = async () => {
    const r = await (await post('/api/bonus', {})).json();
    ui.bonusMsg = r.message;
    setTimeout(() => { ui.bonusMsg = null; render(); }, 4000);
    await load();
  };
  const hg = document.getElementById('hlaunch');
  if (hg) hg.onclick = async () => {
    const r = await (await post('/api/hack', {target: ui.form.target})).json();
    if (r.refused) {
      const t = {text: r.refused};
      ui.toasts.push(t);
      setTimeout(() => { ui.toasts = ui.toasts.filter((x) => x !== t); render(); }, 3000);
    }
    await load();
  };
  const eg = document.getElementById('elaunch');
  if (eg) eg.onclick = async () => {
    const f = ui.form;
    const r = await post('/api/excavate', {target: f.target, hours: f.hours, mira: f.mira,
                                           units: Object.fromEntries(Object.entries(f.units).map(([k, v]) => [k, +v || 0]))});
    if (r.ok) ui.form = {target: '', units: {}, copies: '1', duration: '1', hours: 6, mira: false};
    await load();
  };
  const go = document.getElementById('launch');
  if (go) go.onclick = async () => {
    const f = ui.form;
    const r = await post('/api/explore', {method: METHOD(), target: f.target, copies: +f.copies, duration: +f.duration,
                                          units: Object.fromEntries(Object.entries(f.units).map(([k, v]) => [k, +v || 0]))});
    if (r.ok) ui.form = {target: '', units: {}, copies: '1', duration: '1'};
    await load();
  };
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
        item["pending"] = True  # le niveau n'augmente qu'à la fin de la construction (voir _complete)
        if research:
            g["research_busy"] = True
        else:
            planet["busy"] = True
        g["actions"].append({"kind": "research" if research else "building", "planet": planet["name"], "name": item["name"], "level_after": item["level"] + 1})
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
        if g["no_debit"]:
            pass  # clic sans aucun effet visible : ni débit, ni file d'attente
        elif body["section"] == "population" or g.get("slow_build"):
            item["queue"] += qty  # entraînement ou fabrication longue : l'effectif n'augmente qu'à la fin (voir _complete)
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
        g = app.state.game
        exp = g["exp"]
        for key in ("daily", "daily_max", "active", "active_max", "no_effect", "models"):
            if key in body:
                exp[key] = body[key]
        if "map_api" in body:
            g["map"]["api"] = body["map_api"]
        for index, by_method in (body.get("units") or {}).items():
            for method, units in by_method.items():
                g["planets"][int(index)]["units"][method].update(units)
        return {"ok": True}

    ZOOM_RADII = (300, 1000, 4000, 20000)  # demi-largeur de la fenêtre visible (unités de carte) selon le cran de zoom

    @app.post("/map/continuous/window")
    async def map_window(request: Request):
        """Fenêtre visible de la carte, comme le vrai serveur : DETAIL (objets et positions) de près, SUMMARY de loin."""
        g, body = app.state.game, await request.json()
        zoom = max(0, min(3, int(body.get("zoom", 1))))
        positions, breaches = g["map"]["positions"], g["map"]["breaches"]
        cx, cy = positions.get(g["planets"][g["active"]]["coords"], [0, 0])
        radius = ZOOM_RADII[zoom]

        def inside(x, y, r=radius):
            return abs(x - cx) <= r and abs(y - cy) <= r

        objects = [{"kind": "PLANET", "address": c, "positionMilli": [x * 1000, y * 1000]} for c, (x, y) in positions.items() if inside(x, y)]
        objects += [{"kind": "BREACH", "address": a, "positionMilli": [x * 1000, y * 1000]} for a, x, y in breaches if inside(x, y)]
        objects += [{"kind": "PVE_PLANET", "address": a, "name": n, "positionMilli": [x * 1000, y * 1000]}
                    for a, n, x, y in g["map"]["errants"] if inside(x, y)]
        free = g["map"]["free"]
        objects += [{"kind": "FREE_SLOT", "address": a, "positionMilli": [x * 1000, y * 1000], "capabilities": ["COLONIZE", "EXCAVATION"]}
                    for a, x, y in free if inside(x, y)]
        free_rows = [a for a, x, y in free if inside(x, y, radius if g["map"]["api"] else ZOOM_RADII[2])]
        detail = zoom <= 2 and g["map"]["api"]
        # Sans le détail (secours), la liste « Ensemble » montre quand même les brèches proches
        rows = [a for a, x, y in breaches if inside(x, y, radius if g["map"]["api"] else ZOOM_RADII[2])]
        bounds = [[(cx - radius) * 1000, (cy - radius) * 1000], [(cx + radius) * 1000, (cy + radius) * 1000]]
        return {"success": True, "rows": rows, "free_rows": free_rows,
                "projection": {"mode": "DETAIL" if detail else "SUMMARY", "boundsMilli": bounds, "objects": objects if detail else []}}

    @app.post("/map/continuous/destination")
    async def destination(request: Request):
        """Clic sur Drone d'une ligne de la Galaxie : message (piliers fermés, refus) ou rapport de drone."""
        g, body = app.state.game, await request.json()
        row = next(r for r in g["galaxy"] if r["coords"] == body["coords"])
        g["actions"].append({"kind": "drone", "target": row["coords"], "planet": row["planet"]})
        if row.get("closed"):
            return {"refused": "Impossible d'atteindre la cible : les piliers de la colonie de destination sont désactivés."}
        if row.get("refuse"):
            return {"refused": row["refuse"]}
        return {"report": {"planet": row["planet"], "resources": row.get("spy") or {"Fer": 0, "Cristal": 0, "Éther": 0}}}

    @app.post("/api/_spy_delete")
    async def spy_delete(request: Request):
        app.state.game["actions"].append({"kind": "spy_delete", "planet": (await request.json())["planet"]})
        return {"ok": True}

    @app.post("/api/_zoom")
    async def zoom_level(request: Request):
        app.state.game["actions"].append({"kind": "zoom", "level": (await request.json())["level"]})
        return {"ok": True}

    @app.post("/api/explore")
    async def explore(request: Request):
        g, body = app.state.game, await request.json()
        exp, planet = g["exp"], g["planets"][g["active"]]
        copies = int(body["copies"])
        units = {k: int(v) for k, v in body["units"].items() if int(v) > 0}
        avail = planet["units"][body["method"]]
        if (not units or exp["daily"] + copies > exp["daily_max"] or exp["active"] + copies > exp["active_max"]
                or any(q * copies > avail.get(n, 0) for n, q in units.items())):
            raise HTTPException(409, "exploration refusée")
        if exp["no_effect"]:
            return {"ok": True}  # clic sans effet visible : le compteur ne bouge pas
        exp["daily"] += copies
        exp["active"] += copies
        for n, q in units.items():
            avail[n] -= q * copies
        g["actions"].append({"kind": "exploration", "planet": planet["name"], "method": body["method"], "target": body["target"],
                             "units": units, "copies": copies, "duration": int(body["duration"])})
        return {"ok": True}

    @app.post("/api/excavate")
    async def excavate(request: Request):
        """« Lancer la fouille » : refusée si l'équipe, les places ou le site ne conviennent pas (comme le vrai jeu)."""
        g, body = app.state.game, await request.json()
        exc, planet = g["exc"], g["planets"][g["active"]]
        units = {k: int(v) for k, v in body["units"].items() if int(v) > 0}
        avail = planet["units"]["EXCAVATION"]
        if (not 1 <= sum(units.values()) <= 5000 or len(exc["active"]) >= exc["max"]
                or any(q > avail.get(n, 0) for n, q in units.items())
                or any(a["site"] == body["target"] for a in exc["active"]) or (body["mira"] and exc["mira_busy"])):
            raise HTTPException(409, "fouille refusée")
        if exc["no_effect"]:
            return {"ok": True}  # clic sans effet visible : le compteur ne bouge pas
        for n, q in units.items():
            avail[n] -= q
        hours = int(body["hours"])
        exc["active"].append({"origin": planet["name"], "site": body["target"], "name": f"Site {body['target']}", "hours": hours,
                              "end": time.time() + hours * 3600})
        exc["mira_busy"] = exc["mira_busy"] or bool(body["mira"])
        g["actions"].append({"kind": "excavation", "planet": planet["name"], "target": body["target"], "units": units,
                             "hours": hours, "mira": bool(body["mira"])})
        return {"ok": True}

    @app.post("/api/claim")
    async def claim(request: Request):
        """« Récupérer » d'une mission (ou d'une quête) dont l'objectif est atteint."""
        g, body = app.state.game, await request.json()
        item = g[body["list"]][int(body["index"])]
        if item["claimed"] or item["progress"] < item["goal"]:
            raise HTTPException(409, "rien à récupérer")
        item["claimed"] = True
        g["actions"].append({"kind": "claim", "list": body["list"], "name": item["name"]})
        return {"message": "5 Astrium et 5 XP réclamés."}

    @app.post("/api/token")
    async def token(request: Request):
        """« +1 jeton » ou « Max. » sur la technologie prioritaire."""
        g, body = app.state.game, await request.json()
        a = g["alliance"]
        tech = next(t for t in a["techs"] if t["name"] == a["priority"])
        count = a["tokens"] if body["count"] == "max" else 1
        if count <= 0 or tech["need"] <= 0:
            raise HTTPException(409, "aucun jeton à donner")
        if a["no_effect"]:
            return {"message": None}
        a["tokens"] -= count
        tech["progress"] += count
        g["actions"].append({"kind": "token", "tech": tech["name"], "count": count})
        return {"message": f"Don effectué : {count} jeton(s)."}

    @app.post("/api/_alliance")
    async def set_alliance(request: Request):
        body, a = await request.json(), app.state.game["alliance"]
        for key in ("tokens", "priority", "no_effect"):
            if key in body:
                a[key] = body[key]
        return {"ok": True}

    @app.post("/api/bonus")
    async def bonus(request: Request):
        """Clic sur « Bonus » : gain crédité, bouton retiré (prochain gain plus tard)."""
        g = app.state.game
        if not g["bonus"]:
            raise HTTPException(409, "aucun bonus disponible")
        g["bonus"] = False
        g["actions"].append({"kind": "bonus", "planet": g["planets"][g["active"]]["name"]})
        return {"message": "Bonus récupéré (12 min) : +11 077 267 Fer, +7 989 190 Cristal, +2 823 034 Éther, +8 Astrium, +0 Échos."}

    @app.post("/api/_bonus")
    async def set_bonus(request: Request):
        app.state.game["bonus"] = bool((await request.json())["available"])
        return {"ok": True}

    @app.post("/api/hack")
    async def hack(request: Request):
        """« Lancer le piratage » : refusé (message) si les piliers de l'Errant sont fermés, sinon Naël part 30 minutes."""
        g, body = app.state.game, await request.json()
        h, planet = g["hack"], g["planets"][g["active"]]
        if h["busy"] or h["active"]:
            raise HTTPException(409, "Naël est indisponible")
        row = next((r for r in g["galaxy"] if r.get("coords") == body["target"]), None)
        g["actions"].append({"kind": "hack", "planet": planet["name"], "target": body["target"]})
        if row is not None and row.get("closed"):
            return {"refused": "Impossible d'atteindre la cible : les piliers de la colonie de destination sont désactivés."}
        if h["no_effect"]:
            return {"ok": True}
        h["active"] = {"origin": planet["name"], "origin_coords": planet["coords"], "target": body["target"],
                       "name": row["planet"] if row else body["target"], "end": time.time() + 1800}
        return {"ok": True}

    @app.post("/api/_hack")
    async def set_hack(request: Request):
        """Crochet de test : Naël occupé, piratage en cours (« end_in » secondes) ou terminé (active: null), clic sans effet."""
        body, h = await request.json(), app.state.game["hack"]
        for key in ("busy", "no_effect"):
            if key in body:
                h[key] = body[key]
        if "active" in body:
            a = body["active"]
            h["active"] = None if a is None else {"origin_coords": "M69 38 06", "name": a["target"], **a, "end": time.time() + a.pop("end_in", 1800)}
        return {"ok": True}

    @app.post("/api/_exc")
    async def set_exc(request: Request):
        """Crochet de test : plafond, Fouilles en cours (« end_in » en secondes), Mira, autres missions, clic sans effet."""
        body, exc = await request.json(), app.state.game["exc"]
        for key in ("max", "mira_busy", "other_missions", "no_effect"):
            if key in body:
                exc[key] = body[key]
        if "active" in body:
            exc["active"] = [{"name": f"Site {a['site']}", **a, "end": time.time() + a.pop("end_in", 3600)} for a in body["active"]]
        return {"ok": True}

    @app.post("/api/_forbidden")
    async def forbidden(request: Request):
        app.state.game["actions"].append({"kind": "forbidden", "what": (await request.json())["what"]})
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
        for key in ("close_on_select", "no_debit", "popup_after_click", "require_auth", "slow_build"):
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
        for tech in g["tech"]:
            if tech.pop("pending", False):
                tech["level"] += 1
        for p in g["planets"]:
            p["busy"] = False
            for building in p["buildings"]:
                if building.pop("pending", False):
                    building["level"] += 1
            for item in p["prod"]:  # la population en file d'attente est terminée
                item["owned"] += item["queue"]
                item["queue"] = 0
        return {"ok": True}

    @app.get("/api/_actions")
    async def actions():
        return JSONResponse(app.state.game["actions"])

    return app
