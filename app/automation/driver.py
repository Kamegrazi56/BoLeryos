"""Pilote de l'interface du jeu : lire l'état, changer de planète, ouvrir une page, lancer une amélioration.

Principe de prudence : on relit toujours l'écran avant d'agir (planète active, bouton actif) et après (ressources
débitées). Si l'écran n'est pas celui qu'on attend, on s'arrête et on le dit dans le journal.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from .. import human
from .numbers import norm
from .state import READ_PAGE_JS, READ_PLANETS_JS, Card, PageState, PlanetInfo, parse_page, parse_planets

# Boutons de la navigation du jeu qui mènent à une page (libellés lus dans la barre du bas).
NAV_LABEL = {"facilities": "Installations", "tech": "Laboratoire"}


class GameUiError(Exception):
    """L'écran du jeu n'est pas celui qu'on attendait."""


@dataclass
class LaunchResult:
    confirmed: bool
    detail: str


class GameDriver:
    def __init__(self, bot, guard: Callable[[], Awaitable[None]]) -> None:
        self.bot = bot
        self.guard = guard

    # ---------------------------------------------------------------- accès
    @property
    def page(self):
        page = self.bot._page
        if page is None or page.is_closed():
            raise GameUiError("le navigateur n'est pas disponible")
        return page

    @property
    def settings(self):
        return self.bot._settings

    def _universe_path(self, section: str) -> str:
        match = re.search(r"/game/([^/]+)/", self.page.url)
        if not match:
            raise GameUiError("la page affichée n'est pas une page de jeu (/game/…)")
        return f"/game/{match.group(1)}/{section}"

    def current_section(self) -> str:
        match = re.search(r"/game/[^/]+/([^/?#]+)", self.page.url)
        return match.group(1) if match else ""

    # -------------------------------------------------------------- lecture
    async def read_page(self, attempts: int = 4) -> PageState:
        """Lit la page ; les cartes arrivent parfois après le chargement, on réessaie quelques fois."""
        state = None
        for i in range(attempts):
            state = parse_page(await self.page.evaluate(READ_PAGE_JS))
            if state.cards or state.resources.fer is not None and i >= 1:
                break
            await asyncio.sleep(0.7)
        assert state is not None
        return state

    async def active_planet(self) -> str:
        return (await self.read_page(attempts=1)).planet

    async def _open_planets_window(self) -> list[PlanetInfo]:
        launcher = self.page.get_by_role("button", name="Planètes", exact=True).first
        if not await launcher.count():
            raise GameUiError("bouton « Planètes » introuvable")
        await self.guard()
        await human.click(self.page, launcher, self.settings)
        for _ in range(24):
            planets = parse_planets(await self.page.evaluate(READ_PLANETS_JS))
            if planets:
                return planets
            await asyncio.sleep(0.25)
        raise GameUiError("la liste des planètes ne s'affiche pas")

    async def _close_planets_window(self) -> None:
        close = self.page.get_by_role("button", name="Fermer", exact=True).first
        try:
            if await close.count() and await close.is_visible():
                await human.click(self.page, close, self.settings)
            else:
                await self.page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001 - la fenêtre s'est déjà fermée
            pass
        await asyncio.sleep(0.2)

    async def list_planets(self) -> list[PlanetInfo]:
        planets = await self._open_planets_window()
        await human.think(self.settings)
        await self._close_planets_window()
        return planets

    async def switch_planet(self, planet: PlanetInfo) -> bool:
        if norm(await self.active_planet()) == norm(planet.name):
            return True
        await self._open_planets_window()
        button = self.page.locator("button").filter(has_text=planet.name).filter(has_text=planet.coords).first
        if not await button.count():
            await self._close_planets_window()
            raise GameUiError(f"planète « {planet.name} » introuvable dans la liste")
        await self.guard()
        await human.think(self.settings)
        await human.click(self.page, button, self.settings)
        await asyncio.sleep(0.8)
        if await self.page.get_by_role("button", name="Fermer", exact=True).first.is_visible():
            await self._close_planets_window()
        for _ in range(20):  # on vérifie que le jeu a bien changé de planète
            if norm(await self.active_planet()) == norm(planet.name):
                return True
            await asyncio.sleep(0.3)
        return False

    # ----------------------------------------------------------- navigation
    async def _wait_section(self, section: str, timeout: float) -> bool:
        for _ in range(int(timeout / 0.4)):
            if self.current_section() == section and (await self.read_page(attempts=1)).cards:
                return True
            await asyncio.sleep(0.4)
        return False

    async def open_section(self, section: str) -> bool:
        """Ouvre une page du jeu : clic sur la navigation si elle existe, sinon navigation interne, sinon rechargement."""
        if self.current_section() == section and (await self.read_page(attempts=1)).cards:
            return True
        await self.guard()
        label = NAV_LABEL.get(section)
        if label:
            nav = self.page.locator("button, a").filter(has_text=re.compile(rf"^\s*{label}\s*$")).first
            if await nav.count():
                await human.click(self.page, nav, self.settings)
                if await self._wait_section(section, 6):
                    return True
        path = self._universe_path(section)
        await self.page.evaluate(
            "(p) => { history.pushState({}, '', p); window.dispatchEvent(new PopStateEvent('popstate')); }", path
        )
        if await self._wait_section(section, 5):
            return True
        self.bot.log(f"Navigation interne sans effet vers « {section} » : rechargement de la page.", "warn")
        await self.guard()
        origin = re.match(r"^(https?://[^/]+)", self.page.url).group(1)  # type: ignore[union-attr]
        await self.page.goto(origin + path, wait_until="domcontentloaded", timeout=30_000)
        return await self._wait_section(section, 15)

    # --------------------------------------------------------------- action
    def _card_button(self, name: str, action: str):
        title = self.page.locator("h1,h2,h3,h4").filter(has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")).first
        card = title.locator("xpath=ancestor::*[.//button][1]")
        return card.locator("button").filter(has_text=re.compile(rf"^\s*{re.escape(action)}\s*$")).first

    async def launch(self, card: Card, section: str) -> LaunchResult:
        """Clique sur le bouton de la carte puis vérifie que les ressources ont bien été débitées."""
        if not await self.open_section(section):
            return LaunchResult(False, f"page « {section} » inaccessible")
        before = await self.read_page()
        fresh = next((c for c in before.cards if norm(c.name) == norm(card.name)), None)
        if fresh is None or fresh.action != card.action or not fresh.enabled:
            return LaunchResult(False, "le bouton n'est plus actif")
        button = self._card_button(card.name, card.action)
        if not await button.count() or not await button.is_enabled():
            return LaunchResult(False, "bouton introuvable ou désactivé")
        await self.guard()
        await human.think(self.settings)
        await human.click(self.page, button, self.settings)
        await asyncio.sleep(1.2)
        dialog = self.page.locator("[role=dialog], [aria-modal=true]").first
        if await dialog.count() and await dialog.is_visible():
            raise GameUiError("une fenêtre inattendue s'est ouverte après le clic (capture de la page nécessaire)")
        after = await self.read_page()
        for label, cost in fresh.costs.items():
            was, now = before.resources.stock(label), after.resources.stock(label)
            if was is not None and now is not None and was - now >= 0.5 * cost:
                return LaunchResult(True, f"{label} débité")
        return LaunchResult(False, "les ressources n'ont pas été débitées")
