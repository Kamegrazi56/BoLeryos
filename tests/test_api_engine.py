"""Moteur « api » (lecture seule) : état du jeu → planètes, modèles, inventaire ; session reprise du navigateur."""

import asyncio

import httpx
import pytest

from app.api_engine import (
    ApiEngine,
    api_session_from_request,
    inventory_planets,
    model_infos,
    planet_infos,
    universe_of,
)
from app.engine import GameEngine
from app.planets import GameUiError

STATE = {
    "activePlanetId": "p2",
    "planets": [
        {
            "id": "p1", "name": "6-79-Egide", "spatialLocation": {"address": "M69 38 06"},
            "resources": {"fer": 1500.0, "cristal": 200.5, "ether": 30},
            "buildings": {"mineFer": 41, "caserne": 20, "inconnu": 3}, "groundUnits": {"pilote": 122190, "soldat": 5, "rempart": 7},
            "ships": {"m1": 2}, "shipModels": [{"id": "m1", "name": ">Lentus", "kind": "component", "role": "transport"}],
        },
        {
            "id": "p2", "name": "8-50-Concorde", "spatialLocation": {"address": "Z25 95 16"},
            "resources": {"fer": 10, "cristal": 20, "ether": 30},
            "buildings": {"mineFer": 12}, "groundUnits": {"pilote": 1},
            "ships": {"m1": 4, "m2": 1}, "shipModels": [{"id": "m1", "name": ">Lentus", "kind": "component", "role": "transport"}, {"id": "m2", "name": "Anode", "kind": "component", "role": "warship"}],
        },
    ],
}
BASE = "https://asterion-api.aleryos.com"
HEADERS = {"authorization": "Bearer secret-token", "x-aleryos-client-contract": "7", "cookie": "ne-pas-reprendre"}


def test_planets_are_read_with_coordinates_and_stocks():
    planets = planet_infos(STATE)
    assert [(p.name, p.coords, p.stocks) for p in planets] == [
        ("6-79-Egide", "M69 38 06", (1500.0, 200.5, 30.0)),
        ("8-50-Concorde", "Z25 95 16", (10.0, 20.0, 30.0)),
    ]


def test_models_come_with_the_count_of_the_active_planet():
    assert model_infos(STATE) == [
        {"name": ">Lentus", "kind": "civil", "owned": 4, "planet": "8-50-Concorde", "role": "transport", "access": ""},
        {"name": "Anode", "kind": "militaire", "owned": 1, "planet": "8-50-Concorde", "role": "warship", "access": ""},
    ]


def test_the_inventory_uses_the_bot_identifiers_and_ignores_unknown_keys():
    first = inventory_planets(STATE)[0]
    assert first["name"] == "6-79-Egide" and first["coords"] == "M69 38 06"
    assert first["levels"]["mine-de-fer"] == 41 and first["levels"]["caserne"] == 20
    assert first["levels"]["tour-a-resonance"] == 0 and len(first["levels"]) == 16  # jamais construit : niveau 0, comme la page Inventaire
    assert first["units"] == {"pilote": 122190, "rempart-de-fer": 7}  # le Soldat (automatique) n'est pas une ligne du bot


def test_a_browser_request_to_the_universe_api_gives_a_session_without_cookies():
    session = api_session_from_request(BASE + "/join", HEADERS)
    assert session["base"] == BASE and session["headers"] == {"authorization": "Bearer secret-token", "x-aleryos-client-contract": "7"}
    assert universe_of(BASE) == "asterion"


@pytest.mark.parametrize("url,headers", [
    ("https://aleryos.com/game/asterion/empire", HEADERS),
    ("https://auth.aleryos.com/auth/v1/user", HEADERS),
    ("https://evil.example/join", HEADERS),
    (BASE + "/public/universe-stats", {"user-agent": "x"}),  # sans jeton : rien à reprendre
])
def test_other_requests_give_no_session(url, headers):
    assert api_session_from_request(url, headers) is None


class _Bot:
    def __init__(self, session):
        self.api_session = session


def _engine(handler, session=True):
    bot = _Bot(api_session_from_request(BASE + "/join", HEADERS) if session else None)
    return ApiEngine(bot, transport=httpx.MockTransport(handler))


def test_the_engine_posts_join_with_the_session_headers_and_reads_the_state():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers.get("authorization"), contract=request.headers.get("x-aleryos-client-contract"),
                    cookie=request.headers.get("cookie"), body=request.content)
        return httpx.Response(200, json={"universeId": "u", "state": STATE})

    engine = _engine(handler)
    planets = asyncio.run(engine.scan_planets())
    assert [p.name for p in planets] == ["6-79-Egide", "8-50-Concorde"]
    assert seen["url"] == BASE + "/join" and seen["auth"] == "Bearer secret-token" and seen["contract"] == "7"
    assert seen["cookie"] is None and seen["body"] == b"{}"
    assert isinstance(engine, GameEngine) and engine.kind == "api" and not engine.supports("constructions")


def test_without_a_session_the_engine_says_to_connect_first():
    with pytest.raises(GameUiError, match="connectez-vous"):
        asyncio.run(_engine(lambda r: httpx.Response(200), session=False).scan_planets())


@pytest.mark.parametrize("status,message", [(401, "refuse la session"), (403, "refuse la session"), (500, "a répondu 500")])
def test_api_errors_become_readable_messages(status, message):
    with pytest.raises(GameUiError, match=message):
        asyncio.run(_engine(lambda r: httpx.Response(status)).scan_planets())


def test_an_unreadable_answer_is_refused_without_guessing():
    with pytest.raises(GameUiError, match="illisible"):
        asyncio.run(_engine(lambda r: httpx.Response(200, json={"x": 1})).scan_planets())


def test_a_state_without_planets_is_an_error_not_an_empty_list():
    with pytest.raises(GameUiError, match="aucune planète"):
        asyncio.run(_engine(lambda r: httpx.Response(200, json={"state": {"planets": []}})).scan_planets())


def test_the_token_never_appears_in_an_error_message():
    with pytest.raises(GameUiError) as err:
        asyncio.run(_engine(lambda r: httpx.Response(401)).scan_planets())
    assert "secret-token" not in str(err.value)


def test_the_bot_keeps_the_api_session_of_its_page(game_client):
    bot = game_client.app.state.bot
    bot._on_request(type("R", (), {"url": BASE + "/join", "headers": HEADERS})())
    assert bot.api_session["headers"]["authorization"] == "Bearer secret-token"
    assert bot.engine.kind == "browser"  # le moteur ne change que sur choix de l'utilisateur


def test_choosing_the_api_engine_makes_the_scan_go_through_it(game_client):
    bot = game_client.app.state.bot
    assert game_client.put("/api/preferences", json={"engine": "api"}).status_code == 200
    assert bot.engine.kind == "api"
    result = game_client.post("/api/planets/scan").json()
    assert result["planets"] == [] and "connectez-vous" in result["error"]  # aucune session reprise : message clair, rien d'inventé
