//! Native material updates happen on policy changes, never on move/resize events.
use crate::{
    effects_policy::EffectPolicy,
    platform::{self, NativeBackdropStatus},
};
use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};
use tauri::{AppHandle, Manager, WebviewWindow};

#[derive(Default)]
pub struct EffectsState(Mutex<Effects>);

#[derive(Default)]
struct Effects {
    // Start solid until the main webview restores its saved preference.
    enabled: bool,
    // Labels may be reused while work for the previous window is still queued.
    generations: HashMap<String, Arc<()>>,
    policies: HashMap<String, EffectPolicy>,
    statuses: HashMap<String, NativeBackdropStatus>,
}

pub fn status(window: &WebviewWindow) -> NativeBackdropStatus {
    let state = window.state::<EffectsState>();
    let Ok(state) = state.0.lock() else {
        return NativeBackdropStatus::unavailable(
            platform::current_platform(),
            "Effects state unavailable",
        );
    };
    let mut status = state
        .statuses
        .get(window.label())
        .cloned()
        .unwrap_or_else(NativeBackdropStatus::pending);
    status.enabled = state.enabled;
    status
}

pub fn set_enabled(app: &AppHandle, enabled: bool) -> Result<(), String> {
    app.state::<EffectsState>()
        .0
        .lock()
        .map_err(|e| e.to_string())?
        .enabled = enabled;
    refresh_all(app);
    Ok(())
}

pub fn refresh_all(app: &AppHandle) {
    for window in app.webview_windows().into_values() {
        refresh(&window);
    }
}

pub fn retry_all(app: &AppHandle) {
    // An explicit refresh may retry a transient native failure. Ordinary
    // focus/theme notifications keep the cache, including failures, so they
    // cannot repeatedly rebuild the material on an unsupported platform.
    for window in app.webview_windows().into_values() {
        refresh_with_retry(&window, true);
    }
}

pub fn configure(window: &WebviewWindow) {
    let app = window.app_handle().clone();
    let label = window.label().to_owned();
    let generation = Arc::new(());
    if let Ok(mut state) = app.state::<EffectsState>().0.lock() {
        state.generations.insert(label.clone(), generation.clone());
        state.policies.remove(&label);
        state.statuses.remove(&label);
    } else {
        return;
    }
    refresh_generation(window, generation.clone(), false);
    window.on_window_event(move |event| match event {
        tauri::WindowEvent::Focused(_) | tauri::WindowEvent::ThemeChanged(_) => {
            if let Some(window) = app.get_webview_window(&label) {
                refresh_generation(&window, generation.clone(), false);
            }
        }
        tauri::WindowEvent::Destroyed => {
            if let Ok(mut state) = app.state::<EffectsState>().0.lock() {
                if is_current(&state, &label, &generation) {
                    state.generations.remove(&label);
                    state.policies.remove(&label);
                    state.statuses.remove(&label);
                }
            }
        }
        _ => {}
    });
}

fn refresh(window: &WebviewWindow) {
    refresh_with_retry(window, false);
}

fn refresh_with_retry(window: &WebviewWindow, force: bool) {
    let generation = window
        .state::<EffectsState>()
        .0
        .lock()
        .ok()
        .and_then(|state| state.generations.get(window.label()).cloned());
    if let Some(generation) = generation {
        refresh_generation(window, generation, force);
    }
}

fn is_current(state: &Effects, label: &str, generation: &Arc<()>) -> bool {
    state
        .generations
        .get(label)
        .map(|current| Arc::ptr_eq(current, generation))
        .unwrap_or(false)
}

