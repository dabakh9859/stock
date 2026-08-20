# Gap's Apple — gestion de stock : installation en local

Cette archive contient **le code source complet** de l'application telle qu'elle
tourne en production le 13/08/2026. Elle ne contient **aucun secret, aucune
donnée client et aucune base de données** : vous partirez d'une base vide.

## Ce qui n'est pas dans l'archive, et pourquoi

| Écarté | Raison |
|---|---|
| `.env` | mots de passe de production. Un `.env.example` le remplace |
| `venv/` | environnement Python, à recréer localement |
| `data/`, `*.dump` | bases et sauvegardes : données réelles des clients |
| `whatsapp-service/`, `whatsapp-free/`, `whatsapp-native/` | services annexes, et surtout **sessions WhatsApp appairées** |
| `letsencrypt/`, `credentials/` | certificats et jetons |
| `static/uploads/_avant_avif/` | 39 Mo d'originaux redondants (les AVIF sont là) |

Les photos produits (`static/uploads/products`), bannières et favicons **sont**
incluses : sans elles l'application serait vide à l'écran.

## Démarrer

Python 3.12 est la version utilisée en production.

```bash
cd stock
python3.12 -m venv venv
source venv/bin/activate          # Windows : venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
```

Ouvrez ensuite `.env` et renseignez au minimum :

```
SECRET_KEY=une-chaine-longue-et-aleatoire
ADMIN_PASSWORD=le-mot-de-passe-de-votre-compte-admin-local
```

Laissez `DATABASE_URL` **vide** : sans elle, l'application bascule
automatiquement sur SQLite (`./techzone.db`), créé au premier lancement. C'est le
plus simple en local — inutile d'installer PostgreSQL.

```bash
python start.py
```

L'application écoute sur <http://localhost:8000>. Le premier démarrage crée les
tables et un compte **admin**, dont le mot de passe est celui de `ADMIN_PASSWORD`.
Si vous laissez cette variable vide, un mot de passe aléatoire est tiré et
**affiché une seule fois dans la console** : notez-le au passage.

Pour recharger automatiquement à chaque modification :

```bash
RELOAD=true python start.py
```

## Remplir la base

Une base vide rend l'application difficile à juger. Deux options :

```bash
python scripts/seed_demo.py       # jeu de données de démonstration
```

ou, pour du volume, la fonction `seed_large_test_data()` de `app/init_db.py`.

**N'importez jamais un export de production sur votre machine** : ce sont les
coordonnées, les achats et les IMEI de vrais clients.

## Repères dans le code

| Chemin | Rôle |
|---|---|
| `main.py` | application FastAPI, pages HTML, impression, routes WhatsApp |
| `start.py` | lanceur (hôte, port, rechargement) |
| `app/database.py` | modèles SQLAlchemy et connexion |
| `app/routers/` | l'API : `invoices`, `products`, `shop_admin`, `shop`… |
| `app/services/` | envois et notifications, dont `orange_sms.py` |
| `templates/` | pages Jinja2 |
| `static/js/` | le navigateur : un fichier par écran (`invoices.js`…) |
| `scripts/` | utilitaires ponctuels (migrations, images, démonstration) |

## Ce qui ne marchera pas en local, et c'est normal

- **WhatsApp** : nécessite l'instance Evolution du serveur. Sans
  `EVOLUTION_API_URL`, les envois échouent proprement.
- **SMS Orange** : il faut un forfait et les identifiants
  (`ORANGE_SMS_*`). Voir `app/services/orange_sms.py`.
- **Paiements Bictorys** : clés de production requises.
- **La boutique en ligne** : c'est une autre application (Next.js), qui dialogue
  avec celle-ci par `/api/shop/*`.

## Avant de publier ce code

Si vous poussez ce dossier sur GitHub, **mettez le dépôt en privé**. Vérifiez
aussi que votre `.env` local ne part pas avec : il est déjà listé dans
`.gitignore`, ne l'en retirez pas.
