"""Moteur « api » : lit l'état du jeu par les appels directs du client (`POST /join`), sans lire les pages.

Il ne se connecte pas : la connexion reste humaine (Cloudflare Turnstile). Il reprend les en-têtes d'authentification que le
navigateur de la session envoie déjà à l'API de l'univers (jeton, contrat client). Rien de tout cela n'est écrit dans un
journal, un fichier ou une réponse de l'interface.

**Lecture seule, par choix** : le serveur du jeu refuse (HTTP 403, Cloudflare, `request_forbidden`) la connexion WebSocket
d'un client qui n'est pas le navigateur du jeu (relevé du 2026-11-21) ; les actions passent donc par Chromium, que le moteur
« hybride » (engine.py) combine avec cette lecture. Ce refus n'est pas contourné : c'est la protection qu'on cherche à observer.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

import httpx

from .planets import GameUiError, PlanetInfo

API_HOST_RE = re.compile(r"^https://([a-z0-9-]+)-api\.aleryos\.com$")
FORWARDED_HEADERS = ("authorization", "x-aleryos-client-contract", "origin", "referer", "user-agent")
STATE_CACHE_SECONDS = 3.0  # plusieurs lectures de suite (planètes, modèles, inventaire) partagent le même état
TIMEOUT_SECONDS = 20.0

# Bâtiments et populations : nom de la clé dans l'état du jeu → identifiant du bot (voir constructions.py et populations.py).
BUILDING_IDS = {
    "mineFer": "mine-de-fer", "extracteurCristal": "extracteur-de-cristal", "synthetiseurEther": "synthetiseur-d-ether",
    "centraleEnergetique": "centrale-energetique", "centraleImpulsion": "centrale-a-impulsion", "hangarFer": "hangar-de-fer",
    "hangarCristal": "hangar-de-cristal", "cuveEther": "cuve-d-ether", "usineRobot": "usine-robot",
    "chantierSpatial": "chantier-spatial", "centreRecherche": "centre-de-recherche", "caserne": "caserne",
    "institutFormation": "institut-de-formation", "matriceCoalescence": "matrice-de-coalescence",
    "resonanceTower": "tour-a-resonance", "terraformeur": "terraformeur",
}
UNIT_IDS = {
    "rempart": "rempart-de-fer", "spectre": "spectre-du-rift", "surchargeur": "surchargeur-plasmique",
    "faucheur": "faucheur-cinetique", "pilote": "pilote", "technicien": "technicien", "informaticien": "informaticien",
    "scientifique": "scientifique", "ingenieur": "ingenieur", "mineur": "mineur",
}


def universe_of(base_url: str) -> str:
    match = API_HOST_RE.match(base_url)
    return match.group(1) if match else ""


def planet_infos(state: dict) -> list[PlanetInfo]:
    """Planètes du joueur : nom, coordonnées (adresse spatiale), stocks de Fer, Cristal, Éther."""
    out = []
    for planet in state.get("planets") or []:
        res = planet.get("resources") or {}
        coords = (planet.get("spatialLocation") or {}).get("address") or ""
        out.append(PlanetInfo(str(planet.get("name") or ""), str(coords), tuple(_number(res.get(k)) for k in ("fer", "cristal", "ether"))))  # type: ignore[arg-type]
    return [p for p in out if p.name]


def model_infos(state: dict) -> list[dict]:
    """Modèles de vaisseaux du joueur, avec l'effectif de la planète active (comme la page Assemblages du jeu)."""
    planets = state.get("planets") or []
    active = next((p for p in planets if p.get("id") == state.get("activePlanetId")), planets[0] if planets else {})
    ships = active.get("ships") or {}
    seen: dict[str, dict] = {}
    for planet in planets:
        for model in planet.get("shipModels") or []:
            if model.get("id") and model.get("name") and model["id"] not in seen:
                seen[model["id"]] = model
    return [
        {"name": str(m["name"]), "kind": _kind(m.get("role"), m.get("kind")), "owned": _count(ships.get(mid)),
         "planet": str(active.get("name") or ""), "role": str(m.get("role") or ""), "access": str(m.get("access") or "")}
        for mid, m in seen.items()
    ]


