from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta, date
from decimal import Decimal
import random
import secrets
import string
import os


def _bootstrap_password(variable: str, compte: str) -> str:
    """Mot de passe du compte créé au premier amorçage.

    Le code source étant public, aucun mot de passe ne peut y figurer en clair :
    une valeur codée en dur serait connue de quiconque déploie l'application.
    On lit donc `variable` dans l'environnement ; à défaut on tire un mot de
    passe aléatoire et on l'affiche une seule fois, à l'écran, au moment de la
    création du compte. L'exploitant doit le noter ou le redéfinir ensuite.
    """
    fourni = os.getenv(variable)
    if fourni:
        return fourni
    mdp = secrets.token_urlsafe(12)
    print("=" * 60)
    print(f"  Mot de passe généré pour le compte « {compte} » : {mdp}")
    print(f"  Notez-le : il ne sera plus affiché. Pour le fixer vous-même,")
    print(f"  définissez {variable} avant le premier démarrage.")
    print("=" * 60)
    return mdp

from .database import (
    engine,
    SessionLocal,
    create_tables,
    User,
    Category,
    Client,
    Product,
    ProductVariant,
    ProductVariantAttribute,
    ProductSerialNumber,
    StockMovement,
    Quotation,
    QuotationItem,
    Invoice,
    InvoiceItem,
    InvoicePayment,
    BankTransaction,
    Supplier,
)
from .auth import get_password_hash

