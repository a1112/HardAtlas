#!/usr/bin/env node
import { promises as fs } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

const outDir = process.cwd() + "/output/imagegen/core-v1";
const promptFile = process.cwd() + "/docs/design/image2-prompts.jsonl";
const manifestPath = outDir + "/manifest.json";
const generationConfig = {
  model: "gpt-image-2",
  size: "3840x2160",
  concurrency: 2,
  maxAttempts: 3,
  noAugment: true,
};

function hashBuffer(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function readPngSize(data) {
  const signature = Buffer.from([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  ]);
  if (!data.subarray(0, 8).equals(signature)) {
    throw new Error("not a valid PNG");
  }
  return {
    width: data.readUInt32BE(16),
    height: data.readUInt32BE(20),
  };
}

async function main() {
  const promptText = await fs.readFile(promptFile, "utf8");
  const prompts = promptText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
  const items = [];

  for (const entry of prompts) {
    const filePath = join(outDir, entry.out);
    const data = await fs.readFile(filePath);
    const stat = await fs.stat(filePath);
    const { width, height } = readPngSize(data);
    const promptDigest = hashBuffer(Buffer.from(entry.prompt, "utf8"));
    items.push({
      out: entry.out,
      sha256: hashBuffer(data),
      sizeBytes: stat.size,
      width,
      height,
      prompt: entry.prompt,
      promptHash: promptDigest,
    });
  }

  const manifest = {
    generatedAt: new Date().toISOString(),
    generationConfig,
    count: items.length,
    items,
  };

  await fs.writeFile(
    manifestPath,
    JSON.stringify(manifest, null, 2) + "\n",
    "utf8",
  );
  console.log(`Wrote ${manifestPath}`);
}

main().catch((error) => {
  console.error(
    `FATAL: image manifest generation failed: ${
      error instanceof Error ? error.message : String(error)
    }`,
  );
  process.exit(1);
});
