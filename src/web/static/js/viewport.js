/**
 * Viewport — step-dependent preview panel (right side of split layout).
 *
 * Framework only — no step-specific rendering logic lives here.
 * Each pipeline step registers a ViewportHandler via registerHandler().
 *
 * ── ViewportHandler interface ──────────────────────────────────
 *   label:       string                — title shown in the viewport header
 *   placeholder: string                — message when no data is loaded
 *   render:      (el: Element, data: any) => void
 *   clear:       (el: Element) => void
 * ───────────────────────────────────────────────────────────────
 */

const handlers = new Map();
const cache = new Map();       // step -> last data payload
let activeStep = null;

// ── DOM refs (lazy) ───────────────────────────────────────────

const contentEl  = () => document.getElementById('viewport-content');
const viewportEl = () => document.getElementById('viewport');

// ── Public API ────────────────────────────────────────────────

/**
 * Register a handler for a pipeline step.
 * @param {string} step   — matches data-step in nav ("design", "placement", …)
 * @param {ViewportHandler} handler
 */
export function registerHandler(step, handler) {
    handlers.set(step, handler);
}

/**
 * Switch the viewport to a new step.
 * If cached data exists for the step, it is re-rendered automatically.
 */
export function setStep(step) {
    activeStep = step;
    const handler = handlers.get(step);
    const el = contentEl();
    if (!el) return;

    if (!handler) {
        el.innerHTML = '<p class="viewport-empty">No preview available for this step</p>';
        return;
    }

    const data = cache.get(step);
    if (data !== undefined) {
        handler.render(el, data);
    } else {
        handler.clear(el);
    }
}

/**
 * Push new data for a step.
 * If the step is currently active the viewport re-renders immediately.
 */
export function setData(step, data) {
    cache.set(step, data);
    if (step === activeStep) {
        const handler = handlers.get(step);
        if (handler) handler.render(contentEl(), data);
    }
}

/**
 * Clear cached data (and viewport) for a step (or all steps).
 */
export function clearData(step) {
    if (step) {
        cache.delete(step);
        if (step === activeStep) {
            const handler = handlers.get(step);
            if (handler) handler.clear(contentEl());
        }
    } else {
        cache.clear();
        const handler = handlers.get(activeStep);
        if (handler) handler.clear(contentEl());
    }
}

// ── Drag-resize ───────────────────────────────────────────────

function initResize() {
    const handle = document.getElementById('viewport-resize-handle');
    const vp = viewportEl();
    if (!handle || !vp) return;

    let startX, startW;

    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startX = e.clientX;
        startW = vp.offsetWidth;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });

    function onMove(e) {
        const delta = startX - e.clientX;  // dragging left = wider
        const newW = Math.max(200, Math.min(startW + delta, window.innerWidth * 0.6));
        vp.style.width = newW + 'px';
    }

    function onUp() {
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
    }
}

// Auto-init once DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initResize);
} else {
    initResize();
}
