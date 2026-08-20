"""Déclinaisons — tailles, couleurs, contenances, avec un stock chacune.

Le modèle nécessaire existait déjà et dormait : `Category.requires_variants`
déclare qu'une catégorie se décline, `CategoryAttribute` porte la grille
(Taille : S…XXL, Couleur : Noir, Blanc…), `ProductVariant.quantity` accueille un
stock par combinaison, et la facturation sait déjà décrémenter ce stock
(`_reserve_variant` dans invoices.py). Deux choses manquaient, et ce module s'en
occupe :

1. **La référence obligatoire.** `ProductVariant.imei_serial` est unique et non
   nul : sans aide, le commerçant devait inventer un code pour chacune de ses
   combinaisons. Elle est désormais engendrée (`shop_profile.reference_declinaison`).

2. **La saisie une par une.** Six tailles et quatre couleurs font vingt-quatre
   lignes à remplir à la main. On engendre la grille d'un coup.

**Où est le stock.** Pour un produit à déclinaisons, la vérité est la somme des
quantités de ses combinaisons — c'est déjà ce que renvoie `variants_available` et
ce que la facturation décrémente. `Product.quantity` n'est donc **pas** touché
ici : le mettre à jour en parallèle donnerait deux compteurs pour un seul stock,
et c'est toujours le second qui se trompe.
"""

from __future__ import annotations

import logging
from itertools import product as produit_cartesien
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import shop_profile
from ..auth import get_current_user
from ..database import (Category, CategoryAttribute, Product, ProductVariant,
                        ProductVariantAttribute, get_db)

router = APIRouter(prefix="/api/declinaisons", tags=["declinaisons"])

# Six tailles × quatre couleurs × trois matières font déjà 72 lignes. Au-delà,
# c'est presque toujours une fausse manœuvre, et la page devient illisible.
MAX_COMBINAISONS = 200


class GrilleEntree(BaseModel):
    # {"Taille": ["S", "M"], "Couleur": ["Noir", "Blanc"]}
    attributs: Dict[str, List[str]]
    quantite: int = 0
    prix: Optional[float] = None


class QuantiteEntree(BaseModel):
    quantite: int
    motif: Optional[str] = None


def _attributs_de(variante: ProductVariant) -> List[tuple]:
    return [(a.attribute_name, a.attribute_value)
            for a in (variante.attributes or [])]


def _fiche(variante: ProductVariant) -> dict:
    couples = _attributs_de(variante)
    return {
        "variant_id": variante.variant_id,
        "reference": variante.imei_serial,
        "etiquette": shop_profile.etiquette_declinaison(couples),
        "attributs": {nom: valeur for nom, valeur in couples},
        # Nul veut dire « suivi à l'exemplaire » (is_sold), pas « stock zéro ».
        "quantite": (int(variante.quantity)
                     if variante.quantity is not None else None),
        "suivi_a_l_exemplaire": variante.quantity is None,
        "is_sold": bool(variante.is_sold),
        "prix": float(variante.price) if variante.price is not None else None,
    }


def _signature(couples) -> tuple:
    """Clé de comparaison d'une combinaison, insensible à l'ordre et à la casse.
    Deux déclinaisons « Taille M / Couleur Rouge » et « couleur rouge / taille m »
    sont la même chose et ne doivent pas coexister."""
    return tuple(sorted((str(n).strip().lower(), str(v).strip().lower())
                        for n, v in couples))


def _grille_de_la_categorie(db: Session, nom_categorie: Optional[str]) -> List[dict]:
    """La grille déclarée sur la catégorie du produit : ce que le profil de
    boutique a semé, et que l'administrateur a pu ajuster."""
    if not nom_categorie:
        return []
    categorie = (db.query(Category)
                 .filter(Category.name == nom_categorie).first())
    if categorie is None:
        return []
    attributs = (db.query(CategoryAttribute)
                 .filter(CategoryAttribute.category_id == categorie.category_id)
                 .order_by(CategoryAttribute.sort_order,
                           CategoryAttribute.attribute_id)
                 .all())
    return [{
        "nom": a.name,
        "type": a.type,
        "requis": bool(a.required),
        "valeurs": [v.value for v in a.values],
    } for a in attributs]


