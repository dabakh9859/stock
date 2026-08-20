from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, ForeignKey, UniqueConstraint, Index, Numeric, Date, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import func
from datetime import datetime
import os
from dotenv import load_dotenv
import threading

load_dotenv()

# Source de vérité de la connexion DB
_RAW_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    # Valeur par défaut: SQLite
    "sqlite:///./techzone.db",
)

# Normalisation pour SQLAlchemy
def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    u = url.strip()
    # Alias postgres -> postgresql et forcer le driver psycopg (v3)
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql+psycopg://", 1)
    elif u.startswith("postgresql://"):
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    elif u.startswith("postgresql+psycopg2://"):
        u = u.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    # Gestion SSL: respecter URL explicite, sinon DB_SSLMODE, sinon heuristique locale vs distante
    if u.startswith("postgresql+") and "sslmode=" not in u:
        # 1) Variable d'env prioritaire si définie (ex: require, disable, prefer)
        sslmode_env = os.getenv("DB_SSLMODE")
        if sslmode_env:
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}sslmode={sslmode_env}"
        else:
            # 2) Heuristique: pour localhost/127.0.0.1/db (Docker service) -> disable, sinon require
            lower_u = u.lower()
            is_local = ("@localhost" in lower_u) or ("@127.0.0.1" in lower_u) or ("@db:" in lower_u)
            sep = "&" if "?" in u else "?"
            u = f"{u}{sep}sslmode={'disable' if is_local else 'require'}"
    return u

DATABASE_URL = _normalize_db_url(_RAW_DATABASE_URL)

# Pool configuration (tunable via env)
_is_sqlite = "sqlite" in DATABASE_URL
_pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
_pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
_pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "1800"))  # 30 min

engine_kwargs = {
    "pool_pre_ping": True,
}
if _is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update({
        "pool_size": _pool_size,
        "max_overflow": _max_overflow,
        "pool_timeout": _pool_timeout,
        "pool_recycle": _pool_recycle,
    })

engine = create_engine(
    DATABASE_URL,
    **engine_kwargs,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Modèles de base de données basés sur le schéma PostgreSQL original

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100))
    role = Column(String(20), default="user")  # admin, user, manager
    is_active = Column(Boolean, default=True)
    # Génération de jetons. Tout jeton portant une génération différente est
    # refusé : c'est ce qui permet de déconnecter quelqu'un immédiatement, alors
    # qu'un JWT est par nature valable jusqu'à son échéance. Incrémentée au
    # changement de mot de passe et à la désactivation d'un compte.
    token_epoch = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime)

class Client(Base):
    __tablename__ = "clients"
    
    client_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    contact = Column(String(100))
    email = Column(String(100))
    phone = Column(String(20))
    address = Column(Text)
    city = Column(String(50))
    postal_code = Column(String(10))
    country = Column(String(50), default="Sénégal")
    tax_number = Column(String(50))
    notes = Column(Text)
    disable_debt_reminder = Column(Boolean, default=False)

# Créances clients (dettes clients manuelles)
class ClientDebt(Base):
    __tablename__ = "client_debts"

    debt_id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), index=True, nullable=True)
    reference = Column(String(100), nullable=False)
    date = Column(DateTime, default=func.now())
    due_date = Column(DateTime)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="pending")  # pending, partial, paid, overdue
    description = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    client = relationship("Client")

class ClientDebtPayment(Base):
    __tablename__ = "client_debt_payments"

    payment_id = Column(Integer, primary_key=True, index=True)
    debt_id = Column(Integer, ForeignKey("client_debts.debt_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

# Achats quotidiens (petites dépenses)
class DailyPurchase(Base):
    __tablename__ = "daily_purchases"

    id = Column(Integer, primary_key=True, index=True)
    # Date de l'achat (jour civil)
    date = Column(Date, nullable=False, index=True)
    # Catégorie simple: café, eau, électricité, transport, fournitures, autres
    category = Column(String(50), nullable=False, index=True)
    # Fournisseur ou source libre (ex: "Boutique du coin")
    supplier = Column(String(100))
    # Description libre
    description = Column(Text)
    # Montant TTC
    amount = Column(Numeric(12, 2), nullable=False)
    # Méthode: espece | mobile | virement | cheque
    payment_method = Column(String(20), default="espece", index=True)
    # Référence/Justif optionnelle
    reference = Column(String(100))
    created_at = Column(DateTime, default=func.now())
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Relations
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index('ix_daily_purchases_date_category', 'date', 'category'),
    )

# Paramétrage des catégories d'achats quotidiens
class DailyPurchaseCategory(Base):
    __tablename__ = "daily_purchase_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())

class Category(Base):
    __tablename__ = "categories"
    
    category_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False)
    description = Column(Text)
    requires_variants = Column(Boolean, default=False, nullable=False)

class CategoryAttribute(Base):
    __tablename__ = "category_attributes"
    
    attribute_id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.category_id", ondelete="CASCADE"), index=True, nullable=False)
    name = Column(String(50), nullable=False)
    code = Column(String(50), nullable=True)  # unique within category
    type = Column(String(20), default="select")  # select, multiselect, text, number, boolean
    required = Column(Boolean, default=False, nullable=False)
    multi_select = Column(Boolean, default=False, nullable=False)
    sort_order = Column(Integer, default=0)
    
    # Relations
    # Ordre explicite: sans lui, PostgreSQL renvoie les valeurs dans l'ordre
    # physique des lignes et une valeur modifiée saute en fin de liste.
    values = relationship(
        "CategoryAttributeValue",
        back_populates="attribute",
        cascade="all, delete-orphan",
        order_by="(CategoryAttributeValue.sort_order, CategoryAttributeValue.value_id)",
    )
    
    __table_args__ = (
        UniqueConstraint('category_id', 'code', name='uq_category_attribute_code_per_category'),
    )

class CategoryAttributeValue(Base):
    __tablename__ = "category_attribute_values"
    
    value_id = Column(Integer, primary_key=True, index=True)
    attribute_id = Column(Integer, ForeignKey("category_attributes.attribute_id", ondelete="CASCADE"), index=True, nullable=False)
    value = Column(String(100), nullable=False)
    code = Column(String(100), nullable=True)  # unique within attribute
    sort_order = Column(Integer, default=0)
    
    # Relations
    attribute = relationship("CategoryAttribute", back_populates="values")
    
    __table_args__ = (
        UniqueConstraint('attribute_id', 'code', name='uq_attribute_value_code_per_attribute'),
    )

