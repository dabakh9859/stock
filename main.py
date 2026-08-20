from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse, Response, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from starlette.responses import Response as StarletteResponse
from io import BytesIO
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.orm import joinedload
import uvicorn
import hashlib
import os
import secrets
import jinja2
from dotenv import load_dotenv
import json
import re
from datetime import date, datetime

# Charger les variables d'environnement
load_dotenv()

# Version d'assets pour bust de cache (commit SHA si fourni par la plateforme, sinon variable ou timestamp)
def get_asset_version():
    """Génère une version basée sur le timestamp de modification des fichiers statiques"""
    # En production, utiliser le commit SHA si disponible
    commit_sha = os.getenv("GIT_COMMIT_SHA") or os.getenv("KOYEB_COMMIT_SHA") or os.getenv("ASSET_VERSION")
    if commit_sha:
        return commit_sha[:12]
    
    # En développement, utiliser le timestamp de modification le plus récent parmi static/js, static/css et templates
    try:
        latest_mtime = 0
        for rel, exts in [(os.path.join("static", "js"), (".js",)),
                          (os.path.join("static", "css"), (".css",)),
                          ("templates", (".html",))]:
            if os.path.exists(rel):
                for fn in os.listdir(rel):
                    if fn.endswith(exts):
                        fp = os.path.join(rel, fn)
                        try:
                            m = os.path.getmtime(fp)
                            if m > latest_mtime:
                                latest_mtime = m
                        except Exception:
                            pass
        if latest_mtime > 0:
            return str(int(latest_mtime))
    except Exception:
        pass
    
    # Fallback: timestamp actuel
    return str(int(datetime.now().timestamp()))

ASSET_VERSION = get_asset_version()

# Imports de l'application
from app.database import get_db
from app.database import Invoice, UserSettings, Product, DeliveryNote, DeliveryNoteItem, Client
import re
try:
    # Legacy settings model (template-application) for fallback of company info/logo
    from app.models.models import Settings as LegacySettings  # type: ignore
except Exception:
    LegacySettings = None  # type: ignore
from app.routers import auth, products, clients, stock_movements, invoices, quotations, suppliers, debts, delivery_notes, bank_transactions, reports, user_settings, migrations, cache, dashboard, supplier_invoices, daily_recap, daily_purchases, daily_requests, daily_sales, google_sheets, client_debts, backup, maintenances, arrivals
from app.init_db import init_database
from app.auth import get_current_user
from app.services.migration_processor import migration_processor
try:
    from app.services.debt_notifier import debt_notifier
except Exception:
    debt_notifier = None  # type: ignore
try:
    from app.services.warranty_notifier import warranty_notifier
except Exception:
    warranty_notifier = None  # type: ignore
try:
    from app.services.maintenance_notifier import maintenance_notifier
except Exception:
    maintenance_notifier = None  # type: ignore

# Créer l'application FastAPI
app = FastAPI(
    title="Stock - Gestion de Stock",
    description="Application de gestion de stock et facturation avec FastAPI et Bootstrap",
    version="1.0.0",
    # Préfixe de montage (vide en production, "/v2" en recette). Indispensable :
    # sans lui, les redirections automatiques de slash final de FastAPI renvoient
    # un chemin sans préfixe — le navigateur suivrait vers l'app de production.
    root_path=os.getenv("URL_PREFIX", "").rstrip("/"),
)

