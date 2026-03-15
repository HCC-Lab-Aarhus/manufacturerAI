/* G-code tab — slice STL with PrusaSlicer, inject pauses + ink paths */

import { API, state } from './state.js';

let _pollTimer = null;
let _filaments  = [];

// ── DOM helpers ───────────────────────────────────────────────────

const statusSpan  = () => document.getElementById('gcode-status');
const heroDiv     = () => document.getElementById('gcode-hero');
const scrollDiv   = () => document.getElementById('gcode-scroll');
const infoDiv     = () => document.getElementById('gcode-info');
const runBtn      = () => document.getElementById('btn-run-gcode');
const filamentSel = () => document.getElementById('gcode-filament');

// ── Tab helpers ───────────────────────────────────────────────────

export function enableGcodeTab(flash = false) {
    const btn = document.querySelector('#pipeline-nav .step[data-step="manufacturing"]');
    if (!btn) return;
    btn.disabled = false;
    btn.classList.toggle('tab-flash', flash);
}

function stopTabFlash() {
    const btn = document.querySelector('#pipeline-nav .step[data-step="manufacturing"]');
    if (btn) btn.classList.remove('tab-flash');
}

export function resetGcodePanel() {
    stopPolling();
    const hero   = heroDiv();
    const scroll = scrollDiv();
    if (hero)   hero.hidden   = false;
    if (scroll) scroll.hidden = true;
    if (infoDiv()) infoDiv().innerHTML = '';
    showStatus('');
}

// ── Filament loader ───────────────────────────────────────────────

async function loadFilaments() {
    try {
        const r = await fetch(`${API}/api/filaments`);
        if (!r.ok) return;
        const data = await r.json();
        _filaments = data.filaments || [];
        const sel = filamentSel();
        if (!sel) return;
        sel.innerHTML = '';
        for (const f of _filaments) {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = f.label;
            sel.appendChild(opt);
        }
    } catch { /* ignore */ }
}

// ── Run pipeline ──────────────────────────────────────────────────

