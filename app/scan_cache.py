"""Mémoire des scans : la liste des planètes et les modèles de vaisseaux survivent au redémarrage du bot.

Avant, ces deux lectures n'existaient qu'en mémoire : chaque démarrage (et chaque reconnexion) rouvrait le jeu pour les
relire, même si rien n'avait changé. On les garde maintenant dans `<data_dir>/scans.json`, avec leur date, et le bot ne
les relit au démarrage que s'ils sont trop vieux. Le bouton « Scanner », la commande Discord `/scan` et les relevés
faits par les fonctionnalités restent des lectures forcées.

Les données appartiennent à un compte dans un univers : le fichier ne sert que pour la même « portée » (univers + compte).
Le nom du compte n'y est écrit qu'en empreinte, jamais en clair.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

FILE_NAME = "scans.json"
KINDS = ("planets", "models")  # ce que le fichier peut contenir


def scope_of(universe: str, identifier: str) -> str:
    """Portée des données : l'univers et une empreinte du compte (vide tant que ni l'un ni l'autre n'est connu)."""
    if not universe or not identifier:
        return ""
    digest = hashlib.sha256(identifier.strip().lower().encode("utf8")).hexdigest()[:12]
    return f"{universe.strip().lower()}|{digest}"


def age_of(read_at: float | None, now: float | None = None) -> float | None:
    """Âge en secondes d'une lecture (None si elle n'a jamais eu lieu ou si sa date est dans le futur)."""
    if not isinstance(read_at, (int, float)) or isinstance(read_at, bool):
        return None
    age = (time.time() if now is None else now) - read_at
    return age if age >= 0 else None


def is_fresh(read_at: float | None, max_age: float, now: float | None = None) -> bool:
    age = age_of(read_at, now)
    return age is not None and age <= max_age


def describe_age(seconds: float) -> str:
    """« 20 min », « 3 h » ou « 2 j » : l'âge d'une donnée gardée, pour le journal."""
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} min"
    if seconds < 86400:
        return f"{round(seconds / 3600)} h"
    return f"{round(seconds / 86400)} j"


def _valid_items(items) -> bool:
    return isinstance(items, list) and all(isinstance(i, dict) and isinstance(i.get("name"), str) for i in items)


class ScanCache:
    """Dernier scan des planètes et des modèles de vaisseaux, pour une portée donnée."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data: dict = {"scope": ""}
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
            except (OSError, ValueError):
                return  # fichier illisible : on repart de zéro, le scan suivant le réécrit
            if isinstance(saved, dict) and isinstance(saved.get("scope"), str):
                self._data = {"scope": saved["scope"]}
                for kind in KINDS:
                    entry = saved.get(kind)
                    if isinstance(entry, dict) and _valid_items(entry.get("items")) and age_of(entry.get("read_at")) is not None:
                        self._data[kind] = {"items": entry["items"], "read_at": entry["read_at"], "source": str(entry.get("source", ""))}

    def load(self, kind: str, scope: str) -> dict | None:
        """{"items", "read_at", "source"} du dernier scan de cette portée, ou None (autre compte/univers, rien de gardé)."""
        entry = self._data.get(kind)
        if not scope or self._data.get("scope") != scope or entry is None:
            return None
        return copy.deepcopy(entry)

    def save(self, kind: str, scope: str, items: list[dict], read_at: float, source: str) -> None:
        """Garde un scan réussi. Une autre portée remplace tout le fichier : on ne mélange jamais deux comptes ou univers."""
        if not scope:
            return
        if self._data.get("scope") != scope:
            self._data = {"scope": scope}
        self._data[kind] = {"items": copy.deepcopy(items), "read_at": read_at, "source": source}
        self._write()

    def _write(self) -> None:
        if not self._path:
            return
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".scans-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except (OSError, TypeError, ValueError):
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            # l'enregistrement est facultatif : le bot continue en mémoire
