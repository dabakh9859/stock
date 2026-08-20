// Variables globales
let supplierId = null;
let supplierData = null;
let invoices = [];

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Récupérer l'ID du fournisseur depuis l'URL
    const urlParams = new URLSearchParams(window.location.search);
    supplierId = urlParams.get('id');
    
    if (!supplierId) {
        showError('ID du fournisseur manquant');
        window.location.href = appPath('/suppliers');
        return;
    }
    
    loadSupplierData();
    loadInvoices();
    setupEventListeners();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Formulaire de modification
    document.getElementById('editForm').addEventListener('submit', handleEditSubmit);
}

// Charger les données du fournisseur
async function loadSupplierData() {
    try {
        const response = await axios.get(`/api/suppliers/${supplierId}`);
        supplierData = response.data;
        
        // Afficher les informations
        document.getElementById('supplierName').textContent = supplierData.name;
        document.getElementById('infoName').textContent = supplierData.name || '-';
        document.getElementById('infoContact').textContent = supplierData.contact_person || '-';
        document.getElementById('infoEmail').textContent = supplierData.email || '-';
        document.getElementById('infoPhone').textContent = supplierData.phone || '-';
        document.getElementById('infoAddress').textContent = supplierData.address || '-';
        document.getElementById('infoCreatedAt').textContent = supplierData.created_at ? formatDateTime(supplierData.created_at) : '-';
        
        // Charger les statistiques
        loadStatistics();
        
    } catch (error) {
        console.error('Erreur lors du chargement du fournisseur:', error);
        showError('Erreur lors du chargement des données du fournisseur');
    }
}