def inventory_planets(state: dict) -> list[dict]:
    """Même forme que le relevé de la page Inventaire : nom, coordonnées, niveaux de bâtiments, effectifs de population."""
    out = []
    for planet in state.get("planets") or []:
        # Le jeu n'indique pas les bâtiments jamais construits : niveau 0 (comme la page Inventaire).
        levels = dict.fromkeys(BUILDING_IDS.values(), 0)
        levels.update({BUILDING_IDS[k]: _count(v) for k, v in (planet.get("buildings") or {}).items() if k in BUILDING_IDS})
        units = {UNIT_IDS[k]: _count(v) for k, v in (planet.get("groundUnits") or {}).items() if k in UNIT_IDS}
        coords = (planet.get("spatialLocation") or {}).get("address") or ""
        out.append({"name": str(planet.get("name") or ""), "coords": str(coords), "levels": levels, "units": units})
    return [p for p in out if p["name"]]


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _count(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _kind(*raws) -> str:
    """civil / militaire d'après les champs du modèle (le premier qui le dit). Vrai jeu (20/11) : `kind` vaut « component »
    pour tous les modèles, il ne renseigne pas ; `role` est essayé en premier."""
    for raw in raws:
        text = str(raw or "").lower()
        if text.startswith(("mil", "war", "comb", "att")):
            return "militaire"
        if text.startswith(("civ", "trans", "cargo", "util")):
            return "civil"
    return str(raws[0] or "") if raws else ""


class ApiEngine:
    """Lecture de l'état par `POST /join`, avec la session du navigateur de l'instance."""

    kind = "api"

    def __init__(self, bot, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.bot = bot
        self._transport = transport
        self._cache: tuple[float, dict] | None = None

    def supports(self, feature_id: str) -> bool:
        return False  # lecture seule pour l'instant

    def _session(self) -> tuple[str, dict[str, str]]:
        session = getattr(self.bot, "api_session", None)
        if not session:
            raise GameUiError("aucune session du jeu à reprendre : connectez-vous et entrez dans l'univers")
        return session["base"], dict(session["headers"])

    async def state(self, fresh: bool = False) -> dict:
        if not fresh and self._cache and time.monotonic() - self._cache[0] < STATE_CACHE_SECONDS:
            return self._cache[1]
        base, headers = self._session()
        headers["content-type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport) as client:
                response = await client.post(f"{base}/join", headers=headers, json={})
        except httpx.HTTPError as exc:
            raise GameUiError(f"l'API du jeu ne répond pas ({type(exc).__name__})") from exc
        if response.status_code in (401, 403):
            raise GameUiError("l'API du jeu refuse la session (jeton expiré : la page doit se reconnecter)")
        if response.status_code != 200:
            raise GameUiError(f"l'API du jeu a répondu {response.status_code}")
        try:
            state = response.json()["state"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GameUiError("réponse de l'API illisible") from exc
        if not isinstance(state, dict):
            raise GameUiError("réponse de l'API illisible")
        self._cache = (time.monotonic(), state)
        return state

    async def scan_planets(self) -> list[PlanetInfo]:
        planets = planet_infos(await self.state(fresh=True))
        if not planets:
            raise GameUiError("l'API n'a renvoyé aucune planète")
        return planets

    async def scan_models(self) -> list[dict]:
        return model_infos(await self.state(fresh=True))

    async def read_inventory(self) -> tuple[list[dict], dict[str, int]]:
        planets = inventory_planets(await self.state(fresh=True))
        if not planets:
            raise GameUiError("l'API n'a renvoyé aucune planète")
        return planets, {}  # niveaux de recherche : correspondance avec l'état du jeu pas encore établie


def api_session_from_request(url: str, headers: dict[str, str]) -> dict | None:
    """Session reprenable depuis une requête du navigateur vers l'API de l'univers (None si ce n'en est pas une)."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if not API_HOST_RE.match(base) or "authorization" not in {k.lower() for k in headers}:
        return None
    kept = {k.lower(): v for k, v in headers.items() if k.lower() in FORWARDED_HEADERS}
    return {"base": base, "headers": kept}
