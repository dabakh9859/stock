"""Outils de l'assistant — lecture directe, écritures sous confirmation.

Deux familles d'outils :

- **lecture** (`fn`) : exécutée immédiatement, sans effet de bord ;
- **écriture ou envoi** (`prepare` + `apply`) : `prepare` ne fait que valider et
  produire un résumé lisible ; seul `apply`, déclenché par un clic humain sur
  une action enregistrée en base, produit l'effet. Le modèle n'a donc jamais la
  main sur une écriture, même entièrement détourné par une consigne cachée dans
  les données.

Les outils s'exécutent **dans l'instance du client**, sur sa propre base et
avec les droits de la personne connectée : un vendeur ne peut pas faire dire à
l'assistant ce qu'il n'a pas le droit de voir, et le cloisonnement par plan
d'abonnement s'applique sans code supplémentaire (`outils_disponibles` filtre
la liste avant même de la montrer au modèle).

Aucun outil n'écrit ni n'envoie quoi que ce soit : la phase 1 est en lecture
seule. Les écritures viendront avec leur écran de confirmation.

Minimisation des données. Ce qui part chez le fournisseur du modèle est réduit
au nécessaire :
  - les **identifiants** partent toujours (l'application résout ensuite
    localement, et l'assistant peut désigner une ligne sans ambiguïté) ;
  - les noms de clients sont **tronqués** (« Abdou D. (#42) ») : le commerçant
    reconnaît son client, un tiers beaucoup moins ;
  - téléphone, e-mail, adresse, NINEA et notes **ne sortent jamais** — aucun
    outil de lecture n'en a besoin pour raisonner.
Les montants et les dates, eux, partent en clair : c'est l'objet de l'outil.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..database import Client, ClientDebt, Invoice, Maintenance, Product, ProductVariant
from ..plan import has_feature

# Bornes dures : le modèle peut demander n'importe quoi, et une liste de 5 000
# lignes ferait exploser le coût du tour sans rien apporter.
LIMITE_DEFAUT = 10
LIMITE_MAX = 40
JOURS_MAX = 730

STATUT_ANNULEE = "annulée"


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def _entier(valeur: Any, defaut: int, mini: int, maxi: int) -> int:
    """Le modèle peut envoyer « 20 », 20.0, None ou n'importe quoi : on ramène
    toujours dans les bornes plutôt que de laisser passer une requête absurde."""
    try:
        n = int(float(valeur))
    except (TypeError, ValueError):
        return defaut
    return max(mini, min(maxi, n))


def _texte(valeur: Any, longueur: int = 120) -> str:
    return (valeur or "").strip()[:longueur] if isinstance(valeur, str) else ""


def _fcfa(valeur: Any) -> int:
    """Le franc CFA n'a pas de centimes : renvoyer un entier évite au modèle
    d'écrire « 45000.00 F »."""
    if valeur is None:
        return 0
    try:
        return int(round(float(valeur)))
    except (TypeError, ValueError):
        return 0


def _etiquette_client(client: Optional[Client]) -> str:
    """« Abdou D. (#42) » — reconnaissable par le commerçant, peu identifiant
    pour un tiers. Voir la note de minimisation en tête de fichier."""
    if client is None:
        return "Client de passage"
    morceaux = (client.name or "").strip().split()
    if not morceaux:
        return f"Client #{client.client_id}"
    tete = morceaux[0][:20]
    if len(morceaux) > 1 and morceaux[-1]:
        tete = f"{tete} {morceaux[-1][0].upper()}."
    return f"{tete} (#{client.client_id})"


def _depuis(jours: int) -> datetime:
    return datetime.now() - timedelta(days=jours)


def _quantites_serialisees(db: Session, product_ids: list[int]) -> dict[int, int]:
    """Pour un produit suivi par IMEI/série, le stock réel est le nombre de
    variantes non vendues — `Product.quantity` ne le reflète pas toujours."""
    if not product_ids:
        return {}
    lignes = (
        db.query(ProductVariant.product_id, func.count(ProductVariant.variant_id))
        .filter(ProductVariant.product_id.in_(product_ids),
                ProductVariant.is_sold == False)  # noqa: E712
        .group_by(ProductVariant.product_id)
        .all()
    )
    return {pid: int(n) for pid, n in lignes}


def _stock_reel(produit: Product, serialisees: dict[int, int]) -> int:
    if produit.has_unique_serial:
        return serialisees.get(produit.product_id, 0)
    return int(produit.quantity or 0)


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _chercher_produit(db: Session, user, args: dict) -> dict:
    terme = _texte(args.get("terme"))
    limite = _entier(args.get("limite"), LIMITE_DEFAUT, 1, LIMITE_MAX)

    requete = db.query(Product).filter(Product.is_archived == False)  # noqa: E712
    if terme:
        motif = f"%{terme}%"
        requete = requete.filter(or_(
            Product.name.ilike(motif),
            Product.brand.ilike(motif),
            Product.model.ilike(motif),
            Product.barcode.ilike(motif),
            Product.category.ilike(motif),
        ))
    produits = requete.order_by(Product.name).limit(limite).all()
    serialisees = _quantites_serialisees(
        db, [p.product_id for p in produits if p.has_unique_serial])

    return {
        "nombre": len(produits),
        "produits": [{
            "id": p.product_id,
            "nom": p.name,
            "categorie": p.category,
            "marque": p.brand,
            "stock": _stock_reel(p, serialisees),
            "prix": _fcfa(p.price),
            "suivi_par_serie": bool(p.has_unique_serial),
        } for p in produits],
    }


def _etat_stock(db: Session, user, args: dict) -> dict:
    seuil = _entier(args.get("seuil"), 5, 0, 10_000)
    categorie = _texte(args.get("categorie"), 60)
    limite = _entier(args.get("limite"), 20, 1, LIMITE_MAX)

    requete = db.query(Product).filter(
        Product.is_archived == False,  # noqa: E712
        Product.shop_only == False,    # noqa: E712
    )
    if categorie:
        requete = requete.filter(Product.category.ilike(f"%{categorie}%"))

    # Les produits sérialisés doivent être comptés par variantes : on filtre
    # donc en Python après un premier tri large, plutôt qu'en SQL sur une
    # colonne qui mentirait pour eux.
    produits = requete.order_by(Product.quantity).limit(300).all()
    serialisees = _quantites_serialisees(
        db, [p.product_id for p in produits if p.has_unique_serial])

    faibles = []
    for p in produits:
        stock = _stock_reel(p, serialisees)
        if stock <= seuil:
            faibles.append({
                "id": p.product_id,
                "nom": p.name,
                "categorie": p.category,
                "stock": stock,
                "prix": _fcfa(p.price),
                "en_rupture": stock <= 0,
            })
    faibles.sort(key=lambda x: x["stock"])

    return {
        "seuil": seuil,
        "nombre_total": len(faibles),
        "en_rupture": sum(1 for f in faibles if f["en_rupture"]),
        "produits": faibles[:limite],
    }


def _encours_par_client(db: Session, client_ids: Optional[list[int]] = None) -> dict[int, dict]:
    """Encours d'un client = factures non soldées + créances manuelles."""
    encours: dict[int, dict] = {}

    requete = db.query(
        Invoice.client_id,
        func.sum(Invoice.remaining_amount),
        func.count(Invoice.invoice_id),
        func.min(Invoice.date),
    ).filter(
        Invoice.remaining_amount > 0,
        Invoice.status != STATUT_ANNULEE,
        Invoice.client_id.isnot(None),
    )
    if client_ids:
        requete = requete.filter(Invoice.client_id.in_(client_ids))
    for cid, reste, nb, plus_ancienne in requete.group_by(Invoice.client_id).all():
        encours[cid] = {"montant": _fcfa(reste), "factures": int(nb or 0),
                        "plus_ancienne": plus_ancienne}

    requete = db.query(
        ClientDebt.client_id,
        func.sum(ClientDebt.remaining_amount),
        func.min(ClientDebt.date),
    ).filter(
        ClientDebt.remaining_amount > 0,
        ClientDebt.client_id.isnot(None),
    )
    if client_ids:
        requete = requete.filter(ClientDebt.client_id.in_(client_ids))
    for cid, reste, plus_ancienne in requete.group_by(ClientDebt.client_id).all():
        entree = encours.setdefault(cid, {"montant": 0, "factures": 0, "plus_ancienne": None})
        entree["montant"] += _fcfa(reste)
        if plus_ancienne and (entree["plus_ancienne"] is None
                              or plus_ancienne < entree["plus_ancienne"]):
            entree["plus_ancienne"] = plus_ancienne

    return encours


