"""Attaque automatique des errants [AI].

Le joueur choisit un intervalle de coordonnées, un minimum par ressource (Fer, Cristal, Éther), et combien de ces
ressources doivent dépasser leur minimum (1, 2 ou 3). Les ressources d'une cible viennent du rapport d'espionnage.

État actuel : SIMULATION, et la lecture des rapports d'espionnage n'est PAS codée (l'export ne contient aucun rapport) :
tant qu'aucun rapport n'est connu pour une cible, le bot ne décide rien pour elle et le dit. Aucun clic d'attaque.
"""

from __future__ import annotations

from dataclasses import dataclass

LABELS = (("fer", "Fer", "min_fer"), ("cristal", "Cristal", "min_cristal"), ("ether", "Éther", "min_ether"))


@dataclass
class Verdict:
    attack: bool
    above: list[str]  # ressources au-dessus de leur minimum
    reason: str


def judge(resources: dict[str, float], raid: dict) -> Verdict:
    """`resources` : {"fer", "cristal", "ether"} lus dans le rapport d'espionnage."""
    above = [label for key, label, minimum in LABELS
             if resources.get(key) is not None and resources[key] > raid[minimum]]
    needed = raid["required"]
    if len(above) >= needed:
        return Verdict(True, above, f"{len(above)} ressource(s) au-dessus du minimum ({', '.join(above)})")
    return Verdict(False, above, f"{len(above)} ressource(s) au-dessus du minimum sur {needed} demandée(s)")


def describe_resources(resources: dict[str, float]) -> str:
    return ", ".join(f"{label} {resources[key]:,.0f}".replace(",", " ") for key, label, _ in LABELS if resources.get(key) is not None)
