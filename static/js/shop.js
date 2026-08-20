/* ============================================================================
   Stock — Boutique : thème, panier, rayons, cartes produit.
   ============================================================================ */

const SHOP = {
  cartKey: 'stock-shop-cart',
  themeKey: 'stock-shop-theme',
  visibleCategories: 7,
};

let SHOP_SETTINGS = {};

/* ------------------------------------------------------------------ format */

function formatPrice(amount) {
  return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 }).format(Number(amount || 0)) + ' FCFA';
}

function priceLabel(product) {
  if (product.price_max && product.price_max > product.price) {
    return `${formatPrice(product.price)} <span class="range">– ${formatPrice(product.price_max)}</span>`;
  }
  return formatPrice(product.price);
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = value == null ? '' : String(value);
  return div.innerHTML;
}

function attr(value) { return escapeHtml(value).replace(/"/g, '&quot;'); }

/* ------------------------------------------------------------------ thème */

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem(SHOP.themeKey, theme); } catch (e) { }
  const icon = document.getElementById('themeIcon');
  if (icon) icon.className = theme === 'dark' ? 'bi bi-sun' : 'bi bi-moon';
}

function initTheme() {
  applyTheme(document.documentElement.getAttribute('data-theme') || 'light');
  const toggle = document.getElementById('themeToggle');
  if (!toggle) return;
  toggle.addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    applyTheme(next);
  });
}

/* ------------------------------------------------------------------ panier */

