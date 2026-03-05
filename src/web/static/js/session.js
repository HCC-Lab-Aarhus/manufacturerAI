/* Session management — create, load, list, URL sync */

import { API, state } from './state.js';
import { formatDate, closeModal, openModal } from './utils.js';
import { loadCatalog } from './catalog.js';
import { loadConversation } from './design.js';
import { loadPlacementResult, resetPlacementPanel } from './placement.js';
import { clearData as clearViewportData, setStep } from './viewport.js';
import { enableGuideBtn, closeGuide } from './guide.js';
import { resetRoutingPanel, loadRoutingResult } from './routing.js';
import { resetScadPanel, loadScadResult } from './scad.js';

// ── Printer selector ──────────────────────────────────────────────

let _printersLoaded = false;

export async function loadPrinters() {
    if (_printersLoaded) return;
    const sel = document.getElementById('printer-select');
    if (!sel) return;
    try {
        const res = await fetch(`${API}/api/printers`);
        const data = await res.json();
        sel.innerHTML = data.printers.map(p =>
            `<option value="${p.id}">${p.label} (${p.bed_width}×${p.bed_depth}mm)</option>`
        ).join('');
        _printersLoaded = true;
    } catch { /* ignore — options stay empty */ }
}

export function setPrinterFromSession(printerId) {
    const sel = document.getElementById('printer-select');
    if (sel && printerId) sel.value = printerId;
}

export function initPrinterSelector() {
    const sel = document.getElementById('printer-select');
    if (!sel) return;
    loadPrinters();
    sel.addEventListener('change', async () => {
        if (!state.session) return;
        const id = sel.value;
        await fetch(
            `${API}/api/session/printer?session=${encodeURIComponent(state.session)}&printer_id=${encodeURIComponent(id)}`,
            { method: 'PUT' }
        );
    });
}

export function setSessionLabel(id, name) {
    const label = document.getElementById('session-label');
    if (!id) {
        label.textContent = 'New session';
        label.title = '';
    } else if (name) {
        label.textContent = name;
        label.title = '';
    } else {
        label.textContent = 'Unnamed Session';
        label.title = '';
    }
}

export function setSessionUrl(id) {
    const url = new URL(window.location);
    if (id) {
        url.searchParams.set('session', id);
    } else {
        url.searchParams.delete('session');
    }
    window.history.replaceState({}, '', url);
    state.session = id;
}

/** Unload the current session and return to a clean chat view. */
export function startNewSession() {
    state.session = null;
    setSessionUrl(null);
    setSessionLabel(null);
    clearViewportData();  // reset all viewport caches
    // Close guide if open
    closeGuide();
    enableGuideBtn(false);
    // Reset placement panel and disable tab
    resetPlacementPanel();
    const placementBtn = document.querySelector('#pipeline-nav .step[data-step="placement"]');
    if (placementBtn) {
        placementBtn.disabled = true;
        placementBtn.classList.remove('tab-flash');
    }
    // Reset routing panel and disable tab
    resetRoutingPanel();
    const routingBtn = document.querySelector('#pipeline-nav .step[data-step="routing"]');
    if (routingBtn) {
        routingBtn.disabled = true;
        routingBtn.classList.remove('tab-flash');
    }
    // Reset SCAD panel and disable tab
    resetScadPanel();
    const scadBtn = document.querySelector('#pipeline-nav .step[data-step="scad"]');
    if (scadBtn) {
        scadBtn.disabled = true;
        scadBtn.classList.remove('tab-flash');
    }
    // Switch to design tab
    state.activeStep = 'design';
    document.querySelectorAll('#pipeline-nav .step[data-step]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.step === 'design');
        btn.classList.remove('tab-flash');
    });
    document.getElementById('btn-catalog')?.classList.remove('active');
    document.querySelectorAll('.step-panel').forEach(panel => {
        panel.hidden = panel.id !== 'step-design';
    });
    setStep('design');
    // Clear the chat
    const msgs = document.getElementById('chat-messages');
    if (msgs) msgs.innerHTML = '';
    // Reset token meter
    const meter = document.getElementById('token-meter');
    if (meter) meter.hidden = true;
    // Focus input
    const input = document.getElementById('chat-input');
    if (input) { input.value = ''; input.focus(); }
}

/**
 * Called when the SSE stream sends a session_created event.
 * Sets the session ID in state and URL without reloading.
 */
export function onSessionCreated(sessionId) {
    state.session = sessionId;
    setSessionUrl(sessionId);
    setSessionLabel(sessionId);
}



export async function showSessionsModal() {
    const modal = document.getElementById('sessions-modal');
    const list = document.getElementById('sessions-list');
    list.innerHTML = '<p class="no-sessions">Loading...</p>';
    openModal(modal);

    try {
        const res = await fetch(`${API}/api/sessions`);
        const data = await res.json();

        if (data.sessions.length === 0) {
            list.innerHTML = '<p class="no-sessions">No sessions yet. Start by describing a device.</p>';
            return;
        }

        list.innerHTML = data.sessions.map(s => {
            const displayName = s.name || s.description || 'Unnamed session';
            const prettyDate = formatDate(s.created);
            const hasDesign = s.pipeline_state?.design === 'complete';
            const isActive = s.id === state.session;
            return `
                <div class="session-item${isActive ? ' active' : ''}" data-id="${s.id}" data-name="${escapeAttr(s.name || '')}" data-printer="${s.printer_id || ''}">
                    <div class="session-info">
                        <div class="session-name">${escapeHtml(displayName)}</div>
                        <div class="session-date">${prettyDate}</div>
                    </div>
                    ${hasDesign ? '<span class="badge badge-small">✓ designed</span>' : ''}
                </div>
            `;
        }).join('');

        list.querySelectorAll('.session-item').forEach(item => {
            item.addEventListener('click', () => {
                const id = item.dataset.id;
                const name = item.dataset.name;
                const printer = item.dataset.printer;
                const hasDesign = item.querySelector('.badge') !== null;
                setSessionUrl(id);
                setSessionLabel(id, name || null);
                if (printer) setPrinterFromSession(printer);
                closeModal(modal);
                state.catalog = null; // reset catalog cache
                clearViewportData();  // reset viewport for new session
                resetPlacementPanel(); // reset placement panel to hero
                closeGuide();          // close guide if open
                enableGuideBtn(false); // disable guide until placement exists
                loadConversation();

                // Enable placement tab if design exists, disable otherwise
                const placementBtn = document.querySelector('#pipeline-nav .step[data-step="placement"]');
                if (placementBtn) {
                    placementBtn.disabled = !hasDesign;
                    placementBtn.classList.remove('tab-flash');
                }
                if (hasDesign) {
                    loadPlacementResult();
                    loadRoutingResult();   // restore routing viewport data
                    loadScadResult();      // restore SCAD + STL status (may resume compile)
                }
            });
        });
    } catch (err) {
        list.innerHTML = '<p class="no-sessions">Failed to load sessions.</p>';
    }
}

function escapeHtml(text) {
    const el = document.createElement('div');
    el.textContent = text;
    return el.innerHTML;
}

function escapeAttr(text) {
    return text.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
