/**
 * Viewport handler for the Routing step.
 *
 * Renders routed traces overlaid on the component layout.
 * Reuses the same outline + component rendering as viewportPlacement,
 * adding colored trace polylines on top.
 *
 * Data shape (from routing API):
 * {
 *   traces:          [{ net_id, path: [[x,y], …] }],
 *   pin_assignments: { "mcu_1:gpio": "mcu_1:PD2", … },
 *   failed_nets:     ["NET_X", …],
 *   outline:         [{ x, y, ease_in?, ease_out? }],
 *   components:      [{ instance_id, catalog_id, x_mm, y_mm, rotation_deg, body }]
 * }
 */

import { registerHandler } from './viewport.js';

// ── Register ──────────────────────────────────────────────────

registerHandler('routing', {
    label: 'Routing Preview',
    placeholder: 'Run the router to see trace layout',

    render(el, data) {
        el.innerHTML = '';
        el.appendChild(buildPreview(data));
    },

    clear(el) {
        el.innerHTML = '<p class="viewport-empty">Run the router to see trace layout</p>';
    },
});


// ── Constants ─────────────────────────────────────────────────

const SCALE = 4;      // mm → px
const PAD   = 40;     // px padding around the SVG content
const NS    = 'http://www.w3.org/2000/svg';

// Colours for component bodies (dimmed in routing view)
const COMP_COLORS = [
    '#58a6ff', '#3fb950', '#d29922', '#f778ba', '#bc8cff',
    '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#a5d6ff',
];

// Distinct trace colours (brighter, higher contrast)
const TRACE_COLORS = [
    '#ff6b6b',   // red
    '#51cf66',   // green
    '#339af0',   // blue
    '#fcc419',   // yellow
    '#cc5de8',   // purple
    '#22b8cf',   // cyan
    '#ff922b',   // orange
    '#f06595',   // pink
    '#20c997',   // teal
    '#845ef7',   // violet
];


// ── Preview builder ───────────────────────────────────────────

function buildPreview(data) {
    const wrap = document.createElement('div');
    wrap.className = 'vp-placement';   // reuse placement layout styles

    wrap.appendChild(buildRoutingSVG(data));
    wrap.appendChild(buildTraceTable(data.traces));
    if (data.failed_nets && data.failed_nets.length > 0) {
        wrap.appendChild(buildFailedNets(data.failed_nets));
    }

    return wrap;
}


// ── Routing SVG ───────────────────────────────────────────────

function buildRoutingSVG(data) {
    const { outline, components = [], traces = [], failed_nets = [] } = data;

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
    const oy = PAD - minY * SCALE;

    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('class', 'vp-outline-svg');

    // ── Grid pattern ──
    const gridSize = 10 * SCALE;
    const defs = document.createElementNS(NS, 'defs');
    const gridPat = document.createElementNS(NS, 'pattern');
    gridPat.id = 'vp-routing-grid';
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
    gridRect.setAttribute('fill', 'url(#vp-routing-grid)');
    svg.appendChild(gridRect);

    // ── Outline path ──
    const pathD = buildOutlinePath(verts, corners, ox, oy, SCALE);
    const pathEl = document.createElementNS(NS, 'path');
    pathEl.setAttribute('d', pathD);
    pathEl.setAttribute('class', 'vp-outline-path');
    svg.appendChild(pathEl);

    // ── Components (dimmed) ──
    components.forEach((comp, idx) => {
        const color = COMP_COLORS[idx % COMP_COLORS.length];
        drawComponent(svg, comp, ox, oy, color, 0.10);  // lower opacity
    });

    // ── Traces ──
    const netColorMap = {};
    let colorIdx = 0;
    traces.forEach(trace => {
        if (!netColorMap[trace.net_id]) {
            netColorMap[trace.net_id] = TRACE_COLORS[colorIdx % TRACE_COLORS.length];
            colorIdx++;
        }
        drawTrace(svg, trace, ox, oy, netColorMap[trace.net_id]);
    });

    // ── Trace legend ──
    if (traces.length > 0) {
        const legendY = h - 8;
        let legendX = ox;
        for (const [netId, color] of Object.entries(netColorMap)) {
            const dot = document.createElementNS(NS, 'circle');
            dot.setAttribute('cx', legendX);
            dot.setAttribute('cy', legendY);
            dot.setAttribute('r', '4');
            dot.setAttribute('fill', color);
            svg.appendChild(dot);

            const label = document.createElementNS(NS, 'text');
            label.setAttribute('x', legendX + 8);
            label.setAttribute('y', legendY + 3);
            label.setAttribute('class', 'vp-dim-label');
            label.setAttribute('fill', color);
            label.setAttribute('text-anchor', 'start');
            label.setAttribute('font-size', '10');
            label.textContent = netId;
            svg.appendChild(label);

            legendX += netId.length * 7 + 22;
        }
    }

    // ── Dimension labels ──
    const dimH = document.createElementNS(NS, 'text');
    dimH.setAttribute('x', ox + ((maxX - minX) / 2) * SCALE);
    dimH.setAttribute('y', h - 20);
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

    // ── Status badge ──
    const ok = failed_nets.length === 0;
    const badge = document.createElementNS(NS, 'text');
    badge.setAttribute('x', w - 10);
    badge.setAttribute('y', 16);
    badge.setAttribute('text-anchor', 'end');
    badge.setAttribute('font-size', '12');
    badge.setAttribute('fill', ok ? '#3fb950' : '#f85149');
    badge.textContent = ok ? `✓ All ${traces.length} nets routed` : `⚠ ${failed_nets.length} net${failed_nets.length > 1 ? 's' : ''} failed`;
    svg.appendChild(badge);

    // ── Wrap ──
    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = 'Trace Layout';
    section.appendChild(heading);
    section.appendChild(svg);
    return section;
}


