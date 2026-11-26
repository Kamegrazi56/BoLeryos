"""Constructions : catalogue des 16 bâtiments, liste ordonnée « bâtiment + niveau cible », toutes les planètes ou
les planètes cochées."""

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.constructions import BUILDINGS, ConstructionError, ConstructionSettings, describe, mode_of, validate
from app.features import FEATURES
from app.main import create_app

LINE = {"building": "mine-de-fer", "level": 45}


# ------------------------------------------------------------------------------ catalogue
def test_catalog_has_the_sixteen_game_buildings_split_in_two_pages():
    assert len(BUILDINGS) == 16 and len({b.id for b in BUILDINGS}) == 16
    assert [b.page for b in BUILDINGS].count("Industrie") == 8
    assert [b.page for b in BUILDINGS].count("Infrastructures") == 8
    labels = {b.label for b in BUILDINGS}
    assert {"Mine de Fer", "Synthétiseur d'Éther", "Centrale Énergétique", "Cuve d'Éther", "Tour à Résonance"} <= labels


# ------------------------------------------------------------------------------ validation
def test_defaults_are_all_planets_and_an_empty_list():
    assert validate({}) == {"planets": [], "queue": []}


def test_the_list_keeps_its_order_which_is_the_priority():
    queue = [{"building": "terraformeur", "level": 12}, LINE, {"building": "caserne", "level": 25}]
    assert [line["building"] for line in validate({"queue": queue})["queue"]] == ["terraformeur", "mine-de-fer", "caserne"]


@pytest.mark.parametrize(
    "queue",
    [
        [{"building": "inconnu", "level": 3}],
        [LINE, LINE],  # une seule ligne par bâtiment
        [{"building": "mine-de-fer", "level": 0}],
        [{"building": "mine-de-fer", "level": 501}],
        [{"building": "mine-de-fer", "level": 4.5}],
        [{"building": "mine-de-fer", "level": True}],
        [{"building": "mine-de-fer", "level": "45"}],
        [{"level": 5}],
        ["mine-de-fer"],
    ],
)
def test_bad_lines_are_refused_with_a_message(queue):
    with pytest.raises(ConstructionError) as err:
        validate({"queue": queue})
    assert err.value.errors[0].startswith("Ligne ")


def test_chosen_planets_must_be_known_from_the_scan():
    known = ["8-50-Concorde", "6-79-Egide"]
    with pytest.raises(ConstructionError, match="inconnue"):
        validate({"planets": ["8-50-Concorde", "Nulle-Part"]}, known_planets=known)
    assert validate({"planets": ["8-50-Concorde", "6-79-Egide", "8-50-Concorde"]}, known_planets=known)["planets"] == known  # doublon retiré
    assert validate({"planets": []}, known_planets=known)["planets"] == []  # « toutes » : toujours valable
    for bad in ("8-50-Concorde", [12], [""]):
        with pytest.raises(ConstructionError, match="Planètes"):
            validate({"planets": bad})


def test_the_old_all_or_one_planet_format_is_still_read():
    assert validate({"scope": "one", "planet": "8-50-Concorde"})["planets"] == ["8-50-Concorde"]
    assert validate({"scope": "all", "planet": "8-50-Concorde"})["planets"] == []  # ignorée quand « toutes »
    with pytest.raises(ConstructionError, match="Planètes"):
        validate({"scope": "one"})  # « une seule » sans planète


def test_describe_gives_a_readable_sentence():
    assert describe(validate({})) == "aucune ligne de construction réglée"
    text = describe(validate({"planets": ["8-50-Concorde", "6-79-Egide"], "queue": [LINE]}))
    assert "Mine de Fer niveau 45" in text and "sur 8-50-Concorde, 6-79-Egide" in text
    assert "sur toutes les planètes" in describe(validate({"queue": [LINE]}))


# ------------------------------------------------------------------------------ objectifs propres à une planète
CONCORDE = "8-50-Concorde"
EGIDE = "6-79-Egide"


def test_a_line_can_target_one_planet_and_a_legacy_line_stays_as_it_was():
    queue = validate({"queue": [{**LINE, "planet": CONCORDE}, {"building": "caserne", "level": 25}]})["queue"]
    assert queue == [{"building": "mine-de-fer", "level": 45, "planet": CONCORDE}, {"building": "caserne", "level": 25}]  # pas de clé « planet » ajoutée
    assert validate({"queue": [{**LINE, "planet": ""}]})["queue"] == [LINE]  # vide = toutes les planètes visées


def test_the_same_building_may_have_a_line_per_planet_but_not_two_for_the_same_planet():
    lines = [{**LINE, "planet": CONCORDE}, {**LINE, "planet": EGIDE, "level": 50}, LINE]  # + une ligne « toutes les planètes »
    assert len(validate({"queue": lines})["queue"]) == 3
    with pytest.raises(ConstructionError, match="déjà dans la liste"):
        validate({"queue": [{**LINE, "planet": CONCORDE}, {**LINE, "planet": CONCORDE, "level": 50}]})


