/**
 * Système de gestion des brouillons pour les formulaires modaux
 * Sauvegarde automatique dans localStorage avec interface de gestion
 */

class DraftManager {
    constructor() {
        this.storageKey = 'app_drafts';
        this.autoSaveInterval = 2000; // 2 secondes
        this.autoSaveTimers = {};
        this.currentDrafts = this.loadAllDrafts();
        this.activeModal = null;
        this.draftPanelVisible = false;
        
        this.init();
    }

    init() {
        // Empêcher la fermeture des modals en cliquant sur le backdrop
        this.preventBackdropClose();
        
        // Créer le panneau de brouillons
        this.createDraftPanel();
        
        // Ajouter les boutons de fermeture sur les backdrops
        this.addBackdropCloseButtons();
        
        // Écouter les changements dans les formulaires
        this.setupFormListeners();
    }

    preventBackdropClose() {
        // Intercepter l'événement avant l'ouverture du modal pour désactiver la fermeture au clic
        document.addEventListener('show.bs.modal', (event) => {
            const modal = event.target;
            
            // Récupérer l'instance Bootstrap du modal
            const bsModal = bootstrap.Modal.getInstance(modal) || new bootstrap.Modal(modal, {
                backdrop: 'static',
                keyboard: false
            });
            
            // Forcer les options
            if (bsModal._config) {
                bsModal._config.backdrop = 'static';
                bsModal._config.keyboard = false;
            }
        }, true); // Utiliser la capture pour intercepter avant Bootstrap
        
        // Aussi définir les attributs sur les modals existants
        const setStaticBackdrop = () => {
            const modals = document.querySelectorAll('.modal');
            modals.forEach(modal => {
                modal.setAttribute('data-bs-backdrop', 'static');
                modal.setAttribute('data-bs-keyboard', 'false');
            });
        };
        
        // Exécuter maintenant et après le chargement du DOM
        setStaticBackdrop();
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', setStaticBackdrop);
        }
    }

    addBackdropCloseButtons() {
        // Intercepter l'ouverture des modals pour ajouter le bouton de fermeture
        document.addEventListener('shown.bs.modal', (event) => {
            const modal = event.target;
            this.activeModal = modal;
            
            // Supprimer les anciens boutons s'ils existent (nettoyage préventif)
            modal.querySelectorAll('.draft-backdrop-close, .draft-backdrop-drafts, .draft-backdrop-save').forEach(btn => btn.remove());
            
            // Créer le bouton de fermeture dans le backdrop
            const closeBtn = document.createElement('button');
            closeBtn.className = 'draft-backdrop-close';
            closeBtn.innerHTML = '<i class="bi bi-x-lg"></i>';
            closeBtn.title = 'Fermer (sauvegarde automatique)';
            closeBtn.onclick = (e) => {
                e.stopPropagation();
                this.closeModalWithDraft(modal);
            };
            
            // Créer le bouton pour afficher les brouillons
            const draftsBtn = document.createElement('button');
            draftsBtn.className = 'draft-backdrop-drafts';
            draftsBtn.innerHTML = '<i class="bi bi-file-earmark-text"></i>';
            draftsBtn.title = 'Voir les brouillons';
            draftsBtn.onclick = (e) => {
                e.stopPropagation();
                this.toggleDraftPanel();
            };
            
            // Créer le bouton pour sauvegarder manuellement
            const saveBtn = document.createElement('button');
            saveBtn.className = 'draft-backdrop-save';
            saveBtn.innerHTML = '<i class="bi bi-floppy"></i>';
            saveBtn.title = 'Sauvegarder comme brouillon';
            saveBtn.onclick = (e) => {
                e.stopPropagation();
                const form = modal.querySelector('form');
                if (form) {
                    this.saveDraft(modal, form);
                }
            };
            
            // Ajouter les boutons au modal
            modal.appendChild(closeBtn);
            modal.appendChild(saveBtn);
            modal.appendChild(draftsBtn);
        });
        
        // Nettoyer lors de la fermeture
        document.addEventListener('hidden.bs.modal', (event) => {
            const modal = event.target;
            const closeBtn = modal.querySelector('.draft-backdrop-close');
            const saveBtn = modal.querySelector('.draft-backdrop-save');
            const draftsBtn = modal.querySelector('.draft-backdrop-drafts');
            if (closeBtn) closeBtn.remove();
            if (saveBtn) saveBtn.remove();
            if (draftsBtn) draftsBtn.remove();
            this.activeModal = null;
        });
    }

    createDraftPanel() {
        const panel = document.createElement('div');
        panel.id = 'draftPanel';
        panel.className = 'draft-panel';
        panel.innerHTML = `
            <div class="draft-panel-header">
                <h5><i class="bi bi-file-earmark-text me-2"></i>Brouillons</h5>
                <button class="btn-close btn-close-white" onclick="draftManager.toggleDraftPanel()"></button>
            </div>
            <div class="draft-panel-body" id="draftPanelBody">
                <p class="text-muted text-center">Aucun brouillon</p>
            </div>
        `;
        document.body.appendChild(panel);
    }

    toggleDraftPanel() {
        const panel = document.getElementById('draftPanel');
        this.draftPanelVisible = !this.draftPanelVisible;
        
        if (this.draftPanelVisible) {
            this.updateDraftPanel();
            panel.classList.add('show');
        } else {
            panel.classList.remove('show');
        }
    }

    updateDraftPanel() {
        const body = document.getElementById('draftPanelBody');
        const drafts = this.loadAllDrafts();
        
        if (Object.keys(drafts).length === 0) {
            body.innerHTML = '<p class="text-muted text-center">Aucun brouillon</p>';
            return;
        }
        
        let html = '<div class="draft-list">';
        for (const [key, draft] of Object.entries(drafts)) {
            const date = new Date(draft.savedAt).toLocaleString('fr-FR');
            html += `
                <div class="draft-item">
                    <div class="draft-item-header">
                        <strong>${this.getDraftTitle(draft)}</strong>
                        <span class="badge bg-secondary">${draft.formType}</span>
                    </div>
                    <div class="draft-item-meta">
                        <small class="text-muted">${date}</small>
                    </div>
                    <div class="draft-item-actions">
                        <button class="btn btn-sm btn-primary" onclick="draftManager.loadDraft('${key}')">
                            <i class="bi bi-arrow-clockwise me-1"></i>Charger
                        </button>
                        <button class="btn btn-sm btn-danger" onclick="draftManager.deleteDraft('${key}')">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
            `;
        }
        html += '</div>';
        body.innerHTML = html;
    }

    getDraftTitle(draft) {
        // Extraire un titre significatif du brouillon
        if (draft.data.productName) return draft.data.productName;
        if (draft.data.clientName) return draft.data.clientName;
        if (draft.data.supplierName) return draft.data.supplierName;
        if (draft.data.invoiceNumber) return `Facture ${draft.data.invoiceNumber}`;
        if (draft.data.reference) return draft.data.reference;
        return 'Brouillon sans titre';
    }

    setupFormListeners() {
        // L'auto-sauvegarde est désactivée - sauvegarde manuelle uniquement
        // Les utilisateurs doivent cliquer sur le bouton "Sauvegarder brouillon"
    }

    scheduleAutoSave(modal, form) {
        // Fonction désactivée - sauvegarde manuelle uniquement
    }

    saveDraft(modal, form) {
        console.log('[DraftManager] Début sauvegarde brouillon pour:', modal.id);
        let formData = this.extractFormData(form);
        const formType = this.getFormType(modal);
        
        // Hook pour ajouter des données personnalisées (ex: items de facture)
        const customDataEvent = new CustomEvent('draftSave', { 
            detail: { modalId: modal.id, formType, formData },
            bubbles: true,
            cancelable: true
        });
        modal.dispatchEvent(customDataEvent);
        if (customDataEvent.detail.formData) {
            formData = customDataEvent.detail.formData;
        }

        // Ne pas sauvegarder si le formulaire est vide (moins de 2 champs renseignés)
        const filledFields = Object.values(formData).filter(v => v !== '' && v !== null && v !== false).length;
        if (filledFields < 2) {
            console.warn('[DraftManager] Brouillon trop vide, sauvegarde annulée');
            return;
        }
        
        const draftKey = this.generateDraftKey(formType, formData);
        const draft = {
            formType: formType,
            modalId: modal.id,
            data: formData,
            savedAt: new Date().toISOString()
        };
        
        const drafts = this.loadAllDrafts();
        drafts[draftKey] = draft;
        localStorage.setItem(this.storageKey, JSON.stringify(drafts));
        
        this.currentDrafts = drafts;
        
        console.log('[DraftManager] Brouillon sauvegardé avec succès:', draftKey, formData);
        // Afficher une notification discrète
        this.showNotification('Brouillon sauvegardé', 'success');
    }

    extractFormData(form) {
        const data = {};
        
        // Extraire tous les champs du formulaire (y compris cachés)
        const inputs = form.querySelectorAll('input, select, textarea');
        inputs.forEach(input => {
            const key = input.id || input.name;
            if (!key || input.type === 'button' || input.type === 'submit' || input.type === 'file') return;

            if (input.type === 'checkbox') {
                data[key] = input.checked;
            } else if (input.type === 'radio') {
                if (input.checked) {
                    data[key] = input.value;
                }
            } else if (input.tagName === 'SELECT' && input.multiple) {
                data[key] = Array.from(input.selectedOptions).map(opt => opt.value);
            } else {
                data[key] = input.value;
            }
        });
        
        return data;
    }

    getFormType(modal) {
        // Déterminer le type de formulaire basé sur le modal
        const modalId = modal.id;
        if (modalId.includes('product')) return 'Produit';
        if (modalId.includes('invoice')) return 'Facture';
        if (modalId.includes('quotation')) return 'Devis';
        if (modalId.includes('client')) return 'Client';
        if (modalId.includes('supplier')) return 'Fournisseur';
        if (modalId.includes('transaction')) return 'Transaction';
        if (modalId.includes('debt')) return 'Dette';
        return 'Formulaire';
    }

    generateDraftKey(formType, data) {
        // Générer une clé unique pour le brouillon
        const timestamp = Date.now();
        const dataHash = JSON.stringify(data).substring(0, 50);
        return `${formType}_${timestamp}_${btoa(dataHash).substring(0, 10)}`;
    }

    loadDraft(draftKey) {
        console.log('[DraftManager] Chargement du brouillon:', draftKey);
        const drafts = this.loadAllDrafts();
        const draft = drafts[draftKey];
        
        if (!draft) {
            console.error('[DraftManager] Brouillon introuvable');
            this.showNotification('Brouillon introuvable', 'error');
            return;
        }
        
        const modal = document.getElementById(draft.modalId);
        if (!modal) {
            console.error('[DraftManager] Modal introuvable:', draft.modalId);
            this.showNotification('Formulaire introuvable', 'error');
            return;
        }
        
        // Nettoyage préventif des backdrops si nécessaire
        if (typeof cleanupModalBackdrops === 'function') {
            cleanupModalBackdrops();
        }

        const isAlreadyOpen = modal.classList.contains('show');
        console.log('[DraftManager] Modal déjà ouvert:', isAlreadyOpen);

        const applyData = () => {
            console.log('[DraftManager] Début application des données du brouillon...');
            
            // Remplir le formulaire avec les données du brouillon
            for (const [key, value] of Object.entries(draft.data)) {
                // Ne pas traiter les hooks de données complexes ici
                if (key === 'invoiceItems' || key === 'exchangeItems' || key === 'quotationItems') continue;

                // Chercher par ID d'abord, puis par name, limité au modal actif
                let fields = modal.querySelectorAll(`#${key}, [name="${key}"]`);
                if (fields.length === 0) {
                    fields = document.querySelectorAll(`#${key}, [name="${key}"]`);
                }
                
                fields.forEach(field => {
                    if (!field) return;

                    console.log(`[DraftManager] Restauration champ: ${key} =`, value);

                    try {
                        if (field.type === 'checkbox') {
                            field.checked = (value === true || value === 'true' || value === 'on' || value === 1);
                        } else if (field.type === 'radio') {
                            field.checked = (String(field.value) === String(value));
                        } else if (field.tagName === 'SELECT' && field.multiple && Array.isArray(value)) {
                            Array.from(field.options).forEach(opt => {
                                opt.selected = value.includes(opt.value);
                            });
                        } else if (field.type !== 'file') {
                            field.value = (value === null || value === undefined) ? '' : value;
                        }
                        
                        // Déclencher les événements pour les listeners
                        field.dispatchEvent(new Event('change', { bubbles: true }));
                        field.dispatchEvent(new Event('input', { bubbles: true }));
                        
                        // Support spécial pour Select2 s'il est utilisé
                        if (window.jQuery && jQuery(field).data('select2')) {
                            jQuery(field).trigger('change');
                        }
                    } catch (err) {
                        console.error(`[DraftManager] Erreur lors de la restauration du champ ${key}:`, err);
                    }
                });
            }
            
            // Un petit délai pour laisser les événements se propager avant le hook final
            setTimeout(() => {
                console.log('[DraftManager] Envoi de l\'événement draftLoad aux listeners spécifiques');
                const customLoadEvent = new CustomEvent('draftLoad', { 
                    detail: { modalId: draft.modalId, formType: draft.formType, data: draft.data },
                    bubbles: true
                });
                modal.dispatchEvent(customLoadEvent);
                
                // Forcer la mise à jour visuelle pour les interrupteurs (switches)
                modal.querySelectorAll('.form-check-input[type="checkbox"]').forEach(cb => {
                    const label = modal.querySelector(`label[for="${cb.id}"]`);
                    if (cb.checked) {
                        cb.classList.add('checked');
                    } else {
                        cb.classList.remove('checked');
                    }
                });

                this.showNotification('Brouillon chargé', 'success');
                this.toggleDraftPanel();
            }, 200);
        };

        if (isAlreadyOpen) {
            applyData();
        } else {
            console.log('[DraftManager] Ouverture du modal...');
            let bsModal = bootstrap.Modal.getInstance(modal);
            if (!bsModal) {
                bsModal = new bootstrap.Modal(modal, {
                    backdrop: 'static',
                    keyboard: false
                });
            }
            
            const onShown = () => {
                console.log('[DraftManager] Modal affiché, application des données');
                applyData();
                modal.removeEventListener('shown.bs.modal', onShown);
            };
            modal.addEventListener('shown.bs.modal', onShown);
            bsModal.show();
        }
    }

    async deleteDraft(draftKey) {
        if (!await confirmDialog('Supprimer ce brouillon ?', { variant: 'danger', confirmLabel: 'Supprimer' })) return;
        
        const drafts = this.loadAllDrafts();
        delete drafts[draftKey];
        localStorage.setItem(this.storageKey, JSON.stringify(drafts));
        
        this.currentDrafts = drafts;
        this.updateDraftPanel();
        this.showNotification('Brouillon supprimé', 'success');
    }

    closeModalWithDraft(modal) {
        // Ne plus sauvegarder automatiquement - l'utilisateur doit le faire manuellement
        // Fermer le modal directement
        const bsModal = bootstrap.Modal.getInstance(modal);
        if (bsModal) {
            bsModal.hide();
        }
    }

    loadAllDrafts() {
        try {
            const drafts = localStorage.getItem(this.storageKey);
            if (!drafts) return {};
            const parsed = JSON.parse(drafts);
            
            // Nettoyage des brouillons expirés (plus de 7 jours)
            const now = Date.now();
            const weekInMs = 7 * 24 * 60 * 60 * 1000;
            let cleaned = false;
            
            for (const key in parsed) {
                const savedAt = new Date(parsed[key].savedAt).getTime();
                if (now - savedAt > weekInMs) {
                    delete parsed[key];
                    cleaned = true;
                }
            }
            
            if (cleaned) {
                localStorage.setItem(this.storageKey, JSON.stringify(parsed));
            }
            
            return parsed;
        } catch (e) {
            console.error('Erreur lors du chargement des brouillons:', e);
            return {};
        }
    }

    showNotification(message, type = 'info') {
        // Créer une notification toast discrète
        const toast = document.createElement('div');
        toast.className = `draft-toast draft-toast-${type}`;
        toast.innerHTML = `
            <i class="bi bi-${type === 'success' ? 'check-circle' : 'info-circle'} me-2"></i>
            ${message}
        `;
        
        document.body.appendChild(toast);
        
        // Afficher
        setTimeout(() => toast.classList.add('show'), 10);
        
        // Masquer et supprimer
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 2000);
    }

    async clearAllDrafts() {
        if (!await confirmDialog('Supprimer tous les brouillons ?', { variant: 'danger', confirmLabel: 'Supprimer' })) return;
        
        localStorage.removeItem(this.storageKey);
        this.currentDrafts = {};
        this.updateDraftPanel();
        this.showNotification('Tous les brouillons supprimés', 'success');
    }
}

// Initialiser le gestionnaire de brouillons
let draftManager;
document.addEventListener('DOMContentLoaded', () => {
    draftManager = new DraftManager();
});
