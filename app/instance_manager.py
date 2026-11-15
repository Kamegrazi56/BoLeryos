"""Gestionnaire des instances de jeu : une instance = un bot (un `BotSession`) sur un serveur du jeu.

Chaque instance a son contexte Chromium (dans le navigateur partagé), son dossier de données et son univers ; on peut les
créer, démarrer, arrêter et supprimer indépendamment. Le compte (identifiants, cookies de connexion) est commun.

Une instance « démarrée » est un `BotSession` vivant ; « arrêtée », elle n'existe plus qu'en dossier de données et dans le
registre (`/data/instances.json`). Une 6e « instance » n'en est pas une : la **session de connexion** (`compte`), un
`BotSession` sans dossier de données qui sert à se connecter et à lire la liste des serveurs avant qu'aucune instance
n'existe. Elle est fermée dès qu'une instance démarre, pour économiser un contexte.

Règles : un seul bot par serveur (le jeu n'accepte qu'une session par compte et par serveur), un nombre maximal d'instances,
et une suppression qui met le dossier à la corbeille (`instances/.corbeille/`) au lieu de l'effacer.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from .account import Account
from .bot import BotSession, State
from .browser_host import BrowserHost, chromium_memory_mb
from .config import Settings
from .instances import INSTANCES_FOLDER, default_instance_id, instance_dir, migrate_legacy, slugify
from .numbers import norm
from .preferences import MAX_UNIVERSE_LENGTH, Preferences, PreferencesError

REGISTRY_FILE = "instances.json"
LOBBY_ID = "compte"  # pseudo-instance : la session de connexion
TRASH_FOLDER = ".corbeille"
MAX_NAME_LENGTH = 40
DEFAULT_NAME = "Principale"
STARTUP_STAGGER_SECONDS = 4.0  # écart entre deux instances qui reprennent au démarrage
REFRONT_DELAY_SECONDS = 0.6  # laisse la nouvelle fenêtre s'afficher avant de remettre devant celle qu'on regarde


class InstanceError(Exception):
    """Refus d'une opération sur les instances ; `status` est le code HTTP correspondant."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message, self.status = message, status


