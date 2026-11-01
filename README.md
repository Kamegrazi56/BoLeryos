# BoLeryos

Bot d'automatisation pour Aleryos, destiné aux **tests de protection anti-bot réalisés avec l'éditeur** (GEEKOS NEXUS). Il tourne dans Docker et se pilote depuis une interface web.

> Le règlement du jeu (§12) interdit les bots sur les comptes de joueurs. Ce projet ne doit être utilisé que dans le cadre de l'accord avec l'éditeur, de préférence avec un compte de test dédié.

## Pages

| Page | Rôle |
|---|---|
| `/` | Connexion : le joueur saisit son identifiant (e-mail) et son mot de passe dans des champs texte. |
| `/vision` | Vue en direct du navigateur du bot (captures) et journal de ce qu'il fait. |
| `/actions` | Choix des actions à automatiser (vide pour l'instant : à construire après exploration du jeu connecté). |

## Données du joueur

- Aucun identifiant ni mot de passe dans l'image Docker, `docker-compose.yml`, les variables d'environnement ou un volume.
- Ils sont envoyés par l'interface, utilisés une fois pour remplir le formulaire du jeu, puis oubliés. Seule la session du navigateur (cookies) reste en mémoire, jusqu'à la déconnexion ou l'arrêt du conteneur.
- Le journal ne contient jamais le mot de passe, et l'identifiant y est masqué (`jo***`).

## Lancer

**Avec Portainer (Web editor)** : Stacks > Add stack > Web editor, coller le contenu de `docker-compose.yml`, puis Deploy. L'image est construite directement depuis GitHub, sans variable à déclarer.

**Avec Docker en ligne de commande** :

```bash
docker compose up --build
```

Puis ouvrir http://127.0.0.1:8000. Le port n'est publié que sur la machine du conteneur (pour y accéder à distance : tunnel SSH, ou remplacer par `"8000:8000"` en protégeant l'interface, voir ci-dessous).

## Réglages

Ils se modifient directement dans `app/config.py` (aucune donnée de joueur) :

| Champ | Rôle | Défaut |
|---|---|---|
| `login_url` | Page contenant le formulaire de connexion (URL de test de l'éditeur possible) | `https://aleryos.com/fr` |
| `ui_user` / `ui_password` | Protège l'interface du bot par mot de passe (HTTP Basic) | désactivé |
| `login_timeout` | Attente maximale du résultat de la connexion, en secondes | `30` |
| `email_selector`, `password_selector`, `submit_selector` | Sélecteurs du formulaire | relevés sur le site |

Les mêmes réglages peuvent aussi être surchargés par variables d'environnement (`LOGIN_URL`, `UI_USER`, `UI_PASSWORD`, `LOGIN_TIMEOUT`, `EMAIL_SELECTOR`, `PASSWORD_SELECTOR`, `SUBMIT_SELECTOR`), mais ce n'est pas nécessaire.

Ne mettez jamais un mot de passe réel dans `app/config.py` si le dépôt est public.

## Connexion et Cloudflare Turnstile

Le site protège la connexion avec Cloudflare Turnstile. Le bot **ne résout pas** de captcha. Pour tester les actions du jeu, utiliser un environnement de test fourni par l'éditeur (`LOGIN_URL` ou règle d'exception). Si Turnstile bloque, le journal l'indique et la page Vision montre l'écran bloquant (le navigateur reste ouvert après un échec).

La connexion est considérée comme réussie quand le champ mot de passe disparaît ; l'échec est détecté via un élément `role="alert"` ou un délai dépassé. Ces critères sont à affiner en observant la vraie page après connexion.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium   # ou CHROMIUM_PATH=/chemin/vers/chromium
python -m pytest
```

Les tests utilisent un faux site de connexion local (`tests/mock_site.py`) : aucune tentative n'est envoyée au vrai site.
