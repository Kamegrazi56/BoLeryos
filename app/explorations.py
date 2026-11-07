"""Exploration : réglages (une seule configuration) et relevé du jour (brèche la plus proche de chaque planète, modèles du jeu).

Relevé sur le vrai jeu (2026-11-07) :
  - Opérations › Piliers ou Flotte › Exploration ; la cible est une « Brèche » repérée dans la Galaxie ;
  - la composition vient d'un modèle du jeu (6 par moyen, appliqué avec « Appliquer le modèle ») ou d'une quantité par unité ;
  - copies 1 à 10 (le jeu ne propose que les places actives libres), durée nominale 1 à 6 h ;
  - les compteurs « Explorations quotidiennes » et « Explorations actives » sont communs à tout le compte.

Fichier `<data_dir>/explorations.json` : {"settings": {...}, "daily": {...}}. Le relevé du jour (brèches + modèles) est refait
une fois par jour par le bot, ou à la demande.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

FILE_NAME = "explorations.json"
METHODS = {"PILLARS": "Piliers", "FLEET": "Flotte"}
# Seules ces unités partent en exploration par les Piliers (wiki du jeu) ; en Flotte, ce sont les modèles de vaisseaux.
PILLAR_UNITS = ("Soldat", "Rempart de Fer", "Spectre du Rift", "Surchargeur Plasmique", "Faucheur Cinétique", "Grand Transporteur", "Petit Transporteur")
MAX_COPIES = 10
MAX_DURATION_HOURS = 6
MAX_UNITS = 10_000_000_000
MAX_PER_PASS = 50
DEFAULT_SETTINGS: dict = {
    "method": "PILLARS",  # PILLARS (Piliers) ou FLEET (Flotte)
    "composition": "model",  # model : un modèle du jeu ; manual : une quantité par unité
    "model": "",
    "units": {},
    "copies": 1,
    "duration": 1,  # heures
    "planets": [],  # [] = toutes les planètes
    "rhythm": "fixed",  # fixed : `per_pass` lancements par planète et par passage ; max : autant que possible
    "per_pass": 1,
}
EMPTY_DAILY: dict = {"day": "", "scanned_at": None, "source": "", "error": "", "breaches": {}, "models": {"PILLARS": [], "FLEET": []}}


class ExplorationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def today() -> str:
    return time.strftime("%Y-%m-%d")


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate(data, known_planets: list[str] | None = None, known_models: dict | None = None) -> dict:
    """Réglages complets et valides, ou ExplorationError (messages en français)."""
    if not isinstance(data, dict):
        raise ExplorationError(["Réglages d'exploration illisibles."])
    out = {**copy.deepcopy(DEFAULT_SETTINGS), **{k: data[k] for k in DEFAULT_SETTINGS if k in data}}
    errors: list[str] = []
    if out["method"] not in METHODS:
        errors.append("Moyen : Piliers ou Flotte.")
    if out["composition"] not in ("model", "manual"):
        errors.append("Composition : un modèle du jeu ou une quantité par unité.")
    if not isinstance(out["model"], str):
        errors.append("Modèle : un nom.")
    else:
        out["model"] = out["model"].strip()
    units = out["units"] if isinstance(out["units"], dict) else None
    if units is None:
        errors.append("Unités : une quantité par unité.")
    else:
        cleaned = {}
        for name, qty in units.items():
            if not isinstance(name, str) or not name.strip():
                errors.append("Unités : nom manquant.")
            elif not _int_in(qty, 0, MAX_UNITS):
                errors.append(f"Unités : la quantité de « {name.strip()} » doit être un nombre entier positif.")
            elif qty > 0:
                cleaned[name.strip()] = qty
        out["units"] = cleaned
    if not _int_in(out["copies"], 1, MAX_COPIES):
        errors.append(f"Copies : de 1 à {MAX_COPIES}.")
    if not _int_in(out["duration"], 1, MAX_DURATION_HOURS):
        errors.append(f"Durée : de 1 à {MAX_DURATION_HOURS} heures.")
    if out["rhythm"] not in ("fixed", "max"):
        errors.append("Rythme : un nombre fixe par passage ou autant que possible.")
    if not _int_in(out["per_pass"], 1, MAX_PER_PASS):
        errors.append(f"Lancements par planète et par passage : de 1 à {MAX_PER_PASS}.")
    planets = out["planets"]
    if not isinstance(planets, list) or any(not isinstance(p, str) or not p.strip() for p in planets):
        errors.append("Planètes : toutes, ou une liste de noms.")
    else:
        names: list[str] = []
        for p in planets:
            if p.strip() not in names:
                names.append(p.strip())
        out["planets"] = names
        unknown = [p for p in names if known_planets and p not in known_planets]
        if unknown:
            errors.append(f"Planète inconnue : {unknown[0]} (relancez le scan des planètes).")
    if not errors and out["composition"] == "model":
        if not out["model"]:
            errors.append("Choisissez un modèle du jeu (ou passez en quantité par unité).")
        elif known_models and known_models.get(out["method"]) and out["model"] not in known_models[out["method"]]:
            errors.append(f"Modèle inconnu pour {METHODS[out['method']]} : {out['model']} (relancez le relevé du jour).")
    if not errors and out["composition"] == "manual":
        if not out["units"]:
            errors.append("Indiquez au moins une unité avec une quantité.")
        elif out["method"] == "PILLARS":
            wrong = [n for n in out["units"] if n not in PILLAR_UNITS]
            if wrong:
                errors.append(f"Unité non autorisée en exploration par les Piliers : {wrong[0]}.")
    if errors:
        raise ExplorationError(errors)
    return out


def describe(settings: dict) -> str:
    """Phrase lisible pour le journal et les réglages."""
    what = f"modèle « {settings['model']} »" if settings["composition"] == "model" else ", ".join(f"{q} × {n}" for n, q in settings["units"].items())
    where = ", ".join(settings["planets"]) or "toutes les planètes"
    rhythm = "autant que possible" if settings["rhythm"] == "max" else f"{settings['per_pass']} par planète et par passage"
    return f"{METHODS[settings['method']]}, {what}, {settings['copies']} copie(s), {settings['duration']} h, {where}, {rhythm}"


class ExplorationStore:
    """Réglages d'exploration et relevé du jour, gardés dans `explorations.json` (en mémoire seulement sans dossier de données)."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._settings = copy.deepcopy(DEFAULT_SETTINGS)
        self._daily = copy.deepcopy(EMPTY_DAILY)
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
        except ExplorationError:
            pass  # réglages refusés : valeurs par défaut (la fonctionnalité ne lancera rien sans modèle)
        daily = saved.get("daily")
        if isinstance(daily, dict) and isinstance(daily.get("breaches"), dict) and isinstance(daily.get("models"), dict):
            self._daily = {**copy.deepcopy(EMPTY_DAILY), **daily}

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._settings)

    def replace(self, data, known_planets: list[str] | None = None) -> dict:
        self._settings = validate(data, known_planets, self.known_models())
        self._save()
        return self.get()

    def daily(self) -> dict:
        return copy.deepcopy(self._daily)

    def daily_due(self) -> bool:
        return self._daily["day"] != today()

    def known_models(self) -> dict:
        return {m: [x["name"] for x in self._daily["models"].get(m, [])] for m in METHODS}

    def set_daily(self, breaches: dict, models: dict, source: str, error: str = "") -> dict:
        self._daily = {"day": today(), "scanned_at": time.time(), "source": source, "error": error,
                       "breaches": copy.deepcopy(breaches), "models": {m: list(models.get(m, [])) for m in METHODS}}
        self._save()
        return self.daily()

    def set_daily_error(self, error: str) -> None:
        self._daily = {**self._daily, "error": error}
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".explorations-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"settings": self._settings, "daily": self._daily}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
