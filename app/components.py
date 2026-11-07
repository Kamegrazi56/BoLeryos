"""Catalogue des 37 composants de l'onglet « Composants » du Chantier spatial (relevé sur le jeu le 2026-11-07).

Les composants sont rangés en 6 catégories (sur la page : Infrastructures, Hangars, Armement, Blindages, Boucliers, Propulsion).
Chaque ligne du tableau affiche le nom, l'effectif « ×N », le coût d'UNE unité (F / C / E), sa durée, un champ « Qté », « MAX »
et un bouton « Produire ». Les réglages (lignes élément + mode + quantité + planètes) sont dans `<data_dir>/components.json`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .quantities import LineSpec


@dataclass(frozen=True)
class Component:
    id: str
    label: str  # nom affiché par le jeu
    category: str


def _c(category: str, *labels: str) -> list[Component]:
    return [Component(label.lower().replace(" ", "-").replace(".", "").replace("é", "e").replace("è", "e").replace("à", "a").replace("ô", "o"), label, category) for label in labels]


COMPONENTS: tuple[Component, ...] = tuple(
    _c("Infrastructures", "Chasseur", "Intercepteur", "Transporteur", "Escorteur", "Navette", "Frégate", "Corvette", "Croiseur", "Croiseur lourd", "Cuirassé", "Destroyer")
    + _c("Hangars", "Hangar léger", "Hangar moyen", "Hangar lourd")
    + _c("Armement", "Station de tir", "Canon laser", "Missile IEM", "Canon à plasma", "Rayon plasma", "Disrupteur", "Canon des Monarques", "Rayon des Monarques")
    + _c("Blindages", "Coque à faible densité", "Coque à densité moyenne", "Coque haute densité", "Coque des Monarques")
    + _c("Boucliers", "Petit bouclier", "Grand bouclier", "Bouclier des Monarques")
    + _c("Propulsion", "Turboréacteur", "Statoréacteur", "Propulseur à propergol", "Propulseur hybride", "Propulseur ionique", "Propulseur photonique", "Propulseur nuc. therm.", "Réacteur antimatière")
)
CATEGORIES = ("Infrastructures", "Hangars", "Armement", "Blindages", "Boucliers", "Propulsion")
COMPONENT_LABELS = {c.id: c.label for c in COMPONENTS}
SPEC = LineSpec("components.json", "composant", frozenset(COMPONENT_LABELS))
