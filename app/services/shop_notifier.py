"""
Alerte l'équipe quand une commande arrive sur la boutique.

Sans ça, une commande atterrit en base et reste invisible tant que personne
n'ouvre la page: c'est une vente perdue en silence.

On réutilise l'instance Evolution déjà en service pour les relances de créances,
plutôt que d'ajouter un canal supplémentaire à maintenir.
"""

import logging
import os
import re
from typing import Optional

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE_NAME", "stock")


def _normalize_number(raw: str) -> Optional[str]:
    """Numéro au format international sans séparateurs. Sénégal par défaut."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    # Numéro local sénégalais (9 chiffres commençant par 7)
    if len(digits) == 9 and digits.startswith("7"):
        digits = "221" + digits
    return digits if len(digits) >= 8 else None


def _format_amount(value) -> str:
    try:
        return f"{int(float(value)):,}".replace(",", " ") + " FCFA"
    except (TypeError, ValueError):
        return "—"


def build_order_message(order, site_url: str = "https://localhost") -> str:
    """Message court et actionnable: l'essentiel se lit sans ouvrir l'app."""
    lines = [
        "🛒 *NOUVELLE COMMANDE — Stock*",
        "",
        f"*N°* {order.order_number}",
        f"*Client* {order.customer_name}",
        f"*Téléphone* {order.customer_phone}",
    ]
    if order.delivery_city:
        lines.append(f"*Ville* {order.delivery_city}")
    if order.delivery_address:
        lines.append(f"*Adresse* {order.delivery_address}")

    lines += ["", "*Articles*"]
    for item in (order.items or []):
        suffix = " (sur commande)" if item.availability_at_order == "sur commande" else ""
        lines.append(f"• {item.quantity} × {item.product_name}{suffix}")

    lines += [
        "",
        f"*Total* {_format_amount(order.total)}",
    ]
    if order.payment_method:
        lines.append(f"*Paiement* {order.payment_method}")
    if order.notes:
        lines.append(f"*Note client* {order.notes}")

    lines += ["", f"👉 {site_url}/boutique/commandes"]
    return "\n".join(lines)


async def send_whatsapp(number: str, message: str) -> bool:
    target = _normalize_number(number)
    if not target:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": target, "text": message},
            )
        if response.status_code >= 400:
            logger.warning("Notification boutique refusée (%s): %s", response.status_code, response.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("Notification boutique impossible: %s", e)
        return False


async def notify_new_order(db: Session, order) -> dict:
    """
    Prévient les numéros configurés. Ne lève jamais: une notification qui échoue
    ne doit pas faire échouer la commande du client.
    """
    from ..routers.shop import get_setting

    if (get_setting(db, "notify_on_order", "1") or "1").strip() not in ("1", "true", "True"):
        return {"sent": 0, "skipped": "désactivé"}

    raw = get_setting(db, "notify_numbers", "") or ""
    numbers = [n.strip() for n in re.split(r"[;,\n]", raw) if n.strip()]
    if not numbers:
        # Repli sur le WhatsApp public de la boutique si aucun destinataire dédié.
        fallback = (get_setting(db, "shop_whatsapp", "") or "").strip()
        if fallback:
            numbers = [fallback]

    if not numbers:
        return {"sent": 0, "skipped": "aucun destinataire configuré"}

    message = build_order_message(order)
    sent = 0
    for number in numbers[:5]:
        if await send_whatsapp(number, message):
            sent += 1

    return {"sent": sent, "targets": len(numbers)}


def build_demand_message(demand, site_url: str = "https://localhost") -> str:
    """Message d'alerte à la réception d'une demande produit (réf DA-)."""
    lines = [
        "🔔 Nouvelle demande produit",
        f"Réf : {demand.demand_number}",
        f"Produit : {demand.quantity} × {demand.product_name}",
        f"Client : {demand.customer_name or ''} — {demand.customer_phone or ''}",
    ]
    if demand.notes:
        lines.append(f"Note : {demand.notes}")
    return "\n".join(lines)


async def notify_new_demand(db: Session, demand) -> dict:
    """Prévient les numéros configurés d'une nouvelle demande. Ne lève jamais."""
    from ..routers.shop import get_setting

    if (get_setting(db, "notify_on_order", "1") or "1").strip() not in ("1", "true", "True"):
        return {"sent": 0, "skipped": "désactivé"}

    raw = get_setting(db, "notify_numbers", "") or ""
    numbers = [n.strip() for n in re.split(r"[;,\n]", raw) if n.strip()]
    if not numbers:
        fallback = (get_setting(db, "shop_whatsapp", "") or "").strip()
        if fallback:
            numbers = [fallback]
    if not numbers:
        return {"sent": 0, "skipped": "aucun destinataire configuré"}

    message = build_demand_message(demand)
    sent = 0
    for number in numbers[:5]:
        if await send_whatsapp(number, message):
            sent += 1
    return {"sent": sent, "targets": len(numbers)}
