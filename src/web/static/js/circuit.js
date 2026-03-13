/* Circuit tab — autonomous circuit agent UI (SSE streaming, read-only) */

import { API, state } from './state.js';
import { setData as setViewportData } from './viewport.js';
import { enablePlacementTab, resetPlacementPanel } from './placement.js';
import { resetRoutingPanel } from './routing.js';

const logDiv    = () => document.getElementById('circuit-log');
const statusSpan = () => document.getElementById('circuit-status');

// ── Enable / reset ───────────────────────────────────────────────

export function enableCircuitTab(flash = false) {
    const btn = document.querySelector('#pipeline-nav .step[data-step="circuit"]');
    if (!btn) return;
    btn.disabled = false;
    btn.classList.toggle('tab-flash', flash);
}

export function resetCircuitPanel() {
    const hero = document.getElementById('circuit-hero');
    const scroll = document.getElementById('circuit-scroll');
    const log = logDiv();
    if (hero) hero.hidden = false;
    if (scroll) scroll.hidden = true;
    if (log) log.innerHTML = '';
    showStatus('');
}

// ── Load existing conversation / result ──────────────────────────

export async function loadCircuitConversation() {
    if (!state.session) return;
    const log = logDiv();
    if (!log) return;
    log.innerHTML = '';

    try {
        const res = await fetch(
            `${API}/api/session/circuit/conversation?session=${encodeURIComponent(state.session)}`
        );
        if (!res.ok) return;
        const messages = await res.json();
        if (!Array.isArray(messages) || messages.length === 0) return;

        renderConversation(log, messages);
    } catch {
        // empty log is fine
    }

    // Check for completed circuit result
    loadCircuitResult();
}

export async function loadCircuitResult() {
    if (!state.session) return;
    try {
        const res = await fetch(
            `${API}/api/session/circuit/result?session=${encodeURIComponent(state.session)}`
        );
        if (!res.ok) return;
        const circuit = await res.json();
        if (circuit && circuit.components) {
            appendCircuitSummary(circuit);
            enablePlacementTab();
        }
    } catch {
        // no circuit yet
    }
}

// ── Run circuit agent ────────────────────────────────────────────

export async function runCircuit() {
    if (!state.session) {
        showStatus('No active session', true);
        return;
    }

    const runBtn = document.getElementById('btn-run-circuit');
    if (runBtn) {
        runBtn.disabled = true;
        runBtn.textContent = '⏳ Running…';
    }
    showStatus('Connecting…');

    // Show log area
    const hero = document.getElementById('circuit-hero');
    const scroll = document.getElementById('circuit-scroll');
    if (hero) hero.hidden = true;
    if (scroll) scroll.hidden = false;

    const log = logDiv();
    if (log) log.innerHTML = '';

    try {
        const url = `${API}/api/session/circuit?session=${encodeURIComponent(state.session)}`;
        const response = await fetch(url, { method: 'POST' });

        if (!response.ok) {
            const err = await response.text();
            appendLog('error', `Server error: ${err}`);
            return;
        }

        await consumeSSE(response, log);
    } catch (e) {
        appendLog('error', `Connection error: ${e.message}`);
    } finally {
        if (runBtn) {
            runBtn.disabled = false;
            runBtn.textContent = 'Run Circuit Agent';
        }
    }
}

// ── SSE consumer ─────────────────────────────────────────────────

async function consumeSSE(response, log) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    let thinkingPre = null;
    let messageBubble = null;
    let messageBubbleText = '';
    let currentBlock = null;
    let toolGroup = null;
    let toolGroupItems = null;

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop();

        for (const part of parts) {
            if (!part.trim()) continue;

            let eventType = 'message';
            let dataStr = '';

            for (const line of part.split('\n')) {
                if (line.startsWith('event: ')) {
                    eventType = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    dataStr += line.slice(6);
                } else if (line.startsWith('data:')) {
                    dataStr += line.slice(5);
                }
            }

            let data = {};
            if (dataStr) {
                try { data = JSON.parse(dataStr); } catch { data = {}; }
            }

            switch (eventType) {
                case 'thinking_start':
                    currentBlock = 'thinking';
                    toolGroup = null;
                    toolGroupItems = null;
                    thinkingPre = createThinkingBubble(log);
                    showStatus('Thinking…');
                    break;

                case 'thinking_delta':
                    if (thinkingPre && data.text) {
                        thinkingPre.textContent += data.text;
                        scrollLog(log);
                    }
                    break;

                case 'message_start':
                    currentBlock = 'message';
                    toolGroup = null;
                    toolGroupItems = null;
                    messageBubble = createMessageBubble(log);
                    messageBubbleText = '';
                    showStatus('');
                    break;

                case 'message_delta':
                    if (messageBubble && data.text) {
                        messageBubbleText += data.text;
                        messageBubble.innerHTML = escapeHtml(messageBubbleText);
                        scrollLog(log);
                    }
                    break;

                case 'block_stop':
                    if (currentBlock === 'thinking') thinkingPre = null;
                    else if (currentBlock === 'message') {
                        messageBubble = null;
                        messageBubbleText = '';
                    }
                    currentBlock = null;
                    break;

                case 'tool_call': {
                    if (!toolGroup) {
                        const g = createToolGroup(log);
                        toolGroup = g.details;
                        toolGroupItems = g.items;
                    }
                    appendToolItem(toolGroupItems, data.name, data.input);
                    showStatus(`Calling ${data.name}…`);
                    break;
                }

                case 'tool_result':
                    if (toolGroupItems) {
                        appendToolItemResult(toolGroupItems, data.name);
                    }
                    showStatus('Thinking…');
                    break;

                case 'circuit':
                    appendCircuitSummary(data.circuit);
                    showStatus('Circuit complete!');
                    enablePlacementTab(true);
                    resetPlacementPanel();
                    resetRoutingPanel();
                    // Disable routing tab until new placement
                    {
                        const rBtn = document.querySelector('#pipeline-nav .step[data-step="routing"]');
                        if (rBtn) { rBtn.disabled = true; rBtn.classList.remove('tab-flash'); }
                    }
                    break;

                case 'error':
                    appendLog('error', data.message || 'Unknown error');
                    showStatus('Error');
                    break;

                case 'done':
                    showStatus('Done');
                    break;
            }
        }
    }
}