// ── Draw a trace polyline ─────────────────────────────────────

function drawTrace(svg, trace, ox, oy, color) {
    const path = trace.path;
    if (!path || path.length < 2) return;

    // Main trace line
    const points = path.map(p => `${ox + p[0] * SCALE},${oy + p[1] * SCALE}`).join(' ');
    const polyline = document.createElementNS(NS, 'polyline');
    polyline.setAttribute('points', points);
    polyline.setAttribute('fill', 'none');
    polyline.setAttribute('stroke', color);
    polyline.setAttribute('stroke-width', '2.5');
    polyline.setAttribute('stroke-linecap', 'round');
    polyline.setAttribute('stroke-linejoin', 'round');
    polyline.setAttribute('opacity', '0.85');
    svg.appendChild(polyline);

    // Via dots at each waypoint (intermediate points)
    for (let i = 1; i < path.length - 1; i++) {
        const dot = document.createElementNS(NS, 'circle');
        dot.setAttribute('cx', ox + path[i][0] * SCALE);
        dot.setAttribute('cy', oy + path[i][1] * SCALE);
        dot.setAttribute('r', '2');
        dot.setAttribute('fill', color);
        dot.setAttribute('opacity', '0.7');
        svg.appendChild(dot);
    }

    // Endpoint pads (start and end)
    for (const idx of [0, path.length - 1]) {
        const pad = document.createElementNS(NS, 'circle');
        pad.setAttribute('cx', ox + path[idx][0] * SCALE);
        pad.setAttribute('cy', oy + path[idx][1] * SCALE);
        pad.setAttribute('r', '3.5');
        pad.setAttribute('fill', color);
        pad.setAttribute('stroke', '#0d1117');
        pad.setAttribute('stroke-width', '1');
        svg.appendChild(pad);
    }
}


// ── Draw a placed component (dimmed) ──────────────────────────

function drawComponent(svg, comp, ox, oy, color, opacity = 0.15) {
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
        circle.setAttribute('fill-opacity', String(opacity));
        circle.setAttribute('stroke', color);
        circle.setAttribute('stroke-width', '1');
        circle.setAttribute('stroke-opacity', '0.3');
        svg.appendChild(circle);
    } else {
        let bw = (body.width_mm || 4) * SCALE;
        let bh = (body.length_mm || 4) * SCALE;
        if (rot === 90 || rot === 270) [bw, bh] = [bh, bw];

        const rect = document.createElementNS(NS, 'rect');
        rect.setAttribute('x', cx - bw / 2);
        rect.setAttribute('y', cy - bh / 2);
        rect.setAttribute('width', bw);
        rect.setAttribute('height', bh);
        rect.setAttribute('rx', '2');
        rect.setAttribute('fill', color);
        rect.setAttribute('fill-opacity', String(opacity));
        rect.setAttribute('stroke', color);
        rect.setAttribute('stroke-width', '1');
        rect.setAttribute('stroke-opacity', '0.3');
        svg.appendChild(rect);
    }

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
    label.setAttribute('opacity', '0.5');
    label.textContent = comp.instance_id;
    svg.appendChild(label);
}


// ── Trace table ───────────────────────────────────────────────

function buildTraceTable(traces = []) {
    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = `Traces (${traces.length})`;
    section.appendChild(heading);

    if (traces.length === 0) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'No traces routed';
        section.appendChild(p);
        return section;
    }

    const table = document.createElement('table');
    table.className = 'vp-table';
    table.innerHTML = `
        <thead><tr>
            <th>Net</th>
            <th>Waypoints</th>
            <th>Length (mm)</th>
        </tr></thead>
        <tbody>
            ${traces.map(t => {
                const len = traceLength(t.path);
                return `
                <tr>
                    <td class="vp-mono">${esc(t.net_id)}</td>
                    <td class="vp-mono">${t.path.length}</td>
                    <td class="vp-mono">${len.toFixed(1)}</td>
                </tr>`;
            }).join('')}
        </tbody>`;
    section.appendChild(table);
    return section;
}


// ── Failed nets ───────────────────────────────────────────────

function buildFailedNets(failedNets) {
    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.style.color = 'var(--error, #f85149)';
    heading.textContent = `Failed Nets (${failedNets.length})`;
    section.appendChild(heading);

    const list = document.createElement('div');
    list.className = 'vp-net-list';
    for (const net of failedNets) {
        const row = document.createElement('div');
        row.className = 'vp-net-row';
        row.innerHTML = `<span class="vp-net-id" style="color:var(--error, #f85149)">${esc(net)}</span>`;
        list.appendChild(row);
    }
    section.appendChild(list);
    return section;
}


// ── Outline helpers (same as viewportPlacement) ───────────────

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

function traceLength(path) {
    let len = 0;
    for (let i = 1; i < path.length; i++) {
        const dx = path[i][0] - path[i - 1][0];
        const dy = path[i][1] - path[i - 1][1];
        len += Math.abs(dx) + Math.abs(dy);
    }
    return len;
}

function esc(text) {
    const el = document.createElement('span');
    el.textContent = text ?? '';
    return el.innerHTML;
}
