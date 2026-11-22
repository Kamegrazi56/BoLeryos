"""Fenêtres du jeu qui peuvent apparaître à tout moment et masquer la page (ex. « Journal de développement »).

Deux protections se complètent :
- un gestionnaire Playwright (`add_locator_handler`) ferme la fenêtre dès qu'elle bloque une action du bot ;
- `dismiss()` est appelée avant chaque action et chaque lecture (clics, frappes, surveillance des attaques),
  car les clics à la souris (`page.mouse`) ne déclenchent pas le gestionnaire.

Pour fermer, on n'utilise AUCUN `locator` : toute opération sur un élément de la fenêtre déclencherait le
gestionnaire (« intercepting action »), qui la fermerait avant l'opération et la ferait échouer. La position du
bouton est lue avec `page.evaluate`, puis le clic est un vrai clic de souris.

Pour traiter une autre fenêtre précise, ajouter une ligne à `INTERRUPTIONS`. Toute AUTRE annonce du jeu (même
« transmission » avec sa croix « Fermer la transmission », ex. « Rattrapage galactique ») est fermée sans être listée :
voir `TRANSMISSION_CLOSE`. Les fenêtres que le bot ouvre lui-même (Piliers, Espionnage, modèles, confirmations) n'ont pas
cette croix et ne sont donc jamais fermées par erreur.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass

from playwright.async_api import Locator, Page

GONE_TIMEOUT_MS = 2_000
MAX_PER_PASS = 4  # annonces empilées fermées au plus par passage (évite toute boucle sans fin)

# Croix des annonces du jeu (« transmissions »), relevée sur le vrai jeu le 2026-11-22 (« Rattrapage galactique »).
TRANSMISSION_CLOSE = 'button[aria-label="Fermer la transmission"]'
_TRANSMISSION_DIALOG = f'[role="dialog"]:has({TRANSMISSION_CLOSE})'

# Centre du bouton de fermeture de la fenêtre si elle est visible, sinon null.
_FIND_CLOSE_JS = """([dialog, close]) => {
  const el = document.querySelector(dialog);
  if (!el || !el.getClientRects().length) return null;
  const button = el.querySelector(close);
  if (!button) return null;
  const r = button.getBoundingClientRect();
  return r.width && r.height ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null;
}"""
# Première annonce visible (fenêtre avec la croix des transmissions) : son titre et le centre de la croix.
_FIND_ANY_JS = """(close) => {
  for (const el of document.querySelectorAll('[role="dialog"]')) {
    if (!el.getClientRects().length) continue;
    const button = el.querySelector(close);
    if (!button) continue;
    const r = button.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    const top = document.elementFromPoint(x, y);  // une annonce empilée par-dessus masque la croix : on ferme d'abord celle du dessus
    if (top && button.contains(top)) return { x, y, label: el.getAttribute('aria-label') || '' };
  }
  return null;
}"""
# Vrai tant que la croix donnée est encore visible dans une fenêtre portant ce titre.
_ANY_GONE_JS = """([close, label]) => ![...document.querySelectorAll('[role="dialog"]')].some(
  (el) => el.getClientRects().length && (el.getAttribute('aria-label') || '') === label && el.querySelector(close)
)"""
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
# Annonce « Votre empire vous accompagne sur Steam » (relevée sur le vrai jeu le 2026-11-21) : elle s'affiche quelques
# secondes après CHAQUE chargement de page et avale les clics du bot (ex. « Gérer les Piliers » ne s'ouvrait pas).
STEAM = Interruption(
    name="Annonce Steam",
    dialog='[role="dialog"][aria-label*="Steam" i]',
    close='button[aria-label="Fermer la transmission"]',
)
# Annonce « Vos packs sont arrivés à leur terme » (relevée sur la production le 2026-11-21) : même type de fenêtre, elle
# bloque les lectures tant qu'elle est ouverte. On ne clique que sur la croix, jamais sur « Réactiver mes packs ».
PACKS_EXPIRED = Interruption(
    name="Annonce des packs expirés",
    dialog='[role="dialog"][aria-label*="packs sont arrivés" i]',
    close='button[aria-label="Fermer la transmission"]',
)
INTERRUPTIONS: tuple[Interruption, ...] = (DEVLOG, STEAM, PACKS_EXPIRED)

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


async def _close_any_transmission(page: Page) -> str | None:
    """Ferme une annonce du jeu non listée ; retourne son titre, ou None s'il n'y en a pas (ou si elle résiste)."""
    try:
        found = await page.evaluate(_FIND_ANY_JS, TRANSMISSION_CLOSE)
        if found is None:
            return None
        await page.mouse.click(found["x"], found["y"])
        await page.wait_for_function(_ANY_GONE_JS, arg=[TRANSMISSION_CLOSE, found["label"]], timeout=GONE_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 - déjà fermée, page en navigation… : l'action du bot continue
        return None
    name = found["label"] or "Annonce du jeu"
    _notify(page, name)
    return name


async def dismiss(page: Page) -> list[str]:
    """Ferme les fenêtres gênantes visibles ; retourne les noms de celles qui ont été fermées. Ne lève jamais d'erreur."""
    names = [item.name for item in INTERRUPTIONS if await _close(page, item)]
    for _ in range(MAX_PER_PASS):
        name = await _close_any_transmission(page)
        if name is None:
            break
        names.append(name)
    return names


async def install(page: Page, on_closed: Callable[[str], None] | None = None) -> None:
    """Enregistre les gestionnaires sur la page ; `on_closed(nom)` est appelé à chaque fermeture."""
    if on_closed is not None:
        _callbacks[page] = on_closed
    for item in INTERRUPTIONS:

        async def handler(_dialog: Locator, item: Interruption = item) -> None:
            await _close(page, item)

        await page.add_locator_handler(page.locator(item.dialog), handler)

    async def any_handler(_dialog: Locator) -> None:
        await _close_any_transmission(page)

    await page.add_locator_handler(page.locator(_TRANSMISSION_DIALOG), any_handler)
