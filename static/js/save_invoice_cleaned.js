async function saveInvoice(status) {
    try {
        // UI lock
        const modalEl = document.getElementById('invoiceModal');
        const footerButtons = modalEl ? modalEl.querySelectorAll('.modal-footer button, .modal-footer a') : [];
        const prevStates = [];
        try { footerButtons.forEach(btn => { prevStates.push([btn, btn.disabled, btn.innerHTML]); btn.disabled = true; }); } catch(e) {}
        document.body.style.cursor = 'wait';

        // 1. Capturer les prix externes depuis le DOM (le plus fiable)
        const externalPricesMap = new Map();
        document.querySelectorAll('.external-price-input').forEach(input => {
            const idAttr = input.getAttribute('data-item-id');
            if (idAttr) {
                const digitsOnly = (input.value || '').replace(/\D/g, '');
                const price = digitsOnly && digitsOnly.length > 0 ? parseFloat(digitsOnly) : null;
                externalPricesMap.set(idAttr, price);
            }
        });

        // 2. Préparer les données de base
        const invoiceId = document.getElementById('invoiceId').value;
        const invoiceData = {
            invoice_number: document.getElementById('invoiceNumber').value,
            client_id: parseInt(document.getElementById('clientSelect').value),
            date: document.getElementById('invoiceDate').value,
            due_date: document.getElementById('dueDate').value || null,
            payment_method: (document.getElementById('invoicePaymentMethod') && document.getElementById('invoicePaymentMethod').value) || null,
            notes: document.getElementById('invoiceNotes').value.trim() || '',
            show_tax: document.getElementById('showTaxSwitch')?.checked ?? true,
            tax_rate: parseFloat(document.getElementById('taxRateInput')?.value) || 0,
            show_item_prices: document.getElementById('showSectionPricesSwitch')?.checked ?? true,
            show_section_totals: document.getElementById('showSectionTotalsSwitch')?.checked ?? true,
            subtotal: parseFloat(document.getElementById('invoiceForm').dataset.subtotal || '0'),
            tax_amount: parseFloat(document.getElementById('invoiceForm').dataset.taxAmount || '0'),
            total: parseFloat(document.getElementById('invoiceForm').dataset.total || '0'),
            quotation_id: (function(){ try { const v = document.getElementById('invoiceForm').dataset.quotationId; return v ? Number(v) : null; } catch(e) { return null; } })(),
            has_warranty: document.getElementById('hasWarrantySwitch')?.checked || false,
            warranty_duration: (function() {
                const hasWarranty = document.getElementById('hasWarrantySwitch')?.checked;
                if (!hasWarranty) return null;
                const selectedDuration = document.querySelector('input[name="warrantyDuration"]:checked');
                return selectedDuration ? parseInt(selectedDuration.value) : 12;
            })()
        };

        // 3. Transformer les items
        invoiceData.items = invoiceItems.flatMap(item => {
            const extPrice = externalPricesMap.get(String(item.id)) ?? item.external_price;
            const finalExtPrice = (extPrice !== null && extPrice !== undefined && !isNaN(extPrice) && extPrice > 0) ? Number(extPrice) : null;

            if (item.is_section) {
                const title = String(item.section_title || '').trim();
                return title ? [{ product_id: null, product_name: `[SECTION] ${title}`, quantity: 0, price: 0, total: 0, variant_id: null, external_price: null }] : [];
            }

            if (!item.product_id && item.is_custom) {
                return [{
                    product_id: null,
                    product_name: item.product_name || 'Service',
                    quantity: item.quantity || 1,
                    price: item.unit_price || 0,
                    total: item.total || ((item.quantity || 1) * (item.unit_price || 0)),
                    variant_id: null,
                    external_price: finalExtPrice
                }];
            }

            if (!item.product_id) return [];

            const hasImeis = item.scannedImeis && item.scannedImeis.length > 0;
            if (!hasImeis) {
                return [{
                    product_id: item.product_id,
                    product_name: item.product_name,
                    quantity: item.quantity,
                    price: item.unit_price,
                    total: item.total,
                    variant_id: item.variant_id || null,
                    external_price: finalExtPrice
                }];
            }

            const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
            return item.scannedImeis.map(imei => {
                const v = variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(imei));
                return {
                    product_id: item.product_id,
                    product_name: item.product_name,
                    quantity: 1,
                    price: item.unit_price,
                    total: item.unit_price,
                    variant_id: v ? v.variant_id : null,
                    variant_imei: imei,
                    external_price: finalExtPrice
                };
            });
        }).filter(x => x);

        // 4. Injecter les méta IMEIs
        try {
            const serialsMeta = invoiceItems.filter(i => i.product_id && i.scannedImeis && i.scannedImeis.length)
                .map(i => ({ product_id: i.product_id, imeis: i.scannedImeis }));
            if (serialsMeta.length) {
                const cleaned = (invoiceData.notes || '').replace(/\n?\n?__SERIALS__=.*$/s, '');
                invoiceData.notes = cleaned ? `${cleaned}\n\n__SERIALS__=${JSON.stringify(serialsMeta)}` : `__SERIALS__=${JSON.stringify(serialsMeta)}`;
            }
        } catch (e) {}

        // 5. Validation
        if (!invoiceData.client_id || !invoiceData.date || invoiceData.items.length === 0) {
            throw new Error('Veuillez remplir tous les champs obligatoires et ajouter au moins un article');
        }

        // 6. Signature
        try {
            const fileInput = document.getElementById('signatureFile');
            const canvas = document.getElementById('signatureCanvas');
            let signatureDataUrl = null;
            if (fileInput?.files?.[0]) {
                signatureDataUrl = await new Promise(res => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(fileInput.files[0]); });
            } else if (canvas) {
                const tmp = document.createElement('canvas'); tmp.width = canvas.width; tmp.height = canvas.height;
                if (canvas.toDataURL() !== tmp.toDataURL()) signatureDataUrl = canvas.toDataURL('image/png');
            }
            if (signatureDataUrl) invoiceData.notes += `\n\n__SIGNATURE__=${signatureDataUrl}`;
        } catch(e) {}

        // 7. Envoi
        const url = invoiceId ? `/api/invoices/${invoiceId}` : '/api/invoices/';
        const method = invoiceId ? 'PUT' : 'POST';
        const { data: responseData } = await axios({ method, url, data: invoiceData, withCredentials: true });

        // 8. Paiement immédiat
        const doPay = document.getElementById('paymentNowSwitch')?.checked;
        if (doPay) {
            try {
                const invId = responseData.invoice_id || responseData.id || invoiceId;
                await axios.post(`/api/invoices/${invId}/payments`, {
                    amount: Math.round(parseFloat(document.getElementById('paymentNowAmount').value || '0')),
                    payment_method: document.getElementById('paymentNowMethod').value,
                    reference: document.getElementById('paymentNowRef').value || null
                });
            } catch (e) { console.warn('Erreur paiement:', e); }
        }

        // 9. Finalisation
        const modalInstance = bootstrap.Modal.getInstance(modalEl);
        if (modalInstance) modalInstance.hide();
        
        await Promise.all([loadInvoices(), loadStats(), loadProducts()]);
        showSuccess(invoiceId ? 'Facture modifiée' : 'Facture créée');

    } catch (error) {
        console.error('Erreur sauvegarde:', error);
        showError(error.message || 'Erreur lors de la sauvegarde');
    } finally {
        try { footerButtons.forEach((btn, i) => { if (prevStates[i]) { btn.disabled = prevStates[i][0]; btn.innerHTML = prevStates[i][2]; } }); } catch(e) {}
        document.body.style.cursor = 'default';
    }
}
