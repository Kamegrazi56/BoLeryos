"""Comportement « humain » : délais courts et aléatoires, déplacement de la souris, frappe touche par touche.

Désactivable avec `human_mode=False` (utilisé par les tests pour aller vite).
"""

from __future__ import annotations

import asyncio
import random

from playwright.async_api import Locator, Page

from .config import Settings


async def think(s: Settings, delay: tuple[float, float] | None = None) -> None:
    """Pause de « réflexion » entre deux actions.

    Durée aléatoire dans `delay` (minimum, maximum) s'il est donné, sinon dans [human_delay_min, human_delay_max].
    """
    if s.human_mode:
        low, high = delay if delay else (s.human_delay_min, s.human_delay_max)
        await asyncio.sleep(random.uniform(low, high))


async def micro(s: Settings) -> None:
    """Très courte pause (entre le déplacement de la souris et le clic, etc.)."""
    if s.human_mode:
        await asyncio.sleep(random.uniform(0.05, 0.2))


async def move_mouse_randomly(page: Page) -> None:
    """Place la souris à un endroit quelconque de la page (au lieu du coin supérieur gauche)."""
    await page.mouse.move(random.uniform(200, 900), random.uniform(150, 600))


async def click(page: Page, locator: Locator, s: Settings) -> None:
    """Déplace la souris progressivement vers un point aléatoire de l'élément, puis clique."""
    await locator.scroll_into_view_if_needed()
    box = await locator.bounding_box()
    if box is None:
        await locator.click()
        return
    x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
    y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
    await page.mouse.move(x, y, steps=random.randint(10, 25))
    await micro(s)
    await page.mouse.click(x, y, delay=random.randint(40, 120))


async def type_text(page: Page, text: str, s: Settings) -> None:
    """Tape le texte caractère par caractère, avec un rythme irrégulier."""
    for char in text:
        await page.keyboard.type(char)
        if s.human_mode:
            delay = random.uniform(s.key_delay_min, s.key_delay_max)
            if random.random() < 0.06:  # hésitation occasionnelle
                delay += random.uniform(0.2, 0.5)
            await asyncio.sleep(delay)
