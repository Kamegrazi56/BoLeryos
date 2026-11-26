"""Confirmation « Production longue » du jeu au moment d'un lancement.

Pourquoi : quand la file d'attente dépasse un jour, le jeu n'envoie pas la commande tout de suite après le clic sur
« Recruter », « Produire » ou « Lancer » : il affiche un encart DANS la page (relevé sur le vrai jeu le 26/11 : titre
« Production longue », texte « Ce pack de production dure 1 j 10 h. Confirmez-vous son lancement ? », boutons ANNULER et
CONFIRMER). Sans clic sur CONFIRMER, rien n'est lancé alors que le bot croit avoir lancé. Le bot confirme donc, et seulement
juste après le clic de lancement qu'il a lui-même fait (le réglage de la ligne fait foi : la file longue est voulue). Le texte
du jeu est écrit dans le journal. Par prudence, une vraie fenêtre native (confirm / alert) est aussi acceptée pendant ce clic.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from playwright.async_api import Dialog, Locator, Page

from . import human
from .config import Settings

LONG_TITLE = re.compile(r"production longue", re.I)
CONFIRM_TEXT = re.compile(r"confirmez-vous|dure\b", re.I)
CONFIRM_BUTTON = re.compile(r"^confirmer$", re.I)
APPEAR_SECONDS = 2.5  # le jeu affiche l'encart presque aussitôt ; sans encart, le lancement était direct
GONE_SECONDS = 4.0

ACCEPTED_TYPES = ("confirm", "alert")  # « prompt » (saisie) et « beforeunload » (quitter la page) restent refusés


@asynccontextmanager
async def accepting_launch_dialogs(page: Page, log: Callable[[str], None]) -> AsyncIterator[list[str]]:
    """Accepte les confirmations du jeu pendant le bloc ; fournit la liste des messages vus (vide = aucune fenêtre)."""
    seen: list[str] = []

    async def on_dialog(dialog: Dialog) -> None:
        seen.append(dialog.message)
        if dialog.type in ACCEPTED_TYPES:
            log(f"Le jeu demande une confirmation avant le lancement : « {' '.join(dialog.message.split())[:200]} » — acceptée.")
            await dialog.accept()
        else:
            log(f"Fenêtre inattendue du jeu ({dialog.type}) : refusée.")
            await dialog.dismiss()

    page.on("dialog", on_dialog)
    try:
        yield seen
    finally:
        page.remove_listener("dialog", on_dialog)


async def _long_title_visible(page: Page) -> bool:
    title = page.get_by_text(LONG_TITLE)
    return await title.count() > 0 and await title.first.is_visible()


async def _confirm_button(page: Page) -> Locator | None:
    """Bouton CONFIRMER de l'encart : cherché dans l'encart (fenêtre portant le titre), sinon dans la page (le dernier ouvert)."""
    panel = page.locator("[role=dialog], [aria-modal=true]").filter(has_text=LONG_TITLE)
    for scope in (panel.last, page):
        button = scope.get_by_role("button", name=CONFIRM_BUTTON)
        if await button.count():
            return button.last
    return None


async def confirm_long_production(page: Page, log: Callable[..., None], settings: Settings) -> bool:
    """Si l'encart « Production longue » apparaît juste après un clic de lancement, clique sur CONFIRMER. Vrai si confirmé."""
    end = time.monotonic() + APPEAR_SECONDS
    while not await _long_title_visible(page):
        if time.monotonic() >= end:
            return False  # lancement direct, aucune confirmation demandée
        await asyncio.sleep(0.2)
    button = await _confirm_button(page)
    text = page.get_by_text(CONFIRM_TEXT)
    said = " ".join((await text.first.inner_text()).split())[:200] if await text.count() else "Production longue"
    if button is None:
        log(f"Le jeu demande une confirmation (« {said} ») mais le bouton CONFIRMER est introuvable : rien lancé.", "warn", event=True)
        return False
    log(f"Le jeu demande une confirmation avant le lancement : « {said} » — confirmée.")
    await human.click(page, button, settings)
    end = time.monotonic() + GONE_SECONDS
    while await _long_title_visible(page):
        if time.monotonic() >= end:
            log("L'encart « Production longue » est resté ouvert après le clic sur CONFIRMER.", "warn", event=True)
            return False
        await asyncio.sleep(0.2)
    return True


async def click_launch(page: Page, go: Locator, settings: Settings, log: Callable[..., None]) -> None:
    """Clique sur le bouton de lancement, puis confirme l'avertissement de production longue s'il apparaît."""
    async with accepting_launch_dialogs(page, log):
        await human.click(page, go, settings)
        await confirm_long_production(page, log, settings)
