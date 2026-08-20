# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Le produit de gestion existant utilise FastAPI, SQLAlchemy, Jinja2 et JavaScript. Le nouveau site marketing est une surface web séparée ; le choix de sa stack est délégué à Codex, avec l'obligation de se connecter proprement au backend existant et de rester exécutable localement via Docker.

## Users

Le public principal est composé de propriétaires et responsables de commerces au Sénégal : boutiques de mode, vendeurs de téléphones et d'électronique, commerces alimentaires, grossistes, fournisseurs et vendeurs actifs sur WhatsApp. Ils gèrent leur activité depuis un ordinateur ou un smartphone, souvent sans équipe informatique.

## Product Purpose

Le produit réunit la gestion du stock, des ventes, des clients, des créances, des achats, des fournisseurs, des rapports et de la boutique en ligne. Le site marketing doit permettre à un visiteur de comprendre visuellement cette continuité et de rejoindre l'application.

## Positioning

Une seule opération métier met à jour les éléments concernés partout dans l'application : une vente touche la facture, le paiement, le stock, l'historique client et les rapports. L'application adapte aussi ses capacités au métier du commerce, notamment les IMEI, tailles, couleurs, lots, péremptions et unités.

## Operating Context

Le produit est utilisé dans de vraies boutiques. Les scènes de référence sont la vente au comptoir, la réception de marchandises, le contrôle du stock, le suivi des créances, la consultation des résultats et la gestion depuis WhatsApp ou un smartphone. La monnaie de démonstration est le franc CFA et les exemples doivent rester crédibles pour le Sénégal.

## Capabilities and Constraints

- L'application existante fonctionne et doit rester compatible.
- Les données du site marketing doivent être synthétiques ; aucune donnée client réelle ne doit être exposée.
- L'inscription SaaS self-service, le nom définitif et les prix sont encore des décisions ouvertes.
- Le backend actuel est FastAPI et l'architecture locale repose sur Docker Compose.
- Le site doit fonctionner sur mobile, sur des connexions limitées et avec réduction des animations.
- Les actions de l'assistant IA restent validées par les permissions, les règles métier et une confirmation humaine.

## Brand Commitments

Le produit n'a pas encore de nom définitif. La communication doit être professionnelle, francophone, contemporaine et clairement ancrée au Sénégal sans clichés touristiques, folkloriques ou misérabilistes. Le site doit raconter d'abord par l'image et l'interface, avec des textes très courts.

## Evidence on Hand

Le dépôt contient l'application réelle, ses écrans, ses fonctionnalités et ses profils métier. Il ne contient pas encore de témoignages validés, de chiffres commerciaux publiables, de grille tarifaire définitive ni de campagne photographique officielle. Ces éléments ne doivent pas être inventés.

## Product Principles

- Montrer le produit en train de fonctionner plutôt que multiplier les promesses.
- Représenter les commerçants comme des professionnels autonomes et compétents.
- Relier chaque fonctionnalité à une opération et à un résultat métier réel.
- Préserver la simplicité d'usage malgré la richesse du produit.
- Faire évoluer l'existant progressivement et sans exposer les données des boutiques.

## Accessibility & Inclusion

La surface doit être utilisable au clavier, lisible sur smartphone, compatible avec la réduction des animations et conforme au minimum au niveau WCAG AA pour les contrastes et les interactions principales.
