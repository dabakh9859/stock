"""Profil de boutique — ce qui adapte l'application au métier du commerçant.

L'application est née dans une boutique de téléphonie : identifiant unique par
appareil, atelier de réparation, état « venant ». Rien de tout cela n'a de sens
dans une supérette, où l'on compte des cartons et surveille des dates de
péremption, ni dans une boutique de prêt-à-porter, où le même modèle existe en
six tailles et quatre couleurs.

Un profil décrit ces différences une seule fois, de façon déclarative :

  * `tracage`    — comment on suit une unité de stock (voir TRACAGES) ;
  * `libelles`   — les mots de l'écran (« IMEI » ou « Taille / Couleur ») ;
  * `modules`    — les parties de l'application qui concernent ce métier ;
  * `categories` — un catalogue de départ, avec ses attributs.

Le profil ne remplace aucun réglage existant : il les *présélectionne*. Les
catégories, les attributs et les conditions restent modifiables un par un dans
Paramètres ; `appliquer()` n'ajoute que ce qui manque et ne supprime jamais rien
(un commerçant qui change de profil garde son stock et son historique).

Lu par : l'écran Paramètres, les gabarits Jinja (globales `shop_*`), et l'outil
`configurer_boutique` de l'assistant — c'est ainsi que l'IA configure la
boutique à la fin de l'entretien d'installation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CLE_PROFIL = "SHOP_PROFILE"

# Profil neutre : celui qui ne présume rien du métier. C'est le repli quand la
# boutique n'a pas encore répondu aux questions d'installation.
PROFIL_DEFAUT = "general"


# ---------------------------------------------------------------------------
# Traçages
# ---------------------------------------------------------------------------
# Comment l'application compte une unité de stock. C'est le choix structurant :
# il décide si le formulaire produit réclame un identifiant, une grille de
# tailles, ou juste un nombre.
TRACAGES = {
    "serie": {
        "libelle": "Un identifiant unique par article",
        "explication": (
            "Chaque exemplaire est distinct et se suit à la trace : IMEI d'un "
            "téléphone, numéro de série d'un ordinateur. Le stock est le nombre "
            "d'identifiants encore disponibles."
        ),
        "serial_par_defaut": True,
        "variantes_comptees": False,
    },
    "variantes": {
        "libelle": "Des déclinaisons avec un stock chacune",
        "explication": (
            "Le même modèle existe en plusieurs versions, et chaque combinaison "
            "a son propre stock : 12 t-shirts rouges en taille M et 3 en L, ou "
            "8 flacons de 50 ml et 2 de 100 ml."
        ),
        "serial_par_defaut": False,
        "variantes_comptees": True,
    },
    "lot": {
        "libelle": "Des quantités avec dates de péremption",
        "explication": (
            "On compte des unités interchangeables, mais il faut savoir ce qui "
            "périme et quand : lait, yaourts, conserves, cosmétiques."
        ),
        "serial_par_defaut": False,
        "variantes_comptees": False,
    },
    "simple": {
        "libelle": "Une simple quantité",
        "explication": (
            "Un produit, un nombre. Ni identifiant, ni déclinaison, ni date "
            "limite : le suivi le plus léger."
        ),
        "serial_par_defaut": False,
        "variantes_comptees": False,
    },
}


# ---------------------------------------------------------------------------
# Libellés et modules — valeurs de repli communes à tous les profils
# ---------------------------------------------------------------------------
LIBELLES_DEFAUT = {
    "identifiant": "Numéro de série",
    "identifiant_court": "N° série",
    "identifiant_aide": "Identifiant unique de l'exemplaire, s'il en a un.",
    "variante": "Déclinaison",
    "variantes": "Déclinaisons",
    "produit": "Produit",
    "produits": "Produits",
    "unite": "pièce",
    "unites": "pièces",
}

# Chaque clé correspond à une partie de l'application qu'un profil peut allumer
# ou éteindre. Le plan d'abonnement garde le dernier mot : un module allumé ici
# mais absent du plan reste inaccessible (voir `modules_actifs`).
MODULES_DEFAUT = {
    "atelier": False,        # réparations / SAV
    "garantie": False,       # suivi et impression de garanties
    "peremption": False,     # dates limites et alertes
    "declinaisons": False,   # grille taille / couleur au formulaire produit
    "identifiants": False,   # saisie d'un IMEI / n° de série
    "etats": False,          # neuf / occasion / venant
    "unites": False,         # kg, litre, sachet… au lieu de la pièce
}

# Correspondance module → fonctionnalité du plan d'abonnement. Sert à ne pas
# promettre au commerçant un module que son abonnement ne couvre pas.
MODULE_FONCTIONNALITE = {
    "atelier": "maintenance",
    "garantie": "maintenance",
}

# Comment chaque module se présente au commerçant. Décrit ici, et non dans le
# gabarit : l'écran Paramètres et l'API doivent en dire la même chose.
LIBELLES_MODULES = {
    "atelier": ("Atelier et réparations",
                "Le menu « Maintenance » : fiches de réparation, suivi, tickets."),
    "garantie": ("Garanties",
                 "Durée de garantie sur les factures et certificat imprimable."),
    "peremption": ("Dates de péremption",
                   "Date limite par article et alerte avant l'échéance."),
    "declinaisons": ("Tailles, couleurs, contenances",
                     "Grille de déclinaisons au formulaire produit, avec un "
                     "stock par combinaison."),
    "identifiants": ("Numéros de série",
                     "Saisie d'un identifiant unique par exemplaire (IMEI, "
                     "n° de série)."),
    "etats": ("Neuf, occasion, venant",
              "État de l'article sur la fiche produit et sur les factures."),
    "unites": ("Unités de mesure",
               "Vente au kilo, au litre ou au sachet plutôt qu'à la pièce."),
}


# ---------------------------------------------------------------------------
# Les profils
# ---------------------------------------------------------------------------
# `exemples` et `mots_cles` ne servent pas à l'affichage : ils donnent à l'IA
# d'installation de quoi reconnaître le métier dans les mots du commerçant
# (« je vends des pagnes », « j'ai une boutique de téléphones »).
PROFILS: Dict[str, dict] = {

    "telephonie": {
        "libelle": "Téléphonie, informatique & électronique",
        "resume": (
            "Des appareils suivis un par un, avec état, garantie et atelier de "
            "réparation."
        ),
        "exemples": [
            "boutique de téléphones", "vente d'ordinateurs",
            "accessoires et réparation mobile", "matériel électronique",
        ],
        "mots_cles": [
            "telephone", "portable", "smartphone", "iphone", "samsung",
            "ordinateur", "informatique", "electronique", "imei", "reparation",
            "tablette", "accessoire telephone",
        ],
        "tracage": "serie",
        "libelles": {
            "identifiant": "IMEI / Numéro de série",
            "identifiant_court": "IMEI",
            "identifiant_aide": (
                "IMEI pour un téléphone, numéro de série pour le reste. "
                "Un appareil = une ligne."
            ),
            "variante": "Appareil",
            "variantes": "Appareils",
        },
        "modules": {
            "atelier": True, "garantie": True, "identifiants": True,
            "etats": True,
        },
        "conditions": ["neuf", "occasion", "venant"],
        "categories": [
            {"nom": "Smartphones", "variantes": True, "attributs": [
                {"nom": "Couleur", "valeurs": ["Noir", "Blanc", "Bleu", "Vert",
                                               "Argent", "Or"]},
                {"nom": "Stockage", "valeurs": ["64 Go", "128 Go", "256 Go",
                                                "512 Go", "1 To"]},
            ]},
            {"nom": "Ordinateurs portables", "variantes": True, "attributs": [
                {"nom": "Mémoire", "valeurs": ["4 Go", "8 Go", "16 Go", "32 Go"]},
                {"nom": "Disque", "valeurs": ["256 Go SSD", "512 Go SSD",
                                              "1 To SSD", "1 To HDD"]},
            ]},
            {"nom": "Tablettes", "variantes": True},
            {"nom": "Montres connectées", "variantes": True},
            {"nom": "Accessoires", "variantes": False},
        ],
    },

    "mode": {
        "libelle": "Mode, textile & chaussures",
        "resume": (
            "Un modèle décliné en tailles et couleurs, chaque combinaison avec "
            "son propre stock."
        ),
        "exemples": [
            "boutique de prêt-à-porter", "vente de pagnes et tissus",
            "chaussures", "friperie", "maroquinerie",
        ],
        "mots_cles": [
            "vetement", "habit", "pret-a-porter", "textile", "tissu", "pagne",
            "wax", "bazin", "chaussure", "basket", "sac", "friperie", "mode",
            "couture", "boubou", "taille", "pointure",
        ],
        "tracage": "variantes",
        "libelles": {
            "identifiant": "Référence de la déclinaison",
            "identifiant_court": "Référence",
            "identifiant_aide": (
                "Générée à partir de la taille et de la couleur. À ne remplir "
                "que si vous avez vos propres références."
            ),
            "variante": "Taille / Couleur",
            "variantes": "Tailles & couleurs",
            "produit": "Modèle",
            "produits": "Modèles",
        },
        "modules": {"declinaisons": True, "etats": True},
        "conditions": ["neuf", "friperie"],
        "categories": [
            {"nom": "Vêtements homme", "variantes": True, "attributs": [
                {"nom": "Taille", "valeurs": ["XS", "S", "M", "L", "XL", "XXL",
                                              "3XL"]},
                {"nom": "Couleur", "valeurs": ["Noir", "Blanc", "Bleu", "Rouge",
                                               "Vert", "Jaune", "Beige",
                                               "Gris", "Marron"]},
            ]},
            {"nom": "Vêtements femme", "variantes": True, "attributs": [
                {"nom": "Taille", "valeurs": ["34", "36", "38", "40", "42",
                                              "44", "46", "48"]},
                {"nom": "Couleur", "valeurs": ["Noir", "Blanc", "Bleu", "Rouge",
                                               "Vert", "Jaune", "Rose",
                                               "Beige", "Doré"]},
            ]},
            {"nom": "Enfants", "variantes": True, "attributs": [
                {"nom": "Âge", "valeurs": ["0-6 mois", "6-12 mois", "1-2 ans",
                                           "3-4 ans", "5-6 ans", "7-8 ans",
                                           "9-10 ans", "11-12 ans"]},
                {"nom": "Couleur", "valeurs": ["Noir", "Blanc", "Bleu", "Rouge",
                                               "Vert", "Rose", "Jaune"]},
            ]},
            {"nom": "Chaussures", "variantes": True, "attributs": [
                {"nom": "Pointure", "valeurs": ["36", "37", "38", "39", "40",
                                                "41", "42", "43", "44", "45",
                                                "46"]},
                {"nom": "Couleur", "valeurs": ["Noir", "Blanc", "Marron",
                                               "Bleu", "Rouge", "Beige"]},
            ]},
            {"nom": "Tissus & pagnes", "variantes": True, "attributs": [
                {"nom": "Longueur", "valeurs": ["3 yards", "6 yards",
                                                "12 yards", "Au mètre"]},
                {"nom": "Couleur", "valeurs": ["Multicolore", "Bleu", "Rouge",
                                               "Vert", "Jaune", "Blanc",
                                               "Noir"]},
            ]},
            {"nom": "Sacs & maroquinerie", "variantes": True, "attributs": [
                {"nom": "Couleur", "valeurs": ["Noir", "Marron", "Beige",
                                               "Rouge", "Doré"]},
            ]},
            {"nom": "Accessoires", "variantes": False},
        ],
    },

    "alimentation": {
        "libelle": "Alimentation, supérette & boissons",
        "resume": (
            "Des quantités à l'unité ou au poids, avec dates de péremption et "
            "alertes de réassort."
        ),
        "exemples": [
            "supérette", "boutique de quartier", "épicerie",
            "dépôt de boissons", "produits frais",
        ],
        "mots_cles": [
            "alimentation", "superette", "supermarche", "epicerie", "boutique",
            "nourriture", "boisson", "riz", "huile", "sucre", "lait", "pain",
            "frais", "surgele", "peremption", "kilo", "litre", "sachet",
            "conserve", "denree",
        ],
        "tracage": "lot",
        "libelles": {
            "identifiant": "Numéro de lot",
            "identifiant_court": "Lot",
            "identifiant_aide": (
                "Numéro de lot du fabricant, utile pour un rappel produit."
            ),
            "variante": "Lot",
            "variantes": "Lots",
            "unite": "unité",
            "unites": "unités",
        },
        "modules": {"peremption": True, "unites": True},
        "conditions": ["neuf"],
        "categories": [
            {"nom": "Épicerie sèche", "variantes": False, "attributs": [
                {"nom": "Conditionnement", "valeurs": ["Sachet", "Paquet",
                                                       "Sac 5 kg", "Sac 25 kg",
                                                       "Sac 50 kg", "Au kilo"]},
            ]},
            {"nom": "Boissons", "variantes": False, "attributs": [
                {"nom": "Contenance", "valeurs": ["33 cl", "50 cl", "1 L",
                                                  "1,5 L", "5 L", "Casier"]},
            ]},
            {"nom": "Produits laitiers", "variantes": False},
            {"nom": "Conserves", "variantes": False},
            {"nom": "Fruits & légumes", "variantes": False, "attributs": [
                {"nom": "Conditionnement", "valeurs": ["Au kilo", "À la pièce",
                                                       "Cagette", "Carton"]},
            ]},
            {"nom": "Surgelés", "variantes": False},
            {"nom": "Pain & viennoiserie", "variantes": False},
            {"nom": "Hygiène & entretien", "variantes": False},
        ],
    },

    "cosmetique": {
        "libelle": "Cosmétique, parfumerie & beauté",
        "resume": (
            "Des références déclinées en contenances et teintes, avec dates "
            "limites d'utilisation."
        ),
        "exemples": [
            "institut de beauté", "parfumerie", "produits capillaires",
            "vente de perruques", "cosmétiques",
        ],
        "mots_cles": [
            "cosmetique", "beaute", "parfum", "parfumerie", "creme", "savon",
            "cheveux", "perruque", "meche", "tissage", "maquillage", "soin",
            "institut", "coiffure",
        ],
        "tracage": "variantes",
        "libelles": {
            "identifiant": "Référence de la déclinaison",
            "identifiant_court": "Référence",
            "variante": "Contenance / Teinte",
            "variantes": "Déclinaisons",
        },
        "modules": {"declinaisons": True, "peremption": True},
        "conditions": ["neuf"],
        "categories": [
            {"nom": "Parfums", "variantes": True, "attributs": [
                {"nom": "Contenance", "valeurs": ["30 ml", "50 ml", "100 ml",
                                                  "200 ml"]},
            ]},
            {"nom": "Soins visage", "variantes": True, "attributs": [
                {"nom": "Contenance", "valeurs": ["50 ml", "100 ml", "200 ml",
                                                  "500 ml"]},
            ]},
            {"nom": "Soins corps", "variantes": True, "attributs": [
                {"nom": "Contenance", "valeurs": ["100 ml", "250 ml", "400 ml",
                                                  "500 ml", "1 L"]},
            ]},
            {"nom": "Cheveux & perruques", "variantes": True, "attributs": [
                {"nom": "Longueur", "valeurs": ['8"', '10"', '12"', '14"',
                                                '16"', '18"', '20"', '24"']},
                {"nom": "Couleur", "valeurs": ["Naturel", "1B", "2", "4", "27",
                                               "30", "613", "Blond"]},
            ]},
            {"nom": "Maquillage", "variantes": True, "attributs": [
                {"nom": "Teinte", "valeurs": ["Clair", "Moyen", "Foncé",
                                              "Très foncé"]},
            ]},
            {"nom": "Accessoires beauté", "variantes": False},
        ],
    },

    "general": {
        "libelle": "Boutique générale",
        "resume": (
            "Le suivi le plus simple : un produit, une quantité. À choisir "
            "quand aucun autre profil ne colle, ou pour un stock mélangé."
        ),
        "exemples": [
            "quincaillerie", "librairie et papeterie", "articles divers",
            "matériaux de construction",
        ],
        "mots_cles": [
            "divers", "general", "quincaillerie", "papeterie", "librairie",
            "materiaux", "outillage", "bazar", "melange",
        ],
        "tracage": "simple",
        "libelles": {},
        "modules": {},
        "conditions": ["neuf", "occasion"],
        "categories": [
            {"nom": "Divers", "variantes": False},
        ],
    },
}


# ---------------------------------------------------------------------------
# Lecture / écriture du profil retenu
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Unités de vente
# ---------------------------------------------------------------------------
# `code` va en base (Product.unit), `libelle` s'affiche, `abrege` suit un
# nombre : « 2,5 kg », « 3 sachets ».
#
#   `decimal` — une quantité fractionnaire a-t-elle un sens ? On ne vend pas
#               1,5 sachet, mais bien 1,5 kg.
#   `mot`     — l'abrégé est-il un mot français, qui prend donc un « s » au
#               pluriel ? « 3 sacs » mais « 3 kg ». Déclaré et non deviné : « kg »
#               et « sac » font la même longueur et ne se comportent pas pareil.
UNITES = (
    {"code": "piece", "libelle": "Pièce", "abrege": "pce", "decimal": False, "mot": False},
    {"code": "kg", "libelle": "Kilogramme", "abrege": "kg", "decimal": True, "mot": False},
    {"code": "g", "libelle": "Gramme", "abrege": "g", "decimal": False, "mot": False},
    {"code": "litre", "libelle": "Litre", "abrege": "L", "decimal": True, "mot": False},
    {"code": "cl", "libelle": "Centilitre", "abrege": "cl", "decimal": False, "mot": False},
    {"code": "sachet", "libelle": "Sachet", "abrege": "sachet", "decimal": False, "mot": True},
    {"code": "paquet", "libelle": "Paquet", "abrege": "paquet", "decimal": False, "mot": True},
    {"code": "boite", "libelle": "Boîte", "abrege": "boîte", "decimal": False, "mot": True},
    {"code": "bouteille", "libelle": "Bouteille", "abrege": "bouteille", "decimal": False, "mot": True},
    {"code": "casier", "libelle": "Casier", "abrege": "casier", "decimal": False, "mot": True},
    {"code": "carton", "libelle": "Carton", "abrege": "carton", "decimal": False, "mot": True},
    {"code": "sac", "libelle": "Sac", "abrege": "sac", "decimal": False, "mot": True},
    {"code": "metre", "libelle": "Mètre", "abrege": "m", "decimal": True, "mot": False},
    {"code": "yard", "libelle": "Yard", "abrege": "yd", "decimal": True, "mot": False},
)

UNITE_DEFAUT = "piece"
_UNITES_PAR_CODE = {u["code"]: u for u in UNITES}

# Ce que chaque métier propose en premier. Une liste vide veut dire « toutes » :
# le formulaire montre alors le catalogue complet.
UNITES_PAR_PROFIL = {
    "alimentation": ("piece", "kg", "g", "litre", "cl", "sachet", "paquet",
                     "boite", "bouteille", "casier", "carton", "sac"),
    "mode": ("piece", "metre", "yard"),
    "cosmetique": ("piece", "boite", "carton"),
    "telephonie": ("piece", "carton"),
}


def unite(code: Optional[str]) -> dict:
    """Fiche de l'unité, ou celle de la pièce si le code est vide ou inconnu.
    Une fiche produit sans unité se lit « à la pièce »."""
    return _UNITES_PAR_CODE.get(
        (code or "").strip().lower(), _UNITES_PAR_CODE[UNITE_DEFAUT])


def unites_proposees(db: Optional[Session] = None,
                     connu: Optional[dict] = None) -> List[dict]:
    """Unités à proposer pour ce métier, dans l'ordre d'usage."""
    courant = connu or profil_courant(db)
    codes = UNITES_PAR_PROFIL.get(courant["code"])
    if not codes:
        return [dict(u) for u in UNITES]
    return [dict(_UNITES_PAR_CODE[c]) for c in codes if c in _UNITES_PAR_CODE]


def quantite_lisible(quantite, code_unite: Optional[str]) -> str:
    """« 12 pce », « 2,5 kg », « 3 sachets ». Le pluriel ne s'applique qu'aux
    unités qui sont des mots (sachet, carton), pas aux symboles (kg, L)."""
    fiche = unite(code_unite)
    valeur = float(quantite or 0)
    if fiche["decimal"]:
        nombre = f"{valeur:.3f}".rstrip("0").rstrip(".").replace(".", ",")
    else:
        nombre = str(int(valeur))
    abrege = fiche["abrege"]
    if fiche.get("mot") and abs(valeur) > 1:
        abrege += "s"
    return f"{nombre} {abrege}"


# ---------------------------------------------------------------------------
# Lots
# ---------------------------------------------------------------------------

def reference_lot(product_id: int, lot: Optional[str],
                  peremption: Optional[Any] = None,
                  rang: int = 0) -> str:
    """Référence unique d'un lot, pour `ProductVariant.imei_serial`.

    Cette colonne est unique sur toute la base, alors qu'un numéro de lot ne
    l'est pas : le fabricant peut donner « L2405 » à deux produits différents.
    On préfixe donc par le produit, et on ajoute la date limite puis un rang si
    la boutique reçoit deux fois le même lot.
    """
    morceaux = [f"P{int(product_id)}"]
    propre = _code_technique(lot or "", 40).upper() if lot else ""
    morceaux.append(propre or "LOT")
    if peremption is not None:
        texte = getattr(peremption, "isoformat", lambda: str(peremption))()
        morceaux.append(str(texte)[:10].replace("-", ""))
    if rang:
        morceaux.append(str(rang))
    return "-".join(morceaux)[:255]


# ---------------------------------------------------------------------------
# Déclinaisons
# ---------------------------------------------------------------------------

def reference_declinaison(product_id: int, attributs: Any,
                          rang: int = 0) -> str:
    """Référence unique d'une déclinaison, pour `ProductVariant.imei_serial`.

    Même contrainte que pour les lots : la colonne est unique sur toute la base.
    Un vendeur de prêt-à-porter n'a aucune raison d'inventer un code pour chacune
    de ses vingt-quatre combinaisons — on l'engendre depuis les attributs, ce qui
    donne au passage une référence lisible sur une étiquette :

        P42-M-ROUGE

    `attributs` accepte un dictionnaire {nom: valeur} ou une suite de couples.
    L'ordre est conservé : deux combinaisons décrites dans le même ordre donnent
    la même référence, ce qui permet de repérer un doublon sans interroger la
    base sur chaque attribut.
    """
    couples = (attributs.items() if isinstance(attributs, dict)
               else list(attributs or ()))
    morceaux = [f"P{int(product_id)}"]
    for _, valeur in couples:
        propre = _code_technique(str(valeur or ""), 24).upper()
        if propre:
            morceaux.append(propre)
    if len(morceaux) == 1:
        morceaux.append("VAR")
    if rang:
        morceaux.append(str(rang))
    return "-".join(morceaux)[:255]


def etiquette_declinaison(attributs: Any) -> str:
    """« M · Rouge » — ce que le vendeur lit dans une liste. Les noms d'attributs
    sont omis : dans une grille de tailles et de couleurs, ils sont redondants."""
    couples = (attributs.items() if isinstance(attributs, dict)
               else list(attributs or ()))
    valeurs = [str(v) for _, v in couples if str(v or "").strip()]
    return " · ".join(valeurs) if valeurs else "(sans déclinaison)"


def existe(code: Optional[str]) -> bool:
    return isinstance(code, str) and code.strip().lower() in PROFILS


def _normaliser_code(code: Optional[str]) -> Optional[str]:
    if not isinstance(code, str):
        return None
    code = code.strip().lower()
    return code if code in PROFILS else None


def _profil_env() -> str:
    """Profil posé à la création de l'instance. Permet au provisionnement de
    livrer une boutique déjà orientée, avant toute connexion du commerçant."""
    return _normaliser_code(os.getenv("SHOP_PROFILE")) or PROFIL_DEFAUT


def profil(code: Optional[str] = None) -> dict:
    """Le profil complet, défauts fusionnés. Ne touche pas la base."""
    code = _normaliser_code(code) or PROFIL_DEFAUT
    brut = PROFILS[code]
    tracage = brut.get("tracage", "simple")

    libelles = dict(LIBELLES_DEFAUT)
    libelles.update(brut.get("libelles") or {})

    modules = dict(MODULES_DEFAUT)
    modules.update(brut.get("modules") or {})

    return {
        "code": code,
        "libelle": brut["libelle"],
        "resume": brut.get("resume", ""),
        "exemples": list(brut.get("exemples") or []),
        "mots_cles": list(brut.get("mots_cles") or []),
        "tracage": tracage,
        "tracage_libelle": TRACAGES.get(tracage, {}).get("libelle", ""),
        "tracage_explication": TRACAGES.get(tracage, {}).get("explication", ""),
        "serial_par_defaut": TRACAGES.get(tracage, {}).get(
            "serial_par_defaut", False),
        "variantes_comptees": TRACAGES.get(tracage, {}).get(
            "variantes_comptees", False),
        "libelles": libelles,
        "modules": modules,
        "conditions": list(brut.get("conditions") or ["neuf"]),
        "categories": [dict(c) for c in (brut.get("categories") or [])],
    }


def charger(db: Optional[Session]) -> dict:
    """Profil en vigueur pour cette boutique.

    Tolérant par construction : une base injoignable ou un enregistrement
    illisible ne doit pas empêcher l'application de s'afficher, elle retombe sur
    le profil d'environnement. Les libellés d'un écran ne valent pas une erreur
    500."""
    etat = {"applique": False, "applique_le": None, "applique_par": None}
    code = _profil_env()
    reglages = {}

    if db is not None:
        try:
            from .database import UserSettings
            ligne = (db.query(UserSettings)
                     .filter(UserSettings.setting_key == CLE_PROFIL)
                     .order_by(UserSettings.updated_at.desc())
                     .first())
            if ligne and ligne.setting_value:
                enregistre = json.loads(ligne.setting_value)
                if isinstance(enregistre, dict):
                    code = _normaliser_code(enregistre.get("profil")) or code
                    etat["applique"] = bool(enregistre.get("applique"))
                    etat["applique_le"] = enregistre.get("applique_le")
                    etat["applique_par"] = enregistre.get("applique_par")
                    brut = enregistre.get("modules")
                    if isinstance(brut, dict):
                        reglages = {nom: bool(valeur)
                                    for nom, valeur in brut.items()
                                    if nom in MODULES_DEFAUT}
        except Exception:  # noqa: BLE001
            logger.exception(
                "[profil] lecture impossible, repli sur « %s »", code)

    resultat = profil(code)
    # Le métier propose, l'administrateur décide : un module coché ou décoché
    # depuis l'écran Paramètres l'emporte sur le réglage du profil. Ce qu'il n'a
    # pas touché suit le métier, et suivra donc un futur changement de métier.
    resultat["modules"].update(reglages)
    resultat["modules_regles"] = reglages
    resultat.update(etat)
    return resultat


_CACHE: Dict[str, Any] = {"pose_a": 0.0, "profil": None}

# Un profil change une fois dans la vie d'une boutique, mais il est lu à chaque
# rendu de page. On le garde en mémoire ; la durée courte évite qu'un second
# ouvrier uvicorn, dont le cache n'a pas été invalidé, garde longtemps
# l'ancienne valeur.
_CACHE_DUREE = 30.0


def profil_courant(db: Optional[Session] = None) -> dict:
    """Le profil en vigueur, lu au plus une fois par demi-minute.

    Sert aux gabarits, appelés bien trop souvent pour interroger la base à
    chaque libellé. Passer `db` évite d'ouvrir une session de plus quand on en a
    déjà une sous la main."""
    import time

    maintenant = time.monotonic()
    en_cache = _CACHE["profil"]
    if en_cache is not None and maintenant - _CACHE["pose_a"] < _CACHE_DUREE:
        return en_cache

    if db is not None:
        courant = charger(db)
    else:
        try:
            from .database import SessionLocal
            session = SessionLocal()
            try:
                courant = charger(session)
            finally:
                session.close()
        except Exception:  # noqa: BLE001
            logger.exception("[profil] session indisponible, profil par défaut")
            return profil(_profil_env())

    _CACHE["profil"] = courant
    _CACHE["pose_a"] = maintenant
    return courant


def vider_cache() -> None:
    _CACHE["profil"] = None
    _CACHE["pose_a"] = 0.0


def enregistrer(db: Session, code: str,
                applique_par: Optional[str] = None,
                applique: bool = False,
                modules: Optional[dict] = None) -> dict:
    """Retient le profil choisi. Refuse un code inconnu plutôt que de laisser la
    boutique dans un état qu'aucun profil ne décrit.

    `modules` remplace les réglages manuels. Ne rien passer les **efface** : un
    changement de métier repart des modules du nouveau métier, sinon un atelier
    activé pour la téléphonie resterait allumé dans une supérette.
    """
    from datetime import datetime

    from .database import UserSettings

    normalise = _normaliser_code(code)
    if normalise is None:
        raise ValueError(f"Profil de boutique inconnu : {code!r}")

    charge = {
        "profil": normalise,
        "applique": bool(applique),
        "applique_le": datetime.now().isoformat(timespec="seconds"),
        "applique_par": applique_par,
        "modules": {nom: bool(valeur)
                    for nom, valeur in (modules or {}).items()
                    if nom in MODULES_DEFAUT},
    }

    ligne = (db.query(UserSettings)
             .filter(UserSettings.setting_key == CLE_PROFIL)
             .order_by(UserSettings.updated_at.desc())
             .first())
    serialise = json.dumps(charge, ensure_ascii=False)
    if ligne:
        ligne.setting_value = serialise
    else:
        # `user_id` reste nul : le profil est celui de la boutique, pas d'un
        # vendeur en particulier.
        db.add(UserSettings(user_id=None, setting_key=CLE_PROFIL,
                            setting_value=serialise))
    db.commit()
    vider_cache()
    return charger(db)


# ---------------------------------------------------------------------------
# Aides pour les gabarits et les routes
# ---------------------------------------------------------------------------

def libelle(cle: str, db: Optional[Session] = None,
            connu: Optional[dict] = None) -> str:
    """Mot d'interface adapté au métier : `libelle('identifiant_court')` rend
    « IMEI » en téléphonie et « Référence » en prêt-à-porter."""
    courant = connu or profil_courant(db)
    return courant["libelles"].get(cle, LIBELLES_DEFAUT.get(cle, cle))


def module_actif(nom: str, db: Optional[Session] = None,
                 connu: Optional[dict] = None) -> bool:
    """Vrai si le module a un sens pour ce métier *et* que l'abonnement le
    couvre. L'ordre compte : le profil propose, le plan dispose."""
    courant = connu or profil_courant(db)
    if not courant["modules"].get(nom, False):
        return False

    fonctionnalite = MODULE_FONCTIONNALITE.get(nom)
    if fonctionnalite:
        try:
            from . import plan as subscription_plan
            return bool(subscription_plan.has_feature(fonctionnalite))
        except Exception:  # noqa: BLE001
            logger.exception("[profil] plan illisible pour le module %s", nom)
            return False
    return True


