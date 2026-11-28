"""Client Discord : reçoit les commandes « slash » du joueur et y répond, sans bibliothèque Discord supplémentaire.

Le bot se connecte à Discord (connexion sortante : aucun port à ouvrir). Aucune « intention » privilégiée n'est demandée :
les commandes slash arrivent sans elles. Pas à pas :
  1. lire l'identifiant de l'application et déclarer les commandes sur le serveur choisi (elles apparaissent aussitôt) ;
  2. ouvrir la passerelle (WebSocket), s'identifier, battre le « cœur » à la cadence demandée ;
  3. à chaque commande : vérifier que l'auteur est bien le joueur (et le salon, s'il est fixé), répondre « en cours »,
     exécuter (`CommandRunner`), puis remplacer la réponse par le résultat — visible de lui seul.
"""

from __future__ import annotations

import asyncio
import json
import random

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from .discord_commands import COMMAND_DEFINITIONS, CommandRunner, Reply
from .discord_store import DiscordStore

API = "https://discord.com/api/v10"
EPHEMERAL = 64  # message visible de son seul destinataire
INTERACTION_COMMAND, INTERACTION_BUTTON, INTERACTION_AUTOCOMPLETE = 2, 3, 4
RECONNECT_MIN, RECONNECT_MAX = 5.0, 120.0
# Codes de fermeture après lesquels réessayer ne sert à rien (jeton refusé, intentions interdites…).
FATAL_CLOSE = {4004: "Jeton refusé par Discord.", 4010: "Discord a refusé la connexion.", 4011: "Discord a refusé la connexion.",
               4012: "Discord a refusé la connexion.", 4013: "Discord a refusé la connexion.", 4014: "Discord a refusé la connexion."}


class DiscordFatal(Exception):
    """Erreur qui ne se règle pas en réessayant : le joueur doit corriger un réglage."""


def parse_command(data: dict) -> tuple[tuple[str, str], dict]:
    """(commande, sous-commande) et valeurs saisies, d'après les données d'une interaction."""
    options, sub = data.get("options") or [], ""
    if options and options[0].get("type") == 1:
        sub, options = options[0]["name"], options[0].get("options") or []
    return (data.get("name", ""), sub), {o["name"]: str(o.get("value", "")) for o in options if "name" in o}


