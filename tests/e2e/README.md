# End-to-end journeys

Run the executable browser suite with:

```bash
pnpm test:e2e
```

The configuration starts or reuses the API and Web server, keeps artifacts
under `output/playwright`, and uses Microsoft Edge on the current macOS
workstation unless `PLAYWRIGHT_CHANNEL` selects another installed channel.
CI can install Playwright Chromium and omit the channel.

The repository keeps two complementary acceptance layers. Python API/worker
integration tests and the recorded browser verification cover:

1. multilingual alias search → dynamic encyclopedia entry;
2. Schema-selected allow-listed view blocks and evidence display;
3. Agent DAG execution → persisted checkpoint → governed proposal;
4. policy evaluation → distinct human reviewers → accepted proposal;
5. release staging → immutable entity revision → search index activation;
6. rollback → previous entity revision → restored search index;
7. process restart → proposal, release, and Agent-run state restoration.

The executable Playwright files cover:

1. Domain Pack card → selected scalable taxonomy view;
2. backend Facet filters → evidence-backed browse result and match reason;
3. persisted content-language preference → localized entry and Domain Pack;
4. fixed revision URL → immutable cache contract;
5. WCAG A/AA automated scan with no serious or critical violations.
6. Admin source registration → reliable acquisition event → observable
   maintenance pipeline.
7. Browser-to-API traffic remains behind the Admin same-origin BFF; no
   browser request directly targets the API service.
8. Existing entity revision → pinned private authoring draft → page reload →
   workspace recovery → Domain Pack relationship editor → live safe-view
   preview → persisted governed diff, while the public revision and relation
   graph remain unchanged.
9. Isolated encyclopedia entity → server-filtered proposed candidate → bulk
   policy evaluation → two conflicting Agent proposals → path-level merge
   selection → immutable superseded provenance → policy evaluation and bulk
   approval of the merged result, with every browser API request remaining
   behind the Admin BFF.
10. Admin Domain Pack builder → new knowledge space, taxonomy, entity type,
    attribute and typed cross-domain relationship to an existing Registry type
    → validation → immutable version draft → high-risk governed Schema
    proposal, without application code generation.

Hardware scan and compatibility journeys remain isolated under the optional
hardware extension and are not prerequisites for the universal encyclopedia
kernel.
