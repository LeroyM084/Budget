import os
import secrets
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
    db = get_db()
    with current_app.open_resource("schema.sql") as fichier:
        db.executescript(fichier.read().decode("utf-8"))
    # WAL : un worker Gunicorn peut lire pendant qu'un autre écrit.
    db.execute("PRAGMA journal_mode = WAL")


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

    # Sans effet sur une base existante ; exécuté à chaque démarrage pour que
    # deux workers lancés en même temps ne se gênent pas à la création.
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


def get_mouvement(id):
    return get_db().execute(
        "SELECT id, enveloppe_id, montant, motif, date FROM mouvement WHERE id = ?",
        (id,),
    ).fetchone()


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


def get_utilisateur(id):
    return get_db().execute(
        "SELECT id, nom, mot_de_passe FROM utilisateur WHERE id = ?", (id,)
    ).fetchone()


def get_utilisateur_par_nom(nom):
    return get_db().execute(
        "SELECT id, nom, mot_de_passe FROM utilisateur WHERE nom = ?", (nom,)
    ).fetchone()


def enregistrer_utilisateur(nom, mot_de_passe):
    """Crée l'utilisateur ou remplace son mot de passe ; renvoie True s'il vient d'être créé."""
    db = get_db()
    existant = get_utilisateur_par_nom(nom)
    if existant is None:
        db.execute("INSERT INTO utilisateur (nom, mot_de_passe) VALUES (?, ?)", (nom, mot_de_passe))
    else:
        db.execute(
            "UPDATE utilisateur SET mot_de_passe = ? WHERE id = ?", (mot_de_passe, existant["id"])
        )
    db.commit()
    return existant is None


def echecs_recents(ip, nom, depuis):
    """Horodatages (du plus récent au plus ancien) des échecs de connexion postérieurs
    à `depuis`, pour cette IP puis pour cet identifiant."""
    db = get_db()
    par_ip = db.execute(
        "SELECT horodatage FROM echec_connexion WHERE ip = ? AND horodatage > ?"
        " ORDER BY horodatage DESC",
        (ip, depuis),
    ).fetchall()
    par_nom = db.execute(
        "SELECT horodatage FROM echec_connexion WHERE nom = ? AND horodatage > ?"
        " ORDER BY horodatage DESC",
        (nom, depuis),
    ).fetchall()
    return [ligne[0] for ligne in par_ip], [ligne[0] for ligne in par_nom]


def enregistrer_echec(ip, nom, horodatage, oublier_avant):
    db = get_db()
    db.execute("DELETE FROM echec_connexion WHERE horodatage <= ?", (oublier_avant,))
    db.execute(
        "INSERT INTO echec_connexion (ip, nom, horodatage) VALUES (?, ?, ?)", (ip, nom, horodatage)
    )
    db.commit()


def cle_secrete():
    """Clé de signature des sessions : tirée au hasard au premier démarrage, puis conservée.
    INSERT OR IGNORE garantit une seule clé même si les deux workers démarrent ensemble."""
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO parametre (cle, valeur) VALUES ('cle_secrete', ?)",
        (secrets.token_hex(32),),
    )
    db.commit()
    return db.execute("SELECT valeur FROM parametre WHERE cle = 'cle_secrete'").fetchone()[0]
