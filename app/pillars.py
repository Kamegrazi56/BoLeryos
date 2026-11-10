"""Piliers programmés : ouverture et fermeture des Piliers d'une planète à une date et une heure précises.

Règles du jeu (wiki « Fonctionnement et activation des Piliers », vérifiées sur le vrai jeu le 2026-11-10) :
  - le changement devient effectif 1 heure après le clic ;
  - il faut ensuite attendre 23 heures avant de pouvoir rechanger : deux changements effectifs d'une même planète sont donc
    séparés d'au moins 24 heures ;
  - au plus 2 planètes ont leurs Piliers désactivés en même temps. Le jeu compte au moment du CLIC : une réouverture en
    cours libère déjà la place (vu sur le vrai jeu : Mnesis fermée pendant la réouverture d'Aurore-Bis, Opale déjà fermée).

Le joueur choisit l'heure où le changement doit être EFFECTIF ; le bot clique 1 heure avant. Si le bot n'a pas pu cliquer
(OFF, pause, déconnecté) et que le retard dépasse 1 heure, le changement est abandonné.

Fichier `<data_dir>/pillars.json` :
  - `changes` : [{"id", "planet", "action": "open"|"close", "at": heure effective (epoch), "state": "pending"|"done"|
    "failed"|"abandoned", "note"}] ;
  - `status` : état lu dans la fenêtre « Gérer les Piliers » : {"read_at", "planets": {nom: {"closed", "pending_to":
    "open"|"close"|None, "effective_at"}}} ;
  - `history` : heure effective du dernier changement connu de chaque planète (pour le délai de 24 heures).
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .planning import TIMEZONE

FILE_NAME = "pillars.json"
EFFECT_SECONDS = 3600  # le changement devient effectif 1 heure après le clic
BETWEEN_SECONDS = 24 * 3600  # 1 h d'effet + 23 h d'attente : écart minimal entre deux changements effectifs
MAX_CLOSED = 2  # planètes aux Piliers désactivés en même temps
LATE_SECONDS = 3600  # clic en retard de plus d'1 heure (bot OFF…) : changement abandonné
MAX_CHANGES = 200
ACTIONS = {"open": "Ouvrir", "close": "Fermer"}
STATES = ("pending", "done", "failed", "abandoned")


class PillarError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def when(ts: float) -> str:
    """Date et heure lisibles (heure de Paris), ex. « 10/11 à 21:53 »."""
    return datetime.fromtimestamp(ts, TIMEZONE).strftime("%d/%m à %H:%M")


def parse_local(text) -> float:
    """« 2026-11-10T21:53 » (heure de Paris, champ date/heure de la page) → epoch. ValueError si illisible."""
    if not isinstance(text, str):
        raise ValueError("date illisible")
    return datetime.strptime(text.strip()[:16], "%Y-%m-%dT%H:%M").replace(tzinfo=TIMEZONE).timestamp()


def click_time(change: dict) -> float:
    return change["at"] - EFFECT_SECONDS


def _initial(status: dict, history: dict) -> tuple[dict, dict, dict]:
    """(état logique final, état compté pour la limite, dernier changement effectif) de chaque planète connue.
    Un changement en cours compte déjà pour la limite (le jeu compte au clic) et fixe le dernier changement effectif."""
    logical, counted, last = {}, {}, dict(history)
    for name, p in (status.get("planets") or {}).items():
        closed = bool(p.get("closed"))
        pending = p.get("pending_to")
        if pending == "open":
            closed = False
        elif pending == "close":
            closed = True
        logical[name] = counted[name] = closed
        if pending and p.get("effective_at"):
            last[name] = max(last.get(name, 0.0), p["effective_at"])
    return logical, counted, last


def problems(changes: list[dict], status: dict, history: dict, now: float) -> dict[str, str]:
    """Rejoue les changements en attente dans l'ordre : {id: raison} pour ceux que le jeu refuserait.
    Vérifie l'état (déjà ouverte / déjà fermée), le délai de 24 heures et la limite de 2 planètes fermées."""
    logical, counted, last = _initial(status, history)
    out: dict[str, str] = {}
    for c in sorted((c for c in changes if c["state"] == "pending"), key=lambda c: c["at"]):
        p, closed = c["planet"], logical.get(c["planet"], False)
        if c["action"] == "open" and not closed:
            out[c["id"]] = f"les Piliers de {p} seront déjà ouverts à ce moment-là"
            continue
        if c["action"] == "close" and closed:
            out[c["id"]] = f"les Piliers de {p} seront déjà fermés à ce moment-là"
            continue
        previous = last.get(p)
        if previous is not None and c["at"] < previous + BETWEEN_SECONDS:
            out[c["id"]] = (f"il faut 24 h entre deux changements des Piliers de {p} (1 h d'effet puis 23 h d'attente) : "
                            f"au plus tôt le {when(previous + BETWEEN_SECONDS)}")
            continue
        if c["action"] == "close":
            others = sum(1 for name, shut in counted.items() if shut and name != p)
            if others >= MAX_CLOSED:
                shut = ", ".join(sorted(name for name, s in counted.items() if s and name != p))
                out[c["id"]] = f"le jeu n'autorise que {MAX_CLOSED} planètes aux Piliers fermés en même temps ({shut} le seront déjà)"
                continue
        logical[p] = counted[p] = c["action"] == "close"
        last[p] = c["at"]
    return out


