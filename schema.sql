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

-- Comptes créés en ligne de commande uniquement (pas d'inscription).
CREATE TABLE IF NOT EXISTS utilisateur (
    id            INTEGER PRIMARY KEY,
    nom           TEXT    NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(nom)) > 0),
    mot_de_passe  TEXT    NOT NULL,                          -- empreinte scrypt (werkzeug.security)
    cree_le       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Échecs de connexion récents, pour limiter les essais par IP et par identifiant.
CREATE TABLE IF NOT EXISTS echec_connexion (
    id            INTEGER PRIMARY KEY,
    ip            TEXT    NOT NULL,
    nom           TEXT    NOT NULL,                          -- identifiant saisi, en minuscules
    horodatage    INTEGER NOT NULL                           -- secondes depuis l'époque Unix
);

CREATE INDEX IF NOT EXISTS idx_echec_ip ON echec_connexion (ip, horodatage);
CREATE INDEX IF NOT EXISTS idx_echec_nom ON echec_connexion (nom, horodatage);

CREATE TABLE IF NOT EXISTS parametre (
    cle           TEXT    PRIMARY KEY,
    valeur        TEXT    NOT NULL
);