def enregistrer_modules(db: Session, reglages: Any) -> dict:
    """Écrit les modules réglés à la main, sans toucher au métier choisi.

    Ne retient que les modules connus et les valeurs booléennes ; le reste est
    ignoré en silence plutôt que d'allumer quelque chose par erreur. Reposer un
    module sur la valeur du métier revient à retirer le réglage : la boutique
    suivra de nouveau son métier, y compris s'il change.
    """
    courant = charger(db)
    defauts = profil(courant["code"])["modules"]
    retenus = dict(courant.get("modules_regles") or {})

    if isinstance(reglages, dict):
        for nom, valeur in reglages.items():
            if nom not in MODULES_DEFAUT or not isinstance(valeur, bool):
                continue
            if valeur == defauts.get(nom, False):
                retenus.pop(nom, None)
            else:
                retenus[nom] = valeur

    return enregistrer(db, courant["code"],
                       applique_par=courant.get("applique_par"),
                       applique=bool(courant.get("applique")),
                       modules=retenus)


def catalogue_modules(db: Optional[Session] = None,
                      connu: Optional[dict] = None) -> List[dict]:
    """Les modules et leur état, pour l'écran Paramètres.

    Distingue trois choses que le commerçant confond volontiers : ce que le
    métier propose, ce qu'il a lui-même décidé, et ce que son abonnement
    autorise."""
    courant = connu or charger(db)
    defauts = profil(courant["code"])["modules"]
    regles = courant.get("modules_regles") or {}

    fiches = []
    for nom in LIBELLES_MODULES:
        libelle_module, explication = LIBELLES_MODULES[nom]
        fonctionnalite = MODULE_FONCTIONNALITE.get(nom)
        dans_le_plan = True
        if fonctionnalite:
            try:
                from . import plan as subscription_plan
                dans_le_plan = bool(
                    subscription_plan.has_feature(fonctionnalite))
            except Exception:  # noqa: BLE001
                logger.exception("[profil] plan illisible pour %s", nom)
                dans_le_plan = False
        fiches.append({
            "nom": nom,
            "libelle": libelle_module,
            "explication": explication,
            "actif": bool(courant["modules"].get(nom, False)) and dans_le_plan,
            "defaut_metier": bool(defauts.get(nom, False)),
            "regle_a_la_main": nom in regles,
            "incluse_dans_le_plan": dans_le_plan,
        })
    return fiches


