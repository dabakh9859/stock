"""Rattrapage : convertit en AVIF les images produits déjà en base.

Les nouvelles images sont converties à l'upload (voir services/image_convert.py).
Ce script traite le stock existant, déposé avant la mise en place.

Marche à suivre :

    # 1. Simulation — n'écrit rien, montre ce qui serait fait
    PYTHONPATH=. venv/bin/python scripts/convert_images_to_avif.py

    # 2. Exécution réelle
    PYTHONPATH=. venv/bin/python scripts/convert_images_to_avif.py --appliquer

Les originaux ne sont jamais supprimés : ils sont déplacés dans
`static/uploads/_avant_avif/`. Un retour arrière consiste à les remettre en
place et à restaurer la sauvegarde de base prise avant l'opération.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import _normalize_db_url, _RAW_DATABASE_URL
from app.services.image_convert import to_avif, avif_available

# Colonnes de la base qui contiennent un chemin d'image produit.
SOURCES = [
    ("products", "product_id", "image_path"),
    ("product_images", "image_id", "image_path"),
]

ARCHIVE = Path("static/uploads/_avant_avif")


def collecter(session):
    """Regroupe les références de la base **par fichier**.

    Renvoie `{chemin: [(table, colonne, clé, identifiant), ...]}`.

    Le regroupement est indispensable : une même image est fréquemment
    référencée deux fois, par `products.image_path` et par la ligne
    correspondante de `product_images`. En traitant les références une par une,
    la première convertissait le fichier puis déplaçait l'original à l'archive,
    et la seconde ne trouvait plus rien — laissant une référence cassée.
    """
    par_fichier: dict[str, list] = {}
    for table, cle, colonne in SOURCES:
        requete = text(
            f"SELECT {cle}, {colonne} FROM {table} "
            f"WHERE {colonne} IS NOT NULL AND {colonne} <> ''"
        )
        for identifiant, chemin in session.execute(requete):
            par_fichier.setdefault(chemin, []).append((table, colonne, cle, identifiant))
    return par_fichier


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument(
        "--appliquer",
        action="store_true",
        help="écrit réellement (sans ce drapeau, simulation seule)",
    )
    parseur.add_argument(
        "--qualite", type=int, default=75, help="qualité AVIF (défaut : 75)"
    )
    options = parseur.parse_args()
    simulation = not options.appliquer

    if not avif_available():
        print("ERREUR : l'encodeur AVIF n'est pas disponible dans cet environnement.")
        return 1

    url = _normalize_db_url(_RAW_DATABASE_URL)
    print(f"Base       : {url.rsplit('/', 1)[-1]}")
    print(f"Mode       : {'SIMULATION (aucune écriture)' if simulation else 'ÉCRITURE RÉELLE'}")
    print(f"Qualité    : {options.qualite}")
    print(f"Archive    : {ARCHIVE}/")
    print("-" * 78)

    session = sessionmaker(bind=create_engine(url))()

    if not simulation:
        ARCHIVE.mkdir(parents=True, exist_ok=True)

    converties = deja = introuvables = echecs = 0
    poids_avant = poids_apres = 0

    try:
        for chemin, references in collecter(session).items():
            source = Path(chemin)
            resume = ", ".join(f"{t}#{i}" for t, _c, _k, i in references)

            if source.suffix.lower() == ".avif":
                deja += len(references)
                continue

            if not source.exists():
                introuvables += len(references)
                print(f"  ABSENT     {resume}  {chemin}")
                continue

            octets_avant = source.stat().st_size
            donnees, extension = to_avif(
                source.read_bytes(), source.suffix, quality=options.qualite
            )

            if extension.lower() != ".avif":
                echecs += len(references)
                print(f"  ÉCHEC      {resume}  {source.name} (conservé tel quel)")
                continue

            cible = source.with_suffix(".avif")
            poids_avant += octets_avant
            poids_apres += len(donnees)
            gain = 100 - 100 * len(donnees) / octets_avant

            print(
                f"  {'[simu] ' if simulation else 'CONVERTI '}"
                f"{source.name[:44]:<46} {octets_avant/1024:6.0f}K -> {len(donnees)/1024:5.0f}K  {gain:3.0f}%"
            )

            if not simulation:
                cible.write_bytes(donnees)
                # L'original part à l'archive : rien n'est détruit.
                shutil.move(str(source), str(ARCHIVE / source.name))
                # Toutes les références à ce fichier suivent, pas seulement la première.
                for table, colonne, cle, identifiant in references:
                    session.execute(
                        text(f"UPDATE {table} SET {colonne} = :nouveau WHERE {cle} = :id"),
                        {"nouveau": str(cible), "id": identifiant},
                    )

            converties += len(references)

        if not simulation:
            session.commit()

    except Exception as exc:
        session.rollback()
        print(f"\nERREUR : {exc}")
        return 1
    finally:
        session.close()

    print("-" * 78)
    print(f"Converties    : {converties}")
    print(f"Déjà en AVIF  : {deja}")
    print(f"Introuvables  : {introuvables}")
    print(f"Échecs        : {echecs}")
    if poids_avant:
        print(
            f"Poids         : {poids_avant/1048576:.1f} Mo -> {poids_apres/1048576:.1f} Mo "
            f"({100 - 100*poids_apres/poids_avant:.0f} % de gain)"
        )
    if simulation:
        print("\nSimulation seule. Relancer avec --appliquer pour écrire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
