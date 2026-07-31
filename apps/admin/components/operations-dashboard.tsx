"use client";

import { useCallback, useEffect, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";
import { AuthSessionControl } from "./auth-session-control";

interface AgentItem {
  id: string;
  name: string;
  role: string;
  modelPolicy: string;
  version: string;
}

interface ProposalItem {
  id: string;
  entityId?: string;
  entityName: string;
  proposalType: string;
  risk: string;
  status: string;
  operationCount: number;
  conflictCount: number;
  impact: Record<string, unknown>;
}

interface ScheduleItem {
  id: string;
  graphId: string;
  graphVersion: string;
  triggerType: string;
  status: string;
  requestedBy: string;
  createdAt: string;
}

interface ReleaseManifest {
  id: string;
  proposalIds: string[];
  dataVersion: string;
  schemaVersions: string[];
  previousReleaseId: string;
  trigger: "manual" | "automatic";
  initiatedBy?: string;
  status: string;
  verification: {
    status: "pending" | "running" | "passed" | "failed" | "superseded";
    automaticRollback: boolean;
  };
}

interface OperationsSummary {
  generatedAt: string;
  dataVersion: string;
  agentDefinitionCount: number;
  activeScheduleCount: number;
  proposalCount: number;
  reviewQueueCount: number;
  highRiskReviewCount: number;
  proposalConflictCount: number;
  evidenceCoveragePercent: number;
  sourceCount: number;
  activeSourceCount: number;
  pendingAcquisitionCount: number;
  pendingOutboxCount: number;
  agents: AgentItem[];
  proposals: ProposalItem[];
  schedules: ScheduleItem[];
  latestRelease?: ReleaseManifest;
}

const apiUrl = "/api/backend";

const statusNames: Record<string, string> = {
  proposed: "待策略评估",
  "policy-approved": "策略通过",
  "human-review": "等待审核",
  accepted: "已接受",
  released: "已发布",
  rejected: "已拒绝",
  superseded: "已被合并取代",
  "policy-blocked": "策略阻断",
  queued: "已排队",
  dispatched: "已分发",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  canceled: "已取消",
  staged: "已暂存",
  publishing: "发布中",
  published: "已发布",
  "rolling-back": "回滚中",
  "rolled-back": "已回滚",
};

const verificationStatusNames: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  passed: "已通过",
  failed: "失败",
  superseded: "已由新版本替代",
};