def catalogue() -> List[dict]:
    """Les profils proposés, dans l'ordre d'affichage. Sert à l'écran
    Paramètres, à l'installation guidée et à l'IA."""
    ordre = ["telephonie", "mode", "alimentation", "cosmetique", "general"]
    fiches = []
    for code in ordre:
        p = profil(code)
        fiches.append({
            "code": code,
            "libelle": p["libelle"],
            "resume": p["resume"],
            "exemples": p["exemples"],
            "tracage": p["tracage"],
            "tracage_libelle": p["tracage_libelle"],
            "categories": [c["nom"] for c in p["categories"]],
            "modules": [nom for nom, actif in p["modules"].items() if actif],
        })
    return fiches


def resume_pour_ia() -> str:
    """Description compacte des profils, à coller dans la consigne du modèle.

    Volontairement construite à partir de `PROFILS` : un profil ajouté ici est
    immédiatement connu de l'IA, sans consigne à réécrire ailleurs."""
    lignes = []
    for fiche in catalogue():
        p = profil(fiche["code"])
        lignes.append(
            f"- {fiche['code']} — {fiche['libelle']}. {fiche['resume']} "
            f"Exemples : {', '.join(fiche['exemples'])}. "
            f"Suivi du stock : {p['tracage_explication']}"
        )
    return "\n".join(lignes)


