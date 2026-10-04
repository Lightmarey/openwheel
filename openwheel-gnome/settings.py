# SPDX-License-Identifier: MIT
"""Native application/profile editor. Edits remain a draft until Save."""
import copy
import json
import uuid
from pathlib import Path
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from core import KINDS, KIND_NAMES, action, atomic_save, group_names, migrate_config, parse_shortcut, validate_config
from backends import call, monitors

NAME='org.cachyos.AsusDial';PATH='/org/cachyos/AsusDial'

def clear(container):
    child=container.get_first_child()
    while child:
        following=child.get_next_sibling();container.remove(child);child=following

def entry(group,title,value,changed):
    row=Adw.EntryRow(title=title);row.set_text(str(value));row.connect('changed',lambda r:changed(r.get_text()));group.add(row);return row

def switch(group,title,value,changed):
    row=Adw.SwitchRow(title=title,active=bool(value));row.connect('notify::active',lambda r,_:changed(r.get_active()));group.add(row);return row

def combo(group,title,names,selected,changed):
    row=Adw.ComboRow(title=title,model=Gtk.StringList.new(names));row.set_selected(selected)
    row.connect('notify::selected',lambda r,_:changed(r.get_selected()));group.add(row);return row

def spin(group,title,value,changed,low=.01,high=100,step=.1):
    adjustment=Gtk.Adjustment(value=value,lower=low,upper=high,step_increment=step,page_increment=step*10)
    row=Adw.SpinRow(title=title,adjustment=adjustment,digits=2 if step<1 else 0)
    row.connect('notify::value',lambda r,_:changed(r.get_value()));group.add(row);return row

