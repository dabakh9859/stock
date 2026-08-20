"""Réglages d'envoi d'e-mail — écran Paramètres.

Réservé à l'administrateur : ces réglages portent les identifiants d'une boîte
de courrier, et un vendeur n'a pas à s'en approcher. Le mot de passe n'est jamais
renvoyé au navigateur (voir `mailer.config_publique`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..services import mailer

router = APIRouter(prefix="/api/email", tags=["email"])


class ConfigEntree(BaseModel):
    hote: str = ""
    port: int = 587
    utilisateur: str = ""
    mot_de_passe: str = mailer.INCHANGE
    expediteur: str = ""
    nom_expediteur: str = ""
    securite: str = "starttls"


class EssaiEntree(BaseModel):
    destinataire: str


def _exiger_admin(user):
    if getattr(user, "role", "") != "admin":
        return JSONResponse(status_code=403, content={
            "error": "reserve_admin",
            "message": "Seul un administrateur peut régler l'envoi d'e-mails."})
    return None


@router.get("/config")
def lire(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Réglages actuels, mot de passe masqué."""
    refus = _exiger_admin(user)
    if refus is not None:
        return refus
    return {
        "config": mailer.config_publique(mailer.charger_config(db)),
        "securites": [
            {"valeur": "starttls", "libelle": "STARTTLS (port 587, le plus courant)"},
            {"valeur": "ssl", "libelle": "SSL/TLS (port 465)"},
            {"valeur": "aucun", "libelle": "Aucune (relais local uniquement)"},
        ],
    }


@router.put("/config")
def ecrire(donnees: ConfigEntree, user=Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Enregistre les réglages sans les essayer.

    Volontairement séparé de l'essai : un commerçant doit pouvoir corriger une
    faute de frappe et revenir plus tard, sans qu'un serveur momentanément
    injoignable l'empêche d'enregistrer.
    """
    refus = _exiger_admin(user)
    if refus is not None:
        return refus
    config = mailer.enregistrer_config(db, donnees.dict())
    logging.info("[courriel] réglages modifiés par %s",
                 getattr(user, "username", "?"))
    return {"config": mailer.config_publique(config),
            "message": "Réglages enregistrés. Envoyez-vous un essai pour "
                       "vérifier qu'ils fonctionnent."}


@router.post("/essai")
def essai(donnees: EssaiEntree, user=Depends(get_current_user),
          db: Session = Depends(get_db)):
    """Envoie un message d'essai. C'est le seul moyen honnête de dire au
    commerçant que sa configuration marche : un réglage qui « a l'air bon » ne
    prouve rien."""
    refus = _exiger_admin(user)
    if refus is not None:
        return refus

    config = mailer.charger_config(db)
    resultat = mailer.envoyer(
        config, donnees.destinataire,
        "Essai d'envoi — Stock",
        "Ce message confirme que votre boutique peut envoyer des e-mails.\n\n"
        "Vos factures et vos devis partiront depuis cette adresse, avec le PDF "
        "en pièce jointe.\n\n— Stock")
    if not resultat["envoye"]:
        return JSONResponse(status_code=400, content={
            "error": "essai_echoue", "message": resultat["erreur"]})
    return {"message": f"Message d'essai envoyé à {donnees.destinataire}. "
                       "Vérifiez la boîte de réception, et les indésirables."}
