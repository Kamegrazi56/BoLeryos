import base64

GOOD = {"identifier": "joueur.test@example.com", "password": "good-password"}
BAD = {"identifier": "joueur.test@example.com", "password": "mauvais-mdp-secret"}


def test_pages_are_served(client):
    for path in ("/", "/vision", "/actions"):
        res = client.get(path)
        assert res.status_code == 200
        assert res.headers["cache-control"] == "no-store"


def test_initial_status_is_idle(client):
    data = client.get("/api/status").json()
    assert data["state"] == "idle"


def test_login_success(client):
    res = client.post("/api/login", json=GOOD)
    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "logged_in", data
    assert client.get("/api/status").json()["state"] == "logged_in"


def test_login_failure_reports_site_message(client):
    data = client.post("/api/login", json=BAD).json()
    assert data["state"] == "error"
    assert "Identifiants invalides" in data["message"]


def test_credentials_never_leak(client):
    """Ni l'identifiant complet ni le mot de passe ne doivent apparaître dans l'API ou le journal."""
    responses = [
        client.post("/api/login", json=BAD).text,
        client.post("/api/login", json=GOOD).text,
        client.get("/api/status").text,
    ]
    blob = "\n".join(responses)
    for secret in (BAD["password"], GOOD["password"], GOOD["identifier"]):
        assert secret not in blob
    assert "jo***" in blob  # identifiant masqué dans le journal


def test_login_validation(client):
    assert client.post("/api/login", json={"identifier": "", "password": "x"}).status_code == 422
    assert client.post("/api/login", json={"identifier": "a@b.c"}).status_code == 422


def test_logout_closes_session(client):
    client.post("/api/login", json=GOOD)
    data = client.post("/api/logout").json()
    assert data["state"] == "idle"
    assert client.get("/api/status").json()["state"] == "idle"


def test_cross_origin_requests_are_rejected(client):
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/login", json=GOOD, headers=evil).status_code == 403
    assert client.post("/api/logout", headers=evil).status_code == 403


def test_vision_websocket_streams_screenshot_and_logs(client):
    client.post("/api/login", json=GOOD)
    with client.websocket_connect("/ws/vision") as ws:
        msg = ws.receive_json()
    assert msg["state"] == "logged_in"
    assert msg["image"] and base64.b64decode(msg["image"])[:2] == b"\xff\xd8"  # JPEG
    assert any("Connecté" in entry["msg"] for entry in msg["logs"])


def test_ui_basic_auth_when_configured(make_client):
    with make_client(ui_user="admin", ui_password="s3cret") as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", auth=("admin", "mauvais")).status_code == 401
        assert c.get("/api/status", auth=("admin", "s3cret")).status_code == 200
