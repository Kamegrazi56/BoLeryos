"""Compte du joueur : identifiants et cookies de la session, communs à toutes les sessions de bot.

Un joueur n'a qu'un compte, mais pourra avoir plusieurs sessions de bot (une par serveur de jeu). Le compte garde donc
ce qui appartient au joueur : ses identifiants (en mémoire, et chiffrés sur le disque par le coffre) et les cookies de
sa connexion, dont une nouvelle session de bot repart pour être déjà connectée. Chaque session garde ce qui est propre
à son serveur (univers, réglages, état du jeu).
"""

from __future__ import annotations

import asyncio

from .session_store import SessionStore
from .vault import CredentialVault


class Account:
    def __init__(self, data_dir: str | None) -> None:
        self.vault = CredentialVault(data_dir)
        self.session_store = SessionStore(data_dir)
        # Identifiants gardés EN MÉMOIRE (jamais dans le journal), pour se reconnecter tout seul ; relus du coffre au
        # démarrage SANS se connecter tant que l'utilisateur ne le demande pas.
        self.creds: tuple[str, str] | None = None
        # Une seule connexion au compte à la fois : les sessions de bot partagent le même fichier de cookies, et deux
        # connexions simultanées pourraient se faire concurrence (jetons renouvelés, cookies écrasés).
        self.login_lock = asyncio.Lock()
        self.restore()

    # ------------------------------------------------------------------ identifiants
    def has_credentials(self) -> bool:
        return self.creds is not None

    def identifier(self) -> str:
        """Identifiant (adresse e-mail) mémorisé ou enregistré, pour pré-remplir la page. Jamais le mot de passe."""
        return self.creds[0] if self.creds else self.vault.saved_identifier()

    def remember(self, identifier: str, password: str | None = None) -> bool | None:
        """Mémorise l'identifiant (et l'enregistre chiffré s'il y a un dossier de données).

        Sans nouveau mot de passe, garde celui déjà connu. Retourne None si aucun mot de passe n'est connu (rien n'est
        fait), sinon True si les identifiants sont enregistrés sur le disque, False s'ils ne sont qu'en mémoire."""
        if password is None:
            if self.creds is None:
                return None
            password = self.creds[1]
        self.creds = (identifier, password)
        return self.vault.save(identifier, password)

    def restore(self) -> bool:
        """Recharge en mémoire les identifiants enregistrés (démarrage, ou après une déconnexion)."""
        saved = self.vault.load()
        if saved:
            self.creds = saved
        return saved is not None

    def drop_credentials_from_memory(self) -> None:
        """Se déconnecter : plus de reconnexion automatique (les identifiants enregistrés restent sur le disque)."""
        self.creds = None

    def forget(self) -> bool:
        """Efface les identifiants, y compris ceux enregistrés. Retourne True s'il y en avait."""
        had = bool(self.creds or self.vault.saved_identifier())
        self.creds = None
        self.vault.clear()
        return had

    # ------------------------------------------------------------------ session du navigateur
    def clear_session(self) -> None:
        """Efface les cookies enregistrés (déconnexion) ; les identifiants ne sont pas touchés."""
        self.session_store.clear()