def _chercher_client(db: Session, user, args: dict) -> dict:
    terme = _texte(args.get("terme"))
    limite = _entier(args.get("limite"), LIMITE_DEFAUT, 1, LIMITE_MAX)

    requete = db.query(Client)
    if terme:
        motif = f"%{terme}%"
        requete = requete.filter(or_(Client.name.ilike(motif), Client.contact.ilike(motif)))
    clients = requete.order_by(Client.name).limit(limite).all()
    encours = _encours_par_client(db, [c.client_id for c in clients])

    return {
        "nombre": len(clients),
        "clients": [{
            "id": c.client_id,
            "nom": _etiquette_client(c),
            "ville": c.city,
            "encours": encours.get(c.client_id, {}).get("montant", 0),
        } for c in clients],
    }


def _liste_creances(db: Session, user, args: dict) -> dict:
    minimum = _entier(args.get("montant_minimum"), 0, 0, 100_000_000)
    anciennete = _entier(args.get("anciennete_minimum_jours"), 0, 0, JOURS_MAX)
    limite = _entier(args.get("limite"), 15, 1, LIMITE_MAX)

    encours = _encours_par_client(db)
    if not encours:
        return {"nombre_debiteurs": 0, "total": 0, "clients": []}

    clients = {c.client_id: c for c in
               db.query(Client).filter(Client.client_id.in_(list(encours))).all()}

    maintenant = datetime.now()
    lignes = []
    for cid, info in encours.items():
        if info["montant"] < minimum:
            continue
        jours = None
        if info["plus_ancienne"]:
            jours = max(0, (maintenant - info["plus_ancienne"]).days)
        if anciennete and (jours is None or jours < anciennete):
            continue
        lignes.append({
            "client_id": cid,
            "nom": _etiquette_client(clients.get(cid)),
            "montant_du": info["montant"],
            "factures_impayees": info["factures"],
            "anciennete_jours": jours,
            # Le commerçant a coupé les rappels pour ce client : l'assistant
            # doit le savoir avant de proposer une relance.
            "relance_desactivee": bool(getattr(clients.get(cid), "disable_debt_reminder", False)),
        })

    lignes.sort(key=lambda x: x["montant_du"], reverse=True)
    return {
        "nombre_debiteurs": len(lignes),
        "total": sum(l["montant_du"] for l in lignes),
        "clients": lignes[:limite],
    }


