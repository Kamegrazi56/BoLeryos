"""Page Actions dans un vrai navigateur : onglets, listes intégrées (sans saisie de noms), enregistrement."""

import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.main import create_app
from tests.conftest import _free_port

TABS = ["run", "schedule", "constructions", "research", "state", "log", "settings"]


@pytest.fixture
def ui(settings, game):
    """Application réelle servie en HTTP, avec un bot « connecté » sur le faux jeu, et un navigateur."""
    from dataclasses import replace

    from app.bot import State

    app = create_app(replace(settings, login_url=game.url + "/game/asterion/buildings"))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    bot = app.state.bot
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=settings.chromium_path, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors = []
        page.on("dialog", lambda d: d.accept())
        # Comme un utilisateur : l'ouverture de la page Vision démarre le navigateur du bot (dans la boucle du serveur).
        page.goto(f"http://127.0.0.1:{port}/vision")
        for _ in range(300):
            if bot._page is not None and "/game/" in bot._page.url:
                break
            time.sleep(0.1)
        bot._set(State.LOGGED_IN, "test")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{port}/actions")
        page.wait_for_selector("#run-status")
        yield page, errors, app
        browser.close()
    server.should_exit = True
    thread.join(timeout=5)


def test_everything_is_in_tabs_and_only_one_panel_shows_at_a_time(ui):
    page, errors, _ = ui
    assert [t.get_attribute("data-tab") for t in page.locator("[role=tab]").all()] == TABS
    for tab in TABS:
        page.click(f"#tab-{tab}")
        visible = [t for t in TABS if page.locator(f"#panel-{t}").is_visible()]
        assert visible == [tab], f"onglet {tab} : panneaux visibles {visible}"
        assert page.locator(f"#tab-{tab}").get_attribute("aria-selected") == "true"
    assert errors == []


def test_start_and_emergency_stop_stay_visible_on_every_tab(ui):
    page, _, _ = ui
    for tab in TABS:
        page.click(f"#tab-{tab}")
        assert page.locator("#start").is_visible() and page.locator("#stop").is_visible()


def test_selected_tab_is_remembered_after_reload(ui):
    page, _, _ = ui
    page.click("#tab-research")
    page.reload()
    page.wait_for_selector("#run-status")
    assert page.locator("#panel-research").is_visible()


def test_building_and_research_lists_are_built_in_without_any_name_field(ui):
    page, _, _ = ui
    page.click("#tab-constructions")
    names = page.locator("#cons-goals .goal-name").all_inner_texts()
    assert names[:2] == ["Mine de Fer", "Extracteur de Cristal"] and len(names) == 16
    assert {"Industrie", "Infrastructures"} == {h.text_content() for h in page.locator("#cons-goals h3").all()}
    assert "Tour à résonance" in names and "Terraformeur" in names
    page.click("#tab-research")
    research = page.locator("#res-goals .goal-name").all_inner_texts()
    assert len(research) == 23 and "Ingénierie minière" in research
    # aucune saisie libre de nom : seuls des champs numériques dans les listes
    for panel in ("cons-goals", "res-goals"):
        assert page.locator(f"#{panel} input[type=text]").count() == 0
        assert page.locator(f"#{panel} input:not([type=number])").count() == 0


def test_goals_are_saved_on_the_server_and_empty_means_no_goal(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    page.fill("#cons-goals [data-goal='Mine de Fer']", "45")
    page.fill("#cons-goals [data-goal='Caserne']", "20")
    page.check("#cons-enabled")
    page.fill("#cons-goals [data-goal='Caserne']", "")  # vidé : plus d'objectif
    page.click("#tab-research")
    page.fill("#res-goals [data-goal='Technologie laser']", "25")
    page.check("#res-enabled")
    page.wait_for_function("document.querySelector('#save-msg').textContent.includes('enregistrés')", timeout=5000)
    time.sleep(0.8)
    saved = app.state.bot.automation.store.get()
    assert saved["constructions"]["targets"] == {"Mine de Fer": 45} and saved["constructions"]["enabled"] is True
    assert saved["research"]["targets"] == {"Technologie laser": 25} and saved["research"]["enabled"] is True


def test_planets_levels_and_exceptions_come_from_the_game_not_from_typing(ui):
    page, _, app = ui
    page.click("#tab-state")
    page.click("#read-state")
    page.wait_for_function("document.querySelector('#read-msg').textContent.includes('terminée')", timeout=60000)
    page.click("#tab-constructions")
    # niveaux actuels lus dans le jeu
    row = page.locator("#cons-goals .goal-row", has_text="Mine de Fer")
    assert "actuel : 40" in row.inner_text()
    # liste des planètes à cocher ; exception pour une planète choisie dans une liste
    assert page.locator("#ov-planet option").all_inner_texts()[1:] == ["6-79-Egide", "8-50-Concorde", "9-169-Strate"]
    page.select_option("#ov-planet", "6-79-Egide")
    page.fill("#ov-goals [data-goal='Mine de Fer']", "50")
    time.sleep(1.0)
    assert app.state.bot.automation.store.get()["constructions"]["overrides"] == {"6-79-Egide": {"Mine de Fer": 50}}
    page.uncheck("#cons-planets input")  # « Toutes les planètes » décoché : une case par planète, toutes cochées
    assert page.locator("#cons-planets label.planet").count() == 3
    for other in ("Concorde", "Strate"):
        page.locator("#cons-planets label.planet", has_text=other).locator("input").uncheck()
    time.sleep(1.0)
    assert app.state.bot.automation.store.get()["constructions"]["planets"] == ["6-79-Egide"]
    # planète qui finance les recherches : liste déroulante
    page.click("#tab-research")
    assert "8-50-Concorde" in page.locator("#res-planet option").all_inner_texts()


def test_time_windows_can_be_edited(ui):
    page, _, app = ui
    page.click("#tab-schedule")
    page.check("#sched-enabled")
    page.fill("#windows .window-row:first-child input[type=time] >> nth=0", "09:30")
    time.sleep(1.0)
    saved = app.state.bot.automation.store.get()["schedule"]
    assert saved["enabled"] is True and saved["windows"][0]["start"] == "09:30"
    assert page.locator("#tz option").count() > 20  # fuseaux horaires proposés dans une liste
