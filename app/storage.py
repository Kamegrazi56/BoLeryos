"""Contrôle du dossier de données : survit-il à une mise à jour du conteneur ?

Le bot écrit tout dans `/data`. Si ce dossier n'est pas un volume monté depuis l'extérieur, il vit dans le conteneur
lui-même : le fichier s'écrit sans erreur, mais tout disparaît au premier redéploiement. On le détecte ici pour le dire
clairement à l'écran plutôt que de le découvrir après coup.
"""

from __future__ import annotations

import os

MOUNTINFO = "/proc/self/mountinfo"


def _mount_points() -> set[str] | None:
    """Points de montage du conteneur ; None si la liste n'est pas lisible (hors Linux, droits)."""
    try:
        with open(MOUNTINFO, encoding="utf8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    points: set[str] = set()
    for line in lines:
        fields = line.split()
        if len(fields) > 4:
            points.add(fields[4].replace("\\040", " "))
    return points


def storage_status(data_dir: str | None) -> dict:
    """{"persistent": True/False/None, "message": texte} ; None = on ne sait pas, aucun avertissement."""
    if not data_dir or not os.path.isdir(data_dir):
        return {"persistent": False, "message": "Aucun dossier de données : réglages et identifiants sont perdus à chaque arrêt."}
    if not os.access(data_dir, os.W_OK):
        return {"persistent": False, "message": "Le dossier de données n'est pas inscriptible : rien n'est enregistré."}
    points = _mount_points()
    if points is None:
        return {"persistent": None, "message": ""}
    if os.path.realpath(data_dir) in points:
        return {"persistent": True, "message": ""}
    return {
        "persistent": False,
        "message": "Le dossier de données n'est pas un volume Docker : tout sera perdu à la prochaine mise à jour. "
                   "Montez le volume « boleryos-data » sur /data.",
    }
