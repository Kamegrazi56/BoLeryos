"""Réglages de l'automatisation : validation, valeurs par défaut, enregistrement (jamais de secret ici)."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .numbers import norm
from .schedule import parse_hhmm

DEFAULTS: dict[str, Any] = {
    "mode": "dry_run",  # « dry_run » (à blanc) ou « live » (réel)
    "universe": "",  # univers où entrer automatiquement (nom affiché sur la page de choix) ; vide = demander
    "daily_cap": 20,
    "cycle": {"min_minutes": 3.0, "max_minutes": 8.0},
    "schedule": {
        "enabled": False,
        "timezone": "Europe/Paris",
        "windows": [{"start": "08:00", "end": "23:00", "days": [0, 1, 2, 3, 4, 5, 6]}],
    },
    "constructions": {"enabled": False, "targets": {}, "overrides": {}, "planets": []},
    "research": {"enabled": False, "targets": {}, "funding_planet": ""},
    "catalog": {"buildings": [], "research": [], "planets": [], "universes": []},
}


class SettingsError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def _targets(raw: Any, where: str, errors: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(raw, dict):
        errors.append(f"{where} : un objet {{nom: niveau}} est attendu")
        return out
    for name, level in raw.items():
        name = str(name).strip()
        if not name or len(name) > 80:
            errors.append(f"{where} : nom invalide")
        elif isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 <= level <= 999:
            errors.append(f"{where} : niveau invalide pour « {name} » (0 à 999)")
        else:
            out[name] = int(level)
    return out


def validate(data: dict, *, allow_live: bool, min_cycle_minutes: float = 1.0) -> dict:
    """Retourne des réglages complets et propres, ou lève SettingsError avec la liste des problèmes."""
    errors: list[str] = []
    merged = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        raise SettingsError(["réglages : un objet JSON est attendu"])

    mode = data.get("mode", merged["mode"])
    if mode not in ("dry_run", "live"):
        errors.append("mode : « dry_run » ou « live »")
    elif mode == "live" and not allow_live:
        errors.append("mode réel : confirmation explicite requise")
    else:
        merged["mode"] = mode

    universe = data.get("universe", merged["universe"])
    if not isinstance(universe, str) or len(universe) > 60:
        errors.append("univers : un nom de 60 caractères au plus")
    else:
        merged["universe"] = universe.strip()

    cap = data.get("daily_cap", merged["daily_cap"])
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 500:
        errors.append("plafond quotidien : entier de 1 à 500")
    else:
        merged["daily_cap"] = cap

    cycle = data.get("cycle", merged["cycle"])
    try:
        lo, hi = float(cycle["min_minutes"]), float(cycle["max_minutes"])
        if lo < min_cycle_minutes or hi < lo or hi > 1440:
            raise ValueError
        merged["cycle"] = {"min_minutes": lo, "max_minutes": hi}
    except (KeyError, TypeError, ValueError):
        errors.append(f"cycle : minutes minimum (≥ {min_cycle_minutes:g}) et maximum (≥ minimum, ≤ 1440)")

    schedule = data.get("schedule", merged["schedule"])
    try:
        tz = str(schedule.get("timezone", "Europe/Paris"))
        ZoneInfo(tz)
        windows = []
        for w in schedule.get("windows", []):
            parse_hhmm(w["start"]), parse_hhmm(w["end"])
            days = sorted({int(d) for d in w["days"]})
            if not days or any(d < 0 or d > 6 for d in days):
                raise ValueError("jours")
            windows.append({"start": w["start"], "end": w["end"], "days": days})
        if len(windows) > 12:
            raise ValueError("trop de plages")
        merged["schedule"] = {"enabled": bool(schedule.get("enabled", False)), "timezone": tz, "windows": windows}
    except Exception:  # noqa: BLE001 - tout format invalide est signalé de la même façon
        errors.append("plages horaires : fuseau valide, heures « HH:MM », jours 0 (lundi) à 6 (dimanche)")

    cons = data.get("constructions", merged["constructions"])
    if isinstance(cons, dict):
        overrides: dict[str, dict[str, int]] = {}
        for planet, tg in (cons.get("overrides") or {}).items():
            overrides[str(planet)] = _targets(tg, f"constructions.overrides.{planet}", errors)
        planets = [str(p) for p in (cons.get("planets") or [])]
        merged["constructions"] = {
            "enabled": bool(cons.get("enabled", False)),
            "targets": _targets(cons.get("targets", {}), "constructions.targets", errors),
            "overrides": overrides,
            "planets": planets,
        }
    else:
        errors.append("constructions : un objet est attendu")

    res = data.get("research", merged["research"])
    if isinstance(res, dict):
        merged["research"] = {
            "enabled": bool(res.get("enabled", False)),
            "targets": _targets(res.get("targets", {}), "research.targets", errors),
            "funding_planet": str(res.get("funding_planet", ""))[:80],
        }
    else:
        errors.append("research : un objet est attendu")

    catalog = data.get("catalog", merged["catalog"])
    if isinstance(catalog, dict):
        merged["catalog"] = {
            key: sorted({str(n).strip() for n in catalog.get(key, []) if str(n).strip()})[:200]
            for key in ("buildings", "research", "planets", "universes")
        }

    if errors:
        raise SettingsError(errors)
    return merged


def normalized_targets(targets: dict[str, int]) -> dict[str, int]:
    return {norm(name): level for name, level in targets.items()}


class SettingsStore:
    """Réglages en mémoire, enregistrés dans `<data_dir>/automation.json` s'il existe (non secret)."""

    def __init__(self, data_dir: str | None, min_cycle_minutes: float = 1.0) -> None:
        self._path = Path(data_dir) / "automation.json" if data_dir and os.path.isdir(data_dir) else None
        self._min_cycle = min_cycle_minutes
        self._data = copy.deepcopy(DEFAULTS)
        self.loaded_from_disk = False
        if self._path and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                saved["mode"] = "dry_run"  # le mode réel n'est jamais restauré après un redémarrage
                self._data = validate(saved, allow_live=False, min_cycle_minutes=min_cycle_minutes)
                self.loaded_from_disk = True
            except (OSError, ValueError):
                self._data = copy.deepcopy(DEFAULTS)

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def update(self, data: dict, *, allow_live: bool) -> dict:
        # Le mode réel déjà actif peut rester actif sans nouvelle confirmation.
        cleaned = validate(
            data, allow_live=allow_live or self._data["mode"] == "live", min_cycle_minutes=self._min_cycle
        )
        cleaned["catalog"] = self._data["catalog"]  # entretenu par le bot, jamais modifié par l'utilisateur
        self._data = cleaned
        self._save()
        return self.get()

    def set_universe(self, name: str) -> None:
        if self._data["universe"] != name:
            self._data["universe"] = name
            self._save()

    def add_catalog(self, kind: str, names: list[str]) -> None:
        known = set(self._data["catalog"][kind])
        fresh = {n.strip() for n in names if n.strip()} - known
        if fresh:
            self._data["catalog"][kind] = sorted(known | fresh)[:200]
            self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".automation-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
