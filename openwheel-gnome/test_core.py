# SPDX-License-Identifier: MIT
import copy
from datetime import datetime
import json
import struct
import tempfile
import unittest
from pathlib import Path
from capture_reference import decode_report
from core import Gestures, Profiles, atomic_save, default_config, migrate_config, parse_shortcut, validate_config

class CoreTests(unittest.TestCase):
    def test_real_capture_before_press_and_fine_gesture(self):
        capture=Path(__file__).with_name('raw-reports.jsonl')
        gesture=Gestures();events=[];before=[];pressed=False
        for line in capture.read_text().splitlines():
            record=json.loads(line);button,delta=decode_report(bytes.fromhex(record['hex']))
            if not pressed and delta:before.append(delta)
            pressed|=button
            events+=gesture.feed(button,delta,datetime.fromisoformat(record['at']).timestamp())
        self.assertEqual(len(before),112)
        self.assertEqual(sum(abs(e[1]) for e in events if e[0]=='rotate'),162)
        # All three captured presses include rotation and must not also select a menu item.
        self.assertEqual([e[0] for e in events if e[0]!='rotate'],[])
        self.assertTrue(any(e[2] for e in events if e[0]=='rotate'))
        self.assertFalse(gesture.down)

    def test_signed_full_width_and_report_filter(self):
        for report in [1,10]:
            for delta in [-3600,-257,-256,-10,-2,-1,0,1,2,10,256,257,3600]:
                self.assertEqual(decode_report(bytes([report,0])+struct.pack('<h',delta)),(False,delta))
        self.assertIsNone(decode_report(b'\x0a\x03\x01\x00'))
        self.assertIsNone(decode_report(b'\x0b\x00\x01\x00'))
        self.assertIsNone(decode_report(b'\x0a\x00\x01'))
        self.assertEqual(decode_report(b'\x01\x81\xff\xff'),(True,-1))

    def test_long_press_once_without_release_press(self):
        gesture=Gestures();self.assertEqual(gesture.feed(True,0,100),[])
        self.assertEqual(gesture.tick(100.7),[('long',0,False)])
        self.assertEqual(gesture.tick(101),[])
        self.assertEqual(gesture.feed(False,0,101),[])
        self.assertEqual(gesture.feed(False,1,102),[('rotate',1,False)])

    def test_fine_rotation_consumes_press(self):
        g=Gestures();g.feed(True,0,0)
        self.assertEqual(g.feed(True,1,.1),[('rotate',1,True)])
        self.assertEqual(g.tick(.8),[]);self.assertEqual(g.feed(False,0,1),[])
        g.feed(True,0,2);self.assertEqual(g.feed(False,-1,2.2),[('rotate',-1,True)])
        self.assertEqual(g.feed(False,1,3),[('rotate',1,False)])

    def test_units_and_monotonic_restart(self):
        g=Gestures(units=2,invert=True)
        self.assertEqual(g.feed(False,1,1),[])
        self.assertEqual(g.feed(False,1,2),[('rotate',-1,False)])
        g.feed(True,0,3);g.reset();self.assertEqual(g.feed(False,0,4),[])

    def test_profiles_remember_and_self_caused_focus(self):
        p=Profiles(default_config());self.assertEqual(p.selected,'volume')
        p.select('apps');p.update_context({'app_id':'zen.desktop','dial_focus':True})
        self.assertEqual(p.selected,'apps');self.assertEqual(p.current['id'],'global')
        p.update_context({'app_id':'zen.desktop'});self.assertEqual(p.selected,'scroll')
        p.select('zen_zoom');p.update_context({'app_id':'zen.desktop','window_id':5});self.assertEqual(p.selected,'zen_zoom')
        p.update_context({'wm_class':'something'});self.assertEqual(p.selected,'apps')
        p.update_context({'wm_class':'ZEN'});self.assertEqual(p.selected,'zen_zoom')

    def test_groups_inherit_and_pagination(self):
        config=default_config();p=Profiles(config);p.update_context({'wm_class':'blender'})
        self.assertEqual([i['id'] for i in p.menu_items()],['volume','brightness','apps','workspace','scroll','@next'])
        self.assertEqual([i['id'] for i in p.menu_items(page=1)],['blender_zoom','@group:动画','@group:雕刻 / 绘制','@next'])
        self.assertEqual([i['id'] for i in p.menu_items(('@group:动画',))],['@back','blender_frame','blender_keyframe'])
        p.select('blender_keyframe');self.assertEqual(p.menu_location(),(1,1));self.assertEqual(p.menu_location(('@group:动画',)),(0,2))
        config=default_config();profile=next(p for p in config['profiles'] if p['id']=='blender');profile['inherit_global']=False
        custom=Profiles(config);custom.update_context({'wm_class':'blender'})
        self.assertTrue(any(i['id']=='@group:动画' for i in custom.menu_items()))
        self.assertEqual([i['id'] for i in custom.menu_items(('@group:动画',))],['@back','blender_frame','blender_keyframe'])

    def test_inherited_functions_can_join_reordered_groups(self):
        config=default_config();profile=config['profiles'][1]
        profile['groups']=['网页','系统']
        profile['overrides']={'volume':{'group':'系统'},'brightness':{'group':'系统'},'zen_zoom':{'group':'网页'},'zen_tabs':{'group':'网页'}}
        p=Profiles(config);p.update_context({'wm_class':'zen'})
        self.assertEqual([i['id'] for i in p.menu_items()],['@group:网页','apps','workspace','scroll','@group:系统'])
        self.assertEqual([i['id'] for i in p.menu_items(('@group:系统',))],['@back','volume','brightness'])
        self.assertEqual(config['actions'][0]['group'],'')
        profile['groups']=['重复','重复']
        with self.assertRaises(ValueError):validate_config(config)

    def test_accelerators_for_editor_and_modifiers(self):
        for text in ['<Control>Page_Up','Ctrl+PageUp']:
            self.assertEqual(parse_shortcut(text),['KEY_LEFTCTRL','KEY_PAGEUP'])
        self.assertEqual(parse_shortcut('Ctrl++'),['KEY_LEFTCTRL','KEY_LEFTSHIFT','KEY_EQUAL'])
        self.assertEqual(parse_shortcut('<Control>plus'),['KEY_LEFTCTRL','KEY_LEFTSHIFT','KEY_EQUAL'])
        self.assertEqual(parse_shortcut('ISO_Left_Tab'),['KEY_LEFTSHIFT','KEY_TAB'])
        with self.assertRaises(ValueError):parse_shortcut('<Control>not-a-key')
        self.assertEqual(parse_shortcut(''),[])

    def test_invalid_override_and_reference(self):
        config=default_config();config['profiles'][1]['overrides']={'volume':{'id':'hijack'}}
        with self.assertRaises(ValueError):validate_config(config)
        config=default_config();config['profiles'][1]['overrides']={'volume':{'step':float('nan')}}
        with self.assertRaises(ValueError):validate_config(config)
        config=default_config();config['profiles'][0]['default_action']='deleted'
        with self.assertRaises(ValueError):validate_config(config)

    def test_migration_retains_old_input_and_modes(self):
        old={'modes':[{'action':'wheel','name':'old','step':2}], 'invert_direction':True}
        config=migrate_config(old);self.assertEqual(config['legacy_config'],old)
        self.assertEqual(config['actions'][-1]['step'],2);self.assertTrue(config['input']['invert'])
        self.assertEqual(Profiles(config).selected,'volume')

    def test_atomic_save_and_invalid_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'config.json';atomic_save(file,default_config())
            original=file.read_bytes();bad=default_config();bad['actions'][0]['step']=-1
            with self.assertRaises(ValueError):validate_config(bad);atomic_save(file,bad)
            self.assertEqual(file.read_bytes(),original)
            self.assertEqual(list(Path(directory).iterdir()),[file])

if __name__=='__main__':unittest.main(verbosity=2)
