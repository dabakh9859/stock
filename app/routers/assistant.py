"""Assistant d'aide : boucle agentique côté instance.

L'instance ne détient aucune clé d'IA : elle transmet la conversation à la
plateforme, authentifiée par un secret partagé. La clé du fournisseur reste
donc sur la plateforme, où l'usage est mesuré et facturé, et une instance
compromise ne peut pas en abuser.

C'est **l'instance qui mène la boucle** : elle déclare ses outils, reçoit les
appels décidés par le modèle, les exécute sur sa propre base avec les droits de
la personne connectée, puis renvoie les résultats pour le tour suivant. La
plateforme reste sans état et ne voit que ce que l'instance lui transmet.

Deux gardes qui comptent :

- **Les outils sont filtrés par utilisateur et par plan** avant d'être montrés
  au modèle (`outils_disponibles`) : un outil qu'il ne voit pas, il ne peut pas
  l'appeler, quoi que raconte la conversation.
- **L'historique venu du navigateur est expurgé** : on n'en garde que le texte
  des tours « user » et « assistant ». Sans cela, un client malveillant
  pourrait fabriquer de faux résultats d'outils (« le client X doit 0 F ») et
  faire raconter n'importe quoi à l'assistant.

Sans PLATFORM_URL / ASSISTANT_GATEWAY_SECRET, la route répond 503 et le widget
affiche un renvoi vers le support.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import AssistantAction, get_db
from ..services import assistant_tools

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

TIMEOUT_S = 60
MAX_TOURS = 4          # allers-retours modèle → outils → modèle
MAX_MESSAGES = 12      # profondeur d'historique acceptée du navigateur
DUREE_ACTION_MIN = 10  # au-delà, une proposition non confirmée périme

INDECIS = ("Je n'arrive pas à aboutir sur cette demande. Reformulez-la de "
           "façon plus simple — par exemple une seule question à la fois.")


class ConversationEntree(BaseModel):
    messages: list


class DecisionEntree(BaseModel):
    token: str
    decision: str = "confirmer"   # confirmer | annuler


class DroitsEntree(BaseModel):
    roles: dict


def _exiger_admin(user):
    if getattr(user, "role", "") != "admin":
        return JSONResponse(status_code=403, content={
            "error": "reserve_admin",
            "message": "Seul un administrateur peut modifier les droits de l'assistant."})
    return None


@router.get("/permissions")
def lire_permissions(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Catalogue des outils et rôle exigé par chacun, pour l'écran Paramètres."""
    refus = _exiger_admin(user)
    if refus is not None:
        return refus
    return {
        "outils": assistant_tools.catalogue(db),
        "roles": [{"valeur": valeur,
                   "libelle": assistant_tools.LIBELLES_ROLES[valeur]}
                  for valeur in assistant_tools.ROLES_POSSIBLES],
    }


