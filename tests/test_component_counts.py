"""Effectifs de composants par planète : relevés dans l'onglet « Composants » du Chantier spatial (absents d'Empire › Inventaire)."""

import json

from app.component_counts import ComponentCounts, counts_from_cards
from app.components import COMPONENTS
from app.recruiter import PopCard

CONCORDE = "8-50-Concorde"
EGIDE = "6-79-Egide"
STRATE = "9-169-Strate"


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _refresh(client):
    res = client.post("/api/components/inventory/refresh")
    assert res.status_code == 200, res.text
    return res.json()["inventory"]


def _card(owned):
    return PopCard("x", owned, 0, {"F": 1.0}, 1.0, True, [])


# ------------------------------------------------------------------------------ cartes → effectifs
def test_only_known_components_with_a_readable_count_are_kept():
    cards = {"canon laser": _card(100), "coque des monarques": _card(None), "inconnu": _card(5)}
    assert counts_from_cards(cards) == {"canon-laser": 100}


# ------------------------------------------------------------------------------ enregistrement
def test_a_planet_reading_replaces_only_that_planet(tmp_path):
    store = ComponentCounts(str(tmp_path))
    store.set_planet(CONCORDE, {"canon-laser": 100}, "manuel")
    store.set_planet(EGIDE, {"canon-laser": 7}, "passage")
    store.set_planet(CONCORDE, {"canon-laser": 120}, "passage")
    data = store.get()
    assert data["counts"] == {CONCORDE: {"canon-laser": 120}, EGIDE: {"canon-laser": 7}}
    assert data["source"] == "passage" and data["read_at"] is not None and data["error"] == ""


def test_the_counts_survive_a_restart_and_an_error(tmp_path):
    store = ComponentCounts(str(tmp_path))
    store.set_planet(CONCORDE, {"destroyer": 50}, "manuel")
    store.set_error("Relevé impossible : test")
    assert store.get()["counts"] == {CONCORDE: {"destroyer": 50}} and "test" in store.get()["error"]
    assert ComponentCounts(str(tmp_path)).get()["counts"] == {CONCORDE: {"destroyer": 50}}


def test_a_corrupt_or_foreign_file_gives_empty_counts(tmp_path):
    (tmp_path / "component_counts.json").write_text("{pas du json", encoding="utf8")
    assert ComponentCounts(str(tmp_path)).get()["counts"] == {}
    (tmp_path / "component_counts.json").write_text(json.dumps({"counts": {"A": {"x": "beaucoup"}}}), encoding="utf8")
    assert ComponentCounts(str(tmp_path)).get()["counts"] == {}


def test_the_store_works_in_memory_without_a_data_folder():
    store = ComponentCounts(None)
    store.set_planet(CONCORDE, {"chasseur": 1}, "manuel")
    assert store.get()["counts"] == {CONCORDE: {"chasseur": 1}}


# ------------------------------------------------------------------------------ relevé dans le jeu
def test_nothing_is_known_before_the_first_reading(game_client):
    inventory = game_client.get("/api/components").json()["inventory"]
    assert inventory["counts"] == {} and inventory["read_at"] is None and inventory["reading"] is False


def test_the_button_reads_every_planet_without_producing_anything(game_client, game):
    game.set_prod(1, "Canon laser", owned=7)
    _scan(game_client)
    inventory = _refresh(game_client)
    assert inventory["error"] == "" and inventory["source"] == "manuel" and inventory["read_at"] is not None
    assert sorted(inventory["counts"]) == sorted([CONCORDE, EGIDE, STRATE])
    assert inventory["counts"][CONCORDE]["canon-laser"] == 100 and inventory["counts"][EGIDE]["canon-laser"] == 7
    assert inventory["counts"][STRATE]["destroyer"] == 50
    assert [a for a in game.actions() if a["kind"] == "production"] == []  # lecture seule : rien n'est produit


def test_a_reading_before_the_planet_scan_says_so_and_keeps_the_table(game_client):
    inventory = _refresh(game_client)
    assert "scan des planètes" in inventory["error"] and inventory["counts"] == {}


