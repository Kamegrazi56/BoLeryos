"""Connexion automatique : dès que les identifiants sont connus, le bot se connecte, entre dans l'univers choisi,
reste connecté et scanne les planètes. Il ne lance aucune action de jeu."""

import time

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


def _universe(c, name):
    res = c.put("/api/preferences", json={"universe": name})
    assert res.status_code == 200, res.text


def _creds(c, creds=GOOD):
    assert c.post("/api/account/credentials", json=creds).status_code == 200


def _connection(c):
    return c.get("/api/status").json()["supervisor"]["connection"]


def _in_game(c):
    return "/game/" in c.get("/api/status").json()["page"]


def test_saving_the_account_connects_and_enters_the_universe(world_client, game):
    c = world_client
    _universe(c, "Asterion")
    _creds(c)
    assert _until(lambda: _in_game(c)), _logs(c)[-8:]
    assert game.info() == {"logged_in": True, "login_attempts": 1}
    assert _until(lambda: "dans la partie" in _connection(c))
    assert _has(c, "Connecté à la partie.")


def test_when_already_connected_the_login_page_is_skipped(world_client, game):
    c = world_client
    assert c.post("/api/login", json=GOOD).json()["state"] == "logged_in"  # connexion à la main
    assert c.post("/api/universe", json={"index": 0}).status_code == 200
    assert _until(lambda: _in_game(c))
    _creds(c)
    assert _until(lambda: "dans la partie" in _connection(c))
    assert game.info()["login_attempts"] == 1  # aucune seconde connexion


def test_a_connected_site_is_detected_even_if_the_bot_forgot_its_state(world_client, game):
    c = world_client
    c.post("/api/login", json=GOOD)
    c.post("/api/universe", json={"index": 0})
    assert _until(lambda: _in_game(c))
    bot = c.app.state.bot
    bot._set(bot._state.__class__.IDLE, "oublié")  # comme après un redémarrage du bot avec la session encore ouverte
    _creds(c)
    assert _until(lambda: "dans la partie" in _connection(c)), _logs(c)[-8:]
    assert game.info()["login_attempts"] == 1


def test_without_credentials_it_never_touches_the_form(world_client, game):
    c = world_client
    _universe(c, "Asterion")
    time.sleep(1.5)
    assert game.info()["login_attempts"] == 0
    assert c.get("/api/status").json()["supervisor"]["has_credentials"] is False


def test_refused_credentials_are_tried_only_once_and_forgetting_stops_the_connection(world_client, game):
    c = world_client
    _universe(c, "Asterion")
    _creds(c, BAD)
    assert _until(lambda: "refusée" in _connection(c)), _connection(c)
    time.sleep(1.0)
    assert game.info()["login_attempts"] == 1  # jamais de seconde tentative : risque de blocage du compte
    assert c.delete("/api/account/credentials").status_code == 200
    assert _until(lambda: _connection(c) == "")
    assert c.post("/api/account/login").status_code == 409


def test_credentials_stay_in_memory_and_never_appear_anywhere(world_client):
    c = world_client
    secret = {"identifier": "titulaire.secret@example.com", "password": "mot-de-passe-tres-secret"}
    _creds(c, secret)
    account = c.get("/api/account").json()
    assert account["has_credentials"] is True and account["hint"] == "ti***"
    blobs = [c.get(u).text for u in ("/api/status?limit=1000", "/api/preferences", "/api/account", "/api/planets", "/api/explore/export")]
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


def test_without_a_chosen_universe_it_waits_then_continues_once_chosen(world_client, game):
    c = world_client
    _creds(c)  # aucun univers choisi
    assert _until(lambda: "choisissez un univers" in _connection(c)), _connection(c)
    assert game.info()["logged_in"] is True and not _in_game(c)
    assert "Asterion" in c.get("/api/preferences").json()["preferences"]["catalog"]["universes"]  # suggestions
    _universe(c, "Asterion")
    assert _until(lambda: _in_game(c))


def test_choosing_a_universe_by_hand_remembers_it(world_client):
    c = world_client
    c.post("/api/login", json=GOOD)
    assert c.get("/api/preferences").json()["preferences"]["universe"] == ""
    c.post("/api/universe", json={"index": 0})
    assert c.get("/api/preferences").json()["preferences"]["universe"] == "Asterion"


