"""Identifiants enregistrés (chiffrés) et délai réglable entre deux clics de la connexion."""

import asyncio
import types
from dataclasses import replace

import pytest

from app import human
from app.preferences import Preferences, PreferencesError, validate
from app.bot import BotSession
from app.config import Settings
from app.vault import CredentialVault

ID = "joueur@example.com"
PW = "mot-de-passe-test"


# ------------------------------------------------------------------------------ coffre
def test_vault_roundtrip_never_stores_password_in_clear(tmp_path):
    vault = CredentialVault(str(tmp_path))
    assert vault.save(ID, PW) is True
    assert vault.load() == (ID, PW)
    assert PW not in (tmp_path / "account.json").read_text(encoding="utf8")
    assert (tmp_path / "account.key").exists()


def test_vault_clear_removes_everything(tmp_path):
    vault = CredentialVault(str(tmp_path))
    vault.save(ID, PW)
    vault.clear()
    assert vault.load() is None and vault.saved_identifier() == ""
    assert not (tmp_path / "account.json").exists() and not (tmp_path / "account.key").exists()


def test_vault_lost_key_is_reported_not_crashing(tmp_path):
    vault = CredentialVault(str(tmp_path))
    vault.save(ID, PW)
    (tmp_path / "account.key").unlink()
    assert vault.load() is None
    assert vault.problem
    assert vault.saved_identifier() == ID  # l'identifiant (non secret) reste proposé dans le formulaire


def test_vault_corrupt_file_is_reported(tmp_path):
    (tmp_path / "account.json").write_text("pas du json", encoding="utf8")
    vault = CredentialVault(str(tmp_path))
    assert vault.load() is None and vault.problem


def test_vault_without_data_dir_does_nothing():
    vault = CredentialVault(None)
    assert vault.available is False and vault.save(ID, PW) is False and vault.load() is None


# -------------------------------------------------------------------------- bot (mémoire + disque)
def test_credentials_survive_a_restart(settings, tmp_path):
    cfg = replace(settings, data_dir=str(tmp_path))
    BotSession(cfg).set_credentials(ID, PW)
    restarted = BotSession(cfg)  # redémarrage : nouvelle session, même dossier
    assert restarted.has_credentials() and restarted.credentials_identifier() == ID
    assert restarted._creds == (ID, PW)


def test_forget_also_erases_the_saved_ones(settings, tmp_path):
    cfg = replace(settings, data_dir=str(tmp_path))
    bot = BotSession(cfg)
    bot.set_credentials(ID, PW)
    bot.forget_credentials()
    assert not BotSession(cfg).has_credentials()


def test_empty_password_keeps_the_known_one(settings, tmp_path):
    bot = BotSession(replace(settings, data_dir=str(tmp_path)))
    assert bot.set_credentials(ID, None) is False  # rien à garder : le mot de passe est obligatoire
    bot.set_credentials(ID, PW)
    assert bot.set_credentials("autre@example.com", None) is True
    assert bot._creds == ("autre@example.com", PW)


def test_credentials_never_appear_in_the_journal(settings, tmp_path):
    bot = BotSession(replace(settings, data_dir=str(tmp_path)))
    bot.set_credentials(ID, PW)
    text = str(list(bot._logs)) + str(bot.events(100))
    assert "Identifiants enregistrés" in text  # le journal mentionne l'enregistrement, sans rien révéler
    assert PW not in text and ID not in text


# ------------------------------------------------------------------------------ API
def test_form_endpoint_prefills_identifier_but_never_the_password(make_client, tmp_path):
    c = make_client(data_dir=str(tmp_path))
    assert c.get("/api/account/form").json() == {"identifier": "", "has_password": False}
    c.app.state.bot.set_credentials(ID, PW)
    form = c.get("/api/account/form")
    assert form.json() == {"identifier": ID, "has_password": True}
    assert PW not in form.text and PW not in c.get("/api/account").text
    assert c.get("/api/account").json()["saved_on_disk"] is True


def test_posting_without_password_and_none_known_is_refused(make_client, tmp_path):
    c = make_client(data_dir=str(tmp_path))
    res = c.post("/api/account/credentials", json={"identifier": ID})
    assert res.status_code == 422 and "mot de passe" in res.text


def test_ui_protected_flag(make_client, tmp_path):
    assert make_client(data_dir=str(tmp_path)).get("/api/account").json()["ui_protected"] is False


# ------------------------------------------------------------------------ délai de connexion
def test_login_delay_default_and_validation():
    assert validate({})["login_delay"] == {"min_seconds": 0.4, "max_seconds": 1.4}
    ok = validate({"login_delay": {"min_seconds": 2, "max_seconds": 3.5}})
    assert ok["login_delay"] == {"min_seconds": 2.0, "max_seconds": 3.5}
    for bad in (
        {"min_seconds": 3, "max_seconds": 2},  # minimum après le maximum
        {"min_seconds": -1, "max_seconds": 2},
        {"min_seconds": 0, "max_seconds": 61},
        {"min_seconds": True, "max_seconds": 2},
        {"min_seconds": "1", "max_seconds": 2},
        {"min_seconds": 1},
    ):
        with pytest.raises(PreferencesError):
            validate({"login_delay": bad})


def test_login_delay_is_saved_and_reloaded(tmp_path):
    prefs = Preferences(str(tmp_path))
    prefs.update({"login_delay": {"min_seconds": 2.0, "max_seconds": 5.0}})
    assert Preferences(str(tmp_path)).get()["login_delay"] == {"min_seconds": 2.0, "max_seconds": 5.0}


def test_login_delay_partial_update_keeps_the_other_bound(tmp_path):
    prefs = Preferences(str(tmp_path))
    prefs.update({"login_delay": {"max_seconds": 9}})
    assert prefs.get()["login_delay"] == {"min_seconds": 0.4, "max_seconds": 9.0}


def test_bot_reads_the_chosen_login_delay(settings, tmp_path):
    bot = BotSession(replace(settings, data_dir=str(tmp_path)))
    bot.supervisor.prefs.update({"login_delay": {"min_seconds": 2, "max_seconds": 3}})
    assert bot._login_delay() == (2.0, 3.0)


def test_think_uses_the_given_delay(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(human, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
    s = Settings(human_mode=True, human_delay_min=0.4, human_delay_max=1.4)
    for _ in range(40):
        asyncio.run(human.think(s, (2.0, 3.0)))
    assert recorded and all(2.0 <= r <= 3.0 for r in recorded)
    recorded.clear()
    asyncio.run(human.think(s))  # sans délai donné : valeurs habituelles
    assert 0.4 <= recorded[0] <= 1.4
