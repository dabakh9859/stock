// Variables globales
let currentInvoiceId = null;
let invoices = [];
let suppliers = [];
let products = [];
let currentPage = 1;
let itemsPerPage = 20;
let currentFilters = {
    search: '',
    supplier_id: null,
    status: null
};

// Initialisation
function initializeSupplierInvoices() {
    loadSuppliers();
    loadProducts();
    loadInvoices();
    loadSummaryStats();
    setupEventListeners();
    setupFormValidation();
    populateSupplierPaymentMethods();

    // Le formulaire simplifié n'a plus de champs de date
    // Les dates seront gérées automatiquement par le serveur
}

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Recherche en temps réel
    document.getElementById('searchInput').addEventListener('input', debounce(handleSearch, 300));

    // Filtres
    document.getElementById('supplierFilter').addEventListener('change', handleFilterChange);
    document.getElementById('statusFilter').addEventListener('change', handleFilterChange);

    // Formulaire de facture
    document.getElementById('invoiceForm').addEventListener('submit', handleInvoiceFormSubmit);
    document.getElementById('paymentForm').addEventListener('submit', handlePaymentFormSubmit);

    // Calculs automatiques
    // Modal de paiement
    document.getElementById('addPaymentBtn').addEventListener('click', () => {
        if (currentInvoiceId) {
            openPaymentModal(currentInvoiceId);
        }
    });
}

// Validation des formulaires
function setupFormValidation() {
    const invoiceForm = document.getElementById('invoiceForm');
    const paymentForm = document.getElementById('paymentForm');

    invoiceForm.addEventListener('input', function (e) {
        const field = e.target;
        if (field.validity.valid) {
            field.classList.remove('is-invalid');
            field.classList.add('is-valid');
        } else {
            field.classList.remove('is-valid');
            field.classList.add('is-invalid');
        }
    });

    paymentForm.addEventListener('input', function (e) {
        const field = e.target;
        if (field.validity.valid) {
            field.classList.remove('is-invalid');
            field.classList.add('is-valid');
        } else {
            field.classList.remove('is-valid');
            field.classList.add('is-invalid');
        }
    });
}

// Charger les fournisseurs
async function loadSuppliers() {
    try {
        const response = await axios.get('/api/suppliers/');
        suppliers = response.data.suppliers || response.data;

        // Remplir uniquement le filtre (pas le select car on utilise maintenant la recherche)
        const supplierFilter = document.getElementById('supplierFilter');
        supplierFilter.innerHTML = '<option value="">Tous les fournisseurs</option>';

        suppliers.forEach(supplier => {
            const filterOption = new Option(supplier.name, supplier.supplier_id);
            supplierFilter.appendChild(filterOption);
        });

        // Initialiser la recherche de fournisseur
        setupSupplierSearch();
    } catch (error) {
        console.error('Erreur lors du chargement des fournisseurs:', error);
        showError('Erreur lors du chargement des fournisseurs');
    }
}

// Charger et appliquer les méthodes de paiement configurées pour les factures fournisseurs
async function populateSupplierPaymentMethods(selectFirst = false) {
    try {
        let methods = await apiStorage.getInvoicePaymentMethods();
        if (!Array.isArray(methods)) methods = [];
        methods = methods.map(v => String(v || '').trim()).filter(v => v.length);
        if (!methods.length) methods = ["Espèces", "Virement bancaire", "Mobile Money", "Chèque", "Carte bancaire"]; // fallback

        const sel = document.getElementById('paymentMethodSelect');
        if (!sel) return;

        const opts = [];
        opts.push('<option value="">Sélectionner</option>');
        methods.forEach(label => {
            const value = label;
            opts.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
        });
        sel.innerHTML = opts.join('');

        if (selectFirst && methods.length) {
            sel.value = methods[0];
        }
    } catch (e) {
        console.warn('Impossible de charger les méthodes de paiement fournisseurs configurées:', e);
    }
}