class Product(Base):
    __tablename__ = "products"

    product_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(500), nullable=False)  # Augmenté selon les mémoires
    description = Column(Text)
    quantity = Column(Integer, nullable=False, default=0)
    price = Column(Numeric(10, 2), nullable=False)  # Prix de vente unitaire
    wholesale_price = Column(Numeric(10, 2), nullable=True)  # Prix de vente en gros
    purchase_price = Column(Numeric(10, 2), default=0.00)
    category = Column(String(50), index=True)
    brand = Column(String(100))
    model = Column(String(100))
    barcode = Column(String(255), unique=True)
    condition = Column(String(50), nullable=True, default="neuf")  # neuf | occasion | venant (configurable)
    has_unique_serial = Column(Boolean, default=False)
    # Unité de vente : « piece » par défaut, mais une supérette vend au kilo,
    # au litre ou au sachet. Nul = pièce, pour ne rien changer aux fiches déjà
    # saisies. Les valeurs proposées viennent de shop_profile.UNITES.
    unit = Column(String(20), nullable=True)
    entry_date = Column(DateTime)
    notes = Column(Text)
    image_path = Column(String(500), nullable=True)  # Chemin vers l'image du produit
    source = Column(String(50), nullable=True, default='purchase')  # purchase | exchange | return | other
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), nullable=True, index=True)  # Fournisseur du produit
    created_at = Column(DateTime, default=func.now())
    is_archived = Column(Boolean, default=False, index=True)  # Produit archivé (masqué par défaut)
    # Produit « boutique uniquement » : vendable sur le site (toujours sur commande)
    # mais absent de l'inventaire physique — exclu des écrans de gestion de stock.
    shop_only = Column(Boolean, default=False, index=True)

    # Relations
    serial_numbers = relationship("ProductSerialNumber", back_populates="product", cascade="all, delete-orphan")
    gallery = relationship("ProductImage", back_populates="product", cascade="all, delete-orphan",
                           order_by="ProductImage.sort_order")
    stock_movements = relationship("StockMovement", back_populates="product")
    variants = relationship("ProductVariant", back_populates="product", cascade="all, delete-orphan")
    supplier = relationship("Supplier")

class ProductImage(Base):
    """Galerie d'images d'un produit. `Product.image_path` reste l'image principale."""
    __tablename__ = "product_images"

    image_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False, index=True)
    image_path = Column(String(500), nullable=False)
    source_url = Column(Text)            # Origine si l'image vient d'une recherche en ligne
    sort_order = Column(Integer, default=0, index=True)
    created_at = Column(DateTime, default=func.now())

    product = relationship("Product", back_populates="gallery")


class ProductSerialNumber(Base):
    __tablename__ = "product_serial_numbers"
    
    serial_number_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"))
    serial_number = Column(String(255), nullable=False)
    barcode = Column(String(255), unique=True)
    is_available = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    
    # Relations
    product = relationship("Product", back_populates="serial_numbers")
    
    __table_args__ = (UniqueConstraint('product_id', 'serial_number'),)

class ProductVariant(Base):
    __tablename__ = "product_variants"
    
    variant_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"))
    # Référence unique de l'exemplaire. Un IMEI en téléphonie ; ailleurs, un code
    # engendré (voir shop_profile.reference_lot) — la contrainte d'unicité est
    # globale, or deux produits différents peuvent porter le même numéro de lot.
    imei_serial = Column(String(255), unique=True, nullable=False)
    # Suivi des denrées : numéro de lot tel qu'imprimé par le fabricant (non
    # unique : le même lot peut couvrir plusieurs références) et date limite.
    lot_number = Column(String(100), nullable=True, index=True)
    expiry_date = Column(Date, nullable=True, index=True)
    barcode = Column(String(128), unique=True)  # Selon les mémoires
    condition = Column(String(50), nullable=True)  # hérite par défaut du produit
    price = Column(Numeric(10, 2), nullable=True)  # Prix spécifique à la variante (optionnel)
    quantity = Column(Integer, nullable=True)  # Quantité pour variantes avec IMEI similaires (optionnel)
    is_sold = Column(Boolean, default=False)
    # Arrivage d'où provient cette variante (optionnel) + coût d'achat propre.
    arrival_id = Column(Integer, ForeignKey("arrivals.arrival_id", ondelete="SET NULL"), nullable=True, index=True)
    purchase_price = Column(Numeric(10, 2), nullable=True)
    created_at = Column(DateTime, default=func.now())

    # Relations
    product = relationship("Product", back_populates="variants")
    arrival = relationship("Arrival", back_populates="variants")
    attributes = relationship("ProductVariantAttribute", back_populates="variant", cascade="all, delete-orphan")

class ProductVariantAttribute(Base):
    __tablename__ = "product_variant_attributes"
    
    attribute_id = Column(Integer, primary_key=True, index=True)
    variant_id = Column(Integer, ForeignKey("product_variants.variant_id", ondelete="CASCADE"))
    attribute_name = Column(String(50), nullable=False)  # couleur, stockage, etc.
    attribute_value = Column(String(100), nullable=False)
    
    # Relations
    variant = relationship("ProductVariant", back_populates="attributes")

class StockMovement(Base):
    __tablename__ = "stock_movements"
    
    movement_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"))
    quantity = Column(Integer, nullable=False)
    movement_type = Column(String(10), nullable=False)  # IN, OUT
    reference_type = Column(String(20))  # INVOICE, QUOTATION, etc.
    reference_id = Column(Integer)
    notes = Column(Text)
    unit_price = Column(Numeric(10, 2), default=0)
    created_at = Column(DateTime, default=func.now())
    
    # Relations
    product = relationship("Product", back_populates="stock_movements")

# --- Bank Transactions ---
class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    # 'entry' or 'exit'
    type = Column(String(10), nullable=False, index=True)
    motif = Column(String(255), nullable=False)
    description = Column(Text)
    amount = Column(Numeric(12, 2), nullable=False)
    date = Column(Date, nullable=False, index=True)
    # 'virement' or 'cheque'
    method = Column(String(20), nullable=False, index=True)
    reference = Column(String(255))
    created_at = Column(DateTime, default=func.now())

class Supplier(Base):
    __tablename__ = "suppliers"
    
    supplier_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    contact_person = Column(String(100))
    email = Column(String(100))
    phone = Column(String(20))
    address = Column(Text)

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    
    order_id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id"))
    order_date = Column(DateTime, default=func.now())
    status = Column(String(20), default="PENDING")  # PENDING, DELIVERED, CANCELLED
    total_amount = Column(Numeric(12, 2), default=0)
    
    # Relations
    supplier = relationship("Supplier")
    items = relationship("PurchaseOrderItem", back_populates="order", cascade="all, delete-orphan")

class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("purchase_orders.order_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Numeric(10, 2), nullable=False)
    
    # Relations
    order = relationship("PurchaseOrder", back_populates="items")
    product = relationship("Product")

# Dettes fournisseurs
class SupplierDebt(Base):
    __tablename__ = "supplier_debts"

    debt_id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), index=True, nullable=True)
    reference = Column(String(100), nullable=False)
    date = Column(DateTime, default=func.now())
    due_date = Column(DateTime)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="pending")  # pending, partial, paid, overdue
    description = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    supplier = relationship("Supplier")

class SupplierDebtPayment(Base):
    __tablename__ = "supplier_debt_payments"

    payment_id = Column(Integer, primary_key=True, index=True)
    debt_id = Column(Integer, ForeignKey("supplier_debts.debt_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)

# Factures fournisseur (version simplifiée)
class SupplierInvoice(Base):
    __tablename__ = "supplier_invoices"

    invoice_id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), index=True)
    invoice_number = Column(String(100), unique=True, nullable=True)  # Optionnel - peut être extrait du PDF
    invoice_date = Column(DateTime, nullable=True)  # Optionnel - peut être extrait du PDF
    due_date = Column(DateTime)
    description = Column(Text, nullable=True)  # Optionnel - peut être extrait du PDF
    amount = Column(Numeric(12, 2), nullable=True)  # Optionnel - peut être extrait du PDF
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), default=0)
    status = Column(String(20), default="pending")  # pending, partial, paid, overdue
    payment_method = Column(String(50))
    notes = Column(Text)
    items_json = Column(Text, nullable=True)  # Articles en JSON
    pdf_path = Column(String(500), nullable=True)  # Optionnel - chemin vers le fichier PDF/image
    pdf_filename = Column(String(255), nullable=True)  # Optionnel - nom original du fichier
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relations
    supplier = relationship("Supplier")
    payments = relationship("SupplierInvoicePayment", back_populates="invoice", cascade="all, delete-orphan")

