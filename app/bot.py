"""Session du bot : navigateur Playwright toujours actif, connexion, journal et flux d'images.

Le navigateur démarre dès qu'un écran de l'interface est ouvert et affiche la page d'accueil du site,
même sans connexion. La page est capturée en continu pour l'affichage en direct.

Règles sur les identifiants du joueur :
- ils arrivent par `login()` depuis l'interface web, jamais depuis l'environnement ;
- ils ne sont ni stockés, ni journalisés, ni renvoyés par l'API ;
- seule la session du navigateur (cookies) reste en mémoire après la connexion.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from . import human
from .config import Settings
from .explore import SNAPSHOT_JS, Recorder, should_skip_host

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


def _mask(identifier: str) -> str:
    """Version masquée de l'identifiant, pour le journal (jamais la valeur complète)."""
    return identifier[:2] + "***" if identifier else "***"


class BotSession:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()  # connexion / déconnexion
        self._start_lock = asyncio.Lock()  # démarrage du navigateur
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._state = State.IDLE
        self._message = "Non connecté."
        self._logs: deque[dict[str, str]] = deque(maxlen=200)
        self._dirty = False  # une tentative de connexion a déjà eu lieu dans ce contexte
        self._manual = False  # contrôle manuel (clics/clavier de l'utilisateur) activé
        self._viewport = {"width": VIEWPORT_WIDTH, "height": 900}
        self._last_typing_log = 0.0
        # Flux d'images
        self._capture_task: asyncio.Task | None = None
        self._start_task: asyncio.Task | None = None
        self._viewers = 0
        self._frame_id = 0
        self._frame_jpeg: bytes | None = None
        self._last_frame_time = 0.0
        self._frame_event = asyncio.Event()
        self._cdp = None  # session CDP de la page courante (flux d'images poussé par Chrome)
        self._screencasting = False
        self._tasks: set[asyncio.Task] = set()
        # Exploration de la structure du site (désactivée par défaut)
        self._recorder = Recorder()
        self._snapshot_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ état
    def log(self, message: str, level: str = "info") -> None:
        self._logs.append(
            {"ts": datetime.now(timezone.utc).strftime("%H:%M:%S"), "level": level, "msg": message}
        )

    def logs(self, limit: int = 50) -> list[dict[str, str]]:
        return list(self._logs)[-limit:]

    def status(self) -> dict[str, str]:
        return {
            "state": self._state.value,
            "message": self._message,
            "page": self._page_label(),
            "manual": self._manual,
            "explore": {"recording": self._recorder.enabled, **self._recorder.counts()},
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
                self.log("Contrôle manuel désactivé (plus aucun écran ouvert).")

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
                if self._viewers > 0 and page is not None and not page.is_closed() and idle > 1.0:
                    data = await page.screenshot(type="jpeg", quality=60, timeout=5_000)
                    if data != self._frame_jpeg:
                        self._publish_frame(data)
                    else:
                        self._last_frame_time = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - navigation ou changement de contexte en cours
                pass
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
            try:
                await self._teardown()
                self.log("Démarrage du navigateur du bot…")
                await self._start_browser()
                await self._open_home()
                self._capture_task = asyncio.create_task(self._capture_loop())
            except Exception as exc:  # noqa: BLE001
                self.log(f"Impossible de démarrer le navigateur ({type(exc).__name__}).", "error")
                await self._teardown()

    async def _start_browser(self) -> None:
        s = self._settings
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=s.headless,
            executable_path=s.chromium_path,
            args=["--no-sandbox"] if s.no_sandbox else [],
        )
        await self._new_page()

    async def _new_page(self) -> None:
        """Nouveau contexte = session vierge (sans cookies) avec une page vide."""
        assert self._browser is not None
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
        self._context = await self._browser.new_context(
            viewport=dict(self._viewport), locale="fr-FR"
        )
        if self._settings.show_cursor:
            await self._context.add_init_script(CURSOR_SCRIPT)
        self._page = await self._context.new_page()
        self._context.on("response", self._on_response)
        self._page.on("websocket", self._on_websocket)
        self._page.on("framenavigated", self._on_navigated)
        await self._attach_screencast()

    async def _open_home(self) -> None:
        page = self._page
        assert page is not None
        await page.goto(self._settings.login_url, wait_until="domcontentloaded", timeout=45_000)
        self.log("Page d'accueil chargée.")
        await human.move_mouse_randomly(page)
        await human.think(self._settings)
        await self._refuse_cookies(page)

    async def _reset_context(self) -> None:
        await self._new_page()
        await self._open_home()
        self._dirty = False

    # ------------------------------------------------------------- connexion
    async def login(self, identifier: str, password: str) -> dict[str, str]:
        if self._lock.locked():
            return {**self.status(), "message": "Une connexion est déjà en cours."}
        if self._manual:
            return {**self.status(), "message": "Désactivez le contrôle manuel pour lancer la connexion automatique."}
        async with self._lock:
            self._set(State.LOGGING_IN, "Connexion en cours…")
            self.log(f"Connexion demandée pour {_mask(identifier)}.")
            try:
                await self.ensure_started()
                if self._page is None or self._page.is_closed():
                    self._set(State.ERROR, "Navigateur indisponible : voir le journal.")
                    return self.status()
                if self._dirty:  # nouvelle tentative : session vierge
                    await self._reset_context()
                self._dirty = True
                outcome = await self._do_login(identifier, password)
            except Exception as exc:  # noqa: BLE001 - on ne journalise que le type, jamais le contenu
                self.log(f"Erreur technique pendant la connexion ({type(exc).__name__}).", "error")
                self._set(State.ERROR, "Erreur technique pendant la connexion.")
                return self.status()
            if outcome.success:
                self._set(State.LOGGED_IN, outcome.message)
                self.log(outcome.message)
            else:
                # Le navigateur reste sur la page pour que la vue en direct montre la cause
                # (ex. Turnstile). La prochaine tentative repart d'une session vierge.
                self.log(outcome.message, "error")
                self._set(State.ERROR, outcome.message)
            return self.status()

    async def logout(self) -> dict[str, str]:
        async with self._lock:
            if self._page is not None:
                try:
                    await self._reset_context()
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Erreur pendant la déconnexion ({type(exc).__name__}).", "error")
            self._dirty = False
            self._set(State.IDLE, "Non connecté.")
            self.log("Déconnexion : session du navigateur effacée.")
            return self.status()

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
            return _Outcome(False, "Formulaire de connexion introuvable (sélecteurs à vérifier).")

        await human.think(s)
        self.log("Saisie de l'identifiant.")
        await human.click(page, email, s)
        await human.micro(s)
        await human.type_text(page, identifier, s)

        await human.think(s)
        self.log("Saisie du mot de passe.")
        await human.click(page, pwd, s)
        await human.micro(s)
        await human.type_text(page, password, s)

        await human.think(s)
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
                return _Outcome(False, "La page du navigateur a été fermée.")
            try:
                if not turnstile_seen and await page.locator(TURNSTILE_SELECTOR).count():
                    turnstile_seen = True
                    self.log("Contrôle anti-bot Cloudflare Turnstile détecté.", "warn")
                alert = page.locator('[role="alert"]:visible')
                if await alert.count():
                    text = " ".join((await alert.first.inner_text()).split())
                    if text:
                        return _Outcome(False, f"Connexion refusée par le site : {text}")
                if not await page.locator(s.password_selector).first.is_visible():
                    return _Outcome(True, "Connecté : le formulaire de connexion a disparu.")
            except Exception:  # noqa: BLE001 - navigation en cours : on réessaie
                pass
            await asyncio.sleep(0.4)
        await self._log_form_text(page)
        hint = " (Turnstile détecté : il bloque probablement le bot)" if turnstile_seen else ""
        return _Outcome(False, f"Aucun résultat de connexion avant {s.login_timeout:.0f} s{hint}.")

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
                await self._manual_input(kind, msg)
        except (KeyError, TypeError, ValueError):
            return  # message mal formé : ignoré
        except Exception:  # noqa: BLE001 - page en cours de navigation, etc.
            return

    async def _set_manual(self, enabled: bool) -> None:
        if enabled == self._manual:
            return
        if enabled and self._lock.locked():
            self.log("Contrôle manuel refusé : une connexion automatique est en cours.", "warn")
            return
        self._manual = enabled
        self.log("Contrôle manuel activé : le bot n'agit plus." if enabled else "Contrôle manuel désactivé.")

    async def _set_viewport(self, width: float, height: float) -> None:
        """Adapte la hauteur du navigateur au format de l'écran d'affichage (largeur fixe).

        La taille est mémorisée même si le navigateur n'est pas encore démarré ou si une connexion
        est en cours : elle s'appliquera au prochain contexte.
        """
        new = {
            "width": VIEWPORT_WIDTH,
            "height": int(_num(round(VIEWPORT_WIDTH * height / width), VIEWPORT_MIN_HEIGHT, VIEWPORT_MAX_HEIGHT)),
        }
        if abs(new["height"] - self._viewport["height"]) <= 4:
            return
        self._viewport = new
        page = self._page
        if page is not None and not page.is_closed() and not self._lock.locked():
            await page.set_viewport_size(dict(new))

    async def _manual_input(self, kind: str, msg: dict) -> None:
        page = self._page
        if not self._manual or self._lock.locked() or page is None or page.is_closed():
            return
        size = page.viewport_size or self._viewport
        if kind in {"move", "down", "up", "wheel"}:
            x = _num(msg["x"], 0, 1) * size["width"]
            y = _num(msg["y"], 0, 1) * size["height"]
            await page.mouse.move(x, y)
            if kind in {"down", "up"}:
                button = msg.get("button", "left")
                if button not in _MOUSE_BUTTONS:
                    return
                if kind == "down":
                    self.log(f"Clic manuel en ({x:.0f}, {y:.0f}).")
                    await page.mouse.down(button=button)
                else:
                    await page.mouse.up(button=button)
            elif kind == "wheel":
                await page.mouse.wheel(_num(msg.get("dx", 0), -2000, 2000), _num(msg.get("dy", 0), -2000, 2000))
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
            self._recorder.add_snapshot(page.url, data)
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
            self.log(f"Choix de l'univers : {names.get(index, index)}.")
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
        async with self._start_lock, self._lock:
            await self._teardown()
