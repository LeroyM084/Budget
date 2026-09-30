from flask import Flask

import db
from argent import formater_euros


def create_app():
    app = Flask(__name__)
    db.init_app(app)
    app.add_template_filter(formater_euros, "euros")

    @app.get("/")
    def accueil():
        return "OK"

    return app
