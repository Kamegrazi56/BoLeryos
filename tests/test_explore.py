"""Exploration de la structure du site et choix de l'univers."""

import json
import time

from app.explore import Recorder, mask_url, shape

SECRETS = [
    "SECRETTOKEN12345", "SECRETBEARER999", "COOKIEVALUE123456", "eyJhbGciOi", "c2lnbmF0dXJl",
    "joueur.test@example.com", "3f2b8c1e-1111", "secret.jwt.value", "abc.def.ghi", "good-password",
]
GOOD = {"identifier": "joueur.test@example.com", "password": "good-password"}


def _until(predicate, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


# ------------------------------------------------------------------ masquage (sans navigateur)
def test_mask_url_keeps_parameter_names_but_never_values_or_ids():
    url = "https://api.example.com/users/3f2b8c1e-1111-2222-3333-444455556666/planets/12345678?token=SECRET&page=2"
    masked = mask_url(url)
    assert masked == "api.example.com/users/{id}/planets/{id}?page&token"
    assert "SECRET" not in masked


def test_shape_keeps_keys_and_types_but_hides_values():
    value = {
        "email": "joueur.test@example.com",
        "token": "eyJhbGciOi.eyJzdWIi.c2lnbmF0dXJl",
        "state": "open",
        "count": 42,
        "ok": True,
        "items": [{"name": "Terra"}, {"name": "Autre"}],
        "3f2b8c1e-1111-2222-3333-444455556666": {"x": 1},
    }
    result = shape(value)
    assert result["email"] == "<string>" and result["token"] == "<string>"
    assert result["state"] == "open"  # court libellé conservé : utile pour repérer les états
    assert result["count"] == "<number>" and result["ok"] == "<bool>"
    assert result["items"] == {"<array>": 2, "item": {"name": "Terra"}}
    assert "<id>" in result
    assert "3f2b8c1e" not in json.dumps(result)


def test_identifiers_inside_storage_and_cookie_names_are_masked():
    user, universe = "6f85f193-7286-4f32-b390-5ec46c6393ab", "a0000000-0000-0000-0000-000000000002"
    rec = Recorder()
    data = rec.export(
        [{"name": f"sb-{user}-token", "domain": "x.com"}],
        {"localStorage": [f"aleryos:vote-ready:v1:{universe}:{user}", "aleryos.locale"], "sessionStorage": []},
    )
    raw = json.dumps(data)
    assert user not in raw and universe not in raw
    assert "aleryos:vote-ready:v1:{id}:{id}" in raw and "aleryos.locale" in raw


def test_recorder_is_bounded():
    rec = Recorder()
    for i in range(1000):
        rec.add_request("GET", f"https://api.example.com/route{i}", 200, "fetch", [], None, "application/json", None)
    assert len(rec.requests) <= 400


# -------------------------------------------------------------- avec le navigateur (faux site)
def _bot(client):
    return client.app.state.bot


def _reload(client):
    page = _bot(client)._page
    client.portal.call(page.reload)
    time.sleep(1.5)


def _start(client):
    with client.websocket_connect("/ws/vision") as ws:
        from tests.helpers import receive_until

        receive_until(ws, lambda s, f: f is not None and any("Page d'accueil" in e["msg"] for e in s["logs"]))
    return client


def test_recording_is_off_by_default(client):
    _start(client)
    _reload(client)
    data = client.get("/api/explore/export").json()
    assert data["requests"] == [] and data["pages"] == [] and data["websockets"] == []


def test_recorded_structure_never_contains_secrets(client):
    _start(client)
    assert client.post("/api/explore/recording", json={"enabled": True}).json()["explore"]["recording"] is True
    _reload(client)
    assert client.post("/api/explore/snapshot").json()["ok"] is True

    res = client.get("/api/explore/export")
    assert res.headers["content-disposition"].startswith("attachment")
    data = res.json()
    raw = res.text

    # La structure utile est bien là…
    api = next(r for r in data["requests"] if r["url"].endswith("/api/me"))
    assert api["method"] == "GET" and api["statuses"] == [200]
    assert sorted(api["query_params"]) == ["page", "token"]
    assert "authorization" in api["request_headers"]  # le NOM de l'en-tête, pas sa valeur
    assert api["response_shape"]["email"] == "<string>"
    assert api["response_shape"]["state"] == "open"
    assert api["response_shape"]["planets"]["item"]["id"] == "<string>"
    assert any(c["name"] == "session" and c["httpOnly"] for c in data["cookies"])  # nom du cookie, pas sa valeur
    sock = data["websockets"][0]
    assert sock["url"].endswith("/ws")
    assert sock["sent"][0]["frame"]["shape"] == {"type": "hello", "token": "<string>", "n": "<number>"}
    assert sock["received"][0]["frame"]["shape"]["type"] == "tick"
    page = data["pages"][0]
    assert any(e.get("name") == "password" and e.get("type") == "password" for e in page["elements"])
    assert not any("value" in e for e in page["elements"])
    assert any(t["text"] == "Se connecter" for t in page["texts"])  # textes visibles : niveaux, coûts, durées…

    # …et aucun secret nulle part dans le fichier.
    for secret in SECRETS:
        assert secret not in raw, f"fuite : {secret}"


def test_typed_values_are_not_recorded(client):
    _start(client)
    client.post("/api/explore/recording", json={"enabled": True})
    page = _bot(client)._page
    client.portal.call(page.fill, 'input[name="email"]', "saisie-secrete@example.com")
    client.portal.call(page.fill, 'input[name="password"]', "mot-de-passe-secret")
    assert client.post("/api/explore/snapshot").json()["ok"] is True
    raw = client.get("/api/explore/export").text
    assert "saisie-secrete" not in raw and "mot-de-passe-secret" not in raw
    assert "example.com" not in raw  # aucune adresse e-mail, même partielle


def test_clear_empties_the_recording(client):
    _start(client)
    client.post("/api/explore/recording", json={"enabled": True})
    client.post("/api/explore/snapshot")
    assert client.get("/api/status").json()["explore"]["snapshots"] == 1
    client.post("/api/explore/clear")
    assert client.get("/api/status").json()["explore"]["snapshots"] == 0


def test_explore_requests_from_other_sites_are_rejected(client):
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/explore/recording", json={"enabled": True}, headers=evil).status_code == 403
    assert client.post("/api/explore/snapshot", headers=evil).status_code == 403
    assert client.post("/api/universe", json={"index": 0}, headers=evil).status_code == 403


# -------------------------------------------------------------------- choix de l'univers
def test_universes_are_listed_and_entered_after_login(client, mock_url):
    assert client.get("/api/universes").json() == []  # pas connecté
    assert client.post("/api/login", json=GOOD).json()["state"] == "logged_in"
    page = _bot(client)._page
    client.portal.call(page.goto, mock_url + "servers")
    # Seul Asterion est proposé : pas Kael (verrouillé, bouton « Ajouter à mon agenda »), et le texte masqué
    # « Compte existant — Reprendre la partie » des deux cartes n'est pas pris pour un bouton.
    assert _until(lambda: client.get("/api/universes").json() == [{"index": 0, "name": "Asterion"}])
    client.post("/api/universe", json={"index": 0})
    assert _until(lambda: "/servers/asterion" in client.get("/api/status").json()["page"])
    assert any("Choix de l'univers : Asterion" in e["msg"] for e in client.get("/api/status").json()["logs"])


def test_universe_choice_is_refused_in_manual_mode(client, mock_url):
    client.post("/api/login", json=GOOD)
    client.portal.call(_bot(client)._page.goto, mock_url + "servers")
    with client.websocket_connect("/ws/vision") as ws:
        ws.send_json({"t": "control", "enabled": True})
        assert _until(lambda: client.get("/api/status").json()["manual"])
        res = client.post("/api/universe", json={"index": 0}).json()
        assert "contrôle manuel" in res["message"]