# Ancienne table SupplierInvoiceItem - supprimée dans la version simplifiée
# class SupplierInvoiceItem(Base):
#     __tablename__ = "supplier_invoice_items" - plus utilisée

class SupplierInvoicePayment(Base):
    __tablename__ = "supplier_invoice_payments"

    payment_id = Column(Integer, primary_key=True, index=True)
    supplier_invoice_id = Column(Integer, ForeignKey("supplier_invoices.invoice_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    invoice = relationship("SupplierInvoice", back_populates="payments")

class Quotation(Base):
    __tablename__ = "quotations"
    
    quotation_id = Column(Integer, primary_key=True, index=True)
    quotation_number = Column(String(50), unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    date = Column(DateTime, nullable=False)
    expiry_date = Column(DateTime)
    status = Column(String(20), default="en attente")  # en attente, accepté, refusé, expiré
    is_sent = Column(Boolean, default=False)  # champ séparé pour marquer l'envoi
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(5, 2), default=18.00)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text)  # Legacy field, conserve aussi les métadonnées __SIGNATURE__/__QUOTE_QTYS__
    internal_notes = Column(Text)
    external_notes = Column(Text)
    show_item_prices = Column(Boolean, default=True)  # Afficher prix par article
    show_section_totals = Column(Boolean, default=True)  # Afficher total par section
    created_at = Column(DateTime, default=func.now())
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Relations
    client = relationship("Client")
    items = relationship("QuotationItem", back_populates="quotation", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by])

class QuotationItem(Base):
    __tablename__ = "quotation_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    quotation_id = Column(Integer, ForeignKey("quotations.quotation_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    
    # Relations
    quotation = relationship("Quotation", back_populates="items")
    product = relationship("Product")

class Invoice(Base):
    __tablename__ = "invoices"
    
    invoice_id = Column(Integer, primary_key=True, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)
    invoice_type = Column(String(20), default="normal")  # normal, exchange, flash_sale
    client_id = Column(Integer, ForeignKey("clients.client_id"), nullable=True)
    quotation_id = Column(Integer, ForeignKey("quotations.quotation_id"))
    date = Column(DateTime, nullable=False)
    due_date = Column(DateTime)
    status = Column(String(20), default="en attente")  # en attente, payée, partiellement payée, en retard, annulée
    payment_method = Column(String(50))
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(5, 2), default=18.00)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    exchange_discount = Column(Numeric(12, 2), default=0)  # Montant total de reprise des produits échangés
    paid_amount = Column(Numeric(12, 2), default=0)
    remaining_amount = Column(Numeric(12, 2), nullable=False)
    notes = Column(Text)  # Legacy field, kept for compatibility if needed
    internal_notes = Column(Text)
    external_notes = Column(Text)
    show_tax = Column(Boolean, default=True)
    show_item_prices = Column(Boolean, default=True)  # Afficher prix par article
    show_section_totals = Column(Boolean, default=True)  # Afficher total par section
    price_display = Column(String(10), default="TTC")  # HT, TTC
    # Champs de garantie
    has_warranty = Column(Boolean, default=False)
    warranty_duration = Column(Integer)  # en mois (6 ou 12)
    warranty_start_date = Column(Date)
    warranty_end_date = Column(Date)
    created_at = Column(DateTime, default=func.now())
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Relations
    client = relationship("Client")
    quotation = relationship("Quotation")
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    exchange_items = relationship("InvoiceExchangeItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", back_populates="invoice", cascade="all, delete-orphan")
    creator = relationship("User", foreign_keys=[created_by])

class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    is_gift = Column(Boolean, default=False, nullable=False)  # Article gratuit/cadeau (sort du stock mais n'impacte pas CA/bénéfices)
    # Prix externe et bénéfice (pour produits achetés dans d'autres boutiques)
    external_price = Column(Numeric(10, 2), nullable=True)  # Prix d'achat externe (optionnel)
    external_profit = Column(Numeric(12, 2), nullable=True)  # Bénéfice calculé (total - external_price * quantity)
    # Unité précise vendue sur cette ligne. Source de vérité du rattachement
    # ligne <-> exemplaire physique: c'est elle qui permet de remettre le bon
    # IMEI en stock quand la ligne est retirée ou la facture supprimée.
    # (Auparavant déduit du bloc __SERIALS__ des notes, non fiable.)
    variant_id = Column(Integer, ForeignKey("product_variants.variant_id", ondelete="SET NULL"), nullable=True, index=True)
    variant_imei = Column(String(255), nullable=True)  # copie du n° de série, survit à la suppression de la variante

    # Relations
    invoice = relationship("Invoice", back_populates="items")
    product = relationship("Product")

class InvoiceExchangeItem(Base):
    __tablename__ = "invoice_exchange_items"
    
    exchange_item_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="CASCADE"))
    # Produit échangé (sortant - celui que le client donne)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="SET NULL"), nullable=True)
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=True)  # Prix de reprise du produit échangé
    variant_id = Column(Integer, ForeignKey("product_variants.variant_id", ondelete="SET NULL"), nullable=True)
    variant_imei = Column(String(255), nullable=True)
    # Notes pour le produit échangé
    notes = Column(Text, nullable=True)
    
    # Relations
    invoice = relationship("Invoice", back_populates="exchange_items")
    product = relationship("Product")
    variant = relationship("ProductVariant")

class InvoicePayment(Base):
    __tablename__ = "invoice_payments"
    
    payment_id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="CASCADE"))
    amount = Column(Numeric(12, 2), nullable=False)
    payment_date = Column(DateTime, default=func.now())
    payment_method = Column(String(50))
    reference = Column(String(100))
    notes = Column(Text)
    
    # Relations
    invoice = relationship("Invoice", back_populates="payments")

class DeliveryNote(Base):
    __tablename__ = "delivery_notes"
    
    delivery_note_id = Column(Integer, primary_key=True, index=True)
    delivery_note_number = Column(String(50), unique=True, nullable=False)
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id"))
    client_id = Column(Integer, ForeignKey("clients.client_id"))
    date = Column(DateTime, nullable=False)
    delivery_date = Column(DateTime)
    status = Column(String(20), default="en_preparation")  # en_preparation, en_cours, livré, annulé
    delivery_address = Column(Text)
    delivery_contact = Column(String(100))
    delivery_phone = Column(String(20))
    subtotal = Column(Numeric(12, 2), nullable=False)
    tax_rate = Column(Numeric(5, 2), default=18.00)
    tax_amount = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    transport_cost = Column(Numeric(10, 2), default=0)
    notes = Column(Text)
    delivered_by = Column(String(100))
    signature_received = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())
    delivered_at = Column(DateTime)
    
    # Relations
    invoice = relationship("Invoice")
    client = relationship("Client")
    items = relationship("DeliveryNoteItem", back_populates="delivery_note", cascade="all, delete-orphan")