// Configurer la recherche de fournisseur
function setupSupplierSearch() {
    const searchInput = document.getElementById('supplierSearch');
    const searchResults = document.getElementById('supplierSearchResults');

    if (!searchInput) return;

    function renderSupplierResults(searchTerm) {
        const term = String(searchTerm || '').toLowerCase().trim();

        const filteredSuppliers = suppliers.filter(supplier => {
            if (!term) return true;
            const name = supplier.name || '';
            const contact = supplier.contact || supplier.contact_person || '';
            return name.toLowerCase().includes(term) ||
                contact.toLowerCase().includes(term);
        });

        if (filteredSuppliers.length === 0) {
            searchResults.innerHTML = `
                <button type="button" class="list-group-item list-group-item-action text-center text-muted">
                    <i class="bi bi-search"></i> Aucun fournisseur trouvé - Cliquez sur "Nouveau" pour créer
                </button>
            `;
        } else {
            searchResults.innerHTML = filteredSuppliers.slice(0, 5).map(supplier => `
                <button type="button" class="list-group-item list-group-item-action" 
                        onclick="selectSupplier(${supplier.supplier_id}, '${escapeHtml(supplier.name)}')"
                        data-supplier-id="${supplier.supplier_id}">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <strong>${escapeHtml(supplier.name)}</strong>
                            ${contact ? `<small class="text-muted d-block">${escapeHtml(contact)}</small>` : ''}
                        </div>
                        ${supplier.phone ? `<small class="text-muted">${escapeHtml(supplier.phone)}</small>` : ''}
                    </div>
                </button>
            `).join('');
        }

        searchResults.style.display = 'block';
    }

    // Recherche en temps réel
    searchInput.addEventListener('input', debounce(function (e) {
        const inputVal = (e && e.target && typeof e.target.value === 'string') ? e.target.value : (searchInput.value || '');
        const searchTerm = inputVal.toLowerCase().trim();

        // Si l'utilisateur retape, la sélection précédente ne doit plus être considérée valide
        const supplierIdInput = document.getElementById('supplierId');
        if (supplierIdInput) supplierIdInput.value = '';

        renderSupplierResults(searchTerm);
    }, 300));

    // Ouvrir les résultats dès le focus/clic, même sans saisie
    function openDropdown() {
        renderSupplierResults(searchInput.value);
    }

    searchInput.addEventListener('focus', openDropdown);
    searchInput.addEventListener('click', openDropdown);

    // Fermer les résultats quand on clique ailleurs
    document.addEventListener('click', function (e) {
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
            searchResults.style.display = 'none';
        }
    });

    // Gérer la sélection avec le clavier
    searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            searchResults.style.display = 'none';
            return;
        }

        if (searchResults.style.display === 'none') return;

        const items = searchResults.querySelectorAll('.list-group-item');
        const activeItem = searchResults.querySelector('.list-group-item.active');
        let currentIndex = Array.from(items).indexOf(activeItem);

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (currentIndex < items.length - 1) {
                if (activeItem) activeItem.classList.remove('active');
                items[currentIndex + 1].classList.add('active');
            } else if (currentIndex === -1 && items.length > 0) {
                items[0].classList.add('active');
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (currentIndex > 0) {
                if (activeItem) activeItem.classList.remove('active');
                items[currentIndex - 1].classList.add('active');
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (activeItem) {
                activeItem.click();
            }
        }
    });
}

// Sélectionner un fournisseur
function selectSupplier(supplierId, supplierName) {
    document.getElementById('supplierId').value = supplierId;
    document.getElementById('supplierSearch').value = supplierName;
    document.getElementById('supplierSearchResults').style.display = 'none';
}

// Ouvrir la modal de création rapide de fournisseur
function openQuickSupplierModal() {
    // Fermer temporairement la modal de facture
    const invoiceModal = bootstrap.Modal.getInstance(document.getElementById('invoiceModal'));
    if (invoiceModal) {
        invoiceModal.hide();
    }

    // Réinitialiser le formulaire
    document.getElementById('quickSupplierForm').reset();

    // Ouvrir la modal de création
    const modal = new bootstrap.Modal(document.getElementById('quickSupplierModal'));
    modal.show();

    // Gérer la soumission du formulaire
    document.getElementById('quickSupplierForm').onsubmit = async function (e) {
        e.preventDefault();
        await createQuickSupplier();
    };
}

// Créer un nouveau fournisseur rapidement
async function createQuickSupplier() {
    const supplierData = {
        name: document.getElementById('quickSupplierName').value,
        contact: document.getElementById('quickSupplierContact').value || null,
        phone: document.getElementById('quickSupplierPhone').value || null,
        email: document.getElementById('quickSupplierEmail').value || null,
        address: document.getElementById('quickSupplierAddress').value || null
    };

    try {
        const response = await axios.post('/api/suppliers/', supplierData);
        const newSupplier = response.data;

        // Ajouter le nouveau fournisseur à la liste
        suppliers.push(newSupplier);

        // Sélectionner automatiquement le nouveau fournisseur
        selectSupplier(newSupplier.supplier_id, newSupplier.name);

        // Fermer la modal de création
        const modal = bootstrap.Modal.getInstance(document.getElementById('quickSupplierModal'));
        modal.hide();

        // Rouvrir la modal de facture
        const invoiceModal = new bootstrap.Modal(document.getElementById('invoiceModal'));
        invoiceModal.show();

        showSuccess('Fournisseur créé avec succès');

        // Recharger la liste des fournisseurs pour mettre à jour le filtre
        loadSuppliers();

    } catch (error) {
        console.error('Erreur lors de la création du fournisseur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la création du fournisseur');
    }
}

// Charger les produits
async function loadProducts() {
    try {
        const response = await axios.get('/api/products/');
        products = response.data.products || response.data;
    } catch (error) {
        console.error('Erreur lors du chargement des produits:', error);
        products = [];
    }
}