class Settings(Adw.Application):
    def __init__(self,config_dir=None):
        GLib.set_prgname('org.cachyos.AsusDial.Settings');GLib.set_application_name('ASUS Dial')
        super().__init__(application_id='org.cachyos.AsusDial.Settings',flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.config_path=Path(config_dir or Path.home()/'.config/asus-dial')/'config.json'
        self.connect('activate',self.on_activate);self.connect('shutdown',self.on_shutdown);self.window=None;self.loading=False

    def remote(self,method,signature=None,args=()):return call(self.bus,NAME,PATH,NAME,method,signature,args,1000)

    def on_activate(self,application):
        if self.window:self.window.present();return
        self.bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        try:self.draft=json.loads(self.remote('GetConfig')[0])
        except GLib.Error:self.draft=migrate_config(json.loads(self.config_path.read_text()))
        self.profile_id='global';self.dirty=False
        self.window=Adw.ApplicationWindow(application=self,title='ASUS Dial',default_width=1040,default_height=790)
        self.window.connect('notify::is-active',self.active_changed)
        self.window.connect('close-request',self.close_requested)
        toolbar=Adw.ToolbarView();header=Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title='ASUS Dial',subtitle='直接旋转 · 短按选功能 · 按住微调'))
        add=Gtk.Button(icon_name='list-add-symbolic',tooltip_text='新增应用');add.connect('clicked',lambda _:self.add_profile());header.pack_start(add)
        menu=Gio.Menu()
        for title,name in [('导入配置','import'),('导出配置','export'),('全局选项','options')]:
            menu.append(title,'app.'+name);simple=Gio.SimpleAction.new(name,None)
            simple.connect('activate',lambda a,_:self.file_dialog(a.get_name()) if a.get_name() in ['import','export'] else self.options())
            self.add_action(simple)
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic',menu_model=menu))
        self.save_button=Gtk.Button(label='保存',sensitive=False);self.save_button.add_css_class('suggested-action')
        self.save_button.connect('clicked',lambda _:self.save());header.pack_end(self.save_button)
        toolbar.add_top_bar(header)
        self.toasts=Adw.ToastOverlay();toolbar.set_content(self.toasts)
        split=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,position=270,resize_start_child=False,shrink_start_child=False)
        sidebar=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8,margin_top=12,margin_bottom=12,margin_start=12,margin_end=12)
        sidebar.append(Gtk.Label(label='应用配置',xalign=0,css_classes=['heading']))
        self.list=Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE,css_classes=['navigation-sidebar'])
        self.list.connect('row-selected',self.profile_selected)
        scroller=Gtk.ScrolledWindow(vexpand=True,hscrollbar_policy=Gtk.PolicyType.NEVER);scroller.set_child(self.list);sidebar.append(scroller)
        help_label=Gtk.Label(label='未安装应用也可预先配置。\n快捷键留空的模板需要先编辑。',wrap=True,xalign=0,css_classes=['dim-label'])
        sidebar.append(help_label);split.set_start_child(sidebar)
        self.right=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,vexpand=True);split.set_end_child(self.right)
        self.toasts.set_child(split);self.window.set_content(toolbar)
        self.controller_watch=Gio.bus_watch_name_on_connection(self.bus,NAME,Gio.BusNameWatcherFlags.NONE,
            lambda *_:self.active_changed(self.window,None),None)
        self.refresh_profiles();self.window.present()

    def on_shutdown(self,*args):
        if hasattr(self,'controller_watch'):Gio.bus_unwatch_name(self.controller_watch)

    def current(self):return next(p for p in self.draft['profiles'] if p['id']==self.profile_id)

    def changed(self):
        if not self.loading:self.dirty=True;self.save_button.set_sensitive(True)

    def toast(self,text):self.toasts.add_toast(Adw.Toast(title=str(text)[:240]))

    def active_changed(self,window,_):
        try:self.remote('SetEditing','(b)',(window.is_active(),))
        except GLib.Error:pass

    def close_requested(self,*args):
        if not self.dirty:
            try:self.remote('SetEditing','(b)',(False,))
            except GLib.Error:pass
            return False
        dialog=Adw.AlertDialog(heading='保存修改？',body='尚有未保存的应用或功能设置。')
        dialog.add_response('cancel','继续编辑');dialog.add_response('discard','放弃修改');dialog.add_response('save','保存')
        dialog.set_response_appearance('save',Adw.ResponseAppearance.SUGGESTED)
        dialog.connect('response',self.close_response);dialog.present(self.window);return True

    def close_response(self,dialog,response):
        if response=='save' and not self.save():return
        if response in ['save','discard']:self.dirty=False;self.window.close()

    def refresh_profiles(self):
        self.loading=True;clear(self.list)
        selected=None
        for p in self.draft['profiles']:
            row=Gtk.ListBoxRow();row.profile_id=p['id']
            text=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4,margin_top=10,margin_bottom=10,margin_start=10,margin_end=10)
            text.append(Gtk.Label(label=p['name'],xalign=0,ellipsize=3))
            matching=p.get('match',{})
            subtitle='默认五项与通用功能' if p['id']=='global' else '、'.join(matching.get('desktop_ids',[])+matching.get('wm_classes',[])) or '待指定应用'
            text.append(Gtk.Label(label=subtitle,xalign=0,ellipsize=3,css_classes=['dim-label','caption']))
            row.set_child(text);self.list.append(row)
            if p['id']==self.profile_id:selected=row
        self.list.select_row(selected or self.list.get_row_at_index(0));self.loading=False;self.render_profile()

    def profile_selected(self,_list,row):
        if row:self.profile_id=row.profile_id
        if row and not self.loading:self.render_profile()

    def render_profile(self):
        self.loading=True;clear(self.right);p=self.current()
        page=Adw.PreferencesPage(title=p['name'],vexpand=True);self.right.append(page)
        group=Adw.PreferencesGroup(title='应用与默认功能');page.add(group)
        entry(group,'配置名称',p['name'],lambda text:self.update_field(p,'name',text))
        if p['id']!='global':
            buttons=Gtk.Box(spacing=6)
            for text,callback in [('选择已安装应用',self.pick_app),('使用刚才的前台应用',self.use_foreground)]:
                button=Gtk.Button(label=text,margin_top=8,margin_bottom=8);button.connect('clicked',lambda _,f=callback:f());buttons.append(button)
            row=Adw.ActionRow(title='指定应用');row.add_suffix(buttons);group.add(row)
            entry(group,'应用 ID（逗号分隔）',', '.join(p['match'].get('desktop_ids',[])),lambda text:self.set_matches(p,'desktop_ids',text))
            entry(group,'窗口类别（可用 * 匹配）',', '.join(p['match'].get('wm_classes',[])),lambda text:self.set_matches(p,'wm_classes',text))
            switch(group,'同时提供全局功能',p.get('inherit_global',True),lambda value:self.inherit_changed(p,value))
        identifiers=self.effective_ids(p)
        if identifiers:
            names=[self.effective_action(p,i)['name'] for i in identifiers]
            selected=identifiers.index(p['default_action']) if p.get('default_action') in identifiers else 0
            p['default_action']=identifiers[selected]
            combo(group,'首次使用的功能',names,selected,lambda index:self.update_field(p,'default_action',identifiers[index]))
        switch(group,'记住上次选择的功能',p.get('remember',True),lambda value:self.update_field(p,'remember',value))
        groups=Adw.PreferencesGroup(title='功能组',description='在旋钮菜单中选中组，再按下展开。组名、成员和顺序均可修改。');page.add(groups)
        for name in group_names(self.draft,p):
            count=sum(self.effective_action(p,i).get('group','')==name for i in identifiers)
            row=Adw.ActionRow(title=name,subtitle=f'{count} 项功能',use_markup=False)
            edit=Gtk.Button(icon_name='document-edit-symbolic',tooltip_text='编辑名称和成员',valign=Gtk.Align.CENTER)
            edit.connect('clicked',lambda _,n=name:self.edit_group(n));row.add_suffix(edit);row.set_activatable_widget(edit)
            for icon,offset in [('go-up-symbolic',-1),('go-down-symbolic',1)]:
                button=Gtk.Button(icon_name=icon,tooltip_text='调整组顺序',valign=Gtk.Align.CENTER)
                button.connect('clicked',lambda _,n=name,d=offset:self.reorder_group(n,d));row.add_suffix(button)
            delete=Gtk.Button(icon_name='user-trash-symbolic',tooltip_text='解散组，保留功能',valign=Gtk.Align.CENTER)
            delete.connect('clicked',lambda _,n=name:self.remove_group(n));row.add_suffix(delete);groups.add(row)
        add=Gtk.Button(label='添加功能组',halign=Gtk.Align.START,margin_top=12);add.connect('clicked',lambda _:self.edit_group());groups.add(add)
        funcs=Adw.PreferencesGroup(title='功能',description='短按选择，旋转调节。继承的功能也能单独修改步长和分组。');page.add(funcs)
        for identifier in identifiers:
            a=self.effective_action(p,identifier);inherited=identifier not in p['menu']
            status=KIND_NAMES[KINDS.index(a['kind'])]
            if a['kind']=='shortcut':status+=' · '+(a.get('ccw','')+' / '+a.get('cw','') if a.get('ccw') and a.get('cw') else '未配置快捷键')
            if a.get('group'):status+=' · '+a['group']
            if inherited:status+=' · '+('应用专用设置' if identifier in p.get('overrides',{}) else '继承全局')
            row=Adw.ActionRow(title=a['name'],subtitle=status,use_markup=False)
            edit=Gtk.Button(icon_name='document-edit-symbolic',tooltip_text='编辑功能',valign=Gtk.Align.CENTER)
            edit.connect('clicked',lambda _,i=identifier:self.edit_action(i));row.add_suffix(edit);row.set_activatable_widget(edit)
            if not inherited:
                for icon,offset in [('go-up-symbolic',-1),('go-down-symbolic',1)]:
                    button=Gtk.Button(icon_name=icon,tooltip_text='调整顺序',valign=Gtk.Align.CENTER)
                    button.connect('clicked',lambda _,i=identifier,d=offset:self.reorder(i,d));row.add_suffix(button)
                delete=Gtk.Button(icon_name='user-trash-symbolic',tooltip_text='移除功能',valign=Gtk.Align.CENTER)
                delete.connect('clicked',lambda _,i=identifier:self.remove_action(i));row.add_suffix(delete)
            funcs.add(row)
        add=Gtk.Button(label='添加功能',halign=Gtk.Align.START,margin_top=12);add.connect('clicked',lambda _:self.edit_action(None));funcs.add(add)
        if p['id']!='global':
            remove=Gtk.Button(label='删除此应用配置',halign=Gtk.Align.START,margin_top=18,css_classes=['destructive-action'])
            remove.connect('clicked',lambda _:self.remove_profile())
            group=Adw.PreferencesGroup();group.add(remove);page.add(group)
        self.loading=False

    def effective_ids(self,p):return list(dict.fromkeys((self.draft['profiles'][0]['menu'] if p.get('inherit_global') else [])+p['menu']))

    def effective_action(self,p,identifier):
        base=next(a for a in self.draft['actions'] if a['id']==identifier)
        return dict(base,**p.get('overrides',{}).get(identifier,{}))

    def update_field(self,record,key,value):record[key]=value;self.changed()

    def set_matches(self,p,key,text):p['match'][key]=[v.strip() for v in text.split(',') if v.strip()];self.changed()

    def inherit_changed(self,p,value):
        p['inherit_global']=value;ids=self.effective_ids(p)
        if ids and p['default_action'] not in ids:p['default_action']=ids[0]
        self.changed();GLib.idle_add(lambda:self.render_profile() or GLib.SOURCE_REMOVE)

    def add_profile(self):
        identifier='app_'+uuid.uuid4().hex[:8]
        self.draft['profiles'].append(dict(id=identifier,name='新应用',match=dict(desktop_ids=[],wm_classes=[]),
            inherit_global=True,remember=True,default_action=self.draft['profiles'][0]['default_action'],menu=[],overrides={}))
        self.profile_id=identifier;self.changed();self.refresh_profiles();self.pick_app()

    def remove_profile(self):
        p=self.current();self.draft['profiles'].remove(p);self.profile_id='global';self.changed();self.refresh_profiles()

    def pick_app(self):
        dialog=Adw.Dialog(title='选择应用',content_width=560,content_height=560)
        toolbar=Adw.ToolbarView();toolbar.add_top_bar(Adw.HeaderBar())
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12,margin_top=12,margin_start=18,margin_end=18,margin_bottom=18)
        search=Gtk.SearchEntry(placeholder_text='应用名称');box.append(search)
        listing=Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,css_classes=['boxed-list'])
        scroll=Gtk.ScrolledWindow(vexpand=True);scroll.set_child(listing);box.append(scroll);toolbar.set_content(box);dialog.set_child(toolbar)
        applications=sorted((a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id()),key=lambda a:a.get_display_name().casefold())
        for app in applications:
            row=Adw.ActionRow(title=app.get_display_name(),subtitle=app.get_id(),activatable=True,use_markup=False)
            row.search=(app.get_display_name()+' '+app.get_id()).casefold()
            row.connect('activated',lambda _,a=app:self.choose_app(a,dialog));listing.append(row)
        listing.set_filter_func(lambda row:not search.get_text() or search.get_text().casefold() in row.search)
        search.connect('search-changed',lambda _:listing.invalidate_filter());dialog.present(self.window)

    def choose_app(self,app,dialog):
        wm=app.get_startup_wm_class() if hasattr(app,'get_startup_wm_class') else None
        self.set_application_match([app.get_id()],[wm] if wm else [],app.get_display_name())
        dialog.close()

    def set_application_match(self,desktops,classes,name=None):
        p=self.current()
        existing=next((other for other in self.draft['profiles'] if other['id'] not in ['global',p['id']] and
            (any(d.casefold() in [v.casefold() for v in other['match'].get('desktop_ids',[])] for d in desktops) or
             any(c.casefold() in [v.casefold() for v in other['match'].get('wm_classes',[])] for c in classes))),None)
        if existing:
            if p['id'].startswith('app_') and not p['menu'] and not any(p['match'].values()):self.draft['profiles'].remove(p);self.changed()
            self.profile_id=existing['id'];self.refresh_profiles();self.toast('已打开该应用现有的配置');return
        p['match']=dict(desktop_ids=desktops,wm_classes=classes)
        if name:p['name']=name
        self.changed();self.refresh_profiles()

    def use_foreground(self):
        try:context=json.loads(self.remote('GetState')[0]).get('last_foreground',{})
        except GLib.Error:self.toast('控制器尚未运行');return
        if not context.get('app_id') and not context.get('wm_class'):self.toast('尚未记录前台应用，请先切到目标应用再回来');return
        self.set_application_match([context['app_id']] if context.get('app_id') else [],[context['wm_class']] if context.get('wm_class') else [])

    def reorder(self,identifier,offset):
        menu=self.current()['menu'];index=menu.index(identifier);other=index+offset
        if 0<=other<len(menu):menu[index],menu[other]=menu[other],menu[index];self.changed();self.render_profile()

    def set_action_group(self,config,p,identifier,name):
        if p['id']=='global' or identifier in p['menu']:
            next(a for a in config['actions'] if a['id']==identifier)['group']=name
        else:p.setdefault('overrides',{}).setdefault(identifier,{})['group']=name

    def reorder_group(self,name,offset):
        p=self.current();names=group_names(self.draft,p);index=names.index(name);other=index+offset
        if 0<=other<len(names):
            names[index],names[other]=names[other],names[index];p['groups']=names;self.changed();self.render_profile()

    def remove_group(self,name):
        p=self.current()
        for identifier in self.effective_ids(p):
            if self.effective_action(p,identifier).get('group','')==name:self.set_action_group(self.draft,p,identifier,'')
        p['groups']=[g for g in group_names(self.draft,p) if g!=name]
        self.changed();self.render_profile()

    def commit_group(self,old_name,name,members):
        name=name.strip();p=self.current();names=group_names(self.draft,p)
        if not name:raise ValueError('请输入功能组名称')
        if name!=old_name and name in names:raise ValueError('已有同名功能组，请编辑现有组')
        candidate=copy.deepcopy(self.draft);cp=next(v for v in candidate['profiles'] if v['id']==p['id'])
        for identifier in self.effective_ids(p):
            previous=self.effective_action(p,identifier).get('group','')
            if identifier in members:self.set_action_group(candidate,cp,identifier,name)
            elif old_name and previous==old_name:self.set_action_group(candidate,cp,identifier,'')
        cp['groups']=[name if g==old_name else g for g in names]
        if name not in cp['groups']:cp['groups'].append(name)
        validate_config(candidate);self.draft=candidate;self.changed();self.render_profile()

    def edit_group(self,name=None):
        p=self.current();members={i for i in self.effective_ids(p) if name and self.effective_action(p,i).get('group','')==name}
        dialog=Adw.Dialog(title='编辑功能组' if name else '添加功能组',content_width=640,content_height=640)
        toolbar=Adw.ToolbarView();header=Adw.HeaderBar();apply=Gtk.Button(label='完成',css_classes=['suggested-action']);header.pack_end(apply);toolbar.add_top_bar(header)
        page=Adw.PreferencesPage();toolbar.set_content(page);dialog.set_child(toolbar)
        general=Adw.PreferencesGroup();page.add(general);field=entry(general,'功能组名称',name or '',lambda _:None)
        choices=Adw.PreferencesGroup(title='组内功能',description='勾选要加入此组的功能；从其他组勾选会移到本组。');page.add(choices)
        for identifier in self.effective_ids(p):
            a=self.effective_action(p,identifier);row=Adw.ActionRow(title=a['name'],subtitle=a.get('group','') or '未分组',use_markup=False)
            check=Gtk.CheckButton(active=identifier in members,valign=Gtk.Align.CENTER)
            check.connect('toggled',lambda button,i=identifier:members.add(i) if button.get_active() else members.discard(i))
            row.add_suffix(check);row.set_activatable_widget(check);choices.add(row)
        def commit(_):
            try:self.commit_group(name,field.get_text(),members)
            except (ValueError,TypeError,KeyError) as error:self.toast(error);return
            dialog.close()
        apply.connect('clicked',commit);dialog.present(self.window)

    def remove_action(self,identifier):
        p=self.current()
        if p['id']=='global' and len(p['menu'])==1:self.toast('至少保留一项全局功能');return
        p['menu'].remove(identifier);p.get('overrides',{}).pop(identifier,None)
        for profile in self.draft['profiles']:
            ids=self.effective_ids(profile)
            if ids and profile.get('default_action') not in ids:profile['default_action']=ids[0]
        self.changed();self.render_profile()

    def edit_action(self,identifier):
        p=self.current();new=identifier is None
        a=action('function_'+uuid.uuid4().hex[:8],'新功能','shortcut',ccw='',cw='') if new else copy.deepcopy(self.effective_action(p,identifier))
        dialog=Adw.Dialog(title='添加功能' if new else '编辑功能',content_width=660,content_height=690)
        toolbar=Adw.ToolbarView();header=Adw.HeaderBar();apply=Gtk.Button(label='完成',css_classes=['suggested-action']);header.pack_end(apply);toolbar.add_top_bar(header)
        page=Adw.PreferencesPage();toolbar.set_content(page);dialog.set_child(toolbar)
        general=Adw.PreferencesGroup();page.add(general)
        entry(general,'功能名称',a['name'],lambda value:a.update(name=value))
        entry(general,'菜单分组（空为不分组）',a.get('group',''),lambda value:a.update(group=value))
        extra=Adw.PreferencesGroup(title='动作设置');page.add(extra)
        def rebuild():
            nonlocal extra
            page.remove(extra);extra=Adw.PreferencesGroup(title='动作设置');page.add(extra)
            kind=a['kind']
            discrete=kind in ['apps','workspace','scroll','shortcut']
            spin(extra,'每格步长',a.get('step',1),lambda value:a.update(step=value),1 if discrete else .01,100,1 if discrete else .1)
            spin(extra,'按住旋转步长',a.get('fine_step',1),lambda value:a.update(fine_step=value),1 if discrete else .01,100,1 if discrete else .1)
            if kind=='shortcut':
                for field,title in [('ccw','逆时针快捷键'),('cw','顺时针快捷键')]:
                    row=entry(extra,title,a.get(field,''),lambda value,k=field:a.update({k:value}))
                    button=Gtk.Button(label='录制',valign=Gtk.Align.CENTER)
                    button.connect('clicked',lambda _,r=row,b=button:self.begin_record(r,b));row.add_suffix(button)
            elif kind=='brightness':
                targets=['focused','internal','AOC'];names=['跟随前台窗口所在屏幕','内置屏幕','AOC 外接显示器']
                try:displays=monitors(self.bus)
                except GLib.Error:displays=[]
                for m in displays:
                    if m['internal']:continue
                    target={k:m[k] for k in ['mfg','model','sn'] if m[k]}
                    if target not in targets:targets.append(target);names.append(m['mfg']+' '+m['model']+(' · '+m['sn'] if m['sn'] else ''))
                selected=targets.index(a.get('target','focused')) if a.get('target','focused') in targets else 0
                combo(extra,'目标显示器',names,selected,lambda index:a.update(target=targets[index]))
            elif kind in ['mpris','mpv']:
                operations=['seek','volume','rate']+(['frame'] if kind=='mpv' else [])
                labels=['播放进度（秒）','播放器音量（百分点）','播放倍速']+(['逐帧'] if kind=='mpv' else [])
                selected=operations.index(a.get('operation','seek')) if a.get('operation','seek') in operations else 0
                a['operation']=operations[selected];combo(extra,'播放器功能',labels,selected,lambda index:a.update(operation=operations[index]))
                if kind=='mpris':entry(extra,'播放器名称（空为前台播放器）',a.get('player',''),lambda text:a.update(player=text))
            elif kind in ['apps','workspace']:
                extra.set_description('每格一个候选；步长为整数。应用停转 0.5 秒后确认，按下可立即确认。')
            elif kind=='scroll':extra.set_description('发送普通滚轮，作用于当前指针位置；不会移动指针。')
            else:extra.set_description('音量步长以百分点计；普通旋转与按住微调分别设置。')
        def change_kind(index):
            a['kind']=KINDS[index]
            if a['kind'] in ['volume','brightness']:a.update(step=2,fine_step=1)
            elif a['kind'] in ['apps','workspace','scroll','shortcut']:a.update(step=1,fine_step=1)
            else:a.update(step=5,fine_step=1,operation='seek')
            rebuild()
        combo(general,'动作类型',list(KIND_NAMES),KINDS.index(a['kind']),change_kind);rebuild()
        self.recording=None
        controller=Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        controller.connect('key-pressed',self.record_key);dialog.add_controller(controller)
        def commit(_):
            try:
                candidate=copy.deepcopy(self.draft)
                cp=next(profile for profile in candidate['profiles'] if profile['id']==p['id'])
                if new:candidate['actions'].append(a);cp['menu'].append(a['id']);cp.setdefault('default_action',a['id'])
                elif p['id']=='global' or identifier in p['menu']:
                    candidate['actions'][next(i for i,v in enumerate(candidate['actions']) if v['id']==identifier)]=a
                else:cp.setdefault('overrides',{})[identifier]={k:v for k,v in a.items() if k!='id'}
                validate_config(candidate)
            except (ValueError,TypeError,KeyError) as error:self.toast(error);return
            self.draft=candidate;self.recording=None;dialog.close();self.changed();self.render_profile()
        apply.connect('clicked',commit);dialog.present(self.window)

    def begin_record(self,row,button):
        if self.recording:self.recording[1].set_label('录制')
        self.recording=(row,button);button.set_label('请按快捷键');row.grab_focus()

    def record_key(self,controller,keyval,keycode,state):
        if not self.recording:return False
        row,button=self.recording
        if keyval==Gdk.KEY_Escape:button.set_label('录制');self.recording=None;return True
        if keyval in [Gdk.KEY_Control_L,Gdk.KEY_Control_R,Gdk.KEY_Shift_L,Gdk.KEY_Shift_R,Gdk.KEY_Alt_L,Gdk.KEY_Alt_R,Gdk.KEY_Super_L,Gdk.KEY_Super_R]:return True
        text=Gtk.accelerator_name(keyval,state&Gtk.accelerator_get_default_mod_mask())
        try:parse_shortcut(text)
        except ValueError as error:self.toast(error);return True
        row.set_text(text);button.set_label('录制');self.recording=None;return True

    def options(self):
        dialog=Adw.Dialog(title='全局选项',content_width=600,content_height=600)
        toolbar=Adw.ToolbarView();header=Adw.HeaderBar();done=Gtk.Button(label='完成');done.connect('clicked',lambda _:dialog.close());header.pack_end(done);toolbar.add_top_bar(header)
        page=Adw.PreferencesPage();group=Adw.PreferencesGroup();page.add(group);toolbar.set_content(page);dialog.set_child(toolbar)
        switch(group,'反转旋转方向',self.draft['input'].get('invert',False),lambda value:self.update_field(self.draft['input'],'invert',value))
        spin(group,'长按打开设置（秒）',self.draft['input']['long_press_seconds'],lambda value:self.update_field(self.draft['input'],'long_press_seconds',value),.1,3,.1)
        spin(group,'音量上限（%）',self.draft.get('audio_max_percent',100),lambda value:self.update_field(self.draft,'audio_max_percent',value),1,153,1)
        spin(group,'调节提示停留（秒）',self.draft['ui']['hud_timeout'],lambda value:self.update_field(self.draft['ui'],'hud_timeout',value),.5,60,.5)
        spin(group,'菜单超时（秒）',self.draft['ui']['menu_timeout'],lambda value:self.update_field(self.draft['ui'],'menu_timeout',value),.5,60,.5)
        entry(group,'mpv 本地连接路径',self.draft['mpv_socket'],lambda value:self.update_field(self.draft,'mpv_socket',value))
        group.set_description('调节提示收起后功能保留。mpv 需以 --input-ipc-server 指定同一路径。')
        dialog.present(self.window)

    def save(self):
        try:
            validate_config(self.draft);text=json.dumps(self.draft,ensure_ascii=False)
            try:
                success,error=self.remote('SaveConfig','(s)',(text,))
                if not success:raise ValueError(error)
            except GLib.Error as error:
                if 'ServiceUnknown' not in str(error) and 'NameHasNoOwner' not in str(error):raise
                atomic_save(self.config_path,self.draft)
            self.dirty=False;self.save_button.set_sensitive(False);self.refresh_profiles();self.toast('已保存，配置立即生效');return True
        except (ValueError,TypeError,KeyError,OSError,GLib.Error) as error:self.toast('没有保存：'+str(error));return False

    def file_dialog(self,operation):
        chooser=Gtk.FileDialog(title='导入配置' if operation=='import' else '导出配置',initial_name='asus-dial-config.json' if operation=='export' else None)
        def complete(dialog,result):
            try:
                file=dialog.open_finish(result) if operation=='import' else dialog.save_finish(result)
                path=file.get_path()
                if not path:raise ValueError('请选择本地文件')
                if operation=='import':
                    new=migrate_config(json.loads(Path(path).read_text()));self.draft=new;self.profile_id='global';self.changed();self.refresh_profiles();self.toast('已导入草稿，点击保存后生效')
                else:validate_config(self.draft);atomic_save(path,self.draft);self.toast('已导出当前配置')
            except GLib.Error as error:
                if not error.matches(Gtk.dialog_error_quark(),Gtk.DialogError.DISMISSED):self.toast(error)
            except (OSError,ValueError,TypeError,KeyError) as error:self.toast(error)
        if operation=='import':chooser.open(self.window,None,complete)
        else:chooser.save(self.window,None,complete)
