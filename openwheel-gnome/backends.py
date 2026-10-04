# SPDX-License-Identifier: MIT
"""Narrow desktop actions. Slow display/media IO runs off the input loop."""
import json
import math
import re
import socket
import subprocess
from pathlib import Path
from gi.repository import Gio, GLib
from core import parse_shortcut

DISPLAY_NAME='org.gnome.Mutter.DisplayConfig'
DISPLAY_PATH='/org/gnome/Mutter/DisplayConfig'
PROPERTIES='org.freedesktop.DBus.Properties'

def call(connection, name, path, interface, method, signature=None, args=(), timeout=3000):
    parameters=GLib.Variant(signature,args) if signature else None
    return connection.call_sync(name,path,interface,method,parameters,None,Gio.DBusCallFlags.NONE,timeout,None).unpack()

def props(connection,name,path,interface):
    return call(connection,name,path,PROPERTIES,'GetAll','(s)',(interface,))[0]

class Input:
    def __init__(self):
        from evdev import UInput,ecodes
        self.codes=ecodes;self.pressed=set()
        keys=sorted({value for name,value in ecodes.ecodes.items() if name.startswith('KEY_') and name not in ['KEY_MAX','KEY_CNT','KEY_RESERVED']})
        self.device=UInput({ecodes.EV_KEY:keys+[ecodes.BTN_MIDDLE],ecodes.EV_REL:[ecodes.REL_WHEEL,ecodes.REL_HWHEEL]},
                          name='ASUS Dial Virtual Controller',vendor=0x0B05,product=0x0220)
    def hold(self,name):
        code=self.codes.ecodes[name]
        if code not in self.pressed:
            self.device.write(self.codes.EV_KEY,code,1);self.pressed.add(code);self.device.syn()
    def shortcut(self,text,count=1):
        names=parse_shortcut(text)
        if not names: raise ValueError('请先在设置中录制两个方向的快捷键')
        codes=[self.codes.ecodes[n] for n in names]
        for _ in range(count):
            added=[]
            try:
                for code in codes:
                    if code not in self.pressed:
                        self.device.write(self.codes.EV_KEY,code,1);self.pressed.add(code);added.append(code)
                self.device.syn()
            finally:
                self._release_codes(reversed(added))
    def scroll(self,delta):
        self.device.write(self.codes.EV_REL,self.codes.REL_WHEEL,int(delta));self.device.syn()
    def _release_codes(self,codes):
        error=None
        for code in list(codes):
            try:self.device.write(self.codes.EV_KEY,code,0)
            except OSError as issue:error=issue
            finally:self.pressed.discard(code)
        try:self.device.syn()
        except OSError as issue:error=issue
        if error:raise error
    def release_all(self):self._release_codes(list(self.pressed))
    def close(self):
        try:self.release_all()
        finally:self.device.close()

class Audio:
    def __init__(self):
        import gi
        gi.require_version('Gvc','1.0')
        from gi.repository import Gvc
        self.control=Gvc.MixerControl(name='ASUS Dial')
        self.control.open()
    def adjust(self,delta,maximum):
        sink=self.control.get_default_sink()
        if sink is None: raise ValueError('尚无可用的音频输出设备')
        norm=self.control.get_vol_max_norm()
        value=max(0,min(maximum,sink.get_volume()/norm*100+delta))
        sink.set_volume(round(value*norm/100));sink.push_volume()
        if delta>0 and sink.get_is_muted():sink.change_is_muted(False)
        level=sink.get_volume()/norm;muted=sink.get_is_muted()
        icon='audio-volume-'+('muted' if muted or level<=0 else ['low','medium','high'][min(2,max(0,math.ceil(level*3)-1))])+'-symbolic'
        return dict(label=f'{sink.get_description() or "音量"} · {level*100:.0f}%'+(' · 静音' if muted else ''),
                    osd=dict(kind='volume',icon=icon,level=0 if muted else level,max_level=max(1,maximum/100)))
    def close(self):self.control.close()

