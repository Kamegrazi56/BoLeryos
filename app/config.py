"""Configuration du bot.

Seuls des réglages techniques passent par l'environnement. Les identifiants du
joueur ne sont JAMAIS lus ici : ils sont saisis dans l'interface web et gardés
en mémoire uniquement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    # Page qui contient le formulaire de connexion (URL de test de l'éditeur
    # possible : il suffit de changer LOGIN_URL).
    login_url: str = "https://aleryos.com/fr"
    # Sélecteurs du formulaire de connexion (relevés lors de l'audit).
    email_selector: str = 'input[name="email"]'
    password_selector: str = 'input[name="password"]'
    submit_selector: str = 'form:has(input[name="password"]) button[type="submit"]'
    # Délai maximal d'attente du résultat de la connexion (secondes).
    login_timeout: float = 30.0
    # Navigateur.
    headless: bool = True
    no_sandbox: bool = False
    chromium_path: str | None = None
    # Intervalle entre deux captures du navigateur pour l'affichage en direct (secondes).
    screenshot_interval: float = 0.4
    # Comportement humain : délais courts et aléatoires entre les actions.
    human_mode: bool = True
    human_delay_min: float = 0.4   # pause entre deux actions (secondes)
    human_delay_max: float = 1.4
    key_delay_min: float = 0.05    # délai entre deux touches (secondes)
    key_delay_max: float = 0.18
    # Affiche un point rouge à l'endroit de la souris dans la vue en direct.
    show_cursor: bool = True
    # Dossier des réglages d'automatisation (JSON, jamais d'identifiants). Utilisé seulement s'il existe.
    data_dir: str | None = "/data"
    # Fréquence de vérification de l'écran quand le bot attend (connexion, univers, plage horaire), en secondes.
    poll_seconds: float = 10.0
    # Intervalle minimal autorisé entre deux cycles d'automatisation (minutes).
    min_cycle_minutes: float = 1.0
    # Protection facultative de l'interface du bot (HTTP Basic). Ce n'est PAS
    # un identifiant du jeu.
    ui_user: str | None = None
    ui_password: str | None = None


def load_settings() -> Settings:
    defaults = Settings()
    return Settings(
        login_url=os.environ.get("LOGIN_URL", defaults.login_url),
        email_selector=os.environ.get("EMAIL_SELECTOR", defaults.email_selector),
        password_selector=os.environ.get("PASSWORD_SELECTOR", defaults.password_selector),
        submit_selector=os.environ.get("SUBMIT_SELECTOR", defaults.submit_selector),
        login_timeout=_env_float("LOGIN_TIMEOUT", defaults.login_timeout),
        headless=_env_bool("HEADLESS", defaults.headless),
        no_sandbox=_env_bool("CHROMIUM_NO_SANDBOX", defaults.no_sandbox),
        chromium_path=os.environ.get("CHROMIUM_PATH") or None,
        screenshot_interval=_env_float("SCREENSHOT_INTERVAL", defaults.screenshot_interval),
        human_mode=_env_bool("HUMAN_MODE", defaults.human_mode),
        human_delay_min=_env_float("HUMAN_DELAY_MIN", defaults.human_delay_min),
        human_delay_max=_env_float("HUMAN_DELAY_MAX", defaults.human_delay_max),
        key_delay_min=_env_float("KEY_DELAY_MIN", defaults.key_delay_min),
        key_delay_max=_env_float("KEY_DELAY_MAX", defaults.key_delay_max),
        show_cursor=_env_bool("SHOW_CURSOR", defaults.show_cursor),
        data_dir=os.environ.get("DATA_DIR", defaults.data_dir) or None,
        poll_seconds=_env_float("POLL_SECONDS", defaults.poll_seconds),
        min_cycle_minutes=_env_float("MIN_CYCLE_MINUTES", defaults.min_cycle_minutes),
        ui_user=os.environ.get("UI_USER") or None,
        ui_password=os.environ.get("UI_PASSWORD") or None,
    )