fn refresh_generation(window: &WebviewWindow, generation: Arc<()>, force: bool) {
    let owned = window.clone();
    let result = window.run_on_main_thread(move || {
        if owned
            .app_handle()
            .get_webview_window(owned.label())
            .is_none()
        {
            return;
        }
        let (reduce_transparency, high_contrast) = system_preferences();
        let state = owned.state::<EffectsState>();
        let policy = {
            let Ok(mut state) = state.0.lock() else {
                return;
            };
            if !is_current(&state, owned.label(), &generation) {
                return;
            }
            let next = EffectPolicy {
                enabled: state.enabled,
                reduce_transparency,
                high_contrast,
            };
            if !force && state.policies.get(owned.label()) == Some(&next) {
                return;
            }
            state.policies.insert(owned.label().to_owned(), next);
            next
        };
        // Remove the previous material before creating its replacement. Native
        // calls occur outside the state lock and exclusively on the UI thread.
        platform::clear_native_backdrop(&owned);
        let mut status = if policy.allows_material() {
            platform::apply_native_backdrop(&owned)
        } else {
            NativeBackdropStatus::unavailable(
                platform::current_platform(),
                if !policy.enabled {
                    "Disabled by application preference"
                } else {
                    "Solid background required by system accessibility preferences"
                },
            )
        };
        status.enabled = policy.enabled;
        status.reduced_transparency = policy.reduce_transparency;
        status.high_contrast = policy.high_contrast;
        if let Ok(mut state) = state.0.lock() {
            if !is_current(&state, owned.label(), &generation) {
                return;
            }
            state.statuses.insert(owned.label().to_owned(), status);
        }
        let _ = owned.eval("window.dispatchEvent(new Event('native-backdrop-changed'))");
    });
    if let Err(error) = result {
        eprintln!("failed to schedule native material update: {error}");
    }
}

#[cfg(target_os = "macos")]
fn system_preferences() -> (bool, bool) {
    let workspace = objc2_app_kit::NSWorkspace::sharedWorkspace();
    (
        workspace.accessibilityDisplayShouldReduceTransparency(),
        workspace.accessibilityDisplayShouldIncreaseContrast(),
    )
}

#[cfg(target_os = "windows")]
fn system_preferences() -> (bool, bool) {
    use windows::UI::ViewManagement::{AccessibilitySettings, UISettings};
    // If the preference cannot be queried, choose the solid fallback.
    let transparency = UISettings::new()
        .and_then(|s| s.AdvancedEffectsEnabled())
        .unwrap_or(false);
    let contrast = AccessibilitySettings::new()
        .and_then(|s| s.HighContrast())
        .unwrap_or(true);
    (!transparency, contrast)
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn system_preferences() -> (bool, bool) {
    (true, false)
}

#[cfg(target_os = "macos")]
pub fn observe_preferences(app: &AppHandle) {
    macos_observer::install(app);
}

#[cfg(not(target_os = "macos"))]
pub fn observe_preferences(_app: &AppHandle) {}

#[cfg(target_os = "macos")]
mod macos_observer {
    use super::*;
    use block2::RcBlock;
    use objc2::{rc::Retained, runtime::ProtocolObject};
    use objc2_app_kit::{NSWorkspace, NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification};
    use objc2_foundation::{
        NSNotification, NSNotificationCenter, NSObjectProtocol, NSOperationQueue,
    };
    use std::{cell::RefCell, ptr::NonNull};

    struct Observer {
        center: Retained<NSNotificationCenter>,
        token: Retained<ProtocolObject<dyn NSObjectProtocol>>,
    }
    impl Drop for Observer {
        fn drop(&mut self) {
            // Both registration and this thread-local owner live on the AppKit thread.
            unsafe {
                self.center.removeObserver((*self.token).as_ref());
            }
        }
    }
    thread_local! { static OBSERVER: RefCell<Option<Observer>> = const { RefCell::new(None) }; }

    pub fn install(app: &AppHandle) {
        assert!(objc2::MainThreadMarker::new().is_some());
        OBSERVER.with(|slot| {
            if slot.borrow().is_some() {
                return;
            }
            let app = app.clone();
            let block =
                RcBlock::new(move |_notification: NonNull<NSNotification>| refresh_all(&app));
            let center = NSWorkspace::sharedWorkspace().notificationCenter();
            // The center retains the block; delivery is explicitly on the main
            // queue. No accessibility permissions or preferences are modified.
            let token = unsafe {
                center.addObserverForName_object_queue_usingBlock(
                    Some(NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification),
                    None,
                    Some(&NSOperationQueue::mainQueue()),
                    &block,
                )
            };
            *slot.borrow_mut() = Some(Observer { center, token });
        });
    }
}
