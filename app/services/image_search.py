"""
Recherche d'images produit en ligne (Google Images via SerpAPI) et import serveur.

Deux principes:
  - La clé d'API vit en base, jamais dans le code, et se règle depuis /settings.
  - L'URL fournie par le client n'est jamais de confiance: le serveur télécharge
    lui-même, vérifie le type réel, la taille, et écrit un nom de fichier qu'il
    a généré.
"""

import ipaddress
import json
import logging
import socket
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from ..database import UserSettings

logger = logging.getLogger(__name__)

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
SETTING_KEY = "image_search"

UPLOAD_DIR = Path("static/uploads/products")
MAX_BYTES = 10 * 1024 * 1024  # 10 Mo

# Types acceptés et extension écrite sur le disque.
CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Signatures de fichier: le content-type annoncé peut mentir, pas les premiers octets.
MAGIC_NUMBERS = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)


# ------------------------------------------------------------------ réglages

def get_settings(db: Session) -> dict:
    row = db.query(UserSettings).filter(UserSettings.setting_key == SETTING_KEY).first()
    if not row or not row.setting_value:
        return {}
    try:
        data = json.loads(row.setting_value)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def get_api_key(db: Session) -> str:
    return (get_settings(db).get("serpapi_key") or "").strip()


def save_settings(db: Session, values: dict) -> dict:
    current = get_settings(db)
    current.update({k: v for k, v in (values or {}).items() if k in ("serpapi_key", "country", "language")})

    row = db.query(UserSettings).filter(UserSettings.setting_key == SETTING_KEY).first()
    if row:
        row.setting_value = json.dumps(current)
    else:
        db.add(UserSettings(setting_key=SETTING_KEY, setting_value=json.dumps(current)))
    db.commit()
    return current


# ------------------------------------------------------------------ recherche

async def search_images(db: Session, query: str, limit: int = 24) -> list[dict]:
    """Interroge Google Images via SerpAPI. Lève RuntimeError si la clé manque."""
    key = get_api_key(db)
    if not key:
        raise RuntimeError(
            "Clé SerpAPI non configurée. Renseignez-la dans Paramètres › Recherche d'images."
        )

    query = (query or "").strip()
    if not query:
        raise ValueError("Recherche vide")

    settings = get_settings(db)
    params = {
        "engine": "google_images",
        "q": query,
        "api_key": key,
        "hl": settings.get("language") or "fr",
        "gl": settings.get("country") or "sn",
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.get(SERPAPI_ENDPOINT, params=params)

    if response.status_code == 401:
        raise RuntimeError("Clé SerpAPI refusée. Vérifiez-la dans les paramètres.")
    if response.status_code == 429:
        raise RuntimeError("Quota SerpAPI épuisé pour ce mois.")
    response.raise_for_status()

    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"SerpAPI: {data['error']}")

    results = []
    for item in (data.get("images_results") or []):
        original = item.get("original")
        if not original or not original.startswith(("http://", "https://")):
            continue
        results.append({
            "url": original,
            "thumb": item.get("thumbnail") or original,
            "title": (item.get("title") or "")[:200],
            "source": (item.get("source") or "")[:120],
            "width": item.get("original_width") or 0,
            "height": item.get("original_height") or 0,
        })
        if len(results) >= limit:
            break
    return results


# ------------------------------------------------------------------ import

def _reject_internal_target(hostname: str) -> None:
    """
    Empêche le serveur d'aller chercher une ressource sur le réseau interne.

    Sans ce garde-fou, l'endpoint d'import devient un relais permettant de sonder
    localhost, le réseau privé ou les points de métadonnées d'un hébergeur.
    """
    if not hostname:
        raise ValueError("Hôte manquant")

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError("Hôte introuvable")

    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            raise ValueError("Adresse non autorisée")


async def download_image(url: str) -> Tuple[bytes, str]:
    """Télécharge une image et valide son type réel. Renvoie (contenu, extension)."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL invalide")

    _reject_internal_target(parsed.hostname or "")

    headers = {"User-Agent": BROWSER_UA, "Accept": "image/*,*/*;q=0.8"}
    async with httpx.AsyncClient(headers=headers, timeout=25.0, follow_redirects=True,
                                 max_redirects=5) as client:
        response = await client.get(url)
        response.raise_for_status()
        # Une redirection a pu déplacer la cible vers le réseau interne.
        final_host = response.url.host
        if final_host and final_host != parsed.hostname:
            _reject_internal_target(final_host)
        content = response.content

    if not content:
        raise ValueError("Image vide")
    if len(content) > MAX_BYTES:
        raise ValueError(f"Image trop volumineuse ({len(content) // 1024 // 1024} Mo, maximum 10 Mo)")

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()

    # La signature du fichier prime sur l'en-tête déclaré.
    extension = None
    for magic, ext in MAGIC_NUMBERS:
        if content.startswith(magic):
            extension = ext
            break
    if extension is None and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        extension = ".webp"

    if extension is None:
        if content_type not in CONTENT_TYPE_EXT:
            raise ValueError(f"Le fichier n'est pas une image ({content_type or 'type inconnu'})")
        extension = CONTENT_TYPE_EXT[content_type]

    return content, extension


def store_image(product_id: int, content: bytes, extension: str) -> str:
    """Écrit l'image sur le disque sous un nom généré par le serveur.

    L'image est convertie en AVIF au passage ; si la conversion échoue, le
    format d'origine est conservé (voir services/image_convert.py)."""
    from .image_convert import to_avif

    content, extension = to_avif(content, extension)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"product_{product_id}_{uuid.uuid4().hex}{extension}"
    path = UPLOAD_DIR / filename
    path.write_bytes(content)
    return str(path)


async def import_from_url(product_id: int, url: str, logo: str = None) -> str:
    """Télécharge, valide et enregistre. Renvoie le chemin relatif du fichier.

    `logo` : data URI du logo pour le filigrane (facultatif)."""
    content, extension = await download_image(url)
    if logo:
        from .watermark import apply_watermark
        content = apply_watermark(content, logo)
    return store_image(product_id, content, extension)