// Charger les factures
async function loadInvoices() {
    try {
        showLoading();

        // Construire les paramètres de manière plus simple
        const queryParams = {
            skip: (currentPage - 1) * itemsPerPage,
            limit: itemsPerPage
        };

        // Ajouter les filtres seulement s'ils ont une valeur valide
        if (currentFilters.search && currentFilters.search.trim()) {
            queryParams.search = currentFilters.search.trim();
        }
        if (currentFilters.supplier_id && currentFilters.supplier_id !== 'null' && currentFilters.supplier_id !== null) {
            queryParams.supplier_id = currentFilters.supplier_id;
        }
        if (currentFilters.status && currentFilters.status !== 'null' && currentFilters.status !== null) {
            queryParams.status = currentFilters.status;
        }

        const response = await axios.get('/api/supplier-invoices/', { params: queryParams });
        invoices = response.data.invoices || [];

        displayInvoices();
        updatePagination(response.data.total || 0);
        hideLoading();

    } catch (error) {
        console.error('Erreur lors du chargement des factures:', error);
        hideLoading();
        if (error.response && error.response.status === 401) {
            showError('Vous devez être connecté pour accéder à cette page');
            // Redirection gérée automatiquement par http.js
        } else {
            showError('Erreur lors du chargement des factures: ' + (error.response?.data?.detail || error.message));
        }
    }
}

