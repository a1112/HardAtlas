import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const adminUrl = process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";
const apiUrl = process.env.HARDATLAS_E2E_API_URL ?? "http://127.0.0.1:8000";

test("existing entry revision stays a governed proposal and never publishes from the editor", async ({
  page,
  request,
}, testInfo) => {
  const beforeResponse = await request.get(`${apiUrl}/api/v1/entities/ginkgo`);
  expect(beforeResponse.ok()).toBeTruthy();
  const before = (await beforeResponse.json()) as {
    description: Array<{ value: string }>;
    relationships: unknown[];
    revision: { revisionId: string };
  };
  const browserApiRequests: string[] = [];
  page.on("request", (browserRequest) => {
    if (browserRequest.url().includes("/api/")) {
      browserApiRequests.push(browserRequest.url());
    }
  });

  await page.goto(`${adminUrl}/entries`);
  await expect(
    page.getByRole("heading", { name: "通用条目工作室" }),
  ).toBeVisible();
  await expect(page.getByText("条目、Schema 与分类注册表已同步")).toBeVisible();
  await page.getByRole("button", { name: "修订现有条目" }).click();
  await page.getByLabel("现有条目").selectOption("entity-ginkgo");
  await page.getByRole("button", { name: "载入并钉住修订" }).click();

  await expect(
    page.getByText(`已钉住基础修订 ${before.revision.revisionId}`),
  ).toBeVisible();
  await expect(page.getByLabel("实体 ID")).toBeDisabled();
  await expect(page.getByLabel("稳定 URL slug")).toBeDisabled();
  const revisedDescription = `银杏编辑工作台验证 ${Date.now()}`;
  await page.getByLabel("条目摘要").fill(revisedDescription);
  await expect(
    page.locator(".entry-live-preview").getByText(revisedDescription),
  ).toBeVisible();
  await page.getByRole("button", { name: "添加关系" }).click();
  await page.getByLabel("目标条目").selectOption("entity-snow-leopard");
  await page.getByLabel("限定字段 · rank").fill("e2e-parent");
  await expect(
    page.locator(".entry-live-preview").getByText("分类上级 → 雪豹"),
  ).toBeVisible();
  const [draftResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("/api/backend/api/v1/authoring-drafts") &&
        response.request().method() === "POST" &&
        response.status() === 201,
    ),
    page.getByRole("button", { name: "保存私有草稿" }).click(),
  ]);
  const savedDraft = (await draftResponse.json()) as {
    id: string;
    version: number;
  };
  expect(savedDraft.version).toBe(1);
  await expect(page.getByText(/草稿已保存 · v1/)).toBeVisible();

  await page.reload();
  await expect(page.getByText("条目、Schema 与分类注册表已同步")).toBeVisible();
  await page.getByLabel("编辑中的草稿").selectOption(savedDraft.id);
  await page.getByRole("button", { name: "恢复草稿" }).click();
  await expect(
    page.getByText(`已恢复草稿 ${savedDraft.id} · v1`),
  ).toBeVisible();
  await expect(page.getByLabel("条目摘要")).toHaveValue(revisedDescription);
  await expect(page.getByLabel("实体 ID")).toBeDisabled();
  await expect(page.getByLabel("目标条目")).toHaveValue("entity-snow-leopard");
  await expect(page.getByLabel("限定字段 · rank")).toHaveValue("e2e-parent");
  await expect(
    page.locator(".entry-live-preview").getByText(revisedDescription),
  ).toBeVisible();

  await page.getByRole("button", { name: "校验并提交治理提案" }).click();

  await expect(
    page.getByText(/草稿 v3 已提交，创建 \d+ 项差异操作/),
  ).toBeVisible();
  const proposalLink = page.getByRole("link", { name: /审核提案/ });
  await expect(proposalLink).toBeVisible();
  await proposalLink.click();
  await expect(page).toHaveURL(/\/proposals\/proposal-revise-ginkgo-/);
  await expect(
    page.getByText("human-review").or(page.getByText("proposed")),
  ).toBeVisible();

  const afterResponse = await request.get(`${apiUrl}/api/v1/entities/ginkgo`);
  expect(afterResponse.ok()).toBeTruthy();
  const after = (await afterResponse.json()) as {
    description: Array<{ value: string }>;
    relationships: unknown[];
    revision: { revisionId: string };
  };
  expect(after.revision.revisionId).toBe(before.revision.revisionId);
  expect(after.description[0]?.value).toBe(before.description[0]?.value);
  expect(after.relationships).toEqual(before.relationships);

  await page.goto(`${adminUrl}/entries`);
  await expect(page.getByText("条目、Schema 与分类注册表已同步")).toBeVisible();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  const serious = results.violations.filter(
    (violation) =>
      violation.impact === "serious" || violation.impact === "critical",
  );
  await testInfo.attach("authoring-axe-results", {
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