def deviner(texte: str) -> Optional[str]:
    """Profil suggéré par les mots employés par le commerçant.

    Repli grossier mais utile : si l'appel au modèle échoue, l'installation
    guidée peut encore proposer quelque chose de sensé. Ne décide jamais seule —
    la proposition est toujours confirmée par le commerçant."""
    if not texte:
        return None
    normalise = _sans_accents(texte).lower()
    scores: Dict[str, int] = {}
    for code, brut in PROFILS.items():
        for mot in brut.get("mots_cles") or []:
            if _sans_accents(mot).lower() in normalise:
                scores[code] = scores.get(code, 0) + 1
    if not scores:
        return None
    meilleur = max(scores.items(), key=lambda kv: kv[1])
    return meilleur[0]


def _sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte)
                   if unicodedata.category(c) != "Mn")


def _code_technique(valeur: str, longueur: int = 50) -> str:
    """Code stable dérivé d'un libellé. Les colonnes `code` des attributs sont
    uniques par catégorie : deux libellés distincts doivent donner deux codes
    distincts, d'où le repli sur un suffixe numérique côté appelant."""
    base = _sans_accents(valeur).lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return (base or "valeur")[:longueur]


# ---------------------------------------------------------------------------
# Application du profil
# ---------------------------------------------------------------------------

