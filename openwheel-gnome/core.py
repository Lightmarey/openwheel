# SPDX-License-Identifier: MIT
"""Configuration, report decoding and gestures, independent of desktop IO."""
import copy
import fnmatch
import json
import math
import os
import re
import tempfile
from pathlib import Path

KINDS = ('volume', 'brightness', 'apps', 'workspace', 'scroll', 'shortcut', 'mpris', 'mpv')
KIND_NAMES = ('系统音量', '屏幕亮度', '应用切换', '虚拟桌面', '滚动', '快捷键', '媒体播放器', 'mpv播放器')

def action(identifier, name, kind, step=1, fine=1, **extra):
    return dict(id=identifier, name=name, kind=kind, step=step, fine_step=fine, group='', **extra)

def default_config():
    actions = [action('volume', '音量', 'volume', 2, 1),
               action('brightness', '亮度', 'brightness', 2, 1, target='focused'),
               action('apps', '应用切换', 'apps'), action('workspace', '虚拟桌面', 'workspace'),
               action('scroll', '滚动', 'scroll')]
    profiles = [dict(id='global', name='全局 / 未指定应用', match=dict(desktop_ids=[], wm_classes=[]),
                     default_action='volume', remember=True, inherit_global=False,
                     menu=[a['id'] for a in actions], overrides={})]
    def add_profile(identifier, name, desktops, classes, funcs, default=None):
        actions.extend(funcs)
        profiles.append(dict(id=identifier, name=name, match=dict(desktop_ids=desktops, wm_classes=classes),
                             default_action=default or funcs[0]['id'], remember=True,
                             inherit_global=True, menu=[a['id'] for a in funcs], overrides={}))
    add_profile('zen', 'Firefox / Zen', ['zen.desktop', 'firefox.desktop', 'org.mozilla.firefox.desktop'],
                ['zen', 'firefox', 'zen-alpha'], [
                    action('zen_zoom', '网页缩放', 'shortcut', ccw='<Control>minus', cw='<Control>plus'),
                    action('zen_tabs', '标签切换', 'shortcut', ccw='<Control>Page_Up', cw='<Control>Page_Down')], 'scroll')
    for identifier, name, desktops, classes in [
        ('vlc', 'VLC', ['vlc.desktop', 'org.videolan.VLC.desktop'], ['vlc']),
        ('spotify', 'Spotify', ['spotify.desktop', 'com.spotify.Client.desktop'], ['spotify']),
        ('cider', 'Cider', ['cider.desktop', 'sh.cider.Cider.desktop'], ['cider'])]:
        funcs = [action(identifier+'_seek', '播放进度', 'mpris', 5, 1, operation='seek', player=identifier),
                 action(identifier+'_volume', '播放器音量', 'mpris', 2, 1, operation='volume', player=identifier),
                 action(identifier+'_rate', '播放倍速', 'mpris', .1, .05, operation='rate', player=identifier)]
        for f in funcs: f['group'] = '媒体'
        add_profile(identifier, name, desktops, classes, funcs)
    funcs = [action('mpv_seek', '播放进度', 'mpv', 5, 1, operation='seek'),
             action('mpv_volume', '播放器音量', 'mpv', 2, 1, operation='volume'),
             action('mpv_rate', '播放倍速', 'mpv', .1, .05, operation='rate'),
             action('mpv_frame', '逐帧', 'mpv', operation='frame')]
    for f in funcs: f['group'] = '媒体'
    add_profile('mpv', 'mpv', ['mpv.desktop', 'io.mpv.Mpv.desktop'], ['mpv'], funcs)
    def shortcut(identifier, name, group, ccw='', cw=''):
        a = action(identifier, name, 'shortcut', ccw=ccw, cw=cw); a['group'] = group; return a
    add_profile('blender', 'Blender', ['blender.desktop', 'org.blender.Blender.desktop'], ['blender'], [
        action('blender_zoom', '视图缩放', 'scroll'),
        shortcut('blender_frame', '时间轴逐帧', '动画', 'Left', 'Right'),
        shortcut('blender_keyframe', '关键帧跳转', '动画', 'Down', 'Up'),
        shortcut('blender_brush', '笔刷大小', '雕刻 / 绘制'),
        shortcut('blender_strength', '笔刷强度', '雕刻 / 绘制')])
    for identifier, name, desktops, classes, funcs in [
        ('krita', 'Krita', ['org.kde.krita.desktop', 'krita.desktop'], ['krita'], [('画笔大小','绘图'),('画布旋转','绘图')]),
        ('gimp', 'GIMP', ['gimp.desktop', 'org.gimp.GIMP.desktop'], ['gimp', 'gimp-*'], [('画笔大小','绘图')]),
        ('resolve', 'DaVinci Resolve', ['DaVinciResolve.desktop'], ['resolve'], [('时间线逐帧','时间线'),('时间线缩放','时间线')]),
        ('zoom', 'Zoom', ['Zoom.desktop', 'us.zoom.Zoom.desktop'], ['zoom'], [('会议静音','会议')]),
        ('tencent', 'Tencent Meeting', ['wemeetapp.desktop', 'com.tencent.wemeet.desktop'], ['wemeetapp', 'wemeet'], [('会议静音','会议')]),
        ('studioone', 'Studio One', ['studio-one.desktop'], ['studio one', 'studioone', '*studioone.exe'], [('时间线移动','音频'),('音轨选择','音频')]),
        ('darkroom', '照片应用（待指定）', [], [], [('缩放','照片'),('参数调整','照片')])]:
        own = [shortcut(identifier+'_'+str(i), label, group) for i,(label,group) in enumerate(funcs)]
        add_profile(identifier, name, desktops, classes, own, 'volume')
    return dict(version=2, input=dict(report_ids=[1,10], units_per_step=1, invert=False, long_press_seconds=.6),
                ui=dict(hud_timeout=2, menu_timeout=8), audio_max_percent=100,
                mpv_socket=os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/run/user/'+str(os.getuid())), 'mpv-asus-dial.sock'),
                actions=actions, profiles=profiles)

