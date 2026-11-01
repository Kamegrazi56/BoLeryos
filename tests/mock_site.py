"""Faux site de connexion qui imite la structure relevée sur aleryos.com.

Sert aux tests : aucune tentative de connexion n'est envoyée au vrai site.
Mot de passe valide : « good-password ».
"""

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse

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
  // Appels de type « jeu » : API JSON (avec secrets à masquer) et WebSocket.
  fetch('/api/me?token=SECRETTOKEN12345&page=1', {headers: {Authorization: 'Bearer SECRETBEARER999'}});
  const sock = new WebSocket('ws://' + location.host + '/ws');
  sock.onopen = () => sock.send(JSON.stringify({type: 'hello', token: 'secret.jwt.value', n: 1}));
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


SERVERS_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Univers</title></head>
<body><h1>Choisissez votre univers</h1>
<div class="card"><h2>Kael</h2><span>Ouverture programmée</span><button>Ajouter à mon agenda</button></div>
<div class="card"><h2>Asterion</h2><span>Ouvert</span>
  <button onclick="location.href='/servers/asterion'">Reprendre la partie</button></div>
</body></html>"""  # structure supposée d'après une capture d'écran : à confirmer avec l'export réel


def create_mock_site() -> FastAPI:
    site = FastAPI()

    @site.get("/api/me")
    async def me():
        response = JSONResponse(
            {
                "email": "joueur.test@example.com",
                "token": "eyJhbGciOi.eyJzdWIi.c2lnbmF0dXJl",
                "state": "open",
                "count": 42,
                "planets": [{"id": "3f2b8c1e-1111-2222-3333-444455556666", "name": "Terra", "level": 3}],
            }
        )
        response.set_cookie("session", "COOKIEVALUE123456", httponly=True)
        return response

    @site.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        try:
            await socket.receive_text()
            await socket.send_json({"type": "tick", "token": "abc.def.ghi", "count": 1})
            await socket.receive_text()
        except Exception:  # noqa: BLE001
            pass

    @site.get("/servers", response_class=HTMLResponse)
    async def servers():
        return SERVERS_PAGE

    @site.get("/servers/asterion", response_class=HTMLResponse)
    async def asterion():
        return "<!doctype html><html><body><h1>Asterion</h1></body></html>"

    @site.get("/", response_class=HTMLResponse)
    async def index():
        return PAGE

    return site
