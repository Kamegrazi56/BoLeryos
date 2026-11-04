"""Liste des bâtiments et des recherches du jeu, intégrée au bot.

Relevée sur les pages du jeu (Industrie, Infrastructures, Laboratoire). Si une mise à jour du jeu ajoute un
élément, le bot le découvre en lisant l'écran et il apparaît dans la rubrique « Autres (lus dans le jeu) ».
"""

from __future__ import annotations

from .numbers import norm

OTHER_GROUP = "Autres (lus dans le jeu)"

BUILDING_GROUPS: list[tuple[str, list[str]]] = [
    ("Industrie", [
        "Mine de Fer", "Extracteur de Cristal", "Synthétiseur d'Éther", "Centrale Énergétique",
        "Centrale à Impulsion", "Hangar de Fer", "Hangar de Cristal", "Cuve d'Éther",
    ]),
    ("Infrastructures", [
        "Usine Robot", "Chantier Spatial", "Centre de Recherche", "Caserne", "Institut de Formation",
        "Matrice de Coalescence", "Tour à résonance", "Terraformeur",
    ]),
]

RESEARCH: list[str] = [
    "Ordinateur quantique", "Technologie Énergie", "Technologie Hyperespace", "Technologie Exploration",
    "Infrastructures", "Efficience éthérique", "Forage orbital", "Ingénierie minière", "Extraction du fer",
    "Extraction cristalline", "Raffinement de l’Éther", "Technologie d’espionnage", "Technologie laser",
    "Technologie ionique", "Technologie plasma", "Réacteur à Combustion", "Réacteur à Impulsion",
    "Propulsion Hyperespace", "Systèmes d’armement", "Alliages de coque", "Technologie des boucliers",
    "Virus des Piliers", "Champ de force des Piliers",
]


def builtin(kind: str) -> list[tuple[str, str]]:
    """[(nom, groupe)] dans l'ordre du jeu."""
    if kind == "buildings":
        return [(name, group) for group, names in BUILDING_GROUPS for name in names]
    return [(name, "Recherches") for name in RESEARCH]


def entries(kind: str, discovered: list[str], levels: dict[str, tuple[int, int]]) -> list[dict]:
    """Liste affichée dans l'interface : éléments intégrés puis éléments découverts, avec niveau actuel connu."""
    result, seen = [], set()
    for name, group in builtin(kind):
        seen.add(norm(name))
        result.append((name, group))
    for name in sorted(discovered):
        if norm(name) not in seen:
            seen.add(norm(name))
            result.append((name, OTHER_GROUP))
    out = []
    for name, group in result:
        low_high = levels.get(norm(name))
        out.append({"name": name, "group": group, "level_min": low_high[0] if low_high else None, "level_max": low_high[1] if low_high else None})
    return out


# Raccourcis pour remplir la liste d'améliorations : de simples regroupements de noms, sans prétendre qu'un ordre
# est « le bon » (le joueur le modifie ensuite). Les niveaux à ajouter sont choisis au moment de l'application.
MINES = ["Mine de Fer", "Extracteur de Cristal", "Synthétiseur d'Éther"]
PRESETS = [
    {"id": "mines", "label": "Les 3 mines", "names": MINES},
    {"id": "mines_energy", "label": "Les 3 mines et l'énergie", "names": MINES + ["Centrale Énergétique"]},
    {"id": "mines_energy_storage", "label": "Mines, énergie et stockage", "names": MINES + [
        "Centrale Énergétique", "Hangar de Fer", "Hangar de Cristal", "Cuve d'Éther"]},
]


# --------------------------------------------------------------------------------- production
# Pages où l'on produit « un nombre » d'éléments (relevées sur le jeu). Les modèles de vaisseaux sont créés par le
# joueur : ils ne peuvent pas être intégrés d'avance, le bot les découvre en lisant la page Chantier spatial.
PRODUCTION_SECTIONS = [
    {"id": "population", "label": "Population", "block": "population", "page": "staff"},
    {"id": "ships", "label": "Chantier spatial : vaisseaux", "block": "shipyard", "page": "shipyard"},
    {"id": "components", "label": "Chantier spatial : composants", "block": "shipyard", "page": "shipyard"},
    {"id": "specialized", "label": "Appareils spécialisés", "block": "specialized", "page": "specialized"},
    {"id": "defense", "label": "Défenses", "block": "defense", "page": "defense"},
]
PRODUCTION_SECTION_IDS = [s["id"] for s in PRODUCTION_SECTIONS]

