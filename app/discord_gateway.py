"""Client Discord : reçoit les commandes « slash » du joueur et y répond, sans bibliothèque Discord supplémentaire.

Le bot se connecte à Discord (connexion sortante : aucun port à ouvrir). Seul le jeton est demandé au joueur : le reste se
lit chez Discord. Aucune « intention » privilégiée (seule « serveurs », libre d'accès, pour voir les invitations à venir).
Pas à pas :
  1. lire l'application (identifiant et propriétaire) et les serveurs où le bot est invité ; y déclarer les commandes ;
  2. ouvrir la passerelle (WebSocket), s'identifier, battre le « cœur » à la cadence demandée ; déclarer aussi les
     commandes sur tout serveur où le bot est invité plus tard ;
  3. publier (ou remettre à jour) le panneau de boutons dans le salon choisi, une seule fois (`discord_panel.py`) ;
  4. à chaque commande ou clic : vérifier que l'auteur est le propriétaire de l'application (ou membre de son équipe), répondre
     « en cours », exécuter (`CommandRunner`), puis remplacer la réponse par le résultat — visible de lui seul.
"""

from __future__ import annotations

import asyncio
import json
import random

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from . import discord_panel as panel_mod
from .discord_commands import COMMAND_DEFINITIONS, CommandRunner, Reply
from .discord_store import DiscordStore

API = "https://discord.com/api/v10"
EPHEMERAL = 64  # message visible de son seul destinataire
MAX_MESSAGE = 1900  # Discord refuse plus de 2000 caractères par message
INTERACTION_COMMAND, INTERACTION_BUTTON, INTERACTION_AUTOCOMPLETE = 2, 3, 4
INTENT_GUILDS = 1  # non privilégiée : prévient quand le bot est invité sur un serveur
RECONNECT_MIN, RECONNECT_MAX = 5.0, 120.0
# Codes de fermeture après lesquels réessayer ne sert à rien (jeton refusé, intentions interdites…).
FATAL_CLOSE = {4004: "Jeton refusé par Discord.", 4010: "Discord a refusé la connexion.", 4011: "Discord a refusé la connexion.",
               4012: "Discord a refusé la connexion.", 4013: "Discord a refusé la connexion.", 4014: "Discord a refusé la connexion."}


class DiscordFatal(Exception):
    """Erreur qui ne se règle pas en réessayant : le joueur doit corriger un réglage."""


