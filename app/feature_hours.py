"""Horaires propres à une fonctionnalité (Piratage, Exploration) : plages par jour pendant lesquelles elle peut lancer.

Même format et mêmes règles que le planning général (planning.py) : heure française, plages qui peuvent passer minuit,
jour sans plage = rien ce jour-là, décalage aléatoire des débuts et fins. Hors de ses plages, la fonctionnalité ne lance
rien de nouveau (une mission déjà partie va jusqu'au bout) et repart seule au début de la plage suivante ; les autres
fonctionnalités ne sont pas concernées. Le planning général reste le maître : une plage qui en sort est REFUSÉE.
Fichier `<data_dir>/feature_hours.json` : {"hackings": {...}, "explorations": {...}} (format de planning.py).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .planning import DAY_LABELS, DAYS, DEFAULT_SETTINGS, PlanningError, describe, state, validate as validate_planning

FILE_NAME = "feature_hours.json"
FEATURES = {"hackings": "piratage", "explorations": "exploration"}
DAY_MINUTES = 24 * 60


def _minutes(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _span(slot: dict) -> tuple[int, int]:
    """Plage en minutes depuis minuit du jour ; une plage qui passe minuit finit après 1440."""
    start, end = _minutes(slot["start"]), _minutes(slot["end"])
    return start, end if end > start else end + DAY_MINUTES


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def outside_global(settings: dict, global_settings: dict) -> list[str]:
    """Plages de la fonctionnalité qui sortent du planning général (heures de base, sans décalage). Vide si le planning
    général n'est pas utilisé (bot actif à toute heure)."""
    if not global_settings.get("enabled"):
        return []
    problems = []
    for i, day in enumerate(DAYS):
        before, after = DAYS[i - 1], DAYS[(i + 1) % 7]
        allowed = [_span(s) for s in global_settings["days"][day]]
        allowed += [(a - DAY_MINUTES, b - DAY_MINUTES) for a, b in map(_span, global_settings["days"][before]) if b > DAY_MINUTES]
        allowed += [(a + DAY_MINUTES, b + DAY_MINUTES) for a, b in map(_span, global_settings["days"][after])]
        union = _merged(allowed)
        for slot in settings["days"][day]:
            a, b = _span(slot)
            if not any(x <= a and b <= y for x, y in union):
                problems.append(f"{DAY_LABELS[day].capitalize()} : la plage {slot['start']} → {slot['end']} sort du planning du bot")
    return problems


class FeatureHoursError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def validate(data, global_settings: dict) -> dict:
    try:
        out = validate_planning(data)
    except PlanningError as exc:
        raise FeatureHoursError(exc.errors) from exc
    if out["enabled"]:
        problems = outside_global(out, global_settings)
        if problems:
            raise FeatureHoursError([p + " (le planning général reste le maître)." for p in problems])
    return out


class FeatureHoursStore:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = {fid: copy.deepcopy(DEFAULT_SETTINGS) for fid in FEATURES}
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                for fid in FEATURES:
                    if isinstance(saved.get(fid), dict):
                        self._data[fid] = validate_planning(saved[fid])  # le planning général a pu changer : relu tel quel
            except (OSError, ValueError, PlanningError, AttributeError):
                pass

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self, fid: str) -> dict:
        return copy.deepcopy(self._data[fid])

    def replace(self, fid: str, data, global_settings: dict) -> dict:
        self._data[fid] = validate(data, global_settings)
        self._save()
        return self.get(fid)

    def pause_reason(self, fid: str, now: datetime | None = None) -> str | None:
        """Pourquoi la fonctionnalité ne lance rien maintenant (hors de ses horaires) ; None si elle peut lancer."""
        if fid not in FEATURES:
            return None
        st = state(self._data[fid], now)
        if st["active"]:
            return None
        name = FEATURES[fid]
        return f"hors horaires du {name} — reprise {describe(st['next'], now)}" if st["next"] else f"hors horaires du {name} — aucune plage prévue"

    def message(self, fid: str, now: datetime | None = None) -> str:
        settings = self._data[fid]
        if not settings["enabled"]:
            return f"Horaires non utilisés : le {FEATURES[fid]} peut lancer à toute heure (dans le planning du bot)."
        st = state(settings, now)
        if st["active"]:
            return f"Dans une plage : le {FEATURES[fid]} peut lancer jusqu'à {describe(st['until'], now)}."
        return f"Hors plage : reprise {describe(st['next'], now)}." if st["next"] else "Hors plage : aucune plage prévue."

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".feature-hours-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass
