"""Lots et dates de péremption — le suivi qu'attend une supérette.

Un lot est une réception : tant d'unités d'un produit, arrivées ensemble, avec
une date limite commune. Il est enregistré comme un `ProductVariant` — la
machinerie existe déjà, et le profil « alimentation » nomme d'ailleurs les
déclinaisons « Lots ».

**Ce que ce module fait et ne fait pas.** Créer un lot incrémente le stock du
produit, comme toute réception de marchandise, et laisse une ligne dans
l'historique des mouvements. En revanche, une vente ne décrémente pas un lot en
particulier : l'application ne sait pas lequel le vendeur a pris sur l'étagère.
La quantité d'un lot dit donc **ce qui est arrivé**, pas ce qui reste. C'est
suffisant pour ce qui compte ici — savoir quoi aller vérifier en rayon avant que
ça ne périme — et c'est honnête : un décompte par lot que personne ne tient
serait faux au bout d'une semaine.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import shop_profile
from ..auth import get_current_user
from ..database import Product, ProductVariant, get_db

router = APIRouter(prefix="/api/lots", tags=["lots"])

# Au-delà, ce n'est plus une alerte mais l'inventaire complet.
JOURS_MAX = 365
JOURS_DEFAUT = 30


class LotEntree(BaseModel):
    lot_number: Optional[str] = None
    expiry_date: Optional[date] = None
    quantity: int = 0
    purchase_price: Optional[float] = None


def _fiche(variante: ProductVariant, produit: Optional[Product] = None,
           aujourdhui: Optional[date] = None) -> dict:
    aujourdhui = aujourdhui or date.today()
    limite = variante.expiry_date
    jours = (limite - aujourdhui).days if limite else None
    return {
        "variant_id": variante.variant_id,
        "product_id": variante.product_id,
        "produit": produit.name if produit is not None else None,
        "reference": variante.imei_serial,
        "lot_number": variante.lot_number,
        "expiry_date": limite.isoformat() if limite else None,
        "jours_restants": jours,
        "perime": jours is not None and jours < 0,
        "quantity": int(variante.quantity or 0),
        "unite": shop_profile.unite(
            produit.unit if produit is not None else None)["abrege"],
        # Accord fait ici plutôt qu'au navigateur : « 6 bouteilles » mais
        # « 6 kg », et la règle est déjà écrite et éprouvée côté serveur.
        "quantite_lisible": shop_profile.quantite_lisible(
            int(variante.quantity or 0),
            produit.unit if produit is not None else None),
        "is_sold": bool(variante.is_sold),
    }


def _rang_libre(db: Session, produit_id: int, lot: Optional[str],
                peremption: Optional[date]) -> str:
    """Référence unique, en s'écartant si la boutique reçoit deux fois le même
    lot à la même date (réassort du même carton, par exemple)."""
    for rang in range(0, 50):
        reference = shop_profile.reference_lot(produit_id, lot, peremption, rang)
        existe = (db.query(ProductVariant)
                  .filter(ProductVariant.imei_serial == reference)
                  .first())
        if existe is None:
            return reference
    # 50 lots identiques le même jour : plutôt que d'échouer en silence, on
    # laisse la contrainte d'unicité parler.
    return shop_profile.reference_lot(produit_id, lot, peremption, 50)


@router.get("/produit/{product_id}")
def lots_du_produit(product_id: int,
                    user=Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Les lots d'un produit, du plus proche de la péremption au plus lointain.
    Les lots sans date passent en dernier."""
    produit = db.query(Product).filter(Product.product_id == product_id).first()
    if produit is None:
        return JSONResponse(status_code=404, content={
            "error": "produit_inconnu",
            "message": "Ce produit n'existe pas."})

    variantes = (db.query(ProductVariant)
                 .filter(ProductVariant.product_id == product_id)
                 .all())
    aujourdhui = date.today()
    fiches = [_fiche(v, produit, aujourdhui) for v in variantes]
    fiches.sort(key=lambda f: (f["expiry_date"] is None, f["expiry_date"] or ""))
    return {
        "produit": {"product_id": produit.product_id, "nom": produit.name,
                    "quantity": int(produit.quantity or 0),
                    "unit": produit.unit or shop_profile.UNITE_DEFAUT},
        "lots": fiches,
        "total_recu": sum(f["quantity"] for f in fiches),
    }


