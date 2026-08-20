// Récapitulatif de Stock
document.addEventListener('DOMContentLoaded', function() {
    loadStockSummary();
});

async function loadStockSummary() {
    try {
        showLoading();
        const response = await axios.get('/api/reports/stock-summary');
        const data = response.data;
        
        // Mettre à jour le résumé
        document.getElementById('totalStockValue').textContent = formatCurrency(data.summary.total_stock_value || 0);
        document.getElementById('totalProfit').textContent = formatCurrency(data.summary.total_potential_profit || 0);
        document.getElementById('totalPurchaseCost').textContent = formatCurrency(data.summary.total_purchase_cost || 0);
        document.getElementById('profitMargin').textContent = (data.summary.profit_margin_percent || 0).toFixed(2) + '%';
        
        // Statistiques produits
        document.getElementById('totalProducts').textContent = data.summary.total_products || 0;
        document.getElementById('productsWithStock').textContent = data.summary.products_with_stock || 0;
        document.getElementById('productsOutOfStock').textContent = data.summary.products_out_of_stock || 0;
        
        // Tableau par catégorie
        const tbody = document.getElementById('categoryTableBody');
        const categories = data.by_category || {};
        
        if (Object.keys(categories).length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Aucune donnée disponible</td></tr>';
        } else {
            tbody.innerHTML = Object.entries(categories)
                .sort((a, b) => b[1].stock_value - a[1].stock_value)
                .map(([category, stats]) => `
                    <tr>
                        <td><strong>${escapeHtml(category)}</strong></td>
                        <td>${stats.products_count || 0}</td>
                        <td>${stats.quantity || 0}</td>
                        <td>${formatCurrency(stats.stock_value || 0)}</td>
                        <td class="text-success"><strong>${formatCurrency(stats.potential_profit || 0)}</strong></td>
                    </tr>
                `).join('');
        }
        
        hideLoading();
    } catch (error) {
        console.error('Erreur lors du chargement du récapitulatif:', error);
        showError('Erreur lors du chargement du récapitulatif de stock');
        hideLoading();
    }
}

// Indicateur de chargement propre à la page. Chaque page définit le sien;
// celui-ci manquait alors que loadStockSummary l'appelle dès sa première ligne:
// l'erreur interrompait tout le chargement et la page restait figée à 0 F CFA,
// sans message d'erreur puisque le catch appelait lui aussi hideLoading().
function showLoading() {
    const tbody = document.getElementById('categoryTableBody');
    if (!tbody) return;
    tbody.innerHTML = `
        <tr>
            <td colspan="5" class="text-center py-4">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Chargement...</span>
                </div>
            </td>
        </tr>`;
}

function hideLoading() {
    // Le tableau est réécrit par loadStockSummary en cas de succès. On ne nettoie
    // donc que le spinner encore présent, c'est-à-dire le cas d'erreur.
    const tbody = document.getElementById('categoryTableBody');
    if (tbody && tbody.querySelector('.spinner-border')) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Aucune donnée disponible</td></tr>';
    }
}

function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
