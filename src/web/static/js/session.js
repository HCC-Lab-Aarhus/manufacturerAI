/* Session management — create, load, list, URL sync */

import { API, state } from './state.js';
import { formatDate, closeModal, openModal } from './utils.js';
import { loadCatalog } from './catalog.js';
import { loadConversation } from './design.js';
import { clearData as clearViewportData } from './viewport.js';

export function setSessionLabel(id, name) {
    console.log('Setting session label:', { id, name });
    const label = document.getElementById('session-label');
    if (!id) {
        label.textContent = 'New session';
        label.title = '';
    } else if (name) {
        label.textContent = name;
        label.title = id;
    } else {
        label.textContent = id;
        label.title = id;
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
    // Clear the chat
    const msgs = document.getElementById('chat-messages');
    if (msgs) msgs.innerHTML = '';
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
                <div class="session-item${isActive ? ' active' : ''}" data-id="${s.id}" data-name="${escapeAttr(s.name || '')}">
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
                setSessionUrl(id);
                setSessionLabel(id, name || null);
                closeModal(modal);
                state.catalog = null; // reset catalog cache
                clearViewportData();  // reset viewport for new session
                loadConversation();
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
