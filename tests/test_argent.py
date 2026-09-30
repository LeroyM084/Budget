import pytest

from argent import formater_euros, parser_montant

MOINS = "\u2212"
FINE = "\u202f"
INSECABLE = "\u00a0"


@pytest.mark.parametrize(
    ("texte", "centimes"),
    [
        ("12", 1200),
        ("12.5", 1250),
        ("12,5", 1250),
        ("12,50", 1250),
        ("12.50", 1250),
        ("0,01", 1),
        ("1 234,56", 123456),
        (f"1{INSECABLE}234,56", 123456),
        (f"1{FINE}234,56", 123456),
        ("  42  ", 4200),
        ("007", 700),
        ("1000000", 100_000_000),
        ("1 000 000,00", 100_000_000),
    ],
)
def test_parser_montant_valide(texte, centimes):
    assert parser_montant(texte) == centimes


@pytest.mark.parametrize(
    ("texte", "message"),
    [
        ("", "obligatoire"),
        ("   ", "obligatoire"),
        (INSECABLE, "obligatoire"),
        ("-5", "négatif"),
        (f"{MOINS}5", "négatif"),
        ("-0,01", "négatif"),
        ("0", "supérieur à zéro"),
        ("0,00", "supérieur à zéro"),
        ("12,345", "2 décimales"),
        ("0,001", "2 décimales"),
        ("abc", "nombre"),
        ("12a", "nombre"),
        ("12,5,0", "nombre"),
        ("1.234,56", "nombre"),
        ("-", "nombre"),
        ("1e3", "nombre"),
        ("1_000", "nombre"),
        ("NaN", "nombre"),
        ("Infinity", "nombre"),
        ("\u0661\u0662", "nombre"),  # chiffres arabes-indiens, acceptés par Decimal
        ("1000000,01", "1 000 000"),
        ("2 000 000", "1 000 000"),
    ],
)
def test_parser_montant_invalide(texte, message):
    with pytest.raises(ValueError, match=message):
        parser_montant(texte)


@pytest.mark.parametrize(
    ("centimes", "attendu"),
    [
        (0, f"0,00{INSECABLE}€"),
        (1, f"0,01{INSECABLE}€"),
        (1250, f"12,50{INSECABLE}€"),
        (123456, f"1{FINE}234,56{INSECABLE}€"),
        (100_000_000, f"1{FINE}000{FINE}000,00{INSECABLE}€"),
        (-5, f"{MOINS}0,05{INSECABLE}€"),
        (-123456, f"{MOINS}1{FINE}234,56{INSECABLE}€"),
    ],
)
def test_formater_euros(centimes, attendu):
    assert formater_euros(centimes) == attendu