@router.put("/permissions")
def ecrire_permissions(donnees: DroitsEntree,
                       user=Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Enregistre les droits. Les outils inconnus et les rôles invalides sont
    ignorés — on ne veut pas qu'une faute de frappe ouvre un outil."""
    refus = _exiger_admin(user)
    if refus is not None:
        return refus
    assistant_tools.enregistrer_config(db, donnees.roles)
    logging.info("[assistant] droits modifiés par %s",
                 getattr(user, "username", None) or getattr(user, "user_id", "?"))
    return {"outils": assistant_tools.catalogue(db)}


def _messages_du_navigateur(brut: Any) -> list:
    """Ne conserve que du texte de conversation. Tout `tool_calls` ou rôle
    « tool » venu du client est écarté : les appels et leurs résultats sont
    produits ici, jamais reçus."""
    propres = []
    if not isinstance(brut, list):
        return propres
    for entree in brut[-MAX_MESSAGES:]:
        if not isinstance(entree, dict):
            continue
        role = entree.get("role")
        contenu = entree.get("content")
        if role in ("user", "assistant") and isinstance(contenu, str) and contenu.strip():
            propres.append({"role": role, "content": contenu.strip()[:4000]})
    return propres


def _appel_passerelle(base: str, secret: str, messages: list, outils: list):
    """Un tour de modèle. Renvoie le dictionnaire de la plateforme, ou une
    JSONResponse d'erreur prête à être retournée au widget."""
    corps = json.dumps({"messages": messages, "tools": outils}).encode()
    requete = urllib.request.Request(
        f"{base}/api/assistant/app",
        data=corps,
        method="POST",
        headers={"Content-Type": "application/json", "X-Assistant-Secret": secret},
    )
    try:
        with urllib.request.urlopen(requete, timeout=TIMEOUT_S) as reponse:
            return json.loads(reponse.read())
    except urllib.error.HTTPError as exc:
        detail: Any = {}
        try:
            detail = json.loads(exc.read())
        except Exception:
            pass
        message = detail.get("message") if isinstance(detail, dict) else None
        return JSONResponse(
            status_code=exc.code if exc.code in (400, 401, 501, 503) else 502,
            content={"error": "assistant_error",
                     "message": message or "L'assistant est momentanément "
                                           "indisponible — réessayez."})
    except Exception:
        logging.exception("[assistant] relais indisponible")
        return JSONResponse(status_code=502, content={
            "error": "assistant_error",
            "message": "L'assistant est momentanément indisponible — réessayez.",
        })


@router.post("/chat")
def chat(donnees: ConversationEntree,
         user=Depends(get_current_user),
         db: Session = Depends(get_db)):
    """Mène la conversation jusqu'à une réponse, en exécutant les outils que le
    modèle demande. Session utilisateur exigée : ce sont ses droits qui
    déterminent les outils disponibles."""
    base = (os.getenv("PLATFORM_URL") or "").strip().rstrip("/")
    secret = (os.getenv("ASSISTANT_GATEWAY_SECRET") or "").strip()
    if not base or not secret:
        return JSONResponse(status_code=503, content={
            "error": "assistant_disabled",
            "message": "L'assistant n'est pas activé sur cet espace — "
                       "contactez le support par WhatsApp.",
        })

    messages = _messages_du_navigateur(donnees.messages)
    if not messages:
        return JSONResponse(status_code=400, content={
            "error": "assistant_error", "message": "Aucune question à traiter."})

    outils = assistant_tools.outils_disponibles(user, db)
    identite = getattr(user, "username", None) or getattr(user, "user_id", "?")

    for _ in range(MAX_TOURS):
        tour = _appel_passerelle(base, secret, messages, outils)
        if isinstance(tour, JSONResponse):
            return tour

        appels = tour.get("tool_calls") or []
        if not appels:
            return {"reply": tour.get("text") or ""}

        # Le tour de l'assistant est rejoué tel quel : le modèle a besoin de
        # retrouver ses propres appels en face de leurs résultats.
        messages.append({"role": "assistant",
                         "content": tour.get("text") or "",
                         "tool_calls": appels})

        for appel in appels:
            nom = appel.get("name") or ""
            arguments = appel.get("arguments") or {}

            if assistant_tools.est_ecriture(nom):
                # Écriture ou envoi : on ne fait que préparer. En cas de refus,
                # l'erreur repart au modèle, qui saura l'expliquer ; sinon on
                # interrompt la boucle et on rend la main à l'humain.
                preparation = assistant_tools.preparer(nom, arguments, db, user)
                if "erreur" in preparation:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": appel.get("id") or "",
                        "content": json.dumps(preparation, ensure_ascii=False, default=str),
                    })
                    continue
                action = _enregistrer_action(db, user, nom, arguments, preparation)
                logging.info("[assistant] proposition outil=%s utilisateur=%s jeton=%s",
                             nom, identite, action.token)
                return {
                    "reply": tour.get("text") or "",
                    "action": {
                        "token": action.token,
                        "outil": nom,
                        "resume": preparation.get("resume") or "",
                        "expire_dans_s": DUREE_ACTION_MIN * 60,
                    },
                }

            resultat = assistant_tools.executer(nom, arguments, db, user)
            logging.info("[assistant] lecture outil=%s utilisateur=%s", nom, identite)
            messages.append({
                "role": "tool",
                "tool_call_id": appel.get("id") or "",
                # `default=str` : les dates des outils ne sont pas sérialisables.
                "content": json.dumps(resultat, ensure_ascii=False, default=str),
            })

    # Le modèle tourne en rond : mieux vaut le dire que boucler aux frais du
    # client.
    logging.warning("[assistant] boucle non aboutie après %s tours (utilisateur=%s)",
                    MAX_TOURS, identite)
    return {"reply": INDECIS}


