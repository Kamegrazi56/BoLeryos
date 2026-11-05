"""Réglages de l'automatisation : validation, valeurs par défaut, enregistrement (jamais de secret ici)."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .catalog import ESPIONAGE_WAYS, EXPEDITION_UNIT_MODES, EXPEDITION_WAYS, PRIORITY_BLOCKS, PRODUCTION_SECTION_IDS
from .numbers import norm
from .schedule import parse_hhmm

DEFAULTS: dict[str, Any] = {
    "mode": "dry_run",  # « dry_run » (à blanc) ou « live » (réel)
    "universe": "",  # univers où entrer automatiquement (nom affiché sur la page de choix) ; vide = demander
    "daily_cap": 20,
    "cycle": {"min_minutes": 3.0, "max_minutes": 8.0},
    # Pause aléatoire entre deux clics de la connexion (secondes), choisie dans Réglages › Compte et univers.
    "login_delay": {"min_seconds": 0.4, "max_seconds": 1.4},
    "schedule": {
        "enabled": False,
        "timezone": "Europe/Paris",
        "windows": [{"start": "08:00", "end": "23:00", "days": [0, 1, 2, 3, 4, 5, 6]}],
    },
    "constructions": {
        "enabled": False,
        "scope": "all",  # « all » (toutes les planètes) ou « one » (une seule, désignée par `planet`)
        "planet": "",
        # Liste par ordre de priorité : le bot lance la PREMIÈRE amélioration réalisable avec les ressources.
        "queue": [],  # [{"name": "Mine de Fer", "level": 45}, …]
        "recheck_minutes": 15.0,  # une planète où une amélioration attend des ressources est revérifiée toutes les X min
    },
    "research": {"enabled": False, "targets": {}, "funding_planet": ""},
    # Production d'un nombre d'éléments : population, vaisseaux, composants, appareils spécialisés, défenses.
    "production": {"enabled": False, "scope": "all", "planet": "", "recheck_minutes": 15.0, "queue": []},
    # Expéditions (Opérations › Exploration) : combien en garder d'actives, et leur composition.
    "expeditions": {"enabled": False, "scope": "all", "planet": "", "recheck_minutes": 15.0, "active_target": 1, "queue": []},
    # Espionnage automatique : une cible par passage, dans un intervalle de coordonnées, joueurs et/ou errants [AI].
    "espionage": {"enabled": False, "way": "sonde", "players": False, "errants": True, "exclude_alliance": True,
                  "coord_min": "", "coord_max": "", "planet": ""},
    # Attaque automatique des errants [AI] : intervalle de coordonnées, minimums de ressources lus dans le rapport
    # d'espionnage (Fer, Cristal, Éther ; on attaque si 1, 2 ou les 3 sont dépassés), voie et composition, nombre total.
    "raids": {"enabled": False, "way": "piliers", "coord_min": "", "coord_max": "", "planet": "",
              "min_fer": 0, "min_cristal": 0, "min_ether": 0, "required": 1, "total": 1, "units": []},
    # Alerte d'attaque : lecture des capteurs toutes les N secondes, seulement quand le bot est sur ON.
    "attack_alert": {"enabled": False, "interval_seconds": 60},
    # Ordre dans lequel le bot sert ses « blocs » : le premier prend les ressources avant les suivants.
    "priority": list(PRIORITY_BLOCKS),
    "catalog": {"buildings": [], "research": [], "planets": [], "universes": [], "ships": []},
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


def _queue(raw: Any, errors: list[str]) -> list[dict]:
    """Liste ordonnée d'améliorations : [{id, name, level, relative}], sans doublon.

    `relative` vrai : `level` est un nombre de niveaux À AJOUTER au niveau de départ (lu la première fois sur chaque
    planète) ; sinon c'est le niveau à atteindre. `id` identifie l'élément (point de départ conservé quand on le modifie).
    """
    out: list[dict] = []
    seen: set[str] = set()
    ids: set[str] = set()
    if not isinstance(raw, list):
        errors.append("constructions.queue : une liste est attendue")
        return out
    if len(raw) > 40:
        errors.append("constructions.queue : 40 améliorations au plus")
    for item in raw[:40]:
        name = str(item.get("name", "")).strip() if isinstance(item, dict) else ""
        level = item.get("level") if isinstance(item, dict) else None
        relative = bool(item.get("relative", False)) if isinstance(item, dict) else False
        low = 1 if relative else 0
        if not name or len(name) > 80:
            errors.append("constructions.queue : nom invalide")
        elif isinstance(level, bool) or not isinstance(level, (int, float)) or not low <= level <= 999:
            what = "nombre de niveaux à ajouter (1 à 999)" if relative else "niveau (0 à 999)"
            errors.append(f"constructions.queue : {what} invalide pour « {name} »")
        elif norm(name) in seen:
            errors.append(f"constructions.queue : « {name} » est en double")
        else:
            seen.add(norm(name))
            item_id = str(item.get("id", ""))[:16] if str(item.get("id", "")).isalnum() else ""
            if not item_id or item_id in ids:
                item_id = uuid.uuid4().hex[:8]
            ids.add(item_id)
            out.append({"id": item_id, "name": name, "level": int(level), "relative": relative})
    return out


MAX_LOGIN_DELAY = 60.0  # secondes
PRODUCTION_MODES = ("once", "stock", "repeat")
MAX_AMOUNT = 10**12


def _production_queue(raw: Any, errors: list[str]) -> list[dict]:
    """Lignes de production : [{id, section, name, mode, amount, max}].

    mode « once » : commande unique (faite une fois, puis marquée « faite ») ; « stock » : avoir au moins `amount`
    exemplaires (le bot ne lance que la différence) ; « repeat » : relancer `amount` (ou le maximum) à chaque passage.
    `max` vrai : le bot clique sur le bouton MAX du jeu (impossible avec « stock »).
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    ids: set[str] = set()
    if not isinstance(raw, list):
        errors.append("production.queue : une liste est attendue")
        return out
    if len(raw) > 60:
        errors.append("production.queue : 60 lignes au plus")
    for item in raw[:60]:
        if not isinstance(item, dict):
            errors.append("production.queue : ligne invalide")
            continue
        section = str(item.get("section", ""))
        name = str(item.get("name", "")).strip()
        mode = str(item.get("mode", "once"))
        is_max = bool(item.get("max", False))
        amount = item.get("amount", 1)
        if section not in PRODUCTION_SECTION_IDS:
            errors.append("production.queue : section inconnue")
        elif not name or len(name) > 80:
            errors.append("production.queue : nom invalide")
        elif mode not in PRODUCTION_MODES:
            errors.append(f"production.queue : mode inconnu pour « {name} »")
        elif is_max and mode == "stock":
            errors.append(f"production.queue : « {name} » : le maximum n'a pas de sens pour un objectif de stock")
        elif not is_max and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 1 <= amount <= MAX_AMOUNT):
            errors.append(f"production.queue : quantité invalide pour « {name} » (1 à {MAX_AMOUNT})")
        elif (section, norm(name)) in seen:
            errors.append(f"production.queue : « {name} » est en double")
        else:
            seen.add((section, norm(name)))
            item_id = str(item.get("id", ""))[:16] if str(item.get("id", "")).isalnum() else ""
            if not item_id or item_id in ids:
                item_id = uuid.uuid4().hex[:8]
            ids.add(item_id)
            out.append({"id": item_id, "section": section, "name": name, "mode": mode,
                        "amount": 1 if is_max else int(amount), "max": is_max})
    return out


