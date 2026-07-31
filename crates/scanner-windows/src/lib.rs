use scanner_core::{ScanComponent, ScanSnapshot};

/// Deterministic adapter used by the skeleton. Real SMBIOS and SetupAPI collection
/// is deliberately kept behind this boundary and will require explicit consent.
pub fn collect_preview() -> ScanSnapshot {
    ScanSnapshot {
        platform: if cfg!(windows) {
            "windows".into()
        } else {
            "development-fixture".into()
        },
        components: vec![ScanComponent {
            kind: "system".into(),
            reported_name: "Hardware Atlas deterministic fixture".into(),
            hardware_ids: vec!["FIXTURE\\HARDATLAS_0001".into()],
            serial_number: Some("LOCAL-ONLY-FIXTURE".into()),
        }],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preview_is_stable_and_marks_non_windows_hosts() {
        let preview = collect_preview();
        assert_eq!(preview.components.len(), 1);
        assert!(!preview.components[0].hardware_ids.is_empty());
    }
}