class DeliveryNoteItem(Base):
    __tablename__ = "delivery_note_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    delivery_note_id = Column(Integer, ForeignKey("delivery_notes.delivery_note_id", ondelete="CASCADE"))
    product_id = Column(Integer, ForeignKey("products.product_id"))
    product_name = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    delivered_quantity = Column(Integer, default=0)
    serial_numbers = Column(Text)  # JSON string pour les numéros de série
    
    # Relations
    delivery_note = relationship("DeliveryNote", back_populates="items")
    product = relationship("Product")

# Tables pour les paramètres et cache
class UserSettings(Base):
    __tablename__ = "user_settings"
    
    setting_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=True)
    setting_key = Column(String(100), nullable=False)
    setting_value = Column(Text)  # JSON string
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relations
    user = relationship("User")
    
    __table_args__ = (UniqueConstraint('user_id', 'setting_key'),)

class ScanHistory(Base):
    __tablename__ = "scan_history"
    
    scan_id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"))
    barcode = Column(String(255), nullable=False)
    product_name = Column(String(500))
    scan_type = Column(String(50))  # product, variant, etc.
    result_data = Column(Text)  # JSON string avec les détails
    scanned_at = Column(DateTime, default=func.now())
    
    # Relations
    user = relationship("User")

class AppCache(Base):
    __tablename__ = "app_cache"
    
    cache_id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(255), unique=True, nullable=False)
    cache_value = Column(Text)  # JSON string
    expires_at = Column(DateTime)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

# Demandes quotidiennes des clients
class DailyClientRequest(Base):
    __tablename__ = "daily_client_requests"

    request_id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), index=True, nullable=True)
    client_name = Column(String(100), nullable=False)  # Nom du client (peut être différent de la base)
    client_phone = Column(String(20))  # Téléphone du client
    product_description = Column(Text, nullable=False)  # Description textuelle du produit demandé
    request_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), default="pending")  # pending, fulfilled, cancelled
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Relations
    client = relationship("Client")

# Ventes quotidiennes
class Arrival(Base):
    """Un arrivage de marchandise : un lot reçu à une date, éventuellement d'un
    fournisseur. Les produits/variantes y sont rattachés pour en suivre les ventes."""
    __tablename__ = "arrivals"

    arrival_id = Column(Integer, primary_key=True, index=True)
    reference = Column(String(50), unique=True, nullable=False)
    label = Column(String(200))
    arrival_date = Column(Date, nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.supplier_id", ondelete="SET NULL"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    supplier = relationship("Supplier")
    items = relationship("ArrivalItem", back_populates="arrival", cascade="all, delete-orphan")
    variants = relationship("ProductVariant", back_populates="arrival")


class ArrivalItem(Base):
    """Quantité d'un produit SANS variante rattachée à un arrivage (ex : 50 coques
    sur les 150 en stock). Le suivi des ventes se fait en FIFO par produit."""
    __tablename__ = "arrival_items"

    arrival_item_id = Column(Integer, primary_key=True, index=True)
    arrival_id = Column(Integer, ForeignKey("arrivals.arrival_id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=0)
    purchase_price = Column(Numeric(10, 2), nullable=True)
    created_at = Column(DateTime, default=func.now())

    arrival = relationship("Arrival", back_populates="items")
    product = relationship("Product")


class DailySale(Base):
    __tablename__ = "daily_sales"

    sale_id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), index=True, nullable=True)
    client_name = Column(String(100), nullable=False)  # Nom du client
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="SET NULL"), index=True, nullable=True)
    product_name = Column(String(500), nullable=False)  # Nom du produit vendu
    variant_id = Column(Integer, ForeignKey("product_variants.variant_id", ondelete="SET NULL"), index=True, nullable=True)
    variant_imei = Column(String(255), nullable=True)  # IMEI de la variante vendue
    variant_barcode = Column(String(128), nullable=True)  # Code-barres de la variante
    variant_condition = Column(String(50), nullable=True)  # Condition de la variante
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Numeric(10, 2), nullable=False)
    total_amount = Column(Numeric(12, 2), nullable=False)
    sale_date = Column(Date, nullable=False, index=True)
    payment_method = Column(String(50), default="espece")  # espece, mobile, virement, cheque
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="SET NULL"), nullable=True)  # Si lié à une facture
    notes = Column(Text)
    created_at = Column(DateTime, default=func.now())

    # Relations
    client = relationship("Client")
    product = relationship("Product")
    variant = relationship("ProductVariant")
    invoice = relationship("Invoice")

    __table_args__ = (
        Index('ix_daily_sales_date_client', 'sale_date', 'client_id'),
    )

# Migrations de données
class Migration(Base):
    __tablename__ = "migrations"

    migration_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    type = Column(String(50), nullable=False)  # products, clients, stock, etc.
    status = Column(String(20), default="pending")  # pending, running, completed, failed
    created_at = Column(DateTime, default=func.now())
    completed_at = Column(DateTime)
    total_records = Column(Integer, default=0)
    processed_records = Column(Integer, default=0)
    success_records = Column(Integer, default=0)
    error_records = Column(Integer, default=0)
    file_name = Column(String(255))
    description = Column(Text)
    error_message = Column(Text)
    created_by = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"))

    # Relations
    user = relationship("User")
    logs = relationship("MigrationLog", back_populates="migration", cascade="all, delete-orphan")

class MigrationLog(Base):
    __tablename__ = "migration_logs"

    log_id = Column(Integer, primary_key=True, index=True)
    migration_id = Column(Integer, ForeignKey("migrations.migration_id", ondelete="CASCADE"))
    timestamp = Column(DateTime, default=func.now())
    level = Column(String(20), default="info")  # info, success, error
    message = Column(Text, nullable=False)

    # Relations
    migration = relationship("Migration", back_populates="logs")

# Fonction pour créer les tables
def create_tables():
    Base.metadata.create_all(bind=engine)


_variant_price_checked = False
_variant_price_lock = threading.Lock()
_variant_quantity_checked = False
_variant_quantity_lock = threading.Lock()
_product_source_checked = False
_product_source_lock = threading.Lock()
_invoice_item_is_gift_checked = False
_invoice_item_is_gift_lock = threading.Lock()
_invoice_client_nullable_checked = False
_invoice_client_nullable_lock = threading.Lock()
_exchange_item_price_checked = False
_exchange_item_price_lock = threading.Lock()
_invoice_exchange_discount_checked = False
_invoice_exchange_discount_lock = threading.Lock()
_invoice_item_variant_checked = False
_invoice_item_variant_lock = threading.Lock()


def _ensure_variant_price_column(db) -> None:
    """Ajoute la colonne product_variants.price si absente (migration légère sans Alembic)."""
    global _variant_price_checked
    if _variant_price_checked:
        return
    with _variant_price_lock:
        if _variant_price_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(product_variants)"))
                cols = [row[1] for row in res]
                if 'price' not in cols:
                    db.execute(text("ALTER TABLE product_variants ADD COLUMN price NUMERIC(10, 2)"))
                    db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'product_variants' AND column_name = 'price'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE product_variants ADD COLUMN price NUMERIC(10, 2)"))
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _variant_price_checked = True


