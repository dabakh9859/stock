/**
 * Récapitulatif d'activité sur une tranche de dates.
 *
 * Toute la page est pilotée par un seul état : `{start, end}`. Chaque
 * changement — bouton de tranche prédéfinie, saisie d'une borne, actualisation
 * — passe par `applyRange()`, qui recharge l'unique appel
 * `/api/daily-recap/stats` et redessine l'ensemble. Aucun bloc ne conserve les
 * données d'une tranche précédente.
 *
 * La tranche choisie est mémorisée : on revient rarement sur cet écran pour
 * regarder une autre période que celle qu'on examinait.
 */

const RANGE_KEY = 'stockflow-recap-range';

let currentRecapData = null;
let rangeChart = null;

// --- Dates ------------------------------------------------------------------

/** Date du jour au format attendu par `<input type="date">`. */
function todayISO() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().split('T')[0];
}

/** Décale une date ISO de `days` jours. */
function shiftDay(iso, days) {
    const d = new Date(iso + 'T00:00:00');
    d.setDate(d.getDate() + days);
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().split('T')[0];
}

function firstOfMonth(iso) {
    return iso.slice(0, 8) + '01';
}

/** Bornes d'une tranche prédéfinie. */
function presetRange(preset) {
    const now = todayISO();
    switch (preset) {
        case 'today': return { start: now, end: now };
        case 'yesterday': return { start: shiftDay(now, -1), end: shiftDay(now, -1) };
        case '7d': return { start: shiftDay(now, -6), end: now };
        case '30d': return { start: shiftDay(now, -29), end: now };
        case 'month': return { start: firstOfMonth(now), end: now };
        case 'lastmonth': {
            // Dernier jour du mois précédent = veille du 1er du mois courant.
            const endPrev = shiftDay(firstOfMonth(now), -1);
            return { start: firstOfMonth(endPrev), end: endPrev };
        }
        case 'year': return { start: now.slice(0, 4) + '-01-01', end: now };
        default: return { start: now, end: now };
    }
}

/** Nom de la tranche prédéfinie correspondant aux bornes, s'il y en a une. */
function matchPreset(start, end) {
    const names = ['today', 'yesterday', '7d', '30d', 'month', 'lastmonth', 'year'];
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
    if (!start && !end) return presetRange('today');
    start = start || end;
    end = end || start;
    // Bornes inversées : on les remet dans l'ordre plutôt que d'interroger
    // l'API sur une tranche vide.
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
        const response = await axios.get('/api/daily-recap/stats/', {
            params: { start_date: resolved.start, end_date: resolved.end }
        });
        const data = response.data;
        currentRecapData = data;

        renderRangeHeader(data);
        renderCash(data);
        renderRangeChart(data);
        renderVolumes(data);
        renderUserStats(data.user_stats || {});
        renderDailyPurchases(data.daily_purchases || {});
        renderTables(data);
        renderBankTables(data.finances || {});
        renderDebts(data.debts || {});
        renderDashboard(data.dashboard || {});
        loadStockSummary();
    } catch (error) {
        console.error('Erreur lors du chargement du récapitulatif:', error);
        showError('Impossible de charger le récapitulatif sur cette période');
    }
}

// --- Rendu ------------------------------------------------------------------

function renderRangeHeader(data) {
    const p = data.period || {};
    setText('rangeSubtitle', p.is_single_day
        ? `Journée du ${p.start_formatted}`
        : `${p.days_count} jours — du ${p.start_formatted} au ${p.end_formatted}`);
}

