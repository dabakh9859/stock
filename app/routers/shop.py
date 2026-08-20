"""
Boutique en ligne Stock.

Le catalogue est celui de la gestion de stock: pas de duplication de produits.
Règle de disponibilité (demandée par le client):
  - override manuel 'epuise'  -> Épuisé  (seul cas possible, jamais automatique)
  - quantité en stock > 0     -> En stock
  - sinon                     -> Sur commande
"""

import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from typing import List, Optional
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

from ..database import (
    get_db, Product, ShopProduct, ShopOrder, ShopOrderItem,
    ShopDelivery, ShopSetting, ShopDeliveryZone, ShopDemand,
)
from ..auth import get_current_user
from ..services.html_sanitizer import html_to_text, render_description

router = APIRouter(prefix="/api/shop", tags=["boutique"])

# ---------------------------------------------------------------- constantes

AVAILABILITY_IN_STOCK = "en stock"
AVAILABILITY_ON_ORDER = "sur commande"
AVAILABILITY_SOLD_OUT = "épuisé"

ORDER_STATUSES = ["en attente", "confirmée", "en préparation", "expédiée", "livrée", "annulée"]
DELIVERY_STATUSES = ["à planifier", "planifiée", "en cours", "livrée", "échouée"]

DEFAULT_SETTINGS = {
    "hero_title": "La high-tech qui vous ressemble",
    "hero_subtitle": "Ordinateurs, gaming, smartphones et accessoires — sélectionnés, testés, garantis.",
    "hero_cta_label": "Découvrir la boutique",
    "hero_image": "",
    "featured_title": "Sélection du moment",
    "featured_subtitle": "Nos coups de cœur, disponibles tout de suite.",
    "banner_text": "Livraison à Dakar sous 24h • Garantie sur tout le matériel",
    "shop_phone": "",
    "shop_whatsapp": "",
    "shop_email": "",
    "shop_address": "",
    "delivery_fee_default": "0",
    "currency_suffix": "FCFA",

    # Alerte à la réception d'une commande
    "notify_on_order": "1",
    "notify_numbers": "",          # plusieurs numéros séparés par des virgules

    # Gestion du stock au passage de commande
    # "confirm" (défaut) = déduit à la confirmation, "order" = déduit tout de suite,
    # "never" = jamais déduit automatiquement.
    "stock_mode": "confirm",

    # --- Page d'accueil: carrousel (3 diapositives) ---
    "hero_1_title": "La high-tech qui vous ressemble",
    "hero_1_subtitle": "Ordinateurs, gaming, smartphones et accessoires — sélectionnés, testés, garantis.",
    "hero_1_cta": "Découvrir la boutique",
    "hero_1_link": "/e-commerce/produits",
    "hero_1_image": "/static/img/shop/banners/hero-1.svg",

    "hero_2_title": "En stock, livré vite",
    "hero_2_subtitle": "Le matériel déjà en boutique, prêt à partir chez vous.",
    "hero_2_cta": "Voir les disponibles",
    "hero_2_link": "/e-commerce/produits?availability=en+stock",
    "hero_2_image": "/static/img/shop/banners/hero-2.svg",

    "hero_3_title": "Vous ne trouvez pas ?",
    "hero_3_subtitle": "Un modèle précis en tête ? Commandez-le, nous nous occupons du reste.",
    "hero_3_cta": "Découvrir",
    "hero_3_link": "/e-commerce/produits?availability=sur+commande",
    "hero_3_image": "/static/img/shop/banners/hero-3.svg",

    # --- Carte latérale du carrousel ---
    "side_title": "Disponible tout de suite",
    "side_link": "/e-commerce/produits?availability=en+stock",
    "side_image": "/static/img/shop/banners/side.svg",

    # --- Bannière large ---
    "wide_title": "Le meilleur de la high-tech",
    "wide_subtitle": "Ordinateurs, smartphones et accessoires — testés avant la vente.",
    "wide_price_label": "À partir de",
    "wide_image": "/static/img/shop/banners/wide.svg",
    "wide_link": "/e-commerce/produits",

    # --- Titres des sections ---
    "section_top_title": "Nos meilleures ventes",
    "section_recent_title": "Récemment ajoutés",
    "section_brands_title": "Nos marques",

    # --- Affichage ---
    "show_brands": "1",
    "show_offers": "1",
    "show_recent": "1",
}


