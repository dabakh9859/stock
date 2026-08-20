/*
  Mon profil — son propre compte.

  Deux formulaires indépendants, et c'est délibéré : corriger une adresse et
  changer un mot de passe n'ont ni les mêmes conséquences ni le même risque
  d'erreur. Les fondre en un seul « Enregistrer » ferait ressaisir le mot de
  passe actuel pour corriger une faute de frappe dans un nom.

  Le serveur reste l'autorité : `PUT /api/auth/me` ne lit ni le rôle ni le nom
  de connexion, quoi qu'on lui envoie. Ce qui suit n'est qu'une commodité.
*/

(function () {
  'use strict';

  const ROLES = { admin: 'Administrateur', manager: 'Gestionnaire', user: 'Utilisateur' };

  let profilCharge = null;

  const $ = (id) => document.getElementById(id);

  function initiales(nom, identifiant) {
    const source = (nom || identifiant || '').trim();
    if (!source) return '–';
    const mots = source.split(/\s+/);
    const brut = mots.length > 1 ? mots[0][0] + mots[1][0] : source.slice(0, 2);
    return brut.toUpperCase();
  }

  function dateLisible(valeur) {
    if (!valeur) return 'Jamais';
    const d = new Date(valeur);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('fr-FR', {
      day: 'numeric', month: 'long', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function messageErreur(err, repli) {
    // Le détail de FastAPI est parfois une liste (erreurs de validation
    // Pydantic) : on ne veut pas afficher « [object Object] » à la gérante.
    const detail = err && err.response && err.response.data && err.response.data.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length && detail[0].msg) return detail[0].msg;
    return repli;
  }

  function peupler(profil) {
    profilCharge = profil;
    $('profileInitials').textContent = initiales(profil.full_name, profil.username);
    $('profileName').textContent = profil.full_name || profil.username || 'Utilisateur';
    $('profileUsername').textContent = '@' + (profil.username || '');
    $('profileRole').textContent = ROLES[profil.role] || profil.role || 'Compte';
    $('profileCreated').textContent = dateLisible(profil.created_at);
    $('profileLastLogin').textContent = dateLisible(profil.last_login);

    $('fullName').value = profil.full_name || '';
    $('email').value = profil.email || '';
    $('usernameRO').value = profil.username || '';
    $('roleRO').value = ROLES[profil.role] || profil.role || '';
  }

  async function charger() {
    try {
      const reponse = await api.get('/api/auth/me');
      peupler(reponse.data);
    } catch (err) {
      showAlert(messageErreur(err, 'Impossible de charger votre profil'), 'danger');
    }
  }

  // --- Informations personnelles -------------------------------------------

  async function enregistrerProfil(e) {
    e.preventDefault();
    const bouton = $('profileSubmit');
    const nom = $('fullName').value.trim();
    const email = $('email').value.trim();

    if (!email) {
      showAlert('L’adresse email est obligatoire', 'warning');
      $('email').focus();
      return;
    }

    bouton.disabled = true;
    try {
      const reponse = await api.put('/api/auth/me', { full_name: nom, email: email });
      peupler(reponse.data);
      // La barre supérieure a été rendue par le serveur avec l'ancien nom :
      // on la remet d'accord sans recharger la page.
      const cible = document.getElementById('username');
      if (cible) cible.textContent = reponse.data.full_name || reponse.data.username;
      showAlert('Profil mis à jour', 'success');
    } catch (err) {
      showAlert(messageErreur(err, 'Enregistrement impossible'), 'danger');
    } finally {
      bouton.disabled = false;
    }
  }

  function annuler() {
    if (profilCharge) peupler(profilCharge);
  }

  // --- Mot de passe ---------------------------------------------------------

  function verifierRegles() {
    const actuel = $('currentPassword').value;
    const nouveau = $('newPassword').value;
    const confirme = $('confirmPassword').value;

    const etats = {
      length: nouveau.length >= 8,
      different: nouveau.length > 0 && nouveau !== actuel,
      match: confirme.length > 0 && nouveau === confirme
    };

    document.querySelectorAll('.profile-rules li').forEach((li) => {
      const ok = etats[li.dataset.rule];
      li.setAttribute('data-ok', String(ok));
      const icone = li.querySelector('i');
      if (icone) icone.className = ok ? 'bi bi-check-circle-fill' : 'bi bi-circle';
    });

    return etats;
  }

  async function changerMotDePasse(e) {
    e.preventDefault();
    const bouton = $('passwordSubmit');
    const etats = verifierRegles();

    if (!$('currentPassword').value) {
      showAlert('Saisissez votre mot de passe actuel', 'warning');
      $('currentPassword').focus();
      return;
    }
    if (!etats.length) {
      showAlert('Le nouveau mot de passe doit compter au moins 8 caractères', 'warning');
      $('newPassword').focus();
      return;
    }
    if (!etats.match) {
      showAlert('Les deux saisies ne concordent pas', 'warning');
      $('confirmPassword').focus();
      return;
    }

    bouton.disabled = true;
    try {
      await api.post('/api/auth/me/password', {
        current_password: $('currentPassword').value,
        new_password: $('newPassword').value
      });
      // Le serveur a posé un cookie neuf : la session courante survit. Les
      // champs sont vidés pour ne pas laisser le mot de passe à l'écran.
      $('passwordForm').reset();
      verifierRegles();
      showAlert('Mot de passe modifié. Vos autres sessions ont été fermées.', 'success');
    } catch (err) {
      showAlert(messageErreur(err, 'Changement impossible'), 'danger');
    } finally {
      bouton.disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    charger();
    $('profileForm').addEventListener('submit', enregistrerProfil);
    $('profileReset').addEventListener('click', annuler);
    $('passwordForm').addEventListener('submit', changerMotDePasse);
    ['currentPassword', 'newPassword', 'confirmPassword'].forEach((id) => {
      $(id).addEventListener('input', verifierRegles);
    });
  });

  // Crochet du rafraîchissement automatique de http.js après une écriture.
  // Sans lui, l'écran ne recharge rien — mais le mot de passe n'a pas à être
  // rechargé, et le profil est déjà remis à jour par la réponse du PUT.
  window.rafraichirDonnees = charger;
})();
