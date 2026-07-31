# V1.0 scope matrix

| Capability            | P0 — first closed loop                                          | P1 — expansion                                  | P2 — ecosystem                           |
| --------------------- | --------------------------------------------------------------- | ----------------------------------------------- | ---------------------------------------- |
| Device identification | Windows local scan, consent preview, confidence mapping         | macOS/Linux agents, fleet enrollment            | Vendor-assisted identity and attestation |
| Product knowledge     | Mainstream PC components, SKU/revision, evidence, lifecycle     | Peripherals, servers, networking                | Long-tail and industrial hardware        |
| Compatibility         | Physical, electrical, firmware and software hard constraints    | Enterprise policies and topology-aware planning | Partner-authored certified rule packs    |
| Projects              | Personal build/deployment project, BOM, versioned report        | Team templates, approvals, budget               | Procurement and reseller workflows       |
| Lifecycle             | Driver/firmware/EOL facts and affected-device alerts            | Fleet rollout and maintenance windows           | Predictive replacement planning          |
| AI workload           | Memory decomposition and deterministic stack checks             | Benchmarks and cost/performance ranking         | Organization-specific optimization       |
| Data operations       | Review, de-duplicate, source governance, batch release/rollback | Vendor portal and richer QA automation          | Federated contribution network           |
| API                   | Internal `/api/v1` contracts and SSE                            | Enterprise APIs and webhooks                    | Commercial developer platform            |

P0 acceptance requires the complete identify → understand → judge → save → track
loop. A P1/P2 item must not weaken source traceability, four-state compatibility,
privacy consent, or version replay.
