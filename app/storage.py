"""Contrôle du dossier de données : survit-il à une mise à jour du conteneur ?

Le bot écrit tout dans `/data`. Si ce dossier n'est pas un volume monté depuis l'extérieur, il vit dans le conteneur
lui-même : le fichier s'écrit sans erreur, mais tout disparaît au premier redéploiement. On le détecte ici pour le dire
clairement à l'écran plutôt que de le découvrir après coup.

Cas piégeux : le `Dockerfile` déclare `VOLUME /data`. Un conteneur lancé sans volume nommé reçoit alors un volume
ANONYME (nom de 64 caractères hexadécimaux) : /data est bien « monté », mais un redéploiement en crée un nouveau, vide.
On le distingue donc d'un volume nommé, et on affiche le nom du volume et sa date de création pour qu'un volume
remplacé par un volume vide se voie tout de suite.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

MOUNTINFO = "/proc/self/mountinfo"
# Fichier témoin posé dans le volume : sa date dit depuis quand ce volume existe (un volume neuf = date du jour).
MARKER = ".boleryos-volume"

# Racine d'un volume Docker vue depuis le conteneur : .../volumes/<nom>/_data
_VOLUME_ROOT = re.compile(r"/volumes/([^/]+)/_data$")
_ANONYMOUS_NAME = re.compile(r"^[0-9a-f]{64}$")


def _mounts() -> dict[str, str] | None:
    """{point de montage: racine côté hôte} ; None si la liste n'est pas lisible (hors Linux, droits)."""
    try:
        with open(MOUNTINFO, encoding="utf8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    mounts: dict[str, str] = {}
    for line in lines:
        fields = line.split()
        if len(fields) > 4:
            mounts[fields[4].replace("\\040", " ")] = fields[3].replace("\\040", " ")
    return mounts


def volume_name(root: str) -> str | None:
    """Nom du volume Docker d'après la racine du montage ; None si ce n'est pas un volume Docker (dossier monté, etc.)."""
    found = _VOLUME_ROOT.search(root)
    return found.group(1) if found else None


def _created_on(data_dir: str) -> str | None:
    """Date de création du volume d'après le fichier témoin (posé au premier contrôle) ; None si illisible."""
    path = os.path.join(data_dir, MARKER)
    try:
        if not os.path.exists(path):
            with open(path, "w", encoding="utf8") as f:
                f.write(datetime.now().isoformat(timespec="seconds") + "\n")
        with open(path, encoding="utf8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def storage_status(data_dir: str | None) -> dict:
    """{"persistent": True/False/None, "message": texte, "volume": nom, "created": date} ; None = on ne sait pas."""
    if not data_dir or not os.path.isdir(data_dir):
        return {"persistent": False, "message": "Aucun dossier de données : réglages et identifiants sont perdus à chaque arrêt."}
    if not os.access(data_dir, os.W_OK):
        return {"persistent": False, "message": "Le dossier de données n'est pas inscriptible : rien n'est enregistré."}
    mounts = _mounts()
    if mounts is None:
        return {"persistent": None, "message": ""}
    root = mounts.get(os.path.realpath(data_dir))
    if root is None:
        return {
            "persistent": False,
            "message": "Le dossier de données n'est pas un volume Docker : tout sera perdu à la prochaine mise à jour. "
                       "Montez le volume « boleryos-data » sur /data.",
        }
    name = volume_name(root)
    created = _created_on(data_dir)
    if name and _ANONYMOUS_NAME.match(name):
        return {
            "persistent": False,
            "volume": "anonyme",
            "created": created,
            "message": "Le dossier de données est un volume anonyme : un redéploiement en crée un nouveau, vide, et tout est perdu. "
                       "Déclarez le volume « boleryos-data » sur /data (voir docker-compose.yml).",
        }
    return {"persistent": True, "message": "", "volume": name or "dossier monté", "created": created}
