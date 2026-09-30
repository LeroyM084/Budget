from datetime import date

import pytest

import db
from app import create_app

INSECABLE = " "
HTMX = {"HX-Request": "true"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def creer_enveloppe(app, nom="Courses", type="physique"):
    with app.app_context():
        return db.creer_enveloppe(nom, type)


def solde(app, id):
    with app.app_context():
        return db.get_enveloppe(id)["solde"]


def poster_mouvement(client, id, sens, montant, motif="Test", date="2026-09-30"):
    return client.post(
        f"/enveloppes/{id}/mouvements",
        data={"sens": sens, "montant": montant, "motif": motif, "date": date},
    )


def test_accueil_affiche_soldes_et_totaux(app, client):
    courses = creer_enveloppe(app, "Courses", "physique")
    banque = creer_enveloppe(app, "Banque", "demat")
    poster_mouvement(client, courses, "ajout", "10")
    poster_mouvement(client, banque, "ajout", "1234,56")

    page = client.get("/").get_data(as_text=True)

    assert "Courses" in page and "Banque" in page
    assert f"10,00{INSECABLE}€" in page
    assert f"1 244,56{INSECABLE}€" in page


def test_creation(app, client):
    reponse = client.post("/enveloppes", data={"nom": "  Courses  ", "type": "physique"})

    assert reponse.status_code == 303
    assert reponse.headers["Location"] == "/"
    assert "Courses" in client.get("/").get_data(as_text=True)
    with app.app_context():
        assert [(e["nom"], e["type"]) for e in db.lister_enveloppes()] == [("Courses", "physique")]


@pytest.mark.parametrize(("existant", "doublon"), [("Courses", "COURSES"), ("Épargne", "épargne")])
def test_creation_doublon_insensible_a_la_casse(app, client, existant, doublon):
    creer_enveloppe(app, existant)

    reponse = client.post("/enveloppes", data={"nom": doublon, "type": "demat"})

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert "Une enveloppe porte déjà ce nom." in page
    assert f'value="{doublon}"' in page
    with app.app_context():
        assert len(db.lister_enveloppes()) == 1


def test_creation_invalide(app, client):
    reponse = client.post("/enveloppes", data={"nom": "   ", "type": "carte"})

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert "Le nom est obligatoire." in page
    assert "Choisissez un type." in page
    with app.app_context():
        assert db.lister_enveloppes() == []


def test_ajout(app, client):
    id = creer_enveloppe(app)

    reponse = poster_mouvement(client, id, "ajout", "12,50", motif="Retrait DAB")

    assert reponse.status_code == 303
    assert reponse.headers["Location"] == f"/enveloppes/{id}"
    assert solde(app, id) == 1250
    page = client.get(f"/enveloppes/{id}").get_data(as_text=True)
    assert "Retrait DAB" in page
    assert f"+12,50{INSECABLE}€" in page


def test_retrait(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "20")

    reponse = poster_mouvement(client, id, "retrait", "5")

    assert reponse.status_code == 303
    assert solde(app, id) == 1500
    with app.app_context():
        assert [m["montant"] for m in db.lister_mouvements(id)] == [-500, 2000]


def test_retrait_de_tout_le_solde(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "20")

    assert poster_mouvement(client, id, "retrait", "20").status_code == 303
    assert solde(app, id) == 0


def test_retrait_refuse_si_solde_insuffisant(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "10")

    reponse = poster_mouvement(client, id, "retrait", "10,01", motif="Cinéma")

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert f"Solde insuffisant (solde actuel : 10,00{INSECABLE}€)" in page
    assert 'value="10,01"' in page
    assert 'value="Cinéma"' in page
    assert solde(app, id) == 1000


def test_retrait_a_decouvert_autorise_en_demat(app, client):
    id = creer_enveloppe(app, "Banque", "demat")
    poster_mouvement(client, id, "ajout", "10")

    assert poster_mouvement(client, id, "retrait", "25").status_code == 303
    assert solde(app, id) == -1500
    assert f"−15,00{INSECABLE}€" in client.get(f"/enveloppes/{id}").get_data(as_text=True)


@pytest.mark.parametrize(
    ("champ", "valeur", "message"),
    [
        ("sens", "", "Choisissez « Ajouter » ou « Retirer »."),
        ("montant", "abc", "Le montant doit être un nombre"),
        ("montant", "-5", "Le montant ne peut pas être négatif."),
        ("motif", "   ", "Le motif est obligatoire."),
        ("motif", "x" * 101, "Le motif ne doit pas dépasser 100 caractères."),
        ("date", "2026-02-30", "Saisissez une date valide."),
    ],
)
def test_mouvement_invalide(app, client, champ, valeur, message):
    id = creer_enveloppe(app)
    donnees = {"sens": "ajout", "montant": "5", "motif": "Test", "date": "2026-09-30", champ: valeur}

    reponse = client.post(f"/enveloppes/{id}/mouvements", data=donnees)

    assert reponse.status_code == 422
    assert message in reponse.get_data(as_text=True)
    assert solde(app, id) == 0


def test_mouvement_invalide_reaffiche_les_valeurs(app, client):
    id = creer_enveloppe(app)

    reponse = poster_mouvement(client, id, "retrait", "abc", motif="Pain", date="2026-09-15")

    page = reponse.get_data(as_text=True)
    assert 'value="retrait" required checked' in page
    assert 'value="abc"' in page
    assert 'value="Pain"' in page
    assert 'value="2026-09-15"' in page


def test_suppression_mouvement(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "10")
    poster_mouvement(client, id, "retrait", "3")
    with app.app_context():
        retrait = db.lister_mouvements(id)[0]["id"]

    reponse = client.post(f"/mouvements/{retrait}/supprimer")

    assert reponse.status_code == 303
    assert reponse.headers["Location"] == f"/enveloppes/{id}"
    assert solde(app, id) == 1000


def test_suppression_ajout_refusee_si_physique_devient_negative(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "50")
    poster_mouvement(client, id, "retrait", "40")
    with app.app_context():
        ajout = db.lister_mouvements(id)[1]["id"]

    reponse = client.post(f"/mouvements/{ajout}/supprimer")

    assert reponse.status_code == 422
    message = f"Suppression impossible : le solde passerait à −40,00{INSECABLE}€."
    assert message in reponse.get_data(as_text=True)
    assert solde(app, id) == 1000


def test_suppression_ajout_autorisee_a_decouvert_en_demat(app, client):
    id = creer_enveloppe(app, "Banque", "demat")
    poster_mouvement(client, id, "ajout", "50")
    poster_mouvement(client, id, "retrait", "40")
    with app.app_context():
        ajout = db.lister_mouvements(id)[1]["id"]

    assert client.post(f"/mouvements/{ajout}/supprimer").status_code == 303
    assert solde(app, id) == -4000


def test_suppression_enveloppe_en_cascade(app, client):
    id = creer_enveloppe(app, "Courses")
    autre = creer_enveloppe(app, "Banque", "demat")
    poster_mouvement(client, id, "ajout", "10")
    poster_mouvement(client, autre, "ajout", "5")

    reponse = client.post(f"/enveloppes/{id}/supprimer")

    assert reponse.status_code == 303
    assert reponse.headers["Location"] == "/"
    assert client.get(f"/enveloppes/{id}").status_code == 404
    with app.app_context():
        restants = db.get_db().execute("SELECT enveloppe_id FROM mouvement").fetchall()
    assert [ligne["enveloppe_id"] for ligne in restants] == [autre]


def test_renommer(app, client):
    id = creer_enveloppe(app, "courses")

    reponse = client.post(f"/enveloppes/{id}/renommer", data={"nom": "Courses"})

    assert reponse.status_code == 303
    assert reponse.headers["Location"] == f"/enveloppes/{id}"
    with app.app_context():
        assert db.get_enveloppe(id)["nom"] == "Courses"


def test_renommer_doublon(app, client):
    creer_enveloppe(app, "Courses")
    id = creer_enveloppe(app, "Banque", "demat")

    reponse = client.post(f"/enveloppes/{id}/renommer", data={"nom": "courses"})

    assert reponse.status_code == 422
    assert "Une enveloppe porte déjà ce nom." in reponse.get_data(as_text=True)
    with app.app_context():
        assert db.get_enveloppe(id)["nom"] == "Banque"


@pytest.mark.parametrize(
    ("methode", "url"),
    [
        ("get", "/enveloppes/999"),
        ("post", "/enveloppes/999/mouvements"),
        ("post", "/enveloppes/999/renommer"),
        ("post", "/enveloppes/999/supprimer"),
        ("post", "/mouvements/999/supprimer"),
    ],
)
def test_404(client, methode, url):
    assert getattr(client, methode)(url).status_code == 404


@pytest.mark.parametrize("provenance", ["cross-site", "same-site"])
def test_post_depuis_un_autre_site_refuse(app, client, provenance):
    reponse = client.post(
        "/enveloppes",
        data={"nom": "Courses", "type": "physique"},
        headers={"Sec-Fetch-Site": provenance},
    )

    assert reponse.status_code == 403
    with app.app_context():
        assert db.lister_enveloppes() == []


def test_post_depuis_le_meme_site_accepte(client):
    reponse = client.post(
        "/enveloppes",
        data={"nom": "Courses", "type": "physique"},
        headers={"Sec-Fetch-Site": "same-origin"},
    )

    assert reponse.status_code == 303


def test_affichage_en_iframe_interdit(client):
    assert client.get("/").headers["Content-Security-Policy"] == "frame-ancestors 'none'"


# --- Requêtes HTMX : fragments au lieu de redirections ---


def test_pages_chargent_htmx(client):
    page = client.get("/").get_data(as_text=True)

    assert 'src="/static/htmx.min.js" defer' in page
    assert '<body hx-boost="true"' in page
    assert client.get("/static/htmx.min.js").status_code == 200


def test_htmx_creation_renvoie_des_fragments(app, client):
    reponse = client.post("/enveloppes", data={"nom": "Courses", "type": "physique"}, headers=HTMX)

    assert reponse.status_code == 200
    assert "Location" not in reponse.headers
    page = reponse.get_data(as_text=True)
    assert "<html" not in page
    assert page.startswith('<form id="form-enveloppe"')
    assert 'value=""' in page
    assert '<ul class="cartes" id="enveloppes-physique" hx-swap-oob="true">' in page
    assert "Courses" in page
    assert '<section class="totaux" id="totaux" hx-swap-oob="true">' in page


def test_htmx_creation_invalide_renvoie_le_formulaire(client):
    reponse = client.post("/enveloppes", data={"nom": "", "type": "physique"}, headers=HTMX)

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert "<html" not in page
    assert '<form id="form-enveloppe"' in page
    assert "Le nom est obligatoire." in page
    assert "hx-swap-oob" not in page


def test_htmx_ajout_renvoie_ligne_solde_et_formulaire_vide(app, client):
    id = creer_enveloppe(app)

    reponse = client.post(
        f"/enveloppes/{id}/mouvements",
        data={"sens": "ajout", "montant": "12,50", "motif": "Salaire", "date": "2026-09-01"},
        headers=HTMX,
    )

    assert reponse.status_code == 200
    assert "Location" not in reponse.headers
    page = reponse.get_data(as_text=True)
    assert "<html" not in page
    assert page.startswith('<li class="mouvement"')
    assert "Salaire" in page
    assert f'<p class="solde" id="solde" hx-swap-oob="true">Solde : 12,50{INSECABLE}€</p>' in page
    formulaire = page[page.index('<form id="form-mouvement"'):]
    assert 'hx-swap-oob="true"' in formulaire
    assert 'value="ajout" required checked' in formulaire
    assert 'id="montant" name="montant" value=""' in formulaire
    assert 'id="motif" name="motif" maxlength="100" value=""' in formulaire
    assert f'value="{date.today().isoformat()}"' in formulaire


def test_htmx_mouvement_invalide_renvoie_le_formulaire(app, client):
    id = creer_enveloppe(app)

    reponse = client.post(
        f"/enveloppes/{id}/mouvements",
        data={"sens": "retrait", "montant": "5", "motif": "Pain", "date": "2026-09-01"},
        headers=HTMX,
    )

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert page.startswith('<form id="form-mouvement"')
    assert 'hx-swap-oob="true"' in page
    assert "Solde insuffisant" in page
    assert 'value="Pain"' in page
    assert '<li class="mouvement"' not in page


def test_htmx_suppression_mouvement_renvoie_le_solde(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "10")
    poster_mouvement(client, id, "retrait", "3")
    with app.app_context():
        retrait = db.lister_mouvements(id)[0]["id"]

    reponse = client.post(f"/mouvements/{retrait}/supprimer", headers=HTMX)

    assert reponse.status_code == 200
    assert reponse.get_data(as_text=True) == (
        f'<p class="solde" id="solde" hx-swap-oob="true">Solde : 10,00{INSECABLE}€</p>'
    )
    assert solde(app, id) == 1000


def test_htmx_suppression_mouvement_refusee_renvoie_la_ligne(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "50")
    poster_mouvement(client, id, "retrait", "40")
    with app.app_context():
        ajout = db.lister_mouvements(id)[1]["id"]

    reponse = client.post(f"/mouvements/{ajout}/supprimer", headers=HTMX)

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert page.startswith(f'<li class="mouvement" id="mouvement-{ajout}">')
    assert "Suppression impossible" in page


def test_htmx_suppression_enveloppe_navigue_vers_l_accueil(app, client):
    id = creer_enveloppe(app)

    reponse = client.post(f"/enveloppes/{id}/supprimer", headers=HTMX)

    assert reponse.status_code == 200
    assert reponse.headers["HX-Location"] == "/"
    assert "Location" not in reponse.headers
    with app.app_context():
        assert db.get_enveloppe(id) is None


def test_formulaire_booste_garde_la_redirection(app, client):
    id = creer_enveloppe(app)

    reponse = client.post(
        f"/enveloppes/{id}/renommer",
        data={"nom": "Alimentation"},
        headers={"HX-Request": "true", "HX-Boosted": "true"},
    )

    assert reponse.status_code == 303
    assert reponse.headers["Location"] == f"/enveloppes/{id}"
