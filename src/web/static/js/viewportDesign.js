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
 *   outline:    [{ x, y, ease_in?, ease_out? }, ...]
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

    // Normalise outline to { verts: [[x,y],...], corners: [{ease_in, ease_out},...] }
    const { verts, corners } = normaliseOutline(outline);

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
    const pathD = buildOutlinePath(verts, corners, ox, oy, SCALE);
    const pathEl = document.createElementNS(NS, 'path');
    pathEl.setAttribute('d', pathD);
    pathEl.setAttribute('class', 'vp-outline-path');
    svg.appendChild(pathEl);

    // UI placements
    for (const up of ui_placements) {
        if (up.edge_index != null) {
            // Side-mount component — render on the wall edge
            drawSideMountMarker(svg, NS, up, { vertices: verts }, ox, oy);
        } else {
            // Interior component — circle marker
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


// ── Side-mount component rendering ────────────────────────────

/**
 * Draw a side-mount component marker on the specified outline edge.
 * The marker is a small diamond/arrow shape sitting on the wall to
 * indicate the component protrudes through.
 */
function drawSideMountMarker(svg, NS, up, outline, ox, oy) {
    const verts = outline.vertices;
    const n = verts.length;
    const i = up.edge_index;

    // Edge endpoints
    const v0 = verts[i];
    const v1 = verts[(i + 1) % n];

    // Project x/y onto the edge to find position along it
    const ex = v1[0] - v0[0], ey = v1[1] - v0[1];
    const edgeLen = Math.hypot(ex, ey);
    if (edgeLen === 0) return;

    // Normalised edge direction
    const dx = ex / edgeLen, dy = ey / edgeLen;

    // Vector from v0 to placement point
    const px = up.x_mm - v0[0], py = up.y_mm - v0[1];

    // Project onto edge (clamp to edge bounds)
    let t = (px * dx + py * dy) / edgeLen;
    t = Math.max(0.02, Math.min(0.98, t));

    // Position on the edge
    const cx = ox + (v0[0] + t * ex) * SCALE;
    const cy = oy + (v0[1] + t * ey) * SCALE;

    // Outward normal (pointing inside the polygon for CW winding)
    const nx = -dy, ny = dx;

    // Draw a small triangle/arrow pointing inward from the wall
    const arrowLen = 8;   // length of arrow in px
    const arrowW   = 5;   // half-width of arrow base in px

    // Tip of arrow (pointing inward)
    const tipX = cx + nx * arrowLen * SCALE / 4;
    const tipY = cy + ny * arrowLen * SCALE / 4;

    // Base corners (on the wall)
    const b1x = cx + dx * arrowW;
    const b1y = cy + dy * arrowW;
    const b2x = cx - dx * arrowW;
    const b2y = cy - dy * arrowW;

    const arrow = document.createElementNS(NS, 'polygon');
    arrow.setAttribute('points', `${b1x},${b1y} ${tipX},${tipY} ${b2x},${b2y}`);
    arrow.setAttribute('class', 'vp-side-marker');

    // Small circle on the wall edge itself
    const dot = document.createElementNS(NS, 'circle');
    dot.setAttribute('cx', cx);
    dot.setAttribute('cy', cy);
    dot.setAttribute('r', '3');
    dot.setAttribute('class', 'vp-side-dot');

    // Label — offset inward from the wall
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', cx + nx * 16);
    label.setAttribute('y', cy + ny * 16);
    label.setAttribute('class', 'vp-ui-label');
    label.textContent = up.instance_id;

    svg.appendChild(arrow);
    svg.appendChild(dot);
    svg.appendChild(label);
}


// ── Outline normalisation ─────────────────────────────────────

/**
 * Normalise outline to a consistent internal shape:
 *   { verts: [[x,y],...], corners: [{ease_in, ease_out},...] }
 *
 * Input: [{ x, y, ease_in?, ease_out? }, ...]
 */
function normaliseOutline(outline) {
    if (!outline || !Array.isArray(outline)) return { verts: [], corners: [] };

    const verts = outline.map(p => [p.x, p.y]);
    const corners = outline.map(p => {
        let ein = p.ease_in ?? null;
        let eout = p.ease_out ?? null;
        // If only one side given, mirror to the other (symmetric)
        if (ein != null && eout == null) eout = ein;
        if (eout != null && ein == null) ein = eout;
        return { ease_in: ein ?? 0, ease_out: eout ?? 0 };
    });
    return { verts, corners };
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
 * Sharp corners get straight line-to; eased corners get a quadratic
 * Bézier with the vertex as the control point and tangent points
 * at ease_in / ease_out distances along the adjacent edges.
 *
 * When ease_in == ease_out the curve is symmetric (close to a circular arc).
 * When they differ the curve is asymmetric / oblong.
 */
function buildOutlinePath(verts, edges, ox, oy, scale) {
    const n = verts.length;
    if (n < 3) return '';

    // Convert vertices to screen coords
    const pts = verts.map(v => ({ x: ox + v[0] * scale, y: oy + v[1] * scale }));

    // Pre-compute ease info per vertex in px
    const corners = [];
    for (let i = 0; i < n; i++) {
        const edge = edges[i] ?? { ease_in: 0, ease_out: 0 };
        const eIn  = (edge.ease_in  ?? 0) * scale;
        const eOut = (edge.ease_out ?? 0) * scale;
        corners.push({ round: eIn > 0 || eOut > 0, eIn, eOut });
    }

    const segments = [];

    for (let i = 0; i < n; i++) {
        const prev = (i - 1 + n) % n;
        const next = (i + 1) % n;
        const P = pts[prev], C = pts[i], N = pts[next];

        if (!corners[i].round) {
            // Sharp corner — just go to the vertex
            if (i === 0) segments.push(`M ${C.x} ${C.y}`);
            else segments.push(`L ${C.x} ${C.y}`);
            continue;
        }

        // Rounded corner — tangent points at ease distances from C
        let { eIn, eOut } = corners[i];

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

        // Clamp so we don't exceed ~45% of either adjacent edge
        eIn  = Math.min(eIn,  lenP * 0.45);
        eOut = Math.min(eOut, lenN * 0.45);

        // Tangent points: t1 on incoming edge, t2 on outgoing edge
        const t1x = C.x + (dPx / lenP) * eIn;
        const t1y = C.y + (dPy / lenP) * eIn;
        const t2x = C.x + (dNx / lenN) * eOut;
        const t2y = C.y + (dNy / lenN) * eOut;

        // Line to the first tangent point
        if (i === 0) segments.push(`M ${t1x} ${t1y}`);
        else segments.push(`L ${t1x} ${t1y}`);

        // Quadratic Bézier: control point = vertex, end = second tangent
        segments.push(`Q ${C.x} ${C.y} ${t2x} ${t2y}`);
    }

    segments.push('Z');
    return segments.join(' ');
}
