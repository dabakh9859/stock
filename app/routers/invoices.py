from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_, or_
from typing import List, Optional
from datetime import datetime, date, timedelta
import httpx
from ..database import (
    get_db,
    Invoice,
    InvoiceItem,
    InvoiceExchangeItem,
    InvoicePayment,
    Client,
    Product,
    ProductVariant,
    ProductVariantAttribute,
    Category,
    DeliveryNote,
    DeliveryNoteItem,
    SupplierInvoice,
    SupplierInvoicePayment,
    DailySale,
)
from ..database import DailyPurchase
from ..schemas import InvoiceCreate, InvoiceResponse, InvoiceItemResponse
from ..auth import get_current_user
from ..routers.stock_movements import create_stock_movement_entry
from ..services.stats_manager import recompute_invoices_stats
from ..services.product_duplicates import trouver_fiche_homonyme
from ..services.google_sheets_sync_helper import sync_product_stock_to_sheets
from ..routers.dashboard import invalidate_dashboard_cache
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import logging
import os

router = APIRouter(prefix="/api/invoices", tags=["invoices"])


# ==================== Rattachement ligne <-> unité vendue ====================
# Une ligne de facture portant sur un produit à IMEI référence l'exemplaire
# précis via invoice_items.variant_id / variant_imei. Ces helpers sont la seule
# porte d'entrée pour vendre / remettre en stock une unité : ils gèrent les deux
# modes de suivi des variantes (quantity non nul, ou drapeau is_sold).

def _find_item_variant(db: Session, item) -> Optional[ProductVariant]:
    """Retrouve la variante vendue sur une ligne de facture existante.

    Priorité à variant_id (colonne fiable), puis variant_imei (survit à la
    recréation d'une variante), et en dernier recours l'IMEI éventuellement
    inscrit dans le libellé des lignes historiques.
    """
    variant = None
    if getattr(item, "variant_id", None):
        variant = db.query(ProductVariant).filter(
            ProductVariant.variant_id == item.variant_id
        ).first()
    if variant is None and getattr(item, "variant_imei", None):
        code = str(item.variant_imei).strip()
        if code:
            variant = db.query(ProductVariant).filter(
                func.trim(ProductVariant.imei_serial) == code
            ).first()
    if variant is None and getattr(item, "product_name", None):
        import re as _re
        m = _re.search(r"\(IMEI:\s*([^)]+)\)", str(item.product_name), flags=_re.I)
        if m:
            code = (m.group(1) or "").strip()
            if code:
                variant = db.query(ProductVariant).filter(
                    func.trim(ProductVariant.imei_serial) == code
                ).first()
    return variant


def _release_variant(variant: ProductVariant, quantity: int = 1) -> None:
    """Remet une unité vendue en stock, selon le mode de suivi de la variante."""
    if variant is None:
        return
    if variant.quantity is not None:
        variant.quantity = (variant.quantity or 0) + max(1, int(quantity or 1))
    else:
        variant.is_sold = False


def _reserve_variant(variant: ProductVariant, quantity: int = 1) -> None:
    """Marque une unité comme vendue, selon le mode de suivi de la variante."""
    if variant is None:
        return
    if variant.quantity is not None:
        variant.quantity = (variant.quantity or 0) - max(1, int(quantity or 1))
    else:
        variant.is_sold = True


def _resold_later(db: Session, variant_id: int, invoice) -> bool:
    """Vrai si cet exemplaire est vendu par une facture postérieure.

    Un même IMEI peut légitimement réapparaître (vendu, repris, revendu). Dans ce
    cas, retirer la ligne de la facture d'origine ne doit surtout pas remettre en
    stock un téléphone qui est aujourd'hui chez un autre client.
    """
    if not variant_id:
        return False
    ref_date = getattr(invoice, "date", None)
    query = (
        db.query(InvoiceItem.item_id)
        .join(Invoice, Invoice.invoice_id == InvoiceItem.invoice_id)
        .filter(
            InvoiceItem.variant_id == variant_id,
            InvoiceItem.invoice_id != invoice.invoice_id,
        )
    )
    if ref_date is not None:
        query = query.filter(Invoice.date > ref_date)
    return query.first() is not None


def _restore_sold_units(db: Session, invoice, items) -> set:
    """Remet en stock l'exemplaire précis vendu par chaque ligne produit.

    Utilisé quand des lignes disparaissent d'une facture (modification) et quand
    la facture entière est supprimée. Le rattachement fiable est
    invoice_items.variant_id / variant_imei ; les lignes créées avant ces
    colonnes retombent sur le bloc __SERIALS__ des notes. Une ligne qu'aucune de
    ces sources ne résout ne libère rien du tout : on préfère une unité à
    corriger à la main, signalée dans les logs, à un stock faux en silence.

    Renvoie les variant_id volontairement NON libérés (unité revendue depuis) :
    l'appelant doit s'abstenir de les redécompter s'il réapplique la ligne,
    sinon le stock de la variante partirait dans le négatif.
    """
    skipped = set()
    legacy_items = []
    for it in (items or []):
        if it.product_id is None:
            continue
        variant = _find_item_variant(db, it)
        if variant is None:
            # Produit sans variantes: le stock est purement quantitatif, il a déjà
            # été recrédité par l'appelant. Rien à rattacher, rien à signaler.
            has_variants = db.query(ProductVariant.variant_id).filter(
                ProductVariant.product_id == it.product_id
            ).first() is not None
            if has_variants:
                legacy_items.append(it)
            continue
        if _resold_later(db, variant.variant_id, invoice):
            logging.warning(
                "Facture %s: IMEI %s non remis en stock, il est vendu par une facture postérieure",
                getattr(invoice, "invoice_number", "?"), variant.imei_serial,
            )
            skipped.add(variant.variant_id)
            continue
        _release_variant(variant, it.quantity)

    if not legacy_items:
        return skipped

    invoice_number = getattr(invoice, "invoice_number", "?")
    try:
        import json as _json
        serials_by_product = {}
        txt = str(getattr(invoice, "notes", "") or "")
        if "__SERIALS__=" in txt:
            sub = txt.split("__SERIALS__=", 1)[1]
            cut_idx = sub.find("\n__")
            if cut_idx != -1:
                sub = sub[:cut_idx]
            for entry in (_json.loads(sub.strip()) or []):
                pid = entry.get("product_id")
                if pid is None:
                    continue
                serials_by_product.setdefault(int(pid), []).extend(entry.get("imeis") or [])

        for it in legacy_items:
            pid = int(it.product_id)
            variant = None
            pending = serials_by_product.get(pid) or []
            while pending and variant is None:
                imei = str(pending.pop(0)).strip()
                if imei:
                    variant = db.query(ProductVariant).filter(
                        func.trim(ProductVariant.imei_serial) == imei
                    ).first()
            if variant is None:
                # On ne libère volontairement rien: l'ancien code démarquait ici
                # une unité vendue quelconque du produit, ce qui remettait en
                # stock le mauvais téléphone (et laissait vendu celui qu'on
                # voulait récupérer). Mieux vaut une unité à corriger à la main,
                # signalée dans les logs, qu'un stock faux en silence.
                logging.warning(
                    "Facture %s: ligne %s (produit %s) sans exemplaire rattaché, "
                    "aucune unité remise en stock — à vérifier manuellement",
                    invoice_number, it.item_id, pid,
                )
                continue
            if _resold_later(db, variant.variant_id, invoice):
                logging.warning(
                    "Facture %s: IMEI %s non remis en stock, il est vendu par une facture postérieure",
                    invoice_number, variant.imei_serial,
                )
                skipped.add(variant.variant_id)
                continue
            _release_variant(variant, it.quantity)
    except Exception:
        logging.warning(
            "Facture %s: rattrapage __SERIALS__ impossible lors de la remise en stock",
            invoice_number, exc_info=True,
        )

    return skipped


# Helpers de numérotation
from datetime import datetime as _dt

