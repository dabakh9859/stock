"""
Filigrane automatique du logo sur les images produit.

Le logo de la boutique (réglages société, souvent une data URI PNG) est apposé
semi-transparent sur chaque image téléversée, pour marquer les visuels. Le
traitement est tolérant : si le logo manque ou qu'une image est illisible, on
renvoie l'image d'origine inchangée plutôt que de faire échouer l'envoi.
"""
import base64
import io
import logging
from functools import lru_cache
from typing import Optional

try:
    from PIL import Image
    _PIL_OK = True
except Exception:  # Pillow absent : le filigrane est simplement ignoré.
    _PIL_OK = False

logger = logging.getLogger(__name__)

# --- Réglages du filigrane ---------------------------------------------------
# Position : coin bas-droit, pour ne pas masquer le produit.
WATERMARK_WIDTH_RATIO = 0.22   # largeur du logo = 22 % de la largeur de l'image
WATERMARK_OPACITY = 0.45       # 0 = invisible, 1 = opaque
WATERMARK_MARGIN_RATIO = 0.03  # marge depuis les bords = 3 % de la largeur
MIN_LOGO_WIDTH = 48            # ne pas descendre sous cette largeur (px)


def get_shop_logo(db) -> Optional[str]:
    """Récupère le logo de la société (data URI) depuis les réglages, pour le
    filigrane. Même source que l'en-tête de l'application (clé INVOICE_COMPANY,
    repli sur appSettings.company)."""
    import json
    from ..database import UserSettings

    def _logo_from(key: str, path=("logo",)):
        row = (
            db.query(UserSettings)
            .filter(UserSettings.setting_key == key)
            .order_by(UserSettings.updated_at.desc())
            .first()
        )
        if not row or not row.setting_value:
            return None
        try:
            data = json.loads(row.setting_value)
        except Exception:
            return None
        for part in path:
            data = (data or {}).get(part) if isinstance(data, dict) else None
        return data or None

    try:
        return (
            _logo_from("INVOICE_COMPANY", ("logo",))
            or _logo_from("appSettings", ("company", "logo"))
            or None
        )
    except Exception as exc:
        logger.warning("Logo introuvable pour le filigrane : %s", exc)
        return None


def _decode_logo(logo_value: str) -> Optional["Image.Image"]:
    """Transforme la valeur du logo (data URI base64) en image RGBA."""
    if not logo_value:
        return None
    try:
        raw = logo_value.strip()
        if raw.startswith("data:image"):
            raw = raw.split(",", 1)[1]
        data = base64.b64decode(raw)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:
        logger.warning("Logo de filigrane illisible : %s", exc)
        return None


@lru_cache(maxsize=4)
def _prepared_logo(logo_value: str, opacity_x100: int) -> Optional["Image.Image"]:
    """Logo décodé et pré-atténué à l'opacité voulue. Mémoïsé (le logo change rarement)."""
    logo = _decode_logo(logo_value)
    if logo is None:
        return None
    # Applique l'opacité en atténuant le canal alpha existant.
    alpha = logo.split()[3].point(lambda a: int(a * (opacity_x100 / 100.0)))
    logo.putalpha(alpha)
    return logo


def apply_watermark(image_bytes: bytes, logo_value: Optional[str]) -> bytes:
    """Renvoie l'image avec le logo en filigrane, ou l'image d'origine si impossible."""
    if not _PIL_OK or not logo_value:
        return image_bytes

    try:
        base = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:
        logger.warning("Image de produit illisible, filigrane ignoré : %s", exc)
        return image_bytes

    # On garde le format d'origine pour réécrire dans le même (JPEG, PNG, WebP…).
    fmt = (base.format or "PNG").upper()
    base = base.convert("RGBA")

    logo = _prepared_logo(logo_value, int(WATERMARK_OPACITY * 100))
    if logo is None:
        return image_bytes

    # Redimensionne le logo proportionnellement à l'image.
    target_w = max(MIN_LOGO_WIDTH, int(base.width * WATERMARK_WIDTH_RATIO))
    scale = target_w / logo.width
    target_h = max(1, int(logo.height * scale))
    logo_resized = logo.resize((target_w, target_h), Image.LANCZOS)

    # Coin bas-droit, avec une marge.
    margin = int(base.width * WATERMARK_MARGIN_RATIO)
    x = base.width - logo_resized.width - margin
    y = base.height - logo_resized.height - margin

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay.paste(logo_resized, (max(0, x), max(0, y)), logo_resized)
    composed = Image.alpha_composite(base, overlay)

    out = io.BytesIO()
    if fmt in ("JPG", "JPEG"):
        # JPEG ne gère pas la transparence : on aplatit sur blanc.
        composed = composed.convert("RGB")
        composed.save(out, format="JPEG", quality=90)
    elif fmt == "WEBP":
        composed.save(out, format="WEBP", quality=90)
    else:
        composed.save(out, format="PNG")
    return out.getvalue()
