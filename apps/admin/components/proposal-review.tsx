"use client";

import { useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

type ProposalStatus =
  | "proposed"
  | "policy-approved"
  | "policy-blocked"
  | "human-review"
  | "accepted"
  | "rejected"
  | "superseded"
  | "released";

interface ChangeOperation {
  operation: string;
  path: string;
  before: unknown;
  after: unknown;
  confidence: number;
  citationIds: string[];
  machineGenerated?: boolean;
}

interface GovernedProposal {
  version: number;
  proposal: {
    id: string;
    entityId?: string;
    proposalType: string;
    risk: string;
    status: ProposalStatus;
    agentRunId: string;
    operations: ChangeOperation[];
    impact: Record<string, number>;
  };
  policyEvaluation: null | {
    outcome: string;
    policyVersion: string;
    requiredApprovals: number;
    blockers: string[];
    reasons: string[];
  };
  reviews: Array<{
    id: string;
    reviewerId: string;
    decision: string;
    comment: string;
    createdAt?: string;
  }>;
  sourceProposalIds: string[];
  supersededByProposalId?: string;
  supersededAt?: string;
}

const apiUrl = "/apps/hardatlas-admin/api/backend";
const developmentAuth =
  (process.env.NEXT_PUBLIC_HARDATLAS_AUTH_MODE ?? "development") ===
  "development";

function detailText(detail: unknown) {
  if (typeof detail === "string") return detail;
  if (
    detail &&
    typeof detail === "object" &&
    "code" in detail &&
    detail.code === "proposal-version-conflict"
  ) {
    const conflict = detail as {
      expectedVersion?: number;
      actualVersion?: number;
    };
    return `提案已被其他 Agent 或审核者更新（本地 v${conflict.expectedVersion ?? "?"}，当前 v${conflict.actualVersion ?? "?"}），请基于最新状态重新确认。`;
  }
  return detail ? JSON.stringify(detail) : "治理请求失败";
}

function formatValue(value: unknown) {
  if (value === undefined || value === null) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function impactLabel(key: string) {
  const labels: Record<string, string> = {
    entityCount: "实体",
    relationCount: "关系",
    localeCount: "语言",
    schemaCount: "Schema",
    taxonomyNodeCount: "分类节点",
  };
  return labels[key] ?? key;
}

function proposalTitle(proposal: GovernedProposal["proposal"]) {
  const labels: Record<string, string> = {
    content: "内容变更提案",
    relation: "关系变更提案",
    schema: "Schema / Domain Pack 变更",
    translation: "多语言变更提案",
    merge: "冲突合并提案",
  };
  return proposal.entityId
    ? `${labels[proposal.proposalType] ?? "治理提案"} · ${proposal.entityId}`
    : (labels[proposal.proposalType] ?? "全局治理提案");
}

export function ProposalReview({ proposalId }: { proposalId: string }) {
  const [state, setState] = useState<GovernedProposal | null>(null);
  const [reviewerId, setReviewerId] = useState("reviewer-local");
  const [notice, setNotice] = useState("正在连接治理 API…");
  const [busy, setBusy] = useState(false);

  async function request(path: string, init?: RequestInit) {
    setBusy(true);
    try {
      const response = await adminFetch(`${apiUrl}${path}`, {
        ...init,
        headers: { "content-type": "application/json", ...init?.headers },
      });
      const payload = (await response.json()) as
        GovernedProposal | { detail?: unknown };
      if (!response.ok) {
        throw new Error(
          "detail" in payload && payload.detail
            ? detailText(payload.detail)
            : `请求失败 ${response.status}`,
        );
      }
      setState(payload as GovernedProposal);
      setNotice("治理状态已同步");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "治理 API 不可用");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void request(`/api/v1/proposals/${proposalId}`);
  }, [proposalId]);

  async function evaluate() {
    await request(`/api/v1/proposals/${proposalId}/evaluate`, {
      method: "POST",
    });
  }

  async function review(decision: "approve" | "reject") {
    const developmentHeaders: HeadersInit = developmentAuth
      ? {
          "x-hardatlas-dev-principal": reviewerId,
          "x-hardatlas-dev-display-name": reviewerId,
          "x-hardatlas-dev-roles": "reviewer",
        }
      : {};
    await request(`/api/v1/proposals/${proposalId}/reviews`, {
      method: "POST",
      headers: developmentHeaders,
      body: JSON.stringify({
        decision,
        comment:
          decision === "approve"
            ? "证据、Schema 影响和变更范围已核验"
            : "需要补充证据或修正影响分析",
      }),
    });
  }

  const citationIds = useMemo(
    () =>
      Array.from(
        new Set(
          state?.proposal.operations.flatMap(
            (operation) => operation.citationIds,
          ) ?? [],
        ),
      ),
    [state],
  );

  if (!state) {
    return (
      <main className="review-page">
        <header className="review-header">
          <div>
            <a href="/">← 返回运行中心</a>
            <p>GOVERNED PROPOSAL</p>
            <h1>正在载入提案</h1>
            <span>{proposalId}</span>
          </div>
          <div className="review-status">
            <b data-state="proposed">同步中</b>
            <small>{notice}</small>
          </div>
        </header>
      </main>
    );
  }

  const required = state.policyEvaluation?.requiredApprovals ?? 0;
  const approvals = state.reviews.filter(
    (review) => review.decision === "approve",
  ).length;
  const canEvaluate = ![
    "accepted",
    "rejected",
    "superseded",
    "released",
  ].includes(state.proposal.status);
  const canReview = state.proposal.status === "human-review";

  return (
    <main className="review-page">
      <header className="review-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>GOVERNED PROPOSAL</p>
          <h1>{proposalTitle(state.proposal)}</h1>
          <span>
            {state.proposal.id} · {state.proposal.proposalType} ·{" "}
            {state.proposal.risk} 风险 · 状态版本 v{state.version}
          </span>
        </div>
        <div className="review-status">
          <b data-state={state.proposal.status}>{state.proposal.status}</b>
          <small>{notice}</small>
        </div>
      </header>

      <div className="review-columns">
        <aside className="provenance-panel">
          <p>PROVENANCE</p>
          <h2>可追溯上下文</h2>
          {[
            ["执行来源", state.proposal.agentRunId, "不可由审核表单改写"],
            [
              "作用对象",
              state.proposal.entityId ?? "全局 Schema / Domain Pack",
              state.proposal.proposalType,
            ],
            [
              "变更规模",
              `${state.proposal.operations.length} 个原子操作`,
              `${citationIds.length} 个引用`,
            ],
            [
              "并发基线",
              `状态版本 v${state.version}`,
              "数据库权威版本；并发写入冲突时拒绝旧状态",
            ],
            ...(state.sourceProposalIds.length
              ? [
                  [
                    "合并来源",
                    state.sourceProposalIds.join(" + "),
                    "原提案保持不可变并标记为 superseded",
                  ],
                ]
              : []),
            ...(state.supersededByProposalId
              ? [
                  [
                    "取代提案",
                    state.supersededByProposalId,
                    state.supersededAt ?? "已记录取代时间",
                  ],
                ]
              : []),
          ].map(([name, detail, annotation], index) => (
            <article key={name}>
              <i>{index + 1}</i>
              <div>
                <strong>{name}</strong>
                <span>{detail}</span>
                <code>{annotation}</code>
              </div>
            </article>
          ))}
          <section className="citation-list">
            <strong>引用标识</strong>
            {citationIds.length ? (
              citationIds.map((citationId) => (
                <code key={citationId}>{citationId}</code>
              ))
            ) : (
              <span>没有引用；策略检查应决定是否阻断。</span>
            )}
          </section>
        </aside>

        <section className="diff-panel">
          <p>OPERATION-LEVEL DIFF</p>
          <h2>原子变更</h2>
          <div className="operation-list">
            {state.proposal.operations.map((operation, index) => (
              <article
                className="operation-card"
                key={`${operation.path}-${index}`}
              >
                <header>
                  <code>{operation.path}</code>
                  <span>{operation.operation}</span>
                </header>
                <div className="value-diff">
                  <article>
                    <span>当前值</span>
                    <pre>{formatValue(operation.before)}</pre>
                  </article>
                  <b>→</b>
                  <article className="suggested">
                    <span>建议值</span>
                    <pre>{formatValue(operation.after)}</pre>
                  </article>
                </div>
                <footer>
                  <b>可信度 {Math.round(operation.confidence * 100)}%</b>
                  <span>{operation.citationIds.length} 个引用</span>
                  {operation.machineGenerated && <span>机器生成候选</span>}
                </footer>
              </article>
            ))}
          </div>
          <section className="impact-grid">
            {Object.entries(state.proposal.impact).map(([key, value]) => (
              <article key={key}>
                <strong>{value}</strong>
                <span>{impactLabel(key)}</span>
              </article>
            ))}
          </section>
        </section>

        <aside className="gate-panel">
          <p>POLICY GATE</p>
          <h2>策略与审核</h2>
          <dl>
            <div>
              <dt>策略版本</dt>
              <dd>{state.policyEvaluation?.policyVersion ?? "未评估"}</dd>
            </div>
            <div>
              <dt>策略结论</dt>
              <dd>{state.policyEvaluation?.outcome ?? "待运行"}</dd>
            </div>
            <div>
              <dt>风险等级</dt>
              <dd>{state.proposal.risk}</dd>
            </div>
            <div>
              <dt>审核进度</dt>
              <dd>
                {approvals} / {required || "—"}
              </dd>
            </div>
          </dl>
          {state.policyEvaluation?.blockers.map((blocker) => (
            <p className="policy-message blocker" key={blocker}>
              {blocker}
            </p>
          ))}
          {state.policyEvaluation?.reasons.map((reason) => (
            <p className="policy-message" key={reason}>
              {reason}
            </p>
          ))}
          <button disabled={busy || !canEvaluate} onClick={evaluate}>
            运行策略检查
          </button>
          {developmentAuth ? (
            <label>
              本地开发审核身份
              <input
                value={reviewerId}
                onChange={(event) => setReviewerId(event.target.value)}
              />
            </label>
          ) : null}
          <div className="review-actions">
            <button
              className="reject"
              disabled={busy || !canReview}
              onClick={() => review("reject")}
            >
              退回补证
            </button>
            <button
              disabled={busy || (developmentAuth && !reviewerId) || !canReview}
              onClick={() => review("approve")}
            >
              批准提案
            </button>
          </div>
          <div className="review-history">
            {state.reviews.length ? (
              state.reviews.map((review) => (
                <article key={review.id}>
                  <b>{review.reviewerId}</b>
                  <span>
                    {review.decision} · {review.comment}
                  </span>
                </article>
              ))
            ) : (
              <span>尚无人工审核记录</span>
            )}
          </div>
        </aside>
      </div>
    </main>
  );
}
