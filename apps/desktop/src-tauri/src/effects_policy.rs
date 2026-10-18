//! Accessibility takes precedence over an application's material preference.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct EffectPolicy {
    pub enabled: bool,
    pub reduce_transparency: bool,
    pub high_contrast: bool,
}

impl EffectPolicy {
    pub fn allows_material(self) -> bool {
        self.enabled && !self.reduce_transparency && !self.high_contrast
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transparency_and_contrast_preferences_always_override_opt_in() {
        for (enabled, reduce_transparency, high_contrast, expected) in [
            (false, false, false, false),
            (false, false, true, false),
            (false, true, false, false),
            (false, true, true, false),
            (true, false, false, true),
            (true, false, true, false),
            (true, true, false, false),
            (true, true, true, false),
        ] {
            let policy = EffectPolicy {
                enabled,
                reduce_transparency,
                high_contrast,
            };
            assert_eq!(policy.allows_material(), expected);
        }
    }

    #[test]
    fn closing_effects_then_changing_accessibility_does_not_reenable_them() {
        let mut policy = EffectPolicy {
            enabled: false,
            reduce_transparency: true,
            high_contrast: true,
        };
        policy.reduce_transparency = false;
        policy.high_contrast = false;
        assert!(!policy.allows_material());
    }

    #[test]
    fn clearing_a_temporary_accessibility_override_restores_the_opt_in() {
        let mut policy = EffectPolicy {
            enabled: true,
            reduce_transparency: true,
            high_contrast: false,
        };
        assert!(!policy.allows_material());
        policy.reduce_transparency = false;
        assert!(policy.allows_material());
        policy.high_contrast = true;
        assert!(!policy.allows_material());
        policy.high_contrast = false;
        assert!(policy.allows_material());
    }
}
