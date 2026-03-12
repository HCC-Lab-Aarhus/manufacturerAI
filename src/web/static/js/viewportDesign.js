/**
 * Viewport handler for the Design step.
 *
 * Renders a visual preview of the DesignSpec:
 *   - Interactive SVG outline with draggable UI placement markers
 *   - Component editor panel (mounting style, add/remove)
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

import { registerHandler, cacheData, setData as setViewportData } from './viewport.js';
import { drawComponentIcon } from './componentRenderer.js';
import { normaliseOutline, buildOutlinePath, snapToEdge, esc, SCALE, PAD, NS, attachViewToggle } from './viewportUtils.js';
import { state, API } from './state.js';

let _currentDesign = null;
let _catalogPromise = null;

async function _ensureCatalog() {
    if (state.catalog) return;
    if (!_catalogPromise) {
        const url = state.session
            ? `${API}/api/session/catalog?session=${encodeURIComponent(state.session)}`
            : `${API}/api/catalog`;
        _catalogPromise = fetch(url).then(r => r.json()).then(data => {
            state.catalog = data;
            _catalogPromise = null;
        }).catch(() => { _catalogPromise = null; });
    }
    await _catalogPromise;
}

// ── Toggle controller ───────────────────────────────────────────

const _toggle = attachViewToggle(
    'design',
    async (el, design) => { el.innerHTML = ''; _currentDesign = design; el.appendChild(await buildPreview(design)); },
    async (host) => {
        const { create3DScene } = await import('./viewport3d.js');
        const scene = create3DScene(host);
        let panel = null;
        return {
            update(data, opts) {
                _currentDesign = data;
                scene.update(data, opts);
                if (!panel) {
                    panel = _mountEdgePanel(host, data, scene);
                } else {
                    panel.syncData(data);
                }
            },
            resize(w, h) { scene.resize(w, h); },
            destroy() {
                if (panel) { panel.destroy(); panel = null; }
                scene.destroy();
            },
        };
    },
);

// ── Register ────────────────────────────────────────────────

registerHandler('design', {
    label: 'Design Preview',
    placeholder: 'Submit a design prompt to see the preview',

    render(el, design) { _toggle.render(el, design); },

    clear(el) {
        _toggle.clear(el);
        el.innerHTML = '<p class="viewport-empty">Submit a design prompt to see the preview</p>';
    },

    unmount()        { _toggle.unmount(); },
    onResize(el,w,h) { _toggle.resize(w, h); },
});


// ── Design persistence helpers ──────────────────────────────────

function _stripEnrichment(design) {
    const d = JSON.parse(JSON.stringify(design));
    delete d.height_grid;
    delete d.bottom_height_grid;
    for (const c of (d.components || [])) {
        delete c.body;
        delete c.pins;
        delete c.ui_placement;
        delete c.cap_diameter_mm;
        delete c.cap_clearance_mm;
        delete c.pin_positions;
    }
    for (const up of (d.ui_placements || [])) {
        delete up.z_at_position;
        delete up.surface_normal;
    }
    return d;
}

let _saveSeq = 0;

async function _persistDesign(design) {
    const sid = state.session;
    if (!sid) return;
    const seq = ++_saveSeq;
    const stripped = _stripEnrichment(design);
    try {
        const res = await fetch(
            `${API}/api/session/design?session=${encodeURIComponent(sid)}`,
            { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(stripped) },
        );
        if (res.ok && seq === _saveSeq) {
            const saved = await res.json();
            _currentDesign = saved;
            cacheData('design', saved);
        }
    } catch { /* non-fatal */ }
}

async function _persistConversationSubmitDesign(design) {
    const sid = state.session;
    if (!sid) return;
    const stripped = _stripEnrichment(design);
    try {
        const res = await fetch(
            `${API}/api/session/conversation/submit-design?session=${encodeURIComponent(sid)}`,
            { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ design: stripped }) },
        );
        if (res.ok) _showDesignEditBubble();
    } catch { /* non-fatal */ }
}

let _designEditBubble = null;

function _showDesignEditBubble() {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    if (_designEditBubble && container.contains(_designEditBubble)) {
        _designEditBubble.textContent = '✏️ Design updated via interactive designer';
        return;
    }
    const div = document.createElement('div');
    div.className = 'chat-bubble user';
    div.textContent = '✏️ Design updated via interactive designer';
    container.appendChild(div);
    _designEditBubble = div;
    container.scrollTop = container.scrollHeight;
}

let _validateTimer = null;
let _validateSeq = 0;

