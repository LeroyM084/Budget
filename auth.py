import hashlib
import math
import os
import secrets
import time
from datetime import timedelta

import click
from flask import g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db

ECHECS_MAX = 5
FENETRE = 15 * 60  # secondes
LONGUEUR_MIN_MOT_DE_PASSE = 12

# Vérifiée quand l'identifiant n'existe pas : la réponse prend le même temps
# et ne révèle donc pas quels comptes existent.
EMPREINTE_FACTICE = generate_password_hash(secrets.token_hex(16))


def init_app(app):
    with app.app_context():
        app.secret_key = db.cle_secrete()
    app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_SAMESITE="Lax",
        # Cookie envoyé en HTTPS seulement : à activer derrière le proxy (voir Dockerfile).
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
    )

    @app.before_request
    def exiger_connexion():
        if request.endpoint in ("static", "sante"):
            return None
        g.utilisateur = utilisateur_connecte()
        if g.utilisateur is None and request.endpoint != "connexion":
            if request.headers.get("HX-Request") == "true":
                return "", 401, {"HX-Redirect": url_for("connexion")}
            return redirect(url_for("connexion"), code=303)
        return None

    @app.route("/connexion", methods=["GET", "POST"])
    def connexion():
        if request.method == "GET":
            if g.utilisateur:
                return redirect(url_for("accueil"), code=303)
            return render_template("connexion.html", nom="", erreur=None)

        nom = request.form.get("nom", "").strip()[:100]
        mot_de_passe = request.form.get("mot_de_passe", "")[:1000]
        ip = adresse_client()

        attente = attente_avant_essai(ip, nom)
        if attente:
            erreur = f"Trop de tentatives. Réessayez dans {math.ceil(attente / 60)} min."
            return (
                render_template("connexion.html", nom=nom, erreur=erreur),
                429,
                {"Retry-After": str(attente)},
            )

        utilisateur = db.get_utilisateur_par_nom(nom)
        empreinte = utilisateur["mot_de_passe"] if utilisateur else EMPREINTE_FACTICE
        if not check_password_hash(empreinte, mot_de_passe) or utilisateur is None:
            instant = maintenant()
            db.enregistrer_echec(ip, nom.casefold(), instant, instant - FENETRE)
            erreur = "Identifiant ou mot de passe incorrect."
            return render_template("connexion.html", nom=nom, erreur=erreur), 401

        session.clear()
        session.permanent = True
        session["utilisateur"] = utilisateur["id"]
        session["empreinte"] = empreinte_de_session(utilisateur)
        return redirect(url_for("accueil"), code=303)

    @app.post("/deconnexion")
    def deconnexion():
        session.clear()
        return redirect(url_for("connexion"), code=303)

    @app.cli.command("utilisateur")
    @click.argument("nom")
    def commande_utilisateur(nom):
        """Crée l'utilisateur NOM, ou change son mot de passe. Seul moyen de créer un compte."""
        nom = nom.strip()
        if not 0 < len(nom) <= 50:
            raise click.BadParameter("de 1 à 50 caractères.", param_hint="NOM")
        mot_de_passe = click.prompt(
            "Mot de passe", hide_input=True, confirmation_prompt="Confirmer le mot de passe"
        )
        if len(mot_de_passe) < LONGUEUR_MIN_MOT_DE_PASSE:
            raise click.ClickException(
                f"Le mot de passe doit faire au moins {LONGUEUR_MIN_MOT_DE_PASSE} caractères."
            )
        if db.enregistrer_utilisateur(nom, generate_password_hash(mot_de_passe)):
            click.echo(f"Utilisateur « {nom} » créé.")
        else:
            click.echo(f"Mot de passe de « {nom} » changé : ses sessions ouvertes sont fermées.")


def utilisateur_connecte():
    """Utilisateur de la session, ou None si la session est absente ou périmée
    (compte supprimé, mot de passe changé depuis la connexion)."""
    identifiant = session.get("utilisateur")
    if identifiant is None:
        return None
    utilisateur = db.get_utilisateur(identifiant)
    if utilisateur is None or session.get("empreinte") != empreinte_de_session(utilisateur):
        session.clear()
        return None
    return utilisateur


def empreinte_de_session(utilisateur):
    """Dérivée du mot de passe enregistré : en changer invalide les sessions existantes."""
    return hashlib.sha256(utilisateur["mot_de_passe"].encode()).hexdigest()[:16]


def adresse_client():
    # Derrière Cloudflare, l'adresse du visiteur est dans CF-Connecting-IP ;
    # sinon, c'est celle du proxy ou du client direct.
    return (request.headers.get("CF-Connecting-IP") or request.remote_addr or "?")[:64]


def attente_avant_essai(ip, nom):
    """Secondes à attendre avant un nouvel essai : l'IP comme l'identifiant ont droit
    à ECHECS_MAX échecs par période glissante de FENETRE secondes."""
    instant = maintenant()
    attente = 0
    for echecs in db.echecs_recents(ip, nom.casefold(), instant - FENETRE):
        if len(echecs) >= ECHECS_MAX:
            attente = max(attente, echecs[ECHECS_MAX - 1] + FENETRE - instant)
    return attente


def maintenant():
    return int(time.time())
