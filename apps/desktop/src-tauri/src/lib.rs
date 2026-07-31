use rusqlite::{params, Connection};
use scanner_core::{redact_snapshot, ScanSnapshot};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager};

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct EntityReference {
    id: String,
    slug: String,
    type_id: String,
    canonical_name: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RevisionContext {
    data_version: String,
}

#[derive(Debug, Deserialize)]
struct EntityDocument {
    #[serde(rename = "ref")]
    reference: EntityReference,
    revision: RevisionContext,
}

#[derive(Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct CachedEntitySummary {
    id: String,
    slug: String,
    type_id: String,
    canonical_name: String,
    data_version: String,
    saved_at: i64,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DesktopStatus {
    database_path: String,
    cached_entity_count: i64,
    pending_sync_count: i64,
}

fn unix_timestamp() -> Result<i64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .map_err(|error| error.to_string())
}

fn initialize_database(connection: &Connection) -> rusqlite::Result<()> {
    connection.execute_batch(
        "
        PRAGMA journal_mode = WAL;
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS cached_entity (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            type_id TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            data_version TEXT NOT NULL,
            document TEXT NOT NULL,
            saved_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_sync (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'completed'))
        );
        ",
    )
}

fn database_path(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = app.path().app_data_dir().map_err(|error| error.to_string())?;
    fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
    Ok(directory.join("atlas-offline.sqlite3"))
}

fn open_database(app: &AppHandle) -> Result<(Connection, PathBuf), String> {
    let path = database_path(app)?;
    let connection = Connection::open(&path).map_err(|error| error.to_string())?;
    initialize_database(&connection).map_err(|error| error.to_string())?;
    Ok((connection, path))
}

fn cache_document(
    connection: &Connection,
    entity_json: &str,
    saved_at: i64,
) -> Result<CachedEntitySummary, String> {
    let entity: EntityDocument =
        serde_json::from_str(entity_json).map_err(|error| error.to_string())?;
    connection
        .execute(
            "
            INSERT INTO cached_entity (
                id, slug, type_id, canonical_name, data_version, document, saved_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
            ON CONFLICT(id) DO UPDATE SET
                slug = excluded.slug,
                type_id = excluded.type_id,
                canonical_name = excluded.canonical_name,
                data_version = excluded.data_version,
                document = excluded.document,
                saved_at = excluded.saved_at
            ",
            params![
                entity.reference.id,
                entity.reference.slug,
                entity.reference.type_id,
                entity.reference.canonical_name,
                entity.revision.data_version,
                entity_json,
                saved_at,
            ],
        )
        .map_err(|error| error.to_string())?;
    Ok(CachedEntitySummary {
        id: entity.reference.id,
        slug: entity.reference.slug,
        type_id: entity.reference.type_id,
        canonical_name: entity.reference.canonical_name,
        data_version: entity.revision.data_version,
        saved_at,
    })
}

fn read_cached_entities(
    connection: &Connection,
) -> Result<Vec<CachedEntitySummary>, String> {
    let mut statement = connection
        .prepare(
            "
            SELECT id, slug, type_id, canonical_name, data_version, saved_at
            FROM cached_entity
            ORDER BY saved_at DESC, canonical_name
            ",
        )
        .map_err(|error| error.to_string())?;
    let rows = statement
        .query_map([], |row| {
            Ok(CachedEntitySummary {
                id: row.get(0)?,
                slug: row.get(1)?,
                type_id: row.get(2)?,
                canonical_name: row.get(3)?,
                data_version: row.get(4)?,
                saved_at: row.get(5)?,
            })
        })
        .map_err(|error| error.to_string())?;
    rows
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn desktop_status(app: AppHandle) -> Result<DesktopStatus, String> {
    let (connection, path) = open_database(&app)?;
    let cached_entity_count = connection
        .query_row("SELECT COUNT(*) FROM cached_entity", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let pending_sync_count = connection
        .query_row(
            "SELECT COUNT(*) FROM pending_sync WHERE status = 'pending'",
            [],
            |row| row.get(0),
        )
        .map_err(|error| error.to_string())?;
    Ok(DesktopStatus {
        database_path: path.to_string_lossy().into_owned(),
        cached_entity_count,
        pending_sync_count,
    })
}

#[tauri::command]
fn cache_entity(
    app: AppHandle,
    entity_json: String,
) -> Result<CachedEntitySummary, String> {
    let (connection, _) = open_database(&app)?;
    cache_document(&connection, &entity_json, unix_timestamp()?)
}

#[tauri::command]
fn list_cached_entities(app: AppHandle) -> Result<Vec<CachedEntitySummary>, String> {
    let (connection, _) = open_database(&app)?;
    read_cached_entities(&connection)
}

#[tauri::command]
fn get_cached_entity(app: AppHandle, slug: String) -> Result<Option<String>, String> {
    let (connection, _) = open_database(&app)?;
    let mut statement = connection
        .prepare("SELECT document FROM cached_entity WHERE slug = ?1")
        .map_err(|error| error.to_string())?;
    let mut rows = statement
        .query([slug])
        .map_err(|error| error.to_string())?;
    rows.next()
        .map_err(|error| error.to_string())?
        .map(|row| row.get(0).map_err(|error| error.to_string()))
        .transpose()
}

#[tauri::command]
fn remove_cached_entity(app: AppHandle, id: String) -> Result<bool, String> {
    let (connection, _) = open_database(&app)?;
    connection
        .execute("DELETE FROM cached_entity WHERE id = ?1", [id])
        .map(|affected| affected > 0)
        .map_err(|error| error.to_string())
}

#[tauri::command]
fn start_safe_scan() -> String {
    let snapshot = scanner_windows::collect_preview();
    let redacted: ScanSnapshot = redact_snapshot(snapshot);
    format!(
        "本地预览完成：识别到 {} 个组件；序列号等敏感字段未采集。",
        redacted.components.len()
    )
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            desktop_status,
            cache_entity,
            list_cached_entities,
            get_cached_entity,
            remove_cached_entity,
            start_safe_scan
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Atlas desktop");
}

#[cfg(test)]
mod tests {
    use super::{cache_document, initialize_database, read_cached_entities};
    use rusqlite::Connection;

    #[test]
    fn offline_cache_upserts_versioned_entity_documents() {
        let connection = Connection::open_in_memory().unwrap();
        initialize_database(&connection).unwrap();
        let first = r#"{
          "ref": {
            "id": "entity-ginkgo",
            "slug": "ginkgo",
            "typeId": "type-plant",
            "canonicalName": "银杏"
          },
          "revision": {"dataVersion": "atlas-1"}
        }"#;
        let second = first.replace("atlas-1", "atlas-2");

        cache_document(&connection, first, 10).unwrap();
        cache_document(&connection, &second, 20).unwrap();

        let cached = read_cached_entities(&connection).unwrap();
        assert_eq!(cached.len(), 1);
        assert_eq!(cached[0].canonical_name, "银杏");
        assert_eq!(cached[0].data_version, "atlas-2");
        assert_eq!(cached[0].saved_at, 20);
    }
}