async function _validatePlacement(instanceId, xMm, yMm, edgeIndex) {
    const sid = state.session;
    if (!sid) return { valid: true, errors: [] };
    const seq = ++_validateSeq;
    try {
        const body = { instance_id: instanceId, x_mm: xMm, y_mm: yMm };
        if (edgeIndex != null) body.edge_index = edgeIndex;
        const res = await fetch(
            `${API}/api/session/design/validate-ui-placement?session=${encodeURIComponent(sid)}`,
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
        );
        if (res.ok && seq === _validateSeq) return await res.json();
    } catch { /* non-fatal */ }
    return { valid: true, errors: [] };
}


// ── Preview builder ───────────────────────────────────────────

async function buildPreview(design) {
    const wrap = document.createElement('div');
    wrap.className = 'vp-design';

    await _ensureCatalog();

    wrap.appendChild(buildOutlineSVG(design));
    wrap.appendChild(buildComponentPanel(design));
    wrap.appendChild(buildNetList(design.nets));

    return wrap;
}


// ── Outline SVG (interactive) ─────────────────────────────────

function buildOutlineSVG(design) {
    const { outline, ui_placements = [] } = design;

    const { verts, corners } = normaliseOutline(outline);

    if (verts.length < 3) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'Outline has fewer than 3 vertices';
        return p;
    }

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
    svg.setAttribute('class', 'vp-outline-svg vp-outline-svg-interactive');

    // Grid
    const gridSize = 10 * SCALE;
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

    const pathD = buildOutlinePath(verts, corners, ox, oy, SCALE);
    const pathEl = document.createElementNS(NS, 'path');
    pathEl.setAttribute('d', pathD);
    pathEl.setAttribute('class', 'vp-outline-path');
    svg.appendChild(pathEl);

    const compMap = {};
    for (const c of (design.components || [])) {
        compMap[c.instance_id] = c;
    }

    const UI_COLORS = [
        '#58a6ff', '#3fb950', '#d29922', '#f778ba', '#bc8cff',
        '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#a5d6ff',
    ];

    // Status bar below SVG
    const statusBar = document.createElement('div');
    statusBar.className = 'vp-drag-status';

    ui_placements.forEach((up, idx) => {
        const comp = compMap[up.instance_id];
        const color = UI_COLORS[idx % UI_COLORS.length];

        // Create a draggable group for each UI component
        const dragGroup = document.createElementNS(NS, 'g');
        dragGroup.setAttribute('class', 'vp-draggable-comp');
        dragGroup.style.cursor = 'grab';
        dragGroup.dataset.instanceId = up.instance_id;
        dragGroup.dataset.idx = idx;

        if (up.edge_index != null) {
            const snapInfo = snapToEdge(up, verts, normaliseOutline(design.outline).zTops, (design.enclosure?.height_mm ?? 25));
            if (comp && comp.body) {
                const fakeComp = { ...comp, x_mm: snapInfo.x, y_mm: snapInfo.y, rotation_deg: snapInfo.rot };
                drawComponentIcon(dragGroup, fakeComp, ox, oy, SCALE, { color, bodyOpacity: 0.2, showPins: !!(comp.pins) });
            } else {
                _drawSideMountInGroup(dragGroup, up, verts, ox, oy);
            }
        } else {
            if (comp && comp.body) {
                const fakeComp = { ...comp, x_mm: up.x_mm, y_mm: up.y_mm, rotation_deg: 0 };
                drawComponentIcon(dragGroup, fakeComp, ox, oy, SCALE, { color, bodyOpacity: 0.2, showPins: !!(comp.pins) });
            } else {
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
                dragGroup.appendChild(marker);
                dragGroup.appendChild(label);
            }
        }

        // Hit area for drag
        const hitRect = document.createElementNS(NS, 'rect');
        const bbox = { x: 0, y: 0, w: 20, h: 20 };
        if (comp && comp.body) {
            const bw = (comp.body.width_mm || comp.body.diameter_mm || 6) * SCALE;
            const bh = (comp.body.length_mm || comp.body.diameter_mm || 6) * SCALE;
            const snapInfo = up.edge_index != null
                ? snapToEdge(up, verts, normaliseOutline(design.outline).zTops, (design.enclosure?.height_mm ?? 25))
                : { x: up.x_mm, y: up.y_mm };
            bbox.x = ox + snapInfo.x * SCALE - bw / 2 - 4;
            bbox.y = oy + snapInfo.y * SCALE - bh / 2 - 4;
            bbox.w = bw + 8;
            bbox.h = bh + 8;
        } else {
            const snapInfo = up.edge_index != null
                ? snapToEdge(up, verts, normaliseOutline(design.outline).zTops, (design.enclosure?.height_mm ?? 25))
                : { x: up.x_mm, y: up.y_mm };
            bbox.x = ox + snapInfo.x * SCALE - 12;
            bbox.y = oy + snapInfo.y * SCALE - 12;
            bbox.w = 24;
            bbox.h = 24;
        }
        hitRect.setAttribute('x', bbox.x);
        hitRect.setAttribute('y', bbox.y);
        hitRect.setAttribute('width', bbox.w);
        hitRect.setAttribute('height', bbox.h);
        hitRect.setAttribute('fill', 'transparent');
        hitRect.setAttribute('class', 'vp-drag-hit');
        dragGroup.appendChild(hitRect);

        svg.appendChild(dragGroup);

        // Drag logic
        _attachDrag(svg, dragGroup, up, idx, design, ox, oy, verts, compMap, UI_COLORS, statusBar);
    });

    // Dimension labels
    const dimLabel = document.createElementNS(NS, 'text');
    dimLabel.setAttribute('x', ox + ((maxX - minX) / 2) * SCALE);
    dimLabel.setAttribute('y', h - 6);
    dimLabel.setAttribute('class', 'vp-dim-label');
    dimLabel.textContent = `${(maxX - minX).toFixed(1)} mm`;
    svg.appendChild(dimLabel);

    const dimLabelV = document.createElementNS(NS, 'text');
    dimLabelV.setAttribute('x', 8);
    dimLabelV.setAttribute('y', oy + ((maxY + minY) / 2) * SCALE);
    dimLabelV.setAttribute('class', 'vp-dim-label');
    dimLabelV.setAttribute('transform', `rotate(-90, 8, ${oy + ((maxY + minY) / 2) * SCALE})`);
    dimLabelV.textContent = `${(maxY - minY).toFixed(1)} mm`;
    svg.appendChild(dimLabelV);

    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = 'Outline — drag components to reposition';
    section.appendChild(heading);
    section.appendChild(svg);
    section.appendChild(statusBar);
    return section;
}

