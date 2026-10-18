//! Titlebar gestures honor macOS preferences; explicit buttons keep their own action.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    None,
    Minimize,
    Zoom,
}
pub fn action(
    preference: &str,
    fullscreen: bool,
    sheet: bool,
    can_minimize: bool,
    can_zoom: bool,
) -> Action {
    if fullscreen || sheet || preference == "None" {
        return Action::None;
    }
    if preference == "Minimize" {
        return if can_minimize {
            Action::Minimize
        } else {
            Action::None
        };
    }
    if can_zoom {
        Action::Zoom
    } else {
        Action::None
    }
}
pub fn capability(native_style: bool, button_enabled: Option<bool>, configured: bool) -> bool {
    native_style && button_enabled.unwrap_or(configured)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn borderless_button_absence_uses_configured_native_capability() {
        assert!(capability(true, None, true));
        assert!(!capability(true, None, false));
        assert!(!capability(true, Some(false), true));
        assert!(capability(true, Some(true), true));
        for button in [None, Some(false), Some(true)] {
            assert!(!capability(false, button, true));
        }
    }
    #[test]
    fn all_actions_are_blocked_by_fullscreen_or_sheet() {
        for preference in ["None", "Minimize", "Zoom", "Maximize", "", "unknown"] {
            for (fullscreen, sheet) in [(true, false), (false, true), (true, true)] {
                assert_eq!(
                    action(preference, fullscreen, sheet, true, true),
                    Action::None
                );
            }
        }
    }
    #[test]
    fn none_never_acts() {
        for minimize in [false, true] {
            for zoom in [false, true] {
                assert_eq!(action("None", false, false, minimize, zoom), Action::None);
            }
        }
    }
    #[test]
    fn minimize_does_not_require_zoom_or_resize_capability() {
        for zoom in [false, true] {
            assert_eq!(
                action("Minimize", false, false, true, zoom),
                Action::Minimize
            );
            assert_eq!(action("Minimize", false, false, false, zoom), Action::None);
        }
    }
    #[test]
    fn zoom_and_unset_preferences_require_only_zoom_capability() {
        for preference in ["Zoom", "Maximize", "", "unknown"] {
            for minimize in [false, true] {
                assert_eq!(
                    action(preference, false, false, minimize, true),
                    Action::Zoom
                );
                assert_eq!(
                    action(preference, false, false, minimize, false),
                    Action::None
                );
            }
        }
    }
}

pub fn allows_drag(fullscreen: bool, sheet: bool, movable: bool) -> bool {
    !fullscreen && !sheet && movable
}
