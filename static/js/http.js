// Lightweight fetch-based HTTP client to replace Axios
(function () {
  // Préfixe de montage de l'application (vide en production, "/v2" en recette).
  // Défini par base.html avant le chargement de ce script.
  const prefix = (typeof window !== 'undefined' && window.URL_PREFIX) || '';

  const baseURL = (() => {
    try {
      const origin = window.location.origin || '';
      return origin + prefix;
    } catch {
      return prefix;
    }
  })();

  // No protocol detection needed - use current protocol

  function buildURL(url, params) {
    const hasProto = /^https?:\/\//i.test(url);

    // For localhost, always use HTTP
    let finalUrl;
    if (hasProto) {
      // If URL already has protocol, check if it's localhost and force HTTP
      const urlObj = new URL(url);
      if (urlObj.hostname === 'localhost' || urlObj.hostname === '127.0.0.1') {
        urlObj.protocol = 'http:';
        finalUrl = urlObj.toString();
      } else {
        finalUrl = url;
      }
    } else {
      // Construct URL from baseURL + path
      const loc = window.location;
      if (loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') {
        finalUrl = `http://${loc.hostname}:${loc.port || '8000'}${prefix}${url}`;
      } else {
        finalUrl = baseURL + url;
      }
    }

    const u = new URL(finalUrl);

    if (params && typeof params === 'object') {
      Object.entries(params).forEach(([k, v]) => {
        if (v === undefined || v === null) return;
        if (Array.isArray(v)) {
          v.forEach(val => u.searchParams.append(k, String(val)));
        } else {
          u.searchParams.set(k, String(v));
        }
      });
    }

    const finalResult = u.toString();
    return finalResult;
  }

  function toHeadersObject(headers) {
    const obj = {};
    try {
      for (const [k, v] of headers.entries()) {
        obj[k.toLowerCase()] = v;
      }
    } catch { }
    return obj;
  }

  /*
    Compteur de requêtes en vol.

    Tous les écrans passent par ce client : c'est le seul endroit d'où l'on
    puisse signaler « l'application travaille » sans toucher à chaque page.
    Deux évènements sont émis sur `window`, la coquille s'y abonne pour animer
    la barre de progression.
  */
  let inFlight = 0;

  function signal(delta) {
    const wasIdle = inFlight === 0;
    inFlight = Math.max(0, inFlight + delta);
    try {
      if (delta > 0 && wasIdle) window.dispatchEvent(new CustomEvent('http:start'));
      else if (inFlight === 0) window.dispatchEvent(new CustomEvent('http:idle'));
    } catch { }
  }

  async function request(config) {
    // `responseType` est retiré de `rest` : c'est une option de ce client, pas
    // de fetch, et elle n'a rien à faire dans les options passées au navigateur.
    const { url, method = 'GET', params, data, body, headers = {}, responseType, ...rest } = config || {};
    if (!url) throw new Error('http: url is required');

    const fullUrl = buildURL(url, params);
    const isFormData = (typeof FormData !== 'undefined') && (data instanceof FormData || body instanceof FormData);

    const fetchOpts = {
      method,
      credentials: 'include',
      headers: Object.assign({}, headers, isFormData ? {} : { 'Content-Type': 'application/json' }),
      body: undefined,
      ...rest,
    };

    const payload = data !== undefined ? data : body;
    if (payload !== undefined && method.toUpperCase() !== 'GET' && method.toUpperCase() !== 'HEAD') {
      fetchOpts.body = isFormData ? payload : JSON.stringify(payload);
    }

    // Le compteur est décrémenté dans tous les cas — y compris en erreur —
    // sinon une requête en échec laisserait la barre tourner indéfiniment.
    signal(+1);
    let resp;
    try {
      resp = await fetch(fullUrl, fetchOpts);
    } finally {
      signal(-1);
    }

    let respData;
    const ct = resp.headers.get('content-type') || '';
    try {
      // Un appelant qui réclame explicitement un blob l'obtient toujours : le
      // reniflage par content-type ci-dessous ne peut pas connaître tous les
      // types binaires, et lire du binaire en texte le corrompt silencieusement.
      if (responseType === 'blob') respData = await resp.blob();
      else if (ct.includes('application/json')) respData = await resp.json();
      else if (ct.includes('application/zip') || ct.includes('application/octet-stream') || ct.includes('application/x-sqlite') || ct.includes('application/pdf')) respData = await resp.blob();
      else respData = await resp.text();
    } catch { respData = null; }

    // Sur une erreur, le serveur renvoie du JSON même quand l'appelant attendait
    // un binaire. Le relire en objet garde `error.response.data.detail`
    // exploitable par les écrans, au lieu d'un blob opaque.
    if (!resp.ok && typeof Blob !== 'undefined' && respData instanceof Blob) {
      try { respData = JSON.parse(await respData.text()); } catch { }
    }

    const responseLike = {
      data: respData,
      status: resp.status,
      statusText: resp.statusText,
      headers: toHeadersObject(resp.headers),
      config,
      url: fullUrl,
    };

    if (!resp.ok) {
      if (resp.status === 401) {
        // Ne pas appeler logout automatiquement pour éviter d'effacer le cookie
        // sur des 401 transitoires; redirection simple vers /login
        try { window.location.href = appPath('/login'); } catch { }
      }
      const err = new Error('HTTP error ' + resp.status);
      err.response = responseLike;
      throw err;
    }

    /*
      Rafraîchissement automatique après une écriture.

      Le défaut était systémique : une modification partait au serveur, la
      modale se mettait à jour, mais la liste derrière gardait l'ancienne
      valeur. La gérante devait recharger la page pour voir son propre
      changement — et croyait souvent que rien ne s'était passé.

      Plutôt que de corriger chaque bouton un par un, on branche ici : toute
      écriture réussie (POST, PUT, PATCH, DELETE) déclenche le rechargement des
      données de l'écran courant. Chaque page déclare sa fonction de
      chargement dans `window.rafraichirDonnees` ; celles qui n'en déclarent
      pas ne changent pas de comportement.

      Trois précautions :

      · Le rappel est différé et fusionné (350 ms). Un formulaire qui envoie
        plusieurs requêtes d'affilée ne relance qu'un seul rechargement.
      · Les écritures marquées `sansRafraichissement` sont ignorées — les
        sauvegardes de brouillon, par exemple, partent en continu et
        rechargeraient la liste sans arrêt.
      · Une erreur du rechargement ne remonte pas : l'écriture, elle, a
        réussi, et l'utilisateur ne doit pas voir une erreur pour un simple
        rafraîchissement manqué.
    */
    const methode = String(config.method || 'GET').toUpperCase();
    if (methode !== 'GET' && methode !== 'HEAD' && !config.sansRafraichissement) {
      planifierRafraichissement();
    }

    return responseLike;
  }

  let minuteurRafraichissement = null;

  function planifierRafraichissement() {
    if (typeof window.rafraichirDonnees !== 'function') return;
    clearTimeout(minuteurRafraichissement);
    minuteurRafraichissement = setTimeout(() => {
      try {
        const r = window.rafraichirDonnees();
        if (r && typeof r.catch === 'function') r.catch(() => { });
      } catch (e) {
        console.debug('[rafraichissement] ignoré :', e);
      }
    }, 350);
  }

  /** Exécute une écriture sans déclencher le rechargement de l'écran. */
  window.sansRafraichissement = function (config) {
    return request({ ...(config || {}), sansRafraichissement: true });
  };

  // Convenience methods
  request.get = (url, config = {}) => request({ ...(config || {}), url, method: 'GET' });
  request.delete = (url, config = {}) => request({ ...(config || {}), url, method: 'DELETE' });
  request.post = (url, data, config = {}) => request({ ...(config || {}), url, method: 'POST', data });
  request.put = (url, data, config = {}) => request({ ...(config || {}), url, method: 'PUT', data });
  request.patch = (url, data, config = {}) => request({ ...(config || {}), url, method: 'PATCH', data });

  // Expose as api and as axios shim
  window.api = request;
  window.axios = request;
})();
