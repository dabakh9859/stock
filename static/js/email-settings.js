/*
  Réglages d'envoi d'e-mails — onglet « E-mail » de l'écran Paramètres.

  Même parti pris que les autres onglets récents : autonome, fetch brut.

  Le mot de passe ne fait qu'un aller. Le serveur renvoie un témoin à sa place ;
  le champ le reçoit tel quel et le renvoie inchangé si on n'y touche pas. Le
  navigateur ne voit donc jamais le vrai mot de passe, même en rouvrant l'écran.
*/

(function () {
  var pane = document.getElementById('email');
  if (!pane) return;

  var API = (window.URL_PREFIX || '') + '/api/email';
  var zoneAlerte = document.getElementById('emailAlerte');
  var etat = document.getElementById('emailEtat');
  var champs = {
    hote: document.getElementById('emailHote'),
    port: document.getElementById('emailPort'),
    securite: document.getElementById('emailSecurite'),
    utilisateur: document.getElementById('emailUtilisateur'),
    mot_de_passe: document.getElementById('emailMotDePasse'),
    expediteur: document.getElementById('emailExpediteur'),
    nom_expediteur: document.getElementById('emailNom')
  };
  var boutonEnregistrer = document.getElementById('emailEnregistrer');
  var boutonTester = document.getElementById('emailTester');
  var champEssai = document.getElementById('emailEssai');
  var charge = false;

  function alerte(texte, type) {
    zoneAlerte.innerHTML = '<div class="alert alert-' + type +
      ' py-2 small">' + texte + '</div>';
    if (type === 'success') {
      setTimeout(function () { zoneAlerte.innerHTML = ''; }, 8000);
    }
  }

  function marquerEtat(configure) {
    etat.className = 'badge ' + (configure ? 'text-bg-success' : 'text-bg-secondary');
    etat.textContent = configure ? 'configuré' : 'non configuré';
  }

  function remplir(donnees) {
    var c = donnees.config || {};
    (donnees.securites || []).forEach(function (s) {
      if (champs.securite.querySelector('option[value="' + s.valeur + '"]')) return;
      var o = document.createElement('option');
      o.value = s.valeur;
      o.textContent = s.libelle;
      champs.securite.appendChild(o);
    });
    champs.hote.value = c.hote || '';
    champs.port.value = c.port || 587;
    champs.securite.value = c.securite || 'starttls';
    champs.utilisateur.value = c.utilisateur || '';
    champs.mot_de_passe.value = c.mot_de_passe || '';
    champs.expediteur.value = c.expediteur || '';
    champs.nom_expediteur.value = c.nom_expediteur || '';
    marquerEtat(!!c.configure);
    if (!champEssai.value) champEssai.value = c.expediteur || '';
  }

  function charger() {
    if (charge) return;
    charge = true;
    fetch(API + '/config', { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        return r.json().then(function (d) { return { s: r.status, d: d }; });
      })
      .then(function (res) {
        if (res.s !== 200) {
          charge = false;
          alerte((res.d && (res.d.message || res.d.detail)) ||
            'Réglages illisibles.', 'warning');
          return;
        }
        remplir(res.d);
      })
      .catch(function () {
        charge = false;
        alerte('Connexion impossible — réessayez.', 'warning');
      });
  }

  boutonEnregistrer.addEventListener('click', function () {
    boutonEnregistrer.disabled = true;
    fetch(API + '/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hote: champs.hote.value.trim(),
        port: parseInt(champs.port.value, 10) || 587,
        securite: champs.securite.value,
        utilisateur: champs.utilisateur.value.trim(),
        mot_de_passe: champs.mot_de_passe.value,
        expediteur: champs.expediteur.value.trim(),
        nom_expediteur: champs.nom_expediteur.value.trim()
      })
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      boutonEnregistrer.disabled = false;
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'Enregistrement refusé.', 'danger');
        return;
      }
      remplir({ config: res.d.config });
      alerte(res.d.message, 'success');
    }).catch(function () {
      boutonEnregistrer.disabled = false;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  });

  boutonTester.addEventListener('click', function () {
    var destinataire = champEssai.value.trim();
    if (!destinataire) {
      alerte('Indiquez une adresse pour recevoir l\'essai.', 'warning');
      return;
    }
    boutonTester.disabled = true;
    var initial = boutonTester.innerHTML;
    boutonTester.textContent = 'Envoi…';
    fetch(API + '/essai', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destinataire: destinataire })
    }).then(function (r) {
      return r.json().then(function (d) { return { s: r.status, d: d }; });
    }).then(function (res) {
      boutonTester.disabled = false;
      boutonTester.innerHTML = initial;
      if (res.s !== 200) {
        alerte((res.d && (res.d.message || res.d.detail)) ||
          'L\'essai a échoué.', 'danger');
        return;
      }
      alerte(res.d.message, 'success');
    }).catch(function () {
      boutonTester.disabled = false;
      boutonTester.innerHTML = initial;
      alerte('Connexion impossible — réessayez.', 'danger');
    });
  });

  var onglet = document.querySelector('a[href="#email"][data-bs-toggle="pill"]');
  if (onglet) onglet.addEventListener('shown.bs.tab', charger);
  if (pane.classList.contains('active')) charger();
})();
