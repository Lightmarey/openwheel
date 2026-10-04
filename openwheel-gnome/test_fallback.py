# SPDX-License-Identifier: MIT
"""Delayed focus after releasing Alt must preserve the app-switch action."""
import unittest
from unittest.mock import patch
from core import Profiles,default_config
from service import Controller,DryInput

class FallbackTests(unittest.TestCase):
    def test_delayed_focus_then_external_focus(self):
        controller=Controller.__new__(Controller)
        controller.bridge=False;controller.locked=False;controller.connection=None;controller.input=DryInput()
        controller.sources={};controller.fallback_pending=True;controller.fallback_focus_until=0.;controller.fallback_latched_wm=None
        controller.profiles=Profiles(default_config());controller.profiles.select('apps')
        controller.update_context=controller.profiles.update_context
        timers={};controller.later=lambda key,_delay,callback:timers.update({key:callback})
        wm=['chatgpt']
        with patch('service.backends.call',side_effect=lambda *a,**k:(wm[0],)),patch('service.time.monotonic',return_value=100):
            self.assertTrue(controller.commit_switch())
            self.assertEqual(controller.profiles.selected,'apps')
            wm[0]='zen';timers['fallback-focus']()
            self.assertEqual(controller.profiles.selected,'apps')
        with patch('service.backends.call',side_effect=lambda *a,**k:(wm[0],)),patch('service.time.monotonic',return_value=102):
            controller.fallback_context();self.assertEqual(controller.profiles.selected,'apps')
            wm[0]='blender';controller.fallback_context()
            self.assertEqual(controller.profiles.current['id'],'blender')
            self.assertEqual(controller.profiles.selected,'blender_zoom')

if __name__=='__main__':unittest.main(verbosity=2)