def _chiffre_affaires(db: Session, user, args: dict) -> dict:
    jours = _entier(args.get("jours"), 30, 1, JOURS_MAX)
    depuis = _depuis(jours)

    facture, encaisse, nombre = (
        db.query(
            func.coalesce(func.sum(Invoice.total), 0),
            func.coalesce(func.sum(Invoice.paid_amount), 0),
            func.count(Invoice.invoice_id),
        )
        .filter(Invoice.date >= depuis, Invoice.status != STATUT_ANNULEE)
        .one()
    )

    facture, encaisse, nombre = _fcfa(facture), _fcfa(encaisse), int(nombre or 0)
    return {
        "periode_jours": jours,
        "facture": facture,
        "encaisse": encaisse,
        "reste_a_encaisser": max(0, facture - encaisse),
        "nombre_factures": nombre,
        "panier_moyen": (facture // nombre) if nombre else 0,
    }


def _liste_factures(db: Session, user, args: dict) -> dict:
    jours = _entier(args.get("jours"), 30, 1, JOURS_MAX)
    limite = _entier(args.get("limite"), 15, 1, LIMITE_MAX)
    statut = _texte(args.get("statut"), 30)
    impayees = bool(args.get("seulement_impayees"))

    requete = (db.query(Invoice)
               .filter(Invoice.date >= _depuis(jours), Invoice.status != STATUT_ANNULEE))
    if statut:
        requete = requete.filter(Invoice.status.ilike(f"%{statut}%"))
    if impayees:
        requete = requete.filter(Invoice.remaining_amount > 0)

    factures = requete.order_by(Invoice.date.desc()).limit(limite).all()
    return {
        "nombre": len(factures),
        "factures": [{
            "numero": f.invoice_number,
            "date": f.date.strftime("%Y-%m-%d") if f.date else None,
            "client": _etiquette_client(f.client),
            "total": _fcfa(f.total),
            "paye": _fcfa(f.paid_amount),
            "reste": _fcfa(f.remaining_amount),
            "statut": f.status,
        } for f in factures],
    }


STATUTS_ATELIER_OUVERTS = ("received", "in_progress", "completed", "ready")

LIBELLES_ATELIER = {
    "received": "reçu", "in_progress": "en cours", "completed": "terminé",
    "ready": "prêt", "picked_up": "récupéré", "abandoned": "abandonné",
}


def _nom_tronque(nom: Optional[str]) -> str:
    """Même règle que `_etiquette_client`, pour les noms stockés à plat (une
    fiche d'atelier recopie le nom du client au lieu de le référencer)."""
    morceaux = (nom or "").strip().split()
    if not morceaux:
        return "Client"
    if len(morceaux) == 1:
        return morceaux[0][:20]
    return f"{morceaux[0][:20]} {morceaux[-1][0].upper()}."


def _etat_atelier(db: Session, user, args: dict) -> dict:
    limite = _entier(args.get("limite"), 15, 1, LIMITE_MAX)
    statut = _texte(args.get("statut"), 30)

    requete = db.query(Maintenance)
    if statut in LIBELLES_ATELIER:
        requete = requete.filter(Maintenance.status == statut)
    else:
        requete = requete.filter(Maintenance.status.in_(STATUTS_ATELIER_OUVERTS))
    fiches = requete.order_by(Maintenance.reception_date.desc()).limit(limite).all()

    comptes = dict(db.query(Maintenance.status, func.count(Maintenance.maintenance_id))
                   .group_by(Maintenance.status).all())

    return {
        "par_statut": {LIBELLES_ATELIER.get(s, s): int(n) for s, n in comptes.items()},
        "nombre": len(fiches),
        "fiches": [{
            "numero": f.maintenance_number,
            "client": _nom_tronque(f.client_name),
            "appareil": " ".join(x for x in (f.device_brand, f.device_model or f.device_type) if x),
            "statut": LIBELLES_ATELIER.get(f.status, f.status),
            "cout": _fcfa(f.final_cost if f.final_cost is not None else f.estimated_cost),
            "recu_le": f.reception_date.strftime("%Y-%m-%d") if f.reception_date else None,
            "a_recuperer_avant": f.pickup_deadline.strftime("%Y-%m-%d") if f.pickup_deadline else None,
        } for f in fiches],
    }


# --- Écritures : `prepare` valide et résume, `apply` exécute ---------------

def _prep_creer_client(db: Session, user, args: dict) -> dict:
    nom = _texte(args.get("nom"), 100)
    if len(nom) < 2:
        return {"erreur": "Il faut le nom du client (au moins 2 caractères)."}
    telephone = _texte(args.get("telephone"), 20)
    ville = _texte(args.get("ville"), 50)

    existant = db.query(Client).filter(Client.name.ilike(nom)).first()
    if existant:
        return {"erreur": f"Un client porte déjà ce nom : {_etiquette_client(existant)}. "
                          "Précisez un nom différent si c'est bien un nouveau client."}

    details = {"nom": nom, "telephone": telephone or None, "ville": ville or None}
    return {
        "resume": f"Créer le client « {nom} »"
                  + (f", téléphone {telephone}" if telephone else "")
                  + (f", à {ville}" if ville else "") + ".",
        "details": details,
    }


def _app_creer_client(db: Session, user, args: dict) -> dict:
    prepare = _prep_creer_client(db, user, args)
    if "erreur" in prepare:
        return prepare
    details = prepare["details"]
    client = Client(name=details["nom"], phone=details["telephone"], city=details["ville"])
    db.add(client)
    db.commit()
    db.refresh(client)
    return {"cree": True, "client_id": client.client_id,
            "nom": _etiquette_client(client)}


def _debiteurs_pour_relance(db: Session, client_ids: list[int]) -> tuple[list, list]:
    """Sépare les clients relançables de ceux qu'il faut écarter, avec la raison.
    Le téléphone est lu ici mais ne ressort jamais vers le modèle."""
    encours = _encours_par_client(db, client_ids)
    clients = {c.client_id: c for c in
               db.query(Client).filter(Client.client_id.in_(client_ids)).all()}

    retenus, ecartes = [], []
    for cid in client_ids:
        client = clients.get(cid)
        if client is None:
            ecartes.append({"client_id": cid, "raison": "client introuvable"})
            continue
        montant = encours.get(cid, {}).get("montant", 0)
        if montant <= 0:
            ecartes.append({"client_id": cid, "nom": _etiquette_client(client),
                            "raison": "ne doit rien"})
            continue
        if getattr(client, "disable_debt_reminder", False):
            ecartes.append({"client_id": cid, "nom": _etiquette_client(client),
                            "raison": "rappels désactivés pour ce client"})
            continue
        if not (client.phone or "").strip():
            ecartes.append({"client_id": cid, "nom": _etiquette_client(client),
                            "raison": "aucun numéro enregistré"})
            continue
        retenus.append({"client_id": cid, "nom": _etiquette_client(client),
                        "montant_du": montant})
    return retenus, ecartes


def _prep_relancer_creances(db: Session, user, args: dict) -> dict:
    brut = args.get("client_ids")
    if not isinstance(brut, list) or not brut:
        return {"erreur": "Indiquez les identifiants des clients à relancer "
                          "(obtenus par liste_creances)."}
    client_ids, vus = [], set()
    for valeur in brut[:30]:
        try:
            cid = int(valeur)
        except (TypeError, ValueError):
            continue
        if cid not in vus:
            vus.add(cid)
            client_ids.append(cid)
    if not client_ids:
        return {"erreur": "Aucun identifiant de client exploitable."}

    retenus, ecartes = _debiteurs_pour_relance(db, client_ids)
    if not retenus:
        return {"erreur": "Aucun de ces clients ne peut être relancé. "
                          + "; ".join(f"{e.get('nom', e['client_id'])} : {e['raison']}"
                                      for e in ecartes)}

    total = sum(r["montant_du"] for r in retenus)
    lignes = ", ".join(f"{r['nom']} — {r['montant_du']:,}".replace(",", " ") + " F"
                       for r in retenus)
    resume = (f"Envoyer un rappel WhatsApp à {len(retenus)} client(s) pour "
              f"{total:,}".replace(",", " ") + f" F CFA au total : {lignes}.")
    if ecartes:
        resume += (" Écartés : "
                   + "; ".join(f"{e.get('nom', e['client_id'])} ({e['raison']})"
                               for e in ecartes) + ".")
    return {"resume": resume,
            "details": {"client_ids": [r["client_id"] for r in retenus]},
            "ecartes": ecartes}


def _app_relancer_creances(db: Session, user, args: dict) -> dict:
    prepare = _prep_relancer_creances(db, user, args)
    if "erreur" in prepare:
        return prepare

    # On délègue au service qui envoie déjà les relances automatiques : même
    # message, même canal, même marquage anti-doublon.
    from .debt_notifier import debt_notifier

    envoyes, echecs = [], []
    for cid in prepare["details"]["client_ids"]:
        client = db.query(Client).filter(Client.client_id == cid).first()
        if client is None:
            continue
        factures = [{
            "invoice_number": f.invoice_number,
            "due_date": f.due_date or f.date,
            "remaining": float(f.remaining_amount or 0),
        } for f in db.query(Invoice).filter(
            Invoice.client_id == cid, Invoice.remaining_amount > 0,
            Invoice.status != STATUT_ANNULEE).all()]
        manuelles = [{
            "reference": d.reference,
            "due_date": d.due_date or d.date,
            "remaining": float(d.remaining_amount or 0),
        } for d in db.query(ClientDebt).filter(
            ClientDebt.client_id == cid, ClientDebt.remaining_amount > 0).all()]

        try:
            debt_notifier._send_notification(
                db, cid, {"client": client, "invoices": factures, "manual": manuelles})
            debt_notifier._mark_sent(db, cid)
            envoyes.append(_etiquette_client(client))
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.exception("[assistant] relance impossible pour le client %s", cid)
            echecs.append({"client": _etiquette_client(client),
                           "erreur": type(exc).__name__})

    return {"envoyes": envoyes, "nombre_envoyes": len(envoyes), "echecs": echecs}


# --- Documents : facture et devis ------------------------------------------
#
# Aucune logique de facturation n'est réécrite ici. La numérotation, la
# réservation des variantes IMEI, les mouvements de stock et les contrôles
# vivent dans `routers/invoices.py` et `routers/quotations.py` : on leur
# délègue. Ce module se contente de résoudre ce que l'utilisateur a dit
# (« deux iPhone à 320 000 ») en lignes valides, et de calculer les totaux
# **selon la convention de l'application** — les prix de ligne sont HT et la
# TVA s'ajoute par-dessus (cf. `updateInvoiceTotals` dans static/js/invoices.js).

TVA_DEFAUT = 18.0
MAX_LIGNES = 20


def _f(montant: int) -> str:
    return f"{montant:,}".replace(",", " ")


def _resoudre_client(db: Session, args: dict) -> tuple[Optional[Client], Optional[dict]]:
    identifiant = args.get("client_id")
    if identifiant is not None:
        try:
            client = db.query(Client).filter(Client.client_id == int(identifiant)).first()
        except (TypeError, ValueError):
            client = None
        if client is None:
            return None, {"erreur": f"Aucun client n'a l'identifiant {identifiant}."}
        return client, None

    nom = _texte(args.get("client_nom"), 100)
    if not nom:
        return None, {"erreur": "Précisez le client (identifiant ou nom). "
                                "Utilisez chercher_client si besoin."}
    trouves = db.query(Client).filter(Client.name.ilike(f"%{nom}%")).limit(5).all()
    if not trouves:
        return None, {"erreur": f"Aucun client ne correspond à « {nom} ». "
                                "Proposez de le créer avec creer_client."}
    if len(trouves) > 1:
        # Ambiguïté : on refuse plutôt que de choisir au hasard — une facture
        # au mauvais client est pénible à défaire.
        return None, {"erreur": "Plusieurs clients correspondent : "
                                + ", ".join(_etiquette_client(c) for c in trouves)
                                + ". Précisez l'identifiant."}
    return trouves[0], None


def _resoudre_lignes(db: Session, args: dict, pour_facture: bool) -> tuple[list, Optional[dict]]:
    """`pour_facture` : une facture doit désigner les variantes une par une.
    `create_invoice` refuse en effet une simple quantité sur un produit suivi
    par IMEI (« vous devez sélectionner des variantes »), et il le fait *après*
    avoir enregistré les lignes précédentes — d'où la facture incohérente
    obtenue au premier essai. Un devis, lui, ne touche pas au stock et se
    contente d'une quantité."""
    brut = args.get("lignes")
    if not isinstance(brut, list) or not brut:
        return [], {"erreur": "Indiquez au moins une ligne (produit et quantité)."}

    serialisees_cache: dict[int, int] = {}
    lignes = []
    for entree in brut[:MAX_LIGNES]:
        if not isinstance(entree, dict):
            continue
        quantite = _entier(entree.get("quantite"), 1, 1, 10_000)

        produit = None
        identifiant = entree.get("produit_id")
        if identifiant is not None:
            try:
                produit = db.query(Product).filter(
                    Product.product_id == int(identifiant)).first()
            except (TypeError, ValueError):
                produit = None
            if produit is None:
                return [], {"erreur": f"Aucun produit n'a l'identifiant {identifiant}."}
        else:
            nom = _texte(entree.get("nom"), 200)
            if not nom:
                return [], {"erreur": "Chaque ligne doit désigner un produit "
                                      "(identifiant ou nom)."}
            trouves = (db.query(Product)
                       .filter(Product.is_archived == False,  # noqa: E712
                               Product.name.ilike(f"%{nom}%"))
                       .limit(5).all())
            if not trouves:
                return [], {"erreur": f"Aucun produit ne correspond à « {nom} »."}
            if len(trouves) > 1:
                return [], {"erreur": f"Plusieurs produits correspondent à « {nom} » : "
                                      + ", ".join(f"{p.name} (#{p.product_id})"
                                                  for p in trouves)
                                      + ". Précisez l'identifiant."}
            produit = trouves[0]

        # Prix : celui demandé, sinon le prix de vente du produit.
        prix_demande = entree.get("prix")
        if prix_demande is None:
            prix = _fcfa(produit.price)
        else:
            prix = _entier(prix_demande, _fcfa(produit.price), 0, 1_000_000_000)

        if produit.has_unique_serial and pour_facture:
            libres = (db.query(ProductVariant)
                      .filter(ProductVariant.product_id == produit.product_id,
                              ProductVariant.is_sold == False)  # noqa: E712
                      .order_by(ProductVariant.variant_id)
                      .limit(quantite).all())
            if len(libres) < quantite:
                return [], {"erreur": f"Stock insuffisant pour « {produit.name} » : "
                                      f"{len(libres)} disponible(s), "
                                      f"{quantite} demandé(s)."}
            # Une variante = une unité : autant de lignes que d'appareils.
            for variante in libres:
                lignes.append({
                    "product_id": produit.product_id,
                    "product_name": produit.name,
                    "quantity": 1,
                    "price": prix,
                    "total": prix,
                    "variant_id": variante.variant_id,
                    "variant_imei": variante.imei_serial,
                })
            continue

        if produit.has_unique_serial and produit.product_id not in serialisees_cache:
            serialisees_cache.update(_quantites_serialisees(db, [produit.product_id]))
        disponible = _stock_reel(produit, serialisees_cache)
        if disponible < quantite:
            return [], {"erreur": f"Stock insuffisant pour « {produit.name} » : "
                                  f"{disponible} disponible(s), {quantite} demandé(s)."}

        lignes.append({
            "product_id": produit.product_id,
            "product_name": produit.name,
            "quantity": quantite,
            "price": prix,
            "total": prix * quantite,
        })

    if not lignes:
        return [], {"erreur": "Aucune ligne exploitable."}
    return lignes, None


def _totaux(lignes: list, args: dict) -> dict:
    """Convention de l'application : les prix de ligne sont HT, la TVA s'ajoute."""
    taux_brut = args.get("tva_pourcentage")
    try:
        taux = TVA_DEFAUT if taux_brut is None else float(taux_brut)
    except (TypeError, ValueError):
        taux = TVA_DEFAUT
    taux = max(0.0, min(100.0, taux))

    sous_total = sum(l["total"] for l in lignes)
    tva = int(round(sous_total * taux / 100.0))
    return {"taux": taux, "sous_total": sous_total, "tva": tva,
            "total": sous_total + tva}


def _resume_document(genre: str, client: Client, lignes: list, montants: dict) -> str:
    # Les lignes d'un produit sérialisé sont éclatées une par appareil : on les
    # regroupe pour l'affichage, sinon le résumé répéterait trois fois la même
    # ligne devant l'utilisateur.
    groupes: dict = {}
    for ligne in lignes:
        cle = (ligne["product_name"], ligne["price"])
        groupes[cle] = groupes.get(cle, 0) + ligne["quantity"]
    detail = " ; ".join(f"{quantite} × {nom} à {_f(prix)} F"
                        for (nom, prix), quantite in groupes.items())

    resume = (f"Créer {genre} pour {_etiquette_client(client)} — {detail}. "
              f"Sous-total {_f(montants['sous_total'])} F")
    if montants["tva"]:
        resume += f" + TVA {montants['taux']:g} % ({_f(montants['tva'])} F)"
    resume += f" = {_f(montants['total'])} F CFA."
    return resume


def _preparer_document(db: Session, args: dict, genre: str,
                       pour_facture: bool) -> dict:
    client, erreur = _resoudre_client(db, args)
    if erreur:
        return erreur
    lignes, erreur = _resoudre_lignes(db, args, pour_facture)
    if erreur:
        return erreur
    montants = _totaux(lignes, args)
    return {
        "resume": _resume_document(genre, client, lignes, montants),
        "details": {"client_id": client.client_id, "lignes": lignes,
                    "montants": montants},
    }


def _annuler_facture_partielle(db: Session, numero: str) -> bool:
    """`create_invoice` enregistre les lignes au fil de l'eau : un refus tardif
    peut laisser une facture aux totaux justes mais aux lignes incomplètes, et
    du stock déjà déduit. On la supprime en passant par `delete_invoice`, qui
    sait défaire les mouvements de stock — plutôt que d'effacer la ligne à la
    main et de laisser l'inventaire faux."""
    import logging
    try:
        db.rollback()
        facture = db.query(Invoice).filter(Invoice.invoice_number == numero).first()
        if facture is None:
            return True
        from ..routers.invoices import delete_invoice
        _executer_async(delete_invoice(facture.invoice_id, db=db, current_user=None))
        logging.warning("[assistant] facture partielle %s annulée après échec", numero)
        return True
    except Exception:
        logging.exception("[assistant] impossible d'annuler la facture partielle %s",
                          numero)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _executer_async(coroutine):
    """`create_invoice` et `create_quotation` sont asynchrones ; la route de
    confirmation est synchrone, donc exécutée par FastAPI dans un fil du pool —
    sans boucle d'événements. `asyncio.run` peut donc en ouvrir une sans
    conflit, et cela évite de rendre asynchrone toute la chaîne d'outils."""
    import asyncio
    return asyncio.run(coroutine)


def _message_http(exc) -> str:
    detail = getattr(exc, "detail", None)
    return str(detail) if detail else type(exc).__name__


def _prep_creer_facture(db: Session, user, args: dict) -> dict:
    return _preparer_document(db, args, "la facture", pour_facture=True)


def _app_creer_facture(db: Session, user, args: dict) -> dict:
    prepare = _prep_creer_facture(db, user, args)
    if "erreur" in prepare:
        return prepare      # le stock a pu changer depuis la proposition
    details = prepare["details"]
    montants = details["montants"]

    from fastapi import HTTPException
    from ..routers.invoices import _next_invoice_number, create_invoice
    from ..schemas import InvoiceCreate, InvoiceItemCreate

    numero = _next_invoice_number(db)
    donnees = InvoiceCreate(
        invoice_number=numero,
        invoice_type="normal",
        client_id=details["client_id"],
        date=datetime.now(),
        subtotal=montants["sous_total"],
        tax_rate=montants["taux"],
        tax_amount=montants["tva"],
        total=montants["total"],
        internal_notes="Créée par l'assistant.",
        items=[InvoiceItemCreate(**ligne) for ligne in details["lignes"]],
    )
    try:
        resultat = _executer_async(create_invoice(donnees, db=db, current_user=user))
    except Exception as exc:  # noqa: BLE001
        message = (_message_http(exc) if isinstance(exc, HTTPException)
                   else f"échec inattendu ({type(exc).__name__})")
        if _annuler_facture_partielle(db, numero):
            return {"erreur": f"{message}. Rien n'a été enregistré."}
        return {"erreur": f"{message}. Attention : la facture {numero} a peut-être "
                          "été partiellement enregistrée — vérifiez-la dans la liste."}

    return {"cree": True,
            "numero": getattr(resultat, "invoice_number", None) or numero,
            "total": montants["total"]}


def _prep_creer_devis(db: Session, user, args: dict) -> dict:
    return _preparer_document(db, args, "le devis", pour_facture=False)


def _app_creer_devis(db: Session, user, args: dict) -> dict:
    prepare = _prep_creer_devis(db, user, args)
    if "erreur" in prepare:
        return prepare
    details = prepare["details"]
    montants = details["montants"]

    from fastapi import HTTPException
    from ..routers.quotations import _next_quotation_number, create_quotation
    from ..schemas import QuotationCreate, QuotationItemCreate

    numero = _next_quotation_number(db)
    donnees = QuotationCreate(
        quotation_number=numero,
        client_id=details["client_id"],
        date=datetime.now(),
        subtotal=montants["sous_total"],
        tax_rate=montants["taux"],
        tax_amount=montants["tva"],
        total=montants["total"],
        internal_notes="Créé par l'assistant.",
        # Un devis n'a pas de variantes : on ne garde que les champs du schéma.
        items=[QuotationItemCreate(**{cle: ligne[cle] for cle in
                                      ("product_id", "product_name", "quantity",
                                       "price", "total")})
               for ligne in details["lignes"]],
    )
    try:
        resultat = _executer_async(create_quotation(donnees, db=db, current_user=user))
    except HTTPException as exc:
        return {"erreur": _message_http(exc)}

    return {"cree": True,
            "numero": getattr(resultat, "quotation_number", None) or numero,
            "total": montants["total"]}


# --- Paiement sur facture --------------------------------------------------

MOYENS_PAIEMENT = ("espèces", "Wave", "Orange Money", "virement", "chèque", "carte")


def _resoudre_facture(db: Session, args: dict) -> tuple[Optional[Invoice], Optional[dict]]:
    identifiant = args.get("facture_id")
    if identifiant is not None:
        try:
            facture = db.query(Invoice).filter(
                Invoice.invoice_id == int(identifiant)).first()
        except (TypeError, ValueError):
            facture = None
        if facture is None:
            return None, {"erreur": f"Aucune facture n'a l'identifiant {identifiant}."}
        return facture, None

    numero = _texte(args.get("facture_numero"), 50)
    if not numero:
        return None, {"erreur": "Précisez la facture (numéro ou identifiant). "
                                "Utilisez liste_factures si besoin."}
    trouvees = (db.query(Invoice)
                .filter(Invoice.invoice_number.ilike(f"%{numero}%"))
                .limit(5).all())
    if not trouvees:
        return None, {"erreur": f"Aucune facture ne correspond à « {numero} »."}
    if len(trouvees) > 1:
        return None, {"erreur": "Plusieurs factures correspondent : "
                                + ", ".join(f.invoice_number for f in trouvees)
                                + ". Précisez le numéro complet."}
    return trouvees[0], None


def _prep_enregistrer_paiement(db: Session, user, args: dict) -> dict:
    facture, erreur = _resoudre_facture(db, args)
    if erreur:
        return erreur
    if (facture.status or "").strip() == STATUT_ANNULEE:
        return {"erreur": f"La facture {facture.invoice_number} est annulée."}

    reste = _fcfa(facture.remaining_amount)
    if reste <= 0:
        return {"erreur": f"La facture {facture.invoice_number} est déjà soldée."}

    montant = _entier(args.get("montant"), 0, 0, 1_000_000_000)
    if montant <= 0:
        return {"erreur": "Indiquez le montant encaissé, en francs CFA."}
    # Même contrôle que `add_payment`, mais avant la confirmation : l'humain voit
    # le refus tout de suite au lieu de cliquer pour rien.
    if montant > reste:
        return {"erreur": f"Le montant dépasse le solde : {_f(reste)} F restent dus "
                          f"sur la facture {facture.invoice_number}."}

    moyen = _texte(args.get("moyen"), 50) or "espèces"
    apres = reste - montant
    resume = (f"Enregistrer un paiement de {_f(montant)} F CFA en {moyen} sur la "
              f"facture {facture.invoice_number} "
              f"({_etiquette_client(facture.client)}). "
              + ("La facture sera soldée." if apres == 0
                 else f"Il restera {_f(apres)} F à encaisser."))
    return {"resume": resume,
            "details": {"invoice_id": facture.invoice_id, "montant": montant,
                        "moyen": moyen, "reste_apres": apres}}


def _app_enregistrer_paiement(db: Session, user, args: dict) -> dict:
    prepare = _prep_enregistrer_paiement(db, user, args)
    if "erreur" in prepare:
        return prepare      # un autre paiement a pu tomber entre-temps
    details = prepare["details"]

    from fastapi import HTTPException
    from ..routers.invoices import PaymentCreate, add_payment

    charge = PaymentCreate(
        amount=float(details["montant"]),
        payment_method=details["moyen"],
        reference=_texte(args.get("reference"), 100) or None,
        notes="Enregistré par l'assistant.",
    )
    try:
        _executer_async(add_payment(details["invoice_id"], charge,
                                    db=db, current_user=user))
    except Exception as exc:  # noqa: BLE001
        return {"erreur": (_message_http(exc) if isinstance(exc, HTTPException)
                           else f"échec inattendu ({type(exc).__name__})")}

    facture = db.query(Invoice).filter(
        Invoice.invoice_id == details["invoice_id"]).first()
    return {"enregistre": True,
            "numero": facture.invoice_number if facture else None,
            "montant": details["montant"],
            "reste": _fcfa(facture.remaining_amount) if facture else None,
            "statut": facture.status if facture else None}


# --- Ajustement de stock ---------------------------------------------------

def _prep_ajuster_stock(db: Session, user, args: dict) -> dict:
    produit = None
    identifiant = args.get("produit_id")
    if identifiant is not None:
        try:
            produit = db.query(Product).filter(
                Product.product_id == int(identifiant)).first()
        except (TypeError, ValueError):
            produit = None
        if produit is None:
            return {"erreur": f"Aucun produit n'a l'identifiant {identifiant}."}
    else:
        nom = _texte(args.get("nom"), 200)
        if not nom:
            return {"erreur": "Précisez le produit (identifiant ou nom)."}
        trouves = (db.query(Product)
                   .filter(Product.is_archived == False,  # noqa: E712
                           Product.name.ilike(f"%{nom}%"))
                   .limit(5).all())
        if not trouves:
            return {"erreur": f"Aucun produit ne correspond à « {nom} »."}
        if len(trouves) > 1:
            return {"erreur": f"Plusieurs produits correspondent à « {nom} » : "
                              + ", ".join(f"{p.name} (#{p.product_id})" for p in trouves)
                              + ". Précisez l'identifiant."}
        produit = trouves[0]

    # Un produit suivi par IMEI ne se compte pas : son stock est le nombre de
    # variantes non vendues, et `Product.quantity` ne le reflète pas. Ajuster
    # ce compteur donnerait un inventaire faux.
    if produit.has_unique_serial:
        return {"erreur": f"« {produit.name} » est suivi par numéro de série : son "
                          "stock se règle en ajoutant ou retirant des IMEI depuis la "
                          "fiche produit, pas par une quantité."}

    motif = _texte(args.get("motif"), 200)
    if len(motif) < 3:
        return {"erreur": "Indiquez le motif de l'ajustement (réception, casse, "
                          "perte, inventaire…) : il reste dans l'historique du stock."}

    actuel = int(produit.quantity or 0)
    variation_brute = args.get("variation")
    cible_brute = args.get("nouvelle_quantite")

    if (variation_brute is None) == (cible_brute is None):
        return {"erreur": "Indiquez soit « variation » (par exemple 10 pour une "
                          "réception, -2 pour une casse), soit « nouvelle_quantite » "
                          "pour corriger le compteur — mais pas les deux."}

    if variation_brute is not None:
        variation = _entier(variation_brute, 0, -1_000_000, 1_000_000)
    else:
        variation = _entier(cible_brute, actuel, 0, 1_000_000) - actuel

    if variation == 0:
        return {"erreur": f"Le stock de « {produit.name} » est déjà de {actuel}."}
    if actuel + variation < 0:
        return {"erreur": f"Impossible : il n'y a que {actuel} × « {produit.name} » "
                          f"en stock."}

    sens = "Entrée" if variation > 0 else "Sortie"
    resume = (f"{sens} de {abs(variation)} × {produit.name} (motif : {motif}). "
              f"Stock : {actuel} → {actuel + variation}.")
    return {"resume": resume,
            "details": {"product_id": produit.product_id, "variation": variation,
                        "motif": motif, "avant": actuel}}


def _app_ajuster_stock(db: Session, user, args: dict) -> dict:
    prepare = _prep_ajuster_stock(db, user, args)
    if "erreur" in prepare:
        return prepare
    details = prepare["details"]

    # L'utilitaire synchrone, à ne pas confondre avec la route `POST /` du même
    # module (`create_stock_movement`), qui met à jour `Product.quantity` elle-même.
    # Ici c'est bien l'utilitaire qu'il faut : il enregistre le mouvement sans
    # toucher au compteur, la mise à jour revenant à l'appelant juste en dessous.
    from ..routers.stock_movements import create_stock_movement_entry

    produit = db.query(Product).filter(
        Product.product_id == details["product_id"]).first()
    if produit is None:
        return {"erreur": "Le produit a disparu entre-temps."}

    variation = details["variation"]
    avant = int(produit.quantity or 0)
    # Le stock a pu bouger entre la proposition et le clic.
    if avant + variation < 0:
        return {"erreur": f"Le stock a changé : il ne reste que {avant} × "
                          f"« {produit.name} »."}

    try:
        create_stock_movement_entry(
            db,
            product_id=produit.product_id,
            quantity=abs(variation),
            movement_type="IN" if variation > 0 else "OUT",
            reference_type="ASSISTANT",
            notes=details["motif"],
        )
        produit.quantity = avant + variation
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        import logging
        logging.exception("[assistant] ajustement de stock impossible")
        return {"erreur": f"échec inattendu ({type(exc).__name__})"}

    db.refresh(produit)
    return {"ajuste": True,
            "produit": produit.name,
            "avant": avant,
            "apres": int(produit.quantity or 0)}


# ---------------------------------------------------------------------------
# Profil de boutique — comment l'assistant adapte l'application au métier
# ---------------------------------------------------------------------------

def _profil_boutique(db: Session, user, args: dict) -> dict:
    """Le métier de la boutique tel qu'il est configuré, et les profils
    possibles. L'assistant s'en sert pour deux choses : employer les bons mots
    (« IMEI » ou « taille ») et savoir si l'installation reste à faire."""
    from .. import shop_profile as sp

    courant = sp.charger(db)
    return {
        "profil": courant["code"],
        "libelle": courant["libelle"],
        "configure": bool(courant["applique"]),
        "suivi_du_stock": courant["tracage_explication"],
        "mot_identifiant": courant["libelles"]["identifiant"],
        "mot_variante": courant["libelles"]["variante"],
        "modules_actifs": sorted(
            nom for nom in courant["modules"]
            if sp.module_actif(nom, db, connu=courant)),
        "profils_possibles": [
            {"code": f["code"], "libelle": f["libelle"], "resume": f["resume"],
             "exemples": f["exemples"]}
            for f in sp.catalogue()
        ],
    }


def _alertes_peremption(db: Session, user, args: dict) -> dict:
    """Les lots périmés et ceux qui approchent. Réponse volontairement bornée :
    le commerçant veut savoir quoi aller vérifier en rayon, pas relire son
    inventaire."""
    from ..routers.lots import alertes as _alertes

    jours = _entier(args.get("jours"), 30, 0, 365)
    brut = _alertes(jours=jours, user=user, db=db)

    def resume(fiches):
        return [{"produit": f["produit"],
                 "lot": f["lot_number"] or "(sans numéro)",
                 "date_limite": f["expiry_date"],
                 "jours_restants": f["jours_restants"],
                 "quantite_recue": f["quantity"],
                 "unite": f["unite"]}
                for f in fiches[:25]]

    return {
        "jours": jours,
        "perimes": resume(brut["perimes"]),
        "bientot": resume(brut["bientot"]),
        "nombre_perimes": len(brut["perimes"]),
        "nombre_bientot": len(brut["bientot"]),
        "tronque": (len(brut["perimes"]) > 25 or len(brut["bientot"]) > 25),
        "remarque": ("Les quantités sont celles reçues dans chaque lot, pas ce "
                     "qui reste en rayon : l'application ne sait pas quel lot a "
                     "été vendu."),
    }


def _prep_configurer_boutique(db: Session, user, args: dict) -> dict:
    """Annonce, sans rien écrire, ce que l'application deviendrait sous ce
    profil : catégories à créer, grilles de déclinaison, modules allumés."""
    from .. import shop_profile as sp
    from ..database import Category

    code = _texte(args.get("profil"), 40).lower()
    if not sp.existe(code):
        possibles = ", ".join(f["code"] for f in sp.catalogue())
        return {"erreur": f"Profil inconnu. Choisissez parmi : {possibles}."}

    cible = sp.profil(code)
    actuel = sp.charger(db)

    existantes = {c.name for c in db.query(Category).all()}
    a_creer = [c["nom"] for c in cible["categories"]
               if c["nom"] not in existantes]
    grilles = [f"{c['nom']} ({', '.join(a['nom'] for a in c['attributs'])})"
               for c in cible["categories"] if c.get("attributs")]
    modules = sorted(nom for nom, actif in cible["modules"].items() if actif)

    morceaux = [f"Configurer la boutique en « {cible['libelle']} »."]
    if actuel["applique"] and actuel["code"] != code:
        morceaux.append(
            f"Le profil actuel est « {actuel['libelle']} » : il sera remplacé, "
            "mais aucun produit ni aucune catégorie existante ne sera supprimé.")
    morceaux.append(f"Suivi du stock : {cible['tracage_explication']}")
    if a_creer:
        morceaux.append(f"Catégories à créer : {', '.join(a_creer)}.")
    else:
        morceaux.append("Toutes les catégories de ce profil existent déjà.")
    if grilles:
        morceaux.append("Grilles de déclinaison : " + " ; ".join(grilles) + ".")
    if modules:
        morceaux.append(f"Modules concernés : {', '.join(modules)}.")

    return {"resume": " ".join(morceaux), "details": {"profil": code}}


def _app_configurer_boutique(db: Session, user, args: dict) -> dict:
    from .. import shop_profile as sp

    prepare = _prep_configurer_boutique(db, user, args)
    if "erreur" in prepare:
        return prepare

    rapport = sp.appliquer(db, prepare["details"]["profil"],
                           applique_par=getattr(user, "username", None))
    return {
        "configure": True,
        "profil": rapport["profil"],
        "libelle": rapport["libelle"],
        "resume": sp.resume_rapport(rapport),
        "categories_creees": rapport["categories_creees"],
        "attributs_crees": rapport["attributs_crees"],
        "a_verifier": rapport["variantes_non_modifiees"],
        "categories_hors_profil": rapport["categories_hors_profil"],
    }


# ---------------------------------------------------------------------------
# Déclarations.
#   `feature`   = fonctionnalité de plan requise (non négociable).
#   `role_min`  = rôle minimum PAR DÉFAUT ; l'administrateur le change depuis
#                 l'écran Paramètres. Absent = « user », donc tout le monde.
#   `ecriture`  = passe obligatoirement par la confirmation humaine.
# ---------------------------------------------------------------------------

OUTILS: list[dict] = [
    {
        "name": "chercher_produit",
        "description": (
            "Cherche des produits dans le stock par nom, marque, modèle, "
            "catégorie ou code-barres, et renvoie la quantité réellement "
            "disponible et le prix de vente. À utiliser dès que la question "
            "porte sur un article précis : « combien il me reste de… », "
            "« à quel prix je vends… », « est-ce que j'ai encore… »."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "terme": {"type": "string", "description": "Nom, marque, modèle, catégorie ou code-barres. Vide = tout le catalogue."},
                "limite": {"type": "integer", "description": f"Nombre de produits à renvoyer, 1 à {LIMITE_MAX}. Défaut {LIMITE_DEFAUT}."},
            },
        },
        "fn": _chercher_produit,
    },
    {
        "name": "etat_stock",
        "description": (
            "Liste les produits dont le stock est bas ou nul, du plus critique "
            "au moins critique. À utiliser pour « qu'est-ce qui va me manquer », "
            "« qu'est-ce qui est en rupture », « qu'est-ce que je dois "
            "recommander »."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "seuil": {"type": "integer", "description": "Stock au-dessous ou égal duquel alerter. Défaut 5."},
                "categorie": {"type": "string", "description": "Restreindre à une catégorie."},
                "limite": {"type": "integer", "description": f"1 à {LIMITE_MAX}. Défaut 20."},
            },
        },
        "fn": _etat_stock,
    },
    {
        "name": "chercher_client",
        "description": (
            "Cherche un client par nom et renvoie son encours (ce qu'il reste "
            "à payer). À utiliser pour « est-ce que M. X me doit quelque "
            "chose », « retrouve le client Y »."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "terme": {"type": "string", "description": "Nom ou contact du client."},
                "limite": {"type": "integer", "description": f"1 à {LIMITE_MAX}. Défaut {LIMITE_DEFAUT}."},
            },
        },
        "fn": _chercher_client,
    },
    {
        "name": "liste_creances",
        "description": (
            "Liste les clients qui doivent de l'argent, du montant le plus "
            "élevé au plus faible, avec l'ancienneté de la plus vieille dette. "
            "À utiliser pour « qui me doit de l'argent », « combien on me doit "
            "en tout », « qui traîne depuis longtemps ». Signale aussi les "
            "clients dont les rappels ont été désactivés."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "montant_minimum": {"type": "integer", "description": "Ne garder que les dettes supérieures ou égales à ce montant, en F CFA."},
                "anciennete_minimum_jours": {"type": "integer", "description": "Ne garder que les dettes plus anciennes que ce nombre de jours."},
                "limite": {"type": "integer", "description": f"1 à {LIMITE_MAX}. Défaut 15."},
            },
        },
        "fn": _liste_creances,
    },
    {
        "name": "chiffre_affaires",
        "description": (
            "Chiffre d'affaires facturé et encaissé sur une période, avec le "
            "nombre de factures et le panier moyen. À utiliser pour « combien "
            "j'ai vendu ce mois », « mon chiffre de la semaine », « combien "
            "il me reste à encaisser »."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jours": {"type": "integer", "description": "Profondeur de la période en jours. Défaut 30."},
            },
        },
        "role_min": "admin",
        "feature": "reports",
        "fn": _chiffre_affaires,
    },
    {
        "name": "liste_factures",
        "description": (
            "Liste les dernières factures avec leur client, leur total et ce "
            "qui reste à payer. À utiliser pour « mes dernières ventes », "
            "« quelles factures ne sont pas payées », « la facture de… »."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jours": {"type": "integer", "description": "Profondeur de la période en jours. Défaut 30."},
                "statut": {"type": "string", "description": "Filtrer sur un statut : « payée », « en attente », « partiellement payée », « en retard »."},
                "seulement_impayees": {"type": "boolean", "description": "Ne garder que les factures avec un reste à payer."},
                "limite": {"type": "integer", "description": f"1 à {LIMITE_MAX}. Défaut 15."},
            },
        },
        "fn": _liste_factures,
    },
    {
        "name": "etat_atelier",
        "description": (
            "État de l'atelier de réparation : nombre de fiches par statut et "
            "liste des dernières réceptions, avec l'appareil, le coût et la "
            "date limite de récupération. À utiliser pour « qu'est-ce qu'il y a "
            "à l'atelier », « quelles réparations sont prêtes », « qui n'est pas "
            "venu récupérer »."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "statut": {"type": "string", "description": "Filtrer sur un statut : received, in_progress, completed, ready, picked_up, abandoned. Par défaut, seules les fiches encore ouvertes."},
                "limite": {"type": "integer", "description": f"1 à {LIMITE_MAX}. Défaut 15."},
            },
        },
        "feature": "maintenance",
        "fn": _etat_atelier,
    },
    {
        "name": "creer_client",
        "description": (
            "Prépare la création d'une fiche client. À utiliser quand "
            "l'utilisateur veut enregistrer un nouveau client. L'action est "
            "seulement proposée : elle ne prend effet qu'après confirmation de "
            "l'utilisateur, que tu n'as pas à demander toi-même — l'application "
            "affiche le bouton."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "nom": {"type": "string", "description": "Nom complet du client."},
                "telephone": {"type": "string", "description": "Numéro de téléphone, si donné."},
                "ville": {"type": "string", "description": "Ville, si donnée."},
            },
            "required": ["nom"],
        },
        "ecriture": True,
        "prepare": _prep_creer_client,
        "apply": _app_creer_client,
    },
    {
        "name": "relancer_creances",
        "description": (
            "Prépare l'envoi d'un rappel WhatsApp aux clients qui doivent de "
            "l'argent. Appelle d'abord liste_creances pour obtenir les "
            "identifiants, puis passe-les ici. Écarte automatiquement les "
            "clients sans dette, sans numéro, ou dont les rappels ont été "
            "désactivés. L'envoi ne part qu'après confirmation de l'utilisateur, "
            "que tu n'as pas à demander toi-même — l'application affiche le "
            "bouton et la liste des destinataires."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Identifiants des clients à relancer, 30 au maximum.",
                },
            },
            "required": ["client_ids"],
        },
        "role_min": "admin",
        "feature": "auto_reminders",
        "ecriture": True,
        "prepare": _prep_relancer_creances,
        "apply": _app_relancer_creances,
    },
    {
        "name": "creer_facture",
        "description": (
            "Prépare une facture pour un client, à partir des articles et des "
            "quantités demandés. À utiliser pour « fais une facture à M. X pour "
            "deux iPhone », « facture ça ». Vérifie le stock et refuse si un "
            "produit ou un client est ambigu. L'action est seulement proposée : "
            "elle ne prend effet qu'après confirmation de l'utilisateur, que tu "
            "n'as pas à demander toi-même — l'application affiche le bouton avec "
            "le détail des montants."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "integer", "description": "Identifiant du client — à préférer au nom."},
                "client_nom": {"type": "string", "description": "Nom du client si l'identifiant est inconnu. Refusé si plusieurs clients correspondent : utilise alors chercher_client."},
                "lignes": {
                    "type": "array",
                    "description": "Articles à facturer, 20 au maximum.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "produit_id": {"type": "integer", "description": "Identifiant du produit — à préférer au nom."},
                            "nom": {"type": "string", "description": "Nom du produit si l'identifiant est inconnu."},
                            "quantite": {"type": "integer", "description": "Quantité. Défaut 1."},
                            "prix": {"type": "integer", "description": "Prix unitaire HT en F CFA. Par défaut, le prix de vente enregistré du produit."},
                        },
                    },
                },
                "tva_pourcentage": {"type": "number", "description": "Taux de TVA ajouté aux prix de ligne. 18 par défaut. Mettre 0 si l'utilisateur indique que les prix annoncés incluent déjà la taxe."},
            },
            "required": ["lignes"],
        },
        "ecriture": True,
        "prepare": _prep_creer_facture,
        "apply": _app_creer_facture,
    },
    {
        "name": "creer_devis",
        "description": (
            "Prépare un devis pour un client — mêmes règles que creer_facture, "
            "mais sans effet sur le stock. À utiliser pour « fais un devis à "
            "M. X », « prépare une proposition de prix ». L'action est seulement "
            "proposée et ne prend effet qu'après confirmation de l'utilisateur."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "client_id": {"type": "integer", "description": "Identifiant du client — à préférer au nom."},
                "client_nom": {"type": "string", "description": "Nom du client si l'identifiant est inconnu."},
                "lignes": {
                    "type": "array",
                    "description": "Articles à chiffrer, 20 au maximum.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "produit_id": {"type": "integer"},
                            "nom": {"type": "string"},
                            "quantite": {"type": "integer"},
                            "prix": {"type": "integer", "description": "Prix unitaire HT en F CFA."},
                        },
                    },
                },
                "tva_pourcentage": {"type": "number", "description": "Taux de TVA. 18 par défaut, 0 si les prix incluent la taxe."},
            },
            "required": ["lignes"],
        },
        "ecriture": True,
        "prepare": _prep_creer_devis,
        "apply": _app_creer_devis,
    },
    {
        "name": "enregistrer_paiement",
        "description": (
            "Prépare l'enregistrement d'un encaissement sur une facture. À "
            "utiliser pour « M. X a payé 50 000 », « encaisse 20 000 sur la "
            "facture FAC-0248 ». Refuse si la facture est soldée, annulée, ou "
            "si le montant dépasse le solde restant. L'action est seulement "
            "proposée : elle ne prend effet qu'après confirmation de "
            "l'utilisateur, que tu n'as pas à demander toi-même."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "facture_numero": {"type": "string", "description": "Numéro de la facture, par exemple FAC-0248. Utilise liste_factures si tu ne l'as pas."},
                "facture_id": {"type": "integer", "description": "Identifiant de la facture, si tu l'as."},
                "montant": {"type": "integer", "description": "Montant encaissé, en francs CFA."},
                "moyen": {"type": "string", "description": "Moyen de paiement : " + ", ".join(MOYENS_PAIEMENT) + ". « espèces » par défaut."},
                "reference": {"type": "string", "description": "Référence de la transaction, si donnée."},
            },
            "required": ["montant"],
        },
        "ecriture": True,
        "prepare": _prep_enregistrer_paiement,
        "apply": _app_enregistrer_paiement,
    },
    {
        "name": "ajuster_stock",
        "description": (
            "Prépare un ajustement de stock sur un produit : réception de "
            "marchandise, casse, perte, ou correction d'inventaire. À utiliser "
            "pour « j'ai reçu 10 coques », « on en a cassé 2 », « il ne m'en "
            "reste que 3 ». Donne SOIT variation (10, ou -2), SOIT "
            "nouvelle_quantite pour corriger le compteur. Refuse les produits "
            "suivis par numéro de série, dont le stock se règle par IMEI. "
            "L'action est seulement proposée et ne prend effet qu'après "
            "confirmation de l'utilisateur."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "produit_id": {"type": "integer", "description": "Identifiant du produit — à préférer au nom."},
                "nom": {"type": "string", "description": "Nom du produit si l'identifiant est inconnu."},
                "variation": {"type": "integer", "description": "Quantité à ajouter (positive) ou à retirer (négative)."},
                "nouvelle_quantite": {"type": "integer", "description": "Stock exact après correction, pour un inventaire. À la place de variation."},
                "motif": {"type": "string", "description": "Raison de l'ajustement — elle reste dans l'historique du stock."},
            },
            "required": ["motif"],
        },
        "ecriture": True,
        "prepare": _prep_ajuster_stock,
        "apply": _app_ajuster_stock,
    },
    {
        "name": "alertes_peremption",
        "description": (
            "Liste les lots déjà périmés et ceux dont la date limite approche. "
            "À utiliser pour « qu'est-ce qui périme cette semaine ? », « ai-je "
            "des produits périmés ? », « que dois-je solder ? ». Ne concerne "
            "que les boutiques qui suivent des dates limites (alimentation, "
            "cosmétique)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "jours": {
                    "type": "integer",
                    "description": "Fenêtre de surveillance en jours (30 par "
                                   "défaut). 7 pour « cette semaine », 0 pour "
                                   "seulement ce qui est déjà périmé.",
                },
            },
        },
        # Sans le module « péremption », la boutique ne saisit aucune date : la
        # question n'a pas de sens et l'outil resterait muet.
        "module": "peremption",
        "fn": _alertes_peremption,
    },
    {
        "name": "profil_boutique",
        "description": (
            "Donne le métier configuré pour cette boutique (téléphonie, mode, "
            "alimentation, cosmétique, boutique générale), la façon dont le "
            "stock y est suivi et les mots à employer à l'écran. À appeler "
            "avant de parler d'identifiants, de tailles ou de dates de "
            "péremption, pour ne pas proposer au commerçant une notion qui "
            "n'existe pas chez lui, et pour savoir si l'installation est faite."
        ),
        "parameters": {"type": "object", "properties": {}},
        "fn": _profil_boutique,
    },
    {
        "name": "configurer_boutique",
        "description": (
            "Prépare la configuration de l'application pour un métier : crée "
            "les catégories de départ et leurs grilles de déclinaison (tailles, "
            "couleurs, contenances), choisit la façon de suivre le stock et les "
            "modules utiles. À utiliser quand le commerçant décrit son "
            "activité — « je vends des pagnes », « j'ai une supérette » — et "
            "seulement après avoir vérifié le profil avec profil_boutique. "
            "N'efface aucun produit ni aucune catégorie existante. L'action est "
            "seulement proposée et ne prend effet qu'après confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profil": {
                    "type": "string",
                    "enum": ["telephonie", "mode", "alimentation",
                             "cosmetique", "general"],
                    "description": (
                        "telephonie : appareils suivis par IMEI, garantie, "
                        "atelier. mode : vêtements, chaussures, tissus, "
                        "déclinés en tailles et couleurs. alimentation : "
                        "supérette, épicerie, boissons, avec péremption. "
                        "cosmetique : parfums, soins, cheveux, par contenance "
                        "ou teinte. general : quincaillerie ou stock mélangé, "
                        "simple quantité."
                    ),
                },
            },
            "required": ["profil"],
        },
        # Choix structurant pour toute la boutique : réservé à l'administrateur
        # quel que soit le réglage, et confirmé comme toute écriture.
        "role_min": "admin",
        "ecriture": True,
        "prepare": _prep_configurer_boutique,
        "apply": _app_configurer_boutique,
    },
]