function renderCash(data) {
    const f = data.finances || {};
    const a = data.averages || {};
    const p = data.period || {};
    const multi = !p.is_single_day;

    setText('paymentsReceived', formatCurrency(f.payments_received || 0));
    setText('bankEntries', formatCurrency(f.bank_entries || 0));
    setText('bankExits', formatCurrency(f.bank_exits || 0));
    setText('periodBalance', formatCurrency(f.daily_balance || 0));
    setText('dailyPurchasesOut', formatCurrency(f.daily_purchases_total || 0));
    setText('potentialRevenue', formatCurrency(f.potential_revenue || 0));
    setText('netRevenue', formatCurrency(f.net_revenue || 0));
    setText('externalProfit', formatCurrency(f.external_profit || 0));

    // La moyenne journalière n'a de sens que sur plusieurs jours.
    setText('paymentsReceivedAvg', multi ? `${formatCurrency(a.payments_per_day || 0)} / jour` : '');
    setText('dailyPurchasesAvg', multi ? `${formatCurrency(a.purchases_per_day || 0)} / jour` : '');
    setText('potentialRevenueAvg', multi ? `${formatCurrency(a.invoiced_per_day || 0)} / jour` : '');

    setText('bankEntriesCount', `${(f.bank_entries_list || []).length} opération(s)`);
    setText('bankExitsCount', `${(f.bank_exits_list || []).length} opération(s)`);

    // Le solde porte sa couleur : c'est le chiffre qu'on lit en premier.
    const balance = document.getElementById('periodBalance');
    if (balance) {
        const v = Number(f.daily_balance || 0);
        balance.style.color = v > 0 ? 'hsl(var(--success))' : (v < 0 ? 'hsl(var(--danger))' : '');
    }
}

function renderVolumes(data) {
    const p = data.period || {};
    const a = data.averages || {};
    const multi = !p.is_single_day;

    setText('invoicesCreated', String((data.invoices && data.invoices.created_count) || 0));
    setText('invoicesCreatedAvg', multi
        ? `${(a.invoices_per_day || 0).toFixed(1)} / jour`
        : formatCurrency((data.invoices && data.invoices.created_total) || 0));
    setText('quotationsCreated', String((data.quotations && data.quotations.created_count) || 0));
    setText('quotationsAccepted', `${(data.quotations && data.quotations.accepted_count) || 0} accepté(s)`);
    setText('stockEntries', String((data.stock && data.stock.entries_count) || 0));
    setText('stockEntriesQty', `${(data.stock && data.stock.entries_quantity) || 0} unité(s)`);
    setText('stockExits', String((data.stock && data.stock.exits_count) || 0));
    setText('stockExitsQty', `${(data.stock && data.stock.exits_quantity) || 0} unité(s)`);
}

function renderRangeChart(data) {
    const card = document.getElementById('rangeChartCard');
    const canvas = document.getElementById('rangeChart');
    const byDay = Array.isArray(data.by_day) ? data.by_day : [];

    // Sur une seule journée le graphe n'aurait qu'un point : on masque le bloc
    // plutôt que d'afficher une barre isolée.
    if (byDay.length < 2) {
        if (card) card.style.display = 'none';
        return;
    }
    if (card) card.style.display = '';
    if (!canvas || !window.Chart) return;

    const a = data.averages || {};
    setText('bestDayLabel', a.best_day_label
        ? `Meilleur jour : ${a.best_day_label} — ${formatCurrency(a.best_day_payments || 0)}`
        : '');

    // Au-delà d'une soixantaine de jours, une étiquette sur N suffit à situer
    // la courbe sans que l'axe devienne illisible.
    const labelStep = byDay.length > 60 ? Math.ceil(byDay.length / 30) : 1;

    if (rangeChart) rangeChart.destroy();
    rangeChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: byDay.map(r => r.label),
            datasets: [
                {
                    label: 'Encaissements',
                    data: byDay.map(r => r.payments),
                    backgroundColor: 'rgba(34, 197, 94, 0.55)',
                    borderColor: '#22c55e',
                    borderWidth: 1,
                    borderRadius: 3
                },
                {
                    label: 'Achats',
                    data: byDay.map(r => r.purchases),
                    backgroundColor: 'rgba(239, 68, 68, 0.5)',
                    borderColor: '#ef4444',
                    borderWidth: 1,
                    borderRadius: 3
                },
                {
                    label: 'Solde',
                    type: 'line',
                    data: byDay.map(r => r.balance),
                    borderColor: '#3b82f6',
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
                tooltip: {
                    callbacks: {
                        label: (ctx) => `${ctx.dataset.label} : ${formatCurrency(ctx.parsed.y)}`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: {
                        autoSkip: false,
                        maxRotation: 0,
                        callback: function (value, index) {
                            return index % labelStep === 0 ? this.getLabelForValue(value) : '';
                        }
                    }
                },
                y: {
                    beginAtZero: true,
                    ticks: { callback: (v) => formatCompact(v) }
                }
            }
        }
    });
}

