"""Confirmation de paiement d'une commande boutique.

Ce point d'entrée est appelé par le site marchand après qu'un agrégateur
(Bictorys) lui a notifié un paiement réussi. Il ne s'adresse pas à un humain :
il n'y a pas de session, l'appel est authentifié par un secret partagé.

Pourquoi un endpoint dédié plutôt que l'API d'administration : celle-ci exige un
utilisateur connecté, ce qu'un serveur appelant un autre serveur ne peut pas
fournir. Et lui ouvrir une session de service donnerait à un appel automatique
tous les droits d'un administrateur, là où il n'a besoin que de marquer une
commande payée.
"""

from __future__ import annotations

import hmac
import logging
import os
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import ShopOrder, get_db
from .shop import _apply_stock_movement, get_setting

router = APIRouter(prefix="/api/shop/payments", tags=["boutique - paiement"])

# Écart toléré entre le montant payé et le total de la commande, en francs.
# Un agrégateur peut arrondir ; au-delà, on refuse plutôt que de valider une
# commande sous-payée.
TOLERANCE_XOF = Decimal("1")


class ConfirmationPaiement(BaseModel):
    """Ce que le site transmet après notification de l'agrégateur."""

    reference: str = Field(..., description="Numéro de commande (order_number)")
    amount: Decimal = Field(..., description="Montant effectivement encaissé")
    provider: str = Field("bictorys", max_length=40)
    provider_ref: Optional[str] = Field(None, max_length=120)


def _secret_attendu() -> str:
    secret = (os.getenv("SHOP_PAYMENT_SECRET") or "").strip()
    if not secret:
        # Refuser plutôt que d'accepter : un secret absent laisserait n'importe
        # qui marquer les commandes payées.
        raise HTTPException(
            status_code=503,
            detail="SHOP_PAYMENT_SECRET n'est pas configuré côté stock.",
        )
    return secret


@router.post("/confirm")
def confirmer_paiement(
    donnees: ConfirmationPaiement,
    db: Session = Depends(get_db),
    x_payment_secret: Optional[str] = Header(None, alias="X-Payment-Secret"),
):
    """Marque une commande payée et déclenche la sortie de stock.

    Trois vérifications avant d'écrire quoi que ce soit : le secret, l'existence
    de la commande, et **le montant**. Cette dernière est la plus importante :
    sans elle, un appel forgé annoncerait un paiement de 100 F pour une commande
    de 500 000 F, et la commande partirait en préparation.
    """
    attendu = _secret_attendu()

    # Comparaison à temps constant : une comparaison ordinaire laisse fuir la
    # longueur du préfixe correct par le temps de réponse.
    if not x_payment_secret or not hmac.compare_digest(x_payment_secret, attendu):
        logging.warning("[paiement] secret invalide pour %s", donnees.reference)
        raise HTTPException(status_code=401, detail="Secret invalide.")

    commande = (
        db.query(ShopOrder)
        .filter(ShopOrder.order_number == donnees.reference.strip())
        .first()
    )
    if not commande:
        raise HTTPException(status_code=404, detail="Commande introuvable.")

    # Idempotence : un agrégateur réémet ses notifications jusqu'à recevoir un
    # accusé. Rejouer ne doit ni déduire le stock deux fois, ni échouer.
    if (commande.payment_status or "").strip() == "payé":
        return {
            "reference": commande.order_number,
            "payment_status": commande.payment_status,
            "status": commande.status,
            "deja_traite": True,
        }

    total = Decimal(str(commande.total or 0))
    if (total - Decimal(str(donnees.amount))) > TOLERANCE_XOF:
        logging.warning(
            "[paiement] montant insuffisant pour %s : %s reçu, %s attendu",
            donnees.reference, donnees.amount, total,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Montant insuffisant : {donnees.amount} reçu pour {total} attendu.",
        )

    commande.payment_status = "payé"
    if donnees.provider_ref:
        note = f"Paiement {donnees.provider} — référence {donnees.provider_ref}."
        commande.internal_notes = (
            f"{commande.internal_notes}\n{note}" if commande.internal_notes else note
        )

    # Une commande payée est confirmée : c'est ce passage qui sort le stock,
    # en mode « confirm » comme le fait l'écran d'administration.
    if (commande.status or "").strip() == "en attente":
        mode = (get_setting(db, "stock_mode", "confirm") or "confirm").strip()
        commande.status = "confirmée"
        if mode == "confirm":
            _apply_stock_movement(db, commande, sign=-1)

    db.commit()
    db.refresh(commande)

    logging.info(
        "[paiement] commande %s payée (%s, %s)",
        commande.order_number, donnees.provider, donnees.amount,
    )
    return {
        "reference": commande.order_number,
        "payment_status": commande.payment_status,
        "status": commande.status,
        "deja_traite": False,
    }
