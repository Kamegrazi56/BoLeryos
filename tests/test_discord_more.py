"""Commandes Discord ajoutées (GRAZI-57) : /prochain /flottes /planetes /pause /connexion /scan /fonction statut|lancer,
et bilan de fin de journée envoyé en message privé."""

import asyncio
import time
from types import SimpleNamespace

import pytest

from app import discord_scan
from app.bot import BotSession
from app.day_report import DayReport
from app.discord_commands import COMMAND_DEFINITIONS, CommandRunner
from app.discord_gateway import DiscordClient, DiscordService, parse_command
from app.pause import BotPause
from app.discord_scan import Reading, scan_all
from app.discord_views import amount, span
from app.features import FEATURES
from tests.mock_discord import OWNER_ID
from tests.test_discord import _command, _drive, _manager, _runner, _store, _wait, fake_discord  # noqa: F401 (fixture)

TIMER = {"enabled": True, "min_minutes": 30, "max_minutes": 60}


def _run(runner, command, sub="", **options):
    return asyncio.run(runner.execute((command, sub), options)).text


# ------------------------------------------------------------------ définitions
def test_new_commands_are_declared_and_required_options_come_first():
    names = {c["name"] for c in COMMAND_DEFINITIONS}
    assert {"prochain", "flottes", "planetes", "pause", "connexion", "scan"} <= names
    subs = {s["name"] for c in COMMAND_DEFINITIONS if c["name"] == "fonction" for s in c["options"]}
    assert {"statut", "lancer"} <= subs
    for command in COMMAND_DEFINITIONS:
        groups = [command["options"]] + [s["options"] for s in command["options"] if s.get("type") == 1]
        for options in groups:
            flags = [bool(o.get("required")) for o in options if o.get("type") != 1]
            assert flags == sorted(flags, reverse=True), command["name"]  # Discord exige les options obligatoires d'abord


def test_parse_command_keeps_numbers_and_switches_as_text():
    data = {"name": "pause", "options": [{"type": 4, "name": "minutes", "value": 30}]}
    assert parse_command(data) == (("pause", ""), {"minutes": "30"})
    assert parse_command({"name": "flottes", "options": [{"type": 5, "name": "actualiser", "value": True}]})[1] == {"actualiser": "True"}


def test_views_write_durations_and_amounts_for_humans():
    assert [span(s) for s in (5, 720, 3900, 200000)] == ["5 s", "12 min", "1 h 05", "2 j 7 h"]
    assert amount(1234567.0) == "1 234 567" and amount(None) == "?"


