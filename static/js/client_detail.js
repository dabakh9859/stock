// Page détail client

document.addEventListener('DOMContentLoaded', async function() {
    const url = new URL(window.location.href);
    const clientId = Number(url.searchParams.get('id')) || null;
    if (!clientId) {
        showError('Identifiant client manquant');
        return;
    }
    try {
        const { data } = await axios.get(`/api/clients/${clientId}/details`);
        renderClientDetails(data);
    } catch (e) {
        console.error('Erreur chargement détails client:', e);
        showError(e.response?.data?.detail || 'Erreur lors du chargement des détails client');
    }
});

function renderClientDetails(payload) {
    document.getElementById('clientDetailLoading').classList.add('d-none');
    document.getElementById('clientDetailRoot').classList.remove('d-none');

    const c = payload.client;
    const stats = payload.stats || {};

    // Le nom du client est le titre de la page. L'ancienne version titrait
    // « Fiche client » et reléguait le nom dans une ligne « Nom: » au milieu
    // d'un tableau d'informations.
    document.getElementById('clientNom').textContent = c.name || 'Client sans nom';

    const coordonnees = document.getElementById('clientCoordonnees');
    coordonnees.textContent = '';
    const ajouterCoordonnee = (icone, valeur, lien) => {
        if (!valeur) return;
        const bloc = document.createElement('span');
        bloc.className = 'fc-coordonnee';
        const i = document.createElement('i');
        i.className = 'bi ' + icone;
        bloc.appendChild(i);
        if (lien) {
            const a = document.createElement('a');
            a.href = lien;
            a.textContent = valeur;
            bloc.appendChild(a);
        } else {
            bloc.appendChild(document.createTextNode(valeur));
        }
        coordonnees.appendChild(bloc);
    };
    ajouterCoordonnee('bi-person', c.contact || c.contact_person, null);
    ajouterCoordonnee('bi-telephone', c.phone, c.phone ? 'tel:' + c.phone : null);
    ajouterCoordonnee('bi-envelope', c.email, c.email ? 'mailto:' + c.email : null);
    ajouterCoordonnee('bi-geo-alt', c.address, null);
    if (c.created_at) ajouterCoordonnee('bi-calendar3', 'Client depuis le ' + formatDateTime(c.created_at), null);

    // Tuiles. Le pourcentage encaissé et le nombre de factures donnent au
    // chiffre le contexte qui manquait : « 2 400 000 F » seul ne dit pas si
    // c'est beaucoup, ni ce qu'il en reste.
    const facture = Number(stats.total_invoiced || 0);
    const paye = Number(stats.total_paid || 0);
    const du = Number(stats.total_due || 0);
    const dettes = Number(stats.total_debts || 0);
    const nbFactures = Array.isArray(payload.invoices) ? payload.invoices.length : 0;

    const tuiles = [
        {
            libelle: 'Total facturé', valeur: formatCurrency(facture),
            pied: nbFactures + (nbFactures > 1 ? ' factures' : ' facture')
        },
        {
            libelle: 'Encaissé', valeur: formatCurrency(paye),
            pied: facture > 0 ? Math.round((paye / facture) * 100) + ' % du facturé' : '—',
            classe: (du <= 0 && facture > 0) ? 'fc-tuile--solde' : ''
        },
        {
            libelle: 'Restant dû', valeur: formatCurrency(du),
            pied: du > 0 ? 'à recouvrer' : 'rien à recouvrer',
            classe: du > 0 ? 'fc-tuile--du' : ''
        },
        {
            libelle: 'Créances manuelles', valeur: formatCurrency(dettes),
            pied: 'hors factures',
            classe: dettes > 0 ? 'fc-tuile--du' : ''
        }
    ];

    const hoteTuiles = document.getElementById('clientTuiles');
    hoteTuiles.textContent = '';
    tuiles.forEach(t => {
        const div = document.createElement('div');
        div.className = 'fc-tuile ' + (t.classe || '');
        const l = document.createElement('div'); l.className = 'fc-tuile__libelle'; l.textContent = t.libelle;
        const v = document.createElement('div'); v.className = 'fc-tuile__valeur'; v.textContent = t.valeur;
        const p = document.createElement('div'); p.className = 'fc-tuile__pied'; p.textContent = t.pied;
        div.append(l, v, p);
        hoteTuiles.appendChild(div);
    });

    const invoices = Array.isArray(payload.invoices) ? payload.invoices : [];
    const invBody = document.getElementById('invoicesBody');
    // `appPath` est indispensable sur le lien de facture : un chemin absolu
    // écrit en dur ignore le préfixe d'URL et renverrait la recette vers la
    // production. Le paramètre `view` ouvre directement la facture.
    invBody.innerHTML = invoices.length ? invoices.map(inv => `
        <tr>
            <td><a href="${appPath('/invoices?view=' + inv.invoice_id)}">${escapeHtml(inv.invoice_number)}</a></td>
            <td>${formatDateTime(inv.date)}</td>
            <td><span class="badge ${badgeForStatus(inv.status)}">${escapeHtml(inv.status)}</span></td>
            <td class="text-end">${formatCurrency(inv.total)}</td>
            <td class="text-end">${formatCurrency(inv.paid)}</td>
            <td class="text-end">${formatCurrency(inv.remaining)}</td>
        </tr>
    `).join('') : '<tr><td colspan="6" class="text-center py-4 text-muted">Aucune facture</td></tr>';

    const debts = Array.isArray(payload.debts) ? payload.debts : [];
    const debtBody = document.getElementById('debtsBody');
    debtBody.innerHTML = debts.length ? debts.map(d => `
        <tr>
            <td>${escapeHtml(String(d.debt_id || '-'))}</td>
            <td>${d.due_date ? formatDateTime(d.due_date) : '-'}</td>
            <td><span class="badge ${badgeForDebtStatus(d.status)}">${escapeHtml(d.status || '-')}</span></td>
            <td class="text-end">${formatCurrency(d.amount || 0)}</td>
        </tr>
    `).join('') : '<tr><td colspan="4" class="text-center py-4 text-muted">Aucune dette</td></tr>';

    const newInvoiceBtn = document.getElementById('newInvoiceBtn');
    if (newInvoiceBtn) newInvoiceBtn.href = appPath(`/invoices?create_for=${c.client_id}`);

    const clientDebtsBtn = document.getElementById('clientDebtsBtn');
    if (clientDebtsBtn) clientDebtsBtn.href = appPath(`/clients/debts?client_id=${c.client_id}`);

    const manageDebtsBtn = document.getElementById('manageDebtsBtn');
    if (manageDebtsBtn) manageDebtsBtn.href = appPath(`/clients/debts?client_id=${c.client_id}`);
}

function badgeForStatus(status) {
    const s = (status || '').toLowerCase();
    if (s.includes('pay')) return 'bg-success';
    if (s.includes('retard') || s.includes('over')) return 'bg-danger';
    if (s.includes('part')) return 'bg-warning';
    return 'bg-secondary';
}

function badgeForDebtStatus(status) {
    const s = (status || '').toLowerCase();
    if (s.includes('paid') || s.includes('régl')) return 'bg-success';
    if (s.includes('due') || s.includes('ouvert') || s.includes('open')) return 'bg-warning';
    if (s.includes('late') || s.includes('retard')) return 'bg-danger';
    return 'bg-secondary';
}