# Configuration CORS pour la boutique en ligne (domaine séparé)
# Ajouter votre domaine de boutique dans STORE_DOMAIN
STORE_DOMAIN = os.getenv("STORE_DOMAIN", "http://localhost:3000")
ALLOWED_ORIGINS = [
    STORE_DOMAIN,
    "http://localhost:3000",
    "http://localhost:3001",
    "https://stockv2.example.com",
    "http://stockv2.example.com",
    "https://boutique.votredomaine.com",  # Remplacer par votre domaine
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# --- Cloisonnement par plan d'abonnement (voir app/plan.py) -----------------
# Un point unique pour pages et API : le préfixe d'URL décide de la
# fonctionnalité exigée. Les routers n'ont pas besoin d'être modifiés.
from app import plan as subscription_plan


@app.middleware("http")
async def enforce_subscription(request, call_next):
    path = request.url.path
    if URL_PREFIX and path.startswith(URL_PREFIX):
        path = path[len(URL_PREFIX):] or "/"

    if subscription_plan.is_suspended():
        if not subscription_plan.suspended_path_allowed(path):
            if path.startswith("/api"):
                return JSONResponse(
                    status_code=403,
                    content={"error": "subscription_suspended",
                             "message": "Abonnement suspendu — accès en pause."},
                )
            return templates.TemplateResponse(
                "suspended.html",
                {"request": request, "app_name": os.getenv("APP_NAME", "Stock")},
                status_code=503,
            )
        return await call_next(request)

    feature = subscription_plan.feature_for_path(path)
    if feature and not subscription_plan.has_feature(feature):
        if path.startswith("/api"):
            return JSONResponse(
                status_code=403,
                content={"error": "feature_not_in_plan", "feature": feature,
                         "plan": subscription_plan.PLAN_CODE,
                         "message": "Fonctionnalité non incluse dans votre plan."},
            )
        return templates.TemplateResponse(
            "upgrade.html",
            {
                "request": request,
                "app_name": os.getenv("APP_NAME", "Stock"),
                "plan_label": subscription_plan.plan_label(),
                "feature_label": subscription_plan.FEATURE_LABELS.get(feature, "cette fonctionnalité"),
                "upgrade_url": os.getenv("PLATFORM_URL", "http://localhost:9000") + "/#tarifs",
                "url_prefix": URL_PREFIX,
            },
            status_code=403,
        )
    return await call_next(request)

# (Optionnel) Middleware proxy enlevé pour compatibilité starlette; la baseURL côté frontend force déjà HTTPS

# Middleware to remove CSP headers for print routes (runs first, before cache middleware)
@app.middleware("http")
async def remove_csp_for_print_routes(request, call_next):
    response = await call_next(request)
    
    # Check if route marked itself to skip CSP headers
    if response.headers.get("X-Skip-CSP") == "true":
        if "Content-Security-Policy" in response.headers:
            del response.headers["Content-Security-Policy"]
        if "Strict-Transport-Security" in response.headers:
            del response.headers["Strict-Transport-Security"]
        if "X-Skip-CSP" in response.headers:
            del response.headers["X-Skip-CSP"]
    
    return response

# Middleware de gestion du cache: HTML non cache, assets statiques fortement cacheés
@app.middleware("http")
async def cache_headers_middleware(request, call_next):
    path = request.url.path or ""
    is_print_route = path.startswith("/invoices/print/") or path.startswith("/quotations/print/")
    
    response = await call_next(request)
    
    # Debug logging for print routes
    if is_print_route:
        client_host = request.client.host if request.client else "NO_CLIENT"
        print(f"DEBUG CSP BEFORE TRY: path={path}, client_host={client_host}, headers_before={list(response.headers.keys())}")
    
    try:
        content_type = (response.headers.get("content-type", "") or "").lower()
        if path.startswith("/static/") or path == "/favicon.ico":
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        
        # Help browsers auto-upgrade any stray http resources to https and enable HSTS
        # Skip security headers for Docker internal network requests or routes marked with X-Skip-CSP
        client_host = request.client.host if request.client else ""
        is_docker_internal = client_host.startswith("172.") or client_host.startswith("192.168.")
        skip_csp = response.headers.get("X-Skip-CSP") == "true" or is_docker_internal
        
        # Debug: log for print routes
        if path.startswith("/invoices/print/") or path.startswith("/quotations/print/"):
            print(f"DEBUG CSP: path={path}, client_host={client_host}, is_docker_internal={is_docker_internal}, skip_csp={skip_csp}")
        
        if not skip_csp:
            is_localhost = (
                client_host in ["127.0.0.1", "localhost"] or
                request.headers.get("host", "").startswith(("localhost:", "127.0.0.1:", "app:"))
            )
            if not is_localhost:
                response.headers.setdefault("Content-Security-Policy", "upgrade-insecure-requests")
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")
    except Exception:
        # En cas de souci, on n'empêche pas la réponse de sortir
        pass
    return response

# Initialiser la base de données au démarrage (désactivé par défaut en déploiement)
@app.on_event("startup")
async def startup_event():
    try:
        should_init = os.getenv("INIT_DB_ON_STARTUP", "false").lower() == "true"
        if should_init:
            print("⚙️ INIT_DB_ON_STARTUP=true → initialisation de la base autorisée")
            init_database()
        else:
            print("⏭️ INIT_DB_ON_STARTUP!=true → saut de l'initialisation de la base (aucune écriture)")
        # Démarrer le processeur de migrations en arrière-plan (désactivé par défaut)
        if os.getenv("ENABLE_MIGRATIONS_WORKER", "false").lower() == "true":
            migration_processor.start_background_processor()
        else:
            print("⏭️ ENABLE_MIGRATIONS_WORKER!=true → worker migrations non démarré")
        # Les relances automatiques sont réservées au plan Business : la
        # variable d'activation ne suffit pas si le plan ne les inclut pas.
        _reminders_allowed = subscription_plan.has_feature("auto_reminders")
        if not _reminders_allowed:
            print(f"⏭️ Plan {subscription_plan.PLAN_CODE} → relances automatiques non démarrées")
        # Démarrer le notificateur de créances en retard si activé
        if _reminders_allowed and os.getenv("ENABLE_DEBT_REMINDERS", "false").lower() == "true":
            if debt_notifier is not None:
                debt_notifier.start_background()
                print("✅ Notificateur de créances démarré")
        # Démarrer le notificateur de garanties si activé
        if _reminders_allowed and os.getenv("ENABLE_WARRANTY_REMINDERS", "false").lower() == "true":
            if warranty_notifier is not None:
                warranty_notifier.start_background()
                print("✅ Notificateur de garanties démarré")
        # Démarrer le notificateur de maintenances si activé
        if _reminders_allowed and os.getenv("ENABLE_MAINTENANCE_REMINDERS", "false").lower() == "true":
            if maintenance_notifier is not None:
                maintenance_notifier.start_background()
                print("✅ Notificateur de maintenances démarré")
        # Démarrer le watchdog WhatsApp (à couper sur une copie de recette :
        # deux instances qui surveillent la même instance Evolution se battraient
        # pour la reconnexion)
        if os.getenv("ENABLE_WA_WATCHDOG", "true").lower() == "true":
            start_wa_watchdog()
        else:
            print("⏭️ ENABLE_WA_WATCHDOG!=true → watchdog WhatsApp non démarré")
        print("✅ Application démarrée avec succès")
    except Exception as e:
        print(f"❌ Erreur lors du démarrage: {e}")

# Arrêter le processeur au shutdown
@app.on_event("shutdown")
async def shutdown_event():
    try:
        # Arrêter uniquement si le worker était activé
        if os.getenv("ENABLE_MIGRATIONS_WORKER", "false").lower() == "true":
            migration_processor.stop_background_processor()
        if os.getenv("ENABLE_DEBT_REMINDERS", "false").lower() == "true" and debt_notifier is not None:
            debt_notifier.stop_background()
        if os.getenv("ENABLE_WARRANTY_REMINDERS", "false").lower() == "true" and warranty_notifier is not None:
            warranty_notifier.stop_background()
        if os.getenv("ENABLE_MAINTENANCE_REMINDERS", "false").lower() == "true" and maintenance_notifier is not None:
            maintenance_notifier.stop_background()
        stop_wa_watchdog()
        print("✅ Application arrêtée proprement")
    except Exception as e:
        print(f"❌ Erreur lors de l'arrêt: {e}")

# Configuration des templates et fichiers statiques
templates = Jinja2Templates(directory="templates")
# Exposer une fonction globale de version pour le cache-busting des assets (dynamique)
templates.env.globals["ASSET_VERSION"] = get_asset_version

# Préfixe de montage de l'application.
#
# Vide en production (l'app est servie à la racine de localhost), "/v2" sur la
# copie de recette servie sous localhost/v2. nginx retire le préfixe avant de
# transmettre la requête : les routes ci-dessous restent donc inchangées, seuls
# les liens *générés* (templates et JavaScript) ont besoin de le porter.
URL_PREFIX = os.getenv("URL_PREFIX", "").rstrip("/")
templates.env.globals["URL_PREFIX"] = URL_PREFIX

# Plan d'abonnement : la barre latérale masque ce que le plan n'inclut pas,
# le middleware ci-dessus bloque quand même l'accès direct par URL.
templates.env.globals["plan_has"] = subscription_plan.has_feature
templates.env.globals["plan_code"] = subscription_plan.PLAN_CODE

# Profil de boutique : le métier du commerçant décide des mots employés à
# l'écran (« IMEI » ou « Taille / Couleur ») et des modules affichés.
#
#   {{ mot('identifiant_court') }}      → « IMEI » en téléphonie
#   {% if metier('atelier') %}…{% endif %}
#
# `metier()` consulte aussi le plan d'abonnement : un module hors plan reste
# masqué même si le profil le prévoit.
from app import shop_profile  # noqa: E402  (après le chargement de la config)

templates.env.globals["mot"] = shop_profile.libelle
templates.env.globals["metier"] = shop_profile.module_actif
templates.env.globals["profil_boutique"] = shop_profile.profil_courant
templates.env.globals["unites_boutique"] = shop_profile.unites_proposees


def app_path(path):
    """Préfixe un chemin absolu de l'application.

    Pour les chemins qui ne sont pas écrits en dur dans les gabarits mais lus en
    base (logo, favicon des réglages) : `{{ value | app_path }}`. Les URL externes
    et les data: URI passent inchangées.
    """
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return path
    return f"{URL_PREFIX}{path}"


templates.env.filters["app_path"] = app_path


def nav_visibles(items):
    """Les entrées de menu réellement affichables, dans l'ordre.

    Deux filtres, qui ne disent pas la même chose : `feature` est ce que
    l'abonnement couvre, `metier` ce qui a un sens dans ce commerce. Rendu comme
    filtre et non comme test en ligne dans le gabarit pour que la barre puisse
    aussi savoir si un groupe se retrouve **vide** — un groupe sans entrée
    visible ne doit pas sortir un menu déroulant qui ne s'ouvre sur rien.
    """
    visibles = []
    for item in items or []:
        fonctionnalite = item.get("feature")
        if fonctionnalite and not subscription_plan.has_feature(fonctionnalite):
            continue
        module = item.get("metier")
        if module and not shop_profile.module_actif(module):
            continue
        visibles.append(item)
    return visibles


templates.env.filters["nav_visibles"] = nav_visibles


@jinja2.pass_context
def is_current(context, path: str, exact: bool = False) -> bool:
    """La page affichée correspond-elle à `path` ? (pour marquer le lien actif)

    Passe par une fonction globale et non par une variable de gabarit : les macros
    Jinja ne voient pas les variables posées par `{% set %}` autour d'elles, mais
    elles peuvent appeler les globales. `pass_context` donne accès à `request`.

    On lit `scope["path"]` et non `request.url.path` : Starlette concatène le
    root_path dans `url.path` (« /v2/products »), alors que `scope["path"]` est le
    chemin nu utilisé pour le routage (« /products »), quel que soit le préfixe.
    """
    request = context.get("request")
    if request is None:
        return False
    current = request.scope.get("path", "")
    if exact:
        return current == path
    return current == path or current.startswith(f"{path}/")


templates.env.globals["is_current"] = is_current


@jinja2.pass_context
def is_current_any(context, *paths: str) -> bool:
    """L'un de ces chemins correspond-il à la page affichée ?

    Sert à marquer le menu déroulant qui contient la page courante, sans avoir à
    répéter la logique de comparaison dans le gabarit.
    """
    return any(is_current(context, path) for path in paths)


templates.env.globals["is_current_any"] = is_current_any


@jinja2.pass_context
def nav_user(context) -> dict:
    """Identité de l'utilisateur connecté, pour rendre la barre latérale juste
    dès la première peinture.

    Sans cela, le gabarit sortait les groupes réservés à l'administration en
    `display:none` et un script les révélait après la réponse de `/api/auth/me`.
    À chaque changement de page la barre se réassemblait donc sous les yeux :
    « Finances » apparaissait 75 à 450 ms après le rendu et poussait les groupes
    suivants vers le bas. Le cookie `gt_access` porte déjà le rôle et le nom,
    et sa lecture ne coûte aucune requête en base.

    Renvoie un dictionnaire vide si le cookie est absent ou invalide : le
    gabarit retombe alors sur l'ancien comportement (masqué, puis révélé par le
    script), qui reste correct — seulement moins fluide.
    """
    request = context.get("request")
    if request is None:
        return {}
    token = request.cookies.get("gt_access")
    if not token:
        return {}
    try:
        from app.auth import verify_token
        claims = verify_token(token) or {}
    except Exception:
        return {}
    if not claims:
        return {}
    role = claims.get("role", "user")
    return {
        "username": claims.get("sub"),
        "full_name": claims.get("full_name"),
        "role": role,
        "is_admin": role == "admin",
        # Même libellé que celui appliqué ensuite par le script de base.html,
        # pour que le texte ne change pas après coup.
        "role_label": {"admin": "Admin", "manager": "Manager", "user": "Utilisateur"}.get(role, role),
    }


templates.env.globals["nav_user"] = nav_user

# ---- Jinja filters ----
def _format_number(value) -> str:
    try:
        # Support Decimal, int, float; round to 0 decimals for CFA display
        n = float(value or 0)
        text = f"{n:,.0f}"
        # Replace commas with spaces for French-style grouping
        return text.replace(",", " ")
    except Exception:
        try:
            return str(int(value))
        except Exception:
            return str(value or 0)

templates.env.filters["format_number"] = _format_number

def _format_cfa(value) -> str:
    return f"{_format_number(value)} F CFA"

templates.env.filters["format_cfa"] = _format_cfa

def _format_date_no_time(value) -> str:
    """Formate une date au format français: jour mois année (ex: 31 décembre 2025)"""
    # Mapping des mois en français
    mois_fr = {
        1: "janvier", 2: "février", 3: "mars", 4: "avril",
        5: "mai", 6: "juin", 7: "juillet", 8: "août",
        9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre"
    }
    
    try:
        if value is None:
            return ""
        if isinstance(value, (datetime, date)):
            # Format français: jour mois année (ex: 31 décembre 2025)
            jour = value.day
            mois_nom = mois_fr.get(value.month, "")
            annee = value.year
            return f"{jour} {mois_nom} {annee}"
        
        s = str(value)
        # Si c'est déjà une date formatée, essayer de la convertir
        try:
            dt = None
            # Tenter de parser différents formats et reformater
            if "T" in s:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            elif "-" in s and len(s.split("-")) == 3:
                # Format YYYY-MM-DD ou DD/MM/YYYY
                date_part = s.split(" ")[0]
                if "/" in date_part:
                    # Format DD/MM/YYYY
                    try:
                        dt = datetime.strptime(date_part, "%d/%m/%Y")
                    except:
                        pass
                if dt is None:
                    # Format YYYY-MM-DD
                    dt = datetime.strptime(date_part, "%Y-%m-%d")
            
            if dt:
                jour = dt.day
                mois_nom = mois_fr.get(dt.month, "")
                annee = dt.year
                return f"{jour} {mois_nom} {annee}"
        except Exception:
            pass
        return s.split(" ")[0] if " " in s else s
    except Exception:
        try:
            return str(value).split(" ")[0]
        except Exception:
            return str(value or "")

templates.env.filters["format_date"] = _format_date_no_time

def _format_phone_sn(value) -> str:
    try:
        if value is None:
            return ""
        s = str(value).strip()
        if not s:
            return ""
        # Garder uniquement chiffres et '+'
        s = s.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        # Convertir les formes 00+ en +
        if s.startswith("00"):
            s = "+" + s[2:]
        # Extraire uniquement les chiffres
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            return str(value)

        # Normaliser Sénégal +221
        if digits.startswith("221"):
            local = digits[3:]
        elif digits.startswith("0"):
            local = digits[1:]
        else:
            local = digits

        # Garder 9 chiffres si possible
        if len(local) > 9:
            local = local[-9:]

        if len(local) == 9:
            # Format: +221 77 XXX XX XX
            a = local[0:2]
            b = local[2:5]
            c = local[5:7]
            d = local[7:9]
            return f"+221 {a} {b} {c} {d}"

        # Fallback si longueur inattendue
        return f"+221 {local}" if local else f"+221 {digits}"
    except Exception:
        return str(value or "")

templates.env.filters["format_phone_sn"] = _format_phone_sn

def _replace_regex(value, pattern, replacement):
    try:
        if value is None:
            return ""
        return re.sub(pattern, replacement, str(value), flags=re.S)
    except Exception:
        return value

templates.env.filters["replace_regex"] = _replace_regex

def _normalize_logo(logo_value: str | None) -> str | None:
    try:
        if not logo_value:
            return None
        s = str(logo_value).strip()
        if not s:
            return None
        # Already a proper URL or data URI
        if s.startswith("data:image") or s.startswith("http://") or s.startswith("https://") or s.startswith("/"):
            return s
        # Heuristic: base64 without header → wrap as PNG by default
        if len(s) > 64:
            return f"data:image/png;base64,{s}"
        return s
    except Exception:
        return logo_value
app.mount("/static", StaticFiles(directory="static"), name="static")
# nginx retire le préfixe avant de transmettre, donc /static suffit pour le trafic
# navigateur. On monte aussi la version préfixée pour les accès qui court-circuitent
# nginx — la génération de PDF va chercher les pages sur APP_INTERNAL_URL en direct.
if URL_PREFIX:
    app.mount(f"{URL_PREFIX}/static", StaticFiles(directory="static"), name="static_prefixed")

# Inclure les routers API de l'application de gestion
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(clients.router)
app.include_router(stock_movements.router)
app.include_router(invoices.router)
app.include_router(quotations.router)
app.include_router(suppliers.router)
app.include_router(arrivals.router)
app.include_router(supplier_invoices.router)
app.include_router(debts.router)
app.include_router(client_debts.router)
app.include_router(backup.router)
# Désactivation de la page Bons de Livraison
app.include_router(bank_transactions.router)
app.include_router(reports.router)
app.include_router(user_settings.router)
app.include_router(migrations.router)
app.include_router(cache.router)
app.include_router(dashboard.router)
app.include_router(daily_recap.router)
app.include_router(daily_purchases.router)
app.include_router(daily_requests.router)
app.include_router(daily_sales.router)
app.include_router(google_sheets.router)
app.include_router(maintenances.router)
from app.routers import assistant as assistant_router
app.include_router(assistant_router.router)
from app.routers import shop_profile as shop_profile_router
app.include_router(shop_profile_router.router)
from app.routers import lots as lots_router
app.include_router(lots_router.router)
from app.routers import declinaisons as declinaisons_router
app.include_router(declinaisons_router.router)
from app.routers import email_settings as email_settings_router
app.include_router(email_settings_router.router)
from app.routers import product_duplicates as product_duplicates_router
from app.routers import shop_banners as shop_banners_router
from app.routers import shop_payments as shop_payments_router
app.include_router(shop_payments_router.router)
app.include_router(shop_banners_router.router)
app.include_router(product_duplicates_router.router)

# Boutique en ligne: API publique + administration depuis la gestion de stock
from app.routers import shop as shop_router, shop_admin as shop_admin_router, shop_customer as shop_customer_router
from app.routers import shop_variants_admin as shop_variants_admin_router
app.include_router(shop_router.router)
app.include_router(shop_admin_router.router)
app.include_router(shop_customer_router.router)
app.include_router(shop_variants_admin_router.router)

# Inclure les routers API de la boutique en ligne (API publique)
# TODO: Activer quand le module boutique sera disponible
# from boutique.backend.routers import (
#     products_router,
#     customers_router,
#     cart_router,
#     orders_router,
#     payments_router
# )
# app.include_router(products_router)
# app.include_router(customers_router)
# app.include_router(cart_router)
# app.include_router(orders_router)
# app.include_router(payments_router)

# Route pour le favicon
@app.get("/favicon.ico")
async def favicon(db: Session = Depends(get_db)):
    settings = _load_company_settings(db)
    favicon_url = settings.get("favicon")
    if favicon_url:
        # Si c'est un chemin local statique (ex: /static/uploads/favicons/...)
        if favicon_url.startswith("/static/"):
            # Retirer le leading slash pour l'accès disque local
            local_path = favicon_url.lstrip("/")
            # Nettoyer d'éventuels query params (versioning) pour le check disque
            clean_path = local_path.split("?")[0]
            if os.path.exists(clean_path):
                return FileResponse(clean_path)
    # Fallback par défaut
    return FileResponse("static/favicon.ico")

# Route API de test
@app.get("/api")
async def api_status():
    return {
        "message": "API Stock",
        "status": "running",
        "version": "1.0.0",
        "framework": "FastAPI"
    }

# Endpoint de version pour live-reload
@app.get("/__live/version")
async def live_version():
    return {"v": get_asset_version()}

# ==================== PROTECTION DES PAGES ====================
#
# Défini ici, avant la première route qui s'en sert : `Depends(...)` est évalué
# au moment où la fonction est décorée, pas à l'appel. Une garde déclarée plus
# bas dans le fichier ferait échouer l'import.
#
# Toutes les pages de l'application sont protégées, à deux exceptions près :
# `/login`, qui se redirigerait vers elle-même, et le site `/e-commerce`, où le
# client arrive sans compte.

def require_page_login(request: Request, db: Session = Depends(get_db)):
    """
    Protège une page côté serveur. L'authentification s'appuie sur le cookie
    HttpOnly `gt_access`, ce qui fonctionne au chargement de page — contrairement
    à un jeton gardé en localStorage.

    Renvoie vers /login au lieu d'un 401 JSON: on sert une page, pas une API.
    """
    from app.auth import get_current_user
    try:
        return get_current_user(authorization=None, gt_access=request.cookies.get("gt_access"), db=db)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            # Le préfixe est indispensable : sans lui, la recette montée sous
            # /v2 renvoie vers la connexion de PRODUCTION, dont le jeton ne
            # vaut rien ici (chaque instance a sa propre SECRET_KEY).
            # En production URL_PREFIX est vide : le chemin reste "/login".
            headers={"Location": f"{URL_PREFIX}/login?next={request.url.path}"},
        )