def _ensure_variant_quantity_column(db) -> None:
    """Ajoute la colonne product_variants.quantity si absente (migration légère sans Alembic)."""
    global _variant_quantity_checked
    if _variant_quantity_checked:
        return
    with _variant_quantity_lock:
        if _variant_quantity_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(product_variants)"))
                cols = [row[1] for row in res]
                if 'quantity' not in cols:
                    db.execute(text("ALTER TABLE product_variants ADD COLUMN quantity INTEGER"))
                    db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'product_variants' AND column_name = 'quantity'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE product_variants ADD COLUMN quantity INTEGER"))
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _variant_quantity_checked = True


def _ensure_product_source_column(db) -> None:
    """Ajoute la colonne products.source si absente (migration légère sans Alembic)."""
    global _product_source_checked
    if _product_source_checked:
        return
    with _product_source_lock:
        if _product_source_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(products)"))
                cols = [row[1] for row in res]
                if 'source' not in cols:
                    db.execute(text("ALTER TABLE products ADD COLUMN source VARCHAR(50) DEFAULT 'purchase'"))
                    db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'products' AND column_name = 'source'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE products ADD COLUMN source VARCHAR(50) DEFAULT 'purchase'"))
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _product_source_checked = True


def _ensure_invoice_item_is_gift_column(db) -> None:
    """Ajoute la colonne invoice_items.is_gift si absente (migration légère sans Alembic)."""
    global _invoice_item_is_gift_checked
    if _invoice_item_is_gift_checked:
        return
    with _invoice_item_is_gift_lock:
        if _invoice_item_is_gift_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(invoice_items)"))
                cols = [row[1] for row in res]
                if 'is_gift' not in cols:
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN is_gift INTEGER DEFAULT 0 NOT NULL"))
                    db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'invoice_items' AND column_name = 'is_gift'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN is_gift BOOLEAN DEFAULT FALSE NOT NULL"))
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _invoice_item_is_gift_checked = True

def _ensure_invoice_item_variant_columns(db) -> None:
    """Ajoute invoice_items.variant_id / variant_imei si absentes (migration légère sans Alembic)."""
    global _invoice_item_variant_checked
    if _invoice_item_variant_checked:
        return
    with _invoice_item_variant_lock:
        if _invoice_item_variant_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(invoice_items)"))
                cols = [row[1] for row in res]
                if 'variant_id' not in cols:
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN variant_id INTEGER"))
                if 'variant_imei' not in cols:
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN variant_imei VARCHAR(255)"))
                db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'invoice_items' AND column_name IN ('variant_id', 'variant_imei')"
                ))
                existing = {row[0] for row in result}
                if 'variant_id' not in existing:
                    db.execute(text(
                        "ALTER TABLE invoice_items ADD COLUMN variant_id INTEGER "
                        "REFERENCES product_variants(variant_id) ON DELETE SET NULL"
                    ))
                    db.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_invoice_items_variant_id "
                        "ON invoice_items (variant_id)"
                    ))
                if 'variant_imei' not in existing:
                    db.execute(text("ALTER TABLE invoice_items ADD COLUMN variant_imei VARCHAR(255)"))
                if existing != {'variant_id', 'variant_imei'}:
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _invoice_item_variant_checked = True


def _ensure_invoice_client_nullable(db) -> None:
    """Rend la colonne invoices.client_id nullable pour permettre les ventes flash sans client."""
    global _invoice_client_nullable_checked
    if _invoice_client_nullable_checked:
        return
    with _invoice_client_nullable_lock:
        if _invoice_client_nullable_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                # SQLite ne supporte pas ALTER COLUMN, la colonne est déjà nullable par défaut
                pass
            else:
                # PostgreSQL
                db.execute(text("ALTER TABLE invoices ALTER COLUMN client_id DROP NOT NULL"))
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _invoice_client_nullable_checked = True

def _ensure_exchange_item_price_column(db) -> None:
    """Ajoute la colonne invoice_exchange_items.price si absente (migration légère sans Alembic)."""
    global _exchange_item_price_checked
    if _exchange_item_price_checked:
        return
    with _exchange_item_price_lock:
        if _exchange_item_price_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(invoice_exchange_items)"))
                cols = [row[1] for row in res]
                if 'price' not in cols:
                    db.execute(text("ALTER TABLE invoice_exchange_items ADD COLUMN price NUMERIC(10, 2)"))
                    db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'invoice_exchange_items' AND column_name = 'price'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE invoice_exchange_items ADD COLUMN price NUMERIC(10, 2)"))
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _exchange_item_price_checked = True

def _ensure_invoice_exchange_discount_column(db) -> None:
    """Ajoute la colonne invoices.exchange_discount si absente (migration légère sans Alembic)."""
    global _invoice_exchange_discount_checked
    if _invoice_exchange_discount_checked:
        return
    with _invoice_exchange_discount_lock:
        if _invoice_exchange_discount_checked:
            return
        try:
            bind = db.get_bind()
            dialect = bind.dialect.name
            if dialect == 'sqlite':
                res = db.execute(text("PRAGMA table_info(invoices)"))
                cols = [row[1] for row in res]
                if 'exchange_discount' not in cols:
                    db.execute(text("ALTER TABLE invoices ADD COLUMN exchange_discount NUMERIC(12, 2) DEFAULT 0"))
                    db.commit()
            else:
                # PostgreSQL
                result = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'invoices' AND column_name = 'exchange_discount'"
                ))
                if not result.fetchone():
                    db.execute(text("ALTER TABLE invoices ADD COLUMN exchange_discount NUMERIC(12, 2) DEFAULT 0"))
                    db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _invoice_exchange_discount_checked = True

# Fonction pour obtenir une session de base de données
_shop_delivery_reported_checked = False
_shop_delivery_reported_lock = threading.Lock()


def _ensure_shop_delivery_reported_column(db) -> None:
    """Ajoute shop_deliveries.customer_reported_at si absente (migration légère)."""
    global _shop_delivery_reported_checked
    if _shop_delivery_reported_checked:
        return
    with _shop_delivery_reported_lock:
        if _shop_delivery_reported_checked:
            return
        try:
            bind = db.get_bind()
            if bind.dialect.name == 'sqlite':
                cols = [row[1] for row in db.execute(text("PRAGMA table_info(shop_deliveries)"))]
                if 'customer_reported_at' not in cols:
                    db.execute(text("ALTER TABLE shop_deliveries ADD COLUMN customer_reported_at DATETIME"))
                    db.commit()
            else:
                existe = db.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'shop_deliveries' AND column_name = 'customer_reported_at'"
                )).first()
                if not existe:
                    db.execute(text("ALTER TABLE shop_deliveries ADD COLUMN customer_reported_at TIMESTAMP"))
                    db.commit()
        except Exception:
            db.rollback()
        finally:
            _shop_delivery_reported_checked = True


