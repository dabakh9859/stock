"""API d'administration des variantes commerciales et des prix boutique.

Deux écrans s'appuient dessus :

* **Gestion des variantes** — le vocabulaire (groupes, options, suppléments) et
  les rattachements aux catégories ou aux produits ;
* **Gestion des prix** — le prix boutique de chaque produit et, produit par
  produit, les exceptions sur les suppléments hérités.

Rien ici ne touche au stock : ces tables sont indépendantes de `ProductVariant`
et des attributs de catégorie qui qualifient les IMEI.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import (
    Product,
    ShopProduct,
    ShopVariantAssignment,
    ShopVariantGroup,
    ShopVariantOption,
    ShopVariantOverride,
    get_db,
)
from ..services.shop_variants import resoudre

router = APIRouter(prefix="/api/shop/admin/variants", tags=["boutique-variantes"])


# --------------------------------------------------------------------- schémas

class OptionEntree(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    price_delta: Decimal = Decimal("0")
    sort_order: int = 0
    is_active: bool = True


class GroupeEntree(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    help_text: Optional[str] = Field(None, max_length=160)
    sort_order: int = 0
    is_active: bool = True


class RattachementEntree(BaseModel):
    target_type: str = Field(..., pattern="^(category|product)$")
    category_name: Optional[str] = None
    product_id: Optional[int] = None


class RattachementLotEntree(BaseModel):
    """Rattache un groupe à plusieurs cibles d'un coup.

    Les deux listes peuvent être fournies ensemble : on peut viser une
    catégorie entière et quelques produits isolés dans la même opération.
    """
    category_names: list[str] = []
    product_ids: list[int] = []


class SurchargeEntree(BaseModel):
    option_id: int
    price_delta: Optional[Decimal] = None
    is_hidden: bool = False


class PrixEntree(BaseModel):
    """Prix boutique d'un produit. `None` = on retombe sur le prix du stock."""
    shop_price: Optional[Decimal] = None
    old_price: Optional[Decimal] = None


def _groupe_ou_404(db: Session, group_id: int) -> ShopVariantGroup:
    groupe = db.query(ShopVariantGroup).filter(
        ShopVariantGroup.group_id == group_id).first()
    if groupe is None:
        raise HTTPException(status_code=404, detail="Groupe de variantes introuvable")
    return groupe


def _serialiser_groupe(groupe: ShopVariantGroup) -> dict:
    return {
        "group_id": groupe.group_id,
        "name": groupe.name,
        "help_text": groupe.help_text,
        "sort_order": groupe.sort_order,
        "is_active": groupe.is_active,
        "options": [
            {
                "option_id": o.option_id,
                "label": o.label,
                "price_delta": float(o.price_delta or 0),
                "sort_order": o.sort_order,
                "is_active": o.is_active,
            }
            for o in groupe.options
        ],
        "assignments": [
            {
                "assignment_id": a.assignment_id,
                "target_type": a.target_type,
                "category_name": a.category_name,
                "product_id": a.product_id,
            }
            for a in groupe.assignments
        ],
    }


# ---------------------------------------------------------------- les groupes

@router.get("")
async def lister_groupes(db: Session = Depends(get_db), _=Depends(get_current_user)):
    groupes = (
        db.query(ShopVariantGroup)
        .order_by(ShopVariantGroup.sort_order, ShopVariantGroup.group_id)
        .all()
    )
    return [_serialiser_groupe(g) for g in groupes]


@router.post("")
async def creer_groupe(entree: GroupeEntree, db: Session = Depends(get_db),
                       _=Depends(get_current_user)):
    groupe = ShopVariantGroup(**entree.model_dump())
    db.add(groupe)
    db.commit()
    db.refresh(groupe)
    return _serialiser_groupe(groupe)


