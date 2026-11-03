"""Production d'un nombre d'éléments (population, vaisseaux, composants, appareils, défenses).

Les décisions sont prises ici, sans navigateur : le bot ne lance une production que si les ressources de la planète
couvrent la quantité demandée (tout ou rien), ou clique sur le bouton MAX du jeu quand le joueur a choisi « maximum ».
Un nombre illisible, un stock actuel inconnu ou un coût inconnu : le bot ne lance rien et le dit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .numbers import norm, parse_amount
from .state import Resources

# Libellés de coût affichés par le jeu sur les cartes de production : F = Fer, C = Cristal, E = Éther.
COST_KEYS = {"F": "Fer", "C": "Cristal", "E": "Éther", "FER": "Fer", "CRISTAL": "Cristal", "ÉTHER": "Éther"}
_COST_RE = re.compile(r"^\s*([A-ZÉ]+)\s*:\s*(.+?)\s*$")


@dataclass
class ProdCard:
    name: str
    owned: int | None
    queued: int
    costs: dict[str, float]  # coût d'UNE unité : « Fer » / « Cristal » / « Éther »
    can_produce: bool  # le bouton de production existe et est actif
    has_controls: bool
    unreadable: list[str] = field(default_factory=list)


def parse_count(text: str | None) -> int | None:
    """« 2 507 383 », « 12 393 », « ×91 », « x21331 » → entier ; None si illisible."""
    if text is None:
        return None
    cleaned = re.sub(r"^[×xX]\s*", "", str(text).strip())
    cleaned = cleaned.replace(" ", "").replace(" ", "").replace(" ", "")
    return int(cleaned) if re.fullmatch(r"\d+", cleaned) else None


def parse_costs(raw: dict[str, str], unreadable: list[str]) -> dict[str, float]:
    costs: dict[str, float] = {}
    for key, value in raw.items():
        label = COST_KEYS.get(key.strip().upper())
        if label is None:
            continue
        try:
            costs[label] = parse_amount(value)
        except ValueError:
            unreadable.append(f"{key}: {value}")
    return costs


def parse_prod_card(raw: dict) -> ProdCard:
    unreadable: list[str] = []
    owned = parse_count(raw.get("owned"))
    if raw.get("owned") not in (None, "") and owned is None:
        unreadable.append(str(raw.get("owned")))
    return ProdCard(
        name=str(raw.get("name", "")).strip(),
        owned=owned,
        queued=int(raw.get("queued", 0) or 0),
        costs=parse_costs(raw.get("costs") or {}, unreadable),
        can_produce=bool(raw.get("can_produce")),
        has_controls=bool(raw.get("has_controls")),
        unreadable=unreadable,
    )


def affordable(costs: dict[str, float], resources: Resources) -> int | None:
    """Nombre d'unités payables avec les stocks de la planète ; None si on ne peut pas le savoir."""
    counts = []
    for label, unit in costs.items():
        if unit <= 0:
            continue
        stock = resources.stock(label)
        if stock is None:
            return None
        counts.append(math.floor(stock / unit))
    return min(counts) if counts else None


@dataclass
class ProdDecision:
    action: str  # « launch » | « wait » | « reached » | « skip »
    qty: int | None = None  # None avec « launch » : bouton MAX du jeu
    reason: str = ""
    expected: int | None = None  # nombre d'unités attendu (pour la vérification du débit)

    def label(self) -> str:
        return "MAX" if self.qty is None else f"{self.qty}"


def decide_production(item: dict, card: ProdCard | None, resources: Resources, done: bool) -> ProdDecision:
    """`item` : ligne de réglage {mode, amount, max}. `done` : commande unique déjà faite sur cette planète."""
    mode = item["mode"]
    if mode == "once" and done:
        return ProdDecision("reached", reason="commande déjà faite sur cette planète")
    if card is None:
        return ProdDecision("skip", reason="élément introuvable sur la page")
    if card.unreadable:
        return ProdDecision("skip", reason=f"nombre illisible ({card.unreadable[0]})")
    if not card.has_controls:
        return ProdDecision("skip", reason="pas de bouton de production pour cet élément")
    if not card.costs:
        return ProdDecision("skip", reason="coût illisible")
    can = affordable(card.costs, resources)
    if can is None:
        return ProdDecision("skip", reason="ressources de la planète illisibles")

    if item["max"]:
        if can < 1 or not card.can_produce:
            return ProdDecision("wait", reason="ressources insuffisantes pour une seule unité")
        return ProdDecision("launch", None, expected=can)

    wanted = int(item["amount"])
    if mode == "stock":
        if card.owned is None:
            return ProdDecision("skip", reason="stock actuel illisible")
        missing = wanted - card.owned - card.queued
        if missing <= 0:
            have = card.owned + card.queued
            return ProdDecision("reached", reason=f"objectif de stock atteint ({have} sur {wanted})")
        wanted = missing
    if can < wanted or not card.can_produce:
        return ProdDecision("wait", reason=f"ressources insuffisantes : {max(can, 0)} payable(s) sur {wanted}")
    return ProdDecision("launch", wanted, expected=wanted)


