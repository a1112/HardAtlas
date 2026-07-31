# Desktop offline reader

## Runtime boundary

Atlas Desktop is a Tauri 2 client for the universal encyclopedia. It consumes
the same public `/api/v1` knowledge contracts as Web and does not embed animal,
plant, electronics, or hardware categories in its UI.

At startup it loads:

- build and data version;
- published knowledge spaces;
- taxonomy nodes;
- entity type labels.

Search calls `/api/v1/search`; the selected `KnowledgeEntity` is rendered from
its localized names, claims, sections, citations and revision context.
Hardware scanning remains a deliberately optional local extension.

## Offline data

The native shell creates `atlas-offline.sqlite3` in the platform application
data directory. It uses WAL mode and contains:

- `cached_entity`: complete immutable entity JSON plus identity, type,
  `dataVersion` and save time;
- `pending_sync`: durable queue boundary for later personal-state
  synchronization.

Saving the same entity performs an upsert, so a newer data version replaces the
old snapshot without creating duplicates. Desktop commands expose status,
cache, list, read and removal operations. Browser preview uses isolated
`localStorage` only because the Tauri command bridge is unavailable there.

## Security

The WebView CSP permits encyclopedia traffic only to the configured local
development API origins in addition to Tauri IPC. The API CORS allow-list
contains the Vite desktop origin and Tauri production origins. The desktop
capability remains `core:default`; no broad filesystem or shell permission is
granted to JavaScript.

## Verification

Rust tests open SQLite in memory and verify versioned entity upsert behavior.
The production Tauri binary is built with:

```bash
pnpm --filter @hardatlas/desktop tauri build
```

Browser integration verifies API bootstrap, search, detail rendering, offline
save/read/remove and dynamic taxonomy.
