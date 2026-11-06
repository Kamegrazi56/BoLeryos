"""Cookies du navigateur gardés sur disque pour rester connecté après un redémarrage du conteneur."""

import json
import os

from app.session_store import SessionStore

STATE = {"cookies": [{"name": "sid", "value": "jeton-test", "domain": "aleryos.com", "path": "/"}], "origins": []}


def test_roundtrip(tmp_path):
    store = SessionStore(str(tmp_path))
    assert store.save(STATE) is True
    assert store.load() == STATE


def test_file_is_private(tmp_path):
    SessionStore(str(tmp_path)).save(STATE)
    if os.name != "nt":
        assert (tmp_path / "browser-state.json").stat().st_mode & 0o777 == 0o600


def test_clear_removes_file(tmp_path):
    store = SessionStore(str(tmp_path))
    store.save(STATE)
    store.clear()
    assert store.load() is None
    assert not (tmp_path / "browser-state.json").exists()


def test_empty_state_never_overwrites_good_session(tmp_path):
    store = SessionStore(str(tmp_path))
    store.save(STATE)
    assert store.save({"cookies": [], "origins": []}) is False
    assert store.load() == STATE


def test_corrupt_file_is_ignored(tmp_path):
    (tmp_path / "browser-state.json").write_text("{pas du json", encoding="utf8")
    assert SessionStore(str(tmp_path)).load() is None


def test_state_without_cookies_is_ignored(tmp_path):
    (tmp_path / "browser-state.json").write_text(json.dumps({"origins": []}), encoding="utf8")
    assert SessionStore(str(tmp_path)).load() is None


def test_no_data_dir_is_harmless(tmp_path):
    store = SessionStore(str(tmp_path / "absent"))
    assert store.available is False
    assert store.save(STATE) is False and store.load() is None
    store.clear()
