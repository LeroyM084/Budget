# ROADMAP — Enveloppes

Application web perso de gestion de budget par enveloppes. Ce fichier est la source de vérité du projet : suis-le phase par phase.

## Règles de travail (à respecter strictement)

- Travaille **une phase à la fois**. À la fin de chaque phase : vérifie les critères d'acceptation, fais un commit (`phase N: <résumé>`), puis **arrête-toi et attends ma validation** avant la phase suivante.
- **Simplicité avant tout.** N'ajoute aucune fonctionnalité, dépendance ou abstraction qui n'est pas demandée ici. En cas de doute, demande.
- Pas d'ORM, pas de framework JS, pas de build front, pas de CDN. Seules dépendances Python autorisées : `flask`, `gunicorn`, `pytest`.
- Code, noms de variables et commentaires en français cohérent avec le domaine (enveloppe, mouvement, motif, solde).
- Environnement de dev : Fedora (KDE Plasma), VS Code, Firefox. Python 3 système + venv.

## Périmètre fonctionnel

1. Créer une enveloppe avec un nom et un type : `physique` (espèces) ou `demat` (compte, carte).
2. Ajouter ou retirer de l'argent dans une enveloppe, avec un montant, un motif et une date.
3. Voir le solde de chaque enveloppe, l'historique de ses mouvements, et les totaux.
4. Corriger une erreur : supprimer un mouvement, renommer ou supprimer une enveloppe.
5. Utilisable confortablement sur mobile (priorité) et sur ordinateur, sans rechargement de page visible.

Hors périmètre (ne pas faire) : multi-utilisateur, authentification, catégories, budgets mensuels, graphiques, import bancaire, transferts entre enveloppes, export, mode hors-ligne.

## Stack

- Backend : Python 3 + Flask, templates Jinja rendus côté serveur.
- Base : SQLite via le module `sqlite3` de la bibliothèque standard.
- Interactivité : HTMX 2 (fichier `htmx.min.js` copié dans `static/`, pas de CDN).
- Style : un seul fichier CSS vanilla, mobile-first.
- Prod : Gunicorn dans un conteneur Docker, derrière Caddy, sur mon homelab.

## Structure cible

```
enveloppes/
├── app.py               # création de l'app Flask + routes
├── db.py                # connexion SQLite, init, requêtes
├── argent.py            # parsing et formatage des montants
├── schema.sql
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── index.html       # liste des enveloppes + totaux
│   ├── enveloppe.html   # détail + historique
│   ├── _carte_enveloppe.html
│   ├── _mouvement.html
│   ├── _solde.html
│   └── _form_mouvement.html
├── static/
│   ├── style.css
│   ├── htmx.min.js
│   ├── manifest.json
│   └── icones/          # icon-192.png, icon-512.png, icon.svg
├── tests/
│   ├── test_argent.py
│   └── test_routes.py
├── Dockerfile
├── compose.yaml
├── scripts/backup.sh
├── .gitignore
├── README.md
└── ROADMAP.md
```

---

## Phase 0 — Initialisation

Tâches :
- Créer le dossier, `git init`, `.gitignore` (venv, `__pycache__`, `*.db`, `*.db-*`, `.pytest_cache`, `instance/`).
- Créer un venv : `python3 -m venv .venv`, l'activer, `requirements.txt` avec `flask`, `gunicorn`, `pytest` (versions épinglées aux dernières stables).
- `app.py` minimal avec une factory `create_app()` et une route `/` qui renvoie « OK ».
- `README.md` : description en 2 lignes + commandes pour lancer en dev.

Critères d'acceptation :
- `flask --app app run --debug` démarre et `/` répond.
- Le premier commit est fait.

## Phase 1 — Base de données et gestion de l'argent

