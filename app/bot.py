"""Session du bot : navigateur Playwright, connexion, journal et captures.

Règles sur les identifiants du joueur :
- ils arrivent par `login()` depuis l'interface web, jamais depuis l'environnement ;
- ils ne sont ni stockés, ni journalisés, ni renvoyés par l'API ;
- seule la session du navigateur (cookies) reste en mémoire après la connexion.
"""

from __future__ import annotations

import asyncio
import base64
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .config import Settings

TURNSTILE_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'
COOKIE_REFUSE_NAME = "tout refuser"


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
    """Version masquee de l'identifiant, pour le journal (jamais la valeur complete)."""
    return identifier[:2] + "***" if identifier else "***"


class BotSession:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._state = State.IDLE
        self._message = "Non connecté."
        self._logs: deque[dict[str, str]] = deque(maxlen=200)

    # ------------------------------------------------------------------ état
    def log(self, message: str, level: str = "info") -> None:
        self._logs.append(
            {"ts": datetime.now(timezone.utc).strftime("%H:%M:%S"), "level": level, "msg": message}
        )

    def logs(self, limit: int = 50) -> list[dict[str, str]]:
        return list(self._logs)[-limit:]

    def status(self) -> dict[str, str]:
        return {"state": self._state.value, "message": self._message, "page": self._page_label()}

    def _page_label(self) -> str:
        """Origine + chemin de la page courante (sans query ni fragment, qui peuvent porter des jetons)."""
        if not self._page or self._page.is_closed():
            return ""
        parts = urlsplit(self._page.url)
        return f"{parts.netloc}{parts.path}"

    def _set(self, state: State, message: str) -> None:
        self._state = state
        self._message = message

    # ------------------------------------------------------------- connexion
    async def login(self, identifier: str, password: str) -> dict[str, str]:
        if self._lock.locked():
            return {**self.status(), "message": "Une connexion est déjà en cours."}
        async with self._lock:
            await self._teardown()
            self._set(State.LOGGING_IN, "Connexion en cours…")
            self.log(f"Connexion demandée pour {_mask(identifier)}.")
            try:
                await self._start_browser()
                outcome = await self._do_login(identifier, password)
            except Exception as exc:  # noqa: BLE001 - on ne journalise que le type, jamais le contenu
                self.log(f"Erreur technique pendant la connexion ({type(exc).__name__}).", "error")
                await self._teardown()
                self._set(State.ERROR, "Erreur technique pendant la connexion.")
                return self.status()
            if outcome.success:
                self._set(State.LOGGED_IN, outcome.message)
                self.log(outcome.message)
            else:
                # Le navigateur reste ouvert pour que la page « Vision » montre la cause
                # (ex. Turnstile). La prochaine tentative ou la déconnexion le ferme.
                self.log(outcome.message, "error")
                self._set(State.ERROR, outcome.message)
            return self.status()

    async def logout(self) -> dict[str, str]:
        async with self._lock:
            await self._teardown()
            self._set(State.IDLE, "Non connecté.")
            self.log("Déconnexion : session du navigateur fermée.")
            return self.status()

    async def _start_browser(self) -> None:
        s = self._settings
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=s.headless,
            executable_path=s.chromium_path,
            args=["--no-sandbox"] if s.no_sandbox else [],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="fr-FR"
        )
        self._page = await self._context.new_page()

    async def _do_login(self, identifier: str, password: str) -> _Outcome:
        s = self._settings
        page = self._page
        assert page is not None
        await page.goto(s.login_url, wait_until="domcontentloaded", timeout=45_000)
        self.log("Page de connexion chargée.")

        await self._refuse_cookies(page)

        email = page.locator(s.email_selector).first
        pwd = page.locator(s.password_selector).first
        try:
            await email.wait_for(state="visible", timeout=15_000)
        except Exception:  # noqa: BLE001
            return _Outcome(False, "Formulaire de connexion introuvable (sélecteurs à vérifier).")

        await email.fill(identifier)
        await pwd.fill(password)
        await page.locator(s.submit_selector).first.click()
        self.log("Formulaire envoyé, attente du résultat.")
        return await self._wait_outcome(page)

    async def _refuse_cookies(self, page: Page) -> None:
        """Choix par défaut : refuser les traceurs optionnels (bandeau de consentement)."""
        button = page.get_by_role("button", name=COOKIE_REFUSE_NAME, exact=False)
        try:
            await button.first.click(timeout=4_000)
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

    # --------------------------------------------------------------- captures
    async def screenshot_b64(self) -> str | None:
        page = self._page
        if page is None or page.is_closed():
            return None
        try:
            data = await page.screenshot(type="jpeg", quality=60, timeout=5_000)
        except Exception:  # noqa: BLE001
            return None
        return base64.b64encode(data).decode("ascii")

    # --------------------------------------------------------------- nettoyage
    async def _teardown(self) -> None:
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
        async with self._lock:
            await self._teardown()
