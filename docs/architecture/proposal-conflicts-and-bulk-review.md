# Proposal conflicts and bulk review

Large Agent fleets can produce multiple valid-looking changes for the same
entity at the same time. Atlas treats this as governance state rather than
allowing last-write-wins publication.

## Conflict model

`detect_proposal_conflicts` compares active proposals in the same entity (or
the same global proposal scope). Two proposals conflict when their JSON
Pointer paths are equal or one contains the other, and the operations do not
write the same value. Rejected and released proposals leave the active
conflict set.

`GET /api/v1/proposals/conflicts` returns deterministic conflict IDs, proposal
pairs, affected paths, and blocking state. The operations summary also reports
the total conflict count and a per-proposal conflict count so the Admin queue
can surface contention without loading every proposal detail.

`GET /api/v1/proposals/queue` is the bounded operations read contract. It
accepts repeated `status`, `risk`, and `proposalType` filters plus `entityId`,
free-text `q`, `conflictsOnly`, `offset`, and `limit` (maximum 100). The
response carries the current page, total, `hasMore`, global status counts,
global active-conflict count, and only the conflict records touching the
current page. Conflict count, status priority, risk, and stable source order
determine server-side order. The legacy unpaged `/proposals` contract remains
available for existing release and Schema tooling.

## Review safety

An approval request receives `409 proposal-conflict` while its proposal has an
active conflict. Rejection remains available so a reviewer can choose a
winner, after which the remaining proposal can be approved.

When both proposals contain useful changes, a reviewer uses
`POST /api/v1/proposals/conflicts/{conflictId}/merge`. The request must select
one source proposal for every conflicting JSON Pointer path. The merge builder
deterministically keeps the selected conflicting operations, preserves
non-conflicting operations and citations from both sources, removes exact
duplicates, and rejects ambiguous partial operations that span paths assigned
to different sources.

Both sources must already be in `human-review`. The API atomically persists a
new `merge` proposal in `proposed` state and marks both sources `superseded`,
with `sourceProposalIds`, `supersededByProposalId`, and a shared timestamp.
The new proposal does not inherit approval: it must run policy evaluation and
receive the required human approval before release. One audit-chain event
records the path choices and operator comment.

`POST /api/v1/proposals/bulk-evaluations` accepts up to 100 unique proposed
IDs. It resolves and preflights the complete set before evaluating any item;
one missing, duplicate, or already-transitioned proposal rejects the batch.
The policy result for every item is persisted and one batch audit event records
the resulting status map.

`POST /api/v1/proposals/bulk-reviews` accepts up to 100 unique proposal IDs.
The API preflights the complete set before changing state: every proposal must
be in `human-review`, have a policy evaluation, and not already contain a
decision from the current reviewer. Bulk approval also performs the same
conflict gate. This prevents a partially applied batch caused by predictable
validation failures. Each resulting governed proposal is persisted and the
batch decision is added to the audit hash chain.

Admin `/reviews` consumes the bounded queue through the same-origin BFF. It
supports server-side search, status and conflict filters, paging, a path-level
conflict ledger, a path-level merge composer showing candidate values and
evidence counts, and separate bulk policy-evaluate, approve, and reject
actions. Only `proposed` and `human-review` rows are selectable, and mixed-state
selection cannot invoke an invalid batch transition. Rejected and superseded
proposals leave the active conflict set; an accepted proposal remains visible
but cannot be reviewed again.

Release publication still performs optimistic `before` checks. Conflict
detection improves queue safety but does not replace the final transactional
publication guard.
