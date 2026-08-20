"""Résolution des variantes commerciales d'un produit.

Un produit ne porte pas ses variantes en propre : il les reçoit de sa catégorie
et, éventuellement, de rattachements qui lui sont propres. Ce module fait la
synthèse et calcule les prix.

Vocabulaire :

* **groupe**      — un choix offert au client (« Couleur »)
* **option**      — une valeur de ce choix (« Noir »), portant un supplément
* **surcharge**   — exception propre à un produit : autre supplément, ou option
                    masquée

Règles de prix retenues avec l'utilisateur :

* chaque option porte un **supplément** ajouté au prix de base du produit ;
* la **première option de chaque groupe est présélectionnée** sur le site ;
* le prix affiché au catalogue est celui de cette sélection par défaut,
  précédé de « À partir de » dès qu'une autre combinaison coûte davantage.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from ..database import (
    Product,
    ShopProduct,
    ShopVariantAssignment,
    ShopVariantGroup,
    ShopVariantOption,
    ShopVariantOverride,
)


def _d(valeur) -> Decimal:
    """Convertit en Decimal, en traitant NULL comme zéro."""
    if valeur is None:
        return Decimal("0")
    return valeur if isinstance(valeur, Decimal) else Decimal(str(valeur))


def prix_de_base(produit: Product, reglages: ShopProduct | None) -> Decimal:
    """Prix du produit avant tout supplément de variante."""
    if reglages is not None and reglages.shop_price is not None:
        return _d(reglages.shop_price)
    return _d(getattr(produit, "price", 0))


def groupes_du_produit(
    db: Session, product_id: int, categorie: str | None
) -> list[ShopVariantGroup]:
    """Groupes qui s'appliquent à ce produit, sans doublon.

    Un même groupe peut être rattaché à la fois à la catégorie et au produit
    (par exemple parce qu'on l'a ajouté au produit avant de le généraliser).
    Il ne doit alors apparaître qu'une fois.
    """
    requete = (
        db.query(ShopVariantGroup)
        .join(ShopVariantAssignment,
              ShopVariantAssignment.group_id == ShopVariantGroup.group_id)
        .filter(ShopVariantGroup.is_active.is_(True))
    )

    conditions = [ShopVariantAssignment.product_id == product_id]
    if categorie:
        conditions.append(
            (ShopVariantAssignment.target_type == "category")
            & (ShopVariantAssignment.category_name == categorie)
        )

    from sqlalchemy import or_
    groupes = requete.filter(or_(*conditions)).all()

    # `join` peut renvoyer le même groupe deux fois : on déduplique en gardant
    # l'ordre d'affichage voulu.
    vus: dict[int, ShopVariantGroup] = {}
    for g in groupes:
        vus.setdefault(g.group_id, g)
    return sorted(vus.values(), key=lambda g: (g.sort_order or 0, g.group_id))


def _surcharges(db: Session, product_id: int) -> dict[int, ShopVariantOverride]:
    lignes = (
        db.query(ShopVariantOverride)
        .filter(ShopVariantOverride.product_id == product_id)
        .all()
    )
    return {o.option_id: o for o in lignes}


def resoudre(db: Session, produit: Product, reglages: ShopProduct | None = None) -> dict[str, Any]:
    """Renvoie les groupes, options et prix applicables à un produit.

    Structure renvoyée :

        {
          "base": 500000.0,
          "groupes": [ { "id", "nom", "aide", "options": [ {...} ] } ],
          "prix_defaut": 500000.0,   # première option de chaque groupe
          "prix_min":    500000.0,
          "prix_max":    560000.0,
          "a_partir_de": True        # afficher « À partir de »
        }

    Un produit sans variante renvoie une liste vide et les trois prix égaux au
    prix de base : l'appelant n'a pas de cas particulier à traiter.
    """
    base = prix_de_base(produit, reglages)
    categorie = getattr(produit, "category", None)
    groupes = groupes_du_produit(db, produit.product_id, categorie)

    if not groupes:
        return {
            "base": float(base),
            "groupes": [],
            "prix_defaut": float(base),
            "prix_min": float(base),
            "prix_max": float(base),
            "a_partir_de": False,
        }

    surcharges = _surcharges(db, produit.product_id)

    sortie: list[dict[str, Any]] = []
    total_defaut = base
    total_min = base
    total_max = base

    for groupe in groupes:
        options: list[dict[str, Any]] = []
        for option in groupe.options:
            if not option.is_active:
                continue
            surcharge = surcharges.get(option.option_id)
            if surcharge is not None and surcharge.is_hidden:
                continue
            supplement = (
                _d(surcharge.price_delta)
                if surcharge is not None and surcharge.price_delta is not None
                else _d(option.price_delta)
            )
            options.append({
                "id": option.option_id,
                "libelle": option.label,
                "supplement": float(supplement),
                "prix": float(base + supplement),
                "surchargee": surcharge is not None and surcharge.price_delta is not None,
            })

        # Un groupe dont toutes les options sont masquées ne doit pas s'afficher
        # comme un sélecteur vide.
        if not options:
            continue

        options[0]["par_defaut"] = True
        supplements = [_d(o["supplement"]) for o in options]
        total_defaut += supplements[0]
        total_min += min(supplements)
        total_max += max(supplements)

        sortie.append({
            "id": groupe.group_id,
            "nom": groupe.name,
            "aide": groupe.help_text,
            "options": options,
        })

    return {
        "base": float(base),
        "groupes": sortie,
        "prix_defaut": float(total_defaut),
        "prix_min": float(total_min),
        "prix_max": float(total_max),
        # « À partir de » n'a de sens que si une combinaison coûte plus cher que
        # celle proposée par défaut.
        "a_partir_de": total_max > total_defaut,
    }


def resume_selection(db: Session, options_ids: list[int]) -> str:
    """Libellé figé à la commande : « Couleur: Noir · Capacité: 256 Go ».

    Les libellés sont recopiés et non référencés : renommer une option des mois
    plus tard ne doit pas réécrire une commande déjà passée.
    """
    if not options_ids:
        return ""
    lignes = (
        db.query(ShopVariantOption, ShopVariantGroup)
        .join(ShopVariantGroup, ShopVariantGroup.group_id == ShopVariantOption.group_id)
        .filter(ShopVariantOption.option_id.in_(options_ids))
        .order_by(ShopVariantGroup.sort_order, ShopVariantOption.sort_order)
        .all()
    )
    return " · ".join(f"{groupe.name}: {option.label}" for option, groupe in lignes)


def supplement_total(db: Session, product_id: int, options_ids: list[int]) -> Decimal:
    """Somme des suppléments réellement applicables à ce produit.

    Recalculée côté serveur à partir des identifiants d'options : le prix
    envoyé par le navigateur n'est jamais repris tel quel.
    """
    if not options_ids:
        return Decimal("0")

    surcharges = _surcharges(db, product_id)
    options = (
        db.query(ShopVariantOption)
        .filter(ShopVariantOption.option_id.in_(options_ids))
        .all()
    )

    total = Decimal("0")
    for option in options:
        surcharge = surcharges.get(option.option_id)
        if surcharge is not None and surcharge.is_hidden:
            continue
        if surcharge is not None and surcharge.price_delta is not None:
            total += _d(surcharge.price_delta)
        else:
            total += _d(option.price_delta)
    return total