def appliquer(db: Session, code: str,
              applique_par: Optional[str] = None) -> dict:
    """Prépare la boutique pour ce métier, sans rien détruire.

    Crée les catégories manquantes et leurs attributs, laisse en place tout ce
    qui existe déjà. Un commerçant qui se trompe de profil, l'applique, puis en
    choisit un autre, se retrouve avec deux catalogues côte à côte : gênant,
    mais réversible à la main — alors qu'un stock supprimé ne revient pas.

    Rend un compte rendu de ce qui a été fait, que l'assistant lit au commerçant
    et que l'écran Paramètres affiche.
    """
    from .database import Category, CategoryAttribute, CategoryAttributeValue, Product

    normalise = _normaliser_code(code)
    if normalise is None:
        raise ValueError(f"Profil de boutique inconnu : {code!r}")

    # Réappliquer le même métier ne doit pas effacer les modules que
    # l'administrateur a réglés ; en changer, si — ils décrivaient l'autre
    # commerce.
    avant = charger(db)
    modules_conserves = (dict(avant.get("modules_regles") or {})
                         if avant["code"] == normalise else {})

    p = profil(normalise)
    rapport = {
        "profil": normalise,
        "libelle": p["libelle"],
        "categories_creees": [],
        "categories_existantes": [],
        "attributs_crees": [],
        "valeurs_ajoutees": 0,
        "variantes_non_modifiees": [],
        "categories_hors_profil": [],
    }

    attendues = {c["nom"] for c in p["categories"]}

    for fiche in p["categories"]:
        nom = fiche["nom"]
        veut_variantes = bool(fiche.get("variantes"))

        categorie = db.query(Category).filter(Category.name == nom).first()
        if categorie is None:
            categorie = Category(
                name=nom,
                description=f"Catégorie {nom}",
                requires_variants=veut_variantes,
            )
            db.add(categorie)
            db.flush()  # besoin de category_id pour les attributs
            rapport["categories_creees"].append(nom)
        else:
            rapport["categories_existantes"].append(nom)
            # Basculer `requires_variants` sur une catégorie déjà remplie
            # changerait la façon de compter un stock existant. On ne le fait
            # que si la catégorie est vide, et on le signale sinon.
            if bool(categorie.requires_variants) != veut_variantes:
                occupee = (db.query(Product)
                           .filter(Product.category == nom)
                           .first()) is not None
                if occupee:
                    rapport["variantes_non_modifiees"].append(nom)
                else:
                    categorie.requires_variants = veut_variantes

        for rang, attribut in enumerate(fiche.get("attributs") or []):
            cree, ajoutees = _garantir_attribut(
                db, categorie, attribut, rang,
                CategoryAttribute, CategoryAttributeValue)
            if cree:
                rapport["attributs_crees"].append(f"{nom} → {attribut['nom']}")
            rapport["valeurs_ajoutees"] += ajoutees

    # Catégories déjà présentes qui ne relèvent pas de ce métier : on les laisse
    # (elles portent peut-être du stock) mais on les nomme, pour que le
    # commerçant décide lui-même de les garder ou de les retirer.
    for categorie in db.query(Category).all():
        if categorie.name not in attendues:
            rapport["categories_hors_profil"].append(categorie.name)

    db.commit()
    rapport["etat"] = enregistrer(db, normalise, applique_par=applique_par,
                                  applique=True, modules=modules_conserves)
    return rapport


