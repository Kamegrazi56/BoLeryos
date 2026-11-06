"""Liste des planètes du joueur, lue dans l'interface du jeu (fenêtre « Planètes / Favoris / Piliers »).

Lecture seule : le bot ouvre la fenêtre, lit nom, coordonnées et stocks de chaque planète, puis la referme.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import human, interruptions
from .numbers import parse_amount

# Le bouton du jeu s'appelait « Planètes », il s'appelle « Planètes / Favoris / Piliers » (devlog 1.1.25) : on accepte
# les deux. Le sous-onglet « Planètes » de la fenêtre ouverte est caché tant qu'elle est fermée, il n'est pas pris.
# Pas de « / » dans le motif : Playwright l'envoie au navigateur sous forme de littéral /…/ et un « / » non échappé
# le rend invalide.
PLANETS_LAUNCHER_RE = re.compile(r"^Planètes(\s.*)?$")

# Liste des planètes (fenêtre ouverte) : chaque entrée est un bouton « nom, coordonnées, stocks ».
READ_PLANETS_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const COORD = /^[A-Z]\\d{2} \\d{2} \\d{2}$/;
  const out = [];
  for (const b of document.querySelectorAll('button')) {
    const leaves = [...b.querySelectorAll('*')].filter((el) => el.children.length === 0 && text(el) !== '');
    const ci = leaves.findIndex((el) => COORD.test(text(el)));
    if (ci < 1 || leaves.length < 5) continue;
    out.push({ name: text(leaves[ci - 1]), coords: text(leaves[ci]), stocks: leaves.slice(ci + 1, ci + 4).map(text) });
  }
  return out;
}
"""


class GameUiError(Exception):
    """L'écran du jeu n'est pas celui qu'on attendait."""


class Interrupted(Exception):
    """La lecture s'arrête proprement (contrôle manuel, session fermée…)."""


@dataclass(frozen=True)
class PlanetInfo:
    name: str
    coords: str
    stocks: tuple[float | None, float | None, float | None]  # Fer, Cristal, Éther


def _amount(raw: str | None) -> float | None:
    try:
        return parse_amount(raw) if raw else None
    except ValueError:
        return None


def parse_planets(raw: list[dict]) -> list[PlanetInfo]:
    return [
        PlanetInfo(p["name"], p["coords"], tuple(_amount(s) for s in (p["stocks"] + [None, None, None])[:3]))  # type: ignore[arg-type]
        for p in raw
    ]


class PlanetsReader:
    def __init__(self, bot, guard: Callable[[], Awaitable[None]]) -> None:
        self.bot = bot
        self.guard = guard  # point de contrôle appelé avant chaque action : lève Interrupted si on doit s'arrêter

    @property
    def page(self):
        page = self.bot._page
        if page is None or page.is_closed():
            raise GameUiError("le navigateur n'est pas disponible")
        return page

    @property
    def settings(self):
        return self.bot._settings

    async def _eval(self, script: str):
        """`page.evaluate` précédé de la fermeture d'une éventuelle fenêtre du jeu (ex. « Journal de développement »)."""
        page = self.page
        await interruptions.dismiss(page)
        return await page.evaluate(script)

    async def _open_window(self) -> list[PlanetInfo]:
        launcher = self.page.get_by_role("button", name=PLANETS_LAUNCHER_RE).first
        if not await launcher.count():
            raise GameUiError("bouton « Planètes » introuvable")
        await self.guard()
        await human.click(self.page, launcher, self.settings)
        for _ in range(24):
            planets = parse_planets(await self._eval(READ_PLANETS_JS))
            if planets:
                return planets
            await asyncio.sleep(0.25)
        raise GameUiError("la liste des planètes ne s'affiche pas")

    async def _close_window(self) -> None:
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
        planets = await self._open_window()
        await human.think(self.settings)
        await self._close_window()
        return planets
