/* Entry point — wires DOM events and kicks off initial load */

import { state } from './state.js';
import { closeModal } from './utils.js';
import { setSessionLabel, startNewSession, showSessionsModal } from './session.js';
import { loadCatalog, reloadCatalog } from './catalog.js';
import { sendDesignPrompt, loadConversation } from './design.js';

document.addEventListener('DOMContentLoaded', () => {
    // Restore session from URL
    const params = new URLSearchParams(window.location.search);
    state.session = params.get('session');
    if (state.session) {
        setSessionLabel(state.session);
        loadConversation();
    }

    // Pipeline nav
    document.querySelectorAll('#pipeline-nav .step').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.disabled) return;
            switchStep(btn.dataset.step);
        });
    });

    // Header buttons
    document.getElementById('btn-new-session').addEventListener('click', startNewSession);

    // Nav-bar: Sessions (right side)
    document.getElementById('btn-list-sessions').addEventListener('click', showSessionsModal);

    // Nav-bar: Catalog toggle (right side)
    document.getElementById('btn-catalog').addEventListener('click', () => {
        const catalogPanel = document.getElementById('step-catalog');
        const catalogBtn = document.getElementById('btn-catalog');
        const isVisible = !catalogPanel.hidden;
        if (isVisible) {
            catalogPanel.hidden = true;
            catalogBtn.classList.remove('active');
            // Restore the active pipeline step
            const activeStep = state.activeStep || 'design';
            const activePanel = document.getElementById(`step-${activeStep}`);
            if (activePanel) activePanel.hidden = false;
            // Re-highlight the active pipeline nav button
            document.querySelectorAll('#pipeline-nav .step[data-step]').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.step === activeStep);
            });
        } else {
            // Hide all step panels, show catalog
            document.querySelectorAll('.step-panel').forEach(p => p.hidden = true);
            catalogPanel.hidden = false;
            if (!state.catalog) loadCatalog();
            // Deselect pipeline buttons, highlight catalog
            document.querySelectorAll('#pipeline-nav .step[data-step]').forEach(btn => {
                btn.classList.remove('active');
            });
            catalogBtn.classList.add('active');
        }
    });
    document.getElementById('btn-reload-catalog').addEventListener('click', reloadCatalog);

    // Design chat
    document.getElementById('btn-send-design').addEventListener('click', sendDesignPrompt);
    document.getElementById('chat-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendDesignPrompt();
        }
    });

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
});

function switchStep(step) {
    state.activeStep = step;
    document.querySelectorAll('#pipeline-nav .step').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.step === step);
    });
    document.querySelectorAll('.step-panel').forEach(panel => {
        panel.hidden = panel.id !== `step-${step}`;
    });
    // Lazy-load catalog on first visit
    if (step === 'catalog' && !state.catalog) {
        loadCatalog();
    }
}
