//! Application title gestures; explicit buttons retain their existing commands.
use crate::titlebar_session::{Capabilities, Registry};
use std::sync::Arc;
use tauri::Manager;
pub type TitlebarState = Arc<Registry>;

#[derive(Clone, Copy)]
enum Gesture {
    Drag,
    DoubleClick,
}

pub fn register(window: &tauri::WebviewWindow, capabilities: Capabilities) -> Result<(), String> {
    let registry = window
        .try_state::<TitlebarState>()
        .ok_or("Application titlebar is unavailable")?
        .inner()
        .clone();
    let label = window.label().to_owned();
    let session = registry.register(&label, capabilities)?;
    let retired_registry = registry.clone();
    let retired_session = session.clone();
    let retired_label = label.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::Destroyed) {
            retired_registry.retire(&retired_label, &retired_session);
        }
    });
    let owned = window.clone();
    // Dynamic windows may be created off the UI thread. Bind their actual
    // dispatcher-owned handle there; an unbound session rejects gestures.
    window
        .run_on_main_thread(move || {
            if registry.current(&label, &session) {
                if let Ok(identity) = native_identity(&owned) {
                    session.bind(identity);
                }
            }
        })
        .map_err(|e| e.to_string())
}

pub fn register_configured(window: &tauri::WebviewWindow) -> Result<(), String> {
    let config = window
        .app_handle()
        .config()
        .app
        .windows
        .iter()
        .find(|config| config.label == window.label())
        .ok_or("Window has no matching configuration")?;
    register(
        window,
        Capabilities {
            minimizable: config.minimizable,
            maximizable: config.maximizable,
        },
    )
}

#[tauri::command]
pub async fn native_titlebar_double_click(window: tauri::WebviewWindow) -> Result<(), String> {
    dispatch(window, Gesture::DoubleClick).await
}
#[tauri::command]
pub async fn native_titlebar_drag(window: tauri::WebviewWindow) -> Result<(), String> {
    dispatch(window, Gesture::Drag).await
}
async fn dispatch(window: tauri::WebviewWindow, gesture: Gesture) -> Result<(), String> {
    let registry = window
        .try_state::<TitlebarState>()
        .ok_or("Application titlebar is unavailable")?
        .inner()
        .clone();
    let session = registry.get(window.label())?;
    let owned = window.clone();
    let (send, mut receive) = tauri::async_runtime::channel(1);
    window
        .run_on_main_thread(move || {
            let result = (|| {
                if !registry.current(owned.label(), &session) {
                    return Err("Titlebar window session was replaced".into());
                }
                let identity = native_identity(&owned)?;
                if !session.owns(identity) {
                    return Err("Titlebar window instance was replaced or is unavailable".into());
                }
                perform(&owned, session.capabilities, gesture)
            })();
            // Never block the UI thread when the caller has gone away.
            let _ = send.try_send(result);
        })
        .map_err(|e| e.to_string())?;
    receive
        .recv()
        .await
        .ok_or("Titlebar callback was cancelled")?
}

#[cfg(target_os = "macos")]
fn native_identity(window: &tauri::WebviewWindow) -> Result<usize, String> {
    objc2::MainThreadMarker::new().ok_or("Titlebar gesture requires the main thread")?;
    let handle = window.ns_window().map_err(|e| e.to_string())?;
    if handle.is_null() {
        return Err("Native window is unavailable".into());
    }
    Ok(handle as usize)
}
#[cfg(not(target_os = "macos"))]
fn native_identity(_window: &tauri::WebviewWindow) -> Result<usize, String> {
    Ok(1)
}

#[cfg(target_os = "macos")]
fn perform(
    window: &tauri::WebviewWindow,
    capabilities: Capabilities,
    gesture: Gesture,
) -> Result<(), String> {
    use crate::titlebar_policy::{action, capability, Action};
    use objc2_app_kit::{NSWindow, NSWindowButton, NSWindowStyleMask};
    use objc2_foundation::{NSString, NSUserDefaults};
    let identity = native_identity(window)?;
    // SAFETY: the dispatcher owns this live NSWindow; this UI-thread borrow
    // does not escape. No label lookup, cached native dereference or new owner.
    let native = unsafe { &*(identity as *const NSWindow) };
    let style = native.styleMask();
    let fullscreen = style.contains(NSWindowStyleMask::FullScreen)
        || window.is_fullscreen().map_err(|e| e.to_string())?;
    let sheet = native.attachedSheet().is_some();
    if let Gesture::Drag = gesture {
        if crate::titlebar_policy::allows_drag(fullscreen, sheet, native.isMovable()) {
            window.start_dragging().map_err(|e| e.to_string())?;
        }
        return Ok(());
    }
    let can_minimize = capability(
        style.contains(NSWindowStyleMask::Miniaturizable)
            && window.is_minimizable().map_err(|e| e.to_string())?,
        native
            .standardWindowButton(NSWindowButton::MiniaturizeButton)
            .map(|button| button.isEnabled()),
        capabilities.minimizable,
    );
    // Borderless windows may have no Zoom button; absence differs from disabled.
    let can_zoom = capability(
        style.contains(NSWindowStyleMask::Resizable),
        native
            .standardWindowButton(NSWindowButton::ZoomButton)
            .map(|button| button.isEnabled()),
        capabilities.maximizable,
    );
    let preference = NSUserDefaults::standardUserDefaults()
        .stringForKey(&NSString::from_str("AppleActionOnDoubleClick"))
        .map(|value| value.to_string())
        .unwrap_or_default();
    match action(&preference, fullscreen, sheet, can_minimize, can_zoom) {
        Action::None => {}
        Action::Minimize => native.miniaturize(None),
        Action::Zoom if style.contains(NSWindowStyleMask::Titled) => native.zoom(None),
        Action::Zoom => {
            // Preserve pinned Tao's borderless frame/restore compatibility path.
            if window.is_maximized().map_err(|e| e.to_string())? {
                window.unmaximize().map_err(|e| e.to_string())?;
            } else {
                window.maximize().map_err(|e| e.to_string())?;
            }
        }
    }
    Ok(())
}
#[cfg(not(target_os = "macos"))]
fn perform(
    _window: &tauri::WebviewWindow,
    _capabilities: Capabilities,
    _gesture: Gesture,
) -> Result<(), String> {
    Ok(()) // Win/Linux retain the shared plugin commands; mobile has no custom chrome.
}