function impactText(impact: Record<string, unknown>) {
  const entries = Object.entries(impact);
  if (!entries.length) return "影响范围待计算";
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key} ${String(value)}`)
    .join(" · ");
}

export function OperationsDashboard() {
  const [summary, setSummary] = useState<OperationsSummary>();
  const [notice, setNotice] = useState("正在读取持久化运营状态…");

  const refresh = useCallback(async () => {
    const response = await adminFetch(`${apiUrl}/api/v1/operations/summary`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error("运营摘要 API 返回异常");
    const next = (await response.json()) as OperationsSummary;
    setSummary(next);
    setNotice(
      `已同步 ${new Date(next.generatedAt).toLocaleTimeString("zh-CN")}`,
    );
  }, []);

  useEffect(() => {
    void refresh().catch((error: unknown) =>
      setNotice(error instanceof Error ? error.message : "运营摘要不可用"),
    );
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const reviewBlocked = (summary?.highRiskReviewCount ?? 0) > 0;
  const release = summary?.latestRelease;

  return (
    <main className="admin-shell">
      <aside className="ops-sidebar">
        <div className="brand">
          <span>A</span> ATLAS OPS
        </div>
        <p className="workspace-label">知识生产系统</p>
        <nav>
          <a className="active">运行概览</a>
          <a href="/graphs/knowledge-maintenance">Agent 编排</a>
          <a href="/reviews">
            变更提案
            {(summary?.reviewQueueCount ?? 0) > 0 && (
              <b>{summary?.reviewQueueCount}</b>
            )}
          </a>
          <a href="/entries">条目工作室</a>
          <a href="/quality">质量维护</a>
          <a href="/schema">Schema 中心</a>
          <a href="/sources">来源与许可</a>
          <a href="/releases">发布与回滚</a>
          <a href="/audit">审计日志</a>
        </nav>
        <div className="environment">
          <i /> 持久化状态 · 写入受控
        </div>
      </aside>

      <section className="ops-content">
        <header>
          <div>
            <p className="eyebrow">AGENT MAINTENANCE CONTROL</p>
            <h1>知识维护运行中心</h1>
            <span>{notice}</span>
          </div>
          <div className="header-actions">
            <AuthSessionControl />
            <button className="secondary">
              数据版本 {summary?.dataVersion ?? "同步中"}
            </button>
            <a className="auth-login" href="/graphs/knowledge-maintenance">
              创建维护任务
            </a>
          </div>
        </header>

        <div className="metrics">
          <article>
            <span>活跃 Agent 调度</span>
            <strong>{summary?.activeScheduleCount ?? "—"}</strong>
            <small>{summary?.agentDefinitionCount ?? 0} 个版本化 Agent</small>
          </article>
          <article>
            <span>待处理提案</span>
            <strong>{summary?.reviewQueueCount ?? "—"}</strong>
            <small className={reviewBlocked ? "warning" : ""}>
              {summary?.highRiskReviewCount ?? 0} 项高风险 ·{" "}
              {summary?.proposalConflictCount ?? 0} 组冲突
            </small>
          </article>
          <article>
            <span>证据覆盖率</span>
            <strong>
              {summary ? `${summary.evidenceCoveragePercent}%` : "—"}
            </strong>
            <small>{summary?.proposalCount ?? 0} 个持久化提案</small>
          </article>
          <article>
            <span>待分发事件</span>
            <strong>{summary?.pendingOutboxCount ?? "—"}</strong>
            <small>
              {summary?.pendingAcquisitionCount ?? 0} 个采集任务 ·{" "}
              {summary?.activeSourceCount ?? 0}/{summary?.sourceCount ?? 0}{" "}
              来源可用
            </small>
          </article>
        </div>

        <div className="pipeline">
          <div className="panel-title">
            <div>
              <p>VERSIONED AGENT REGISTRY</p>
              <h2>Agent 维护流水线</h2>
            </div>
            <a href="/graphs/knowledge-maintenance">打开图控制台 →</a>
          </div>
          <div className="pipeline-grid">
            {summary?.agents.map((agent, index) => (
              <article key={agent.id}>
                <div className={`agent-icon agent-${(index % 4) + 1}`}>
                  A{index + 1}
                </div>
                <div>
                  <strong>{agent.name}</strong>
                  <span>{agent.role}</span>
                </div>
                <b data-status="已完成">已注册</b>
                <small>
                  {agent.modelPolicy} · v{agent.version}
                </small>
              </article>
            ))}
          </div>
        </div>

        <div className="review-grid">
          <section className="proposal-panel">
            <div className="panel-title">
              <div>
                <p>LIVE REVIEW QUEUE</p>
                <h2>最近治理提案</h2>
              </div>
              <a href="/reviews">
                {summary?.proposals.length ?? 0} 项 · 打开审核队列 →
              </a>
            </div>
            <div className="proposal-table">
              {summary?.proposals.length === 0 && (
                <div className="ops-empty">当前没有治理提案。</div>
              )}
              {summary?.proposals.map((proposal) => (
                <article key={proposal.id}>
                  <code>{proposal.id}</code>
                  <div>
                    <strong>
                      {proposal.entityName} · {proposal.proposalType}
                    </strong>
                    <span>
                      {statusNames[proposal.status] ?? proposal.status} ·{" "}
                      {impactText(proposal.impact)}
                      {proposal.conflictCount
                        ? ` · ${proposal.conflictCount} 组路径冲突`
                        : ""}
                    </span>
                  </div>
                  <b>{proposal.risk}</b>
                  <a
                    aria-label={`查看 ${proposal.id}`}
                    href={`/proposals/${encodeURIComponent(proposal.id)}`}
                  >
                    →
                  </a>
                </article>
              ))}
            </div>
          </section>

          <aside className="release-panel">
            <p>LATEST RELEASE</p>
            <h2>{release?.id ?? "尚无发布批次"}</h2>
            <span>
              {release
                ? `${release.proposalIds.length} 项提案 · ${release.schemaVersions.length} 个 Schema 版本 · ${
                    release.trigger === "automatic"
                      ? "Agent 自动发布"
                      : "人工批次"
                  }`
                : "在发布工作台创建首个受治理批次"}
            </span>
            <ul>
              <li className="pass">
                ✓ 证据覆盖率 {summary?.evidenceCoveragePercent ?? 0}%
              </li>
              <li className="pass">
                ✓ {summary?.activeSourceCount ?? 0} 个来源通过采集政策
              </li>
              <li className={reviewBlocked ? "blocked" : "pass"}>
                {reviewBlocked ? "!" : "✓"} {summary?.highRiskReviewCount ?? 0}{" "}
                项高风险审核未完成
              </li>
              <li
                className={
                  (summary?.pendingOutboxCount ?? 0) > 0 ? "blocked" : "pass"
                }
              >
                {(summary?.pendingOutboxCount ?? 0) > 0 ? "!" : "✓"}{" "}
                {summary?.pendingOutboxCount ?? 0} 个 Outbox 事件待分发
              </li>
              <li
                className={
                  release?.verification.status === "failed" ? "blocked" : "pass"
                }
              >
                {release?.verification.status === "failed" ? "!" : "✓"}{" "}
                发布后验证{" "}
                {release?.verification.status
                  ? (verificationStatusNames[release.verification.status] ??
                    release.verification.status)
                  : "尚未开始"}
              </li>
            </ul>
            <button disabled>
              {release
                ? (statusNames[release.status] ?? release.status)
                : "等待创建发布"}
            </button>
            <small>回滚目标：{release?.previousReleaseId ?? "尚未建立"}</small>
            <a className="release-workbench-link" href="/releases">
              打开发布工作台 →
            </a>
          </aside>
        </div>
      </section>
    </main>
  );
}
