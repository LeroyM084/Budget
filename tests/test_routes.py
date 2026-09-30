import json
import re
import struct
from datetime import date, timedelta

import pytest

import db
from app import create_app, libelle_jour

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
    assert fichier_statique(client, "htmx.min.js").startswith(b"var htmx=")


def test_htmx_creation_renvoie_des_fragments(app, client):
    reponse = client.post("/enveloppes", data={"nom": "Courses", "type": "physique"}, headers=HTMX)

    assert reponse.status_code == 200
    assert "Location" not in reponse.headers
    page = reponse.get_data(as_text=True)
    assert "<html" not in page
    formulaire, tableau = page.split('<div class="tableau" id="tableau" hx-swap-oob="true">')
    assert formulaire.startswith('<form id="form-enveloppe"')
    assert 'hx-swap-oob="true"' in formulaire
    assert 'id="nom" name="nom" value=""' in formulaire
    assert "Courses" in tableau
    assert "carte-total" in tableau


def test_htmx_creation_invalide_renvoie_le_formulaire(client):
    reponse = client.post("/enveloppes", data={"nom": "", "type": "physique"}, headers=HTMX)

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert "<html" not in page
    assert page.startswith('<form id="form-enveloppe"')
    assert "Le nom est obligatoire." in page
    assert 'id="tableau"' not in page


def test_htmx_ajout_renvoie_historique_solde_et_formulaire_vide(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "5", motif="Retrait DAB", date="2025-12-31")
    hier = (date.today() - timedelta(days=1)).isoformat()

    reponse = client.post(
        f"/enveloppes/{id}/mouvements",
        data={"sens": "ajout", "montant": "12,50", "motif": "Salaire", "date": hier},
        headers=HTMX,
    )

    assert reponse.status_code == 200
    assert "Location" not in reponse.headers
    page = reponse.get_data(as_text=True)
    assert "<html" not in page
    assert page.startswith('<div class="historique" id="historique" hx-swap-oob="true">')
    assert page.count('<li class="mouvement nouveau">') == 1
    assert page.index('<li class="mouvement nouveau">') < page.index("Salaire") < page.index("Retrait DAB")
    assert '<p class="solde modifie" id="solde" hx-swap-oob="true">' in page
    assert f"17,50{INSECABLE}€" in page
    formulaire = page[page.index('<form id="form-mouvement"'):]
    assert 'hx-swap-oob="true"' in formulaire
    assert 'value="ajout" required checked' in formulaire
    assert 'id="montant" name="montant" value=""' in formulaire
    assert 'id="motif" name="motif" maxlength="100" value=""' in formulaire
    assert f'value="{date.today().isoformat()}"' in formulaire
    assert '<option value="Salaire">' in formulaire


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
    assert 'id="historique"' not in page


def test_htmx_suppression_mouvement_renvoie_historique_et_solde(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "10", motif="Retrait DAB")
    poster_mouvement(client, id, "retrait", "3", motif="Boulangerie")
    with app.app_context():
        retrait = db.lister_mouvements(id)[0]["id"]

    reponse = client.post(f"/mouvements/{retrait}/supprimer", headers=HTMX)

    assert reponse.status_code == 200
    page = reponse.get_data(as_text=True)
    assert page.startswith('<div class="historique" id="historique" hx-swap-oob="true">')
    assert "Retrait DAB" in page
    assert "Boulangerie" not in page
    assert '<p class="solde modifie" id="solde" hx-swap-oob="true">' in page
    assert f"10,00{INSECABLE}€" in page
    assert solde(app, id) == 1000


def test_htmx_suppression_mouvement_refusee_renvoie_l_historique(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "50")
    poster_mouvement(client, id, "retrait", "40")
    with app.app_context():
        ajout = db.lister_mouvements(id)[1]["id"]

    reponse = client.post(f"/mouvements/{ajout}/supprimer", headers=HTMX)

    assert reponse.status_code == 422
    page = reponse.get_data(as_text=True)
    assert page.startswith('<div class="historique" id="historique" hx-swap-oob="true">')
    assert page.count("Suppression impossible") == 1
    assert 'id="solde"' not in page


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


# --- Interface (phase 4) ---


def test_accueil_vide_propose_de_creer_une_enveloppe(client):
    page = client.get("/").get_data(as_text=True)

    assert "Aucune enveloppe pour l'instant." in page
    assert 'data-modale="modale-enveloppe">Créer une enveloppe</a>' in page
    assert "carte-total" not in page


def test_accueil_n_affiche_que_les_sections_non_vides(app, client):
    creer_enveloppe(app, "Courses", "physique")

    page = client.get("/").get_data(as_text=True)

    assert '<h2 class="titre-section">Physique</h2>' in page
    assert '<h2 class="titre-section">Dématérialisé</h2>' not in page
    assert "Aucune enveloppe" not in page


