# SPDX-License-Identifier: MIT
"""Tests run only within run_headless.py's private D-Bus and Wayland session."""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from gi.repository import Gio,GLib
from core import Profiles,atomic_save,default_config
from service import Controller,NAME,PATH,SHELL_NAME,SHELL_PATH
from backends import call

source=Path(__file__).resolve().parent;root=source/'headless-test'
assert os.environ['XDG_RUNTIME_DIR']==str(root/'runtime'), 'Never run tests against the live desktop'
subprocess.run(['gsettings','set','org.gnome.shell','enabled-extensions',"['asus-dial@local.cachyos']"],check=True)
subprocess.run(['gsettings','set','org.gnome.shell','disable-user-extensions','false'],check=True)
subprocess.run(['gsettings','set','org.gnome.shell','welcome-dialog-last-shown-version',"'50.5'"],check=True)
subprocess.run(['gsettings','--schemadir',str(root/'data/gnome-shell/extensions/asus-dial@local.cachyos/schemas'),
    'reset','org.openwheel.gnome','menu-position'],check=True)
subprocess.run(['gsettings','set','org.gnome.desktop.media-handling','automount','false'],check=True)
subprocess.run(['gsettings','set','org.gnome.desktop.media-handling','automount-open','false'],check=True)
subprocess.run(['gsettings','set','org.gnome.desktop.media-handling','autorun-never','true'],check=True)
log=open(root/'shell.log','w')
shell=subprocess.Popen(['gnome-shell','--headless','--wayland','--no-x11','--virtual-monitor','1280x800',
    '--wayland-display','dial-test'],stdout=log,stderr=log,start_new_session=True)
os.environ['WAYLAND_DISPLAY']='dial-test'
controller=None;app=None;clients=[]
results={}

def pump(seconds=.15):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        while GLib.MainContext.default().pending():GLib.MainContext.default().iteration(False)
        time.sleep(.005)

def own_call(method,signature=None,args=()):
    output=[]
    parameters=GLib.Variant(signature,args) if signature else None
    def done(connection,result):
        try:output.append(connection.call_finish(result).unpack())
        except Exception as error:output.append(error)
    controller.connection.call(NAME,PATH,NAME,method,parameters,None,Gio.DBusCallFlags.NONE,1000,None,done)
    deadline=time.monotonic()+3
    while not output and time.monotonic()<deadline:pump(.02)
    if not output:raise RuntimeError('Own D-Bus API timed out')
    if isinstance(output[0],Exception):raise output[0]
    return output[0]

def snapshot(filename):
    return json.loads(call(bus,SHELL_NAME,SHELL_PATH,SHELL_NAME,'TestSnapshot','(s)',(str(root/filename),))[0])

def test_client(identifier):
    script='''import gi,sys
gi.require_version('Gtk','4.0')
from gi.repository import Gtk
app=Gtk.Application(application_id=sys.argv[1])
def activate(app):
    window=Gtk.ApplicationWindow(application=app,title=sys.argv[1],default_width=600,default_height=400)
    window.set_child(Gtk.Label(label='Private ASUS Dial focus test'));window.present()
app.connect('activate',activate);app.run([])
'''
    with open(root/(identifier+'.log'),'w') as output:
        client=subprocess.Popen(['/usr/bin/python3','-c',script,identifier],stdout=output,stderr=output)
    clients.append(client);pump(1)
    assert client.poll() is None,(identifier,(root/(identifier+'.log')).read_text())