class DiscordClient:
    def __init__(self, store: DiscordStore, runner: CommandRunner, api: str = API) -> None:
        self._store, self._runner, self._api = store, runner, api
        self._http: httpx.AsyncClient | None = None
        self._app_id = ""
        self._seq: int | None = None
        self._session_id = ""
        self._resume_url = ""
        self._acked = True
        self._tasks: set[asyncio.Task] = set()
        self.state, self.error = "off", ""

    def status(self) -> dict:
        return {"state": self.state, "error": self.error}

    # ------------------------------------------------------------------ boucle de connexion
    async def run(self) -> None:
        """Reste connecté tant que la tâche vit ; se reconnecte seule après une coupure."""
        self._http = httpx.AsyncClient(headers={"Authorization": f"Bot {self._store.token()}"}, timeout=15.0)
        delay = RECONNECT_MIN
        try:
            while True:
                self.state = "connecting"
                try:
                    await self._connect_once()
                    delay = RECONNECT_MIN
                except DiscordFatal as exc:
                    self.state, self.error = "error", str(exc)
                    return
                except ConnectionClosed as exc:
                    code = getattr(exc.rcvd, "code", None)
                    if code in FATAL_CLOSE:
                        self.state, self.error = "error", FATAL_CLOSE[code]
                        return
                except (OSError, WebSocketException, httpx.HTTPError) as exc:
                    self.state, self.error = "connecting", f"Discord injoignable ({type(exc).__name__}) : nouvel essai."
                await asyncio.sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX)
        finally:
            await self._shutdown_tasks()
            await self._http.aclose()
            if self.state != "error":
                self.state = "off"

    def _check_close(self, code: int | None) -> None:
        if code in FATAL_CLOSE:
            raise DiscordFatal(FATAL_CLOSE[code])

    async def _connect_once(self) -> None:
        if not self._app_id:
            await self._register_commands()
        url = self._resume_url
        if not url:
            url = (await self._call("GET", "/gateway/bot")).json()["url"]
        async with connect(f"{url}/?v=10&encoding=json", max_size=2**22) as ws:
            await self._pump(ws)
            self._check_close(ws.close_code)

    async def _register_commands(self) -> None:
        self._app_id = (await self._call("GET", "/oauth2/applications/@me")).json()["id"]
        guild = self._store.get()["guild_id"]
        await self._call("PUT", f"/applications/{self._app_id}/guilds/{guild}/commands", json=COMMAND_DEFINITIONS)

    # ------------------------------------------------------------------ passerelle
    async def _pump(self, ws) -> None:
        heart: asyncio.Task | None = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("s") is not None:
                    self._seq = msg["s"]
                op = msg.get("op")
                if op == 10:
                    self._acked = True
                    heart = asyncio.create_task(self._heartbeat(ws, msg["d"]["heartbeat_interval"] / 1000))
                    await self._identify(ws)
                elif op == 11:
                    self._acked = True
                elif op == 1:
                    await ws.send(json.dumps({"op": 1, "d": self._seq}))
                elif op in (7, 9):
                    if op == 9 and not msg.get("d"):
                        self._session_id, self._resume_url, self._seq = "", "", None
                    return  # reconnexion (reprise si la session est encore valable)
                elif op == 0:
                    self._dispatch(msg.get("t"), msg.get("d") or {})
        finally:
            if heart is not None:
                heart.cancel()

    async def _identify(self, ws) -> None:
        if self._session_id:
            body = {"op": 6, "d": {"token": self._store.token(), "session_id": self._session_id, "seq": self._seq}}
        else:
            body = {"op": 2, "d": {"token": self._store.token(), "intents": 0,
                                   "properties": {"os": "linux", "browser": "boleryos", "device": "boleryos"}}}
        await ws.send(json.dumps(body))

    async def _heartbeat(self, ws, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not self._acked:  # le serveur ne répond plus : on coupe, la boucle se reconnecte
                await ws.close(code=4000)
                return
            self._acked = False
            await ws.send(json.dumps({"op": 1, "d": self._seq}))
            await asyncio.sleep(interval)

    def _dispatch(self, event: str | None, data: dict) -> None:
        if event == "READY":
            self._session_id, self._resume_url = data.get("session_id", ""), data.get("resume_gateway_url", "")
            self.state, self.error = "connected", ""
        elif event == "RESUMED":
            self.state, self.error = "connected", ""
        elif event == "INTERACTION_CREATE":
            task = asyncio.create_task(self._on_interaction(data))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _shutdown_tasks(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------ interactions
    def _allowed(self, data: dict) -> bool:
        """Seul le joueur choisi, sur le serveur choisi (et le salon, s'il est fixé), peut commander."""
        cfg = self._store.get()
        user = (data.get("member") or {}).get("user") or {}
        if user.get("id") != cfg["owner_id"] or data.get("guild_id") != cfg["guild_id"]:
            return False
        return not cfg["channel_id"] or data.get("channel_id") == cfg["channel_id"]

    async def _on_interaction(self, data: dict) -> None:
        try:
            await self._handle(data)
        except (httpx.HTTPError, DiscordFatal, KeyError, ValueError):
            pass  # réponse impossible (délai de 3 s dépassé, Discord coupé) : la commande suivante repartira proprement

    async def _handle(self, data: dict) -> None:
        kind = data.get("type")
        if not self._allowed(data):
            if kind == INTERACTION_AUTOCOMPLETE:
                await self._callback(data, 8, {"choices": []})
            else:
                await self._callback(data, 4, {"content": "Commande réservée au propriétaire du bot.", "flags": EPHEMERAL})
        elif kind == INTERACTION_AUTOCOMPLETE:
            await self._suggest(data)
        elif kind == INTERACTION_COMMAND:
            path, options = parse_command(data["data"])
            await self._callback(data, 5, {"flags": EPHEMERAL})  # « en cours » : Discord n'attend que 3 s
            await self._edit_original(data, await self._runner.execute(path, options, False))
        elif kind == INTERACTION_BUTTON:
            await self._on_button(data)

    async def _suggest(self, data: dict) -> None:
        focused = next((o for o in parse_options(data["data"]) if o.get("focused")), None)
        choices = self._runner.suggestions(focused["name"], str(focused.get("value", ""))) if focused else []
        await self._callback(data, 8, {"choices": choices})

    async def _on_button(self, data: dict) -> None:
        action = str(data["data"].get("custom_id", ""))
        await self._callback(data, 6, None)  # met à jour le message d'origine (sans nouveau message)
        if not action.startswith("ok:"):
            await self._edit_original(data, Reply("Annulé."))
            return
        command, sub, instance = (action[3:].split(":", 2) + ["", "", ""])[:3]
        reply = await self._runner.execute((command, sub), {"instance": instance} if instance else {}, True)
        await self._edit_original(data, reply)

    async def _edit_original(self, data: dict, reply: Reply) -> None:
        body: dict = {"content": reply.text, "components": []}
        if reply.confirm:
            body["components"] = [{"type": 1, "components": [
                {"type": 2, "style": 4, "label": "Confirmer", "custom_id": f"ok:{reply.confirm}"},
                {"type": 2, "style": 2, "label": "Annuler", "custom_id": "no"}]}]
        await self._call("PATCH", f"/webhooks/{self._app_id}/{data['token']}/messages/@original", json=body)

    async def _callback(self, data: dict, kind: int, payload: dict | None) -> None:
        body: dict = {"type": kind}
        if payload is not None:
            body["data"] = payload
        await self._call("POST", f"/interactions/{data['id']}/{data['token']}/callback", json=body)

    # ------------------------------------------------------------------ appels REST
    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        assert self._http is not None
        for attempt in range(2):
            response = await self._http.request(method, self._api + path, **kwargs)
            if response.status_code == 429 and attempt == 0:  # trop de requêtes : une seule nouvelle tentative
                await asyncio.sleep(min(float(response.json().get("retry_after", 1)), 5.0))
                continue
            break
        self._raise_for(response)
        return response

    @staticmethod
    def _raise_for(response: httpx.Response) -> None:
        if response.status_code in (401, 403, 404) and "/interactions/" not in str(response.url) and "/webhooks/" not in str(response.url):
            reasons = {401: "Jeton refusé par Discord.",
                       403: "Le bot Discord n'a pas le droit d'agir sur ce serveur : invitez-le avec la portée « applications.commands ».",
                       404: "Serveur Discord introuvable : vérifiez son numéro et que le bot y est invité."}
            raise DiscordFatal(reasons[response.status_code])
        response.raise_for_status()


def parse_options(data: dict) -> list[dict]:
    """Les valeurs saisies (sous-commande comprise), avec la marque « focused » de celle en cours de saisie."""
    options = data.get("options") or []
    if options and options[0].get("type") == 1:
        return options[0].get("options") or []
    return options


class DiscordService:
    """Garde le client Discord en phase avec les réglages : (re)lancé après chaque modification, arrêté si coupé."""

    def __init__(self, store: DiscordStore, runner: CommandRunner) -> None:
        self._store, self._runner = store, runner
        self._client: DiscordClient | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> dict:
        if self._client is None:
            return {"state": "off", "error": ""}
        return self._client.status()

    async def apply(self) -> None:
        """Relance le client avec les réglages actuels (ou l'arrête s'ils sont incomplets ou coupés)."""
        await self.shutdown()
        if self._store.ready():
            self._client = DiscordClient(self._store, self._runner)
            self._task = asyncio.create_task(self._client.run())

    async def shutdown(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._client = None
