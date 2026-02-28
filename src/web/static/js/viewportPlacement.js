/**
 * Viewport handler for the Placement step.
 *
 * Renders placed components inside the device outline:
 *   - SVG outline (reuses same rendering approach as viewportDesign)
 *   - Placed component footprints (rect / circle) at their world positions
 *   - Component labels
 *   - Dimension labels
 *
 * Data shape (from enriched placement_to_dict):
 * {
 *   components: [{
 *     instance_id, catalog_id, x_mm, y_mm, rotation_deg,
 *     body: { shape, width_mm, length_mm, diameter_mm }
 *   }],
 *   outline: [{ x, y, ease_in?, ease_out? }],
 *   nets:    [{ id, pins }]
 * }
 */

import { registerHandler } from './viewport.js';
import { drawComponentIcon } from './componentRenderer.js';

// ── Register ──────────────────────────────────────────────────

registerHandler('placement', {
    label: 'Placement Preview',
    placeholder: 'Run the placer to see component layout',

    render(el, data) {
        el.innerHTML = '';
        el.appendChild(buildPreview(data));
    },

    clear(el) {
        el.innerHTML = '<p class="viewport-empty">Run the placer to see component layout</p>';
    },
});


// ── Constants ─────────────────────────────────────────────────

const SCALE = 4;      // mm → px
const PAD   = 40;     // px padding around the SVG content
const NS    = 'http://www.w3.org/2000/svg';


// ── Preview builder ───────────────────────────────────────────

function buildPreview(data) {
    const wrap = document.createElement('div');
    wrap.className = 'vp-placement';

    wrap.appendChild(buildPlacementSVG(data));
    wrap.appendChild(buildComponentTable(data.components));
    wrap.appendChild(buildNetList(data.nets));

    return wrap;
}


// ── Placement SVG ─────────────────────────────────────────────

function buildPlacementSVG(data) {
    const { outline, components = [] } = data;

    const { verts, corners } = normaliseOutline(outline);
    if (verts.length < 3) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'Outline has fewer than 3 vertices';
        return p;
    }

    // Bounding box (in mm)
    const xs = verts.map(v => v[0]);
    const ys = verts.map(v => v[1]);
    const [minX, maxX] = [Math.min(...xs), Math.max(...xs)];
    const [minY, maxY] = [Math.min(...ys), Math.max(...ys)];

    const w = (maxX - minX) * SCALE + PAD * 2;
    const h = (maxY - minY) * SCALE + PAD * 2;
    const ox = PAD - minX * SCALE;
    const oy = PAD - minY * SCALE;    // Screen convention: no Y flip

    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('class', 'vp-outline-svg');

    // ── Grid pattern ──
    const gridSize = 10 * SCALE;
    const defs = document.createElementNS(NS, 'defs');
    const gridPat = document.createElementNS(NS, 'pattern');
    gridPat.id = 'vp-placement-grid';
    gridPat.setAttribute('width', gridSize);
    gridPat.setAttribute('height', gridSize);
    gridPat.setAttribute('patternUnits', 'userSpaceOnUse');
    const gridLine = document.createElementNS(NS, 'path');
    gridLine.setAttribute('d', `M ${gridSize} 0 L 0 0 0 ${gridSize}`);
    gridLine.setAttribute('fill', 'none');
    gridLine.setAttribute('stroke', 'rgba(255,255,255,0.04)');
    gridLine.setAttribute('stroke-width', '1');
    gridPat.appendChild(gridLine);
    defs.appendChild(gridPat);
    svg.appendChild(defs);

    const gridRect = document.createElementNS(NS, 'rect');
    gridRect.setAttribute('width', '100%');
    gridRect.setAttribute('height', '100%');
    gridRect.setAttribute('fill', 'url(#vp-placement-grid)');
    svg.appendChild(gridRect);

    // ── Outline path ──
    const pathD = buildOutlinePath(verts, corners, ox, oy, SCALE);
    const pathEl = document.createElementNS(NS, 'path');
    pathEl.setAttribute('d', pathD);
    pathEl.setAttribute('class', 'vp-outline-path');
    svg.appendChild(pathEl);

    // ── Placed components ──
    const COMP_COLORS = [
        '#58a6ff', '#3fb950', '#d29922', '#f778ba', '#bc8cff',
        '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#a5d6ff',
    ];
    components.forEach((comp, idx) => {
        const color = COMP_COLORS[idx % COMP_COLORS.length];
        drawComponentIcon(svg, comp, ox, oy, SCALE, {
            color,
            bodyOpacity: 0.2,
            showPins: !!(comp.pins && comp.pins.length),
        });
    });

    // ── Dimension labels ──
    const dimH = document.createElementNS(NS, 'text');
    dimH.setAttribute('x', ox + ((maxX - minX) / 2) * SCALE);
    dimH.setAttribute('y', h - 6);
    dimH.setAttribute('class', 'vp-dim-label');
    dimH.textContent = `${(maxX - minX).toFixed(1)} mm`;
    svg.appendChild(dimH);

    const dimV = document.createElementNS(NS, 'text');
    dimV.setAttribute('x', 8);
    dimV.setAttribute('y', oy + ((maxY + minY) / 2) * SCALE);
    dimV.setAttribute('class', 'vp-dim-label');
    dimV.setAttribute('transform', `rotate(-90, 8, ${oy + ((maxY + minY) / 2) * SCALE})`);
    dimV.textContent = `${(maxY - minY).toFixed(1)} mm`;
    svg.appendChild(dimV);

    // ── Wrap ──
    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = 'Component Layout';
    section.appendChild(heading);
    section.appendChild(svg);
    return section;
}