# Jeton des appels serveur à serveur. À défaut d'être fourni, il est **dérivé de
# SECRET_KEY** et posé dans l'environnement du processus, où tous les appelants
# internes le lisent par `os.getenv`.
#
# Dérivé, et non tiré au hasard : plusieurs ouvriers uvicorn partagent SECRET_KEY
# mais pas leur mémoire. Un jeton aléatoire par processus donnerait des PDF qui
# échouent une fois sur quatre, selon l'ouvrier qui sert la page d'impression —
# le genre de panne qu'on met des jours à reproduire.
#
# Sans ce repli, une instance où le réglage a été oublié verrait ses PDF cesser
# de se générer le jour où les pages d'impression ont été fermées. Le définir
# explicitement reste nécessaire dès qu'un **autre** service appelle (le site
# e-commerce, par exemple), puisqu'il ne connaît pas cette clé.
if not (os.getenv("INTERNAL_API_TOKEN") or "").strip():
    _cle = os.getenv("SECRET_KEY", "")
    os.environ["INTERNAL_API_TOKEN"] = hashlib.sha256(
        f"jeton-interne:{_cle}".encode()).hexdigest()
    print("ℹ️ INTERNAL_API_TOKEN non défini : dérivé de SECRET_KEY "
          "(à définir explicitement si un autre service appelle cette instance)")


def require_page_or_internal(request: Request, db: Session = Depends(get_db)):
    """Protège une page d'impression, sans casser la génération de PDF.

    Ces pages étaient ouvertes : n'importe qui pouvait lire la facture d'un
    client en devinant son numéro. Les fermer d'un `require_page_login` aurait
    en revanche cassé les PDF — c'est un Chromium local qui va chercher l'URL
    (voir `_generate_pdf_from_url`), et il n'a pas le cookie de session.

    Deux voies légitimes, donc, les mêmes que pour `/api/whatsapp/*` :
    le cookie d'une personne connectée, ou le jeton interne présenté par nos
    propres services. Playwright pose désormais cet en-tête sur la page qu'il
    charge.
    """
    jeton = (os.getenv("INTERNAL_API_TOKEN", "") or "").strip()
    fourni = (request.headers.get("X-Internal-Token") or "").strip()
    if jeton and fourni and secrets.compare_digest(fourni, jeton):
        return {"interne": True}
    return require_page_login(request, db)