def compute_availability(product: Product, shop: Optional[ShopProduct]) -> str:
    """Disponibilité affichée au client. Voir la règle en tête de module."""
    if shop is not None and (shop.availability_override or "").strip().lower() == "epuise":
        return AVAILABILITY_SOLD_OUT
    try:
        quantity = int(product.quantity or 0)
    except (TypeError, ValueError):
        quantity = 0
    return AVAILABILITY_IN_STOCK if quantity > 0 else AVAILABILITY_ON_ORDER


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Illustration de repli par rayon. On ne l'écrit jamais en base: la photo
# réelle du produit reprend automatiquement la main dès qu'elle est ajoutée.
FALLBACK_ART = [
    ("smartphone", ("smartphone", "telephone", "téléphone", "phone", "mobile", "iphone")),
    ("tablette", ("tablette", "tablet", "ipad")),
    ("ordinateur", ("ordinateur", "laptop", "pc", "macbook", "portable")),
    ("casque", ("casque", "headphone", "audio")),
    ("ecouteur", ("ecouteur", "écouteur", "earbud", "airpod", "sans fil")),
    ("montre", ("montre", "watch")),
    ("ecran", ("ecran", "écran", "moniteur", "display", "tv")),
    ("accessoire", ("accessoire", "cable", "câble", "chargeur", "adaptateur")),
]


def _fallback_image(product: Product) -> str:
    """Visuel d'attente choisi d'après le rayon, puis le nom du produit."""
    haystack = " ".join(filter(None, [
        (product.category or ""), (product.name or ""), (product.model or "")
    ])).lower()
    for slug, keywords in FALLBACK_ART:
        if any(word in haystack for word in keywords):
            return f"/static/img/shop/products/{slug}.svg"
    return "/static/img/shop/products/generique.svg"


_UPLOADS_DIR = "static/uploads/products"
_media_version_cache = {"value": "0", "checked_at": 0.0}


def _media_version() -> str:
    """Jeton de version des images, basé sur la dernière modification du dossier
    d'uploads. Change dès qu'une image est ajoutée ou réécrite (upload, filigrane) :
    ajouté en `?v=` aux URLs, il force les navigateurs à recharger malgré le cache
    de 7 jours — le nom de fichier, lui, ne change pas quand le contenu est réécrit.
    Recalculé au plus toutes les 60 s pour ne pas interroger le disque à chaque appel."""
    now = time.time()
    if now - _media_version_cache["checked_at"] > 60:
        try:
            _media_version_cache["value"] = str(int(os.path.getmtime(_UPLOADS_DIR)))
        except Exception:
            pass
        _media_version_cache["checked_at"] = now
    return _media_version_cache["value"]


def _public_image_url(path: Optional[str]) -> Optional[str]:
    """Chemin stocké en base -> URL servable. Le stock enregistre un chemin relatif."""
    if not path:
        return None
    path = str(path).strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return path
    if not path.startswith("/"):
        path = "/" + path.lstrip("/")
    # Cache-busting des images servies localement (voir _media_version).
    if path.startswith("/static/"):
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}v={_media_version()}"
    return path