# Colonnes ajoutées après coup. Les fonctions ci-dessus datent d'avant : chacune
# refait le même travail avec son propre drapeau et son propre verrou. Pour les
# suivantes, une seule table de correspondance et un seul passage — le
# comportement reste celui de la maison (ALTER TABLE léger, sans Alembic, jamais
# fatal, vérifié une fois par processus).
#
# Chaque entrée : (table, colonne, type SQLite, type PostgreSQL).
COLONNES_TARDIVES = (
    ("products", "unit", "VARCHAR(20)", "VARCHAR(20)"),
    ("product_variants", "lot_number", "VARCHAR(100)", "VARCHAR(100)"),
    ("product_variants", "expiry_date", "DATE", "DATE"),
    # Zéro par défaut, comme les jetons déjà émis qui ne portent pas la
    # génération : les sessions en cours survivent à la mise à jour.
    ("users", "token_epoch", "INTEGER DEFAULT 0 NOT NULL",
     "INTEGER DEFAULT 0 NOT NULL"),
)

_colonnes_tardives_faites = False
_colonnes_tardives_lock = threading.Lock()


def _ensure_colonnes_tardives(db) -> None:
    global _colonnes_tardives_faites
    if _colonnes_tardives_faites:
        return
    with _colonnes_tardives_lock:
        if _colonnes_tardives_faites:
            return
        try:
            sqlite = db.get_bind().dialect.name == 'sqlite'
            for table, colonne, type_sqlite, type_pg in COLONNES_TARDIVES:
                try:
                    if sqlite:
                        presentes = [r[1] for r in db.execute(
                            text(f"PRAGMA table_info({table})"))]
                        manque = colonne not in presentes
                        definition = type_sqlite
                    else:
                        manque = not db.execute(text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t AND column_name = :c"
                        ), {"t": table, "c": colonne}).first()
                        definition = type_pg
                    if manque:
                        db.execute(text(
                            f"ALTER TABLE {table} ADD COLUMN {colonne} {definition}"))
                        db.commit()
                except Exception:
                    # Une colonne qui résiste ne doit pas empêcher les suivantes.
                    db.rollback()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            _colonnes_tardives_faites = True


def get_db():
    db = SessionLocal()
    try:
        try:
            _ensure_variant_price_column(db)
        except Exception:
            pass
        try:
            _ensure_colonnes_tardives(db)
        except Exception:
            pass
        try:
            _ensure_shop_delivery_reported_column(db)
        except Exception:
            pass
        try:
            _ensure_variant_quantity_column(db)
        except Exception:
            pass
        try:
            _ensure_product_source_column(db)
        except Exception:
            pass
        try:
            _ensure_invoice_item_is_gift_column(db)
        except Exception:
            pass
        try:
            _ensure_invoice_item_variant_columns(db)
        except Exception:
            pass
        try:
            _ensure_invoice_client_nullable(db)
        except Exception:
            pass
        try:
            _ensure_exchange_item_price_column(db)
        except Exception:
            pass
        try:
            _ensure_invoice_exchange_discount_column(db)
        except Exception:
            pass
        yield db
    finally:
        # Defensive close: if the server terminated the connection (e.g.,
        # IdleInTransactionSessionTimeout), SQLAlchemy may raise during the
        # implicit rollback on close. Swallow those errors to avoid masking
        # the real response with a 500 at shutdown.
        try:
            db.close()
        except Exception:
            try:
                # Attempt explicit rollback then close, ignore any errors.
                db.rollback()
            except Exception:
                pass


# ==================== MODÈLE MAINTENANCE ====================

class Maintenance(Base):
    __tablename__ = "maintenances"
    
    maintenance_id = Column(Integer, primary_key=True, index=True)
    maintenance_number = Column(String(50), unique=True, nullable=False, index=True)  # Ex: MAINT-0001
    
    # Client
    client_id = Column(Integer, ForeignKey("clients.client_id", ondelete="SET NULL"), nullable=True)
    client_name = Column(String(100), nullable=False)
    client_phone = Column(String(20))
    client_email = Column(String(100))
    
    # Machine/Appareil
    device_type = Column(String(100), nullable=False)  # Ordinateur portable, PC fixe, Imprimante, etc.
    device_brand = Column(String(100))  # Marque
    device_model = Column(String(100))  # Modèle
    device_serial = Column(String(100))  # Numéro de série
    device_description = Column(Text)  # Description détaillée de l'appareil
    device_accessories = Column(Text)  # Accessoires laissés (chargeur, souris, etc.)
    device_condition = Column(Text)  # État à la réception (rayures, dommages, etc.)
    
    # Problème et diagnostic
    problem_description = Column(Text, nullable=False)  # Description du problème par le client
    diagnosis = Column(Text)  # Diagnostic du technicien
    work_done = Column(Text)  # Travaux effectués
    
    # Dates
    reception_date = Column(DateTime, nullable=False, default=func.now())  # Date de réception
    estimated_completion_date = Column(Date)  # Date estimée de fin
    actual_completion_date = Column(Date)  # Date réelle de fin
    pickup_deadline = Column(Date)  # Date limite de récupération
    pickup_date = Column(Date)  # Date de récupération effective
    
    # Statut
    status = Column(String(30), default="received")  # received, in_progress, completed, ready, picked_up, abandoned
    priority = Column(String(20), default="normal")  # low, normal, high, urgent
    
    # Coûts
    estimated_cost = Column(Numeric(12, 2))  # Coût estimé
    final_cost = Column(Numeric(12, 2))  # Coût final
    advance_paid = Column(Numeric(12, 2), default=0)  # Avance payée
    
    # Garantie et responsabilité
    warranty_days = Column(Integer, default=30)  # Jours de garantie sur la réparation
    liability_waived = Column(Boolean, default=False)  # Responsabilité dégagée après délai
    liability_waived_date = Column(Date)  # Date de dégagement de responsabilité
    
    # Rappels
    reminder_sent = Column(Boolean, default=False)  # Rappel envoyé
    reminder_sent_date = Column(DateTime)  # Date d'envoi du rappel
    
    # Technicien
    technician_id = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    
    # Notes
    notes = Column(Text)
    internal_notes = Column(Text)  # Notes internes (non visibles sur la fiche)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relations
    client = relationship("Client")
    technician = relationship("User")
    
    __table_args__ = (
        Index('ix_maintenances_status', 'status'),
        Index('ix_maintenances_pickup_deadline', 'pickup_deadline'),
    )


# ==================== MODÈLES BOUTIQUE EN LIGNE ====================
# La boutique partage directement le catalogue de la gestion de stock: aucune
# synchronisation à maintenir, un produit = une ligne dans `products`.

class ShopProduct(Base):
    """Réglages boutique d'un produit du stock (publication, vedette, disponibilité)."""
    __tablename__ = "shop_products"

    shop_product_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"), unique=True, nullable=False, index=True)

    is_published = Column(Boolean, default=True, index=True)   # Visible sur le site
    is_featured = Column(Boolean, default=False, index=True)   # Produit en vedette
    # Disponibilité: NULL = automatique (en stock / sur commande selon la quantité).
    # 'epuise' = forcé manuellement par l'utilisateur, seul cas où un produit est épuisé.
    availability_override = Column(String(20), nullable=True)

    shop_description = Column(Text)          # Description vitrine (sinon celle du stock)
    shop_price = Column(Numeric(12, 2), nullable=True)   # Prix boutique (sinon prix du stock)
    shop_price_max = Column(Numeric(12, 2), nullable=True)  # Borne haute: prix affiché en intervalle
    sort_order = Column(Integer, default=0, index=True)

    # --- Présentation vitrine (parité avec le nouveau site) ---
    old_price = Column(Numeric(12, 2), nullable=True)    # Ancien prix -> badge promo
    specs = Column(Text, nullable=True)                  # Caractéristiques, une par ligne
    is_new = Column(Boolean, default=False, index=True)  # Badge "Nouveau"
    is_bestseller = Column(Boolean, default=False, index=True)  # Badge "Meilleure vente"
    rating = Column(Numeric(2, 1), nullable=True)        # Note moyenne (ex. 4.8)
    reviews_count = Column(Integer, default=0)           # Nombre d'avis affiché

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    product = relationship("Product")


