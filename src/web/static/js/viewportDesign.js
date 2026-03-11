/**
 * Viewport handler for the Design step.
 *
 * Renders a 3D CSG preview of the design with surface placement markers.
 */

import { registerHandler, cacheData } from './viewport.js';
import { drawComponentIcon } from './componentRenderer.js';
import { normaliseOutline, buildOutlinePath, snapToEdge, esc, SCALE, PAD, NS, attachViewToggle } from './viewportUtils.js';
import { state, API } from './state.js';

// ── Toggle controller ───────────────────────────────────────────

const _toggle = attachViewToggle(
    'design',
    (el, design) => { el.innerHTML = ''; el.appendChild(buildPreview(design)); },
    async (host) => {
        const { create3DScene } = await import('./viewport3d.js');
        const scene = create3DScene(host);
        // Wrap to also manage the edge profile panel overlay
        let panel = null;
        return {
            update(data, opts) {
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

    render(el, design) {
        if (design && design.has_mesh) {
            _render3dDesign(el, design);
        } else {
            _toggle.render(el, design);
        }
    },

    clear(el) {
        _cleanup3dDesign();
        _toggle.clear(el);
        el.innerHTML = '<p class="viewport-empty">Submit a design prompt to see the preview</p>';
    },

    unmount() {
        _cleanup3dDesign();
        _toggle.unmount();
    },
    onResize(el,w,h) {
        if (_scene3d) _scene3d.resize(w, h);
        else _toggle.resize(w, h);
    },
});


// ── 3D CSG design renderer ──────────────────────────────────

let _scene3d = null;
let _scene3dHost = null;

function _cleanup3dDesign() {
    if (_scene3d) { _scene3d.destroy(); _scene3d = null; }
    if (_scene3dHost) { _scene3dHost.remove(); _scene3dHost = null; }
}

async function _render3dDesign(el, design) {
    _cleanup3dDesign();
    el.innerHTML = '';

    // Create the 3D viewport
    const host = document.createElement('div');
    host.className = 'vp-3d-host';
    host.style.cssText = 'width:100%;height:100%;position:relative;';
    el.appendChild(host);
    _scene3dHost = host;

    // Info overlay
    const info = document.createElement('div');
    info.style.cssText = 'position:absolute;top:8px;left:8px;color:#8ba;font-size:12px;z-index:10;pointer-events:none;';
    const compCount = design.components?.length || 0;
    const placeCount = design.surface_placements?.length || 0;
    info.textContent = `3D Design: ${compCount} components · ${placeCount} surface placements`;
    host.appendChild(info);

    // Load Three.js and STLLoader
    const THREE = await import('three');
    const { OrbitControls } = await import('three/addons/controls/OrbitControls.js');
    const { GLTFLoader } = await import('three/addons/loaders/GLTFLoader.js');
    const { getSharedRenderer, setSharedRenderer } = await import('./viewport.js');

    let renderer = getSharedRenderer();
    if (!renderer) {
        renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setClearColor(0x000000, 0);
        renderer.setPixelRatio(window.devicePixelRatio);
        setSharedRenderer(renderer);
    }

    const canvas = renderer.domElement;
    canvas.style.display = 'block';
    host.appendChild(canvas);

    const w = host.clientWidth || 600;
    const h = host.clientHeight || 400;
    renderer.setSize(w, h);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0d1117);
    scene.fog = new THREE.FogExp2(0x0d1117, 0.002);

    const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 10000);
    camera.position.set(80, 100, 150);

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const key = new THREE.DirectionalLight(0xffffff, 1.2);
    key.position.set(80, 200, 120);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xaac8ff, 0.5);
    fill.position.set(-80, 60, -120);
    scene.add(fill);

    const grid = new THREE.GridHelper(400, 40, 0x1a2a3a, 0x151f2a);
    grid.position.y = -0.5;
    scene.add(grid);

    let animId = null;
    function animate() {
        animId = requestAnimationFrame(animate);
        controls.update();
        renderer.render(scene, camera);
    }
    animate();

    if (design.has_mesh && state.session) {
        const loader = new GLTFLoader();
        const url = `${API}/api/session/design/mesh?session=${encodeURIComponent(state.session)}&t=${Date.now()}`;
        loader.load(url, (gltf) => {
            const root = gltf.scene;
            root.rotation.x = -Math.PI / 2; // Z-up → Y-up

            // Apply viewport material to all meshes
            const mat = new THREE.MeshPhongMaterial({
                color: 0x3a7a9a,
                shininess: 40,
                transparent: true,
                opacity: 0.6,
                side: THREE.DoubleSide,
                depthWrite: true,
            });
            root.traverse((child) => {
                if (child.isMesh) {
                    child.material = mat;
                    // Add wireframe overlay per sub-mesh
                    const wire = new THREE.WireframeGeometry(child.geometry);
                    const wireMat = new THREE.LineBasicMaterial({ color: 0x60b0d0, opacity: 0.3, transparent: true });
                    child.add(new THREE.LineSegments(wire, wireMat));
                }
            });
            scene.add(root);

            _addPlacementMarkers(scene, design, THREE);

            // Fit camera
            const box = new THREE.Box3().setFromObject(root);
            if (!box.isEmpty()) {
                const center = box.getCenter(new THREE.Vector3());
                const size = box.getSize(new THREE.Vector3());
                const maxDim = Math.max(size.x, size.y, size.z);
                const dist = maxDim * 1.4 / Math.tan((camera.fov / 2) * Math.PI / 180);
                camera.position.set(center.x + dist * 0.6, center.y + dist * 0.5, center.z + dist * 0.8);
                camera.lookAt(center);
                controls.target.copy(center);
                controls.update();
            }
        });
    }

    _scene3d = {
        resize(w2, h2) {
            if (w2 <= 0 || h2 <= 0) return;
            renderer.setSize(w2, h2);
            camera.aspect = w2 / h2;
            camera.updateProjectionMatrix();
        },
        destroy() {
            if (animId !== null) cancelAnimationFrame(animId);
            controls.dispose();
            if (canvas.parentNode === host) host.removeChild(canvas);
        },
    };
}

