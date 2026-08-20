/**
 * Listes en fiches sur téléphone : étiquetage des cellules.
 *
 * Sur petit écran, `css/mobile.css` transforme chaque ligne de liste en fiche
 * et affiche devant chaque valeur l'intitulé de sa colonne. Cet intitulé doit
 * être présent dans le HTML : ce script le recopie depuis l'en-tête du tableau
 * vers l'attribut `data-label` de chaque cellule.
 *
 * Le travail est fait ici plutôt que dans les dix fichiers de liste parce que
 * ces listes réécrivent leurs lignes en JavaScript à chaque tri, filtre ou
 * changement de page : un attribut posé une fois à l'affichage disparaîtrait
 * au premier rafraîchissement. Un observateur suit donc ces reconstructions.
 */

(function () {
  "use strict";

  // En dessous de 4 colonnes, une table tient à l'écran : la transformer en
  // fiches ferait perdre de la densité sans rien gagner.
  var COLONNES_MINIMUM = 4;

  /** Le tableau est-il une liste de données, et non une grille de saisie ? */
  function estUneListe(table) {
    if (table.hasAttribute("data-no-cards")) return false;

    var entetes = table.querySelectorAll(":scope > thead > tr > th");
    if (entetes.length < COLONNES_MINIMUM) return false;

    if (table.querySelector(":scope > tbody") === null) return false;

    return !estGrilleDeSaisie(table, entetes.length);
  }

  /**
   * Distingue une grille de saisie d'une liste ordinaire.
   *
   * Le critère est la densité de champs, pas leur simple présence : une liste
   * comporte couramment une commande ou deux par ligne — la liste des devis a
   * un menu de statut et une case « envoyé » — sans cesser d'être une liste.
   * Une grille de saisie, elle, a un champ dans presque chaque colonne.
   *
   * Ces grilles gardent leur disposition en tableau : leurs champs sont alignés
   * colonne par colonne et parcourus au clavier ; les passer en tuiles
   * désorganiserait l'écran de facturation, le plus sensible de l'application.
   */
  function estGrilleDeSaisie(table, nbColonnes) {
    var lignes = table.querySelectorAll(":scope > tbody > tr");
    if (lignes.length === 0) return false;

    // Cases à cocher, boutons radio et champs masqués ne sont pas de la saisie
    // de données : ce sont des sélecteurs de ligne ou des bascules.
    var SAISIE = "input:not([type=checkbox]):not([type=radio]):not([type=hidden]), select, textarea";

    var cellulesAvecSaisie = 0;
    lignes.forEach(function (ligne) {
      ligne.querySelectorAll(":scope > td").forEach(function (cellule) {
        if (cellule.querySelector(SAISIE)) cellulesAvecSaisie += 1;
      });
    });

    var moyenneParLigne = cellulesAvecSaisie / lignes.length;
    return moyenneParLigne >= nbColonnes / 3;
  }

  /** Intitulés de colonnes, nettoyés des icônes de tri. */
  function intitules(table) {
    var entetes = table.querySelectorAll(":scope > thead > tr > th");
    return Array.prototype.map.call(entetes, function (th) {
      // `innerText` ignore les éléments masqués ; les boutons de tri, eux,
      // sont visibles : on part du texte propre du nœud, sans les icônes.
      var copie = th.cloneNode(true);
      copie.querySelectorAll("i, svg, .sort-icon, button").forEach(function (n) {
        n.remove();
      });
      return (copie.textContent || "").replace(/\s+/g, " ").trim();
    });
  }

  // Colonnes reconnues par leur intitulé plutôt que par leur contenu. Deux
  // raisons, constatées sur les listes réelles : la cellule d'image ne contient
  // pas d'`<img>` quand le produit n'a pas de photo (un cadre vide en tient
  // lieu), et la cellule d'actions contient le texte des entrées de son menu
  // déroulant (« Dupliquer », « Archiver »…), donc n'est jamais vide.
  var ENTETES_VISUEL = ["image", "photo", "visuel", "aperçu", "apercu"];
  var ENTETES_ACTIONS = ["actions", "action"];

  function normaliser(texte) {
    return (texte || "").toLowerCase().trim();
  }

  /** Colonne servant de visuel à la fiche. */
  function estVisuel(cellule, intitule) {
    if (ENTETES_VISUEL.indexOf(normaliser(intitule)) !== -1) return true;
    // Repli pour un tableau sans intitulé : une cellule réduite à une image.
    return (
      cellule.querySelector("img") !== null &&
      (cellule.textContent || "").trim().length === 0
    );
  }

  /** Colonne des commandes de la ligne. */
  function estActions(cellule, intitule) {
    if (ENTETES_ACTIONS.indexOf(normaliser(intitule)) !== -1) return true;
    // Repli : une cellule qui n'est faite que de commandes, sans texte propre.
    var commandes = cellule.querySelectorAll("button, a");
    return (
      commandes.length > 0 && (cellule.textContent || "").trim().length === 0
    );
  }

  function etiqueter(table) {
    if (!estUneListe(table)) {
      // Un tableau peut avoir été classé « liste » alors que son corps était
      // encore vide, puis se révéler être une grille de saisie une fois rempli.
      // Sans ce retrait, il resterait affiché en tuiles sans intitulés.
      table.classList.remove("js-card-table");
      return;
    }

    var labels = intitules(table);
    table.classList.add("js-card-table");

    table.querySelectorAll(":scope > tbody > tr").forEach(function (ligne) {
      var cellules = ligne.querySelectorAll(":scope > td");

      cellules.forEach(function (cellule, index) {
        // Une cellule à `colspan` (message « aucun résultat ») n'appartient à
        // aucune colonne : elle garde son affichage pleine largeur.
        if (cellule.hasAttribute("colspan")) return;

        var label = labels[index];

        if (estVisuel(cellule, label)) {
          cellule.setAttribute("data-media", "");
          cellule.removeAttribute("data-label");
          return;
        }

        // La colonne d'actions n'est pas toujours la dernière : on la reconnaît,
        // on ne la déduit pas de sa position.
        if (estActions(cellule, label)) {
          cellule.setAttribute("data-actions", "");
          cellule.removeAttribute("data-label");
          return;
        }

        if (label) cellule.setAttribute("data-label", label);
      });
    });
  }

  function parcourir(racine) {
    var tables = (racine || document).querySelectorAll
      ? (racine || document).querySelectorAll("table")
      : [];
    tables.forEach(etiqueter);
  }

  function demarrer() {
    parcourir(document);

    // Les listes sont reconstruites en JavaScript : on ré-étiquette à chaque
    // remplacement de lignes. Le traitement est groupé dans une micro-tâche
    // pour ne pas s'exécuter à chaque nœud ajouté d'une même mise à jour.
    var enAttente = false;
    var observateur = new MutationObserver(function () {
      if (enAttente) return;
      enAttente = true;
      requestAnimationFrame(function () {
        enAttente = false;
        parcourir(document);
      });
    });

    observateur.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", demarrer);
  } else {
    demarrer();
  }
})();
