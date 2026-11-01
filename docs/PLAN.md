# Plan de développement de BoLeryos

*Dernière mise à jour : 2026-11-01. Ce document fait foi : on le met à jour à chaque phase, et on ne repose pas une question dont la réponse y figure.*

## 1. Objectif

Un bot qui tourne dans Docker (déployé avec Portainer) et se pilote depuis une interface web :
- **page Connexion** : le joueur saisit son identifiant et son mot de passe, voit le site en direct ;
- **page Vision** : l'écran du navigateur du bot en direct, le journal de tout ce qu'il fait, et la possibilité de jouer à la main ;
- **page Actions** : le joueur choisit les actions à automatiser (première cible : **l'amélioration des mines**).

Cadre : test de protections anti-bot **avec l'éditeur** (GEEKOS NEXUS), sur un compte réel. Le règlement du jeu (§12) interdit les bots : l'accord de l'éditeur doit être **conservé par écrit**, et la période du test et l'adresse IP du serveur lui sont communiquées.

## 2. Décisions prises (ne plus en discuter)

| Sujet | Décision |
|---|---|
| Technologies | Python 3.13, FastAPI, Playwright (Chromium). Interface en HTML/JS simple, sans framework. |
| Déploiement | Image `ghcr.io/kamegrazi56/boleryos:latest` construite par GitHub Actions ; stack Portainer en *Web editor* (`image:`), port `7654:8000`, aucune variable à déclarer. Réglages techniques dans `app/config.py`. |
| Données du joueur | Saisies dans l'interface, **en mémoire seulement**. Jamais dans l'image, le compose, les variables d'environnement, un volume, le journal ou git. |
| Moteur | **Navigateur piloté** (clics sur l'interface du jeu). Le moteur API/WebSocket direct vient plus tard, derrière la même interface interne. |
| Interface en direct | Flux d'images poussé par Chrome (CDP), envoyé en binaire. Pleine page. « Contrôle manuel » désactivé par défaut. |
| Comportement humain | Pauses aléatoires courtes entre les actions, souris progressive, frappe touche par touche. |
| Hors périmètre | **Aucune résolution de captcha, aucun contournement ni dissimulation du bot** (empreinte, proxys, faux profil d'appareil). Si Turnstile bloque, c'est l'humain qui se connecte. |
| Actions qui dépensent | **Mode « à blanc » par défaut** : le bot décide et journalise, sans cliquer. Le mode réel s'active explicitement, avec un plafond. |
| Connaissance du jeu | On **capture avant de coder** (export de structure, relu par le joueur). On ne devine jamais un sélecteur ni un appel. |
| Livraison | Branche `claude/bot-login-setup`. Un commit n'est fait que si tous les tests passent. Fusion sur `main` uniquement sur demande. |

## 3. Règles de travail (pour limiter les questions)

**Je décide seul :** noms, structure du code, détails d'interface, tests, refactorisations, messages du journal.

**Je ne demande que si :** (a) une action dépense des ressources réelles ou est irréversible ; (b) il faut changer une décision du tableau ci-dessus ; (c) il manque une information du jeu qu'une capture peut fournir ; (d) un point de sécurité est en jeu.

**À chaque phase :** livrable décrit ci-dessous, tests verts, README à jour, commit et push, image reconstruite, puis un message court avec **ce qui est vérifié** et **ce qui ne l'est pas**.

## 4. Ce qu'on sait déjà du jeu (export du 2026-11-01)

- Page des bâtiments (`/game/asterion/buildings`, catégorie « Industrie ») : une carte par bâtiment (Mine de Fer, Extracteur de Cristal, Synthétiseur d'Éther, Centrale Énergétique, Centrale à Impulsion, Hangars, Cuve d'Éther) avec niveau, coûts, durée et bouton « Améliorer ». Le bouton porte l'attribut `disabled` quand le coût n'est pas couvert.
- Nombres avec suffixes (`201.765M`, `58,035 M`, `4,121 G`) et séparateurs décimaux variés.
- 13 planètes, stocks très différents. Les ressources par planète et les niveaux de bâtiments arrivent dans les réponses `POST /join` et `POST /resolve`.
- Les actions passent par un WebSocket authentifié (`ACTION` : `enqueue_research`, `set_active_planet`…). Aucune action « améliorer un bâtiment » capturée pour l'instant.
- Règles du wiki : 1 construction à la fois sans bonus ; énergie insuffisante = toutes les mines ralenties ; au-delà de la capacité, la production s'arrête.

## 5. Feuille de route

| Phase | Contenu | Critères d'acceptation | Ce qu'il me faut de vous |
|---|---|---|---|
| **0. Socle** *(fait)* | Connexion, vue en direct, contrôle manuel, choix d'univers, export de structure, déploiement Portainer. | 37 tests verts, image publiée. | — |
| **1. Lire l'état du jeu** (lecture seule) | Lire les cartes de bâtiments (nom, niveau, coûts, durée, bouton actif), les ressources et l'énergie ; comprendre les suffixes K/M/G ; afficher un tableau « État » dans l'interface. | Tests sur une page reconstituée d'après l'export ; le tableau correspond à l'écran du jeu sur votre compte. | Vérifier le tableau sur votre compte (une fois). |
| **2. Planificateur** | Cadre des actions automatiques : intervalle aléatoire, arrêt propre, verrou avec le contrôle manuel, journal, page Actions (case par action, réglages), export/import des réglages en JSON. Aucune action réelle. | Une action factice démarre, s'arrête, se journalise ; réglages rechargeables. | — |
| **3. Mines (à blanc)** | Règles de décision (§6), journalisées : « j'améliorerais la Mine de Fer, coût X, parce que… ». | Décisions cohérentes avec l'état affiché, sur plusieurs cycles. | Relire le journal à blanc. |
| **4. Mines (réel)** | Clic sur « Améliorer » avec délais humains, vérifications avant/après (bouton actif, file, ressources débitées), plafond d'améliorations par jour, bouton d'arrêt d'urgence. | Une amélioration réelle réussie sur votre compte, confirmée par le niveau suivant. | **Votre accord explicite** pour passer en réel. |
| **5. Plusieurs planètes** | Changement de planète, boucle sur les planètes, état par planète. | Le bot traite plusieurs planètes sans doublon ni conflit. | Une capture du sélecteur de planètes (« Planètes »). |
| **6. Observation des files** | Lecture des constructions en cours et de leur fin ; capture d'une amélioration de **bâtiment** réelle (appel WebSocket). | Le bot n'envoie jamais deux fois la même amélioration. | Une amélioration lancée à la main pendant l'enregistrement. |
| **7. Moteur API (optionnel)** | Client WebSocket authentifié avec le jeton du navigateur ; comparaison avec le moteur par clics. Les valeurs de coût et de durée du client ne sont **jamais** modifiées. | Mêmes décisions, même résultat que par l'interface. | Décision d'y aller après la phase 6. |
| **8. Robustesse** | Session expirée, erreurs, mode veille (plages horaires), protection de l'interface par mot de passe, HTTPS, notifications (optionnel). | 24 h de fonctionnement sans intervention en mode à blanc. | Choix du mot de passe de l'interface. |
| **Transversal : rapport pour l'éditeur** | Constats utiles à la protection (voir §8), mis à jour à chaque phase. | Fichier `docs/RAPPORT-EDITEUR.md`. | Relecture avant envoi. |

## 6. Valeurs par défaut des décisions ouvertes (à valider une fois)

| Sujet | Défaut proposé |
|---|---|
| Planètes (phase 3-4) | La planète affichée. Toutes les planètes à la phase 5. |
| Quelles mines | Mine de Fer, Extracteur de Cristal, Synthétiseur d'Éther. |
| Priorité entre mines | Celle dont le coût pèse **le moins sur le stock** (plus grand coût de l'une des ressources divisé par son stock, le plus petit d'abord). |
| Niveau cible | Aucun (le bot continue tant que c'est possible). Réglable par mine. |
| Énergie | Si le bilan est négatif : ne pas améliorer de mine, améliorer la Centrale Énergétique si abordable, sinon l'écrire dans le journal. |
| Stockage | Prévenir dans le journal quand une ressource atteint 95 % de sa capacité. Pas d'amélioration automatique des hangars. |
| Rythme | Un cycle toutes les 3 à 8 minutes (aléatoire), puis délais humains entre les clics. |
| File | 1 construction à la fois ; pas de nouvelle amélioration tant que le bouton est désactivé. |
| Plafond | 20 améliorations par jour au maximum, bouton d'arrêt d'urgence toujours visible. |
| Mode | À blanc au départ, réel seulement après votre accord (phase 4). |

## 7. Qualité et sécurité

- Tests : `pytest` (37 aujourd'hui), faux site local, **pages reconstituées d'après vos exports** (pseudo et valeurs remplacés). Aucune tentative de connexion n'est envoyée au vrai site par les tests.
- Les sélecteurs reposent sur les **textes et rôles** (« Améliorer », nom du bâtiment), pas sur les classes CSS (ce sont des classes utilitaires susceptibles de changer).
- Le journal ne contient jamais de mot de passe, de jeton ni de frappe clavier. L'export de structure masque valeurs, jetons, cookies, e-mails et identifiants ; il n'est jamais commité (`.gitignore`).
- L'interface reçoit le mot de passe du jeu : elle doit être protégée avant toute exposition hors de votre réseau (phase 8).

## 8. Constats pour l'éditeur (liste vivante)

1. L'action `enqueue_research` envoie depuis le client le **coût, l'heure de début, l'heure de fin et la durée** : le serveur doit les recalculer et ne pas s'y fier (non vérifié côté serveur, et non testé).
2. Le jeton d'authentification est conservé dans le stockage local du navigateur (`aleryos-central-auth-token`) et réutilisé par l'API et le WebSocket.
3. Les actions de jeu passent par un WebSocket qui reçoit ce jeton ; l'API REST attend en plus un en-tête de version de contrat client (`x-aleryos-client-contract`).

## 9. Risques et inconnues

| Risque | Parade |
|---|---|
| Mise à jour du jeu qui change la page | Sélecteurs par texte/rôle, tests sur pages reconstituées, message clair dans le journal quand un élément manque. |
| Suffixes de nombres autres que K/M/G | Le bot refuse d'agir (et le dit) s'il ne comprend pas un nombre. |
| File de construction non documentée | Phase 6 avant toute automatisation intensive ; en attendant, 1 amélioration à la fois. |
| Turnstile ou détection du bot | L'humain se connecte ; pas de contournement. Le résultat sert au rapport. |
| Mémoire (un Chromium en permanence) | Navigateur arrêté quand aucun écran n'est ouvert et qu'aucune action n'est active (phase 8). |
