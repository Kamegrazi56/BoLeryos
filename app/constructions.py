"""Réglages de la construction de bâtiments : catalogue des 16 bâtiments du jeu et liste d'objectifs.

Le joueur compose une **liste ordonnée** de lignes « bâtiment + niveau cible » (l'ordre est la priorité) et choisit
**toutes les planètes** (`planets` vide) ou **les planètes cochées**. L'ancien format (`scope` « all » / « one » +
`planet`) est encore lu. Réglages enregistrés dans `<data_dir>/constructions.json`.
Ce module ne clique sur rien : le lancement des constructions est dans builder.py.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .quantities import _clean_planets

FILE_NAME = "constructions.json"
MAX_LINES = 50
MAX_LEVEL = 500
MAX_PLANET_LENGTH = 60


@dataclass(frozen=True)
class Building:
    id: str
    label: str  # nom affiché par le jeu
    page: str  # onglet du jeu : « Industrie » (/buildings) ou « Infrastructures » (/facilities)


# Relevé sur le jeu (version 1.1.25) : 8 bâtiments dans Industrie, 8 dans Infrastructures.
BUILDINGS: tuple[Building, ...] = (
    Building("mine-de-fer", "Mine de Fer", "Industrie"),
    Building("extracteur-de-cristal", "Extracteur de Cristal", "Industrie"),
    Building("synthetiseur-d-ether", "Synthétiseur d'Éther", "Industrie"),
    Building("centrale-energetique", "Centrale Énergétique", "Industrie"),
    Building("centrale-a-impulsion", "Centrale à Impulsion", "Industrie"),
    Building("hangar-de-fer", "Hangar de Fer", "Industrie"),
    Building("hangar-de-cristal", "Hangar de Cristal", "Industrie"),
    Building("cuve-d-ether", "Cuve d'Éther", "Industrie"),
    Building("usine-robot", "Usine Robot", "Infrastructures"),
    Building("chantier-spatial", "Chantier Spatial", "Infrastructures"),
    Building("centre-de-recherche", "Centre de Recherche", "Infrastructures"),
    Building("caserne", "Caserne", "Infrastructures"),
    Building("institut-de-formation", "Institut de Formation", "Infrastructures"),
    Building("matrice-de-coalescence", "Matrice de Coalescence", "Infrastructures"),
    Building("tour-a-resonance", "Tour à Résonance", "Infrastructures"),
    Building("terraformeur", "Terraformeur", "Infrastructures"),
)
BUILDING_IDS = {b.id for b in BUILDINGS}

DEFAULTS: dict = {"planets": [], "queue": []}


class ConstructionError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _planets_of(data: dict):
    """Planètes choisies : `planets` (liste, vide = toutes), ou l'ancien `scope` + `planet`."""
    if "planets" in data:
        return data["planets"]
    if data.get("scope", "all") != "one":
        return []
    planet = data.get("planet", "")
    if not isinstance(planet, str) or not planet.strip():
        return None  # « une seule » sans planète : refusé
    return [planet]


def where(planets: list[str]) -> str:
    return ", ".join(planets) or "toutes les planètes"


def validate(data: dict, known_planets: list[str] | None = None) -> dict:
    """Réglages complets et valides, ou ConstructionError avec la liste des problèmes (en français)."""
    errors: list[str] = []
    planets, problem = _clean_planets(_planets_of(data), known_planets)
    if planets is None:
        errors.append(f"Planètes : {problem}")
        planets = []
    raw_queue = data.get("queue", [])
    queue: list[dict] = []
    if not isinstance(raw_queue, list) or len(raw_queue) > MAX_LINES:
        errors.append(f"Liste : {MAX_LINES} lignes au plus.")
        raw_queue = []
    seen: set[str] = set()
    for i, line in enumerate(raw_queue, start=1):
        building = line.get("building") if isinstance(line, dict) else None
        level = line.get("level") if isinstance(line, dict) else None
        if building not in BUILDING_IDS:
            errors.append(f"Ligne {i} : bâtiment inconnu.")
        elif building in seen:
            errors.append(f"Ligne {i} : ce bâtiment est déjà dans la liste (une seule ligne par bâtiment).")
        elif isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= MAX_LEVEL:
            errors.append(f"Ligne {i} : le niveau cible est un nombre entier entre 1 et {MAX_LEVEL}.")
        else:
            seen.add(building)
            queue.append({"building": building, "level": level})
    if errors:
        raise ConstructionError(errors)
    return {"planets": planets, "queue": queue}


class ConstructionSettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULTS)
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict):
                    self._data = validate(saved)  # planètes inconnues : acceptées au chargement
            except (OSError, ValueError, ConstructionError):
                self._data = copy.deepcopy(DEFAULTS)  # fichier illisible ou refusé : liste vide

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data: dict, known_planets: Callable[[], list[str]] | None = None) -> dict:
        self._data = validate(data, known_planets() if known_planets else None)
        self._save()
        return self.get()

    def drop(self, finished: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """Retire les lignes (id, niveau cible) atteintes ; une ligne modifiée entre-temps reste. Retourne celles retirées."""
        wanted = set(finished)
        gone = [(ln["building"], ln["level"]) for ln in self._data["queue"] if (ln["building"], ln["level"]) in wanted]
        if gone:
            self._data = {**self._data, "queue": [ln for ln in self._data["queue"] if (ln["building"], ln["level"]) not in wanted]}
            self._save()
        return gone

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".constructions-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire


def describe(settings: dict) -> str:
    """Phrase de résumé pour le journal."""
    labels = {b.id: b.label for b in BUILDINGS}
    if not settings["queue"]:
        return "aucune ligne de construction réglée"
    lines = ", ".join(f"{labels[line['building']]} niveau {line['level']}" for line in settings["queue"])
    return f"{len(settings['queue'])} ligne(s) sur {where(settings['planets'])} : {lines}"
