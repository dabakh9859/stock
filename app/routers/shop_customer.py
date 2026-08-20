"""
Comptes clients de la boutique en ligne (auth serveur).

Indépendant de l'auth du personnel (`get_current_user`) : un client possède un
jeton JWT dédié (scope « shop »). Couvre inscription/connexion, profil, favoris,
adresses enregistrées, et l'historique des commandes/demandes rattaché au compte.
"""

import re
from typing import List, Optional
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session
from jose import JWTError, jwt

from ..database import (
    get_db, Product, ShopProduct, ShopCustomer, ShopSavedZone,
    ShopWishlistItem, ShopOrder, ShopDemand, ShopDeliveryZone,
)
from ..auth import get_password_hash, verify_password, SECRET_KEY, ALGORITHM
from .shop import serialize_product

router = APIRouter(prefix="/api/shop", tags=["boutique-client"])

CUSTOMER_TOKEN_DAYS = 30


# ---------------------------------------------------------------- helpers auth

def _norm_phone(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone or "")


def _same_phone(a: str, b: str) -> bool:
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    if len(da) < 7 or len(db) < 7:
        return da == db
    return da == db or da.endswith(db) or db.endswith(da)


def _make_token(customer: ShopCustomer) -> str:
    from datetime import datetime
    payload = {
        "cid": customer.customer_id,
        "sub": customer.phone,
        "scope": "shop",
        "exp": datetime.utcnow() + timedelta(days=CUSTOMER_TOKEN_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _serialize_customer(c: ShopCustomer) -> dict:
    return {
        "id": c.customer_id,
        "name": c.name,
        "phone": c.phone,
        "email": c.email,
        "address": c.default_address or "",
    }


def get_current_customer(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> ShopCustomer:
    """Résout le client connecté depuis le jeton Bearer (scope « shop »)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentification requise")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Session invalide ou expirée")
    if payload.get("scope") != "shop":
        raise HTTPException(status_code=401, detail="Jeton non autorisé")
    customer = db.query(ShopCustomer).filter(ShopCustomer.customer_id == payload.get("cid")).first()
    if not customer or not customer.is_active:
        raise HTTPException(status_code=401, detail="Compte introuvable")
    return customer


# ---------------------------------------------------------------- inscription / connexion

class RegisterIn(BaseModel):
    name: str
    phone: str
    email: Optional[str] = None
    password: str = Field(min_length=6)


class LoginIn(BaseModel):
    phone: str
    password: str


@router.post("/auth/register", status_code=201)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    phone = _norm_phone(payload.phone)
    if len(re.sub(r"\D", "", phone)) < 7:
        raise HTTPException(status_code=400, detail="Numéro de téléphone invalide")
    if db.query(ShopCustomer).filter(ShopCustomer.phone == phone).first():
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec ce numéro")
    customer = ShopCustomer(
        name=payload.name.strip() or "Client",
        phone=phone,
        email=(payload.email or "").strip() or None,
        password_hash=get_password_hash(payload.password),
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    # Rattache les commandes/demandes anonymes passées avec ce numéro.
    _attach_history(db, customer)
    return {"token": _make_token(customer), "user": _serialize_customer(customer)}


@router.post("/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    phone = _norm_phone(payload.phone)
    customer = db.query(ShopCustomer).filter(ShopCustomer.phone == phone).first()
    if not customer:
        # Repli: tolère indicatif présent/absent.
        for c in db.query(ShopCustomer).all():
            if _same_phone(c.phone, phone):
                customer = c
                break
    if not customer or not verify_password(payload.password, customer.password_hash):
        raise HTTPException(status_code=401, detail="Téléphone ou mot de passe incorrect")
    return {"token": _make_token(customer), "user": _serialize_customer(customer)}


@router.get("/auth/me")
def me(customer: ShopCustomer = Depends(get_current_customer)):
    return {"user": _serialize_customer(customer)}


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


@router.put("/auth/me")
def update_me(
    payload: ProfileUpdate,
    customer: ShopCustomer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    if payload.name is not None and payload.name.strip():
        customer.name = payload.name.strip()
    if payload.email is not None:
        customer.email = payload.email.strip() or None
    if payload.address is not None:
        customer.default_address = payload.address.strip() or None
    db.commit()
    db.refresh(customer)
    return {"user": _serialize_customer(customer)}


def _attach_history(db: Session, customer: ShopCustomer) -> None:
    """Rattache commandes/demandes anonymes du même numéro au compte."""
    phone_digits = re.sub(r"\D", "", customer.phone or "")
    if len(phone_digits) < 7:
        return
    for order in db.query(ShopOrder).filter(ShopOrder.customer_id.is_(None)).all():
        if _same_phone(order.customer_phone or "", customer.phone):
            order.customer_id = customer.customer_id
    for demand in db.query(ShopDemand).filter(ShopDemand.customer_id.is_(None)).all():
        if _same_phone(demand.customer_phone or "", customer.phone):
            demand.customer_id = customer.customer_id
    db.commit()


# ---------------------------------------------------------------- historique

@router.get("/account/orders")
def my_orders(customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    orders = (
        db.query(ShopOrder)
        .filter(or_(ShopOrder.customer_id == customer.customer_id,
                    ShopOrder.customer_phone == customer.phone))
        .order_by(ShopOrder.created_at.desc())
        .all()
    )
    return [
        {
            "order_number": o.order_number,
            "status": o.status,
            "total": float(o.total or 0),
            "created_at": o.created_at,
            "items": [{"name": i.product_name, "quantity": i.quantity} for i in o.items],
        }
        for o in orders
    ]


@router.get("/account/demands")
def my_demands(customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    demands = (
        db.query(ShopDemand)
        .filter(or_(ShopDemand.customer_id == customer.customer_id,
                    ShopDemand.customer_phone == customer.phone))
        .order_by(ShopDemand.created_at.desc())
        .all()
    )
    return [
        {
            "demand_number": d.demand_number,
            "product_name": d.product_name,
            "quantity": d.quantity,
            "status": d.status,
            "created_at": d.created_at,
        }
        for d in demands
    ]


# ---------------------------------------------------------------- favoris

def _serialize_wishlist_product(db: Session, product_id: int) -> Optional[dict]:
    row = (
        db.query(Product, ShopProduct)
        .outerjoin(ShopProduct, ShopProduct.product_id == Product.product_id)
        .filter(Product.product_id == product_id)
        .first()
    )
    if not row:
        return None
    return serialize_product(row[0], row[1])


@router.get("/account/wishlist")
def get_wishlist(customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    items = db.query(ShopWishlistItem).filter(ShopWishlistItem.customer_id == customer.customer_id).all()
    products = [p for p in (_serialize_wishlist_product(db, it.product_id) for it in items) if p]
    return {"products": products}


class WishlistIn(BaseModel):
    product_id: int


@router.post("/account/wishlist", status_code=201)
def add_wishlist(payload: WishlistIn, customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    exists = (
        db.query(ShopWishlistItem)
        .filter(ShopWishlistItem.customer_id == customer.customer_id,
                ShopWishlistItem.product_id == payload.product_id)
        .first()
    )
    if not exists:
        db.add(ShopWishlistItem(customer_id=customer.customer_id, product_id=payload.product_id))
        db.commit()
    return {"ok": True}


@router.delete("/account/wishlist/{product_id}")
def remove_wishlist(product_id: int, customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    db.query(ShopWishlistItem).filter(
        ShopWishlistItem.customer_id == customer.customer_id,
        ShopWishlistItem.product_id == product_id,
    ).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- adresses enregistrées

def _serialize_saved_zone(z: ShopSavedZone, zone: Optional[ShopDeliveryZone]) -> dict:
    return {
        "id": z.saved_zone_id,
        "label": z.label,
        "zone_id": zone.code if zone else None,
        "zone_name": zone.name if zone else None,
        "details": z.details or "",
        "lat": float(z.lat) if z.lat is not None else None,
        "lng": float(z.lng) if z.lng is not None else None,
    }


@router.get("/account/saved-zones")
def get_saved_zones(customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    rows = db.query(ShopSavedZone).filter(ShopSavedZone.customer_id == customer.customer_id).all()
    out = []
    for z in rows:
        zone = db.query(ShopDeliveryZone).filter(ShopDeliveryZone.zone_id == z.zone_id).first() if z.zone_id else None
        out.append(_serialize_saved_zone(z, zone))
    return out


class SavedZoneIn(BaseModel):
    label: str
    zone_code: Optional[str] = None
    details: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


@router.post("/account/saved-zones", status_code=201)
def add_saved_zone(payload: SavedZoneIn, customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    zone = db.query(ShopDeliveryZone).filter(ShopDeliveryZone.code == payload.zone_code).first() if payload.zone_code else None
    z = ShopSavedZone(
        customer_id=customer.customer_id,
        label=payload.label.strip() or "Adresse",
        zone_id=zone.zone_id if zone else None,
        details=(payload.details or "").strip() or None,
        lat=payload.lat,
        lng=payload.lng,
    )
    db.add(z)
    db.commit()
    db.refresh(z)
    return _serialize_saved_zone(z, zone)


@router.delete("/account/saved-zones/{saved_zone_id}")
def remove_saved_zone(saved_zone_id: int, customer: ShopCustomer = Depends(get_current_customer), db: Session = Depends(get_db)):
    db.query(ShopSavedZone).filter(
        ShopSavedZone.saved_zone_id == saved_zone_id,
        ShopSavedZone.customer_id == customer.customer_id,
    ).delete()
    db.commit()
    return {"ok": True}
