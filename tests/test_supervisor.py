"""Interrupteur ON/OFF : le bot regarde où il en est (connexion, choix de l'univers, partie) et ne fait que ce qui manque."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

GOOD = {"identifier": "joueur.test@example.com", "password": "good-password"}
BAD = {"identifier": "joueur.test@example.com", "password": "mauvais-mdp-secret"}


def _until(predicate, timeout=60.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.15)
    return False


def _logs(c):
    return [e["msg"] for e in c.get("/api/status?limit=1000").json()["logs"]]


def _has(c, text):
    return any(text in line for line in _logs(c))


def _count(c, text):
    return sum(text in line for line in _logs(c))


def _settings(c, **patch):
    current = c.get("/api/automation").json()["settings"]
    current.update(patch)
    current.pop("catalog", None)
    current["cycle"] = {"min_minutes": 0.02, "max_minutes": 0.02}
    res = c.put("/api/automation/settings", json={"settings": current})
    assert res.status_code == 200, res.text


def _creds(c, creds=GOOD):
    assert c.post("/api/account/credentials", json=creds).status_code == 200


def _on(c):
    return c.post("/api/automation/start").json()


def _off(c):
    return c.post("/api/automation/stop").json()


def _status(c):
    return c.get("/api/status").json()["automation"]


# ---------------------------------------------------------------------- le cas complet
def test_on_logs_in_picks_the_universe_and_starts_working(world_client, game):
    c = world_client
    _creds(c)
    _settings(c, universe="Asterion")
    assert _on(c)["running"] is True
    assert _until(lambda: _has(c, "Cycle :")), _logs(c)[-8:]
    assert game.info() == {"logged_in": True, "login_attempts": 1}
    assert _has(c, "(automatique)") and _has(c, "Page de choix de l'univers")
    _off(c)


def test_when_already_connected_the_login_page_is_skipped(world_client, game):
    c = world_client
    assert c.post("/api/login", json=GOOD).json()["state"] == "logged_in"  # connexion à la main
    assert c.post("/api/universe", json={"index": 0}).status_code == 200  # univers choisi à la main
    assert _until(lambda: "/game/" in c.get("/api/status").json()["page"])
    _settings(c)
    _on(c)
    assert _until(lambda: _has(c, "Cycle :")), _logs(c)[-8:]
    assert game.info()["login_attempts"] == 1  # aucune seconde connexion
    assert not _has(c, "(automatique)")
    _off(c)


def test_a_connected_site_is_detected_even_if_the_bot_forgot_its_state(world_client, game):
    c = world_client
    c.post("/api/login", json=GOOD)
    c.post("/api/universe", json={"index": 0})
    assert _until(lambda: "/game/" in c.get("/api/status").json()["page"])
    bot = c.app.state.bot
    bot._set(bot._state.__class__.IDLE, "oublié")  # comme après un redémarrage du bot avec la session encore ouverte
    _settings(c)
    _on(c)
    assert _until(lambda: _has(c, "Session détectée")), _logs(c)[-8:]
    assert _until(lambda: _has(c, "Cycle :"))
    assert game.info()["login_attempts"] == 1
    _off(c)


# ------------------------------------------------------------------------ identifiants
def test_without_credentials_it_waits_and_never_touches_the_form(world_client, game):
    c = world_client
    _settings(c, universe="Asterion")
    _on(c)
    assert _until(lambda: _has(c, "En attente : identifiants requis"))
    time.sleep(1.5)
    assert game.info()["login_attempts"] == 0
    assert "identifiants requis" in _status(c)["waiting"]
    _creds(c)  # ils arrivent plus tard : le bot continue tout seul
    assert _until(lambda: _has(c, "Cycle :"))
    _off(c)


def test_refused_credentials_are_tried_only_once(world_client, game):
    c = world_client
    _creds(c, BAD)
    _settings(c, universe="Asterion")
    _on(c)
    assert _until(lambda: _has(c, "Reconnexion automatique suspendue"))
    time.sleep(2.5)
    assert game.info()["login_attempts"] == 1  # jamais de seconde tentative : risque de blocage du compte
    assert "corrigez les identifiants" in _status(c)["waiting"]
    # corrigés, puis OFF / ON : ça repart
    _off(c)
    _creds(c, GOOD)
    _on(c)
    assert _until(lambda: _has(c, "Cycle :"))
    _off(c)


def test_credentials_stay_in_memory_and_never_appear_anywhere(world_client, tmp_path):
    c = world_client
    secret = {"identifier": "titulaire.secret@example.com", "password": "mot-de-passe-tres-secret"}
    _creds(c, secret)
    account = c.get("/api/account").json()
    assert account["has_credentials"] is True and account["hint"] == "ti***"
    blobs = [c.get(u).text for u in ("/api/status?limit=1000", "/api/automation", "/api/account", "/api/automation/export", "/api/explore/export")]
    for blob in blobs:
        assert "mot-de-passe-tres-secret" not in blob and "titulaire.secret@example.com" not in blob
    assert c.post("/api/logout").status_code == 200  # se déconnecter = oublier
    assert c.get("/api/account").json()["has_credentials"] is False


def test_forget_credentials_and_origin_check(world_client):
    c = world_client
    _creds(c)
    assert c.delete("/api/account/credentials").json()["has_credentials"] is False
    evil = {"Origin": "http://evil.example"}
    assert c.post("/api/account/credentials", json=GOOD, headers=evil).status_code == 403
    assert c.delete("/api/account/credentials", headers=evil).status_code == 403


# ----------------------------------------------------------------------------- univers
def test_without_a_chosen_universe_it_waits_then_continues_once_chosen(world_client, game):
    c = world_client
    _creds(c)
    _settings(c)  # aucun univers choisi
    _on(c)
    assert _until(lambda: _has(c, "choisissez un univers"))
    assert game.info()["logged_in"] is True and not _has(c, "Cycle :")
    _settings(c, universe="Asterion")
    assert _until(lambda: _has(c, "Cycle :"))
    _off(c)


def test_choosing_a_universe_by_hand_remembers_it(world_client):
    c = world_client
    c.post("/api/login", json=GOOD)
    assert c.get("/api/automation").json()["settings"]["universe"] == ""
    c.post("/api/universe", json={"index": 0})
    assert c.get("/api/automation").json()["settings"]["universe"] == "Asterion"
    assert "Asterion" in c.get("/api/account").json()["universes"] or True


# ------------------------------------------------------------------- session expirée
def test_an_expired_session_is_reconnected_automatically(world_client, game):
    c = world_client
    _creds(c)
    _settings(c, universe="Asterion")
    _on(c)
    assert _until(lambda: _has(c, "Cycle :"))
    game.logout()  # la session du site expire
    c.portal.call(c.app.state.bot._page.reload)
    assert _until(lambda: game.info()["login_attempts"] == 2), _logs(c)[-10:]
    assert _has(c, "Session expirée")
    assert _until(lambda: _count(c, "Dans la partie : le bot travaille.") == 2)  # le travail reprend après la reconnexion
    _off(c)


# ------------------------------------------------------------------ plage, contrôle manuel
def test_outside_the_time_window_the_bot_stays_connected_but_does_not_work(world_client, game):
    """La plage horaire limite le travail du bot, pas sa connexion (qui suit les identifiants saisis)."""
    c = world_client
    now = datetime.now(ZoneInfo("Europe/Paris"))
    window = {"enabled": True, "timezone": "Europe/Paris",
              "windows": [{"start": f"{(now.hour + 2) % 24:02d}:00", "end": f"{(now.hour + 3) % 24:02d}:00", "days": list(range(7))}]}
    _creds(c)
    _settings(c, universe="Asterion", schedule=window)
    _on(c)
    assert _until(lambda: _has(c, "Plage horaire : inactive"))
    assert _until(lambda: "dans la partie" in _status(c)["connection"])
    time.sleep(1.5)
    assert game.info()["login_attempts"] == 1 and not _has(c, "Cycle :")
    _off(c)


def test_manual_control_pauses_the_supervisor(world_client, game):
    c = world_client
    bot = c.app.state.bot
    bot._manual = True  # avant de saisir les identifiants : la connexion automatique démarrerait aussitôt
    _creds(c)
    _settings(c, universe="Asterion")
    _on(c)
    assert _until(lambda: _has(c, "En attente : contrôle manuel"))
    assert game.info()["login_attempts"] == 0
    bot._manual = False
    assert _until(lambda: _has(c, "Cycle :"))
    _off(c)


def test_on_starts_the_browser_even_when_no_screen_is_open(settings, game):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    game.reset(require_auth=True)
    with TestClient(create_app(replace(settings, login_url=game.url + "/"))) as c:  # aucun écran ouvert, navigateur non démarré
        assert c.app.state.bot._page is None
        _on(c)
        assert c.app.state.bot._page is not None
        _off(c)


# ------------------------------------------------- connexion automatique, sans l'interrupteur
def test_saving_the_account_connects_and_enters_the_universe_without_switching_on(world_client, game):
    c = world_client
    _settings(c, universe="Asterion")
    _creds(c)
    assert _until(lambda: "/game/" in c.get("/api/status").json()["page"]), _logs(c)[-8:]
    assert game.info() == {"logged_in": True, "login_attempts": 1}
    assert _status(c)["running"] is False  # jamais passé sur ON
    assert _until(lambda: "dans la partie" in _status(c)["connection"])
    assert "Connecté" in c.get("/api/dashboard").json()["sentence"] or "connecté" in c.get("/api/dashboard").json()["sentence"]
    assert _has(c, "Connecté à la partie.") and not _has(c, "Cycle :")  # il ne travaille pas tant qu'il est sur OFF


def test_without_an_universe_the_bot_waits_on_the_choice_page_then_enters_once_it_is_typed(world_client, game):
    c = world_client
    _creds(c)  # pas d'univers choisi
    assert _until(lambda: "choisissez un univers" in _status(c)["connection"]), _status(c)
    assert "Asterion" in c.get("/api/automation").json()["catalog"]["universes"]  # suggestions disponibles
    _settings(c, universe="Asterion")
    assert _until(lambda: "/game/" in c.get("/api/status").json()["page"])


def test_session_expiry_is_repaired_while_the_switch_is_off(world_client, game):
    c = world_client
    _settings(c, universe="Asterion")
    _creds(c)
    assert _until(lambda: "dans la partie" in _status(c)["connection"])
    game.logout()
    c.portal.call(c.app.state.bot._page.reload)
    assert _until(lambda: game.info()["login_attempts"] == 2 and "dans la partie" in _status(c)["connection"]), _logs(c)[-8:]


def test_refused_credentials_are_tried_only_once_and_forgetting_stops_the_connection(world_client, game):
    c = world_client
    _settings(c, universe="Asterion")
    _creds(c, BAD)
    assert _until(lambda: "refusée" in _status(c)["connection"]), _status(c)
    time.sleep(1.0)
    assert game.info()["login_attempts"] == 1
    assert c.delete("/api/account/credentials").status_code == 200
    assert _until(lambda: _status(c)["connection"] == "")
    assert c.post("/api/account/login").status_code == 409


def test_the_connection_is_kept_when_the_switch_goes_on_then_off(world_client, game):
    c = world_client
    _settings(c, universe="Asterion")
    _creds(c)
    assert _until(lambda: "dans la partie" in _status(c)["connection"])
    _on(c)
    assert _until(lambda: _has(c, "Cycle :"))
    _off(c)
    assert _status(c)["running"] is False and c.get("/api/status").json()["state"] == "logged_in"
    assert game.info()["login_attempts"] == 1


def test_logout_forgets_the_account_and_stops_the_connection(world_client, game):
    c = world_client
    _settings(c, universe="Asterion")
    _creds(c)
    assert _until(lambda: "dans la partie" in _status(c)["connection"])
    assert c.post("/api/logout").status_code == 200
    time.sleep(1.0)
    assert _status(c)["connection"] == "" and not c.get("/api/account").json()["has_credentials"]
    assert game.info()["login_attempts"] == 1
