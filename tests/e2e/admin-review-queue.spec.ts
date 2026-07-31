import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const adminUrl = process.env.HARDATLAS_E2E_ADMIN_URL ?? "http://127.0.0.1:3001";
const apiUrl = process.env.HARDATLAS_E2E_API_URL ?? "http://127.0.0.1:8000";

test("conflict-aware queue merges path choices before approving the governed result", async ({
  page,
  request,
}, testInfo) => {
  const stamp = Date.now();
  const adminHeaders = {
    "x-hardatlas-dev-principal": "review-queue-e2e-admin",
    "x-hardatlas-dev-display-name": "Review Queue E2E Admin",
    "x-hardatlas-dev-roles": "admin",
  };
  const apiGet = (path: string) =>
    request.get(`${apiUrl}${path}`, { headers: adminHeaders });
  const apiPost = (path: string, data?: object) =>
    request.post(`${apiUrl}${path}`, {
      headers: adminHeaders,
      ...(data ? { data } : {}),
    });
  const entityId = `entity-review-e2e-${stamp}`;
  const slug = `review-e2e-${stamp}`;
  const createProposalId = `proposal-create-review-e2e-${stamp}`;
  const createReleaseId = `release-create-review-e2e-${stamp}`;

  const ginkgoContextResponse = await apiGet(
    "/api/v1/entities/entity-ginkgo/authoring-draft",
  );
  expect(ginkgoContextResponse.ok()).toBeTruthy();
  const ginkgoContext = (await ginkgoContextResponse.json()) as {
    draft: Record<string, unknown> & {
      names: Array<{ locale: string; value: string }>;
      description: Array<{ locale: string; value: string }>;
    };
  };
  const creationDraft = structuredClone(ginkgoContext.draft);
  Object.assign(creationDraft, {
    id: entityId,
    slug,
    names: [{ locale: "zh-CN", value: `审核队列测试条目 ${stamp}` }],
    aliases: [],
    description: [{ locale: "zh-CN", value: "用于隔离验证并发提案冲突。" }],
    relationships: [],
  });
  const creation = await apiPost("/api/v1/entity-drafts/proposals", {
    proposalId: createProposalId,
    agentRunId: `run-${createProposalId}`,
    confidence: 0.98,
    risk: "low",
    draft: creationDraft,
  });
  expect(creation.ok()).toBeTruthy();
  const evaluatedCreation = await apiPost(
    `/api/v1/proposals/${createProposalId}/evaluate`,
  );
  expect(evaluatedCreation.ok()).toBeTruthy();
  expect((await evaluatedCreation.json()).proposal.status).toBe(
    "policy-approved",
  );
  expect(
    (await apiPost(`/api/v1/proposals/${createProposalId}/accept-policy`)).ok(),
  ).toBeTruthy();
  expect(
    (
      await apiPost("/api/v1/releases", {
        id: createReleaseId,
        proposalIds: [createProposalId],
        dataVersion: `atlas-review-e2e-${stamp}`,
        schemaVersions: ["schema-2.2.0"],
        previousReleaseId: "atlas-2026.07.29",
      })
    ).ok(),
  ).toBeTruthy();
  expect(
    (await apiPost(`/api/v1/releases/${createReleaseId}/publish`)).ok(),
  ).toBeTruthy();

  const releaseBrowserApiRequests: string[] = [];
  page.on("request", (browserRequest) => {
    if (browserRequest.url().includes("/api/")) {
      releaseBrowserApiRequests.push(browserRequest.url());
    }
  });
  await page.goto(`${adminUrl}/releases`);
  await expect(
    page.getByRole("heading", { name: "发布与回滚工作台" }),
  ).toBeVisible();
  const releaseCard = page
    .locator(".release-manifest-list > article")
    .filter({ hasText: createReleaseId });
  await expect(releaseCard).toBeVisible();
  const [verificationResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response
          .url()
          .includes(`/api/backend/api/v1/releases/${createReleaseId}/verify`) &&
        response.request().method() === "POST",
    ),
    releaseCard.getByRole("button", { name: "立即验证" }).click(),
  ]);
  expect(verificationResponse.ok()).toBeTruthy();
  await expect(releaseCard.locator("[data-verification=passed]")).toBeVisible();
  await expect(releaseCard.getByText("search-discoverability")).toBeVisible();
  await expect(page.getByText("发布后完整性验证已通过")).toBeVisible();
  expect(
    releaseBrowserApiRequests.every((url) =>
      url.startsWith(`${adminUrl}/api/`),
    ),
  ).toBeTruthy();
  const releaseAccessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    releaseAccessibility.violations.filter(
      (violation) =>
        violation.impact === "serious" || violation.impact === "critical",
    ),
  ).toEqual([]);

  const contextResponse = await apiGet(
    `/api/v1/entities/${entityId}/authoring-draft`,
  );
  expect(contextResponse.ok()).toBeTruthy();
  const context = (await contextResponse.json()) as {
    draft: typeof ginkgoContext.draft;
    baseRevisionId: string;
  };
  const proposalIds = [
    `proposal-review-e2e-a-${stamp}`,
    `proposal-review-e2e-b-${stamp}`,
  ];
  for (const [index, proposalId] of proposalIds.entries()) {
    const draft = structuredClone(context.draft);
    draft.description[0].value = `并发候选描述 ${index + 1}`;
    const proposed = await apiPost(
      `/api/v1/entities/${entityId}/draft-proposals`,
      {
        proposalId,
        agentRunId: `run-${proposalId}`,
        confidence: 0.98,
        risk: "medium",
        draft,
        baseRevisionId: context.baseRevisionId,
      },
    );
    expect(proposed.ok()).toBeTruthy();
    const evaluated = await apiPost(`/api/v1/proposals/${proposalId}/evaluate`);
    expect(evaluated.ok()).toBeTruthy();
    expect((await evaluated.json()).proposal.status).toBe("human-review");
  }
  const evaluationProposalId = `proposal-review-e2e-evaluate-${stamp}`;
  const evaluationDraft = structuredClone(context.draft);
  evaluationDraft.aliases = [
    {
      locale: "zh-CN",
      value: `策略评估候选别名 ${stamp}`,
    },
  ];
  const evaluationCandidate = await apiPost(
    `/api/v1/entities/${entityId}/draft-proposals`,
    {
      proposalId: evaluationProposalId,
      agentRunId: `run-${evaluationProposalId}`,
      confidence: 0.98,
      risk: "medium",
      draft: evaluationDraft,
      baseRevisionId: context.baseRevisionId,
    },
  );
  expect(evaluationCandidate.ok()).toBeTruthy();
  expect((await evaluationCandidate.json()).proposal.status).toBe("proposed");

  const browserApiRequests: string[] = [];
  page.on("request", (browserRequest) => {
    if (browserRequest.url().includes("/api/")) {
      browserApiRequests.push(browserRequest.url());
    }
  });
  await page.goto(`${adminUrl}/reviews`);
  await expect(
    page.getByRole("heading", { name: "提案审核队列" }),
  ).toBeVisible();

  const queueSearch = page.getByLabel("搜索提案、实体、Agent 或路径");
  await queueSearch.fill(evaluationProposalId);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(
    page.getByText(evaluationProposalId, { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/状态版本 v\d+/).first()).toBeVisible();
  await page.getByLabel(`选择 ${evaluationProposalId}`).check();
  await page.getByRole("button", { name: "批量策略评估" }).click();
  await expect(page.getByText(/已评估 1 项提案/)).toBeVisible();
  await expect(page.getByLabel(`选择 ${evaluationProposalId}`)).toBeEnabled();
  await page.getByLabel(`选择 ${evaluationProposalId}`).check();
  await page.getByRole("button", { name: "批量拒绝" }).click();
  await expect(page.getByText("已拒绝 1 项提案")).toBeVisible();

  await queueSearch.fill(entityId);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(proposalIds[0], { exact: true })).toBeVisible();
  await expect(page.getByText(proposalIds[1], { exact: true })).toBeVisible();
  await expect(
    page
      .locator(".conflict-ledger article")
      .filter({ hasText: proposalIds[0] })
      .filter({ hasText: proposalIds[1] }),
  ).toBeVisible();

  await page.getByRole("button", { name: "逐路径合并" }).click();
  await expect(
    page.getByRole("region", { name: "冲突合并方案" }),
  ).toBeVisible();
  await expect(
    page.locator(".merge-composer legend").filter({ hasText: "/description" }),
  ).toBeVisible();
  await expect(
    page.locator(".merge-composer input[type=radio]:checked").first(),
  ).toBeChecked();
  const [mergeResponse] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/proposals/conflicts/") &&
        response.url().endsWith("/merge") &&
        response.request().method() === "POST",
    ),
    page.getByRole("button", { name: "创建受治理合并提案" }).click(),
  ]);
  expect(mergeResponse.ok()).toBeTruthy();
  const mergePayload = (await mergeResponse.json()) as {
    proposal: { proposal: { id: string; status: string } };
    supersededProposals: Array<{
      proposal: { id: string; status: string };
      supersededByProposalId: string;
    }>;
  };
  const mergeProposalId = mergePayload.proposal.proposal.id;
  expect(mergePayload.proposal.proposal.status).toBe("proposed");
  expect(
    mergePayload.supersededProposals.map((item) => item.proposal.status),
  ).toEqual(["superseded", "superseded"]);
  expect(
    mergePayload.supersededProposals.every(
      (item) => item.supersededByProposalId === mergeProposalId,
    ),
  ).toBeTruthy();
  await expect(
    page.getByText(new RegExp(`已创建合并提案 ${mergeProposalId}`)),
  ).toBeVisible();

  await queueSearch.fill(mergeProposalId);
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page.getByText(mergeProposalId, { exact: true })).toBeVisible();
  await page.getByLabel(`选择 ${mergeProposalId}`).check();
  await page.getByRole("button", { name: "批量策略评估" }).click();
  await expect(page.getByText(/已评估 1 项提案/)).toBeVisible();
  await page.getByLabel(`选择 ${mergeProposalId}`).check();
  await page.getByRole("button", { name: "批量批准" }).click();
  await expect(page.getByText("已批准 1 项提案")).toBeVisible();

  const merged = await apiGet(`/api/v1/proposals/${mergeProposalId}`);
  expect((await merged.json()).proposal.status).toBe("accepted");
  for (const proposalId of proposalIds) {
    const source = await apiGet(`/api/v1/proposals/${proposalId}`);
    const sourcePayload = await source.json();
    expect(sourcePayload.proposal.status).toBe("superseded");
    expect(sourcePayload.supersededByProposalId).toBe(mergeProposalId);
  }
  const accessibility = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  await testInfo.attach("review-queue-axe-results", {
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
