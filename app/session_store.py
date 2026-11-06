"""Session du navigateur du jeu (cookies) gardée dans `<data_dir>/browser-state.json`.

Permet de relancer le conteneur (mise à jour, redémarrage) sans se reconnecter : le navigateur repart avec
les cookies de la dernière session connectée. Le fichier contient un jeton de session, il est donc créé
avec les droits 0600 dans le volume de données, et supprimé à la déconnexion.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class SessionStore:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._file = folder / "browser-state.json" if folder else None

    @property
    def available(self) -> bool:
        return self._file is not None

    def load(self) -> dict | None:
        """État Playwright enregistré, ou None s'il est absent, abîmé ou sans cookie."""
        if not self._file or not self._file.exists():
            return None
        try:
            state = json.loads(self._file.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return None
        if not isinstance(state, dict) or not state.get("cookies"):
            return None
        return state

    def save(self, state: dict) -> bool:
        """Enregistre l'état ; retourne False si le dossier est absent, non inscriptible ou l'état sans cookie."""
        if not self._file or not state.get("cookies"):
            return False
        try:
            fd, tmp = tempfile.mkstemp(dir=self._file.parent, prefix=".browser-state-")
        except OSError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(state, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._file)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        return True

    def clear(self) -> None:
        if self._file:
            try:
                self._file.unlink(missing_ok=True)
            except OSError:
                pass