MAX_HOURS = 24
_COORD_RE = re.compile(r"^([A-Z])(\d{2}) (\d{2}) (\d{2})$")


def coord_key(text: str) -> tuple[str, int, int, int] | None:
    """« I27 95 00 » → ("I", 27, 95, 0) : ordre lettre, puis les trois nombres (bornes comprises)."""
    match = _COORD_RE.match(str(text).strip().upper())
    return (match.group(1), int(match.group(2)), int(match.group(3)), int(match.group(4))) if match else None


def _expedition_units(raw: Any, name_for_error: str, errors: list[str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list) or not raw:
        errors.append(f"expeditions.queue : « {name_for_error} » : choisissez au moins une unité")
        return out
    for unit in raw[:30]:
        name = str(unit.get("name", "")).strip() if isinstance(unit, dict) else ""
        mode = unit.get("mode", "number") if isinstance(unit, dict) else ""
        value = unit.get("value", 1) if isinstance(unit, dict) else None
        if not name or len(name) > 80:
            errors.append("expeditions.queue : nom d'unité invalide")
        elif mode not in EXPEDITION_UNIT_MODES:
            errors.append(f"expeditions.queue : mode inconnu pour « {name} »")
        elif norm(name) in seen:
            errors.append(f"expeditions.queue : « {name} » est en double dans une composition")
        elif mode == "max":
            seen.add(norm(name))
            out.append({"name": name, "mode": "max", "value": 1})
        else:
            low, high = (1, MAX_AMOUNT) if mode == "number" else (1, 100)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                errors.append(f"expeditions.queue : quantité invalide pour « {name} » ({low} à {high}{' %' if mode == 'percent' else ''})")
            else:
                seen.add(norm(name))
                out.append({"name": name, "mode": mode, "value": int(value)})
    return out


def _expedition_queue(raw: Any, errors: list[str]) -> list[dict]:
    """Lignes d'expédition : [{id, way, hours, units:[{name, mode, value}]}] (mode : number | max | percent)."""
    out: list[dict] = []
    ids: set[str] = set()
    if not isinstance(raw, list):
        errors.append("expeditions.queue : une liste est attendue")
        return out
    if len(raw) > 20:
        errors.append("expeditions.queue : 20 lignes au plus")
    for item in raw[:20]:
        if not isinstance(item, dict):
            errors.append("expeditions.queue : ligne invalide")
            continue
        way, hours = item.get("way"), item.get("hours", 1)
        label = EXPEDITION_WAYS.get(way, "?")
        if way not in EXPEDITION_WAYS:
            errors.append("expeditions.queue : voie inconnue (piliers ou flotte)")
            continue
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 1 <= hours <= MAX_HOURS:
            errors.append(f"expeditions.queue : durée invalide ({label}) : 1 à {MAX_HOURS} heures")
            continue
        units = _expedition_units(item.get("units"), label, errors)
        item_id = str(item.get("id", ""))[:16] if str(item.get("id", "")).isalnum() else ""
        if not item_id or item_id in ids:
            item_id = uuid.uuid4().hex[:8]
        ids.add(item_id)
        out.append({"id": item_id, "way": way, "hours": int(hours), "units": units})
    return out


def _priority(raw: Any, errors: list[str]) -> list[str]:
    """Ordre des blocs : une liste des 6 blocs connus ; les blocs oubliés sont ajoutés à la fin."""
    if not isinstance(raw, list) or any(not isinstance(b, str) for b in raw):
        errors.append("priority : une liste de blocs est attendue")
        return list(PRIORITY_BLOCKS)
    unknown = [b for b in raw if b not in PRIORITY_BLOCKS]
    if unknown:
        errors.append(f"priority : bloc inconnu ({unknown[0]})")
    out: list[str] = []
    for block in [*raw, *PRIORITY_BLOCKS]:
        if block in PRIORITY_BLOCKS and block not in out:
            out.append(block)
    return out


def _migrate_constructions(cons: dict) -> dict:
    """Anciens réglages : niveaux par bâtiment (`targets`) et liste de planètes (`planets`)."""
    cons = dict(cons)
    if "queue" not in cons and isinstance(cons.get("targets"), dict):
        cons["queue"] = [{"name": n, "level": lvl} for n, lvl in cons["targets"].items()]
    if "scope" not in cons and isinstance(cons.get("planets"), list):
        planets = [str(p) for p in cons["planets"]]
        cons["scope"], cons["planet"] = ("one", planets[0]) if len(planets) == 1 else ("all", "")
    return cons


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

    login_delay = data.get("login_delay", merged["login_delay"])
    try:
        d_lo, d_hi = login_delay["min_seconds"], login_delay["max_seconds"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (d_lo, d_hi)):
            raise ValueError
        if not 0 <= d_lo <= d_hi <= MAX_LOGIN_DELAY:
            raise ValueError
        merged["login_delay"] = {"min_seconds": float(d_lo), "max_seconds": float(d_hi)}
    except (KeyError, TypeError, ValueError):
        errors.append(f"délai entre deux clics de la connexion : minimum ≥ 0, maximum ≥ minimum, ≤ {MAX_LOGIN_DELAY:g} secondes")

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
        cons = _migrate_constructions(cons)
        scope = cons.get("scope", "all")
        planet = str(cons.get("planet", "")).strip()[:80]
        if scope not in ("all", "one"):
            errors.append("constructions.scope : « all » ou « one »")
        elif scope == "one" and not planet:
            errors.append("constructions : choisissez la planète concernée")
        try:
            recheck = float(cons.get("recheck_minutes", merged["constructions"]["recheck_minutes"]))
            if recheck < min_cycle_minutes or recheck > 1440:
                raise ValueError
        except (TypeError, ValueError):
            recheck = merged["constructions"]["recheck_minutes"]
            errors.append(f"constructions : revérification toutes les {min_cycle_minutes:g} à 1440 minutes")
        merged["constructions"] = {
            "enabled": bool(cons.get("enabled", False)),
            "scope": scope if scope in ("all", "one") else "all",
            "planet": planet if scope == "one" else "",
            "queue": _queue(cons.get("queue", []), errors),
            "recheck_minutes": recheck,
        }
    else:
        errors.append("constructions : un objet est attendu")

    prod = data.get("production", merged["production"])
    if isinstance(prod, dict):
        scope = prod.get("scope", "all")
        planet = str(prod.get("planet", "")).strip()[:80]
        if scope not in ("all", "one"):
            errors.append("production.scope : « all » ou « one »")
        elif scope == "one" and not planet:
            errors.append("production : choisissez la planète concernée")
        try:
            recheck = float(prod.get("recheck_minutes", merged["production"]["recheck_minutes"]))
            if recheck < min_cycle_minutes or recheck > 1440:
                raise ValueError
        except (TypeError, ValueError):
            recheck = merged["production"]["recheck_minutes"]
            errors.append(f"production : revérification toutes les {min_cycle_minutes:g} à 1440 minutes")
        merged["production"] = {
            "enabled": bool(prod.get("enabled", False)),
            "scope": scope if scope in ("all", "one") else "all",
            "planet": planet if scope == "one" else "",
            "recheck_minutes": recheck,
            "queue": _production_queue(prod.get("queue", []), errors),
        }
    else:
        errors.append("production : un objet est attendu")
    exp = data.get("expeditions", merged["expeditions"])
    if isinstance(exp, dict):
        scope = exp.get("scope", "all")
        planet = str(exp.get("planet", "")).strip()[:80]
        if scope not in ("all", "one"):
            errors.append("expeditions.scope : « all » ou « one »")
        elif scope == "one" and not planet:
            errors.append("expeditions : choisissez la planète de départ")
        try:
            recheck = float(exp.get("recheck_minutes", merged["expeditions"]["recheck_minutes"]))
            if recheck < min_cycle_minutes or recheck > 1440:
                raise ValueError
        except (TypeError, ValueError):
            recheck = merged["expeditions"]["recheck_minutes"]
            errors.append(f"expeditions : revérification toutes les {min_cycle_minutes:g} à 1440 minutes")
        target = exp.get("active_target", 1)
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not 1 <= target <= 50:
            errors.append("expeditions : nombre d'explorations à garder actives : 1 à 50")
            target = 1
        merged["expeditions"] = {
            "enabled": bool(exp.get("enabled", False)),
            "scope": scope if scope in ("all", "one") else "all",
            "planet": planet if scope == "one" else "",
            "recheck_minutes": recheck,
            "active_target": int(target),
            "queue": _expedition_queue(exp.get("queue", []), errors),
        }
    else:
        errors.append("expeditions : un objet est attendu")
    spy = data.get("espionage", merged["espionage"])
    if isinstance(spy, dict):
        way = spy.get("way", "sonde")
        lo, hi = str(spy.get("coord_min", "")).strip().upper(), str(spy.get("coord_max", "")).strip().upper()
        lo_key, hi_key = coord_key(lo), coord_key(hi)
        if way not in ESPIONAGE_WAYS:
            errors.append("espionage.way : « sonde », « drone » ou « both »")
            way = "sonde"
        if (lo and lo_key is None) or (hi and hi_key is None):
            errors.append("espionnage : coordonnées au format « I27 95 00 » (lettre, puis 3 nombres de 2 chiffres)")
        elif lo_key and hi_key and lo_key > hi_key:
            errors.append("espionnage : la coordonnée minimum doit être avant la coordonnée maximum")
        enabled = bool(spy.get("enabled", False))
        players, errants = bool(spy.get("players", False)), bool(spy.get("errants", True))
        if enabled and not (lo and hi):
            errors.append("espionnage : indiquez la coordonnée minimum et la coordonnée maximum")
        if enabled and not (players or errants):
            errors.append("espionnage : choisissez joueurs et/ou errants")
        merged["espionage"] = {"enabled": enabled, "way": way, "players": players, "errants": errants,
                               "exclude_alliance": bool(spy.get("exclude_alliance", True)), "coord_min": lo, "coord_max": hi,
                               "planet": str(spy.get("planet", "")).strip()[:80]}
    else:
        errors.append("espionage : un objet est attendu")

    raid = data.get("raids", merged["raids"])
    if isinstance(raid, dict):
        way = raid.get("way", "piliers")
        if way not in EXPEDITION_WAYS:
            errors.append("attaque des errants : voie « piliers » ou « flotte »")
            way = "piliers"
        lo, hi = str(raid.get("coord_min", "")).strip().upper(), str(raid.get("coord_max", "")).strip().upper()
        lo_key, hi_key = coord_key(lo), coord_key(hi)
        if (lo and lo_key is None) or (hi and hi_key is None):
            errors.append("attaque des errants : coordonnées au format « I27 95 00 »")
        elif lo_key and hi_key and lo_key > hi_key:
            errors.append("attaque des errants : la coordonnée minimum doit être avant la coordonnée maximum")
        mins = {}
        for key in ("min_fer", "min_cristal", "min_ether"):
            value = raid.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10**15:
                errors.append("attaque des errants : minimums de ressources de 0 à 10^15")
                value = 0
            mins[key] = int(value)
        required, total = raid.get("required", 1), raid.get("total", 1)
        if required not in (1, 2, 3) or isinstance(required, bool):
            errors.append("attaque des errants : attaquer si 1, 2 ou 3 ressources dépassent leur minimum")
            required = 1
        if isinstance(total, bool) or not isinstance(total, (int, float)) or not 1 <= total <= 10000:
            errors.append("attaque des errants : nombre d'attaques de 1 à 10 000")
            total = 1
        enabled = bool(raid.get("enabled", False))
        units = _expedition_units(raid.get("units"), "attaque des errants", errors) if (enabled or raid.get("units")) else []
        if enabled and not (lo and hi):
            errors.append("attaque des errants : indiquez la coordonnée minimum et la coordonnée maximum")
        merged["raids"] = {"enabled": enabled, "way": way, "coord_min": lo, "coord_max": hi,
                           "planet": str(raid.get("planet", "")).strip()[:80], **mins, "required": int(required),
                           "total": int(total), "units": units}
    else:
        errors.append("raids : un objet est attendu")

    alert = data.get("attack_alert", merged["attack_alert"])
    if isinstance(alert, dict):
        low = max(1.0, min(10.0, min_cycle_minutes * 60))
        interval = alert.get("interval_seconds", 60)
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or not low <= interval <= 3600:
            errors.append(f"alerte d'attaque : vérification toutes les {low:g} à 3600 secondes")
            interval = 60
        merged["attack_alert"] = {"enabled": bool(alert.get("enabled", False)), "interval_seconds": float(interval)}
    else:
        errors.append("attack_alert : un objet est attendu")
    merged["priority"] = _priority(data.get("priority", merged["priority"]), errors)

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
            for key in ("buildings", "research", "planets", "universes", "ships")
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
        self.load_problems: list[str] = []  # valeurs du fichier refusées au démarrage (affichées dans le journal)
        if self._path and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if not isinstance(saved, dict):
                    raise ValueError("objet JSON attendu")
                saved["mode"] = "dry_run"  # le mode réel n'est jamais restauré après un redémarrage
                try:
                    self._data = validate(saved, allow_live=False, min_cycle_minutes=min_cycle_minutes)
                except SettingsError as exc:
                    # Une seule valeur refusée (ex. limite changée par une mise à jour) ne doit pas tout effacer :
                    # on garde chaque partie valide, et une copie du fichier d'origine est conservée à côté.
                    self._data = self._salvage(saved, min_cycle_minutes)
                    self.load_problems = exc.errors
                    try:
                        shutil.copyfile(self._path, self._path.with_name(self._path.name + ".bak"))
                    except OSError:
                        pass  # la copie est une précaution : son échec n'empêche pas de démarrer
                self.loaded_from_disk = True
            except (OSError, ValueError):
                self._data = copy.deepcopy(DEFAULTS)
                self.load_problems = ["fichier de réglages illisible : valeurs par défaut"]

    @staticmethod
    def _salvage(saved: dict, min_cycle_minutes: float) -> dict:
        """Réglages partiellement valides : chaque partie est validée seule ; une partie refusée garde sa valeur par défaut."""
        data = copy.deepcopy(DEFAULTS)
        for key in DEFAULTS:
            if key == "mode" or key not in saved:
                continue
            try:
                data[key] = validate({key: saved[key]}, allow_live=False, min_cycle_minutes=min_cycle_minutes)[key]
            except SettingsError:
                pass
        return data

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def update(self, data: dict, *, allow_live: bool) -> dict:
        """Les clés absentes de `data` restent inchangées : une page peut n'envoyer que ce qu'elle modifie."""
        merged = self.get()
        for key, value in (data if isinstance(data, dict) else {}).items():
            if key == "catalog":
                continue  # entretenu par le bot
            if key in ("cycle", "login_delay", "schedule", "constructions", "research", "production", "expeditions", "attack_alert", "espionage", "raids") and isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        # Le mode réel déjà actif peut rester actif sans nouvelle confirmation.
        cleaned = validate(
            merged, allow_live=allow_live or self._data["mode"] == "live", min_cycle_minutes=self._min_cycle
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
