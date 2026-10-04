# SPDX-License-Identifier: MIT
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from evdev import ecodes
from backends import Audio,Input,brightness,select_monitor

class FakeDevice:
    def __init__(self,fail=None):self.events=[];self.fail=fail
    def write(self,type,code,value):
        if (code,value)==self.fail:self.fail=None;raise OSError('simulated disconnected device')
        self.events.append((type,code,value))
    def syn(self):pass

class BackendTests(unittest.TestCase):
    def make_input(self,fail=None):
        instance=Input.__new__(Input);instance.codes=ecodes;instance.pressed=set();instance.device=FakeDevice(fail);return instance
    def test_release_modifiers_after_key_injection_failure(self):
        device=self.make_input((ecodes.KEY_S,1))
        with self.assertRaises(OSError):device.shortcut('<Control><Shift>s')
        self.assertEqual(device.pressed,set())
        self.assertIn((ecodes.EV_KEY,ecodes.KEY_LEFTCTRL,0),device.device.events)
        self.assertIn((ecodes.EV_KEY,ecodes.KEY_LEFTSHIFT,0),device.device.events)
    def test_release_all_continues_after_one_release_failure(self):
        device=self.make_input((ecodes.KEY_LEFTCTRL,0));device.pressed={ecodes.KEY_LEFTCTRL,ecodes.KEY_LEFTSHIFT}
        with self.assertRaises(OSError):device.release_all()
        self.assertEqual(device.pressed,set())
        self.assertIn((ecodes.EV_KEY,ecodes.KEY_LEFTSHIFT,0),device.device.events)
    def test_switch_held_alt_is_released_only_by_commit(self):
        device=self.make_input();device.hold('KEY_LEFTALT');device.shortcut('<Alt><Shift>Tab',2)
        self.assertEqual(device.pressed,{ecodes.KEY_LEFTALT})
        device.release_all();self.assertEqual(device.pressed,set())
    def test_brightness_target_never_retargets_missing_aoc(self):
        inner=dict(internal=True,primary=True,mfg='AUO',model='inner',sn='',rect={'x':0,'y':0})
        outer=dict(internal=False,primary=False,mfg='AOC',model='AG273QG3R3B',sn='actual',rect={'x':1920,'y':0})
        self.assertEqual(select_monitor([inner,outer],'focused',{'monitor_rect':{'x':1920,'y':0}}),outer)
        self.assertEqual(select_monitor([inner,outer],{'mfg':'AOC','model':'AG273QG3R3B','sn':'actual'},{}),outer)
        with self.assertRaises(ValueError):select_monitor([inner],'AOC',{})
        with self.assertRaises(ValueError):select_monitor([inner],{'mfg':'AOC','sn':'actual'}, {})

    def test_audio_osd_uses_real_volume_and_mute(self):
        class Sink:
            volume=19661;muted=True
            def get_volume(self):return self.volume
            def set_volume(self,value):self.volume=value
            def push_volume(self):pass
            def get_description(self):return '测试输出'
            def get_is_muted(self):return self.muted
            def change_is_muted(self,value):self.muted=value
        class Control:
            def get_default_sink(self):return sink
            def get_vol_max_norm(self):return 65536
        sink=Sink();audio=Audio.__new__(Audio);audio.control=Control()
        result=audio.adjust(2,100)
        self.assertEqual(result['osd']['level'],sink.volume/65536)
        self.assertEqual(result['osd']['icon'],'audio-volume-low-symbolic');self.assertFalse(sink.muted)
        sink.muted=True;result=audio.adjust(-1,100)
        self.assertEqual(result['osd']['level'],0);self.assertEqual(result['osd']['icon'],'audio-volume-muted-symbolic')
        result=audio.adjust(150,100);self.assertEqual(result['osd']['level'],1)

    def test_inner_brightness_osd_uses_readback_and_connector(self):
        inner=dict(internal=True,primary=False,connector='eDP-1',rect={'x':0,'y':0})
        entry=dict(connector='eDP-1',active=True,min=5,max=504,value=337)
        def fake_call(_connection,_name,_path,interface,method,signature=None,args=()):
            if method=='SetBacklight':entry['value']=args[2];return ()
            return ((8,[dict(entry)]),)
        with patch('backends.monitors',return_value=[inner]),patch('backends.call',side_effect=fake_call):
            result=brightness(None,{'target':'internal'},1,{})
        self.assertEqual(result['osd']['connector'],'eDP-1')
        self.assertEqual(result['osd']['level'],(entry['value']-5)/499)
        self.assertEqual(result['osd']['label'],'内屏')

    def test_aoc_brightness_osd_uses_ddc_readback(self):
        outer=dict(internal=False,primary=True,connector='DP-2',mfg='AOC',model='AG273QG3R3B',sn='actual',rect={'x':1920,'y':0})
        responses=[SimpleNamespace(returncode=0,stdout='VCP 10 C 180 255',stderr=''),
                   SimpleNamespace(returncode=0,stdout='',stderr=''),
                   SimpleNamespace(returncode=0,stdout='VCP 10 C 184 255',stderr='')]
        with patch('backends.monitors',return_value=[outer]),patch('backends.subprocess.run',side_effect=responses) as run:
            result=brightness(None,{'target':'focused'},1,{'monitor_rect':{'x':1920,'y':0}})
        self.assertEqual(result['osd']['level'],184/255);self.assertEqual(result['osd']['connector'],'DP-2')
        self.assertEqual(run.call_args_list[1].args[0][-3:],['setvcp','10','183'])

if __name__=='__main__':unittest.main(verbosity=2)
