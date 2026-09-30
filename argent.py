import re
from decimal import Decimal

MONTANT_MAX = Decimal("1000000")
FORMAT_MONTANT = re.compile(r"[0-9]+(?:\.[0-9]+)?")

SIGNE_MOINS = "−"
ESPACE_FINE = " "  # séparateur des milliers, insécable
ESPACE_INSECABLE = " "


def parser_montant(texte):
    """Convertit une saisie (« 12 », « 12,5 », « 1 234,56 »…) en centimes positifs.

    Lève ValueError avec un message affichable tel quel si la saisie est invalide.
    """
    saisie = "".join(texte.split()).replace(",", ".")
    if not saisie:
        raise ValueError("Le montant est obligatoire.")

    negatif = saisie[0] in ("-", SIGNE_MOINS)
    if negatif:
        saisie = saisie[1:]
    if not FORMAT_MONTANT.fullmatch(saisie):
        raise ValueError("Le montant doit être un nombre, par exemple 12,50.")
    if negatif:
        raise ValueError("Le montant ne peut pas être négatif.")
    if len(saisie.partition(".")[2]) > 2:
        raise ValueError("Le montant ne peut pas avoir plus de 2 décimales.")

    montant = Decimal(saisie)
    if montant == 0:
        raise ValueError("Le montant doit être supérieur à zéro.")
    if montant > MONTANT_MAX:
        raise ValueError("Le montant ne peut pas dépasser 1 000 000 €.")
    return int(montant * 100)


def formater_euros(centimes):
    """Formate des centimes à la française : 123456 → « 1 234,56 € »."""
    signe = SIGNE_MOINS if centimes < 0 else ""
    euros, reste = divmod(abs(centimes), 100)
    milliers = f"{euros:,}".replace(",", ESPACE_FINE)
    return f"{signe}{milliers},{reste:02d}{ESPACE_INSECABLE}€"
