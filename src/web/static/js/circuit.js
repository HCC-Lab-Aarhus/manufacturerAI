/* Circuit tab — automated agent run (no user chat, run-button + read-only log) */

import { API, state } from './state.js';
import { enablePlacementTab, resetPlacementPanel } from './placement.js';
import { resetRoutingPanel } from './routing.js';

const statusSpan = () => document.getElementById('circuit-status');
const logDiv     = () => document.getElementById('circuit-log');
const infoDiv    = () => document.getElementById('circuit-info');
const heroDiv    = () => document.getElementById('circuit-hero');
const scrollDiv  = () => document.getElementById('circuit-scroll');
const runBtn     = () => document.getElementById('btn-run-circuit');

export function enableCircuitTab(flash = false) {
    const btn = document.querySelector('#pipeline-nav .step[data-step="circuit"]');
    if (!btn) return;
    btn.disabled = false;
    btn.classList.toggle('tab-flash', flash);
}

export function resetCircuitPanel() {
    const hero = heroDiv();
    const scroll = scrollDiv();
    const log = logDiv();
    const info = infoDiv();
    if (hero) hero.hidden = false;
    if (scroll) scroll.hidden = true;
    if (log) log.innerHTML = '';
    if (info) info.innerHTML = '';
    showStatus('');
}

function showStatus(text, isError = false) {
    const el = statusSpan();
    if (!el) return;
    el.textContent = text;
    el.style.color = isError ? 'var(--error, #f44)' : '';
}

export async function runCircuit() {
    if (!state.session) {
        showStatus('No active session', true);
        return;
    }

    const btn = runBtn();
    if (btn) btn.disabled = true;
    showStatus('Starting circuit agent…');

    const hero = heroDiv();
    const scroll = scrollDiv();
    const log = logDiv();
    if (hero) hero.hidden = true;
    if (scroll) scroll.hidden = false;
    if (log) log.innerHTML = '';

    try {
        const url = `${API}/api/session/circuit?session=${encodeURIComponent(state.session)}`;
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });

        if (!response.ok) {
            const err = await response.text();
            showStatus(`Error: ${err}`, true);
            if (hero) hero.hidden = false;
            if (scroll) scroll.hidden = true;
            if (btn) btn.disabled = false;
            return;
        }

        await consumeSSE(response);
    } catch (e) {
        showStatus(`Connection error: ${e.message}`, true);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function consumeSSE(response) {
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
                if (line.startsWith('event: ')) eventType = line.slice(7).trim();
                else if (line.startsWith('data: ')) dataStr += line.slice(6);
                else if (line.startsWith('data:')) dataStr += line.slice(5);
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
                    thinkingPre = createThinkingBubble();
                    showStatus('Thinking…');
                    break;

                case 'thinking_delta':
                    if (thinkingPre && data.text) {
                        thinkingPre.textContent += data.text;
                        scrollLog();
                    }
                    break;

                case 'message_start':
                    currentBlock = 'message';
                    toolGroup = null;
                    toolGroupItems = null;
                    messageBubble = createMessageBubble();
                    messageBubbleText = '';
                    showStatus('');
                    break;

                case 'message_delta':
                    if (messageBubble && data.text) {
                        messageBubbleText += data.text;
                        messageBubble.innerHTML = renderMarkdown(messageBubbleText);
                        scrollLog();
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
                        const g = createToolGroup();
                        toolGroup = g.details;
                        toolGroupItems = g.items;
                    }
                    appendToolItem(toolGroupItems, data.name, data.input);
                    showStatus(`Calling ${data.name}…`);
                    break;
                }

                case 'tool_result':
                    if (toolGroupItems) appendToolItemResult(toolGroupItems, data.name);
                    showStatus('Thinking…');
                    break;

                case 'circuit':
                    appendCircuitResult(data.circuit);
                    showStatus('Circuit complete!');
                    enablePlacementTab(true);
                    resetPlacementPanel();
                    resetRoutingPanel();
                    {
                        const rBtn = document.querySelector('#pipeline-nav .step[data-step="routing"]');
                        if (rBtn) { rBtn.disabled = true; rBtn.classList.remove('tab-flash'); }
                    }
                    break;

                case 'error':
                    appendLogMessage('error', data.message || 'Unknown error');
                    showStatus('Error', true);
                    break;

                case 'done':
                    if (!statusSpan()?.textContent?.includes('complete')) {
                        showStatus('Done');
                    }
                    break;
            }
        }
    }
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
            const hero = heroDiv();
            const scroll = scrollDiv();
            if (hero) hero.hidden = true;
            if (scroll) scroll.hidden = false;
            appendCircuitResult(circuit);
            enablePlacementTab();
        }
    } catch { /* no circuit yet */ }
}

