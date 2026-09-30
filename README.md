# Enveloppes

Application web personnelle de gestion de budget par enveloppes (espèces et comptes).
Python + Flask + SQLite, pensée d'abord pour le mobile.

## Lancer en développement

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
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