_PAR_NOM = {o["name"]: o for o in OUTILS}


# ---------------------------------------------------------------------------
# Droits d'accès aux outils
#
# Deux filtres indépendants, et l'ordre importe :
#
# 1. le **plan d'abonnement** (`feature`) — ce n'est pas négociable, c'est ce
#    que le commerçant a payé ;
# 2. le **rôle minimum**, que l'administrateur règle depuis l'écran Paramètres.
#    Le défaut de chaque outil est posé dans sa déclaration (`role_min`), mais
#    c'est bien la valeur enregistrée qui décide. « desactive » coupe l'outil
#    pour tout le monde, y compris l'administrateur.
# ---------------------------------------------------------------------------

CLE_DROITS = "ASSISTANT_TOOL_ROLES"

NIVEAUX = {"user": 1, "manager": 2, "admin": 3}
ROLES_POSSIBLES = ("desactive", "admin", "manager", "user")

LIBELLES_ROLES = {
    "desactive": "Désactivé",
    "admin": "Administrateurs seulement",
    "manager": "Managers et administrateurs",
    "user": "Tout le monde",
}


def _niveau(role: Optional[str]) -> int:
    return NIVEAUX.get((role or "").strip().lower(), 1)


def config_par_defaut() -> dict:
    return {o["name"]: o.get("role_min", "user") for o in OUTILS}


