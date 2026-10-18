#[cfg(desktop)]
use tauri::Manager;
use tauri::{AppHandle, WebviewWindow};

#[tauri::command]
pub fn window_chrome_available() -> bool {
    cfg!(desktop)
}

#[tauri::command]
pub fn native_effects_status(window: WebviewWindow) -> crate::platform::NativeBackdropStatus {
    #[cfg(desktop)]
    {
        crate::effects::status(&window)
    }
    #[cfg(not(desktop))]
    {
        let _ = window;
        crate::platform::NativeBackdropStatus::unavailable(
            crate::platform::current_platform(),
            "System-managed mobile window",
        )
    }
}

#[tauri::command]
pub fn set_native_effects_enabled(app: AppHandle, enabled: bool) -> Result<(), String> {
    #[cfg(desktop)]
    {
        crate::effects::set_enabled(&app, enabled)
    }
    #[cfg(not(desktop))]
    {
        let _ = (app, enabled);
        Err("System-managed mobile window".into())
    }
}

#[tauri::command]
pub fn refresh_native_effects(app: AppHandle) {
    #[cfg(desktop)]
    crate::effects::refresh_all(&app);
    #[cfg(not(desktop))]
    let _ = app;
}

#[tauri::command]
pub fn desktop_window_action(window: WebviewWindow, action: &str) -> Result<(), String> {
    #[cfg(desktop)]
    {
        match action {
            "close" => window.close(),
            "minimize" => window.minimize(),
            "fullscreen" => {
                window.set_fullscreen(!window.is_fullscreen().map_err(|e| e.to_string())?)
            }
            "maximize" => {
                if window.is_fullscreen().map_err(|e| e.to_string())? {
                    window.set_fullscreen(false)
                } else if window.is_maximized().map_err(|e| e.to_string())? {
                    window.unmaximize()
                } else {
                    window.maximize()
                }
            }
            "quit" => {
                window.app_handle().exit(0);
                return Ok(());
            }
            _ => return Err("Unknown window action".into()),
        }
        .map_err(|e| e.to_string())
    }
    #[cfg(not(desktop))]
    {
        let _ = (window, action);
        Err("System-managed mobile window".into())
    }
}