def serialize_product(product: Product, shop: Optional[ShopProduct], include_gallery: bool = False) -> dict:
    """Vue boutique d'un produit du stock.

    `include_gallery` : joindre toutes les images de la galerie (`product_images`),
    l'image principale (`product.image_path`) en tête. Réservé au détail — sur une
    liste de produits, charger la galerie de chacun ferait une requête par produit.
    """
    price = _to_float(shop.shop_price if (shop and shop.shop_price is not None) else product.price) or 0.0
    price_max = _to_float(shop.shop_price_max) if shop else None
    description = (shop.shop_description if (shop and shop.shop_description) else product.description) or ""

    # Présentation vitrine (surcharges boutique, parité nouveau site).
    old_price = _to_float(shop.old_price) if shop else None
    specs_text = (shop.specs if (shop and shop.specs) else "") or ""
    specs = [line.strip() for line in specs_text.splitlines() if line.strip()]
    rating = _to_float(shop.rating) if shop else None

    primary_image = _public_image_url(product.image_path) or _fallback_image(product)

    # Galerie complète, principale en tête, sans doublon. La principale est
    # normalement la première ligne de la galerie : la déduplication l'évite.
    images = [primary_image]
    if include_gallery:
        gallery = sorted(product.gallery, key=lambda g: (g.sort_order or 0, g.image_id))
        for item in gallery:
            url = _public_image_url(item.image_path)
            if url and url not in images:
                images.append(url)

    return {
        "product_id": product.product_id,
        "name": product.name,
        "brand": product.brand,
        "model": product.model,
        "category": product.category,
        "condition": product.condition,
        "description": description,
        "description_html": render_description(description),
        "description_text": html_to_text(description),
        "image_path": primary_image,
        "images": images,
        "has_real_image": bool(product.image_path),
        "price": price,
        "price_max": price_max if (price_max and price_max > price) else None,
        "old_price": old_price if (old_price and old_price > price) else None,
        "specs": specs,
        "availability": compute_availability(product, shop),
        "is_featured": bool(shop.is_featured) if shop else False,
        "is_new": bool(shop.is_new) if shop else False,
        "bestseller": bool(shop.is_bestseller) if shop else False,
        "rating": rating,
        "reviews": int(shop.reviews_count or 0) if shop else 0,
        "is_published": bool(shop.is_published) if shop else True,
        "quantity": int(product.quantity or 0),
    }


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.query(ShopSetting).filter(ShopSetting.key == key).first()
    if row and row.value is not None:
        return row.value
    return DEFAULT_SETTINGS.get(key, default)


def get_all_settings(db: Session) -> dict:
    values = dict(DEFAULT_SETTINGS)
    for row in db.query(ShopSetting).all():
        if row.value is not None:
            values[row.key] = row.value
    return values


def _published_query(db: Session):
    """Produits visibles en boutique: non archivés et non dépubliés."""
    return (
        db.query(Product, ShopProduct)
        .outerjoin(ShopProduct, ShopProduct.product_id == Product.product_id)
        .filter(or_(Product.is_archived.is_(False), Product.is_archived.is_(None)))
        .filter(or_(ShopProduct.is_published.is_(True), ShopProduct.shop_product_id.is_(None)))
    )


# ---------------------------------------------------------------- API publique

