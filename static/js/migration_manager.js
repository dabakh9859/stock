// Gestionnaire de migration
let migrations = [];
let filteredMigrations = [];
let currentMigrationId = null;

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Empêcher les appels si non authentifié
    const isAuthenticated = !!(window.authManager && (window.authManager.token || (window.authManager.userData && Object.keys(window.authManager.userData).length)));
    if (!isAuthenticated) return;
    loadMigrations();
    setupEventListeners();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Filtres
    document.getElementById('typeFilter').addEventListener('change', filterMigrations);
    document.getElementById('statusFilter').addEventListener('change', filterMigrations);
    document.getElementById('startDate').addEventListener('change', filterMigrations);
    document.getElementById('endDate').addEventListener('change', filterMigrations);

    // Formulaire d'import
    document.getElementById('dataFile').addEventListener('change', handleFileSelect);
    document.getElementById('migrationType').addEventListener('change', updateFileFormat);
}

// Charger les migrations
async function loadMigrations() {
    try {
        showLoading();
        console.log('🔄 Chargement des migrations depuis /api/migrations...');
        const { data } = await axios.get('/api/migrations');
        console.log('✅ Données reçues:', data);
        migrations = Array.isArray(data) ? data : (data?.items || []);
        filteredMigrations = [...migrations];
        console.log(`📊 ${migrations.length} migrations chargées`);
        displayMigrations();
        updateStatistics();
    } catch (error) {
        console.error('❌ Erreur lors du chargement des migrations:', error?.response?.data?.detail || error.message);
        console.log('🔧 Affichage de l\'état vide (pas de données factices)');
        // Pas de données factices: afficher simplement l'état vide
        migrations = [];
        filteredMigrations = [];
        displayMigrations();
        updateStatistics();
    } finally {
        hideLoading();
    }
}

// Afficher les migrations
function displayMigrations() {
    const container = document.getElementById('migrationsContainer');
    container.innerHTML = '';

    if (filteredMigrations.length === 0) {
        container.innerHTML = `
            <div class="card">
                <div class="card-body text-center py-5">
                    <i class="bi bi-arrow-repeat display-1 text-muted"></i>
                    <h4 class="text-muted mt-3">Aucune migration trouvée</h4>
                    <p class="text-muted">Commencez par créer une nouvelle migration</p>
                    <button class="btn btn-primary" onclick="showImportModal()">
                        <i class="bi bi-upload me-2"></i>
                        Nouvelle Migration
                    </button>
                </div>
            </div>
        `;
        return;
    }

    filteredMigrations.forEach(migration => {
        const card = createMigrationCard(migration);
        container.appendChild(card);
    });
}

