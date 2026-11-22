"""Contrôle de la persistance du dossier de données (app/storage.py)."""

from __future__ import annotations

from pathlib import Path

from app import storage


def _mountinfo(tmp_path: Path, mount_point: str) -> str:
    f = tmp_path / "mountinfo"
    f.write_text(f"36 35 98:0 /mnt1 {mount_point} rw,noatime - ext3 /dev/root rw\n", encoding="utf8")
    return str(f)


def test_volume_monte_est_persistant(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(storage, "MOUNTINFO", _mountinfo(tmp_path, str(data.resolve())))
    assert storage_ok(data)["persistent"] is True


def test_dossier_non_monte_est_signale(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(storage, "MOUNTINFO", _mountinfo(tmp_path, "/autre"))
    status = storage_ok(data)
    assert status["persistent"] is False
    assert "volume" in status["message"]


def test_liste_des_montages_illisible_ne_dit_rien(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(storage, "MOUNTINFO", str(tmp_path / "absent"))
    assert storage_ok(data)["persistent"] is None


def test_sans_dossier_de_donnees(tmp_path: Path) -> None:
    assert storage.storage_status(None)["persistent"] is False
    assert storage.storage_status(str(tmp_path / "inconnu"))["persistent"] is False


def storage_ok(path: Path) -> dict:
    return storage.storage_status(str(path))