function getCart() {
  try {
    const raw = localStorage.getItem(SHOP.cartKey);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (e) { return []; }
}

function saveCart(items) {
  try { localStorage.setItem(SHOP.cartKey, JSON.stringify(items)); } catch (e) { }
  renderCartCount();
}

function cartQuantity() {
  return getCart().reduce((sum, item) => sum + Number(item.quantity || 0), 0);
}

function renderCartCount() {
  const count = cartQuantity();
  document.querySelectorAll('.badge-count').forEach((badge) => {
    const previous = Number(badge.getAttribute('data-count') || 0);
    badge.textContent = count > 0 ? count : '';
    badge.setAttribute('data-count', String(count));
    if (count > previous) {
      badge.classList.remove('pop');
      void badge.offsetWidth;   // force le redémarrage de l'animation
      badge.classList.add('pop');
    }
  });
}

function addToCart(product, quantity = 1) {
  if (product.availability === 'épuisé') {
    toast('Ce produit est épuisé', 'error');
    return false;
  }
  const cart = getCart();
  const existing = cart.find((item) => item.product_id === product.product_id);
  if (existing) {
    existing.quantity += quantity;
  } else {
    cart.push({
      product_id: product.product_id,
      name: product.name,
      price: product.price,
      image_path: product.image_path,
      availability: product.availability,
      category: product.category,
      quantity,
    });
  }
  saveCart(cart);
  toast(`« ${product.name} » ajouté au panier`);
  return true;
}

function updateCartQuantity(productId, quantity) {
  let cart = getCart();
  if (quantity <= 0) {
    cart = cart.filter((item) => item.product_id !== productId);
  } else {
    const line = cart.find((item) => item.product_id === productId);
    if (line) line.quantity = quantity;
  }
  saveCart(cart);
}

function clearCart() { saveCart([]); }

/* ------------------------------------------------------------------ toast */

function toast(message, type = 'success') {
  const stack = document.getElementById('toastStack');
  if (!stack) return;
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<i class="bi ${type === 'error' ? 'bi-exclamation-circle' : 'bi-check-circle'}"></i><span>${escapeHtml(message)}</span>`;
  stack.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateX(26px)';
    setTimeout(() => el.remove(), 320);
  }, 2800);
}

/* ------------------------------------------------------------------ rayons */

/* Icône déduite du nom du rayon: les catégories sont saisies librement
   dans la gestion de stock, aucune liste fermée ne tiendrait. */
const CATEGORY_ICONS = [
  [/smartphone|telephone|téléphone|phone|mobile/i, 'bi-phone'],
  [/tablette|tablet|ipad/i, 'bi-tablet'],
  [/ordinateur|laptop|pc|macbook/i, 'bi-laptop'],
  [/ecouteur|écouteur|casque|audio|airpod/i, 'bi-headphones'],
  [/montre|watch/i, 'bi-smartwatch'],
  [/ecran|écran|moniteur|display/i, 'bi-display'],
  [/gaming|console|jeu|manette/i, 'bi-controller'],
  [/accessoire|cable|câble|chargeur/i, 'bi-usb-plug'],
  [/imprimante|printer|scanner/i, 'bi-printer'],
  [/photo|camera|caméra|drone/i, 'bi-camera'],
  [/stockage|disque|ssd|cle usb|clé/i, 'bi-device-hdd'],
  [/reseau|réseau|routeur|wifi/i, 'bi-router'],
];

function categoryIcon(name) {
  const found = CATEGORY_ICONS.find(([pattern]) => pattern.test(name || ''));
  return found ? found[1] : 'bi-box-seam';
}

let CATEGORIES = [];

async function loadCategories() {
  try {
    const res = await fetch(appPath('/api/shop/categories'));
    const data = await res.json();
    CATEGORIES = Array.isArray(data) ? data : [];
  } catch (e) { CATEGORIES = []; }

  renderCategoryBar();
  renderCategoryPanel();
}

function renderCategoryBar() {
  const bar = document.getElementById('catBar');
  if (!bar) return;
  if (!CATEGORIES.length) { bar.innerHTML = ''; return; }

  const current = new URLSearchParams(location.search).get('category') || '';
  bar.innerHTML = CATEGORIES.slice(0, SHOP.visibleCategories).map((cat) => `
    <a href="/e-commerce/produits?category=${encodeURIComponent(cat.name)}"
       class="catbar-item" style="${cat.name === current ? 'color:var(--accent)' : ''}">
      <i class="bi ${categoryIcon(cat.name)}"></i>${escapeHtml(cat.name)}
    </a>`).join('');
}

function renderCategoryPanel() {
  const panel = document.getElementById('catPanel');
  const toggle = document.getElementById('catToggle');
  const label = document.getElementById('catToggleLabel');
  const hidden = document.getElementById('searchCategory');
  if (!panel || !toggle) return;

  const current = hidden ? hidden.value : '';
  if (current && label) label.textContent = current;

  panel.innerHTML = `
    <a href="#" data-cat=""><i class="bi bi-grid"></i>Tous les rayons</a>
    ${CATEGORIES.map((cat) => `
      <a href="#" data-cat="${attr(cat.name)}">
        <i class="bi ${categoryIcon(cat.name)}"></i>${escapeHtml(cat.name)}
        <span class="count">${cat.count}</span>
      </a>`).join('')}`;

  // Le panneau sert de sélecteur: il renseigne le champ caché du formulaire.
  panel.querySelectorAll('a[data-cat]').forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const value = link.dataset.cat || '';
      if (hidden) hidden.value = value;
      if (label) label.textContent = value || 'Tous les rayons';
      closeCategoryPanel();
    });
  });

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = panel.classList.toggle('open');
    toggle.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', String(open));
  });

  const burger = document.getElementById('burgerBtn');
  if (burger) burger.addEventListener('click', (e) => {
    e.stopPropagation();
    panel.classList.add('open');
    toggle.classList.add('open');
  });

  document.addEventListener('click', (e) => {
    if (!panel.contains(e.target) && e.target !== toggle) closeCategoryPanel();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCategoryPanel(); });
}

function closeCategoryPanel() {
  const panel = document.getElementById('catPanel');
  const toggle = document.getElementById('catToggle');
  if (panel) panel.classList.remove('open');
  if (toggle) { toggle.classList.remove('open'); toggle.setAttribute('aria-expanded', 'false'); }
}

/* ------------------------------------------------------------------ cartes */

/** Visuel de repli: aucun produit du stock n'a encore de photo. */
function emptyMedia(product) {
  return `<div class="media-empty">
      <span>GAPS·APPLE</span>
      <i class="bi ${categoryIcon(product.category)}"></i>
    </div>`;
}

function productMedia(product) {
  return product.image_path
    ? `<img src="${attr(product.image_path)}" alt="${attr(product.name)}" loading="lazy">`
    : emptyMedia(product);
}

/**
 * Badge de disponibilité en flux normal.
 * À utiliser partout où le parent n'est pas positionné: un .pcard-flag
 * (absolu) y remonterait jusqu'au coin haut-gauche de la page.
 */
function availabilityTag(availability) {
  if (availability === 'épuisé') return '<span class="stock-tag out">Épuisé</span>';
  if (availability === 'sur commande') return '<span class="stock-tag order">Sur commande</span>';
  return '<span class="stock-tag in">En stock</span>';
}

/** Alias conservé pour le panier. */
function availabilityBadge(availability) { return availabilityTag(availability); }

function availabilityFlag(availability) {
  if (availability === 'épuisé') return '<span class="pcard-flag out">Épuisé</span>';
  if (availability === 'sur commande') return '<span class="pcard-flag order">Sur commande</span>';
  return '';
}

/** Carte produit verticale, façon modèle: rayon, outils, visuel, nom, prix, panier. */
function productCard(product) {
  const soldOut = product.availability === 'épuisé';
  return `
    <article class="pcard reveal">
      <div class="pcard-top">
        <span class="pcard-cat">${escapeHtml(product.category || '')}</span>
        <div class="pcard-tools">
          <button type="button" data-wish="${product.product_id}" aria-label="Ajouter aux favoris" title="Favoris">
            <i class="bi bi-heart"></i>
          </button>
          <button type="button" data-compare="${product.product_id}" aria-label="Comparer" title="Comparer">
            <i class="bi bi-arrow-left-right"></i>
          </button>
        </div>
      </div>
      <a href="/e-commerce/produit/${product.product_id}" class="pcard-media">
        ${availabilityFlag(product.availability)}
        ${productMedia(product)}
      </a>
      <div class="pcard-body">
        <a href="/e-commerce/produit/${product.product_id}" class="pcard-name">${escapeHtml(product.name)}</a>
        <div class="pcard-foot">
          <div class="pcard-price">${priceLabel(product)}</div>
          <button type="button" class="pcard-cart" data-add="${product.product_id}" ${soldOut ? 'disabled' : ''}
            aria-label="Ajouter au panier" title="${soldOut ? 'Indisponible' : 'Ajouter au panier'}">
            <i class="bi bi-cart2"></i>
          </button>
        </div>
      </div>
    </article>`;
}

/** Carte horizontale, utilisée par « Récemment ajoutés ». */
function productCardWide(product) {
  const soldOut = product.availability === 'épuisé';
  return `
    <article class="hcard reveal">
      <a href="/e-commerce/produit/${product.product_id}" class="hcard-media">
        ${productMedia(product)}
      </a>
      <div class="hcard-body">
        <div class="hcard-top">
          <span class="pcard-cat">${escapeHtml(product.category || '')}</span>
          <div class="pcard-tools" style="flex-direction:row;gap:10px">
            <button type="button" data-wish="${product.product_id}" aria-label="Favoris"><i class="bi bi-heart"></i></button>
          </div>
        </div>
        ${availabilityTag(product.availability)}
        <a href="/e-commerce/produit/${product.product_id}" class="hcard-name">${escapeHtml(product.name)}</a>
        <div class="pcard-foot">
          <div class="pcard-price">${priceLabel(product)}</div>
          <button type="button" class="pcard-cart" data-add="${product.product_id}" ${soldOut ? 'disabled' : ''}
            aria-label="Ajouter au panier"><i class="bi bi-cart2"></i></button>
        </div>
      </div>
    </article>`;
}

/** Évite un aller-retour réseau au moment de l'ajout au panier. */
function cacheProducts(products) {
  window.__productCache = window.__productCache || {};
  (products || []).forEach((p) => { window.__productCache[p.product_id] = p; });
}

/** Un seul écouteur pour tous les boutons d'achat, présents ou à venir. */
function initCartDelegation() {
  if (window.__cartDelegationReady) return;
  window.__cartDelegationReady = true;

  document.addEventListener('click', async (e) => {
    const wish = e.target.closest('button[data-wish], button[data-compare]');
    if (wish) {
      e.preventDefault();
      toast('Fonctionnalité à venir');
      return;
    }

    const btn = e.target.closest('.pcard-cart[data-add]');
    if (!btn || btn.disabled) return;
    e.preventDefault();

    const productId = Number(btn.dataset.add);
    let product = (window.__productCache || {})[productId];
    if (!product) {
      try {
        const res = await fetch(appPath(`/api/shop/products/${productId}`));
        if (!res.ok) throw new Error();
        product = await res.json();
      } catch (err) {
        toast('Produit indisponible', 'error');
        return;
      }
    }

    if (addToCart(product, 1)) {
      btn.classList.add('added');
      btn.innerHTML = '<i class="bi bi-check2"></i>';
      setTimeout(() => {
        btn.classList.remove('added');
        btn.innerHTML = '<i class="bi bi-cart2"></i>';
      }, 1600);
    }
  });
}

/* ------------------------------------------------------------------ reveal */

function initReveal() {
  const items = document.querySelectorAll('.reveal:not(.visible)');
  if (!items.length) return;

  if (!('IntersectionObserver' in window)) {
    items.forEach((el) => el.classList.add('visible'));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    // Le décalage se calcule au déclenchement: les éléments vus plus tard
    // ne héritent pas d'un retard accumulé.
    entries.filter((entry) => entry.isIntersecting).forEach((entry, index) => {
      entry.target.style.setProperty('--delay', `${Math.min(index * 60, 360)}ms`);
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.06, rootMargin: '0px 0px -40px' });

  items.forEach((el) => observer.observe(el));
}

/* ------------------------------------------------------------------ divers */

function initToTop() {
  const btn = document.createElement('button');
  btn.className = 'to-top';
  btn.type = 'button';
  btn.setAttribute('aria-label', 'Retour en haut');
  btn.innerHTML = '<i class="bi bi-arrow-up"></i>';
  document.body.appendChild(btn);

  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  window.addEventListener('scroll', () => {
    btn.classList.toggle('show', window.scrollY > 700);
  }, { passive: true });
}

async function loadShopSettings() {
  try {
    const res = await fetch(appPath('/api/shop/settings'));
    if (!res.ok) return;
    SHOP_SETTINGS = await res.json();
  } catch (e) { return; }

  const phone = document.getElementById('footerPhone');
  if (phone) phone.textContent = SHOP_SETTINGS.shop_phone || 'Nous écrire';

  const contact = document.getElementById('footerContact');
  if (contact) {
    const rows = [];
    if (SHOP_SETTINGS.shop_phone) rows.push(`<li><a href="tel:${attr(SHOP_SETTINGS.shop_phone)}">${escapeHtml(SHOP_SETTINGS.shop_phone)}</a></li>`);
    if (SHOP_SETTINGS.shop_email) rows.push(`<li><a href="mailto:${attr(SHOP_SETTINGS.shop_email)}">${escapeHtml(SHOP_SETTINGS.shop_email)}</a></li>`);
    if (SHOP_SETTINGS.shop_address) rows.push(`<li><span style="color:var(--text-mid);font-size:.93rem">${escapeHtml(SHOP_SETTINGS.shop_address)}</span></li>`);
    if (rows.length) contact.innerHTML = rows.join('');
  }

  const wa = document.getElementById('waRail');
  const number = String(SHOP_SETTINGS.shop_whatsapp || '').replace(/\D/g, '');
  if (wa && number) {
    wa.href = `https://wa.me/${number}`;
    wa.style.display = '';
  }
}

function initNewsletter() {
  const form = document.getElementById('newsletterForm');
  if (!form) return;
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    // Pas encore de collecte côté serveur: on le dit plutôt que de faire semblant.
    toast('Inscription bientôt disponible — écrivez-nous en attendant');
    form.reset();
  });
}

/* ------------------------------------------------------------------ init */

document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  renderCartCount();
  initCartDelegation();
  initReveal();
  initToTop();
  initNewsletter();
  loadCategories();
  loadShopSettings();

  const year = document.getElementById('year');
  if (year) year.textContent = new Date().getFullYear();
});
