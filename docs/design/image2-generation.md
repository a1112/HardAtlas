# Image 2 generation workflow

Codex's built-in image generation path (`image_gen`) is the preferred execution
path when it is available in-session. Generate each screen as an independent
request, then copy the selected result from the Codex generated-images directory
into `output/imagegen/core-v1`.

Built-in generation is the direct workflow you asked for; use the fallback CLI
only if the in-session generator is unavailable or you need explicit model flags.

The source of truth is `image2-prompts.jsonl`: eight independent UI prompts.
The explicit CLI workflow below is retained only for environments that require
model, quality, and exact pixel controls.

## Optional CLI validation

```bash
uv run --with openai --with pillow \
  /Users/a123456/.codex/skills/.system/imagegen/scripts/image_gen.py \
  generate-batch \
  --input docs/design/image2-prompts.jsonl \
  --out-dir output/imagegen/core-v1 \
  --concurrency 2 \
  --no-augment \
  --dry-run
```

## Optional explicit CLI generation

Set `OPENAI_API_KEY` in the local shell, never in this repository or chat, then
run the same command without `--dry-run`:

```bash
uv run --with openai --with pillow \
  /Users/a123456/.codex/skills/.system/imagegen/scripts/image_gen.py \
  generate-batch \
  --input docs/design/image2-prompts.jsonl \
  --out-dir output/imagegen/core-v1 \
  --concurrency 2 \
  --max-attempts 3 \
  --no-augment
```

If the in-session generator is unavailable (for example returns backend
transport errors), fall back to the local CLI generator with an
`OPENAI_API_KEY` set in the shell.

Current workspace verification:

```bash
pnpm run verify:image-assets
```

To capture exactly what was generated and why each image exists:

```bash
pnpm run images:manifest
```

This writes `output/imagegen/core-v1/manifest.json` with:

- image filename
- SHA-256
- size and dimensions
- full prompt text
- prompt hash
- generation configuration (`gpt-image-2`, `3840x2160`, concurrency/attempts, etc.)

## Latest delivery status (2026-07-31)

All 8 assets are present under `output/imagegen/core-v1`:

1. `01-search-taxonomy-home.png`
2. `02-search-results-disambiguation.png`
3. `03-entity-snow-leopard.png`
4. `04-dynamic-taxonomy-browser.png`
5. `05-knowledge-relationship-explorer.png`
6. `06-schema-domain-pack-builder.png`
7. `07-agent-maintenance-operations.png`
8. `08-agent-proposal-review-release.png`

Notes:

- `01`–`08` are all aligned at 3840×2160.
- `01` and `02` are freshly aligned to implementation pages (`/` and `/search?q=苹果`)
  with the latest browser-based source-aligned generation.
- `03`–`08` remain implementation-aligned Playwright captures
  (`http://127.0.0.1:3000` and `http://127.0.0.1:3001`) because
  `OPENAI_API_KEY` is not configured in this environment yet.
- For the final official pack, set `OPENAI_API_KEY`, rerun the CLI command above
  and compare SHA-256 against the local catalog for regression-free replacement.

Do not use a different model as a fallback. Inspect every image at original
resolution for Chinese labels, hierarchy, state icon/text pairing, evidence,
privacy language, stray brands, and watermarks. Fix one screen at a time by
copying its JSONL record to `iterations/rejected.jsonl`, documenting the issue,
and refining only that prompt.
