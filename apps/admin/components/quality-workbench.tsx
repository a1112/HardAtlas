"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";
import { AuthSessionControl } from "./auth-session-control";

type AssessmentStatus = "healthy" | "attention" | "critical";
type TaskStatus = "open" | "scheduled" | "resolved" | "superseded";

interface QualityIssue {
  id: string;
  code: string;
  severity: "warning" | "error";
  path: string;
  message: string;
  action: string;
  penalty: number;
}

interface EntityRef {
  id: string;
  slug: string;
  typeId: string;
  canonicalName: string;
}

interface QualityAssessment {
  id: string;
  entity: EntityRef;
  revisionId: string;
  dataVersion: string;
  profileId: string;
  profileVersion: string;
  status: AssessmentStatus;
  score: number;
  issues: QualityIssue[];
  assessedAt: string;
}

interface MaintenanceTask {
  id: string;
  assessmentId: string;
  entity: EntityRef;
  revisionId: string;
  profileId: string;
  profileVersion: string;
  issue: QualityIssue;
  action: string;
  status: TaskStatus;
  priority: "low" | "medium" | "high" | "critical";
  agentGraphScheduleId?: string;
  createdAt: string;
  updatedAt: string;
}

type WorkRoute =
  "source-acquisition" | "translation-evidence" | "taxonomy-review";

interface MaintenanceWorkItem {
  id: string;
  maintenanceTaskId: string;
  assessmentId: string;
  entity: EntityRef;
  revisionId: string;
  action: string;
  issue: QualityIssue;
  route: WorkRoute;
  requiredCapabilities: string[];
  requiresEvidence: boolean;
  proposalEligible: boolean;
  status:
    "queued" | "ready" | "claimed" | "blocked" | "completed" | "superseded";
  priority: MaintenanceTask["priority"];
  triageScheduleId: string;
  triageRunId: string;
  assigneeId?: string;
  leaseExpiresAt?: string;
  attempt: number;
  blockedReason?: string;
  evidenceRefs: Array<{
    kind: "citation" | "source-snapshot";
    id: string;
  }>;
  outputRefs: Array<{
    kind: "governed-proposal" | "source-acquisition-job";
    id: string;
  }>;
  updatedAt: string;
}

interface QualitySummary {
  generatedAt: string;
  assessedEntityCount: number;
  healthyCount: number;
  attentionCount: number;
  criticalCount: number;
  averageScore: number;
  openTaskCount: number;
  scheduledTaskCount: number;
  tasksByAction: Record<string, number>;
}

interface AgentRuntimeState {
  runtime: {
    id: string;
    definitionId: string;
    definitionVersion: string;
    capabilities: string[];
    supportedRoutes: WorkRoute[];
    status: "online" | "draining";
    maxConcurrency: number;
    heartbeatTtlSeconds: number;
    labels: Record<string, string>;
    lastHeartbeatAt: string;
  };
  effectiveStatus: "online" | "draining" | "offline";
  activeLeaseCount: number;
  availableCapacity: number;
  observedAt: string;
}

interface ScanResult {
  assessed: QualityAssessment[];
  skippedEntityIds: string[];
  failures: Array<{ entityId: string; code: string; message: string }>;
  openTaskCount: number;
  startedAt: string;
  completedAt: string;
}

const apiUrl = "/apps/hardatlas-admin/api/backend";
const assessmentPageSize = 20;
const taskPageSize = 18;

const actionNames: Record<string, string> = {
  "enrich-attribute": "补全属性",
  translate: "补充翻译",
  "add-citation": "补充证据",
  "add-section": "扩充章节",
  "refresh-evidence": "刷新证据",
  classify: "完成分类",
};

const statusNames: Record<AssessmentStatus, string> = {
  healthy: "健康",
  attention: "需关注",
  critical: "关键缺口",
};

const priorityNames: Record<MaintenanceTask["priority"], string> = {
  low: "低",
  medium: "中",
  high: "高",
  critical: "紧急",
};

const taskStatusNames: Record<TaskStatus, string> = {
  open: "等待安全分诊",
  scheduled: "已进入 Agent 图",
  resolved: "已解决",
  superseded: "已被新修订替代",
};

const routeNames: Record<WorkRoute, string> = {
  "source-acquisition": "来源采集",
  "translation-evidence": "翻译与证据",
  "taxonomy-review": "分类审核",
};

