# Reusing OpenWheel rather than growing a second controller

## Current boundary

```text
ASUS2020 HID
  → OpenWheel C daemon (patched report decoder)
  → org.asus.dial Rotate(i)/Press(i)
  → GNOME integration controller (legacy JSON/gestures/backends)
  → native Shell menu, context, OSD and window/workspace actions
```

The duplicate Python hidraw reader and production report decoder have been
removed. The upstream signal ABI is unchanged, and the daemon patch can be
reviewed independently from this GNOME prototype. No Qt gadget is launched
alongside the GNOME action consumer.

## Next upstream split

1. Extract `Profile`, `Function`, `FunctionGroup`, `ProfileManager`,
   `RotationHandler` and the state portion of `DialController` into a core target.
   Preserve their existing files and GPL-3.0-or-later headers. Keep Qt/QML,
   `ApplicationMatcher` platform discovery and action execution outside that
   target. Both frontends should consume the same profile and selection state.
2. Expose that shared controller through a session D-Bus interface: get/save
   profiles, receive foreground context, move/confirm/back through menus, select
   functions and publish state. The existing QML gadget uses the same controller
   directly; the GNOME frontend consumes its state over D-Bus. Avoid Python Qt
   bindings or a second overlay process just to access the core.
3. Add context and action backend interfaces. The GNOME Shell extension pushes
   application ID, WM_CLASS and monitor on focus changes. It also handles native
   window/workspace actions and OSD. KDE retains its existing provider. Generic
   Wayland keyboard/wheel injection and MPRIS/mpv adapters can be shared across
   frontends instead of staying GNOME-only.
4. Extend the shared menu model to include function groups. The existing
   `FunctionGroup.functionIds` is the source of membership; return group and
   back entries from the core rather than grouping again in each frontend.
5. Migrate the old JSON through a one-time adapter, preserving global inheritance,
   per-app overrides, remembered selections, fine steps and custom bindings.
   Those fields are not all present in the current upstream schema. Keep a
   lossless original backup; use flattened profiles or additive schema fields
   agreed with the maintainer. The GTK editor then reads/saves shared profiles,
   and the transitional Python matcher/validator/menu model is removed.

## Contribution slices

- Input fix: signed reports, button-edge ordering, reconnect exit and an optional
  startup-notification flag, with C regression tests. Ready independently.
- Shared core extraction: mechanical CMake/source split preserving current KDE
  behavior, with existing rotation/profile tests.
- Headless controller D-Bus and backend interfaces: a separate behavioral patch.
- GNOME frontend: native context/OSD/menu and GTK profile editor, rebased to the
  shared core. The current prototype supplies tested behavior and screenshots,
  but duplicating its entire legacy Python profile core upstream is not the
  intended final architecture.

The radial menu drawing and centre drag handle are GNOME presentation code and
appropriately remain in the extension. There is no need to copy ProArt images,
install Fly-Pie/Kando, or create another HID driver for this UI.
