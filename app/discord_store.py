"""Réglages du pilotage par Discord : jeton du bot Discord, qui a le droit de commander, et où.

Tout est gardé dans `<data_dir>/discord.json` (droits 0600). Le jeton donne le contrôle du bot Discord : il n'est jamais
renvoyé par l'API (seulement ses 4 derniers caractères), jamais écrit dans le journal ni dans les réglages exportés.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

FILE_NAME = "discord.json"
SNOWFLAKE_RE = re.compile(r"^\d{15,25}$")  # identifiant Discord (utilisateur, serveur, salon)
TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{40,120}$")
DEFAULTS: dict = {"enabled": False, "token": "", "owner_id": "", "guild_id": "", "channel_id": "", "confirm": True}


class DiscordSettingsError(Exception):
    """Réglage refusé ; `errors` liste les phrases à montrer au joueur."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ".join(errors))
        self.errors = errors


class DiscordStore:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._file = folder / FILE_NAME if folder else None
        self._data: dict = dict(DEFAULTS)
        self._load()

    @property
    def persistent(self) -> bool:
        return self._file is not None

    def _load(self) -> None:
        if self._file is None:
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self._data = self._clean(raw)

    @staticmethod
    def _clean(raw: dict) -> dict:
        out = dict(DEFAULTS)
        out["enabled"] = raw.get("enabled") is True
        out["confirm"] = raw.get("confirm") is not False
        token = raw.get("token")
        out["token"] = token if isinstance(token, str) and TOKEN_RE.match(token) else ""
        for key in ("owner_id", "guild_id", "channel_id"):
            value = raw.get(key)
            out[key] = value if isinstance(value, str) and SNOWFLAKE_RE.match(value) else ""
        return out

    def token(self) -> str:
        return self._data["token"]

    def ready(self) -> bool:
        """Activé et complet : le client Discord peut se connecter."""
        d = self._data
        return bool(d["enabled"] and d["token"] and d["owner_id"] and d["guild_id"])

    def get(self) -> dict:
        """Les réglages sans le jeton (voir l'en-tête du module)."""
        d = self._data
        hint = d["token"][-4:] if d["token"] else ""
        return {"enabled": d["enabled"], "has_token": bool(d["token"]), "token_hint": hint, "owner_id": d["owner_id"],
                "guild_id": d["guild_id"], "channel_id": d["channel_id"], "confirm": d["confirm"],
                "persistent": self.persistent}

    def update(self, patch: dict) -> dict:
        """Applique les clés présentes ; un jeton vide laisse le jeton actuel. Lève `DiscordSettingsError`."""
        merged, errors = dict(self._data), []
        for key in ("owner_id", "guild_id", "channel_id"):
            if key not in patch:
                continue
            value = str(patch[key]).strip()
            if value and not SNOWFLAKE_RE.match(value):
                errors.append(f"{_LABELS[key]} : un numéro d'une quinzaine de chiffres est attendu.")
            merged[key] = value
        token = str(patch.get("token") or "").strip()
        if token:
            if TOKEN_RE.match(token):
                merged["token"] = token
            else:
                errors.append("Jeton : format inattendu (copiez-le en entier depuis le portail Discord).")
        for key in ("enabled", "confirm"):
            if key in patch:
                merged[key] = bool(patch[key])
        if merged["enabled"] and not errors:
            errors += [f"{label} manquant." for key, label in _REQUIRED.items() if not merged[key]]
        if errors:
            raise DiscordSettingsError(errors)
        self._data = merged
        self._save()
        return self.get()

    def forget_token(self) -> dict:
        """Oublie le jeton et coupe le pilotage."""
        self._data = {**self._data, "token": "", "enabled": False}
        self._save()
        return self.get()

    def _save(self) -> None:
        if self._file is None:
            return
        fd, tmp = tempfile.mkstemp(dir=self._file.parent, prefix=".discord-")
        try:
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._file)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise DiscordSettingsError(["Enregistrement impossible : le dossier de données n'est pas inscriptible."]) from exc


_LABELS = {"owner_id": "Votre identifiant Discord", "guild_id": "Serveur Discord", "channel_id": "Salon"}
_REQUIRED = {"token": "Jeton", "owner_id": "Votre identifiant Discord", "guild_id": "Serveur Discord"}
