"""
Applique le filigrane du logo aux images produit DÉJÀ présentes sur le disque.

À lancer une fois, après la mise en place du filigrane automatique, pour
rattraper les photos ajoutées avant. Idempotent et réversible :

  - chaque original est copié dans `<dossier>/_originals/` avant traitement ;
  - un fichier dont l'original est déjà sauvegardé est considéré comme déjà
    traité et ignoré (relancer le script ne double donc pas le filigrane) ;
  - l'écriture du fichier servi est atomique (temp + rename), le site pouvant
    lire ces images en même temps.

Usage :
    venv/bin/python scripts/watermark_existing.py [--limit N] [--dry-run]
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal            # noqa: E402
from app.services.watermark import apply_watermark, get_shop_logo  # noqa: E402

PRODUCTS_DIR = Path("static/uploads/products")
ORIGINALS_DIR = PRODUCTS_DIR / "_originals"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def main():
    limit = None
    dry_run = "--dry-run" in sys.argv
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    db = SessionLocal()
    logo = get_shop_logo(db)
    db.close()
    if not logo:
        print("Aucun logo configuré : rien à faire.")
        return

    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)

    images = sorted(
        p for p in PRODUCTS_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in EXTS
    )

    done = skipped = failed = 0
    for path in images:
        original = ORIGINALS_DIR / path.name

        # Original déjà sauvegardé → image déjà traitée lors d'un run précédent.
        if original.exists():
            skipped += 1
            continue

        if limit is not None and done >= limit:
            break

        try:
            source_bytes = path.read_bytes()
            stamped = apply_watermark(source_bytes, logo)
            if stamped == source_bytes:
                # Filigrane non appliqué (image illisible) : on ne touche à rien.
                print(f"  (inchangée) {path.name}")
                failed += 1
                continue

            if dry_run:
                print(f"  [dry-run] filigranerait {path.name}")
                done += 1
                continue

            # Sauvegarde de l'original, puis écriture atomique du fichier servi.
            shutil.copy2(path, original)
            fd, tmp = tempfile.mkstemp(dir=str(PRODUCTS_DIR), suffix=path.suffix)
            with os.fdopen(fd, "wb") as f:
                f.write(stamped)
            # mkstemp crée en 0600 : sans ça, le serveur web (autre utilisateur)
            # ne peut plus lire l'image et renvoie 403.
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
            done += 1
        except Exception as exc:
            failed += 1
            print(f"  ERREUR sur {path.name} : {exc}")

    print(
        f"\nTerminé — {done} filigranée(s), {skipped} déjà traitée(s), "
        f"{failed} en échec, sur {len(images)} image(s)."
    )


if __name__ == "__main__":
    main()