// ── Draw a placed component ───────────────────────────────────

function drawComponent(svg, comp, ox, oy, color) {
    const body = comp.body || {};
    const cx = ox + comp.x_mm * SCALE;
    const cy = oy + comp.y_mm * SCALE;
    const rot = comp.rotation_deg || 0;

    if (body.shape === 'circle') {
        const r = ((body.diameter_mm || 5) / 2) * SCALE;
        const circle = document.createElementNS(NS, 'circle');
        circle.setAttribute('cx', cx);
        circle.setAttribute('cy', cy);
        circle.setAttribute('r', r);
        circle.setAttribute('fill', color);
        circle.setAttribute('fill-opacity', '0.2');
        circle.setAttribute('stroke', color);
        circle.setAttribute('stroke-width', '1.5');
        circle.setAttribute('class', 'vp-placed-body');
        svg.appendChild(circle);
    } else {
        // Rectangle body
        let bw = (body.width_mm || 4) * SCALE;
        let bh = (body.length_mm || 4) * SCALE;

        // Swap dimensions for 90/270° rotation
        if (rot === 90 || rot === 270) {
            [bw, bh] = [bh, bw];
        }

        const rect = document.createElementNS(NS, 'rect');
        rect.setAttribute('x', cx - bw / 2);
        rect.setAttribute('y', cy - bh / 2);
        rect.setAttribute('width', bw);
        rect.setAttribute('height', bh);
        rect.setAttribute('rx', '2');
        rect.setAttribute('fill', color);
        rect.setAttribute('fill-opacity', '0.15');
        rect.setAttribute('stroke', color);
        rect.setAttribute('stroke-width', '1.5');
        rect.setAttribute('class', 'vp-placed-body');
        svg.appendChild(rect);

        // Pin-1 indicator (small dot at top-left of body)
        const p1x = cx - bw / 2 + 3;
        const p1y = cy - bh / 2 + 3;
        const pin1 = document.createElementNS(NS, 'circle');
        pin1.setAttribute('cx', p1x);
        pin1.setAttribute('cy', p1y);
        pin1.setAttribute('r', '2');
        pin1.setAttribute('fill', color);
        pin1.setAttribute('opacity', '0.6');
        svg.appendChild(pin1);
    }

    // Center dot
    const dot = document.createElementNS(NS, 'circle');
    dot.setAttribute('cx', cx);
    dot.setAttribute('cy', cy);
    dot.setAttribute('r', '2.5');
    dot.setAttribute('fill', color);
    dot.setAttribute('opacity', '0.9');
    svg.appendChild(dot);

    // Label
    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', cx);
    label.setAttribute('y', cy - ((body.shape === 'circle'
        ? (body.diameter_mm || 5) / 2
        : (rot === 90 || rot === 270
            ? (body.width_mm || 4) / 2
            : (body.length_mm || 4) / 2)
    ) * SCALE) - 5);
    label.setAttribute('class', 'vp-placed-label');
    label.setAttribute('fill', color);
    label.textContent = comp.instance_id;
    svg.appendChild(label);
}


