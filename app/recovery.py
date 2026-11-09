"""Écran « Récupération interrompue » du jeu (vu sur le vrai jeu au rechargement d'une page Opérations) :
« Récupération interrompue — Impossible de restaurer l'univers actif », boutons « Réessayer » et « Choisir un serveur ».
« Réessayer » rétablit la partie. L'ordonnanceur s'en sert : écran vu après un passage → 2 min d'attente, « Réessayer »,
puis le passage est retenté ; au bout de 3 échecs, il passe à la fonctionnalité suivante.
"""

from __future__ import annotations

SCREEN_TEXT = "Récupération interrompue"
RETRY_LABEL = "Réessayer"

SCREEN_JS = """(wanted) => [...document.querySelectorAll('body *')].some((e) => !e.children.length
  && e.getClientRects().length && (e.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase().startsWith(wanted.toLowerCase()))"""


async def screen_shown(page) -> bool:
    """Vrai si l'écran « Récupération interrompue » est affiché (page absente ou illisible : faux)."""
    if page is None:
        return False
    try:
        return bool(await page.evaluate(SCREEN_JS, SCREEN_TEXT))
    except Exception:  # noqa: BLE001 - page en cours de chargement : on ne conclut rien
        return False


async def press_retry(page) -> bool:
    """Clique sur « Réessayer » s'il est visible. Renvoie vrai si le clic a eu lieu."""
    if page is None:
        return False
    try:
        button = page.get_by_role("button", name=RETRY_LABEL, exact=True)
        for i in range(await button.count()):
            if await button.nth(i).is_visible():
                await button.nth(i).click()
                return True
    except Exception:  # noqa: BLE001 - bouton disparu entre-temps : le passage retenté rechargera la page
        return False
    return False
