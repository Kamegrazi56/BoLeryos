"""Planning : plages horaires actives par jour de la semaine (heure française).

Le bouton ON/OFF reste le maître : sur ON, en dehors d'une plage active, le bot ne lance aucun passage et se déconnecte du
jeu ; il se reconnecte seul au début de la plage suivante. Chaque début et chaque fin de plage est décalé au hasard de X à Y
minutes, avant ou après l'heure de base (le tirage est fixe pour une plage donnée d'un jour donné). Une plage peut passer
minuit (ex. 22:00 → 02:00). Un jour sans plage est inactif. Sans la case « Utiliser le planning », le bot est toujours actif.
L'espionnage manuel n'est pas concerné. Fichier `<data_dir>/planning.json`.
"""

from __future__ import annotations

import copy
import json
import os
import random
import re
import tempfile
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FILE_NAME = "planning.json"
TIMEZONE = ZoneInfo("Europe/Paris")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # même ordre que date.weekday()
DAY_LABELS = {"mon": "lundi", "tue": "mardi", "wed": "mercredi", "thu": "jeudi", "fri": "vendredi", "sat": "samedi", "sun": "dimanche"}
MAX_SLOTS_PER_DAY = 10
MAX_OFFSET_MINUTES = 120
LOOKAHEAD_DAYS = 8  # cherche la prochaine plage sur une semaine et un jour
HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DEFAULT_SETTINGS: dict = {"enabled": False, "days": {d: [] for d in DAYS}, "offset_min": 0, "offset_max": 0}


class PlanningError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate(data) -> dict:
    if not isinstance(data, dict):
        raise PlanningError(["Planning illisible."])
    errors: list[str] = []
    out = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(data.get("enabled", False), bool):
        errors.append("« Utiliser le planning » : oui ou non.")
    else:
        out["enabled"] = data.get("enabled", False)
    days = data.get("days", {})
    if not isinstance(days, dict):
        errors.append("Jours : une liste de plages par jour.")
        days = {}
    for day in DAYS:
        slots = days.get(day, [])
        label = DAY_LABELS[day].capitalize()
        if not isinstance(slots, list) or len(slots) > MAX_SLOTS_PER_DAY:
            errors.append(f"{label} : {MAX_SLOTS_PER_DAY} plages au plus.")
            continue
        for slot in slots:
            start, end = (slot.get("start"), slot.get("end")) if isinstance(slot, dict) else (None, None)
            if not isinstance(start, str) or not isinstance(end, str) or not HHMM.match(start) or not HHMM.match(end):
                errors.append(f"{label} : heures au format HH:MM.")
            elif start == end:
                errors.append(f"{label} : une plage doit avoir un début et une fin différents ({start}).")
            else:
                out["days"][day].append({"start": start, "end": end})
        out["days"][day].sort(key=lambda s: s["start"])
    lo, hi = data.get("offset_min", 0), data.get("offset_max", 0)
    if not _int_in(lo, 0, MAX_OFFSET_MINUTES) or not _int_in(hi, 0, MAX_OFFSET_MINUTES):
        errors.append(f"Décalage : de 0 à {MAX_OFFSET_MINUTES} minutes.")
    elif lo > hi:
        errors.append("Décalage : le minimum dépasse le maximum.")
    else:
        out["offset_min"], out["offset_max"] = lo, hi
    if errors:
        raise PlanningError(errors)
    return out


def _clock(text: str) -> dtime:
    h, m = text.split(":")
    return dtime(int(h), int(m))


def _offset(settings: dict, seed: str) -> timedelta:
    """Décalage tiré au hasard entre le minimum et le maximum, avant ou après ; toujours le même pour la même graine."""
    if settings["offset_max"] <= 0:
        return timedelta(0)
    rng = random.Random(f"{seed}|{settings['offset_min']}|{settings['offset_max']}")
    minutes = rng.uniform(settings["offset_min"], settings["offset_max"])
    return timedelta(minutes=minutes if rng.random() < 0.5 else -minutes)


def windows(settings: dict, first: date, days: int) -> list[tuple[datetime, datetime]]:
    """Plages effectives (décalages compris) qui commencent entre `first` et `first + days`, triées."""
    out = []
    for n in range(days + 1):
        day = first + timedelta(days=n)
        for i, slot in enumerate(settings["days"][DAYS[day.weekday()]]):
            start_t, end_t = _clock(slot["start"]), _clock(slot["end"])
            start = datetime.combine(day, start_t, TIMEZONE) + _offset(settings, f"{day}|{i}|début")
            end_day = day if end_t > start_t else day + timedelta(days=1)  # plage qui passe minuit
            end = datetime.combine(end_day, end_t, TIMEZONE) + _offset(settings, f"{day}|{i}|fin")
            if end > start:
                out.append((start, end))
    return sorted(out)


def state(settings: dict, now: datetime | None = None) -> dict:
    """{"active": bool, "until": fin de la plage en cours, "next": début de la prochaine plage} (heures françaises)."""
    now = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
    if not settings["enabled"]:
        return {"active": True, "until": None, "next": None}
    spans = windows(settings, now.date() - timedelta(days=1), LOOKAHEAD_DAYS)
    current = [(s, e) for s, e in spans if s <= now < e]
    if current:
        return {"active": True, "until": max(e for _, e in current), "next": None}
    upcoming = [s for s, _ in spans if s > now]
    return {"active": False, "until": None, "next": min(upcoming) if upcoming else None}


def describe(moment: datetime | None, now: datetime | None = None) -> str:
    if moment is None:
        return "aucune plage prévue"
    now = (now or datetime.now(TIMEZONE)).astimezone(TIMEZONE)
    moment = moment.astimezone(TIMEZONE)
    if moment.date() == now.date():
        day = "aujourd'hui"
    elif moment.date() == now.date() + timedelta(days=1):
        day = "demain"
    else:
        day = DAY_LABELS[DAYS[moment.weekday()]]
    return f"{day} à {moment:%H:%M}"


def pause_reason(settings: dict, now: datetime | None = None) -> str | None:
    """Pourquoi le bot (sur ON) ne doit rien faire maintenant ; None pendant une plage active ou sans planning."""
    st = state(settings, now)
    if st["active"]:
        return None
    return f"hors planning — reprise {describe(st['next'], now)}" if st["next"] else "hors planning — aucune plage prévue"


class PlanningStore:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        if self._path is not None:
            try:
                self._data = validate(json.loads(self._path.read_text(encoding="utf8")))
            except (OSError, ValueError, PlanningError):
                pass  # absent ou illisible : pas de planning (toujours actif)

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data) -> dict:
        self._data = validate(data)
        if self._path:
            try:
                fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".planning-")
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except OSError:
                pass
        return self.get()
