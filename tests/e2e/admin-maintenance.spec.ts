import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const adminUrl = process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";

test("source maintenance stays behind the Admin BFF and exposes its durable pipeline", async ({
  page,
}, testInfo) => {
  const browserApiRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) {
      browserApiRequests.push(request.url());
    }
  });
  const suffix = `${Date.now()}-${testInfo.workerIndex}`;
  const sourceId = `source-e2e-${suffix}`;

  await page.goto(`${adminUrl}/sources`);
  await expect(
    page.getByRole("heading", { name: "来源与许可中心" }),
  ).toBeVisible();
  await expect(page.getByText("来源与解析器注册表已同步")).toBeVisible();

  await page.getByLabel("来源 ID").fill(sourceId);
  await page.getByLabel("显示名称").fill(`E2E governed source ${suffix}`);
  await page
    .getByLabel("基础 URL")
    .fill(`https://data.example.org/${sourceId}.json`);
  await page.getByLabel("实体类型范围").fill("type-plant");
  await page.getByLabel("分类节点范围").fill("tax-plants");
  await page.getByLabel("来源类型").selectOption("dataset");
  await page.getByLabel("许可标识").fill("CC-BY-4.0");
  await page.getByLabel("许可状态").selectOption("allowed");
  await page.getByLabel("robots 策略").selectOption("not-applicable");
  await page.getByLabel("robots 状态").selectOption("allowed");
  await page.getByLabel("初始状态").selectOption("active");
  await page.getByLabel("允许媒体类型（逗号分隔）").fill("application/json");
  await page.getByLabel("解析器 ID").fill("parser-animals-json");
  await page.getByLabel("解析器版本").fill("1.0.0");
  await page.getByRole("button", { name: "保存来源版本" }).click();

  const sourceCard = page.locator("article").filter({ hasText: sourceId });
  await expect(sourceCard).toBeVisible();
  await expect(sourceCard.getByText("可采集")).toBeVisible();
  await expect(sourceCard.getByText(/类型：type-plant/)).toBeVisible();
  await expect(sourceCard.getByText(/分类：tax-plants/)).toBeVisible();
  await sourceCard.getByRole("button", { name: "请求采集" }).click();
  await expect(sourceCard.getByText(/1 个采集任务/)).toBeVisible();
  await expect(sourceCard.getByText("queued", { exact: true })).toBeVisible();
  await expect(sourceCard.getByText("自动维护流水线")).toBeVisible();
  await expect(sourceCard.getByText(/可靠事件待派发/)).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  const serious = results.violations.filter(
    (violation) =>
      violation.impact === "serious" || violation.impact === "critical",
  );
  await testInfo.attach("admin-axe-results", {
    body: JSON.stringify(results, null, 2),
    contentType: "application/json",
  });
  expect(serious).toEqual([]);

  expect(browserApiRequests.length).toBeGreaterThan(0);
  expect(
    browserApiRequests.every((url) => {
      const parsed = new URL(url);
      return (
        parsed.origin === adminUrl &&
        parsed.pathname.startsWith("/api/backend/api/v1/")
      );
    }),
  ).toBeTruthy();
});