def charger_config(db: Optional[Session]) -> dict:
    """Droits enregistrés, complétés par les défauts. Tolérant : une base
    injoignable ou une valeur illisible ne doit pas priver l'utilisateur de son
    assistant, elle le ramène aux défauts."""
    config = config_par_defaut()
    if db is None:
        return config
    try:
        from ..database import UserSettings
        # Même lecture tolérante que `_load_company_settings` de main.py : on
        # ignore `user_id`, ce réglage est global à la boutique.
        ligne = (db.query(UserSettings)
                 .filter(UserSettings.setting_key == CLE_DROITS)
                 .order_by(UserSettings.updated_at.desc())
                 .first())
        if ligne and ligne.setting_value:
            import json as _json
            enregistre = _json.loads(ligne.setting_value)
            if isinstance(enregistre, dict):
                for nom, role in enregistre.items():
                    if nom in config and role in ROLES_POSSIBLES:
                        config[nom] = role
    except Exception:  # noqa: BLE001
        import logging
        logging.exception("[assistant] droits illisibles, retour aux défauts")
    return config


def enregistrer_config(db: Session, valeurs: Any) -> dict:
    """Écrit les droits. Ne retient que les outils connus et les rôles valides :
    le reste est ignoré en silence plutôt que d'ouvrir une porte par erreur."""
    import json as _json

    from ..database import UserSettings

    config = charger_config(db)
    if isinstance(valeurs, dict):
        for nom, role in valeurs.items():
            if nom in _PAR_NOM and role in ROLES_POSSIBLES:
                config[nom] = role

    ligne = (db.query(UserSettings)
             .filter(UserSettings.setting_key == CLE_DROITS)
             .order_by(UserSettings.updated_at.desc())
             .first())
    charge = _json.dumps(config, ensure_ascii=False)
    if ligne:
        ligne.setting_value = charge
    else:
        db.add(UserSettings(user_id=None, setting_key=CLE_DROITS, setting_value=charge))
    db.commit()
    return config


