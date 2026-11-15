"""Effectifs de composants de chaque planète, pour le tableau de l'onglet Composants.

Contrairement aux bâtiments, aux recherches et aux populations, les composants ne figurent pas dans Empire › Inventaire (relevé
sur le vrai jeu le 2026-11-15) : le seul endroit où le jeu affiche leur effectif (« ×N ») est l'onglet « Composants » du Chantier
spatial, planète par planète. Le bot les note donc à chaque planète qu'il visite pendant un passage, et le bouton « Relever
maintenant » les relit toutes, sans rien produire. Dernier relevé gardé dans `<data_dir>/component_counts.json`.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

from .components import COMPONENTS
from .numbers import norm

FILE_NAME = "component_counts.json"
_IDS = {norm(c.label): c.id for c in COMPONENTS}


def counts_from_cards(cards: dict) -> dict[str, int]:
    """{id du composant: effectif} d'après les cartes lues sur la page (clés = noms normalisés ; effectif illisible : écarté)."""
    return {_IDS[name]: card.owned for name, card in cards.items() if name in _IDS and card.owned is not None}


def _empty() -> dict:
    return {"counts": {}, "read_at": None, "source": "", "error": ""}


def _valid(counts) -> bool:
    return isinstance(counts, dict) and all(
        isinstance(planet, str) and isinstance(by_id, dict)
        and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in by_id.items())
        for planet, by_id in counts.items()
    )


class ComponentCounts:
    """Dernier effectif connu de chaque composant sur chaque planète (gardé sur disque : il survit au redémarrage)."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = _empty()
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict) and _valid(saved.get("counts")):
                    self._data = {**_empty(), "counts": saved["counts"], "read_at": saved.get("read_at"), "source": str(saved.get("source", ""))}
            except (OSError, ValueError):
                self._data = _empty()  # fichier illisible : tableau vide, rempli au prochain passage

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def set_planet(self, planet: str, counts: dict[str, int], source: str) -> None:
        """Remplace les effectifs d'UNE planète (les autres planètes gardent leur dernier relevé)."""
        self._data = {"counts": {**self._data["counts"], planet: dict(counts)}, "read_at": time.time(), "source": source, "error": ""}
        self._save()

    def set_error(self, message: str) -> None:
        """Le relevé a échoué : les effectifs d'avant restent affichés, avec la raison."""
        self._data = {**self._data, "error": message}

    def _save(self) -> None:
        if not self._path:
            return
        payload = {k: self._data[k] for k in ("counts", "read_at", "source")}
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".component-counts-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass  # l'enregistrement est facultatif : le bot continue en mémoire
