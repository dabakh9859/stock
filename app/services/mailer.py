"""Envoi d'e-mails — factures, devis, et ce que la boutique voudra joindre.

Jusqu'ici la fonction n'existait pas : les routes répondaient poliment que la
configuration SMTP viendrait plus tard. Elle est là.

**Où vit la configuration.** Deux sources, dans cet ordre : les variables
d'environnement (posées au provisionnement, pratiques pour un exploitant qui
gère cent instances) puis les réglages enregistrés depuis l'écran Paramètres,
qui l'emportent. Un commerçant sur une instance SaaS n'a pas accès aux variables
d'environnement de son conteneur ; sans réglage en base, il ne pourrait jamais
brancher sa propre adresse.

**Le mot de passe ne ressort jamais.** `config_publique()` le remplace par un
témoin. L'écran Paramètres ne peut donc que l'écrire, jamais le relire — ce qui
évite qu'une session volée le récupère en clair.

**Aucune exception ne remonte.** `envoyer()` rend toujours un dictionnaire :
un serveur de courrier injoignable ne doit pas transformer l'enregistrement
d'une facture en erreur 500. L'appelant décide quoi en faire.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CLE_CONFIG = "SMTP_CONFIG"

# Témoin renvoyé à la place du mot de passe. Renvoyé tel quel par le navigateur,
# il signifie « ne change pas le mot de passe enregistré ».
INCHANGE = "__inchange__"

DEFAUTS = {
    "hote": "",
    "port": 587,
    "utilisateur": "",
    "mot_de_passe": "",
    "expediteur": "",
    "nom_expediteur": "",
    # starttls : port 587, le plus courant. ssl : port 465. aucun : relais local.
    "securite": "starttls",
    "delai": 20,
}

SECURITES = ("starttls", "ssl", "aucun")


def _depuis_environnement() -> dict:
    config = dict(DEFAUTS)
    correspondances = {
        "hote": "SMTP_HOST",
        "utilisateur": "SMTP_USER",
        "mot_de_passe": "SMTP_PASSWORD",
        "expediteur": "SMTP_FROM",
        "nom_expediteur": "SMTP_FROM_NAME",
        "securite": "SMTP_SECURITY",
    }
    for cle, variable in correspondances.items():
        valeur = (os.getenv(variable) or "").strip()
        if valeur:
            config[cle] = valeur
    for cle, variable in (("port", "SMTP_PORT"), ("delai", "SMTP_TIMEOUT")):
        try:
            brut = (os.getenv(variable) or "").strip()
            if brut:
                config[cle] = int(brut)
        except ValueError:
            logger.warning("[courriel] %s ignoré : entier attendu", variable)
    if config["securite"] not in SECURITES:
        config["securite"] = DEFAUTS["securite"]
    return config


def charger_config(db: Optional[Session]) -> dict:
    """Configuration en vigueur : environnement, puis réglages enregistrés.

    Tolérant : une base injoignable ou un enregistrement illisible ramène à
    l'environnement plutôt que de faire échouer l'écran qui demande l'état.
    """
    config = _depuis_environnement()
    if db is None:
        return config
    try:
        from ..database import UserSettings
        ligne = (db.query(UserSettings)
                 .filter(UserSettings.setting_key == CLE_CONFIG)
                 .order_by(UserSettings.updated_at.desc())
                 .first())
        if ligne and ligne.setting_value:
            enregistre = json.loads(ligne.setting_value)
            if isinstance(enregistre, dict):
                for cle in DEFAUTS:
                    if cle in enregistre and enregistre[cle] not in (None, ""):
                        config[cle] = enregistre[cle]
    except Exception:  # noqa: BLE001
        logger.exception("[courriel] configuration illisible, repli sur "
                         "l'environnement")
    try:
        config["port"] = int(config["port"])
        config["delai"] = int(config["delai"])
    except (TypeError, ValueError):
        config["port"], config["delai"] = DEFAUTS["port"], DEFAUTS["delai"]
    if config["securite"] not in SECURITES:
        config["securite"] = DEFAUTS["securite"]
    return config


def enregistrer_config(db: Session, valeurs: Any) -> dict:
    """Écrit les réglages. Un mot de passe laissé au témoin `INCHANGE` conserve
    celui déjà enregistré : l'écran peut donc être validé sans le ressaisir."""
    from ..database import UserSettings

    actuel = charger_config(db)
    retenu = {cle: actuel[cle] for cle in DEFAUTS}

    if isinstance(valeurs, dict):
        for cle in DEFAUTS:
            if cle not in valeurs:
                continue
            valeur = valeurs[cle]
            if cle == "mot_de_passe":
                if valeur == INCHANGE or valeur is None:
                    continue
                retenu[cle] = str(valeur)
            elif cle in ("port", "delai"):
                try:
                    retenu[cle] = int(valeur)
                except (TypeError, ValueError):
                    pass
            elif cle == "securite":
                if valeur in SECURITES:
                    retenu[cle] = valeur
            else:
                retenu[cle] = str(valeur or "").strip()

    ligne = (db.query(UserSettings)
             .filter(UserSettings.setting_key == CLE_CONFIG)
             .order_by(UserSettings.updated_at.desc())
             .first())
    charge = json.dumps(retenu, ensure_ascii=False)
    if ligne:
        ligne.setting_value = charge
    else:
        db.add(UserSettings(user_id=None, setting_key=CLE_CONFIG,
                            setting_value=charge))
    db.commit()
    return charger_config(db)


