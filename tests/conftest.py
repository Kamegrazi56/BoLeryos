import os
import socket
import threading
import time
from dataclasses import replace

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.mock_game import create_mock_game
from tests.mock_site import create_mock_site

# Chromium déjà présent dans l'environnement de développement ; sinon Playwright utilise le sien.
_DEFAULT_CHROMIUM = "/opt/pw-browsers/chromium"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_url():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_mock_site(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def settings(mock_url):
    chromium = os.environ.get("CHROMIUM_PATH") or (
        _DEFAULT_CHROMIUM if os.path.exists(_DEFAULT_CHROMIUM) else None
    )
    return Settings(
        login_url=mock_url,
        login_timeout=10.0,
        no_sandbox=True,
        chromium_path=chromium,
        screenshot_interval=0.1,
        human_mode=False,  # pas de délais artificiels dans les tests (voir test_human.py)
        data_dir=None,
        min_cycle_minutes=0.01,
        poll_seconds=0.3,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def make_client(settings):
    def factory(**overrides):
        return TestClient(create_app(replace(settings, **overrides)))

    return factory


class GameHandle:
    """Pilote le faux jeu depuis les tests (réinitialisation, actions reçues)."""

    def __init__(self, url: str) -> None:
        self.url = url

    def reset(self, **patch) -> None:
        httpx.post(self.url + "/api/_reset", json=patch, timeout=5).raise_for_status()

    def complete(self) -> None:
        httpx.post(self.url + "/api/_complete", timeout=5).raise_for_status()

    def set_stocks(self, index: int, **stocks) -> None:
        httpx.post(self.url + "/api/_stocks", json={"index": index, "stocks": stocks}, timeout=5).raise_for_status()

    def logout(self) -> None:
        httpx.post(self.url + "/api/_logout", timeout=5).raise_for_status()

    def info(self) -> dict:
        return httpx.get(self.url + "/api/_info", timeout=5).json()

    def actions(self) -> list[dict]:
        return httpx.get(self.url + "/api/_actions", timeout=5).json()


@pytest.fixture(scope="session")
def game_server():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_mock_game(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield GameHandle(f"http://127.0.0.1:{port}")
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def game(game_server):
    game_server.reset()
    return game_server


@pytest.fixture
def game_client(settings, game):
    """Bot déjà « connecté » et placé sur une page du faux jeu."""
    from app.bot import State

    with TestClient(create_app(replace(settings, login_url=game.url + "/game/asterion/buildings"))) as c:
        bot = c.app.state.bot
        c.portal.call(bot.ensure_started)
        bot._set(State.LOGGED_IN, "test")
        yield c


@pytest.fixture
def world_client(settings, game):
    """Bot qui démarre sur la page de connexion d'un faux site complet (connexion → univers → partie)."""
    game.reset(require_auth=True)
    with TestClient(create_app(replace(settings, login_url=game.url + "/", login_timeout=15.0))) as c:
        c.portal.call(c.app.state.bot.ensure_started)
        yield c