// Charger les statistiques
async function loadSummaryStats() {
    try {
        const response = await axios.get('/api/supplier-invoices/stats/summary/');
        const stats = response.data;

        document.getElementById('totalInvoices').textContent = stats.total_invoices;
        document.getElementById('pendingInvoices').textContent = stats.pending_invoices;
        document.getElementById('totalAmount').textContent = formatCurrency(stats.total_amount);
        document.getElementById('remainingAmount').textContent = formatCurrency(stats.remaining_amount);

    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Afficher les factures
function displayInvoices() {
    const tbody = document.getElementById('invoicesTableBody');

    if (invoices.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" class="text-center py-4">
                    <i class="bi bi-inbox text-muted" style="font-size: 2rem;"></i>
                    <p class="text-muted mt-2">Aucune facture trouvée</p>
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = invoices.map(invoice => `
        <tr>
            <td>
                <strong>${escapeHtml(invoice.invoice_number)}</strong>
            </td>
            <td>${escapeHtml(invoice.supplier_name || 'N/A')}</td>
            <td>${invoice.invoice_date ? formatDateTime(invoice.invoice_date) : '-'}</td>
            <td>${invoice.due_date ? formatDateTime(invoice.due_date) : '-'}</td>
            <td class="amount-display">${invoice.amount ? formatCurrency(invoice.amount) : '-'}</td>
            <td class="amount-display">${formatCurrency(invoice.paid_amount || 0)}</td>
            <td class="amount-display">${formatCurrency(invoice.remaining_amount || 0)}</td>
            <td>
                <span class="badge ${getStatusBadgeClass(invoice.status)}">
                    ${getStatusLabel(invoice.status)}
                </span>
            </td>
            <td>
                ${invoice.pdf_path ? `
                    <a href="/api/supplier-invoices/pdf/${invoice.invoice_id}" target="_blank" class="btn btn-sm btn-outline-primary" title="Voir PDF">
                        <i class="bi bi-file-pdf"></i>
                    </a>
                ` : '<span class="text-muted">-</span>'}
            </td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-info" onclick="viewInvoice(${invoice.invoice_id})" title="Voir détails">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-outline-primary" onclick="editInvoice(${invoice.invoice_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${invoice.remaining_amount > 0 ? `
                        <button class="btn btn-outline-success" onclick="openPaymentModal(${invoice.invoice_id})" title="Ajouter paiement">
                            <i class="bi bi-credit-card"></i>
                        </button>
                    ` : ''}
                    <button class="btn btn-outline-danger" onclick="deleteInvoice(${invoice.invoice_id})" title="Supprimer">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

// Ouvrir la modal de facture
function openInvoiceModal(invoiceId = null) {
    currentInvoiceId = invoiceId;
    const modal = new bootstrap.Modal(document.getElementById('invoiceModal'));
    const title = document.getElementById('invoiceModalLabel');
    const saveBtn = document.getElementById('saveButtonText');

    if (invoiceId) {
        title.textContent = 'Modifier la facture';
        saveBtn.textContent = 'Mettre à jour';
        // Charger les données après l'affichage du modal
        modal.show();
        setTimeout(() => loadInvoiceForEdit(invoiceId), 100);
    } else {
        title.textContent = 'Nouvelle facture fournisseur';
        saveBtn.textContent = 'Enregistrer';
        resetInvoiceForm();
        modal.show();
    }
}

// Réinitialiser le formulaire de facture
function resetInvoiceForm() {
    document.getElementById('invoiceForm').reset();

    // Supprimer les classes de validation
    document.querySelectorAll('.form-control, .form-select').forEach(field => {
        field.classList.remove('is-valid', 'is-invalid');
    });

    // Réinitialiser les champs
    document.getElementById('supplierSearch').value = '';
    document.getElementById('supplierId').value = '';
    document.getElementById('invoiceNumber').value = '';
    // Date facture: aujourd'hui
    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);
    document.getElementById('invoiceDate').value = todayStr;

    // Date d'échéance: +1 mois par défaut
    const due = new Date(today);
    due.setMonth(due.getMonth() + 1);
    const dueStr = due.toISOString().slice(0, 10);
    document.getElementById('dueDate').value = dueStr;
    document.getElementById('totalAmount').value = '';
    document.getElementById('invoiceNotes').value = '';
    document.getElementById('invoiceImage').value = '';

    // Total calculé automatiquement depuis les lignes
    const totalAmountInput = document.getElementById('totalAmount');
    if (totalAmountInput) {
        totalAmountInput.readOnly = true;
        totalAmountInput.classList.add('bg-light');
        try {
            totalAmountInput.style.backgroundColor = '#e9ecef';
            totalAmountInput.style.cursor = 'not-allowed';
        } catch (e) { /* ignore */ }
    }

    // Vider le conteneur d'articles
    document.getElementById('invoiceItemsContainer').innerHTML = '';
}

// Charger une facture pour édition
async function loadInvoiceForEdit(invoiceId) {
    try {
        console.log('Chargement facture ID:', invoiceId);
        const response = await axios.get(`/api/supplier-invoices/${invoiceId}`);
        const invoice = response.data;
        console.log('Données facture reçues:', invoice);

        // Remplir le fournisseur
        const supplierIdField = document.getElementById('supplierId');
        const supplierSearchField = document.getElementById('supplierSearch');
        if (supplierIdField) supplierIdField.value = invoice.supplier_id || '';
        if (supplierSearchField) supplierSearchField.value = invoice.supplier_name || '';

        // Remplir les champs de base
        const invoiceNumberField = document.getElementById('invoiceNumber');
        if (invoiceNumberField) invoiceNumberField.value = invoice.invoice_number || '';

        // Dates (format YYYY-MM-DD pour input type="date")
        if (invoice.invoice_date) {
            const invoiceDate = new Date(invoice.invoice_date);
            const invoiceDateField = document.getElementById('invoiceDate');
            if (invoiceDateField) invoiceDateField.value = invoiceDate.toISOString().slice(0, 10);
        }
        if (invoice.due_date) {
            const dueDate = new Date(invoice.due_date);
            const dueDateField = document.getElementById('dueDate');
            if (dueDateField) dueDateField.value = dueDate.toISOString().slice(0, 10);
        }

        // Montant total
        const totalAmountField = document.getElementById('totalAmount');
        if (totalAmountField) totalAmountField.value = invoice.amount || invoice.total_amount || '';

        // Notes
        const notesField = document.getElementById('invoiceNotes');
        if (notesField) notesField.value = invoice.notes || '';

        // Charger les articles si disponibles (parse depuis description ou items)
        const itemsContainer = document.getElementById('invoiceItemsContainer');
        if (itemsContainer) {
            itemsContainer.innerHTML = '';

            // Si la facture a des items structurés, les charger
            if (invoice.items && Array.isArray(invoice.items) && invoice.items.length > 0) {
                invoice.items.forEach(item => {
                    addInvoiceItem();
                    const lastCard = itemsContainer.lastElementChild;
                    if (lastCard) {
                        const descField = lastCard.querySelector('.item-description');
                        const qtyField = lastCard.querySelector('.item-quantity');
                        const priceField = lastCard.querySelector('.item-price');
                        const totalField = lastCard.querySelector('.item-total');

                        if (descField) descField.value = item.description || '';
                        if (qtyField) qtyField.value = item.quantity || 1;
                        if (priceField) priceField.value = item.unit_price || 0;
                        if (totalField) {
                            // Calculer le total pour cet item
                            const qty = parseFloat(qtyField?.value || 0);
                            const price = parseFloat(priceField?.value || 0);
                            totalField.value = (qty * price).toFixed(0);
                        }
                    }
                });

                // Recalculer le total de la facture après avoir chargé tous les articles
                const totalAmountInput = document.getElementById('totalAmount');
                if (totalAmountInput) {
                    const itemTotals = Array.from(document.querySelectorAll('#invoiceItemsContainer .item-total'));
                    const sum = itemTotals.reduce((acc, el) => {
                        const n = parseFloat(el.value);
                        return acc + (Number.isFinite(n) ? n : 0);
                    }, 0);
                    totalAmountInput.value = sum > 0 ? String(Math.floor(sum)) : '';
                }
            }
        }

        console.log('Champs remplis avec succès');
    } catch (error) {
        console.error('Erreur lors du chargement de la facture:', error);
        showError('Erreur lors du chargement de la facture');
    }
}

// Ajouter un élément de facture
function addInvoiceItem() {
    const container = document.getElementById('invoiceItemsContainer');
    const itemIndex = container.children.length;

    const itemHtml = `
        <div class="card mb-2" data-item-index="${itemIndex}">
            <div class="card-body">
                <div class="row g-2">
                    <div class="col-md-5">
                        <label class="form-label small">Désignation *</label>
                        <input type="text" class="form-control form-control-sm item-description" placeholder="Description de l'article" required>
                    </div>
                    <div class="col-md-2">
                        <label class="form-label small">Quantité *</label>
                        <input type="number" class="form-control form-control-sm item-quantity" step="1" min="1" value="1" required>
                    </div>
                    <div class="col-md-2">
                        <label class="form-label small">Prix unitaire (FCFA) *</label>
                        <input type="number" class="form-control form-control-sm item-price" step="1" min="0" required>
                    </div>
                    <div class="col-md-2">
                        <label class="form-label small">Total (FCFA)</label>
                        <input type="number" class="form-control form-control-sm item-total" readonly>
                    </div>
                    <div class="col-md-1 d-flex align-items-end">
                        <button type="button" class="btn btn-sm btn-danger" onclick="removeInvoiceItem(this)">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    container.insertAdjacentHTML('beforeend', itemHtml);

    // Ajouter les écouteurs pour calculer le total automatiquement
    const newItem = container.lastElementChild;
    const quantityInput = newItem.querySelector('.item-quantity');
    const priceInput = newItem.querySelector('.item-price');
    const totalInput = newItem.querySelector('.item-total');

    const updateInvoiceTotalFromItems = () => {
        const totalAmountInput = document.getElementById('totalAmount');
        if (!totalAmountInput) return;

        const itemTotals = Array.from(document.querySelectorAll('#invoiceItemsContainer .item-total'));
        const sum = itemTotals.reduce((acc, el) => {
            const n = parseFloat(el.value);
            return acc + (Number.isFinite(n) ? n : 0);
        }, 0);
        totalAmountInput.value = sum > 0 ? String(Math.floor(sum)) : '';

        // Si le total est recalculé, enlever l'état invalide éventuel
        if (sum > 0) {
            totalAmountInput.classList.remove('is-invalid');
        }
    };

    const calculateItemTotal = () => {
        const quantity = parseFloat(quantityInput.value) || 0;
        const price = parseFloat(priceInput.value) || 0;
        totalInput.value = (quantity * price).toFixed(0);
        updateInvoiceTotalFromItems();
    };

    quantityInput.addEventListener('input', calculateItemTotal);
    priceInput.addEventListener('input', calculateItemTotal);

    // Forcer le champ total en lecture seule (calculé automatiquement)
    const totalAmountInput = document.getElementById('totalAmount');
    if (totalAmountInput) {
        totalAmountInput.readOnly = true;
        totalAmountInput.classList.add('bg-light');
        try {
            totalAmountInput.style.backgroundColor = '#e9ecef';
            totalAmountInput.style.cursor = 'not-allowed';
        } catch (e) { /* ignore */ }
    }

    // Initialiser le total de la ligne + total facture
    calculateItemTotal();
}

// Supprimer un élément de facture
function removeInvoiceItem(button) {
    button.closest('.card').remove();

    const totalAmountInput = document.getElementById('totalAmount');
    if (!totalAmountInput) return;

    const itemTotals = Array.from(document.querySelectorAll('#invoiceItemsContainer .item-total'));
    const sum = itemTotals.reduce((acc, el) => {
        const n = parseFloat(el.value);
        return acc + (Number.isFinite(n) ? n : 0);
    }, 0);
    totalAmountInput.value = sum > 0 ? String(Math.floor(sum)) : '';
}

// Gérer le changement de produit
function handleProductChange(select, itemIndex) {
    const productId = parseInt(select.value);
    const product = products.find(p => p.product_id === productId);

    if (product) {
        const row = select.closest('tr');
        row.querySelector('input[name*="[product_name]"]').value = product.name;
        row.querySelector('input[name*="[unit_price]"]').value = product.purchase_price;
        calculateItemTotal(itemIndex);
    }
}

// Calculer le total d'un élément
function calculateItemTotal(itemIndex) {
    const container = document.getElementById('invoiceItems');
    const row = container.children[itemIndex];

    const quantity = parseFloat(row.querySelector('.quantity-input').value) || 0;
    const unitPrice = parseFloat(row.querySelector('.unit-price-input').value) || 0;
    const total = quantity * unitPrice;

    row.querySelector('.total-input').value = total.toFixed(2);

    calculateTotals();
}

// Calculer les totaux
function calculateTotals() {
    const container = document.getElementById('invoiceItems');
    const taxRate = parseFloat(document.getElementById('taxRate').value) || 0;

    let subtotal = 0;

    for (const row of container.children) {
        const total = parseFloat(row.querySelector('.total-input').value) || 0;
        subtotal += total;
    }

    const taxAmount = (subtotal * taxRate) / 100;
    const total = subtotal + taxAmount;

    document.getElementById('subtotalDisplay').textContent = formatCurrency(subtotal);
    document.getElementById('taxDisplay').textContent = formatCurrency(taxAmount);
    document.getElementById('totalDisplay').textContent = formatCurrency(total);
}

// Gérer la soumission du formulaire de facture
async function handleInvoiceFormSubmit(e) {
    e.preventDefault();

    if (!validateInvoiceForm()) {
        return;
    }

    try {
        const formData = collectInvoiceFormData();

        if (currentInvoiceId) {
            await axios.put(`/api/supplier-invoices/${currentInvoiceId}`, formData);
            showSuccess('Facture mise à jour avec succès');
        } else {
            await axios.post('/api/supplier-invoices/', formData);
            showSuccess('Facture créée avec succès');
        }

        const modal = bootstrap.Modal.getInstance(document.getElementById('invoiceModal'));
        modal.hide();

        loadInvoices();
        loadSummaryStats();

    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la sauvegarde');
    }
}

// Valider le formulaire de facture
function validateInvoiceForm() {
    const supplierId = document.getElementById('supplierId');
    const invoiceNumber = document.getElementById('invoiceNumber');
    const invoiceDate = document.getElementById('invoiceDate');
    const totalAmount = document.getElementById('totalAmount');
    const supplierSearch = document.getElementById('supplierSearch');

    let isValid = true;

    // Vérifier le fournisseur
    if (supplierId && supplierSearch && !supplierId.value) {
        const typed = (supplierSearch.value || '').trim().toLowerCase();
        if (typed) {
            const match = (suppliers || []).find(s => String(s?.name || '').trim().toLowerCase() === typed);
            if (match && match.supplier_id != null) {
                supplierId.value = String(match.supplier_id);
            }
        }
    }
    if (!supplierId.value) {
        document.getElementById('supplierSearch').classList.add('is-invalid');
        isValid = false;
    } else {
        document.getElementById('supplierSearch').classList.remove('is-invalid');
    }

    // Vérifier le numéro de facture
    if (!invoiceNumber.value.trim()) {
        invoiceNumber.classList.add('is-invalid');
        isValid = false;
    } else {
        invoiceNumber.classList.remove('is-invalid');
    }

    // Vérifier la date
    if (!invoiceDate.value) {
        invoiceDate.classList.add('is-invalid');
        isValid = false;
    } else {
        invoiceDate.classList.remove('is-invalid');
    }

    // Vérifier le montant total
    if (!totalAmount.value || parseFloat(totalAmount.value) <= 0) {
        totalAmount.classList.add('is-invalid');
        isValid = false;
    } else {
        totalAmount.classList.remove('is-invalid');
    }

    if (!isValid) {
        showError('Veuillez remplir tous les champs obligatoires');
    }

    return isValid;
}

// Collecter les données du formulaire
function collectInvoiceFormData() {
    const formData = new FormData();

    // Informations de base
    formData.append('supplier_id', document.getElementById('supplierId').value);
    formData.append('invoice_number', document.getElementById('invoiceNumber').value.trim());
    formData.append('invoice_date', document.getElementById('invoiceDate').value);
    formData.append('total_amount', document.getElementById('totalAmount').value);

    // Date d'échéance (optionnelle)
    const dueDate = document.getElementById('dueDate').value;
    if (dueDate) {
        formData.append('due_date', dueDate);
    }

    // Notes (optionnelles)
    const notes = document.getElementById('invoiceNotes').value.trim();
    if (notes) {
        formData.append('notes', notes);
    }

    // Photo de la facture (optionnelle)
    const imageFile = document.getElementById('invoiceImage').files[0];
    if (imageFile) {
        formData.append('invoice_image', imageFile);
    }

    // Collecter les articles
    const items = [];
    const itemCards = document.querySelectorAll('#invoiceItemsContainer .card');
    itemCards.forEach((card, index) => {
        const description = card.querySelector('.item-description').value.trim();
        const quantity = parseFloat(card.querySelector('.item-quantity').value) || 0;
        const price = parseFloat(card.querySelector('.item-price').value) || 0;

        if (description && quantity > 0 && price >= 0) {
            items.push({
                description: description,
                quantity: quantity,
                unit_price: price,
                total: quantity * price
            });
        }
    });

    formData.append('items', JSON.stringify(items));

    return formData;
}

// Voir les détails d'une facture
async function viewInvoice(invoiceId) {
    try {
        const response = await axios.get(`/api/supplier-invoices/${invoiceId}`);
        const invoice = response.data;

        currentInvoiceId = invoiceId;

        const content = document.getElementById('invoiceDetailsContent');
        content.innerHTML = `
            <div class="row">
                <div class="col-md-6">
                    <h6>Informations générales</h6>
                    <p><strong>N° Facture :</strong> ${escapeHtml(invoice.invoice_number || 'Non défini')}</p>
                    <p><strong>Fournisseur :</strong> ${escapeHtml(invoice.supplier_name)}</p>
                    <p><strong>Date facture :</strong> ${invoice.invoice_date ? formatDateTime(invoice.invoice_date) : 'Non définie'}</p>
                    <p><strong>Date échéance :</strong> ${invoice.due_date ? formatDateTime(invoice.due_date) : 'Non définie'}</p>
                    <p><strong>Méthode de paiement :</strong> ${invoice.payment_method ? escapeHtml(invoice.payment_method) : 'Non spécifié'}</p>
                    <p><strong>Statut :</strong> 
                        <span class="badge ${getStatusBadgeClass(invoice.status)}">
                            ${getStatusLabel(invoice.status)}
                        </span>
                    </p>
                </div>
                <div class="col-md-6">
                    <h6>Montants</h6>
                    <p><strong>Montant total :</strong> ${invoice.amount ? formatCurrency(invoice.amount) : 'Non défini'}</p>
                    <p><strong>Montant payé :</strong> ${formatCurrency(invoice.paid_amount || 0)}</p>
                    <p><strong>Montant restant :</strong> ${formatCurrency(invoice.remaining_amount || 0)}</p>
                </div>
            </div>
            
            ${invoice.description ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>Description</h6>
                        <p>${escapeHtml(invoice.description)}</p>
                    </div>
                </div>
            ` : ''}
            
            ${invoice.pdf_path ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>Document PDF</h6>
                        <a href="/api/supplier-invoices/pdf/${invoice.invoice_id}" target="_blank" class="btn btn-primary">
                            <i class="bi bi-file-pdf me-2"></i>Voir le PDF de la facture
                        </a>
                        <small class="text-muted d-block mt-1">${escapeHtml(invoice.pdf_filename || 'Document PDF')}</small>
                    </div>
                </div>
            ` : ''}
            
            ${invoice.notes ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>Notes</h6>
                        <p>${escapeHtml(invoice.notes)}</p>
                    </div>
                </div>
            ` : ''}
        `;

        // Afficher/masquer le bouton de paiement
        const addPaymentBtn = document.getElementById('addPaymentBtn');
        addPaymentBtn.style.display = invoice.remaining_amount > 0 ? 'inline-block' : 'none';

        const modal = new bootstrap.Modal(document.getElementById('invoiceDetailsModal'));
        modal.show();

    } catch (error) {
        console.error('Erreur lors du chargement des détails:', error);
        showError('Erreur lors du chargement des détails');
    }
}

// Modifier une facture
function editInvoice(invoiceId) {
    openInvoiceModal(invoiceId);
}

// Supprimer une facture
function deleteInvoice(invoiceId) {
    currentInvoiceId = invoiceId;

    // Trouver la facture pour vérifier si elle a des paiements
    const invoice = invoices.find(inv => inv.invoice_id === invoiceId);
    if (invoice && invoice.paid_amount > 0) {
        // Personnaliser le message de confirmation pour les factures avec paiements
        const modalBody = document.querySelector('#deleteModal .modal-body');
        if (modalBody) {
            modalBody.innerHTML = `
                <p>Êtes-vous sûr de vouloir supprimer cette facture ?</p>
                <p class="text-warning">
                    <i class="bi bi-exclamation-triangle"></i>
                    <strong>Attention :</strong> Cette facture a un montant payé de ${formatCurrency(invoice.paid_amount)}.
                    Ce montant sera automatiquement rétabli dans votre chiffre d'affaires.
                </p>
            `;
        }
    } else {
        // Message standard pour les factures sans paiement
        const modalBody = document.querySelector('#deleteModal .modal-body');
        if (modalBody) {
            modalBody.innerHTML = '<p>Êtes-vous sûr de vouloir supprimer cette facture ?</p>';
        }
    }

    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    if (!currentInvoiceId) return;

    try {
        await axios.delete(`/api/supplier-invoices/${currentInvoiceId}`);

        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteModal'));
        modal.hide();

        showSuccess('Facture supprimée avec succès');
        loadInvoices();
        loadSummaryStats();

        currentInvoiceId = null;

    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la suppression');
    }
}

// Ouvrir la modal de paiement
async function openPaymentModal(invoiceId) {
    try {
        const response = await axios.get(`/api/supplier-invoices/${invoiceId}`);
        const invoice = response.data;

        currentInvoiceId = invoiceId;

        // Remplir les informations de la facture
        const infoDiv = document.getElementById('paymentInvoiceInfo');
        infoDiv.innerHTML = `
            <div class="d-flex justify-content-between">
                <span><strong>Facture :</strong> ${escapeHtml(invoice.invoice_number)}</span>
                <span><strong>Fournisseur :</strong> ${escapeHtml(invoice.supplier_name)}</span>
            </div>
            <div class="d-flex justify-content-between mt-2">
                <span><strong>Total facture :</strong> ${formatCurrency(invoice.amount || invoice.total)}</span>
                <span><strong>Déjà payé :</strong> ${formatCurrency(invoice.paid_amount)}</span>
            </div>
            <div class="d-flex justify-content-between mt-2">
                <span><strong>Restant à payer :</strong></span>
                <span class="text-danger"><strong>${formatCurrency(invoice.remaining_amount)}</strong></span>
            </div>
        `;

        // Préremplir le montant avec le restant dû (entier)
        const remainingInt = Math.floor(invoice.remaining_amount || 0);
        const paymentAmountEl = document.getElementById('paymentAmount');
        paymentAmountEl.step = '1';
        paymentAmountEl.value = remainingInt;
        paymentAmountEl.max = remainingInt;
        paymentAmountEl.addEventListener('input', () => {
            const raw = String(paymentAmountEl.value).replace(',', '.');
            const n = Math.floor(Number(raw));
            paymentAmountEl.value = Number.isFinite(n) && n >= 0 ? String(n) : '';
        });

        // Réinitialiser le formulaire
        const methodSel = document.getElementById('paymentMethodSelect');
        if (methodSel) {
            // Recharger les méthodes configurées (liste de chaînes)
            await populateSupplierPaymentMethods(true);
        }
        document.getElementById('paymentReference').value = '';
        document.getElementById('paymentNotes').value = '';

        const modal = new bootstrap.Modal(document.getElementById('paymentModal'));
        modal.show();

    } catch (error) {
        console.error('Erreur lors de l\'ouverture de la modal de paiement:', error);
        showError('Erreur lors de l\'ouverture de la modal de paiement');
    }
}

// Gérer la soumission du formulaire de paiement
async function handlePaymentFormSubmit(e) {
    e.preventDefault();

    const amount = Math.round(parseFloat(document.getElementById('paymentAmount').value));
    const paymentDate = document.getElementById('paymentDate').value;
    const paymentMethod = document.getElementById('paymentMethodSelect').value;
    const reference = document.getElementById('paymentReference').value;
    const notes = document.getElementById('paymentNotes').value;

    if (!amount || amount <= 0) {
        showError('Veuillez saisir un montant valide');
        return;
    }

    if (!paymentDate) {
        showError('Veuillez saisir une date de paiement');
        return;
    }

    if (!paymentMethod) {
        showError('Veuillez sélectionner une méthode de paiement');
        return;
    }

    try {
        const paymentData = {
            amount: amount,
            payment_date: paymentDate,
            payment_method: paymentMethod,
            reference: reference || null,
            notes: notes || null
        };

        await axios.post(`/api/supplier-invoices/${currentInvoiceId}/payments`, paymentData);

        const modal = bootstrap.Modal.getInstance(document.getElementById('paymentModal'));
        modal.hide();

        showSuccess('Paiement enregistré avec succès');

        // Recharger les données
        loadInvoices();
        loadSummaryStats();

        // Si la modal de détails est ouverte, la rafraîchir
        const detailsModal = bootstrap.Modal.getInstance(document.getElementById('invoiceDetailsModal'));
        if (detailsModal) {
            viewInvoice(currentInvoiceId);
        }

    } catch (error) {
        console.error('Erreur lors de l\'enregistrement du paiement:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'enregistrement du paiement');
    }
}

// Gestion des filtres et recherche
function handleSearch() {
    currentFilters.search = document.getElementById('searchInput').value;
    currentPage = 1;
    loadInvoices();
}

function handleFilterChange() {
    currentFilters.supplier_id = document.getElementById('supplierFilter').value || null;
    currentFilters.status = document.getElementById('statusFilter').value || null;
    currentPage = 1;
    loadInvoices();
}

function clearFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('supplierFilter').value = '';
    document.getElementById('statusFilter').value = '';

    currentFilters = { search: '', supplier_id: null, status: null };
    currentPage = 1;
    loadInvoices();
}

// Pagination
function updatePagination(total) {
    const totalPages = Math.ceil(total / itemsPerPage);
    const paginationContainer = document.getElementById('pagination');

    paginationContainer.innerHTML = '';

    if (totalPages <= 1) return;

    // Bouton précédent
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${currentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage - 1})">Précédent</a>`;
    paginationContainer.appendChild(prevLi);

    // Pages
    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, currentPage + 2);

    for (let i = startPage; i <= endPage; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === currentPage ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="changePage(${i})">${i}</a>`;
        paginationContainer.appendChild(li);
    }

    // Bouton suivant
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${currentPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage + 1})">Suivant</a>`;
    paginationContainer.appendChild(nextLi);
}

function changePage(page) {
    if (page < 1) return;
    currentPage = page;
    loadInvoices();
}

// Fonctions utilitaires
function getStatusBadgeClass(status) {
    const classes = {
        pending: 'bg-warning text-dark',
        partial: 'bg-info',
        paid: 'bg-success',
        overdue: 'bg-danger'
    };
    return classes[status] || 'bg-secondary';
}

function getStatusLabel(status) {
    const labels = {
        pending: 'En attente',
        partial: 'Partiellement payé',
        paid: 'Payé',
        overdue: 'En retard'
    };
    return labels[status] || status;
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'XOF',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(Math.round(amount || 0));
}

function formatDateTime(dateString) {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleDateString('fr-FR', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text || '').replace(/[&<>"']/g, m => map[m]);
}

function showLoading() {
    document.getElementById('invoicesTableBody').innerHTML = `
        <tr>
            <td colspan="9" class="text-center py-4">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Chargement...</span>
                </div>
            </td>
        </tr>
    `;
}

function hideLoading() {
    // Le loading sera masqué par displayInvoices()
}

function showError(message) {
    // Utiliser le système de notification existant
    if (typeof showNotification === 'function') {
        showNotification(message, 'error');
    } else {
        showAlert('Erreur: ' + message);
    }
}

function showSuccess(message) {
    // Utiliser le système de notification existant
    if (typeof showNotification === 'function') {
        showNotification(message, 'success');
    } else {
        showAlert('Succès: ' + message);
    }
}

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}
