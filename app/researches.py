"""Réglages des recherches : catalogue du Laboratoire (23 recherches) et liste d'objectifs.

Le joueur compose une **liste ordonnée** de lignes « recherche + niveau cible » (l'ordre est la priorité) et choisit la
**planète chargée des recherches** : dans le jeu, une recherche est payée par une seule planète et une seule recherche
tourne à la fois. Réglages enregistrés dans `<data_dir>/researches.json`.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

FILE_NAME = "researches.json"
MAX_LINES = 50
MAX_LEVEL = 500
MAX_PLANET_LENGTH = 60


@dataclass(frozen=True)
class Research:
    id: str
    label: str  # nom affiché par le jeu
    tab: str  # onglet du Laboratoire : « Civil » ou « Militaire »


# Relevé sur le jeu (Laboratoire, /game/<univers>/tech) : 11 recherches civiles, 12 militaires.
RESEARCHES: tuple[Research, ...] = (
    Research("ordinateur-quantique", "Ordinateur Quantique", "Civil"),
    Research("technologie-energie", "Technologie Énergie", "Civil"),
    Research("efficience-etherique", "Efficience Éthérique", "Civil"),
    Research("technologie-hyperespace", "Technologie Hyperespace", "Civil"),
    Research("technologie-exploration", "Technologie Exploration", "Civil"),
    Research("forage-orbital", "Forage Orbital", "Civil"),
    Research("ingenierie-miniere", "Ingénierie Minière", "Civil"),
    Research("extraction-du-fer", "Extraction du Fer", "Civil"),
    Research("extraction-cristalline", "Extraction Cristalline", "Civil"),
    Research("raffinement-de-l-ether", "Raffinement de l’Éther", "Civil"),
    Research("infrastructures", "Infrastructures", "Civil"),
    Research("technologie-d-espionnage", "Technologie d’Espionnage", "Militaire"),
    Research("technologie-laser", "Technologie Laser", "Militaire"),
    Research("technologie-ionique", "Technologie Ionique", "Militaire"),
    Research("technologie-plasma", "Technologie Plasma", "Militaire"),
    Research("reacteur-a-combustion", "Réacteur à Combustion", "Militaire"),
    Research("reacteur-a-impulsion", "Réacteur à Impulsion", "Militaire"),
    Research("propulsion-hyperespace", "Propulsion Hyperespace", "Militaire"),
    Research("systemes-d-armement", "Systèmes d’Armement", "Militaire"),
    Research("alliages-de-coque", "Alliages de Coque", "Militaire"),
    Research("technologie-des-boucliers", "Technologie des Boucliers", "Militaire"),
    Research("virus-des-piliers", "Virus des Piliers", "Militaire"),
    Research("champ-de-force-des-piliers", "Champ de Force des Piliers", "Militaire"),
)
RESEARCH_IDS = {r.id for r in RESEARCHES}

DEFAULTS: dict = {"planet": "", "queue": []}


class ResearchError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def validate(data: dict, known_planets: list[str] | None = None) -> dict:
    """Réglages complets et valides, ou ResearchError avec la liste des problèmes (en français)."""
    errors: list[str] = []
    planet = data.get("planet", "")
    if not isinstance(planet, str) or len(planet) > MAX_PLANET_LENGTH:
        errors.append(f"Planète : texte de {MAX_PLANET_LENGTH} caractères au plus.")
        planet = ""
    planet = planet.strip()
    if planet and known_planets and planet not in known_planets:
        errors.append("Planète inconnue : lancez d'abord un scan des planètes (Réglages).")
    raw_queue = data.get("queue", [])
    queue: list[dict] = []
    if not isinstance(raw_queue, list) or len(raw_queue) > MAX_LINES:
        errors.append(f"Liste : {MAX_LINES} lignes au plus.")
        raw_queue = []
    seen: set[str] = set()
    for i, line in enumerate(raw_queue, start=1):
        research = line.get("research") if isinstance(line, dict) else None
        level = line.get("level") if isinstance(line, dict) else None
        if research not in RESEARCH_IDS:
            errors.append(f"Ligne {i} : recherche inconnue.")
        elif research in seen:
            errors.append(f"Ligne {i} : cette recherche est déjà dans la liste (une seule ligne par recherche).")
        elif isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= MAX_LEVEL:
            errors.append(f"Ligne {i} : le niveau cible est un nombre entier entre 1 et {MAX_LEVEL}.")
        else:
            seen.add(research)
            queue.append({"research": research, "level": level})
    if queue and not planet and not errors:
        errors.append("Choisissez la planète chargée des recherches.")
    if errors:
        raise ResearchError(errors)
    return {"planet": planet, "queue": queue}


class ResearchSettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULTS)
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict):
                    self._data = validate(saved)  # planète inconnue : acceptée au chargement
            except (OSError, ValueError, ResearchError):
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
        gone = [(ln["research"], ln["level"]) for ln in self._data["queue"] if (ln["research"], ln["level"]) in wanted]
        if gone:
            self._data = {**self._data, "queue": [ln for ln in self._data["queue"] if (ln["research"], ln["level"]) not in wanted]}
            self._save()
        return gone

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".researches-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire


def describe(settings: dict) -> str:
    """Phrase de résumé pour le journal."""
    labels = {r.id: r.label for r in RESEARCHES}
    if not settings["queue"]:
        return "aucune ligne de recherche réglée"
    lines = ", ".join(f"{labels[line['research']]} niveau {line['level']}" for line in settings["queue"])
    return f"{len(settings['queue'])} ligne(s) sur la planète {settings['planet']} : {lines}"
