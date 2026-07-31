import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const adminUrl = process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";
const apiUrl = process.env.HARDATLAS_E2E_API_URL ?? "http://127.0.0.1:8000";

test("domain pack builder creates attributes and typed relationships without code changes", async ({
  page,
  request,
}, testInfo) => {
  const stamp = Date.now();
  const packId = `oceanography-${stamp}`;
  const spaceKey = `ocean-${stamp}`;
  const categoryKey = `marine-objects-${stamp}`;
  const typeKey = `marine-object-${stamp}`;
  const browserApiRequests: string[] = [];
  page.on("request", (browserRequest) => {
    if (browserRequest.url().includes("/api/")) {
      browserApiRequests.push(browserRequest.url());
    }
  });

  await page.goto(`${adminUrl}/schema/domain-packs`);
  await expect(
    page.getByRole("heading", { name: "Domain Pack 构造器" }),
  ).toBeVisible();
  await page.getByLabel("Pack ID").fill(packId);
  await page.getByLabel("显示名称").fill("海洋科学扩展包");
  await page.getByLabel("空间 key").fill(spaceKey);
  await page.getByLabel("空间名称").fill("海洋与水圈");
  await page.getByLabel("根分类 key").fill(categoryKey);
  await page.getByLabel("根分类名称").fill("海洋对象");
  await page.getByLabel("类型 key").fill(typeKey);
  await page.getByLabel("类型名称").fill("海洋对象");
  await page.getByLabel("关系 1 key").fill("co-occurs-with-species");
  await page.getByLabel("关系 1 名称").fill("与物种共现");
  await page.getByLabel("关系 1 反向名称").fill("与海洋对象共现");
  await page.getByLabel("关系 1 目标实体类型").selectOption(["type-animal"]);
  await page.getByLabel("关系 1 源基数").selectOption("many");
  await page.getByLabel("关系 1 目标基数").selectOption("one");
  await page
    .getByLabel("关系 1 限定字段", { exact: true })
    .fill("observation-method");

  await page.getByRole("button", { name: "仅做影响分析" }).click();
  await expect(page.getByText("扩展包校验通过，可以保存草稿")).toBeVisible();
  await expect(page.getByText("✓ 所有引用闭合，可安全新增")).toBeVisible();

  const [savedResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/backend/api/v1/domain-packs") &&
        response.request().method() === "POST" &&
        response.ok(),
    ),
    page.getByRole("button", { name: "保存版本草稿" }).click(),
  ]);
  const saved = (await savedResponse.json()) as {
    id: string;
    version: string;
    proposalId: string;
    relationshipTypes: Array<{
      key: string;
      sourceEntityTypeIds: string[];
      targetEntityTypeIds: string[];
      sourceCardinality: string;
      targetCardinality: string;
      qualifierSchema: Record<string, { type: string }>;
    }>;
  };
  expect(saved.id).toBe(packId);
  expect(saved.relationshipTypes).toEqual([
    expect.objectContaining({
      key: "co-occurs-with-species",
      sourceEntityTypeIds: [`type-${typeKey}`],
      targetEntityTypeIds: ["type-animal"],
      sourceCardinality: "many",
      targetCardinality: "one",
      qualifierSchema: { "observation-method": { type: "string" } },
    }),
  ]);
  await expect(page.getByText(`已保存 ${packId}@1.0.0 草稿`)).toBeVisible();
  const card = page
    .locator(".domain-pack-cards article")
    .filter({ hasText: `${packId}@1.0.0` });
  await expect(card).toContainText("1 关系");
  await expect(card.getByText(/等待双人审核/)).toBeVisible();

  const proposalResponse = await request.get(
    `${apiUrl}/api/v1/proposals/${saved.proposalId}`,
    {
      headers: {
        "x-hardatlas-dev-principal": "domain-pack-e2e-admin",
        "x-hardatlas-dev-roles": "admin",
      },
    },
  );
  expect(proposalResponse.ok()).toBeTruthy();
  const proposal = await proposalResponse.json();
  expect(proposal.proposal.operations[0].after.relationshipTypes[0].key).toBe(
    "co-occurs-with-species",
  );
  expect(
    proposal.proposal.operations[0].after.relationshipTypes[0]
      .targetEntityTypeIds,
  ).toEqual(["type-animal"]);

  const evaluated = await request.post(
    `${apiUrl}/api/v1/proposals/${saved.proposalId}/evaluate`,
    {
      headers: {
        "x-hardatlas-dev-principal": "domain-pack-e2e-admin",
        "x-hardatlas-dev-roles": "admin",
      },
    },
  );
  expect(evaluated.ok()).toBeTruthy();
  expect((await evaluated.json()).proposal.status).toBe("human-review");
  for (const reviewer of ["domain-pack-reviewer-a", "domain-pack-reviewer-b"]) {
    const reviewed = await request.post(
      `${apiUrl}/api/v1/proposals/${saved.proposalId}/reviews`,
      {
        headers: {
          "x-hardatlas-dev-principal": reviewer,
          "x-hardatlas-dev-roles": "admin",
        },
        data: {
          decision: "approve",
          comment: `${reviewer} 已核对跨领域引用与版本影响`,
        },
      },
    );
    expect(reviewed.ok()).toBeTruthy();
  }
  const published = await request.post(
    `${apiUrl}/api/v1/domain-packs/${packId}/1.0.0/publish`,
    {
      headers: {
        "x-hardatlas-dev-principal": "domain-pack-e2e-admin",
        "x-hardatlas-dev-roles": "admin",
      },
    },
  );
  expect(published.ok()).toBeTruthy();
  expect((await published.json()).rollbackSnapshot).toBeTruthy();

  await page.reload();
  await expect(card.getByText("published", { exact: true })).toBeVisible();
  const [analysisResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(
            `/api/backend/api/v1/domain-packs/${packId}/1.0.0/rollback-analysis`,
          ) &&
        response.request().method() === "GET" &&
        response.ok(),
    ),
    card.getByRole("button", { name: "回滚影响分析" }).click(),
  ]);
  expect((await analysisResponse.json()).safe).toBe(true);
  await expect(
    page.getByRole("heading", { name: `${packId}@1.0.0` }),
  ).toBeVisible();
  await expect(page.getByText("✓ 可以安全回滚")).toBeVisible();
  await expect(page.getByText("+7 新增")).toBeVisible();
  await page
    .getByLabel("回滚审计说明")
    .fill("E2E 验证发布前快照、乐观锁、Outbox 与审计链");
  const [rollbackResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .endsWith(
            `/api/backend/api/v1/domain-packs/${packId}/1.0.0/rollback`,
          ) &&
        response.request().method() === "POST" &&
        response.ok(),
    ),
    page.getByRole("button", { name: "执行安全回滚" }).click(),
  ]);
  expect((await rollbackResponse.json()).rolledBack.status).toBe("rolled-back");
  await expect(card.getByText("rolled-back", { exact: true })).toBeVisible();
  await expect(card.getByText(/已回滚/)).toBeVisible();
  const registryAfterRollback = await request.get(
    `${apiUrl}/api/v1/schema-registry`,
    {
      headers: {
        "x-hardatlas-dev-principal": "domain-pack-e2e-admin",
        "x-hardatlas-dev-roles": "admin",
      },
    },
  );
  expect(registryAfterRollback.ok()).toBeTruthy();
  expect(
    (await registryAfterRollback.json()).entityTypes.some(
      (item: { id: string }) => item.id === `type-${typeKey}`,
    ),
  ).toBe(false);

  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  await testInfo.attach("domain-pack-axe-results", {
    body: JSON.stringify(accessibility, null, 2),
    contentType: "application/json",
  });
  expect(
    accessibility.violations.filter(
      (violation) =>
        violation.impact === "serious" || violation.impact === "critical",
    ),
  ).toEqual([]);
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
