"""Lecture des nombres affichés par le jeu.

Exemples vus dans le jeu : « 201.765M », « 58,035 M », « 4,121 G », « 3,4 Md », « 188.2k », « 11 014 ».
Un nombre non compris lève ValueError : le bot refuse alors d'agir plutôt que de deviner.
"""

from __future__ import annotations

import re

MULTIPLIERS = {"": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "Md": 1e9}
_AMOUNT_RE = re.compile(r"^([+-]?)\s*([\d\s.,]*\d)\s*(Md|k|K|M|G)?$")


def parse_amount(text: str) -> float:
    # Le jeu sépare parfois les milliers par une espace insécable (U+00A0) ou fine insécable (U+202F).
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


def norm(name: str) -> str:
    """Forme de comparaison d'un nom du jeu (apostrophes typographiques, casse, espaces)."""
    return " ".join(str(name).replace("’", "'").replace("‘", "'").casefold().split())
