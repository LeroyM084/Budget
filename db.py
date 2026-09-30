import os
import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    """Connexion SQLite de la requête en cours, ouverte au premier appel."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def fermer_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with current_app.open_resource("schema.sql") as fichier:
        get_db().executescript(fichier.read().decode("utf-8"))


def commande_init_db():
    """Crée les tables de la base (sans toucher aux données existantes)."""
    init_db()
    print(f"Base initialisée : {current_app.config['DATABASE_PATH']}")


def init_app(app):
    chemin = Path(os.environ.get("DATABASE_PATH") or Path(app.instance_path, "enveloppes.db"))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    app.config["DATABASE_PATH"] = str(chemin)

    app.teardown_appcontext(fermer_db)
    app.cli.command("init-db")(commande_init_db)

    if not chemin.exists():
        with app.app_context():
            init_db()


def lister_enveloppes():
    return get_db().execute(
        """
        SELECT e.id, e.nom, e.type, COALESCE(SUM(m.montant), 0) AS solde
        FROM enveloppe AS e
        LEFT JOIN mouvement AS m ON m.enveloppe_id = e.id
        GROUP BY e.id
        ORDER BY e.type, e.nom COLLATE NOCASE
        """
    ).fetchall()


def totaux():
    return get_db().execute(
        """
        SELECT
            COALESCE(SUM(m.montant) FILTER (WHERE e.type = 'physique'), 0) AS physique,
            COALESCE(SUM(m.montant) FILTER (WHERE e.type = 'demat'), 0) AS demat,
            COALESCE(SUM(m.montant), 0) AS global
        FROM mouvement AS m
        JOIN enveloppe AS e ON e.id = m.enveloppe_id
        """
    ).fetchone()


def get_enveloppe(id):
    return get_db().execute(
        """
        SELECT e.id, e.nom, e.type, COALESCE(SUM(m.montant), 0) AS solde
        FROM enveloppe AS e
        LEFT JOIN mouvement AS m ON m.enveloppe_id = e.id
        WHERE e.id = ?
        GROUP BY e.id
        """,
        (id,),
    ).fetchone()


def creer_enveloppe(nom, type):
    db = get_db()
    curseur = db.execute("INSERT INTO enveloppe (nom, type) VALUES (?, ?)", (nom, type))
    db.commit()
    return curseur.lastrowid


def renommer_enveloppe(id, nom):
    db = get_db()
    db.execute("UPDATE enveloppe SET nom = ? WHERE id = ?", (nom, id))
    db.commit()


def supprimer_enveloppe(id):
    db = get_db()
    db.execute("DELETE FROM enveloppe WHERE id = ?", (id,))
    db.commit()


def lister_mouvements(enveloppe_id):
    return get_db().execute(
        """
        SELECT id, montant, motif, date
        FROM mouvement
        WHERE enveloppe_id = ?
        ORDER BY date DESC, id DESC
        """,
        (enveloppe_id,),
    ).fetchall()


def ajouter_mouvement(enveloppe_id, montant_centimes, motif, date):
    db = get_db()
    curseur = db.execute(
        "INSERT INTO mouvement (enveloppe_id, montant, motif, date) VALUES (?, ?, ?, ?)",
        (enveloppe_id, montant_centimes, motif, date),
    )
    db.commit()
    return curseur.lastrowid


def supprimer_mouvement(id):
    """Supprime un mouvement et renvoie l'id de son enveloppe (None s'il n'existe pas)."""
    db = get_db()
    ligne = db.execute("SELECT enveloppe_id FROM mouvement WHERE id = ?", (id,)).fetchone()
    if ligne is None:
        return None
    db.execute("DELETE FROM mouvement WHERE id = ?", (id,))
    db.commit()
    return ligne["enveloppe_id"]


def motifs_recents(limite=10):
    lignes = get_db().execute(
        """
        SELECT motif
        FROM mouvement
        GROUP BY motif
        ORDER BY MAX(id) DESC
        LIMIT ?
        """,
        (limite,),
    ).fetchall()
    return [ligne["motif"] for ligne in lignes]
