from datetime import date

from flask import Flask, abort, redirect, render_template, request, url_for

import db
from argent import formater_euros, parser_montant

TYPES = {"physique": "Physique", "demat": "Dématérialisé"}
MOTIF_MAX = 100


def create_app():
    app = Flask(__name__)
    db.init_app(app)
    app.add_template_filter(formater_euros, "euros")
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
            return page_accueil(valeurs, erreurs), 422

        db.creer_enveloppe(valeurs["nom"], valeurs["type"])
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
            return page_enveloppe(enveloppe, valeurs, erreurs), 422

        montant = centimes if valeurs["sens"] == "ajout" else -centimes
        db.ajouter_mouvement(id, montant, valeurs["motif"], date_operation.isoformat())
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
            return page_enveloppe(enveloppe, erreurs={f"mouvement-{id}": erreur}), 422

        enveloppe_id = db.supprimer_mouvement(id)
        return redirect(url_for("voir_enveloppe", id=enveloppe_id), code=303)

    return app


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
        valeurs=valeurs,
        erreurs=erreurs or {},
    )
