// SPDX-License-Identifier: MIT
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class AsusDialPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const page = new Adw.PreferencesPage({title: 'ASUS Dial'});
        const settings = this.getSettings();
        const position = new Adw.PreferencesGroup({title: '圆环位置', description: '菜单默认在右下方；打开后按住圆心拖动，会自动记住位置。'});
        const presets = [[0.92, 0.82], [0.08, 0.82], [0.92, 0.08], [0.08, 0.08]];
        const choices = new Adw.ComboRow({title: '快速定位', model: Gtk.StringList.new(['右下', '左下', '右上', '左上', '自定义（拖动圆心）'])});
        const sync = () => {
            const value = settings.get_value('menu-position').deepUnpack();
            const index = presets.findIndex(p => p.every((v, i) => Math.abs(value[i] - v) < 0.001));
            choices.selected = index < 0 ? 4 : index;
        };
        sync();
        choices.connect('notify::selected', () => {
            if (choices.selected < presets.length)
                settings.set_value('menu-position', new GLib.Variant('(dd)', presets[choices.selected]));
        });
        const id = settings.connect('changed::menu-position', sync);
        window.connect('close-request', () => { settings.disconnect(id); return false; });
        position.add(choices); page.add(position);
        const group = new Adw.PreferencesGroup({title: '应用与功能'});
        const row = new Adw.ActionRow({title: '打开 ASUS Dial 设置', subtitle: '添加应用、录制快捷键、选择显示器及调整步长'});
        const button = new Gtk.Button({label: '打开设置', valign: Gtk.Align.CENTER});
        button.connect('clicked', () => Gio.Subprocess.new([`${GLib.get_home_dir()}/.local/bin/asus-dial`, '--settings'], Gio.SubprocessFlags.NONE));
        row.add_suffix(button);
        group.add(row); page.add(group); window.add(page);
    }
}