def _module_actif(nom: Optional[str], db: Optional[Session]) -> bool:
    """Le module du métier est-il allumé ? Tolérant : un profil illisible ne doit
    pas priver l'utilisateur de tout son assistant."""
    if not nom:
        return True
    try:
        from .. import shop_profile
        return bool(shop_profile.module_actif(nom, db))
    except Exception:  # noqa: BLE001
        import logging
        logging.exception("[assistant] profil illisible pour le module %s", nom)
        return False


def _autorise(outil: dict, user, config: dict,
              db: Optional[Session] = None) -> bool:
    fonctionnalite = outil.get("feature")
    if fonctionnalite and not has_feature(fonctionnalite):
        return False
    # Le métier de la boutique : un outil qui parle de dates de péremption n'a
    # rien à dire dans une boutique qui n'en saisit pas.
    if not _module_actif(outil.get("module"), db):
        return False
    requis = config.get(outil["name"], outil.get("role_min", "user"))
    if requis == "desactive":
        return False
    return _niveau(getattr(user, "role", None)) >= NIVEAUX.get(requis, 1)


def catalogue(db: Optional[Session] = None) -> list[dict]:
    """Description des outils pour l'écran Paramètres : ce que fait chacun, s'il
    écrit, ce que le plan autorise, et le rôle actuellement exigé."""
    config = charger_config(db)
    fiches = []
    for outil in OUTILS:
        fonctionnalite = outil.get("feature")
        module = outil.get("module")
        fiches.append({
            "nom": outil["name"],
            # Première phrase de la description : suffisant pour un tableau.
            "resume": outil["description"].split(". ")[0].rstrip(".") + ".",
            "ecriture": bool(outil.get("ecriture")),
            "role": config.get(outil["name"], outil.get("role_min", "user")),
            "defaut": outil.get("role_min", "user"),
            "fonctionnalite": fonctionnalite,
            "incluse_dans_le_plan": (not fonctionnalite) or has_feature(fonctionnalite),
            "module": module,
            # Deux raisons différentes d'être indisponible, à ne pas confondre à
            # l'écran : l'abonnement ne le couvre pas, ou le métier n'en a pas
            # l'usage (et l'administrateur peut allumer le module).
            "module_actif": _module_actif(module, db),
        })
    return fiches