# Routes pour l'interface web
# Page d'accueil: Dashboard classique avec barre de navigation
@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "global_settings": _load_company_settings(db)})

# Interface Desktop accessible via /desktop (interface avec fenêtres type macOS)
@app.get("/desktop", response_class=HTMLResponse)
async def desktop_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    return templates.TemplateResponse("desktop.html", {"request": request, "global_settings": _load_company_settings(db)})

# Alias /dashboard pour compatibilité
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_alias(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    """Page de connexion"""
    return templates.TemplateResponse("login.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des produits"""
    return templates.TemplateResponse("products.html", {"request": request, "global_settings": _load_company_settings(db)})


# ==================== BOUTIQUE EN LIGNE (site public) ====================

@app.get("/e-commerce", response_class=HTMLResponse)
async def shop_home(request: Request, db: Session = Depends(get_db)):
    """Page d'accueil du site e-commerce"""
    from app.routers.shop import get_all_settings
    return templates.TemplateResponse("shop/index.html", {
        "request": request,
        "settings": get_all_settings(db),
    })


@app.get("/e-commerce/produits", response_class=HTMLResponse)
async def shop_catalog(request: Request, db: Session = Depends(get_db)):
    """Catalogue public"""
    from app.routers.shop import get_all_settings
    return templates.TemplateResponse("shop/catalog.html", {
        "request": request,
        "settings": get_all_settings(db),
    })


@app.get("/e-commerce/produit/{product_id}", response_class=HTMLResponse)
async def shop_product_detail(request: Request, product_id: int, db: Session = Depends(get_db)):
    """Fiche produit publique"""
    from app.routers.shop import get_all_settings, get_product
    try:
        data = get_product(product_id, db)
    except HTTPException:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    related = data.pop("related", [])
    return templates.TemplateResponse("shop/product.html", {
        "request": request,
        "settings": get_all_settings(db),
        "product": data,
        "product_json": json.dumps(data, default=str),
        "related_json": json.dumps(related, default=str),
    })


@app.get("/e-commerce/panier", response_class=HTMLResponse)
async def shop_cart(request: Request, db: Session = Depends(get_db)):
    """Panier et validation de commande"""
    from app.routers.shop import get_all_settings
    return templates.TemplateResponse("shop/cart.html", {
        "request": request,
        "settings": get_all_settings(db),
    })


@app.get("/e-commerce/suivi", response_class=HTMLResponse)
async def shop_tracking(request: Request, db: Session = Depends(get_db)):
    """Suivi public d'une commande"""
    from app.routers.shop import get_all_settings
    return templates.TemplateResponse("shop/tracking.html", {
        "request": request,
        "settings": get_all_settings(db),
    })


# ==================== ACCÈS AUX ROUTES WHATSAPP ====================

INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "").strip()
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "gt_access")


def require_whatsapp_access(request: Request, db: Session = Depends(get_db)):
    """Réserve les routes `/api/whatsapp/*` — deux voies légitimes, pas une de plus.

    Ces routes étaient ouvertes à Internet sans aucun contrôle : n'importe qui
    pouvait écrire depuis le numéro de la boutique, récupérer le QR d'appairage
    pour rattacher son propre téléphone, déconnecter l'instance, ou faire charger
    une URL interne par le Chromium du serveur. Fermé le 13/08/2026.

    Deux appelants légitimes :
    - l'écran `/whatsapp`, depuis le navigateur, avec le cookie de session ;
    - les services de l'application (factures, devis, rappels, boutique) et le
      site e-commerce, qui appellent en local et présentent `X-Internal-Token`.

    La comparaison du jeton est à temps constant : une égalité naïve laisse
    deviner le secret caractère par caractère.
    """
    jeton = (request.headers.get("X-Internal-Token") or "").strip()
    if INTERNAL_API_TOKEN and jeton and secrets.compare_digest(jeton, INTERNAL_API_TOKEN):
        return {"interne": True}

    from app.auth import get_current_user
    try:
        return get_current_user(
            authorization=request.headers.get("Authorization"),
            gt_access=request.cookies.get(AUTH_COOKIE_NAME),
            db=db,
        )
    except HTTPException:
        raise HTTPException(status_code=401, detail="Accès refusé.")


def _url_interne_autorisee(url: str) -> bool:
    """L'URL à rendre en PDF appartient-elle bien à cette application ?

    `generate-pdf` et `send-pdf-from-html` acceptaient n'importe quelle adresse
    et la faisaient charger par un Chromium local : de quoi lire, depuis
    Internet, tout ce qui tourne sur cette machine et n'est pas exposé. On
    n'autorise donc que nos propres pages.
    """
    from urllib.parse import urlparse
    try:
        cible = urlparse(url)
    except Exception:
        return False
    if cible.scheme not in ("http", "https"):
        return False
    autorises = set()
    for base in (os.getenv("APP_INTERNAL_URL", "http://localhost:8000"),
                 os.getenv("APP_PUBLIC_URL", "")):
        if not base:
            continue
        h = urlparse(base)
        if h.hostname:
            autorises.add((h.hostname, h.port))
    autorises.update({("127.0.0.1", None), ("localhost", None)})
    return any(cible.hostname == host and (port is None or cible.port == port)
               for host, port in autorises)


# ==================== BOUTIQUE — ADMINISTRATION ====================

