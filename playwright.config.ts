import { defineConfig, devices } from "@playwright/test";

const webUrl = process.env.HARDATLAS_E2E_WEB_URL ?? "http://127.0.0.1:3000";
const adminUrl = process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";
const apiUrl = process.env.HARDATLAS_E2E_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./output/playwright/test-results",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "./output/playwright/report" }],
  ],
  use: {
    baseURL: webUrl,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "off",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        channel:
          process.env.PLAYWRIGHT_CHANNEL ??
          (process.platform === "darwin" ? "msedge" : undefined),
      },
    },
  ],
  webServer: [
    {
      command:
        "bash -lc 'mkdir -p .local && uv run alembic -c packages/py/data/alembic.ini upgrade head && uv run uvicorn hardatlas_api.app:app --host 127.0.0.1 --port 8000'",
      env: {
        HARDATLAS_DATABASE_URL:
          process.env.HARDATLAS_DATABASE_URL ??
          "sqlite+pysqlite:///./.local/hardatlas-e2e.db",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      url: `${apiUrl}/api/v1/ready`,
    },
    {
      command: "pnpm --filter @hardatlas/web dev --hostname 127.0.0.1",
      env: {
        HARDATLAS_API_URL: apiUrl,
        HARDATLAS_ALLOW_FIXTURE_FALLBACK: "false",
        NEXT_PUBLIC_HARDATLAS_WEB_URL: webUrl,
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      url: webUrl,
    },
    {
      command: "pnpm --filter @hardatlas/admin dev --hostname 127.0.0.1",
      env: {
        HARDATLAS_API_URL: apiUrl,
        NEXT_PUBLIC_HARDATLAS_AUTH_MODE: "development",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      url: adminUrl,
    },
  ],
});
