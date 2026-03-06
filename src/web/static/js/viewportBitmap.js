/**
 * Viewport handler for the Bitmap step.
 *
 * Shows the printer build plate with the PCB outline centered on it,
 * and overlays the conductive-ink bitmap grid so the user can verify
 * that traces are correctly positioned and scaled.
 */

import { registerHandler } from './viewport.js';
import { normaliseOutline, NS } from './viewportUtils.js';
import { buildNetColorMap } from './viewportRouting.js';

const PLATE_PAD = 30;

registerHandler('bitmap', {
    label: 'Bitmap Verification',
    placeholder: 'Run the router to generate a trace bitmap',

    render(el, data) {
        el.innerHTML = '';
        el.appendChild(buildBitmapView(data));
    },

    clear(el) {
        el.innerHTML = '<p class="viewport-empty">Run the router to generate a trace bitmap</p>';
    },
});


function buildBitmapView(data) {
    const {
        bitmap_cols, bitmap_rows,
        bed_width, bed_depth,
        bed_offset_x, bed_offset_y,
        outline, components = [], traces = [], trace_width_mm = 0.5,
        bitmap_b64,
    } = data;

    const wrap = document.createElement('div');
    wrap.className = 'vp-placement';

    const section = document.createElement('div');
    section.className = 'vp-section';
    const heading = document.createElement('h4');
    heading.textContent = 'Build Plate — Bitmap Overlay';
    section.appendChild(heading);

    const container = document.createElement('div');
    container.style.cssText = 'position:relative; overflow:auto; max-height:80vh;';

    const SCALE = Math.min(
        600 / bed_width,
        600 / bed_depth,
        4,
    );

    const svgW = bed_width * SCALE + PLATE_PAD * 2;
    const svgH = bed_depth * SCALE + PLATE_PAD * 2;

    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${svgW} ${svgH}`);
    svg.setAttribute('class', 'vp-outline-svg');
    svg.style.maxWidth = '100%';

    // Build plate background
    const plate = document.createElementNS(NS, 'rect');
    plate.setAttribute('x', PLATE_PAD);
    plate.setAttribute('y', PLATE_PAD);
    plate.setAttribute('width', bed_width * SCALE);
    plate.setAttribute('height', bed_depth * SCALE);
    plate.setAttribute('fill', '#1a1a2e');
    plate.setAttribute('stroke', '#444');
    plate.setAttribute('stroke-width', '2');
    plate.setAttribute('stroke-dasharray', '6 3');
    svg.appendChild(plate);

    // Build plate label
    const plateLabel = document.createElementNS(NS, 'text');
    plateLabel.setAttribute('x', PLATE_PAD + 6);
    plateLabel.setAttribute('y', PLATE_PAD + 14);
    plateLabel.setAttribute('font-size', '10');
    plateLabel.setAttribute('fill', '#666');
    plateLabel.textContent = `Build plate ${bed_width} × ${bed_depth} mm`;
    svg.appendChild(plateLabel);

    // Dimension labels on plate edges
    const dimH = document.createElementNS(NS, 'text');
    dimH.setAttribute('x', PLATE_PAD + (bed_width * SCALE) / 2);
    dimH.setAttribute('y', svgH - 8);
    dimH.setAttribute('class', 'vp-dim-label');
    dimH.textContent = `${bed_width} mm`;
    svg.appendChild(dimH);

    const dimV = document.createElementNS(NS, 'text');
    dimV.setAttribute('x', 8);
    dimV.setAttribute('y', PLATE_PAD + (bed_depth * SCALE) / 2);
    dimV.setAttribute('class', 'vp-dim-label');
    dimV.setAttribute('transform', `rotate(-90, 8, ${PLATE_PAD + (bed_depth * SCALE) / 2})`);
    dimV.textContent = `${bed_depth} mm`;
    svg.appendChild(dimV);

    // bed_offset = bed_centre - model_bbox_centre (same as PrusaSlicer centering).
    // Model coord → bed coord: bed = model + bed_offset.
    // SVG y-axis is flipped relative to bed y.
    const bedToSvgX = (bx) => PLATE_PAD + bx * SCALE;
    const bedToSvgY = (by) => PLATE_PAD + (bed_depth - by) * SCALE;

    // Board outline on the build plate.
    // Model coords → bed coords via + bed_offset.
    const { verts, corners } = normaliseOutline(outline);
    if (verts.length >= 3) {
        const bedVerts = verts.map(v => [v[0] + bed_offset_x, v[1] + bed_offset_y]);
        const svgVerts = bedVerts.map(v => [bedToSvgX(v[0]), bedToSvgY(v[1])]);

        let pathD = `M ${svgVerts[0][0]} ${svgVerts[0][1]}`;
        for (let i = 1; i < svgVerts.length; i++) {
            pathD += ` L ${svgVerts[i][0]} ${svgVerts[i][1]}`;
        }
        pathD += ' Z';

        const outlinePath = document.createElementNS(NS, 'path');
        outlinePath.setAttribute('d', pathD);
        outlinePath.setAttribute('fill', 'rgba(30, 80, 120, 0.3)');
        outlinePath.setAttribute('stroke', '#58a6ff');
        outlinePath.setAttribute('stroke-width', '1.5');
        svg.appendChild(outlinePath);
    }

    // Components (simple rectangles)
    const COMP_COLORS = [
        '#58a6ff', '#3fb950', '#d29922', '#f778ba', '#bc8cff',
        '#79c0ff', '#56d364', '#e3b341', '#ff7b72', '#a5d6ff',
    ];
    components.forEach((comp, idx) => {
        const color = COMP_COLORS[idx % COMP_COLORS.length];
        const bedX = (comp.x_mm || 0) + bed_offset_x;
        const bedY = (comp.y_mm || 0) + bed_offset_y;
        const body = comp.body || {};
        const rot = comp.rotation_deg || 0;
        let bw = (body.width_mm || 5);
        let bh = (body.length_mm || body.width_mm || 5);
        if (body.shape === 'circle') {
            bw = bh = body.diameter_mm || 5;
        }
        if (rot === 90 || rot === 270) {
            [bw, bh] = [bh, bw];
        }
        const rect = document.createElementNS(NS, 'rect');
        rect.setAttribute('x', bedToSvgX(bedX) - (bw / 2) * SCALE);
        rect.setAttribute('y', bedToSvgY(bedY) - (bh / 2) * SCALE);
        rect.setAttribute('width', bw * SCALE);
        rect.setAttribute('height', bh * SCALE);
        rect.setAttribute('fill', color);
        rect.setAttribute('fill-opacity', '0.15');
        rect.setAttribute('stroke', color);
        rect.setAttribute('stroke-width', '1');
        rect.setAttribute('stroke-opacity', '0.4');
        svg.appendChild(rect);

        const label = document.createElementNS(NS, 'text');
        label.setAttribute('x', bedToSvgX(bedX));
        label.setAttribute('y', bedToSvgY(bedY) + 3);
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('font-size', Math.max(6, Math.min(9, SCALE * 2)));
        label.setAttribute('fill', color);
        label.setAttribute('opacity', '0.6');
        label.textContent = comp.instance_id || '';
        svg.appendChild(label);
    });

    // Traces (dimmed, for reference)
    if (traces.length > 0) {
        const netColorMap = buildNetColorMap(traces.map(t => t.net_id));
        const traceWPx = (trace_width_mm || 0.5) * SCALE;
        for (const trace of traces) {
            const path = trace.path;
            if (!path || path.length < 2) continue;
            const points = path.map(p => {
                const bx = p[0] + bed_offset_x;
                const by = p[1] + bed_offset_y;
                return `${bedToSvgX(bx)},${bedToSvgY(by)}`;
            }).join(' ');
            const polyline = document.createElementNS(NS, 'polyline');
            polyline.setAttribute('points', points);
            polyline.setAttribute('fill', 'none');
            polyline.setAttribute('stroke', netColorMap[trace.net_id] || '#888');
            polyline.setAttribute('stroke-width', String(traceWPx));
            polyline.setAttribute('stroke-linecap', 'round');
            polyline.setAttribute('stroke-linejoin', 'round');
            polyline.setAttribute('opacity', '0.4');
            svg.appendChild(polyline);
        }
    }

    // Bitmap overlay — decode 1-bit-per-pixel base64, render to canvas, embed as SVG image
    if (bitmap_b64) {
        const raw = atob(bitmap_b64);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);

        const byteCols = Math.ceil(bitmap_cols / 8);
        const canvas = document.createElement('canvas');
        canvas.width = bitmap_cols;
        canvas.height = bitmap_rows;
        const ctx = canvas.getContext('2d');
        const img = ctx.createImageData(bitmap_cols, bitmap_rows);

        for (let row = 0; row < bitmap_rows; row++) {
            const rowOffset = row * byteCols;
            for (let col = 0; col < bitmap_cols; col++) {
                const byteIdx = rowOffset + (col >> 3);
                const bitIdx = 7 - (col & 7);
                const isInk = (bytes[byteIdx] >> bitIdx) & 1;
                const pi = (row * bitmap_cols + col) * 4;
                if (isInk) {
                    img.data[pi]     = 0;
                    img.data[pi + 1] = 255;
                    img.data[pi + 2] = 100;
                    img.data[pi + 3] = 200;
                }
            }
        }
        ctx.putImageData(img, 0, 0);

        const bitmapImage = document.createElementNS(NS, 'image');
        bitmapImage.setAttribute('x', PLATE_PAD);
        bitmapImage.setAttribute('y', PLATE_PAD);
        bitmapImage.setAttribute('width', bed_width * SCALE);
        bitmapImage.setAttribute('height', bed_depth * SCALE);
        bitmapImage.setAttribute('image-rendering', 'pixelated');
        bitmapImage.setAttributeNS('http://www.w3.org/1999/xlink', 'href', canvas.toDataURL());
        svg.appendChild(bitmapImage);
    }

    // Toggle controls
    const controls = document.createElement('div');
    controls.style.cssText = 'position:absolute; top:6px; left:6px; z-index:1; display:flex; gap:10px;';

    const bitmapToggle = document.createElement('label');
    bitmapToggle.style.cssText = 'font-size:11px; color:#c9d1d9; cursor:pointer; user-select:none;';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.style.cssText = 'margin-right:4px; vertical-align:middle; cursor:pointer;';
    const bitmapImageRef = svg.querySelector('image');
    if (bitmapImageRef) {
        cb.addEventListener('change', () => {
            bitmapImageRef.setAttribute('display', cb.checked ? 'inline' : 'none');
        });
    }
    bitmapToggle.appendChild(cb);
    bitmapToggle.appendChild(document.createTextNode('Bitmap'));
    controls.appendChild(bitmapToggle);

    const svgWrap = document.createElement('div');
    svgWrap.style.position = 'relative';
    svgWrap.appendChild(svg);
    svgWrap.appendChild(controls);

    container.appendChild(svgWrap);
    section.appendChild(container);
    wrap.appendChild(section);
    return wrap;
}