class PillarStore:
    """Changements programmés, état des Piliers lu dans le jeu et historique, gardés dans `pillars.json`."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._changes: list[dict] = []
        self._status: dict = {"read_at": None, "planets": {}}
        self._history: dict[str, float] = {}
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        keys = {"id", "planet", "action", "at", "state"}
        self._changes = [c for c in saved.get("changes") or [] if isinstance(c, dict) and keys <= c.keys()
                         and c["action"] in ACTIONS and c["state"] in STATES]
        if isinstance(saved.get("status"), dict) and isinstance(saved["status"].get("planets"), dict):
            self._status = {"read_at": saved["status"].get("read_at"), "planets": saved["status"]["planets"]}
        if isinstance(saved.get("history"), dict):
            self._history = {k: float(v) for k, v in saved["history"].items() if isinstance(v, (int, float))}

    @property
    def persistent(self) -> bool:
        return self._path is not None

    # ------------------------------------------------------------------ lecture
    def changes(self) -> list[dict]:
        return copy.deepcopy(sorted(self._changes, key=lambda c: c["at"]))

    def status(self) -> dict:
        return copy.deepcopy(self._status)

    def history(self) -> dict:
        return dict(self._history)

    def pending(self) -> list[dict]:
        return [c for c in self.changes() if c["state"] == "pending"]

    def next_click(self) -> float | None:
        clicks = [click_time(c) for c in self._changes if c["state"] == "pending"]
        return min(clicks) if clicks else None

    def problems(self, now: float | None = None) -> dict[str, str]:
        return problems(self._changes, self._status, self._history, time.time() if now is None else now)

    # ------------------------------------------------------------------ modification
    def add(self, planet, action, at_text, known_planets: list[str] | None = None, now: float | None = None) -> dict:
        """Programme un changement. PillarError (messages en français) s'il est impossible."""
        now = time.time() if now is None else now
        errors: list[str] = []
        if not isinstance(planet, str) or not planet.strip():
            errors.append("Choisissez la planète.")
        elif known_planets and planet.strip() not in known_planets:
            errors.append(f"Planète inconnue ({planet}) : relancez le scan des planètes.")
        if action not in ACTIONS:
            errors.append("Choisissez « Ouvrir » ou « Fermer ».")
        try:
            at = parse_local(at_text)
        except ValueError:
            errors.append("Choisissez la date et l'heure.")
            at = 0.0
        if not errors and at - EFFECT_SECONDS < now:
            errors.append(f"Trop tôt : le changement prend 1 h après le clic du bot, il peut être effectif au plus tôt le "
                          f"{when(now + EFFECT_SECONDS + 60)}.")
        if len([c for c in self._changes if c["state"] == "pending"]) >= MAX_CHANGES:
            errors.append(f"{MAX_CHANGES} changements programmés au plus.")
        if errors:
            raise PillarError(errors)
        change = {"id": secrets.token_hex(4), "planet": planet.strip(), "action": action, "at": at, "state": "pending", "note": ""}
        trial = [*self._changes, change]
        problem = problems(trial, self._status, self._history, now).get(change["id"])
        if problem:
            raise PillarError([f"Impossible : {problem}."])
        self._changes = trial
        self._save()
        return copy.deepcopy(change)

    def remove(self, change_id: str) -> bool:
        """Supprime un changement (programmé, fait ou non). Faux s'il n'existe pas."""
        kept = [c for c in self._changes if c["id"] != change_id]
        if len(kept) == len(self._changes):
            return False
        self._changes = kept
        self._save()
        return True

    def finish(self, change_id: str, state: str, note: str, effective_at: float | None = None) -> None:
        """Résultat d'un changement : fait (effectif à `effective_at`), échoué ou abandonné."""
        for c in self._changes:
            if c["id"] == change_id:
                c["state"], c["note"] = state, note
                if state == "done" and effective_at is not None:
                    self._history[c["planet"]] = effective_at
        self._save()

    def set_status(self, planets: dict, now: float | None = None) -> None:
        """État lu dans la fenêtre « Gérer les Piliers » ; un changement en cours fixe l'historique de la planète."""
        now = time.time() if now is None else now
        self._status = {"read_at": now, "planets": copy.deepcopy(planets)}
        for name, p in planets.items():
            if p.get("pending_to") and p.get("effective_at"):
                self._history[name] = max(self._history.get(name, 0.0), p["effective_at"])
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".pillars-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"changes": self._changes, "status": self._status, "history": self._history}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
