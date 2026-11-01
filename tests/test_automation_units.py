"""Briques pures de l'automatisation : nombres, horaires, réglages, décisions."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.automation.decide import decide
from app.automation.numbers import norm, parse_amount, parse_duration
from app.automation.schedule import Window, is_active, next_change, parse_hhmm
from app.automation.settings import DEFAULTS, SettingsError, SettingsStore, normalized_targets, validate
from app.automation.state import Card, Resources, parse_page, parse_planets

PARIS = ZoneInfo("Europe/Paris")


# ---------------------------------------------------------------- nombres et durées
@pytest.mark.parametrize(
    "text,expected",
    [
        ("201.765M", 201.765e6), ("58,035 M", 58.035e6), ("4,121 G", 4.121e9), ("3,4 Md", 3.4e9),
        ("188.2k", 188200.0), ("1.254M", 1.254e6), ("535", 535.0), ("11 014", 11014.0), ("4 808", 4808.0),
        ("1 111", 1111.0), ("+4 338 260", 4338260.0), ("-12", -12.0), ("10.744G", 10.744e9), ("775 M", 775e6),
        ("4,808", 4808.0), ("1.234.567", 1234567.0), ("1 234,5 M", 1234.5e6), ("942,839 M", 942.839e6),
    ],
)
def test_parse_amount(text, expected):
    assert parse_amount(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "12 T", "1.5m", "—", "12 Ko"])
def test_parse_amount_refuses_what_it_does_not_understand(text):
    with pytest.raises(ValueError):
        parse_amount(text)


@pytest.mark.parametrize(
    "text,seconds",
    [("1j 2h", 93600), ("12h 22m", 44520), ("5m 25s", 325), ("5.00s", 5), ("12.15s", 12.15), ("1m 33s", 93), ("2h 14m", 8040)],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "—", "bientôt", "5"])
def test_parse_duration_refuses_what_it_does_not_understand(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_norm_ignores_case_spaces_and_curly_apostrophes():
    assert norm("Synthétiseur d’Éther") == norm("  synthétiseur d'éther ")


# -------------------------------------------------------------------- plages horaires
def _w(start, end, days=range(7)):
    return Window(parse_hhmm(start), parse_hhmm(end), frozenset(days))


def test_daytime_window():
    ws = [_w("08:00", "23:00")]
    assert is_active(datetime(2026, 11, 2, 8, 0, tzinfo=PARIS), ws)
    assert is_active(datetime(2026, 11, 2, 22, 59, tzinfo=PARIS), ws)
    assert not is_active(datetime(2026, 11, 2, 23, 0, tzinfo=PARIS), ws)
    assert not is_active(datetime(2026, 11, 2, 7, 59, tzinfo=PARIS), ws)


def test_overnight_window_uses_the_starting_day():
    ws = [_w("22:00", "06:00", days=[0])]  # lundi soir → mardi matin
    assert is_active(datetime(2026, 11, 2, 23, 0, tzinfo=PARIS), ws)  # lundi 23:00
    assert is_active(datetime(2026, 11, 3, 5, 59, tzinfo=PARIS), ws)  # mardi 05:59
    assert not is_active(datetime(2026, 11, 3, 6, 0, tzinfo=PARIS), ws)
    assert not is_active(datetime(2026, 11, 3, 23, 0, tzinfo=PARIS), ws)  # mardi soir : non
    assert not is_active(datetime(2026, 11, 2, 5, 0, tzinfo=PARIS), ws)  # lundi matin : non


def test_days_and_multiple_windows():
    ws = [_w("08:00", "12:00", days=[0, 1]), _w("14:00", "18:00", days=[5])]
    assert is_active(datetime(2026, 11, 2, 9, 0, tzinfo=PARIS), ws)  # lundi
    assert not is_active(datetime(2026, 11, 4, 9, 0, tzinfo=PARIS), ws)  # mercredi
    assert is_active(datetime(2026, 11, 7, 15, 0, tzinfo=PARIS), ws)  # samedi


def test_next_change():
    ws = [_w("08:00", "23:00")]
    active, change = next_change(datetime(2026, 11, 2, 10, 30, tzinfo=PARIS), ws)
    assert active and (change.hour, change.minute) == (23, 0)
    active, change = next_change(datetime(2026, 11, 2, 23, 30, tzinfo=PARIS), ws)
    assert not active and (change.day, change.hour) == (3, 8)
    assert next_change(datetime(2026, 11, 2, 12, 0, tzinfo=PARIS), []) == (False, None)


# ------------------------------------------------------------------------- réglages
def test_defaults_are_valid_and_dry_run():
    cleaned = validate(DEFAULTS, allow_live=False)
    assert cleaned["mode"] == "dry_run" and cleaned["constructions"]["enabled"] is False


def test_live_mode_needs_explicit_confirmation():
    data = {**DEFAULTS, "mode": "live"}
    with pytest.raises(SettingsError, match="confirmation"):
        validate(data, allow_live=False)
    assert validate(data, allow_live=True)["mode"] == "live"


@pytest.mark.parametrize(
    "patch",
    [
        {"daily_cap": 0}, {"daily_cap": "beaucoup"}, {"cycle": {"min_minutes": 0.1, "max_minutes": 5}},
        {"cycle": {"min_minutes": 5, "max_minutes": 2}}, {"mode": "autre"},
        {"schedule": {"enabled": True, "timezone": "Mars/Olympus", "windows": []}},
        {"schedule": {"enabled": True, "timezone": "Europe/Paris", "windows": [{"start": "25:00", "end": "08:00", "days": [1]}]}},
        {"schedule": {"enabled": True, "timezone": "Europe/Paris", "windows": [{"start": "08:00", "end": "09:00", "days": [9]}]}},
        {"constructions": {"enabled": True, "targets": {"Mine de Fer": 5000}}},
        {"constructions": {"enabled": True, "targets": {"Mine de Fer": "dix"}}},
    ],
)
def test_invalid_settings_are_refused(patch):
    with pytest.raises(SettingsError):
        validate({**DEFAULTS, **patch}, allow_live=True)


def test_store_persists_but_never_restores_live_mode(tmp_path):
    store = SettingsStore(str(tmp_path))
    data = {**DEFAULTS, "mode": "live", "constructions": {"enabled": True, "targets": {"Mine de Fer": 45}}}
    store.update(data, allow_live=True)
    assert store.get()["mode"] == "live"
    again = SettingsStore(str(tmp_path))  # redémarrage
    assert again.loaded_from_disk and again.get()["mode"] == "dry_run"
    assert again.get()["constructions"]["targets"] == {"Mine de Fer": 45}


def test_store_keeps_live_mode_when_saving_other_fields():
    store = SettingsStore(None)
    store.update({**DEFAULTS, "mode": "live"}, allow_live=True)
    store.update({**store.get(), "daily_cap": 5}, allow_live=False)  # même mode, sans nouvelle confirmation
    assert store.get()["mode"] == "live" and store.get()["daily_cap"] == 5


def test_store_never_contains_credentials(tmp_path):
    store = SettingsStore(str(tmp_path))
    store.update(DEFAULTS, allow_live=False)
    raw = (tmp_path / "automation.json").read_text().lower()
    assert "password" not in raw and "mot de passe" not in raw and "identifier" not in raw


# --------------------------------------------------------------------------- décisions
def _card(name, level, costs, enabled=True, action="Améliorer", duration=60.0):
    return Card(name, level, costs, duration, action, enabled)


RES = Resources(fer=100e6, cristal=50e6, ether=20e6, energie=5000)
T = normalized_targets({"Mine de Fer": 45, "Extracteur de Cristal": 45, "Centrale Énergétique": 30})


def test_picks_the_upgrade_that_weighs_least_on_the_stock():
    cards = [
        _card("Mine de Fer", 40, {"Fer": 80e6, "Cristal": 10e6}),  # 80 % du Fer
        _card("Extracteur de Cristal", 38, {"Fer": 30e6, "Cristal": 20e6}),  # 40 % du Cristal
    ]
    out = decide(cards, RES, T, "Améliorer")
    assert out.decision.card.name == "Extracteur de Cristal"


def test_ignores_goals_reached_unaffordable_and_untargeted():
    cards = [
        _card("Mine de Fer", 45, {"Fer": 1e6}),  # objectif atteint
        _card("Extracteur de Cristal", 38, {"Fer": 500e6}),  # trop cher
        _card("Hangar de Fer", 10, {"Fer": 1e6}),  # sans objectif
    ]
    out = decide(cards, RES, T, "Améliorer")
    assert out.decision is None
    assert "objectif" in out.summary() and "non lançable" in out.summary()


def test_never_acts_on_a_disabled_button_even_if_numbers_look_affordable():
    out = decide([_card("Mine de Fer", 40, {"Fer": 1e6}, enabled=False)], RES, T, "Améliorer")
    assert out.decision is None


def test_negative_energy_blocks_mines_but_not_the_power_plant():
    res = Resources(fer=100e6, cristal=50e6, ether=20e6, energie=-30)
    cards = [_card("Mine de Fer", 40, {"Fer": 1e6}), _card("Centrale Énergétique", 27, {"Fer": 50e6, "Cristal": 20e6})]
    out = decide(cards, res, T, "Améliorer")
    assert out.decision.card.name == "Centrale Énergétique"
    assert "énergie négative" in out.summary()


def test_refuses_to_act_when_a_number_or_stock_is_unreadable():
    bad = Card("Mine de Fer", 40, {}, None, "Améliorer", True, unreadable=["Fer '12 To'"])
    assert decide([bad], RES, T, "Améliorer").decision is None
    blind = Resources(fer=None, cristal=50e6, ether=20e6, energie=0)
    assert decide([_card("Mine de Fer", 40, {"Fer": 1e6})], blind, T, "Améliorer").decision is None
    nolevel = Card("Mine de Fer", None, {"Fer": 1e6}, None, "Améliorer", True)
    assert decide([nolevel], RES, T, "Améliorer").decision is None


def test_research_uses_its_own_button_label():
    t = normalized_targets({"Technologie laser": 30})
    cards = [_card("Technologie laser", 22, {"Fer": 1e6}, action="Rechercher")]
    assert decide(cards, RES, t, "Rechercher").decision is not None
    assert decide(cards, RES, t, "Améliorer").decision is None
    locked = [_card("Technologie laser", 22, {"Fer": 1e6}, enabled=False, action="Ressources insuffisantes")]
    assert decide(locked, RES, t, "Rechercher").decision is None


def test_parse_page_and_planets_tolerate_unreadable_numbers():
    page = parse_page(
        {
            "path": "/game/asterion/buildings", "section": "Industrie", "planet": "8-50-Concorde", "coords": "Z25 95 16",
            "resources": {"fer": "58,035 M", "cristal": "22,360 M", "ether": "19,809 M", "energie": "11 014"},
            "cards": [
                {"name": "Mine de Fer", "level": "40", "costs": {"Fer": "201.765M", "Cristal": "50.441M"}, "duration": "1j 2h", "action": "Améliorer", "enabled": False},
                {"name": "Mine X", "level": "", "costs": {"Fer": "12 To"}, "duration": "bientôt", "action": "Améliorer", "enabled": True},
            ],
        }
    )
    assert page.resources.fer == pytest.approx(58.035e6) and page.resources.energie == 11014
    assert page.cards[0].level == 40 and page.cards[0].duration_s == 93600 and not page.cards[0].enabled
    assert page.cards[1].level is None and page.cards[1].unreadable and page.cards[1].duration_s is None
    planets = parse_planets([{"name": "6-79-Egide", "coords": "M69 38 06", "stocks": ["3,4 Md", "1,2 Md", "775 M"]}])
    assert planets[0].stocks == (pytest.approx(3.4e9), pytest.approx(1.2e9), pytest.approx(775e6))
