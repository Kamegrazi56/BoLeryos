"""Coffre des identifiants du jeu : identifiant + mot de passe chiffrés dans `<data_dir>/account.json`.

Le mot de passe est chiffré (Fernet : AES + signature) avec une clé aléatoire gardée à côté, dans
`<data_dir>/account.key` (droits 0600). Cette clé protège contre une lecture directe du fichier
(sauvegarde, copie, capture d'écran), pas contre quelqu'un qui copie tout le dossier : décision D1 « B »
du plan. Rien n'est jamais écrit dans le journal ni dans les réglages exportés.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class CredentialVault:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._file = folder / "account.json" if folder else None
        self._key_file = folder / "account.key" if folder else None
        self.problem = ""  # fichier présent mais illisible (clé perdue, contenu abîmé) : affiché dans le journal

    @property
    def available(self) -> bool:
        return self._file is not None

    def _fernet(self, create: bool) -> Fernet | None:
        assert self._key_file is not None
        try:
            if self._key_file.exists():
                return Fernet(self._key_file.read_bytes().strip())
            if not create:
                return None
            key = Fernet.generate_key()
            self._write(self._key_file, key)
            return Fernet(key)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _write(path: Path, content: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".account-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def save(self, identifier: str, password: str) -> bool:
        """Enregistre le couple ; retourne False si le dossier de données est absent ou non inscriptible."""
        if not self._file:
            return False
        fernet = self._fernet(create=True)
        if fernet is None:
            return False
        try:
            token = fernet.encrypt(password.encode("utf8")).decode("ascii")
            self._write(self._file, json.dumps({"identifier": identifier, "password": token}).encode("utf8"))
        except OSError:
            return False
        self.problem = ""
        return True

    def load(self) -> tuple[str, str] | None:
        """(identifiant, mot de passe) enregistrés, ou None s'il n'y en a pas ou s'ils sont illisibles."""
        if not self._file or not self._file.exists():
            return None
        try:
            data = json.loads(self._file.read_text(encoding="utf8"))
            fernet = self._fernet(create=False)
            if fernet is None:
                raise ValueError("clé introuvable")
            identifier = str(data["identifier"])
            password = fernet.decrypt(str(data["password"]).encode("ascii")).decode("utf8")
        except (OSError, ValueError, KeyError, TypeError, InvalidToken):
            self.problem = "identifiants enregistrés illisibles : à ressaisir"
            return None
        if not identifier or not password:
            return None
        self.problem = ""
        return identifier, password

    def saved_identifier(self) -> str:
        """L'identifiant enregistré (non secret), même si le mot de passe est illisible."""
        if not self._file or not self._file.exists():
            return ""
        try:
            return str(json.loads(self._file.read_text(encoding="utf8")).get("identifier", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def clear(self) -> None:
        for path in (self._file, self._key_file):
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
        self.problem = ""
