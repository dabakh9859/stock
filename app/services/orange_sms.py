"""Envoi de SMS par l'API Orange Sénégal.

C'est l'opérateur en direct, et non un agrégateur international : le message
part de Dakar vers Dakar sans terminaison internationale. Environ **20 F CFA le
SMS contre ~314 F chez Twilio**, et le forfait se paie en crédit de
communication — sans carte bancaire internationale. L'API dessert les trois
réseaux du pays (Orange, Free, Expresso).

Quatre variables d'environnement :
    ORANGE_SMS_CLIENT_ID       identifiant de l'application
    ORANGE_SMS_CLIENT_SECRET   secret de l'application
    ORANGE_SMS_SENDER_ADDRESS  numéro émetteur du contrat, « tel:+221XXXXXXXXX »
    ORANGE_SMS_SENDER_NAME     (facultatif) nom affiché, 11 caractères maximum

Elles se lisent sur developer.orange.com une fois l'application créée et le
forfait acheté. Le module frère côté boutique est `lib/sms/orange.ts`, qui parle
à la même API : les deux `.env` portent donc les mêmes valeurs.
"""

import base64
import logging
import os
import re
import threading
import time
import unicodedata
from urllib.parse import quote

import httpx

TOKEN_URL = "https://api.orange.com/oauth/v3/token"
SMS_URL = "https://api.orange.com/smsmessaging/v1/outbound"
CONTRATS_URL = "https://api.orange.com/sms/admin/v1/contracts"

#: Orange plafonne le nom d'expéditeur à 11 caractères alphanumériques.
NOM_MAX = 11

# Alphabet GSM-7. Un caractère en dehors fait basculer le message entier en
# UCS-2, où le segment facturé tombe de 160 à 70 caractères — un « × » ou un
# emoji dans le nom d'un client double donc le prix d'un message identique.
_GSM = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    "^{}\\[~]|€"
)

_REMPLACEMENTS = {
    "—": "-", "–": "-", "×": "x", "’": "'", "‘": "'",
    "“": '"', "”": '"', "…": "...", "•": "-", " ": " ", " ": " ",
}


def vers_gsm7(texte: str) -> str:
    """Rend le texte représentable en GSM-7, pour ne pas payer double.

    Les accents que l'alphabet GSM connaît (é, è, à, ü…) sont conservés : les
    retirer n'économise rien et abîme la lecture. Seul ce qui forcerait l'UCS-2
    est translittéré, puis ce qui reste — emoji notamment — est supprimé, faute
    d'équivalent.
    """
    sortie = []
    for caractere in texte:
        caractere = _REMPLACEMENTS.get(caractere, caractere)
        if caractere in _GSM:
            sortie.append(caractere)
            continue
        # « Â » se décompose en « A » + accent combinant, que l'on retire.
        decompose = "".join(
            c for c in unicodedata.normalize("NFD", caractere)
            if not unicodedata.combining(c)
        )
        sortie.append("".join(c for c in decompose if c in _GSM))

    propre = "".join(sortie)
    # La suppression des emoji laisse des espaces doubles.
    while "  " in propre:
        propre = propre.replace("  ", " ")
    return "\n".join(ligne.rstrip() for ligne in propre.split("\n"))


#: Marqueurs de mise en forme WhatsApp : *gras*, _italique_, ~barré~.
_MISE_EN_FORME = re.compile(r"(?<![\w])([*_~])(\S(?:[^*_~\n]*\S)?)\1(?![\w])")


def sans_mise_en_forme(texte: str) -> str:
    """Retire la mise en forme WhatsApp.

    Les messages sont composés pour WhatsApp, où `*Livraison CMD-2608-0002*`
    s'affiche en gras. En SMS il n'y a pas de gras : le livreur lirait les
    astérisques telles quelles.
    """
    return _MISE_EN_FORME.sub(r"\2", texte)


