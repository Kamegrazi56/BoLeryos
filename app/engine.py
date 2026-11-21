"""Moteur de jeu : la manière dont le bot lit l'état du jeu et agit dedans.

Le moteur `browser` pilote un vrai Chromium (il lit les pages, clique). Le moteur `api` (appels directs aux serveurs du jeu)
s'ajoute à côté, sans rien changer aux fonctionnalités :
elles demandent « lis les planètes » ou « construis », pas « clique ici ».

Chaque instance a son moteur, choisi dans ses préférences (`engine`). Un moteur dit quelles fonctionnalités il sait faire
(`supports`) : l'ordonnanceur n'en lance aucune qu'il ne sache pas faire, et le journal le dit au lieu d'échouer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .planets import GameUiError

ENGINES = ("browser", "hybrid", "api")
DEFAULT_ENGINE = "browser"
ENGINE_LABELS = {
    "browser": "Navigateur : le bot lit et clique dans Chromium",
    "hybrid": "Hybride : lecture par l'API du jeu, actions dans Chromium",
    "api": "API seule (diagnostic) : lecture seule, aucune action",
}
# Pourquoi pas d'actions par l'API : le serveur du jeu refuse (HTTP 403, Cloudflare) la connexion WebSocket d'un client qui
# n'est pas le navigateur du jeu. Ce refus est respecté, pas contourné (voir docs/API-PROTOCOL.md).


@runtime_checkable
class GameEngine(Protocol):
    """Ce qu'un moteur sait faire. Les lectures sont indépendantes des fonctionnalités ; les actions (construire, recruter…)
    restent portées par chaque fonctionnalité tant qu'elles n'ont pas de version propre à un moteur."""

    kind: str

    def supports(self, feature_id: str) -> bool:
        """Le moteur sait-il exécuter cette fonctionnalité ?"""

    async def scan_planets(self) -> list:
        """Planètes du joueur (nom, coordonnées, stocks)."""

    async def scan_models(self) -> list[dict]:
        """Modèles de vaisseaux (nom, type, effectif de la planète affichée)."""

    async def read_inventory(self) -> tuple[list[dict], dict[str, int]]:
        """(planètes avec leurs niveaux de bâtiments, niveaux des recherches)."""


class BrowserEngine:
    """Moteur actuel : lit et agit à travers les pages du jeu dans Chromium. Il porte toutes les fonctionnalités."""

    kind = "browser"

    def __init__(self, planets_reader, models_reader, inventory_reader) -> None:
        self._planets = planets_reader
        self._models = models_reader
        self._inventory = inventory_reader

    def supports(self, feature_id: str) -> bool:
        return True

    async def scan_planets(self) -> list:
        return await self._planets.list_planets()

    async def scan_models(self) -> list[dict]:
        return await self._models.list_models()

    async def read_inventory(self) -> tuple[list[dict], dict[str, int]]:
        return await self._inventory.read()


class HybridEngine:
    """Lit l'état par l'API du jeu (sans ouvrir de page), agit par le navigateur. Si l'API ne répond pas (session pas encore
    reprise, jeton expiré), la lecture retombe sur le navigateur et le journal le dit : jamais de lecture inventée."""

    kind = "hybrid"

    def __init__(self, browser, api, on_fallback) -> None:
        self._browser = browser
        self._api = api
        self._on_fallback = on_fallback  # reçoit la phrase à écrire au journal

    def supports(self, feature_id: str) -> bool:
        return self._browser.supports(feature_id)  # les actions passent par le navigateur

    async def _read(self, what: str, api_read, browser_read):
        try:
            return await api_read()
        except GameUiError as exc:
            self._on_fallback(f"Lecture « {what} » par l'API impossible ({exc}) : lecture par le navigateur.")
            return await browser_read()

    async def scan_planets(self) -> list:
        return await self._read("planètes", self._api.scan_planets, self._browser.scan_planets)

    async def scan_models(self) -> list[dict]:
        return await self._read("modèles", self._api.scan_models, self._browser.scan_models)

    async def read_inventory(self) -> tuple[list[dict], dict[str, int]]:
        """Niveaux de bâtiments par l'API ; niveaux de recherche (pas encore lus par l'API) et repli : par le navigateur."""
        async def both():
            planets, _ = await self._api.read_inventory()
            _, researches = await self._browser.read_inventory()
            return planets, researches

        return await self._read("inventaire", both, self._browser.read_inventory)


def describe_unsupported(engine: GameEngine, label: str) -> str:
    """Phrase du journal quand une fonctionnalité n'est pas faisable avec le moteur de l'instance."""
    return f"« {label} » n'est pas disponible avec le moteur « {engine.kind} » : rien n'a été fait"
