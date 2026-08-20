from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Literal
from datetime import datetime, date
from decimal import Decimal

# Schémas pour l'authentification
class UserLogin(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=128)

class UserCreate(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    password: str
    full_name: Optional[str] = None
    role: Literal["admin", "manager", "user"] = "user"

class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    full_name: Optional[str] = None
    role: Optional[Literal["admin", "manager", "user"]] = None

class UserResponse(BaseModel):
    user_id: int
    username: str
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class ProfileResponse(UserResponse):
    """Son propre compte, tel que l'écran « Mon profil » l'affiche.

    Ajoute la dernière connexion, que `UserResponse` ne porte pas : elle n'a de
    sens que pour soi-même — la donner sur la liste des comptes reviendrait à
    exposer les horaires de chacun à tous.
    """
    last_login: Optional[datetime] = None


class ProfileUpdate(BaseModel):
    """Ce que l'on peut changer sur son propre compte.

    Volontairement séparé de `UserUpdate`, qui porte `role` : le réutiliser ici
    laisserait n'importe qui se nommer administrateur en glissant un champ de
    plus dans la requête. Le rôle, le nom de connexion et l'activation restent
    des décisions d'administration.
    """
    full_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None


class PasswordChange(BaseModel):
    """Changement de son propre mot de passe.

    L'actuel est exigé : sans lui, une session laissée ouverte sur un poste
    partagé suffirait à s'approprier le compte définitivement.
    """
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

# Schémas pour les catégories d'achats quotidiens
class DailyPurchaseCategoryCreate(BaseModel):
    name: str

class DailyPurchaseCategoryResponse(BaseModel):
    id: int
    name: str
    class Config:
        from_attributes = True

# Schémas pour les transactions bancaires (simplifiés)
class BankTransactionCreate(BaseModel):
    # type: "entry" (entrée) ou "exit" (sortie)
    type: Literal['entry', 'exit']
    motif: str
    description: Optional[str] = None
    amount: Decimal
    date: date
    # méthode de paiement
    method: Literal['virement', 'cheque', 'carte', 'especes', 'mobile_money', 'prelevement', 'virement_instantane', 'autre']
    reference: Optional[str] = None

class BankTransactionResponse(BaseModel):
    id: int
    type: Literal['entry', 'exit']
    motif: str
    description: Optional[str] = None
    amount: Decimal
    date: date
    method: Literal['virement', 'cheque', 'carte', 'especes', 'mobile_money', 'prelevement', 'virement_instantane', 'autre']
    reference: Optional[str] = None

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

# Schémas pour les clients
class ClientCreate(BaseModel):
    name: str
    contact: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "Sénégal"
    tax_number: Optional[str] = None
    notes: Optional[str] = None
    disable_debt_reminder: Optional[bool] = False

class ClientUpdate(BaseModel):
    name: Optional[str] = None
    contact: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    tax_number: Optional[str] = None
    notes: Optional[str] = None
    disable_debt_reminder: Optional[bool] = None

class ClientResponse(BaseModel):
    client_id: int
    name: str
    contact: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    address: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    country: Optional[str] = None
    tax_number: Optional[str]
    notes: Optional[str]
    disable_debt_reminder: Optional[bool] = False
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

# Schémas pour les variantes de produits
class ProductVariantAttributeCreate(BaseModel):
    attribute_name: str
    attribute_value: str

class ProductVariantAttributeResponse(BaseModel):
    attribute_id: int
    attribute_name: str
    attribute_value: str
    
    class Config:
        from_attributes = True

class ProductVariantCreate(BaseModel):
    # Facultatif : engendré depuis les attributs de la déclinaison quand la
    # boutique ne suit pas d'identifiant unique (voir products.py). Reste exigé
    # en téléphonie, où un IMEI inventé serait un faux.
    imei_serial: Optional[str] = None
    barcode: Optional[str] = None
    condition: Optional[str] = None  # neuf | occasion | venant (configurable)
    price: Optional[Decimal] = None  # Prix spécifique à la variante (optionnel)
    quantity: Optional[int] = None  # Quantité pour variantes avec IMEI similaires (optionnel)
    lot_number: Optional[str] = None  # Numéro de lot du fabricant (denrées)
    expiry_date: Optional[date] = None  # Date limite de consommation / d'utilisation
    attributes: List[ProductVariantAttributeCreate] = []

class ProductVariantResponse(BaseModel):
    variant_id: int
    imei_serial: str
    barcode: Optional[str]
    condition: Optional[str] = None
    price: Optional[Decimal] = None
    quantity: Optional[int] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[date] = None
    is_sold: bool
    created_at: datetime
    attributes: List[ProductVariantAttributeResponse] = []
    
    class Config:
        from_attributes = True

# Schémas pour les produits
class ProductCreate(BaseModel):
    name: str
    description: Optional[str] = None
    quantity: int = 0
    price: Decimal
    wholesale_price: Optional[Decimal] = None
    purchase_price: Optional[Decimal] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    barcode: Optional[str] = None
    condition: Optional[str] = None  # default configurable; falls back to "neuf"
    has_unique_serial: bool = False
    unit: Optional[str] = None  # piece | kg | litre | sachet… (voir shop_profile.UNITES)
    entry_date: Optional[datetime] = None
    notes: Optional[str] = None
    source: Optional[str] = 'purchase'  # purchase | exchange | return | other
    supplier_id: Optional[int] = None  # Fournisseur du produit
    image_path: Optional[str] = None  # Chemin vers l'image du produit
    is_archived: Optional[bool] = False
    variants: List[ProductVariantCreate] = []

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[int] = None
    price: Optional[Decimal] = None
    wholesale_price: Optional[Decimal] = None
    purchase_price: Optional[Decimal] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    barcode: Optional[str] = None
    condition: Optional[str] = None
    has_unique_serial: Optional[bool] = None
    unit: Optional[str] = None
    entry_date: Optional[datetime] = None
    notes: Optional[str] = None
    source: Optional[str] = None  # purchase | exchange | return | other
    supplier_id: Optional[int] = None  # Fournisseur du produit
    image_path: Optional[str] = None  # Chemin vers l'image du produit
    is_archived: Optional[bool] = None
    variants: Optional[List[ProductVariantCreate]] = None

class ProductResponse(BaseModel):
    product_id: int
    name: str
    description: Optional[str]
    quantity: int
    price: Decimal
    wholesale_price: Optional[Decimal]
    purchase_price: Optional[Decimal]
    category: Optional[str]
    brand: Optional[str]
    model: Optional[str]
    barcode: Optional[str]
    condition: Optional[str] = None
    has_unique_serial: bool
    unit: Optional[str] = None
    entry_date: Optional[datetime]
    notes: Optional[str]
    image_path: Optional[str] = None
    source: Optional[str] = 'purchase'
    supplier_id: Optional[int] = None
    created_at: datetime
    is_archived: Optional[bool] = False
    variants: List[ProductVariantResponse] = []

    class Config:
        from_attributes = True

#
# Lightweight list models (to speed up listing by avoiding variant attributes loading)
#

class ProductVariantListItem(BaseModel):
    variant_id: int
    imei_serial: str
    barcode: Optional[str]
    condition: Optional[str] = None
    price: Optional[Decimal] = None
    quantity: Optional[int] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[date] = None
    is_sold: bool
    created_at: datetime

    class Config:
        from_attributes = True

class ProductListItem(BaseModel):
    product_id: int
    name: str
    description: Optional[str]
    quantity: int
    price: Decimal
    wholesale_price: Optional[Decimal]
    purchase_price: Decimal
    category: Optional[str]
    brand: Optional[str]
    model: Optional[str]
    barcode: Optional[str]
    condition: Optional[str] = None
    has_unique_serial: bool
    unit: Optional[str] = None
    entry_date: Optional[datetime]
    notes: Optional[str]
    image_path: Optional[str] = None  # Chemin vers l'image du produit
    source: Optional[str] = 'purchase'
    supplier_id: Optional[int] = None
    created_at: datetime
    is_archived: Optional[bool] = False
    # Champs légers pour l'affichage de la liste (optimisation)
    has_variants: Optional[bool] = None
    variants_available: Optional[int] = None
    variant_condition_counts: Optional[dict] = None
    # Ancien champ conservé pour rétro-compatibilité
    variants: List[ProductVariantListItem] = []

    class Config:
        from_attributes = True

# Schémas pour les mouvements de stock
class StockMovementCreate(BaseModel):
    product_id: int
    quantity: int
    movement_type: str  # IN, OUT
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    notes: Optional[str] = None
    unit_price: Optional[Decimal] = 0

class StockMovementResponse(BaseModel):
    movement_id: int
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    quantity: int
    movement_type: str
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    notes: Optional[str] = None
    unit_price: Optional[Decimal] = 0
    created_at: datetime
    
    class Config:
        from_attributes = True

# Schémas pour les fournisseurs
class SupplierCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None

class SupplierResponse(BaseModel):
    supplier_id: int
    name: str
    contact_person: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    address: Optional[str]
    
    class Config:
        from_attributes = True

# Schémas pour les devis
class QuotationItemCreate(BaseModel):
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Decimal
    total: Decimal

class QuotationItemResponse(BaseModel):
    item_id: int
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Decimal
    total: Decimal
    
    class Config:
        from_attributes = True

class QuotationCreate(BaseModel):
    quotation_number: str
    client_id: int
    date: datetime
    expiry_date: Optional[datetime] = None
    subtotal: Decimal
    tax_rate: Decimal = 18.00
    tax_amount: Decimal
    total: Decimal
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    external_notes: Optional[str] = None
    show_item_prices: bool = True
    show_section_totals: bool = True
    items: List[QuotationItemCreate]

class QuotationResponse(BaseModel):
    quotation_id: int
    quotation_number: str
    client_id: int
    date: datetime
    expiry_date: Optional[datetime]
    status: str
    is_sent: bool = False
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    external_notes: Optional[str] = None
    show_item_prices: bool = True
    show_section_totals: bool = True
    created_at: datetime
    invoice_id: Optional[int] = None
    items: List[QuotationItemResponse] = []
    
    class Config:
        from_attributes = True

# Schémas pour les factures
class InvoiceItemCreate(BaseModel):
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Decimal
    total: Decimal
    is_gift: Optional[bool] = False  # Article gratuit/cadeau
    variant_id: Optional[int] = None
    variant_imei: Optional[str] = None
    external_price: Optional[Decimal] = None  # Prix d'achat externe (optionnel)
    # Pour les produits entrants dans un échange (nouveau produit à créer)
    create_as_new_product: Optional[bool] = False
    new_product_category: Optional[str] = None
    new_product_condition: Optional[str] = None
    new_variant_imei: Optional[str] = None
    new_variant_barcode: Optional[str] = None

class InvoiceItemResponse(BaseModel):
    item_id: int
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Decimal
    total: Decimal
    is_gift: Optional[bool] = False  # Article gratuit/cadeau
    variant_id: Optional[int] = None  # unité précise vendue (produits à IMEI)
    variant_imei: Optional[str] = None
    external_price: Optional[Decimal] = None
    external_profit: Optional[Decimal] = None  # Bénéfice calculé
    
    class Config:
        from_attributes = True

class InvoiceExchangeItemCreate(BaseModel):
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Optional[Decimal] = None  # Prix pour les articles personnalisés
    category_id: Optional[int] = None  # Catégorie pour les nouveaux produits
    variant_id: Optional[int] = None
    variant_imei: Optional[str] = None
    barcode: Optional[str] = None  # Code barre pour les produits sans variantes
    notes: Optional[str] = None

class InvoiceExchangeItemResponse(BaseModel):
    exchange_item_id: int
    product_id: Optional[int] = None
    product_name: str
    quantity: int
    price: Optional[Decimal] = None
    variant_id: Optional[int] = None
    variant_imei: Optional[str] = None
    notes: Optional[str] = None
    
    class Config:
        from_attributes = True

class InvoiceCreate(BaseModel):
    invoice_number: str
    invoice_type: str = "normal"  # normal, exchange, flash_sale
    client_id: Optional[int] = None  # Optionnel pour les ventes flash
    quotation_id: Optional[int] = None
    date: datetime
    due_date: Optional[datetime] = None
    payment_method: Optional[str] = None
    subtotal: Decimal
    tax_rate: Decimal = 18.00
    tax_amount: Decimal
    total: Decimal
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    external_notes: Optional[str] = None
    show_tax: bool = True
    show_item_prices: bool = True
    show_section_totals: bool = True
    price_display: str = "TTC"
    # Champs de garantie
    has_warranty: bool = False
    warranty_duration: Optional[int] = None
    warranty_start_date: Optional[date] = None
    warranty_end_date: Optional[date] = None
    items: List[InvoiceItemCreate]
    exchange_items: Optional[List[InvoiceExchangeItemCreate]] = []  # Produits échangés (sortants)

class InvoiceResponse(BaseModel):
    invoice_id: int
    invoice_number: str
    invoice_type: str = "normal"
    client_id: Optional[int] = None
    client_name: str  # Ajouter le nom du client
    quotation_id: Optional[int]
    date: datetime
    due_date: Optional[datetime]
    status: str
    payment_method: Optional[str]
    subtotal: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    total: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
    notes: Optional[str] = None
    internal_notes: Optional[str] = None
    external_notes: Optional[str] = None
    show_tax: bool
    show_item_prices: bool = True
    show_section_totals: bool = True
    price_display: str
    # Champs de garantie
    has_warranty: bool = False
    warranty_duration: Optional[int] = None
    warranty_start_date: Optional[date] = None
    warranty_end_date: Optional[date] = None
    created_at: datetime
    items: List[InvoiceItemResponse] = []
    exchange_items: List[InvoiceExchangeItemResponse] = []
    
    class Config:
        from_attributes = True

# Schémas pour les catégories
class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None
    requires_variants: bool = False

class CategoryResponse(BaseModel):
    category_id: int
    name: str
    description: Optional[str]
    requires_variants: bool = False
    
    class Config:
        from_attributes = True

# Schémas pour les attributs de catégorie
class CategoryAttributeValueCreate(BaseModel):
    value: str
    code: Optional[str] = None
    sort_order: int = 0

class CategoryAttributeValueUpdate(BaseModel):
    value: Optional[str] = None
    code: Optional[str] = None
    sort_order: Optional[int] = None

class CategoryAttributeValueResponse(BaseModel):
    value_id: int
    value: str
    code: Optional[str]
    sort_order: int
    
    class Config:
        from_attributes = True

class CategoryAttributeCreate(BaseModel):
    name: str
    code: Optional[str] = None
    type: str = "select"  # select, multiselect, text, number, boolean
    required: bool = False
    multi_select: bool = False
    sort_order: int = 0
    values: List[CategoryAttributeValueCreate] = []

class CategoryAttributeUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    type: Optional[str] = None
    required: Optional[bool] = None
    multi_select: Optional[bool] = None
    sort_order: Optional[int] = None

class CategoryAttributeResponse(BaseModel):
    attribute_id: int
    category_id: int
    name: str
    code: Optional[str]
    type: str
    required: bool
    multi_select: bool
    sort_order: int
    values: List[CategoryAttributeValueResponse] = []
    
    class Config:
        from_attributes = True

# Schémas pour les fournisseurs (pour création rapide)
class SupplierQuickCreate(BaseModel):
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    


# Schémas pour les factures fournisseur (version simplifiée)
class SupplierInvoiceCreate(BaseModel):
    supplier_id: int
    invoice_number: str
    invoice_date: datetime
    due_date: Optional[datetime] = None
    description: Optional[str] = None  # Description simple du service/produit (optionnel)
    amount: Decimal  # Montant total de la facture
    paid_amount: Optional[Decimal] = 0  # Montant déjà payé
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    pdf_filename: Optional[str] = None  # Nom du fichier PDF uploadé

class SupplierInvoiceUpdate(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date: Optional[datetime] = None
    due_date: Optional[datetime] = None
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    payment_method: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    pdf_filename: Optional[str] = None

class SupplierInvoiceResponse(BaseModel):
    invoice_id: int
    supplier_id: int
    supplier_name: Optional[str] = None
    invoice_number: str
    invoice_date: datetime
    due_date: Optional[datetime]
    description: str
    amount: Decimal  # Montant total
    paid_amount: Decimal
    remaining_amount: Decimal
    status: str  # pending, partial, paid, overdue
    payment_method: Optional[str]
    notes: Optional[str]
    pdf_path: Optional[str] = None  # Chemin vers le PDF
    pdf_filename: Optional[str] = None  # Nom du fichier PDF
    items: Optional[list] = None  # Articles structurés en JSON
    created_at: datetime
    
    class Config:
        from_attributes = True

# Schémas pour les paiements de factures fournisseur
class SupplierInvoicePaymentCreate(BaseModel):
    amount: Decimal
    payment_date: datetime
    payment_method: str
    reference: Optional[str] = None
    notes: Optional[str] = None

class SupplierInvoicePaymentResponse(BaseModel):
    payment_id: int
    supplier_invoice_id: int
    amount: Decimal
    payment_date: datetime
    payment_method: str
    reference: Optional[str]
    notes: Optional[str]
    
    class Config:
        from_attributes = True

# Schémas pour Achats quotidiens
class DailyPurchaseCreate(BaseModel):
    date: date
    category: str
    supplier: Optional[str] = None
    description: Optional[str] = None
    amount: Decimal
    payment_method: str = 'espece'
    reference: Optional[str] = None

class DailyPurchaseUpdate(BaseModel):
    date: Optional[date] = None
    category: Optional[str] = None
    supplier: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[Decimal] = None
    payment_method: Optional[str] = None
    reference: Optional[str] = None

class DailyPurchaseResponse(BaseModel):
    id: int
    date: date
    category: str
    supplier: Optional[str]
    description: Optional[str]
    amount: Decimal
    payment_method: str
    reference: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# Schémas pour les demandes quotidiennes des clients
class DailyClientRequestCreate(BaseModel):
    client_id: Optional[int] = None
    client_name: str
    client_phone: Optional[str] = None
    product_description: str
    request_date: date
    status: str = "pending"
    notes: Optional[str] = None

class DailyClientRequestUpdate(BaseModel):
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    client_phone: Optional[str] = None
    product_description: Optional[str] = None
    request_date: Optional[date] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class DailyClientRequestResponse(BaseModel):
    request_id: int
    client_id: Optional[int]
    client_name: str
    client_phone: Optional[str]
    product_description: str
    request_date: date
    status: str
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Schémas pour les ventes quotidiennes
class DailySaleCreate(BaseModel):
    client_id: Optional[int] = None
    client_name: str
    product_id: Optional[int] = None
    product_name: str
    variant_id: Optional[int] = None
    variant_imei: Optional[str] = None
    variant_barcode: Optional[str] = None
    variant_condition: Optional[str] = None
    quantity: int = 1
    unit_price: Decimal
    total_amount: Decimal
    sale_date: date
    payment_method: str = "espece"
    invoice_id: Optional[int] = None
    notes: Optional[str] = None

class DailySaleUpdate(BaseModel):
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    variant_id: Optional[int] = None
    variant_imei: Optional[str] = None
    variant_barcode: Optional[str] = None
    variant_condition: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    sale_date: Optional[date] = None
    payment_method: Optional[str] = None
    invoice_id: Optional[int] = None
    notes: Optional[str] = None

class DailySaleResponse(BaseModel):
    sale_id: int
    client_id: Optional[int]
    client_name: str
    product_id: Optional[int]
    product_name: str
    variant_id: Optional[int]
    variant_imei: Optional[str]
    variant_barcode: Optional[str]
    variant_condition: Optional[str]
    quantity: int
    unit_price: Decimal
    total_amount: Decimal
    sale_date: date
    payment_method: str
    invoice_id: Optional[int]
    invoice_status: Optional[str] = None
    notes: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