export async function runGcode() {
    if (!state.session) {
        showStatus('No active session', true);
        return;
    }

    const hero  = runBtn();
    const rerun = document.querySelector('#gcode-info .placement-toolbar-rerun');
    if (hero)  { hero.disabled  = true; hero.textContent  = '⏳ Slicing…'; }
    if (rerun) { rerun.disabled = true; rerun.textContent = '⏳ Slicing…'; }

    const filamentId = filamentSel()?.value || null;
    const silverinkOnly = document.getElementById('gcode-silverink-only')?.checked || false;
    const params = new URLSearchParams({ session: state.session, force: 'true' });
    if (filamentId) params.set('filament', filamentId);
    if (silverinkOnly) params.set('silverink_only', 'true');

    try {
        const res = await fetch(`${API}/api/session/gcode?${params}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: res.statusText }));
            const msg = typeof err.detail === 'string'
                ? err.detail
                : err.detail?.reason || JSON.stringify(err.detail);
            showStatus(`Failed: ${msg}`, true);
            renderError(msg);
            if (rerun) rerun.textContent = '❌ Failed';
            return;
        }
        showStatus('Slicing in progress…');
        showScrollView();
        renderRunning();
        pollStatus();
    } catch (e) {
        showStatus(`Error: ${e.message}`, true);
    } finally {
        if (hero)  { hero.disabled  = false; hero.textContent  = 'Generate G-code'; }
    }
}

// ── Session restore ───────────────────────────────────────────────

export async function loadGcodeResult() {
    if (!state.session) return;
    try {
        const r = await fetch(`${API}/api/session/gcode?session=${encodeURIComponent(state.session)}`);
        if (!r.ok) return;
        const st = await r.json();
        if (st.status === 'done') {
            renderResult(st);
            stopTabFlash();
        } else if (st.status === 'running') {
            showScrollView();
            renderRunning();
            pollStatus();
        }
    } catch { /* not ready yet */ }
}

// ── Polling ───────────────────────────────────────────────────────

function stopPolling() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
}

function pollStatus() {
    stopPolling();
    _pollTimer = setInterval(async () => {
        if (!state.session) { stopPolling(); return; }
        try {
            const r = await fetch(
                `${API}/api/session/gcode?session=${encodeURIComponent(state.session)}`
            );
            const st = await r.json();
            if (st.status === 'done') {
                stopPolling();
                renderResult(st);
                stopTabFlash();
            } else if (st.status === 'error') {
                stopPolling();
                renderError(st.message || 'Unknown error');
                showStatus(`Error: ${st.message}`, true);
            }
            // 'running' → keep polling
        } catch { /* transient — keep going */ }
    }, 4000);
}

// ── Render helpers ────────────────────────────────────────────────

function showStatus(msg, isError = false) {
    const span = statusSpan();
    if (!span) return;
    span.textContent = msg;
    span.style.color = isError ? 'var(--error)' : '';
}

function showScrollView() {
    const hero   = heroDiv();
    const scroll = scrollDiv();
    if (hero)   hero.hidden   = true;
    if (scroll) scroll.hidden = false;
}

function renderRunning() {
    const el = infoDiv();
    if (!el) return;
    el.innerHTML = `
        <div style="display:flex; align-items:center; gap:12px; padding:20px 0; color:var(--text-muted);">
            <span style="font-size:24px; animation:spin 1.4s linear infinite; display:inline-block;">⏳</span>
            <div>
                <div style="font-weight:600; color:var(--text);">Slicing in progress…</div>
                <div style="font-size:12px; margin-top:4px;">PrusaSlicer is slicing the STL and injecting pause + ink toolpaths.</div>
            </div>
        </div>
    `;
}

function renderResult(data) {
    const el = infoDiv();
    if (!el) return;
    showScrollView();
    showStatus('');

    const bytes     = data.gcode_bytes  ?? 0;
    const stages    = data.stages       ?? [];

    el.innerHTML = '';

    // Toolbar
    const toolbar = document.createElement('div');
    toolbar.className = 'placement-toolbar';
    toolbar.innerHTML = `
        <span class="placement-toolbar-summary">
            ✅ G-code ready &nbsp;·&nbsp; <strong>${(bytes / 1024).toFixed(0)} kB</strong>
        </span>
    `;

    const rerunBtn = document.createElement('button');
    rerunBtn.className = 'placement-toolbar-rerun';
    rerunBtn.textContent = '↻ Re-slice';
    rerunBtn.addEventListener('click', runGcode);
    toolbar.appendChild(rerunBtn);

    // Silver ink debug toggle (synced with hero checkbox)
    const heroCheckbox = document.getElementById('gcode-silverink-only');
    const toggleWrap = document.createElement('label');
    toggleWrap.style.cssText = 'display:flex; align-items:center; gap:6px; font-size:12px; color:var(--text-muted); cursor:pointer; margin-left:8px;';
    const toggleCb = document.createElement('input');
    toggleCb.type = 'checkbox';
    toggleCb.style.accentColor = 'var(--accent,#58a6ff)';
    toggleCb.checked = heroCheckbox?.checked || false;
    toggleCb.addEventListener('change', () => { if (heroCheckbox) heroCheckbox.checked = toggleCb.checked; });
    toggleWrap.appendChild(toggleCb);
    toggleWrap.appendChild(document.createTextNode('Silver ink debug'));
    toolbar.appendChild(toggleWrap);

    el.appendChild(toolbar);

    // Download buttons
    const dlRow = document.createElement('div');
    dlRow.style.cssText = 'display:flex; gap:8px; margin-top:12px; flex-wrap:wrap;';

    const dlGcode = document.createElement('a');
    dlGcode.href = `${API}/api/session/gcode/download?session=${encodeURIComponent(state.session)}`;
    dlGcode.download = 'enclosure.gcode';
    dlGcode.className = 'placement-toolbar-rerun';
    dlGcode.style.textDecoration = 'none';
    dlGcode.textContent = '⬇ Download .gcode';
    dlRow.appendChild(dlGcode);

    el.appendChild(dlRow);

    // Stats cards
    const cards = document.createElement('div');
    cards.style.cssText = 'margin-top:14px; display:grid; grid-template-columns:1fr 1fr; gap:8px;';
    cards.style.gridTemplateColumns = '1fr';
    cards.innerHTML = `
        <div style="background:var(--surface-raised,#2a2a2a); border-radius:6px; padding:10px 14px;">
            <div style="font-size:11px; color:var(--text-muted); margin-bottom:2px;">G-code size</div>
            <div style="font-size:20px; font-weight:600;">${(bytes / 1024).toFixed(0)} kB</div>
        </div>
    `;
    el.appendChild(cards);

    // Pipeline stages log
    if (stages.length) {
        const stagesDiv = document.createElement('div');
        stagesDiv.style.cssText = 'margin-top:14px;';
        stagesDiv.innerHTML = `<div style="font-size:11px; color:var(--text-muted); margin-bottom:6px; text-transform:uppercase; letter-spacing:.05em;">Pipeline stages</div>`;
        const list = document.createElement('ol');
        list.style.cssText = 'margin:0; padding-left:20px; font-size:12px; color:var(--text-muted); line-height:1.7;';
        for (const s of stages) {
            const li = document.createElement('li');
            li.textContent = s;
            list.appendChild(li);
        }
        stagesDiv.appendChild(list);
        el.appendChild(stagesDiv);
    }
}

function renderError(msg) {
    const el = infoDiv();
    if (!el) return;
    showScrollView();
    el.innerHTML = `
        <div class="placement-error" style="margin-top:12px;">
            <strong>G-code generation failed</strong>
            <p>${esc(msg)}</p>
            <p style="font-size:11px; color:var(--text-muted); margin-top:8px;">
                Make sure PrusaSlicer is installed and <code>enclosure.stl</code> has been compiled.
            </p>
        </div>
        <button class="placement-toolbar-rerun" id="gcode-retry-btn" style="margin-top:10px;">↻ Retry</button>
    `;
    document.getElementById('gcode-retry-btn')?.addEventListener('click', runGcode);
}

function esc(text) {
    const el = document.createElement('span');
    el.textContent = text ?? '';
    return el.innerHTML;
}

// ── Init ──────────────────────────────────────────────────────────

// Load filaments when the module is first imported
loadFilaments();
