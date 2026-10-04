# Optional GNOME frontend (integration prototype)

This directory ports the locally tested ASUS Dial GNOME integration into the
OpenWheel tree. It uses **OpenWheel's C input daemon**, not another hidraw reader.
The Qt/Plasma gadget remains separate; run one action consumer at a time.

The native Shell frontend provides a compact radial menu, function groups,
application matching, GNOME volume/brightness OSD, window previews and workspace
switching. Drag the menu centre to move it. The saved position is relative to the
focused monitor's usable area; the default is the bottom right. Extension
preferences also provide four corner presets. Drawing happens on selection
changes, with no animation loop or window polling.

## Scope and reuse

Already reused:

- `openwheel-daemon`: device discovery, hidraw lifetime, D-Bus ownership and the
  existing `org.asus.dial` `Rotate(i)` / `Press(i)` interface.
- `openwheel_input.py`: a small subscriber adapting those signals to UI gestures.
  It neither discovers nor reads hardware and does not decode raw reports.

Still transitional:

- Python configuration/profile matching and gesture state, retained to preserve
  existing users' configurations and the tested press/hold behavior.
- GTK profile editor and GNOME audio/DDC/input backends.
- The historical `asus-dial` paths and `org.cachyos.AsusDial` frontend bus name.

These are **not** claimed to be a complete port of the upstream Qt controller.
The proposed next extraction is documented in [DESIGN.md](DESIGN.md): share the
existing Qt profile/group/rotation/controller core behind D-Bus, then remove the
corresponding Python model. The GNOME Shell menu/context/OSD frontend remains
native because the current Qt overlay does not provide GNOME Shell integration.

## Local installation

Verified on GNOME Shell 50 / Wayland. Other Shell versions are unverified. Requires
Python PyGObject, GTK 4, libadwaita, python-evdev, GNOME's Gvc typelib and libdbus.
External monitor brightness additionally uses ddcutil; mpv and MPRIS actions use
the player's existing IPC interfaces. Device access must already be granted to
the active user. The installer does not alter device permissions.

From the repository root:

```sh
cmake -S . -B build -DBUILD_GADGET=OFF -DBUILD_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
python3 openwheel-gnome/install-user.py --input-binary build/bin/asus_wheel
```

The installer saves a user-file backup, retains the existing configuration and
enables two session units: `openwheel-input.service` and `asus-dial.service`.
The input daemon uses `--no-notify`. Log out and back in normally to load updated
GNOME extension code; GNOME 50 cannot reload an already imported extension module.
Launch `asus-dial --settings` for profiles and functions, or use extension
preferences for menu placement. A short Dial press opens/confirms the menu;
holding while turning adjusts more finely; a long press opens settings.

Restore a backup with `python3 openwheel-gnome/rollback-user.py BACKUP_DIRECTORY`,
then log in again to load the restored extension code.

## Validation

```sh
cd openwheel-gnome
python3 -m unittest test_core test_backends test_fallback -v
gjs -m test_geometry.js
python3 run_headless.py
```

The last command starts a private software-rendered GNOME session with its own
bus, config, data and runtime directories. It exercises OpenWheel D-Bus input,
native OSD, groups, drag/save/reopen, application switching and GTK settings.
Screenshot probes exist only in that private test copy of the extension. The
installed extension exposes no screenshot, Eval or synthetic-input methods.
`capture_reference.py` is a test oracle for a recorded report fixture, not runtime
hardware code. `headless-test/` and compiled schemas are ignored by Git.

The prototype currently retains Chinese UI strings; localization, upstream
naming/packaging and shared-core migration remain work before a frontend release.
