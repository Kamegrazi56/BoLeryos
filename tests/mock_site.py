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


# Page « Choisissez votre univers », reconstituée d'après un export réel (pseudo remplacé).
# Points repris du vrai site : un <article> par univers, le titre (h2) dans une colonne et le bouton
# « Reprendre la partie » dans une autre ; un texte masqué « Compte existant — Reprendre la partie »
# présent dans les DEUX cartes (qui ne doit pas être pris pour un bouton) ; Kael verrouillé.
SERVERS_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Aleryos</title></head>
<body><nav><button>Mon compte</button><button aria-label="Déconnexion"></button></nav>
<main><header><div><h1>Choisissez votre univers</h1><p>Sélectionnez une instance active.</p></div></header>
<div class="space-y-2.5">
<article class="relative min-h-[150px]"><div class="relative flex min-h-[inherit]">
  <div class="min-w-0 lg:flex-1 opacity-60">
    <div class="flex flex-wrap items-center"><h2>Kael</h2><div class="ml-auto"><span>Ouvert</span></div></div>
    <div class="mt-2 flex"><div class="grid"><div><div>Inscrits</div><div>342</div></div></div><button type="button">Caractéristiques</button></div>
    <div class="mt-2 flex items-center"><span style="position:absolute;left:-9999px">Compte existant — Reprendre la partie</span></div>
  </div>
  <div class="flex shrink-0 flex-col"><p>Votre pseudo : Joueur</p></div>
  <div class="flex w-full items-center"><div class="min-w-0 flex-1"><p>Ouverture officielle le 07/11/2026 09:00.</p><p>Votre empire est prêt, mais l'accès au jeu reste verrouillé.</p></div>
    <button id="calendar-button-1" type="button"><span>Ajouter à mon agenda</span></button></div>
</div></article>
<article class="relative min-h-[150px]"><div class="relative flex min-h-[inherit]">
  <div class="min-w-0 lg:flex-1">
    <div class="flex flex-wrap items-center"><h2>Asterion</h2><div class="ml-auto"><span>Ouvert</span></div></div>
    <div class="mt-2 flex"><div class="grid"><div><div>Inscrits</div><div>1 111</div></div></div><button type="button">Caractéristiques</button></div>
    <div class="mt-2 flex items-center"><span style="position:absolute;left:-9999px">Compte existant — Reprendre la partie</span></div>
    <details class="group mt-2"><summary><span>Rattrapage galactique</span><span>Disponible</span></summary><p>Les nouveaux joueurs progressent plus vite.</p></details>
  </div>
  <div class="flex shrink-0 flex-col"><p>Votre pseudo : Joueur</p>
    <button type="button" onclick="location.href='/servers/asterion'">Reprendre la partie</button></div>
</div></article>
</div></main><footer><p>© 2026 Aleryos</p></footer></body></html>"""


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
