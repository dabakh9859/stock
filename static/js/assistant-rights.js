/*
  Droits de l'assistant — onglet « Assistant IA » de l'écran Paramètres.

  Volontairement autonome (fetch brut, pas de dépendance à settings.js ni à
  apiStorage) : ce réglage n'a rien à voir avec le bloc de paramètres de
  l'application, il vit dans sa propre table de droits côté serveur.

  La liste des outils vient du serveur, jamais du navigateur : c'est lui qui
  sait ce que le plan d'abonnement autorise, et lui seul décide ensuite quels
  outils sont réellement montrés au modèle.
*/

(function () {
  var pane = document.getElementById('assistant');
  if (!pane) return;

  var corps = document.getElementById('assistantRightsBody');
  var zoneAlerte = document.getElementById('assistantRightsAlert');
  var bouton = document.getElementById('saveAssistantRights');
  var API = (window.URL_PREFIX || '') + '/api/assistant/permissions';

  var roles = [];
  var charge = false;

  function alerte(texte, type) {
    zoneAlerte.innerHTML =
      '<div class="alert alert-' + type + ' py-2 small">' + texte + '</div>';
    if (type === 'success') {
      setTimeout(function () { zoneAlerte.innerHTML = ''; }, 4000);
    }
  }

  function ligne(outil) {
    var tr = document.createElement('tr');

    var cellule = document.createElement('td');
    var titre = document.createElement('div');
    titre.textContent = outil.resume;
    cellule.appendChild(titre);

    var etiquettes = document.createElement('div');
    etiquettes.className = 'small text-muted mt-1';
    var marques = [];
    if (outil.ecriture) marques.push('modifie des données — toujours confirmé');
    if (!outil.incluse_dans_le_plan) {
      marques.push('absent de votre plan d\'abonnement');
    } else if (!outil.module_actif) {
      // Indisponible pour une autre raison, et celle-ci se corrige : le module
      // du métier est éteint dans l'onglet « Métier de la boutique ».
      marques.push('le module « ' + outil.module + ' » est éteint pour votre métier');
    }
    etiquettes.textContent = marques.join(' · ');
    if (marques.length) cellule.appendChild(etiquettes);
    tr.appendChild(cellule);

    var celluleRole = document.createElement('td');
    var select = document.createElement('select');
    select.className = 'form-select form-select-sm';
    select.dataset.outil = outil.nom;
    // Un outil hors plan, ou dont le module métier est éteint, n'est de toute
    // façon pas utilisable : le régler n'aurait aucun effet, autant le dire en
    // le désactivant.
    select.disabled = !outil.incluse_dans_le_plan || !outil.module_actif;
    roles.forEach(function (role) {
      var option = document.createElement('option');
      option.value = role.valeur;
      option.textContent = role.libelle;
      if (role.valeur === outil.role) option.selected = true;
      select.appendChild(option);
    });
    celluleRole.appendChild(select);
    tr.appendChild(celluleRole);

    return tr;
  }

  function dessiner(outils) {
    corps.innerHTML = '';
    outils.forEach(function (outil) { corps.appendChild(ligne(outil)); });
  }

  function charger() {
    if (charge) return;
    charge = true;
    fetch(API, { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.json().then(function (d) { return { s: r.status, d: d }; }); })
      .then(function (res) {
        if (res.s !== 200) {
          charge = false;
          corps.innerHTML = '';
          alerte((res.d && (res.d.message || res.d.detail)) ||
            'Droits illisibles.', 'warning');
          return;
        }
        roles = res.d.roles || [];
        dessiner(res.d.outils || []);
      })
      .catch(function () {
        charge = false;
        corps.innerHTML = '';
        alerte('Connexion impossible — réessayez.', 'warning');
      });
  }

  bouton.addEventListener('click', function () {
    var valeurs = {};
    corps.querySelectorAll('select[data-outil]').forEach(function (select) {
      if (!select.disabled) valeurs[select.dataset.outil] = select.value;
    });

    bouton.disabled = true;
    fetch(API, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ roles: valeurs })
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      bouton.disabled = false;
      if (res.s === 200) {
        dessiner(res.d.outils || []);
        alerte('Droits enregistrés. Ils s\'appliquent à la prochaine question ' +
          'posée à l\'assistant.', 'success');
      } else {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Enregistrement refusé.', 'danger');
      }
    }).catch(function () {
      bouton.disabled = false;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  });

  // Chargé à l'ouverture de l'onglet plutôt qu'au chargement de la page : la
  // plupart des visites de l'écran Paramètres ne le concernent pas.
  var onglet = document.querySelector('a[href="#assistant"][data-bs-toggle="pill"]');
  if (onglet) onglet.addEventListener('shown.bs.tab', charger);
  if (pane.classList.contains('active')) charger();
})();
