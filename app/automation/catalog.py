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
