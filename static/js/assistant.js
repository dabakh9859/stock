/*
  Widget de discussion avec l'assistant IA — autonome et réutilisable.

  Inclusion : <script src=".../assistant.js" defer
                      data-endpoint="/api/assistant/site"
                      data-title="Conseiller Stock"
                      data-intro="Bonjour ! ..."></script>

  Il injecte ses propres styles (clair/sombre via prefers-color-scheme ou
  l'attribut data-theme du document), garde l'historique en sessionStorage,
  et affiche proprement le mode « assistant non activé » (HTTP 503).
*/

(function () {
  var script = document.currentScript;
  if (!script) return;

  var ENDPOINT = script.dataset.endpoint || '/api/assistant/chat';
  var TITLE = script.dataset.title || 'Assistant';
  var INTRO = script.dataset.intro ||
    'Bonjour ! Posez-moi votre question, je réponds en quelques secondes.';
  var STORE_KEY = 'stock-assistant:' + ENDPOINT;

  var css = [
    '.sa-root{--sa-bg:#ffffff;--sa-fg:#141419;--sa-muted:#6b6b76;--sa-border:#e4e4ea;',
    '--sa-me:#141419;--sa-me-fg:#fafafa;--sa-accent:#1f8a4c;',
    'font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;z-index:2147483000;position:fixed}',
    '@media (prefers-color-scheme: dark){.sa-root:not([data-sa-light]){--sa-bg:#101014;--sa-fg:#fafafa;',
    '--sa-muted:#a1a1aa;--sa-border:#2a2a31;--sa-me:#fafafa;--sa-me-fg:#141419}}',
    '[data-theme="dark"] .sa-root{--sa-bg:#101014;--sa-fg:#fafafa;--sa-muted:#a1a1aa;',
    '--sa-border:#2a2a31;--sa-me:#fafafa;--sa-me-fg:#141419}',
    '.sa-btn{position:fixed;right:1.25rem;bottom:1.25rem;width:3.25rem;height:3.25rem;border-radius:50%;',
    'border:none;background:var(--sa-me);color:var(--sa-me-fg);font-size:1.375rem;cursor:pointer;',
    'box-shadow:0 10px 30px -8px rgb(0 0 0/.45);transition:transform .12s ease;display:grid;place-items:center}',
    '.sa-btn:active{transform:scale(.94)}',
    '.sa-panel{position:fixed;right:1.25rem;bottom:5.25rem;width:min(24rem,calc(100vw - 2rem));',
    'height:min(32rem,calc(100vh - 7rem));background:var(--sa-bg);color:var(--sa-fg);',
    'border:1px solid var(--sa-border);border-radius:1rem;box-shadow:0 30px 70px -20px rgb(0 0 0/.4);',
    'display:none;flex-direction:column;overflow:hidden}',
    '.sa-panel.on{display:flex}',
    '.sa-head{display:flex;justify-content:space-between;align-items:center;gap:.5rem;',
    'padding:.875rem 1rem;border-bottom:1px solid var(--sa-border);font-weight:650;font-size:.9375rem}',
    '.sa-head small{display:block;font-weight:400;font-size:.75rem;color:var(--sa-muted)}',
    '.sa-close{border:none;background:none;color:var(--sa-muted);font-size:1.125rem;cursor:pointer;padding:.25rem}',
    '.sa-msgs{flex:1;overflow-y:auto;padding:1rem;display:flex;flex-direction:column;gap:.625rem}',
    '.sa-m{max-width:88%;padding:.5625rem .8125rem;border-radius:.875rem;font-size:.875rem;line-height:1.55;white-space:pre-wrap}',
    '.sa-m.user{align-self:flex-end;background:var(--sa-me);color:var(--sa-me-fg);border-bottom-right-radius:.25rem}',
    '.sa-m.bot{align-self:flex-start;background:transparent;border:1px solid var(--sa-border);border-bottom-left-radius:.25rem}',
    '.sa-m.err{align-self:center;color:var(--sa-muted);font-size:.8125rem;text-align:center}',
    '.sa-wait{align-self:flex-start;color:var(--sa-muted);font-size:.8125rem;padding:.25rem .5rem}',
    '.sa-act{align-self:stretch;border:1px solid var(--sa-border);border-radius:.875rem;padding:.75rem .875rem;font-size:.8438rem;line-height:1.5}',
    '.sa-act strong{display:block;margin-bottom:.375rem;font-size:.6875rem;text-transform:uppercase;letter-spacing:.05em;color:var(--sa-muted)}',
    '.sa-act-btns{display:flex;gap:.5rem;margin-top:.75rem}',
    '.sa-act-btns button{flex:1;font:inherit;font-size:.8125rem;font-weight:600;padding:.4375rem .75rem;border-radius:.5rem;cursor:pointer;border:1px solid var(--sa-border);background:transparent;color:var(--sa-fg)}',
    '.sa-act-btns button.sa-act-ok{background:var(--sa-me);color:var(--sa-me-fg);border-color:transparent}',
    '.sa-act-btns button:disabled{opacity:.5;cursor:default}',
    '.sa-form{display:flex;gap:.5rem;padding:.75rem;border-top:1px solid var(--sa-border)}',
    '.sa-input{flex:1;font:inherit;font-size:.875rem;color:var(--sa-fg);background:transparent;',
    'border:1px solid var(--sa-border);border-radius:.625rem;padding:.5625rem .75rem;outline:none}',
    '.sa-input:focus{border-color:var(--sa-muted)}',
    '.sa-send{border:none;background:var(--sa-me);color:var(--sa-me-fg);border-radius:.625rem;',
    'padding:.5625rem .875rem;font:inherit;font-size:.875rem;font-weight:600;cursor:pointer}',
    '.sa-send:disabled{opacity:.5;cursor:default}',
    '@media (prefers-reduced-motion: reduce){.sa-btn{transition:none}}'
  ].join('');

  var style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  var root = document.createElement('div');
  root.className = 'sa-root';
  root.innerHTML =
    '<button type="button" class="sa-btn" aria-label="Ouvrir l\'assistant">💬</button>' +
    '<div class="sa-panel" role="dialog" aria-label="' + TITLE + '">' +
    '  <div class="sa-head"><span>' + TITLE + '<small>Répond en quelques secondes</small></span>' +
    '  <button type="button" class="sa-close" aria-label="Fermer">✕</button></div>' +
    '  <div class="sa-msgs"></div>' +
    '  <form class="sa-form"><input class="sa-input" maxlength="2000" ' +
    'placeholder="Écrivez votre question…" aria-label="Votre question">' +
    '  <button class="sa-send" type="submit">Envoyer</button></form>' +
    '</div>';
  document.body.appendChild(root);

  var btn = root.querySelector('.sa-btn');
  var panel = root.querySelector('.sa-panel');
  var msgs = root.querySelector('.sa-msgs');
  var form = root.querySelector('.sa-form');
  var input = root.querySelector('.sa-input');
  var send = root.querySelector('.sa-send');

  var history = [];
  try { history = JSON.parse(sessionStorage.getItem(STORE_KEY) || '[]'); } catch (e) {}

  function persist() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify(history.slice(-16))); } catch (e) {}
  }

  function bubble(role, text) {
    var el = document.createElement('div');
    el.className = 'sa-m ' + role;
    el.textContent = text;
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;
    return el;
  }

  function render() {
    msgs.innerHTML = '';
    bubble('bot', INTRO);
    history.forEach(function (m) { bubble(m.role === 'user' ? 'user' : 'bot', m.content); });
  }

  var CONFIRM = ENDPOINT.replace(/\/chat$/, '/confirmer');

  /* Carte de confirmation. C'est le seul chemin par lequel une écriture ou un
     envoi se produit : l'assistant ne fait que proposer, rien ne part sans ce
     clic. La carte n'est volontairement pas conservée dans l'historique — au
     rouvrir, son jeton serait périmé et le bouton mensonger. */
  function carteAction(action) {
    var el = document.createElement('div');
    el.className = 'sa-m sa-act';

    var titre = document.createElement('strong');
    titre.textContent = 'À confirmer';
    var texte = document.createElement('div');
    texte.textContent = action.resume || '';

    var btns = document.createElement('div');
    btns.className = 'sa-act-btns';
    var ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'sa-act-ok';
    ok.textContent = 'Confirmer';
    var non = document.createElement('button');
    non.type = 'button';
    non.textContent = 'Annuler';
    btns.appendChild(ok);
    btns.appendChild(non);

    el.appendChild(titre);
    el.appendChild(texte);
    el.appendChild(btns);
    msgs.appendChild(el);
    msgs.scrollTop = msgs.scrollHeight;

    function decider(decision) {
      ok.disabled = non.disabled = true;
      fetch(CONFIRM, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: action.token, decision: decision })
      }).then(function (r) {
        return r.json().then(function (d) { return { status: r.status, data: d }; });
      }).then(function (res) {
        btns.remove();
        var texte = (res.data && (res.data.reply || res.data.message)) ||
          'Action indisponible.';
        if (res.status === 200 && res.data.reply) {
          history.push({ role: 'assistant', content: texte });
          persist();
          bubble('bot', texte);
        } else {
          bubble('err', texte);
        }
      }).catch(function () {
        ok.disabled = non.disabled = false;
        bubble('err', 'Connexion impossible — réessayez.');
      });
    }

    ok.addEventListener('click', function () { decider('confirmer'); });
    non.addEventListener('click', function () { decider('annuler'); });
  }

  btn.addEventListener('click', function () {
    panel.classList.toggle('on');
    if (panel.classList.contains('on')) { render(); input.focus(); }
  });
  root.querySelector('.sa-close').addEventListener('click', function () {
    panel.classList.remove('on');
  });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text || send.disabled) return;
    input.value = '';
    history.push({ role: 'user', content: text });
    persist();
    bubble('user', text);

    var wait = document.createElement('div');
    wait.className = 'sa-wait';
    wait.textContent = 'L\'assistant écrit…';
    msgs.appendChild(wait);
    msgs.scrollTop = msgs.scrollHeight;
    send.disabled = true;

    fetch(ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: history.slice(-12) })
    }).then(function (r) {
      return r.json().then(function (data) { return { status: r.status, data: data }; });
    }).then(function (res) {
      wait.remove();
      send.disabled = false;
      if (res.status === 200 && (res.data.reply || res.data.action)) {
        if (res.data.reply) {
          history.push({ role: 'assistant', content: res.data.reply });
          persist();
          bubble('bot', res.data.reply);
        }
        if (res.data.action) carteAction(res.data.action);
      } else {
        var msg = (res.data && (res.data.message || res.data.detail)) ||
          'L\'assistant est indisponible pour le moment — réessayez plus tard.';
        bubble('err', msg);
      }
    }).catch(function () {
      wait.remove();
      send.disabled = false;
      bubble('err', 'Connexion impossible — vérifiez votre réseau et réessayez.');
    });
    input.focus();
  });
})();
