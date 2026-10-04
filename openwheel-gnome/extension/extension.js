// SPDX-License-Identifier: MIT
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {RadialMenu} from './radialMenu.js';

const CONTROLLER = 'org.cachyos.AsusDial';
const PATH = '/org/cachyos/AsusDial';
const NAME = `${CONTROLLER}.Shell`;
const SHELL_PATH = `${PATH}/Shell`;
const XML = `<node><interface name="${NAME}">
 <method name="GetContext"><arg type="s" direction="out"/></method>
 <method name="RunAction"><arg type="s" direction="in"/><arg type="i" direction="in"/><arg type="b" direction="in"/><arg type="s" direction="out"/></method>
 <method name="CommitSwitch"><arg type="s" direction="out"/></method>
 <method name="CancelSwitch"/>
</interface></node>`;
const ICONS = {volume: 'audio-volume-high-symbolic', brightness: 'display-brightness-symbolic',
    apps: 'view-grid-symbolic', workspace: 'view-paged-symbolic', scroll: 'input-mouse-symbolic',
    shortcut: 'input-keyboard-symbolic', mpris: 'media-playback-start-symbolic', mpv: 'media-playback-start-symbolic'};

export default class AsusDialExtension extends Extension {
    enable() {
        this._connections = [];
        this._controllerReady = false;
        this._osdSerial = 0;
        this._switchTimer = 0;
        this._focusTimer = 0;
        this._expectedWindow = null;
        this._selfFocusUntil = 0;
        this._lastWindow = global.display.focus_window;
        this._tracker = Shell.WindowTracker.get_default();
        this._overlay = new St.Widget({layout_manager: new Clutter.FixedLayout(), reactive: false, visible: false});
        Main.layoutManager.addTopChrome(this._overlay);
        this._overlay.connect('key-press-event', (_actor, event) => this._key(event));
        this._overlay.connect('button-press-event', (_actor, event) => {
            if (event.get_source() !== this._overlay) return Clutter.EVENT_PROPAGATE;
            this._call('CancelMenu');
            return Clutter.EVENT_STOP;
        });
        this._hud = new St.BoxLayout({style_class: 'osd-window asus-dial-hud', reactive: false});
        this._icon = new St.Icon({icon_name: 'input-dialpad-symbolic', icon_size: 32});
        this._hud.add_child(this._icon);
        const text = new St.BoxLayout({vertical: true, style_class: 'asus-dial-text'});
        this._heading = new St.Label({style_class: 'asus-dial-heading'});
        this._value = new St.Label();
        this._profile = new St.Label({style_class: 'asus-dial-caption'});
        for (const label of [this._heading, this._value, this._profile]) {
            label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            text.add_child(label);
        }
        this._hud.add_child(text);
        this._overlay.add_child(this._hud);
        this._menu = new RadialMenu(this.getSettings(),
            index => this._call('MenuSelect', new GLib.Variant('(i)', [index])),
            index => this._call('MenuHighlight', new GLib.Variant('(i)', [index])),
            () => this._call('KeepMenuOpen'));
        this._overlay.add_child(this._menu);
        this._object = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._object.export(Gio.DBus.session, SHELL_PATH);
        this._nameId = Gio.bus_own_name_on_connection(Gio.DBus.session, NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._stateSubscription = Gio.DBus.session.signal_subscribe(CONTROLLER, CONTROLLER, 'StateChanged', PATH,
            null, Gio.DBusSignalFlags.NONE, (_bus, _sender, _path, _iface, _signal, parameters) => {
                try { this._render(JSON.parse(parameters.deepUnpack()[0])); } catch (error) { console.error(error); }
            });
        this._watch = Gio.bus_watch_name_on_connection(Gio.DBus.session, CONTROLLER, Gio.BusNameWatcherFlags.NONE,
            () => {
                this._controllerReady = true;
                this._call('GetState', null, result => this._render(JSON.parse(result[0])));
                this._pushContext();
            }, () => { this._controllerReady = false; this._osdSerial = 0; this._hide(); this.CancelSwitch(); });
        this._connect(global.display, 'notify::focus-window', () => this._queueFocus());
        this._connect(global.display, 'window-entered-monitor', () => this._queueFocus());
        this._connect(global.display, 'window-left-monitor', () => this._queueFocus());
        this._connect(Main.layoutManager, 'monitors-changed', () => this._queueFocus());
        this._connect(Main.sessionMode, 'updated', () => {
            if (Main.sessionMode.isLocked) { this._hide(); this.CancelSwitch(); }
            this._queueFocus();
        });
    }

    _connect(object, name, callback) { this._connections.push([object, object.connect(name, callback)]); }

    _call(method, parameters = null, callback = null) {
        if (!this._controllerReady) return;
        Gio.DBus.session.call(CONTROLLER, PATH, CONTROLLER, method, parameters, null,
            Gio.DBusCallFlags.NONE, 1500, null, (connection, result) => {
                try {
                    const reply = connection.call_finish(result)?.deepUnpack();
                    callback?.(reply);
                } catch (error) {
                    if (this._controllerReady) console.error(`ASUS Dial ${method}: ${error.message}`);
                }
            });
    }

    GetContext() { return JSON.stringify(this._context()); }

    _context() {
        let window = global.display.focus_window;
        if (window) this._lastWindow = window;
        else if (this._grab) window = this._lastWindow;
        const app = window ? this._tracker.get_window_app(window) : null;
        const [px, py] = global.get_pointer();
        let index = window ? window.get_monitor() : Main.layoutManager.monitors.findIndex(m =>
            px >= m.x && py >= m.y && px < m.x + m.width && py < m.y + m.height);
        if (index < 0 || !Main.layoutManager.monitors[index]) index = Main.layoutManager.primaryIndex;
        const m = Main.layoutManager.monitors[index];
        return {app_id: app?.get_id() ?? '', wm_class: window?.get_wm_class() ?? '',
            window_id: window?.get_id() ?? null, monitor: index,
            monitor_rect: m ? {x: m.x, y: m.y, width: m.width, height: m.height, scale: m.geometryScale} : {},
            locked: Boolean(Main.sessionMode.isLocked),
            dial_focus: Boolean((window && window === this._expectedWindow) || GLib.get_monotonic_time() < this._selfFocusUntil)};
    }

    _queueFocus() {
        if (this._focusTimer) return;
        this._focusTimer = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._focusTimer = 0;
            const window = global.display.focus_window;
            if (this._expectedWindow && window && window !== this._expectedWindow) this._expectedWindow = null;
            this._pushContext();
            return GLib.SOURCE_REMOVE;
        });
    }

    _pushContext() { this._call('UpdateContext', new GLib.Variant('(s)', [this.GetContext()])); }

    RunAction(kind, delta, _fine) {
        if (Main.sessionMode.isLocked) return JSON.stringify({error: '桌面已锁定'});
        if (kind === 'workspace') {
            const manager = global.workspace_manager;
            const index = Math.max(0, Math.min(manager.n_workspaces - 1, manager.get_active_workspace_index() + delta));
            this._selfFocusUntil = GLib.get_monotonic_time() + 700000;
            manager.get_workspace_by_index(index).activate(global.get_current_time());
            this._pushContext();
            return JSON.stringify({label: `虚拟桌面 · ${index + 1} / ${manager.n_workspaces}`});
        }
        if (kind !== 'apps') return JSON.stringify({error: '未知桌面功能'});
        if (!this._switchWindows) {
            const windows = global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null);
            const seen = new Set();
            this._switchWindows = windows.filter(window => {
                const app = this._tracker.get_window_app(window);
                const id = app?.get_id() ?? window.get_wm_class() ?? String(window.get_id());
                if (seen.has(id)) return false;
                seen.add(id);
                return true;
            });
            this._switchIndex = Math.max(0, this._switchWindows.indexOf(global.display.focus_window));
        }
        if (!this._switchWindows.length) { this.CancelSwitch(); return JSON.stringify({error: '没有可切换的应用'}); }
        const length = this._switchWindows.length;
        this._switchIndex = ((this._switchIndex + delta) % length + length) % length;
        const target = this._switchWindows[this._switchIndex];
        const app = this._tracker.get_window_app(target);
        this._preview(target);
        if (this._switchTimer) GLib.source_remove(this._switchTimer);
        this._switchTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 500, () => {
            this._switchTimer = 0;
            this.CommitSwitch();
            return GLib.SOURCE_REMOVE;
        });
        return JSON.stringify({label: `应用切换 · ${app?.get_name() ?? target.get_title()}`});
    }

    _preview(window) {
        this._previewActor?.destroy();
        this._previewActor = null;
        const actor = window.get_compositor_private();
        if (!actor || !actor.width || !actor.height) return;
        const scale = Math.min(320 / actor.width, 170 / actor.height);
        this._previewActor = new Clutter.Clone({source: actor, width: actor.width * scale, height: actor.height * scale});
        this._overlay.add_child(this._previewActor);
        this._position();
    }

    CommitSwitch() {
        if (!this._switchWindows) return JSON.stringify({committed: false});
        const target = this._switchWindows[this._switchIndex];
        this.CancelSwitch();
        if (!target || !target.get_compositor_private()) return JSON.stringify({committed: false});
        this._expectedWindow = target;
        Main.activateWindow(target, global.get_current_time());
        this._pushContext();
        return JSON.stringify({committed: true});
    }

    CancelSwitch() {
        if (this._switchTimer) GLib.source_remove(this._switchTimer);
        this._switchTimer = 0;
        this._switchWindows = null;
        this._previewActor?.destroy();
        this._previewActor = null;
    }

    _key(event) {
        if (!this._grab) return Clutter.EVENT_PROPAGATE;
        const key = event.get_key_symbol();
        if (key === Clutter.KEY_Escape) this._call('CancelMenu');
        else if ([Clutter.KEY_Left, Clutter.KEY_Up].includes(key)) this._call('MenuMove', new GLib.Variant('(i)', [-1]));
        else if ([Clutter.KEY_Right, Clutter.KEY_Down].includes(key)) this._call('MenuMove', new GLib.Variant('(i)', [1]));
        else if ([Clutter.KEY_Return, Clutter.KEY_KP_Enter, Clutter.KEY_space].includes(key)) this._call('MenuActivate');
        return Clutter.EVENT_STOP;
    }

    _render(state) {
        this._state = state;
        if (!state.visible || state.editing || state.locked || Main.sessionMode.isLocked) { this._hide(); return; }
        if (state.mode !== 'menu' && ['volume', 'brightness'].includes(state.action?.kind) && !state.error) {
            this._hide();
            const osd = state.osd;
            if (osd && osd.serial !== this._osdSerial) {
                this._osdSerial = osd.serial;
                const icon = new Gio.ThemedIcon({name: osd.icon});
                if (osd.kind === 'brightness') {
                    const monitor = global.backend.get_monitor_manager().get_monitor_for_connector(osd.connector);
                    if (monitor >= 0) Main.osdWindowManager.showOne(monitor, icon, osd.label, osd.level, osd.max_level);
                } else {
                    Main.osdWindowManager.showAll(icon, null, osd.level, osd.max_level);
                }
            }
            return;
        }
        this._overlay.set_size(global.stage.width, global.stage.height);
        this._overlay.show();
        this._heading.text = state.action?.name ?? 'ASUS Dial';
        this._value.text = state.error || state.label || '';
        this._profile.text = state.profile || '';
        this._icon.icon_name = ICONS[state.action?.kind] ?? 'input-keyboard-symbolic';
        this._hud.visible = state.mode !== 'menu';
        this._menu.visible = state.mode === 'menu';
        this._overlay.reactive = state.mode === 'menu';
        if (state.mode === 'menu') {
            this.CancelSwitch();
            this._menu.update(state.menu, state.menu_index, state.profile, state.menu_path ?? []);
            if (!this._grab) this._grab = Main.pushModal(this._overlay);
        } else this._releaseModal();
        this._position();
    }

    _position() {
        const index = this._state?.context?.monitor ?? global.display.focus_window?.get_monitor() ?? Main.layoutManager.primaryIndex;
        const monitor = Main.layoutManager.monitors[index] ?? Main.layoutManager.primaryMonitor;
        if (!monitor) return;
        const available = global.workspace_manager.get_active_workspace().get_work_area_for_monitor(monitor.index);
        const width = Math.min(480, available.width - 32);
        this._hud.width = width;
        this._hud.set_position(available.x + (available.width - width) / 2, available.y + available.height - 155);
        this._menu.place(available);
        if (this._previewActor) this._previewActor.set_position(available.x + (available.width - this._previewActor.width) / 2,
            available.y + available.height - 175 - this._previewActor.height);
    }

    _releaseModal() {
        if (this._grab) { Main.popModal(this._grab); this._grab = null; }
    }

    _hide() {
        this._menu?.cancelDrag();
        this._releaseModal();
        this._overlay?.hide();
        if (this._overlay) this._overlay.reactive = false;
        this._previewActor?.hide();
    }

    disable() {
        this._controllerReady = false;
        this._hide();
        this.CancelSwitch();
        if (this._focusTimer) GLib.source_remove(this._focusTimer);
        this._focusTimer = 0;
        for (const [object, id] of this._connections) object.disconnect(id);
        this._connections = [];
        Gio.bus_unwatch_name(this._watch);
        Gio.DBus.session.signal_unsubscribe(this._stateSubscription);
        Gio.bus_unown_name(this._nameId);
        this._object.unexport();
        this._object = null;
        this._menu.disposeMenu();
        this._menu = null;
        this._overlay.destroy();
        this._overlay = null;
        this._state = null;
    }
}
