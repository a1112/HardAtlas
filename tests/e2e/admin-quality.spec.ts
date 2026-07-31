import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const adminUrl = process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";

test("quality workbench discovers explainable agent-ready gaps through the Admin BFF", async ({
  page,
}, testInfo) => {
  const browserApiRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) {
      browserApiRequests.push(request.url());
    }
  });

  await page.goto(`${adminUrl}/quality`);
  await expect(
    page.getByRole("heading", { name: "知识质量维护中心" }),
  ).toBeVisible();

  const [scanResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/backend/api/v1/quality/scans") &&
        response.request().method() === "POST" &&
        response.ok(),
    ),
    page.getByRole("button", { name: "运行全库质量扫描" }).click(),
  ]);
  const scan = (await scanResponse.json()) as {
    assessed: unknown[];
    failures: unknown[];
    openTaskCount: number;
  };
  expect(scan.assessed.length).toBeGreaterThanOrEqual(3);
  expect(scan.failures).toEqual([]);
  expect(scan.openTaskCount).toBeGreaterThan(0);

  await expect(page.getByText(/完成 \d+ 项评估/)).toBeVisible();
  await expect(
    page.locator(".assessment-table").getByText("雪豹"),
  ).toBeVisible();
  const secondPageEntity = scan.assessed[20]?.entity?.canonicalName;
  const hasSecondPageData = scan.assessed.length > 20;
  const nextAssessmentButton = page.getByRole("button", {
    name: "下一页评估",
  });
  if (!(await nextAssessmentButton.isDisabled()) && hasSecondPageData && secondPageEntity) {
    await nextAssessmentButton.click();
    await expect(
      page.locator(".assessment-table").getByText(secondPageEntity),
    ).toBeVisible();
  } else {
    await expect(
      page.locator(".assessment-table").getByText("银杏"),
    ).toBeVisible();
  }
  await expect(
    page.locator(".maintenance-grid").getByText("补充证据").first(),
  ).toBeVisible();
  await expect(
    page
      .locator(".maintenance-grid")
      .getByText(/quality-.*@1.0.0/)
      .first(),
  ).toBeVisible();
  await expect(
    page.locator(".maintenance-grid").getByText("等待安全分诊").first(),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "受治理 Agent 工作队列" }),
  ).toBeVisible();
  await expect(page.getByText("租约令牌不会出现在读取接口")).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  const serious = results.violations.filter(
    (violation) =>
      violation.impact === "serious" || violation.impact === "critical",
  );
  await testInfo.attach("admin-quality-axe-results", {
    body: JSON.stringify(results, null, 2),
    contentType: "application/json",
  });
  expect(serious).toEqual([]);

  expect(browserApiRequests.length).toBeGreaterThan(0);
  expect(
    browserApiRequests.some((url) =>
      new URL(url).pathname.startsWith("/api/backend/api/v1/"),
    ),
  ).toBeTruthy();
  expect(
    browserApiRequests.every((url) => {
      const parsed = new URL(url);
      return (
        parsed.origin === adminUrl &&
        (parsed.pathname.startsWith("/api/backend/api/v1/") ||
          parsed.pathname.startsWith("/api/auth/"))
      );
    }),
  ).toBeTruthy();
});
