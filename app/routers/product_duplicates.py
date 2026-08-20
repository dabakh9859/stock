"""API du gestionnaire de doublons de produits.

La détection et la fusion vivent dans `app.services.product_duplicates` ; ce
module ne fait que les exposer et poser les garde-fous d'accès.

La fusion réécrit des lignes de facture et de vente : elle est réservée aux
administrateurs et confirmée côté écran par un récapitulatif de ce qui sera
déplacé.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..auth import get_current_user
from ..services.product_duplicates import detecter, fusionner

router = APIRouter(prefix="/api/products/duplicates", tags=["doublons"])


class DemandeFusion(BaseModel):
    garde_id: int = Field(..., description="Fiche qui reçoit tout")
    absorbes_ids: list[int] = Field(..., min_length=1)
    nom: str | None = Field(None, description="Nom corrigé de la fiche gardée")


def _exiger_admin(utilisateur) -> None:
    role = getattr(utilisateur, "role", None)
    if role not in ("admin", "manager"):
        raise HTTPException(
            status_code=403,
            detail="La fusion de fiches produit est réservée aux administrateurs.",
        )


@router.get("")
def lister(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Groupes de fiches actives partageant le même nom."""
    groupes = detecter(db)
    return {
        "groupes": [
            {
                "cle": g.cle,
                "libelle": g.libelle,
                "generique": g.generique,
                "echange": g.echange,
                "suggestion_id": g.suggestion_id,
                "stock_total": g.stock_total,
                "variantes_totales": g.variantes_totales,
                "ventes_totales": g.ventes_totales,
                "fiches": [
                    {
                        "product_id": f.product_id,
                        "name": f.name,
                        "source": f.source,
                        "category": f.category,
                        "quantity": f.quantity,
                        "variantes": f.variantes_disponibles,
                        "ventes": f.ventes,
                        "price": f.price,
                        "image_path": f.image_path,
                        "entry_date": f.entry_date.isoformat() if f.entry_date else None,
                    }
                    for f in g.fiches
                ],
            }
            for g in groupes
        ],
        "total_groupes": len(groupes),
        "total_fiches": sum(len(g.fiches) for g in groupes),
    }


@router.post("/merge")
def fusionner_fiches(demande: DemandeFusion, db: Session = Depends(get_db),
                     current_user=Depends(get_current_user)):
    """Rattache les fiches absorbées à la fiche gardée, puis les archive."""
    _exiger_admin(current_user)
    try:
        resultat = fusionner(db, demande.garde_id, demande.absorbes_ids, demande.nom)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        # Couverture incomplète : refuser plutôt que de laisser des lignes
        # pointer vers une fiche archivée.
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    except Exception:
        db.rollback()
        raise

    db.commit()
    return resultat
