# SPDX-License-Identifier: MIT
"""Launch an isolated, software-rendered GNOME session for this extension's tests."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

source=Path(__file__).resolve().parent
root=source/'headless-test'
root.mkdir(exist_ok=True)
for directory in ['config','data','runtime','cache']:(root/directory).mkdir(exist_ok=True)
(root/'runtime').chmod(0o700)
extension=root/'data/gnome-shell/extensions/asus-dial@local.cachyos'
if extension.exists():shutil.rmtree(extension)
shutil.copytree(source/'extension',extension)
subprocess.run(['glib-compile-schemas', str(extension/'schemas')], check=True)
# A capture method exists only in this private test copy, never in the installed extension.
script=(extension/'extension.js').read_text()
script=script.replace('<method name="GetContext">','<method name="TestSnapshot"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>\n <method name="TestDesktop"/>\n <method name="TestDrag"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>\n <method name="GetContext">')
probe='''    async TestSnapshotAsync(params, invocation) {
        const [path] = params;
        try {
            const stream = Gio.File.new_for_path(path).replace(null, false, Gio.FileCreateFlags.NONE, null);
            await new Shell.Screenshot().screenshot(false, stream);
            stream.close(null);
            invocation.return_value(new GLib.Variant('(s)', [JSON.stringify({modal: Boolean(this._grab), menu: this._menu.visible,
                hud: this._overlay.visible && this._hud.visible, stage: [global.stage.width, global.stage.height], menu_position: this._menu.get_position(), saved_position: this._menu._settings.get_value('menu-position').deepUnpack(), menu_size: [this._menu.width, this._menu.height], icons: this._menu._icons.map(i => i.icon_name),
                osd: Main.osdWindowManager._osdWindows.map(w => ({visible: w.visible, level: w._level.value,
                    icon: w._icon.gicon?.to_string(), label: w._label.visible ? w._label.text : null})),
                windows: global.display.get_tab_list(Meta.TabList.NORMAL_ALL, null).map(w => [w.get_wm_class(), w.get_title()]),
                overview: Main.overview.visible})]));
        } catch (error) { invocation.return_dbus_error('org.cachyos.AsusDial.TestError', error.message); }
    }

    TestDrag(dx, dy) { this._menu.beginDrag(this._menu.x + 180, this._menu.y + 180); this._menu.dragTo(this._menu.x + 180 + dx, this._menu.y + 180 + dy); this._menu.endDrag(); }

    TestDesktop() { Main.overview.hide(); global.workspace_manager.get_workspace_by_index(0).activate(global.get_current_time()); }

'''
script=script.replace('    disable() {',probe+'    disable() {')
(extension/'extension.js').write_text(script)
apps=root/'data/applications';apps.mkdir(exist_ok=True)
(apps/'asus-dial-settings.desktop').write_text('[Desktop Entry]\nName=ASUS Dial\nType=Application\nExec=/usr/bin/true\nStartupWMClass=org.cachyos.AsusDial.Settings\n')
for identifier,name in [('org.test.DialZen','Test Zen'),('org.test.DialBlender','Test Blender')]:
    (apps/(identifier+'.desktop')).write_text(f'[Desktop Entry]\nName={name}\nType=Application\nExec=/usr/bin/true\nStartupWMClass={identifier}\n')
environment=dict(os.environ,XDG_CONFIG_HOME=str(root/'config'),XDG_DATA_HOME=str(root/'data'),XDG_RUNTIME_DIR=str(root/'runtime'),
    XDG_CACHE_HOME=str(root/'cache'),LIBGL_ALWAYS_SOFTWARE='1',GDK_BACKEND='wayland',GTK_A11Y='none',GNOME_SHELL_SESSION_MODE='user')
for key in ['DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS']:environment.pop(key,None)
result=subprocess.run(['dbus-run-session','--','/usr/bin/python3',str(source/'integration_session.py')],env=environment,timeout=80)
sys.exit(result.returncode)
