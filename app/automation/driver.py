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
from .espionage import READ_GALAXY_JS
from .expedition import READ_EXPEDITION_JS, ExpeditionPage, parse_expedition_page
from .production import READ_PRODUCTION_JS, ProdCard, parse_prod_card
from .state import READ_PAGE_JS, READ_PLANETS_JS, Card, PageState, PlanetInfo, parse_page, parse_planets

# Boutons de la navigation du jeu qui mènent à une page (libellés lus dans la barre du bas).
NAV_LABEL = {
    "facilities": "Installations", "tech": "Laboratoire", "staff": "Population", "shipyard": "Chantier spatial",
    "specialized": "Appareils spécialisés", "defense": "Défenses", "operations": "Opérations", "galaxy": "Galaxie",
}
# Pages de production : élément qui prouve que la page est affichée (pas de carte « Améliorer » sur ces pages).
READY_SELECTOR = {
    "staff": '[data-testid="population-grid"]',
    "shipyard": '[data-testid="ship-assembly-grid"], [data-testid="component-stock-summary"]',
    "specialized": '[data-testid="special-unit-grid"]',
    "defense": '[data-testid="defense-grid"]',
    "galaxy": 'input[aria-label="Recherche de destination"]',
}
# Section de production → (page du jeu, nature des cartes lues, libellé de l'onglet interne à ouvrir si besoin).
PRODUCTION_PAGE = {
    "population": ("staff", "population", None),
    "ships": ("shipyard", "ships", "Assemblages"),
    "components": ("shipyard", "components", "Composants"),
    "specialized": ("specialized", "other", None),
    "defense": ("defense", "other", None),
}


class GameUiError(Exception):
    """L'écran du jeu n'est pas celui qu'on attendait."""


@dataclass
class LaunchResult:
    confirmed: bool
    detail: str
    qty: int | None = None  # quantité réellement saisie (utile avec MAX)
    verified: bool = True  # False : le clic est parti mais le débit est trop petit pour se voir à l'écran


