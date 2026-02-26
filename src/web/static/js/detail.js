/* Component detail modal + SVG pin diagram */

import { openModal } from './utils.js';

/** Open the detail modal for a component. */
export function showComponentDetail(comp) {
    const modal = document.getElementById('component-modal');
    document.getElementById('component-modal-title').textContent = comp.name;

    const detail = document.getElementById('component-detail');
    try {
        detail.innerHTML = renderDetail(comp);
    } catch (err) {
        console.error('Error rendering component detail:', err);
        detail.innerHTML = `
            <p style="color:var(--error)">Error rendering: ${err.message}</p>
            <pre style="font-size:11px;color:var(--text-dim)">${JSON.stringify(comp, null, 2)}</pre>`;
    }

    openModal(modal);
}

// ── Detail HTML ───────────────────────────────────────────────────

function renderDetail(comp) {
    return `
        <div class="detail-desc">${comp.description}</div>

        <div class="card-badges" style="margin-bottom: 16px;">
            <span class="badge badge-category">${comp.category}</span>
            <span class="badge badge-mounting">${comp.mounting.style} mount</span>
            ${comp.ui_placement ? '<span class="badge badge-ui">UI placement</span>' : ''}
            ${comp.mounting.blocks_routing ? '<span class="badge" style="background:var(--error-bg);color:var(--error);border:1px solid var(--error)">blocks routing</span>' : ''}
        </div>

        ${renderBodySection(comp)}
        ${renderMountingSection(comp)}
        ${renderPinsSection(comp)}
        ${renderInternalNetsSection(comp)}
        ${renderPinGroupsSection(comp)}
        ${renderConfigurableSection(comp)}
    `;
}

function renderBodySection(comp) {
    const dims = comp.body.shape === 'rect'
        ? `<tr><th>Width</th><td>${comp.body.width_mm} mm</td></tr>
           <tr><th>Length</th><td>${comp.body.length_mm} mm</td></tr>`
        : `<tr><th>Diameter</th><td>${comp.body.diameter_mm} mm</td></tr>`;

    return `
        <div class="detail-section">
            <h3>Body</h3>
            <table class="detail-table">
                <tr><th>Shape</th><td>${comp.body.shape}</td></tr>
                ${dims}
                <tr><th>Height</th><td>${comp.body.height_mm} mm</td></tr>
            </table>
        </div>`;
}

function renderMountingSection(comp) {
    const m = comp.mounting;
    let extra = '';

    if (m.cap) {
        extra += `
            <tr><th>Cap diameter</th><td>${m.cap.diameter_mm} mm</td></tr>
            <tr><th>Cap height</th><td>${m.cap.height_mm} mm</td></tr>
            <tr><th>Cap clearance</th><td>${m.cap.hole_clearance_mm} mm</td></tr>`;
    }
    if (m.hatch) {
        extra += `
            <tr><th>Hatch enabled</th><td>${m.hatch.enabled ? 'Yes' : 'No'}</td></tr>
            <tr><th>Hatch clearance</th><td>${m.hatch.clearance_mm} mm</td></tr>
            <tr><th>Hatch thickness</th><td>${m.hatch.thickness_mm} mm</td></tr>`;
    }

    return `
        <div class="detail-section">
            <h3>Mounting</h3>
            <table class="detail-table">
                <tr><th>Default style</th><td>${m.style}</td></tr>
                <tr><th>Allowed styles</th><td>${m.allowed_styles.join(', ')}</td></tr>
                <tr><th>Keepout margin</th><td>${m.keepout_margin_mm} mm</td></tr>
                <tr><th>Blocks routing</th><td>${m.blocks_routing ? 'Yes' : 'No'}</td></tr>
                ${extra}
            </table>
        </div>`;
}

function renderPinsSection(comp) {
    const rows = comp.pins.map(p => `
        <tr>
            <td>${p.id}</td>
            <td style="font-family:var(--font)">${p.label}</td>
            <td>[${p.position_mm[0]}, ${p.position_mm[1]}]</td>
            <td>${p.direction}</td>
            <td>${p.voltage_v !== null ? p.voltage_v + 'V' : '—'}</td>
            <td>${p.current_max_ma !== null ? p.current_max_ma + 'mA' : '—'}</td>
            <td>${p.hole_diameter_mm}mm</td>
        </tr>`).join('');

    return `
        <div class="detail-section">
            <h3>Pins (${comp.pins.length})</h3>
            ${renderPinDiagram(comp)}
            <table class="detail-table">
                <tr>
                    <th>ID</th><th>Label</th><th>Position</th>
                    <th>Direction</th><th>Voltage</th><th>Current</th><th>Hole ⌀</th>
                </tr>
                ${rows}
            </table>
        </div>`;
}

