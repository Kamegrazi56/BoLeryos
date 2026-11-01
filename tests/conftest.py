import os
import socket
import threading
import time
from dataclasses import replace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
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
