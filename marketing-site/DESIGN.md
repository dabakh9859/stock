---
name: Stock (nom de travail)
description: Un petit monde commercial connecté, du mouvement en boutique à la vue d'ensemble.
colors:
  navy: "#18204e"
  navy-raised: "#222b67"
  indigo: "#6673d8"
  periwinkle: "#aab4ff"
  cloud: "#f2f4ff"
  cloud-raised: "#e6eaff"
  white: "#ffffff"
  action-orange: "#ff6b3d"
  action-orange-hover: "#dc4822"
  mango: "#ffc94f"
  mint: "#37c5a0"
  ink: "#151a3e"
  muted: "#62698a"
  line: "rgba(24, 32, 78, .14)"
typography:
  display:
    fontFamily: "Sora, sans-serif"
    fontSize: "clamp(56px, 6vw, 94px)"
    fontWeight: 720
    lineHeight: 0.96
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "Sora, sans-serif"
    fontSize: "clamp(43px, 5.4vw, 80px)"
    fontWeight: 690
    lineHeight: 1
    letterSpacing: "-0.04em"
  title:
    fontFamily: "Sora, sans-serif"
    fontSize: "clamp(26px, 3vw, 42px)"
    fontWeight: 690
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  body:
    fontFamily: "Afacad, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: "Afacad, sans-serif"
    fontSize: "12px"
    fontWeight: 700
    lineHeight: 1
rounded:
  compact: "7px"
  control: "11px"
  action: "13px"
  panel: "14px"
  window: "17px"
  full: "50%"
spacing:
  compact: "9px"
  control-x: "23px"
  panel: "24px"
  mobile-gutter: "20px"
  section-gutter: "7vw"
components:
  button-primary:
    backgroundColor: "{colors.action-orange}"
    textColor: "{colors.white}"
    typography: "{typography.label}"
    rounded: "{rounded.action}"
    padding: "0 23px"
    height: "55px"
  button-primary-hover:
    backgroundColor: "{colors.action-orange-hover}"
  button-light:
    backgroundColor: "{colors.white}"
    textColor: "{colors.navy}"
    typography: "{typography.label}"
    rounded: "{rounded.action}"
    padding: "0 23px"
    height: "55px"
  operational-overlay:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "12px 14px"
  product-window:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.window}"
---

# Design System: Stock (nom de travail)

## Overview

**Creative North Star: "Le petit monde opérationnel"**

Stock transforme le commerce en un monde 2.5D relié : boutique, fournisseur, cliente, marchandise, événements et application partagent le même espace indigo-pervenche. Les scènes transparentes, les lignes de circulation et les interfaces flottantes font voir une opération physique rejoindre le système, puis devenir une décision.

La page reste ample et narrative. Elle évite le hero-dashboard et la grille de cartes SaaS : les panneaux compacts servent seulement de preuves opérationnelles à l'intérieur d'une scène, tandis que les grandes sections restent des champs continus. L'ancrage sénégalais vient de situations et détails métier crédibles, jamais d'un registre touristique.

**Key Characteristics:**

- Scènes de commerce 2.5D transparentes dans une palette commune.
- Sora dense pour les déclarations; Afacad clair pour texte, navigation et opérations.
- Sol cloud/pervenche, structure indigo, orange d'action, mangue et menthe fonctionnelles.
- Récit spatial GSAP épinglé sur desktop, scène sticky à quatre états sur mobile.
- Toute métrique de démonstration est explicitement qualifiée de fictive ou de démonstration.

## Colors

La palette est froide et lumineuse dans ses grands champs; le corail-orange déclenche l'action, tandis que mangue et menthe codent l'attention et la validation.

### Primary

- **Operational Navy** (`#18204e`): grands fonds de continuité, barres d'application et étiquettes de nœuds.
- **Connected Indigo** (`#6673d8`): accent de marque, emphase typographique, liens du monde et champ final.
- **Action Orange** (`#ff6b3d`): appels principaux, progression active et opération en cours; hover à `#dc4822`.

### Secondary

- **Mango Signal** (`#ffc94f`): flux, assistant et détails d'attention sur fonds indigo.
- **Mint Confirmation** (`#37c5a0`): états connectés, succès, validation et capacités disponibles.
- **Periwinkle** (`#aab4ff`): famille illustrative et soutien chromatique du monde 2.5D.