function renderUserStats(u) {
    const invoices = u.invoices || {};
    const quotations = u.quotations || {};
    const payments = u.payments || {};
    const purchases = u.daily_purchases || {};

    setText('userStatsUsername', u.username ? `(${u.username})` : '');
    setText('userInvoicesCount', String(invoices.count || 0));
    setText('userInvoicesTotal', formatCurrency(invoices.total || 0));
    setText('userQuotationsCount', String(quotations.count || 0));
    setText('userQuotationsTotal', formatCurrency(quotations.total || 0));
    setText('userPaymentsTotal', formatCurrency(payments.total || 0));
    setText('userPaymentsCount', `${payments.count || 0} paiement(s)`);
    setText('userPurchasesTotal', formatCurrency(purchases.total || 0));
    setText('userPurchasesCount', `${purchases.count || 0} achat(s)`);

    const net = Number(u.net_balance || 0);
    const netEl = document.getElementById('userNetBalance');
    if (netEl) {
        netEl.textContent = `Solde net : ${formatCurrency(net)}`;
        netEl.className = `badge badge-plain ${net > 0 ? 'bg-success' : (net < 0 ? 'bg-danger' : 'bg-secondary')}`;
    }

    fillTable('userInvoicesTable', invoices.list || [], 5, invoiceRow);
    fillTable('userQuotationsTable', quotations.list || [], 5, quotationRow);
}

function renderDailyPurchases(dp) {
    setText('dailyPurchasesTotal', formatCurrency(dp.total || 0));

    const cats = document.getElementById('dailyPurchasesByCategory');
    if (cats) {
        const list = Array.isArray(dp.by_category) ? dp.by_category : [];
        cats.innerHTML = list.length
            ? list.map(c => `<span class="badge bg-secondary badge-plain">${escapeHtml(c.category || 'Sans catégorie')} — ${formatCurrency(c.amount || 0)}</span>`).join('')
            : '<span class="text-muted" style="font-size:0.8125rem;">Aucun achat sur la période</span>';
    }

    fillTable('dailyPurchasesTable', dp.list || [], 7, it => `
        <tr>
            <td class="cell-muted">${escapeHtml(it.date)}</td>
            <td class="cell-muted">${escapeHtml(it.time)}</td>
            <td><span class="badge bg-secondary badge-plain">${escapeHtml(it.category)}</span></td>
            <td>${escapeHtml(it.description)}</td>
            <td class="text-end num-strong">${formatCurrency(it.amount || 0)}</td>
            <td class="cell-muted">${escapeHtml(it.method)}</td>
            <td class="cell-muted">${escapeHtml(it.reference)}</td>
        </tr>`, { href: '/daily-purchases', label: 'voir tous les achats' });
}

function invoiceRow(inv) {
    return `
        <tr>
            <td class="cell-muted">${escapeHtml(inv.date)}</td>
            <td><a href="#" onclick="goToInvoiceFromRecap(${Number(inv.id) || 0}); return false;">${escapeHtml(inv.number)}</a></td>
            <td>${escapeHtml(inv.client_name)}</td>
            <td class="text-end num-strong">${formatCurrency(inv.total || 0)}</td>
            <td><span class="badge ${statusBadgeClass(inv.status)}">${escapeHtml(statusLabel(inv.status))}</span></td>
        </tr>`;
}

function quotationRow(q) {
    return `
        <tr>
            <td class="cell-muted">${escapeHtml(q.date)}</td>
            <td><a href="#" onclick="goToQuotationFromRecap(${Number(q.id) || 0}); return false;">${escapeHtml(q.number)}</a></td>
            <td>${escapeHtml(q.client_name)}</td>
            <td class="text-end num-strong">${formatCurrency(q.total || 0)}</td>
            <td><span class="badge ${quotationBadgeClass(q.status)}">${escapeHtml(q.status)}</span></td>
        </tr>`;
}

