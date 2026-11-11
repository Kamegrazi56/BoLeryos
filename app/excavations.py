"""Fouilles : réglages (planètes de départ, Fouilles simultanées, durée et équipe de chaque Fouille, Mira Venn, délai avant
relance), relevé du jour et suivi.

Relevé sur le vrai jeu (2026-11-07) :
  - Opérations › Piliers › Fouille (`operations?method=PILLARS&mission=EXCAVATION`) : champ « Cible » (emplacement libre),
    héros (« Lancer sans héros » ou « Mira Venn »), une quantité par unité (1 à 5 000 unités au total), durée 3 h / 6 h / 12 h /
    24 h, compteurs « Fouilles actives » et « Missions actives », bouton « Lancer la fouille » ; une Fouille ne se rappelle pas ;
  - une seule Fouille active par joueur et par site ; les sites sont les emplacements libres de la Galaxie ;
  - les Fouilles en cours sont listées dans « Missions en cours » (origine, site, heure de fin, compte à rebours).

Réglages :
  - l'envoi commun : durée et équipe (une quantité par unité, 1 à 5 000 unités) ;
  - « toutes les planètes » (`planets` vide) avec `copies` Fouilles simultanées chacune, ou les planètes choisies, chacune avec
    son nombre de Fouilles simultanées ;
  - `per_slot` : chaque Fouille (1, 2, …) a sa propre durée et sa propre équipe (`fouilles` de la planète, ou `fouilles` commun
    avec « toutes les planètes »). Décoché, l'envoi commun vaut pour toutes, mais les réglages par Fouille restent en mémoire ;
  - `mira` : la planète dont la Fouille 1 part avec Mira Venn (« » : aucune) ;
  - le délai avant relance (minutes, tiré au hasard entre le minimum et le maximum).
L'ancien format (`lines` : planète, équipe, durée, places, Mira) est encore lu et converti.

Fichier `<data_dir>/excavations.json` : {"settings": {...}, "daily": {...}, "tracked": [...]}.
`tracked` : les Fouilles suivies par le bot (planète, numéro de la Fouille `slot`, durée, site, fin, moment où la place est de
nouveau libre = fin + délai tiré au hasard entre le minimum et le maximum choisis par le joueur).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

from .planning import local_date

FILE_NAME = "excavations.json"
# Le jeu accepte aussi Rempart de Fer, Spectre du Rift, Surchargeur Plasmique et Faucheur Cinétique : retirés à la demande du
# joueur, qui ne les envoie jamais en Fouille.
UNITS = ("Soldat", "Scientifique", "Mineur")
DURATIONS = (3, 6, 12, 24)  # heures
MAX_TEAM = 5000
MAX_SLOTS = 10  # Fouilles simultanées par planète
MAX_PLANETS = 50
MAX_DELAY_MINUTES = 24 * 60
SITES_KEPT = 10  # sites libres les plus proches gardés par planète au relevé du jour
DEFAULT_SETTINGS: dict = {
    "duration": DURATIONS[0], "units": {},  # envoi commun
    "copies": 1,  # Fouilles simultanées par planète avec « toutes les planètes »
    "fouilles": [],  # avec « toutes les planètes » et `per_slot` : [{"duration", "units"}] (Fouille 1, 2, …)
    "planets": [],  # [] = toutes les planètes ; sinon [{"name", "copies", "fouilles": [{"duration", "units"}]}]
    "per_slot": False,
    "mira": "",
    "delay_min": None, "delay_max": None,
}
# `tried` : planètes dont les sites ont été relevés ce jour-là ; une planète choisie après le relevé l'est au passage suivant.
EMPTY_DAILY: dict = {"day": "", "scanned_at": None, "source": "", "error": "", "max_active": None, "sites": {}, "tried": []}


class ExcavationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def today() -> str:
    return local_date()  # date française, pas celle du conteneur


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _send(raw, where: str, errors: list[str]) -> dict:
    """Durée + équipe d'une Fouille (commune ou propre), nettoyées ; l'équipe complète est vérifiée à part (`_team_problem`)."""
    if not isinstance(raw, dict):
        errors.append(f"{where} : illisible.")
        return {"duration": DURATIONS[0], "units": {}}
    duration = raw.get("duration", DURATIONS[0])
    if duration not in DURATIONS or isinstance(duration, bool):
        errors.append(f"{where} : durée de 3, 6, 12 ou 24 heures.")
        duration = DURATIONS[0]
    units: dict[str, int] = {}
    raw_units = raw.get("units", {})
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
    return {"duration": duration, "units": units}


def _team_problem(units: dict) -> str:
    total = sum(units.values())
    return "" if 1 <= total <= MAX_TEAM else f"l'équipe doit compter de 1 à {MAX_TEAM} unités (actuellement {total})"


def _sends(raw, where: str, errors: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_SLOTS:
        errors.append(f"{where} : {MAX_SLOTS} Fouilles au plus.")
        return []
    return [_send(f, f"{where}, Fouille {k}", errors) for k, f in enumerate(raw, 1)]


def _from_lines(data: dict) -> dict:
    """Ancien format (`lines`) → nouveau : les lignes d'une planète deviennent ses Fouilles (Mira avec la Fouille 1)."""
    lines = [ln for ln in data.get("lines") or [] if isinstance(ln, dict)]
    planets: dict[str, dict] = {}
    mira = ""
    for ln in sorted(lines, key=lambda x: not x.get("mira")):  # la ligne de Mira en tête de sa planète
        name = ln.get("planet") if isinstance(ln.get("planet"), str) else ""
        slots = ln.get("slots", 1) if _int_in(ln.get("slots", 1), 1, MAX_SLOTS) else 1
        entry = planets.setdefault(name, {"name": name, "copies": 0, "fouilles": []})
        entry["fouilles"] += [{"duration": ln.get("duration"), "units": ln.get("units", {})}] * slots
        entry["copies"] = min(len(entry["fouilles"]), MAX_SLOTS)
        if ln.get("mira") is True and not mira:
            mira = name
    first = lines[0] if lines else {}
    return {"duration": first.get("duration", DURATIONS[0]), "units": first.get("units", {}), "copies": 1, "fouilles": [],
            "planets": [{**p, "fouilles": p["fouilles"][:MAX_SLOTS]} for p in planets.values()], "per_slot": bool(lines),
            "mira": mira, "delay_min": data.get("delay_min"), "delay_max": data.get("delay_max")}


