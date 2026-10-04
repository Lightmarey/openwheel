#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Restore the user files saved by install-user.py."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from gi.repository import Gio

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('backup',type=Path)
args=parser.parse_args()
manifest=json.loads((args.backup/'manifest.json').read_text())
subprocess.run(['systemctl','--user','stop','asus-dial.service','openwheel-input.service'],check=True)
for name,status in manifest['services'].items():
    if not status['enabled']:subprocess.run(['systemctl','--user','disable',name],check=True)
for item in manifest['files']:
    path=Path(item['path']);stored=args.backup/'files'/path.relative_to('/')
    # Keep any edits made after installation before restoring the older files.
    if path.exists():
        recent=args.backup/'before-rollback'/path.relative_to('/')
        recent.parent.mkdir(parents=True,exist_ok=True)
        if path.is_dir():shutil.copytree(path,recent);shutil.rmtree(path)
        else:shutil.copy2(path,recent);path.unlink()
    if item['existed']:
        path.parent.mkdir(parents=True,exist_ok=True)
        if stored.is_dir():shutil.copytree(stored,path)
        else:shutil.copy2(stored,path)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
for name,status in manifest['services'].items():
    if status['enabled']:subprocess.run(['systemctl','--user','enable',name],check=True)
    if status['active']:subprocess.run(['systemctl','--user','start',name],check=True)
settings=Gio.Settings.new('org.gnome.shell')
uuid='asus-dial@local.cachyos'
if uuid not in manifest['enabled_extensions']:
    settings.set_strv('enabled-extensions',[u for u in settings.get_strv('enabled-extensions') if u!=uuid]);Gio.Settings.sync()
print('Dial files restored. Log in again to reload GNOME extension code.')