@router.post("/produit/{product_id}")
def creer_lot(product_id: int, donnees: LotEntree,
              user=Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Enregistre une réception : le lot est créé et le stock du produit monte
    d'autant, avec sa ligne dans l'historique des mouvements."""
    from .stock_movements import create_stock_movement_entry

    produit = db.query(Product).filter(Product.product_id == product_id).first()
    if produit is None:
        return JSONResponse(status_code=404, content={
            "error": "produit_inconnu",
            "message": "Ce produit n'existe pas."})

    quantite = int(donnees.quantity or 0)
    if quantite <= 0:
        return JSONResponse(status_code=400, content={
            "error": "quantite_invalide",
            "message": "Indiquez le nombre d'unités reçues dans ce lot."})

    # Un produit suivi par numéro de série unique se remplit exemplaire par
    # exemplaire : y ajouter un lot compté ferait deux façons de compter le même
    # stock.
    if produit.has_unique_serial:
        return JSONResponse(status_code=400, content={
            "error": "produit_serialise",
            "message": f"« {produit.name} » est suivi par numéro de série : "
                       "ajoutez les exemplaires un par un depuis la fiche "
                       "produit."})

    if donnees.expiry_date and donnees.expiry_date < date.today():
        # Recevoir un lot déjà périmé est possible (erreur de saisie, ou lot à
        # retourner au fournisseur) : on l'accepte mais on le signale.
        logging.info("[lots] lot déjà périmé enregistré sur le produit %s",
                     product_id)

    variante = ProductVariant(
        product_id=product_id,
        imei_serial=_rang_libre(db, product_id, donnees.lot_number,
                               donnees.expiry_date),
        lot_number=(donnees.lot_number or "").strip()[:100] or None,
        expiry_date=donnees.expiry_date,
        quantity=quantite,
        condition=produit.condition,
        purchase_price=donnees.purchase_price,
    )
    db.add(variante)

    avant = int(produit.quantity or 0)
    create_stock_movement_entry(
        db, product_id=product_id, quantity=quantite, movement_type="IN",
        reference_type="LOT",
        notes=f"Réception lot {donnees.lot_number or '(sans numéro)'}"
              + (f", péremption {donnees.expiry_date.isoformat()}"
                 if donnees.expiry_date else ""),
        unit_price=float(donnees.purchase_price or 0),
    )
    produit.quantity = avant + quantite
    db.commit()
    db.refresh(variante)
    db.refresh(produit)

    return {"lot": _fiche(variante, produit),
            "stock": {"avant": avant, "apres": int(produit.quantity or 0)},
            "message": f"Lot enregistré. Stock de « {produit.name} » : "
                       f"{shop_profile.quantite_lisible(produit.quantity, produit.unit)}."}


@router.put("/{variant_id}")
def modifier_lot(variant_id: int, donnees: LotEntree,
                 user=Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Corrige le numéro de lot ou la date limite.

    La quantité n'est pas modifiable ici : elle est adossée à un mouvement de
    stock déjà enregistré. Pour la corriger, passez par un ajustement de stock,
    qui laisse une trace de la correction.
    """
    variante = (db.query(ProductVariant)
                .filter(ProductVariant.variant_id == variant_id).first())
    if variante is None:
        return JSONResponse(status_code=404, content={
            "error": "lot_inconnu", "message": "Ce lot n'existe pas."})

    variante.lot_number = (donnees.lot_number or "").strip()[:100] or None
    variante.expiry_date = donnees.expiry_date
    db.commit()
    db.refresh(variante)
    produit = db.query(Product).filter(
        Product.product_id == variante.product_id).first()
    return {"lot": _fiche(variante, produit)}


@router.get("/alertes")
def alertes(jours: int = Query(JOURS_DEFAUT, ge=0, le=JOURS_MAX),
            user=Depends(get_current_user),
            db: Session = Depends(get_db)):
    """Les lots périmés et ceux qui approchent de leur date limite.

    Les deux listes sont séparées : un lot périmé se retire du rayon, un lot qui
    approche se solde ou se met en avant. Les mélanger obligerait le commerçant
    à relire les dates une par une.
    """
    aujourdhui = date.today()
    echeance = aujourdhui + timedelta(days=jours)

    lignes = (db.query(ProductVariant, Product)
              .join(Product, Product.product_id == ProductVariant.product_id)
              .filter(ProductVariant.expiry_date.isnot(None),
                      ProductVariant.expiry_date <= echeance,
                      ProductVariant.is_sold == False,  # noqa: E712
                      Product.is_archived == False)     # noqa: E712
              .order_by(ProductVariant.expiry_date)
              .all())

    perimes, bientot = [], []
    for variante, produit in lignes:
        fiche = _fiche(variante, produit, aujourdhui)
        (perimes if fiche["perime"] else bientot).append(fiche)

    return {
        "jours": jours,
        "aujourdhui": aujourdhui.isoformat(),
        "perimes": perimes,
        "bientot": bientot,
        "total": len(perimes) + len(bientot),
        "unites_concernees": sum(f["quantity"] for f in perimes + bientot),
    }


@router.get("/unites")
def unites(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Unités de vente à proposer, selon le métier de la boutique."""
    courant = shop_profile.charger(db)
    return {
        "unites": shop_profile.unites_proposees(db, connu=courant),
        "defaut": shop_profile.UNITE_DEFAUT,
        "toutes": [dict(u) for u in shop_profile.UNITES],
        "actif": shop_profile.module_actif("unites", db, connu=courant),
        "peremption": shop_profile.module_actif("peremption", db, connu=courant),
    }


def compter_alertes(db: Session, jours: int = JOURS_DEFAUT) -> dict:
    """Compte les lots à surveiller, sans les détailler.

    Sert au badge de la barre de navigation et au récapitulatif : une requête de
    comptage plutôt que le chargement de toutes les fiches.
    """
    aujourdhui = date.today()
    base = (db.query(func.count(ProductVariant.variant_id))
            .join(Product, Product.product_id == ProductVariant.product_id)
            .filter(ProductVariant.expiry_date.isnot(None),
                    ProductVariant.is_sold == False,  # noqa: E712
                    Product.is_archived == False))    # noqa: E712
    perimes = base.filter(ProductVariant.expiry_date < aujourdhui).scalar() or 0
    bientot = base.filter(
        ProductVariant.expiry_date >= aujourdhui,
        ProductVariant.expiry_date <= aujourdhui + timedelta(days=jours)
    ).scalar() or 0
    return {"perimes": int(perimes), "bientot": int(bientot),
            "total": int(perimes) + int(bientot)}