function renderInternalNetsSection(comp) {
    if (!comp.internal_nets || comp.internal_nets.length === 0) return '';
    return `
        <div class="detail-section">
            <h3>Internal Nets</h3>
            <table class="detail-table">
                ${comp.internal_nets.map((net, i) =>
                    `<tr><th>Net ${i + 1}</th><td>${net.join(' ↔ ')}</td></tr>`
                ).join('')}
            </table>
        </div>`;
}

function renderPinGroupsSection(comp) {
    if (!comp.pin_groups) return '';
    return `
        <div class="detail-section">
            <h3>Pin Groups</h3>
            <table class="detail-table">
                <tr><th>Group</th><th>Pins</th><th>Allocatable</th><th>Net</th><th>Capabilities</th></tr>
                ${comp.pin_groups.map(g => `
                    <tr>
                        <td>${g.id}</td>
                        <td style="max-width:200px;word-wrap:break-word">${g.pin_ids.join(', ')}</td>
                        <td>${g.allocatable ? '✓' : '—'}</td>
                        <td>${g.fixed_net || '—'}</td>
                        <td>${g.capabilities ? g.capabilities.join(', ') : '—'}</td>
                    </tr>`).join('')}
            </table>
        </div>`;
}

function renderConfigurableSection(comp) {
    if (!comp.configurable) return '';
    return `
        <div class="detail-section">
            <h3>Configurable</h3>
            <pre style="font-family:var(--mono);font-size:12px;color:var(--text-dim);background:var(--surface2);padding:12px;border-radius:var(--radius);overflow-x:auto">${JSON.stringify(comp.configurable, null, 2)}</pre>
        </div>`;
}

// ── SVG Pin Diagram ───────────────────────────────────────────────

const PIN_COLORS = { in: '#58a6ff', out: '#f85149', bidirectional: '#d29922' };
const SCALE = 12;   // mm → px
const PAD = 8;

function renderPinDiagram(comp) {
    const { pins } = comp;
    if (pins.length === 0) return '';

    const [bodyW, bodyH] = comp.body.shape === 'rect'
        ? [comp.body.width_mm, comp.body.length_mm]
        : [comp.body.diameter_mm, comp.body.diameter_mm];

    // Bounding box (component-center-relative)
    const xs = pins.map(p => p.position_mm[0]).concat([-bodyW / 2, bodyW / 2]);
    const ys = pins.map(p => p.position_mm[1]).concat([-bodyH / 2, bodyH / 2]);
    const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
    const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];

    const svgW = (maxX - minX) * SCALE + PAD * 2 + 60;
    const svgH = (maxY - minY) * SCALE + PAD * 2 + 40;
    const ox = PAD + 30 - minX * SCALE;
    const oy = PAD + 20 - minY * SCALE;

    const parts = [`<svg width="${Math.max(svgW, 120)}" height="${Math.max(svgH, 80)}" xmlns="http://www.w3.org/2000/svg">`];

    // Body outline
    if (comp.body.shape === 'circle') {
        const r = (comp.body.diameter_mm / 2) * SCALE;
        parts.push(`<circle cx="${ox}" cy="${oy}" r="${r}" fill="none" stroke="#30363d" stroke-width="1.5" stroke-dasharray="4,3"/>`);
    } else {
        const bx = ox + (-bodyW / 2) * SCALE;
        const by = oy + (-bodyH / 2) * SCALE;
        parts.push(`<rect x="${bx}" y="${by}" width="${bodyW * SCALE}" height="${bodyH * SCALE}" fill="none" stroke="#30363d" stroke-width="1.5" stroke-dasharray="4,3" rx="2"/>`);
    }

    // Pins
    for (const p of pins) {
        const px = ox + p.position_mm[0] * SCALE;
        const py = oy + p.position_mm[1] * SCALE;
        const color = PIN_COLORS[p.direction] || '#8b949e';
        const r = Math.max((p.hole_diameter_mm / 2) * SCALE * 1.5, 4);
        parts.push(`<circle cx="${px}" cy="${py}" r="${r}" fill="${color}" opacity="0.8"/>`);
        parts.push(`<text x="${px}" y="${py - r - 4}" text-anchor="middle" fill="#e6edf3" font-size="10" font-family="Consolas, monospace">${p.id}</text>`);
    }

    // Legend
    const ly = svgH - 12;
    parts.push(
        `<circle cx="10" cy="${ly}" r="4" fill="#58a6ff"/>`,
        `<text x="18" y="${ly + 3}" fill="#8b949e" font-size="9">in</text>`,
        `<circle cx="42" cy="${ly}" r="4" fill="#f85149"/>`,
        `<text x="50" y="${ly + 3}" fill="#8b949e" font-size="9">out</text>`,
        `<circle cx="78" cy="${ly}" r="4" fill="#d29922"/>`,
        `<text x="86" y="${ly + 3}" fill="#8b949e" font-size="9">bidir</text>`,
    );

    parts.push('</svg>');
    return `<div class="pin-diagram">${parts.join('')}</div>`;
}