# ------------------------------------------------------------------ /prochain et /fonction statut|lancer
def test_next_lists_enabled_features_by_due_time_and_says_when_the_bot_is_off(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    bot.scheduler.update("constructions", TIMER)
    assert "OFF" in _run(runner, "prochain") and "Constructions de bâtiments" in _run(runner, "prochain")
    assert "dans" not in _run(runner, "prochain")  # sur OFF, aucun minuteur ne court : on ne promet pas d'heure

    async def on():
        await runner.execute(("bot", "demarrer"), {})
        return (await runner.execute(("prochain", ""), {})).text

    text = asyncio.run(on())
    assert "Bot ON" in text and "Planning :" in text and "• Constructions de bâtiments :" in text


def test_function_status_shows_rule_last_pass_and_disabled_names(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    bot.scheduler.update("constructions", TIMER)
    text = _run(runner, "fonction", "statut")
    assert "Constructions de bâtiments — toutes les 30 à 60 min ; dernier passage : aucun" in text
    assert "Désactivées :" in text and "Recherches du Laboratoire" in text


def test_function_launch_wakes_an_enabled_feature_and_explains_every_refusal(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    assert "désactivée" in _run(runner, "fonction", "lancer", fonction="constructions")
    bot.scheduler.update("constructions", TIMER)
    assert "dès que le bot sera sur ON" in _run(runner, "fonction", "lancer", fonction="Constructions de bâtiments")
    assert any("lancée par une commande Discord" in e["msg"] for e in bot.events(5))
    assert "inconnue" in _run(runner, "fonction", "lancer", fonction="nimporte")


def test_function_launch_brings_the_next_pass_forward(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    bot.scheduler.update("test", TIMER)
    bot.scheduler._runtime["test"].next_at = bot.scheduler._clock() + 3600
    assert "passe au prochain tour" in asyncio.run(_launch_on(runner))
    assert bot.scheduler.summary()["features"][-1]["next_in_s"] < 5


async def _launch_on(runner):
    await runner.execute(("bot", "demarrer"), {})
    return (await runner.execute(("fonction", "lancer"), {"fonction": "test"})).text


# ------------------------------------------------------------------ /flottes et /planetes
def test_fleets_show_the_last_reading_without_the_bots_own_fleets(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    assert "Aucun relevé" in _run(runner, "flottes")
    now = time.time()
    flight = {"id": "a1", "kind": "Transport", "origin": {"name": "Egide", "coords": "1:2:3"}, "destination": {"name": "Orion", "coords": "4:5:6"},
              "arrival_at": now + 3700, "can_recall": True, "excluded": False}
    bot.fleets.set_snapshot([flight, {**flight, "id": "b2", "kind": "Fouille", "excluded": True}, {**flight, "id": "c3", "arrival_at": now - 50}], now - 120)
    text = _run(runner, "flottes")
    assert "Transport · Egide 1:2:3 → Orion 4:5:6 : arrive dans 1 h 01 — rappelable" in text and "relevé il y a 2 min" in text
    assert "Fouille" not in text and text.count("• ") == 1  # exclue, et déjà arrivée depuis le relevé


def test_fleets_refresh_reads_the_game_first_and_keeps_the_old_reading_on_failure(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    bot.fleets.set_snapshot([], time.time())
    asked = []

    async def scan(source):
        asked.append(source)
        return {"error": "le bot n'est pas dans une partie"}

    bot.fleet.scan = scan
    text = _run(runner, "flottes", actualiser="True")
    assert asked == ["Discord"] and "Relevé impossible (le bot n'est pas dans une partie)" in text and "Aucune flotte en vol" in text
    _run(runner, "flottes")
    assert asked == ["Discord"]  # sans l'option, on ne lit pas le jeu


def test_planets_show_stocks_and_refresh_on_request(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    assert "Aucune planète" in _run(runner, "planetes")
    sup = bot.supervisor
    sup.scanned_planets = [{"name": "Egide", "coords": "1:2:3", "fer": 1500.0, "cristal": 20, "ether": None}]
    sup.scan_at = time.time() - 60
    text = _run(runner, "planetes")
    assert "• Egide 1:2:3 : Fer 1 500 · Cristal 20 · Éther ?" in text and "1 planète(s) (relevé il y a 1 min)" in text
    assert "Scan impossible" in _run(runner, "planetes", actualiser="True")  # pas dans une partie : message clair, ancien relevé gardé
    assert "Egide" in _run(runner, "planetes", actualiser="True")


# ------------------------------------------------------------------ /pause
def test_pause_turns_the_bot_off_then_back_on_by_itself(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    gate = asyncio.Event()

    async def fake_sleep(seconds):
        assert seconds == 90 * 60
        await gate.wait()

    bot.scheduler.pause = BotPause(bot.scheduler, fake_sleep)

    async def scenario():
        assert "déjà sur OFF" in (await runner.execute(("pause", ""), {"minutes": "5"})).text
        bot.scheduler.start()
        text = (await runner.execute(("pause", ""), {"minutes": "90"})).text
        assert not bot.scheduler.on and "repasse sur ON à" in text and bot.scheduler.pause.active
        gate.set()
        await _wait_async(lambda: bot.scheduler.on)
        assert not bot.scheduler.pause.active

    asyncio.run(scenario())
    assert any("Fin de la pause" in e["msg"] for e in bot.events(10))


async def _wait_async(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "délai dépassé"
        await asyncio.sleep(0.01)


def test_a_later_order_cancels_the_pause_and_bad_durations_are_refused(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path, confirm=False)
    bot = manager.resolve()
    bot.scheduler.pause = BotPause(bot.scheduler, lambda s: asyncio.sleep(3600))

    async def scenario():
        bot.scheduler.start()
        await runner.execute(("pause", ""), {"minutes": "30", "instance": "asterion"})
        assert bot.scheduler.pause.active
        await runner.execute(("bot", "demarrer"), {"instance": "asterion"})  # le dernier ordre l'emporte
        assert not bot.scheduler.pause.active and bot.scheduler.on
        for bad, word in (("0", "refusée"), ("721", "refusée"), ("abc", "illisible"), ("", "illisible")):
            assert word in (await runner.execute(("pause", ""), {"minutes": bad})).text

    asyncio.run(scenario())


def test_the_pause_does_not_restart_a_bot_whose_instance_was_stopped(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    gate = asyncio.Event()
    bot.scheduler.pause = BotPause(bot.scheduler, lambda s: gate.wait())

    async def scenario():
        bot.scheduler.start()
        await runner.execute(("pause", ""), {"minutes": "10"})
        await manager.stop("asterion")
        gate.set()
        await asyncio.sleep(0.05)
        assert not bot.scheduler.on

    asyncio.run(scenario())


# ------------------------------------------------------------------ /connexion
def test_reconnect_needs_credentials_then_restarts_the_supervision(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    calls = []
    bot.supervisor.reset_connection = lambda: calls.append("reset")
    bot.supervisor.ensure_keeper = lambda: calls.append("keeper")
    bot.restore_credentials = lambda: False
    assert "aucun identifiant" in _run(runner, "connexion") and calls == []
    bot.has_credentials = lambda: True
    assert "connexion relancée" in _run(runner, "connexion", instance="asterion") and calls == ["reset", "keeper"]
    assert any("Connexion relancée par une commande Discord" in e["msg"] for e in bot.events(5))


# ------------------------------------------------------------------ /scan
def _stub_bot(enabled: set[str]):
    logs = []
    return SimpleNamespace(
        supervisor=SimpleNamespace(game_blocker=lambda: None),
        scheduler=SimpleNamespace(store=SimpleNamespace(get=lambda fid: {"enabled": fid in enabled})),
        log=lambda msg, *a, **k: logs.append(msg), logs=logs)


def test_scan_reads_planets_first_then_only_what_enabled_features_need(monkeypatch):
    order = []

    def reading(label, error="", features=()):
        async def run(bot):
            order.append(label)
            return {"error": error}
        return Reading(label, run, features)

    monkeypatch.setattr(discord_scan, "READINGS", (
        reading("Planètes"), reading("Vaisseaux", features=("ships",)), reading("Piliers", "le jeu a refusé", ("pillars",)),
        reading("Fouilles", features=("excavations",))))
    bot = _stub_bot({"ships", "pillars"})
    text = asyncio.run(scan_all(bot))
    assert order == ["Planètes", "Vaisseaux", "Piliers"]  # « Fouilles » : fonctionnalité éteinte, pas lue
    assert text.splitlines() == ["✅ Planètes", "✅ Vaisseaux", "❌ Piliers : le jeu a refusé"]
    assert "2 lecture(s) réussie(s), 1 en échec" in bot.logs[0]


def test_scan_waits_for_a_running_pass_and_survives_a_crashing_reading(monkeypatch):
    monkeypatch.setattr(discord_scan, "BUSY_WAIT", 0.0)
    tries = []

    async def busy_then_ok(bot):
        tries.append(1)
        return {"error": "Une fonctionnalité est en cours, réessayez dans un instant" if len(tries) < 3 else ""}

    async def crash(bot):
        raise RuntimeError("secret interne")

    monkeypatch.setattr(discord_scan, "READINGS", (Reading("Planètes", busy_then_ok), Reading("Cassée", crash)))
    text = asyncio.run(scan_all(_stub_bot(set())))
    assert len(tries) == 3 and text.splitlines()[0] == "✅ Planètes"
    assert text.splitlines()[1] == "❌ Cassée : erreur technique (RuntimeError)" and "secret" not in text


def test_scan_does_nothing_when_the_bot_is_not_in_a_game(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    assert "Scan impossible : le bot n'est pas dans une partie" in _run(runner, "scan")


def test_every_reading_is_wired_to_a_real_method_and_known_features(settings, tmp_path):
    """Chaque lecture appelle une vraie méthode du bot (une faute de frappe se verrait ici) ; hors partie, toutes refusent proprement."""
    bot = _manager(settings, tmp_path).resolve()
    ids = {f.id for f in FEATURES}
    for reading in discord_scan.READINGS:
        assert set(reading.features) <= ids, reading.label
        result = asyncio.run(reading.run(bot))
        assert "pas dans une partie" in str(result.get("error")), reading.label


# ------------------------------------------------------------------ bilan de fin de journée
def test_day_report_counts_passes_per_feature_and_starts_over_each_day():
    day = ["2026-11-27"]
    report = DayReport(lambda: day[0])
    assert not report.has_activity()
    report.record("Fouilles", True, "2 Fouilles lancées")
    report.record("Fouilles", False, "erreur technique (X)")
    report.record("Missions du jour", True, "3 récompense(s) récupérée(s)")
    text = report.text()
    assert "Bilan du 27/11/2026" in text
    assert "• Fouilles : 1 passage(s), 1 en échec — dernier : erreur technique (X)" in text and "• Missions du jour : 1 passage(s)" in text
    day[0] = "2026-11-28"
    assert not report.has_activity()


def test_every_scheduler_pass_is_recorded_in_the_day_report(settings, tmp_path):
    bot = _manager(settings, tmp_path).resolve()
    bot.scheduler.update("test", TIMER)
    feature = next(f for f in FEATURES if f.id == "test")
    asyncio.run(bot.scheduler._run(feature))
    assert "• Test (ne fait rien) : 1 passage(s)" in bot.day_report.text()


def test_the_report_is_sent_once_logged_and_cleared(settings, tmp_path):
    bot = _manager(settings, tmp_path).resolve()
    sent = []

    async def sink(text):
        sent.append(text)
        return True

    asyncio.run(bot._send_day_report())  # aucun canal : rien à faire
    bot.on_day_end = sink
    asyncio.run(bot._send_day_report())  # canal, mais aucune activité : pas de message vide
    assert sent == []
    bot.day_report.record("Fouilles", True, "ok")
    asyncio.run(bot._send_day_report())
    asyncio.run(bot._send_day_report())
    assert len(sent) == 1 and "Fouilles" in sent[0]
    assert any("Bilan de fin de journée envoyé" in e["msg"] for e in bot.events(10))


@pytest.mark.parametrize("outcome, expected", [(False, "non envoyé"), (RuntimeError("x"), "envoi impossible"), (None, "")])
def test_a_failed_or_refused_report_is_logged_without_breaking_anything(settings, tmp_path, outcome, expected):
    bot = _manager(settings, tmp_path).resolve()

    async def sink(text):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    bot.on_day_end = sink
    bot.day_report.record("Fouilles", True, "ok")
    asyncio.run(bot._send_day_report())
    messages = " ".join(e["msg"] for e in bot.events(10))
    assert (expected in messages) if expected else "Bilan" not in messages
    assert not bot.day_report.has_activity()


def test_the_end_of_the_last_slot_sends_the_report_after_closing_the_game(settings, tmp_path, monkeypatch):
    bot = _manager(settings, tmp_path).resolve()
    sent = []

    async def sink(text):
        sent.append(text)
        return True

    bot.on_day_end = sink
    bot.day_report.record("Fouilles", True, "ok")
    monkeypatch.setattr(bot, "_last_slot_of_day", lambda: False)
    asyncio.run(bot.pause_session("hors planning"))
    assert sent == []  # une plage suivante existe aujourd'hui : pas encore la fin de journée
    monkeypatch.setattr(bot, "_last_slot_of_day", lambda: True)
    asyncio.run(bot.pause_session("hors planning"))
    assert len(sent) == 1 and "Fouilles" in sent[0]


# ------------------------------------------------------------------ Discord : message privé et commande de bout en bout
def test_owners_receive_the_report_as_a_private_message(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        manager.report_sink = client.notify_owners
        info = manager.summary()["instances"][0]
        assert await manager._send_day_report(info["id"], "Bilan du jour") is True

    _drive(settings, tmp_path, fake_discord, steps)
    assert fake_discord.dms == [{"to": OWNER_ID, "content": "**Asterion**\nBilan du jour"}]


def test_a_refused_private_message_is_reported_as_not_sent(settings, tmp_path, fake_discord):
    fake_discord.dm_refused = True

    async def steps(manager, client):
        assert await client.notify_owners("texte") is False

    _drive(settings, tmp_path, fake_discord, steps)
    assert fake_discord.dms == []


def test_without_a_connection_there_is_nothing_to_send(settings, tmp_path):
    store, manager = _store(tmp_path), _manager(settings, tmp_path)
    service = DiscordService(store, CommandRunner(manager, lambda: True))
    assert asyncio.run(service.notify("texte")) is None  # pilotage coupé : le bot n'en parle pas
    assert asyncio.run(DiscordClient(store, CommandRunner(manager, lambda: True)).notify_owners("texte")) is False
    assert asyncio.run(manager._send_day_report(manager.default_id, "texte")) is None


def test_a_new_command_answers_through_discord(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_command("prochain"))
        await _wait(lambda: fake_discord.edits)

    _drive(settings, tmp_path, fake_discord, steps)
    assert "Asterion" in fake_discord.edits[0]["body"]["content"]
    assert {"prochain", "flottes", "planetes", "pause", "connexion", "scan"} <= {c["name"] for c in fake_discord.registered}
