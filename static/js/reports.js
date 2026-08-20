/**
 * Rapports commerciaux sur une tranche de dates.
 *
 * Tout vient d'un seul appel, `/api/reports/summary`, qui agrège en SQL.
 * La version précédente téléchargeait `/api/products/`, `/api/invoices/` et
 * `/api/stock-movements/` pour recalculer les totaux dans le navigateur : ces
 * listes sont plafonnées à 100 lignes côté serveur, et les rapports affichaient
 * donc des chiffres tronqués sans que rien ne le signale.
 *
 * La tranche est mémorisée sous une clé propre aux rapports : on n'y regarde
 * pas la même période que sur le récapitulatif quotidien.
 */

const RANGE_KEY = 'stockflow-reports-range';

/** Palette des graphiques. Teintes de saturation moyenne : elles tiennent sur
 *  le thème sombre comme sur le thème clair. */
const SERIES_COLORS = [
    '#22c55e', '#3b82f6', '#f59e0b', '#a855f7', '#ef4444',
    '#14b8a6', '#ec4899', '#84cc16', '#6366f1', '#f97316'
];

let currentReport = null;
const charts = {};

// --- Dates ------------------------------------------------------------------

function todayISO() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().split('T')[0];
}

function shiftDay(iso, days) {
    const d = new Date(iso + 'T00:00:00');
    d.setDate(d.getDate() + days);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().split('T')[0];
}

function firstOfMonth(iso) {
    return iso.slice(0, 8) + '01';
}

function presetRange(preset) {
    const now = todayISO();
    switch (preset) {
        case 'today': return { start: now, end: now };
        case '7d': return { start: shiftDay(now, -6), end: now };
        case '30d': return { start: shiftDay(now, -29), end: now };
        case 'month': return { start: firstOfMonth(now), end: now };
        case 'lastmonth': {
            const endPrev = shiftDay(firstOfMonth(now), -1);
            return { start: firstOfMonth(endPrev), end: endPrev };
        }
        case 'quarter': return { start: shiftDay(now, -89), end: now };
        case 'year': return { start: now.slice(0, 4) + '-01-01', end: now };
        default: return { start: shiftDay(now, -29), end: now };
    }
}

function matchPreset(start, end) {
    const names = ['today', '7d', '30d', 'month', 'lastmonth', 'quarter', 'year'];
    return names.find(name => {
        const r = presetRange(name);
        return r.start === start && r.end === end;
    }) || null;
}

// --- État -------------------------------------------------------------------

function readRange() {
    const startEl = document.getElementById('rangeStart');
    const endEl = document.getElementById('rangeEnd');
    let start = startEl ? startEl.value : '';
    let end = endEl ? endEl.value : '';
    if (!start && !end) return presetRange('30d');
    start = start || end;
    end = end || start;
    return start > end ? { start: end, end: start } : { start, end };
}

function writeRange(range) {
    const startEl = document.getElementById('rangeStart');
    const endEl = document.getElementById('rangeEnd');
    if (startEl) startEl.value = range.start;
    if (endEl) endEl.value = range.end;

    const active = matchPreset(range.start, range.end);
    document.querySelectorAll('.range-preset').forEach(btn => {
        btn.setAttribute('aria-pressed', String(btn.dataset.preset === active));
    });

    try { localStorage.setItem(RANGE_KEY, JSON.stringify(range)); } catch (e) { }
}

// --- Chargement -------------------------------------------------------------

async function applyRange(range) {
    const resolved = range || readRange();
    writeRange(resolved);

    try {
        const response = await axios.get('/api/reports/summary/', {
            params: { start_date: resolved.start, end_date: resolved.end }
        });
        currentReport = response.data;

        renderHeader(currentReport);
        renderKpis(currentReport);
        renderTrendChart(currentReport);
        renderCategories(currentReport);
        renderPaymentMethods(currentReport);
        renderTopLists(currentReport);
        renderQuotations(currentReport);
        renderStatuses(currentReport);
        renderTreasury(currentReport);
        renderStock(currentReport);
    } catch (error) {
        console.error('Erreur lors du chargement des rapports:', error);
        showError('Impossible de charger les rapports sur cette période');
    }
}

// --- Rendu ------------------------------------------------------------------

