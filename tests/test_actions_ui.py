"""Page Actions dans un vrai navigateur : onglets, listes intégrées (sans saisie de noms), enregistrement."""

import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.main import create_app
from tests.conftest import _free_port

TABS = ["run", "account", "schedule", "constructions", "research", "state", "log", "settings"]


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


def test_the_on_off_switch_is_in_the_header_on_every_page_and_every_tab(ui):
    page, _, _ = ui
    for tab in TABS:
        page.click(f"#tab-{tab}")
        assert page.locator("header.top #power").is_visible()
    base = page.url.split("/actions")[0]
    page.goto(base + "/vision")
    assert page.locator("header.top #power").is_visible()
    # la page Connexion redirige vers Vision quand on est déjà connecté : on vérifie son contenu
    html = page.request.get(base + "/").text()
    assert 'id="power"' in html and "/static/power.js" in html


def test_the_switch_turns_the_bot_on_then_off(ui):
    page, _, app = ui
    power = page.locator("#power")
    assert power.inner_text() == "Bot : OFF" and power.get_attribute("aria-pressed") == "false"
    power.click()
    page.wait_for_function("document.querySelector('#power').textContent.includes('ON')", timeout=15000)
    assert app.state.bot.automation.summary()["running"] is True
    assert page.locator("#power").get_attribute("aria-pressed") == "true"
    page.locator("#power").click()
    page.wait_for_function("document.querySelector('#power').textContent.includes('OFF')", timeout=15000)
    assert app.state.bot.automation.summary()["running"] is False


def test_account_tab_keeps_credentials_in_memory_and_never_shows_them_again(ui):
    page, _, app = ui
    page.click("#tab-account")
    page.fill("#acct-id", "titulaire.secret@example.com")
    page.fill("#acct-pw", "mot-de-passe-tres-secret")
    page.click("#acct-save")
    page.wait_for_function("document.querySelector('#acct-status').textContent.includes('en mémoire (ti***)')", timeout=5000)
    assert page.input_value("#acct-pw") == ""  # le mot de passe ne reste pas dans la page
    assert app.state.bot.has_credentials()
    assert "titulaire.secret" not in page.locator("#acct-status").inner_text()
    page.click("#acct-forget")
    page.wait_for_function("document.querySelector('#acct-status').textContent.includes('Aucun identifiant')", timeout=5000)
    assert not app.state.bot.has_credentials()


def test_universe_is_chosen_in_a_list_and_saved(ui):
    page, _, app = ui
    app.state.bot.automation.store.add_catalog("universes", ["Asterion", "Kael"])
    page.reload()
    page.wait_for_selector("#run-status")
    page.click("#tab-account")
    assert page.locator("#universe option").all_inner_texts() == ["Aucun : me demander", "Asterion", "Kael"]
    page.select_option("#universe", "Asterion")
    time.sleep(1.0)
    assert app.state.bot.automation.store.get()["universe"] == "Asterion"


def test_selected_tab_is_remembered_after_reload(ui):
    page, _, _ = ui
    page.click("#tab-research")
    page.reload()
    page.wait_for_selector("#run-status")
    assert page.locator("#panel-research").is_visible()


def _saved(app):
    return app.state.bot.automation.store.get()


def _wait_saved(page, seconds=1.0):
    time.sleep(seconds)  # l'interface enregistre ~0,5 s après la dernière saisie


def test_buildings_and_research_come_from_built_in_lists_without_any_name_field(ui):
    page, _, _ = ui
    page.click("#tab-constructions")
    names = page.locator("#cons-add-name option").all_text_contents()[1:]
    assert len(names) == 16 and names[:2] == ["Mine de Fer", "Extracteur de Cristal"]
    assert {"Industrie", "Infrastructures"} == {g.get_attribute("label") for g in page.locator("#cons-add-name optgroup").all()}
    assert "Tour à résonance" in names and "Terraformeur" in names
    assert page.locator("#panel-constructions input[type=text]").count() == 0  # aucune saisie libre de nom
    page.click("#tab-research")
    research = page.locator("#res-goals .goal-name").all_inner_texts()
    assert len(research) == 23 and "Ingénierie minière" in research
    assert page.locator("#res-goals input:not([type=number])").count() == 0