@app.get("/boutique/catalogue", response_class=HTMLResponse)
async def shop_admin_catalog(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: publication, vedettes et disponibilité des produits"""
    return templates.TemplateResponse("shop_admin_catalog.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/produits/doublons", response_class=HTMLResponse)
async def produits_doublons(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Outil: regroupement des fiches produit qui désignent le même appareil"""
    return templates.TemplateResponse("product_duplicates.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/bannieres", response_class=HTMLResponse)
async def shop_admin_banners(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: bannières éditoriales photo/vidéo de la page d'accueil"""
    return templates.TemplateResponse("shop_admin_banners.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/variantes", response_class=HTMLResponse)
async def shop_admin_variants(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: groupes de variantes commerciales et leurs rattachements"""
    return templates.TemplateResponse("shop_admin_variants.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/prix", response_class=HTMLResponse)
async def shop_admin_prices(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: prix boutique et exceptions de variantes par produit"""
    return templates.TemplateResponse("shop_admin_prices.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/commandes", response_class=HTMLResponse)
async def shop_admin_orders(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: gestion des commandes"""
    return templates.TemplateResponse("shop_admin_orders.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/livraisons", response_class=HTMLResponse)
async def shop_admin_deliveries(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: gestion des livraisons"""
    return templates.TemplateResponse("shop_admin_deliveries.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/configuration", response_class=HTMLResponse)
async def shop_admin_settings(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: configuration de la page d'accueil et des coordonnées"""
    return templates.TemplateResponse("shop_admin_settings.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/zones", response_class=HTMLResponse)
async def shop_admin_zones(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: zones de livraison (frais/délais)"""
    return templates.TemplateResponse("shop_admin_zones.html", {
        "request": request, "global_settings": _load_company_settings(db)})


@app.get("/boutique/demandes", response_class=HTMLResponse)
async def shop_admin_demands(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Back-office: demandes de produits (réf DA-)"""
    return templates.TemplateResponse("shop_admin_demands.html", {
        "request": request, "global_settings": _load_company_settings(db)})

@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des clients"""
    return templates.TemplateResponse("clients.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/clients/detail", response_class=HTMLResponse)
async def client_detail_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de détail d'un client"""
    return templates.TemplateResponse("clients_detail.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/clients/debts", response_class=HTMLResponse)
async def client_debts_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des créances d'un client (agrégées)"""
    return templates.TemplateResponse("client_debts.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/clients/debts/print/{client_id}", response_class=HTMLResponse)
async def client_debts_print_page(request: Request, client_id: int, db: Session = Depends(get_db),
                  current_user = Depends(require_page_or_internal)):
    """Page imprimable du récapitulatif des créances d'un client"""
    # Construire le même agrégat que l'API JSON pour le rendu
    from datetime import date as _date
    from sqlalchemy.orm import joinedload
    from app.database import Client as _Client, Invoice as _Invoice, ClientDebt as _ClientDebt
    cl = db.query(_Client).filter(_Client.client_id == client_id).first()
    if not cl:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    today = _date.today()
    remaining_sql = func.coalesce(_Invoice.remaining_amount, _Invoice.total - func.coalesce(_Invoice.paid_amount, 0))
    invs = (
        db.query(_Invoice)
        .options(joinedload(_Invoice.items))
        .filter(_Invoice.client_id == client_id)
        .filter(remaining_sql > 0)
        .order_by(_Invoice.date.desc())
        .all()
    )
    def inv_status(inv):
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid))
        overdue = bool(inv.due_date and getattr(inv.due_date, 'date', lambda: inv.due_date)() < today and remaining > 0)
        st = "paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
        return amount, paid, remaining, st
    inv_data = []
    for inv in invs:
        amount, paid, remaining, st = inv_status(inv)
        inv_data.append({
            "id": int(inv.invoice_id),
            "invoice_number": inv.invoice_number,
            "date": inv.date,
            "due_date": inv.due_date,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "status": st,
            "items": [
                {
                    "product_name": it.product_name,
                    "quantity": int(it.quantity or 0),
                    "price": float(it.price or 0),
                    "total": float(it.total or 0),
                } for it in (inv.items or [])
            ]
        })
    remaining_cd = func.coalesce(_ClientDebt.remaining_amount, _ClientDebt.amount - func.coalesce(_ClientDebt.paid_amount, 0))
    cds = (
        db.query(_ClientDebt)
        .filter(_ClientDebt.client_id == client_id)
        .filter(remaining_cd > 0)
        .order_by(_ClientDebt.date.desc())
        .all()
    )
    md_data = []
    for d in cds:
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        overdue = bool(d.due_date and getattr(d.due_date, 'date', lambda: d.due_date)() < today and remaining > 0)
        st = d.status or ("paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending")))
        md_data.append({
            "id": int(d.debt_id),
            "reference": d.reference,
            "date": d.date,
            "due_date": d.due_date,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "status": st,
            "description": d.description,
        })
    total_amount = sum(x.get("amount", 0.0) for x in inv_data) + sum(x.get("amount", 0.0) for x in md_data)
    total_paid = sum(x.get("paid_amount", 0.0) for x in inv_data) + sum(x.get("paid_amount", 0.0) for x in md_data)
    total_remaining = sum(x.get("remaining_amount", 0.0) for x in inv_data) + sum(x.get("remaining_amount", 0.0) for x in md_data)

    company_settings = _load_company_settings(db)
    context = {
        "request": request,
        "global_settings": company_settings,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "city": company_settings.get("city"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": company_settings.get("logo"),
            "logo_path": company_settings.get("logo_path"),
            "footer_text": company_settings.get("footer_text"),
        },
        "client": cl,
        "invoices": inv_data,
        "manual_debts": md_data,
        "summary": {
            "total_amount": total_amount,
            "total_paid": total_paid,
            "total_remaining": total_remaining,
        }
    }
    return templates.TemplateResponse("print_client_debts.html", context)

@app.get("/stock-movements", response_class=HTMLResponse)
async def stock_movements_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des mouvements de stock"""
    return templates.TemplateResponse("stock_movements.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/invoices", response_class=HTMLResponse)
async def invoices_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des factures"""
    return templates.TemplateResponse("invoices.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/quotations", response_class=HTMLResponse)
async def quotations_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des devis"""
    return templates.TemplateResponse("quotations.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/maintenances", response_class=HTMLResponse)
async def maintenances_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des maintenances"""
    return templates.TemplateResponse("maintenances.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/peremption", response_class=HTMLResponse)
async def peremption_page(request: Request, db: Session = Depends(get_db),
                          current_user = Depends(require_page_login)):
    """Lots périmés et lots à écouler.

    L'écran n'a de sens que si la boutique saisit des dates limites : sans le
    module, on renvoie vers les produits plutôt que d'afficher deux tableaux
    éternellement vides.
    """
    if not shop_profile.module_actif("peremption", db):
        return RedirectResponse(url=f"{URL_PREFIX}/products", status_code=302)
    return templates.TemplateResponse("peremption.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/scan", response_class=HTMLResponse)
async def scan_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de scan de codes-barres"""
    return templates.TemplateResponse("scan.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des paramètres de l'application"""
    return templates.TemplateResponse("settings.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/suppliers", response_class=HTMLResponse)
async def suppliers_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des fournisseurs"""
    return templates.TemplateResponse("suppliers.html", {"request": request, "global_settings": _load_company_settings(db)})


@app.get("/arrivals", response_class=HTMLResponse)
async def arrivals_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des arrivages"""
    return templates.TemplateResponse("arrivals.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/supplier/{supplier_id}", response_class=HTMLResponse)
async def supplier_detail_page(request: Request, supplier_id: int, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de détails d'un fournisseur"""
    return templates.TemplateResponse("supplier_detail.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/delivery-notes", response_class=HTMLResponse)
async def delivery_notes_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des bons de livraison"""
    return templates.TemplateResponse("delivery_notes.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/bank-transactions", response_class=HTMLResponse)
async def bank_transactions_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des transactions bancaires"""
    return templates.TemplateResponse("bank_transactions.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des rapports"""
    return templates.TemplateResponse("reports.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/stock-summary", response_class=HTMLResponse)
async def stock_summary_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page du récapitulatif de stock"""
    return templates.TemplateResponse("stock_summary.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/supplier-invoices", response_class=HTMLResponse)
async def supplier_invoices_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des factures fournisseur"""
    return templates.TemplateResponse("supplier_invoices.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/debts", response_class=HTMLResponse)
async def debts_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion des dettes"""
    return templates.TemplateResponse("debts.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/barcode-generator", response_class=HTMLResponse)
async def barcode_generator_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page du générateur de codes-barres"""
    return templates.TemplateResponse("barcode_generator.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/guide", response_class=HTMLResponse)
async def guide_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page du guide utilisateur"""
    return templates.TemplateResponse("guide.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/migration-manager", response_class=HTMLResponse)
async def migration_manager_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page du gestionnaire de migration"""
    return templates.TemplateResponse("migration_manager.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/cache-manager", response_class=HTMLResponse)
async def cache_manager_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page du gestionnaire de cache"""
    return templates.TemplateResponse("cache_manager.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/daily-recap", response_class=HTMLResponse)
async def daily_recap_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page du récap quotidien"""
    return templates.TemplateResponse("daily_recap.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/daily-purchases", response_class=HTMLResponse)
async def daily_purchases_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des achats quotidiens"""
    return templates.TemplateResponse("daily_purchases.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/daily-requests", response_class=HTMLResponse)
async def daily_requests_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des demandes quotidiennes des clients"""
    return templates.TemplateResponse("daily_requests.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/daily-sales", response_class=HTMLResponse)
async def daily_sales_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page des ventes quotidiennes"""
    return templates.TemplateResponse("daily_sales.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/google-sheets-sync", response_class=HTMLResponse)
async def google_sheets_sync_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de synchronisation Google Sheets"""
    return templates.TemplateResponse("google_sheets_sync.html", {"request": request, "global_settings": _load_company_settings(db)})

# ===================== PRINT ROUTES (Invoice, Delivery Note) =====================

def _load_company_settings(db: Session) -> dict:
    result = {}

    # Load company settings from INVOICE_COMPANY
    try:
        s = db.query(UserSettings).filter(UserSettings.setting_key == "INVOICE_COMPANY").order_by(UserSettings.updated_at.desc()).first()
        if s and s.setting_value:
            result = json.loads(s.setting_value)
    except Exception:
        pass

    # Fallback: read from consolidated appSettings.company if present
    if not result:
        try:
            legacy_us = (
                db.query(UserSettings)
                .filter(UserSettings.setting_key == "appSettings")
                .order_by(UserSettings.updated_at.desc())
                .first()
            )
            if legacy_us and legacy_us.setting_value:
                data = json.loads(legacy_us.setting_value)
                comp = (data or {}).get("company") or {}
                if comp:
                    result = {
                        "name": comp.get("companyName") or comp.get("name"),
                        "address": comp.get("companyAddress") or comp.get("address"),
                        "email": comp.get("companyEmail") or comp.get("email"),
                        "phone": comp.get("companyPhone") or comp.get("phone"),
                        "website": comp.get("companyWebsite") or comp.get("website"),
                        "logo": comp.get("logo"),  # DataURL support
                    }
        except Exception:
            pass

    # Load favicon from appSettings.general.faviconUrl
    try:
        app_settings_record = (
            db.query(UserSettings)
            .filter(UserSettings.setting_key == "appSettings")
            .order_by(UserSettings.updated_at.desc())
            .first()
        )
        if app_settings_record and app_settings_record.setting_value:
            app_data = json.loads(app_settings_record.setting_value)
            general = (app_data or {}).get("general") or {}
            favicon_url = general.get("faviconUrl")
            if favicon_url:
                result["favicon"] = favicon_url
    except Exception:
        pass
    # Fallback: pull from legacy Settings table if available (only if result is still empty)
    if not result:
        try:
            if LegacySettings is not None:
                legacy = db.query(LegacySettings).first()
                if legacy:
                    result = {
                        "name": getattr(legacy, "company_name", None),
                        "address": getattr(legacy, "address", None),
                        "city": getattr(legacy, "city", None),
                        "email": getattr(legacy, "email", None),
                        "phone": getattr(legacy, "phone", None),
                        "phone2": getattr(legacy, "phone2", None),
                        "whatsapp": getattr(legacy, "whatsapp", None),
                        "instagram": getattr(legacy, "instagram", None),
                        "website": getattr(legacy, "website", None),
                        # Prefer unified key 'logo' for templates; keep 'logo_path' for compatibility
                        "logo": getattr(legacy, "logo_path", None),
                        "logo_path": getattr(legacy, "logo_path", None),
                        "footer_text": getattr(legacy, "footer_text", None),
                    }
        except Exception:
            pass
    return result


@app.get("/invoices/print/{invoice_id}", response_class=HTMLResponse)
async def print_invoice_page(request: Request, invoice_id: int, db: Session = Depends(get_db),
                  current_user = Depends(require_page_or_internal)):
    inv = (
        db.query(Invoice)
        .options(joinedload(Invoice.items), joinedload(Invoice.client), joinedload(Invoice.payments), joinedload(Invoice.exchange_items))
        .filter(Invoice.invoice_id == invoice_id)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    # Parse IMEIs from notes meta (if present)
    imeis_by_product_id = {}
    try:
        if inv.notes:
            # Be robust: stop at next meta marker (e.g., __SIGNATURE__) or end of string
            txt = str(inv.notes)
            if "__SERIALS__=" in txt:
                sub = txt.split("__SERIALS__=", 1)[1]
                cut_idx = sub.find("\n__")
                if cut_idx != -1:
                    sub = sub[:cut_idx].strip()
                sub = sub.strip()
                try:
                    arr = json.loads(sub)
                except Exception:
                    # Fallback: non-greedy regex inside brackets
                    m = re.search(r"__SERIALS__=(\[.*?\])", txt, flags=re.S)
                    arr = json.loads(m.group(1)) if m else []
                for entry in (arr or []):
                    pid = str(entry.get("product_id"))
                    imeis_by_product_id[pid] = entry.get("imeis") or []
    except Exception:
        pass

    # Parse original quotation quantities from notes meta (if present)
    quote_qty_by_product_id = {}
    try:
        if inv.notes:
            txt = str(inv.notes)
            if "__QUOTE_QTYS__=" in txt:
                sub = txt.split("__QUOTE_QTYS__=", 1)[1]
                cut_idx = sub.find("\n__")
                if cut_idx != -1:
                    sub = sub[:cut_idx].strip()
                sub = sub.strip()
                try:
                    arrq = json.loads(sub)
                except Exception:
                    mqq = re.search(r"__QUOTE_QTYS__=(\[.*?\])", txt, flags=re.S)
                    arrq = json.loads(mqq.group(1)) if mqq else []
                for entry in (arrq or []):
                    try:
                        pid = str(int(entry.get("product_id")))
                        qty = int(entry.get("qty") or 0)
                        quote_qty_by_product_id[pid] = qty
                    except Exception:
                        pass
    except Exception:
        pass

    # Build product descriptions map for involved products
    product_descriptions = {}
    try:
        product_ids = sorted({int(it.product_id) for it in (inv.items or []) if it.product_id is not None})
        if product_ids:
            for p in db.query(Product).filter(Product.product_id.in_(product_ids)).all():
                product_descriptions[str(p.product_id)] = (p.description or "")
    except Exception:
        product_descriptions = {}

    # Group items by product_id + price and attach IMEIs (from notes or inline fallback)
    # Tout en supportant des "sections" personnalisées encodées comme items sans produit
    grouped_by_key: dict[str, dict] = {}
    item_to_key: dict[int, str] = {}

    for it in (inv.items or []):
        name = it.product_name or ""

        # Sections: product_id nul et libellé commençant par [SECTION]
        if it.product_id is None and isinstance(name, str) and name.strip().startswith("[SECTION]"):
            key = f"SECTION|{getattr(it, 'item_id', id(it))}"
            raw = name.strip()
            # Extraire le titre après le préfixe
            title = raw[len("[SECTION]"):].strip(" :-") or raw[len("[SECTION]"):].strip()
            grouped_by_key[key] = {
                "product_id": None,
                "name": title or "Section",
                "description": "",
                "price": 0.0,
                "qty": 0,
                "total": 0.0,
                "imeis": [],
                "quote_qty": None,
                "is_section": True,
            }
            if getattr(it, "item_id", None) is not None:
                item_to_key[it.item_id] = key
            continue

        # Lignes personnalisées sans produit (services, etc.)
        if it.product_id is None:
            key = f"CUSTOM|{getattr(it, 'item_id', id(it))}"
            grouped_by_key[key] = {
                "product_id": None,
                "name": name,
                "description": "",
                "price": float(it.price or 0),
                "qty": int(it.quantity or 0),
                "total": float(it.total or 0),
                "imeis": [],
                "quote_qty": None,
                "is_section": False,
            }
            if getattr(it, "item_id", None) is not None:
                item_to_key[it.item_id] = key
            continue

        # Produits classiques: grouper par (product_id, price)
        key = f"{it.product_id}|{float(it.price or 0)}"
        if key not in grouped_by_key:
            grouped_by_key[key] = {
                "product_id": it.product_id,
                "name": it.product_name,
                "description": product_descriptions.get(str(it.product_id)) if it.product_id is not None else "",
                "price": float(it.price or 0),
                "qty": 0,
                "total": 0.0,
                "imeis": [],  # list of IMEIs to render on separate lines
                "imeis_col": [],  # IMEIs portés par la ligne elle-même (source fiable)
                "quote_qty": None,
                "is_section": False,
            }
        g = grouped_by_key[key]
        g["qty"] += int(it.quantity or 0)
        g["total"] += float(it.total or 0)
        if getattr(it, "item_id", None) is not None:
            item_to_key[it.item_id] = key

        # Source prioritaire: invoice_items.variant_imei, renseigné à la vente
        # depuis le 29/07/2026. Chaque ligne tombe dans son propre groupe, donc
        # aucun risque de mélange entre deux groupes du même produit à prix différents.
        vimei = str(getattr(it, "variant_imei", None) or "").strip()
        if vimei and vimei not in g["imeis_col"]:
            g["imeis_col"].append(vimei)

        # Fallback: extract inline IMEI from product_name like "(IMEI: 123...)"
        try:
            pname = (it.product_name or "")
            m = re.search(r"\(IMEI:\s*([^)]+)\)", pname, flags=re.I)
            if m:
                imei = (m.group(1) or "").strip()
                if imei and imei not in g["imeis"]:
                    g["imeis"].append(imei)
        except Exception:
            pass

    # Choisir la source des IMEI, par ordre de fiabilité décroissante :
    #   1. invoice_items.variant_imei (colonne, depuis le 29/07/2026)
    #   2. le bloc __SERIALS__ des notes (factures antérieures à la colonne)
    #   3. l'IMEI inline dans le libellé "(IMEI: ...)" (très anciennes factures)
    for g in grouped_by_key.values():
        # Ne pas toucher aux sections
        if g.get("is_section"):
            continue
        col = g.get("imeis_col") or []
        lst = imeis_by_product_id.get(str(g["product_id"])) or []
        # Attach original quotation quantity if available
        try:
            g["quote_qty"] = quote_qty_by_product_id.get(str(g["product_id"]))
        except Exception:
            g["quote_qty"] = g.get("quote_qty")
        if col:
            # La quantité groupée est déjà juste (une ligne par exemplaire, ou
            # une ligne de quantité N pour une variante suivie en quantité) :
            # la réécrire à partir du nombre d'IMEI fausserait le second cas.
            g["imeis"] = col
        elif lst:
            g["imeis"] = lst
            g["qty"] = len(lst)
            g["total"] = g["qty"] * float(g["price"])
        elif g.get("imeis"):
            g["qty"] = len(g["imeis"])
            g["total"] = g["qty"] * float(g["price"])

    # Extract signature image from notes if embedded
    signature_data_url = None
    signature_location = None
    try:
        if inv.notes:
            # Extract location first (before signature, since signature contains long base64)
            m_loc = re.search(r"__LOCATION__=([\d.\-]+,[\d.\-]+)", inv.notes)
            if m_loc:
                signature_location = m_loc.group(1).strip()
            m2 = re.search(r"__SIGNATURE__=(.*?)(?:\n\n__LOCATION__|$)", inv.notes, flags=re.S)
            if m2:
                signature_data_url = (m2.group(1) or '').strip()
    except Exception:
        pass

    company_settings = _load_company_settings(db)

    # Resolve payment method: invoice.payment_method or latest payment's method
    resolved_payment_method = getattr(inv, "payment_method", None)
    try:
        if not resolved_payment_method and getattr(inv, "payments", None):
            latest = None
            for p in inv.payments:
                if not latest:
                    latest = p
                else:
                    try:
                        if (p.payment_date or 0) > (latest.payment_date or 0):
                            latest = p
                    except Exception:
                        pass
            if latest and getattr(latest, "payment_method", None):
                resolved_payment_method = latest.payment_method
    except Exception:
        pass
    # Déterminer si on doit afficher la garantie (certificat)
    warranty_certificate = None
    try:
        if getattr(inv, "has_warranty", False) and getattr(inv, "warranty_duration", None):
            warranty_certificate = {
                "duration": inv.warranty_duration,
                "start_date": getattr(inv, "warranty_start_date", None),
                "end_date": getattr(inv, "warranty_end_date", None),
                "invoice_number": inv.invoice_number,
                "client_name": (inv.client.name if getattr(inv, "client", None) else ""),
                "date": inv.date,
                "products": [item["name"] for item in grouped_by_key.values() if not item.get("is_section")],
            }
    except Exception:
        warranty_certificate = None

    # Reconstituer la liste ordonnée en respectant l'ordre d'origine des items
    ordered_items = []
    seen_keys = set()
    for it in (inv.items or []):
        key = item_to_key.get(getattr(it, "item_id", -1))
        if not key or key in seen_keys:
            continue
        g = grouped_by_key.get(key)
        if not g:
            continue
        ordered_items.append(g)
        seen_keys.add(key)

    # Charger les exchange_items si c'est une facture d'échange
    exchange_items = []
    if hasattr(inv, 'exchange_items') and inv.exchange_items:
        exchange_items = inv.exchange_items
    
    # Filtrer les garanties selon les catégories de produits présentes dans la facture
    warranties = []
    try:
        # Récupérer les paramètres de facturation avec les garanties
        invoice_settings = db.query(UserSettings).filter(
            UserSettings.setting_key == 'appSettings'
        ).order_by(UserSettings.updated_at.desc()).first()
        
        if invoice_settings:
            settings_data = json.loads(invoice_settings.setting_value)
            all_warranties = settings_data.get('invoice', {}).get('warranties', [])
            
            # Extraire les catégories de produits présentes dans la facture
            categories_in_invoice = set()
            for item in ordered_items:
                if not item.get('is_section') and item.get('product_id'):
                    product = db.query(Product).filter(Product.product_id == item['product_id']).first()
                    if product and product.category:
                        categories_in_invoice.add(product.category)
            
            # Filtrer les garanties pour ne garder que celles des catégories présentes
            warranties = [w for w in all_warranties if w.get('category') in categories_in_invoice]
    except Exception as e:
        print(f"Erreur lors du chargement des garanties: {e}")
        warranties = []
    
    context = {
        "request": request,
        "invoice": inv,
        "grouped_items": ordered_items,
        "exchange_items": exchange_items,
        "signature_data_url": signature_data_url,
        "signature_location": signature_location,
        "resolved_payment_method": resolved_payment_method,
        "warranty_certificate": warranty_certificate,
        "warranties": warranties,
        # Pass through the whole company settings dict to let the template use additional fields
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "city": company_settings.get("city"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "whatsapp": company_settings.get("whatsapp"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": _normalize_logo(company_settings.get("logo") or company_settings.get("logo_path")),
            "logo_path": company_settings.get("logo_path"),
            "footer_text": company_settings.get("footer_text"),
            # Optional legal fields
            "rc_number": company_settings.get("rc_number"),
            "ninea_number": company_settings.get("ninea_number"),
        },
    }

    # Toujours utiliser le template principal (les garanties par catégorie sont affichées directement dedans)
    template_name = "print_invoice.html"
    response = templates.TemplateResponse(template_name, context)
    
    # Mark response to skip CSP headers (checked in middleware)
    response.headers["X-Skip-CSP"] = "true"
    
    return response


@app.get("/invoices/pdf/{invoice_id}")
async def get_invoice_pdf(request: Request, invoice_id: int, db: Session = Depends(get_db)):
    """Génère et retourne le PDF de la facture via Playwright (Chromium)"""
    inv = db.query(Invoice).filter(Invoice.invoice_id == invoice_id).first()
    if not inv:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    app_internal_url = os.getenv("APP_INTERNAL_URL", "http://localhost:8000")
    html_url = f"{app_internal_url}/invoices/print/{invoice_id}"
    pdf_bytes = await _generate_pdf_from_url(html_url)

    filename = f"Facture_{inv.invoice_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/quotations/pdf/{quotation_id}")
async def get_quotation_pdf(request: Request, quotation_id: int, db: Session = Depends(get_db)):
    """Génère et retourne le PDF du devis via Playwright (Chromium)"""
    from app.database import Quotation

    q = db.query(Quotation).filter(Quotation.quotation_id == quotation_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Devis non trouvé")

    app_internal_url = os.getenv("APP_INTERNAL_URL", "http://localhost:8000")
    html_url = f"{app_internal_url}/quotations/print/{quotation_id}"
    pdf_bytes = await _generate_pdf_from_url(html_url)

    filename = f"Devis_{q.quotation_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/quotations/print/{quotation_id}", response_class=HTMLResponse)
async def print_quotation_page(request: Request, quotation_id: int, db: Session = Depends(get_db),
                  current_user = Depends(require_page_or_internal)):
    from app.database import Quotation, Client
    q = (
        db.query(Quotation)
        .options(joinedload(Quotation.items), joinedload(Quotation.client))
        .filter(Quotation.quotation_id == quotation_id)
        .first()
    )
    if not q:
        raise HTTPException(status_code=404, detail="Devis non trouvé")

    # Signature depuis notes meta si présente
    signature_data_url = None
    try:
        if q.notes:
            m2 = re.search(r"__SIGNATURE__=(.*)$", q.notes, flags=re.S)
            if m2:
                signature_data_url = (m2.group(1) or '').strip()
    except Exception:
        pass

    # Build product descriptions map
    product_descriptions = {}
    try:
        product_ids = sorted({int(it.product_id) for it in (q.items or []) if it.product_id is not None})
        if product_ids:
            for p in db.query(Product).filter(Product.product_id.in_(product_ids)).all():
                product_descriptions[str(p.product_id)] = (p.description or "")
    except Exception:
        product_descriptions = {}

    company_settings = _load_company_settings(db)
    context = {
        "request": request,
        "quotation": q,
        "client": q.client,
        "settings": {
            **company_settings,
            "logo": _normalize_logo(company_settings.get("logo") or company_settings.get("logo_path")),
        },
        "signature_data_url": signature_data_url,
        "product_descriptions": product_descriptions,
    }
    return templates.TemplateResponse("print_quotation.html", context)

@app.get("/delivery-notes/print/{note_id}", response_class=HTMLResponse)
async def print_delivery_note_page(request: Request, note_id: int, db: Session = Depends(get_db),
                  current_user = Depends(require_page_or_internal)):
    # La base d'abord, la liste de démonstration ensuite.
    #
    # C'était l'inverse jusqu'au 13/08/2026, et cette liste en mémoire contient
    # deux entrées aux identifiants 1 et 2 (« BL-2024-001 / Amadou Ba »). Un vrai
    # bon de livraison portant l'un de ces identifiants imprimait donc la fiche
    # d'un autre client — nom, adresse et articles compris. La production y a
    # échappé de peu : sa séquence était déjà au-delà.
    note = None
    dn = (
        db.query(DeliveryNote)
        .filter(DeliveryNote.delivery_note_id == note_id)
        .first()
    )
    if not dn:
        try:
            from app.routers.delivery_notes import delivery_notes_data  # type: ignore
            note = next((n for n in delivery_notes_data if int(n.get("id")) == int(note_id)), None)
        except Exception:
            note = None

    if not note:
        dn = (
            db.query(DeliveryNote)
            .filter(DeliveryNote.delivery_note_id == note_id)
            .first()
        )
        if not dn:
            raise HTTPException(status_code=404, detail="Bon de livraison non trouvé")
        # Charger relations
        _ = dn.items
        _ = dn.client
        note = {
            "id": dn.delivery_note_id,
            "number": dn.delivery_note_number,
            "client_id": dn.client_id,
            "client_name": (dn.client.name if dn.client else None),
            "date": dn.date,
            "delivery_date": dn.delivery_date,
            "status": dn.status,
            "delivery_address": dn.delivery_address,
            "delivery_contact": dn.delivery_contact,
            "delivery_phone": dn.delivery_phone,
            "items": (lambda _items: [
                (lambda _clean_name, _serials: {
                    "product_id": it.product_id,
                    "product_name": _clean_name,
                    "quantity": it.quantity,
                    "unit_price": float(it.price or 0),
                    "serials": _serials
                })(
                    # Nettoyer le libellé: retirer un éventuel suffixe "(IMEI: xxx)"
                    (re.sub(r"\s*\(IMEI:\s*[^)]+\)\s*$", "", (it.product_name or ""), flags=re.I) if 're' in globals() else (it.product_name or "")),
                    (lambda s: (json.loads(s) if (isinstance(s, str) and s.strip().startswith("[")) else ([])))(it.serial_numbers or "")
                )
                for it in _items
            ])(dn.items or []),
            "subtotal": float(dn.subtotal or 0),
            "tax_rate": float(dn.tax_rate or 0),
            "tax_amount": float(dn.tax_amount or 0),
            "total": float(dn.total or 0),
            "notes": dn.notes,
            "created_at": dn.created_at,
        }

    # Construire la map des descriptions produits (clé: str(product_id))
    product_descriptions = {}
    try:
        item_list = (note.get("items") if isinstance(note, dict) else []) or []
        product_ids = sorted({int(it.get("product_id")) for it in item_list if it.get("product_id") is not None})
        if product_ids:
            for p in db.query(Product).filter(Product.product_id.in_(product_ids)).all():
                product_descriptions[str(p.product_id)] = (p.description or "")
    except Exception:
        product_descriptions = {}

    company_settings = _load_company_settings(db)
    context = {
        "request": request,
        "note": note,
        "product_descriptions": product_descriptions,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "whatsapp": company_settings.get("whatsapp"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": company_settings.get("logo"),
            "rc_number": company_settings.get("rc_number"),
            "ninea_number": company_settings.get("ninea_number"),
        },
    }
    return templates.TemplateResponse("print_delivery_note.html", context)

# ==================== WHATSAPP ROUTES (Evolution API) ====================

import httpx
import base64
import asyncio

# Evolution API configuration
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE_NAME", "stock")

def _evo_headers():
    """Return standard headers for Evolution API requests."""
    return {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}

def _normalize_phone(number: str) -> str:
    """Normalize phone number to Evolution API format (digits only)."""
    n = str(number).strip()
    n = n.replace("@c.us", "").replace("+", "").replace(" ", "").replace("-", "")
    return n

async def _ensure_instance():
    """Ensure the Evolution API instance exists, create if needed."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{EVOLUTION_API_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(), timeout=5.0
            )
            if resp.status_code == 200:
                return
        except Exception:
            pass
        try:
            await client.post(
                f"{EVOLUTION_API_URL}/instance/create",
                headers=_evo_headers(),
                json={"instanceName": EVOLUTION_INSTANCE, "integration": "WHATSAPP-BAILEYS", "qrcode": True},
                timeout=10.0
            )
        except Exception:
            pass

# ---- WhatsApp connection watchdog ----
import threading

_wa_watchdog_running = False

def _wa_watchdog_loop():
    """Background thread that monitors WhatsApp connection and auto-reconnects."""
    import time
    interval = 60  # check every 60 seconds
    while _wa_watchdog_running:
        try:
            with httpx.Client() as client:
                resp = client.get(
                    f"{EVOLUTION_API_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
                    headers=_evo_headers(), timeout=10.0
                )
                if resp.status_code == 200:
                    state = resp.json().get("instance", {}).get("state", "unknown")
                    if state in ("close", "connecting"):
                        print(f"[WA-Watchdog] Connection state: {state} — attempting reconnect...")
                        reconnect_resp = client.get(
                            f"{EVOLUTION_API_URL}/instance/connect/{EVOLUTION_INSTANCE}",
                            headers=_evo_headers(), timeout=15.0
                        )
                        rdata = reconnect_resp.json()
                        if "base64" in rdata or "code" in rdata:
                            print("[WA-Watchdog] QR code generated — scan required to reconnect")
                        elif state == "connecting":
                            print("[WA-Watchdog] Instance is reconnecting automatically...")
                        else:
                            print(f"[WA-Watchdog] Reconnect response: {str(rdata)[:200]}")
                    elif state == "open":
                        pass  # connection is healthy
                    else:
                        print(f"[WA-Watchdog] Unknown state: {state}")
        except Exception as e:
            print(f"[WA-Watchdog] Check failed: {e}")
        time.sleep(interval)

def start_wa_watchdog():
    global _wa_watchdog_running
    if _wa_watchdog_running:
        return
    _wa_watchdog_running = True
    t = threading.Thread(target=_wa_watchdog_loop, daemon=True, name="wa-watchdog")
    t.start()
    print("[WA-Watchdog] Started — monitoring WhatsApp connection every 60s")

def stop_wa_watchdog():
    global _wa_watchdog_running
    _wa_watchdog_running = False
    print("[WA-Watchdog] Stopped")

async def _generate_pdf_from_url(html_url: str) -> bytes:
    """Generate a PDF from an HTML URL using Playwright (Chromium)."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page()
        # Les pages d'impression exigent maintenant une session ou le jeton
        # interne (voir `require_page_or_internal`). Chromium n'a pas de cookie :
        # on lui donne le jeton.
        jeton_interne = (os.getenv("INTERNAL_API_TOKEN") or "").strip()
        if jeton_interne:
            await page.set_extra_http_headers({"X-Internal-Token": jeton_interne})
        await page.goto(html_url, wait_until="networkidle", timeout=30000)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"},
        )
        await browser.close()
    return pdf_bytes

@app.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_page(request: Request, db: Session = Depends(get_db),
                  current_user = Depends(require_page_login)):
    """Page de gestion WhatsApp"""
    return templates.TemplateResponse("whatsapp.html", {"request": request, "global_settings": _load_company_settings(db)})

@app.get("/api/whatsapp/status")
async def whatsapp_status(_acces=Depends(require_whatsapp_access)):
    """Vérifier l'état de la connexion WhatsApp via Evolution API."""
    try:
        await _ensure_instance()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{EVOLUTION_API_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(), timeout=5.0
            )
            data = response.json()
            state = data.get("instance", {}).get("state", data.get("state", "close"))
            is_connected = state == "open"
            return {
                "connected": is_connected,
                "ready": is_connected,
                "status": "WORKING" if is_connected else ("SCAN_QR_CODE" if state in ("connecting", "waiting_qr") else "DISCONNECTED"),
                "hasQr": state in ("connecting", "close", "disconnected", "waiting_qr")
            }
    except Exception as e:
        return {"connected": False, "ready": False, "status": "DISCONNECTED", "hasQr": False, "error": str(e)}

@app.get("/api/whatsapp/qr")
async def whatsapp_qr(_acces=Depends(require_whatsapp_access)):
    """Obtenir le QR Code pour la connexion via Evolution API."""
    try:
        await _ensure_instance()
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{EVOLUTION_API_URL}/instance/connect/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(), timeout=10.0
            )
            data = response.json()
            qr_code = data.get("code")
            qr_base64 = data.get("base64")
            if qr_code:
                return {"qr": qr_code, "qrBase64": qr_base64}
            elif qr_base64:
                return {"qr": None, "qrBase64": qr_base64}
            else:
                return {"error": "QR Code non disponible - peut-être déjà connecté", "data": data}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/whatsapp/send-text")
async def whatsapp_send_text(request: Request, _acces=Depends(require_whatsapp_access)):
    """Envoyer un message texte via WhatsApp (Evolution API)."""
    try:
        data = await request.json()
        number = _normalize_phone(data.get("number", ""))
        message = data.get("message", "") or data.get("text", "")
        if not number or not message:
            return {"error": "number and message are required"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(),
                json={"number": number, "text": message},
                timeout=15.0
            )
            result = response.json()
            if response.status_code >= 400:
                return {"success": False, "error": result.get("response", {}).get("message", result), "response": result}
            return {"success": True, "response": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/whatsapp/send-image")
async def whatsapp_send_image(request: Request, _acces=Depends(require_whatsapp_access)):
    """Envoyer une image via WhatsApp (Evolution API)."""
    try:
        data = await request.json()
        number = _normalize_phone(data.get("number", ""))
        image_url = data.get("image_url", "") or data.get("url", "")
        caption = data.get("caption", "")
        if not number or not image_url:
            return {"error": "number and image_url are required"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendMedia/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(),
                json={"number": number, "mediatype": "image", "media": image_url, "caption": caption, "mimetype": "image/jpeg"},
                timeout=30.0
            )
            result = response.json()
            return {"success": True, "response": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/whatsapp/logout")
async def whatsapp_logout(_acces=Depends(require_whatsapp_access)):
    """Déconnecter WhatsApp via Evolution API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{EVOLUTION_API_URL}/instance/logout/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(), timeout=10.0
            )
            return {"success": True}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/whatsapp/client-info")
async def whatsapp_client_info(_acces=Depends(require_whatsapp_access)):
    """Obtenir les informations du client WhatsApp via Evolution API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{EVOLUTION_API_URL}/instance/fetchInstances",
                headers=_evo_headers(),
                params={"instanceName": EVOLUTION_INSTANCE},
                timeout=5.0
            )
            instances = response.json()
            if instances and isinstance(instances, list) and len(instances) > 0:
                inst = instances[0]
                owner = inst.get("instance", {}).get("owner", "")
                name = inst.get("instance", {}).get("profileName", "")
                return {"pushName": name or "WhatsApp User", "wid": owner, "platform": "Evolution API"}
            return {"error": "Instance non trouvée"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/whatsapp/send-file")
async def whatsapp_send_file(request: Request, _acces=Depends(require_whatsapp_access)):
    """Envoyer un fichier via WhatsApp (Evolution API)."""
    try:
        data = await request.json()
        number = _normalize_phone(data.get("number", ""))
        url = data.get("url", "")
        filename = data.get("filename", "document")
        caption = data.get("caption", "")
        if not number or not url:
            return {"error": "number and url are required"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendMedia/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(),
                json={"number": number, "mediatype": "document", "media": url, "fileName": filename, "caption": caption, "mimetype": "application/octet-stream"},
                timeout=30.0
            )
            result = response.json()
            return {"success": True, "response": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/whatsapp/send-pdf")
async def whatsapp_send_pdf(request: Request, _acces=Depends(require_whatsapp_access)):
    """Envoyer un PDF via WhatsApp depuis une URL (Evolution API)."""
    try:
        data = await request.json()
        number = _normalize_phone(data.get("number", ""))
        url = data.get("url", "")
        filename = data.get("filename", "document.pdf")
        caption = data.get("caption", "")
        if not number or not url:
            return {"error": "number and url are required"}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendMedia/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(),
                json={"number": number, "mediatype": "document", "media": url, "fileName": filename, "caption": caption, "mimetype": "application/pdf"},
                timeout=60.0
            )
            result = response.json()
            return {"success": True, "response": result}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/whatsapp/send-pdf-from-html")
async def whatsapp_send_pdf_from_html(request: Request, _acces=Depends(require_whatsapp_access)):
    """Générer un PDF depuis une URL HTML et l'envoyer via WhatsApp (Evolution API + Playwright)."""
    try:
        data = await request.json()
        number = _normalize_phone(data.get("number", ""))
        html_url = data.get("htmlUrl", "")
        filename = data.get("filename", "document.pdf")
        caption = data.get("caption", "")
        if not number or not html_url:
            return {"error": "number and htmlUrl are required"}
        if not _url_interne_autorisee(html_url):
            return {"error": "URL non autorisée : seules les pages de cette application peuvent être rendues."}
        # Generate PDF from HTML URL using Playwright/Chromium
        pdf_bytes = await _generate_pdf_from_url(html_url)
        if not pdf_bytes or len(pdf_bytes) < 100:
            return {"error": "Le PDF généré est vide ou invalide"}
        # Encode as base64 and send via Evolution API
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendMedia/{EVOLUTION_INSTANCE}",
                headers=_evo_headers(),
                json={
                    "number": number,
                    "mediatype": "document",
                    "media": pdf_base64,
                    "fileName": filename,
                    "caption": caption,
                    "mimetype": "application/pdf"
                },
                timeout=60.0
            )
            result = response.json()
            message_id = result.get("key", {}).get("id", "")
            return {"success": True, "messageId": message_id}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

@app.post("/api/whatsapp/generate-pdf")
async def whatsapp_generate_pdf(request: Request, _acces=Depends(require_whatsapp_access)):
    """Générer un PDF depuis une URL HTML (sans envoi WhatsApp)."""
    try:
        data = await request.json()
        html_url = data.get("htmlUrl", "")
        filename = data.get("filename", "document.pdf")
        if not html_url:
            return {"error": "htmlUrl is required"}
        if not _url_interne_autorisee(html_url):
            return {"error": "URL non autorisée : seules les pages de cette application peuvent être rendues."}
        pdf_bytes = await _generate_pdf_from_url(html_url)
        from fastapi.responses import Response
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        return {"error": str(e)}

# Gestion des erreurs
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse("500.html", {"request": request}, status_code=500)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
