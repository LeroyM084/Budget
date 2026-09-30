from datetime import date, timedelta

from flask import Flask, abort, redirect, render_template, request, url_for

import db
from argent import formater_euros, parser_montant

TYPES = {"physique": "Physique", "demat": "Dématérialisé"}
MOTIF_MAX = 100
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MOIS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


def create_app():
    app = Flask(__name__)
    db.init_app(app)
    app.add_template_filter(formater_euros, "euros")
    app.add_template_filter(libelle_jour)
    app.jinja_env.globals["TYPES"] = TYPES

    @app.before_request
    def refuser_ecritures_intersites():
        """Protection CSRF : refuse les POST envoyés depuis la page d'un autre site.

        Les navigateurs récents indiquent la provenance dans Sec-Fetch-Site ;
        sans cet en-tête (curl, tests), la requête est acceptée.
        """
        provenance = request.headers.get("Sec-Fetch-Site")
        if request.method == "POST" and provenance not in (None, "same-origin", "none"):
            abort(403)

    @app.after_request
    def interdire_iframes(reponse):
        reponse.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return reponse

    @app.get("/")
    def accueil():
        return page_accueil()

    @app.post("/enveloppes")
    def creer_enveloppe():
        valeurs = {
            "nom": request.form.get("nom", "").strip(),
            "type": request.form.get("type", ""),
        }
        erreurs = {}
        if erreur := valider_nom(valeurs["nom"]):
            erreurs["nom"] = erreur
        if valeurs["type"] not in TYPES:
            erreurs["type"] = "Choisissez un type."
        if erreurs:
            if requete_htmx():
                return fragment("_form_enveloppe.html", valeurs=valeurs, erreurs=erreurs), 422
            return page_accueil(valeurs, erreurs), 422

        db.creer_enveloppe(valeurs["nom"], valeurs["type"])
        if requete_htmx():
            return fragment("_form_enveloppe.html", valeurs={}, erreurs={}) + fragment(
                "_tableau.html", enveloppes=db.lister_enveloppes(), totaux=db.totaux()
            )
        return redirect(url_for("accueil"), code=303)

    @app.get("/enveloppes/<int:id>")
    def voir_enveloppe(id):
        return page_enveloppe(trouver_enveloppe(id))

    @app.post("/enveloppes/<int:id>/mouvements")
    def ajouter_mouvement(id):
        enveloppe = trouver_enveloppe(id)
        valeurs = {
            champ: request.form.get(champ, "").strip()
            for champ in ("sens", "montant", "motif", "date")
        }
        erreurs = {}

        if valeurs["sens"] not in ("ajout", "retrait"):
            erreurs["sens"] = "Choisissez « Ajouter » ou « Retirer »."

        try:
            centimes = parser_montant(valeurs["montant"])
        except ValueError as erreur:
            erreurs["montant"] = str(erreur)
        else:
            retrait = valeurs["sens"] == "retrait"
            if retrait and decouvert_interdit(enveloppe, enveloppe["solde"] - centimes):
                solde = formater_euros(enveloppe["solde"])
                erreurs["montant"] = f"Solde insuffisant (solde actuel : {solde})"

        if not valeurs["motif"]:
            erreurs["motif"] = "Le motif est obligatoire."
        elif len(valeurs["motif"]) > MOTIF_MAX:
            erreurs["motif"] = f"Le motif ne doit pas dépasser {MOTIF_MAX} caractères."

        try:
            date_operation = date.fromisoformat(valeurs["date"])
        except ValueError:
            erreurs["date"] = "Saisissez une date valide."

        if erreurs:
            if requete_htmx():
                return fragment_form_mouvement(enveloppe, valeurs, erreurs), 422
            return page_enveloppe(enveloppe, valeurs, erreurs), 422

        montant = centimes if valeurs["sens"] == "ajout" else -centimes
        mouvement_id = db.ajouter_mouvement(id, montant, valeurs["motif"], date_operation.isoformat())
        if requete_htmx():
            enveloppe = db.get_enveloppe(id)
            return (
                fragment_historique(id, nouveau_id=mouvement_id)
                + fragment("_solde.html", enveloppe=enveloppe)
                + fragment_form_mouvement(enveloppe, {"sens": valeurs["sens"]})
            )
        return redirect(url_for("voir_enveloppe", id=id), code=303)

    @app.post("/enveloppes/<int:id>/renommer")
    def renommer_enveloppe(id):
        enveloppe = trouver_enveloppe(id)
        nom = request.form.get("nom", "").strip()
        if erreur := valider_nom(nom, sauf_id=id):
            return page_enveloppe(enveloppe, {"nom": nom}, {"nom": erreur}), 422

        db.renommer_enveloppe(id, nom)
        return redirect(url_for("voir_enveloppe", id=id), code=303)

    @app.post("/enveloppes/<int:id>/supprimer")
    def supprimer_enveloppe(id):
        trouver_enveloppe(id)
        db.supprimer_enveloppe(id)
        if requete_htmx():
            # HX-Location navigue vers l'accueil en AJAX, là où HX-Redirect recharge la page.
            return "", {"HX-Location": url_for("accueil")}
        return redirect(url_for("accueil"), code=303)

    @app.post("/mouvements/<int:id>/supprimer")
    def supprimer_mouvement(id):
        mouvement = db.get_mouvement(id)
        if mouvement is None:
            abort(404)
        enveloppe = db.get_enveloppe(mouvement["enveloppe_id"])
        nouveau_solde = enveloppe["solde"] - mouvement["montant"]
        if decouvert_interdit(enveloppe, nouveau_solde):
            erreur = f"Suppression impossible : le solde passerait à {formater_euros(nouveau_solde)}."
            erreurs = {f"mouvement-{id}": erreur}
            if requete_htmx():
                return fragment_historique(enveloppe["id"], erreurs), 422
            return page_enveloppe(enveloppe, erreurs=erreurs), 422

        enveloppe_id = db.supprimer_mouvement(id)
        if requete_htmx():
            return fragment_historique(enveloppe_id) + fragment(
                "_solde.html", enveloppe=db.get_enveloppe(enveloppe_id)
            )
        return redirect(url_for("voir_enveloppe", id=enveloppe_id), code=303)

    return app


