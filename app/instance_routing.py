"""Aiguillage des requêtes vers l'instance de jeu voulue, sans toucher aux routes existantes.

Les ~70 routes de `create_app` parlent à « le bot » (`bot.status()`, `bot.scheduler…`). Plutôt que de les réécrire :
  - `/api/i/<instance>/…` et `/ws/i/<instance>/…` sont réécrits en `/api/…` et `/ws/…` par `InstanceScopeMiddleware`, qui
    retient l'instance visée dans une variable de contexte (propre à chaque requête) ;
  - `BotProxy` remplace le bot dans les routes : chaque accès à un attribut est renvoyé vers le `BotSession` de l'instance
    visée (la première instance quand aucune n'est nommée, comme avant l'arrivée des instances).
`<instance>` vaut un identifiant d'instance, ou `compte` pour la session de connexion (identifiants, liste des serveurs).
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.responses import JSONResponse

from .instance_manager import LOBBY_ID, InstanceManager
from .instances import ID_RE, MAX_ID_LENGTH

current_instance: ContextVar[str | None] = ContextVar("current_instance", default=None)

_PREFIXES = (("/api/i/", "/api/"), ("/ws/i/", "/ws/"))


def _valid_id(instance_id: str) -> bool:
    return instance_id == LOBBY_ID or (len(instance_id) <= MAX_ID_LENGTH and bool(ID_RE.match(instance_id)))


class BotProxy:
    """Se comporte comme le `BotSession` de l'instance visée par la requête en cours."""

    def __init__(self, manager: InstanceManager) -> None:
        object.__setattr__(self, "_manager", manager)

    def target(self):
        """Le bot visé, à garder pour toute la durée d'une connexion WebSocket (ne change pas en cours de route)."""
        return self._manager.resolve(current_instance.get())

    def __getattr__(self, name: str):
        return getattr(self.target(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self.target(), name, value)


class InstanceScopeMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope["path"]
            for prefix, target in _PREFIXES:
                if not path.startswith(prefix):
                    continue
                instance_id, sep, rest = path[len(prefix):].partition("/")
                if not sep or not _valid_id(instance_id):
                    await self._not_found(scope, receive, send)
                    return
                new_path = target + rest
                scope = {**scope, "path": new_path, "raw_path": new_path.encode()}
                token = current_instance.set(instance_id)
                try:
                    await self.app(scope, receive, send)
                finally:
                    current_instance.reset(token)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _not_found(scope, receive, send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": "Instance inconnue."}, status_code=404)(scope, receive, send)
