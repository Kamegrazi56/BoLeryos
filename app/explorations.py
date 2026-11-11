"""Exploration : réglages (une seule configuration) et relevé du jour (brèche la plus proche de chaque planète, modèles du jeu).

Relevé sur le vrai jeu (2026-11-07) :
  - Opérations › Piliers ou Flotte › Exploration ; la cible est une « Brèche » repérée dans la Galaxie ;
  - la composition vient d'un modèle du jeu (6 par moyen, appliqué avec « Appliquer le modèle ») ou d'une quantité par unité ;
  - copies 1 à 10 (le jeu ne propose que les places actives libres), durée nominale 1 à 6 h ;
  - les compteurs « Explorations quotidiennes » et « Explorations actives » sont communs à tout le compte.

Réglages (une seule configuration) :
  - l'envoi commun : moyen, composition (modèle ou quantités), durée ;
  - « toutes les planètes » (`planets` vide) avec `copies` expéditions simultanées chacune, ou les planètes choisies, chacune
    avec son nombre d'expéditions simultanées (`copies`, le champ « Copies » du jeu) ;
  - `per_planet` : chaque planète choisie a son propre envoi (`send`). Décoché, l'envoi commun vaut pour toutes, mais les
    envois par planète restent en mémoire.
À chaque passage, le bot fait UN lancement par planète. L'ancien format (`planets` en liste de noms, `rhythm`, `per_pass`) est
encore lu.

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

from .planning import local_date

FILE_NAME = "explorations.json"
METHODS = {"PILLARS": "Piliers", "FLEET": "Flotte"}
# Seules ces unités partent en exploration par les Piliers (wiki du jeu) ; en Flotte, ce sont les modèles de vaisseaux.
PILLAR_UNITS = ("Soldat", "Rempart de Fer", "Spectre du Rift", "Surchargeur Plasmique", "Faucheur Cinétique", "Grand Transporteur", "Petit Transporteur")
MAX_COPIES = 10
MAX_DURATION_HOURS = 6
MAX_UNITS = 10_000_000_000
MAX_PLANETS = 200
SEND_KEYS = ("method", "composition", "model", "units", "duration")  # l'envoi : commun, ou propre à une planète
DEFAULT_SETTINGS: dict = {
    "method": "PILLARS",  # PILLARS (Piliers) ou FLEET (Flotte)
    "composition": "model",  # model : un modèle du jeu ; manual : une quantité par unité
    "model": "",
    "units": {},
    "duration": 1,  # heures
    "copies": 1,  # expéditions simultanées par planète avec « toutes les planètes »
    "planets": [],  # [] = toutes les planètes ; sinon [{"name", "copies", "send": {SEND_KEYS…} | None}]
    "per_planet": False,  # vrai : chaque planète choisie utilise son propre envoi
}
# `tried` : planètes dont la brèche a été cherchée ce jour-là (trouvée ou non) ; une planète choisie après le relevé est
# cherchée au passage suivant, une seule fois par jour.
EMPTY_DAILY: dict = {"day": "", "scanned_at": None, "source": "", "error": "", "breaches": {}, "models": {"PILLARS": [], "FLEET": []}, "tried": []}


class ExplorationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def today() -> str:
    return local_date()  # date française, pas celle du conteneur


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _clean_send(raw, prefix: str, errors: list[str], known_models: dict | None, complete: bool) -> dict:
    """Envoi (moyen, composition, modèle, unités, durée) nettoyé ; les problèmes vont dans `errors`, précédés de `prefix`.
    `complete` : vérifie aussi qu'il est utilisable (modèle choisi et connu, au moins une unité autorisée)."""
    if not isinstance(raw, dict):
        errors.append(f"{prefix}envoi illisible.")
        return {k: copy.deepcopy(DEFAULT_SETTINGS[k]) for k in SEND_KEYS}
    out = {k: copy.deepcopy(raw.get(k, DEFAULT_SETTINGS[k])) for k in SEND_KEYS}
    before = len(errors)
    if out["method"] not in METHODS:
        errors.append(f"{prefix}moyen : Piliers ou Flotte.")
    if out["composition"] not in ("model", "manual"):
        errors.append(f"{prefix}composition : un modèle du jeu ou une quantité par unité.")
    if not isinstance(out["model"], str):
        errors.append(f"{prefix}modèle : un nom.")
        out["model"] = ""
    out["model"] = out["model"].strip()
    if not isinstance(out["units"], dict):
        errors.append(f"{prefix}unités : une quantité par unité.")
        out["units"] = {}
    cleaned = {}
    for name, qty in out["units"].items():
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix}unités : nom manquant.")
        elif not _int_in(qty, 0, MAX_UNITS):
            errors.append(f"{prefix}la quantité de « {name.strip()} » doit être un nombre entier positif.")
        elif qty > 0:
            cleaned[name.strip()] = qty
    out["units"] = cleaned
    if not _int_in(out["duration"], 1, MAX_DURATION_HOURS):
        errors.append(f"{prefix}durée de 1 à {MAX_DURATION_HOURS} heures.")
    if not complete or len(errors) > before:
        return out
    if out["composition"] == "model":
        if not out["model"]:
            errors.append(f"{prefix}choisissez un modèle du jeu (ou passez en quantité par unité).")
        elif known_models and known_models.get(out["method"]) and out["model"] not in known_models[out["method"]]:
            errors.append(f"{prefix}modèle inconnu pour {METHODS[out['method']]} : {out['model']} (relancez le relevé du jour).")
    elif not out["units"]:
        errors.append(f"{prefix}indiquez au moins une unité avec une quantité.")
    elif out["method"] == "PILLARS":
        wrong = [n for n in out["units"] if n not in PILLAR_UNITS]
        if wrong:
            errors.append(f"{prefix}unité non autorisée en exploration par les Piliers : {wrong[0]}.")
    return out