def test_the_player_builds_an_ordered_list_of_upgrades(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    for name, level in (("Mine de Fer", "45"), ("Centrale Énergétique", "30"), ("Caserne", "20")):
        page.select_option("#cons-add-name", name)
        page.fill("#cons-add-level", level)
        page.click("#cons-add")
    assert page.locator("#cons-queue .queue-row .goal-name").all_inner_texts() == ["Mine de Fer", "Centrale Énergétique", "Caserne"]
    assert "Mine de Fer" not in page.locator("#cons-add-name option").all_text_contents()  # déjà dans la liste
    page.locator("#cons-queue .queue-row").nth(2).locator("button[title^='Monter']").click()
    page.locator("#cons-queue .queue-row").nth(1).locator("button[title^='Monter']").click()  # Caserne passe en premier
    page.locator("#cons-queue .queue-row").nth(2).locator("button[title='Retirer de la liste']").click()  # retire Centrale
    page.locator("#cons-queue .queue-row").nth(1).locator("input[type=number]").fill("46")
    _wait_saved(page)
    assert _saved(app)["constructions"]["queue"] == [{"name": "Caserne", "level": 20}, {"name": "Mine de Fer", "level": 46}]
    assert page.locator("#cons-queue .queue-row .rank").all_inner_texts() == ["1", "2"]
    assert page.locator("#cons-queue .queue-row").nth(0).locator("button[title^='Monter']").is_disabled()  # le premier ne monte plus


def test_research_goals_are_saved_and_empty_means_no_goal(ui):
    page, _, app = ui
    page.click("#tab-research")
    page.fill("#res-goals [data-goal='Technologie laser']", "25")
    page.fill("#res-goals [data-goal='Technologie ionique']", "20")
    page.check("#res-enabled")
    page.fill("#res-goals [data-goal='Technologie ionique']", "")
    _wait_saved(page)
    assert _saved(app)["research"] == {"enabled": True, "targets": {"Technologie laser": 25}, "funding_planet": ""}


def test_all_planets_or_a_single_selected_planet(ui):
    page, _, app = ui
    page.click("#tab-state")
    page.click("#read-state")
    page.wait_for_function("document.querySelector('#read-msg').textContent.includes('terminée')", timeout=60000)
    page.click("#tab-constructions")
    assert page.locator("#scope-all").is_checked() and page.locator("#cons-planet").is_disabled()
    assert page.locator("#cons-planet option").all_inner_texts()[1:] == ["6-79-Egide", "8-50-Concorde", "9-169-Strate"]
    page.check("#scope-one")
    assert page.locator("#cons-planet").is_enabled() and page.locator("#cons-planet").input_value() == "6-79-Egide"  # première proposée
    page.select_option("#cons-planet", "9-169-Strate")
    _wait_saved(page)
    cons = _saved(app)["constructions"]
    assert (cons["scope"], cons["planet"]) == ("one", "9-169-Strate")
    page.check("#scope-all")
    _wait_saved(page)
    cons = _saved(app)["constructions"]
    assert (cons["scope"], cons["planet"]) == ("all", "") and page.locator("#cons-planet").is_disabled()
    # niveau actuel lu dans le jeu, affiché dans la liste
    page.select_option("#cons-add-name", "Mine de Fer")
    page.fill("#cons-add-level", "45")
    page.click("#cons-add")
    assert "actuel : 40" in page.locator("#cons-queue .queue-row").first.inner_text()
    page.click("#tab-research")
    assert "8-50-Concorde" in page.locator("#res-planet option").all_inner_texts()


def test_the_waiting_timer_is_set_in_minutes_and_saved(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    assert page.input_value("#cons-recheck") == "15"
    page.fill("#cons-recheck", "30")
    _wait_saved(page)
    assert _saved(app)["constructions"]["recheck_minutes"] == 30.0


def test_planets_waiting_for_resources_are_listed_with_their_next_check(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    assert "Aucune planète suivie" in page.locator("#cons-pending").inner_text()
    page.select_option("#cons-add-name", "Mine de Fer")
    page.fill("#cons-add-level", "45")
    page.click("#cons-add")
    page.check("#cons-enabled")
    _wait_saved(page)
    page.click("#power")  # ON : un premier cycle (à blanc) vérifie les planètes
    page.wait_for_selector("#cons-pending table", timeout=60000)
    page.wait_for_function(
        "document.querySelectorAll('#cons-pending tr').length >= 4", timeout=60000)  # en-tête + les trois planètes
    text = page.locator("#cons-pending").inner_text()
    assert "8-50-Concorde" in text and "en attente de ressources" in text and "dans" in text
    assert "réalisable (mode à blanc)" in text  # Egide pourrait être améliorée
    page.click("#power")  # OFF


def test_time_windows_can_be_edited(ui):
    page, _, app = ui
    page.click("#tab-schedule")
    page.check("#sched-enabled")
    page.fill("#windows .window-row:first-child input[type=time] >> nth=0", "09:30")
    time.sleep(1.0)
    saved = app.state.bot.automation.store.get()["schedule"]
    assert saved["enabled"] is True and saved["windows"][0]["start"] == "09:30"
    assert page.locator("#tz option").count() > 20  # fuseaux horaires proposés dans une liste