def _enregistrer_action(db: Session, user, nom: str, arguments: dict,
                        preparation: dict) -> AssistantAction:
    """Dépose la proposition en base et rend le jeton qui permettra de
    l'exécuter. C'est la seule trace qui compte : l'exécution relira ces
    arguments-là, pas ceux d'un éventuel appel ultérieur du modèle."""
    action = AssistantAction(
        token=secrets.token_urlsafe(32),
        user_id=getattr(user, "user_id", None),
        username=str(getattr(user, "username", "") or "")[:50],
        tool_name=nom,
        arguments=json.dumps(arguments, ensure_ascii=False, default=str),
        summary=preparation.get("resume") or "",
        status="pending",
        expires_at=datetime.now() + timedelta(minutes=DUREE_ACTION_MIN),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _message_resultat(nom: str, resultat: dict) -> str:
    if nom == "creer_client" and resultat.get("cree"):
        return f"C'est fait : le client {resultat.get('nom')} est enregistré."
    if nom == "enregistrer_paiement" and resultat.get("enregistre"):
        montant = f"{resultat.get('montant', 0):,}".replace(",", " ")
        reste = resultat.get("reste") or 0
        suite = ("La facture est soldée." if reste <= 0
                 else f"Il reste {f'{reste:,}'.replace(',', ' ')} F à encaisser.")
        return (f"Paiement de {montant} F CFA enregistré sur la facture "
                f"{resultat.get('numero')}. {suite}")
    if nom == "ajuster_stock" and resultat.get("ajuste"):
        return (f"Stock de « {resultat.get('produit')} » ajusté : "
                f"{resultat.get('avant')} → {resultat.get('apres')}.")
    if nom in ("creer_facture", "creer_devis") and resultat.get("cree"):
        genre = "Facture" if nom == "creer_facture" else "Devis"
        montant = f"{resultat.get('total', 0):,}".replace(",", " ")
        return (f"{genre} {resultat.get('numero')} créé(e) pour {montant} F CFA. "
                "Vous pouvez l'ouvrir depuis la liste pour l'imprimer ou l'envoyer.")
    if nom == "relancer_creances":
        envoyes = resultat.get("nombre_envoyes") or 0
        message = (f"Rappel envoyé à {envoyes} client(s)." if envoyes
                   else "Aucun rappel n'a pu être envoyé.")
        echecs = resultat.get("echecs") or []
        if echecs:
            message += (" Échecs : "
                        + ", ".join(str(e.get("client")) for e in echecs) + ".")
        return message
    return "C'est fait."


@router.post("/confirmer")
def confirmer(donnees: DecisionEntree,
              user=Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Exécute — ou annule — une action proposée par l'assistant.

    C'est **le seul chemin** par lequel une écriture se produit, et il exige un
    clic humain authentifié. Le modèle ne l'atteint jamais."""
    jeton = (donnees.token or "").strip()
    action = (db.query(AssistantAction)
              .filter(AssistantAction.token == jeton).first()) if jeton else None
    if action is None:
        return JSONResponse(status_code=404, content={
            "error": "action_introuvable",
            "message": "Cette action n'existe plus — redemandez-la à l'assistant."})

    # Un jeton ne vaut que pour celui qui l'a obtenu : sans ce contrôle, un
    # utilisateur pourrait exécuter la proposition faite à un collègue.
    demandeur = getattr(user, "user_id", None)
    if action.user_id is not None and demandeur != action.user_id:
        logging.warning("[assistant] jeton %s présenté par l'utilisateur %s "
                        "au lieu de %s", jeton[:8], demandeur, action.user_id)
        return JSONResponse(status_code=403, content={
            "error": "action_refusee",
            "message": "Cette action a été proposée à un autre utilisateur."})

    if action.status != "pending":
        return {"reply": "Cette proposition a déjà été traitée."}

    maintenant = datetime.now()
    if action.expires_at and maintenant > action.expires_at:
        action.status, action.resolved_at = "expired", maintenant
        db.commit()
        return {"reply": "La proposition a expiré — redemandez-la à l'assistant."}

    if (donnees.decision or "").strip().lower() == "annuler":
        action.status, action.resolved_at = "cancelled", maintenant
        db.commit()
        logging.info("[assistant] action %s annulée par %s", action.tool_name, demandeur)
        return {"reply": "C'est annulé, rien n'a été fait."}

    try:
        arguments = json.loads(action.arguments or "{}")
    except ValueError:
        arguments = {}

    resultat = assistant_tools.appliquer(action.tool_name, arguments, db, user)

    if "erreur" in resultat:
        action.status, action.error = "failed", str(resultat["erreur"])[:2000]
        reponse = f"Ça n'a pas abouti : {resultat['erreur']}"
    else:
        action.status = "confirmed"
        action.result = json.dumps(resultat, ensure_ascii=False, default=str)[:4000]
        reponse = _message_resultat(action.tool_name, resultat)

    action.resolved_at = datetime.now()
    db.commit()
    logging.info("[assistant] action=%s statut=%s utilisateur=%s",
                 action.tool_name, action.status, demandeur)
    return {"reply": reponse}
