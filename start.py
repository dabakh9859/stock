#!/usr/bin/env python3
"""
Script de démarrage pour l'application Stock
"""

import uvicorn
import os
import sys
from pathlib import Path

# Ajouter le répertoire racine au PYTHONPATH
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

def main():
    """Démarrer l'application FastAPI"""
    print("🚀 Démarrage de Stock - Gestion de Stock")
    print("=" * 50)
    
    # Configuration
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    # Désactiver le reload par défaut en production (Koyeb)
    reload = os.getenv("RELOAD", "false").lower() == "true"
    
    print(f"📍 Serveur: http://{host}:{port}")
    print(f"🔄 Rechargement automatique: {'Activé' if reload else 'Désactivé'}")
    print(f"🗄️  Base de données: PostgreSQL")
    print("=" * 50)
    # Les mots de passe ne sont plus imprimés ici : ils sont fournis par
    # ADMIN_PASSWORD / USER_PASSWORD, ou tirés au hasard et affichés une seule
    # fois par l'amorçage de la base (voir app/init_db.py).
    if os.getenv("SECRET_KEY") in (None, "", "your-secret-key-here"):
        print("⚠️  SECRET_KEY n'est pas défini : les jetons de session sont")
        print("   signés avec une clé connue. À corriger avant toute mise en ligne.")
        print("=" * 50)
    
    try:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
            access_log=True,
            # Faire confiance aux en-têtes X-Forwarded-* du reverse proxy, sinon les
            # redirections générées repassent en http:// alors que le site est en https.
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
        )
    except KeyboardInterrupt:
        print("\n👋 Arrêt de l'application")
    except Exception as e:
        print(f"❌ Erreur lors du démarrage: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
