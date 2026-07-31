#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const scanDirs = [path.join(root, "apps/web"), path.join(root, "apps/admin")];
const includeExtensions = new Set([".ts", ".tsx", ".js", ".jsx"]);
const skipPathFragments = [
  "/.next/",
  "/node_modules/",
  "/dist/",
  "/target/",
  "/output/",
  "/.turbo/",
];

function shouldSkip(filePath) {
  const normalized = filePath.replace(/\\/g, "/");
  return skipPathFragments.some((fragment) => normalized.includes(fragment));
}

const violations = [];

function checkFile(filePath) {
  const lines = fs.readFileSync(filePath, "utf8").split(/\r?\n/);
  for (const [index, line] of lines.entries()) {
    if (!line.includes("/api/v1/")) continue;

    const trimmed = line.trim();

    if (
      trimmed.includes("fetch(") &&
      trimmed.includes("/api/v1/") &&
      !trimmed.includes("/api/backend")
    ) {
      violations.push({
        file: path.relative(root, filePath),
        line: index + 1,
        text: trimmed,
      });
      continue;
    }

    if (
      trimmed.includes("adminFetch(") &&
      trimmed.includes("/api/v1/") &&
      !trimmed.includes("/api/backend") &&
      !trimmed.includes("apiUrl")
    ) {
      violations.push({
        file: path.relative(root, filePath),
        line: index + 1,
        text: trimmed,
      });
    }
  }
}

for (const scanDir of scanDirs) {
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const fullPath = path.join(dir, entry.name);
      if (shouldSkip(fullPath)) continue;
      if (entry.isDirectory()) {
        walk(fullPath);
        continue;
      }
      if (!includeExtensions.has(path.extname(entry.name))) continue;
      const normalized = fullPath.replace(/\\/g, "/");
      if (normalized.includes("/tests/")) continue;
      checkFile(fullPath);
    }
  };
  walk(scanDir);
}

if (violations.length > 0) {
  console.error(
    "Raw browser callsite check found /api/v1 paths not prefixed by BFF helper:\n",
  );
  for (const item of violations) {
    console.error(`${item.file}:${item.line}: ${item.text}`);
  }
  process.exitCode = 1;
} else {
  console.log(
    "PASS: Browser callsites in web/admin do not call /api/v1 directly (or include BFF path).",
  );
}
