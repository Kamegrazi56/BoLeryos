"""Alertes : message envoyé à une adresse (webhook) choisie par le joueur quand le bot lance une amélioration
ou a besoin d'une intervention.

Sécurité :
- l'adresse est un secret léger (qui la connaît peut poster des messages) : enregistrée à part dans
  `<data_dir>/notifications.json` (droits 0600), jamais dans les réglages exportés, jamais dans le journal,
  jamais renvoyée en entier par l'API ;
- seul le https est accepté et les adresses privées, locales ou réservées sont refusées (le serveur ne doit pas
  servir à joindre un réseau interne) ;
- le contenu des messages ne comprend ni identifiant, ni mot de passe, ni jeton.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

KINDS = ("launch", "attention", "attack")  # amélioration lancée ; intervention ou arrêt automatique ; attaque en approche
FORMATS = ("json", "text")  # json : Discord / Slack / Mattermost ; text : ntfy (texte brut)


class NotifyError(ValueError):
    pass


def _addresses(host: str) -> list[ipaddress._BaseAddress]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def check_url(url: str, *, allow_private: bool = False) -> str:
    """Retourne l'adresse nettoyée ou lève NotifyError (adresse non sûre)."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" and not (allow_private and parts.scheme == "http"):
        raise NotifyError("L'adresse doit commencer par https://")
    if not parts.hostname or len(url) > 500:
        raise NotifyError("Adresse invalide.")
    if parts.username or parts.password:
        raise NotifyError("L'adresse ne doit pas contenir d'identifiants.")
    if not allow_private:
        try:
            addresses = _addresses(parts.hostname)
        except OSError as exc:
            raise NotifyError("Adresse introuvable (DNS).") from exc
        for ip in addresses:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise NotifyError("Les adresses du réseau local ou réservées sont refusées.")
    return url


class Notifier:
    def __init__(self, data_dir: str | None, allow_private: bool = False) -> None:
        self._path = Path(data_dir) / "notifications.json" if data_dir and os.path.isdir(data_dir) else None
        self._allow_private = allow_private
        self._url = ""
        self._format = "json"
        self._events = {kind: True for kind in KINDS}
        self.last_error = ""
        self.last_sent = 0.0
        if self._path and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                self._url = check_url(saved["url"], allow_private=allow_private) if saved.get("url") else ""
                self._format = saved.get("format") if saved.get("format") in FORMATS else "json"
                self._events = {kind: bool(saved.get("events", {}).get(kind, True)) for kind in KINDS}
            except (OSError, ValueError, KeyError, TypeError):
                self._url = ""

    # ---------------------------------------------------------------- configuration
    @property
    def configured(self) -> bool:
        return bool(self._url)

    def public_config(self) -> dict:
        """Ce que l'interface peut voir : jamais l'adresse complète (seulement le nom du serveur)."""
        return {
            "configured": self.configured,
            "host": urlsplit(self._url).hostname or "" if self._url else "",
            "format": self._format,
            "events": dict(self._events),
            "last_error": self.last_error,
            "last_sent_ago_s": round(time.time() - self.last_sent) if self.last_sent else None,
            "persistent": self._path is not None,
        }

    def configure(self, *, url: str | None, fmt: str, events: dict) -> None:
        """`url` None = conserver l'adresse actuelle ; « » = la supprimer."""
        if fmt not in FORMATS:
            raise NotifyError("Format inconnu.")
        if url is not None:
            self._url = check_url(url, allow_private=self._allow_private) if url.strip() else ""
        self._format = fmt
        self._events = {kind: bool(events.get(kind, self._events[kind])) for kind in KINDS}
        self.last_error = ""
        self._save()

    def clear(self) -> None:
        self._url, self.last_error = "", ""
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".notifications-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"url": self._url, "format": self._format, "events": self._events}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError:
            pass

    # ------------------------------------------------------------------------ envoi
    def wants(self, kind: str) -> bool:
        return self.configured and self._events.get(kind, False)

    async def send(self, text: str) -> bool:
        """Envoie le message ; ne lève jamais (une alerte qui échoue ne doit pas gêner le bot)."""
        if not self.configured:
            return False
        try:
            url = await asyncio.to_thread(check_url, self._url, allow_private=self._allow_private)  # revérifié à chaque envoi
            kwargs = {"json": {"content": text, "text": text}} if self._format == "json" else {
                "content": text.encode("utf-8"), "headers": {"Content-Type": "text/plain; charset=utf-8", "Title": "BoLeryos"}}
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                response = await client.post(url, **kwargs)
            if response.status_code >= 400:
                raise NotifyError(f"le serveur d'alertes a répondu {response.status_code}")
            self.last_error, self.last_sent = "", time.time()
            return True
        except (NotifyError, httpx.HTTPError, OSError) as exc:
            self.last_error = str(exc)[:160] if isinstance(exc, NotifyError) else f"envoi impossible ({type(exc).__name__})"
            return False