### Neutral

- **Cloud Ground** (`#f2f4ff`): fond principal et colonne narrative.
- **Raised Cloud** (`#e6eaff`): surfaces secondaires, icônes et séparation tonale.
- **Commerce Ink** (`#151a3e`): texte principal.
- **Operational Muted** (`#62698a`): texte d'appui, métadonnées et légendes.
- **White** (`#ffffff`): fenêtres produit et overlays opérationnels.
- **Indigo Hairline** (`rgba(24, 32, 78, .14)`): structure discrète des listes et séparations.

### Named Rules

**The Orange Means Action Rule.** Orange signale une action, une progression ou l'opération active; il ne devient pas un grand fond décoratif.

**The Functional Accent Rule.** Mangue attire l'attention et menthe confirme; ne pas intervertir ces rôles dans les interfaces opérationnelles.

## Typography

**Display Font:** Sora (avec sans-serif en repli)  
**Body Font:** Afacad (avec sans-serif en repli)

**Character:** Sora donne aux titres une géométrie compacte adaptée au monde isométrique. Afacad apporte une voix plus humaine, rapide à lire, jusque dans les micro-données.

### Hierarchy

- **Display** (720, `clamp(56px, 6vw, 94px)`, 0.96): proposition du hero; réduit jusqu'à 43px sur les plus petits écrans.
- **Headline** (680–690, `clamp(43px, 5.4vw, 80px)`, 1–1.06): arguments de section et transitions du récit.
- **Title** (690, `clamp(26px, 3vw, 42px)`, 1.1): titres métier et fenêtres produit.
- **Body** (400, 14–18px, 1.55–1.7): explication courte, limitée à 365–560px.
- **Label** (620–760, 8–14px): navigation, états, CTA et données opérationnelles; chiffres tabulaires quand leur comparaison compte.

### Named Rules

**The Two-Layer Voice Rule.** Sora porte les propositions et titres conséquents; Afacad porte ce qui explique, guide, chiffre ou déclenche une action.

**The Short Copy Rule.** Les grandes compositions racontent visuellement; les paragraphes restent courts et étroits.

## Layout

Le hero desktop est un split asymétrique, environ 42/58, avec une offre concise à gauche et une grande scène 2.5D débordante à droite. Les sections utilisent un gutter récurrent de 7vw et 135–165px de respiration verticale. Les champs alternent cloud, navy, blanc et indigo plutôt que d'être enfermés dans des cartes.

La séquence signature occupe un viewport : copie à 31% et monde à 69%. Sur desktop (900px et plus), GSAP épingle la scène sur 4300px de progression et fait voyager la marchandise en quatre opérations. À 899px et moins, la scène devient sticky (`46svh`, minimum 370px) au-dessus de quatre blocs de `52svh`; chaque bloc met en avant un état du même monde. Hero, profils et assistant passent en colonne, les gutters deviennent 20px, et les actions principales prennent toute la largeur. À 480px, les éléments du monde sont resserrés sans changer le récit.

**The World Before Widgets Rule.** Une grande scène continue établit le système; les overlays compacts prouvent les opérations sans devenir une grille de cartes autonome.

**The Same Story, New Choreography Rule.** Conserver les quatre états et leur ordre; seule la chorégraphie passe du pin desktop à la scène sticky mobile.

## Elevation & Depth

Le système utilise un relief doux et froid pour détacher des objets du champ 2.5D : ombres multi-couches, transparence blanche et léger blur pour les overlays; drop-shadow pour les scènes PNG. Les grands fonds et la structure restent plats. Cercles, grilles, lignes courbes et changements d'échelle produisent autant de profondeur que les ombres.

### Shadow Vocabulary

- **Floating Overlay** (`0 24px 70px rgba(41, 49, 105, .16), 0 8px 24px rgba(41, 49, 105, .08)`): événements, menus et panneaux flottants.
- **Action Lift** (`0 12px 28px rgba(225, 72, 34, .23)`): bouton orange; devient `0 17px 35px rgba(225, 72, 34, .29)` au hover.
- **Product Window** (`0 30px 70px rgba(35,43,96,.22), 0 9px 22px rgba(35,43,96,.1)`): aperçu complet de l'application.
- **Assistant Stage** (`0 34px 90px rgba(4,8,32,.34)`): démonstration claire sur fond navy.