### schema.sql

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS enveloppe (
    id            INTEGER PRIMARY KEY,
    nom           TEXT    NOT NULL UNIQUE CHECK (length(trim(nom)) > 0),
    type          TEXT    NOT NULL CHECK (type IN ('physique', 'demat')),
    cree_le       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS mouvement (
    id            INTEGER PRIMARY KEY,
    enveloppe_id  INTEGER NOT NULL REFERENCES enveloppe(id) ON DELETE CASCADE,
    montant       INTEGER NOT NULL CHECK (montant <> 0),   -- en centimes, signé : + ajout, − retrait
    motif         TEXT    NOT NULL CHECK (length(trim(motif)) > 0),
    date          TEXT    NOT NULL,                          -- YYYY-MM-DD, date de l'opération
    cree_le       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_mouvement_enveloppe
    ON mouvement (enveloppe_id, date DESC, id DESC);
```

Règles de conception :
- Le solde n'est **jamais stocké** : il est toujours calculé par `SUM(montant)`.
- Tous les montants sont des **entiers en centimes**. Aucun `float` nulle part dans le code.

### db.py

- `get_db()` : une connexion par requête stockée dans `flask.g`, `row_factory = sqlite3.Row`, et `PRAGMA foreign_keys = ON` exécuté à chaque connexion.
- Fermeture automatique via `app.teardown_appcontext`.
- Chemin de la base lu depuis la variable d'environnement `DATABASE_PATH`, par défaut `instance/enveloppes.db` (créer le dossier si absent).
- Commande CLI `flask --app app init-db` qui exécute `schema.sql`. La base est aussi initialisée automatiquement au démarrage si elle n'existe pas.
- Fonctions de requête, toutes avec paramètres liés (`?`), jamais de formatage de chaîne SQL :
  - `lister_enveloppes()` → id, nom, type, solde (via `LEFT JOIN` + `COALESCE(SUM(montant), 0)`), triées par type puis nom.
  - `totaux()` → total physique, total démat, total global.
  - `get_enveloppe(id)` → enveloppe + solde, ou `None`.
  - `creer_enveloppe(nom, type)`, `renommer_enveloppe(id, nom)`, `supprimer_enveloppe(id)`.
  - `lister_mouvements(enveloppe_id)` → triés par `date DESC, id DESC`.
  - `ajouter_mouvement(enveloppe_id, montant_centimes, motif, date)`.
  - `supprimer_mouvement(id)` → renvoie l'`enveloppe_id` concerné.
  - `motifs_recents(limite=10)` → motifs distincts les plus récents, pour l'autocomplétion.

### argent.py

- `parser_montant(texte) -> int` : accepte `12`, `12.5`, `12,50`, `1 234,56`, espaces insécables compris. Utilise `decimal.Decimal`. Refuse (lève `ValueError` avec message clair) : vide, négatif, zéro, plus de 2 décimales, non numérique, supérieur à 1 000 000 €. Renvoie des centimes positifs.
- `formater_euros(centimes) -> str` : format français, `1 234,56 €`, signe `−` pour les négatifs. Enregistré comme filtre Jinja `euros`.

Critères d'acceptation :
- `flask init-db` crée la base avec les deux tables.
- `tests/test_argent.py` couvre tous les cas valides et invalides listés ci-dessus et passe.

## Phase 2 — Routes et pages fonctionnelles (sans HTMX)

L'application doit être **entièrement utilisable avec de simples formulaires HTML et des redirections** (pattern Post/Redirect/Get). HTMX viendra se greffer par-dessus en phase 3.

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/` | Liste des enveloppes, soldes, totaux, formulaire de création |
| POST | `/enveloppes` | Créer une enveloppe |
| GET | `/enveloppes/<id>` | Détail : solde, formulaire de mouvement, historique |
| POST | `/enveloppes/<id>/mouvements` | Ajouter un mouvement |
| POST | `/enveloppes/<id>/renommer` | Renommer |
| POST | `/enveloppes/<id>/supprimer` | Supprimer l'enveloppe et ses mouvements |
| POST | `/mouvements/<id>/supprimer` | Supprimer un mouvement |

Formulaire de mouvement :
- Champ `sens` : `ajout` ou `retrait` (deux boutons radio stylés en segmented control).
- Champ `montant` (texte, parsé par `parser_montant`), `motif` (texte, max 100 caractères, trim), `date` (input `date`, valeur par défaut aujourd'hui).
- Le signe est appliqué côté serveur selon `sens`. L'utilisateur ne tape jamais de signe.

Règles métier :
- **Le solde d'une enveloppe physique ne peut jamais devenir négatif.** Un retrait qui le rendrait négatif est refusé avec le message « Solde insuffisant (solde actuel : X €) » ; la suppression d'un ajout qui le rendrait négatif est refusée aussi. Une enveloppe démat peut être à découvert (solde négatif). *(Décidé après la phase 2.)*
- Nom d'enveloppe unique (insensible à la casse) : message d'erreur si doublon.
- Enveloppe ou mouvement inexistant : 404.
- En cas d'erreur de validation, réafficher le formulaire avec les valeurs saisies et le message d'erreur sous le champ concerné.

Sécurité *(décidé après la phase 2)* :
- Protection CSRF : refuser (403) les POST dont l'en-tête `Sec-Fetch-Site` indique un autre site.
- Interdire l'affichage de l'app dans une iframe (`Content-Security-Policy: frame-ancestors 'none'`).

Templates :
- `base.html` : `<html lang="fr">`, meta viewport, lien CSS, en-tête avec le nom de l'app et lien vers l'accueil.
- Les fragments `_*.html` sont inclus dans les pages complètes via `{% include %}` dès maintenant, pour être réutilisés tels quels en phase 3.

Critères d'acceptation :
- Tout le périmètre fonctionnel marche dans Firefox avec JavaScript désactivé.
- `tests/test_routes.py` (avec le client de test Flask et une base temporaire) couvre : création, doublon, ajout, retrait, retrait refusé, suppression de mouvement, suppression d'enveloppe en cascade, 404.

## Phase 3 — HTMX : zéro rechargement visible

- Copier `htmx.min.js` (dernière version 2.x) dans `static/` et l'inclure dans `base.html` avec `defer`.
- `<body hx-boost="true">` : tous les liens et formulaires classiques deviennent des navigations AJAX.
- Détecter les requêtes HTMX via l'en-tête `HX-Request`. Pour une requête HTMX, renvoyer un **fragment** ; sinon, garder la redirection de la phase 2 (fallback).

Comportements attendus :
- **Ajout ou retrait d'un mouvement** : `hx-post` sur le formulaire. La réponse contient la nouvelle ligne `_mouvement.html` (insérée en haut de l'historique, `hx-swap="afterbegin"`) et le nouveau solde `_solde.html` en `hx-swap-oob="true"`. Le formulaire est réinitialisé (montant et motif vidés, date remise à aujourd'hui) et la modale se ferme.
- **Erreur de validation** : la réponse renvoie le formulaire avec l'erreur, qui remplace le formulaire existant, sans fermer la modale. Configurer HTMX pour swapper les réponses 422 (`htmx.config.responseHandling`) et renvoyer 422 dans ce cas.
- **Suppression d'un mouvement** : `hx-post` + `hx-confirm="Supprimer ce mouvement ?"`, la ligne disparaît (`hx-swap="outerHTML"` avec réponse vide) et le solde se met à jour en OOB.
- **Création d'enveloppe** : la nouvelle carte apparaît dans la bonne section de la liste, les totaux se mettent à jour en OOB.
- **Suppression d'enveloppe** : confirmation, puis redirection vers l'accueil (`HX-Redirect`).
- Afficher un indicateur de chargement discret sur le bouton pendant la requête (`hx-indicator` / classe `htmx-request`) et désactiver le bouton pour éviter les doubles soumissions (`hx-disabled-elt="this"`).

Critères d'acceptation :
- Aucune action ne provoque de rechargement complet de page.
- Avec JavaScript désactivé, tout fonctionne toujours comme en phase 2.
- Les tests de la phase 2 passent toujours ; ajouter des tests vérifiant qu'une requête avec `HX-Request: true` reçoit un fragment et non une redirection.

## Phase 4 — UI et UX mobile-first

Principes :
- Conçu d'abord pour un écran de 375 px de large, puis adapté au desktop (contenu centré, largeur max ~640 px).
- CSS vanilla avec variables (`--fond`, `--texte`, `--accent`, `--positif`, `--negatif`, `--rayon`…). Thème clair et sombre via `prefers-color-scheme`.
- Police système (`system-ui`). Chiffres alignés avec `font-variant-numeric: tabular-nums`.
- Toutes les zones tactiles font au moins 44 × 44 px.

Écran d'accueil :
- En haut, carte « Total » bien visible, avec en dessous le détail physique / démat.
- Deux sections, « Physique » et « Dématérialisé », chacune avec ses cartes d'enveloppe (nom, solde, icône SVG inline : billet pour physique, carte pour démat).
- Bouton flottant ou fixé en bas « Nouvelle enveloppe » qui ouvre une modale.
- État vide : message et bouton de création quand il n'y a aucune enveloppe.

Écran enveloppe :
- Solde en grand en haut.
- Deux gros boutons côte à côte, fixés en bas de l'écran sur mobile (`position: sticky`, avec `env(safe-area-inset-bottom)`) : « + Ajouter » (vert) et « − Retirer » (rouge). Chacun ouvre la modale de mouvement avec le sens déjà sélectionné.
- Historique regroupé par date (« Aujourd'hui », « Hier », puis date en toutes lettres), chaque ligne affichant motif et montant coloré (+ vert, − rouge).
- Menu discret (renommer, supprimer l'enveloppe).
- État vide : « Aucun mouvement pour l'instant ».

Modale de saisie :
- Élément `<dialog>` natif, ouvert en « bottom sheet » sur mobile (collé en bas, coins arrondis en haut), centré sur desktop. Fermeture par bouton, touche Échap ou clic sur le fond.
- Champ montant : `inputmode="decimal"`, `autocomplete="off"`, grande taille de police, focus automatique à l'ouverture.
- Champ motif avec un `<datalist>` alimenté par `motifs_recents()`.
- Le moins de JavaScript possible : un petit script inline dans `base.html` pour ouvrir/fermer les `<dialog>` et présélectionner le sens. Rien d'autre.

Retours visuels :
- Petite animation CSS sur le solde quand il change (classe ajoutée via `htmx:afterSwap`, ou `@keyframes` sur les éléments insérés).
- Nouvelle ligne d'historique qui apparaît en fondu.

Critères d'acceptation :
- Test manuel sur téléphone réel (voir phase 6) : ajouter un retrait de 3 taps + saisie, sans zoom involontaire (taille de police des inputs ≥ 16 px), sans débordement horizontal.
- Rendu correct en clair et en sombre.

## Phase 5 — PWA (ajout à l'écran d'accueil)

- `static/manifest.json` : `name` « Enveloppes », `short_name`, `start_url: "/"`, `display: "standalone"`, `background_color` et `theme_color` cohérents avec le CSS, icônes 192 et 512 px (dont une `purpose: "maskable"`).
- Générer une icône simple et originale en SVG (`static/icones/icon.svg`) puis les PNG 192 et 512 (script ou outil au choix, sans ajouter de dépendance au projet).
- Dans `base.html` : lien vers le manifest, `<meta name="theme-color">` (clair et sombre), `apple-touch-icon`, `<meta name="apple-mobile-web-app-capable" content="yes">`.
- Pas de service worker : l'app a besoin du serveur de toute façon.

Critères d'acceptation :
- Le manifest est valide dans les outils de dev de Firefox (onglet Application / Manifest).

## Phase 6 — Test sur téléphone en local

- Lancer en dev accessible sur le réseau local : `flask --app app run --debug --host 0.0.0.0 --port 5000`.
- Sur Fedora, ouvrir temporairement le port : `sudo firewall-cmd --add-port=5000/tcp` (sans `--permanent`, donc fermé au prochain reboot).
- Ouvrir `http://<ip-du-pc>:5000` sur le téléphone et dérouler tout le périmètre fonctionnel.
- Lister les problèmes d'UX constatés et les corriger avant de passer au déploiement.

## Phase 7 — Conteneurisation

`Dockerfile` :
- Image `python:3.13-slim`.
- Utilisateur non-root.
- Installation des dépendances depuis `requirements.txt` (sans `pytest` en prod : séparer `requirements-dev.txt` si besoin).
- `DATABASE_PATH=/data/enveloppes.db`, dossier `/data` déclaré en volume.
- Commande : `gunicorn -w 2 -b 0.0.0.0:8000 "app:create_app()"`.
- `HEALTHCHECK` sur une route `/sante` qui renvoie 200 (à ajouter dans `app.py`).
- Activer le mode WAL de SQLite au démarrage (`PRAGMA journal_mode=WAL`) pour supporter plusieurs workers.

`compose.yaml` :
- Service `enveloppes`, `restart: unless-stopped`, volume nommé monté sur `/data`.
- **Aucun port publié** sur l'hôte.
- Rattaché à un réseau Docker dédié `enveloppes_net` et au réseau de Caddy (réseau externe, nom à paramétrer).

Critères d'acceptation :
- `docker compose up -d --build` en local fonctionne, les données survivent à un `docker compose down` / `up`.

## Après la phase 7 — Décisions et ajouts

- **Déploiement** : HTTPS via mon Cloudflare Tunnel ; déploiement et infrastructure gérés par moi. La phase 6 (test sur téléphone) est reportée.
- **Authentification** (ajoutée à ma demande) :
  - Connexion par identifiant et mot de passe, **aucune inscription** : comptes créés ou modifiés avec `flask --app app utilisateur NOM`.
  - Limitation stricte des essais : 5 échecs par période glissante de 15 minutes, par IP et par identifiant ; au-delà, réponse 429.
  - Session de 30 jours ; changer le mot de passe ferme les sessions ouvertes.

## Phase 8 — Déploiement sur le homelab

Contexte : VM Ubuntu Server sur Proxmox, Docker géré via Portainer, Caddy en reverse proxy, domaines `.lan` via réécritures DNS AdGuard, accès distant via Tailscale (Split DNS). Code hébergé sur mon Forgejo.

- Pousser le dépôt sur Forgejo.
- Déployer la stack via Portainer (stack depuis le dépôt Git ou compose collé).
- Bloc Caddy à ajouter, par exemple :
  ```
  enveloppes.lan {
      tls internal
      reverse_proxy enveloppes:8000
  }
  ```
- Réécriture DNS AdGuard : `enveloppes.lan` → IP de la VM.
- **Données financières : pas d'exposition publique.** Pas de Cloudflare Tunnel, accès uniquement en LAN et via Tailscale.
- Point à trancher avec moi : l'installation de la PWA sur mobile exige un HTTPS reconnu par le téléphone. Options : installer l'autorité racine de Caddy (`tls internal`) sur le téléphone, ou utiliser un certificat Tailscale (`*.ts.net` via `tailscale serve` / `tailscale cert`). Me présenter les deux avec leurs étapes, sans choisir à ma place.

`scripts/backup.sh` :
- Sauvegarde à chaud avec `sqlite3 /data/enveloppes.db ".backup '/backups/enveloppes-$(date +%F).db'"` exécuté dans le conteneur (ou via `docker exec`).
- Rotation : garder les 30 dernières sauvegardes.
- Documenter dans le README la ligne cron à ajouter sur la VM (quotidienne).

Critères d'acceptation :
- L'app est accessible sur `https://enveloppes.lan` depuis le PC et depuis le téléphone (Wi-Fi maison et 4G via Tailscale).
- L'app est installée sur l'écran d'accueil du téléphone et s'ouvre en plein écran.
- Une sauvegarde manuelle fonctionne et la restauration est documentée dans le README.

## Phase 9 — Finition

- Relire tout le code : supprimer le code mort, uniformiser les noms, vérifier qu'aucun `float` n'est utilisé pour l'argent.
- `README.md` complet : présentation, dev local, tests, déploiement, sauvegarde et restauration.
- `pytest` passe intégralement.
- Tag `v1.0.0`.

---

## Idées pour plus tard (NE PAS implémenter sans demande explicite)

Transfert entre enveloppes, objectif par enveloppe, archivage d'enveloppe, recherche dans l'historique, export CSV.