@router.get("/produit/{product_id}")
def declinaisons_du_produit(product_id: int,
                            user=Depends(get_current_user),
                            db: Session = Depends(get_db)):
    """Les combinaisons existantes, et la grille disponible pour en ajouter."""
    produit = db.query(Product).filter(Product.product_id == product_id).first()
    if produit is None:
        return JSONResponse(status_code=404, content={
            "error": "produit_inconnu", "message": "Ce produit n'existe pas."})

    variantes = (db.query(ProductVariant)
                 .filter(ProductVariant.product_id == product_id)
                 .order_by(ProductVariant.variant_id)
                 .all())
    fiches = [_fiche(v) for v in variantes]
    comptees = [f for f in fiches if f["quantite"] is not None]

    return {
        "produit": {
            "product_id": produit.product_id,
            "nom": produit.name,
            "categorie": produit.category,
            "has_unique_serial": bool(produit.has_unique_serial),
            "unite": shop_profile.unite(produit.unit)["abrege"],
        },
        "grille": _grille_de_la_categorie(db, produit.category),
        "declinaisons": fiches,
        # Somme des combinaisons comptées : c'est le stock réel du produit.
        "stock": sum(f["quantite"] for f in comptees if not f["is_sold"]),
        "mots": {
            "variante": shop_profile.libelle("variante", db),
            "variantes": shop_profile.libelle("variantes", db),
            "identifiant": shop_profile.libelle("identifiant", db),
        },
    }