def _garantir_attribut(db: Session, categorie, fiche: dict, rang: int,
                       CategoryAttribute, CategoryAttributeValue):
    """Crée l'attribut s'il manque, complète ses valeurs manquantes.

    Rend `(attribut_cree, nombre_de_valeurs_ajoutees)`. Ne renomme ni ne
    supprime : un attribut que le commerçant a ajusté reste tel quel."""
    nom = fiche["nom"]
    valeurs = list(fiche.get("valeurs") or [])
    type_attribut = fiche.get("type") or ("select" if valeurs else "text")

    attribut = (db.query(CategoryAttribute)
                .filter(CategoryAttribute.category_id == categorie.category_id,
                        CategoryAttribute.name == nom)
                .first())
    cree = False
    if attribut is None:
        attribut = CategoryAttribute(
            category_id=categorie.category_id,
            name=nom,
            code=_code_technique(nom),
            type=type_attribut,
            required=bool(fiche.get("requis")),
            multi_select=bool(fiche.get("multiple")),
            sort_order=rang,
        )
        db.add(attribut)
        db.flush()
        cree = True

    existantes = {(v.value or "").strip().lower() for v in attribut.values}
    ajoutees = 0
    for position, valeur in enumerate(valeurs):
        if valeur.strip().lower() in existantes:
            continue
        db.add(CategoryAttributeValue(
            attribute_id=attribut.attribute_id,
            value=valeur,
            code=_code_technique(valeur, 100),
            sort_order=position,
        ))
        existantes.add(valeur.strip().lower())
        ajoutees += 1

    return cree, ajoutees


