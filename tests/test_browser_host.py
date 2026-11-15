"""Chromium partagé : un seul processus, un contexte par session de bot."""

import asyncio

from app.bot import BotSession
from app.browser_host import BrowserHost


def _run(coro):
    return asyncio.run(coro)


def test_host_starts_one_browser_and_reuses_it(settings):
    async def scenario():
        host = BrowserHost(settings)
        try:
            assert not host.connected
            await host.ensure()
            first = host._browser
            await host.ensure()
            assert host.connected and host._browser is first
        finally:
            await host.shutdown()

    _run(scenario())


def test_contexts_share_the_browser_but_not_their_cookies(settings):
    async def scenario():
        host = BrowserHost(settings)
        try:
            one = await host.new_context()
            two = await host.new_context()
            await one.add_cookies([{"name": "session", "value": "x", "url": settings.login_url}])
            assert host.context_count == 2
            assert await two.cookies() == []  # sessions indépendantes
            assert len(await one.cookies()) == 1
        finally:
            await host.shutdown()

    _run(scenario())


def test_host_relaunches_the_browser_when_it_died(settings):
    async def scenario():
        host = BrowserHost(settings)
        try:
            await host.ensure()
            await host._browser.close()  # Chromium disparu (plantage, fermeture extérieure)
            assert not host.connected
            context = await host.new_context()
            assert host.connected and context.pages == []
        finally:
            await host.shutdown()

    _run(scenario())


def test_close_is_idempotent_and_leaves_the_host_reusable(settings):
    async def scenario():
        host = BrowserHost(settings)
        await host.close()  # jamais démarré : rien à faire
        await host.ensure()
        await host.close()
        await host.close()
        assert not host.connected and host.context_count == 0
        try:
            await host.ensure()
            assert host.connected
        finally:
            await host.shutdown()

    _run(scenario())


def test_a_session_on_a_shared_host_closes_only_its_own_context(settings):
    async def scenario():
        host = BrowserHost(settings)
        first, second = BotSession(settings, host), BotSession(settings, host)
        try:
            await first.ensure_started()
            await second.ensure_started()
            assert host.context_count == 2
            await first._teardown()
            assert first._page is None and first._context is None
            assert host.connected and host.context_count == 1  # le navigateur et l'autre session sont intacts
            assert second._page is not None and not second._page.is_closed()
        finally:
            await first.shutdown()
            await second.shutdown()
            await host.shutdown()

    _run(scenario())


def test_a_session_without_a_host_owns_and_closes_its_browser(settings):
    async def scenario():
        bot = BotSession(settings)
        await bot.ensure_started()
        assert bot.host.connected
        await bot._teardown()
        assert not bot.host.connected
        await bot.ensure_started()  # redémarrage complet comme avant
        assert bot.host.connected and bot._page is not None
        await bot.shutdown()
        assert not bot.host.connected

    _run(scenario())


def test_a_session_recovers_when_the_shared_browser_died(settings):
    async def scenario():
        host = BrowserHost(settings)
        bot = BotSession(settings, host)
        try:
            await bot.ensure_started()
            await host._browser.close()
            assert bot._page.is_closed()
            await bot.ensure_started()  # la session repart sur un nouveau Chromium
            assert host.connected and not bot._page.is_closed()
        finally:
            await bot.shutdown()
            await host.shutdown()

    _run(scenario())
