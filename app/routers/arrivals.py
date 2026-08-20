"""
Gestion des arrivages : lots de marchandise reçus, avec suivi des ventes et
récapitulatif financier. Voir services/arrivals_finance.py pour les calculs.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from ..database import get_db, Arrival, ArrivalItem, Product, ProductVariant, Supplier
from ..auth import get_current_user
from ..services.arrivals_finance import compute_arrival_financials

router = APIRouter(prefix="/api/arrivals", tags=["arrivages"])


# --------------------------------------------------------------------------
# Schémas
# --------------------------------------------------------------------------

class ArrivalCreate(BaseModel):
    label: Optional[str] = None
    arrival_date: Optional[str] = None   # ISO (AAAA-MM-JJ) ; défaut = aujourd'hui
    supplier_id: Optional[int] = None
    notes: Optional[str] = None
    reference: Optional[str] = None       # laissé vide = généré


class ArrivalUpdate(BaseModel):
    label: Optional[str] = None
    arrival_date: Optional[str] = None
    supplier_id: Optional[int] = None
    notes: Optional[str] = None


class ArrivalItemIn(BaseModel):
    product_id: int
    quantity: int
    purchase_price: Optional[float] = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Date invalide (format AAAA-MM-JJ)")


def _generate_reference(db: Session, when: date) -> str:
    """Référence lisible et unique : ARR-AAMMJJ-NNN (NNN = rang du jour)."""
    prefix = f"ARR-{when.strftime('%y%m%d')}"
    count_today = db.query(func.count(Arrival.arrival_id)).filter(
        Arrival.reference.like(f"{prefix}%")
    ).scalar() or 0
    seq = count_today + 1
    ref = f"{prefix}-{seq:03d}"
    # Garde-fou en cas de collision (références importées).
    while db.query(Arrival.arrival_id).filter(Arrival.reference == ref).first():
        seq += 1
        ref = f"{prefix}-{seq:03d}"
    return ref


def _serialize_arrival(db: Session, arrival: Arrival, with_financials: bool = False) -> dict:
    supplier_name = arrival.supplier.name if arrival.supplier else None
    data = {
        "arrival_id": arrival.arrival_id,
        "reference": arrival.reference,
        "label": arrival.label,
        "arrival_date": arrival.arrival_date.isoformat() if arrival.arrival_date else None,
        "supplier_id": arrival.supplier_id,
        "supplier_name": supplier_name,
        "notes": arrival.notes,
        "created_at": arrival.created_at.isoformat() if arrival.created_at else None,
    }
    if with_financials:
        fin = compute_arrival_financials(db, arrival)
        data["financials"] = fin
        data["product_count"] = len(fin["products"])
    return data


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@router.get("")
@router.get("/")
def list_arrivals(
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 25,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Liste des arrivages, du plus récent au plus ancien, avec récap financier.

    Paginée : chaque ligne demande un récapitulatif financier calculé produit
    par produit, et tout renvoyer d'un coup devenait coûteux autant à produire
    qu'à lire.
    """
    query = db.query(Arrival).options(joinedload(Arrival.supplier))
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            func.coalesce(Arrival.reference, "").ilike(pattern)
            | func.coalesce(Arrival.label, "").ilike(pattern)
        )

    total = query.count()
    limit = max(1, min(int(limit or 25), 200))
    skip = max(0, int(skip or 0))

    arrivals = (
        query.order_by(Arrival.arrival_date.desc(), Arrival.arrival_id.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return {
        "arrivals": [_serialize_arrival(db, a, with_financials=True) for a in arrivals],
        "total": total,
        "skip": skip,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/options")
def arrival_options(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Liste allégée (id + référence) pour les listes déroulantes du formulaire produit."""
    arrivals = db.query(Arrival).order_by(Arrival.arrival_date.desc(), Arrival.arrival_id.desc()).all()
    return {"arrivals": [
        {"arrival_id": a.arrival_id, "reference": a.reference,
         "label": a.label, "arrival_date": a.arrival_date.isoformat() if a.arrival_date else None}
        for a in arrivals
    ]}


@router.post("", status_code=201)
@router.post("/", status_code=201)
def create_arrival(
    payload: ArrivalCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    when = _parse_date(payload.arrival_date)
    reference = (payload.reference or "").strip() or _generate_reference(db, when)
    if db.query(Arrival.arrival_id).filter(Arrival.reference == reference).first():
        raise HTTPException(status_code=400, detail="Cette référence d'arrivage existe déjà")

    arrival = Arrival(
        reference=reference,
        label=(payload.label or "").strip() or None,
        arrival_date=when,
        supplier_id=payload.supplier_id,
        notes=(payload.notes or "").strip() or None,
    )
    db.add(arrival)
    db.commit()
    db.refresh(arrival)
    return _serialize_arrival(db, arrival, with_financials=True)


@router.get("/{arrival_id}")
def get_arrival(arrival_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    arrival = db.query(Arrival).filter(Arrival.arrival_id == arrival_id).first()
    if not arrival:
        raise HTTPException(status_code=404, detail="Arrivage introuvable")
    return _serialize_arrival(db, arrival, with_financials=True)


@router.put("/{arrival_id}")
def update_arrival(
    arrival_id: int,
    payload: ArrivalUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    arrival = db.query(Arrival).filter(Arrival.arrival_id == arrival_id).first()
    if not arrival:
        raise HTTPException(status_code=404, detail="Arrivage introuvable")
    if payload.label is not None:
        arrival.label = payload.label.strip() or None
    if payload.arrival_date is not None:
        arrival.arrival_date = _parse_date(payload.arrival_date)
    if payload.supplier_id is not None:
        arrival.supplier_id = payload.supplier_id or None
    if payload.notes is not None:
        arrival.notes = payload.notes.strip() or None
    db.commit()
    db.refresh(arrival)
    return _serialize_arrival(db, arrival, with_financials=True)


@router.delete("/{arrival_id}")
def delete_arrival(arrival_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    arrival = db.query(Arrival).filter(Arrival.arrival_id == arrival_id).first()
    if not arrival:
        raise HTTPException(status_code=404, detail="Arrivage introuvable")
    # Les variantes liées repassent à NULL (FK SET NULL) ; les items partent en cascade.
    db.delete(arrival)
    db.commit()
    return {"deleted": arrival_id}


# --- Liaison de produits sans variante (quantité) --------------------------

@router.post("/{arrival_id}/items", status_code=201)
def add_arrival_item(
    arrival_id: int,
    payload: ArrivalItemIn,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    arrival = db.query(Arrival).filter(Arrival.arrival_id == arrival_id).first()
    if not arrival:
        raise HTTPException(status_code=404, detail="Arrivage introuvable")
    product = db.query(Product).filter(Product.product_id == payload.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    if int(payload.quantity or 0) <= 0:
        raise HTTPException(status_code=400, detail="Quantité invalide")

    # Un même produit peut déjà être rattaché : on cumule sur la ligne existante.
    item = (
        db.query(ArrivalItem)
        .filter(ArrivalItem.arrival_id == arrival_id, ArrivalItem.product_id == payload.product_id)
        .first()
    )
    if item:
        item.quantity = int(item.quantity or 0) + int(payload.quantity)
        if payload.purchase_price is not None:
            item.purchase_price = payload.purchase_price
    else:
        item = ArrivalItem(
            arrival_id=arrival_id,
            product_id=payload.product_id,
            quantity=int(payload.quantity),
            purchase_price=payload.purchase_price,
        )
        db.add(item)
    db.commit()
    return _serialize_arrival(db, arrival, with_financials=True)


class LinkVariantsPayload(BaseModel):
    purchase_price: Optional[float] = None
    only_unassigned: bool = True   # ne rattacher que les variantes sans arrivage


@router.post("/{arrival_id}/link-product/{product_id}")
def link_product_variants(
    arrival_id: int,
    product_id: int,
    payload: LinkVariantsPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Rattache les variantes (IMEI) d'un produit à un arrivage. Appelé depuis la
    fiche produit."""
    arrival = db.query(Arrival).filter(Arrival.arrival_id == arrival_id).first()
    if not arrival:
        raise HTTPException(status_code=404, detail="Arrivage introuvable")

    q = db.query(ProductVariant).filter(ProductVariant.product_id == product_id)
    if payload.only_unassigned:
        q = q.filter(ProductVariant.arrival_id.is_(None))
    variants = q.all()

    linked = 0
    for v in variants:
        v.arrival_id = arrival_id
        if payload.purchase_price is not None:
            v.purchase_price = payload.purchase_price
        linked += 1
    db.commit()
    return {"linked": linked, "arrival_id": arrival_id}


@router.post("/detach-variant/{variant_id}")
def detach_variant(variant_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Détache une variante de son arrivage."""
    v = db.query(ProductVariant).filter(ProductVariant.variant_id == variant_id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Variante introuvable")
    v.arrival_id = None
    db.commit()
    return {"variant_id": variant_id, "detached": True}


@router.get("/product/{product_id}/links")
def product_arrival_links(product_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """État des rattachements d'un produit : variantes par arrivage, et lignes de
    quantité. Sert à afficher la section « Arrivage » de la fiche produit."""
    variants = db.query(ProductVariant).filter(ProductVariant.product_id == product_id).all()
    total = len(variants)
    assigned = sum(1 for v in variants if v.arrival_id)
    items = db.query(ArrivalItem).options(joinedload(ArrivalItem.arrival)).filter(
        ArrivalItem.product_id == product_id
    ).all()
    return {
        "has_variants": total > 0,
        "variant_total": total,
        "variant_assigned": assigned,
        "variant_unassigned": total - assigned,
        "quantity_links": [
            {"arrival_item_id": it.arrival_item_id, "arrival_id": it.arrival_id,
             "reference": it.arrival.reference if it.arrival else None,
             "quantity": it.quantity} for it in items
        ],
    }


@router.delete("/items/{item_id}")
def delete_arrival_item(item_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    item = db.query(ArrivalItem).filter(ArrivalItem.arrival_item_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Ligne introuvable")
    db.delete(item)
    db.commit()
    return {"deleted": item_id}