def monitors(connection):
    serial,physical,logical,_=call(connection,DISPLAY_NAME,DISPLAY_PATH,DISPLAY_NAME,'GetCurrentState')
    result=[]
    for spec,_modes,properties in physical:
        rect=None;primary=False
        for x,y,scale,transform,is_primary,specs,_props in logical:
            if tuple(spec) in [tuple(s) for s in specs]: rect={'x':x,'y':y};primary=is_primary;break
        result.append(dict(connector=spec[0],mfg=spec[1],model=spec[2],sn=spec[3],
                           internal=bool(properties.get('is-builtin',False)),primary=primary,rect=rect,
                           name=properties.get('display-name') or spec[2]))
    return result

def select_monitor(displays,target,context):
    active=[m for m in displays if m['rect'] is not None]
    if isinstance(target,dict):
        candidates=[m for m in active if all(m.get(k,'')==v for k,v in target.items() if k in ['mfg','model','sn'] and v)]
    elif target=='internal':candidates=[m for m in active if m['internal']]
    elif target=='AOC':candidates=[m for m in active if m['mfg']=='AOC']
    else:
        rect=context.get('monitor_rect',{})
        candidates=[m for m in active if rect and m['rect']['x']==rect.get('x') and m['rect']['y']==rect.get('y')]
        if not candidates:candidates=[m for m in active if m['primary']]
    if len(candidates)!=1:raise ValueError('目标显示器不可用或不唯一，请在设置中指定显示器')
    return candidates[0]

def brightness(connection,action,delta,context):
    monitor=select_monitor(monitors(connection),action.get('target','focused'),context)
    if monitor['internal']:
        serial,entries=call(connection,DISPLAY_NAME,DISPLAY_PATH,PROPERTIES,'Get','(ss)',(DISPLAY_NAME,'Backlight'))[0]
        entry=next((e for e in entries if e['connector']==monitor['connector'] and e.get('active')),None)
        if not entry:raise ValueError('内屏背光当前不可用')
        low,high=entry['min'],entry['max'];value=entry['value']
        if high<=low:raise ValueError('背光范围无效')
        requested=round(max(low,min(high,value+delta*(high-low)/100)))
        call(connection,DISPLAY_NAME,DISPLAY_PATH,DISPLAY_NAME,'SetBacklight','(usi)',(serial,monitor['connector'],requested))
        _serial,updated=call(connection,DISPLAY_NAME,DISPLAY_PATH,PROPERTIES,'Get','(ss)',(DISPLAY_NAME,'Backlight'))[0]
        actual=next(e['value'] for e in updated if e['connector']==monitor['connector'])
        level=(actual-low)/(high-low)
        return dict(label=f'内屏 · {level*100:.0f}%',osd=dict(kind='brightness',icon='display-brightness-symbolic',
                    label='内屏',connector=monitor['connector'],level=level,max_level=1))
    args=['ddcutil','--mfg',monitor['mfg'],'--model',monitor['model']]
    if monitor['sn']:args+=['--sn',monitor['sn']]
    def read():
        result=subprocess.run(args+['getvcp','10','--brief'],capture_output=True,text=True,timeout=8)
        match=re.search(r'VCP\s+10\s+C\s+(\d+)\s+(\d+)',result.stdout)
        if result.returncode or not match:raise ValueError('DDC亮度读取失败：'+(result.stderr or result.stdout).strip()[:140])
        return int(match[1]),int(match[2])
    current,maximum=read()
    if maximum<=0:raise ValueError('显示器亮度范围无效')
    requested=round(max(0,min(maximum,current+delta*maximum/100)))
    if requested!=current:
        result=subprocess.run(args+['setvcp','10',str(requested)],capture_output=True,text=True,timeout=8)
        if result.returncode:raise ValueError('DDC亮度写入失败：'+(result.stderr or result.stdout).strip()[:140])
    actual,maximum=read()
    label=f'{monitor["mfg"]} {monitor["model"]}'
    return dict(label=f'{label} · {actual/maximum*100:.0f}%',osd=dict(kind='brightness',icon='display-brightness-symbolic',
                label=label,connector=monitor['connector'],level=actual/maximum,max_level=1))

