import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ context }) => {
  await context.addCookies([
    {
      name: "atlas-locale",
      value: "zh-CN",
      url: "http://127.0.0.1:3000",
    },
  ]);
});

test("domain cards open a scalable selected taxonomy view", async ({
  page,
}) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "探索世界的每一种知识" }),
  ).toBeVisible();
  await page.locator("a.space-card[href*='space=space-life']").first().click();

  await expect(page).toHaveURL(/\/categories\?space=space-life/);
  await expect(page.getByRole("heading", { name: "动物" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: /查看该分类全部条目/ }),
  ).toHaveAttribute("href", /taxonomy=tax-animals/);
});

test("search browse filters execute in the backend and expose match reasons", async ({
  page,
}) => {
  await page.goto(
    "/search?q=&space=space-life&taxonomy=tax-animals&locale=la&sourceTier=authoritative&minimumSources=1",
  );

  await expect(page.getByText("找到 1 个匹配条目")).toBeVisible();
  await expect(page.getByRole("heading", { name: "雪豹" })).toBeVisible();
  await expect(page.getByText(/分类浏览 · 雪豹/)).toBeVisible();
  await expect(page.getByLabel("知识领域")).toHaveValue("space-life");
  await expect(page.getByLabel("分类节点")).toHaveValue("tax-animals");
  await expect(page.locator('select[name="locale"]')).toHaveValue("la");
  await expect(page.getByLabel("来源等级")).toHaveValue("authoritative");
});

test("category page links to the same filtered search context", async ({
  page,
}) => {
  await page.goto("/categories?space=space-life&taxonomy=tax-animals");

  await expect(page.getByRole("heading", { name: "分类体系" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "动物" })).toBeVisible();

  const link = page.getByRole("link", { name: /查看该分类全部条目/ });
  await expect(link).toHaveAttribute(
    "href",
    /\/search\?space=space-life&taxonomy=tax-animals/,
  );

  await link.click();
  await expect(page).toHaveURL(
    /\/search\?space=space-life&taxonomy=tax-animals/,
  );
  await expect(page.getByRole("heading", { name: "全部条目" })).toBeVisible();
});

test("category page relies on web-proxied API calls during client interactions", async ({
  page,
}) => {
  const browserApiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) {
      browserApiRequests.push(new URL(request.url()).pathname);
    }
  });

  await page.goto(
    "/categories?space=space-engineering&taxonomy=tax-electronics",
  );
  await expect(page.getByRole("heading", { name: "电子元件" })).toBeVisible();
  const sawDirectApi = browserApiRequests.some((path) =>
    path.startsWith("/api/v1/"),
  );
  expect(sawDirectApi).toBeFalsy();
});

test("content language preference survives navigation and localizes entries", async ({
  page,
}) => {
  await page.goto("/entry/snow-leopard");

  await page.getByLabel("内容语言").first().selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(
    page.getByRole("heading", { level: 1, name: "Snow leopard" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { level: 2, name: "Overview" }),
  ).toBeVisible();

  await page.goto("/categories?space=space-engineering");
  await expect(
    page.getByText("Engineering and Technology").first(),
  ).toBeVisible();
});

test("natural-language answers stay same-origin and expose fixed evidence", async ({
  page,
}, testInfo) => {
  const browserApiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) {
      browserApiRequests.push(request.url());
    }
  });

  await page.goto("/ask?q=雪豹生活在什么环境");

  await expect(
    page.getByRole("heading", { name: "雪豹生活在什么环境" }),
  ).toBeVisible();
  await expect(page.getByText("证据充分")).toBeVisible();
  await expect(page.getByText(/高海拔环境/).first()).toBeVisible();
  await expect(page.getByText(/IUCN Red List/).first()).toBeVisible();
  await expect(page.getByText(/rev-snow-leopard/).first()).toBeVisible();
  expect(
    browserApiRequests.some(
      (url) => new URL(url).pathname === "/api/backend/answers",
    ),
  ).toBeTruthy();
  expect(
    browserApiRequests.every(
      (url) => new URL(url).origin === "http://127.0.0.1:3000",
    ),
  ).toBeTruthy();

  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  await testInfo.attach("answer-axe-results", {
    body: JSON.stringify(results, null, 2),
    contentType: "application/json",
  });
  expect(
    results.violations.filter(
      (violation) =>
        violation.impact === "serious" || violation.impact === "critical",
    ),
  ).toEqual([]);
});

test("fixed revision URLs remain immutable public resources", async ({
  page,
  request,
}) => {
  const response = await request.get(
    "http://127.0.0.1:8000/api/v1/entities/ginkgo",
  );
  expect(response.ok()).toBeTruthy();
  const entity = (await response.json()) as {
    ref: { slug: string };
    revision: { revisionId: string };
  };

  await page.goto(
    `/entry/${entity.ref.slug}/revisions/${entity.revision.revisionId}`,
  );
  await expect(page.getByText("正在查看历史修订")).toBeVisible();
  await expect(
    page.getByText(entity.revision.revisionId).first(),
  ).toBeVisible();

  const fixed = await request.get(
    `http://127.0.0.1:8000/api/v1/entities/${entity.ref.slug}/revisions/${entity.revision.revisionId}`,
  );
  expect(fixed.headers()["cache-control"]).toContain("immutable");
  expect(fixed.headers().etag).toBeTruthy();
});

test("public discovery has no serious automated accessibility violations", async ({
  page,
}, testInfo) => {
  await page.goto("/search?q=Ginkgo");
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  const serious = results.violations.filter(
    (violation) =>
      violation.impact === "serious" || violation.impact === "critical",
  );
  await testInfo.attach("axe-results", {
    body: JSON.stringify(results, null, 2),
    contentType: "application/json",
  });
  expect(serious).toEqual([]);
});

test("topics page presents published domains as cards", async ({ page }) => {
  await page.goto("/topics");

  await expect(
    page.getByRole("heading", { level: 1, name: "专题入口" }),
  ).toBeVisible();
  await expect(
    page.locator(".topic-card").filter({ hasText: "Life" }).or(
      page.locator(".topic-card").filter({ hasText: "生命" }),
    ),
  ).toBeVisible();
});

test("about page explains universal encyclopedia goals", async ({ page }) => {
  await page.goto("/about");

  await expect(
    page.getByRole("heading", { level: 1, name: "Atlas 维基栈" }),
  ).toBeVisible();
  await expect(page.getByText("架构目标")).toBeVisible();
  await expect(page.getByText("扩展策略")).toBeVisible();
});
