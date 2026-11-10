"""Piliers programmés : vérifications à l'enregistrement (1 h d'effet, 24 h entre deux changements, 2 planètes fermées au
plus, état déjà ouvert / fermé), enregistrement, API."""

from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.pillars import PillarError, PillarStore, click_time, parse_local
from app.planning import TIMEZONE

EGIDE, MNESIS, OPALE, BIS = "6-79-Egide", "2-69-Mnesis", "3-111-Opale", "Aurore-Bis"
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=TIMEZONE).timestamp()  # mardi 10/11 à 20:00, heure de Paris


def at(day: int, hour: int, minute: int = 0) -> str:
    return f"2026-11-{day:02d}T{hour:02d}:{minute:02d}"


def store(tmp_path, closed=(), pending=None) -> PillarStore:
    s = PillarStore(str(tmp_path))
    planets = {p: {"closed": p in closed, "pending_to": None, "effective_at": None} for p in (EGIDE, MNESIS, OPALE, BIS)}
    for name, (to, minutes) in (pending or {}).items():
        planets[name] = {"closed": to == "open", "pending_to": to, "effective_at": NOW + minutes * 60}
    s.set_status(planets, now=NOW)
    return s


def test_the_time_chosen_is_when_the_change_takes_effect_so_the_click_is_one_hour_before(tmp_path):
    s = store(tmp_path)
    change = s.add(EGIDE, "close", at(10, 22), now=NOW)
    assert change["at"] == parse_local(at(10, 22)) and click_time(change) == parse_local(at(10, 21))
    assert s.next_click() == parse_local(at(10, 21))


def test_a_change_needs_at_least_one_hour(tmp_path):
    with pytest.raises(PillarError, match="Trop tôt : le changement prend 1 h"):
        store(tmp_path).add(EGIDE, "close", at(10, 20, 30), now=NOW)


def test_a_planet_already_open_or_closed_is_refused(tmp_path):
    s = store(tmp_path, closed=[OPALE])
    with pytest.raises(PillarError, match="déjà ouverts"):
        s.add(EGIDE, "open", at(11, 9), now=NOW)
    with pytest.raises(PillarError, match="déjà fermés"):
        s.add(OPALE, "close", at(11, 9), now=NOW)
    s.add(EGIDE, "close", at(11, 9), now=NOW)
    with pytest.raises(PillarError, match="déjà fermés"):  # fermée par le changement programmé juste avant
        s.add(EGIDE, "close", at(13, 9), now=NOW)


def test_two_changes_of_a_planet_need_24_hours(tmp_path):
    s = store(tmp_path)
    s.add(EGIDE, "close", at(11, 9), now=NOW)
    with pytest.raises(PillarError, match=r"24 h entre deux changements.*au plus tôt le 12/11 à 09:00"):
        s.add(EGIDE, "open", at(12, 8, 59), now=NOW)
    s.add(EGIDE, "open", at(12, 9), now=NOW)


def test_a_change_already_running_in_the_game_counts_for_the_24_hours(tmp_path):
    s = store(tmp_path, pending={BIS: ("open", 50)})  # réouverture en cours, effective à 20:50
    with pytest.raises(PillarError, match="au plus tôt le 11/11 à 20:50"):
        s.add(BIS, "close", at(11, 20, 49), now=NOW)
    s.add(BIS, "close", at(11, 20, 50), now=NOW)


def test_no_more_than_two_planets_closed_at_the_same_time(tmp_path):
    s = store(tmp_path, closed=[OPALE])
    s.add(MNESIS, "close", at(11, 9), now=NOW)
    with pytest.raises(PillarError, match=r"que 2 planètes aux Piliers fermés.*2-69-Mnesis, 3-111-Opale"):
        s.add(EGIDE, "close", at(11, 12), now=NOW)
    s.add(OPALE, "open", at(11, 11), now=NOW)  # Opale rouverte (clic à 10:00) : la place est libre dès le clic
    s.add(EGIDE, "close", at(11, 12), now=NOW)


def test_a_reopening_in_progress_frees_the_place_as_seen_in_the_game(tmp_path):
    # vrai jeu 10/11 : Opale fermée, Aurore-Bis en cours de réouverture → fermer Mnesis a été accepté
    s = store(tmp_path, closed=[OPALE], pending={BIS: ("open", 55)})
    s.add(MNESIS, "close", at(10, 21, 30), now=NOW)


def test_removing_a_change_and_an_unknown_planet(tmp_path):
    s = store(tmp_path)
    change = s.add(EGIDE, "close", at(11, 9), now=NOW)
    assert s.remove(change["id"]) and s.pending() == [] and not s.remove(change["id"])
    with pytest.raises(PillarError, match="Planète inconnue"):
        s.add("Nulle-Part", "close", at(11, 9), known_planets=[EGIDE], now=NOW)
    with pytest.raises(PillarError, match="« Ouvrir » ou « Fermer »"):
        s.add(EGIDE, "souvent", at(11, 9), now=NOW)
    with pytest.raises(PillarError, match="date et l'heure"):
        s.add(EGIDE, "close", "demain", now=NOW)


def test_changes_status_and_history_survive_a_restart(tmp_path):
    s = store(tmp_path, pending={BIS: ("open", 50)})
    change = s.add(EGIDE, "close", at(11, 9), now=NOW)
    s.finish(change["id"], "done", "cliqué", effective_at=NOW + 3600)
    again = PillarStore(str(tmp_path))
    assert again.changes()[0]["state"] == "done" and again.history()[EGIDE] == NOW + 3600
    assert again.status()["planets"][BIS]["pending_to"] == "open" and again.next_click() is None


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_the_api_programs_lists_and_removes_changes_and_explains_a_refusal(api):
    body = api.get("/api/pillars").json()
    assert body["changes"] == [] and body["rules"] == {"effect_minutes": 60, "between_hours": 24, "max_closed": 2}
    later = datetime.fromtimestamp(datetime.now(TIMEZONE).timestamp() + 3 * 3600, TIMEZONE).strftime("%Y-%m-%dT%H:%M")
    res = api.post("/api/pillars/changes", json={"planet": EGIDE, "action": "close", "at": later})
    assert res.status_code == 200, res.text
    (change,) = res.json()["changes"]
    assert change["planet"] == EGIDE and change["state"] == "pending" and change["click_at"] == change["at"] - 3600
    refused = api.post("/api/pillars/changes", json={"planet": EGIDE, "action": "close", "at": later})
    assert refused.status_code == 422 and "déjà fermés" in " ".join(refused.json()["detail"])
    assert api.delete(f"/api/pillars/changes/{change['id']}").json()["changes"] == []
    assert api.delete("/api/pillars/changes/inconnu").status_code == 404
    assert api.post("/api/pillars/changes", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_the_pillars_feature_is_self_timed_on_the_next_click(api):
    feature = next(f for f in api.app.state.bot.scheduler._features.values() if f.id == "pillars")
    assert feature.self_timed and feature.next_due(api.app.state.bot) is None  # rien de programmé
