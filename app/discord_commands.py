"""Commandes Discord du bot : ce qu'on peut demander, et ce que chacune fait sur le bot et sur chaque instance.

Aucun accès réseau ici : le client Discord (`discord_gateway.py`) reçoit les messages, vérifie qui parle, puis appelle
`CommandRunner.execute`. Une réponse est un texte court ; les actions qui coupent le jeu demandent d'abord une confirmation
(réglable). Chaque ordre exécuté laisse une trace dans le journal de l'instance visée.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from . import discord_views as views
from .discord_pause import MAX_MINUTES, MIN_MINUTES, PauseTimers
from .discord_scan import scan_all
from .features import FEATURES, Feature, FeaturesError
from .instance_manager import InstanceError, InstanceManager
from .numbers import norm

MAX_REPLY = 1900  # Discord refuse plus de 2000 caractères par message
JOURNAL_LINES = 8
STATE_TEXT = {"idle": "non connecté", "logging_in": "connexion en cours", "logged_in": "connecté", "error": "connexion échouée"}

# Définition envoyée à Discord (commandes « slash » du serveur). Type 1 = sous-commande, type 3 = texte.
_INSTANCE_OPT = {"type": 3, "name": "instance", "description": "Nom de l'instance", "autocomplete": True}
_INSTANCE_REQ = {**_INSTANCE_OPT, "required": True}
_FEATURE_REQ = {"type": 3, "name": "fonction", "description": "Fonctionnalité du bot", "required": True, "autocomplete": True}
_REFRESH = {"type": 5, "name": "actualiser", "description": "Relire dans le jeu avant de répondre"}
_MINUTES = {"type": 4, "name": "minutes", "description": f"Durée de la pause en minutes ({MIN_MINUTES} à {MAX_MINUTES})",
            "required": True, "min_value": MIN_MINUTES, "max_value": MAX_MINUTES}


def _sub(name: str, description: str, *options: dict) -> dict:
    return {"type": 1, "name": name, "description": description, "options": list(options)}


COMMAND_DEFINITIONS: list[dict] = [
    {"name": "bot", "description": "Piloter le bot (interrupteur ON/OFF)", "options": [
        _sub("statut", "État de toutes les instances"),
        _sub("demarrer", "Mettre le bot sur ON", _INSTANCE_OPT),
        _sub("arreter", "Mettre le bot sur OFF", _INSTANCE_OPT),
    ]},
    {"name": "instance", "description": "Piloter les instances de jeu", "options": [
        _sub("liste", "Lister les instances"),
        _sub("statut", "Détail d'une instance", _INSTANCE_REQ),
        _sub("demarrer", "Démarrer la session d'une instance", _INSTANCE_REQ),
        _sub("arreter", "Arrêter la session d'une instance", _INSTANCE_REQ),
    ]},
    {"name": "fonction", "description": "Voir, activer, désactiver ou lancer une fonctionnalité", "options": [
        _sub("statut", "Fonctionnalités actives et dernier passage", _INSTANCE_OPT),
        _sub("activer", "Activer une fonctionnalité", _FEATURE_REQ, _INSTANCE_OPT),
        _sub("desactiver", "Désactiver une fonctionnalité", _FEATURE_REQ, _INSTANCE_OPT),
        _sub("lancer", "Faire passer une fonctionnalité activée tout de suite", _FEATURE_REQ, _INSTANCE_OPT),
    ]},
    {"name": "journal", "description": "Derniers événements du bot", "options": [_INSTANCE_OPT]},
    {"name": "prochain", "description": "Prochain passage de chaque fonctionnalité", "options": [_INSTANCE_OPT]},
    {"name": "flottes", "description": "Flottes en vol et retours programmés", "options": [_INSTANCE_OPT, _REFRESH]},
    {"name": "planetes", "description": "Planètes et leurs stocks", "options": [_INSTANCE_OPT, _REFRESH]},
    {"name": "pause", "description": "Bot sur OFF pendant une durée, puis de nouveau sur ON", "options": [_MINUTES, _INSTANCE_OPT]},
    {"name": "connexion", "description": "Relancer la connexion au jeu", "options": [_INSTANCE_OPT]},
    {"name": "scan", "description": "Relire dans le jeu ce dont les fonctionnalités ont besoin", "options": [_INSTANCE_OPT]},
]


@dataclass(frozen=True)
class Reply:
    text: str
    confirm: str | None = None  # action à confirmer (« bot:arreter:<instance> ») ; None : rien à confirmer


class CommandRunner:
    def __init__(self, manager: InstanceManager, confirm_enabled: Callable[[], bool]) -> None:
        self._manager = manager
        self._confirm_enabled = confirm_enabled
        self._pauses = PauseTimers()
        self._handlers: dict[tuple[str, str], Callable[[dict, bool], Awaitable[Reply]]] = {
            ("bot", "statut"): self._bot_status,
            ("bot", "demarrer"): self._bot_on,
            ("bot", "arreter"): self._bot_off,
            ("instance", "liste"): self._instance_list,
            ("instance", "statut"): self._instance_status,
            ("instance", "demarrer"): self._instance_start,
            ("instance", "arreter"): self._instance_stop,
            ("fonction", "statut"): self._per_instance(views.features_status),
            ("fonction", "activer"): lambda o, c: self._feature(o, True),
            ("fonction", "desactiver"): lambda o, c: self._feature(o, False),
            ("fonction", "lancer"): self._feature_launch,
            ("journal", ""): self._journal,
            ("prochain", ""): self._per_instance(views.next_passes),
            ("flottes", ""): self._fleets,
            ("planetes", ""): self._planets,
            ("pause", ""): self._pause,
            ("connexion", ""): self._reconnect,
            ("scan", ""): self._scan,
        }

    async def execute(self, path: tuple[str, str], options: dict, confirmed: bool = False) -> Reply:
        handler = self._handlers.get(path)
        if handler is None:
            return Reply("Commande inconnue.")
        try:
            reply = await handler(options, confirmed)
        except InstanceError as exc:
            return Reply(exc.message)
        return Reply(reply.text[:MAX_REPLY], reply.confirm)

    # ------------------------------------------------------------------ choix proposés pendant la saisie
    def suggestions(self, option: str, typed: str) -> list[dict]:
        if option == "instance":
            pairs = [(i["name"], i["id"]) for i in self._manager.summary()["instances"]]
        elif option == "fonction":
            pairs = [(f.label, f.id) for f in FEATURES]
        else:
            return []
        wanted = norm(typed)
        return [{"name": label[:100], "value": value} for label, value in pairs if wanted in norm(label) or wanted in value][:25]

    def instances(self) -> list[dict]:
        """Les instances (nom, univers, démarrée ou non) : de quoi dessiner les menus du panneau."""
        return self._infos()

    # ------------------------------------------------------------------ outils
    def _infos(self) -> list[dict]:
        return self._manager.summary()["instances"]

    def _find(self, wanted: str) -> dict:
        """L'instance désignée par son identifiant ou par son nom (début de nom accepté s'il est sans ambiguïté)."""
        infos, key = self._infos(), norm(wanted)
        exact = [i for i in infos if i["id"] == wanted or norm(i["name"]) == key]
        found = exact or [i for i in infos if norm(i["name"]).startswith(key)]
        if not found:
            raise InstanceError("Instance inconnue : « /instance liste » donne les noms.", 404)
        if len(found) > 1:
            raise InstanceError("Plusieurs instances correspondent : précisez le nom.", 409)
        return found[0]

    def _targets(self, options: dict) -> list[dict]:
        """Les instances démarrées visées : celle qui est nommée, sinon toutes."""
        if options.get("instance"):
            info = self._find(options["instance"])
            if not info["running"]:
                raise InstanceError(f"« {info['name']} » est arrêtée : « /instance demarrer » la démarre.", 409)
            return [info]
        running = [i for i in self._infos() if i["running"]]
        if not running:
            raise InstanceError("Aucune instance n'est démarrée.", 409)
        return running

    @staticmethod
    def _describe(info: dict, bot=None) -> str:
        head = f"**{info['name']}** ({info['universe'] or 'serveur non choisi'})"
        if not info["running"]:
            return f"{head} : arrêtée"
        state = STATE_TEXT.get(info["state"], info["state"])
        parts = [state, "dans le jeu" if info["in_game"] else "", f"bot {'ON' if info['bot_on'] else 'OFF'}"]
        if bot is not None:
            parts.append(views.activity(bot.scheduler.activity()))
        return f"{head} : " + ", ".join(p for p in parts if p)

    # ------------------------------------------------------------------ /bot
    async def _bot_status(self, options: dict, confirmed: bool) -> Reply:
        lines = [self._describe(i, self._manager.resolve(i["id"]) if i["running"] else None) for i in self._infos()]
        return Reply("\n".join(lines) or "Aucune instance : créez-en une depuis la page Accueil.")

    async def _bot_on(self, options: dict, confirmed: bool) -> Reply:
        names = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            self._pauses.cancel(info["id"])  # le dernier ordre l'emporte sur une pause en cours
            bot.scheduler.start()
            bot.log("Bot mis sur ON par une commande Discord.", event=True)
            names.append(info["name"])
        return Reply("Bot sur ON : " + ", ".join(names) + ".")

    async def _bot_off(self, options: dict, confirmed: bool) -> Reply:
        targets = self._targets(options)
        if not options.get("instance") and self._confirm_enabled() and not confirmed:
            return Reply(f"Mettre le bot sur OFF sur {len(targets)} instance(s) ?", "bot:arreter:")
        for info in targets:
            bot = self._manager.resolve(info["id"])
            self._pauses.cancel(info["id"])
            bot.stop_with_missions()
            bot.log("Bot mis sur OFF par une commande Discord.", event=True)
        return Reply("Bot sur OFF : " + ", ".join(i["name"] for i in targets) + ". Le passage en cours se termine.")

    # ------------------------------------------------------------------ /instance
    async def _instance_list(self, options: dict, confirmed: bool) -> Reply:
        infos = self._infos()
        return Reply("\n".join(self._describe(i) for i in infos) or "Aucune instance : créez-en une depuis la page Accueil.")

    async def _instance_status(self, options: dict, confirmed: bool) -> Reply:
        info = self._find(options.get("instance", ""))
        if not info["running"]:
            return Reply(self._describe(info))
        bot = self._manager.resolve(info["id"])
        active = [f["label"] for f in bot.scheduler.summary()["features"] if f["enabled"]]
        lines = [self._describe(info, bot), f"Fonctionnalités actives : {', '.join(active) or 'aucune'}."]
        if info["connection"]:
            lines.append(f"Connexion : {info['connection']}")
        return Reply("\n".join(lines))

    async def _instance_start(self, options: dict, confirmed: bool) -> Reply:
        info = self._find(options.get("instance", ""))
        if info["running"]:
            return Reply(f"« {info['name']} » est déjà démarrée.")
        await self._manager.start(info["id"])
        self._manager.resolve(info["id"]).log("Instance démarrée par une commande Discord.", event=True)
        return Reply(f"« {info['name']} » démarre : la connexion prend quelques secondes.")

    async def _instance_stop(self, options: dict, confirmed: bool) -> Reply:
        info = self._find(options.get("instance", ""))
        if not info["running"]:
            return Reply(f"« {info['name']} » est déjà arrêtée.")
        if self._confirm_enabled() and not confirmed:
            return Reply(f"Arrêter la session de « {info['name']} » ? Le bot s'y arrête aussi.", f"instance:arreter:{info['id']}")
        await self._manager.stop(info["id"])
        return Reply(f"« {info['name']} » est arrêtée.")

    # ------------------------------------------------------------------ /fonction et /journal
    @staticmethod
    def _find_feature(wanted: str) -> Feature | None:
        key = norm(wanted)
        if not key:
            return None
        return next((f for f in FEATURES if f.id == key or norm(f.label) == key), None) \
            or next((f for f in FEATURES if norm(f.label).startswith(key)), None)

    async def _feature(self, options: dict, enabled: bool) -> Reply:
        feature = self._find_feature(options.get("fonction", ""))
        if feature is None:
            return Reply("Fonctionnalité inconnue : la saisie propose la liste.")
        names = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            try:
                bot.scheduler.update(feature.id, {"enabled": enabled})
            except FeaturesError as exc:
                return Reply(f"Refusé sur « {info['name']} » : {' '.join(exc.errors)}")
            names.append(info["name"])
        verb = "activée" if enabled else "désactivée"
        return Reply(f"« {feature.label} » {verb} sur : {', '.join(names)}. (Le bot doit être sur ON pour qu'elle tourne.)")

    async def _feature_launch(self, options: dict, confirmed: bool) -> Reply:
        """Fait passer la fonctionnalité au prochain tour de l'ordonnanceur, sans attendre son minuteur."""
        feature = self._find_feature(options.get("fonction", ""))
        if feature is None:
            return Reply("Fonctionnalité inconnue : la saisie propose la liste.")
        lines = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            lines.append(f"**{info['name']}** : {self._launch_one(bot, feature)}")
        return Reply("\n".join(lines))

    @staticmethod
    def _launch_one(bot, feature: Feature) -> str:
        item = next(f for f in bot.scheduler.summary()["features"] if f["id"] == feature.id)
        if not item["enabled"]:
            return f"« {feature.label} » est désactivée : « /fonction activer » d'abord."
        if item["running"]:
            return f"« {feature.label} » est déjà en cours."
        bot.scheduler.wake(feature.id)
        bot.log(f"Fonctionnalité « {feature.label} » lancée par une commande Discord.", event=True)
        if not bot.scheduler.on:
            return f"« {feature.label} » passera dès que le bot sera sur ON."
        reason = bot.feature_pause(feature.id)
        if reason:
            return f"« {feature.label} » est demandée mais attend : {reason}."
        return f"« {feature.label} » passe au prochain tour (quelques secondes si le bot est libre)."

    async def _journal(self, options: dict, confirmed: bool) -> Reply:
        blocks = []
        for info in self._targets(options):
            events = self._manager.resolve(info["id"]).events(JOURNAL_LINES)
            lines = [f"`{e['ts']}` {e['msg']}" for e in events] or ["Aucun événement."]
            blocks.append("\n".join([f"**{info['name']}**", *lines]))
        return Reply("\n\n".join(blocks))


    # ------------------------------------------------------------------ lecture de l'état : /prochain /flottes /planetes
    def _per_instance(self, render: Callable[[object], str]) -> Callable[[dict, bool], Awaitable[Reply]]:
        """Commande de lecture : un bloc de texte par instance démarrée visée."""
        async def handler(options: dict, confirmed: bool) -> Reply:
            blocks = [f"**{info['name']}**\n{render(self._manager.resolve(info['id']))}" for info in self._targets(options)]
            return Reply("\n\n".join(blocks))
        return handler

    @staticmethod
    def _asked_refresh(options: dict) -> bool:
        return str(options.get("actualiser", "")).lower() == "true"

    async def _fleets(self, options: dict, confirmed: bool) -> Reply:
        blocks = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            note = ""
            if self._asked_refresh(options):
                error = (await bot.fleet.scan("Discord")).get("error")
                note = f"Relevé impossible ({error}) : dernier relevé gardé.\n" if error else ""
            blocks.append(f"**{info['name']}**\n{note}{views.fleets_in_flight(bot)}")
        return Reply("\n\n".join(blocks))

    async def _planets(self, options: dict, confirmed: bool) -> Reply:
        blocks = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            note = ""
            if self._asked_refresh(options):
                error = (await bot.supervisor.scan_planets("Discord")).get("error")
                note = f"Scan impossible ({error}) : dernier relevé gardé.\n" if error else ""
            blocks.append(f"**{info['name']}**\n{note}{views.planets_stocks(bot)}")
        return Reply("\n\n".join(blocks))

    # ------------------------------------------------------------------ /pause
    async def _pause(self, options: dict, confirmed: bool) -> Reply:
        try:
            minutes = int(options.get("minutes", ""))
        except ValueError:
            return Reply("Durée illisible : indiquez un nombre de minutes.")
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            return Reply(f"Durée refusée : de {MIN_MINUTES} à {MAX_MINUTES} minutes.")
        lines = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            if not bot.scheduler.on:
                lines.append(f"**{info['name']}** : le bot est déjà sur OFF, rien à mettre en pause.")
                continue
            instance_id = info["id"]
            back = self._pauses.start(instance_id, bot, minutes, lambda i=instance_id, b=bot: b in self._manager.running_bots())
            bot.log(f"Bot en pause {minutes} min par une commande Discord.", event=True)
            lines.append(f"**{info['name']}** : bot sur OFF, il repasse sur ON à {back:%H:%M} (heure française). Le passage en cours se termine.")
        return Reply("\n".join(lines))

    # ------------------------------------------------------------------ /connexion et /scan
    async def _reconnect(self, options: dict, confirmed: bool) -> Reply:
        lines = []
        for info in self._targets(options):
            bot = self._manager.resolve(info["id"])
            if not bot.has_credentials():
                bot.restore_credentials()  # identifiants enregistrés (après une déconnexion, par exemple)
            if not bot.has_credentials():
                lines.append(f"**{info['name']}** : aucun identifiant en mémoire, saisissez-les d'abord dans l'interface.")
                continue
            bot.supervisor.reset_connection()
            bot.supervisor.ensure_keeper()
            bot.log("Connexion relancée par une commande Discord.", event=True)
            lines.append(f"**{info['name']}** : connexion relancée (« /instance statut » montre où elle en est).")
        return Reply("\n".join(lines))

    async def _scan(self, options: dict, confirmed: bool) -> Reply:
        blocks = []
        for info in self._targets(options):
            blocks.append(f"**{info['name']}**\n{await scan_all(self._manager.resolve(info['id']))}")
        return Reply("\n\n".join(blocks))
