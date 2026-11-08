"""Fonctionnalités du bot et leurs réglages (activation, minuteur, priorité).

Chaque fonctionnalité a un interrupteur et un minuteur « entre X et Y minutes » : après chaque passage, le bot attend une
durée tirée au hasard entre les deux, puis relance. L'ordre de la liste est l'ordre de priorité. Les réglages sont gardés
dans `<data_dir>/features.json` et survivent à un redémarrage ; seul l'interrupteur général du bot repasse à OFF.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable


MAX_MINUTES = 24 * 60.0
FILE_NAME = "features.json"
DEFAULT_ENTRY: dict = {"enabled": False, "min_minutes": None, "max_minutes": None}


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    description: str
    run: Callable[[object], Awaitable[str]]  # reçoit le bot, renvoie une phrase de résultat pour le journal
    timer_label: str = "Relancer toutes les"  # texte devant le minuteur dans l'onglet Fonctionnalités
    # Relance automatique (Fouilles, Piratage) : pas de minuteur ; après chaque passage, le suivant est programmé à
    # l'heure renvoyée par `next_due(bot)` (fin de mission + délai du joueur), avec une vérification de secours.
    self_timed: bool = False
    next_due: Callable[[object], float | None] | None = None


async def _run_constructions(bot) -> str:
    return await bot.builder.run_pass()


async def _run_researches(bot) -> str:
    return await bot.researcher.run_pass()


async def _run_populations(bot) -> str:
    return await bot.recruiter.run_pass()


async def _run_ships(bot) -> str:
    return await bot.shipper.run_pass()


async def _run_components(bot) -> str:
    return await bot.producer.run_pass()


async def _run_explorations(bot) -> str:
    return await bot.explorer.run_pass()


async def _run_excavations(bot) -> str:
    return await bot.excavator.run_pass()


async def _run_hackings(bot) -> str:
    return await bot.hacker.run_pass()


async def _run_alliance_tokens(bot) -> str:
    return await bot.token_giver.run_pass()


async def _run_test(bot) -> str:
    return "passage de test : rien n'a été fait dans le jeu"


# Liste des fonctionnalités, de la plus prioritaire (par défaut) à la moins prioritaire.
# La fonctionnalité de test ne touche pas au jeu : elle sert à vérifier minuteurs, priorités et interrupteurs.
FEATURES: tuple[Feature, ...] = (
    Feature("constructions", "Constructions de bâtiments", "Monte les bâtiments de la liste (onglet Constructions), dans l'ordre de priorité, jusqu'au niveau voulu : clique sur « Améliorer » et dépense les ressources du jeu.", _run_constructions),
    Feature("researches", "Recherches du Laboratoire", "Lance les recherches de la liste (onglet Recherches), dans l'ordre de priorité, jusqu'au niveau voulu : clique sur « Rechercher » sur la planète chargée des recherches et dépense ses ressources. Une seule recherche à la fois.", _run_researches),
    Feature("populations", "Recrutement de population", "Recrute les populations de la liste (onglet Population) : quantité fixe ou MAX à chaque passage, ou effectif à atteindre, population par population et planète par planète. Clique sur « Recruter » et dépense les ressources de la planète.", _run_populations),
    Feature("ships", "Assemblages de vaisseaux", "Construit les vaisseaux de la liste (onglet Assemblages) : quantité fixe ou MAX à chaque passage ou une seule fois, ou effectif à atteindre, sur toutes les planètes, une seule ou plusieurs. Construit avec des ressources (mode Construction), clique sur « Lancer » et dépense les ressources de la planète.", _run_ships),
    Feature("components", "Production de composants", "Produit les composants de la liste (onglet Composants) : quantité fixe ou MAX à chaque passage ou une seule fois, ou effectif à atteindre, sur toutes les planètes, une seule ou plusieurs. Clique sur « Produire » et dépense les ressources de la planète.", _run_components),
    Feature("explorations", "Explorations", "Envoie des explorations (onglet Exploration) vers la brèche la plus proche de chaque planète choisie : Piliers ou Flotte, modèle du jeu ou quantités, copies et durée au choix. Relève brèches et modèles une fois par jour, respecte les quotas quotidien et d'explorations actives, et saute une planète à qui il manque quelque chose.", _run_explorations,
            timer_label="Vague d'explorations toutes les"),
    Feature("excavations", "Fouilles", "Lance les Fouilles des lignes réglées (onglet Fouilles) : planète de départ, équipe (1 à 5 000 unités), durée, nombre de places et Mira Venn. Vise le site libre le plus proche sans Fouille en cours, relève les sites une fois par jour, et relance une place à la fin de sa Fouille plus le délai choisi.", _run_excavations,
            self_timed=True, next_due=lambda bot: bot.excavator.next_due()),
    Feature("hackings", "Piratage (expérience de Naël)", "Fait pirater en boucle par Naël Soryn l'Errant le plus proche de la planète choisie (onglet Piratage), pour son expérience : relance dès que le piratage précédent est fini et que le délai choisi est passé, et passe à l'Errant suivant si le jeu refuse la cible. Relève les Errants une fois par jour.", _run_hackings,
            self_timed=True, next_due=lambda bot: bot.hacker.next_due()),
    Feature("alliance_tokens", "Dons de jetons d'alliance", "Donne vos jetons d'alliance (onglet Alliance : tous, 1 ou 2 à chaque passage) à la technologie marquée « priorité de dons de l'alliance ». Ne donne rien si aucune technologie n'est prioritaire ; ne change jamais la priorité. Un jeton revient environ toutes les 2 h (6 au plus).", _run_alliance_tokens,
            timer_label="Donner des jetons toutes les"),
    Feature("test", "Test (ne fait rien)", "Écrit une ligne dans le journal à chaque passage. À retirer quand les vraies fonctionnalités arrivent.", _run_test),
)


class FeaturesError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_entry(entry: dict, floor: float, timed: bool = True) -> dict:
    """Entrée complète et valide. Activer sans minuteur renseigné est refusé (sauf relance automatique : `timed` faux)."""
    errors: list[str] = []
    out = {"enabled": entry.get("enabled", False), "min_minutes": entry.get("min_minutes"), "max_minutes": entry.get("max_minutes")}
    if not isinstance(out["enabled"], bool):
        errors.append("Activation : oui ou non.")
    low, high = out["min_minutes"], out["max_minutes"]
    for name, value in (("minimum", low), ("maximum", high)):
        if value is not None and (not _is_number(value) or not floor <= value <= MAX_MINUTES):
            errors.append(f"Minuteur : le {name} doit être entre {floor:g} et {MAX_MINUTES:g} minutes.")
    if not errors and low is not None and high is not None and low > high:
        errors.append("Minuteur : le minimum dépasse le maximum.")
    if not errors and timed and out["enabled"] is True and (low is None or high is None):
        errors.append("Renseignez le minuteur (minimum et maximum en minutes) avant d'activer.")
    if errors:
        raise FeaturesError(errors)
    for key in ("min_minutes", "max_minutes"):
        if out[key] is not None:
            out[key] = float(out[key])
    return out


class FeatureStore:
    def __init__(self, data_dir: str | None, features: tuple[Feature, ...], floor: float) -> None:
        self._ids = [f.id for f in features]
        self._timed = {f.id: not f.self_timed for f in features}
        self._floor = floor
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._entries = {i: copy.deepcopy(DEFAULT_ENTRY) for i in self._ids}
        self._order = list(self._ids)
        if self._path is not None:
            self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self._path.read_text(encoding="utf8"))  # type: ignore[union-attr]
        except (OSError, ValueError):
            return  # absent ou illisible : valeurs par défaut
        if not isinstance(saved, dict):
            return
        entries = saved.get("features")
        for fid in self._ids:
            raw = entries.get(fid) if isinstance(entries, dict) else None
            if isinstance(raw, dict):
                try:
                    self._entries[fid] = validate_entry(raw, self._floor, self._timed[fid])
                except FeaturesError:
                    pass  # entrée refusée : fonctionnalité éteinte, minuteur à refaire
        order = saved.get("order")
        if isinstance(order, list):
            seen: list[str] = []
            for i in order:
                if i in self._ids and i not in seen:
                    seen.append(i)
            self._order = seen + [i for i in self._ids if i not in seen]  # nouvelle fonctionnalité : en dernier

    @property
    def persistent(self) -> bool:
        return self._path is not None

    @property
    def order(self) -> list[str]:
        return list(self._order)

    def get(self, fid: str) -> dict:
        return dict(self._entries[fid])

    def update(self, fid: str, patch: dict) -> dict:
        if fid not in self._entries:
            raise KeyError(fid)
        cleaned = validate_entry({**self._entries[fid], **patch}, self._floor, self._timed[fid])
        self._entries[fid] = cleaned
        self._save()
        return dict(cleaned)

    def set_order(self, order: list[str]) -> list[str]:
        if sorted(order) != sorted(self._ids):
            raise FeaturesError(["Priorités : la liste doit contenir chaque fonctionnalité une seule fois."])
        self._order = list(order)
        self._save()
        return self.order

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".features-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({"order": self._order, "features": self._entries}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass  # l'enregistrement est facultatif : le bot continue en mémoire
