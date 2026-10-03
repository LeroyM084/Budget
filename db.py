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
    migrer_enveloppes(db)
    migrer_decouvert(db)
    # WAL : un worker Gunicorn peut lire pendant qu'un autre écrit.
    db.execute("PRAGMA journal_mode = WAL")


def migrer_enveloppes(db):
    """Met à niveau une table enveloppe d'une version antérieure : ajoute propriétaire et
    position (comptes séparés) et autorise le type « epargne ». SQLite ne sait modifier ni
    une contrainte UNIQUE ni un CHECK : la table est reconstruite."""
    colonnes = {ligne["name"] for ligne in db.execute("PRAGMA table_info(enveloppe)")}
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name = 'enveloppe'").fetchone()[0]
    if "utilisateur_id" in colonnes and "'epargne'" in sql:
        return
    # La colonne decouvert_autorise, si elle existe, est ajoutée ensuite par migrer_decouvert.
    if "decouvert_autorise" in colonnes:
        raise RuntimeError("Table enveloppe inattendue : migration impossible.")
    if "utilisateur_id" in colonnes:
        selection = "SELECT id, utilisateur_id, nom, type, position, cree_le FROM enveloppe"
    else:
        selection = """
        SELECT id, (SELECT MIN(id) FROM utilisateur), nom, type,
               ROW_NUMBER() OVER (PARTITION BY type ORDER BY nom COLLATE NOCASE), cree_le
        FROM enveloppe"""
    # Sans cela, supprimer l'ancienne table effacerait les mouvements en cascade.
    db.execute("PRAGMA foreign_keys = OFF")
    db.executescript(
        f"""
        BEGIN;
        CREATE TABLE enveloppe_nouvelle (
            id              INTEGER PRIMARY KEY,
            utilisateur_id  INTEGER REFERENCES utilisateur(id) ON DELETE CASCADE,
            nom             TEXT    NOT NULL CHECK (length(trim(nom)) > 0),
            type            TEXT    NOT NULL CHECK (type IN ('physique', 'demat', 'epargne')),
            position        INTEGER NOT NULL DEFAULT 0,
            cree_le         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
            UNIQUE (utilisateur_id, nom)
        );
        INSERT INTO enveloppe_nouvelle (id, utilisateur_id, nom, type, position, cree_le)
        {selection};
        DROP TABLE enveloppe;
        ALTER TABLE enveloppe_nouvelle RENAME TO enveloppe;
        COMMIT;
        """
    )
    db.execute("PRAGMA foreign_keys = ON")


def migrer_decouvert(db):
    """Ajoute le choix du découvert ; les enveloppes démat le gardent autorisé."""
    colonnes = {ligne["name"] for ligne in db.execute("PRAGMA table_info(enveloppe)")}
    if "decouvert_autorise" in colonnes:
        return
    db.execute(
        "ALTER TABLE enveloppe ADD COLUMN decouvert_autorise INTEGER NOT NULL DEFAULT 0"
    )
    db.execute("UPDATE enveloppe SET decouvert_autorise = 1 WHERE type = 'demat'")
    db.commit()


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


def proprietaire():
    """Id de l'utilisateur connecté : toutes les requêtes sur les enveloppes s'y limitent."""
    return g.utilisateur["id"]


def lister_enveloppes():
    return get_db().execute(
        """
        SELECT e.id, e.nom, e.type, e.decouvert_autorise, COALESCE(SUM(m.montant), 0) AS solde
        FROM enveloppe AS e
        LEFT JOIN mouvement AS m ON m.enveloppe_id = e.id
        WHERE e.utilisateur_id = ?
        GROUP BY e.id
        ORDER BY e.type, e.position, e.nom COLLATE NOCASE
        """,
        (proprietaire(),),
    ).fetchall()


def totaux():
    return get_db().execute(
        """
        SELECT
            COALESCE(SUM(m.montant) FILTER (WHERE e.type = 'physique'), 0) AS physique,
            COALESCE(SUM(m.montant) FILTER (WHERE e.type = 'demat'), 0) AS demat,
            COALESCE(SUM(m.montant) FILTER (WHERE e.type = 'epargne'), 0) AS epargne,
            -- L'épargne est mise de côté : elle ne compte pas dans le total disponible.
            COALESCE(SUM(m.montant) FILTER (WHERE e.type <> 'epargne'), 0) AS global
        FROM mouvement AS m
        JOIN enveloppe AS e ON e.id = m.enveloppe_id
        WHERE e.utilisateur_id = ?
        """,
        (proprietaire(),),
    ).fetchone()


