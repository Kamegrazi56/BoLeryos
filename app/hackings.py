"""Piratage : réglages (planète de départ, délai avant relance), relevé du jour (Errants les plus proches) et suivi de Naël.

Relevé sur le vrai jeu (2026-11-07, piratage Egide → Kairo-Vigie-228 6, M58 60 94) :
  - Opérations › Piliers › Piratage (`operations?method=PILLARS&mission=HACKING`) : champ « Cible », carte « Naël Soryn · Niveau 11 ·
    Disponible », durée fixe 30 min, « Quota cible du jour 0 / ∞ » contre un Errant, bouton « Lancer le piratage » ;
  - après le clic : « Initialisation numérique en cours. » puis « Vortex généré. Mission de Piratage en cours. », Naël
    « Indisponible », carte « Piratage · Arrivée : 18:29:09 · 00:29:49 · Origine … · Destination … » dans « Missions en cours ».
But choisi par le joueur : faire gagner de l'expérience à Naël en piratant en boucle l'Errant le plus proche de la planète choisie.

Fichier `<data_dir>/hackings.json` : {"settings": {...}, "daily": {...}, "state": {...}}.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

FILE_NAME = "hackings.json"
MAX_DELAY_MINUTES = 24 * 60
TARGETS_KEPT = 8  # Errants les plus proches gardés au relevé du jour (le suivant sert si le jeu refuse le premier)
DEFAULT_SETTINGS: dict = {"planet": "", "delay_min": None, "delay_max": None}
EMPTY_DAILY: dict = {"day": "", "scanned_at": None, "source": "", "error": "", "planet": "", "targets": []}
EMPTY_STATE: dict = {"target": "", "end": None, "ready": None, "refused": []}


class HackingError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def today() -> str:
    return time.strftime("%Y-%m-%d")


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate(data, known_planets: list[str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise HackingError(["Réglages de piratage illisibles."])
    errors: list[str] = []
    planet = data.get("planet", "")
    if not isinstance(planet, str):
        errors.append("Planète de départ : un nom.")
        planet = ""
    planet = planet.strip()
    if planet and known_planets and planet not in known_planets:
        errors.append(f"Planète inconnue : {planet} (relancez le scan des planètes).")
    delay_min, delay_max = data.get("delay_min"), data.get("delay_max")
    if planet or delay_min is not None or delay_max is not None:
        if not _int_in(delay_min, 0, MAX_DELAY_MINUTES) or not _int_in(delay_max, 0, MAX_DELAY_MINUTES):
            errors.append(f"Délai avant relance : choisissez un minimum et un maximum (0 à {MAX_DELAY_MINUTES} minutes).")
        elif delay_min > delay_max:
            errors.append("Délai avant relance : le minimum dépasse le maximum.")
    if errors:
        raise HackingError(errors)
    return {"planet": planet, "delay_min": delay_min, "delay_max": delay_max}


class HackingStore:
    """Réglages, relevé du jour et suivi du piratage en cours, gardés dans `hackings.json`."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._daily = copy.deepcopy(EMPTY_DAILY)
        self._state = copy.deepcopy(EMPTY_STATE)
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        try:
            self._settings = validate(saved.get("settings") or {})
        except HackingError:
            pass
        if isinstance(saved.get("daily"), dict) and isinstance(saved["daily"].get("targets"), list):
            self._daily = {**copy.deepcopy(EMPTY_DAILY), **saved["daily"]}
        if isinstance(saved.get("state"), dict):
            self._state = {**copy.deepcopy(EMPTY_STATE), **saved["state"]}

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._settings)

    def replace(self, data, known_planets: list[str] | None = None) -> dict:
        settings = validate(data, known_planets)
        if settings["planet"] != self._settings["planet"]:  # autre planète : autres Errants, relevé à refaire
            self._daily = copy.deepcopy(EMPTY_DAILY)
            self._state = {**self._state, "target": "", "refused": []}
        self._settings = settings
        self._save()
        return self.get()

    def daily(self) -> dict:
        return copy.deepcopy(self._daily)

    def daily_due(self) -> bool:
        return self._daily["day"] != today() or self._daily["planet"] != self._settings["planet"]

    def set_daily(self, targets: list[dict], source: str, error: str = "") -> dict:
        self._daily = {"day": today(), "scanned_at": time.time(), "source": source, "error": error,
                       "planet": self._settings["planet"], "targets": copy.deepcopy(targets)}
        self._state = {**self._state, "refused": []}  # nouveau relevé : on retente tous les Errants
        self._save()
        return self.daily()

    def set_daily_error(self, error: str) -> None:
        self._daily = {**self._daily, "error": error}
        self._save()

    def state(self) -> dict:
        return copy.deepcopy(self._state)

    def set_state(self, **values) -> dict:
        self._state = {**self._state, **copy.deepcopy(values)}
        self._save()
        return self.state()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".hackings-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"settings": self._settings, "daily": self._daily, "state": self._state}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
