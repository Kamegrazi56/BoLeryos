"""Scan des planètes : à la demande (page Compte et univers) et tout seul une fois par connexion."""

from app.planets import GameUiError
from app.bot import State

MOCK_PLANETS = ["8-50-Concorde", "6-79-Egide", "9-169-Strate"]
AUTO_ATTEMPTS = 3


def _bot(client):
    return client.app.state.bot


def _runner(client):
    return _bot(client).supervisor


def _ensure_ready(client):
    return client.portal.call(_runner(client).ensure_ready)


def test_nothing_is_scanned_before_the_first_scan(game_client):
    data = game_client.get("/api/planets").json()
    assert data["planets"] == [] and data["scanned_at"] is None and data["error"] == "" and data["scanning"] is False


def test_manual_scan_lists_the_planets_with_coordinates_and_stocks(game_client):
    data = game_client.post("/api/planets/scan").json()
    assert [p["name"] for p in data["planets"]] == MOCK_PLANETS
    first = data["planets"][0]
    assert first["coords"] == "Z25 95 16"
    assert all(first[k] is not None for k in ("fer", "cristal", "ether"))
    assert data["source"] == "manuel" and data["scanned_at"] and data["error"] == ""
    assert game_client.get("/api/planets").json()["planets"] == data["planets"]  # le résultat est gardé


def test_manual_scan_works_while_the_bot_is_off(game_client):
    assert len(game_client.post("/api/planets/scan").json()["planets"]) == len(MOCK_PLANETS)


def test_scan_refused_with_a_reason_when_not_in_a_game(game_client):
    _bot(game_client)._set(State.IDLE, "test")
    data = game_client.post("/api/planets/scan").json()
    assert data["planets"] == [] and "pas dans une partie" in data["error"]


def test_scan_refused_in_manual_control(game_client):
    _bot(game_client)._manual = True
    data = game_client.post("/api/planets/scan").json()
    assert data["planets"] == [] and "contrôle manuel" in data["error"].lower()


def test_failed_scan_keeps_the_previous_list_and_says_why(game_client):
    game_client.post("/api/planets/scan")

    async def broken():
        raise GameUiError("la liste des planètes ne s'affiche pas")

    _runner(game_client)._reader.list_planets = broken
    data = game_client.post("/api/planets/scan").json()
    assert [p["name"] for p in data["planets"]] == MOCK_PLANETS
    assert "ne s'affiche pas" in data["error"]


def test_scanned_planets_feed_the_planet_suggestions(game_client):
    game_client.post("/api/planets/scan")
    assert set(MOCK_PLANETS) <= set(_runner(game_client).prefs.get()["catalog"]["planets"])


def test_the_bot_scans_by_itself_once_when_it_enters_the_game(game_client):
    assert _ensure_ready(game_client) is None
    data = _runner(game_client).scan_summary()
    assert [p["name"] for p in data["planets"]] == MOCK_PLANETS and data["source"] == "démarrage"
    _ensure_ready(game_client)
    assert _runner(game_client).scan_summary()["scanned_at"] == data["scanned_at"]  # pas de second scan pour la même connexion


def test_the_bot_scans_again_after_a_new_connection(game_client):
    runner = _runner(game_client)
    _ensure_ready(game_client)
    first_scan = runner.scan_summary()["scanned_at"]
    runner._scan_done_session, runner._scan_attempts = False, 0  # ce que fait la supervision en revoyant la page de connexion
    _ensure_ready(game_client)
    assert runner.scan_summary()["scanned_at"] > first_scan


def test_automatic_scan_gives_up_after_three_attempts_and_the_bot_stays_usable(game_client):
    calls = []

    async def broken():
        calls.append(1)
        raise GameUiError("bouton introuvable")

    _runner(game_client)._reader.list_planets = broken
    for _ in range(AUTO_ATTEMPTS + 2):
        assert _ensure_ready(game_client) is None
    assert len(calls) == AUTO_ATTEMPTS
