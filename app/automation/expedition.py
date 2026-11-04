"""Expéditions (Opérations › Exploration) : lecture de la page et décisions.

État actuel : SIMULATION SEULEMENT. Le bot lit la page et dit ce qu'il lancerait ; le lancement réel n'est pas codé
(l'export ne montre ni les choix des listes « Durée nominale » et « Copies », ni le bouton de lancement une fois des
unités choisies, ni une exploration en cours). Rien ici ne clique sur un bouton de lancement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .numbers import norm
from .production import parse_count

_RATIO_RE = re.compile(r"^\s*(\d[\d\s]*)\s*/\s*(\d[\d\s]*)\s*$")


def parse_ratio(text: str | None) -> tuple[int, int] | None:
    """« 0/46 » → (0, 46)."""
    match = _RATIO_RE.match(str(text or "").replace(" ", " ").replace(" ", " "))
    if not match:
        return None
    return int(match.group(1).replace(" ", "")), int(match.group(2).replace(" ", ""))


@dataclass
class ExpeditionPage:
    way: str  # « piliers » | « flotte »
    active: int | None
    active_max: int | None
    daily: int | None
    daily_max: int | None
    available: dict[str, int]  # nom normalisé → « Disponibles : N »
    names: dict[str, str] = field(default_factory=dict)  # nom normalisé → nom affiché
    unreadable: list[str] = field(default_factory=list)


def parse_expedition_page(way: str, raw: dict) -> ExpeditionPage:
    unreadable: list[str] = []
    active = parse_ratio(raw.get("active"))
    daily = parse_ratio(raw.get("daily"))
    if raw.get("active") and active is None:
        unreadable.append(f"actives : {raw.get('active')}")
    if raw.get("daily") and daily is None:
        unreadable.append(f"quotidiennes : {raw.get('daily')}")
    available: dict[str, int] = {}
    names: dict[str, str] = {}
    for unit in raw.get("units", []):
        count = parse_count(unit.get("available"))
        name = str(unit.get("name", "")).strip()
        if not name:
            continue
        if count is None:
            unreadable.append(f"{name} : {unit.get('available')}")
            continue
        available[norm(name)] = count
        names[norm(name)] = name
    return ExpeditionPage(way, active[0] if active else None, active[1] if active else None,
                          daily[0] if daily else None, daily[1] if daily else None, available, names, unreadable)


@dataclass
class PlannedLaunch:
    line: dict
    units: list[tuple[str, int]]

    def describe(self) -> str:
        way = "les Piliers" if self.line["way"] == "piliers" else "la Flotte"
        units = ", ".join(f"{qty} × {name}" for name, qty in self.units)
        return f"une exploration par {way} ({self.line['hours']} h) avec {units}"


@dataclass
class ExpeditionPlan:
    launches: list[PlannedLaunch]
    notes: list[str]  # pourquoi rien (ou moins) n'est lancé
    free_slots: int | None


def free_slots(page: ExpeditionPage, target: int) -> tuple[int | None, str]:
    """Places libres : min(objectif − actives, maximum du jeu − actives, quota du jour restant)."""
    if page.active is None:
        return None, "nombre d'explorations actives illisible"
    limits = [target - page.active]
    if page.active_max is not None:
        limits.append(page.active_max - page.active)
    if page.daily is not None and page.daily_max is not None:
        limits.append(page.daily_max - page.daily)
    free = min(limits)
    if free > 0:
        return free, ""
    if target - page.active <= 0:
        return 0, f"{page.active} exploration(s) active(s) sur {target} voulue(s)"
    if page.active_max is not None and page.active_max - page.active <= 0:
        return 0, f"toutes les places du jeu sont occupées ({page.active}/{page.active_max})"
    return 0, f"quota du jour atteint ({page.daily}/{page.daily_max})"


def resolve_line(line: dict, available: dict[str, int], names: dict[str, str]) -> tuple[list[tuple[str, int]] | None, str]:
    """Quantités d'une ligne d'après les disponibilités ; (None, raison) si la ligne n'est pas réalisable en entier."""
    resolved: list[tuple[str, int]] = []
    for unit in line["units"]:
        key = norm(unit["name"])
        if key not in available:
            return None, f"« {unit['name']} » absent de la page"
        have = available[key]
        shown = names.get(key, unit["name"])
        if unit["mode"] == "max":
            qty = have
        elif unit["mode"] == "percent":
            qty = have * unit["value"] // 100
        else:
            qty = unit["value"]
            if have < qty:
                return None, f"{shown} : {have} disponible(s) sur {qty}"
        if qty < 1:
            return None, f"{shown} : aucun disponible"
        resolved.append((shown, qty))
    return resolved, ""


def plan_expeditions(lines: list[dict], pages: dict[str, ExpeditionPage], target: int) -> ExpeditionPlan:
    """Remplit les places libres avec la PREMIÈRE ligne réalisable (l'ordre de la liste = la priorité). Les unités
    d'une exploration prévue sont retirées des disponibilités avant la suivante. Les compteurs viennent de la page lue
    en premier (ils sont communs aux deux voies)."""
    if not pages:
        return ExpeditionPlan([], ["page des explorations illisible"], None)
    first = next(iter(pages.values()))
    if first.unreadable:
        return ExpeditionPlan([], [f"nombre illisible ({first.unreadable[0]})"], None)
    free, why = free_slots(first, target)
    if not free:
        return ExpeditionPlan([], [why or "aucune place libre"], free)
    remaining = {way: dict(page.available) for way, page in pages.items()}
    launches: list[PlannedLaunch] = []
    notes: list[str] = []
    for _ in range(free):
        chosen = None
        for line in lines:
            page = pages.get(line["way"])
            if page is None:
                continue
            units, reason = resolve_line(line, remaining[line["way"]], page.names)
            if units is not None:
                chosen = PlannedLaunch(line, units)
                break
            notes.append(reason)
        if chosen is None:
            break
        for name, qty in chosen.units:
            remaining[chosen.line["way"]][norm(name)] -= qty
        launches.append(chosen)
    return ExpeditionPlan(launches, list(dict.fromkeys(notes))[:3], free)


# Lecture de la page, exécutée dans le navigateur : compteurs « x/y » après leur libellé et « Disponibles : N » après
# le nom de chaque unité (structure de l'export du 2026-11-03).
READ_EXPEDITION_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const leaves = [...document.body.querySelectorAll('*')].filter((e) => e.children.length === 0 && text(e) !== '' && !e.matches('script,style,noscript')).map(text);
  const after = (label) => { const i = leaves.indexOf(label); return i >= 0 && leaves[i + 1] ? leaves[i + 1] : null; };
  const units = [];
  leaves.forEach((t, i) => {
    const m = t.match(/^Disponibles\\s*:\\s*(.+)$/);
    if (m && i > 0) units.push({ name: leaves[i - 1], available: m[1] });
  });
  return { daily: after('Explorations quotidiennes'), active: after('Explorations actives'), units };
}
"""