def parse_shortcut(text):
    """GTK accelerator names and common Ctrl+key notation -> Linux key names."""
    if not text.strip(): return []
    text = text.strip()
    mods = re.findall(r'<([^>]+)>', text)
    if mods:
        key = re.sub(r'<[^>]+>', '', text)
    elif '+' in text:
        if text.endswith('+'):
            mods = [p for p in text[:-1].split('+') if p]; key = 'plus'
        else:
            parts = text.split('+'); mods, key = parts[:-1], parts[-1]
    else: key = text
    modifiers = {'ctrl':'KEY_LEFTCTRL','control':'KEY_LEFTCTRL','primary':'KEY_LEFTCTRL',
                 'shift':'KEY_LEFTSHIFT','alt':'KEY_LEFTALT','super':'KEY_LEFTMETA','meta':'KEY_LEFTMETA'}
    result=[]
    for mod in mods:
        if mod.lower() not in modifiers: raise ValueError('不支持的修饰键：'+mod)
        if modifiers[mod.lower()] not in result: result.append(modifiers[mod.lower()])
    aliases = {'left':'LEFT','right':'RIGHT','up':'UP','down':'DOWN','pageup':'PAGEUP','pagedown':'PAGEDOWN',
               'home':'HOME','end':'END','insert':'INSERT','delete':'DELETE','backspace':'BACKSPACE',
               'return':'ENTER','enter':'ENTER','escape':'ESC','esc':'ESC','space':'SPACE','tab':'TAB',
               'minus':'MINUS','-':'MINUS','equal':'EQUAL','=':'EQUAL','plus':'EQUAL',
               'bracketleft':'LEFTBRACE','leftbracket':'LEFTBRACE','[':'LEFTBRACE',
               'bracketright':'RIGHTBRACE','rightbracket':'RIGHTBRACE',']':'RIGHTBRACE',
               'comma':'COMMA',',':'COMMA','period':'DOT','.':'DOT','slash':'SLASH','/':'SLASH',
               'backslash':'BACKSLASH','semicolon':'SEMICOLON','apostrophe':'APOSTROPHE','grave':'GRAVE',
               'kpadd':'KPPLUS','kpsubtract':'KPMINUS','kpmultiply':'KPASTERISK','kpdivide':'KPSLASH',
               'kpenter':'KPENTER','xf86audiomute':'MUTE','xf86audiolowervolume':'VOLUMEDOWN',
               'xf86audioraisevolume':'VOLUMEUP','xf86audioplay':'PLAYPAUSE','xf86audionext':'NEXTSONG',
               'xf86audioprev':'PREVIOUSSONG','xf86monbrightnessup':'BRIGHTNESSUP','xf86monbrightnessdown':'BRIGHTNESSDOWN'}
    normalized=key.lower().replace('_','').replace(' ','')
    if normalized == 'isolefttab':
        normalized='tab'
        if 'KEY_LEFTSHIFT' not in result: result.append('KEY_LEFTSHIFT')
    if normalized == 'plus' and 'KEY_LEFTSHIFT' not in result: result.append('KEY_LEFTSHIFT')
    if normalized in aliases: code='KEY_'+aliases[normalized]
    elif re.fullmatch('[a-z0-9]', normalized): code='KEY_'+normalized.upper()
    elif re.fullmatch(r'f(?:[1-9]|1[0-9]|2[0-4])', normalized): code='KEY_'+normalized.upper()
    elif re.fullmatch(r'kp[0-9]', normalized): code='KEY_KP'+normalized[-1]
    else: raise ValueError('不支持的按键：'+key)
    result.append(code)
    return result

