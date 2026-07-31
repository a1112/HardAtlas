# Risks and open decisions

## Controlled risks

| Risk                           | P0 control                                               | Acceptance signal                                      |
| ------------------------------ | -------------------------------------------------------- | ------------------------------------------------------ |
| SKU/revision misidentification | Separate identities, confidence, user confirmation       | Low-confidence matches never auto-confirm              |
| Privacy leakage from scans     | Local preview, allow-list fields, Rust redaction         | Golden fixtures contain no device-unique serial        |
| Rule false result              | Four states, evidence, golden replay, immutable versions | Every non-compatible result explains a rule and action |
| Conflicting sources            | Scope and source-level metadata, explicit unknown        | Conflict stays visible and blocks false certainty      |
| Bad publication                | Transaction manifest, outbox, index alias switch         | Release can be rolled back as one batch                |
| AI hallucination               | Tool-first orchestration, no AI in deterministic path    | Core search/compare/rules operate without model access |
| Source licensing               | Rights and redistribution metadata per source            | Unapproved content cannot enter a public release       |

## Open decisions

- Select the production OIDC provider after enterprise and regional requirements
  are known; preserve standard OIDC at the application boundary.
- Confirm commercial redistribution rights for each priority manufacturer.
- Define initial supported Windows 10 servicing versions before beta.
- Choose the first benchmark corpus and licensing model for AI workload planning.
- Decide retention periods for private scan snapshots and diagnostic uploads.
- Establish the human review SLA and approval quorum for high-impact rule changes.
