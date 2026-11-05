"""Navigateur complet : vraie fenêtre Chromium sur un écran virtuel (Xvnc), affichée dans la page Écran par noVNC.

Ces tests ont besoin de Xvnc, xrandr et noVNC (installés dans l'image Docker) ; ils sont sautés sinon."""

import asyncio
import gc
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.display import VirtualDisplay
from app.main import create_app
from tests.conftest import _free_port
from tests.helpers import receive_until

NEEDS_VNC = pytest.mark.skipif(
    not (shutil.which("Xvnc") and shutil.which("xrandr") and Path("/usr/share/novnc/core/rfb.js").exists()),
    reason="Xvnc, xrandr ou noVNC absent (présents dans l'image Docker)")


def test_without_vnc_the_screen_uses_images(client):
    assert client.get("/api/display").json()["mode"] == "images"
    with pytest.raises(Exception):  # pas d'écran virtuel : refusé
        with client.websocket_connect("/ws/vnc") as ws:
            ws.receive_bytes()


@NEEDS_VNC
def test_the_virtual_screen_starts_and_follows_the_requested_size():
    async def scenario():
        display = VirtualDisplay(91, 1600, 900, 5991)
        await display.ensure()
        try:
            assert await display.resize(1018, 714) and (display.width, display.height) == (1018, 714)
            assert not await display.resize(1019, 715)  # écart minime : rien à faire
            assert await display.resize(100, 100) and (display.width, display.height) == (640, 400)  # bornes
        finally:
            await display.stop()
        assert not display.running

    asyncio.run(scenario())


@pytest.fixture
def vnc_app(settings):
    app = create_app(replace(settings, vnc=True, vnc_display=92, vnc_port=5992))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield app, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


FIELD_JS = """() => { const r = document.querySelector('input[name=email]').getBoundingClientRect();
    return [r.x + r.width / 2 + window.screenX + (window.outerWidth - window.innerWidth),
            r.y + r.height / 2 + window.screenY + (window.outerHeight - window.innerHeight)]; }"""


def _loop():
    return next(o for o in gc.get_objects() if isinstance(o, asyncio.AbstractEventLoop) and o.is_running())


