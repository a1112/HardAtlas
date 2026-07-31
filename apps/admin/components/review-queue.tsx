"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface GovernedProposal {
  version: number;
  proposal: {
    id: string;
    entityId?: string;
    proposalType: string;
    risk: string;
    status: string;
    agentRunId: string;
    operations: Array<{
      path: string;
      operation: string;
      before?: unknown;
      after?: unknown;
      confidence: number;
      citationIds: string[];
    }>;
  };
  policyEvaluation?: {
    requiredApprovals: number;
    blockers: string[];
  };
  reviews: Array<{ reviewerId: string; decision: string }>;
  sourceProposalIds?: string[];
  supersededByProposalId?: string;
}

interface ProposalConflict {
  id: string;
  proposalIds: string[];
  entityId?: string;
  paths: string[];
  reason: string;
  blocking: boolean;
}

interface ProposalQueueItem {
  governed: GovernedProposal;
  conflictIds: string[];
}

interface ProposalQueueResponse {
  items: ProposalQueueItem[];
  conflicts: ProposalConflict[];
  total: number;
  offset: number;
  limit: number;
  hasMore: boolean;
  totalConflicts: number;
  statusCounts: Record<string, number>;
}

const apiUrl = "/api/backend";
const pageSize = 20;
const developmentAuth =
  (process.env.NEXT_PUBLIC_HARDATLAS_AUTH_MODE ?? "development") ===
  "development";

const statusLabel: Record<string, string> = {
  proposed: "待策略评估",
  "policy-approved": "策略通过",
  "policy-blocked": "策略阻断",
  "human-review": "等待人工审核",
  accepted: "已接受",
  rejected: "已拒绝",
  superseded: "已被合并取代",
  released: "已发布",
};

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (
    detail &&
    typeof detail === "object" &&
    "code" in detail &&
    detail.code === "proposal-version-conflict"
  ) {
    const conflict = detail as {
      proposalId?: string;
      expectedVersion?: number;
      actualVersion?: number;
    };
    return `提案 ${conflict.proposalId ?? ""} 已被其他 Agent 或审核者更新（本地 v${conflict.expectedVersion ?? "?"}，当前 v${conflict.actualVersion ?? "?"}），请刷新后重试。`;
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return "治理批处理请求失败";
}

function pathsOverlap(left: string, right: string) {
  return (
    left === right ||
    left.startsWith(`${right}/`) ||
    right.startsWith(`${left}/`)
  );
}

function compactValue(value: unknown) {
  const serialized =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  if (!serialized) return "—";
  return serialized.length > 280 ? `${serialized.slice(0, 277)}…` : serialized;
}

