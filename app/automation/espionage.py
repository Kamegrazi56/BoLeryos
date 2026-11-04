"""Espionnage automatique : lecture de la liste de la Galaxie et choix d'UNE cible par passage.

État actuel : SIMULATION SEULEMENT. Le bot lit la Galaxie et dit qui il espionnerait ; l'envoi réel (bouton « Sonde »
ou « Drone ») n'est pas codé : l'export ne montre pas ce qui se passe après le clic.

Structure relevée sur le jeu (export du 2026-11-03) : la liste `continuous-inspector__list-row` affiche, pour chaque
planète, le joueur « Nom [TAG] » (les errants ont le tag [AI]), « Planète · I27 95 00 », puis des boutons rapides
« Flotte », « Piliers », « Sonde », « Drone ». Les planètes minières et les brèches n'ont pas de bouton « Flotte ».
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .numbers import norm
from .settings import coord_key

READ_GALAXY_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  return [...document.querySelectorAll('.continuous-inspector__list-row')].map((row) => {
    const buttons = [...row.querySelectorAll('button')];
    const labels = buttons.map((b) => b.getAttribute('aria-label') || '').filter(Boolean);
    const texts = buttons.map(text).filter(Boolean);
    return { texts, actions: labels };
  });
}
"""

_PLAYER_RE = re.compile(r"^(?P<name>.+?)(?:\s*\[(?P<tag>[^\]]+)\])?$")
_PLANET_RE = re.compile(r"^(?P<planet>.*?)\s*·\s*(?P<coords>[A-Z]\d{2} \d{2} \d{2})$")


@dataclass(frozen=True)
class Target:
    player: str
    tag: str
    planet: str
    coords: str
    actions: tuple[str, ...]

    @property
    def errant(self) -> bool:
        return self.tag.upper() == "AI"

    def label(self) -> str:
        who = f"{self.player} [{self.tag}]" if self.tag else self.player
        return f"{who} ({self.planet}, {self.coords})"


def parse_galaxy(rows: list[dict]) -> list[Target]:
    """Planètes de joueurs et d'errants de la liste (lignes avec un bouton « Flotte »), sans doublon."""
    out: list[Target] = []
    seen: set[str] = set()
    for row in rows:
        actions = tuple(row.get("actions", []))
        if "Flotte" not in actions:
            continue  # planète minière, brèche, ou une de vos planètes
        texts = [t for t in row.get("texts", []) if t]
        planet_line = next((t for t in texts if _PLANET_RE.match(t)), None)
        player_line = next((t for t in texts if t != planet_line), None)
        if not planet_line or not player_line:
            continue
        pm, who = _PLANET_RE.match(planet_line), _PLAYER_RE.match(player_line)
        target = Target(who.group("name").strip(), (who.group("tag") or "").strip(), pm.group("planet").strip(),
                        pm.group("coords"), actions)
        if target.coords not in seen:
            seen.add(target.coords)
            out.append(target)
    return out


def my_tag(rows: list[dict], my_planets: list[str]) -> str:
    """Tag d'alliance du joueur : celui affiché sur une ligne dont la planète est à lui."""
    mine = {norm(p) for p in my_planets}
    for row in rows:
        texts = row.get("texts", [])
        planet_line = next((t for t in texts if _PLANET_RE.match(t)), "")
        match = _PLANET_RE.match(planet_line)
        if match and norm(match.group("planet")) in mine:
            for t in texts:
                who = _PLAYER_RE.match(t)
                if t != planet_line and who and who.group("tag"):
                    return who.group("tag").strip()
    return ""


def eligible(targets: list[Target], spy: dict, my_planets: list[str], alliance: str) -> list[Target]:
    lo, hi = coord_key(spy["coord_min"]), coord_key(spy["coord_max"])
    mine = {norm(p) for p in my_planets}
    out = []
    for t in targets:
        key = coord_key(t.coords)
        if key is None or lo is None or hi is None or not lo <= key <= hi:
            continue
        if norm(t.planet) in mine:
            continue
        if t.errant and not spy["errants"]:
            continue
        if not t.errant and not spy["players"]:
            continue
        if spy["exclude_alliance"] and alliance and norm(t.tag) == norm(alliance):
            continue
        out.append(t)
    return out


def choose(candidates: list[Target], last_spied: dict[str, float]) -> Target | None:
    """Une cible par passage : jamais espionnée d'abord, sinon celle espionnée il y a le plus longtemps."""
    if not candidates:
        return None
    return min(candidates, key=lambda t: (last_spied.get(t.coords, 0.0), coord_key(t.coords)))


def buttons_for(way: str) -> list[str]:
    return {"sonde": ["Sonde"], "drone": ["Drone"], "both": ["Sonde", "Drone"]}[way]
