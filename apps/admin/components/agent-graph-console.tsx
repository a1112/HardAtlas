"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { adminFetch } from "../lib/auth-fetch";

interface NodeRun {
  nodeId: string;
  status: string;
  attempts: number;
  output: Record<string, unknown>;
  error: string | null;
  modelInvocations: ModelInvocation[];
}

interface ModelInvocation {
  gatewayId: string;
  gatewayBaseUrl: string;
  provider: string;
  model: string;
  requestId: string;
  agentId: string;
}

interface GraphRun {
  id: string;
  graphId: string;
  graphVersion: string;
  status: string;
  nodes: Record<string, NodeRun>;
  proposalIds: string[];
  budgetExhausted: boolean;
  usage: {
    modelCalls: number;
    inputTokens: number;
    outputTokens: number;
    costMicrousd: number;
  };
  modelInvocations: ModelInvocation[];
}

interface GraphRunSchedule {
  id: string;
  graphId: string;
  graphVersion: string;
  triggerType: string;
  status: string;
  requestedBy: string;
  runId: string | null;
  error: string | null;
  createdAt: string;
}

interface JobProgressEvent {
  jobId: string;
  jobType: string;
  status: string;
  progress: number;
  runId?: string | null;
  error?: string | null;
}

const apiUrl = "/apps/hardatlas-admin/api/backend";

interface AgentDefinition {
  id: string;
  name: string;
  description: string;
  role: string;
  modelPolicy: string;
}

interface GraphSpec {
  id: string;
  version: string;
  name?: string;
  description?: string;
  nodes: Array<{
    id: string;
    agentId?: string;
    handlerKey?: string;
    maxAttempts: number;
  }>;
  budget: {
    maxModelCalls: number;
    maxInputTokens: number;
    maxOutputTokens: number;
    maxCostMicrousd: number;
    deadlineSeconds: number;
  };
}

