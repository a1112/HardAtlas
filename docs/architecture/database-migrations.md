# Database migrations

Alembic is the authoritative database bootstrap and upgrade mechanism. The
current head is `0006_maintenance_work_queue`. The `0001_atlas_kernel` baseline
creates the kernel tables, indexes, private device snapshots, collections, and
their PostgreSQL RLS policies. Revision `0002` adds private resumable authoring
drafts and the corresponding forced-RLS policy. Revision `0003` adds the
governed-proposal compare-and-swap version used by concurrent API instances and
Agent workers; existing proposal documents are upgraded to version 1 without
rewriting their operations, evidence, reviews, or provenance.
Revision `0004` adds immutable evidence-grounded public answer records so the
response a reader saw can be replayed against its pinned entity revisions and
model-gateway provenance.
Revision `0005` adds current-revision quality assessments and the persistent,
idempotent maintenance-task queue consumed by governed Agent workflows.
Revision `0006` adds the revision-pinned, leaseable maintenance Work Item queue
used by concurrent route-specific Agents.

Run an upgrade before starting the API, workers, scheduler, or dispatcher:

```bash
uv run alembic -c packages/py/data/alembic.ini upgrade head
```

`HARDATLAS_DATABASE_URL` overrides the URL in `alembic.ini`. Production
processes do not call `metadata.create_all()`: when
`HARDATLAS_AUTO_CREATE_SCHEMA` is unset, automatic creation is enabled only
outside `prod` and `production`. Worker processes always require an already
migrated database.

The Compose stack includes a one-shot `migrate` service that waits for
PostgreSQL health and upgrades to Alembic head. The numbered SQL files in
`packages/py/data/sql` are retained as human-readable policy/contract
references; they are not a deployment migration system and are no longer
mounted into PostgreSQL's first-boot directory.

The SQLite migration test upgrades an empty database twice and checks the head
revision, proposal lock column, and tables used by the current kernel.
PostgreSQL RLS behavior is
verified separately by running `tests/contracts/test_rls.sql` as the non-owner
application role.