function renderHeader(d) {
    const p = d.period || {};
    setText('rangeSubtitle', p.is_single_day
        ? `Journée du ${p.start_formatted}`
        : `${p.days_count} jours — du ${p.start_formatted} au ${p.end_formatted}`);

    const prev = d.previous_period || {};
    setText('comparisonLabel', prev.start_formatted
        ? `Comparé au ${prev.start_formatted} – ${prev.end_formatted}`
        : '');
}

/** Écrit une évolution en pourcentage, avec sa flèche et sa couleur. */
function setDelta(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    if (value === null || value === undefined) {
        // Pas de base de comparaison : afficher « +100 % » à partir de zéro
        // n'apprendrait rien.
        el.className = 'v3-stat__hint delta delta--flat';
        el.textContent = 'Période précédente sans activité';
        return;
    }
    const up = value > 0, flat = value === 0;
    el.className = `v3-stat__hint delta ${flat ? 'delta--flat' : (up ? 'delta--up' : 'delta--down')}`;
    el.textContent = `${up ? '▲' : (flat ? '=' : '▼')} ${Math.abs(value).toFixed(1)} % vs période précédente`;
}

function renderKpis(d) {
    const r = d.revenue || {};
    setText('kpiInvoiced', formatCurrency(r.invoiced || 0));
    setText('kpiPaid', formatCurrency(r.paid || 0));
    setText('kpiOutstanding', formatCurrency(r.outstanding || 0));
    setText('kpiAvgTicket', formatCurrency(r.avg_ticket || 0));
    setText('kpiInvoicesCount', `${r.invoices_count || 0} facture(s)`);
    setDelta('kpiInvoicedDelta', r.growth_invoiced);
    setDelta('kpiPaidDelta', r.growth_paid);
}

function renderTrendChart(d) {
    const byDay = Array.isArray(d.by_day) ? d.by_day : [];
    const canvas = document.getElementById('trendChart');
    if (!canvas || !window.Chart) return;

    // Au-delà d'une soixantaine de jours, une étiquette sur N suffit à situer
    // la courbe sans que l'axe devienne illisible.
    const step = byDay.length > 60 ? Math.ceil(byDay.length / 30) : 1;

    destroyChart('trend');
    charts.trend = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: byDay.map(r => r.label),
            datasets: [
                {
                    label: 'Facturé',
                    data: byDay.map(r => r.invoiced),
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: '#3b82f6',
                    borderWidth: 1,
                    borderRadius: 3
                },
                {
                    label: 'Encaissé',
                    type: 'line',
                    data: byDay.map(r => r.paid),
                    borderColor: '#22c55e',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: byDay.length > 45 ? 0 : 2,
                    tension: 0.25
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
                tooltip: { callbacks: { label: (c) => `${c.dataset.label} : ${formatCurrency(c.parsed.y)}` } }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: {
                        autoSkip: false, maxRotation: 0,
                        callback: function (v, i) { return i % step === 0 ? this.getLabelForValue(v) : ''; }
                    }
                },
                y: { beginAtZero: true, ticks: { callback: (v) => formatCompact(v) } }
            }
        }
    });
}