def test_the_planet_of_a_line_must_be_a_known_planet_but_is_accepted_leniently_at_load():
    with pytest.raises(ConstructionError, match="Ligne 1 : planète inconnue"):
        validate({"queue": [{**LINE, "planet": "Nulle-Part"}]}, known_planets=[CONCORDE, EGIDE])
    assert validate({"queue": [{**LINE, "planet": CONCORDE}]}, known_planets=[CONCORDE])["queue"][0]["planet"] == CONCORDE
    assert validate({"queue": [{**LINE, "planet": "Nulle-Part"}]})["queue"][0]["planet"] == "Nulle-Part"  # chargement : sans scan
    for bad in (12, ["x"], True):
        with pytest.raises(ConstructionError, match="Ligne 1"):
            validate({"queue": [{**LINE, "planet": bad}]})


def test_a_line_per_building_and_planet_fits_in_the_list():
    planets = [f"{i}-Planete" for i in range(15)]
    lines = [{"building": b.id, "level": 40, "planet": p} for b in BUILDINGS for p in planets]  # 16 × 15 = 240
    assert len(validate({"queue": lines})["queue"]) == 240


def test_dropping_a_reached_line_only_removes_the_line_of_that_planet(tmp_path):
    store = ConstructionSettings(str(tmp_path))
    store.replace({"queue": [{"building": "caserne", "level": 25, "planet": EGIDE}, {"building": "caserne", "level": 25, "planet": CONCORDE}, LINE]})
    assert store.drop([("caserne", EGIDE, 25)]) == [("caserne", EGIDE, 25)]
    assert store.get()["queue"] == [{"building": "caserne", "level": 25, "planet": CONCORDE}, LINE]
    assert store.drop([("caserne", "", 25)]) == []  # la ligne « toutes les planètes » est une autre ligne
    assert store.drop([("mine-de-fer", "", 45)]) == [("mine-de-fer", "", 45)]
    assert ConstructionSettings(str(tmp_path)).get()["queue"] == [{"building": "caserne", "level": 25, "planet": CONCORDE}]


def test_describe_says_which_planet_a_line_is_for():
    text = describe(validate({"queue": [{**LINE, "planet": CONCORDE}, {"building": "caserne", "level": 25}]}))
    assert f"Mine de Fer niveau 45 ({CONCORDE})" in text and "Caserne niveau 25" in text


# ------------------------------------------------------------------------------ enregistrement
def test_settings_survive_a_restart(tmp_path):
    ConstructionSettings(str(tmp_path)).replace({"planets": ["Aurore-Bis", "6-79-Egide"], "queue": [LINE]})
    again = ConstructionSettings(str(tmp_path)).get()
    assert again == {"planets": ["Aurore-Bis", "6-79-Egide"], "queue": [LINE]}


def test_a_file_in_the_old_format_is_converted(tmp_path):
    (tmp_path / "constructions.json").write_text(json.dumps({"scope": "one", "planet": "Aurore-Bis", "queue": [LINE]}), encoding="utf8")
    assert ConstructionSettings(str(tmp_path)).get() == {"planets": ["Aurore-Bis"], "queue": [LINE]}


def test_a_refused_change_keeps_the_previous_settings(tmp_path):
    store = ConstructionSettings(str(tmp_path))
    store.replace({"queue": [LINE]})
    with pytest.raises(ConstructionError):
        store.replace({"queue": [{"building": "x", "level": 1}]})
    assert store.get()["queue"] == [LINE]


def test_a_corrupt_file_gives_an_empty_list(tmp_path):
    (tmp_path / "constructions.json").write_text("{pas du json", encoding="utf8")
    assert ConstructionSettings(str(tmp_path)).get() == {"planets": [], "queue": []}


# ------------------------------------------------------------------------------ modes de lancement par planète
def test_modes_are_kept_only_when_set_and_default_to_the_first_payable_line():
    assert "modes" not in validate({"queue": [LINE]})  # anciens réglages : strictement inchangés
    saved = validate({"queue": [LINE], "modes": {"Egide": "order", "": "any"}})
    assert saved["modes"] == {"Egide": "order", "": "any"}
    assert mode_of({}, "Egide") == "any"
    assert mode_of({"Egide": "order"}, "  egide ") == "order"
    assert mode_of({"": "order"}, "Concorde") == "order"  # la planète sans mode propre suit celui des lignes « toutes les planètes »
    assert mode_of({"": "order", "Concorde": "any"}, "Concorde") == "any"


