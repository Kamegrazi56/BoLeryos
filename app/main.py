"""Interface web du bot : connexion, vision du jeu et choix des actions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .bot import BotSession
from .config import Settings, load_settings

STATIC_DIR = Path(__file__).parent / "static"
NO_STORE = {"Cache-Control": "no-store"}
WS_TICK = 0.25  # fréquence de rafraîchissement du statut et du journal (secondes)


class NoStoreStaticFiles(StaticFiles):
    """Fichiers JS/CSS jamais mis en cache : après une mise à jour du bot, le navigateur recharge les nouveaux."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


class LoginRequest(BaseModel):
    # Libellé « identifiant » : le formulaire du jeu attend une adresse e-mail.
    identifier: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=256)


class BasicAuthMiddleware:
    """Protège toute l'interface (HTTP et WebSocket) si UI_USER / UI_PASSWORD sont définis."""

    def __init__(self, app, user: str, password: str) -> None:
        self.app = app
        self._expected = (user, password)

    def _authorized(self, headers: dict[bytes, bytes]) -> bool:
        raw = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = raw.partition(" ")
        if scheme.lower() != "basic":
            return False
        try:
            user, _, password = base64.b64decode(token).decode("utf-8").partition(":")
        except (binascii.Error, UnicodeDecodeError):
            return False
        return secrets.compare_digest(user, self._expected[0]) & secrets.compare_digest(
            password, self._expected[1]
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not self._authorized(dict(scope["headers"])):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            response = JSONResponse(
                {"detail": "Authentification requise."},
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="BoLeryos"'},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _same_origin(headers, host: str | None) -> bool:
    """Refuse les requêtes émises depuis un autre site (CSRF / détournement WebSocket)."""
    origin = headers.get("origin")
    return origin is None or urlsplit(origin).netloc == host


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    bot = BotSession(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await bot.shutdown()

    app = FastAPI(title="BoLeryos", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bot = bot

    # --- API -----------------------------------------------------------------
    @app.post("/api/login")
    async def api_login(body: LoginRequest, request: Request):
        if not _same_origin(request.headers, request.headers.get("host")):
            raise HTTPException(403, "Origine refusée.")
        status = await bot.login(body.identifier, body.password.get_secret_value())
        return JSONResponse(status, headers=NO_STORE)

    @app.post("/api/logout")
    async def api_logout(request: Request):
        if not _same_origin(request.headers, request.headers.get("host")):
            raise HTTPException(403, "Origine refusée.")
        return JSONResponse(await bot.logout(), headers=NO_STORE)

    @app.get("/api/status")
    async def api_status():
        return JSONResponse({**bot.status(), "logs": bot.logs()}, headers=NO_STORE)

    @app.websocket("/ws/vision")
    async def ws_vision(ws: WebSocket):
        if not _same_origin(ws.headers, ws.headers.get("host")):
            await ws.close(code=1008)
            return
        await ws.accept()
        bot.viewer_joined()
        bot.start_in_background()  # le navigateur affiche le site même sans connexion

        async def reader() -> None:
            """Reçoit les ordres de l'interface (contrôle manuel, taille d'affichage, souris, clavier)."""
            while True:
                raw = await ws.receive_text()
                if len(raw) > 4000:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await bot.handle_input(msg)

        reader_task = asyncio.create_task(reader())
        last_frame = -1
        try:
            while not reader_task.done():  # la tâche se termine quand le client part
                frame_id, image = bot.frame()
                payload = {**bot.status(), "logs": bot.logs()}
                if frame_id != last_frame:  # l'image n'est envoyée que si elle a changé
                    payload["image"] = image
                    last_frame = frame_id
                await ws.send_json(payload)
                await asyncio.sleep(WS_TICK)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            reader_task.cancel()
            await asyncio.gather(reader_task, return_exceptions=True)
            bot.viewer_left()

    # --- Pages ---------------------------------------------------------------
    def page(name: str):
        async def handler():
            return FileResponse(STATIC_DIR / name, headers=NO_STORE)

        return handler

    app.add_api_route("/", page("login.html"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/vision", page("vision.html"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/actions", page("actions.html"), methods=["GET"], include_in_schema=False)
    app.mount("/static", NoStoreStaticFiles(directory=STATIC_DIR), name="static")

    if settings.ui_user and settings.ui_password:
        app.add_middleware(BasicAuthMiddleware, user=settings.ui_user, password=settings.ui_password)
    return app


app = create_app()
