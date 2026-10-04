// SPDX-License-Identifier: MIT
// Position is a fraction of usable travel, so it survives resolution and scale
// changes. It is saved once on drag release, rather than on every motion event.
export function menuGeometry(area, position, size = 360) {
    const margin = 20;
    const scale = Math.min(1, (area.width - 2 * margin) / size, (area.height - 2 * margin) / size);
    const travelX = Math.max(0, area.width - 2 * margin - size * scale);
    const travelY = Math.max(0, area.height - 2 * margin - size * scale);
    return {x: area.x + margin + travelX * Math.max(0, Math.min(1, position[0])),
        y: area.y + margin + travelY * Math.max(0, Math.min(1, position[1])), scale};
}

export function positionFromOrigin(area, x, y, scaledSize) {
    const margin = 20;
    return [Math.max(0, Math.min(1, (x - area.x - margin) / Math.max(1, area.width - 2 * margin - scaledSize))),
        Math.max(0, Math.min(1, (y - area.y - margin) / Math.max(1, area.height - 2 * margin - scaledSize)))];
}

export function sectorAt(x, y, count, size = 360, inner = 111) {
    const center = size / 2;
    const radius = Math.hypot(x - center, y - center);
    if (!count || radius <= inner || radius > center) return -1;
    const step = 2 * Math.PI / count;
    const angle = (Math.atan2(y - center, x - center) + Math.PI / 2 + step / 2 + 2 * Math.PI) % (2 * Math.PI);
    return Math.floor(angle / step) % count;
}