function renderTables(data) {
    const invoices = (data.invoices && data.invoices.created_list) || [];
    const payments = (data.payments && data.payments.list) || [];
    const quotations = (data.quotations && data.quotations.created_list) || [];
    const stock = data.stock || {};

    setText('invoicesTableCount', String(invoices.length));
    setText('paymentsTableCount', String(payments.length));
    setText('quotationsTableCount', String(quotations.length));

    fillTable('invoicesTable', invoices, 5, invoiceRow,
        { href: '/invoices', label: 'voir toutes les factures' });
    fillTable('quotationsTable', quotations, 5, quotationRow,
        { href: '/quotations', label: 'voir tous les devis' });

    fillTable('paymentsTable', payments, 4, p => `
        <tr>
            <td class="cell-muted">${escapeHtml(p.date)}</td>
            <td>${p.invoice_id
            ? `<a href="#" onclick="goToInvoiceFromRecap(${Number(p.invoice_id)}); return false;">${escapeHtml(p.invoice_number)}</a>`
            : escapeHtml(p.invoice_number)}</td>
            <td class="text-end num-strong">${formatCurrency(p.amount || 0)}</td>
            <td class="cell-muted">${escapeHtml(p.method)}</td>
        </tr>`, { href: '/invoices', label: 'voir les factures' });

    const stockRow = s => `
        <tr>
            <td class="cell-muted">${escapeHtml(s.date)}</td>
            <td>${escapeHtml(s.product_name)}</td>
            <td class="text-end num-strong">${Number(s.quantity) || 0}</td>
            <td class="cell-muted">${s.invoice_id
            ? `<a href="#" onclick="goToInvoiceFromRecap(${Number(s.invoice_id)}); return false;">${escapeHtml(s.invoice_number)}</a>`
            : escapeHtml(s.reference)}</td>
        </tr>`;

    const stockMore = { href: '/stock-movements', label: 'voir tous les mouvements' };
    fillTable('stockEntriesTable', stock.entries_list || [], 4, stockRow, stockMore);
    fillTable('stockExitsTable', stock.exits_list || [], 4, stockRow, stockMore);
}

function renderBankTables(finances) {
    const row = t => `
        <tr>
            <td class="cell-muted">${escapeHtml(t.date)}</td>
            <td>${escapeHtml(t.motif)}</td>
            <td class="text-end num-strong">${formatCurrency(t.amount || 0)}</td>
            <td class="cell-muted">${escapeHtml(t.method)}</td>
        </tr>`;
    const more = { href: '/bank-transactions', label: 'voir la trésorerie' };
    fillTable('bankEntriesTable', finances.bank_entries_list || [], 4, row, more);
    fillTable('bankExitsTable', finances.bank_exits_list || [], 4, row, more);
}

function renderDebts(debts) {
    setText('debtsClientRemaining', formatCurrency(debts.client_total_remaining || 0));
    setText('debtsSupplierRemaining', formatCurrency(debts.supplier_total_remaining || 0));
    setText('debtsTotalRemaining', formatCurrency(debts.total_remaining || 0));
    setText('debtsOverdueAmount', formatCurrency(debts.overdue_amount || 0));
    setText('debtsOverdueCount', `${debts.overdue_count || 0} dette(s)`);
}

function renderDashboard(dashboard) {
    setText('dashboardTotalStock', String(dashboard.total_stock != null ? dashboard.total_stock : '-'));
    setText('dashboardCriticalStock', String(dashboard.critical_stock != null ? dashboard.critical_stock : '-'));
    setText('dashboardOutOfStock', String(dashboard.out_of_stock != null ? dashboard.out_of_stock : '-'));
    setText('dashboardMonthlyRevenue', formatCurrency(dashboard.monthly_revenue || 0));
    setText('dashboardUnpaidAmount', formatCurrency(dashboard.unpaid_amount || 0));
    setText('dashboardAvgTicket', formatCurrency(dashboard.avg_ticket || 0));
    setText('dashboardConversionRate', `${(dashboard.conversion_rate || 0).toFixed(1)} %`);

    fillTable('topProductsTable', dashboard.top_products || [], 2, p => `
        <tr>
            <td>${escapeHtml(p.name || '-')}</td>
            <td class="text-end num-strong">${formatCurrency(p.revenue || 0)}</td>
        </tr>`);

    fillTable('paymentMethodsTable', dashboard.payment_methods || [], 2, pm => `
        <tr>
            <td>${escapeHtml(pm.method || 'Non spécifié')}</td>
            <td class="text-end num-strong">${formatCurrency(pm.amount || 0)}</td>
        </tr>`);
}

