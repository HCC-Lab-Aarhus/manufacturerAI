/* Design tab — chat interface for the LLM design agent (SSE streaming) */

import { API, state } from './state.js';
import { onSessionCreated, setSessionLabel } from './session.js';
import { setData as setViewportData, clearData as clearViewportData } from './viewport.js';

const messagesDiv = () => document.getElementById('chat-messages');
const statusSpan = () => document.getElementById('design-status');

// ── Load conversation history ─────────────────────────────────────

/** Load and render saved conversation for the current session. */
export async function loadConversation() {
    const container = messagesDiv();
    if (!container) return;
    container.innerHTML = '';

    if (!state.session) return;

    try {
        const res = await fetch(
            `${API}/api/session/conversation?session=${encodeURIComponent(state.session)}`
        );
        if (!res.ok) return;
        const messages = await res.json();
        if (!Array.isArray(messages) || messages.length === 0) return;

        renderConversation(messages);
    } catch {
        // Silently ignore — empty chat is fine
    }

    // Fetch current token count and update the meter
    if (state.session) {
        try {
            const res = await fetch(`${API}/api/session/tokens?session=${encodeURIComponent(state.session)}`);
            if (res.ok) {
                const t = await res.json();
                updateTokenMeter(t.input_tokens, t.budget);
            }
        } catch { /* best-effort */ }
    }
}

/**
 * Render a saved Anthropic-format message list into the chat UI.
 * Produces the same DOM structure as the live SSE stream.
 */
function renderConversation(messages) {
    for (const msg of messages) {
        if (msg.role === 'user') {
            if (typeof msg.content === 'string') {
                appendMessage('user', msg.content);
            }
            // tool_result arrays — render as grouped results
            if (Array.isArray(msg.content)) {
                const toolResults = msg.content.filter(b => b.type === 'tool_result');
                if (toolResults.length > 0) {
                    // Results are rendered inline with the preceding tool group
                    // (already done via appendToolCallStatic marking ✓)
                }
            }
        } else if (msg.role === 'assistant') {
            renderAssistantBlocks(msg.content);
        }
    }

    // If a design was completed, show the design result at the bottom
    loadDesignResult();
}

/** Render an array of content blocks (thinking, text, tool_use). */
function renderAssistantBlocks(blocks) {
    if (!Array.isArray(blocks)) return;

    // Group tool_use blocks together
    let toolItems = [];

    const flushToolItems = () => {
        if (toolItems.length === 0) return;
        const div = document.createElement('div');
        div.className = 'chat-bubble tool-group';
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.className = 'tool-group-header';
        summary.innerHTML = `<span class="tool-icon">🔧</span> ${toolItems.length} tool call${toolItems.length > 1 ? 's' : ''}`;
        const items = document.createElement('div');
        items.className = 'tool-group-items';
        for (const el of toolItems) items.appendChild(el);
        details.appendChild(summary);
        details.appendChild(items);
        div.appendChild(details);
        messagesDiv().appendChild(div);
        toolItems = [];
    };

    for (const block of blocks) {
        switch (block.type) {
            case 'thinking':
                flushToolItems();
                if (block.thinking) {
                    const pre = createThinkingBubble(false);
                    pre.textContent = block.thinking;
                }
                break;
            case 'text':
                flushToolItems();
                if (block.text) {
                    const div = createMessageBubble();
                    div.textContent = block.text;
                }
                break;
            case 'tool_use':
                toolItems.push(appendToolCallStatic(block.name, block.input));
                break;
        }
    }
    flushToolItems();
}

/** If this session has a design.json, render the design result box */
async function loadDesignResult() {
    if (!state.session) return;
    try {
        const res = await fetch(
            `${API}/api/session/design/result?session=${encodeURIComponent(state.session)}`
        );
        if (!res.ok) return;
        const design = await res.json();
        if (design && design.components) {
            appendDesignResult(design);
            setViewportData('design', design);
        }
    } catch {
        // No design yet — that's fine
    }
}

