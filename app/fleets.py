"""Retours de flottes programmés : relevé des flottes en vol et heure d'arrivée voulue pour leur retour.

Le joueur choisit, pour une flotte en vol, l'heure à laquelle elle doit être RENTRÉE. Le bot ne connaît pas à l'avance la
durée du retour (elle dépend du moment du rappel) : il la lit dans la fenêtre « Préparer le retour de la flotte » du jeu et
clique sur la confirmation quand « maintenant + durée de retour affichée » atteint l'heure voulue (moins la marge).
L'arrivée prévue d'un rappel avance au plus de 2 s par seconde : l'écart restant permet donc de savoir combien de temps
on peut attendre sans risquer de dépasser l'heure (voir `decide`).

Les flottes d'exploration, de fouille, de forage, de piratage et d'espionnage ne sont jamais proposées (choix du joueur).
Fichier `<data_dir>/fleets.json` :
  - `settings` : {"margin": secondes d'avance du clic} ;
  - `snapshot` : dernier relevé {"read_at", "fleets": [flotte]} ;
  - `recalls` : [{"id", "fleet": {"key", "label", "arrival_at"}, "at": heure de retour voulue (epoch), "state": "pending"|
    "done"|"failed"|"abandoned", "note", "next_check": prochain contrôle (epoch), "reads": [[t, arrivée prévue]]}].
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .planning import TIMEZONE

FILE_NAME = "fleets.json"
DEFAULT_MARGIN = 5
MAX_MARGIN = 120
MAX_RECALLS = 100
ABANDON_SECONDS = 120  # heure voulue dépassée de plus de 2 min sans rappel : abandonné
HOLD_SECONDS = 90  # à moins de 90 s du clic, le bot reste dans la fenêtre de retour et surveille la durée affichée
CHECK_LEAD_SECONDS = 60  # un contrôle intermédiaire est programmé 1 min avant la fin de l'attente estimée
STATES = ("pending", "done", "failed", "abandoned")
# Missions qui ne sont jamais rappelées par le bot (réglées dans leurs propres onglets).
EXCLUDED = re.compile(r"explor|fouille|forage|piratage|espionn|sonde|drone", re.I)


class FleetError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def when(ts: float) -> str:
    """Date et heure lisibles (heure de Paris), ex. « 22/11 à 21:53:10 »."""
    return datetime.fromtimestamp(ts, TIMEZONE).strftime("%d/%m à %H:%M:%S")


def parse_local(text) -> float:
    """« 2026-11-22T21:53:10 » (heure de Paris, champ date/heure de la page) → epoch. ValueError si illisible."""
    if not isinstance(text, str):
        raise ValueError("date illisible")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=TIMEZONE).timestamp()
        except ValueError:
            continue
    raise ValueError("date illisible")


def parse_clock(text: str | None) -> int | None:
    """« 01:20:00 », « 2j 01:20:00 », « 1 h 2 min 3 s » → secondes ; None si illisible."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(?:(\d+)\s*j\s*)?(\d+):(\d{2}):(\d{2})\s*", text)
    if m:
        return int(m[1] or 0) * 86400 + int(m[2]) * 3600 + int(m[3]) * 60 + int(m[4])
    parts = re.findall(r"(\d+)\s*(j|h|min|m|s)\b", text.lower())
    if not parts:
        return None
    return sum(int(n) * {"j": 86400, "h": 3600, "min": 60, "m": 60, "s": 1}[u] for n, u in parts)


def fleet_key(kind: str, origin: str, destination: str) -> str:
    return f"{kind}|{origin}|{destination}"


def build_fleet(raw: dict, now: float) -> dict:
    """Carte lue dans les Capteurs → flotte du relevé (heure d'arrivée absolue, exclusion, identifiant du relevé)."""
    left = raw.get("left")
    arrival = now + left if isinstance(left, (int, float)) else None
    origin, destination = raw.get("origin") or {}, raw.get("destination") or {}
    key = fleet_key(raw.get("kind", ""), origin.get("coords", ""), destination.get("coords", ""))
    fid = hashlib.sha1(f"{key}|{round(arrival) if arrival else ''}|{raw.get('units', '')}".encode()).hexdigest()[:8]
    return {"id": fid, "index": raw.get("index"), "key": key, "kind": raw.get("kind", ""), "units": raw.get("units", ""), "origin": origin,
            "destination": destination, "left": left, "arrival_at": arrival, "can_recall": bool(raw.get("can_recall")),
            "excluded": bool(EXCLUDED.search(raw.get("text", "") or raw.get("kind", ""))), "text": (raw.get("text") or "")[:300]}


def match_fleet(fleets: list[dict], key: str, arrival_at: float | None) -> dict | None:
    """Flotte du relevé qui correspond à celle d'un rappel : même trajet, arrivée à moins de 2 min près (la plus proche)."""
    same = [f for f in fleets if f["key"] == key and f.get("arrival_at") and arrival_at is not None
            and abs(f["arrival_at"] - arrival_at) <= 120]
    return min(same, key=lambda f: abs(f["arrival_at"] - arrival_at)) if same else None


def estimate_rate(reads: list[list[float]]) -> float:
    """Vitesse (secondes d'arrivée prévue gagnées par seconde) mesurée sur les deux dernières lectures, entre 1 et 2."""
    if len(reads) >= 2 and reads[-1][0] - reads[-2][0] >= 60:
        rate = (reads[-1][1] - reads[-2][1]) / (reads[-1][0] - reads[-2][0])
        return min(2.0, max(1.0, rate))
    return 2.0  # cas le plus rapide : on ne risque jamais de dépasser l'heure


