"""Réglages du recrutement de population : catalogue des 10 populations recrutables et liste de lignes.

Chaque ligne (l'ordre de la liste est la priorité) dit, pour UNE population :
  - « fixed »  : recruter N unités ; « max » : cliquer sur MAX. Avec `repeat` vrai, à chaque passage de la fonctionnalité
    (en boucle) ; avec `repeat` faux, une seule fois (par planète) ;
  - « target » : atteindre un effectif de N unités, planète par planète (jamais répété).
Une ligne qui ne se répète pas est retirée de la liste par le bot dès qu'elle est faite sur toutes ses planètes.
et les planètes concernées (liste vide = toutes les planètes, chacune étant traitée indépendamment). L'ancien format
(`planet` : une seule planète, « » pour toutes) est encore lu. Une ligne par couple (population, planètes).
Réglages enregistrés dans `<data_dir>/populations.json`.
Le Soldat n'est pas listé : il est produit automatiquement par la Caserne et n'a pas de bouton de recrutement.
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

FILE_NAME = "populations.json"
MAX_LINES = 100
MAX_AMOUNT = 1_000_000_000
MAX_PLANET_LENGTH = 60
MODES = ("fixed", "max", "target")
MODE_LABELS = {"fixed": "recruter {n}", "max": "MAX", "target": "atteindre {n} (par planète)"}


@dataclass(frozen=True)
class Population:
    id: str
    label: str  # nom affiché par le jeu
    tab: str  # onglet de la page Population : « Assaut », « Logistique » ou « Administration »


# Relevé sur le jeu (page Population, /game/<univers>/staff) : 4 unités d'Assaut recrutables (le Soldat est automatique),
# 3 de Logistique, 3 d'Administration ; l'onglet Hôpital ne propose aucun recrutement.
POPULATIONS: tuple[Population, ...] = (
    Population("rempart-de-fer", "Rempart de Fer", "Assaut"),
    Population("spectre-du-rift", "Spectre du Rift", "Assaut"),
    Population("surchargeur-plasmique", "Surchargeur Plasmique", "Assaut"),
    Population("faucheur-cinetique", "Faucheur Cinétique", "Assaut"),
    Population("pilote", "Pilote", "Logistique"),
    Population("technicien", "Technicien", "Logistique"),
    Population("informaticien", "Informaticien", "Logistique"),
    Population("scientifique", "Scientifique", "Administration"),
    Population("ingenieur", "Ingénieur", "Administration"),
    Population("mineur", "Mineur", "Administration"),
)
POPULATION_IDS = {p.id for p in POPULATIONS}

DEFAULTS: dict = {"queue": []}


class PopulationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _planets_of(line: dict):
    """Planètes d'une ligne : `planets` (liste), ou l'ancien `planet` (une planète, « » pour toutes)."""
    if "planets" in line:
        return line["planets"]
    planet = line.get("planet", "")
    if not isinstance(planet, str) or len(planet) > MAX_PLANET_LENGTH:
        return None  # refusé par _clean_planets
    return [planet.strip()] if planet.strip() else []


def where(planets: list[str]) -> str:
    return ", ".join(planets) or "toutes les planètes"


def validate(data: dict, known_planets: list[str] | None = None) -> dict:
    """Réglages complets et valides, ou PopulationError avec la liste des problèmes (en français)."""
    errors: list[str] = []
    raw_queue = data.get("queue", [])
    queue: list[dict] = []
    if not isinstance(raw_queue, list) or len(raw_queue) > MAX_LINES:
        errors.append(f"Liste : {MAX_LINES} lignes au plus.")
        raw_queue = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for i, line in enumerate(raw_queue, start=1):
        if not isinstance(line, dict):
            errors.append(f"Ligne {i} : format illisible.")
            continue
        population, mode, amount = line.get("population"), line.get("mode"), line.get("amount", 0)
        if population not in POPULATION_IDS:
            errors.append(f"Ligne {i} : population inconnue.")
            continue
        if mode not in MODES:
            errors.append(f"Ligne {i} : choisissez « quantité fixe », « maximum » ou « effectif à atteindre ».")
            continue
        planets, problem = _clean_planets(_planets_of(line), known_planets)
        if planets is None:
            errors.append(f"Ligne {i} : {problem}")
            continue
        if mode == "max":
            amount = 0
        elif not _is_int(amount) or not 1 <= amount <= MAX_AMOUNT:
            errors.append(f"Ligne {i} : la quantité est un nombre entier entre 1 et {MAX_AMOUNT:,}.".replace(",", " "))
            continue
        repeat = line.get("repeat", True)
        if not isinstance(repeat, bool):
            errors.append(f"Ligne {i} : « à chaque passage » ou « une seule fois » attendu.")
            continue
        key = (population, tuple(sorted(planets)))
        if key in seen:
            errors.append(f"Ligne {i} : cette population est déjà dans la liste pour {where(planets)}.")
            continue
        seen.add(key)
        queue.append({"population": population, "mode": mode, "amount": amount, "planets": planets, "repeat": repeat and mode != "target"})
    if errors:
        raise PopulationError(errors)
    return {"queue": queue}


class PopulationSettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULTS)
        self._done: dict[str, list[str]] = {}  # lignes « une seule fois » déjà faites : clé de ligne → planètes
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict):
                    self._data = validate(saved)  # planète inconnue : acceptée au chargement
                    self._done = self._prune(saved.get("done"))
            except (OSError, ValueError, PopulationError):
                self._data = copy.deepcopy(DEFAULTS)  # fichier illisible ou refusé : liste vide
                self._done = {}

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data: dict, known_planets: Callable[[], list[str]] | None = None) -> dict:
        self._data = validate(data, known_planets() if known_planets else None)
        self._done = self._prune(self._done)  # une ligne modifiée ou recréée repart de zéro
        self._save()
        return self.get()

    @staticmethod
    def line_key(line: dict) -> str:
        return f"{line['population']}|{','.join(sorted(line['planets']))}|{line['mode']}|{line['amount']}"

    def _prune(self, done) -> dict[str, list[str]]:
        """Ne garde que le « fait » des lignes « une seule fois » encore présentes, inchangées."""
        keys = {self.line_key(ln) for ln in self._data["queue"] if not ln["repeat"]}
        if not isinstance(done, dict):
            return {}
        return {k: [p for p in v if isinstance(p, str)] for k, v in done.items() if k in keys and isinstance(v, list)}

    def drop_done(self, planets: list[str]) -> list[dict]:
        """Retire les lignes qui ne se répètent pas (« une seule fois », « effectif à atteindre ») faites sur toutes leurs planètes."""
        if not planets:
            return []
        gone = [
            ln
            for ln in self._data["queue"]
            if not ln["repeat"] and all(self.is_done(ln, p) for p in (ln["planets"] or planets))
        ]
        if gone:
            keys = {self.line_key(ln) for ln in gone}
            self._data = {**self._data, "queue": [ln for ln in self._data["queue"] if self.line_key(ln) not in keys]}
            self._done = self._prune(self._done)
            self._save()
        return gone

    def is_done(self, line: dict, planet: str) -> bool:
        return not line["repeat"] and planet in self._done.get(self.line_key(line), [])

    def mark_done(self, line: dict, planet: str) -> None:
        if line["repeat"] or self.is_done(line, planet):
            return
        self._done.setdefault(self.line_key(line), []).append(planet)
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".populations-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({**self._data, "done": self._done}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire


def describe(settings: dict) -> str:
    """Phrase de résumé pour le journal."""
    labels = {p.id: p.label for p in POPULATIONS}
    if not settings["queue"]:
        return "aucune ligne de population réglée"
    parts = []
    for line in settings["queue"]:
        what = MODE_LABELS[line["mode"]].format(n=line["amount"])
        if line["mode"] != "target":
            what += " à chaque passage" if line["repeat"] else " une seule fois"
        parts.append(f"{labels[line['population']]} : {what} sur {where(line['planets'])}")
    return f"{len(parts)} ligne(s) : " + " ; ".join(parts)
