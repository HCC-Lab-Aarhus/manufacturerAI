/* Entry point — wires DOM events and kicks off initial load */

import { state } from './state.js';
import { closeModal } from './utils.js';
import { setSessionLabel, createNewSession, showSessionsModal } from './session.js';
import { loadCatalog, reloadCatalog } from './catalog.js';

document.addEventListener('DOMContentLoaded', () => {
    // Restore session from URL
    const params = new URLSearchParams(window.location.search);
    state.session = params.get('session');
    if (state.session) setSessionLabel(state.session);

    // Pipeline nav
    document.querySelectorAll('#pipeline-nav .step').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            switchStep(btn.dataset.step);
        });
    });

    // Header buttons
    document.getElementById('btn-new-session').addEventListener('click', createNewSession);
    document.getElementById('btn-list-sessions').addEventListener('click', showSessionsModal);
    document.getElementById('btn-reload-catalog').addEventListener('click', reloadCatalog);

    // Modal close buttons
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', () => closeModal(btn.closest('.modal')));
    });

    // Backdrop click closes modal
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal(modal);
        });
    });

    // Initial load
    loadCatalog();
});

function switchStep(step) {
    state.activeStep = step;
    document.querySelectorAll('#pipeline-nav .step').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.step === step);
    });
    document.querySelectorAll('.step-panel').forEach(panel => {
        panel.hidden = panel.id !== `step-${step}`;
    });
}
