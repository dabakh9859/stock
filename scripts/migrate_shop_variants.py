"""Crée les tables des variantes commerciales de la boutique.

Sans effet si elles existent déjà : le script est rejouable.

    PYTHONPATH=. venv/bin/python scripts/migrate_shop_variants.py            # simulation
    PYTHONPATH=. venv/bin/python scripts/migrate_shop_variants.py --appliquer

Aucune table existante n'est modifiée, à une exception près : une colonne
`variant_summary` est ajoutée à `shop_order_items` pour figer le choix du
client au moment de sa commande. L'ajout d'une colonne nullable ne réécrit pas
les lignes déjà présentes.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, inspect, text

from app.database import (
    Base,
    _normalize_db_url,
    _RAW_DATABASE_URL,
    ShopVariantGroup,
    ShopVariantOption,
    ShopVariantAssignment,
    ShopVariantOverride,
)

TABLES = [
    ShopVariantGroup.__table__,
    ShopVariantOption.__table__,
    ShopVariantAssignment.__table__,
    ShopVariantOverride.__table__,
]


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--appliquer", action="store_true", help="écrit réellement")
    options = parseur.parse_args()
    simulation = not options.appliquer

    url = _normalize_db_url(_RAW_DATABASE_URL)
    print(f"Base  : {url.rsplit('/', 1)[-1]}")
    print(f"Mode  : {'SIMULATION' if simulation else 'ÉCRITURE RÉELLE'}")
    print("-" * 70)

    moteur = create_engine(url)
    inspecteur = inspect(moteur)
    existantes = set(inspecteur.get_table_names())

    a_creer = [t for t in TABLES if t.name not in existantes]
    for table in TABLES:
        etat = "déjà présente" if table.name in existantes else "À CRÉER"
        print(f"  {table.name:<32} {etat}")

    colonnes_commande = {c["name"] for c in inspecteur.get_columns("shop_order_items")} \
        if "shop_order_items" in existantes else set()
    ajout_colonne = "variant_summary" not in colonnes_commande and "shop_order_items" in existantes
    print(f"  {'shop_order_items.variant_summary':<32} "
          f"{'À AJOUTER' if ajout_colonne else 'déjà présente'}")

    if simulation:
        print("\nSimulation seule. Relancer avec --appliquer pour écrire.")
        return 0

    if a_creer:
        Base.metadata.create_all(moteur, tables=a_creer)
        print(f"\n  {len(a_creer)} table(s) créée(s)")

    if ajout_colonne:
        with moteur.begin() as cx:
            cx.execute(text(
                "ALTER TABLE shop_order_items ADD COLUMN variant_summary VARCHAR(300)"
            ))
        print("  colonne variant_summary ajoutée")

    # Contrôle : ce que la base contient réellement après l'opération.
    apres = set(inspect(moteur).get_table_names())
    manquantes = [t.name for t in TABLES if t.name not in apres]
    if manquantes:
        print(f"\nERREUR : tables toujours absentes : {', '.join(manquantes)}")
        return 1

    print("\nToutes les tables sont en place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
