# Title gesture regression checks

Run from this src-tauri directory:

```
node --test src/project-window-chrome.test.cjs tests/test-titlebar-dispatch.cjs
python3 tests/test-titlebar-command.py
```

The combined JS fixture loads the actual locked Tauri drag.js from the local Cargo cache; it does not download dependencies. It executes production shared listeners (and WindowBase's actual legacy function) with synthetic event propagation and records the listener producer and requested IPC. App-owned titles use data-project-drag-region, so declined gestures cannot fall through to Tauri's separate data-tauri-drag-region handler. Unrelated legacy regions remain untouched. The WindowBase desktop legacy handler is disabled; its existing runtime-only mobile path and browser no-op remain.

The Rust fixture compiles the production registration, command, session registry and pure policy with Rust 1.94.1 against a queued fake host. It covers old queued calls, calls from an old dispatcher after label reuse, late old destruction, distinct child capabilities, failed scheduling, missing state and caller cancellation. It does not execute AppKit. Registration binds an actual dispatcher handle on the UI thread; command execution retains the incoming Window clone, checks the captured generation and actual handle, and never finds a replacement Window by label. The registry owns no Window or AppHandle.

macOS uses public NSUserDefaults AppleActionOnDoubleClick and NSWindow actions/capabilities for None, Minimize and Zoom. Fullscreen and attached sheets veto title gestures; nonmovable windows veto dragging. Borderless Zoom retains pinned Tao's maximize/restore compatibility path. Explicit buttons are unchanged. macOS content resize overlays are absent; the existing native Resizable style/perimeter remains. Win/Linux retain their plugin drag and eight resize directions.

HTML modal/inert checks occur at event dispatch. Native gates run before delegating start_dragging to Tauri; the subsequent dispatcher queue and OS event timing are not atomic with that check. No custom pointer tracking or currentEvent assumptions were introduced. A submitted IPC is not cancelled merely by dropping its response receiver. Tests do not validate physical dragging/resizing, real cancellation, sheet timing, frame rate, CPU, screen scale transitions, native system preferences or visuals. Native acceptance remains pending. No system preference is written.
