# Personal collections and workspace isolation

Atlas keeps published encyclopedia knowledge globally readable while isolating
private user and organization state by workspace.

## Identity and workspace context

Every authenticated principal receives a UUID workspace context. An OIDC token
may provide it through the configurable `workspace_id` claim. If the claim is
absent, the API derives a stable personal workspace UUID from the issuer and
subject. Development authentication accepts
`x-hardatlas-dev-workspace-id`; production rejects development authentication.

The workspace value is never accepted from a collection or authoring-draft
request body. The API always takes it from the authenticated principal.

## Saved collections

The personal API supports:

- listing and creating collections;
- deleting a collection and its items;
- listing saved items in one collection;
- idempotently saving an entity to a collection;
- removing a saved entity.

Each item records the entity revision and data version visible at save time.
This makes a collection reproducible even after the public entry advances to a
new revision. Notes and tags remain private workspace data.
The save request includes `entityRevisionId`; the API verifies that the
revision belongs to the requested entity. Personal-space links return to that
fixed revision URL rather than silently opening the current revision.

## Private authoring drafts

Entry drafts use the same workspace boundary as collections. A draft records
its creation or revision mode, complete `EntityDraft`, pinned base revision,
creator, lifecycle status, proposal link, and monotonically increasing
version. `expectedVersion` is required for updates, submission, and
abandonment so stale tabs receive a conflict instead of overwriting newer
work.

Only `editing` drafts appear in the Admin recovery selector. Submission moves
the record to `submitted`, links the governed proposal, and keeps public
knowledge unchanged until a separate approved release. Cross-workspace detail
reads return `404` and list calls never reveal another workspace's draft.

## Web session and reader workflow

The public Next.js application uses its own Authorization Code + PKCE client
and a separate set of `HttpOnly`, `SameSite=Lax` cookies. Browser code never
receives the access or refresh token. Authenticated collection calls pass
through the same-origin `/api/backend` proxy, which adds the access token
server-side and performs one refresh-token retry after a `401`.

The entry page can save the visible revision directly. If the reader has no
collection, Atlas creates a default `我的收藏` collection. The personal-space
page supports creating and selecting collections, opening a saved entry, and
removing an item or collection. The UI always shows the pinned data version
and entity revision rather than implying that the saved item tracks the latest
public content.

## Defense in depth

Repository queries include explicit workspace predicates and reject a
collection or authoring draft from another workspace. On PostgreSQL, each
private-data transaction also sets `app.workspace_id` with transaction-local
scope. Alembic enables and forces Row Level Security on both collection tables
and `authoring_draft`, using the same workspace context for `USING` and
`WITH CHECK`.

The SQL contract under `tests/contracts/test_rls.sql` must run as the non-owner
application role. Table owners and superusers can bypass normal RLS semantics,
so they are not valid identities for the isolation test.