def test_an_expired_session_is_reconnected_automatically(world_client, game):
    c = world_client
    _universe(c, "Asterion")
    _creds(c)
    assert _until(lambda: "dans la partie" in _connection(c))
    game.logout()  # la session du site expire
    c.portal.call(c.app.state.bot._page.reload)
    assert _until(lambda: game.info()["login_attempts"] == 2 and "dans la partie" in _connection(c)), _logs(c)[-8:]


def _page_url(c):
    return c.app.state.bot._page.url


def test_outside_the_planning_the_game_page_is_closed_but_the_session_is_kept(world_client, game):
    """Fin de plage = onglet fermé (pas de « Déconnexion ») ; début de plage = jeu rouvert sans nouvelle connexion."""
    c = world_client
    bot = c.app.state.bot
    _universe(c, "Asterion")
    _creds(c)
    assert _until(lambda: "dans la partie" in _connection(c)), _logs(c)[-8:]
    context = bot._context
    cleared = []
    bot._session_store.clear = lambda: cleared.append(True)  # les tests n'ont pas de dossier de données : on espionne
    bot.planning.replace({"enabled": True, "days": {}, "offset_min": 0, "offset_max": 0})  # aucune plage
    bot.scheduler._on = True  # ON sans lancer la boucle des fonctionnalités
    try:
        assert _until(lambda: _has(c, "page du jeu fermée (session gardée)")), _logs(c)[-8:]
        assert _until(lambda: _page_url(c) == "about:blank")
        assert bot._context is context  # même navigateur : cookies gardés
        assert cleared == []  # session enregistrée jamais effacée
        assert "hors planning" in _connection(c)
        bot.planning.replace({"enabled": False, "days": {}, "offset_min": 0, "offset_max": 0})  # début de plage
        assert _until(lambda: "dans la partie" in _connection(c)), _logs(c)[-8:]
        assert _has(c, "Planning : début de plage, réouverture du jeu.")
        assert game.info()["login_attempts"] == 1  # aucune nouvelle connexion avec les identifiants
        assert bot._context is context

        bot.planning.replace({"enabled": True, "days": {}, "offset_min": 0, "offset_max": 0})
        assert _until(lambda: _page_url(c) == "about:blank")
        game.logout()  # pendant la pause, le jeu expire la session
        bot.planning.replace({"enabled": False, "days": {}, "offset_min": 0, "offset_max": 0})
        assert _until(lambda: game.info()["login_attempts"] == 2 and "dans la partie" in _connection(c)), _logs(c)[-8:]
    finally:
        bot.scheduler._on = False


def test_manual_control_pauses_the_supervisor(world_client, game):
    c = world_client
    bot = c.app.state.bot
    bot._manual = True  # avant les identifiants : la connexion automatique démarrerait aussitôt
    _universe(c, "Asterion")
    _creds(c)
    assert _until(lambda: "contrôle manuel" in _connection(c))
    assert game.info()["login_attempts"] == 0
    bot._manual = False
    assert _until(lambda: "dans la partie" in _connection(c))


def test_logout_forgets_the_account_and_stops_the_connection(world_client, game):
    c = world_client
    _universe(c, "Asterion")
    _creds(c)
    assert _until(lambda: "dans la partie" in _connection(c))
    assert c.post("/api/logout").status_code == 200
    time.sleep(1.0)
    assert _connection(c) == "" and not c.get("/api/account").json()["has_credentials"]
    assert game.info()["login_attempts"] == 1


def test_preferences_are_validated(world_client):
    c = world_client
    res = c.put("/api/preferences", json={"login_delay": {"min_seconds": 5, "max_seconds": 1}})
    assert res.status_code == 422 and "minimum" in " ".join(res.json()["detail"])
    assert c.put("/api/preferences", json={"universe": "x" * 200}).status_code == 422
    evil = {"Origin": "http://evil.example"}
    assert c.put("/api/preferences", json={"universe": "A"}, headers=evil).status_code == 403