def _clean_planets(raw, default_copies, per_planet: bool, errors: list[str], known_planets, known_models) -> list[dict]:
    """Planètes choisies : [{"name", "copies", "send"}]. Une liste de noms (ancien format) prend `default_copies`."""
    if not isinstance(raw, list) or len(raw) > MAX_PLANETS:
        errors.append("Planètes : toutes, ou une liste de planètes.")
        return []
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"name": entry, "copies": default_copies}
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.strip():
            errors.append("Planètes : nom de planète manquant.")
            continue
        name = name.strip()
        if any(p["name"] == name for p in out):
            continue  # doublon
        if known_planets and name not in known_planets:
            errors.append(f"Planète inconnue : {name} (relancez le scan des planètes).")
            continue
        copies = entry.get("copies", 1)
        if not _int_in(copies, 1, MAX_COPIES):
            errors.append(f"{name} : expéditions simultanées de 1 à {MAX_COPIES}.")
            continue
        send = entry.get("send")
        if send is not None:
            send = _clean_send(send, f"{name} : ", errors, known_models, complete=per_planet)
        out.append({"name": name, "copies": copies, "send": send})
    return out


def validate(data, known_planets: list[str] | None = None, known_models: dict | None = None) -> dict:
    """Réglages complets et valides, ou ExplorationError (messages en français)."""
    if not isinstance(data, dict):
        raise ExplorationError(["Réglages d'exploration illisibles."])
    errors: list[str] = []
    send = _clean_send({k: data.get(k, DEFAULT_SETTINGS[k]) for k in SEND_KEYS}, "", errors, known_models, complete=False)
    copies = data.get("copies", 1)
    if not _int_in(copies, 1, MAX_COPIES):
        errors.append(f"Expéditions simultanées : de 1 à {MAX_COPIES}.")
        copies = 1
    per_planet = data.get("per_planet", False)
    if not isinstance(per_planet, bool):
        errors.append("Réglages différents par planète : oui ou non.")
        per_planet = False
    planets = _clean_planets(data.get("planets", []), copies, per_planet, errors, known_planets, known_models)
    if per_planet and not planets:
        errors.append("Réglages différents par planète : choisissez d'abord les planètes.")
    if not errors:
        # L'envoi commun doit être utilisable s'il sert à au moins une planète.
        if not per_planet or not planets or any(p["send"] is None for p in planets):
            _clean_send(send, "", errors, known_models, complete=True)
    if errors:
        raise ExplorationError([e[0].upper() + e[1:] for e in errors])
    return {**send, "copies": copies, "planets": planets, "per_planet": per_planet}


def plans(settings: dict, all_planets: list[str]) -> list[dict]:
    """Envois du passage, planète par planète : {"planet", "copies", + l'envoi (SEND_KEYS)}."""
    common = {k: settings[k] for k in SEND_KEYS}
    if not settings["planets"]:
        return [{"planet": p, "copies": settings["copies"], **common} for p in all_planets]
    return [
        {"planet": p["name"], "copies": p["copies"], **(p["send"] if settings["per_planet"] and p["send"] else common)}
        for p in settings["planets"]
    ]


def describe_send(send: dict) -> str:
    what = f"modèle « {send['model']} »" if send["composition"] == "model" else ", ".join(f"{q} × {n}" for n, q in send["units"].items())
    return f"{METHODS[send['method']]}, {what}, {send['duration']} h"


def describe(settings: dict) -> str:
    """Phrase lisible pour le journal et les réglages."""
    if not settings["planets"]:
        return f"{describe_send(settings)} ; {settings['copies']} expédition(s) simultanée(s) depuis chaque planète"
    parts = []
    for p in settings["planets"]:
        own = f" ({describe_send(p['send'])})" if settings["per_planet"] and p["send"] else ""
        parts.append(f"{p['copies']} depuis {p['name']}{own}")
    return f"{describe_send(settings)} ; expéditions simultanées : " + ", ".join(parts)


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
            if not isinstance(daily.get("tried"), list):  # relevé d'avant `tried` : ses brèches comptent comme cherchées
                self._daily["tried"] = list(daily["breaches"])

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

    def set_daily(self, breaches: dict, models: dict, source: str, error: str = "", tried: list[str] | None = None) -> dict:
        self._daily = {"day": today(), "scanned_at": time.time(), "source": source, "error": error,
                       "breaches": copy.deepcopy(breaches), "models": {m: list(models.get(m, [])) for m in METHODS},
                       "tried": list(tried if tried is not None else breaches)}
        self._save()
        return self.daily()

    def add_breaches(self, breaches: dict, tried: list[str], error: str = "") -> dict:
        """Complète le relevé du jour avec des planètes choisies après lui."""
        errors = " ; ".join(e for e in (self._daily["error"], error) if e)
        self._daily = {**self._daily, "breaches": {**self._daily["breaches"], **copy.deepcopy(breaches)},
                       "tried": [*self._daily["tried"], *(p for p in tried if p not in self._daily["tried"])], "error": errors}
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
