from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from typing import List, Optional
from datetime import datetime, timedelta, date
import json
import logging

from ..database import get_db, User
from ..database import Invoice, InvoiceItem, InvoicePayment, Quotation, Product, ProductVariant, Client
from ..auth import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])

@router.get("/dashboard")
async def get_dashboard_metrics(
    days: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """KPI réels pour le tableau de bord, calculés depuis SQLite.
    - Panier moyen (factures payées sur N derniers jours)
    - Taux de conversion devis -> factures (N jours)
    - Stock critique (<=3) + en rupture (=0)
    - Clients actifs (90 jours)
    - Répartition des paiements (N jours)
    - Top produits par CA (N jours)
    """
    try:
        now = datetime.now()
        since = now - timedelta(days=max(1, days))

        # Panier moyen sur factures payées (FR/EN)
        paid_statuses = ["payée", "PAID"]
        invoices_q = (
            db.query(Invoice)
            .filter(func.date(Invoice.date) >= since.date())
            .filter(Invoice.status.in_(paid_statuses))
        )
        num_invoices = invoices_q.count()
        total_revenue = float(
            db.query(func.coalesce(func.sum(Invoice.total), 0))
            .filter(func.date(Invoice.date) >= since.date())
            .filter(Invoice.status.in_(paid_statuses))
            .scalar()
            or 0
        )
        avg_ticket = float(total_revenue / num_invoices) if num_invoices else 0.0

        # Conversion devis -> factures (N jours)
        quotes_total = db.query(func.count(Quotation.quotation_id)).filter(func.date(Quotation.date) >= since.date()).scalar() or 0
        converted_quotes = (
            db.query(func.count(func.distinct(Invoice.quotation_id)))
            .filter(Invoice.quotation_id.isnot(None))
            .filter(func.date(Invoice.date) >= since.date())
            .scalar()
            or 0
        )
        conversion_rate = float((converted_quotes / quotes_total) * 100) if quotes_total else 0.0

        # Stock critique
        out_of_stock = db.query(func.count(Product.product_id)).filter((Product.quantity == 0) | (Product.quantity.is_(None))).scalar() or 0
        low_stock = db.query(func.count(Product.product_id)).filter(Product.quantity > 0, Product.quantity <= 3).scalar() or 0

        # Clients actifs (90 jours)
        since_90 = now - timedelta(days=90)
        active_customers = (
            db.query(func.count(func.distinct(Invoice.client_id)))
            .filter(Invoice.client_id.isnot(None))
            .filter(func.date(Invoice.date) >= since_90.date())
            .scalar()
            or 0
        )

        # Répartition des paiements (N jours)
        payments = (
            db.query(InvoicePayment.payment_method, func.coalesce(func.sum(InvoicePayment.amount), 0).label("amount"))
            .filter(func.date(InvoicePayment.payment_date) >= since.date())
            .group_by(InvoicePayment.payment_method)
            .order_by(desc("amount"))
            .all()
        )
        payments_breakdown = [
            {"method": (pm or "Non spécifié"), "amount": float(am or 0)} for pm, am in payments
        ]

        # Top produits par CA (N jours)
        top_products_rows = (
            db.query(
                InvoiceItem.product_name,
                func.coalesce(func.sum(InvoiceItem.total), 0).label("revenue"),
            )
            .join(Invoice, InvoiceItem.invoice_id == Invoice.invoice_id)
            .filter(func.date(Invoice.date) >= since.date())
            .group_by(InvoiceItem.product_name)
            .order_by(desc("revenue"))
            .limit(5)
            .all()
        )
        top_products = [
            {"name": (name or "-"), "revenue": float(rev or 0)} for name, rev in top_products_rows
        ]

        return {
            "avg_ticket": avg_ticket,
            "conversion_rate": conversion_rate,
            "stock": {
                "low_stock": int(low_stock),
                "out_of_stock": int(out_of_stock),
                "critical_total": int(low_stock + out_of_stock),
            },
            "active_customers": int(active_customers),
            "payments": payments_breakdown,
            "top_products": top_products,
            "period_days": days,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stock-summary")
async def get_stock_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récapitulatif de stock avec valeur et bénéfice potentiel"""
    try:
        # Récupérer tous les produits non archivés
        products = db.query(Product).filter(Product.is_archived == False).all()
        
        total_products = 0
        total_stock_value = 0.0
        total_potential_profit = 0.0
        total_purchase_cost = 0.0
        products_with_stock = 0
        products_out_of_stock = 0
        
        # Par catégorie
        category_stats = {}
        
        for product in products:
            # Déterminer si le produit a des variantes
            has_variants = db.query(ProductVariant.variant_id).filter(
                ProductVariant.product_id == product.product_id
            ).first() is not None
            
            available_quantity = 0
            product_price = float(product.price or 0)
            product_purchase_price = float(product.purchase_price or 0)
            
            if has_variants:
                # Pour les produits avec variantes, compter les variantes non vendues
                available_quantity = db.query(func.count(ProductVariant.variant_id)).filter(
                    ProductVariant.product_id == product.product_id,
                    ProductVariant.is_sold == False
                ).scalar() or 0
                
                # Pour les variantes, utiliser le prix de la variante si disponible, sinon le prix du produit
                # On calcule une moyenne (simplifié - pourrait être amélioré)
                variants = db.query(ProductVariant).filter(
                    ProductVariant.product_id == product.product_id,
                    ProductVariant.is_sold == False
                ).all()
                
                if variants:
                    variant_prices = [float(v.price or product_price) for v in variants if v.price is not None or product_price > 0]
                    if variant_prices:
                        product_price = sum(variant_prices) / len(variant_prices)
            else:
                # Pour les produits sans variantes, utiliser la quantité
                available_quantity = int(product.quantity or 0)
            
            if available_quantity > 0:
                products_with_stock += 1
                stock_value = available_quantity * product_price
                purchase_cost = available_quantity * product_purchase_price
                potential_profit = stock_value - purchase_cost
                
                total_stock_value += stock_value
                total_purchase_cost += purchase_cost
                total_potential_profit += potential_profit
                
                # Statistiques par catégorie
                category = product.category or "Non catégorisé"
                if category not in category_stats:
                    category_stats[category] = {
                        "products_count": 0,
                        "stock_value": 0.0,
                        "potential_profit": 0.0,
                        "quantity": 0
                    }
                category_stats[category]["products_count"] += 1
                category_stats[category]["stock_value"] += stock_value
                category_stats[category]["potential_profit"] += potential_profit
                category_stats[category]["quantity"] += available_quantity
            else:
                products_out_of_stock += 1
            
            total_products += 1
        
        # Calculer la marge bénéficiaire en pourcentage
        profit_margin = 0.0
        if total_stock_value > 0:
            profit_margin = (total_potential_profit / total_stock_value) * 100
        
        return {
            "summary": {
                "total_products": total_products,
                "products_with_stock": products_with_stock,
                "products_out_of_stock": products_out_of_stock,
                "total_stock_value": round(total_stock_value, 2),
                "total_purchase_cost": round(total_purchase_cost, 2),
                "total_potential_profit": round(total_potential_profit, 2),
                "profit_margin_percent": round(profit_margin, 2)
            },
            "by_category": {
                category: {
                    "products_count": stats["products_count"],
                    "quantity": stats["quantity"],
                    "stock_value": round(stats["stock_value"], 2),
                    "potential_profit": round(stats["potential_profit"], 2)
                }
                for category, stats in category_stats.items()
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Rapport de synthèse sur une tranche de dates
#
# Tout est agrégé en SQL. La page appelait auparavant `/api/products/`,
# `/api/invoices/` et `/api/stock-movements/` pour recalculer les totaux dans le
# navigateur : ces listes sont plafonnées côté serveur à 100 lignes, et les
# rapports affichaient donc des chiffres tronqués — 100 produits sur 120, 100
# mouvements sur 332 — sans que rien ne le signale.
# =============================================================================

# Une facture annulée ne fait pas de chiffre d'affaires. Les libellés varient
# selon l'écran qui a écrit le statut, d'où la liste.
CANCELLED_STATUSES = ("annulée", "annulee", "CANCELLED", "cancelled")


def _parse_day(value: Optional[str]) -> Optional[date]:
    """Lit une date en ISO (2026-08-04) ou en français (04/08/2026)."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _resolve_range(start_date: Optional[str], end_date: Optional[str]):
    """Bornes de la tranche, remises dans l'ordre si elles arrivent inversées."""
    start = _parse_day(start_date)
    end = _parse_day(end_date)
    if start is None and end is None:
        end = date.today()
        start = end - timedelta(days=29)
    else:
        start = start or end
        end = end or start
        if start > end:
            start, end = end, start
    return start, end


def _growth(current: float, previous: float) -> Optional[float]:
    """Évolution en pourcentage. `None` quand la période précédente est vide :
    afficher une progression à partir de zéro ne veut rien dire."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _revenue_totals(db: Session, start: date, end: date):
    """Facturé, nombre de factures et encaissé sur une tranche."""
    invoiced, count = db.query(
        func.coalesce(func.sum(Invoice.total), 0),
        func.count(Invoice.invoice_id)
    ).filter(
        func.date(Invoice.date) >= start,
        func.date(Invoice.date) <= end,
        Invoice.status.notin_(CANCELLED_STATUSES)
    ).one()

    paid = db.query(func.coalesce(func.sum(InvoicePayment.amount), 0)).filter(
        func.date(InvoicePayment.payment_date) >= start,
        func.date(InvoicePayment.payment_date) <= end
    ).scalar() or 0

    return float(invoiced or 0), int(count or 0), float(paid or 0)


def _iso_key(value):
    """`func.date()` renvoie une chaîne sous SQLite et une date sous
    PostgreSQL : on ramène les deux à une chaîne ISO."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


@router.get("/summary")
async def get_reports_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Synthèse commerciale sur une tranche de dates, comparée à la tranche
    précédente de même durée."""
    try:
        from ..database import BankTransaction, DailyPurchase

        start, end = _resolve_range(start_date, end_date)
        days_count = (end - start).days + 1

        # Tranche précédente de même durée, pour l'évolution.
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days_count - 1)

        invoiced, invoices_count, paid = _revenue_totals(db, start, end)
        prev_invoiced, prev_invoices_count, prev_paid = _revenue_totals(db, prev_start, prev_end)

        in_range = (
            func.date(Invoice.date) >= start,
            func.date(Invoice.date) <= end,
            Invoice.status.notin_(CANCELLED_STATUSES),
        )

        # --- Ventilation quotidienne --------------------------------------
        invoiced_rows = db.query(
            func.date(Invoice.date).label("day"),
            func.coalesce(func.sum(Invoice.total), 0),
            func.count(Invoice.invoice_id)
        ).filter(*in_range).group_by("day").all()

        paid_rows = db.query(
            func.date(InvoicePayment.payment_date).label("day"),
            func.coalesce(func.sum(InvoicePayment.amount), 0)
        ).filter(
            func.date(InvoicePayment.payment_date) >= start,
            func.date(InvoicePayment.payment_date) <= end
        ).group_by("day").all()

        invoiced_by_day = {_iso_key(d): (float(t or 0), int(c or 0)) for d, t, c in invoiced_rows}
        paid_by_day = {_iso_key(d): float(t or 0) for d, t in paid_rows}

        by_day = []
        cursor = start
        while cursor <= end:
            iso = cursor.isoformat()
            amount, count = invoiced_by_day.get(iso, (0.0, 0))
            by_day.append({
                "date": iso,
                "label": cursor.strftime("%d/%m"),
                "invoiced": amount,
                "invoices_count": count,
                "paid": paid_by_day.get(iso, 0.0),
            })
            cursor += timedelta(days=1)

        # --- Produits et catégories ---------------------------------------
        # Les articles offerts sortent du stock mais ne font pas de chiffre
        # d'affaires (cf. `InvoiceItem.is_gift`).
        item_filters = in_range + (InvoiceItem.is_gift.is_(False),)

        category_rows = db.query(
            func.coalesce(Product.category, "Sans catégorie").label("category"),
            func.coalesce(func.sum(InvoiceItem.total), 0),
            func.coalesce(func.sum(InvoiceItem.quantity), 0)
        ).select_from(InvoiceItem).join(
            Invoice, Invoice.invoice_id == InvoiceItem.invoice_id
        ).outerjoin(
            Product, Product.product_id == InvoiceItem.product_id
        ).filter(*item_filters).group_by("category").all()

        by_category = sorted(
            [{"category": c, "revenue": float(r or 0), "quantity": int(q or 0)}
             for c, r, q in category_rows],
            key=lambda x: x["revenue"], reverse=True
        )

        product_rows = db.query(
            InvoiceItem.product_name,
            func.coalesce(func.sum(InvoiceItem.total), 0),
            func.coalesce(func.sum(InvoiceItem.quantity), 0)
        ).select_from(InvoiceItem).join(
            Invoice, Invoice.invoice_id == InvoiceItem.invoice_id
        ).filter(*item_filters).group_by(InvoiceItem.product_name).order_by(
            desc(func.sum(InvoiceItem.total))
        ).limit(10).all()

        top_products = [
            {"name": n or "Produit inconnu", "revenue": float(r or 0), "quantity": int(q or 0)}
            for n, r, q in product_rows
        ]

        # --- Clients --------------------------------------------------------
        client_rows = db.query(
            func.coalesce(Client.name, "Client inconnu").label("name"),
            func.coalesce(func.sum(Invoice.total), 0),
            func.count(Invoice.invoice_id)
        ).select_from(Invoice).outerjoin(
            Client, Client.client_id == Invoice.client_id
        ).filter(*in_range).group_by("name").order_by(
            desc(func.sum(Invoice.total))
        ).limit(10).all()

        top_clients = [
            {"name": n, "revenue": float(r or 0), "invoices_count": int(c or 0)}
            for n, r, c in client_rows
        ]

        # --- Modes de règlement ---------------------------------------------
        method_rows = db.query(
            func.coalesce(InvoicePayment.payment_method, "Non précisé").label("method"),
            func.coalesce(func.sum(InvoicePayment.amount), 0),
            func.count(InvoicePayment.payment_id)
        ).filter(
            func.date(InvoicePayment.payment_date) >= start,
            func.date(InvoicePayment.payment_date) <= end
        ).group_by("method").order_by(desc(func.sum(InvoicePayment.amount))).all()

        payment_methods = [
            {"method": m, "amount": float(a or 0), "count": int(c or 0)}
            for m, a, c in method_rows
        ]

        # --- Statuts de facture (annulées comprises : c'est un volume) -------
        status_rows = db.query(
            func.coalesce(Invoice.status, "en attente").label("status"),
            func.count(Invoice.invoice_id),
            func.coalesce(func.sum(Invoice.total), 0)
        ).filter(
            func.date(Invoice.date) >= start,
            func.date(Invoice.date) <= end
        ).group_by("status").all()

        invoice_status = [
            {"status": s, "count": int(c or 0), "total": float(t or 0)}
            for s, c, t in status_rows
        ]

        # --- Devis ------------------------------------------------------------
        quotations_created, quotations_total = db.query(
            func.count(Quotation.quotation_id),
            func.coalesce(func.sum(Quotation.total), 0)
        ).filter(
            func.date(Quotation.date) >= start,
            func.date(Quotation.date) <= end
        ).one()

        quotations_accepted, quotations_accepted_total = db.query(
            func.count(Quotation.quotation_id),
            func.coalesce(func.sum(Quotation.total), 0)
        ).filter(
            func.date(Quotation.date) >= start,
            func.date(Quotation.date) <= end,
            Quotation.status == "accepté"
        ).one()

        # --- Trésorerie --------------------------------------------------------
        def _bank(kind):
            return float(db.query(func.coalesce(func.sum(BankTransaction.amount), 0)).filter(
                func.date(BankTransaction.date) >= start,
                func.date(BankTransaction.date) <= end,
                BankTransaction.type == kind
            ).scalar() or 0)

        bank_in, bank_out = _bank("entry"), _bank("exit")

        purchases = float(db.query(func.coalesce(func.sum(DailyPurchase.amount), 0)).filter(
            DailyPurchase.date >= start,
            DailyPurchase.date <= end
        ).scalar() or 0)

        # --- Encours de la période -------------------------------------------
        # `invoiced - paid` serait faux : `paid` compte aussi les règlements de
        # factures antérieures à la tranche, et peut donc dépasser le facturé.
        # L'encours se calcule facture par facture, sur celles de la tranche.
        paid_per_invoice = db.query(
            InvoicePayment.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoicePayment.amount), 0).label("paid")
        ).group_by(InvoicePayment.invoice_id).subquery()

        outstanding = float(db.query(
            func.coalesce(func.sum(Invoice.total - func.coalesce(paid_per_invoice.c.paid, 0)), 0)
        ).select_from(Invoice).outerjoin(
            paid_per_invoice, paid_per_invoice.c.invoice_id == Invoice.invoice_id
        ).filter(*in_range).scalar() or 0)

        # --- Stock (état instantané, indépendant de la tranche) ---------------
        # On réutilise `/stock-summary` plutôt que de recalculer : la règle des
        # produits à variantes (la quantité disponible est le nombre de
        # variantes non vendues, pas `Product.quantity`) y est déjà écrite, et
        # la dupliquer ferait diverger les deux écrans.
        stock_summary = await get_stock_summary(current_user=current_user, db=db)
        stock_block = stock_summary.get("summary", {})

        return {
            "period": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "start_formatted": start.strftime("%d/%m/%Y"),
                "end_formatted": end.strftime("%d/%m/%Y"),
                "days_count": days_count,
                "is_single_day": days_count == 1,
            },
            "previous_period": {
                "start_formatted": prev_start.strftime("%d/%m/%Y"),
                "end_formatted": prev_end.strftime("%d/%m/%Y"),
            },
            "revenue": {
                "invoiced": invoiced,
                "paid": paid,
                "outstanding": outstanding,
                "invoices_count": invoices_count,
                "avg_ticket": (invoiced / invoices_count) if invoices_count else 0.0,
                "invoiced_per_day": (invoiced / days_count) if days_count else 0.0,
                "growth_invoiced": _growth(invoiced, prev_invoiced),
                "growth_paid": _growth(paid, prev_paid),
                "growth_invoices_count": _growth(invoices_count, prev_invoices_count),
                "previous_invoiced": prev_invoiced,
                "previous_paid": prev_paid,
            },
            "by_day": by_day,
            "by_category": by_category,
            "top_products": top_products,
            "top_clients": top_clients,
            "payment_methods": payment_methods,
            "invoice_status": invoice_status,
            "quotations": {
                "created": int(quotations_created or 0),
                "created_total": float(quotations_total or 0),
                "accepted": int(quotations_accepted or 0),
                "accepted_total": float(quotations_accepted_total or 0),
                "conversion_rate": (
                    round(int(quotations_accepted or 0) / int(quotations_created) * 100, 1)
                    if quotations_created else 0.0
                ),
            },
            "treasury": {
                "bank_in": bank_in,
                "bank_out": bank_out,
                "purchases": purchases,
                "net": paid + bank_in - bank_out - purchases,
            },
            "stock": {
                "products_count": int(stock_block.get("total_products") or 0),
                "with_stock": int(stock_block.get("products_with_stock") or 0),
                "out_of_stock": int(stock_block.get("products_out_of_stock") or 0),
                "stock_value": float(stock_block.get("total_stock_value") or 0),
                "purchase_cost": float(stock_block.get("total_purchase_cost") or 0),
                "potential_profit": float(stock_block.get("total_potential_profit") or 0),
                "margin": float(stock_block.get("profit_margin_percent") or 0),
            },
        }

    except Exception as e:
        logging.error(f"Erreur rapport de synthèse: {e}")
        raise HTTPException(status_code=500, detail=str(e))
