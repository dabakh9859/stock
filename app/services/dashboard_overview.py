"""Données du tableau de bord, en un seul appel.

Le tableau de bord affichait quatre chiffres et deux graphiques, chacun servi
par un point d'API distinct. Ce service rassemble tout ce dont la page a besoin
en une seule réponse : la page fait un aller-retour au lieu de six, et les
chiffres affichés proviennent tous de la même lecture de la base — ils ne
peuvent donc pas se contredire d'un encadré à l'autre.

**Ce qui n'est volontairement pas calculé ici : la marge.** Le prix d'achat
n'est renseigné que sur 4 produits sur 129 et sur aucune des 726 variantes.
Un « bénéfice » calculé là-dessus serait une invention, pas une mesure.
De même, les moyens de paiement ne sont pas exposés : les 642 ventes portent
toutes la même valeur, un graphique n'y montrerait qu'une seule part.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

# Statuts considérés comme encaissés / partiels / en attente.
STATUT_PAYEE = "payée"
STATUT_PARTIELLE = "partiellement payée"
STATUT_ATTENTE = "en attente"


def _f(valeur) -> float:
    """Convertit en flottant, en traitant NULL comme zéro."""
    return float(valeur) if valeur is not None else 0.0


def _evolution(actuel: float, precedent: float) -> float | None:
    """Variation en pourcentage entre deux périodes.

    Renvoie None quand la période précédente est vide : afficher « +100 % »
    parce qu'on est passé de rien à quelque chose serait trompeur.
    """
    if not precedent:
        return None
    return round((actuel - precedent) / precedent * 100, 1)


def _indicateurs(db: Session, debut: date, fin: date) -> dict[str, float]:
    ligne = db.execute(
        text(
            """
            SELECT coalesce(sum(total), 0)            AS facture,
                   coalesce(sum(paid_amount), 0)      AS encaisse,
                   coalesce(sum(remaining_amount), 0) AS restant,
                   count(*)                           AS nb
            FROM invoices
            WHERE date >= :debut AND date < :fin
            """
        ),
        {"debut": debut, "fin": fin},
    ).first()

    facture, encaisse, restant, nb = (
        _f(ligne.facture),
        _f(ligne.encaisse),
        _f(ligne.restant),
        int(ligne.nb or 0),
    )
    return {
        "facture": facture,
        "encaisse": encaisse,
        "restant": restant,
        "nb_factures": nb,
        "panier_moyen": round(facture / nb) if nb else 0,
    }


def _serie_journaliere(db: Session, debut: date, fin: date) -> list[dict[str, Any]]:
    """Facturé et encaissé jour par jour, sans trou dans le calendrier.

    Les jours sans facture doivent apparaître à zéro : une courbe qui saute
    directement d'un jour au surlendemain déforme la pente et laisse croire à
    une activité continue.
    """
    lignes = db.execute(
        text(
            """
            SELECT date::date                    AS jour,
                   coalesce(sum(total), 0)       AS facture,
                   coalesce(sum(paid_amount), 0) AS encaisse
            FROM invoices
            WHERE date >= :debut AND date < :fin
            GROUP BY 1
            """
        ),
        {"debut": debut, "fin": fin},
    ).all()

    par_jour = {l.jour: (_f(l.facture), _f(l.encaisse)) for l in lignes}

    serie = []
    jour = debut
    while jour < fin:
        facture, encaisse = par_jour.get(jour, (0.0, 0.0))
        serie.append({"jour": jour.isoformat(), "facture": facture, "encaisse": encaisse})
        jour += timedelta(days=1)
    return serie


def _statut_encaissement(db: Session, debut: date, fin: date) -> list[dict[str, Any]]:
    lignes = db.execute(
        text(
            """
            SELECT status                   AS statut,
                   count(*)                 AS nb,
                   coalesce(sum(total), 0)  AS montant
            FROM invoices
            WHERE date >= :debut AND date < :fin
            GROUP BY status
            """
        ),
        {"debut": debut, "fin": fin},
    ).all()

    connus = {l.statut: (int(l.nb), _f(l.montant)) for l in lignes}
    ordre = [
        (STATUT_PAYEE, "payee"),
        (STATUT_PARTIELLE, "partielle"),
        (STATUT_ATTENTE, "attente"),
    ]
    resultat = []
    for libelle, cle in ordre:
        nb, montant = connus.pop(libelle, (0, 0.0))
        resultat.append({"cle": cle, "libelle": libelle, "nb": nb, "montant": montant})

    # Un statut inattendu (ancien libellé, saisie manuelle) ne doit pas
    # disparaître silencieusement du total.
    for libelle, (nb, montant) in connus.items():
        resultat.append({"cle": "autre", "libelle": libelle or "(sans statut)", "nb": nb, "montant": montant})
    return resultat


def _creances_par_anciennete(db: Session) -> list[dict[str, Any]]:
    """Ce qui reste dû, classé par retard.

    Volontairement hors période : une créance de mars reste due aujourd'hui.
    La borner à la période affichée la ferait disparaître de l'écran alors
    qu'elle est précisément ce qu'il faut aller récupérer.
    """
    lignes = db.execute(
        text(
            """
            SELECT CASE
                     WHEN due_date IS NULL                      THEN 'sans_echeance'
                     WHEN current_date - due_date::date <= 0    THEN 'a_echoir'
                     WHEN current_date - due_date::date <= 30   THEN 'j1_30'
                     WHEN current_date - due_date::date <= 60   THEN 'j31_60'
                     WHEN current_date - due_date::date <= 90   THEN 'j61_90'
                     ELSE                                            'j90_plus'
                   END                                  AS tranche,
                   count(*)                             AS nb,
                   coalesce(sum(remaining_amount), 0)   AS montant
            FROM invoices
            WHERE remaining_amount > 0
            GROUP BY 1
            """
        )
    ).all()

    connus = {l.tranche: (int(l.nb), _f(l.montant)) for l in lignes}
    ordre = [
        ("a_echoir", "À échoir"),
        ("j1_30", "1 – 30 j"),
        ("j31_60", "31 – 60 j"),
        ("j61_90", "61 – 90 j"),
        ("j90_plus", "Plus de 90 j"),
        ("sans_echeance", "Sans échéance"),
    ]
    resultat = []
    for cle, libelle in ordre:
        nb, montant = connus.get(cle, (0, 0.0))
        if nb or cle in ("j1_30", "j31_60", "j61_90", "j90_plus"):
            resultat.append({"cle": cle, "libelle": libelle, "nb": nb, "montant": montant})
    return resultat


def _classement(db: Session, debut: date, fin: date, quoi: str, limite: int = 8) -> list[dict[str, Any]]:
    """Meilleurs produits, clients ou catégories sur la période."""
    requetes = {
        "produits": """
            SELECT coalesce(ds.product_name, '(sans nom)') AS libelle,
                   coalesce(sum(ds.total_amount), 0)       AS montant
            FROM daily_sales ds
            WHERE ds.sale_date >= :debut AND ds.sale_date < :fin
            GROUP BY 1 ORDER BY 2 DESC LIMIT :limite
        """,
        # L'identifiant remonte avec le nom : sans lui le tableau de bord ne
        # peut pas faire de ses barres un lien vers la fiche du client.
        "clients": """
            SELECT coalesce(c.name, '(client supprimé)') AS libelle,
                   coalesce(sum(i.total), 0)             AS montant,
                   i.client_id                           AS identifiant
            FROM invoices i
            LEFT JOIN clients c ON c.client_id = i.client_id
            WHERE i.date >= :debut AND i.date < :fin
            GROUP BY 1, 3 ORDER BY 2 DESC LIMIT :limite
        """,
        "categories": """
            SELECT coalesce(p.category, 'Sans catégorie') AS libelle,
                   coalesce(sum(ds.total_amount), 0)      AS montant
            FROM daily_sales ds
            LEFT JOIN products p ON p.product_id = ds.product_id
            WHERE ds.sale_date >= :debut AND ds.sale_date < :fin
            GROUP BY 1 ORDER BY 2 DESC LIMIT :limite
        """,
    }
    lignes = db.execute(
        text(requetes[quoi]), {"debut": debut, "fin": fin, "limite": limite}
    ).all()
    return [
        {
            "libelle": l.libelle,
            "montant": _f(l.montant),
            # Seul le classement des clients porte un identifiant ; les autres
            # renvoient None et le tableau de bord n'en fait pas un lien.
            "id": getattr(l, "identifiant", None),
        }
        for l in lignes
    ]


def _activite(db: Session, debut: date, fin: date) -> dict[str, Any]:
    """Répartition des facturations par jour de semaine et par heure.

    Sert à repérer les moments de forte affluence — utile pour organiser les
    présences en boutique.
    """
    lignes = db.execute(
        text(
            """
            SELECT extract(isodow FROM date)::int AS jour,
                   extract(hour  FROM date)::int  AS heure,
                   count(*)                       AS nb
            FROM invoices
            WHERE date >= :debut AND date < :fin
            GROUP BY 1, 2
            """
        ),
        {"debut": debut, "fin": fin},
    ).all()

    cellules = {(l.jour, l.heure): int(l.nb) for l in lignes}
    heures = list(range(8, 21))  # amplitude d'ouverture réaliste
    grille = [
        {"jour": j, "heure": h, "nb": cellules.get((j, h), 0)}
        for j in range(1, 8)
        for h in heures
    ]
    return {
        "heures": heures,
        "cellules": grille,
        "max": max((c["nb"] for c in grille), default=0),
    }


#: Seuil d'alerte de stock. La table `products` n'a pas de colonne de seuil :
#: le reste de l'application considère « stock faible » à 3 unités ou moins.
#: Cette valeur est reprise telle quelle pour que le tableau de bord annonce
#: les mêmes décomptes que la page Produits — un écart entre les deux écrans
#: coûterait plus cher en confiance que le choix d'un meilleur seuil.
SEUIL_STOCK_FAIBLE = 3


def _stock(db: Session) -> dict[str, Any]:
    ligne = db.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE coalesce(quantity, 0) > :seuil)      AS sain,
                   count(*) FILTER (WHERE coalesce(quantity, 0) > 0
                                      AND coalesce(quantity, 0) <= :seuil)     AS faible,
                   count(*) FILTER (WHERE coalesce(quantity, 0) <= 0)          AS rupture,
                   coalesce(sum(coalesce(quantity, 0) * coalesce(price, 0)), 0) AS valeur_vente
            FROM products
            WHERE coalesce(is_archived, false) = false
              AND coalesce(shop_only, false) = false
            """
        ),
        {"seuil": SEUIL_STOCK_FAIBLE},
    ).first()
    return {
        "sain": int(ligne.sain or 0),
        "faible": int(ligne.faible or 0),
        "rupture": int(ligne.rupture or 0),
        "valeur_vente": _f(ligne.valeur_vente),
    }


