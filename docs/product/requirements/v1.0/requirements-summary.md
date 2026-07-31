# Hardware Atlas V1.0 implementation baseline

## Product definition

Hardware Atlas identifies real devices, separates models from SKU and hardware
revisions, explains compatibility and alternatives, tracks drivers, firmware,
and lifecycle, and supports purchase, upgrade, deployment, and asset decisions.
It is a decision system built from normalized entities, evidence, compatibility
rules, device instances, and immutable data versions rather than a collection
of parameter pages.

The P0 value loop is:

1. Identify the exact device and confidence signals.
2. Explain the product, version, specifications, sources, and lifecycle.
3. Check physical, electrical, firmware, software, and workload constraints.
4. Save an actionable upgrade or deployment project.
5. Re-evaluate devices and projects when data or rules change.

## P0 surfaces

| Surface             | P0 responsibility                                                                                                         |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Windows desktop     | Permission disclosure, local scan, entity mapping, device snapshot, offline cache, compatibility and project entry points |
| Public web          | Search, taxonomy, product details, sources, lifecycle, comparison and stable share links                                  |
| Signed-in workspace | Devices, projects, saved items, alerts, privacy, export and AI context                                                    |
| Data operations     | Entity/SKU/revision editing, provenance, de-duplication, rule review, release batches and rollback                        |
| API boundary        | Stable IDs, versioned search/detail/compatibility contracts and asynchronous progress                                     |

## P0 domain invariants

- Model, SKU/region, hardware revision, and user device instance are distinct.
- Original, normalized, and display specification values are preserved.
- Every decision-grade claim has source, scope, valid time, confidence, and
  review status.
- Compatibility has exactly four public outcomes: compatible, conditional,
  incompatible, and unknown.
- Missing critical input is unknown, never silently compatible.
- Specific revision rules override SKU rules; SKU rules override model rules.
- Hard constraints run before ranking. A high score never overrides an
  unresolved hard constraint.
- Published data and rules are immutable versions. Reports preserve both
  `data_version` and `rule_version`.
- Public facts never absorb private device identifiers or unreviewed user
  reports.
- AI explains and orchestrates structured tools; it does not invent hardware
  facts and cannot perform high-impact writes without confirmation.

## P0 capability matrix

| Area            | P0                                                                                 |
| --------------- | ---------------------------------------------------------------------------------- |
| Account         | Visitor read access, sign-in, preferences, separate scan/diagnostic consent        |
| Search          | Model, SKU, part number, alias, hardware ID, typo and natural-language constraints |
| Product         | Identity, source-backed specifications, relations, software support, lifecycle     |
| Compare         | 2-4 products, normalized units, difference view, missing-data markers              |
| Scan            | CPU, GPU, board, memory, storage, NIC, OS, driver and firmware signals             |
| Device          | Component tree, mapping confidence, snapshots, risks, upgrade entry points         |
| Compatibility   | PC physical/electrical/firmware/software rules with explanations and fixes         |
| Project         | Goals, slots, constraints, live issues, versions, report and BOM                   |
| AI workload     | Model size, precision, context, concurrency, memory decomposition and stack checks |
| Lifecycle       | Driver, firmware, release, EOL/EOS and impact-aware alerts                         |
| Data operations | Source governance, entity merging, field review, release and rollback              |

## Non-functional baseline

- Common page P75 interactive time: no more than 2.5 seconds.
- Autocomplete P95: no more than 300 ms; full search P95: no more than 1.2 seconds.
- Common compatibility check P95: no more than 2 seconds.
- Core service monthly availability target: 99.9%.
- Public data releases are atomic and reversible.
- RPO: at most 15 minutes; RTO: at most 4 hours.
- Core flows meet the important requirements of WCAG 2.2 AA.
- Windows 11 and supported Windows 10 versions are P0; macOS and Linux follow.

## Risks and controls

- Data cold start: focus on recent mainstream P0 hardware, measure field/source
  coverage, and go deep before expanding categories.
- SKU/revision confusion: enforce identity hierarchy and require confirmation
  for low-confidence mapping.
- Licensing uncertainty: track permitted use, redistribution, caching, and
  attribution per source.
- Rule false positives/negatives: versioned rules, evidence, golden sets,
  canaries, impact analysis, and rollback.
- Scanner privacy: local-first preview, minimum data, field-level controls, and
  irreversible identifiers before synchronization.
- AI hallucination: tool-first answers, source citations, explicit unknown
  states, action previews, and fixed evaluation sets.

## Deferred decisions

- Production OIDC vendor remains replaceable; local development uses Keycloak.
- Pricing and commerce data are out of P0 compatibility truth.
- Enterprise assets, macOS/Linux scanning, vendor portal, and public API
  commercialization remain P1.
- A graph database is not introduced in P0; relation edges and recursive
  traversal remain transactional in PostgreSQL.