def media(connection,action,delta,context):
    names=call(connection,'org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','ListNames')[0]
    path='/org/mpris/MediaPlayer2';interface='org.mpris.MediaPlayer2.Player'
    hint=action.get('player','').casefold()
    wm=context.get('wm_class','').casefold();desktop=context.get('app_id','').removesuffix('.desktop').casefold()
    candidates=[]
    for name in names:
        if not name.startswith('org.mpris.MediaPlayer2.'):continue
        root=props(connection,name,path,'org.mpris.MediaPlayer2')
        identity=str(root.get('Identity','')).casefold();entry=str(root.get('DesktopEntry','')).casefold()
        if hint and any(hint in value for value in [name.casefold(),identity,entry]):candidates.append(name)
        elif not hint and any(value and (value in entry or value in identity or value in name.casefold()) for value in [desktop,wm]):candidates.append(name)
    if len(candidates)!=1:raise ValueError('未找到唯一的目标播放器；可在设置中指定播放器')
    name=candidates[0];values=props(connection,name,path,interface);operation=action.get('operation','seek')
    if not values.get('CanControl',False):raise ValueError('播放器当前不允许远程控制')
    if operation=='seek':
        if not values.get('CanSeek',False):raise ValueError('该播放器或当前曲目不支持跳转')
        call(connection,name,path,interface,'Seek','(x)',(round(delta*1000000),))
        position=props(connection,name,path,interface).get('Position')
        if position is None:return f'进度 {delta:+g} 秒'
        return f'进度 · {int(position//60000000)}:{int(position//1000000)%60:02d}'
    property_name='Volume' if operation=='volume' else 'Rate'
    if property_name not in values:raise ValueError('该播放器不提供此参数')
    low,high=(0,1) if operation=='volume' else (values.get('MinimumRate',1),values.get('MaximumRate',1))
    if operation=='rate' and low==high:raise ValueError('该播放器不支持调速')
    value=max(low,min(high,values[property_name]+delta/(100 if operation=='volume' else 1)))
    call(connection,name,path,PROPERTIES,'Set','(ssv)',(interface,property_name,GLib.Variant('d',float(value))))
    actual=call(connection,name,path,PROPERTIES,'Get','(ss)',(interface,property_name))[0]
    return f'播放器音量 · {actual*100:.0f}%' if operation=='volume' else f'播放倍速 · {actual:.2f}×'

def mpv(action,delta,config):
    path=Path(config['mpv_socket']).expanduser()
    if not path.exists():raise ValueError('mpv未启用IPC；请在设置中配置其本地连接路径')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(2);client.connect(str(path));stream=client.makefile('r',encoding='utf-8')
        serial=0
        def command(*args):
            nonlocal serial
            serial+=1;client.sendall((json.dumps({'command':list(args),'request_id':serial})+'\n').encode())
            while True:
                line=stream.readline()
                if not line:raise ValueError('mpv连接已关闭')
                response=json.loads(line)
                if response.get('request_id')==serial:
                    if response.get('error')!='success':raise ValueError('mpv：'+response.get('error','未知错误'))
                    return response.get('data')
        operation=action.get('operation','seek')
        if operation=='frame':
            for _ in range(max(1,round(abs(delta)))):command('frame-step' if delta>0 else 'frame-back-step')
            position=command('get_property','time-pos');return f'进度 · {position:.2f} 秒'
        key={'seek':'time-pos','volume':'volume','rate':'speed'}[operation]
        current=command('get_property',key)
        low,high=(0,command('get_property','duration') or math.inf) if operation=='seek' else (0,100) if operation=='volume' else (.01,100)
        value=max(low,min(high,current+delta));command('set_property',key,value);actual=command('get_property',key)
        return f'进度 · {actual:.1f} 秒' if operation=='seek' else f'播放器音量 · {actual:.0f}%' if operation=='volume' else f'播放倍速 · {actual:.2f}×'