@pytest.mark.parametrize("modes", [{"Egide": "vite"}, {"Egide": 3}, {"X" * 61: "order"}, ["Egide"]])
def test_a_bad_mode_is_refused_in_french(modes):
    with pytest.raises(ConstructionError) as exc:
        validate({"queue": [LINE], "modes": modes})
    assert any(e.startswith("Modes :") for e in exc.value.errors)


def test_the_mode_of_an_unknown_planet_is_refused_once_the_scan_is_known():
    with pytest.raises(ConstructionError, match="planète inconnue"):
        validate({"modes": {"Fantôme": "order"}}, known_planets=["Egide"])
    assert validate({"modes": {"Fantôme": "order"}})["modes"] == {"Fantôme": "order"}  # acceptée au chargement


def test_modes_survive_a_restart_and_show_in_the_summary(tmp_path):
    store = ConstructionSettings(str(tmp_path))
    store.replace({"queue": [LINE], "modes": {"Egide": "order"}})
    assert ConstructionSettings(str(tmp_path)).get()["modes"] == {"Egide": "order"}
    assert "ordre strict de la liste : Egide" in describe(store.get())


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_lists_the_catalog_and_saves_the_list(api):
    data = api.get("/api/constructions").json()
    assert len(data["buildings"]) == 16 and data["settings"]["queue"] == []
    res = api.put("/api/constructions", json={"planets": [], "queue": [LINE, {"building": "caserne", "level": 25}]})
    assert res.status_code == 200
    assert [line["building"] for line in api.get("/api/constructions").json()["settings"]["queue"]] == ["mine-de-fer", "caserne"]


def test_api_keeps_the_planet_of_a_line_and_accepts_a_long_list(api):
    lines = [{"building": b.id, "level": 40, "planet": f"{i}-Planete"} for b in BUILDINGS for i in range(10)]  # 160 lignes, bien au-delà de l'ancienne limite de 50
    assert api.put("/api/constructions", json={"queue": lines}).status_code == 200
    saved = api.get("/api/constructions").json()["settings"]["queue"]
    assert len(saved) == 160 and saved[0] == {"building": "mine-de-fer", "level": 40, "planet": "0-Planete"}


def test_a_planet_known_only_from_the_inventory_is_accepted_for_a_line(game_client):
    game_client.post("/api/planets/scan")
    bot = game_client.app.state.bot
    bot.inventory.set([{"name": "Aurore-Bis", "coords": "", "levels": {}}], "manuel")  # colonie vue dans l'inventaire, absente du scan
    ok = game_client.put("/api/constructions", json={"queue": [{**LINE, "planet": "Aurore-Bis"}]})
    assert ok.status_code == 200 and ok.json()["settings"]["queue"][0]["planet"] == "Aurore-Bis"
    assert game_client.put("/api/constructions", json={"queue": [{**LINE, "planet": "Nulle-Part"}]}).status_code == 422


def test_a_saved_line_for_a_vanished_planet_does_not_block_the_next_save(game_client):
    game_client.post("/api/planets/scan")
    old = {**LINE, "planet": "Nulle-Part"}
    game_client.app.state.bot.constructions.replace({"queue": [old]})  # chargée telle quelle (la planète n'existe plus)
    assert game_client.put("/api/constructions", json={"queue": [old, {"building": "caserne", "level": 25}]}).status_code == 200
    assert game_client.put("/api/constructions", json={"queue": [{**LINE, "planet": "Autre-Part"}]}).status_code == 422  # une nouvelle planète inconnue reste refusée


def test_api_refuses_a_bad_list_in_french_and_checks_the_origin(api):
    res = api.put("/api/constructions", json={"queue": [{"building": "x", "level": 3}]})
    assert res.status_code == 422 and "bâtiment inconnu" in " ".join(res.json()["detail"])
    assert api.put("/api/constructions", json={"planets": [""]}).status_code == 422
    assert api.put("/api/constructions", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_chosen_planets_must_come_from_the_scan(game_client):
    game_client.post("/api/planets/scan")
    planets = game_client.get("/api/constructions").json()["planets"]
    assert "8-50-Concorde" in planets
    assert game_client.put("/api/constructions", json={"planets": ["8-50-Concorde", "6-79-Egide"]}).status_code == 200
    assert game_client.put("/api/constructions", json={"planets": ["Nulle-Part"]}).status_code == 422


def test_the_construction_feature_with_an_empty_list_does_nothing(api):
    feature = next(f for f in FEATURES if f.id == "constructions")
    assert "rien à faire" in asyncio.run(feature.run(api.app.state.bot))


def test_api_saves_and_returns_the_mode_of_a_planet(game_client):
    res = game_client.put("/api/constructions", json={"queue": [LINE], "modes": {"": "order"}})
    assert res.status_code == 200 and game_client.get("/api/constructions").json()["settings"]["modes"] == {"": "order"}
    assert game_client.put("/api/constructions", json={"queue": [LINE], "modes": {"": "rapide"}}).status_code == 422