def validate(data, known_planets: list[str] | None = None, max_active: int | None = None) -> dict:
    """Réglages complets et valides, ou ExcavationError (messages en français).
    `max_active` : Fouilles actives au plus pour le compte (relevé du jour) ; le total des Fouilles simultanées ne peut pas
    le dépasser (avec « toutes les planètes » : Fouilles par planète × planètes connues)."""
    if not isinstance(data, dict):
        raise ExcavationError(["Réglages de fouille illisibles."])
    if "lines" in data and "planets" not in data:
        data = _from_lines(data)
    errors: list[str] = []
    common = _send({"duration": data.get("duration", DURATIONS[0]), "units": data.get("units", {})}, "Envoi", errors)
    copies = data.get("copies", 1)
    if not _int_in(copies, 1, MAX_SLOTS):
        errors.append(f"Fouilles simultanées : de 1 à {MAX_SLOTS}.")
        copies = 1
    per_slot = data.get("per_slot", False)
    if not isinstance(per_slot, bool):
        errors.append("Réglages différents par Fouille : oui ou non.")
        per_slot = False
    fouilles = _sends(data.get("fouilles", []), "Toutes les planètes", errors)
    planets: list[dict] = []
    raw_planets = data.get("planets", [])
    if not isinstance(raw_planets, list) or len(raw_planets) > MAX_PLANETS:
        errors.append("Planètes : toutes, ou une liste de planètes.")
        raw_planets = []
    for raw in raw_planets:
        name = raw.get("name") if isinstance(raw, dict) else None
        if not isinstance(name, str) or not name.strip():
            errors.append("Planètes : choisissez la planète de départ.")
            continue
        name = name.strip()
        if any(p["name"] == name for p in planets):
            continue
        if known_planets and name not in known_planets:
            errors.append(f"Planète inconnue ({name}), relancez le scan des planètes.")
            continue
        count = raw.get("copies", 1)
        if not _int_in(count, 1, MAX_SLOTS):
            errors.append(f"{name} : Fouilles simultanées de 1 à {MAX_SLOTS}.")
            continue
        planets.append({"name": name, "copies": count, "fouilles": _sends(raw.get("fouilles", []), name, errors)})
    mira = data.get("mira", "")
    if not isinstance(mira, str):
        errors.append("Mira Venn : une planète de départ, ou aucune.")
        mira = ""
    mira = mira.strip()
    if mira and planets and mira not in {p["name"] for p in planets}:
        errors.append(f"Mira Venn : {mira} n'est pas une planète de départ.")
    elif mira and not planets and known_planets and mira not in known_planets:
        errors.append(f"Mira Venn : planète inconnue ({mira}).")
    delay_min, delay_max = data.get("delay_min"), data.get("delay_max")
    if not _int_in(delay_min, 0, MAX_DELAY_MINUTES) or not _int_in(delay_max, 0, MAX_DELAY_MINUTES):
        errors.append(f"Délai avant relance : choisissez un minimum et un maximum (0 à {MAX_DELAY_MINUTES} minutes).")
    elif delay_min > delay_max:
        errors.append("Délai avant relance : le minimum dépasse le maximum.")
    out = {**common, "copies": copies, "fouilles": fouilles, "planets": planets, "per_slot": per_slot, "mira": mira,
           "delay_min": delay_min, "delay_max": delay_max}
    if max_active and not errors:
        wanted = sum(p["copies"] for p in planets) if planets else copies * len(known_planets or [])
        if wanted > max_active:
            errors.append(f"Fouilles simultanées : {wanted} au total, le jeu en permet {max_active} à la fois.")
    if not errors:  # chaque Fouille qui partira doit avoir une équipe complète
        for slot in slots(out, [] if planets else ["toutes les planètes"]):
            problem = _team_problem(slot["units"])
            if problem:
                where = "Envoi" if slot["common"] else f"{slot['planet']}, Fouille {slot['index'] + 1}"
                if f"{where} : {problem}." not in errors:
                    errors.append(f"{where} : {problem}.")
    if errors:
        raise ExcavationError(errors)
    return out