@router.post("/produit/{product_id}/grille")
def engendrer_grille(product_id: int, donnees: GrilleEntree,
                     user=Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Crée toutes les combinaisons demandées d'un coup.

    Les combinaisons déjà présentes sont laissées telles quelles et signalées :
    régénérer une grille après avoir ajouté une couleur ne doit pas remettre à
    zéro le stock des tailles déjà en rayon.
    """
    produit = db.query(Product).filter(Product.product_id == product_id).first()
    if produit is None:
        return JSONResponse(status_code=404, content={
            "error": "produit_inconnu", "message": "Ce produit n'existe pas."})

    if produit.has_unique_serial:
        return JSONResponse(status_code=400, content={
            "error": "produit_serialise",
            "message": f"« {produit.name} » est suivi par numéro de série : "
                       "chaque exemplaire s'ajoute individuellement, il n'y a "
                       "pas de grille à engendrer."})

    # On garde l'ordre des attributs tel qu'il arrive : il décide de l'ordre des
    # morceaux dans la référence engendrée, et donc de sa lisibilité.
    axes = [(nom, [v for v in valeurs if str(v).strip()])
            for nom, valeurs in donnees.attributs.items()
            if str(nom).strip() and valeurs]
    if not axes:
        return JSONResponse(status_code=400, content={
            "error": "grille_vide",
            "message": "Choisissez au moins une valeur, par exemple une taille "
                       "ou une couleur."})

    total = 1
    for _, valeurs in axes:
        total *= len(valeurs)
    if total > MAX_COMBINAISONS:
        return JSONResponse(status_code=400, content={
            "error": "grille_trop_grande",
            "message": f"{total} combinaisons demandées, {MAX_COMBINAISONS} au "
                       "maximum. Créez-les par groupes — par couleur, par "
                       "exemple."})

    quantite = max(0, int(donnees.quantite or 0))
    deja = {_signature(_attributs_de(v))
            for v in db.query(ProductVariant)
            .filter(ProductVariant.product_id == product_id).all()}

    creees, ignorees = [], []
    for combinaison in produit_cartesien(*[valeurs for _, valeurs in axes]):
        couples = list(zip([nom for nom, _ in axes], combinaison))
        if _signature(couples) in deja:
            ignorees.append(shop_profile.etiquette_declinaison(couples))
            continue

        variante = ProductVariant(
            product_id=product_id,
            imei_serial=_reference_libre(db, product_id, couples),
            quantity=quantite,
            condition=produit.condition,
            price=donnees.prix,
        )
        db.add(variante)
        db.flush()  # variant_id nécessaire pour les attributs
        for nom, valeur in couples:
            db.add(ProductVariantAttribute(
                variant_id=variante.variant_id,
                attribute_name=nom,
                attribute_value=valeur))
        deja.add(_signature(couples))
        creees.append(variante)

    if creees and quantite:
        _tracer_reception(db, product_id, len(creees) * quantite,
                          f"Création de {len(creees)} déclinaison(s) à "
                          f"{quantite} unité(s)")

    db.commit()
    for v in creees:
        db.refresh(v)

    logging.info("[declinaisons] %s combinaison(s) créée(s) sur le produit %s "
                 "par %s", len(creees), product_id,
                 getattr(user, "username", "?"))

    return {
        "creees": [_fiche(v) for v in creees],
        "ignorees": ignorees,
        "message": _resume_grille(len(creees), ignorees, quantite,
                                  produit.name),
    }


def _resume_grille(nombre: int, ignorees: list, quantite: int,
                   nom_produit: str) -> str:
    morceaux = []
    if nombre:
        morceaux.append(
            f"{nombre} déclinaison(s) créée(s) sur « {nom_produit} »"
            + (f", à {quantite} unité(s) chacune." if quantite
               else ", à zéro — renseignez le stock au fur et à mesure."))
    else:
        morceaux.append("Aucune nouvelle combinaison.")
    if ignorees:
        apercu = ", ".join(ignorees[:5]) + ("…" if len(ignorees) > 5 else "")
        morceaux.append(f"{len(ignorees)} existai(en)t déjà et n'ont pas été "
                        f"touchée(s) : {apercu}.")
    return " ".join(morceaux)


def _reference_libre(db: Session, product_id: int, couples) -> str:
    """Référence engendrée, en s'écartant si elle est déjà prise.

    Le cas se présente quand deux valeurs différentes donnent le même code
    technique — « Bleu ciel » et « Bleu-ciel », par exemple.
    """
    for rang in range(0, 50):
        reference = shop_profile.reference_declinaison(product_id, couples, rang)
        if db.query(ProductVariant).filter(
                ProductVariant.imei_serial == reference).first() is None:
            return reference
    return shop_profile.reference_declinaison(product_id, couples, 50)


def _tracer_reception(db: Session, product_id: int, quantite: int,
                      note: str) -> None:
    """Laisse une ligne dans l'historique des mouvements, sans toucher au
    compteur `Product.quantity` — pour un produit à déclinaisons, le stock est
    la somme des combinaisons."""
    from .stock_movements import create_stock_movement_entry
    create_stock_movement_entry(
        db, product_id=product_id, quantity=quantite, movement_type="IN",
        reference_type="DECLINAISON", notes=note)


@router.put("/{variant_id}/quantite")
def regler_quantite(variant_id: int, donnees: QuantiteEntree,
                    user=Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Corrige le stock d'une combinaison — réception, casse, inventaire.

    Un mouvement de stock est enregistré pour la différence, afin que
    l'historique explique l'écart plutôt que de le subir.
    """
    variante = (db.query(ProductVariant)
                .filter(ProductVariant.variant_id == variant_id).first())
    if variante is None:
        return JSONResponse(status_code=404, content={
            "error": "declinaison_inconnue",
            "message": "Cette déclinaison n'existe pas."})

    if variante.quantity is None:
        return JSONResponse(status_code=400, content={
            "error": "suivi_a_l_exemplaire",
            "message": "Cette déclinaison est suivie à l'exemplaire (vendue ou "
                       "non), elle n'a pas de quantité à régler."})

    nouvelle = int(donnees.quantite or 0)
    if nouvelle < 0:
        return JSONResponse(status_code=400, content={
            "error": "quantite_negative",
            "message": "Une quantité ne peut pas être négative."})

    avant = int(variante.quantity or 0)
    variation = nouvelle - avant
    if variation:
        from .stock_movements import create_stock_movement_entry
        etiquette = shop_profile.etiquette_declinaison(
            _attributs_de(variante))
        create_stock_movement_entry(
            db, product_id=variante.product_id, quantity=abs(variation),
            movement_type="IN" if variation > 0 else "OUT",
            reference_type="DECLINAISON",
            notes=f"{etiquette} : {avant} → {nouvelle}"
                  + (f" ({donnees.motif})" if donnees.motif else ""))
    variante.quantity = nouvelle
    db.commit()
    db.refresh(variante)

    return {"declinaison": _fiche(variante),
            "avant": avant, "apres": nouvelle}


@router.delete("/{variant_id}")
def supprimer_declinaison(variant_id: int,
                          user=Depends(get_current_user),
                          db: Session = Depends(get_db)):
    """Retire une combinaison que la boutique ne fait plus.

    Refusé si elle porte encore du stock : la faire disparaître ferait
    disparaître ce stock sans trace. Mettez la quantité à zéro d'abord, ce qui
    laisse un mouvement expliquant où sont passées les unités.
    """
    variante = (db.query(ProductVariant)
                .filter(ProductVariant.variant_id == variant_id).first())
    if variante is None:
        return JSONResponse(status_code=404, content={
            "error": "declinaison_inconnue",
            "message": "Cette déclinaison n'existe pas."})

    if variante.quantity is not None and int(variante.quantity) > 0:
        return JSONResponse(status_code=400, content={
            "error": "stock_restant",
            "message": f"Cette déclinaison porte encore "
                       f"{int(variante.quantity)} unité(s). Mettez sa quantité "
                       "à zéro avant de la retirer, pour que l'historique "
                       "explique où elles sont passées."})

    etiquette = shop_profile.etiquette_declinaison(_attributs_de(variante))
    db.delete(variante)   # les attributs suivent par cascade
    db.commit()
    return {"supprimee": True, "etiquette": etiquette}
