/*
  Écran des dates limites.

  Deux listes séparées, et c'est volontaire : un lot périmé se retire du rayon,
  un lot qui approche se solde ou se met en avant. Les mélanger obligerait le
  commerçant à relire les dates une par une.
*/

(function () {
  var API = (window.URL_PREFIX || '') + '/api/lots/alertes';
  var fenetre = document.getElementById('fenetre');
  var zoneAlerte = document.getElementById('alerte');

  function texte(parent, balise, classe, contenu) {
    var el = document.createElement(balise);
    if (classe) el.className = classe;
    if (contenu != null) el.textContent = contenu;
    parent.appendChild(el);
    return el;
  }

  function jourFr(iso) {
    if (!iso) return '—';
    var p = iso.split('-');
    return p.length === 3 ? p[2] + '/' + p[1] + '/' + p[0] : iso;
  }

  function delai(jours) {
    if (jours === null || jours === undefined) return '—';
    var n = Math.abs(jours);
    if (n === 0) return "aujourd'hui";
    return n + (n > 1 ? ' jours' : ' jour');
  }

  function ligne(lot) {
    var tr = document.createElement('tr');
    /*
      Les noms de champs sont ceux de GET /api/lots/alertes (`_fiche` dans
      lots.py) : `lot_number`, `expiry_date`, `quantity`. L'outil de l'assistant
      renomme les mêmes données pour le modèle (`lot`, `date_limite`,
      `quantite_recue`) — s'y fier ici affichait « undefined » et des colonnes
      vides.
    */
    texte(tr, 'td', null, lot.produit || '—');
    texte(tr, 'td', 'font-monospace small', lot.lot_number || '—');
    texte(tr, 'td', null, jourFr(lot.expiry_date));
    texte(tr, 'td', null, delai(lot.jours_restants));
    texte(tr, 'td', 'text-end',
      lot.quantite_lisible ||
      ((lot.quantity != null ? lot.quantity : '—') + ' ' + (lot.unite || '')));
    return tr;
  }

  function remplir(corpsId, compteurId, lots, videMessage) {
    var corps = document.getElementById(corpsId);
    corps.innerHTML = '';
    document.getElementById(compteurId).textContent = lots.length;
    if (!lots.length) {
      var tr = document.createElement('tr');
      var td = texte(tr, 'td', 'text-muted', videMessage);
      td.colSpan = 5;
      corps.appendChild(tr);
      return;
    }
    lots.forEach(function (lot) { corps.appendChild(ligne(lot)); });
  }

  function charger() {
    zoneAlerte.innerHTML = '';
    fetch(API + '?jours=' + encodeURIComponent(fenetre.value), {
      headers: { 'Accept': 'application/json' }
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      if (res.s !== 200) {
        zoneAlerte.innerHTML = '<div class="alert alert-warning py-2 small">' +
          ((res.d && (res.d.message || res.d.detail)) ||
            'Liste indisponible.') + '</div>';
        return;
      }
      remplir('corpsPerimes', 'compteurPerimes', res.d.perimes || [],
        'Rien de périmé — bonne nouvelle.');
      remplir('corpsBientot', 'compteurBientot', res.d.bientot || [],
        'Aucune date limite dans cette fenêtre.');
    }).catch(function () {
      zoneAlerte.innerHTML = '<div class="alert alert-warning py-2 small">' +
        'Connexion impossible — réessayez.</div>';
    });
  }

  fenetre.addEventListener('change', charger);
  charger();
})();