def describe(item: dict, decision: ProdDecision) -> str:
    return f"{item['name']} (maximum)" if decision.qty is None else f"{decision.qty} × {item['name']}"


def signature(item: dict) -> str:
    """Identifie le contenu d'une ligne : une commande unique modifiée redevient à faire."""
    return f"{item['section']}|{norm(item['name'])}|{item['mode']}|{item['amount']}|{item['max']}"


# Lecture d'une page de production, exécutée dans le navigateur. `kind` : population | ships | components | other
# (appareils spécialisés et défenses). Structure relevée sur le jeu (export du 2026-11-03) : cartes avec un titre h3,
# une pastille « stock » AVANT le titre, des coûts « F: … / C: … / E: … », un champ numérique, un bouton MAX et un
# bouton « Recruter » ou « Lancer » ; modèles de vaisseaux = `ship-assembly-card` avec « En service : N » ; composants =
# `component-production-row` avec « ×N » ; file d'attente de la population = `queue-operation-units` (« nom », « xN »).
READ_PRODUCTION_JS = """
(kind) => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const leaves = (root) => [...root.querySelectorAll('*')].filter((e) => e.children.length === 0 && text(e) !== '');
  const COST = /^([FCE]):\\s*(.+)$/;
  const costsOf = (root) => {
    const c = {};
    leaves(root).forEach((e) => { const m = text(e).match(COST); if (m) c[m[1]] = m[2]; });
    return c;
  };
  const enabled = (b) => !!b && !b.disabled && b.getAttribute('aria-disabled') !== 'true';
  const cards = [];
  if (kind === 'ships') {
    document.querySelectorAll('[data-testid="ship-assembly-card"]').forEach((card) => {
      const control = card.querySelector('[data-testid="ship-production-control"]');
      if (!control) return;
      const head = [...card.querySelectorAll('button')].find((b) => /En service/.test(text(b)));
      if (!head) return;
      const first = leaves(head)[0];
      const m = text(head).match(/En service\\s*:\\s*([\\d\\s\u00a0\u202f]+)/);
      const costs = {};
      control.querySelectorAll('[aria-label]').forEach((e) => {
        const mm = (e.getAttribute('aria-label') || '').match(/^(FER|CRISTAL|ÉTHER)\\s*:\\s*(.+)$/);
        if (mm) costs[mm[1]] = mm[2];
      });
      const buttons = [...control.querySelectorAll('button')];
      const go = buttons.find((b) => text(b) === 'Lancer');
      cards.push({ name: first ? text(first) : '', owned: m ? m[1].trim() : null, costs, queued: 0,
        has_controls: !!go && !!control.querySelector('input[type=number]'), can_produce: enabled(go) });
    });
  } else if (kind === 'components') {
    document.querySelectorAll('[data-testid="component-production-row"]').forEach((row) => {
      const ls = leaves(row);
      const stock = ls.map(text).find((t) => /^[×x]\\s*\\d/.test(t));
      const controls = row.querySelector('[data-testid="component-row-controls"]');
      const buttons = controls ? [...controls.querySelectorAll('button')] : [];
      const go = buttons.length >= 2 ? buttons[buttons.length - 1] : null;
      cards.push({ name: ls.length ? text(ls[0]) : '', owned: stock || null, costs: costsOf(row), queued: 0,
        has_controls: !!go && !!(controls && controls.querySelector('input[type=number]')), can_produce: enabled(go) });
    });
  } else {
    const done = new Set();
    document.querySelectorAll('h3').forEach((h) => {
      let card = h.parentElement;
      while (card && card !== document.body && !card.querySelector('input[type=number]')) card = card.parentElement;
      if (!card || card === document.body || done.has(card) || card.querySelectorAll('input[type=number]').length !== 1) return;
      done.add(card);
      const ls = leaves(card);
      const at = ls.findIndex((e) => h.contains(e));
      let owned = null;
      for (let i = at - 1; i >= 0; i--) { const t = text(ls[i]); if (/^[\\d\\s\u00a0\u202f]+$/.test(t)) { owned = t; break; } }
      const go = [...card.querySelectorAll('button')].find((b) => /^(Recruter|Lancer)\\b/.test(b.getAttribute('aria-label') || text(b)));
      cards.push({ name: text(h), owned, costs: costsOf(card), queued: 0, has_controls: !!go, can_produce: enabled(go) });
    });
  }
  const queue = {};
  document.querySelectorAll('[data-testid="queue-operation-units"]').forEach((q) => {
    const ls = leaves(q).map(text);
    ls.forEach((t, i) => {
      const m = t.match(/^x(\\d+)$/);
      if (m && i > 0) queue[ls[i - 1]] = (queue[ls[i - 1]] || 0) + parseInt(m[1], 10);
    });
  });
  cards.forEach((c) => { c.queued = queue[c.name] || 0; });
  return { cards, queue };
}
"""
