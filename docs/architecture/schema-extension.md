# Declarative domain extension model

Each domain pack contains versioned JSON documents:

```text
domain-packs/<domain>/
├── manifest.json
├── entity-types/
├── attributes/
├── relationship-types/
├── views/
├── policies/
└── seeds/
```

An entity type references attribute definitions and allowed relations. A view
definition arranges allow-listed blocks such as `hero`, `summary`,
`attribute-table`, `classification`, `timeline`, `map`, `media-gallery`,
`relationship-list`, and `citations`.

Adding a domain must not require a frontend deployment. Unsupported blocks fail
closed to a visible `unsupported-block` placeholder in authoring, never a blank
public section.

`HARDATLAS_DOMAIN_PACK_PATHS` is loaded at API startup. The runtime registry
merges byte-equivalent shared definitions, rejects conflicting IDs, validates
all cross-document references, and persists every accepted definition as an
immutable Schema document. Python fixtures are not a Schema source.

Admin `/schema/domain-packs` provides the same contract as a structured
constructor. It can declare a knowledge space, root taxonomy, entity type,
typed attributes, safe view blocks, and relationship types with inverse
labels, direction, source/target cardinality, evidence requirements, and
qualifier Schema. A relationship source is the type declared by the current
pack; its target can be that type, one or more already-published Registry
types, or both. This allows a new astronomy pack to reference an existing
electronic-component type without copying or redefining either Schema. The
constructor resolves the complete Registry first, then runs cross-reference
and cycle validation, saves one immutable pack version and creates a high-risk
governed Schema proposal. It never publishes directly.

The version registry exposes deterministic manifest diffs and a fail-closed
rollback inspector. Publication captures the exact prior Schema activations,
spaces, and taxonomy nodes. Rollback is accepted only when no active entity,
Schema, relationship, taxonomy, or space dependency would be orphaned and the
submitted publication timestamp still matches. The repository repeats these
checks inside the transaction, restores the snapshot, emits an outbox event,
and records the operator's audit comment.

Schema changes are classified:

- additive: new optional field or relation;
- compatible: display or validation refinement;
- migratory: value transformation required;
- breaking: removal or semantic redefinition.

Migratory and breaking changes require impact analysis, a migration manifest,
golden rendering checks, and an explicit rollback target.
