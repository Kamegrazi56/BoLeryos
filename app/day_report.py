"""Bilan de la journée d'une instance : ce que le bot a fait, passage par passage, depuis minuit (heure française).

Tenu en mémoire seulement : un redémarrage du conteneur repart d'une page blanche. Il sert au message de fin de journée
envoyé par Discord quand le bot, qui suit le planning, s'arrête après la dernière plage.
"""

from __future__ import annotations

from typing import Callable

from . import planning

RESULT_CHARS = 110  # un résultat de passage peut être long : on n'en garde que le début


class DayReport:
    def __init__(self, today: Callable[[], str] = planning.local_date) -> None:
        self._today = today
        self._day = today()
        self._rows: dict[str, dict] = {}

    def _roll(self) -> None:
        """Nouveau jour : on repart de zéro (le bilan d'hier n'a plus lieu d'être)."""
        day = self._today()
        if day != self._day:
            self._day, self._rows = day, {}

    def record(self, label: str, ok: bool, result: str) -> None:
        self._roll()
        row = self._rows.setdefault(label, {"ok": 0, "failed": 0, "last": ""})
        row["ok" if ok else "failed"] += 1
        row["last"] = result.strip()

    def has_activity(self) -> bool:
        self._roll()
        return bool(self._rows)

    def reset(self) -> None:
        self._rows = {}

    def text(self) -> str:
        """Le bilan en quelques lignes, une par fonctionnalité (dans l'ordre où elles ont travaillé)."""
        self._roll()
        year, month, day = self._day.split("-")
        lines = [f"Bilan du {day}/{month}/{year} : fin de la dernière plage du planning."]
        for label, row in self._rows.items():
            count = f"{row['ok']} passage(s)" + (f", {row['failed']} en échec" if row["failed"] else "")
            last = row["last"][:RESULT_CHARS] + ("…" if len(row["last"]) > RESULT_CHARS else "")
            lines.append(f"• {label} : {count}" + (f" — dernier : {last}" if last else ""))
        return "\n".join(lines)
