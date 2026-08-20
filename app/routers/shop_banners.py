"""Bannières éditoriales de la boutique en ligne.

Deux surfaces dans un seul module :

* `/api/shop/banners` — lecture publique, consommée par le site. Elle ne renvoie
  que les bannières actives, dans l'ordre voulu, avec des URL servables.
* `/api/shop-admin/banners` — création, modification, téléversement et
  suppression, réservées aux administrateurs.

Le média est stocké sur disque comme les photos produit, et seul son chemin
relatif vit en base. On garde ainsi le même mécanisme de sauvegarde et de
service statique que pour le reste du catalogue.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import ShopBanner, get_db
from ..auth import get_current_user

router = APIRouter(tags=["boutique - bannières"])

# --- Emplacements ---------------------------------------------------------
#
# La liste est fermée et partagée avec le site. Un emplacement inconnu
# n'afficherait rien : mieux vaut refuser la valeur à la saisie que laisser une
# bannière invisible sans que personne comprenne pourquoi.
EMPLACEMENTS = {
    "apres_vitrine": "Après la vitrine Apple",
    "apres_categories": "Après les catégories",
    "apres_vedettes": "Après les produits en vedette",
    "apres_accessoires": "Après les accessoires",
    "avant_pied": "Juste avant le pied de page",
}

TYPES_MEDIA = {"image", "video"}

_DOSSIER = "static/uploads/banners"

# Extensions acceptées. La vidéo est limitée au MP4 et au WebM : ce sont les
# deux formats que lisent tous les navigateurs visés, et accepter un .mov de
# téléphone donnerait une bannière qui ne se lit que sur iPhone.
EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".avif"},
    "video": {".mp4", ".webm"},
}

# 12 Mo. Une bannière est vue par chaque visiteur, et souvent sur une connexion
# mobile dakaroise : au-delà, elle coûte plus de ventes qu'elle n'en rapporte.
POIDS_MAX = 12 * 1024 * 1024


def _exiger_admin(utilisateur) -> None:
    if getattr(utilisateur, "role", None) not in ("admin", "manager"):
        raise HTTPException(
            status_code=403,
            detail="La gestion des bannières est réservée aux administrateurs.",
        )


def _nom_de_fichier(nom: str) -> str:
    """Nom sûr, sans accent ni espace, préfixé d'un horodatage."""
    base, ext = os.path.splitext(nom)
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower()[:60] or "banniere"
    return f"{int(time.time())}-{base}{ext.lower()}"


def _url_publique(chemin: Optional[str]) -> Optional[str]:
    if not chemin:
        return None
    chemin = str(chemin).strip().replace("\\", "/")
    if chemin.startswith(("http://", "https://")):
        return chemin
    return "/" + chemin.lstrip("/")


def _en_dict(b: ShopBanner) -> dict:
    return {
        "id": b.banner_id,
        "media_type": b.media_type,
        "media_url": _url_publique(b.media_path),
        "poster_url": _url_publique(b.poster_path),
        "title": b.title,
        "subtitle": b.subtitle,
        "link_label": b.link_label,
        "link_url": b.link_url,
        "placement": b.placement,
        "sort_order": b.sort_order or 0,
        "is_active": bool(b.is_active),
    }


# =========================================================================
#  Lecture publique — consommée par le site
# =========================================================================

@router.get("/api/shop/banners")
def lister_publiques(db: Session = Depends(get_db)):
    """Bannières actives, groupées par emplacement."""
    lignes = (
        db.query(ShopBanner)
        .filter(ShopBanner.is_active.is_(True))
        .order_by(ShopBanner.sort_order, ShopBanner.banner_id)
        .all()
    )
    return {"banners": [_en_dict(b) for b in lignes]}


# =========================================================================
#  Administration
# =========================================================================