def est_configure(config: dict) -> bool:
    """Le minimum pour espérer envoyer : un serveur et une adresse d'expéditeur."""
    return bool((config.get("hote") or "").strip()
                and (config.get("expediteur") or "").strip())


def config_publique(config: dict) -> dict:
    """Ce que l'écran Paramètres a le droit de voir. Le mot de passe est
    remplacé par un témoin : on peut savoir qu'il existe, pas ce qu'il vaut."""
    public = {cle: config[cle] for cle in DEFAUTS if cle != "mot_de_passe"}
    public["mot_de_passe"] = INCHANGE if config.get("mot_de_passe") else ""
    public["configure"] = est_configure(config)
    return public


def _expediteur(config: dict) -> str:
    adresse = (config.get("expediteur") or "").strip()
    nom = (config.get("nom_expediteur") or "").strip()
    if nom:
        # Le nom est cité : une virgule ou un deux-points non protégés
        # couperaient l'en-tête en deux adresses.
        nom = nom.replace('"', "'")
        return f'"{nom}" <{adresse}>'
    return adresse


def envoyer(config: dict, destinataire: str, sujet: str, texte: str,
            pieces: Optional[list] = None) -> dict:
    """Envoie un message. Rend toujours un dictionnaire.

    `pieces` : liste de `{"nom", "contenu" (bytes), "type"}`. Le type est celui
    du document, « application/pdf » pour une facture.
    """
    destinataire = (destinataire or "").strip()
    if not destinataire or "@" not in destinataire:
        return {"envoye": False, "erreur": "Adresse du destinataire invalide."}
    if not est_configure(config):
        return {"envoye": False, "non_configure": True,
                "erreur": "L'envoi par e-mail n'est pas configuré. "
                          "Renseignez votre serveur dans Paramètres → E-mail."}

    message = EmailMessage()
    message["From"] = _expediteur(config)
    message["To"] = destinataire
    message["Subject"] = sujet
    message.set_content(texte)

    for piece in (pieces or []):
        contenu = piece.get("contenu")
        if not contenu:
            continue
        type_mime = (piece.get("type") or "application/octet-stream")
        principal, _, sous = type_mime.partition("/")
        message.add_attachment(contenu, maintype=principal,
                               subtype=sous or "octet-stream",
                               filename=piece.get("nom") or "document")

    hote = config["hote"].strip()
    port = int(config["port"])
    delai = int(config["delai"])
    securite = config["securite"]

    try:
        if securite == "ssl":
            contexte = ssl.create_default_context()
            serveur = smtplib.SMTP_SSL(hote, port, timeout=delai,
                                       context=contexte)
        else:
            serveur = smtplib.SMTP(hote, port, timeout=delai)
        with serveur:
            serveur.ehlo()
            if securite == "starttls":
                serveur.starttls(context=ssl.create_default_context())
                serveur.ehlo()
            if config.get("utilisateur"):
                serveur.login(config["utilisateur"], config.get("mot_de_passe") or "")
            serveur.send_message(message)
        return {"envoye": True, "destinataire": destinataire}

    # Les motifs d'échec sont distingués : le commerçant doit savoir s'il s'est
    # trompé de mot de passe ou si son serveur est injoignable.
    except smtplib.SMTPAuthenticationError:
        logger.warning("[courriel] authentification refusée par %s", hote)
        return {"envoye": False,
                "erreur": "Le serveur a refusé l'identifiant ou le mot de passe. "
                          "Avec Gmail, il faut un « mot de passe d'application »."}
    except smtplib.SMTPRecipientsRefused:
        return {"envoye": False,
                "erreur": f"Le serveur a refusé l'adresse « {destinataire} »."}
    except smtplib.SMTPSenderRefused:
        return {"envoye": False,
                "erreur": "Le serveur a refusé l'adresse d'expéditeur. Elle doit "
                          "en général appartenir au compte utilisé."}
    except (smtplib.SMTPException, OSError) as exc:
        logger.exception("[courriel] échec d'envoi vers %s", hote)
        return {"envoye": False,
                "erreur": f"Serveur de courrier injoignable ou refus : "
                          f"{type(exc).__name__}."}


def envoyer_document(db: Optional[Session], destinataire: str, sujet: str,
                     texte: str, pdf: Optional[bytes] = None,
                     nom_fichier: str = "document.pdf") -> dict:
    """Raccourci employé par les factures et les devis : charge la
    configuration, joint le PDF s'il a pu être produit, envoie."""
    config = charger_config(db)
    pieces = []
    if pdf:
        pieces.append({"nom": nom_fichier, "contenu": pdf,
                       "type": "application/pdf"})
    resultat = envoyer(config, destinataire, sujet, texte, pieces)
    resultat["piece_jointe"] = bool(pdf)
    return resultat
