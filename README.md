# BoLeryos

Bot d'automatisation pour Aleryos, destiné aux **tests de protection anti-bot réalisés avec l'éditeur** (GEEKOS NEXUS). Il tourne dans Docker et se pilote depuis une interface web.

> Le règlement du jeu (§12) interdit les bots sur les comptes de joueurs. Ce projet ne doit être utilisé que dans le cadre de l'accord avec l'éditeur, de préférence avec un compte de test dédié.

> Plan de développement, décisions prises et feuille de route : [`docs/PLAN.md`](docs/PLAN.md).

## Pages

| Page | Rôle |
|---|---|
| `/` **Accueil** | Un grand interrupteur **ON / OFF**, une phrase qui dit ce que fait le bot, la liste **« Prêt à démarrer ? »** (✓ / ✗ avec un lien « Corriger »), le choix **Simulation / Réel**, un résumé en phrases de ce que le bot aurait fait, les derniers événements et les planètes suivies. |
| `/ecran` **Écran** | Vue en direct du navigateur du bot, contrôle manuel (jouer soi-même dans la même vue), choix d'univers et exploration de la structure du site. |
| `/reglages` **Réglages** | 5 onglets : **Compte et univers**, **Constructions**, **Recherches**, **Horaires et rythme**, **Sauvegarde et alertes**. Enregistrement automatique (« Enregistré ✓ »). |
| `/suivi` **Suivi** | **Journal** lisible (événements importants ; détails techniques en option), **État du jeu** (lecture seule) et **Planètes suivies**. |

Un bandeau permanent indique le mode : **SIMULATION** (vert, le bot ne clique pas) ou **MODE RÉEL** (rouge). Les anciennes adresses `/vision` et `/actions` redirigent vers `/ecran` et `/reglages`. Des pastilles **?** expliquent les réglages moins évidents.

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

### Connexion automatique (comme TBot)

Dans **Réglages › Compte et univers**, saisissez l'identifiant, le mot de passe et l'univers (champ avec suggestions), puis **« Enregistrer et se connecter »** : le bot se connecte **tout seul**, entre dans l'univers indiqué et **reste connecté** (il se reconnecte si la session expire), sans toucher à l'interrupteur. Le champ « Connexion : … » montre l'avancement en direct. L'interrupteur ON/OFF ne sert qu'à faire **travailler** le bot (constructions, recherches) ; les plages horaires limitent ce travail, pas la connexion. « Oublier le compte » ou « Se déconnecter » (page Écran) arrête la connexion automatique.

### Interrupteur « Bot : ON / OFF »

Un seul interrupteur, dans l'en-tête de chaque page (et en grand sur l'Accueil). Si des éléments obligatoires manquent (identifiants, univers, quelque chose à faire), une fenêtre les liste avec un bouton « Corriger » et un bouton « Démarrer quand même ». **ON** : le bot regarde où en est le site et ne fait que ce qui manque :

| Écran du site | Ce que fait le bot |
|---|---|
| Page de connexion | Se connecte avec les identifiants mémorisés (onglet **Compte et univers** de la page Réglages, bouton « Se connecter maintenant » possible, ou connexion faite une fois à la main sur la page Écran). |
| Choix de l'univers | Entre dans l'univers choisi dans l'onglet Compte et univers (cliquer sur un bouton d'univers le mémorise aussi). |
| Déjà dans une partie | Saute la connexion et le choix de l'univers, puis travaille. |
| Session expirée | Se reconnecte tout seul. |

**Garde-fous :** (valables pour la connexion automatique, ON ou OFF) si le site **refuse** les identifiants, le bot n'essaie qu'**une fois** (risque de blocage du compte) et l'indique ; si la connexion n'aboutit pas (contrôle anti-bot ?), 3 essais espacés (1 min, 5 min) puis il demande de se connecter à la main et **reprend seul** dès que le site est connecté ; le contrôle manuel le met en pause. **OFF** arrête tout (c'est l'arrêt d'urgence).

**Identifiants :** gardés **en mémoire du bot seulement** (jamais sur disque, ni dans le journal, ni dans les réglages exportés, ni renvoyés par l'API). Ils sont oubliés à la déconnexion et perdus au redémarrage du conteneur : il faut alors les ressaisir (Réglages, onglet Compte et univers). Les conserver au-delà d'un redémarrage est la décision D1 du plan, non prise.


Les réglages sont dans la page **Réglages**. Tout est en **simulation par défaut** : le bot lit le jeu et dit ce qu'il *ferait* (« Il y a 5 min : sur Egide, j'aurais lancé Mine de Fer niveau 41 »), sans cliquer. Le **mode réel** se choisit à la main, depuis l'Accueil, avec une confirmation (avertissement si aucune simulation n'a encore été observée), et n'est jamais rétabli après un redémarrage ni activé par un fichier importé.

