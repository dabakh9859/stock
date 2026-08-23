"""Configuration publique minimale du site de présentation.

Cette route ne touche ni la base de données ni les données commerciales. Elle
permet au frontend marketing de reprendre le nom, l'adresse de connexion et les
profils métiers réellement disponibles dans l'application.
"""

import os

from fastapi import APIRouter

from .. import shop_profile

router = APIRouter(prefix="/api/public", tags=["site-public"])


@router.get("/site-config")
def site_config() -> dict:
    app_name = os.getenv("APP_NAME", "Stock")
    app_url = os.getenv("APP_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    profiles = []
    for profile in shop_profile.catalogue():
        profiles.append({
            "code": profile["code"],
            "label": profile["libelle"],
            "summary": profile["resume"],
            "examples": list(profile.get("exemples", []))[:2],
        })

    return {
        "appName": app_name,
        "appUrl": app_url,
        "loginUrl": f"{app_url}/login",
        "currency": "XOF",
        "locale": "fr-SN",
        "profiles": profiles,
    }
