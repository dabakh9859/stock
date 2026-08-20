"""Détection et fusion des fiches produit en double.

Le catalogue accumule des doublons parce qu'une facture d'échange dont la ligne
n'est pas rattachée à un produit existant crée systématiquement une fiche
neuve. Sept fiches « IPHONE XR » coexistaient ainsi, chacune avec sa part du
stock et de l'historique — le stock réel d'un modèle devenait impossible à lire
d'un coup d'œil, et les statistiques par produit étaient éclatées.

Deux opérations sont proposées :

* `detecter` regroupe les fiches actives dont le nom est identique une fois la
  casse et les espaces normalisés, avec de quoi décider laquelle garder ;
* `fusionner` rattache tout ce qui pend aux fiches absorbées vers la fiche
  gardée, additionne les quantités, puis archive les fiches vidées.

La fusion est irréversible : elle réécrit des lignes de facture et de vente.
Elle est donc entièrement transactionnelle, et refuse de s'exécuter si une
table référençant un produit n'a pas été prévue (voir `verifier_couverture`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Product


# --- Tables à rattacher --------------------------------------------------
#
# `daily_sales` et `stock_movements` portent une colonne `product_id` SANS
# contrainte de clé étrangère : rien dans la base ne signalerait leur oubli, et
# les ventes du produit absorbé deviendraient silencieusement orphelines. Elles
# sont donc listées explicitement, au même titre que les autres.
TABLES_SIMPLES = (
    "arrival_items",
    "daily_sales",
    "delivery_note_items",
    "invoice_exchange_items",
    "invoice_items",
    "product_images",
    "product_serial_numbers",
    "product_variants",
    "purchase_order_items",
    "quotation_items",
    "shop_demands",
    "shop_order_items",
    "stock_movements",
)

# Ces tables portent une contrainte d'unicité incluant `product_id`. Rattacher
# une ligne absorbée qui ferait doublon avec une ligne de la fiche gardée
# violerait la contrainte : dans ce cas la ligne de la fiche gardée fait foi et
# la ligne absorbée est supprimée. La clé indiquée est celle qui, avec
# `product_id`, forme la contrainte.
TABLES_UNIQUES = {
    "product_serial_numbers": "serial_number",
    "shop_products": None,  # un seul enregistrement boutique par produit
    "shop_variant_overrides": "option_id",
    "shop_wishlist_items": "customer_id",
}

# `shop_variant_assignments` a une contrainte à quatre colonnes ; elle est
# traitée à part pour rester lisible.
TABLE_AFFECTATIONS = "shop_variant_assignments"


def normaliser(nom: str | None) -> str:
    """Clé de regroupement d'un nom de produit.

    Casse et espaces multiples sont ignorés : « IPHONE  XR » et « iPhone XR »
    désignent le même appareil. Les accents sont conservés — « Ecouteurs » et
    « Écouteurs » sont deux saisies différentes qu'il vaut mieux montrer à
    l'utilisateur que fusionner d'office.
    """
    if not nom:
        return ""
    sans_espaces = re.sub(r"\s+", " ", nom).strip()
    # NFC pour que deux écritures Unicode du même accent se rejoignent.
    return unicodedata.normalize("NFC", sans_espaces).casefold()


def est_generique(cle: str) -> bool:
    """Le nom est-il un libellé de remplissage plutôt qu'un vrai modèle ?

    « Article personnalisé » et « Produit repris (IMEI) » sont posés
    automatiquement quand la ligne d'échange n'a pas de nom : deux fiches qui
    les portent désignent presque toujours deux appareils différents. Elles ne
    doivent donc ni être rapprochées d'office, ni être proposées à la fusion
    d'un simple clic.
    """
    return cle.startswith(("article personnalisé", "produit repris"))


def trouver_fiche_homonyme(db: Session, nom: str | None) -> Product | None:
    """Fiche active portant le même nom, ou `None`.

    Sert à rattacher un appareil repris en échange à la fiche existante du
    modèle au lieu d'en créer une nouvelle. La comparaison ignore la casse et
    les espaces multiples, comme la détection de doublons — les deux doivent
    voir les mêmes fiches, sans quoi l'écran de fusion signalerait des doublons
    que la saisie continuerait de produire.

    La plus récente l'emporte quand plusieurs fiches existent déjà : c'est la
    fiche que l'écran de fusion propose aussi de garder.
    """
    cle = normaliser(nom)
    if not cle or est_generique(cle):
        return None

    ligne = db.execute(text("""
        SELECT product_id FROM products
        WHERE coalesce(is_archived, false) = false
          AND lower(regexp_replace(btrim(name), '\\s+', ' ', 'g')) = :cle
        ORDER BY product_id DESC
        LIMIT 1
    """), {"cle": cle}).first()
    if not ligne:
        return None
    return db.query(Product).filter(Product.product_id == ligne.product_id).first()


@dataclass
class FicheDoublon:
    product_id: int
    name: str
    source: str | None
    category: str | None
    quantity: int
    price: float
    variantes_disponibles: int
    ventes: int
    entry_date: object | None
    image_path: str | None


@dataclass
class GroupeDoublon:
    cle: str
    libelle: str
    #: Vrai si le groupe rassemble des fiches nées d'une reprise en échange.
    echange: bool = False
    fiches: list[FicheDoublon] = field(default_factory=list)

    @property
    def stock_total(self) -> int:
        return sum(f.quantity for f in self.fiches)

    @property
    def ventes_totales(self) -> int:
        return sum(f.ventes for f in self.fiches)

    @property
    def variantes_totales(self) -> int:
        return sum(f.variantes_disponibles for f in self.fiches)

    @property
    def generique(self) -> bool:
        """Nom de remplissage : le groupe est affiché mais avec un avertissement.

        Voir `est_generique`. Ces fiches signalent un vrai défaut de saisie,
        mais les fusionner confondrait deux appareils distincts.
        """
        # La clé porte l'origine en suffixe : on ne teste que le nom.
        return est_generique(self.cle.split("\u241f")[0])

    @property
    def suggestion_id(self) -> int:
        """Fiche proposée pour recevoir la fusion.

        C'est la plus récente : à l'usage, c'est celle qu'on vient d'alimenter
        et dont la saisie est la plus à jour. Ce n'est qu'une proposition — le
        choix reste entièrement libre dans l'écran, chaque fiche affichant son
        stock, ses variantes et son historique pour trancher.
        """
        return max(f.product_id for f in self.fiches)


def detecter(db: Session) -> list[GroupeDoublon]:
    """Groupes de fiches actives partageant le même nom normalisé."""
    lignes = db.execute(text("""
        SELECT p.product_id, p.name, p.source, p.category, p.image_path,
               coalesce(p.quantity, 0) AS quantity,
               coalesce(p.price, 0) AS price, p.entry_date,
               (SELECT count(*) FROM product_variants v
                 WHERE v.product_id = p.product_id
                   AND coalesce(v.is_sold, false) = false) AS variantes,
               (SELECT count(*) FROM invoice_items ii
                 WHERE ii.product_id = p.product_id) AS ventes
        FROM products p
        WHERE coalesce(p.is_archived, false) = false
        ORDER BY p.product_id
    """)).fetchall()

    par_cle: dict[str, GroupeDoublon] = {}
    for l in lignes:
        nom = normaliser(l.name)
        if not nom:
            continue
        """
        Les fiches d'échange forment leurs propres groupes.

        Une reprise et un appareil acheté au fournisseur portent le même nom
        mais ne sont pas la même chose : l'un est d'occasion, avec son état et
        son prix propres, l'autre est du neuf. Les fusionner mélangerait deux
        stocks distincts et rendrait le prix de vente incohérent. La clé de
        regroupement inclut donc l'origine — on voit ainsi « iPhone XR » deux
        fois, une fois côté achat, une fois côté échange, chacune fusionnable
        séparément.
        """
        echange = (l.source or "").strip().lower() == "exchange"
        cle = f"{nom}\u241f{'echange' if echange else 'normal'}"
        libelle = f"{l.name} — {'reprises en échange' if echange else 'achats'}"
        groupe = par_cle.setdefault(
            cle, GroupeDoublon(cle=cle, libelle=libelle, echange=echange))
        groupe.fiches.append(FicheDoublon(
            product_id=l.product_id, name=l.name, source=l.source,
            category=l.category, quantity=int(l.quantity or 0),
            price=float(l.price or 0), variantes_disponibles=int(l.variantes or 0),
            ventes=int(l.ventes or 0), entry_date=l.entry_date,
            image_path=l.image_path,
        ))

    groupes = [g for g in par_cle.values() if len(g.fiches) > 1]
    # Les groupes les plus encombrants d'abord : ce sont ceux qui gênent
    # vraiment la lecture du stock.
    groupes.sort(key=lambda g: (-len(g.fiches), g.libelle.casefold()))
    return groupes


def verifier_couverture(db: Session) -> list[str]:
    """Tables référençant un produit et non prévues par la fusion.

    Un module ajouté plus tard (une nouvelle table avec `product_id`) doit
    faire échouer la fusion plutôt que de laisser des lignes pointer vers une
    fiche archivée. Ce garde-fou coûte une requête et évite une corruption
    silencieuse.
    """
    connues = set(TABLES_SIMPLES) | set(TABLES_UNIQUES) | {TABLE_AFFECTATIONS, "products"}
    presentes = {r.table_name for r in db.execute(text("""
        SELECT table_name FROM information_schema.columns
        WHERE column_name = 'product_id' AND table_schema = 'public'
    """))}
    return sorted(presentes - connues)


def _rattacher_simple(db: Session, table: str, garde: int, absorbes: list[int]) -> int:
    r = db.execute(
        text(f"UPDATE {table} SET product_id = :garde WHERE product_id = ANY(:absorbes)"),
        {"garde": garde, "absorbes": absorbes},
    )
    return r.rowcount or 0


def _rattacher_unique(db: Session, table: str, cle: str | None,
                      garde: int, absorbes: list[int]) -> tuple[int, int]:
    """Rattache en écartant ce qui ferait doublon avec la fiche gardée."""
    if cle:
        supprime = db.execute(text(f"""
            DELETE FROM {table} a
            WHERE a.product_id = ANY(:absorbes)
              AND EXISTS (SELECT 1 FROM {table} g
                           WHERE g.product_id = :garde AND g.{cle} = a.{cle})
        """), {"garde": garde, "absorbes": absorbes}).rowcount or 0
    else:
        # Unicité sur `product_id` seul : la fiche gardée ne peut avoir qu'une
        # ligne, donc on ne rattache que si elle n'en a aucune.
        possede = db.execute(
            text(f"SELECT 1 FROM {table} WHERE product_id = :garde LIMIT 1"),
            {"garde": garde},
        ).first()
        if possede:
            supprime = db.execute(
                text(f"DELETE FROM {table} WHERE product_id = ANY(:absorbes)"),
                {"absorbes": absorbes},
            ).rowcount or 0
            return 0, supprime
        # Sinon on n'en garde qu'une seule parmi les absorbées.
        restant = db.execute(text(f"""
            DELETE FROM {table} WHERE product_id = ANY(:absorbes)
              AND ctid NOT IN (SELECT min(ctid) FROM {table}
                                WHERE product_id = ANY(:absorbes))
        """), {"absorbes": absorbes}).rowcount or 0
        supprime = restant

    rattache = _rattacher_simple(db, table, garde, absorbes)
    return rattache, supprime


def fusionner(db: Session, garde_id: int, absorbes_ids: list[int],
              nom: str | None = None) -> dict:
    """Rattache tout à `garde_id`, additionne les stocks, archive le reste.

    `nom` renomme au passage la fiche gardée. C'est utile parce que la fiche la
    plus récente est souvent celle dont le nom est le moins soigné (« iphone
    xr » en minuscules, saisi à la volée pendant un échange) alors que c'est
    elle qu'on veut garder pour son stock : sans cette possibilité, il faudrait
    fusionner puis rouvrir la fiche pour la renommer.

    L'appelant est responsable du `commit` : la fusion touche une quinzaine de
    tables et ne doit jamais être appliquée à moitié.
    """
    absorbes_ids = [i for i in absorbes_ids if i != garde_id]
    if not absorbes_ids:
        raise ValueError("Aucune fiche à absorber.")

    oubliees = verifier_couverture(db)
    if oubliees:
        raise RuntimeError(
            "Fusion refusée : ces tables référencent un produit sans être "
            f"prises en charge ({', '.join(oubliees)}). Ajoutez-les à "
            "product_duplicates.py avant de fusionner."
        )

    garde = db.query(Product).filter(Product.product_id == garde_id).first()
    if not garde:
        raise ValueError(f"Fiche gardée introuvable (#{garde_id}).")

    fiches_absorbees = db.query(Product).filter(
        Product.product_id.in_(absorbes_ids)
    ).all()
    if len(fiches_absorbees) != len(absorbes_ids):
        trouves = {p.product_id for p in fiches_absorbees}
        manquants = sorted(set(absorbes_ids) - trouves)
        raise ValueError(f"Fiches introuvables : {manquants}")

    detail: dict[str, int] = {}

    # 1. Tables à unicité : on écarte d'abord les collisions.
    for table, cle in TABLES_UNIQUES.items():
        rattache, supprime = _rattacher_unique(db, table, cle, garde_id, absorbes_ids)
        if rattache:
            detail[table] = rattache
        if supprime:
            detail[f"{table} (doublons écartés)"] = supprime

    # 2. Les affectations de variantes boutique, dont l'unicité porte sur
    #    quatre colonnes.
    supprime = db.execute(text(f"""
        DELETE FROM {TABLE_AFFECTATIONS} a
        WHERE a.product_id = ANY(:absorbes)
          AND EXISTS (SELECT 1 FROM {TABLE_AFFECTATIONS} g
                       WHERE g.product_id = :garde
                         AND g.group_id = a.group_id
                         AND g.target_type = a.target_type
                         AND g.category_name IS NOT DISTINCT FROM a.category_name)
    """), {"garde": garde_id, "absorbes": absorbes_ids}).rowcount or 0
    if supprime:
        detail[f"{TABLE_AFFECTATIONS} (doublons écartés)"] = supprime
    rattache = _rattacher_simple(db, TABLE_AFFECTATIONS, garde_id, absorbes_ids)
    if rattache:
        detail[TABLE_AFFECTATIONS] = rattache

    # 3. Le reste se rattache sans condition — dont `product_variants` : les
    #    variantes (IMEI, codes-barres, prix d'achat) des fiches absorbées
    #    viennent s'ajouter à celles de la fiche gardée, qui se retrouve avec
    #    l'intégralité des appareils du modèle. C'est le cœur de l'opération :
    #    sept fiches « IPHONE XR » portant chacune quelques IMEI deviennent une
    #    seule fiche listant tous les appareils en stock.
    for table in TABLES_SIMPLES:
        if table in TABLES_UNIQUES:
            continue  # déjà traité au titre de son unicité
        rattache = _rattacher_simple(db, table, garde_id, absorbes_ids)
        if rattache:
            detail[table] = rattache

    # 4. Stocks additionnés, fiches vidées archivées.
    #    On additionne les quantités telles qu'elles sont plutôt que de les
    #    recalculer depuis les variantes : les deux divergent déjà sur
    #    certaines fiches, et une fusion n'a pas à corriger d'elle-même des
    #    chiffres de stock que personne n'a demandé à revoir.
    stock_ajoute = 0
    for fiche in fiches_absorbees:
        stock_ajoute += int(fiche.quantity or 0)
        fiche.quantity = 0
        fiche.is_archived = True
        note = f"Fusionnée dans la fiche #{garde_id} ({garde.name})."
        fiche.notes = f"{fiche.notes}\n{note}" if fiche.notes else note

    garde.quantity = int(garde.quantity or 0) + stock_ajoute

    nom_propre = (nom or "").strip()
    if nom_propre and nom_propre != garde.name:
        detail["nom corrigé"] = 1
        garde.name = nom_propre[:500]

    return {
        "garde": {"product_id": garde.product_id, "name": garde.name,
                  "quantity": garde.quantity},
        "absorbes": [{"product_id": f.product_id, "name": f.name}
                     for f in fiches_absorbees],
        "stock_ajoute": stock_ajoute,
        "detail": detail,
    }
