"""Bonus d'activité : récupéré au début de chaque action du bot, quelle que soit la planète affichée.

Relevé sur le vrai jeu (2026-11-07) : bouton « Bonus » en bas à gauche (`button[data-guidance-target="activity-bonus"]`),
présent seulement quand un gain est disponible. Un clic crédite le gain sans fenêtre ni confirmation, affiche
« Bonus récupéré (12 min) : +11 077 267 Fer, +7 989 190 Cristal, +2 823 034 Éther, +8 Astrium, +0 Échos. » et le bouton
disparaît (prochain gain 2 à 4 h plus tard, d'après le wiki). Les ressources vont sur la planète active.
"""

from __future__ import annotations

import asyncio
import time

from . import human

BUTTON = 'button[data-guidance-target="activity-bonus"]'
CONFIRM_SECONDS = 5.0

VISIBLE_BUTTON_JS = (
    "(selector) => [...document.querySelectorAll(selector)].findIndex((b) => b.getClientRects().length > 0 && !b.disabled)"
)
MESSAGE_JS = """() => {
  const leaf = [...document.querySelectorAll('body *')].find((e) => !e.children.length && e.getClientRects().length
    && /^Bonus récupéré/i.test((e.textContent || '').trim()));
  return leaf ? leaf.textContent.replace(/\\s+/g, ' ').trim() : null;
}"""


async def _message_soon(page, seconds: float = 1.5) -> str | None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        message = await page.evaluate(MESSAGE_JS)
        if message:
            return message
        await asyncio.sleep(0.2)
    return None


async def claim(page, settings, log) -> str | None:
    """Clique sur « Bonus » s'il est visible. Renvoie le message du jeu (ou un résumé), None si rien n'était à récupérer.
    Ne lève jamais d'erreur : le bonus ne doit pas empêcher l'action qui suit."""
    try:
        if page is None or page.is_closed():
            return None
        index = await page.evaluate(VISIBLE_BUTTON_JS, BUTTON)
        if index < 0:
            return None
        await human.click(page, page.locator(BUTTON).nth(index), settings)
        end = time.monotonic() + CONFIRM_SECONDS
        while time.monotonic() < end:
            message = await page.evaluate(MESSAGE_JS)
            if message:
                log(f"Bonus d'activité : {message}", event=True, kind="launch")
                return message
            if await page.evaluate(VISIBLE_BUTTON_JS, BUTTON) < 0:  # bouton parti : le message suit d'un instant
                message = await _message_soon(page)
                log(f"Bonus d'activité : {message}" if message else "Bonus d'activité récupéré.", event=True, kind="launch")
                return message or "récupéré"
            await asyncio.sleep(0.25)
        log("Bonus d'activité : clic fait mais le bouton est toujours là.", "warn", event=True)
        return None
    except Exception as exc:  # noqa: BLE001 — page en navigation, navigateur fermé… : on passe à l'action suivante
        log(f"Bonus d'activité : vérification impossible ({type(exc).__name__}).", "warn")
        return None