async function loadStockSummary() {
    try {
        const response = await axios.get('/api/reports/stock-summary/');
        // La réponse imbrique ses totaux sous `summary`, et nomme la marge
        // `profit_margin_percent` : les lire à la racine renvoyait 0 partout.
        const s = (response.data && response.data.summary) || {};
        setText('recapStockValue', formatCurrency(s.total_stock_value || 0));
        setText('recapStockProfit', formatCurrency(s.total_potential_profit || 0));
        setText('recapStockCost', formatCurrency(s.total_purchase_cost || 0));
        setText('recapStockMargin', `${(s.profit_margin_percent || 0).toFixed(1)} %`);
    } catch (e) {
        console.error('Erreur récapitulatif de stock:', e);
    }
}

// --- Utilitaires ------------------------------------------------------------

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

/**
 * Nombre de lignes affichées par tableau de détail.
 *
 * Sur une tranche d'un mois, « Mouvements de stock » dépasse le millier de
 * lignes : la page devenait un mur de plusieurs dizaines de milliers de
 * pixels, illisible et lent à rendre. On plafonne — mais jamais en silence :
 * une dernière ligne annonce le nombre de lignes masquées et renvoie vers
 * l'écran qui les affiche toutes.
 */
const TABLE_LIMIT = 25;

/** Remplit un `<tbody>`, ou affiche une ligne « aucune donnée » sur la bonne largeur. */
function fillTable(id, list, colspan, rowFn, more) {
    const body = document.getElementById(id);
    if (!body) return;

    const rows = Array.isArray(list) ? list : [];
    if (!rows.length) {
        body.innerHTML = `<tr><td colspan="${colspan}" class="text-center text-muted py-4">Aucune donnée sur la période</td></tr>`;
        return;
    }

    const shown = rows.slice(0, TABLE_LIMIT);
    let html = shown.map(rowFn).join('');

    const hidden = rows.length - shown.length;
    if (hidden > 0) {
        const link = more
            ? ` — <a href="${appPath(more.href)}">${escapeHtml(more.label)}</a>`
            : '';
        html += `<tr><td colspan="${colspan}" class="text-center text-muted py-3">
            ${shown.length} lignes affichées sur ${rows.length}${link}
        </td></tr>`;
    }

    body.innerHTML = html;
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

/** Format court pour les graduations d'axe (« 1,2 M » plutôt que « 1 200 000 »). */
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
    if (s === 'payée' || s === 'payee') return 'bg-success';
    if (s === 'partiellement payée' || s === 'partiellement payee') return 'bg-warning';
    if (s === 'en retard') return 'bg-danger';
    if (s === 'annulée' || s === 'annulee') return 'bg-secondary';
    // « En attente » en rouge : c'est la convention retenue dans toute
    // l'application (cf. getInvoiceStatusColor dans invoices.js).
    return 'bg-danger';
}

function statusLabel(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'payée' || s === 'payee') return 'Payée';
    if (s === 'partiellement payée' || s === 'partiellement payee') return 'Partiellement payée';
    if (s === 'en retard') return 'En retard';
    if (s === 'annulée' || s === 'annulee') return 'Annulée';
    return 'En attente';
}

function quotationBadgeClass(status) {
    const s = String(status || '').toLowerCase();
    if (s === 'accepté' || s === 'accepte') return 'bg-success';
    if (s === 'refusé' || s === 'refuse') return 'bg-danger';
    if (s === 'expiré' || s === 'expire') return 'bg-secondary';
    return 'bg-warning';
}

function showError(message) {
    if (typeof showAlert === 'function') { showAlert(message, 'danger'); return; }
    console.error(message);
}

// --- Navigation vers les écrans détaillés -----------------------------------

function goToInvoiceFromRecap(invoiceId) {
    if (!invoiceId) return;
    window.location.href = appPath(`/invoices?highlight=${invoiceId}`);
}

function goToQuotationFromRecap(quotationId) {
    if (!quotationId) return;
    window.location.href = appPath(`/quotations?highlight=${quotationId}`);
}

// --- Démarrage --------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    // Tranche de départ : celle de la dernière visite, sinon aujourd'hui.
    let initial = presetRange('today');
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

    // Modifier une borne recharge directement : un bouton « Appliquer » qu'il
    // faut penser à cliquer laisse lire des chiffres qui ne sont plus ceux de
    // la tranche affichée.
    ['rangeStart', 'rangeEnd'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('change', () => applyRange());
    });

    applyRange(initial);
});
