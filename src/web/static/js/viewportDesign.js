/**
 * Viewport handler for the Design step.
 *
 * Renders a visual preview of the DesignSpec:
 *   - SVG outline with UI placement markers
 *   - Component summary table
 *   - Net connection list
 *
 * Data shape (matches DesignSpec JSON from the backend):
 * {
 *   components: [{ catalog_id, instance_id, config?, mounting_style? }]
 *   nets:       [{ id, pins: ["instance:pin", …] }]
 *   outline:    { vertices: [[x,y], …], edges: [{ style, curve?, radius_mm? }] }
 *   ui_placements: [{ instance_id, x_mm, y_mm }]
 * }
 */

import { registerHandler } from './viewport.js';

// ── Register ──────────────────────────────────────────────────

registerHandler('design', {
    label: 'Design Preview',
    placeholder: 'Submit a design prompt to see the preview',

    render(el, design) {
        el.innerHTML = '';
        el.appendChild(buildPreview(design));
    },

    clear(el) {
        el.innerHTML = '<p class="viewport-empty">Submit a design prompt to see the preview</p>';
    },
});


// ── Preview builder ───────────────────────────────────────────

function buildPreview(design) {
    const wrap = document.createElement('div');
    wrap.className = 'vp-design';

    wrap.appendChild(buildOutlineSVG(design));
    wrap.appendChild(buildComponentList(design.components));
    wrap.appendChild(buildNetList(design.nets));

    return wrap;
}


// ── Outline SVG ───────────────────────────────────────────────

const SCALE = 4;     // mm → px
const PAD   = 32;    // px padding around the SVG content

function buildOutlineSVG(design) {
    const { outline, ui_placements = [] } = design;
    const verts = outline?.vertices ?? [];

    if (verts.length < 3) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'Outline has fewer than 3 vertices';
        return p;
    }

    // Bounding box
    const xs = verts.map(v => v[0]);
    const ys = verts.map(v => v[1]);
    const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
    const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];

    const w = (maxX - minX) * SCALE + PAD * 2;
    const h = (maxY - minY) * SCALE + PAD * 2;
    const ox = PAD - minX * SCALE;
    const oy = PAD - minY * SCALE;

    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('class', 'vp-outline-svg');

    // Grid (subtle)
    const gridSize = 10 * SCALE;  // 10 mm grid
    const grid = document.createElementNS(NS, 'pattern');
    grid.id = 'vp-grid';
    grid.setAttribute('width', gridSize);
    grid.setAttribute('height', gridSize);
    grid.setAttribute('patternUnits', 'userSpaceOnUse');
    const gridLine1 = document.createElementNS(NS, 'path');
    gridLine1.setAttribute('d', `M ${gridSize} 0 L 0 0 0 ${gridSize}`);
    gridLine1.setAttribute('fill', 'none');
    gridLine1.setAttribute('stroke', 'rgba(255,255,255,0.04)');
    gridLine1.setAttribute('stroke-width', '1');
    grid.appendChild(gridLine1);

    const defs = document.createElementNS(NS, 'defs');
    defs.appendChild(grid);
    svg.appendChild(defs);

    const gridRect = document.createElementNS(NS, 'rect');
    gridRect.setAttribute('width', '100%');
    gridRect.setAttribute('height', '100%');
    gridRect.setAttribute('fill', 'url(#vp-grid)');
    svg.appendChild(gridRect);

    // Build outline path with proper rounded corners
    const edges = outline.edges ?? [];
    const pathD = buildOutlinePath(verts, edges, ox, oy, SCALE);
    const pathEl = document.createElementNS(NS, 'path');
    pathEl.setAttribute('d', pathD);
    pathEl.setAttribute('class', 'vp-outline-path');
    svg.appendChild(pathEl);

    // UI placements
    for (const up of ui_placements) {
        const cx = ox + up.x_mm * SCALE;
        const cy = oy + up.y_mm * SCALE;

        const marker = document.createElementNS(NS, 'circle');
        marker.setAttribute('cx', cx);
        marker.setAttribute('cy', cy);
        marker.setAttribute('r', '6');
        marker.setAttribute('class', 'vp-ui-marker');

        const label = document.createElementNS(NS, 'text');
        label.setAttribute('x', cx);
        label.setAttribute('y', cy - 10);
        label.setAttribute('class', 'vp-ui-label');
        label.textContent = up.instance_id;

        svg.appendChild(marker);
        svg.appendChild(label);
    }

    // Dimension labels
    const dimLabel = document.createElementNS(NS, 'text');
    dimLabel.setAttribute('x', ox + ((maxX - minX) / 2) * SCALE);
    dimLabel.setAttribute('y', h - 6);
    dimLabel.setAttribute('class', 'vp-dim-label');
    dimLabel.textContent = `${(maxX - minX).toFixed(1)} mm`;
    svg.appendChild(dimLabel);

    const dimLabelV = document.createElementNS(NS, 'text');
    dimLabelV.setAttribute('x', 8);
    dimLabelV.setAttribute('y', oy + ((maxY - minY) / 2) * SCALE);
    dimLabelV.setAttribute('class', 'vp-dim-label');
    dimLabelV.setAttribute('transform', `rotate(-90, 8, ${oy + ((maxY - minY) / 2) * SCALE})`);
    dimLabelV.textContent = `${(maxY - minY).toFixed(1)} mm`;
    svg.appendChild(dimLabelV);

    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = 'Outline';
    section.appendChild(heading);
    section.appendChild(svg);
    return section;
}


// ── Component list ────────────────────────────────────────────

