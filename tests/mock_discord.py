"""Faux serveur Discord pour les tests : API REST minimale + passerelle WebSocket, tout en local.

Ce qu'il fait : donne l'application (identifiant, propriétaire), la liste des serveurs invités, retient les commandes déclarées, envoie les interactions que le test
place dans `state.outbox` et note les réponses du bot (`callbacks`, `edits`). Ce n'est PAS le vrai Discord : la validation
finale se fait avec un vrai jeton (voir README, section « Piloter le bot par Discord »).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse

APP_ID = "111111111111111111"
OWNER_ID = "222222222222222222"  # propriétaire de l'application
GUILD_ID = "333333333333333333"  # serveur privé où le bot est invité


@dataclass
class DiscordState:
    port: int = 0
    reject_token: bool = False  # simule un jeton refusé (401)
    close_code: int | None = None  # ferme la passerelle avec ce code après l'identification (ex. 4004)
    guilds: list[str] = field(default_factory=lambda: [GUILD_ID])  # serveurs où le bot est invité
    team_members: list[str] = field(default_factory=list)  # si rempli : application d'équipe
    refused_guilds: set[str] = field(default_factory=set)  # serveurs qui répondent 403 (portée manquante)
    registered: list[dict] = field(default_factory=list)  # commandes déclarées
    registered_on: list[str] = field(default_factory=list)  # serveurs où elles l'ont été
    guild_events: list[str] = field(default_factory=list)  # serveurs « rejoints » à signaler sur la passerelle
    identifies: list[dict] = field(default_factory=list)
    outbox: list[dict] = field(default_factory=list)  # interactions à envoyer au bot
    callbacks: list[dict] = field(default_factory=list)  # {"id", "body"}
    edits: list[dict] = field(default_factory=list)  # contenus de « @original »


def create_mock_discord(state: DiscordState) -> FastAPI:
    app = FastAPI()

    @app.get("/gateway/bot")
    async def gateway():
        return {"url": f"ws://127.0.0.1:{state.port}/gw"}

    @app.get("/oauth2/applications/@me")
    async def application():
        if state.reject_token:
            return _unauthorized()
        app = {"id": APP_ID, "owner": {"id": OWNER_ID}}
        if state.team_members:
            app = {"id": APP_ID, "owner": {"id": "888888888888888888"},
                   "team": {"members": [{"user": {"id": m}} for m in state.team_members]}}
        return app

    @app.get("/users/@me/guilds")
    async def guilds():
        return [{"id": g} for g in state.guilds]

    @app.put("/applications/{app_id}/guilds/{guild_id}/commands")
    async def commands(app_id: str, guild_id: str, request: Request):
        if guild_id in state.refused_guilds:
            return JSONResponse({"message": "Missing Access", "code": 50001}, status_code=403)
        state.registered = await request.json()
        state.registered_on.append(guild_id)
        return state.registered

    @app.post("/interactions/{interaction_id}/{token}/callback")
    async def callback(interaction_id: str, token: str, request: Request):
        state.callbacks.append({"id": interaction_id, "body": await request.json()})
        return {}

    @app.patch("/webhooks/{app_id}/{token}/messages/@original")
    async def edit(app_id: str, token: str, request: Request):
        state.edits.append({"token": token, "body": await request.json()})
        return {}

    @app.websocket("/gw/")
    async def gateway_socket(ws: WebSocket):
        await ws.accept()
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 30000}})
        state.identifies.append(await ws.receive_json())
        if state.close_code is not None:
            await ws.close(code=state.close_code)
            return
        await ws.send_json({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "s1", "resume_gateway_url": f"ws://127.0.0.1:{state.port}/gw"}})
        seq = 1
        closing = asyncio.create_task(ws.receive_text())  # le bot parle (cœur) ou s'en va : on le voit ici
        try:
            while True:
                if closing.done():
                    if closing.exception() is not None:  # le bot s'est déconnecté
                        break
                    closing = asyncio.create_task(ws.receive_text())  # simple battement de cœur
                while state.guild_events:
                    seq += 1
                    await ws.send_json({"op": 0, "s": seq, "t": "GUILD_CREATE", "d": {"id": state.guild_events.pop(0)}})
                while state.outbox:
                    seq += 1
                    await ws.send_json({"op": 0, "s": seq, "t": "INTERACTION_CREATE", "d": state.outbox.pop(0)})
                await asyncio.sleep(0.02)
        finally:
            closing.cancel()

    return app


def _unauthorized():
    return JSONResponse({"message": "401: Unauthorized", "code": 0}, status_code=401)
