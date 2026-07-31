use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanComponent {
    pub kind: String,
    pub reported_name: String,
    pub hardware_ids: Vec<String>,
    pub serial_number: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScanSnapshot {
    pub platform: String,
    pub components: Vec<ScanComponent>,
}

/// Removes device-unique fields before a snapshot crosses the local trust boundary.
pub fn redact_snapshot(mut snapshot: ScanSnapshot) -> ScanSnapshot {
    for component in &mut snapshot.components {
        component.serial_number = None;
    }
    snapshot
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_serial_numbers_but_keeps_matching_signals() {
        let snapshot = ScanSnapshot {
            platform: "windows".into(),
            components: vec![ScanComponent {
                kind: "gpu".into(),
                reported_name: "Fixture GPU".into(),
                hardware_ids: vec!["PCI\\VEN_10DE&DEV_0000".into()],
                serial_number: Some("sensitive".into()),
            }],
        };

        let redacted = redact_snapshot(snapshot);
        assert_eq!(redacted.components[0].serial_number, None);
        assert_eq!(redacted.components[0].hardware_ids.len(), 1);
    }
}
