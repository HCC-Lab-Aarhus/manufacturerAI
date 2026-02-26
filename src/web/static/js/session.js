/* Session management — create, load, list, URL sync */

import { API, state } from './state.js';
import { formatDate, closeModal, openModal } from './utils.js';
import { loadCatalog } from './catalog.js';
import { loadConversation } from './design.js';

export function setSessionLabel(id) {
    document.getElementById('session-label').textContent = id ? `Session: ${id}` : 'No session';
}

export function setSessionUrl(id) {
    const url = new URL(window.location);
    url.searchParams.set('session', id);
    window.history.replaceState({}, '', url);
    state.session = id;
    setSessionLabel(id);
}

export async function createNewSession() {
    try {
        const res = await fetch(`${API}/api/sessions`, { method: 'POST' });
        const data = await res.json();
        setSessionUrl(data.session_id);
        loadCatalog();
        loadConversation();
    } catch (err) {
        console.error('Failed to create session:', err);
    }
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
            list.innerHTML = '<p class="no-sessions">No sessions yet. Click "+ New" to create one.</p>';
            return;
        }

        list.innerHTML = data.sessions.map(s => `
            <div class="session-item" data-id="${s.id}">
                <div>
                    <div class="session-id">${s.id}</div>
                    <div class="session-date">${formatDate(s.created)}</div>
                </div>
                <div>${s.description || ''}</div>
            </div>
        `).join('');

        list.querySelectorAll('.session-item').forEach(item => {
            item.addEventListener('click', () => {
                setSessionUrl(item.dataset.id);
                closeModal(modal);
                loadCatalog();
                loadConversation();
            });
        });
    } catch (err) {
        list.innerHTML = '<p class="no-sessions">Failed to load sessions.</p>';
    }
}