Vocabulaire : « fréquence de vérification » (durée aléatoire entre deux passages du bot), « maximum de lancements par jour » ; ces deux réglages et le fuseau horaire sont dans « Avancé » (onglet Horaires et rythme).

| Fonction | Ce que fait le bot |
|---|---|
| **Plages horaires** | N'agit que dans les plages choisies (plusieurs plages, jours, fuseau ; plages de nuit possibles). Un cycle en cours s'arrête à la fin de la plage. |
| **Constructions** | **Toutes les planètes** ou **une seule** (choisie dans une liste). Le joueur compose une **liste d'améliorations par ordre de priorité** (bâtiments choisis dans la liste intégrée des 16, avec un niveau demandé) ; chaque ligne vise soit **« jusqu'au niveau N »** soit **« monter de N niveaux »** (par rapport au niveau vu par le bot la première fois, mémorisé par planète) ; des **listes toutes faites** (les 3 mines ; mines et énergie ; mines, énergie et stockage) donnent un point de départ à modifier. Sur chaque planète, le bot lance la **première** réalisable avec les ressources de la planète (niveau inférieur à l'objectif, bouton actif). Une seule construction à la fois par planète ; si l'énergie est négative, les mines sont ignorées. Si rien n'est réalisable, la planète est mise **en attente** et **revérifiée toutes les X minutes** (réglable, 15 par défaut), sans repasser sur les autres planètes ; une planète où une construction vient d'être lancée est revue à sa fin. Le tableau « Planètes suivies » (page Suivi) montre l'état et la prochaine vérification de chacune. |
| **Production** | Pages **Population** (Assaut, Logistique, Administration), **Chantier spatial** (vos modèles de vaisseaux, en mode « Construction », et les composants), **Appareils spécialisés** et **Défenses**. Pour chaque ligne : le nombre voulu ou **« maximum »** (le bot clique sur le bouton MAX du jeu), avec trois comportements : **commande unique** (une fois par planète, puis « faite » ; bouton « Relancer »), **objectif de stock** (le bot ne commande que la différence entre l'objectif et le stock + la file d'attente) et **répéter à chaque passage**. Toutes les planètes ou une seule. Le bot ne lance un nombre que si les ressources de la planète le couvrent **en entier** ; sinon la planète est mise en attente et revérifiée toutes les X minutes. Le débit des ressources est vérifié après le clic ; quand il est trop petit pour se voir (le jeu arrondit les gros stocks), le journal le signale. |
| **Expéditions** *(simulation seulement)* | Réglages › Expéditions : voie **Piliers** (unités au sol) ou **Flotte** (vos modèles de vaisseaux), durée en heures, composition définie dans le bot (pour chaque unité : un nombre, le maximum ou un pourcentage des disponibles). Vous choisissez **combien d'explorations garder actives** ; à chaque passage le bot lit la page Opérations › Exploration (« Explorations actives x/10 », « quotidiennes x/46 », « Disponibles : N ») et, pour chaque place libre, prend la première ligne réalisable **en entier**. Il dit ce qu'il lancerait (« j'aurais lancé une exploration par les Piliers (3 h) avec… ») mais **ne lance rien** : le lancement réel n'est pas codé tant qu'il manque une capture d'une exploration lancée à la main (listes « Durée nominale » et « Copies », bouton de lancement, exploration en cours). |
| **Espionnage** *(simulation seulement)* | Réglages › Espionnage : **joueurs** et/ou **errants** (tag [AI]), alliance exclue au choix, **intervalle de coordonnées** (minimum et maximum, comparés lettre puis nombres, bornes comprises), envoi par **Sonde**, **Drone** ou les deux, depuis une planète choisie ou la planète active. **Une cible par passage** : d'abord celles jamais espionnées, puis la plus ancienne. Le bot lit la liste de la Galaxie et dit qui il espionnerait, mais **n'envoie rien** : l'envoi réel attend une capture d'un envoi fait à la main (bouton « Sonde » de la Galaxie). Limite actuelle : seules les cibles de la liste affichée autour de la planète de départ sont vues. |
| **Alerte d'attaque** | Réglages › Sauvegarde et alertes. Quand le bot est sur **ON**, il lit les « Capteurs interstellaires » du jeu toutes les N secondes (réglable, **sans clic** : le contenu du panneau est dans la page) et signale **une seule fois** chaque **attaque** qui arrive sur une de vos planètes depuis une autre planète : bandeau rouge sur l'Accueil, journal, alerte « attaque en approche » (adresse d'envoi, notification du navigateur). Il **n'agit pas** dans le jeu. Espionnages et mouvements entre vos planètes : ignorés. **Non vérifié sur une vraie attaque** : l'export ne montre qu'un déploiement ; le type d'une attaque est supposé contenir « attaque ». Si les capteurs signalent des mouvements illisibles, le journal le dit. |
| **Priorités** | Réglages › Priorités : ordre dans lequel le bot sert ses blocs (constructions, recherches, population, chantier spatial, appareils, défenses, expéditions). Le premier dépense les ressources avant les suivants, ce qui compte surtout avec « maximum ». |
| **Recherches** | Les **23 recherches** du Laboratoire sont intégrées. Même principe avec « Rechercher », sur la planète désignée (ou celle qui a le plus de Fer). |
| **Alertes** | Message envoyé à une adresse **https** que vous choisissez (Discord, Slack, Mattermost ou ntfy) quand une amélioration est lancée ou que le bot a besoin de vous (connexion à refaire, contrôle anti-bot, arrêt automatique) ; notifications du navigateur en option. L'adresse est enregistrée à part dans le volume (`/data/notifications.json`, droits 0600), n'est **jamais** réaffichée, exportée ni écrite dans le journal ; les adresses du réseau local sont refusées. Les messages ne contiennent ni identifiant ni mot de passe. Telegram n'est pas géré. |
| **Lecture de l'état** | Bouton « Lire l'état du jeu » (page Suivi) : planètes, ressources, niveaux, coûts, durées, boutons actifs. Sert à vérifier que le bot comprend l'écran. |

**Sécurités :** maximum de lancements par jour, arrêt d'urgence, contrôle manuel prioritaire, relecture de l'écran avant et après chaque clic (les ressources doivent être débitées), arrêt si deux lancements ne sont pas confirmés ou si une fenêtre inattendue s'ouvre, refus d'agir sur un nombre que le bot ne comprend pas, délais humains.

**Réglages :** enregistrés dans le volume Docker `boleryos-data` (`/data/automation.json`, non secret) ; exportables et importables en JSON (un fichier importé ne peut jamais activer le mode réel).

**Pas encore fait :** conservation des identifiants après un redémarrage du conteneur (attend le choix D1 du plan) et expéditions (attendent des captures). Voir [`docs/PLAN.md`](docs/PLAN.md).

**Non vérifié sur le vrai jeu (production) :** les pages Population, Chantier spatial, Appareils spécialisés et Défenses ont été codées d'après votre export de structure du 2026-11-03 et testées sur un faux jeu qui en reproduit les balises, pas sur Aleryos. Points à confirmer : le stock affiché sur chaque carte (pastille avant le titre, « En service : N », « ×N »), le bouton sans libellé des composants, la file d'attente des vaisseaux. Utilisez **Suivi › État du jeu › Lire l'état du jeu** : il affiche stock, file d'attente, coût d'une unité et état du bouton lus par le bot ; comparez avec l'écran avant d'activer la production en réel.

**Non vérifié sur le vrai jeu :** la navigation entre les pages Industrie / Installations / Laboratoire et le changement de planète ont été testés sur un faux jeu reconstitué d'après votre export, pas sur Aleryos. Commencez par « Lire l'état du jeu », puis laissez tourner en simulation et relisez le journal avant d'activer le mode réel.

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

Le site protège la connexion avec Cloudflare Turnstile. Le bot **ne résout pas** de captcha. Pour tester les actions du jeu, utiliser un environnement de test fourni par l'éditeur (`LOGIN_URL` ou règle d'exception). Si Turnstile bloque, le journal l'indique et la page Écran montre l'écran bloquant (le navigateur reste ouvert après un échec).

La connexion est considérée comme réussie quand le champ mot de passe disparaît ; l'échec est détecté via un élément `role="alert"` ou un délai dépassé. Ces critères sont à affiner en observant la vraie page après connexion.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium   # ou CHROMIUM_PATH=/chemin/vers/chromium
python -m pytest
```

Les tests utilisent un faux site de connexion local (`tests/mock_site.py`) : aucune tentative n'est envoyée au vrai site.