class DiscordRefused(DiscordFatal):
    """Discord refuse (403/404) : fatal pour la connexion, mais ne bloque pas les autres serveurs."""


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
        self._owners: set[str] = set()  # seuls ces comptes peuvent commander : le propriétaire de l'application
        self._guilds: set[str] = set()  # serveurs où les commandes sont déclarées
        self._notice = ""  # remarque à montrer au joueur (ex. bot invité nulle part), sans couper la connexion
        self._panel = panel_mod.Panel(runner)
        self._panel_lock = asyncio.Lock()  # deux synchronisations en même temps publieraient deux panneaux
        self._panel_error = ""
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
        app = (await self._call("GET", "/oauth2/applications/@me")).json()
        self._app_id, self._owners = app["id"], _owner_ids(app)
        if not self._owners:
            raise DiscordFatal("Propriétaire du bot Discord introuvable.")
        guilds = (await self._call("GET", "/users/@me/guilds")).json()
        for guild in guilds:
            await self._register_guild(str(guild["id"]))
        self._update_notice(bool(guilds))

    async def _register_guild(self, guild_id: str) -> None:
        """Déclare les commandes sur un serveur ; un refus n'empêche pas les autres serveurs."""
        if guild_id in self._guilds:
            return
        try:
            await self._call("PUT", f"/applications/{self._app_id}/guilds/{guild_id}/commands", json=COMMAND_DEFINITIONS)
        except DiscordRefused:
            self._notice = ("Le bot n'a pas pu installer ses commandes sur un serveur : "
                            "réinvitez-le avec la portée « applications.commands ».")
            return
        self._guilds.add(guild_id)
        self._update_notice(True)

    def _update_notice(self, invited: bool) -> None:
        if not invited:
            self._notice = "Le bot n'est invité sur aucun serveur Discord : invitez-le sur votre serveur privé."
        elif self._guilds:
            self._notice = ""
        self.error = self._shown()

    def _shown(self) -> str:
        return " ".join(t for t in (self._notice, self._panel_error) if t)

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
            body = {"op": 2, "d": {"token": self._store.token(), "intents": INTENT_GUILDS,
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
            self.state, self.error = "connected", self._shown()
            self._spawn(self._sync_panel())
        elif event == "RESUMED":
            self.state, self.error = "connected", self._shown()
        elif event == "GUILD_CREATE" and data.get("id"):
            self._spawn(self._register_guild(str(data["id"])))
        elif event == "INTERACTION_CREATE":
            self._spawn(self._on_interaction(data))

    def _spawn(self, job) -> None:
        task = asyncio.create_task(job)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _shutdown_tasks(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------ interactions
    def _allowed(self, data: dict) -> bool:
        """Seul le propriétaire de l'application Discord peut commander, et seulement depuis un serveur."""
        user = (data.get("member") or {}).get("user") or {}
        return bool(data.get("guild_id")) and user.get("id") in self._owners

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
        if panel_mod.is_panel(action):
            await self._on_panel(data, action)
            return
        await self._callback(data, 6, None)  # met à jour le message d'origine (sans nouveau message)
        if not action.startswith("ok:"):
            await self._edit_original(data, Reply("Annulé."))
            return
        command, sub, instance = (action[3:].split(":", 2) + ["", "", ""])[:3]
        reply = await self._runner.execute((command, sub), {"instance": instance} if instance else {}, True)
        await self._edit_original(data, reply)

    async def _edit_original(self, data: dict, reply: Reply, rows: list[dict] | None = None) -> None:
        body: dict = {"content": reply.text, "components": rows or []}
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

    # ------------------------------------------------------------------ panneau de boutons
    async def _on_panel(self, data: dict, custom_id: str) -> None:
        """Clic dans le panneau : réservé au salon choisi (le propriétaire est déjà vérifié par `_handle`)."""
        channel, _ = self._store.panel()
        if not channel or data.get("channel_id") != channel:
            await self._callback(data, 4, {"content": "Ce panneau ne fonctionne que dans son salon.", "flags": EPHEMERAL})
            return
        if panel_mod.is_update(custom_id):
            outcome = await self._panel.handle(data)
            await self._callback(data, 7, outcome.update)  # redessine le panneau : le choix y reste inscrit
            return
        ephemeral = bool((data.get("message") or {}).get("flags", 0) & EPHEMERAL)
        if ephemeral:
            await self._callback(data, 6, None)  # clic dans une réponse déjà privée : on la remplace
        else:
            await self._callback(data, 5, {"flags": EPHEMERAL})
        outcome = await self._panel.handle(data)
        await self._edit_original(data, outcome.reply or Reply("Rien à faire."), outcome.rows)

    async def _sync_panel(self) -> None:
        """Publie le panneau s'il n'existe pas, sinon le remet à jour sur place. Jamais deux panneaux."""
        channel, message_id = self._store.panel()
        if not channel:
            self._panel_error = ""
            return
        async with self._panel_lock:
            try:
                await self._publish_panel(channel, message_id)
                self._panel_error = ""
            except DiscordRefused:
                self._panel_error = ("Panneau non publié : le bot manque de droits dans le salon "
                                     "(voir le salon, envoyer des messages, lire l'historique).")
            except (httpx.HTTPError, DiscordFatal, KeyError, ValueError):
                self._panel_error = "Panneau non publié : Discord n'a pas répondu, nouvel essai à la prochaine connexion."
            if self.state == "connected":
                self.error = self._shown()

    async def _publish_panel(self, channel: str, message_id: str) -> None:
        current = await self._panel_message(channel, message_id) if message_id else None
        if current is None:  # message effacé ou jamais publié : un panneau déjà là (donnée perdue) est repris, pas doublé
            current = await self._find_panel(channel)
        if current is None:
            body = self._panel.message()
            created = (await self._call("POST", f"/channels/{channel}/messages", json=body)).json()
            self._store.set_panel_message(str(created["id"]))
            return
        picked = panel_mod.chosen(current)  # on garde le choix de l'instance et de la fonction
        body = self._panel.message(picked.get(panel_mod.PREFIX + "inst", panel_mod.ALL), picked.get(panel_mod.PREFIX + "fn", ""))
        await self._call("PATCH", f"/channels/{channel}/messages/{current['id']}", json=body)
        self._store.set_panel_message(str(current["id"]))

    async def _panel_message(self, channel: str, message_id: str) -> dict | None:
        try:
            return (await self._call("GET", f"/channels/{channel}/messages/{message_id}")).json()
        except DiscordRefused:
            return None  # introuvable : la recherche qui suit dira si c'est un vrai manque de droits

    async def _find_panel(self, channel: str) -> dict | None:
        messages = (await self._call("GET", f"/channels/{channel}/messages", params={"limit": 50})).json()
        for message in messages:
            mine = str((message.get("author") or {}).get("id", "")) == self._app_id
            if mine and any(str(c.get("custom_id", "")).startswith(panel_mod.PREFIX)
                            for row in message.get("components") or [] for c in row.get("components") or []):
                return message
        return None

    # ------------------------------------------------------------------ messages envoyés sans qu'on les ait demandés
    async def notify_owners(self, text: str) -> bool:
        """Message privé à chaque propriétaire (bilan de fin de journée). True si au moins un l'a reçu.
        Faux si le client n'est pas connecté ou si Discord refuse (messages privés fermés)."""
        if self.state != "connected" or self._http is None or not self._owners:
            return False
        sent = False
        for owner in sorted(self._owners):
            try:
                channel = (await self._call("POST", "/users/@me/channels", json={"recipient_id": owner})).json()["id"]
                await self._call("POST", f"/channels/{channel}/messages", json={"content": text[:MAX_MESSAGE]})
                sent = True
            except (httpx.HTTPError, DiscordFatal, KeyError, ValueError):
                continue  # ce destinataire n'a pas reçu le message ; les autres peuvent encore le recevoir
        return sent

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
            if response.status_code == 401:
                raise DiscordFatal("Jeton refusé par Discord.")
            raise DiscordRefused("Discord a refusé la demande (droits manquants ou serveur introuvable).")
        response.raise_for_status()


def _owner_ids(app: dict) -> set[str]:
    """Qui possède l'application : son créateur, ou les membres de l'équipe si elle appartient à une équipe."""
    team = app.get("team") or {}
    members = {str((m.get("user") or {}).get("id", "")) for m in team.get("members") or []}
    owner = str((app.get("owner") or {}).get("id", ""))
    return {i for i in (members or {owner}) if i}


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

    async def notify(self, text: str) -> bool | None:
        """Envoie un message privé aux propriétaires. None : le pilotage est coupé (rien à envoyer), sinon réussi ou non."""
        client = self._client
        if client is None:
            return None
        return await client.notify_owners(text)

    async def shutdown(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._client = None