class ShopOrder(Base):
    """Commande passée depuis le site e-commerce."""
    __tablename__ = "shop_orders"

    order_id = Column(Integer, primary_key=True, index=True)
    order_number = Column(String(50), unique=True, nullable=False, index=True)

    customer_name = Column(String(200), nullable=False)
    customer_phone = Column(String(50), nullable=False)
    customer_email = Column(String(200))
    delivery_address = Column(Text)
    delivery_city = Column(String(100))

    # Zone de livraison choisie + point exact (parité nouveau site)
    zone_id = Column(Integer, ForeignKey("shop_delivery_zones.zone_id", ondelete="SET NULL"), nullable=True)
    zone_name = Column(String(150))          # figé à la commande
    delivery_details = Column(Text)          # précisions d'adresse saisies par le client
    delivery_lat = Column(Numeric(9, 6))
    delivery_lng = Column(Numeric(9, 6))

    # Rattachement à un compte client (Phase 5, nullable pour commande anonyme)
    customer_id = Column(Integer, ForeignKey("shop_customers.customer_id", ondelete="SET NULL"), nullable=True, index=True)

    # en attente | confirmée | en préparation | expédiée | livrée | annulée
    status = Column(String(30), default="en attente", index=True)
    payment_method = Column(String(50))
    payment_status = Column(String(30), default="non payé")  # non payé | acompte | payé

    subtotal = Column(Numeric(12, 2), nullable=False, default=0)
    delivery_fee = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), nullable=False, default=0)

    notes = Column(Text)                 # Note du client
    internal_notes = Column(Text)        # Note interne équipe
    invoice_id = Column(Integer, ForeignKey("invoices.invoice_id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    items = relationship("ShopOrderItem", back_populates="order", cascade="all, delete-orphan")
    delivery = relationship("ShopDelivery", back_populates="order", uselist=False, cascade="all, delete-orphan")


class ShopOrderItem(Base):
    __tablename__ = "shop_order_items"

    item_id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("shop_orders.order_id", ondelete="CASCADE"), index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="SET NULL"), nullable=True)

    product_name = Column(String(500), nullable=False)  # Figé à la commande
    quantity = Column(Integer, nullable=False, default=1)
    price = Column(Numeric(12, 2), nullable=False)
    total = Column(Numeric(12, 2), nullable=False)
    availability_at_order = Column(String(30))  # en stock | sur commande, au moment de l'achat
    # Variantes choisies par le client, figées au moment de la commande sous la
    # forme « Couleur: Noir · Capacité: 256 Go ». Le libellé est recopié plutôt
    # que référencé : renommer une option des mois plus tard ne doit pas
    # réécrire une commande déjà passée.
    variant_summary = Column(String(300), nullable=True)

    order = relationship("ShopOrder", back_populates="items")
    product = relationship("Product")


class ShopDelivery(Base):
    """Suivi de livraison d'une commande."""
    __tablename__ = "shop_deliveries"

    delivery_id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("shop_orders.order_id", ondelete="CASCADE"), unique=True, index=True)

    # à planifier | planifiée | en cours | livrée | échouée
    status = Column(String(30), default="à planifier", index=True)
    carrier = Column(String(100))
    tracking_number = Column(String(100))
    delivery_address = Column(Text)
    delivery_city = Column(String(100))
    scheduled_date = Column(DateTime)
    # Le client a signalé lui-même avoir reçu sa commande. C'est un signal, pas
    # une preuve : seule la confirmation en boutique (`delivered_at`) fait foi.
    customer_reported_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime)
    delivery_fee = Column(Numeric(12, 2), default=0)
    notes = Column(Text)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    order = relationship("ShopOrder", back_populates="delivery")