// ── Nearest-edge detection ────────────────────────────────────

function _nearestEdge(x, y, verts) {
    let best = { edgeIndex: 0, t: 0.5, snapX: x, snapY: y, dist: Infinity };
    const n = verts.length;
    for (let i = 0; i < n; i++) {
        const v0 = verts[i], v1 = verts[(i + 1) % n];
        const ex = v1[0] - v0[0], ey = v1[1] - v0[1];
        const lenSq = ex * ex + ey * ey;
        if (lenSq < 1e-12) continue;
        let t = ((x - v0[0]) * ex + (y - v0[1]) * ey) / lenSq;
        t = Math.max(0, Math.min(1, t));
        const sx = v0[0] + t * ex, sy = v0[1] + t * ey;
        const d = Math.hypot(x - sx, y - sy);
        if (d < best.dist) {
            best = { edgeIndex: i, t, snapX: Math.round(sx * 10) / 10, snapY: Math.round(sy * 10) / 10, dist: d };
        }
    }
    return best;
}

// ── Drag-to-move logic ────────────────────────────────────────

function _attachDrag(svg, group, up, idx, design, ox, oy, verts, compMap, colors, statusBar) {
    let dragging = false;
    let startPt = null;
    let origX = up.x_mm;
    let origY = up.y_mm;
    let lastValid = true;

    function svgPoint(e) {
        const pt = svg.createSVGPoint();
        const ctm = svg.getScreenCTM().inverse();
        pt.x = e.clientX;
        pt.y = e.clientY;
        return pt.matrixTransform(ctm);
    }

    function onPointerDown(e) {
        if (e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        dragging = true;
        lastValid = true;
        startPt = svgPoint(e);
        origX = up.x_mm;
        origY = up.y_mm;
        group.style.cursor = 'grabbing';
        svg.style.cursor = 'grabbing';
        group.classList.add('vp-dragging');
        statusBar.textContent = `Moving ${up.instance_id}…`;
        statusBar.className = 'vp-drag-status';
        svg.setPointerCapture(e.pointerId);
        svg.addEventListener('pointermove', onPointerMove);
        svg.addEventListener('pointerup', onPointerUp);
    }

    const isSideMount = up.edge_index != null;
    let dragEdge = up.edge_index;

    function onPointerMove(e) {
        if (!dragging) return;
        const cur = svgPoint(e);
        const dx = (cur.x - startPt.x) / SCALE;
        const dy = (cur.y - startPt.y) / SCALE;
        let newX = origX + dx;
        let newY = origY + dy;

        if (isSideMount) {
            const snap = _nearestEdge(newX, newY, verts);
            dragEdge = snap.edgeIndex;
            newX = snap.snapX;
            newY = snap.snapY;
            const edgeTransX = (newX - origX) * SCALE;
            const edgeTransY = (newY - origY) * SCALE;
            group.setAttribute('transform', `translate(${edgeTransX}, ${edgeTransY})`);
        } else {
            group.setAttribute('transform', `translate(${(newX - origX) * SCALE}, ${(newY - origY) * SCALE})`);
        }

        // Debounced validation
        clearTimeout(_validateTimer);
        const valX = newX, valY = newY, valEdge = dragEdge;
        _validateTimer = setTimeout(async () => {
            const result = await _validatePlacement(up.instance_id, valX, valY, valEdge);
            lastValid = result.valid;
            const edgeLabel = isSideMount ? ` edge ${valEdge}` : '';
            if (result.valid) {
                group.classList.remove('vp-drag-invalid');
                group.classList.add('vp-drag-valid');
                statusBar.textContent = `${up.instance_id}: (${valX.toFixed(1)}, ${valY.toFixed(1)}) mm${edgeLabel} ✓`;
                statusBar.className = 'vp-drag-status vp-drag-status-valid';
            } else {
                group.classList.remove('vp-drag-valid');
                group.classList.add('vp-drag-invalid');
                statusBar.textContent = `${up.instance_id}: ${result.errors[0] || 'Invalid'}`;
                statusBar.className = 'vp-drag-status vp-drag-status-invalid';
            }
        }, 100);
    }

    async function onPointerUp(e) {
        if (!dragging) return;
        dragging = false;
        svg.releasePointerCapture(e.pointerId);
        svg.removeEventListener('pointermove', onPointerMove);
        svg.removeEventListener('pointerup', onPointerUp);
        group.style.cursor = 'grab';
        svg.style.cursor = '';
        group.classList.remove('vp-dragging', 'vp-drag-invalid', 'vp-drag-valid');
        clearTimeout(_validateTimer);

        const cur = svgPoint(e);
        const dx = (cur.x - startPt.x) / SCALE;
        const dy = (cur.y - startPt.y) / SCALE;
        let newX = Math.round((origX + dx) * 10) / 10;
        let newY = Math.round((origY + dy) * 10) / 10;

        if (isSideMount) {
            const snap = _nearestEdge(newX, newY, verts);
            dragEdge = snap.edgeIndex;
            newX = snap.snapX;
            newY = snap.snapY;
        }

        if (Math.abs(newX - origX) < 0.2 && Math.abs(newY - origY) < 0.2 && dragEdge === up.edge_index) {
            group.setAttribute('transform', '');
            statusBar.textContent = '';
            return;
        }

        // Final validation
        const result = await _validatePlacement(up.instance_id, newX, newY, isSideMount ? dragEdge : up.edge_index);
        if (!result.valid) {
            group.setAttribute('transform', '');
            statusBar.textContent = `Reverted — ${result.errors[0] || 'invalid placement'}`;
            statusBar.className = 'vp-drag-status vp-drag-status-invalid';
            setTimeout(() => { statusBar.textContent = ''; }, 3000);
            return;
        }

        // Commit the move
        up.x_mm = newX;
        up.y_mm = newY;
        if (isSideMount) up.edge_index = dragEdge;
        design.ui_placements[idx] = { ...design.ui_placements[idx], x_mm: newX, y_mm: newY, ...(isSideMount ? { edge_index: dragEdge } : {}) };

        statusBar.textContent = `Saving ${up.instance_id}…`;
        statusBar.className = 'vp-drag-status';

        await _persistDesign(design);
        await _persistConversationSubmitDesign(design);

        // Re-render the full preview with updated positions
        setViewportData('design', _currentDesign || design);
        statusBar.textContent = `${up.instance_id} moved to (${newX.toFixed(1)}, ${newY.toFixed(1)}) ✓`;
        statusBar.className = 'vp-drag-status vp-drag-status-valid';
        setTimeout(() => { statusBar.textContent = ''; }, 3000);
    }

    group.addEventListener('pointerdown', onPointerDown);
}


// ── Component panel (mounting, add/remove) ────────────────────

function _resolveAllowedStyles(comp) {
    const catEntry = _getCatalogEntry(comp.catalog_id);
    if (catEntry?.mounting?.allowed_styles?.length > 1) return catEntry.mounting.allowed_styles;
    return null;
}

function _resolveCurrentStyle(comp) {
    const catEntry = _getCatalogEntry(comp.catalog_id);
    return comp.mounting_style || catEntry?.mounting?.style || '—';
}

function _edgeMidpoint(verts, edgeIndex) {
    const n = verts.length;
    const v0 = verts[edgeIndex % n];
    const v1 = verts[(edgeIndex + 1) % n];
    return {
        x: Math.round(((v0[0] + v1[0]) / 2) * 10) / 10,
        y: Math.round(((v0[1] + v1[1]) / 2) * 10) / 10,
    };
}

function _isUIComponent(comp) {
    if (comp.ui_placement != null) return comp.ui_placement;
    const catEntry = _getCatalogEntry(comp.catalog_id);
    return catEntry?.ui_placement ?? false;
}

function buildComponentPanel(design) {
    const section = document.createElement('div');
    section.className = 'vp-section';

    const components = design.components || [];
    const uiComps = components.filter(c => _isUIComponent(c));
    const internalComps = components.filter(c => !_isUIComponent(c));
    const uiMap = {};
    for (const up of (design.ui_placements || [])) uiMap[up.instance_id] = up;

    // ── UI Components (cards) ──
    const uiHeading = document.createElement('h4');
    uiHeading.textContent = `UI Components (${uiComps.length})`;
    section.appendChild(uiHeading);

    if (uiComps.length === 0) {
        const p = document.createElement('p');
        p.className = 'viewport-empty';
        p.textContent = 'No UI components placed yet';
        section.appendChild(p);
    } else {
        const cardList = document.createElement('div');
        cardList.className = 'vp-comp-cards';

        for (const comp of uiComps) {
            const up = uiMap[comp.instance_id];
            cardList.appendChild(_buildCompCard(comp, up, design));
        }
        section.appendChild(cardList);
    }

    // ── Add component ──
    const addRow = document.createElement('div');
    addRow.className = 'vp-add-row';
    const addSelect = document.createElement('select');
    addSelect.className = 'vp-add-select';
    addSelect.innerHTML = '<option value="">+ Add UI component…</option>';
    const catalog = _getUICatalog();
    for (const c of catalog) {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.name} (${c.id})`;
        addSelect.appendChild(opt);
    }
    addSelect.addEventListener('change', async () => {
        const catId = addSelect.value;
        if (!catId) return;
        addSelect.value = '';
        await _addUIComponent(catId, design);
    });
    addRow.appendChild(addSelect);
    section.appendChild(addRow);

    // ── Internal Components (collapsed) ──
    if (internalComps.length > 0) {
        const details = document.createElement('details');
        details.className = 'vp-internal-details';
        const summary = document.createElement('summary');
        summary.className = 'vp-internal-summary';
        summary.textContent = `Internal Components (${internalComps.length})`;
        details.appendChild(summary);

        const internalList = document.createElement('div');
        internalList.className = 'vp-internal-list';
        for (const comp of internalComps) {
            const row = document.createElement('div');
            row.className = 'vp-internal-row';
            row.innerHTML = `<span class="vp-mono">${esc(comp.instance_id)}</span><span class="vp-internal-cat">${esc(comp.catalog_id)}</span>`;
            internalList.appendChild(row);

            const catEntry = _getCatalogEntry(comp.catalog_id);
            if (catEntry?.configurable && Object.keys(catEntry.configurable).length > 0) {
                const configContainer = document.createElement('div');
                configContainer.className = 'vp-internal-config';
                _appendConfigFields(configContainer, comp, design);
                internalList.appendChild(configContainer);
            }
        }
        details.appendChild(internalList);
        section.appendChild(details);
    }

    return section;
}

function _buildCompCard(comp, up, design) {
    const card = document.createElement('div');
    card.className = 'vp-comp-card';

    // Header row: icon + name + remove
    const header = document.createElement('div');
    header.className = 'vp-comp-card-header';

    const nameSpan = document.createElement('span');
    nameSpan.className = 'vp-comp-card-name';
    nameSpan.textContent = comp.instance_id;
    header.appendChild(nameSpan);

    const catSpan = document.createElement('span');
    catSpan.className = 'vp-comp-card-cat';
    catSpan.textContent = comp.catalog_id;
    header.appendChild(catSpan);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'vp-remove-btn';
    removeBtn.textContent = '×';
    removeBtn.title = `Remove ${comp.instance_id}`;
    removeBtn.addEventListener('click', async () => {
        design.components = design.components.filter(c => c.instance_id !== comp.instance_id);
        design.ui_placements = (design.ui_placements || []).filter(u => u.instance_id !== comp.instance_id);
        for (const net of (design.nets || [])) {
            net.pins = net.pins.filter(p => !p.startsWith(comp.instance_id + ':'));
        }
        design.nets = (design.nets || []).filter(n => n.pins.length >= 2);
        await _persistDesign(design);
        await _persistConversationSubmitDesign(design);
        setViewportData('design', _currentDesign || design);
    });
    header.appendChild(removeBtn);
    card.appendChild(header);

    // Properties
    const props = document.createElement('div');
    props.className = 'vp-comp-card-props';

    // Position row
    if (up) {
        const posRow = document.createElement('div');
        posRow.className = 'vp-comp-prop';
        posRow.innerHTML = `<span class="vp-prop-label">Position</span>`;
        const posVal = document.createElement('span');
        posVal.className = 'vp-prop-value vp-mono';
        posVal.textContent = `(${up.x_mm.toFixed(1)}, ${up.y_mm.toFixed(1)}) mm`;
        posRow.appendChild(posVal);
        props.appendChild(posRow);
    }

    // Mounting style row
    const mountRow = document.createElement('div');
    mountRow.className = 'vp-comp-prop';
    mountRow.innerHTML = `<span class="vp-prop-label">Mounting</span>`;

    const allowedStyles = _resolveAllowedStyles(comp);
    const currentStyle = _resolveCurrentStyle(comp);

    if (allowedStyles) {
        const select = document.createElement('select');
        select.className = 'vp-mount-select';
        for (const style of allowedStyles) {
            const opt = document.createElement('option');
            opt.value = style;
            opt.textContent = style;
            if (style === currentStyle) opt.selected = true;
            select.appendChild(opt);
        }
        select.addEventListener('change', async () => {
            comp.mounting_style = select.value;
            if (up) {
                if (select.value === 'side' && up.edge_index == null) {
                    up.edge_index = 0;
                    const verts = normaliseOutline(design.outline).verts;
                    const mid = _edgeMidpoint(verts, 0);
                    up.x_mm = mid.x;
                    up.y_mm = mid.y;
                } else if (select.value !== 'side') {
                    delete up.edge_index;
                }
            }
            await _persistDesign(design);
            await _persistConversationSubmitDesign(design);
            setViewportData('design', _currentDesign || design);
        });
        mountRow.appendChild(select);
    } else {
        const mountVal = document.createElement('span');
        mountVal.className = 'vp-prop-value';
        mountVal.textContent = currentStyle;
        mountRow.appendChild(mountVal);
    }
    props.appendChild(mountRow);

    // Edge indicator (side-mount only, auto-detected during drag)
    if (up && up.edge_index != null) {
        const edgeRow = document.createElement('div');
        edgeRow.className = 'vp-comp-prop';
        edgeRow.innerHTML = `<span class="vp-prop-label">Edge</span>`;
        const edgeVal = document.createElement('span');
        edgeVal.className = 'vp-prop-value vp-mono';
        const verts = normaliseOutline(design.outline).verts;
        const v0 = verts[up.edge_index], v1 = verts[(up.edge_index + 1) % verts.length];
        const len = Math.hypot(v1[0] - v0[0], v1[1] - v0[1]).toFixed(1);
        edgeVal.textContent = `Edge ${up.edge_index} (${len} mm) — drag to change`;
        edgeRow.appendChild(edgeVal);
        props.appendChild(edgeRow);
    }

    // Configurable properties
    _appendConfigFields(props, comp, design);

    card.appendChild(props);
    return card;
}

function _appendConfigFields(container, comp, design) {
    const catEntry = _getCatalogEntry(comp.catalog_id);
    const schema = catEntry?.configurable;
    if (!schema || Object.keys(schema).length === 0) return;

    if (!comp.config) comp.config = {};

    for (const [key, meta] of Object.entries(schema)) {
        const row = document.createElement('div');
        row.className = 'vp-comp-prop';

        const label = document.createElement('span');
        label.className = 'vp-prop-label';
        label.textContent = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        label.title = meta.description || '';
        row.appendChild(label);

        const input = document.createElement('input');
        input.className = 'vp-config-input';
        input.type = 'text';
        input.placeholder = meta.description ? meta.description.slice(0, 40) : key;
        input.title = meta.description || '';
        input.value = comp.config[key] ?? '';

        let saveTimer = null;
        input.addEventListener('input', () => {
            clearTimeout(saveTimer);
            saveTimer = setTimeout(async () => {
                const raw = input.value.trim();
                const num = Number(raw);
                comp.config[key] = raw === '' ? undefined : (isNaN(num) ? raw : num);
                if (comp.config[key] === undefined) delete comp.config[key];
                await _persistDesign(design);
                await _persistConversationSubmitDesign(design);
            }, 400);
        });

        row.appendChild(input);
        container.appendChild(row);
    }
}

async function _addUIComponent(catId, design) {
    const catEntry = _getCatalogEntry(catId);
    if (!catEntry) return;

    const existing = new Set((design.components || []).map(c => c.instance_id));
    let n = 1;
    let iid = `${catId.replace(/[^a-z0-9_]/gi, '_')}_${n}`;
    while (existing.has(iid)) { n++; iid = `${catId.replace(/[^a-z0-9_]/gi, '_')}_${n}`; }

    const newComp = { catalog_id: catId, instance_id: iid };
    if (catEntry.mounting?.style) newComp.mounting_style = catEntry.mounting.style;

    const verts = normaliseOutline(design.outline).verts;
    const cx = verts.reduce((s, v) => s + v[0], 0) / verts.length;
    const cy = verts.reduce((s, v) => s + v[1], 0) / verts.length;

    const newUp = { instance_id: iid, x_mm: Math.round(cx * 10) / 10, y_mm: Math.round(cy * 10) / 10 };
    if (catEntry.mounting?.style === 'side') {
        newUp.edge_index = 0;
        const mid = _edgeMidpoint(verts, 0);
        newUp.x_mm = mid.x;
        newUp.y_mm = mid.y;
    }

    if (!design.components) design.components = [];
    if (!design.ui_placements) design.ui_placements = [];
    design.components.push(newComp);
    design.ui_placements.push(newUp);

    await _persistDesign(design);
    await _persistConversationSubmitDesign(design);
    setViewportData('design', _currentDesign || design);
}


function _getCatalogEntry(catalogId) {
    const cat = state.catalog;
    if (!cat || !cat.components) return null;
    return cat.components.find(c => c.id === catalogId) || null;
}

function _getUICatalog() {
    const cat = state.catalog;
    if (!cat || !cat.components) return [];
    return cat.components.filter(c => c.ui_placement);
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

function _drawSideMountInGroup(group, up, verts, ox, oy) {
    const n = verts.length;
    const i = up.edge_index;
    const v0 = verts[i];
    const v1 = verts[(i + 1) % n];

    const ex = v1[0] - v0[0], ey = v1[1] - v0[1];
    const edgeLen = Math.hypot(ex, ey);
    if (edgeLen === 0) return;

    const dx = ex / edgeLen, dy = ey / edgeLen;
    const px = up.x_mm - v0[0], py = up.y_mm - v0[1];
    let t = (px * dx + py * dy) / edgeLen;
    t = Math.max(0.02, Math.min(0.98, t));

    const cx = ox + (v0[0] + t * ex) * SCALE;
    const cy = oy + (v0[1] + t * ey) * SCALE;

    const sdx = dx, sdy = dy;
    const nx = sdy, ny = -sdx;

    const arrowLen = 8, arrowW = 5;
    const tipX = cx + nx * arrowLen * SCALE / 4;
    const tipY = cy + ny * arrowLen * SCALE / 4;
    const b1x = cx + sdx * arrowW, b1y = cy + sdy * arrowW;
    const b2x = cx - sdx * arrowW, b2y = cy - sdy * arrowW;

    const arrow = document.createElementNS(NS, 'polygon');
    arrow.setAttribute('points', `${b1x},${b1y} ${tipX},${tipY} ${b2x},${b2y}`);
    arrow.setAttribute('class', 'vp-side-marker');

    const dot = document.createElementNS(NS, 'circle');
    dot.setAttribute('cx', cx);
    dot.setAttribute('cy', cy);
    dot.setAttribute('r', '3');
    dot.setAttribute('class', 'vp-side-dot');

    const label = document.createElementNS(NS, 'text');
    label.setAttribute('x', cx + nx * 16);
    label.setAttribute('y', cy + ny * 16);
    label.setAttribute('class', 'vp-ui-label');
    label.textContent = up.instance_id;

    group.appendChild(arrow);
    group.appendChild(dot);
    group.appendChild(label);
}

// ── Edge Profile Panel ─────────────────────────────────────────────────────────

/**
 * Mount a floating edge-profile control panel overlaid on the 3D viewport host.
 * Lets the user pick top / bottom wall profile (sharp, chamfer, fillet) and size,
 * live-previews changes via scene.update(), and persists via PATCH API.
 *
 * Returns { syncData(data), destroy() }.
 */
function _mountEdgePanel(host, initialData, scene) {
    let design = initialData;

    const panel = document.createElement('div');
    panel.className = 'ep-panel';
    panel.innerHTML = `
        <div class="ep-header">
            <span class="ep-title">Wall Edge</span>
            <button class="ep-collapse" title="Collapse">▼</button>
        </div>
        <div class="ep-body">
            <div class="ep-tabs">
                <button class="ep-tab ep-tab-active" data-side="top" title="Where wall meets lid">Top</button>
                <button class="ep-tab" data-side="bottom" title="Where wall meets floor">Bottom</button>
            </div>
            <div class="ep-types">
                <label class="ep-type-opt" title="Sharp 90° corner">
                    <input type="radio" name="ep-type" value="none" checked>
                    <span class="ep-type-icon">▐</span> Sharp
                </label>
                <label class="ep-type-opt" title="Flat 45° bevel">
                    <input type="radio" name="ep-type" value="chamfer">
                    <span class="ep-type-icon">◥</span> Chamfer
                </label>
                <label class="ep-type-opt" title="Smooth curved round-over">
                    <input type="radio" name="ep-type" value="fillet">
                    <span class="ep-type-icon">◜</span> Fillet
                </label>
            </div>
            <div class="ep-size-row" hidden>
                <span class="ep-size-lbl">Size</span>
                <input type="range" class="ep-size-slider" min="0.5" max="10" step="0.5" value="3">
                <span class="ep-size-val">3.0 mm</span>
            </div>
            <p class="ep-hint">Viewed from the side — the wall edge profile</p>
        </div>
    `;
    // Mount in the viewport toolbar so it sits above the 3D scene, not over it
    (document.getElementById('viewport-toolbar') ?? host).appendChild(panel);

    let activeSide = 'top';

    const _profileFor = (side) =>
        (design.enclosure ?? {})[`edge_${side}`] ?? { type: 'none', size_mm: 2.0 };

    function _refreshUI() {
        const prof = _profileFor(activeSide);
        const type = prof.type ?? 'none';
        panel.querySelectorAll('[name="ep-type"]').forEach(r => { r.checked = (r.value === type); });
        const size = prof.size_mm ?? 3.0;
        panel.querySelector('.ep-size-slider').value = size;
        panel.querySelector('.ep-size-val').textContent = size.toFixed(1) + ' mm';
        panel.querySelector('.ep-size-row').hidden = (type === 'none');
    }

    function _preview(side, type, size_mm) {
        if (!design.enclosure) design.enclosure = { height_mm: 25 };
        design.enclosure[`edge_${side}`] = { type, size_mm };
        scene.update(design);
    }

    let _patchSeq = 0;

    async function _persist(side, type, size_mm) {
        _preview(side, type, size_mm);
        const sid = state.session;
        if (!sid) return;
        const seq = ++_patchSeq;
        try {
            const res = await fetch(`${API}/api/session/design/enclosure?session=${encodeURIComponent(sid)}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ [`edge_${side}`]: { type, size_mm } }),
            });
            if (res.ok && seq === _patchSeq) {
                const saved = await res.json();
                design = saved;
                cacheData('design', saved);
            }
        } catch { /* non-fatal — user sees the optimistic preview regardless */ }
    }

    // Tab clicks
    panel.querySelectorAll('.ep-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            activeSide = btn.dataset.side;
            panel.querySelectorAll('.ep-tab').forEach(b =>
                b.classList.toggle('ep-tab-active', b.dataset.side === activeSide));
            _refreshUI();
        });
    });

    // Radio changes
    panel.querySelectorAll('[name="ep-type"]').forEach(radio => {
        radio.addEventListener('change', () => {
            if (!radio.checked) return;
            const type = radio.value;
            const size = parseFloat(panel.querySelector('.ep-size-slider').value);
            panel.querySelector('.ep-size-row').hidden = (type === 'none');
            _persist(activeSide, type, size);
        });
    });

    // Slider: optimistic 3D preview while dragging, persist on release
    const slider = panel.querySelector('.ep-size-slider');
    slider.addEventListener('input', () => {
        const size = parseFloat(slider.value);
        panel.querySelector('.ep-size-val').textContent = size.toFixed(1) + ' mm';
        const type = panel.querySelector('[name="ep-type"]:checked')?.value ?? 'none';
        if (type !== 'none') _preview(activeSide, type, size);
    });
    slider.addEventListener('change', () => {
        const type = panel.querySelector('[name="ep-type"]:checked')?.value ?? 'none';
        if (type !== 'none') _persist(activeSide, type, parseFloat(slider.value));
    });

    let _collapsed = false;
    const _body = panel.querySelector('.ep-body');
    const _collapseBtn = panel.querySelector('.ep-collapse');
    _collapseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _collapsed = !_collapsed;
        _body.style.display = _collapsed ? 'none' : '';
        _collapseBtn.textContent = _collapsed ? '▲' : '▼';
    });

    _refreshUI();

    return {
        syncData(data) { design = data; _refreshUI(); },
        destroy()      { panel.remove(); },
    };
}