@router.get("/products")
def list_products(
    search: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    availability: Optional[str] = None,
    featured: Optional[bool] = None,
    sort: str = "recent",
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=96),
    db: Session = Depends(get_db),
):
    """Catalogue public, filtré et paginé."""
    query = _published_query(db)

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(
            Product.name.ilike(pattern),
            Product.brand.ilike(pattern),
            Product.model.ilike(pattern),
            Product.description.ilike(pattern),
        ))
    if category:
        query = query.filter(Product.category == category)
    if brand:
        query = query.filter(Product.brand == brand)
    if featured is True:
        query = query.filter(ShopProduct.is_featured.is_(True))

    if sort == "price_asc":
        query = query.order_by(Product.price.asc())
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc())
    elif sort == "name":
        query = query.order_by(Product.name.asc())
    else:
        query = query.order_by(Product.created_at.desc().nullslast(), Product.product_id.desc())

    rows = query.all()
    items = [serialize_product(p, s) for p, s in rows]

    # Sur une liste, on n'attache pas les options — seulement de quoi afficher
    # « À partir de ». Charger les groupes de chaque produit ferait une requête
    # par ligne pour une information que la vignette n'affiche pas.
    from ..services.shop_variants import resoudre
    for item, (produit, reglages) in zip(items, rows):
        resolution = resoudre(db, produit, reglages)
        item["prix_defaut"] = resolution["prix_defaut"]
        item["prix_max"] = resolution["prix_max"]
        item["a_partir_de"] = resolution["a_partir_de"]

    # Le filtre de disponibilité s'applique après calcul (règle métier, pas une colonne).
    if availability:
        wanted = availability.strip().lower()
        items = [i for i in items if i["availability"] == wanted]

    total = len(items)
    start = (page - 1) * limit
    return {
        "products": items[start:start + limit],
        "pagination": {
            "total": total,
            "page": page,
            "limit": limit,
            "pages": max(1, (total + limit - 1) // limit),
        },
    }


@router.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    row = (
        _published_query(db)
        # La galerie est jointe ici pour éviter une requête par image sur le détail.
        .options(joinedload(Product.gallery))
        .filter(Product.product_id == product_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Produit introuvable")
    product, shop = row
    data = serialize_product(product, shop, include_gallery=True)

    related = (
        _published_query(db)
        .filter(Product.category == product.category, Product.product_id != product_id)
        .limit(4)
        .all()
    )
    data["related"] = [serialize_product(p, s) for p, s in related]

    # Variantes commerciales : groupes hérités de la catégorie ou rattachés au
    # produit, suppléments et exceptions déjà appliqués. La première option de
    # chaque groupe est celle que le site présélectionne.
    from ..services.shop_variants import resoudre
    data["variantes"] = resoudre(db, product, shop)
    return data


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)):
    rows = (
        _published_query(db)
        .with_entities(Product.category, func.count(Product.product_id))
        .filter(Product.category.isnot(None), Product.category != "")
        .group_by(Product.category)
        .order_by(func.count(Product.product_id).desc())
        .all()
    )
    return [{"name": name, "count": count} for name, count in rows]


@router.get("/settings")
def public_settings(db: Session = Depends(get_db)):
    return get_all_settings(db)


# Logo de la boutique, servi comme image légère pour la barre de navigation du
# site. Le logo enregistré (data URI) fait plusieurs centaines de Ko : on le
# redimensionne une fois et on le garde en cache mémoire.
_shop_logo_cache = {"png": None, "checked_at": 0.0}


@router.get("/logo")
def shop_logo(db: Session = Depends(get_db)):
    """Logo de la société en PNG (hauteur ~120 px). 404 si aucun logo configuré."""
    now = time.time()
    if _shop_logo_cache["png"] is None or now - _shop_logo_cache["checked_at"] > 300:
        _shop_logo_cache["checked_at"] = now
        try:
            import base64
            import io
            from PIL import Image
            from ..services.watermark import get_shop_logo

            raw = get_shop_logo(db)
            if raw:
                data = raw.split(",", 1)[1] if raw.startswith("data:image") else raw
                img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
                # Réduit à une hauteur d'affichage raisonnable (retina inclus).
                target_h = 120
                if img.height > target_h:
                    w = max(1, int(img.width * target_h / img.height))
                    img = img.resize((w, target_h), Image.LANCZOS)
                out = io.BytesIO()
                img.save(out, format="PNG")
                _shop_logo_cache["png"] = out.getvalue()
            else:
                _shop_logo_cache["png"] = None
        except Exception as exc:
            logging.warning("Logo boutique indisponible : %s", exc)
            _shop_logo_cache["png"] = None

    if not _shop_logo_cache["png"]:
        raise HTTPException(status_code=404, detail="Aucun logo configuré")
    return Response(
        content=_shop_logo_cache["png"],
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/zones")
def list_zones(db: Session = Depends(get_db)):
    """Zones de livraison actives (frais + délai), pour le tunnel de commande."""
    rows = (
        db.query(ShopDeliveryZone)
        .filter(ShopDeliveryZone.is_active.is_(True))
        .order_by(ShopDeliveryZone.sort_order.asc(), ShopDeliveryZone.zone_id.asc())
        .all()
    )
    return [
        {
            "id": z.code or str(z.zone_id),
            "zone_id": z.zone_id,
            "name": z.name,
            "fee": _to_float(z.fee) or 0,
            "delay": z.delay or "",
            "lat": _to_float(z.lat),
            "lng": _to_float(z.lng),
        }
        for z in rows
    ]


# ---------------------------------------------------------------- commandes

class OrderItemIn(BaseModel):
    product_id: int
    quantity: int = Field(ge=1)
    # Options de variantes retenues par le client. Seuls les identifiants
    # circulent : le prix correspondant est recalculé côté serveur.
    variant_option_ids: Optional[List[int]] = None


class OrderIn(BaseModel):
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_city: Optional[str] = None
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    items: List[OrderItemIn]
    # Zone de livraison + point exact (nouveau site)
    zone_code: Optional[str] = None       # code de zone (ex. "plateau")
    delivery_details: Optional[str] = None
    delivery_lat: Optional[float] = None
    delivery_lng: Optional[float] = None
    customer_id: Optional[int] = None     # rempli si client connecté (Phase 5)


def _next_order_number(db: Session) -> str:
    prefix = f"CMD-{datetime.now().strftime('%y%m')}-"
    last = (
        db.query(ShopOrder.order_number)
        .filter(ShopOrder.order_number.like(f"{prefix}%"))
        .order_by(ShopOrder.order_number.desc())
        .first()
    )
    counter = 1
    if last and last[0]:
        try:
            counter = int(str(last[0]).rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            counter = 1
    return f"{prefix}{counter:04d}"


def _apply_stock_movement(db: Session, order, sign: int) -> None:
    """
    Ajuste le stock des articles d'une commande. sign=-1 déduit, sign=+1 restitue.
    Les produits « sur commande » ne sont pas touchés: ils ne sont pas en stock.
    """
    for item in (order.items or []):
        if not item.product_id or item.availability_at_order == AVAILABILITY_ON_ORDER:
            continue
        product = db.query(Product).filter(Product.product_id == item.product_id).first()
        if not product:
            continue
        current = int(product.quantity or 0)
        product.quantity = max(0, current + sign * int(item.quantity or 0))


@router.post("/orders", status_code=201)
async def create_order(payload: OrderIn, db: Session = Depends(get_db)):
    """Commande client. Un produit épuisé ne peut pas être commandé."""
    if not payload.items:
        raise HTTPException(status_code=400, detail="Le panier est vide")

    # Résolution de la zone de livraison (par code) → frais + libellé figés.
    zone = None
    if payload.zone_code:
        zone = (
            db.query(ShopDeliveryZone)
            .filter(ShopDeliveryZone.code == payload.zone_code.strip())
            .first()
        )

    order = ShopOrder(
        order_number=_next_order_number(db),
        customer_name=payload.customer_name.strip(),
        customer_phone=payload.customer_phone.strip(),
        customer_email=(payload.customer_email or "").strip() or None,
        delivery_address=payload.delivery_address,
        delivery_city=payload.delivery_city,
        zone_id=zone.zone_id if zone else None,
        zone_name=zone.name if zone else None,
        delivery_details=payload.delivery_details,
        delivery_lat=payload.delivery_lat,
        delivery_lng=payload.delivery_lng,
        customer_id=payload.customer_id,
        payment_method=payload.payment_method,
        notes=payload.notes,
        status="en attente",
    )

    subtotal = Decimal("0")
    for line in payload.items:
        row = (
            db.query(Product, ShopProduct)
            .outerjoin(ShopProduct, ShopProduct.product_id == Product.product_id)
            .filter(Product.product_id == line.product_id)
            .first()
        )
        if not row:
            raise HTTPException(status_code=400, detail=f"Produit {line.product_id} introuvable")

        product, shop = row
        availability = compute_availability(product, shop)
        if availability == AVAILABILITY_SOLD_OUT:
            raise HTTPException(status_code=400, detail=f"« {product.name} » est épuisé")

        unit_price = Decimal(str(serialize_product(product, shop)["price"]))

        # Suppléments des variantes choisies. Le montant est TOUJOURS recalculé
        # ici à partir des seuls identifiants d'options : reprendre un prix
        # envoyé par le navigateur laisserait commander un iPhone à 1 F.
        options_choisies = list(getattr(line, "variant_option_ids", None) or [])
        resume_variantes = None
        if options_choisies:
            from ..services.shop_variants import resume_selection, supplement_total
            unit_price += supplement_total(db, product.product_id, options_choisies)
            resume_variantes = resume_selection(db, options_choisies) or None

        line_total = unit_price * line.quantity
        subtotal += line_total

        order.items.append(ShopOrderItem(
            product_id=product.product_id,
            product_name=product.name,
            quantity=line.quantity,
            price=unit_price,
            total=line_total,
            availability_at_order=availability,
            variant_summary=resume_variantes,
        ))

    # Frais de livraison : ceux de la zone choisie, sinon le défaut global.
    if zone is not None and zone.fee is not None:
        delivery_fee = Decimal(str(zone.fee))
    else:
        try:
            delivery_fee = Decimal(get_setting(db, "delivery_fee_default", "0") or "0")
        except Exception:
            delivery_fee = Decimal("0")

    order.subtotal = subtotal
    order.delivery_fee = delivery_fee
    order.total = subtotal + delivery_fee

    db.add(order)
    db.flush()

    # Une commande crée d'office son suivi de livraison.
    db.add(ShopDelivery(
        order_id=order.order_id,
        delivery_address=order.delivery_address,
        delivery_city=order.delivery_city,
        delivery_fee=delivery_fee,
        status="à planifier",
    ))

    # Déduction du stock selon le mode choisi en back-office.
    if (get_setting(db, "stock_mode", "confirm") or "confirm").strip() == "order":
        _apply_stock_movement(db, order, sign=-1)

    db.commit()
    db.refresh(order)

    # L'alerte ne doit jamais faire échouer la commande du client.
    notification = {"sent": 0}
    try:
        from ..services.shop_notifier import notify_new_order
        notification = await notify_new_order(db, order)
    except Exception as e:
        logging.warning("Alerte de commande impossible: %s", e)

    return {
        "order_id": order.order_id,
        "order_number": order.order_number,
        "total": float(order.total),
        "notified": notification.get("sent", 0),
    }


# ---------------------------------------------------------------- demandes produit

class DemandIn(BaseModel):
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    customer_name: str
    customer_phone: str
    customer_email: Optional[str] = None
    customer_id: Optional[int] = None
    notes: Optional[str] = None


def _next_demand_number(db: Session) -> str:
    prefix = f"DA-{datetime.now().strftime('%y%m')}-"
    last = (
        db.query(ShopDemand.demand_number)
        .filter(ShopDemand.demand_number.like(f"{prefix}%"))
        .order_by(ShopDemand.demand_number.desc())
        .first()
    )
    counter = 1
    if last and last[0]:
        try:
            counter = int(str(last[0]).rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            counter = 1
    return f"{prefix}{counter:04d}"


@router.post("/demands", status_code=201)
async def create_demand(payload: DemandIn, db: Session = Depends(get_db)):
    """Demande d'un produit (souvent hors stock). Génère une référence DA-."""
    product = None
    if payload.product_id:
        product = db.query(Product).filter(Product.product_id == payload.product_id).first()
    product_name = (product.name if product else None) or (payload.product_name or "").strip()
    if not product_name:
        raise HTTPException(status_code=400, detail="Produit de la demande manquant")

    demand = ShopDemand(
        demand_number=_next_demand_number(db),
        product_id=product.product_id if product else None,
        product_name=product_name,
        quantity=payload.quantity,
        customer_name=payload.customer_name.strip(),
        customer_phone=payload.customer_phone.strip(),
        customer_email=(payload.customer_email or "").strip() or None,
        customer_id=payload.customer_id,
        notes=payload.notes,
        status="en attente",
    )
    db.add(demand)
    db.commit()
    db.refresh(demand)

    # L'alerte ne doit jamais faire échouer la demande du client.
    notification = {"sent": 0}
    try:
        from ..services.shop_notifier import notify_new_demand
        notification = await notify_new_demand(db, demand)
    except Exception as e:
        logging.warning("Alerte de demande impossible: %s", e)

    return {
        "demand_id": demand.demand_id,
        "demand_number": demand.demand_number,
        "notified": notification.get("sent", 0),
    }


@router.get("/demands/track/{demand_number}")
def track_demand(demand_number: str, db: Session = Depends(get_db)):
    demand = (
        db.query(ShopDemand)
        .filter(ShopDemand.demand_number == demand_number.strip())
        .first()
    )
    if not demand:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    return {
        "demand_number": demand.demand_number,
        "product_name": demand.product_name,
        "quantity": demand.quantity,
        "status": demand.status,
        "created_at": demand.created_at,
    }


def _phone_key(raw) -> str:
    """Ramène un numéro à ses chiffres, préfixe pays compris.

    La boutique envoie « 221771234567 », l'équipe saisit parfois
    « 77 123 45 67 » ou « +221 77 123 45 67 » : sans cette mise à plat, deux
    écritures du même numéro ne se reconnaissent pas.
    """
    digits = "".join(c for c in str(raw or "") if c.isdigit())
    if not digits:
        return ""
    return digits if digits.startswith("221") else f"221{digits}"


def _order_payload(order) -> dict:
    """Représentation d'une commande pour la vitrine."""
    return {
        "order_number": order.order_number,
        "status": order.status,
        # L'encaissement se range dans payment_status, pas dans status : après un
        # paiement en ligne, status vaut « confirmée ». Sans ce champ, la page de
        # confirmation de la boutique ne peut pas annoncer « c'est payé ».
        "payment_status": order.payment_status,
        "created_at": order.created_at,
        "subtotal": float(order.subtotal or 0),
        "delivery_fee": float(order.delivery_fee or 0),
        "total": float(order.total or 0),
        "delivery_city": order.delivery_city,
        "delivery_address": order.delivery_address,
        "delivery_status": order.delivery.status if order.delivery else None,
        "customer_reported_at": order.delivery.customer_reported_at if order.delivery else None,
        "items": [
            {
                "name": i.product_name,
                "quantity": i.quantity,
                "price": float(i.price or 0),
                "total": float(i.total or 0),
            }
            for i in order.items
        ],
    }


@router.get("/orders/track/{order_number}")
def track_order(
    order_number: str,
    phone: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Suivi public d'une commande par son numéro.

    `phone` est facultatif : la page de confirmation, atteinte juste après la
    commande, n'a que la référence. Le suivi « sans compte » le fournit, lui, et
    l'on exige alors qu'il corresponde — sinon une référence devinée dévoilerait
    l'adresse de livraison d'un tiers.
    """
    order = (
        db.query(ShopOrder)
        .options(joinedload(ShopOrder.items), joinedload(ShopOrder.delivery))
        .filter(ShopOrder.order_number == order_number.strip())
        .first()
    )

    # Numéro fourni mais différent : on répond exactement comme pour une
    # référence inconnue, afin de ne pas révéler que celle-ci existe.
    if not order or (phone and _phone_key(phone) != _phone_key(order.customer_phone)):
        raise HTTPException(status_code=404, detail="Commande introuvable")

    return _order_payload(order)


@router.post("/orders/{order_number}/reported-delivered")
def report_delivered(order_number: str, db: Session = Depends(get_db)):
    """Le client signale avoir reçu sa commande.

    **Ce signalement ne fait pas foi.** Il ne change ni le statut de la commande
    ni celui de la livraison : il pose seulement une date que la boutique voit
    dans son écran, pour confirmer ensuite avec le livreur. Un client qui ne sait
    pas s'en servir ne bloque donc rien — l'équipe confirme sans lui.

    Volontairement ouvert à qui détient la référence : puisque rien n'est décidé
    ici, exiger une identification écarterait la majorité des clients, qui
    commandent sans compte.
    """
    order = (
        db.query(ShopOrder)
        .options(joinedload(ShopOrder.delivery))
        .filter(ShopOrder.order_number == order_number.strip())
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Commande introuvable")

    if order.status == "annulée":
        raise HTTPException(status_code=400, detail="Cette commande est annulée.")

    livraison = order.delivery
    if livraison is None:
        # Commande antérieure au suivi de livraison : on la crée à la volée
        # plutôt que de refuser le signalement.
        livraison = ShopDelivery(
            order_id=order.order_id,
            status="à planifier",
            delivery_address=order.delivery_address,
            delivery_city=order.delivery_city,
        )
        db.add(livraison)
        db.flush()

    deja_livree = livraison.status == "livrée" or order.status == "livrée"

    # Idempotent : re-signaler ne réécrit pas la première date.
    if livraison.customer_reported_at is None and not deja_livree:
        livraison.customer_reported_at = datetime.now()
        db.commit()

    return {
        "ok": True,
        "deja_confirmee": deja_livree,
        "signale_le": livraison.customer_reported_at,
    }


@router.get("/orders/by-phone")
def orders_by_phone(phone: str, db: Session = Depends(get_db), limit: int = 20):
    """Commandes rattachées à un numéro, pour l'espace client de la vitrine.

    C'est l'application de gestion de stock qui détient les commandes : le site
    n'en garde aucune copie. Sans cette route, son espace client interrogeait sa
    propre base, restée vide depuis que les commandes ont déménagé — et
    n'affichait donc jamais rien.
    """
    key = _phone_key(phone)
    if not key:
        raise HTTPException(status_code=400, detail="Numéro de téléphone requis")

    orders = (
        db.query(ShopOrder)
        .options(joinedload(ShopOrder.items), joinedload(ShopOrder.delivery))
        .order_by(ShopOrder.created_at.desc())
        .limit(200)
        .all()
    )
    # La comparaison se fait en Python : les numéros sont stockés tels qu'ils ont
    # été saisis, et aucun index ne porterait sur leur forme normalisée.
    retenues = [o for o in orders if _phone_key(o.customer_phone) == key][:limit]
    return {"orders": [_order_payload(o) for o in retenues]}
