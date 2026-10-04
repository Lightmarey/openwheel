# SPDX-License-Identifier: MIT
"""Event-driven ASUS Dial controller and session D-Bus configuration API."""
import concurrent.futures
import copy
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from gi.repository import Gio, GLib
import backends
from openwheel_input import OpenWheelInput
from core import Gestures, Profiles, atomic_save, default_config, migrate_config, validate_config

NAME='org.cachyos.AsusDial'
PATH='/org/cachyos/AsusDial'
SHELL_NAME=NAME+'.Shell'
SHELL_PATH=PATH+'/Shell'
XML='''<node><interface name="org.cachyos.AsusDial">
 <method name="GetState"><arg type="s" direction="out"/></method>
 <method name="GetConfig"><arg type="s" direction="out"/></method>
 <method name="SaveConfig"><arg type="s" direction="in"/><arg type="b" direction="out"/><arg type="s" direction="out"/></method>
 <method name="ReloadConfig"><arg type="b" direction="out"/><arg type="s" direction="out"/></method>
 <method name="UpdateContext"><arg type="s" direction="in"/></method>
 <method name="MenuMove"><arg type="i" direction="in"/></method>
 <method name="MenuHighlight"><arg type="i" direction="in"/></method>
 <method name="KeepMenuOpen"/>
 <method name="MenuActivate"/>
 <method name="MenuSelect"><arg type="i" direction="in"/></method>
 <method name="CancelMenu"/>
 <method name="SetEditing"><arg type="b" direction="in"/></method>
 <method name="GetMonitors"><arg type="s" direction="out"/></method>
 <method name="SelectFunction"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
 <method name="OpenSettings"/>
 <signal name="StateChanged"><arg type="s"/></signal>
</interface></node>'''

class DryInput:
    """Integration-test input; never creates uinput or sends desktop keys."""
    def __init__(self):self.pressed=set()
    def hold(self,name):self.pressed.add(name)
    def shortcut(self,text,count=1):pass
    def scroll(self,delta):pass
    def release_all(self):self.pressed.clear()
    def close(self):self.release_all()

