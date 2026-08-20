from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_, case
from typing import Optional
from datetime import datetime, date, timedelta
import logging

from ..database import (
    get_db, User, Invoice, InvoiceItem, InvoicePayment, 
    Quotation, Product, StockMovement, SupplierInvoice,
    SupplierInvoicePayment, BankTransaction, Client,
    DailyPurchase
)
from ..auth import get_current_user
from .dashboard import get_dashboard_stats
from .debts import get_debts_stats

router = APIRouter(prefix="/api/daily-recap", tags=["daily-recap"])

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


def _as_day(value) -> Optional[date]:
    """Ramène un `datetime` ou une `date` à une `date`, sinon `None`."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _fmt_day(value) -> str:
    """Date d'une ligne de tableau. Sur une tranche, la colonne « heure »
    seule ne suffit plus à situer un mouvement."""
    day = _as_day(value)
    return day.strftime("%d/%m/%Y") if day else ""


@router.get("/stats")
async def get_daily_recap_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    target_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Récapitulatif d'activité sur une tranche de dates.
    - Factures créées / paiements encaissés
    - Devis créés / acceptés
    - Mouvements de stock
    - Caisse (encaissements, banque, achats quotidiens)
    - Ventilation jour par jour sur la tranche

    `target_date` est conservé pour les appelants qui ne demandent qu'une
    journée (la tuile « Mes statistiques du jour » du tableau de bord) : il
    équivaut à `start_date == end_date`.
    """
    try:
        # --- Bornes de la tranche ---------------------------------------
        start = _parse_day(start_date)
        end = _parse_day(end_date)

        if start is None and end is None:
            single = _parse_day(target_date) or date.today()
            start = end = single
        else:
            # Une seule borne fournie : la tranche se réduit à ce jour.
            start = start or end
            end = end or start
            # Bornes inversées par l'appelant : on les remet dans l'ordre
            # plutôt que de renvoyer une tranche vide.
            if start > end:
                start, end = end, start

        # Le reste du calcul lit `recap_date` pour tout ce qui reste attaché à
        # un seul jour (libellé, compatibilité de la réponse).
        recap_date = end
        days_count = (end - start).days + 1

        # === FACTURES ===
        try:
            # Factures créées sur la tranche, avec client et paiements
            from sqlalchemy.orm import joinedload
            invoices_created = db.query(Invoice).options(
                joinedload(Invoice.client),
                joinedload(Invoice.payments)
            ).filter(
                func.date(Invoice.date) >= start,
                func.date(Invoice.date) <= end
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des factures: {e}")
            invoices_created = []
        
        try:
            # Paiements encaissés sur la tranche, avec leur facture
            payments_received = db.query(InvoicePayment).options(
                joinedload(InvoicePayment.invoice)
            ).filter(
                func.date(InvoicePayment.payment_date) >= start,
                func.date(InvoicePayment.payment_date) <= end
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des paiements: {e}")
            payments_received = []
        
        # === DEVIS ===
        try:
            # Devis créés sur la tranche, avec leur client
            quotations_created = db.query(Quotation).options(
                joinedload(Quotation.client)
            ).filter(
                func.date(Quotation.date) >= start,
                func.date(Quotation.date) <= end
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des devis créés: {e}")
            quotations_created = []
        
        try:
            # Devis acceptés sur la tranche, avec leur client
            quotations_accepted = db.query(Quotation).options(
                joinedload(Quotation.client)
            ).filter(
                func.date(Quotation.date) >= start,
                func.date(Quotation.date) <= end,
                Quotation.status == "accepté"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des devis acceptés: {e}")
            quotations_accepted = []
        
        # === MOUVEMENTS DE STOCK ===
        try:
            # Entrées de stock sur la tranche, avec leur produit
            stock_in = db.query(StockMovement).options(
                joinedload(StockMovement.product)
            ).filter(
                func.date(StockMovement.created_at) >= start,
                func.date(StockMovement.created_at) <= end,
                StockMovement.movement_type == "IN"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des entrées de stock: {e}")
            stock_in = []
        
        try:
            # Sorties de stock sur la tranche, avec leur produit
            stock_out = db.query(StockMovement).options(
                joinedload(StockMovement.product)
            ).filter(
                func.date(StockMovement.created_at) >= start,
                func.date(StockMovement.created_at) <= end,
                StockMovement.movement_type == "OUT"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des sorties de stock: {e}")
            stock_out = []
        
        # === TRANSACTIONS BANCAIRES ===
        try:
            # Entrées d'argent sur la tranche
            bank_entries = db.query(BankTransaction).filter(
                func.date(BankTransaction.date) >= start,
                func.date(BankTransaction.date) <= end,
                BankTransaction.type == "entry"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des entrées bancaires: {e}")
            bank_entries = []
        
        try:
            # Sorties d'argent sur la tranche
            bank_exits = db.query(BankTransaction).filter(
                func.date(BankTransaction.date) >= start,
                func.date(BankTransaction.date) <= end,
                BankTransaction.type == "exit"
            ).all()
        except Exception as e:
            logging.error(f"Erreur lors du chargement des sorties bancaires: {e}")
            bank_exits = []
        
        # === ACHATS QUOTIDIENS ===
        try:
            daily_purchases = db.query(DailyPurchase).filter(
                ((DailyPurchase.date >= start) & (DailyPurchase.date <= end))
                | ((func.date(DailyPurchase.created_at) >= start) & (func.date(DailyPurchase.created_at) <= end))
            ).all()
        except Exception as e:
            logging.error(f"Erreur chargement achats quotidiens: {e}")
            daily_purchases = []

        total_daily_purchases = sum(float(p.amount or 0) for p in daily_purchases)
        
        # === STATISTIQUES PAR UTILISATEUR ===
        # Factures créées par l'utilisateur connecté
        try:
            user_invoices = [inv for inv in invoices_created if inv.created_by == current_user.user_id]
            user_invoices_total = sum(float(inv.total or 0) for inv in user_invoices)
        except Exception as e:
            logging.error(f"Erreur calcul factures utilisateur: {e}")
            user_invoices = []
            user_invoices_total = 0
        
        # Devis créés par l'utilisateur connecté
        try:
            user_quotations = [q for q in quotations_created if q.created_by == current_user.user_id]
            user_quotations_total = sum(float(q.total or 0) for q in user_quotations)
        except Exception as e:
            logging.error(f"Erreur calcul devis utilisateur: {e}")
            user_quotations = []
            user_quotations_total = 0
        
        # Achats quotidiens créés par l'utilisateur connecté
        try:
            user_purchases = [dp for dp in daily_purchases if dp.created_by == current_user.user_id]
            user_purchases_total = sum(float(dp.amount or 0) for dp in user_purchases)
        except Exception as e:
            logging.error(f"Erreur calcul achats utilisateur: {e}")
            user_purchases = []
            user_purchases_total = 0
        
        # Paiements reçus par l'utilisateur (via les factures qu'il a créées)
        try:
            # IMPORTANT: On veut les paiements du jour sur les factures de l'utilisateur,
            # même si ces factures ont été créées un autre jour.
            user_payments = [
                p for p in payments_received
                if p.invoice and getattr(p.invoice, 'created_by', None) == current_user.user_id
            ]
            user_payments_total = sum(float(p.amount or 0) for p in user_payments)
        except Exception as e:
            logging.error(f"Erreur calcul paiements utilisateur: {e}")
            user_payments = []
            user_payments_total = 0
        
        # Répartition par catégorie
        try:
            by_cat_rows = (
                db.query(DailyPurchase.category, func.coalesce(func.sum(DailyPurchase.amount), 0))
                .filter(((DailyPurchase.date >= start) & (DailyPurchase.date <= end))
                | ((func.date(DailyPurchase.created_at) >= start) & (func.date(DailyPurchase.created_at) <= end)))
                .group_by(DailyPurchase.category)
                .all()
            )
        except Exception:
            by_cat_rows = []
        by_category = [
            {"category": (c or ""), "amount": float(a or 0)}
            for c, a in by_cat_rows
        ]

        # === CALCULS FINANCIERS ===
        # Total des paiements reçus
        total_payments = sum(float(p.amount or 0) for p in payments_received)
        
        # Total des entrées bancaires
        total_bank_entries = sum(float(t.amount or 0) for t in bank_entries)
        
        # Total des sorties bancaires  
        total_bank_exits = sum(float(t.amount or 0) for t in bank_exits)
        
        # Solde du jour (déduction des Achats quotidiens)
        daily_balance = total_payments + total_bank_entries - total_bank_exits - total_daily_purchases
        
        # Chiffre d'affaires facturé (pour info)
        potential_revenue = sum(float(inv.total or 0) for inv in invoices_created)
        # Chiffre d'affaires encaissé net (aligné avec la caisse): paiements reçus - achats quotidiens
        net_revenue = float(total_payments) - float(total_daily_purchases)
        
        # Bénéfice externe du jour (somme des external_profit des items des factures créées ce jour)
        try:
            from app.database import InvoiceItem
            invoice_ids_created = [inv.invoice_id for inv in invoices_created]
            daily_external_profit = 0
            if invoice_ids_created:
                daily_external_profit = db.query(func.coalesce(func.sum(InvoiceItem.external_profit), 0)).filter(
                    InvoiceItem.invoice_id.in_(invoice_ids_created),
                    InvoiceItem.external_profit.isnot(None)
                ).scalar() or 0
        except Exception as e:
            logging.error(f"Erreur calcul bénéfice externe: {e}")
            daily_external_profit = 0
        
        # Préparer un mapping facture_id -> numéro pour les mouvements de stock liés à une facture
        try:
            invoice_ids_from_stock = {
                int(s.reference_id)
                for s in list(stock_in or []) + list(stock_out or [])
                if getattr(s, "reference_type", None) == "INVOICE" and getattr(s, "reference_id", None)
            }
            invoices_map = {}
            if invoice_ids_from_stock:
                rows = db.query(Invoice.invoice_id, Invoice.invoice_number).filter(
                    Invoice.invoice_id.in_(invoice_ids_from_stock)
                ).all()
                invoices_map = {int(i): num for i, num in rows}
        except Exception as e:
            logging.error(f"Erreur lors de la préparation du mapping factures pour les mouvements de stock: {e}")
            invoices_map = {}

        # === STATS COMPLÉMENTAIRES (DETTES, DASHBOARD) ===
        # On réutilise les endpoints internes pour ne pas dupliquer la logique métier.
        try:
            dashboard_stats = await get_dashboard_stats(
                force_refresh=False,
                db=db,
                current_user=current_user
            )
        except Exception as e:
            logging.error(f"Erreur lors du chargement des stats dashboard dans daily_recap: {e}")
            dashboard_stats = None

        try:
            debts_stats = await get_debts_stats(
                current_user=current_user,
                db=db
            )
        except Exception as e:
            logging.error(f"Erreur lors du chargement des stats dettes dans daily_recap: {e}")
            debts_stats = None

        # === VENTILATION JOUR PAR JOUR ===
        # Calculée en Python à partir des listes déjà chargées : une tranche
        # d'un mois ferait sinon une trentaine d'allers-retours en base pour
        # des données qui sont déjà en mémoire.
        by_day_index = {}
        cursor = start
        while cursor <= end:
            by_day_index[cursor] = {
                "date": cursor.isoformat(),
                "label": cursor.strftime("%d/%m"),
                "payments": 0.0,
                "bank_entries": 0.0,
                "bank_exits": 0.0,
                "purchases": 0.0,
                "invoiced": 0.0,
                "invoices_count": 0,
            }
            cursor += timedelta(days=1)

        def _bucket(value):
            """Le seau du jour, ou `None` si l'enregistrement tombe hors
            tranche (dates limites, fuseaux)."""
            day = _as_day(value)
            return by_day_index.get(day) if day else None

        for p in payments_received:
            b = _bucket(p.payment_date)
            if b:
                b["payments"] += float(p.amount or 0)

        for t in bank_entries:
            b = _bucket(t.date)
            if b:
                b["bank_entries"] += float(t.amount or 0)

        for t in bank_exits:
            b = _bucket(t.date)
            if b:
                b["bank_exits"] += float(t.amount or 0)

        for dp in daily_purchases:
            b = _bucket(getattr(dp, "date", None) or getattr(dp, "created_at", None))
            if b:
                b["purchases"] += float(dp.amount or 0)

        for inv in invoices_created:
            b = _bucket(inv.date or inv.created_at)
            if b:
                b["invoiced"] += float(inv.total or 0)
                b["invoices_count"] += 1

        by_day = []
        for day in sorted(by_day_index):
            row = by_day_index[day]
            row["balance"] = (
                row["payments"] + row["bank_entries"] - row["bank_exits"] - row["purchases"]
            )
            by_day.append(row)

        # Jour le plus fort de la tranche, pour l'en-tête.
        best_day = max(by_day, key=lambda r: r["payments"]) if by_day else None

        # === PRÉPARATION DES DONNÉES ===
        return {
            # `date` reste la borne haute : les appelants d'une seule journée
            # (tuile du tableau de bord) continuent d'y lire leur date.
            "date": recap_date.isoformat(),
            "date_formatted": recap_date.strftime("%d/%m/%Y"),

            # Tranche demandée
            "period": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "start_formatted": start.strftime("%d/%m/%Y"),
                "end_formatted": end.strftime("%d/%m/%Y"),
                "days_count": days_count,
                "is_single_day": days_count == 1,
            },

            # Ventilation quotidienne et moyennes
            "by_day": by_day,
            "averages": {
                "payments_per_day": (total_payments / days_count) if days_count else 0.0,
                "purchases_per_day": (float(total_daily_purchases) / days_count) if days_count else 0.0,
                "invoiced_per_day": (potential_revenue / days_count) if days_count else 0.0,
                "invoices_per_day": (len(invoices_created) / days_count) if days_count else 0.0,
                "best_day": best_day["date"] if best_day else None,
                "best_day_label": best_day["label"] if best_day else None,
                "best_day_payments": best_day["payments"] if best_day else 0.0,
            },

            # Factures
            "invoices": {
                "created_count": len(invoices_created),
                "created_total": potential_revenue,
                "created_list": [
                    (lambda inv: {
                        "id": inv.invoice_id,
                        "number": inv.invoice_number,
                        "client_name": (inv.client.name if inv.client else ("Vente Flash" if getattr(inv, 'invoice_type', None) == 'flash_sale' else "Client inconnu")),
                        "total": float(inv.total or 0),
                        # Statut recalculé selon paiements cumulés (évite états obsolètes)
                        "status": (
                            "payée" if sum(float(p.amount or 0) for p in (inv.payments or [])) >= float(inv.total or 0) else
                            ("partiellement payée" if sum(float(p.amount or 0) for p in (inv.payments or [])) > 0 else "en attente")
                        ),
                        "date": _fmt_day(inv.date or inv.created_at),
                        "time": inv.created_at.strftime("%H:%M") if inv.created_at else ""
                    })(inv)
                    for inv in invoices_created
                ]
            },
            
            # Paiements
            "payments": {
                "count": len(payments_received),
                "total": total_payments,
                "list": [
                    {
                        "id": p.payment_id,
                        "invoice_id": (p.invoice.invoice_id if p.invoice else None),
                        "invoice_number": p.invoice.invoice_number if p.invoice else f"Paiement #{p.payment_id}",
                        "amount": float(p.amount or 0),
                        "method": p.payment_method,
                        "date": _fmt_day(p.payment_date),
                        "time": p.payment_date.strftime("%H:%M") if p.payment_date else ""
                    }
                    for p in payments_received
                ]
            },
            
            # Devis
            "quotations": {
                "created_count": len(quotations_created),
                "accepted_count": len(quotations_accepted),
                "created_total": sum(float(q.total or 0) for q in quotations_created),
                "accepted_total": sum(float(q.total or 0) for q in quotations_accepted),
                "created_list": [
                    {
                        "id": q.quotation_id,
                        "number": q.quotation_number,
                        "client_name": (q.client.name if q.client else "Client inconnu"),
                        "total": float(q.total or 0),
                        "status": q.status,
                        "date": _fmt_day(q.date or q.created_at),
                        "time": q.created_at.strftime("%H:%M") if q.created_at else ""
                    }
                    for q in quotations_created
                ],
                "accepted_list": [
                    {
                        "id": q.quotation_id,
                        "number": q.quotation_number,
                        "client_name": (q.client.name if q.client else "Client inconnu"),
                        "total": float(q.total or 0),
                        "date": _fmt_day(q.date or q.created_at),
                        "time": q.created_at.strftime("%H:%M") if q.created_at else ""
                    }
                    for q in quotations_accepted
                ]
            },
            
            # Stock
            "stock": {
                "entries_count": len(stock_in),
                "exits_count": len(stock_out),
                "entries_quantity": sum(s.quantity for s in stock_in),
                "exits_quantity": sum(s.quantity for s in stock_out),
                "entries_list": [
                    {
                        "id": s.movement_id,
                        "product_name": s.product.name if s.product else "Produit inconnu",
                        "quantity": s.quantity,
                        "reference": s.reference_type,
                        "reference_id": s.reference_id,
                        "invoice_id": int(s.reference_id) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "invoice_number": invoices_map.get(int(s.reference_id)) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "notes": s.notes,
                        "date": _fmt_day(s.created_at),
                        "time": s.created_at.strftime("%H:%M") if s.created_at else ""
                    }
                    for s in stock_in
                ],
                "exits_list": [
                    {
                        "id": s.movement_id,
                        "product_name": s.product.name if s.product else "Produit inconnu",
                        "quantity": s.quantity,
                        "reference": s.reference_type,
                        "reference_id": s.reference_id,
                        "invoice_id": int(s.reference_id) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "invoice_number": invoices_map.get(int(s.reference_id)) if s.reference_type == "INVOICE" and s.reference_id else None,
                        "notes": s.notes,
                        "date": _fmt_day(s.created_at),
                        "time": s.created_at.strftime("%H:%M") if s.created_at else ""
                    }
                    for s in stock_out
                ]
            },
            
            # Finances (Caisse)
            "finances": {
                "payments_received": total_payments,
                "bank_entries": total_bank_entries,
                "bank_exits": total_bank_exits,
                "daily_purchases_total": float(total_daily_purchases),
                "daily_balance": daily_balance,
                "potential_revenue": potential_revenue,
                "net_revenue": net_revenue,
                "external_profit": float(daily_external_profit),
                "bank_entries_list": [
                    {
                        "id": t.id,
                        "motif": t.motif,
                        "description": t.description,
                        "amount": float(t.amount or 0),
                        "method": t.method,
                        "reference": t.reference,
                        "date": _fmt_day(t.date)
                    }
                    for t in bank_entries
                ],
                "bank_exits_list": [
                    {
                        "id": t.id,
                        "motif": t.motif,
                        "description": t.description,
                        "amount": float(t.amount or 0),
                        "method": t.method,
                        "reference": t.reference,
                        "date": _fmt_day(t.date)
                    }
                    for t in bank_exits
                ]
            },
            # Achats quotidiens
            "daily_purchases": {
                "count": len(daily_purchases),
                "total": float(total_daily_purchases),
                "by_category": by_category,
                "list": [
                    {
                        "id": dp.id,
                        "date": _fmt_day(getattr(dp, "date", None) or getattr(dp, "created_at", None)),
                        "time": (dp.created_at.strftime("%H:%M") if getattr(dp, 'created_at', None) else ""),
                        "category": dp.category,
                        "description": dp.description,
                        "amount": float(dp.amount or 0),
                        "method": dp.payment_method,
                        "reference": dp.reference,
                    }
                    for dp in daily_purchases
                ]
            },
            # Dettes (clients et fournisseurs)
            "debts": debts_stats or {},
            # Stats avancées / Dashboard global
            "dashboard": dashboard_stats or {},
            
            # Statistiques de l'utilisateur connecté
            "user_stats": {
                "user_id": current_user.user_id,
                "username": current_user.username,
                "invoices": {
                    "count": len(user_invoices),
                    "total": user_invoices_total,
                    "list": [
                        {
                            "id": inv.invoice_id,
                            "number": inv.invoice_number,
                            "client_name": (inv.client.name if inv.client else ("Vente Flash" if getattr(inv, 'invoice_type', None) == 'flash_sale' else "Client inconnu")),
                            "total": float(inv.total or 0),
                            "status": (
                                "payée" if sum(float(p.amount or 0) for p in (inv.payments or [])) >= float(inv.total or 0) else
                                ("partiellement payée" if sum(float(p.amount or 0) for p in (inv.payments or [])) > 0 else "en attente")
                            ),
                            "date": _fmt_day(inv.date or inv.created_at),
                        "time": inv.created_at.strftime("%H:%M") if inv.created_at else ""
                        }
                        for inv in user_invoices
                    ]
                },
                "quotations": {
                    "count": len(user_quotations),
                    "total": user_quotations_total,
                    "list": [
                        {
                            "id": q.quotation_id,
                            "number": q.quotation_number,
                            "client_name": (q.client.name if q.client else "Client inconnu"),
                            "total": float(q.total or 0),
                            "status": q.status,
                            "date": _fmt_day(q.date or q.created_at),
                        "time": q.created_at.strftime("%H:%M") if q.created_at else ""
                        }
                        for q in user_quotations
                    ]
                },
                "payments": {
                    "count": len(user_payments),
                    "total": user_payments_total
                },
                "daily_purchases": {
                    "count": len(user_purchases),
                    "total": user_purchases_total,
                    "list": [
                        {
                            "id": dp.id,
                            "date": _fmt_day(getattr(dp, "date", None) or getattr(dp, "created_at", None)),
                        "time": (dp.created_at.strftime("%H:%M") if getattr(dp, 'created_at', None) else ""),
                            "category": dp.category,
                            "description": dp.description,
                            "amount": float(dp.amount or 0),
                            "method": dp.payment_method,
                        }
                        for dp in user_purchases
                    ]
                },
                "net_balance": user_payments_total - user_purchases_total
            }
        }
        
    except Exception as e:
        logging.error(f"Erreur daily recap stats: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du récap quotidien: {str(e)}")

@router.get("/period-summary")
async def get_period_summary(
    start_date: str,
    end_date: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Résumé sur une période donnée pour comparaison
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        
        # Paiements reçus sur la période
        total_payments = db.query(func.coalesce(func.sum(InvoicePayment.amount), 0)).filter(
            func.date(InvoicePayment.payment_date) >= start,
            func.date(InvoicePayment.payment_date) <= end
        ).scalar() or 0
        
        # Factures créées sur la période
        invoices_count = db.query(func.count(Invoice.invoice_id)).filter(
            func.date(Invoice.created_at) >= start,
            func.date(Invoice.created_at) <= end
        ).scalar() or 0
        
        # Devis créés sur la période
        quotations_count = db.query(func.count(Quotation.quotation_id)).filter(
            func.date(Quotation.created_at) >= start,
            func.date(Quotation.created_at) <= end
        ).scalar() or 0
        
        return {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days_count": (end - start).days + 1,
            "total_payments": float(total_payments),
            "invoices_created": invoices_count,
            "quotations_created": quotations_count,
            "average_daily_payment": float(total_payments) / ((end - start).days + 1) if (end - start).days >= 0 else 0
        }
        
    except Exception as e:
        logging.error(f"Erreur period summary: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors du calcul du résumé de période: {str(e)}")
