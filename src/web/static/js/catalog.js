/* Catalog loading + grid rendering */

import { API, state } from './state.js';
import { formatDimensions } from './utils.js';
import { showComponentDetail } from './detail.js';

export async function loadCatalog() {
    const status = document.getElementById('catalog-status');
    status.textContent = 'Loading...';
    status.style.color = 'var(--text-dim)';

    try {
        const url = state.session
            ? `${API}/api/session/catalog?session=${state.session}`
            : `${API}/api/catalog`;
        const res = await fetch(url);
        state.catalog = await res.json();
        renderCatalog(state.catalog);
    } catch (err) {
        status.textContent = 'Failed to load catalog';
        status.style.color = 'var(--error)';
        console.error(err);
    }
}

export async function reloadCatalog() {
    const status = document.getElementById('catalog-status');
    status.textContent = 'Reloading...';

    try {
        const res = await fetch(`${API}/api/catalog/reload`, { method: 'POST' });
        state.catalog = await res.json();
        renderCatalog(state.catalog);
    } catch (err) {
        status.textContent = 'Reload failed';
        status.style.color = 'var(--error)';
    }
}

function renderCatalog(data) {
    const status = document.getElementById('catalog-status');
    const errPanel = document.getElementById('catalog-errors');
    const grid = document.getElementById('catalog-grid');

    // Status badge
    if (data.ok) {
        status.textContent = `${data.component_count} components loaded ✓`;
        status.style.color = 'var(--success)';
    } else {
        status.textContent = `${data.component_count} components, ${data.errors.length} error(s)`;
        status.style.color = 'var(--warning)';
    }

    // Errors panel
    if (data.errors && data.errors.length > 0) {
        errPanel.hidden = false;
        errPanel.innerHTML = `
            <h3>Validation Errors (${data.errors.length})</h3>
            <ul>
                ${data.errors.map(e => `
                    <li>
                        <strong>${e.component_id}</strong>
                        <span class="err-field">${e.field}</span>: ${e.message}
                    </li>`).join('')}
            </ul>`;
    } else {
        errPanel.hidden = true;
    }

    // Component cards
    grid.innerHTML = data.components.map(c => `
        <div class="comp-card" data-id="${c.id}">
            <div class="card-header">
                <h3>${c.name}</h3>
                <span class="card-id">${c.id}</span>
            </div>
            <div class="card-badges">
                <span class="badge badge-category">${c.category}</span>
                <span class="badge badge-mounting">${c.mounting.style}</span>
                ${c.ui_placement ? '<span class="badge badge-ui">UI</span>' : ''}
            </div>
            <div class="card-desc">${c.description}</div>
            <div class="card-dims">${formatDimensions(c)}</div>
            <div class="card-pins">
                ${c.pins.map(p => `<span class="pin-tag">${p.id}</span>`).join('')}
            </div>
        </div>`).join('');

    // Wire card clicks
    grid.querySelectorAll('.comp-card').forEach(card => {
        card.addEventListener('click', () => {
            const comp = data.components.find(c => c.id === card.dataset.id);
            if (comp) showComponentDetail(comp);
        });
    });
}