def init_database():
    """Initialiser la base de données avec les tables et données de base"""
    try:
        # Créer toutes les tables
        create_tables()
        print("✅ Tables créées avec succès")

        # Colonnes ajoutées après coup. `create_tables()` ne touche pas aux
        # tables déjà là : sans ce passage, elles n'apparaîtraient qu'à la
        # première requête HTTP (voir get_db), et tout ce qui ouvre une session
        # sans passer par elle — script, worker de fond — tomberait sur une
        # colonne manquante.
        try:
            from .database import _ensure_colonnes_tardives
            db = SessionLocal()
            try:
                _ensure_colonnes_tardives(db)
                print("✅ Colonnes complémentaires vérifiées")
            finally:
                db.close()
        except Exception as e:
            print(f"⚠️ Colonnes complémentaires non vérifiées : {e}")


        # Migration: Ajouter les colonnes external_price et external_profit si elles n'existent pas
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(engine)
            columns = [col['name'] for col in inspector.get_columns('invoice_items')]
            invoice_columns = [col['name'] for col in inspector.get_columns('invoices')]
            quotation_columns = [col['name'] for col in inspector.get_columns('quotations')]
            db = SessionLocal()
            try:
                if 'external_price' not in columns:
                    # SQLite supporte ALTER TABLE ADD COLUMN
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN external_price NUMERIC(10, 2)"))
                    db.commit()
                    print("✅ Colonne external_price ajoutée")
                if 'external_profit' not in columns:
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN external_profit NUMERIC(12, 2)"))
                    db.commit()
                    print("✅ Colonne external_profit ajoutée")
                
                # Migration pour les notes d'invoice
                if 'internal_notes' not in invoice_columns:
                    db.execute(text("ALTER TABLE invoices ADD COLUMN internal_notes TEXT"))
                    db.commit()
                    print("✅ Colonne internal_notes ajoutée à invoices")
                if 'external_notes' not in invoice_columns:
                    db.execute(text("ALTER TABLE invoices ADD COLUMN external_notes TEXT"))
                    db.commit()
                    print("✅ Colonne external_notes ajoutée à invoices")

                # Migration pour les notes de devis
                if 'internal_notes' not in quotation_columns:
                    db.execute(text("ALTER TABLE quotations ADD COLUMN internal_notes TEXT"))
                    db.commit()
                    print("✅ Colonne internal_notes ajoutée à quotations")
                if 'external_notes' not in quotation_columns:
                    db.execute(text("ALTER TABLE quotations ADD COLUMN external_notes TEXT"))
                    db.commit()
                    print("✅ Colonne external_notes ajoutée à quotations")
            except Exception as e:
                db.rollback()
                print(f"⚠️ Erreur lors de l'ajout des colonnes (peut-être déjà présentes ou syntaxe différente): {e}")
            finally:
                db.close()
        except Exception as e:
            print(f"⚠️ Erreur lors de la vérification des colonnes: {e}")
        
        # Migration boutique: colonnes de présentation (parité avec le nouveau site)
        try:
            from sqlalchemy import inspect as _inspect, text as _text
            _insp = _inspect(engine)
            _db = SessionLocal()
            try:
                _shop_cols = {c['name'] for c in _insp.get_columns('shop_products')}
                _shop_add = {
                    'old_price': 'NUMERIC(12, 2)',
                    'specs': 'TEXT',
                    'is_new': 'BOOLEAN DEFAULT FALSE',
                    'is_bestseller': 'BOOLEAN DEFAULT FALSE',
                    'rating': 'NUMERIC(2, 1)',
                    'reviews_count': 'INTEGER DEFAULT 0',
                }
                for _col, _type in _shop_add.items():
                    if _col not in _shop_cols:
                        _db.execute(_text(f"ALTER TABLE shop_products ADD COLUMN {_col} {_type}"))
                        _db.commit()
                        print(f"✅ shop_products.{_col} ajoutée")

                # Colonnes zone/géoloc/compte sur shop_orders
                _order_cols = {c['name'] for c in _insp.get_columns('shop_orders')}
                _order_add = {
                    'zone_id': 'INTEGER',
                    'zone_name': 'VARCHAR(150)',
                    'delivery_details': 'TEXT',
                    'delivery_lat': 'NUMERIC(9, 6)',
                    'delivery_lng': 'NUMERIC(9, 6)',
                    'customer_id': 'INTEGER',
                }
                for _col, _type in _order_add.items():
                    if _col not in _order_cols:
                        _db.execute(_text(f"ALTER TABLE shop_orders ADD COLUMN {_col} {_type}"))
                        _db.commit()
                        print(f"✅ shop_orders.{_col} ajoutée")
            except Exception as e:
                _db.rollback()
                print(f"⚠️ Migration boutique (colonnes): {e}")
            finally:
                _db.close()
        except Exception as e:
            print(f"⚠️ Migration boutique (inspect): {e}")

        # Seed des zones de livraison par défaut (Dakar) si la table est vide.
        try:
            from .database import ShopDeliveryZone as _Zone
            _db = SessionLocal()
            try:
                if _db.query(_Zone).count() == 0:
                    _default_zones = [
                        ("plateau", "Dakar-Plateau", 0, "24 h", 14.6708, -17.4381),
                        ("medina", "Médina / Gueule Tapée", 0, "24 h", 14.6779, -17.4547),
                        ("fann-point-e", "Fann / Point E / Amitié", 0, "24 h", 14.6926, -17.4644),
                        ("mermoz-sacre", "Mermoz / Sacré-Cœur", 0, "24 h", 14.7092, -17.4707),
                        ("grand-dakar", "Grand Dakar / HLM", 0, "24 h", 14.7089, -17.4468),
                        ("ouakam", "Ouakam", 0, "24–48 h", 14.7217, -17.4889),
                        ("almadies-ngor", "Almadies / Ngor", 0, "24–48 h", 14.7433, -17.5094),
                        ("yoff", "Yoff", 0, "24–48 h", 14.7503, -17.468),
                        ("parcelles", "Parcelles Assainies", 1000, "24–48 h", 14.7639, -17.4419),
                        ("pikine", "Pikine", 1500, "48 h", 14.7549, -17.3903),
                        ("guediawaye", "Guédiawaye", 1500, "48 h", 14.7692, -17.4056),
                        ("keur-massar", "Keur Massar", 2000, "48 h", 14.7789, -17.3128),
                        ("rufisque", "Rufisque / Bargny", 2500, "48–72 h", 14.7167, -17.2667),
                        ("autre", "Autre ville / Régions", 3000, "2–4 jours", 14.4974, -14.4524),
                    ]
                    for _i, (_code, _name, _fee, _delay, _lat, _lng) in enumerate(_default_zones):
                        _db.add(_Zone(code=_code, name=_name, fee=_fee, delay=_delay,
                                      lat=_lat, lng=_lng, sort_order=_i, is_active=True))
                    _db.commit()
                    print(f"✅ {len(_default_zones)} zones de livraison créées")
            except Exception as e:
                _db.rollback()
                print(f"⚠️ Seed zones: {e}")
            finally:
                _db.close()
        except Exception as e:
            print(f"⚠️ Seed zones (import): {e}")

        # Créer une session
        db = SessionLocal()

        try:
            # Migration légère (spécifique SQLite) supprimée pour compatibilité PostgreSQL.
            # La colonne 'requires_variants' est déjà définie dans les modèles SQLAlchemy et sera créée via create_tables().
            
            # Garde-fou: ne semer les données par défaut que si la variable d'env est activée
            seed_defaults = os.getenv("SEED_DEFAULT_DATA", "false").lower() == "true"

            # Une instance livrée à un client a besoin de ses comptes, mais
            # certainement pas du catalogue de démonstration en téléphonie : ses
            # catégories viennent de son métier (voir SHOP_PROFILE plus bas).
            # `SEED_DEFAULT_DATA` reste donc le tout-en-un du développement, et
            # `SEED_ACCOUNTS` ne crée que les deux comptes.
            seed_accounts = seed_defaults or (
                os.getenv("SEED_ACCOUNTS", "false").lower() == "true")

            if seed_accounts:
                # Créer l'utilisateur admin par défaut
                admin_user = db.query(User).filter(User.username == "admin").first()
                if not admin_user:
                    mdp = _bootstrap_password("ADMIN_PASSWORD", "admin")
                    admin_user = User(
                        username="admin",
                        email="admin@techzone.com",
                        password_hash=get_password_hash(mdp),
                        full_name="Administrateur",
                        role="admin",
                        is_active=True
                    )
                    db.add(admin_user)
                    print("✅ Utilisateur admin créé")

                # Créer un utilisateur normal par défaut
                user = db.query(User).filter(User.username == "user").first()
                if not user:
                    mdp = _bootstrap_password("USER_PASSWORD", "user")
                    user = User(
                        username="user",
                        email="user@techzone.com",
                        password_hash=get_password_hash(mdp),
                        full_name="Utilisateur",
                        role="user",
                        is_active=True
                    )
                    db.add(user)
                    print("✅ Utilisateur normal créé")

            if seed_defaults:
                # Catalogue de démonstration, en téléphonie. Réservé au
                # développement : chez un client, les catégories viennent de son
                # métier.
                categories = [
                    {"name": "Smartphones", "requires_variants": True},
                    {"name": "Ordinateurs portables", "requires_variants": True},
                    {"name": "Tablettes", "requires_variants": True},
                    {"name": "Accessoires", "requires_variants": False},
                    {"name": "Téléphones fixes", "requires_variants": False},
                    {"name": "Montres connectées", "requires_variants": True},
                ]
                
                for cat in categories:
                    existing_cat = db.query(Category).filter(Category.name == cat["name"]).first()
                    if not existing_cat:
                        category = Category(
                            name=cat["name"],
                            description=f"Catégorie {cat['name']}",
                            requires_variants=bool(cat.get("requires_variants", False))
                        )
                        db.add(category)
                print("✅ Catégories par défaut créées")
                
                # Créer un client par défaut
                default_client = db.query(Client).filter(Client.name == "Client par défaut").first()
                if not default_client:
                    default_client = Client(
                        name="Client par défaut",
                        contact="Contact par défaut",
                        email="client@example.com",
                        phone="+221 77 123 45 67",
                        address="Adresse par défaut",
                        city="Dakar",
                        country="Sénégal"
                    )
                    db.add(default_client)
                    print("✅ Client par défaut créé")

            # Métier de la boutique, posé par le provisionnement (SHOP_PROFILE).
            #
            # Appliqué une seule fois, au premier démarrage : l'instance d'un
            # client arrive donc avec les catégories et les grilles de son
            # commerce, sans qu'il ait rien à configurer. Ensuite, c'est lui qui
            # décide — un profil déjà enregistré n'est jamais réécrit ici, sinon
            # chaque redémarrage annulerait ses choix.
            try:
                from . import shop_profile
                voulu = (os.getenv("SHOP_PROFILE") or "").strip().lower()
                if voulu and shop_profile.existe(voulu):
                    deja = shop_profile.charger(db)
                    if not deja.get("applique"):
                        rapport = shop_profile.appliquer(
                            db, voulu, applique_par="provisionnement")
                        print(f"✅ {shop_profile.resume_rapport(rapport)}")
                    else:
                        print(f"ℹ️ Métier déjà configuré ({deja['code']}), "
                              f"SHOP_PROFILE ignoré")
                elif voulu:
                    print(f"⚠️ SHOP_PROFILE={voulu!r} inconnu, ignoré")
            except Exception as e:
                print(f"⚠️ Métier de la boutique non appliqué : {e}")


            # Seed massif de données de test si demandé
            seed_large = os.getenv("SEED_LARGE_TEST_DATA", "false").lower() == "true"
            if seed_large:
                sizes = {
                    "clients": int(os.getenv("SEED_CLIENTS", "100")),
                    "products": int(os.getenv("SEED_PRODUCTS", "300")),
                    "variants_per_product_min": int(os.getenv("SEED_VARIANTS_MIN", "1")),
                    "variants_per_product_max": int(os.getenv("SEED_VARIANTS_MAX", "5")),
                    "invoices": int(os.getenv("SEED_INVOICES", "150")),
                    "quotations": int(os.getenv("SEED_QUOTATIONS", "150")),
                    "bank_transactions": int(os.getenv("SEED_BANK_TX", "200")),
                }
                seed_large_test_data(db, sizes)

            # Commit seulement si des changements ont été ajoutés à la session
            if db.new or db.dirty or db.deleted:
                db.commit()
                print("✅ Base de données initialisée/mise à jour avec succès")
            else:
                print("ℹ️ Aucun semis de données par défaut (SEED_DEFAULT_DATA!=true) et aucune écriture effectuée")
            
        except Exception as e:
            db.rollback()
            print(f"❌ Erreur lors de l'initialisation des données: {e}")
            raise
        finally:
            db.close()
            
    except Exception as e:
        print(f"❌ Erreur lors de l'initialisation de la base de données: {e}")
        raise