@router.put("/{group_id}")
async def modifier_groupe(group_id: int, entree: GroupeEntree,
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    groupe = _groupe_ou_404(db, group_id)
    for champ, valeur in entree.model_dump().items():
        setattr(groupe, champ, valeur)
    db.commit()
    db.refresh(groupe)
    return _serialiser_groupe(groupe)


@router.delete("/{group_id}")
async def supprimer_groupe(group_id: int, db: Session = Depends(get_db),
                           _=Depends(get_current_user)):
    groupe = _groupe_ou_404(db, group_id)
    db.delete(groupe)          # options et rattachements suivent en cascade
    db.commit()
    return {"message": "Groupe supprimé"}


# ---------------------------------------------------------------- les options

@router.post("/{group_id}/options")
async def ajouter_option(group_id: int, entree: OptionEntree,
                         db: Session = Depends(get_db), _=Depends(get_current_user)):
    _groupe_ou_404(db, group_id)
    doublon = (
        db.query(ShopVariantOption)
        .filter(ShopVariantOption.group_id == group_id,
                func.lower(ShopVariantOption.label) == entree.label.strip().lower())
        .first()
    )
    if doublon:
        raise HTTPException(status_code=400,
                            detail=f"L'option « {entree.label} » existe déjà dans ce groupe")

    # Sans position explicite, la nouvelle option va en fin de liste : elle ne
    # doit surtout pas se glisser en tête, où elle deviendrait l'option
    # présélectionnée sur le site et changerait le prix affiché.
    if not entree.sort_order:
        dernier = (
            db.query(func.coalesce(func.max(ShopVariantOption.sort_order), 0))
            .filter(ShopVariantOption.group_id == group_id).scalar()
        )
        entree.sort_order = int(dernier) + 1

    option = ShopVariantOption(group_id=group_id, **entree.model_dump())
    db.add(option)
    db.commit()
    db.refresh(option)
    return {"option_id": option.option_id, "label": option.label,
            "price_delta": float(option.price_delta or 0),
            "sort_order": option.sort_order, "is_active": option.is_active}


@router.put("/options/{option_id}")
async def modifier_option(option_id: int, entree: OptionEntree,
                          db: Session = Depends(get_db), _=Depends(get_current_user)):
    option = db.query(ShopVariantOption).filter(
        ShopVariantOption.option_id == option_id).first()
    if option is None:
        raise HTTPException(status_code=404, detail="Option introuvable")
    for champ, valeur in entree.model_dump().items():
        setattr(option, champ, valeur)
    db.commit()
    return {"message": "Option modifiée"}


@router.delete("/options/{option_id}")
async def supprimer_option(option_id: int, db: Session = Depends(get_db),
                           _=Depends(get_current_user)):
    option = db.query(ShopVariantOption).filter(
        ShopVariantOption.option_id == option_id).first()
    if option is None:
        raise HTTPException(status_code=404, detail="Option introuvable")
    db.delete(option)
    db.commit()
    return {"message": "Option supprimée"}


@router.post("/options/reorder")
async def reordonner_options(ordre: list[int], db: Session = Depends(get_db),
                             _=Depends(get_current_user)):
    """Fixe l'ordre des options d'un groupe — la première est celle par défaut."""
    for position, option_id in enumerate(ordre):
        db.query(ShopVariantOption).filter(
            ShopVariantOption.option_id == option_id
        ).update({"sort_order": position})
    db.commit()
    return {"message": "Ordre enregistré"}


# ----------------------------------------------------------- les rattachements

@router.post("/{group_id}/assignments")
async def rattacher(group_id: int, entree: RattachementEntree,
                    db: Session = Depends(get_db), _=Depends(get_current_user)):
    _groupe_ou_404(db, group_id)

    if entree.target_type == "category" and not entree.category_name:
        raise HTTPException(status_code=400, detail="Catégorie manquante")
    if entree.target_type == "product" and not entree.product_id:
        raise HTTPException(status_code=400, detail="Produit manquant")

    existe = (
        db.query(ShopVariantAssignment)
        .filter(ShopVariantAssignment.group_id == group_id,
                ShopVariantAssignment.target_type == entree.target_type,
                ShopVariantAssignment.category_name == entree.category_name,
                ShopVariantAssignment.product_id == entree.product_id)
        .first()
    )
    if existe:
        raise HTTPException(status_code=400, detail="Ce rattachement existe déjà")

    rattachement = ShopVariantAssignment(group_id=group_id, **entree.model_dump())
    db.add(rattachement)
    db.commit()
    db.refresh(rattachement)
    return {"assignment_id": rattachement.assignment_id}


@router.post("/{group_id}/assignments/bulk")
async def rattacher_en_lot(group_id: int, entree: RattachementLotEntree,
                           db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Rattache un groupe à plusieurs produits et/ou catégories.

    Les cibles déjà rattachées sont ignorées sans erreur : sélectionner
    vingt produits dont trois étaient déjà liés doit ajouter les dix-sept
    autres, pas échouer sur les trois.
    """
    _groupe_ou_404(db, group_id)

    if not entree.category_names and not entree.product_ids:
        raise HTTPException(status_code=400, detail="Aucune cible sélectionnée")

    existants = {
        (a.target_type, a.category_name, a.product_id)
        for a in db.query(ShopVariantAssignment)
        .filter(ShopVariantAssignment.group_id == group_id).all()
    }

    ajoutes = 0
    ignores = 0

    for nom in entree.category_names:
        cle = ("category", nom, None)
        if cle in existants:
            ignores += 1
            continue
        db.add(ShopVariantAssignment(group_id=group_id, target_type="category",
                                     category_name=nom, product_id=None))
        existants.add(cle)
        ajoutes += 1

    # Un produit inexistant ferait échouer la transaction entière au commit :
    # on filtre en amont sur ce qui existe réellement.
    if entree.product_ids:
        connus = {
            pid for (pid,) in db.query(Product.product_id)
            .filter(Product.product_id.in_(entree.product_ids)).all()
        }
        for pid in entree.product_ids:
            if pid not in connus:
                ignores += 1
                continue
            cle = ("product", None, pid)
            if cle in existants:
                ignores += 1
                continue
            db.add(ShopVariantAssignment(group_id=group_id, target_type="product",
                                         category_name=None, product_id=pid))
            existants.add(cle)
            ajoutes += 1

    db.commit()
    return {"ajoutes": ajoutes, "ignores": ignores}


@router.delete("/assignments/{assignment_id}")
async def detacher(assignment_id: int, db: Session = Depends(get_db),
                   _=Depends(get_current_user)):
    rattachement = db.query(ShopVariantAssignment).filter(
        ShopVariantAssignment.assignment_id == assignment_id).first()
    if rattachement is None:
        raise HTTPException(status_code=404, detail="Rattachement introuvable")
    db.delete(rattachement)
    db.commit()
    return {"message": "Rattachement supprimé"}


# ------------------------------------------------------- côté produit / prix

@router.get("/product/{product_id}")
async def variantes_du_produit(product_id: int, db: Session = Depends(get_db),
                               _=Depends(get_current_user)):
    """Ce qu'un produit reçoit réellement, surcharges appliquées.

    Sert aux deux écrans : c'est exactement ce que verra le client sur le site.
    """
    produit = db.query(Product).filter(Product.product_id == product_id).first()
    if produit is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")

    reglages = db.query(ShopProduct).filter(
        ShopProduct.product_id == product_id).first()
    resolution = resoudre(db, produit, reglages)

    return {
        "product_id": product_id,
        "name": produit.name,
        "category": produit.category,
        "prix_stock": float(produit.price or 0),
        "shop_price": float(reglages.shop_price) if reglages and reglages.shop_price is not None else None,
        "old_price": float(reglages.old_price) if reglages and reglages.old_price is not None else None,
        **resolution,
    }


@router.put("/product/{product_id}/price")
async def definir_prix(product_id: int, entree: PrixEntree,
                       db: Session = Depends(get_db), _=Depends(get_current_user)):
    produit = db.query(Product).filter(Product.product_id == product_id).first()
    if produit is None:
        raise HTTPException(status_code=404, detail="Produit introuvable")

    reglages = db.query(ShopProduct).filter(
        ShopProduct.product_id == product_id).first()
    if reglages is None:
        # `is_published` doit être posé explicitement : la colonne n'a aucune
        # valeur par défaut en base, et un NULL retirerait le produit de la
        # boutique alors qu'on voulait seulement fixer son prix.
        reglages = ShopProduct(product_id=product_id, is_published=True, is_featured=False)
        db.add(reglages)

    reglages.shop_price = entree.shop_price
    reglages.old_price = entree.old_price
    db.commit()
    return {"message": "Prix enregistré"}


@router.put("/product/{product_id}/override")
async def definir_surcharge(product_id: int, entree: SurchargeEntree,
                            db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Exception sur une option pour ce produit : autre supplément, ou masquée."""
    option = db.query(ShopVariantOption).filter(
        ShopVariantOption.option_id == entree.option_id).first()
    if option is None:
        raise HTTPException(status_code=404, detail="Option introuvable")

    surcharge = (
        db.query(ShopVariantOverride)
        .filter(ShopVariantOverride.product_id == product_id,
                ShopVariantOverride.option_id == entree.option_id)
        .first()
    )

    # Ni prix particulier ni masquage : la surcharge n'a plus de raison d'être,
    # le produit reprend simplement la valeur de son groupe.
    if entree.price_delta is None and not entree.is_hidden:
        if surcharge is not None:
            db.delete(surcharge)
            db.commit()
        return {"message": "Le produit reprend la valeur du groupe"}

    if surcharge is None:
        surcharge = ShopVariantOverride(product_id=product_id, option_id=entree.option_id)
        db.add(surcharge)
    surcharge.price_delta = entree.price_delta
    surcharge.is_hidden = entree.is_hidden
    db.commit()
    return {"message": "Exception enregistrée"}


@router.get("/products/search")
async def chercher_produits(q: str = Query("", max_length=100), limite: int = 20,
                            db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Recherche de produits pour les deux écrans."""
    requete = db.query(Product).filter(
        or_(Product.is_archived.is_(False), Product.is_archived.is_(None)))
    if q.strip():
        motif = f"%{q.strip()}%"
        requete = requete.filter(or_(Product.name.ilike(motif),
                                     Product.brand.ilike(motif),
                                     Product.model.ilike(motif)))
    produits = requete.order_by(Product.name).limit(min(limite, 50)).all()

    reglages = {
        r.product_id: r for r in
        db.query(ShopProduct).filter(
            ShopProduct.product_id.in_([p.product_id for p in produits])).all()
    } if produits else {}

    return [
        {
            "product_id": p.product_id,
            "name": p.name,
            "category": p.category,
            "prix_stock": float(p.price or 0),
            "shop_price": (float(reglages[p.product_id].shop_price)
                           if p.product_id in reglages and reglages[p.product_id].shop_price is not None
                           else None),
        }
        for p in produits
    ]


@router.get("/categories")
async def lister_categories(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Catégories réellement portées par des produits, pour le rattachement."""
    lignes = (
        db.query(Product.category, func.count(Product.product_id))
        .filter(Product.category.isnot(None), Product.category != "")
        .group_by(Product.category)
        .order_by(Product.category)
        .all()
    )
    return [{"name": nom, "nb_produits": nb} for nom, nb in lignes]
