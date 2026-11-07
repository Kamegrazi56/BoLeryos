"""Préférences non secrètes : univers à rejoindre, délai de connexion, suggestions (univers et planètes vus).

Enregistrées dans `<data_dir>/preferences.json` s'il existe. Aucun identifiant ici : ils ont leur propre coffre.
Au premier démarrage, l'univers et le délai de l'ancien fichier `automation.json` sont repris s'il est là.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

MAX_UNIVERSE_LENGTH = 60
MAX_DELAY_SECONDS = 60.0
MAX_SUGGESTIONS = 200
CATALOG_KINDS = ("universes", "planets", "models")

DEFAULTS: dict = {
    "universe": "",  # univers où entrer automatiquement (nom affiché sur la page de choix) ; vide = demander
    "login_delay": {"min_seconds": 0.4, "max_seconds": 1.4},  # pause avant chaque clic ou saisie de la connexion
    "catalog": {"universes": [], "planets": [], "models": []},  # suggestions, entretenues par le bot
}


class PreferencesError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def validate(data: dict) -> dict:
    """Préférences complètes et valides à partir de `data` (les clés absentes gardent leur valeur par défaut)."""
    errors: list[str] = []
    merged = copy.deepcopy(DEFAULTS)
    universe = data.get("universe", merged["universe"])
    if not isinstance(universe, str) or len(universe) > MAX_UNIVERSE_LENGTH:
        errors.append(f"Univers : texte de {MAX_UNIVERSE_LENGTH} caractères au plus.")
    else:
        merged["universe"] = universe.strip()
    delay = data.get("login_delay", merged["login_delay"])
    try:
        raw = (delay["min_seconds"], delay["max_seconds"])
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in raw):
            raise ValueError
        low, high = float(raw[0]), float(raw[1])
        if not (0 <= low <= MAX_DELAY_SECONDS and 0 <= high <= MAX_DELAY_SECONDS):
            raise ValueError
        if low > high:
            errors.append("Connexion : le délai minimum dépasse le délai maximum.")
        merged["login_delay"] = {"min_seconds": low, "max_seconds": high}
    except (KeyError, TypeError, ValueError):
        errors.append(f"Connexion : délais entre 0 et {MAX_DELAY_SECONDS:g} secondes.")
    if errors:
        raise PreferencesError(errors)
    return merged


class Preferences:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / "preferences.json" if folder else None
        self._data = copy.deepcopy(DEFAULTS)
        if folder is None:
            return
        source = self._path if self._path.exists() else folder / "automation.json"  # type: ignore[union-attr]
        self._load(source)

    def _load(self, source: Path) -> None:
        if not source.exists():
            return
        try:
            saved = json.loads(source.read_text(encoding="utf8"))
            if not isinstance(saved, dict):
                return
            self._data = validate(saved)
            catalog = saved.get("catalog")
            if isinstance(catalog, dict):
                for kind in CATALOG_KINDS:
                    names = catalog.get(kind)
                    if isinstance(names, list):
                        self._data["catalog"][kind] = [n for n in names if isinstance(n, str)][:MAX_SUGGESTIONS]
        except (OSError, ValueError, PreferencesError):
            self._data = copy.deepcopy(DEFAULTS)  # fichier illisible ou refusé : valeurs par défaut

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def update(self, data: dict) -> dict:
        """Les clés absentes de `data` restent inchangées : la page n'envoie que ce qu'elle modifie."""
        merged = self.get()
        for key in ("universe", "login_delay"):
            if key in data:
                merged[key] = {**merged[key], **data[key]} if isinstance(data[key], dict) else data[key]
        cleaned = validate(merged)
        cleaned["catalog"] = self._data["catalog"]  # entretenu par le bot, jamais modifié par l'utilisateur
        self._data = cleaned
        self._save()
        return self.get()

    def set_universe(self, name: str) -> None:
        if self._data["universe"] != name:
            self._data["universe"] = name
            self._save()

    def add_catalog(self, kind: str, names: list[str]) -> None:
        known = set(self._data["catalog"][kind])
        fresh = {n.strip() for n in names if n.strip()} - known
        if fresh:
            self._data["catalog"][kind] = sorted(known | fresh)[:MAX_SUGGESTIONS]
            self._save()

    def set_catalog(self, kind: str, names: list[str]) -> None:
        """Remplace la liste (les modèles supprimés dans le jeu disparaissent au scan suivant)."""
        fresh = sorted({n.strip() for n in names if n.strip()})[:MAX_SUGGESTIONS]
        if fresh != self._data["catalog"][kind]:
            self._data["catalog"][kind] = fresh
            self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".preferences-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
