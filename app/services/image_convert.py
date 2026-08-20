"""Conversion des images produits en AVIF.

L'AVIF divise le poids des visuels par ~10 à qualité visuellement identique.
Toutes les images produits déposées dans l'application y passent, quel que soit
leur format d'origine.

Le traitement est volontairement tolérant : si quoi que ce soit échoue
(format exotique, image corrompue, encodeur indisponible), on renvoie l'image
d'origine inchangée plutôt que de faire échouer l'upload. Une image au mauvais
format vaut mieux qu'un produit sans image.
"""

from __future__ import annotations

import io
import logging
from typing import Optional, Tuple

# Qualité d'encodage. 75 laisse de la marge pour les photos prises en boutique,
# plus bruitées que les rendus sur fond blanc des catalogues fournisseurs.
AVIF_QUALITY = 75

AVIF_EXTENSION = ".avif"


def _load_pillow():
    """Importe Pillow et amorce les greffons. Renvoie None si indisponible."""
    try:
        from PIL import Image, ImageOps

        # Les greffons de Pillow se chargent à la demande : sans cet appel,
        # Image.SAVE est vide et le test de disponibilité de l'AVIF échoue à tort.
        Image.init()
        return Image, ImageOps
    except Exception as exc:  # pragma: no cover - dépend de l'installation
        logging.warning("Pillow indisponible, conversion AVIF ignorée: %s", exc)
        return None


def avif_available() -> bool:
    """Indique si l'encodeur AVIF est utilisable dans cet environnement."""
    loaded = _load_pillow()
    if loaded is None:
        return False
    Image, _ = loaded
    return "AVIF" in Image.SAVE


def to_avif(
    image_bytes: bytes,
    original_extension: str = "",
    quality: int = AVIF_QUALITY,
) -> Tuple[bytes, str]:
    """Convertit une image en AVIF.

    Renvoie `(données, extension)`. En cas d'échec ou d'image inconvertible,
    renvoie les octets d'origine et l'extension d'origine — l'appelant n'a donc
    pas à distinguer les deux cas, il écrit ce qu'il reçoit sous l'extension
    qu'il reçoit.
    """
    fallback = (image_bytes, original_extension)

    loaded = _load_pillow()
    if loaded is None:
        return fallback
    Image, ImageOps = loaded

    if "AVIF" not in Image.SAVE:
        logging.warning("Encodeur AVIF absent de Pillow, image conservée telle quelle")
        return fallback

    try:
        source = Image.open(io.BytesIO(image_bytes))

        # Les images animées perdraient leur animation : on les laisse intactes.
        if getattr(source, "n_frames", 1) > 1:
            return fallback

        # Redresse selon l'orientation EXIF, sinon les photos prises au
        # téléphone ressortent tournées (l'AVIF ne conserve pas ce champ).
        source = ImageOps.exif_transpose(source)

        # Palettes et niveaux de gris à transparence n'ont pas d'équivalent
        # direct ; CMJN n'est pas encodable. On ramène à RGB/RGBA.
        if source.mode in ("P", "LA"):
            source = source.convert("RGBA")
        elif source.mode not in ("RGB", "RGBA"):
            source = source.convert("RGB")

        out = io.BytesIO()
        source.save(out, format="AVIF", quality=quality)
        data = out.getvalue()

        if not data:
            return fallback

        return data, AVIF_EXTENSION

    except Exception as exc:
        logging.warning("Conversion AVIF impossible, image conservée: %s", exc)
        return fallback