### Named Rules

**The Operational Lift Rule.** Le relief appartient aux objets, contrôles et interfaces qui flottent dans le monde; pas aux sections éditoriales.

## Shapes

Les silhouettes sont douces et compactes : boutons à 11–13px, overlays à 14–15px, fenêtres produit à 16–18px. Les modules internes descendent à 7–10px. Les ellipses larges représentent terrain et orbites; les cercles complets restent réservés aux avatars, statuts et marqueurs. Les traits indigo fins relient sans encadrer toute la page.

## Components

### Buttons

- **Shape:** action principale de 55px, rayon 13px, padding horizontal 23px; variante header de 43px et rayon 11px.
- **Primary:** fond orange, texte blanc, Afacad fort et icône directionnelle.
- **Hover / Focus:** translation -3px, orange sombre et ombre renforcée sur 250ms; focus orange de 3px avec offset 4px.
- **Light:** blanc sur champ final indigo; devient orange avec texte blanc au hover.

### Cards / Containers

- **Corner Style:** 14–18px pour overlays et fenêtres; 7–10px pour modules internes.
- **Background:** blanc presque opaque pour les événements et blanc/cloud pour l'application.
- **Shadow Strategy:** Floating Overlay ou Product Window uniquement dans la scène opérationnelle.
- **Border:** hairline indigo froid, généralement autour de 14% d'opacité.
- **Internal Padding:** 12–24px dans les overlays; 21–38px dans les vues produit.

### Navigation

Le header desktop est une grille à trois zones de 86px sur cloud. Les liens Afacad passent de 72% à 100% d'opacité et se soulignent au hover. Sous 900px, un bouton navy de 42px ouvre un panneau blanc arrondi et élevé; la connexion y reçoit l'orange d'action.

### Operational Overlay

Chaque événement associe une icône teinte, un libellé compact, une valeur forte et, si nécessaire, une confirmation menthe. Les panneaux sont positionnés dans le monde, jamais alignés comme une collection de cartes. Les chiffres et noms restent fictifs et leur statut de démonstration visible.

### Product Window

La fenêtre miniature utilise une barre navy, un logo orange, un point live menthe, une navigation latérale pâle et des modules métriques bordés. La variante compacte supprime l'activité secondaire et réduit typographie, padding et grille sans changer la hiérarchie.

### Profile Selector

Sur desktop, cinq lignes Sora de 78px occupent la colonne gauche; l'état actif devient navy, se décale de 15px et révèle une flèche orange. Le panneau droit est une seule grande fenêtre produit adaptée au métier. Sur mobile, les lignes deviennent un rail horizontal et l'actif reçoit une ligne orange de 2px.

### Spatial Commerce Journey

Les trois PNG transparents partagent cadrage isométrique, palette et éclairage. L'animation desktop révèle successivement fournisseur, boutique, vente puis application via lignes, colis, événements et orbites. Mobile conserve la scène sticky et module opacité/échelle par état. En réduction de mouvement, les éléments essentiels sont visibles, les traits complets et les durées ramenées à `.01ms`.

## Do's and Don'ts

### Do:

- **Do** construire les scènes comme un même monde 2.5D transparent, avec perspective, éclairage et palette cohérents.
- **Do** employer les interfaces flottantes pour démontrer une opération précise et son effet.
- **Do** préserver l'ordre fournisseur → stock → vente → vue d'ensemble sur desktop et mobile.
- **Do** qualifier toute donnée synthétique avec « fictif », « fictive » ou « démonstration ».
- **Do** écrire « Stock (nom de travail) » ou rendre le statut provisoire adjacent à la marque.
- **Do** conserver un parcours complet et lisible avec `prefers-reduced-motion`.

### Don't:

- **Don't** revenir à la photographie documentaire ou mélanger les anciennes photos au monde 2.5D.
- **Don't** remplacer la scène spatiale par un hero-dashboard ou une grille de cartes SaaS.
- **Don't** utiliser l'orange comme grand aplat décoratif, ni mangue ou menthe hors de leurs rôles.
- **Don't** présenter métrique, client, témoignage, prix ou entreprise de démonstration comme réel.
- **Don't** introduire d'eyebrow décoratif; un petit libellé nomme un lieu, état, opération ou statut des données.