export function ReviewQueue() {
  const [items, setItems] = useState<ProposalQueueItem[]>([]);
  const [conflicts, setConflicts] = useState<ProposalConflict[]>([]);
  const [total, setTotal] = useState(0);
  const [totalConflicts, setTotalConflicts] = useState(0);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [operatorId, setOperatorId] = useState("governance-operator-local");
  const [notice, setNotice] = useState("正在同步治理队列…");
  const [busy, setBusy] = useState(false);
  const [offset, setOffset] = useState(0);
  const [statusFilter, setStatusFilter] = useState("active");
  const [conflictsOnly, setConflictsOnly] = useState(false);
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [mergeConflict, setMergeConflict] = useState<ProposalConflict | null>(
    null,
  );
  const [mergeSources, setMergeSources] = useState<GovernedProposal[]>([]);
  const [mergeSelections, setMergeSelections] = useState<
    Record<string, string>
  >({});
  const [mergeComment, setMergeComment] = useState(
    "逐路径核对证据后合并，保留两个来源提案的完整审计链",
  );

  const refresh = useCallback(async () => {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(pageSize),
    });
    if (statusFilter !== "active") params.append("status", statusFilter);
    if (conflictsOnly) params.set("conflictsOnly", "true");
    if (query) params.set("q", query);
    const response = await adminFetch(
      `${apiUrl}/api/v1/proposals/queue?${params.toString()}`,
      { cache: "no-store" },
    );
    if (!response.ok) {
      throw new Error("治理队列 API 返回异常");
    }
    const payload = (await response.json()) as ProposalQueueResponse;
    setItems(payload.items);
    setConflicts(payload.conflicts);
    setTotal(payload.total);
    setTotalConflicts(payload.totalConflicts);
    setStatusCounts(payload.statusCounts);
    setSelectedIds((current) =>
      current.filter((id) =>
        payload.items.some(
          (item) =>
            item.governed.proposal.id === id &&
            ["proposed", "human-review"].includes(
              item.governed.proposal.status,
            ),
        ),
      ),
    );
    setNotice(
      `已载入 ${payload.items.length} / ${payload.total} 项 · ${payload.totalConflicts} 组活动冲突`,
    );
  }, [conflictsOnly, offset, query, statusFilter]);

  useEffect(() => {
    void refresh().catch((error: unknown) =>
      setNotice(error instanceof Error ? error.message : "治理队列不可用"),
    );
  }, [refresh]);

  const conflictMap = useMemo(() => {
    const result = new Map<string, ProposalConflict[]>();
    for (const conflict of conflicts) {
      for (const proposalId of conflict.proposalIds) {
        result.set(proposalId, [...(result.get(proposalId) ?? []), conflict]);
      }
    }
    return result;
  }, [conflicts]);

  const selectedProposals = useMemo(
    () =>
      selectedIds
        .map(
          (id) =>
            items.find((item) => item.governed.proposal.id === id)?.governed
              .proposal,
        )
        .filter((proposal) => proposal !== undefined),
    [items, selectedIds],
  );
  const canEvaluate =
    selectedProposals.length > 0 &&
    selectedProposals.every((proposal) => proposal.status === "proposed");
  const canReview =
    selectedProposals.length > 0 &&
    selectedProposals.every((proposal) => proposal.status === "human-review");

  function changeStatus(value: string) {
    setStatusFilter(value);
    setOffset(0);
    setSelectedIds([]);
  }

  function applySearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(queryDraft.trim());
    setOffset(0);
    setSelectedIds([]);
  }

  function toggle(proposalId: string) {
    setSelectedIds((current) =>
      current.includes(proposalId)
        ? current.filter((id) => id !== proposalId)
        : [...current, proposalId],
    );
  }

  function developmentHeaders(): HeadersInit {
    return developmentAuth
      ? {
          "x-hardatlas-dev-principal": operatorId,
          "x-hardatlas-dev-display-name": operatorId,
          "x-hardatlas-dev-roles": "admin",
        }
      : {};
  }

  async function bulkEvaluate() {
    if (!canEvaluate) return;
    setBusy(true);
    setNotice("正在预检并批量执行策略评估…");
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/proposals/bulk-evaluations`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            ...developmentHeaders(),
          },
          body: JSON.stringify({ proposalIds: selectedIds }),
        },
      );
      const payload = (await response.json()) as {
        proposals?: GovernedProposal[];
        detail?: unknown;
      };
      if (!response.ok || !payload.proposals) {
        throw new Error(detailText(payload.detail));
      }
      setSelectedIds([]);
      await refresh();
      const summary = payload.proposals.reduce<Record<string, number>>(
        (counts, governed) => {
          const proposalStatus = governed.proposal.status;
          counts[proposalStatus] = (counts[proposalStatus] ?? 0) + 1;
          return counts;
        },
        {},
      );
      setNotice(
        `已评估 ${payload.proposals.length} 项提案 · ${Object.entries(summary)
          .map(([key, count]) => `${statusLabel[key] ?? key} ${count}`)
          .join(" · ")}`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "批量策略评估失败");
    } finally {
      setBusy(false);
    }
  }

  async function bulkReview(decision: "approve" | "reject") {
    if (!canReview) return;
    setBusy(true);
    setNotice(
      decision === "approve" ? "正在预检并批量批准…" : "正在预检并批量拒绝…",
    );
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/proposals/bulk-reviews`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            ...developmentHeaders(),
          },
          body: JSON.stringify({
            proposalIds: selectedIds,
            decision,
            comment:
              decision === "approve"
                ? "批量核验证据、路径冲突与影响范围"
                : "批量拒绝以解决冲突或要求补充证据",
          }),
        },
      );
      const payload = (await response.json()) as {
        proposals?: GovernedProposal[];
        detail?: unknown;
      };
      if (!response.ok || !payload.proposals) {
        throw new Error(detailText(payload.detail));
      }
      setSelectedIds([]);
      await refresh();
      setNotice(
        `已${decision === "approve" ? "批准" : "拒绝"} ${payload.proposals.length} 项提案`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "批量审核失败");
    } finally {
      setBusy(false);
    }
  }

  async function openMerge(conflict: ProposalConflict) {
    setBusy(true);
    setNotice("正在载入冲突双方的原子操作与证据…");
    try {
      const responses = await Promise.all(
        conflict.proposalIds.map((proposalId) =>
          adminFetch(
            `${apiUrl}/api/v1/proposals/${encodeURIComponent(proposalId)}`,
            { cache: "no-store" },
          ),
        ),
      );
      if (responses.some((response) => !response.ok)) {
        throw new Error("冲突来源提案载入失败");
      }
      const sources = (await Promise.all(
        responses.map(
          async (response) => (await response.json()) as GovernedProposal,
        ),
      )) as GovernedProposal[];
      if (sources.some((source) => source.proposal.status !== "human-review")) {
        throw new Error("只有均处于人工审核状态的冲突提案可以合并");
      }
      const defaultSource = conflict.proposalIds[0];
      if (!defaultSource) throw new Error("冲突缺少来源提案");
      setMergeSources(sources);
      setMergeSelections(
        Object.fromEntries(conflict.paths.map((path) => [path, defaultSource])),
      );
      setMergeConflict(conflict);
      setNotice("请选择每条冲突路径保留的候选值");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法创建合并方案");
    } finally {
      setBusy(false);
    }
  }

  async function createMerge() {
    if (!mergeConflict) return;
    const stamp = Date.now();
    const proposalId = `proposal-merge-${stamp}`;
    setBusy(true);
    setNotice("正在事务化创建合并提案并取代来源提案…");
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/proposals/conflicts/${encodeURIComponent(mergeConflict.id)}/merge`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            ...developmentHeaders(),
          },
          body: JSON.stringify({
            proposalId,
            agentRunId: `human-conflict-merge-${stamp}`,
            resolutions: mergeConflict.paths.map((path) => ({
              path,
              proposalId: mergeSelections[path],
            })),
            comment: mergeComment,
          }),
        },
      );
      const payload = (await response.json()) as {
        proposal?: GovernedProposal;
        supersededProposals?: GovernedProposal[];
        detail?: unknown;
      };
      if (!response.ok || !payload.proposal || !payload.supersededProposals) {
        throw new Error(detailText(payload.detail));
      }
      setMergeConflict(null);
      setMergeSources([]);
      setMergeSelections({});
      setSelectedIds([]);
      await refresh();
      setNotice(
        `已创建合并提案 ${payload.proposal.proposal.id} · ${payload.supersededProposals.length} 个来源已被取代`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "冲突合并失败");
    } finally {
      setBusy(false);
    }
  }

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + items.length, total);

  return (
    <main className="review-queue-page">
      <header className="review-queue-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>CONFLICT-AWARE REVIEW QUEUE</p>
          <h1>提案审核队列</h1>
          <span>大量 Agent 共享同一套路径冲突、证据与人工审批边界</span>
        </div>
        <div>
          <strong>{totalConflicts}</strong>
          <span>组活动路径冲突</span>
          <small>{notice}</small>
        </div>
      </header>

      <section className="review-queue-filters" aria-label="审核队列筛选">
        <form onSubmit={applySearch} role="search">
          <label>
            搜索提案、实体、Agent 或路径
            <input
              onChange={(event) => setQueryDraft(event.target.value)}
              placeholder="例如 entity-ginkgo 或 /description"
              value={queryDraft}
            />
          </label>
          <button className="secondary" type="submit">
            搜索
          </button>
        </form>
        <label>
          状态
          <select
            onChange={(event) => changeStatus(event.target.value)}
            value={statusFilter}
          >
            <option value="active">全部活动状态</option>
            {Object.entries(statusLabel).map(([value, label]) => (
              <option key={value} value={value}>
                {label}（{statusCounts[value] ?? 0}）
              </option>
            ))}
          </select>
        </label>
        <label className="review-conflicts-only">
          <input
            checked={conflictsOnly}
            onChange={(event) => {
              setConflictsOnly(event.target.checked);
              setOffset(0);
              setSelectedIds([]);
            }}
            type="checkbox"
          />
          仅显示冲突
        </label>
      </section>

      <section className="review-queue-toolbar">
        <label>
          操作身份
          <input
            disabled={!developmentAuth}
            onChange={(event) => setOperatorId(event.target.value)}
            value={operatorId}
          />
        </label>
        <span>已选择 {selectedIds.length} 项</span>
        <button
          className="secondary"
          disabled={busy || !canEvaluate}
          onClick={() => void bulkEvaluate()}
        >
          批量策略评估
        </button>
        <button
          className="secondary reject"
          disabled={busy || !canReview}
          onClick={() => void bulkReview("reject")}
        >
          批量拒绝
        </button>
        <button
          disabled={busy || !canReview}
          onClick={() => void bulkReview("approve")}
        >
          批量批准
        </button>
      </section>

      <div className="review-queue-layout">
        <section className="review-queue-list">
          <div className="review-queue-title">
            <h2>活动提案</h2>
            <span>
              {pageStart}–{pageEnd} / {total} 项
            </span>
          </div>
          {items.length === 0 && (
            <div className="ops-empty">没有符合当前筛选条件的提案。</div>
          )}
          {items.map((item) => {
            const proposal = item.governed.proposal;
            const proposalConflicts = conflictMap.get(proposal.id) ?? [];
            const selectable = ["proposed", "human-review"].includes(
              proposal.status,
            );
            return (
              <article
                data-conflict={item.conflictIds.length > 0}
                key={proposal.id}
              >
                <input
                  aria-label={`选择 ${proposal.id}`}
                  checked={selectedIds.includes(proposal.id)}
                  disabled={!selectable}
                  onChange={() => toggle(proposal.id)}
                  type="checkbox"
                />
                <div>
                  <a href={`/proposals/${encodeURIComponent(proposal.id)}`}>
                    {proposal.id}
                  </a>
                  <strong>
                    {proposal.entityId ?? "全局 Schema"} ·{" "}
                    {proposal.proposalType}
                  </strong>
                  <span>
                    {proposal.operations.length} 个操作 ·{" "}
                    {proposal.operations.reduce(
                      (totalCitations, operation) =>
                        totalCitations + operation.citationIds.length,
                      0,
                    )}{" "}
                    个引用 · 状态版本 v{item.governed.version}
                  </span>
                </div>
                <div className="review-queue-state">
                  <b data-status={proposal.status}>
                    {statusLabel[proposal.status] ?? proposal.status}
                  </b>
                  <small>{proposal.risk} 风险</small>
                </div>
                {proposalConflicts.length ? (
                  <aside>
                    <b>{proposalConflicts.length} 组冲突</b>
                    <span>
                      {proposalConflicts
                        .flatMap((conflict) => conflict.paths)
                        .slice(0, 3)
                        .join(" · ")}
                    </span>
                  </aside>
                ) : (
                  <aside data-clear="true">
                    <b>无活动冲突</b>
                    <span>
                      {proposal.status === "proposed"
                        ? "可进入批量策略评估"
                        : "可按政策继续审核"}
                    </span>
                  </aside>
                )}
              </article>
            );
          })}
          <nav className="review-pagination" aria-label="提案分页">
            <button
              className="secondary"
              disabled={busy || offset === 0}
              onClick={() => {
                setOffset(Math.max(0, offset - pageSize));
                setSelectedIds([]);
              }}
            >
              上一页
            </button>
            <span>
              第 {total === 0 ? 0 : Math.floor(offset / pageSize) + 1} 页
            </span>
            <button
              className="secondary"
              disabled={busy || offset + items.length >= total}
              onClick={() => {
                setOffset(offset + pageSize);
                setSelectedIds([]);
              }}
            >
              下一页
            </button>
          </nav>
        </section>

        <aside className="conflict-ledger">
          <p>CONFLICT LEDGER</p>
          <h2>本页冲突账本</h2>
          {mergeConflict && (
            <section className="merge-composer" aria-label="冲突合并方案">
              <header>
                <div>
                  <b>路径级合并方案</b>
                  <small>{mergeConflict.entityId ?? "全局 Schema"}</small>
                </div>
                <button
                  aria-label="关闭冲突合并方案"
                  className="secondary"
                  disabled={busy}
                  onClick={() => {
                    setMergeConflict(null);
                    setMergeSources([]);
                  }}
                >
                  ×
                </button>
              </header>
              {mergeConflict.paths.map((path) => (
                <fieldset key={path}>
                  <legend>{path}</legend>
                  {mergeSources.map((source) => {
                    const operations = source.proposal.operations.filter(
                      (operation) => pathsOverlap(operation.path, path),
                    );
                    return (
                      <label key={source.proposal.id}>
                        <input
                          checked={mergeSelections[path] === source.proposal.id}
                          name={`merge-${mergeConflict.id}-${path}`}
                          onChange={() =>
                            setMergeSelections((current) => ({
                              ...current,
                              [path]: source.proposal.id,
                            }))
                          }
                          type="radio"
                        />
                        <span>
                          <b>{source.proposal.id}</b>
                          <code>
                            {operations
                              .map((operation) => compactValue(operation.after))
                              .join("\n")}
                          </code>
                          <small>
                            {operations.reduce(
                              (count, operation) =>
                                count + operation.citationIds.length,
                              0,
                            )}{" "}
                            个引用
                          </small>
                        </span>
                      </label>
                    );
                  })}
                </fieldset>
              ))}
              <label>
                合并说明
                <textarea
                  onChange={(event) => setMergeComment(event.target.value)}
                  rows={3}
                  value={mergeComment}
                />
              </label>
              <button
                disabled={
                  busy ||
                  !mergeComment.trim() ||
                  mergeConflict.paths.some((path) => !mergeSelections[path])
                }
                onClick={() => void createMerge()}
              >
                创建受治理合并提案
              </button>
              <small>
                原提案将标记为 superseded；合并结果仍需策略评估与人工批准。
              </small>
            </section>
          )}
          {conflicts.length ? (
            conflicts.map((conflict) => (
              <article key={conflict.id}>
                <b>{conflict.entityId ?? "全局 Schema"}</b>
                <code>{conflict.paths.join(" · ")}</code>
                <span>{conflict.proposalIds.join(" ↔ ")}</span>
                <small>可选择一个候选，也可逐路径合并双方有效变更。</small>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => void openMerge(conflict)}
                >
                  逐路径合并
                </button>
              </article>
            ))
          ) : (
            <div className="ops-empty">当前页没有活动路径冲突。</div>
          )}
        </aside>
      </div>
    </main>
  );
}