export function AgentGraphConsole() {
  const [run, setRun] = useState<GraphRun | null>(null);
  const [spec, setSpec] = useState<GraphSpec>();
  const [definitions, setDefinitions] = useState<AgentDefinition[]>([]);
  const [schedules, setSchedules] = useState<GraphRunSchedule[]>([]);
  const [notice, setNotice] = useState("尚未启动");
  const [busy, setBusy] = useState(false);
  const [watchedScheduleId, setWatchedScheduleId] = useState<string>();
  const [jobProgress, setJobProgress] = useState<JobProgressEvent>();

  useEffect(() => {
    void Promise.all([
      adminFetch(`${apiUrl}/api/v1/agent-graphs/knowledge-maintenance`, {
        cache: "no-store",
      }),
      adminFetch(`${apiUrl}/api/v1/agent-definitions`, {
        cache: "no-store",
      }),
      adminFetch(
        `${apiUrl}/api/v1/agent-graphs/knowledge-maintenance/schedules`,
        { cache: "no-store" },
      ),
    ])
      .then(async ([graphResponse, definitionsResponse, schedulesResponse]) => {
        if (
          !graphResponse.ok ||
          !definitionsResponse.ok ||
          !schedulesResponse.ok
        ) {
          throw new Error("Agent Registry 返回异常");
        }
        setSpec((await graphResponse.json()) as GraphSpec);
        setDefinitions((await definitionsResponse.json()) as AgentDefinition[]);
        setSchedules((await schedulesResponse.json()) as GraphRunSchedule[]);
        setNotice("版本化 Agent Registry 已同步");
      })
      .catch((error: unknown) =>
        setNotice(
          error instanceof Error ? error.message : "Agent Registry 不可用",
        ),
      );
  }, []);

  useEffect(() => {
    if (!watchedScheduleId) return;
    const source = new EventSource(
      `${apiUrl}/api/v1/jobs/${encodeURIComponent(
        watchedScheduleId,
      )}/events?follow=true&intervalMs=1000`,
    );
    const statuses = [
      "queued",
      "dispatched",
      "running",
      "completed",
      "failed",
      "canceled",
    ];
    const handleProgress = (event: MessageEvent<string>) => {
      const progress = JSON.parse(event.data) as JobProgressEvent;
      setJobProgress(progress);
      setSchedules((current) =>
        current.map((schedule) =>
          schedule.id === progress.jobId
            ? {
                ...schedule,
                status: progress.status,
                runId: progress.runId ?? schedule.runId,
                error: progress.error ?? schedule.error,
              }
            : schedule,
        ),
      );
      setNotice(
        `Worker 状态：${progress.status} · ${progress.progress}%${
          progress.runId ? ` · ${progress.runId}` : ""
        }`,
      );
      if (
        progress.status === "completed" ||
        progress.status === "failed" ||
        progress.status === "canceled"
      ) {
        source.close();
        if (progress.runId) {
          void adminFetch(
            `${apiUrl}/api/v1/agent-graphs/knowledge-maintenance/runs/${encodeURIComponent(
              progress.runId,
            )}`,
            { cache: "no-store" },
          )
            .then(async (response) => {
              if (!response.ok) throw new Error("运行结果读取失败");
              setRun((await response.json()) as GraphRun);
            })
            .catch((error: unknown) =>
              setNotice(
                error instanceof Error ? error.message : "运行结果读取失败",
              ),
            );
        }
      }
    };
    statuses.forEach((status) =>
      source.addEventListener(status, handleProgress as EventListener),
    );
    source.onerror = () => {
      source.close();
      setNotice("实时连接已结束；可重新选择调度继续跟踪");
    };
    return () => {
      statuses.forEach((status) =>
        source.removeEventListener(status, handleProgress as EventListener),
      );
      source.close();
    };
  }, [watchedScheduleId]);

  const agentsById = useMemo(
    () => new Map(definitions.map((definition) => [definition.id, definition])),
    [definitions],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const mode = submitter?.getAttribute("data-mode") ?? "run";
    const runId = `run-console-${Date.now()}`;
    const input = {
      sourceId: form.get("sourceId"),
      snapshotHash: form.get("snapshotHash"),
      entityId: form.get("entityId"),
      label: form.get("label"),
      fieldPath: form.get("fieldPath"),
      proposedValue: form.get("proposedValue"),
      citationId: form.get("citationId"),
      confidence: Number(form.get("confidence")),
      risk: form.get("risk"),
      sourceExcerpt: form.get("sourceExcerpt"),
      candidateEntityIds: String(form.get("candidateEntityIds") ?? "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
    };
    setBusy(true);
    setNotice(
      mode === "schedule"
        ? "正在写入版本化调度与事务 Outbox…"
        : "Agent 图正在执行并保存检查点…",
    );
    try {
      if (mode === "schedule") {
        const scheduleId = `schedule-console-${Date.now()}`;
        const response = await adminFetch(
          `${apiUrl}/api/v1/agent-graphs/knowledge-maintenance/schedules`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              id: scheduleId,
              triggerType: "manual",
              input,
              idempotencyKey: `console-${scheduleId}`,
            }),
          },
        );
        const payload = (await response.json()) as
          GraphRunSchedule | { detail?: string };
        if (!response.ok) {
          throw new Error(
            "detail" in payload ? payload.detail : "Agent 调度创建失败",
          );
        }
        setSchedules((current) => [payload as GraphRunSchedule, ...current]);
        setWatchedScheduleId((payload as GraphRunSchedule).id);
        setJobProgress({
          jobId: (payload as GraphRunSchedule).id,
          jobType: "agent-graph-schedule",
          status: (payload as GraphRunSchedule).status,
          progress: 0,
          runId: (payload as GraphRunSchedule).runId,
          error: (payload as GraphRunSchedule).error,
        });
        setNotice("任务已入队；Worker 将按钉住的图与 Agent 版本执行");
        return;
      }
      const response = await adminFetch(
        `${apiUrl}/api/v1/agent-graphs/knowledge-maintenance/runs`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            runId,
            ...input,
          }),
        },
      );
      const payload = (await response.json()) as GraphRun | { detail?: string };
      if (!response.ok) {
        throw new Error(
          "detail" in payload ? payload.detail : "Agent 图运行失败",
        );
      }
      setRun(payload as GraphRun);
      setNotice("运行检查点与提案已持久化");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Agent 图 API 不可用");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="graph-page">
      <header className="graph-header">
        <div>
          <a href="/">← 返回运行中心</a>
          <p>GENERATIVE AGENT GRAPH</p>
          <h1>知识维护图控制台</h1>
          <span>声明式 DAG · 节点检查点 · 幂等恢复 · 只生成治理提案</span>
        </div>
        <b data-state={run?.status ?? "queued"}>{run?.status ?? "ready"}</b>
      </header>

      <div className="graph-layout">
        <form className="graph-form" onSubmit={submit}>
          <p>RUN INPUT</p>
          <h2>启动维护任务</h2>
          <label>
            来源标识
            <input defaultValue="source-console-demo" name="sourceId" />
          </label>
          <label>
            来源快照哈希
            <input defaultValue="sha256:console-demo" name="snapshotHash" />
          </label>
          <label>
            目标实体
            <input defaultValue="entity-ginkgo" name="entityId" />
          </label>
          <label>
            实体标签
            <input defaultValue="Ginkgo biloba" name="label" />
          </label>
          <label>
            变更路径
            <input defaultValue="/sections/0/body" name="fieldPath" />
          </label>
          <label>
            建议值
            <textarea
              defaultValue="由维护图生成、等待治理审核的候选内容。"
              name="proposedValue"
            />
          </label>
          <label>
            不可变来源摘录（配置模型代理后启用模型抽取）
            <textarea
              defaultValue="银杏是银杏科、银杏属植物，是中生代孑遗的稀有树种。"
              name="sourceExcerpt"
            />
          </label>
          <label>
            实体候选 ID（逗号分隔，至少两个时启用模型消歧）
            <input
              defaultValue="entity-ginkgo, entity-ginkgo-fossil"
              name="candidateEntityIds"
            />
          </label>
          <div className="graph-form-row">
            <label>
              引用
              <input defaultValue="cite-ginkgo-001" name="citationId" />
            </label>
            <label>
              可信度
              <input
                defaultValue="0.95"
                max="1"
                min="0"
                name="confidence"
                step="0.01"
                type="number"
              />
            </label>
          </div>
          <label>
            风险
            <select defaultValue="medium" name="risk">
              <option value="low">低</option>
              <option value="medium">中</option>
              <option value="high">高</option>
              <option value="critical">关键</option>
            </select>
          </label>
          <div className="graph-form-actions">
            <button data-mode="schedule" disabled={busy} type="submit">
              {busy ? "处理中…" : "加入 Worker 队列"}
            </button>
            <button
              className="secondary"
              data-mode="run"
              disabled={busy}
              type="submit"
            >
              同步试运行
            </button>
          </div>
          <small>{notice}</small>
        </form>

        <section className="graph-canvas">
          <div className="graph-canvas-title">
            <div>
              <p>EXECUTION GRAPH</p>
              <h2>{spec?.name ?? "knowledge-maintenance"}</h2>
            </div>
            <code>{run?.graphVersion ?? spec?.version ?? "加载中"}</code>
          </div>
          <div
            className="graph-flow"
            style={{
              gridTemplateColumns: `repeat(${Math.max(spec?.nodes.length ?? 1, 1)}, minmax(0, 1fr))`,
            }}
          >
            {spec?.nodes.map((nodeSpec, index) => {
              const { id } = nodeSpec;
              const definition = nodeSpec.agentId
                ? agentsById.get(nodeSpec.agentId)
                : undefined;
              const node = run?.nodes[id];
              return (
                <div className="graph-node-wrap" key={id}>
                  <article data-state={node?.status ?? "queued"}>
                    <i>{index + 1}</i>
                    <div>
                      <strong>{definition?.name ?? id}</strong>
                      <span>
                        {definition?.description ?? nodeSpec.handlerKey}
                      </span>
                      <code>
                        {nodeSpec.agentId ?? nodeSpec.handlerKey} ·{" "}
                        {definition?.modelPolicy ?? "adapter"}
                      </code>
                    </div>
                    <b>{node?.status ?? "queued"}</b>
                    <small>尝试 {node?.attempts ?? 0} 次</small>
                    {node?.error ? <em>{node.error}</em> : null}
                  </article>
                  {index < (spec?.nodes.length ?? 0) - 1 ? (
                    <span className="graph-arrow">→</span>
                  ) : null}
                </div>
              );
            })}
          </div>
          <div className="graph-output">
            <strong>运行输出</strong>
            <span>Run ID: {run?.id ?? "—"}</span>
            <span>状态: {run?.status ?? "等待输入"}</span>
            <span>
              模型调用: {run?.usage.modelCalls ?? 0} /{" "}
              {spec?.budget.maxModelCalls ?? "—"}
            </span>
            <span>
              Token:{" "}
              {(run?.usage.inputTokens ?? 0) + (run?.usage.outputTokens ?? 0)}
            </span>
            <span>预算状态: {run?.budgetExhausted ? "已耗尽" : "正常"}</span>
            {run?.modelInvocations.map((invocation) => (
              <span key={`${invocation.agentId}-${invocation.requestId}`}>
                代理: {invocation.gatewayId} · {invocation.model} ·{" "}
                {invocation.requestId}
              </span>
            ))}
            {run?.proposalIds.map((proposalId) => (
              <a href={`/proposals/${proposalId}`} key={proposalId}>
                打开治理提案 {proposalId} →
              </a>
            ))}
          </div>
          <div className="graph-schedules">
            <div>
              <div>
                <strong>最近调度</strong>
                <span>Outbox → Redis → Worker → 治理提案</span>
              </div>
              {jobProgress ? (
                <b data-state={jobProgress.status}>
                  {jobProgress.status} · {jobProgress.progress}%
                </b>
              ) : null}
            </div>
            {schedules.length ? (
              schedules.slice(0, 6).map((schedule) => (
                <article key={schedule.id}>
                  <code>{schedule.id}</code>
                  <span>
                    {schedule.graphVersion} · {schedule.triggerType}
                  </span>
                  <b data-state={schedule.status}>{schedule.status}</b>
                  {schedule.runId ? <small>{schedule.runId}</small> : null}
                  {schedule.error ? <em>{schedule.error}</em> : null}
                  <button
                    aria-pressed={watchedScheduleId === schedule.id}
                    onClick={() => {
                      setJobProgress(undefined);
                      setWatchedScheduleId(schedule.id);
                      setNotice(`正在跟踪 ${schedule.id}`);
                    }}
                    type="button"
                  >
                    {watchedScheduleId === schedule.id ? "跟踪中" : "实时跟踪"}
                  </button>
                </article>
              ))
            ) : (
              <small>暂无持久化调度</small>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