const workStatusNames: Record<MaintenanceWorkItem["status"], string> = {
  queued: "等待投递",
  ready: "可领取",
  claimed: "执行中",
  blocked: "已阻塞",
  completed: "已完成",
  superseded: "已过期",
};

async function readJson<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => undefined)) as
      { detail?: string } | undefined;
    throw new Error(body?.detail ?? message);
  }
  return (await response.json()) as T;
}

export function QualityWorkbench() {
  const [summary, setSummary] = useState<QualitySummary>();
  const [assessments, setAssessments] = useState<QualityAssessment[]>([]);
  const [tasks, setTasks] = useState<MaintenanceTask[]>([]);
  const [workItems, setWorkItems] = useState<MaintenanceWorkItem[]>([]);
  const [agentRuntimes, setAgentRuntimes] = useState<AgentRuntimeState[]>([]);
  const [statusFilter, setStatusFilter] = useState<"all" | AssessmentStatus>(
    "all",
  );
  const [actionFilter, setActionFilter] = useState("all");
  const [assessmentPage, setAssessmentPage] = useState(0);
  const [taskPage, setTaskPage] = useState(0);
  const [notice, setNotice] = useState("正在读取质量快照…");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const [
      summaryResponse,
      assessmentResponse,
      taskResponse,
      workResponse,
      runtimeResponse,
    ] = await Promise.all([
      adminFetch(`${apiUrl}/api/v1/quality/summary`, {
        cache: "no-store",
      }),
      adminFetch(`${apiUrl}/api/v1/quality/assessments?limit=500`, {
        cache: "no-store",
      }),
      adminFetch(`${apiUrl}/api/v1/quality/tasks?limit=500`, {
        cache: "no-store",
      }),
      adminFetch(`${apiUrl}/api/v1/maintenance/work-items?limit=500`, {
        cache: "no-store",
      }),
      adminFetch(`${apiUrl}/api/v1/agent-runtimes`, {
        cache: "no-store",
      }),
    ]);
    const [
      nextSummary,
      nextAssessments,
      nextTasks,
      nextWorkItems,
      nextRuntimes,
    ] = await Promise.all([
      readJson<QualitySummary>(summaryResponse, "质量摘要 API 返回异常"),
      readJson<QualityAssessment[]>(
        assessmentResponse,
        "质量评估 API 返回异常",
      ),
      readJson<MaintenanceTask[]>(taskResponse, "维护任务 API 返回异常"),
      readJson<MaintenanceWorkItem[]>(
        workResponse,
        "Agent 工作队列 API 返回异常",
      ),
      readJson<AgentRuntimeState[]>(
        runtimeResponse,
        "Agent Runtime API 返回异常",
      ),
    ]);
    setSummary(nextSummary);
    setAssessments(nextAssessments);
    setTasks(
      nextTasks.filter(
        (task) => task.status === "open" || task.status === "scheduled",
      ),
    );
    setWorkItems(
      nextWorkItems.filter(
        (item) => item.status !== "completed" && item.status !== "superseded",
      ),
    );
    setAgentRuntimes(nextRuntimes);
    setNotice(
      `质量快照已同步 · ${new Date(nextSummary.generatedAt).toLocaleTimeString(
        "zh-CN",
      )}`,
    );
  }, []);

  useEffect(() => {
    void refresh().catch((error: unknown) =>
      setNotice(error instanceof Error ? error.message : "质量服务不可用"),
    );
  }, [refresh]);

  async function scan() {
    setBusy(true);
    setNotice("正在按活动 Domain Pack 评估全部已发布条目…");
    try {
      const response = await adminFetch(`${apiUrl}/api/v1/quality/scans`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "idempotency-key": `quality-scan-${new Date()
            .toISOString()
            .slice(0, 16)}`,
        },
        body: JSON.stringify({ entityIds: [] }),
      });
      const result = await readJson<ScanResult>(response, "质量扫描执行失败");
      await refresh();
      setNotice(
        result.failures.length
          ? `完成 ${result.assessed.length} 项评估，${result.failures.length} 项隔离失败`
          : `完成 ${result.assessed.length} 项评估，发现 ${result.openTaskCount} 个开放任务`,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "质量扫描执行失败");
    } finally {
      setBusy(false);
    }
  }

  async function requeueWorkItem(workItemId: string) {
    setBusy(true);
    setNotice(`正在重新投递 ${workItemId}…`);
    try {
      const response = await adminFetch(
        `${apiUrl}/api/v1/maintenance/work-items/${workItemId}/requeue`,
        { method: "POST" },
      );
      await readJson<MaintenanceWorkItem>(response, "重新投递 Work Item 失败");
      await refresh();
      setNotice(`${workItemId} 已重新写入可靠队列`);
    } catch (error) {
      setNotice(
        error instanceof Error ? error.message : "重新投递 Work Item 失败",
      );
    } finally {
      setBusy(false);
    }
  }

  const visibleAssessments = useMemo(
    () =>
      assessments.filter(
        (assessment) =>
          statusFilter === "all" || assessment.status === statusFilter,
      ),
    [assessments, statusFilter],
  );
  const filteredTasks = useMemo(
    () =>
      tasks.filter(
        (task) => actionFilter === "all" || task.action === actionFilter,
      ),
    [actionFilter, tasks],
  );
  const assessmentPageCount = Math.max(
    Math.ceil(visibleAssessments.length / assessmentPageSize),
    1,
  );
  const taskPageCount = Math.max(
    Math.ceil(filteredTasks.length / taskPageSize),
    1,
  );
  const currentAssessmentPage = Math.min(
    assessmentPage,
    assessmentPageCount - 1,
  );
  const currentTaskPage = Math.min(taskPage, taskPageCount - 1);
  const pagedAssessments = visibleAssessments.slice(
    currentAssessmentPage * assessmentPageSize,
    (currentAssessmentPage + 1) * assessmentPageSize,
  );
  const pagedTasks = filteredTasks.slice(
    currentTaskPage * taskPageSize,
    (currentTaskPage + 1) * taskPageSize,
  );
  const actions = useMemo(
    () => Array.from(new Set(tasks.map((task) => task.action))).sort(),
    [tasks],
  );

  return (
    <main className="admin-shell quality-shell">
      <aside className="ops-sidebar">
        <div className="brand">
          <span>A</span> ATLAS OPS
        </div>
        <p className="workspace-label">知识生产系统</p>
        <nav>
          <a href="/">运行概览</a>
          <a href="/graphs/knowledge-maintenance">Agent 编排</a>
          <a href="/reviews">变更提案</a>
          <a href="/entries">条目工作室</a>
          <a className="active" href="/quality">
            质量维护
            {(summary?.openTaskCount ?? 0) +
              (summary?.scheduledTaskCount ?? 0) >
              0 && (
              <b>
                {(summary?.openTaskCount ?? 0) +
                  (summary?.scheduledTaskCount ?? 0)}
              </b>
            )}
          </a>
          <a href="/schema">Schema 中心</a>
          <a href="/sources">来源与许可</a>
          <a href="/releases">发布与回滚</a>
          <a href="/audit">审计日志</a>
        </nav>
        <div className="environment">
          <i /> 声明式规则 · 任务持久化
        </div>
      </aside>

      <section className="ops-content quality-content">
        <header>
          <div>
            <p className="eyebrow">KNOWLEDGE QUALITY CONTROL</p>
            <h1>知识质量维护中心</h1>
            <span>{notice}</span>
          </div>
          <div className="header-actions">
            <AuthSessionControl />
            <a className="secondary-link" href="/schema/domain-packs">
              管理质量档案
            </a>
            <button disabled={busy} onClick={() => void scan()} type="button">
              {busy ? "扫描中…" : "运行全库质量扫描"}
            </button>
          </div>
        </header>

        <div className="quality-metrics">
          <article data-tone="score">
            <span>平均质量分</span>
            <strong>{summary?.averageScore ?? "—"}</strong>
            <small>{summary?.assessedEntityCount ?? 0} 个当前修订</small>
          </article>
          <article data-tone="healthy">
            <span>健康条目</span>
            <strong>{summary?.healthyCount ?? "—"}</strong>
            <small>满足领域质量档案</small>
          </article>
          <article data-tone="attention">
            <span>需关注</span>
            <strong>{summary?.attentionCount ?? "—"}</strong>
            <small>可排入低风险维护</small>
          </article>
          <article data-tone="critical">
            <span>关键缺口</span>
            <strong>{summary?.criticalCount ?? "—"}</strong>
            <small>
              {summary?.openTaskCount ?? 0} 待派发 ·{" "}
              {summary?.scheduledTaskCount ?? 0} 已进入 Agent 图
            </small>
          </article>
        </div>

        <section className="quality-assessments">
          <div className="quality-panel-header">
            <div>
              <p>ENTITY ASSESSMENTS</p>
              <h2>当前修订质量快照</h2>
            </div>
            <label>
              状态
              <select
                aria-label="评估状态"
                onChange={(event) => {
                  setStatusFilter(
                    event.target.value as "all" | AssessmentStatus,
                  );
                  setAssessmentPage(0);
                }}
                value={statusFilter}
              >
                <option value="all">全部</option>
                <option value="healthy">健康</option>
                <option value="attention">需关注</option>
                <option value="critical">关键缺口</option>
              </select>
            </label>
          </div>
          <div className="assessment-table">
            <div className="assessment-row assessment-heading">
              <span>条目与当前修订</span>
              <span>质量档案</span>
              <span>得分</span>
              <span>缺口</span>
              <span>状态</span>
            </div>
            {visibleAssessments.length === 0 && (
              <div className="quality-empty">
                尚无质量快照。运行扫描后，系统会按实体类型加载活动质量档案。
              </div>
            )}
            {pagedAssessments.map((assessment) => (
              <article
                className="assessment-row"
                data-status={assessment.status}
                key={assessment.id}
              >
                <div>
                  <strong>{assessment.entity.canonicalName}</strong>
                  <code>{assessment.revisionId}</code>
                </div>
                <div>
                  <strong>{assessment.profileId}</strong>
                  <small>v{assessment.profileVersion}</small>
                </div>
                <b>{assessment.score}</b>
                <span>{assessment.issues.length}</span>
                <em>{statusNames[assessment.status]}</em>
              </article>
            ))}
          </div>
          <div className="quality-pagination">
            <span>{visibleAssessments.length} 项评估</span>
            <div>
              <button
                aria-label="上一页评估"
                disabled={currentAssessmentPage === 0}
                onClick={() =>
                  setAssessmentPage((page) => Math.max(page - 1, 0))
                }
                type="button"
              >
                上一页
              </button>
              <strong>
                {currentAssessmentPage + 1} / {assessmentPageCount}
              </strong>
              <button
                aria-label="下一页评估"
                disabled={currentAssessmentPage + 1 >= assessmentPageCount}
                onClick={() =>
                  setAssessmentPage((page) =>
                    Math.min(page + 1, assessmentPageCount - 1),
                  )
                }
                type="button"
              >
                下一页
              </button>
            </div>
          </div>
        </section>

        <section className="quality-tasks">
          <div className="quality-panel-header">
            <div>
              <p>AGENT-READY MAINTENANCE QUEUE</p>
              <h2>可解释维护任务</h2>
            </div>
            <label>
              修复动作
              <select
                aria-label="修复动作"
                onChange={(event) => {
                  setActionFilter(event.target.value);
                  setTaskPage(0);
                }}
                value={actionFilter}
              >
                <option value="all">全部动作</option>
                {actions.map((action) => (
                  <option key={action} value={action}>
                    {actionNames[action] ?? action}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="maintenance-grid">
            {filteredTasks.length === 0 && (
              <div className="quality-empty">
                当前没有活动任务；已解决和被新修订替代的任务仍保留在审计数据中。
              </div>
            )}
            {pagedTasks.map((task) => (
              <article data-priority={task.priority} key={task.id}>
                <header>
                  <div>
                    <span>{actionNames[task.action] ?? task.action}</span>
                    <h3>{task.entity.canonicalName}</h3>
                  </div>
                  <b>{priorityNames[task.priority]}优先级</b>
                </header>
                <p>{task.issue.message}</p>
                <dl>
                  <div>
                    <dt>定位</dt>
                    <dd>
                      <code>{task.issue.path}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>规则</dt>
                    <dd>
                      {task.issue.code} · −{task.issue.penalty}
                    </dd>
                  </div>
                  <div>
                    <dt>版本</dt>
                    <dd>
                      {task.profileId}@{task.profileVersion}
                    </dd>
                  </div>
                  {task.agentGraphScheduleId && (
                    <div>
                      <dt>Agent 图</dt>
                      <dd>
                        <code>{task.agentGraphScheduleId}</code>
                      </dd>
                    </div>
                  )}
                </dl>
                <footer>
                  <code>{task.id}</code>
                  <span>{taskStatusNames[task.status]}</span>
                </footer>
              </article>
            ))}
          </div>
          <div className="quality-pagination">
            <span>{filteredTasks.length} 个活动任务</span>
            <div>
              <button
                aria-label="上一页任务"
                disabled={currentTaskPage === 0}
                onClick={() => setTaskPage((page) => Math.max(page - 1, 0))}
                type="button"
              >
                上一页
              </button>
              <strong>
                {currentTaskPage + 1} / {taskPageCount}
              </strong>
              <button
                aria-label="下一页任务"
                disabled={currentTaskPage + 1 >= taskPageCount}
                onClick={() =>
                  setTaskPage((page) => Math.min(page + 1, taskPageCount - 1))
                }
                type="button"
              >
                下一页
              </button>
            </div>
          </div>
        </section>

        <section className="quality-work-queue">
          <div className="quality-panel-header">
            <div>
              <p>AGENT RUNTIME CAPACITY</p>
              <h2>Agent 在线状态与容量</h2>
            </div>
            <span>
              {
                agentRuntimes.filter(
                  (item) => item.effectiveStatus === "online",
                ).length
              }{" "}
              在线 ·{" "}
              {agentRuntimes.reduce(
                (total, item) => total + item.availableCapacity,
                0,
              )}{" "}
              可用槽位
            </span>
          </div>
          <div className="agent-runtime-grid">
            {agentRuntimes.length === 0 && (
              <div className="quality-empty">
                暂无 Agent Runtime 心跳。Agent
                使用固定定义注册后，才可按能力自动领取任务。
              </div>
            )}
            {agentRuntimes.map((item) => (
              <article data-status={item.effectiveStatus} key={item.runtime.id}>
                <header>
                  <div>
                    <strong>{item.runtime.id}</strong>
                    <code>
                      {item.runtime.definitionId}@
                      {item.runtime.definitionVersion}
                    </code>
                  </div>
                  <em>{item.effectiveStatus}</em>
                </header>
                <p>
                  {item.runtime.supportedRoutes
                    .map((route) => routeNames[route])
                    .join(" · ")}
                </p>
                <div>
                  <span>
                    {item.activeLeaseCount}/{item.runtime.maxConcurrency}{" "}
                    活动租约
                  </span>
                  <span>{item.availableCapacity} 可用</span>
                </div>
                <small>
                  最后心跳：
                  {new Date(item.runtime.lastHeartbeatAt).toLocaleTimeString(
                    "zh-CN",
                  )}
                </small>
              </article>
            ))}
          </div>
        </section>

        <section className="quality-work-queue">
          <div className="quality-panel-header">
            <div>
              <p>LEASEABLE AGENT WORK</p>
              <h2>受治理 Agent 工作队列</h2>
            </div>
            <span>
              {workItems.length} 个活动 Work Item · 租约令牌不会出现在读取接口
            </span>
          </div>
          <div className="work-queue-table">
            <div className="work-queue-row work-queue-heading">
              <span>条目与固定修订</span>
              <span>安全路由</span>
              <span>所需能力</span>
              <span>租约</span>
              <span>状态</span>
            </div>
            {workItems.length === 0 && (
              <div className="quality-empty">
                暂无已分诊工作。Worker 完成质量分诊后，任务会通过持久化 Outbox
                进入这里。
              </div>
            )}
            {workItems.slice(0, 50).map((item) => (
              <article
                className="work-queue-row"
                data-status={item.status}
                key={item.id}
              >
                <div>
                  <strong>{item.entity.canonicalName}</strong>
                  <code>{item.revisionId}</code>
                  <small>{item.id}</small>
                </div>
                <div>
                  <strong>{routeNames[item.route]}</strong>
                  <small>
                    证据必需 · 禁止直接提案 · 第 {item.attempt} 次领取
                  </small>
                  <small>
                    {item.route === "source-acquisition"
                      ? "自动选择唯一合规来源并回写快照"
                      : item.route === "translation-evidence"
                        ? "提交缺失语言与固定修订引用后进入人工审核"
                        : "提交允许的分类节点与引用后进入人工审核"}
                  </small>
                </div>
                <div className="work-capabilities">
                  {item.requiredCapabilities.map((capability) => (
                    <code key={capability}>{capability}</code>
                  ))}
                </div>
                <div>
                  <strong>
                    {item.assigneeId ?? item.blockedReason ?? "未领取"}
                  </strong>
                  <small>
                    {item.leaseExpiresAt
                      ? `至 ${new Date(item.leaseExpiresAt).toLocaleTimeString(
                          "zh-CN",
                        )}`
                      : "无活动租约"}
                  </small>
                </div>
                <div className="work-status-action">
                  <em>{workStatusNames[item.status]}</em>
                  {item.status === "blocked" && (
                    <button
                      disabled={busy}
                      onClick={() => void requeueWorkItem(item.id)}
                      type="button"
                    >
                      重新入队
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}