function buildComponentList(components = []) {
    const section = document.createElement('div');
    section.className = 'vp-section';

    const heading = document.createElement('h4');
    heading.textContent = `Components (${components.length})`;
    section.appendChild(heading);

    if (components.length === 0) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'No components';
        section.appendChild(p);
        return section;
    }

    const table = document.createElement('table');
    table.className = 'vp-table';
    table.innerHTML = `
        <thead><tr><th>Instance</th><th>Catalog ID</th><th>Mount</th></tr></thead>
        <tbody>
            ${components.map(c => `
                <tr>
                    <td class="vp-mono">${esc(c.instance_id)}</td>
                    <td>${esc(c.catalog_id)}</td>
                    <td>${esc(c.mounting_style || '—')}</td>
                </tr>
            `).join('')}
        </tbody>`;
    section.appendChild(table);
    return section;
}


// ── Net list ──────────────────────────────────────────────────

function buildNetList(nets = []) {
    const section = document.createElement('div');
    section.className = 'vp-section';

    const heading = document.createElement('h4');
    heading.textContent = `Nets (${nets.length})`;
    section.appendChild(heading);

    if (nets.length === 0) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'No nets';
        section.appendChild(p);
        return section;
    }

    const list = document.createElement('div');
    list.className = 'vp-net-list';
    for (const net of nets) {
        const row = document.createElement('div');
        row.className = 'vp-net-row';
        row.innerHTML = `
            <span class="vp-net-id">${esc(net.id)}</span>
            <span class="vp-net-pins">${net.pins.map(p => `<code>${esc(p)}</code>`).join(' · ')}</span>
        `;
        list.appendChild(row);
    }
    section.appendChild(list);
    return section;
}


// ── Helpers ───────────────────────────────────────────────────

function esc(text) {
    const el = document.createElement('span');
    el.textContent = text ?? '';
    return el.innerHTML;
}


// ── Outline path with rounded corners ─────────────────────────

/**
 * Build an SVG path `d` string for the outline polygon.
 * Sharp edges get straight line-to; round edges get an arc that
 * trims into the two adjacent edges by `radius_mm`.
 *
 * curve = ease_in  → starts gentle, ends at full radius  (larger sweep start)
 * curve = ease_out → starts at full radius, tapers off
 * curve = ease_in_out → symmetric (default circular arc)
 */
function buildOutlinePath(verts, edges, ox, oy, scale) {
    const n = verts.length;
    if (n < 3) return '';

    // Convert vertices to screen coords
    const pts = verts.map(v => ({ x: ox + v[0] * scale, y: oy + v[1] * scale }));

    // Pre-compute edge info: which vertex has a round corner and its radius in px
    const corners = [];
    for (let i = 0; i < n; i++) {
        const edge = edges[i] ?? { style: 'sharp' };
        const isRound = edge.style === 'round';
        const rPx = isRound ? (edge.radius_mm ?? 3) * scale : 0;
        const curve = edge.curve ?? null;
        corners.push({ round: isRound, r: rPx, curve });
    }

    const segments = [];

    for (let i = 0; i < n; i++) {
        const prev = (i - 1 + n) % n;
        const next = (i + 1) % n;
        const P = pts[prev], C = pts[i], N = pts[next];

        if (!corners[i].round || corners[i].r <= 0) {
            // Sharp corner — just go to the vertex
            if (i === 0) segments.push(`M ${C.x} ${C.y}`);
            else segments.push(`L ${C.x} ${C.y}`);
            continue;
        }

        // Rounded corner — compute tangent points
        const r = corners[i].r;

        // Direction vectors from C toward P and N
        const dPx = P.x - C.x, dPy = P.y - C.y;
        const dNx = N.x - C.x, dNy = N.y - C.y;
        const lenP = Math.hypot(dPx, dPy);
        const lenN = Math.hypot(dNx, dNy);

        if (lenP === 0 || lenN === 0) {
            if (i === 0) segments.push(`M ${C.x} ${C.y}`);
            else segments.push(`L ${C.x} ${C.y}`);
            continue;
        }

        // Clamp radius so it doesn't exceed half of either adjacent edge
        const maxR = Math.min(lenP, lenN) * 0.45;
        const rClamped = Math.min(r, maxR);

        // Tangent points: where the arc meets each edge
        const t1x = C.x + (dPx / lenP) * rClamped;
        const t1y = C.y + (dPy / lenP) * rClamped;
        const t2x = C.x + (dNx / lenN) * rClamped;
        const t2y = C.y + (dNy / lenN) * rClamped;

        // Determine sweep direction (CW vs CCW) using cross product
        const cross = dPx * dNy - dPy * dNx;
        const sweepFlag = cross > 0 ? 0 : 1;

        // Line to the first tangent point
        if (i === 0) segments.push(`M ${t1x} ${t1y}`);
        else segments.push(`L ${t1x} ${t1y}`);

        // Arc or quadratic Bézier depending on curve type
        const curveType = corners[i].curve;
        if (!curveType || curveType === 'ease_in_out') {
            // Symmetric circular arc
            segments.push(`A ${rClamped} ${rClamped} 0 0 ${sweepFlag} ${t2x} ${t2y}`);
        } else {
            // Use a quadratic Bézier through the corner vertex for
            // ease_in / ease_out feel; shift the control point to bias the curve
            let cpx, cpy;
            if (curveType === 'ease_in') {
                // Control point biased toward the incoming edge (holds straight longer on entry)
                cpx = (t1x + C.x * 2) / 3;
                cpy = (t1y + C.y * 2) / 3;
            } else {
                // ease_out — biased toward outgoing edge
                cpx = (t2x + C.x * 2) / 3;
                cpy = (t2y + C.y * 2) / 3;
            }
            segments.push(`Q ${cpx} ${cpy} ${t2x} ${t2y}`);
        }
    }

    segments.push('Z');
    return segments.join(' ');
}
