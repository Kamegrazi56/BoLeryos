"""Fenêtre « Journal de développement » du jeu : fermée avant chaque action du bot, sans toucher aux autres fenêtres."""

import asyncio

from playwright.async_api import async_playwright

from app import human, interruptions

DEVLOG_NAME = "Journal de développement"

STEAM_NAME = "Annonce Steam"
PACKS_NAME = "Annonce des packs expirés"
PACKS_PAGE = """
<button id="target" onclick="window.hits = (window.hits || 0) + 1">Cible</button>
<div id="packs" role="dialog" aria-label="Vos packs sont arrivés à leur terme"
     style="position:fixed;inset:0;background:#000a;z-index:98">
  <button aria-label="Fermer la transmission" onclick="document.getElementById('packs').remove()">x</button>
  <button id="renew" onclick="window.renewed = true">RÉACTIVER MES PACKS</button>
</div>
"""

STEAM_PAGE = """
<button id="target" onclick="window.hits = (window.hits || 0) + 1">Cible</button>
<div id="steam" role="dialog" aria-label="Votre empire vous accompagne sur Steam"
     style="position:fixed;inset:0;background:#000a;z-index:98">
  <button aria-label="Fermer la transmission" onclick="document.getElementById('steam').remove()">x</button>
</div>
"""

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


def _run(settings, scenario, html: str = PAGE):
    """Lance Chromium sur la page de test et exécute `scenario(page, closed)` ; `closed` liste les fermetures notées."""

    async def main():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=settings.chromium_path, args=["--no-sandbox"])
            try:
                page = await browser.new_page()
                await page.set_content(html)
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


def test_human_click_closes_the_steam_announcement_before_clicking(settings):
    async def scenario(page, closed):
        await human.click(page, page.locator("#target"), settings)
        return await page.evaluate("window.hits || 0"), closed

    hits, closed = _run(settings, scenario, STEAM_PAGE)
    assert hits == 1  # sans la fermeture, l'annonce aurait reçu le clic
    assert closed == [STEAM_NAME]


def test_human_click_closes_the_expired_packs_announcement_without_renewing(settings):
    async def scenario(page, closed):
        await human.click(page, page.locator("#target"), settings)
        return await page.evaluate("[window.hits || 0, !!window.renewed]"), closed

    (hits, renewed), closed = _run(settings, scenario, PACKS_PAGE)
    assert hits == 1 and not renewed  # fermée par la croix : le bouton d'achat n'est jamais touché
    assert closed == [PACKS_NAME]


CATCHUP_NAME = "Rattrapage galactique"
CATCHUP_PAGE = """
<button id="target" onclick="window.hits = (window.hits || 0) + 1">Cible</button>
<div id="catchup" role="dialog" aria-label="Rattrapage galactique"
     style="position:fixed;inset:0;background:#000a;z-index:98">
  <button aria-label="Réduire la fenêtre" onclick="window.reduced = true">-</button>
  <button aria-label="Fermer la transmission" onclick="document.getElementById('catchup').remove()">x</button>
  <button id="launch" onclick="window.launched = true">Lancer le rattrapage</button>
</div>
<div id="confirm" role="dialog" aria-modal="true" aria-label="Confirmer l'action" style="position:fixed;top:0;right:0">
  <button aria-label="Fermer" onclick="document.getElementById('confirm').remove()">x</button>
</div>
"""
STACKED_PAGE = """
<button id="target" onclick="window.hits = (window.hits || 0) + 1">Cible</button>
<div id="a" role="dialog" aria-label="Annonce A" style="position:fixed;inset:0;z-index:97">
  <button aria-label="Fermer la transmission" onclick="document.getElementById('a').remove()">x</button>
</div>
<div id="b" role="dialog" aria-label="Annonce B" style="position:fixed;inset:0;z-index:98">
  <button aria-label="Fermer la transmission" onclick="document.getElementById('b').remove()">x</button>
</div>
"""


def test_unlisted_announcement_is_closed_by_its_cross_only(settings):
    async def scenario(page, closed):
        await human.click(page, page.locator("#target"), settings)
        state = await page.evaluate("[window.hits || 0, !!window.launched, !!window.reduced]")
        return state, closed, await page.locator("#confirm").count()

    (hits, launched, reduced), closed, confirm = _run(settings, scenario, CATCHUP_PAGE)
    assert hits == 1 and not launched and not reduced  # seule la croix est cliquée
    assert closed == [CATCHUP_NAME]
    assert confirm == 1  # une fenêtre sans « Fermer la transmission » reste ouverte


def test_stacked_announcements_are_all_closed(settings):
    async def scenario(page, closed):
        names = await interruptions.dismiss(page)
        return names, await page.locator("[role=dialog]").count()

    names, remaining = _run(settings, scenario, STACKED_PAGE)
    assert sorted(names) == ["Annonce A", "Annonce B"]
    assert remaining == 0


def test_locator_handler_closes_unlisted_announcement_that_blocks_a_click(settings):
    async def scenario(page, closed):
        await page.locator("#target").click(timeout=10_000)
        return await page.evaluate("window.hits || 0"), closed

    hits, closed = _run(settings, scenario, CATCHUP_PAGE)
    assert hits == 1
    assert closed == [CATCHUP_NAME]


# Le clic sur « Modèles » est avalé par une annonce qui surgit juste après lui (vu sur la production, 22/11).
LATE_PACKS_PAGE = """
<button onclick="openModels(this)">Modèles</button>
<script>
function openModels(button) {
  if (!window.shown) {  // 1er clic : l'annonce arrive et le clic est perdu
    window.shown = true;
    document.body.insertAdjacentHTML('beforeend', `
      <div id="packs" role="dialog" aria-label="Vos packs sont arrivés à leur terme"
           style="position:fixed;inset:0;background:#000a;z-index:98">
        <button aria-label="Fermer la transmission" onclick="document.getElementById('packs').remove()">x</button>
      </div>`);
    return;
  }
  document.body.insertAdjacentHTML('beforeend', `
    <div role="dialog" aria-label="Modèles de Flotte"><div><span>Expe</span><span>5 Unités</span>
    <button title="Appliquer le modèle">Appliquer</button><button>Supprimer</button></div></div>`);
}
</script>
"""


def test_models_dialog_is_reopened_when_an_announcement_swallows_the_click(settings):
    from types import SimpleNamespace

    from app.explorer import ExplorationPass

    async def scenario(page, closed):
        bot = SimpleNamespace(_page=page, _settings=settings, supervisor=SimpleNamespace(game_blocker=lambda: None))
        explorer = ExplorationPass.__new__(ExplorationPass)
        explorer.bot = bot
        return await explorer._open_models_dialog(), closed

    rows, closed = _run(settings, scenario, LATE_PACKS_PAGE)
    assert [r["name"] for r in rows] == ["Expe"]
    assert closed == [PACKS_NAME]
