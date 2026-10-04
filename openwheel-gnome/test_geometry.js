import {menuGeometry, positionFromOrigin, sectorAt} from './extension/menuGeometry.js';
function check(value, message) { if (!value) throw new Error(message); }
for (const area of [{x: 0, y: 32, width: 1280, height: 768},
    {x: -2560, y: 0, width: 2560, height: 1440}, {x: 0, y: 0, width: 320, height: 300}]) {
    for (const position of [[0, 0], [.92, .82], [1, 1]]) {
        const {x, y, scale} = menuGeometry(area, position);
        check(x >= area.x && y >= area.y && x + 360 * scale <= area.x + area.width &&
            y + 360 * scale <= area.y + area.height, 'Menu must stay on screen');
        const saved = positionFromOrigin(area, x, y, 360 * scale);
        const again = menuGeometry(area, saved);
        check(Math.abs(again.x - x) < 1e-6 && Math.abs(again.y - y) < 1e-6, 'Drag coordinates round-trip');
    }
}
for (const count of [1, 3, 5, 6]) {
    for (let i = 0; i < count; i++) {
        const angle = -Math.PI / 2 + i * Math.PI * 2 / count;
        check(sectorAt(180 + Math.cos(angle) * 144, 180 + Math.sin(angle) * 144, count) === i,
            `Sector ${i}/${count} hit testing`);
    }
    check(sectorAt(180, 180, count) === -1, 'Centre must stay a drag handle');
    check(sectorAt(0, 0, count) === -1, 'Outside the circle is not a function');
}
print('Menu placement, scaling, saved position and sector hit testing: passed');