@dataclass
class ProdPage:
    cards: list[ProdCard]
    resources: object
    planet: str


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

    async def refresh(self) -> None:
        """Recharge la page affichée : après une longue attente, l'écran peut montrer des ressources périmées."""
        section = self.current_section()
        if not section:
            return
        await self.guard()
        await self.page.reload(wait_until="domcontentloaded", timeout=30_000)
        await self._wait_section(section, 15)

    async def switch_planet(self, planet: PlanetInfo) -> bool:
        if norm(await self.active_planet()) == norm(planet.name):
            # Même planète : changer de planète rafraîchit l'écran, pas ce cas-là — on recharge pour lire des valeurs à jour.
            await self.refresh()
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
    async def _section_ready(self, section: str) -> bool:
        if section == "operations":  # pas d'élément repérable par identifiant : les boutons de mission font foi
            return await self.page.get_by_role("button", name=re.compile(r"^\s*exploration\s*$", re.I)).count() > 0
        selector = READY_SELECTOR.get(section)
        if selector:
            return await self.page.locator(selector).count() > 0
        return bool((await self.read_page(attempts=1)).cards)

    async def _wait_section(self, section: str, timeout: float) -> bool:
        for _ in range(int(timeout / 0.4)):
            if self.current_section() == section and await self._section_ready(section):
                return True
            await asyncio.sleep(0.4)
        return False

    async def open_section(self, section: str) -> bool:
        """Ouvre une page du jeu : clic sur la navigation si elle existe, sinon navigation interne, sinon rechargement."""
        if self.current_section() == section and await self._section_ready(section):
            return True
        await self.guard()
        label = NAV_LABEL.get(section)
        if label:
            nav = self.page.locator("button:visible, a:visible").filter(has_text=re.compile(rf"^\s*{label}\s*$")).first
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

    # ------------------------------------------------------------ production
    async def _read_production_cards(self, kind: str) -> list[ProdCard]:
        raw = await self.page.evaluate(READ_PRODUCTION_JS, kind)
        return [parse_prod_card(c) for c in raw["cards"]]

    async def _open_tab(self, label: str, *, role: str) -> None:
        tab = self.page.get_by_role(role, name=label, exact=True).filter(visible=True).first  # type: ignore[arg-type]
        if not await tab.count():
            raise GameUiError(f"onglet « {label} » introuvable")
        await self.guard()
        await human.click(self.page, tab, self.settings)
        await asyncio.sleep(0.8)

    async def read_production(self, section: str, tab: str | None = None, want: str | None = None, force_tab: bool = False) -> ProdPage:
        """Ouvre la page de production et lit ses cartes. `tab` : onglet de la population (Assaut, Logistique…) ;
        `want` : nom attendu — si absent de la vue affichée, on ouvre l'onglet interne puis on relit."""
        page_name, kind, subtab = PRODUCTION_PAGE[section]
        if not await self.open_section(page_name):
            raise GameUiError(f"page « {page_name} » inaccessible")
        cards = await self._read_production_cards(kind)
        present = lambda: (want is None and not force_tab) or (want is not None and any(norm(c.name) == norm(want) for c in cards))  # noqa: E731
        if (force_tab or not present()) and (tab or subtab):
            if tab:
                await self._open_tab(tab, role="tab")
            else:
                await self._open_tab(subtab, role="button")  # type: ignore[arg-type]
            for _ in range(5):
                cards = await self._read_production_cards(kind)
                if (cards if force_tab else present()):
                    break
                await asyncio.sleep(0.5)
        state = await self.read_page(attempts=2)
        return ProdPage(cards=cards, resources=state.resources, planet=state.planet)

    def _production_card_locator(self, section: str, index: int):
        if section == "ships":
            return self.page.locator('[data-testid="ship-assembly-card"]').filter(
                has=self.page.locator('[data-testid="ship-production-control"]')).nth(index)
        if section == "components":
            return self.page.locator('[data-testid="component-production-row"]').nth(index)
        raise ValueError(section)

    async def produce(self, section: str, tab: str | None, name: str, qty: int | None) -> LaunchResult:
        """Saisit la quantité (ou clique sur MAX), clique sur « Recruter » / « Lancer », puis vérifie le débit."""
        before = await self.read_production(section, tab, want=name)
        index = next((i for i, c in enumerate(before.cards) if norm(c.name) == norm(name)), None)
        if index is None:
            return LaunchResult(False, "élément introuvable")
        fresh = before.cards[index]
        if not fresh.has_controls or not fresh.can_produce:
            return LaunchResult(False, "le bouton n'est plus actif")
        page = self.page
        if section in ("ships", "components"):
            card = self._production_card_locator(section, index)
        else:
            title = page.locator("h3").filter(has_text=re.compile(rf"^\s*{re.escape(fresh.name)}\s*$")).first
            card = title.locator("xpath=ancestor::*[.//input[@type='number']][1]")
        quantity = card.locator("input[type=number]").first
        if section == "ships":
            construction = card.get_by_role("button", name="Construction", exact=True).first
            if await construction.count():
                await human.click(page, construction, self.settings)  # mode « Construction » demandé par le joueur
                await asyncio.sleep(0.6)  # la carte se redessine après le changement de mode
        await self.guard()
        await human.think(self.settings)
        if qty is None:
            maxi = card.get_by_role("button", name=re.compile(r"^(MAX|Quantité maximale)")).first
            if not await maxi.count():
                return LaunchResult(False, "bouton MAX introuvable")
            await human.click(page, maxi, self.settings)
        else:
            await human.click(page, quantity, self.settings)
            await quantity.fill(str(qty))
        await asyncio.sleep(0.3)
        typed = await quantity.input_value()
        try:
            planned = int(re.sub(r"\D", "", typed)) if typed else 0
        except ValueError:
            planned = 0
        if planned < 1 or (qty is not None and planned != qty):
            return LaunchResult(False, f"la quantité saisie n'est pas celle demandée (« {typed} »)")
        if section == "components":
            go = card.locator('[data-testid="component-row-controls"] button').last
        elif section == "ships":
            go = card.get_by_role("button", name="Lancer", exact=True).first
        else:
            go = card.get_by_role("button", name=re.compile(r"^(Recruter|Lancer)\b")).first
        if not await go.count() or not await go.is_enabled():
            return LaunchResult(False, "bouton de production introuvable ou désactivé", planned)
        await self.guard()
        await human.click(page, go, self.settings)
        await asyncio.sleep(1.2)
        dialog = page.locator("[role=dialog], [aria-modal=true]").first
        if await dialog.count() and await dialog.is_visible():
            raise GameUiError("une fenêtre inattendue s'est ouverte après le clic (capture de la page nécessaire)")
        after = await self.read_page(attempts=2)
        # 1) un débit visible : le jeu arrondit les gros stocks, un petit débit peut passer inaperçu
        invisible = True
        for label, unit in fresh.costs.items():
            expected = unit * planned
            step = before.resources.step(label)
            was, now = before.resources.stock(label), after.resources.stock(label)
            if unit <= 0 or was is None or now is None or step is None or expected < 2 * step:
                continue
            invisible = False
            if was - now >= 0.5 * expected:
                return LaunchResult(True, f"{label} débité", planned)
        if not invisible:
            return LaunchResult(False, "les ressources n'ont pas été débitées", planned)
        # 2) débit trop petit pour se voir : on regarde si le stock ou la file d'attente de l'élément a augmenté
        again = await self._read_production_cards(PRODUCTION_PAGE[section][1])
        seen = next((c for c in again if norm(c.name) == norm(name)), None)
        if seen is not None and seen.owned is not None and fresh.owned is not None and seen.owned + seen.queued > fresh.owned + fresh.queued:
            return LaunchResult(True, "stock ou file d'attente en hausse", planned)
        return LaunchResult(True, "débit trop petit pour être vérifié à l'écran", planned, verified=False)


    # ------------------------------------------------------------ expéditions (lecture seule)
    async def read_expedition(self, way: str) -> ExpeditionPage:
        return await self.read_operation(way, "Exploration")

    async def read_operation(self, way: str, mission: str) -> ExpeditionPage:
        """Ouvre Opérations, choisit le moyen de lancement (Piliers / Flotte) et la mission (« Exploration »,
        « Attaque »…), puis lit les compteurs et les unités disponibles (« Disponibles : N » ou « Stock : N »).
        Aucun champ n'est rempli, rien n'est lancé."""
        if not await self.open_section("operations"):
            raise GameUiError("page « Opérations » inaccessible")
        for label in ("Piliers" if way == "piliers" else "Flotte", mission):
            button = self.page.get_by_role("button", name=re.compile(rf"^\s*{label}\s*$", re.I)).filter(visible=True).first
            if not await button.count():
                raise GameUiError(f"bouton « {label} » introuvable sur la page Opérations")
            await self.guard()
            await human.click(self.page, button, self.settings)
            await asyncio.sleep(0.6)
        raw = {}
        for _ in range(10):
            raw = await self.page.evaluate(READ_EXPEDITION_JS)
            if raw.get("units") and (raw.get("active") or mission != "Exploration"):
                break
            await asyncio.sleep(0.5)
        return parse_expedition_page(way, raw)

    # ------------------------------------------------------------ galaxie (lecture seule)
    async def read_galaxy(self) -> list[dict]:
        """Ouvre la Galaxie (centrée sur la planète active) et lit la liste des planètes affichées. Aucun clic d'envoi."""
        if not await self.open_section("galaxy"):
            raise GameUiError("page « Galaxie » inaccessible")
        rows: list[dict] = []
        for _ in range(20):  # la liste se charge par portions (« Chargement de la portion… »)
            rows = await self.page.evaluate(READ_GALAXY_JS)
            if rows:
                break
            await asyncio.sleep(0.5)
        return rows
