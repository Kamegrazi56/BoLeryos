"""Comportement « humain » : délais courts et aléatoires, déplacement de la souris, frappe touche par touche.

Désactivable avec `human_mode=False` (utilisé par les tests pour aller vite).
"""

from __future__ import annotations

import asyncio
import random

from playwright.async_api import Locator, Page

from . import interruptions
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


# Vrai si l'élément visible au point (x, y) est l'élément lui-même ou l'un de ses descendants (rien ne le masque).
_HIT_JS = "(e, p) => { const t = document.elementFromPoint(p[0], p[1]); return !!t && e.contains(t); }"
_IS_BUTTON_JS = "e => e.tagName === 'BUTTON' || e.getAttribute('role') === 'button'"
# Autres points essayés quand le premier tombe sur autre chose (fractions de la largeur et de la hauteur de l'élément).
_FALLBACK_POINTS = ((0.5, 0.5), (0.25, 0.3), (0.75, 0.3), (0.25, 0.7), (0.75, 0.7))


async def _reachable_point(locator: Locator, box: dict) -> tuple[float, float] | None:
    """Premier point de l'élément que la souris atteint vraiment (le jeu a des barres d'outils flottantes qui masquent des boutons)."""
    candidates = [(box["x"] + box["width"] * random.uniform(0.2, 0.8), box["y"] + box["height"] * random.uniform(0.25, 0.75))]
    candidates += [(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy) for fx, fy in _FALLBACK_POINTS]
    for x, y in candidates:
        if await locator.evaluate(_HIT_JS, [x, y]):
            return x, y
    return None


async def click(page: Page, locator: Locator, s: Settings) -> None:
    """Déplace la souris progressivement vers un point aléatoire de l'élément, puis clique.

    Ferme d'abord une éventuelle fenêtre du jeu (ex. « Journal de développement ») qui intercepterait le clic.
    Si un autre élément du jeu masque tout l'élément (barre d'outils flottante, barre du bas…), le clic tomberait à côté :
    dans ce cas, un bouton est activé au clavier (focus puis Entrée), comme le ferait un utilisateur qui tabule jusqu'à lui."""
    await interruptions.dismiss(page)
    await locator.scroll_into_view_if_needed()
    box = await locator.bounding_box()
    if box is None:
        await locator.click()
        return
    point = await _reachable_point(locator, box)
    if point is None:
        await micro(s)
        await locator.focus()
        if await locator.evaluate(_IS_BUTTON_JS):
            await page.keyboard.press("Enter")
        return
    x, y = point
    await page.mouse.move(x, y, steps=random.randint(10, 25))
    await micro(s)
    await page.mouse.click(x, y, delay=random.randint(40, 120))


async def type_text(page: Page, text: str, s: Settings) -> None:
    """Tape le texte caractère par caractère, avec un rythme irrégulier."""
    await interruptions.dismiss(page)
    for char in text:
        await page.keyboard.type(char)
        if s.human_mode:
            delay = random.uniform(s.key_delay_min, s.key_delay_max)
            if random.random() < 0.06:  # hésitation occasionnelle
                delay += random.uniform(0.2, 0.5)
            await asyncio.sleep(delay)
