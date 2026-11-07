"""Espionnage des Errants par drone (Piliers) : réglages et tri des rapports.

Réglages (`<data_dir>/spying.json`) : la planète autour de laquelle espionner, un minimum pour chaque ressource et le nombre
de ressources (1, 2 ou 3) qui doivent atteindre leur minimum pour que le rapport soit GARDÉ ; sinon il est supprimé dans le jeu.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

FILE_NAME = "spying.json"
RESOURCES = ("Fer", "Cristal", "Éther")
MAX_AMOUNT = 10**15
DEFAULT_SETTINGS: dict = {"planet": "", "minimums": {r: 0 for r in RESOURCES}, "required": 1}


class SpyingError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def validate(data, known_planets: list[str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise SpyingError(["Réglages d'espionnage illisibles."])
    out = copy.deepcopy(DEFAULT_SETTINGS)
    errors: list[str] = []
    planet = data.get("planet", "")
    if not isinstance(planet, str):
        errors.append("Planète : un nom.")
    else:
        out["planet"] = planet.strip()
        if out["planet"] and known_planets and out["planet"] not in known_planets:
            errors.append(f"Planète inconnue : {out['planet']} (relancez le scan des planètes).")
    minimums = data.get("minimums", {})
    if not isinstance(minimums, dict):
        errors.append("Minimums : un nombre par ressource.")
    else:
        for r in RESOURCES:
            value = minimums.get(r, 0)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_AMOUNT:
                errors.append(f"Minimum de {r} : un nombre entier positif.")
            else:
                out["minimums"][r] = value
    required = data.get("required", 1)
    if isinstance(required, bool) or required not in (1, 2, 3):
        errors.append("Nombre de ressources à atteindre : 1, 2 ou 3.")
    else:
        out["required"] = required
    if errors:
        raise SpyingError(errors)
    return out


def report_matches(resources: dict, settings: dict) -> bool:
    """Rapport gardé si au moins `required` ressources atteignent leur minimum."""
    reached = sum(1 for r in RESOURCES if (resources.get(r) or 0) >= settings["minimums"][r])
    return reached >= settings["required"]


class SpySettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        if self._path is not None:
            try:
                self._data = validate(json.loads(self._path.read_text(encoding="utf8")))
            except (OSError, ValueError, SpyingError):
                pass  # absent ou illisible : valeurs par défaut

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data, known_planets: list[str] | None = None) -> dict:
        self._data = validate(data, known_planets)
        if self._path:
            try:
                fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".spying-")
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except OSError:
                pass  # l'enregistrement est facultatif : le bot continue en mémoire
        return self.get()