class ShopSetting(Base):
    """Configuration du site (page d'accueil, bannières, coordonnées, thème)."""
    __tablename__ = "shop_settings"

    setting_id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text)
    group = Column(String(50), default="general", index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ShopDeliveryZone(Base):
    """Zone de livraison de la boutique (frais + délai indicatif)."""
    __tablename__ = "shop_delivery_zones"

    zone_id = Column(Integer, primary_key=True, index=True)
    code = Column(String(60), unique=True, index=True)   # slug stable, ex. "plateau"
    name = Column(String(150), nullable=False)
    fee = Column(Numeric(12, 2), default=0)              # frais en FCFA (0 = gratuit)
    delay = Column(String(60))                            # ex. "24 h", "48–72 h"
    lat = Column(Numeric(9, 6))
    lng = Column(Numeric(9, 6))
    sort_order = Column(Integer, default=0, index=True)
    is_active = Column(Boolean, default=True, index=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ShopCustomer(Base):
    """Compte client de la boutique en ligne (auth serveur)."""
    __tablename__ = "shop_customers"

    customer_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    phone = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(200))
    password_hash = Column(String(255), nullable=False)
    default_address = Column(Text)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ShopSavedZone(Base):
    """Adresse enregistrée d'un client (« Maison », « Bureau »…)."""
    __tablename__ = "shop_saved_zones"

    saved_zone_id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("shop_customers.customer_id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(100), nullable=False)
    zone_id = Column(Integer, ForeignKey("shop_delivery_zones.zone_id", ondelete="SET NULL"), nullable=True)
    details = Column(Text)
    lat = Column(Numeric(9, 6))
    lng = Column(Numeric(9, 6))
    created_at = Column(DateTime, default=func.now())


class ShopWishlistItem(Base):
    """Favori d'un client (référence un produit du stock)."""
    __tablename__ = "shop_wishlist_items"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("shop_customers.customer_id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())
    __table_args__ = (UniqueConstraint('customer_id', 'product_id', name='uq_wishlist_customer_product'),)


class ShopDemand(Base):
    """Demande d'un produit hors stock (réf DA-), suivie côté boutique."""
    __tablename__ = "shop_demands"

    demand_id = Column(Integer, primary_key=True, index=True)
    demand_number = Column(String(50), unique=True, nullable=False, index=True)  # DA-...
    product_id = Column(Integer, ForeignKey("products.product_id", ondelete="SET NULL"), nullable=True)
    product_name = Column(String(500))          # figé à la demande
    quantity = Column(Integer, default=1)

    customer_name = Column(String(200))
    customer_phone = Column(String(50))
    customer_email = Column(String(200))
    customer_id = Column(Integer, ForeignKey("shop_customers.customer_id", ondelete="SET NULL"), nullable=True, index=True)

    # en attente | disponible | annulée
    status = Column(String(30), default="en attente", index=True)
    notes = Column(Text)

    created_at = Column(DateTime, default=func.now(), index=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    product = relationship("Product")


# ---------------------------------------------------------------------------
# Variantes commerciales de la boutique
# ---------------------------------------------------------------------------
# Ces tables n'ont AUCUN lien avec les variantes du stock (`ProductVariant`,
# qui portent les IMEI) ni avec les attributs de catégorie qui les qualifient.
# Elles décrivent les choix proposés au client sur le site — couleur, capacité —
# et le supplément de prix attaché à chacun.
#
# Le cloisonnement est délibéré : modifier une couleur en vitrine ne doit pas
# pouvoir déqualifier un IMEI en stock.
#
# Un groupe se déclare une fois puis se rattache à autant de catégories ou de
# produits que voulu (`ShopVariantAssignment`), ce qui évite de ressaisir la
# même grille produit par produit. Une exception ponctuelle se règle sans
# défaire l'héritage, via `ShopVariantOverride`.

class ShopBanner(Base):
    """Une bannière éditoriale de la boutique en ligne : photo ou vidéo.

    Elle sert à mettre en avant une opération, un arrivage ou une marque, sans
    passer par le catalogue. Le contenu est piloté depuis l'application de
    stock ; le site n'invente rien.

    `emplacement` désigne l'endroit de la page d'accueil où la bannière se
    glisse. Les valeurs sont fixes et connues du site — un emplacement inconnu
    n'affiche rien plutôt que de casser la mise en page.
    """
    __tablename__ = "shop_banners"

    banner_id = Column(Integer, primary_key=True, index=True)

    # `image` ou `video`. Le type décide du rendu et des contrôles proposés.
    media_type = Column(String(10), nullable=False, default="image")
    # Chemin relatif sous static/uploads/banners/, comme les photos produit.
    media_path = Column(String(500), nullable=False)
    # Image d'attente d'une vidéo : affichée pendant le chargement, et servie
    # telle quelle aux navigateurs qui refusent la lecture automatique.
    poster_path = Column(String(500), nullable=True)

    title = Column(String(120), nullable=True)
    subtitle = Column(String(240), nullable=True)
    # Libellé et cible du bouton. Sans lien, la bannière n'est pas cliquable.
    link_label = Column(String(60), nullable=True)
    link_url = Column(String(500), nullable=True)

    # Où la bannière se place dans la page d'accueil.
    placement = Column(String(30), nullable=False, default="apres_categories", index=True)
    sort_order = Column(Integer, default=0, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ShopVariantGroup(Base):
    """Un choix proposé au client : « Couleur », « Capacité »…"""
    __tablename__ = "shop_variant_groups"

    group_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(60), nullable=False)
    # Aide à la saisie affichée sous le sélecteur sur le site.
    help_text = Column(String(160), nullable=True)
    sort_order = Column(Integer, default=0, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())

    options = relationship(
        "ShopVariantOption",
        back_populates="group",
        cascade="all, delete-orphan",
        # Ordre explicite : la PREMIÈRE option est celle présélectionnée sur le
        # site. Sans tri imposé, PostgreSQL renvoie l'ordre physique des lignes
        # et une option modifiée sauterait en fin de liste — changeant du même
        # coup le prix affiché par défaut.
        order_by="(ShopVariantOption.sort_order, ShopVariantOption.option_id)",
    )
    assignments = relationship(
        "ShopVariantAssignment", back_populates="group", cascade="all, delete-orphan"
    )


class ShopVariantOption(Base):
    """Une valeur possible : « Noir », « 256 Go »."""
    __tablename__ = "shop_variant_options"

    option_id = Column(Integer, primary_key=True, index=True)
    group_id = Column(
        Integer, ForeignKey("shop_variant_groups.group_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    label = Column(String(80), nullable=False)
    # Supplément ajouté au prix de base, en francs. Zéro pour l'option d'entrée
    # de gamme. Négatif accepté (remise sur une finition moins chère).
    price_delta = Column(Numeric(12, 2), nullable=False, default=0)
    sort_order = Column(Integer, default=0, index=True)
    is_active = Column(Boolean, default=True, nullable=False)

    group = relationship("ShopVariantGroup", back_populates="options")

    __table_args__ = (
        UniqueConstraint("group_id", "label", name="uq_shop_variant_option_label"),
    )


class ShopVariantAssignment(Base):
    """Rattache un groupe à une catégorie ou à un produit précis."""
    __tablename__ = "shop_variant_assignments"

    assignment_id = Column(Integer, primary_key=True, index=True)
    group_id = Column(
        Integer, ForeignKey("shop_variant_groups.group_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # 'category' ou 'product'. Une seule des deux colonnes suivantes est remplie.
    target_type = Column(String(12), nullable=False, index=True)
    # Le nom de la catégorie, pas son identifiant : `products.category` est une
    # chaîne, et c'est elle qui sert au rattachement dans tout le reste du code.
    category_name = Column(String(50), nullable=True, index=True)
    product_id = Column(
        Integer, ForeignKey("products.product_id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    sort_order = Column(Integer, default=0)

    group = relationship("ShopVariantGroup", back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("group_id", "target_type", "category_name", "product_id",
                         name="uq_shop_variant_assignment"),
    )


class ShopVariantOverride(Base):
    """Exception sur un produit : autre supplément, ou option retirée.

    Permet de garder l'héritage de catégorie tout en traitant le cas
    particulier — sans quoi il faudrait sortir le produit de sa catégorie.
    """
    __tablename__ = "shop_variant_overrides"

    override_id = Column(Integer, primary_key=True, index=True)
    product_id = Column(
        Integer, ForeignKey("products.product_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    option_id = Column(
        Integer, ForeignKey("shop_variant_options.option_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # NULL = on garde le supplément du groupe et on ne change que la visibilité.
    price_delta = Column(Numeric(12, 2), nullable=True)
    is_hidden = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("product_id", "option_id", name="uq_shop_variant_override"),
    )


# ---------------------------------------------------------------------------
# Actions de l'assistant : file d'attente de confirmation ET journal d'audit
#
# Une seule table pour les deux usages : c'est la même information vue à deux
# moments — ce que l'assistant a proposé, puis ce qui a réellement été exécuté.
#
# Le modèle n'écrit jamais directement. Il dépose ici une proposition, que seul
# un clic humain transforme en action (voir routers/assistant.py). Même
# entièrement détourné par une consigne cachée dans les données, il ne peut donc
# que proposer.
# ---------------------------------------------------------------------------

class AssistantAction(Base):
    __tablename__ = "assistant_actions"

    action_id = Column(Integer, primary_key=True, index=True)
    # Jeton opaque remis au navigateur : c'est lui qui autorise l'exécution, et
    # il ne vaut que pour l'utilisateur qui l'a obtenu.
    token = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="SET NULL"),
                     nullable=True, index=True)
    # Recopié : le journal doit rester lisible même si le compte disparaît.
    username = Column(String(50))
    tool_name = Column(String(60), nullable=False, index=True)
    arguments = Column(Text)   # JSON des arguments proposés par le modèle
    summary = Column(Text)     # résumé en clair, celui qu'a lu l'humain
    # pending → confirmed | cancelled | expired | failed
    status = Column(String(20), default="pending", nullable=False, index=True)
    result = Column(Text)      # JSON du résultat d'exécution
    error = Column(Text)
    created_at = Column(DateTime, default=func.now(), index=True)
    expires_at = Column(DateTime)
    resolved_at = Column(DateTime)