/** Send a design prompt and stream SSE events */
export async function sendDesignPrompt() {
    const input = document.getElementById('chat-input');
    const prompt = input.value.trim();
    if (!prompt) return;

    // Show user message and clear input
    appendMessage('user', prompt);
    input.value = '';
    input.disabled = true;
    document.getElementById('btn-send-design').disabled = true;
    statusSpan().textContent = 'Connecting…';

    try {
        // Build URL — session param is optional (server auto-creates if missing)
        let url = `${API}/api/session/design`;
        if (state.session) {
            url += `?session=${encodeURIComponent(state.session)}`;
        }

        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt }),
        });

        if (!response.ok) {
            const err = await response.text();
            appendMessage('error', `Server error: ${err}`);
            return;
        }

        await consumeSSE(response);
    } catch (e) {
        appendMessage('error', `Connection error: ${e.message}`);
    } finally {
        input.disabled = false;
        document.getElementById('btn-send-design').disabled = false;
        statusSpan().textContent = '';
    }
}

// ── SSE parser ────────────────────────────────────────────────────

/**
 * Parse an SSE stream from a fetch Response.
 * We use fetch + ReadableStream instead of EventSource because
 * EventSource only supports GET requests.
 */
async function consumeSSE(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // Track current live-updating elements
    let thinkingPre = null;   // <pre> inside the thinking bubble
    let messageBubble = null; // <div> for the assistant text bubble
    let currentBlock = null;  // 'thinking' | 'message' | null
    let toolGroup = null;     // current tool group <details> element
    let toolGroupItems = null; // container for tool items inside the group

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE messages are separated by double newlines
        const parts = buffer.split('\n\n');
        buffer = parts.pop(); // last part is incomplete

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

            // ── Handle each event type ──

            switch (eventType) {
                case 'session_created':
                    onSessionCreated(data.session_id);
                    break;

                case 'thinking_start':
                    currentBlock = 'thinking';
                    toolGroup = null;
                    toolGroupItems = null;
                    thinkingPre = createThinkingBubble();
                    statusSpan().textContent = 'Thinking…';
                    break;

                case 'thinking_delta':
                    if (thinkingPre && data.text) {
                        thinkingPre.textContent += data.text;
                        scrollToBottom();
                    }
                    break;

                case 'message_start':
                    currentBlock = 'message';
                    toolGroup = null;
                    toolGroupItems = null;
                    messageBubble = createMessageBubble();
                    statusSpan().textContent = '';
                    break;

                case 'message_delta':
                    if (messageBubble && data.text) {
                        messageBubble.textContent += data.text;
                        scrollToBottom();
                    }
                    break;

                case 'block_stop':
                    if (currentBlock === 'thinking') {
                        thinkingPre = null;
                    } else if (currentBlock === 'message') {
                        messageBubble = null;
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
                    statusSpan().textContent = `Calling ${data.name}…`;
                    break;
                }

                case 'tool_result':
                    if (toolGroupItems) {
                        appendToolItemResult(toolGroupItems, data.name, data.content);
                    }
                    statusSpan().textContent = 'Thinking…';
                    break;

                case 'design':
                    appendDesignResult(data.design);
                    setViewportData('design', data.design);
                    statusSpan().textContent = 'Design complete!';
                    break;

                case 'error':
                    appendMessage('error', data.message || 'Unknown error');
                    statusSpan().textContent = 'Error';
                    break;

                case 'session_named':
                    if (data.name && state.session) {
                        setSessionLabel(state.session, data.name);
                    }
                    break;

                case 'token_usage':
                    updateTokenMeter(data.input_tokens, data.budget);
                    break;

                case 'done': {
                    // Refresh token count from server after turn completes
                    // (includes any tool_result messages appended after last count)
                    if (state.session) {
                        fetch(`${API}/api/session/tokens?session=${encodeURIComponent(state.session)}`)
                            .then(r => r.ok ? r.json() : null)
                            .then(t => { if (t) updateTokenMeter(t.input_tokens, t.budget); })
                            .catch(() => {});
                    }
                    break;
                }

                case 'done':
                    statusSpan().textContent = 'Done';
                    break;
            }
        }
    }
}

// ── Render helpers ────────────────────────────────────────────────

function appendMessage(role, text) {
    const div = document.createElement('div');
    div.className = `chat-bubble ${role}`;
    div.textContent = text;
    messagesDiv().appendChild(div);
    scrollToBottom();
}

