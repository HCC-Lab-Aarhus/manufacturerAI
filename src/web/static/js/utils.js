/* Shared utilities */

export function formatDate(iso) {
    try {
        const d = new Date(iso);
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch {
        return iso;
    }
}

export function formatDimensions(c) {
    if (c.body.shape === 'circle') {
        return `⌀${c.body.diameter_mm}mm × ${c.body.height_mm}mm H`;
    }
    return `${c.body.width_mm}×${c.body.length_mm}mm × ${c.body.height_mm}mm H`;
}

/** Hide a modal (set hidden attribute). */
export function closeModal(el) {
    if (typeof el === 'string') el = document.getElementById(el);
    if (el) el.hidden = true;
}

/** Show a modal (remove hidden attribute). */
export function openModal(el) {
    if (typeof el === 'string') el = document.getElementById(el);
    if (el) el.hidden = false;
}
