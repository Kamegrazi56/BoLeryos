"""Fouilles : réglages (lignes planète / équipe / durée / places / Mira Venn, délai avant relance), relevé du jour et suivi.

Relevé sur le vrai jeu (2026-11-07) :
  - Opérations › Piliers › Fouille (`operations?method=PILLARS&mission=EXCAVATION`) : champ « Cible » (emplacement libre),
    héros (« Lancer sans héros » ou « Mira Venn »), une quantité par unité (1 à 5 000 unités au total), durée 3 h / 6 h / 12 h /
    24 h, compteurs « Fouilles actives » et « Missions actives », bouton « Lancer la fouille » ; une Fouille ne se rappelle pas ;
  - une seule Fouille active par joueur et par site ; les sites sont les emplacements libres de la Galaxie ;
  - les Fouilles en cours sont listées dans « Missions en cours » (origine, site, heure de fin, compte à rebours).

Fichier `<data_dir>/excavations.json` : {"settings": {...}, "daily": {...}, "tracked": [...]}.
`tracked` : les Fouilles suivies par le bot (planète, durée, site, fin, moment où la place est de nouveau libre = fin + délai tiré
au hasard entre le minimum et le maximum choisis par le joueur).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

FILE_NAME = "excavations.json"
UNITS = ("Soldat", "Rempart de Fer", "Spectre du Rift", "Surchargeur Plasmique", "Faucheur Cinétique", "Scientifique", "Mineur")
DURATIONS = (3, 6, 12, 24)  # heures
MAX_TEAM = 5000
MAX_SLOTS = 10
MAX_LINES = 20
MAX_DELAY_MINUTES = 24 * 60
SITES_KEPT = 10  # sites libres les plus proches gardés par planète au relevé du jour
DEFAULT_SETTINGS: dict = {"lines": [], "delay_min": None, "delay_max": None}
EMPTY_DAILY: dict = {"day": "", "scanned_at": None, "source": "", "error": "", "max_active": None, "sites": {}}


class ExcavationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def today() -> str:
    return time.strftime("%Y-%m-%d")


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _line(raw, n: int, known_planets: list[str] | None, errors: list[str]) -> dict | None:
    where = f"Ligne {n}"
    if not isinstance(raw, dict):
        errors.append(f"{where} : illisible.")
        return None
    planet = raw.get("planet")
    if not isinstance(planet, str) or not planet.strip():
        errors.append(f"{where} : choisissez la planète de départ.")
        planet = ""
    planet = planet.strip()
    if planet and known_planets and planet not in known_planets:
        errors.append(f"{where} : planète inconnue ({planet}), relancez le scan des planètes.")
    units: dict[str, int] = {}
    raw_units = raw.get("units")
    if not isinstance(raw_units, dict):
        errors.append(f"{where} : une quantité par unité.")
    else:
        for name, qty in raw_units.items():
            if name not in UNITS:
                errors.append(f"{where} : unité non autorisée en Fouille ({name}).")
            elif not _int_in(qty, 0, MAX_TEAM):
                errors.append(f"{where} : la quantité de « {name} » doit être un nombre entier de 0 à {MAX_TEAM}.")
            elif qty > 0:
                units[name] = qty
        total = sum(units.values())
        if not 1 <= total <= MAX_TEAM:
            errors.append(f"{where} : l'équipe doit compter de 1 à {MAX_TEAM} unités (actuellement {total}).")
    duration = raw.get("duration")
    if duration not in DURATIONS or isinstance(duration, bool):
        errors.append(f"{where} : durée de 3, 6, 12 ou 24 heures.")
    slots = raw.get("slots", 1)
    if not _int_in(slots, 1, MAX_SLOTS):
        errors.append(f"{where} : nombre de places de 1 à {MAX_SLOTS}.")
    mira = raw.get("mira", False)
    if not isinstance(mira, bool):
        errors.append(f"{where} : Mira Venn oui ou non.")
    return {"planet": planet, "units": units, "duration": duration, "slots": slots, "mira": mira is True}


def validate(data, known_planets: list[str] | None = None) -> dict:
    """Réglages complets et valides, ou ExcavationError (messages en français)."""
    if not isinstance(data, dict):
        raise ExcavationError(["Réglages de fouille illisibles."])
    errors: list[str] = []
    raw_lines = data.get("lines", [])
    if not isinstance(raw_lines, list) or len(raw_lines) > MAX_LINES:
        raise ExcavationError([f"Lignes : une liste de {MAX_LINES} lignes au plus."])
    lines = [line for n, raw in enumerate(raw_lines, 1) if (line := _line(raw, n, known_planets, errors))]
    keys = [(ln["planet"], ln["duration"]) for ln in lines]
    if len(set(keys)) != len(keys):
        errors.append("Une seule ligne par planète et par durée (augmentez plutôt son nombre de places).")
    if sum(1 for ln in lines if ln["mira"]) > 1:
        errors.append("Mira Venn ne peut être affectée qu'à une seule ligne.")
    delay_min, delay_max = data.get("delay_min"), data.get("delay_max")
    if lines or delay_min is not None or delay_max is not None:
        if not _int_in(delay_min, 0, MAX_DELAY_MINUTES) or not _int_in(delay_max, 0, MAX_DELAY_MINUTES):
            errors.append(f"Délai avant relance : choisissez un minimum et un maximum (0 à {MAX_DELAY_MINUTES} minutes).")
        elif delay_min > delay_max:
            errors.append("Délai avant relance : le minimum dépasse le maximum.")
    if errors:
        raise ExcavationError(errors)
    return {"lines": lines, "delay_min": delay_min, "delay_max": delay_max}


def describe(line: dict) -> str:
    team = ", ".join(f"{q} {n}" for n, q in line["units"].items())
    return f"{line['planet']}, {line['duration']} h, {line['slots']} place(s), {team}" + (", avec Mira Venn" if line["mira"] else "")


class ExcavationStore:
    """Réglages, relevé du jour et Fouilles suivies, gardés dans `excavations.json` (en mémoire seulement sans dossier de données)."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._daily = copy.deepcopy(EMPTY_DAILY)
        self._tracked: list[dict] = []
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return  # absent ou illisible : valeurs par défaut
        if not isinstance(saved, dict):
            return
        try:
            self._settings = validate(saved.get("settings") or {})
        except ExcavationError:
            pass  # réglages refusés : rien ne sera lancé
        daily = saved.get("daily")
        if isinstance(daily, dict) and isinstance(daily.get("sites"), dict):
            self._daily = {**copy.deepcopy(EMPTY_DAILY), **daily}
        tracked = saved.get("tracked")
        if isinstance(tracked, list):
            self._tracked = [t for t in tracked if isinstance(t, dict) and {"planet", "duration", "site", "end", "ready"} <= t.keys()]

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._settings)

    def replace(self, data, known_planets: list[str] | None = None) -> dict:
        self._settings = validate(data, known_planets)
        self._save()
        return self.get()

    def daily(self) -> dict:
        return copy.deepcopy(self._daily)

    def daily_due(self) -> bool:
        return self._daily["day"] != today()

    def set_daily(self, sites: dict, max_active, source: str, error: str = "") -> dict:
        self._daily = {"day": today(), "scanned_at": time.time(), "source": source, "error": error,
                       "max_active": max_active, "sites": copy.deepcopy(sites)}
        self._save()
        return self.daily()

    def set_daily_error(self, error: str) -> None:
        self._daily = {**self._daily, "error": error}
        self._save()

    def tracked(self) -> list[dict]:
        return copy.deepcopy(self._tracked)

    def set_tracked(self, tracked: list[dict]) -> None:
        self._tracked = copy.deepcopy(tracked)
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".excavations-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"settings": self._settings, "daily": self._daily, "tracked": self._tracked}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
