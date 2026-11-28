"""Routes de l'interface pour le pilotage par Discord : lire l'état, enregistrer les réglages, oublier le jeton.

Ces réglages sont ceux de l'application entière (pas d'une instance) : les routes ne passent donc pas par `bot`.
Le jeton se saisit mais ne se relit jamais (voir `discord_store.py`).
"""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from .discord_commands import CommandRunner
from .discord_gateway import DiscordService
from .discord_store import DiscordSettingsError, DiscordStore
from .instance_manager import InstanceManager

NO_STORE = {"Cache-Control": "no-store"}


class DiscordRequest(BaseModel):
    # Clés absentes : valeurs inchangées. Un jeton absent ou vide garde le jeton actuel.
    enabled: bool | None = None
    token: SecretStr | None = Field(default=None, max_length=200)
    confirm: bool | None = None


def build_discord(data_dir: str | None, manager: InstanceManager) -> tuple[DiscordStore, DiscordService]:
    store = DiscordStore(data_dir)
    runner = CommandRunner(manager, lambda: store.get()["confirm"])
    service = DiscordService(store, runner)
    manager.report_sink = service.notify  # le bilan de fin de journée part par message privé
    return store, service


def install_discord(app: FastAPI, store: DiscordStore, service: DiscordService, check_origin: Callable[[Request], None]) -> None:
    def payload() -> dict:
        return {"settings": store.get(), "status": service.status()}

    @app.get("/api/discord")
    async def api_discord():
        return JSONResponse(payload(), headers=NO_STORE)

    @app.put("/api/discord")
    async def api_discord_put(body: DiscordRequest, request: Request):
        check_origin(request)
        patch = body.model_dump(exclude_none=True)
        if "token" in patch:
            patch["token"] = body.token.get_secret_value()
        try:
            store.update(patch)
        except DiscordSettingsError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        await service.apply()  # le client repart avec les nouveaux réglages
        return JSONResponse(payload(), headers=NO_STORE)

    @app.delete("/api/discord/token")
    async def api_discord_forget(request: Request):
        check_origin(request)
        try:
            store.forget_token()
        except DiscordSettingsError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        await service.apply()
        return JSONResponse(payload(), headers=NO_STORE)
