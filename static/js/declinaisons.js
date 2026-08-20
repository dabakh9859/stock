/*
  Grille de déclinaisons — tailles, couleurs, contenances.

  La grille proposée vient du serveur, pas du navigateur : ce sont les attributs
  déclarés sur la catégorie du produit (semés par le profil de boutique, ajustés
  ensuite dans Paramètres). Le navigateur ne fait que cocher et croiser.

  `ouvrirGrilleDeclinaisons(productId)` est appelée depuis la fiche produit
  (products.js). Le reste est privé.
*/

(function () {
  var modale = document.getElementById('grilleDeclinaisonsModal');
  if (!modale) return;   // boutique qui ne décline pas : rien à faire ici

  var API = (window.URL_PREFIX || '') + '/api/declinaisons';
  var zoneAxes = document.getElementById('grilleAxes');
  var zoneAlerte = document.getElementById('grilleAlerte');
  var corps = document.getElementById('grilleCorps');
  var apercu = document.getElementById('grilleApercu');
  var champQuantite = document.getElementById('grilleQuantite');
  var boutonCreer = document.getElementById('grilleEngendrer');
  var titreProduit = document.getElementById('grilleProduitNom');

  var produitCourant = null;
  var enCours = false;

  function texte(parent, balise, classe, contenu) {
    var el = document.createElement(balise);
    if (classe) el.className = classe;
    if (contenu != null) el.textContent = contenu;
    parent.appendChild(el);
    return el;
  }

  function alerte(message, type) {
    zoneAlerte.innerHTML = '<div class="alert alert-' + type +
      ' py-2 small">' + message + '</div>';
  }

  function selection() {
    var choix = {};
    zoneAxes.querySelectorAll('input[data-axe]:checked').forEach(function (c) {
      var axe = c.dataset.axe;
      if (!choix[axe]) choix[axe] = [];
      choix[axe].push(c.value);
    });
    return choix;
  }

  function majApercu() {
    var choix = selection();
    var axes = Object.keys(choix);
    if (!axes.length) {
      apercu.textContent = '';
      boutonCreer.disabled = true;
      return;
    }
    var total = axes.reduce(function (n, axe) {
      return n * choix[axe].length;
    }, 1);
    apercu.textContent = total + ' combinaison' + (total > 1 ? 's' : '');
    boutonCreer.disabled = false;
  }

  function dessinerAxes(grille) {
    zoneAxes.innerHTML = '';
    if (!grille.length) {
      texte(zoneAxes, 'div', 'alert alert-warning py-2 small',
        "Aucun attribut n'est déclaré sur la catégorie de ce produit. " +
        'Ajoutez-en dans Paramètres → Catégories, ou choisissez un métier qui ' +
        'en propose dans Paramètres → Métier de la boutique.');
      boutonCreer.disabled = true;
      return;
    }
    grille.forEach(function (axe) {
      var bloc = texte(zoneAxes, 'div', 'mb-2');
      texte(bloc, 'div', 'small fw-semibold', axe.nom);
      var lignes = texte(bloc, 'div', 'd-flex flex-wrap gap-2 mt-1');
      (axe.valeurs || []).forEach(function (valeur, i) {
        var enveloppe = texte(lignes, 'div', 'form-check form-check-inline m-0');
        var coche = document.createElement('input');
        coche.className = 'form-check-input';
        coche.type = 'checkbox';
        coche.value = valeur;
        coche.dataset.axe = axe.nom;
        coche.id = 'axe-' + axe.nom.replace(/\W+/g, '_') + '-' + i;
        coche.addEventListener('change', majApercu);
        enveloppe.appendChild(coche);
        var etiquette = document.createElement('label');
        etiquette.className = 'form-check-label small';
        etiquette.htmlFor = coche.id;
        etiquette.textContent = valeur;
        enveloppe.appendChild(etiquette);
      });
    });
    majApercu();
  }

  function ligne(d) {
    var tr = document.createElement('tr');
    texte(tr, 'td', null, d.etiquette);
    texte(tr, 'td', 'font-monospace small text-muted', d.reference);

    var cellule = document.createElement('td');
    if (d.suivi_a_l_exemplaire) {
      // Ancienne variante suivie par is_sold : pas de quantité à régler.
      texte(cellule, 'span', 'small text-muted',
        d.is_sold ? 'vendue' : 'en stock');
    } else {
      var groupe = texte(cellule, 'div', 'input-group input-group-sm');
      var champ = document.createElement('input');
      champ.type = 'number';
      champ.className = 'form-control';
      champ.min = '0';
      champ.value = d.quantite;
      groupe.appendChild(champ);
      var valider = texte(groupe, 'button', 'btn btn-outline-secondary');
      valider.type = 'button';
      valider.innerHTML = '<i class="bi bi-check2"></i>';
      valider.addEventListener('click', function () {
        reglerQuantite(d, champ, valider);
      });
    }
    tr.appendChild(cellule);

    var actions = document.createElement('td');
    var supprimer = texte(actions, 'button', 'btn btn-sm btn-outline-danger');
    supprimer.type = 'button';
    supprimer.innerHTML = '<i class="bi bi-trash"></i>';
    supprimer.title = 'Retirer cette déclinaison';
    supprimer.addEventListener('click', function () {
      supprimerDeclinaison(d, supprimer);
    });
    actions.appendChild(supprimer);
    tr.appendChild(actions);

    return tr;
  }

  function dessiner(donnees) {
    produitCourant = donnees.produit;
    titreProduit.textContent = '— ' + donnees.produit.nom;
    document.getElementById('grilleCompteur').textContent =
      donnees.declinaisons.length;
    document.getElementById('grilleStock').textContent =
      'stock total : ' + donnees.stock;
    dessinerAxes(donnees.grille || []);

    corps.innerHTML = '';
    if (!donnees.declinaisons.length) {
      var tr = document.createElement('tr');
      var td = texte(tr, 'td', 'text-muted', 'Aucune pour le moment.');
      td.colSpan = 4;
      corps.appendChild(tr);
      return;
    }
    donnees.declinaisons.forEach(function (d) { corps.appendChild(ligne(d)); });
  }

  function charger(productId) {
    zoneAlerte.innerHTML = '';
    fetch(API + '/produit/' + productId, {
      headers: { 'Accept': 'application/json' }
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Grille indisponible.', 'warning');
        return;
      }
      dessiner(res.d);
    }).catch(function () {
      alerte('Connexion impossible — réessayez.', 'warning');
    });
  }

  function engendrer() {
    if (enCours || !produitCourant) return;
    var choix = selection();
    if (!Object.keys(choix).length) return;

    enCours = true;
    boutonCreer.disabled = true;
    fetch(API + '/produit/' + produitCourant.product_id + '/grille', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        attributs: choix,
        quantite: parseInt(champQuantite.value, 10) || 0
      })
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      enCours = false;
      boutonCreer.disabled = false;
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Création refusée.', 'danger');
        return;
      }
      alerte(res.d.message, res.d.creees.length ? 'success' : 'secondary');
      charger(produitCourant.product_id);
    }).catch(function () {
      enCours = false;
      boutonCreer.disabled = false;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  }

  function reglerQuantite(d, champ, bouton) {
    bouton.disabled = true;
    fetch(API + '/' + d.variant_id + '/quantite', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quantite: parseInt(champ.value, 10) || 0 })
    }).then(function (r) {
      return r.json().then(function (x) { return { s: r.status, d: x }; });
    }).then(function (res) {
      bouton.disabled = false;
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Correction refusée.', 'danger');
        return;
      }
      alerte(d.etiquette + ' : ' + res.d.avant + ' → ' + res.d.apres +
        '. Le mouvement est dans l\'historique du stock.', 'success');
      charger(produitCourant.product_id);
    }).catch(function () {
      bouton.disabled = false;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  }

  function supprimerDeclinaison(d, bouton) {
    if (!window.confirm('Retirer « ' + d.etiquette + ' » ?')) return;
    bouton.disabled = true;
    fetch(API + '/' + d.variant_id, { method: 'DELETE' })
      .then(function (r) {
        return r.json().then(function (x) { return { s: r.status, d: x }; });
      }).then(function (res) {
        bouton.disabled = false;
        if (res.s !== 200) {
          alerte((res.d && (res.d.message || res.d.detail)) ||
            'Suppression refusée.', 'danger');
          return;
        }
        alerte('« ' + res.d.etiquette + ' » retirée.', 'success');
        charger(produitCourant.product_id);
      }).catch(function () {
        bouton.disabled = false;
        alerte('Connexion impossible — réessayez.', 'danger');
      });
  }

  boutonCreer.addEventListener('click', engendrer);

  window.ouvrirGrilleDeclinaisons = function (productId) {
    corps.innerHTML = '<tr><td colspan="4" class="text-muted">Chargement…</td></tr>';
    zoneAxes.innerHTML = '<div class="text-muted small">Chargement…</div>';
    apercu.textContent = '';
    charger(productId);
    new bootstrap.Modal(modale).show();
  };
})();
