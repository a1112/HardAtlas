# Desktop window chrome and native effects

The main window closes normally; Cmd/Ctrl-Q exits. No tray behavior was added. Explore, offline, entity-reader, taxonomy and hardware-extension status surfaces remain inside the same main shell. There is no application child-window factory. iOS keeps system-managed decorations and an opaque window.

The shell has a visible background-effects toggle. Its local preference is restored before enabling a material. System Reduce Transparency / Increase Contrast overrides the saved opt-in without overwriting it. A failed native material keeps an opaque parent surface; CSS backdrop-filter is not a substitute for the desktop material. macOS uses the public NSVisualEffectView WindowBackground material. The shared corner mask is 10 logical pixels when normal and zero when maximized/fullscreen. Windows uses Mica when available; unsupported systems use solid backgrounds.

## Reproduce automated checks

From this frontend directory, with Node >= 22.18 and a Rust toolchain:

```sh
npm run test:window-chrome
npm run test:native-effects
```

The Node suite executes the production preference controller, keyboard shortcut function and injected chrome entry script against instrumented storage/IPC/DOM. The Python suite reads the production Rust effects controller and policy verbatim, replacing only the native window/queue/preferences boundary with a host that counts material calls and queued work. It uses temporary files and removes only its own test outputs. Rust crate unit tests cover geometry/writer and corner policy behavior.

These tests do not launch an application, exercise AppKit preference notifications or measure native performance. Frontend production build and macOS cargo check are separate compile checks. No frame-rate/CPU/drag latency claim follows from them.

## Pending native acceptance on the connected MacBook Pro

- Launch the review build; verify each in-page route, light/dark and narrow layouts, with controls accessible by Tab/Space/Enter. Check close/minimize/maximize/restore/fullscreen and Cmd/Ctrl-W/M/Q, Ctrl-Cmd-F/F11.
- Drag the titlebar background; type/select in inputs, click nested controls, cancel a pointer gesture, and drag a synthetic file across the titlebar. Interactive controls and file drops must not trigger window dragging.
- Resize all eight edges/corners continuously, maximize/restore and enter/exit fullscreen. Verify the native/CSS corner masks agree with no white or transparent gap.
- Move between already configured displays at different scales when available; verify the physical-rectangle geometry and asynchronous persistence repairs remain correct.
- Toggle effects off, restart, and confirm the saved solid mode. With accessibility preferences already set (or changed by the user), confirm material removal and later opt-in restoration. This change does not automate any system settings.
- Measure drag/resize timing, CPU and frames with desktop instruments before claiming native smoothness improvements. Browser previews and instrumented tests are not hardware evidence.
