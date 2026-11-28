"""Panneau Discord : un message persistant avec menus et boutons, pour commander le bot sans saisir de « / ».

Aucun accès réseau ici : `discord_gateway.py` publie le message et lui transmet les clics. Le panneau ne garde rien en
mémoire : l'instance et la fonction choisies sont les options « sélectionnées » du message lui-même, que Discord renvoie
à chaque clic. Un redémarrage du bot ne change donc rien : les boutons restent valables (identifiants fixes « pn:… »).
Chaque action passe par `CommandRunner` : mêmes règles, mêmes confirmations, même journal que les commandes « / ».
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .discord_commands import CommandRunner, Reply
from .features import FEATURES
from .pause import MAX_MINUTES, MIN_MINUTES

PREFIX = "pn:"
ALL = "*"  # valeur du menu « Toutes les instances » (une option ne peut pas être vide)
MAX_OPTIONS = 25  # limite d'un menu Discord
SECONDARY, PRIMARY, SUCCESS, DANGER = 2, 1, 3, 4  # styles de bouton
PAUSE_CHOICES = (15, 30, 60, 120, 240, 480, 720)

CONTENT = ("**Panneau de commande Boleryos**\n"
           "1. Choisissez une instance (ou « Toutes les instances »).\n"
           "2. Cliquez sur une action : la réponse n'est visible que de vous et s'efface d'elle-même.\n"
           "Pour activer, désactiver ou lancer une fonctionnalité, choisissez-la d'abord dans le second menu.\n"
           "Réservé au propriétaire du bot ; les commandes « / » marchent toujours.")

# Boutons du panneau : (code, libellé, style). Le code est la fin de l'identifiant « pn:do:<code> ».
READS = (("st", "État", SECONDARY), ("nx", "Prochain passage", SECONDARY), ("fl", "Flottes", SECONDARY),
         ("pl", "Planètes", SECONDARY), ("jr", "Journal", SECONDARY))
ACTIONS = (("on", "Bot ON", SUCCESS), ("off", "Bot OFF", DANGER), ("pz", "Pause…", SECONDARY),
           ("cx", "Reconnecter", SECONDARY), ("sc", "Relire le jeu", SECONDARY))
FEATURE_BUTTONS = (("fa", "Activer", SUCCESS), ("fd", "Désactiver", DANGER), ("fx", "Lancer", PRIMARY),
                   ("fs", "Fonctions", SECONDARY))
# Code du bouton → commande « / » qu'il déclenche (« st » dépend du choix : voir `_command`).
COMMANDS = {"nx": ("prochain", ""), "fl": ("flottes", ""), "pl": ("planetes", ""), "jr": ("journal", ""),
            "on": ("bot", "demarrer"), "off": ("bot", "arreter"), "cx": ("connexion", ""), "sc": ("scan", ""),
            "fs": ("fonction", "statut"), "fa": ("fonction", "activer"), "fd": ("fonction", "desactiver"),
            "fx": ("fonction", "lancer")}
NEEDS_FEATURE = {"fa", "fd", "fx"}
STALE = "Cette instance n'existe plus : cliquez sur « Actualiser le panneau »."


@dataclass
class Outcome:
    """Ce que le client Discord doit faire d'un clic : mettre à jour le panneau, ou répondre à son auteur seul."""
    update: dict | None = None  # nouveau contenu du panneau
    reply: Reply | None = None
    rows: list[dict] = field(default_factory=list)  # boutons ou menu à placer sous la réponse


def _button(label: str, style: int, custom_id: str) -> dict:
    return {"type": 2, "style": style, "label": label, "custom_id": custom_id}


def _row(*items: dict) -> dict:
    return {"type": 1, "components": list(items)}


def is_panel(custom_id: str) -> bool:
    return custom_id.startswith(PREFIX)


def is_update(custom_id: str) -> bool:
    """Clic qui ne fait que redessiner le panneau (réponse immédiate, sans commande à exécuter)."""
    return custom_id in (PREFIX + "inst", PREFIX + "fn", PREFIX + "sync")


def chosen(message: dict | None) -> dict[str, str]:
    """Les options sélectionnées du panneau : {identifiant du menu: valeur}."""
    found: dict[str, str] = {}
    for row in (message or {}).get("components") or []:
        for comp in row.get("components") or []:
            for option in comp.get("options") or []:
                if option.get("default"):
                    found[str(comp.get("custom_id", ""))] = str(option.get("value", ""))
    return found


