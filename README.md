# BoLeryos

Bot d'automatisation pour Aleryos, destiné aux **tests de protection anti-bot réalisés avec l'éditeur** (GEEKOS NEXUS). Il tourne dans Docker et se pilote depuis une interface web.

> Le règlement du jeu (§12) interdit les bots sur les comptes de joueurs. Ce projet ne doit être utilisé que dans le cadre de l'accord avec l'éditeur, de préférence avec un compte de test dédié.

> Plan de développement, décisions prises et feuille de route : [`docs/PLAN.md`](docs/PLAN.md).

## Pages

| Page | Rôle |
|---|---|
| `/` | Connexion : le joueur saisit son identifiant (e-mail) et son mot de passe dans des champs texte, avec la vue en direct du navigateur du bot à côté. |
| `/vision` | Vue en direct du navigateur du bot et journal de tout ce qu'il fait. Le navigateur affiche le site même sans connexion. |
| `/actions` | Choix des actions à automatiser (vide pour l'instant : à construire après exploration du jeu connecté). |

### Contrôle manuel

Une case « Contrôle manuel » (au-dessus de l'écran) rend la vue cliquable comme une page web : clics, molette, clavier et collage de texte sont rejoués sur le navigateur du bot.

- Désactivé par défaut. Tant qu'il est activé, le bot n'agit pas (la connexion automatique est refusée) ; il se désactive tout seul quand le dernier écran est fermé.
- Les frappes ne sont jamais écrites dans le journal.
- Non géré : menus déroulants natifs du navigateur, clic droit, fenêtres de fichiers.
- La zone d'affichage occupe toute la page, et la hauteur du navigateur du bot s'adapte au format de l'écran (largeur fixe de 1440 px).
- Toute personne qui accède à l'interface peut agir sur le compte du jeu : protégez-la (`ui_user` / `ui_password`).

### Affichage en direct

Chrome pousse lui-même une image à chaque changement de la page (CDP `Page.startScreencast`) ; le serveur ne garde que la dernière et l'envoie en JPEG brut sur le WebSocket. Mesure en local (serveur seul, sans réseau ni affichage du navigateur) : médiane de 5 ms, maximum d'environ 50 ms entre un changement de la page et l'envoi de l'image. Une capture classique sert de filet de sécurité si Chrome n'envoie rien pendant 1 s.

### Choix de l'univers

Après la connexion, la page « Choisissez votre univers » du jeu est lue par le bot : un bouton par univers apparaît au-dessus de l'écran, et un clic le fait entrer (avec les délais humains). La détection repose pour l'instant sur le seul libellé observé (« Reprendre la partie ») et sur une structure supposée d'après une capture d'écran : à confirmer avec l'export ci-dessous. En attendant, le contrôle manuel permet de cliquer soi-même.

### Exploration de la structure du site

Le panneau « Exploration de la structure du site » (désactivé par défaut) enregistre, pendant que vous naviguez, la structure des pages (balises, attributs, tous les textes visibles : niveaux, coûts, durées…), les chemins d'API et la **forme** des réponses JSON et des messages WebSocket. Il exporte un fichier `boleryos-structure.json`.

- Conservés : chemins (identifiants remplacés par `{id}`), noms de paramètres, **noms** d'en-têtes, de cookies et de clés de stockage, types des valeurs, courts libellés alphabétiques (ex. `open`).
- Jamais conservés : valeurs de cookies, d'en-têtes et de stockage, valeurs de paramètres d'URL, mots de passe et saisies, e-mails, jetons, identifiants. Des tests le vérifient.
- Les textes visibles à l'écran (titres, boutons, pseudo) et les courts libellés peuvent contenir des informations de jeu : relisez le fichier avant de le partager. Ne le commitez pas dans un dépôt public.

## Automatisation

La page **Actions** pilote le planificateur. Tout est **à blanc par défaut** : le bot lit le jeu et écrit dans le journal ce qu'il *ferait*, sans cliquer. Le mode réel s'active à la main (confirmation) et n'est jamais rétabli après un redémarrage.

| Fonction | Ce que fait le bot |
|---|---|
| **Plages horaires** | N'agit que dans les plages choisies (plusieurs plages, jours, fuseau ; plages de nuit possibles). Un cycle en cours s'arrête à la fin de la plage. |
| **Constructions** | Pour chaque planète (toutes ou une liste), améliore les bâtiments dont le niveau est inférieur au niveau demandé, si la planète a les ressources et si le bouton est actif. Une seule construction à la fois par planète. Priorité au bâtiment qui pèse le moins sur le stock. Si l'énergie est négative, les mines sont ignorées. |
| **Recherches** | Même principe avec « Rechercher », sur la planète désignée (ou celle qui a le plus de Fer). |
| **Lecture de l'état** | Bouton « Lire l'état du jeu » : planètes, ressources, niveaux, coûts, durées, boutons actifs. Sert à vérifier que le bot comprend l'écran. |

**Sécurités :** plafond de lancements par jour, arrêt d'urgence, contrôle manuel prioritaire, relecture de l'écran avant et après chaque clic (les ressources doivent être débitées), arrêt si deux lancements ne sont pas confirmés ou si une fenêtre inattendue s'ouvre, refus d'agir sur un nombre que le bot ne comprend pas, délais humains.

**Réglages :** enregistrés dans le volume Docker `boleryos-data` (`/data/automation.json`, non secret) ; exportables et importables en JSON (un fichier importé ne peut jamais activer le mode réel).

**Pas encore fait :** connexion automatique avec identifiants enregistrés (attend le choix D1 du plan) et expéditions (attendent des captures). Voir [`docs/PLAN.md`](docs/PLAN.md).

**Non vérifié sur le vrai jeu :** la navigation entre les pages Industrie / Installations / Laboratoire et le changement de planète ont été testés sur un faux jeu reconstitué d'après votre export, pas sur Aleryos. Commencez par « Lire l'état du jeu », puis laissez tourner à blanc et relisez le journal avant d'activer le mode réel.

## Données du joueur

- Aucun identifiant ni mot de passe dans l'image Docker, `docker-compose.yml`, les variables d'environnement ou un volume.
- Ils sont envoyés par l'interface, utilisés une fois pour remplir le formulaire du jeu, puis oubliés. Seule la session du navigateur (cookies) reste en mémoire, jusqu'à la déconnexion ou l'arrêt du conteneur.
- Le journal ne contient jamais le mot de passe, et l'identifiant y est masqué (`jo***`).

## Lancer

**Avec Portainer (Web editor)** : Stacks > Add stack > Web editor, coller le contenu de `docker-compose.yml`, puis Deploy. Aucune variable à déclarer.

L'image `ghcr.io/kamegrazi56/boleryos:latest` est construite et publiée automatiquement par GitHub Actions (`.github/workflows/docker-publish.yml`) à chaque mise à jour du code. Pour mettre à jour le bot : attendre la fin du workflow dans l'onglet Actions du dépôt, puis redéployer la stack dans Portainer (`pull_policy: always` télécharge la dernière image).

Première publication : le paquet est créé en privé. Sur GitHub, aller dans le profil > Packages > `boleryos` > Package settings > Change visibility > Public, pour que Portainer puisse le télécharger sans identifiants.

**Avec Docker en ligne de commande** :

```bash
docker compose up --build
```

Puis ouvrir `http://<adresse-du-serveur>:7654`. Le port se change dans `docker-compose.yml` (`"7654:8000"` : port du serveur à gauche, port du conteneur à droite).

**Attention** : l'interface reçoit le mot de passe du jeu et n'est accessible qu'en HTTP. Ne l'exposez pas sur internet sans protection (réglages `ui_user` / `ui_password` ci-dessous, et idéalement un reverse proxy HTTPS).

## Réglages

Ils se modifient directement dans `app/config.py` (aucune donnée de joueur) :

| Champ | Rôle | Défaut |
|---|---|---|
| `login_url` | Page contenant le formulaire de connexion (URL de test de l'éditeur possible) | `https://aleryos.com/fr` |
| `ui_user` / `ui_password` | Protège l'interface du bot par mot de passe (HTTP Basic) | désactivé |
| `human_mode` | Comportement humain : pauses courtes et aléatoires entre les actions, souris déplacée progressivement, frappe touche par touche | `True` |
| `human_delay_min` / `human_delay_max` | Pause entre deux actions (secondes) | `0.4` / `1.4` |
| `key_delay_min` / `key_delay_max` | Délai entre deux touches (secondes) | `0.05` / `0.18` |
| `show_cursor` | Point rouge à l'endroit de la souris dans la vue en direct | `True` |
| `screenshot_interval` | Intervalle entre deux images de la vue en direct (secondes) | `0.4` |
| `login_timeout` | Attente maximale du résultat de la connexion, en secondes | `30` |
| `email_selector`, `password_selector`, `submit_selector` | Sélecteurs du formulaire | relevés sur le site |

Les mêmes réglages peuvent aussi être surchargés par variables d'environnement (`DATA_DIR`, `MIN_CYCLE_MINUTES`, `LOGIN_URL`, `UI_USER`, `UI_PASSWORD`, `LOGIN_TIMEOUT`, `EMAIL_SELECTOR`, `PASSWORD_SELECTOR`, `SUBMIT_SELECTOR`), mais ce n'est pas nécessaire.

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
