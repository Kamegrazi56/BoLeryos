"""Compte du joueur : identifiants et cookies de session, communs à toutes les sessions de bot."""

import asyncio
from dataclasses import replace

from app.account import Account
from app.bot import BotSession
from app.browser_host import BrowserHost

ID = "joueur@example.com"
PW = "mot-de-passe-test"


# ------------------------------------------------------------------ identifiants
def test_remember_keeps_credentials_in_memory_and_on_disk(tmp_path):
    account = Account(str(tmp_path))
    assert not account.has_credentials() and account.identifier() == ""
    assert account.remember(ID, PW) is True  # enregistré sur le disque
    assert account.has_credentials() and account.identifier() == ID and account.creds == (ID, PW)
    reloaded = Account(str(tmp_path))  # redémarrage : même dossier
    assert reloaded.creds == (ID, PW)


def test_remember_without_data_dir_is_memory_only():
    account = Account(None)
    assert account.remember(ID, PW) is False
    assert account.creds == (ID, PW)


def test_remember_without_password_keeps_the_known_one(tmp_path):
    account = Account(str(tmp_path))
    assert account.remember(ID, None) is None  # rien à garder : refusé
    account.remember(ID, PW)
    assert account.remember("autre@example.com", None) is True
    assert account.creds == ("autre@example.com", PW)


def test_forget_erases_memory_and_disk(tmp_path):
    account = Account(str(tmp_path))
    assert account.forget() is False  # rien à oublier
    account.remember(ID, PW)
    assert account.forget() is True
    assert account.creds is None and not Account(str(tmp_path)).has_credentials()


def test_logout_drops_memory_but_keeps_the_saved_identifier(tmp_path):
    account = Account(str(tmp_path))
    account.remember(ID, PW)
    account.drop_credentials_from_memory()
    assert not account.has_credentials()
    assert account.identifier() == ID  # l'identifiant enregistré pré-remplit toujours le formulaire
    assert account.restore() is True and account.creds == (ID, PW)


# ------------------------------------------------------------------ session du navigateur
def test_clear_session_removes_the_cookies_but_not_the_credentials(tmp_path):
    account = Account(str(tmp_path))
    account.remember(ID, PW)
    account.session_store.save({"cookies": [{"name": "sid", "value": "x", "domain": "127.0.0.1", "path": "/"}], "origins": []})
    assert account.session_store.load() is not None
    account.clear_session()
    assert account.session_store.load() is None and account.has_credentials()


# ------------------------------------------------------------------ partagé entre sessions de bot
def test_two_sessions_share_one_account(settings, tmp_path):
    cfg = replace(settings, data_dir=str(tmp_path))
    account = Account(cfg.data_dir)
    first, second = BotSession(cfg, BrowserHost(cfg), account), BotSession(cfg, BrowserHost(cfg), account)
    assert first.account is second.account
    first.set_credentials(ID, PW)
    assert second.has_credentials() and second.credentials_identifier() == ID and second._creds == (ID, PW)
    second.forget_credentials()
    assert not first.has_credentials()


def test_a_session_without_an_account_gets_its_own(settings, tmp_path):
    cfg = replace(settings, data_dir=str(tmp_path))
    bot = BotSession(cfg)
    bot.set_credentials(ID, PW)
    assert Account(cfg.data_dir).creds == (ID, PW)  # même dossier : c'est le compte enregistré


def test_a_new_session_starts_from_the_account_cookies(settings, tmp_path):
    cfg = replace(settings, data_dir=str(tmp_path))
    account = Account(cfg.data_dir)
    account.session_store.save({"cookies": [{"name": "sid", "value": "partage", "domain": "127.0.0.1", "path": "/"}], "origins": []})

    async def scenario():
        host = BrowserHost(cfg)
        bot = BotSession(cfg, host, account)
        try:
            await bot.ensure_started()
            assert bot._session_restored is True
            cookies = await bot._context.cookies("http://127.0.0.1/")
            assert {c["name"]: c["value"] for c in cookies}["sid"] == "partage"  # (le faux site pose aussi son cookie)
        finally:
            await bot.shutdown()
            await host.shutdown()

    asyncio.run(scenario())
