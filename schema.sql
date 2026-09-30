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
