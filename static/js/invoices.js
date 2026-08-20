// Supprimer les backdrops orphelins et réinitialiser les styles body si aucun modal n'est visible
function cleanupModalBackdrops() {
    try {
        const anyShown = document.querySelector('.modal.show');
        if (!anyShown) {
            document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            document.body.classList.remove('modal-open');
            try { document.body.style.removeProperty('overflow'); } catch (e) { }
            try { document.body.style.removeProperty('padding-right'); } catch (e) { }
        }
    } catch (e) { /* ignore */ }
}
// Gestion des factures
let currentPage = 1;
// Mémorisation du dernier fichier de signature
let lastSignatureFile = null;
// Réduire le nombre de factures par page pour limiter le travail côté navigateur
const itemsPerPage = 20;
let invoices = [];
let filteredInvoices = [];
let clients = [];
let products = [];
let categories = []; // Catégories pour les produits échangés
let productVariantsByProductId = new Map(); // product_id -> [{variant_id, imei_serial, barcode, is_sold}]
let invoiceItems = [];
let exchangeItems = []; // Produits échangés (sortants)
// Tri courant (pour la liste principale)
let currentSort = { by: 'created_at', dir: 'desc' };
// Quantités d'origine issues du devis (si disponibles) par product_id
let quoteQtyByProductId = new Map();
// Helpers to track used IMEIs across rows
function _normalizeCode(v) { return String(v || '').trim().toLowerCase(); }
function getUsedImeis(excludeItemId) {
    const set = new Set();
    (invoiceItems || []).forEach(i => {
        if (excludeItemId && i.id === excludeItemId) return;
        (i.scannedImeis || []).forEach(imei => set.add(_normalizeCode(imei)));
    });
    return set;
}
function rowHasImei(row, imei) {
    const n = _normalizeCode(imei);
    return (row.scannedImeis || []).some(x => _normalizeCode(x) === n);
}

// Calcule le stock disponible pour un produit
function computeAvailableStock(product) {
    try {
        let variants = productVariantsByProductId.get(Number(product.product_id)) || [];
        if (!variants.length && Array.isArray(product.variants)) {
            variants = product.variants;
        }
        if (variants.length > 0) {
            return variants.reduce((acc, v) => {
                if (v && v.is_sold) return acc;
                const q = v && v.quantity;
                if (q == null || q === undefined) return acc + 1;
                const numQ = Number(q);
                return acc + (Number.isFinite(numQ) && numQ > 0 ? numQ : 1);
            }, 0);
        }
        return Number(product.quantity || 0);
    } catch (e) {
        return 0;
    }
}

async function resetInvoicePaymentsFromDetail(invoiceId) {
    try {
        if (!invoiceId) return;
        if (!await confirmDialog('Réinitialiser tous les paiements de cette facture ?', { variant: 'danger', confirmLabel: 'Supprimer' })) return;
        await axios.post(`/api/invoices/${invoiceId}/payments/reset/`);
        showSuccess('Paiements réinitialisés');
        await loadInvoiceDetail(invoiceId);
    } catch (error) {
        console.error('Erreur réinitialisation paiements:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la réinitialisation des paiements');
    }
}