class Panel:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    # ------------------------------------------------------------------ dessin
    def message(self, instance: str = ALL, feature: str = "") -> dict:
        """Le contenu du panneau, avec l'instance et la fonction données comme choisies."""
        infos = self._runner.instances()[: MAX_OPTIONS - 1]
        if instance != ALL and not any(i["id"] == instance for i in infos):
            instance = ALL  # instance supprimée depuis : retour à « Toutes »
        options = [{"label": "Toutes les instances", "value": ALL, "default": instance == ALL}]
        for info in infos:
            where = info["universe"] or "serveur non choisi"
            options.append({"label": info["name"][:100], "value": info["id"], "default": instance == info["id"],
                            "description": f"{where} · {'démarrée' if info['running'] else 'arrêtée'}"[:100]})
        features = [{"label": f.label[:100], "value": f.id, "default": feature == f.id} for f in FEATURES[:MAX_OPTIONS]]
        return {"content": CONTENT, "components": [
            _row({"type": 3, "custom_id": PREFIX + "inst", "placeholder": "Instance", "options": options}),
            _row(*(_button(label, style, f"{PREFIX}do:{code}") for code, label, style in READS)),
            _row(*(_button(label, style, f"{PREFIX}do:{code}") for code, label, style in ACTIONS)),
            _row({"type": 3, "custom_id": PREFIX + "fn", "placeholder": "Fonctionnalité (pour activer, désactiver, lancer)",
                  "options": features}),
            _row(*(_button(label, style, f"{PREFIX}do:{code}") for code, label, style in FEATURE_BUTTONS),
                 _button("Actualiser le panneau", SECONDARY, PREFIX + "sync")),
        ]}

    # ------------------------------------------------------------------ clics
    async def handle(self, data: dict) -> Outcome:
        """Traite un clic ou un choix de menu (`data` : l'interaction reçue de Discord)."""
        custom_id = str(data["data"].get("custom_id", ""))
        values = [str(v) for v in data["data"].get("values") or []]
        kind, _, rest = custom_id[len(PREFIX):].partition(":")
        picked = chosen(data.get("message"))
        instance, feature = picked.get(PREFIX + "inst", ALL), picked.get(PREFIX + "fn", "")
        if kind in ("inst", "fn") and values:
            instance, feature = (values[0], feature) if kind == "inst" else (instance, values[0])
        if kind in ("inst", "fn", "sync"):
            return Outcome(update=self.message(instance, feature))
        if kind == "do":
            return await self._do(rest, instance, feature)
        if kind == "pz" and values:  # durée choisie dans le menu de la pause
            return await self._run(("pause", ""), {"minutes": values[0], **_target(rest)})
        if kind == "go":  # « Actualiser dans le jeu » sous les flottes ou les planètes
            command, _, target = rest.partition(":")
            return await self._run(COMMANDS.get(command, ("", "")), {"actualiser": "true", **_target(target)}, command)
        if kind == "ss":  # démarrer ou arrêter la session d'une instance
            action, _, target = rest.partition(":")
            return await self._run(("instance", "demarrer" if action == "go" else "arreter"), _target(target))
        return Outcome(reply=Reply("Bouton inconnu : cliquez sur « Actualiser le panneau »."))

    async def _do(self, code: str, instance: str, feature: str) -> Outcome:
        if instance != ALL and not any(i["id"] == instance for i in self._runner.instances()):
            return Outcome(reply=Reply(STALE))
        if code in NEEDS_FEATURE and not feature:
            return Outcome(reply=Reply("Choisissez d'abord une fonctionnalité dans le second menu."))
        if code == "pz":
            return Outcome(reply=Reply("Pause de combien de temps ? Le bot passe sur OFF, puis revient sur ON tout seul."),
                           rows=[self._pause_menu(instance)])
        options = _target(instance) | ({"fonction": feature} if code in NEEDS_FEATURE else {})
        if code == "st":
            return await self._status(instance, options)
        return await self._run(COMMANDS[code], options, code)

    async def _status(self, instance: str, options: dict) -> Outcome:
        if instance == ALL:
            return await self._run(("bot", "statut"), {})
        outcome = await self._run(("instance", "statut"), options)
        outcome.rows = [_row(_button("Démarrer la session", SUCCESS, f"{PREFIX}ss:go:{instance}"),
                             _button("Arrêter la session", DANGER, f"{PREFIX}ss:no:{instance}"))]
        return outcome

    async def _run(self, path: tuple[str, str], options: dict, code: str = "") -> Outcome:
        reply = await self._runner.execute(path, options)
        rows: list[dict] = []
        if code in ("fl", "pl") and reply.confirm is None:
            target = options.get("instance") or ALL
            rows = [_row(_button("Actualiser dans le jeu", PRIMARY, f"{PREFIX}go:{code}:{target}"))]
        return Outcome(reply=reply, rows=rows)

    @staticmethod
    def _pause_menu(instance: str) -> dict:
        options = [{"label": f"{m} min" if m < 60 else f"{m // 60} h", "value": str(m)}
                   for m in PAUSE_CHOICES if MIN_MINUTES <= m <= MAX_MINUTES]
        return _row({"type": 3, "custom_id": f"{PREFIX}pz:{instance}", "placeholder": "Durée de la pause", "options": options})


def _target(instance: str) -> dict:
    """Option « instance » d'une commande : rien pour « Toutes » (toutes les instances démarrées)."""
    return {} if instance in (ALL, "") else {"instance": instance}
