/**
 * Lignes de tableau cliquables.
 *
 * Dans toutes les listes de l'application, chaque ligne porte déjà un bouton
 * « Voir » (une icône `bi-eye`) dans sa colonne Actions. Viser ce petit bouton
 * est inutilement précis : ce script rend la ligne entière cliquable et lui
 * fait déclencher ce même bouton.
 *
 * Le choix d'un gestionnaire délégué unique, plutôt qu'un `onclick` ajouté dans
 * chacune des dix listes, tient à la façon dont elles sont construites : leurs
 * lignes sont réécrites en JavaScript à chaque rafraîchissement, tri ou
 * changement de page. Un gestionnaire posé sur le document survit à ces
 * reconstructions et couvre du même coup les listes ajoutées plus tard.
 */

(function () {
  "use strict";

  // Éléments qui ont leur propre comportement au clic : on ne leur vole jamais
  // l'événement (boutons d'action, liens, cases à cocher, menus déroulants…).
  var INTERACTIFS =
    "button, a, input, select, textarea, label, summary, " +
    "[onclick], [data-bs-toggle], .dropdown, .form-check, .no-row-click";

  /** Retrouve le bouton « Voir » d'une ligne, s'il existe. */
  function boutonVoir(ligne) {
    var boutons = ligne.querySelectorAll("button, a");
    for (var i = 0; i < boutons.length; i++) {
      var b = boutons[i];
      if (b.disabled) continue;

      // Signal principal : l'icône œil, commune à toutes les listes.
      if (b.querySelector("i.bi-eye, i.bi-eye-fill")) return b;

      // Filet de sécurité pour une liste qui n'utiliserait pas l'icône.
      var titre = (b.getAttribute("title") || "").toLowerCase();
      if (titre.indexOf("voir") === 0 || titre.indexOf("détails") === 0) return b;
    }
    return null;
  }

  document.addEventListener("click", function (evenement) {
    if (evenement.button !== 0 || evenement.defaultPrevented) return;

    // Un clic avec Ctrl/Cmd/Maj sert à ouvrir ailleurs ou à étendre une
    // sélection : on le laisse tranquille.
    if (evenement.ctrlKey || evenement.metaKey || evenement.shiftKey) return;

    var cible = evenement.target;
    if (!(cible instanceof Element)) return;

    var ligne = cible.closest("tbody tr");
    if (!ligne) return;

    if (cible.closest(INTERACTIFS)) return;

    // Sélectionner du texte dans une cellule (pour le copier) ne doit pas
    // déclencher la navigation.
    var selection = window.getSelection();
    if (selection && String(selection).length > 0) return;

    var bouton = boutonVoir(ligne);
    if (!bouton) return;

    evenement.preventDefault();
    bouton.click();
  });

  // Curseur « main » sur les seules lignes réellement cliquables. `:has()` n'est
  // pas connu des navigateurs anciens ; le cas échéant on s'en passe, le clic
  // continue de fonctionner.
  try {
    var style = document.createElement("style");
    style.textContent =
      "tbody tr:has(button i.bi-eye), tbody tr:has(a i.bi-eye) { cursor: pointer; }";
    document.head.appendChild(style);
  } catch (e) {
    /* sans conséquence : seul l'indice visuel manque */
  }
})();