/** Create an empty thinking bubble and return the <pre> for delta appending */
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
    messagesDiv().appendChild(div);
    scrollToBottom();
    return pre;
}

/** Create an empty assistant message bubble and return it for delta appending */
function createMessageBubble() {
    const div = document.createElement('div');
    div.className = 'chat-bubble assistant';
    messagesDiv().appendChild(div);
    scrollToBottom();
    return div;
}

/** Create a tool group container (collapsed <details>) and return refs */
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
    messagesDiv().appendChild(div);
    scrollToBottom();
    return { details, items };
}

/** Add a tool call entry inside a tool group */
function appendToolItem(container, name, input) {
    const item = document.createElement('div');
    item.className = 'tool-item';
    item.dataset.toolName = name;
    const inputStr = input && Object.keys(input).length > 0
        ? `(${Object.values(input).map(v => typeof v === 'string' ? v : JSON.stringify(v)).join(', ')})`
        : '()';
    item.innerHTML = `<span class="tool-name">${escapeHtml(name)}</span>${escapeHtml(inputStr)}`;
    container.appendChild(item);

    // Update summary count
    const summary = container.parentElement.querySelector('.tool-group-header');
    const count = container.children.length;
    summary.innerHTML = `<span class="tool-icon">🔧</span> ${count} tool call${count > 1 ? 's' : ''}`;
    scrollToBottom();
}

/** Append a result line to the most recent matching tool item */
function appendToolItemResult(container, name, content) {
    // Find the last tool item for this name
    const items = container.querySelectorAll(`.tool-item[data-tool-name="${name}"]`);
    const item = items[items.length - 1];
    if (!item) return;
    // Mark as done
    const nameSpan = item.querySelector('.tool-name');
    if (nameSpan) nameSpan.textContent = `✓ ${name}`;
}

/** Render tool calls from saved conversation (static, not streaming) */
function appendToolCallStatic(name, input) {
    const item = document.createElement('div');
    item.className = 'tool-item';
    const inputStr = input && Object.keys(input).length > 0
        ? `(${Object.values(input).map(v => typeof v === 'string' ? v : JSON.stringify(v)).join(', ')})`
        : '()';
    item.innerHTML = `<span class="tool-name">✓ ${escapeHtml(name)}</span>${escapeHtml(inputStr)}`;
    return item;
}

function appendDesignResult(design) {
    const div = document.createElement('div');
    div.className = 'chat-bubble design-result';

    const compCount = design.components?.length || 0;
    const netCount = design.nets?.length || 0;
    const vertCount = (Array.isArray(design.outline) ? design.outline : design.outline?.vertices)?.length || 0;

    div.innerHTML = `
        <div class="design-summary">
            <strong>✅ Design Validated</strong>
            <span>${compCount} components · ${netCount} nets · ${vertCount}-vertex outline</span>
        </div>
        <details>
            <summary>View design JSON</summary>
            <pre class="design-json">${escapeHtml(JSON.stringify(design, null, 2))}</pre>
        </details>
    `;
    messagesDiv().appendChild(div);
    scrollToBottom();
}

function scrollToBottom() {
    const container = messagesDiv();
    const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 40;
    if (atBottom) container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const el = document.createElement('div');
    el.textContent = text;
    return el.innerHTML;
}

// ── Token meter ───────────────────────────────────────────────────

function updateTokenMeter(inputTokens, budget) {
    const meter = document.getElementById('token-meter');
    const fill = meter?.querySelector('.token-pie-fill');
    const label = document.getElementById('token-label');
    if (!meter || !fill || !label) return;

    meter.hidden = false;

    const pct = Math.min(inputTokens / budget, 1);
    const dashLen = (pct * 100).toFixed(1);
    fill.setAttribute('stroke-dasharray', `${dashLen} 100`);

    // Color thresholds
    fill.classList.remove('warn', 'critical');
    if (pct >= 0.9) fill.classList.add('critical');
    else if (pct >= 0.7) fill.classList.add('warn');

    const usedK = (inputTokens / 1000).toFixed(1);
    const budgetK = (budget / 1000).toFixed(0);
    label.textContent = `${usedK}k / ${budgetK}k`;
}
