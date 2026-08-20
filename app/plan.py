"""Plan d'abonnement de l'instance.

Chaque client exploite sa propre instance : le plan souscrit est porté par la
variable d'environnement SUBSCRIPTION_PLAN (posée par la plateforme au
provisionnement, modifiée lors d'un changement de plan, puis redémarrage).
SUBSCRIPTION_STATUS=suspended coupe l'accès en cas d'impayé sans toucher aux
données. L'application n'a donc aucune logique de facturation : elle applique.

Le cloisonnement se fait en un point unique — le middleware ci-dessous, qui
couvre pages ET API par préfixe d'URL — plus le masquage dans la barre
latérale (global Jinja `plan_has`) et la limite de comptes à l'inscription.
"""

import os

PLAN_FEATURES = {
    "essentiel": {"core"},
    "pro": {"core", "barcode", "maintenance", "whatsapp", "reports", "sheets", "backups"},
    "business": {
        "core", "barcode", "maintenance", "whatsapp", "reports", "sheets",
        "backups", "shop", "auto_reminders",
    },
}

# Comptes utilisateurs actifs autorisés (None = illimité).
PLAN_USER_LIMITS = {"essentiel": 1, "pro": 3, "business": None}

# Une valeur inconnue retombe sur "business" : une instance mal configurée
# doit rester utilisable, pas amputée.
_raw = os.getenv("SUBSCRIPTION_PLAN", "business").strip().lower()
PLAN_CODE = _raw if _raw in PLAN_FEATURES else "business"

SUBSCRIPTION_STATUS = os.getenv("SUBSCRIPTION_STATUS", "active").strip().lower()

PLAN_LABELS = {"essentiel": "Essentiel", "pro": "Pro", "business": "Business"}

FEATURE_LABELS = {
    "barcode": "le générateur de codes-barres",
    "maintenance": "l'atelier / SAV",
    "whatsapp": "l'envoi WhatsApp",
    "reports": "les rapports avancés",
    "sheets": "la synchronisation Google Sheets",
    "backups": "les sauvegardes",
    "shop": "la boutique en ligne",
    "auto_reminders": "les relances automatiques",
}

# Préfixes d'URL gardés (pages et API confondues). L'ordre n'importe pas :
# un préfixe correspond si le chemin lui est égal ou le prolonge par « / ».
FEATURE_PATHS = (
    ("/api/whatsapp", "whatsapp"),
    ("/whatsapp", "whatsapp"),
    ("/barcode-generator", "barcode"),
    ("/api/maintenances", "maintenance"),
    ("/maintenances", "maintenance"),
    ("/api/reports", "reports"),
    ("/reports", "reports"),
    ("/api/daily-recap", "reports"),
    ("/daily-recap", "reports"),
    ("/api/google-sheets", "sheets"),
    ("/google-sheets-sync", "sheets"),
    ("/api/backup", "backups"),
    ("/api/shop", "shop"),
    ("/boutique", "shop"),
    ("/e-commerce", "shop"),
)

# Chemins toujours servis quand l'abonnement est suspendu : de quoi afficher
# la page d'information et laisser l'administrateur se connecter au retour.
SUSPENDED_ALLOWED_PREFIXES = ("/static", "/login", "/api/auth", "/favicon.ico", "/api")


def has_feature(key: str) -> bool:
    return key in PLAN_FEATURES[PLAN_CODE]


def user_limit():
    return PLAN_USER_LIMITS[PLAN_CODE]


def plan_label() -> str:
    return PLAN_LABELS[PLAN_CODE]


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def feature_for_path(path: str):
    """Fonctionnalité exigée par ce chemin, ou None s'il est libre."""
    for prefix, feature in FEATURE_PATHS:
        if _matches(path, prefix):
            return feature
    return None


def is_suspended() -> bool:
    return SUBSCRIPTION_STATUS == "suspended"


def suspended_path_allowed(path: str) -> bool:
    # /api reste servi en 403 JSON par le middleware (pas de page HTML), mais
    # /api/auth doit fonctionner pour que la déconnexion/connexion reste saine.
    return any(_matches(path, p) for p in ("/static", "/login", "/favicon.ico")) or _matches(path, "/api/auth")
