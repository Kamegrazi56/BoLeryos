# L'image Playwright embarque Chromium et ses dépendances système.
# Le tag doit correspondre à la version de `playwright` dans requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

WORKDIR /srv

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Aucune donnée du joueur dans l'image ni dans l'environnement :
# l'identifiant et le mot de passe sont saisis dans l'interface web.
ENV CHROMIUM_NO_SANDBOX=true \
    PYTHONUNBUFFERED=1

USER pwuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
