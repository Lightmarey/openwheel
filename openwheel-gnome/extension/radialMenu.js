// SPDX-License-Identifier: MIT
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';
import St from 'gi://St';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import {menuGeometry, sectorAt, positionFromOrigin} from './menuGeometry.js';

const SIZE = 360;
const CENTER = SIZE / 2;
const INNER = 111;
const ICON_RADIUS = 144;
const ICONS = {volume: 'audio-volume-high-symbolic', brightness: 'display-brightness-symbolic',
    apps: 'view-grid-symbolic', workspace: 'view-paged-symbolic', scroll: 'input-mouse-symbolic',
    shortcut: 'input-keyboard-symbolic', mpris: 'media-playback-start-symbolic', mpv: 'media-playback-start-symbolic',
    group: 'view-grid-symbolic', back: 'go-previous-symbolic', next: 'view-more-symbolic'};

// A single drawing surface paints the annulus. Actors are used for text, icons,
// accessibility and dragging; there are no per-frame timers or image assets.
export const RadialMenu = GObject.registerClass(class RadialMenu extends St.Widget {
    _init(settings, activate, highlight, keepOpen) {
        super._init({style_class: 'openwheel-ring', width: SIZE, height: SIZE,
            layout_manager: new Clutter.FixedLayout(), reactive: true, visible: false});
        this._settings = settings;
        this._activate = activate;
        this._highlight = highlight;
        this._keepOpen = keepOpen;
        this._items = [];
        this._selected = 0;
        this._icons = [];
        this._position = settings.get_value('menu-position').deepUnpack();
        this._canvas = new St.DrawingArea({width: SIZE, height: SIZE, reactive: true});
        this.add_child(this._canvas);
        this._canvas.connect('repaint', () => this._paint());
        this._canvas.connect('button-press-event', (_actor, event) => {
            if (event.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
            const index = this._indexAt(event);
            if (index >= 0) this._activate(index);
            return Clutter.EVENT_STOP;
        });
        this._canvas.connect('motion-event', (_actor, event) => {
            const index = this._indexAt(event);
            if (index >= 0 && index !== this._selected) this._highlight(index);
            return Clutter.EVENT_PROPAGATE;
        });
        this._center = new St.Button({style_class: 'openwheel-center', x: CENTER - INNER, y: CENTER - INNER,
            width: INNER * 2, height: INNER * 2, reactive: true, can_focus: true,
            accessible_name: '拖动圆心调整菜单位置'});
        const content = new St.BoxLayout({vertical: true, x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER, width: 180, style_class: 'openwheel-center-content'});
        this._title = new St.Label({style_class: 'openwheel-title', x_align: Clutter.ActorAlign.CENTER});
        this._title.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._title.clutter_text.set_line_alignment(Pango.Alignment.CENTER);
        this._subtitle = new St.Label({style_class: 'openwheel-subtitle',
            x_align: Clutter.ActorAlign.CENTER});
        this._subtitle.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._subtitle.clutter_text.set_line_alignment(Pango.Alignment.CENTER);
        this._handle = new St.Icon({icon_name: 'view-grid-symbolic', icon_size: 13,
            style_class: 'openwheel-handle', x_align: Clutter.ActorAlign.CENTER});
        content.add_child(this._handle);
        content.add_child(this._title);
        content.add_child(this._subtitle);
        this._center.set_child(content);
        this.add_child(this._center);
        this._center.connect('button-press-event', (_actor, event) => {
            if (event.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
            this.beginDrag(...event.get_coords());
            return Clutter.EVENT_STOP;
        });
        this._center.connect('motion-event', (_actor, event) => {
            if (!this._drag) return Clutter.EVENT_PROPAGATE;
            this.dragTo(...event.get_coords());
            return Clutter.EVENT_STOP;
        });
        this._center.connect('button-release-event', (_actor, event) => {
            if (!this._drag || event.get_button() !== 1) return Clutter.EVENT_PROPAGATE;
            this.endDrag();
            return Clutter.EVENT_STOP;
        });
        this.connect('scroll-event', (_actor, event) => {
            const direction = event.get_scroll_direction();
            let delta = direction === Clutter.ScrollDirection.UP ? -1 : 1;
            if (direction === Clutter.ScrollDirection.SMOOTH) {
                const [, dy] = event.get_scroll_delta();
                if (Math.abs(dy) < 0.2) return Clutter.EVENT_STOP;
                delta = Math.sign(dy);
            }
            if (this._items.length) this._highlight((this._selected + delta + this._items.length) % this._items.length);
            return Clutter.EVENT_STOP;
        });
        this._settingsSignal = settings.connect('changed::menu-position', () => {
            this._position = settings.get_value('menu-position').deepUnpack();
            if (this._available && !this._drag) this.place(this._available);
        });
    }

    update(items, selected, profile, path) {
        const signature = JSON.stringify(items);
        if (signature !== this._signature) {
            this._signature = signature;
            this._items = items;
            this._icons.forEach(icon => icon.destroy());
            this._icons = items.map((item, index) => {
                const angle = -Math.PI / 2 + index * 2 * Math.PI / items.length;
                const icon = new St.Icon({icon_name: ICONS[item.type] ?? ICONS[item.kind] ?? ICONS.shortcut,
                    icon_size: 25, style_class: 'openwheel-sector-icon', reactive: false,
                    width: 40, height: 40, x: CENTER + Math.cos(angle) * ICON_RADIUS - 20,
                    y: CENTER + Math.sin(angle) * ICON_RADIUS - 20});
                icon.accessible_name = item.name;
                this.add_child(icon);
                return icon;
            });
            // Keep the centre's shadow and content above the drawing surface.
            this.set_child_above_sibling(this._center, null);
        }
        this._selected = selected;
        const item = items[selected];
        this._title.text = item?.name ?? '选择功能';
        this._subtitle.text = item?.available === false ? '待配置' :
            path.length ? path.at(-1).replace('@group:', '') : profile;
        this._handle.icon_name = item?.type === 'group' ? 'view-grid-symbolic' :
            item?.type === 'back' ? 'go-previous-symbolic' : 'view-grid-symbolic';
        this._center.accessible_name = `${item?.name ?? '选择功能'}；拖动圆心调整位置`;
        this._icons.forEach((icon, index) => {
            icon.set_style(index === selected ? 'color: #f1e3c5;' : 'color: #d4d5dc;');
            icon.opacity = items[index].available === false ? 125 : 255;
        });
        this._canvas.queue_repaint();
    }

    _indexAt(event) {
        const [x, y] = event.get_coords();
        const [success, localX, localY] = this.transform_stage_point(x, y);
        return success ? sectorAt(localX, localY, this._items.length, SIZE, INNER) : -1;
    }

    _paint() {
        const cr = this._canvas.get_context();
        cr.setSourceRGBA(0.24, 0.26, 0.30, 0.97);
        cr.arc(CENTER, CENTER, CENTER - 1, 0, 2 * Math.PI);
        cr.fill();
        const step = 2 * Math.PI / Math.max(1, this._items.length);
        for (let index = 0; index < this._items.length; index++) {
            const angle = -Math.PI / 2 + index * step;
            const gap = this._items.length > 1 ? 0.009 : 0;
            const start = angle - step / 2 + gap;
            const end = angle + step / 2 - gap;
            cr.newPath();
            cr.arc(CENTER, CENTER, CENTER - 2, start, end);
            cr.arcNegative(CENTER, CENTER, INNER - 1, end, start);
            cr.closePath();
            if (index === this._selected) cr.setSourceRGBA(0.63, 0.60, 0.52, 0.92);
            else cr.setSourceRGBA(0.31, 0.33, 0.38, 0.97);
            cr.fill();
        }
        cr.setSourceRGBA(1, 1, 1, 0.14);
        cr.setLineWidth(1);
        cr.arc(CENTER, CENTER, CENTER - 1, 0, 2 * Math.PI);
        cr.stroke();
        cr.$dispose();
    }

    place(available) {
        this._available = available;
        if (this._drag) return;
        this._geometry = menuGeometry(available, this._position, SIZE);
        const {x, y, scale} = this._geometry;
        this.set_scale(scale, scale);
        this.set_position(x, y);
    }

    beginDrag(x, y) {
        if (!this._available) return;
        this._drag = {x, y, originX: this.x, originY: this.y, moved: false,
            lastKeepOpen: GLib.get_monotonic_time()};
        this._center.add_style_pseudo_class('active');
        this._dragSignal = global.stage.connect('captured-event', (_actor, event) => {
            if (event.type() === Clutter.EventType.MOTION) {
                this.dragTo(...event.get_coords());
                return Clutter.EVENT_STOP;
            }
            if (event.type() === Clutter.EventType.BUTTON_RELEASE && event.get_button() === 1) {
                this.endDrag();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._keepOpen();
    }

    dragTo(x, y) {
        if (!this._drag) return;
        const nextX = this._drag.originX + x - this._drag.x;
        const nextY = this._drag.originY + y - this._drag.y;
        this._position = positionFromOrigin(this._available, nextX, nextY, SIZE * this.scale_x);
        const geometry = menuGeometry(this._available, this._position, SIZE);
        this.set_position(geometry.x, geometry.y);
        this._drag.moved ||= Math.hypot(x - this._drag.x, y - this._drag.y) > 4;
        if (GLib.get_monotonic_time() - this._drag.lastKeepOpen > 1000000) {
            this._drag.lastKeepOpen = GLib.get_monotonic_time();
            this._keepOpen();
        }
    }

    endDrag() {
        if (!this._drag) return;
        const moved = this._drag.moved;
        this._drag = null;
        global.stage.disconnect(this._dragSignal);
        this._dragSignal = 0;
        this._center.remove_style_pseudo_class('active');
        if (moved) this._settings.set_value('menu-position', new GLib.Variant('(dd)', this._position));
        this._keepOpen();
    }

    disposeMenu() {
        this.cancelDrag();
        this._settings.disconnect(this._settingsSignal);
        this.destroy();
    }

    cancelDrag() {
        if (this._drag) this._position = this._settings.get_value('menu-position').deepUnpack();
        if (this._dragSignal) global.stage.disconnect(this._dragSignal);
        this._dragSignal = 0;
        this._drag = null;
        this._center.remove_style_pseudo_class('active');
    }
});
