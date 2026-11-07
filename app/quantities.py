"""Listes de lignes « élément + mode + quantité + planètes » (Assemblages de vaisseaux et Composants du Chantier spatial).

Chaque ligne (l'ordre de la liste est la priorité) dit, pour UN élément :
  - « fixed »  : produire N unités ; « max » : cliquer sur MAX. Avec `repeat` vrai, à chaque passage de la fonctionnalité
    (en boucle) ; avec `repeat` faux, une seule fois (par planète) ;
  - « target » : atteindre un effectif de N unités, planète par planète (jamais répété) ;
et les planètes concernées : liste vide = toutes les planètes, sinon une ou plusieurs planètes précises.
Chaque planète est traitée indépendamment. Une ligne qui ne se répète pas est retirée de la liste par le bot dès qu'elle est
faite sur toutes ses planètes. Une ligne par couple (élément, ensemble de planètes).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAX_LINES = 100
MAX_AMOUNT = 1_000_000_000
MAX_PLANET_LENGTH = 60
MAX_PLANETS_PER_LINE = 50
MAX_ITEM_LENGTH = 80
MODES = ("fixed", "max", "target")
MODE_LABELS = {"fixed": "produire {n}", "max": "MAX", "target": "atteindre {n} (par planète)"}


@dataclass(frozen=True)
class LineSpec:
    file_name: str
    subject: str  # « modèle », « composant » : pour les messages
    ids: frozenset[str] | None = None  # éléments autorisés ; None = noms libres (modèles lus dans le jeu)


class LineError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clean_planets(raw, known_planets: list[str] | None) -> tuple[list[str] | None, str]:
    """(planètes nettoyées, message d'erreur) ; liste vide = toutes les planètes."""
    if not isinstance(raw, list) or len(raw) > MAX_PLANETS_PER_LINE:
        return None, "planètes invalides."
    cleaned: list[str] = []
    for planet in raw:
        if not isinstance(planet, str) or not planet.strip() or len(planet) > MAX_PLANET_LENGTH:
            return None, "planète invalide."
        planet = planet.strip()
        if known_planets and planet not in known_planets:
            return None, f"planète inconnue ({planet}) : lancez d'abord un scan des planètes."
        if planet not in cleaned:
            cleaned.append(planet)
    return cleaned, ""


def validate(spec: LineSpec, data: dict, known_planets: list[str] | None = None, known_items: list[str] | None = None) -> dict:
    """Réglages complets et valides, ou LineError avec la liste des problèmes (en français)."""
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
        item, mode, amount = line.get("item"), line.get("mode"), line.get("amount", 0)
        if not isinstance(item, str) or not item.strip() or len(item) > MAX_ITEM_LENGTH:
            errors.append(f"Ligne {i} : {spec.subject} invalide.")
            continue
        item = item.strip()
        if spec.ids is not None and item not in spec.ids:
            errors.append(f"Ligne {i} : {spec.subject} inconnu.")
            continue
        if spec.ids is None and known_items and item not in known_items:
            errors.append(f"Ligne {i} : {spec.subject} inconnu (lancez d'abord un scan des modèles).")
            continue
        if mode not in MODES:
            errors.append(f"Ligne {i} : choisissez « quantité fixe », « maximum » ou « effectif à atteindre ».")
            continue
        planets, problem = _clean_planets(line.get("planets", []), known_planets)
        if planets is None:
            errors.append(f"Ligne {i} : {problem}")
            continue
        repeat = line.get("repeat", True)
        if not isinstance(repeat, bool):
            errors.append(f"Ligne {i} : « à chaque passage » ou « une seule fois » attendu.")
            continue
        if mode == "max":
            amount = 0
        elif not _is_int(amount) or not 1 <= amount <= MAX_AMOUNT:
            errors.append(f"Ligne {i} : la quantité est un nombre entier entre 1 et {MAX_AMOUNT:,}.".replace(",", " "))
            continue
        key = (item, tuple(sorted(planets)))
        if key in seen:
            errors.append(f"Ligne {i} : cet élément est déjà dans la liste pour {', '.join(planets) or 'toutes les planètes'}.")
            continue
        seen.add(key)
        queue.append({"item": item, "mode": mode, "amount": amount, "planets": planets, "repeat": repeat and mode != "target"})
    if errors:
        raise LineError(errors)
    return {"queue": queue}


def line_key(line: dict) -> str:
    return f"{line['item']}|{','.join(sorted(line['planets']))}|{line['mode']}|{line['amount']}"


def applies_to(line: dict, planet: str) -> bool:
    return not line["planets"] or planet in line["planets"]


class LineSettings:
    """Liste de lignes enregistrée dans `<data_dir>/<file>` + mémoire des lignes « une seule fois » déjà faites."""

    def __init__(self, data_dir: str | None, spec: LineSpec) -> None:
        self.spec = spec
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / spec.file_name if folder else None
        self._data: dict = {"queue": []}
        self._done: dict[str, list[str]] = {}  # clé de ligne → planètes où elle est faite
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict):
                    self._data = validate(spec, saved)  # planète ou modèle inconnu : accepté au chargement
                    self._done = self._prune(saved.get("done"))
            except (OSError, ValueError, LineError):
                self._data, self._done = {"queue": []}, {}  # fichier illisible ou refusé : liste vide

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data: dict, known_planets: Callable[[], list[str]] | None = None, known_items: Callable[[], list[str]] | None = None) -> dict:
        self._data = validate(self.spec, data, known_planets() if known_planets else None, known_items() if known_items else None)
        self._done = self._prune(self._done)  # une ligne modifiée ou recréée repart de zéro
        self._save()
        return self.get()

    def _prune(self, done) -> dict[str, list[str]]:
        keys = {line_key(ln) for ln in self._data["queue"] if not ln["repeat"]}
        if not isinstance(done, dict):
            return {}
        return {k: [p for p in v if isinstance(p, str)] for k, v in done.items() if k in keys and isinstance(v, list)}

    def is_done(self, line: dict, planet: str) -> bool:
        return not line["repeat"] and planet in self._done.get(line_key(line), [])

    def mark_done(self, line: dict, planet: str) -> None:
        if line["repeat"] or self.is_done(line, planet):
            return
        self._done.setdefault(line_key(line), []).append(planet)
        self._save()

    def drop_done(self, all_planets: list[str]) -> list[dict]:
        """Retire les lignes qui ne se répètent pas, faites sur toutes leurs planètes ; retourne celles retirées."""
        if not all_planets:
            return []
        gone = [
            ln
            for ln in self._data["queue"]
            if not ln["repeat"] and all(self.is_done(ln, p) for p in (ln["planets"] or all_planets))
        ]
        if gone:
            keys = {line_key(ln) for ln in gone}
            self._data = {**self._data, "queue": [ln for ln in self._data["queue"] if line_key(ln) not in keys]}
            self._done = self._prune(self._done)
            self._save()
        return gone

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".lines-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({**self._data, "done": self._done}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire


def describe(settings: dict, labels: dict[str, str] | None = None) -> str:
    """Phrase de résumé pour le journal."""
    if not settings["queue"]:
        return "aucune ligne réglée"
    parts = []
    for line in settings["queue"]:
        what = MODE_LABELS[line["mode"]].format(n=line["amount"])
        if line["mode"] != "target":
            what += " à chaque passage" if line["repeat"] else " une seule fois"
        where = ", ".join(line["planets"]) or "toutes les planètes"
        parts.append(f"{(labels or {}).get(line['item'], line['item'])} : {what} sur {where}")
    return f"{len(parts)} ligne(s) : " + " ; ".join(parts)