class Controller:
    def __init__(self,config_dir=None,no_hardware=False):
        self.directory=Path(config_dir or Path.home()/'.config/asus-dial')
        self.directory.mkdir(parents=True,exist_ok=True)
        self.config_path=self.directory/'config.json';self.state_path=self.directory/'state.json'
        self.no_hardware=no_hardware;self.stopping=False
        if not self.config_path.exists():atomic_save(self.config_path,default_config())
        loaded=json.loads(self.config_path.read_text());self.config=migrate_config(loaded)
        if loaded.get('version')!=2:atomic_save(self.config_path,self.config)
        try:remembered=json.loads(self.state_path.read_text()).get('selected',{})
        except (OSError,ValueError,AttributeError):remembered={}
        if not isinstance(remembered,dict):remembered={}
        self.profiles=Profiles(self.config,remembered)
        self.gestures=Gestures(**self.gesture_options())
        self.connection=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        self.loop=GLib.MainLoop();self.sources={};self.hardware_ready=False;self.device_path=None
        self.input=DryInput() if no_hardware else None;self.audio=None
        self.bridge=False;self.locked=False;self.editing=False;self.settings_starting=False
        self.visible=False;self.mode='adjust';self.label='直接旋转调节，短按选功能';self.error='';self.osd=None;self.osd_serial=0
        self.menu_path=();self.menu_page=0;self.menu_index=0
        self.last_foreground={};self.audio_delta=0.;self.shortcut_pending=None;self.fallback_pending=False
        self.fallback_focus_until=0.;self.fallback_latched_wm=None
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=2,thread_name_prefix='dial-io')
        self.jobs={};self.job_epoch=0
        info=Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.registration=self.connection.register_object(PATH,info,self.on_method,None,None)
        self.name_id=Gio.bus_own_name_on_connection(self.connection,NAME,Gio.BusNameOwnerFlags.NONE,None,self.name_lost)
        self.shell_watch=Gio.bus_watch_name_on_connection(self.connection,SHELL_NAME,Gio.BusNameWatcherFlags.NONE,self.shell_appeared,self.shell_vanished)
        self.settings_watch=Gio.bus_watch_name_on_connection(self.connection,NAME+'.Settings',Gio.BusNameWatcherFlags.NONE,None,self.settings_vanished)
        self.lock_subscription=self.connection.signal_subscribe('org.gnome.ScreenSaver','org.gnome.ScreenSaver','ActiveChanged',
            '/org/gnome/ScreenSaver',None,Gio.DBusSignalFlags.NONE,self.lock_changed)
        try:self.locked=backends.call(self.connection,'org.gnome.ScreenSaver','/org/gnome/ScreenSaver','org.gnome.ScreenSaver','GetActive')[0]
        except GLib.Error:pass
        self.monitor=Gio.File.new_for_path(str(self.directory)).monitor_directory(Gio.FileMonitorFlags.NONE,None)
        self.monitor.connect('changed',self.config_changed)
        if not no_hardware:
            try:self.audio=backends.Audio()
            except (ImportError,ValueError,GLib.Error) as error:print('Gvc unavailable:',error,file=sys.stderr,flush=True)
            self.input=backends.Input()
        self.openwheel_input=OpenWheelInput(self.connection,self.feed_input,self.hardware_changed)
        self.signal_sources=[GLib.unix_signal_add(GLib.PRIORITY_DEFAULT,s,self.stop) for s in [signal.SIGTERM,signal.SIGINT]]

    def gesture_options(self):
        raw=self.config['input']
        return dict(hold=raw['long_press_seconds'],units=raw['units_per_step'],invert=raw.get('invert',False))

    def later(self,key,milliseconds,callback):
        self.cancel(key)
        def run():
            self.sources.pop(key,None)
            if not self.stopping:callback()
            return GLib.SOURCE_REMOVE
        self.sources[key]=GLib.timeout_add(max(1,round(milliseconds)),run)

    def cancel(self,key):
        source=self.sources.pop(key,None)
        if source:GLib.source_remove(source)

    def state(self):
        a=self.profiles.get_action()
        return dict(version=2,profile_id=self.profiles.current['id'],profile=self.profiles.current['name'],
            action_id=self.profiles.selected,action=a,context=self.profiles.context,last_foreground=self.last_foreground,
            mode=self.mode,visible=self.visible,label=self.label,error=self.error,menu=self.profiles.menu_items(self.menu_path,self.menu_page),
            menu_index=self.menu_index,menu_path=list(self.menu_path),bridge=self.bridge,locked=self.locked,
            editing=self.editing,device=self.device_path,hardware_ready=self.hardware_ready,no_hardware=self.no_hardware,osd=self.osd)

    def publish(self):
        if 'publish' not in self.sources:self.later('publish',33,self.emit_state)

    def emit_state(self):
        self.connection.emit_signal(None,PATH,NAME,'StateChanged',GLib.Variant('(s)',(json.dumps(self.state(),ensure_ascii=False),)))

    def show(self,label=None,error='',osd=None):
        if label is not None:self.label=label
        self.osd=None
        if osd and not error:
            self.osd_serial+=1;self.osd=dict(osd,serial=self.osd_serial)
        self.error=error;self.visible=not self.locked and not self.editing
        self.later('hide',self.config['ui']['menu_timeout' if self.mode=='menu' else 'hud_timeout']*1000,self.hide)
        self.publish()

    def hide(self):
        if self.mode=='menu':self.mode='adjust';self.menu_path=();self.menu_page=0
        self.visible=False;self.publish()

    def on_method(self,connection,sender,path,interface,method,parameters,invocation):
        try:
            args=parameters.unpack();result=None
            if method=='GetState':result=GLib.Variant('(s)',(json.dumps(self.state(),ensure_ascii=False),))
            elif method=='GetConfig':result=GLib.Variant('(s)',(json.dumps(self.config,ensure_ascii=False),))
            elif method=='SaveConfig':result=GLib.Variant('(bs)',self.save_config(args[0]))
            elif method=='ReloadConfig':result=GLib.Variant('(bs)',self.reload_config())
            elif method=='UpdateContext':self.update_context(json.loads(args[0]))
            elif method=='MenuMove':self.menu_move(args[0])
            elif method=='MenuHighlight':
                if self.mode=='menu' and 0<=args[0]<len(self.profiles.menu_items(self.menu_path,self.menu_page)):
                    self.menu_index=args[0];self.show()
            elif method=='KeepMenuOpen':
                if self.mode=='menu':self.later('hide',self.config['ui']['menu_timeout']*1000,self.hide)
            elif method=='MenuActivate':self.menu_activate()
            elif method=='MenuSelect':self.menu_index=args[0];self.menu_activate()
            elif method=='CancelMenu':self.close_menu()
            elif method=='SetEditing':self.set_editing(args[0])
            elif method=='GetMonitors':result=GLib.Variant('(s)',(json.dumps(backends.monitors(self.connection),ensure_ascii=False),))
            elif method=='SelectFunction':
                profile_id,identifier=args
                if self.profiles.current['id']!=profile_id:raise ValueError('请先切换到对应应用')
                self.select(identifier)
            elif method=='OpenSettings':self.open_settings()
            else:raise ValueError('未知方法')
            invocation.return_value(result)
        except Exception as error:invocation.return_dbus_error(NAME+'.Error',str(error))

    def name_lost(self,*args):
        print('Another controller owns the session bus name.',file=sys.stderr,flush=True);self.stop()

    def save_config(self,text):
        try:
            new=validate_config(json.loads(text));atomic_save(self.config_path,new);self.apply_config(new)
            return True,''
        except (ValueError,TypeError,KeyError,OSError) as error:return False,str(error)

    def reload_config(self):
        try:self.apply_config(validate_config(json.loads(self.config_path.read_text())));return True,''
        except (ValueError,TypeError,KeyError,OSError) as error:
            self.show('配置没有载入',str(error));print('Config retained:',error,file=sys.stderr,flush=True)
            return False,str(error)

    def apply_config(self,new):
        if new==self.config:return
        self.cancel_actions();remembered=self.profiles.remembered;context=self.profiles.context
        self.config=new;self.profiles=Profiles(new,remembered)
        self.profiles.update_context(dict(context,dial_focus=False));self.gestures=Gestures(**self.gesture_options())
        self.close_menu();self.publish()

    def config_changed(self,monitor,file,other,event):
        if any(f and f.get_basename()=='config.json' for f in [file,other]):self.later('config-reload',100,self.reload_config)

    def shell_appeared(self,*args):
        self.bridge=True;self.cancel_actions()
        try:self.update_context(json.loads(self.shell('GetContext')[0]))
        except (GLib.Error,ValueError) as error:print('Shell context:',error,file=sys.stderr,flush=True)
        self.publish()

    def shell_vanished(self,*args):
        self.bridge=False;self.cancel_actions();self.close_menu();self.publish()

    def shell(self,method,signature=None,args=()):
        return backends.call(self.connection,SHELL_NAME,SHELL_PATH,SHELL_NAME,method,signature,args,1000)

    def update_context(self,context):
        if not isinstance(context,dict):raise ValueError('窗口信息无效')
        self.set_locked(bool(context.get('locked',self.locked)))
        if context.get('app_id') in ['org.cachyos.AsusDial.Settings.desktop','asus-dial-settings.desktop'] or context.get('wm_class','').casefold()=='org.cachyos.asusdial.settings':
            self.set_editing(True);return
        # GTK owns the editor focus flag. An X11 GTK window can report generic WM_CLASS=python3;
        # don't mistake that for a different application and resume injection into the editor.
        if self.editing:return
        if context.get('app_id') or context.get('wm_class'):
            self.last_foreground=copy.deepcopy(context)
        changed=self.profiles.update_context(context)
        if changed:
            self.cancel_actions();self.close_menu()
            self.show(self.profiles.get_action()['name'] if self.profiles.get_action() else '请添加功能')
        self.publish()

    def fallback_context(self):
        if self.bridge:return
        try:
            wm=backends.call(self.connection,'org.gnome.Shell','/io/github/pwr_solaar/solaar','io.github.pwr_solaar.solaar','ActiveWindow',timeout=300)[0]
            caused=self.fallback_pending or time.monotonic()<self.fallback_focus_until
            if caused and wm:self.fallback_latched_wm=wm
            elif self.fallback_latched_wm==wm:caused=True
            else:self.fallback_latched_wm=None
            self.update_context(dict(wm_class=wm,locked=self.locked,dial_focus=caused))
        except GLib.Error:pass

    def lock_changed(self,connection,sender,path,interface,signal_name,parameters):self.set_locked(parameters.unpack()[0])

    def set_locked(self,locked):
        if self.locked==locked:return
        self.locked=locked
        if locked:self.cancel_actions();self.close_menu();self.gestures.reset()
        self.publish()

    def set_editing(self,editing):
        self.settings_starting=False
        if self.editing==editing:return
        self.editing=editing
        if editing:self.cancel_actions();self.close_menu();self.gestures.reset()
        else:self.later('editor-focus',100,self.refresh_context)
        self.publish()

    def settings_vanished(self,*args):
        if not self.settings_starting:self.set_editing(False)

    def refresh_context(self):
        if self.editing:return
        if self.bridge:
            try:self.update_context(json.loads(self.shell('GetContext')[0]))
            except (GLib.Error,ValueError):pass
        else:self.fallback_context()

    def open_settings(self):
        if self.locked:return
        self.set_editing(True);self.settings_starting=True
        try:subprocess.Popen([str(Path.home()/'.local/bin/asus-dial'),'--settings'],stdout=subprocess.DEVNULL)
        except OSError as error:self.set_editing(False);self.show('设置未能打开',str(error))
        self.later('settings-start',3000,lambda:self.set_editing(False) if self.settings_starting else None)

    def hardware_changed(self,ready):
        self.hardware_ready=ready;self.device_path='org.asus.dial' if ready else None
        self.gestures.reset();self.cancel('hold')
        if not ready:self.cancel_actions();self.close_menu()
        print('OpenWheel input '+('ready' if ready else 'waiting'),flush=True);self.publish()

    def feed_input(self,button,delta):
        try:
            self.fallback_context()
            was_down=self.gestures.down
            events=self.gestures.feed(button,delta,time.monotonic())
            if button and not was_down:self.later('hold',self.gestures.hold*1000,self.hold_tick)
            if not button or delta:self.cancel('hold')
            self.handle(events)
        except Exception as error:
            self.show('操作没有完成',str(error));print('OpenWheel input:',error,file=sys.stderr,flush=True)

    def hold_tick(self):self.handle(self.gestures.tick(time.monotonic()))

    def handle(self,events):
        for event,delta,fine in events:
            if self.editing:continue
            if self.locked:
                a=self.profiles.get_action()
                if event=='rotate' and a and a['kind'] in ['volume','brightness']:self.rotate(delta,fine)
                continue
            if event=='long':self.open_settings()
            elif event=='press':
                if self.mode=='menu':self.menu_activate()
                elif self.commit_switch():pass
                else:self.open_menu()
            elif self.mode=='menu':self.menu_move(delta)
            else:self.rotate(delta,fine)

    def open_menu(self):
        self.cancel_actions();self.mode='menu';self.menu_path=();self.menu_page=0
        self.menu_page,self.menu_index=self.profiles.menu_location()
        items=self.profiles.menu_items(page=self.menu_page)
        if len(items)==1 and items[0]['type']=='action':self.select(items[0]['id']);return
        self.show('旋转选择，再按确认')

    def menu_move(self,delta):
        if self.mode!='menu':return
        items=self.profiles.menu_items(self.menu_path,self.menu_page)
        if not items:return
        self.menu_index=(self.menu_index+delta)%len(items)
        self.show(items[self.menu_index]['name'])

    def menu_activate(self):
        if self.mode!='menu':return
        items=self.profiles.menu_items(self.menu_path,self.menu_page)
        if not 0<=self.menu_index<len(items):return
        item=items[self.menu_index]
        if item['type']=='action':self.select(item['id']);return
        if item['type']=='group':
            self.menu_path+=item['id'],;self.menu_page,self.menu_index=self.profiles.menu_location(self.menu_path)
        elif item['type']=='back':
            self.menu_path=self.menu_path[:-1];self.menu_page,self.menu_index=self.profiles.menu_location(self.menu_path)
        elif item['type']=='next':self.menu_page+=1;self.menu_index=0
        self.show('旋转选择，再按确认')

    def close_menu(self):
        self.mode='adjust';self.menu_path=();self.menu_page=0;self.hide()

    def select(self,identifier):
        self.cancel_actions();self.profiles.select(identifier);atomic_save(self.state_path,dict(selected=self.profiles.remembered))
        self.mode='adjust';self.show(self.profiles.get_action()['name'])

    def rotate(self,steps,fine):
        a=self.profiles.get_action()
        if not a:return
        amount=steps*a.get('fine_step' if fine else 'step',1);kind=a['kind']
        if kind not in ['volume','brightness']:self.show(a['name']+(' · 微调' if fine else ''))
        if self.no_hardware and kind not in ['apps','workspace']:
            self.show(f'{a["name"]} · 测试输入 {amount:+g}');return
        if kind=='volume':
            self.audio_delta+=amount
            if 'audio' not in self.sources:self.later('audio',35,self.adjust_audio)
        elif kind in ['brightness','mpris','mpv']:
            key=(kind,json.dumps(a.get('target',a.get('player','')),sort_keys=True),self.profiles.context.get('monitor',-1) if kind=='brightness' else '')
            job=self.jobs.setdefault(key,dict(pending=0,inflight=False,action=None,context=None,epoch=0))
            job.update(action=copy.deepcopy(a),context=copy.deepcopy(self.profiles.context),epoch=self.job_epoch)
            job['pending']+=amount
            source_key='job:'+str(key)
            if not job['inflight'] and source_key not in self.sources:self.later(source_key,100,lambda:self.start_job(key))
        elif kind=='scroll':self.input.scroll(round(amount))
        elif kind=='shortcut':
            key=a.get('cw' if amount>0 else 'ccw','')
            if not key:self.show(a['name'],'请在设置中录制两个方向的快捷键');return
            self.queue_shortcut(key,max(1,round(abs(amount))))
            self.show(a['name']+' · '+key)
        elif kind in ['apps','workspace']:
            if self.bridge:
                response=json.loads(self.shell('RunAction','(sib)',(kind,round(amount),fine))[0])
                self.show(response.get('label',a['name']),response.get('error',''))
            elif kind=='apps':
                self.input.hold('KEY_LEFTALT');self.fallback_pending=True
                self.input.shortcut('<Alt>Tab' if amount>0 else '<Alt><Shift>Tab',max(1,round(abs(amount))))
                self.later('fallback-commit',500,self.commit_switch)
            else:
                self.fallback_pending=True
                self.input.shortcut('<Control><Alt>Right' if amount>0 else '<Control><Alt>Left',max(1,round(abs(amount))))
                self.later('fallback-commit',500,self.commit_switch)

    def queue_shortcut(self,key,count):
        if self.shortcut_pending and self.shortcut_pending[0]==key:self.shortcut_pending[1]+=count
        else:self.shortcut_pending=[key,count]
        if 'shortcut' not in self.sources:self.later('shortcut',1,self.send_shortcuts)

    def send_shortcuts(self):
        if not self.shortcut_pending:return
        key,count=self.shortcut_pending;batch=min(16,count)
        try:self.input.shortcut(key,batch)
        except (ValueError,OSError,KeyError) as error:self.shortcut_pending=None;self.show('快捷键未能发送',str(error));return
        if count>batch:self.shortcut_pending[1]-=batch;self.later('shortcut',1,self.send_shortcuts)
        else:self.shortcut_pending=None

    def adjust_audio(self):
        delta=self.audio_delta;self.audio_delta=0
        try:
            if self.audio:
                result=self.audio.adjust(delta,self.config.get('audio_max_percent',100));self.show(result['label'],osd=result['osd'])
            else:raise ValueError('音量接口尚未就绪')
        except (ValueError,GLib.Error) as error:self.show('音量没有改变',str(error))

    def start_job(self,key):
        job=self.jobs[key]
        if job['inflight'] or not job['pending']:return
        delta=job['pending'];job['pending']=0;job['inflight']=True
        a,context,epoch=job['action'],job['context'],job['epoch'];config=copy.deepcopy(self.config)
        def execute():
            if epoch!=self.job_epoch or self.stopping:return a['name']
            if a['kind']=='brightness':return backends.brightness(self.connection,a,delta,context)
            if a['kind']=='mpris':return backends.media(self.connection,a,delta,context)
            return backends.mpv(a,delta,config)
        future=self.executor.submit(execute)
        def done(f):
            try:result=f.result();error=''
            except Exception as issue:result=a['name'];error=str(issue)
            GLib.idle_add(self.finish_job,key,epoch,a['id'],result,error)
        future.add_done_callback(done)

    def finish_job(self,key,epoch,identifier,result,error):
        job=self.jobs.get(key)
        if job is None or self.stopping:return GLib.SOURCE_REMOVE
        job['inflight']=False
        if epoch==self.job_epoch and identifier==self.profiles.selected and self.mode!='menu' and not self.editing:
            if isinstance(result,dict):self.show(result['label'],error,osd=result.get('osd'))
            else:self.show(result,error)
        if job['pending']:self.later('job:'+str(key),100,lambda:self.start_job(key))
        return GLib.SOURCE_REMOVE

    def commit_switch(self):
        if self.bridge:
            try:result=json.loads(self.shell('CommitSwitch')[0]);return result.get('committed',False)
            except (GLib.Error,ValueError):return False
        if not self.fallback_pending:return False
        self.cancel('fallback-commit')
        if self.input:self.input.release_all()
        # Alt release commits asynchronously. Read once after that focus change, without idle polling.
        self.fallback_focus_until=time.monotonic()+.35
        self.fallback_context();self.fallback_pending=False
        self.later('fallback-focus',80,self.fallback_context);return True

    def cancel_actions(self):
        self.job_epoch+=1;self.audio_delta=0.;self.shortcut_pending=None
        for key in list(self.sources):
            if key in ['audio','shortcut','fallback-commit','fallback-focus'] or key.startswith('job:'):self.cancel(key)
        for job in self.jobs.values():job['pending']=0
        self.fallback_pending=False;self.fallback_focus_until=0.;self.fallback_latched_wm=None
        if self.input:
            try:self.input.release_all()
            except OSError:pass
        if self.bridge:
            try:self.shell('CancelSwitch')
            except GLib.Error:pass

    def stop(self,*args):
        if self.stopping:return GLib.SOURCE_REMOVE
        self.stopping=True;self.cancel_actions()
        self.openwheel_input.close()
        for key in list(self.sources):self.cancel(key)
        self.monitor.cancel();self.connection.signal_unsubscribe(self.lock_subscription)
        Gio.bus_unwatch_name(self.shell_watch);Gio.bus_unwatch_name(self.settings_watch);Gio.bus_unown_name(self.name_id)
        self.connection.unregister_object(self.registration)
        if self.input:self.input.close()
        if self.audio:self.audio.close()
        self.executor.shutdown(wait=False,cancel_futures=True);self.loop.quit()
        return GLib.SOURCE_REMOVE

    def run(self):
        try:self.loop.run()
        finally:self.stop()