def decide(now: float, duration: float, target: float, margin: float, reads: list[list[float]]) -> tuple[str, float]:
    """Que faire avec la durée de retour `duration` affichée maintenant ?

    Arrivée prévue si on rappelle maintenant : A = now + duration. On clique quand A + marge atteint l'heure voulue.
    Renvoie ("click", 0), ("hold", écart) (rester dans la fenêtre et surveiller), ("wait", prochain contrôle epoch) ou
    ("late", écart) : même un rappel immédiat arriverait après l'heure voulue."""
    arrival = now + duration
    gap = target - margin - arrival
    if gap <= 0:
        return ("late", -gap) if -gap > margin + 30 else ("click", 0.0)
    rate = estimate_rate(reads)
    if gap / rate <= HOLD_SECONDS:
        return "hold", gap
    return "wait", now + gap / rate - CHECK_LEAD_SECONDS


class FleetStore:
    """Relevé des flottes, rappels programmés et réglage de la marge, gardés dans `fleets.json`."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._settings = {"margin": DEFAULT_MARGIN}
        self._snapshot: dict = {"read_at": None, "fleets": []}
        self._recalls: list[dict] = []
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        margin = (saved.get("settings") or {}).get("margin")
        if isinstance(margin, int) and not isinstance(margin, bool) and 0 <= margin <= MAX_MARGIN:
            self._settings["margin"] = margin
        snap = saved.get("snapshot")
        if isinstance(snap, dict) and isinstance(snap.get("fleets"), list):
            self._snapshot = {"read_at": snap.get("read_at"), "fleets": [f for f in snap["fleets"] if isinstance(f, dict)]}
        keys = {"id", "fleet", "at", "state"}
        self._recalls = [r for r in saved.get("recalls") or [] if isinstance(r, dict) and keys <= r.keys()
                         and r["state"] in STATES and isinstance(r["fleet"], dict)]

    @property
    def persistent(self) -> bool:
        return self._path is not None

    # ------------------------------------------------------------------ lecture
    def margin(self) -> int:
        return self._settings["margin"]

    def snapshot(self) -> dict:
        return copy.deepcopy(self._snapshot)

    def recalls(self) -> list[dict]:
        return copy.deepcopy(sorted(self._recalls, key=lambda r: r["at"]))

    def pending(self) -> list[dict]:
        return [r for r in self.recalls() if r["state"] == "pending"]

    def next_check(self) -> float | None:
        checks = [r.get("next_check") or 0.0 for r in self._recalls if r["state"] == "pending"]
        return min(checks) if checks else None

    # ------------------------------------------------------------------ modification
    def set_margin(self, margin) -> int:
        if isinstance(margin, bool) or not isinstance(margin, int) or not 0 <= margin <= MAX_MARGIN:
            raise FleetError([f"Marge : un nombre entier de secondes de 0 à {MAX_MARGIN}."])
        self._settings["margin"] = margin
        self._save()
        return margin

    def set_snapshot(self, fleets: list[dict], now: float | None = None) -> None:
        self._snapshot = {"read_at": time.time() if now is None else now, "fleets": copy.deepcopy(fleets)}
        self._save()

    def add(self, fleet_id, at_text, now: float | None = None) -> dict:
        """Programme le retour d'une flotte du dernier relevé. FleetError (messages en français) s'il est impossible."""
        now = time.time() if now is None else now
        fleet = next((f for f in self._snapshot["fleets"] if f["id"] == fleet_id), None)
        errors: list[str] = []
        if fleet is None:
            errors.append("Flotte inconnue : relevez de nouveau les flottes en vol.")
        elif fleet["excluded"] or not fleet["can_recall"]:
            errors.append("Cette flotte ne peut pas être rappelée par le bot.")
        try:
            at = parse_local(at_text)
        except ValueError:
            errors.append("Choisissez la date et l'heure de retour.")
            at = 0.0
        if not errors and at <= now + self._settings["margin"]:
            errors.append("L'heure de retour est déjà passée ou trop proche.")
        if not errors and any(r["state"] == "pending" and r["fleet"]["key"] == fleet["key"]
                              and abs((r["fleet"].get("arrival_at") or 0) - (fleet["arrival_at"] or 0)) <= 120 for r in self._recalls):
            errors.append("Un retour est déjà programmé pour cette flotte : supprimez-le d'abord.")
        if len([r for r in self._recalls if r["state"] == "pending"]) >= MAX_RECALLS:
            errors.append(f"{MAX_RECALLS} retours programmés au plus.")
        if errors:
            raise FleetError(errors)
        label = f"{fleet['kind']} {fleet['origin'].get('coords', '')} → {fleet['destination'].get('coords', '')}".strip()
        recall = {"id": secrets.token_hex(4), "fleet": {"key": fleet["key"], "label": label, "arrival_at": fleet["arrival_at"],
                                                        "units": fleet["units"]},
                  "at": at, "state": "pending", "note": "", "next_check": now, "reads": []}
        self._recalls.append(recall)
        self._save()
        return copy.deepcopy(recall)

    def remove(self, recall_id: str) -> bool:
        kept = [r for r in self._recalls if r["id"] != recall_id]
        if len(kept) == len(self._recalls):
            return False
        self._recalls = kept
        self._save()
        return True

    def update(self, recall_id: str, **changes) -> None:
        for r in self._recalls:
            if r["id"] == recall_id:
                r.update(changes)
                r["reads"] = r.get("reads", [])[-5:]
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".fleets-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"settings": self._settings, "snapshot": self._snapshot, "recalls": self._recalls}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
