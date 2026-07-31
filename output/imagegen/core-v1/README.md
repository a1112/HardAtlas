# Atlas V2 UI outputs

Expected final assets:

1. `01-search-taxonomy-home.png`
2. `02-search-results-disambiguation.png`
3. `03-entity-snow-leopard.png`
4. `04-dynamic-taxonomy-browser.png`
5. `05-knowledge-relationship-explorer.png`
6. `06-schema-domain-pack-builder.png`
7. `07-agent-maintenance-operations.png`
8. `08-agent-proposal-review-release.png`

The V1 hardware dashboard was moved to `iterations/` after product-scope review.
V2 prompts define a universal, search-first encyclopedia with declarative
domain expansion and evidence-governed multi-Agent maintenance.

Current generation status:

- `01-search-taxonomy-home.png`: refreshed from current Atlas web homepage UI at
  3840×2160 and stored as the encyclopedia-aligned artifact; SHA-256
  `bca57fe383f935f09d13f21fc772ac66a8e7b1b7c12d7e3d57171a65f2ae647e`.
- `02-search-results-disambiguation.png`: refreshed from current Atlas search
  interface (`/search?q=苹果`) at 3840×2160 and stored as the encyclopedia-aligned
  artifact; SHA-256
  `237c0b920760b3045338a4fb00aaf08be9d759b6456f7f326612cf32176c63f7`.
- `03-entity-snow-leopard.png`: captured via 3840×2160 fallback Playwright pass from
  `/entry/snow-leopard` due no `OPENAI_API_KEY` in this runtime; SHA-256
  `7f17026f42eb4839ab46ad3ecf59b7ca96d73ae931eb0dc429fd7d6dd3673ad0`.
- `04-dynamic-taxonomy-browser.png`: captured via 3840×2160 fallback Playwright pass from
  `/categories?space=space-life&taxonomy=tax-animals`; SHA-256
  `3c34ad565de1385b2963a8cdae31cb1d9bae94aa6aa91136f05b7619a986897f`.
- `05-knowledge-relationship-explorer.png`: captured via 3840×2160 fallback Playwright pass from
  `/relations/snow-leopard`; SHA-256
  `82d943d4c5b6240ff2d706effb09e7a91e71253332327bc98b909b46765f0615`.
- `06-schema-domain-pack-builder.png`: captured via 3840×2160 fallback Playwright pass from
  `/schema/domain-packs`; SHA-256
  `cae62bb16bba17572b1f48890ee7e1e5881d493f1187b15eb3f137a9e05e29ca`.
- `07-agent-maintenance-operations.png`: captured via 3840×2160 fallback Playwright pass from
  `/`; SHA-256
  `88cb5f9d2cd910fe53bf11c1875050124a09a735c8c67a63dce0ae7bc3ac5a03`.
- `08-agent-proposal-review-release.png`: captured via 3840×2160 fallback Playwright pass from
  `/proposals/proposal-snow-leopard-status`; SHA-256
  `dc09796a58ab6a3bbc857e6560012cb26d3f73bb7cbdeac7b4ae8b7a8943e30a`.

Current executable status:

- `01-search-taxonomy-home.png`: available.
- `02-search-results-disambiguation.png`: available.
- `03-entity-snow-leopard.png`: available (fallback capture).
- `04-dynamic-taxonomy-browser.png`: available (fallback capture).
- `05-knowledge-relationship-explorer.png`: available (fallback capture).
- `06-schema-domain-pack-builder.png`: available (fallback capture).
- `07-agent-maintenance-operations.png`: available (fallback capture).
- `08-agent-proposal-review-release.png`: available (fallback capture).

Generation workflow is validated with dry-run and writes the exact 8 prompt
contracts for `gpt-image-2` (`3840×2160` target is required per plan), but
actual image calls still require a valid `OPENAI_API_KEY` in the shell.

To complete once key is available:

```bash
uv run --with openai --with pillow \
  /Users/a123456/.codex/skills/.system/imagegen/scripts/image_gen.py \
  generate-batch \
  --input docs/design/image2-prompts.jsonl \
  --out-dir output/imagegen/core-v1 \
  --concurrency 2 \
  --size 3840x2160 \
  --max-attempts 3 \
  --no-augment
```

The command is unchanged from this repository's image-generation design and
keeps each screen as a separate asset for independent review, replacement, and
prompt refinement.

- The implemented Web and Admin interfaces have independently passed local
  browser visual inspection.
