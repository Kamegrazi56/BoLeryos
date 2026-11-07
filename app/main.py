"""Interface web du bot : connexion, vision du jeu et choix des actions."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .bot import BotSession
from .config import Settings, load_settings
from .components import COMPONENTS
from .constructions import BUILDINGS, ConstructionError
from .populations import POPULATIONS, PopulationError
from .researches import RESEARCHES, ResearchError
from .features import FeaturesError
from .preferences import MAX_UNIVERSE_LENGTH, PreferencesError
from .quantities import LineError
from .explorations import METHODS, PILLAR_UNITS, ExplorationError
from .excavations import DURATIONS as EXCAVATION_DURATIONS
from .excavations import MAX_TEAM as EXCAVATION_MAX_TEAM
from .excavations import UNITS as EXCAVATION_UNITS
from .excavations import ExcavationError
from .hackings import HackingError
from .planning import DAY_LABELS as PLANNING_DAY_LABELS
from .planning import PlanningError
from .planning import describe as planning_describe
from .planning import state as planning_state
from .spying import RESOURCES as SPY_RESOURCES
from .spying import SpyingError

STATIC_DIR = Path(__file__).parent / "static"
NO_STORE = {"Cache-Control": "no-store"}
WS_TICK = 0.25  # délai maximal entre deux envois du statut et du journal (secondes)
MIN_FRAME_INTERVAL = 0.04  # écart minimal entre deux images envoyées


NOVNC_DIR = Path("/usr/share/novnc")


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


class PreferencesRequest(BaseModel):
    # Clés absentes : valeurs inchangées (la page n'envoie que ce qu'elle modifie).
    universe: str | None = Field(default=None, max_length=MAX_UNIVERSE_LENGTH)
    login_delay: dict | None = None


class FeatureRequest(BaseModel):
    # Clés absentes : valeurs inchangées.
    enabled: bool | None = None
    min_minutes: float | None = None
    max_minutes: float | None = None


class ConstructionLine(BaseModel):
    building: str = Field(max_length=60)
    level: int = Field(ge=0, le=100000)


class ConstructionsRequest(BaseModel):
    scope: str = Field(default="all", max_length=10)
    planet: str = Field(default="", max_length=200)
    queue: list[ConstructionLine] = Field(default_factory=list, max_length=200)


class ResearchLine(BaseModel):
    research: str = Field(max_length=60)
    level: int = Field(ge=0, le=100000)


class ResearchesRequest(BaseModel):
    planet: str = Field(default="", max_length=200)
    queue: list[ResearchLine] = Field(default_factory=list, max_length=200)


class PopulationLine(BaseModel):
    population: str = Field(max_length=60)
    mode: str = Field(max_length=10)
    amount: int = Field(default=0, ge=0, le=10**12)
    planet: str = Field(default="", max_length=200)
    repeat: bool = True


class PopulationsRequest(BaseModel):
    queue: list[PopulationLine] = Field(default_factory=list, max_length=500)


class QuantityLine(BaseModel):
    item: str = Field(max_length=200)
    mode: str = Field(max_length=10)
    amount: int = Field(default=0, ge=0, le=10**12)
    planets: list[str] = Field(default_factory=list, max_length=200)
    repeat: bool = True


class QuantityRequest(BaseModel):
    queue: list[QuantityLine] = Field(default_factory=list, max_length=500)


class ExplorationRequest(BaseModel):
    method: str = Field(default="PILLARS", max_length=10)
    composition: str = Field(default="model", max_length=10)
    model: str = Field(default="", max_length=200)
    units: dict[str, int] = Field(default_factory=dict, max_length=60)
    copies: int = Field(default=1, ge=0, le=100)
    duration: int = Field(default=1, ge=0, le=100)
    planets: list[str] = Field(default_factory=list, max_length=200)
    rhythm: str = Field(default="fixed", max_length=10)
    per_pass: int = Field(default=1, ge=0, le=1000)


class ExcavationLine(BaseModel):
    planet: str = Field(default="", max_length=200)
    units: dict[str, int] = Field(default_factory=dict, max_length=10)
    duration: int = Field(default=3, ge=0, le=100)
    slots: int = Field(default=1, ge=0, le=100)
    mira: bool = False


class ExcavationRequest(BaseModel):
    lines: list[ExcavationLine] = Field(default_factory=list, max_length=50)
    delay_min: int | None = Field(default=None, ge=0, le=100_000)
    delay_max: int | None = Field(default=None, ge=0, le=100_000)


class HackingRequest(BaseModel):
    planet: str = Field(default="", max_length=200)
    delay_min: int | None = Field(default=None, ge=0, le=100_000)
    delay_max: int | None = Field(default=None, ge=0, le=100_000)


class PlanningSlot(BaseModel):
    start: str = Field(default="", max_length=5)
    end: str = Field(default="", max_length=5)


class PlanningRequest(BaseModel):
    enabled: bool = False
    days: dict[str, list[PlanningSlot]] = Field(default_factory=dict, max_length=7)
    offset_min: int = Field(default=0, ge=0, le=10_000)
    offset_max: int = Field(default=0, ge=0, le=10_000)


class SpyingRequest(BaseModel):
    planet: str = Field(default="", max_length=200)
    minimums: dict[str, int] = Field(default_factory=dict, max_length=3)
    required: int = Field(default=1, ge=0, le=10)


class OrderRequest(BaseModel):
    order: list[str] = Field(max_length=50)


class CredentialsRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=254)
    # None : garder le mot de passe déjà enregistré (le champ de la page reste vide, il n'est jamais réaffiché).
    password: Annotated[SecretStr, Field(min_length=1, max_length=256)] | None = None


class RecordingRequest(BaseModel):
    enabled: bool


class UniverseRequest(BaseModel):
    index: int = Field(ge=0, le=50)


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

    # --- Exploration de la structure du site ------------------------------------
    def _check_origin(request: Request) -> None:
        if not _same_origin(request.headers, request.headers.get("host")):
            raise HTTPException(403, "Origine refusée.")

    @app.post("/api/explore/recording")
    async def api_explore_recording(body: RecordingRequest, request: Request):
        _check_origin(request)
        bot.set_recording(body.enabled)
        return JSONResponse(bot.status(), headers=NO_STORE)

    @app.post("/api/explore/snapshot")
    async def api_explore_snapshot(request: Request):
        _check_origin(request)
        ok = await bot.snapshot_now()
        return JSONResponse({"ok": ok, **bot.status()}, headers=NO_STORE)

    @app.post("/api/explore/clear")
    async def api_explore_clear(request: Request):
        _check_origin(request)
        bot.clear_recording()
        return JSONResponse(bot.status(), headers=NO_STORE)

    @app.get("/api/explore/export")
    async def api_explore_export():
        data = await bot.export_structure()
        return JSONResponse(
            data,
            headers={**NO_STORE, "Content-Disposition": 'attachment; filename="boleryos-structure.json"'},
        )

    # --- Choix de l'univers -----------------------------------------------------
    @app.get("/api/universes")
    async def api_universes():
        return JSONResponse(await bot.list_universes(), headers=NO_STORE)

    @app.post("/api/universe")
    async def api_universe(body: UniverseRequest, request: Request):
        _check_origin(request)
        return JSONResponse(await bot.enter_universe(body.index), headers=NO_STORE)

    # --- Compte : identifiants gardés en mémoire seulement ----------------------------
    @app.get("/api/account")
    async def api_account():
        prefs = bot.supervisor.prefs.get()
        return JSONResponse(
            {
                "has_credentials": bot.has_credentials(),
                "hint": bot.credentials_hint(),  # jamais l'identifiant complet ni le mot de passe
                "universe": prefs["universe"],
                "universes": prefs["catalog"]["universes"],
                "saved_on_disk": bot.vault.available,  # le mot de passe survit à un redémarrage (chiffré)
                "ui_protected": settings.ui_user is not None,  # l'interface du bot demande un mot de passe
            },
            headers=NO_STORE,
        )

    @app.get("/api/account/form")
    async def api_account_form():
        """Pré-remplit les champs de la page : l'identifiant, jamais le mot de passe (seulement « il en existe un »)."""
        return JSONResponse(
            {"identifier": bot.credentials_identifier(), "has_password": bot.credentials_password_known()},
            headers=NO_STORE,
        )

    @app.post("/api/account/credentials")
    async def api_account_credentials(body: CredentialsRequest, request: Request):
        _check_origin(request)
        password = body.password.get_secret_value() if body.password else None
        if not bot.set_credentials(body.identifier.strip(), password):
            raise HTTPException(422, "Saisissez le mot de passe.")
        # Comme TBot : une fois les identifiants saisis, le bot se connecte et choisit l'univers tout seul.
        bot.supervisor.reset_connection()
        bot.supervisor.ensure_keeper()
        return JSONResponse({"has_credentials": True, "hint": bot.credentials_hint()}, headers=NO_STORE)

    @app.delete("/api/account/credentials")
    async def api_account_forget(request: Request):
        _check_origin(request)
        bot.forget_credentials()
        await bot.supervisor.stop_keeper()
        return JSONResponse({"has_credentials": False, "hint": ""}, headers=NO_STORE)

    @app.post("/api/account/login")
    async def api_account_login(request: Request):
        """Relance tout de suite la connexion automatique (après une erreur ou une intervention manuelle)."""
        _check_origin(request)
        if not bot.has_credentials():
            bot.restore_credentials()  # identifiants enregistrés (après une déconnexion, par exemple)
        if not bot.has_credentials():
            raise HTTPException(409, "Aucun identifiant en mémoire : saisissez-les d'abord.")
        bot.supervisor.reset_connection()
        bot.supervisor.ensure_keeper()
        return JSONResponse({**bot.status(), "message": "Connexion relancée."}, headers=NO_STORE)

    # --- Scan des planètes ------------------------------------------------------------
    @app.get("/api/planets")
    async def api_planets():
        """Dernier scan des planètes (liste lue dans le jeu, date, origine, erreur éventuelle)."""
        return JSONResponse(bot.supervisor.scan_summary(), headers=NO_STORE)

    @app.post("/api/planets/scan")
    async def api_planets_scan(request: Request):
        """Relit tout de suite la liste des planètes dans le jeu (le bot doit être dans une partie)."""
        _check_origin(request)
        return JSONResponse(await bot.supervisor.scan_planets("manuel"), headers=NO_STORE)

    # --- Tableau de bord, événements, alertes -----------------------------------------
    @app.get("/api/events")
    async def api_events(after: float = 0.0, limit: int = 20):
        events = [e for e in bot.events(200) if e["t"] > after][-max(1, min(limit, 100)):]
        return JSONResponse(events, headers=NO_STORE)

    # --- Préférences (univers à rejoindre, délai de connexion) ---------------------------
    @app.get("/api/preferences")
    async def api_preferences():
        return JSONResponse(
            {"preferences": bot.supervisor.prefs.get(), "persistent": bot.supervisor.prefs.persistent}, headers=NO_STORE
        )

    @app.put("/api/preferences")
    async def api_preferences_put(body: PreferencesRequest, request: Request):
        _check_origin(request)
        previous_universe = bot.supervisor.prefs.get()["universe"]
        try:
            saved = bot.supervisor.prefs.update(body.model_dump(exclude_none=True))
        except PreferencesError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        if saved["universe"] != previous_universe:  # nouvel univers demandé : le bot réessaie tout de suite
            bot.supervisor.reset_connection()
        return JSONResponse(
            {"preferences": saved, "persistent": bot.supervisor.prefs.persistent}, headers=NO_STORE
        )

    # --- Fonctionnalités : interrupteur général, minuteurs, priorités ----------------------
    @app.get("/api/features")
    async def api_features():
        return JSONResponse(bot.scheduler.summary(), headers=NO_STORE)

    @app.put("/api/features/order")
    async def api_features_order(body: OrderRequest, request: Request):
        _check_origin(request)
        try:
            bot.scheduler.reorder(body.order)
        except FeaturesError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse(bot.scheduler.summary(), headers=NO_STORE)

    @app.put("/api/features/{feature_id}")
    async def api_feature_update(feature_id: str, body: FeatureRequest, request: Request):
        _check_origin(request)
        try:
            bot.scheduler.update(feature_id, body.model_dump(exclude_none=True))
        except KeyError as exc:
            raise HTTPException(404, "Fonctionnalité inconnue.") from exc
        except FeaturesError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse(bot.scheduler.summary(), headers=NO_STORE)

    @app.post("/api/bot/start")
    async def api_bot_start(request: Request):
        _check_origin(request)
        bot.scheduler.start()
        return JSONResponse(bot.scheduler.summary(), headers=NO_STORE)

    @app.post("/api/bot/stop")
    async def api_bot_stop(request: Request):
        _check_origin(request)
        bot.scheduler.stop()
        return JSONResponse(bot.scheduler.summary(), headers=NO_STORE)

    # --- Constructions : liste d'objectifs (bâtiment + niveau cible) et choix des planètes ----------
    def _known_planets() -> list[str]:
        return [p["name"] for p in bot.supervisor.scan_summary()["planets"]] or bot.supervisor.prefs.get()["catalog"]["planets"]

    @app.get("/api/constructions")
    async def api_constructions():
        return JSONResponse(
            {
                "settings": bot.constructions.get(),
                "buildings": [{"id": b.id, "label": b.label, "page": b.page} for b in BUILDINGS],
                "planets": _known_planets(),
                "persistent": bot.constructions.persistent,
            },
            headers=NO_STORE,
        )

    @app.put("/api/constructions")
    async def api_constructions_put(body: ConstructionsRequest, request: Request):
        _check_origin(request)
        try:
            saved = bot.constructions.replace(body.model_dump(), _known_planets)
        except ConstructionError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse({"settings": saved}, headers=NO_STORE)

    # --- Recherches : liste d'objectifs (recherche + niveau cible) et planète chargée des recherches ----------
    @app.get("/api/researches")
    async def api_researches():
        return JSONResponse(
            {
                "settings": bot.researches.get(),
                "researches": [{"id": r.id, "label": r.label, "tab": r.tab} for r in RESEARCHES],
                "planets": _known_planets(),
                "persistent": bot.researches.persistent,
            },
            headers=NO_STORE,
        )

    @app.put("/api/researches")
    async def api_researches_put(body: ResearchesRequest, request: Request):
        _check_origin(request)
        try:
            saved = bot.researches.replace(body.model_dump(), _known_planets)
        except ResearchError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse({"settings": saved}, headers=NO_STORE)

    # --- Population : lignes de recrutement (population, mode, quantité, planète) ----------
    @app.get("/api/populations")
    async def api_populations():
        return JSONResponse(
            {
                "settings": bot.populations.get(),
                "populations": [{"id": p.id, "label": p.label, "tab": p.tab} for p in POPULATIONS],
                "planets": _known_planets(),
                "persistent": bot.populations.persistent,
            },
            headers=NO_STORE,
        )

    @app.put("/api/populations")
    async def api_populations_put(body: PopulationsRequest, request: Request):
        _check_origin(request)
        try:
            saved = bot.populations.replace(body.model_dump(), _known_planets)
        except PopulationError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse({"settings": saved}, headers=NO_STORE)

    # --- Chantier spatial : scan des modèles, Assemblages (vaisseaux) et Composants ---------------
    @app.get("/api/models")
    async def api_models():
        """Dernier scan des modèles de vaisseaux (liste lue dans l'onglet Assemblages, date, origine, erreur éventuelle)."""
        return JSONResponse({**bot.supervisor.models_summary(), "known": bot.supervisor.known_models()}, headers=NO_STORE)

    @app.post("/api/models/scan")
    async def api_models_scan(request: Request):
        """Relit tout de suite les modèles dans le jeu (le bot doit être dans une partie)."""
        _check_origin(request)
        return JSONResponse({**await bot.supervisor.scan_models("manuel"), "known": bot.supervisor.known_models()}, headers=NO_STORE)

    @app.get("/api/ships")
    async def api_ships():
        return JSONResponse(
            {
                "settings": bot.ships.get(),
                "models": bot.supervisor.known_models(),
                "planets": _known_planets(),
                "persistent": bot.ships.persistent,
            },
            headers=NO_STORE,
        )

    @app.put("/api/ships")
    async def api_ships_put(body: QuantityRequest, request: Request):
        _check_origin(request)
        try:
            saved = bot.ships.replace(body.model_dump(), _known_planets, bot.supervisor.known_models)
        except LineError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse({"settings": saved}, headers=NO_STORE)

    @app.get("/api/components")
    async def api_components():
        return JSONResponse(
            {
                "settings": bot.components.get(),
                "components": [{"id": c.id, "label": c.label, "category": c.category} for c in COMPONENTS],
                "planets": _known_planets(),
                "persistent": bot.components.persistent,
            },
            headers=NO_STORE,
        )

    @app.put("/api/components")
    async def api_components_put(body: QuantityRequest, request: Request):
        _check_origin(request)
        try:
            saved = bot.components.replace(body.model_dump(), _known_planets)
        except LineError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse({"settings": saved}, headers=NO_STORE)

    # --- Exploration : réglages, relevé du jour (brèches les plus proches, modèles du jeu) -----------
    def _explorations_payload() -> dict:
        return {
            "settings": bot.explorations.get(),
            "daily": bot.explorations.daily(),
            "planets": _known_planets(),
            "units": {"PILLARS": list(PILLAR_UNITS), "FLEET": bot.supervisor.known_models()},
            "models": bot.explorations.known_models(),
            "methods": METHODS,
            "scanning": bot.explorer.scanning,
            "persistent": bot.explorations.persistent,
        }

    @app.get("/api/explorations")
    async def api_explorations():
        return JSONResponse(_explorations_payload(), headers=NO_STORE)

    @app.put("/api/explorations")
    async def api_explorations_put(body: ExplorationRequest, request: Request):
        _check_origin(request)
        try:
            bot.explorations.replace(body.model_dump(), _known_planets())
        except ExplorationError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse(_explorations_payload(), headers=NO_STORE)

    @app.post("/api/explorations/scan")
    async def api_explorations_scan(request: Request):
        """Refait tout de suite le relevé du jour : brèche la plus proche de chaque planète visée et modèles du jeu."""
        _check_origin(request)
        await bot.explorer.scan("manuel")
        return JSONResponse(_explorations_payload(), headers=NO_STORE)

    # --- Fouilles : lignes, délai avant relance, relevé du jour (sites libres), Fouilles suivies -----------
    def _excavations_payload() -> dict:
        return {
            "settings": bot.excavations.get(),
            "daily": bot.excavations.daily(),
            "tracked": bot.excavations.tracked(),
            "planets": _known_planets(),
            "units": list(EXCAVATION_UNITS),
            "durations": list(EXCAVATION_DURATIONS),
            "max_team": EXCAVATION_MAX_TEAM,
            "scanning": bot.excavator.scanning,
            "persistent": bot.excavations.persistent,
        }

    @app.get("/api/excavations")
    async def api_excavations():
        return JSONResponse(_excavations_payload(), headers=NO_STORE)

    @app.put("/api/excavations")
    async def api_excavations_put(body: ExcavationRequest, request: Request):
        _check_origin(request)
        try:
            bot.excavations.replace(body.model_dump(), _known_planets())
        except ExcavationError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse(_excavations_payload(), headers=NO_STORE)

    @app.post("/api/excavations/scan")
    async def api_excavations_scan(request: Request):
        """Refait tout de suite le relevé du jour : sites libres les plus proches de chaque planète de départ."""
        _check_origin(request)
        await bot.excavator.scan("manuel")
        return JSONResponse(_excavations_payload(), headers=NO_STORE)

    # --- Piratage : planète de départ, délai avant relance, Errants les plus proches, suivi de Naël -----------
    def _hackings_payload() -> dict:
        return {"settings": bot.hackings.get(), "daily": bot.hackings.daily(), "state": bot.hackings.state(),
                "planets": _known_planets(), "scanning": bot.hacker.scanning, "persistent": bot.hackings.persistent}

    @app.get("/api/hackings")
    async def api_hackings():
        return JSONResponse(_hackings_payload(), headers=NO_STORE)

    @app.put("/api/hackings")
    async def api_hackings_put(body: HackingRequest, request: Request):
        _check_origin(request)
        try:
            bot.hackings.replace(body.model_dump(), _known_planets())
        except HackingError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse(_hackings_payload(), headers=NO_STORE)

    @app.post("/api/hackings/scan")
    async def api_hackings_scan(request: Request):
        """Refait tout de suite le relevé : Errants les plus proches de la planète de départ, dans le ratio ×2."""
        _check_origin(request)
        if bot.hackings.get()["planet"]:
            await bot.hacker.scan("manuel")
        return JSONResponse(_hackings_payload(), headers=NO_STORE)

    # --- Planning : plages horaires actives par jour (heure française) -----------
    def _planning_payload() -> dict:
        settings = bot.planning.get()
        st = planning_state(settings)
        if not settings["enabled"]:
            message = "Planning désactivé : le bot agit à toute heure quand il est sur ON."
        elif st["active"]:
            message = f"Dans une plage active jusqu'à {planning_describe(st['until'])}."
        else:
            message = f"Hors plage : reprise {planning_describe(st['next'])}." if st["next"] else "Hors plage : aucune plage prévue."
        return {"settings": settings, "active": st["active"], "message": message, "days": PLANNING_DAY_LABELS,
                "persistent": bot.planning.persistent}

    @app.get("/api/planning")
    async def api_planning():
        return JSONResponse(_planning_payload(), headers=NO_STORE)

    @app.put("/api/planning")
    async def api_planning_put(body: PlanningRequest, request: Request):
        _check_origin(request)
        try:
            bot.planning.replace(body.model_dump())
        except PlanningError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        bot.log("Planning enregistré.", event=True)
        return JSONResponse(_planning_payload(), headers=NO_STORE)

    # --- Espionnage des Errants : réglages et campagne manuelle (le bot passe sur OFF) -----------
    def _spying_payload() -> dict:
        return {"settings": bot.spying.get(), "status": bot.spy.status(), "planets": _known_planets(),
                "resources": list(SPY_RESOURCES), "persistent": bot.spying.persistent, "bot_on": bot.scheduler.on}

    @app.get("/api/spying")
    async def api_spying():
        return JSONResponse(_spying_payload(), headers=NO_STORE)

    @app.put("/api/spying")
    async def api_spying_put(body: SpyingRequest, request: Request):
        _check_origin(request)
        try:
            bot.spying.replace(body.model_dump(), _known_planets())
        except SpyingError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        return JSONResponse(_spying_payload(), headers=NO_STORE)

    @app.post("/api/spying/start")
    async def api_spying_start(request: Request):
        """Lance la campagne d'espionnage ; les autres automatismes s'arrêtent (bot sur OFF) jusqu'à ce que l'utilisateur les relance."""
        _check_origin(request)
        refused = bot.spy.start()
        if refused:
            raise HTTPException(409, detail=refused)
        return JSONResponse(_spying_payload(), headers=NO_STORE)

    @app.post("/api/spying/stop")
    async def api_spying_stop(request: Request):
        _check_origin(request)
        bot.spy.stop()
        return JSONResponse(_spying_payload(), headers=NO_STORE)

    @app.get("/api/status")
    async def api_status(limit: int = 50):
        return JSONResponse({**bot.status(), "logs": bot.logs(max(1, min(limit, 1000)))}, headers=NO_STORE)

    @app.get("/api/display")
    async def api_display():
        """« vnc » : navigateur complet (vraie fenêtre Chromium affichée par noVNC) ; « images » : vue par images."""
        d = bot.display
        return JSONResponse({"mode": "vnc" if d is not None and NOVNC_DIR.is_dir() else "images",
                             "width": d.width if d else None, "height": d.height if d else None}, headers=NO_STORE)

    @app.websocket("/ws/vnc")
    async def ws_vnc(ws: WebSocket):
        """Relais WebSocket ↔ serveur VNC local (127.0.0.1) : aucun port VNC n'est exposé hors du conteneur."""
        if not _same_origin(ws.headers, ws.headers.get("host")) or bot.display is None:
            await ws.close(code=1008)
            return
        protocols = ws.scope.get("subprotocols") or []
        await ws.accept(subprotocol="binary" if "binary" in protocols else None)
        bot.viewer_joined()
        bot.start_in_background()
        writer = None
        try:
            await bot.display.ensure()
            reader, writer = await asyncio.open_connection("127.0.0.1", bot.display.port)

            async def upstream() -> None:  # clavier, souris, presse-papiers de l'utilisateur → VNC
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        return
                    data = msg.get("bytes") if msg.get("bytes") is not None else (msg.get("text") or "").encode()
                    writer.write(data)
                    await writer.drain()

            async def downstream() -> None:  # images de l'écran → navigateur de l'utilisateur
                while True:
                    data = await reader.read(65536)
                    if not data:
                        return
                    await ws.send_bytes(data)

            tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass
        finally:
            if writer is not None:
                writer.close()
            bot.viewer_left()
            try:
                await ws.close()
            except (RuntimeError, WebSocketDisconnect):
                pass  # déjà fermé par le navigateur

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
        last_status = None
        last_status_time = 0.0
        try:
            while not reader_task.done():  # la tâche se termine quand le client part
                frame_id, image = bot.frame()
                if image is not None and frame_id != last_frame:
                    await ws.send_bytes(image)  # image JPEG brute, sans conversion en texte
                    last_frame = frame_id
                    await asyncio.sleep(MIN_FRAME_INTERVAL)  # plafonne le débit (~25 images/s)
                status = {**bot.status(), "logs": bot.logs()}
                now = time.monotonic()
                if status != last_status or now - last_status_time > 2:
                    await ws.send_json(status)
                    last_status, last_status_time = status, now
                await bot.wait_frame(WS_TICK)
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

    app.add_api_route("/", page("home.html"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/ecran", page("ecran.html"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/reglages", page("reglages.html"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/suivi", page("suivi.html"), methods=["GET"], include_in_schema=False)

    def redirect(target: str):
        async def handler():
            return RedirectResponse(target)

        return handler

    # Anciennes adresses : les favoris continuent de fonctionner.
    app.add_api_route("/vision", redirect("/ecran"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/actions", redirect("/reglages"), methods=["GET"], include_in_schema=False)
    app.add_api_route("/fonctionnalites", redirect("/reglages#features"), methods=["GET"], include_in_schema=False)
    app.mount("/static", NoStoreStaticFiles(directory=STATIC_DIR), name="static")
    if NOVNC_DIR.is_dir():  # client noVNC (paquet « novnc » de l'image Docker)
        app.mount("/novnc", NoStoreStaticFiles(directory=NOVNC_DIR), name="novnc")

    if settings.ui_user and settings.ui_password:
        app.add_middleware(BasicAuthMiddleware, user=settings.ui_user, password=settings.ui_password)
    return app


app = create_app()
