"""Dossier de données de chaque instance de jeu (une instance = un bot sur un serveur du jeu).

Disposition de `/data` :
  - à la racine : ce qui appartient au compte du joueur (identifiants chiffrés, clé, cookies de connexion) ;
  - `instances/<id>/` : tout ce qui est propre à un serveur (réglages, objectifs, relevés, planning, univers choisi).

Avant ce découpage, tous les fichiers étaient à plat dans `/data`. Au premier démarrage, ceux d'une instance sont COPIÉS
dans son dossier (les originaux restent en place : on peut revenir à la version précédente sans rien perdre) ; la copie
ne se refait jamais par-dessus un dossier d'instance déjà rempli.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

INSTANCES_FOLDER = "instances"
DEFAULT_INSTANCE_ID = "principale"
MAX_ID_LENGTH = 40
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# Fichiers du compte : restent à la racine de /data, communs à toutes les instances.
ACCOUNT_FILES = ("account.json", "account.key", "browser-state.json")

# Fichiers propres à une instance (tout ce que les réglages et relevés du bot écrivent). Un fichier inconnu de cette
# liste (reste d'une ancienne version) n'est jamais déplacé ni supprimé.
INSTANCE_FILES = (
    "alliance.json", "component_counts.json", "components.json", "constructions.json", "excavations.json",
    "explorations.json", "feature_hours.json", "features.json", "hackings.json", "inventory.json", "minings.json",
    "missions.json", "pillars.json", "planning.json", "populations.json", "preferences.json", "researches.json",
    "scans.json", "ships.json", "slots.json", "spying.json", "transport.json",
)


def slugify(text: str) -> str:
    """Identifiant sûr (minuscules, chiffres, tirets) tiré d'un nom ; chaîne vide si rien d'utilisable."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:MAX_ID_LENGTH].strip("-")


def _check_id(instance_id: str) -> None:
    if not isinstance(instance_id, str) or len(instance_id) > MAX_ID_LENGTH or not ID_RE.match(instance_id):
        raise ValueError("identifiant d'instance invalide")


def instance_dir(data_dir: str, instance_id: str) -> Path:
    """Dossier de l'instance (créé au besoin). Refuse un identifiant qui pourrait sortir de `instances/`."""
    _check_id(instance_id)
    folder = Path(data_dir) / INSTANCES_FOLDER / instance_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def default_instance_id(data_dir: str | None) -> str:
    """Identifiant de l'instance existante : l'univers déjà choisi (ex. « asterion »), sinon « principale »."""
    if data_dir:
        try:
            with open(Path(data_dir) / "preferences.json", encoding="utf8") as f:
                universe = json.load(f).get("universe", "")
        except (OSError, ValueError, AttributeError):
            return DEFAULT_INSTANCE_ID
        if isinstance(universe, str):
            return slugify(universe) or DEFAULT_INSTANCE_ID
    return DEFAULT_INSTANCE_ID


def migrate_legacy(data_dir: str | None, instance_id: str) -> list[str]:
    """Copie les anciens fichiers à plat de `/data` dans le dossier de l'instance ; retourne les noms copiés.

    Ne fait rien si le dossier de l'instance contient déjà des fichiers (déjà repris, ou instance déjà utilisée). La copie
    se fait dans un dossier provisoire renommé d'un coup : un arrêt en cours de route ne laisse jamais un dossier à moitié
    rempli qui passerait ensuite pour « déjà repris »."""
    _check_id(instance_id)
    if not data_dir or not os.path.isdir(data_dir):
        return []
    root = Path(data_dir)
    target = root / INSTANCES_FOLDER / instance_id
    if target.exists() and any(target.iterdir()):
        return []
    present = [name for name in INSTANCE_FILES if (root / name).is_file()]
    if not present:
        return []
    staging = root / INSTANCES_FOLDER / f".reprise-{instance_id}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        for name in present:
            shutil.copy2(root / name, staging / name)
        if target.exists():  # dossier vide
            target.rmdir()
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return present