const _PLACE_COLORS = [0x4ea8d8, 0x52d474, 0xeeb830, 0xee6e6e, 0xb890e8, 0x40c0d0];

function _addPlacementMarkers(scene, design, THREE) {
    const placements = design.surface_placements || [];
    const compMap = {};
    for (const c of (design.components || [])) compMap[c.instance_id] = c;

    placements.forEach((sp, i) => {
        const pos = sp.snapped_position || sp.position;
        if (!pos || pos.length < 3) return;
        const color = _PLACE_COLORS[i % _PLACE_COLORS.length];

        // Marker sphere
        const geo = new THREE.SphereGeometry(2.5, 16, 16);
        const mat = new THREE.MeshPhongMaterial({ color, emissive: color, emissiveIntensity: 0.3 });
        const marker = new THREE.Mesh(geo, mat);
        marker.position.set(pos[0], pos[2], -pos[1]);
        scene.add(marker);

        // Normal arrow
        const normal = sp.surface_normal;
        if (normal && normal.length === 3) {
            const dir = new THREE.Vector3(normal[0], normal[2], -normal[1]).normalize();
            const arrow = new THREE.ArrowHelper(dir, marker.position, 8, color, 3, 2);
            scene.add(arrow);
        }

        // Label
        const comp = compMap[sp.instance_id];
        const label = comp ? comp.instance_id : sp.instance_id;
        const sprite = _textSprite(label, color, THREE);
        sprite.position.set(pos[0], pos[2] + 6, -pos[1]);
        scene.add(sprite);
    });
}

function _textSprite(text, color, THREE) {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    canvas.width = 256;
    canvas.height = 64;
    ctx.fillStyle = 'transparent';
    ctx.fillRect(0, 0, 256, 64);
    ctx.font = 'bold 28px monospace';
    ctx.fillStyle = '#' + color.toString(16).padStart(6, '0');
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 128, 32);
    const tex = new THREE.CanvasTexture(canvas);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true });
    const sprite = new THREE.Sprite(mat);
    sprite.scale.set(20, 5, 1);
    return sprite;
}


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
    // Screen convention: y=0 at top, y increases downward (matches SVG).
    const oy = PAD - minY * SCALE;

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

    // UI placements — use shared component renderer when body data
    // is available, otherwise fall back to simple marker dots.
    const compMap = {};
    for (const c of (design.components || [])) {
        compMap[c.instance_id] = c;
    }

    const UI_COLORS = [
        '#58a6ff', '#3fb950', '#d29922', '#f778ba', '#bc8cff',
        '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#a5d6ff',
    ];

    ui_placements.forEach((up, idx) => {
        const comp = compMap[up.instance_id];
        const color = UI_COLORS[idx % UI_COLORS.length];

        if (up.edge_index != null) {
            // Side-mount — snap to wall, then draw component icon
            const snapInfo = snapToEdge(up, verts, normaliseOutline(design.outline).zTops, (design.enclosure?.height_mm ?? 25));
            if (comp && comp.body) {
                const fakeComp = {
                    ...comp,
                    x_mm: snapInfo.x, y_mm: snapInfo.y,
                    rotation_deg: snapInfo.rot,
                };
                drawComponentIcon(svg, fakeComp, ox, oy, SCALE, {
                    color, bodyOpacity: 0.2, showPins: !!(comp.pins),
                });
            } else {
                drawSideMountMarker(svg, NS, up, { vertices: verts }, ox, oy);
            }
        } else {
            // Interior UI component
            if (comp && comp.body) {
                const fakeComp = {
                    ...comp,
                    x_mm: up.x_mm, y_mm: up.y_mm,
                    rotation_deg: 0,
                };
                drawComponentIcon(svg, fakeComp, ox, oy, SCALE, {
                    color, bodyOpacity: 0.2, showPins: !!(comp.pins),
                });
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

                svg.appendChild(marker);
                svg.appendChild(label);
            }
        }
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

    // Position on the edge (screen convention: Y not flipped)
    const cx = ox + (v0[0] + t * ex) * SCALE;
    const cy = oy + (v0[1] + t * ey) * SCALE;

    // Edge direction in screen space (no Y flip)
    const sdx = dx, sdy = dy;
    // Inward normal in screen space: perpendicular to (sdx,sdy) rotated 90° CW
    // For clockwise winding, inward normal points right of edge direction
    const nx = sdy, ny = -sdx;

    // Draw a small triangle/arrow pointing inward from the wall
    const arrowLen = 8;   // length of arrow in px
    const arrowW   = 5;   // half-width of arrow base in px

    // Tip of arrow (pointing inward)
    const tipX = cx + nx * arrowLen * SCALE / 4;
    const tipY = cy + ny * arrowLen * SCALE / 4;

    // Base corners (on the wall)
    const b1x = cx + sdx * arrowW;
    const b1y = cy + sdy * arrowW;
    const b2x = cx - sdx * arrowW;
    const b2y = cy - sdy * arrowW;

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