def requete_htmx():
    """Requête hx-post d'un fragment ; une navigation hx-boost attend une page complète."""
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"


def fragment(template, **contexte):
    """Fragment qui remplace, côté client, l'élément portant le même id (swap out-of-band)."""
    return render_template(template, oob=True, **contexte)


def fragment_historique(enveloppe_id, erreurs=None, nouveau_id=None):
    return fragment(
        "_historique.html",
        mouvements=db.lister_mouvements(enveloppe_id),
        erreurs=erreurs or {},
        nouveau_id=nouveau_id,
    )


def fragment_form_mouvement(enveloppe, valeurs, erreurs=None):
    return fragment(
        "_form_mouvement.html",
        enveloppe=enveloppe,
        valeurs={"date": date.today().isoformat(), **valeurs},
        erreurs=erreurs or {},
        motifs=db.motifs_recents(),
    )


def libelle_jour(date_iso, aujourdhui=None):
    """« Aujourd'hui », « Hier », sinon la date en toutes lettres : « Lundi 28 septembre »."""
    jour = date.fromisoformat(date_iso)
    aujourdhui = aujourdhui or date.today()
    if jour == aujourdhui:
        return "Aujourd'hui"
    if jour == aujourdhui - timedelta(days=1):
        return "Hier"
    numero = "1er" if jour.day == 1 else jour.day
    libelle = f"{JOURS[jour.weekday()]} {numero} {MOIS[jour.month - 1]}"
    if jour.year != aujourdhui.year:
        libelle += f" {jour.year}"
    return libelle.capitalize()


def trouver_enveloppe(id):
    enveloppe = db.get_enveloppe(id)
    if enveloppe is None:
        abort(404)
    return enveloppe


def decouvert_interdit(enveloppe, nouveau_solde):
    """Seules les enveloppes démat peuvent avoir un solde négatif."""
    return enveloppe["type"] == "physique" and nouveau_solde < 0


def valider_nom(nom, sauf_id=None):
    """Message d'erreur pour un nom d'enveloppe, ou None s'il est valide."""
    if not nom:
        return "Le nom est obligatoire."
    for enveloppe in db.lister_enveloppes():
        if enveloppe["id"] != sauf_id and enveloppe["nom"].casefold() == nom.casefold():
            return "Une enveloppe porte déjà ce nom."
    return None


def page_accueil(valeurs=None, erreurs=None):
    return render_template(
        "index.html",
        enveloppes=db.lister_enveloppes(),
        totaux=db.totaux(),
        valeurs=valeurs or {},
        erreurs=erreurs or {},
    )


def page_enveloppe(enveloppe, valeurs=None, erreurs=None):
    valeurs = {"date": date.today().isoformat(), "nom": enveloppe["nom"], **(valeurs or {})}
    return render_template(
        "enveloppe.html",
        enveloppe=enveloppe,
        mouvements=db.lister_mouvements(enveloppe["id"]),
        motifs=db.motifs_recents(),
        valeurs=valeurs,
        erreurs=erreurs or {},
    )
