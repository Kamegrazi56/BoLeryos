"""Écran « Récupération interrompue » : repéré sur la page du bot, et « Réessayer » cliqué."""

SCREEN = """<main><div role="dialog"><h2>Récupération interrompue</h2>
<p>Impossible de restaurer l'univers actif.</p>
<button type="button" onclick="document.querySelector('[role=dialog]').remove()">Réessayer</button>
<button type="button">Choisir un serveur</button></div></main>"""


def _bot(client):
    return client.app.state.bot


def test_the_recovery_screen_is_seen_and_retry_clears_it(game_client):
    bot = _bot(game_client)
    assert game_client.portal.call(bot.recovery_screen) is False  # page normale du jeu

    async def show():
        await bot._page.set_content(SCREEN)

    game_client.portal.call(show)
    assert game_client.portal.call(bot.recovery_screen) is True
    assert game_client.portal.call(bot.retry_recovery) is True
    assert game_client.portal.call(bot.recovery_screen) is False
    assert game_client.portal.call(bot.retry_recovery) is False  # plus de bouton : rien à cliquer