// Charger les statistiques
async function loadStatistics() {
    try {
        const response = await axios.get(`/api/supplier-invoices?supplier_id=${supplierId}&limit=1000`);
        const invoices = response.data.invoices || [];
        
        // Calculer les statistiques
        const totalInvoices = invoices.length;
        const totalAmount = invoices.reduce((sum, inv) => sum + (inv.amount || 0), 0);
        const paidAmount = invoices.reduce((sum, inv) => sum + (inv.paid_amount || 0), 0);
        const remainingAmount = invoices.reduce((sum, inv) => sum + (inv.remaining_amount || 0), 0);
        
        const paidInvoices = invoices.filter(inv => inv.status === 'paid').length;
        const pendingInvoices = invoices.filter(inv => inv.status === 'pending' || inv.status === 'partial').length;
        const overdueInvoices = invoices.filter(inv => inv.status === 'overdue').length;
        
        // Dernière facture
        const lastInvoice = invoices.length > 0 ? invoices[0] : null;
        
        // Taux de paiement
        const paymentRate = totalAmount > 0 ? Math.round((paidAmount / totalAmount) * 100) : 0;
        
        // Afficher les statistiques
        document.getElementById('totalInvoices').textContent = totalInvoices;
        document.getElementById('totalAmount').textContent = formatCurrency(totalAmount);
        document.getElementById('paidAmount').textContent = formatCurrency(paidAmount);
        document.getElementById('remainingAmount').textContent = formatCurrency(remainingAmount);
        
        document.getElementById('statPaidInvoices').textContent = paidInvoices;
        document.getElementById('statPendingInvoices').textContent = pendingInvoices;
        document.getElementById('statOverdueInvoices').textContent = overdueInvoices;
        document.getElementById('statLastInvoice').textContent = lastInvoice ? formatDate(lastInvoice.invoice_date) : '-';
        
        document.getElementById('paymentRateBar').style.width = `${paymentRate}%`;
        document.getElementById('paymentRateText').textContent = `${paymentRate}%`;
        
    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Charger les factures
async function loadInvoices() {
    try {
        const response = await axios.get(`/api/supplier-invoices?supplier_id=${supplierId}&limit=100`);
        invoices = response.data.invoices || [];
        
        renderInvoices();
        
    } catch (error) {
        console.error('Erreur lors du chargement des factures:', error);
        showError('Erreur lors du chargement des factures');
    }
}

// Afficher les factures
function renderInvoices() {
    const tbody = document.getElementById('invoicesTableBody');
    
    if (invoices.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-4">
                    Aucune facture pour ce fournisseur
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = invoices.map(invoice => `
        <tr>
            <td><strong>${escapeHtml(invoice.invoice_number)}</strong></td>
            <td>${formatDate(invoice.invoice_date)}</td>
            <td>${invoice.due_date ? formatDate(invoice.due_date) : '-'}</td>
            <td><strong>${formatCurrency(invoice.amount)}</strong></td>
            <td class="text-success">${formatCurrency(invoice.paid_amount)}</td>
            <td class="text-warning">${formatCurrency(invoice.remaining_amount)}</td>
            <td>
                <span class="badge bg-${getStatusBadgeColor(invoice.status)}">
                    ${getStatusLabel(invoice.status)}
                </span>
            </td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-info" onclick="viewInvoice(${invoice.invoice_id})" title="Voir">
                        <i class="bi bi-eye"></i>
                    </button>
                    ${invoice.pdf_path ? `
                        <a href="${invoice.pdf_path}" target="_blank" class="btn btn-outline-secondary" title="Voir PDF/Image">
                            <i class="bi bi-file-earmark-image"></i>
                        </a>
                    ` : ''}
                    ${invoice.status !== 'paid' ? `
                        <button class="btn btn-outline-success" onclick="addPayment(${invoice.invoice_id})" title="Paiement">
                            <i class="bi bi-credit-card"></i>
                        </button>
                    ` : ''}
                </div>
            </td>
        </tr>
    `).join('');
}

// Ouvrir la modal de modification
function editSupplier() {
    if (!supplierData) return;
    
    document.getElementById('editName').value = supplierData.name || '';
    document.getElementById('editContact').value = supplierData.contact_person || '';
    document.getElementById('editEmail').value = supplierData.email || '';
    document.getElementById('editPhone').value = supplierData.phone || '';
    document.getElementById('editAddress').value = supplierData.address || '';
    
    const modal = new bootstrap.Modal(document.getElementById('editModal'));
    modal.show();
}

// Gérer la soumission du formulaire de modification
async function handleEditSubmit(e) {
    e.preventDefault();
    
    try {
        const data = {
            name: document.getElementById('editName').value.trim(),
            contact_person: document.getElementById('editContact').value.trim() || null,
            email: document.getElementById('editEmail').value.trim() || null,
            phone: document.getElementById('editPhone').value.trim() || null,
            address: document.getElementById('editAddress').value.trim() || null
        };
        
        await axios.put(`/api/suppliers/${supplierId}`, data);
        
        showSuccess('Fournisseur modifié avec succès');
        
        const modal = bootstrap.Modal.getInstance(document.getElementById('editModal'));
        modal.hide();
        
        loadSupplierData();
        
    } catch (error) {
        console.error('Erreur lors de la modification:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la modification');
    }
}

// Supprimer le fournisseur
function deleteSupplier() {
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    try {
        await axios.delete(`/api/suppliers/${supplierId}`);
        
        showSuccess('Fournisseur supprimé avec succès');
        
        setTimeout(() => {
            window.location.href = appPath('/suppliers');
        }, 1000);
        
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la suppression');
    }
}

// Créer une nouvelle facture pour ce fournisseur
function createInvoice() {
    window.location.href = appPath(`/supplier-invoices?supplier_id=${supplierId}`);
}

// Voir les détails d'une facture
function viewInvoice(invoiceId) {
    window.location.href = appPath(`/supplier-invoices?invoice_id=${invoiceId}`);
}

// Ajouter un paiement
function addPayment(invoiceId) {
    window.location.href = appPath(`/supplier-invoices?invoice_id=${invoiceId}&action=payment`);
}

// Obtenir la couleur du badge de statut
function getStatusBadgeColor(status) {
    const colors = {
        'paid': 'success',
        'pending': 'warning',
        'partial': 'info',
        'overdue': 'danger',
        'cancelled': 'secondary'
    };
    return colors[status] || 'secondary';
}

// Obtenir le label du statut
function getStatusLabel(status) {
    const labels = {
        'paid': 'Payé',
        'pending': 'En attente',
        'partial': 'Partiel',
        'overdue': 'En retard',
        'cancelled': 'Annulé'
    };
    return labels[status] || status;
}

// Fonctions utilitaires
function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'XOF',
        maximumFractionDigits: 0
    }).format(amount || 0);
}

function formatDate(dateString) {
    if (!dateString) return '-';
    return new Date(dateString).toLocaleDateString('fr-FR');
}

function formatDateTime(dateString) {
    if (!dateString) return '-';
    return new Date(dateString).toLocaleString('fr-FR');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showSuccess(message) {
    // Utiliser la fonction globale si disponible, sinon alert
    if (typeof window.showSuccess === 'function') {
        window.showSuccess(message);
    } else {
        showAlert(message);
    }
}

function showError(message) {
    // Utiliser la fonction globale si disponible, sinon alert
    if (typeof window.showError === 'function') {
        window.showError(message);
    } else {
        showAlert(message);
    }
}
