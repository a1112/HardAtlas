# Atlas V2 — Agent-native universal encyclopedia

## Product definition

Atlas is a professional, extensible encyclopedia framework. It is not tied to
hardware: the same kernel must represent animals, plants, electronic
components, places, people, events, technologies, and domains not known when
the software is deployed.

The public experience is search- and taxonomy-first. The maintenance experience
is an evidence-first, multi-agent production system. Agents may discover,
extract, normalize, translate, link, verify, monitor, and propose changes, but
publication remains governed, replayable, and reversible.

## Primary loop

1. Search a concept, alias, identifier, or natural-language question.
2. Traverse a dynamically generated taxonomy and relation graph.
3. Read a structured article whose sections and attributes come from its type
   schema.
4. Inspect citations, confidence, revision history, and machine-generated
   disclosures.
5. Follow relationships into adjacent knowledge.
6. Let maintenance agents detect source changes and propose evidence-backed
   revisions.

## Product principles

- **Schema before content:** domain packs define types, fields, relations, and
  views without changing the kernel.
- **Facts are claims:** every decision-grade value has scope, source, valid
  time, confidence, and revision.
- **Generation is not publication:** generated text is always a proposal until
  policy and evidence gates pass.
- **The graph is useful, not ornamental:** relations drive navigation,
  disambiguation, maintenance impact, and retrieval context.
- **Unknown stays visible:** missing or conflicting knowledge is represented
  explicitly.
- **Every release is replayable:** articles record schema, data, model, prompt,
  policy, and agent-run versions.
- **Human attention is allocated by risk:** low-risk routine changes may be
  policy-approved; sensitive or high-impact changes require human review.

## Reference domains for framework acceptance

| Domain      | Reference entity           | What it proves                                       |
| ----------- | -------------------------- | ---------------------------------------------------- |
| Animals     | Snow leopard / 雪豹        | Taxonomy, distribution, conservation, media, aliases |
| Plants      | Ginkgo / 银杏              | Scientific classification, morphology, history, uses |
| Electronics | NE555 timer / NE555 定时器 | Structured parameters, packages, variants, diagrams  |

The kernel passes the first architecture milestone only when all three can be
rendered, searched, versioned, cited, and maintained with the same APIs.

## Hardware status

Computer hardware scanning and compatibility are retained as an optional
`hardware` domain extension. They are not dependencies of the encyclopedia
kernel or public home page.
