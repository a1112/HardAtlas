#!/usr/bin/env node
import { promises as fs } from "node:fs";
import { join, parse, basename } from "node:path";

const outDir = process.cwd() + "/output/imagegen/core-v1";
const expected = [
  "01-search-taxonomy-home.png",
  "02-search-results-disambiguation.png",
  "03-entity-snow-leopard.png",
  "04-dynamic-taxonomy-browser.png",
  "05-knowledge-relationship-explorer.png",
  "06-schema-domain-pack-builder.png",
  "07-agent-maintenance-operations.png",
  "08-agent-proposal-review-release.png",
];
const targetWidth = 3840;
const targetHeight = 2160;

async function readPngSize(filePath) {
  const data = await fs.readFile(filePath);
  // PNG signature check
  const signature = Buffer.from([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  ]);
  if (!data.subarray(0, 8).equals(signature)) {
    throw new Error(`not a valid PNG: ${basename(filePath)}`);
  }
  const width = data.readUInt32BE(16);
  const height = data.readUInt32BE(20);
  return { width, height };
}

async function main() {
  let failures = 0;
  for (const name of expected) {
    const path = join(outDir, name);
    try {
      const stat = await fs.stat(path);
      const { width, height } = await readPngSize(path);
      if (stat.size <= 0) {
        console.error(`FAIL: ${name} is empty`);
        failures += 1;
      } else if (width !== targetWidth || height !== targetHeight) {
        console.error(`FAIL: ${name} is ${width}×${height}`);
        failures += 1;
      } else {
        console.log(`OK: ${name} (${width}×${height})`);
      }
    } catch (error) {
      failures += 1;
      console.error(
        `FAIL: ${name} -> ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  }

  if (failures > 0) {
    console.error(`\nImage asset verification failed: ${failures} issue(s).`);
    process.exit(1);
  }
  console.log(
    `\nAll ${expected.length} image assets verified at ${targetWidth}×${targetHeight}.`,
  );
}

main().catch((error) => {
  console.error(
    `FATAL: image asset verification failed: ${error instanceof Error ? error.message : String(error)}`,
  );
  process.exit(1);
});