class InstanceManager:
    def __init__(self, settings: Settings, host: BrowserHost, account: Account) -> None:
        self._settings = settings
        self._host = host
        self._account = account
        self._root: Path | None = Path(settings.data_dir) if settings.data_dir and os.path.isdir(settings.data_dir) else None
        self._lock = asyncio.Lock()  # démarrages, arrêts, créations : un à la fois
        self._infos: list[dict] = []
        self.universes: list[str] = []  # serveurs lus par le dernier scan
        self._bots: dict[str, BotSession] = {}
        self._lobby: BotSession | None = None
        self._startup_task: asyncio.Task | None = None
        self._load()
        for info in self._infos:  # les instances qui tournaient au dernier arrêt reprennent (sans lancer le navigateur ici)
            if info["autostart"]:
                self._bots[info["id"]] = self._make_bot(info["id"])

    # ------------------------------------------------------------------ registre
    def _load(self) -> None:
        data = None
        if self._root is not None:
            try:
                data = json.loads((self._root / REGISTRY_FILE).read_text(encoding="utf8"))
            except (OSError, ValueError):
                data = None
        if isinstance(data, dict) and isinstance(data.get("instances"), list):
            # Un registre existant fait foi, même vide : après la suppression de la dernière instance, les anciens fichiers
            # à plat de /data (gardés pour un retour en arrière) ne doivent pas la ressusciter.
            self._infos = self._clean(data["instances"])
            names = data.get("universes")
            self.universes = [n for n in names if isinstance(n, str)][:50] if isinstance(names, list) else []
            return
        # Premier démarrage avec ce gestionnaire : l'instance existante (anciens fichiers) devient la première.
        root = str(self._root) if self._root is not None else None
        instance_id = default_instance_id(root)
        universe = ""
        if root is not None:
            migrate_legacy(root, instance_id)
            universe = Preferences(str(instance_dir(root, instance_id))).get()["universe"]
        self._infos = [{"id": instance_id, "name": universe or DEFAULT_NAME, "universe": universe, "autostart": True}]
        self._save()

    @staticmethod
    def _clean(raw) -> list[dict]:
        out, seen = [], set()
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            iid = item.get("id")
            if not isinstance(iid, str) or iid in seen or iid == LOBBY_ID or slugify(iid) != iid:
                continue
            seen.add(iid)
            out.append({
                "id": iid,
                "name": str(item.get("name") or iid)[:MAX_NAME_LENGTH],
                "universe": str(item.get("universe") or "")[:MAX_UNIVERSE_LENGTH],
                "autostart": bool(item.get("autostart")),
            })
        return out

    def _save(self) -> None:
        if self._root is None:
            return
        payload = {"instances": self._infos, "universes": self.universes}
        try:
            fd, tmp = tempfile.mkstemp(dir=self._root, prefix=".instances-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._root / REGISTRY_FILE)
        except OSError:
            pass  # facultatif : le gestionnaire continue en mémoire

    def _info(self, instance_id: str) -> dict:
        for info in self._infos:
            if info["id"] == instance_id:
                return info
        raise InstanceError("Instance inconnue.", 404)

    @property
    def default_id(self) -> str | None:
        return self._infos[0]["id"] if self._infos else None

    # ------------------------------------------------------------------ sessions de bot
    def _settings_for(self, instance_id: str) -> Settings:
        if self._root is None:
            return self._settings
        return replace(self._settings, data_dir=str(instance_dir(str(self._root), instance_id)))

    def _make_bot(self, instance_id: str) -> BotSession:
        bot = BotSession(self._settings_for(instance_id), self._host, self._account)
        bot.after_new_page = self.refront
        return bot

    def refront(self) -> None:
        """Une instance vient d'ouvrir sa fenêtre sur l'écran virtuel commun : celles qu'un écran regarde repassent devant."""
        for bot in [*self._bots.values(), self._lobby]:
            if bot is not None and bot._viewers > 0:
                bot._spawn(bot.bring_to_front(delay=REFRONT_DELAY_SECONDS))

    @staticmethod
    def _kick(bot: BotSession) -> None:
        """Lance le navigateur de l'instance ; avec des identifiants connus, la supervision se connecte et entre dans l'univers."""
        bot.start_in_background()
        if bot.has_credentials():
            bot.supervisor.reset_connection()
            bot.supervisor.ensure_keeper()

    def resolve(self, instance_id: str | None = None) -> BotSession:
        """Le bot d'une instance (la première si aucune n'est nommée ; `compte` : la session de connexion)."""
        if instance_id == LOBBY_ID:
            return self.lobby()
        target = instance_id or self.default_id
        if target is None:
            raise InstanceError("Aucune instance : créez-en une.", 409)
        self._info(target)
        bot = self._bots.get(target)
        if bot is None:
            raise InstanceError("Instance arrêtée : démarrez-la.", 409)
        return bot

    def running_bots(self) -> list[BotSession]:
        return list(self._bots.values())

    # ------------------------------------------------------------------ session de connexion
    def lobby(self) -> BotSession:
        if self._lobby is None:
            self._lobby = BotSession(replace(self._settings, data_dir=None), self._host, self._account)
            self._lobby.after_new_page = self.refront
        return self._lobby

    async def close_lobby(self) -> None:
        lobby, self._lobby = self._lobby, None
        if lobby is not None:
            await lobby.shutdown()

    async def scan_universes(self) -> dict:
        """Lit les serveurs proposés au compte, avec la session de connexion (elle se connecte seule si besoin)."""
        if not self._account.has_credentials():
            return {"universes": self.universes, "error": "Saisissez d'abord vos identifiants."}
        lobby = self.lobby()
        self._kick(lobby)
        if lobby._state is not State.LOGGED_IN:
            return {"universes": self.universes, "error": "Connexion en cours : réessayez dans quelques secondes."}
        found = [u["name"] for u in await lobby.list_universes()]
        if not found:
            return {"universes": self.universes, "error": "La liste des serveurs n'est pas affichée : réessayez dans un instant."}
        self.universes = found[:50]
        self._save()
        if self._bots:  # des instances tournent déjà : la session de connexion n'a plus d'utilité (un contexte de moins)
            await self.close_lobby()
        return {"universes": self.universes, "error": ""}

    # ------------------------------------------------------------------ cycle de vie
    def create(self, universe: str, name: str | None = None) -> dict:
        universe = (universe or "").strip()
        if not universe or len(universe) > MAX_UNIVERSE_LENGTH:
            raise InstanceError("Choisissez un serveur.")
        if any(norm(i["universe"]) == norm(universe) for i in self._infos):
            raise InstanceError("Ce serveur a déjà une instance : le jeu n'accepte qu'une session par serveur.", 409)
        if len(self._infos) >= self._settings.max_instances:
            raise InstanceError(f"Nombre maximal d'instances atteint ({self._settings.max_instances}).", 409)
        label = (name or universe).strip()[:MAX_NAME_LENGTH] or universe
        taken = {i["id"] for i in self._infos} | {LOBBY_ID}
        base = slugify(label) or "instance"
        instance_id, n = base, 1
        while instance_id in taken:
            n += 1
            suffix = f"-{n}"
            instance_id = base[: 40 - len(suffix)] + suffix
        if self._root is not None:
            try:
                Preferences(str(instance_dir(str(self._root), instance_id))).update({"universe": universe})
            except PreferencesError as exc:
                raise InstanceError("Nom de serveur refusé.") from exc
        info = {"id": instance_id, "name": label, "universe": universe, "autostart": False}
        self._infos.append(info)
        self._save()
        return dict(info)

    async def start(self, instance_id: str) -> None:
        async with self._lock:
            info = self._info(instance_id)
            if instance_id in self._bots:
                return
            bot = self._make_bot(instance_id)
            self._bots[instance_id] = bot
            info["autostart"] = True
            self._save()
            self._kick(bot)
        lobby = self._lobby
        if lobby is not None and lobby._state is not State.LOGGING_IN:  # la session de connexion n'a plus d'utilité
            await self.close_lobby()

    async def stop(self, instance_id: str) -> None:
        async with self._lock:
            info = self._info(instance_id)
            info["autostart"] = False
            self._save()
            bot = self._bots.pop(instance_id, None)
        if bot is not None:
            await bot.shutdown()

    async def delete(self, instance_id: str) -> None:
        """Arrête l'instance puis met son dossier à la corbeille (rien n'est effacé définitivement)."""
        self._info(instance_id)
        await self.stop(instance_id)
        async with self._lock:
            self._infos = [i for i in self._infos if i["id"] != instance_id]
            self._save()
            if self._root is not None:
                source = self._root / INSTANCES_FOLDER / instance_id
                if source.is_dir():
                    trash = self._root / INSTANCES_FOLDER / TRASH_FOLDER
                    trash.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(trash / f"{instance_id}-{int(time.time())}"))

    async def startup(self) -> None:
        """Au démarrage de l'application : les instances qui tournaient reprennent leur connexion (si AUTOSTART)."""
        if self._settings.autostart and self._bots:
            self._startup_task = asyncio.create_task(self._staggered_kick(self.running_bots()))

    async def _staggered_kick(self, bots: list[BotSession]) -> None:
        """Une instance après l'autre : les connexions au compte ne se font pas toutes à la même seconde."""
        for i, bot in enumerate(bots):
            if i:
                await asyncio.sleep(STARTUP_STAGGER_SECONDS)
            self._kick(bot)

    async def shutdown(self) -> None:
        """Arrêt de l'application : toutes les sessions se ferment ; les instances gardent leur marque « démarrée »."""
        task, self._startup_task = self._startup_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.close_lobby()
        bots, self._bots = list(self._bots.values()), {}
        await asyncio.gather(*(bot.shutdown() for bot in bots), return_exceptions=True)

    # ------------------------------------------------------------------ compte (commun à toutes les instances)
    async def logout_others(self, done: BotSession | None) -> None:
        """Déconnexion du joueur : les autres sessions repartent aussi d'un contexte vierge."""
        others = [b for b in [*self._bots.values(), self._lobby] if b is not None and b is not done]
        await asyncio.gather(*(b.logout() for b in others), return_exceptions=True)

    async def stop_keepers(self) -> None:
        """Identifiants oubliés : aucune session ne se reconnecte plus toute seule."""
        sessions = [b for b in [*self._bots.values(), self._lobby] if b is not None]
        await asyncio.gather(*(b.supervisor.stop_keeper() for b in sessions), return_exceptions=True)

    def credentials_changed(self, session: BotSession | None = None) -> None:
        """Nouveaux identifiants : les instances démarrées se reconnectent avec eux. Sans instance démarrée, c'est la
        session qui les a reçus (la session de connexion, en général) qui se connecte ; sinon elle reste inactive."""
        targets = self.running_bots() or ([session] if session is not None else [])
        for bot in targets:
            bot.supervisor.reset_connection()
            bot.supervisor.ensure_keeper()

    # ------------------------------------------------------------------ état
    def summary(self) -> dict:
        used = {norm(i["universe"]) for i in self._infos}
        lobby = self._lobby
        return {
            "default": self.default_id,
            "max": self._settings.max_instances,
            "memory_mb": chromium_memory_mb(),
            "instances": [self._describe(i) for i in self._infos],
            "universes": [{"name": n, "used": norm(n) in used} for n in self.universes],
            "lobby": {
                "open": lobby is not None,
                "state": lobby._state.value if lobby is not None else "idle",
                "has_credentials": self._account.has_credentials(),
            },
        }

    def _describe(self, info: dict) -> dict:
        bot = self._bots.get(info["id"])
        out = {**info, "running": bot is not None, "state": "stopped", "message": "", "page": "", "in_game": False,
               "bot_on": False, "connection": ""}
        if bot is not None:
            status = bot.status()
            out.update(
                state=status["state"], message=status["message"], page=status["page"],
                in_game="/game/" in status["page"], bot_on=status["scheduler"]["on"],
                connection=status["supervisor"]["connection"],
            )
        return out
