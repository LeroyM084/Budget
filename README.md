# Enveloppes

Application web personnelle de gestion de budget par enveloppes (espèces et comptes).
Python + Flask + SQLite, pensée d'abord pour le mobile.

## Lancer en développement

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
flask --app app run --debug
```

L'application est alors disponible sur <http://127.0.0.1:5000>.

La base SQLite est créée automatiquement au démarrage dans `instance/enveloppes.db`
(chemin modifiable avec la variable d'environnement `DATABASE_PATH`).
`flask --app app init-db` crée les tables manquantes sans toucher aux données.

## Tests

```sh
pytest
```

## Docker

L'image lance Gunicorn (2 workers) sur le port 8000, sans le publier sur l'hôte : le proxy
(Caddy ou cloudflared) joint l'application à l'adresse `http://enveloppes:8000` par son propre
réseau Docker, dont le nom se règle avec la variable `CADDY_NETWORK` (`caddy` par défaut).
Les données sont dans le volume `enveloppes_donnees`, monté sur `/data`.

```sh
docker compose up -d --build
```

Avec Portainer : créer la stack depuis le dépôt Git (la construction de l'image a besoin des
sources), la nommer `enveloppes` et définir `CADDY_NETWORK` dans ses variables d'environnement.
Chaque redéploiement reconstruit l'image avec le code à jour.

Pour un essai local sans proxy, créer d'abord le réseau attendu : `docker network create caddy`.

## Icônes

L'icône source est `static/icones/icon.svg`. Pour régénérer les PNG (ImageMagick) :

```sh
cd static/icones
magick -density 96 -background none icon.svg -alpha off -strip icon-512.png
magick -density 36 -background none icon.svg -alpha off -strip icon-192.png
```
