# SPDX-License-Identifier: MIT
"""Consume OpenWheel's existing session D-Bus API; never open hidraw here."""
from gi.repository import Gio

NAME = 'org.asus.dial'
PATH = '/org/asus/dial'

class OpenWheelInput:
    def __init__(self, connection, feed, readiness):
        self.connection = connection
        self.feed = feed
        self.readiness = readiness
        self.down = False
        self.subscriptions = [connection.signal_subscribe(NAME, NAME, signal, PATH,
            None, Gio.DBusSignalFlags.NONE, self.on_signal) for signal in ['Rotate', 'Press']]
        self.watch = Gio.bus_watch_name_on_connection(connection, NAME, Gio.BusNameWatcherFlags.NONE,
            self.appeared, self.vanished)

    def on_signal(self, connection, sender, path, interface, signal, parameters):
        value = parameters.unpack()[0]
        if signal == 'Press':
            self.down = bool(value)
            self.feed(self.down, 0)
        else:
            self.feed(self.down, value)

    def appeared(self, *args):
        self.down = False
        self.readiness(True)

    def vanished(self, *args):
        self.down = False
        self.readiness(False)

    def close(self):
        Gio.bus_unwatch_name(self.watch)
        for subscription in self.subscriptions:
            self.connection.signal_unsubscribe(subscription)
