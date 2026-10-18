use serde::Serialize;
#[cfg(not(mobile))]
use tauri::WebviewWindow;

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct NativeBackdropStatus {
    pub platform: &'static str,
    pub effect: &'static str,
    pub applied: bool,
    pub detail: String,
    pub enabled: bool,
    pub reduced_transparency: bool,
    pub high_contrast: bool,
}

impl NativeBackdropStatus {
    pub fn pending() -> Self {
        Self::new(current_platform(), "Pending", false, "Not applied yet")
    }

    #[cfg_attr(mobile, allow(dead_code))]
    pub fn applied(platform: &'static str, effect: &'static str) -> Self {
        Self::new(platform, effect, true, "Native backdrop applied")
    }

    pub fn unavailable(platform: &'static str, detail: impl Into<String>) -> Self {
        Self::new(platform, "Unavailable", false, detail)
    }

    fn new(
        platform: &'static str,
        effect: &'static str,
        applied: bool,
        detail: impl Into<String>,
    ) -> Self {
        Self {
            platform,
            effect,
            applied,
            detail: detail.into(),
            enabled: false,
            reduced_transparency: false,
            high_contrast: false,
        }
    }
}

#[cfg(not(mobile))]
pub fn configure_window(window: &WebviewWindow) {
    apply_platform_chrome(window);
    crate::effects::configure(window);
}

pub fn current_platform() -> &'static str {
    #[cfg(target_os = "windows")]
    {
        "Windows"
    }

    #[cfg(target_os = "macos")]
    {
        "macOS"
    }

    #[cfg(target_os = "ios")]
    {
        "iOS"
    }

    #[cfg(target_os = "linux")]
    {
        "Linux"
    }

    #[cfg(not(any(
        target_os = "windows",
        target_os = "macos",
        target_os = "ios",
        target_os = "linux"
    )))]
    {
        "Unsupported"
    }
}

#[cfg(all(not(mobile), target_os = "windows"))]
fn apply_platform_chrome(window: &WebviewWindow) {
    if let Err(error) = window.set_decorations(false) {
        eprintln!("failed to apply Windows frameless chrome: {error}");
    }

    // The shared chrome plugin owns rounded/maximized/fullscreen corners.
}

#[cfg(all(not(mobile), target_os = "macos"))]
fn apply_platform_chrome(window: &WebviewWindow) {
    if let Err(error) = window.set_decorations(false) {
        eprintln!("failed to apply macOS frameless chrome: {error}");
    }
}

#[cfg(all(not(mobile), not(any(target_os = "windows", target_os = "macos"))))]
fn apply_platform_chrome(_window: &WebviewWindow) {}

#[cfg(all(not(mobile), target_os = "macos"))]
pub(crate) fn apply_native_backdrop(window: &WebviewWindow) -> NativeBackdropStatus {
    use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};
    // NSVisualEffectView uses BehindWindow blending in window-vibrancy. The
    // parent content layer owns the only corner mask (10pt, or 0 when square),
    // so this material fills its bounds through resize/maximize/fullscreen.
    match apply_vibrancy(
        window,
        NSVisualEffectMaterial::WindowBackground,
        None,
        Some(0.0),
    ) {
        Ok(()) => NativeBackdropStatus::applied("macOS", "Native window background"),
        Err(error) => NativeBackdropStatus::unavailable("macOS", error.to_string()),
    }
}

#[cfg(all(not(mobile), target_os = "macos"))]
pub(crate) fn clear_native_backdrop(window: &WebviewWindow) {
    let _ = window_vibrancy::clear_liquid_glass(window);
    let _ = window_vibrancy::clear_vibrancy(window);
}

#[cfg(all(not(mobile), target_os = "windows"))]
pub(crate) fn apply_native_backdrop(window: &WebviewWindow) -> NativeBackdropStatus {
    // Prefer the system wallpaper material. Older/restricted systems stay solid;
    // avoid the previous forced Acrylic path during live dragging/resizing.
    match window_vibrancy::apply_mica(window, None) {
        Ok(()) => NativeBackdropStatus::applied("Windows", "Mica wallpaper material"),
        Err(error) => NativeBackdropStatus::unavailable("Windows", error.to_string()),
    }
}

#[cfg(all(not(mobile), target_os = "windows"))]
pub(crate) fn clear_native_backdrop(window: &WebviewWindow) {
    let _ = window_vibrancy::clear_mica(window);
}

#[cfg(all(not(mobile), not(any(target_os = "macos", target_os = "windows"))))]
pub(crate) fn apply_native_backdrop(_window: &WebviewWindow) -> NativeBackdropStatus {
    NativeBackdropStatus::unavailable(current_platform(), "Solid background on this platform")
}

#[cfg(all(not(mobile), not(any(target_os = "macos", target_os = "windows"))))]
pub(crate) fn clear_native_backdrop(_window: &WebviewWindow) {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backdrop_status_records_success_and_failure() {
        let applied = NativeBackdropStatus::applied("Windows", "Mica");
        assert_eq!(applied.platform, "Windows");
        assert_eq!(applied.effect, "Mica");
        assert!(applied.applied);

        let unavailable = NativeBackdropStatus::unavailable("Windows", "unsupported build");
        assert_eq!(unavailable.effect, "Unavailable");
        assert!(!unavailable.applied);
        assert_eq!(unavailable.detail, "unsupported build");
    }

    #[test]
    fn pending_status_uses_current_platform() {
        let pending = NativeBackdropStatus::pending();
        assert_eq!(pending.platform, current_platform());
        assert_eq!(pending.effect, "Pending");
        assert!(!pending.applied);
    }
}