# Ordre d'exécution par défaut des « blocs » (les ressources vont d'abord au premier) : réglable par le joueur.
PRIORITY_BLOCKS = ["constructions", "research", "population", "shipyard", "specialized", "defense", "expeditions"]
BLOCK_LABELS = {
    "constructions": "Constructions (bâtiments)", "research": "Recherches", "population": "Population",
    "shipyard": "Chantier spatial (vaisseaux et composants)", "specialized": "Appareils spécialisés", "defense": "Défenses",
    "expeditions": "Expéditions (exploration)",
}

# Population : onglet du jeu → unités (le « Soldat » de base n'a pas de bouton de recrutement).
POPULATION: list[tuple[str, list[str]]] = [
    ("Assaut", ["Rempart de Fer", "Spectre du Rift", "Surchargeur Plasmique", "Faucheur Cinétique"]),
    ("Logistique", ["Pilote", "Technicien", "Informaticien"]),
    ("Administration", ["Scientifique", "Ingénieur", "Mineur"]),
]
SPECIALIZED = [
    "Sonde Spatiale", "Drone Terrestre", "Petit Transporteur", "Grand Transporteur", "Recycleur I", "Recycleur II",
    "Foreuse orbitale",
]
DEFENSE = [
    "Sentinelle de Rempart", "Faisceau de Verrouillage", "Éclateur Ionique", "Forge Plasmique", "Relais de Rupture",
    "Égide de Trame", "Lance du Trône", "Bastion d'Orbite",
]
COMPONENTS: list[tuple[str, list[str]]] = [
    ("Infrastructures", ["Chasseur", "Intercepteur", "Transporteur", "Escorteur", "Navette", "Frégate", "Corvette", "Croiseur",
                         "Croiseur lourd", "Cuirassé", "Destroyer"]),
    ("Hangars", ["Hangar léger", "Hangar moyen", "Hangar lourd"]),
    ("Armement", ["Station de tir", "Canon laser", "Missile IEM", "Canon à plasma", "Rayon plasma", "Disrupteur",
                  "Canon des Monarques", "Rayon des Monarques"]),
    ("Blindages", ["Coque à faible densité", "Coque à densité moyenne", "Coque haute densité", "Coque des Monarques"]),
    ("Boucliers", ["Petit bouclier", "Grand bouclier", "Bouclier des Monarques"]),
    ("Propulsion", ["Turboréacteur", "Statoréacteur", "Propulseur à propergol", "Propulseur hybride", "Propulseur ionique",
                    "Propulseur photonique", "Propulseur nuc. therm.", "Réacteur antimatière"]),
]


def production_entries(discovered_ships: list[str]) -> dict[str, list[dict]]:
    """Éléments proposés par section : [{name, group}]."""
    ships = [{"name": n, "group": "Mes modèles (lus dans le jeu)"} for n in sorted(discovered_ships)]
    return {
        "population": [{"name": n, "group": g} for g, names in POPULATION for n in names],
        "ships": ships,
        "components": [{"name": n, "group": g} for g, names in COMPONENTS for n in names],
        "specialized": [{"name": n, "group": "Appareils spécialisés"} for n in SPECIALIZED],
        "defense": [{"name": n, "group": "Défenses"} for n in DEFENSE],
    }


def section_of_block(block: str) -> list[str]:
    return [s["id"] for s in PRODUCTION_SECTIONS if s["block"] == block]


# ------------------------------------------------------------------------------------ expéditions
# Page Opérations › Exploration. « piliers » : unités au sol (terrestre) ; « flotte » : vaisseaux (spatial).
EXPEDITION_WAYS = {"piliers": "Piliers (terrestre)", "flotte": "Flotte (spatial)"}
EXPEDITION_UNIT_MODES = ("number", "max", "percent")


def expedition_units(discovered_ships: list[str]) -> dict[str, list[dict]]:
    """Unités proposées par voie : unités au sol et appareils pour les Piliers ; modèles (lus dans le jeu) et appareils
    pour la Flotte."""
    ground = [{"name": "Soldat", "group": "Assaut"}] + [{"name": n, "group": g} for g, names in POPULATION for n in names]
    devices = [{"name": n, "group": "Appareils spécialisés"} for n in SPECIALIZED]
    ships = [{"name": n, "group": "Mes modèles (lus dans le jeu)"} for n in sorted(discovered_ships)]
    return {"piliers": ground + devices, "flotte": ships + devices}