@NEEDS_VNC
def test_the_screen_page_shows_the_real_browser_window_and_manual_control_reaches_it(vnc_app, settings):
    app, base = vnc_app
    bot = app.state.bot
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=settings.chromium_path, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/ecran")
        assert page.evaluate("fetch('/api/display').then((r) => r.json())")["mode"] == "vnc"
        page.wait_for_selector("#screen canvas", timeout=30000)
        for _ in range(150):
            if bot._page is not None and "/" in bot._page.url and bot._page.url.startswith("http"):
                break
            time.sleep(0.2)
        loop = _loop()
        run = lambda coro: asyncio.run_coroutine_threadsafe(coro, loop).result(15)  # noqa: E731
        # l'écran virtuel épouse la zone d'affichage (pas de bandes vides)
        area = page.evaluate("() => { const s = document.querySelector('#screen'); return [s.clientWidth, s.clientHeight]; }")
        for _ in range(50):
            if abs(bot.display.width - area[0]) <= 4:
                break
            time.sleep(0.2)
        assert abs(bot.display.width - area[0]) <= 4 and abs(bot.display.height - area[1]) <= 4
        time.sleep(1.5)
        field = run(bot._page.evaluate(FIELD_JS))
        canvas = page.locator("#screen canvas").first
        box = canvas.bounding_box()
        size = page.evaluate("() => { const c = document.querySelector('#screen canvas'); return [c.width, c.height]; }")
        target = (box["x"] + field[0] * box["width"] / size[0], box["y"] + field[1] * box["height"] / size[1])

        # la molette fait défiler la page du jeu même sans contrôle manuel (lecture seule)
        run(bot._page.evaluate("() => { document.body.style.height = '6000px'; window.scrollTo(0, 0); }"))
        time.sleep(0.5)
        center = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.move(*center)
        for _ in range(6):
            page.mouse.wheel(0, 120)
            time.sleep(0.1)
        for _ in range(40):
            if run(bot._page.evaluate("() => window.scrollY")) > 0:
                break
            time.sleep(0.1)
        assert run(bot._page.evaluate("() => window.scrollY")) > 0
        assert page.evaluate("() => window.scrollY") == 0  # la page de l'interface, elle, ne bouge pas
        time.sleep(1.0)  # fin du défilement doux déclenché par la molette, puis retour en haut
        run(bot._page.evaluate("() => { document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0); }"))
        time.sleep(0.5)

        # sans contrôle manuel : on regarde seulement, rien n'atteint la page
        page.mouse.click(*target)
        page.keyboard.type("intrus")
        time.sleep(1)
        assert run(bot._page.input_value("input[name=email]")) == ""

        page.check("#manual")
        for _ in range(50):
            if bot._manual:
                break
            time.sleep(0.1)
        time.sleep(0.5)
        focused = "() => document.activeElement && document.activeElement.name === 'email'"
        for _ in range(5):  # machine chargée : on attend que le clic ait bien donné le focus au champ
            field = run(bot._page.evaluate(FIELD_JS))  # position du champ à l'instant du clic
            box = canvas.bounding_box()  # la page a pu défiler : on recalcule la position
            page.mouse.click(box["x"] + field[0] * box["width"] / size[0], box["y"] + field[1] * box["height"] / size[1])
            for _ in range(20):
                if run(bot._page.evaluate(focused)):
                    break
                time.sleep(0.1)
            if run(bot._page.evaluate(focused)):
                break
        page.keyboard.type("joueur@example.com")
        for _ in range(160):
            if run(bot._page.input_value("input[name=email]")) == "joueur@example.com":
                break
            time.sleep(0.05)
        assert run(bot._page.input_value("input[name=email]")) == "joueur@example.com", (
            run(bot._page.evaluate(focused)), page.evaluate("() => rfb && [rfb.viewOnly, rfb._rfbConnectionState]"), bot._manual,
            run(bot._page.evaluate("() => [location.href, document.activeElement && document.activeElement.outerHTML.slice(0, 80)]")))
        assert errors == []
        browser.close()


@NEEDS_VNC
def test_the_vnc_relay_checks_the_origin(vnc_app):
    app, base = vnc_app
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        with pytest.raises(Exception):
            with c.websocket_connect("/ws/vnc", headers={"origin": "https://evil.example"}) as ws:
                ws.receive_bytes()


def test_queued_mouse_moves_never_delay_a_click(client):
    """Mode images : une page occupée (comme le jeu) ne doit pas faire attendre un clic derrière des dizaines de
    mouvements de souris : ils sont fusionnés."""
    heavy = """() => {
      window.__clicks = [];
      document.addEventListener('mousedown', () => window.__clicks.push(performance.now()), true);
      const spin = () => { const t = performance.now(); while (performance.now() - t < 25) {} requestAnimationFrame(spin); };
      requestAnimationFrame(spin);
    }"""
    bot = client.app.state.bot
    with client.websocket_connect("/ws/vision") as ws:
        receive_until(ws, lambda s, f: f is not None)
        ws.send_json({"t": "control", "enabled": True})
        time.sleep(0.5)
        client.portal.call(bot._page.evaluate, heavy)
        start = time.monotonic()
        for i in range(120):
            ws.send_json({"t": "move", "x": 0.3 + i / 1000, "y": 0.5})
        ws.send_json({"t": "down", "button": "left", "x": 0.5, "y": 0.5})
        ws.send_json({"t": "up", "button": "left", "x": 0.5, "y": 0.5})
        while time.monotonic() - start < 10 and not client.portal.call(bot._page.evaluate, "() => window.__clicks.length"):
            time.sleep(0.02)
        assert time.monotonic() - start < 1.5
        assert bot.input_stats["merged"] > 50