try:
    bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if shell.poll() is not None:raise RuntimeError('Headless shell failed to start; see shell.log')
        try:
            context=json.loads(call(bus,SHELL_NAME,SHELL_PATH,SHELL_NAME,'GetContext',timeout=250)[0]);break
        except GLib.Error:time.sleep(.15)
    else:raise RuntimeError('Extension bridge did not load')
    results['extension_context']=context
    config_dir=root/'dial';config_dir.mkdir(exist_ok=True)
    initial=default_config()
    for p,identifier in [('zen','org.test.DialZen'),('blender','org.test.DialBlender')]:
        profile=next(item for item in initial['profiles'] if item['id']==p)
        profile['match']['desktop_ids'].append(identifier+'.desktop');profile['match']['wm_classes'].append(identifier)
    atomic_save(config_dir/'config.json',initial)
    if (config_dir/'state.json').exists():(config_dir/'state.json').unlink()
    controller=Controller(config_dir,no_hardware=True);pump(.5)
    assert controller.bridge
    results['dbus_get_state']=json.loads(own_call('GetState')[0])
    input_owner=Gio.bus_own_name_on_connection(bus,'org.asus.dial',Gio.BusNameOwnerFlags.NONE,None,None);pump(.2)
    assert controller.hardware_ready
    def emit_input(signal,value):
        bus.emit_signal(None,'/org/asus/dial','org.asus.dial',signal,GLib.Variant('(i)',(value,)));pump(.08)
    emit_input('Rotate',1)
    assert controller.visible and controller.profiles.selected=='volume'
    emit_input('Press',1);emit_input('Rotate',1);emit_input('Press',0)
    assert controller.mode=='adjust' and not controller.gestures.down
    emit_input('Press',1);emit_input('Press',0)
    assert controller.mode=='menu'
    controller.close_menu();Gio.bus_unown_name(input_owner);pump(.2)
    assert not controller.hardware_ready
    results['existing_openwheel_bus_input_direct_fine_press_and_disconnect']=True
    controller.show('测试输出 · 42%',osd=dict(kind='volume',icon='audio-volume-medium-symbolic',level=.42,max_level=1));pump(.2)
    osd=snapshot('volume-osd.png')
    assert not osd['hud'] and osd['osd'][0]['visible'] and abs(osd['osd'][0]['level']-.42)<.005,osd
    assert osd['osd'][0]['label'] is None and osd['osd'][0]['icon']=='audio-volume-medium-symbolic'
    controller.publish();pump(1.6)
    assert not snapshot('volume-osd-expired.png')['osd'][0]['visible'],'Context updates must not keep refreshing an old OSD'
    controller.select('brightness')
    physical=call(bus,'org.gnome.Mutter.DisplayConfig','/org/gnome/Mutter/DisplayConfig','org.gnome.Mutter.DisplayConfig','GetCurrentState')[1]
    connector=physical[0][0][0]
    controller.show('测试屏幕 · 67%',osd=dict(kind='brightness',icon='display-brightness-symbolic',level=.67,max_level=1,connector=connector,label='测试屏幕'));pump(.2)
    osd=snapshot('brightness-osd.png')
    assert not osd['hud'] and osd['osd'][0]['visible'] and abs(osd['osd'][0]['level']-.67)<.005,osd
    assert osd['osd'][0]['label']=='测试屏幕'
    results['stock_volume_and_brightness_osd']=True
    controller.select('volume');pump(1.6)
    controller.handle([('rotate',1,False)]);pump()
    assert controller.visible and controller.profiles.selected=='volume'
    controller.handle([('press',0,False)]);pump()
    assert controller.mode=='menu' and len(controller.state()['menu'])==5
    call(bus,SHELL_NAME,SHELL_PATH,SHELL_NAME,'TestDesktop');pump(.2)
    original_menu=snapshot('menu.png')
    assert original_menu['modal'] and original_menu['menu_size']==[360,360]
    assert original_menu['menu_position'][0]>700,original_menu
    assert original_menu['icons'][0]=='audio-volume-high-symbolic',original_menu
    call(bus,SHELL_NAME,SHELL_PATH,SHELL_NAME,'TestDrag','(ii)',(-550,-220));pump(.2)
    moved_menu=snapshot('menu-moved.png')
    assert moved_menu['menu_position'][0]<original_menu['menu_position'][0]-400,moved_menu
    controller.close_menu();pump(.1);controller.open_menu();pump(.2)
    reopened=snapshot('menu-position-retained.png')
    assert reopened['saved_position']==moved_menu['saved_position']
    assert reopened['menu_position']==moved_menu['menu_position']
    results['radial_menu_drag_and_persistence']=dict(before=original_menu['menu_position'],after=moved_menu['menu_position'],normalized=moved_menu['saved_position'])
    controller.handle([('rotate',1,False)]);pump()
    assert controller.menu_index==1
    controller.handle([('press',0,False)]);pump()
    assert controller.mode=='adjust' and controller.profiles.selected=='brightness'
    results['menu_selection_without_keys']=True
    controller.update_context(dict(wm_class='blender',monitor=0));pump()
    controller.open_menu();controller.menu_index=5;controller.menu_activate();pump()
    assert controller.menu_page==1
    controller.menu_index=1;controller.menu_activate();pump()
    assert controller.menu_path==('@group:动画',)
    assert [item['id'] for item in controller.state()['menu']]==['@back','blender_frame','blender_keyframe']
    snapshot('group-ring.png');results['blender_group_menu']=True
    controller.close_menu();controller.update_context({});pump()
    controller.select('workspace');controller.rotate(1,False);pump(.5)
    assert controller.profiles.selected=='workspace'
    results['native_workspace']=controller.label
    controller.select('apps');controller.rotate(1,False);pump(.6)
    assert controller.profiles.selected=='apps'
    results['apps_empty_handled']=controller.error
    before=(config_dir/'config.json').read_bytes();bad=copy.deepcopy(controller.config);bad['actions'][0]['step']=-1
    success,message=own_call('SaveConfig','(s)',(json.dumps(bad),));assert not success and message
    assert (config_dir/'config.json').read_bytes()==before
    good=copy.deepcopy(controller.config);good['actions'][0]['step']=3
    assert own_call('SaveConfig','(s)',(json.dumps(good),))[0];pump(.2)
    assert controller.config['actions'][0]['step']==3
    results['valid_save_invalid_preservation']=True
    call(bus,SHELL_NAME,SHELL_PATH,SHELL_NAME,'TestDesktop');pump(.5)
    test_client('org.test.DialZen')
    results['test_windows']=snapshot('first-window.png')
    assert controller.profiles.current['id']=='zen',controller.profiles.context
    test_client('org.test.DialBlender')
    assert controller.profiles.current['id']=='blender',controller.profiles.context
    controller.select('apps');before_context=controller.profiles.context.copy();controller.rotate(1,False);pump(.15)
    snapshot('app-preview.png');pump(.6)
    assert controller.profiles.context.get('window_id')!=before_context.get('window_id')
    assert controller.profiles.selected=='apps' and controller.profiles.context.get('dial_focus')
    results['native_switch_real_windows_keeps_action']=True
    from settings import Settings
    app=Settings(config_dir)
    # Controller and editor normally live in separate processes. Let this combined test dispatch the real async bus round trip.
    app.remote=lambda method,signature=None,args=():own_call(method,signature,args)
    assert app.register(None);app.activate();pump(.8)
    assert app.window and app.window.get_visible()
    assert controller.editing,'GTK focus must pause Dial actions while editing'
    foreground=controller.last_foreground.copy()
    controller.update_context({'wm_class':'python3'})
    controller.handle([('rotate',1,False),('press',0,False)])
    assert controller.editing and controller.last_foreground==foreground
    results['editor_focus_guards_generic_wmclass']=True
    snapshot('settings.png')
    # Exercise the real widget builders: action edit, app picker and global options.
    for identifier in ['brightness','volume','scroll']:
        app.edit_action(identifier);pump(.1)
        visible=app.window.get_visible_dialog();assert visible;visible.close();pump(.1)
    app.profile_id='blender';app.refresh_profiles();app.edit_action('blender_brush');pump(.1)
    dialog=app.window.get_visible_dialog();assert dialog;dialog.close();pump(.1)
    app.pick_app();pump(.2);app.window.get_visible_dialog().close();pump(.1)
    app.options();pump(.1);app.window.get_visible_dialog().close();pump(.1)
    app.edit_group('动画');pump(.4);assert app.window.get_visible_dialog();snapshot('group-editor.png');app.window.get_visible_dialog().close();pump(.1)
    app.commit_group('动画','时间线',{'blender_frame','blender_keyframe','volume'})
    app.reorder_group('时间线',1)
    assert app.effective_action(app.current(),'volume')['group']=='时间线'
    assert next(a for a in app.draft['actions'] if a['id']=='volume')['group']==''
    assert app.save();pump(.2)
    saved=Profiles(controller.config);saved.update_context({'wm_class':'blender'})
    assert [item['id'] for item in saved.menu_items(('@group:时间线',))]==['@back','volume','blender_frame','blender_keyframe']
    app.remove_group('时间线')
    assert app.effective_action(app.current(),'volume')['group']==''
    assert 'blender_frame' in app.current()['menu']
    assert app.save();pump(.2)
    results['gtk_group_members_rename_order_and_ungroup']=True
    app.profile_id='global';app.refresh_profiles();assert app.save()
    results['gtk_editor_dialogs_and_save']=True
    controller.stop();pump(.15)
    controller=Controller(config_dir,no_hardware=True);pump(.5)
    # A modal editor can leave the GTK parent inactive; Shell still identifies its foreground app.
    context=json.loads(call(bus,SHELL_NAME,SHELL_PATH,SHELL_NAME,'GetContext')[0])
    assert context['wm_class']=='org.cachyos.AsusDial.Settings',context
    assert controller.editing,'Controller reconnect must retain pause for the foreground editor'
    results['controller_reconnect_resyncs_editor_focus']=True
    # Destroy settings normally, without touching the real session's applications.
    app.dirty=False;app.window.close();pump(.2)
    assert not controller.editing
    for client in clients:client.terminate();client.wait(timeout=3)
    clients.clear();pump(.2)
    controller.update_context({});controller.select('volume')
    controller.open_menu();pump(.2)
    # GNOME disables extensions on lock; exercise disable/re-enable cleanup, including a modal menu.
    subprocess.run(['gdbus','call','--session','--dest','org.gnome.Shell.Extensions','--object-path','/org/gnome/Shell/Extensions',
        '--method','org.gnome.Shell.Extensions.DisableExtension','asus-dial@local.cachyos'],check=True,capture_output=True)
    pump(.3);assert not controller.bridge
    subprocess.run(['gdbus','call','--session','--dest','org.gnome.Shell.Extensions','--object-path','/org/gnome/Shell/Extensions',
        '--method','org.gnome.Shell.Extensions.EnableExtension','asus-dial@local.cachyos'],check=True,capture_output=True)
    pump(.5);assert controller.bridge
    results['extension_disable_reenable_modal_cleanup']=True
    results['passed']=True
except Exception as error:
    import traceback
    results['passed']=False;results['error']=str(error);traceback.print_exc()
finally:
    for client in clients:
        client.terminate()
        try:client.wait(timeout=2)
        except subprocess.TimeoutExpired:client.kill();client.wait()
    if controller:controller.stop();pump(.1)
    os.killpg(shell.pid,signal.SIGTERM)
    try:shell.wait(timeout=6)
    except subprocess.TimeoutExpired:os.killpg(shell.pid,signal.SIGKILL);shell.wait()
    log.close();atomic_save(root/'result.json',results)
    print(json.dumps(results,ensure_ascii=False,indent=2))
sys.exit(0 if results.get('passed') else 1)
