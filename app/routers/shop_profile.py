"""Profil de boutique — lecture et changement depuis l'écran Paramètres.

L'assistant peut configurer la boutique lui-même (outil `configurer_boutique`),
mais le commerçant doit pouvoir le faire à la main : voir ce qui est en vigueur,
comparer les métiers proposés, et basculer sans passer par une conversation.

Comme l'outil de l'assistant, ces routes ne détruisent rien : appliquer un
profil ajoute les catégories manquantes et laisse le reste en place.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import shop_profile
from ..auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/api/shop-profile", tags=["profil-boutique"])


class ProfilEntree(BaseModel):
    profil: str
    appliquer: bool = True


class ModulesEntree(BaseModel):
    modules: dict


def _exiger_admin(user):
    if getattr(user, "role", "") != "admin":
        return JSONResponse(status_code=403, content={
            "error": "reserve_admin",
            "message": "Seul un administrateur peut changer le métier de la "
                       "boutique."})
    return None


def _etat(db: Session) -> dict:
    courant = shop_profile.charger(db)
    return {
        "actuel": {
            "code": courant["code"],
            "libelle": courant["libelle"],
            "resume": courant["resume"],
            "applique": courant["applique"],
            "applique_le": courant["applique_le"],
            "applique_par": courant["applique_par"],
            "tracage": courant["tracage"],
            "tracage_libelle": courant["tracage_libelle"],
            "tracage_explication": courant["tracage_explication"],
            "libelles": courant["libelles"],
            "modules": {nom: shop_profile.module_actif(nom, db, connu=courant)
                        for nom in courant["modules"]},
        },
        "modules": shop_profile.catalogue_modules(db, connu=courant),
        "profils": shop_profile.catalogue(),
    }


@router.get("")
def lire(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Profil en vigueur et métiers proposés.

    Ouvert à tous les comptes : un vendeur a besoin de savoir dans quel métier
    il travaille pour lire son écran — seul le changement est réservé.
    """
    return _etat(db)


@router.put("")
def ecrire(donnees: ProfilEntree,
           user=Depends(get_current_user),
           db: Session = Depends(get_db)):
    """Retient le profil et, sauf demande contraire, prépare la boutique.

    `appliquer=false` sert au cas où le commerçant a déjà son propre catalogue
    et ne veut que les libellés et les modules du métier, sans catégories
    ajoutées.
    """
    refus = _exiger_admin(user)
    if refus is not None:
        return refus

    code = (donnees.profil or "").strip().lower()
    if not shop_profile.existe(code):
        possibles = [f["code"] for f in shop_profile.catalogue()]
        return JSONResponse(status_code=400, content={
            "error": "profil_inconnu",
            "message": f"Métier inconnu. Valeurs possibles : "
                       f"{', '.join(possibles)}."})

    auteur = getattr(user, "username", None)
    if donnees.appliquer:
        rapport = shop_profile.appliquer(db, code, applique_par=auteur)
        message = shop_profile.resume_rapport(rapport)
    else:
        shop_profile.enregistrer(db, code, applique_par=auteur, applique=False)
        rapport = None
        message = (f"Métier enregistré. Les libellés et les modules suivent "
                   f"désormais « {shop_profile.profil(code)['libelle']} ». "
                   "Aucune catégorie n'a été créée.")

    logging.info("[profil] boutique passée en « %s » par %s (appliquer=%s)",
                 code, auteur or "?", donnees.appliquer)

    reponse = _etat(db)
    reponse["message"] = message
    if rapport is not None:
        reponse["rapport"] = {
            "categories_creees": rapport["categories_creees"],
            "attributs_crees": rapport["attributs_crees"],
            "valeurs_ajoutees": rapport["valeurs_ajoutees"],
            "a_verifier": rapport["variantes_non_modifiees"],
            "categories_hors_profil": rapport["categories_hors_profil"],
        }
    return reponse


@router.put("/modules")
def ecrire_modules(donnees: ModulesEntree,
                   user=Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """Allume ou éteint un module, par-dessus ce que propose le métier.

    Sert au cas fréquent d'une boutique à cheval sur deux commerces : un
    magasin de mode qui répare aussi des machines à coudre garde son atelier,
    une supérette qui ne pèse rien éteint les unités de mesure.
    """
    refus = _exiger_admin(user)
    if refus is not None:
        return refus

    shop_profile.enregistrer_modules(db, donnees.modules)
    logging.info("[profil] modules ajustés par %s : %s",
                 getattr(user, "username", None) or "?", donnees.modules)

    reponse = _etat(db)
    reponse["message"] = ("Modules enregistrés. Rechargez la page pour voir la "
                          "navigation mise à jour.")
    return reponse
