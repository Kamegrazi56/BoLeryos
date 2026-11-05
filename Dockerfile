# L'image Playwright embarque Chromium et ses dépendances système.
# Le tag doit correspondre à la version de `playwright` dans requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

WORKDIR /srv

COPY requirements.txt .
# Pas de venv : l'image n'embarque pas python3-venv. Le conteneur ne sert qu'à ce bot.
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# Navigateur complet : écran virtuel VNC (Xvnc), client web noVNC et xrandr (taille de l'écran ajustée à la page).
# Installé après pip : noVNC tire des paquets Python de la distribution, que pip ne sait pas remplacer.
# Le serveur VNC n'écoute que dans le conteneur ; l'interface du bot le relaie sur son propre port.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tigervnc-standalone-server novnc x11-xserver-utils \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

COPY app ./app

# Dossier des réglages d'automatisation (JSON non secret : objectifs, plages horaires, compteurs).
# Jamais d'identifiants ici. Monté comme volume dans docker-compose.yml.
RUN mkdir -p /data && chown pwuser:pwuser /data

# Aucune donnée du joueur dans l'image ni dans l'environnement :
# l'identifiant et le mot de passe sont saisis dans l'interface web.
ENV CHROMIUM_NO_SANDBOX=true \
    VNC=true \
    PYTHONUNBUFFERED=1

USER pwuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