// ── Component table ───────────────────────────────────────────

function buildComponentTable(components = []) {
    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = `Placed Components (${components.length})`;
    section.appendChild(heading);

    if (components.length === 0) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'No components placed';
        section.appendChild(p);
        return section;
    }

    const table = document.createElement('table');
    table.className = 'vp-table';
    table.innerHTML = `
        <thead><tr>
            <th>Instance</th>
            <th>Catalog ID</th>
            <th>Position</th>
            <th>Rotation</th>
        </tr></thead>
        <tbody>
            ${components.map(c => `
                <tr>
                    <td class="vp-mono">${esc(c.instance_id)}</td>
                    <td>${esc(c.catalog_id)}</td>
                    <td class="vp-mono">(${c.x_mm.toFixed(1)}, ${c.y_mm.toFixed(1)})</td>
                    <td class="vp-mono">${c.rotation_deg}°</td>
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

    if (nets.length === 0) return section;

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


// ── Outline helpers (same logic as viewportDesign) ────────────

function normaliseOutline(outline) {
    if (!outline || !Array.isArray(outline)) return { verts: [], corners: [] };
    const verts = outline.map(p => [p.x, p.y]);
    const corners = outline.map(p => {
        let ein = p.ease_in ?? null;
        let eout = p.ease_out ?? null;
        if (ein != null && eout == null) eout = ein;
        if (eout != null && ein == null) ein = eout;
        return { ease_in: ein ?? 0, ease_out: eout ?? 0 };
    });
    return { verts, corners };
}

function buildOutlinePath(verts, edges, ox, oy, scale) {
    const n = verts.length;
    if (n < 3) return '';

    const pts = verts.map(v => ({ x: ox + v[0] * scale, y: oy + v[1] * scale }));

    const cornerInfo = [];
    for (let i = 0; i < n; i++) {
        const edge = edges[i] ?? { ease_in: 0, ease_out: 0 };
        const eIn  = (edge.ease_in  ?? 0) * scale;
        const eOut = (edge.ease_out ?? 0) * scale;
        cornerInfo.push({ round: eIn > 0 || eOut > 0, eIn, eOut });
    }

    const segments = [];
    for (let i = 0; i < n; i++) {
        const prev = (i - 1 + n) % n;
        const next = (i + 1) % n;
        const P = pts[prev], C = pts[i], N = pts[next];

        if (!cornerInfo[i].round) {
            segments.push(i === 0 ? `M ${C.x} ${C.y}` : `L ${C.x} ${C.y}`);
            continue;
        }

        let { eIn, eOut } = cornerInfo[i];
        const dPx = P.x - C.x, dPy = P.y - C.y;
        const dNx = N.x - C.x, dNy = N.y - C.y;
        const lenP = Math.hypot(dPx, dPy);
        const lenN = Math.hypot(dNx, dNy);

        if (lenP === 0 || lenN === 0) {
            segments.push(i === 0 ? `M ${C.x} ${C.y}` : `L ${C.x} ${C.y}`);
            continue;
        }

        eIn  = Math.min(eIn,  lenP * 0.45);
        eOut = Math.min(eOut, lenN * 0.45);

        const t1x = C.x + (dPx / lenP) * eIn;
        const t1y = C.y + (dPy / lenP) * eIn;
        const t2x = C.x + (dNx / lenN) * eOut;
        const t2y = C.y + (dNy / lenN) * eOut;

        segments.push(i === 0 ? `M ${t1x} ${t1y}` : `L ${t1x} ${t1y}`);
        segments.push(`Q ${C.x} ${C.y} ${t2x} ${t2y}`);
    }

    segments.push('Z');
    return segments.join(' ');
}


// ── Helpers ───────────────────────────────────────────────────

function esc(text) {
    const el = document.createElement('span');
    el.textContent = text ?? '';
    return el.innerHTML;
}