def construire_apercu(db: Session, jours: int = 30) -> dict[str, Any]:
    """Assemble l'ensemble des données du tableau de bord pour N jours."""
    jours = max(1, min(int(jours), 730))

    fin = date.today() + timedelta(days=1)          # borne haute exclusive : inclut aujourd'hui
    debut = fin - timedelta(days=jours)
    debut_precedent = debut - timedelta(days=jours)  # même durée, juste avant

    actuel = _indicateurs(db, debut, fin)
    precedent = _indicateurs(db, debut_precedent, debut)

    return {
        "periode": {
            "jours": jours,
            "debut": debut.isoformat(),
            "fin": (fin - timedelta(days=1)).isoformat(),
        },
        "indicateurs": {
            **actuel,
            "evolution": {
                "facture": _evolution(actuel["facture"], precedent["facture"]),
                "encaisse": _evolution(actuel["encaisse"], precedent["encaisse"]),
                "nb_factures": _evolution(actuel["nb_factures"], precedent["nb_factures"]),
                "panier_moyen": _evolution(actuel["panier_moyen"], precedent["panier_moyen"]),
            },
            "precedent": precedent,
        },
        "serie": _serie_journaliere(db, debut, fin),
        "statuts": _statut_encaissement(db, debut, fin),
        "creances": _creances_par_anciennete(db),
        "top_produits": _classement(db, debut, fin, "produits"),
        "top_clients": _classement(db, debut, fin, "clients"),
        "categories": _classement(db, debut, fin, "categories", limite=6),
        "activite": _activite(db, debut, fin),
        "stock": _stock(db),
    }