def migrate_from_postgresql():
    """Fonction pour migrer les données depuis PostgreSQL (à implémenter)"""
    # Cette fonction pourra être utilisée pour migrer les données existantes
    # depuis la base PostgreSQL vers SQLite
    pass

if __name__ == "__main__":
    init_database()

# ===================== SEEDING HELPERS =====================

def _rand_choice(seq):
    return seq[random.randrange(0, len(seq))]

def _rand_str(prefix: str, n: int = 8):
    return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

def _price(min_v=1000, max_v=500000):
    v = random.randint(min_v, max_v)
    return Decimal(v)

def _rand_date_within(days: int = 180) -> datetime:
    delta = random.randint(0, days)
    return datetime.now() - timedelta(days=delta)

def seed_large_test_data(db: Session, sizes: dict):
    print("🧪 Seed massif: démarrage...")

    # Ensure some suppliers
    suppliers = []
    supplier_names = [
        "TechGlobal SARL",
        "SenCom Import",
        "DigitalExpress",
        "Afrique Devices",
        "ElectroHub Dakar",
    ]
    for name in supplier_names:
        s = db.query(Supplier).filter(Supplier.name == name).first()
        if not s:
            s = Supplier(name=name, contact_person="Commercial", phone="+22177" + str(random.randint(1000000, 9999999)))
            db.add(s)
        suppliers.append(s)

    # Categories baseline
    cat_specs = [
        ("Smartphones", True),
        ("Ordinateurs portables", True),
        ("Tablettes", True),
        ("Accessoires", False),
        ("Montres connectées", True),
    ]
    cats = {}
    for (cname, req_var) in cat_specs:
        c = db.query(Category).filter(Category.name == cname).first()
        if not c:
            c = Category(name=cname, description=f"Catégorie {cname}", requires_variants=req_var)
            db.add(c)
        cats[cname] = c

    db.flush()  # assign IDs

    # Clients
    existing_clients = db.query(Client).count()
    to_create_clients = max(0, sizes.get("clients", 0) - existing_clients)
    for i in range(to_create_clients):
        c = Client(
            name=f"Client {i+1}",
            contact=f"Contact {i+1}",
            email=f"client{i+1}@example.com",
            phone=f"+221 77 {random.randint(1000000, 9999999)}",
            address=f"Adresse {i+1}",
            city=_rand_choice(["Dakar", "Thies", "Saint-Louis", "Touba", "Kaolack"]),
            country="Sénégal",
        )
        db.add(c)

    # Products with optional variants
    brands = ["Samsung", "Apple", "Xiaomi", "Infinix", "Tecno", "HP", "Dell", "Lenovo"]
    conditions = ["neuf", "occasion", "venant"]
    existing_products = db.query(Product).count()
    to_create_products = max(0, sizes.get("products", 0) - existing_products)
    for i in range(to_create_products):
        catname = _rand_choice(list(cats.keys()))
        cat_requires_variants = cats[catname].requires_variants
        name = f"{_rand_choice(brands)} {_rand_choice(['S','Note','Pro','Air','Plus','Max'])}-{random.randint(1,999)}"
        p = Product(
            name=name,
            description=f"Produit de test {name}",
            quantity=0,
            price=_price(50000, 1500000) / Decimal(100),
            purchase_price=_price(30000, 900000) / Decimal(100),
            category=catname,
            brand=_rand_choice(brands),
            model=_rand_choice(["A1","A2","M2","G5","Z10","2023","2024"]),
            barcode=_rand_str("BC", 10),
            condition=_rand_choice(conditions),
            has_unique_serial=cat_requires_variants,
            entry_date=_rand_date_within(120),
        )
        db.add(p)
        db.flush()

        # Stock movements (IN) to populate quantity
        in_qty = random.randint(1, 30)
        db.add(StockMovement(product_id=p.product_id, quantity=in_qty, movement_type="IN", reference_type="SEED", unit_price=p.purchase_price))
        p.quantity += in_qty

        # Create variants if required
        if cat_requires_variants:
            nvars = random.randint(sizes.get("variants_per_product_min", 1), sizes.get("variants_per_product_max", 3))
            for _ in range(nvars):
                imei = _rand_str("IMEI", 12)
                v = ProductVariant(
                    product_id=p.product_id,
                    imei_serial=imei,
                    barcode=_rand_str("VB", 10),
                    condition=_rand_choice(conditions),
                    is_sold=False,
                )
                db.add(v)
                db.flush()
                # Attributes example
                if cats[catname].name in ("Smartphones", "Montres connectées"):
                    db.add(ProductVariantAttribute(variant=v, attribute_name="couleur", attribute_value=_rand_choice(["noir","bleu","argent","or"])) )
                    db.add(ProductVariantAttribute(variant=v, attribute_name="stockage", attribute_value=_rand_choice(["64Go","128Go","256Go"])) )

    db.flush()

    # Quotations
    all_clients = db.query(Client).all()
    all_products = db.query(Product).all()
    for i in range(sizes.get("quotations", 0)):
        if not all_clients or not all_products:
            break
        cl = _rand_choice(all_clients)
        q = Quotation(
            quotation_number=f"Q{datetime.now().strftime('%y%m%d')}-{i+1:04d}",
            client_id=cl.client_id,
            date=_rand_date_within(100),
            status=_rand_choice(["en attente","accepté","refusé","expiré"]),
            subtotal=Decimal(0), tax_rate=Decimal("18.00"), tax_amount=Decimal(0), total=Decimal(0),
            notes=None,
        )
        db.add(q)
        db.flush()
        nitems = random.randint(1, 4)
        subtotal = Decimal(0)
        for _ in range(nitems):
            pr = _rand_choice(all_products)
            qty = random.randint(1, 3)
            price = Decimal(float(pr.price))
            total = price * qty
            db.add(QuotationItem(quotation_id=q.quotation_id, product_id=pr.product_id, product_name=pr.name, quantity=qty, price=price, total=total))
            subtotal += total
        tax = (subtotal * Decimal("0.18")).quantize(Decimal("1."))
        q.subtotal = subtotal
        q.tax_amount = tax
        q.total = subtotal + tax

    # Invoices with payments and OUT stock movements
    for i in range(sizes.get("invoices", 0)):
        if not all_clients or not all_products:
            break
        cl = _rand_choice(all_clients)
        inv = Invoice(
            invoice_number=f"F{datetime.now().strftime('%y%m%d')}-{i+1:05d}",
            client_id=cl.client_id,
            date=_rand_date_within(90),
            status=_rand_choice(["en attente","payée","partiellement payée","en retard","annulée"]),
            payment_method=_rand_choice(["espèces","carte","virement"]),
            subtotal=Decimal(0), tax_rate=Decimal("18.00"), tax_amount=Decimal(0), total=Decimal(0),
            paid_amount=Decimal(0), remaining_amount=Decimal(0),
        )
        db.add(inv)
        db.flush()
        nitems = random.randint(1, 4)
        subtotal = Decimal(0)
        for _ in range(nitems):
            pr = _rand_choice(all_products)
            qty = random.randint(1, 3)
            price = Decimal(float(pr.price))
            total = price * qty
            db.add(InvoiceItem(invoice_id=inv.invoice_id, product_id=pr.product_id, product_name=pr.name, quantity=qty, price=price, total=total))
            subtotal += total
            # stock OUT movement
            db.add(StockMovement(product_id=pr.product_id, quantity=qty, movement_type="OUT", reference_type="INVOICE", reference_id=inv.invoice_id, unit_price=price))
            pr.quantity = max(0, (pr.quantity or 0) - qty)
        tax = (subtotal * Decimal("0.18")).quantize(Decimal("1."))
        inv.subtotal = subtotal
        inv.tax_amount = tax
        inv.total = subtotal + tax
        # payments
        paid = subtotal if random.random() < 0.6 else subtotal * Decimal("0.5")
        paid = paid.quantize(Decimal("1."))
        if paid > 0:
            db.add(InvoicePayment(invoice_id=inv.invoice_id, amount=paid, payment_method=inv.payment_method, payment_date=_rand_date_within(60)))
        inv.paid_amount = paid
        inv.remaining_amount = inv.total - paid

    # Bank Transactions
    for i in range(sizes.get("bank_transactions", 0)):
        ttype = _rand_choice(["entry", "exit"])
        method = _rand_choice(["virement", "cheque"])
        amt = Decimal(random.randint(5000, 200000))
        bt = BankTransaction(
            type=ttype,
            motif=_rand_choice(["Vente", "Achat", "Dépense", "Avoir", "Divers"]),
            description=f"Transaction {i+1}",
            amount=amt,
            date=_rand_date_within(200).date(),
            method=method,
            reference=_rand_str("TX", 8),
        )
        db.add(bt)

    print("🧪 Seed massif: terminé.")
