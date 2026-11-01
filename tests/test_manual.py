"""Contrôle manuel : clics, clavier et taille d'affichage envoyés depuis l'interface."""

import time

from tests.helpers import receive_until

EMAIL = 'input[name="email"]'


def _page(client):
    return client.app.state.bot._page


def _until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _email_value(client):
    return client.portal.call(_page(client).input_value, EMAIL)


def _email_center(client):
    """Centre du champ e-mail du faux site, en fraction de la page (comme le calcule l'interface)."""
    page = _page(client)
    box = client.portal.call(page.locator(EMAIL).bounding_box)
    size = page.viewport_size
    return (box["x"] + box["width"] / 2) / size["width"], (box["y"] + box["height"] / 2) / size["height"]


def _wait_home(ws):
    receive_until(ws, lambda s, f: f is not None)


def _click(ws, x, y):
    ws.send_json({"t": "down", "button": "left", "x": x, "y": y})
    ws.send_json({"t": "up", "button": "left", "x": x, "y": y})


def test_manual_control_is_off_by_default_and_input_is_ignored(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        assert client.get("/api/status").json()["manual"] is False
        x, y = _email_center(client)
        _click(ws, x, y)
        ws.send_json({"t": "type", "text": "abc"})
        time.sleep(0.5)
        assert _email_value(client) == ""


def test_manual_click_and_typing_reach_the_bot_browser(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        ws.send_json({"t": "control", "enabled": True})
        assert _until(lambda: client.get("/api/status").json()["manual"])
        x, y = _email_center(client)
        _click(ws, x, y)
        for char in "abc":
            ws.send_json({"t": "type", "text": char})
        ws.send_json({"t": "press", "key": "Backspace"})
        assert _until(lambda: _email_value(client) == "ab"), _email_value(client)


def test_automatic_login_is_refused_while_manual_control_is_on(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        ws.send_json({"t": "control", "enabled": True})
        assert _until(lambda: client.get("/api/status").json()["manual"])
        res = client.post("/api/login", json={"identifier": "a@b.c", "password": "good-password"}).json()
        assert res["state"] == "idle"
        assert "contrôle manuel" in res["message"]


def test_manual_control_is_released_when_the_last_screen_closes(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        ws.send_json({"t": "control", "enabled": True})
        assert _until(lambda: client.get("/api/status").json()["manual"])
    assert _until(lambda: client.get("/api/status").json()["manual"] is False)


def test_invalid_or_dangerous_messages_are_ignored(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        ws.send_json({"t": "control", "enabled": True})
        assert _until(lambda: client.get("/api/status").json()["manual"])
        x, y = _email_center(client)
        _click(ws, x, y)
        ws.send_text("pas du json")
        ws.send_text("x" * 5000)
        ws.send_json({"t": "press", "key": "Control+Alt+Delete; rm -rf /"})
        ws.send_json({"t": "down", "button": "bizarre", "x": x, "y": y})
        ws.send_json({"t": "move", "x": "abc", "y": None})
        ws.send_json({"t": "inconnu"})
        ws.send_json({"t": "type", "text": "ok"})
        assert _until(lambda: _email_value(client) == "ok"), _email_value(client)
        assert client.get("/api/status").status_code == 200  # le serveur répond toujours


def test_typed_text_is_never_written_to_the_log(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        ws.send_json({"t": "control", "enabled": True})
        assert _until(lambda: client.get("/api/status").json()["manual"])
        x, y = _email_center(client)
        _click(ws, x, y)
        ws.send_json({"t": "type", "text": "motdepasse-secret"})
        assert _until(lambda: _email_value(client) == "motdepasse-secret")
    logs = client.get("/api/status").text
    assert "motdepasse-secret" not in logs
    assert "Saisie manuelle" in logs


def test_viewport_follows_the_display_area(client):
    with client.websocket_connect("/ws/vision") as ws:
        _wait_home(ws)
        ws.send_json({"t": "viewport", "width": 1800, "height": 900})  # format 2:1
        assert _until(lambda: _page(client).viewport_size["height"] == 720)
        assert _page(client).viewport_size["width"] == 1440
        ws.send_json({"t": "viewport", "width": 100, "height": 100000})  # hauteur plafonnée
        assert _until(lambda: _page(client).viewport_size["height"] == 1100)


def test_viewport_sent_before_the_browser_starts_is_remembered(settings):
    """Au chargement de la page, le format arrive avant le démarrage du navigateur."""
    import asyncio

    from app.bot import BotSession

    bot = BotSession(settings)
    asyncio.run(bot.handle_input({"t": "viewport", "width": 1800, "height": 900}))
    assert bot._viewport == {"width": 1440, "height": 720}
