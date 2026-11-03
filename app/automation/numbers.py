"""Lecture des nombres et des durées affichés par le jeu.

Exemples vus dans le jeu : « 201.765M », « 58,035 M », « 4,121 G », « 3,4 Md », « 188.2k », « 11 014 »,
durées « 1j 2h », « 12h 22m », « 5m 25s », « 5.00s ».
Un nombre ou une durée non compris lève ValueError : le bot refuse alors d'agir plutôt que de deviner.
"""

from __future__ import annotations

import re

MULTIPLIERS = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "Md": 1e9}
_AMOUNT_RE = re.compile(r"^([+-]?)\s*([\d\s.,]*\d)\s*(Md|k|K|M|G)?$")
_DURATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(j|h|m|s)")
_UNIT_SECONDS = {"j": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}


def parse_amount(text: str) -> float:
    cleaned = str(text).replace(" ", " ").replace(" ", " ").strip()
    match = _AMOUNT_RE.match(cleaned)
    if not match:
        raise ValueError(f"nombre illisible : {text!r}")
    sign, number, suffix = match.groups()
    number = number.replace(" ", "")
    has_comma, has_dot = "," in number, "." in number
    if has_comma and has_dot:  # le dernier séparateur est la décimale, l'autre sépare les milliers
        decimal = "," if number.rfind(",") > number.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        number = number.replace(thousands, "").replace(decimal, ".")
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = number.split(sep)
        if len(parts) > 2:  # « 1.234.567 » : séparateurs de milliers
            number = "".join(parts)
        elif suffix is None and len(parts[1]) == 3 and len(parts[0]) <= 3:  # « 4,808 » sans unité : milliers
            number = "".join(parts)
        else:  # « 201.765M », « 58,035 M », « 5,5 » : décimale
            number = parts[0] + "." + parts[1]
    value = float(number) * MULTIPLIERS[suffix or ""]
    return -value if sign == "-" else value


def parse_duration(text: str) -> float:
    """Durée en secondes."""
    cleaned = str(text).strip()
    total, position = 0.0, 0
    for match in _DURATION_RE.finditer(cleaned):
        if cleaned[position:match.start()].strip():
            raise ValueError(f"durée illisible : {text!r}")
        total += float(match.group(1).replace(",", ".")) * _UNIT_SECONDS[match.group(2)]
        position = match.end()
    if position == 0 or cleaned[position:].strip():
        raise ValueError(f"durée illisible : {text!r}")
    return total


def norm(name: str) -> str:
    """Forme de comparaison d'un nom du jeu (apostrophes typographiques, casse, espaces)."""
    return " ".join(str(name).replace("’", "'").replace("‘", "'").casefold().split())


def display_step(text: str) -> float | None:
    """Plus petite variation visible d'un montant affiché : « 3,201 G » → 1 000 000 ; « 58,035 M » → 1 000 ; « 11 014 » → 1.

    Sert à savoir si un débit est assez grand pour se voir à l'écran (le jeu arrondit les gros stocks).
    """
    cleaned = str(text).replace(" ", " ").replace(" ", " ").strip()
    match = _AMOUNT_RE.match(cleaned)
    if not match:
        return None
    _, number, suffix = match.groups()
    number = number.replace(" ", "")
    decimals = 0
    has_comma, has_dot = "," in number, "." in number
    if has_comma and has_dot:
        decimals = len(number[max(number.rfind(","), number.rfind(".")) + 1:])
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = number.split(sep)
        if len(parts) == 2 and not (suffix is None and len(parts[1]) == 3 and len(parts[0]) <= 3):
            decimals = len(parts[1])  # décimale (même règle que parse_amount)
    return MULTIPLIERS[suffix or ""] * 10 ** -decimals