def validate_config(config):
    if not isinstance(config,dict) or config.get('version') != 2: raise ValueError('配置版本不受支持')
    if not isinstance(config.get('actions'),list) or not isinstance(config.get('profiles'),list): raise ValueError('功能与应用配置须为列表')
    ids=set()
    for a in config.get('actions',[]):
        if not isinstance(a,dict) or not isinstance(a.get('id'),str) or not a['id'] or a['id'].startswith('@') or a['id'] in ids: raise ValueError('功能标识重复、为空或使用了保留前缀')
        ids.add(a['id'])
        if not isinstance(a.get('name'),str) or not a['name'].strip(): raise ValueError('功能名称不能为空')
        if a.get('kind') not in KINDS: raise ValueError('未知动作类型')
        for field in ['step','fine_step']:
            value=a.get(field,1)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0: raise ValueError('步长须为正数')
            if a['kind'] in ['apps','workspace','scroll','shortcut'] and not float(value).is_integer(): raise ValueError('窗口、工作区、滚轮及快捷键的步长须为整数')
        if a['kind']=='shortcut':
            if not all(isinstance(a.get(k,''),str) for k in ['ccw','cw']): raise ValueError('快捷键须为文本')
            parse_shortcut(a.get('ccw',''));parse_shortcut(a.get('cw',''))
        if a['kind'] in ['mpris','mpv'] and a.get('operation','seek') not in (['seek','volume','rate'] if a['kind']=='mpris' else ['seek','volume','rate','frame']): raise ValueError('未知播放器功能')
        if not isinstance(a.get('group',''),str): raise ValueError('功能分组须为文本')
        if a['kind']=='brightness':
            target=a.get('target','focused')
            if not (target in ['focused','internal','AOC'] if isinstance(target,str) else isinstance(target,dict) and bool(target) and all(k in ['mfg','model','sn'] and isinstance(v,str) for k,v in target.items())): raise ValueError('显示器目标无效')
    profiles=config.get('profiles',[])
    if not profiles or not isinstance(profiles[0],dict) or profiles[0].get('id')!='global' or not profiles[0].get('menu'): raise ValueError('需要至少一个全局功能')
    seen=set()
    for p in profiles:
        if not isinstance(p,dict) or not isinstance(p.get('id'),str) or not p['id'] or p['id'] in seen or not isinstance(p.get('name'),str) or not p['name'].strip(): raise ValueError('应用配置标识或名称无效')
        seen.add(p['id'])
        if not isinstance(p.get('menu',[]),list) or any(not isinstance(i,str) for i in p.get('menu',[])): raise ValueError('功能菜单须为标识列表')
        groups=p.get('groups',[])
        if not isinstance(groups,list) or any(not isinstance(g,str) or not g.strip() for g in groups) or len(set(groups))!=len(groups): raise ValueError('功能组名称须非空且不重复')
        effective=list(dict.fromkeys((profiles[0]['menu'] if p.get('inherit_global') else [])+p.get('menu',[])))
        if any(i not in ids for i in effective): raise ValueError('菜单引用不存在的功能')
        if effective and p.get('default_action') not in effective: raise ValueError('默认功能须在该应用菜单内')
        if not isinstance(p.get('match',{}),dict): raise ValueError('应用匹配格式无效')
        for field in ['desktop_ids','wm_classes']:
            if not isinstance(p.get('match',{}).get(field,[]),list) or any(not isinstance(v,str) for v in p.get('match',{}).get(field,[])): raise ValueError('应用匹配须为名称列表')
        if not isinstance(p.get('overrides',{}),dict): raise ValueError('应用覆盖须为对象')
        for identifier,fields in p.get('overrides',{}).items():
            if identifier not in ids: raise ValueError('覆盖引用不存在的功能')
            if not isinstance(fields,dict) or 'id' in fields: raise ValueError('应用覆盖不能改变功能标识')
            original=next(a for a in config['actions'] if a['id']==identifier)
            merged=dict(original,**fields);merged['id']=identifier
            temporary=dict(config,actions=[merged],profiles=[dict(profiles[0],menu=[identifier],default_action=identifier,overrides={})])
            validate_config(temporary)
    raw=config.get('input',{})
    if not 0.1<=float(raw.get('long_press_seconds',.6))<=3: raise ValueError('长按时长须为0.1–3秒')
    if not 0<float(raw.get('units_per_step',1))<=3600: raise ValueError('旋转单位无效')
    if not raw.get('report_ids') or any(v not in [1,10] for v in raw['report_ids']): raise ValueError('旋钮报告须为1或10')
    if not 1<=float(config.get('audio_max_percent',100))<=153: raise ValueError('音量上限须为1–153%')
    for field in ['hud_timeout','menu_timeout']:
        if not .5<=float(config.get('ui',{}).get(field,2))<=60: raise ValueError('显示时长无效')
    return config

