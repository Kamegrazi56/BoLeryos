"""Transport de ressources pour les constructions : réglages et calcul du manque.

Quand une construction de la liste est bloquée faute de ressources, le bot peut faire envoyer ce qui manque à cet instant
par une ou plusieurs planètes « donneuses », par les Piliers (arrivée immédiate) ou par vaisseaux (spatial). La voie est un
choix fixe ; si les Piliers d'une donneuse sont fermés, le bot essaie le spatial, puis saute cette donneuse.

Fichier `<data_dir>/transport.json` : {"enabled", "donors": [planètes], "method": "PILLARS"|"FLEET",
"models": [modèles Piliers, dans l'ordre d'essai ; vide en voie Flotte], "ships": [vaisseaux relevés sur la planète donneuse,
dans l'ordre d'emploi : la voie Flotte, ou le secours spatial quand la voie est « Piliers »], "margin": pourcentage par pas de 0,5}.
Ce module ne clique sur rien : l'envoi est dans transporter.py.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Callable

from .explorations import METHODS

FILE_NAME = "transport.json"
RESOURCES = ("Fer", "Cristal", "Éther")
MARGIN_STEP = 0.5
MAX_MARGIN = 50.0
MAX_DONORS = 50
MAX_MODELS = 10
MAX_SHIPS = 20
MAX_NAME_LENGTH = 80
# L'en-tête du jeu arrondit les stocks (« 104,334 M » : au millier près, davantage en G) : sans cette petite avance, un envoi
# « juste ce qui manque » peut rester quelques unités en dessous du coût et la construction resterait grisée.
DISPLAY_SLACK = 1000

DEFAULTS: dict = {"enabled": False, "donors": [], "method": "PILLARS", "models": [], "ships": [], "margin": 0.0}


class TransportError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _clean_names(raw, label: str, limit: int, errors: list[str], known: list[str] | None = None) -> list[str]:
    if not isinstance(raw, list) or len(raw) > limit:
        errors.append(f"{label} : une liste de {limit} noms au plus.")
        return []
    names: list[str] = []
    for item in raw:
        name = item.strip() if isinstance(item, str) else ""
        if not name or len(name) > MAX_NAME_LENGTH:
            errors.append(f"{label} : nom invalide.")
        elif known and name not in known:
            errors.append(f"{label} : « {name} » est inconnu.")
        elif name not in names:
            names.append(name)
    return names


def _clean_margin(raw, errors: list[str]) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 <= raw <= MAX_MARGIN:
        errors.append(f"Marge : un pourcentage entre 0 et {MAX_MARGIN:g}.")
        return 0.0
    steps = raw / MARGIN_STEP
    if abs(steps - round(steps)) > 1e-9:
        errors.append(f"Marge : par pas de {MARGIN_STEP:g} %.")
        return 0.0
    return round(steps) * MARGIN_STEP


def validate(data: dict, known_planets: list[str] | None = None, known_models: dict | None = None,
             known_ships: list[str] | None = None) -> dict:
    """Réglages complets et valides, ou TransportError avec la liste des problèmes (en français)."""
    errors: list[str] = []
    merged = {**DEFAULTS, **{k: data[k] for k in DEFAULTS if k in data}}
    if not isinstance(merged["enabled"], bool):
        errors.append("Activation : oui ou non.")
    method = merged["method"]
    if method not in METHODS:
        errors.append("Voie : Piliers ou Flotte.")
        method = DEFAULTS["method"]
    donors = _clean_names(merged["donors"], "Planètes donneuses", MAX_DONORS, errors, known_planets)
    # Les modèles ne servent qu'aux Piliers : en voie Flotte, ce sont les vaisseaux de la planète qui composent l'envoi.
    models = _clean_names(merged["models"], "Modèles", MAX_MODELS, errors, known_models.get("PILLARS") if known_models else None) if method == "PILLARS" else []
    ships = _clean_names(merged["ships"], "Vaisseaux", MAX_SHIPS, errors, known_ships)
    margin = _clean_margin(merged["margin"], errors)
    if merged["enabled"] is True and not errors:
        if not donors:
            errors.append("Choisissez au moins une planète donneuse.")
        if method == "PILLARS" and not models:
            errors.append("Choisissez au moins un modèle.")
        if method == "FLEET" and not ships:
            errors.append("Choisissez au moins un vaisseau (relevez d'abord ceux des planètes donneuses).")
    if errors:
        raise TransportError(errors)
    return {"enabled": merged["enabled"], "donors": donors, "method": method, "models": models, "ships": ships, "margin": margin}


def _migrated(saved: dict) -> dict:
    """Ancien fichier (avant le relevé des vaisseaux) : les modèles Flotte ne sont plus utilisés. Une option réglée en voie
    Flotte n'a plus de vaisseaux choisis : elle est désactivée plutôt que de partir avec une composition devinée."""
    if "ships" in saved:
        return saved
    kept = {k: v for k, v in saved.items() if k != "fleet_models"}
    if kept.get("method") == "FLEET":
        kept["enabled"] = False
    return kept


class TransportSettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULTS)
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict):
                    self._data = validate(_migrated(saved))  # planètes, modèles et vaisseaux inconnus : acceptés au chargement
            except (OSError, ValueError, TransportError):
                self._data = copy.deepcopy(DEFAULTS)  # fichier illisible ou refusé : option désactivée

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data: dict, known_planets: Callable[[], list[str]] | None = None,
                known_models: Callable[[], dict] | None = None, known_ships: Callable[[], list[str]] | None = None) -> dict:
        self._data = validate(data, known_planets() if known_planets else None, known_models() if known_models else None,
                              known_ships() if known_ships else None)
        self._save()
        return self.get()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".transport-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire


def shortfall(costs: dict[str, float | None], stocks: dict[str, float | None]) -> dict[str, float]:
    """Ce qui manque à l'instant pour payer `costs` avec `stocks` (ressources inconnues ou suffisantes : absentes)."""
    out: dict[str, float] = {}
    for name in RESOURCES:
        cost, stock = costs.get(name), stocks.get(name)
        if cost and stock is not None and cost > stock:
            out[name] = cost - stock
    return out


def with_margin(missing: dict[str, float], margin: float) -> dict[str, int]:
    """Quantités à envoyer : le manque plus la marge (en %), arrondi à l'unité supérieure, plus l'avance d'arrondi d'affichage."""
    return {k: math.ceil(v * (1 + margin / 100)) + DISPLAY_SLACK for k, v in missing.items() if v > 0}


def describe(settings: dict) -> str:
    """Phrase de résumé pour le journal."""
    if not settings["enabled"]:
        return "transport désactivé"
    models = f"modèles : {', '.join(settings['models'])}" if settings["models"] else "sans modèle"
    ships = f" ; vaisseaux : {', '.join(settings['ships'])}" if settings["ships"] else ""
    return (f"transport par {METHODS[settings['method']]} depuis {', '.join(settings['donors'])} "
            f"({models}{ships} ; marge {settings['margin']:g} %)")
