"""
Récapitulatif financier d'un arrivage.

Deux natures de produits, deux méthodes :

- Variantes à IMEI (`ProductVariant.arrival_id`) : chaque unité est identifiable,
  donc le chiffre d'affaires est EXACT — on lit les ventes dans `DailySale`
  (jointes par `variant_id`).

- Produits sans variante (`ArrivalItem`) : les unités sont fongibles (coques,
  câbles…). On ne sait pas de quel lot sort une unité vendue : on applique donc
  du FIFO (premier arrivé, premier vendu) sur l'historique du produit. Le CA de la
  part vendue est valorisé au prix de vente moyen réel du produit.
"""
from decimal import Decimal
from datetime import date

from sqlalchemy import func

from ..database import (
    Product, ProductVariant, Arrival, ArrivalItem, DailySale,
)


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------
# FIFO pour les produits sans variante
# --------------------------------------------------------------------------

def _fifo_sold_by_item(db, product_id: int) -> dict:
    """Répartit les ventes d'un produit fongible sur ses lignes d'arrivage, en FIFO.

    Renvoie {arrival_item_id: unités vendues attribuées à cette ligne}.
    Le « stock initial » (ce qui existait avant tout arrivage) absorbe les
    premières ventes ; il n'apparaît pas dans le résultat.
    """
    items = (
        db.query(ArrivalItem)
        .join(Arrival, Arrival.arrival_id == ArrivalItem.arrival_id)
        .filter(ArrivalItem.product_id == product_id)
        .all()
    )
    if not items:
        return {}

    product = db.query(Product).filter(Product.product_id == product_id).first()
    current_qty = int(product.quantity or 0) if product else 0

    # Ventes fongibles = ventes du produit sans variante identifiée.
    total_sold = int(
        db.query(func.coalesce(func.sum(DailySale.quantity), 0))
        .filter(DailySale.product_id == product_id, DailySale.variant_id.is_(None))
        .scalar() or 0
    )
    total_arrivals = sum(int(it.quantity or 0) for it in items)

    # Ce qui était là avant le premier arrivage : reconstitué à partir de l'état
    # actuel (stock + déjà vendu − total reçu par arrivages).
    initial_stock = max(0, current_qty + total_sold - total_arrivals)

    # Entrées ordonnées : stock initial d'abord, puis chaque arrivage par date.
    entries = [(None, initial_stock, date.min)]
    for it in items:
        arr_date = it.arrival.arrival_date if it.arrival else date.max
        entries.append((it.arrival_item_id, int(it.quantity or 0), arr_date))
    entries.sort(key=lambda e: (e[2], e[0] or 0))

    remaining_sold = total_sold
    result = {}
    for item_id, qty, _ in entries:
        take = min(qty, remaining_sold)
        remaining_sold -= take
        if item_id is not None:
            result[item_id] = take
    return result


def _avg_sale_price(db, product_id: int) -> float:
    """Prix de vente unitaire moyen réel d'un produit fongible (depuis DailySale)."""
    row = (
        db.query(
            func.coalesce(func.sum(DailySale.total_amount), 0),
            func.coalesce(func.sum(DailySale.quantity), 0),
        )
        .filter(DailySale.product_id == product_id, DailySale.variant_id.is_(None))
        .first()
    )
    total_amount, qty = _f(row[0]), int(row[1] or 0)
    return (total_amount / qty) if qty > 0 else 0.0


# --------------------------------------------------------------------------
# Récapitulatif complet
# --------------------------------------------------------------------------

def compute_arrival_financials(db, arrival: Arrival) -> dict:
    """Récap financier d'un arrivage : CA, coût, bénéfice, unités reçues / vendues
    / restantes, et le détail par produit."""
    products = []
    revenue = cost = 0.0
    units_received = units_sold = units_remaining = 0
    remaining_value_cost = 0.0

    # --- 1. Variantes à IMEI rattachées à l'arrivage -----------------------
    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.arrival_id == arrival.arrival_id)
        .all()
    )
    by_product = {}
    for v in variants:
        prod = v.product
        unit_cost = _f(v.purchase_price if v.purchase_price is not None
                       else (prod.purchase_price if prod else 0))

        sold_row = (
            db.query(
                func.coalesce(func.sum(DailySale.total_amount), 0),
                func.coalesce(func.sum(DailySale.quantity), 0),
            )
            .filter(DailySale.variant_id == v.variant_id)
            .first()
        )
        v_revenue = _f(sold_row[0])
        v_sold = int(sold_row[1] or 0)
        # Restant : mode « quantité » suit v.quantity, mode « unitaire » suit is_sold.
        if v.quantity is not None:
            v_remaining = max(0, int(v.quantity or 0))
        else:
            v_remaining = 0 if v.is_sold else 1
        v_received = v_sold + v_remaining

        agg = by_product.setdefault(prod.product_id if prod else v.product_id, {
            "product_id": prod.product_id if prod else v.product_id,
            "name": prod.name if prod else "Produit supprimé",
            "kind": "variant",
            "received": 0, "sold": 0, "remaining": 0,
            "revenue": 0.0, "cost": 0.0, "profit": 0.0,
        })
        agg["received"] += v_received
        agg["sold"] += v_sold
        agg["remaining"] += v_remaining
        agg["revenue"] += v_revenue
        agg["cost"] += v_sold * unit_cost
        agg["profit"] = agg["revenue"] - agg["cost"]

        revenue += v_revenue
        cost += v_sold * unit_cost
        units_received += v_received
        units_sold += v_sold
        units_remaining += v_remaining
        remaining_value_cost += v_remaining * unit_cost

    products.extend(by_product.values())

    # --- 2. Produits sans variante (FIFO) ----------------------------------
    for item in arrival.items:
        prod = item.product
        received = int(item.quantity or 0)
        sold_map = _fifo_sold_by_item(db, item.product_id)
        sold = int(sold_map.get(item.arrival_item_id, 0))
        remaining = max(0, received - sold)

        unit_cost = _f(item.purchase_price if item.purchase_price is not None
                       else (prod.purchase_price if prod else 0))
        avg_price = _avg_sale_price(db, item.product_id)

        i_revenue = sold * avg_price
        i_cost = sold * unit_cost

        products.append({
            "product_id": item.product_id,
            "name": prod.name if prod else "Produit supprimé",
            "kind": "quantity",
            "received": received,
            "sold": sold,
            "remaining": remaining,
            "revenue": i_revenue,
            "cost": i_cost,
            "profit": i_revenue - i_cost,
        })

        revenue += i_revenue
        cost += i_cost
        units_received += received
        units_sold += sold
        units_remaining += remaining
        remaining_value_cost += remaining * unit_cost

    return {
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "profit": round(revenue - cost, 2),
        "units_received": units_received,
        "units_sold": units_sold,
        "units_remaining": units_remaining,
        "remaining_value_cost": round(remaining_value_cost, 2),
        "products": products,
    }