class BanniereModif(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    subtitle: Optional[str] = Field(None, max_length=240)
    link_label: Optional[str] = Field(None, max_length=60)
    link_url: Optional[str] = Field(None, max_length=500)
    placement: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("/api/shop-admin/banners")
def lister_toutes(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    _exiger_admin(current_user)
    lignes = (
        db.query(ShopBanner)
        .order_by(ShopBanner.placement, ShopBanner.sort_order, ShopBanner.banner_id)
        .all()
    )
    return {
        "banners": [_en_dict(b) for b in lignes],
        "placements": EMPLACEMENTS,
    }


@router.post("/api/shop-admin/banners")
async def creer(
    fichier: UploadFile = File(...),
    poster: Optional[UploadFile] = File(None),
    media_type: str = Form("image"),
    placement: str = Form("apres_categories"),
    title: Optional[str] = Form(None),
    subtitle: Optional[str] = Form(None),
    link_label: Optional[str] = Form(None),
    link_url: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Téléverse le média et crée la bannière."""
    _exiger_admin(current_user)

    if media_type not in TYPES_MEDIA:
        raise HTTPException(400, f"Type de média inconnu : {media_type}")
    if placement not in EMPLACEMENTS:
        raise HTTPException(400, f"Emplacement inconnu : {placement}")

    ext = os.path.splitext(fichier.filename or "")[1].lower()
    if ext not in EXTENSIONS[media_type]:
        attendus = ", ".join(sorted(EXTENSIONS[media_type]))
        raise HTTPException(400, f"Extension {ext or '(aucune)'} refusée. Attendu : {attendus}")

    contenu = await fichier.read()
    if len(contenu) > POIDS_MAX:
        raise HTTPException(
            413,
            f"Fichier de {len(contenu) // 1024 // 1024} Mo : la limite est de "
            f"{POIDS_MAX // 1024 // 1024} Mo. Une bannière trop lourde est "
            "chargée par chaque visiteur, souvent en 4G.",
        )
    if not contenu:
        raise HTTPException(400, "Fichier vide.")

    os.makedirs(_DOSSIER, exist_ok=True)
    nom = _nom_de_fichier(fichier.filename or f"banniere{ext}")
    with open(os.path.join(_DOSSIER, nom), "wb") as sortie:
        sortie.write(contenu)

    chemin_poster = None
    if poster is not None and poster.filename:
        ext_p = os.path.splitext(poster.filename)[1].lower()
        if ext_p not in EXTENSIONS["image"]:
            raise HTTPException(400, f"Image d'attente refusée : {ext_p}")
        donnees = await poster.read()
        if donnees:
            nom_p = _nom_de_fichier(poster.filename)
            with open(os.path.join(_DOSSIER, nom_p), "wb") as sortie:
                sortie.write(donnees)
            chemin_poster = f"{_DOSSIER}/{nom_p}"

    dernier = (
        db.query(ShopBanner)
        .filter(ShopBanner.placement == placement)
        .order_by(ShopBanner.sort_order.desc())
        .first()
    )

    banniere = ShopBanner(
        media_type=media_type,
        media_path=f"{_DOSSIER}/{nom}",
        poster_path=chemin_poster,
        title=(title or "").strip() or None,
        subtitle=(subtitle or "").strip() or None,
        link_label=(link_label or "").strip() or None,
        link_url=(link_url or "").strip() or None,
        placement=placement,
        sort_order=((dernier.sort_order or 0) + 1) if dernier else 0,
        is_active=True,
    )
    db.add(banniere)
    db.commit()
    db.refresh(banniere)
    return _en_dict(banniere)


@router.put("/api/shop-admin/banners/{banner_id}")
def modifier(
    banner_id: int,
    modif: BanniereModif,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _exiger_admin(current_user)
    banniere = db.query(ShopBanner).filter(ShopBanner.banner_id == banner_id).first()
    if not banniere:
        raise HTTPException(404, "Bannière introuvable.")

    donnees = modif.model_dump(exclude_unset=True)
    if "placement" in donnees and donnees["placement"] not in EMPLACEMENTS:
        raise HTTPException(400, f"Emplacement inconnu : {donnees['placement']}")

    for champ, valeur in donnees.items():
        if isinstance(valeur, str):
            valeur = valeur.strip() or None
        setattr(banniere, champ, valeur)

    db.commit()
    db.refresh(banniere)
    return _en_dict(banniere)


@router.delete("/api/shop-admin/banners/{banner_id}")
def supprimer(
    banner_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Supprime la bannière et ses fichiers."""
    _exiger_admin(current_user)
    banniere = db.query(ShopBanner).filter(ShopBanner.banner_id == banner_id).first()
    if not banniere:
        raise HTTPException(404, "Bannière introuvable.")

    # Le fichier part avec l'enregistrement : sans cela le dossier enfle
    # indéfiniment de médias que plus rien ne référence.
    for chemin in (banniere.media_path, banniere.poster_path):
        if not chemin:
            continue
        try:
            os.remove(chemin)
        except OSError:
            pass  # fichier déjà absent : rien à réparer

    db.delete(banniere)
    db.commit()
    return {"supprime": banner_id}