def resume_rapport(rapport: dict) -> str:
    """Compte rendu en une phrase ou deux, lisible par un commerçant — l'IA le
    reprend tel quel plutôt que de paraphraser des listes JSON."""
    morceaux = [f"Profil « {rapport['libelle']} » appliqué."]

    creees = rapport.get("categories_creees") or []
    if creees:
        morceaux.append(
            f"{len(creees)} catégorie(s) créée(s) : {', '.join(creees)}.")

    attributs = rapport.get("attributs_crees") or []
    if attributs:
        morceaux.append(
            f"{len(attributs)} grille(s) de déclinaison ajoutée(s) "
            f"({rapport.get('valeurs_ajoutees', 0)} valeurs).")

    existantes = rapport.get("categories_existantes") or []
    if existantes:
        morceaux.append(
            f"{len(existantes)} catégorie(s) déjà en place, laissée(s) telle(s) "
            "quelle(s).")

    bloquees = rapport.get("variantes_non_modifiees") or []
    if bloquees:
        morceaux.append(
            "À vérifier dans Paramètres : "
            f"{', '.join(bloquees)} contien(nen)t déjà des produits, le mode de "
            "déclinaison n'a pas été changé.")

    hors = rapport.get("categories_hors_profil") or []
    if hors:
        apercu = ", ".join(hors[:5]) + ("…" if len(hors) > 5 else "")
        morceaux.append(
            f"{len(hors)} catégorie(s) ne relèvent pas de ce métier ({apercu}) ; "
            "elles restent disponibles, à vous de les retirer si elles ne "
            "servent plus.")

    return " ".join(morceaux)