// ── Render helpers ────────────────────────────────────────────────

function appendLogMessage(role, text) {
    const div = document.createElement('div');
    div.className = `chat-bubble ${role}`;
    div.textContent = text;
    logDiv()?.appendChild(div);
    scrollLog();
}

function createThinkingBubble(open = true) {
    const div = document.createElement('div');
    div.className = 'chat-bubble thinking';
    const details = document.createElement('details');
    details.open = open;
    const summary = document.createElement('summary');
    summary.textContent = '💭 Thinking…';
    const pre = document.createElement('pre');
    pre.className = 'thinking-text';
    details.appendChild(summary);
    details.appendChild(pre);
    div.appendChild(details);
    logDiv()?.appendChild(div);
    scrollLog();
    return pre;
}

function createMessageBubble() {
    const div = document.createElement('div');
    div.className = 'chat-bubble assistant';
    logDiv()?.appendChild(div);
    scrollLog();
    return div;
}

function createToolGroup() {
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
    logDiv()?.appendChild(div);
    scrollLog();
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
    scrollLog();
}

function appendToolItemResult(container, name) {
    const items = container.querySelectorAll(`.tool-item[data-tool-name="${name}"]`);
    const item = items[items.length - 1];
    if (!item) return;
    const nameSpan = item.querySelector('.tool-name');
    if (nameSpan) nameSpan.textContent = `✓ ${name}`;
}

function appendCircuitResult(circuit) {
    const compCount = circuit.components?.length || 0;
    const netCount = circuit.nets?.length || 0;
    const uiCount = circuit.components?.filter(c => c.ui_placement)?.length || 0;
    const summaryText = `${compCount} components (${uiCount} UI) · ${netCount} nets`;

    const div = document.createElement('div');
    div.className = 'chat-bubble design-result';
    div.innerHTML = `
        <div class="design-summary">
            <strong>✅ Circuit Validated</strong>
            <span>${summaryText}</span>
        </div>
        <details>
            <summary>View circuit JSON</summary>
            <pre class="design-json">${escapeHtml(JSON.stringify(circuit, null, 2))}</pre>
        </details>
    `;
    logDiv()?.appendChild(div);

    const info = infoDiv();
    if (info) {
        info.innerHTML = `<p><strong>Circuit:</strong> ${summaryText}</p>`;
    }
    scrollLog();
}

function scrollLog() {
    const container = logDiv();
    if (!container) return;
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
    if (atBottom) container.scrollTop = container.scrollHeight;
}

function renderMarkdown(text) {
    const lines = text.split('\n');
    const out = [];
    let inList = false;
    const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
    for (const line of lines) {
        if (/^[-*] /.test(line)) {
            if (!inList) { out.push('<ul>'); inList = true; }
            out.push(`<li>${inlineMarkdown(line.slice(2))}</li>`);
        } else {
            closeList();
            if (line.trim() === '') out.push('<br>');
            else out.push(`<p>${inlineMarkdown(line)}</p>`);
        }
    }
    closeList();
    return out.join('');
}

function inlineMarkdown(raw) {
    const esc = raw
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    return esc
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>');
}

function escapeHtml(text) {
    const el = document.createElement('div');
    el.textContent = text;
    return el.innerHTML;
}
