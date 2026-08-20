#!/usr/bin/env python
"""Rattache les lignes de facture existantes à l'exemplaire vendu (IMEI).

Remplit invoice_items.variant_id / variant_imei pour l'historique, à partir des
sources disponibles, de la plus fiable à la moins fiable :

  1. daily_sales   : la vente porte déjà variant_id + variant_imei ;
  2. __SERIALS__   : le bloc de métadonnées stocké dans invoices.notes ;
  3. "(IMEI: ...)" : ancien format où l'IMEI était collé au libellé de la ligne.

Une variante n'est jamais attribuée à deux lignes différentes. Les lignes qu'on
ne sait pas rattacher sont laissées vides et listées en fin de rapport : le
routeur retombe alors sur son rattrapage historique.

Usage :
    python scripts/backfill_invoice_item_variants.py [--apply]

Sans --apply, le script n'écrit rien et affiche seulement ce qu'il ferait.
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402

from app.database import (  # noqa: E402
    SessionLocal,
    DailySale,
    Invoice,
    InvoiceItem,
    ProductVariant,
)


def parse_serials(notes):
    """Extrait {product_id: [imei, ...]} du bloc __SERIALS__ des notes."""
    out = defaultdict(list)
    txt = str(notes or "")
    if "__SERIALS__=" not in txt:
        return out
    sub = txt.split("__SERIALS__=", 1)[1]
    cut = sub.find("\n__")
    if cut != -1:
        sub = sub[:cut]
    try:
        entries = json.loads(sub.strip())
    except Exception:
        return out
    for entry in entries or []:
        pid = entry.get("product_id")
        if pid is None:
            continue
        out[int(pid)].extend(entry.get("imeis") or [])
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="écrire réellement en base (sinon simulation)")
    args = parser.parse_args()

    db = SessionLocal()
    stats = defaultdict(int)
    unresolved = []

    try:
        invoices = db.query(Invoice).order_by(Invoice.invoice_id).all()
        print(f"{len(invoices)} factures à examiner\n")

        for invoice in invoices:
            items = [it for it in (invoice.items or []) if it.product_id is not None]
            if not items:
                continue

            # Variantes déjà consommées par une autre ligne, toutes factures
            # confondues : on ne rattache jamais deux fois le même exemplaire.
            taken = set()

            serials = parse_serials(invoice.notes)
            sales_by_product = defaultdict(list)
            for sale in db.query(DailySale).filter(DailySale.invoice_id == invoice.invoice_id).all():
                if sale.product_id is not None and (sale.variant_id or sale.variant_imei):
                    sales_by_product[int(sale.product_id)].append(sale)

            for item in items:
                if item.variant_id or item.variant_imei:
                    stats["deja_rattachees"] += 1
                    continue

                pid = int(item.product_id)
                variant = None
                source = None
                orphan_imei = None  # IMEI connu mais dont la variante a disparu

                # 1) daily_sales
                for sale in sales_by_product.get(pid, []):
                    candidate = None
                    if sale.variant_id and sale.variant_id not in taken:
                        candidate = db.query(ProductVariant).filter(
                            ProductVariant.variant_id == sale.variant_id).first()
                    elif sale.variant_imei:
                        candidate = db.query(ProductVariant).filter(
                            func.trim(ProductVariant.imei_serial) == str(sale.variant_imei).strip()).first()
                    if candidate is not None and candidate.variant_id not in taken:
                        variant, source = candidate, "daily_sales"
                        sales_by_product[pid].remove(sale)
                        break
                    if candidate is None and sale.variant_imei and orphan_imei is None:
                        orphan_imei = str(sale.variant_imei).strip()

                # 2) bloc __SERIALS__ des notes
                if variant is None:
                    pending = serials.get(pid) or []
                    while pending and variant is None:
                        imei = str(pending.pop(0)).strip()
                        if not imei:
                            continue
                        candidate = db.query(ProductVariant).filter(
                            func.trim(ProductVariant.imei_serial) == imei).first()
                        if candidate is not None and candidate.variant_id not in taken:
                            variant, source = candidate, "notes"
                        elif candidate is None and orphan_imei is None:
                            orphan_imei = imei

                # 3) IMEI dans le libellé de la ligne
                if variant is None:
                    m = re.search(r"\(IMEI:\s*([^)]+)\)", item.product_name or "", flags=re.I)
                    if m:
                        imei = (m.group(1) or "").strip()
                        if imei:
                            candidate = db.query(ProductVariant).filter(
                                func.trim(ProductVariant.imei_serial) == imei).first()
                            if candidate is not None and candidate.variant_id not in taken:
                                variant, source = candidate, "libelle"
                            elif candidate is None and orphan_imei is None:
                                orphan_imei = imei

                # L'IMEI est connu mais la variante a été supprimée de la base:
                # on garde la trace textuelle, sans rattachement possible.
                if variant is None and orphan_imei:
                    stats["imei_sans_variante"] += 1
                    if args.apply:
                        item.variant_imei = orphan_imei
                    continue

                if variant is None:
                    # Produit sans variantes du tout : normal, rien à rattacher.
                    has_variants = db.query(ProductVariant.variant_id).filter(
                        ProductVariant.product_id == pid).first() is not None
                    if has_variants:
                        stats["non_rattachees"] += 1
                        unresolved.append((invoice.invoice_number, item.item_id, item.product_name))
                    else:
                        stats["produit_sans_variante"] += 1
                    continue

                taken.add(variant.variant_id)
                stats[f"rattachees_{source}"] += 1
                if args.apply:
                    item.variant_id = variant.variant_id
                    item.variant_imei = variant.imei_serial

        if args.apply:
            db.commit()
            print(">>> Modifications enregistrées\n")
        else:
            db.rollback()
            print(">>> SIMULATION (relancer avec --apply pour écrire)\n")

        for key in sorted(stats):
            print(f"  {key:28} {stats[key]}")

        if unresolved:
            print(f"\n  {len(unresolved)} ligne(s) non rattachée(s) "
                  f"(produit à variantes, aucune source exploitable) :")
            for number, item_id, name in unresolved[:40]:
                print(f"    - {number} ligne {item_id} : {name}")
            if len(unresolved) > 40:
                print(f"    ... et {len(unresolved) - 40} autres")
    finally:
        db.close()


if __name__ == "__main__":
    main()
