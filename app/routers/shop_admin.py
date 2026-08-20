"""
Administration de la boutique en ligne, depuis la gestion de stock Stock.

Couvre: catalogue (publication / vedette / disponibilité / prix vitrine),
commandes, livraisons et configuration de la page d'accueil.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_, func
import logging
import os

import httpx
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel

from ..database import (
    get_db, Product, ShopProduct, ShopOrder, ShopOrderItem, ProductVariant, Client, Invoice,
    ShopDelivery, ShopSetting, ShopDeliveryZone, ShopDemand, StockMovement,
)
from ..auth import get_current_user
from ..services.html_sanitizer import sanitize_html
from .shop import (
    _phone_key,
    compute_availability, serialize_product, get_all_settings, get_setting,
    _apply_stock_movement, DEFAULT_SETTINGS, ORDER_STATUSES, DELIVERY_STATUSES,
)

router = APIRouter(prefix="/api/shop/admin", tags=["boutique-admin"])


def _entetes_internes() -> dict:
    """Jeton présenté aux routes internes de l'application (voir main.py).

    Sans lui, ces appels serveur à serveur se heurteraient au 401 posé le
    13/08/2026 sur `/api/whatsapp/*`.
    """
    jeton = os.getenv("INTERNAL_API_TOKEN", "").strip()
    return {"X-Internal-Token": jeton} if jeton else {}


def _url_interne() -> str:
    return os.getenv("APP_INTERNAL_URL", "http://localhost:8000").rstrip("/")


def _prevenir_livraison(order: ShopOrder) -> None:
    """Note de livraison envoyée au client, sur WhatsApp.

    Best-effort : un envoi qui échoue ne doit jamais empêcher d'enregistrer la
    livraison. L'équipe a constaté la remise avec le livreur ; le message n'est
    qu'une courtoisie, et le journal garde la trace des échecs.
    """
    numero = (order.customer_phone or "").strip()
    if not numero:
        return
    texte = (
        f"Bonjour {order.customer_name or ''},\n\n"
        f"Votre commande *{order.order_number}* a bien été livrée. "
        f"Merci de votre confiance !\n\n"
        f"Pour toute question, répondez simplement à ce message."
    )
    try:
        with httpx.Client(timeout=15.0) as client:
            reponse = client.post(
                f"{_url_interne()}/api/whatsapp/send-text",
                headers=_entetes_internes(),
                json={"number": numero, "message": texte},
            )
            data = reponse.json()
        if not data.get("success"):
            logging.warning("Note de livraison %s non envoyée : %s",
                            order.order_number, data.get("error"))
    except Exception as erreur:
        logging.warning("Note de livraison %s non envoyée : %s", order.order_number, erreur)


def _marquer_livree(db: Session, order: ShopOrder) -> None:
    """Passe commande ET livraison à « livrée », et prévient le client.

    Les deux écrans (Commandes et Livraisons) appellent ceci : avant le
    13/08/2026, changer le statut depuis l'écran Commandes laissait la fiche de
    livraison en arrière — une commande pouvait être « livrée » avec une
    livraison encore « à planifier », donc sans date de remise exploitable.
    """
    order.status = "livrée"
    livraison = order.delivery
    if livraison is None:
        livraison = ShopDelivery(
            order_id=order.order_id,
            delivery_address=order.delivery_address,
            delivery_city=order.delivery_city,
        )
        db.add(livraison)
    livraison.status = "livrée"
    if livraison.delivered_at is None:
        livraison.delivered_at = datetime.now()


def _get_or_create_shop_product(db: Session, product_id: int) -> ShopProduct:
    shop = db.query(ShopProduct).filter(ShopProduct.product_id == product_id).first()
    if shop:
        return shop
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    shop = ShopProduct(product_id=product_id)
    db.add(shop)
    db.flush()
    return shop


# ---------------------------------------------------------------- catalogue

@router.get("/products")
def admin_list_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    published: Optional[bool] = None,
    featured: Optional[bool] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Tout le catalogue du stock, avec ses réglages boutique."""
    query = (
        db.query(Product, ShopProduct)
        .outerjoin(ShopProduct, ShopProduct.product_id == Product.product_id)
        .filter(or_(Product.is_archived.is_(False), Product.is_archived.is_(None)))
    )

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(
            Product.name.ilike(pattern),
            Product.brand.ilike(pattern),
            Product.model.ilike(pattern),
        ))
    if category:
        query = query.filter(Product.category == category)
    if published is not None:
        if published:
            query = query.filter(or_(ShopProduct.is_published.is_(True), ShopProduct.shop_product_id.is_(None)))
        else:
            query = query.filter(ShopProduct.is_published.is_(False))
    if featured is not None:
        if featured:
            query = query.filter(ShopProduct.is_featured.is_(True))
        else:
            query = query.filter(or_(ShopProduct.is_featured.is_(False), ShopProduct.shop_product_id.is_(None)))

    total = query.count()
    rows = (
        query.order_by(Product.created_at.desc().nullslast(), Product.product_id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    products = []
    for product, shop in rows:
        data = serialize_product(product, shop)
        data["availability_override"] = shop.availability_override if shop else None
        data["shop_price"] = float(shop.shop_price) if (shop and shop.shop_price is not None) else None
        data["shop_price_max"] = float(shop.shop_price_max) if (shop and shop.shop_price_max is not None) else None
        data["stock_price"] = float(product.price or 0)
        data["shop_only"] = bool(product.shop_only)
        products.append(data)

    return {
        "products": products,
        "pagination": {"total": total, "page": page, "limit": limit,
                       "pages": max(1, (total + limit - 1) // limit)},
    }


class ProductShopUpdate(BaseModel):
    is_published: Optional[bool] = None
    is_featured: Optional[bool] = None
    availability_override: Optional[str] = None  # 'epuise' ou null (= automatique)
    shop_description: Optional[str] = None
    shop_price: Optional[float] = None
    shop_price_max: Optional[float] = None
    sort_order: Optional[int] = None
    # Présentation vitrine (parité nouveau site)
    old_price: Optional[float] = None
    specs: Optional[str] = None          # une caractéristique par ligne
    is_new: Optional[bool] = None
    is_bestseller: Optional[bool] = None
    rating: Optional[float] = None       # 0..5
    reviews_count: Optional[int] = None


@router.put("/products/{product_id}")
def admin_update_product(
    product_id: int,
    payload: ProductShopUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    shop = _get_or_create_shop_product(db, product_id)

    if payload.is_published is not None:
        shop.is_published = payload.is_published
    if payload.is_featured is not None:
        shop.is_featured = payload.is_featured
    if payload.availability_override is not None:
        value = (payload.availability_override or "").strip().lower()
        # Seul 'epuise' est un forçage valide; toute autre valeur rend la main à l'automatique.
        shop.availability_override = "epuise" if value == "epuise" else None
    if payload.shop_description is not None:
        shop.shop_description = sanitize_html(payload.shop_description) or None
    if payload.shop_price is not None:
        shop.shop_price = payload.shop_price if payload.shop_price > 0 else None
    if payload.shop_price_max is not None:
        shop.shop_price_max = payload.shop_price_max if payload.shop_price_max > 0 else None
    if payload.sort_order is not None:
        shop.sort_order = payload.sort_order
    if payload.old_price is not None:
        shop.old_price = payload.old_price if payload.old_price > 0 else None
    if payload.specs is not None:
        # Normalise: une caractéristique par ligne, lignes vides retirées.
        lines = [l.strip() for l in payload.specs.splitlines() if l.strip()]
        shop.specs = "\n".join(lines) or None
    if payload.is_new is not None:
        shop.is_new = payload.is_new
    if payload.is_bestseller is not None:
        shop.is_bestseller = payload.is_bestseller
    if payload.rating is not None:
        shop.rating = max(0.0, min(5.0, payload.rating)) if payload.rating > 0 else None
    if payload.reviews_count is not None:
        shop.reviews_count = max(0, payload.reviews_count)

    db.commit()
    db.refresh(shop)

    product = db.query(Product).filter(Product.product_id == product_id).first()
    return serialize_product(product, shop)


@router.post("/products/bulk")
def admin_bulk_update(
    payload: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Action groupée: publier, dépublier, mettre en vedette, retirer des vedettes."""
    ids = payload.get("product_ids") or []
    action = (payload.get("action") or "").strip()
    if not ids or not action:
        raise HTTPException(status_code=400, detail="product_ids et action requis")

    actions = {
        "publish": ("is_published", True),
        "unpublish": ("is_published", False),
        "feature": ("is_featured", True),
        "unfeature": ("is_featured", False),
        "mark_new": ("is_new", True),
        "unmark_new": ("is_new", False),
        "mark_bestseller": ("is_bestseller", True),
        "unmark_bestseller": ("is_bestseller", False),
    }
    if action not in actions:
        raise HTTPException(status_code=400, detail=f"Action inconnue: {action}")

    field, value = actions[action]
    for product_id in ids:
        shop = _get_or_create_shop_product(db, int(product_id))
        setattr(shop, field, value)

    db.commit()
    return {"updated": len(ids), "action": action}


def _stock_is_deducted(status: str, mode: str) -> bool:
    """Le stock doit-il être considéré comme déduit pour ce statut ?"""
    if mode == "never":
        return False
    if status == "annulée":
        return False
    if mode == "order":
        return True          # déduit dès la création, restitué seulement si annulée
    return status in ("confirmée", "en préparation", "expédiée", "livrée")


# ---------------------------------------------------------------- commandes

@router.get("/orders")
def admin_list_orders(
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ShopOrder).options(joinedload(ShopOrder.items), joinedload(ShopOrder.delivery))
    if status:
        query = query.filter(ShopOrder.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(
            ShopOrder.order_number.ilike(pattern),
            ShopOrder.customer_name.ilike(pattern),
            ShopOrder.customer_phone.ilike(pattern),
        ))

    total = query.count()
    orders = query.order_by(ShopOrder.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    return {
        "orders": [{
            "order_id": o.order_id,
            "order_number": o.order_number,
            "customer_name": o.customer_name,
            "customer_phone": o.customer_phone,
            "customer_email": o.customer_email,
            "delivery_city": o.delivery_city,
            "delivery_address": o.delivery_address,
            "zone_name": o.zone_name,
            "delivery_details": o.delivery_details,
            # Point posé par le client sur la carte de la boutique : il évite au
            # livreur de chercher une villa sans numéro.
            "delivery_lat": float(o.delivery_lat) if o.delivery_lat is not None else None,
            "delivery_lng": float(o.delivery_lng) if o.delivery_lng is not None else None,
            # Date à laquelle le client a dit avoir reçu. Signal, pas preuve :
            # c'est la boutique qui confirme.
            "customer_reported_at": (o.delivery.customer_reported_at if o.delivery else None),
            "customer_id": o.customer_id,
            "status": o.status,
            "payment_status": o.payment_status,
            "payment_method": o.payment_method,
            "subtotal": float(o.subtotal or 0),
            "delivery_fee": float(o.delivery_fee or 0),
            "total": float(o.total or 0),
            "notes": o.notes,
            "internal_notes": o.internal_notes,
            "created_at": o.created_at,
            "delivery_status": o.delivery.status if o.delivery else None,
            "items": [{
                "product_id": i.product_id,
                "product_name": i.product_name,
                "quantity": i.quantity,
                "price": float(i.price or 0),
                "total": float(i.total or 0),
                "availability_at_order": i.availability_at_order,
            } for i in o.items],
        } for o in orders],
        "pagination": {"total": total, "page": page, "limit": limit,
                       "pages": max(1, (total + limit - 1) // limit)},
    }


class OrderUpdate(BaseModel):
    status: Optional[str] = None
    payment_status: Optional[str] = None
    payment_method: Optional[str] = None
    internal_notes: Optional[str] = None
    delivery_fee: Optional[float] = None


@router.put("/orders/{order_id}")
def admin_update_order(
    order_id: int,
    payload: OrderUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.query(ShopOrder).filter(ShopOrder.order_id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")

    a_prevenir = False
    if payload.status is not None:
        if payload.status not in ORDER_STATUSES:
            raise HTTPException(status_code=400, detail=f"Statut invalide. Attendu: {', '.join(ORDER_STATUSES)}")

        previous = order.status
        order.status = payload.status

        # « livrée » depuis cet écran doit valoir la même chose que depuis
        # l'écran Livraisons : fiche de livraison à jour, date de remise, et
        # note envoyée au client.
        if payload.status == "livrée" and previous != "livrée":
            _marquer_livree(db, order)
            a_prevenir = True

        # En mode « confirm », le stock bouge au passage de/vers « en attente ».
        # Une commande annulée restitue toujours ce qui avait été déduit.
        mode = (get_setting(db, "stock_mode", "confirm") or "confirm").strip()
        deducted_before = _stock_is_deducted(previous, mode)
        deducted_after = _stock_is_deducted(payload.status, mode)
        if deducted_after and not deducted_before:
            _apply_stock_movement(db, order, sign=-1)
        elif deducted_before and not deducted_after:
            _apply_stock_movement(db, order, sign=+1)
    if payload.payment_status is not None:
        order.payment_status = payload.payment_status
    if payload.payment_method is not None:
        order.payment_method = payload.payment_method
    if payload.internal_notes is not None:
        order.internal_notes = payload.internal_notes
    if payload.delivery_fee is not None:
        # `subtotal` est un Numeric, donc un Decimal ; l'addition avec le float
        # de la requête levait un TypeError. Comme l'écran envoie toujours ce
        # champ, le moindre enregistrement — un simple changement de statut —
        # répondait 500 et ne modifiait rien. Corrigé le 13/08/2026.
        fee = Decimal(str(payload.delivery_fee))
        order.delivery_fee = fee
        order.total = (order.subtotal or Decimal(0)) + fee

    db.commit()

    # Après le commit : un envoi lent ou en échec ne doit pas retenir la
    # transaction, ni faire perdre l'enregistrement.
    if a_prevenir:
        _prevenir_livraison(order)

    return {"ok": True, "order_id": order.order_id, "status": order.status}


@router.delete("/orders/{order_id}")
def admin_delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Supprimer définitivement une commande annulée.

    Deux garde-fous, parce qu'une commande supprimée ne se retrouve pas :
    - réservé au rôle administrateur ;
    - **uniquement** sur une commande au statut « annulée ». Passer par
      l'annulation d'abord n'est pas une formalité : c'est elle qui remet le
      stock en place (voir `admin_update_order`). Supprimer une commande encore
      active laisserait les articles déduits sans rien pour l'expliquer.

    Les lignes et la livraison partent avec, par cascade au niveau de la base.
    """
    if getattr(current_user, "role", None) != "admin":
        raise HTTPException(status_code=403, detail="Suppression réservée aux administrateurs.")

    order = db.query(ShopOrder).filter(ShopOrder.order_id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")

    if order.status != "annulée":
        raise HTTPException(
            status_code=400,
            detail="Seule une commande annulée peut être supprimée. Annulez-la d'abord : "
                   "c'est ce passage qui remet les articles en stock.",
        )

    numero = order.order_number
    # Explicite plutôt que de compter sur la configuration de l'ORM : la cascade
    # est déclarée en base, mais rien ne garantit que la session la déclenche.
    db.query(ShopOrderItem).filter(ShopOrderItem.order_id == order_id).delete(synchronize_session=False)
    db.query(ShopDelivery).filter(ShopDelivery.order_id == order_id).delete(synchronize_session=False)
    db.delete(order)
    db.commit()

    logging.info("Commande boutique %s supprimée par %s", numero, getattr(current_user, "username", "?"))
    return {"ok": True, "order_number": numero}


class EnvoiPointLivraison(BaseModel):
    phone: str
    canal: str = "whatsapp"   # whatsapp | sms


def _envoyer_sms(numero: str, texte: str) -> tuple[bool, str]:
    """SMS, par l'opérateur local d'abord. (succès, message d'erreur).

    Orange Sénégal passe en premier : c'est l'opérateur en direct, ~20 F CFA le
    message contre ~314 F chez Twilio pour le même texte, et il dessert les
    trois réseaux du pays. Twilio ne reste qu'en dernier recours, s'il est
    renseigné — une fiche de livraison y coûte quatre segments.
    """
    from app.services import orange_sms

    echecs = []
    if orange_sms.config():
        ok, erreur = orange_sms.envoyer(numero, texte)
        if ok:
            return True, ""
        echecs.append(f"Orange : {erreur}")

    ok, erreur = _envoyer_sms_twilio(numero, texte)
    if ok:
        return True, ""
    echecs.append(erreur)

    if not echecs[:-1] and not orange_sms.config():
        # Aucun opérateur configuré : le message doit dire quoi faire.
        return False, ("L'envoi par SMS n'est pas configuré (identifiants Orange absents). "
                       "Utilisez WhatsApp en attendant.")
    return False, " | ".join(echecs)


def _envoyer_sms_twilio(numero: str, texte: str) -> tuple[bool, str]:
    """SMS par l'API REST de Twilio. (succès, message d'erreur)."""
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    expediteur = os.getenv("TWILIO_FROM")
    if not (sid and token and expediteur):
        return False, "Twilio : identifiants absents."
    try:
        import base64 as _b64
        entetes = {
            "Authorization": "Basic " + _b64.b64encode(f"{sid}:{token}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        }
        destinataire = numero if numero.startswith("+") else f"+{numero}"
        with httpx.Client(timeout=15.0) as client:
            r = client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                headers=entetes,
                data={"From": expediteur, "To": destinataire, "Body": texte},
            )
        if 200 <= r.status_code < 300:
            return True, ""
        detail = (r.json() or {}).get("message") if r.headers.get("content-type", "").startswith("application/json") else None
        return False, detail or f"Twilio a répondu {r.status_code}."
    except Exception as erreur:
        return False, str(erreur)


def _envoyer_whatsapp(numero: str, texte: str) -> tuple[bool, str]:
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(f"{_url_interne()}/api/whatsapp/send-text",
                            headers=_entetes_internes(),
                            json={"number": numero, "message": texte})
            data = r.json()
        if data.get("success"):
            return True, ""
        return False, str(data.get("error") or "Envoi WhatsApp impossible.")
    except Exception as erreur:
        return False, str(erreur)


def _fiche_livraison(order: ShopOrder) -> str:
    """Tout ce qu'il faut au livreur, en un message.

    L'état du paiement y figure en toutes lettres : c'est ce qui décide s'il doit
    encaisser à la remise, et l'oublier coûte un second déplacement.
    """
    lignes = [f"*Livraison {order.order_number}*", ""]
    lignes.append(f"Client : {order.customer_name or '—'}")
    if order.customer_phone:
        lignes.append(f"Téléphone : {order.customer_phone}")
    adresse = ", ".join(filter(None, [order.delivery_address, order.delivery_city]))
    if adresse:
        lignes.append(f"Adresse : {adresse}")
    if order.delivery_details:
        lignes.append(f"Précisions : {order.delivery_details}")

    if order.items:
        lignes.append("")
        lignes.append("Articles :")
        for article in order.items:
            lignes.append(f"- {article.quantity} × {article.product_name}")

    total = float(order.total or 0)
    paye = (order.payment_status or "").strip().lower() == "payé"
    lignes.append("")
    lignes.append(
        f"Total : {total:,.0f} F CFA".replace(",", " ") +
        (" — déjà payé, ne rien encaisser" if paye else " — À ENCAISSER à la remise")
    )

    if order.delivery_lat is not None and order.delivery_lng is not None:
        lignes.append("")
        lignes.append("Point de livraison :")
        lignes.append(f"https://www.google.com/maps/search/?api=1&query={order.delivery_lat},{order.delivery_lng}")

    return "\n".join(lignes)


@router.post("/orders/{order_id}/send-delivery-point")
def admin_send_delivery_point(
    order_id: int,
    payload: EnvoiPointLivraison,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Envoie la fiche de livraison à un numéro, par WhatsApp ou par SMS.

    Pensé pour le livreur : il reçoit l'adresse, le point sur la carte, les
    articles et surtout s'il doit encaisser. Le numéro est saisi à la volée —
    ce n'est pas forcément celui du client.
    """
    order = (
        db.query(ShopOrder)
        .options(joinedload(ShopOrder.items))
        .filter(ShopOrder.order_id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")

    numero = "".join(c for c in (payload.phone or "") if c.isdigit())
    if len(numero) < 9:
        raise HTTPException(status_code=400, detail="Numéro de téléphone invalide.")
    if not numero.startswith("221"):
        numero = f"221{numero}"

    canal = (payload.canal or "whatsapp").strip().lower()
    if canal not in ("whatsapp", "sms"):
        raise HTTPException(status_code=400, detail="Canal inconnu.")

    texte = _fiche_livraison(order)
    ok, erreur = (_envoyer_whatsapp if canal == "whatsapp" else _envoyer_sms)(numero, texte)

    if not ok:
        logging.warning("Fiche de livraison %s non envoyée (%s) : %s", order.order_number, canal, erreur)
        raise HTTPException(status_code=502, detail=erreur)

    logging.info("Fiche de livraison %s envoyée à %s par %s", order.order_number, numero, canal)
    return {"ok": True, "canal": canal, "numero": numero}


# ------------------------------------------------- facturation d'une commande

@router.get("/orders/{order_id}/invoice-preparation")
def admin_invoice_preparation(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Ce qu'il faut choisir avant de facturer une commande de la boutique.

    Une commande boutique ne désigne qu'un produit et une quantité. La
    facturation, elle, exige l'exemplaire précis pour tout produit à IMEI —
    c'est ce rattachement qui permet de retrouver l'appareil vendu et de remettre
    le bon en stock en cas de retour. On renvoie donc, ligne par ligne, les
    exemplaires disponibles parmi lesquels choisir.
    """
    order = (
        db.query(ShopOrder)
        .options(joinedload(ShopOrder.items))
        .filter(ShopOrder.order_id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")

    if order.invoice_id:
        facture = db.query(Invoice).filter(Invoice.invoice_id == order.invoice_id).first()
        if facture:
            return {
                "deja_facturee": True,
                "invoice_id": facture.invoice_id,
                "invoice_number": facture.invoice_number,
            }

    lignes = []
    for ligne in order.items:
        produit = db.query(Product).filter(Product.product_id == ligne.product_id).first() if ligne.product_id else None
        a_variantes = bool(produit) and db.query(ProductVariant.variant_id).filter(
            ProductVariant.product_id == produit.product_id
        ).first() is not None

        disponibles = []
        if a_variantes:
            for v in db.query(ProductVariant).filter(
                ProductVariant.product_id == produit.product_id,
                or_(ProductVariant.is_sold.is_(False), ProductVariant.is_sold.is_(None)),
            ).order_by(ProductVariant.variant_id).all():
                if v.quantity is not None and v.quantity <= 0:
                    continue
                disponibles.append({
                    "variant_id": v.variant_id,
                    "imei_serial": v.imei_serial,
                    "condition": v.condition,
                    "price": float(v.price) if v.price is not None else None,
                })

        lignes.append({
            "item_id": ligne.item_id,
            "product_id": ligne.product_id,
            "product_name": ligne.product_name,
            "quantity": ligne.quantity,
            "price": float(ligne.price or 0),
            "requires_variants": a_variantes,
            "stock": int(produit.quantity or 0) if produit else 0,
            "variants": disponibles,
            # Signalé tôt : sans exemplaire disponible, la facture est impossible
            # et mieux vaut le dire avant d'ouvrir le formulaire.
            "manquants": (a_variantes and len(disponibles) < (ligne.quantity or 0)),
        })

    client = None
    if order.customer_phone:
        cible = _phone_key(order.customer_phone)
        for c in db.query(Client).all():
            if _phone_key(c.phone) == cible:
                client = {"client_id": c.client_id, "name": c.name}
                break

    return {
        "deja_facturee": False,
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "delivery_fee": float(order.delivery_fee or 0),
        "payment_status": order.payment_status,
        "client_existant": client,
        "lignes": lignes,
    }


class LigneAFacturer(BaseModel):
    item_id: int
    variant_ids: List[int] = []


class GenerationFacture(BaseModel):
    lignes: List[LigneAFacturer] = []
    creer_bon_livraison: bool = True


@router.post("/orders/{order_id}/invoice")
async def admin_create_invoice_from_order(
    order_id: int,
    payload: GenerationFacture,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Génère la facture d'une commande boutique, par le chemin normal.

    On ne réimplémente rien : la charge est confiée à `create_invoice()` du
    module Factures, exactement comme une saisie manuelle. C'est elle qui
    déduit le stock, marque l'exemplaire vendu et rattache l'IMEI à la ligne —
    dupliquer cette logique ici, c'est se garantir deux comportements
    divergents au premier correctif.
    """
    from ..schemas import InvoiceCreate, InvoiceItemCreate
    from .invoices import create_invoice, create_delivery_note_from_invoice

    order = (
        db.query(ShopOrder)
        .options(joinedload(ShopOrder.items))
        .filter(ShopOrder.order_id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")
    if order.invoice_id:
        raise HTTPException(status_code=400, detail="Cette commande a déjà une facture.")
    if order.status == "annulée":
        raise HTTPException(status_code=400, detail="Commande annulée : rien à facturer.")

    choix = {l.item_id: l.variant_ids for l in payload.lignes}

    # --- Le client de la facture ------------------------------------------
    client = None
    if order.customer_phone:
        cible = _phone_key(order.customer_phone)
        for c in db.query(Client).all():
            if _phone_key(c.phone) == cible:
                client = c
                break
    if client is None:
        client = Client(
            name=order.customer_name or "Client boutique",
            phone=order.customer_phone,
            email=order.customer_email,
            address=order.delivery_address,
            city=order.delivery_city,
        )
        db.add(client)
        db.flush()

    # --- Les lignes ---------------------------------------------------------
    articles: List[InvoiceItemCreate] = []
    sous_total = Decimal(0)

    for ligne in order.items:
        produit = db.query(Product).filter(Product.product_id == ligne.product_id).first() if ligne.product_id else None
        a_variantes = bool(produit) and db.query(ProductVariant.variant_id).filter(
            ProductVariant.product_id == produit.product_id
        ).first() is not None
        prix = Decimal(str(ligne.price or 0))

        if a_variantes:
            ids = choix.get(ligne.item_id) or []
            if len(ids) != int(ligne.quantity or 0):
                raise HTTPException(
                    status_code=400,
                    detail=f"« {ligne.product_name} » : choisissez {ligne.quantity} exemplaire(s), "
                           f"{len(ids)} sélectionné(s).",
                )
            if len(set(ids)) != len(ids):
                raise HTTPException(
                    status_code=400,
                    detail=f"« {ligne.product_name} » : le même exemplaire est sélectionné deux fois.",
                )
            # Une ligne de facture par exemplaire : c'est ce que le module
            # Factures attend pour un produit à IMEI.
            for vid in ids:
                articles.append(InvoiceItemCreate(
                    product_id=ligne.product_id,
                    product_name=(ligne.product_name or "")[:100],
                    quantity=1,
                    price=prix,
                    total=prix,
                    variant_id=vid,
                ))
                sous_total += prix
        else:
            quantite = int(ligne.quantity or 0)
            total_ligne = prix * quantite
            articles.append(InvoiceItemCreate(
                product_id=ligne.product_id,
                product_name=(ligne.product_name or "")[:100],
                quantity=quantite,
                price=prix,
                total=total_ligne,
            ))
            sous_total += total_ligne

    # Les frais de livraison deviennent une ligne sans produit : ils doivent
    # apparaître sur la facture remise au client, sans toucher au stock.
    frais = Decimal(str(order.delivery_fee or 0))
    if frais > 0:
        articles.append(InvoiceItemCreate(
            product_name="Livraison",
            quantity=1,
            price=frais,
            total=frais,
        ))
        sous_total += frais

    if not articles:
        raise HTTPException(status_code=400, detail="Cette commande n'a aucune ligne à facturer.")

    donnees = InvoiceCreate(
        invoice_number="",          # numéroté automatiquement (FAC-####)
        client_id=client.client_id,
        date=datetime.now(),
        payment_method=order.payment_method,
        subtotal=sous_total,
        tax_rate=Decimal(0),        # la boutique vend TTC, sans TVA affichée
        tax_amount=Decimal(0),
        total=sous_total,
        show_tax=False,
        notes=f"Commande boutique {order.order_number}",
        items=articles,
    )

    facture = await create_invoice(donnees, db=db, current_user=current_user)
    invoice_id = facture.invoice_id if hasattr(facture, "invoice_id") else facture["invoice_id"]

    order.invoice_id = invoice_id
    db.commit()

    bon = None
    if payload.creer_bon_livraison:
        try:
            resultat = await create_delivery_note_from_invoice(invoice_id, db=db, current_user=current_user)
            bon = resultat.get("delivery_note_id") if isinstance(resultat, dict) else None
        except Exception as erreur:
            # La facture, elle, est bien créée : on ne la perd pas pour un BL.
            logging.warning("Bon de livraison non créé pour %s : %s", order.order_number, erreur)

    numero = facture.invoice_number if hasattr(facture, "invoice_number") else facture["invoice_number"]
    logging.info("Facture %s générée depuis la commande %s", numero, order.order_number)
    return {
        "ok": True,
        "invoice_id": invoice_id,
        "invoice_number": numero,
        "delivery_note_id": bon,
    }


@router.get("/orders/stats")
def admin_order_stats(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Compteurs pour le tableau de bord boutique."""
    by_status = dict(
        db.query(ShopOrder.status, func.count(ShopOrder.order_id))
        .group_by(ShopOrder.status).all()
    )
    revenue = db.query(func.coalesce(func.sum(ShopOrder.total), 0)).filter(
        ShopOrder.status.notin_(["annulée"])
    ).scalar()
    return {
        "total_orders": sum(by_status.values()),
        "by_status": {s: by_status.get(s, 0) for s in ORDER_STATUSES},
        "revenue": float(revenue or 0),
        "pending": by_status.get("en attente", 0),
    }


# ---------------------------------------------------------------- livraisons

@router.get("/deliveries")
def admin_list_deliveries(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ShopDelivery).options(joinedload(ShopDelivery.order))
    if status:
        query = query.filter(ShopDelivery.status == status)
    deliveries = query.order_by(ShopDelivery.created_at.desc()).limit(200).all()

    return [{
        "delivery_id": d.delivery_id,
        "order_id": d.order_id,
        "order_number": d.order.order_number if d.order else None,
        "customer_name": d.order.customer_name if d.order else None,
        "customer_phone": d.order.customer_phone if d.order else None,
        "status": d.status,
        "carrier": d.carrier,
        "tracking_number": d.tracking_number,
        "delivery_address": d.delivery_address,
        "delivery_city": d.delivery_city,
        "scheduled_date": d.scheduled_date,
        "delivered_at": d.delivered_at,
        "delivery_fee": float(d.delivery_fee or 0),
        "notes": d.notes,
    } for d in deliveries]


class DeliveryUpdate(BaseModel):
    status: Optional[str] = None
    carrier: Optional[str] = None
    tracking_number: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_city: Optional[str] = None
    scheduled_date: Optional[datetime] = None
    delivery_fee: Optional[float] = None
    notes: Optional[str] = None


@router.put("/deliveries/{delivery_id}")
def admin_update_delivery(
    delivery_id: int,
    payload: DeliveryUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    delivery = db.query(ShopDelivery).filter(ShopDelivery.delivery_id == delivery_id).first()
    if not delivery:
        raise HTTPException(status_code=404, detail="Livraison introuvable")

    a_prevenir = None

    if payload.status is not None:
        if payload.status not in DELIVERY_STATUSES:
            raise HTTPException(status_code=400, detail=f"Statut invalide. Attendu: {', '.join(DELIVERY_STATUSES)}")
        etait_livree = delivery.status == "livrée"
        delivery.status = payload.status
        if payload.status == "livrée":
            delivery.delivered_at = delivery.delivered_at or datetime.now()
            # Une livraison terminée fait basculer la commande en « livrée ».
            if delivery.order and delivery.order.status != "annulée":
                delivery.order.status = "livrée"
                if not etait_livree:
                    a_prevenir = delivery.order

    for field in ("carrier", "tracking_number", "delivery_address", "delivery_city", "notes"):
        value = getattr(payload, field)
        if value is not None:
            setattr(delivery, field, value)
    if payload.scheduled_date is not None:
        delivery.scheduled_date = payload.scheduled_date
    if payload.delivery_fee is not None:
        delivery.delivery_fee = payload.delivery_fee

    db.commit()

    # Après le commit, pour la même raison que côté commandes : la note est une
    # courtoisie, elle ne doit ni retenir la transaction ni la faire échouer.
    if a_prevenir is not None:
        _prevenir_livraison(a_prevenir)

    return {"ok": True, "delivery_id": delivery.delivery_id, "status": delivery.status}


# ---------------------------------------------------------------- réglages

@router.get("/settings")
def admin_get_settings(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return get_all_settings(db)


@router.put("/settings")
def admin_update_settings(
    payload: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Enregistre la configuration de la page d'accueil et des coordonnées."""
    for key, value in (payload or {}).items():
        if key not in DEFAULT_SETTINGS:
            continue  # On n'accepte que les clés connues
        row = db.query(ShopSetting).filter(ShopSetting.key == key).first()
        if row:
            row.value = str(value) if value is not None else None
        else:
            db.add(ShopSetting(key=key, value=str(value) if value is not None else None,
                               group="homepage" if key.startswith(("hero", "featured", "banner")) else "general"))
    db.commit()
    return get_all_settings(db)


# ---------------------------------------------------------------- bannières

@router.post("/upload-banner")
async def upload_shop_banner(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Téléverse une image de bannière et renvoie son URL publique."""
    import uuid
    from pathlib import Path

    allowed = {".jpg", ".jpeg", ".png", ".webp", ".svg", ".gif"}
    extension = Path(file.filename or "").suffix.lower()
    if extension not in allowed:
        raise HTTPException(status_code=400, detail=f"Format non accepté. Utilisez : {', '.join(sorted(allowed))}")

    content = await file.read()
    if len(content) > 6 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image trop lourde (maximum 6 Mo)")

    upload_dir = Path("static/uploads/shop")
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"banner_{uuid.uuid4().hex}{extension}"
    (upload_dir / filename).write_bytes(content)

    return {"url": f"/static/uploads/shop/{filename}"}


# ---------------------------------------------------------------- zones de livraison

def _slugify_code(text: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "zone"


def _serialize_zone(z: ShopDeliveryZone) -> dict:
    return {
        "zone_id": z.zone_id,
        "code": z.code,
        "name": z.name,
        "fee": float(z.fee or 0),
        "delay": z.delay or "",
        "lat": float(z.lat) if z.lat is not None else None,
        "lng": float(z.lng) if z.lng is not None else None,
        "sort_order": z.sort_order or 0,
        "is_active": bool(z.is_active),
    }


class ZoneIn(BaseModel):
    name: str
    fee: Optional[float] = 0
    delay: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("/zones")
def admin_list_zones(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = (
        db.query(ShopDeliveryZone)
        .order_by(ShopDeliveryZone.sort_order.asc(), ShopDeliveryZone.zone_id.asc())
        .all()
    )
    return [_serialize_zone(z) for z in rows]


@router.post("/zones", status_code=201)
def admin_create_zone(payload: ZoneIn, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="Le nom de la zone est requis")
    code = _slugify_code(payload.name)
    # Unicité du code
    base, i = code, 2
    while db.query(ShopDeliveryZone).filter(ShopDeliveryZone.code == code).first():
        code = f"{base}-{i}"
        i += 1
    zone = ShopDeliveryZone(
        code=code,
        name=payload.name.strip(),
        fee=payload.fee or 0,
        delay=(payload.delay or "").strip() or None,
        lat=payload.lat,
        lng=payload.lng,
        sort_order=payload.sort_order or 0,
        is_active=True if payload.is_active is None else payload.is_active,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return _serialize_zone(zone)


@router.put("/zones/{zone_id}")
def admin_update_zone(zone_id: int, payload: ZoneIn, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    zone = db.query(ShopDeliveryZone).filter(ShopDeliveryZone.zone_id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone introuvable")
    if payload.name is not None and payload.name.strip():
        zone.name = payload.name.strip()
    if payload.fee is not None:
        zone.fee = max(0.0, payload.fee)
    if payload.delay is not None:
        zone.delay = payload.delay.strip() or None
    if payload.lat is not None:
        zone.lat = payload.lat
    if payload.lng is not None:
        zone.lng = payload.lng
    if payload.sort_order is not None:
        zone.sort_order = payload.sort_order
    if payload.is_active is not None:
        zone.is_active = payload.is_active
    db.commit()
    db.refresh(zone)
    return _serialize_zone(zone)


@router.delete("/zones/{zone_id}")
def admin_delete_zone(zone_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    zone = db.query(ShopDeliveryZone).filter(ShopDeliveryZone.zone_id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone introuvable")
    db.delete(zone)
    db.commit()
    return {"deleted": zone_id}


# ---------------------------------------------------------------- demandes produit

DEMAND_STATUSES = ["en attente", "disponible", "annulée"]


def _serialize_demand(d: ShopDemand) -> dict:
    return {
        "demand_id": d.demand_id,
        "demand_number": d.demand_number,
        "product_id": d.product_id,
        "product_name": d.product_name,
        "quantity": d.quantity,
        "customer_name": d.customer_name,
        "customer_phone": d.customer_phone,
        "customer_email": d.customer_email,
        "status": d.status,
        "notes": d.notes,
        "created_at": d.created_at,
    }


@router.get("/demands")
def admin_list_demands(
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ShopDemand)
    if status:
        query = query.filter(ShopDemand.status == status)
    total = query.count()
    rows = (
        query.order_by(ShopDemand.created_at.desc().nullslast(), ShopDemand.demand_id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "demands": [_serialize_demand(d) for d in rows],
        "pagination": {"total": total, "page": page, "limit": limit,
                       "pages": max(1, (total + limit - 1) // limit)},
    }


class DemandUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None


@router.put("/demands/{demand_id}")
def admin_update_demand(
    demand_id: int,
    payload: DemandUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    demand = db.query(ShopDemand).filter(ShopDemand.demand_id == demand_id).first()
    if not demand:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if payload.status is not None:
        if payload.status not in DEMAND_STATUSES:
            raise HTTPException(status_code=400, detail=f"Statut invalide: {payload.status}")
        demand.status = payload.status
    if payload.notes is not None:
        demand.notes = payload.notes or None
    db.commit()
    db.refresh(demand)
    return _serialize_demand(demand)


# ---------------------------------------------------------- produits « boutique »
#
# Produits qu'on veut vendre sans les avoir en stock (vitrine / sur commande).
# Ils vivent dans la table `products` avec `shop_only=True` : publiés sur le site
# (toujours « sur commande » car quantité 0), mais exclus des écrans de gestion
# de stock (voir routers/products.py et le tableau de bord).

class ShopOnlyProductCreate(BaseModel):
    name: str
    price: float
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    condition: Optional[str] = "neuf"
    description: Optional[str] = None
    is_published: bool = True
    is_featured: bool = False


@router.post("/products", status_code=201)
def admin_create_shop_only_product(
    payload: ShopOnlyProductCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crée un produit « boutique uniquement » (hors inventaire physique)."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Le nom du produit est requis")
    if payload.price is None or payload.price < 0:
        raise HTTPException(status_code=400, detail="Prix invalide")

    product = Product(
        name=name,
        price=payload.price,
        quantity=0,
        category=(payload.category or "").strip() or None,
        brand=(payload.brand or "").strip() or None,
        model=(payload.model or "").strip() or None,
        condition=(payload.condition or "neuf").strip() or "neuf",
        description=(payload.description or None),
        shop_only=True,
        is_archived=False,
        entry_date=datetime.utcnow(),
    )
    db.add(product)
    db.flush()  # pour disposer de product_id

    shop = ShopProduct(
        product_id=product.product_id,
        is_published=bool(payload.is_published),
        is_featured=bool(payload.is_featured),
    )
    db.add(shop)
    db.commit()
    db.refresh(product)
    db.refresh(shop)
    return serialize_product(product, shop)


class ConvertToStockPayload(BaseModel):
    quantity: int = 0
    purchase_price: Optional[float] = None


@router.post("/products/{product_id}/convert-to-stock")
def admin_convert_shop_product_to_stock(
    product_id: int,
    payload: ConvertToStockPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Passe un produit « boutique uniquement » dans l'inventaire physique."""
    product = db.query(Product).filter(Product.product_id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    if not product.shop_only:
        raise HTTPException(status_code=400, detail="Ce produit est déjà dans le stock")

    qty = max(0, int(payload.quantity or 0))
    product.shop_only = False
    product.quantity = qty
    if payload.purchase_price is not None:
        product.purchase_price = payload.purchase_price

    # Trace l'entrée initiale pour l'historique des mouvements.
    if qty > 0:
        db.add(StockMovement(
            product_id=product.product_id,
            quantity=qty,
            movement_type="IN",
            reference_type="CONVERT",
            notes="Passage de la boutique au stock",
            unit_price=payload.purchase_price or 0,
        ))

    db.commit()
    db.refresh(product)
    return {"product_id": product.product_id, "shop_only": product.shop_only, "quantity": product.quantity}
