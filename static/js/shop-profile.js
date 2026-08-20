/*
  Métier de la boutique — onglet « Métier de la boutique » de l'écran Paramètres.

  Même parti pris que assistant-rights.js : autonome, fetch brut, aucune
  dépendance au bloc appSettings. Le métier n'est pas un réglage d'affichage
  parmi d'autres, il vit dans sa propre entrée côté serveur.

  Le serveur reste seul juge : la liste des métiers, le métier en vigueur et
  l'état des modules viennent de lui. Le navigateur ne fait que présenter.
*/

(function () {
  var pane = document.getElementById('metier');
  if (!pane) return;

  var zoneAlerte = document.getElementById('shopProfileAlert');
  var zoneActuel = document.getElementById('shopProfileCurrent');
  var liste = document.getElementById('shopProfileList');
  var listeModules = document.getElementById('shopModulesList');
  var boutonModules = document.getElementById('saveShopModules');
  var API = (window.URL_PREFIX || '') + '/api/shop-profile';

  var etat = null;
  var charge = false;
  var enCours = false;

  function alerte(texte, type) {
    zoneAlerte.innerHTML = '<div class="alert alert-' + type +
      ' py-2 small mb-3">' + texte + '</div>';
    if (type === 'success') {
      zoneAlerte.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  function texte(parent, balise, classe, contenu) {
    var el = document.createElement(balise);
    if (classe) el.className = classe;
    if (contenu != null) el.textContent = contenu;
    parent.appendChild(el);
    return el;
  }

  function dessinerActuel(actuel) {
    zoneActuel.innerHTML = '';
    var boite = texte(zoneActuel, 'div', 'border rounded p-3 bg-body-tertiary');

    var entete = texte(boite, 'div', 'd-flex align-items-center gap-2 mb-1');
    texte(entete, 'strong', null, actuel.libelle);
    texte(entete, 'span',
      'badge ' + (actuel.applique ? 'text-bg-success' : 'text-bg-warning'),
      actuel.applique ? 'configuré' : 'non configuré');

    texte(boite, 'div', 'small text-muted', actuel.tracage_explication);

    if (!actuel.applique) {
      texte(boite, 'div', 'small text-muted mt-2',
        'Choisissez votre métier ci-dessous pour préparer les catégories et ' +
        'les grilles de tailles correspondantes.');
    }
  }

  function carte(fiche, courant) {
    var col = document.createElement('div');
    col.className = 'col-md-6';

    var actif = fiche.code === courant;
    var boite = texte(col, 'div',
      'h-100 border rounded p-3' + (actif ? ' border-primary' : ''));

    texte(boite, 'div', 'fw-semibold', fiche.libelle);
    texte(boite, 'div', 'small text-muted mt-1', fiche.resume);
    texte(boite, 'div', 'small text-muted mt-2',
      'Catégories : ' + fiche.categories.slice(0, 4).join(', ') +
      (fiche.categories.length > 4 ? '…' : ''));

    var pied = texte(boite, 'div', 'mt-3');
    if (actif) {
      texte(pied, 'span', 'badge text-bg-primary', 'Métier actuel');
    } else {
      var bouton = texte(pied, 'button', 'btn btn-outline-primary btn-sm',
        'Adopter ce métier');
      bouton.type = 'button';
      bouton.addEventListener('click', function () {
        appliquer(fiche, bouton);
      });
    }
    return col;
  }

  function ligneModule(module) {
    var col = document.createElement('div');
    col.className = 'col-md-6';

    var boite = texte(col, 'div', 'form-check border rounded p-3 h-100');

    var coche = document.createElement('input');
    coche.className = 'form-check-input';
    coche.type = 'checkbox';
    coche.id = 'mod-' + module.nom;
    coche.checked = module.actif;
    coche.dataset.module = module.nom;
    // Un module hors abonnement ne s'allume pas : le proposer serait mentir.
    coche.disabled = !module.incluse_dans_le_plan;
    boite.appendChild(coche);

    var etiquette = document.createElement('label');
    etiquette.className = 'form-check-label';
    etiquette.htmlFor = coche.id;
    texte(etiquette, 'div', 'fw-semibold', module.libelle);
    texte(etiquette, 'div', 'small text-muted', module.explication);

    var notes = [];
    if (!module.incluse_dans_le_plan) {
      notes.push('absent de votre plan d\'abonnement');
    } else if (module.regle_a_la_main) {
      notes.push(module.defaut_metier
        ? 'éteint par vous, alors que votre métier le propose'
        : 'allumé par vous, en dehors de votre métier');
    }
    if (notes.length) {
      texte(etiquette, 'div', 'small text-warning-emphasis mt-1', notes.join(''));
    }
    boite.appendChild(etiquette);

    return col;
  }

  function dessinerModules(modules) {
    listeModules.innerHTML = '';
    (modules || []).forEach(function (m) {
      listeModules.appendChild(ligneModule(m));
    });
  }

  function dessiner(donnees) {
    etat = donnees;
    dessinerActuel(donnees.actuel);
    liste.innerHTML = '';
    donnees.profils.forEach(function (fiche) {
      liste.appendChild(carte(fiche, donnees.actuel.code));
    });
    dessinerModules(donnees.modules);
  }

  function appliquer(fiche, bouton) {
    if (enCours) return;
    // Un changement de métier crée des catégories : mieux vaut une question de
    // trop qu'un catalogue rempli par accident.
    var question = 'Passer la boutique en « ' + fiche.libelle + ' » ?\n\n' +
      'Les catégories manquantes de ce métier seront créées. Aucun produit, ' +
      'aucune catégorie et aucun historique ne sera supprimé.';
    if (!window.confirm(question)) return;

    enCours = true;
    bouton.disabled = true;
    var initial = bouton.textContent;
    bouton.textContent = 'Configuration…';

    fetch(API, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profil: fiche.code, appliquer: true })
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      enCours = false;
      bouton.disabled = false;
      bouton.textContent = initial;
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Changement refusé.', 'danger');
        return;
      }
      dessiner(res.d);
      // Les libellés sont rendus côté serveur : ils ne changeront qu'au
      // prochain chargement des écrans.
      alerte((res.d.message || 'Métier enregistré.') +
        ' Rechargez la page pour voir les nouveaux libellés.', 'success');
    }).catch(function () {
      enCours = false;
      bouton.disabled = false;
      bouton.textContent = initial;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  }

  function charger() {
    if (charge) return;
    charge = true;
    fetch(API, { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        return r.json().then(function (d) { return { s: r.status, d: d }; });
      })
      .then(function (res) {
        if (res.s !== 200) {
          charge = false;
          liste.innerHTML = '';
          alerte((res.d && (res.d.message || res.d.detail)) ||
            'Métier illisible.', 'warning');
          return;
        }
        dessiner(res.d);
      })
      .catch(function () {
        charge = false;
        liste.innerHTML = '';
        alerte('Connexion impossible — réessayez.', 'warning');
      });
  }

  boutonModules.addEventListener('click', function () {
    var valeurs = {};
    listeModules.querySelectorAll('input[data-module]').forEach(function (c) {
      if (!c.disabled) valeurs[c.dataset.module] = c.checked;
    });

    boutonModules.disabled = true;
    fetch(API + '/modules', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modules: valeurs })
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      boutonModules.disabled = false;
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Enregistrement refusé.', 'danger');
        return;
      }
      dessiner(res.d);
      alerte(res.d.message || 'Modules enregistrés.', 'success');
    }).catch(function () {
      boutonModules.disabled = false;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  });

  var onglet = document.querySelector('a[href="#metier"][data-bs-toggle="pill"]');
  if (onglet) onglet.addEventListener('shown.bs.tab', charger);
  if (pane.classList.contains('active')) charger();
})();
