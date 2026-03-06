/* Bitmap tab — load and display the trace bitmap verification view */

import { API, state } from './state.js';
import { setData as setViewportData } from './viewport.js';

const statusSpan = () => document.getElementById('bitmap-status');
const infoDiv    = () => document.getElementById('bitmap-info');

export function enableBitmapTab(flash = false) {
    const btn = document.querySelector('#pipeline-nav .step[data-step="bitmap"]');
    if (!btn) return;
    btn.disabled = false;
    btn.classList.toggle('tab-flash', flash);
}

function showStatus(msg, isError = false) {
    const span = statusSpan();
    if (!span) return;
    span.textContent = msg;
    span.style.color = isError ? 'var(--error)' : '';
}

function showResultView() {
    const hero = document.getElementById('bitmap-hero');
    const scroll = document.getElementById('bitmap-scroll');
    if (hero) hero.hidden = true;
    if (scroll) scroll.hidden = false;
}

function renderInfo(data) {
    const el = infoDiv();
    if (!el) return;
    showResultView();

    const outlineVerts = (data.outline || []).map(p => [p.x, p.y]);
    const boardW = outlineVerts.length
        ? (Math.max(...outlineVerts.map(v => v[0])) - Math.min(...outlineVerts.map(v => v[0]))).toFixed(1)
        : '?';
    const boardH = outlineVerts.length
        ? (Math.max(...outlineVerts.map(v => v[1])) - Math.min(...outlineVerts.map(v => v[1]))).toFixed(1)
        : '?';

    el.innerHTML = `
        <div class="vp-section">
            <h4>Bitmap Info</h4>
            <table class="vp-table"><tbody>
                <tr><td>Bitmap resolution</td><td>${data.bitmap_cols} × ${data.bitmap_rows}</td></tr>
                <tr><td>Build plate</td><td>${data.bed_width} × ${data.bed_depth} mm</td></tr>
                <tr><td>Board size</td><td>${boardW} × ${boardH} mm</td></tr>
                <tr><td>Bed offset</td><td>(${data.bed_offset_x.toFixed(1)}, ${data.bed_offset_y.toFixed(1)}) mm</td></tr>
                <tr><td>Cell size</td><td>${(data.bed_width / data.bitmap_cols).toFixed(3)} × ${(data.bed_depth / data.bitmap_rows).toFixed(3)} mm</td></tr>
            </tbody></table>
        </div>
    `;
}

export async function loadBitmapResult() {
    if (!state.session) return;
    try {
        const res = await fetch(
            `${API}/api/session/bitmap?session=${encodeURIComponent(state.session)}`
        );
        if (!res.ok) return;
        const data = await res.json();
        setViewportData('bitmap', data);
        renderInfo(data);
    } catch (e) {
        console.error('loadBitmapResult failed:', e);
    }
}
