"""Forages de planètes minières : réglages et suivi des forages en cours.

Réglages (`<data_dir>/minings.json`, clé "settings") : planètes de départ, ressources visées (fer, cristal, éther), nombre de
Mineurs par forage (1 Foreuse orbitale + 1 à 200 000 Mineurs), nombre de places (forages en même temps), délai avant relance
(minimum et maximum en minutes, choisis par le joueur).
Suivi (clé "tracked") : un forage par place — planète de départ, planète minière, ressource, phase (aller, extraction,
retour, terminé), fin (retour réel) et moment où la place est de nouveau libre (fin + délai tiré au hasard).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

FILE_NAME = "minings.json"
RESOURCES = {"fer": "Fer", "cristal": "Cristal", "ether": "Éther"}  # valeurs de `miningResourceType` sur la carte
MAX_MINERS = 200_000
MAX_SLOTS = 20
MAX_DELAY_MINUTES = 24 * 60
DEFAULT_SETTINGS: dict = {"planets": [], "resources": [], "miners": MAX_MINERS, "slots": 1, "delay_min": None, "delay_max": None}


class MiningError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate(data, known_planets: list[str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise MiningError(["Réglages de forage illisibles."])
    errors: list[str] = []
    planets = data.get("planets", [])
    if not isinstance(planets, list) or any(not isinstance(p, str) or not p.strip() for p in planets):
        errors.append("Planètes de départ : une liste de noms.")
        planets = []
    planets = list(dict.fromkeys(p.strip() for p in planets))
    unknown = [p for p in planets if known_planets and p not in known_planets]
    if unknown:
        errors.append(f"Planète inconnue : {unknown[0]} (relancez le scan des planètes).")
    resources = data.get("resources", [])
    if not isinstance(resources, list) or any(r not in RESOURCES for r in resources):
        errors.append("Ressources : Fer, Cristal et/ou Éther.")
        resources = []
    resources = [r for r in RESOURCES if r in resources]
    miners = data.get("miners", MAX_MINERS)
    if not _int_in(miners, 1, MAX_MINERS):
        errors.append(f"Mineurs : de 1 à {MAX_MINERS} par forage.")
    slots = data.get("slots", 1)
    if not _int_in(slots, 1, MAX_SLOTS):
        errors.append(f"Places : de 1 à {MAX_SLOTS} forages en même temps.")
    delay_min, delay_max = data.get("delay_min"), data.get("delay_max")
    if planets or resources or delay_min is not None or delay_max is not None:
        if not planets:
            errors.append("Choisissez au moins une planète de départ.")
        if not resources:
            errors.append("Choisissez au moins une ressource à forer.")
        if not _int_in(delay_min, 0, MAX_DELAY_MINUTES) or not _int_in(delay_max, 0, MAX_DELAY_MINUTES):
            errors.append(f"Délai avant relance : choisissez un minimum et un maximum (0 à {MAX_DELAY_MINUTES} minutes).")
        elif delay_min > delay_max:
            errors.append("Délai avant relance : le minimum dépasse le maximum.")
    if errors:
        raise MiningError(errors)
    return {"planets": planets, "resources": resources, "miners": miners, "slots": slots, "delay_min": delay_min, "delay_max": delay_max}


class MiningStore:
    """Réglages et forages suivis, gardés dans `minings.json` (en mémoire seulement sans dossier de données)."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._tracked: list[dict] = []
        if self._path is not None:
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
            except (OSError, ValueError):
                return
            if isinstance(saved, dict):
                try:
                    self._settings = validate(saved.get("settings") or {})
                except MiningError:
                    pass
                tracked = saved.get("tracked")
                if isinstance(tracked, list):
                    self._tracked = [t for t in tracked if isinstance(t, dict) and {"planet", "site", "phase"} <= t.keys()]

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._settings)

    def replace(self, data, known_planets: list[str] | None = None) -> dict:
        self._settings = validate(data, known_planets)
        self._save()
        return self.get()

    def tracked(self) -> list[dict]:
        return copy.deepcopy(self._tracked)

    def set_tracked(self, tracked: list[dict]) -> None:
        self._tracked = copy.deepcopy(tracked)
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".minings-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"settings": self._settings, "tracked": self._tracked}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass
