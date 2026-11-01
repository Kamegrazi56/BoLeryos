"""Règles de décision (pures, sans navigateur) : que lancer, sur une planète, avec les ressources disponibles."""

from __future__ import annotations

from dataclasses import dataclass, field

from .numbers import norm
from .state import Card, Resources

# Mines concernées par la règle d'énergie (pas d'amélioration si le bilan d'énergie est négatif).
MINES = {norm("Mine de Fer"), norm("Extracteur de Cristal"), norm("Synthétiseur d'Éther")}


@dataclass
class Decision:
    card: Card
    target: int
    score: float  # plus grand rapport coût / stock : plus il est petit, moins l'amélioration pèse sur le stock

    def describe(self) -> str:
        costs = ", ".join(f"{k} {v:,.0f}".replace(",", " ") for k, v in self.card.costs.items())
        return f"{self.card.name} (niveau {self.card.level} → {self.card.level + 1}, objectif {self.target}) pour {costs}"


@dataclass
class Outcome:
    decision: Decision | None
    notes: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)  # améliorations voulues mais pas encore réalisables (avec la raison)
    reached: int = 0  # objectifs déjà atteints

    def summary(self) -> str:
        return "; ".join(self.notes) if self.notes else "rien à faire"


def decide(cards: list[Card], resources: Resources, targets: dict[str, int], action: str) -> Outcome:
    """`targets` : nom normalisé → niveau demandé. `action` : « Améliorer » ou « Rechercher »."""
    notes: list[str] = []
    candidates: list[Decision] = []
    reached = insufficient = 0
    energy_negative = resources.energie is not None and resources.energie < 0

    for card in cards:
        target = targets.get(norm(card.name))
        if target is None:
            continue
        if card.level is None:
            notes.append(f"niveau illisible pour {card.name}")
            continue
        if card.level >= target:
            reached += 1
            continue
        if card.unreadable:
            notes.append(f"coût illisible pour {card.name} ({', '.join(card.unreadable)})")
            continue
        if card.action != action or not card.enabled:
            insufficient += 1
            continue
        if not card.costs:
            notes.append(f"aucun coût lu pour {card.name}")
            continue
        scores: list[float] = []
        ok = True
        for label, cost in card.costs.items():
            stock = resources.stock(label)
            if stock is None:
                notes.append(f"stock de {label} illisible")
                ok = False
                break
            if cost > stock:
                ok = False
                insufficient += 1
                break
            scores.append(cost / stock if stock > 0 else 1.0)
        if not ok:
            continue
        if energy_negative and norm(card.name) in MINES:
            notes.append(f"énergie négative : {card.name} ignorée")
            continue
        candidates.append(Decision(card, target, max(scores)))

    if not candidates:
        if reached:
            notes.append(f"{reached} objectif(s) atteint(s)")
        if insufficient:
            notes.append(f"{insufficient} non lançable(s) (ressources insuffisantes ou bouton inactif)")
        return Outcome(None, notes)
    best = min(candidates, key=lambda d: (d.score, d.card.name))
    return Outcome(best, notes)


def decide_ordered(cards: list[Card], resources: Resources, queue: list[tuple[str, int]], action: str = "Améliorer") -> Outcome:
    """Parcourt la liste dans l'ordre choisi par le joueur et retient la PREMIÈRE amélioration réalisable.

    `queue` : [(nom, niveau demandé)] par ordre de priorité. Réalisable = niveau inférieur à l'objectif, bouton actif,
    ressources suffisantes sur la planète (et énergie non négative pour une mine).
    """
    by_name = {norm(c.name): c for c in cards}
    energy_negative = resources.energie is not None and resources.energie < 0
    notes: list[str] = []
    pending: list[str] = []
    reached = 0
    for name, target in queue:
        card = by_name.get(norm(name))
        if card is None:
            notes.append(f"{name} introuvable sur les pages lues")
            continue
        if card.level is None:
            notes.append(f"niveau illisible pour {card.name}")
            continue
        if card.level >= target:
            reached += 1
            continue
        reason = None
        scores: list[float] = []
        if card.unreadable:
            reason = f"coût illisible ({', '.join(card.unreadable)})"
        elif card.action != action or not card.enabled:
            reason = "ressources insuffisantes ou bouton inactif"
        elif not card.costs:
            reason = "aucun coût lu"
        else:
            for label, cost in card.costs.items():
                stock = resources.stock(label)
                if stock is None:
                    reason = f"stock de {label} illisible"
                    break
                if cost > stock:
                    reason = f"ressources insuffisantes ({label})"
                    break
                scores.append(cost / stock if stock > 0 else 1.0)
        if reason is None and energy_negative and norm(card.name) in MINES:
            reason = "énergie négative"
        if reason is None:
            return Outcome(Decision(card, target, max(scores)), notes, pending, reached)
        pending.append(f"{card.name} : {reason}")
    return Outcome(None, notes, pending, reached)