// Initialisation
document.addEventListener('DOMContentLoaded', function () {
    // Inject sort arrows like products/quotations if header present
    try {
        const thMap = [
            ['number', 'Numéro'], ['client', 'Client'], ['date', 'Date'], ['due', 'Échéance'], ['total', 'Montant'], ['status', 'Statut']
        ];
        if (typeof window.buildSortHeader !== 'function') {
            window.buildSortHeader = function (label, byKey) {
                const isActive = (currentSort.by === byKey);
                const ascActive = isActive && currentSort.dir === 'asc';
                const descActive = isActive && currentSort.dir === 'desc';
                return `
                    <div class="d-flex align-items-center gap-2 sort-header">
                        <span>${label}</span>
                        <div class="sort-btn-group" role="group" aria-label="Trier ${label}">
                            <button type="button" class="sort-btn ${ascActive ? 'active' : ''}" data-sort-by="${byKey}" data-sort-dir="asc" title="Trier par ${label} (croissant)">
                                <i class="bi bi-chevron-up"></i>
                            </button>
                            <button type="button" class="sort-btn ${descActive ? 'active' : ''}" data-sort-by="${byKey}" data-sort-dir="desc" title="Trier par ${label} (décroissant)">
                                <i class="bi bi-chevron-down"></i>
                            </button>
                        </div>
                    </div>
                `;
            };
        }
        thMap.forEach(([k, label]) => {
            const th = document.querySelector(`table thead th[data-col="${k}"]`);
            if (th) th.innerHTML = buildSortHeader(label, k);
        });
        document.querySelectorAll('table thead [data-sort-by]')?.forEach(btn => {
            btn.addEventListener('click', () => {
                const by = btn.getAttribute('data-sort-by');
                const dir = btn.getAttribute('data-sort-dir');
                currentSort = { by, dir };
                loadInvoices();
            });
        });
    } catch (e) { /* ignore */ }
    // Utiliser la nouvelle logique d'authentification basée sur cookies
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    // Masquer la carte "Chiffre d'Affaires" pour les non-admin
    try {
        const hasAuthManager = !!window.authManager;
        if (hasAuthManager) {
            const user = window.authManager.userData;
            if (user && user.role !== 'admin') {
                const revenueCard = document.querySelector('.card-revenue');
                if (revenueCard) revenueCard.style.display = 'none';
            }
        }
    } catch (e) { }

    // Gestion des brouillons pour les factures
    const invoiceModal = document.getElementById('invoiceModal');
    if (invoiceModal) {
        invoiceModal.addEventListener('draftSave', (e) => {
            if (e.detail.formType === 'Facture') {
                console.log('[Invoices] Sauvegarde du brouillon, articles:', invoiceItems.length);
                e.detail.formData.invoiceItems = JSON.parse(JSON.stringify(invoiceItems));
                e.detail.formData.exchangeItems = JSON.parse(JSON.stringify(exchangeItems));
            }
        });

        invoiceModal.addEventListener('draftLoad', async (e) => {
            if (e.detail.formType === 'Facture') {
                console.log('[Invoices] Chargement du brouillon, données reçues:', e.detail.data);
                
                invoiceItems = e.detail.data.invoiceItems || [];
                exchangeItems = e.detail.data.exchangeItems || [];
                
                // Rendu immédiat des articles
                if (typeof updateInvoiceItemsDisplay === 'function') {
                    updateInvoiceItemsDisplay();
                }
                if (typeof renderExchangeItems === 'function') {
                    renderExchangeItems();
                }
                if (typeof calculateTotals === 'function') {
                    calculateTotals();
                }

                // Attendre que les données de base soient chargées
                try {
                    const promises = [];
                    if (!Array.isArray(products) || products.length === 0) {
                        if (typeof loadProducts === 'function') promises.push(loadProducts());
                    }
                    if (!Array.isArray(categories) || categories.length === 0) {
                        if (typeof loadCategories === 'function') promises.push(loadCategories());
                    }
                    if (!Array.isArray(clients) || clients.length === 0) {
                        if (typeof loadClients === 'function') promises.push(loadClients());
                    }
                    if (promises.length > 0) await Promise.all(promises);
                } catch (err) { console.warn('[Invoices] Erreur attente données:', err); }

                // Ré-afficher après chargement
                if (typeof updateInvoiceItemsDisplay === 'function') updateInvoiceItemsDisplay();
                if (typeof renderExchangeItems === 'function') renderExchangeItems();
                if (typeof calculateTotals === 'function') calculateTotals();
                
                // Restauration du client (champ spécial)
                if (e.detail.data.clientSearch) {
                    const clientInput = document.getElementById('clientSearch');
                    if (clientInput) {
                        clientInput.value = e.detail.data.clientSearch;
                        clientInput.dispatchEvent(new Event('input', { bubbles: true }));
                        
                        // ID client caché : essayer les deux IDs possibles
                        const hiddenClientId = document.getElementById('clientSelect') || document.getElementById('invoiceClientId');
                        if (hiddenClientId && e.detail.data.clientSelect) {
                            hiddenClientId.value = e.detail.data.clientSelect;
                            hiddenClientId.dispatchEvent(new Event('change', { bubbles: true }));
                        } else if (hiddenClientId && e.detail.data.invoiceClientId) {
                            hiddenClientId.value = e.detail.data.invoiceClientId;
                            hiddenClientId.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                }
                
                // Champs de date
                if (e.detail.data.invoiceDate) {
                    const dateInput = document.getElementById('invoiceDate');
                    if (dateInput) {
                        dateInput.value = e.detail.data.invoiceDate;
                        dateInput.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
                if (e.detail.data.dueDate) {
                    const dueInput = document.getElementById('dueDate');
                    if (dueInput) {
                        dueInput.value = e.detail.data.dueDate;
                        dueInput.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }

                // Garantie
                const warrantySwitch = document.getElementById('hasWarrantySwitch');
                if (warrantySwitch) {
                    const shouldBeChecked = !!e.detail.data.hasWarrantySwitch;
                    console.log('[Invoices] Restauration garantie:', shouldBeChecked);
                    warrantySwitch.checked = shouldBeChecked;
                    // Déclencher le changement pour afficher/masquer les options de garantie
                    if (typeof handleWarrantyChange === 'function') {
                        handleWarrantyChange();
                    } else {
                        warrantySwitch.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    
                    // Restaurer la durée de garantie si présente
                    if (shouldBeChecked && e.detail.data.warrantyDuration) {
                        const durationRadio = document.querySelector(`input[name="warrantyDuration"][value="${e.detail.data.warrantyDuration}"]`);
                        if (durationRadio) {
                            durationRadio.checked = true;
                            durationRadio.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }
                }

                // Autres champs (Notes, etc.)
                const fieldsToRestore = ['externalNotes', 'internalNotes', 'invoiceNumber', 'invoicePaymentMethod', 'taxRateInput'];
                fieldsToRestore.forEach(id => {
                    const el = document.getElementById(id);
                    if (el && e.detail.data[id] !== undefined) {
                        el.value = e.detail.data[id];
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                });

                console.log('[Invoices] Brouillon appliqué, items:', invoiceItems.length);
            }
        });
    }

    const quotationModal = document.getElementById('quotationModal');
    if (quotationModal) {
        quotationModal.addEventListener('draftSave', (e) => {
            if (e.detail.formType === 'Devis') {
                console.log('[Quotations] Sauvegarde du brouillon, articles:', quotationItems.length);
                if (typeof quotationItems !== 'undefined') {
                    e.detail.formData.quotationItems = JSON.parse(JSON.stringify(quotationItems));
                }
            }
        });

        quotationModal.addEventListener('draftLoad', (e) => {
            if (e.detail.formType === 'Devis') {
                console.log('[Quotations] Chargement du brouillon, données reçues:', e.detail.data);
                if (typeof quotationItems !== 'undefined') {
                    quotationItems = e.detail.data.quotationItems || [];
                    
                    // Restauration du client (champ spécial)
                    if (e.detail.data.clientSearch) {
                        const clientInput = document.getElementById('clientSearch');
                        if (clientInput) {
                            clientInput.value = e.detail.data.clientSearch;
                            clientInput.dispatchEvent(new Event('input', { bubbles: true }));
                            
                            const hiddenClientId = document.getElementById('clientSelect');
                            if (hiddenClientId && e.detail.data.clientSelect) {
                                hiddenClientId.value = e.detail.data.clientSelect;
                                hiddenClientId.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }
                    }

                    if (typeof updateQuotationItemsDisplay === 'function') updateQuotationItemsDisplay();
                    if (typeof calculateTotals === 'function') calculateTotals();
                }
            }
        });
    }

    // Initialiser immédiatement sans délai pour un chargement instantané
    loadInvoices();
    try { loadStats(); } catch (e) { }
    // Lazy: clients/products/categories chargés à l'usage
    setupEventListeners();
    setDefaultDates();
    // Charger les catégories pour les factures d'échange
    try { loadCategories(); } catch (e) { }
    // Si on vient d'une conversion devis -> facture, ouvrir le formulaire pré-rempli
    try {
        const raw = sessionStorage.getItem('prefill_invoice_from_quotation');
        if (raw) {
            const data = JSON.parse(raw);
            sessionStorage.removeItem('prefill_invoice_from_quotation');
            if (!Array.isArray(products) || !products.length) { try { loadProducts(); } catch (e) { } }
            if (!Array.isArray(categories) || !categories.length) { try { loadCategories(); } catch (e) { } }
            if (!Array.isArray(clients) || !clients.length) { try { loadClients(); } catch (e) { } }
            Promise.all([waitForProductsLoaded(), waitForCategoriesLoaded(), waitForClientsLoaded()])
                .then(() => preloadPrefilledInvoiceFromQuotation(data));
        }
        // Si on vient de convertir un devis via API, ouvrir directement l'édition de la facture créée
        const editId = sessionStorage.getItem('edit_invoice_id');
        if (editId) {
            sessionStorage.removeItem('edit_invoice_id');
            // Précharger la facture dans le formulaire d'édition et ouvrir le modal
            preloadInvoiceIntoForm(Number(editId));
        }
        // Si on arrive depuis un devis pour ouvrir une facture spécifique, pré-remplir le champ recherche
        const q = sessionStorage.getItem('invoiceSearchQuery');
        if (q) {
            sessionStorage.removeItem('invoiceSearchQuery');
            try {
                const input = document.getElementById('invoiceSearch');
                if (input) { input.value = q; filterInvoices(); }
            } catch (e) { }
        }
        // Ouvrir directement une facture quand on arrive d'un autre écran.
        //
        // Deux sources sont acceptées :
        //   · un paramètre d'URL — /invoices?view=123 (aussi ?highlight= et
        //     ?invoice=, deux formes déjà employées ailleurs dans l'application) ;
        //   · la clé de session `open_invoice_detail_id`, posée par les écrans
        //     Créances et Produits.
        //
        // Le paramètre d'URL est la forme à privilégier : il survit à une copie
        // du lien, à un nouvel onglet et à un partage, ce que `sessionStorage`
        // ne fait pas. La clé de session reste lue pour ne pas casser les écrans
        // qui l'utilisent déjà.
        let openId = null;
        try {
            const params = new URLSearchParams(window.location.search);
            openId = params.get('view') || params.get('highlight') || params.get('invoice');
        } catch (e) { }

        if (!openId) {
            openId = sessionStorage.getItem('open_invoice_detail_id');
        }
        sessionStorage.removeItem('open_invoice_detail_id');

        if (openId && /^\d+$/.test(String(openId).trim())) {
            // Laisser la liste s'afficher d'abord : la modale se superpose à une
            // page déjà dessinée plutôt qu'à un écran vide.
            setTimeout(() => {
                try { viewInvoice(Number(openId)); } catch (e) { }
            }, 200);
        }
    } catch (e) { }

    // Nettoyage global des backdrops Bootstrap après fermeture d'un modal
    try {
        document.addEventListener('hidden.bs.modal', () => {
            cleanupModalBackdrops();
        });
    } catch (e) { /* ignore */ }
});

function setupEventListeners() {
    // Filtres
    // Dès qu'un filtre change, recharger côté serveur
    document.getElementById('statusFilter')?.addEventListener('change', () => { currentPage = 1; loadInvoices(); });
    document.getElementById('clientFilter')?.addEventListener('input', debounce(() => { currentPage = 1; loadInvoices(); }, 300));
    document.getElementById('dateFromFilter')?.addEventListener('change', () => { currentPage = 1; loadInvoices(); });
    document.getElementById('dateToFilter')?.addEventListener('change', () => { currentPage = 1; loadInvoices(); });
    document.getElementById('invoiceSearch')?.addEventListener('input', debounce(() => { currentPage = 1; loadInvoices(); }, 250));

    // TVA controls (bind once at load; also rebind inside openInvoiceModal for safety)
    setupTaxControls();
    // Catch-all: recalc totals on any input change within the invoice form
    // Skip events from exchangeTotalAmount to avoid overwriting user input
    const form = document.getElementById('invoiceForm');
    if (form) {
        form.addEventListener('input', (e) => {
            if (e.target && e.target.id === 'exchangeTotalAmount') return;
            calculateTotals();
        });
        form.addEventListener('change', (e) => {
            if (e.target && e.target.id === 'exchangeTotalAmount') return;
            calculateTotals();
        });
    }

    // Barcode input: Enter or short pause auto-add
    const barcodeInput = document.getElementById('invoiceBarcodeInput');
    if (barcodeInput) {
        barcodeInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addItemByBarcode();
            }
        });
        let scanTimer;
        barcodeInput.addEventListener('input', () => {
            clearTimeout(scanTimer);
            const v = (barcodeInput.value || '').trim();
            if (v.length >= 3) {
                scanTimer = setTimeout(() => addItemByBarcode(), 250);
            }
        });
    }

    // Champ de saisie manuelle code-barres (sans auto-search)
    const manualBarcodeInput = document.getElementById('invoiceManualBarcodeInput');
    if (manualBarcodeInput) {
        manualBarcodeInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addItemByManualBarcode();
            }
        });
    }
    // Quick client modal
    document.getElementById('openQuickClientBtn')?.addEventListener('click', () => {
        new bootstrap.Modal(document.getElementById('clientQuickModal')).show();
        setTimeout(() => document.getElementById('qcName')?.focus(), 150);
    });
    document.getElementById('saveQuickClientBtn')?.addEventListener('click', saveQuickClient);

    // Initialiser la recherche client côté serveur (si non initialisée)
    try { setupClientSearch(); } catch (e) { }

    // Payment now switch
    const paySwitch = document.getElementById('paymentNowSwitch');
    if (paySwitch) {
        paySwitch.addEventListener('change', () => {
            const v = paySwitch.checked;
            const paymentFields = document.getElementById('paymentNowFields');
            const amountInput = document.getElementById('paymentNowAmount');
            const maxAmountSpan = document.getElementById('maxPaymentAmount');

            if (paymentFields) paymentFields.style.display = v ? 'flex' : 'none';

            if (v && amountInput) {
                // Calculer le montant maximum en tenant compte des paiements existants
                const totalAmount = Math.round(parseFloat(document.getElementById('invoiceForm').dataset.total || '0'));
                const invoiceId = document.getElementById('invoiceId').value;

                if (invoiceId) {
                    // En édition : récupérer le montant restant depuis les infos affichées
                    const paymentInfo = document.getElementById('existingPaymentInfo');
                    if (paymentInfo && paymentInfo.style.display !== 'none') {
                        // Extraire le montant restant du résumé affiché
                        const paymentSummary = document.getElementById('paymentStatusSummary');
                        if (paymentSummary) {
                            // Extraire le montant restant en cherchant tous les chiffres (y compris les espaces)
                            const remainingMatch = paymentSummary.innerHTML.match(/Restant:[^\d]*([\d\s,\.]+)\s*(?:FCFA|€|\$)?/i);
                            if (remainingMatch) {
                                // Nettoyer le montant en supprimant les espaces et en remplaçant la virgule par un point
                                const cleanAmount = remainingMatch[1].replace(/\s/g, '').replace(',', '.');
                                const remainingAmount = parseFloat(cleanAmount);

                                if (!isNaN(remainingAmount)) {
                                    amountInput.value = remainingAmount.toString();
                                    amountInput.max = remainingAmount.toString();
                                    if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(remainingAmount);
                                    return;
                                }
                            }
                        }
                    }
                }

                // Par défaut (nouvelle facture ou pas de paiements existants)
                amountInput.value = totalAmount.toString();
                amountInput.max = totalAmount.toString();
                if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(totalAmount);
            }
        });
    }

    // Charger les méthodes de paiement (depuis SQLite via API) et peupler les selects
    populatePaymentMethodSelects();

    // Délégation pour recherche produit dans le tableau des articles (document-level)
    document.addEventListener('input', debounce(async (e) => {
        const target = e.target;
        if (!(target && target.classList && target.classList.contains('product-search-input'))) return;
        const query = String(target.value || '').trim();
        const row = target.closest('tr');
        if (!row) return;
        const suggestBox = row.querySelector('.product-suggestions');
        if (!suggestBox) return;
        if (query.length < 2) { suggestBox.classList.add('d-none'); suggestBox.innerHTML = ''; return; }
        try {
            const res = await axios.get('/api/products/', { params: { search: query, limit: 20 } });
            let list = res.data?.items || res.data || [];
            // Ne plus masquer les produits épuisés, les afficher avec indication
            // Conserver la dernière liste pour la sélection (fallback si products[] non chargé)
            try { window._latestProductResults = list; } catch (e) { }
            suggestBox.innerHTML = (list.length ? list : [{ __empty: true }]).map(p => {
                if (p.__empty) {
                    return '<div class="list-group-item text-muted small">Aucun produit</div>';
                }
                const variants = Array.isArray(p.variants) ? p.variants : [];
                const hasVariants = variants.length > 0;
                const available = hasVariants
                    ? variants.reduce((acc, v) => {
                        if (v && v.is_sold) return acc;
                        const q = v && v.quantity;
                        if (q == null || q === undefined) return acc + 1;
                        const numQ = Number(q);
                        return acc + (Number.isFinite(numQ) && numQ > 0 ? numQ : 1);
                    }, 0)
                    : Number(p.quantity || 0);
                const stockBadge = `<span class="badge ${available > 0 ? 'bg-success' : 'bg-danger'} ms-2">${available > 0 ? ('Stock: ' + available) : 'Rupture'}</span>`;
                // Signale un produit issu d'un échange, pour éviter de le vendre par erreur.
                const exchangeBadge = (p.source === 'exchange')
                    ? '<span class="badge bg-warning text-dark ms-1"><i class="bi bi-arrow-left-right"></i> Échange</span>' : '';
                const sub = [p.category, p.brand, p.model].filter(Boolean).join(' • ');
                const isOutOfStock = available === 0;
                return `
                <div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center ${isOutOfStock ? 'text-muted' : ''}" data-product-id="${p.product_id}">
                    <div class="d-flex align-items-center gap-2 me-2">
                        ${(() => {
                        if (!p.image_path) return '';
                        const imgPath = String(p.image_path).trim();
                        if (!imgPath) return '';
                        let imageUrl = imgPath.startsWith('/') ? imgPath : '/' + imgPath;
                        if (!imageUrl.startsWith('/static')) {
                            imageUrl = '/static/' + imgPath.replace(/^\/+/, '');
                        }
                        return `<img src="${imageUrl}" alt="${escapeHtml(p.name || '')}"
                                 style="width: 40px; height: 40px; object-fit: cover; border-radius: 4px;"
                                 onerror="this.style.display='none';">`;
                    })()}
                        <div>
                            <div class="fw-semibold d-flex align-items-center flex-wrap">${escapeHtml(p.name || '')} ${stockBadge} ${exchangeBadge}</div>
                            <div class="text-muted small">${[p.barcode ? 'Code: ' + escapeHtml(p.barcode) : '', sub ? escapeHtml(sub) : ''].filter(Boolean).join(' • ')}</div>
                        </div>
                    </div>
                    <div class="text-nowrap ms-3">
                        <div class="fw-semibold">${formatCurrency(p.price)}</div>
                        ${p.wholesale_price ? `<div class="text-muted small">Gros: ${formatCurrency(p.wholesale_price)}</div>` : ''}
                    </div>
                </div>`;
            }).join('');
            anchorSuggestionBox(target, suggestBox);
            suggestBox.classList.remove('d-none');
        } catch (err) {
            suggestBox.classList.add('d-none'); suggestBox.innerHTML = '';
        }
    }, 250));

    // Les suggestions sont ancrées à l'écran: tout défilement les décrocherait.
    document.addEventListener('scroll', closeAnchoredSuggestions, true);
    window.addEventListener('resize', closeAnchoredSuggestions);

    // Recherche produit pour les produits échangés (délégation document-level)
    document.addEventListener('input', debounce(async (e) => {
        const target = e.target;
        if (!(target && target.classList && target.classList.contains('exchange-product-search-input'))) return;
        const query = String(target.value || '').trim();
        const exchangeItemId = target.getAttribute('data-exchange-item-id');
        if (!exchangeItemId) return;
        const container = target.closest('.position-relative');
        if (!container) return;
        const suggestBox = container.querySelector('.exchange-product-suggestions');
        if (!suggestBox) return;
        if (query.length < 2) { suggestBox.classList.add('d-none'); suggestBox.innerHTML = ''; return; }
        try {
            const res = await axios.get('/api/products/', { params: { search: query, limit: 20 } });
            let list = res.data?.items || res.data || [];
            suggestBox.innerHTML = (list.length ? list : [{ __empty: true }]).map(p => {
                if (p.__empty) {
                    return '<div class="list-group-item text-muted small">Aucun produit</div>';
                }
                const variants = Array.isArray(p.variants) ? p.variants : [];
                const hasVariants = variants.length > 0;
                const available = hasVariants
                    ? variants.reduce((acc, v) => {
                        if (v && v.is_sold) return acc;
                        const q = v && v.quantity;
                        if (q == null || q === undefined) return acc + 1;
                        const numQ = Number(q);
                        return acc + (Number.isFinite(numQ) && numQ > 0 ? numQ : 1);
                    }, 0)
                    : Number(p.quantity || 0);
                const stockBadge = `<span class="badge ${available > 0 ? 'bg-success' : 'bg-secondary'} ms-2">Stock: ${available}</span>`;
                const exchangeBadge = (p.source === 'exchange')
                    ? '<span class="badge bg-warning text-dark ms-1"><i class="bi bi-arrow-left-right"></i> Échange</span>' : '';
                const sub = [p.category, p.brand, p.model].filter(Boolean).join(' • ');
                return `
                <div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center" data-exchange-product-id="${p.product_id}">
                    <div class="d-flex align-items-center gap-2 me-2">
                        ${(() => {
                        if (!p.image_path) return '';
                        const imgPath = String(p.image_path).trim();
                        if (!imgPath) return '';
                        let imageUrl = imgPath.startsWith('/') ? imgPath : '/' + imgPath;
                        if (!imageUrl.startsWith('/static')) {
                            imageUrl = '/static/' + imgPath.replace(/^\/+/, '');
                        }
                        return `<img src="${imageUrl}" alt="${escapeHtml(p.name || '')}"
                                 style="width: 40px; height: 40px; object-fit: cover; border-radius: 4px;"
                                 onerror="this.style.display='none';">`;
                    })()}
                        <div>
                            <div class="fw-semibold d-flex align-items-center flex-wrap">${escapeHtml(p.name || '')} ${stockBadge} ${exchangeBadge}</div>
                            <div class="text-muted small">${[p.barcode ? 'Code: ' + escapeHtml(p.barcode) : '', sub ? escapeHtml(sub) : ''].filter(Boolean).join(' • ')}</div>
                        </div>
                    </div>
                    <div class="text-nowrap ms-3">
                        <div class="fw-semibold">${formatCurrency(p.price)}</div>
                    </div>
                </div>`;
            }).join('');
            anchorSuggestionBox(target, suggestBox);
            suggestBox.classList.remove('d-none');
        } catch (err) {
            suggestBox.classList.add('d-none'); suggestBox.innerHTML = '';
        }
    }, 250));

    // Sélection d'un produit échangé depuis les suggestions.
    //
    // `pointerdown` et non `mousedown` : au doigt, sur téléphone ou tablette,
    // l'événement souris n'est pas émis de façon fiable avant que la perte de
    // focus ne referme la liste — le produit n'était alors jamais retenu, et
    // la recherche paraissait ne pas fonctionner. `pointerdown` couvre la
    // souris, le doigt et le stylet d'un seul écouteur.
    document.addEventListener('pointerdown', (e) => {
        const exchangeItem = e.target.closest('.list-group-item[data-exchange-product-id]');
        if (exchangeItem) {
            e.preventDefault();
            e.stopPropagation();
            const productId = exchangeItem.getAttribute('data-exchange-product-id');
            if (!productId) return;
            const suggestBox = exchangeItem.closest('.exchange-product-suggestions');
            if (!suggestBox) return;
            const container = suggestBox.closest('.position-relative');
            if (!container) return;
            const input = container.querySelector('.exchange-product-search-input');
            if (!input) return;
            const exchangeItemId = input.getAttribute('data-exchange-item-id');
            if (!exchangeItemId) return;

            // Trouver le produit dans le cache local OU extraire les données du DOM de la suggestion
            let product = products.find(p => String(p.product_id) === String(productId));
            if (!product) {
                // Produit pas dans le cache local (>1000 produits) : extraire du DOM
                try {
                    const nameEl = exchangeItem.querySelector('.fw-semibold');
                    const priceEl = exchangeItem.querySelector('.text-nowrap .fw-semibold');
                    product = {
                        product_id: parseInt(productId),
                        name: nameEl ? nameEl.textContent.replace(/Stock:.*$/, '').trim() : 'Produit #' + productId,
                        price: priceEl ? parseFloat(priceEl.textContent.replace(/[^\d]/g, '')) || 0 : 0,
                        barcode: null,
                        category: null
                    };
                } catch (err) {
                    console.warn('[Exchange] Impossible d\'extraire le produit du DOM:', err);
                }
            }
            if (product) {
                const item = exchangeItems.find(i => i.id === Number(exchangeItemId));
                if (item) {
                    item.product_id = product.product_id;
                    item.product_name = product.name;
                    // Inférer category_id depuis la catégorie du produit (pour afficher IMEI/Série si requis)
                    try {
                        const catName = product.category ? String(product.category).trim() : '';
                        if (catName && Array.isArray(categories) && categories.length) {
                            const cat = categories.find(c => String(c.name || '').trim().toLowerCase() === catName.toLowerCase());
                            if (cat) item.category_id = Number(cat.category_id || cat.id);
                        }
                    } catch (e) { }
                    // Pré-remplir code-barres si existant
                    try {
                        if (product.barcode) item.barcode = String(product.barcode);
                    } catch (e) { }

                    // Aligner le champ de recherche sur le produit choisi AVANT
                    // de redessiner la ligne.
                    //
                    // Le champ porte un `onchange` qui recopie sa valeur dans
                    // `product_name`. Le re-rendu le remplace par un nouvel
                    // élément, mais l'ancien — détaché — émet quand même son
                    // `change` en perdant le focus, avec le texte tapé. Il
                    // écrasait alors le nom du produit : l'écran affichait
                    // « TABLETTE AMAZON » (nouvel élément) tandis que la
                    // facture imprimée portait « az » (valeur enregistrée).
                    // En recopiant le nom ici, ce `change` tardif devient sans
                    // effet puisqu'il transporte désormais la bonne valeur.
                    input.value = product.name || '';

                    renderExchangeItems();
                }
            }
            suggestBox.classList.add('d-none');
            suggestBox.innerHTML = '';
            return;
        }

        const item = e.target.closest('.list-group-item[data-product-id]');
        if (!item) return;
        // Empêcher le comportement par défaut et la propagation
        e.preventDefault();
        e.stopPropagation();

        const productId = item.getAttribute('data-product-id');
        if (!productId) return;

        // Trouver le conteneur de la ligne: on cherche l'ancêtre portant data-item-id
        let row = item.closest('[data-item-id]');
        if (!row) {
            const suggestBox = item.closest('.product-suggestions');
            if (suggestBox) {
                row = suggestBox.closest('[data-item-id]');
            }
        }

        const idAttr = row?.getAttribute('data-item-id');
        console.log('[ProductSelect] click on product', productId, 'row:', row, 'idAttr:', idAttr);

        if (productId && idAttr) {
            selectProduct(Number(idAttr), productId);
            const box = row.querySelector('.product-suggestions');
            if (box) { box.innerHTML = ''; box.classList.add('d-none'); }
            const input = row.querySelector('.product-search-input');
            if (input) input.value = '';
        }
    }, true); // Utiliser la phase de capture pour s'exécuter en premier

    // Cacher le dropdown si clic en dehors
    document.addEventListener('click', (e) => {
        // Fermer les suggestions au clic en dehors (sauf si on clique sur une suggestion)
        if (e.target.closest('.list-group-item[data-product-id]')) return;
        if (e.target.closest('.list-group-item[data-exchange-product-id]')) return;
        document.querySelectorAll('.product-suggestions, .exchange-product-suggestions').forEach(box => {
            // Vérifier si le clic est dans le conteneur parent (input-group ou le box lui-même)
            const container = box.closest('.position-relative');
            if (container && container.contains(e.target)) return;
            if (box.contains(e.target)) return;
            box.classList.add('d-none');
        });
    });

    document.addEventListener('blur', (e) => {
        // e.target peut ne pas être un élément DOM (ex: window, document)
        if (!e.target || typeof e.target.closest !== 'function') return;
        // Fermer les suggestions au clic en dehors (sauf si on clique sur une suggestion)
        if (e.target.closest('.list-group-item[data-product-id]')) return;
        if (e.target.closest('.list-group-item[data-exchange-product-id]')) return;
        document.querySelectorAll('.product-suggestions, .exchange-product-suggestions').forEach(box => {
            // Vérifier si le clic est dans le conteneur parent (input-group ou le box lui-même)
            const container = box.closest('.position-relative');
            if (container && container.contains(e.target)) return;
            if (box.contains(e.target)) return;
            box.classList.add('d-none');
        });
    }, true);

    // Cacher le dropdown si clic en dehors
    document.addEventListener('click', (e) => {
        // Ne pas fermer si le clic est sur un élément de suggestion (déjà géré ci-dessus)
        if (e.target.closest('.list-group-item[data-product-id]')) return;
        document.querySelectorAll('.product-suggestions').forEach(box => {
            // Vérifier si le clic est dans le conteneur parent (input-group ou le box lui-même)
            const container = box.closest('.position-relative');
            if (container && container.contains(e.target)) return;
            if (box.contains(e.target)) return;
            box.classList.add('d-none');
        });
    });
}

function setupTaxControls() {
    const taxSwitch = document.getElementById('showTaxSwitch');
    const taxRateInput = document.getElementById('taxRateInput');
    if (taxSwitch) {
        taxSwitch.removeEventListener('change', calculateTotals);
        taxSwitch.addEventListener('change', calculateTotals);
    }
    if (taxRateInput) {
        const handler = () => calculateTotals();
        taxRateInput.removeEventListener('input', handler);
        taxRateInput.addEventListener('input', handler);
        taxRateInput.removeEventListener('change', handler);
        taxRateInput.addEventListener('change', handler);
        taxRateInput.removeEventListener('keyup', handler);
        taxRateInput.addEventListener('keyup', handler);
        taxRateInput.removeEventListener('blur', handler);
        taxRateInput.addEventListener('blur', handler);
    }
}

function setupWarrantyControls() {
    const warrantySwitch = document.getElementById('hasWarrantySwitch');
    if (warrantySwitch) {
        warrantySwitch.removeEventListener('change', handleWarrantyChange);
        warrantySwitch.addEventListener('change', handleWarrantyChange);
    }
}

function handleWarrantyChange() {
    const warrantySwitch = document.getElementById('hasWarrantySwitch');
    const warrantyOptions = document.getElementById('warrantyOptions');
    const warrantyInfo = document.getElementById('warrantyInfo');
    const isEnabled = warrantySwitch?.checked || false;

    if (warrantyOptions) {
        warrantyOptions.style.display = isEnabled ? 'block' : 'none';
    }
    if (warrantyInfo) {
        warrantyInfo.style.display = isEnabled ? 'block' : 'none';
    }
}

function setDefaultDates() {
    const today = new Date().toISOString().split('T')[0];
    const invoiceDate = document.getElementById('invoiceDate');
    const paymentDate = document.getElementById('paymentDate');

    if (invoiceDate) invoiceDate.value = today;
    if (paymentDate) paymentDate.value = today;

    // Date d'échéance par défaut = même date que la création
    const dueDateInput = document.getElementById('dueDate');
    if (dueDateInput) dueDateInput.value = today;
}

// Attendre le chargement des produits (variants inclus)
function waitForProductsLoaded(maxWaitMs = 2000) {
    return new Promise(resolve => {
        const start = Date.now();
        const tick = () => {
            if ((products && products.length) || (productVariantsByProductId && productVariantsByProductId.size)) {
                resolve();
                return;
            }
            if (Date.now() - start > maxWaitMs) { resolve(); return; }
            setTimeout(tick, 50);
        };
        tick();
    });
}

// Attendre le chargement des clients (pour préremplissage depuis devis)
function waitForClientsLoaded(maxWaitMs = 2000) {
    return new Promise(resolve => {
        const start = Date.now();
        const tick = () => {
            if (Array.isArray(clients) && clients.length) { resolve(); return; }
            if (Date.now() - start > maxWaitMs) { resolve(); return; }
            setTimeout(tick, 50);
        };
        tick();
    });
}

// Attendre le chargement des catégories
function waitForCategoriesLoaded(maxWaitMs = 2000) {
    return new Promise(resolve => {
        const start = Date.now();
        const tick = () => {
            if (categories && categories.length) {
                resolve();
            } else if (Date.now() - start < maxWaitMs) {
                requestAnimationFrame(tick);
            } else {
                resolve();
            }
        };
        tick();
    });
}

// Charger les statistiques
async function loadStats() {
    try {
        const { data: stats } = await axios.get('/api/invoices/stats/dashboard/');
        document.getElementById('totalInvoices').textContent = stats.total_invoices || 0;
        document.getElementById('paidInvoices').textContent = stats.paid_invoices || 0;
        document.getElementById('pendingInvoices').textContent = stats.pending_invoices || 0;
        document.getElementById('totalRevenue').textContent = formatCurrency(stats.total_revenue || 0);
    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Charger la liste des factures (server-side pagination)
async function loadInvoices(page) {
    try {
        if (page) {
            currentPage = page;
        }
        showLoading();
        // Construire les paramètres côté serveur
        const statusFilter = document.getElementById('statusFilter')?.value || '';
        const clientFilter = (document.getElementById('clientFilter')?.value || '').trim();
        const dateFromFilter = document.getElementById('dateFromFilter')?.value || '';
        const dateToFilter = document.getElementById('dateToFilter')?.value || '';
        const search = (document.getElementById('invoiceSearch')?.value || '').trim();

        const params = {
            page: currentPage,
            page_size: itemsPerPage,
            sort_by: currentSort.by,
            sort_dir: currentSort.dir,
            _ts: Date.now() // cache-buster to avoid stale responses après suppression
        };
        if (statusFilter) params.status_filter = statusFilter;
        if (clientFilter) params.client_search = clientFilter;
        if (dateFromFilter) params.start_date = dateFromFilter;
        if (dateToFilter) params.end_date = dateToFilter;
        if (search) params.search = search;

        // Appel direct sans couche de retry/timeout supplémentaire
        const response = await axios.get('/api/invoices/paginated/', { params });

        const payload = response?.data || { invoices: [], total: 0, page: 1, pages: 1 };
        invoices = Array.isArray(payload.invoices) ? payload.invoices : [];
        filteredInvoices = [...invoices];

        // Si une recherche d'ID/numéro a été demandée depuis la page devis, l'appliquer maintenant
        try {
            const pending = sessionStorage.getItem('invoiceSearchQuery');
            if (pending) {
                const input = document.getElementById('invoiceSearch');
                if (input) {
                    input.value = pending;
                    filterInvoices();
                    // garder ou nettoyer selon préférence; on nettoie pour ne pas réappliquer à chaque refresh
                    sessionStorage.removeItem('invoiceSearchQuery');
                }
            }
        } catch (e) { }

        displayInvoices();
        updatePagination(payload.total || filteredInvoices.length);
    } catch (error) {
        console.error('Erreur lors du chargement des factures:', error);

        // Afficher un message d'erreur dans le tableau
        const tbody = document.getElementById('invoicesTableBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-danger py-4">
                        <i class="bi bi-exclamation-triangle fs-1 d-block mb-2"></i>
                        Erreur lors du chargement des factures
                    </td>
                </tr>
            `;
        }

        if (typeof showAlert === 'function') {
            showAlert('Erreur lors du chargement des factures', 'danger');
        }
    }
}

// Charger les clients
async function loadClients() {
    try {
        const { data } = await axios.get('/api/clients/');
        clients = data.items || data || [];
        populateClientSelect();
        setupClientSearch();
    } catch (error) {
        console.error('Erreur lors du chargement des clients:', error);
    }
}

// Charger les produits
async function loadProducts() {
    try {
        const { data } = await axios.get('/api/products/', { params: { limit: 1000 } });
        products = data.items || data || [];
        // Prefetch variants per product for quick selection
        await Promise.all(products.map(async (p) => {
            try {
                const variants = (p.variants && p.variants.length) ? p.variants : [];
                productVariantsByProductId.set(p.product_id, variants);
            } catch (e) { /* ignore */ }
        }));
        // Build quick lookup by barcode
        window._productByBarcode = new Map();
        products.forEach(p => { if (p.barcode) window._productByBarcode.set(String(p.barcode).trim(), p); });
        // Add variants barcodes too
        products.forEach(p => {
            (productVariantsByProductId.get(p.product_id) || []).forEach(v => {
                if (v.barcode) window._productByBarcode.set(String(v.barcode).trim(), { ...p, _variant: v });
                if (v.imei_serial) window._productByBarcode.set(String(v.imei_serial).trim(), { ...p, _variant: v });
            });
        });
    } catch (error) {
        console.error('Erreur lors du chargement des produits:', error);
    }
}

// Charger les catégories
async function loadCategories() {
    try {
        const { data } = await axios.get('/api/products/categories');
        categories = data || [];
    } catch (error) {
        console.error('Erreur lors du chargement des catégories:', error);
        categories = [];
    }
}

// Ajouter une ligne via code-barres (produit ou variante)
async function addItemByBarcode() {
    const input = document.getElementById('invoiceBarcodeInput');
    const code = (input?.value || '').trim();
    if (!code) {
        showWarning('Veuillez saisir/scanner un code-barres');
        return;
    }
    // 1) lookup local cache
    let hit = window._productByBarcode && window._productByBarcode.get(code);
    // 2) fallback server
    if (!hit) {
        try {
            const res = await fetch(appPath(`/api/products/scan/${encodeURIComponent(code)}/`), { credentials: 'include' });
            if (res.ok) {
                const data = await res.json();
                if (data && data.product_id) {
                    const prod = products.find(p => p.product_id === data.product_id);
                    hit = prod ? { ...prod } : { product_id: data.product_id, name: data.product_name, price: data.price };
                    if (data.variant) hit._variant = { variant_id: data.variant.variant_id, imei_serial: data.variant.imei_serial };
                }
            }
        } catch (e) { /* ignore */ }
    }
    if (!hit) {
        showError('Produit non trouvé');
        return;
    }

    // Si variante détectée, regrouper par produit et accumuler IMEIs
    if (hit._variant && hit._variant.imei_serial) {
        const imeiNorm = _normalizeCode(hit._variant.imei_serial);
        const existing = invoiceItems.find(i => i.product_id === hit.product_id);
        if (existing) {
            existing.scannedImeis = existing.scannedImeis || [];
            // si IMEI déjà dans la ligne, ne pas incrémenter la quantité
            if (existing.scannedImeis.some(x => _normalizeCode(x) === imeiNorm)) {
                showWarning(`Cette ${MOT('variante').toLowerCase()} est déjà scannée`);
            } else {
                existing.quantity = (existing.scannedImeis.length + 1);
                existing.total = existing.quantity * existing.unit_price;
                existing.scannedImeis.push(hit._variant.imei_serial);
                // supprimer tout identifiant de variante unique pour rester en "groupe produit"
                existing.variant_id = null;
                existing.variant_imei = null;
            }
        } else {
            invoiceItems.push({
                id: Date.now(),
                product_id: hit.product_id,
                product_name: hit.name,
                variant_id: null,
                variant_imei: null,
                scannedImeis: [hit._variant.imei_serial],
                quantity: 1,
                unit_price: Math.round(Number(hit.price) || 0),
                total: Math.round(Number(hit.price) || 0)
            });
        }
        // Fusionner si plusieurs lignes de même produit existent
        mergeProductRows();
    } else {
        // Produit simple: regrouper par produit sans variante
        const existing = invoiceItems.find(i => i.product_id === hit.product_id && !i.variant_id);
        if (existing) {
            existing.quantity += 1;
            existing.total = existing.quantity * existing.unit_price;
        } else {
            invoiceItems.push({
                id: Date.now(),
                product_id: hit.product_id,
                product_name: hit.name,
                variant_id: null,
                variant_imei: null,
                scannedImeis: [],
                quantity: 1,
                unit_price: Math.round(Number(hit.price) || 0),
                total: Math.round(Number(hit.price) || 0)
            });
        }
    }
    updateInvoiceItemsDisplay();
    calculateTotals();
    if (input) input.value = '';
}

// Saisie manuelle de code-barres (sans auto-search, l'utilisateur tape puis clique Valider)
function addItemByManualBarcode() {
    const manualInput = document.getElementById('invoiceManualBarcodeInput');
    const code = (manualInput?.value || '').trim();
    if (!code) {
        showWarning(`Veuillez saisir un code-barres ou ${MOT('identifiant_court').toLowerCase()}`);
        manualInput?.focus();
        return;
    }
    // Copier dans le champ principal et déclencher la recherche
    const mainInput = document.getElementById('invoiceBarcodeInput');
    if (mainInput) mainInput.value = code;
    addItemByBarcode();
    // Vider le champ manuel
    if (manualInput) {
        manualInput.value = '';
        manualInput.focus();
    }
}

// Fusionner les lignes de même produit en une seule avec IMEIs cumulés
// IMPORTANT: préserve l'ordre original des items (sections, custom items restent à leur place)
// Le tableau des articles défile horizontalement : une liste de suggestions
// positionnée en absolu dedans serait rognée par le conteneur. On l'ancre donc
// en `fixed` sous son champ de saisie, hors du flux du tableau.
function anchorSuggestionBox(input, box) {
    if (!input || !box) return;
    const r = input.getBoundingClientRect();
    // Le champ de recherche est étroit; on s'aligne sur la colonne qui le
    // contient, avec un plancher lisible pour les noms de produits longs.
    const container = input.closest('.position-relative');
    const width = Math.max(r.width, container ? container.getBoundingClientRect().width : 0, 320);
    // Ne pas déborder à droite de la fenêtre.
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    // `setProperty(..., 'important')` est indispensable : la boîte porte la
    // classe utilitaire Bootstrap `position-absolute`, déclarée
    // `position: absolute !important`. Un simple `style.position = 'fixed'`
    // perdait donc l'arbitrage — le `top` était calculé en coordonnées écran
    // mais appliqué comme décalage dans le conteneur, et la liste atterrissait
    // plusieurs centaines de pixels trop bas, souvent hors de l'écran.
    box.style.setProperty('position', 'fixed', 'important');
    box.style.setProperty('left', `${left}px`, 'important');
    box.style.setProperty('right', 'auto', 'important');
    box.style.setProperty('width', `${width}px`, 'important');
    box.style.overflowY = 'auto';

    // Une liste `fixed` ne défile pas avec la page : ce qui dépasse du bas de
    // l'écran est définitivement hors d'atteinte. Sur mobile, où le champ se
    // retrouve souvent en bas, on bascule la liste au-dessus du champ dès que
    // la place manque dessous, et on rogne la hauteur à l'espace réellement
    // disponible.
    const MARGE = 8;
    const placeDessous = window.innerHeight - r.bottom - MARGE;
    const placeDessus = r.top - MARGE;
    const auDessus = placeDessous < 160 && placeDessus > placeDessous;
    const hauteur = Math.min(240, Math.max(120, auDessus ? placeDessus : placeDessous));

    box.style.setProperty('max-height', `${hauteur}px`, 'important');
    if (auDessus) {
        box.style.setProperty('top', 'auto', 'important');
        box.style.setProperty('bottom', `${window.innerHeight - r.top}px`, 'important');
    } else {
        box.style.setProperty('top', `${r.bottom}px`, 'important');
        box.style.setProperty('bottom', 'auto', 'important');
    }

    // Une liste ancrée doit passer au-dessus du contenu de la modale.
    box.style.setProperty('z-index', '2000', 'important');
}

// Une liste ancrée en `fixed` ne suit pas le défilement : on la referme.
function closeAnchoredSuggestions() {
    document.querySelectorAll('#invoiceModal .product-suggestions, #invoiceModal .exchange-product-suggestions')
        .forEach(box => {
            if (box.classList.contains('d-none')) return;
            box.classList.add('d-none');
            box.innerHTML = '';
        });
}

function mergeProductRows() {
    const groups = new Map();
    // Grouper par product_id, mais garder l'index de la première occurrence
    // Ne pas grouper les lignes splittées individuellement (_splitVariant)
    invoiceItems.forEach((row, idx) => {
        const key = String(row.product_id || '');
        if (!key) return; // Pas de product_id = section ou custom sans produit
        if (row._splitVariant) return; // Ne pas regrouper les variantes séparées
        if (!groups.has(key)) groups.set(key, { firstIndex: idx, rows: [] });
        groups.get(key).rows.push(row);
    });

    // Créer un tableau avec les items fusionnés, en préservant l'ordre
    const merged = [];
    const processedProductIds = new Set();

    invoiceItems.forEach((row, idx) => {
        const key = String(row.product_id || '');

        // Item sans product_id (section, custom item vide) : garder tel quel
        if (!key) {
            merged.push(row);
            return;
        }

        // Lignes splittées individuellement: garder telles quelles
        if (row._splitVariant) {
            merged.push(row);
            return;
        }

        // Déjà traité ce product_id
        if (processedProductIds.has(key)) return;
        processedProductIds.add(key);

        const group = groups.get(key);
        if (!group) return;

        const rows = group.rows;
        if (rows.length === 1) {
            merged.push(rows[0]);
            return;
        }

        // Fusionner les lignes du même produit
        const base = { ...rows[0] };
        base.scannedImeis = ([]).concat(...rows.map(r => r.scannedImeis || []));
        // dédoublonner IMEIs
        const uniq = Array.from(new Set(base.scannedImeis.map(_normalizeCode)));
        base.scannedImeis = uniq;
        base.variant_id = null;
        base.variant_imei = null;
        if (base.scannedImeis.length > 0) {
            base.quantity = base.scannedImeis.length;
        } else {
            base.quantity = rows.reduce((s, r) => s + (Number(r.quantity) || 0), 0);
        }
        base.total = base.quantity * (Number(base.unit_price) || 0);
        merged.push(base);
    });

    invoiceItems = merged;
}

// Remplir le select des clients
function populateClientSelect() {
    const clientSelect = document.getElementById('clientSelect');
    if (!clientSelect) return;

    // Si c'est un input hidden, on ne remplit rien (compatibilité ancienne UI)
    if (clientSelect.tagName && clientSelect.tagName.toLowerCase() === 'select') {
        clientSelect.innerHTML = '<option value="">Sélectionner un client</option>';
        clients.forEach(client => {
            const option = document.createElement('option');
            option.value = client.client_id;
            option.textContent = client.name;
            clientSelect.appendChild(option);
        });
    }
}

// Recherche client avec autocomplétion (améliorée: dropdown au focus + filtrage live)
function setupClientSearch() {
    const searchInput = document.getElementById('clientSearch');
    const resultsBox = document.getElementById('clientSearchResults');
    if (!searchInput || !resultsBox) return;

    const closeResults = () => { resultsBox.style.display = 'none'; };

    // Conserver la dernière liste de résultats pour la sélection
    let _latestClientResults = [];

    const renderList = async (term) => {
        const t = String(term || '').trim();
        try {
            // server-side search, limited
            const { data } = await axios.get('/api/clients/', { params: { search: t || undefined, limit: 8 } });
            const list = Array.isArray(data) ? data : (data.items || []);
            _latestClientResults = list || [];
            if (!list.length) {
                resultsBox.innerHTML = '<div class="list-group-item text-muted small">Aucun client</div>';
            } else {
                resultsBox.innerHTML = list.map(c => `
                    <button type="button" class="list-group-item list-group-item-action" data-client-id="${c.client_id}">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <strong>${escapeHtml(c.name || '')}</strong>
                                ${c.email ? `<div class=\"text-muted small\">${escapeHtml(c.email)}</div>` : ''}
                            </div>
                            ${c.phone ? `<small class=\"text-muted\">${escapeHtml(c.phone)}</small>` : ''}
                        </div>
                    </button>
                `).join('');
            }
            resultsBox.style.display = 'block';
        } catch (e) {
            resultsBox.innerHTML = '<div class="list-group-item text-muted small">Erreur de chargement</div>';
            resultsBox.style.display = 'block';
        }
    };

    // Ouvrir au focus (même sans recherche)
    searchInput.addEventListener('focus', () => {
        renderList(searchInput.value);
    });

    // Filtrage live au fil de la frappe
    searchInput.addEventListener('input', debounce(function (e) {
        const inputVal = (e && e.target && typeof e.target.value === 'string') ? e.target.value : (searchInput.value || '');
        renderList(inputVal);
    }, 200));

    // Sélection par clic
    resultsBox.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-client-id]');
        if (!btn) return;
        const id = Number(btn.getAttribute('data-client-id'));
        // Chercher d'abord dans les derniers résultats, sinon fallback global
        const c = (_latestClientResults || []).find(x => Number(x.client_id) === id) || (clients || []).find(x => Number(x.client_id) === id);
        if (c) {
            selectClient(c.client_id, c.name);
        } else {
            // Fallback minimal si aucune info (remplit juste l'id)
            selectClient(id, '');
        }
    });

    // Navigation clavier
    searchInput.addEventListener('keydown', function (e) {
        const items = resultsBox.querySelectorAll('.list-group-item');
        const active = resultsBox.querySelector('.list-group-item.active');
        let idx = Array.from(items).indexOf(active);
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (idx < items.length - 1) { if (active) active.classList.remove('active'); items[idx + 1]?.classList.add('active'); }
            else if (idx === -1 && items.length) { items[0].classList.add('active'); }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (idx > 0) { if (active) active.classList.remove('active'); items[idx - 1]?.classList.add('active'); }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (active) {
                active.click();
            } else if (items.length) {
                items[0].click();
            }
        }
    });

    // Clic extérieur pour fermer
    document.addEventListener('click', function (e) {
        if (!searchInput.contains(e.target) && !resultsBox.contains(e.target)) {
            closeResults();
        }
    });
}

function selectClient(clientId, clientName) {
    const hidden = document.getElementById('clientSelect');
    const input = document.getElementById('clientSearch');
    const resultsBox = document.getElementById('clientSearchResults');
    if (hidden) hidden.value = String(clientId || '');
    if (input) input.value = clientName || '';
    if (resultsBox) resultsBox.style.display = 'none';
}

// Afficher les factures
function displayInvoices() {
    const tbody = document.getElementById('invoicesTableBody');
    if (!tbody) return;

    if (filteredInvoices.length === 0) {
        showEmptyState();
        return;
    }

    // Server-side pagination: render the page as returned
    const invoicesToShow = filteredInvoices;

    tbody.innerHTML = invoicesToShow.map(invoice => {
        // Utiliser directement le nom du client retourné par l'API
        const clientName = invoice.client_name || '';
        const invDate = invoice.date || invoice.invoice_date;
        const due = invoice.due_date || invoice.dueDate;
        const total = Number((invoice.total ?? invoice.total_amount) || 0);
        const isPaid = String(invoice.status).toUpperCase() === 'PAID';

        // Déterminer le type de facture et son badge
        const invoiceType = invoice.invoice_type || 'normal';
        let typeBadge = '';
        let typeLabel = '';

        if (invoiceType === 'flash_sale') {
            typeBadge = 'bg-warning text-dark';
            typeLabel = '<i class="bi bi-lightning-fill me-1"></i>Flash';
        } else if (invoiceType === 'exchange') {
            typeBadge = 'bg-info';
            typeLabel = '<i class="bi bi-arrow-left-right me-1"></i>Échange';
        } else {
            typeBadge = 'bg-secondary';
            typeLabel = '<i class="bi bi-receipt me-1"></i>Normal';
        }

        // Formater la date sans l'heure
        const dateTimeDisplay = invDate ? formatDate(invDate) : '-';

        return `
        <tr>
            <td>
                <strong>${escapeHtml(invoice.invoice_number)}</strong>
            </td>
            <td>
                <span class="badge ${typeBadge}">${typeLabel}</span>
            </td>
            <td>${invoice.client_id
                ? `<a href="${appPath('/clients/detail?id=' + invoice.client_id)}" class="text-reset">${escapeHtml(clientName || '-')}</a>`
                : escapeHtml(clientName || '-')}</td>
            <td>${dateTimeDisplay}</td>
            <td>${due ? formatDate(due) : '-'}</td>
            <td><strong>${formatCurrency(total)}</strong></td>
            <td>
                <span class="badge bg-${getInvoiceStatusBadgeColor(invoice.status)}">
                    ${getInvoiceStatusLabel(invoice.status)}
                </span>
            </td>
            <td>
                <!--
                  Neuf boutons par ligne saturaient la colonne et la faisaient
                  couper sur les écrans étroits. Seuls les gestes courants
                  restent visibles — consulter, modifier, encaisser — le reste
                  passe dans le menu. L'attribut data-bs-config place ce menu en
                  position fixe : sous 1200 px le conteneur du tableau défile
                  horizontalement et le rognerait.
                -->
                <div class="btn-group" role="group">
                    <button class="btn btn-sm btn-outline-info" onclick="viewInvoice(${invoice.invoice_id})" title="Voir">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" onclick="editInvoice(${invoice.invoice_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${!isPaid ? `
                        <button class="btn btn-sm btn-outline-success" onclick="addPayment(${invoice.invoice_id})" title="Enregistrer un paiement">
                            <i class="bi bi-credit-card"></i>
                        </button>
                    ` : ''}
                    <div class="dropdown">
                        <button class="btn btn-sm btn-outline-secondary" type="button" data-bs-toggle="dropdown"
                            data-bs-config='{"popperConfig":{"strategy":"fixed"}}'
                            aria-expanded="false" title="Autres actions" aria-label="Autres actions">
                            <i class="bi bi-three-dots"></i>
                        </button>
                        <ul class="dropdown-menu dropdown-menu-end">
                            <li><button class="dropdown-item" type="button" onclick="openReturnModal(${invoice.invoice_id})">
                                <i class="bi bi-arrow-return-left"></i>Retour produit
                            </button></li>
                            ${invoice.quotation_id ? `
                            <li><button class="dropdown-item" type="button" onclick="viewQuotation(${invoice.quotation_id})">
                                <i class="bi bi-file-text"></i>Voir le devis d'origine
                            </button></li>
                            ` : ''}
                            <li><button class="dropdown-item" type="button" onclick="generateDeliveryNote(${invoice.invoice_id})">
                                <i class="bi bi-truck"></i>Générer un bon de livraison
                            </button></li>
                            <li><button class="dropdown-item" type="button" onclick="printInvoice(${invoice.invoice_id})">
                                <i class="bi bi-printer"></i>Imprimer
                            </button></li>
                            <li><button class="dropdown-item" type="button" onclick="duplicateInvoice(${invoice.invoice_id})">
                                <i class="bi bi-copy"></i>Dupliquer
                            </button></li>
                            <li><hr class="dropdown-divider"></li>
                            <li><button class="dropdown-item text-danger" type="button" onclick="deleteInvoice(${invoice.invoice_id})">
                                <i class="bi bi-trash"></i>Supprimer
                            </button></li>
                        </ul>
                    </div>
                </div>
            </td>
        </tr>
    `}).join('');
}

function showEmptyState() {
    const tbody = document.getElementById('invoicesTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    Aucune facture trouvée
                </td>
            </tr>
        `;
    }
}

function showLoading() {
    // No visual loading indicator to keep UI instant
}

// Utilitaires pour les statuts de factures
function getInvoiceStatusBadgeColor(status) {
    const s = String(status || '').trim();
    const sLower = s.toLowerCase();
    // Normalized groups
    if (s === 'PAID' || sLower === 'payee' || sLower === 'payée' || sLower === 'paye' || sLower === 'payé') {
        // Green for paid
        return 'success';
    }
    if (s === 'OVERDUE' || sLower === 'en retard') {
        // Treat overdue as red to indicate attention
        return 'danger';
    }
    if (s === 'CANCELLED' || sLower === 'annulee' || sLower === 'annulée' || sLower === 'annule' || sLower === 'annulé') {
        // Red for cancelled
        return 'danger';
    }
    if (sLower === 'partiellement payee' || sLower === 'partiellement payée' || sLower === 'partially paid' || sLower === 'partially_paid') {
        // Yellow for partially paid
        return 'warning';
    }
    if (s === 'SENT' || s === 'DRAFT' || sLower === 'en attente' || sLower === 'pending' || sLower === 'brouillon' || sLower === 'envoyée' || sLower === 'envoyee') {
        // Red for pending per requirement
        return 'danger';
    }
    return 'secondary';
}

function getInvoiceStatusLabel(status) {
    const s = String(status || '').trim();
    const sLower = s.toLowerCase();
    switch (s) {
        case 'DRAFT': return 'Brouillon';
        case 'SENT': return 'Envoyée';
        case 'PAID': return 'Payée';
        case 'OVERDUE': return 'En retard';
        case 'CANCELLED': return 'Annulée';
    }
    // Handle French lowercase statuses
    if (sLower === 'en attente' || sLower === 'pending') return 'En attente';
    if (sLower === 'payee' || sLower === 'payée' || sLower === 'paye' || sLower === 'payé') return 'Payée';
    if (sLower === 'partiellement payee' || sLower === 'partiellement payée' || sLower === 'partially paid' || sLower === 'partially_paid') return 'Partiellement payée';
    if (sLower === 'en retard' || sLower === 'overdue') return 'En retard';
    if (sLower === 'annulee' || sLower === 'annulée' || sLower === 'annule' || sLower === 'annulé' || sLower === 'cancelled') return 'Annulée';
    if (sLower === 'brouillon' || sLower === 'draft') return 'Brouillon';
    if (sLower === 'envoyee' || sLower === 'envoyée' || sLower === 'sent') return 'Envoyée';
    return s;
}

// Filtrer les factures
// Conservé pour compat, mais désormais on recharge côté serveur
function filterInvoices() { currentPage = 1; loadInvoices(); }

// Extrait un bloc de métadonnées (__SERIALS__, __QUOTE_QTYS__...) des notes.
// Les notes peuvent enchaîner plusieurs blocs (__SERIALS__ puis __SIGNATURE__,
// __LOCATION__...) : on s'arrête donc au bloc suivant au lieu de tout avaler,
// sinon le JSON.parse échoue et la métadonnée est silencieusement perdue.
function extractNotesRawBlock(notes, key) {
    const txt = String(notes || '');
    const marker = `__${key}__=`;
    const start = txt.indexOf(marker);
    if (start === -1) return null;
    let sub = txt.slice(start + marker.length);
    const cut = sub.indexOf('\n__');
    if (cut !== -1) sub = sub.slice(0, cut);
    sub = sub.trim();
    return sub || null;
}

function extractNotesMeta(notes, key) {
    const sub = extractNotesRawBlock(notes, key);
    if (!sub) return null;
    try {
        return JSON.parse(sub);
    } catch (e) {
        console.warn(`Métadonnée __${key}__ illisible dans les notes:`, e);
        return null;
    }
}

// Notes brutes de la facture en cours d'édition. Permet de reconduire les blocs
// que le formulaire ne sait pas ressaisir (signature, géolocalisation) au lieu
// de les effacer à chaque enregistrement.
let _editingInvoiceRawNotes = null;

// ==================== Retour produit ====================
// Retire des articles vendus d'une facture et les remet en stock (avec
// remboursement automatique si la facture était payée). Source des unités :
// GET /api/invoices/{id}/returnable-units ; action : POST .../return-items.
let _returnData = null;

async function openReturnModal(invoiceId) {
    try {
        const res = await axios.get(`/api/invoices/${invoiceId}/returnable-units`);
        _returnData = res.data || {};
        document.getElementById('returnInvoiceId').value = invoiceId;
        document.getElementById('returnInvoiceNumber').textContent = _returnData.invoice_number || ('#' + invoiceId);
        renderReturnUnits(_returnData);
        new bootstrap.Modal(document.getElementById('returnModal')).show();
    } catch (e) {
        showError('Impossible de charger les articles de la facture : ' + (e.message || e));
    }
}

function renderReturnUnits(data) {
    const container = document.getElementById('returnUnitsContainer');
    const empty = document.getElementById('returnEmpty');
    const units = (data && data.units) || [];
    if (!units.length) {
        container.innerHTML = '';
        empty.classList.remove('d-none');
        document.getElementById('returnConfirmBtn').disabled = true;
        document.getElementById('returnSummary').textContent = 'Aucun article sélectionné';
        return;
    }
    empty.classList.add('d-none');
    container.innerHTML = units.map(u => {
        const imei = u.variant_imei
            ? `<span class="badge bg-light text-dark border ms-2"><i class="bi bi-upc-scan"></i> ${escapeHtml(u.variant_imei)}</span>` : '';
        const cond = u.variant_condition ? `<span class="text-muted small ms-2">${escapeHtml(u.variant_condition)}</span>` : '';
        const qtyControl = (!u.is_variant && u.quantity > 1)
            ? `<div class="input-group input-group-sm" style="width:150px;">
                   <span class="input-group-text">Retour</span>
                   <input type="number" class="form-control return-qty" data-sale="${u.sale_id}" min="1" max="${u.quantity}" value="${u.quantity}" oninput="updateReturnSummary()">
                   <span class="input-group-text">/ ${u.quantity}</span>
               </div>`
            : `<span class="text-muted small text-nowrap">Qté ${u.quantity}</span>`;
        return `
        <div class="d-flex align-items-center justify-content-between border rounded p-2 mb-2">
            <div class="form-check d-flex align-items-center gap-2 mb-0">
                <input class="form-check-input return-check" type="checkbox" data-sale="${u.sale_id}" data-unit="${u.unit_price}" id="ret_${u.sale_id}" onchange="updateReturnSummary()">
                <label class="form-check-label" for="ret_${u.sale_id}">
                    <span class="fw-semibold">${escapeHtml(u.product_name || '')}</span>${imei}${cond}
                </label>
            </div>
            <div class="d-flex align-items-center gap-3">
                ${qtyControl}
                <span class="fw-semibold text-nowrap">${formatCurrency(u.total_amount)}</span>
            </div>
        </div>`;
    }).join('');
    updateReturnSummary();
}

function updateReturnSummary() {
    const data = _returnData || {};
    const checks = document.querySelectorAll('.return-check:checked');
    let count = 0, subtotalHT = 0;
    checks.forEach(chk => {
        const sale = chk.getAttribute('data-sale');
        const unitPrice = parseFloat(chk.getAttribute('data-unit')) || 0;
        const qtyInput = document.querySelector(`.return-qty[data-sale="${sale}"]`);
        const u = (data.units || []).find(x => String(x.sale_id) === String(sale));
        const qty = qtyInput ? (parseInt(qtyInput.value) || 0) : (u ? u.quantity : 1);
        count += qty;
        subtotalHT += unitPrice * qty;
    });
    const taxFactor = (data.show_tax && data.tax_rate) ? (1 + data.tax_rate / 100) : 1;
    let refund = subtotalHT * taxFactor;
    const paid = data.paid_amount || 0;
    if (refund > paid) refund = paid; // on ne rembourse jamais plus que ce qui a été encaissé
    const btn = document.getElementById('returnConfirmBtn');
    const summary = document.getElementById('returnSummary');
    if (count === 0) {
        btn.disabled = true;
        summary.textContent = 'Aucun article sélectionné';
        return;
    }
    btn.disabled = false;
    if (paid > 0) {
        summary.innerHTML = `<strong>${count}</strong> article(s) → reviennent en stock &nbsp;|&nbsp; Remboursement estimé : <strong>${formatCurrency(refund)}</strong>`;
    } else {
        summary.innerHTML = `<strong>${count}</strong> article(s) → reviennent en stock &nbsp;|&nbsp; <span class="text-muted">Facture non payée : aucun remboursement</span>`;
    }
}

async function submitReturn() {
    const invoiceId = document.getElementById('returnInvoiceId').value;
    const checks = document.querySelectorAll('.return-check:checked');
    if (!checks.length) return;
    const units = [];
    checks.forEach(chk => {
        const sale = parseInt(chk.getAttribute('data-sale'));
        const qtyInput = document.querySelector(`.return-qty[data-sale="${sale}"]`);
        const qty = qtyInput ? (parseInt(qtyInput.value) || 1) : null;
        units.push(qty ? { sale_id: sale, quantity: qty } : { sale_id: sale });
    });
    const btn = document.getElementById('returnConfirmBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Traitement...';
    try {
        const res = await axios.post(`/api/invoices/${invoiceId}/return-items`, { units, refund_method: 'espèces' });
        const d = res.data || {};
        const msg = (d.refund_amount > 0)
            ? `Retour effectué. Remboursement enregistré : ${formatCurrency(d.refund_amount)}.`
            : 'Retour effectué. Article(s) remis en stock.';
        if (typeof showSuccess === 'function') showSuccess(msg); else if (typeof showToast === 'function') showToast(msg); else showAlert(msg);
        const modal = bootstrap.Modal.getInstance(document.getElementById('returnModal'));
        if (modal) modal.hide();
        loadInvoices();
    } catch (e) {
        const detail = (e.response && e.response.data && e.response.data.detail) || e.message || 'Erreur';
        showError('Échec du retour : ' + detail);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-arrow-return-left me-2"></i>Valider le retour';
    }
}

// Pagination
function updatePagination(totalCount) {
    const count = Number.isFinite(totalCount) ? Number(totalCount) : filteredInvoices.length;
    const totalPages = Math.ceil(count / itemsPerPage);
    const paginationContainer = document.getElementById('pagination-container');

    if (!paginationContainer || totalPages <= 1) {
        if (paginationContainer) paginationContainer.innerHTML = '';
        return;
    }

    let paginationHTML = '<nav><ul class="pagination justify-content-center">';

    // Bouton précédent
    paginationHTML += `
        <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="#" onclick="changePage(${currentPage - 1})">Précédent</a>
        </li>
    `;

    // Numéros de page
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
            paginationHTML += `
                <li class="page-item ${i === currentPage ? 'active' : ''}">
                    <a class="page-link" href="#" onclick="changePage(${i})">${i}</a>
                </li>
            `;
        } else if (i === currentPage - 3 || i === currentPage + 3) {
            paginationHTML += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        }
    }

    // Bouton suivant
    paginationHTML += `
        <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
            <a class="page-link" href="#" onclick="changePage(${currentPage + 1})">Suivant</a>
        </li>
    `;

    paginationHTML += '</ul></nav>';
    paginationContainer.innerHTML = paginationHTML;
}

function changePage(page) {
    const totalPagesEl = document.getElementById('pagination-container');
    if (page >= 1) {
        currentPage = page;
        loadInvoices();
    }
}

// Ouvrir le modal pour nouvelle facture
function openInvoiceModal() {
    try {
        // Vérifier l'existence des éléments essentiels
        const modalEl = document.getElementById('invoiceModal');
        if (!modalEl) {
            console.error('Modal #invoiceModal introuvable dans le DOM');
            if (typeof showError === 'function') {
                showError('Erreur: formulaire de facture introuvable');
            }
            return;
        }
        // Charger les produits dès l'ouverture si la liste est vide
        try { if (!Array.isArray(products) || products.length === 0) { loadProducts().catch(() => { }); } } catch (e) { }

        const titleEl = document.getElementById('invoiceModalTitle');
        const formEl = document.getElementById('invoiceForm');
        const idEl = document.getElementById('invoiceId');
        const numberEl = document.getElementById('invoiceNumber');

        // Titre du modal
        if (titleEl) {
            titleEl.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouvelle Facture';
        }

        // Reset du formulaire
        if (formEl) {
            formEl.reset();
            try {
                formEl.dataset.quotationId = '';
            } catch (e) { }
        }

        if (idEl) {
            idEl.value = '';
            delete idEl.dataset.paidAmount;
        }

        // Reset client search/hidden
        try {
            const hidden = document.getElementById('clientSelect');
            const input = document.getElementById('clientSearch');
            const box = document.getElementById('clientSearchResults');
            const externalNotes = document.getElementById('externalNotes');
            const internalNotes = document.getElementById('internalNotes');
            if (hidden) hidden.value = '';
            if (input) input.value = '';
            if (box) box.style.display = 'none';
            if (externalNotes) externalNotes.value = '';
            if (internalNotes) internalNotes.value = '';
        } catch (e) { }

        setDefaultDates();

        // Pré-remplir un numéro de facture fiable depuis le serveur
        if (numberEl) {
            try {
                numberEl.value = '';
                numberEl.placeholder = 'Chargement du numéro...';
                axios.get('/api/invoices/next-number/').then(({ data }) => {
                    // Le formulaire a pu basculer en édition pendant l'appel:
                    // ne jamais écraser le numéro d'une facture existante.
                    if ((document.getElementById('invoiceId') || {}).value) return;
                    if (data && data.invoice_number) {
                        numberEl.value = data.invoice_number;
                        numberEl.placeholder = '';
                    } else {
                        // Si l'API ne renvoie rien, laisser vide: le serveur générera automatiquement
                        numberEl.value = '';
                        numberEl.placeholder = 'Sera généré automatiquement';
                    }
                }).catch((error) => {
                    console.error('Erreur lors du chargement du numéro de facture:', error);
                    // En cas d'erreur API, laisser vide pour laisser le backend générer
                    numberEl.value = '';
                    numberEl.placeholder = 'Sera généré automatiquement';
                });
            } catch (e) { /* ignore */ }
        }

        // Démarrer avec une liste vide; l'utilisateur scanne pour ajouter
        invoiceItems = [];
        exchangeItems = [];
        _editingInvoiceRawNotes = null;
        // Réinitialiser les quantités de devis
        quoteQtyByProductId = new Map();
        updateInvoiceItemsDisplay();
        renderExchangeItems();

        // Reset somme totale échange
        const exchangeTotalInput = document.getElementById('exchangeTotalAmount');
        if (exchangeTotalInput) exchangeTotalInput.value = 0;

        // Reset méthode de paiement
        try {
            const pmSel = document.getElementById('invoicePaymentMethod');
            if (pmSel) pmSel.value = '';
        } catch (e) { }

        // Defaults for TVA UI
        const taxSwitch = document.getElementById('showTaxSwitch');
        const taxRateInput = document.getElementById('taxRateInput');
        if (taxSwitch) taxSwitch.checked = true;
        if (taxRateInput) taxRateInput.value = 18;

        // Reset garantie
        const warrantySwitch = document.getElementById('hasWarrantySwitch');
        const warrantyOptions = document.getElementById('warrantyOptions');
        const warrantyInfo = document.getElementById('warrantyInfo');
        if (warrantySwitch) warrantySwitch.checked = false;
        if (warrantyOptions) warrantyOptions.style.display = 'none';
        if (warrantyInfo) warrantyInfo.style.display = 'none';
        // Sélectionner 12 mois par défaut
        const warranty12 = document.getElementById('warranty12months');
        if (warranty12) warranty12.checked = true;

        // Ensure listeners are bound in case modal was created after initial setup
        setupTaxControls();
        setupWarrantyControls();
        calculateTotals();

        // Recharger les méthodes de paiement lors de l'ouverture du modal
        populatePaymentMethodSelects(true);

        // Setup signature pad
        try {
            const canvas = document.getElementById('signatureCanvas');
            if (canvas && canvas.getContext) {
                const ctx = canvas.getContext('2d');
                let drawing = false; let last = null;
                const getPos = (e) => {
                    const rect = canvas.getBoundingClientRect();
                    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
                    const y = (e.touches ? e.touches[0].clientY : e.clientY) - rect.top;
                    return { x, y };
                };
                const start = (e) => { drawing = true; last = getPos(e); };
                const move = (e) => {
                    if (!drawing) return;
                    const pos = getPos(e);
                    ctx.beginPath();
                    ctx.moveTo(last.x, last.y);
                    ctx.lineTo(pos.x, pos.y);
                    ctx.strokeStyle = '#111';
                    ctx.lineWidth = 2;
                    ctx.lineCap = 'round';
                    ctx.stroke();
                    last = pos;
                    e.preventDefault();
                };
                const end = () => { drawing = false; };
                canvas.addEventListener('mousedown', start);
                canvas.addEventListener('mousemove', move);
                window.addEventListener('mouseup', end);
                canvas.addEventListener('touchstart', start, { passive: false });
                canvas.addEventListener('touchmove', move, { passive: false });
                canvas.addEventListener('touchend', end);

                const clearBtn = document.getElementById('signatureClearBtn');
                if (clearBtn) {
                    clearBtn.addEventListener('click', () => {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                    });
                }
            }
        } catch (e) {
            console.warn('Erreur initialisation signature pad:', e);
        }

        // Initialiser l'état du formulaire selon le type de facture
        try {
            toggleInvoiceType();
        } catch (e) {
            console.warn('Erreur initialisation type facture:', e);
        }

        // Afficher le modal - version sécurisée
        try {
            if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
                if (modalInstance && typeof modalInstance.show === 'function') {
                    modalInstance.show();
                } else {
                    console.warn('Instance modal Bootstrap invalide');
                }
            } else {
                console.error('Bootstrap Modal non disponible');
            }
        } catch (e) {
            console.error('Erreur ouverture modal:', e);
            if (typeof showError === 'function') {
                showError('Erreur lors de l\'ouverture du formulaire de facture');
            }
        }

    } catch (error) {
        console.error('Erreur dans openInvoiceModal:', error);
        if (typeof showError === 'function') {
            showError('Erreur lors de l\'ouverture du formulaire de facture');
        }
    }
}

// Pré-chargement facture depuis un devis
async function preloadPrefilledInvoiceFromQuotation(prefill) {
    openInvoiceModal();
    document.getElementById('invoiceModalTitle').innerHTML = '<i class="bi bi-receipt me-2"></i>Facture depuis devis ' + (prefill?.quotation_number || '');
    try { if (prefill?.quotation_id) document.getElementById('invoiceForm').dataset.quotationId = String(prefill.quotation_id); } catch (e) { }
    // Client
    try {
        const clientSel = document.getElementById('clientSelect');
        if (clientSel && prefill?.client_id) clientSel.value = prefill.client_id;
        const input = document.getElementById('clientSearch');
        let c = (clients || []).find(x => Number(x.client_id) === Number(prefill?.client_id));
        if (!c && prefill?.client_id) {
            try {
                const { data } = await axios.get(`/api/clients/${prefill.client_id}`);
                if (data) {
                    c = data;
                    try {
                        if (!Array.isArray(clients)) clients = [];
                        if (!clients.some(x => Number(x.client_id) === Number(data.client_id))) clients.push(data);
                    } catch (e) { }
                }
            } catch (e) { }
        }
        if (input) input.value = c ? (c.name || '') : (prefill?.client_name || '');
    } catch (e) { }
    // Date: pour une conversion, utiliser la date du jour (ne pas réutiliser la date du devis)
    try {
        const invDate = document.getElementById('invoiceDate');
        if (invDate) {
            const today = new Date().toISOString().split('T')[0];
            invDate.value = today;
        }
    } catch (e) { }
    // Articles
    invoiceItems = [];
    const _quoteAgg = new Map();
    (prefill?.items || []).forEach(it => {
        // Normalize product_id and detect custom lines coming from quotation
        const rawPid = it.product_id;
        const rawStr = String(rawPid);
        let isMissingPid = (rawPid === null || rawPid === undefined || rawStr === '' || rawStr.toLowerCase() === 'null' || rawStr.toLowerCase() === 'none' || Number(rawPid) === 0);
        // If prefill explicitly flags as custom, force it
        let forceCustom = !!it.is_custom;
        // If a pid is present but doesn't match any known product, treat as custom
        let normPid = (isMissingPid || forceCustom) ? '' : rawPid;
        try {
            const exists = products && products.some(p => Number(p.product_id) === Number(normPid));
            if (!exists) { isMissingPid = true; normPid = ''; }
        } catch (e) { }
        const hasVariants = (productVariantsByProductId.get(Number(normPid)) || []).length > 0;
        invoiceItems.push({
            id: Date.now() + Math.random(),
            product_id: normPid,
            product_name: (isMissingPid || forceCustom) ? (it.product_name || 'Service personnalisé') : it.product_name,
            is_custom: (forceCustom || isMissingPid) ? true : false,
            variant_id: null,
            variant_imei: null,
            scannedImeis: [],
            quantity: hasVariants ? Number(it.quantity || 0) : Number(it.quantity || 1),
            unit_price: Math.round(Number(it.price || 0)),
            total: Math.round(Number(it.total || 0))
        });
        // Agréger quantité demandée dans le devis pour info UI
        try {
            const pid = Number(it.product_id);
            const qty = Number(it.quantity || 0);
            if (!Number.isNaN(pid)) _quoteAgg.set(pid, (_quoteAgg.get(pid) || 0) + qty);
        } catch (e) { }
    });
    // Exposer les quantités du devis dans l'UI (affichage "Qté devis: X")
    quoteQtyByProductId = _quoteAgg;
    // Afficher X emplacements IMEI vides pour variants: on garde la quantité et on laisse l'utilisateur sélectionner
    updateInvoiceItemsDisplay();
    calculateTotals();
}

// Gestion des articles de facture
function addInvoiceItem() {
    const newItem = {
        id: Date.now(),
        product_id: '',
        product_name: '',
        variant_id: null,
        variant_imei: null,
        quantity: 1,
        unit_price: 0,
        total: 0,
        is_gift: false,  // Article gratuit/cadeau
        external_price: null  // Prix d'achat externe (optionnel)
    };

    invoiceItems.push(newItem);
    // S'assurer que les produits sont chargés pour le select
    try { if (!Array.isArray(products) || products.length === 0) { loadProducts().catch(() => { }); } } catch (e) { }
    updateInvoiceItemsDisplay();
}

// Ajouter une ligne libre/service (sans produit, propre à la facture)
function addCustomItem() {
    const newItem = {
        id: Date.now(),
        product_id: '',
        product_name: 'Service personnalisé',
        is_custom: true,
        variant_id: null,
        variant_imei: null,
        scannedImeis: [],
        quantity: 1,
        unit_price: 0,
        total: 0,
        is_gift: false,  // Article gratuit/cadeau
        external_price: null  // Prix d'achat externe (optionnel)
    };
    invoiceItems.push(newItem);
    updateInvoiceItemsDisplay();
}

// Ajouter une section (titre uniquement, sans quantité ni prix)
function addSectionRow() {
    const newItem = {
        id: Date.now(),
        is_section: true,
        section_title: 'Nouvelle section',
        product_id: null,
        product_name: '',
        variant_id: null,
        variant_imei: null,
        scannedImeis: [],
        quantity: 0,
        unit_price: 0,
        total: 0
    };
    invoiceItems.push(newItem);
    updateInvoiceItemsDisplay();
}

// Mettre à jour le titre d'une section
function updateSectionTitle(itemId, title) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    item.section_title = String(title || '').trim();
}

// Basculer le statut cadeau d'un article
function toggleGift(itemId, isGift) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    item.is_gift = !!isGift;
    if (item.is_gift) {
        // Sauvegarder le prix original avant de mettre à 0
        if (item.unit_price > 0) {
            item._originalPrice = item.unit_price;
        }
        item.unit_price = 0;
        item.total = 0;
    } else {
        // Restaurer le prix original
        let restored = false;
        if (item._originalPrice !== undefined && item._originalPrice !== null && item._originalPrice > 0) {
            item.unit_price = item._originalPrice;
            item.total = item._originalPrice * (item.quantity || 1);
            delete item._originalPrice;
            restored = true;
        }
        // Fallback: retrouver le prix du produit depuis la liste
        if (!restored && item.product_id) {
            const product = (products || []).find(p => Number(p.product_id) === Number(item.product_id))
                || (Array.isArray(window._latestProductResults) ? window._latestProductResults.find(p => Number(p.product_id) === Number(item.product_id)) : null);
            if (product) {
                const price = item.price_type === 'wholesale' ? (Number(product.wholesale_price) || Number(product.price) || 0) : (Number(product.price) || 0);
                item.unit_price = price;
                item.total = price * (item.quantity || 1);
            }
        }
    }
    updateInvoiceItemsDisplay();
}

// Exposer les fonctions pour les attributs onclick en HTML
window.addSectionRow = addSectionRow;
window.updateSectionTitle = updateSectionTitle;
window.toggleGift = toggleGift;

function updateInvoiceItemsDisplay() {
    console.log('[Invoices] Mise à jour de l\'affichage des articles, count:', invoiceItems.length);
    const tbody = document.getElementById('invoiceItemsBody');
    if (!tbody) return;

    if (invoiceItems.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" class="text-center text-muted py-3">
                    <i class="bi bi-inbox me-2"></i>Aucun article ajouté
                </td>
            </tr>
        `;
        calculateTotals();
        return;
    }

    const selectedProductIds = new Set(invoiceItems.map(r => Number(r.product_id)).filter(Boolean));
    tbody.innerHTML = invoiceItems.map((item, index) => {
        // Ligne de section: juste un titre, pas de quantité ni prix
        if (item.is_section) {
            const title = escapeHtml(String(item.section_title || 'Nouvelle section').trim());
            return `
        <tr data-item-id="${item.id}" class="table-secondary">
            <td class="text-center align-middle drag-handle" style="cursor:grab;" title="Glisser pour réordonner">
                <i class="bi bi-grip-vertical text-muted fs-5"></i>
            </td>
            <td colspan="6">
                <input type="text" class="form-control form-control-sm fw-bold" value="${title}"
                       placeholder="Nom de section (ex: Matériel, Main d'œuvre)"
                       oninput="updateSectionTitle(${item.id}, this.value)">
            </td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="removeInvoiceItem(${item.id})">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>`;
        }
        // Ligne standard (produit ou service custom)
        const giftClass = item.is_gift ? ' table-secondary' : '';
        const giftStyle = item.is_gift ? ' style="opacity: 0.65;"' : '';
        return `
        <tr data-item-id="${item.id}" class="${giftClass}"${giftStyle}>
            <td class="text-center align-middle drag-handle" style="cursor:grab;" title="Glisser pour réordonner">
                <i class="bi bi-grip-vertical text-muted fs-5"></i>
            </td>
            <td>
                ${item.is_custom ? `
                <input type="text" class="form-control form-control-sm" value="${escapeHtml(item.product_name || '')}" placeholder="Libellé (ex: Service d'installation)" oninput="updateCustomName(${item.id}, this.value)">
                ` : `
                <div class="position-relative" style="min-width: 24rem; z-index: 2000;">
                    <div class="input-group input-group-sm">
                        <input type="text" class="form-control form-control-sm product-search-input" placeholder="Nom, code-barres ou n° série..." data-item-id="${item.id}" />
                        <select class="form-select form-select-sm" onchange="selectProduct(${item.id}, this.value)">
                            <option value="">Sélectionner un produit</option>
                            ${(() => {
                const productList = Array.isArray(products) && products.length
                    ? products
                    : (Array.isArray(window._latestProductResults) ? window._latestProductResults : []);
                // Si le produit actuel n'est pas dans la liste, l'ajouter en premier
                const currentProductInList = item.product_id && productList.some(p => p.product_id == item.product_id);
                let options = '';
                if (item.product_id && !currentProductInList) {
                    options += `<option value="${item.product_id}" selected>${escapeHtml(item.product_name || 'Produit #' + item.product_id)} - ${formatCurrency(item.unit_price)}</option>`;
                }
                options += productList.map(product => {
                    let variants = productVariantsByProductId.get(Number(product.product_id)) || [];
                    if (!variants.length && Array.isArray(product.variants)) {
                        variants = product.variants;
                    }
                    const available = variants.length > 0
                        ? variants.reduce((acc, v) => {
                            if (v && v.is_sold) return acc;
                            const q = v && v.quantity;
                            if (q == null || q === undefined) return acc + 1;
                            const numQ = Number(q);
                            return acc + (Number.isFinite(numQ) && numQ > 0 ? numQ : 1);
                        }, 0)
                        : Number(product.quantity || 0);
                    const alreadySelected = selectedProductIds.has(Number(product.product_id)) && Number(product.product_id) !== Number(item.product_id);
                    const isOutOfStock = available === 0;
                    const disabled = alreadySelected || isOutOfStock;
                    return `
                                <option value="${product.product_id}" ${product.product_id == item.product_id ? 'selected' : ''} ${disabled ? 'disabled' : ''}>
                                    ${escapeHtml(product.name)} - ${formatCurrency(product.price)}${product.wholesale_price ? ` / Gros: ${formatCurrency(product.wholesale_price)}` : ''} ${isOutOfStock ? '(épuisé)' : `(Stock: ${available})`} ${alreadySelected ? '(déjà sélectionné)' : ''}
                                </option>`;
                }).join('');
                return options;
            })()}
                        </select>
                    </div>
                    <div class="product-suggestions list-group d-none" style="position:absolute; left:0; right:0; top:100%; z-index:3000; max-height:240px; overflow:auto; width:100%; background:#fff; border:1px solid rgba(0,0,0,.125); border-radius:.25rem; box-shadow:0 2px 6px rgba(0,0,0,.15);"></div>
                </div>
                `}
                ${(() => {
                const list = item.scannedImeis || [];
                if (!list.length) return '';
                const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
                let html = `<div class=\"small text-muted mt-1\">${MOT('identifiant_court')} : ` + list.map((im, idx) => {
                    const v = variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(im));
                    const condPart = (v && v.condition)
                        ? ' (' + escapeHtml(String(v.condition).charAt(0).toUpperCase() + String(v.condition).slice(1)) + ')'
                        : '';
                    return `
                        <span class=\"badge bg-light text-dark border me-1\">${escapeHtml(im)}${condPart}
                            <a href=\"#\" class=\"text-danger text-decoration-none ms-1\" onclick=\"removeScannedImeiAt(${item.id}, ${idx}); return false;\">&times;</a>
                        </span>`;
                }).join('') + `<a href=\"#\" class=\"ms-1\" onclick=\"clearScannedImeis(${item.id}); return false;\">Tout supprimer</a></div>`;
                if (list.length > 1) {
                    html += `<div class=\"mt-1\"><a href=\"#\" class=\"small text-primary\" onclick=\"splitVariantRows(${item.id}); return false;\"><i class=\"bi bi-arrows-expand me-1\"></i>Séparer les variantes (prix individuel)</a></div>`;
                }
                return html;
            })()}
                ${(() => { try { const q = quoteQtyByProductId.get(Number(item.product_id)); return (typeof q === 'number') ? `<div class=\"small text-muted mt-1\">Qté devis: ${q}</div>` : ''; } catch (e) { return ''; } })()}
                ${(() => { try { const p = (products || []).find(pp => Number(pp.product_id) === Number(item.product_id)) || (Array.isArray(window._latestProductResults) ? window._latestProductResults.find(pp => Number(pp.product_id) === Number(item.product_id)) : null); if (!p) return ''; const stock = computeAvailableStock(p); const cls = stock > 0 ? 'text-success' : 'text-danger'; return `<div class=\"small ${cls} mt-1\">Stock: ${stock}</div>`; } catch (e) { return ''; } })()}
            </td>
            <td>
                <select class="form-select form-select-sm" onchange="selectVariant(${item.id}, this.value)" ${(item.is_custom || !item.product_id) ? 'disabled' : ''}>
                    <option value="">(optionnel) ${MOT('variante')}</option>
                    ${renderVariantOptions(item.product_id, item.variant_id)}
                </select>
            </td>
            <td>
                <input type="number" class="form-control form-control-sm" value="${item.quantity}" min="0"
                       ${item.is_custom ? '' : (((productVariantsByProductId.get(Number(item.product_id)) || []).length > 0 && (item.scannedImeis || []).length > 0) ? 'disabled' : '')}
                       onchange="updateItemQuantity(${item.id}, this.value)">
            </td>
            <td>
                ${!item.is_custom && item.product_id ? (() => {
                const product = (products || []).find(p => p.product_id == item.product_id) || (Array.isArray(window._latestProductResults) ? window._latestProductResults.find(p => p.product_id == item.product_id) : null);
                const hasWholesale = product && product.wholesale_price;
                return hasWholesale ? `
                        <select class="form-select form-select-sm" onchange="togglePriceType(${item.id}, this.value === 'wholesale')">
                            <option value="unit" ${item.price_type !== 'wholesale' ? 'selected' : ''}>Unitaire</option>
                            <option value="wholesale" ${item.price_type === 'wholesale' ? 'selected' : ''}>Gros</option>
                        </select>
                    ` : '<small class="text-muted">-</small>';
            })() : '<small class="text-muted">-</small>'}
            </td>
            <td>
                <input type="text" class="form-control form-control-sm unit-price-input" value="${(item.unit_price).toLocaleString('fr-FR')}"
                       oninput="handlePriceInput(${item.id}, this)"
                       onchange="handlePriceInput(${item.id}, this)"
                       ${item.is_gift ? 'disabled' : ''}>
            </td>
            <td>
                <input type="text" class="form-control form-control-sm external-price-input" 
                       value="${item.external_price && !isNaN(item.external_price) && item.external_price > 0 ? String(item.external_price) : ''}" 
                       placeholder="Prix externe (optionnel)"
                       data-item-id="${item.id}"
                       oninput="handleExternalPriceInput(${item.id}, this, event)"
                       onchange="handleExternalPriceInput(${item.id}, this, event)">
            </td>
            <td>
                ${(() => {
                // Utiliser external_profit si disponible (depuis la DB), sinon calculer
                if (item.external_profit !== null && item.external_profit !== undefined) {
                    const profit = Number(item.external_profit);
                    const profitClass = profit >= 0 ? 'text-success' : 'text-danger';
                    return `<strong class="${profitClass}">${formatCurrency(profit)}</strong>`;
                }
                const externalPrice = item.external_price ? Number(item.external_price) : null;
                const quantity = Number(item.quantity) || 0;
                const total = Number(item.total) || 0;
                if (externalPrice !== null && externalPrice > 0) {
                    const profit = total - (externalPrice * quantity);
                    const profitClass = profit >= 0 ? 'text-success' : 'text-danger';
                    return `<strong class="${profitClass}">${formatCurrency(profit)}</strong>`;
                }
                return '<small class="text-muted">-</small>';
            })()}
            </td>
            <td><strong class="row-total">${item.is_gift ? '<span class="badge bg-info text-white">CADEAU</span>' : formatCurrency(item.total)}</strong></td>
            <td class="text-center">
                <div class="form-check form-switch d-inline-block">
                    <input class="form-check-input" type="checkbox" 
                           id="gift_${item.id}" 
                           ${item.is_gift ? 'checked' : ''}
                           onchange="toggleGift(${item.id}, this.checked)"
                           title="Marquer comme cadeau (ne compte pas dans le CA)">
                    <label class="form-check-label" for="gift_${item.id}" title="Cadeau">
                        <i class="bi bi-gift"></i>
                    </label>
                </div>
            </td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="removeInvoiceItem(${item.id})">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>
    `;
    }).join('');
    // Ensure totals reflect any DOM-driven changes
    calculateTotals();
    // Initialiser le drag-and-drop si SortableJS est disponible
    initSortable();
}

// Initialiser SortableJS pour le drag-and-drop des lignes
function initSortable() {
    const tbody = document.getElementById('invoiceItemsBody');
    if (!tbody || typeof Sortable === 'undefined') return;
    // Détruire l'instance précédente si elle existe
    if (tbody._sortableInstance) {
        try { tbody._sortableInstance.destroy(); } catch (e) { }
    }
    tbody._sortableInstance = new Sortable(tbody, {
        animation: 150,
        handle: '.drag-handle',
        ghostClass: 'table-primary',
        chosenClass: 'table-info',
        onEnd: function (evt) {
            // Réordonner invoiceItems selon le nouvel ordre du DOM
            const rows = Array.from(tbody.querySelectorAll('tr[data-item-id]'));
            const newOrder = rows.map(row => Number(row.getAttribute('data-item-id')));
            const reordered = [];
            newOrder.forEach(id => {
                const item = invoiceItems.find(i => i.id === id);
                if (item) reordered.push(item);
            });
            // Ajouter les items qui n'auraient pas été trouvés (sécurité)
            invoiceItems.forEach(item => {
                if (!reordered.includes(item)) reordered.push(item);
            });
            invoiceItems = reordered;
        }
    });
}

function selectProduct(itemId, productId, useBulkPrice = null) {
    // Si useBulkPrice n'est pas spécifié, utiliser le type de prix global
    if (useBulkPrice === null) {
        const priceTypeSelect = document.getElementById('priceTypeSelect');
        useBulkPrice = priceTypeSelect && priceTypeSelect.value === 'wholesale';
    }
    
    const item = invoiceItems.find(i => i.id === itemId);
    let product = products.find(p => String(p.product_id) == String(productId));
    // Fallback: utiliser les derniers résultats de recherche
    if (!product && Array.isArray(window._latestProductResults)) {
        product = window._latestProductResults.find(p => String(p.product_id) == String(productId));
    }
    // Si toujours pas trouvé, tenter un chargement ponctuel depuis l'API
    if (!product) {
        try {
            axios.get(`/api/products/${encodeURIComponent(productId)}`).then(({ data }) => {
                if (!data) return;
                // Mettre en cache local
                if (!products.some(p => Number(p.product_id) === Number(data.product_id))) {
                    products.push(data);
                }
                try { if (Array.isArray(data.variants)) productVariantsByProductId.set(Number(data.product_id), data.variants); } catch (e) { }
                // Appliquer la sélection
                selectProduct(itemId, data.product_id, useBulkPrice);
            }).catch(() => {
                // dernier recours: appliquer avec les infos minimales
                if (item) {
                    item.product_id = Number(productId);
                    item.product_name = item.product_name || '';
                    item.unit_price = Number(item.unit_price) || 0;
                    item.total = item.quantity * item.unit_price;
                    item.variant_id = null; item.variant_imei = null;
                    updateInvoiceItemsDisplay();
                    calculateTotals();
                }
            });
        } catch (e) { }
        return;
    }
    if (item && product) {
        const productChanged = Number(item.product_id) !== Number(product.product_id);
        item.product_id = product.product_id;
        item.product_name = product.name;
        // Ne mettre à jour le prix que si le produit a changé (préserver les prix sauvegardés)
        if (productChanged) {
            item.unit_price = Math.round((useBulkPrice && product.wholesale_price) ? Number(product.wholesale_price) : Number(product.price) || 0);
            item.price_type = (useBulkPrice && product.wholesale_price) ? 'wholesale' : 'unit';
        }
        item.total = item.quantity * item.unit_price;
        // Reset variant when product changes
        if (productChanged) {
            item.variant_id = null;
            item.variant_imei = null;
        }
        // S'assurer que le produit est présent dans la liste locale pour l'affichage du <select>
        if (!products.some(p => Number(p.product_id) === Number(product.product_id))) {
            products.push(product);
        }
        // Précharger les variantes pour ce produit si fournies
        try {
            const variants = Array.isArray(product.variants) ? product.variants : [];
            if (variants.length) productVariantsByProductId.set(Number(product.product_id), variants);
        } catch (e) { }
        updateInvoiceItemsDisplay();
        calculateTotals();
    }
}

function togglePriceType(itemId, useBulkPrice) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item || !item.product_id) return;
    // Changement explicite de type de prix: mettre à jour le prix directement
    let product = products.find(p => String(p.product_id) == String(item.product_id));
    if (!product && Array.isArray(window._latestProductResults)) {
        product = window._latestProductResults.find(p => String(p.product_id) == String(item.product_id));
    }
    if (product) {
        item.unit_price = Math.round((useBulkPrice && product.wholesale_price) ? Number(product.wholesale_price) : Number(product.price) || 0);
        item.price_type = (useBulkPrice && product.wholesale_price) ? 'wholesale' : 'unit';
        item.total = item.quantity * item.unit_price;
        updateInvoiceItemsDisplay();
        calculateTotals();
    }
}

function updateCustomName(itemId, name) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    item.product_name = name || '';
}

function renderVariantOptions(productId, selectedVariantId) {
    if (!productId) return '';
    const variants = productVariantsByProductId.get(Number(productId)) || [];
    if (!variants.length) return '';
    // Ne proposer que les variantes disponibles (non vendues)
    return variants.filter(v => !v.is_sold).map(v => `
        <option value="${v.variant_id}" ${String(v.variant_id) === String(selectedVariantId || '') ? 'selected' : ''}>
            ${escapeHtml(v.imei_serial || v.barcode || ('Variante #' + v.variant_id))}
            ${v.condition ? '(' + escapeHtml(String(v.condition).charAt(0).toUpperCase() + String(v.condition).slice(1)) + ')' : ''}
        </option>
    `).join('');
}

function selectVariant(itemId, variantId) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
    const v = variants.find(x => String(x.variant_id) === String(variantId));
    if (v && v.is_sold) { showWarning('Cette variante est déjà vendue'); return; }
    const usedImeis = getUsedImeis(itemId);
    if (v && !usedImeis.has(_normalizeCode(v.imei_serial))) {
        item.scannedImeis = item.scannedImeis || [];
        if (!item.scannedImeis.some(x => _normalizeCode(x) === _normalizeCode(v.imei_serial))) {
            item.scannedImeis.push(v.imei_serial);
            // quantity becomes count of IMEIs
            item.quantity = item.scannedImeis.length;
            item.total = item.quantity * (Number(item.unit_price) || 0);
        }
    } else {
        if (v && usedImeis.has(_normalizeCode(v.imei_serial))) showWarning(`Cette ${MOT('variante').toLowerCase()} est déjà scannée dans la facture`);
    }
    // Keep dropdown option unselected to allow multiple adds
    const selectEl = document.querySelector(`select[onchange="selectVariant(${itemId}, this.value)"]`);
    if (selectEl) selectEl.value = '';
    // Merge same product rows
    mergeProductRows();
    updateInvoiceItemsDisplay();
}

// Retirer une IMEI spécifique déjà scannée dans la ligne (dé-sélection variante)
function removeScannedImeiAt(itemId, index) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    const list = item.scannedImeis || [];
    if (index < 0 || index >= list.length) return;
    list.splice(index, 1);
    item.scannedImeis = list;
    // La ligne portait des IMEI: sa quantité suit forcément le nombre d'unités
    // restantes. (Auparavant conditionné au chargement de la liste des variantes,
    // qui n'est pas garantie en édition: la quantité restait alors périmée.)
    item.quantity = item.scannedImeis.length;
    item.total = item.quantity * (Number(item.unit_price) || 0);
    mergeProductRows();
    updateInvoiceItemsDisplay();
}

// Vider toutes les IMEIs scannées pour la ligne
function clearScannedImeis(itemId) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    const had = (item.scannedImeis || []).length > 0;
    item.scannedImeis = [];
    if (had) {
        item.quantity = 0;
        item.total = 0;
    }
    mergeProductRows();
    updateInvoiceItemsDisplay();
}

/**
 * Sépare une ligne groupée (avec plusieurs IMEI) en lignes individuelles.
 * Chaque IMEI obtient sa propre ligne avec son propre prix modifiable.
 */
function splitVariantRows(itemId) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item || !item.scannedImeis || item.scannedImeis.length <= 1) return;

    const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
    const idx = invoiceItems.indexOf(item);

    // Créer une ligne par IMEI
    const newRows = item.scannedImeis.map((imei, i) => {
        const v = variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(imei));
        // Utiliser le prix de la variante si disponible, sinon le prix unitaire du parent
        let unitPrice = item.unit_price;
        if (v && v.price !== null && v.price !== undefined && Number(v.price) > 0) {
            unitPrice = Math.round(Number(v.price));
        }
        return {
            id: Date.now() + i,
            product_id: item.product_id,
            product_name: item.product_name,
            is_custom: false,
            variant_id: v ? v.variant_id : null,
            variant_imei: imei,
            scannedImeis: [imei],
            quantity: 1,
            unit_price: unitPrice,
            total: unitPrice,
            is_gift: item.is_gift || false,
            external_price: item.external_price || null,
            price_type: item.price_type || 'unit',
            _splitVariant: true, // Empêche la re-fusion par mergeProductRows
        };
    });

    // Remplacer la ligne groupée par les lignes individuelles
    invoiceItems.splice(idx, 1, ...newRows);
    updateInvoiceItemsDisplay();
}

function updateItemQuantity(itemId, quantity) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (item) {
        const allVariants = productVariantsByProductId.get(Number(item.product_id)) || [];
        const hasVariants = allVariants.length > 0;
        const hasScannedImeis = (item.scannedImeis || []).length > 0;
        if (hasVariants && hasScannedImeis) {
            // lock quantity to number of scanned IMEIs for variant products with scanned IMEIs
            item.quantity = item.scannedImeis.length;
        } else {
            // Produit sans variante OU produit avec variantes mais sans IMEI scanné
            const parsed = parseInt(quantity, 10);
            item.quantity = Number.isNaN(parsed) ? 0 : Math.max(0, parsed);
        }
        item.total = item.quantity * item.unit_price;

        updateInvoiceItemsDisplay();
        calculateTotals();
    }
}

function updateItemPrice(itemId, price) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (item) {
        item.unit_price = Math.round(parseInt(price, 10) || 0);
        item.total = item.quantity * item.unit_price;
        // Update DOM in place to avoid re-render focus loss
        try {
            const row = document.querySelector(`tr[data-item-id="${itemId}"]`);
            if (row) {
                const qtyInput = row.querySelector('input[type="number"]');
                const priceInput = row.querySelector('.unit-price-input');
                const totalCell = row.querySelector('.row-total');
                if (qtyInput) qtyInput.value = String(item.quantity);
                if (priceInput) priceInput.value = (item.unit_price).toLocaleString('fr-FR');
                if (totalCell) totalCell.textContent = formatCurrency(item.total);
            }
        } catch (e) { /* ignore */ }
        calculateTotals();
    }
}

// Friendlier price input that preserves focus and caret
function handlePriceInput(itemId, inputEl) {
    if (!inputEl) return;
    const caret = inputEl.selectionStart;
    // sanitize but keep comma and dot
    const digitsOnly = (inputEl.value || '').replace(/\D/g, '');
    inputEl.value = digitsOnly;
    updateItemPrice(itemId, digitsOnly);
    // restore caret position if possible
    try { inputEl.setSelectionRange(caret, caret); } catch (e) { }
}

// Debounce pour éviter les appels multiples
const externalPriceInputTimers = new Map();

function handleExternalPriceInput(itemId, inputEl, event) {
    if (!inputEl || !event) return;

    // Empêcher la propagation pour éviter les déclenchements multiples
    event.stopPropagation();

    // Annuler le timer précédent pour cet item
    if (externalPriceInputTimers.has(itemId)) {
        clearTimeout(externalPriceInputTimers.get(itemId));
    }

    // Sauvegarder la position du curseur
    const caret = inputEl.selectionStart;
    const rawValue = inputEl.value || '';

    // Programmer la mise à jour avec un délai (debounce)
    const timer = setTimeout(() => {
        try {
            const item = invoiceItems.find(i => i.id === itemId);
            if (!item) {
                return;
            }

            // Nettoyer la valeur pour ne garder que les chiffres
            const digitsOnly = rawValue.replace(/\D/g, '');
            const externalPrice = digitsOnly && digitsOnly.length > 0 ? parseFloat(digitsOnly) : null;

            // Mettre à jour le prix externe dans l'objet item seulement si différent
            if (item.external_price !== externalPrice) {
                item.external_price = externalPrice;

                // Mettre à jour la valeur de l'input pour refléter le nettoyage (sans déclencher d'événement)
                if (inputEl.value !== digitsOnly) {
                    // Utiliser une approche plus sûre : créer un nouvel événement pour éviter la boucle
                    const wasFocused = document.activeElement === inputEl;
                    inputEl.value = digitsOnly;
                    if (wasFocused && document.activeElement !== inputEl) {
                        inputEl.focus();
                    }
                    // Restaurer la position du curseur
                    try {
                        const newCaret = Math.min(caret, digitsOnly.length);
                        inputEl.setSelectionRange(newCaret, newCaret);
                    } catch (e) { }
                }

                // Mettre à jour la cellule du bénéfice
                try {
                    const row = document.querySelector(`tr[data-item-id="${itemId}"]`);
                    if (row) {
                        const cells = row.querySelectorAll('td');
                        // La colonne bénéfice est à l'index 7 (après prix externe qui est à l'index 6)
                        if (cells.length > 7) {
                            const profitCell = cells[7];
                            if (profitCell) {
                                const quantity = Number(item.quantity) || 0;
                                const total = Number(item.total) || 0;
                                if (externalPrice !== null && externalPrice > 0 && !isNaN(externalPrice)) {
                                    const profit = total - (externalPrice * quantity);
                                    const profitClass = profit >= 0 ? 'text-success' : 'text-danger';
                                    profitCell.innerHTML = `<strong class="${profitClass}">${formatCurrency(profit)}</strong>`;
                                } else {
                                    profitCell.innerHTML = '<small class="text-muted">-</small>';
                                }
                            }
                        }
                    }
                } catch (e) {
                    // Ignorer les erreurs silencieusement
                }
            }
        } catch (e) {
            console.error('Erreur dans handleExternalPriceInput:', e);
        } finally {
            externalPriceInputTimers.delete(itemId);
        }
    }, 200); // Délai de 200ms pour le debounce

    externalPriceInputTimers.set(itemId, timer);
}

function removeInvoiceItem(itemId) {
    invoiceItems = invoiceItems.filter(i => i.id !== itemId);
    updateInvoiceItemsDisplay();
    calculateTotals();
}

// Réordonner les lignes d'articles (option B: boutons monter/descendre)
function moveItemUp(itemId) {
    const idx = invoiceItems.findIndex(i => i.id === itemId);
    if (idx <= 0) return;
    const tmp = invoiceItems[idx - 1];
    invoiceItems[idx - 1] = invoiceItems[idx];
    invoiceItems[idx] = tmp;
    updateInvoiceItemsDisplay();
}

function moveItemDown(itemId) {
    const idx = invoiceItems.findIndex(i => i.id === itemId);
    if (idx === -1 || idx >= invoiceItems.length - 1) return;
    const tmp = invoiceItems[idx + 1];
    invoiceItems[idx + 1] = invoiceItems[idx];
    invoiceItems[idx] = tmp;
    updateInvoiceItemsDisplay();
}

// Calculer les totaux
function calculateTotals() {
    // Re-read totals from current invoiceItems
    let subtotal = invoiceItems.reduce((sum, item) => sum + (Number(item.total) || 0), 0);

    // Fallback parsing DOM if memory state is empty but rows exist
    if (subtotal === 0 && invoiceItems.length > 0) {
        try {
            const tbody = document.getElementById('invoiceItemsBody');
            const rows = Array.from(tbody?.querySelectorAll('tr') || []);
            subtotal = rows.reduce((acc, tr) => {
                const qty = parseFloat((tr.querySelector('input[type="number"]')?.value || '0').replace(',', '.')) || 0;
                const inputs = tr.querySelectorAll('input[type="number"]');
                const priceInput = inputs.length > 1 ? inputs[1] : null;
                const unit = parseFloat((priceInput?.value || '0').replace(',', '.')) || 0;
                return acc + qty * unit;
            }, 0);
        } catch (e) { }
    }

    const showTax = document.getElementById('showTaxSwitch')?.checked ?? true;
    let taxRateRaw = (document.getElementById('taxRateInput')?.value || '0').toString().replace(',', '.');
    const taxRatePct = parseFloat(taxRateRaw) || 0;
    const rate = showTax ? Math.max(0, taxRatePct) / 100 : 0;
    const taxAmount = subtotal * rate;
    const grossTotal = subtotal + taxAmount; // Total avant échange

    // === CALCUL DE LA DÉDUCTION ÉCHANGE ===
    const invoiceType = document.getElementById('invoiceType') ? document.getElementById('invoiceType').value : 'normal';
    const isExchange = invoiceType === 'exchange';

    let exchangeDeduction = 0;
    if (typeof exchangeItems !== 'undefined' && Array.isArray(exchangeItems)) {
        exchangeDeduction = exchangeItems.reduce((sum, item) => {
            const amount = parseFloat(item.manual_paid_amount) || 0;
            const qty = parseFloat(item.quantity) || 1;
            item.price = amount;
            return sum + (amount * qty);
        }, 0);
    }

    // Pour les factures échange ET normales :
    // total = sous-total + TVA - déduction échange
    // Le champ "Somme totale" est auto-calculé pour les échanges (modifiable manuellement)
    let netTotal;
    if (isExchange) {
        // Auto-calculer : prix des articles neufs - valeur de reprise
        const autoCalculated = Math.max(0, grossTotal - exchangeDeduction);
        const exchangeTotalInput = document.getElementById('exchangeTotalAmount');

        // Mettre à jour le champ avec le calcul automatique
        // (l'utilisateur peut toujours modifier manuellement)
        if (exchangeTotalInput) {
            // Ne JAMAIS écraser si l'utilisateur a modifié OU si le champ a le focus
            const hasFocus = document.activeElement === exchangeTotalInput;
            const isUserModified = exchangeTotalInput.dataset.userModified === 'true';
            if (!isUserModified && !hasFocus) {
                exchangeTotalInput.value = Math.round(autoCalculated);
            }
            // Toujours lire la valeur actuelle du champ (qu'elle soit auto ou manuelle)
            netTotal = parseFloat(exchangeTotalInput.value) || 0;
        } else {
            netTotal = autoCalculated;
        }
    } else {
        netTotal = Math.max(0, grossTotal - exchangeDeduction);
    }

    // Mise à jour de l'affichage
    const exchangeRow = document.getElementById('exchangeDeductionRow');
    const totalLabel = document.getElementById('totalLabel');

    if (isExchange) {
        // Mode échange: afficher le breakdown complet
        const subtotalRow = document.getElementById('subtotalRow');
        const taxRow = document.getElementById('taxRow');
        const taxOptionsRow = document.getElementById('taxOptionsRow');
        const sectionPricesRow = document.getElementById('sectionPricesRow');
        const sectionTotalsRow = document.getElementById('sectionTotalsRow');
        // Afficher sous-total, TVA et options TVA pour la transparence
        if (subtotalRow) subtotalRow.style.display = '';
        if (taxRow) taxRow.style.display = taxAmount > 0 ? '' : 'none';
        if (taxOptionsRow) taxOptionsRow.style.display = '';
        if (sectionPricesRow) sectionPricesRow.style.display = 'none';
        if (sectionTotalsRow) sectionTotalsRow.style.display = 'none';
        // Afficher la ligne de déduction échange
        if (exchangeRow) {
            if (exchangeDeduction > 0) {
                exchangeRow.style.display = '';
                document.getElementById('exchangeDeductionAmount').textContent = '- ' + formatCurrency(exchangeDeduction);
            } else {
                exchangeRow.style.display = 'none';
            }
        }
        if (totalLabel) totalLabel.textContent = 'Total à payer:';
    } else {
        // Mode normal: afficher tout
        const subtotalRow = document.getElementById('subtotalRow');
        const taxRow = document.getElementById('taxRow');
        const taxOptionsRow = document.getElementById('taxOptionsRow');
        const sectionPricesRow = document.getElementById('sectionPricesRow');
        const sectionTotalsRow = document.getElementById('sectionTotalsRow');
        if (subtotalRow) subtotalRow.style.display = '';
        if (taxRow) taxRow.style.display = '';
        if (taxOptionsRow) taxOptionsRow.style.display = '';
        if (sectionPricesRow) sectionPricesRow.style.display = '';
        if (sectionTotalsRow) sectionTotalsRow.style.display = '';
        if (totalLabel) totalLabel.textContent = 'Total:';

        if (exchangeRow) {
            if (exchangeDeduction > 0) {
                exchangeRow.style.display = '';
                document.getElementById('exchangeDeductionAmount').textContent = '- ' + formatCurrency(exchangeDeduction);
            } else {
                exchangeRow.style.display = 'none';
            }
        }
    }

    document.getElementById('subtotal').textContent = formatCurrency(subtotal);
    document.getElementById('taxAmount').textContent = formatCurrency(taxAmount);
    document.getElementById('totalAmount').textContent = formatCurrency(netTotal);

    // Mettre à jour les labels
    const taxLabel = document.getElementById('taxLabel');
    if (taxLabel) taxLabel.textContent = `TVA (${(rate * 100).toFixed(2)}%):`;

    // Store computed numbers
    const form = document.getElementById('invoiceForm');
    if (form) {
        form.dataset.subtotal = String(subtotal);
        form.dataset.taxAmount = String(taxAmount);
        form.dataset.total = String(netTotal);
        form.dataset.exchangeDeduction = String(exchangeDeduction);
    }

    // Trigger renderExchangeItems to update the "Price" column if it's showing calculated values?
    // Attention boucle infinie si render trigger calc. Render ne doit pas trigger calc.
    // Mais updateExchangeItem trigger calc. Donc update de manual_paid_amount -> Calc -> Update item.price.
    // L'input affiche manual_paid_amount, donc pas besoin de refresh visuel de l'input.
    // Mais si on change le PANIER, le prix de reprise change, donc subtotal change -> Calc -> Update item.price. 
    // L'input "Somme ajoutée" reste fixe (ce que veut le client).

    updatePaymentNowMaxAmount();
}

// Sauvegarder une facture
async function saveInvoice(status) {
    try {
        // AVANT TOUT: s'assurer que les prix externes sont bien récupérés des inputs
        // car les événements oninput peuvent ne pas avoir été déclenchés
        try {
            document.querySelectorAll('.external-price-input').forEach(input => {
                const itemId = parseFloat(input.getAttribute('data-item-id'));
                if (itemId) {
                    const item = invoiceItems.find(i => i.id === itemId);
                    if (item) {
                        const rawValue = input.value || '';
                        const digitsOnly = rawValue.replace(/\D/g, '');
                        item.external_price = digitsOnly && digitsOnly.length > 0 ? parseFloat(digitsOnly) : null;
                    }
                }
            });
        } catch (e) { console.warn('Erreur lecture prix externes:', e); }

        // Synchroniser les montants échange depuis les inputs DOM (au cas où onchange n'a pas été déclenché)
        try {
            document.querySelectorAll('#exchangeItemsBody tr[data-item-id]').forEach(row => {
                const itemId = Number(row.getAttribute('data-item-id'));
                if (!itemId) return;
                const item = exchangeItems.find(i => i.id === itemId);
                if (!item) return;
                // Récupérer la somme ajoutée depuis l'input
                const priceInput = row.querySelectorAll('input[type="number"]')[0]; // Premier input number = somme ajoutée
                if (priceInput) {
                    const val = parseFloat(priceInput.value) || 0;
                    item.manual_paid_amount = val;
                    item.price = val;
                }
                // Récupérer le product_name depuis l'input de recherche (si non-custom)
                if (!item.is_custom && !item.product_id) {
                    const searchInput = row.querySelector('.exchange-product-search-input');
                    if (searchInput && searchInput.value.trim()) {
                        item.product_name = searchInput.value.trim();
                    }
                }
                // Récupérer variant_imei ou barcode
                const inputs = row.querySelectorAll('input[type="text"]');
                inputs.forEach(inp => {
                    const ph = (inp.placeholder || '').toLowerCase();
                    if (ph.includes('imei') || ph.includes('série')) {
                        item.variant_imei = inp.value.trim() || null;
                    } else if (ph.includes('code barre')) {
                        item.barcode = inp.value.trim() || null;
                    } else if (ph.includes('notes')) {
                        item.notes = inp.value.trim() || null;
                    }
                });
                // Récupérer quantité
                const qtyInputs = row.querySelectorAll('input[type="number"]');
                if (qtyInputs.length > 1) {
                    item.quantity = parseInt(qtyInputs[1].value) || 1;
                }
            });
        } catch (e) { console.warn('Erreur sync exchange items:', e); }

        // Recalculer les totaux après sync
        try { calculateTotals(); } catch (e) { }

        // UI lock: prevent double submit and indicate progress
        const modalEl = document.getElementById('invoiceModal');
        const footerButtons = modalEl ? modalEl.querySelectorAll('.modal-footer button, .modal-footer a') : [];
        const prevStates = [];
        try { footerButtons.forEach(btn => { prevStates.push([btn, btn.disabled, btn.innerHTML]); btn.disabled = true; }); } catch (e) { }
        document.body.style.cursor = 'wait';
        const invoiceType = document.getElementById('invoiceType')?.value || 'normal';
        const clientSelectValue = document.getElementById('clientSelect')?.value;
        // Construire la date avec l'heure actuelle
        const dateValue = document.getElementById('invoiceDate').value;
        const dateWithTime = dateValue ? new Date(dateValue + 'T' + new Date().toTimeString().split(' ')[0]).toISOString() : new Date().toISOString();

        const invoiceData = {
            invoice_number: document.getElementById('invoiceNumber').value,
            invoice_type: invoiceType,
            client_id: (invoiceType === 'flash_sale' || !clientSelectValue) ? null : parseInt(clientSelectValue),
            date: dateWithTime,
            due_date: document.getElementById('dueDate').value ? new Date(document.getElementById('dueDate').value).toISOString() : null,
            payment_method: (document.getElementById('invoicePaymentMethod') && document.getElementById('invoicePaymentMethod').value) || null,
            notes: (document.getElementById('externalNotes').value || '').trim(), // Use external notes as primary legacy notes
            internal_notes: (document.getElementById('internalNotes').value || '').trim(),
            external_notes: (document.getElementById('externalNotes').value || '').trim(),
            show_tax: document.getElementById('showTaxSwitch')?.checked ?? true,
            tax_rate: parseFloat(document.getElementById('taxRateInput')?.value) || 0,
            show_item_prices: document.getElementById('showSectionPricesSwitch')?.checked ?? true,
            show_section_totals: document.getElementById('showSectionTotalsSwitch')?.checked ?? true,
            subtotal: parseFloat(document.getElementById('invoiceForm').dataset.subtotal || '0'),
            tax_amount: parseFloat(document.getElementById('invoiceForm').dataset.taxAmount || '0'),
            total: parseFloat(document.getElementById('invoiceForm').dataset.total || '0'),
            quotation_id: (function () { try { const v = document.getElementById('invoiceForm').dataset.quotationId; return v ? Number(v) : null; } catch (e) { return null; } })(),
            // Champs de garantie
            has_warranty: document.getElementById('hasWarrantySwitch')?.checked || false,
            warranty_duration: (function () {
                const hasWarranty = document.getElementById('hasWarrantySwitch')?.checked;
                if (!hasWarranty) return null;
                const selectedDuration = document.querySelector('input[name="warrantyDuration"]:checked');
                return selectedDuration ? parseInt(selectedDuration.value) : 12;
            })(),
            exchange_items: invoiceType === 'exchange' ? exchangeItems.map(item => {
                // Sync product_name from search input if still empty (user typed but didn't select)
                if (!item.product_name && !item.is_custom) {
                    const row = document.querySelector(`tr[data-item-id="${item.id}"]`);
                    if (row) {
                        const input = row.querySelector('.exchange-product-search-input');
                        if (input && input.value.trim()) item.product_name = input.value.trim();
                    }
                }
                return {
                product_id: item.product_id,
                product_name: item.product_name || '',
                quantity: item.quantity || 1,
                category_id: item.category_id || null,
                variant_id: item.variant_id || null,
                variant_imei: item.variant_imei || null,
                barcode: item.barcode || null,
                price: item.price || 0,
                notes: item.notes || null
            }; }) : [],
            items: invoiceItems
                .flatMap(item => {
                    // Ligne de section (aucun montant, juste un titre visuel)
                    if (item.is_section) {
                        const rawTitle = String(item.section_title || '').trim();
                        if (!rawTitle) return [];
                        const label = `[SECTION] ${rawTitle}`;
                        return [{
                            product_id: null,
                            product_name: label,
                            quantity: 0,
                            price: 0,
                            total: 0,
                            variant_id: null,
                            external_price: null
                        }];
                    }
                    // Ligne personnalisée sans produit
                    if (!item.product_id && item.is_custom) {
                        return [{
                            product_id: null,
                            product_name: item.product_name || 'Service',
                            quantity: item.quantity || 1,
                            price: item.unit_price || 0,
                            total: item.total || ((item.quantity || 1) * (item.unit_price || 0)),
                            is_gift: item.is_gift || false,
                            variant_id: null,
                            external_price: (() => {
                                const extPrice = item.external_price;
                                if (extPrice === null || extPrice === undefined || extPrice === '') return null;
                                const numPrice = Number(extPrice);
                                return (!isNaN(numPrice) && numPrice > 0) ? numPrice : null;
                            })()
                        }];
                    }
                    if (!item.product_id) return [];
                    const hasImeis = item.scannedImeis && item.scannedImeis.length > 0;
                    if (!hasImeis) {
                        // Débogage: vérifier le prix externe avant envoi
                        const extPrice = item.external_price;
                        const finalExtPrice = (() => {
                            if (extPrice === null || extPrice === undefined || extPrice === '') return null;
                            const numPrice = Number(extPrice);
                            return (!isNaN(numPrice) && numPrice > 0) ? numPrice : null;
                        })();
                        console.log('Envoi item sans IMEI:', {
                            product_id: item.product_id,
                            product_name: item.product_name,
                            external_price_raw: extPrice,
                            external_price_type: typeof extPrice,
                            external_price_final: finalExtPrice
                        });
                        return [{
                            product_id: item.product_id,
                            product_name: item.product_name,
                            quantity: item.quantity,
                            price: item.unit_price,
                            total: item.total,
                            is_gift: item.is_gift || false,
                            variant_id: item.variant_id || null,
                            external_price: finalExtPrice
                        }];
                    }
                    // Expand into one item per IMEI with variant_id resolved
                    const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
                    const extPrice = item.external_price;
                    const finalExtPrice = (() => {
                        if (extPrice === null || extPrice === undefined || extPrice === '') return null;
                        const numPrice = Number(extPrice);
                        return (!isNaN(numPrice) && numPrice > 0) ? numPrice : null;
                    })();
                    console.log('Envoi item avec IMEI:', {
                        product_id: item.product_id,
                        external_price_raw: extPrice,
                        external_price_type: typeof extPrice,
                        external_price_final: finalExtPrice
                    });
                    return item.scannedImeis.map(imei => {
                        const v = variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(imei));
                        return {
                            product_id: item.product_id,
                            // Keep product_name concise to avoid DB limits; IMEI is carried in variant_imei and notes meta
                            product_name: item.product_name,
                            quantity: 1,
                            price: item.unit_price,
                            total: item.unit_price,
                            is_gift: item.is_gift || false,
                            variant_id: v ? v.variant_id : null,
                            variant_imei: imei,
                            external_price: finalExtPrice
                        };
                    });
                })
        };

        // Inject IMEI meta into notes for detail rendering
        try {
            const serialsMeta = invoiceItems
                .filter(i => i.product_id && i.scannedImeis && i.scannedImeis.length)
                .map(i => ({ product_id: i.product_id, imeis: i.scannedImeis }));
            if (serialsMeta.length) {
                const cleaned = (invoiceData.notes || '').replace(/\n?\n?__SERIALS__=.*$/s, '');
                invoiceData.notes = cleaned ? `${cleaned}\n\n__SERIALS__=${JSON.stringify(serialsMeta)}` : `__SERIALS__=${JSON.stringify(serialsMeta)}`;
            }
        } catch (e) { }

        const invoiceId = document.getElementById('invoiceId').value;

        // Validation : client_id obligatoire sauf pour les ventes flash
        const requiresClient = invoiceData.invoice_type !== 'flash_sale';
        if ((requiresClient && !invoiceData.client_id) || !invoiceData.date) {
            showError('Veuillez remplir tous les champs obligatoires');
            return;
        }
        if (invoiceData.items.length === 0) {
            // Création: une facture vide n'a pas de sens.
            if (!invoiceId) {
                showError('Ajoutez au moins un article');
                return;
            }
            // Modification: retirer la dernière ligne est autorisé. La facture est
            // vidée puis annulée, tout revient en stock et le payé est remboursé.
            const paid = Number(document.getElementById('invoiceId').dataset.paidAmount || 0);
            const message = paid > 0
                ? `Vous retirez le dernier article : la facture sera annulée, les produits `
                  + `reviennent en stock et un remboursement de ${formatCurrency(paid)} sera enregistré.\n\nContinuer ?`
                : 'Vous retirez le dernier article : la facture sera annulée et les produits '
                  + 'reviennent en stock.\n\nContinuer ?';
            if (!await confirmDialog(message)) return;
        }

        // Log pour déboguer
        console.log('Items à envoyer:', JSON.stringify(invoiceData.items.map(i => ({
            product_name: i.product_name,
            external_price: i.external_price
        })), null, 2));


        const url = invoiceId ? `/api/invoices/${invoiceId}` : '/api/invoices/';
        const method = invoiceId ? 'PUT' : 'POST';

        // Attach signature data if any
        let signatureDataUrl = null;
        try {
            const fileInput = document.getElementById('signatureFile');
            const canvas = document.getElementById('signatureCanvas');
            if (fileInput && fileInput.files && fileInput.files[0]) {
                const file = fileInput.files[0];
                signatureDataUrl = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result || '')); r.readAsDataURL(file); });
            } else if (canvas) {
                const tmp = document.createElement('canvas'); tmp.width = canvas.width; tmp.height = canvas.height;
                if (canvas.toDataURL() !== tmp.toDataURL()) signatureDataUrl = canvas.toDataURL('image/png');
            }
        } catch (e) { }
        if (!signatureDataUrl && _editingInvoiceRawNotes) {
            // Aucune nouvelle signature saisie: reconduire celle déjà enregistrée
            // (et la géolocalisation associée), que le formulaire ne réaffiche pas.
            ['SIGNATURE', 'LOCATION'].forEach(key => {
                const block = extractNotesRawBlock(_editingInvoiceRawNotes, key);
                if (block) invoiceData.notes = (invoiceData.notes || '') + `\n\n__${key}__=${block}`;
            });
        }
        if (signatureDataUrl) {
            invoiceData.notes = (invoiceData.notes || '') + `\n\n__SIGNATURE__=${signatureDataUrl}`;

            // Capturer la géolocalisation au moment de la signature
            try {
                if (navigator.geolocation) {
                    const pos = await new Promise((resolve, reject) => {
                        navigator.geolocation.getCurrentPosition(resolve, reject, {
                            enableHighAccuracy: true,
                            timeout: 5000,
                            maximumAge: 60000
                        });
                    });
                    const loc = `${pos.coords.latitude},${pos.coords.longitude}`;
                    invoiceData.notes = invoiceData.notes + `\n\n__LOCATION__=${loc}`;
                }
            } catch (geoErr) {
                console.warn('Géolocalisation non disponible:', geoErr.message);
            }
        }

        // Utiliser axios (avec withCredentials configuré globalement) pour éviter les soucis d'auth avec fetch
        let responseData;
        try {
            if (method === 'POST') {
                const { data } = await axios.post(url, invoiceData, { withCredentials: true });
                responseData = data;
            } else {
                const { data } = await axios.put(url, invoiceData, { withCredentials: true });
                responseData = data;
            }
        } catch (err) {
            const msg = err?.response?.data?.detail || err?.message || 'Erreur lors de la sauvegarde';
            throw new Error(msg);
        }

        // Fermer le modal immédiatement après la sauvegarde principale pour une UX réactive
        try {
            const instance = bootstrap.Modal.getInstance(modalEl) || (modalEl ? new bootstrap.Modal(modalEl) : null);
            if (instance) instance.hide();
        } catch (e) { /* ignore */ }

        // Attacher meta IMEIs dans notes pour l'aperçu
        try {
            const serialsMeta = invoiceItems
                .filter(i => i.product_id && i.scannedImeis && i.scannedImeis.length)
                .map(i => ({ product_id: i.product_id, imeis: i.scannedImeis }));
            if (serialsMeta.length) {
                const existingNotes = (document.getElementById('invoiceNotes').value || '').trim();
                const cleaned = existingNotes.replace(/\n?\n?__SERIALS__=.*$/s, '');
                const meta = `__SERIALS__=${JSON.stringify(serialsMeta)}`;
                // patch invoice payload sent earlier
                const payloadObj = JSON.parse(document.getElementById('invoiceForm').dataset.lastPayload || '{}');
                // but since we already sent the request, we keep for next time; also update notes locally
                // no-op if not needed
            }
        } catch (e) { }

        // Paiement immédiat si demandé: on ATTEND le POST paiement
        const doPay = document.getElementById('paymentNowSwitch')?.checked;
        if (doPay) {
            try {
                const inv = responseData || {};
                const invId = inv.invoice_id || inv.id || document.getElementById('invoiceId').value;
                await axios.post(`/api/invoices/${invId || ''}/payments/`, {
                    amount: Math.round(parseFloat(document.getElementById('paymentNowAmount').value || '0')),
                    payment_method: document.getElementById('paymentNowMethod').value,
                    reference: document.getElementById('paymentNowRef').value || null
                });
            } catch (e) { /* ignore payment error but continue */ }
        }

        // Recharger la liste + rafraîchir produits/variantes pour refléter les ventes
        await loadInvoices();
        await loadStats();
        await loadProducts(); // refresh variants availability after deletion restore
        await loadCategories(); // load categories for exchange items

        showSuccess(invoiceId ? 'Facture modifiée avec succès' : 'Facture créée avec succès');
        // Unlock UI using previous states
        try { prevStates.forEach(([btn, prevDisabled, prevHtml]) => { btn.disabled = prevDisabled; if (prevHtml !== undefined) btn.innerHTML = prevHtml; }); } catch (e) { }
        document.body.style.cursor = '';

    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.message || 'Erreur lors de la sauvegarde de la facture');
    } finally {
        // Ensure UI unlock if an error occurred before normal unlock
        const modalEl = document.getElementById('invoiceModal');
        const footerButtons = modalEl ? modalEl.querySelectorAll('.modal-footer button, .modal-footer a') : [];
        try { footerButtons.forEach(btn => { btn.disabled = false; }); } catch (e) { }
        document.body.style.cursor = '';
    }
}

async function saveQuickClient() {
    const name = (document.getElementById('qcName')?.value || '').trim();
    if (!name) { showWarning('Le nom du client est obligatoire'); return; }

    const payload = {
        name,
        phone: (document.getElementById('qcPhone')?.value || '').trim(),
        email: (document.getElementById('qcEmail')?.value || '').trim(),
        address: (document.getElementById('qcAddress')?.value || '').trim()
    };

    try {
        const { data: client } = await axios.post('/api/clients/', payload);
        // Rafraîchir via recherche serveur pour s'assurer de l'affichage immédiat
        try {
            const input = document.getElementById('clientSearch');
            if (input) {
                input.value = client.name || '';
            }
            // Mettre à jour le hidden
            selectClient(client.client_id, client.name);
        } catch (e) { }
        // Optionnel: recharger une courte liste de clients récents
        try {
            const { data } = await axios.get('/api/clients/', { params: { search: client.name, limit: 8 } });
            clients = Array.isArray(data) ? data : (data.items || []);
        } catch (e) { }
        const qm = bootstrap.Modal.getInstance(document.getElementById('clientQuickModal'));
        if (qm) qm.hide();
        showSuccess('Client ajouté');
    } catch (e) {
        console.error('Erreur lors de la création du client:', e);
        console.error('Payload envoyé:', payload);
        console.error('Response:', e.response);

        let errorMessage = 'Erreur lors de la création du client';
        if (e.response?.data?.detail) {
            errorMessage = e.response.data.detail;
        } else if (e.message) {
            errorMessage = e.message;
        }

        showError(errorMessage);
    }
}

// Actions sur les factures
function viewInvoice(invoiceId) {
    loadInvoiceDetail(invoiceId).catch(() => showError('Impossible de charger la facture'));
}

function editInvoice(invoiceId) {
    try {
        const detailModalEl = document.getElementById('invoiceDetailModal');
        if (detailModalEl) {
            const existing = bootstrap.Modal.getInstance(detailModalEl) || new bootstrap.Modal(detailModalEl);
            existing.hide();
            if (typeof existing.dispose === 'function') {
                existing.dispose();
            }
            detailModalEl.classList.remove('show');
            detailModalEl.setAttribute('aria-hidden', 'true');
            detailModalEl.style.display = 'none';
            try {
                document.body.classList.remove('modal-open');
                document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            } catch (e) { /* ignore */ }
        }
    } catch (e) { /* ignore */ }

    preloadInvoiceIntoForm(invoiceId).catch((e) => {
        console.error('Erreur chargement facture:', e);
        showError('Impossible de charger la facture pour édition: ' + (e.message || e));
    });
}

function viewQuotation(quotationId) {
    // Naviguer vers la page des devis avec le devis sélectionné
    sessionStorage.setItem('open_quotation_detail_id', quotationId);
    window.location.href = appPath('/quotations');
}

function addPayment(invoiceId) {
    const invoice = invoices.find(i => i.invoice_id === invoiceId);
    if (!invoice) return;
    // If already fully paid, do not allow new payment
    if (String(invoice.status).toUpperCase() === 'PAID') {
        showWarning('Cette facture est déjà payée');
        return;
    }

    document.getElementById('paymentInvoiceId').value = invoiceId;
    // Show summary (paid/remaining)
    const container = document.getElementById('paymentForm');
    const paid = Number(invoice.paid_amount || invoice.paid || 0);
    const total = Number(invoice.total_amount || invoice.total || 0);
    const remaining = Math.max(0, total - paid);
    const remainingInt = Math.floor(remaining);
    let summary = document.getElementById('paymentSummary');
    if (!summary) {
        summary = document.createElement('div');
        summary.id = 'paymentSummary';
        summary.className = 'alert alert-info py-2';
        container.insertBefore(summary, container.firstChild);
    }
    summary.innerHTML = `<div><strong>Déjà payé:</strong> ${formatCurrency(paid)} &nbsp; | &nbsp; <strong>Restant:</strong> ${formatCurrency(remainingInt)}</div>`;
    const paymentAmountInput = document.getElementById('paymentAmount');
    if (paymentAmountInput) {
        // Remplacer le listener pour éviter l'accumulation (cloner le noeud)
        const fresh = paymentAmountInput.cloneNode(true);
        paymentAmountInput.parentNode.replaceChild(fresh, paymentAmountInput);
        fresh.step = '1';
        fresh.value = remainingInt;
        fresh.max = remainingInt;
        fresh.removeAttribute('readonly');
        fresh.removeAttribute('disabled');
        // Empêcher de saisir plus que le restant
        fresh.addEventListener('input', () => _enforceMaxOnInput(fresh));
    }

    const modal = new bootstrap.Modal(document.getElementById('paymentModal'));
    modal.show();
}

async function printInvoice(invoiceId) {
    // Ouvre une pop-up (fenêtre contrôlée) pour l'impression
    const features = [
        'width=980',
        'height=800',
        'menubar=0',
        'toolbar=0',
        'location=0',
        'status=0',
        'scrollbars=1',
        'resizable=1'
    ].join(',');
    const popup = window.open(appPath(`/invoices/print/${invoiceId}`), 'invoice_print_popup', features);
    if (!popup) {
        showWarning('La fenêtre pop-up a été bloquée par le navigateur');
    }
}

// Télécharger la facture en PDF.
// La génération passe par Chromium côté serveur et prend quelques secondes :
// le bouton est donc verrouillé pendant l'appel pour éviter les doubles clics.
async function downloadInvoicePdf(invoiceId, btn) {
    if (!invoiceId) return;
    const original = btn ? btn.innerHTML : null;
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>PDF...';
    }
    try {
        const response = await axios.get(`/invoices/pdf/${invoiceId}`, { responseType: 'blob' });

        // Reprendre le nom de fichier envoyé par le serveur (Facture_FAC-0570.pdf),
        // sinon retomber sur le numéro affiché dans la modale.
        let filename = `Facture_${invoiceId}.pdf`;
        const disposition = response.headers?.['content-disposition'] || '';
        const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
        if (match && match[1]) {
            filename = decodeURIComponent(match[1].trim());
        }

        const url = URL.createObjectURL(response.data);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        // Laisser au navigateur le temps de démarrer le téléchargement avant de libérer l'URL.
        setTimeout(() => URL.revokeObjectURL(url), 10000);
        showSuccess('PDF téléchargé');
    } catch (error) {
        console.error('Erreur téléchargement PDF:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la génération du PDF');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = original;
        }
    }
}

// Générer un bon de livraison à partir d'une facture
async function generateDeliveryNote(invoiceId) {
    if (!invoiceId) return;
    if (!await confirmDialog('Générer un bon de livraison pour cette facture ?')) return;
    try {
        const { data } = await axios.post(`/api/invoices/${invoiceId}/delivery-note/`, {});
        showSuccess('Bon de livraison créé');
        // Ouvrir la page d'impression BL si disponible dans l'app
        try {
            const dnId = data?.delivery_note_id;
            if (dnId) {
                const popup = window.open(appPath(`/delivery-notes/print/${dnId}`), 'delivery_note_print', 'width=980,height=800,scrollbars=1,resizable=1');
                if (!popup) showWarning('La fenêtre pop-up a été bloquée par le navigateur');
            }
        } catch (e) { }
    } catch (error) {
        console.error('Erreur génération BL:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la génération du bon de livraison');
    }
}

async function loadInvoiceDetail(invoiceId) {
    const { data: inv } = await axios.get(`/api/invoices/${invoiceId}`);
    const client = clients.find(c => c.client_id === inv.client_id);
    const body = document.getElementById('invoiceDetailBody');
    // Parse original quotation quantities from notes meta if present
    let quoteQtyByProductId = new Map();
    try {
        (extractNotesMeta(inv.notes, 'QUOTE_QTYS') || []).forEach(e => {
            const pid = Number(e.product_id);
            const qty = Number(e.qty || 0);
            if (!Number.isNaN(pid)) quoteQtyByProductId.set(pid, qty);
        });
    } catch (e) { }
    // Fallback: if not present in notes, fetch the original quotation and compute quantities
    if (!quoteQtyByProductId.size && inv.quotation_id) {
        try {
            const { data: q } = await axios.get(`/api/quotations/${inv.quotation_id}`);
            (q.items || []).forEach(it => {
                const pid = Number(it.product_id);
                const qty = Number(it.quantity || 0);
                if (!Number.isNaN(pid)) {
                    quoteQtyByProductId.set(pid, (quoteQtyByProductId.get(pid) || 0) + qty);
                }
            });
        } catch (e) { }
    }

    const items = (inv.items || []).map(it => `
        <tr>
            <td>${escapeHtml(it.product_name || '')}</td>
            <td class="text-end">${it.quantity}${(() => { try { const q = quoteQtyByProductId.get(Number(it.product_id)); return (typeof q === 'number') ? `<div class=\"text-muted small\">Qté devis: ${q}</div>` : ''; } catch (e) { return ''; } })()}</td>
            <td class="text-end">${formatCurrency(it.price)}</td>
            <td class="text-end">${formatCurrency(it.total)}</td>
        </tr>
    `).join('');
    // Extract serials meta, prefer backend-parsed map if provided
    let serialsMap = new Map();
    try {
        const serverMap = inv.serials_by_product_id || {};
        Object.keys(serverMap).forEach(pid => {
            serialsMap.set(String(pid), Array.isArray(serverMap[pid]) ? serverMap[pid] : []);
        });
    } catch (e) { }
    if (serialsMap.size === 0) {
        try {
            const txt = String(inv.notes || '');
            if (txt.includes('__SERIALS__=')) {
                let sub = txt.split('__SERIALS__=', 1)[1];
                const cut = sub.indexOf('\n__');
                if (cut !== -1) sub = sub.slice(0, cut).trim();
                sub = sub.trim();
                let arr;
                try { arr = JSON.parse(sub); } catch (e) {
                    const m2 = txt.match(/__SERIALS__=(\[.*?\])/s);
                    arr = m2 ? JSON.parse(m2[1]) : [];
                }
                (arr || []).forEach(e => {
                    const pid = String(e.product_id);
                    if (!serialsMap.has(pid)) serialsMap.set(pid, []);
                    (e.imeis || []).forEach(im => serialsMap.get(pid).push(im));
                });
            }
        } catch (e) { }
    }

    // En-tête : le numéro, le montant et l'état d'encaissement sont ce qu'on
    // vient vérifier. Ils étaient noyés dans une pile de « Numéro: / Client: ».
    const _statut = String(inv.status || '').toLowerCase();
    const _etat = _statut.includes('pay') && !_statut.includes('partiel')
        ? { classe: 'fd-statut--paye', icone: 'bi-check-circle', texte: 'Payée' }
        : _statut.includes('partiel')
            ? { classe: 'fd-statut--partiel', icone: 'bi-clock-history', texte: 'Partiellement payée' }
            : { classe: 'fd-statut--attente', icone: 'bi-exclamation-circle', texte: inv.status || 'En attente' };

    const _reste = Number(inv.remaining_amount ?? 0);
    const _lienClient = inv.client_id
        ? `<a href="${appPath('/clients/detail?id=' + inv.client_id)}">${escapeHtml(client ? client.name : (inv.client_name || '-'))}</a>`
        : escapeHtml(inv.client_name || '-');

    body.innerHTML = `
        <div class="fd-entete">
            <div>
                <h3 class="fd-numero">${escapeHtml(inv.invoice_number || '-')}</h3>
                <p class="fd-client">${_lienClient}</p>
                <div class="fd-dates">
                    <span>Émise le ${inv.date ? formatDate(inv.date) : '-'}</span>
                    ${inv.due_date ? `<span>Échéance ${formatDate(inv.due_date)}</span>` : ''}
                    ${inv.invoice_type === 'exchange' ? '<span><i class="bi bi-arrow-left-right"></i> Facture d\'échange</span>' : ''}
                </div>
            </div>
            <div class="fd-montants">
                <div class="fd-total">${formatCurrency(inv.total)}</div>
                ${_reste > 0
                    ? `<div class="fd-reste">Reste à encaisser ${formatCurrency(_reste)}</div>`
                    : ''}
                <span class="fd-statut ${_etat.classe}">
                    <i class="bi ${_etat.icone}"></i>${escapeHtml(_etat.texte)}
                </span>
            </div>
        </div>

        ${inv.external_notes ? `
        <div class="fd-note">
            <h4>Note visible par le client</h4>
            <p>${escapeHtml(inv.external_notes)}</p>
        </div>` : ''}
        ${inv.internal_notes ? `
        <div class="fd-note fd-note--interne">
            <h4>Note interne — non imprimée</h4>
            <p>${escapeHtml(inv.internal_notes)}</p>
        </div>` : ''}

        ${inv.invoice_type === 'exchange' ? '<div class="mb-2"><span class="badge bg-info"><i class="bi bi-arrow-left-right me-1"></i>Facture d\'échange</span></div>' : ''}
        ${(inv.exchange_items && inv.exchange_items.length) ? `
        <div class="mb-3">
            <strong>Produits échangés (reprise):</strong>
            <div class="table-responsive mt-2">
                <table class="table table-sm table-bordered">
                    <thead class="table-light">
                        <tr>
                            <th>Produit</th>
                            <th>Quantité</th>
                            <th>${MOT('identifiant_court')}</th>
                            <th>Somme ajoutée</th>
                            <th>Notes</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${inv.exchange_items.map(ex => `
                            <tr>
                                <td><strong>${escapeHtml(ex.product_name || '-')}</strong></td>
                                <td class="text-center">${ex.quantity || 1}</td>
                                <td>${ex.variant_imei ? `<code>${escapeHtml(ex.variant_imei)}</code>` : '<span class="text-muted">-</span>'}</td>
                                <td class="text-end">${ex.price ? formatCurrency(ex.price) : '<span class="text-muted">-</span>'}</td>
                                <td>${ex.notes ? escapeHtml(ex.notes) : '<span class="text-muted">-</span>'}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        </div>
        ` : ''}
        ${(inv.payments && inv.payments.length) ? `
        <div class=\"mb-2\"><strong>Paiements:</strong> ${inv.payments.length} paiement(s)</div>
        <div class=\"table-responsive\"> 
            <table class=\"table table-sm\"> 
                <thead><tr><th>Date</th><th class=\"text-end\">Montant</th><th>Mode</th><th>Réf.</th><th></th></tr></thead>
                <tbody>
                ${(() => {
                try {
                    const sorted = [...(inv.payments || [])].sort((a, b) => {
                        const da = new Date(a.payment_date || a.date || 0).getTime();
                        const db = new Date(b.payment_date || b.date || 0).getTime();
                        return da - db; // tri chronologique croissant
                    });
                    return sorted.map(p => `
                            <tr>
                                <td>${formatDateTime(p.payment_date || p.date || '-')}</td>
                                <td class=\"text-end\">${formatCurrency(p.amount || 0)}</td>
                                <td>${escapeHtml(p.payment_method || '-')}</td>
                                <td>${escapeHtml(p.reference || '-')}</td>
                                <td class=\"text-end\">
                                    <button type=\"button\" class=\"btn btn-sm btn-outline-danger\" title=\"Supprimer le paiement\" onclick=\"deletePaymentFromInvoiceDetail(${p.payment_id || 0}, ${invoiceId})\">
                                        <i class=\"bi bi-trash\"></i>
                                    </button>
                                </td>
                            </tr>
                        `).join('');
                } catch (e) { return (inv.payments || []).map(() => '').join(''); }
            })()}
                </tbody>
            </table>
        </div>` : ''}
        <hr/>
        <div class="table-responsive">
            <table class="table table-sm">
                <thead><tr>
                    <th>Article</th>
                    <th class="text-end">Qté</th>
                    <th class="text-end">PU</th>
                    ${(() => {
            // Vérifier si l'utilisateur est admin ou manager
            const isAdminOrManager = window.authManager && (window.authManager.isAdmin() || (window.authManager.userData && (window.authManager.userData.role === 'admin' || window.authManager.userData.role === 'manager')));
            return isAdminOrManager ? '<th class="text-end">Prix externe</th><th class="text-end">Bénéfice</th>' : '';
        })()}
                    <th class="text-end">Total</th>
                </tr></thead>
                <tbody>
                ${(() => {
            // Vérifier si l'utilisateur est admin ou manager
            const isAdminOrManager = window.authManager && (window.authManager.isAdmin() || (window.authManager.userData && (window.authManager.userData.role === 'admin' || window.authManager.userData.role === 'manager')));

            const rows = inv.items || [];
            const groups = new Map();
            rows.forEach(it => {
                // Inclure external_price dans la clé pour éviter les incohérences de regroupement
                const key = `${it.product_id}|${Number(it.price || 0)}|${Number(it.external_price || 0)}`;
                const baseName = (it.product_name || '').replace(/\s*\(IMEI:.*\)$/i, '');
                if (!groups.has(key)) {
                    groups.set(key, {
                        name: baseName,
                        product_id: it.product_id,
                        price: Number(it.price || 0),
                        qty: 0,
                        total: 0,
                        imeis: [],
                        imeis_lignes: [],
                        external_price: it.external_price || null,
                        external_profit: 0
                    });
                }
                const g = groups.get(key);
                g.qty += Number(it.quantity || 0);
                g.total += Number(it.total || 0);
                // Agréger le bénéfice externe (on le recalculera à la fin pour être sûr)
                if (it.external_profit !== null && it.external_profit !== undefined) {
                    g.external_profit += Number(it.external_profit || 0);
                }
                // L'IMEI de l'unité vendue, tel qu'enregistré sur la ligne.
                //
                // C'est la source la plus fiable — elle désigne l'appareil
                // précis sorti du stock — et elle couvre la grande majorité des
                // lignes. Elle n'était pourtant pas lue : le détail ne montrait
                // un IMEI que s'il avait été recopié dans les métadonnées des
                // notes ou collé dans le nom du produit, si bien qu'une facture
                // parfaitement renseignée s'affichait sans numéro de série.
                // Volontairement rangé à part de `g.imeis` : ce dernier pilote
                // le réalignement de la quantité et le recalcul du total plus
                // bas. Une ligne de quantité 2 ne portant qu'un IMEI verrait
                // alors son total divisé par deux. Ici on ne veut qu'afficher
                // le numéro de série, pas retoucher les montants.
                try {
                    const imeiLigne = String(it.variant_imei || '').trim();
                    if (imeiLigne && !g.imeis_lignes.includes(imeiLigne)) g.imeis_lignes.push(imeiLigne);
                } catch (e) { }
                // Fallback: parse inline IMEI from product_name when notes meta not present
                try {
                    const m = String(it.product_name || '').match(/\(IMEI:\s*([^\)]+)\)/i);
                    if (m && m[1]) {
                        const imei = String(m[1]).trim();
                        if (imei && !(g.imeis || []).includes(imei)) g.imeis.push(imei);
                    }
                } catch (e) { }
            });
            // Attach IMEIs from notes meta (takes priority)
            groups.forEach(g => {
                const list = serialsMap.get(String(g.product_id)) || [];
                if (list.length) {
                    g.imeis = list;
                    g.qty = list.length; // align qty to count of IMEIs
                    g.total = g.qty * g.price;
                } else if ((g.imeis || []).length) {
                    g.qty = g.imeis.length;
                    g.total = g.qty * g.price;
                }

                // RECALCULER le bénéfice externe pour le groupe afin d'éviter toute incohérence
                // Bénéfice = Total - (Prix Externe * Quantité)
                if (g.external_price !== null && g.external_price !== undefined && g.external_price > 0) {
                    g.external_profit = g.total - (Number(g.external_price) * g.qty);
                } else {
                    g.external_profit = null;
                }

                try {
                    const q = quoteQtyByProductId.get(Number(g.product_id));
                    if (typeof q === 'number') {
                        g.quote_qty = q;
                    }
                } catch (e) { }
            });
            return Array.from(groups.values()).map(g => `
                        <tr>
                            <td>
                                <strong>${escapeHtml(g.name)}</strong>
                                ${(() => {
                    // Les IMEI issus des métadonnées gardent la priorité ; à
                    // défaut on montre ceux enregistrés sur les lignes.
                    const numeros = (g.imeis && g.imeis.length) ? g.imeis : (g.imeis_lignes || []);
                    if (!numeros.length) return '';
                    return `<div class="text-muted small mt-1 fd-imei">${numeros.map(n => `<span class="d-block">${escapeHtml(n)}</span>`).join('')}</div>`;
                })()}
                                ${(() => { try { const p = (products || []).find(pp => Number(pp.product_id) === Number(g.product_id)); return (p && p.description) ? `<div class=\"text-muted small mt-1\" style=\"text-align:justify\">${escapeHtml(p.description)}</div>` : ''; } catch (e) { return ''; } })()}
                            </td>
                            <td class=\"text-end\">${g.qty}${(typeof g.quote_qty === 'number') ? `<div class=\"text-muted small\">Qté devis: ${g.quote_qty}</div>` : ''}</td>
                            <td class=\"text-end\">${formatCurrency(g.price)}</td>
                            ${isAdminOrManager ? `
                                <td class=\"text-end\">${g.external_price ? formatCurrency(g.external_price) : '<span class="text-muted">-</span>'}</td>
                                <td class=\"text-end\">${g.external_profit !== null && g.external_profit !== undefined ? `<strong class="${Number(g.external_profit) >= 0 ? 'text-success' : 'text-danger'}">${formatCurrency(g.external_profit)}</strong>` : '<span class="text-muted">-</span>'}</td>
                            ` : ''}
                            <td class=\"text-end\">${formatCurrency(g.total)}</td>
                        </tr>
                    `).join('');
        })()}
                </tbody>
            </table>
        </div>
        <div class="text-end">
            ${inv.invoice_type === 'exchange' ? `
                ${inv.exchange_discount ? `<div class="text-info"><strong><i class="bi bi-arrow-left-right me-1"></i>Somme ajoutée (reprise):</strong> ${formatCurrency(inv.exchange_discount)}</div>` : ''}
                <div class="fs-5"><strong>Total à payer:</strong> ${formatCurrency(inv.total || 0)}</div>
            ` : `
                <div><strong>Sous-total:</strong> ${formatCurrency(inv.subtotal || 0)}</div>
                ${inv.show_tax ? `<div><strong>TVA (${Number(inv.tax_rate || 0)}%):</strong> ${formatCurrency(inv.tax_amount || 0)}</div>` : ''}
                <div class="fs-5"><strong>Total:</strong> ${formatCurrency(inv.total || 0)}</div>
            `}
            
            ${(() => {
            const isAdminOrManager = window.authManager && (window.authManager.isAdmin() || (window.authManager.userData && (window.authManager.userData.role === 'admin' || window.authManager.userData.role === 'manager')));
            if (isAdminOrManager && inv.total_external_profit !== undefined && inv.total_external_profit !== null) {
                return `
                        <div class="mt-2 pt-2 border-top">
                            <div class="fs-6"><strong>Bénéfice externe total:</strong> <span class="${Number(inv.total_external_profit) >= 0 ? 'text-success' : 'text-danger'}">${formatCurrency(inv.total_external_profit)}</span></div>
                        </div>
                    `;
            }
            return '';
        })()}
        </div>
    `;
    const modalEl = document.getElementById('invoiceDetailModal');
    if (modalEl) {
        modalEl.dataset.invoiceId = String(invoiceId);
        new bootstrap.Modal(modalEl).show();
    }
}

async function deletePaymentFromInvoiceDetail(paymentId, invoiceId) {
    try {
        if (!paymentId || !invoiceId) return;
        if (!await confirmDialog('Supprimer ce paiement ?', { variant: 'danger', confirmLabel: 'Supprimer' })) return;
        await axios.delete(`/api/invoices/${invoiceId}/payments/${paymentId}`);
        showSuccess('Paiement supprimé');
        await loadInvoiceDetail(invoiceId);
    } catch (error) {
        console.error('Erreur suppression paiement:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la suppression du paiement');
    }
}

async function preloadInvoiceIntoForm(invoiceId) {
    // Charger les produits si pas encore chargés
    if (!products || products.length === 0) {
        try {
            const { data } = await axios.get('/api/products/');
            products = data.items || data || [];
        } catch (e) {
            console.warn('Erreur chargement produits:', e);
        }
    }
    const { data: inv } = await axios.get(`/api/invoices/${invoiceId}`);
    openInvoiceModal();

    // Marquer le formulaire en mode édition immédiatement: openInvoiceModal vient
    // de lancer la récupération asynchrone du prochain numéro, qui ne doit pas
    // écraser le numéro de la facture en cours de modification.
    const idElEarly = document.getElementById('invoiceId');
    if (idElEarly) idElEarly.value = invoiceId;

    // Mémoriser les notes d'origine pour reconduire signature/géolocalisation
    _editingInvoiceRawNotes = inv.notes || null;

    // Attendre que le modal soit complètement affiché avant de remplir les champs
    await new Promise(resolve => setTimeout(resolve, 150));

    const titleEl = document.getElementById('invoiceModalTitle');
    const idEl = document.getElementById('invoiceId');
    const numberEl = document.getElementById('invoiceNumber');
    const dateEl = document.getElementById('invoiceDate');
    const dueDateEl = document.getElementById('dueDate');
    const clientSel = document.getElementById('clientSelect');

    if (titleEl) titleEl.innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier la Facture';
    if (idEl) idEl.value = inv.invoice_id;
    if (numberEl) numberEl.value = inv.invoice_number;
    if (dateEl) dateEl.value = (inv.date || '').split('T')[0] || '';
    if (dueDateEl) dueDateEl.value = (inv.due_date || '').split('T')[0] || '';
    if (clientSel) clientSel.value = inv.client_id;
    
    // Charger les notes
    const externalNotes = document.getElementById('externalNotes');
    const internalNotes = document.getElementById('internalNotes');
    if (externalNotes) {
        let rawNotes = inv.external_notes || inv.notes || '';
        // Nettoyer les métadonnées si on utilise l'ancien champ notes
        if (!inv.external_notes && rawNotes) {
            rawNotes = rawNotes.replace(/\n?\n?__(SERIALS|SIGNATURE|QUOTE_QTYS)__=.*$/s, '').trim();
        }
        externalNotes.value = rawNotes;
    }
    if (internalNotes) internalNotes.value = inv.internal_notes || '';

    try {
        const c = (clients || []).find(x => Number(x.client_id) === Number(inv.client_id));
        const input = document.getElementById('clientSearch');
        if (input) input.value = c ? (c.name || '') : (inv.client_name || '');
    } catch (e) { }
    const showTaxSwitch = document.getElementById('showTaxSwitch');
    const taxRateInput = document.getElementById('taxRateInput');
    if (showTaxSwitch) showTaxSwitch.checked = !!inv.show_tax;
    if (taxRateInput) taxRateInput.value = Number(inv.tax_rate || 0);
    // Restaurer l'option d'affichage des prix par article
    const showItemPricesSwitch = document.getElementById('showSectionPricesSwitch');
    if (showItemPricesSwitch) {
        showItemPricesSwitch.checked = inv.show_item_prices !== false;
    }
    // Restaurer l'option d'affichage des totaux par section
    const showSectionTotalsSwitch = document.getElementById('showSectionTotalsSwitch');
    if (showSectionTotalsSwitch) {
        showSectionTotalsSwitch.checked = inv.show_section_totals !== false;
    }

    // Afficher les informations de paiement existants
    const totalAmount = Number(inv.total || inv.total_amount || 0);
    const paidAmount = Number(inv.paid_amount || inv.paid || 0);
    // Mémorisé pour annoncer le montant remboursé si la facture est vidée
    if (idEl) idEl.dataset.paidAmount = String(paidAmount);
    const remainingAmount = Math.max(0, totalAmount - paidAmount);
    const isFullyPaid = String(inv.status).toUpperCase() === 'PAID' || remainingAmount <= 0;

    // Afficher le résumé des paiements s'il y a des paiements existants ou si c'est payé
    if (paidAmount > 0 || isFullyPaid) {
        const paymentInfo = document.getElementById('existingPaymentInfo');
        const paymentSummary = document.getElementById('paymentStatusSummary');

        if (paymentInfo && paymentSummary) {
            paymentSummary.innerHTML = `
                <div><strong>Déjà payé:</strong> ${formatCurrency(paidAmount)}</div>
                <div><strong>Restant:</strong> ${formatCurrency(remainingAmount)}</div>
            `;
            paymentInfo.style.display = 'block';

            // Changer la couleur si entièrement payé
            if (isFullyPaid) {
                paymentInfo.className = 'alert alert-success py-2 mb-3';
                paymentSummary.innerHTML += '<div class="text-success"><i class="bi bi-check-circle"></i> <strong>Facture entièrement payée</strong></div>';
            } else {
                paymentInfo.className = 'alert alert-info py-2 mb-3';
            }
        }
    } else {
        // Cacher les informations de paiement s'il n'y a pas de paiements
        const paymentInfo = document.getElementById('existingPaymentInfo');
        if (paymentInfo) paymentInfo.style.display = 'none';
    }

    // Retrouver l'IMEI vendu sur chaque ligne. La source fiable est désormais
    // item.variant_imei, porté par la ligne elle-même ; le bloc __SERIALS__ des
    // notes ne sert plus que pour les factures antérieures à cette colonne.
    let serialsMap = new Map();
    (inv.items || []).forEach(it => {
        if (!it.product_id || !it.variant_imei) return;
        const pid = String(it.product_id);
        if (!serialsMap.has(pid)) serialsMap.set(pid, []);
        serialsMap.get(pid).push(it.variant_imei);
    });
    if (serialsMap.size === 0) {
        (extractNotesMeta(inv.notes, 'SERIALS') || []).forEach(e => {
            const pid = String(e.product_id);
            if (!serialsMap.has(pid)) serialsMap.set(pid, []);
            (e.imeis || []).forEach(im => serialsMap.get(pid).push(im));
        });
    }

    // Reconstituer les items EN PRÉSERVANT L'ORDRE ORIGINAL
    // On parcourt les items dans l'ordre et on groupe les produits identiques à leur première occurrence
    invoiceItems = [];
    const processedProductIds = new Set();
    const productItemsMap = new Map(); // product_id -> array of raw items

    // D'abord, grouper les items par product_id pour pouvoir fusionner les IMEI
    (inv.items || []).forEach(it => {
        const key = String(it.product_id || '');
        if (key) {
            if (!productItemsMap.has(key)) productItemsMap.set(key, []);
            productItemsMap.get(key).push(it);
        }
    });

    // Maintenant parcourir dans l'ordre original
    (inv.items || []).forEach(it => {
        const pname = String(it.product_name || '');
        const key = String(it.product_id || '');

        // Détecter les sections: product_id null et nom commençant par [SECTION]
        if (!key && pname.startsWith('[SECTION]')) {
            const title = pname.replace(/^\[SECTION\]\s*/, '').trim();
            invoiceItems.push({
                id: Date.now() + Math.random(),
                is_section: true,
                section_title: title || 'Section',
                product_id: null,
                product_name: '',
                variant_id: null,
                variant_imei: null,
                scannedImeis: [],
                quantity: 0,
                unit_price: 0,
                total: 0
            });
            return;
        }

        // Item custom sans product_id (service personnalisé, pas une section)
        if (!key) {
            invoiceItems.push({
                id: Date.now() + Math.random(),
                product_id: it.product_id,
                product_name: it.product_name,
                is_custom: true,
                variant_id: it.variant_id || null,
                variant_imei: it.variant_imei || null,
                scannedImeis: [],
                quantity: it.quantity,
                unit_price: Number(it.price),
                total: Number(it.total),
                external_price: it.external_price || null,
                is_gift: !!it.is_gift
            });
            return;
        }

        // Produit normal: ne traiter qu'à la première occurrence
        if (processedProductIds.has(key)) return;
        processedProductIds.add(key);

        const groupItems = productItemsMap.get(key) || [it];
        const imeiList = serialsMap.get(key) || [];

        // Vérifier si les items du groupe ont un statut cadeau mixte
        const hasAnyGift = groupItems.some(x => !!x.is_gift);
        const allGift = groupItems.every(x => !!x.is_gift);
        const mixedGift = hasAnyGift && !allGift;

        // Si statut cadeau mixte ET plusieurs items avec IMEI → lignes séparées
        if (mixedGift && groupItems.length > 1 && imeiList.length > 0) {
            const variants = productVariantsByProductId.get(Number(it.product_id)) || [];
            groupItems.forEach((gi, idx) => {
                // Trouver l'IMEI correspondant à cet item
                const giImei = gi.variant_imei || (imeiList[idx] || null);
                const v = giImei ? variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(giImei)) : null;
                invoiceItems.push({
                    id: Date.now() + Math.random() + idx,
                    product_id: gi.product_id,
                    product_name: gi.product_name,
                    is_custom: false,
                    variant_id: v ? v.variant_id : null,
                    variant_imei: giImei,
                    scannedImeis: giImei ? [giImei] : [],
                    quantity: 1,
                    unit_price: Number(gi.price),
                    total: Number(gi.total),
                    external_price: gi.external_price || null,
                    external_profit: gi.external_profit ? Number(gi.external_profit) : null,
                    is_gift: !!gi.is_gift,
                    _splitVariant: true
                });
            });
            return;
        }

        const totalQuantity = groupItems.reduce((sum, x) => sum + Number(x.quantity || 0), 0);
        const totalAmount = groupItems.reduce((sum, x) => sum + Number(x.total || 0), 0);

        // Récupérer le prix externe et bénéfice du premier item du groupe (devrait être le même pour tous)
        const firstItem = groupItems[0];
        // Agréger le bénéfice externe si plusieurs items ont le même product_id
        const totalExternalProfit = groupItems.reduce((sum, x) => sum + (Number(x.external_profit || 0)), 0);
        invoiceItems.push({
            id: Date.now() + Math.random(),
            product_id: it.product_id,
            product_name: it.product_name,
            is_custom: false,
            variant_id: null,
            variant_imei: null,
            scannedImeis: [...imeiList],
            quantity: imeiList.length > 0 ? imeiList.length : totalQuantity,
            unit_price: Number(it.price),
            total: totalAmount,
            external_price: firstItem ? (firstItem.external_price || null) : null,
            external_profit: totalExternalProfit !== 0 ? totalExternalProfit : null,
            is_gift: allGift
        });
    });

    // Fallback: si pas de métadonnées IMEI, essayer de parser depuis les noms de produits
    if (serialsMap.size === 0) {
        invoiceItems.forEach(item => {
            const imeis = [];
            // Chercher des IMEI dans les items originaux ayant le même product_id
            (inv.items || []).forEach(it => {
                if (Number(it.product_id) === Number(item.product_id)) {
                    // Parser IMEI depuis le nom du produit si présent
                    try {
                        const match = String(it.product_name || '').match(/\(IMEI:\s*([^\)]+)\)/i);
                        if (match && match[1]) {
                            const imei = String(match[1]).trim();
                            if (imei && !imeis.includes(imei)) {
                                imeis.push(imei);
                            }
                        }
                    } catch (e) { }

                    // Ou utiliser variant_imei si disponible
                    if (it.variant_imei && !imeis.includes(it.variant_imei)) {
                        imeis.push(it.variant_imei);
                    }
                }
            });

            if (imeis.length > 0) {
                item.scannedImeis = imeis;
                item.quantity = imeis.length;
            }
        });
    }

    // Charger les quantités d'origine du devis
    quoteQtyByProductId = new Map();
    try {
        (extractNotesMeta(inv.notes, 'QUOTE_QTYS') || []).forEach(e => {
            const pid = Number(e.product_id);
            const qty = Number(e.qty || 0);
            if (!Number.isNaN(pid)) quoteQtyByProductId.set(pid, qty);
        });
    } catch (e) { }
    if (!quoteQtyByProductId.size && inv.quotation_id) {
        try {
            const { data: q } = await axios.get(`/api/quotations/${inv.quotation_id}`);
            (q.items || []).forEach(it => {
                const pid = Number(it.product_id);
                const qty = Number(it.quantity || 0);
                if (!Number.isNaN(pid)) {
                    quoteQtyByProductId.set(pid, (quoteQtyByProductId.get(pid) || 0) + qty);
                }
            });
        } catch (e) { }
    }

    updateInvoiceItemsDisplay();
    calculateTotals();

    // Définir le type de facture et charger les produits échangés si c'est une facture d'échange
    const invoiceTypeSelect = document.getElementById('invoiceType');
    if (invoiceTypeSelect && inv.invoice_type) {
        invoiceTypeSelect.value = inv.invoice_type;
    }

    // Charger les produits échangés
    if (inv.invoice_type === 'exchange' && inv.exchange_items && Array.isArray(inv.exchange_items)) {
        // Pré-remplir la somme totale avec le total stocké de la facture
        const exchangeTotalInput = document.getElementById('exchangeTotalAmount');
        if (exchangeTotalInput) {
            exchangeTotalInput.value = inv.total || 0;
            // Marquer comme modifié par l'utilisateur pour préserver la valeur en édition
            exchangeTotalInput.dataset.userModified = 'true';
        }

        try {
            if (!Array.isArray(products) || !products.length) { try { loadProducts(); } catch (e) { } }
            if (!Array.isArray(categories) || !categories.length) { try { loadCategories(); } catch (e) { } }
        } catch (e) { }

        Promise.all([waitForProductsLoaded(), waitForCategoriesLoaded()]).then(() => {
            exchangeItems = inv.exchange_items.map((exItem, index) => {
                const pid = exItem.product_id || null;
                const prod = pid ? (products || []).find(p => Number(p.product_id) === Number(pid)) : null;
                const catName = prod?.category ? String(prod.category).trim() : '';
                const cat = catName ? (categories || []).find(c => String(c.name).trim().toLowerCase() === catName.toLowerCase()) : null;
                const catId = cat ? Number(cat.category_id || cat.id) : null;

                return {
                    id: Date.now() + index,
                    product_id: pid,
                    product_name: exItem.product_name || prod?.name || '',
                    is_custom: !pid,
                    category_id: catId,
                    variant_id: exItem.variant_id || null,
                    variant_imei: exItem.variant_imei || '',
                    barcode: prod?.barcode || '',
                    quantity: exItem.quantity || 1,
                    price: exItem.price || 0,
                    manual_paid_amount: exItem.price || 0,
                    notes: exItem.notes || ''
                };
            });
            renderExchangeItems();
            // Restaurer le total sauvegardé et le flag userModified avant calculateTotals
            // car toggleInvoiceType() (appelé après) peut avoir supprimé le flag
            const etInput = document.getElementById('exchangeTotalAmount');
            if (etInput) {
                etInput.value = inv.total || 0;
                etInput.dataset.userModified = 'true';
            }
            calculateTotals();
        });
    } else {
        exchangeItems = [];
        renderExchangeItems();
    }

    // Appeler toggleInvoiceType pour afficher/masquer les sections appropriées
    toggleInvoiceType();

    // Restaurer le flag userModified APRÈS toggleInvoiceType (qui le supprime)
    // pour que calculateTotals() ne puisse pas écraser le total sauvegardé
    if (inv.invoice_type === 'exchange') {
        const exchangeTotalInput = document.getElementById('exchangeTotalAmount');
        if (exchangeTotalInput) {
            exchangeTotalInput.value = inv.total || 0;
            exchangeTotalInput.dataset.userModified = 'true';
        }
    }

    // Préselectionner la méthode de paiement si disponible
    try {
        const pmSel = document.getElementById('invoicePaymentMethod');
        if (pmSel && inv.payment_method) pmSel.value = inv.payment_method;
    } catch (e) { }

    // Charger les données de garantie
    const warrantySwitch = document.getElementById('hasWarrantySwitch');
    const warrantyOptions = document.getElementById('warrantyOptions');
    const warrantyInfo = document.getElementById('warrantyInfo');

    if (warrantySwitch) {
        warrantySwitch.checked = !!inv.has_warranty;

        // Afficher/masquer les options de garantie
        if (warrantyOptions) {
            warrantyOptions.style.display = inv.has_warranty ? 'block' : 'none';
        }
        if (warrantyInfo) {
            warrantyInfo.style.display = inv.has_warranty ? 'block' : 'none';
        }

        // Sélectionner la durée de garantie appropriée
        if (inv.has_warranty && inv.warranty_duration) {
            const durationRadio = document.querySelector(`input[name="warrantyDuration"][value="${inv.warranty_duration}"]`);
            if (durationRadio) {
                durationRadio.checked = true;
            }
        }

        // Afficher les dates de garantie si disponibles
        if (inv.has_warranty && warrantyInfo) {
            let warrantyInfoText = '';
            if (inv.warranty_start_date) {
                warrantyInfoText += `<strong>Début:</strong> ${formatDate(inv.warranty_start_date)} `;
            }
            if (inv.warranty_end_date) {
                warrantyInfoText += `<strong>Fin:</strong> ${formatDate(inv.warranty_end_date)}`;
            }
            if (warrantyInfoText) {
                const warrantyDatesDiv = warrantyInfo.querySelector('.warranty-dates') ||
                    (() => {
                        const div = document.createElement('div');
                        div.className = 'warranty-dates small text-muted mt-2';
                        warrantyInfo.appendChild(div);
                        return div;
                    })();
                warrantyDatesDiv.innerHTML = warrantyInfoText;
            }
        }
    }
}

async function duplicateInvoice(invoiceId) {
    try {
        console.log('[DuplicateInvoice] Début duplication facture:', invoiceId);

        // Récupérer les données de la facture originale
        const response = await axios.get(`/api/invoices/${invoiceId}`);
        const data = response.data;

        console.log('[DuplicateInvoice] Données récupérées:', data);

        // Ouvrir le modal manuellement sans passer par openInvoiceModal qui réinitialise tout
        const modalEl = document.getElementById('invoiceModal');
        if (!modalEl) {
            console.error('[DuplicateInvoice] Modal introuvable');
            showError('Erreur: formulaire de facture introuvable');
            return;
        }

        // Charger les produits si nécessaire
        try { if (!Array.isArray(products) || products.length === 0) { loadProducts().catch(() => { }); } } catch (e) { }

        // Reset le formulaire d'abord
        const formEl = document.getElementById('invoiceForm');
        if (formEl) formEl.reset();

        // Titre du modal
        const titleEl = document.getElementById('invoiceModalTitle');
        if (titleEl) titleEl.innerHTML = '<i class="bi bi-copy me-2"></i>Dupliquer la Facture';

        // Ne pas renseigner l'ID pour créer une nouvelle facture
        const idEl = document.getElementById('invoiceId');
        if (idEl) idEl.value = '';

        // Dates
        const dateEl = document.getElementById('invoiceDate');
        const dueDateEl = document.getElementById('invoiceDueDate') || document.getElementById('dueDate');
        if (dateEl) dateEl.value = new Date().toISOString().split('T')[0];
        if (dueDateEl) dueDateEl.value = new Date().toISOString().split('T')[0];

        // Numéro de facture - récupérer le prochain
        const numberEl = document.getElementById('invoiceNumber');
        if (numberEl) {
            numberEl.value = '';
            numberEl.placeholder = 'Chargement...';
            axios.get('/api/invoices/next-number').then(({ data: numData }) => {
                if (numData && numData.invoice_number) {
                    numberEl.value = numData.invoice_number;
                    numberEl.placeholder = '';
                }
            }).catch(() => {
                numberEl.placeholder = 'Sera généré automatiquement';
            });
        }

        // Renseigner le client
        const clientHidden = document.getElementById('clientSelect');
        const clientInput = document.getElementById('clientSearch');
        if (clientHidden && data.client_id) {
            clientHidden.value = data.client_id;
            console.log('[DuplicateInvoice] Client ID défini:', data.client_id);
        }
        if (clientInput && data.client_name) {
            clientInput.value = data.client_name;
            console.log('[DuplicateInvoice] Client name défini:', data.client_name);
        }

        // Notes
        const extNotesEl = document.getElementById('externalNotes');
        const intNotesEl = document.getElementById('internalNotes');
        if (extNotesEl) extNotesEl.value = data.external_notes || data.notes || '';
        if (intNotesEl) intNotesEl.value = data.internal_notes || '';

        // TVA
        const taxInput = document.getElementById('taxRateInput');
        if (taxInput) taxInput.value = Number(data.tax_rate || 18);
        const showTaxSwitch = document.getElementById('showTaxSwitch');
        if (showTaxSwitch) showTaxSwitch.checked = data.show_tax !== false;

        // Méthode de paiement
        const pmSel = document.getElementById('invoicePaymentMethod');
        if (pmSel && data.payment_method) pmSel.value = data.payment_method;

        // Copier les articles comme lignes personnalisées
        const items = data.items || [];
        console.log('[DuplicateInvoice] Articles à copier:', items);

        invoiceItems = items.map((it, idx) => {
            return {
                id: Date.now() + idx + Math.random(),
                product_id: it.product_id,
                product_name: it.product_name || '',
                variant_id: null,
                quantity: it.quantity || 1,
                unit_price: it.price || 0,
                total: it.total || 0,
                is_custom: true
            };
        });

        console.log('[DuplicateInvoice] invoiceItems après copie:', invoiceItems);

        // Afficher les articles
        updateInvoiceItemsDisplay();
        calculateTotals();

        // Ouvrir le modal
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();

        showSuccess('Formulaire pré-rempli avec les données de la facture.');
    } catch (error) {
        console.error('Erreur lors de la duplication:', error);
        showError('Erreur lors de la duplication de la facture');
    }
}

async function deleteInvoice(invoiceId) {
    if (!await confirmDialog('Êtes-vous sûr de vouloir supprimer cette facture ?', { variant: 'danger', confirmLabel: 'Supprimer' })) {
        return;
    }

    try {
        await axios.delete(`/api/invoices/${invoiceId}`);
        // Optimistic update: retirer la facture localement immédiatement
        try {
            invoices = Array.isArray(invoices) ? invoices.filter(inv => Number(inv.invoice_id) !== Number(invoiceId)) : [];
            filteredInvoices = Array.isArray(filteredInvoices) ? filteredInvoices.filter(inv => Number(inv.invoice_id) !== Number(invoiceId)) : [];
            displayInvoices();
        } catch (e) { }

        await loadInvoices();
        await loadStats();
        await loadProducts(); // refresh variants availability after deletion restore
        showSuccess('Facture supprimée avec succès');

    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la suppression de la facture');
    }
}

// Sauvegarder un paiement
async function savePayment() {
    try {
        const paymentData = {
            invoice_id: parseInt(document.getElementById('paymentInvoiceId').value),
            amount: Math.round(parseFloat(document.getElementById('paymentAmount').value)),
            payment_method: document.getElementById('paymentMethod').value,
            payment_date: document.getElementById('paymentDate').value,
            reference: document.getElementById('paymentReference').value.trim() || null,
            notes: document.getElementById('paymentNotes').value.trim() || null
        };

        if (!paymentData.amount || !paymentData.payment_method || !paymentData.payment_date) {
            showError('Veuillez remplir tous les champs obligatoires');
            return;
        }

        const { data: payRes } = await axios.post(`/api/invoices/${paymentData.invoice_id}/payments`, {
            amount: paymentData.amount,
            payment_method: paymentData.payment_method,
            payment_date: paymentData.payment_date ? `${paymentData.payment_date}T00:00:00` : null,
            reference: paymentData.reference,
            notes: paymentData.notes
        });

        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('paymentModal'));
        modal.hide();

        // Recharger la liste et les stats, et rafraîchir la ligne si visible
        await Promise.all([loadInvoices(), loadStats()]);
        try {
            // Si le détail est ouvert sur cette facture, recharger son contenu
            const modal = document.getElementById('invoiceDetailModal');
            if (modal && modal.classList.contains('show')) {
                const invId = Number(document.getElementById('paymentInvoiceId').value || 0);
                if (invId) await loadInvoiceDetail(invId);
            }
        } catch (e) { }

        showSuccess('Paiement enregistré avec succès');

    } catch (error) {
        console.error('Erreur lors de l\'enregistrement du paiement:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de l\'enregistrement du paiement');
    }
}

// Mettre à jour le montant maximum de paiement immédiat
function _enforceMaxOnInput(input) {
    if (!input || !input.max) return;
    const maxVal = parseFloat(input.max);
    if (isNaN(maxVal)) return;
    const curVal = parseFloat(input.value);
    if (!isNaN(curVal) && curVal > maxVal) {
        input.value = Math.floor(maxVal).toString();
    }
}

function updatePaymentNowMaxAmount() {
    const amountInput = document.getElementById('paymentNowAmount');
    const maxAmountSpan = document.getElementById('maxPaymentAmount');
    const paymentSwitch = document.getElementById('paymentNowSwitch');

    // Ne pas traiter si le switch n'est pas activé
    if (!paymentSwitch || !paymentSwitch.checked || !amountInput) return;

    // Attacher le listener de plafonnement une seule fois
    if (!amountInput._maxEnforced) {
        amountInput.addEventListener('input', () => _enforceMaxOnInput(amountInput));
        amountInput._maxEnforced = true;
    }

    // Si l'utilisateur est en train de modifier le champ, ne pas écraser sa saisie
    if (document.activeElement === amountInput) return;

    const totalAmount = Math.round(parseFloat(document.getElementById('invoiceForm').dataset.total || '0'));
    const invoiceId = document.getElementById('invoiceId').value;

    if (invoiceId) {
        // En édition : utiliser le montant restant depuis les infos affichées
        const paymentInfo = document.getElementById('existingPaymentInfo');
        if (paymentInfo && paymentInfo.style.display !== 'none') {
            const paymentSummary = document.getElementById('paymentStatusSummary');
            if (paymentSummary) {
                const remainingMatch = paymentSummary.innerHTML.match(/Restant:[^\d]*([\d\s,\.]+)\s*(?:FCFA|€|\$)?/i);
                if (remainingMatch) {
                    const cleanAmount = remainingMatch[1].replace(/\s/g, '').replace(',', '.');
                    const remainingAmount = Math.round(parseFloat(cleanAmount));

                    if (!isNaN(remainingAmount)) {
                        amountInput.max = remainingAmount.toString();
                        if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(remainingAmount);
                        if (!amountInput.value || amountInput.value === '0') {
                            amountInput.value = remainingAmount.toString();
                        }
                        return;
                    }
                }
            }
        }
    }

    // Par défaut (nouvelle facture ou pas de paiements existants)
    amountInput.max = totalAmount.toString();
    if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(totalAmount);
    if (!amountInput.value || amountInput.value === '0') {
        amountInput.value = totalAmount.toString();
    }
}

// Envoyer la facture par WhatsApp via n8n
async function sendInvoiceWhatsApp(invoiceId) {
    if (!invoiceId) return;
    try {
        // Récupérer les infos de la facture pour avoir le numéro du client
        const { data: invoice } = await axios.get(`/api/invoices/${invoiceId}`);
        let phone = invoice.client?.phone || '';

        // Vérifier si le numéro est renseigné
        if (!phone || phone.trim() === '') {
            phone = prompt('Le numéro de téléphone du client n\'est pas renseigné.\nVeuillez entrer le numéro WhatsApp (ex: +221771234567):');
            if (!phone || phone.trim() === '') {
                showError('Numéro de téléphone requis pour l\'envoi WhatsApp');
                return;
            }
        }

        // Normaliser le numéro (enlever espaces, tirets)
        phone = phone.replace(/[\s\-\.]/g, '').trim();
        if (!phone.startsWith('+')) {
            phone = '+221' + phone.replace(/^0/, ''); // Défaut Sénégal
        }

        // Appeler l'API n8n pour envoyer
        showSuccess('Envoi en cours via WhatsApp...');
        const response = await axios.post('/api/invoices/send-whatsapp/', {
            invoice_id: invoiceId,
            phone: phone
        });

        if (response.data?.success) {
            showSuccess('Facture envoyée par WhatsApp avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi WhatsApp');
        }
    } catch (error) {
        console.error('Erreur envoi WhatsApp:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par WhatsApp');
    }
}

// Envoyer la facture par Email via n8n
async function sendInvoiceEmail(invoiceId) {
    if (!invoiceId) return;
    try {
        // Récupérer les infos de la facture pour avoir l'email du client
        const { data: invoice } = await axios.get(`/api/invoices/${invoiceId}`);
        let email = invoice.client?.email || '';

        // Vérifier si l'email est renseigné
        if (!email || email.trim() === '') {
            email = prompt('L\'email du client n\'est pas renseigné.\nVeuillez entrer l\'adresse email:');
            if (!email || email.trim() === '') {
                showError('Adresse email requise pour l\'envoi');
                return;
            }
        }

        // Validation basique de l'email
        if (!email.includes('@') || !email.includes('.')) {
            showError('Adresse email invalide');
            return;
        }

        // Appeler l'API n8n pour envoyer
        showSuccess('Envoi en cours par email...');
        const response = await axios.post('/api/invoices/send-email/', {
            invoice_id: invoiceId,
            email: email.trim()
        });

        if (response.data?.success) {
            showSuccess('Facture envoyée par email avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi email');
        }
    } catch (error) {
        console.error('Erreur envoi email:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par email');
    }
}

// Charger et appliquer les méthodes de paiement configurées
async function populatePaymentMethodSelects(selectFirst = false) {
    try {
        // Récupération directe (SQLite via API), pas de cache local
        let methods = await apiStorage.getInvoicePaymentMethods();
        if (!Array.isArray(methods)) methods = [];
        methods = methods.map(v => String(v || '').trim()).filter(v => v.length);
        if (!methods.length) methods = ["Espèces", "Virement bancaire", "Mobile Money", "Chèque", "Carte bancaire"]; // fallback

        const mapToOptions = (arr, withEmpty) => {
            const opts = [];
            if (withEmpty) opts.push('<option value="">Sélectionner un mode</option>');
            arr.forEach(label => {
                // Utiliser l'étiquette comme valeur pour conserver l'affichage côté backend/rapports
                const value = label;
                opts.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
            });
            return opts.join('');
        };

        // Paiement immédiat dans le formulaire de facture
        const nowSel = document.getElementById('paymentNowMethod');
        if (nowSel) {
            nowSel.innerHTML = mapToOptions(methods, false);
            if (selectFirst && methods.length) nowSel.value = methods[0];
            // Forcer un change pour UI réactive
            try { nowSel.dispatchEvent(new Event('change')); } catch (e) { }
        }

        // Modal paiement
        const modalSel = document.getElementById('paymentMethod');
        if (modalSel) {
            modalSel.innerHTML = mapToOptions(methods, true);
            if (selectFirst && !modalSel.value && methods.length) modalSel.value = methods[0];
        }
    } catch (e) {
        // En cas d'erreur on ne bloque pas l'UI, on garde les valeurs par défaut du HTML
        console.warn('Impossible de charger les méthodes de paiement configurées:', e);
    }
}

// ============ GESTION DES FACTURES D'ÉCHANGE ============

let _exchangeTotalDebounce = null;
function onExchangeTotalChanged() {
    // Marquer que l'utilisateur a modifié manuellement le total
    const input = document.getElementById('exchangeTotalAmount');
    if (input) input.dataset.userModified = 'true';
    // Debounce pour éviter que calculateTotals() interfère pendant la saisie
    clearTimeout(_exchangeTotalDebounce);
    _exchangeTotalDebounce = setTimeout(() => calculateTotals(), 300);
}

// Attacher les listeners sur exchangeTotalAmount au chargement
document.addEventListener('DOMContentLoaded', () => {
    const etField = document.getElementById('exchangeTotalAmount');
    if (etField) {
        // Stopper la propagation pour que le listener du form ne capte PAS cet input
        etField.addEventListener('input', (e) => {
            e.stopPropagation();
            etField.dataset.userModified = 'true';
            clearTimeout(_exchangeTotalDebounce);
            _exchangeTotalDebounce = setTimeout(() => calculateTotals(), 400);
        });
        etField.addEventListener('change', (e) => {
            e.stopPropagation();
            etField.dataset.userModified = 'true';
            calculateTotals();
        });
    }
});

function toggleInvoiceType() {
    const typeSelect = document.getElementById('invoiceType');
    const exchangeSection = document.getElementById('exchangeItemsSection');
    const itemsTitle = document.getElementById('itemsSectionTitle');
    const clientSection = document.querySelector('#clientSearch')?.closest('.col-md-6');
    const clientSearchInput = document.getElementById('clientSearch');
    const clientSelectHidden = document.getElementById('clientSelect');

    if (!typeSelect) return;

    const invoiceType = typeSelect.value;
    const isExchange = invoiceType === 'exchange';
    const isFlashSale = invoiceType === 'flash_sale';

    // Réinitialiser le flag de modification manuelle du total échange
    const exchangeTotalInput = document.getElementById('exchangeTotalAmount');
    if (exchangeTotalInput) delete exchangeTotalInput.dataset.userModified;

    // Gérer la section d'échange
    if (exchangeSection) {
        exchangeSection.style.display = isExchange ? 'block' : 'none';
    }

    // Gérer le titre des articles
    if (itemsTitle) {
        const h6 = itemsTitle.querySelector('h6');
        if (h6) {
            h6.textContent = isExchange ? 'Articles (produits donnés au client)' : 'Articles';
        }
    }

    // Gérer le champ client pour les ventes flash
    if (clientSection) {
        if (isFlashSale) {
            clientSection.style.display = 'none';
            // Retirer l'attribut required pour les ventes flash
            if (clientSearchInput) clientSearchInput.removeAttribute('required');
            if (clientSelectHidden) clientSelectHidden.removeAttribute('required');
        } else {
            clientSection.style.display = 'block';
            // Remettre l'attribut required pour les autres types
            if (clientSearchInput) clientSearchInput.setAttribute('required', 'required');
            if (clientSelectHidden) clientSelectHidden.setAttribute('required', 'required');
        }
    }

    // Recalculer les totaux pour mettre à jour l'affichage (masquer/afficher sous-total/TVA)
    calculateTotals();
}

// Garder l'ancienne fonction pour compatibilité
function toggleExchangeMode() {
    toggleInvoiceType();
}

function addExchangeItem() {
    const itemId = Date.now();
    exchangeItems.push({
        id: itemId,
        product_id: null,
        product_name: '',
        is_custom: false,
        category_id: null,
        variant_id: null,
        variant_imei: '',
        barcode: '',
        quantity: 1,
        price: 0,
        manual_paid_amount: 0,
        notes: ''
    });
    // Réinitialiser l'auto-calcul quand on ajoute un article d'échange
    const etInput = document.getElementById('exchangeTotalAmount');
    if (etInput) delete etInput.dataset.userModified;
    renderExchangeItems();
    calculateTotals();
}

function addCustomExchangeItem() {
    const itemId = Date.now();
    exchangeItems.push({
        id: itemId,
        product_id: null,
        product_name: 'Article personnalisé',
        is_custom: true,
        category_id: null,
        variant_id: null,
        variant_imei: '',
        barcode: '',
        quantity: 1,
        price: 0,
        manual_paid_amount: 0,
        notes: ''
    });
    renderExchangeItems();
    calculateTotals();
}

function removeExchangeItem(itemId) {
    exchangeItems = exchangeItems.filter(item => item.id !== itemId);
    // Réinitialiser l'auto-calcul quand on supprime un article d'échange
    const etInput = document.getElementById('exchangeTotalAmount');
    if (etInput) delete etInput.dataset.userModified;
    renderExchangeItems();
    calculateTotals();
}

function renderExchangeItems() {
    const tbody = document.getElementById('exchangeItemsBody');
    if (!tbody) return;

    if (exchangeItems.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Aucun produit échangé</td></tr>';
        return;
    }

    tbody.innerHTML = exchangeItems.map(item => {
        // Get categories (ensure they're loaded)
        const categoryOptions = (categories && categories.length > 0) ? categories.map(c =>
            `<option value="${c.category_id || c.id}" ${Number(item.category_id) === Number(c.category_id || c.id) ? 'selected' : ''}>${c.name} ${c.requires_variants ? '(avec variantes)' : ''}</option>`
        ).join('') : '<option value="">Chargement des catégories...</option>';

        const productOptions = products.map(p =>
            `<option value="${p.product_id}" ${item.product_id === p.product_id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
        ).join('');

        // Get selected category to check if it requires variants
        const selectedCategory = categories.find(c => Number(c.category_id || c.id) === Number(item.category_id));
        const requiresVariants = selectedCategory && selectedCategory.requires_variants;
        // Afficher un champ texte pour les articles personnalisés, sinon un champ de recherche
        const categorySelectHtml = `
                <select class="form-select form-select-sm mt-2" onchange="updateExchangeItem(${item.id}, 'category_id', this.value ? parseInt(this.value) : null)">
                    <option value="">Sélectionner une catégorie...</option>
                    ${categoryOptions}
                </select>
        `;

        const productCell = item.is_custom ? `
            <div>
                <input type="text" class="form-control form-control-sm mb-2" 
                       placeholder="Nom de l'article..."
                       value="${escapeHtml(item.product_name || '')}"
                       onchange="updateExchangeItem(${item.id}, 'product_name', this.value)">
                <select class="form-select form-select-sm" onchange="updateExchangeItem(${item.id}, 'category_id', parseInt(this.value))">
                    <option value="">Sélectionner une catégorie...</option>
                    ${categoryOptions}
                </select>
            </div>
        ` : `
            <div class="position-relative" style="min-width: 20rem;">
                <input type="text" class="form-control form-control-sm exchange-product-search-input"
                       placeholder="Rechercher un produit..."
                       value="${escapeHtml(item.product_name || '')}"
                       data-exchange-item-id="${item.id}"
                       onchange="updateExchangeItem(${item.id}, 'product_name', this.value)" />
                <div class="exchange-product-suggestions list-group position-absolute w-100 d-none" 
                     style="max-height: 300px; overflow-y: auto; z-index: 1050; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                </div>
                ${categorySelectHtml}
            </div>
        `;

        // Initialize manual_paid_amount if missing - utiliser directement le prix de reprise
        if (item.manual_paid_amount === undefined || item.manual_paid_amount === null) {
            item.manual_paid_amount = parseFloat(item.price) || 0;
        }

        // Champ "Somme ajoutée" (ce que le client paie)
        const priceCell = `
            <input type="number" class="form-control form-control-sm" 
                   placeholder="Somme à payer..."
                   min="0" step="1"
                   value="${item.manual_paid_amount !== undefined ? item.manual_paid_amount : ''}"
                   onchange="updateExchangeItem(${item.id}, 'manual_paid_amount', parseFloat(this.value) || 0)">
        `;

        // Variant/IMEI cell or Barcode cell based on category
        const variantBarcodeCell = requiresVariants ? `
            <input type="text" class="form-control form-control-sm" 
                   placeholder="${MOT('identifiant')}"
                   value="${escapeHtml(item.variant_imei || '')}"
                   onchange="updateExchangeItem(${item.id}, 'variant_imei', this.value)">
        ` : `
            <input type="text" class="form-control form-control-sm" 
                   placeholder="Code barre..."
                   value="${escapeHtml(item.barcode || '')}"
                   onchange="updateExchangeItem(${item.id}, 'barcode', this.value)">
        `;

        return `
            <tr data-item-id="${item.id}">
                <td>
                    ${productCell}
                </td>
                <td>
                    ${priceCell}
                </td>
                <td>
                    ${variantBarcodeCell}
                </td>
                <td>
                    <input type="number" class="form-control form-control-sm" 
                           min="1" value="${item.quantity || 1}"
                           onchange="updateExchangeItem(${item.id}, 'quantity', parseInt(this.value) || 1)">
                </td>
                <td>
                    <input type="text" class="form-control form-control-sm" 
                           placeholder="Notes..."
                           value="${escapeHtml(item.notes || '')}"
                           onchange="updateExchangeItem(${item.id}, 'notes', this.value)">
                </td>
                <td>
                    <button class="btn btn-sm btn-outline-danger" onclick="removeExchangeItem(${item.id})">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

function updateExchangeItem(itemId, field, value) {
    const item = exchangeItems.find(i => i.id === itemId);
    if (!item) return;

    if (field === 'product_id') {
        item.product_id = value ? parseInt(value) : null;
        const product = products.find(p => p.product_id === parseInt(value));
        if (product) {
            item.product_name = product.name;
            // Inférer category_id depuis le nom de catégorie du produit
            try {
                const catName = product.category ? String(product.category).trim() : '';
                if (catName) {
                    const cat = (categories || []).find(c => String(c.name).trim().toLowerCase() === catName.toLowerCase());
                    if (cat) item.category_id = Number(cat.category_id || cat.id);
                }
            } catch (e) { }
            // Pré-remplir code-barres si existant
            try {
                if (product.barcode) item.barcode = String(product.barcode);
            } catch (e) { }
        }
    } else if (field === 'product_name') {
        item.product_name = value;
        // Pour les articles personnalisés, s'assurer que product_id reste null
        if (item.is_custom) {
            item.product_id = null;
        }
    } else if (field === 'category_id') {
        // Quand la catégorie change, re-render pour afficher IMEI ou Code barre
        item.category_id = value;
        renderExchangeItems();
        calculateTotals();
        return;
    } else if (field === 'manual_paid_amount') {
        item.manual_paid_amount = value;
        // Réinitialiser l'auto-calcul quand la valeur de reprise change
        const etInput = document.getElementById('exchangeTotalAmount');
        if (etInput) delete etInput.dataset.userModified;
    } else {
        item[field] = value;
    }
    calculateTotals();
}

function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/*
  Ce que cet écran recharge après une écriture.

  Déclaré ici et appelé par la couche HTTP (voir static/js/http.js) : toute
  création, modification ou suppression réussie relance ce chargement, si bien
  que la liste ne montre plus une valeur périmée pendant que la modale, elle,
  est à jour.
*/
window.rafraichirDonnees = function () {
    const taches = [];
    if (typeof loadInvoices === 'function') taches.push(loadInvoices());
    if (typeof loadStats === 'function') taches.push(loadStats());
    return Promise.allSettled(taches);
};