def _resoudre(nom: str, arguments: Any, user,
              db: Optional[Session] = None) -> tuple[Optional[dict], Optional[dict], dict]:
    """Renvoie (outil, erreur, arguments_propres)."""
    outil = _PAR_NOM.get(nom)
    if outil is None:
        return None, {"erreur": f"Outil inconnu : {nom}"}, {}
    if not _autorise(outil, user, charger_config(db), db):
        return None, {"erreur": "Cet outil n'est pas disponible pour votre compte "
                                "ou votre plan."}, {}
    return outil, None, arguments if isinstance(arguments, dict) else {}


def outils_disponibles(user, db: Optional[Session] = None) -> list[dict]:
    """Schémas des outils que *cet* utilisateur peut employer, dans *ce* plan,
    avec les droits réglés par l'administrateur. Un outil absent de cette liste
    est invisible pour le modèle : il ne peut donc pas l'appeler, quoi que
    raconte la conversation."""
    config = charger_config(db)
    return [{
        "name": o["name"],
        "description": o["description"],
        "parameters": o["parameters"],
    } for o in OUTILS if _autorise(o, user, config, db)]


def est_ecriture(nom: str) -> bool:
    return bool((_PAR_NOM.get(nom) or {}).get("ecriture"))


def _appeler(outil: dict, cle: str, nom: str, db: Session, user, arguments: dict,
             echec: str) -> dict:
    try:
        return outil[cle](db, user, arguments)
    except Exception as exc:  # noqa: BLE001
        import logging
        logging.exception("[assistant] échec de %s sur l'outil %s", cle, nom)
        try:
            db.rollback()
        except Exception:
            pass
        return {"erreur": f"{echec} ({type(exc).__name__})."}