// ── Render helpers ────────────────────────────────────────────────

function showStatus(msg, isError = false) {
    const span = statusSpan();
    if (!span) return;
    span.textContent = msg;
    span.style.color = isError ? 'var(--error)' : '';
}

function appendLog(role, text) {
    const log = logDiv();
    if (!log) return;
    const div = document.createElement('div');
    div.className = `chat-bubble ${role}`;
    div.textContent = text;
    log.appendChild(div);
    scrollLog(log);
}

function createThinkingBubble(container) {
    const div = document.createElement('div');
    div.className = 'chat-bubble thinking';
    const details = document.createElement('details');
    details.open = true;
    const summary = document.createElement('summary');
    summary.textContent = '💭 Thinking…';
    const pre = document.createElement('pre');
    pre.className = 'thinking-text';
    details.appendChild(summary);
    details.appendChild(pre);
    div.appendChild(details);
    container.appendChild(div);
    scrollLog(container);
    return pre;
}

function createMessageBubble(container) {
    const div = document.createElement('div');
    div.className = 'chat-bubble assistant';
    container.appendChild(div);
    scrollLog(container);
    return div;
}

function createToolGroup(container) {
    const div = document.createElement('div');
    div.className = 'chat-bubble tool-group';
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.className = 'tool-group-header';
    summary.innerHTML = '<span class="tool-icon">🔧</span> Tool calls';
    const items = document.createElement('div');
    items.className = 'tool-group-items';
    details.appendChild(summary);
    details.appendChild(items);
    div.appendChild(details);
    container.appendChild(div);
    scrollLog(container);
    return { details, items };
}

function appendToolItem(container, name, input) {
    const item = document.createElement('div');
    item.className = 'tool-item';
    item.dataset.toolName = name;
    const inputStr = input && Object.keys(input).length > 0
        ? `(${Object.values(input).map(v => typeof v === 'string' ? v : JSON.stringify(v)).join(', ')})`
        : '()';
    item.innerHTML = `<span class="tool-name">${escapeHtml(name)}</span>${escapeHtml(inputStr)}`;
    container.appendChild(item);

    const summary = container.parentElement.querySelector('.tool-group-header');
    const count = container.children.length;
    summary.innerHTML = `<span class="tool-icon">🔧</span> ${count} tool call${count > 1 ? 's' : ''}`;
    scrollLog(container.closest('.chat-container, .placement-scroll, #circuit-scroll'));
}

function appendToolItemResult(container, name) {
    const items = container.querySelectorAll(`.tool-item[data-tool-name="${name}"]`);
    const item = items[items.length - 1];
    if (!item) return;
    const nameSpan = item.querySelector('.tool-name');
    if (nameSpan) nameSpan.textContent = `✓ ${name}`;
}

function appendCircuitSummary(circuit) {
    const log = logDiv();
    if (!log) return;
    const div = document.createElement('div');
    div.className = 'chat-bubble design-result';

    const compCount = circuit?.components?.length || 0;
    const netCount = circuit?.nets?.length || 0;

    div.innerHTML = `
        <div class="design-summary">
            <strong>✅ Circuit Validated</strong>
            <span>${compCount} components · ${netCount} nets</span>
        </div>
        <details>
            <summary>View circuit JSON</summary>
            <pre class="design-json">${escapeHtml(JSON.stringify(circuit, null, 2))}</pre>
        </details>
    `;
    log.appendChild(div);
    scrollLog(log);
}

function renderConversation(container, messages) {
    for (const msg of messages) {
        if (msg.role === 'assistant' && Array.isArray(msg.content)) {
            for (const block of msg.content) {
                if (block.type === 'thinking' && block.thinking) {
                    const pre = createThinkingBubble(container);
                    pre.textContent = block.thinking;
                } else if (block.type === 'text' && block.text) {
                    const div = createMessageBubble(container);
                    div.innerHTML = escapeHtml(block.text);
                } else if (block.type === 'tool_use') {
                    // Show tool calls in collapsed groups
                }
            }
        }
    }
}

function scrollLog(container) {
    if (container) container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const el = document.createElement('div');
    el.textContent = text;
    return el.innerHTML;
}