/** Anneau + tableau part-à-part, motif partagé par catégories et règlements. */
function renderBreakdown(opts) {
    const rows = Array.isArray(opts.rows) ? opts.rows : [];
    const total = rows.reduce((sum, r) => sum + (opts.value(r) || 0), 0);

    const canvas = document.getElementById(opts.canvasId);
    if (canvas && window.Chart) {
        destroyChart(opts.key);
        if (rows.length) {
            charts[opts.key] = new Chart(canvas, {
                type: 'doughnut',
                data: {
                    labels: rows.map(opts.label),
                    datasets: [{
                        data: rows.map(opts.value),
                        backgroundColor: rows.map((_, i) => SERIES_COLORS[i % SERIES_COLORS.length]),
                        borderColor: 'transparent',
                        borderWidth: 2
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '62%',
                    plugins: {
                        legend: { position: 'bottom', labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true } },
                        tooltip: { callbacks: { label: (c) => `${c.label} : ${formatCurrency(c.parsed)}` } }
                    }
                }
            });
        }
    }

    fillTable(opts.tableId, rows, 4, r => {
        const value = opts.value(r) || 0;
        const share = total ? (value / total * 100) : 0;
        return `
            <tr>
                <td>${escapeHtml(opts.label(r))}</td>
                <td class="text-end cell-muted">${escapeHtml(String(opts.middle(r)))}</td>
                <td class="text-end num-strong">${formatCurrency(value)}</td>
                <td class="text-end cell-muted">${share.toFixed(1)} %</td>
            </tr>`;
    });
}

function renderCategories(d) {
    renderBreakdown({
        key: 'category',
        canvasId: 'categoryChart',
        tableId: 'categoryTable',
        rows: d.by_category,
        label: r => r.category || 'Sans catégorie',
        value: r => r.revenue,
        middle: r => r.quantity
    });
}

function renderPaymentMethods(d) {
    renderBreakdown({
        key: 'payment',
        canvasId: 'paymentChart',
        tableId: 'paymentTable',
        rows: d.payment_methods,
        label: r => r.method || 'Non précisé',
        value: r => r.amount,
        middle: r => r.count
    });
}

function renderTopLists(d) {
    fillTable('topProductsTable', d.top_products, 4, (p, i) => `
        <tr>
            <td class="cell-muted">${i + 1}</td>
            <td>${escapeHtml(p.name)}</td>
            <td class="text-end cell-muted">${Number(p.quantity) || 0}</td>
            <td class="text-end num-strong">${formatCurrency(p.revenue || 0)}</td>
        </tr>`);

    fillTable('topClientsTable', d.top_clients, 4, (c, i) => `
        <tr>
            <td class="cell-muted">${i + 1}</td>
            <td>${escapeHtml(c.name)}</td>
            <td class="text-end cell-muted">${Number(c.invoices_count) || 0}</td>
            <td class="text-end num-strong">${formatCurrency(c.revenue || 0)}</td>
        </tr>`);
}

function renderQuotations(d) {
    const q = d.quotations || {};
    setText('quotationsCreated', String(q.created || 0));
    setText('quotationsCreatedTotal', formatCurrency(q.created_total || 0));
    setText('quotationsAccepted', String(q.accepted || 0));
    setText('quotationsAcceptedTotal', formatCurrency(q.accepted_total || 0));
    setText('quotationsConversion', `${(q.conversion_rate || 0).toFixed(1)} %`);
}

function renderStatuses(d) {
    fillTable('statusTable', d.invoice_status, 3, s => `
        <tr>
            <td><span class="badge ${statusBadgeClass(s.status)}">${escapeHtml(statusLabel(s.status))}</span></td>
            <td class="text-end cell-muted">${Number(s.count) || 0}</td>
            <td class="text-end num-strong">${formatCurrency(s.total || 0)}</td>
        </tr>`);
}

function renderTreasury(d) {
    const t = d.treasury || {};
    setText('treasuryIn', formatCurrency(t.bank_in || 0));
    setText('treasuryOut', formatCurrency(t.bank_out || 0));
    setText('treasuryPurchases', formatCurrency(t.purchases || 0));
    setText('treasuryNet', formatCurrency(t.net || 0));

    const net = document.getElementById('treasuryNet');
    if (net) {
        const v = Number(t.net || 0);
        net.style.color = v > 0 ? 'hsl(var(--success))' : (v < 0 ? 'hsl(var(--danger))' : '');
    }
}

function renderStock(d) {
    const s = d.stock || {};
    setText('stockValue', formatCurrency(s.stock_value || 0));
    setText('stockProducts', `${s.products_count || 0} produit(s)`);
    setText('stockCost', formatCurrency(s.purchase_cost || 0));
    setText('stockProfit', formatCurrency(s.potential_profit || 0));
    setText('stockMargin', `${(s.margin || 0).toFixed(1)} % de marge`);
    setText('stockOutOfStock', String(s.out_of_stock || 0));
    setText('stockWithStock', `${s.with_stock || 0} en stock`);
}

// --- Export -----------------------------------------------------------------

/**
 * Export CSV de la synthèse affichée.
 *
 * Séparateur point-virgule et BOM UTF-8 : c'est ce qu'attend Excel en
 * configuration française, sinon les accents se cassent et tout atterrit dans
 * une seule colonne.
 */
function exportReport() {
    if (!currentReport) return;
    const d = currentReport;
    const p = d.period || {};
    const r = d.revenue || {};

    const lines = [];
    const push = (...cells) => lines.push(cells.map(csvCell).join(';'));

    push('Rapport', `${p.start_formatted} au ${p.end_formatted}`, `${p.days_count} jours`);
    push('');
    push('Indicateur', 'Valeur');
    push("Chiffre d'affaires facturé", r.invoiced || 0);
    push('Encaissé', r.paid || 0);
    push('Reste à encaisser', r.outstanding || 0);
    push('Nombre de factures', r.invoices_count || 0);
    push('Panier moyen', r.avg_ticket || 0);

    const section = (title, header, rows, mapper) => {
        push('');
        push(title);
        push(...header);
        (rows || []).forEach(row => push(...mapper(row)));
    };

    section('Par jour', ['Date', 'Facturé', 'Encaissé', 'Factures'], d.by_day,
        x => [x.date, x.invoiced, x.paid, x.invoices_count]);
    section('Par catégorie', ['Catégorie', 'Quantité', 'CA'], d.by_category,
        x => [x.category, x.quantity, x.revenue]);
    section('Meilleurs produits', ['Produit', 'Quantité', 'CA'], d.top_products,
        x => [x.name, x.quantity, x.revenue]);
    section('Meilleurs clients', ['Client', 'Factures', 'CA'], d.top_clients,
        x => [x.name, x.invoices_count, x.revenue]);
    section('Modes de règlement', ['Mode', 'Opérations', 'Montant'], d.payment_methods,
        x => [x.method, x.count, x.amount]);
    section('Statuts de facture', ['Statut', 'Nombre', 'Montant'], d.invoice_status,
        x => [x.status, x.count, x.total]);

    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `rapport_${p.start}_${p.end}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

/** Échappe une cellule CSV : guillemets doublés, cellule citée si besoin. */
function csvCell(value) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'number') return String(value).replace('.', ',');
    const s = String(value);
    return /[";\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// --- Utilitaires ------------------------------------------------------------

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function destroyChart(key) {
    if (charts[key]) {
        charts[key].destroy();
        delete charts[key];
    }
}

function fillTable(id, list, colspan, rowFn) {
    const body = document.getElementById(id);
    if (!body) return;
    const rows = Array.isArray(list) ? list : [];
    body.innerHTML = rows.length
        ? rows.map(rowFn).join('')
        : `<tr><td colspan="${colspan}" class="text-center text-muted py-4">Aucune donnée sur la période</td></tr>`;
}

function formatCurrency(amount) {
    try {
        return new Intl.NumberFormat('fr-FR', {
            style: 'currency', currency: 'XOF', maximumFractionDigits: 0
        }).format(Number(amount) || 0);
    } catch (e) {
        return `${Math.round(Number(amount) || 0)} F CFA`;
    }
}

function formatCompact(value) {
    const v = Number(value) || 0;
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1).replace('.0', '') + ' M';
    if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(0) + ' k';
    return String(v);
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function statusBadgeClass(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'payée' || s === 'payee' || s === 'paid') return 'bg-success';
    if (s.startsWith('partiellement')) return 'bg-warning';
    if (s === 'annulée' || s === 'annulee' || s === 'cancelled') return 'bg-secondary';
    // « En attente » en rouge : convention retenue dans toute l'application
    // (cf. getInvoiceStatusColor dans invoices.js).
    return 'bg-danger';
}

function statusLabel(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'payée' || s === 'payee' || s === 'paid') return 'Payée';
    if (s.startsWith('partiellement')) return 'Partiellement payée';
    if (s === 'en retard' || s === 'overdue') return 'En retard';
    if (s === 'annulée' || s === 'annulee' || s === 'cancelled') return 'Annulée';
    if (s === 'brouillon' || s === 'draft') return 'Brouillon';
    return 'En attente';
}

function showError(message) {
    if (typeof showAlert === 'function') { showAlert(message, 'danger'); return; }
    console.error(message);
}

// --- Démarrage --------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    let initial = presetRange('30d');
    try {
        const saved = JSON.parse(localStorage.getItem(RANGE_KEY) || 'null');
        if (saved && saved.start && saved.end) initial = saved;
    } catch (e) { }

    document.querySelectorAll('.range-preset').forEach(btn => {
        btn.addEventListener('click', () => applyRange(presetRange(btn.dataset.preset)));
    });

    const apply = document.getElementById('rangeApply');
    if (apply) apply.addEventListener('click', () => applyRange());

    const refresh = document.getElementById('rangeRefresh');
    if (refresh) refresh.addEventListener('click', () => applyRange());

    const exportBtn = document.getElementById('reportExport');
    if (exportBtn) exportBtn.addEventListener('click', exportReport);

    // Modifier une borne recharge directement : un bouton qu'il faut penser à
    // cliquer laisse lire des chiffres qui ne sont plus ceux de la tranche.
    ['rangeStart', 'rangeEnd'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => applyRange());
    });

    applyRange(initial);
});
