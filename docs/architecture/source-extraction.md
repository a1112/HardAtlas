# Source snapshot extraction

The extraction boundary converts an immutable, policy-approved source snapshot
into staging candidates. It never writes published encyclopedia entities.

## Versioned parser contract

Parser definitions live under `parser-packs/` and are pinned by both
`parserId` and `parserVersion` on a `SourceDefinition`. A definition declares:

- one of the built-in `json`, `jsonl`, `csv`, or `html` adapters;
- accepted media types, entity type, locale, and record limit;
- a record path or restricted HTML record selector;
- label, external-id, entity-id, and field mappings;
- target field paths, confidence, and required-field policy.

The path and selector vocabularies are deliberately non-executable. JSON uses
JSON Pointer or dotted keys. HTML supports a single tag, class, id, or
attribute selector, plus `selector@attribute` reads. Script, style, and
template contents are ignored. Domain packs and source payloads cannot inject
Python, JavaScript, XPath, JSONPath functions, or template code.

## Provenance and immutability

Before parsing, the worker checks the S3 object byte size and SHA-256 against
the `SourceSnapshot`. Every extraction batch records the source version,
snapshot id/hash, parser id/version, deterministic candidate ids, and status.
Every field candidate records:

- original and proposed values;
- the target knowledge path and optional attribute id;
- record/field locator and quote hash;
- citation id and confidence.

The PostgreSQL extraction batch is immutable. Candidate content and provenance
are immutable; only its workflow state and linked schedule ids may advance.
Repeating the same snapshot and parser version produces the same ids.

The batch and its candidates are committed with a
`source.extraction.completed` Outbox event in one transaction. Extraction and
finalization are separate replay boundaries:

1. snapshot bytes → immutable extraction batch and candidates;
2. extraction batch → deterministic resolution and version-pinned Agent
   schedules;
3. Agent schedule → checkpointed run and governed proposal.

This split prevents a broker outage after parsing from stranding candidates,
and lets operators replay only the failed stage.

## Agent handoff

Candidates that already contain a resolved `entityId` are split into
field-level, idempotent `knowledge-maintenance` graph schedules. Candidates
without an entity id first pass through deterministic exact-name and exact-alias
resolution within the declared entity type. A unique match advances to
`resolved`; multiple matches remain visible as `parsed` with all candidates
recorded. Fuzzy or model-assisted matching never silently selects an entity.
The Agent graph creates governed proposals with citations, and those proposals
still pass policy and human review before publication.

Before a resolved field is scheduled, the worker reads the current immutable
entity revision and records `currentRevisionId`, whether the target path exists,
and its exact current value. The resulting proposal therefore uses `replace`
with an optimistic `before` value or `add` for a missing path. A stale field
fails closed during publication even if an unrelated review completed earlier.

The candidate's full snapshot-derived Citation is embedded in the schedule.
When that Citation is not already present on the entity, the proposal contains
an atomic `/citations/{citationId}` addition followed by the field operation
that references it. Citation IDs use JSON Pointer escaping, so distinct sources
remain independently mergeable. The policy gate treats such an addition as
self-evidencing only when its ID matches the path and it contains both an
immutable locator and quote hash. Reprocessing a field whose published value is
already equal to the candidate creates no new schedule or proposal.

Runtime paths:

- parser definitions: `HARDATLAS_PARSER_PATHS` (default `parser-packs/core`);
- snapshots: S3/MinIO through `S3_*`;
- task: `extract_source_snapshot_task`;
- finalization task: `finalize_extraction_batch_task`;
- API: source extraction batches and batch candidates under `/api/v1`.
