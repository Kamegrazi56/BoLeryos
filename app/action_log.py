"""Journal en mémoire des actions que la page du jeu envoie par WebSocket, et des réponses du serveur.

Sert à écrire le moteur « api » d'après ce que le vrai client fait (forme exacte des messages, calcul des coûts et des
durées), sans rien deviner. Lecture seule : on observe les images WebSocket de la page, on n'envoie rien.

Ne garde jamais le jeton : le message `AUTH_INIT` est ignoré. Les valeurs conservées sont des données de jeu (identifiants de
planète, quantités, coûts, heures). Mémoire bornée ; effacée à la fermeture de la session.
"""

from __future__ import annotations

import json
import time
from collections import deque

MAX_ENTRIES = 60
MAX_REPLY_CHARS = 2000
IGNORED_TYPES = {"AUTH_INIT", "STATE_UPDATE", "PING", "PONG"}


class ActionLog:
    def __init__(self) -> None:
        self._entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
        self._by_request: dict[str, dict] = {}

    def clear(self) -> None:
        self._entries.clear()
        self._by_request.clear()

    @staticmethod
    def _parse(payload: str | bytes) -> dict | None:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def sent(self, payload: str | bytes) -> None:
        data = self._parse(payload)
        if data is None or data.get("type") in IGNORED_TYPES or data.get("type") != "ACTION":
            return
        entry = {"at": time.time(), "action": data.get("action"), "requestId": data.get("requestId"), "payload": data.get("payload"), "reply": None}
        self._entries.append(entry)
        if isinstance(entry["requestId"], str):
            self._by_request[entry["requestId"]] = entry
            while len(self._by_request) > MAX_ENTRIES * 2:
                self._by_request.pop(next(iter(self._by_request)))

    def received(self, payload: str | bytes) -> None:
        data = self._parse(payload)
        if data is None or data.get("type") in IGNORED_TYPES:
            return
        entry = self._by_request.get(data.get("requestId")) if isinstance(data.get("requestId"), str) else None
        if entry is not None:
            entry["reply"] = json.dumps(data, ensure_ascii=False)[:MAX_REPLY_CHARS]

    def entries(self, only_writes: bool = True) -> list[dict]:
        """Actions vues, les plus récentes d'abord. Par défaut sans les lectures (`get_…`)."""
        out = [e for e in reversed(self._entries) if not (only_writes and str(e["action"]).startswith("get_"))]
        return [dict(e) for e in out]
