"""Boutique de démonstration « Ndèye Couture » — pour les captures du site.

À quoi cela sert : la visite guidée du site vitrine montre de vrais écrans de
l'application. Des écrans vides ne vendent rien, et les vraies données d'un
commerçant n'ont rien à faire sur une page publique. D'où cette boutique fictive,
crédible et maîtrisée.

Elle couvre exprès **deux métiers** : le prêt-à-porter (tailles et couleurs) et
l'alimentation (dates limites), parce que ce sont les deux écrans que la visite
doit montrer pour parler à toute la clientèle.

Tout ce qui est créé porte la marque `MARQUE` dans ses notes. `--retirer` s'en
sert pour tout défaire : ce semeur ne doit pas laisser de résidu dans une base
qui sert par ailleurs.

    python scripts/seed_vitrine.py            # sème
    python scripts/seed_vitrine.py --retirer  # défait
"""

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from app.database import (Category, CategoryAttribute, CategoryAttributeValue,
                          Client, ClientDebt, Invoice, InvoiceItem,
                          InvoicePayment, Product, ProductVariant,
                          ProductVariantAttribute, SessionLocal, ShopProduct,
                          StockMovement, Supplier, create_tables)

MARQUE = "[demo-vitrine]"
AUJOURDHUI = datetime.now().replace(hour=10, minute=30, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

CATEGORIES = [
    ("Vêtements femme", True, [
        ("Taille", ["36", "38", "40", "42", "44", "46"]),
        ("Couleur", ["Bleu", "Rouge", "Vert", "Noir", "Jaune"]),
    ]),
    ("Tissus & pagnes", True, [
        ("Longueur", ["3 yards", "6 yards", "12 yards"]),
        ("Couleur", ["Multicolore", "Bleu", "Rouge", "Vert"]),
    ]),
    ("Accessoires", False, []),
    ("Épicerie sèche", False, []),
    ("Boissons", False, []),
    ("Produits laitiers", False, []),
]

# (nom, catégorie, prix de vente, prix d'achat, unité, quantité si sans variante)
PRODUITS = [
    ("Robe wax — Collection Korité", "Vêtements femme", 24000, 14500, None, 0),
    ("Ensemble bazin brodé", "Vêtements femme", 45000, 28000, None, 0),
    ("Pagne wax Vlisco", "Tissus & pagnes", 18000, 11000, None, 0),
    ("Foulard assorti", "Accessoires", 6500, 3200, "piece", 34),
    ("Sac à main cuir", "Accessoires", 32000, 19000, "piece", 6),
    ("Riz parfumé Sénégal", "Épicerie sèche", 21000, 17500, "sac", 42),
    ("Huile d'arachide 5 L", "Épicerie sèche", 9500, 7800, "bouteille", 28),
    ("Jus Kirène 1 L", "Boissons", 1200, 850, "bouteille", 96),
    ("Lait caillé Dolima 50 cl", "Produits laitiers", 900, 620, "bouteille", 40),
    ("Yaourt nature 12 pots", "Produits laitiers", 3600, 2650, "paquet", 18),
]

# Déclinaisons : (produit, [(taille/longueur, couleur, quantité)])
DECLINAISONS = {
    "Robe wax — Collection Korité": ("Taille", "Couleur", [
        ("36", "Bleu", 3), ("38", "Bleu", 5), ("40", "Bleu", 5),
        ("42", "Bleu", 2), ("44", "Bleu", 0), ("46", "Bleu", 1),
        ("38", "Rouge", 4), ("40", "Rouge", 3), ("42", "Rouge", 1),
        ("40", "Vert", 1), ("38", "Noir", 2), ("40", "Noir", 4),
    ]),
    "Ensemble bazin brodé": ("Taille", "Couleur", [
        ("38", "Jaune", 2), ("40", "Jaune", 3), ("42", "Jaune", 1),
        ("40", "Noir", 2), ("42", "Noir", 2),
    ]),
    "Pagne wax Vlisco": ("Longueur", "Couleur", [
        ("6 yards", "Multicolore", 12), ("6 yards", "Bleu", 8),
        ("12 yards", "Multicolore", 5), ("3 yards", "Rouge", 6),
    ]),
}

# Lots à date limite : (produit, numéro, jours par rapport à aujourd'hui, quantité)
LOTS = [
    ("Lait caillé Dolima 50 cl", "DL-2608", -2, 6),
    ("Lait caillé Dolima 50 cl", "DL-2612", 4, 18),
    ("Yaourt nature 12 pots", "YN-4471", 6, 8),
    ("Yaourt nature 12 pots", "YN-4488", 21, 10),
    ("Jus Kirène 1 L", "KR-9903", 45, 60),
    ("Huile d'arachide 5 L", "HA-2211", 210, 28),
]

CLIENTS = [
    ("Aïssatou Ndiaye", "77 512 44 08", "aissatou.ndiaye@exemple.sn", "Sacré-Cœur, Dakar"),
    ("Modou Fall", "76 208 91 340", "modou.fall@exemple.sn", "Grand Yoff, Dakar"),
    ("Boutique Teranga", "78 660 12 55", "contact@teranga.exemple.sn", "Marché HLM, Dakar"),
    ("Fatou Diop", "70 445 77 21", "fatou.diop@exemple.sn", "Guédiawaye"),
    ("Mariama Sow", "77 903 18 62", None, "Thiès"),
]

FOURNISSEURS = [
    ("Textiles Sandaga", "M. Diallo", "77 111 22 33"),
    ("Grossiste Kermel", "Mme Bâ", "78 444 55 66"),
]


def _marque(texte=""):
    return f"{MARQUE} {texte}".strip()


ENTREPRISE = {
    "name": "Ndèye Couture",
    "address": "Marché HLM, allée 12 — Dakar",
    "phone": "+221 77 512 44 08",
    "email": "contact@ndeye-couture.sn",
    "website": "ndeye-couture.stock.sn",
}


def _poser_entreprise(db):
    """En-tête des factures. Sans elle, les documents portent « Stock » — le nom
    du logiciel là où le client attend celui de la boutique."""
    import json as _json

    from app.database import UserSettings
    ligne = (db.query(UserSettings)
             .filter(UserSettings.setting_key == "INVOICE_COMPANY")
             .order_by(UserSettings.updated_at.desc()).first())
    charge = _json.dumps(ENTREPRISE, ensure_ascii=False)
    if ligne:
        ligne.setting_value = charge
    else:
        db.add(UserSettings(user_id=None, setting_key="INVOICE_COMPANY",
                            setting_value=charge))
    db.commit()


def semer(db):
    cree = {"categories": 0, "produits": 0, "declinaisons": 0, "lots": 0,
            "clients": 0, "factures": 0, "boutique": 0}
    _poser_entreprise(db)

    # --- Catégories et grilles ---
    for nom, variantes, attributs in CATEGORIES:
        categorie = db.query(Category).filter(Category.name == nom).first()
        if categorie is None:
            categorie = Category(name=nom, description=_marque(f"Catégorie {nom}"),
                                 requires_variants=variantes)
            db.add(categorie)
            db.flush()
            cree["categories"] += 1
        for rang, (nom_attr, valeurs) in enumerate(attributs):
            attribut = (db.query(CategoryAttribute)
                        .filter(CategoryAttribute.category_id == categorie.category_id,
                                CategoryAttribute.name == nom_attr).first())
            if attribut is None:
                attribut = CategoryAttribute(
                    category_id=categorie.category_id, name=nom_attr,
                    code=nom_attr.lower(), type="select", sort_order=rang)
                db.add(attribut)
                db.flush()
            presentes = {v.value for v in attribut.values}
            for position, valeur in enumerate(valeurs):
                if valeur not in presentes:
                    db.add(CategoryAttributeValue(
                        attribute_id=attribut.attribute_id, value=valeur,
                        code=valeur.lower().replace(" ", "_"),
                        sort_order=position))
    db.commit()

    # --- Fournisseurs ---
    for nom, contact, tel in FOURNISSEURS:
        if db.query(Supplier).filter(Supplier.name == nom).first() is None:
            db.add(Supplier(name=nom, contact_person=contact, phone=tel,
                            address=_marque()))
    db.commit()

    # --- Produits ---
    produits = {}
    for nom, categorie, prix, achat, unite, quantite in PRODUITS:
        produit = db.query(Product).filter(Product.name == nom).first()
        if produit is None:
            produit = Product(
                name=nom, category=categorie, price=Decimal(prix),
                purchase_price=Decimal(achat), quantity=quantite,
                unit=unite, condition="neuf", has_unique_serial=False,
                notes=_marque(), entry_date=AUJOURDHUI - timedelta(days=21))
            db.add(produit)
            db.flush()
            cree["produits"] += 1
            if quantite:
                db.add(StockMovement(
                    product_id=produit.product_id, quantity=quantite,
                    movement_type="IN", reference_type="DEMO",
                    unit_price=Decimal(achat), notes=_marque("réception")))
        produits[nom] = produit
    db.commit()

    # --- Déclinaisons ---
    from app import shop_profile as sp
    for nom, (attr_a, attr_b, combinaisons) in DECLINAISONS.items():
        produit = produits[nom]
        total = 0
        for valeur_a, valeur_b, quantite in combinaisons:
            couples = [(attr_a, valeur_a), (attr_b, valeur_b)]
            reference = sp.reference_declinaison(produit.product_id, couples)
            if db.query(ProductVariant).filter(
                    ProductVariant.imei_serial == reference).first():
                continue
            variante = ProductVariant(
                product_id=produit.product_id, imei_serial=reference,
                quantity=quantite, condition="neuf")
            db.add(variante)
            db.flush()
            for nom_attr, valeur in couples:
                db.add(ProductVariantAttribute(
                    variant_id=variante.variant_id, attribute_name=nom_attr,
                    attribute_value=valeur))
            total += quantite
            cree["declinaisons"] += 1
        if total:
            produit.quantity = total
            db.add(StockMovement(
                product_id=produit.product_id, quantity=total,
                movement_type="IN", reference_type="DEMO",
                notes=_marque("déclinaisons")))
    db.commit()

    # --- Lots à date limite ---
    for nom, lot, jours, quantite in LOTS:
        produit = produits[nom]
        reference = sp.reference_lot(produit.product_id, lot,
                                     (AUJOURDHUI + timedelta(days=jours)).date())
        if db.query(ProductVariant).filter(
                ProductVariant.imei_serial == reference).first():
            continue
        db.add(ProductVariant(
            product_id=produit.product_id, imei_serial=reference,
            lot_number=lot, quantity=quantite,
            expiry_date=(AUJOURDHUI + timedelta(days=jours)).date(),
            condition="neuf"))
        cree["lots"] += 1
    db.commit()

    # --- Clients ---
    clients = {}
    for nom, tel, courriel, adresse in CLIENTS:
        client = db.query(Client).filter(Client.name == nom).first()
        if client is None:
            client = Client(name=nom, phone=tel, email=courriel, address=adresse,
                            city="Dakar", country="Sénégal", notes=_marque())
            db.add(client)
            db.flush()
            cree["clients"] += 1
        clients[nom] = client
    db.commit()

    # --- Factures ---
    # (numéro, client, jours, lignes, part payée, statut)
    factures = [
        ("FAC-0248", "Aïssatou Ndiaye", -6, [
            ("Robe wax — Collection Korité", 1, 24000),
            ("Foulard assorti", 2, 6500)], 20000, "partiellement payée"),
        ("FAC-0249", "Boutique Teranga", -4, [
            ("Pagne wax Vlisco", 6, 18000)], 127440, "payée"),
        ("FAC-0250", "Modou Fall", -2, [
            ("Ensemble bazin brodé", 1, 45000),
            ("Sac à main cuir", 1, 32000)], 0, "en attente"),
        ("FAC-0251", "Fatou Diop", 0, [
            ("Riz parfumé Sénégal", 2, 21000),
            ("Huile d'arachide 5 L", 3, 9500)], 70210, "payée"),
        ("FAC-0252", "Mariama Sow", 0, [
            ("Jus Kirène 1 L", 12, 1200),
            ("Yaourt nature 12 pots", 2, 3600)], 0, "en attente"),
    ]
    for numero, nom_client, jours, lignes, paye, statut in factures:
        if db.query(Invoice).filter(Invoice.invoice_number == numero).first():
            continue
        date = AUJOURDHUI + timedelta(days=jours)
        sous_total = sum(q * p for _, q, p in lignes)
        taxe = round(sous_total * 0.18)
        total = sous_total + taxe
        facture = Invoice(
            invoice_number=numero, client_id=clients[nom_client].client_id,
            date=date, due_date=date + timedelta(days=15), status=statut,
            payment_method="wave" if paye else None,
            subtotal=Decimal(sous_total), tax_rate=Decimal("18.00"),
            tax_amount=Decimal(taxe), total=Decimal(total),
            paid_amount=Decimal(paye),
            remaining_amount=Decimal(total - paye),
            internal_notes=_marque())
        db.add(facture)
        db.flush()
        for nom_produit, quantite, prix in lignes:
            db.add(InvoiceItem(
                invoice_id=facture.invoice_id,
                product_id=produits[nom_produit].product_id,
                product_name=nom_produit, quantity=quantite,
                price=Decimal(prix), total=Decimal(quantite * prix)))
        if paye:
            db.add(InvoicePayment(
                invoice_id=facture.invoice_id, amount=Decimal(paye),
                payment_date=date, payment_method="wave",
                reference=_marque(), notes=_marque()))
        cree["factures"] += 1
    db.commit()

    # --- Créances ---
    # Aucune n'est semée : l'écran des dettes les dérive déjà des factures non
    # soldées. En ajouter faisait apparaître chaque client deux fois, avec deux
    # montants légèrement différents — le genre d'incohérence qu'une capture
    # marketing ne pardonne pas.

    # --- Boutique en ligne ---
    vitrine = [
        ("Robe wax — Collection Korité", True, True, "Coupe ajustée, wax authentique."),
        ("Ensemble bazin brodé", True, False, "Bazin riche, broderie main."),
        ("Pagne wax Vlisco", True, True, "6 yards, coloris exclusifs."),
        ("Sac à main cuir", True, False, "Cuir pleine fleur, doublure coton."),
        ("Foulard assorti", True, False, "S'accorde à toute la collection."),
    ]
    for nom, publie, vedette, description in vitrine:
        produit = produits[nom]
        if db.query(ShopProduct).filter(
                ShopProduct.product_id == produit.product_id).first():
            continue
        db.add(ShopProduct(
            product_id=produit.product_id, is_published=publie,
            is_featured=vedette, shop_description=f"{description}",
            is_new=vedette, sort_order=cree["boutique"]))
        cree["boutique"] += 1
    db.commit()

    return cree


def retirer(db):
    """Défait le semis. Ordre imposé par les clés étrangères : les lignes avant
    les factures, les variantes avant les produits."""
    retire = {}

    noms_produits = [p[0] for p in PRODUITS]
    produits = db.query(Product).filter(Product.name.in_(noms_produits)).all()
    ids = [p.product_id for p in produits]

    numeros = ["FAC-0248", "FAC-0249", "FAC-0250", "FAC-0251", "FAC-0252"]
    factures = db.query(Invoice).filter(Invoice.invoice_number.in_(numeros)).all()
    ids_factures = [f.invoice_id for f in factures]
    if ids_factures:
        retire["lignes"] = db.query(InvoiceItem).filter(
            InvoiceItem.invoice_id.in_(ids_factures)).delete(
                synchronize_session=False)
        retire["reglements"] = db.query(InvoicePayment).filter(
            InvoicePayment.invoice_id.in_(ids_factures)).delete(
                synchronize_session=False)
    retire["factures"] = db.query(Invoice).filter(
        Invoice.invoice_number.in_(numeros)).delete(synchronize_session=False)
    retire["creances"] = db.query(ClientDebt).filter(
        ClientDebt.reference.in_(numeros)).delete(synchronize_session=False)
    db.commit()

    if ids:
        variantes = db.query(ProductVariant).filter(
            ProductVariant.product_id.in_(ids)).all()
        ids_variantes = [v.variant_id for v in variantes]
        if ids_variantes:
            retire["attributs"] = db.query(ProductVariantAttribute).filter(
                ProductVariantAttribute.variant_id.in_(ids_variantes)).delete(
                    synchronize_session=False)
        retire["declinaisons"] = db.query(ProductVariant).filter(
            ProductVariant.product_id.in_(ids)).delete(synchronize_session=False)
        retire["boutique"] = db.query(ShopProduct).filter(
            ShopProduct.product_id.in_(ids)).delete(synchronize_session=False)
        retire["mouvements"] = db.query(StockMovement).filter(
            StockMovement.product_id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    retire["produits"] = db.query(Product).filter(
        Product.name.in_(noms_produits)).delete(synchronize_session=False)

    retire["clients"] = db.query(Client).filter(
        Client.name.in_([c[0] for c in CLIENTS])).delete(
            synchronize_session=False)
    retire["fournisseurs"] = db.query(Supplier).filter(
        Supplier.name.in_([f[0] for f in FOURNISSEURS])).delete(
            synchronize_session=False)

    # Les catégories semées ici portent la marque dans leur description : celles
    # que la boutique avait déjà ne sont pas touchées.
    categories = db.query(Category).filter(
        Category.name.in_([c[0] for c in CATEGORIES]),
        Category.description.like(f"{MARQUE}%")).all()
    retire["categories"] = len(categories)
    for c in categories:
        db.delete(c)   # attributs et valeurs suivent par cascade

    # L'en-tête de facture n'est retiré que s'il porte encore le nom fictif :
    # une boutique qui aurait renseigné le sien entre-temps le garde.
    from app.database import UserSettings
    ligne = (db.query(UserSettings)
             .filter(UserSettings.setting_key == "INVOICE_COMPANY").first())
    if ligne and ENTREPRISE["name"] in (ligne.setting_value or ""):
        db.delete(ligne)
        retire["entreprise"] = 1
    db.commit()
    return retire


def main():
    create_tables()
    db = SessionLocal()
    try:
        if "--retirer" in sys.argv:
            print("Retrait de la boutique de démonstration…")
            for cle, nombre in retirer(db).items():
                print(f"  {cle:14} {nombre}")
        else:
            print("Semis de « Ndèye Couture »…")
            for cle, nombre in semer(db).items():
                print(f"  {cle:14} +{nombre}")
        print("\nÉtat :")
        for nom, modele in (("produits", Product), ("déclinaisons", ProductVariant),
                            ("clients", Client), ("factures", Invoice),
                            ("créances", ClientDebt), ("boutique", ShopProduct)):
            print(f"  {nom:14} {db.query(modele).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
