"""Fenêtres du jeu qui peuvent apparaître à tout moment et masquer la page (ex. « Journal de développement »).

Deux protections se complètent :
- un gestionnaire Playwright (`add_locator_handler`) ferme la fenêtre dès qu'elle bloque une action du bot ;
- `dismiss()` est appelée avant chaque action et chaque lecture (clics, frappes, surveillance des attaques),
  car les clics à la souris (`page.mouse`) ne déclenchent pas le gestionnaire.

Pour fermer, on n'utilise AUCUN `locator` : toute opération sur un élément de la fenêtre déclencherait le
gestionnaire (« intercepting action »), qui la fermerait avant l'opération et la ferait échouer. La position du
bouton est lue avec `page.evaluate`, puis le clic est un vrai clic de souris.

Pour traiter une autre fenêtre, ajouter une ligne à `INTERRUPTIONS`.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass

from playwright.async_api import Locator, Page

GONE_TIMEOUT_MS = 2_000

# Centre du bouton de fermeture de la fenêtre si elle est visible, sinon null.
_FIND_CLOSE_JS = """([dialog, close]) => {
  const el = document.querySelector(dialog);
  if (!el || !el.getClientRects().length) return null;
  const button = el.querySelector(close);
  if (!button) return null;
  const r = button.getBoundingClientRect();
  return r.width && r.height ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null;
}"""
_IS_GONE_JS = "(dialog) => { const el = document.querySelector(dialog); return !el || !el.getClientRects().length; }"


@dataclass(frozen=True)
class Interruption:
    name: str
    dialog: str  # sélecteur CSS de la fenêtre (précis : le jeu a d'autres `role=dialog`, dont un panneau caché)
    close: str  # sélecteur CSS du bouton de fermeture, cherché à l'intérieur de la fenêtre


DEVLOG = Interruption(
    name="Journal de développement",
    dialog='[role="dialog"][aria-modal="true"][aria-label*="Devlog" i]',
    close='button[aria-label="Fermer"]',
)
INTERRUPTIONS: tuple[Interruption, ...] = (DEVLOG,)

# Rappel (journal du bot) enregistré par page ; la page disparaît, l'entrée aussi.
_callbacks: weakref.WeakKeyDictionary[Page, Callable[[str], None]] = weakref.WeakKeyDictionary()


def _notify(page: Page, name: str) -> None:
    callback = _callbacks.get(page)
    if callback is not None:
        callback(name)


async def _close(page: Page, item: Interruption) -> bool:
    """Clique sur « Fermer » et attend que la fenêtre disparaisse. Retourne True si c'est cette fonction qui l'a fermée."""
    try:
        point = await page.evaluate(_FIND_CLOSE_JS, [item.dialog, item.close])
        if point is None:
            return False
        await page.mouse.click(point["x"], point["y"])
        await page.wait_for_function(_IS_GONE_JS, arg=item.dialog, timeout=GONE_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - déjà fermée, page en navigation… : l'action du bot continue
        return False
    _notify(page, item.name)
    return True


async def dismiss(page: Page) -> list[str]:
    """Ferme les fenêtres gênantes visibles ; retourne les noms de celles qui ont été fermées. Ne lève jamais d'erreur."""
    return [item.name for item in INTERRUPTIONS if await _close(page, item)]


async def install(page: Page, on_closed: Callable[[str], None] | None = None) -> None:
    """Enregistre les gestionnaires sur la page ; `on_closed(nom)` est appelé à chaque fermeture."""
    if on_closed is not None:
        _callbacks[page] = on_closed
    for item in INTERRUPTIONS:

        async def handler(_dialog: Locator, item: Interruption = item) -> None:
            await _close(page, item)

        await page.add_locator_handler(page.locator(item.dialog), handler)
