"""Attaque automatique des errants : seuils de ressources (1, 2 ou 3), intervalle, composition, nombre total.
Simulation seulement ; la lecture des rapports d'espionnage n'est pas encore codée (les tests fournissent les rapports)."""

from app.automation.raids import judge
from tests.test_automation import _cycle, _has, _logs, _runner

RULE = {"min_fer": 1_000_000, "min_cristal": 500_000, "min_ether": 100_000}


def _raid(client, mode="dry_run", **raid):
    body = {"mode": mode, "cycle": {"min_minutes": 0.02, "max_minutes": 0.02},
            "raids": {"enabled": True, "way": "piliers", "coord_min": "M69 00 00", "coord_max": "M69 99 99", "planet": "",
                      **RULE, "required": 1, "total": 3, "units": [{"name": "Rempart de Fer", "mode": "number", "value": 100}], **raid}}
    res = client.put("/api/automation/settings", json={"settings": body, "confirm_live": mode == "live"})
    assert res.status_code == 200, res.text
    client.app.state.bot.automation.store.add_catalog("planets", ["6-79-Egide", "8-50-Concorde", "9-169-Strate"])


def _report(client, coords, fer, cristal, ether):
    _runner(client)._reports[coords] = {"fer": fer, "cristal": cristal, "ether": ether, "t": 0}


def _simulated(client):
    return [line for line in _logs(client) if line.startswith("[Simulation] Attaque des errants")]


# ----------------------------------------------------------------------------- sans navigateur
def test_one_two_or_three_resources_must_exceed_their_minimum():
    rich_fer = {"fer": 2_000_000, "cristal": 100, "ether": 100}
    two = {"fer": 2_000_000, "cristal": 600_000, "ether": 100}
    all_three = {"fer": 2_000_000, "cristal": 600_000, "ether": 200_000}
    assert judge(rich_fer, {**RULE, "required": 1}).attack and not judge(rich_fer, {**RULE, "required": 2}).attack
    assert judge(two, {**RULE, "required": 2}).attack and not judge(two, {**RULE, "required": 3}).attack
    assert judge(all_three, {**RULE, "required": 3}).above == ["Fer", "Cristal", "Éther"]
    assert not judge({"fer": 1_000_000, "cristal": 0, "ether": 0}, {**RULE, "required": 1}).attack  # « dépasser » : strictement au-dessus
    assert not judge({}, {**RULE, "required": 1}).attack


# ----------------------------------------------------------------------------------- réglages
def test_raid_settings_are_validated(game_client):
    def put(**raid):
        return game_client.put("/api/automation/settings", json={"settings": {"raids": raid}})

    assert put(way="flotte", coord_min="M69 00 00", coord_max="M69 99 99", required=3, total=10).status_code == 200
    for bad, word in [
        ({"way": "tunnel"}, "voie"), ({"coord_min": "M6 0 0"}, "format"), ({"coord_min": "M70 00 00", "coord_max": "M69 00 00"}, "avant"),
        ({"required": 4}, "1, 2 ou 3"), ({"total": 0}, "nombre d'attaques"), ({"min_fer": -1}, "minimums"),
        ({"enabled": True, "coord_min": "", "coord_max": "", "units": [{"name": "Soldat", "mode": "max"}]}, "coordonnée minimum"),
        ({"enabled": True, "coord_min": "M69 00 00", "coord_max": "M69 99 99", "units": []}, "au moins une unité"),
    ]:
        res = put(**bad)
        assert res.status_code == 422 and word in " ".join(res.json()["detail"]), (bad, res.text)


# ------------------------------------------------------------------- avec le navigateur (faux jeu)
def test_without_reports_nothing_is_decided_and_it_is_said(game_client, game):
    _raid(game_client)
    _cycle(game_client)
    assert _simulated(game_client) == []
    assert _has(game_client, "2 errant(s) dans l'intervalle, mais aucune ressource connue : la lecture des rapports d'espionnage n'est pas encore codée")


def test_a_rich_errant_in_the_interval_would_be_attacked_with_the_composition(game_client, game):
    _raid(game_client, required=2)
    _report(game_client, "M69 34 68", 5_000_000, 50, 50)  # Kairo-Vigie : une seule ressource au-dessus → ignoré
    _report(game_client, "M69 37 24", 5_000_000, 900_000, 50)  # Bryn-Socle : deux ressources au-dessus
    _report(game_client, "M97 50 19", 9e9, 9e9, 9e9)  # Torek : hors intervalle
    _cycle(game_client)
    (line,) = _simulated(game_client)
    assert "une attaque par les Piliers sur Bryn-Socle-176 [AI] (Bryn-Socle-176 3, M69 37 24) avec 100 × Rempart de Fer" in line
    assert "ressources : Fer 5 000 000, Cristal 900 000, Éther 50" in line
    assert _has(game_client, "Kairo-Vigie-378 [AI] (Kairo-Vigie-378 7, M69 34 68) ignoré (1 ressource(s) au-dessus du minimum sur 2 demandée(s))")
    assert game.actions() == []


def test_the_flotte_way_uses_its_own_units_and_a_missing_unit_is_reported(game_client, game):
    _raid(game_client, way="flotte", units=[{"name": "Testo Civil Transpo Destroy Def", "mode": "percent", "value": 50}])
    _report(game_client, "M69 34 68", 5_000_000, 0, 0)
    _cycle(game_client)
    assert "une attaque par la Flotte sur Kairo-Vigie-378 [AI]" in _simulated(game_client)[0] and "21 × Testo Civil" in _simulated(game_client)[0]
    _raid(game_client, way="flotte", units=[{"name": "Vaisseau fantôme", "mode": "number", "value": 1}])
    _cycle(game_client)
    assert _has(game_client, "composition impossible (« Vaisseau fantôme » absent de la page)")


def test_targets_rotate_and_the_total_stops_the_attacks(game_client, game):
    _raid(game_client, total=2)
    for coords in ("M69 34 68", "M69 37 24"):
        _report(game_client, coords, 5_000_000, 0, 0)
    for _ in range(2):
        _cycle(game_client)
    lines = _simulated(game_client)
    assert "Kairo-Vigie-378" in lines[0] and "Bryn-Socle-176" in lines[1]  # jamais attaquée d'abord
    runner = _runner(game_client)
    runner._raids_done = 2  # (l'attaque réelle comptera ; ici on simule le total atteint)
    _cycle(game_client)
    assert len(_simulated(game_client)) == 2 and _has(game_client, "les 2 attaque(s) demandée(s) sont faites")
    assert game_client.post("/api/automation/raids/reset").json()["raids_done"] == 0
    _cycle(game_client)
    assert len(_simulated(game_client)) == 3


def test_real_mode_never_attacks_for_now(game_client, game):
    _raid(game_client, mode="live")
    _report(game_client, "M69 34 68", 5_000_000, 0, 0)
    _cycle(game_client)
    assert game.actions() == [] and _has(game_client, "l'attaque réelle n'est pas encore disponible") and len(_simulated(game_client)) == 1
    assert game_client.get("/api/automation").json()["status"]["raids_done"] == 0
