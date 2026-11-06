"""Fenêtre « Journal de développement » du jeu : fermée avant chaque action du bot, sans toucher aux autres fenêtres."""

import asyncio

from playwright.async_api import async_playwright

from app import human, interruptions

DEVLOG_NAME = "Journal de développement"

PAGE = """
<button id="target" onclick="window.hits = (window.hits || 0) + 1">Cible</button>
<div id="devlog" role="dialog" aria-modal="true" aria-label="Devlog d’Aleryos"
     style="position:fixed;inset:0;background:#000a;z-index:99">
  <button aria-label="Fermer" onclick="document.getElementById('devlog').remove()">x</button>
</div>
<div id="nav" role="dialog" aria-label="Navigation" hidden>
  <button aria-label="Fermer" onclick="document.getElementById('nav').remove()">x</button>
</div>
<div id="confirm" role="dialog" aria-modal="true" aria-label="Confirmer l'action" style="position:fixed;top:0;right:0">
  <button aria-label="Fermer" onclick="document.getElementById('confirm').remove()">x</button>
</div>
"""


def _run(settings, scenario):
    """Lance Chromium sur la page de test et exécute `scenario(page, closed)` ; `closed` liste les fermetures notées."""

    async def main():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=settings.chromium_path, args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content(PAGE)
                closed: list[str] = []
                await interruptions.install(page, closed.append)
                return await scenario(page, closed)
            finally:
                await browser.close()

    return asyncio.run(main())


def test_dismiss_closes_devlog_and_reports_it(settings):
    async def scenario(page, closed):
        names = await interruptions.dismiss(page)
        return names, closed, await page.locator("#devlog").count()

    names, closed, remaining = _run(settings, scenario)
    assert names == [DEVLOG_NAME]
    assert closed == [DEVLOG_NAME]
    assert remaining == 0


def test_dismiss_leaves_other_dialogs_alone(settings):
    async def scenario(page, closed):
        await interruptions.dismiss(page)
        return await page.locator("#nav").count(), await page.locator("#confirm").count()

    nav, confirm = _run(settings, scenario)
    assert nav == 1  # panneau « Navigation » caché du jeu : jamais fermé
    assert confirm == 1  # autre fenêtre modale : jamais fermée


def test_dismiss_without_dialog_does_nothing(settings):
    async def scenario(page, closed):
        await interruptions.dismiss(page)
        return await interruptions.dismiss(page), closed

    names, closed = _run(settings, scenario)
    assert names == []
    assert closed == [DEVLOG_NAME]  # la première passe l'a fermée, la seconde ne signale rien de plus


def test_human_click_closes_devlog_before_clicking(settings):
    async def scenario(page, closed):
        await human.click(page, page.locator("#target"), settings)
        return await page.evaluate("window.hits || 0"), closed

    hits, closed = _run(settings, scenario)
    assert hits == 1  # sans la fermeture, la fenêtre aurait reçu le clic
    assert closed == [DEVLOG_NAME]


def test_locator_handler_closes_devlog_that_blocks_a_click(settings):
    async def scenario(page, closed):
        await page.locator("#target").click(timeout=10_000)
        return await page.evaluate("window.hits || 0"), closed

    hits, closed = _run(settings, scenario)
    assert hits == 1
    assert closed == [DEVLOG_NAME]


def test_dismiss_never_raises_on_closed_page(settings):
    async def scenario(page, closed):
        await page.close()
        return await interruptions.dismiss(page)

    assert _run(settings, scenario) == []