def _next_invoice_number(db: Session, prefix: Optional[str] = None) -> str:
    """Génère le prochain numéro de facture séquentiel sous la forme PREFIX-####.
    Par défaut, PREFIX = 'FAC'. L'algorithme recherche d'abord les numéros
    existants au format exact PREFIX-<digits> et incrémente le plus grand.
    S'il n'en trouve pas, il tente un fallback sur le plus grand suffixe
    numérique présent et repart ensuite proprement.
    """
    import re
    pf = (prefix or 'FAC').strip('-')
    base_prefix = f"{pf}-"

    # Récupérer tous les numéros existants qui commencent par PREFIX-
    try:
        rows = db.query(Invoice.invoice_number).filter(Invoice.invoice_number.ilike(f"{base_prefix}%")).all()
    except Exception:
        rows = []

    last_seq = 0
    # 1) Chercher le max parmi les numéros au format exact PREFIX-####
    for (num,) in (rows or []):
        if not isinstance(num, str):
            continue
        m = re.fullmatch(rf"{re.escape(pf)}-(\\d+)", num.strip())
        if m:
            val = int(m.group(1))
            if val > last_seq:
                last_seq = val

    # 2) Fallback: si aucun au format exact, prendre le plus grand suffixe numérique
    if last_seq == 0:
        for (num,) in (rows or []):
            if not isinstance(num, str):
                continue
            matches = re.findall(r'(\\d+)', num.strip())
            if matches:
                val = int(matches[-1])  # dernier groupe de chiffres
                if val > last_seq:
                    last_seq = val

    next_seq = last_seq + 1

    # Garantir l'unicité (en cas de race, trous, etc.)
    while True:
        candidate = f"{base_prefix}{next_seq:04d}"
        exists = db.query(Invoice).filter(Invoice.invoice_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1

@router.get("/next-number")
async def get_next_invoice_number(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Retourne le prochain numéro de facture disponible (FAC-####).
    Placé avant la route dynamique '/{invoice_id}' pour éviter un 422 dû à la résolution de chemin.
    """
    try:
        return {"invoice_number": _next_invoice_number(db)}
    except Exception as e:
        logging.error(f"Erreur get_next_invoice_number: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/", response_model=List[InvoiceResponse])
async def list_invoices(
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    client_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les factures avec filtres"""
    # Utiliser un LEFT JOIN pour inclure les factures sans client (ventes flash)
    query = db.query(Invoice, Client.name.label('client_name')).outerjoin(Client, Invoice.client_id == Client.client_id).order_by(desc(Invoice.created_at))

    if search:
        # Recherche de la barre supérieure : le numéro de facture d'abord, qui
        # est ce qu'on a sous la main quand on tient le papier, puis le client.
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Invoice.invoice_number.ilike(pattern),
                Client.name.ilike(pattern),
            )
        )

    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    
    if client_id:
        query = query.filter(Invoice.client_id == client_id)
    
    if start_date:
        query = query.filter(func.date(Invoice.date) >= start_date)
    
    if end_date:
        query = query.filter(func.date(Invoice.date) <= end_date)
    
    results = query.offset(skip).limit(limit).all()
    
    # Construire la réponse avec le nom du client
    invoices = []
    for invoice, client_name in results:
        # Si pas de client (vente flash), utiliser "Vente Flash"
        display_client_name = client_name if client_name else ("Vente Flash" if invoice.invoice_type == 'flash_sale' else "Client inconnu")
        invoice_dict = {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "client_id": invoice.client_id,
            "client_name": display_client_name,
            "quotation_id": invoice.quotation_id,
            "date": invoice.date,
            "due_date": invoice.due_date,
            "status": invoice.status,
            "payment_method": invoice.payment_method,
            "subtotal": float(invoice.subtotal or 0),
            "tax_rate": float(invoice.tax_rate or 0),
            "tax_amount": float(invoice.tax_amount or 0),
            "total": float(invoice.total or 0),
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
            "notes": invoice.notes,
            "show_tax": bool(invoice.show_tax),
            "show_item_prices": bool(getattr(invoice, 'show_item_prices', True)),
            "show_section_totals": bool(getattr(invoice, 'show_section_totals', True)),
            "price_display": invoice.price_display or "FCFA",
            # Champs de garantie
            "has_warranty": bool(getattr(invoice, "has_warranty", False)),
            "warranty_duration": getattr(invoice, "warranty_duration", None),
            "warranty_start_date": getattr(invoice, "warranty_start_date", None),
            "warranty_end_date": getattr(invoice, "warranty_end_date", None),
            "created_at": invoice.created_at,
            "items": []
        }
        invoices.append(invoice_dict)
    
    return invoices

# Simple in-process cache for list responses
_invoices_cache = {}
_CACHE_TTL_SECONDS = 30

@router.get("/paginated")
async def list_invoices_paginated(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=200),
    status_filter: Optional[str] = None,
    client_search: Optional[str] = None,
    search: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    sort_by: Optional[str] = Query("created_at"),  # created_at | date | number | total | status | client
    sort_dir: Optional[str] = Query("desc"),       # asc | desc
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Lister les factures avec pagination, filtres et tri pour la liste principale."""
    # Cache key
    try:
        import time, hashlib
        key_raw = f"p={page}|s={page_size}|sf={status_filter}|cs={client_search}|q={search}|sd={start_date}|ed={end_date}|ob={sort_by}|od={sort_dir}"
        key = hashlib.md5(key_raw.encode()).hexdigest()
        entry = _invoices_cache.get(key)
        if entry and (time.time() - entry['ts']) < _CACHE_TTL_SECONDS:
            return entry['data']
    except Exception:
        key = None
    # Base avec JOIN client pour récupérer le nom
    base = db.query(
        Invoice,
        Client.name.label('client_name')
    ).join(Client, Client.client_id == Invoice.client_id, isouter=True)

    # Filtres
    if status_filter:
        base = base.filter(Invoice.status == status_filter)
    if client_search:
        like = f"%{client_search.strip()}%"
        base = base.filter(Client.name.ilike(like))
    if start_date:
        base = base.filter(func.date(Invoice.date) >= start_date)
    if end_date:
        base = base.filter(func.date(Invoice.date) <= end_date)
    if search:
        s = search.strip()

        # Sous-requête: factures contenant un produit/une variante correspondant au code (barcode/IMEI)
        try:
            from sqlalchemy.sql import exists as _exists
        except Exception:
            _exists = None

        product_match_invoice_ids = None
        try:
            like = f"%{s}%"

            # L'IMEI se cherche dans `daily_sales`, qui enregistre l'appareil
            # RÉELLEMENT vendu sur cette facture.
            #
            # La version précédente joignait `ProductVariant` par `product_id` :
            # elle trouvait le produit possédant cet IMEI, puis renvoyait toutes
            # les factures contenant ce modèle. Chercher un IMEI unique ramenait
            # ainsi 68 factures au lieu d'une seule — la recherche existait, mais
            # ne servait à rien.
            ventes_q = (
                db.query(DailySale.invoice_id)
                .filter(DailySale.invoice_id.isnot(None))
                .filter(
                    or_(
                        func.trim(DailySale.variant_imei).ilike(like),
                        func.trim(DailySale.variant_barcode).ilike(like),
                    )
                )
                .distinct()
            )
            ids = {row[0] for row in ventes_q.all()}

            # Le code-barres d'un PRODUIT reste une recherche par modèle : on
            # veut alors bien toutes les factures qui le contiennent.
            produits_q = (
                db.query(InvoiceItem.invoice_id)
                .join(Product, InvoiceItem.product_id == Product.product_id)
                .filter(func.trim(Product.barcode).ilike(like))
                .distinct()
            )
            ids.update(row[0] for row in produits_q.all())

            # Seconde source : `invoice_items.variant_imei`, renseigné à la vente
            # depuis le 29/07/2026. `daily_sales` couvre presque tout, mais pas
            # les lignes rattachées après coup — deux factures y échappaient.
            # Cette colonne, elle, appartient à la facture : elle ne peut pas
            # manquer.
            lignes_q = (
                db.query(InvoiceItem.invoice_id)
                .filter(func.trim(InvoiceItem.variant_imei).ilike(like))
                .distinct()
            )
            ids.update(row[0] for row in lignes_q.all())

            product_match_invoice_ids = list(ids)
        except Exception:
            product_match_invoice_ids = None

        if s.isdigit():
            # Recherche numérique: matcher l'ID de facture, le numéro, ET les produits/IMEI éventuels
            try:
                id_val = int(s)
                # `invoice_id` est un entier 32 bits. Un IMEI en fait quinze
                # chiffres : le comparer à cette colonne faisait lever
                # « integer out of range » à PostgreSQL, et **toute recherche
                # par IMEI répondait 500**. Au-delà de la borne, ce n'est pas un
                # identifiant de facture — on ne le compare simplement pas.
                if not 0 < id_val <= 2_147_483_647:
                    id_val = None
            except Exception:
                id_val = None

            conditions = []
            if id_val is not None:
                conditions.append(Invoice.invoice_id == id_val)
            conditions.append(Invoice.invoice_number.ilike(f"%{s}%"))
            if product_match_invoice_ids:
                conditions.append(Invoice.invoice_id.in_(product_match_invoice_ids))

            base = base.filter(or_(*conditions))
        else:
            # Recherche texte: numéro de facture ou IMEI/barcode produit/variante
            conditions = [Invoice.invoice_number.ilike(f"%{s}%")]
            if product_match_invoice_ids:
                conditions.append(Invoice.invoice_id.in_(product_match_invoice_ids))
            base = base.filter(or_(*conditions))

    # Total avant pagination
    total = base.count()

    # Tri
    sort_col = Invoice.created_at
    if sort_by == "date":
        sort_col = Invoice.date
    elif sort_by == "number":
        sort_col = Invoice.invoice_number
    elif sort_by == "total":
        sort_col = Invoice.total
    elif sort_by == "status":
        sort_col = Invoice.status
    elif sort_by == "client":
        sort_col = Client.name

    if (sort_dir or "").lower() == "asc":
        base = base.order_by(sort_col.asc())
    else:
        base = base.order_by(sort_col.desc())

    # Pagination
    skip = (page - 1) * page_size
    rows = base.offset(skip).limit(page_size).all()

    # Façonner la réponse légère (pas d'items/payments pour la liste,
    # et surtout pas le champ "notes" qui peut contenir des signatures base64 très lourdes)
    result_invoices = []
    for inv, client_name in rows:
        result_invoices.append({
            "invoice_id": inv.invoice_id,
            "invoice_number": inv.invoice_number,
            "invoice_type": inv.invoice_type or "normal",
            "client_id": inv.client_id,
            "client_name": client_name or "",
            "quotation_id": inv.quotation_id,
            "date": inv.date,
            "due_date": inv.due_date,
            "status": inv.status,
            "payment_method": inv.payment_method,
            "subtotal": float(inv.subtotal or 0),
            "tax_rate": float(inv.tax_rate or 0),
            "tax_amount": float(inv.tax_amount or 0),
            "total": float(inv.total or 0),
            "paid_amount": float(inv.paid_amount or 0),
            "remaining_amount": float(inv.remaining_amount or 0),
            "show_tax": bool(inv.show_tax),
            "price_display": inv.price_display or "FCFA",
            "created_at": inv.created_at,
        })

    result = {
        "invoices": result_invoices,
        "total": total,
        "page": page,
        "pages": (total + page_size - 1) // page_size if total > 0 else 1,
    }

    # Store in cache
    try:
        if key:
            import time
            _invoices_cache[key] = { 'ts': time.time(), 'data': result }
    except Exception:
        pass

    return result

@router.get("/{invoice_id}")
async def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir une facture par ID avec items, paiements et nom du client"""
    invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    # Forcer chargement relations
    _ = invoice.items
    _ = invoice.exchange_items if hasattr(invoice, 'exchange_items') else []
    _ = invoice.payments

    client_name = None
    client_phone = None
    if invoice.client_id:
        try:
            client_data = db.query(Client.name, Client.phone).filter(Client.client_id == invoice.client_id).first()
            if client_data:
                client_name = client_data.name
                client_phone = client_data.phone
        except Exception:
            client_name = None
            client_phone = None
    else:
        client_name = "Vente Flash"
        client_phone = None

    return {
        "invoice_id": invoice.invoice_id,
        "invoice_number": invoice.invoice_number,
        "invoice_type": getattr(invoice, 'invoice_type', 'normal'),
        "client_id": invoice.client_id,
        "client_name": client_name,
        "client": {"name": client_name, "phone": client_phone} if client_name else None,
        "date": invoice.date,
        "due_date": invoice.due_date,
        "status": invoice.status,
        "payment_method": invoice.payment_method,
        "subtotal": float(invoice.subtotal or 0),
        "tax_rate": float(invoice.tax_rate or 0),
        "tax_amount": float(invoice.tax_amount or 0),
        "total": float(invoice.total or 0),
        "exchange_discount": float(getattr(invoice, 'exchange_discount', 0) or 0),
        "paid_amount": float(invoice.paid_amount or 0),
        "remaining_amount": float(invoice.remaining_amount or 0),
        "show_tax": bool(invoice.show_tax),
        "show_item_prices": bool(getattr(invoice, 'show_item_prices', True)),
        "show_section_totals": bool(getattr(invoice, 'show_section_totals', True)),
        "notes": invoice.notes,
        "internal_notes": invoice.internal_notes,
        "external_notes": invoice.external_notes,
        # Champs de garantie
        "has_warranty": bool(getattr(invoice, "has_warranty", False)),
        "warranty_duration": getattr(invoice, "warranty_duration", None),
        "warranty_start_date": getattr(invoice, "warranty_start_date", None),
        "warranty_end_date": getattr(invoice, "warranty_end_date", None),
        "items": [
            {
                "item_id": it.item_id,
                "product_id": it.product_id,
                "product_name": it.product_name,
                "quantity": it.quantity,
                "price": float(it.price or 0),
                "total": float(it.total or 0),
                "external_price": float(it.external_price) if it.external_price is not None else None,
                "external_profit": float(it.external_profit) if it.external_profit is not None else None,
                "is_gift": bool(getattr(it, 'is_gift', False)),
                "variant_id": getattr(it, 'variant_id', None),
                "variant_imei": getattr(it, 'variant_imei', None)
            } for it in (invoice.items or [])
        ],
        "exchange_items": [
            {
                "exchange_item_id": ex.exchange_item_id,
                "product_id": ex.product_id,
                "product_name": ex.product_name,
                "quantity": ex.quantity,
                "price": float(ex.price) if ex.price is not None else None,
                "variant_id": ex.variant_id,
                "variant_imei": ex.variant_imei,
                "notes": ex.notes
            } for ex in (getattr(invoice, 'exchange_items', []) or [])
        ],
        "payments": [
            {
                "payment_id": p.payment_id,
                "amount": float(p.amount or 0),
                "payment_date": p.payment_date,
                "payment_method": p.payment_method,
                "reference": p.reference
            } for p in (invoice.payments or [])
        ]
    }

@router.post("/", response_model=InvoiceResponse)
async def create_invoice(
    invoice_data: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Créer une nouvelle facture.
    - Si le numéro est vide ou déjà utilisé, génère automatiquement le prochain numéro disponible (FAC-####).
    """
    try:
        # Vérifier que le client existe (sauf pour les ventes flash)
        client = None
        if invoice_data.client_id:
            client = db.query(Client).filter(Client.client_id == invoice_data.client_id).first()
            if not client:
                raise HTTPException(status_code=404, detail="Client non trouvé")
        elif invoice_data.invoice_type != 'flash_sale':
            raise HTTPException(status_code=400, detail="Client requis pour ce type de facture")
        
        # Déterminer le numéro final (tolère vide/auto/duplicate)
        requested_number = (str(invoice_data.invoice_number or '').strip())
        final_number = None
        if not requested_number or requested_number.upper() in {"AUTO", "AUTOMATIC"}:
            final_number = _next_invoice_number(db)
        else:
            # Si déjà existant, basculer sur le prochain disponible
            exists = db.query(Invoice).filter(Invoice.invoice_number == requested_number).first()
            final_number = requested_number if not exists else _next_invoice_number(db)
        
        # Calculer le montant restant
        remaining_amount = invoice_data.total
        
        # Date d'échéance par défaut = même date que la création
        try:
            final_due_date = invoice_data.due_date or invoice_data.date
        except Exception:
            final_due_date = invoice_data.due_date or datetime.utcnow()

        # Créer la facture
        db_invoice = Invoice(
            invoice_number=final_number,
            invoice_type=getattr(invoice_data, 'invoice_type', 'normal'),
            client_id=invoice_data.client_id,
            quotation_id=invoice_data.quotation_id,
            date=invoice_data.date,
            due_date=final_due_date,
            payment_method=invoice_data.payment_method,
            subtotal=invoice_data.subtotal,
            tax_rate=invoice_data.tax_rate,
            tax_amount=invoice_data.tax_amount,
            total=invoice_data.total,
            paid_amount=0,
            remaining_amount=remaining_amount,
            notes=invoice_data.notes,
            internal_notes=getattr(invoice_data, 'internal_notes', None),
            external_notes=getattr(invoice_data, 'external_notes', None),
            show_tax=invoice_data.show_tax,
            show_item_prices=getattr(invoice_data, 'show_item_prices', True),
            show_section_totals=getattr(invoice_data, 'show_section_totals', True),
            price_display=invoice_data.price_display,
            # Champs de garantie
            has_warranty=bool(getattr(invoice_data, "has_warranty", False)),
            warranty_duration=getattr(invoice_data, "warranty_duration", None),
            warranty_start_date=getattr(invoice_data, "warranty_start_date", None),
            warranty_end_date=getattr(invoice_data, "warranty_end_date", None),
            status="en attente",
            created_by=current_user.user_id
        )
        
        db.add(db_invoice)
        db.flush()  # Pour obtenir l'ID de la facture

        # Si on applique des prix de variantes, on recalcule les totaux facture pour figer les montants réels
        should_recompute_totals = False
        computed_items_subtotal = 0
        
        # Créer les éléments de facture et gérer le stock
        # Log pour déboguer
        for i, item_data in enumerate(invoice_data.items):
            logging.info(f"Item {i}: product_name={item_data.product_name}, external_price={getattr(item_data, 'external_price', 'N/A')}")
        
        for item_data in invoice_data.items:
            resolved_variant = None
            # Lignes personnalisées sans produit: pas d'impact stock
            if not getattr(item_data, 'product_id', None):
                # Ensure custom line name respects DB length
                safe_custom_name = (item_data.product_name or 'Service')[:100]
                # Calculer le bénéfice externe si le prix externe est fourni
                external_price = getattr(item_data, 'external_price', None)
                # Debug: logger le prix externe reçu
                logging.debug(f"Item custom - external_price reçu: {external_price} (type: {type(external_price)})")
                # Convertir en Decimal si présent, sinon None
                from decimal import Decimal
                external_price_decimal = None
                if external_price is not None:
                    try:
                        # Gérer les cas où external_price pourrait être une chaîne vide, 0, ou None
                        if external_price == '' or external_price == 0:
                            external_price_decimal = None
                        else:
                            external_price_decimal = Decimal(str(external_price))
                            if external_price_decimal <= 0:
                                external_price_decimal = None
                    except (ValueError, TypeError) as e:
                        logging.warning(f"Erreur conversion external_price: {e}")
                        external_price_decimal = None
                
                external_profit = None
                if external_price_decimal is not None:
                    external_profit = Decimal(str(item_data.total)) - (external_price_decimal * Decimal(str(item_data.quantity)))
                
                db_item = InvoiceItem(
                    invoice_id=db_invoice.invoice_id,
                    product_id=None,
                    product_name=safe_custom_name,
                    quantity=item_data.quantity,
                    price=item_data.price,
                    total=item_data.total,
                    is_gift=getattr(item_data, 'is_gift', False),
                    external_price=external_price_decimal,
                    external_profit=external_profit
                )
                db.add(db_item)
                try:
                    computed_items_subtotal += float(db_item.total or 0)
                except Exception:
                    pass
                continue

            # Vérifier que le produit existe
            product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Produit {item_data.product_id} non trouvé")
            
            # Déterminer si le produit possède des variantes
            has_variants = db.query(ProductVariant.variant_id).filter(ProductVariant.product_id == product.product_id).first() is not None

            # Remis à None à chaque ligne: sinon la variante de la ligne
            # précédente fuiterait sur un produit sans variantes.
            resolved_variant = None

            if has_variants:
                # Les produits à variantes ne peuvent pas utiliser une quantité agrégée
                # Exiger une variante explicite (ID ou IMEI) et forcer quantity=1 par ligne
                if getattr(item_data, 'variant_id', None):
                    resolved_variant = db.query(ProductVariant).filter(ProductVariant.variant_id == item_data.variant_id).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante {item_data.variant_id} introuvable")
                elif getattr(item_data, 'variant_imei', None):
                    imei_code = str(item_data.variant_imei).strip()
                    resolved_variant = db.query(ProductVariant).filter(
                        ProductVariant.product_id == product.product_id,
                        func.trim(ProductVariant.imei_serial) == imei_code
                    ).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante avec IMEI {imei_code} introuvable")
                else:
                    raise HTTPException(status_code=400, detail="Produit avec variantes: vous devez sélectionner des variantes (IMEI) au lieu de définir une quantité")

                # Valider l'appartenance et la disponibilité de la variante
                if resolved_variant.product_id != product.product_id:
                    raise HTTPException(status_code=400, detail="Variante n'appartient pas au produit")
                
                # Vérifier disponibilité selon le mode de gestion stock
                variant_qty = getattr(resolved_variant, 'quantity', None)
                if variant_qty is not None:
                    # Mode quantité: vérifier stock disponible
                    if variant_qty <= 0:
                        raise HTTPException(status_code=400, detail=f"Stock insuffisant pour la variante {resolved_variant.imei_serial}")
                    # Accepter quantity demandée <= variant.quantity
                    if int(item_data.quantity or 0) > variant_qty:
                        raise HTTPException(status_code=400, detail=f"Quantité demandée ({item_data.quantity}) supérieure au stock disponible ({variant_qty})")
                else:
                    # Mode is_sold (rétrocompat): vérifier si déjà vendue
                    if bool(resolved_variant.is_sold):
                        raise HTTPException(status_code=400, detail=f"La variante {resolved_variant.imei_serial} est déjà vendue")
                    # Forcer quantité = 1 pour une ligne de variante sans quantity
                    if int(item_data.quantity or 0) != 1:
                        raise HTTPException(status_code=400, detail="Pour un produit avec variantes (sans quantité), la quantité doit être 1 par ligne de variante")

                # Décrémenter le stock de la variante
                if variant_qty is not None:
                    resolved_variant.quantity = variant_qty - int(item_data.quantity or 0)
                else:
                    # Mode is_sold (rétrocompat)
                    resolved_variant.is_sold = True
            else:
                # Produits sans variantes: vérifier stock disponible agrégé
                if (product.quantity or 0) < item_data.quantity:
                    raise HTTPException(status_code=400, detail=f"Stock insuffisant pour le produit {product.name}")
            
            # Créer l'élément de facture
            # Ensure product_name respects DB length (String(100))
            safe_name = (item_data.product_name or product.name)[:100]

            # Appliquer le prix de variante si défini (option A: figer dans InvoiceItem)
            from decimal import Decimal
            unit_price_dec = Decimal(str(item_data.price))
            if has_variants and resolved_variant is not None:
                try:
                    v_price = getattr(resolved_variant, 'price', None)
                    if v_price is not None and Decimal(str(v_price)) > Decimal('0'):
                        unit_price_dec = Decimal(str(v_price))
                        should_recompute_totals = True
                except Exception:
                    pass
            qty_dec = Decimal(str(item_data.quantity or 0))
            line_total_dec = unit_price_dec * qty_dec
            # Calculer le bénéfice externe si le prix externe est fourni
            external_price = getattr(item_data, 'external_price', None)
            # Debug: logger le prix externe reçu
            logging.debug(f"Item produit {product.name} - external_price reçu: {external_price} (type: {type(external_price)})")
            # Convertir en Decimal si présent, sinon None
            external_price_decimal = None
            if external_price is not None:
                try:
                    # Gérer les cas où external_price pourrait être une chaîne vide, 0, ou None
                    if external_price == '' or external_price == 0:
                        external_price_decimal = None
                    else:
                        external_price_decimal = Decimal(str(external_price))
                        if external_price_decimal <= 0:
                            external_price_decimal = None
                except (ValueError, TypeError) as e:
                    logging.warning(f"Erreur conversion external_price pour {product.name}: {e}")
                    external_price_decimal = None
            
            external_profit = None
            if external_price_decimal is not None:
                external_profit = line_total_dec - (external_price_decimal * qty_dec)
                logging.debug(f"Bénéfice calculé pour {product.name}: {external_profit}")
            
            db_item = InvoiceItem(
                invoice_id=db_invoice.invoice_id,
                product_id=item_data.product_id,
                product_name=safe_name,
                quantity=item_data.quantity,
                price=unit_price_dec,
                total=line_total_dec,
                is_gift=getattr(item_data, 'is_gift', False),
                # Rattachement à l'exemplaire vendu: permet de remettre le bon
                # IMEI en stock si la ligne est retirée plus tard.
                variant_id=resolved_variant.variant_id if resolved_variant is not None else None,
                variant_imei=resolved_variant.imei_serial if resolved_variant is not None else None,
                external_price=external_price_decimal,
                external_profit=external_profit
            )
            db.add(db_item)

            try:
                computed_items_subtotal += float(db_item.total or 0)
            except Exception:
                pass
            
            # Mettre à jour le stock produit
            # Pour les variantes avec quantity, le stock produit sera recalculé après commit
            # Pour les autres cas, décrémenter directement
            if not has_variants:
                product.quantity = (product.quantity or 0) - item_data.quantity
            try:
                create_stock_movement_entry(
                    db=db,
                    product_id=item_data.product_id,
                    quantity=item_data.quantity,
                    movement_type="OUT",
                    reference_type="INVOICE",
                    reference_id=db_invoice.invoice_id,
                    notes=f"Vente - Facture {final_number}",
                    unit_price=float(unit_price_dec)
                )
            except Exception:
                # Ne pas bloquer la création de facture si l'enregistrement du mouvement échoue
                pass

            # Synchroniser le stock avec Google Sheets (si activé)
            try:
                sync_product_stock_to_sheets(db, item_data.product_id)
            except Exception as e:
                # Ne pas bloquer la création de facture si la sync Google Sheets échoue
                logging.warning(f"Échec de synchronisation Google Sheets pour le produit {item_data.product_id}: {e}")
                pass
        
        # Gérer les factures d'échange
        if getattr(invoice_data, 'invoice_type', 'normal') == 'exchange':
            exchange_items = getattr(invoice_data, 'exchange_items', []) or []
            
            # Debug: log exchange items payload
            logging.info(f"[EXCHANGE DEBUG] Received {len(exchange_items)} exchange items")
            for idx, ex_item in enumerate(exchange_items):
                logging.info(f"[EXCHANGE DEBUG] Item {idx}: product_id={getattr(ex_item, 'product_id', None)}, "
                           f"product_name={getattr(ex_item, 'product_name', None)}, "
                           f"category_id={getattr(ex_item, 'category_id', None)}, "
                           f"variant_imei={getattr(ex_item, 'variant_imei', None)}, "
                           f"barcode={getattr(ex_item, 'barcode', None)}")
            
            # Traiter les produits échangés (sortants - ceux que le client donne)
            for exchange_item in exchange_items:
                exchange_product = None
                actual_product_id = exchange_item.product_id
                is_new_product = False  # Pour éviter le double-incrément du stock

                # Si un product_id est fourni mais ne correspond à aucun produit existant
                # (produit supprimé / référence obsolète envoyée par le frontend), on bascule
                # sur la création d'un nouveau produit de reprise au lieu d'insérer une clé
                # étrangère invalide (qui provoquait une erreur 500 à la sauvegarde).
                if exchange_item.product_id:
                    _existing_product = db.query(Product).filter(Product.product_id == exchange_item.product_id).first()
                    if _existing_product is None:
                        logging.warning(
                            f"[EXCHANGE] product_id={exchange_item.product_id} introuvable "
                            f"(produit supprimé ?) → création d'un nouveau produit de reprise "
                            f"'{exchange_item.product_name}'"
                        )
                        exchange_item.product_id = None
                        actual_product_id = None

                # Si product_name est vide mais variant_imei est fourni, générer un nom
                if not exchange_item.product_id and not (exchange_item.product_name or '').strip() and getattr(exchange_item, 'variant_imei', None):
                    exchange_item.product_name = f"Produit repris ({exchange_item.variant_imei})"

                # Si c'est un article personnalisé (product_id=null), créer un nouveau produit avec source='exchange'
                if not exchange_item.product_id and exchange_item.product_name:
                    from decimal import Decimal
                    
                    # Utiliser le prix fourni ou 0 par défaut
                    exchange_price = getattr(exchange_item, 'price', None)
                    if exchange_price is None:
                        exchange_price = Decimal("0")
                    else:
                        exchange_price = Decimal(str(exchange_price))
                    
                    # Récupérer la catégorie spécifiée ou utiliser 'Divers' par défaut
                    category_id = getattr(exchange_item, 'category_id', None)
                    category_name = 'Divers'
                    requires_variants = False
                    
                    if category_id:
                        category = db.query(Category).filter(Category.category_id == category_id).first()
                        if category:
                            category_name = category.name
                            requires_variants = category.requires_variants

                    # Réutiliser la fiche existante du même modèle plutôt que
                    # d'en créer une nouvelle.
                    #
                    # Sans cela, chaque échange saisi à la main ajoutait une
                    # fiche : le catalogue comptait sept « IPHONE XR », chacune
                    # avec sa part du stock et de l'historique. On cherche donc
                    # une fiche active portant le même nom, casse et espaces
                    # ignorés, et on lui rattache l'appareil repris — c'est
                    # exactement ce que produirait une sélection du produit dans
                    # la liste de recherche.
                    fiche_existante = trouver_fiche_homonyme(db, exchange_item.product_name)
                    if fiche_existante:
                        exchange_product = fiche_existante
                        actual_product_id = fiche_existante.product_id
                        # La catégorie de la fiche retenue fait foi : c'est elle
                        # qui décide si l'appareil se gère par IMEI.
                        category_name = fiche_existante.category or category_name
                        categorie_fiche = db.query(Category).filter(
                            Category.name == fiche_existante.category
                        ).first()
                        requires_variants = bool(
                            categorie_fiche.requires_variants if categorie_fiche
                            else requires_variants
                        )
                        # Le stock est crédité ici, comme le fait la création
                        # plus bas ; le drapeau évite qu'il le soit une
                        # deuxième fois à la fin du traitement.
                        fiche_existante.quantity = (fiche_existante.quantity or 0) + exchange_item.quantity
                        db.flush()
                        is_new_product = True

                    else:
                        # Aucune fiche homonyme : on crée la fiche de reprise.
                        new_exchange_product = Product(
                            name=exchange_item.product_name[:500],
                            description=getattr(exchange_item, 'notes', None),
                            quantity=exchange_item.quantity,
                            price=exchange_price,
                            purchase_price=Decimal("0"),
                            category=category_name,
                            condition='occasion',  # Par défaut occasion pour les échanges
                            source='exchange',  # Marquer comme provenant d'un échange
                            has_unique_serial=False,
                            entry_date=invoice_data.date
                        )
                        db.add(new_exchange_product)
                        db.flush()
                        exchange_product = new_exchange_product
                        actual_product_id = new_exchange_product.product_id
                        is_new_product = True
                    
                    # Si la catégorie nécessite des variantes, créer ou réutiliser une variante
                    if requires_variants and getattr(exchange_item, 'variant_imei', None):
                        variant_imei = str(getattr(exchange_item, 'variant_imei', '')).strip()
                        if variant_imei:
                            existing_variant = db.query(ProductVariant).filter(ProductVariant.imei_serial == variant_imei).first()
                            if existing_variant and existing_variant.is_sold:
                                # Produit revendu par le client via échange : réutiliser la variante
                                existing_variant.is_sold = False
                                existing_variant.quantity = (existing_variant.quantity or 0) + exchange_item.quantity
                                existing_variant.product_id = actual_product_id
                                exchange_item.variant_id = existing_variant.variant_id
                                db.flush()
                            elif existing_variant:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"L'IMEI/Série {variant_imei} existe déjà dans le système et n'est pas marqué comme vendu."
                                )
                            else:
                                new_variant = ProductVariant(
                                    product_id=actual_product_id,
                                    imei_serial=variant_imei,
                                    quantity=exchange_item.quantity,
                                    is_sold=False
                                )
                                db.add(new_variant)
                                db.flush()
                                exchange_item.variant_id = new_variant.variant_id
                    elif not requires_variants:
                        barcode = getattr(exchange_item, 'barcode', None)
                        if barcode:
                            new_exchange_product.barcode = barcode
                elif exchange_item.product_id:
                    exchange_product = db.query(Product).filter(Product.product_id == exchange_item.product_id).first()
                    if exchange_product and not getattr(exchange_product, 'source', None):
                        exchange_product.source = 'exchange'

                    try:
                        category_id = getattr(exchange_item, 'category_id', None)
                        if exchange_product and category_id:
                            category = db.query(Category).filter(Category.category_id == category_id).first()
                            if category:
                                exchange_product.category = category.name
                    except Exception:
                        pass

                    category_id = getattr(exchange_item, 'category_id', None)
                    requires_variants = False
                    if category_id:
                        category = db.query(Category).filter(Category.category_id == category_id).first()
                        if category:
                            requires_variants = bool(category.requires_variants)

                    if exchange_product and requires_variants:
                        variant_imei = getattr(exchange_item, 'variant_imei', None)
                        if variant_imei and not getattr(exchange_item, 'variant_id', None):
                            variant_imei_str = str(variant_imei).strip()
                            existing_variant = db.query(ProductVariant).filter(ProductVariant.imei_serial == variant_imei_str).first()
                            if existing_variant and existing_variant.is_sold:
                                # Produit revendu par le client via échange : réutiliser la variante
                                existing_variant.is_sold = False
                                existing_variant.quantity = (existing_variant.quantity or 0) + exchange_item.quantity
                                existing_variant.product_id = exchange_product.product_id
                                exchange_item.variant_id = existing_variant.variant_id
                                db.flush()
                            elif existing_variant:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"L'IMEI/Série {variant_imei_str} existe déjà dans le système et n'est pas marqué comme vendu."
                                )
                            else:
                                new_variant = ProductVariant(
                                    product_id=exchange_product.product_id,
                                    imei_serial=variant_imei_str,
                                    quantity=exchange_item.quantity,
                                    is_sold=False
                                )
                                db.add(new_variant)
                                db.flush()
                                exchange_item.variant_id = new_variant.variant_id
                    elif exchange_product and not requires_variants:
                        barcode = getattr(exchange_item, 'barcode', None)
                        if barcode:
                            exchange_product.barcode = str(barcode)
                
                db_exchange_item = InvoiceExchangeItem(
                    invoice_id=db_invoice.invoice_id,
                    product_id=actual_product_id,
                    product_name=exchange_item.product_name,
                    quantity=exchange_item.quantity,
                    price=getattr(exchange_item, 'price', None),
                    variant_id=getattr(exchange_item, 'variant_id', None),
                    variant_imei=getattr(exchange_item, 'variant_imei', None),
                    notes=getattr(exchange_item, 'notes', None)
                )
                db.add(db_exchange_item)
                
                # Augmenter le stock du produit échangé (seulement pour les produits EXISTANTS,
                # les nouveaux sont déjà créés avec la bonne quantité)
                if exchange_product and not is_new_product:
                    if exchange_item.variant_id:
                        # Pour les variantes, réactiver ou incrémenter quantity
                        variant = db.query(ProductVariant).filter(ProductVariant.variant_id == exchange_item.variant_id).first()
                        if variant:
                            variant_qty = getattr(variant, 'quantity', None)
                            if variant_qty is not None:
                                # Mode quantité: incrémenter
                                variant.quantity = variant_qty + exchange_item.quantity
                            else:
                                # Mode is_sold: réactiver
                                variant.is_sold = False
                    else:
                        # Produit sans variantes: augmenter la quantité
                        exchange_product.quantity = (exchange_product.quantity or 0) + exchange_item.quantity

                # Créer un mouvement de stock d'entrée
                if exchange_product:
                    try:
                        create_stock_movement_entry(
                            db=db,
                            product_id=actual_product_id,
                            quantity=exchange_item.quantity,
                            movement_type="IN",
                            reference_type="EXCHANGE",
                            reference_id=db_invoice.invoice_id,
                            notes=f"Échange - Produit reçu - Facture {final_number}"
                        )
                    except Exception as e:
                        logging.warning(f"[EXCHANGE] Stock movement creation failed for product {actual_product_id}: {e}")
            
            # Calculer le total de reprise et stocker dans exchange_discount.
            # La session est en autoflush=False: sans ce flush, la requête ne voit
            # aucune des lignes d'échange ajoutées juste au-dessus et la reprise
            # est enregistrée à zéro.
            db.flush()
            exchange_total = 0
            for ex_item in db.query(InvoiceExchangeItem).filter(InvoiceExchangeItem.invoice_id == db_invoice.invoice_id).all():
                if ex_item.price:
                    exchange_total += float(ex_item.price) * ex_item.quantity

            from decimal import Decimal
            if exchange_total > 0:
                exchange_discount = Decimal(str(exchange_total))
                db_invoice.exchange_discount = exchange_discount
            else:
                exchange_discount = Decimal('0')

            # Utiliser le total envoyé par le frontend (l'utilisateur peut le modifier manuellement)
            # Sinon fallback sur le calcul : sous-total + TVA - déduction échange
            if invoice_data.total is not None and float(invoice_data.total) > 0:
                db_invoice.total = Decimal(str(invoice_data.total))
            else:
                gross_total = Decimal(str(db_invoice.subtotal or 0)) + Decimal(str(db_invoice.tax_amount or 0))
                db_invoice.total = max(Decimal('0'), gross_total - exchange_discount)
            db_invoice.remaining_amount = db_invoice.total - Decimal(str(db_invoice.paid_amount or 0))

            # Traiter les produits entrants (ceux qu'on donne au client) - créer nouveaux produits si nécessaire
            for item_data in invoice_data.items:
                if getattr(item_data, 'create_as_new_product', False):
                    # Créer un nouveau produit
                    from decimal import Decimal
                    
                    category_name = getattr(item_data, 'new_product_category', None) or 'Divers'
                    category = db.query(Category).filter(Category.name == category_name).first()
                    requires_variants = category.requires_variants if category else False
                    
                    new_product = Product(
                        name=item_data.product_name[:500],
                        description=None,
                        quantity=1 if requires_variants else item_data.quantity,
                        price=Decimal(str(item_data.price)),
                        purchase_price=Decimal("0"),
                        category=category_name,
                        condition=getattr(item_data, 'new_product_condition', 'neuf') or 'neuf',
                        has_unique_serial=requires_variants,
                        entry_date=invoice_data.date
                    )
                    db.add(new_product)
                    db.flush()
                    
                    # Créer la variante si nécessaire
                    if requires_variants and getattr(item_data, 'new_variant_imei', None):
                        new_imei = str(getattr(item_data, 'new_variant_imei', None)).strip()
                        # Vérifier l'unicité de l'IMEI avant création
                        existing_variant = db.query(ProductVariant).filter(ProductVariant.imei_serial == new_imei).first()
                        if existing_variant:
                            raise HTTPException(
                                status_code=400, 
                                detail=f"L'IMEI/Série {new_imei} existe déjà dans le système."
                            )
                            
                        new_variant = ProductVariant(
                            product_id=new_product.product_id,
                            imei_serial=new_imei,
                            barcode=getattr(item_data, 'new_variant_barcode', None),
                            condition=new_product.condition,
                            is_sold=True  # Marquer comme vendu car dans la facture
                        )
                        db.add(new_variant)
                    
                    # Mettre à jour l'InvoiceItem avec le nouveau product_id
                    db_item = db.query(InvoiceItem).filter(
                        InvoiceItem.invoice_id == db_invoice.invoice_id,
                        InvoiceItem.product_name == item_data.product_name
                    ).order_by(InvoiceItem.item_id.desc()).first()
                    
                    if db_item:
                        db_item.product_id = new_product.product_id
        
        # Recalculer totaux facture si des prix variantes ont été appliqués
        if should_recompute_totals:
            try:
                from decimal import Decimal
                subtotal_dec = Decimal(str(computed_items_subtotal or 0))
                tax_rate_dec = Decimal(str(db_invoice.tax_rate or 0))
                tax_amount_dec = Decimal('0')
                if bool(db_invoice.show_tax):
                    tax_amount_dec = (subtotal_dec * tax_rate_dec) / Decimal('100')
                total_dec = subtotal_dec + tax_amount_dec
                db_invoice.subtotal = subtotal_dec
                db_invoice.tax_amount = tax_amount_dec

                # Pour les factures échange, le total envoyé par le frontend est le
                # montant net à payer (après déduction de la reprise). Ne pas l'écraser.
                if getattr(invoice_data, 'invoice_type', 'normal') == 'exchange':
                    # Garder le total du frontend (montant net à payer)
                    pass
                else:
                    db_invoice.total = total_dec

                # remaining = total (facture fraîche, paid_amount = 0)
                db_invoice.remaining_amount = Decimal(str(db_invoice.total or 0))
            except Exception:
                pass

        db.commit()
        db.refresh(db_invoice)
        
        # Recalculer product.quantity pour les produits avec variantes (mode quantity)
        try:
            affected_product_ids = set()
            for item_data in invoice_data.items:
                if getattr(item_data, 'product_id', None):
                    product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
                    if product:
                        has_variants = db.query(ProductVariant.variant_id).filter(ProductVariant.product_id == product.product_id).first() is not None
                        if has_variants and product.product_id not in affected_product_ids:
                            # Recalculer quantity comme somme des variant.quantity
                            total_qty = 0
                            for db_v in db.query(ProductVariant).filter(ProductVariant.product_id == product.product_id).all():
                                vq = getattr(db_v, 'quantity', None)
                                if vq is not None and vq > 0:
                                    total_qty += vq
                                elif vq is None and not db_v.is_sold:
                                    # Variante sans quantity: compter 1 si non vendue (rétrocompat)
                                    total_qty += 1
                            product.quantity = total_qty
                            affected_product_ids.add(product.product_id)
            db.commit()
        except Exception:
            pass  # Non bloquant
        
        # Invalider le cache du dashboard après création/modification de facture
        try:
            invalidate_dashboard_cache()
        except Exception:
            pass  # Non bloquant
        
        # Créer automatiquement les ventes quotidiennes pour chaque produit de la facture
        try:
            for item_data in invoice_data.items:
                if getattr(item_data, 'product_id', None):  # Seulement pour les produits réels
                    product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
                    if not product:
                        continue

                    # Préparer les infos de variante si applicable
                    variant_id_val = None
                    variant_imei_val = None
                    variant_barcode_val = None
                    variant_condition_val = None
                    try:
                        has_variants = (
                            db.query(ProductVariant.variant_id)
                            .filter(ProductVariant.product_id == product.product_id)
                            .first()
                            is not None
                        )
                        if has_variants:
                            resolved_variant = None
                            if getattr(item_data, 'variant_id', None):
                                resolved_variant = (
                                    db.query(ProductVariant)
                                    .filter(ProductVariant.variant_id == item_data.variant_id)
                                    .first()
                                )
                            elif getattr(item_data, 'variant_imei', None):
                                imei_code = str(item_data.variant_imei).strip()
                                if imei_code:
                                    resolved_variant = (
                                        db.query(ProductVariant)
                                        .filter(
                                            ProductVariant.product_id == product.product_id,
                                            func.trim(ProductVariant.imei_serial) == imei_code,
                                        )
                                        .first()
                                    )
                            if resolved_variant is not None:
                                variant_id_val = resolved_variant.variant_id
                                variant_imei_val = resolved_variant.imei_serial
                                variant_barcode_val = resolved_variant.barcode
                                variant_condition_val = resolved_variant.condition
                    except Exception:
                        pass

                    daily_sale = DailySale(
                        client_id=invoice_data.client_id,
                        client_name=client.name if client else 'Vente Flash',
                        product_id=item_data.product_id,
                        product_name=item_data.product_name or product.name,
                        variant_id=variant_id_val,
                        variant_imei=variant_imei_val,
                        variant_barcode=variant_barcode_val,
                        variant_condition=variant_condition_val,
                        quantity=item_data.quantity,
                        unit_price=item_data.price,
                        total_amount=item_data.total,
                        sale_date=invoice_data.date.date(),
                        payment_method=invoice_data.payment_method or "espece",
                        invoice_id=db_invoice.invoice_id,
                        notes=f"Vente automatique depuis facture {final_number}",
                    )
                    db.add(daily_sale)

            db.commit()
        except Exception as e:
            # Ne pas bloquer la création de facture si l'enregistrement des ventes quotidiennes échoue
            logging.warning(f"Erreur lors de la création des ventes quotidiennes: {e}")
            pass
        
        # Clear invoices cache after creation to ensure fresh data on next load
        _invoices_cache.clear()
        
        try:
            # Mettre à jour les stats persistées
            recompute_invoices_stats(db)
        except Exception:
            pass

        # Façonner et retourner la réponse complète avec client_name
        try:
            if db_invoice.client_id:
                client_name = db.query(Client.name).filter(Client.client_id == db_invoice.client_id).scalar() or ""
            else:
                client_name = "Vente Flash"
        except Exception:
            client_name = "Vente Flash" if db_invoice.invoice_type == 'flash_sale' else ""
        try:
            _ = db_invoice.items
        except Exception:
            pass
        return {
            "invoice_id": db_invoice.invoice_id,
            "invoice_number": db_invoice.invoice_number,
            "client_id": db_invoice.client_id,
            "client_name": client_name,
            "quotation_id": db_invoice.quotation_id,
            "date": db_invoice.date,
            "due_date": db_invoice.due_date,
            "status": db_invoice.status,
            "payment_method": db_invoice.payment_method,
            "subtotal": float(db_invoice.subtotal or 0),
            "tax_rate": float(db_invoice.tax_rate or 0),
            "tax_amount": float(db_invoice.tax_amount or 0),
            "total": float(db_invoice.total or 0),
            "paid_amount": float(db_invoice.paid_amount or 0),
            "remaining_amount": float(db_invoice.remaining_amount or 0),
            "notes": db_invoice.notes,
            "show_tax": bool(db_invoice.show_tax),
            "price_display": db_invoice.price_display or "FCFA",
            # Champs de garantie
            "has_warranty": bool(getattr(db_invoice, "has_warranty", False)),
            "warranty_duration": getattr(db_invoice, "warranty_duration", None),
            "warranty_start_date": getattr(db_invoice, "warranty_start_date", None),
            "warranty_end_date": getattr(db_invoice, "warranty_end_date", None),
            "created_at": db_invoice.created_at,
            "items": [
                {
                    "item_id": it.item_id,
                    "product_id": it.product_id,
                    "product_name": it.product_name,
                    "quantity": it.quantity,
                    "price": float(it.price or 0),
                    "total": float(it.total or 0),
                    "is_gift": bool(getattr(it, 'is_gift', False)),
                    "variant_id": getattr(it, 'variant_id', None),
                    "variant_imei": getattr(it, 'variant_imei', None),
                }
                for it in (db_invoice.items or [])
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception(f"Erreur lors de la création de la facture")
        if str(os.getenv("DEBUG_ERRORS", "")).lower() == "true":
            raise HTTPException(status_code=500, detail=f"Erreur serveur: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: int,
    invoice_data: InvoiceCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour une facture existante avec réconciliation du stock et des variantes.

    Stratégie:
    - Restaurer le stock des anciens items (IN) et tenter de réactiver les variantes vendues
      en se basant sur les métadonnées de notes (__SERIALS__) ou, à défaut, sur le libellé (IMEI: ...).
      En dernier recours, désactiver l'état vendu de n variantes correspondant à la quantité.
    - Remplacer les items par ceux du payload et appliquer le nouveau stock (OUT) + variantes vendues.
    - Mettre à jour les montants et le statut en cohérence avec le montant payé actuel.
    """
    try:
        # Charger la facture existante
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")


        # Vérifier que le client existe (sauf pour les ventes flash)
        client = None
        if invoice_data.client_id:
            client = db.query(Client).filter(Client.client_id == invoice_data.client_id).first()
            if not client:
                raise HTTPException(status_code=404, detail="Client non trouvé")
        elif invoice_data.invoice_type != 'flash_sale':
            raise HTTPException(status_code=400, detail="Client requis pour ce type de facture")

        # 1) REVERT: restaurer le stock des anciens items et réactiver variantes
        #   a) Restaurer le stock pour chaque item produit
        old_items = list(invoice.items or [])
        for it in old_items:
            if it.product_id is None:
                continue
            product = db.query(Product).filter(Product.product_id == it.product_id).first()
            if product:
                try:
                    product.quantity = (product.quantity or 0) + int(it.quantity or 0)
                except Exception:
                    product.quantity = (product.quantity or 0)
                # Mouvement IN pour revert
                try:
                    create_stock_movement_entry(
                        db=db,
                        product_id=it.product_id,
                        quantity=int(it.quantity or 0),
                        movement_type="IN",
                        reference_type="INV_UPDATE_REVERT",  # Shortened to fit VARCHAR(20)
                        reference_id=invoice_id,
                        notes=f"Revert mise à jour facture {invoice.invoice_number}",
                        unit_price=float(it.price or 0),
                    )
                except Exception:
                    pass

        #   b) Remettre en stock l'exemplaire précis vendu par chaque ancienne ligne.
        #      Les unités revendues depuis ne sont pas libérées: si la ligne est
        #      réappliquée plus bas, il ne faut pas non plus les redécompter.
        skipped_variants = _restore_sold_units(db, invoice, old_items)

        # Supprimer les anciens items
        for it in old_items:
            try:
                db.delete(it)
            except Exception:
                pass
        db.flush()

        # 2) APPLY: mettre à jour la facture et recréer les items avec nouveaux impacts stock/variants
        invoice.invoice_number = invoice.invoice_number
        # Le type est modifiable: passer d'un échange à une facture normale (ou
        # l'inverse) doit être persisté, sinon la facture garde son ancien type.
        invoice.invoice_type = getattr(invoice_data, 'invoice_type', None) or 'normal'
        invoice.client_id = invoice_data.client_id
        invoice.quotation_id = invoice_data.quotation_id
        invoice.date = invoice_data.date
        invoice.due_date = invoice_data.due_date
        invoice.payment_method = invoice_data.payment_method
        invoice.subtotal = invoice_data.subtotal
        invoice.tax_rate = invoice_data.tax_rate
        invoice.tax_amount = invoice_data.tax_amount
        invoice.total = invoice_data.total
        invoice.notes = invoice_data.notes
        invoice.internal_notes = getattr(invoice_data, 'internal_notes', None)
        invoice.external_notes = getattr(invoice_data, 'external_notes', None)
        invoice.show_tax = bool(invoice_data.show_tax)
        invoice.show_item_prices = bool(getattr(invoice_data, 'show_item_prices', True))
        invoice.show_section_totals = bool(getattr(invoice_data, 'show_section_totals', True))
        invoice.price_display = invoice_data.price_display
        # Champs de garantie
        invoice.has_warranty = bool(getattr(invoice_data, "has_warranty", False))
        invoice.warranty_duration = getattr(invoice_data, "warranty_duration", None)
        invoice.warranty_start_date = getattr(invoice_data, "warranty_start_date", None)
        invoice.warranty_end_date = getattr(invoice_data, "warranty_end_date", None)

        # Recalculer remaining_amount en fonction du payé existant
        try:
            paid = float(invoice.paid_amount or 0)
            total_val = float(invoice.total or 0)
            # Retour produit : si des articles ont été retirés d'une facture déjà
            # (partiellement) payée, le nouveau total peut passer sous le montant déjà
            # encaissé. On enregistre alors automatiquement un remboursement (paiement
            # négatif) de la différence, ramène le "payé" au niveau du total, et laisse
            # une trace dans l'historique de paiement — les comptes restent justes.
            if paid > total_val:
                from decimal import Decimal
                refund_amount = round(paid - total_val)
                if refund_amount > 0:
                    refund_method = (getattr(invoice_data, 'payment_method', None)
                                     or invoice.payment_method or "espèces")
                    db.add(InvoicePayment(
                        invoice_id=invoice.invoice_id,
                        amount=Decimal(str(-refund_amount)),
                        payment_method=refund_method,
                        payment_date=datetime.now(),
                        reference=f"RETOUR-{invoice.invoice_number}",
                        notes="Remboursement automatique suite au retour d'un produit",
                    ))
                    invoice.paid_amount = Decimal(str(round(total_val)))
                    paid = float(invoice.paid_amount or 0)
            invoice.remaining_amount = max(0, total_val - paid)
            # Ajuster le statut si nécessaire
            if invoice.remaining_amount == 0:
                invoice.status = "payée"
            elif paid > 0:
                invoice.status = "partiellement payée"
            else:
                invoice.status = "en attente"
        except Exception:
            pass

        # Créer les nouveaux items et appliquer le stock
        for item_data in (invoice_data.items or []):
            # Lignes personnalisées sans produit: pas d'impact stock
            if not getattr(item_data, 'product_id', None):
                # Ensure custom line name respects DB length
                safe_custom_name = (item_data.product_name or 'Service')[:100]
                # Calculer le bénéfice externe si le prix externe est fourni
                external_price = getattr(item_data, 'external_price', None)
                # Convertir en Decimal si présent, sinon None
                from decimal import Decimal
                external_price_decimal = None
                if external_price is not None:
                    try:
                        external_price_decimal = Decimal(str(external_price))
                        if external_price_decimal <= 0:
                            external_price_decimal = None
                    except (ValueError, TypeError):
                        external_price_decimal = None
                
                external_profit = None
                if external_price_decimal is not None:
                    external_profit = Decimal(str(item_data.total)) - (external_price_decimal * Decimal(str(item_data.quantity)))
                
                db_item = InvoiceItem(
                    invoice_id=invoice.invoice_id,
                    product_id=None,
                    product_name=safe_custom_name,
                    quantity=item_data.quantity,
                    price=item_data.price,
                    total=item_data.total,
                    external_price=external_price_decimal,
                    external_profit=external_profit
                )
                db.add(db_item)
                continue

            # Vérifier produit
            product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
            if not product:
                raise HTTPException(status_code=404, detail=f"Produit {item_data.product_id} non trouvé")

            # Déterminer si le produit possède des variantes
            has_variants = db.query(ProductVariant.variant_id).filter(ProductVariant.product_id == product.product_id).first() is not None

            # Remis à None à chaque ligne: sinon la variante de la ligne
            # précédente fuiterait sur un produit sans variantes.
            resolved_variant = None

            if has_variants:
                # Pour la mise à jour, on est plus permissif: si aucune variante n'est spécifiée,
                # on permet quand même la mise à jour (les variantes ont été restaurées dans REVERT)
                if getattr(item_data, 'variant_id', None):
                    resolved_variant = db.query(ProductVariant).filter(ProductVariant.variant_id == item_data.variant_id).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante {item_data.variant_id} introuvable")
                elif getattr(item_data, 'variant_imei', None):
                    imei_code = str(item_data.variant_imei).strip()
                    resolved_variant = db.query(ProductVariant).filter(
                        ProductVariant.product_id == product.product_id,
                        func.trim(ProductVariant.imei_serial) == imei_code
                    ).first()
                    if not resolved_variant:
                        raise HTTPException(status_code=404, detail=f"Variante avec IMEI {imei_code} introuvable")
                
                # Si une variante est spécifiée, valider et marquer comme vendue
                if resolved_variant:
                    if resolved_variant.product_id != product.product_id:
                        raise HTTPException(status_code=400, detail="Variante n'appartient pas au produit")
                    # Unité conservée sur la facture mais revendue depuis par une
                    # facture postérieure: elle n'a pas été libérée au revert, on
                    # la laisse telle quelle plutôt que de la décompter deux fois.
                    already_counted = resolved_variant.variant_id in skipped_variants
                    if not already_counted and bool(resolved_variant.is_sold):
                        raise HTTPException(status_code=400, detail=f"La variante {resolved_variant.imei_serial} est déjà vendue")
                    # Forcer quantité = 1 par ligne de variante
                    if int(item_data.quantity or 0) != 1:
                        raise HTTPException(status_code=400, detail="Pour un produit avec variantes, la quantité doit être 1 par ligne de variante")
                    if not already_counted:
                        _reserve_variant(resolved_variant, item_data.quantity)
                # Si aucune variante n'est spécifiée lors d'une mise à jour, on permet quand même
                # (c'est une modification de facture existante, les variantes ont été restaurées)
            else:
                # Produits sans variantes: vérifier stock disponible agrégé
                if (product.quantity or 0) < int(item_data.quantity or 0):
                    raise HTTPException(status_code=400, detail=f"Stock insuffisant pour le produit {product.name}")

            # Créer l'item
            # Ensure product_name respects DB length (String(100))
            safe_name = (item_data.product_name or product.name)[:100]
            # Calculer le bénéfice externe si le prix externe est fourni
            external_price = getattr(item_data, 'external_price', None)
            # Convertir en Decimal si présent, sinon None
            from decimal import Decimal
            external_price_decimal = None
            if external_price is not None:
                try:
                    external_price_decimal = Decimal(str(external_price))
                    if external_price_decimal <= 0:
                        external_price_decimal = None
                except (ValueError, TypeError):
                    external_price_decimal = None
            
            # Calculer le bénéfice externe
            external_profit = None
            if external_price_decimal is not None:
                external_profit = Decimal(str(item_data.total)) - (external_price_decimal * Decimal(str(item_data.quantity)))
            
            db_item = InvoiceItem(
                invoice_id=invoice.invoice_id,
                product_id=item_data.product_id,
                product_name=safe_name,
                quantity=item_data.quantity,
                price=item_data.price,
                total=item_data.total,
                is_gift=getattr(item_data, 'is_gift', False),
                # Rattachement à l'exemplaire vendu (cf. create_invoice)
                variant_id=resolved_variant.variant_id if resolved_variant is not None else None,
                variant_imei=resolved_variant.imei_serial if resolved_variant is not None else None,
                external_price=external_price_decimal,
                external_profit=external_profit
            )
            db.add(db_item)
            
            # Appliquer le stock et enregistrer le mouvement OUT
            product.quantity = (product.quantity or 0) - int(item_data.quantity or 0)
            try:
                create_stock_movement_entry(
                    db=db,
                    product_id=item_data.product_id,
                    quantity=int(item_data.quantity or 0),
                    movement_type="OUT",
                    reference_type="INVOICE_UPDATE",
                    reference_id=invoice.invoice_id,
                    notes=f"Mise à jour - Facture {invoice.invoice_number}",
                    unit_price=float(item_data.price or 0),
                )
            except Exception:
                pass

            # Synchroniser le stock avec Google Sheets (si activé)
            try:
                sync_product_stock_to_sheets(db, item_data.product_id)
            except Exception as e:
                logging.warning(f"Échec de synchronisation Google Sheets pour le produit {item_data.product_id}: {e}")
                pass
        # Gérer les produits échangés (UPDATE) - Ajouté pour fixer la persistence
        # 1. Supprimer les anciens items d'échange
        try:
            db.query(InvoiceExchangeItem).filter(InvoiceExchangeItem.invoice_id == invoice.invoice_id).delete()
            db.flush()
        except Exception as e:
            logging.warning(f"Erreur lors de la suppression des anciens items d'échange: {e}")

        # La facture n'est plus un échange: la reprise ne doit plus être déduite,
        # sinon un ancien montant resterait accroché à une facture normale.
        if getattr(invoice_data, 'invoice_type', 'normal') != 'exchange':
            from decimal import Decimal
            invoice.exchange_discount = Decimal('0')

        # 2. Créer les nouveaux items d'échange et mettre à jour les totaux
        if getattr(invoice_data, 'invoice_type', 'normal') == 'exchange':
            exchange_items = getattr(invoice_data, 'exchange_items', []) or []
            
            for exchange_item in exchange_items:
                actual_product_id = exchange_item.product_id
                
                # Si c'est un article personnalisé (product_id=null), créer un nouveau produit
                if not exchange_item.product_id and exchange_item.product_name:
                    try:
                        from decimal import Decimal
                        
                        exchange_price = getattr(exchange_item, 'price', None)
                        if exchange_price is None:
                            exchange_price = Decimal("0")
                        else:
                            exchange_price = Decimal(str(exchange_price))
                        
                        # Récupérer la catégorie spécifiée ou utiliser 'Divers' par défaut
                        category_id = getattr(exchange_item, 'category_id', None)
                        category_name = 'Divers'
                        requires_variants = False
                        
                        if category_id:
                            category = db.query(Category).filter(Category.category_id == category_id).first()
                            if category:
                                category_name = category.name
                                requires_variants = category.requires_variants
                        
                        # Créer le produit de base
                        new_exchange_product = Product(
                            name=exchange_item.product_name[:500],
                            description=getattr(exchange_item, 'notes', None),
                            quantity=exchange_item.quantity,
                            price=exchange_price,
                            purchase_price=Decimal("0"),
                            category=category_name,
                            condition='occasion',
                            source='exchange',
                            has_unique_serial=False,
                            entry_date=invoice_data.date
                        )
                        db.add(new_exchange_product)
                        db.flush()
                        actual_product_id = new_exchange_product.product_id
                        
                        # Si la catégorie nécessite des variantes, créer ou réutiliser une variante
                        if requires_variants and getattr(exchange_item, 'variant_imei', None):
                            variant_imei = str(getattr(exchange_item, 'variant_imei', '')).strip()
                            if variant_imei:
                                existing_variant = db.query(ProductVariant).filter(ProductVariant.imei_serial == variant_imei).first()
                                if existing_variant and existing_variant.is_sold:
                                    existing_variant.is_sold = False
                                    existing_variant.quantity = (existing_variant.quantity or 0) + exchange_item.quantity
                                    existing_variant.product_id = actual_product_id
                                    exchange_item.variant_id = existing_variant.variant_id
                                    db.flush()
                                elif existing_variant:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f"L'IMEI/Série {variant_imei} existe déjà dans le système et n'est pas marqué comme vendu."
                                    )
                                else:
                                    new_variant = ProductVariant(
                                        product_id=actual_product_id,
                                        imei_serial=variant_imei,
                                        quantity=exchange_item.quantity,
                                        is_sold=False
                                    )
                                    db.add(new_variant)
                                    db.flush()
                                    exchange_item.variant_id = new_variant.variant_id
                        elif not requires_variants:
                            barcode = getattr(exchange_item, 'barcode', None)
                            if barcode:
                                new_exchange_product.barcode = barcode
                    except Exception as e:
                        logging.error(f"Erreur création produit échange: {e}")
                        continue

                elif exchange_item.product_id:
                    # Produit existant: appliquer catégorie / barcode / variante IMEI
                    try:
                        exchange_product = db.query(Product).filter(Product.product_id == exchange_item.product_id).first()
                        if exchange_product and not getattr(exchange_product, 'source', None):
                            exchange_product.source = 'exchange'

                        category_id = getattr(exchange_item, 'category_id', None)
                        requires_variants = False
                        if category_id:
                            category = db.query(Category).filter(Category.category_id == category_id).first()
                            if category:
                                exchange_product.category = category.name
                                requires_variants = bool(category.requires_variants)

                        if exchange_product and requires_variants:
                            variant_imei = getattr(exchange_item, 'variant_imei', None)
                            if variant_imei and not getattr(exchange_item, 'variant_id', None):
                                variant_imei_str = str(variant_imei).strip()
                                existing_variant = db.query(ProductVariant).filter(ProductVariant.imei_serial == variant_imei_str).first()
                                if existing_variant and existing_variant.is_sold:
                                    existing_variant.is_sold = False
                                    existing_variant.quantity = (existing_variant.quantity or 0) + exchange_item.quantity
                                    existing_variant.product_id = exchange_product.product_id
                                    exchange_item.variant_id = existing_variant.variant_id
                                    db.flush()
                                elif existing_variant:
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f"L'IMEI/Série {variant_imei_str} existe déjà dans le système et n'est pas marqué comme vendu."
                                    )
                                else:
                                    new_variant = ProductVariant(
                                        product_id=exchange_product.product_id,
                                        imei_serial=variant_imei_str,
                                        quantity=exchange_item.quantity,
                                        is_sold=False
                                    )
                                    db.add(new_variant)
                                    db.flush()
                                    exchange_item.variant_id = new_variant.variant_id
                        elif exchange_product and not requires_variants:
                            barcode = getattr(exchange_item, 'barcode', None)
                            if barcode:
                                exchange_product.barcode = str(barcode)
                    except Exception as e:
                        logging.error(f"Erreur mise à jour produit existant échange: {e}")
                        # continuer: on enregistre quand même la ligne d'échange
                        pass

                try:
                    db_exchange_item = InvoiceExchangeItem(
                        invoice_id=invoice.invoice_id,
                        product_id=actual_product_id,
                        product_name=exchange_item.product_name,
                        quantity=exchange_item.quantity,
                        price=getattr(exchange_item, 'price', None),
                        variant_id=getattr(exchange_item, 'variant_id', None),
                        variant_imei=getattr(exchange_item, 'variant_imei', None),
                        notes=getattr(exchange_item, 'notes', None)
                    )
                    db.add(db_exchange_item)
                except Exception as e:
                    logging.error(f"Erreur ajout item échange: {e}")

            # Calculer le total de reprise et stocker dans exchange_discount
            exchange_total = 0
            for ex in exchange_items:
                 if getattr(ex, 'price', None):
                      try:
                          exchange_total += float(ex.price) * (ex.quantity or 1)
                      except: pass

            from decimal import Decimal
            if exchange_total > 0:
                exchange_discount = Decimal(str(exchange_total))
                invoice.exchange_discount = exchange_discount
            else:
                exchange_discount = Decimal('0')

            # Utiliser le total envoyé par le frontend (l'utilisateur peut le modifier manuellement)
            if invoice_data.total is not None and float(invoice_data.total) > 0:
                invoice.total = Decimal(str(invoice_data.total))
            else:
                gross_total = Decimal(str(invoice.subtotal or 0)) + Decimal(str(invoice.tax_amount or 0))
                invoice.total = max(Decimal('0'), gross_total - exchange_discount)
            invoice.remaining_amount = max(Decimal('0'), invoice.total - Decimal(str(invoice.paid_amount or 0)))

        # Mettre à jour les ventes quotidiennes associées à cette facture
        try:
            # Supprimer les ventes quotidiennes existantes pour cette facture
            existing_sales = db.query(DailySale).filter(DailySale.invoice_id == invoice.invoice_id).all()
            for s in existing_sales:
                db.delete(s)
            db.flush()

            # Recréer les ventes quotidiennes à partir des nouveaux items produits
            for item_data in (invoice_data.items or []):
                if not getattr(item_data, "product_id", None):
                    continue

                product = db.query(Product).filter(Product.product_id == item_data.product_id).first()
                if not product:
                    continue

                # Préparer les infos de variante si applicable
                variant_id_val = None
                variant_imei_val = None
                variant_barcode_val = None
                variant_condition_val = None
                try:
                    has_variants = (
                        db.query(ProductVariant.variant_id)
                        .filter(ProductVariant.product_id == product.product_id)
                        .first()
                        is not None
                    )
                    if has_variants:
                        resolved_variant = None
                        if getattr(item_data, "variant_id", None):
                            resolved_variant = (
                                db.query(ProductVariant)
                                .filter(ProductVariant.variant_id == item_data.variant_id)
                                .first()
                            )
                        elif getattr(item_data, "variant_imei", None):
                            imei_code = str(item_data.variant_imei).strip()
                            if imei_code:
                                resolved_variant = (
                                    db.query(ProductVariant)
                                    .filter(
                                        ProductVariant.product_id == product.product_id,
                                        func.trim(ProductVariant.imei_serial) == imei_code,
                                    )
                                    .first()
                                )
                        if resolved_variant is not None:
                            variant_id_val = resolved_variant.variant_id
                            variant_imei_val = resolved_variant.imei_serial
                            variant_barcode_val = resolved_variant.barcode
                            variant_condition_val = resolved_variant.condition
                except Exception:
                    pass

                daily_sale = DailySale(
                    client_id=invoice.client_id,
                    client_name=client.name if client else 'Vente Flash',
                    product_id=item_data.product_id,
                    product_name=item_data.product_name or product.name,
                    variant_id=variant_id_val,
                    variant_imei=variant_imei_val,
                    variant_barcode=variant_barcode_val,
                    variant_condition=variant_condition_val,
                    quantity=item_data.quantity,
                    unit_price=item_data.price,
                    total_amount=item_data.total,
                    sale_date=invoice.date.date(),
                    payment_method=invoice.payment_method or "espece",
                    invoice_id=invoice.invoice_id,
                    notes=f"Mise à jour automatique depuis facture {invoice.invoice_number}",
                )
                db.add(daily_sale)
        except Exception as e:
            # Ne pas bloquer la mise à jour de facture si la mise à jour des ventes quotidiennes échoue
            logging.warning(f"Erreur lors de la mise à jour des ventes quotidiennes pour la facture {invoice.invoice_id}: {e}")

        # Facture vidée de toutes ses lignes: elle est annulée, pas "payée".
        # Le stock a été restitué au revert et le trop-perçu remboursé plus haut.
        # Même issue que le retour intégral via /return-items, pour que les deux
        # chemins laissent la facture dans le même état.
        if not (invoice_data.items or []):
            from decimal import Decimal
            invoice.subtotal = Decimal("0")
            invoice.tax_amount = Decimal("0")
            invoice.total = Decimal("0")
            invoice.remaining_amount = Decimal("0")
            invoice.status = "annulée"

        db.commit()
        db.refresh(invoice)

        # Invalider le cache du dashboard après mise à jour de facture
        try:
            invalidate_dashboard_cache()
        except Exception:
            pass  # Non bloquant

        # Clear invoices cache after update to ensure fresh data on next load
        _invoices_cache.clear()

        try:
            recompute_invoices_stats(db)
        except Exception:
            pass

        # Façonner la réponse complète avec client_name pour respecter InvoiceResponse
        try:
            if invoice.client_id:
                client_name = db.query(Client.name).filter(Client.client_id == invoice.client_id).scalar() or ""
            else:
                client_name = "Vente Flash"
        except Exception:
            client_name = "Vente Flash" if invoice.invoice_type == 'flash_sale' else ""
        try:
            _ = invoice.items
        except Exception:
            pass
        return {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "client_id": invoice.client_id,
            "client_name": client_name,
            "quotation_id": invoice.quotation_id,
            "date": invoice.date,
            "due_date": invoice.due_date,
            "status": invoice.status,
            "payment_method": invoice.payment_method,
            "subtotal": float(invoice.subtotal or 0),
            "tax_rate": float(invoice.tax_rate or 0),
            "tax_amount": float(invoice.tax_amount or 0),
            "total": float(invoice.total or 0),
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
            "notes": invoice.notes,
            "show_tax": bool(invoice.show_tax),
            "show_item_prices": bool(getattr(invoice, 'show_item_prices', True)),
            "show_section_totals": bool(getattr(invoice, 'show_section_totals', True)),
            "price_display": invoice.price_display or "FCFA",
            "created_at": getattr(invoice, "created_at", None),
            "items": [
                {
                    "item_id": it.item_id,
                    "product_id": it.product_id,
                    "product_name": it.product_name,
                    "quantity": it.quantity,
                    "price": float(it.price or 0),
                    "total": float(it.total or 0),
                    "is_gift": bool(getattr(it, 'is_gift', False)),
                    "variant_id": getattr(it, 'variant_id', None),
                    "variant_imei": getattr(it, 'variant_imei', None),
                }
                for it in (invoice.items or [])
            ],
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.exception(f"Erreur lors de la mise à jour de la facture")
        if str(os.getenv("DEBUG_ERRORS", "")).lower() == "true":
            raise HTTPException(status_code=500, detail=f"Erreur serveur: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.put("/{invoice_id}/status")
async def update_invoice_status(
    invoice_id: int,
    status: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Mettre à jour le statut d'une facture"""
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        if getattr(current_user, "role", "user") != "admin":
            raise HTTPException(status_code=403, detail="Permissions insuffisantes")
        
        valid_statuses = ["en attente", "payée", "partiellement payée", "en retard", "annulée"]
        if status not in valid_statuses:
            raise HTTPException(status_code=400, detail="Statut invalide")
        
        invoice.status = status
        db.commit()
        
        # Invalider le cache du dashboard après changement de statut
        try:
            invalidate_dashboard_cache()
        except Exception:
            pass  # Non bloquant
        
        # Clear invoices cache after status update to ensure fresh data on next load
        _invoices_cache.clear()
        
        return {"message": "Statut mis à jour avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la mise à jour du statut: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime

class PaymentCreate(BaseModel):
    amount: float
    payment_method: str
    payment_date: Optional[datetime] = None
    reference: Optional[str] = None
    notes: Optional[str] = None

def _recompute_invoice_payment_status(invoice: Invoice, db: Session) -> None:
    try:
        total_dec = Decimal(str(invoice.total or 0)).quantize(Decimal('1'))
    except Exception:
        total_dec = Decimal('0')

    try:
        payments = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == invoice.invoice_id).all()
    except Exception:
        payments = []

    paid_dec = Decimal('0')
    for p in (payments or []):
        try:
            paid_dec += Decimal(str(p.amount or 0)).quantize(Decimal('1'))
        except Exception:
            continue

    remaining_dec = total_dec - paid_dec
    if remaining_dec < Decimal('0'):
        remaining_dec = Decimal('0')

    invoice.paid_amount = paid_dec
    invoice.remaining_amount = remaining_dec

    if total_dec > Decimal('0') and remaining_dec == Decimal('0'):
        invoice.status = "payée"
    elif paid_dec > Decimal('0'):
        invoice.status = "partiellement payée"
    else:
        invoice.status = "en attente"

# REMOVED duplicate get_next_invoice_number defined earlier to prevent conflicts

@router.post("/{invoice_id}/payments")
async def add_payment(
    invoice_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Ajouter un paiement à une facture (JSON body)"""
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        if payload.amount <= 0:
            raise HTTPException(status_code=400, detail="Le montant doit être positif")
        
        # Convertir en Decimal et forcer un montant entier
        amount_dec = Decimal(str(payload.amount)).quantize(Decimal('1'))
        remaining = Decimal(str(invoice.remaining_amount or 0)).quantize(Decimal('1'))
        if amount_dec > remaining:
            raise HTTPException(status_code=400, detail="Le montant dépasse le solde restant")
        
        # Créer le paiement
        payment = InvoicePayment(
            invoice_id=invoice_id,
            amount=amount_dec,
            payment_method=payload.payment_method,
            payment_date=(payload.payment_date or datetime.now()),
            reference=payload.reference,
            notes=payload.notes
        )
        db.add(payment)
        
        # Mettre à jour les montants de la facture
        invoice.paid_amount = Decimal(str(invoice.paid_amount or 0)) + amount_dec
        invoice.remaining_amount = remaining - amount_dec

        # Mettre à jour le statut de façon cohérente avec tous les paiements
        # IMPORTANT: flush avant le recalcul pour que la requête voie le nouveau paiement
        db.flush()
        _recompute_invoice_payment_status(invoice, db)
        
        db.commit()
        db.refresh(payment)
        
        # Invalider le cache du dashboard après paiement
        try:
            invalidate_dashboard_cache()
        except Exception:
            pass  # Non bloquant
        
        # Clear invoices cache after payment to ensure fresh data on next load
        _invoices_cache.clear()
        
        return {"message": "Paiement ajouté avec succès", "payment_id": payment.payment_id}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de l'ajout du paiement: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.delete("/{invoice_id}/payments/{payment_id}")
async def delete_payment(
    invoice_id: int,
    payment_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer un paiement d'une facture et recalculer le statut/montants.

    Permet de corriger une facture marquée "payée" par erreur en retirant
    un ou plusieurs paiements, puis en remettant la facture au bon état
    (en attente / partiellement payée) selon les paiements restants.
    """
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        payment = (
            db.query(InvoicePayment)
            .filter(InvoicePayment.payment_id == payment_id, InvoicePayment.invoice_id == invoice_id)
            .first()
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Paiement non trouvé")

        db.delete(payment)
        db.flush()

        _recompute_invoice_payment_status(invoice, db)

        db.commit()
        db.refresh(invoice)

        _invoices_cache.clear()

        return {
            "message": "Paiement supprimé avec succès",
            "invoice_id": invoice.invoice_id,
            "status": invoice.status,
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression du paiement: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.post("/{invoice_id}/payments/reset")
async def reset_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer tous les paiements d'une facture et remettre le statut/montants à zéro.

    Utile lorsqu'une facture a été marquée payée par erreur :
    on vide les paiements puis on repasse automatiquement la facture à
    "en attente" (ou partiellement payée si d'autres paiements sont recréés ensuite).
    """
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        payments = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == invoice_id).all()
        for p in (payments or []):
            db.delete(p)

        db.flush()

        _recompute_invoice_payment_status(invoice, db)

        db.commit()
        db.refresh(invoice)

        _invoices_cache.clear()

        return {
            "message": "Paiements réinitialisés avec succès",
            "invoice_id": invoice.invoice_id,
            "status": invoice.status,
            "paid_amount": float(invoice.paid_amount or 0),
            "remaining_amount": float(invoice.remaining_amount or 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la réinitialisation des paiements: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


# ---------------------------------------------------------------------------
# Retour produit : retirer une ou plusieurs unités vendues d'une facture, les
# remettre en stock et (si la facture est payée) enregistrer le remboursement.
# Source de vérité des unités vendues = daily_sales (porte variant_id / IMEI /
# montant exact), ce qui permet un retour précis à l'unité, y compris par IMEI.
# ---------------------------------------------------------------------------
from pydantic import BaseModel as _BaseModel

class ReturnUnitIn(_BaseModel):
    sale_id: int
    quantity: Optional[int] = None  # None => toute la ligne de vente

class ReturnItemsIn(_BaseModel):
    units: List[ReturnUnitIn]
    refund_method: Optional[str] = "espèces"


@router.get("/{invoice_id}/returnable-units")
async def get_returnable_units(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Unités vendues encore rattachées à la facture, éligibles à un retour."""
    invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvée")
    sales = db.query(DailySale).filter(DailySale.invoice_id == invoice_id).all()
    units = []
    for s in sales:
        units.append({
            "sale_id": s.sale_id,
            "product_id": s.product_id,
            "product_name": s.product_name,
            "variant_imei": s.variant_imei,
            "variant_condition": s.variant_condition,
            "is_variant": s.variant_id is not None,
            "quantity": int(s.quantity or 1),
            "unit_price": float(s.unit_price or 0),
            "total_amount": float(s.total_amount or 0),
        })
    return {
        "invoice_id": invoice_id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "total": float(invoice.total or 0),
        "paid_amount": float(invoice.paid_amount or 0),
        "tax_rate": float(invoice.tax_rate or 0),
        "show_tax": bool(invoice.show_tax),
        "units": units,
    }


@router.post("/{invoice_id}/return-items")
async def return_invoice_items(
    invoice_id: int,
    payload: ReturnItemsIn,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """Retourne des unités vendues : remise en stock, annulation du CA, et
    remboursement automatique (paiement négatif) si la facture était payée."""
    from decimal import Decimal
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        if not payload.units:
            raise HTTPException(status_code=400, detail="Aucune unité à retourner")

        returned_subtotal = Decimal("0")
        details = []

        for u in payload.units:
            sale = db.query(DailySale).filter(
                DailySale.sale_id == u.sale_id,
                DailySale.invoice_id == invoice_id,
            ).first()
            if not sale:
                continue
            avail = int(sale.quantity or 1)
            qty = avail if (u.quantity is None or u.quantity <= 0 or u.quantity > avail) else int(u.quantity)
            unit_price = Decimal(str(sale.unit_price or 0))
            line_amount = unit_price * qty

            product = db.query(Product).filter(Product.product_id == sale.product_id).first() if sale.product_id else None

            # 1) Remise en stock
            if sale.variant_id:
                variant = db.query(ProductVariant).filter(ProductVariant.variant_id == sale.variant_id).first()
                _release_variant(variant, qty)
                if product is not None:
                    product.quantity = (product.quantity or 0) + qty
            elif product is not None:
                product.quantity = (product.quantity or 0) + qty           # produit simple

            # Mouvement de stock IN
            try:
                if sale.product_id:
                    create_stock_movement_entry(
                        db=db, product_id=sale.product_id, quantity=qty,
                        movement_type="IN", reference_type="RETURN", reference_id=invoice_id,
                        notes=f"Retour produit facture {invoice.invoice_number}",
                        unit_price=float(unit_price),
                    )
            except Exception:
                pass

            # 2) Annuler le CA (daily_sales)
            if qty >= avail:
                db.delete(sale)
            else:
                sale.quantity = avail - qty
                sale.total_amount = Decimal(str(sale.total_amount or 0)) - line_amount

            # 3) Réduire/supprimer la ligne de facture correspondante (hors cadeaux).
            #    Si l'unité vendue est identifiée (IMEI), on vise la ligne exacte
            #    plutôt que n'importe quelle ligne du même produit.
            item = None
            if sale.variant_id:
                item = (
                    db.query(InvoiceItem)
                    .filter(
                        InvoiceItem.invoice_id == invoice_id,
                        InvoiceItem.variant_id == sale.variant_id,
                        InvoiceItem.is_gift == False,  # noqa: E712
                        InvoiceItem.quantity > 0,
                    )
                    .first()
                )
            if item is None:
                item = (
                    db.query(InvoiceItem)
                    .filter(
                        InvoiceItem.invoice_id == invoice_id,
                        InvoiceItem.product_id == sale.product_id,
                        InvoiceItem.is_gift == False,  # noqa: E712
                        InvoiceItem.quantity > 0,
                    )
                    .order_by(InvoiceItem.quantity.asc())
                    .first()
                )
            if item is not None:
                remaining = int(item.quantity or 0) - qty
                if remaining <= 0:
                    db.delete(item)
                else:
                    item.quantity = remaining
                    item.total = Decimal(str(item.price or 0)) * remaining

            returned_subtotal += line_amount
            details.append({
                "product_name": sale.product_name,
                "imei": sale.variant_imei,
                "quantity": qty,
                "amount": float(line_amount),
            })

        db.flush()

        # 4) Recalcul des totaux de la facture (par delta, préserve toute remise d'échange)
        rate = Decimal(str(invoice.tax_rate or 0))
        tax_delta = Decimal("0")
        if bool(invoice.show_tax) and rate > 0:
            tax_delta = (returned_subtotal * rate / Decimal("100")).quantize(Decimal("1"))
        new_subtotal = Decimal(str(invoice.subtotal or 0)) - returned_subtotal
        if new_subtotal < 0:
            new_subtotal = Decimal("0")
        new_tax = Decimal(str(invoice.tax_amount or 0)) - tax_delta
        if new_tax < 0:
            new_tax = Decimal("0")
        new_total = Decimal(str(invoice.total or 0)) - returned_subtotal - tax_delta
        if new_total < 0:
            new_total = Decimal("0")
        invoice.subtotal = new_subtotal
        invoice.tax_amount = new_tax
        invoice.total = new_total

        # 5) Remboursement si la facture était déjà (partiellement) payée
        refund = Decimal("0")
        paid = Decimal(str(invoice.paid_amount or 0))
        if paid > new_total:
            refund = (paid - new_total)
            db.add(InvoicePayment(
                invoice_id=invoice_id,
                amount=(-refund).quantize(Decimal("1")),
                payment_method=(payload.refund_method or "espèces"),
                payment_date=datetime.now(),
                reference=f"RETOUR-{invoice.invoice_number}",
                notes="Remboursement automatique suite au retour d'un produit",
            ))
            invoice.paid_amount = new_total.quantize(Decimal("1"))
            paid = new_total
        invoice.remaining_amount = (new_total - paid) if new_total > paid else Decimal("0")

        # Statut
        remaining_items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice_id).count()
        if remaining_items == 0:
            invoice.status = "annulée"
        elif invoice.remaining_amount == 0 and paid > 0:
            invoice.status = "payée"
        elif paid > 0:
            invoice.status = "partiellement payée"
        else:
            invoice.status = "en attente"

        db.commit()
        try:
            invalidate_dashboard_cache()
        except Exception:
            pass
        try:
            recompute_invoices_stats(db)
        except Exception:
            pass
        _invoices_cache.clear()

        return {
            "message": "Retour effectué avec succès",
            "returned": details,
            "returned_amount": float(returned_subtotal + tax_delta),
            "refund_amount": float(refund),
            "new_total": float(new_total),
            "status": invoice.status,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors du retour produit: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")


@router.delete("/{invoice_id}")
async def delete_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Supprimer une facture (admin seulement)"""
    try:
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        if current_user.role not in ["admin"]:
            raise HTTPException(status_code=403, detail="Permissions insuffisantes")
        
        # Restaurer le stock des produits
        for item in invoice.items:
            product = db.query(Product).filter(Product.product_id == item.product_id).first()
            if product:
                product.quantity += item.quantity
                create_stock_movement_entry(
                    db=db,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    movement_type="IN",
                    reference_type="INVOICE_CANCELLATION",
                    reference_id=invoice_id,
                    notes=f"Annulation facture {invoice.invoice_number}",
                    unit_price=float(item.price)
                )

                # Synchroniser le stock avec Google Sheets (si activé)
                try:
                    sync_product_stock_to_sheets(db, item.product_id)
                except Exception as e:
                    logging.warning(f"Échec de synchronisation Google Sheets pour le produit {item.product_id}: {e}")
        
        # Réactiver les unités vendues (même rattachement que lors d'une modification).
        # La quantité produit a déjà été recréditée ligne par ligne juste au-dessus.
        _restore_sold_units(db, invoice, list(invoice.items or []))
        
        # Supprimer également tous les bons de livraison associés à cette facture
        try:
            related_dns = db.query(DeliveryNote).filter(DeliveryNote.invoice_id == invoice_id).all()
            for dn in (related_dns or []):
                try:
                    # Les items seront supprimés grâce au cascade="all, delete-orphan"
                    db.delete(dn)
                except Exception:
                    pass
        except Exception:
            # Ne pas bloquer la suppression de la facture si la recherche/itération échoue
            pass

        # Supprimer explicitement les paiements avant de supprimer la facture (pour éviter les problèmes de cache)
        try:
            payments = db.query(InvoicePayment).filter(InvoicePayment.invoice_id == invoice_id).all()
            for payment in payments:
                db.delete(payment)
            db.flush()
        except Exception as e:
            logging.warning(f"Erreur lors de la suppression des paiements: {e}")
        
        # Supprimer les produits provenant d'un échange liés à cette facture
        try:
            for ex_item in (invoice.exchange_items or []):
                if ex_item.product_id:
                    ex_product = db.query(Product).filter(
                        Product.product_id == ex_item.product_id,
                        Product.source == 'exchange'
                    ).first()
                    if ex_product:
                        # Supprimer explicitement le produit créé par l'échange
                        db.delete(ex_product)
        except Exception as e:
            logging.warning(f"Erreur lors de la suppression des produits d'échange: {e}")

        db.delete(invoice)
        db.commit()
        
        # Clear invoices cache after deletion to ensure fresh data on next load
        _invoices_cache.clear()
        
        try:
            recompute_invoices_stats(db)
        except Exception:
            pass
        
        return {"message": "Facture supprimée avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la suppression de la facture: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/stats/dashboard")
async def get_invoice_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Obtenir les statistiques des factures pour le tableau de bord"""
    try:
        today = date.today()
        
        # Total des factures
        total_invoices = db.query(Invoice).count()
        
        # Comptages par statut (support FR/EN)
        pending_invoices = db.query(Invoice).filter(Invoice.status.in_(["en attente", "SENT", "DRAFT", "OVERDUE", "partiellement payée"]) ).count()
        paid_invoices = db.query(Invoice).filter(Invoice.status.in_(["payée", "PAID"]) ).count()

        # Si l'utilisateur n'est pas admin, ne pas exposer les chiffres d'affaires
        try:
            role = getattr(current_user, "role", "user")
        except Exception:
            role = "user"
        if role != "admin":
            return {
                "total_invoices": total_invoices,
                "pending_invoices": pending_invoices,
                "paid_invoices": paid_invoices,
            }
        
        # Chiffre d'affaires brut du mois
        monthly_revenue_gross = db.query(func.sum(Invoice.total)).filter(
            func.extract('month', Invoice.date) == today.month,
            func.extract('year', Invoice.date) == today.year,
            Invoice.status.in_(["payée", "PAID"])
        ).scalar() or 0

        # Achats quotidiens du mois (par date ou created_at)
        monthly_daily_purchases = db.query(func.coalesce(func.sum(DailyPurchase.amount), 0)).filter(
            or_(
                and_(func.extract('month', DailyPurchase.date) == today.month, func.extract('year', DailyPurchase.date) == today.year),
                and_(func.extract('month', DailyPurchase.created_at) == today.month, func.extract('year', DailyPurchase.created_at) == today.year),
            )
        ).scalar() or 0
        
        # Paiements aux fournisseurs du mois
        monthly_supplier_payments = db.query(func.sum(SupplierInvoice.paid_amount)).filter(
            func.extract('month', SupplierInvoice.invoice_date) == today.month,
            func.extract('year', SupplierInvoice.invoice_date) == today.year
        ).scalar() or 0
        
        # Chiffre d'affaires net du mois (déduction achats quotidiens)
        monthly_revenue = float(monthly_revenue_gross or 0) - float(monthly_supplier_payments or 0) - float(monthly_daily_purchases or 0)
        
        # Chiffre d'affaires total brut (toutes factures payées)
        total_revenue_gross = db.query(func.sum(Invoice.total)).filter(Invoice.status.in_(["payée", "PAID"])).scalar() or 0
        
        # Total des paiements aux fournisseurs
        total_supplier_payments = db.query(func.sum(SupplierInvoice.paid_amount)).scalar() or 0
        
        # Total des achats quotidiens (toute période)
        total_daily_purchases = db.query(func.coalesce(func.sum(DailyPurchase.amount), 0)).scalar() or 0
        
        # Chiffre d'affaires total net (déduction achats quotidiens)
        total_revenue = float(total_revenue_gross or 0) - float(total_supplier_payments or 0) - float(total_daily_purchases or 0)
        
        # Montant impayé (restant)
        unpaid_amount = db.query(func.sum(Invoice.remaining_amount)).filter(Invoice.status.in_(["en attente", "partiellement payée", "OVERDUE"])) .scalar() or 0
        
        # Toujours recalculer à la demande pour refléter immédiatement les derniers changements (admin uniquement)
        try:
            from ..services.stats_manager import recompute_invoices_stats
            return recompute_invoices_stats(db)
        except Exception:
            return {
                "total_invoices": total_invoices,
                "pending_invoices": pending_invoices,
                "paid_invoices": paid_invoices,
                "monthly_revenue": float(monthly_revenue),
                "monthly_revenue_gross": float(monthly_revenue_gross),
                "monthly_supplier_payments": float(monthly_supplier_payments),
                "monthly_daily_purchases": float(monthly_daily_purchases),
                "total_revenue": float(total_revenue),
                "total_revenue_gross": float(total_revenue_gross),
                "total_supplier_payments": float(total_supplier_payments),
                "total_daily_purchases": float(total_daily_purchases),
                "unpaid_amount": float(unpaid_amount)
            }
        
    except Exception as e:
        logging.error(f"Erreur lors du calcul des stats factures: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.post("/{invoice_id}/delivery-note")
async def create_delivery_note_from_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Générer un bon de livraison à partir d'une facture existante.

    - Copie les lignes produits (ignore les lignes personnalisées sans produit)
    - Calque les montants (HT/TVA/Total) de la facture
    - Tente d'attacher les numéros de série/IMEI depuis les notes de la facture (__SERIALS__=...)
    """
    try:
        # Charger la facture et ses éléments
        invoice = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        _ = invoice.items  # force load
        _ = invoice.client  # force load

        # Générer un numéro de BL: BL-YYYYMMDD-XXXX
        from datetime import datetime as _dt
        today_prefix = _dt.now().strftime("BL-%Y%m%d-")
        last_note = (
            db.query(DeliveryNote)
            .filter(DeliveryNote.delivery_note_number.ilike(f"{today_prefix}%"))
            .order_by(DeliveryNote.delivery_note_id.desc())
            .first()
        )
        if last_note and last_note.delivery_note_number.startswith(today_prefix):
            try:
                last_seq = int(last_note.delivery_note_number.split("-")[-1])
            except Exception:
                last_seq = 0
            next_seq = last_seq + 1
        else:
            next_seq = 1
        delivery_number = f"{today_prefix}{next_seq:04d}"

        # Parser les IMEIs/séries depuis les notes de facture si présents
        serials_meta = []
        try:
            txt = str(invoice.notes or "")
            if "__SERIALS__=" in txt:
                import re, json
                sub = txt.split("__SERIALS__=", 1)[1]
                cut_idx = sub.find("\n__")
                if cut_idx != -1:
                    sub = sub[:cut_idx].strip()
                sub = sub.strip()
                try:
                    serials_meta = json.loads(sub)
                except Exception:
                    m = re.search(r"__SERIALS__=(\[.*?\])", txt, flags=re.S)
                    if m:
                        serials_meta = json.loads(m.group(1))
        except Exception:
            serials_meta = []

        # Index des séries par produit
        product_id_to_imeis = {}
        try:
            for entry in (serials_meta or []):
                pid = entry.get("product_id")
                if pid is None:
                    continue
                product_id_to_imeis[int(pid)] = list(entry.get("imeis") or [])
        except Exception:
            product_id_to_imeis = {}

        # Créer le BL
        dn = DeliveryNote(
            delivery_note_number=delivery_number,
            invoice_id=invoice.invoice_id,
            client_id=invoice.client_id,
            date=invoice.date or _dt.now(),
            delivery_date=_dt.now(),
            status="en_preparation",
            delivery_address=getattr(invoice.client, "address", None) if invoice.client else None,
            delivery_contact=getattr(invoice.client, "name", None) if invoice.client else None,
            delivery_phone=getattr(invoice.client, "phone", None) if invoice.client else None,
            subtotal=invoice.subtotal,
            tax_rate=invoice.tax_rate,
            tax_amount=invoice.tax_amount,
            total=invoice.total,
            notes=f"Créé depuis facture {invoice.invoice_number}"
        )
        db.add(dn)
        db.flush()  # obtenir l'ID

        # Lignes du BL à partir des lignes facture (produits uniquement)
        for it in (invoice.items or []):
            if it.product_id is None:
                # ignorer lignes personnalisées
                continue
            # Priorité à l'IMEI porté par la ligne : il vise l'exemplaire exact,
            # là où le bloc __SERIALS__ recopie toute la liste du produit sur
            # chacune de ses lignes. Repli sur les notes pour les factures
            # antérieures à la colonne variant_imei.
            line_imei = str(getattr(it, "variant_imei", None) or "").strip()
            if line_imei:
                imeis = [line_imei]
            else:
                imeis = product_id_to_imeis.get(int(it.product_id), [])
            dn_item = DeliveryNoteItem(
                delivery_note_id=dn.delivery_note_id,
                product_id=it.product_id,
                product_name=it.product_name,
                quantity=it.quantity,
                price=it.price,
                delivered_quantity=0,
                serial_numbers=(None if not imeis else __import__("json").dumps(imeis))
            )
            db.add(dn_item)

        db.commit()
        db.refresh(dn)

        return {
            "message": "Bon de livraison créé",
            "delivery_note_id": dn.delivery_note_id,
            "delivery_note_number": dn.delivery_note_number,
        }

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la génération du BL depuis facture: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# Créer l'instance de templates
templates = Jinja2Templates(directory="templates")

@router.get("/{invoice_id}/warranty-certificate", response_class=HTMLResponse)
async def get_warranty_certificate(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Générer et afficher le certificat de garantie pour une facture"""
    try:
        # Charger la facture avec le client
        from sqlalchemy.orm import joinedload
        invoice = db.query(Invoice).options(
            joinedload(Invoice.client)
        ).filter(Invoice.invoice_id == invoice_id).first()
        
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Vérifier si la facture a une garantie
        if not getattr(invoice, 'has_warranty', False):
            raise HTTPException(status_code=400, detail="Cette facture n'a pas de garantie associée")
        
        # Forcer le chargement du client
        _ = invoice.client
        
        # Préparer les données pour le template
        warranty_duration = getattr(invoice, 'warranty_duration', 12)
        
        # Utiliser une fausse requête pour le template
        class FakeRequest:
            def __init__(self):
                pass
            
            def get(self, key, default=None):
                return default
        
        return templates.TemplateResponse("warranty_certificate.html", {
            "request": FakeRequest(),
            "invoice": invoice,
            "warranty_duration": warranty_duration
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la génération du certificat de garantie: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

# Configuration email (SMTP à configurer ultérieurement)

from pydantic import BaseModel


async def _pdf_de_la_facture(invoice) -> Optional[bytes]:
    """Rend la page d'impression en PDF, pour la joindre au message.

    Best-effort : un Chromium indisponible ne doit pas empêcher le client de
    recevoir son message. L'appelant le dit alors dans le texte, plutôt que
    d'annoncer une pièce jointe absente.
    """
    try:
        from main import _generate_pdf_from_url
        base = os.getenv("APP_INTERNAL_URL", "http://localhost:8000").rstrip("/")
        return await _generate_pdf_from_url(
            f"{base}/invoices/print/{invoice.invoice_id}")
    except Exception:  # noqa: BLE001
        logging.exception("[courriel] PDF de la facture %s non produit",
                          invoice.invoice_id)
        return None


def _entetes_internes() -> dict:
    """Jeton présenté aux routes internes de l'application (voir main.py).

    Sans lui, ces appels serveur à serveur se heurteraient au 401 posé le
    13/08/2026 sur `/api/whatsapp/*`.
    """
    jeton = os.getenv("INTERNAL_API_TOKEN", "").strip()
    return {"X-Internal-Token": jeton} if jeton else {}



class SendWhatsAppRequest(BaseModel):
    invoice_id: int
    phone: str

class SendEmailRequest(BaseModel):
    invoice_id: int
    email: str

@router.post("/send-whatsapp")
async def send_invoice_whatsapp(
    request: Request,
    data: SendWhatsAppRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Envoyer une facture par WhatsApp via Evolution API (directement)"""
    try:
        # Vérifier que la facture existe
        invoice = db.query(Invoice).filter(Invoice.invoice_id == data.invoice_id).first()
        if not invoice:
            raise HTTPException(status_code=404, detail="Facture non trouvée")

        # Construire l'URL du PDF de la facture
        app_internal_url = os.getenv("APP_INTERNAL_URL", "http://stock_app:8000")
        html_url = f"{app_internal_url}/invoices/print/{data.invoice_id}"

        # Formater le total
        total = float(invoice.total or 0)
        total_formatted = f"{total:,.0f}".replace(",", " ")

        client_name = invoice.client.name if invoice.client else "Client"
        caption = (
            f"Bonjour {client_name},\n\n"
            f"\U0001f4c4 *FACTURE N\u00b0 {invoice.invoice_number}*\n"
            f"\U0001f4b0 Montant total: {total_formatted} F CFA\n\n"
            f"Merci pour votre confiance.\n"
            f"Pour toute question, n'h\u00e9sitez pas \u00e0 nous contacter.\n\n"
            f"Cordialement,\n"
            f"{os.getenv('APP_NAME', 'Stock')}"
        )

        filename = f"Facture-{invoice.invoice_number}.pdf"
        phone = data.phone.strip()

        # Appeler directement notre proxy WhatsApp (qui gère PDF + Evolution API)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{app_internal_url}/api/whatsapp/send-pdf-from-html",
                headers=_entetes_internes(),
                json={
                    "number": phone,
                    "htmlUrl": html_url,
                    "filename": filename,
                    "caption": caption
                }
            )
            result = response.json()

        if result.get("success"):
            return {"success": True, "message": "Facture envoy\u00e9e par WhatsApp"}
        else:
            error_msg = result.get("error", "Erreur inconnue")
            logging.error(f"Erreur envoi WhatsApp facture: {error_msg}")
            return {"success": False, "message": f"Erreur: {error_msg}"}

    except httpx.RequestError as e:
        logging.error(f"Erreur connexion WhatsApp: {e}")
        raise HTTPException(status_code=503, detail="Service WhatsApp indisponible")
    except Exception as e:
        logging.error(f"Erreur envoi WhatsApp: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/send-email")
async def send_invoice_email(
    request: Request,
    data: SendEmailRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Envoyer une facture par e-mail, PDF joint."""
    from ..services import mailer

    invoice = db.query(Invoice).filter(Invoice.invoice_id == data.invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    config = mailer.charger_config(db)
    if not mailer.est_configure(config):
        # 200 avec success=false : l'interface affiche déjà ce message, et ce
        # n'est pas une erreur du serveur mais un réglage à faire.
        return {"success": False,
                "message": "L'envoi par e-mail n'est pas configuré. Renseignez "
                           "votre serveur dans Paramètres → E-mail."}

    pdf = await _pdf_de_la_facture(invoice)

    total = float(invoice.total or 0)
    total_formate = f"{total:,.0f}".replace(",", " ")
    nom_client = invoice.client.name if invoice.client else "Madame, Monsieur"
    boutique = os.getenv("APP_NAME", "Stock")

    texte = (
        f"Bonjour {nom_client},\n\n"
        f"Vous trouverez ci-joint votre facture n° {invoice.invoice_number}, "
        f"d'un montant de {total_formate} F CFA.\n\n"
        f"Merci pour votre confiance.\n"
        f"Pour toute question, n'hésitez pas à nous répondre directement.\n\n"
        f"Cordialement,\n{boutique}"
    )
    if not pdf:
        texte += ("\n\n(Le PDF n'a pas pu être joint : contactez-nous pour en "
                  "recevoir une copie.)")

    resultat = mailer.envoyer_document(
        db, data.email, f"Facture {invoice.invoice_number} — {boutique}",
        texte, pdf, f"Facture-{invoice.invoice_number}.pdf")

    if not resultat["envoye"]:
        return {"success": False, "message": resultat["erreur"]}
    return {
        "success": True,
        "message": (f"Facture envoyée à {data.email}."
                    if pdf else
                    f"Message envoyé à {data.email}, mais sans le PDF."),
        "piece_jointe": bool(pdf),
    }

@router.post("/{invoice_id}/duplicate", response_model=InvoiceResponse)
async def duplicate_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Dupliquer une facture existante avec tous ses articles (sans les paiements)"""
    try:
        original = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
        if not original:
            raise HTTPException(status_code=404, detail="Facture non trouvée")
        
        # Générer un nouveau numéro de facture
        new_number = _next_invoice_number(db)
        
        # Créer une copie de la facture
        new_date = datetime.now()
        new_invoice = Invoice(
            invoice_number=new_number,
            client_id=original.client_id,
            date=new_date,
            due_date=new_date + timedelta(days=30),
            status="en attente",
            payment_method=original.payment_method,
            subtotal=original.subtotal,
            tax_rate=original.tax_rate,
            tax_amount=original.tax_amount,
            total=original.total,
            paid_amount=0,
            remaining_amount=original.total,
            notes=original.notes,
            internal_notes=original.internal_notes,
            external_notes=original.external_notes,
            show_tax=original.show_tax,
            show_item_prices=original.show_item_prices,
            show_section_totals=original.show_section_totals,
            price_display=original.price_display,
            has_warranty=original.has_warranty,
            warranty_duration=original.warranty_duration,
        )
        
        db.add(new_invoice)
        db.flush()  # Pour obtenir l'ID de la nouvelle facture
        
        # Copier les articles (sans décrémenter le stock).
        # variant_id/variant_imei ne sont volontairement pas repris: la copie ne
        # vend pas le même exemplaire physique, l'IMEI sera choisi à la validation.
        original_items = db.query(InvoiceItem).filter(InvoiceItem.invoice_id == invoice_id).all()
        for item in original_items:
            new_item = InvoiceItem(
                invoice_id=new_invoice.invoice_id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                price=item.price,
                total=item.total,
                is_gift=item.is_gift,
                external_price=item.external_price,
                external_profit=item.external_profit
            )
            db.add(new_item)
        
        db.commit()
        db.refresh(new_invoice)
        
        # Récupérer le nom du client pour la réponse
        client = db.query(Client).filter(Client.client_id == new_invoice.client_id).first()
        client_name = client.name if client else ""
        
        # Construire la réponse avec client_name
        return {
            "invoice_id": new_invoice.invoice_id,
            "invoice_number": new_invoice.invoice_number,
            "client_id": new_invoice.client_id,
            "client_name": client_name,
            "quotation_id": new_invoice.quotation_id,
            "date": new_invoice.date,
            "due_date": new_invoice.due_date,
            "status": new_invoice.status,
            "payment_method": new_invoice.payment_method,
            "subtotal": new_invoice.subtotal,
            "tax_rate": new_invoice.tax_rate,
            "tax_amount": new_invoice.tax_amount,
            "total": new_invoice.total,
            "paid_amount": new_invoice.paid_amount,
            "remaining_amount": new_invoice.remaining_amount,
            "notes": new_invoice.notes,
            "internal_notes": new_invoice.internal_notes,
            "external_notes": new_invoice.external_notes,
            "show_tax": new_invoice.show_tax,
            "show_item_prices": new_invoice.show_item_prices,
            "show_section_totals": new_invoice.show_section_totals,
            "price_display": new_invoice.price_display,
            "has_warranty": new_invoice.has_warranty,
            "warranty_duration": new_invoice.warranty_duration,
            "warranty_start_date": new_invoice.warranty_start_date,
            "warranty_end_date": new_invoice.warranty_end_date,
            "created_at": new_invoice.created_at,
            "items": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Erreur lors de la duplication de la facture: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la duplication")