def migrate_config(old):
    if old.get('version')==2: return validate_config(old)
    new=default_config(); new['legacy_config']=copy.deepcopy(old)
    new['input']['invert']=bool(old.get('invert_direction',False))
    own=[]
    for i,m in enumerate(old.get('modes',[])):
        kind=m.get('action','wheel'); identifier='legacy_'+str(i)
        if kind=='wheel': a=action(identifier,m.get('name','旧滚动'),'scroll',max(1,m.get('step',1)))
        elif kind in ['volume','brightness']: a=action(identifier,m.get('name','旧功能'),kind,6 if kind=='volume' else 2,1)
        elif kind=='brush': a=action(identifier,m.get('name','旧画笔'),'shortcut',ccw='bracketleft',cw='bracketright')
        else: continue
        new['actions'].append(a);own.append(identifier)
    if own:
        new['profiles'].append(dict(id='legacy',name='旧四模式（保留）',match=dict(desktop_ids=[],wm_classes=[]),
                                   default_action=own[0],inherit_global=False,remember=True,menu=own,overrides={}))
    return validate_config(new)

def atomic_save(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(value,output,ensure_ascii=False,indent=2);output.write('\n');output.flush();os.fsync(output.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

class Gestures:
    def __init__(self, hold=.6, units=1, invert=False):
        self.hold,self.units,self.invert=hold,units,invert;self.reset()
    def reset(self):
        self.down=False;self.started=0.;self.rotated=False;self.consumed=False;self.remainder=0.
    def feed(self, button, delta, now):
        events=[];was_down=self.down
        if button and not was_down:
            self.started=now;self.rotated=False;self.consumed=False
        self.down=button
        if delta:
            self.remainder+=delta*(-1 if self.invert else 1)
            steps=math.trunc(self.remainder/self.units);self.remainder-=steps*self.units
            if button or was_down: self.rotated=True
            if steps and not self.consumed: events.append(('rotate',steps,bool(button or was_down)))
        if was_down and not button:
            if not self.rotated and not self.consumed: events.append(('long' if now-self.started>=self.hold else 'press',0,False))
            self.consumed=False
        return events
    def tick(self,now):
        if self.down and not self.rotated and not self.consumed and now-self.started>=self.hold:
            self.consumed=True;return [('long',0,False)]
        return []

def group_names(config,profile):
    identifiers=list(dict.fromkeys((config['profiles'][0]['menu'] if profile.get('inherit_global') else [])+profile.get('menu',[])))
    actions={a['id']:a for a in config['actions']}
    groups=list(profile.get('groups',[]))
    for identifier in identifiers:
        name=dict(actions[identifier],**profile.get('overrides',{}).get(identifier,{})).get('group','')
        if name and name not in groups:groups.append(name)
    return groups

class Profiles:
    def __init__(self, config, remembered=None):
        self.config=validate_config(config);self.remembered=remembered or {};self.current=config['profiles'][0]
        self.selected=self._initial(self.current);self.context={}
    def identifiers(self,p=None):
        p=p or self.current
        return list(dict.fromkeys((self.config['profiles'][0]['menu'] if p.get('inherit_global') else [])+p.get('menu',[])))
    def _initial(self,p):
        remembered=self.remembered.get(p['id']) if p.get('remember',True) else None
        ids=self.identifiers(p)
        return remembered if remembered in ids else p.get('default_action') if p.get('default_action') in ids else ids[0] if ids else None
    def update_context(self, context):
        self.context=context
        if context.get('dial_focus'): return False
        desktop=context.get('app_id','').casefold();wm=context.get('wm_class','').casefold()
        found=self.config['profiles'][0]
        for p in self.config['profiles'][1:]:
            match=p.get('match',{})
            if any(desktop and fnmatch.fnmatchcase(desktop,v.casefold()) for v in match.get('desktop_ids',[])) or any(wm and fnmatch.fnmatchcase(wm,v.casefold()) for v in match.get('wm_classes',[])):
                found=p;break
        if found['id']==self.current['id']: return False
        self.current=found;self.selected=self._initial(found);return True
    def select(self,identifier):
        if identifier not in self.identifiers(): raise ValueError('该应用没有这个功能')
        self.selected=identifier
        if self.current.get('remember',True): self.remembered[self.current['id']]=identifier
    def get_action(self,identifier=None):
        identifier=identifier or self.selected
        source=next((a for a in self.config['actions'] if a['id']==identifier),None)
        return dict(source,**self.current.get('overrides',{}).get(identifier,{})) if source else None
    def menu_items(self,path=(),page=0):
        identifiers=self.identifiers();items=[]
        def leaf(identifier):
            a=self.get_action(identifier)
            return dict(id=identifier,name=a['name'],kind=a['kind'],type='action',selected=identifier==self.selected,
                        available=a['kind']!='shortcut' or bool(a.get('ccw') and a.get('cw')))
        if not path:
            seen=set()
            for identifier in identifiers:
                group=self.get_action(identifier).get('group','')
                if group:
                    if group not in seen:items.append(dict(id='@group:'+group,name=group,type='group'));seen.add(group)
                else: items.append(leaf(identifier))
            ordered=[g for g in group_names(self.config,self.current) if g in seen]
            iterator=iter(ordered)
            for index,item in enumerate(items):
                if item['type']!='group':continue
                name=next(iterator)
                members=[i for i in identifiers if self.get_action(i).get('group','')==name]
                items[index]=dict(id='@group:'+name,name=name,type='group',selected=self.selected in members)
        else:
            group=path[-1].removeprefix('@group:')
            items=[leaf(i) for i in identifiers if self.get_action(i).get('group','')==group]
        if path: items.insert(0,dict(id='@back',name='返回',type='back'))
        if len(items)>6:
            pages=math.ceil(len(items)/5);page%=pages
            items=items[page*5:(page+1)*5]+[dict(id='@next',name=f'下一页 {page+1}/{pages}',type='next')]
        return items

    def menu_location(self,path=()):
        # Start on the remembered function, including when it lives inside a group or on another page.
        for page in range(math.ceil((len(self.identifiers())+1)/5)+1):
            items=self.menu_items(path,page)
            for index,item in enumerate(items):
                if item.get('selected'):return page,index
            if not any(item['type']=='next' for item in items):break
        return 0,1 if path and len(self.menu_items(path))>1 else 0
