"""Authentification — jetons de session, et ce qui permet de les révoquer.

Un JWT est autonome : une fois signé, il vaut jusqu'à son échéance, et rien
dans le jeton lui-même ne dit qu'entre-temps le compte a été désactivé,
rétrogradé ou supprimé. Avec une durée de sept jours, cela signifiait qu'un
vendeur congédié gardait l'accès une semaine.

Deux mécanismes y répondent ici :

* **L'état du compte est relu en base**, plus déduit des attributions du jeton.
  Rôle et activité viennent donc de la vérité, pas d'une photographie.
* **Une génération de jeton** (`token_epoch`) accompagne chaque session. La
  changer invalide d'un coup tous les jetons déjà émis pour ce compte — c'est
  le « déconnecter partout » qu'un JWT ne sait pas faire seul.

Reste la raison qui avait fait supprimer la vérification en base : une requête
par appel ralentissait sensiblement le chargement des listes, où une seule page
déclenche beaucoup d'appels. D'où le cache ci-dessous — courte durée, et vidé
explicitement dès qu'un compte change.
"""

import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import Cookie, Depends, Header, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import User, get_db

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

# Mode sans base : les attributions du jeton font foi. Plus rapide, mais on ne
# peut alors plus déconnecter personne avant l'échéance. Réservé à un contexte
# où la révocation est assurée autrement (passerelle en amont, jetons courts).
# Le défaut est « false » : la sécurité d'abord, le cache s'occupe du reste.
AUTH_TRUST_JWT_CLAIMS = str(os.getenv("AUTH_TRUST_JWT_CLAIMS", "false")).lower() == "true"

# Durée de vie du cache d'état des comptes. C'est le délai maximal entre une
# désactivation et sa prise d'effet sur un autre ouvrier ; sur celui qui a reçu
# la modification, l'effet est immédiat (voir `invalider_utilisateur`).
AUTH_CACHE_SECONDS = float(os.getenv("AUTH_CACHE_SECONDS", "30"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Cache d'état des comptes
# ---------------------------------------------------------------------------
# Clé : user_id. Valeur : (posé_à, état) où `état` vaut None pour un compte
# supprimé — un jeton orphelin ne doit pas provoquer une requête à chaque appel.
_cache_comptes: dict = {}
_verrou_cache = threading.Lock()


def invalider_utilisateur(user_id: Optional[int] = None) -> None:
    """Oublie l'état mis en cache. Appelée par les routes qui modifient un
    compte, pour que le changement prenne effet sans attendre l'expiration.
    Sans argument, vide tout le cache."""
    with _verrou_cache:
        if user_id is None:
            _cache_comptes.clear()
        else:
            _cache_comptes.pop(int(user_id), None)


def _etat_compte(db: Session, user_id: int) -> Optional[dict]:
    """Rôle, activité et génération de jeton du compte, éventuellement en cache.

    Rend None si le compte n'existe plus.
    """
    maintenant = time.monotonic()
    with _verrou_cache:
        entree = _cache_comptes.get(user_id)
        if entree is not None and maintenant - entree[0] < AUTH_CACHE_SECONDS:
            return entree[1]

    utilisateur = db.query(User).filter(User.user_id == user_id).first()
    etat = None
    if utilisateur is not None:
        etat = {
            "user_id": utilisateur.user_id,
            "username": utilisateur.username,
            "email": utilisateur.email,
            "full_name": utilisateur.full_name,
            "role": utilisateur.role or "user",
            "is_active": bool(utilisateur.is_active),
            "token_epoch": int(getattr(utilisateur, "token_epoch", 0) or 0),
        }

    with _verrou_cache:
        _cache_comptes[user_id] = (maintenant, etat)
    return etat


def revoquer_sessions(db: Session, user_id: int) -> int:
    """Invalide tous les jetons déjà émis pour ce compte.

    Employée au changement de mot de passe et à la désactivation : c'est le seul
    moyen de mettre fin à une session en cours, un JWT ne se reprenant pas.
    Rend la nouvelle génération.
    """
    utilisateur = db.query(User).filter(User.user_id == user_id).first()
    if utilisateur is None:
        return 0
    utilisateur.token_epoch = int(getattr(utilisateur, "token_epoch", 0) or 0) + 1
    db.commit()
    invalider_utilisateur(user_id)
    return utilisateur.token_epoch

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return payload
    except JWTError:
        return None

class AuthUser:
    """Lightweight user built from JWT claims when DB-free auth is enabled."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

def get_current_user(
    authorization: Optional[str] = Header(None),
    gt_access: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Extraire le token d'abord depuis le cookie HttpOnly "gt_access" (source de vérité),
    # puis éventuellement depuis l'en-tête Authorization si présent et valide.
    token: Optional[str] = None
    if gt_access:
        token = gt_access
    elif authorization and authorization.startswith("Bearer "):
        possible = authorization.split(" ", 1)[1]
        # Ignorer les placeholders hérités comme "cookie-based"
        if possible and possible.lower() != "cookie-based":
            token = possible

    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    attributions = {
        "username": payload.get("sub"),
        "user_id": payload.get("user_id"),
        "email": payload.get("email"),
        "full_name": payload.get("full_name"),
        "role": payload.get("role", "user"),
        "is_active": payload.get("is_active", True),
    }

    if AUTH_TRUST_JWT_CLAIMS:
        # Mode sans base, explicitement demandé. Le jeton fait foi : on ne sait
        # donc pas si le compte a été désactivé depuis son émission.
        if not bool(attributions.get("is_active", True)):
            raise credentials_exception
        return AuthUser(**attributions)

    identifiant = attributions.get("user_id")
    if identifiant is None:
        # Jeton sans identifiant : rien à vérifier en base, donc rien à
        # révoquer. On refuse plutôt que de le laisser passer sur sa seule
        # signature.
        raise credentials_exception

    etat = _etat_compte(db, int(identifiant))
    if etat is None:
        # Compte supprimé entre-temps.
        raise credentials_exception
    if not etat["is_active"]:
        raise credentials_exception

    # Génération : un jeton d'avant le dernier changement de mot de passe, ou
    # d'avant une révocation, ne vaut plus rien. Les jetons émis avant
    # l'introduction de ce champ n'en portent pas — ils valent la génération 0,
    # celle des comptes qui n'ont jamais été révoqués, et restent donc valables.
    if int(payload.get("epoch", 0) or 0) != etat["token_epoch"]:
        raise credentials_exception

    # Le rôle et l'activité viennent de la base, jamais du jeton : un compte
    # rétrogradé perd ses droits immédiatement, sans attendre l'échéance.
    return AuthUser(**{**attributions, **{
        "username": etat["username"],
        "user_id": etat["user_id"],
        "email": etat["email"],
        "full_name": etat["full_name"],
        "role": etat["role"],
        "is_active": etat["is_active"],
    }})

def require_role(required_role: str):
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role != required_role and current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return role_checker

def get_current_active_user(current_user: User = Depends(get_current_user)):
    # Works for both ORM User and AuthUser (claims)
    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def require_any_role(roles: list[str]):
    """Authorize if user's role is in roles OR user is admin."""
    def checker(current_user: User = Depends(get_current_user)):
        r = getattr(current_user, "role", "user")
        if r == "admin":
            return current_user
        if r not in set(roles or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return checker
