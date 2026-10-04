#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Install the optional GNOME frontend and a built OpenWheel input daemon."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
from gi.repository import Gio

source = Path(__file__).resolve().parent
home = Path.home()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input-binary', type=Path, required=True)
parser.add_argument('--backup-dir', type=Path)
args = parser.parse_args()
binary = args.input_binary.resolve(strict=True)
uuid = json.loads((source/'extension/metadata.json').read_text())['uuid']
library = home/'.local/lib/asus-dial'
extension = home/'.local/share/gnome-shell/extensions'/uuid
paths = [library, extension, home/'.local/bin/asus-dial',
    home/'.local/libexec/openwheel/asus_wheel', home/'.config/systemd/user/asus-dial.service',
    home/'.config/systemd/user/openwheel-input.service', home/'.local/share/applications/asus-dial-settings.desktop',
    home/'.config/asus-dial/config.json', home/'.config/asus-dial/state.json']
backup = args.backup_dir or home/'.cache/openwheel/backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
backup.mkdir(parents=True, mode=0o700)
records = []
for path in paths:
    records.append(dict(path=str(path), existed=path.exists()))
    if not path.exists():
        continue
    saved = backup/'files'/path.relative_to('/')
    saved.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir(): shutil.copytree(path, saved)
    else: shutil.copy2(path, saved)

def service_state(unit, verb):
    return subprocess.run(['systemctl','--user',verb,'--quiet',unit]).returncode == 0

settings = Gio.Settings.new('org.gnome.shell')
manifest = dict(created_at=datetime.now().astimezone().isoformat(), files=records,
    enabled_extensions=list(settings.get_strv('enabled-extensions')),
    services={name:dict(active=service_state(name,'is-active'),enabled=service_state(name,'is-enabled'))
        for name in ['asus-dial.service','openwheel-input.service']})
(backup/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
# Stop the old raw reader before starting OpenWheel, so input is handled once.
subprocess.run(['systemctl','--user','stop','asus-dial.service'], check=True)
library.mkdir(parents=True, exist_ok=True)
for name in ['main.py','core.py','service.py','backends.py','settings.py','openwheel_input.py']:
    shutil.copy2(source/name,library/name)
extension.mkdir(parents=True, exist_ok=True)
for path in (source/'extension').rglob('*'):
    if not path.is_file() or path.name == 'gschemas.compiled': continue
    dest = extension/path.relative_to(source/'extension')
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path,dest)
subprocess.run(['glib-compile-schemas',str(extension/'schemas')],check=True)

for dest, origin, mode in [
    (home/'.local/bin/asus-dial', source/'asus-dial-wrapper',0o755),
    (home/'.local/libexec/openwheel/asus_wheel',binary,0o755),
    (home/'.config/systemd/user/asus-dial.service',source/'asus-dial.service',0o644),
    (home/'.config/systemd/user/openwheel-input.service',source/'openwheel-input.service',0o644)]:
    dest.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(origin,dest);dest.chmod(mode)
desktop = home/'.local/share/applications/asus-dial-settings.desktop'
desktop.parent.mkdir(parents=True,exist_ok=True)
desktop.write_text((source/'asus-dial-settings.desktop').read_text().replace('Exec=asus-dial --settings',f'Exec={home}/.local/bin/asus-dial --settings'))
subprocess.run(['desktop-file-validate',str(desktop)],check=True)
enabled=list(settings.get_strv('enabled-extensions'))
if uuid not in enabled:
    settings.set_strv('enabled-extensions',enabled+[uuid]);Gio.Settings.sync()
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','enable','asus-dial.service','openwheel-input.service'],check=True)
subprocess.run(['systemctl','--user','restart','openwheel-input.service'],check=True)
subprocess.run(['systemctl','--user','start','asus-dial.service'],check=True)
print(json.dumps(dict(backup=str(backup),input_service='org.asus.dial',extension_version=3,
    native_menu='loads after the next normal GNOME login'),ensure_ascii=False,indent=2))
