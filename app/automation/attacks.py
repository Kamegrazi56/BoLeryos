"""Alerte d'attaque : lecture des « Capteurs interstellaires » (sans clic) et repérage des attaques qui arrivent.

Structure relevée sur le jeu (export du 2026-11-03) : chaque mouvement du panneau des capteurs s'écrit
« type », « distance (U) », « Arrivée: HH:MM:SS », « compte à rebours », puis « Origine » planète / coordonnées /
joueur et « Destination » planète / coordonnées / joueur. Le contenu du panneau est présent dans la page même quand il
est fermé : le bot le lit sans l'ouvrir.

NON VÉRIFIÉ sur une vraie attaque : l'export ne contient qu'un déploiement du joueur. Le type d'une attaque est
supposé contenir « attaque » ; un mouvement est « entrant et étranger » quand sa destination est une de vos planètes
et que son origine n'en est pas une.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .numbers import norm

READ_SENSORS_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const leaves = [...document.body.querySelectorAll('*')]
    .filter((e) => e.children.length === 0 && text(e) !== '' && !e.matches('script,style,noscript')).map(text);
  const out = [];
  leaves.forEach((t, i) => {
    if (t !== 'Origine' || leaves[i + 4] !== 'Destination') return;
    const arrivalAt = [i - 1, i - 2, i - 3].find((k) => k >= 0 && /^Arriv[ée]e?\\s*:/i.test(leaves[k]));
    const typeAt = arrivalAt === undefined ? -1 : arrivalAt - 2;
    out.push({
      type: typeAt >= 0 ? leaves[typeAt] : '',
      arrival: arrivalAt === undefined ? '' : leaves[arrivalAt].replace(/^Arriv[ée]e?\\s*:\\s*/i, ''),
      countdown: arrivalAt === undefined ? '' : (leaves[arrivalAt + 1] || ''),
      origin: { planet: leaves[i + 1], coords: leaves[i + 2], player: leaves[i + 3] },
      destination: { planet: leaves[i + 5], coords: leaves[i + 6], player: leaves[i + 7] },
    });
  });
  const badges = [...document.querySelectorAll('[class*="game-utility-badge"]')].map((b) => ({ cls: b.className, text: text(b) }));
  return { movements: out, badges };
}
"""

@dataclass(frozen=True)
class Movement:
    type: str
    arrival: str
    countdown: str
    origin_planet: str
    origin_coords: str
    origin_player: str
    dest_planet: str
    dest_coords: str
    dest_player: str

    @property
    def key(self) -> str:
        """Identifie un mouvement d'une lecture à l'autre (le compte à rebours change, pas le reste)."""
        return f"{norm(self.type)}|{self.origin_coords}|{self.dest_coords}|{self.arrival}"

    def describe(self) -> str:
        when = f"arrivée {self.arrival}" + (f" (dans {self.countdown})" if self.countdown else "") if self.arrival else "heure d'arrivée inconnue"
        return (f"{self.type or 'mouvement'} de {self.origin_player or '?'} ({self.origin_planet} {self.origin_coords}) "
                f"vers {self.dest_planet} ({self.dest_coords}), {when}")


def parse_movements(raw: dict) -> list[Movement]:
    seen: set[str] = set()
    out: list[Movement] = []
    for m in raw.get("movements", []):
        o, d = m.get("origin") or {}, m.get("destination") or {}
        mv = Movement(str(m.get("type", "")).strip(), str(m.get("arrival", "")).strip(), str(m.get("countdown", "")).strip(),
                      str(o.get("planet", "")).strip(), str(o.get("coords", "")).strip(), str(o.get("player", "")).strip(),
                      str(d.get("planet", "")).strip(), str(d.get("coords", "")).strip(), str(d.get("player", "")).strip())
        if mv.key not in seen:  # le panneau peut figurer deux fois dans la page (vue d'ensemble + panneau rangé)
            seen.add(mv.key)
            out.append(mv)
    return out


def badge_count(raw: dict) -> int:
    total = 0
    for badge in raw.get("badges", []):
        if re.fullmatch(r"\d+", str(badge.get("text", ""))):
            total += int(badge["text"])
    return total


def _coords(text: str) -> str:
    return " ".join(str(text).upper().split())


def incoming_attacks(movements: list[Movement], my_planets: list[str], my_coords: list[str] | None = None) -> list[Movement]:
    """Attaques qui arrivent sur une de vos planètes et viennent d'ailleurs.

    « Vos planètes » : de préférence leurs COORDONNÉES (uniques, alors qu'une planète ennemie peut porter le même nom
    qu'une des vôtres) ; à défaut, leurs noms lus dans le jeu ; si rien n'est connu, on compare les joueurs (origine
    différente de la destination)."""
    coords = {_coords(c) for c in (my_coords or []) if c}
    mine = {norm(p) for p in my_planets}
    out = []
    for mv in movements:
        if "attaq" not in norm(mv.type):
            continue
        if coords and mv.dest_coords and mv.origin_coords:
            if _coords(mv.dest_coords) in coords and _coords(mv.origin_coords) not in coords:
                out.append(mv)
        elif mine:
            if norm(mv.dest_planet) in mine and norm(mv.origin_planet) not in mine:
                out.append(mv)
        elif mv.origin_player and mv.dest_player and norm(mv.origin_player) != norm(mv.dest_player):
            out.append(mv)
    return out
