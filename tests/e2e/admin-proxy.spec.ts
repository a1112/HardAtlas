import { expect, test } from "@playwright/test";

test("admin runtime should never call backend API directly from browser", async ({
  page,
}) => {
  const adminUrl =
    process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";
  const browserApiRequests: string[] = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith("/api/")) {
      browserApiRequests.push(pathname);
    }
  });

  await page.goto(adminUrl);
  await page.waitForLoadState("networkidle");

  const sawDirectApi = browserApiRequests.some((path) =>
    path.startsWith("/api/v1/"),
  );
  expect(sawDirectApi).toBeFalsy();

  const hasProxiedCalls =
    browserApiRequests.includes("/api/backend/operations/summary") ||
    browserApiRequests.some((path) => path.startsWith("/api/backend/"));
  expect(hasProxiedCalls).toBeTruthy();
});
