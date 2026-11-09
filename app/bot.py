"""Session du bot : navigateur Playwright toujours actif, connexion, journal et flux d'images.

Le navigateur démarre dès qu'un écran de l'interface est ouvert et affiche la page d'accueil du site,
même sans connexion. La page est capturée en continu pour l'affichage en direct.

Règles sur les identifiants du joueur :
- ils arrivent par `login()` depuis l'interface web, jamais depuis l'environnement ;
- ils ne sont ni stockés, ni journalisés, ni renvoyés par l'API ;
- seule la session du navigateur (cookies) est gardée après la connexion : en mémoire, et dans le dossier de
  données (droits 0600) pour survivre au redémarrage du conteneur ; elle est effacée à la déconnexion.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from . import human, interruptions
from .config import Settings
from .session_store import SessionStore
from .display import VirtualDisplay
from .explore import MAX_SNAPSHOTS, SNAPSHOT_JS, Recorder, should_skip_host
from .preferences import Preferences
from .builder import ConstructionPass
from .constructions import ConstructionSettings
from . import components
from .populations import PopulationSettings
from .quantities import LineSettings, LineSpec
from .recruiter import PopulationPass
from .researcher import ResearchPass
from .researches import ResearchSettings
from .shipyard import ComponentPass, ShipPass
from .explorations import ExplorationStore
from .explorer import ExplorationPass
from .features import FEATURES, FeatureStore
from . import bonus, planning, recovery
from .alliance import AllianceSettings, TokenPass
from .miner import MiningPass
from .minings import MiningStore
from .missions import MissionClaimer, MissionsState
from .planning import PlanningStore
from .excavations import ExcavationStore
from .excavator import ExcavationPass
from .hacker import HackingPass
from .hackings import HackingStore
from .scheduler import Scheduler
from .spy import SpyCampaign
from .spying import SpySettings
from .supervisor import ConnectionSupervisor
from .vault import CredentialVault

TURNSTILE_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'
COOKIE_REFUSE_NAME = "tout refuser"
# Bouton d'entrée d'un univers (seul libellé observé : « Reprendre la partie » ; à compléter après exploration).
UNIVERSE_BUTTON_RE = re.compile(r"reprendre la partie", re.I)

# Point rouge qui suit la souris : rend les déplacements du bot visibles dans la vue en direct.
CURSOR_SCRIPT = """
(() => {
  if (window.top !== window) return;
  const dot = document.createElement('div');
  dot.style.cssText = 'position:fixed;z-index:2147483647;width:14px;height:14px;border-radius:50%;'
    + 'background:rgba(255,60,60,.8);border:2px solid #fff;pointer-events:none;left:-30px;top:-30px;'
    + 'transform:translate(-50%,-50%)';
  const add = () => document.documentElement.appendChild(dot);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
  addEventListener('mousemove', (e) => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
})();
"""


# Touches que le contrôle manuel peut envoyer au navigateur (liste fermée : rien d'autre n'est accepté).
_KEY_RE = re.compile(
    r"^(?:(?:Control|Alt|Shift|Meta)\+)*"
    r"(?:[A-Za-z0-9]|Enter|Backspace|Tab|Escape|Delete|Home|End|PageUp|PageDown|Arrow(?:Up|Down|Left|Right)|F\d{1,2})$"
)
_MOUSE_BUTTONS = {"left", "middle", "right"}
VIEWPORT_WIDTH = 1440
VIEWPORT_MIN_HEIGHT, VIEWPORT_MAX_HEIGHT = 600, 1100
SESSION_SAVE_INTERVAL = 60.0  # secondes entre deux enregistrements des cookies (connecté)
SCREENCAST_MIN_INTERVAL = 0.03  # secondes entre deux images acceptées de Chrome


def _num(value, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


class State(str, Enum):
    IDLE = "idle"
    LOGGING_IN = "logging_in"
    LOGGED_IN = "logged_in"
    ERROR = "error"


@dataclass
class _Outcome:
    success: bool
    message: str
    kind: str = ""  # « refused » (le site refuse), « timeout » (aucun résultat : Turnstile ?), « technical »


def _mask(identifier: str) -> str:
    """Version masquée de l'identifiant, pour le journal (jamais la valeur complète)."""
    return identifier[:2] + "***" if identifier else "***"


class BotSession:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()  # connexion / déconnexion
        self._start_lock = asyncio.Lock()  # démarrage du navigateur
        self.work = asyncio.Lock()  # un seul travail dans le jeu à la fois : passage d'une fonctionnalité ou scan des planètes
        self._boot: asyncio.Future | None = None  # démarrage du navigateur en cours
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._state = State.IDLE
        self._message = "Non connecté."
        self._logs: deque[dict[str, str]] = deque(maxlen=1000)
        self._dirty = False  # une tentative de connexion a déjà eu lieu dans ce contexte
        self._manual = False  # contrôle manuel (clics/clavier de l'utilisateur) activé
        # Identifiants gardés EN MÉMOIRE seulement (jamais sur disque ni dans le journal), pour se reconnecter tout seul.
        self._creds: tuple[str, str] | None = None
        self.last_login_kind = ""
        self._viewport = {"width": VIEWPORT_WIDTH, "height": 900}
        self._last_typing_log = 0.0
        # Contrôle manuel : file des entrées (souris, clavier) traitée dans l'ordre par une seule tâche ; les
        # mouvements de souris en attente sont fusionnés pour qu'un clic ne patiente jamais derrière eux.
        self._input_queue: deque[tuple[str, dict]] = deque()
        self._input_wakeup = asyncio.Event()
        self._input_task: asyncio.Task | None = None
        self._mouse_at: tuple[float, float] | None = None
        self._window_size: tuple[int, int] | None = None  # navigateur complet : taille voulue de la fenêtre
        self._mouse_buttons = 0
        self.input_stats = {"received": 0, "applied": 0, "merged": 0}
        # Flux d'images
        self._capture_task: asyncio.Task | None = None
        self._start_task: asyncio.Task | None = None
        self._viewers = 0
        self._frame_id = 0
        self._frame_jpeg: bytes | None = None
        self._last_frame_time = 0.0
        self._frame_event = asyncio.Event()
        self._cdp = None  # session CDP de la page courante (flux d'images poussé par Chrome)
        # Navigateur complet : vraie fenêtre Chromium sur un écran virtuel affiché par noVNC (image Docker)
        self.display = (VirtualDisplay(settings.vnc_display, settings.vnc_width, settings.vnc_height, settings.vnc_port)
                        if settings.vnc and VirtualDisplay.available() else None)
        self._screencasting = False
        self._tasks: set[asyncio.Task] = set()
        # Préférences non secrètes (univers, délai de connexion) et supervision : connexion automatique et scan des planètes
        self.supervisor = ConnectionSupervisor(self, Preferences(settings.data_dir))
        self.constructions = ConstructionSettings(settings.data_dir)  # liste d'objectifs de construction
        self.builder = ConstructionPass(self)  # lancement des constructions (clic sur « Améliorer »)
        self.researches = ResearchSettings(settings.data_dir)  # liste d'objectifs de recherche + planète chargée des recherches
        self.researcher = ResearchPass(self)  # lancement des recherches (clic sur « Rechercher »)
        self.populations = PopulationSettings(settings.data_dir)  # lignes de recrutement de population (mode, quantité, planète)
        self.recruiter = PopulationPass(self)  # recrutement (clic sur « Recruter »)
        self.ships = LineSettings(settings.data_dir, LineSpec("ships.json", "modèle"))  # Assemblages : modèle + mode + quantité + planètes
        self.shipper = ShipPass(self, self.ships)  # construction de vaisseaux avec des ressources (mode Construction, bouton « Lancer »)
        self.components = LineSettings(settings.data_dir, components.SPEC)  # Composants : composant + mode + quantité + planètes
        self.producer = ComponentPass(self, self.components)  # production de composants (bouton « Produire »)
        self.explorations = ExplorationStore(settings.data_dir)  # réglages d'exploration + relevé du jour (brèches, modèles)
        self.explorer = ExplorationPass(self, self.explorations)  # explorations vers la brèche la plus proche de chaque planète
        self.excavations = ExcavationStore(settings.data_dir)  # Fouilles : lignes, délai, relevé du jour, Fouilles suivies
        self.excavator = ExcavationPass(self, self.excavations)  # Fouilles vers le site libre le plus proche
        self.hackings = HackingStore(settings.data_dir)  # Piratage : planète de départ, délai, Errants les plus proches, suivi
        self.hacker = HackingPass(self, self.hackings)  # Naël Soryn pirate en boucle l'Errant le plus proche
        self.minings = MiningStore(settings.data_dir)  # forages : planètes, ressources, Mineurs, places, délai, suivi
        self.miner = MiningPass(self, self.minings)  # forages de planètes minières
        self.alliance = AllianceSettings(settings.data_dir)  # jetons d'alliance à donner à chaque passage (tous, 1 ou 2)
        self.token_giver = TokenPass(self, self.alliance)  # dons de jetons à la technologie prioritaire de l'alliance
        self.mission_state = MissionsState(settings.data_dir)  # date de la dernière récupération des missions
        self.missions = MissionClaimer(self, self.mission_state)  # « Récupérer » de l'onglet Missions, 1 fois par jour
        self._missions_task: asyncio.Task | None = None
        self.planning = PlanningStore(settings.data_dir)  # plages horaires actives par jour (heure française)
        self.spying = SpySettings(settings.data_dir)  # espionnage des Errants : planète, minimums, nombre de ressources
        self.spy = SpyCampaign(self, self.spying)  # campagne manuelle : un drone par Errant visible, rapports gardés ou supprimés
        # Ordonnanceur des fonctionnalités (réglages enregistrés ; l'interrupteur général repasse toujours à OFF au démarrage)
        self.scheduler = Scheduler(
            self,
            FeatureStore(settings.data_dir, FEATURES, settings.feature_min_minutes),
            FEATURES,
            blocker=self._feature_blocker,
            gap=(settings.feature_gap_min, settings.feature_gap_max),
            tick=settings.scheduler_tick,
        )
        # Identifiants enregistrés (chiffrés) : relus en mémoire au démarrage, SANS se connecter tant que
        # l'utilisateur ne le demande pas (« Relancer la connexion »).
        self.vault = CredentialVault(settings.data_dir)
        self.restore_credentials()
        # Cookies du navigateur : rechargés au démarrage pour rester connecté après un redémarrage du conteneur.
        self._session_store = SessionStore(settings.data_dir)
        self._last_state_save = 0.0
        self._session_restored = False  # les cookies de la session précédente ont été rechargés au démarrage du navigateur
        if self.vault.problem:
            self.log(f"Identifiants : {self.vault.problem}.", "warn", event=True)
        # Exploration de la structure du site (désactivée par défaut)
        self._recorder = Recorder()
        self._snapshot_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ état
    def reset_failures(self, feature_id: str) -> None:
        """Remet à zéro les lancements non confirmés d'une fonctionnalité (appelé quand l'utilisateur la réactive)."""
        passes = {"constructions": self.builder, "researches": self.researcher, "populations": self.recruiter,
                  "ships": self.shipper, "components": self.producer, "explorations": self.explorer, "excavations": self.excavator, "hackings": self.hacker, "minings": self.miner}
        if feature_id in passes:
            passes[feature_id]._unconfirmed = 0

    def planning_pause(self) -> str | None:
        """Bot sur ON hors d'une plage du planning : rien ne tourne et le bot se déconnecte (None : il peut agir).
        Sur OFF, en contrôle manuel ou pendant une campagne d'espionnage (manuelle), le planning ne s'applique pas."""
        if not self.scheduler.on or self._manual or self.spy.running:
            return None
        return planning.pause_reason(self.planning.get())

    def _feature_blocker(self) -> str | None:
        return self.planning_pause() or self.supervisor.game_blocker()

    async def claim_missions(self, reason: str) -> str | None:
        """(A) Le joueur met le bot sur OFF : récompenses des missions (1 fois par jour), après le passage en cours."""
        if self.mission_state.done_today() or self.supervisor.game_blocker():
            return None
        async with self.work:
            return await self.missions.claim_daily(reason)

    def stop_with_missions(self) -> None:
        """Bouton OFF : plus aucun passage, puis récupération des missions du jour en tâche de fond."""
        was_on = self.scheduler.on
        self.scheduler.stop()
        if was_on and (self._missions_task is None or self._missions_task.done()):
            self._missions_task = asyncio.create_task(self.claim_missions("l'arrêt du bot (OFF)"))

    def _last_slot_of_day(self) -> bool:
        """Le planning ne prévoit plus de plage aujourd'hui (date française)."""
        nxt = planning.state(self.planning.get())["next"]
        return nxt is None or nxt.date() > datetime.now(planning.TIMEZONE).date()

    async def pause_session(self, reason: str) -> None:
        """Hors planning : le passage en cours se termine, puis la session de jeu est effacée (déconnexion).
        Les identifiants restent en mémoire : la reconnexion se fait seule au début de la plage suivante.
        (B) Fin de la dernière plage de la journée : les récompenses des missions sont récupérées juste avant."""
        async with self.work:
            if self._last_slot_of_day() and not self.supervisor.game_blocker():
                await self.missions.claim_daily("la fin de la dernière plage du planning")
            self._session_store.clear()
            async with self._lock:
                if self._page is not None:
                    try:
                        await self._reset_context()
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"Erreur pendant la déconnexion ({type(exc).__name__}).", "error")
                self._dirty = False
                self._set(State.IDLE, f"Déconnecté : {reason}.")
        self.log(f"Planning : {reason} ; déconnexion du jeu.", event=True)

    async def claim_bonus(self) -> str | None:
        """Récupère le bonus d'activité s'il est disponible (appelé au début de chaque action du bot)."""
        return await bonus.claim(self._page, self._settings, self.log)

    async def recovery_screen(self) -> bool:
        """Vrai si le jeu affiche l'écran « Récupération interrompue » (vérifié par l'ordonnanceur après chaque passage)."""
        return await recovery.screen_shown(self._page)

    async def retry_recovery(self) -> bool:
        """Clique sur « Réessayer » de l'écran « Récupération interrompue », s'il est affiché."""
        return await recovery.press_retry(self._page)

    def log(self, message: str, level: str = "info", event: bool = False, kind: str = "info") -> None:
        """`event` : fait marquant, affiché dans « Derniers événements » et le journal lisible.
        `kind` : « attention » signale une intervention nécessaire (c'est aussi un fait marquant)."""
        self._logs.append(
            {
                "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "t": time.time(),
                "level": level,
                "msg": message,
                "event": event or kind != "info",
                "kind": kind,
            }
        )

    def logs(self, limit: int = 50) -> list[dict]:
        return list(self._logs)[-limit:]

    def events(self, limit: int = 10) -> list[dict]:
        return [e for e in self._logs if e["event"]][-limit:]

    def status(self) -> dict[str, str]:
        return {
            "state": self._state.value,
            "message": self._message,
            "page": self._page_label(),
            "manual": self._manual,
            "display": "vnc" if self.display is not None else "images",
            "explore": {"recording": self._recorder.enabled, **self._recorder.counts()},
            "supervisor": self.supervisor.summary(),
            "scheduler": {"on": self.scheduler.on, "waiting": self.scheduler.waiting},
        }

    def _page_label(self) -> str:
        """Origine + chemin de la page courante (sans query ni fragment, qui peuvent porter des jetons)."""
        if not self._page or self._page.is_closed():
            return ""
        parts = urlsplit(self._page.url)
        return f"{parts.netloc}{parts.path}"

    def _set(self, state: State, message: str) -> None:
        self._state = state
        self._message = message

    # ---------------------------------------------------- flux d'images (live)
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def viewer_joined(self) -> None:
        self._viewers += 1
        if self._viewers == 1:
            self._spawn(self._start_screencast())

    def viewer_left(self) -> None:
        self._viewers = max(0, self._viewers - 1)
        if self._viewers == 0:
            self._spawn(self._stop_screencast())
            if self._manual:  # personne ne regarde : on rend la main au bot
                self._manual = False
                self.log("Contrôle manuel désactivé (plus aucun écran ouvert).", event=True)

    def frame(self) -> tuple[int, bytes | None]:
        """Dernière image (JPEG) et son numéro ; le numéro change à chaque nouvelle image."""
        return self._frame_id, self._frame_jpeg

    async def wait_frame(self, timeout: float) -> None:
        """Attend l'arrivée d'une nouvelle image (ou la fin du délai)."""
        try:
            await asyncio.wait_for(self._frame_event.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    def _publish_frame(self, jpeg: bytes) -> None:
        self._frame_jpeg = jpeg
        self._frame_id += 1
        self._last_frame_time = time.monotonic()
        self._frame_event.set()  # réveille les écrans en attente, puis on réarme
        self._frame_event.clear()

    async def _attach_screencast(self) -> None:
        """Chrome pousse lui-même une image à chaque changement de la page (bien plus réactif que des captures)."""
        if self._context is None or self._page is None:
            return
        cdp = await self._context.new_cdp_session(self._page)
        self._cdp = cdp
        self._screencasting = False

        async def on_frame(params: dict) -> None:
            try:
                self._publish_frame(base64.b64decode(params["data"]))
                # Retarder l'accusé plafonne le débit (~30 images/s) sans jamais perdre le dernier
                # changement : Chrome regroupe ce qui se passe entre-temps dans l'image suivante.
                await asyncio.sleep(SCREENCAST_MIN_INTERVAL)
            finally:
                try:  # sans accusé de réception, Chrome n'envoie plus d'images
                    await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                except Exception:  # noqa: BLE001
                    pass

        cdp.on("Page.screencastFrame", on_frame)
        if self._viewers > 0:
            await self._start_screencast()

    async def _start_screencast(self) -> None:
        if self._cdp is None or self._screencasting:
            return
        try:
            await self._cdp.send(
                "Page.startScreencast",
                {
                    "format": "jpeg",
                    "quality": 60,
                    "maxWidth": VIEWPORT_WIDTH,
                    "maxHeight": VIEWPORT_MAX_HEIGHT,
                    "everyNthFrame": 1,
                },
            )
            self._screencasting = True
        except Exception:  # noqa: BLE001 - la capture de secours prend le relais
            self._screencasting = False

    async def _stop_screencast(self) -> None:
        if self._cdp is None or not self._screencasting or self._viewers > 0:
            return
        self._screencasting = False
        try:
            await self._cdp.send("Page.stopScreencast")
        except Exception:  # noqa: BLE001
            pass

    async def _capture_loop(self) -> None:
        """Filet de sécurité : une capture classique si Chrome n'a envoyé aucune image depuis 1 s
        (page immobile, flux interrompu, changement de page)."""
        while True:
            try:
                page = self._page
                idle = time.monotonic() - self._last_frame_time
                if self.display is None and self._viewers > 0 and page is not None and not page.is_closed() and idle > 1.0:
                    data = await page.screenshot(type="jpeg", quality=60, timeout=5_000)
                    if data != self._frame_jpeg:
                        self._publish_frame(data)
                    else:
                        self._last_frame_time = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - navigation ou changement de contexte en cours
                pass
            if time.monotonic() - self._last_state_save > SESSION_SAVE_INTERVAL:
                await self._persist_session()  # le site renouvelle ses cookies : on garde les plus récents
            await asyncio.sleep(self._settings.screenshot_interval)

    # ------------------------------------------------------------- navigateur
    def start_in_background(self) -> None:
        """Démarre le navigateur sans bloquer l'appelant (ouverture d'un écran de l'interface)."""
        if self._start_task is None or self._start_task.done():
            self._start_task = asyncio.create_task(self.ensure_started())

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._page is not None and not self._page.is_closed():
                return
            # Le démarrage du navigateur est protégé : si l'appelant est annulé (arrêt de la connexion automatique,
            # fermeture de l'application), il va quand même au bout ou se nettoie, au lieu de laisser un Chromium à moitié lancé.
            self._boot = asyncio.ensure_future(self._boot_browser())
            await asyncio.shield(self._boot)

    async def _boot_browser(self) -> None:
        try:
            await self._teardown()
            self.log("Démarrage du navigateur du bot…")
            await self._start_browser()
            await self._open_home()
            self._capture_task = asyncio.create_task(self._capture_loop())
            if self._session_restored and self.has_credentials():
                # Session rechargée + identifiants enregistrés : la supervision constate que le site est déjà
                # connecté, reprend l'univers et lance le scan des planètes, sans qu'il faille relancer la connexion.
                self.supervisor.ensure_keeper()
        except Exception as exc:  # noqa: BLE001
            self.log(f"Impossible de démarrer le navigateur ({type(exc).__name__}).", "error", kind="attention")
            await self._teardown()

    async def _start_browser(self) -> None:
        s = self._settings
        self._pw = await async_playwright().start()
        args = ["--no-sandbox"] if s.no_sandbox else []
        if self.display is not None:
            # Vraie fenêtre (barre d'adresse, onglets) sur l'écran virtuel, à sa taille exacte
            await self.display.ensure()
            args += ["--window-position=0,0", f"--window-size={self.display.width},{self.display.height}",
                     "--no-first-run", "--no-default-browser-check", "--lang=fr-FR"]
            self._browser = await self._pw.chromium.launch(
                headless=False, executable_path=s.chromium_path, args=args,
                # langue de l'interface de Chromium = celle du jeu : pas de proposition de traduction
                env={**os.environ, "DISPLAY": self.display.name, "LANGUAGE": "fr", "LANG": "fr_FR.UTF-8"},
            )
        else:
            self._browser = await self._pw.chromium.launch(headless=s.headless, executable_path=s.chromium_path, args=args)
        await self._new_page(restore=True)

    async def _new_page(self, *, restore: bool = False) -> None:
        """Nouveau contexte = session vierge (sans cookies) avec une page vide.

        `restore=True` (démarrage du navigateur seulement) repart des cookies enregistrés lors de la dernière
        connexion, pour rester connecté après un redémarrage du conteneur."""
        assert self._browser is not None
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
        if self.display is not None:  # la page suit la taille de la fenêtre, comme dans un navigateur normal
            options: dict = {"no_viewport": True, "locale": "fr-FR"}
        else:
            options = {"viewport": dict(self._viewport), "locale": "fr-FR"}
        saved = self._session_store.load() if restore else None
        if restore:
            self._session_restored = False
        try:
            self._context = await self._browser.new_context(**options, **({"storage_state": saved} if saved else {}))
            if saved:
                self._session_restored = True
                self.log("Session précédente rechargée : le site devrait rester connecté.", event=True)
        except Exception as exc:  # noqa: BLE001 - état enregistré refusé : on repart d'une session vierge
            if not saved:
                raise
            self.log(f"Session précédente illisible ({type(exc).__name__}) : session vierge.", "warn")
            self._session_store.clear()
            self._context = await self._browser.new_context(**options)
        if self._settings.show_cursor and self.display is None:
            await self._context.add_init_script(CURSOR_SCRIPT)
        self._page = await self._context.new_page()
        # Fenêtres du jeu qui masquent la page (ex. « Journal de développement ») : fermées dès qu'elles gênent une action
        await interruptions.install(
            self._page, lambda name: self.log(f"Fenêtre « {name} » fermée automatiquement.", event=True)
        )
        self._context.on("response", self._on_response)
        self._page.on("websocket", self._on_websocket)
        self._page.on("framenavigated", self._on_navigated)
        if self.display is None:
            await self._attach_screencast()
        elif self._window_size:
            await self._fit_window(*self._window_size)

    async def _open_home(self) -> None:
        page = self._page
        assert page is not None
        await page.goto(self._settings.login_url, wait_until="domcontentloaded", timeout=45_000)
        self.log("Page d'accueil chargée.")
        await human.move_mouse_randomly(page)
        await human.think(self._settings)
        await self._refuse_cookies(page)

    async def _persist_session(self) -> None:
        """Enregistre les cookies du navigateur (uniquement connecté, pour ne jamais écraser une bonne session)."""
        if self._state is not State.LOGGED_IN or self._context is None:
            return
        try:
            saved = self._session_store.save(await self._context.storage_state())
        except Exception as exc:  # noqa: BLE001 - contexte en cours de fermeture
            self.log(f"Session non enregistrée ({type(exc).__name__}).", "warn")
            return
        if saved:
            self._last_state_save = time.monotonic()

    async def _reset_context(self) -> None:
        await self._new_page()
        await self._open_home()
        self._dirty = False

    # ------------------------------------------------------------- connexion
    # ----------------------------------------------------- identifiants (mémoire)
    def has_credentials(self) -> bool:
        return self._creds is not None

    def credentials_hint(self) -> str:
        return _mask(self._creds[0]) if self._creds else ""

    def credentials_identifier(self) -> str:
        """Identifiant (adresse e-mail) mémorisé ou enregistré, pour pré-remplir la page. Jamais le mot de passe."""
        return self._creds[0] if self._creds else self.vault.saved_identifier()

    def credentials_password_known(self) -> bool:
        """Un mot de passe est disponible (en mémoire ou enregistré) : le champ peut rester vide."""
        return self._creds is not None

    def set_credentials(self, identifier: str, password: str | None = None) -> bool:
        """Mémorise l'identifiant. Sans nouveau mot de passe, garde celui déjà connu. Retourne False si aucun n'existe."""
        if password is None:
            if self._creds is None:
                return False
            password = self._creds[1]
        self._creds = (identifier, password)
        if self.vault.save(identifier, password):
            self.log(f"Identifiants enregistrés pour la reconnexion automatique ({_mask(identifier)}) : mot de passe chiffré sur le disque du bot.", event=True)
        else:
            self.log(f"Identifiants mémorisés pour la reconnexion automatique ({_mask(identifier)}) : en mémoire seulement (pas de dossier /data).", event=True)
        return True

    def restore_credentials(self) -> bool:
        """Recharge en mémoire les identifiants enregistrés (démarrage, ou après une déconnexion)."""
        saved = self.vault.load()
        if saved:
            self._creds = saved
        return saved is not None

    def forget_credentials(self) -> None:
        if self._creds or self.vault.saved_identifier():
            self.log("Identifiants oubliés (y compris ceux enregistrés).", event=True)
        self._creds = None
        self.vault.clear()

    # ----------------------------------------------------- écran courant du site
    async def detect_screen(self) -> str:
        """« game » (dans une partie), « universes » (choix de l'univers), « login » (formulaire visible) ou « unknown »."""
        page = self._page
        if page is None or page.is_closed():
            return "unknown"
        path = urlsplit(page.url).path.rstrip("/")
        screen = "unknown"
        if "/game/" in path + "/":
            screen = "game"
        elif path == "/servers" or path.endswith("/servers"):
            screen = "universes"
        else:
            try:
                s = self._settings
                if await page.locator(s.email_selector).first.is_visible() and await page.locator(s.password_selector).first.is_visible():
                    screen = "login"
            except Exception:  # noqa: BLE001 - page en cours de chargement
                pass
        if screen in ("game", "universes") and self._state is not State.LOGGED_IN:
            self._set(State.LOGGED_IN, "Session détectée : déjà connecté.")
            self.log("Session détectée : le site est déjà connecté, la connexion est sautée.", event=True)
        elif screen == "login" and self._state is State.LOGGED_IN:
            self._set(State.IDLE, "Session expirée.")
            self.log("Session expirée : la page de connexion s'affiche à nouveau.", "warn", event=True)
        return screen

    # ------------------------------------------------------------- connexion
    async def login(self, identifier: str, password: str, *, supervised: bool = False) -> dict[str, str]:
        if self._lock.locked():
            return {**self.status(), "message": "Une connexion est déjà en cours."}
        if self._manual:
            return {**self.status(), "message": "Désactivez le contrôle manuel pour lancer la connexion automatique."}
        async with self._lock:
            self._set(State.LOGGING_IN, "Connexion en cours…")
            self.log(f"Connexion demandée pour {_mask(identifier)}{' (automatique)' if supervised else ''}.", event=True)
            try:
                await self.ensure_started()
                if self._page is None or self._page.is_closed():
                    self._set(State.ERROR, "Navigateur indisponible : voir le journal.")
                    self.last_login_kind = "technical"
                    return self.status()
                if self._dirty:  # nouvelle tentative : session vierge
                    await self._reset_context()
                self._dirty = True
                outcome = await self._do_login(identifier, password)
            except Exception as exc:  # noqa: BLE001 - on ne journalise que le type, jamais le contenu
                self.log(f"Erreur technique pendant la connexion ({type(exc).__name__}).", "error", event=True)
                self._set(State.ERROR, "Erreur technique pendant la connexion.")
                self.last_login_kind = "technical"
                return self.status()
            self.last_login_kind = outcome.kind
            if outcome.success:
                self._creds = (identifier, password)  # en mémoire, pour se reconnecter si la session expire
                self._set(State.LOGGED_IN, outcome.message)
                self.log(outcome.message, event=True)
                await self._persist_session()
            else:
                # Le navigateur reste sur la page pour que la vue en direct montre la cause
                # (ex. Turnstile). La prochaine tentative repart d'une session vierge.
                self.log(outcome.message, "error", event=True)
                self._set(State.ERROR, outcome.message)
            return self.status()

    async def logout(self) -> dict[str, str]:
        self._creds = None  # se déconnecter : plus de reconnexion automatique (les identifiants enregistrés restent sur disque)
        self._session_store.clear()  # les cookies enregistrés partent avec la session
        await self.supervisor.stop_keeper()
        async with self._lock:
            if self._page is not None:
                try:
                    await self._reset_context()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Erreur pendant la déconnexion ({type(exc).__name__}).", "error")
            self._dirty = False
            self._set(State.IDLE, "Non connecté.")
            self.log("Déconnexion : session du navigateur effacée.", event=True)
            return self.status()

    def _login_delay(self) -> tuple[float, float]:
        d = self.supervisor.prefs.get()["login_delay"]
        return d["min_seconds"], d["max_seconds"]

    async def _do_login(self, identifier: str, password: str) -> _Outcome:
        s = self._settings
        page = self._page
        assert page is not None

        email = page.locator(s.email_selector).first
        pwd = page.locator(s.password_selector).first
        submit = page.locator(s.submit_selector).first
        try:
            await email.wait_for(state="visible", timeout=15_000)
        except Exception:  # noqa: BLE001
            return _Outcome(False, "Formulaire de connexion introuvable (sélecteurs à vérifier).", "technical")

        delay = self._login_delay()  # délai min/max entre deux clics, choisi dans Réglages › Compte et univers
        await human.think(s, delay)
        self.log("Saisie de l'identifiant.")
        await human.click(page, email, s)
        await human.micro(s)
        await human.type_text(page, identifier, s)

        await human.think(s, delay)
        self.log("Saisie du mot de passe.")
        await human.click(page, pwd, s)
        await human.micro(s)
        await human.type_text(page, password, s)

        await human.think(s, delay)
        self.log("Clic sur le bouton de connexion.")
        await human.click(page, submit, s)
        self.log("Formulaire envoyé, attente du résultat.")
        return await self._wait_outcome(page)

    async def _refuse_cookies(self, page: Page) -> None:
        """Choix par défaut : refuser les traceurs optionnels (bandeau de consentement)."""
        button = page.get_by_role("button", name=COOKIE_REFUSE_NAME, exact=False).first
        try:
            await button.wait_for(state="visible", timeout=4_000)
            await human.click(page, button, self._settings)
            self.log("Bandeau cookies : « Tout refuser ».")
        except Exception:  # noqa: BLE001 - pas de bandeau : rien à faire
            self.log("Pas de bandeau cookies détecté.")

    async def _wait_outcome(self, page: Page) -> _Outcome:
        """Succès = le champ mot de passe disparaît. Échec = message `role=alert` ou délai dépassé."""
        s = self._settings
        deadline = time.monotonic() + s.login_timeout
        turnstile_seen = False
        while time.monotonic() < deadline:
            if page.is_closed():
                return _Outcome(False, "La page du navigateur a été fermée.", "technical")
            try:
                if not turnstile_seen and await page.locator(TURNSTILE_SELECTOR).count():
                    turnstile_seen = True
                    self.log("Contrôle anti-bot Cloudflare Turnstile détecté.", "warn")
                alert = page.locator('[role="alert"]:visible')
                if await alert.count():
                    text = " ".join((await alert.first.inner_text()).split())
                    if text:
                        return _Outcome(False, f"Connexion refusée par le site : {text}", "refused")
                if not await page.locator(s.password_selector).first.is_visible():
                    return _Outcome(True, "Connecté : le formulaire de connexion a disparu.")
            except Exception:  # noqa: BLE001 - navigation en cours : on réessaie
                pass
            await asyncio.sleep(0.4)
        await self._log_form_text(page)
        hint = " (Turnstile détecté : il bloque probablement le bot)" if turnstile_seen else ""
        return _Outcome(False, f"Aucun résultat de connexion avant {s.login_timeout:.0f} s{hint}.", "timeout")

    async def _log_form_text(self, page: Page) -> None:
        """Diagnostic : texte visible autour du formulaire (n'inclut pas les valeurs saisies)."""
        try:
            area = page.locator(self._settings.password_selector).first.locator("xpath=ancestor::form[1]/..")
            text = " ".join((await area.inner_text(timeout=2_000)).split())[:300]
            self.log(f"Texte visible autour du formulaire : {text}", "warn")
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------- contrôle manuel (clics, clavier)
    async def handle_input(self, msg: dict) -> None:
        """Applique un message de l'interface : contrôle, taille d'affichage, souris ou clavier.

        Les frappes ne sont jamais écrites dans le journal (elles peuvent contenir un mot de passe).
        """
        kind = msg.get("t")
        try:
            if kind == "control":
                await self._set_manual(bool(msg.get("enabled")))
            elif kind == "viewport":
                await self._set_viewport(_num(msg["width"], 1, 10_000), _num(msg["height"], 1, 10_000))
            elif kind in {"move", "down", "up", "wheel", "type", "press"}:
                if self._manual:
                    self._enqueue_input(kind, msg)
        except (KeyError, TypeError, ValueError):
            return  # message mal formé : ignoré
        except Exception:  # noqa: BLE001 - page en cours de navigation, etc.
            return

    async def _set_manual(self, enabled: bool) -> None:
        if enabled == self._manual:
            return
        if enabled and self._lock.locked():
            self.log("Contrôle manuel refusé : une connexion automatique est en cours.", "warn", event=True)
            return
        self._manual = enabled
        self.log("Contrôle manuel activé : le bot n'agit plus." if enabled else "Contrôle manuel désactivé.", event=True)

    async def _set_viewport(self, width: float, height: float) -> None:
        """Adapte la hauteur du navigateur au format de l'écran d'affichage (largeur fixe).

        La taille est mémorisée même si le navigateur n'est pas encore démarré ou si une connexion
        est en cours : elle s'appliquera au prochain contexte.
        """
        new = {
            "width": VIEWPORT_WIDTH,
            "height": int(_num(round(VIEWPORT_WIDTH * height / width), VIEWPORT_MIN_HEIGHT, VIEWPORT_MAX_HEIGHT)),
        }
        if self.display is not None:
            await self.display.resize(int(width), int(height))
            await self._fit_window(self.display.width, self.display.height)
            return
        if abs(new["height"] - self._viewport["height"]) <= 4:
            return
        self._viewport = new
        page = self._page
        if page is not None and not page.is_closed() and not self._lock.locked():
            await page.set_viewport_size(dict(new))

    async def _fit_window(self, width: int, height: int) -> None:
        """Navigateur complet : l'écran virtuel prend la taille de la zone d'affichage (noVNC le demande) ; la fenêtre
        Chromium la suit pour remplir tout l'écran, sans bandes vides."""
        self._window_size = (max(640, min(width, 3840)), max(400, min(height, 2160)))
        page = self._page
        if page is None or page.is_closed() or self._context is None:
            return
        try:
            cdp = await self._context.new_cdp_session(page)
            window = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
            await cdp.send("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {
                "left": 0, "top": 0, "width": self._window_size[0], "height": self._window_size[1]}})
            await cdp.detach()
        except Exception:  # noqa: BLE001 - fenêtre en cours de fermeture ou de création
            pass

    def _enqueue_input(self, kind: str, msg: dict) -> None:
        if len(self._input_queue) > 500:  # garde-fou : un client qui inonde le serveur
            return
        self.input_stats["received"] += 1
        self._input_queue.append((kind, msg))
        self._input_wakeup.set()
        if self._input_task is None or self._input_task.done():
            self._input_task = asyncio.create_task(self._input_worker())

    async def _input_worker(self) -> None:
        """Applique les entrées dans l'ordre. Un mouvement de souris suivi d'une autre entrée est inutile (chaque
        clic et chaque molette portent leur position) : il est sauté. Ainsi un clic part tout de suite, même si
        des dizaines de mouvements sont arrivés juste avant pendant que la page du jeu était occupée."""
        while True:
            if not self._input_queue:
                self._input_wakeup.clear()
                try:
                    await asyncio.wait_for(self._input_wakeup.wait(), 30)
                except asyncio.TimeoutError:
                    return
                continue
            kind, msg = self._input_queue.popleft()
            if kind == "move" and self._input_queue:
                self.input_stats["merged"] += 1
                continue
            if not await self._wait_input_allowed():
                self._input_queue.clear()
                continue
            try:
                await self._manual_input(kind, msg)
                self.input_stats["applied"] += 1
            except (KeyError, TypeError, ValueError):
                pass
            except Exception:  # noqa: BLE001 - page en cours de navigation, etc.
                pass

    async def _wait_input_allowed(self) -> bool:
        """Le bot finit l'action en cours avant de rendre la main (au plus 10 s) : les clics attendent au lieu d'être perdus."""
        end = time.monotonic() + 10
        warned = False
        while self._manual and self._lock.locked():
            if not warned:
                warned = True
                self.log("Contrôle manuel : le bot termine son action en cours, vos clics partiront juste après.", "warn")
            if time.monotonic() > end:
                self.log("Contrôle manuel : le bot est resté occupé, clics ignorés.", "warn")
                return False
            await asyncio.sleep(0.1)
        return self._manual

    async def _dispatch_mouse(self, page, event_type: str, x: float, y: float, **extra) -> None:
        """Un seul aller-retour vers Chrome par évènement (CDP Input.dispatchMouseEvent), position comprise."""
        if self._cdp is not None:
            await self._cdp.send("Input.dispatchMouseEvent", {"type": event_type, "x": x, "y": y, **extra})
            return
        if event_type == "mouseMoved":  # sans session CDP (rare) : Playwright
            await page.mouse.move(x, y)
        elif event_type == "mousePressed":
            await page.mouse.move(x, y)
            await page.mouse.down(button=extra["button"])
        elif event_type == "mouseReleased":
            await page.mouse.move(x, y)
            await page.mouse.up(button=extra["button"])
        elif event_type == "mouseWheel":
            await page.mouse.move(x, y)
            await page.mouse.wheel(extra["deltaX"], extra["deltaY"])

    async def _manual_input(self, kind: str, msg: dict) -> None:
        page = self._page
        if not self._manual or page is None or page.is_closed():
            return
        size = page.viewport_size or self._viewport
        if kind in {"move", "down", "up", "wheel"}:
            x = _num(msg["x"], 0, 1) * size["width"]
            y = _num(msg["y"], 0, 1) * size["height"]
            if kind == "move":
                await self._dispatch_mouse(page, "mouseMoved", x, y, buttons=self._mouse_buttons)
            elif kind in {"down", "up"}:
                button = msg.get("button", "left")
                if button not in _MOUSE_BUTTONS:
                    return
                bit = {"left": 1, "right": 2, "middle": 4}[button]
                if self._mouse_at != (x, y):  # survol d'abord (menus qui s'ouvrent au survol), puis l'appui
                    await self._dispatch_mouse(page, "mouseMoved", x, y, buttons=self._mouse_buttons)
                if kind == "down":
                    self.log(f"Clic manuel en ({x:.0f}, {y:.0f}).")
                    self._mouse_buttons |= bit
                    await self._dispatch_mouse(page, "mousePressed", x, y, button=button, buttons=self._mouse_buttons, clickCount=1)
                else:
                    self._mouse_buttons &= ~bit
                    await self._dispatch_mouse(page, "mouseReleased", x, y, button=button, buttons=self._mouse_buttons, clickCount=1)
            else:
                await self._dispatch_mouse(page, "mouseWheel", x, y, deltaX=_num(msg.get("dx", 0), -2000, 2000),
                                           deltaY=_num(msg.get("dy", 0), -2000, 2000))
            self._mouse_at = (x, y)
        elif kind == "type":
            text = str(msg["text"])[:500]
            if not text:
                return
            if len(text) == 1:
                await page.keyboard.type(text)
            else:
                await page.keyboard.insert_text(text)
            now = time.monotonic()
            if now - self._last_typing_log > 3:  # un seul message à la fois, sans le contenu
                self._last_typing_log = now
                self.log("Saisie manuelle au clavier.")
        elif kind == "press":
            key = str(msg["key"])
            if _KEY_RE.match(key):
                await page.keyboard.press(key)

    # ------------------------------------------------ exploration de la structure
    async def _on_response(self, response) -> None:
        rec = self._recorder
        if not rec.enabled:
            return
        try:
            request = response.request
            if request.resource_type not in {"xhr", "fetch", "document"}:
                return
            if should_skip_host(urlsplit(response.url).hostname):
                return
            content_type = response.headers.get("content-type", "")
            body = None
            if "json" in content_type:
                raw = await response.body()
                body = raw.decode("utf-8", errors="ignore") if len(raw) <= 300_000 else None
            try:
                request_body = request.post_data
            except Exception:  # noqa: BLE001 - corps binaire
                request_body = None
            header_names = list((await request.all_headers()).keys())
            rec.add_request(
                request.method, response.url, response.status, request.resource_type,
                header_names, request_body, content_type, body,
            )
        except Exception:  # noqa: BLE001 - redirection, page fermée, etc.
            return

    def _on_websocket(self, ws) -> None:
        rec = self._recorder
        if not rec.enabled or should_skip_host(urlsplit(ws.url).hostname):
            return
        ws.on("framesent", lambda payload: rec.enabled and rec.add_ws_frame(ws.url, "sent", payload))
        ws.on("framereceived", lambda payload: rec.enabled and rec.add_ws_frame(ws.url, "received", payload))

    def _on_navigated(self, frame) -> None:
        """Prend un cliché de la page 1,5 s après chaque navigation, quand l'enregistrement est actif."""
        page = self._page
        if not self._recorder.enabled or page is None or frame != page.main_frame:
            return
        if self._snapshot_task is not None and not self._snapshot_task.done():
            self._snapshot_task.cancel()

        async def later() -> None:
            await asyncio.sleep(1.5)
            await self.snapshot_now()

        self._snapshot_task = asyncio.create_task(later())

    def set_recording(self, enabled: bool) -> None:
        if enabled == self._recorder.enabled:
            return
        self._recorder.enabled = enabled
        self.log(
            "Enregistrement de la structure du site activé : navigation et échanges observés (valeurs masquées)."
            if enabled else "Enregistrement de la structure du site désactivé."
        )

    def clear_recording(self) -> None:
        self._recorder.clear()
        self.log("Enregistrement effacé.")

    async def snapshot_now(self) -> bool:
        page = self._page
        if page is None or page.is_closed():
            return False
        try:
            data = await page.evaluate(SNAPSHOT_JS)
            if not self._recorder.add_snapshot(page.url, data):
                self.log(f"Limite de {MAX_SNAPSHOTS} pages capturées atteinte : exportez puis effacez pour continuer.", "warn", event=True)
                return False
            self.log(f"Page capturée : {self._page_label()} ({len(data['elements'])} éléments).")
            return True
        except Exception:  # noqa: BLE001
            return False

    async def export_structure(self) -> dict:
        cookies, storage = [], {}
        if self._context is not None and self._page is not None and not self._page.is_closed():
            try:
                cookies = await self._context.cookies()  # on n'en garde que les noms (voir Recorder.export)
                storage = await self._page.evaluate(
                    "() => ({ localStorage: Object.keys(localStorage), sessionStorage: Object.keys(sessionStorage) })"
                )
            except Exception:  # noqa: BLE001
                pass
        return self._recorder.export(cookies, storage)

    # ------------------------------------------------------------ choix de l'univers
    async def _universe_buttons(self):
        page = self._page
        if page is None or page.is_closed() or self._state is not State.LOGGED_IN:
            return None
        return page.locator("button, a").filter(has_text=UNIVERSE_BUTTON_RE)

    async def list_universes(self) -> list[dict]:
        """Univers proposés par la page de choix. Structure supposée d'après une capture : à confirmer par l'export."""
        buttons = await self._universe_buttons()
        if buttons is None:
            return []
        found = []
        try:
            page_title = ""
            heading = self._page.locator("h1").first
            if await heading.count():
                page_title = " ".join((await heading.inner_text(timeout=1_000)).split())
            for i in range(await buttons.count()):
                button = buttons.nth(i)
                if not await button.is_visible():
                    continue
                card = button.locator("xpath=ancestor::*[.//h1 or .//h2 or .//h3 or .//h4][1]")
                name = ""
                if await card.count():
                    title = card.locator("h1,h2,h3,h4").first
                    name = " ".join((await title.inner_text(timeout=1_000)).split())
                if not name or name == page_title:
                    name = f"Univers {len(found) + 1}"
                found.append({"index": i, "name": name[:40]})
        except Exception:  # noqa: BLE001 - page en cours de chargement
            return []
        return found

    async def enter_universe(self, index: int) -> dict[str, str]:
        if self._manual:
            return {**self.status(), "message": "Désactivez le contrôle manuel pour choisir un univers."}
        if self._lock.locked():
            return {**self.status(), "message": "Une opération est déjà en cours."}
        async with self._lock:
            buttons = await self._universe_buttons()
            if buttons is None or not 0 <= index < await buttons.count():
                self.log("Choix d'univers impossible : bouton introuvable.", "error")
                return self.status()
            names = {u["index"]: u["name"] for u in await self.list_universes()}
            self.log(f"Choix de l'univers : {names.get(index, index)}.", event=True)
            if index in names:
                self.supervisor.prefs.set_universe(names[index])  # mémorisé : le bot y retournera tout seul
            try:
                await human.think(self._settings)
                await human.click(self._page, buttons.nth(index), self._settings)
                await self._page.wait_for_load_state("domcontentloaded", timeout=15_000)
                self.log(f"Page affichée : {self._page_label()}.")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Erreur pendant le choix de l'univers ({type(exc).__name__}).", "error")
            return self.status()

    # --------------------------------------------------------------- nettoyage
    async def _teardown(self) -> None:
        if self._capture_task is not None:
            self._capture_task.cancel()
            self._capture_task = None
        for closer in (
            self._context.close if self._context else None,
            self._browser.close if self._browser else None,
            self._pw.stop if self._pw else None,
        ):
            if closer is not None:
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass
        self._page = self._context = self._browser = self._pw = None

    async def shutdown(self) -> None:
        try:
            await self._shutdown()
        finally:
            if self.display is not None:
                await self.display.stop()

    async def _shutdown(self) -> None:
        await self.scheduler.shutdown()
        await self.spy.shutdown()
        await self.supervisor.stop_keeper()
        boot = self._boot
        if boot is not None and not boot.done():  # un démarrage de navigateur est en cours : on l'attend avant de tout fermer
            await asyncio.gather(boot, return_exceptions=True)
        async with self._start_lock, self._lock:
            await self._persist_session()  # dernière version des cookies avant de fermer le navigateur
            await self._teardown()