def test_historique_regroupe_par_jour(app, client):
    id = creer_enveloppe(app)
    aujourdhui = date.today()
    hier = aujourdhui - timedelta(days=1)
    poster_mouvement(client, id, "ajout", "10", motif="Ancien", date="2025-12-31")
    poster_mouvement(client, id, "ajout", "10", motif="Veille", date=hier.isoformat())
    poster_mouvement(client, id, "ajout", "10", motif="Du jour", date=aujourdhui.isoformat())

    page = client.get(f"/enveloppes/{id}").get_data(as_text=True)

    ordre_attendu = [
        "<h3>Aujourd&#39;hui</h3>",
        '<span class="motif">Du jour</span>',
        "<h3>Hier</h3>",
        '<span class="motif">Veille</span>',
        "<h3>Mercredi 31 décembre 2025</h3>",
        '<span class="motif">Ancien</span>',
    ]
    positions = [page.index(repere) for repere in ordre_attendu]
    assert positions == sorted(positions)


def test_historique_vide(app, client):
    id = creer_enveloppe(app)

    assert "Aucun mouvement pour l'instant" in client.get(f"/enveloppes/{id}").get_data(as_text=True)


@pytest.mark.parametrize(
    ("jour", "libelle"),
    [
        ("2026-09-30", "Aujourd'hui"),
        ("2026-09-29", "Hier"),
        ("2026-09-28", "Lundi 28 septembre"),
        ("2026-10-01", "Jeudi 1er octobre"),
        ("2025-12-31", "Mercredi 31 décembre 2025"),
    ],
)
def test_libelle_jour(jour, libelle):
    assert libelle_jour(jour, aujourdhui=date(2026, 9, 30)) == libelle


def test_page_enveloppe_propose_motifs_et_sens(app, client):
    id = creer_enveloppe(app)
    poster_mouvement(client, id, "ajout", "10", motif="Salaire")

    page = client.get(f"/enveloppes/{id}").get_data(as_text=True)

    assert '<option value="Salaire">' in page
    assert 'data-modale="modale-mouvement" data-sens="ajout"' in page
    assert 'data-modale="modale-mouvement" data-sens="retrait"' in page
    assert 'inputmode="decimal" autocomplete="off"' in page


def test_modale_ouverte_quand_le_formulaire_sans_htmx_contient_une_erreur(app, client):
    id = creer_enveloppe(app)

    page_normale = client.get(f"/enveloppes/{id}").get_data(as_text=True)
    page_erreur = poster_mouvement(client, id, "retrait", "5").get_data(as_text=True)

    assert '<dialog id="modale-mouvement" aria-labelledby="titre-modale-mouvement">' in page_normale
    assert '<dialog id="modale-mouvement" aria-labelledby="titre-modale-mouvement" open>' in page_erreur


# --- PWA (phase 5) ---


def fichier_statique(client, nom):
    with client.get(f"/static/{nom}") as reponse:
        assert reponse.status_code == 200
        return reponse.get_data()


def test_manifest_et_icones(client):
    manifest = json.loads(fichier_statique(client, "manifest.json"))

    assert manifest["name"] == "Enveloppes"
    assert manifest["short_name"]
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    icones = {(icone["sizes"], icone.get("purpose", "any")) for icone in manifest["icons"]}
    assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= icones
    for icone in manifest["icons"]:
        image = fichier_statique(client, icone["src"])
        assert image[:8] == b"\x89PNG\r\n\x1a\n"
        largeur, hauteur = struct.unpack(">II", image[16:24])
        assert f"{largeur}x{hauteur}" == icone["sizes"]


def test_pages_declarent_la_pwa(client):
    page = client.get("/").get_data(as_text=True)

    assert '<link rel="manifest" href="/static/manifest.json">' in page
    assert '<link rel="apple-touch-icon" href="/static/icones/icon-192.png">' in page
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in page


def test_couleurs_pwa_identiques_au_fond_du_css(client):
    manifest = json.loads(fichier_statique(client, "manifest.json"))
    css = fichier_statique(client, "style.css").decode()
    fond_clair, fond_sombre = re.findall(r"--fond: (#[0-9a-f]{6});", css)
    page = client.get("/").get_data(as_text=True)

    assert manifest["background_color"] == manifest["theme_color"] == fond_clair
    assert f'name="theme-color" content="{fond_clair}" media="(prefers-color-scheme: light)"' in page
    assert f'name="theme-color" content="{fond_sombre}" media="(prefers-color-scheme: dark)"' in page
