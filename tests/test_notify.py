"""Alertes : adresse https choisie par le joueur, jamais exposée, adresses locales refusées, envoi vers un serveur de test."""

import json
import os
import stat
import threading
import time
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.notify import NotifyError, Notifier, check_url
from tests.test_automation import MINES, _configure, _cycle, _runner, _until

SECRET = "TOKEN-TRES-SECRET-987"


class Receiver:
    """Petit serveur HTTP qui garde les messages reçus."""

    def __init__(self) -> None:
        self.messages: list[tuple[dict, bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.messages.append((dict(self.headers), body))
                self.send_response(outer.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.status = 204
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook/{SECRET}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.close()


# ---------------------------------------------------------------------------- adresses
@pytest.mark.parametrize("url", [
    "http://example.com/hook", "ftp://example.com/x", "https://127.0.0.1/x", "https://localhost/x", "https://10.0.0.5/x",
    "https://192.168.1.10/x", "https://169.254.169.254/latest", "https://[::1]/x", "https://0.0.0.0/x",
    "https://user:pass@93.184.216.34/x", "https:///x", "https://" + "a" * 600 + ".com/x", "",
    "https://100.64.1.1/x", "https://[::ffff:127.0.0.1]/x",  # réseau partagé d'opérateur / VPN ; IPv4 dans IPv6
])
def test_unsafe_addresses_are_refused(url):
    with pytest.raises(NotifyError):
        check_url(url)


def test_a_public_https_address_is_accepted_and_private_ones_only_in_tests():
    assert check_url(" https://93.184.216.34/hook ") == "https://93.184.216.34/hook"
    assert check_url("http://127.0.0.1:8080/x", allow_private=True) == "http://127.0.0.1:8080/x"


def test_a_dns_failure_at_startup_does_not_lose_the_saved_address(tmp_path, monkeypatch):
    """Réseau pas encore prêt au redémarrage : l'adresse enregistrée est gardée, et n'est pas effacée du fichier."""
    import app.notify as notify_module

    (tmp_path / "notifications.json").write_text(json.dumps({"url": f"https://example.com/{SECRET}", "format": "json",
                                                             "events": {}}), encoding="utf8")

    def no_dns(host):
        raise OSError("dns")

    monkeypatch.setattr(notify_module, "_addresses", no_dns)
    n = Notifier(str(tmp_path))
    assert n.configured
    n.configure(url=None, fmt="json", events={"launch": False})  # l'utilisateur change seulement une case
    assert json.loads((tmp_path / "notifications.json").read_text(encoding="utf8"))["url"].endswith(SECRET)


# ------------------------------------------------------------------------- configuration
def test_config_never_exposes_the_full_address(tmp_path):
    n = Notifier(str(tmp_path), allow_private=True)
    n.configure(url=f"http://127.0.0.1:9/hook/{SECRET}", fmt="json", events={"launch": True, "attention": False})
    public = json.dumps(n.public_config())
    assert SECRET not in public and "127.0.0.1" in public and n.wants("launch") and not n.wants("attention")
    assert n.public_config()["host"] == "127.0.0.1"


def test_address_is_saved_apart_with_restricted_rights_and_survives_a_restart(tmp_path):
    n = Notifier(str(tmp_path), allow_private=True)
    n.configure(url=f"http://127.0.0.1:9/hook/{SECRET}", fmt="text", events={"launch": True, "attention": True})
    path = tmp_path / "notifications.json"
    if os.name != "nt":  # Windows ne gère pas les droits Unix ; le bot tourne sous Linux (Docker), où c'est vérifié
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    again = Notifier(str(tmp_path), allow_private=True)
    assert again.configured and again.public_config()["format"] == "text" and again.wants("attention")
    again.clear()
    assert not Notifier(str(tmp_path), allow_private=True).configured


def test_none_keeps_the_address_and_empty_string_removes_it(tmp_path):
    n = Notifier(str(tmp_path), allow_private=True)
    n.configure(url="http://127.0.0.1:9/a", fmt="json", events={})
    n.configure(url=None, fmt="text", events={"launch": False})
    assert n.configured and n.public_config()["format"] == "text" and not n.wants("launch")
    n.configure(url="", fmt="json", events={})
    assert not n.configured


def test_unknown_format_is_refused(tmp_path):
    with pytest.raises(NotifyError):
        Notifier(str(tmp_path), allow_private=True).configure(url=None, fmt="xml", events={})


# ------------------------------------------------------------------------------------ API
@pytest.fixture
def alert_client(settings, game):
    from app.bot import State

    with TestClient(create_app(replace(settings, login_url=game.url + "/game/asterion/buildings", allow_private_webhooks=True))) as c:
        bot = c.app.state.bot
        c.portal.call(bot.ensure_started)
        bot._set(State.LOGGED_IN, "test")
        yield c


def test_api_refuses_unsafe_addresses_by_default(game_client):
    res = game_client.put("/api/notifications", json={"url": "https://127.0.0.1/hook", "format": "json", "events": {}})
    assert res.status_code == 422 and "réseau local" in res.json()["detail"]
    res = game_client.put("/api/notifications", json={"url": "http://example.com/hook", "format": "json", "events": {}})
    assert res.status_code == 422 and "https" in res.json()["detail"]
    assert game_client.put("/api/notifications", json={"url": "https://93.184.216.34/x", "format": "xml", "events": {}}).status_code == 422
    assert game_client.get("/api/notifications").json()["configured"] is False


def test_the_address_appears_nowhere_in_the_api_the_export_or_the_log(alert_client, receiver):
    res = alert_client.put("/api/notifications", json={"url": receiver.url, "format": "json", "events": {"launch": True, "attention": True}})
    assert res.status_code == 200 and SECRET not in res.text
    blob = "\n".join([
        alert_client.get("/api/notifications").text, alert_client.get("/api/status?limit=1000").text,
        alert_client.get("/api/automation").text, alert_client.get("/api/automation/export").text,
        alert_client.get("/api/dashboard").text, alert_client.get("/api/events").text,
        alert_client.get("/api/account").text,
    ])
    assert SECRET not in blob


def test_test_message_is_sent_to_the_chosen_address(alert_client, receiver):
    assert alert_client.post("/api/notifications/test").status_code == 409  # rien de configuré
    alert_client.put("/api/notifications", json={"url": receiver.url, "format": "json", "events": {}})
    res = alert_client.post("/api/notifications/test")
    assert res.status_code == 200 and res.json()["ok"] is True and SECRET not in res.text
    headers, body = receiver.messages[-1]
    assert json.loads(body)["content"].startswith("BoLeryos : message de test")


def test_text_format_for_ntfy_and_failures_are_reported_not_raised(alert_client, receiver):
    alert_client.put("/api/notifications", json={"url": receiver.url, "format": "text", "events": {}})
    assert alert_client.post("/api/notifications/test").json()["ok"] is True
    headers, body = receiver.messages[-1]
    assert body.decode().startswith("BoLeryos") and headers["Content-Type"].startswith("text/plain")
    receiver.status = 500
    res = alert_client.post("/api/notifications/test").json()
    assert res["ok"] is False and "500" in res["last_error"] and SECRET not in json.dumps(res)


def test_unreachable_address_does_not_break_the_bot(alert_client):
    alert_client.put("/api/notifications", json={"url": "http://127.0.0.1:9/nope", "format": "json", "events": {"launch": True}})
    res = alert_client.post("/api/notifications/test").json()
    assert res["ok"] is False and "envoi impossible" in res["last_error"]


def test_delete_removes_the_address(alert_client, receiver):
    alert_client.put("/api/notifications", json={"url": receiver.url, "format": "json", "events": {}})
    assert alert_client.delete("/api/notifications").json()["configured"] is False
    assert alert_client.post("/api/notifications/test").status_code == 409


def test_a_launch_sends_an_alert_but_only_if_that_kind_is_wanted(alert_client, receiver, game):
    alert_client.put("/api/notifications", json={"url": receiver.url, "format": "json", "events": {"launch": True, "attention": False}})
    _configure(alert_client, mode="live", constructions=MINES)
    _cycle(alert_client)
    assert _until(lambda: receiver.messages)
    sent = [json.loads(body)["content"] for _, body in receiver.messages]
    assert any("Mine de Fer lancée (niveau 40 → 41)" in m for m in sent)
    assert not any("Identifiants" in m or "password" in m.lower() for m in sent)
    # arrêt automatique : « attention » non demandé → aucun message supplémentaire
    count = len(receiver.messages)
    runner = _runner(alert_client)
    runner._running = True
    alert_client.portal.call(partial(runner.stop, "problème", automatic=True))
    time.sleep(0.5)
    assert len(receiver.messages) == count
    alert_client.put("/api/notifications", json={"url": None, "format": "json", "events": {"attention": True}})
    runner._running = True
    alert_client.portal.call(partial(runner.stop, "autre problème", automatic=True))
    assert _until(lambda: len(receiver.messages) > count)
    assert "autre problème" in json.loads(receiver.messages[-1][1])["content"]
