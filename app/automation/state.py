"""Lecture de l'état du jeu à partir de la page affichée (cartes, ressources, planètes)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .numbers import display_step, parse_amount, parse_duration

ACTION_LABELS = ("Améliorer", "Rechercher", "Ressources insuffisantes")

# Exécuté dans le navigateur. Structure relevée sur le jeu : une carte par bâtiment ou technologie, avec un titre
# (h3), une pastille de niveau (nombre seul, avant le titre), des lignes « ressource / montant », une durée et un
# bouton d'action. Les ressources du bandeau du haut sont écrites « valeur » puis « LIBELLÉ » (FER, CRISTAL…),
# et la planète active suit le libellé masqué « Favoris ».
READ_PAGE_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const leaves = [...document.body.querySelectorAll('*')].filter(
    (el) => el.children.length === 0 && text(el) !== '' && !el.matches('script,style,noscript'));
  const LABELS = { 'FER': 'fer', 'CRISTAL': 'cristal', 'ÉTHER': 'ether', 'ÉNERGIE': 'energie', 'ASTRIUM': 'astrium', 'ÉCHOS': 'echos' };
  const resources = {};
  leaves.forEach((el, i) => {
    const t = text(el);
    if (LABELS[t] && !(LABELS[t] in resources) && i > 0) resources[LABELS[t]] = text(leaves[i - 1]);
  });
  const fav = leaves.findIndex((el) => text(el) === 'Favoris');
  const planet = fav >= 0 && leaves[fav + 1] ? text(leaves[fav + 1]) : '';
  const coords = fav >= 0 && leaves[fav + 2] ? text(leaves[fav + 2]) : '';
  const ACTIONS = %ACTIONS%;
  const isAction = (b) => ACTIONS.includes(text(b));
  const cards = [];
  for (const btn of document.querySelectorAll('button')) {
    if (!isAction(btn)) continue;
    let card = btn.parentElement;
    while (card && card !== document.body) {
      const n = [...card.querySelectorAll('button')].filter(isAction).length;
      if (n > 1) { card = null; break; }
      if (card.querySelector('h1,h2,h3,h4')) break;
      card = card.parentElement;
    }
    if (!card || card === document.body) continue;
    const heading = card.querySelector('h3,h4,h2,h1');
    const inCard = leaves.filter((el) => card.contains(el));
    const nameIdx = inCard.findIndex((el) => heading.contains(el));
    const levelLeaf = inCard.slice(0, Math.max(nameIdx, 0)).find((el) => /^\\d+$/.test(text(el)))
      || inCard.find((el) => /^\\d+$/.test(text(el)));
    const costs = {};
    let duration = '';
    inCard.forEach((el, i) => {
      const t = text(el);
      const next = inCard[i + 1] ? text(inCard[i + 1]) : '';
      if (['Fer', 'Cristal', 'Éther'].includes(t) && next) costs[t] = next;
      if (['Durée', 'Temps de recherche'].includes(t) && /\\d/.test(next) && !ACTIONS.includes(next)) duration = next;
    });
    cards.push({
      name: text(heading), level: levelLeaf ? text(levelLeaf) : '', costs, duration,
      action: text(btn), enabled: !btn.disabled && btn.getAttribute('aria-disabled') !== 'true',
    });
  }
  const h2 = document.querySelector('main h2, h2');
  return { path: location.pathname, section: h2 ? text(h2) : '', planet, coords, resources, cards };
}
""".replace("%ACTIONS%", str(list(ACTION_LABELS)).replace("'", '"'))

# Liste des planètes (fenêtre « Planètes » ouverte) : chaque entrée est un bouton « nom, coordonnées, stocks ».
READ_PLANETS_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const COORD = /^[A-Z]\\d{2} \\d{2} \\d{2}$/;
  const out = [];
  for (const b of document.querySelectorAll('button')) {
    const leaves = [...b.querySelectorAll('*')].filter((el) => el.children.length === 0 && text(el) !== '');
    const ci = leaves.findIndex((el) => COORD.test(text(el)));
    if (ci < 1 || leaves.length < 5) continue;
    out.push({ name: text(leaves[ci - 1]), coords: text(leaves[ci]), stocks: leaves.slice(ci + 1, ci + 4).map(text) });
  }
  return out;
}
"""


@dataclass
class Card:
    name: str
    level: int | None
    costs: dict[str, float]  # « Fer » / « Cristal » / « Éther » → montant
    duration_s: float | None
    action: str
    enabled: bool
    unreadable: list[str] = field(default_factory=list)  # nombres que le bot n'a pas compris


@dataclass
class Resources:
    fer: float | None = None
    cristal: float | None = None
    ether: float | None = None
    energie: float | None = None
    # Plus petite variation visible de chaque stock (« 3,201 G » : 1 M) : un petit débit peut passer inaperçu.
    steps: dict[str, float] = field(default_factory=dict)

    def step(self, label: str) -> float | None:
        return self.steps.get(label)

    def stock(self, label: str) -> float | None:
        return {"Fer": self.fer, "Cristal": self.cristal, "Éther": self.ether}[label]


@dataclass
class PageState:
    path: str
    section: str
    planet: str
    coords: str
    resources: Resources
    cards: list[Card]


@dataclass
class PlanetInfo:
    name: str
    coords: str
    stocks: tuple[float | None, float | None, float | None]  # Fer, Cristal, Éther


def _amount(raw: str | None) -> float | None:
    try:
        return parse_amount(raw) if raw else None
    except ValueError:
        return None


def parse_page(raw: dict) -> PageState:
    res = raw.get("resources", {})
    resources = Resources(_amount(res.get("fer")), _amount(res.get("cristal")), _amount(res.get("ether")), _amount(res.get("energie")))
    for key, label in (("fer", "Fer"), ("cristal", "Cristal"), ("ether", "Éther")):
        step = display_step(res[key]) if res.get(key) else None
        if step is not None:
            resources.steps[label] = step
    cards: list[Card] = []
    for c in raw.get("cards", []):
        bad: list[str] = []
        costs: dict[str, float] = {}
        for label, value in c.get("costs", {}).items():
            try:
                costs[label] = parse_amount(value)
            except ValueError:
                bad.append(f"{label} {value!r}")
        try:
            level: int | None = int(c["level"])
        except (KeyError, ValueError):
            level = None
        try:
            duration = parse_duration(c["duration"]) if c.get("duration") else None
        except ValueError:
            duration = None
        cards.append(Card(c["name"], level, costs, duration, c["action"], bool(c["enabled"]), bad))
    return PageState(raw.get("path", ""), raw.get("section", ""), raw.get("planet", ""), raw.get("coords", ""), resources, cards)


def parse_planets(raw: list[dict]) -> list[PlanetInfo]:
    return [
        PlanetInfo(p["name"], p["coords"], tuple(_amount(s) for s in (p["stocks"] + [None, None, None])[:3]))  # type: ignore[arg-type]
        for p in raw
    ]
