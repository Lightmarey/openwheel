#!/usr/bin/python3
# SPDX-License-Identifier: MIT
import argparse
import json
import os
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description='ASUS Dial Controller')
    parser.add_argument('--settings',action='store_true')
    parser.add_argument('--check',action='store_true',help='检查配置与权限，不执行动作')
    parser.add_argument('--no-hardware',action='store_true',help='独立集成测试：不读取设备、不创建虚拟输入')
    parser.add_argument('--config-dir',help='独立测试或其他配置目录')
    args=parser.parse_args()
    if args.settings:
        from settings import Settings
        Settings(args.config_dir).run([])
    elif args.check:
        from core import migrate_config
        from gi.repository import Gio
        from backends import call
        path=Path(args.config_dir or Path.home()/'.config/asus-dial')/'config.json'
        config=migrate_config(json.loads(path.read_text()))
        connected=call(Gio.bus_get_sync(Gio.BusType.SESSION,None),'org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','NameHasOwner','(s)',('org.asus.dial',))[0]
        print(json.dumps(dict(config_valid=True,version=config['version'],input_service='org.asus.dial',openwheel_running=connected,
            uinput_writable=os.access('/dev/uinput',os.W_OK),
            actions=len(config['actions']),profiles=len(config['profiles']),report_ids=config['input']['report_ids']),ensure_ascii=False,indent=2))
    else:
        from service import Controller
        Controller(args.config_dir,args.no_hardware).run()

if __name__=='__main__':main()
