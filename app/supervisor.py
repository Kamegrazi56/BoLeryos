"""Supervision de la connexion et scan des planètes.

Dès que des identifiants sont connus, le bot se connecte tout seul, entre dans l'univers choisi, reste connecté
(reconnexion si la session expire) et lit la liste des planètes une fois par connexion. Il ne lance aucune action
de jeu : constructions, recherches, production, expéditions, espionnage et attaques n'existent plus.
"""

from __future__ import annotations

import asyncio
import time

from .numbers import norm
from .planets import GameUiError, Interrupted, PlanetsReader
from .shipyard import ModelScanner
from .preferences import Preferences

SCAN_AUTO_ATTEMPTS = 3  # tentatives du scan de démarrage par connexion (pas d'acharnement si le jeu change)
UNIVERSE_ATTEMPTS = 4  # entrées dans l'univers sans résultat avant de demander une intervention
UNKNOWN_SCREEN_SECONDS = 45  # écran non reconnu pendant ce temps : retour à la page de départ
LOGIN_FAILURES_BEFORE_BLOCK = 3
LOGIN_RETRY_DELAYS = (60, 300)  # secondes avant la 2e et la 3e tentative de connexion


class ConnectionSupervisor:
    def __init__(self, bot, prefs: Preferences) -> None:
        self.bot = bot
        self.prefs = prefs
        self._reader = PlanetsReader(bot, self._scan_guard)
        # Scan des planètes : lit la liste des planètes du joueur (au démarrage après connexion, ou à la demande).
        self.scanned_planets: list[dict] = []
        self.scan_at: float | None = None
        self.scan_source = ""
        self.scan_error = ""
        self.scanning = False
        self._scan_done_session = False  # le scan de démarrage a réussi pour cette connexion
        self._scan_attempts = 0  # tentatives automatiques pour cette connexion
        # Scan des modèles de vaisseaux (onglet Assemblages du Chantier spatial) : même principe que les planètes.
        self._models_reader = ModelScanner(bot)
        self.scanned_models: list[dict] = []
        self.models_scan_at: float | None = None
        self.models_scan_source = ""
        self.models_scan_error = ""
        self.scanning_models = False
        self._models_done_session = False
        self._models_attempts = 0
        # Connexion automatique
        self.connection = ""  # état affiché dans l'interface
        self._keeper: asyncio.Task | None = None
        self._ready_lock = asyncio.Lock()
        self._login_failures = 0
        self._login_blocked = ""  # raison si les tentatives automatiques sont suspendues
        self._next_login_at = 0.0
        self._unknown_since: float | None = None
        self._universe_attempts = 0
        self._in_game = False
        self._paused = False  # déconnecté par le planning (bot sur ON hors plage)
        self._last_notes: dict[str, str] = {}

    # ------------------------------------------------------------------ état
    def summary(self) -> dict:
        return {
            "connection": self.connection if self.bot.has_credentials() else "",
            "has_credentials": self.bot.has_credentials(),
            "universe": self.prefs.get()["universe"],
            "persistent": self.prefs.persistent,
        }

    def _note(self, key: str, message: str, level: str = "info", event: bool = False, kind: str = "info") -> None:
        """Écrit dans le journal sauf si c'est le même message que la fois précédente pour cette clé."""
        if self._last_notes.get(key) != message:
            self._last_notes[key] = message
            self.bot.log(message, level, event=event, kind=kind)

    # ------------------------------------------------------------ scan des planètes
    def game_blocker(self) -> str | None:
        """Pourquoi le bot ne peut pas agir dans le jeu maintenant (None : il peut)."""
        b = self.bot
        if b._manual:
            return "contrôle manuel activé"
        if b._state.value != "logged_in" or b._page is None or b._page.is_closed() or "/game/" not in b._page.url:
            return "le bot n'est pas dans une partie (connectez-vous et entrez dans l'univers)"
        return None

    async def _scan_guard(self) -> None:
        """Point de contrôle du scan : s'arrête si le contrôle manuel est activé ou si la session se ferme."""
        reason = self.game_blocker()
        if reason:
            raise Interrupted(reason)
        await asyncio.sleep(0)

    def scan_summary(self) -> dict:
        return {
            "planets": list(self.scanned_planets),
            "scanned_at": self.scan_at,
            "source": self.scan_source,
            "error": self.scan_error,
            "scanning": self.scanning,
        }

    async def scan_planets(self, source: str) -> dict:
        """Ouvre la liste des planètes du jeu, la lit et la garde. Ne lève jamais : l'échec est dans `error`."""
        if self.scanning:
            return self.scan_summary()
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.game_blocker() or busy
        if reason:
            self.scan_error = reason[0].upper() + reason[1:]
            return self.scan_summary()
        self.scanning = True
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                planets = await self._reader.list_planets()
        except (Interrupted, GameUiError) as exc:
            self.scan_error = f"Scan impossible : {exc}"
            self.bot.log(f"Scan des planètes impossible : {exc}.", "warn", event=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.scan_error = f"Scan impossible : erreur technique ({type(exc).__name__})"
            self.bot.log(f"Scan des planètes : erreur technique ({type(exc).__name__}).", "error", event=True)
        else:
            self.scanned_planets = [
                {"name": p.name, "coords": p.coords, "fer": p.stocks[0], "cristal": p.stocks[1], "ether": p.stocks[2]}
                for p in planets
            ]
            self.scan_at, self.scan_source, self.scan_error = time.time(), source, ""
            self._scan_done_session = True
            self.prefs.add_catalog("planets", [p.name for p in planets])
            self.bot.log(f"Scan des planètes ({source}) : {len(planets)} planète(s) trouvée(s).", event=True)
        finally:
            self.scanning = False
        return self.scan_summary()

    # ------------------------------------------------------------ scan des modèles
    def models_summary(self) -> dict:
        return {
            "models": list(self.scanned_models),
            "scanned_at": self.models_scan_at,
            "source": self.models_scan_source,
            "error": self.models_scan_error,
            "scanning": self.scanning_models,
        }

    def known_models(self) -> list[str]:
        """Noms des modèles : dernier scan, sinon ceux gardés des scans précédents."""
        return [m["name"] for m in self.scanned_models] or self.prefs.get()["catalog"]["models"]

    async def scan_models(self, source: str) -> dict:
        """Ouvre l'onglet Assemblages du Chantier spatial et lit les modèles. Ne lève jamais : l'échec est dans `error`."""
        if self.scanning_models:
            return self.models_summary()
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.game_blocker() or busy
        if reason:
            self.models_scan_error = reason[0].upper() + reason[1:]
            return self.models_summary()
        self.scanning_models = True
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                models = await self._models_reader.list_models()
        except (Interrupted, GameUiError) as exc:
            self.models_scan_error = f"Scan impossible : {exc}"
            self.bot.log(f"Scan des modèles impossible : {exc}.", "warn", event=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.models_scan_error = f"Scan impossible : erreur technique ({type(exc).__name__})"
            self.bot.log(f"Scan des modèles : erreur technique ({type(exc).__name__}).", "error", event=True)
        else:
            self.scanned_models = models
            self.models_scan_at, self.models_scan_source, self.models_scan_error = time.time(), source, ""
            self._models_done_session = True
            self.prefs.set_catalog("models", [m["name"] for m in models])
            self.bot.log(f"Scan des modèles ({source}) : {len(models)} modèle(s) trouvé(s).", event=True)
        finally:
            self.scanning_models = False
        return self.models_summary()

    # ------------------------------------------------------------ supervision de la connexion
    async def ensure_ready(self) -> str | None:
        async with self._ready_lock:  # une seule vérification de l'écran à la fois
            return await self._ensure_ready_locked()

    async def _ensure_ready_locked(self) -> str | None:
        """Regarde où en est le site et ne fait que ce qui manque : connexion, choix de l'univers, scan, ou rien.

        Retourne None quand le bot est dans une partie, sinon la raison de l'attente.
        """
        b = self.bot
        if b._manual:
            return "contrôle manuel activé"
        pause = b.planning_pause()
        if pause:  # bot sur ON hors planning : déconnexion une seule fois, puis plus rien jusqu'à la plage suivante
            if not self._paused:
                self._paused = True
                self._in_game = False
                await b.pause_session(pause)
            return pause
        if self._paused:
            self._paused = False
            b.log("Planning : début de plage, reconnexion au jeu.", event=True)
        if b._lock.locked():
            return "une autre opération est en cours"
        if b._page is None or b._page.is_closed():
            await b.ensure_started()
            return "démarrage du navigateur"
        screen = await b.detect_screen()
        if screen != "unknown":
            self._unknown_since = None
        if screen in ("game", "universes"):  # connecté (par le bot ou à la main) : les blocages de connexion tombent
            self._login_failures, self._login_blocked, self._next_login_at = 0, "", 0.0
        if screen != "game":
            self._in_game = False
        if screen in ("login", "universes"):  # nouvelle connexion : la liste des planètes est à relire
            self._scan_done_session, self._scan_attempts = False, 0
            self._models_done_session, self._models_attempts = False, 0
        if screen == "game":
            self._universe_attempts = 0
            if not self._in_game:  # arrivée (ou retour) dans la partie
                self._in_game = True
                b.log("Connecté à la partie.", event=True)
            if not self._scan_done_session and self._scan_attempts < SCAN_AUTO_ATTEMPTS and not b.work.locked():
                self._scan_attempts += 1
                await self.scan_planets("démarrage")  # une fois par connexion
            if self._scan_done_session and not self._models_done_session and self._models_attempts < SCAN_AUTO_ATTEMPTS and not b.work.locked():
                self._models_attempts += 1
                await self.scan_models("démarrage")  # une fois par connexion, après les planètes
            return None
        if screen == "universes":
            return await self._pick_universe()
        if screen == "login":
            return await self._log_in()
        # écran inconnu (chargement, redirection…) : on laisse un peu de temps puis on recharge la page de départ
        now = time.monotonic()
        self._unknown_since = self._unknown_since or now
        if now - self._unknown_since > UNKNOWN_SCREEN_SECONDS:
            self._unknown_since = None
            b.log(f"Écran non reconnu depuis {UNKNOWN_SCREEN_SECONDS} s : retour à la page de départ.", "warn")
            await b._page.goto(b._settings.login_url, wait_until="domcontentloaded", timeout=30_000)
        return "écran du site non reconnu"

    def reset_connection(self) -> None:
        """Nouveaux identifiants ou nouvelle demande : les blocages de connexion précédents tombent."""
        self._login_failures, self._login_blocked, self._next_login_at = 0, "", 0.0
        self._universe_attempts = 0

    def ensure_keeper(self) -> None:
        """Dès que des identifiants sont en mémoire, le bot se connecte et choisit l'univers tout seul, puis garde la
        connexion (reconnexion si la session expire)."""
        if self._keeper is None or self._keeper.done():
            self._keeper = asyncio.create_task(self._keep_connected())

    async def stop_keeper(self) -> None:
        task, self._keeper = self._keeper, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.connection = ""

    async def _keep_connected(self) -> None:
        poll = self.bot._settings.poll_seconds
        while self.bot.has_credentials():
            try:
                problem = await self.ensure_ready()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                problem = f"vérification de l'écran impossible ({type(exc).__name__})"
            self.connection = problem or "connecté, dans la partie"
            if problem:
                self._note("connection", f"Connexion automatique : {problem}.", event=True)
            else:
                self._last_notes.pop("connection", None)
            await asyncio.sleep(poll)
        self.connection = ""  # plus d'identifiants en mémoire : plus rien à maintenir

    async def _pick_universe(self) -> str | None:
        b = self.bot
        universes = await b.list_universes()
        self.prefs.add_catalog("universes", [u["name"] for u in universes])
        wanted = self.prefs.get()["universe"]
        if not wanted:
            return "choisissez un univers (Réglages › Compte et univers, ou bouton d'univers sur la page Écran)"
        match = next((u for u in universes if norm(u["name"]) == norm(wanted)), None)
        if match is None:
            return f"univers « {wanted} » indisponible sur la page de choix"
        self._universe_attempts += 1
        if self._universe_attempts > UNIVERSE_ATTEMPTS:
            return "l'entrée dans l'univers n'aboutit pas : entrez-y à la main (contrôle manuel) ; le bot reprendra seul"
        b.log(f"Page de choix de l'univers : entrée dans « {match['name']} ».", event=True)
        await b.enter_universe(match["index"])
        return "entrée dans l'univers en cours"

    async def _log_in(self) -> str | None:
        b = self.bot
        if self._login_blocked:
            return self._login_blocked
        if not b.has_credentials():
            return "identifiants requis (Réglages › Compte et univers)"
        wait = self._next_login_at - time.monotonic()
        if wait > 0:
            return f"nouvelle tentative de connexion dans {wait:.0f} s"
        identifier, password = b._creds  # type: ignore[misc]
        await b.login(identifier, password, supervised=True)
        if b._state.value == "logged_in":
            self._login_failures = 0
            return "connexion réussie, vérification de l'écran"
        kind = b.last_login_kind
        self._login_failures += 1
        if kind == "refused":  # identifiants refusés : ne pas réessayer (risque de blocage du compte)
            self._login_blocked = "connexion refusée par le site : corrigez les identifiants (Réglages › Compte et univers)"
            b.log("Reconnexion automatique suspendue : le site a refusé les identifiants (corrigez-les dans Réglages › Compte).", "error", kind="attention")
        elif self._login_failures >= LOGIN_FAILURES_BEFORE_BLOCK:
            self._login_blocked = "intervention requise (contrôle anti-bot ?) : connectez-vous à la main (page Écran, contrôle manuel) ; le bot reprendra seul"
            b.log("Trois tentatives de connexion sans résultat : connectez-vous à la main (page Écran, contrôle manuel) ; le bot reprendra seul.", "error", kind="attention")
        else:
            delay = LOGIN_RETRY_DELAYS[min(self._login_failures - 1, len(LOGIN_RETRY_DELAYS) - 1)]
            self._next_login_at = time.monotonic() + delay
            b.log(f"Connexion sans résultat : nouvelle tentative dans {delay} s.", "warn", event=True)
        return self._login_blocked or "connexion non aboutie"