def slots(settings: dict, all_planets: list[str]) -> list[dict]:
    """Fouilles réglées, planète par planète : {"planet", "index", "duration", "units", "mira", "common"}.
    `common` : la Fouille prend l'envoi commun (option décochée, ou Fouille sans réglage propre)."""
    common = {"duration": settings["duration"], "units": settings["units"]}
    entries = settings["planets"] or [{"name": p, "copies": settings["copies"], "fouilles": settings["fouilles"]} for p in all_planets]
    out = []
    for entry in entries:
        for k in range(entry["copies"]):
            own = entry["fouilles"][k] if settings["per_slot"] and k < len(entry["fouilles"]) else None
            out.append({"planet": entry["name"], "index": k, **(own or common), "common": own is None,
                        "mira": k == 0 and entry["name"] == settings["mira"]})
    return out


def describe(slot: dict) -> str:
    team = ", ".join(f"{q} {n}" for n, q in slot["units"].items())
    return f"{slot['planet']}, Fouille {slot['index'] + 1}, {slot['duration']} h, {team}" + (", avec Mira Venn" if slot["mira"] else "")


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
            if not isinstance(daily.get("tried"), list):  # relevé d'avant `tried`
                self._daily["tried"] = list(daily["sites"])
        tracked = saved.get("tracked")
        if isinstance(tracked, list):
            # `slot` absent (suivi d'avant les Fouilles numérotées) : attribué au prochain passage (voir sync_tracked).
            self._tracked = [t for t in tracked if isinstance(t, dict) and {"planet", "duration", "site", "end", "ready"} <= t.keys()]

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._settings)

    def replace(self, data, known_planets: list[str] | None = None) -> dict:
        self._settings = validate(data, known_planets, self._daily.get("max_active"))
        self._save()
        return self.get()

    def daily(self) -> dict:
        return copy.deepcopy(self._daily)

    def daily_due(self) -> bool:
        return self._daily["day"] != today()

    def set_daily(self, sites: dict, max_active, source: str, error: str = "", tried: list[str] | None = None) -> dict:
        self._daily = {"day": today(), "scanned_at": time.time(), "source": source, "error": error,
                       "max_active": max_active, "sites": copy.deepcopy(sites), "tried": list(tried if tried is not None else sites)}
        self._save()
        return self.daily()

    def add_sites(self, sites: dict, tried: list[str], error: str = "") -> dict:
        """Complète le relevé du jour avec des planètes de départ choisies après lui."""
        errors = " ; ".join(e for e in (self._daily["error"], error) if e)
        self._daily = {**self._daily, "sites": {**self._daily["sites"], **copy.deepcopy(sites)},
                       "tried": [*self._daily["tried"], *(p for p in tried if p not in self._daily["tried"])], "error": errors}
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
