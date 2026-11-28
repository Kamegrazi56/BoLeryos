"""Relevé des vaisseaux d'une planète donneuse (Opérations › Flotte › Transport) et choix de ceux qui partent.

Relevé sur le vrai jeu (2026-11-28, Asterion, lecture seule) : bloc « 2. Composition de la flotte » = une ligne par vaisseau
(nom, « Disponibles : N », champ « Quantité <nom> ») ; bloc « 3. Logistique et chargement » = « Capacité de soute » suivie de
« N m³ » puis « Espace libre : N m³ ». La soute est proportionnelle au nombre de vaisseaux (11 vaisseaux = 11 × la soute d'un
seul) et 1 ressource occupe 1 m³. L'« Espace libre » est plus petit que la capacité (480 m³ par vaisseau d'écart sur le
« Trasnpo civil cuir ») : c'est lui qui dit ce qu'un vaisseau peut vraiment charger.

Fichier `<data_dir>/transport_ships.json` : {"planets": {planète: {"scanned_at", "ships": [{"name", "available", "cargo", "free"}]}}}
où `cargo` est la capacité de soute d'un vaisseau et `free` l'espace libre avec un seul vaisseau (None : non mesuré).
Ce module ne clique sur rien.
"""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import time
from pathlib import Path

from .numbers import parse_amount

FILE_NAME = "transport_ships.json"

# Lignes de la composition : le champ « Quantité <nom> » donne le nom, le texte « Disponibles : N » de la même ligne l'effectif.
SHIP_ROWS_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const rows = [];
  for (const input of document.querySelectorAll('main input[aria-label^="Quantité "]')) {
    if (!input.getClientRects().length) continue;
    let box = input.parentElement;
    while (box && !/Disponibles\\s*:/i.test(text(box))) box = box.parentElement;
    if (!box) continue;
    const found = /Disponibles\\s*:\\s*([^A-Za-zÀ-ÿ]*)/i.exec(text(box));
    rows.push({ name: input.getAttribute('aria-label').slice('Quantité '.length), available: found ? found[1].trim() : '' });
  }
  return rows;
}"""

# « Capacité de soute » puis sa valeur, et « Espace libre : … » : textes bruts (« 2 968 928 m³ »), lus comme TRAVEL_TIME_JS.
HOLD_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const leaves = [...document.querySelectorAll('main *')].filter((e) => e.children.length === 0 && e.getClientRects().length > 0 && text(e));
  const i = leaves.findIndex((e) => /^Capacité de soute$/i.test(text(e)));
  const free = leaves.find((e) => /^Espace libre\\s*:/i.test(text(e)));
  return { capacity: i >= 0 && leaves[i + 1] ? text(leaves[i + 1]) : null, free: free ? text(free) : null };
}"""


def parse_volume(text: str | None) -> int | None:
    """« 2 968 928 m³ » ou « Espace libre : 2 968 448 m³ » → entier. None si illisible."""
    raw = (text or "").split(":")[-1].replace("m³", "").replace("m3", "").strip()
    try:
        return int(parse_amount(raw)) if raw else None
    except ValueError:
        return None


def parse_available(rows: list[dict]) -> dict[str, int]:
    """Lignes brutes de SHIP_ROWS_JS → {nom: effectif disponible} (effectif illisible : 0, on n'agit pas sur une ligne incertaine)."""
    out: dict[str, int] = {}
    for row in rows:
        try:
            out[str(row["name"])] = max(0, int(parse_amount(row["available"])))
        except (KeyError, ValueError):
            out[str(row.get("name", ""))] = 0
    return {name: n for name, n in out.items() if name}


def pick_ships(chosen: list[str], available: dict[str, int], hold: dict[str, int], volume: int) -> tuple[dict[str, int], int]:
    """Vaisseaux à envoyer pour porter `volume` m³ : les vaisseaux cochés, dans l'ordre, chacun au plus son effectif disponible.
    `hold` = espace utilisable d'un vaisseau. Retourne ({nom: nombre}, espace total obtenu) ; un vaisseau sans soute connue est sauté."""
    counts: dict[str, int] = {}
    room, left = 0, volume
    for name in chosen:
        unit, have = hold.get(name) or 0, available.get(name, 0)
        if left <= 0:
            break
        if unit <= 0 or have <= 0:
            continue
        count = min(have, math.ceil(left / unit))
        counts[name] = count
        room += count * unit
        left -= count * unit
    return counts, room


def fit_cargo(wanted: dict[str, int], room: int) -> dict[str, int]:
    """Cargaison ramenée à la place disponible (proportionnellement) quand les vaisseaux choisis ne suffisent pas."""
    total = sum(wanted.values())
    if total <= room or total <= 0:
        return dict(wanted)
    scaled = {name: math.floor(qty * room / total) for name, qty in wanted.items()}
    return {name: qty for name, qty in scaled.items() if qty > 0}


class ShipRoster:
    """Dernier relevé des vaisseaux de chaque planète donneuse, gardé d'un démarrage à l'autre."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._planets: dict[str, dict] = {}
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                self._planets = self._clean(saved.get("planets")) if isinstance(saved, dict) else {}
            except (OSError, ValueError):
                self._planets = {}  # fichier illisible : on repart d'un relevé vide

    @staticmethod
    def _clean(raw) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for planet, info in (raw.items() if isinstance(raw, dict) else []):
            ships = info.get("ships") if isinstance(info, dict) else None
            if not isinstance(ships, list):
                continue
            kept = [{"name": s["name"], "available": int(s.get("available") or 0), "cargo": s.get("cargo"), "free": s.get("free")}
                    for s in ships if isinstance(s, dict) and isinstance(s.get("name"), str)]
            out[str(planet)] = {"scanned_at": info.get("scanned_at"), "ships": kept}
        return out

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        """{"planets": {planète: {"scanned_at", "ships"}}, "names": [vaisseaux connus, triés]}."""
        names = sorted({s["name"] for info in self._planets.values() for s in info["ships"]}, key=str.casefold)
        return {"planets": copy.deepcopy(self._planets), "names": names}

    def known_names(self) -> list[str]:
        return self.get()["names"]

    def ships_of(self, planet: str) -> list[dict]:
        return copy.deepcopy(self._planets.get(planet, {}).get("ships", []))

    def hold_of(self, name: str) -> int | None:
        """Espace utilisable d'un vaisseau (le plus récemment mesuré, toutes planètes confondues : le modèle est le même)."""
        for info in self._planets.values():
            for ship in info["ships"]:
                if ship["name"] == name and (ship.get("free") or ship.get("cargo")):
                    return int(ship.get("free") or ship["cargo"])
        return None

    def set_planet(self, planet: str, ships: list[dict]) -> None:
        self._planets[planet] = {"scanned_at": time.time(), "ships": copy.deepcopy(ships)}
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".transport-ships-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"planets": self._planets}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