def bilan_quinzaines(type):
    """Entrées et sorties par quinzaine (du 1er au 14, du 15 à la fin du mois), de la plus ancienne."""
    return get_db().execute(
        """
        SELECT
            substr(m.date, 1, 7) AS mois,
            CASE WHEN CAST(substr(m.date, 9, 2) AS INTEGER) < 15 THEN 1 ELSE 2 END AS quinzaine,
            COALESCE(SUM(m.montant) FILTER (WHERE m.montant > 0), 0) AS entrees,
            COALESCE(-SUM(m.montant) FILTER (WHERE m.montant < 0), 0) AS sorties
        FROM mouvement AS m
        JOIN enveloppe AS e ON e.id = m.enveloppe_id
        WHERE e.utilisateur_id = ? AND e.type = ?
        GROUP BY mois, quinzaine
        ORDER BY mois, quinzaine
        """,
        (proprietaire(), type),
    ).fetchall()


def get_enveloppe(id):
    return get_db().execute(
        """
        SELECT e.id, e.nom, e.type, e.decouvert_autorise, COALESCE(SUM(m.montant), 0) AS solde
        FROM enveloppe AS e
        LEFT JOIN mouvement AS m ON m.enveloppe_id = e.id
        WHERE e.id = ? AND e.utilisateur_id = ?
        GROUP BY e.id
        """,
        (id, proprietaire()),
    ).fetchone()


def creer_enveloppe(nom, type):
    """Crée l'enveloppe à la fin de sa section ; seul le type démat autorise d'emblée le découvert."""
    db = get_db()
    curseur = db.execute(
        """
        INSERT INTO enveloppe (utilisateur_id, nom, type, position, decouvert_autorise)
        SELECT ?, ?, ?, COALESCE(MAX(position), 0) + 1, ?
        FROM enveloppe WHERE utilisateur_id = ? AND type = ?
        """,
        (proprietaire(), nom, type, type == "demat", proprietaire(), type),
    )
    db.commit()
    return curseur.lastrowid


def renommer_enveloppe(id, nom):
    db = get_db()
    db.execute(
        "UPDATE enveloppe SET nom = ? WHERE id = ? AND utilisateur_id = ?", (nom, id, proprietaire())
    )
    db.commit()


def changer_decouvert(id, autorise):
    db = get_db()
    db.execute(
        "UPDATE enveloppe SET decouvert_autorise = ? WHERE id = ? AND utilisateur_id = ?",
        (autorise, id, proprietaire()),
    )
    db.commit()


def supprimer_enveloppe(id):
    db = get_db()
    db.execute("DELETE FROM enveloppe WHERE id = ? AND utilisateur_id = ?", (id, proprietaire()))
    db.commit()


def ordonner_enveloppes(ids):
    """Range les enveloppes dans l'ordre donné. Refuse (False) si la liste ne correspond pas
    exactement à une section complète (même type) de l'utilisateur."""
    db = get_db()
    lignes = db.execute(
        "SELECT id, type FROM enveloppe WHERE utilisateur_id = ?", (proprietaire(),)
    ).fetchall()
    types = {ligne["type"] for ligne in lignes if ligne["id"] in ids}
    if len(types) != 1 or len(set(ids)) != len(ids):
        return False
    section = {ligne["id"] for ligne in lignes if ligne["type"] in types}
    if section != set(ids):
        return False
    db.executemany(
        "UPDATE enveloppe SET position = ? WHERE id = ?",
        [(position, id) for position, id in enumerate(ids, start=1)],
    )
    db.commit()
    return True


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
        """
        SELECT m.id, m.enveloppe_id, m.montant, m.motif, m.date
        FROM mouvement AS m
        JOIN enveloppe AS e ON e.id = m.enveloppe_id
        WHERE m.id = ? AND e.utilisateur_id = ?
        """,
        (id, proprietaire()),
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
    ligne = get_mouvement(id)
    if ligne is None:
        return None
    db = get_db()
    db.execute("DELETE FROM mouvement WHERE id = ?", (id,))
    db.commit()
    return ligne["enveloppe_id"]


def motifs_recents(limite=10):
    lignes = get_db().execute(
        """
        SELECT m.motif
        FROM mouvement AS m
        JOIN enveloppe AS e ON e.id = m.enveloppe_id
        WHERE e.utilisateur_id = ?
        GROUP BY m.motif
        ORDER BY MAX(m.id) DESC
        LIMIT ?
        """,
        (proprietaire(), limite),
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
        curseur = db.execute(
            "INSERT INTO utilisateur (nom, mot_de_passe) VALUES (?, ?)", (nom, mot_de_passe)
        )
        # Données d'avant les comptes séparés : elles reviennent au premier compte créé.
        db.execute(
            "UPDATE enveloppe SET utilisateur_id = ? WHERE utilisateur_id IS NULL",
            (curseur.lastrowid,),
        )
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
