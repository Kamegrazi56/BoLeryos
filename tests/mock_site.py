"""Faux site de connexion qui imite la structure relevée sur aleryos.com.

Sert aux tests : aucune tentative de connexion n'est envoyée au vrai site.
Mot de passe valide : « good-password ».
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>Mock Aleryos</title></head>
<body>
<main>
  <section id="access">
    <form id="f">
      <input type="email" name="email" placeholder="Adresse e-mail">
      <input type="password" name="password" placeholder="Mot de passe">
      <button type="submit">Se connecter</button>
    </form>
    <p id="err" role="alert" hidden></p>
  </section>
  <section id="game" hidden><h1>Bienvenue commandant</h1></section>
</main>
<div id="cookies"><button type="button" id="refuse">TOUT REFUSER</button></div>
<script>
  document.getElementById('refuse').onclick = () => document.getElementById('cookies').remove();
  document.getElementById('f').onsubmit = (e) => {
    e.preventDefault();
    const pw = e.target.password.value;
    const err = document.getElementById('err');
    if (pw === 'good-password') {
      document.getElementById('access').hidden = true;
      document.getElementById('game').hidden = false;
    } else {
      err.hidden = false;
      err.textContent = 'Identifiants invalides.';
    }
  };
</script>
</body></html>"""


def create_mock_site() -> FastAPI:
    site = FastAPI()

    @site.get("/", response_class=HTMLResponse)
    async def index():
        return PAGE

    return site