def test_a_reading_outside_a_game_gives_a_french_error(client):
    inventory = client.post("/api/components/inventory/refresh").json()["inventory"]
    assert inventory["counts"] == {} and inventory["error"] and inventory["error"][0].isupper()


def test_the_refresh_route_checks_the_origin(game_client):
    assert game_client.post("/api/components/inventory/refresh", headers={"Origin": "http://evil.example"}).status_code == 403


def test_a_production_pass_notes_the_counts_of_every_planet_it_visits(game_client, game):
    _scan(game_client)
    line = {"item": "canon-laser", "mode": "fixed", "amount": 10, "planets": [CONCORDE], "repeat": True}
    assert game_client.put("/api/components", json={"queue": [line]}).status_code == 200
    game_client.portal.call(_bot(game_client).producer.run_pass)
    inventory = game_client.get("/api/components").json()["inventory"]
    assert inventory["source"] == "passage" and list(inventory["counts"]) == [CONCORDE]  # seules les planètes visitées sont notées
    assert inventory["counts"][CONCORDE]["canon-laser"] == 100  # l'effectif d'AVANT la production lancée


# ------------------------------------------------------------------------------ ligne la plus précise
def test_the_most_precise_line_wins_whatever_the_order(game_client, game):
    _scan(game_client)
    lines = [
        {"item": "chasseur", "mode": "fixed", "amount": 7, "planets": [], "repeat": True},
        {"item": "chasseur", "mode": "fixed", "amount": 6, "planets": [EGIDE, STRATE], "repeat": True},
        {"item": "chasseur", "mode": "fixed", "amount": 5, "planets": [STRATE], "repeat": True},
    ]
    assert game_client.put("/api/components", json={"queue": lines}).status_code == 200
    game_client.portal.call(_bot(game_client).producer.run_pass)
    made = [(a["planet"], a["qty"]) for a in game.actions() if a["kind"] == "production" and a["section"] == "component"]
    assert made == [(CONCORDE, 7), (EGIDE, 6), (STRATE, 5)]


def test_the_list_accepts_a_line_per_component_and_planet(game_client):
    _scan(game_client)
    queue = [{"item": c.id, "mode": "target", "amount": 5, "planets": [p], "repeat": False}
             for c in COMPONENTS for p in (CONCORDE, EGIDE, STRATE)]
    assert len(queue) == 37 * 3  # plus que les 100 lignes d'avant
    assert game_client.put("/api/components", json={"queue": queue}).status_code == 200
    assert len(game_client.get("/api/components").json()["settings"]["queue"]) == 37 * 3


def test_a_saved_line_for_a_planet_gone_from_the_scan_does_not_block_the_next_save(game_client):
    _scan(game_client)
    old = {"item": "chasseur", "mode": "fixed", "amount": 1, "planets": ["Colonie-Perdue"], "repeat": True}
    _bot(game_client).components.replace({"queue": [old]}, lambda: [CONCORDE, "Colonie-Perdue"])  # enregistrée quand la planète existait
    new = {"item": "destroyer", "mode": "target", "amount": 5, "planets": [EGIDE], "repeat": False}
    assert game_client.put("/api/components", json={"queue": [old, new]}).status_code == 200
    # une planète jamais vue reste refusée
    ghost = {"item": "destroyer", "mode": "target", "amount": 5, "planets": ["Inconnue"], "repeat": False}
    assert game_client.put("/api/components", json={"queue": [old, ghost]}).status_code == 422


def test_a_planet_that_fails_does_not_stop_the_reading_of_the_others(game_client):
    from app.planets import GameUiError

    _scan(game_client)
    producer = _bot(game_client).producer
    original = producer._select_planet

    async def flaky(planet, *args, **kwargs):
        if planet == EGIDE:
            raise GameUiError("page lente")
        return await original(planet, *args, **kwargs)

    producer._select_planet = flaky
    inventory = _refresh(game_client)
    assert sorted(inventory["counts"]) == sorted([CONCORDE, STRATE])  # Égide manque, les autres sont lues
    assert "incomplet" in inventory["error"] and EGIDE in inventory["error"]