// Créer une carte de migration
function createMigrationCard(migration) {
    const card = document.createElement('div');
    card.className = `card migration-card ${migration.status} mb-3`;
    
    const statusInfo = getStatusInfo(migration.status);
    const progress = migration.total_records > 0 ? 
        Math.round((migration.processed_records / migration.total_records) * 100) : 0;
    
    card.innerHTML = `
        <div class="card-body">
            <div class="row">
                <div class="col-md-8">
                    <div class="d-flex align-items-start">
                        <div class="me-3">
                            <i class="bi ${getTypeIcon(migration.type)} display-6 text-${statusInfo.color}"></i>
                        </div>
                        <div class="flex-grow-1">
                            <h5 class="card-title mb-1">${escapeHtml(migration.name)}</h5>
                            <p class="text-muted mb-2">${escapeHtml(migration.description || '')}</p>
                            
                            <div class="row text-sm">
                                <div class="col-md-6">
                                    <small class="text-muted">
                                        <i class="bi bi-file-earmark me-1"></i>
                                        ${escapeHtml(migration.file_name)}
                                    </small>
                                </div>
                                <div class="col-md-6">
                                    <small class="text-muted">
                                        <i class="bi bi-calendar me-1"></i>
                                        ${formatDateTime(migration.created_at)}
                                    </small>
                                </div>
                            </div>

                            ${migration.status === 'running' ? `
                                <div class="progress-container">
                                    <div class="d-flex justify-content-between align-items-center mb-1">
                                        <small class="text-muted">Progression</small>
                                        <small class="text-muted">${progress}%</small>
                                    </div>
                                    <div class="progress" style="height: 6px;">
                                        <div class="progress-bar bg-primary" style="width: ${progress}%"></div>
                                    </div>
                                    <small class="text-muted">
                                        ${migration.processed_records} / ${migration.total_records} enregistrements
                                    </small>
                                </div>
                            ` : ''}

                            ${migration.error_message ? `
                                <div class="alert alert-danger alert-sm mt-2 mb-0">
                                    <i class="bi bi-exclamation-triangle me-2"></i>
                                    ${escapeHtml(migration.error_message)}
                                </div>
                            ` : ''}
                        </div>
                    </div>
                </div>
                
                <div class="col-md-4">
                    <div class="text-end">
                        <span class="badge bg-${statusInfo.color} mb-2">${statusInfo.text}</span>
                        
                        ${migration.status === 'completed' || migration.status === 'failed' ? `
                            <div class="mb-2">
                                <div class="row text-center">
                                    <div class="col-4">
                                        <div class="text-success">
                                            <strong>${migration.success_records || 0}</strong>
                                            <br><small>Réussis</small>
                                        </div>
                                    </div>
                                    <div class="col-4">
                                        <div class="text-danger">
                                            <strong>${migration.error_records || 0}</strong>
                                            <br><small>Erreurs</small>
                                        </div>
                                    </div>
                                    <div class="col-4">
                                        <div class="text-primary">
                                            <strong>${migration.total_records || 0}</strong>
                                            <br><small>Total</small>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        ` : ''}

                        <div class="btn-group-vertical btn-group-sm w-100">
                            <button class="btn btn-outline-primary" onclick="showMigrationDetails(${migration.id})">
                                <i class="bi bi-info-circle me-2"></i>
                                Détails
                            </button>
                            <button class="btn btn-outline-secondary" onclick="showMigrationLogs(${migration.id})">
                                <i class="bi bi-terminal me-2"></i>
                                Logs
                            </button>
                            ${migration.status === 'running' ? `
                                <button class="btn btn-outline-danger" onclick="cancelMigration(${migration.id})">
                                    <i class="bi bi-stop me-2"></i>
                                    Annuler
                                </button>
                            ` : ''}
                            ${migration.status === 'failed' ? `
                                <button class="btn btn-outline-warning" onclick="retryMigration(${migration.id})">
                                    <i class="bi bi-arrow-repeat me-2"></i>
                                    Relancer
                                </button>
                            ` : ''}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    return card;
}

// Obtenir les informations de statut
function getStatusInfo(status) {
    const statusMap = {
        pending: { color: 'warning', text: 'En attente' },
        running: { color: 'primary', text: 'En cours' },
        completed: { color: 'success', text: 'Terminée' },
        failed: { color: 'danger', text: 'Échouée' }
    };
    return statusMap[status] || { color: 'secondary', text: 'Inconnu' };
}

// Obtenir l'icône du type
function getTypeIcon(type) {
    const iconMap = {
        products: 'bi-box',
        clients: 'bi-people',
        suppliers: 'bi-truck',
        invoices: 'bi-receipt',
        stock: 'bi-boxes'
    };
    return iconMap[type] || 'bi-file-earmark';
}

// Filtrer les migrations
function filterMigrations() {
    const typeFilter = document.getElementById('typeFilter').value;
    const statusFilter = document.getElementById('statusFilter').value;
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;

    filteredMigrations = migrations.filter(migration => {
        const matchesType = !typeFilter || migration.type === typeFilter;
        const matchesStatus = !statusFilter || migration.status === statusFilter;
        
        let matchesDate = true;
        if (startDate || endDate) {
            const migrationDate = new Date(migration.created_at).toISOString().split('T')[0];
            if (startDate && migrationDate < startDate) matchesDate = false;
            if (endDate && migrationDate > endDate) matchesDate = false;
        }

        return matchesType && matchesStatus && matchesDate;
    });

    displayMigrations();
    updateStatistics();
}

// Effacer les filtres
function clearFilters() {
    document.getElementById('typeFilter').value = '';
    document.getElementById('statusFilter').value = '';
    document.getElementById('startDate').value = '';
    document.getElementById('endDate').value = '';
    filterMigrations();
}

// Mettre à jour les statistiques
function updateStatistics() {
    const total = filteredMigrations.length;
    const completed = filteredMigrations.filter(m => m.status === 'completed').length;
    const running = filteredMigrations.filter(m => m.status === 'running').length;
    const failed = filteredMigrations.filter(m => m.status === 'failed').length;

    document.getElementById('totalMigrations').textContent = total;
    document.getElementById('completedMigrations').textContent = completed;
    document.getElementById('runningMigrations').textContent = running;
    document.getElementById('failedMigrations').textContent = failed;
}

// Afficher la modal d'import
function showImportModal() {
    const modal = new bootstrap.Modal(document.getElementById('importModal'));
    modal.show();
    
    // Réinitialiser le formulaire
    document.getElementById('importForm').reset();
    document.getElementById('dataPreview').style.display = 'none';
}

// Gérer la sélection de fichier
function handleFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    // Vérifier la taille du fichier (10MB max)
    if (file.size > 10 * 1024 * 1024) {
        showError('Le fichier est trop volumineux. Taille maximum : 10MB');
        event.target.value = '';
        return;
    }

    // Mettre à jour le nom de migration si vide
    const migrationName = document.getElementById('migrationName');
    if (!migrationName.value) {
        const baseName = file.name.replace(/\.[^/.]+$/, "");
        const type = document.getElementById('migrationType').value;
        migrationName.value = `Import ${type} depuis ${baseName}`;
    }

    // Détecter le format automatiquement
    const extension = file.name.split('.').pop().toLowerCase();
    const formatSelect = document.getElementById('fileFormat');
    if (extension === 'csv') {
        formatSelect.value = 'csv';
    } else if (extension === 'xlsx' || extension === 'xls') {
        formatSelect.value = 'excel';
    } else if (extension === 'json') {
        formatSelect.value = 'json';
    }
}

// Mettre à jour le format de fichier selon le type
function updateFileFormat() {
    const type = document.getElementById('migrationType').value;
    const formatSelect = document.getElementById('fileFormat');
    
    // Réinitialiser les options
    formatSelect.innerHTML = `
        <option value="">Sélectionner un format</option>
        <option value="csv">CSV</option>
        <option value="excel">Excel (.xlsx)</option>
        <option value="json">JSON</option>
    `;
}

// Aperçu des données
function previewData() {
    const file = document.getElementById('dataFile').files[0];
    if (!file) {
        showError('Veuillez sélectionner un fichier');
        return;
    }

    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            let preview = '';
            const extension = file.name.split('.').pop().toLowerCase();
            
            if (extension === 'json') {
                const data = JSON.parse(e.target.result);
                preview = JSON.stringify(data.slice(0, 5), null, 2);
            } else if (extension === 'csv') {
                const lines = e.target.result.split('\n').slice(0, 6);
                preview = lines.join('\n');
            } else {
                preview = 'Aperçu non disponible pour ce format';
            }

            document.getElementById('previewContent').textContent = preview;
            document.getElementById('dataPreview').style.display = 'block';
        } catch (error) {
            showError('Erreur lors de la lecture du fichier');
        }
    };

    if (file.name.endsWith('.json') || file.name.endsWith('.csv')) {
        reader.readAsText(file);
    } else {
        showError('Aperçu disponible uniquement pour les fichiers JSON et CSV');
    }
}

// Démarrer la migration
async function startMigration() {
    const form = document.getElementById('importForm');
    if (!form.checkValidity()) {
        form.reportValidity();
        return;
    }

    const file = document.getElementById('dataFile').files[0];
    if (!file) {
        showError('Veuillez sélectionner un fichier');
        return;
    }

    try {
        showLoading();
        // 1) Créer la migration (status pending)
        const payload = {
            name: document.getElementById('migrationName').value,
            type: document.getElementById('migrationType').value,
            status: 'pending',
            description: document.getElementById('migrationDescription').value || undefined
        };
        const { data: created } = await axios.post('/api/migrations', payload);

        // 2) Uploader le fichier
        const formData = new FormData();
        formData.append('file', file);
        await axios.post(`/api/migrations/${created.id}/upload`, formData, {
            headers: { 'Content-Type': 'multipart/form-data' }
        });

        // 3) Démarrer la migration côté backend
        await axios.post(`/api/migrations/${created.id}/start`, {});

        // 4) Fermer la modal et rafraîchir
        const modal = bootstrap.Modal.getInstance(document.getElementById('importModal'));
        if (modal) modal.hide();
        await loadMigrations();
        showSuccess('Migration démarrée avec succès');
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors du démarrage de la migration';
        showError(msg);
    } finally {
        hideLoading();
    }
}

// [Deprecated] Ancienne simulation de migration (désactivée)
function simulateMigration() {
    console.warn('simulateMigration est désactivé. Les migrations sont gérées par le backend.');
}

// Afficher les détails d'une migration
function showMigrationDetails(migrationId) {
    const migration = migrations.find(m => m.id === migrationId);
    if (!migration) return;

    const content = document.getElementById('migrationDetailsContent');
    content.innerHTML = `
        <div class="row">
            <div class="col-md-6">
                <h6>Informations Générales</h6>
                <table class="table table-sm">
                    <tr><td><strong>Nom :</strong></td><td>${escapeHtml(migration.name)}</td></tr>
                    <tr><td><strong>Type :</strong></td><td>${escapeHtml(migration.type)}</td></tr>
                    <tr><td><strong>Fichier :</strong></td><td>${escapeHtml(migration.file_name)}</td></tr>
                    <tr><td><strong>Statut :</strong></td><td><span class="badge bg-${getStatusInfo(migration.status).color}">${getStatusInfo(migration.status).text}</span></td></tr>
                    <tr><td><strong>Créé le :</strong></td><td>${formatDateTime(migration.created_at)}</td></tr>
                    ${migration.completed_at ? `<tr><td><strong>Terminé le :</strong></td><td>${formatDateTime(migration.completed_at)}</td></tr>` : ''}
                </table>
            </div>
            <div class="col-md-6">
                <h6>Statistiques</h6>
                <table class="table table-sm">
                    <tr><td><strong>Total :</strong></td><td>${migration.total_records || 0}</td></tr>
                    <tr><td><strong>Traités :</strong></td><td>${migration.processed_records || 0}</td></tr>
                    <tr><td><strong>Réussis :</strong></td><td class="text-success">${migration.success_records || 0}</td></tr>
                    <tr><td><strong>Erreurs :</strong></td><td class="text-danger">${migration.error_records || 0}</td></tr>
                </table>
            </div>
        </div>
        ${migration.description ? `
            <div class="mt-3">
                <h6>Description</h6>
                <p>${escapeHtml(migration.description)}</p>
            </div>
        ` : ''}
    `;

    const modal = new bootstrap.Modal(document.getElementById('migrationDetailsModal'));
    modal.show();
}

// Afficher les logs d'une migration (depuis l'API)
async function showMigrationLogs(migrationId) {
    currentMigrationId = migrationId;
    const logsContainer = document.getElementById('migrationLogs');
    logsContainer.innerHTML = '<div class="text-muted">Chargement des logs...</div>';
    try {
        const { data: logs } = await axios.get(`/api/migrations/${migrationId}/logs`);
        if (!Array.isArray(logs) || logs.length === 0) {
            logsContainer.innerHTML = '<div class="text-muted">Aucun log disponible pour cette migration.</div>';
        } else {
            logsContainer.innerHTML = logs.map(log => 
                `<div class="log-entry log-${log.level}">[${formatDateTime(log.timestamp)}] ${log.level?.toUpperCase?.() || ''}: ${escapeHtml(log.message || '')}</div>`
            ).join('');
        }
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors de la récupération des logs';
        logsContainer.innerHTML = `<div class="text-danger">${escapeHtml(msg)}</div>`;
    }
    const modal = new bootstrap.Modal(document.getElementById('logsModal'));
    modal.show();
}

// [Deprecated] Génération de logs d'exemple (supprimée)
function generateSampleLogs() {
    return [];
}

// Télécharger les logs (depuis l'API)
async function downloadLogs() {
    if (!currentMigrationId) return;
    try {
        const { data: logs } = await axios.get(`/api/migrations/${currentMigrationId}/logs`);
        const safeLogs = Array.isArray(logs) ? logs : [];
        const logText = safeLogs.map(log => `[${formatDateTime(log.timestamp)}] ${(log.level || '').toUpperCase()}: ${log.message || ''}`).join('\n');
        const blob = new Blob([logText || ''], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `migration_${currentMigrationId}_logs.txt`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors du téléchargement des logs';
        showError(msg);
    }
}

// Annuler une migration (la marquer comme échouée côté backend)
async function cancelMigration(migrationId) {
    if (!await confirmDialog('Êtes-vous sûr de vouloir annuler cette migration ?')) return;
    try {
        await axios.post(`/api/migrations/${migrationId}/complete`, {
            status: 'failed',
            error_message: "Migration annulée par l'utilisateur"
        });
        await loadMigrations();
        showSuccess('Migration annulée');
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors de l\'annulation';
        showError(msg);
    }
}

// Relancer une migration (backend)
async function retryMigration(migrationId) {
    try {
        await axios.post(`/api/migrations/${migrationId}/start`, {});
        await loadMigrations();
        showSuccess('Migration relancée');
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors du redémarrage';
        showError(msg);
    }
}

// Actualiser les migrations
function refreshMigrations() {
    loadMigrations();
}

// Utilitaires
function showLoading() {
    const container = document.getElementById('migrationsContainer');
    container.innerHTML = `
        <div class="card">
            <div class="card-body text-center py-5">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Chargement...</span>
                </div>
                <p class="text-muted mt-2 mb-0">Chargement des migrations...</p>
            </div>
        </div>
    `;
}

function hideLoading() {
    // Le loading sera masqué par displayMigrations()
}

function formatDateTime(dateString) {
    return new Date(dateString).toLocaleString('fr-FR');
}
