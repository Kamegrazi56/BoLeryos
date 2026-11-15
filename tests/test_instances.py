"""Dossier de données de chaque instance de jeu, et reprise des anciens fichiers à plat de /data."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.instances import (
    ACCOUNT_FILES,
    INSTANCE_FILES,
    default_instance_id,
    instance_dir,
    migrate_legacy,
    slugify,
)
from app.main import create_app

LINE = {"building": "mine-de-fer", "level": 45}


def _write(folder, name, content):
    (folder / name).write_text(json.dumps(content), encoding="utf8")


# ------------------------------------------------------------------ identifiants d'instance
def test_slugify_gives_a_safe_lowercase_identifier():
    assert slugify("Asterion") == "asterion"
    assert slugify("  Kaël II  ") == "kael-ii"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("???") == ""


def test_instance_dir_is_created_under_instances(tmp_path):
    folder = instance_dir(str(tmp_path), "asterion")
    assert folder == tmp_path / "instances" / "asterion" and folder.is_dir()
    assert instance_dir(str(tmp_path), "asterion") == folder  # idempotent


@pytest.mark.parametrize("bad", ["", "..", "a/b", "A", "x" * 41, "-a", ".hidden"])
def test_instance_dir_refuses_unsafe_identifiers(tmp_path, bad):
    with pytest.raises(ValueError):
        instance_dir(str(tmp_path), bad)
    assert not (tmp_path / "instances").exists() or not any((tmp_path / "instances").iterdir())


def test_default_instance_id_follows_the_saved_universe(tmp_path):
    assert default_instance_id(str(tmp_path)) == "principale"  # rien d'enregistré
    _write(tmp_path, "preferences.json", {"universe": "Asterion"})
    assert default_instance_id(str(tmp_path)) == "asterion"
    _write(tmp_path, "preferences.json", {"universe": "???"})
    assert default_instance_id(str(tmp_path)) == "principale"
    (tmp_path / "preferences.json").write_text("pas du json", encoding="utf8")
    assert default_instance_id(str(tmp_path)) == "principale"


# ------------------------------------------------------------------ reprise des anciens fichiers
def test_migration_copies_instance_files_and_leaves_account_files(tmp_path):
    for name in ("constructions.json", "preferences.json", "inventory.json"):
        _write(tmp_path, name, {"de": name})
    for name in ACCOUNT_FILES:
        (tmp_path / name).write_text("secret", encoding="utf8")
    (tmp_path / "automation.json").write_text("ancien fichier inconnu", encoding="utf8")

    moved = migrate_legacy(str(tmp_path), "asterion")

    target = tmp_path / "instances" / "asterion"
    assert sorted(moved) == ["constructions.json", "inventory.json", "preferences.json"]
    assert json.loads((target / "constructions.json").read_text(encoding="utf8")) == {"de": "constructions.json"}
    assert not any((target / name).exists() for name in ACCOUNT_FILES)  # le compte reste à la racine
    assert not (target / "automation.json").exists()  # fichier inconnu : laissé tel quel
    assert (tmp_path / "constructions.json").exists()  # copie : les originaux restent (retour en arrière possible)


def test_migration_runs_once_and_never_overwrites(tmp_path):
    _write(tmp_path, "slots.json", {"v": 1})
    assert migrate_legacy(str(tmp_path), "asterion") == ["slots.json"]
    target = tmp_path / "instances" / "asterion"
    _write(target, "slots.json", {"v": 2})  # modifié depuis par le bot
    _write(tmp_path, "slots.json", {"v": 3})  # l'original à la racine ne doit plus être recopié
    assert migrate_legacy(str(tmp_path), "asterion") == []
    assert json.loads((target / "slots.json").read_text(encoding="utf8")) == {"v": 2}


def test_migration_without_files_or_folder_does_nothing(tmp_path):
    assert migrate_legacy(str(tmp_path), "asterion") == []
    assert migrate_legacy(None, "asterion") == []
    assert migrate_legacy(str(tmp_path / "absent"), "asterion") == []


def test_every_instance_file_is_distinct_from_the_account_files():
    assert not set(INSTANCE_FILES) & set(ACCOUNT_FILES)


# ------------------------------------------------------------------ dans l'application
def test_app_reads_and_writes_instance_files_in_the_instance_folder(settings, tmp_path):
    _write(tmp_path, "preferences.json", {"universe": "Asterion"})
    _write(tmp_path, "constructions.json", {"scope": "all", "queue": [LINE]})
    cfg = replace(settings, data_dir=str(tmp_path))
    with TestClient(create_app(cfg)) as client:
        bot = client.app.state.bot
        assert bot._settings.data_dir == str(tmp_path / "instances" / "asterion")
        assert client.app.state.instance_id == "asterion"
        assert client.get("/api/constructions").json()["settings"]["queue"][0]["building"] == "mine-de-fer"  # repris
        assert client.put("/api/constructions", json={"planets": [], "queue": []}).status_code == 200
    assert json.loads((tmp_path / "instances" / "asterion" / "constructions.json").read_text(encoding="utf8"))["queue"] == []
    assert json.loads((tmp_path / "constructions.json").read_text(encoding="utf8"))["queue"] == [LINE]  # l'original n'est pas touché
    with TestClient(create_app(cfg)) as client:  # redémarrage : les fichiers de l'instance ne sont pas recopiés par-dessus
        assert client.get("/api/constructions").json()["settings"]["queue"] == []
        client.app.state.bot.set_credentials("joueur@example.com", "mot-de-passe-test")
    assert (tmp_path / "account.json").exists()  # le compte reste à la racine
    assert not (tmp_path / "instances" / "asterion" / "account.json").exists()


def test_app_without_data_dir_creates_no_folder(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=None))) as client:
        assert client.app.state.bot._settings.data_dir is None
    assert not (tmp_path / "instances").exists()