def executer(nom: str, arguments: Any, db: Session, user) -> dict:
    """Exécute un outil de lecture. Renvoie toujours un dictionnaire : une
    erreur est une donnée que le modèle peut lire et expliquer, pas une
    exception qui casse le tour."""
    outil, erreur, propres = _resoudre(nom, arguments, user, db)
    if erreur:
        return erreur
    if outil.get("ecriture"):
        return {"erreur": "Cet outil modifie des données : il passe par une "
                          "confirmation, pas par une lecture."}
    return _appeler(outil, "fn", nom, db, user, propres, "La lecture a échoué")


def preparer(nom: str, arguments: Any, db: Session, user) -> dict:
    """Valide une écriture et en produit un résumé lisible, **sans rien
    modifier**. C'est ce résumé que l'humain voit avant de confirmer."""
    outil, erreur, propres = _resoudre(nom, arguments, user, db)
    if erreur:
        return erreur
    if not outil.get("ecriture"):
        return {"erreur": "Cet outil ne modifie rien : il n'y a rien à confirmer."}
    return _appeler(outil, "prepare", nom, db, user, propres,
                    "La préparation a échoué")


def appliquer(nom: str, arguments: Any, db: Session, user) -> dict:
    """Exécute réellement l'écriture. Appelée **uniquement** depuis la route de
    confirmation, après un clic humain — jamais depuis la boucle du modèle."""
    outil, erreur, propres = _resoudre(nom, arguments, user, db)
    if erreur:
        return erreur
    if not outil.get("ecriture"):
        return {"erreur": "Cet outil ne modifie rien."}
    return _appeler(outil, "apply", nom, db, user, propres, "L'exécution a échoué")