def segments(texte: str) -> int:
    """Nombre de segments facturés par l'opérateur."""
    if any(c not in _GSM for c in texte):
        return 1 if len(texte) <= 70 else -(-len(texte) // 67)
    longueur = sum(2 if c in "^{}\\[~]|€" else 1 for c in texte)
    return 1 if longueur <= 160 else -(-longueur // 153)


def config() -> dict | None:
    """Les identifiants sont-ils tous présents ? Sinon le canal est inactif."""
    client_id = (os.getenv("ORANGE_SMS_CLIENT_ID") or "").strip()
    secret = (os.getenv("ORANGE_SMS_CLIENT_SECRET") or "").strip()
    emetteur = (os.getenv("ORANGE_SMS_SENDER_ADDRESS") or "").strip()
    if not (client_id and secret and emetteur):
        return None

    # Tolère « 221771234567 », « +221771234567 » ou « tel:+221771234567 » : la
    # valeur est recopiée depuis la console, la forme varie selon l'écran.
    if not emetteur.startswith("tel:"):
        emetteur = "tel:+" + emetteur.lstrip("+")

    nom = "".join(
        c for c in (os.getenv("ORANGE_SMS_SENDER_NAME") or "").strip()
        if c.isalnum() or c == " "
    )[:NOM_MAX]

    return {"client_id": client_id, "secret": secret, "emetteur": emetteur, "nom": nom or None}


# Le jeton vaut une heure et Orange demande de le réutiliser plutôt que d'en
# redemander un à chaque envoi. Le verrou protège des requêtes concurrentes.
_jeton: tuple[str, float] | None = None
_verrou = threading.Lock()


def _obtenir_jeton(conf: dict) -> str:
    global _jeton
    with _verrou:
        if _jeton and time.time() < _jeton[1]:
            return _jeton[0]

        identifiants = base64.b64encode(
            f"{conf['client_id']}:{conf['secret']}".encode()
        ).decode()

        with httpx.Client(timeout=15.0) as client:
            reponse = client.post(
                TOKEN_URL,
                headers={
                    "Authorization": f"Basic {identifiants}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                content="grant_type=client_credentials",
            )

        if reponse.status_code == 401:
            raise RuntimeError("Identifiants Orange refusés (client ID ou secret).")
        if reponse.status_code >= 300:
            raise RuntimeError(f"Orange a refusé le jeton ({reponse.status_code}).")

        donnees = reponse.json()
        acces = donnees.get("access_token")
        if not acces:
            raise RuntimeError("Orange n'a pas renvoyé de jeton.")

        # Renouvelé une minute avant l'échéance, pour ne pas se faire refuser un
        # message sur une horloge qui dérive.
        duree = int(donnees.get("expires_in") or 3600)
        _jeton = (acces, time.time() + duree - 60)
        return acces


def _oublier_jeton() -> None:
    global _jeton
    with _verrou:
        _jeton = None


def envoyer(numero: str, texte: str) -> tuple[bool, str]:
    """Envoie un SMS. Renvoie (succès, message d'erreur).

    `numero` est au format international sans « + » : 221771234567.
    """
    conf = config()
    if not conf:
        return False, "L'envoi par SMS n'est pas configuré (identifiants Orange absents)."

    message = vers_gsm7(sans_mise_en_forme(texte))
    corps = {
        "outboundSMSMessageRequest": {
            "address": "tel:+" + numero.lstrip("+"),
            "senderAddress": conf["emetteur"],
            "outboundSMSTextMessage": {"message": message},
        }
    }
    if conf["nom"]:
        corps["outboundSMSMessageRequest"]["senderName"] = conf["nom"]

    try:
        acces = _obtenir_jeton(conf)

        # L'adresse émettrice voyage dans le chemin, encodée : « tel:+221… » y
        # devient « tel%3A%2B221… ».
        url = f"{SMS_URL}/{quote(conf['emetteur'], safe='')}/requests"
        with httpx.Client(timeout=20.0) as client:
            reponse = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {acces}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=corps,
            )

        if 200 <= reponse.status_code < 300:
            logging.info(
                "SMS Orange envoyé à %s (%s segment(s))", numero, segments(message)
            )
            return True, ""

        # Un jeton révoqué avant son échéance renverrait 401 : on le jette pour
        # que la tentative suivante en redemande un.
        if reponse.status_code == 401:
            _oublier_jeton()

        detail = ""
        try:
            erreur = (reponse.json() or {}).get("requestError", {})
            detail = (
                erreur.get("serviceException", {}).get("text")
                or erreur.get("policyException", {}).get("text")
                or ""
            )
        except Exception:
            pass
        detail = detail or f"Orange a répondu {reponse.status_code}."

        # Le forfait épuisé est le cas courant en préachat : il mérite un message
        # que la personne de la boutique comprend sans ouvrir les journaux.
        if reponse.status_code == 403 and any(
            mot in detail.lower() for mot in ("balance", "quota", "credit")
        ):
            return False, "Forfait SMS Orange épuisé — rechargez-le."
        return False, detail

    except Exception as erreur:
        return False, str(erreur)


def solde_sms() -> int | None:
    """SMS restants sur le forfait, ou None si l'information n'est pas lisible.

    Le forfait est prépayé : épuisé, il ferait échouer les envois sans prévenir.
    """
    conf = config()
    if not conf:
        return None
    try:
        acces = _obtenir_jeton(conf)
        with httpx.Client(timeout=12.0) as client:
            reponse = client.get(
                CONTRATS_URL,
                params={"country": "SEN"},
                headers={"Authorization": f"Bearer {acces}", "Accept": "application/json"},
            )
        if reponse.status_code >= 300:
            return None
        contrats = (reponse.json() or {}).get("partnerContracts", {}).get("contracts", [])
        actifs = [c for c in contrats if (c.get("status") or "ACTIVE").upper() == "ACTIVE"]
        return sum(int(c.get("availableUnits") or 0) for c in actifs)
    except Exception:
        return None
